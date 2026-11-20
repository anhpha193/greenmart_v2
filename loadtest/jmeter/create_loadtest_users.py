#!/usr/bin/env python3
"""Tạo N tài khoản thử tải qua trang /register/ (như người dùng thật) và ghi users.csv (username,password) cho JMeter.
CHỈ chạy trên môi trường test/staging. Chạy lại được: user đã tồn tại thì bỏ qua và vẫn ghi vào CSV (giả định cùng mật khẩu).
Chạy song song nhiều luồng (--workers) để tạo nhanh; cluster tạo lại thì phải tạo lại user (DB mới).
  python create_loadtest_users.py --base http://<dns-ALB> --count 300 --workers 10
"""
import argparse, csv, re, sys, threading
from concurrent.futures import ThreadPoolExecutor

import requests

ap = argparse.ArgumentParser()
ap.add_argument("--base", required=True)
ap.add_argument("--count", type=int, default=300)
ap.add_argument("--prefix", default="lt_user_")
ap.add_argument("--password", default="LoadTest2024x")  # chỉ ký tự ASCII, không khoảng trắng (quy tắc đăng ký)
ap.add_argument("--out", default="users.csv")
ap.add_argument("--workers", type=int, default=10, help="số luồng đăng ký cùng lúc (đừng để quá cao: chính nó cũng là tải)")
a = ap.parse_args()
base = a.base.rstrip("/")
local = threading.local()


def session():
    if not hasattr(local, "s"):
        local.s = requests.Session()
    return local.s


def token(html):
    m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
    return m.group(1) if m else None


def register(i):
    u = f"{a.prefix}{i:04d}"
    s = session()
    s.cookies.clear()  # mỗi lần đăng ký là một khách mới
    for attempt in range(3):
        try:
            page = s.get(base + "/register/", timeout=30)
            t = token(page.text)
            if not t:
                return u, "lỗi: không thấy CSRF token (sai --base?)"
            r = s.post(base + "/register/", data={"fnm": u, "email": f"{u}@loadtest.local", "pwd": a.password,
                                                  "pwd1": a.password, "csrfmiddlewaretoken": t, "next": ""},
                       headers={"Referer": base + "/register/"}, timeout=30)
            return u, "tạo mới" if r.url.rstrip("/").endswith("/loginn") else "đã có"
        except requests.RequestException as exc:
            err = str(exc)
    return u, f"lỗi: {err[:80]}"


try:
    requests.get(base + "/gateway-health", timeout=15).raise_for_status()
except requests.RequestException as exc:
    sys.exit(f"Không vào được {base}/gateway-health: {exc}")

results = {}
with ThreadPoolExecutor(max_workers=a.workers) as pool:
    for n, (u, status) in enumerate(pool.map(register, range(1, a.count + 1)), 1):
        results[u] = status
        if n % 50 == 0:
            print(f"  {n}/{a.count}")

errors = {u: s for u, s in results.items() if s.startswith("lỗi")}
with open(a.out, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, lineterminator="\n")
    w.writerow(["username", "password"])
    for u in sorted(results):
        if u not in errors:
            w.writerow([u, a.password])

counts = {k: sum(1 for s in results.values() if s == k) for k in ("tạo mới", "đã có")}
print(f"Xong: tạo mới {counts['tạo mới']}, đã có {counts['đã có']}, lỗi {len(errors)} -> {a.out}")
for u, s in list(errors.items())[:5]:
    print(f"  {u}: {s}")
sys.exit(1 if errors else 0)
