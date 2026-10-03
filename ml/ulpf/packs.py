"""Vendor parser packs: declarative YAML, versioned, hot-reloaded, no code.

A pack refines the core parse of a source:
  match:    which events it applies to (format, syslog app, product/vendor, required fields, regexes)
  extract:  csv_columns (name the columns) and regex rules that split one field into sub-fields
            (each sub-field keeps its exact span, so losslessness is preserved)
  map:      field → OCSF path (strict: only these mappings, no generic aliases)
  values:   value translations (e.g. EventID 4625 → failure) applied when mapping
  class / class_rules, time (field holding the event time), host (field naming the device)
  expect:   OCSF paths a healthy event must have; the share present is the event's fill rate,
            which CausalOps watches to detect parser drift.

Example (FortiGate):
    id: fortinet-fortigate
    version: 1
    vendor: Fortinet
    product: FortiGate
    match: {format: kv, fields_present: [logid, devid]}
    map: {srcip: src_endpoint.ip, dstip: dst_endpoint.ip, action: disposition}
    expect: [src_endpoint.ip, dst_endpoint.ip, disposition]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .model import Field, Parsed, dedupe_names
from .ocsf import get_path

BUILTIN_DIR = Path(__file__).parent / "packs"


class PackError(ValueError):
    pass


@dataclass
class RegexRule:
    field: str
    pattern: re.Pattern
    when: dict[str, re.Pattern] = field(default_factory=dict)


@dataclass
class Pack:
    id: str
    version: int
    vendor: str | None
    product: str | None
    match: dict
    csv_columns: list[str]
    regex: list[RegexRule]
    mapping: dict[str, str]
    values: dict[str, dict[str, str]]
    class_uid: int | None
    class_rules: list[tuple[dict[str, re.Pattern], int]]
    time_field: str | None
    host_field: str | None
    expect: list[str]
    strict: bool
    source_text: str
    priority: int = 50

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    # ── matching ────────────────────────────────────────────────────────────
    def matches(self, parsed: Parsed, text: str) -> bool:
        m = self.match
        if not m:
            return False
        if "format" in m and not any(parsed.format.split("+")[-1] == f for f in _list(m["format"])):
            return False
        for key, value in (("app", parsed.get("syslog.app")), ("product", parsed.product), ("vendor", parsed.vendor)):
            if key in m and not (value and re.search(m[key], value)):
                return False
        names = set(parsed.names())
        if any(f not in names for f in _list(m.get("fields_present", []))):
            return False
        for name, pattern in (m.get("field_regex") or {}).items():
            v = parsed.get(name)
            if v is None or not re.search(pattern, v):
                return False
        if "body_regex" in m and not re.search(m["body_regex"], text):
            return False
        return True

    # ── applying ────────────────────────────────────────────────────────────
    def apply(self, parsed: Parsed) -> Parsed:
        fields = list(parsed.fields)
        if self.csv_columns:
            renamed = []
            for f in fields:
                m = re.fullmatch(r"col(\d+)", f.name)
                if m and int(m.group(1)) <= len(self.csv_columns) and self.csv_columns[int(m.group(1)) - 1]:
                    f = Field(self.csv_columns[int(m.group(1)) - 1], f.text, f.start, f.end)
                renamed.append(f)
            fields = renamed
        for rule in self.regex:
            target = next((f for f in fields if f.name == rule.field), None)
            if target is None:
                continue
            current = {f.name: f.text for f in fields}
            if any(not (current.get(k) is not None and p.search(current[k])) for k, p in rule.when.items()):
                continue
            m = rule.pattern.search(target.text)
            if not m:
                continue
            subs = [Field(name, m.group(name), target.start + m.start(name), target.start + m.end(name))
                    for name in rule.pattern.groupindex if m.group(name) not in (None, "")]
            fields = [f for f in fields if f is not target] + subs
            break  # first matching rule per event
        out = Parsed(parsed.format, dedupe_names(sorted(fields, key=lambda f: f.start)),
                     self.vendor or parsed.vendor, self.product or parsed.product, parsed.version,
                     parser=f"pack:{self.id}", parser_version=str(self.version))
        return out

    def classify(self, parsed: Parsed) -> int | None:
        current = {f.name: f.text for f in parsed.fields}
        for when, cls in self.class_rules:
            if all(current.get(k) is not None and p.search(current[k]) for k, p in when.items()):
                return cls
        return self.class_uid

    def fill(self, event: dict) -> float:
        if not self.expect:
            return 1.0
        return sum(1 for p in self.expect if get_path(event, p) not in (None, "")) / len(self.expect)


def _list(v) -> list:
    return v if isinstance(v, list) else [v]


def _compile_when(d: dict | None) -> dict[str, re.Pattern]:
    return {k: re.compile(str(v)) for k, v in (d or {}).items()}


def load(text: str) -> Pack:
    try:
        d = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise PackError(f"invalid YAML: {e}") from e
    if not isinstance(d, dict) or not d.get("id") or not isinstance(d.get("version"), int):
        raise PackError("a pack needs an 'id' and an integer 'version'")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,79}", d["id"]):
        raise PackError("id must be lower-case letters, digits, '.', '_' or '-'")
    extract = d.get("extract") or {}
    try:
        rules = [RegexRule(r.get("field", "message"), re.compile(r["pattern"]), _compile_when(r.get("when")))
                 for r in extract.get("regex", [])]
        class_rules = [(_compile_when(r.get("when")), int(r["class"])) for r in d.get("class_rules", [])]
    except (re.error, KeyError, TypeError) as e:
        raise PackError(f"bad extract/class rule: {e}") from e
    mapping = {str(k): str(v) for k, v in (d.get("map") or {}).items()}
    values = {str(k): {str(a): str(b) for a, b in (v or {}).items()} for k, v in (d.get("values") or {}).items()}
    return Pack(d["id"], d["version"], d.get("vendor"), d.get("product"), d.get("match") or {},
                list(extract.get("csv_columns") or []), rules, mapping, values,
                int(d["class"]) if d.get("class") is not None else None, class_rules,
                d.get("time"), d.get("host"), list(d.get("expect") or []), bool(d.get("strict", True)), text,
                int(d.get("priority", 50)))


def builtin() -> list[Pack]:
    return [load(p.read_text(encoding="utf-8")) for p in sorted(BUILTIN_DIR.glob("*.yaml"))]


class Registry:
    """Champion packs plus source → pack bindings, as seen by one worker (refreshed from the database)."""

    def __init__(self, packs: list[Pack] | None = None, bindings: dict[str, str] | None = None):
        self.packs: dict[str, Pack] = {p.id: p for p in (packs or [])}
        self.bindings: dict[str, str] = dict(bindings or {})

    def for_event(self, source_id: str, parsed: Parsed, text: str) -> tuple[Pack | None, bool]:
        """Returns (pack, newly_bound). A source bound to a pack keeps using it even when its format
        changes; that is how a silent firmware change shows up as parser drift instead of disappearing."""
        bound = self.bindings.get(source_id)
        if bound and bound in self.packs:
            return self.packs[bound], False
        for pack in sorted(self.packs.values(), key=lambda p: (p.priority, p.id)):
            if pack.matches(parsed, text):
                self.bindings[source_id] = pack.id
                return pack, True
        return None, False
