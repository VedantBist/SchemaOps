"""The raw vault: append-only, hash-chained segments holding every received byte, before any parsing.

Record layout (big-endian):
    magic "ULPF" | u32 raw length | 32B sha256(raw) | 32B chain | u16 uid length | uid | raw
chain_i = sha256(chain_{i-1} || sha256_i). A segment starts from the previous segment's head (per
writer), so deleting, reordering or editing any record breaks the chain from that point on.
Sealed segments are made read-only (write-once).
"""
from __future__ import annotations

import hashlib
import os
import struct
import time
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"ULPF"
HEADER = struct.Struct(">4sI32s32sH")
GENESIS = b"\x00" * 32


@dataclass(frozen=True)
class Ref:
    segment: str
    offset: int
    length: int
    sha256: str
    chain: str


class Vault:
    def __init__(self, root: str | os.PathLike, writer: str, max_bytes: int = 64 * 2 ** 20, max_age_s: int = 600,
                 on_seal=None):
        self.root = Path(root)
        self.writer = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in writer)[:60]
        self.dir = self.root / self.writer
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes, self.max_age_s = max_bytes, max_age_s
        self.on_seal = on_seal
        self._fh = None
        self._segment = None
        self._opened = 0.0
        self._records = 0
        self._head = self._recover_head()

    # ── writing ──────────────────────────────────────────────────────────────
    def _recover_head(self) -> bytes:
        """After a restart, continue the chain from the last valid record of this writer's newest segment."""
        segments = sorted(self.dir.glob("*.seg"))
        if not segments:
            return GENESIS
        head = GENESIS
        for rec in iter_records(segments[-1]):
            head = bytes.fromhex(rec["chain"])
        prev_head = self._read_meta(segments[-1]).get("prev")
        if head == GENESIS and prev_head:
            head = bytes.fromhex(prev_head)
        # The newest segment may have been left open by a crash: seal it and start a fresh one.
        self._seal_file(segments[-1])
        return head

    def _open(self) -> None:
        name = f"{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}-{os.getpid()}-{int(time.time() * 1000) % 100000:05d}"
        self._segment = f"{self.writer}/{name}"
        path = self.dir / f"{name}.seg"
        (self.dir / f"{name}.meta").write_text(f"prev={self._head.hex()}\nopened={time.time():.3f}\n")
        self._fh = open(path, "ab")
        self._opened = time.time()
        self._records = 0

    def append_many(self, items: list[tuple[str, bytes]]) -> list[Ref]:
        """Appends (uid, raw) records and fsyncs once. Returns their references in order."""
        if self._fh is None:
            self._open()
        refs = []
        for uid, raw in items:
            digest = hashlib.sha256(raw).digest()
            chain = hashlib.sha256(self._head + digest).digest()
            uid_b = uid.encode()
            offset = self._fh.tell()
            self._fh.write(HEADER.pack(MAGIC, len(raw), digest, chain, len(uid_b)) + uid_b + raw)
            self._head = chain
            self._records += 1
            refs.append(Ref(self._segment, offset, HEADER.size + len(uid_b) + len(raw), digest.hex(), chain.hex()))
        self._fh.flush()
        os.fsync(self._fh.fileno())
        if self._fh.tell() >= self.max_bytes or time.time() - self._opened >= self.max_age_s:
            self.seal()
        return refs

    def seal(self) -> None:
        if self._fh is None:
            return
        path = Path(self._fh.name)
        self._fh.close()
        self._fh = None
        info = self._seal_file(path)
        if self.on_seal:
            self.on_seal(self._segment, info)

    def _seal_file(self, path: Path) -> dict:
        meta = self._read_meta(path)
        records, size = 0, path.stat().st_size
        head = meta.get("prev", GENESIS.hex())
        for rec in iter_records(path):
            records += 1
            head = rec["chain"]
        meta_path = path.with_suffix(".meta")
        if "sealed" not in meta:
            with open(meta_path, "a") as fh:
                fh.write(f"sealed={time.time():.3f}\nrecords={records}\nhead={head}\nbytes={size}\n")
        try:
            os.chmod(path, 0o444)
        except OSError:
            pass
        return {"records": records, "head": head, "bytes": size, "prev": meta.get("prev")}

    @staticmethod
    def _read_meta(path: Path) -> dict:
        meta_path = path.with_suffix(".meta")
        if not meta_path.exists():
            return {}
        return dict(line.split("=", 1) for line in meta_path.read_text().splitlines() if "=" in line)


# ── reading and verification (any process with the volume mounted) ──────────────
def iter_records(path: Path):
    with open(path, "rb") as fh:
        while True:
            offset = fh.tell()
            head = fh.read(HEADER.size)
            if len(head) < HEADER.size:
                return
            magic, length, digest, chain, uid_len = HEADER.unpack(head)
            if magic != MAGIC:
                return
            uid = fh.read(uid_len).decode()
            raw = fh.read(length)
            if len(raw) < length:
                return  # torn write at the end of a crashed segment: ignored, never acknowledged
            yield {"offset": offset, "uid": uid, "raw": raw, "sha256": digest.hex(), "chain": chain.hex()}


def read(root: str | os.PathLike, segment: str, offset: int) -> dict:
    path = Path(root) / f"{segment}.seg"
    with open(path, "rb") as fh:
        fh.seek(offset)
        magic, length, digest, chain, uid_len = HEADER.unpack(fh.read(HEADER.size))
        if magic != MAGIC:
            raise ValueError("no vault record at this offset")
        uid = fh.read(uid_len).decode()
        raw = fh.read(length)
    return {"uid": uid, "raw": raw, "sha256": digest.hex(), "chain": chain.hex(),
            "sha256_ok": hashlib.sha256(raw).hexdigest() == digest.hex()}


def verify_writer(root: str | os.PathLike, writer: str) -> dict:
    """Recomputes every hash and the whole chain for one writer's segments, oldest first."""
    folder = Path(root) / writer
    # After retention removed the oldest segments, the chain continues from the recorded anchor
    # (the head of the last removed segment), not from genesis.
    anchor = folder / "ANCHOR"
    expected_prev = anchor.read_text().strip() if anchor.exists() else GENESIS.hex()
    result = {"writer": writer, "segments": 0, "records": 0, "ok": True, "errors": []}
    for path in sorted(folder.glob("*.seg")):
        meta = Vault._read_meta(path)
        prev = meta.get("prev", GENESIS.hex())
        if prev != expected_prev:
            result["ok"] = False
            result["errors"].append(f"{path.stem}: chain does not continue from the previous segment")
        head = bytes.fromhex(prev)
        for rec in iter_records(path):
            if hashlib.sha256(rec["raw"]).hexdigest() != rec["sha256"]:
                result["ok"] = False
                result["errors"].append(f"{path.stem}@{rec['offset']}: content hash mismatch")
            head = hashlib.sha256(head + bytes.fromhex(rec["sha256"])).digest()
            if head.hex() != rec["chain"]:
                result["ok"] = False
                result["errors"].append(f"{path.stem}@{rec['offset']}: chain broken")
                head = bytes.fromhex(rec["chain"])
            result["records"] += 1
        if meta.get("head") and meta["head"] != head.hex():
            result["ok"] = False
            result["errors"].append(f"{path.stem}: sealed head does not match the records")
        expected_prev = head.hex()
        result["segments"] += 1
    result["errors"] = result["errors"][:20]
    return result


def purge(root: str | os.PathLike, writer: str, segments: list[Path]) -> None:
    """Removes the given oldest sealed segments of a writer and records the chain anchor first."""
    segments = sorted(segments)
    if not segments:
        return
    last = segments[-1]
    head = Vault._read_meta(last).get("head")
    if not head:
        head = GENESIS.hex()
        for rec in iter_records(last):
            head = rec["chain"]
    (Path(root) / writer / "ANCHOR").write_text(head + "\n")
    for seg in segments:
        os.chmod(seg, 0o644)
        seg.unlink()
        meta = seg.with_suffix(".meta")
        if meta.exists():
            meta.unlink()


def writers(root: str | os.PathLike) -> list[str]:
    root = Path(root)
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []
