"""PostgreSQL persistence for normalized events, sources and vault segments (schema: Flyway V8)."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

EVENT_COLUMNS = ("uid", "source_id", "received_at", "event_time", "class_uid", "status", "format", "product",
                 "src_ip", "dst_ip", "user_name", "action", "message", "lossless", "segment", "vault_offset",
                 "sha256", "chain", "parser", "fill", "event")


def dsn() -> str:
    explicit = os.environ.get("ULPF_DB_DSN") or os.environ.get("ENGINE_DB_DSN")
    if explicit:
        return explicit
    return (f"host={os.environ.get('ENGINE_DB_HOST', 'postgres')} port={os.environ.get('ENGINE_DB_PORT', '5432')} "
            f"dbname={os.environ.get('ENGINE_DB_NAME', 'causalops')} user={os.environ.get('ENGINE_DB_USER', 'causalops')} "
            f"password={os.environ.get('ENGINE_DB_PASSWORD', 'causalops')}")


class Store:
    def __init__(self, conninfo: str | None = None):
        self.conninfo = conninfo or dsn()
        self._conn = None

    def conn(self):
        import psycopg
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self.conninfo, autocommit=True)
        return self._conn

    @contextmanager
    def cursor(self) -> Iterator[Any]:
        try:
            with self.conn().cursor() as cur:
                yield cur
        except Exception:
            if self._conn is not None and self._conn.broken:
                self._conn = None
            raise

    def rows(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self.cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return []
            cols = [c.name for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def execute(self, sql: str, params: tuple | dict = ()) -> None:
        with self.cursor() as cur:
            cur.execute(sql, params)

    # ── writes from the worker ──────────────────────────────────────────────
    @contextmanager
    def transaction(self) -> Iterator[Any]:
        """One database transaction (events, source counters and entity graph commit together)."""
        conn = self.conn()
        with conn.transaction(), conn.cursor() as cur:
            yield cur

    @staticmethod
    def insert_events(cur, rows: list[tuple]) -> set[str]:
        """Idempotent batch insert (a redelivered stream entry keeps its first stored copy). Returns new uids."""
        if not rows:
            return set()
        if True:
            cur.execute("CREATE TEMP TABLE IF NOT EXISTS ulpf_events_in (LIKE ulpf_events INCLUDING DEFAULTS) ON COMMIT DELETE ROWS")
            with cur.copy(f"COPY ulpf_events_in ({', '.join(EVENT_COLUMNS)}) FROM STDIN") as copy:
                for r in rows:
                    copy.write_row(r)
            cur.execute(f"INSERT INTO ulpf_events ({', '.join(EVENT_COLUMNS)}) "
                        f"SELECT {', '.join(EVENT_COLUMNS)} FROM ulpf_events_in ON CONFLICT (uid) DO NOTHING RETURNING uid")
            return {r[0] for r in cur.fetchall()}

    @staticmethod
    def upsert_sources(cur, stats: dict[str, dict]) -> None:
        if not stats:
            return
        if True:
            for sid, s in stats.items():
                cur.execute("""
                    INSERT INTO log_sources (id, host, vendor, product, format, first_seen, last_seen, last_peer, transport,
                                             events, normalized, partial, quarantined, lossless_ok, bytes, pack_id,
                                             pack_bound_at, fill_sum, fill_events)
                    VALUES (%(id)s, %(host)s, %(vendor)s, %(product)s, %(format)s, %(first)s, %(last)s, %(peer)s, %(transport)s,
                            %(events)s, %(NORMALIZED)s, %(PARTIAL)s, %(QUARANTINED)s, %(lossless)s, %(bytes)s, %(bind)s::varchar,
                            CASE WHEN %(bind)s::varchar IS NULL THEN NULL ELSE now() END, %(fill_sum)s, %(fill_events)s)
                    ON CONFLICT (id) DO UPDATE SET
                        pack_id = COALESCE(log_sources.pack_id, EXCLUDED.pack_id),
                        pack_bound_at = COALESCE(log_sources.pack_bound_at, EXCLUDED.pack_bound_at),
                        fill_sum = log_sources.fill_sum + EXCLUDED.fill_sum,
                        fill_events = log_sources.fill_events + EXCLUDED.fill_events,
                        last_seen = GREATEST(log_sources.last_seen, EXCLUDED.last_seen),
                        vendor = COALESCE(EXCLUDED.vendor, log_sources.vendor),
                        product = COALESCE(EXCLUDED.product, log_sources.product),
                        format = EXCLUDED.format, last_peer = EXCLUDED.last_peer, transport = EXCLUDED.transport,
                        events = log_sources.events + EXCLUDED.events,
                        normalized = log_sources.normalized + EXCLUDED.normalized,
                        partial = log_sources.partial + EXCLUDED.partial,
                        quarantined = log_sources.quarantined + EXCLUDED.quarantined,
                        lossless_ok = log_sources.lossless_ok + EXCLUDED.lossless_ok,
                        bytes = log_sources.bytes + EXCLUDED.bytes""", {"id": sid, **s})

    def record_segment(self, segment: str, writer: str, info: dict, sealed: bool) -> None:
        self.execute("""
            INSERT INTO vault_segments (id, writer, prev_head, head, records, bytes, sealed_at, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET head = EXCLUDED.head, records = EXCLUDED.records, bytes = EXCLUDED.bytes,
                sealed_at = EXCLUDED.sealed_at, status = EXCLUDED.status""",
                     (segment, writer, info.get("prev"), info.get("head"), info.get("records", 0), info.get("bytes", 0),
                      datetime.now(timezone.utc) if sealed else None, "SEALED" if sealed else "OPEN"))


def event_row(uid: str, result, ref, received_at: datetime) -> tuple:
    e = result.event
    src = (e.get("src_endpoint") or {}).get("ip")
    dst = (e.get("dst_endpoint") or {}).get("ip")
    user = ((e.get("actor") or {}).get("user") or {}).get("name")
    msg = e.get("message") or (e.get("finding_info") or {}).get("title")
    event_time = datetime.fromtimestamp(e["time"] / 1000, tz=timezone.utc)
    e["ulpf"].update({"uid": uid, "vault": {"segment": ref.segment, "offset": ref.offset, "chain": ref.chain}})
    return (uid, result.source_id, received_at, event_time, e["class_uid"], result.status,
            result.parsed.format[:60], (result.parsed.product or "")[:120] or None, src, dst,
            (user or "")[:200] or None, e.get("action"), (msg or "")[:500] or None, result.lossless, ref.segment,
            ref.offset, result.sha256, ref.chain, f"{result.parsed.parser}@{result.parsed.parser_version}"[:80],
            result.fill, json.dumps(e, separators=(",", ":")))
