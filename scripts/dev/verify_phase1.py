"""Phase 1 verification: is the telemetry real, and do real faults move it?

Checks, against a running docker compose stack:
  1. Prometheus has RED metrics (rate, error %, p50/p95/p99) for every reference service,
     derived from OTel traces by the collector's spanmetrics connector.
  2. The collector's servicegraph connector discovered the real call graph,
     including inventory-service -> inventory-db.
  3. HikariCP pool metrics are scraped from inventory-service.
  4. (--faults) Each fault type, injected through the CausalOps API, changes the
     metric it should, measured before vs during the fault.

Standard library only. Usage:
  python scripts/dev/verify_phase1.py                  # checks 1-3
  python scripts/dev/verify_phase1.py --faults         # also runs every fault (~6 min)
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SERVICES = ["api-gateway", "order-service", "inventory-service", "payment-service"]
EXPECTED_EDGES = {
    ("api-gateway", "order-service"),
    ("order-service", "inventory-service"),
    ("order-service", "payment-service"),
    ("inventory-service", "inventory-db"),
}

SERVER = 'span_kind="SPAN_KIND_SERVER"'


def q_rate(svc):
    return f'sum(rate(traces_span_metrics_calls_total{{{SERVER},service_name="{svc}"}}[1m]))'


def q_err_pct(svc):
    return (f'100 * sum(rate(traces_span_metrics_calls_total{{{SERVER},service_name="{svc}",status_code="STATUS_CODE_ERROR"}}[1m]))'
            f' / sum(rate(traces_span_metrics_calls_total{{{SERVER},service_name="{svc}"}}[1m]))')


def q_quantile(svc, q, kind="SPAN_KIND_SERVER", extra=""):
    return (f'histogram_quantile({q}, sum by (le) (rate(traces_span_metrics_duration_milliseconds_bucket'
            f'{{span_kind="{kind}",service_name="{svc}"{extra}}}[1m])))')


Q_DB_P95 = q_quantile("inventory-service", 0.95, "SPAN_KIND_CLIENT", ',db_system="postgresql"')
Q_POOL_PENDING = 'max(hikaricp_connections_pending{application="inventory-service"})'
Q_EDGES = 'sum by (client, server) (rate(traces_service_graph_request_total[2m]))'

# fault -> (request, metric that must rise, minimum factor or absolute rise, description)
FAULTS = [
    ({"type": "DB_LATENCY", "target": "inventory-db", "parameters": {"latencyMs": 400}},
     Q_DB_P95, "inventory-service DB client p95 (ms)"),
    ({"type": "SERVICE_LATENCY", "target": "order-service", "parameters": {"latencyMs": 600}},
     q_quantile("order-service", 0.95), "order-service p95 (ms)"),
    ({"type": "ERROR_RATE", "target": "payment-service", "parameters": {"errorRatePct": 40}},
     q_err_pct("payment-service"), "payment-service error %"),
    ({"type": "SERVICE_FAILURE", "target": "inventory-service", "parameters": {}},
     q_err_pct("order-service"), "order-service error % (propagated)"),
    ({"type": "NETWORK_LATENCY", "target": "payment-service", "parameters": {"latencyMs": 500}},
     q_quantile("order-service", 0.95), "order-service p95 (ms), slow link to payment"),
    ({"type": "CONNECTION_POOL_SATURATION", "target": "inventory-service", "parameters": {}},
     Q_POOL_PENDING, "inventory-service Hikari pending connections"),
]


def get_json(url, data=None, method=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 method=method or ("POST" if data is not None else "GET"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        body = r.read().decode()
        return json.loads(body) if body else None


def prom(base, expr):
    url = f"{base}/api/v1/query?{urllib.parse.urlencode({'query': expr})}"
    res = get_json(url)["data"]["result"]
    return res


def scalar(base, expr):
    res = prom(base, expr)
    if not res:
        return None
    v = float(res[0]["value"][1])
    return None if v != v else v  # NaN -> None


def check(ok, label, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
    return ok


def static_checks(prom_base):
    ok = True
    print("1. RED metrics per service (from OTel traces)")
    for svc in SERVICES:
        rate = scalar(prom_base, q_rate(svc))
        p50, p95, p99 = (scalar(prom_base, q_quantile(svc, q)) for q in (0.5, 0.95, 0.99))
        err = scalar(prom_base, q_err_pct(svc))
        if err is None and rate:
            err = 0.0  # no error series yet means zero errors
        fmt = lambda v: "n/a" if v is None else f"{v:.1f}"
        ok &= check(rate is not None and rate > 0 and p99 is not None, svc,
                    f"rate={fmt(rate)} req/s p50={fmt(p50)} p95={fmt(p95)} p99={fmt(p99)} ms err={fmt(err)}%")
    print("2. Service graph discovered from traces")
    found = {(r["metric"].get("client"), r["metric"].get("server")) for r in prom(prom_base, Q_EDGES)}
    for edge in sorted(EXPECTED_EDGES):
        ok &= check(edge in found, f"{edge[0]} -> {edge[1]}")
    extra = found - EXPECTED_EDGES
    if extra:
        print(f"  (also seen: {sorted(extra)})")
    print("3. Connection pool metrics")
    pool_max = scalar(prom_base, 'max(hikaricp_connections_max{application="inventory-service"})')
    ok &= check(pool_max is not None, "hikaricp_connections_max{inventory-service}", f"max={pool_max}")
    return ok


def fault_checks(prom_base, api_base, window):
    ok = True
    print(f"4. Real faults through the CausalOps API ({window}s baseline vs {window}s under fault)")
    for request, expr, label in FAULTS:
        # A 30s rate window that is shorter than the settle time keeps each baseline free of the previous fault.
        expr = expr.replace("[1m]", "[30s]")
        body = dict(request, severity="HIGH", durationSeconds=window + 30)
        time.sleep(window)
        before = scalar(prom_base, expr) or 0.0
        fault = get_json(f"{api_base}/faults", body)
        try:
            time.sleep(window)
            during = scalar(prom_base, expr) or 0.0
            if "STATUS_CODE_ERROR" in expr and during == 0.0:
                time.sleep(15)  # first error series may appear one scrape late
                during = scalar(prom_base, expr) or 0.0
        finally:
            get_json(f"{api_base}/faults/{fault['id']}/stop", {}, method="POST")
        rose = during > max(before * 1.5, before + 1.0)
        ok &= check(rose, f"{request['type']} on {request['target']}",
                    f"{label}: {before:.1f} -> {during:.1f}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prometheus", default="http://localhost:9090")
    ap.add_argument("--api", default="http://localhost:8080/api")
    ap.add_argument("--faults", action="store_true", help="also inject every fault type and measure its effect")
    ap.add_argument("--window", type=int, default=45, help="seconds per baseline/fault window")
    args = ap.parse_args()
    try:
        ok = static_checks(args.prometheus)
        if args.faults:
            ok &= fault_checks(args.prometheus, args.api, args.window)
    except urllib.error.URLError as e:
        print(f"Cannot reach the stack: {e}")
        return 2
    print("\nRESULT:", "ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
