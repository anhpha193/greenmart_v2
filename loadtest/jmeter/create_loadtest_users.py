#!/usr/bin/env python3
"""Tạo N tài khoản thử tải qua trang /register/ (như người dùng thật) và ghi users.csv (username,password) cho JMeter.
CHỈ chạy trên môi trường test/staging. Chạy lại được: user đã tồn tại thì bỏ qua và vẫn ghi vào CSV (giả định cùng mật khẩu).
  python3 create_loadtest_users.py --base http://<host> --count 200 --out users.csv
"""
import argparse, csv, re, sys
import requests

ap = argparse.ArgumentParser()
ap.add_argument("--base", required=True)
ap.add_argument("--count", type=int, default=100)
ap.add_argument("--prefix", default="lt_user_")
ap.add_argument("--password", default="LoadTest2024x")  # chỉ ký tự ASCII, không khoảng trắng (quy tắc đăng ký)
ap.add_argument("--out", default="users.csv")
a = ap.parse_args()
base = a.base.rstrip("/")


def token(html):
    m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
    if not m: sys.exit("Không tìm thấy CSRF token – kiểm tra --base")
    return m.group(1)


created = skipped = 0
with open(a.out, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, lineterminator="\n"); w.writerow(["username", "password"])
    s = requests.Session()
    for i in range(1, a.count + 1):
        u = f"{a.prefix}{i:04d}"
        page = s.get(base + "/register/", timeout=30)
        r = s.post(base + "/register/", data={"fnm": u, "email": f"{u}@loadtest.local", "pwd": a.password, "pwd1": a.password,
                                              "csrfmiddlewaretoken": token(page.text), "next": ""},
                   headers={"Referer": base + "/register/"}, timeout=30)
        if r.url.rstrip("/").endswith("/loginn"): created += 1
        else: skipped += 1   # đã tồn tại (hoặc bị từ chối)
        w.writerow([u, a.password])
        if i % 25 == 0: print(f"  {i}/{a.count}")
print(f"Xong: tạo mới {created}, bỏ qua {skipped} -> {a.out}")
