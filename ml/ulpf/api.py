"""ULPF service: syslog/HTTP intake plus the read API used by the console (through causalops-api)."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import redis
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from . import collector, entities, lossless, packs, packstore, studio, vault
from .pipeline import process
from .store import Store

log = logging.getLogger("ulpf.api")
VAULT_DIR = os.environ.get("ULPF_VAULT_DIR", "/var/lib/ulpf/vault")
REDIS_URL = os.environ.get("ULPF_REDIS_URL", "redis://redis:6379/0")
store = Store()
sync_redis = redis.Redis.from_url(REDIS_URL)
state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    r = aioredis.from_url(REDIS_URL)
    state["intake"] = collector.Intake(r)
    if os.environ.get("ULPF_SYSLOG_ENABLED", "true") == "true":
        state["servers"] = await collector.start(state["intake"])
    else:
        import asyncio
        asyncio.create_task(state["intake"].flush_loop())
    import asyncio
    asyncio.create_task(_seed_packs())
    yield
    for s in state.get("servers", []):
        s.close()


async def _seed_packs():
    """Bundled packs go into the database once the schema exists (Flyway runs in causalops-api)."""
    import asyncio
    for _ in range(120):
        try:
            added = packstore.seed_builtin(store)
            log.info("parser packs seeded (%d new)", added)
            return
        except Exception as e:
            log.info("waiting for the ULPF schema: %s", e)
            await asyncio.sleep(5)


app = FastAPI(title="CausalOps ULPF", version="1.0.0", lifespan=lifespan)


def clean(value):
    """JSON-safe conversion for rows (datetimes, decimals, inet)."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    from decimal import Decimal
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


@app.get("/health")
def health():
    return {"status": "UP"}


@app.get("/ready")
def ready():
    try:
        sync_redis.ping()
        store.rows("SELECT 1")
    except Exception as e:
        raise HTTPException(503, f"not ready: {e}")
    return {"status": "READY"}


# ── intake over HTTP ──────────────────────────────────────────────────────────
@app.post("/ingest")
async def ingest(request: Request):
    """Body: newline-separated raw events, or a JSON array of strings. Header X-Source names the source."""
    body = await request.body()
    hint = request.headers.get("x-source")
    peer = request.client.host if request.client else None
    lines: list[bytes]
    if request.headers.get("content-type", "").startswith("application/json") and body.strip().startswith(b"["):
        lines = [str(x).encode() for x in json.loads(body)]
    else:
        lines = [ln for ln in body.split(b"\n") if ln.strip()]
    for ln in lines:
        await state["intake"].put(ln, peer, "http", hint)
    return {"accepted": len(lines)}


# ── pipeline status and conservation ─────────────────────────────────────────
@app.get("/stats")
def stats():
    # One atomic Redis snapshot: intake counter, stored (acknowledged) counter and queue length.
    snap = sync_redis.pipeline(transaction=True)
    snap.hgetall("ulpf:received")
    snap.xlen("ulpf:raw")
    received_raw, backlog = snap.execute()
    received = {k.decode(): int(v) for k, v in received_raw.items()}
    dropped = {k.decode(): int(v) for k, v in sync_redis.hgetall("ulpf:dropped").items()}
    processed = {k.decode(): int(v) for k, v in sync_redis.hgetall("ulpf:processed").items()}
    beats = {k.decode(): float(v) for k, v in sync_redis.hgetall("ulpf:workers").items()}
    totals = store.rows("""SELECT COALESCE(SUM(events),0) AS stored, COALESCE(SUM(normalized),0) AS normalized,
                                  COALESCE(SUM(partial),0) AS partial, COALESCE(SUM(quarantined),0) AS quarantined,
                                  COALESCE(SUM(lossless_ok),0) AS lossless, COALESCE(SUM(bytes),0) AS bytes,
                                  COUNT(*) AS sources FROM log_sources""")[0]
    rate = store.rows("""SELECT COUNT(*) AS n FROM ulpf_events WHERE received_at > now() - interval '60 seconds'""")[0]["n"]
    now = time.time()
    total_received = received.get("total", 0)
    acked = received.get("stored", 0)
    stored = int(totals["stored"])
    return clean({
        "received": total_received, "receivedByTransport": {k: v for k, v in received.items() if k not in ("total", "stored")},
        "stored": stored, "normalized": int(totals["normalized"]), "partial": int(totals["partial"]),
        "quarantined": int(totals["quarantined"]), "losslessVerified": int(totals["lossless"]),
        "bytes": int(totals["bytes"]), "sources": int(totals["sources"]), "backlog": backlog,
        "dropped": dropped, "eventsPerSecond": round(rate / 60.0, 2),
        # Conservation: everything received is either stored or still queued in the stream.
        "conservation": {"received": total_received, "stored": acked, "inFlight": backlog,
                         "unaccounted": total_received - acked - backlog,
                         "balanced": total_received == acked + backlog,
                         "storedInDatabase": stored},
        "workers": [{"name": n, "lastHeartbeatSecondsAgo": round(now - t, 1), "alive": now - t < 15,
                     "processed": processed.get(n, 0)} for n, t in sorted(beats.items())
                    if now - t < 600],  # replaced containers drop off after 10 minutes
    })


@app.get("/sources")
def sources():
    rows = store.rows("""SELECT id, host, vendor, product, format, first_seen, last_seen, last_peer, transport, events,
                                normalized, partial, quarantined, lossless_ok, bytes, pack_id, pack_bound_at,
                                CASE WHEN fill_events > 0 THEN fill_sum / fill_events END AS avg_fill,
                                EXTRACT(EPOCH FROM now() - last_seen) AS silent_seconds
                         FROM log_sources ORDER BY events DESC""")
    for r in rows:
        n = max(int(r["events"]), 1)
        avg = r.pop("avg_fill")
        r["fillPct"] = round(100 * float(avg), 2) if avg is not None else None
        r["normalizedPct"] = round(100 * int(r["normalized"]) / n, 2)
        r["losslessPct"] = round(100 * int(r["lossless_ok"]) / n, 2)
    return clean(rows)


# ── events and lineage ───────────────────────────────────────────────────────
@app.get("/events")
def events(source: str | None = None, classUid: int | None = None, status: str | None = None,
           ip: str | None = None, user: str | None = None, q: str | None = None, minutes: int | None = None,
           before: str | None = None, limit: int = Query(100, le=500)):
    where, params = [], []
    if source:
        where.append("source_id = %s"); params.append(source)
    if classUid is not None:
        where.append("class_uid = %s"); params.append(classUid)
    if status:
        where.append("status = %s"); params.append(status)
    if ip:
        where.append("(src_ip = %s::inet OR dst_ip = %s::inet)"); params += [ip, ip]
    if user:
        where.append("user_name = %s"); params.append(user)
    if q:
        where.append("message ILIKE %s"); params.append(f"%{q}%")
    if minutes:
        where.append("received_at > now() - make_interval(mins => %s)"); params.append(minutes)
    if before:
        where.append("received_at < %s::timestamptz"); params.append(before)
    sql = ("SELECT uid, source_id, received_at, event_time, class_uid, event->>'class_name' AS class_name, status, format, "
           "product, host(src_ip) AS src_ip, host(dst_ip) AS dst_ip, user_name, action, message, lossless, parser "
           "FROM ulpf_events" + (" WHERE " + " AND ".join(where) if where else "") +
           " ORDER BY received_at DESC LIMIT %s")
    return clean(store.rows(sql, tuple(params + [limit])))


def _event(uid: str) -> dict:
    rows = store.rows("SELECT uid, source_id, received_at, segment, vault_offset, sha256, chain, lossless, event "
                      "FROM ulpf_events WHERE uid = %s", (uid,))
    if not rows:
        raise HTTPException(404, f"no event {uid}")
    return rows[0]


@app.get("/events/{uid}")
def event_detail(uid: str):
    row = _event(uid)
    rec = vault.read(VAULT_DIR, row["segment"], row["vault_offset"])
    enc = row["event"]["ulpf"].get("encoding", "utf-8")
    return clean({
        "uid": uid, "sourceId": row["source_id"], "receivedAt": row["received_at"],
        "raw": rec["raw"].decode(enc, errors="replace"), "rawBase64": base64.b64encode(rec["raw"]).decode(),
        "vault": {"segment": row["segment"], "offset": row["vault_offset"], "sha256": row["sha256"], "chain": row["chain"]},
        "event": row["event"],
    })


@app.post("/events/{uid}/verify")
def verify_event(uid: str):
    """Proof for one event: vault bytes match their hash, the record sits in the chain, and the raw
    event rebuilt from the normalized OCSF record alone hashes to the same SHA-256."""
    row = _event(uid)
    rec = vault.read(VAULT_DIR, row["segment"], row["vault_offset"])
    enc = row["event"]["ulpf"].get("encoding", "utf-8")
    try:
        rebuilt = lossless.reconstruct(row["event"]).encode(enc, errors="surrogateescape")
        rebuilt_sha = hashlib.sha256(rebuilt).hexdigest()
    except KeyError as e:
        rebuilt_sha = None
        log.warning("reconstruction failed for %s: %s", uid, e)
    checks = {
        "vaultHashMatches": rec["sha256_ok"] and rec["sha256"] == row["sha256"],
        "vaultRecordIsThisEvent": rec["uid"] == uid and rec["chain"] == row["chain"],
        "rebuiltFromNormalizedMatches": rebuilt_sha == row["sha256"],
    }
    return {"uid": uid, "sha256": row["sha256"], "rebuiltSha256": rebuilt_sha, "checks": checks,
            "lossless": all(checks.values())}


# ── vault ─────────────────────────────────────────────────────────────────────
@app.get("/vault")
def vault_status():
    out = []
    for w in vault.writers(VAULT_DIR):
        segs = sorted((vault.Path(VAULT_DIR) / w).glob("*.seg"))
        out.append({"writer": w, "segments": len(segs), "bytes": sum(s.stat().st_size for s in segs),
                    "sealed": sum(1 for s in segs if "sealed" in vault.Vault._read_meta(s))})
    return out


@app.post("/vault/verify")
def vault_verify():
    started = time.time()
    results = [vault.verify_writer(VAULT_DIR, w) for w in vault.writers(VAULT_DIR)]
    return {"ok": all(r["ok"] for r in results), "writers": results,
            "records": sum(r["records"] for r in results), "seconds": round(time.time() - started, 2)}


# ── parser test bench (no storage) ───────────────────────────────────────────
@app.post("/parse")
async def parse_sample(request: Request):
    body = await request.json()
    raw = str(body.get("raw", "")).encode()
    if not raw.strip():
        raise HTTPException(400, "raw is empty")
    res = process(raw, received_at=datetime.now(timezone.utc), peer=None, source_hint=body.get("source"))
    return clean({"format": res.parsed.format, "status": res.status, "lossless": res.lossless,
                  "fields": [{"name": f.name, "value": f.text, "start": f.start, "end": f.end} for f in res.parsed.fields],
                  "event": res.event})


@app.post("/admin/reconcile")
def reconcile():
    """Recomputes every source's counters from the stored events (audit / repair)."""
    store.execute("""
        UPDATE log_sources s SET events = c.events, normalized = c.normalized, partial = c.partial,
               quarantined = c.quarantined, lossless_ok = c.lossless_ok, fill_sum = c.fill_sum, fill_events = c.fill_events
        FROM (SELECT source_id, count(*) AS events, count(*) FILTER (WHERE status = 'NORMALIZED') AS normalized,
                     count(*) FILTER (WHERE status = 'PARTIAL') AS partial,
                     count(*) FILTER (WHERE status = 'QUARANTINED') AS quarantined,
                     count(*) FILTER (WHERE lossless) AS lossless_ok,
                     COALESCE(sum(fill), 0) AS fill_sum, count(fill) AS fill_events
              FROM ulpf_events GROUP BY source_id) c
        WHERE s.id = c.source_id""")
    total = store.rows("SELECT sum(events) AS stored FROM log_sources")[0]["stored"]
    return {"storedInDatabase": int(total or 0)}


# ── parser packs ──────────────────────────────────────────────────────────────
@app.get("/packs")
def list_packs():
    rows = store.rows("""SELECT p.id, p.version, p.status, p.vendor, p.product, p.origin, p.created_at, p.created_by,
                                p.notes, p.test, (SELECT count(*) FROM log_sources s WHERE s.pack_id = p.id) AS bound_sources
                         FROM parser_packs p ORDER BY p.id, p.version DESC""")
    return clean(rows)


@app.get("/packs/{pack_id}/{version}")
def get_pack(pack_id: str, version: int):
    rows = store.rows("SELECT * FROM parser_packs WHERE id = %s AND version = %s", (pack_id, version))
    if not rows:
        raise HTTPException(404, f"no pack {pack_id}@{version}")
    return clean(rows[0])


@app.post("/packs")
async def save_pack(request: Request):
    """Saves a new pack version. activate=true makes it the champion; bindSource binds a source to it."""
    body = await request.json()
    text = body.get("yaml", "")
    try:
        tested = studio.test(text, body["samples"], None)["summary"] if body.get("samples") else None
        status = "CHAMPION" if body.get("activate") else "PROPOSED"
        p = packstore.save(store, text, status=status, origin=body.get("origin", "STUDIO"),
                           created_by=body.get("user") or "local operator", notes=body.get("notes"), test=tested)
    except packs.PackError as e:
        raise HTTPException(400, str(e))
    if body.get("bindSource"):
        packstore.bind(store, body["bindSource"], p.id)
    return {"id": p.id, "version": p.version, "status": status, "test": tested}


@app.post("/packs/{pack_id}/{version}/activate")
def activate_pack(pack_id: str, version: int):
    try:
        packstore.activate(store, pack_id, version)
    except LookupError as e:
        raise HTTPException(404, str(e))
    return {"id": pack_id, "version": version, "status": "CHAMPION"}


@app.post("/sources/{source_id}/bind")
async def bind_source(source_id: str, request: Request):
    body = await request.json()
    packstore.bind(store, source_id, body.get("packId"))
    return {"source": source_id, "packId": body.get("packId")}


# ── parser studio ─────────────────────────────────────────────────────────────
@app.get("/studio/samples")
def studio_samples(source: str, limit: int = Query(100, le=500)):
    """Recent raw events of a source, read back from the vault (exact bytes)."""
    rows = store.rows("SELECT segment, vault_offset, event->'ulpf'->>'encoding' AS enc FROM ulpf_events "
                      "WHERE source_id = %s ORDER BY received_at DESC LIMIT %s", (source, limit))
    out = []
    for r in rows:
        try:
            rec = vault.read(VAULT_DIR, r["segment"], r["vault_offset"])
            out.append(rec["raw"].decode(r["enc"] or "utf-8", errors="replace"))
        except FileNotFoundError:
            continue
    return {"source": source, "samples": out}


@app.post("/studio/analyze")
async def studio_analyze(request: Request):
    body = await request.json()
    samples = [s for s in body.get("samples", []) if s.strip()][:500]
    if not samples:
        raise HTTPException(400, "no samples")
    return clean(studio.analyze(samples, packstore.registry(store), body.get("source")))


@app.post("/studio/test")
async def studio_test(request: Request):
    body = await request.json()
    samples = [s for s in body.get("samples", []) if s.strip()][:500]
    try:
        candidate = packs.load(body.get("yaml", ""))
    except packs.PackError as e:
        raise HTTPException(400, str(e))
    if not samples:
        raise HTTPException(400, "no samples")
    champion = packstore.registry(store).packs.get(candidate.id)
    return clean(studio.test(body["yaml"], samples, champion))


# ── entity graph ──────────────────────────────────────────────────────────────
@app.get("/entities")
def list_entities(q: str | None = None, kind: str | None = None, limit: int = Query(100, le=500)):
    where, params = [], []
    if q:
        where.append("value ILIKE %s")
        params.append(f"%{q}%")
    if kind:
        where.append("kind = %s")
        params.append(kind)
    sql = ("SELECT key, kind, value, country, is_private, events, cardinality(sources) AS source_count, sources, "
           "first_seen, last_seen FROM entities" + (" WHERE " + " AND ".join(where) if where else "") +
           " ORDER BY events DESC LIMIT %s")
    return clean(store.rows(sql, tuple(params + [limit])))


@app.get("/entity")
def entity_detail(key: str, minutes: int | None = Query(60, ge=1)):
    """minutes limits links and events to a recent window (None/0 = all history)."""
    node = store.rows("SELECT * FROM entities WHERE key = %s", (key,))
    if not node:
        raise HTTPException(404, f"no entity {key}")
    # Two hops: enough to resolve the asset (ip ↔ mac ↔ host) and the users seen on it.
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes) if minutes else datetime(1970, 1, 1, tzinfo=timezone.utc)
    links = store.rows("SELECT a, b, kind, events, sources, last_seen FROM entity_links WHERE (a = %s OR b = %s) AND last_seen >= %s "
                       "ORDER BY events DESC LIMIT 200", (key, key, since))
    hop1 = list({lk["a"] for lk in links} | {lk["b"] for lk in links})
    if hop1:
        more = store.rows("SELECT a, b, kind, events, sources, last_seen FROM entity_links WHERE (a = ANY(%s) OR b = ANY(%s)) "
                          "AND kind <> 'user-ip' AND last_seen >= %s ORDER BY events DESC LIMIT 400", (hop1, hop1, since))
        seen = {(lk["a"], lk["b"]) for lk in links}
        links += [lk for lk in more if (lk["a"], lk["b"]) not in seen]
    keys = list({lk["a"] for lk in links} | {lk["b"] for lk in links} | {key})
    nodes = store.rows("SELECT key, kind, value, country, events, sources FROM entities WHERE key = ANY(%s)", (keys,))
    asset = sorted(entities.asset_group(links, key))
    kind, value = node[0]["kind"], node[0]["value"]
    ips = [k[3:] for k in asset if k.startswith("ip:")]
    users = [value] if kind == "user" else []
    cond, params = [], []
    if ips:
        cond.append("src_ip = ANY(%s::inet[]) OR dst_ip = ANY(%s::inet[])")
        params += [ips, ips]
    if users:
        cond.append("lower(user_name) = ANY(%s)")
        params.append(users)
    events, by_source = [], []
    if cond:
        where = "(" + " OR ".join(cond) + ") AND received_at >= %s"
        params.append(since)
        events = store.rows("SELECT uid, source_id, received_at, class_uid, event->>'class_name' AS class_name, "
                            "host(src_ip) AS src_ip, host(dst_ip) AS dst_ip, user_name, action, product FROM ulpf_events "
                            "WHERE " + where + " ORDER BY received_at DESC LIMIT 100", tuple(params))
        by_source = store.rows("SELECT source_id, count(*) AS n FROM ulpf_events WHERE " + where +
                               " GROUP BY 1 ORDER BY 2 DESC", tuple(params))
    return clean({"entity": node[0], "asset": asset, "nodes": nodes, "links": links, "events": events,
                  "eventsBySourceLastHour": by_source, "windowMinutes": minutes})


# ── quality, incidents, re-normalization, scenarios, settings ─────────────────
@app.get("/quality")
def quality_series(source: str, minutes: int = Query(30, le=1440)):
    rows = store.rows("SELECT bucket, events, normalized, partial, quarantined, lossless_ok, fill_avg, skew_ms, pack "
                      "FROM ulpf_quality WHERE source_id = %s AND bucket > now() - make_interval(mins => %s) ORDER BY bucket",
                      (source, minutes))
    baselines = store.rows("SELECT metric, median, scale, threshold, samples, fitted_at FROM source_baselines WHERE source_id = %s",
                           (source,))
    return clean({"source": source, "buckets": rows, "baselines": {b["metric"]: b for b in baselines}})


@app.get("/quality/overview")
def quality_overview():
    """Per source: learning progress, current quality and the learned normal."""
    settings = {r["key"]: r["value"] for r in store.rows("SELECT key, value FROM ulpf_settings")}
    b = int(settings["bucketSeconds"])
    need = int(settings["learningMinutes"]) * 60 // b
    rows = store.rows("""
        SELECT s.id, s.pack_id, s.skew_ms, s.last_seen, EXTRACT(EPOCH FROM now() - s.last_seen) AS silent_seconds,
               (SELECT count(*) FROM ulpf_quality q WHERE q.source_id = s.id AND q.bucket > now() - interval '6 hours') AS buckets,
               (SELECT json_agg(json_build_object('metric', metric, 'median', median, 'scale', scale, 'threshold', threshold,
                                                  'samples', samples, 'fittedAt', fitted_at)) FROM source_baselines b WHERE b.source_id = s.id) AS baselines,
               (SELECT json_build_object('events', events, 'fill', fill_avg, 'normalized', normalized, 'skewMs', skew_ms, 'bucket', bucket)
                  FROM ulpf_quality q WHERE q.source_id = s.id AND q.bucket < now() - make_interval(secs => %s)
                  ORDER BY bucket DESC LIMIT 1) AS latest,
               (SELECT count(*) FROM ulpf_incidents i WHERE i.source_id = s.id AND i.status <> 'RESOLVED') AS open_incidents
        FROM log_sources s ORDER BY s.id""", (b,))
    for r in rows:
        r["learningProgress"] = min(1.0, int(r["buckets"]) / max(need, 1))
        r["state"] = "WATCHED" if r["baselines"] else "LEARNING"
    return clean({"settings": settings, "sources": rows})


@app.get("/incidents")
def list_incidents(status: str | None = None, limit: int = Query(100, le=500)):
    where = "WHERE status <> 'RESOLVED'" if status == "active" else "WHERE status = 'RESOLVED'" if status == "resolved" else ""
    rows = store.rows(f"""SELECT id, incident_key, kind, source_id, severity, status, title, summary, onset_at, detected_at,
                                 mitigated_at, resolved_at,
                                 EXTRACT(EPOCH FROM detected_at - onset_at) AS mttd_seconds,
                                 EXTRACT(EPOCH FROM resolved_at - onset_at) AS mttr_seconds
                          FROM ulpf_incidents {where} ORDER BY detected_at DESC LIMIT %s""", (limit,))
    return clean(rows)


@app.get("/incidents/{incident_id}")
def incident_detail(incident_id: str):
    rows = store.rows("""SELECT *, EXTRACT(EPOCH FROM detected_at - onset_at) AS mttd_seconds,
                                EXTRACT(EPOCH FROM resolved_at - onset_at) AS mttr_seconds
                         FROM ulpf_incidents WHERE id::text = %s OR incident_key = %s""", (incident_id, incident_id))
    if not rows:
        raise HTTPException(404, f"no incident {incident_id}")
    inc = rows[0]
    timeline = store.rows("SELECT at, type, payload FROM ulpf_incident_events WHERE incident_id = %s ORDER BY at, id", (inc["id"],))
    actions = store.rows("SELECT * FROM ulpf_actions WHERE incident_id = %s ORDER BY created_at", (inc["id"],))
    jobs = store.rows("SELECT * FROM renormalization_jobs WHERE incident_id = %s ORDER BY created_at", (inc["id"],))
    return clean({"incident": inc, "timeline": timeline, "actions": actions, "jobs": jobs})


@app.post("/actions/{action_id}/{decision}")
async def decide(action_id: str, decision: str, request: Request):
    """Human approval for a PROPOSED action (approve → the monitor executes it on its next tick)."""
    if decision not in ("approve", "reject"):
        raise HTTPException(400, "decision is approve or reject")
    body = await request.json() if (await request.body()) else {}
    user = body.get("user") or "local operator"
    rows = store.rows("UPDATE ulpf_actions SET status = %s, decided_by = %s WHERE id::text = %s AND status = 'PROPOSED' RETURNING incident_id, action",
                      ("APPROVED" if decision == "approve" else "REJECTED", user, action_id))
    if not rows:
        raise HTTPException(409, "only a PROPOSED action can be approved or rejected")
    store.execute("INSERT INTO ulpf_incident_events (incident_id, type, payload) VALUES (%s, %s, %s)",
                  (rows[0]["incident_id"], f"ACTION_{decision.upper()}D", json.dumps({"action": rows[0]["action"], "by": user})))
    store.execute("INSERT INTO audit_log (actor, action, entity_type, entity_id, incident_id, detail) VALUES (%s, %s, 'ulpf', %s, %s, %s)",
                  (user, f"ACTION_{decision.upper()}D", action_id, rows[0]["incident_id"], json.dumps({"action": rows[0]["action"]})))
    return {"id": action_id, "decision": decision}


@app.post("/renormalize")
async def request_renormalization(request: Request):
    body = await request.json()
    if not body.get("source"):
        raise HTTPException(400, "source is required")
    row = store.rows("""INSERT INTO renormalization_jobs (source_id, window_from, window_to, only_degraded, status, requested_by)
                        VALUES (%s, %s, %s, %s, 'PENDING', %s) RETURNING id""",
                     (body["source"], body.get("from"), body.get("to"), bool(body.get("onlyDegraded", True)),
                      body.get("user") or "local operator"))[0]
    store.execute("INSERT INTO audit_log (actor, action, entity_type, entity_id, detail) VALUES (%s, 'RENORMALIZATION_REQUESTED', 'ulpf', %s, %s)",
                  (body.get("user") or "local operator", row["id"], json.dumps(body)))
    return {"id": str(row["id"]), "status": "PENDING"}


@app.get("/jobs")
def jobs(limit: int = Query(50, le=200)):
    return clean(store.rows("SELECT * FROM renormalization_jobs ORDER BY created_at DESC LIMIT %s", (limit,)))


@app.get("/events/{uid}/versions")
def event_versions(uid: str):
    rows = store.rows("SELECT revision, parser, status, fill, event, replaced_at, job_id FROM ulpf_event_versions "
                      "WHERE uid = %s ORDER BY revision", (uid,))
    return clean(rows)


SCENARIOS = {"firmware": "vendor firmware changes its log format", "silent": "device stops sending logs",
             "skewSeconds": "device clock drifts (seconds, + = ahead)"}


@app.get("/scenarios")
def list_scenarios():
    from . import samples
    now = time.time()
    active = {k.decode(): json.loads(v) for k, v in sync_redis.hgetall("ulpf:scenarios").items()}
    return {"devices": list(samples.DEVICES), "kinds": SCENARIOS,
            "active": {d: {**s, "remainingSeconds": round(s["until"] - now)} for d, s in active.items() if s["until"] > now}}


@app.post("/scenarios")
async def set_scenario(request: Request):
    """Fault injection for the demo traffic (the replayer applies it within 2 s)."""
    body = await request.json()
    device, kind = body.get("device"), body.get("kind")
    if kind not in SCENARIOS:
        raise HTTPException(400, f"kind must be one of {list(SCENARIOS)}")
    value = body.get("value", True)
    duration = int(body.get("durationSeconds", 600))
    current = json.loads(sync_redis.hget("ulpf:scenarios", device) or "{}")
    current.update({kind: value, "until": time.time() + duration})
    sync_redis.hset("ulpf:scenarios", device, json.dumps(current))
    store.execute("INSERT INTO audit_log (actor, action, entity_type, detail) VALUES (%s, 'FAULT_INJECTED', 'ulpf', %s)",
                  (body.get("user") or "local operator", json.dumps({"device": device, "kind": kind, "value": value,
                                                                     "durationSeconds": duration})))
    return {"device": device, "scenario": current}


@app.delete("/scenarios/{device}")
def clear_scenario(device: str):
    sync_redis.hdel("ulpf:scenarios", device)
    store.execute("INSERT INTO audit_log (actor, action, entity_type, detail) VALUES ('local operator', 'FAULT_CLEARED', 'ulpf', %s)",
                  (json.dumps({"device": device}),))
    return {"device": device, "cleared": True}


@app.get("/settings")
def get_settings():
    return {r["key"]: r["value"] for r in store.rows("SELECT key, value FROM ulpf_settings")}


@app.put("/settings")
async def put_settings(request: Request):
    body = await request.json()
    allowed = {"autoExecuteMaxTier", "dryRun", "learningMinutes", "consecutiveBuckets", "maxFalsePositiveRate", "zFloor",
               "promotionCooldownMinutes", "skewThresholdSeconds"}
    before = get_settings()
    for k, v in body.items():
        if k in allowed:
            store.execute("UPDATE ulpf_settings SET value = %s, updated_at = now(), updated_by = 'local operator' WHERE key = %s",
                          (json.dumps(v), k))
    after = get_settings()
    store.execute("INSERT INTO audit_log (actor, action, entity_type, detail) VALUES ('local operator', 'ULPF_SETTINGS_CHANGED', 'ulpf', %s)",
                  (json.dumps({"before": before, "after": after}),))
    return after


# ── onboarding ────────────────────────────────────────────────────────────────
@app.get("/onboarding")
def onboarding():
    cert = Path(os.environ.get("ULPF_TLS_DIR", "/var/lib/ulpf/tls")) / "server.crt"
    pem = cert.read_text() if cert.exists() else None
    der = base64.b64decode("".join(ln for ln in pem.splitlines() if ln and not ln.startswith("-----"))) if pem else None
    return {"syslogUdpPort": int(os.environ.get("ULPF_PUBLIC_UDP_PORT", "5514")),
            "syslogTcpPort": int(os.environ.get("ULPF_PUBLIC_TCP_PORT", "5514")),
            "syslogTlsPort": int(os.environ.get("ULPF_PUBLIC_TLS_PORT", "6514")),
            "tlsCertificate": pem, "tlsFingerprintSha256": hashlib.sha256(der).hexdigest() if der else None,
            "httpIngestPath": "/api/ulpf/ingest", "geoip": "DB-IP IP to Country Lite (CC BY 4.0), bundled offline"}


@app.exception_handler(FileNotFoundError)
def _missing(_request, exc):
    return JSONResponse(status_code=404, content={"message": f"vault segment not found: {exc}"})
