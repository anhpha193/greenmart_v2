from decimal import Decimal

from django.db import transaction
from django.http import HttpResponse
from rest_framework.response import Response
from rest_framework.views import APIView

from greenmart_common import cartmodel, fastjson
from greenmart_common.client import NotFound

from .cart import (CartClosed, bump, change_item, get_or_create_cart, is_stale_item, load_cart, refresh_order,
                   summary)
from .clients import fetch_products, promotions
from .events import publish_order_completed
from .models import Order, OrderItem
from .pricing import serialize_order

PROMOTION_FIELDS = [f"promotion_{f}" for f in
                    ("id", "code", "name", "discount_type", "apply_type", "discount_value", "min_order_value")]


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


def _needs_live_prices(order):
    if not order.complete:
        return True
    return any(i.unit_price is None for i in order.items.all())


def bad_owner_response():
    return Response({"error": "invalid_owner"}, status=400)


def json_bytes(payload):
    """Trả nguyên bytes JSON đã dựng sẵn trong read model: không dựng/serialize lại ở mỗi lần đọc."""
    return HttpResponse(payload, content_type="application/json")


# ---------- giỏ hàng ----------

class CurrentCartView(APIView):
    """POST {customer_id | guest_token, detail?, prune_hidden?} -> giỏ đang mở.
    Chỉ ĐỌC: chưa có giỏ thì trả giỏ trống, không tạo hàng mới trong MySQL.
    Đọc Cart Read Model trong Valkey trước (1 round-trip, trả nguyên bytes); trượt/tombstone thì dựng từ MySQL bằng
    một câu SELECT rồi ghi lại read model (orders/cart.py)."""

    def post(self, request):
        try:
            cid, token = parse_owner(request.data)
        except BadOwner:
            return bad_owner_response()
        detail = bool(request.data.get("detail"))
        prune = bool(request.data.get("prune_hidden"))

        hit = cartmodel.get(cartmodel.owner_key(cid, token))
        if hit and hit[2] is not None:
            payload = hit[2]
            if detail and not prune:
                return json_bytes(payload)
            data = fastjson.loads(payload)
            if not detail:
                return Response(self._count(data, cid))
            if not any(is_stale_item(i["product"]) for i in data["items"]):
                return json_bytes(payload)
        data, payload, _ = load_cart(cid, token, prune)
        return json_bytes(payload) if detail else Response(self._count(data, cid))

    @staticmethod
    def _count(data, cid):
        return {"id": data["id"], "customer_id": data["customer_id"] if data["id"] else cid,
                "get_cart_items": data["get_cart_items"], "promotion": data["promotion"]}


class CartItemView(APIView):
    """POST {owner, product_id, delta} -> cộng/trừ số lượng; <= 0 thì xóa dòng.
    Trả luôn số món, tổng tiền, tóm tắt và phiên bản giỏ để web cập nhật giao diện không cần gọi thêm."""

    def post(self, request):
        try:
            cid, token = parse_owner(request.data)
            product_id = int(request.data.get("product_id"))
            delta = int(request.data.get("delta", 1))
        except (BadOwner, TypeError, ValueError):
            return Response({"error": "invalid_request"}, status=400)
        try:
            order_id, removed = change_item(cid, token, product_id, delta)
        except CartClosed:
            return Response({"error": "cart_closed"}, status=409)
        data, _, version = refresh_order(cid, token, order_id)
        s = summary(data)
        return Response({"order_id": order_id, "removed": removed, "get_cart_items": s["get_cart_items"],
                         "cart_count": s["get_cart_items"], "cart_total": s["get_cart_total"],
                         "cart_version": version, "cart_summary": s})


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
        data, _, _ = refresh_order(cid, token, order.id)
        if not data["items"]:
            return Response({"error": "cart_empty"}, status=409)
        if Decimal(data["get_cart_total"]) < Decimal(promo["min_order_value"]):
            return Response({"error": "min_order_not_met", "min_order_value": promo["min_order_value"]}, status=409)

        with transaction.atomic():
            order.set_promotion(promo)
            order.save(update_fields=PROMOTION_FIELDS)  # không ghi đè cột version
            bump(order.id)
        data, _, _ = refresh_order(cid, token, order.id)
        return Response(data)


class ClearPromotionView(APIView):
    def post(self, request):
        try:
            cid, token = parse_owner(request.data)
        except BadOwner:
            return bad_owner_response()
        order = get_or_create_cart(cid, token)
        with transaction.atomic():
            order.clear_promotion()
            order.save(update_fields=PROMOTION_FIELDS)
            bump(order.id)
        refresh_order(cid, token, order.id)
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
            guest = Order.objects.select_for_update().filter(
                customer_id__isnull=True, guest_token=token, complete=False).first()
            if guest is None:
                return Response({"merged": False})
            user_order = get_or_create_cart(customer_id, None)
            for item in guest.items.all():
                user_item, created = OrderItem.objects.get_or_create(
                    order=user_order, product_id=item.product_id, defaults={"quantity": item.quantity})
                if not created:
                    user_item.quantity = (user_item.quantity or 0) + (item.quantity or 0)
                    user_item.save(update_fields=["quantity"])
            guest_label = (guest.id, guest.version + 1)
            guest.delete()
            bump(user_order.id)
        # Giỏ khách đã mất: tombstone với nhãn mới hơn mọi bản dựng cũ, rồi ghi "giỏ trống" thay vào.
        cartmodel.tombstone(cartmodel.owner_key(None, token), *guest_label)
        load_cart(None, token)
        refresh_order(customer_id, None, user_order.id)
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
            order.save(update_fields=["complete"])
            bump(order.id)
            label = (order.id, order.version + 1)  # đang giữ khóa dòng (select_for_update) nên version + 1 là đúng
        # Giỏ đã thành đơn: tombstone rồi ghi "giỏ trống" -> trang giỏ không bao giờ hiện lại giỏ vừa chốt.
        cartmodel.tombstone(cartmodel.owner_key(customer_id, None), *label)
        load_cart(customer_id, None)
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
