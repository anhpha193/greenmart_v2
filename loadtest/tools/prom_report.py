#!/usr/bin/env python3
"""Báo cáo từ Prometheus trong cluster cho một khoảng thời gian (dữ liệu giữ 7 ngày: KHÔNG cần chạy lại bài test).
  1) kubectl -n monitoring port-forward svc/kps-kube-prometheus-stack-prometheus 9090:9090   (cửa sổ khác)
  2) python tools/prom_report.py 2026-11-23T02:55:00Z 2026-11-23T02:59:30Z
In ra: CPU trung bình/cao nhất theo deployment và từng node, CPU bị throttle, request/s và p95 theo service,
top view Django theo request/s, tỉ lệ /api/me/ trong request tới web.
"""
import json, sys, urllib.parse, urllib.request
from datetime import datetime

PROM = "http://localhost:" + __import__("os").environ.get("PROM_PORT","9090") + "/api/v1/query"
if len(sys.argv) != 3:
    sys.exit(__doc__)
t0, t1 = (datetime.fromisoformat(a.replace("Z", "+00:00")).timestamp() for a in sys.argv[1:])
W = f"{int(t1 - t0)}s"
DEPLOY = 'label_replace({q}, "app", "$1", "pod", "(.+)-[a-z0-9]+-[a-z0-9]{{5}}")'
EXCL = 'view!~"greenmart_common.health.health|django_prometheus.exports.ExportToDjangoView"'


def q(expr):
    url = PROM + "?" + urllib.parse.urlencode({"query": expr, "time": t1})
    data = json.load(urllib.request.urlopen(url, timeout=30))
    return [(r["metric"], float(r["value"][1])) for r in data["data"]["result"] if r["value"][1] not in ("NaN", "+Inf")]


def table(title, rows, key, fmt):
    print(f"\n{title}")
    for m, v in sorted(rows, key=lambda x: -x[1]):
        print(f"  {m.get(key, '?'):<40} {fmt(v)}")


cpu = 'sum by (app) (' + DEPLOY.format(q='rate(container_cpu_usage_seconds_total{namespace="greenmart",container!="",container!="POD"}[1m])') + ')'
table(f"CPU theo deployment, trung bình {W} (core)", q(f"avg_over_time(({cpu})[{W}:15s])"), "app", lambda v: f"{v:.2f}")
table(f"CPU theo deployment, cao nhất {W} (core)", q(f"max_over_time(({cpu})[{W}:15s])"), "app", lambda v: f"{v:.2f}")
sysns = 'sum by (namespace) (rate(container_cpu_usage_seconds_total{namespace!="greenmart",container!=""}[1m]))'
table(f"CPU các namespace hệ thống, trung bình (core)", q(f"avg_over_time(({sysns})[{W}:15s])"), "namespace", lambda v: f"{v:.2f}")
node = '(1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[1m]))) * 100'
table(f"CPU từng node, cao nhất (%)", q(f"max_over_time(({node})[{W}:15s])"), "instance", lambda v: f"{v:.0f}%")
thr = 'sum by (app) (' + DEPLOY.format(q='rate(container_cpu_cfs_throttled_periods_total{namespace="greenmart"}[1m])') + ') / sum by (app) (' + DEPLOY.format(q='rate(container_cpu_cfs_periods_total{namespace="greenmart"}[1m])') + ')'
table("Tỉ lệ chu kỳ CPU bị throttle (trung bình)", q(f"avg_over_time(({thr})[{W}:15s])"), "app", lambda v: f"{v*100:.1f}%")
rps = f'sum by (service) (rate(django_http_responses_total_by_status_view_method_total{{{EXCL}}}[1m]))'
table(f"Request/s tới Django theo service, trung bình", q(f"avg_over_time(({rps})[{W}:15s])"), "service", lambda v: f"{v:.1f}")
p95 = f'histogram_quantile(0.95, sum by (le, service) (rate(django_http_requests_latency_seconds_by_view_method_bucket{{{EXCL}}}[2m])))'
table("p95 xử lý trong Django theo service (cuối khoảng)", q(p95), "service", lambda v: f"{v*1000:.0f} ms")
views = f'topk(10, sum by (service, view) (rate(django_http_responses_total_by_status_view_method_total{{{EXCL}}}[{W}])))'
print(f"\nTop view Django theo request/s (trung bình {W})")
rows = q(views)
for m, v in sorted(rows, key=lambda x: -x[1]):
    print(f"  {m.get('service','?'):<18} {m.get('view','?'):<45} {v:.1f}/s")
web = sum(v for m, v in q(f'sum by (view) (rate(django_http_responses_total_by_status_view_method_total{{service="web",{EXCL}}}[{W}]))'))
me = sum(v for m, v in q(f'sum(rate(django_http_responses_total_by_status_view_method_total{{service="web",view=~"me|app.views.me"}}[{W}]))'))
if web:
    print(f"\n/api/me/ chiếm {100*me/web:.1f}% request tới web ({me:.1f}/{web:.1f} req/s)")
