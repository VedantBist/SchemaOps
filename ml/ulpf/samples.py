"""Realistic perimeter-device log generators, written from the vendors' documented formats.

Used by the replayer (demo traffic and benchmarks) and by the golden tests. Each generator returns
one raw line for a randomly drawn connection, login or alert, with no real customer data.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

INTERNAL = [f"10.20.{a}.{b}" for a in (1, 2, 3) for b in (11, 23, 37, 41, 58, 64, 75, 99)]
# Public addresses in three roles that do not overlap, as on a real perimeter:
SERVICES = ["104.18.32.7", "142.250.183.14", "13.107.42.14", "20.190.160.20", "151.101.1.140"]   # SaaS / CDNs (outbound)
REMOTE = ["49.44.180.12", "117.200.12.45", "122.171.88.20", "106.51.77.3", "182.68.5.19"]         # staff at home (Indian ISPs)
BAD = ["185.220.101.34", "45.155.205.233", "89.248.165.52", "198.51.100.23", "203.0.113.77"]      # internet scanners
EXTERNAL = SERVICES + REMOTE + BAD
USERS = ["arjun.mehta", "priya.nair", "svc_backup", "admin", "neha.kulkarni", "rahul.verma", "root", "vpn_guest"]
# Each internal address belongs to one machine and (mostly) one person, so the same user, IP, MAC and
# hostname recur across vendors' logs, as on a real network.
PEOPLE = ["arjun.mehta", "priya.nair", "neha.kulkarni", "rahul.verma", "svc_backup", "admin"]
OWNER = {ip: PEOPLE[i % len(PEOPLE)] for i, ip in enumerate(INTERNAL)}
HOSTNAME = {ip: f"{'srv' if OWNER[ip] in ('svc_backup', 'admin') else 'lt'}-{OWNER[ip].split('.')[0]}-{i:02d}"
            for i, ip in enumerate(INTERNAL)}
MAC = {ip: "00:1a:2b:%02x:%02x:%02x" % (i * 7 % 256, i * 13 % 256, i * 29 % 256) for i, ip in enumerate(INTERNAL)}
APPS = [("ssl", 443), ("web-browsing", 80), ("dns", 53), ("ssh", 22), ("ms-rdp", 3389), ("smtp", 25), ("ntp", 123)]
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


class Ctx:
    """Per-device state: clock offset (for the clock-drift scenario) and format variant (firmware)."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.skew = timedelta(0)
        self.variant = 1
        self.seq = rng.randint(1000, 9000)

    def now(self) -> datetime:
        return datetime.now(timezone.utc) + self.skew

    def flow(self):
        r = self.rng
        app, port = r.choice(APPS)
        outbound = r.random() < 0.7
        if outbound:
            src, dst, allowed = r.choice(INTERNAL), r.choice(SERVICES), r.random() < 0.95
        elif r.random() < 0.6:  # internet background noise: mostly blocked
            src, dst, allowed = r.choice(BAD), r.choice(INTERNAL), r.random() < 0.1
        else:                   # remote staff reaching published services
            src, dst, allowed = r.choice(REMOTE), r.choice(INTERNAL), True
            app, port = ("ssl", 443)
        proto = "udp" if app in ("dns", "ntp") else "tcp"
        return dict(src=src, dst=dst, sport=r.randint(1025, 65000), dport=port, app=app, proto=proto,
                    allowed=allowed, sent=r.randint(200, 90000), rcvd=r.randint(200, 900000),
                    user=(OWNER[src] if src in OWNER else None) if r.random() < 0.4 else None)


def bsd(ts: datetime) -> str:
    return f"{MONTHS[ts.month - 1]} {ts.day:2d} {ts:%H:%M:%S}"


def paloalto(c: Ctx, host="pa-edge-01") -> str:
    f, t = c.flow(), c.now()
    c.seq += 1
    action = "allow" if f["allowed"] else "deny"
    fields = ["1", f"{t:%Y/%m/%d %H:%M:%S}", "013201004417", "TRAFFIC", "end", "2560", f"{t:%Y/%m/%d %H:%M:%S}",
              f["src"], f["dst"], "0.0.0.0", "0.0.0.0", "allow-outbound" if f["allowed"] else "block-inbound",
              f["user"] or "", "", f["app"], "vsys1", "trust" if f["src"].startswith("10.") else "untrust",
              "untrust" if f["src"].startswith("10.") else "trust", "ethernet1/2", "ethernet1/1", "Log-Fwd",
              f"{t:%Y/%m/%d %H:%M:%S}", str(c.seq), "1", str(f["sport"]), str(f["dport"]), "0", "0", "0x400000",
              f["proto"], action, str(f["sent"] + f["rcvd"]), str(f["sent"]), str(f["rcvd"]), "12",
              f"{t:%Y/%m/%d %H:%M:%S}", "3", "any", "0", str(c.seq * 7), "0x0", "10.0.0.0-10.255.255.255",
              "IN" if not f["src"].startswith("10.") else "US", "0", "8", "4", "tcp-fin" if f["allowed"] else "policy-deny"]
    return f"<14>{bsd(t)} {host} " + ",".join(fields)


def fortigate(c: Ctx, host="fgt-dc-01") -> str:
    f, t = c.flow(), c.now()
    action = "accept" if f["allowed"] else "deny"
    proto = 6 if f["proto"] == "tcp" else 17
    if c.variant == 1:
        body = (f'date={t:%Y-%m-%d} time={t:%H:%M:%S} devname="{host}" devid="FG100FTK19000123" logid="0000000013" '
                f'type="traffic" subtype="forward" level="notice" vd="root" srcip={f["src"]} srcport={f["sport"]} '
                f'srcintf="port2" dstip={f["dst"]} dstport={f["dport"]} dstintf="port1" policyid=12 proto={proto} '
                f'action="{action}" service="{f["app"].upper()}" sentbyte={f["sent"]} rcvdbyte={f["rcvd"]} '
                f'duration={c.rng.randint(1, 300)}' + (f' user="{f["user"]}"' if f["user"] else ""))
    else:
        # FortiOS 7.4-style firmware change: new key names and an epoch timestamp (used by the drift scenario).
        body = (f'eventtime={int(t.timestamp() * 1e9)} tz="+0530" logid="0000000013" type="traffic" subtype="forward" '
                f'level="notice" vd="root" src_ip={f["src"]} src_port={f["sport"]} dst_ip={f["dst"]} '
                f'dst_port={f["dport"]} policy_id=12 proto={proto} fw_action="{action}" svc="{f["app"].upper()}" '
                f'bytes_sent={f["sent"]} bytes_rcvd={f["rcvd"]}')
    return f"<189>{bsd(t)} {host} {body}"


def cisco_asa(c: Ctx, host="asa-perimeter") -> str:
    f, t = c.flow(), c.now()
    c.seq += 1
    if f["allowed"]:
        msg = (f"%ASA-6-302013: Built outbound TCP connection {c.seq} for outside:{f['dst']}/{f['dport']} "
               f"({f['dst']}/{f['dport']}) to inside:{f['src']}/{f['sport']} ({f['src']}/{f['sport']})")
    else:
        msg = (f"%ASA-4-106023: Deny {f['proto']} src outside:{f['src']}/{f['sport']} dst inside:{f['dst']}/{f['dport']} "
               f"by access-group \"outside_access_in\" [0x0, 0x0]")
    return f"<166>{MONTHS[t.month - 1]} {t.day:02d} {t.year} {t:%H:%M:%S} {host} : {msg}"


def checkpoint(c: Ctx, host="cp-gw-01") -> str:
    f, t = c.flow(), c.now()
    action = "Accept" if f["allowed"] else "Drop"
    return (f"LEEF:2.0|Check Point|VPN-1 & FireWall-1|R81.20|{action}|x09|cat=Firewall\tdevTime={int(t.timestamp() * 1000)}"
            f"\tsrc={f['src']}\tdst={f['dst']}\tsrcPort={f['sport']}\tdstPort={f['dport']}\tproto={f['proto']}"
            f"\taction={action}\trule_name={'Outbound_Web' if f['allowed'] else 'Cleanup'}\torigin={host}"
            + (f"\tusrName={f['user']}" if f["user"] else ""))


def pfsense(c: Ctx, host="pfsense-branch") -> str:
    f, t = c.flow(), c.now()
    proto_id = "6" if f["proto"] == "tcp" else "17"
    fields = ["5", "", "", "1000000103", "igb0", "match", "pass" if f["allowed"] else "block", "in", "4", "0x0", "",
              "64", str(c.rng.randint(1000, 60000)), "0", "DF", proto_id, f["proto"], str(c.rng.randint(40, 1500)),
              f["src"], f["dst"], str(f["sport"]), str(f["dport"]), "0"]
    if f["proto"] == "tcp":
        fields += ["S", str(c.rng.randint(10 ** 8, 4 * 10 ** 9)), "", "64240", "", "mss;nop;wscale"]
    return f"<134>{bsd(t)} {host} filterlog[{c.rng.randint(300, 9000)}]: " + ",".join(fields)


def juniper_srx(c: Ctx, host="srx-core-01") -> str:
    f, t = c.flow(), c.now()
    kind = "RT_FLOW_SESSION_CREATE" if f["allowed"] else "RT_FLOW_SESSION_DENY"
    sd = (f'[junos@2636.1.1.1.2.129 source-address="{f["src"]}" source-port="{f["sport"]}" '
          f'destination-address="{f["dst"]}" destination-port="{f["dport"]}" service-name="junos-{f["app"]}" '
          f'protocol-id="{6 if f["proto"] == "tcp" else 17}" policy-name="{"trust-to-untrust" if f["allowed"] else "deny-all"}" '
          f'source-zone-name="trust" destination-zone-name="untrust" username="{f["user"] or "N/A"}"]')
    ist = t.astimezone(timezone(timedelta(hours=5, minutes=30)))  # the device logs local time with its offset
    return f"<14>1 {ist:%Y-%m-%dT%H:%M:%S.%f}"[:-3] + f"+05:30 {host} RT_FLOW - {kind} {sd} session {'created' if f['allowed'] else 'denied'}"


def sophos(c: Ctx, host="sophos-xg") -> str:
    f, t = c.flow(), c.now()
    return (f'<30>device="SFW" date={t:%Y-%m-%d} time={t:%H:%M:%S} timezone="IST" device_name="XG330" device_id={host} '
            f'log_id=010101600001 log_type="Firewall" log_component="Firewall Rule" log_subtype="{"Allowed" if f["allowed"] else "Denied"}" '
            f'status="{"Allow" if f["allowed"] else "Deny"}" priority=Information fw_rule_id=5 user_name="{f["user"] or ""}" '
            f'src_ip={f["src"]} dst_ip={f["dst"]} protocol="{f["proto"].upper()}" src_port={f["sport"]} dst_port={f["dport"]} '
            f'sent_bytes={f["sent"]} recv_bytes={f["rcvd"]}')


SIGS = [(2010935, "ET SCAN Suspicious inbound to MSSQL port 1433", 2), (2001219, "ET SCAN Potential SSH Scan", 2),
        (2024897, "ET USER_AGENTS Go HTTP Client User-Agent", 3), (2027865, "ET INFO Observed DNS Query to .onion TLD", 1)]


def suricata(c: Ctx, host="ids-sensor-01") -> str:
    f, t = c.flow(), c.now()
    sid, sig, sev = c.rng.choice(SIGS)
    if "SCAN" in sig:  # scans come from internet scanners
        f["src"], f["dst"] = c.rng.choice(BAD), c.rng.choice(INTERNAL)
    elif "onion" in sig or "USER_AGENTS" in sig:  # outbound oddities come from inside
        f["src"], f["dst"] = c.rng.choice(INTERNAL), c.rng.choice(SERVICES)
    ev = {"timestamp": f"{t:%Y-%m-%dT%H:%M:%S.%f}+0000", "flow_id": c.rng.randint(10 ** 14, 10 ** 15),
          "in_iface": "eth1", "event_type": "alert", "src_ip": f["src"], "src_port": f["sport"],
          "dest_ip": f["dst"], "dest_port": f["dport"], "proto": f["proto"].upper(),
          "alert": {"action": "allowed", "gid": 1, "signature_id": sid, "rev": 3, "signature": sig,
                    "category": "Attempted Information Leak", "severity": sev}, "host": host}
    return json.dumps(ev, separators=(",", ":"))


def cef_waf(c: Ctx, host="waf-01") -> str:
    f, t = c.flow(), c.now()
    attack = c.rng.choice([("SQL Injection", 8), ("Cross Site Scripting", 7), ("Path Traversal", 6), ("Bot Access", 3)])
    return (f"<134>{bsd(t)} {host} CEF:0|F5|Advanced WAF|17.1.0|200000098|{attack[0]}|{attack[1]}|"
            f"rt={int(t.timestamp() * 1000)} src={c.rng.choice(BAD if attack[1] > 5 else REMOTE)} spt={f['sport']} dst={c.rng.choice(INTERNAL)} dpt=443 "
            f"requestMethod=GET request=https://portal.example.in/login?id\\=1 act={'blocked' if attack[1] > 5 else 'alerted'} "
            f"cs1Label=policy_name cs1=/Common/portal_policy")


def sshd(c: Ctx, host="bastion-01") -> str:
    t = c.now()
    if c.rng.random() < 0.65:
        # Staff log in from their own machine.
        ip = c.rng.choice(INTERNAL)
        user, ok = OWNER[ip], c.rng.random() < 0.9
    else:
        # Internet brute force against common account names.
        ip = c.rng.choice(BAD[:4])
        user, ok = c.rng.choice(["root", "admin", "oracle", "test", "ubuntu"]), False
    port = c.rng.randint(30000, 65000)
    if ok:
        msg = f"Accepted publickey for {user} from {ip} port {port} ssh2"
    else:
        msg = f"Failed password for {'invalid user ' if user in ('oracle', 'test', 'ubuntu') else ''}{user} from {ip} port {port} ssh2"
    return f"<38>{bsd(t)} {host} sshd[{c.rng.randint(1000, 40000)}]: {msg}"


def windows_xml(c: Ctx, host="dc01.corp.local") -> str:
    t = c.now()
    ip = c.rng.choice(INTERNAL)
    user = OWNER[ip] if c.rng.random() < 0.97 else "admin"
    ok = c.rng.random() < 0.5
    return (f'<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System>'
            f'<Provider Name="Microsoft-Windows-Security-Auditing"/><EventID>{4624 if ok else 4625}</EventID>'
            f'<TimeCreated SystemTime="{t:%Y-%m-%dT%H:%M:%S.%f}Z"/><Computer>{host}</Computer></System><EventData>'
            f'<Data Name="TargetUserName">{user}</Data><Data Name="WorkstationName">{HOSTNAME[ip]}</Data><Data Name="IpAddress">{ip}</Data>'
            f'<Data Name="LogonType">{c.rng.choice([3, 10])}</Data></EventData></Event>')


def dhcpd(c: Ctx, host="dhcp-01") -> str:
    t = c.now()
    ip = c.rng.choice(INTERNAL)
    kind = c.rng.choice(["DHCPACK", "DHCPACK", "DHCPREQUEST"])
    if kind == "DHCPACK":
        msg = f"DHCPACK on {ip} to {MAC[ip]} ({HOSTNAME[ip]}) via eth0"
    else:
        msg = f"DHCPREQUEST for {ip} from {MAC[ip]} ({HOSTNAME[ip]}) via eth0"
    return f"<30>{bsd(t)} {host} dhcpd[{c.rng.randint(400, 900)}]: {msg}"


def custom_vpn(c: Ctx, host="vpn-gw-01") -> str:
    """An in-house VPN gateway with its own text format: no bundled pack (onboarded in Parser Studio)."""
    t = c.now()
    # Each person connects from their home ISP; occasional mistyped passwords, plus scanners guessing.
    staff = [p for p in PEOPLE if "." in p]  # people, not service or admin accounts
    person = c.rng.randrange(len(staff))
    user, ip = staff[person], REMOTE[person]
    r = c.rng.random()
    if r > 0.92:
        user, ip = c.rng.choice(["admin", "vpn_guest", "test"]), c.rng.choice(BAD)
        r = 0.5  # always a failure
    elif 0.45 <= r < 0.75:
        r = 0.0 if c.rng.random() < 0.8 else 0.5  # staff mostly succeed (0.0 = session opened, 0.5 = failed)
    if r < 0.45:
        msg = f"session opened for user {user} from {ip} port {c.rng.randint(30000, 65000)} assigned 10.8.0.{c.rng.randint(2, 250)}"
    elif r < 0.75:
        msg = f"authentication failed for user {user} from {ip} port {c.rng.randint(30000, 65000)} reason bad-credentials"
    else:
        msg = f"session closed for user {user} duration {c.rng.randint(30, 9000)}s bytes {c.rng.randint(10 ** 4, 10 ** 8)}"
    return f"<134>{bsd(t)} {host} vpnd[{c.rng.randint(300, 900)}]: {msg}"


DEVICES = {
    "pa-edge-01": paloalto, "fgt-dc-01": fortigate, "asa-perimeter": cisco_asa, "cp-gw-01": checkpoint,
    "pfsense-branch": pfsense, "srx-core-01": juniper_srx, "sophos-xg": sophos, "ids-sensor-01": suricata,
    "waf-01": cef_waf, "bastion-01": sshd, "dc01.corp.local": windows_xml, "dhcp-01": dhcpd,
    "vpn-gw-01": custom_vpn,
}


# ── attack scenario (Fault lab): scan → brute force → successful login, one attacker, many vendors ──
ATTACKER = "203.0.113.66"


def attack_line(c: Ctx, phase: str, ip: str = ATTACKER) -> tuple[str, str]:
    """Returns (device, raw line) for the given phase of the attack."""
    r, t = c.rng, c.now()
    victim = r.choice(INTERNAL)
    sport = r.randint(30000, 65000)
    if phase == "scan":
        port = r.randint(1, 1024)
        device = r.choice(["pa-edge-01", "fgt-dc-01", "asa-perimeter", "pfsense-branch"])
        if device == "pa-edge-01":
            c.seq += 1
            fields = ["1", f"{t:%Y/%m/%d %H:%M:%S}", "013201004417", "TRAFFIC", "deny", "2560", f"{t:%Y/%m/%d %H:%M:%S}",
                      ip, victim, "0.0.0.0", "0.0.0.0", "block-inbound", "", "", "not-applicable", "vsys1", "untrust", "trust",
                      "ethernet1/1", "", "Log-Fwd", f"{t:%Y/%m/%d %H:%M:%S}", str(c.seq), "1", str(sport), str(port), "0", "0",
                      "0x0", "tcp", "deny", "60", "60", "0", "1", f"{t:%Y/%m/%d %H:%M:%S}", "0", "any", "0", str(c.seq * 7),
                      "0x0", "IN", "10.0.0.0-10.255.255.255", "0", "1", "0", "policy-deny"]
            return device, f"<14>{bsd(t)} {device} " + ",".join(fields)
        if device == "fgt-dc-01":
            return device, (f'<189>{bsd(t)} {device} date={t:%Y-%m-%d} time={t:%H:%M:%S} devname="{device}" devid="FG100FTK19000123" '
                            f'logid="0000000013" type="traffic" subtype="forward" level="notice" vd="root" srcip={ip} srcport={sport} '
                            f'srcintf="port1" dstip={victim} dstport={port} dstintf="port2" policyid=0 proto=6 action="deny" '
                            f'service="tcp/{port}" sentbyte=0 rcvdbyte=0 duration=0')
        if device == "asa-perimeter":
            return device, (f"<166>{MONTHS[t.month - 1]} {t.day:02d} {t.year} {t:%H:%M:%S} {device} : %ASA-4-106023: Deny tcp src "
                            f"outside:{ip}/{sport} dst inside:{victim}/{port} by access-group \"outside_access_in\" [0x0, 0x0]")
        fields = ["5", "", "", "1000000103", "igb0", "match", "block", "in", "4", "0x0", "", "64", str(r.randint(1000, 60000)),
                  "0", "DF", "6", "tcp", "60", ip, victim, str(sport), str(port), "0", "S", str(r.randint(10 ** 8, 4 * 10 ** 9)),
                  "", "64240", "", "mss"]
        return device, f"<134>{bsd(t)} {device} filterlog[{r.randint(300, 9000)}]: " + ",".join(fields)
    if phase == "brute":
        user = r.choice(["admin", "root", "administrator", "oracle", "backup", "arjun.mehta"])
        if r.random() < 0.5:
            return "bastion-01", f"<38>{bsd(t)} bastion-01 sshd[{r.randint(1000, 40000)}]: Failed password for {user} from {ip} port {sport} ssh2"
        return "vpn-gw-01", f"<134>{bsd(t)} vpn-gw-01 vpnd[{r.randint(300, 900)}]: authentication failed for user {user} from {ip} port {sport} reason bad-credentials"
    # success: the attacker gets in with a guessed admin password
    if r.random() < 0.5:
        return "bastion-01", f"<38>{bsd(t)} bastion-01 sshd[{r.randint(1000, 40000)}]: Accepted password for admin from {ip} port {sport} ssh2"
    return "vpn-gw-01", f"<134>{bsd(t)} vpn-gw-01 vpnd[{r.randint(300, 900)}]: session opened for user admin from {ip} port {sport} assigned 10.8.0.66"
