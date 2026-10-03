"""Key=value (FortiGate, Check Point, Juniper, Sophos, generic) and delimited CSV (PAN-OS, pfSense)."""
from __future__ import annotations

import re

from ..model import Field

_KV = re.compile(r'(?:(?<=[\s,;])|^)([A-Za-z_][A-Za-z0-9_.:/-]{0,63})=(?:"((?:[^"\\]|\\.)*)"|\'([^\']*)\'|([^\s,;"]*))')


def kv_pairs(text: str, start: int) -> list[re.Match]:
    return list(_KV.finditer(text, start))


def parse_kv(text: str, start: int) -> list[Field]:
    out = []
    for m in kv_pairs(text, start):
        for g in (2, 3, 4):
            if m.group(g) is not None:
                if m.end(g) > m.start(g):
                    out.append(Field(m.group(1), m.group(g), m.start(g), m.end(g)))
                break
    return out


def split_csv(text: str, start: int, delim: str = ",") -> list[tuple[int, int]]:
    """Field spans (without surrounding quotes) of one delimited record starting at `start`."""
    spans = []
    i = start
    n = len(text)
    while True:
        if i < n and text[i] == '"':
            j = i + 1
            while j < n:
                if text[j] == '"':
                    if j + 1 < n and text[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            spans.append((i + 1, j))
            i = j + 1
            while i < n and text[i] != delim:
                i += 1
        else:
            j = text.find(delim, i)
            j = n if j < 0 else j
            end = j
            while end > i and text[end - 1] in "\r\n":
                end -= 1
            spans.append((i, end))
            i = j
        if i >= n or text[i] != delim:
            return spans
        i += 1


def parse_csv(text: str, start: int, names: list[str] | None = None) -> list[Field]:
    out = []
    for idx, (a, b) in enumerate(split_csv(text, start)):
        if b > a:
            name = names[idx] if names and idx < len(names) and names[idx] else f"col{idx + 1}"
            out.append(Field(name, text[a:b], a, b))
    return out
