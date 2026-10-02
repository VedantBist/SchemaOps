"""PostgreSQL access for the engine.

The platform API owns telemetry, topology, environments and incidents; the engine reads
them and writes only its own tables (calibration_runs, model_registry).
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator

import pandas as pd


@dataclass(frozen=True)
class EnvironmentRecord:
    id: str
    name: str
    status: str
    config: dict
    learning_started_at: datetime


def _dsn() -> str:
    explicit = os.environ.get("ENGINE_DB_DSN")
    if explicit:
        return explicit
    return (f"host={os.environ.get('ENGINE_DB_HOST', 'postgres')} "
            f"port={os.environ.get('ENGINE_DB_PORT', '5432')} "
            f"dbname={os.environ.get('ENGINE_DB_NAME', 'causalops')} "
            f"user={os.environ.get('ENGINE_DB_USER', 'causalops')} "
            f"password={os.environ.get('ENGINE_DB_PASSWORD', 'causalops')}")


class Store:
    """Thin, explicit SQL layer. Every query is parameterized."""

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or _dsn()

    @contextmanager
    def connect(self) -> Iterator[Any]:
        import psycopg  # imported lazily so pure-model modules and tests do not need a database driver
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            yield conn

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [c.name for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)

    # ── reads ────────────────────────────────────────────────────────────────
    def environment(self, env_id: str) -> EnvironmentRecord:
        rows = self._rows("SELECT id::text, name, status, config, learning_started_at FROM environments WHERE id = %s", (env_id,))
        if not rows:
            raise LookupError(f"Unknown environment {env_id}")
        r = rows[0]
        return EnvironmentRecord(r["id"], r["name"], r["status"], r["config"], r["learning_started_at"])

    def topology(self, env_id: str) -> tuple[list[dict], list[dict]]:
        nodes = self._rows("SELECT name, kind FROM services WHERE environment_id = %s ORDER BY name", (env_id,))
        edges = self._rows("""SELECT source_service AS client, target_service AS server, connection_type
                              FROM dependencies WHERE environment_id = %s ORDER BY 1, 2""", (env_id,))
        return nodes, edges

    def telemetry(self, env_id: str, start: datetime, end: datetime) -> pd.DataFrame:
        rows = self._rows("""
            SELECT captured_at, service_name, p50_latency, p95_latency, p99_latency, error_rate, request_rate,
                   db_latency, pool_utilization, pool_pending
            FROM telemetry_snapshots
            WHERE environment_id = %s AND captured_at >= %s AND captured_at <= %s
            ORDER BY captured_at""", (env_id, start, end))
        return pd.DataFrame(rows)

    def edge_telemetry(self, env_id: str, start: datetime, end: datetime) -> pd.DataFrame:
        rows = self._rows("""
            SELECT captured_at, client, server, client_p95, server_p95, request_rate, error_rate
            FROM edge_snapshots
            WHERE environment_id = %s AND captured_at >= %s AND captured_at <= %s
            ORDER BY captured_at""", (env_id, start, end))
        return pd.DataFrame(rows)

    def data_span(self, env_id: str) -> tuple[datetime | None, datetime | None]:
        """Telemetry usable for learning: samples since learning (re)started, i.e. measured with the
        environment's current metric definitions."""
        r = self._rows("""SELECT min(t.captured_at) AS first, max(t.captured_at) AS last
                          FROM telemetry_snapshots t JOIN environments e ON e.id = t.environment_id
                          WHERE t.environment_id = %s AND t.captured_at >= e.learning_started_at""", (env_id,))[0]
        return r["first"], r["last"]

    def faults(self, env_id: str, start: datetime, end: datetime) -> list[dict]:
        """Injected faults with known target, type and timing: the labelled ground truth."""
        return self._rows("""
            SELECT id::text, type, target, parameters, status, started_at, stopped_at, duration_seconds
            FROM fault_injections
            WHERE environment_id = %s AND status <> 'FAILED' AND started_at >= %s AND started_at <= %s
            ORDER BY started_at""", (env_id, start, end))

    # ── calibration runs ─────────────────────────────────────────────────────
    def has_running_calibration(self, env_id: str) -> bool:
        return bool(self._rows("SELECT 1 FROM calibration_runs WHERE environment_id = %s AND status = 'RUNNING'", (env_id,)))

    def start_run(self, env_id: str, mode: str, trigger: str) -> str:
        rows = self._rows("""INSERT INTO calibration_runs (environment_id, mode, trigger) VALUES (%s, %s, %s)
                             RETURNING id::text""", (env_id, mode, trigger))
        return rows[0]["id"]

    def finish_run(self, run_id: str, *, status: str, data_from=None, data_to=None, model_version=None,
                   promoted=None, decision=None, quality_passed=None, metrics=None, error=None) -> None:
        self._execute("""
            UPDATE calibration_runs SET status = %s, finished_at = now(), data_from = %s, data_to = %s,
                   model_version = %s, promoted = %s, decision = %s, quality_passed = %s,
                   metrics = %s::jsonb, error = %s
            WHERE id = %s""", (status, data_from, data_to, model_version, promoted, decision, quality_passed,
                               json.dumps(metrics or {}, default=str), error, run_id))

    def runs(self, env_id: str, limit: int = 20) -> list[dict]:
        return self._rows("""SELECT id::text, mode, trigger, status, started_at, finished_at, model_version, promoted,
                                    quality_passed, decision, error, metrics
                             FROM calibration_runs WHERE environment_id = %s ORDER BY started_at DESC LIMIT %s""",
                          (env_id, limit))

    # ── model registry ───────────────────────────────────────────────────────
    def champion(self, env_id: str) -> dict | None:
        rows = self._rows("""SELECT version, artifact_path, checksum, data_from, data_to, metrics
                             FROM model_registry WHERE environment_id = %s AND status = 'CHAMPION'""", (env_id,))
        return rows[0] if rows else None

    def register_model(self, env_id: str, *, version: str, status: str, run_id: str, data_from, data_to,
                       artifact_path: str, checksum: str, metrics: dict, components: dict) -> None:
        """Adds a model version; promoting it retires the previous champion in the same transaction."""
        with self.connect() as conn:
            with conn.transaction(), conn.cursor() as cur:
                if status == "CHAMPION":
                    cur.execute("""UPDATE model_registry SET status = 'RETIRED', retired_at = now()
                                   WHERE environment_id = %s AND status = 'CHAMPION'""", (env_id,))
                cur.execute("""
                    INSERT INTO model_registry (environment_id, version, status, calibration_run_id, data_from, data_to,
                        artifact_path, checksum, metrics, components, promoted_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                            CASE WHEN %s = 'CHAMPION' THEN now() END)""",
                            (env_id, version, status, run_id, data_from, data_to, artifact_path, checksum,
                             json.dumps(metrics, default=str), json.dumps(components, default=str), status))

    def models(self, env_id: str) -> list[dict]:
        return self._rows("""SELECT version, status, created_at, promoted_at, retired_at, data_from, data_to, checksum, metrics
                             FROM model_registry WHERE environment_id = %s ORDER BY created_at DESC""", (env_id,))
