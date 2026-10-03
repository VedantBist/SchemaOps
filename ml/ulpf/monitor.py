"""ULPF monitor: CausalOps watching the log pipeline itself.

Every few seconds it
  1. aggregates per-source quality buckets (fill, normalized share, volume, device-clock offset),
  2. (re)calibrates each source's baselines from its own quiet history (quality.py),
  3. opens incidents: PARSER_DRIFT, SOURCE_SILENT, CLOCK_SKEW,
  4. drives each incident through tiered, verified remediation:
       drift  → repaired pack inferred (repair.py) → shadow-tested against the champion → policy →
                promote (auto for tier 1, else approval) → verify on live buckets → re-normalize the broken
                window → resolved; verification failure → roll back → escalate to a human
       skew   → clock correction applied per source → verified → resolved once the device clock is fixed
       silent → evidence and escalation; resolved when traffic returns (nothing on the device side can be
                fixed from here)
  5. runs re-normalization jobs.
Every decision is written to the incident timeline and to the shared append-only audit_log.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from . import packstore, quality, repair, renormalize, vault
from .ocsf import get_path
from .store import Store

log = logging.getLogger("ulpf.monitor")
VAULT_DIR = os.environ.get("ULPF_VAULT_DIR", "/var/lib/ulpf/vault")
RELOAD_S = 12          # workers reload packs and clock corrections every 10 s
VERIFY_TIMEOUT_S = 150
ACTOR = "ulpf-monitor"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Monitor:
    def __init__(self, store: Store | None = None):
        self.store = store or Store()
        self.last_calibration = 0.0
        self.baselines: dict[str, dict[str, quality.Baseline]] = {}

    # ── settings and guards ─────────────────────────────────────────────────
    def settings(self) -> dict:
        rows = self.store.rows("SELECT key, value FROM ulpf_settings")
        return {r["key"]: r["value"] for r in rows}

    def kill_switch(self) -> bool:
        rows = self.store.rows("SELECT bool_or(COALESCE((config->'remediation'->>'killSwitch')::boolean, false)) AS k "
                               "FROM environments")
        env = os.environ.get("CAUSALOPS_REMEDIATION_KILL_SWITCH", "false") == "true"
        return bool(rows and rows[0]["k"]) or env

    def audit(self, action: str, incident_id, entity_id, detail: dict) -> None:
        self.store.execute("INSERT INTO audit_log (actor, action, entity_type, entity_id, incident_id, detail) "
                           "VALUES (%s, %s, 'ulpf', %s, %s, %s)",
                           (ACTOR, action, entity_id, incident_id, json.dumps(detail, default=str)))

    def timeline(self, incident_id, kind: str, payload: dict) -> None:
        self.store.execute("INSERT INTO ulpf_incident_events (incident_id, type, payload) VALUES (%s, %s, %s)",
                           (incident_id, kind, json.dumps(payload, default=str)))

    # ── 1. quality buckets ──────────────────────────────────────────────────
    def aggregate(self, b: int, lookback_buckets: int = 3) -> None:
        self.store.execute("""
            INSERT INTO ulpf_quality (source_id, bucket, events, normalized, partial, quarantined, lossless_ok, fill_avg, skew_ms, pack)
            SELECT source_id, to_timestamp(floor(extract(epoch FROM received_at) / %(b)s) * %(b)s) AS bucket,
                   count(*), count(*) FILTER (WHERE status = 'NORMALIZED'), count(*) FILTER (WHERE status = 'PARTIAL'),
                   count(*) FILTER (WHERE status = 'QUARANTINED'), count(*) FILTER (WHERE lossless), avg(fill),
                   (percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM received_at - device_time) * 1000)
                        FILTER (WHERE device_time IS NOT NULL))::bigint,
                   max(parser)
            FROM ulpf_events
            WHERE received_at >= to_timestamp(floor(extract(epoch FROM now()) / %(b)s) * %(b)s) - make_interval(secs => %(b)s * %(n)s)
            GROUP BY 1, 2
            ON CONFLICT (source_id, bucket) DO UPDATE SET events = EXCLUDED.events, normalized = EXCLUDED.normalized,
                partial = EXCLUDED.partial, quarantined = EXCLUDED.quarantined, lossless_ok = EXCLUDED.lossless_ok,
                fill_avg = EXCLUDED.fill_avg, skew_ms = EXCLUDED.skew_ms, pack = EXCLUDED.pack""", {"b": b, "n": lookback_buckets})

    def series(self, source: str, since: datetime, until: datetime, b: int) -> list[dict]:
        """Completed buckets in [since, until), zero-filled (a bucket without events is a real zero)."""
        rows = self.store.rows("SELECT bucket, events, normalized, fill_avg, skew_ms FROM ulpf_quality "
                               "WHERE source_id = %s AND bucket >= %s AND bucket < %s ORDER BY bucket",
                               (source, since, until))
        by = {r["bucket"]: r for r in rows}
        out = []
        t = datetime.fromtimestamp((int(since.timestamp()) // b) * b, tz=timezone.utc)
        while t + timedelta(seconds=b) <= until:
            r = by.get(t)
            n = int(r["events"]) if r else 0
            out.append({"bucket": t, "events": n,
                        "fill": float(r["fill_avg"]) if r and r["fill_avg"] is not None else None,
                        "normalized_ratio": (int(r["normalized"]) / n) if n else 0.0,
                        "skew_ms": int(r["skew_ms"]) if r and r["skew_ms"] is not None else None})
            t += timedelta(seconds=b)
        return out

    # ── 2. calibration ──────────────────────────────────────────────────────
    def calibrate(self, cfg: dict) -> None:
        b = int(cfg["bucketSeconds"])
        need = int(cfg["learningMinutes"]) * 60 // b
        now = utcnow()
        sources = self.store.rows("""SELECT s.id, s.pack_id, s.pack_bound_at,
                                            (SELECT min(bucket) FROM ulpf_quality q WHERE q.source_id = s.id) AS first_bucket
                                     FROM log_sources s""")
        incidents = self.store.rows("SELECT source_id, onset_at, detected_at, resolved_at FROM ulpf_incidents "
                                    "WHERE detected_at > now() - interval '6 hours'")
        for src in sources:
            if src["first_bucket"] is None:
                continue
            since = max(src["first_bucket"], now - timedelta(hours=6))
            rows = self.series(src["id"], since, now - timedelta(seconds=b), b)
            # Buckets inside an incident (from just before its onset to its resolution) are not "normal".
            windows = [((i["onset_at"] or i["detected_at"]) - timedelta(seconds=2 * b), i["resolved_at"] or now)
                       for i in incidents if i["source_id"] == src["id"]]
            quiet = [r for r in rows if not any(a <= r["bucket"] <= z for a, z in windows)]
            fitted = {}
            if len(quiet) >= need:
                fitted["volume"] = quality.fit("volume", [r["events"] for r in quiet], float(cfg["zFloor"]),
                                               float(cfg["maxFalsePositiveRate"]))
            if src["pack_id"] and src["pack_bound_at"]:
                bound = [r for r in quiet if r["bucket"] >= src["pack_bound_at"] and r["events"] >= 3 and r["fill"] is not None]
                if len(bound) >= need // 2:
                    fitted["fill"] = quality.fit("fill", [r["fill"] for r in bound], float(cfg["zFloor"]), float(cfg["maxFalsePositiveRate"]))
                    fitted["normalized"] = quality.fit("normalized", [r["normalized_ratio"] for r in bound],
                                                       float(cfg["zFloor"]), float(cfg["maxFalsePositiveRate"]))
            for metric, bl in fitted.items():
                self.store.execute("""INSERT INTO source_baselines (source_id, metric, median, scale, threshold, samples, fitted_at)
                                      VALUES (%s, %s, %s, %s, %s, %s, now())
                                      ON CONFLICT (source_id, metric) DO UPDATE SET median = EXCLUDED.median, scale = EXCLUDED.scale,
                                          threshold = EXCLUDED.threshold, samples = EXCLUDED.samples, fitted_at = now()""",
                                   (src["id"], metric, bl.median, bl.scale, bl.threshold, bl.samples))
        self.load_baselines()

    def load_baselines(self) -> None:
        self.baselines = {}
        for r in self.store.rows("SELECT * FROM source_baselines"):
            self.baselines.setdefault(r["source_id"], {})[r["metric"]] = quality.Baseline(
                r["metric"], r["median"], r["scale"], r["threshold"], r["samples"])

    # ── 3. detection ────────────────────────────────────────────────────────
    def open_incident(self, kind: str, source: str, severity: str, title: str, summary: str, evidence: dict,
                      onset: datetime | None) -> str:
        row = self.store.rows("""INSERT INTO ulpf_incidents (kind, source_id, severity, status, title, summary, evidence, onset_at)
                                 VALUES (%s, %s, %s, 'OPEN', %s, %s, %s, %s) RETURNING id, incident_key""",
                              (kind, source, severity, title, summary, json.dumps(evidence, default=str), onset))[0]
        self.timeline(row["id"], "DETECTED", {"kind": kind, "source": source, "evidence": evidence})
        self.audit("INCIDENT_OPENED", row["id"], row["id"], {"key": row["incident_key"], "kind": kind, "source": source})
        log.warning("%s %s opened for %s: %s", row["incident_key"], kind, source, title)
        return row["id"]

    def open_for(self, kind: str, source: str) -> dict | None:
        rows = self.store.rows("SELECT * FROM ulpf_incidents WHERE kind = %s AND source_id = %s AND status <> 'RESOLVED' "
                               "ORDER BY detected_at DESC LIMIT 1", (kind, source))
        return rows[0] if rows else None

    def detect(self, cfg: dict) -> None:
        b = int(cfg["bucketSeconds"])
        now = utcnow()
        end = now - timedelta(seconds=3)  # buckets still being written are not judged
        consecutive = int(cfg["consecutiveBuckets"])
        for src in self.store.rows("SELECT id, pack_id, skew_ms, last_seen FROM log_sources"):
            bl = self.baselines.get(src["id"], {})
            if not bl:
                continue
            rows = self.series(src["id"], end - timedelta(seconds=b * 40), end, b)
            # parser drift
            if "fill" in bl and not self.open_for("PARSER_DRIFT", src["id"]):
                bad, flags = quality.drifting(bl.get("fill"), bl.get("normalized"), rows, consecutive)
                if bad:
                    self._open_drift(src, bl, flags, b)
            # silence
            if "volume" in bl and bl["volume"].median >= 0.5 and not self.open_for("SOURCE_SILENT", src["id"]):
                k = quality.silence_buckets(bl["volume"].median)
                tail = rows[-k:]
                if len(tail) == k and all(r["events"] == 0 for r in tail):
                    self.open_incident(
                        "SOURCE_SILENT", src["id"], "HIGH", f"{src['id']} stopped sending logs",
                        f"No events for {k * b} s; this source normally sends {bl['volume'].median:.1f} events every {b} s. "
                        f"A Poisson source at that rate would stay silent this long with probability below 0.01%. "
                        f"A device that stops logging is a security signal and a CERT-In logging gap.",
                        {"lastEventAt": src["last_seen"], "expectedPerBucket": bl["volume"].median, "bucketSeconds": b,
                         "silentBuckets": k, "certIn": "mandatory log source not reporting"}, src["last_seen"])
            # clock skew
            skews = [r["skew_ms"] for r in rows[-3:] if r["skew_ms"] is not None and r["events"] >= 3]
            thr = float(cfg["skewThresholdSeconds"]) * 1000
            if len(skews) == 3 and all(abs(s) > thr for s in skews) and max(skews) - min(skews) < 10_000 \
                    and not self.open_for("CLOCK_SKEW", src["id"]):
                applied = src["skew_ms"]
                m = int(quality.median(skews))
                if applied is None or abs(applied - m) > thr:
                    ahead = m < 0
                    self.open_incident(
                        "CLOCK_SKEW", src["id"], "MEDIUM",
                        f"{src['id']} clock is {abs(m) / 1000:.0f} s {'ahead' if ahead else 'behind'}",
                        f"Events state times {abs(m) / 1000:.1f} s {'in the future' if ahead else 'in the past'} relative to "
                        f"their arrival, consistently over {3 * b} s. Correlation across sources and CERT-In NTP "
                        f"synchronisation are affected.",
                        {"medianOffsetMs": m, "bucketOffsetsMs": skews, "thresholdMs": thr}, now - timedelta(seconds=3 * b))

    def _open_drift(self, src: dict, bl: dict, flags: list[dict], b: int) -> None:
        first_bad = flags[0]["bucket"]
        cutoff = bl["fill"].median - bl["fill"].threshold * bl["fill"].scale
        onset = self.store.rows("SELECT uid, received_at FROM ulpf_events WHERE source_id = %s AND received_at >= %s "
                                "AND (fill < %s OR status <> 'NORMALIZED') ORDER BY received_at LIMIT 1",
                                (src["id"], first_bad - timedelta(seconds=b), cutoff))
        bad = self.store.rows("SELECT event FROM ulpf_events WHERE source_id = %s AND received_at >= %s AND fill < %s "
                              "ORDER BY received_at DESC LIMIT 50", (src["id"], first_bad, cutoff))
        good = self.store.rows("SELECT event FROM ulpf_events WHERE source_id = %s AND received_at < %s AND fill >= %s "
                               "ORDER BY received_at DESC LIMIT 50", (src["id"], first_bad - timedelta(seconds=b), bl["fill"].median))
        pack = packstore.registry(self.store).packs.get(src["pack_id"])
        expect = pack.expect if pack else []
        presence = []
        for path in expect:
            before = sum(get_path(r["event"], path) not in (None, "") for r in good) / max(len(good), 1)
            after = sum(get_path(r["event"], path) not in (None, "") for r in bad) / max(len(bad), 1)
            presence.append({"attribute": path, "before": round(before, 3), "after": round(after, 3)})
        old_unmapped = set().union(*[set((r["event"].get("unmapped") or {}).keys()) for r in good]) if good else set()
        new_unmapped = set().union(*[set((r["event"].get("unmapped") or {}).keys()) for r in bad]) if bad else set()
        fill_now = flags[-1]["fill"] or 0
        missing = [p["attribute"] for p in presence if p["after"] < 0.5 <= p["before"]]
        self.open_incident(
            "PARSER_DRIFT", src["id"], "HIGH" if fill_now < 0.5 else "MEDIUM",
            f"Parser drift on {src['id']}: {len(missing)} expected attribute(s) no longer extracted",
            f"Pack {src['pack_id']} now fills {fill_now:.0%} of its expected OCSF attributes (normally "
            f"{bl['fill'].median:.0%}; {flags[-1]['zFill']} robust SD below normal, threshold {bl['fill'].threshold:.1f}). "
            f"Missing: {', '.join(missing) or 'none'}. New fields in the events: {', '.join(sorted(new_unmapped - old_unmapped)[:12]) or 'none'}. "
            f"The raw events are intact in the vault.",
            {"pack": src["pack_id"], "buckets": flags, "attributes": presence,
             "newFields": sorted(new_unmapped - old_unmapped), "goneFields": sorted(old_unmapped - new_unmapped),
             "firstBadEvent": onset[0]["uid"] if onset else None, "baseline": vars(bl["fill"])},
            onset[0]["received_at"] if onset else first_bad)

    # ── 4. remediation ──────────────────────────────────────────────────────
    def actions(self, incident_id) -> list[dict]:
        return self.store.rows("SELECT * FROM ulpf_actions WHERE incident_id = %s ORDER BY created_at", (incident_id,))

    def new_action(self, incident: dict, action: str, tier: int, params: dict, policy: list[dict], cfg: dict) -> dict:
        hard_fail = [r for r in policy if not r["passed"] and r["kind"] == "safety"]
        autonomy_fail = [r for r in policy if not r["passed"] and r["kind"] == "autonomy"]
        if hard_fail:
            status = "BLOCKED"
        elif autonomy_fail:
            status = "PROPOSED"
        else:
            status = "APPROVED"
        row = self.store.rows("""INSERT INTO ulpf_actions (incident_id, action, tier, status, automatic, params, policy, decided_by)
                                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                              (incident["id"], action, tier, status, status == "APPROVED", json.dumps(params, default=str),
                               json.dumps(policy), ACTOR if status == "APPROVED" else None))[0]
        self.timeline(incident["id"], f"ACTION_{status}", {"action": action, "tier": tier, "id": row["id"],
                                                          "failedRules": [r["rule"] for r in hard_fail + autonomy_fail]})
        self.audit(f"ACTION_{status}", incident["id"], row["id"], {"action": action, "tier": tier, "params": params, "policy": policy})
        if status == "BLOCKED":
            self.escalate(incident, f"{action} blocked by safety rule(s): {', '.join(r['rule'] for r in hard_fail)}")
        return row

    def common_rules(self, tier: int, cfg: dict) -> list[dict]:
        kill = self.kill_switch()
        dry = bool(cfg.get("dryRun"))
        max_tier = int(cfg["autoExecuteMaxTier"])
        return [
            {"rule": "KILL_SWITCH_OFF", "kind": "autonomy", "passed": not kill,
             "detail": "global remediation kill switch is " + ("ON" if kill else "off")},
            {"rule": "NOT_DRY_RUN", "kind": "autonomy", "passed": not dry, "detail": "dry-run " + ("on" if dry else "off")},
            {"rule": "TIER_ALLOWED", "kind": "autonomy", "passed": tier <= max_tier,
             "detail": f"tier {tier} vs automatic up to tier {max_tier}"},
        ]

    def escalate(self, incident: dict, reason: str) -> None:
        self.store.execute("UPDATE ulpf_incidents SET status = 'ESCALATED', updated_at = now() WHERE id = %s", (incident["id"],))
        self.timeline(incident["id"], "ESCALATED", {"reason": reason})
        self.audit("INCIDENT_ESCALATED", incident["id"], incident["id"], {"reason": reason})

    def set_status(self, incident: dict, status: str, kind: str, payload: dict) -> None:
        col = {"MITIGATED": ", mitigated_at = now()", "RESOLVED": ", resolved_at = now()"}.get(status, "")
        self.store.execute(f"UPDATE ulpf_incidents SET status = %s, updated_at = now(){col} WHERE id = %s", (status, incident["id"]))
        self.timeline(incident["id"], kind, payload)
        self.audit(f"INCIDENT_{status}", incident["id"], incident["id"], payload)

    def raw_samples(self, source: str, where: str, params: tuple, limit: int = 120) -> list[str]:
        rows = self.store.rows("SELECT segment, vault_offset, event->'ulpf'->>'encoding' AS enc FROM ulpf_events "
                               f"WHERE source_id = %s AND {where} ORDER BY received_at DESC LIMIT {int(limit)}", (source, *params))
        out = []
        for r in rows:
            try:
                out.append(vault.read(VAULT_DIR, r["segment"], r["vault_offset"])["raw"].decode(r["enc"] or "utf-8", "replace"))
            except FileNotFoundError:
                continue
        return out

    def progress(self, cfg: dict) -> None:
        for inc in self.store.rows("SELECT * FROM ulpf_incidents WHERE status IN ('OPEN', 'MITIGATING', 'MITIGATED', 'ESCALATED') "
                                   "ORDER BY detected_at"):
            try:
                getattr(self, f"_progress_{inc['kind'].lower()}", lambda *_: None)(inc, cfg)
            except Exception as e:
                log.exception("incident %s: %s", inc["incident_key"], e)

    def _progress_parser_drift(self, inc: dict, cfg: dict) -> None:
        b = int(cfg["bucketSeconds"])
        acts = self.actions(inc["id"])
        source = inc["source_id"]
        if not acts and inc["status"] == "OPEN":
            reg = packstore.registry(self.store)
            champion = reg.packs.get(inc["evidence"].get("pack"))
            if champion is None:
                return self.escalate(inc, "the source has no champion pack to repair")
            bl = self.baselines.get(source, {}).get("fill")
            cutoff = (bl.median - bl.threshold * bl.scale) if bl else 0.75
            bad = self.raw_samples(source, "received_at >= %s AND fill < %s", (inc["onset_at"], cutoff))
            good = self.raw_samples(source, "received_at < %s AND fill >= %s", (inc["onset_at"], bl.median if bl else 1.0))
            now = utcnow()
            latest = self.store.rows("SELECT max(version) AS v FROM parser_packs WHERE id = %s", (champion.id,))[0]["v"]
            text, changes = repair.infer(champion, bad, now, next_version=(latest or champion.version) + 1)
            if text is None:
                self.timeline(inc["id"], "NO_AUTOMATIC_FIX", {"reason": "no field in the new events could be matched to the missing attributes"})
                return self.escalate(inc, "no automatic repair found; onboard the new format in Parser Studio")
            challenger = repair.packs.load(text)
            shadow = {"challenger": {"drifted": repair.evaluate(challenger, bad, now), "previous": repair.evaluate(challenger, good, now)},
                      "champion": {"drifted": repair.evaluate(champion, bad, now), "previous": repair.evaluate(champion, good, now)}}
            packstore.save(self.store, text, status="SHADOW", origin="REPAIR", created_by=ACTOR,
                           notes=f"challenger for {inc['incident_key']}", test=shadow["challenger"]["drifted"])
            self.timeline(inc["id"], "CHALLENGER_TESTED", {"pack": challenger.ref, "changes": changes, "shadow": shadow})
            recent_promotions = self.store.rows(
                "SELECT count(*) AS n FROM ulpf_actions WHERE action = 'PROMOTE_PACK' AND status IN ('VERIFIED','VERIFYING','DONE') "
                "AND params->>'pack' = %s AND executed_at > now() - make_interval(mins => %s)",
                (challenger.id, int(cfg["promotionCooldownMinutes"])))[0]["n"]
            ch, cp = shadow["challenger"], shadow["champion"]
            policy = self.common_rules(1, cfg) + [
                {"rule": "FIXES_THE_DRIFT", "kind": "safety", "passed": ch["drifted"]["fill"] >= 0.99,
                 "detail": f"challenger fill on drifted events {ch['drifted']['fill']:.0%} (champion {cp['drifted']['fill']:.0%})"},
                {"rule": "NO_REGRESSION", "kind": "safety",
                 "passed": not good or ch["previous"]["fill"] >= cp["previous"]["fill"] - 0.001,
                 "detail": f"on events before the change: challenger {ch['previous']['fill']:.0%} vs champion {cp['previous']['fill']:.0%}"},
                {"rule": "LOSSLESS", "kind": "safety", "passed": ch["drifted"]["lossless"] == 1.0 and (not good or ch["previous"]["lossless"] == 1.0),
                 "detail": "every test event rebuilt byte-for-byte"},
                {"rule": "ENOUGH_EVIDENCE", "kind": "safety", "passed": len(bad) >= 10,
                 "detail": f"{len(bad)} drifted events tested"},
                {"rule": "COOLDOWN", "kind": "safety", "passed": recent_promotions == 0,
                 "detail": f"{recent_promotions} promotion(s) of this pack in the last {cfg['promotionCooldownMinutes']} min"},
                {"rule": "REVERSIBLE", "kind": "safety", "passed": True, "detail": f"rollback target {champion.ref}"},
            ]
            self.new_action(inc, "PROMOTE_PACK", 1, {"pack": challenger.id, "toVersion": challenger.version,
                                                     "fromVersion": champion.version, "changes": changes, "shadow": shadow},
                            policy, cfg)
            return
        for act in acts:
            if act["action"] == "PROMOTE_PACK":
                self._drive_promotion(inc, act, cfg, b)
            elif act["action"] == "RENORMALIZE":
                self._drive_renormalize(inc, act)

    def _drive_promotion(self, inc: dict, act: dict, cfg: dict, b: int) -> None:
        p = act["params"]
        if act["status"] == "APPROVED":
            packstore.activate(self.store, p["pack"], p["toVersion"])
            self.store.execute("UPDATE ulpf_actions SET status = 'VERIFYING', executed_at = now() WHERE id = %s", (act["id"],))
            self.set_status(inc, "MITIGATING", "PACK_PROMOTED", {"pack": f"{p['pack']}@{p['toVersion']}",
                                                                 "previous": f"{p['pack']}@{p['fromVersion']}"})
            return
        if act["status"] != "VERIFYING":
            return
        settle = act["executed_at"] + timedelta(seconds=RELOAD_S)
        now = utcnow()
        rows = [r for r in self.series(inc["source_id"], settle, now - timedelta(seconds=3), b)]
        bl = self.baselines.get(inc["source_id"], {})
        if quality.healthy(bl.get("fill"), bl.get("normalized"), rows, 2):
            result = {"buckets": [{"bucket": r["bucket"], "events": r["events"], "fill": r["fill"]} for r in rows[-2:]]}
            self.store.execute("UPDATE ulpf_actions SET status = 'VERIFIED', finished_at = now(), result = %s WHERE id = %s",
                               (json.dumps(result, default=str), act["id"]))
            self.audit("ACTION_VERIFIED", inc["id"], act["id"], result)
            self.set_status(inc, "MITIGATED", "VERIFIED", {"action": "PROMOTE_PACK", **result})
            policy = self.common_rules(1, cfg) + [{"rule": "RAW_EVENTS_IN_VAULT", "kind": "safety", "passed": True,
                                                   "detail": "re-normalization reads the original bytes; old records are versioned"}]
            self.new_action(inc, "RENORMALIZE", 1, {"source": inc["source_id"], "from": inc["onset_at"] - timedelta(seconds=b),
                                                    "to": act["executed_at"] + timedelta(seconds=RELOAD_S + b)}, policy, cfg)
        elif (now - act["executed_at"]).total_seconds() > VERIFY_TIMEOUT_S:
            packstore.activate(self.store, p["pack"], p["fromVersion"])
            self.store.execute("UPDATE ulpf_actions SET status = 'ROLLED_BACK', finished_at = now(), result = %s WHERE id = %s",
                               (json.dumps({"reason": "quality did not return to normal after promotion"}), act["id"]))
            self.audit("ACTION_ROLLED_BACK", inc["id"], act["id"], {"restored": f"{p['pack']}@{p['fromVersion']}"})
            self.timeline(inc["id"], "ROLLED_BACK", {"restored": f"{p['pack']}@{p['fromVersion']}"})
            self.escalate(inc, "the repaired pack did not restore quality on live events; rolled back")

    def _drive_renormalize(self, inc: dict, act: dict) -> None:
        p = act["params"]
        if act["status"] == "APPROVED":
            job = self.store.rows("""INSERT INTO renormalization_jobs (source_id, incident_id, pack, window_from, window_to,
                                     only_degraded, status, requested_by) VALUES (%s, %s, %s, %s, %s, true, 'PENDING', %s) RETURNING id""",
                                  (p["source"], inc["id"], None, p["from"], p["to"], ACTOR))[0]
            self.store.execute("UPDATE ulpf_actions SET status = 'EXECUTING', executed_at = now(), params = params || %s::jsonb WHERE id = %s",
                               (json.dumps({"job": str(job["id"])}), act["id"]))
            self.timeline(inc["id"], "RENORMALIZATION_STARTED", {"job": job["id"], "from": p["from"], "to": p["to"]})
            return
        if act["status"] == "EXECUTING":
            job = self.store.rows("SELECT * FROM renormalization_jobs WHERE id = %s", (p["job"],))
            if job and job[0]["status"] in ("DONE", "FAILED"):
                j = job[0]
                result = {k: j[k] for k in ("processed", "changed", "improved", "fields_recovered", "lossless_ok", "status", "error")}
                self.store.execute("UPDATE ulpf_actions SET status = %s, finished_at = now(), result = %s WHERE id = %s",
                                   ("DONE" if j["status"] == "DONE" else "FAILED", json.dumps(result), act["id"]))
                self.audit("RENORMALIZATION_" + j["status"], inc["id"], act["id"], result)
                if j["status"] == "DONE":
                    self.set_status(inc, "RESOLVED", "RESOLVED", {"renormalization": result})
                else:
                    self.escalate(inc, f"re-normalization failed: {j['error']}")

    def _progress_source_silent(self, inc: dict, cfg: dict) -> None:
        b = int(cfg["bucketSeconds"])
        bl = self.baselines.get(inc["source_id"], {}).get("volume")
        now = utcnow()
        rows = self.series(inc["source_id"], now - timedelta(seconds=b * 4), now - timedelta(seconds=3), b)
        if bl and len(rows) >= 2 and all(r["events"] >= max(1.0, 0.3 * bl.median) for r in rows[-2:]):
            back = self.store.rows("SELECT min(received_at) AS t FROM ulpf_events WHERE source_id = %s AND received_at > %s",
                                   (inc["source_id"], inc["detected_at"] - timedelta(seconds=b * quality.silence_buckets(bl.median))))
            self.set_status(inc, "RESOLVED", "TRAFFIC_RESUMED", {"firstEventAfterSilence": back[0]["t"] if back else None,
                                                                 "events": [r["events"] for r in rows[-2:]]})
        elif inc["status"] == "OPEN":
            self.store.execute("UPDATE ulpf_incidents SET status = 'ESCALATED', updated_at = now() WHERE id = %s", (inc["id"],))
            self.timeline(inc["id"], "ESCALATED", {"reason": "the device side must be checked (power, link, logging config); "
                                                             "no safe automatic action exists from the collector"})

    def _progress_clock_skew(self, inc: dict, cfg: dict) -> None:
        b = int(cfg["bucketSeconds"])
        thr = float(cfg["skewThresholdSeconds"]) * 1000
        acts = self.actions(inc["id"])
        source = inc["source_id"]
        now = utcnow()
        if not acts:
            m = int(inc["evidence"]["medianOffsetMs"])
            policy = self.common_rules(1, cfg) + [
                {"rule": "CONSISTENT_OFFSET", "kind": "safety", "passed": max(inc["evidence"]["bucketOffsetsMs"]) -
                 min(inc["evidence"]["bucketOffsetsMs"]) < 10_000, "detail": f"offsets {inc['evidence']['bucketOffsetsMs']} ms"},
                {"rule": "ORIGINAL_TIME_KEPT", "kind": "safety", "passed": True,
                 "detail": "the device's own timestamp stays in metadata.original_time and the raw event"},
            ]
            self.new_action(inc, "CLOCK_CORRECTION", 1, {"source": source, "skewMs": m}, policy, cfg)
            return
        act = acts[0]
        if act["status"] == "APPROVED":
            self.store.execute("UPDATE log_sources SET skew_ms = %s, skew_applied_at = now() WHERE id = %s",
                               (act["params"]["skewMs"], source))
            self.store.execute("UPDATE ulpf_actions SET status = 'VERIFYING', executed_at = now() WHERE id = %s", (act["id"],))
            self.set_status(inc, "MITIGATING", "CLOCK_CORRECTION_APPLIED", {"skewMs": act["params"]["skewMs"]})
        elif act["status"] == "VERIFYING" and now - act["executed_at"] > timedelta(seconds=RELOAD_S + b):
            r = self.store.rows("""SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM received_at - event_time) * 1000) AS m,
                                          count(*) AS n FROM ulpf_events WHERE source_id = %s AND received_at > %s AND device_time IS NOT NULL""",
                                (source, act["executed_at"] + timedelta(seconds=RELOAD_S)))[0]
            if r["n"] and r["n"] >= 3 and abs(r["m"]) < 2000:
                self.store.execute("UPDATE ulpf_actions SET status = 'VERIFIED', finished_at = now(), result = %s WHERE id = %s",
                                   (json.dumps({"residualOffsetMs": round(r["m"]), "events": r["n"]}), act["id"]))
                self.set_status(inc, "MITIGATED", "VERIFIED", {"residualOffsetMs": round(r["m"]), "events": r["n"],
                                                               "note": "times corrected; the device's NTP still needs fixing"})
            elif now - act["executed_at"] > timedelta(seconds=VERIFY_TIMEOUT_S):
                self.store.execute("UPDATE log_sources SET skew_ms = NULL WHERE id = %s", (source,))
                self.store.execute("UPDATE ulpf_actions SET status = 'ROLLED_BACK', finished_at = now() WHERE id = %s", (act["id"],))
                self.escalate(inc, "correction did not align event times; removed")
        elif act["status"] == "VERIFIED":
            rows = self.series(source, now - timedelta(seconds=b * 4), now - timedelta(seconds=3), b)
            recent = [r["skew_ms"] for r in rows[-3:] if r["skew_ms"] is not None]
            if len(recent) == 3 and all(abs(s) < thr for s in recent):
                self.store.execute("UPDATE log_sources SET skew_ms = NULL, skew_applied_at = NULL WHERE id = %s", (source,))
                self.set_status(inc, "RESOLVED", "DEVICE_CLOCK_FIXED", {"offsetsMs": recent, "correction": "removed"})

    # ── 5. jobs (background thread: detection never waits for a long re-normalization) ──
    def run_jobs(self) -> None:
        import threading
        if getattr(self, "_job_thread", None) is not None and self._job_thread.is_alive():
            return
        self._job_thread = threading.Thread(target=self._run_one_job, daemon=True, name="renormalize")
        self._job_thread.start()

    def _run_one_job(self) -> None:
        store = Store()
        job = store.rows("UPDATE renormalization_jobs SET status = 'RUNNING', started_at = now() WHERE id = "
                              "(SELECT id FROM renormalization_jobs WHERE status = 'PENDING' ORDER BY created_at LIMIT 1) RETURNING *")
        if not job:
            return
        j = job[0]
        change = self._maintenance_start(store, j)
        try:
            counts = renormalize.run(store, j, packstore.registry(store), VAULT_DIR)
            store.execute("UPDATE renormalization_jobs SET status = 'DONE', finished_at = now() WHERE id = %s", (j["id"],))
            store.execute("INSERT INTO audit_log (actor, action, entity_type, entity_id, incident_id, detail) "
                          "VALUES (%s, 'RENORMALIZED', 'ulpf', %s, %s, %s)",
                          (ACTOR, j["id"], j["incident_id"], json.dumps({"source": j["source_id"], **counts})))
            log.info("re-normalization %s done: %s", j["id"], counts)
        except Exception as e:
            log.exception("re-normalization %s failed", j["id"])
            store.execute("UPDATE renormalization_jobs SET status = 'FAILED', finished_at = now(), error = %s WHERE id = %s",
                               (str(e)[:500], j["id"]))
        finally:
            if change:
                store.execute("UPDATE change_events SET ended_at = now() WHERE reference_id = %s AND source = 'ulpf-monitor'",
                              (j["id"],))

    @staticmethod
    def _maintenance_start(store: Store, job: dict):
        """Bulk database work is announced to CausalOps as a MAINTENANCE change on every environment, so an
        incident during it is marked change-correlated and calibration leaves the window out."""
        try:
            first = None
            for env in store.rows("SELECT id FROM environments"):
                row = store.rows("""INSERT INTO change_events (environment_id, kind, target, started_at, source, description, reference_id)
                                    VALUES (%s, 'MAINTENANCE', 'postgres', now(), 'ulpf-monitor', %s, %s) RETURNING id""",
                                 (env["id"], f"ULPF re-normalization of {job['source_id']} (bulk reads/writes on the shared database)",
                                  job["id"]))
                first = first or row[0]["id"]
            return first
        except Exception as e:
            log.warning("could not record the maintenance window: %s", e)
            return None

    # ── loop ────────────────────────────────────────────────────────────────
    def tick(self) -> None:
        cfg = self.settings()
        self.aggregate(int(cfg["bucketSeconds"]))
        if time.time() - self.last_calibration > 60:
            self.calibrate(cfg)
            self.last_calibration = time.time()
        self.detect(cfg)
        self.progress(cfg)
        self.run_jobs()


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    m = Monitor()
    while True:
        try:  # quality history for the last 6 h, so a restarted monitor calibrates from real buckets
            m.aggregate(int(m.settings()["bucketSeconds"]), 6 * 3600 // int(m.settings()["bucketSeconds"]))
            # A job interrupted by a restart is resumed (re-normalization is idempotent).
            m.store.execute("UPDATE renormalization_jobs SET status = 'PENDING' WHERE status = 'RUNNING'")
            break
        except Exception as e:
            log.info("waiting for the database: %s", e)
            time.sleep(5)
    while True:
        try:
            m.tick()
        except Exception as e:
            log.exception("monitor tick failed: %s", e)
            m.store._conn = None
        time.sleep(float(os.environ.get("ULPF_MONITOR_INTERVAL", "5")))


if __name__ == "__main__":
    main()
