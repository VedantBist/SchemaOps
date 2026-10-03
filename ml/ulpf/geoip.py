"""Offline IP → country lookup (DB-IP "IP to Country Lite", CC BY 4.0, bundled in the image).

No network access at runtime: the range table ships in ml/ulpf/data. Private and reserved
addresses are reported as such instead of a country.
"""
from __future__ import annotations

import bisect
import gzip
import ipaddress
import os
from array import array
from functools import lru_cache
from pathlib import Path

DATA = Path(os.environ.get("ULPF_GEOIP_FILE", Path(__file__).parent / "data" / "dbip-country-lite.csv.gz"))
_v4_starts: array | None = None
_v4_cc: list[str] = []
_v6_starts: list[int] = []
_v6_cc: list[str] = []


def _load() -> None:
    global _v4_starts
    if _v4_starts is not None:
        return
    starts = array("L")
    if DATA.exists():
        with gzip.open(DATA, "rt") as fh:
            for line in fh:
                start, _end, cc = line.rstrip("\n").split(",")
                if ":" in start:
                    _v6_starts.append(int(ipaddress.IPv6Address(start)))
                    _v6_cc.append(cc)
                else:
                    a, b, c, d = start.split(".")
                    starts.append((int(a) << 24) | (int(b) << 16) | (int(c) << 8) | int(d))
                    _v4_cc.append(cc)
    _v4_starts = starts


def available() -> bool:
    return DATA.exists()


@lru_cache(maxsize=65536)
def country(ip: str) -> str | None:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_multicast:
        return None
    _load()
    if addr.version == 4:
        i = bisect.bisect_right(_v4_starts, int(addr)) - 1
        cc = _v4_cc[i] if i >= 0 else None
    else:
        i = bisect.bisect_right(_v6_starts, int(addr)) - 1
        cc = _v6_cc[i] if i >= 0 else None
    return None if cc in (None, "ZZ") else cc


def enrich(event: dict) -> None:
    """Adds OCSF endpoint.location.country for public addresses."""
    for ep in ("src_endpoint", "dst_endpoint"):
        node = event.get(ep)
        if node and node.get("ip") and not node.get("is_private", True):
            cc = country(node["ip"])
            if cc:
                node["location"] = {"country": cc}
