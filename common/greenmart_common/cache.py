"""Cache dùng chung cho các service: ElastiCache (Valkey) khi có REDIS_URL, nếu không thì bộ nhớ trong tiến trình.

  REDIS_URL=rediss://<endpoint>:6379/0   (Terraform ghi vào ConfigMap greenmart-infra; rediss = có TLS)

Theo hướng dẫn ElastiCache cho e-commerce của AWS: đọc theo kiểu cache-aside, khi dữ liệu đổi thì xóa/đổi
phiên bản key thay vì ghi đè, và khi cache lỗi thì vẫn chạy bằng database (chậm hơn nhưng không hỏng).

Dùng trong settings.py:
    from greenmart_common.cache import cache_settings
    CACHES = cache_settings("catalog")
"""
import logging
import time

from django.core.cache import cache

from .env import env

log = logging.getLogger(__name__)


def redis_url():
    return env("REDIS_URL", "")


def cache_settings(key_prefix):
    url = redis_url()
    if not url:
        return {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "KEY_PREFIX": key_prefix}}
    return {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": url,
            "KEY_PREFIX": key_prefix,
            "TIMEOUT": 300,
            # Timeout ngắn: Valkey chậm/chết thì bỏ qua cache và đọc DB, không treo request.
            "OPTIONS": {"socket_connect_timeout": 1, "socket_timeout": 0.5, "health_check_interval": 30},
        }
    }


def safe_get(key):
    try:
        return cache.get(key)
    except Exception:  # noqa: BLE001 - cache lỗi không được làm hỏng request
        log.warning("cache get lỗi: %s", key, exc_info=True)
        return None


def safe_set(key, value, timeout):
    try:
        cache.set(key, value, timeout)
    except Exception:  # noqa: BLE001
        log.warning("cache set lỗi: %s", key, exc_info=True)


# 1.2.8: nhớ số phiên bản trong tiến trình VERSION_MEMO_SECONDS giây. Mỗi lần đọc qua cached() bớt một round-trip
# Valkey (trước: GET phiên bản + GET dữ liệu). Đánh đổi: sau khi một nhóm đổi phiên bản (vd. sửa sản phẩm), tiến trình
# KHÁC thấy dữ liệu mới chậm thêm tối đa ngần ấy giây (tiến trình tự bump thì thấy ngay). 0 = tắt.
VERSION_MEMO_SECONDS = float(env("CACHE_VERSION_MEMO_SECONDS", "1"))
_memo = {}


def get_version(name):
    """Số phiên bản của một nhóm key. Tăng số này là vô hiệu toàn bộ key của nhóm cùng lúc."""
    if VERSION_MEMO_SECONDS > 0:
        hit = _memo.get(name)
        now = time.monotonic()
        if hit and hit[1] > now:
            return hit[0]
    try:
        value = cache.get_or_set(f"v:{name}", 1, None)
    except Exception:  # noqa: BLE001
        return None
    if VERSION_MEMO_SECONDS > 0 and value is not None:
        _memo[name] = (value, time.monotonic() + VERSION_MEMO_SECONDS)
    return value


def bump_version(name):
    _memo.pop(name, None)
    try:
        cache.incr(f"v:{name}")
    except ValueError:  # key chưa có
        try:
            cache.set(f"v:{name}", 2, None)
        except Exception:  # noqa: BLE001
            log.warning("không tăng được phiên bản cache %s", name, exc_info=True)
    except Exception:  # noqa: BLE001
        log.warning("không tăng được phiên bản cache %s", name, exc_info=True)


def cached(group, key, timeout, compute):
    """Cache-aside: trả giá trị trong cache nếu có, không thì gọi compute() rồi ghi lại.
    Cache lỗi (hoặc không lấy được phiên bản) thì gọi thẳng compute()."""
    version = get_version(group)
    if version is None:
        return compute()
    full_key = f"{group}:{version}:{key}"
    value = safe_get(full_key)
    if value is None:
        value = compute()
        safe_set(full_key, value, timeout)
    return value
