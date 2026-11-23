"""Đọc cấu hình từ biến môi trường (12-factor)."""
import os


def env(name, default=None, required=False):
    value = os.environ.get(name)
    if value is None or value == "":  # biến đặt nhưng rỗng (vd. `FOO=` trong compose/configmap) coi như chưa đặt
        value = default
    if required and (value is None or value == ""):
        raise RuntimeError(f"Thiếu biến môi trường bắt buộc: {name}")
    return value


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def mysql_database(prefix="DB"):
    """Cấu hình DATABASES['default'] cho MySQL từ biến DB_* (utf8mb4, strict mode)."""
    return {
        "ENGINE": "django.db.backends.mysql",
        "NAME": env(f"{prefix}_NAME", required=True),
        "USER": env(f"{prefix}_USER", required=True),
        "PASSWORD": env(f"{prefix}_PASSWORD", required=True),
        "HOST": env(f"{prefix}_HOST", "mysql"),
        "PORT": env(f"{prefix}_PORT", "3306"),
        # Giữ kết nối MySQL giữa các request (giây). gunicorn gthread: MỖI THREAD giữ một kết nối, nên
        # số kết nối = pod x worker x thread. RDS db.t4g.micro chỉ cho 60 kết nối -> overlay EKS đặt 0
        # (mở/đóng theo request; catalog/web phần lớn đọc từ Valkey nên ít khi chạm MySQL).
        "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "60")),
        # Health check = ping MySQL ở đầu mỗi request khi dùng lại kết nối (profile 1.2.7: 12% thời gian của
        # order-service). Tắt (DB_CONN_HEALTH_CHECKS=0) thì kết nối chết (DB restart/failover, quá wait_timeout)
        # làm lỗi đúng 1 request trên kết nối đó rồi Django tự mở lại. Mặc định bật; chỉ tắt sau khi A/B đạt.
        "CONN_HEALTH_CHECKS": env_bool("DB_CONN_HEALTH_CHECKS", True),
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    }
