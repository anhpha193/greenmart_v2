import time
from decimal import Decimal

from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F, Sum
from rest_framework.response import Response
from rest_framework.views import APIView

from django.core.cache import cache
from django.db.models import prefetch_related_objects

from greenmart_common.client import NotFound

from .clients import fetch_products, promotions
from .events import publish_order_completed
from .models import Order, OrderItem
from .pricing import promotion_snapshot_dict, serialize_order


# ---------- helpers ----------

class BadOwner(Exception):
    pass


def parse_owner(data):
    """(customer_id, guest_token). Đúng một trong hai."""
    cid = data.get("customer_id")
    token = data.get("guest_token")
    if cid not in (None, ""):
        try:
            return int(cid), None
        except (TypeError, ValueError):
            raise BadOwner()
    if token:
        return None, str(token)[:64]
    raise BadOwner()


def find_cart(customer_id, guest_token):
    qs = Order.objects.filter(complete=False)
    if customer_id is not None:
        qs = qs.filter(customer_id=customer_id)
    else:
        qs = qs.filter(customer_id__isnull=True, guest_token=guest_token)
    return qs.order_by("id").first()


def get_or_create_cart(customer_id, guest_token):
    order = find_cart(customer_id, guest_token)
    if order is None:
        order = Order.objects.create(customer_id=customer_id, guest_token=guest_token, complete=False)
    return order


# ---------- read model giỏ hàng (Valkey) ----------
# Dữ liệu hiển thị giỏ (JSON đã dựng) được cache theo chủ giỏ + PHIÊN BẢN. Mọi lần ghi giỏ (thêm/bớt, khuyến mãi,
# gộp, chốt đơn) đặt phiên bản mới SAU KHI transaction commit; bản cache cũ nằm ở key phiên bản cũ nên không bao
# giờ được đọc lại (kể cả khi một request đọc chậm ghi đè sau). TTL ngắn là lớp an toàn thứ hai.
# Chốt đơn vẫn đọc thẳng MySQL + giá mới từ catalog (CompleteOrderView), không dùng read model.
CART_TTL = 60


def _owner_key(customer_id, guest_token):
    return f"c{customer_id}" if customer_id is not None else f"g{guest_token}"


def _cart_version(owner):
    try:
        return cache.get(f"cartv:{owner}") or 0
    except Exception:  # noqa: BLE001 - Valkey lỗi: coi như không có cache
        return None


def touch_cart(customer_id=None, guest_token=None):
    """Gọi trong transaction ghi giỏ: sau commit đặt phiên bản mới -> read model cũ hết hiệu lực."""
    owner = _owner_key(customer_id, guest_token)

    def bump():
        try:
            cache.set(f"cartv:{owner}", time.time_ns(), 86400)
        except Exception:  # noqa: BLE001
            pass
    transaction.on_commit(bump)


EMPTY_CART = {"id": None, "customer_id": None, "date_order": None, "complete": False, "transaction_id": None,
              "promotion": None, "items": [], "get_cart_items": 0, "get_cart_total": "0.00",
              "get_discount_amount": "0.00", "get_total_after_discount": "0.00"}


def full_order(order):
    products = fetch_products(i.product_id for i in order.items.all()) if _needs_live_prices(order) else {}
    return serialize_order(order, products)


def _needs_live_prices(order):
    if not order.complete:
        return True
    return any(i.unit_price is None for i in order.items.all())


def bad_owner_response():
    return Response({"error": "invalid_owner"}, status=400)


# ---------- giỏ hàng ----------

class CurrentCartView(APIView):
    """POST {customer_id | guest_token, detail?, prune_hidden?} -> giỏ đang mở.
    Chỉ ĐỌC: chưa có giỏ thì trả giỏ trống, không tạo hàng mới trong MySQL (trước đây mỗi khách mở trang giỏ/đếm
    giỏ là một lệnh INSERT). Bản chi tiết lấy từ read model trong Valkey nếu giỏ chưa đổi kể từ lần dựng trước."""

    def post(self, request):
        try:
            cid, token = parse_owner(request.data)
        except BadOwner:
            return bad_owner_response()

        if not request.data.get("detail"):
            order = find_cart(cid, token)
            if order is None:
                return Response({"id": None, "customer_id": cid, "get_cart_items": 0, "promotion": None})
            count = order.items.aggregate(s=Sum("quantity"))["s"] or 0
            return Response({"id": order.id, "customer_id": order.customer_id, "get_cart_items": count,
                             "promotion": promotion_snapshot_dict(order)})

        prune = bool(request.data.get("prune_hidden"))
        owner = _owner_key(cid, token)
        version = _cart_version(owner)
        if version is None:
            return Response(self._build(cid, token, prune))
        key = f"cart:{owner}:{version}:{int(prune)}"
        try:
            data = cache.get(key)
        except Exception:  # noqa: BLE001
            data = None
        if data is None:
            data = self._build(cid, token, prune)
            try:
                cache.set(key, data, CART_TTL)
            except Exception:  # noqa: BLE001
                pass
        return Response(data)

    @staticmethod
    def _build(cid, token, prune):
        order = find_cart(cid, token)
        if order is None:
            return {**EMPTY_CART, "customer_id": cid}
        prefetch_related_objects([order], "items")        # 1 query lấy mọi dòng, dùng lại cho cả bước lọc và dựng JSON
        items = list(order.items.all())
        products = fetch_products(i.product_id for i in items)
        if prune:
            # Sản phẩm bị ẩn/đã mất khỏi catalog thì loại khỏi giỏ (bản cũ làm việc này ở view giỏ hàng).
            stale = [i.id for i in items if i.product_id not in products or products[i.product_id]["status"] == "hidden"]
            if stale:
                with transaction.atomic():
                    OrderItem.objects.filter(pk__in=stale).delete()
                    touch_cart(cid, token)
                order = Order.objects.prefetch_related("items").get(pk=order.pk)
        return serialize_order(order, products)


class CartItemView(APIView):
    """POST {owner, product_id, delta} -> cộng/trừ số lượng; <= 0 thì xóa dòng."""

    def post(self, request):
        try:
            cid, token = parse_owner(request.data)
            product_id = int(request.data.get("product_id"))
            delta = int(request.data.get("delta", 1))
        except (BadOwner, TypeError, ValueError):
            return Response({"error": "invalid_request"}, status=400)

        # Ít query nhất có thể (trước: SELECT giỏ, SELECT/INSERT dòng, UPDATE, SELECT lại, [DELETE], SUM):
        #   món đã có: 1 UPDATE nguyên tử (quantity = quantity + delta), không đọc trước
        #   món mới  : UPDATE không trúng dòng nào -> INSERT
        #   chỉ khi delta < 0 mới cần xóa dòng có quantity <= 0
        # Hai request cùng lúc thêm CÙNG món mới (bấm 2 lần): một bên gặp lỗi khóa duy nhất hoặc deadlock (MySQL
        # REPEATABLE READ) -> chạy lại cả transaction; lần sau UPDATE thấy dòng đã có và cộng dồn, không mất số lượng.
        for attempt in range(3):
            try:
                with transaction.atomic():
                    order = get_or_create_cart(cid, token)
                    rows = OrderItem.objects.filter(order=order, product_id=product_id)
                    if not rows.update(quantity=F("quantity") + delta) and delta > 0:
                        OrderItem.objects.create(order=order, product_id=product_id, quantity=delta)
                    removed = bool(delta <= 0 and rows.filter(quantity__lte=0).delete()[0])
                    touch_cart(cid, token)
                    count = order.items.aggregate(s=Sum("quantity"))["s"] or 0
                break
            except (IntegrityError, OperationalError) as exc:
                if attempt == 2 or (isinstance(exc, OperationalError) and exc.args[:1] not in ((1213,), (1205,))):
                    raise
        return Response({"order_id": order.id, "removed": removed, "get_cart_items": count})


class ApplyPromotionView(APIView):
    """POST {owner, promotion_id}. 404 nếu mã không tồn tại/đang tắt; 409 nếu giỏ trống hoặc chưa đủ giá trị tối thiểu."""

    def post(self, request):
        try:
            cid, token = parse_owner(request.data)
            promo_id = int(request.data.get("promotion_id"))
        except (BadOwner, TypeError, ValueError):
            return Response({"error": "invalid_request"}, status=400)
        try:
            promo = promotions.get(f"/api/promotions/{promo_id}/")
        except NotFound:
            return Response({"error": "promotion_not_found"}, status=404)
        if not promo["is_active"]:
            return Response({"error": "promotion_not_found"}, status=404)

        order = get_or_create_cart(cid, token)
        data = full_order(order)
        if not data["items"]:
            return Response({"error": "cart_empty"}, status=409)
        if Decimal(data["get_cart_total"]) < Decimal(promo["min_order_value"]):
            return Response({"error": "min_order_not_met", "min_order_value": promo["min_order_value"]}, status=409)

        with transaction.atomic():
            order.set_promotion(promo)
            order.save()
            touch_cart(cid, token)
        return Response(full_order(Order.objects.prefetch_related("items").get(pk=order.pk)))


class ClearPromotionView(APIView):
    def post(self, request):
        try:
            cid, token = parse_owner(request.data)
        except BadOwner:
            return bad_owner_response()
        order = get_or_create_cart(cid, token)
        with transaction.atomic():
            order.clear_promotion()
            order.save()
            touch_cart(cid, token)
        return Response({"id": order.id})


class MergeCartView(APIView):
    """Khi khách đăng nhập: gộp giỏ vãng lai (guest_token) vào giỏ của user, rồi xóa giỏ vãng lai."""

    def post(self, request):
        token = request.data.get("guest_token")
        try:
            customer_id = int(request.data.get("customer_id"))
        except (TypeError, ValueError):
            return Response({"error": "invalid_request"}, status=400)
        if not token:
            return Response({"merged": False})
        with transaction.atomic():
            guest = Order.objects.filter(customer_id__isnull=True, guest_token=token, complete=False).first()
            if guest is None:
                return Response({"merged": False})
            user_order = get_or_create_cart(customer_id, None)
            for item in guest.items.all():
                user_item, created = OrderItem.objects.get_or_create(
                    order=user_order, product_id=item.product_id, defaults={"quantity": item.quantity})
                if not created:
                    user_item.quantity = (user_item.quantity or 0) + (item.quantity or 0)
                    user_item.save()
            guest.delete()
            touch_cart(None, token)
            touch_cart(customer_id, None)
        return Response({"merged": True})


# ---------- đơn hàng ----------

class CompleteOrderView(APIView):
    """POST {customer_id}: chốt giỏ đang mở thành đơn hoàn tất, lưu snapshot tên/giá từng dòng."""

    def post(self, request):
        try:
            customer_id = int(request.data.get("customer_id"))
        except (TypeError, ValueError):
            return Response({"error": "invalid_request"}, status=400)
        with transaction.atomic():
            order = Order.objects.select_for_update().filter(customer_id=customer_id, complete=False).order_by("id").first()
            if order is None:
                return Response({"error": "no_open_cart"}, status=404)
            products = fetch_products((i.product_id for i in order.items.all()), fresh=True)
            for item in order.items.all():
                p = products.get(item.product_id)
                if p:
                    item.product_name = p["name"]
                    item.product_slug = p["slug"]
                    item.unit_price = Decimal(p["price"])
                    item.save()
            order.complete = True
            order.save()
            touch_cart(customer_id, None)
        order = Order.objects.prefetch_related("items").get(pk=order.pk)
        publish_order_completed(order)  # sau khi commit: order-worker trừ tồn kho (chỉ khi có ORDER_EVENTS_QUEUE_URL)
        return Response(serialize_order(order, products))


class OrderListView(APIView):
    """GET ?complete=true|false&customer_id=&ordering=-date_order"""

    ORDERING = {"id", "-id", "date_order", "-date_order"}

    def get(self, request):
        p = request.query_params
        qs = Order.objects.prefetch_related("items")
        if p.get("complete") in ("true", "1"):
            qs = qs.filter(complete=True)
        elif p.get("complete") in ("false", "0"):
            qs = qs.filter(complete=False)
        if p.get("customer_id"):
            qs = qs.filter(customer_id=p["customer_id"])
        ordering = p.get("ordering", "id")
        orders = list(qs.order_by(ordering if ordering in self.ORDERING else "id"))

        live_ids = [i.product_id for o in orders if _needs_live_prices(o) for i in o.items.all()]
        products = fetch_products(live_ids) if live_ids else {}
        return Response([serialize_order(o, products) for o in orders])


class StatsView(APIView):
    def get(self, request):
        return Response({"order_count": Order.objects.count()})
