"""Throughput benchmarks (results are measured on the machine they run on and stored with its details).

PROCESSING   one worker's CPU path in-process: parse → pack → OCSF → GeoIP → lossless proof → vault append
             (fsync per batch), over a realistic vendor mix. Reports events/s per worker.
END_TO_END   syslog over TCP into the running intake as fast as possible; measures what is stored per second
             (queue → workers → vault → Postgres) until the queue is empty. Run from scripts/ulpf/bench.py.
"""
from __future__ import annotations

import os
import platform
import random
import tempfile
import time
from datetime import datetime, timezone

from . import packs, samples
from .pipeline import process
from .vault import Vault


def corpus(n: int, seed: int = 1) -> list[bytes]:
    rng = random.Random(seed)
    ctx = {name: samples.Ctx(random.Random(rng.random())) for name in samples.DEVICES}
    names = list(samples.DEVICES)
    return [samples.DEVICES[d](ctx[d], d).encode() for d in (rng.choice(names) for _ in range(n))]


def processing(seconds: float = 15.0, batch: int = 500, registry: packs.Registry | None = None) -> dict:
    registry = registry or packs.Registry(packs.builtin())
    lines = corpus(20000)
    now = datetime.now(timezone.utc)
    n, lossless, bytes_in = 0, 0, 0
    with tempfile.TemporaryDirectory() as d:
        v = Vault(d, "bench")
        started = time.perf_counter()
        i = 0
        while time.perf_counter() - started < seconds:
            items = []
            for _ in range(batch):
                raw = lines[i % len(lines)]
                i += 1
                r = process(raw, received_at=now, peer="192.0.2.1", registry=registry)
                lossless += r.lossless
                bytes_in += len(raw)
                items.append((f"{i}-0", raw))
            v.append_many(items)
            n += batch
        elapsed = time.perf_counter() - started
        v.seal()
    eps = n / elapsed
    return {"kind": "PROCESSING", "events": n, "seconds": round(elapsed, 2), "eventsPerSecondPerWorker": round(eps),
            "eventsPerDayPerWorker": round(eps * 86400), "mbPerSecond": round(bytes_in / elapsed / 2 ** 20, 2),
            "losslessPct": round(100 * lossless / n, 3), "avgEventBytes": round(bytes_in / n),
            "machine": {"cpu": platform.processor() or platform.machine(), "cores": os.cpu_count(), "python": platform.python_version()},
            "note": "single process, one CPU core; workers scale horizontally (stateless, consumer group)"}


if __name__ == "__main__":
    import json
    print(json.dumps(processing(float(os.environ.get("ULPF_BENCH_SECONDS", "15"))), indent=1))
