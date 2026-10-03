"""Historical re-normalization ("time travel"): re-run today's champion pack over stored raw events.

The raw bytes come back from the vault, so a parser fix also repairs every event that was parsed badly
before it. The previous normalized record is kept in ulpf_event_versions (revision history, "as parsed
by v1 vs v2"), the event row gets the new record, and the job counts what changed and what improved.
Every rebuilt record goes through the same lossless proof as live events.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

from . import vault
from .entities import Batch, identifiers
from .ocsf import get_path
from .pipeline import process
from .store import Store

log = logging.getLogger("ulpf.renormalize")
# Bulk re-normalization shares the database with live traffic (and, in the demo stack, with the reference
# system's own database): it is throttled so it stays a background load.
RATE = float(os.environ.get("ULPF_RENORM_RATE", "250"))  # events per second
RANK = {"QUARANTINED": 0, "PARTIAL": 1, "NORMALIZED": 2}


def _mapped(event: dict) -> int:
    return sum(1 for loc in (event.get("ulpf", {}).get("fields") or {}).values() if not loc.startswith("unmapped:"))


def run(store: Store, job: dict, registry, vault_dir: str) -> dict:
    counts = {"processed": 0, "changed": 0, "improved": 0, "fields_recovered": 0, "lossless_ok": 0}
    cursor_time, cursor_uid = datetime(1970, 1, 1, tzinfo=timezone.utc), ""
    while True:
        where = ["source_id = %s", "(received_at, uid) > (%s, %s)"]
        params: list = [job["source_id"], cursor_time, cursor_uid]
        if job.get("window_from"):
            where.append("received_at >= %s"); params.append(job["window_from"])
        if job.get("window_to"):
            where.append("received_at <= %s"); params.append(job["window_to"])
        if job.get("only_degraded"):
            where.append("(status <> 'NORMALIZED' OR fill IS NULL OR fill < 1)")
        rows = store.rows("SELECT uid, source_id, received_at, segment, vault_offset, status, fill, parser, revision, event "
                          "FROM ulpf_events WHERE " + " AND ".join(where) + " ORDER BY received_at, uid LIMIT 300",
                          tuple(params))
        if not rows:
            break
        cursor_time, cursor_uid = rows[-1]["received_at"], rows[-1]["uid"]
        batch_started = time.time()
        updates, versions, graph = [], [], Batch()
        for row in rows:
            old = row["event"]
            try:
                raw = vault.read(vault_dir, row["segment"], row["vault_offset"])["raw"]
            except FileNotFoundError:
                continue
            peer = (old.get("device") or {}).get("ip")
            res = process(raw, received_at=row["received_at"], peer=peer, source_hint=None, registry=registry)
            counts["processed"] += 1
            counts["lossless_ok"] += int(res.lossless)
            new = res.event
            new["ulpf"]["uid"] = row["uid"]
            new["ulpf"]["vault"] = old.get("ulpf", {}).get("vault")
            new["ulpf"]["source_id"] = row.get("source_id") or old["ulpf"].get("source_id")
            parser = f"{res.parsed.parser}@{res.parsed.parser_version}"[:80]
            if parser == row["parser"] and res.status == row["status"] and (res.fill or 0) == (row["fill"] or 0):
                continue
            counts["changed"] += 1
            gained = _mapped(new) - _mapped(old)
            if RANK[res.status] > RANK[row["status"]] or (res.fill or 0) > (row["fill"] or 0):
                counts["improved"] += 1
            counts["fields_recovered"] += max(gained, 0)
            new["ulpf"]["revision"] = row["revision"] + 1
            new["ulpf"]["renormalized"] = {"job": str(job["id"]), "previous": row["parser"],
                                           "at": datetime.now(timezone.utc).isoformat()}
            versions.append((row["uid"], row["revision"], row["parser"], row["status"], row["fill"],
                             json.dumps(old, separators=(",", ":")), str(job["id"])))
            src = (new.get("src_endpoint") or {}).get("ip")
            dst = (new.get("dst_endpoint") or {}).get("ip")
            user = ((new.get("actor") or {}).get("user") or {}).get("name")
            msg = new.get("message") or (new.get("finding_info") or {}).get("title")
            updates.append((json.dumps(new, separators=(",", ":")), res.status, res.fill, parser, new["class_uid"],
                            src, dst, (user or "")[:200] or None, new.get("action"), (msg or "")[:500] or None,
                            res.lossless, row["uid"]))
            # Only identifiers the old record did not have are added to the entity graph.
            before = {n[0] for n in identifiers(old)[0]}
            if {n[0] for n in identifiers(new)[0]} - before:
                graph.add(_only_new(new, before), new["ulpf"]["source_id"], row["received_at"])
        if updates:
            with store.transaction() as cur:
                cur.executemany("""INSERT INTO ulpf_event_versions (uid, revision, parser, status, fill, event, job_id)
                                   VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""", versions)
                cur.executemany("""UPDATE ulpf_events SET event = %s, status = %s, fill = %s, parser = %s, class_uid = %s,
                                   src_ip = %s, dst_ip = %s, user_name = %s, action = %s, message = %s, lossless = %s,
                                   revision = revision + 1 WHERE uid = %s""", updates)
                try:
                    with cur.connection.transaction():
                        graph.flush(cur)
                except Exception as e:
                    log.warning("entity graph update skipped: %s", e)
        if RATE > 0:
            time.sleep(max(0.0, len(rows) / RATE - (time.time() - batch_started)))
        store.execute("UPDATE renormalization_jobs SET processed = %s, changed = %s, improved = %s, fields_recovered = %s, "
                      "lossless_ok = %s WHERE id = %s", (counts["processed"], counts["changed"], counts["improved"],
                                                         counts["fields_recovered"], counts["lossless_ok"], job["id"]))
    reconcile_source(store, job["source_id"])
    return counts


def _only_new(event: dict, known: set[str]) -> dict:
    """A copy of the event reduced to identifiers not yet counted (so entity counts are not doubled)."""
    out = {}
    for ep in ("src_endpoint", "dst_endpoint"):
        node = event.get(ep) or {}
        kept = {}
        for kind, key in (("ip", "ip"), ("host", "hostname"), ("mac", "mac")):
            v = node.get(key)
            k = f"{kind}:{v.lower() if kind != 'ip' else v}" if v else None
            if v and k not in known:
                kept[key] = v
        if kept:
            kept["is_private"] = node.get("is_private")
            if node.get("location"):
                kept["location"] = node["location"]
            out[ep] = kept
    user = get_path(event, "actor.user.name")
    if user and f"user:{user.lower()}" not in known:
        out["actor"] = {"user": {"name": user}}
        if "src_endpoint" not in out and get_path(event, "src_endpoint.ip"):
            out["src_endpoint"] = {"ip": get_path(event, "src_endpoint.ip")}
    return out


def reconcile_source(store: Store, source_id: str) -> None:
    store.execute("""
        UPDATE log_sources s SET normalized = c.normalized, partial = c.partial, quarantined = c.quarantined,
               lossless_ok = c.lossless_ok, fill_sum = c.fill_sum, fill_events = c.fill_events
        FROM (SELECT count(*) AS events, count(*) FILTER (WHERE status = 'NORMALIZED') AS normalized,
                     count(*) FILTER (WHERE status = 'PARTIAL') AS partial,
                     count(*) FILTER (WHERE status = 'QUARANTINED') AS quarantined,
                     count(*) FILTER (WHERE lossless) AS lossless_ok,
                     COALESCE(sum(fill), 0) AS fill_sum, count(fill) AS fill_events
              FROM ulpf_events WHERE source_id = %s) c
        WHERE s.id = %s""", (source_id, source_id))
