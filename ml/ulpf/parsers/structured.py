"""JSON and XML with exact character spans for every leaf value, so normalization stays provably lossless."""
from __future__ import annotations

import re

from ..model import Field

_WS = " \t\r\n"
_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


class JsonSpanError(ValueError):
    pass


def parse_json(text: str, start: int) -> list[Field]:
    out: list[Field] = []
    end = _value(text, _skip(text, start), "", out)
    if text[_skip(text, end):].strip():
        raise JsonSpanError("trailing data after JSON value")
    return out


def _skip(text: str, i: int) -> int:
    while i < len(text) and text[i] in _WS:
        i += 1
    return i


def _string(text: str, i: int) -> int:
    """i points at the opening quote; returns the index of the closing quote."""
    j = i + 1
    while j < len(text):
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == '"':
            return j
        j += 1
    raise JsonSpanError("unterminated string")


def _value(text: str, i: int, path: str, out: list[Field]) -> int:
    if i >= len(text):
        raise JsonSpanError("unexpected end")
    c = text[i]
    if c == "{":
        i = _skip(text, i + 1)
        if text[i:i + 1] == "}":
            return i + 1
        while True:
            if text[i:i + 1] != '"':
                raise JsonSpanError(f"expected key at {i}")
            kend = _string(text, i)
            key = text[i + 1:kend]
            i = _skip(text, kend + 1)
            if text[i:i + 1] != ":":
                raise JsonSpanError(f"expected ':' at {i}")
            i = _value(text, _skip(text, i + 1), f"{path}.{key}" if path else key, out)
            i = _skip(text, i)
            if text[i:i + 1] == ",":
                i = _skip(text, i + 1)
                continue
            if text[i:i + 1] == "}":
                return i + 1
            raise JsonSpanError(f"expected ',' or '}}' at {i}")
    if c == "[":
        i = _skip(text, i + 1)
        if text[i:i + 1] == "]":
            return i + 1
        idx = 0
        while True:
            i = _skip(text, _value(text, i, f"{path}.{idx}" if path else str(idx), out))
            idx += 1
            if text[i:i + 1] == ",":
                i = _skip(text, i + 1)
                continue
            if text[i:i + 1] == "]":
                return i + 1
            raise JsonSpanError(f"expected ',' or ']' at {i}")
    if c == '"':
        end = _string(text, i)
        if end > i + 1:
            out.append(Field(path or "value", text[i + 1:end], i + 1, end))
        return end + 1
    for lit in ("true", "false", "null"):
        if text.startswith(lit, i):
            if lit != "null":
                out.append(Field(path or "value", lit, i, i + len(lit)))
            return i + len(lit)
    m = _NUM.match(text, i)
    if m:
        out.append(Field(path or "value", m.group(), i, m.end()))
        return m.end()
    raise JsonSpanError(f"unexpected character {c!r} at {i}")


_TAG = re.compile(r"<(/?)([A-Za-z_][\w:.-]*)((?:\s+[\w:.-]+\s*=\s*(?:\"[^\"]*\"|'[^']*'))*)\s*(/?)>")
_ATTR = re.compile(r"([\w:.-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")
_SKIP = re.compile(r"<\?.*?\?>|<!--.*?-->|<!\[CDATA\[|\]\]>", re.S)


def parse_xml(text: str, start: int) -> list[Field]:
    """Element text and attributes. Windows events' <Data Name="X">v</Data> becomes the field 'EventData.X'."""
    out: list[Field] = []
    stack: list[str] = []
    named: list[str | None] = []
    pos = start
    while pos < len(text):
        lt = text.find("<", pos)
        seg_end = len(text) if lt < 0 else lt
        body = text[pos:seg_end]
        if stack and body.strip():
            lead = len(body) - len(body.lstrip())
            trail = len(body) - len(body.rstrip())
            a, b = pos + lead, seg_end - trail
            label = named[-1] or ".".join(stack)
            out.append(Field(label, text[a:b], a, b))
        if lt < 0:
            break
        sk = _SKIP.match(text, lt)
        if sk:
            pos = sk.end()
            continue
        m = _TAG.match(text, lt)
        if not m:
            raise ValueError(f"malformed XML at {lt}")
        closing, name, attrs, selfclose = m.group(1), m.group(2), m.group(3), m.group(4)
        if closing:
            if stack:
                stack.pop()
                named.pop()
        else:
            path = ".".join(stack + [name])
            label = None
            for am in _ATTR.finditer(text, m.start(3), m.end(3)):
                g = 2 if am.group(2) is not None else 3
                if am.group(1) == "Name" and name == "Data":
                    label = f"{stack[-1] if stack else 'Data'}.{am.group(g)}"
                    continue
                if am.end(g) > am.start(g):
                    out.append(Field(f"{path}@{am.group(1)}", am.group(g), am.start(g), am.end(g)))
            if not selfclose:
                stack.append(name)
                named.append(label)
        pos = m.end()
    return out
