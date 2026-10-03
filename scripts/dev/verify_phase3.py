"""Phase 3 verification: the engine learns this environment and analyses real incidents.

Checks against the running stack:
  1. Engine health reflects its real dependencies (database, model store).
  2. The archived tg_v1 serving endpoints are gone.
  3. Calibration on stored telemetry and labelled faults (runs one if no champion exists, or with --calibrate).
  4. A champion model is registered with a checksum; the environment is ACTIVE (all quality gates passed).
  5. Live evaluation stores anomaly scores and forecasts with model version and method.
  6. A counterfactual on a recorded fault window passes its own validity checks.
  7. (--incident) A real DB fault is detected, its RCA names inventory-db automatically, a
     counterfactual is stored with it, and the incident resolves after the fault stops.
  8. No EXP-015 / tg_v1 fallback remains in the serving code.

Usage:
  python scripts/dev/verify_phase3.py
  python scripts/dev/verify_phase3.py --calibrate --incident
"""
import argparse
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
results = []


def http(method, url, body=None, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


def check(ok, label, detail=""):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' - ' + str(detail)) if detail != '' else ''}")
    return ok


def engine_health(api):
    print("1. Engine health reflects real dependencies")
    code, h = http("GET", f"{api}/engine/health")
    check(code == 200 and h["components"] == {"database": "HEALTHY", "model_store": "HEALTHY"}, "/api/engine/health",
          h.get("components") if isinstance(h, dict) else h)
    code, _ = http("GET", f"{api}/engine/ready")
    check(code == 200, "/api/engine/ready", f"HTTP {code}")


def legacy_removed(api):
    print("2. Archived tg_v1 serving endpoints are removed")
    for path in ("/engine/analyze/root-cause", "/engine/causal/counterfactual", "/engine/predict/failure",
                 "/engine/predict/failure-v2", "/engine/simulate/counterfactual", "/engine/causal/recommendation"):
        code, _ = http("POST", f"{api}{path}", {})
        check(code in (404, 405), path, f"HTTP {code}")


def calibration(api, force):
    print("3. Calibration on this environment's own telemetry")
    _, st = http("GET", f"{api}/calibration/status")
    if force or st.get("champion") is None:
        code, body = http("POST", f"{api}/calibration/run")
        if not check(code == 202, "calibration started", body):
            return None
        deadline = time.time() + 900
        while time.time() < deadline:
            time.sleep(10)
            _, st = http("GET", f"{api}/calibration/status")
            if st["runs"] and st["runs"][0]["status"] != "RUNNING":
                break
    run = st["runs"][0] if st["runs"] else None
    if not check(run is not None and run["status"] == "SUCCEEDED", "latest calibration run succeeded",
                 run and (run.get("error") or run["decision"])):
        return None
    m = run["metrics"] if not isinstance(run["metrics"], str) else json.loads(run["metrics"])
    d = m["data"]
    print(f"     data: {d['samples']} samples ({d['from']} .. {d['to']}), {d['variables']} variables, "
          f"{d['nodes']} nodes, {d['edges']} edges, {d['labelled_episodes']} labelled episodes")
    fpr = m["gate"].get("heldout_false_positive_rate")
    check(fpr is not None, "held-out gate false-positive rate measured", fpr)
    ep = m.get("episodes")
    if check(ep is not None, "labelled incidents evaluated (leave-one-episode-out)", ep and ep["count"]):
        print(f"     detection recall {ep['detection_recall']} on {ep['count']}, median delay {ep['median_detection_delay_s']} s; "
              f"RCA on {ep['single_root']} single-root incidents: top-1 {ep['rca_top1_accuracy']}, top-2 {ep['rca_top2_accuracy']} "
              f"({ep['concurrent_excluded_from_rca']} concurrent excluded)")
        for t, v in ep["rca_by_fault_type"].items():
            print(f"       {t:<28} {v['top1']}/{v['episodes']} correct at top-1")
        for r in ep["per_episode"]:
            print(f"       {r['type']:<26} {r['target']:<18} {'CONCURRENT ' if r['concurrent'] else ''}detected={r['detected']!s:<5} "
                  f"delay={r['detection_delay_s']} expected={r['expected']} got={r['predicted'][:2]}")
    for h, f in m["forecast"].items():
        print(f"     forecast {h}s: method={f['method']} positives={f.get('positives')} "
              f"{f.get('note') or f.get('learned_cv') or ''}")
    print(f"     SCM: {m['scm']}")
    r2 = m["scm"].get("median_holdout_r2")
    check(r2 is not None and r2 >= 0.5, "causal model explains held-out data (median cross-validated R2 >= 0.5)", r2)
    return st


def registry(api, st):
    print("4. Model registry and lifecycle")
    _, models = http("GET", f"{api}/models")
    champ = [m for m in models if m["status"] == "CHAMPION"]
    check(len(champ) == 1 and len(champ[0]["checksum"]) == 64, "exactly one champion with a SHA-256 checksum",
          champ and champ[0]["version"])
    deadline = time.time() + 150   # the platform applies finished runs on its next minute tick
    while True:
        _, st = http("GET", f"{api}/calibration/status")
        if st["environment"]["status"] != "LEARNING" or time.time() > deadline:
            break
        time.sleep(10)
    check(st["environment"]["status"] == "ACTIVE", "environment ACTIVE (every quality gate passed)",
          f"{st['environment']['status']}: {st['statusReason']}")
    check(st["nextRetrainDue"] is not None, "weekly retrain scheduled", st["nextRetrainDue"])


def live(api):
    print("5. Live evaluation stores anomaly scores and forecasts")
    deadline = time.time() + 90
    scored, preds = [], []
    while time.time() < deadline and not (scored and preds):
        _, m = http("GET", f"{api}/metrics?minutes=1")
        scored = [s for s in m["samples"] if s["anomalyScore"] is not None]
        _, preds = http("GET", f"{api}/predictions")
        preds = [p for p in preds if p.get("method")] if isinstance(preds, list) else []
        time.sleep(5)
    check(scored, "anomaly_score written on fresh snapshots", f"{len(scored)} scored samples in the last minute")
    _, p = http("GET", f"{api}/predictions")
    if check(isinstance(p, list) and p, "forecasts stored", f"{len(p) if isinstance(p, list) else p} rows"):
        print(f"     e.g. {p[0]['service']} {p[0]['horizonSeconds']}s p={p[0]['probability']} {p[0]['riskLevel']}")


def counterfactual_on_recorded_fault(api):
    print("6. Counterfactual on a recorded fault (real SCM rollout)")
    _, faults = http("GET", f"{api}/faults")
    _, envs = http("GET", f"{api}/environments")
    f = next((x for x in faults if x["type"] == "DB_LATENCY" and x["stoppedAt"]), None)
    if not check(f is not None, "a recorded DB_LATENCY fault exists"):
        return
    start = f["startedAt"]
    code, cf = http("POST", f"{api}/engine/counterfactual", {
        "environment_id": envs[0]["id"], "start": start, "end": f["stoppedAt"], "unit": "inventory-db",
        "intervene_from": start})
    if not check(code == 200, "/api/engine/counterfactual", f"HTTP {code} {'' if code == 200 else cf}"):
        return
    v = cf["validity"]
    check(v["pre_intervention_max_diff"] == 0, "identical to the observation before the intervention")
    check(v["status"] == "PASS", "counterfactual validity PASS (fit, physical bounds, reachability)", v["warnings"])
    check(v["unreachable_max_diff"] < 1e-6, "payment-service (unreachable from inventory-db) unchanged", v["unreachable_max_diff"])
    impact = cf["entry_impact"].get("api-gateway", {})
    check(impact.get("peak_avoided_latency_ms", 0) > 0, "removing the DB delay lowers gateway latency",
          f"peak {impact.get('peak_avoided_latency_ms')} ms, mean {impact.get('mean_avoided_latency_ms')} ms; "
          f"{cf['ensemble_members']} ensemble members; validity {v['status']} {v['warnings']}")


def incident(api):
    print("7. Real fault -> detection -> automatic RCA -> resolution")
    http("POST", f"{api}/faults/clear")
    code, fault = http("POST", f"{api}/faults", {"type": "DB_LATENCY", "target": "inventory-db", "severity": "HIGH",
                                                  "durationSeconds": 180, "parameters": {"latencyMs": 400}})
    if not check(code == 201, "injected 400 ms DB latency on inventory-db"):
        return
    t0 = time.time()
    inc = None
    while time.time() - t0 < 120 and inc is None:
        time.sleep(3)
        _, active = http("GET", f"{api}/incidents?state=active")
        inc = active[0] if active else None
    if not check(inc is not None, "incident opened", inc and f"{inc['incidentKey']} by {inc['detectionSource']} "
                                                         f"after {time.time() - t0:.0f}s"):
        http("POST", f"{api}/faults/{fault['id']}/stop")
        return
    rca = None
    while time.time() - t0 < 240 and rca is None:
        time.sleep(5)
        code, body = http("GET", f"{api}/incidents/{inc['id']}/root-cause")
        rca = body if code == 200 else None
    if check(rca is not None, "RCA ran automatically", rca and rca["analysis"]["methodology"][:60]):
        a = rca["analysis"]
        check(a["rootCause"] == "inventory-db", "root cause is inventory-db",
              f"{a['rootCause']} ({a['rootCauseKind']}, confidence {a['confidence']:.2f}); candidates "
              f"{[c['service'] for c in rca['candidates'][:3]]}")
        cf = rca["counterfactual"]
        if check(cf is not None, "counterfactual stored with the RCA"):
            res = cf["result"] if not isinstance(cf["result"], str) else json.loads(cf["result"])
            check(res["validity"]["status"] == "PASS" and res["validity"]["pre_intervention_max_diff"] == 0,
                  "counterfactual validity PASS",
                  f"{res['validity']['status']}, gateway peak avoided "
                  f"{res['entry_impact'].get('api-gateway', {}).get('peak_avoided_latency_ms')} ms")
    http("POST", f"{api}/faults/{fault['id']}/stop")
    resolved = None
    while time.time() - t0 < 600 and resolved is None:
        time.sleep(5)
        _, i = http("GET", f"{api}/incidents/{inc['id']}")
        resolved = i if i["status"] == "RESOLVED" else None
    check(resolved is not None, "incident resolved after the fault stopped")
    _, tl = http("GET", f"{api}/incidents/{inc['id']}/timeline")
    print("     timeline: " + " -> ".join(e["eventType"] for e in tl))


def no_fallbacks():
    print("8. No archived-dataset fallback in serving code")
    hits = []
    for base in (ROOT / "ai-engine" / "app", ROOT / "ml" / "engine"):
        for f in base.rglob("*.py"):
            text = f.read_text(encoding="utf-8")
            for pat in (r"EXP-0\d\d", r"dataset/tg_v1", r"TemporalGraphDataset", r"simulation\.propagate",
                        r"prediction\.baseline", r"causal_scm"):
                if re.search(pat, text):
                    hits.append(f"{f.relative_to(ROOT)}: {pat}")
    check(not hits, "no EXP-015 / tg_v1 dataset use in ai-engine/app or ml/engine", "; ".join(hits))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080/api")
    ap.add_argument("--calibrate", action="store_true", help="run a fresh calibration even if a champion exists")
    ap.add_argument("--incident", action="store_true")
    a = ap.parse_args()
    engine_health(a.api)
    legacy_removed(a.api)
    st = calibration(a.api, a.calibrate)
    if st is not None:
        registry(a.api, st)
        live(a.api)
        counterfactual_on_recorded_fault(a.api)
        if a.incident:
            incident(a.api)
    no_fallbacks()
    ok = all(results)
    print(f"\nRESULT: {'ALL CHECKS PASSED' if ok else 'SOME CHECKS FAILED'} ({sum(results)}/{len(results)})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
