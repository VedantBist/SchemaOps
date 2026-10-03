"""Syslog headers: RFC 5424 (with structured data) and RFC 3164 / BSD, including common vendor variants
(year in the timestamp, ISO timestamps, missing PRI, missing hostname)."""
from __future__ import annotations

import re

from ..model import Field

_5424 = re.compile(r"<(\d{1,3})>(\d{1,2}) (\S+) (\S+) (\S+) (\S+) (\S+) ")
_MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
_3164 = re.compile(
    r"(?:<(?P<pri>\d{1,3})>)?"
    r"(?P<ts>(?:" + _MONTHS + r") {1,2}\d{1,2}(?: \d{4})? \d\d:\d\d:\d\d(?:\.\d+)?"
    r"|\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?)"
    r"(?::)? "
    r"(?:(?P<host>(?!%)[^\s:\[\]]+) )?"
    r"(?:(?P<tag>(?!%|(?:CEF|LEEF):)[A-Za-z0-9_./-]+)(?:\[(?P<pid>[^\]\s]+)\])?: ?)?")
_PRI_ONLY = re.compile(r"<(\d{1,3})>")


def parse_header(text: str) -> tuple[str | None, list[Field], int]:
    """Returns (variant, header fields, offset where the message body starts)."""
    m = _5424.match(text)
    if m:
        names = ["syslog.pri", "syslog.version", "syslog.timestamp", "syslog.host", "syslog.app",
                 "syslog.procid", "syslog.msgid"]
        fields = [Field(n, m.group(i + 1), m.start(i + 1), m.end(i + 1))
                  for i, n in enumerate(names) if m.group(i + 1) != "-"]
        pos = m.end()
        sd_fields, pos = _structured_data(text, pos)
        fields += sd_fields
        if pos < len(text) and text[pos] == " ":
            pos += 1
        if text.startswith("\ufeff", pos):  # BOM before an UTF-8 MSG
            pos += 1
        return "syslog-5424", fields, pos
    m = _3164.match(text)
    if m and (m.group("pri") or m.group("ts")):
        fields = []
        for group, name in (("pri", "syslog.pri"), ("ts", "syslog.timestamp"), ("host", "syslog.host"),
                            ("tag", "syslog.app"), ("pid", "syslog.procid")):
            if m.group(group):
                fields.append(Field(name, m.group(group), m.start(group), m.end(group)))
        return "syslog-3164", fields, m.end()
    m = _PRI_ONLY.match(text)
    if m:
        return "syslog-3164", [Field("syslog.pri", m.group(1), m.start(1), m.end(1))], m.end()
    return None, [], 0


def _structured_data(text: str, pos: int) -> tuple[list[Field], int]:
    """RFC 5424 SD-ELEMENTs: [id param="value" ...]... ; '-' means none. Values may contain \\" \\] \\\\."""
    fields: list[Field] = []
    if text.startswith("-", pos):
        return fields, pos + 1
    while pos < len(text) and text[pos] == "[":
        end_id = pos + 1
        while end_id < len(text) and text[end_id] not in " ]":
            end_id += 1
        sd_id = text[pos + 1:end_id]
        pos = end_id
        while pos < len(text) and text[pos] == " ":
            pos += 1
            eq = text.find("=", pos)
            if eq < 0 or eq + 1 >= len(text) or text[eq + 1] != '"':
                return fields, pos
            name = text[pos:eq]
            vstart = eq + 2
            i = vstart
            while i < len(text) and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            fields.append(Field(f"sd.{sd_id}.{name}", text[vstart:i], vstart, i))
            pos = i + 1
        if pos < len(text) and text[pos] == "]":
            pos += 1
        else:
            break
    return fields, pos
