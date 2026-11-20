#!/usr/bin/env bash
# Dọn dữ liệu do test tải tạo ra (CHỈ dùng ở môi trường test).
#   tools/cleanup.sh               xóa giỏ hàng/đơn hàng của lt_user_*, giỏ trống của khách vãng lai, session
#   tools/cleanup.sh --users       xóa thêm tài khoản lt_user_* (lần test sau phải chạy lại create_loadtest_users.py)
# Tồn kho: dùng tools/stock.sh restore (không nằm ở đây).
set -Eeuo pipefail
NS="${NS:-greenmart}"
run() { kubectl -n "$NS" exec "deploy/$1" -- python manage.py shell -c "$2" 2>/dev/null | sed -n 's/^OUT://p'; }

IDS=$(run user-service "from django.contrib.auth.models import User
print('OUT:' + ','.join(str(i) for i in User.objects.filter(username__startswith='lt_user_').values_list('id', flat=True)))")
echo "Số tài khoản lt_user_*: $(echo "$IDS" | tr ',' '\n' | grep -c . || true)"

run order-service "from orders.models import Order
ids = [int(i) for i in '$IDS'.split(',') if i]
a = Order.objects.filter(customer_id__in=ids).delete()[0]
b = Order.objects.filter(customer_id__isnull=True, complete=False).delete()[0]
print(f'OUT:Đã xóa {a} dòng đơn/giỏ của lt_user_* và {b} dòng giỏ khách vãng lai (gồm cả order item)')"

run web "from django.contrib.sessions.models import Session
from django.core.cache import cache
n = Session.objects.all().delete()[0]
try:
    cache.clear()
except Exception:
    pass
print(f'OUT:Đã xóa {n} session (mọi người phải đăng nhập lại)')"

if [ "${1:-}" = "--users" ]; then
  run user-service "from django.contrib.auth.models import User
print(f'OUT:Đã xóa {User.objects.filter(username__startswith=\"lt_user_\").delete()[0]} dòng tài khoản lt_user_*')"
fi
