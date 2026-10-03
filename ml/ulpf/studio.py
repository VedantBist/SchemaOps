"""Parser Studio: onboard an unknown log source without writing code.

1. analyze(): core-parse the samples, show which pack (if any) matches, and propose a pack:
   - free-text bodies → Drain3 template mining; each template becomes a regex rule whose parameters
     are named from their context ("from <ip>" → src_ip, "port <n>" → src_port, "user <x>" → user)
   - structured bodies (key=value, JSON, CSV, CEF/LEEF) → field names mapped through the OCSF alias
     table, CSV columns named by value type (first IP → src, second IP → dst, ...)
2. test(): run a candidate pack on the samples and compare it with the current champion
   (normalized share, fill rate, losslessness), before anyone activates it.
"""
from __future__ import annotations

import ipaddress
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

import yaml

from . import packs
from .detect import parse
from .ocsf import ALIASES, alias_key
from .pipeline import decode, process

_INT = re.compile(r"^\d+$")
_MAC = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")
_SRC_WORDS = {"from", "src", "source", "client", "rhost", "by", "srcip"}
_DST_WORDS = {"to", "dst", "destination", "dest", "server", "target", "dstip"}
_USER_WORDS = {"user", "username", "for", "account", "login", "usr", "uid"}
_OCSF = {"src_ip": "src_endpoint.ip", "dst_ip": "dst_endpoint.ip", "src_port": "src_endpoint.port",
         "dst_port": "dst_endpoint.port", "user": "actor.user.name", "mac": "src_endpoint.mac",
         "src_host": "src_endpoint.hostname", "protocol": "connection_info.protocol_name", "action": "disposition"}
_ACTIONS = {"accept", "accepted", "allow", "allowed", "deny", "denied", "drop", "dropped", "block", "blocked",
            "reject", "rejected", "failed", "success", "succeeded", "opened", "closed", "pass"}
_FAIL = {"deny", "denied", "drop", "dropped", "block", "blocked", "reject", "rejected", "failed"}
_AUTH = re.compile(r"(?i)(login|logon|auth|password|session|vpn|user)")


def _is_ip(v: str) -> bool:
    try:
        ipaddress.ip_address(v)
        return True
    except ValueError:
        return False


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60] or "custom"


# ── analysis ────────────────────────────────────────────────────────────────
def analyze(samples: list[str], registry: packs.Registry | None, source_hint: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    rows, parsed_list = [], []
    for s in samples:
        res = process(s.encode(), received_at=now, source_hint=source_hint, registry=registry)
        core = parse(decode(s.encode())[0])
        parsed_list.append(core)
        rows.append({"sample": s[:400], "format": core.format, "pack": res.pack.ref if res.pack else None,
                     "status": res.status, "fill": res.fill, "lossless": res.lossless, "classUid": res.event["class_uid"]})
    formats = Counter(p.format.split("+")[-1] for p in parsed_list)
    body_format = formats.most_common(1)[0][0] if formats else "text"
    apps = {p.get("syslog.app") for p in parsed_list}
    app = next(iter(apps)) if len(apps) == 1 else None
    if body_format == "text":
        proposal = _propose_text(parsed_list, app, source_hint)
    else:
        proposal = _propose_structured(parsed_list, body_format, app, source_hint)
    return {"samples": rows, "bodyFormat": body_format, "proposal": proposal}


def _propose_text(parsed_list, app, source_hint) -> dict:
    from drain3 import TemplateMiner
    from drain3.template_miner_config import TemplateMinerConfig

    config = TemplateMinerConfig()
    config.profiling_enabled = False
    config.drain_sim_th = 0.5
    config.drain_depth = 4
    miner = TemplateMiner(config=config)
    examples: dict[int, list[str]] = defaultdict(list)
    for p in parsed_list:
        msg = p.get("message")
        if not msg:
            continue
        result = miner.add_log_message(msg)
        examples[result["cluster_id"]].append(msg)
    rules, mapped_names, templates, per_template = [], set(), [], []
    for cluster in sorted(miner.drain.clusters, key=lambda c: -c.size):
        template = cluster.get_template()
        msgs = examples.get(cluster.cluster_id, [])
        rule = _template_regex(template, msgs)
        if rule is None:
            continue
        pattern, names = rule
        templates.append({"template": template, "count": cluster.size, "pattern": pattern, "fields": names})
        rules.append({"pattern": pattern})
        mapped_names.update(n for n in names if n in _OCSF)
        per_template.append({n for n in names if n in _OCSF})
    pid = _slug(f"custom-{app or source_hint or 'text'}")
    mapping = {n: _OCSF[n] for n in sorted(mapped_names)}
    has_user = "user" in mapping
    cls = 3002 if has_user and any(_AUTH.search(t["template"]) for t in templates) else 4001 if "src_ip" in mapping else 0
    # Expected attributes: those every template provides (a healthy event of any kind has them).
    common = set.intersection(*per_template) if per_template else set()
    doc = {"id": pid, "version": 1, "vendor": None, "product": app, "priority": 20,
           "match": {"app": f"^{re.escape(app)}$"} if app else {"format": "text"},
           "extract": {"regex": rules}, "class": cls, "map": mapping,
           "values": {"action": {w: ("failure" if w in _FAIL else "success") for w in sorted(_ACTIONS)
                                 if any(w in t["template"].lower().split() for t in templates)}} if "action" in mapping else None,
           "expect": [mapping[n] for n in ("user", "src_ip", "action", "dst_ip") if n in common]}
    return {"kind": "drain", "templates": templates, "yaml": _dump(doc)}


def _template_regex(template: str, msgs: list[str]):
    """Turns a Drain template into a named-group regex, checked against the cluster's own messages."""
    tokens = template.split()
    params: list[list[str]] = [[] for t in tokens if t == "<*>"]
    for m in msgs:
        parts = m.split()
        if len(parts) != len(tokens):
            continue
        k = 0
        for tok, val in zip(tokens, parts):
            if tok == "<*>":
                params[k].append(val)
                k += 1
    pieces, names, used = [], [], Counter()
    k = 0
    last_ip_role = "src"
    for i, tok in enumerate(tokens):
        if tok != "<*>":
            word = tok.lower().strip(".,:;")
            if word in _ACTIONS and "action" not in used and word == tok.lower():
                # A literal outcome word ("failed", "opened") is the event's disposition: capture it.
                used["action"] += 1
                names.append("action")
                pieces.append(f"(?P<action>{re.escape(tok)})")
            else:
                pieces.append(re.escape(tok))
            continue
        values = params[k] if k < len(params) else []
        k += 1
        prev = re.sub(r"[^a-z]", "", tokens[i - 1].lower()) if i > 0 else ""
        lead, trail = _common_affixes(values)
        core = [v[len(lead):len(v) - len(trail) if trail else None] for v in values]
        name = _name(prev, core, used, last_ip_role)
        if name in ("src_ip", "dst_ip"):
            last_ip_role = name[:3]
        used[name] += 1
        if used[name] > 1:
            name = f"{name}_{used[name]}"
        names.append(name)
        group = r"\d+" if core and all(_INT.match(v) for v in core) else r"[^\s]+?" if trail else r"\S+"
        pieces.append(re.escape(lead) + f"(?P<{name}>{group})" + re.escape(trail))
    pattern = "^" + r"\s+".join(pieces) + "$"
    try:
        rx = re.compile(pattern)
    except re.error:
        return None
    if msgs and sum(1 for m in msgs if rx.search(m)) < 0.9 * len(msgs):
        pattern = pattern.rstrip("$")  # tolerate trailing variation
    return pattern, names


def _common_affixes(values: list[str]) -> tuple[str, str]:
    if not values:
        return "", ""
    lead = ""
    for chars in zip(*values):
        if len(set(chars)) == 1 and not chars[0].isalnum():
            lead += chars[0]
        else:
            break
    trail = ""
    for chars in zip(*[v[::-1] for v in values]):
        if len(set(chars)) == 1 and not chars[0].isalnum():
            trail = chars[0] + trail
        else:
            break
    if any(len(v) <= len(lead) + len(trail) for v in values):
        return "", ""
    return lead, trail


def _name(prev: str, values: list[str], used: Counter, last_ip_role: str) -> str:
    if values and all(_is_ip(v) for v in values):
        if prev in _DST_WORDS:
            return "dst_ip"
        if prev in _SRC_WORDS or "src_ip" not in used:
            return "src_ip"
        return "dst_ip"
    if values and all(_INT.match(v) for v in values):
        if prev == "port":
            return f"{last_ip_role}_port"
        return f"{prev or 'num'}"[:30] if prev else "num"
    if values and all(_MAC.match(v) for v in values):
        return "mac"
    if prev in _USER_WORDS:
        return "user"
    if values and all(v.lower() in _ACTIONS for v in values):
        return "action"
    if prev in ("proto", "protocol"):
        return "protocol"
    return re.sub(r"[^a-z0-9_]", "", prev)[:30] or "field"


def _propose_structured(parsed_list, body_format, app, source_hint) -> dict:
    names = Counter()
    values: dict[str, list[str]] = defaultdict(list)
    for p in parsed_list:
        for f in p.fields:
            if not f.name.startswith("syslog."):
                names[f.name] += 1
                values[f.name].append(f.text)
    mapping, csv_columns = {}, []
    if body_format == "csv":
        width = max((int(n[3:]) for n in names if re.fullmatch(r"col\d+", n)), default=0)
        ips = 0
        for i in range(1, width + 1):
            col_values = values.get(f"col{i}", [])
            label = f"col{i}"
            if col_values and all(_is_ip(v) for v in col_values) and ips < 2:
                label = ["src_ip", "dst_ip"][ips]
                ips += 1
                mapping[label] = _OCSF[label]
            elif col_values and all(v.lower() in _ACTIONS for v in col_values) and "action" not in mapping:
                label = "action"
                mapping[label] = _OCSF[label]
            csv_columns.append(label)
    else:
        for n in names:
            path = ALIASES.get(alias_key(n))
            if path and path not in mapping.values():
                mapping[n] = path
    vendors = {p.vendor for p in parsed_list if p.vendor}
    products = {p.product for p in parsed_list if p.product}
    pid = _slug(f"custom-{(next(iter(products)) if len(products) == 1 else None) or app or source_hint or body_format}")
    match: dict = {"format": body_format}
    if app:
        match["app"] = f"^{re.escape(app)}$"
    elif vendors and len(vendors) == 1:
        match["vendor"] = f"^{re.escape(next(iter(vendors)))}$"
    common = [n for n, c in names.items() if c == len(parsed_list)]
    if body_format in ("kv", "json") and common:
        match["fields_present"] = sorted(common)[:3]
    paths = list(mapping.values())
    doc = {"id": pid, "version": 1, "vendor": next(iter(vendors)) if len(vendors) == 1 else None,
           "product": next(iter(products)) if len(products) == 1 else app, "priority": 20, "match": match,
           "class": 4001 if "src_endpoint.ip" in paths else 0, "map": mapping,
           "expect": [p for p in ("src_endpoint.ip", "dst_endpoint.ip", "disposition", "actor.user.name") if p in paths][:4]}
    if csv_columns:
        doc["extract"] = {"csv_columns": csv_columns}
    return {"kind": "auto-map", "templates": [], "yaml": _dump(doc)}


def _dump(doc: dict) -> str:
    doc = {k: v for k, v in doc.items() if v not in (None, {}, [])}
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=140)


# ── test bench ──────────────────────────────────────────────────────────────
def test(text: str, samples: list[str], champion: packs.Pack | None) -> dict:
    candidate = packs.load(text)
    now = datetime.now(timezone.utc)

    def run(pack):
        out = []
        for s in samples:
            r = process(s.encode(), received_at=now, pack=pack)
            out.append(r)
        n = max(len(out), 1)
        return out, {"normalizedPct": round(100 * sum(r.status == "NORMALIZED" for r in out) / n, 2),
                     "fillPct": round(100 * sum((r.fill or 0) for r in out) / n, 2),
                     "losslessPct": round(100 * sum(r.lossless for r in out) / n, 2),
                     "matchPct": round(100 * sum(pack.matches(parse(decode(s.encode())[0]), s) for s in samples) / n, 2)}

    results, summary = run(candidate)
    comparison = run(champion)[1] if champion is not None and champion.id == candidate.id else None
    details = [{"sample": s[:400], "status": r.status, "fill": r.fill, "lossless": r.lossless,
                "classUid": r.event["class_uid"],
                "fields": [{"name": f.name, "value": f.text} for f in r.parsed.fields if not f.name.startswith("syslog.")][:40],
                "ocsf": {k: r.event.get(k) for k in ("class_name", "src_endpoint", "dst_endpoint", "actor", "disposition",
                                                     "action", "connection_info") if r.event.get(k) is not None}}
               for s, r in zip(samples, results)]
    return {"pack": candidate.ref, "summary": summary, "champion": comparison, "details": details}
