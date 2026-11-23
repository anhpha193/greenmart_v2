"""JSON nhanh hơn cho API nội bộ (orjson, viết bằng Rust): DRF renderer + hàm đọc cho ServiceClient.
Không có orjson (chạy local chưa cài) thì quay về json chuẩn. Kết quả giống JSONRenderer của DRF:
Decimal -> chuỗi, datetime -> ISO 8601."""
import decimal
import json
import uuid

from rest_framework.renderers import BaseRenderer, JSONRenderer

try:
    import orjson
except ImportError:  # pragma: no cover
    orjson = None


def _default(obj):
    if isinstance(obj, (decimal.Decimal, uuid.UUID)):
        return str(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    raise TypeError(f"Không chuyển được {type(obj).__name__} sang JSON")


def loads(data):
    return orjson.loads(data) if orjson else json.loads(data)


if orjson:
    class FastJSONRenderer(BaseRenderer):
        media_type = "application/json"
        format = "json"
        charset = None

        def render(self, data, accepted_media_type=None, renderer_context=None):
            if data is None:
                return b""
            # OPT_UTC_Z: thời gian ghi "...Z" giống JSONRenderer của DRF (mặc định orjson ghi "+00:00")
            return orjson.dumps(data, default=_default, option=orjson.OPT_NON_STR_KEYS | orjson.OPT_UTC_Z)
else:  # pragma: no cover
    FastJSONRenderer = JSONRenderer
