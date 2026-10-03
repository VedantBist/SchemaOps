"""ULPF core: format detection, OCSF normalization, provable losslessness and the hash-chained vault."""
import hashlib
import os
import random
from datetime import datetime, timezone

import pytest

from ml.ulpf import lossless, samples, vault
from ml.ulpf.detect import parse
from ml.ulpf.pipeline import process

NOW = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)


def run(line: str, **kw):
    return process(line.encode(), received_at=NOW, peer="192.0.2.10", **kw)


@pytest.mark.parametrize("device", sorted(samples.DEVICES))
def test_every_sample_vendor_is_lossless(device):
    ctx = samples.Ctx(random.Random(42))
    for _ in range(400):
        r = run(samples.DEVICES[device](ctx, device))
        assert r.lossless, (device, r.event["ulpf"].get("error"))
        assert r.status in ("NORMALIZED", "PARTIAL")


def test_firmware_variant_is_also_lossless():
    ctx = samples.Ctx(random.Random(1))
    ctx.variant = 2
    for _ in range(200):
        assert run(samples.fortigate(ctx)).lossless


def test_rfc5424_with_structured_data():
    line = ('<165>1 2026-10-03T09:00:00.003Z fw01 RT_FLOW 1234 ID47 [junos@2636 source-address="10.0.0.5" '
            'destination-address="8.8.8.8" destination-port="53" note="a \\"quoted\\" \\] value"] session created')
    r = run(line)
    assert r.parsed.format == "syslog-5424+text"
    assert r.event["src_endpoint"]["ip"] == "10.0.0.5"
    assert r.event["dst_endpoint"] == {"ip": "8.8.8.8", "port": 53, "is_private": False, "location": {"country": "US"}}
    assert r.parsed.get("sd.junos@2636.note") == 'a \\"quoted\\" \\] value'
    assert r.lossless


def test_cef_extension_with_escapes_and_spaces():
    line = ("CEF:0|Security|threatmanager|1.0|100|worm successfully stopped|10|src=10.0.0.1 dst=2.1.2.2 "
            "spt=1232 msg=Detected a threat. No action needed request=http://x.in/a?b\\=1 act=blocked")
    r = run(line)
    assert r.parsed.format == "cef"
    assert r.event["message"] == "Detected a threat. No action needed"
    assert r.event["class_uid"] == 2004 and r.event["action"] == "Denied"
    assert r.lossless


def test_leef2_with_hex_delimiter():
    line = "LEEF:2.0|Lancope|StealthWatch|1.0|41|^|src=10.0.1.8^dst=10.0.0.5^sev=5^proto=6"
    r = run(line)
    assert r.event["src_endpoint"]["ip"] == "10.0.1.8"
    assert r.event["connection_info"]["protocol_name"] == "tcp"
    assert r.event["unmapped"]["proto"] == "6"  # typed value differs from the text, so the text is kept
    assert r.lossless


def test_json_with_unicode_escapes_and_nesting():
    line = '{"src_ip":"10.1.1.1","user":"r\\u00e9mi","tags":["a","b"],"n":{"x":1.5e3,"ok":true,"none":null}}'
    r = run(line)
    assert r.parsed.format == "json"
    assert {"tags.0", "tags.1", "n.x", "n.ok"} <= set(r.parsed.names())
    assert r.lossless


def test_non_utf8_bytes_are_preserved():
    raw = b"<13>Oct  3 09:00:00 host app: caf\xe9 user=bob action=deny src=10.0.0.9"
    r = process(raw, received_at=NOW)
    assert r.encoding == "latin-1"
    assert r.lossless and r.sha256 == hashlib.sha256(raw).hexdigest()


def test_garbage_is_kept_not_dropped():
    r = run("\x00\x01 ::: ??? ::: ")
    assert r.lossless
    assert r.event["ulpf"]["status"] in ("PARTIAL", "QUARANTINED")


def test_parser_crash_quarantines_with_raw_kept():
    def broken(_text):
        raise RuntimeError("boom")
    r = run("<14>Oct  3 09:00:00 h a: x=1 y=2 z=3", parser=broken)
    assert r.status == "QUARANTINED"
    assert lossless.reconstruct(r.event) == "<14>Oct  3 09:00:00 h a: x=1 y=2 z=3"


def test_tampering_with_normalized_record_breaks_the_proof():
    r = run(samples.fortigate(samples.Ctx(random.Random(3))))
    assert lossless.verify(r.event, r.encoding, r.sha256)
    r.event["src_endpoint"]["ip"] = "10.9.9.9"
    assert not lossless.verify(r.event, r.encoding, r.sha256)


def test_rfc3164_year_inference_near_new_year():
    p = parse("<13>Dec 31 23:59:58 h app: hello")
    from ml.ulpf.ocsf import parse_time
    ts = parse_time(p.get("syslog.timestamp"), datetime(2027, 1, 1, 0, 0, 5, tzinfo=timezone.utc))
    assert ts.year == 2026


def test_vault_chain_and_tamper_detection(tmp_path):
    v = vault.Vault(tmp_path, "w1", max_bytes=600)
    refs = []
    for i in range(20):
        refs += v.append_many([(f"{i}-0", f"event number {i}".encode())])
    v.seal()
    result = vault.verify_writer(tmp_path, "w1")
    assert result["ok"] and result["records"] == 20 and result["segments"] > 1
    rec = vault.read(tmp_path, refs[7].segment, refs[7].offset)
    assert rec["raw"] == b"event number 7" and rec["sha256_ok"]

    # Flip one byte of a stored event: its hash and the chain must fail verification.
    path = tmp_path / f"{refs[7].segment}.seg"
    os.chmod(path, 0o644)
    data = bytearray(path.read_bytes())
    data[refs[7].offset + vault.HEADER.size + len(b"7-0") + 3] ^= 0x01
    path.write_bytes(bytes(data))
    broken = vault.verify_writer(tmp_path, "w1")
    assert not broken["ok"]
    assert any("content hash mismatch" in e for e in broken["errors"])


def test_vault_continues_chain_after_restart(tmp_path):
    v = vault.Vault(tmp_path, "w1")
    v.append_many([("1-0", b"a"), ("2-0", b"b")])
    del v  # crash: segment left open
    v2 = vault.Vault(tmp_path, "w1")
    v2.append_many([("3-0", b"c")])
    v2.seal()
    assert vault.verify_writer(tmp_path, "w1")["ok"]


def test_cef_first_extension_key_after_header_pipe():
    r = run("<14>Oct  3 10:00:00 myfw CEF:0|Acme|FW|1|100|blocked|7|src=10.0.0.1 dst=8.8.8.8 act=deny")
    assert r.event["src_endpoint"]["ip"] == "10.0.0.1"
    assert r.event["dst_endpoint"]["ip"] == "8.8.8.8"
    assert r.lossless


def test_vault_verifies_after_retention_purge(tmp_path):
    v = vault.Vault(tmp_path, "w1", max_bytes=200)
    for i in range(12):
        v.append_many([(f"{i}-0", f"event {i} with some padding".encode())])
    v.seal()
    segs = sorted((tmp_path / "w1").glob("*.seg"))
    assert len(segs) > 3
    vault.purge(tmp_path, "w1", segs[:2])
    result = vault.verify_writer(tmp_path, "w1")
    assert result["ok"], result["errors"]
    assert result["segments"] == len(segs) - 2
