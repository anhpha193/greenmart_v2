#!/usr/bin/env python3
"""Chạy thử kế hoạch .jmx bằng Python: đọc CHÍNH file .jmx (sampler, tham số, regex extractor, assertion, timer, IfController)
và thực thi với app thật để xác nhận đường dẫn/regex/assertion đúng TRƯỚC khi nạp tải bằng JMeter.
Đây là mô phỏng gần đúng ngữ nghĩa JMeter, KHÔNG thay thế JMeter (không đo được năng lực chịu tải thật).

  python3 tools/validate_jmx.py --base http://127.0.0.1:8100 --users users.csv                # 1 vòng mỗi nhóm
  python3 tools/validate_jmx.py --base ... --users users.csv --guest-threads 10 --user-threads 4 --duration 30   # tải nhẹ
"""
import argparse, csv, random, re, statistics, threading, time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

import requests

ap = argparse.ArgumentParser()
ap.add_argument("--jmx", default=str(Path(__file__).resolve().parents[1] / "greenmart-loadtest.jmx"))
ap.add_argument("--base", required=True)
ap.add_argument("--users", default="users.csv")
ap.add_argument("--guest-threads", type=int, default=1)
ap.add_argument("--user-threads", type=int, default=1)
ap.add_argument("--iterations", type=int, default=1)
ap.add_argument("--duration", type=float, default=0, help="giây; >0 thì chạy theo thời gian thay vì số vòng")
ap.add_argument("--prop", action="append", default=[], help="k=v, như -Jk=v của JMeter")
a = ap.parse_args()

u = urlparse(a.base)
PROPS = {"host": u.hostname, "port": str(u.port or (443 if u.scheme == "https" else 80)), "protocol": u.scheme, "base_url": a.base.rstrip("/"),
         "think_ms": "0", "think_range_ms": "0"}
PROPS.update(dict(p.split("=", 1) for p in a.prop))
ORIGIN = f"{PROPS['protocol']}://{PROPS['host']}:{PROPS['port']}"


# ---------- đọc cây JMX ----------
def pairs(ht):
    k = list(ht)
    return [(k[i], k[i + 1]) for i in range(0, len(k), 2)]

def sprop(e, name):
    for c in e:
        if c.get("name") == name and c.tag in ("stringProp", "boolProp", "intProp"):
            return c.text or ""
    return ""

root = ET.parse(a.jmx).getroot()
plan_el, plan_ht = pairs(root[0])[0]
UDV = {}
for arg in plan_el.iter("elementProp"):
    if arg.get("elementType") == "Argument":
        UDV[sprop(arg, "Argument.name")] = sprop(arg, "Argument.value")


# ---------- hàm/biến JMeter ----------
PAT = re.compile(r"\$\{([^${}]*)\}")
def subst(s, vars_):
    while True:
        m = PAT.search(s)
        if not m: return s
        e = m.group(1)
        if e.startswith("__P("):
            n, _, d = e[4:-1].partition(","); val = PROPS.get(n, d)
        elif e.startswith("__chooseRandom("):
            parts = e[len("__chooseRandom("):-1].split(","); val = random.choice(parts[:-1]); vars_[parts[-1]] = val
        else:
            val = vars_.get(e, f"‹{e}›")
        s = s[:m.start()] + val + s[m.end():]


class Stats:
    def __init__(self): self.lock = threading.Lock(); self.rows = {}; self.fail = {}
    def add(self, name, ok, ms, msgs):
        with self.lock:
            self.rows.setdefault(name, []).append((ok, ms))
            if not ok: self.fail.setdefault(name, []).append("; ".join(msgs))
STATS = Stats()

csv_lock = threading.Lock(); csv_rows = []; csv_idx = [0]
def next_user():
    global csv_rows
    with csv_lock:
        if not csv_rows:
            path = Path(a.users)
            csv_rows = list(csv.DictReader(path.open(encoding="utf-8")))
        r = csv_rows[csv_idx[0] % len(csv_rows)]; csv_idx[0] += 1
        return r


def run_sampler(el, ht, sess, vars_, headers, cfg):
    name = el.get("testname")
    path = subst(sprop(el, "HTTPSampler.path"), vars_); method = sprop(el, "HTTPSampler.method")
    params = {}
    for arg in el.iter("elementProp"):
        if arg.get("elementType") == "HTTPArgument":
            params[sprop(arg, "Argument.name")] = subst(sprop(arg, "Argument.value"), vars_)
    url = f"{ORIGIN}{path}"
    hdr = {k: subst(v, vars_) for k, v in headers.items()}
    t0 = time.perf_counter()
    try:
        r = sess.request(method, url, params=params if method == "GET" else None, data=params if method == "POST" else None,
                         headers=hdr, allow_redirects=True, timeout=30)
        body, code, err = r.text, r.status_code, None
    except Exception as ex:  # noqa: BLE001
        body, code, err = "", 0, str(ex)
    ms = (time.perf_counter() - t0) * 1000
    msgs = [f"lỗi kết nối: {err}"] if err else []
    for child, _ in pairs(ht):
        if child.tag == "RegexExtractor":
            found = re.findall(sprop(child, "RegexExtractor.regex"), body)
            n = int(sprop(child, "RegexExtractor.match_number") or 1)
            val = (random.choice(found) if n == 0 else found[n - 1]) if found and (n == 0 or len(found) >= n) else sprop(child, "RegexExtractor.default")
            vars_[sprop(child, "RegexExtractor.refname")] = val
    for child, _ in pairs(ht):
        if child.tag == "ResponseAssertion":
            field = sprop(child, "Assertion.test_field"); ttype = int(sprop(child, "Assertion.test_type"))
            target = str(code) if field == "Assertion.response_code" else body
            for ts in child.iter("stringProp"):
                if ts.get("name", "").isdigit():
                    s = subst(ts.text or "", vars_)
                    ok = (target == s) if ttype & 8 else (s in target)
                    if not ok: msgs.append(f"assert '{child.get('testname')}' sai (cần '{s}', code={code})")
    for dur in cfg["durations"]:
        if ms > dur: msgs.append(f"chậm {ms:.0f}ms > {dur:.0f}ms")
    STATS.add(name, not msgs, ms, msgs)
    return not msgs


def walk(children, sess, vars_, headers, cfg):
    for el, ht in children:
        if el.tag == "HTTPSamplerProxy":
            run_sampler(el, ht, sess, vars_, headers, cfg)
            if cfg["think"]: time.sleep(random.uniform(0, cfg["think"]))
        elif el.tag == "IfController":
            if subst(sprop(el, "IfController.condition"), vars_).strip() == "true":
                walk(pairs(ht), sess, vars_, headers, cfg)


def tg_worker(tg, ht, deadline, iters):
    children = pairs(ht)
    cfg = {"durations": [float(subst(sprop(c, "DurationAssertion.duration"), {})) for c, _ in children if c.tag == "DurationAssertion"],
           "think": float(PROPS["think_ms"]) / 1000 * 2}
    vars_ = dict(UDV); vars_["base_url"] = subst(UDV["base_url"], {})
    uses_csv = any(c.tag == "CSVDataSet" for c, _ in children)
    done = 0
    while (time.time() < deadline) if deadline else (done < iters):
        sess = requests.Session()   # CookieManager.clearEachIteration = true
        if uses_csv: vars_.update(next_user())
        walk([(c, h) for c, h in children if c.tag in ("HTTPSamplerProxy", "IfController")], sess, vars_, HEADERS, cfg)
        done += 1


HEADERS = {}
for el, ht in pairs(plan_ht):
    if el.tag == "HeaderManager":
        for h in el.iter("elementProp"):
            if h.get("elementType") == "Header": HEADERS[sprop(h, "Header.name")] = sprop(h, "Header.value")
    if el.tag == "ConfigTestElement": pass

threads, deadline = [], (time.time() + a.duration) if a.duration > 0 else 0
for el, ht in pairs(plan_ht):
    if el.tag != "ThreadGroup": continue
    n = a.guest_threads if "guest" in sprop(el, "ThreadGroup.num_threads") else a.user_threads
    for _ in range(n):
        threads.append(threading.Thread(target=tg_worker, args=(el, ht, deadline, a.iterations)))
t0 = time.time()
for t in threads: t.start()
for t in threads: t.join()
wall = time.time() - t0

print(f"\n{'Sampler':36}{'#':>6}{'lỗi':>6}{'avg ms':>9}{'p95 ms':>9}{'max ms':>9}")
tot = err = 0
for name in sorted(STATS.rows):
    rows = STATS.rows[name]; ms = sorted(m for _, m in rows); e = sum(1 for ok, _ in rows if not ok)
    p95 = ms[min(len(ms) - 1, int(len(ms) * 0.95))]
    print(f"{name:36}{len(rows):>6}{e:>6}{statistics.mean(ms):>9.0f}{p95:>9.0f}{ms[-1]:>9.0f}"); tot += len(rows); err += e
print(f"{'TỔNG':36}{tot:>6}{err:>6}   thời gian {wall:.0f}s, ~{tot / wall:.1f} req/s (chỉ tham khảo; sandbox chia sẻ CPU với app)")
for name, msgs in STATS.fail.items():
    print(f"  ✗ {name}: {len(msgs)} lỗi, ví dụ: {msgs[0][:140]}")
raise SystemExit(1 if err else 0)
