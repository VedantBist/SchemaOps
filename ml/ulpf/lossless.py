"""Provable losslessness.

`skeleton()` is the raw text with every extracted field cut out: only the literal text between
fields remains. `reconstruct()` rebuilds the raw event from the skeleton plus the values read back
from the *normalized* OCSF record (through `ulpf.fields`). The rebuilt bytes are hashed and compared
with the SHA-256 stored in the vault, so the check proves the normalized record lost nothing.
"""
from __future__ import annotations

import hashlib

from .model import Field
from .ocsf import get_path


def skeleton(text: str, fields: list[Field]) -> list:
    """Alternating literals (str) and field references (str name wrapped in a 1-item list)."""
    out: list = []
    pos = 0
    for f in sorted(fields, key=lambda f: f.start):
        if f.start < pos:
            raise ValueError(f"overlapping field {f.name}")
        if f.start > pos:
            out.append(text[pos:f.start])
        out.append([f.name])
        pos = f.end
    if pos < len(text):
        out.append(text[pos:])
    return out


def reconstruct(event: dict) -> str:
    parts = []
    locations = event["ulpf"]["fields"]
    for piece in event["ulpf"]["skeleton"]:
        if isinstance(piece, str):
            parts.append(piece)
            continue
        name = piece[0]
        loc = locations.get(name)
        if loc is None:
            value = None
        elif loc.startswith("unmapped:"):
            value = event.get("unmapped", {}).get(loc[len("unmapped:"):])
        else:
            value = get_path(event, loc)
        if value is None:
            raise KeyError(f"field {name} not recoverable from the normalized record")
        parts.append(value if isinstance(value, str) else str(value))
    return "".join(parts)


def verify(event: dict, encoding: str, sha256: str) -> bool:
    try:
        rebuilt = reconstruct(event).encode(encoding, errors="surrogateescape")
    except (KeyError, UnicodeEncodeError):
        return False
    return hashlib.sha256(rebuilt).hexdigest() == sha256
