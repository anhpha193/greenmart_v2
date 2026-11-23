"""web (BFF): giao diện storefront + admin panel. Không chứa dữ liệu nghiệp vụ; gọi các service qua HTTP.
Database web_db chỉ dùng cho session."""
import os
from pathlib import Path

from greenmart_common.cache import cache_settings, redis_url
from greenmart_common.env import env, env_bool, env_list, mysql_database

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = env("SECRET_KEY", required=True)
DEBUG = env_bool("DEBUG", False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "*")
INTERNAL_API_TOKEN = env("INTERNAL_API_TOKEN", required=True)

USER_SERVICE_URL = env("USER_SERVICE_URL", "http://user-service:8000")
CATALOG_SERVICE_URL = env("CATALOG_SERVICE_URL", "http://catalog-service:8000")
ORDER_SERVICE_URL = env("ORDER_SERVICE_URL", "http://order-service:8000")
PROMOTION_SERVICE_URL = env("PROMOTION_SERVICE_URL", "http://promotion-service:8000")

INSTALLED_APPS = [
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "app",
    "adminpanel",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "greenmart.middleware.RemoteUserMiddleware",   # request.user lấy từ user-service
    "django.contrib.messages.middleware.MessageMiddleware",
    "greenmart.middleware.MeRefreshMiddleware",     # báo gm.js hỏi lại /api/me/ khi đăng nhập/giỏ/thông báo đổi
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "greenmart.middleware.ServiceErrorMiddleware",  # service phụ thuộc chết -> trang 503 thân thiện
]

ROOT_URLCONF = "greenmart.urls"
WSGI_APPLICATION = "greenmart.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.messages.context_processors.messages",
        "greenmart.context.user",
    ]},
}]

DATABASES = {"default": mysql_database("DB")}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Có Valkey (REDIS_URL): session đọc từ cache, chỉ ghi xuống web_db khi session đổi (cached_db),
# nên mỗi request không còn một câu SELECT vào bảng session. Valkey mất dữ liệu thì session vẫn còn trong DB.
CACHES = cache_settings("web")
# WEB_SESSION_BACKEND (khi có Valkey):
#   cached_db (mặc định): đọc từ Valkey, mỗi lần tạo/đổi session vẫn ghi MySQL web_db (bền, nhưng mỗi khách mới = 1 INSERT)
#   cache   (overlay EKS từ 1.2.6): chỉ Valkey -> không ghi MySQL. Đánh đổi: Valkey khởi động lại/thay node thì mọi
#           người phải đăng nhập lại và giỏ của khách chưa đăng nhập không còn gắn với trình duyệt (giỏ user không mất).
_SESSION_BACKEND = env("WEB_SESSION_BACKEND", "cached_db")
if not redis_url():
    SESSION_ENGINE = "django.contrib.sessions.backends.db"
elif _SESSION_BACKEND == "cache":
    SESSION_ENGINE = "django.contrib.sessions.backends.cache"
else:
    SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = env("STATIC_ROOT", "/staticfiles")

LOGIN_URL = "/loginn/"
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "http://localhost:8088,http://127.0.0.1:8088")
# Địa chỉ thêm do Terraform ghi vào ConfigMap greenmart-infra (vd. https://<id>.cloudfront.net khi đi qua CloudFront).
CSRF_TRUSTED_ORIGINS += env_list("EXTRA_CSRF_TRUSTED_ORIGINS", "")
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

# Feature flag: gm.js lưu /api/me/ trong sessionStorage (1 lần gọi mỗi phiên thay vì mỗi trang).
# Tắt (WEB_CLIENT_ME_CACHE=0) để quay về gọi /api/me/ ở mọi trang nếu thấy trạng thái hiển thị bị cũ.
CLIENT_ME_CACHE = env_bool("WEB_CLIENT_ME_CACHE", True)

# 1.2.8 - mỗi nhóm thay đổi một cờ để A/B và quay lại nhanh (đổi ConfigMap rồi restart pod, không cần build image):
#   WEB_LEAN_CART        số món giỏ ghi bằng 1 lệnh SET ... GET; thêm giỏ dùng luôn số món order-service trả về
#   WEB_CART_DIRECT_READ trang giỏ/thanh toán/đếm giỏ đọc thẳng Cart Read Model trong Valkey, trượt mới gọi order-service
WEB_LEAN_CART = env_bool("WEB_LEAN_CART", True)
WEB_CART_DIRECT_READ = env_bool("WEB_CART_DIRECT_READ", True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}

# Quan sát: Prometheus /metrics, xem bằng Grafana (bật bằng ENABLE_PROMETHEUS, mặc định tắt).
from greenmart_common.observability import apply as _apply_observability  # noqa: E402

_apply_observability(globals(), "web")
