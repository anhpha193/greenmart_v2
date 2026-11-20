#!/usr/bin/env python3
"""Tóm tắt kết quả JMeter (results.jtl dạng CSV) thành bảng ngắn: tổng req/s, p95, % lỗi theo từng bước,
req/s cao nhất trong 10 giây, mã lỗi hay gặp. run-loadtest.sh tự gọi sau mỗi lần chạy.
  python tools/summarize.py results/<thư-mục>/results.jtl
"""
import csv, re, sys
from collections import Counter, defaultdict


def pct(values, p):
    if not values:
        return 0
    values = sorted(values)
    k = max(0, min(len(values) - 1, int(round(p / 100 * len(values) + 0.5)) - 1))
    return values[k]


def main(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8", errors="replace")))
    if not rows:
        sys.exit("results.jtl trống")
    # Request có chuyển hướng (POST -> 302 -> GET) được JMeter ghi thêm dòng con "<tên>-0", "<tên>-1".
    # Bỏ các dòng con để không đếm một request hai ba lần.
    labels = {r["label"] for r in rows}
    rows = [r for r in rows if not (re.search(r"-\d+$", r["label"]) and re.sub(r"-\d+$", "", r["label"]) in labels)]
    ts = [int(r["timeStamp"]) for r in rows]
    start, end = min(ts), max(int(r["timeStamp"]) + int(r["elapsed"]) for r in rows)
    secs = max(1, (end - start) / 1000)

    by_label = defaultdict(list)
    for r in rows:
        by_label[r["label"]].append(r)

    def line(name, rs):
        el = [int(r["elapsed"]) for r in rs]
        err = sum(1 for r in rs if r["success"] != "true")
        return (f"{name[:34]:<34} {len(rs):>7} {len(rs)/secs:>7.1f} {sum(el)/len(el):>7.0f} "
                f"{pct(el, 95):>7} {pct(el, 99):>7} {100*err/len(rs):>6.1f}%")

    head = f"{'Bước':<34} {'Số req':>7} {'req/s':>7} {'TB ms':>7} {'p95 ms':>7} {'p99 ms':>7} {'Lỗi':>7}"
    print(f"\nThời gian chạy: {secs:.0f}s  |  luồng tối đa: {max(int(r.get('allThreads') or 0) for r in rows)}")
    print(head); print("-" * len(head))
    for label in sorted(by_label):
        print(line(label, by_label[label]))
    print("-" * len(head)); print(line("TỔNG", rows))

    buckets = Counter((int(r["timeStamp"]) - start) // 10000 for r in rows)
    peak_bucket, peak = max(buckets.items(), key=lambda kv: kv[1])
    print(f"\nreq/s cao nhất (trung bình 10 giây): {peak/10:.1f} ở giây thứ {peak_bucket*10}-{peak_bucket*10+10}")

    # Từng khoảng 30 giây: thấy được lúc nào bắt đầu chậm/lỗi khi tăng tải
    print("\nTheo thời gian (30 giây): giây | luồng | req/s | p95 ms | lỗi")
    win = defaultdict(list)
    for r in rows:
        win[(int(r["timeStamp"]) - start) // 30000].append(r)
    for w in sorted(win):
        rs = win[w]
        el = [int(r["elapsed"]) for r in rs]
        err = sum(1 for r in rs if r["success"] != "true")
        th = max(int(r.get("allThreads") or 0) for r in rs)
        print(f"  {w*30:>4}-{w*30+30:<4} | {th:>5} | {len(rs)/30:>6.1f} | {pct(el, 95):>6} | {100*err/len(rs):>5.1f}%")

    fails = Counter(f'{r["responseCode"]} {(r.get("failureMessage") or r.get("responseMessage") or "")[:70]}'
                    for r in rows if r["success"] != "true")
    if fails:
        print("\nLỗi hay gặp:")
        for msg, n in fails.most_common(6):
            print(f"  {n:>6} × {msg}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
