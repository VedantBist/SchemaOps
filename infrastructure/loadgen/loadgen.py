"""Continuous business traffic for the CausalOps reference system.

Real telemetry needs real requests. This sends a steady, slowly varying stream of
order lookups through the edge (via Toxiproxy) so every service and the database
produce spans and metrics all the time, not only during experiments.

Configuration (environment variables):
  LOADGEN_TARGET        base URL of the edge, default http://toxiproxy:18081
  LOADGEN_RPS           mean request rate, default 5
  LOADGEN_VARIATION     relative amplitude of the slow rate wave (0..0.9), default 0.3
  LOADGEN_PERIOD_S      period of the rate wave in seconds, default 600
  LOADGEN_TIMEOUT_S     per-request timeout, default 15
  LOADGEN_MAX_INFLIGHT  concurrency cap, default 64
"""
import logging
import math
import os
import random
import threading
import time
import urllib.error
import urllib.request

TARGET = os.environ.get("LOADGEN_TARGET", "http://toxiproxy:18081").rstrip("/")
RPS = float(os.environ.get("LOADGEN_RPS", "5"))
VARIATION = min(0.9, max(0.0, float(os.environ.get("LOADGEN_VARIATION", "0.3"))))
PERIOD = float(os.environ.get("LOADGEN_PERIOD_S", "600"))
TIMEOUT = float(os.environ.get("LOADGEN_TIMEOUT_S", "15"))
MAX_INFLIGHT = int(os.environ.get("LOADGEN_MAX_INFLIGHT", "64"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s loadgen %(levelname)s %(message)s")
log = logging.getLogger("loadgen")

inflight = threading.BoundedSemaphore(MAX_INFLIGHT)
lock = threading.Lock()
stats = {"ok": 0, "error": 0, "dropped": 0}


def one_request() -> None:
    order_id = f"ord-{random.randint(1, 10_000)}"
    try:
        with urllib.request.urlopen(f"{TARGET}/orders/{order_id}", timeout=TIMEOUT) as resp:
            resp.read()
        outcome = "ok"
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        outcome = "error"
    finally:
        inflight.release()
    with lock:
        stats[outcome] += 1


def current_rate(t0: float) -> float:
    wave = math.sin(2 * math.pi * (time.monotonic() - t0) / PERIOD)
    return max(0.1, RPS * (1 + VARIATION * wave))


def main() -> None:
    log.info("target=%s mean_rps=%.2f variation=%.2f period=%ss", TARGET, RPS, VARIATION, PERIOD)
    t0 = time.monotonic()
    last_report = t0
    next_tick = t0
    while True:
        # Poisson arrivals around the current rate.
        next_tick += random.expovariate(current_rate(t0))
        delay = next_tick - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        elif delay < -5:
            next_tick = time.monotonic()  # fell far behind; resynchronise instead of bursting
        if inflight.acquire(blocking=False):
            threading.Thread(target=one_request, daemon=True).start()
        else:
            with lock:
                stats["dropped"] += 1
        now = time.monotonic()
        if now - last_report >= 30:
            with lock:
                log.info("last 30s: ok=%d error=%d dropped=%d rate=%.2f", stats["ok"], stats["error"],
                         stats["dropped"], current_rate(t0))
                stats.update(ok=0, error=0, dropped=0)
            last_report = now


if __name__ == "__main__":
    main()
