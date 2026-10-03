"""Stateless pipeline worker: Redis Streams consumer group → vault → parse → OCSF → Postgres → ack.

Run several replicas; each claims entries left pending by a dead worker (XAUTOCLAIM), so a crash
never loses an event. Order per batch: vault append + fsync, then the database insert, then ack.
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import time
from collections import defaultdict
from datetime import datetime, timezone

import redis

from . import packstore
from .entities import Batch
from .pipeline import process
from .store import Store, event_row
from .vault import Vault

STREAM = "ulpf:raw"
GROUP = "workers"
log = logging.getLogger("ulpf.worker")


class Worker:
    def __init__(self):
        self.name = os.environ.get("ULPF_WORKER_NAME") or socket.gethostname()
        self.r = redis.Redis.from_url(os.environ.get("ULPF_REDIS_URL", "redis://redis:6379/0"))
        self.store = Store()
        self.vault = Vault(os.environ.get("ULPF_VAULT_DIR", "/var/lib/ulpf/vault"), self.name,
                           max_bytes=int(os.environ.get("ULPF_SEGMENT_MB", "64")) * 2 ** 20,
                           max_age_s=int(os.environ.get("ULPF_SEGMENT_SECONDS", "600")),
                           on_seal=lambda seg, info: self.store.record_segment(seg, self.vault.writer, info, True))
        self.batch = int(os.environ.get("ULPF_BATCH", "500"))
        self.claim_idle_ms = int(os.environ.get("ULPF_CLAIM_IDLE_MS", "30000"))
        self.stop = False
        self.processed = 0
        self.last_claim = 0.0
        self.registry = packstore.registry(self.store)
        self.registry_loaded = time.time()
        try:
            self.r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    def run(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stop", True))
        log.info("worker %s started", self.name)
        # First drain anything this consumer had pending before a restart.
        self._handle(self.r.xreadgroup(GROUP, self.name, {STREAM: "0"}, count=self.batch))
        while not self.stop:
            try:
                if time.time() - self.registry_loaded > 10:  # hot reload: new champions and bindings
                    self.registry = packstore.registry(self.store)
                    self.registry_loaded = time.time()
                if time.time() - self.last_claim > 5:
                    self.last_claim = time.time()
                    _, claimed, *_ = self.r.xautoclaim(STREAM, GROUP, self.name, self.claim_idle_ms, "0-0", count=self.batch)
                    if claimed:
                        log.warning("claimed %d entries left pending by another worker", len(claimed))
                        self._process(claimed)
                self._handle(self.r.xreadgroup(GROUP, self.name, {STREAM: ">"}, count=self.batch, block=1000))
                self._heartbeat()
            except (redis.ConnectionError, OSError) as e:
                log.error("transient failure: %s", e)
                time.sleep(2)
            except Exception as e:  # e.g. database restart: entries stay pending and are reclaimed
                log.exception("batch failed, will be retried: %s", e)
                self.store._conn = None
                time.sleep(2)
        self.vault.seal()

    def _handle(self, response) -> None:
        for _stream, entries in response or []:
            if entries:
                self._process(entries)

    def _process(self, entries) -> None:
        items, results = [], []
        for entry_id, fields in entries:
            if not fields:  # entry deleted after another worker acknowledged it
                continue
            uid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
            raw = fields[b"d"]
            received = datetime.fromtimestamp(int(fields[b"t"]) / 1000, tz=timezone.utc)
            peer = fields.get(b"p", b"").decode() or None
            hint = fields.get(b"h", b"").decode() or None
            transport = fields.get(b"x", b"").decode() or None
            items.append((uid, raw))
            results.append((uid, received, transport, peer, len(raw),
                            process(raw, received_at=received, peer=peer, source_hint=hint, registry=self.registry)))
        if not items:
            return
        refs = self.vault.append_many(items)
        rows = [event_row(uid, res, ref, received) for (uid, received, _t, _p, _s, res), ref in zip(results, refs)]
        with self.store.transaction() as cur:
            new = self.store.insert_events(cur, rows)
            stats = defaultdict(lambda: {"events": 0, "NORMALIZED": 0, "PARTIAL": 0, "QUARANTINED": 0,
                                         "lossless": 0, "bytes": 0, "bind": None, "fill_sum": 0.0, "fill_events": 0})
            graph = Batch()
            for uid, received, transport, peer, size, res in results:
                if uid not in new:
                    continue  # redelivered after a crash: already stored and counted
                s = stats[res.source_id]
                s["events"] += 1
                s[res.status] += 1
                s["lossless"] += int(res.lossless)
                s["bytes"] += size
                if res.newly_bound:
                    s["bind"] = res.pack.id
                if res.fill is not None:
                    s["fill_sum"] += res.fill
                    s["fill_events"] += 1
                graph.add(res.event, res.source_id, received)
                s.update({"host": res.parsed.get("syslog.host") or peer, "vendor": res.parsed.vendor,
                          "product": res.parsed.product, "format": res.parsed.format[:60], "peer": peer,
                          "transport": transport, "last": received})
                s.setdefault("first", received)
            self.store.upsert_sources(cur, stats)
            try:
                with cur.connection.transaction():  # savepoint: the derived entity graph never blocks the pipeline
                    graph.flush(cur)
            except Exception as e:
                log.warning("entity graph update skipped for this batch: %s", e)
        ids = [uid for uid, _ in items]
        pipe = self.r.pipeline(transaction=True)  # ack, delete and count atomically (conservation check)
        pipe.xack(STREAM, GROUP, *ids)
        pipe.xdel(STREAM, *ids)
        pipe.hincrby("ulpf:processed", self.name, len(ids))
        pipe.hincrby("ulpf:received", "stored", len(ids))
        pipe.execute()
        self.processed += len(ids)

    def _heartbeat(self) -> None:
        self.r.hset("ulpf:workers", self.name, f"{time.time():.3f}")


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    while True:
        try:
            Worker().run()
            return
        except Exception as e:  # e.g. database not migrated yet: retry instead of crash-looping fast
            log.exception("worker failed to start or crashed: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
