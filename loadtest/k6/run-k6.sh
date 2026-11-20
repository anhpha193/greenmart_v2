#!/usr/bin/env bash
# Chạy kịch bản k6 và lưu kết quả.
#   ./run-k6.sh [base_url] [profile=smoke] [thêm tham số k6, vd. -e RPS=300]
#   ví dụ:  ./run-k6.sh "" capacity -e RPS=300
#           ./run-k6.sh "" ramp -e RPS_MAX=600
# Profile (mỗi bài tối đa 5 phút): smoke, capacity, ramp, stress, spike  (xem greenmart.js)
# base_url bỏ trống thì lấy biến BASE, rồi tới địa chỉ ALB mặc định bên dưới.
# k6: biến K6 hoặc D:\Tools\k6\k6.exe hoặc `k6` trong PATH. Cần users.csv (loadtest/jmeter/create_loadtest_users.py).
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_BASE="http://k8s-greenmart-d003b7e256-305952565.ap-southeast-1.elb.amazonaws.com"   # ĐỔI khi cluster tạo lại (ALB mới)
BASE_URL="${1:-${BASE:-$DEFAULT_BASE}}"; BASE_URL="${BASE_URL%/}"; PROFILE="${2:-smoke}"
shift $(( $# >= 2 ? 2 : $# ))

K6="${K6:-}"
[ -n "$K6" ] || { [ -x /d/Tools/k6/k6.exe ] && K6=/d/Tools/k6/k6.exe; } || true
[ -n "$K6" ] || K6="$(command -v k6 || true)"
[ -n "$K6" ] || { echo "Không tìm thấy k6 (đặt biến K6, hoặc giải nén vào D:\\Tools\\k6\\k6.exe)"; exit 1; }
[ -f "$HERE/../jmeter/users.csv" ] || { echo "Thiếu loadtest/jmeter/users.csv: python loadtest/jmeter/create_loadtest_users.py --base $BASE_URL --count 300"; exit 1; }

code=$(curl -s -o /dev/null -m 15 -w "%{http_code}" "$BASE_URL/gateway-health" || true)
[ "$code" = 200 ] || { echo "Không vào được $BASE_URL/gateway-health (HTTP $code). Kiểm tra địa chỉ ALB (http, không phải https)."; exit 1; }

OUT="$HERE/results/$(date +%Y%m%d-%H%M%S)-$PROFILE"; mkdir -p "$OUT"
echo "k6 profile '$PROFILE' -> $BASE_URL | kết quả: $OUT"
cd "$HERE"
"$K6" run --summary-mode=full --summary-export="$OUT/summary.json" --out csv="$OUT/metrics.csv.gz" \
  -e BASE_URL="$BASE_URL" -e PROFILE="$PROFILE" "$@" greenmart.js 2>&1 | tee "$OUT/summary.txt"
PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] && PYTHONIOENCODING=utf-8 "$PY" "$HERE/timeline.py" "$OUT/metrics.csv.gz" | tee "$OUT/timeline.txt"
echo "Kết quả: $OUT/summary.txt"
