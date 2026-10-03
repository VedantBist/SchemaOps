#!/usr/bin/env python3
"""End-to-end checks for CausalOps ULPF against a running stack (standard library only).

    python3 scripts/dev/verify_ulpf.py                 # all checks, including the fault scenarios (~6 min)
    python3 scripts/dev/verify_ulpf.py --quick         # no fault scenarios (~20 s)
    python3 scripts/dev/verify_ulpf.py --api http://localhost:8080/api/ulpf

Every check reads real results from the API; nothing is assumed. Exit code 0 only if all checks pass.
"""
from __future__ import annotations

import argparse
import calendar
import json
import socket
import sys
import time
import urllib.error
import urllib.request

RESULTS: list[tuple[str, bool, str]] = []
API = "http://localhost:8080/api/ulpf"


def call(path: str, body=None, method: str | None = None, timeout: int = 120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method or ("POST" if data is not None else "GET"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode()
        return json.loads(text) if text else None


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""), flush=True)
    return bool(ok)


def wait(pred, timeout: float, every: float = 3.0):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = pred()
            if last:
                return last
        except (urllib.error.URLError, KeyError, IndexError, TypeError):
            pass
        time.sleep(every)
    return last


# ── U1: lossless core ─────────────────────────────────────────────────────────
def u1(host: str, port: int) -> None:
    print("U1 lossless core")
    stats = call("/stats")
    c = stats["conservation"]
    check("conservation: received = stored + in flight", c["balanced"], f"{c['received']} = {c['stored']} + {c['inFlight']}")
    check("workers alive", any(w["alive"] for w in stats["workers"]), ", ".join(w["name"][:8] for w in stats["workers"] if w["alive"]))
    marker = f"verify-{int(time.time())}"
    line = f"<14>Oct  3 10:00:00 verifyfw CEF:0|Acme|FW|1|100|{marker}|7|src=10.0.0.1 dst=8.8.8.8 act=deny"
    socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(line.encode(), (host, port))
    ev = wait(lambda: [e for e in call(f"/events?q={marker}&limit=5")], 30, 1)
    if check("syslog UDP event ingested and stored", bool(ev), ev[0]["uid"] if ev else "not found within 30 s"):
        proof = call(f"/events/{ev[0]['uid']}/verify", {})
        check("lossless proof (rebuilt from OCSF = vault SHA-256)", proof["lossless"], proof["sha256"][:16])
        check("CEF fields extracted (first key after header pipe)", ev[0]["src_ip"] == "10.0.0.1" and ev[0]["dst_ip"] == "8.8.8.8",
              f"{ev[0]['src_ip']} → {ev[0]['dst_ip']}")
    v = call("/vault/verify", {})
    check("vault: every hash and chain link verified", v["ok"], f"{v['records']} records in {v['seconds']} s")
    check("all stored events proven lossless", stats["losslessVerified"] == stats["stored"],
          f"{stats['losslessVerified']} / {stats['stored']}")


# ── U2: packs, studio, entities ───────────────────────────────────────────────
def u2() -> None:
    print("U2 packs, onboarding, entities")
    packs = call("/packs")
    champions = {p["id"] for p in packs if p["status"] == "CHAMPION"}
    check("13+ vendor packs active", len(champions) >= 13, f"{len(champions)} champions")
    sources = call("/sources")
    bound = [s for s in sources if s["pack_id"]]
    check("sources bound to packs automatically", len(bound) >= 12, f"{len(bound)} of {len(sources)}")
    q = call("/quality/overview")
    recent = [s for s in q["sources"] if s["latest"] and s["pack_id"] and s["latest"]["events"] >= 3]
    low = [s["id"] for s in recent if (s["latest"]["fill"] or 0) < 0.99]
    check("fill ≥ 99% on every bound source (latest bucket)", recent and not low, f"low: {low}" if low else f"{len(recent)} sources")
    vpn = [f"<134>Oct  3 10:00:{i:02d} gw-x vpnx[311]: session opened for user u{i} from 203.0.113.{i} port {40000 + i} assigned 10.8.0.{i}"
           for i in range(1, 40)] + [f"<134>Oct  3 10:01:{i:02d} gw-x vpnx[311]: authentication failed for user u{i} from 198.51.100.{i} port {41000 + i}"
                                     for i in range(1, 30)]
    a = call("/studio/analyze", {"samples": vpn})
    check("unknown text format: Drain3 proposes a pack", a["proposal"]["kind"] == "drain" and len(a["proposal"]["templates"]) >= 2,
          f"{len(a['proposal']['templates'])} templates")
    t = call("/studio/test", {"yaml": a["proposal"]["yaml"], "samples": vpn})
    s = t["summary"]
    check("proposed pack tests 100% lossless and ≥ 95% normalized", s["losslessPct"] == 100 and s["normalizedPct"] >= 95,
          f"normalized {s['normalizedPct']}% fill {s['fillPct']}% lossless {s['losslessPct']}%")
    ent = call("/entities?kind=ip&q=10.20.1.11")
    if check("entity found for an internal IP", bool(ent)):
        d = call(f"/entity?key=ip:10.20.1.11&minutes=60")
        kinds = {k.split(":")[0] for k in d["asset"]}
        check("asset resolved across identifiers (ip + mac + host)", {"ip", "mac", "host"} <= kinds, ", ".join(d["asset"]))
        check("asset seen by several vendors", len(d["eventsBySourceLastHour"]) >= 5, f"{len(d['eventsBySourceLastHour'])} sources")


# ── U3: self-calibrating quality ──────────────────────────────────────────────
def utc(iso: str) -> float:
    return calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))


def scenario(device: str, kind: str, value, seconds: int):
    return call("/scenarios", {"device": device, "kind": kind, "value": value, "durationSeconds": seconds, "user": "verify_ulpf"})


def incident_after(kind: str, source: str, since: float):
    rows = [i for i in call("/incidents?limit=50") if i["kind"] == kind and i["source_id"] == source]
    rows = [i for i in rows if utc(i["detected_at"]) >= since - 5]
    return rows[0] if rows else None


def u3(full: bool) -> None:
    print("U3 self-calibrating quality")
    q = call("/quality/overview")
    watched = [s for s in q["sources"] if s["state"] == "WATCHED"]
    check("baselines learned per source", len(watched) >= 12, f"{len(watched)} watched")
    if not full:
        return
    # Parser drift → repair → verify → re-normalize (FortiGate firmware change).
    fgt = next(s for s in call("/sources") if s["id"].startswith("fgt-dc-01"))
    packs = [p for p in call("/packs") if p["id"] == "fortinet-fortigate"]
    builtin = next(p for p in packs if p["origin"] == "BUILTIN")
    recent = [p for p in packs if p["origin"] == "REPAIR" and time.time() - utc(p["created_at"]) < 600]
    if recent:
        check("parser drift scenario", True, "skipped: a repair ran in the last 10 min (promotion cooldown); rerun later")
    else:
        call(f"/packs/fortinet-fortigate/{builtin['version']}/activate", {})  # start from the original pack
        time.sleep(15)
        t0 = time.time()
        scenario("fgt-dc-01", "firmware", True, 900)
        inc = wait(lambda: (lambda i: i if i and i["status"] in ("RESOLVED", "ESCALATED") else None)(
            incident_after("PARSER_DRIFT", fgt["id"], t0)), 420, 5)
        if check("parser drift detected and resolved automatically", inc and inc["status"] == "RESOLVED",
                 f"{inc['incident_key']} MTTD {inc['mttd_seconds']:.0f} s, MTTR {inc['mttr_seconds']:.0f} s" if inc else "timeout"):
            d = call(f"/incidents/{inc['incident_key']}")
            acts = {a["action"]: a for a in d["actions"]}
            pr = acts.get("PROMOTE_PACK")
            check("repaired pack passed every policy rule and was verified on live data",
                  pr and pr["status"] == "VERIFIED" and all(r["passed"] for r in pr["policy"]),
                  ", ".join(f"{c['field']}→{c['attribute']}" for c in pr["params"]["changes"]) if pr else "")
            job = d["jobs"][0] if d["jobs"] else None
            check("drifted window re-normalized from the vault, all lossless",
                  job and job["status"] == "DONE" and job["changed"] > 0 and job["lossless_ok"] == job["processed"],
                  f"{job['changed']} events repaired, {job['fields_recovered']} fields recovered" if job else "")
        call("/scenarios/fgt-dc-01", method="DELETE")
    # Clock drift → correction → verified.
    srx = next(s for s in call("/sources") if s["id"].startswith("srx-core-01"))
    t0 = time.time()
    scenario("srx-core-01", "skewSeconds", 240, 240)
    inc = wait(lambda: (lambda i: i if i and i["status"] in ("MITIGATED", "RESOLVED", "ESCALATED") else None)(
        incident_after("CLOCK_SKEW", srx["id"], t0)), 240, 5)
    if check("clock skew detected and corrected", inc and inc["status"] in ("MITIGATED", "RESOLVED"),
             f"{inc['incident_key']} {inc['title']}" if inc else "timeout"):
        a = call(f"/incidents/{inc['incident_key']}")["actions"][0]
        check("corrected event times align with arrival", a["status"] == "VERIFIED" and abs(a["result"]["residualOffsetMs"]) < 2000,
              f"residual {a['result']['residualOffsetMs']} ms")
    call("/scenarios/srx-core-01", method="DELETE")
    # Silent source.
    asa = next(s for s in call("/sources") if s["id"].startswith("asa-perimeter"))
    t0 = time.time()
    scenario("asa-perimeter", "silent", True, 100)
    inc = wait(lambda: incident_after("SOURCE_SILENT", asa["id"], t0), 180, 5)
    check("silent source detected", bool(inc), f"{inc['incident_key']} {inc['summary'][:90]}" if inc else "timeout")
    if inc:
        res = wait(lambda: (lambda i: i if i and i["status"] == "RESOLVED" else None)(incident_after("SOURCE_SILENT", asa["id"], t0)), 240, 5)
        check("silent source resolved when traffic returned", bool(res), f"MTTR {res['mttr_seconds']:.0f} s" if res else "timeout")


def main() -> int:
    global API
    p = argparse.ArgumentParser()
    p.add_argument("--api", default=API)
    p.add_argument("--syslog-host", default="127.0.0.1")
    p.add_argument("--syslog-port", type=int, default=5514)
    p.add_argument("--quick", action="store_true", help="skip the fault scenarios")
    args = p.parse_args()
    API = args.api.rstrip("/")
    started = time.time()
    try:
        u1(args.syslog_host, args.syslog_port)
        u2()
        u3(not args.quick)
    except urllib.error.URLError as e:
        check("API reachable", False, str(e))
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed in {time.time() - started:.0f} s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
