#!/usr/bin/env python3
"""End-to-end ULPF throughput: send generated perimeter logs over syslog TCP as fast as possible, then
measure how many events per second the running stack actually stores (intake → queue → workers → vault
→ database), until the queue is drained. Standard library plus the repository's sample generators.

    python3 scripts/ulpf/bench.py --seconds 30
    python3 scripts/ulpf/bench.py --seconds 30 --api http://localhost:8080/api/ulpf --host 127.0.0.1 --port 5514

The result is also posted to the API, so the console's Benchmarks panel shows it.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ml.ulpf.bench import corpus  # noqa: E402


def stats(api):
    return json.load(urllib.request.urlopen(api + "/stats", timeout=30))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://localhost:8080/api/ulpf")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5514)
    p.add_argument("--seconds", type=float, default=30)
    p.add_argument("--connections", type=int, default=4)
    a = p.parse_args()
    lines = corpus(50000, seed=99)
    frames = [f"{len(x)} ".encode() + x for x in lines]
    before = stats(a.api)
    socks = [socket.create_connection((a.host, a.port)) for _ in range(a.connections)]
    sent, i, t0 = 0, 0, time.time()
    while time.time() - t0 < a.seconds:
        chunk = b"".join(frames[(i + k) % len(frames)] for k in range(200))
        socks[i % len(socks)].sendall(chunk)
        i += 200
        sent += 200
    send_s = time.time() - t0
    for s in socks:
        s.close()
    # Wait until everything sent was received and the queue drained.
    deadline = time.time() + 600
    while time.time() < deadline:
        st = stats(a.api)
        c = st["conservation"]
        if c["received"] - before["conservation"]["received"] >= sent and c["inFlight"] == 0:
            break
        time.sleep(1)
    total_s = time.time() - t0
    after = stats(a.api)
    stored = after["conservation"]["stored"] - before["conservation"]["stored"]
    result = {"kind": "END_TO_END", "sent": sent, "sendSeconds": round(send_s, 1), "storedEvents": stored,
              "seconds": round(total_s, 1), "storedPerSecond": round(stored / total_s), "intakePerSecond": round(sent / send_s),
              "workers": len([w for w in after["workers"] if w["alive"]]),
              "conservationBalanced": after["conservation"]["balanced"],
              "eventsPerDay": round(stored / total_s * 86400)}
    print(json.dumps(result, indent=1))
    try:
        req = urllib.request.Request(a.api + "/benchmarks", data=json.dumps(result).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30)
    except Exception as e:  # the console simply will not show it
        print("could not store the result:", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
