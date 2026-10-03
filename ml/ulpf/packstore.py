"""Pack versions in Postgres: built-in seeding, champion lookup, saving new versions, source bindings."""
from __future__ import annotations

import json

from . import packs
from .store import Store


def seed_builtin(store: Store) -> int:
    """Inserts bundled packs that are not in the database yet; a pack id without a champion gets one."""
    added = 0
    for p in packs.builtin():
        exists = store.rows("SELECT 1 FROM parser_packs WHERE id = %s AND version = %s", (p.id, p.version))
        if exists:
            continue
        has_champion = store.rows("SELECT version FROM parser_packs WHERE id = %s AND status = 'CHAMPION'", (p.id,))
        newer = has_champion and has_champion[0]["version"] > p.version
        if has_champion and not newer:
            store.execute("UPDATE parser_packs SET status = 'RETIRED' WHERE id = %s AND status = 'CHAMPION'", (p.id,))
        store.execute("""INSERT INTO parser_packs (id, version, status, vendor, product, yaml, origin, created_by)
                         VALUES (%s, %s, %s, %s, %s, %s, 'BUILTIN', 'causalops')""",
                      (p.id, p.version, "RETIRED" if newer else "CHAMPION", p.vendor, p.product, p.source_text))
        added += 1
    return added


def registry(store: Store) -> packs.Registry:
    rows = store.rows("SELECT id, version, yaml FROM parser_packs WHERE status = 'CHAMPION'")
    loaded = []
    for r in rows:
        try:
            loaded.append(packs.load(r["yaml"]))
        except packs.PackError:
            continue  # a broken stored pack must not stop the pipeline; the studio shows it
    bindings = {r["id"]: r["pack_id"] for r in store.rows("SELECT id, pack_id FROM log_sources WHERE pack_id IS NOT NULL")}
    return packs.Registry(loaded, bindings)


def save(store: Store, text: str, *, status: str, origin: str, created_by: str | None, notes: str | None,
         test: dict | None) -> packs.Pack:
    p = packs.load(text)
    latest = store.rows("SELECT max(version) AS v FROM parser_packs WHERE id = %s", (p.id,))[0]["v"]
    if latest is not None and p.version <= latest:
        raise packs.PackError(f"version must be greater than {latest} (the latest stored version of {p.id})")
    with store.conn().transaction(), store.conn().cursor() as cur:
        if status == "CHAMPION":
            cur.execute("UPDATE parser_packs SET status = 'RETIRED' WHERE id = %s AND status = 'CHAMPION'", (p.id,))
        cur.execute("""INSERT INTO parser_packs (id, version, status, vendor, product, yaml, origin, created_by, notes, test)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (p.id, p.version, status, p.vendor, p.product, text, origin, created_by, notes,
                     json.dumps(test) if test is not None else None))
    return p


def activate(store: Store, pack_id: str, version: int) -> None:
    rows = store.rows("SELECT 1 FROM parser_packs WHERE id = %s AND version = %s", (pack_id, version))
    if not rows:
        raise LookupError(f"no pack {pack_id}@{version}")
    with store.conn().transaction(), store.conn().cursor() as cur:
        cur.execute("UPDATE parser_packs SET status = 'RETIRED' WHERE id = %s AND status = 'CHAMPION'", (pack_id,))
        cur.execute("UPDATE parser_packs SET status = 'CHAMPION' WHERE id = %s AND version = %s", (pack_id, version))


def bind(store: Store, source_id: str, pack_id: str | None) -> None:
    store.execute("UPDATE log_sources SET pack_id = %s, pack_bound_at = now() WHERE id = %s", (pack_id, source_id))
