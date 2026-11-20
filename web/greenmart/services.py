"""Client tới các service nội bộ (dùng chung một Session/connection pool cho mỗi service)."""
import hashlib
import json

from django.conf import settings

from greenmart_common.cache import bump_version, cached
from greenmart_common.client import ServiceClient
from greenmart_common.env import env

# Nhóm key cache dữ liệu catalog phía web. Tăng phiên bản = bỏ toàn bộ bản cache cũ cùng lúc.
CATALOG_VIEW_GROUP = "catalog-view"


class CatalogClient(ServiceClient):
    """Client catalog: mọi lần GHI (POST/PUT/DELETE) thành công từ web (trang quản trị, viết đánh giá)
    đều bỏ cache catalog phía web, để người dùng thấy ngay thay đổi của chính mình."""

    def request(self, method, path, **kwargs):
        data = super().request(method, path, **kwargs)
        if method.upper() != "GET":
            bump_version(CATALOG_VIEW_GROUP)
        return data


class CachedReader:
    """Đọc catalog cho storefront qua cache Valkey (cache-aside), TTL ngắn.

    Trang chủ, danh sách, chi tiết, tìm kiếm không phải gọi HTTP sang catalog-service mỗi lần nữa.
    Thay đổi không đi qua web (Django admin của catalog, order-worker trừ tồn kho) hiện ra chậm tối đa TTL giây.
    """

    def __init__(self, client, ttl):
        self.client = client
        self.ttl = ttl

    def get(self, path, params=None):
        raw = path + "?" + json.dumps(params or {}, sort_keys=True, ensure_ascii=False)
        key = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        return cached(CATALOG_VIEW_GROUP, key, self.ttl, lambda: self.client.get(path, params=params))


users = ServiceClient("user-service", settings.USER_SERVICE_URL, settings.INTERNAL_API_TOKEN)
catalog = CatalogClient("catalog-service", settings.CATALOG_SERVICE_URL, settings.INTERNAL_API_TOKEN, timeout=(2, 15))
orders = ServiceClient("order-service", settings.ORDER_SERVICE_URL, settings.INTERNAL_API_TOKEN)
promotions = ServiceClient("promotion-service", settings.PROMOTION_SERVICE_URL, settings.INTERNAL_API_TOKEN)

# Storefront đọc catalog qua đây; trang quản trị vẫn dùng `catalog` (luôn đọc mới).
catalog_read = CachedReader(catalog, ttl=int(env("STOREFRONT_CATALOG_CACHE_SECONDS", "30")))
