#!/usr/bin/env bash
# Chạy JMeter không GUI, sinh báo cáo HTML và in bảng tóm tắt.
#   ./run-loadtest.sh [base_url] [profile=smoke] [do_checkout] [thêm tham số -J...]
#   ví dụ:  JMETER_HOME=/d/apache-jmeter-5.6.3 ./run-loadtest.sh http://<dns-ALB> load
# Profile (profiles/*.properties, mỗi bài tối đa 5 phút): smoke, load, stress, spike, soak, checkout
#   checkout: tự bật do_checkout=true (TẠO ĐƠN HÀNG THẬT) nếu không truyền tham số thứ 3.
# base_url bỏ trống thì lấy biến BASE, rồi tới địa chỉ ALB mặc định bên dưới.
# Cần: Java 17+, JMeter 5.6.x (JMETER_HOME hoặc `jmeter` trong PATH), users.csv (tạo bằng create_loadtest_users.py).
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_BASE="http://k8s-greenmart-d003b7e256-305952565.ap-southeast-1.elb.amazonaws.com"   # ĐỔI khi cluster tạo lại (ALB mới)
BASE_URL="${1:-${BASE:-$DEFAULT_BASE}}"; PROFILE="${2:-smoke}"
if [ -n "${3:-}" ]; then DO_CHECKOUT="$3"; elif [ "$PROFILE" = checkout ]; then DO_CHECKOUT=true; else DO_CHECKOUT=false; fi
shift $(( $# >= 3 ? 3 : $# ))

JMETER="${JMETER_HOME:+$JMETER_HOME/bin/}jmeter"
command -v "$JMETER" >/dev/null 2>&1 || [ -x "$JMETER" ] || { echo "Không tìm thấy JMeter (đặt JMETER_HOME hoặc thêm jmeter vào PATH)"; exit 1; }
PROPS="$HERE/profiles/$PROFILE.properties"
[ -f "$PROPS" ] || { echo "Profile không tồn tại: $PROFILE"; ls "$HERE/profiles"; exit 1; }
[ -f "$HERE/users.csv" ] || { echo "Thiếu users.csv (python create_loadtest_users.py --base $BASE_URL --count 300)"; exit 1; }

[[ "$BASE_URL" =~ ^(https?)://([^/:]+)(:([0-9]+))?/?$ ]] || { echo "base_url phải dạng http(s)://host[:port]"; exit 1; }
PROTO="${BASH_REMATCH[1]}"; HOST="${BASH_REMATCH[2]}"; PORT="${BASH_REMATCH[4]:-}"
[ -n "$PORT" ] || { [ "$PROTO" = https ] && PORT=443 || PORT=80; }
CANON="$PROTO://$HOST"; { [ "$PROTO" = http ] && [ "$PORT" = 80 ]; } || { [ "$PROTO" = https ] && [ "$PORT" = 443 ]; } || CANON="$CANON:$PORT"

# Trang web phải trả 200 trước khi nạp tải (đỡ mất 5 phút chạy vào địa chỉ sai)
code=$(curl -s -o /dev/null -m 15 -w "%{http_code}" "$CANON/gateway-health" || true)
[ "$code" = 200 ] || { echo "Không vào được $CANON/gateway-health (HTTP $code). Kiểm tra địa chỉ ALB (http, không phải https)."; exit 1; }

dur=$(sed -n 's/^duration=//p' "$PROPS")
[ "${dur:-0}" -le 300 ] || echo "Lưu ý: profile $PROFILE chạy ${dur}s (> 5 phút)."

if [ "$DO_CHECKOUT" = "true" ]; then
  echo "CẢNH BÁO: do_checkout=true TẠO ĐƠN HÀNG THẬT -> order-worker trừ tồn kho thật."
  echo "          Tồn kho thật chỉ vài món mỗi sản phẩm: chạy 'tools/stock.sh backup && tools/stock.sh set 100000' trước, 'tools/stock.sh restore' sau."
  sleep 5
fi

# Máy chạy JMeter cần đủ RAM cho vài trăm luồng
export HEAP="${HEAP:--Xms1g -Xmx3g}"
OUT="$HERE/results/$(date +%Y%m%d-%H%M%S)-$PROFILE"; mkdir -p "$OUT"
echo "Chạy profile '$PROFILE' -> $CANON | do_checkout=$DO_CHECKOUT | kết quả: $OUT"
"$JMETER" -n -t "$HERE/greenmart-loadtest.jmx" -q "$PROPS" \
  -Jhost="$HOST" -Jport="$PORT" -Jprotocol="$PROTO" -Jbase_url="$CANON" -Jdo_checkout="$DO_CHECKOUT" -Jusers_csv="$HERE/users.csv" "$@" \
  -l "$OUT/results.jtl" -j "$OUT/jmeter.log" -e -o "$OUT/report"

PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] && PYTHONIOENCODING=utf-8 "$PY" "$HERE/tools/summarize.py" "$OUT/results.jtl" | tee "$OUT/summary.txt"
echo "Báo cáo chi tiết: $OUT/report/index.html"
