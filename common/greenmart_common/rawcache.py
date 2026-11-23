"""Truy cập Valkey trực tiếp (redis-py) cho các thao tác Django cache API không có: SET ... GET, Lua (so sánh rồi ghi).

Dùng cùng REDIS_URL và cùng timeout ngắn với Django RedisCache. Không có Valkey (chạy local/test với LocMemCache)
thì client() trả None và nơi gọi tự dùng cách thay thế. Key ở đây là key "thô" (không có KEY_PREFIX của từng
service) để web và order-service đọc chung được.
"""
import logging
import threading

log = logging.getLogger(__name__)
_lock = threading.Lock()
_client = None
_ready = False


def client():
    """redis.Redis dùng chung cho cả tiến trình (thread-safe, có pool riêng), hoặc None nếu không có Valkey."""
    global _client, _ready
    if not _ready:
        with _lock:
            if not _ready:
                try:
                    from django.core.cache import caches
                    from django.core.cache.backends.redis import RedisCache
                    backend = caches["default"]
                    _client = backend._cache.get_client(write=True) if isinstance(backend, RedisCache) else None
                except Exception:  # noqa: BLE001
                    log.warning("không lấy được client Valkey", exc_info=True)
                    _client = None
                _ready = True
    return _client
