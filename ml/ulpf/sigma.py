"""Sigma detection rules evaluated on OCSF, so one rule covers every vendor.

Supported subset of the Sigma format:
  logsource.category   authentication · network_connection · firewall · webserver · dns · finding · dhcp
  detection.<name>     map of field → value(s); keys are ANDed, list values ORed; modifiers
                       |contains |startswith |endswith |re |gte |lte |gt |lt |cidr |all
  detection.condition  names joined with and / or / not and parentheses, optionally followed by an
                       aggregation:  | count() by <field> >= N   or   | count(<field>) by <field> >= N
                       (count(<field>) counts distinct values, as in Sigma)
  timeframe            window of the aggregation, e.g. 2m, 30s, 1h
Field names are OCSF paths (src_endpoint.ip) or common Sigma names (src_ip, dst_port, user, action).
"""
from __future__ import annotations

import ipaddress
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .ocsf import get_path

RULE_DIR = Path(__file__).parent / "sigma"
CATEGORY = {"authentication": {3002}, "network_connection": {4001}, "firewall": {4001}, "webserver": {4002},
            "dns": {4003}, "finding": {2004}, "ids": {2004}, "dhcp": {4004}}
FIELD = {"src_ip": "src_endpoint.ip", "dst_ip": "dst_endpoint.ip", "src_port": "src_endpoint.port",
         "dst_port": "dst_endpoint.port", "user": "actor.user.name", "action": "action",
         "src_is_private": "src_endpoint.is_private", "dst_is_private": "dst_endpoint.is_private",
         "protocol": "connection_info.protocol_name", "signature": "finding_info.title",
         "url": "http_request.url.url_string", "query": "query.hostname", "bytes_out": "traffic.bytes_out",
         "src_country": "src_endpoint.location.country", "product": "metadata.product.name"}
_AGG = re.compile(r"^\s*count\(\s*([\w.]*)\s*\)\s*(?:by\s+([\w.]+))?\s*(>=|>|==|<=|<)\s*(\d+)\s*$")


def _units(t: str | None) -> int:
    if not t:
        return 0
    n, u = int(t[:-1]), t[-1]
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[u] * 1000


def _path(name: str) -> str:
    return FIELD.get(name, name)


@dataclass
class Rule:
    id: str
    title: str
    level: str
    tags: list[str]
    classes: set[int] | None
    selections: dict[str, list[list[tuple]]]
    condition: str
    agg: tuple | None
    window_ms: int
    text: str
    description: str = ""
    windows: dict = field(default_factory=lambda: defaultdict(deque))
    fired: dict = field(default_factory=dict)

    def match(self, e: dict) -> bool:
        if self.classes and e.get("class_uid") not in self.classes:
            return False
        names = {n: any(all(_test(e, p, mod, vals, every) for p, mod, vals, every in conj) for conj in alts)
                 for n, alts in self.selections.items()}
        return _eval(self.condition, names)


def _test(e: dict, path: str, mod: str, vals: list, every: bool = False) -> bool:
    v = get_path(e, path)
    if v is None:
        return any(x is None for x in vals)
    sv = str(v)
    checks = []
    for x in vals:
        xs = str(x)
        if mod == "contains":
            checks.append(xs.lower() in sv.lower())
        elif mod == "startswith":
            checks.append(sv.lower().startswith(xs.lower()))
        elif mod == "endswith":
            checks.append(sv.lower().endswith(xs.lower()))
        elif mod == "re":
            checks.append(re.search(xs, sv) is not None)
        elif mod in ("gte", "lte", "gt", "lt"):
            try:
                a, b = float(v), float(x)
            except (TypeError, ValueError):
                checks.append(False)
                continue
            checks.append({"gte": a >= b, "lte": a <= b, "gt": a > b, "lt": a < b}[mod])
        elif mod == "cidr":
            try:
                checks.append(ipaddress.ip_address(sv) in ipaddress.ip_network(xs, strict=False))
            except ValueError:
                checks.append(False)
        elif isinstance(v, bool):
            checks.append(v == (x is True or str(x).lower() == "true"))
        else:
            checks.append(sv.lower() == xs.lower())
    return all(checks) if every else any(checks)


def _eval(cond: str, names: dict[str, bool]) -> bool:
    tokens = re.findall(r"\(|\)|\w+", cond)
    expr = []
    for t in tokens:
        if t in ("and", "or", "not", "(", ")"):
            expr.append(t)
        elif t in names:
            expr.append("True" if names[t] else "False")
        elif t == "1" or t == "of":
            continue
        else:
            raise ValueError(f"unknown name in condition: {t}")
    return bool(eval(" ".join(expr), {"__builtins__": {}}, {}))  # tokens are only True/False/and/or/not/()


def load(text: str) -> Rule:
    d = yaml.safe_load(text)
    det = dict(d["detection"])
    condition = det.pop("condition")
    main, _, agg_s = condition.partition("|")
    agg = None
    if agg_s.strip():
        m = _AGG.match(agg_s)
        if not m:
            raise ValueError(f"unsupported aggregation: {agg_s}")
        agg = (_path(m.group(1)) if m.group(1) else None, _path(m.group(2)) if m.group(2) else None, m.group(3), int(m.group(4)))
    selections = {}
    for name, body in det.items():
        alts = body if isinstance(body, list) else [body]
        conjs = []
        for alt in alts:
            conj = []
            for key, val in alt.items():
                fname, *mods = key.split("|")
                every = "all" in mods
                mods = [m for m in mods if m != "all"]
                conj.append((_path(fname), mods[0] if mods else "eq", val if isinstance(val, list) else [val], every))
            conjs.append(conj)
        selections[name] = conjs
    cat = (d.get("logsource") or {}).get("category")
    return Rule(d["id"], d["title"], d.get("level", "medium"), d.get("tags", []), CATEGORY.get(cat) if cat else None,
                selections, main.strip(), agg, _units(d.get("timeframe")), text, d.get("description", ""))


def builtin() -> list[Rule]:
    return [load(p.read_text(encoding="utf-8")) for p in sorted(RULE_DIR.glob("*.yml"))]


class Engine:
    """Streams events through the rules. Returns hits; aggregation windows slide on event time."""

    def __init__(self, rules: list[Rule]):
        self.rules = rules

    def process(self, events: list[dict]) -> list[dict]:
        hits = []
        for e in events:
            for r in self.rules:
                if not r.match(e):
                    continue
                if r.agg is None:
                    key = f"{get_path(e, 'src_endpoint.ip') or '-'}"
                    hits.append(_hit(r, key, [e], 1, None))
                    continue
                distinct_f, by, op, n = r.agg
                key = str(get_path(e, by)) if by else "*"
                w = r.windows[key]
                w.append(e)
                while w and e["time"] - w[0]["time"] > r.window_ms:
                    w.popleft()
                count = len({str(get_path(x, distinct_f)) for x in w}) if distinct_f else len(w)
                ok = {">=": count >= n, ">": count > n, "==": count == n, "<=": count <= n, "<": count < n}[op]
                last = r.fired.get(key)
                if ok and (last is None or e["time"] - last > r.window_ms):
                    r.fired[key] = e["time"]
                    hits.append(_hit(r, key, list(w), len(w), count if distinct_f else None))
        return hits


def _hit(r: Rule, key: str, evs: list[dict], count: int, distinct: int | None) -> dict:
    vendors = sorted({(x.get("metadata", {}).get("product") or {}).get("vendor_name") or
                      (x.get("metadata", {}).get("product") or {}).get("name") or "?" for x in evs})
    return {"rule_id": r.id, "rule_title": r.title, "level": r.level, "group_key": key[:300], "count": count,
            "distinct_count": distinct, "sources": sorted({x["ulpf"]["source_id"] for x in evs}), "vendors": vendors,
            "sample_uids": [x["ulpf"].get("uid") for x in evs[-20:]],
            "first_seen": min(x["time"] for x in evs), "last_seen": max(x["time"] for x in evs)}
