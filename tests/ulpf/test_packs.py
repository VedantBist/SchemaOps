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
