"""One raw event → normalized OCSF record with lineage and a lossless proof."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

from . import geoip, lossless
from .detect import parse
from .model import Parsed
from .ocsf import normalize

_SAFE = re.compile(r"[^A-Za-z0-9_.:-]+")


@dataclass
class Result:
    event: dict
    status: str            # NORMALIZED | PARTIAL | QUARANTINED
    lossless: bool
    sha256: str
    encoding: str
    source_id: str
    parsed: Parsed
    pack: object = None        # the packs.Pack applied, if any
    newly_bound: bool = False  # this event bound its source to the pack
    fill: float | None = None  # share of the pack's expected OCSF attributes present
    device_time_ms: int | None = None  # time stated by the device itself (None when the event carries no time)


def decode(raw: bytes) -> tuple[str, str]:
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def source_key(parsed: Parsed, peer: str | None, hint: str | None) -> str:
    if hint:
        return _SAFE.sub("-", hint)[:120]
    host = parsed.get("syslog.host")
    for name in ("devname", "device_id", "origin", "host", "Event.System.Computer", "hostname", "dvchost"):
        if host:
            break
        host = parsed.get(name)
    host = host or peer or "unknown"
    kind = parsed.product or parsed.format.split("+")[-1]
    return _SAFE.sub("-", f"{host}/{kind}")[:120]


def process(raw: bytes, *, received_at: datetime, peer: str | None = None, source_hint: str | None = None,
            parser=parse, registry=None, pack=None) -> Result:
    """`registry` picks the pack by source binding or match; `pack` forces one (parser test bench)."""
    sha = hashlib.sha256(raw).hexdigest()
    text, encoding = decode(raw)
    try:
        parsed = parser(text)
        error = None
    except Exception as e:  # a parser bug must never lose the event: it is quarantined instead
        parsed = Parsed("unparsed", parser="none")
        error = f"{type(e).__name__}: {e}"
    source_id = source_key(parsed, peer, source_hint)
    newly_bound = False
    if pack is None and registry is not None and error is None:
        pack, newly_bound = registry.for_event(source_id, parsed, text)
    if pack is not None and error is None:
        try:
            parsed = pack.apply(parsed)
        except Exception as e:
            error = f"pack {pack.ref}: {type(e).__name__}: {e}"
    event = normalize(parsed, received_at=received_at, source_id=source_id, peer=peer, pack=pack)
    geoip.enrich(event)
    device_ms = event["time"] if event["metadata"].get("original_time") is not None else None
    skew = registry.skews.get(source_id) if registry is not None and hasattr(registry, "skews") else None
    if device_ms is not None and skew:
        # The source's clock is known to be off by `skew` ms (estimated by the monitor): correct the event time
        # used for search and correlation; the device's own time stays in metadata.original_time and ulpf.clock.
        event["time"] = device_ms + skew
        event["ulpf"]["clock"] = {"device_time": device_ms, "skew_ms": skew, "corrected": True}
    try:
        event["ulpf"]["skeleton"] = lossless.skeleton(text, parsed.fields)
    except ValueError as e:
        error = str(e)
        event["ulpf"]["skeleton"] = [text]
        event["ulpf"]["fields"] = {}
    ok = lossless.verify(event, encoding, sha)
    real_fields = [f for f in parsed.fields if not f.name.startswith("syslog.") and f.name != "message"]
    fill = pack.fill(event) if pack is not None else None
    if error or not parsed.fields:
        status = "QUARANTINED"
    elif fill is not None:
        status = "NORMALIZED" if fill >= 0.75 and event["class_uid"] != 0 else "PARTIAL"
    elif not real_fields or event["class_uid"] == 0:
        status = "PARTIAL"
    else:
        status = "NORMALIZED"
    event["ulpf"].update({"status": status, "lossless": ok, "sha256": sha, "encoding": encoding})
    if pack is not None:
        event["ulpf"]["pack"] = {"id": pack.id, "version": pack.version, "fill": round(fill, 4)}
    if error:
        event["ulpf"]["error"] = error
    return Result(event, status, ok, sha, encoding, source_id, parsed, pack, newly_bound, fill, device_ms)
