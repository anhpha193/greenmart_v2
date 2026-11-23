#!/usr/bin/env python3
"""Đọc metrics.csv.gz của k6 (--out csv) và in theo từng 30 giây: số VU, req/s, p95, % lỗi, lượt/giây.
Request chuyển hướng (POST thêm giỏ/đăng nhập -> 302 -> GET) k6 tính là 2 request; cột "lượt/s" là số lượt kịch bản.
  python timeline.py results/<thư-mục>/metrics.csv.gz
"""
import csv, gzip, sys
from collections import defaultdict


def pct(v, p):
    if not v:
        return 0
    v = sorted(v)
    return v[min(len(v) - 1, int(p / 100 * len(v)))]


rows = csv.DictReader(gzip.open(sys.argv[1], "rt", encoding="utf-8"))
dur, fail, vus, iters = defaultdict(list), defaultdict(int), defaultdict(int), defaultdict(int)
t0 = None
for r in rows:
    t = int(r["timestamp"])
    t0 = t if t0 is None else min(t0, t)
    w = t // 30
    m = r["metric_name"]
    if m == "http_req_duration":
        dur[w].append(float(r["metric_value"]))
    elif m == "http_req_failed" and float(r["metric_value"]) == 1:  # k6 ghi "1.000000"
        fail[w] += 1
    elif m == "vus":
        vus[w] = max(vus[w], int(float(r["metric_value"])))
    elif m == "iterations":
        iters[w] += 1
print("\nTheo thời gian (30 giây): giây | VU | req/s | p95 ms | lỗi | lượt/s")
base = t0 // 30
for w in sorted(dur):
    d = dur[w]
    print(f"  {(w-base)*30:>4}-{(w-base)*30+30:<4} | {vus.get(w, 0):>4} | {len(d)/30:>6.1f} | {pct(d, 95):>7.0f} | "
          f"{100*fail[w]/len(d):>5.1f}% | {iters[w]/30:>5.1f}")
