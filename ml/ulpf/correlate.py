"""Cross-device attack-chain correlation (SECURITY_CORRELATION incidents).

Sigma detections from different products are linked when they share the acting address and follow the
kill-chain order within a window: reconnaissance → credential attack → access (a successful login by the
same address, read from the normalized events) → further activity. Each step keeps the products that saw it,
so one incident shows what the Palo Alto, the FortiGate, the sshd and the VPN gateway each contributed.
The ordering is causal in the operational sense used by analysts: the same actor, steps in time order, each
step a precondition of the next; it does not claim more than the logs show.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

STAGES = {"ulpf-003": "reconnaissance", "ulpf-007": "reconnaissance", "ulpf-001": "credential-attack",
          "ulpf-002": "credential-attack", "ulpf-004": "access", "ulpf-006": "exploitation", "ulpf-008": "command-and-control",
          "ulpf-009": "known-bad-infrastructure", "ulpf-010": "exfiltration", "ulpf-005": "exposure"}
ORDER = ["reconnaissance", "exposure", "credential-attack", "exploitation", "access", "command-and-control",
         "known-bad-infrastructure", "exfiltration"]
WINDOW = timedelta(minutes=30)
ACCESS_AFTER = timedelta(minutes=10)  # a successful login this soon after the last failed guess counts as access


def chains(store, now: datetime | None = None) -> list[dict]:
    """Candidate chains: per source address, the stages seen in the last 30 minutes."""
    now = now or datetime.now(timezone.utc)
    hits = store.rows("""SELECT rule_id, rule_title, level, group_key, count, distinct_count, sources, vendors, sample_uids,
                                first_seen, last_seen FROM sigma_hits WHERE last_seen > %s ORDER BY first_seen""", (now - WINDOW,))
    by_actor: dict[str, list[dict]] = {}
    for h in hits:
        key = h["group_key"]
        if key in ("*", "-") or key.startswith(("10.", "192.168.", "172.")):
            continue
        by_actor.setdefault(key, []).append(h)
    out = []
    for actor, hs in by_actor.items():
        steps = [{"stage": STAGES.get(h["rule_id"], "other"), "rule": h["rule_id"], "title": h["rule_title"],
                  "first": h["first_seen"], "last": h["last_seen"], "events": h["count"], "distinct": h["distinct_count"],
                  "vendors": h["vendors"], "sources": h["sources"], "evidence": h["sample_uids"][-5:]} for h in hs]
        cred = [s for s in steps if s["stage"] == "credential-attack"]
        if cred:
            # A compromise looks like success during the guessing or soon after it, not hours later.
            logins = store.rows("""SELECT uid, source_id, received_at, user_name, product FROM ulpf_events
                                   WHERE class_uid = 3002 AND action = 'Allowed' AND src_ip = %s::inet
                                     AND received_at BETWEEN %s AND %s
                                   ORDER BY received_at LIMIT 20""",
                                (actor, min(s["first"] for s in cred), max(s["last"] for s in cred) + ACCESS_AFTER))
            if logins:
                steps.append({"stage": "access", "rule": "successful-login-after-attack",
                              "title": f"Successful login by {', '.join(sorted({l['user_name'] or '?' for l in logins}))} from the attacking address",
                              "first": logins[0]["received_at"], "last": logins[-1]["received_at"], "events": len(logins),
                              "distinct": None, "vendors": sorted({l["product"] or "?" for l in logins}),
                              "sources": sorted({l["source_id"] for l in logins}), "evidence": [l["uid"] for l in logins[:5]],
                              "accounts": sorted({l["user_name"] for l in logins if l["user_name"]})})
        stages = {s["stage"] for s in steps}
        ordered = sorted(steps, key=lambda s: (ORDER.index(s["stage"]) if s["stage"] in ORDER else 99, s["first"]))
        multi_stage = len(stages & {"reconnaissance", "credential-attack", "access", "exploitation"}) >= 2
        if multi_stage:
            out.append({"actor": actor, "steps": ordered, "stages": sorted(stages, key=lambda x: ORDER.index(x) if x in ORDER else 99),
                        "access": "access" in stages, "vendors": sorted({v for s in steps for v in s["vendors"]}),
                        "sources": sorted({x for s in steps for x in s["sources"]}),
                        "first": min(s["first"] for s in steps), "last": max(s["last"] for s in steps)})
    return out
