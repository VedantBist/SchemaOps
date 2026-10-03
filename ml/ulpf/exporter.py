"""ULPF exporter: follows newly committed events (by insert order) and
  1. runs the Sigma rules on them (one engine, every vendor) and records detections,
  2. writes them to every enabled sink: the data lake gets everything; SIEM-tier sinks get the tiered stream
     (security-relevant events whole, routine allowed flows summarised with a pointer to the lake),
  3. pseudonymises personal data for sinks marked `tokenize` (DPDP), and
  4. measures bytes per sink and what an untiered stream would have cost.
A sink's cursor only advances after a successful write, so a failing sink catches up when it recovers.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

from . import sigma, sinks, tiering
from .privacy import Tokenizer
from .store import Store

log = logging.getLogger("ulpf.exporter")
GUARD_S = 10          # rows younger than this may still have earlier-sequenced rows committing
BATCH = 2000
SIEM_EVERY_S = 60     # SIEM-tier sinks send one batch per minute, so routine flows can be summarised


class Exporter:
    def __init__(self, store: Store | None = None):
        self.store = store or Store()
        self.tokenizer = Tokenizer(store=self.store)
        self.instances: dict[str, tuple[str, sinks.Sink]] = {}
        self.engine = None
        self.rules_loaded = 0.0
        self.sigma_cursor = None
        self.last_sent: dict[str, float] = {}

    # ── rules ────────────────────────────────────────────────────────────────
    def load_rules(self) -> None:
        for r in sigma.builtin():
            self.store.execute("""INSERT INTO sigma_rules (id, title, level, tags, yaml) VALUES (%s, %s, %s, %s, %s)
                                  ON CONFLICT (id) DO NOTHING""", (r.id, r.title, r.level, r.tags, r.text))
        rules = []
        for row in self.store.rows("SELECT yaml FROM sigma_rules WHERE enabled"):
            try:
                rules.append(sigma.load(row["yaml"]))
            except Exception as e:
                log.warning("sigma rule not loaded: %s", e)
        old = {r.id: r for r in (self.engine.rules if self.engine else [])}
        for r in rules:  # keep sliding windows across reloads
            if r.id in old:
                r.windows, r.fired = old[r.id].windows, old[r.id].fired
        self.engine = sigma.Engine(rules)
        self.rules_loaded = time.time()

    # ── events ──────────────────────────────────────────────────────────────
    def fetch(self, after: int, limit: int = BATCH, guard: int = GUARD_S) -> list[dict]:
        rows = self.store.rows("SELECT seq, uid, event FROM ulpf_events WHERE seq > %s AND inserted_at < now() - make_interval(secs => %s) "
                               "ORDER BY seq LIMIT %s", (after, guard, limit))
        out = []
        for r in rows:
            e = r["event"]
            e["ulpf"]["uid"] = r["uid"]
            e["ulpf"]["seq"] = r["seq"]
            out.append(e)
        return out

    def detect(self) -> int:
        if self.sigma_cursor is None:
            row = self.store.rows("SELECT value FROM ulpf_settings WHERE key = 'sigmaCursor'")
            self.sigma_cursor = int(row[0]["value"]) if row else self.store.rows("SELECT COALESCE(max(seq), 0) AS m FROM ulpf_events")[0]["m"]
        events = self.fetch(self.sigma_cursor, 5000)
        if not events:
            return 0
        hits = self.engine.process(events)
        for h in hits:
            self.save_hit(h)
        self.sigma_cursor = events[-1]["ulpf"]["seq"]
        self.store.execute("INSERT INTO ulpf_settings (key, value) VALUES ('sigmaCursor', %s) ON CONFLICT (key) DO UPDATE "
                           "SET value = EXCLUDED.value, updated_at = now()", (json.dumps(self.sigma_cursor),))
        return len(hits)

    def save_hit(self, h: dict) -> None:
        """One open detection per rule and group: repeated hits within 10 minutes extend it."""
        first = datetime.fromtimestamp(h["first_seen"] / 1000, tz=timezone.utc)
        last = datetime.fromtimestamp(h["last_seen"] / 1000, tz=timezone.utc)
        open_ = self.store.rows("SELECT id FROM sigma_hits WHERE rule_id = %s AND group_key = %s AND last_seen > %s - interval '10 minutes' "
                                "ORDER BY last_seen DESC LIMIT 1", (h["rule_id"], h["group_key"], first))
        if open_:
            self.store.execute("""UPDATE sigma_hits SET count = count + %s, last_seen = GREATEST(last_seen, %s),
                                      distinct_count = GREATEST(COALESCE(distinct_count, 0), COALESCE(%s, 0)),
                                      sources = ARRAY(SELECT DISTINCT unnest(sources || %s)),
                                      vendors = ARRAY(SELECT DISTINCT unnest(vendors || %s)),
                                      sample_uids = (sample_uids || %s)[GREATEST(1, cardinality(sample_uids || %s) - 49):]
                                  WHERE id = %s""",
                               (h["count"], last, h["distinct_count"], h["sources"], h["vendors"], h["sample_uids"], h["sample_uids"],
                                open_[0]["id"]))
        else:
            self.store.execute("""INSERT INTO sigma_hits (rule_id, rule_title, level, group_key, count, distinct_count, sources, vendors,
                                                          sample_uids, first_seen, last_seen)
                                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                               (h["rule_id"], h["rule_title"], h["level"], h["group_key"], h["count"], h["distinct_count"],
                                h["sources"], h["vendors"], h["sample_uids"], first, last))

    # ── sinks ───────────────────────────────────────────────────────────────
    def sink(self, row: dict) -> sinks.Sink:
        sig = json.dumps([row["kind"], row["config"]], sort_keys=True)
        cached = self.instances.get(row["name"])
        if cached and cached[0] == sig:
            return cached[1]
        s = sinks.build(row["kind"], row["name"], row["config"] or {})
        self.instances[row["name"]] = (sig, s)
        return s

    def export(self) -> None:
        for row in self.store.rows("SELECT * FROM ulpf_sinks WHERE enabled ORDER BY name"):
            if row["cursor_seq"] == 0:  # a new sink starts at the current end, not at the beginning of history
                start = self.store.rows("SELECT COALESCE(max(seq), 0) AS m FROM ulpf_events")[0]["m"]
                self.store.execute("UPDATE ulpf_sinks SET cursor_seq = %s WHERE name = %s", (start, row["name"]))
                continue
            siem = row["tier"] == "siem"
            if siem and time.time() - self.last_sent.get(row["name"], 0) < SIEM_EVERY_S:
                continue
            events = self.fetch(row["cursor_seq"], 20000 if siem else BATCH)
            if not events:
                continue
            self.last_sent[row["name"]] = time.time()
            # Every event that matches a detection rule's selection stays whole in the SIEM tier.
            sigma_uids = {e["ulpf"]["uid"] for e in events if any(r.match(e) for r in self.engine.rules)}
            full = [tiering.strip_internal(e) for e in events]
            out, stats = (tiering.tier(full, sigma_uids) if row["tier"] == "siem" else (full, {"out": len(full)}))
            if row["tokenize"]:
                out = [self.tokenizer.event(e)[0] for e in out]
            try:
                written = self.sink(row).write(out)
                if row["tokenize"]:
                    self.tokenizer.flush()
            except Exception as e:
                log.warning("sink %s failed: %s", row["name"], e)
                self.store.execute("UPDATE ulpf_sinks SET last_error = %s, last_error_at = now() WHERE name = %s",
                                   (str(e)[:500], row["name"]))
                self._stats(row["name"], 0, 0, 0, 0, errors=1)
                continue
            self.store.execute("UPDATE ulpf_sinks SET cursor_seq = %s, exported = exported + %s, last_ok = now() WHERE name = %s",
                               (events[-1]["ulpf"]["seq"], len(out), row["name"]))
            self._stats(row["name"], len(events), len(out), written, tiering.size(full))

    def _stats(self, sink: str, n_in: int, n_out: int, written: int, full: int, errors: int = 0) -> None:
        self.store.execute("""INSERT INTO sink_stats (sink, day, events_in, events_out, bytes, full_bytes, errors)
                              VALUES (%s, current_date, %s, %s, %s, %s, %s)
                              ON CONFLICT (sink, day) DO UPDATE SET events_in = sink_stats.events_in + EXCLUDED.events_in,
                                  events_out = sink_stats.events_out + EXCLUDED.events_out, bytes = sink_stats.bytes + EXCLUDED.bytes,
                                  full_bytes = sink_stats.full_bytes + EXCLUDED.full_bytes, errors = sink_stats.errors + EXCLUDED.errors""",
                           (sink, n_in, n_out, written, full, errors))

    def tick(self) -> None:
        if self.engine is None or time.time() - self.rules_loaded > 30:
            self.load_rules()
        self.detect()
        self.export()


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ex = Exporter()
    while True:
        try:
            ex.tick()
        except Exception as e:
            log.exception("exporter tick failed: %s", e)
            ex.store._conn = None
        time.sleep(float(os.environ.get("ULPF_EXPORT_INTERVAL", "3")))


if __name__ == "__main__":
    main()
