"""Cross-vendor entity graph.

Every normalized event contributes identifiers (IP, hostname, MAC, user) and links between them:
ip–mac, ip–host and host–mac mean "same asset" (DHCP, Windows, endpoint fields); user–ip means "this
user acted from this address" (authentication, firewall user-ID). The same machine or person seen by
a Palo Alto, a FortiGate, a domain controller and a DHCP server becomes one connected entity.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

SAME_ASSET = ("ip-mac", "ip-host", "host-mac")
_SKIP_IPS = {"0.0.0.0", "::", "255.255.255.255"}


def _key(kind: str, value) -> str | None:
    if value in (None, ""):
        return None
    v = str(value).strip()
    if kind == "ip" and v in _SKIP_IPS:
        return None
    if kind in ("host", "mac", "user"):
        v = v.lower()
    return f"{kind}:{v}"[:300]


def identifiers(event: dict) -> tuple[list[tuple[str, str, str, dict]], list[tuple[str, str, str]]]:
    """Returns ([(key, kind, value, extra)], [(a, b, link_kind)]) for one OCSF event."""
    nodes, links = [], []
    for ep in ("src_endpoint", "dst_endpoint"):
        node = event.get(ep) or {}
        ip, host, mac = _key("ip", node.get("ip")), _key("host", node.get("hostname")), _key("mac", node.get("mac"))
        if ip:
            nodes.append((ip, "ip", node["ip"], {"country": (node.get("location") or {}).get("country"),
                                                 "is_private": node.get("is_private")}))
        if host:
            nodes.append((host, "host", node["hostname"].lower(), {}))
        if mac:
            nodes.append((mac, "mac", node["mac"].lower(), {}))
        for a, b, kind in ((ip, mac, "ip-mac"), (ip, host, "ip-host"), (host, mac, "host-mac")):
            if a and b:
                links.append((a, b, kind))
    user = _key("user", ((event.get("actor") or {}).get("user") or {}).get("name"))
    if user:
        nodes.append((user, "user", user[5:], {}))
        src_ip = _key("ip", (event.get("src_endpoint") or {}).get("ip"))
        if src_ip:
            links.append((user, src_ip, "user-ip"))
    return nodes, links


class Batch:
    """Aggregates one worker batch so the database sees one upsert per entity and link."""

    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.links: dict[tuple[str, str], dict] = {}

    def add(self, event: dict, source_id: str, at: datetime) -> None:
        nodes, links = identifiers(event)
        for key, kind, value, extra in nodes:
            n = self.nodes.setdefault(key, {"kind": kind, "value": value[:290], "events": 0, "sources": set(),
                                            "first": at, "last": at, **extra})
            n["events"] += 1
            n["sources"].add(source_id)
            n["last"] = max(n["last"], at)
        for a, b, kind in links:
            k = (a, b) if a <= b else (b, a)
            link = self.links.setdefault(k, {"kind": kind, "events": 0, "sources": set(), "first": at, "last": at})
            link["events"] += 1
            link["sources"].add(source_id)
            link["last"] = max(link["last"], at)

    def flush(self, cur) -> None:
        for key, n in sorted(self.nodes.items()):
            cur.execute("""
                INSERT INTO entities (key, kind, value, country, is_private, events, sources, first_seen, last_seen)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET events = entities.events + EXCLUDED.events,
                    last_seen = GREATEST(entities.last_seen, EXCLUDED.last_seen),
                    country = COALESCE(EXCLUDED.country, entities.country),
                    sources = ARRAY(SELECT DISTINCT unnest(entities.sources || EXCLUDED.sources))""",
                        (key, n["kind"], n["value"], n.get("country"), n.get("is_private"), n["events"],
                         sorted(n["sources"]), n["first"], n["last"]))
        for (a, b), link in sorted(self.links.items()):
            cur.execute("""
                INSERT INTO entity_links (a, b, kind, events, sources, first_seen, last_seen)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (a, b) DO UPDATE SET events = entity_links.events + EXCLUDED.events,
                    last_seen = GREATEST(entity_links.last_seen, EXCLUDED.last_seen),
                    sources = ARRAY(SELECT DISTINCT unnest(entity_links.sources || EXCLUDED.sources))""",
                        (a, b, link["kind"], link["events"], sorted(link["sources"]), link["first"], link["last"]))


def asset_group(links: list[dict], start: str) -> set[str]:
    """Identifiers connected to `start` through same-asset links (BFS)."""
    adj: dict[str, set[str]] = defaultdict(set)
    for link in links:
        if link["kind"] in SAME_ASSET:
            adj[link["a"]].add(link["b"])
            adj[link["b"]].add(link["a"])
    seen, todo = {start}, [start]
    while todo:
        for nxt in adj[todo.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen
