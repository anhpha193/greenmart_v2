"""Số món trong giỏ (hiện ở góc trang, /api/me/, JSON sau khi thêm giỏ) lưu trong Valkey, KHÔNG lưu trong session.

Trước 1.2.6 số này nằm trong session (cached_db): mỗi lần thêm giỏ là một lần ghi session xuống MySQL web_db
(khoảng 130 lần/giây ở trần) -> RDS hết kết nối. Giờ: key theo user (đã đăng nhập) hoặc theo session key (khách),
TTL 120 giây. Nguồn đúng vẫn là order-service; Valkey mất dữ liệu thì chỉ hỏi lại order-service.

1.2.8 (WEB_LEAN_CART, mặc định bật): ghi bằng một lệnh SET ... GET (Valkey >= 6.2) - vừa ghi số mới vừa lấy số cũ
để biết số món có đổi không (báo gm.js hỏi lại /api/me/), thay cho GET rồi SET (2 round-trip). Giá trị là chuỗi
"số_món:thời_điểm" ở key thô gm:cc:... (không qua pickle của Django). Không có Valkey (local/test) thì như cũ.
"""
import time

from django.conf import settings

from greenmart_common.cache import safe_get, safe_set
from greenmart_common.rawcache import client

FRESH_SECONDS = 60     # quá thời gian này thì hỏi lại order-service (giỏ có thể đổi ở thiết bị khác)
KEEP_SECONDS = 120


def _key(request):
    if request.user.is_authenticated:
        return f"cc:u{request.user.id}"
    if request.session.session_key:
        return f"cc:s{request.session.session_key}"
    return None


def _raw():
    return client() if settings.WEB_LEAN_CART else None


def _parse(raw):
    """b"3:1795414632" -> (3, 1795414632.0); sai định dạng -> None."""
    try:
        count, ts = (raw.decode() if isinstance(raw, bytes) else raw).split(":")
        return int(count), float(ts)
    except (AttributeError, ValueError):
        return None


def get_fresh(request):
    """Số món còn mới trong Valkey, hoặc None nếu chưa có/đã cũ/Valkey lỗi."""
    key = _key(request)
    if not key:
        return None
    c = _raw()
    if c is not None:
        try:
            saved = _parse(c.get("gm:" + key))
        except Exception:  # noqa: BLE001 - Valkey lỗi: hỏi order-service
            saved = None
    else:
        saved = safe_get(key)
    if saved and time.time() - saved[1] < FRESH_SECONDS:
        return saved[0]
    return None


def remember(request, count):
    """Ghi số món mới. Đánh dấu request để MeRefreshMiddleware báo gm.js nếu số đổi ở phía server."""
    key = _key(request)
    if not key:
        return
    count = int(count or 0)
    c = _raw()
    if c is not None:
        try:
            old = _parse(c.set("gm:" + key, f"{count}:{time.time():.0f}", ex=KEEP_SECONDS, get=True))
        except Exception:  # noqa: BLE001
            old = None
    else:
        old = safe_get(key)
        safe_set(key, [count, time.time()], KEEP_SECONDS)
    if not old or old[0] != count:
        request._gm_cart_changed = True


def forget(*keys):
    """Xóa số món đã lưu (gộp giỏ khi đăng nhập làm số món đổi)."""
    c = _raw()
    for k in keys:
        if not k:
            continue
        if c is not None:
            try:
                c.delete("gm:" + k)
            except Exception:  # noqa: BLE001
                pass
        else:
            safe_set(k, None, 1)


def user_key(user_id):
    return f"cc:u{user_id}"


def guest_key(session_key):
    return f"cc:s{session_key}" if session_key else None
