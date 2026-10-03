# ULPF Phase U2: Vendor packs, unknown-format onboarding, entity graph, offline GeoIP

**Result:** U2 is complete. ULPF now has:
- **13 declarative vendor packs** (YAML, versioned, hot-reloaded by every worker without a restart);
- a **Parser Studio** that onboards a source it has never seen, through **Drain3** template mining or automatic field mapping, with a test bench and one-click activation;
- a **cross-vendor entity graph** (USP 11, graph part);
- **offline GeoIP**.

**Covers SIH26156 points:** (b) parse source-specific fields, (e) plug-and-play onboarding of new sources, (f) unified visibility, (i) less parser development effort.

## What was built

| Part | Where | What it does |
|---|---|---|
| Pack engine | `ml/ulpf/packs.py` | `match` (format, syslog app, vendor/product, required fields, regexes) · `extract.csv_columns` and `extract.regex` (splits a field into sub-fields with exact spans, so losslessness is kept) · strict `map` to OCSF · `values` translations · `class`/`class_rules` · `time` · `host` · `expect`, which gives the per-event **fill rate**. |
| Source binding | `Registry.for_event`, `log_sources.pack_id` | The first match binds a source to its pack, and the source keeps that pack even if its format changes. A silent firmware change therefore shows up as a **drop in fill rate** (the drift signal for U3) instead of quietly falling back to generic parsing. |
| Built-in packs | `ml/ulpf/packs/*.yaml` | Palo Alto PAN-OS traffic, Fortinet FortiGate, Cisco ASA (302013/302015/106023/106001/auth), Check Point (LEEF 2.0), pfSense filterlog, Juniper SRX (RFC 5424 structured data), Sophos XG, Suricata EVE, Linux sshd, Windows Security (XML), ISC DHCP, generic CEF, generic LEEF |
| Pack store | `packstore.py`, Flyway `V9` | `parser_packs` (versions; one CHAMPION per id, enforced by a partial unique index). Built-ins are seeded at start. New versions are saved with their test-bench results. Workers reload champions and bindings every 10 s. |
| Parser Studio | `studio.py`, console **Log pipeline → Parser studio** | Load the latest raw samples of a source from the vault, or paste lines. **Analyze** shows the detected format and the current pack, and proposes a pack: free text goes through **Drain3** (templates become regex rules, parameters named from context such as `from <ip>` → `src_ip` or `port <n>` → `src_port`, and literal outcome words such as "failed" become the disposition); structured formats are mapped automatically. Then **Test** (candidate vs current champion: match, normalized, fill, lossless) and **Activate** (version saved, source bound, live within 10 s). |
| Packs page | console **Parser packs** | Every version, its status, origin (BUILTIN/STUDIO), bound sources, test result at save, and YAML. |
| Entity graph | `entities.py`, V9 `entities`/`entity_links`, console **Entities** | IPs, hostnames, MACs and users from every event. Links: ip–mac, ip–host, host–mac (same asset) and user–ip. The asset is resolved by BFS over same-asset links. The entity page shows a radial graph, the resolved asset, the users seen, events per source in the last hour, and events across vendors. |
| GeoIP | `geoip.py`, `ml/ulpf/data/` | DB-IP IP to Country Lite (CC BY 4.0) bundled in the image, so there are no runtime downloads. It adds `location.country` to public endpoints. |
| Onboarding | `/api/ulpf/onboarding`, panel in Parser Studio | Syslog UDP/TCP/TLS endpoints, the TLS certificate SHA-256 with a download link, and an HTTP ingest example. |
| Consistency | `worker.py`, `store.transaction()` | Each batch writes the event inserts, source counters and entity graph (as a savepoint) in **one transaction**. `/admin/reconcile` recomputes the counters from the events. |
| Console caching | `infrastructure/frontend/nginx.conf` | `index.html` is `no-cache` and hashed assets are immutable, so an upgraded console loads immediately. |

## Measured results (live stack, 2026-10-03)

| Check | Result |
|---|---|
| Unit and golden tests (`tests/ulpf`) | **43 passed**. Each of the 12 sample vendors is processed 600 times through its pack with ≥ 99% normalized, ≥ 99% fill and 100% lossless. The firmware change keeps the bound pack and fill falls below 50% (drift signal). Studio proposals for an unseen text format and a key=value format both test at 100%. |
| Live sources | 12 sources bound automatically to their packs, each at **100% fill** and 100% lossless |
| Onboarding an unknown format (in-house VPN gateway, `vpnd`) | Drain3 found 3 templates in 150 vault samples. The proposed pack tested at **100% match / 100% normalized / 100% fill / 100% lossless**. Activated as `custom-vpnd@1`; live events were `NORMALIZED Authentication` within 25 s, with no restart. |
| Your own test line (`myfw` CEF over UDP) | Stored losslessly. Now matched by `generic-cef`, with `src 10.0.0.1`, `dst 8.8.8.8`, Denied. It also exposed a bug that is now fixed: the first CEF extension key straight after the header pipe was not extracted (regression test added). |
| Entity resolution | `ip:10.20.1.11` → `host:lt-arjun-00` + `mac:00:1a:2b:00:00:00`, seen by **12 vendor sources** in one hour. Users appear across 8 sources each. |
| Conservation after the transaction fix | received = stored (Redis) = stored (database) = **66,086** |

## Notes

- **Lifetime "normalized" figures:** Palo Alto, ASA, pfSense and sshd still show low lifetime percentages, because their U1 history was stored before the packs existed. Re-normalizing past logs (USP 5, U3) will re-run the packs over that vault history, and it makes a good demo of that feature.
- **Entity graph before the sample fix:** the graph holds links created while the sample generator still drew random users for SSH. Those users now come from each machine's owner, and brute force comes only from internet addresses. The older links stay in the graph until the demo snapshot is rebuilt in U5.

## Check it yourself

- **Log pipeline → Parser studio:**
  1. Choose `vpn-gw-01-vpnd` or `myfw-FW`, then **Load samples** → **Analyze** → **Test on samples**.
  2. Or start from an existing pack: the version is bumped automatically.
- **Log pipeline → Parser packs:** see versions and their YAML.
- **Log pipeline → Entities:** search `arjun` and open an IP or a user.
- **Tests:**
  ```bash
  python -m pytest tests/ulpf -q
  ```
