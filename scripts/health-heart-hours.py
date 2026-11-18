#!/usr/bin/env python3
"""How many hours the health heart (cc:overall_status) spent red and amber, and what held it red.

Run it to measure the heart over a window, before and after a change to the command-center rules
(apps/monitoring/prometheus-rules-command-center.yaml), and quote its output in the PR. It prints
red (2) and amber (1) hours, the hours with samples, then the top items of cc:red_now, the domains
that were red (cc:domain_status == 2, printer left out as the heart leaves it out) and the
fleet=fix alerts that kept the alertmanager-fleet watch tripped, each in hours.

  scripts/health-heart-hours.py            # the last 48h
  scripts/health-heart-hours.py 7d 15      # a window, and how many rows per list

Stdlib only. PROMETHEUS_URL overrides https://prometheus.kblab.me.

Why: the heart was measured by hand-written PromQL for hc-322 (red 41.1h, amber 6.9h before
#1028) and again for hc-436, and each session rewrote the queries.
"""

import json
import os
import sys
import urllib.parse
import urllib.request

PROM = os.environ.get("PROMETHEUS_URL", "https://prometheus.kblab.me").rstrip("/")
STEP = "1m"  # the subquery resolution; a sample is one minute, so the sum / 60 is hours


def query(expr):
    url = f"{PROM}/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    with urllib.request.urlopen(url, timeout=120) as resp:
        body = json.load(resp)
    if body.get("status") != "success":
        sys.exit(f"FAIL: Prometheus said {body.get('error')} for {expr}")
    return body["data"]["result"]


def hours(expr, window):
    res = query(f"sum_over_time(({expr})[{window}:{STEP}]) / 60")
    return float(res[0]["value"][1]) if res else 0.0


def ranked(by, expr, window, top):
    res = query(f"sort_desc(sum by ({by}) (sum_over_time(({expr})[{window}:{STEP}])) / 60 > 0)")
    return [(r["metric"].get(by, "?"), float(r["value"][1])) for r in res[:top]]


def main(argv):
    window = argv[1] if len(argv) > 1 else "48h"
    top = int(argv[2]) if len(argv) > 2 else 10
    covered = hours("cc:overall_status * 0 + 1", window)
    if not covered:
        sys.exit(f"FAIL: no cc:overall_status samples in {window}; the rule is not evaluating")
    red = hours("cc:overall_status == bool 2", window)
    amber = hours("cc:overall_status == bool 1", window)
    print(f"window {window}: red {red:.1f}h  amber {amber:.1f}h  sampled {covered:.1f}h")
    lists = (
        ("red items (cc:red_now)", "item", "cc:red_now"),
        ("red domains", "domain", 'cc:domain_status{domain!="printer"} == bool 2'),
        ("fleet=fix alerts firing", "alertname",
         'max by (alertname) (ALERTS{alertstate="firing",fleet="fix"}) * 0 + 1'),
    )
    for title, by, expr in lists:
        print(f"\n{title}, hours:")
        for name, h in ranked(by, expr, window, top):
            print(f"  {h:5.1f}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
