from django.conf import settings
import logging
import time

from django.shortcuts import render

from greenmart_common.cache import safe_get, safe_set
from greenmart_common.client import NotFound, ServiceUnavailable

from .auth import AnonymousUser, SessionUser
from .services import users

log = logging.getLogger(__name__)

REVALIDATE_SECONDS = 60


class RemoteUserMiddleware:
    """Gắn request.user từ session. Cứ ~60s hỏi lại user-service để phát hiện tài khoản bị khóa/xóa/đổi quyền."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.user = self._resolve(request)
        return self.get_response(request)

    def _resolve(self, request):
        data = request.session.get("user")
        if not data:
            return AnonymousUser()
        # Dấu "vừa kiểm tra" nằm ở Valkey (60 giây), không ghi vào session: trước đây cứ 60 giây mỗi user đăng nhập
        # lại ghi session (cached_db = một lệnh ghi MySQL) chỉ để cập nhật thời điểm kiểm tra.
        mark = f"uchk:{data['id']}"
        if safe_get(mark):
            return SessionUser(data)
        if time.time() - data.get("checked_at", 0) > REVALIDATE_SECONDS:
            try:
                fresh = users.get(f"/api/users/{data['id']}/")
            except NotFound:
                request.session.flush()
                return AnonymousUser()
            except ServiceUnavailable:
                log.warning("user-service không phản hồi; tạm dùng danh tính trong session")
            else:
                if not fresh["is_active"]:
                    request.session.flush()
                    return AnonymousUser()
                changed = {k: fresh[k] for k in ("username", "email", "is_staff", "is_superuser") if data.get(k) != fresh[k]}
                if changed:  # chỉ ghi session khi thông tin user thật sự đổi
                    data.update(changed, checked_at=time.time())
                    request.session["user"] = data
                safe_set(mark, 1, REVALIDATE_SECONDS)
        return SessionUser(data)


class ServiceErrorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, ServiceUnavailable):
            log.error("Service phụ thuộc không khả dụng: %s", exception)
            return render(request, "service_unavailable.html", {"service": exception.service}, status=503)
        return None


ME_REFRESH_COOKIE = "gm_me"


class MeRefreshMiddleware:
    """Báo cho gm.js bỏ bản lưu /api/me/ (sessionStorage) khi trạng thái hiển thị đổi ở PHÍA SERVER:
    đăng nhập/đăng xuất (session key đổi), số món trong giỏ đổi (trang giỏ, chốt đơn...), có thông báo mới.
    Cách báo: đặt cookie gm_me=1 (JS đọc được); /api/me/ xóa cookie này sau khi trả trạng thái mới.
    Cookie chỉ là tín hiệu "hãy hỏi lại", không chứa dữ liệu. Đặt SAU MessageMiddleware để thấy thông báo đang chờ."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        key_before = request.COOKIES.get(settings.SESSION_COOKIE_NAME)
        response = self.get_response(request)
        if request.path == "/api/me/":
            if ME_REFRESH_COOKIE in request.COOKIES:
                response.delete_cookie(ME_REFRESH_COOKIE, samesite="Lax")
            return response
        storage = getattr(request, "_messages", None)
        # Thêm giỏ bằng fetch (gm.js): JS đã tự cập nhật số món từ JSON trả về, không cần báo hỏi lại.
        via_js = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        changed = (
            request.session.session_key != key_before
            or (not via_js and getattr(request, "_gm_cart_changed", False))
            or bool(storage is not None and getattr(storage, "_queued_messages", None))
        )
        if changed:
            response.set_cookie(ME_REFRESH_COOKIE, "1", max_age=300, samesite="Lax", httponly=False)
        return response
