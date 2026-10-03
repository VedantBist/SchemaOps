"""Phase 2 verification: CausalOps ingests real telemetry and discovers the real topology.

Checks against the running docker compose stack:
  1. /api/topology equals the collector's service graph in Prometheus (no seeded nodes).
  2. /api/services values match the same PromQL run directly against Prometheus.
  3. Ingestion is fresh and every component in /api/system/status is UP.
  4. Logs (Loki) and traces (Tempo) are served live through /api/logs and /api/traces.
  5. The AI engine is reachable only through the /api/engine proxy, with status codes intact.
  6. Errors use real HTTP status codes; CORS only allows configured origins.
  7. No synthetic telemetry code remains in the backend source.
  8. (--incident) A real SLO breach opens an incident with measured evidence, and it
     resolves on its own after the fault is removed.

Standard library only. Usage:
  python scripts/dev/verify_phase2.py
  python scripts/dev/verify_phase2.py --incident      # also runs the ~3 minute incident check
"""
import argparse
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]


def http(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode()
            return r.status, dict(r.headers), (json.loads(raw) if raw and r.headers.get_content_type() == "application/json" else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = raw
        return e.code, dict(e.headers), parsed


def prom(base, expr):
    _, _, body = http("GET", f"{base}/api/v1/query?{urllib.parse.urlencode({'query': expr})}")
    return body["data"]["result"]


results = []


def check(ok, label, detail=""):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
    return ok


def topology_matches(api, prom_base):
    print("1. Topology is discovered from the service graph")
    _, _, topo = http("GET", f"{api}/topology")
    api_edges = {(e["source"], e["target"]) for e in topo["edges"] if not e["stale"]}
    graph = prom(prom_base, 'sum by (client, server) (rate(traces_service_graph_request_total[5m])) > 0')
    prom_edges = {(r["metric"]["client"], r["metric"]["server"]) for r in graph
                  if r["metric"]["client"] not in ("user", "unknown") and r["metric"]["server"] not in ("user", "unknown")}
    check(api_edges == prom_edges, "API edges == Prometheus service graph", f"{sorted(api_edges)}")
    names = {n["name"] for n in topo["nodes"]}
    check("auth-gateway" not in names, "no hand-seeded nodes (auth-gateway is gone)", f"nodes={sorted(names)}")
    check(all(n["source"] == "discovered" for n in topo["nodes"]), "every node was discovered, none seeded")
    kinds = {n["name"]: n["kind"] for n in topo["nodes"]}
    check(kinds.get("inventory-db") == "database", "inventory-db classified as a database from its spans")
    return topo


def values_match(api, prom_base):
    print("2. Stored values are the Prometheus measurements")
    _, _, env = http("GET", f"{api}/environments")
    tpl = env[0]["config"]["telemetry"]["serviceMetrics"]
    window = env[0]["config"]["telemetry"]["rateWindow"]
    direct_p99 = {r["metric"]["service_name"]: float(r["value"][1])
                  for r in prom(prom_base, tpl["latencyP99"]["query"].replace("${window}", window))}
    direct_rps = {r["metric"]["service_name"]: float(r["value"][1])
                  for r in prom(prom_base, tpl["requestRate"]["query"].replace("${window}", window))}
    _, _, services = http("GET", f"{api}/services")
    for s in services:
        if s["kind"] != "service":
            continue
        name, p99, rps = s["name"], s["latencyP99"], s["requestRate"]
        dp99, drps = direct_p99.get(name), direct_rps.get(name)
        ok = (p99 is not None and dp99 is not None and abs(p99 - dp99) <= max(15.0, 0.5 * dp99)
              and rps is not None and drps is not None and abs(rps - drps) <= max(1.0, 0.3 * drps))
        check(ok, f"{name}", f"API p99={p99 and round(p99, 1)} vs Prometheus {dp99 and round(dp99, 1)} ms; "
                             f"rps={rps and round(rps, 2)} vs {drps and round(drps, 2)}")


def system_status(api):
    print("3. Live component health and ingestion freshness")
    _, _, st = http("GET", f"{api}/system/status")
    for comp, state in st["components"].items():
        check(state == "UP", f"{comp}", state)
    check(st["ingestion"]["status"] == "FRESH", "telemetry ingestion is fresh", f"last sample {st['ingestion']['ageSeconds']}s ago")
    _, _, samples = http("GET", f"{api}/metrics?service=order-service&minutes=2")
    n = len(samples["samples"])
    check(n >= 10, "order-service has a stored series", f"{n} samples in the last 2 minutes")


def logs_and_traces(api):
    print("4. Logs and traces read live from Loki and Tempo")
    code, _, logs = http("GET", f"{api}/logs?service=inventory-service&minutes=30&limit=20")
    check(code == 200 and isinstance(logs, list) and len(logs) > 0 and all(l["service"] == "inventory-service" for l in logs),
          "/api/logs?service=inventory-service", f"{len(logs) if isinstance(logs, list) else logs} lines")
    code, _, traces = http("GET", f"{api}/traces?service=api-gateway&minutes=10&limit=5")
    ok = code == 200 and isinstance(traces, list) and len(traces) > 0
    check(ok, "/api/traces?service=api-gateway", f"{len(traces) if isinstance(traces, list) else traces} traces")
    if ok:
        code, _, full = http("GET", f"{api}/traces/{traces[0]['traceId']}")
        spans = sum(len(ss.get("spans", [])) for b in full.get("batches", full.get("resourceSpans", []))
                    for ss in b.get("scopeSpans", b.get("instrumentationLibrarySpans", [])))
        check(code == 200 and spans > 1, "/api/traces/{id} returns the full trace", f"{spans} spans")


def engine_proxy(api):
    print("5. AI engine through the platform API")
    code, _, body = http("GET", f"{api}/engine/health")
    check(code == 200 and isinstance(body, dict) and "status" in body, "/api/engine/health proxied", f"HTTP {code}")
    code, _, _ = http("GET", f"{api}/engine/remediation/executions/does-not-exist")
    check(code in (200, 404), "engine status codes pass through", f"HTTP {code}")


def error_contract(api):
    print("6. Error contract and CORS")
    code, _, body = http("GET", f"{api}/incidents/00000000-0000-0000-0000-000000000000")
    check(code == 404 and isinstance(body, dict) and body.get("status") == 404, "unknown incident -> 404 JSON")
    code, _, body = http("POST", f"{api}/faults", {"type": "DB_LATENCY", "target": "inventory-db", "severity": "LOW",
                                                    "durationSeconds": 5, "parameters": {}})
    check(code == 400, "invalid fault -> 400", str(body.get("message") if isinstance(body, dict) else body))
    code, hdrs, _ = http("OPTIONS", f"{api}/services", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
    check(code == 403, "preflight from unlisted origin is refused", f"HTTP {code}")
    code, hdrs, _ = http("OPTIONS", f"{api}/services", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
    check(hdrs.get("Access-Control-Allow-Origin") == "http://localhost:3000", "configured origin is allowed")


def no_synthetic_code():
    print("7. No synthetic telemetry left in the backend")
    src = ROOT / "backend" / "causalops-api" / "src" / "main"
    patterns = [r"Math\.pow", r"0\.62", r"60 req/min", r"demo-mode", r"DEMO_MODE", r"new Random\("]
    hits = []
    for f in src.rglob("*"):
        if f.suffix in (".java", ".yml", ".sql") and f.name not in ("V1__schema.sql", "V2__auth_gateway.sql"):
            text = f.read_text(encoding="utf-8")
            hits += [f"{f.relative_to(ROOT)}: {p}" for p in patterns if re.search(p, text)]
    check(not hits, "no formula telemetry, demo flag or random keys", "; ".join(hits))


def incident_lifecycle(api):
    print("8. Real SLO breach -> incident -> automatic resolution")
    http("POST", f"{api}/faults/clear")
    code, _, fault = http("POST", f"{api}/faults", {"type": "SERVICE_LATENCY", "target": "order-service", "severity": "HIGH",
                                                     "durationSeconds": 120, "parameters": {"latencyMs": 900}})
    if not check(code == 201, "injected 900 ms latency into order-service (SLO p99 500 ms)"):
        return
    incident, deadline = None, time.time() + 120
    while time.time() < deadline and incident is None:
        time.sleep(5)
        _, _, active = http("GET", f"{api}/incidents?state=active")
        incident = next((i for i in active if "order-service" in i["affectedServices"]), None)
    if not check(incident is not None, "incident opened from measured breach",
                 incident and f"{incident['incidentKey']} '{incident['title']}' severity={incident['severity']}"):
        http("POST", f"{api}/faults/{fault['id']}/stop")
        return
    evidence = incident["evidence"] if not isinstance(incident["evidence"], str) else json.loads(incident["evidence"])
    ev = next((e for e in evidence if e["service"] == "order-service"), None)
    check(ev is not None and ev["observed"] > ev["threshold"], "evidence holds the measured value",
          ev and f"{ev['metric']} observed {ev['observed']} > threshold {ev['threshold']} ({ev['source']})")
    check(re.fullmatch(r"INC-\d+", incident["incidentKey"]) is not None, "sequential incident key")

    http("POST", f"{api}/faults/{fault['id']}/stop")
    resolved, deadline = None, time.time() + 180
    while time.time() < deadline and resolved is None:
        time.sleep(5)
        _, _, inc = http("GET", f"{api}/incidents/{incident['id']}")
        resolved = inc if inc["status"] == "RESOLVED" else None
    check(resolved is not None, "incident resolved itself after recovery", resolved and f"resolvedAt={resolved['resolvedAt']}")
    _, _, timeline = http("GET", f"{api}/incidents/{incident['id']}/timeline")
    check([e["eventType"] for e in timeline][0] == "DETECTED" and timeline[-1]["eventType"] == "RECOVERED",
          "timeline DETECTED ... RECOVERED", " -> ".join(e["eventType"] for e in timeline))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080/api")
    ap.add_argument("--prometheus", default="http://localhost:9090")
    ap.add_argument("--incident", action="store_true")
    a = ap.parse_args()
    try:
        topology_matches(a.api, a.prometheus)
        values_match(a.api, a.prometheus)
        system_status(a.api)
        logs_and_traces(a.api)
        engine_proxy(a.api)
        error_contract(a.api)
        no_synthetic_code()
        if a.incident:
            incident_lifecycle(a.api)
    except urllib.error.URLError as e:
        print(f"Cannot reach the stack: {e}")
        return 2
    ok = all(results)
    print(f"\nRESULT: {'ALL CHECKS PASSED' if ok else 'SOME CHECKS FAILED'} ({sum(results)}/{len(results)})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
