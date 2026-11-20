#!/usr/bin/env bash
# Quản lý tồn kho cho bài test đặt hàng (profile checkout). order-worker trừ tồn kho thật sau mỗi đơn,
# mà tồn kho thật chỉ vài món/sản phẩm: hết hàng thì trang sản phẩm trống và test hỏng.
#   tools/stock.sh show            xem tồn kho hiện tại
#   tools/stock.sh backup          lưu tồn kho + trạng thái vào stock-backup.json (không ghi đè bản đã có)
#   tools/stock.sh set 100000      đặt mọi sản phẩm (trừ sản phẩm đã ẩn) về số lượng này, trạng thái "Còn hàng"
#   tools/stock.sh restore         trả lại đúng như bản backup, rồi xóa file backup
# Cần kubectl trỏ đúng cluster. Cache sản phẩm (Valkey) được làm mới sau mỗi lần sửa.
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NS="${NS:-greenmart}"
FILE="${STOCK_FILE:-$HERE/stock-backup.json}"
shell() { kubectl -n "$NS" exec deploy/catalog-service -- python manage.py shell -c "$1" 2>/dev/null | sed -n 's/^OUT://p'; }

case "${1:-}" in
  show)
    shell "from catalog.models import Product
for p in Product.objects.order_by('id'): print(f'OUT:{p.id:>4}  {p.slug:<24} stock={p.stock:<8} {p.status}')" ;;
  backup)
    [ ! -f "$FILE" ] || { echo "Đã có $FILE (bản gốc chưa restore). Không ghi đè; xóa tay nếu chắc chắn."; exit 1; }
    shell "import json
from catalog.models import Product
print('OUT:' + json.dumps({p.id: [p.stock, p.status] for p in Product.objects.all()}))" > "$FILE"
    [ -s "$FILE" ] || { rm -f "$FILE"; echo "Không đọc được tồn kho"; exit 1; }
    echo "Đã lưu tồn kho vào $FILE" ;;
  set)
    N="${2:?Cần số lượng, vd: tools/stock.sh set 100000}"
    [[ "$N" =~ ^[0-9]+$ ]] || { echo "Số lượng không hợp lệ: $N"; exit 1; }
    [ -f "$FILE" ] || { echo "Chưa có backup: chạy 'tools/stock.sh backup' trước"; exit 1; }
    shell "from catalog.models import Product
from greenmart_common.cache import bump_version
n = Product.objects.exclude(status='hidden').update(stock=$N, status='available')
bump_version('catalog')
print(f'OUT:Đã đặt tồn kho {n} sản phẩm = $N')" ;;
  restore)
    [ -f "$FILE" ] || { echo "Không có $FILE"; exit 1; }
    kubectl -n "$NS" exec deploy/catalog-service -- env STOCK_JSON="$(cat "$FILE")" python manage.py shell -c "import json, os
from catalog.models import Product
from greenmart_common.cache import bump_version
data = json.loads(os.environ['STOCK_JSON'])
for pid, (stock, status) in data.items():
    Product.objects.filter(pk=int(pid)).update(stock=stock, status=status)
bump_version('catalog')
print(f'OUT:Đã khôi phục {len(data)} sản phẩm')" 2>/dev/null | sed -n 's/^OUT://p'
    rm -f "$FILE" ;;
  *) sed -n '2,8p' "$0"; exit 1 ;;
esac
