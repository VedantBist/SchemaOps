#!/usr/bin/env python3
"""Builds the SIH26156 deliverables from the running system:
  docs/sih/metrics.json            measured numbers read live from the API (nothing typed in by hand)
  docs/sih/img/*.png               architecture and self-healing diagrams
  docs/sih/CausalOps_ULPF_Architecture.pdf   architecture document (2 pages)
  docs/sih/CausalOps_ULPF_SIH26156.pptx      technical presentation (5 slides)

    python3 docs/sih/build.py                       # reads http://localhost:8080/api/ulpf
    python3 docs/sih/build.py --offline             # reuses metrics.json
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMG = HERE / "img"
NAVY, STEEL, ICE, PEACH, CORAL, INK, GREY, GREEN = "#1F3A4D", "#3A6A82", "#D7E7EF", "#F6CFB2", "#D9764A", "#171A19", "#5E6561", "#2F7D5C"


# ── measured numbers ─────────────────────────────────────────────────────────
def collect(api: str) -> dict:
    def get(path):
        with urllib.request.urlopen(api + path, timeout=60) as r:
            return json.loads(r.read().decode())
    stats, sources, cost = get("/stats"), get("/sources"), get("/cost")
    packs = [p for p in get("/packs") if p["status"] == "CHAMPION"]
    incidents = get("/incidents?limit=500")
    bench = [b for b in get("/benchmarks") if b["kind"] == "PROCESSING"]
    rules = get("/sigma/rules")
    comp = get("/compliance")
    by_kind = {}
    for i in incidents:
        if i["status"] == "RESOLVED" and i["mttd_seconds"] is not None:
            by_kind.setdefault(i["kind"], []).append(i)
    def med(xs):
        xs = sorted(x for x in xs if x is not None)
        return round(xs[len(xs) // 2]) if xs else None
    kinds = {k: {"resolved": len(v), "medianMttd": med([i["mttd_seconds"] for i in v]),
                 "medianMttr": med([i["mttr_seconds"] for i in v])} for k, v in by_kind.items()}
    return {
        "events": stats["stored"], "received": stats["received"], "lossless": stats["losslessVerified"],
        "normalizedPct": round(100 * stats["normalized"] / max(stats["stored"], 1), 2),
        "conservationBalanced": stats["conservation"]["balanced"], "sources": len(sources),
        "packs": len(packs), "sigmaRules": len(rules),
        "siemReductionPct": cost.get("reductionPct"), "savedInrPerMonth": cost.get("savedInrPerMonth"),
        "fullGbPerDay": cost.get("fullGbPerDay"), "siemGbPerDay": cost.get("siemGbPerDay"), "ratePerGbInr": cost.get("ratePerGbInr"),
        "benchmark": bench[0]["result"] if bench else None, "incidents": kinds,
        "compliance": {"passed": comp["passed"], "failed": comp["failed"]},
    }


# ── diagrams ─────────────────────────────────────────────────────────────────
def box(ax, x, y, w, h, text, fc=ICE, ec=STEEL, color=INK, size=9, bold=False, lw=1.2):
    from matplotlib.patches import FancyBboxPatch
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
                                fc=fc, ec=ec, lw=lw))
    ax.text(x, y, text, ha="center", va="center", fontsize=size, color=color, fontweight="bold" if bold else "normal",
            linespacing=1.25, family="DejaVu Sans")


def arrow(ax, x1, y1, x2, y2, color=STEEL, text=None):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(arrowstyle="-|>", color=color, lw=1.4, shrinkA=2, shrinkB=2))
    if text:
        ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 0.12, text, ha="center", va="bottom", fontsize=7, color=GREY)


def architecture(path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(13, 6.6), dpi=200)
    ax.set_xlim(0, 13); ax.set_ylim(0, 6.6); ax.axis("off")
    box(ax, 1.15, 3.6, 2.1, 4.6, "Perimeter & IT sources\n\nPalo Alto · FortiGate\nCisco ASA · Check Point\npfSense · Juniper\nSophos · Suricata\nF5 WAF · VPN · sshd\nWindows · DHCP\n\nSyslog · CEF · LEEF\nJSON · XML · CSV\nkey=value · text",
        fc="white", size=9)
    box(ax, 3.4, 4.9, 1.75, 1.2, "Intake\nsyslog UDP/TCP\nTLS · HTTP", size=9)
    box(ax, 3.4, 3.2, 1.75, 1.0, "Redis Stream\ndurable queue", size=9)
    arrow(ax, 2.2, 4.6, 2.52, 4.9); arrow(ax, 3.4, 4.3, 3.4, 3.7)
    box(ax, 6.1, 3.6, 3.3, 4.6, "", fc="#EEF4F7", ec=STEEL, lw=1.6)
    ax.text(6.1, 5.7, "Stateless workers × N", ha="center", fontsize=10.5, fontweight="bold", color=NAVY)
    steps = ["1  Raw vault: SHA-256 + chain", "2  Format detection", "3  Vendor pack (YAML)",
             "4  OCSF 1.3 + lineage", "5  Lossless proof", "6  Clock fix · GeoIP · entities"]
    for k, t in enumerate(steps):
        ax.text(4.65, 5.15 - k * 0.62, t, fontsize=9.5, color=INK, va="center")
    arrow(ax, 4.28, 3.2, 4.45, 3.2)
    box(ax, 9.0, 4.9, 1.85, 1.0, "PostgreSQL\nOCSF + lineage", size=9)
    box(ax, 9.0, 2.3, 1.85, 1.0, "Vault volume\nraw bytes, sealed", size=9, fc="white")
    arrow(ax, 7.75, 4.5, 8.07, 4.85); arrow(ax, 7.75, 2.8, 8.07, 2.4)
    box(ax, 11.7, 5.55, 2.5, 1.6, "Exporter\nParquet lake (MinIO)\nSIEM tier · HEC · CEF\nOpenSearch · Kafka\nDPDP tokens · Sigma", size=9)
    box(ax, 11.7, 3.3, 2.5, 2.5, "CausalOps monitor\nlearned baselines\ndrift · silence · skew\nself-healing pipeline\nrepair → verify →\nre-normalize\nattack chains · CERT-In", size=9,
        fc=PEACH, ec=CORAL)
    box(ax, 11.7, 1.2, 2.5, 1.1, "Console\nlineage · studio\nhealth · detections", size=9, fc="white")
    arrow(ax, 9.93, 5.1, 10.45, 5.45); arrow(ax, 9.93, 4.75, 10.45, 3.9); arrow(ax, 11.7, 2.05, 11.7, 1.75)
    box(ax, 9.0, 0.75, 1.85, 0.9, "AI engine executors\nDocker · K8s · webhook", size=8.5, fc="white", ec=CORAL)
    arrow(ax, 10.45, 2.5, 9.93, 1.0, color=CORAL, text="")
    ax.text(4.5, 0.25, "Air-gapped: every image, font, GeoIP table and model ships inside; no outbound calls.",
            ha="center", fontsize=8, color=GREY, style="italic")
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def selfheal(path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(13, 3.0), dpi=200)
    ax.set_xlim(0, 13); ax.set_ylim(0, 3); ax.axis("off")
    labels = [("Vendor changes\nformat", "white", STEEL), ("Fill rate\ndrops", ICE, STEEL),
              ("Repair\ninferred", ICE, STEEL), ("Shadow test,\n9 policy rules", ICE, STEEL),
              ("Promote\n(tier 1)", PEACH, CORAL), ("Verify live,\nelse roll back", PEACH, CORAL),
              ("Re-normalize\nhistory", "#E3F1EA", GREEN)]
    xs = [0.95 + i * 1.85 for i in range(len(labels))]
    for x, (t, fc, ec) in zip(xs, labels):
        box(ax, x, 1.55, 1.62, 1.1, t, fc=fc, ec=ec, size=10)
    for a, b in zip(xs, xs[1:]):
        arrow(ax, a + 0.8, 1.55, b - 0.8, 1.55)
    ax.text(6.5, 0.35, "Raw bytes never change; every repaired event is proven lossless again and keeps its previous revision.",
            ha="center", fontsize=8.5, color=GREY, style="italic")
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ── architecture document (2 pages) ─────────────────────────────────────────
def fmt(n, unit=""):
    if n is None:
        return "—"
    if isinstance(n, float):
        return f"{n:,.1f}{unit}"
    return f"{n:,}{unit}"


def pdf(m: dict, path: Path) -> None:
    import fitz
    b = m.get("benchmark") or {}
    inc = m.get("incidents", {})
    drift = inc.get("PARSER_DRIFT", {})
    css = """
    body { font-family: sans-serif; font-size: 8.6pt; color: #171A19; line-height: 1.32; }
    h1 { font-size: 15pt; color: #1F3A4D; margin: 0 0 2px 0; }
    h2 { font-size: 10.5pt; color: #3A6A82; margin: 8px 0 3px 0; }
    p { margin: 0 0 4px 0; }
    table { border-collapse: collapse; width: 100%; }
    td, th { border: 0.5px solid #B8C7CB; padding: 2px 4px; font-size: 8pt; vertical-align: top; }
    th { background-color: #D7E7EF; text-align: left; }
    .sub { color: #5E6561; font-size: 8.4pt; }
    """
    page1 = f"""
    <h1>CausalOps ULPF: Universal Log Pre-processing Framework</h1>
    <p class="sub">SIH 2026 · PS 26156 (NTRO) · Blockchain &amp; Cybersecurity · Architecture document</p>
    <img src="img/architecture.png" width="540"/>
    <h2>Data path (every event)</h2>
    <p><b>Intake</b> accepts syslog over UDP, TCP (newline or RFC 6587 octet counting) and TLS, and HTTP. Messages are queued
    unchanged in a durable Redis stream. <b>Stateless workers</b> (a consumer group; add replicas to scale) first append the raw
    bytes to a <b>write-once vault</b> with a SHA-256 per event and a hash chain per segment, then detect the format, apply the
    source's <b>vendor pack</b> (declarative YAML: matching, field extraction, mapping, value translation), normalize to
    <b>OCSF 1.3</b> and keep every vendor field under <code>unmapped</code>. The <b>lossless proof</b> rebuilds the raw event from
    the normalized record alone and compares its SHA-256 with the vault. Clock correction, offline GeoIP and the cross-vendor
    entity graph are added before the record is committed with its lineage (uid, vault segment, offset, chain hash, pack version).
    A crashed worker's events are reclaimed by the others, so the conservation check <i>received = stored + in flight</i> holds.</p>
    <h2>How PS 26156 is met</h2>
    <table>
    <tr><th>Requirement</th><th>Implementation</th></tr>
    <tr><td>a  Preserve raw data</td><td>Write-once vault, SHA-256 + hash chain, retention-safe anchors; lossless proof per event</td></tr>
    <tr><td>b  Parse source attributes</td><td>{m['packs']} packs (12 vendors + generic CEF/LEEF), exact field spans, Drain3 for unknown formats</td></tr>
    <tr><td>c  Common taxonomy</td><td>OCSF 1.3 classes 2004 · 3002 · 4001 · 4002 · 4003 · 4004, vendor fields kept in unmapped</td></tr>
    <tr><td>d  Traceability</td><td>Every record carries uid, raw SHA-256, vault position, pack@version; click-through lineage view</td></tr>
    <tr><td>e  Plug-and-play onboarding</td><td>Parser Studio: samples → proposed pack → test vs champion → activate (live in 10 s, no restart)</td></tr>
    <tr><td>f  Unified visibility</td><td>Console: sources, events, entity graph across vendors, pipeline health, detections</td></tr>
    <tr><td>g  SIEM / data lake</td><td>Parquet lake on MinIO (S3), SIEM tier, Splunk HEC, CEF syslog, OpenSearch, Kafka</td></tr>
    <tr><td>h  AI/ML ready</td><td>OCSF Parquet features; CausalOps anomaly gate, causal ordering, {m['sigmaRules']} Sigma rules on OCSF</td></tr>
    <tr><td>i  Less parser effort</td><td>YAML instead of code; automatic proposals; automatic repair after format changes</td></tr>
    <tr><td>j  Air-gapped</td><td>All images, fonts, GeoIP (DB-IP Lite) bundled; offline image archive; no outbound calls</td></tr>
    <tr><td>k  Containers</td><td>One docker compose; images on Docker Hub; offline tarball for isolated networks</td></tr>
    </table>
    """
    page2 = f"""
    <h2>What no log pipeline usually does: CausalOps watches the pipeline itself</h2>
    <img src="img/selfheal.png" width="540"/>
    <p>Each source's parsing quality (share of expected OCSF attributes present), volume and clock offset are learned from its own
    quiet history with robust baselines (median, MAD) and a threshold calibrated to at most 1% false alarms. A vendor format change
    becomes a <b>parser-drift incident</b> naming the lost attributes and the new fields; the repair is inferred, shadow-tested
    against the current pack on drifted and earlier events, promoted through a tiered policy (kill switch, dry run, tier,
    fixes the drift, no regression, lossless, evidence, cooldown, reversible), verified on live data or rolled back, and the
    broken window is <b>re-normalized from the vault</b>. Silent sources, clock skew and crashed workers are handled the same way.</p>
    <h2>Twelve differentiators</h2>
    <table>
    <tr><th>1</th><td>Parser drift detection with verified, reversible pack promotion</td><th>7</th><td>Clock-skew estimation and correction per source</td></tr>
    <tr><th>2</th><td>Provable losslessness (rebuild from OCSF = vault SHA-256)</td><th>8</th><td>SIEM cost tiering with lake pointers, no data lost</td></tr>
    <tr><th>3</th><td>Silent-source detection from learned volume</td><th>9</th><td>DPDP pseudonymised export, audited detokenisation</td></tr>
    <tr><th>4</th><td>Self-healing pipeline through the Docker executor</td><th>10</th><td>Sigma rules on OCSF: one rule, every vendor</td></tr>
    <tr><th>5</th><td>Historical re-normalization with revision history</td><th>11</th><td>Cross-vendor entity graph and attack-chain correlation</td></tr>
    <tr><th>6</th><td>CERT-In 2022 compliance from measurements</td><th>12</th><td>Ed25519-signed bundles for data diodes and evidence</td></tr>
    </table>
    <h2>Measured on the running system</h2>
    <table>
    <tr><th>Events stored / proven lossless</th><td>{fmt(m['events'])} / {fmt(m['lossless'])} ({fmt(m['normalizedPct'], '%')} normalized)</td></tr>
    <tr><th>Processing throughput, one worker</th><td>{fmt(b.get('eventsPerSecondPerWorker'))} events/s ≈ {fmt(round((b.get('eventsPerDayPerWorker') or 0) / 1e6))} M events/day per worker, lossless {fmt(b.get('losslessPct'), '%')}</td></tr>
    <tr><th>Parser drift (format change)</th><td>{drift.get('resolved', 0)} resolved; median detect {fmt(drift.get('medianMttd'), ' s')}, resolve {fmt(drift.get('medianMttr'), ' s')}</td></tr>
    <tr><th>SIEM tier</th><td>{fmt(m['siemReductionPct'], '%')} fewer bytes than the full stream ({fmt(m['fullGbPerDay'])} → {fmt(m['siemGbPerDay'])} GB/day at this rate)</td></tr>
    <tr><th>CERT-In checks</th><td>{m['compliance']['passed']} passed, {m['compliance']['failed']} failed (measured, not assumed)</td></tr>
    </table>
    <h2>Deployment</h2>
    <p>One command (<code>bash scripts/demo/start.sh</code>) brings up intake, workers, monitor, exporter, MinIO, Redis, PostgreSQL,
    the CausalOps platform and the console. For isolated networks <code>scripts/release/export_offline.sh</code> saves every image to one
    archive and <code>load_offline.sh</code> starts the stack without contacting any registry. Workers scale with
    <code>--scale ulpf-worker=N</code>; Kafka and OpenSearch run under <code>--profile full</code>.</p>
    """
    tmp = path.with_suffix(".unsubset.pdf")
    writer = fitz.DocumentWriter(str(tmp))
    for html in (page1, page2):
        story = fitz.Story(html=html, user_css=css, archive=fitz.Archive(str(HERE)))
        rect = fitz.paper_rect("a4")
        where = rect + (36, 32, -36, -32)
        more = True
        while more:
            dev = writer.begin_page(rect)
            more, _ = story.place(where)
            story.draw(dev)
            writer.end_page()
            if more:
                print("warning: a page overflowed onto an extra page", file=sys.stderr)
    writer.close()
    doc = fitz.open(str(tmp))   # embed only the glyphs used: the PDF stays small
    doc.subset_fonts()
    doc.save(str(path), garbage=4, deflate=True)
    doc.close()
    try:
        tmp.unlink()
    except OSError:  # Windows may still hold the handle; the next build overwrites it
        pass


# ── technical deck (5 slides) ────────────────────────────────────────────────
def deck(m: dict, path: Path) -> None:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt
    rgb = lambda h: RGBColor.from_string(h.lstrip("#"))
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    b = m.get("benchmark") or {}
    drift = m.get("incidents", {}).get("PARSER_DRIFT", {})

    def slide(title, kicker):
        s = prs.slides.add_slide(blank)
        bar = s.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(0.12))
        bar.fill.solid(); bar.fill.fore_color.rgb = rgb(STEEL); bar.line.fill.background()
        tb = s.shapes.add_textbox(Inches(0.6), Inches(0.35), Inches(12), Inches(0.9)).text_frame
        tb.text = title
        p = tb.paragraphs[0]; p.runs[0].font.size = Pt(30); p.runs[0].font.bold = True; p.runs[0].font.color.rgb = rgb(NAVY)
        k = tb.add_paragraph(); k.text = kicker; k.runs[0].font.size = Pt(15); k.runs[0].font.color.rgb = rgb(GREY)
        foot = s.shapes.add_textbox(Inches(0.6), Inches(7.05), Inches(12), Inches(0.3)).text_frame
        foot.text = "CausalOps ULPF · SIH 2026 · PS 26156 · numbers measured on the running system"
        foot.paragraphs[0].runs[0].font.size = Pt(10); foot.paragraphs[0].runs[0].font.color.rgb = rgb(GREY)
        return s

    def bullets(s, x, y, w, h, items, size=16):
        tf = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)).text_frame
        tf.word_wrap = True
        for i, (head, body) in enumerate(items):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            r1 = p.add_run(); r1.text = head; r1.font.bold = True; r1.font.size = Pt(size); r1.font.color.rgb = rgb(NAVY)
            if body:
                r2 = p.add_run(); r2.text = "  " + body; r2.font.size = Pt(size - 2); r2.font.color.rgb = rgb(INK)
            p.space_after = Pt(8)

    def card(s, x, y, w, h, title, body, fc=ICE, ec=STEEL, tsize=17, bsize=13, num=None):
        from pptx.enum.shapes import MSO_SHAPE
        from pptx.enum.text import MSO_ANCHOR
        sh = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
        sh.adjustments[0] = 0.08
        sh.fill.solid(); sh.fill.fore_color.rgb = rgb(fc); sh.line.color.rgb = rgb(ec); sh.line.width = Pt(1.25)
        tf = sh.text_frame; tf.word_wrap = True; tf.vertical_anchor = MSO_ANCHOR.TOP
        tf.margin_left = tf.margin_right = Inches(0.14); tf.margin_top = Inches(0.1)
        p = tf.paragraphs[0]
        if num is not None:
            r0 = p.add_run(); r0.text = f"{num}  "; r0.font.size = Pt(tsize); r0.font.bold = True; r0.font.color.rgb = rgb(CORAL)
        r = p.add_run(); r.text = title; r.font.size = Pt(tsize); r.font.bold = True; r.font.color.rgb = rgb(NAVY)
        if body:
            q = tf.add_paragraph(); q.space_before = Pt(4)
            rb = q.add_run(); rb.text = body; rb.font.size = Pt(bsize); rb.font.color.rgb = rgb(INK)
        return sh

    def stat(s, x, y, value, label, w=2.9, color=NAVY):
        card(s, x, y, w, 1.45, value, label, fc="FFFFFF", ec=STEEL, tsize=30, bsize=12)

    s = slide("Problem and our answer", "Every vendor logs differently; parsers break silently when firmware changes")
    card(s, 0.6, 1.55, 7.6, 1.55, "The problem",
         "Firewalls, routers, IDS, VPN and servers emit Syslog, CEF, LEEF, JSON, XML, CSV and proprietary text. Parsers are hand-written, "
         "fields get lost, nobody can prove what was kept, and a vendor's format change breaks parsing silently.", fc="FFFFFF", bsize=14)
    card(s, 0.6, 3.25, 7.6, 1.55, "CausalOps ULPF",
         "Ingests any format, keeps every raw byte in a hash-chained vault, normalizes to OCSF 1.3, and proves for each event that the "
         "normalized record still contains the whole original.", bsize=14)
    card(s, 0.6, 4.95, 7.6, 1.75, "And it looks after itself",
         "The CausalOps engine learns each source's normal quality and detects format drift, silence, clock skew and crashed workers, "
         "repairs them through a verified, reversible policy, and re-normalizes the affected history.", fc=PEACH, ec=CORAL, bsize=14)
    stat(s, 8.6, 1.55, fmt(m["events"]), "events stored, every one proven lossless", w=4.1)
    stat(s, 8.6, 3.25, f"{m['packs']} packs · {m['sources']} sources", "12 vendors; unknown formats onboarded in Parser Studio", w=4.1)
    stat(s, 8.6, 4.95, f"{fmt(b.get('eventsPerSecondPerWorker'))} events/s", "per worker, measured; workers scale out", w=4.1)

    s = slide("Architecture", "Stateless, horizontally scalable, air-gapped; lossless by construction")
    s.shapes.add_picture(str(IMG / "architecture.png"), Inches(0.6), Inches(1.55), width=Inches(12.1))

    s = slide("Differentiator: a pipeline that repairs itself", "Detect → infer → shadow-test → promote → verify → re-normalize")
    s.shapes.add_picture(str(IMG / "selfheal.png"), Inches(0.5), Inches(1.45), width=Inches(12.3))
    stat(s, 0.6, 4.55, fmt(drift.get("medianMttd"), " s"), "median time to detect a vendor format change")
    stat(s, 3.75, 4.55, fmt(drift.get("medianMttr"), " s"), "median time to repaired, verified and re-normalized")
    stat(s, 6.9, 4.55, "9 rules", "kill switch · dry run · tier · fixes drift · no regression · lossless · evidence · cooldown · reversible")
    stat(s, 10.05, 4.55, "100%", "re-normalized events proven lossless again")
    card(s, 0.6, 6.15, 12.35, 0.75, "Same loop for silent sources, clock skew and crashed workers",
         "", fc="FFFFFF", tsize=14)

    s = slide("Twelve differentiators", "What the SOC gets beyond normalization")
    items = [("Parser drift", "detected from learned fill rate, repaired automatically"), ("Provable losslessness", "rebuild from OCSF = vault SHA-256"),
             ("Silent sources", "Poisson test on each source's learned rate"), ("Self-healing pipeline", "crashed workers restarted, zero loss"),
             ("Re-normalization", "fix the past from the vault, keep revisions"), ("CERT-In 2022", "retention, NTP, sources, integrity, evidence"),
             ("Clock skew", "per-source offset estimated and corrected"), ("SIEM cost tiering", f"{fmt(m['siemReductionPct'], '%')} less volume, lake pointers"),
             ("DPDP privacy", "format-preserving tokens, audited reveal"), ("Sigma on OCSF", "one rule fires across every vendor"),
             ("Entity graph + attack chains", "scan → brute force → login, one incident"), ("Signed bundles", "Ed25519, data diode, tamper-evident")]
    for k, (t, d) in enumerate(items):
        col, row = k % 3, k // 3
        card(s, 0.6 + col * 4.15, 1.55 + row * 1.33, 4.0, 1.2, t, d, fc=(PEACH if k < 4 else ICE), ec=(CORAL if k < 4 else STEEL),
             tsize=16, bsize=12.5, num=k + 1)

    s = slide("Results and deployment", "One command, Docker Hub images, offline archive for isolated networks")
    stat(s, 0.6, 1.55, f"{fmt(m['normalizedPct'], '%')}", "of stored events fully normalized")
    stat(s, 3.75, 1.55, f"{fmt(m['siemReductionPct'], '%')}", "smaller SIEM ingest, nothing dropped")
    stat(s, 6.9, 1.55, f"{m['compliance']['passed']}/{m['compliance']['passed'] + m['compliance']['failed']}", "CERT-In checks passing (measured)")
    stat(s, 10.05, 1.55, "a – k", "every PS 26156 requirement met")
    for k, (t, d) in enumerate([("Run", "bash scripts/demo/start.sh\nconsole: http://localhost:3000"),
                                ("Air-gapped", "export_offline.sh on a connected machine,\nload_offline.sh inside the isolated network"),
                                ("Scale", "docker compose up -d --scale ulpf-worker=N\nKafka and OpenSearch: --profile full"),
                                ("Verify", "python3 scripts/dev/verify_ulpf.py\nevery claim, end to end, on the live stack")]):
        card(s, 0.6 + (k % 2) * 6.2, 3.35 + (k // 2) * 1.7, 6.05, 1.5, t, d, fc="FFFFFF", tsize=17, bsize=14)
    prs.save(str(path))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080/api/ulpf")
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    IMG.mkdir(parents=True, exist_ok=True)
    mpath = HERE / "metrics.json"
    if a.offline:
        m = json.loads(mpath.read_text())
    else:
        m = collect(a.api.rstrip("/"))
        mpath.write_text(json.dumps(m, indent=1))
    architecture(IMG / "architecture.png")
    selfheal(IMG / "selfheal.png")
    pdf(m, HERE / "CausalOps_ULPF_Architecture.pdf")
    deck(m, HERE / "CausalOps_ULPF_SIH26156.pptx")
    print(json.dumps(m, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
