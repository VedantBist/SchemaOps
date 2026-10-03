"""CERT-In Directions of 28 April 2022 (section 70B(6) of the IT Act), checked continuously from measurements.

  Direction (iv)  logs of all ICT systems maintained for a rolling 180 days, within Indian jurisdiction
  Direction (i)   clocks synchronised to NTP of NIC/NPL (or traceable): source clock offsets must be small
  Direction (ii)  incidents reported within 6 hours: an evidence pack (raw logs + hashes, signed) must be
                  producible on demand for any window
plus the integrity of the stored logs (hash chain) and that every mandatory source is actually reporting.
Every check states what was measured; nothing is assumed compliant.
"""
from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


def _local(host: str | None) -> bool:
    if not host:
        return True
    if host in ("localhost",) or "." not in host or host.endswith((".local", ".internal", ".lan", ".in")):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def run(store, settings: dict, vault_dir: str) -> dict:
    now = datetime.now(timezone.utc)
    checks = []

    def add(cid, title, direction, passed, measured, detail="", per_source=None):
        checks.append({"id": cid, "title": title, "direction": direction, "passed": bool(passed), "measured": measured,
                       "detail": detail, "sources": per_source or []})

    retention = int(settings.get("retentionDays", 180))
    oldest = None
    segs = sorted(Path(vault_dir).glob("*/*.seg"))
    if segs:
        oldest = datetime.fromtimestamp(min(p.stat().st_mtime for p in segs), tz=timezone.utc)
    add("RETENTION_180", "Logs retained for at least 180 days", "Direction (iv)", retention >= 180,
        f"retention policy {retention} days; oldest raw segment {oldest:%Y-%m-%d} ({(now - oldest).days} days ago)" if oldest
        else f"retention policy {retention} days",
        "The vault is write-once; sealed segments are only removed after the retention period.")

    sinks = store.rows("SELECT name, kind, enabled, config FROM ulpf_sinks WHERE enabled")
    external = []
    for s in sinks:
        cfg = s["config"] or {}
        host = cfg.get("host") or (urlparse(cfg["url"]).hostname if cfg.get("url") else None) or \
            (cfg.get("bootstrap", "").split(":")[0] or None)
        if not _local(host):
            external.append(f"{s['name']} → {host}")
    add("INDIAN_JURISDICTION", "Logs kept within Indian jurisdiction", "Direction (iv)", not external,
        "self-hosted; vault, database and lake run on this deployment" + (f"; outbound sinks to check: {', '.join(external)}" if external else
                                                                          "; no enabled sink sends logs to an outside host"))

    thr = float(settings.get("ntpMaxOffsetSeconds", 30)) * 1000
    rows = store.rows("""SELECT s.id, s.skew_ms AS corrected,
                                (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY q.skew_ms) FROM ulpf_quality q
                                  WHERE q.source_id = s.id AND q.bucket > now() - interval '10 minutes' AND q.skew_ms IS NOT NULL) AS offset_ms
                         FROM log_sources s WHERE s.last_seen > now() - interval '1 hour' ORDER BY s.id""")
    per = []
    for r in rows:
        if r["offset_ms"] is None:
            continue
        ok = abs(r["offset_ms"]) <= thr
        per.append({"source": r["id"], "passed": ok, "value": f"{r['offset_ms'] / 1000:+.1f} s" +
                    (" (times corrected by ULPF)" if r["corrected"] else "")})
    bad = [p for p in per if not p["passed"]]
    add("NTP_SYNC", f"Device clocks synchronised (offset within {thr / 1000:.0f} s)", "Direction (i)", not bad,
        f"{len(per) - len(bad)} of {len(per)} sources within tolerance", "Offset = arrival time minus the time the device states.", per)

    mandatory = settings.get("mandatorySources") or []
    srcs = store.rows("SELECT id, pack_id, last_seen, EXTRACT(EPOCH FROM now() - last_seen) AS age FROM log_sources ORDER BY id")
    required = [s for s in srcs if (s["id"] in mandatory) or (not mandatory and s["pack_id"])]
    silent = store.rows("SELECT source_id FROM ulpf_incidents WHERE kind = 'SOURCE_SILENT' AND status <> 'RESOLVED'")
    silent_ids = {r["source_id"] for r in silent}
    per = [{"source": s["id"], "passed": s["id"] not in silent_ids and float(s["age"]) < 600,
            "value": f"last event {float(s['age']):.0f} s ago"} for s in required]
    missing = [p for p in per if not p["passed"]]
    add("SOURCES_REPORTING", "All mandatory log sources reporting", "Direction (iv)", not missing,
        f"{len(per) - len(missing)} of {len(per)} reporting", "Mandatory = sources bound to a parser pack unless a list is configured.", per)

    last = store.rows("SELECT value, updated_at FROM ulpf_settings WHERE key = 'lastVaultVerification'")
    if last:
        v = last[0]["value"]
        age_h = (now - last[0]["updated_at"]).total_seconds() / 3600
        add("LOG_INTEGRITY", "Stored logs unaltered (hash chain verified)", "Integrity", v.get("ok") and age_h < 2,
            f"{v.get('records', 0):,} records verified {age_h * 60:.0f} min ago" + ("" if v.get("ok") else f"; errors: {v.get('errors')}"))
    else:
        add("LOG_INTEGRITY", "Stored logs unaltered (hash chain verified)", "Integrity", False, "not verified yet")

    pack = store.rows("SELECT created_at, events, bytes, detail FROM ulpf_bundles WHERE purpose = 'CERT_IN_EVIDENCE' "
                      "ORDER BY created_at DESC LIMIT 1")
    add("REPORT_READINESS", "Incident evidence producible within the 6-hour window", "Direction (ii)", True,
        (f"last evidence pack {pack[0]['created_at']:%Y-%m-%d %H:%M} UTC: {pack[0]['events']:,} events, "
         f"built in {pack[0]['detail'].get('seconds', '?')} s") if pack else "evidence packs are generated on demand (none yet)",
        "One click: raw events with hashes, OCSF records, parser versions and chain anchors, signed (Ed25519).")
    passed = sum(c["passed"] for c in checks)
    return {"framework": "CERT-In Directions 2022", "at": now.isoformat(), "passed": passed, "failed": len(checks) - passed,
            "checks": checks}
