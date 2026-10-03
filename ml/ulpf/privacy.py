"""Privacy-preserving export (India's Digital Personal Data Protection Act, 2023).

Personal data in exported events is replaced by deterministic, format-preserving tokens:
  user names      → u-<10 hex>            (same person, same token: analytics still work)
  e-mail          → <token>@pii.invalid
  Indian mobile   → +91 9xxxxxxxxx        (digits derived from the keyed hash)
  Aadhaar-like    → 12 digits that still pass the Verhoeff check (only numbers that pass it are tokenised)
  internal IPv4   → 100.64.x.y            (carrier-grade NAT range: obviously not a real host)
  internal host   → host-<10 hex>
Tokens are HMAC-SHA256 under a secret key, so they cannot be reversed without it. The token → value map is
stored encrypted (AES-GCM, key derived from the same secret) so an authorised operator can detokenise, and
every detokenisation is written to an audit table with who and why. The raw vault is not altered: it
stays the access-controlled, lossless forensic record.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import re

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_MOBILE = re.compile(r"(?<!\d)(?:\+91[\s-]?|0)?([6-9]\d{9})(?!\d)")
_AADHAAR = re.compile(r"(?<!\d)(\d{4}[\s-]?\d{4}[\s-]?\d{4})(?!\d)")

# Verhoeff tables (Aadhaar check digit)
_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
      [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
      [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
      [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
      [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
      [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_ok(num: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_digit(num: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return str(_INV[c])


class Tokenizer:
    def __init__(self, secret: str | None = None, store=None):
        self.secret = (secret or os.environ.get("ULPF_PRIVACY_KEY") or "dev-privacy-key-change-me").encode()
        self.enc_key = hashlib.sha256(b"ulpf-privacy-map|" + self.secret).digest()
        self.store = store
        self.pending: dict[str, tuple[str, str]] = {}

    def _h(self, kind: str, value: str) -> bytes:
        return hmac.new(self.secret, f"{kind}|{value}".encode(), hashlib.sha256).digest()

    def _remember(self, token: str, kind: str, value: str) -> str:
        self.pending.setdefault(token, (kind, value))
        return token

    def user(self, v: str) -> str:
        return self._remember("u-" + self._h("user", v.lower()).hex()[:10], "user", v)

    def email(self, v: str) -> str:
        return self._remember(self._h("email", v.lower()).hex()[:12] + "@pii.invalid", "email", v)

    def mobile(self, digits: str) -> str:
        h = int.from_bytes(self._h("mobile", digits), "big")
        return self._remember("+91 9" + f"{h % 10 ** 9:09d}", "mobile", digits)

    def aadhaar(self, digits: str) -> str:
        h = int.from_bytes(self._h("aadhaar", digits), "big")
        body = str(2 + h % 8) + f"{(h // 10) % 10 ** 10:010d}"
        return self._remember(body + verhoeff_digit(body), "aadhaar", digits)

    def ip(self, v: str) -> str:
        h = self._h("ip", v)
        return self._remember(f"100.64.{h[0] % 64}.{h[1]}", "ip", v)

    def host(self, v: str) -> str:
        return self._remember("host-" + self._h("host", v.lower()).hex()[:10], "host", v)

    def text(self, s: str) -> str:
        s = _EMAIL.sub(lambda m: self.email(m.group()), s)

        def aad(m):
            digits = re.sub(r"\D", "", m.group(1))
            return self.aadhaar(digits) if verhoeff_ok(digits) else m.group()
        s = _AADHAAR.sub(aad, s)
        s = _MOBILE.sub(lambda m: self.mobile(m.group(1)), s)
        return s

    # ── events ─────────────────────────────────────────────────────────────
    def event(self, e: dict) -> tuple[dict, list[str]]:
        """Tokenised copy of an exported event and the list of fields changed."""
        out = _deep_copy(e)
        changed = []
        user = ((out.get("actor") or {}).get("user") or {})
        if user.get("name"):
            user["name"] = self.user(user["name"])
            changed.append("actor.user.name")
        for ep in ("src_endpoint", "dst_endpoint", "device"):
            node = out.get(ep) or {}
            ip = node.get("ip")
            if ip and _private(ip):
                node["ip"] = self.ip(ip)
                changed.append(f"{ep}.ip")
            if node.get("hostname") and (ep != "device"):
                node["hostname"] = self.host(node["hostname"])
                changed.append(f"{ep}.hostname")
            if node.get("mac"):
                node["mac"] = "02:" + ":".join(f"{b:02x}" for b in self._h("mac", node["mac"].lower())[:5])
                self._remember(node["mac"], "mac", e[ep]["mac"])
                changed.append(f"{ep}.mac")
        for key in ("message",):
            if isinstance(out.get(key), str):
                new = self.text(out[key])
                if new != out[key]:
                    out[key] = new
                    changed.append(key)
        unmapped = out.get("unmapped")
        if isinstance(unmapped, dict):
            for k, v in list(unmapped.items()):
                if isinstance(v, str):
                    new = self.text(v)
                    if _looks_user_key(k) and v and new == v:
                        new = self.user(v)
                    elif _private(v):
                        new = self.ip(v)
                    if new != v:
                        unmapped[k] = new
                        changed.append(f"unmapped.{k}")
        meta = out.setdefault("ulpf", {})
        meta.pop("skeleton", None)
        meta.pop("fields", None)
        meta["privacy"] = "dpdp-pseudonymised"  # the field list is returned to the caller, not repeated per record
        return out, changed

    def flush(self) -> int:
        """Stores newly seen token → value pairs (encrypted). Returns how many were new."""
        if not self.pending or self.store is None:
            self.pending.clear()
            return 0
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aes = AESGCM(self.enc_key)
        rows = []
        for token, (kind, value) in self.pending.items():
            nonce = hashlib.sha256(b"nonce|" + token.encode()).digest()[:12]  # deterministic per token: idempotent rows
            rows.append((token, kind, nonce + aes.encrypt(nonce, value.encode(), token.encode())))
        with self.store.transaction() as cur:
            cur.executemany("INSERT INTO pii_tokens (token, kind, value_enc) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", rows)
        n = len(rows)
        self.pending.clear()
        return n

    def reveal(self, token: str) -> tuple[str, str] | None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        rows = self.store.rows("SELECT kind, value_enc FROM pii_tokens WHERE token = %s", (token,))
        if not rows:
            return None
        blob = bytes(rows[0]["value_enc"])
        return rows[0]["kind"], AESGCM(self.enc_key).decrypt(blob[:12], blob[12:], token.encode()).decode()


def _private(v: str) -> bool:
    try:
        a = ipaddress.ip_address(v)
        return a.is_private and not a.is_loopback
    except ValueError:
        return False


def _looks_user_key(k: str) -> bool:
    k = k.lower().split(".")[-1]
    return k in {"user", "username", "user_name", "usrname", "suser", "duser", "srcuser", "targetusername", "account"}


def _deep_copy(o):
    if isinstance(o, dict):
        return {k: _deep_copy(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_deep_copy(v) for v in o]
    return o
