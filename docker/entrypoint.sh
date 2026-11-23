#!/bin/sh
# Entrypoint dùng chung cho mọi service Django.
#   RUN_MIGRATE=1        chạy migrate khi khởi động (mỗi service chỉ migrate DB của chính nó)
#   RUN_COLLECTSTATIC=1  gom static vào volume dùng chung cho gateway
#   DJANGO_SUPERUSER_*   nếu có, tạo superuser cho Django admin (service có admin)
set -e

if [ -n "${PROMETHEUS_MULTIPROC_DIR:-}" ]; then
  rm -rf "$PROMETHEUS_MULTIPROC_DIR"; mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
fi

if [ "${RUN_MIGRATE:-1}" = "1" ]; then
  echo "[entrypoint] migrate..."
  n=0
  until python manage.py migrate --noinput; do
    n=$((n+1))
    [ "$n" -ge 20 ] && { echo "[entrypoint] migrate thất bại sau $n lần"; exit 1; }
    echo "[entrypoint] DB chưa sẵn sàng, thử lại sau 3s ($n)"; sleep 3
  done
fi

if [ "${RUN_COLLECTSTATIC:-0}" = "1" ]; then
  python manage.py collectstatic --noinput >/dev/null
fi

if [ -n "${DJANGO_SUPERUSER_USERNAME:-}" ] && [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ] && [ "${CREATE_SUPERUSER:-0}" = "1" ]; then
  python manage.py createsuperuser --noinput 2>/dev/null || echo "[entrypoint] superuser đã tồn tại (bỏ qua)"
fi

# Truyền lệnh riêng (vd. order-worker: python manage.py consume_order_events) thì chạy lệnh đó thay cho gunicorn.
if [ "$#" -gt 0 ]; then
  exec "$@"
fi

# gthread: mỗi worker có nhiều thread, request đang chờ service khác/MySQL không chặn cả tiến trình
# (sync worker cũ: 2 worker = chỉ 2 request cùng lúc mỗi pod). Timeout 60s thay cho mặc định 30s.
#   WEB_CONCURRENCY   số worker (tiến trình), mặc định 2
#   GUNICORN_THREADS  số thread mỗi worker, mặc định 4  => mỗi pod xử lý 8 request cùng lúc
#   GUNICORN_TIMEOUT  giây, mặc định 60
#   GUNICORN_ACCESS_LOG  không đặt = ghi log từng request (như cũ); đặt rỗng = tắt (overlay EKS tắt:
#                        ở ~1.000 req/s, ghi log từng request tốn CPU cả trong pod lẫn container runtime trên node;
#                        số request/độ trễ đã có ở ALB, nginx và Prometheus)
ACCESS_LOG="${GUNICORN_ACCESS_LOG-1}"
exec gunicorn "${WSGI_MODULE}.wsgi:application" --bind 0.0.0.0:8000 \
  --workers "${WEB_CONCURRENCY:-2}" --worker-class gthread --threads "${GUNICORN_THREADS:-4}" \
  --timeout "${GUNICORN_TIMEOUT:-60}" --keep-alive 5 \
  ${ACCESS_LOG:+--access-logfile -} --error-logfile -
