"""ArcSight CEF and IBM QRadar LEEF (1.0 and 2.0)."""
from __future__ import annotations

import re

from ..model import Field

_CEF_HEADER = ["cef.version", "cef.vendor", "cef.product", "cef.device_version", "cef.signature_id",
               "cef.name", "cef.severity"]
_LEEF_HEADER = ["leef.version", "leef.vendor", "leef.product", "leef.device_version", "leef.event_id"]
# A key follows whitespace, or directly the last header pipe ("...|7|src=10.0.0.1 ...").
_CEF_KEY = re.compile(r"(?:(?<=[\s|])|^)([A-Za-z0-9_.\[\]-]+)=")


def _split_pipes(text: str, start: int, count: int) -> tuple[list[tuple[int, int]], int]:
    """Splits `count` pipe-terminated header fields honouring \\| escapes. Returns spans and the offset after."""
    spans = []
    i = cur = start
    while len(spans) < count and i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "|":
            spans.append((cur, i))
            cur = i + 1
        i += 1
    return spans, cur


def parse_cef(text: str, start: int) -> tuple[list[Field], dict]:
    assert text.startswith("CEF:", start)
    spans, ext_start = _split_pipes(text, start + 4, 7)
    fields = [Field(n, text[a:b], a, b) for n, (a, b) in zip(_CEF_HEADER, spans) if b > a]
    fields += _cef_extension(text, ext_start)
    meta = {"vendor": _get(fields, "cef.vendor"), "product": _get(fields, "cef.product"),
            "version": _get(fields, "cef.device_version")}
    return fields, meta


def _cef_extension(text: str, start: int) -> list[Field]:
    keys = [m for m in _CEF_KEY.finditer(text, start) if not _escaped(text, m.end() - 1)]
    out = []
    for i, m in enumerate(keys):
        vstart = m.end()
        vend = keys[i + 1].start() if i + 1 < len(keys) else len(text)
        while vend > vstart and text[vend - 1] in " \t\r\n":
            vend -= 1
        if vend > vstart:
            out.append(Field(m.group(1), text[vstart:vend], vstart, vend))
    return out


def _escaped(text: str, i: int) -> bool:
    n = 0
    while i - 1 - n >= 0 and text[i - 1 - n] == "\\":
        n += 1
    return n % 2 == 1


def parse_leef(text: str, start: int) -> tuple[list[Field], dict]:
    assert text.startswith("LEEF:", start)
    spans, attr_start = _split_pipes(text, start + 5, 5)
    fields = [Field(n, text[a:b], a, b) for n, (a, b) in zip(_LEEF_HEADER, spans) if b > a]
    delim = "\t"
    if _get(fields, "leef.version", "").startswith("2"):
        # LEEF 2.0 adds a delimiter field: a character or its hex code (x5E / 0x5E).
        dspans, after = _split_pipes(text, attr_start, 1)
        if dspans:
            a, b = dspans[0]
            raw = text[a:b]
            if raw:
                fields.append(Field("leef.delimiter", raw, a, b))
            hexv = raw.lower().lstrip("0").lstrip("x")
            delim = chr(int(hexv, 16)) if raw.lower().startswith(("x", "0x")) and hexv else (raw or "\t")
            attr_start = after
    pos = attr_start
    while pos < len(text):
        nxt = text.find(delim, pos)
        seg_end = len(text) if nxt < 0 else nxt
        eq = text.find("=", pos, seg_end)
        if eq > pos:
            vend = seg_end
            while vend > eq + 1 and text[vend - 1] in "\r\n":
                vend -= 1
            if vend > eq + 1:
                out_name = text[pos:eq].strip()
                fields.append(Field(out_name, text[eq + 1:vend], eq + 1, vend))
        if nxt < 0:
            break
        pos = nxt + len(delim)
    meta = {"vendor": _get(fields, "leef.vendor"), "product": _get(fields, "leef.product"),
            "version": _get(fields, "leef.device_version")}
    return fields, meta


def _get(fields: list[Field], name: str, default=None):
    for f in fields:
        if f.name == name:
            return f.text
    return default
