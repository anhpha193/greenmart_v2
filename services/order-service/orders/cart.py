"""Giỏ hàng 1.2.8 (CQRS-lite): lệnh ghi ngắn trên MySQL + Cart Read Model trong Valkey (greenmart_common.cartmodel).

Ghi (CartItemView, ORDER_CART_WRITE_MODE):
  upsert (mặc định): không mở transaction cho thao tác thêm món (delta > 0). Mỗi câu lệnh tự commit:
      SELECT id giỏ mở -> [INSERT giỏ] -> INSERT ... SELECT ... ON DUPLICATE KEY UPDATE (chỉ trúng khi giỏ còn mở,
      dựa trên unique (order_id, product_id)) -> UPDATE version = version + 1
    Bỏ được SET autocommit=0 / COMMIT / SET autocommit=1 và SELECT SUM của bản cũ.
    Bớt món (delta <= 0) vẫn chạy trong transaction (UPDATE + DELETE dòng <= 0 phải đi cùng nhau).
  txn: như 1.2.6 (mọi thao tác trong một transaction), để A/B và quay lại nhanh.
Sau khi ghi: dựng lại read model ngay từ MySQL (write-through) và trả luôn số món/tổng tiền/phiên bản cho web,
nên trang giỏ ngay sau khi thêm món đọc được từ Valkey thay vì dựng lại (profile 1.2.7: _build = 29% thời gian).

Vì sao không cần transaction cho thêm món mà vẫn đúng: read model được dựng bằng MỘT câu SELECT (giỏ + các dòng,
LEFT JOIN) nên phiên bản và dữ liệu luôn khớp nhau; mỗi lần đổi dòng đều có một lần tăng version theo sau, và bản
dựng nào có nhãn lớn nhất cũng được đọc sau lần tăng version cuối cùng -> read model cuối cùng luôn là trạng thái mới
nhất (cartmodel chỉ ghi khi nhãn không nhỏ hơn).
"""
from types import SimpleNamespace

from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import F
from django.utils import timezone

from greenmart_common import cartmodel, fastjson
from greenmart_common.env import env

from .clients import fetch_products
from .models import Order, OrderItem
from .pricing import serialize_order

WRITE_MODE = env("ORDER_CART_WRITE_MODE", "upsert")  # upsert | txn

EMPTY_CART = {"id": None, "customer_id": None, "date_order": None, "complete": False, "transaction_id": None,
              "promotion": None, "items": [], "get_cart_items": 0, "get_cart_total": "0.00",
              "get_discount_amount": "0.00", "get_total_after_discount": "0.00"}

_ORDER_FIELDS = ("id", "customer_id", "date_order", "complete", "transaction_id", "version",
                 "promotion_id", "promotion_code", "promotion_name", "promotion_discount_type",
                 "promotion_apply_type", "promotion_discount_value", "promotion_min_order_value")
_ITEM_FIELDS = ("items__id", "items__product_id", "items__quantity", "items__date_added")
RETRYABLE_MYSQL = ((1213,), (1205,))  # deadlock, lock wait timeout


class CartClosed(Exception):
    """Giỏ vừa được chốt thành đơn trong lúc đang thêm món (thử lại 3 lần vẫn vậy)."""


def _open(customer_id, guest_token):
    qs = Order.objects.filter(complete=False)
    if customer_id is not None:
        return qs.filter(customer_id=customer_id)
    return qs.filter(customer_id__isnull=True, guest_token=guest_token)


def find_cart(customer_id, guest_token):
    return _open(customer_id, guest_token).order_by("id").first()


def find_cart_id(customer_id, guest_token):
    return _open(customer_id, guest_token).order_by("id").values_list("id", flat=True).first()


def get_or_create_cart(customer_id, guest_token):
    order = find_cart(customer_id, guest_token)
    if order is None:
        order = Order.objects.create(customer_id=customer_id, guest_token=guest_token, complete=False,
                                     updated_at=timezone.now())
        order.refresh_from_db(fields=["version"])  # db_default: lấy giá trị thật thay cho biểu thức DEFAULT
    return order


def bump(order_id):
    """Tăng version của giỏ (gọi trong cùng transaction với lần ghi, hoặc ngay sau câu ghi khi chạy upsert)."""
    Order.objects.filter(pk=order_id).update(version=F("version") + 1, updated_at=timezone.now())


def is_retryable(exc):
    return isinstance(exc, IntegrityError) or (isinstance(exc, OperationalError) and exc.args[:1] in RETRYABLE_MYSQL)


# ---------- dựng read model ----------

class _Items(list):
    def all(self):  # serialize_order/item_rows gọi order.items.all()
        return self


def _state(rows):
    """Các dòng values() (giỏ LEFT JOIN dòng) của MỘT giỏ -> đối tượng giống Order cho serialize_order."""
    first = rows[0]
    order = SimpleNamespace(**{f: first[f] for f in _ORDER_FIELDS})
    order.items = _Items(
        SimpleNamespace(id=r["items__id"], product_id=r["items__product_id"], quantity=r["items__quantity"],
                        date_added=r["items__date_added"], unit_price=None, product_name="", product_slug="")
        for r in rows if r["items__id"] is not None)
    return order


def _load_rows(qs):
    rows = list(qs.order_by("id", "items__id").values(*_ORDER_FIELDS, *_ITEM_FIELDS))
    if not rows:
        return None
    oid = rows[0]["id"]
    return _state([r for r in rows if r["id"] == oid])


def is_stale_item(product):
    return product is None or product.get("status") == "hidden"


def _publish(owner, order, products):
    data = serialize_order(order, products)
    payload = fastjson.dumps(data)
    cartmodel.put(owner, order.id, order.version, payload)
    return data, payload, f"{order.id}:{order.version}"


def load_cart(customer_id, guest_token, prune=False):
    """Dựng giỏ đang mở từ MySQL (1 câu SELECT) + giá từ catalog (cache), ghi read model.
    -> (dict, bytes JSON, "order_id:version")."""
    owner = cartmodel.owner_key(customer_id, guest_token)
    order = _load_rows(_open(customer_id, guest_token))
    if order is None:
        data = {**EMPTY_CART, "customer_id": customer_id}
        payload = fastjson.dumps(data)
        cartmodel.put_empty(owner, payload)
        return data, payload, "0:0"
    products = fetch_products(i.product_id for i in order.items)
    if prune:
        # Sản phẩm bị ẩn/đã mất khỏi catalog thì loại khỏi giỏ (như bản cũ), rồi dựng lại.
        stale = [i.id for i in order.items if is_stale_item(products.get(i.product_id))]
        if stale:
            with transaction.atomic():
                OrderItem.objects.filter(pk__in=stale).delete()
                bump(order.id)
            return load_cart(customer_id, guest_token, prune=False)
    return _publish(owner, order, products)


def refresh_order(customer_id, guest_token, order_id):
    """Sau một lần ghi: dựng lại read model của giỏ vừa ghi (write-through). -> như load_cart."""
    order = _load_rows(Order.objects.filter(pk=order_id, complete=False))
    if order is None:  # giỏ vừa bị chốt/xóa ở request khác: dựng theo chủ giỏ
        return load_cart(customer_id, guest_token)
    products = fetch_products(i.product_id for i in order.items)
    return _publish(cartmodel.owner_key(customer_id, guest_token), order, products)


def summary(data):
    """Phần web cần để cập nhật giao diện sau khi ghi (không phải gọi thêm request nào)."""
    return {"get_cart_items": data["get_cart_items"], "get_cart_total": data["get_cart_total"],
            "get_total_after_discount": data["get_total_after_discount"], "promotion": data["promotion"]}


# ---------- ghi ----------

def _upsert_item(order_id, product_id, delta):
    """Cộng delta (> 0) vào dòng (order, product), chưa có thì thêm. Chỉ có tác dụng khi giỏ còn mở.
    Trả số dòng bị ảnh hưởng (0 = giỏ đã bị chốt/xóa)."""
    t, o = OrderItem._meta.db_table, Order._meta.db_table
    now = connection.ops.adapt_datetimefield_value(timezone.now())
    insert = (f"INSERT INTO {t} (order_id, product_id, quantity, date_added, product_name, product_slug) "
              f"SELECT id, %s, %s, %s, '', '' FROM {o} WHERE id = %s AND complete = 0 ")
    if connection.vendor == "mysql":
        sql = insert + "ON DUPLICATE KEY UPDATE quantity = quantity + %s"
    else:  # sqlite (test/local)
        sql = insert + "ON CONFLICT(order_id, product_id) DO UPDATE SET quantity = quantity + %s"
    with connection.cursor() as cur:
        cur.execute(sql, [product_id, delta, now, order_id, delta])
        return cur.rowcount


def _decrease(order, product_id, delta):
    """delta <= 0: trừ số lượng rồi xóa dòng <= 0 - phải đi cùng nhau nên chạy trong transaction."""
    rows = OrderItem.objects.filter(order_id=order, product_id=product_id)
    rows.update(quantity=F("quantity") + delta)
    return bool(rows.filter(quantity__lte=0).delete()[0])


def change_item(customer_id, guest_token, product_id, delta):
    """-> (order_id, removed). Thử lại khi deadlock/khóa duy nhất/giỏ vừa bị chốt."""
    for attempt in range(3):
        try:
            if WRITE_MODE == "txn":
                with transaction.atomic():
                    order_id = get_or_create_cart(customer_id, guest_token).id
                    removed = False
                    if delta > 0:
                        rows = OrderItem.objects.filter(order_id=order_id, product_id=product_id)
                        if not rows.update(quantity=F("quantity") + delta):
                            OrderItem.objects.create(order_id=order_id, product_id=product_id, quantity=delta)
                    else:
                        removed = _decrease(order_id, product_id, delta)
                    bump(order_id)
                return order_id, removed
            order_id = find_cart_id(customer_id, guest_token)
            if order_id is None:
                order_id = Order.objects.create(customer_id=customer_id, guest_token=guest_token, complete=False,
                                                updated_at=timezone.now()).id
            if delta > 0:
                if not _upsert_item(order_id, product_id, delta):
                    continue  # giỏ vừa bị chốt: lần sau tìm/tạo giỏ mới
                removed = False
            else:
                with transaction.atomic():
                    removed = _decrease(order_id, product_id, delta)
            bump(order_id)
            return order_id, removed
        except (IntegrityError, OperationalError) as exc:
            if attempt == 2 or not is_retryable(exc):
                raise
    raise CartClosed()
