"""Normalization into OCSF (Open Cybersecurity Schema Framework) 1.3.

Each extracted field either lands on an OCSF attribute whose value reproduces the original text
exactly, or its original text is kept under `unmapped` (and also mapped, typed, when OCSF needs a
different type). `ulpf.fields` records where every field's original text can be read back, which
is what makes the lossless proof possible.
"""
from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timezone

from .model import Parsed

OCSF_VERSION = "1.3.0"

# alias (lower-case, without prefixes) → OCSF path. Packs (U2) add vendor-specific mappings.
ALIASES: dict[str, str] = {}
for _path, _names in {
    "src_endpoint.ip": "src srcip src_ip source_ip sourceip srcaddr src_addr sourceaddress ipaddress client_ip clientip c-ip saddr src_ip_addr id.orig_h source-address",
    "src_endpoint.port": "spt srcport src_port sport source_port sourceport s-port id.orig_p source-port",
    "src_endpoint.hostname": "shost srchost src_host source_host workstationname sourcehostname",
    "src_endpoint.mac": "smac srcmac src_mac sourcemacaddress",
    "dst_endpoint.ip": "dst dstip dst_ip dest_ip destination_ip destinationip dstaddr dst_addr destinationaddress daddr dest server_ip s-ip id.resp_h destination-address",
    "dst_endpoint.port": "dpt dstport dst_port dport dest_port destination_port destinationport id.resp_p destination-port",
    "dst_endpoint.hostname": "dhost dsthost dst_host destination_host destinationhostname hostname",
    "dst_endpoint.mac": "dmac dstmac dst_mac destinationmacaddress",
    "connection_info.protocol_name": "proto protocol protocol-id transport ip_protocol app_proto service",
    "disposition": "act action disposition deviceaction fw_action alert.action",
    "actor.user.name": "user usr username suser src_user srcuser user_name targetusername account accountname duser login",
    "message": "msg message description reason",
    "http_request.url.url_string": "request url uri http.url requesturl",
    "http_request.http_method": "requestmethod method http.http_method",
    "http_request.user_agent": "requestclientapplication user_agent useragent http.http_user_agent",
    "query.hostname": "query qname dns.rrname dns_query",
    "traffic.bytes_out": "out sentbyte sentbytes bytes_out bytes_sent bytessent",
    "traffic.bytes_in": "in rcvdbyte rcvdbytes bytes_in bytes_received bytesrecv",
    "app_name": "app application appcat",
    "policy.name": "policyname policy_name rule rulename rule_name policyid policy-name",
    "finding_info.title": "cef.name signature alert.signature attack threat_name name",
    "finding_info.uid": "cef.signature_id alert.signature_id signature_id sid attackid leef.event_id",
}.items():
    for _n in _names.split():
        ALIASES.setdefault(_n, _path)

INT_PATHS = {"src_endpoint.port", "dst_endpoint.port", "traffic.bytes_out", "traffic.bytes_in"}
ALLOW = {"accept", "accepted", "allow", "allowed", "pass", "permit", "permitted", "built", "close", "success", "succeeded"}
DENY = {"deny", "denied", "drop", "dropped", "block", "blocked", "reject", "rejected", "reset", "teardown-deny",
        "fail", "failed", "failure", "blocked-url"}
PROTOCOLS = {"6": "tcp", "17": "udp", "1": "icmp", "58": "ipv6-icmp", "47": "gre", "50": "esp"}
_MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
_AUTH_WORDS = re.compile(r"(?i)(login|logon|log on|authentication|password|sshd|vpn|logged)")
CLASS = {
    0: ("Base Event", 0, "Other"),
    2004: ("Detection Finding", 2, "Findings"),
    3002: ("Authentication", 3, "Identity & Access Management"),
    4001: ("Network Activity", 4, "Network Activity"),
    4002: ("HTTP Activity", 4, "Network Activity"),
    4003: ("DNS Activity", 4, "Network Activity"),
    4004: ("DHCP Activity", 4, "Network Activity"),
}


def alias_key(name: str) -> str:
    n = name.split("#", 1)[0]
    if n.startswith("sd.") and n.count(".") >= 2:
        n = n.rsplit(".", 1)[1]  # RFC 5424 structured data: sd.<id>.<param>
    for prefix in ("cef.ext.", "leef.", "kv.", "EventData.", "event.", "data."):
        if n.startswith(prefix) and n[len(prefix):].lower() in ALIASES:
            n = n[len(prefix):]
    return n.lower()


def normalize(parsed: Parsed, *, received_at: datetime, source_id: str, peer: str | None, pack=None) -> dict:
    """Builds the OCSF event. A pack's mapping replaces the generic aliases (strict packs use only theirs)."""
    event: dict = {}
    unmapped: dict[str, str] = {}
    locations: dict[str, str] = {}
    mapping = pack.mapping if pack else {}
    values = pack.values if pack else {}
    use_aliases = not (pack and pack.strict)
    for f in parsed.fields:
        path = mapping.get(f.name) or (ALIASES.get(alias_key(f.name)) if use_aliases else None)
        if path and _get(event, path) is None:
            translated = values.get(f.name, {}).get(f.text.strip()) if f.name in values else None
            value = _typed(path, translated if translated is not None else f.text)
            if value is not None and value != "":
                _set(event, path, value)
                if (value if isinstance(value, str) else str(value)) == f.text:
                    locations[f.name] = path
                    continue
        unmapped[f.name] = f.text
        locations[f.name] = "unmapped:" + f.name
    if unmapped:
        event["unmapped"] = unmapped

    sev = _severity(parsed)
    time_ms, time_text = _time(parsed, received_at, pack.time_field if pack else None)
    forced = pack.classify(parsed) if pack else None
    class_uid = forced if forced in CLASS else _class(event, parsed)
    name, category_uid, category = CLASS[class_uid]
    disposition = str(event.get("disposition", "")).lower()
    action_id = 1 if disposition in ALLOW else 2 if disposition in DENY else 0
    event.update({
        "class_uid": class_uid, "class_name": name, "category_uid": category_uid, "category_name": category,
        "activity_id": 0 if class_uid == 0 else 6 if class_uid == 4001 else 1 if class_uid == 3002 else 99,
        "type_uid": class_uid * 100 + (0 if class_uid == 0 else 99),
        "severity_id": sev, "severity": ["Unknown", "Informational", "Low", "Medium", "High", "Critical", "Fatal"][sev],
        "time": time_ms,
        "metadata": {
            "version": OCSF_VERSION,
            "product": {k: v for k, v in (("vendor_name", parsed.vendor), ("name", parsed.product),
                                          ("version", parsed.version)) if v},
            "log_name": parsed.format,
            "original_time": time_text,
            "logged_time": int(received_at.timestamp() * 1000),
        },
    })
    if action_id:
        event["action_id"] = action_id
        event["action"] = "Allowed" if action_id == 1 else "Denied"
    if class_uid == 3002:
        event["status_id"] = 1 if action_id == 1 else 2 if action_id == 2 else 0
    host = (parsed.get(pack.host_field) if pack and pack.host_field else None) or parsed.get("syslog.host")
    device = {"hostname": host} if host else {}
    if peer:
        device["ip"] = peer
    if device:
        event["device"] = device
    for ep in ("src_endpoint", "dst_endpoint"):
        ip = (event.get(ep) or {}).get("ip")
        if ip:
            try:
                addr = ipaddress.ip_address(ip)
                event[ep]["is_private"] = addr.is_private  # helps SIEM-tier routing and enrichment
            except ValueError:
                pass
    event["ulpf"] = {"source_id": source_id, "format": parsed.format, "parser": parsed.parser,
                     "parser_version": parsed.parser_version, "fields": locations}
    return event


def _typed(path: str, text: str):
    t = text.strip()
    if path in INT_PATHS:
        return int(t) if t.isdigit() else None
    if path == "connection_info.protocol_name":
        return PROTOCOLS.get(t, t.lower()) if t else None
    if path.endswith(".ip"):
        try:
            ipaddress.ip_address(t)
            return t
        except ValueError:
            return None
    return text


def _class(event: dict, parsed: Parsed) -> int:
    has_net = "src_endpoint" in event or "dst_endpoint" in event
    if parsed.format.endswith(("cef", "leef")) and "finding_info" in event and _cef_severity(parsed) >= 4:
        return 2004
    if str(parsed.get("event_type") or "") == "alert" or "alert.signature" in parsed.names():
        return 2004
    if "http_request" in event:
        return 4002
    if "query" in event:
        return 4003
    text = " ".join(f.text for f in parsed.fields if f.name in ("message", "syslog.app", "cef.name", "msg", "logdesc"))
    if "actor" in event and _AUTH_WORDS.search(text + " " + " ".join(parsed.names())):
        return 3002
    if _AUTH_WORDS.search(text) and ("Failed password" in text or "Accepted" in text or "authentication" in text.lower()):
        return 3002
    return 4001 if has_net else 0


def _cef_severity(parsed: Parsed) -> int:
    s = parsed.get("cef.severity") or ""
    return int(s) if s.isdigit() else {"low": 3, "medium": 5, "high": 8, "very-high": 10}.get(s.lower(), 0)


def _severity(parsed: Parsed) -> int:
    if parsed.get("cef.severity") is not None:
        s = _cef_severity(parsed)
        return 1 if s <= 3 else 3 if s <= 6 else 4 if s <= 8 else 5
    pri = parsed.get("syslog.pri") or parsed.get("asa.severity")
    if pri and pri.isdigit():
        level = int(pri) % 8 if parsed.get("syslog.pri") else int(pri)
        return {0: 6, 1: 5, 2: 5, 3: 4, 4: 3, 5: 2, 6: 1, 7: 1}[level]
    return 0


def _time(parsed: Parsed, received_at: datetime, preferred: str | list | None = None) -> tuple[int, str | None]:
    if isinstance(preferred, list):  # e.g. [date, time]
        parts = [parsed.get(n) for n in preferred]
        if all(parts):
            ts = parse_time("T".join(parts), received_at)
            if ts:
                return int(ts.timestamp() * 1000), " ".join(parts)
    elif preferred and parsed.get(preferred):
        ts = parse_time(parsed.get(preferred), received_at)
        if ts:
            return int(ts.timestamp() * 1000), parsed.get(preferred)
    candidates = [parsed.get(n) for n in ("syslog.timestamp", "timestamp", "@timestamp", "rt", "devTime", "eventtime",
                                          "TimeCreated@SystemTime", "Event.System.TimeCreated@SystemTime")]
    date, clock = parsed.get("date"), parsed.get("time")
    if date and clock:
        candidates.insert(0, f"{date}T{clock}")
    for text in candidates:
        if not text:
            continue
        ts = parse_time(text, received_at)
        if ts:
            return int(ts.timestamp() * 1000), text
    return int(received_at.timestamp() * 1000), None


def parse_time(text: str, ref: datetime) -> datetime | None:
    t = text.strip()
    if t.isdigit() and len(t) in (10, 13):
        v = int(t)
        return datetime.fromtimestamp(v / 1000 if len(t) == 13 else v, tz=timezone.utc)
    try:
        iso = t.replace("Z", "+00:00").replace(" ", "T", 1) if re.match(r"\d{4}-\d\d-\d\d", t) else None
        if iso:
            iso = re.sub(r"(\.\d{6})\d+", r"\1", iso)
            dt = datetime.fromisoformat(iso)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        if re.match(r"\d{4}/\d\d/\d\d", t):
            return datetime.strptime(t[:19], "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    m = re.match(r"([A-Z][a-z]{2}) {1,2}(\d{1,2})(?: (\d{4}))? (\d\d):(\d\d):(\d\d)", t)
    if m and m.group(1) in _MONTHS:
        # RFC 3164 has no year (and usually no zone): take the year that puts the event closest to arrival.
        month, day = _MONTHS[m.group(1)], int(m.group(2))
        hh, mm, ss = int(m.group(4)), int(m.group(5)), int(m.group(6))
        years = [int(m.group(3))] if m.group(3) else [ref.year - 1, ref.year, ref.year + 1]
        best = None
        for y in years:
            try:
                dt = datetime(y, month, day, hh, mm, ss, tzinfo=timezone.utc)
            except ValueError:
                continue
            if best is None or abs((dt - ref).total_seconds()) < abs((best - ref).total_seconds()):
                best = dt
        return best
    return None


def _get(obj: dict, path: str):
    for part in path.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


def _set(obj: dict, path: str, value) -> None:
    parts = path.split(".")
    for part in parts[:-1]:
        obj = obj.setdefault(part, {})
    obj[parts[-1]] = value


get_path = _get
