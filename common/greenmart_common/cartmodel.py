"""Cart Read Model (1.2.8): JSON giỏ hàng đã dựng sẵn trong Valkey, order-service ghi, web và order-service cùng đọc.

Key  : gm:cart:<owner>        owner = c<customer_id> | g<guest_token>
Value: b"<order_id>:<version>|<json>"   json rỗng = "tombstone" (đã đổi, chưa dựng lại)

Nhãn (order_id, version) tăng đơn điệu cho mỗi chủ giỏ:
  - version là cột Order.version, tăng 1 trong CÙNG câu lệnh/transaction với mỗi lần ghi giỏ;
  - giỏ mới (sau khi chốt đơn) có order_id lớn hơn giỏ cũ (khóa tự tăng).
Ghi vào Valkey bằng Lua "so sánh rồi ghi" (một round-trip, nguyên tử): chỉ ghi khi nhãn mới >= nhãn đang có, nên
request chậm mang dữ liệu cũ không bao giờ ghi đè dữ liệu mới, kể cả khi nhiều request ghi cùng lúc.
  data : ghi bản dựng có nhãn; cùng nhãn chỉ được thay tombstone
  tomb : ghi tombstone có nhãn (sau khi chốt đơn/xóa giỏ: giỏ biến mất nên không còn dữ liệu để dựng)
  empty: người đọc thấy KHÔNG có giỏ mở -> chỉ thay được tombstone (giữ nhãn của nó) hoặc key trống (nhãn 0:0)
TTL ngắn (CART_TTL) là lớp an toàn khi một lần ghi Valkey bị lỗi. Valkey lỗi thì nơi gọi đọc thẳng MySQL.
"""
import logging

from django.core.cache import cache

from .rawcache import client

log = logging.getLogger(__name__)

CART_TTL = 60

_LUA = """
local cur = redis.call('GET', KEYS[1])
local mode = ARGV[1]
local no = tonumber(ARGV[2])
local nv = tonumber(ARGV[3])
local ttl = tonumber(ARGV[5])
if cur then
  local co, cv, p = string.match(cur, '^(%d+):(%d+)|()')
  if co then
    co = tonumber(co)
    cv = tonumber(cv)
    local tomb = (p > #cur)
    if mode == 'empty' then
      if not tomb then return 0 end
      redis.call('SET', KEYS[1], string.format('%d:%d|', co, cv) .. ARGV[4], 'EX', ttl)
      return 1
    end
    if no < co or (no == co and nv < cv) then return 0 end
    if no == co and nv == cv and (mode == 'tomb' or not tomb) then return 0 end
  end
end
if mode == 'empty' then
  no = 0
  nv = 0
end
local val = string.format('%d:%d|', no, nv)
if mode ~= 'tomb' then val = val .. ARGV[4] end
redis.call('SET', KEYS[1], val, 'EX', ttl)
return 1
"""
_script = None


def owner_key(customer_id, guest_token):
    return f"c{customer_id}" if customer_id is not None else f"g{guest_token}"


def key(owner):
    return f"gm:cart:{owner}"


def parse(raw):
    """bytes -> (order_id, version, payload|None). payload None = tombstone. Sai định dạng -> None."""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.encode()
    head, sep, payload = raw.partition(b"|")
    if not sep:
        return None
    try:
        oid, ver = (int(x) for x in head.split(b":"))
    except ValueError:
        return None
    return oid, ver, (payload or None)


def get(owner):
    """(order_id, version, payload|None) hoặc None (không có/Valkey lỗi)."""
    c = client()
    try:
        raw = c.get(key(owner)) if c is not None else cache.get(key(owner))
    except Exception:  # noqa: BLE001 - cache lỗi: đọc MySQL
        log.warning("đọc read model giỏ lỗi", exc_info=True)
        return None
    return parse(raw)


def _write(owner, mode, oid, ver, payload=b""):
    c = client()
    try:
        if c is not None:
            global _script
            if _script is None:
                _script = c.register_script(_LUA)
            return bool(_script(keys=[key(owner)], args=[mode, oid, ver, payload, CART_TTL]))
        return _write_local(owner, mode, oid, ver, payload)
    except Exception:  # noqa: BLE001
        log.warning("ghi read model giỏ lỗi, xóa key cho an toàn", exc_info=True)
        try:
            (c.delete(key(owner)) if c is not None else cache.delete(key(owner)))
        except Exception:  # noqa: BLE001
            pass
        return False


def _write_local(owner, mode, oid, ver, payload):
    """Cùng luật với Lua, cho LocMemCache (chạy local/test; không nguyên tử, không dùng trên EKS)."""
    cur = parse(cache.get(key(owner)))
    if cur:
        co, cv, cur_payload = cur
        tomb = cur_payload is None
        if mode == "empty":
            if not tomb:
                return False
            cache.set(key(owner), f"{co}:{cv}|".encode() + payload, CART_TTL)
            return True
        if (oid, ver) < (co, cv):
            return False
        if (oid, ver) == (co, cv) and (mode == "tomb" or not tomb):
            return False
    if mode == "empty":
        oid, ver = 0, 0
    val = f"{oid}:{ver}|".encode() + (b"" if mode == "tomb" else payload)
    cache.set(key(owner), val, CART_TTL)
    return True


def put(owner, oid, ver, payload):
    return _write(owner, "data", oid, ver, payload)


def tombstone(owner, oid, ver):
    return _write(owner, "tomb", oid, ver)


def put_empty(owner, payload):
    return _write(owner, "empty", 0, 0, payload)
