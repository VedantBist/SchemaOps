"""Shared types: an extracted field keeps its exact position in the raw text."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Field:
    name: str
    text: str          # exact characters from the raw event (escapes and quoting stripped only at the edges)
    start: int         # offset in the decoded raw text
    end: int


@dataclass
class Parsed:
    """The outcome of parsing one raw event. Fields never overlap and are sorted by position."""
    format: str                       # syslog-5424+cef, syslog-3164+kv, json, csv, ...
    fields: list[Field] = field(default_factory=list)
    vendor: str | None = None
    product: str | None = None
    version: str | None = None
    parser: str = "core"              # which parser produced it (core:<format> or pack:<id>)
    parser_version: str = "1"

    def get(self, name: str) -> str | None:
        for f in self.fields:
            if f.name == name:
                return f.text
        return None

    def names(self) -> list[str]:
        return [f.name for f in self.fields]


def dedupe_names(fields: list[Field]) -> list[Field]:
    """Keeps field names unique (a second 'src' becomes 'src#2') so every value stays addressable."""
    seen: dict[str, int] = {}
    out = []
    for f in fields:
        n = seen.get(f.name, 0) + 1
        seen[f.name] = n
        out.append(f if n == 1 else Field(f"{f.name}#{n}", f.text, f.start, f.end))
    return out
