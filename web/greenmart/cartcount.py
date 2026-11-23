"""Số món trong giỏ (hiện ở góc trang, /api/me/, JSON sau khi thêm giỏ) lưu trong Valkey, KHÔNG lưu trong session.

Trước 1.2.6 số này nằm trong session (cached_db): mỗi lần thêm giỏ là một lần ghi session xuống MySQL web_db
(khoảng 130 lần/giây ở trần) -> RDS hết kết nối. Giờ: key theo user (đã đăng nhập) hoặc theo session key (khách),
TTL 120 giây. Nguồn đúng vẫn là order-service; Valkey mất dữ liệu thì chỉ hỏi lại order-service.
"""
import time

from greenmart_common.cache import safe_get, safe_set

FRESH_SECONDS = 60     # quá thời gian này thì hỏi lại order-service (giỏ có thể đổi ở thiết bị khác)
KEEP_SECONDS = 120


def _key(request):
    if request.user.is_authenticated:
        return f"cc:u{request.user.id}"
    if request.session.session_key:
        return f"cc:s{request.session.session_key}"
    return None


def get_fresh(request):
    """Số món còn mới trong Valkey, hoặc None nếu chưa có/đã cũ/Valkey lỗi."""
    key = _key(request)
    saved = safe_get(key) if key else None
    if saved and time.time() - saved[1] < FRESH_SECONDS:
        return saved[0]
    return None


def remember(request, count):
    """Ghi số món mới. Đánh dấu request để MeRefreshMiddleware báo gm.js nếu số đổi ở phía server."""
    key = _key(request)
    if not key:
        return
    count = int(count or 0)
    saved = safe_get(key)
    if not saved or saved[0] != count:
        request._gm_cart_changed = True
    safe_set(key, [count, time.time()], KEEP_SECONDS)


def forget(*keys):
    """Xóa số món đã lưu (gộp giỏ khi đăng nhập làm số món đổi)."""
    for k in keys:
        if k:
            safe_set(k, None, 1)


def user_key(user_id):
    return f"cc:u{user_id}"


def guest_key(session_key):
    return f"cc:s{session_key}" if session_key else None
