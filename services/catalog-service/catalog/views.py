import hashlib

from django.db import transaction
from django.db.models import Case, F, Value, When
from django.shortcuts import get_object_or_404
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from greenmart_common.cache import bump_version, cached

from .models import Category, OrderStockApplied, Product, Review
from .serializers import ProductWriteSerializer, category_dict, product_dict, review_dict
from .signals import CACHE_GROUP

ALLOWED_ORDERING = {"id", "-id", "created_at", "-created_at", "name", "-name"}

# Thời gian cache theo hướng dẫn ElastiCache cho e-commerce: danh mục/danh sách vài phút, chi tiết sản phẩm 5 phút.
# Không lo dữ liệu cũ: mọi lần ghi đều tăng phiên bản cache (catalog/signals.py) nên key cũ bị bỏ ngay.
LIST_TTL = 120
DETAIL_TTL = 300


def _query_key(params):
    """Khóa ngắn, không dấu cách (tham số q có thể là tiếng Việt có dấu)."""
    raw = "&".join(f"{k}={params.get(k)}" for k in sorted(params.keys()))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class CategoryListView(APIView):
    def get(self, request):
        data = cached(CACHE_GROUP, "categories", LIST_TTL,
                      lambda: [category_dict(c) for c in Category.objects.all().order_by("id")])
        return Response(data)


class ProductListCreateView(APIView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request):
        p = request.query_params
        if p.get("ids"):
            try:
                [int(i) for i in p["ids"].split(",") if i.strip()]
            except ValueError:
                return Response({"error": "invalid_ids"}, status=400)
        data = cached(CACHE_GROUP, "products?" + _query_key(p), LIST_TTL, lambda: self._list(p))
        return Response(data)

    @staticmethod
    def _list(p):
        qs = Product.objects.select_related("category")
        if p.get("featured") in ("1", "true"):
            qs = qs.filter(is_featured=True)
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("q"):
            qs = qs.filter(name__icontains=p["q"])
        if p.get("ids"):
            qs = qs.filter(pk__in=[int(i) for i in p["ids"].split(",") if i.strip()])
        ordering = p.get("ordering", "id")
        qs = qs.order_by(ordering if ordering in ALLOWED_ORDERING else "id")
        return [product_dict(x) for x in qs]

    def post(self, request):
        ser = ProductWriteSerializer(data=request.data)
        if not ser.is_valid():
            return Response({"errors": ser.errors}, status=400)
        product = ser.save()
        return Response(product_dict(product), status=201)


class ProductDetailView(APIView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request, slug):
        data = cached(CACHE_GROUP, f"product:{slug}", DETAIL_TTL, lambda: self._detail(slug))
        return Response(data)

    @staticmethod
    def _detail(slug):
        product = get_object_or_404(Product.objects.select_related("category"), slug=slug)
        data = product_dict(product, with_review_count=True)
        top = product.reviews.filter(parent__isnull=True).order_by("-created_at", "-id")
        data["reviews"] = [review_dict(r) for r in top]
        return data

    def put(self, request, slug):
        product = get_object_or_404(Product, slug=slug)
        # Không gửi ảnh mới => giữ ảnh cũ (ProductWriteSerializer: image required=False).
        ser = ProductWriteSerializer(product, data=request.data, partial=True)
        if not ser.is_valid():
            return Response({"errors": ser.errors}, status=400)
        product = ser.save()
        return Response(product_dict(product))


class ProductHideView(APIView):
    """'Xóa' sản phẩm = ẩn + về tồn kho 0 (không xóa dữ liệu) – như bản monolith."""

    def post(self, request, slug):
        product = get_object_or_404(Product, slug=slug)
        product.status = "hidden"
        product.stock = 0
        product.save()
        return Response(product_dict(product))


def _clean_user(request):
    try:
        return int(request.data.get("user_id")), (request.data.get("username") or "")[:150]
    except (TypeError, ValueError):
        return None, ""


class ProductReviewCreateView(APIView):
    def post(self, request, slug):
        product = get_object_or_404(Product, slug=slug)
        user_id, username = _clean_user(request)
        if user_id is None:
            return Response({"error": "user_required"}, status=400)
        try:
            rating = int(request.data.get("rating", 0))
        except (TypeError, ValueError):
            rating = 0
        comment = (request.data.get("comment") or "").strip()
        if rating < 1 or rating > 5:
            return Response({"error": "invalid_rating"}, status=400)
        if not comment:
            return Response({"error": "empty_comment"}, status=400)
        review = Review.objects.create(product=product, user_id=user_id, username=username,
                                       rating=rating, comment=comment)
        return Response(review_dict(review), status=201)


class ReviewReplyView(APIView):
    def post(self, request, review_id):
        parent = get_object_or_404(Review.objects.select_related("product"), pk=review_id)
        user_id, username = _clean_user(request)
        if user_id is None:
            return Response({"error": "user_required"}, status=400)
        content = request.data.get("content") or ""
        if not content:
            return Response({"error": "empty_comment", "product_slug": parent.product.slug}, status=400)
        reply = Review.objects.create(product=parent.product, user_id=user_id, username=username,
                                      comment=content, parent=parent)
        data = review_dict(reply, with_replies=False)
        data["product_slug"] = parent.product.slug
        return Response(data, status=201)


class StatsView(APIView):
    def get(self, request):
        return Response({"product_count": Product.objects.count(), "category_count": Category.objects.count()})


class ApplyOrderStockView(APIView):
    """POST {order_id, items: [{product_id, quantity}]} từ order-worker (tin nhắn SQS sau khi đơn hoàn tất).

    Trừ tồn kho (không xuống dưới 0), sản phẩm về 0 thì chuyển "Hết hàng". Gọi lại cùng order_id không trừ lần hai.
    """

    def post(self, request):
        try:
            order_id = int(request.data.get("order_id"))
            items = [(int(i["product_id"]), int(i["quantity"])) for i in request.data.get("items") or []]
        except (TypeError, ValueError, KeyError):
            return Response({"error": "invalid_request"}, status=400)

        with transaction.atomic():
            _, created = OrderStockApplied.objects.get_or_create(order_id=order_id)
            if not created:
                return Response({"applied": False, "reason": "already_applied"})
            touched = []
            for product_id, qty in items:
                if qty <= 0:
                    continue
                # stock là số không âm (UNSIGNED): dùng CASE thay vì trừ thẳng để không lỗi tràn số khi qty > stock.
                Product.objects.filter(pk=product_id).update(
                    stock=Case(When(stock__gte=qty, then=F("stock") - qty), default=Value(0)))
                touched.append(product_id)
            Product.objects.filter(pk__in=touched, stock=0, status="available").update(status="out_of_stock")
        # update() không phát signal nên tự làm mới cache.
        bump_version(CACHE_GROUP)
        return Response({"applied": True, "products": touched})
