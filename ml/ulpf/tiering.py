"""SIEM cost tiering without data loss.

Every event goes to the data lake in full (OCSF plus every vendor field, lineage and the vault position).
The SIEM tier receives what detection and investigation need, and points to the lake for the rest:

  field-level tiering  each record keeps the normalized OCSF attributes (who, what, where, outcome, finding)
                       and drops the vendor-specific `unmapped` fields and internal metadata; `ulpf.uid` and
                       `ulpf.sha256` identify the full record in the lake and the raw bytes in the vault
  event-level tiering  security-relevant events stay one record each (findings, authentication, denied or
                       blocked traffic, HTTP, DNS, anything matching a detection rule); routine *allowed*
                       network flows and DHCP leases in the same batch collapse into one summary per
                       (source, src, dst, port, protocol, action) with the count, byte totals, time span and
                       the uids of the events it covers (`ulpf.lake_ref`)
Nothing is dropped: every event is either a record or counted in exactly one summary, and its full form is
one lookup away.
"""
from __future__ import annotations

import json
from collections import OrderedDict

KEEP_CLASSES = {2004, 3002, 4002, 4003}
SUMMARISE_CLASSES = {4001, 4004}
CORE = ("class_uid", "class_name", "category_uid", "activity_id", "type_uid", "severity_id", "time", "action", "action_id",
        "status_id", "disposition", "src_endpoint", "dst_endpoint", "actor", "connection_info", "traffic", "app_name",
        "policy", "http_request", "query", "auth_protocol", "logon_type", "activity_name", "count", "end_time")


def relevant(event: dict, sigma_uids: set[str]) -> bool:
    if event["class_uid"] in KEEP_CLASSES or event["class_uid"] not in SUMMARISE_CLASSES:
        return True
    if event.get("ulpf", {}).get("uid") in sigma_uids:
        return True
    if event.get("action_id") == 2:  # denied / blocked / failed
        return True
    if event.get("ulpf", {}).get("status") != "NORMALIZED":
        return True  # not understood well enough to summarise: keep it
    return False


def slim(e: dict) -> dict:
    """The SIEM record: normalized attributes plus the pointer to the full record."""
    out = {k: e[k] for k in CORE if k in e}
    if e.get("finding_info"):
        out["finding_info"] = {k: v for k, v in e["finding_info"].items() if k in ("title", "uid")}
    if isinstance(e.get("message"), str):
        out["message"] = e["message"][:256]
    prod = (e.get("metadata") or {}).get("product") or {}
    out["metadata"] = {"product": {k: v for k, v in prod.items() if k in ("vendor_name", "name")}}
    if (e.get("device") or {}).get("hostname"):
        out["device"] = {"hostname": e["device"]["hostname"]}
    meta = e.get("ulpf") or {}
    out["ulpf"] = {k: meta[k] for k in ("uid", "source_id", "sha256", "summary", "lake_ref", "privacy") if k in meta}
    return out


def tier(events: list[dict], sigma_uids: set[str] | None = None) -> tuple[list[dict], dict]:
    """Returns (SIEM-tier records, stats). Kept events first in arrival order, then summaries."""
    sigma_uids = sigma_uids or set()
    kept, groups = [], OrderedDict()
    for e in events:
        if relevant(e, sigma_uids):
            kept.append(slim(e))
            continue
        src, dst = e.get("src_endpoint") or {}, e.get("dst_endpoint") or {}
        key = (e["ulpf"]["source_id"], e["class_uid"], src.get("ip"), dst.get("ip"), dst.get("port"),
               (e.get("connection_info") or {}).get("protocol_name"), e.get("action"))
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"first": e, "count": 0, "uids": [], "bytes_in": 0, "bytes_out": 0,
                               "start": e["time"], "end": e["time"]}
        g["count"] += 1
        g["uids"].append(e["ulpf"]["uid"])
        t = e.get("traffic") or {}
        g["bytes_in"] += int(t.get("bytes_in") or 0)
        g["bytes_out"] += int(t.get("bytes_out") or 0)
        g["start"], g["end"] = min(g["start"], e["time"]), max(g["end"], e["time"])
    summaries = []
    for (source, cls, srcip, dstip, port, proto, action), g in groups.items():
        f = g["first"]
        if g["count"] == 1:
            summaries.append(slim(f))
            continue
        s = slim(f)
        s.update({"time": g["start"], "end_time": g["end"], "count": g["count"],
                  "src_endpoint": {k: v for k, v in (f.get("src_endpoint") or {}).items() if k in ("ip", "is_private", "location", "mac", "hostname")},
                  "traffic": {"bytes_in": g["bytes_in"], "bytes_out": g["bytes_out"]}})
        s["src_endpoint"].pop("port", None)
        s["ulpf"] = {"source_id": source, "summary": True,
                     "lake_ref": {"uids": g["uids"], "count": g["count"], "time_from": g["start"], "time_to": g["end"]}}
        summaries.append(s)
    out = kept + summaries
    return out, {"in": len(events), "kept": len(kept), "summaries": len(summaries), "out": len(out)}


def size(events: list[dict]) -> int:
    """Bytes a SIEM would ingest for these events (compact JSON, one per line)."""
    return sum(len(json.dumps(e, separators=(",", ":"), default=str)) + 1 for e in events)


def strip_internal(event: dict) -> dict:
    """The full exported record: OCSF, vendor fields and lineage, without the lossless skeleton (kept in the store)."""
    e = dict(event)
    meta = dict(e.get("ulpf") or {})
    for k in ("skeleton", "fields"):
        meta.pop(k, None)
    e["ulpf"] = meta
    return e
