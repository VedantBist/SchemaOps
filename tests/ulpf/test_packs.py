"""Golden tests for the bundled vendor packs: every sample device must normalize fully and losslessly."""
import random
from datetime import datetime, timezone

import pytest

from ml.ulpf import packs, samples
from ml.ulpf.pipeline import process

NOW = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)
EXPECTED_PACK = {
    "pa-edge-01": "paloalto-panos-traffic", "fgt-dc-01": "fortinet-fortigate", "asa-perimeter": "cisco-asa",
    "cp-gw-01": "checkpoint-firewall", "pfsense-branch": "pfsense-filterlog", "srx-core-01": "juniper-srx",
    "sophos-xg": "sophos-xg", "ids-sensor-01": "suricata-eve", "waf-01": "generic-cef", "bastion-01": "linux-sshd",
    "dc01.corp.local": "windows-security", "dhcp-01": "isc-dhcpd",
}


def test_all_builtin_packs_load():
    loaded = packs.builtin()
    assert len(loaded) >= 13
    assert len({p.id for p in loaded}) == len(loaded)


@pytest.mark.parametrize("device", sorted(EXPECTED_PACK))
def test_pack_normalizes_device(device):
    registry = packs.Registry(packs.builtin())
    ctx = samples.Ctx(random.Random(11))
    n, normalized, fill, lossless = 600, 0, 0.0, 0
    for _ in range(n):
        r = process(samples.DEVICES[device](ctx, device).encode(), received_at=NOW, peer="192.0.2.1", registry=registry)
        assert r.pack is not None and r.pack.id == EXPECTED_PACK[device], (device, r.pack and r.pack.id)
        normalized += r.status == "NORMALIZED"
        fill += r.fill
        lossless += r.lossless
    assert lossless == n
    assert normalized / n >= 0.99, (device, normalized / n)
    assert fill / n >= 0.99, (device, fill / n)


def test_bound_source_keeps_its_pack_when_firmware_changes():
    """A FortiGate whose firmware renames keys still goes through its pack; the fill rate collapses,
    which is the parser-drift signal (instead of silently falling back to generic aliases)."""
    registry = packs.Registry(packs.builtin())
    ctx = samples.Ctx(random.Random(2))
    r1 = process(samples.fortigate(ctx).encode(), received_at=NOW, registry=registry)
    assert r1.newly_bound and r1.fill == 1.0
    ctx.variant = 2
    r2 = process(samples.fortigate(ctx).encode(), received_at=NOW, registry=registry)
    assert r2.pack.id == "fortinet-fortigate" and r2.fill < 0.5 and r2.status == "PARTIAL"
    assert r2.lossless


def test_specific_values():
    registry = packs.Registry(packs.builtin())
    r = process(b"<38>Oct  3 09:00:00 bastion-01 sshd[811]: Failed password for invalid user oracle from 45.155.205.233 port 50122 ssh2",
                received_at=NOW, registry=registry)
    e = r.event
    assert e["class_uid"] == 3002 and e["status_id"] == 2 and e["actor"]["user"]["name"] == "oracle"
    assert e["src_endpoint"]["location"]["country"]  # offline GeoIP enrichment
    r = process(b'<166>Oct 03 2026 09:00:00 asa : %ASA-4-106023: Deny tcp src outside:203.0.113.9/4444 dst inside:10.20.1.11/22 by access-group "outside_in" [0x0, 0x0]',
                received_at=NOW, registry=registry)
    e = r.event
    assert (e["src_endpoint"]["ip"], e["dst_endpoint"]["port"], e["action"], e["policy"]["name"]) == ("203.0.113.9", 22, "Denied", "outside_in")


def test_studio_proposes_a_working_pack_for_an_unknown_text_format():
    from ml.ulpf import studio
    ctx = samples.Ctx(random.Random(4))
    lines = [samples.custom_vpn(ctx) for _ in range(150)]
    registry = packs.Registry(packs.builtin())
    analysis = studio.analyze(lines, registry)
    assert analysis["bodyFormat"] == "text" and analysis["proposal"]["kind"] == "drain"
    assert len(analysis["proposal"]["templates"]) >= 3
    result = studio.test(analysis["proposal"]["yaml"], lines, None)
    assert result["summary"]["losslessPct"] == 100.0
    assert result["summary"]["fillPct"] >= 95, result["summary"]
    assert result["summary"]["normalizedPct"] >= 95, result["summary"]


def test_studio_auto_maps_structured_kv():
    from ml.ulpf import studio
    lines = [f"<13>Oct  3 09:00:0{i} edge acme_fw: src={ip} dst=8.8.8.8 dport=53 action=allow user=u{i}"
             for i, ip in enumerate(["10.0.0.1", "10.0.0.2", "10.0.0.3"])]
    proposal = studio.analyze(lines, packs.Registry([]))["proposal"]
    assert proposal["kind"] == "auto-map"
    result = studio.test(proposal["yaml"], lines, None)
    assert result["summary"]["normalizedPct"] == 100.0 and result["summary"]["losslessPct"] == 100.0


def test_repair_infers_renamed_fields_and_keeps_old_format_working():
    from ml.ulpf import quality, repair
    champion = next(p for p in packs.builtin() if p.id == "fortinet-fortigate")
    ctx = samples.Ctx(random.Random(9))
    old = [samples.fortigate(ctx) for _ in range(80)]
    ctx.variant = 2
    new = [samples.fortigate(ctx) for _ in range(80)]
    assert repair.evaluate(champion, new, NOW)["fill"] < 0.5
    text, changes = repair.infer(champion, new, NOW)
    mapped = {c["field"]: c["attribute"] for c in changes}
    assert mapped["src_ip"] == "src_endpoint.ip" and mapped["dst_port"] == "dst_endpoint.port"
    assert mapped["fw_action"] == "disposition"
    assert any(c["attribute"] == "time" and c["field"] == "eventtime" for c in changes)
    challenger = packs.load(text)
    assert challenger.version == champion.version + 1
    on_new = repair.evaluate(challenger, new, NOW)
    on_old = repair.evaluate(challenger, old, NOW)
    assert on_new["fill"] == 1.0 and on_new["lossless"] == 1.0
    assert on_old["fill"] == 1.0  # no regression for devices still on the old firmware
    r = process(new[0].encode(), received_at=NOW, pack=challenger)
    assert abs(r.event["time"] / 1000 - NOW.timestamp()) > 0  # eventtime (ns) parsed, not the arrival time
    assert r.event["metadata"]["original_time"].isdigit()


def test_quality_gate_flags_drift_and_silence():
    from ml.ulpf import quality
    rng = random.Random(3)
    fills = [1.0 if rng.random() > 0.02 else 0.83 for _ in range(90)]
    b = quality.fit("fill", fills, z_floor=4, max_fpr=0.01)
    healthy = [{"bucket": i, "events": 12, "fill": 1.0, "normalized_ratio": 1.0} for i in range(3)]
    bad = [{"bucket": i, "events": 12, "fill": 0.17, "normalized_ratio": 0.0} for i in range(3)]
    assert not quality.drifting(b, None, healthy, 2)[0]
    assert quality.drifting(b, None, healthy + bad, 2)[0]
    assert not quality.drifting(b, None, healthy + bad[:1], 2)[0]  # one bad bucket is not enough
    assert quality.silence_buckets(10) == 3 and quality.silence_buckets(0.5) == 19
