"""Phase 4 verification: tiered auto-remediation with real executors on the running stack.

Needs a calibrated (ACTIVE) environment (Phase 3) and the Docker executor enabled for the
bundled compose project (CAUSALOPS_DOCKER_PROJECT, default "causalops").

  1. Executor endpoints are protected (engine token; not reachable through /api/engine).
  2. Policy and catalog: Docker executor enabled, autonomy settings exposed.
  3. AUTO (tier 1): a 10-minute SERVICE_FAILURE on payment-service is detected, its root cause is
     named, the container is really restarted, recovery is verified on the following measurements
     and the incident resolves while the injected fault was still scheduled to run.
  4. CONTROL: the same fault with the kill switch on: the policy blocks every action, the incident
     waits for a human and recovers only when the fault ends.
  5. APPROVAL + ROLLBACK (autonomy tier 0): SERVICE_LATENCY on order-service. An operator approves
     raising its CPU/memory limits (a real `docker update`); it does not help, so the limits are
     rolled back to their previous values; the next proposal (restart) is approved and fixes it.
  6. Audit trail, incident timeline and the outcome metrics (MTTR / downtime per handling).
  (--database) 7. A database fault, which no enabled executor can fix, ends in an escalation.

Usage:
  python scripts/dev/verify_phase4.py            # 1-6, about 20 minutes
  python scripts/dev/verify_phase4.py --database
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

results = []
ENGINE = "http://localhost:8000"


def http(method, url, body=None, headers=None, timeout=60):
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
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
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' - ' + str(detail)) if detail != '' else ''}", flush=True)
    return ok


def info(text):
    print(f"     {text}", flush=True)


def container(service):
    cid = subprocess.run(["docker", "compose", "ps", "-q", service], capture_output=True, text=True).stdout.strip()
    out = subprocess.run(["docker", "inspect", "-f", "{{.State.StartedAt}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}}", cid],
                         capture_output=True, text=True).stdout.split()
    return {"id": cid[:12], "started": out[0], "nano_cpus": int(out[1]), "memory": int(out[2])}


def wait(what, fn, timeout, every=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(every)
    info(f"timed out after {timeout}s waiting for {what}")
    return None


def autonomy(api, **kw):
    code, body = http("PUT", f"{api}/remediation/autonomy", {**kw, "changedBy": "verify_phase4"})
    return code, body


def active_ids(api):
    return {i["id"] for i in (http("GET", f"{api}/incidents/active")[1] or [])}


def inject(api, ftype, target, seconds, params=None):
    http("POST", f"{api}/faults/clear")
    before = active_ids(api)
    code, fault = http("POST", f"{api}/faults", {"type": ftype, "target": target, "severity": "HIGH",
                                                 "durationSeconds": seconds, "parameters": params or {}})
    check(code in (200, 201), f"injected {ftype} on {target} for {seconds}s", fault.get("id") if isinstance(fault, dict) else fault)
    return fault, before


def new_incident(api, before):
    def find():
        fresh = [i for i in (http("GET", f"{api}/incidents/active")[1] or []) if i["id"] not in before]
        return fresh[0] if fresh else None
    return wait("an incident", find, 180)


def incident(api, iid):
    return http("GET", f"{api}/incidents/{iid}")[1]


def timeline(api, iid):
    return [e["eventType"] for e in http("GET", f"{api}/incidents/{iid}/timeline")[1]]


def executions(api, iid):
    return http("GET", f"{api}/remediation/executions?incidentId={iid}")[1]


def wait_quiet(api):
    """Lets the previous scenario's incident resolve so scenarios do not overlap."""
    wait("no open incident", lambda: not http("GET", f"{api}/incidents/active")[1], 300)
    time.sleep(20)


def security(api):
    print("1. Executor endpoints are protected")
    code, _ = http("POST", f"{ENGINE}/executors/execute", {})
    check(code in (401, 503), "engine /executors without token", f"HTTP {code}")
    code, _ = http("POST", f"{ENGINE}/executors/execute", {}, {"X-Engine-Token": "wrong-token-wrong-token"})
    check(code == 401, "engine /executors with a wrong token", f"HTTP {code}")
    code, _ = http("POST", f"{api}/engine/executors/execute", {})
    check(code == 403, "/api/engine/executors is not proxied", f"HTTP {code}")


def policy(api):
    print("2. Policy, catalog and executors")
    code, p = http("GET", f"{api}/remediation/policy")
    ok = check(code == 200 and "docker" in p["enabledExecutors"], "Docker executor enabled",
               p.get("enabledExecutors") if isinstance(p, dict) else p)
    if isinstance(p, dict):
        s = p["settings"]
        check(p["environmentStatus"] == "ACTIVE", "environment is ACTIVE (Phase 3 calibration)", p["environmentStatus"])
        info(f"autonomy up to tier {s['autoExecuteMaxTier']}, min RCA confidence {s['minRcaConfidence']}, "
             f"verification {s['verification']}, {len(s['actions'])} catalog actions")
    return ok


def auto_scenario(api):
    print("3. AUTO: tier-1 restart fixes a SERVICE_FAILURE on payment-service")
    wait_quiet(api)
    autonomy(api, autoExecuteMaxTier=1, killSwitch=False, dryRun=False)
    before = container("payment-service")
    fault, before_ids = inject(api, "SERVICE_FAILURE", "payment-service", 600)
    inc = new_incident(api, before_ids)
    if not check(inc is not None, "incident opened", inc and inc["incidentKey"]):
        return None
    iid = inc["id"]
    ex = wait("a verified remediation", lambda: next((e for e in executions(api, iid) if e["status"] in
                                                       ("VERIFIED", "FAILED", "ERROR", "ROLLED_BACK")), None), 360)
    check(ex is not None and ex["mode"] == "AUTO", "remediation executed without a human",
          ex and f"{ex['actionId']} on {ex['targetNode']} ({ex['mode']})")
    if ex:
        check(ex["status"] == "VERIFIED", "recovery verified on real measurements",
              f"{ex['status']} {json.dumps(ex.get('verification', {}).get('watched'))}")
        check(ex["targetNode"] == "payment-service" and ex["executor"] == "docker", "acted on the root cause", ex["targetNode"])
    after = container("payment-service")
    check(after["started"] != before["started"], "payment-service container really restarted",
          f"{before['started']} -> {after['started']}")
    resolved = wait("incident resolved", lambda: (lambda i: i if i["status"] == "RESOLVED" else None)(incident(api, iid)), 300)
    check(resolved is not None, "incident resolved", resolved and resolved.get("resolvedAt"))
    _, faults = http("GET", f"{api}/faults")
    f = next((x for x in faults if x["id"] == fault["id"]), {})
    check(f.get("status") == "ACTIVE", "the injected fault was still scheduled to run (recovery is the action's doing)",
          f.get("status"))
    http("POST", f"{api}/faults/{fault['id']}/stop")
    info("timeline: " + " -> ".join(timeline(api, iid)))
    return iid


def control_scenario(api, seconds):
    print(f"4. CONTROL: kill switch on, the same fault lasts its {seconds}s")
    wait_quiet(api)
    code, _ = autonomy(api, killSwitch=True)
    check(code == 200, "kill switch on")
    before = container("payment-service")
    fault, before_ids = inject(api, "SERVICE_FAILURE", "payment-service", seconds)
    inc = new_incident(api, before_ids)
    if not check(inc is not None, "incident opened", inc and inc["incidentKey"]):
        autonomy(api, killSwitch=False)
        return None
    iid = inc["id"]
    escalated = wait("escalation", lambda: (lambda i: i if i["status"] in ("MANUAL_INTERVENTION", "RESOLVED") else None)(incident(api, iid)), 180)
    check(escalated is not None and escalated["status"] == "MANUAL_INTERVENTION", "policy blocked execution, human needed",
          escalated and escalated["status"])
    _, recs = http("GET", f"{api}/remediation/recommendations?incidentId={iid}")
    top = recs[0] if recs else {}
    rules = [r["id"] for r in (top.get("policy") or {}).get("rules", []) if not r["passed"]]
    check("KILL_SWITCH_OFF" in rules, "blocked by the kill switch rule", rules)
    resolved = wait("incident resolved", lambda: (lambda i: i if i["status"] == "RESOLVED" else None)(incident(api, iid)), seconds + 240)
    check(resolved is not None, "recovered only after the fault ended", resolved and resolved.get("resolvedAt"))
    check(container("payment-service")["started"] == before["started"], "no container was touched")
    autonomy(api, killSwitch=False)
    http("POST", f"{api}/faults/{fault['id']}/stop")
    return iid


def approval_scenario(api):
    print("5. APPROVAL + ROLLBACK: autonomy tier 0, SERVICE_LATENCY on order-service")
    wait_quiet(api)
    autonomy(api, autoExecuteMaxTier=0, killSwitch=False)
    limits_before = container("order-service")
    fault, before_ids = inject(api, "SERVICE_LATENCY", "order-service", 900, {"latencyMs": 700})
    inc = new_incident(api, before_ids)
    if not check(inc is not None, "incident opened", inc and inc["incidentKey"]):
        autonomy(api, autoExecuteMaxTier=1)
        return None
    iid = inc["id"]

    def awaiting():
        _, recs = http("GET", f"{api}/remediation/recommendations?incidentId={iid}&status=AWAITING_APPROVAL")
        return recs[0] if recs else None
    first = wait("an approval request", awaiting, 240)
    check(first is not None and incident(api, iid)["status"] == "AWAITING_APPROVAL", "waiting for an operator",
          first and f"{first['actionId']}: {first['policy']['summary']}")
    _, recs = http("GET", f"{api}/remediation/recommendations?incidentId={iid}")
    limits = next((r for r in recs if r["actionId"] == "docker-raise-limits" and r["status"] in ("AWAITING_APPROVAL", "PROPOSED")), None)
    if not check(limits is not None, "raise-limits is among the proposals", [r["actionId"] for r in recs]):
        autonomy(api, autoExecuteMaxTier=1)
        http("POST", f"{api}/faults/{fault['id']}/stop")
        return iid
    code, ex = http("POST", f"{api}/remediation/recommendations/{limits['id']}/approve",
                    {"by": "verify_phase4@oncall", "reason": "try more headroom first"})
    check(code == 200 and ex["mode"] == "APPROVED", "operator approval executes the tier-2 action", f"HTTP {code}")
    raised = wait("limits raised", lambda: (lambda c: c if (c["nano_cpus"], c["memory"]) > (limits_before["nano_cpus"], limits_before["memory"]) and c["memory"] >= limits_before["memory"] else None)(container("order-service")), 60, 2)
    check(raised is not None, "CPU/memory limits really raised (docker update)",
          raised and f"cpu {limits_before['nano_cpus']} -> {raised['nano_cpus']}, mem {limits_before['memory']} -> {raised['memory']}")
    failed = wait("verification verdict", lambda: next((e for e in executions(api, iid) if e["id"] == ex["id"] and
                                                         e["status"] not in ("EXECUTING", "VERIFYING")), None), 300)
    check(failed is not None and failed["status"] == "ROLLED_BACK", "did not help: verification failed and the change was rolled back",
          failed and failed["status"])
    restored = container("order-service")
    check(restored["nano_cpus"] == limits_before["nano_cpus"] and restored["memory"] == limits_before["memory"],
          "limits restored to their previous values", f"cpu {restored['nano_cpus']}, mem {restored['memory']}")
    restart = wait("next proposal", lambda: (lambda r: r if r and r["actionId"] == "docker-restart" else None)(awaiting()), 120)
    check(restart is not None, "next proposal (restart) waits for approval", restart and restart["status"])
    if restart:
        code, ex2 = http("POST", f"{api}/remediation/recommendations/{restart['id']}/approve",
                         {"by": "verify_phase4@oncall", "reason": "restart"})
        check(code == 200, "restart approved", f"HTTP {code}")
        done = wait("verified restart", lambda: next((e for e in executions(api, iid) if e["id"] == ex2["id"] and
                                                       e["status"] not in ("EXECUTING", "VERIFYING")), None), 300)
        check(done is not None and done["status"] == "VERIFIED", "restart verified", done and done["status"])
    resolved = wait("incident resolved", lambda: (lambda i: i if i["status"] == "RESOLVED" else None)(incident(api, iid)), 300)
    check(resolved is not None, "incident resolved")
    http("POST", f"{api}/faults/{fault['id']}/stop")
    autonomy(api, autoExecuteMaxTier=1)
    info("timeline: " + " -> ".join(timeline(api, iid)))
    return iid


def database_scenario(api):
    print("7. DATABASE fault outside the enabled executors' reach")
    wait_quiet(api)
    fault, before_ids = inject(api, "DB_LATENCY", "inventory-db", 300, {"latencyMs": 400})
    inc = new_incident(api, before_ids)
    if not check(inc is not None, "incident opened", inc and inc["incidentKey"]):
        return None
    iid = inc["id"]
    final = wait("a decision", lambda: (lambda i: i if i["status"] in ("MANUAL_INTERVENTION", "AWAITING_APPROVAL", "MITIGATED") else None)(incident(api, iid)), 400)
    check(final is not None and final["status"] in ("MANUAL_INTERVENTION", "AWAITING_APPROVAL"),
          "no unsafe automatic action: a human decides", final and final["status"])
    info("timeline: " + " -> ".join(timeline(api, iid)))
    http("POST", f"{api}/faults/{fault['id']}/stop")
    return iid


def evidence(api, ids):
    print("6. Audit trail and outcome metrics")
    for name, iid in ids.items():
        if not iid:
            continue
        _, audit = http("GET", f"{api}/remediation/audit?incidentId={iid}")
        actions = [a["action"] for a in audit]
        check(len(actions) > 0, f"{name}: audit trail", " -> ".join(actions))
    _, m = http("GET", f"{api}/remediation/metrics")
    rows = {r["incidentId"]: r for r in m["incidents"]}
    for name, iid in ids.items():
        r = rows.get(iid)
        if r:
            info(f"{name:9s} {r['incidentKey']}: handling={r['handling']} MTTD={r['mttdSeconds']}s "
                 f"mitigation={r['timeToMitigationSeconds']}s MTTR={r['mttrSeconds']}s downtime={r['downtimeSeconds']}s")
    auto, ctrl = rows.get(ids.get("auto")), rows.get(ids.get("control"))
    if auto and ctrl and auto["mttrSeconds"] and ctrl["mttrSeconds"]:
        check(auto["mttrSeconds"] < ctrl["mttrSeconds"], "auto-remediation shortened recovery",
              f"MTTR {auto['mttrSeconds']}s vs {ctrl['mttrSeconds']}s, downtime {auto['downtimeSeconds']}s vs {ctrl['downtimeSeconds']}s")
    info("summary by handling: " + json.dumps(m["summary"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080/api")
    ap.add_argument("--control-seconds", type=int, default=240)
    ap.add_argument("--database", action="store_true")
    ap.add_argument("--skip-control", action="store_true")
    a = ap.parse_args()
    security(a.api)
    if not policy(a.api):
        print("Docker executor is not enabled; set CAUSALOPS_DOCKER_PROJECT and restart causalops-api.")
        sys.exit(1)
    ids = {"auto": auto_scenario(a.api)}
    if not a.skip_control:
        ids["control"] = control_scenario(a.api, a.control_seconds)
    ids["approval"] = approval_scenario(a.api)
    if a.database:
        ids["database"] = database_scenario(a.api)
    evidence(a.api, ids)
    http("POST", f"{a.api}/faults/clear")
    passed = sum(results)
    print(f"\nRESULT: {'ALL CHECKS PASSED' if passed == len(results) else 'SOME CHECKS FAILED'} ({passed}/{len(results)})")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/../..")
    main()
