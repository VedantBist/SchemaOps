#!/usr/bin/env python3
"""
CausalOps Phase 8 — Local Concurrency & Load Benchmark
======================================================
Executes controlled concurrency tests (10, 50, and 100 requests) against
key API endpoints to quantify local Docker throughput, response latencies,
error rates, and rate-limiting behaviors.

Usage:
  python3 scripts/phase8_load_test.py
  python3 scripts/phase8_load_test.py --concurrency 10,50,100
"""

import argparse
import concurrent.futures
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "ai-engine"))

from fastapi.testclient import TestClient
from app.main import app

OUT_DIR = REPO_ROOT / "artifacts" / "phase8"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def test_endpoint_load(
    client: TestClient,
    method: str,
    path: str,
    payload: Any,
    concurrency_levels: List[int],
) -> Dict[str, Any]:
    """Test an endpoint at various concurrency levels."""
    endpoint_results = {}

    for c in concurrency_levels:
        latencies = []
        status_codes = {}
        errors = 0
        rate_limited = 0

        def send_single_request(_: int):
            t0 = time.perf_counter()
            try:
                if method == "GET":
                    resp = client.get(path)
                elif method == "POST":
                    resp = client.post(path, json=payload)
                else:
                    resp = client.request(method, path, json=payload)
                dur = (time.perf_counter() - t0) * 1000
                return resp.status_code, dur, None
            except Exception as e:
                dur = (time.perf_counter() - t0) * 1000
                return -1, dur, str(e)

        t_batch_start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(c, 16)) as executor:
            futures = [executor.submit(send_single_request, i) for i in range(c)]
            for fut in concurrent.futures.as_completed(futures):
                code, dur, err = fut.result()
                latencies.append(dur)
                status_codes[code] = status_codes.get(code, 0) + 1
                if code == 429:
                    rate_limited += 1
                elif code >= 400 or code < 0:
                    errors += 1
        total_time = time.perf_counter() - t_batch_start

        arr = np.array(latencies)
        rps = round(c / total_time, 2) if total_time > 0 else 0.0

        endpoint_results[f"concurrency_{c}"] = {
            "requests_sent": c,
            "duration_seconds": round(total_time, 3),
            "requests_per_second": rps,
            "status_distribution": status_codes,
            "rate_limited_count": rate_limited,
            "error_count": errors,
            "success_rate_pct": round(((c - errors) / c) * 100, 2),
            "latency_p50_ms": round(float(np.median(arr)), 2),
            "latency_p95_ms": round(float(np.percentile(arr, 95)), 2),
            "latency_p99_ms": round(float(np.percentile(arr, 99)), 2),
            "latency_mean_ms": round(float(np.mean(arr)), 2),
        }

    return endpoint_results


def main():
    parser = argparse.ArgumentParser(description="Phase 8 Local Load Test")
    parser.add_argument("--concurrency", default="10,50,100", help="Comma-separated concurrency levels")
    args = parser.parse_args()

    levels = [int(x.strip()) for x in args.concurrency.split(",") if x.strip()]

    print("=" * 70)
    print(" CausalOps Phase 8 — Local Concurrency & Load Benchmark")
    print(f" Concurrency levels: {levels}")
    print("=" * 70)

    client = TestClient(app)

    endpoints_to_test = [
        ("GET", "/health", None),
        ("GET", "/ready", None),
        ("GET", "/models", None),
        ("POST", "/analyze/root-cause", {
            "topology": {
                "nodes": ["api-gateway", "order-service", "inventory-service", "payment-service", "inventory-db"],
                "edges": [["api-gateway", "order-service"], ["order-service", "inventory-service"]]
            },
            "telemetry": [
                {"timestamp": 1, "service": "inventory-db", "metrics": {"db_latency": 450.0}},
                {"timestamp": 2, "service": "inventory-db", "metrics": {"db_latency": 520.0}},
            ]
        }),
    ]

    all_results = {
        "benchmark_type": "Local Docker / In-Process Concurrency Benchmark",
        "concurrency_levels": levels,
        "endpoints": {},
    }

    for method, path, payload in endpoints_to_test:
        print(f"\nBenchmarking {method} {path}...")
        res = test_endpoint_load(client, method, path, payload, levels)
        all_results["endpoints"][f"{method} {path}"] = res
        for lvl in levels:
            c_res = res[f"concurrency_{lvl}"]
            print(
                f"  [{lvl} reqs] {c_res['requests_per_second']} rps | "
                f"p50: {c_res['latency_p50_ms']}ms | p95: {c_res['latency_p95_ms']}ms | "
                f"Success: {c_res['success_rate_pct']}% (Rate-limited: {c_res['rate_limited_count']})"
            )

    out_file = OUT_DIR / "load_test_results.json"
    out_file.write_text(json.dumps(all_results, indent=2))
    print(f"\n✅ Load test completed. Results written to: {out_file}")


if __name__ == "__main__":
    main()
