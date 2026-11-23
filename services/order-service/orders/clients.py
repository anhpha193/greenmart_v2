from django.conf import settings

from greenmart_common.cache import cached
from greenmart_common.client import ServiceClient

catalog = ServiceClient("catalog-service", settings.CATALOG_SERVICE_URL, settings.INTERNAL_API_TOKEN)
promotions = ServiceClient("promotion-service", settings.PROMOTION_SERVICE_URL, settings.INTERNAL_API_TOKEN)


def fetch_products(ids, fresh=False):
    """Lấy thông tin sản phẩm hiện tại (giá, tên, trạng thái...) theo lô -> {id: product}.
    Hiển thị giỏ hàng: dùng bản cache 30 giây trong Valkey (bớt một lần gọi HTTP sang catalog mỗi trang giỏ).
    fresh=True (chốt đơn): luôn hỏi catalog để giá ghi vào đơn là giá hiện tại."""
    ids = sorted({int(i) for i in ids if i})
    if not ids:
        return {}
    key = ",".join(str(i) for i in ids)
    load = lambda: catalog.get("/api/products/", params={"ids": key})  # noqa: E731
    data = load() if fresh else cached("order-products", key, 30, load)
    return {p["id"]: p for p in data}
