"""Log replayer: sends realistic perimeter-device traffic to the collector over syslog UDP/TCP.

Used for demos and benchmarks. Each device has its own transport, rate share and state (clock
offset, firmware variant); scenario switches are read from Redis keys so the console's Fault Lab
can change a device's behaviour while the replayer runs.
"""
from __future__ import annotations

import argparse
import logging
import os
import random
import socket
import time

from . import samples

log = logging.getLogger("ulpf.replay")
TCP_DEVICES = {"pa-edge-01", "srx-core-01", "ids-sensor-01", "dc01.corp.local"}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=os.environ.get("ULPF_TARGET_HOST", "ulpf"))
    p.add_argument("--port", type=int, default=int(os.environ.get("ULPF_TARGET_PORT", "5514")))
    p.add_argument("--rate", type=float, default=float(os.environ.get("ULPF_REPLAY_RATE", "40")), help="events/s")
    p.add_argument("--count", type=int, default=0, help="stop after N events (0 = run forever)")
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args()
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    rng = random.Random(args.seed)
    ctx = {name: samples.Ctx(random.Random(rng.random())) for name in samples.DEVICES}
    names = list(samples.DEVICES)
    weights = [3 if n in ("pa-edge-01", "fgt-dc-01", "asa-perimeter") else 1 for n in names]
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tcp = None
    sent, started = 0, time.time()
    interval = 1.0 / args.rate if args.rate > 0 else 0
    while args.count == 0 or sent < args.count:
        name = rng.choices(names, weights)[0]
        line = samples.DEVICES[name](ctx[name], name).encode()
        try:
            if name in TCP_DEVICES:
                if tcp is None:
                    tcp = socket.create_connection((args.host, args.port), timeout=5)
                tcp.sendall(f"{len(line)} ".encode() + line)  # RFC 6587 octet counting
            else:
                udp.sendto(line, (args.host, args.port))
            sent += 1
        except OSError as e:
            log.warning("send failed (%s); retrying in 2 s", e)
            tcp = None
            time.sleep(2)
            continue
        if sent % 5000 == 0:
            log.info("sent %d events (%.1f/s)", sent, sent / (time.time() - started))
        if interval:
            time.sleep(max(0.0, started + sent * interval - time.time()))


if __name__ == "__main__":
    main()
