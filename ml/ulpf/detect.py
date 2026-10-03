"""Format detection and core parsing.

Order: syslog header (optional) → body format (CEF, LEEF, JSON, XML, key=value, delimited, plain
text). Every byte that is not inside a field stays in the literal skeleton, so nothing is lost even
when a format is only partly understood.
"""
from __future__ import annotations

import re

from .model import Field, Parsed, dedupe_names
from .parsers.cef import parse_cef, parse_leef
from .parsers.structured import JsonSpanError, parse_json, parse_xml
from .parsers.syslog import parse_header
from .parsers.text import kv_pairs, parse_csv, parse_kv, split_csv

_ASA = re.compile(r"%(ASA|FTD|PIX|FWSM)-(\d)-(\d{6}): ?")
_EMBEDDED = re.compile(r"(CEF|LEEF):\d")


def parse(text: str) -> Parsed:
    variant, fields, body = parse_header(text)
    parsed = _body(text, body)
    parsed.fields = dedupe_names(sorted(fields + parsed.fields, key=lambda f: f.start))
    if variant:
        parsed.format = f"{variant}+{parsed.format}" if parsed.format != "empty" else variant
        if not parsed.product:
            parsed.product = _get(fields, "syslog.app")
    return parsed


def _body(text: str, start: int) -> Parsed:
    rest = text[start:]
    stripped = rest.lstrip()
    if not stripped:
        return Parsed("empty")
    pos = start + (len(rest) - len(stripped))

    m = _EMBEDDED.search(text, pos, min(len(text), pos + 64))
    if m:
        prefix = []
        if m.start() > pos and text[pos:m.start()].strip():
            prefix = [Field("prefix", text[pos:m.start()].rstrip(), pos, pos + len(text[pos:m.start()].rstrip()))]
        if m.group(1) == "CEF":
            fields, meta = parse_cef(text, m.start())
            return Parsed("cef", prefix + fields, meta["vendor"], meta["product"], meta["version"], "core:cef")
        fields, meta = parse_leef(text, m.start())
        return Parsed("leef", prefix + fields, meta["vendor"], meta["product"], meta["version"], "core:leef")

    if stripped[0] in "{[":
        try:
            return Parsed("json", parse_json(text, pos), parser="core:json")
        except JsonSpanError:
            pass
    if stripped[0] == "<" and len(stripped) > 1 and (stripped[1].isalpha() or stripped[1] in "?!"):
        try:
            return Parsed("xml", parse_xml(text, pos), parser="core:xml")
        except ValueError:
            pass

    asa_pos = pos + 2 if text.startswith(": ", pos) else pos  # "host : %ASA-..." puts a colon before the tag
    asa = _ASA.match(text, asa_pos)
    if asa:
        head = [Field("asa.facility", asa.group(1), asa.start(1), asa.end(1)),
                Field("asa.severity", asa.group(2), asa.start(2), asa.end(2)),
                Field("asa.message_id", asa.group(3), asa.start(3), asa.end(3))]
        msg_end = len(text.rstrip("\r\n"))
        msg = [Field("message", text[asa.end():msg_end], asa.end(), msg_end)] if msg_end > asa.end() else []
        return Parsed("cisco-asa", head + msg, "Cisco", asa.group(1), None, "core:asa")

    pairs = kv_pairs(text, pos)
    covered = sum(m.end() - m.start() for m in pairs)
    if len(pairs) >= 3 and covered >= 0.5 * len(stripped):
        return Parsed("kv", parse_kv(text, pos), parser="core:kv")

    spans = split_csv(text, pos)
    if len(spans) >= 6 and "  " not in stripped[:80]:
        return Parsed("csv", parse_csv(text, pos), parser="core:csv")

    end = len(text.rstrip("\r\n"))
    return Parsed("text", [Field("message", text[pos:end], pos, end)] if end > pos else [], parser="core:text")


def _get(fields: list[Field], name: str):
    for f in fields:
        if f.name == name:
            return f.text
    return None
