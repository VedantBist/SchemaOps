"""Automatic pack repair after a format change (the challenger in champion/challenger promotion).

When a bound source drifts, the events it sends now are parsed with the champion pack to find which
expected OCSF attributes went missing. For each one, the fields that appeared in the new events and are
not mapped yet are scored as replacements:
  alias   the field name is a known name for that attribute (OCSF alias table)      +3
  values  ≥ 90% of its values have the attribute's type (IP, port, protocol, action) +2
  name    similarity to the old field name (e.g. srcip → src_ip)                   +0..2
The best candidate above the bar is added to the map. Old mappings stay, so devices still on the old
firmware keep parsing: the repaired pack accepts both formats. The result is only a proposal: it must
beat the champion on the drifted events without regressing on the old ones before it can be promoted.
"""
from __future__ import annotations

import difflib
import ipaddress
import re
from collections import defaultdict

import yaml

from . import packs
from .detect import parse
from .ocsf import ALIASES, ALLOW, DENY, PROTOCOLS, alias_key, get_path
from .pipeline import decode, process

_INT = re.compile(r"^\d+$")


def _type_ok(path: str, value: str) -> bool:
    v = value.strip().strip('"')
    if path.endswith(".ip"):
        try:
            ipaddress.ip_address(v)
            return True
        except ValueError:
            return False
    if path.endswith(".port"):
        return bool(_INT.match(v)) and 0 <= int(v) <= 65535
    if path == "connection_info.protocol_name":
        return v.lower() in PROTOCOLS.values() or v in PROTOCOLS
    if path == "disposition":
        return v.lower() in ALLOW | DENY
    if path.endswith(".mac"):
        return bool(re.match(r"^[0-9a-fA-F]{2}([:-][0-9a-fA-F]{2}){5}$", v))
    return bool(v)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def infer(champion: packs.Pack, drifted: list[str], now, next_version: int | None = None) -> tuple[str | None, list[dict]]:
    """Returns (challenger YAML or None, the mapping changes with their evidence)."""
    results = [process(s.encode(), received_at=now, pack=champion) for s in drifted]
    if not results:
        return None, []
    missing = [p for p in champion.expect
               if sum(get_path(r.event, p) not in (None, "") for r in results) < 0.5 * len(results)]
    reverse = {path: name for name, path in champion.mapping.items()}
    values: dict[str, list[str]] = defaultdict(list)
    for r in results:
        for f in r.parsed.fields:
            if f.name not in champion.mapping and not f.name.startswith("syslog."):
                values[f.name].append(f.text)
    changes = []
    used = set()
    for path in missing:
        best = None
        old = reverse.get(path, "")
        for name, vals in values.items():
            if name in used or len(vals) < 0.5 * len(results):
                continue
            score, why = 0.0, []
            if ALIASES.get(alias_key(name)) == path:
                score += 3
                why.append("known alias")
            ok = sum(_type_ok(path, v) for v in vals) / len(vals)
            if ok >= 0.9:
                score += 2
                why.append(f"{ok:.0%} of values have the right type")
            elif ok < 0.5:
                continue
            if old:
                sim = difflib.SequenceMatcher(None, _norm(old), _norm(name)).ratio()
                score += 2 * sim
                if sim >= 0.6:
                    why.append(f"name similar to '{old}' ({sim:.2f})")
            if score >= 3.5 and (best is None or score > best[0]):
                best = (score, name, why, ok)
        if best:
            used.add(best[1])
            changes.append({"attribute": path, "field": best[1], "replaces": old or None, "score": round(best[0], 2),
                            "evidence": best[2], "sampleValues": values[best[1]][:3]})
    time_change = _time_field(champion, results, values)
    if not changes and not time_change:
        return None, []
    doc = yaml.safe_load(champion.source_text)
    doc["version"] = next_version or champion.version + 1
    doc.setdefault("map", {})
    for c in changes:
        doc["map"][c["field"]] = c["attribute"]
    if time_change:
        doc["time_fallback"] = time_change["field"]
        changes.append(time_change)
    doc["notes"] = "repaired automatically after a format change: " + ", ".join(
        f"{c['field']} → {c['attribute']}" for c in changes)
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=140), changes


def _time_field(champion: packs.Pack, results, values) -> dict | None:
    """If the champion's time field disappeared, pick a new field holding a parseable timestamp."""
    if not champion.time_field:
        return None
    wanted = champion.time_field if isinstance(champion.time_field, list) else [champion.time_field]
    if any(r.parsed.get(wanted[0]) for r in results):
        return None
    from .ocsf import parse_time
    from datetime import datetime, timezone
    ref = datetime.now(timezone.utc)
    for name, vals in values.items():
        if any(k in name.lower() for k in ("time", "date", "ts")) and all(parse_time(v, ref) for v in vals[:20]):
            return {"attribute": "time", "field": name, "replaces": "+".join(wanted), "score": 3.0,
                    "evidence": ["timestamp-shaped values"], "sampleValues": vals[:3]}
    return None


def evaluate(pack_text: str, samples: list[str], now) -> dict:
    """Fill, normalized share and losslessness of a pack on samples (forced, no registry)."""
    p = packs.load(pack_text) if isinstance(pack_text, str) else pack_text
    out = [process(s.encode(), received_at=now, pack=p) for s in samples]
    n = max(len(out), 1)
    return {"events": len(out), "fill": round(sum((r.fill or 0) for r in out) / n, 4),
            "normalized": round(sum(r.status == "NORMALIZED" for r in out) / n, 4),
            "lossless": round(sum(r.lossless for r in out) / n, 4)}


def detect_unmapped(samples: list[str]) -> set[str]:
    names = set()
    for s in samples:
        names |= {f.name for f in parse(decode(s.encode())[0]).fields}
    return names
