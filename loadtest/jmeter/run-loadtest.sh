#!/usr/bin/env bash
# Chạy JMeter không GUI và sinh báo cáo HTML.
#   ./run-loadtest.sh <base_url> [profile=smoke|load|stress|soak] [do_checkout=false] [thêm tham số -J...]
#   ví dụ:  JMETER_HOME=/opt/apache-jmeter-5.6.3 ./run-loadtest.sh http://my-alb.ap-southeast-1.elb.amazonaws.com load
# Cần: Java 17+, JMeter 5.6.x (JMETER_HOME hoặc `jmeter` trong PATH), users.csv (tạo bằng create_loadtest_users.py).
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_URL="${1:-http://k8s-greenmart-d003b7e256-1548607219.ap-southeast-1.elb.amazonaws.com}"; PROFILE="${2:-smoke}"; DO_CHECKOUT="${3:-false}"; shift $(( $# >= 3 ? 3 : $# ))

JMETER="${JMETER_HOME:+$JMETER_HOME/bin/}jmeter"
command -v "$JMETER" >/dev/null 2>&1 || [ -x "$JMETER" ] || { echo "Không tìm thấy JMeter (đặt JMETER_HOME hoặc thêm jmeter vào PATH)"; exit 1; }
[ -f "$HERE/profiles/$PROFILE.properties" ] || { echo "Profile không tồn tại: $PROFILE"; ls "$HERE/profiles"; exit 1; }
[ -f "$HERE/users.csv" ] || { echo "Thiếu users.csv (python3 create_loadtest_users.py --base $BASE_URL --count 100)"; exit 1; }

[[ "$BASE_URL" =~ ^(https?)://([^/:]+)(:([0-9]+))?/?$ ]] || { echo "base_url phải dạng http(s)://host[:port]"; exit 1; }
PROTO="${BASH_REMATCH[1]}"; HOST="${BASH_REMATCH[2]}"; PORT="${BASH_REMATCH[4]:-}"
[ -n "$PORT" ] || { [ "$PROTO" = https ] && PORT=443 || PORT=80; }
CANON="$PROTO://$HOST"; { [ "$PROTO" = http ] && [ "$PORT" = 80 ]; } || { [ "$PROTO" = https ] && [ "$PORT" = 443 ]; } || CANON="$CANON:$PORT"

if [ "$DO_CHECKOUT" = "true" ]; then
  echo "CẢNH BÁO: do_checkout=true sẽ TẠO ĐƠN HÀNG THẬT cho mỗi lượt. Chỉ dùng ở môi trường test."; sleep 3
fi
OUT="$HERE/results/$(date +%Y%m%d-%H%M%S)-$PROFILE"; mkdir -p "$OUT"
echo "Chạy profile '$PROFILE' -> $CANON  | kết quả: $OUT"
"$JMETER" -n -t "$HERE/greenmart-loadtest.jmx" -q "$HERE/profiles/$PROFILE.properties" \
  -Jhost="$HOST" -Jport="$PORT" -Jprotocol="$PROTO" -Jbase_url="$CANON" -Jdo_checkout="$DO_CHECKOUT" -Jusers_csv="$HERE/users.csv" "$@" \
  -l "$OUT/results.jtl" -j "$OUT/jmeter.log" -e -o "$OUT/report"
echo "Xong. Mở báo cáo: $OUT/report/index.html"
