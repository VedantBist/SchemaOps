"""Signed bundles for one-way (data-diode) transfer and CERT-In evidence packs.

A bundle is a tar.gz with:
  raw.jsonl        every selected event's exact raw bytes (base64) with uid, source, SHA-256 and vault position
  events.parquet   the normalized OCSF records
  packs.json       the parser pack versions that produced them (with SHA-256 of each pack's YAML)
  chain.json       for every vault segment touched: writer, previous head and sealed head (chain anchors)
  manifest.json    SHA-256 of every file, counts, time window, purpose, creator, signing key id
  manifest.sig     Ed25519 signature over manifest.json
  signer.pub       the signer's public key (trust comes from the receiver's own trusted-key list, not from this file)
Import verifies, in order: the signature with a trusted key, every file hash, every raw event's SHA-256, then
(optionally) feeds the raw events into the receiving pipeline, which re-proves losslessness on its side.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import vault

KEY_DIR = Path(os.environ.get("ULPF_KEY_DIR", "/var/lib/ulpf/keys"))
BUNDLE_DIR = Path(os.environ.get("ULPF_BUNDLE_DIR", "/var/lib/ulpf/bundles"))


def signing_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    path = KEY_DIR / "bundle-signing.key"
    if not path.exists():
        key = Ed25519PrivateKey.generate()
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
        os.chmod(path, 0o600)
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def public_pem(key=None) -> bytes:
    from cryptography.hazmat.primitives import serialization
    key = key or signing_key()
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def key_id(pub_pem: bytes) -> str:
    return "ed25519:" + hashlib.sha256(pub_pem).hexdigest()[:16]


def trusted_keys() -> dict[str, bytes]:
    """This instance's own key plus any PEM placed in <key dir>/trusted/ (keys of sending sites)."""
    keys = {}
    own = public_pem()
    keys[key_id(own)] = own
    for p in sorted((KEY_DIR / "trusted").glob("*.pem")) if (KEY_DIR / "trusted").exists() else []:
        pem = p.read_bytes()
        keys[key_id(pem)] = pem
    return keys


def create(store, *, source: str | None, since: datetime, until: datetime, purpose: str, created_by: str,
           vault_dir: str, limit: int = 200_000, incident: dict | None = None) -> dict:
    import pyarrow as pa
    import pyarrow.parquet as pq
    where, params = ["received_at >= %s", "received_at <= %s"], [since, until]
    if source:
        where.append("source_id = %s")
        params.append(source)
    rows = store.rows("SELECT uid, source_id, received_at, segment, vault_offset, sha256, chain, parser, event FROM ulpf_events WHERE "
                      + " AND ".join(where) + " ORDER BY received_at, uid LIMIT %s", tuple(params + [limit]))
    raw_lines, ocsf, packs_used, segments = [], [], set(), set()
    for r in rows:
        rec = vault.read(vault_dir, r["segment"], r["vault_offset"])
        raw_lines.append(json.dumps({"uid": r["uid"], "source": r["source_id"], "received_at": r["received_at"].isoformat(),
                                     "sha256": r["sha256"], "segment": r["segment"], "offset": r["vault_offset"],
                                     "chain": r["chain"], "raw": base64.b64encode(rec["raw"]).decode()}, separators=(",", ":")))
        e = dict(r["event"])
        meta = dict(e.get("ulpf") or {})
        for k in ("skeleton",):
            meta.pop(k, None)
        e["ulpf"] = meta
        ocsf.append(json.dumps(e, separators=(",", ":"), default=str))
        packs_used.add(r["parser"])
        segments.add(r["segment"])
    files: dict[str, bytes] = {"raw.jsonl": ("\n".join(raw_lines) + "\n").encode() if raw_lines else b""}
    table = pa.table({"uid": [r["uid"] for r in rows], "source_id": [r["source_id"] for r in rows],
                      "sha256": [r["sha256"] for r in rows], "ocsf": ocsf})
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    files["events.parquet"] = buf.getvalue()
    pack_rows = []
    for p in sorted(x for x in packs_used if x.startswith("pack:")):
        pid, _, ver = p[5:].partition("@")
        got = store.rows("SELECT yaml FROM parser_packs WHERE id = %s AND version = %s", (pid, int(ver or 0)))
        if got:
            pack_rows.append({"pack": p[5:], "sha256": hashlib.sha256(got[0]["yaml"].encode()).hexdigest(), "yaml": got[0]["yaml"]})
    files["packs.json"] = json.dumps(pack_rows, indent=1).encode()
    chain = []
    for seg in sorted(segments):
        meta = vault.Vault._read_meta(Path(vault_dir) / f"{seg}.seg")
        chain.append({"segment": seg, "prev": meta.get("prev"), "head": meta.get("head"), "sealed": "sealed" in meta})
    files["chain.json"] = json.dumps(chain, indent=1).encode()
    key = signing_key()
    pub = public_pem(key)
    manifest = {
        "format": "causalops-ulpf-bundle/1", "purpose": purpose, "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": created_by, "source": source, "window": [since.isoformat(), until.isoformat()],
        "events": len(rows), "truncated": len(rows) >= limit, "incident": incident,
        "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in files.items()},
        "signer": key_id(pub),
    }
    manifest_bytes = json.dumps(manifest, indent=1, sort_keys=True).encode()
    files["manifest.json"] = manifest_bytes
    files["manifest.sig"] = base64.b64encode(key.sign(manifest_bytes))
    files["signer.pub"] = pub
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"ulpf-{purpose.lower()}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{len(rows)}.tar.gz"
    path = BUNDLE_DIR / name
    with tarfile.open(path, "w:gz") as tar:
        for fname, data in files.items():
            info = tarfile.TarInfo(fname)
            info.size, info.mtime = len(data), int(time.time())
            tar.addfile(info, io.BytesIO(data))
    blob = path.read_bytes()
    return {"file": name, "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest(), "events": len(rows),
            "signer": manifest["signer"], "manifest": manifest}


def verify(blob: bytes) -> dict:
    """Checks a bundle without trusting anything inside it except what the signature covers."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    checks, problems = [], []
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            files = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    except (tarfile.TarError, OSError, EOFError) as e:
        return {"ok": False, "checks": [], "problems": [f"not a readable bundle: {e}"]}
    for required in ("manifest.json", "manifest.sig", "raw.jsonl"):
        if required not in files:
            return {"ok": False, "checks": [], "problems": [f"missing {required}"]}
    manifest_bytes = files["manifest.json"]
    manifest = json.loads(manifest_bytes)
    trusted = trusted_keys()
    signer = manifest.get("signer")
    pem = trusted.get(signer)
    if pem is None:
        problems.append(f"signer {signer} is not a trusted key (add its public key to the trusted list)")
        checks.append(("signature by a trusted key", False))
    else:
        try:
            serialization.load_pem_public_key(pem).verify(base64.b64decode(files["manifest.sig"]), manifest_bytes)
            checks.append(("signature by a trusted key", True))
        except (InvalidSignature, ValueError):
            problems.append("manifest signature is invalid: the manifest was changed after signing")
            checks.append(("signature by a trusted key", False))
    bad_files = [n for n, meta in manifest["files"].items()
                 if n not in files or hashlib.sha256(files[n]).hexdigest() != meta["sha256"]]
    checks.append(("every file matches the signed manifest", not bad_files))
    if bad_files:
        problems.append(f"file(s) changed or missing: {', '.join(bad_files)}")
    bad_events, n = [], 0
    for line in files["raw.jsonl"].splitlines():
        if not line.strip():
            continue
        n += 1
        rec = json.loads(line)
        if hashlib.sha256(base64.b64decode(rec["raw"])).hexdigest() != rec["sha256"]:
            bad_events.append(rec["uid"])
    checks.append(("every raw event matches its SHA-256", not bad_events))
    if bad_events:
        problems.append(f"{len(bad_events)} raw event(s) altered, first {bad_events[0]}")
    checks.append(("event count matches the manifest", n == manifest["events"]))
    if n != manifest["events"]:
        problems.append(f"manifest says {manifest['events']} events, bundle has {n}")
    ok = all(c[1] for c in checks)
    return {"ok": ok, "checks": [{"check": c, "passed": p} for c, p in checks], "problems": problems,
            "manifest": {k: manifest.get(k) for k in ("purpose", "created_at", "created_by", "source", "window", "events", "signer")}}


def raw_events(blob: bytes):
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        data = tar.extractfile("raw.jsonl").read()
    for line in data.splitlines():
        if line.strip():
            rec = json.loads(line)
            yield rec["source"], base64.b64decode(rec["raw"])


def tamper(blob: bytes) -> bytes:
    """Demo helper: flips one byte inside one raw event and repacks (the manifest and signature are kept)."""
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        files = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    lines = files["raw.jsonl"].splitlines()
    if lines:
        rec = json.loads(lines[0])
        raw = bytearray(base64.b64decode(rec["raw"]))
        raw[len(raw) // 2] ^= 0x01
        rec["raw"] = base64.b64encode(bytes(raw)).decode()
        lines[0] = json.dumps(rec, separators=(",", ":")).encode()
        files["raw.jsonl"] = b"\n".join(lines) + b"\n"
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for fname, data in files.items():
            info = tarfile.TarInfo(fname)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()
