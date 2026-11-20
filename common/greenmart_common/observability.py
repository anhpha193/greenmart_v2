"""Quan sát (observability): chỉ Prometheus (django-prometheus); xem bằng Grafana. Bật bằng biến môi trường.

  ENABLE_PROMETHEUS=1   thêm endpoint /metrics (số request, độ trễ, mã trạng thái theo view)

Mặc định TẮT nên chạy local / docker compose như cũ. Gọi ở cuối settings.py của mỗi service:
    from greenmart_common.observability import apply
    apply(globals(), "user-service")
và trong urls.py:
    urlpatterns = observability_urls() + urlpatterns
"""
import os

from django.urls import include, path

from .env import env_bool


def prometheus_enabled():
    return env_bool("ENABLE_PROMETHEUS", False)


def apply(settings, service_name):
    """Chèn app/middleware quan sát vào settings của service (dict `globals()` của settings.py)."""
    if not prometheus_enabled():
        return
    # Chế độ nhiều worker: thư mục chung phải tồn tại trước khi tạo metric đầu tiên (kể cả khi chạy manage.py).
    if multiprocess_dir():
        os.makedirs(multiprocess_dir(), exist_ok=True)
    settings["INSTALLED_APPS"] = list(settings["INSTALLED_APPS"]) + ["django_prometheus"]
    settings["MIDDLEWARE"] = (
        ["django_prometheus.middleware.PrometheusBeforeMiddleware"]
        + list(settings["MIDDLEWARE"])
        + ["django_prometheus.middleware.PrometheusAfterMiddleware"]
    )


def observability_urls():
    """URL /metrics (chỉ khi bật Prometheus). Gateway chặn /metrics ra Internet; Prometheus scrape thẳng vào pod."""
    if not prometheus_enabled():
        return []
    return [path("", include("django_prometheus.urls"))]


def multiprocess_dir():
    return os.environ.get("PROMETHEUS_MULTIPROC_DIR", "")
