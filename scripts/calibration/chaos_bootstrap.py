"""Staging chaos bootstrap: produce labelled incidents for calibration.

Runs a campaign of real faults through the CausalOps API (POST /api/faults). The platform
records every fault in fault_injections with its type, target and timing; calibration reads
those records as ground truth to evaluate and supervise RCA, the anomaly gate and the failure
forecaster. Only run this against a staging or reference environment.

Standard library only. Usage:
  python scripts/calibration/chaos_bootstrap.py --plan scripts/calibration/chaos_plan.reference.json
  python scripts/calibration/chaos_bootstrap.py --plan ... --repeat 2 --shuffle
"""
import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request


def call(method, url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def inject(api, fault_type, target, params, seconds):
    code, body = call("POST", f"{api}/faults", {"type": fault_type, "target": target, "severity": "HIGH",
                                                 "durationSeconds": seconds, "parameters": params})
    if code != 201:
        raise RuntimeError(f"fault {fault_type} on {target} rejected: HTTP {code} {body}")
    return body["id"]


def stop(api, fault_id):
    call("POST", f"{api}/faults/{fault_id}/stop", {})


def run_episode(api, ep, fault_seconds, log):
    ramp = ep.get("ramp")
    if not ramp:
        fid = inject(api, ep["type"], ep["target"], ep.get("parameters", {}), fault_seconds + 30)
        time.sleep(fault_seconds)
        stop(api, fid)
        return
    step = max(5, fault_seconds // len(ramp))
    for latency in ramp:
        fid = inject(api, ep["type"], ep["target"], {**ep.get("parameters", {}), "latencyMs": latency}, step + 30)
        log(f"    ramp {ep['target']} -> {latency} ms")
        time.sleep(step)
        stop(api, fid)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080/api")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--shuffle", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    plan = json.load(open(a.plan, encoding="utf-8"))
    episodes = [ep for _ in range(a.repeat) for ep in plan["episodes"]]
    if a.shuffle:
        random.Random(a.seed).shuffle(episodes)
    fault_s, recovery_s = plan["faultSeconds"], plan["recoverySeconds"]

    def log(msg):
        print(time.strftime("%H:%M:%S"), msg, flush=True)

    total = len(episodes) * (fault_s + recovery_s) / 60
    log(f"{len(episodes)} episodes, about {total:.0f} minutes")
    call("POST", f"{a.api}/faults/clear")
    for i, ep in enumerate(episodes, 1):
        log(f"[{i}/{len(episodes)}] {ep['type']} on {ep['target']} {ep.get('parameters', {})}{' (ramp)' if ep.get('ramp') else ''}")
        try:
            run_episode(a.api, ep, fault_s, log)
        except RuntimeError as e:
            log(f"    skipped: {e}")
            continue
        time.sleep(recovery_s)
    log("campaign finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
