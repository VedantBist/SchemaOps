"""U4: SIEM tiering, DPDP tokenisation, Sigma across vendors, signed bundles."""
import base64
import random
from datetime import datetime, timedelta, timezone

import pytest

from ml.ulpf import packs, privacy, samples, sigma, tiering, vault
from ml.ulpf.pipeline import process

NOW = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)


def events(n=600, seed=5):
    reg = packs.Registry(packs.builtin())
    rng = random.Random(seed)
    ctx = {d: samples.Ctx(random.Random(rng.random())) for d in samples.DEVICES}
    out = []
    for i in range(n):
        d = rng.choice(list(samples.DEVICES))
        r = process(samples.DEVICES[d](ctx[d], d).encode(), received_at=NOW, peer="192.0.2.1", registry=reg)
        r.event["ulpf"]["uid"] = f"{i}-0"
        out.append(r.event)
    return out


def test_siem_tier_keeps_security_events_and_counts_everything():
    evs = [tiering.strip_internal(e) for e in events()]
    out, stats = tiering.tier(evs)
    kept = [e for e in out if not e.get("ulpf", {}).get("summary")]
    summaries = [e for e in out if e.get("ulpf", {}).get("summary")]
    assert sum(e.get("count", 1) for e in out) == len(evs)  # nothing lost: every event counted once
    assert all(e["class_uid"] != 3002 for e in summaries) and all(e.get("action_id") != 2 for e in summaries)
    assert all(s["ulpf"]["lake_ref"]["count"] == s["count"] for s in summaries)
    assert len([e for e in evs if e["class_uid"] in (2004, 3002)]) == len([e for e in kept if e["class_uid"] in (2004, 3002)])
    assert tiering.size(out) <= tiering.size(evs)


def test_verhoeff_and_aadhaar_tokens_stay_valid():
    body = "23412341234"
    good = body + privacy.verhoeff_digit(body)
    assert privacy.verhoeff_ok(good) and not privacy.verhoeff_ok(body + str((int(good[-1]) + 1) % 10))
    t = privacy.Tokenizer("test-key")
    tok = t.aadhaar(good)
    assert len(tok) == 12 and tok != good and privacy.verhoeff_ok(tok)
    assert t.text(f"id {good[:4]} {good[4:8]} {good[8:]}") != f"id {good[:4]} {good[4:8]} {good[8:]}"
    assert t.text("ticket 123456789012") == "ticket 123456789012" or not privacy.verhoeff_ok("123456789012")


def test_tokenised_export_has_no_personal_data_and_is_deterministic():
    t = privacy.Tokenizer("test-key")
    e = {"class_uid": 3002, "actor": {"user": {"name": "arjun.mehta"}}, "src_endpoint": {"ip": "10.20.1.11", "hostname": "lt-arjun-00"},
         "dst_endpoint": {"ip": "8.8.8.8"}, "message": "mail arjun@corp.in or +91 9876543210", "ulpf": {"uid": "1-0"}}
    out, changed = t.event(e)
    text = str(out)
    for secret in ("arjun.mehta", "10.20.1.11", "lt-arjun-00", "arjun@corp.in", "9876543210"):
        assert secret not in text
    assert out["dst_endpoint"]["ip"] == "8.8.8.8"  # public addresses are not personal data here
    assert t.event(e)[0]["actor"] == out["actor"]  # same person → same token (analytics still work)
    assert set(changed) >= {"actor.user.name", "src_endpoint.ip", "src_endpoint.hostname", "message"}
    assert t.pending[out["actor"]["user"]["name"]] == ("user", "arjun.mehta")


def test_sigma_port_scan_fires_across_four_firewall_vendors():
    reg = packs.Registry(packs.builtin())
    c = samples.Ctx(random.Random(2))
    evs = []
    for i in range(120):
        _, line = samples.attack_line(c, "scan")
        r = process(line.encode(), received_at=NOW + timedelta(seconds=i * 0.5), registry=reg)
        r.event["ulpf"]["uid"] = f"s{i}"
        r.event["time"] = int((NOW + timedelta(seconds=i * 0.5)).timestamp() * 1000)
        evs.append(r.event)
    hits = sigma.Engine(sigma.builtin()).process(evs)
    scan = [h for h in hits if h["rule_id"] == "ulpf-003"]
    assert scan and scan[0]["group_key"] == samples.ATTACKER and scan[0]["distinct_count"] >= 15
    assert len(scan[0]["vendors"]) >= 3


def test_sigma_parses_every_bundled_rule_and_conditions():
    rules = sigma.builtin()
    assert len(rules) >= 10
    tor = next(r for r in rules if r.id == "ulpf-008")
    assert tor.match({"class_uid": 4003, "query": {"hostname": "abc.onion"}})
    assert not tor.match({"class_uid": 4003, "query": {"hostname": "example.in"}})


class _Store:
    def __init__(self, rows):
        self._rows = rows

    def rows(self, sql, params=()):
        if "FROM ulpf_events" in sql:
            return self._rows
        return []


def test_bundle_signature_and_tamper_detection(tmp_path, monkeypatch):
    from ml.ulpf import bundle
    monkeypatch.setattr(bundle, "KEY_DIR", tmp_path / "keys")
    monkeypatch.setattr(bundle, "BUNDLE_DIR", tmp_path / "bundles")
    v = vault.Vault(tmp_path / "vault", "w1")
    raws = [f"<14>Oct  3 09:00:0{i} h app: event {i} x=1 y=2 z=3".encode() for i in range(5)]
    refs = v.append_many([(f"{i}-0", r) for i, r in enumerate(raws)])
    v.seal()
    rows = []
    for i, (raw, ref) in enumerate(zip(raws, refs)):
        res = process(raw, received_at=NOW)
        rows.append({"uid": f"{i}-0", "source_id": "h-app", "received_at": NOW, "segment": ref.segment, "vault_offset": ref.offset,
                     "sha256": ref.sha256, "chain": ref.chain, "parser": "core:kv@1", "event": res.event})
    made = bundle.create(_Store(rows), source=None, since=NOW, until=NOW, purpose="DATA_DIODE", created_by="test",
                         vault_dir=str(tmp_path / "vault"))
    blob = (tmp_path / "bundles" / made["file"]).read_bytes()
    ok = bundle.verify(blob)
    assert ok["ok"], ok["problems"]
    assert [r for _, r in bundle.raw_events(blob)] == raws  # byte-for-byte
    bad = bundle.verify(bundle.tamper(blob))
    assert not bad["ok"] and any("altered" in p or "changed" in p for p in bad["problems"])
