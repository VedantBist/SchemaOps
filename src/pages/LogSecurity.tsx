import React, { useState } from 'react';
import { ulpf, ulpf4, type BundleVerify, type PrivacyPreview } from '../api/ulpf';
import { useApi } from '../hooks/useApi';
import {
  Async, Badge, Button, Empty, ErrorBox, Field, KeyValue, Page, Panel, Stat, Table, Td, ago, fmtDateTime, fmtNum, fmtTime, inputClass,
} from '../components/ui';

type Navigate = (path: string) => void;
const bytes = (n: number | null | undefined) => (n == null ? '—' : n > 2 ** 30 ? `${(n / 2 ** 30).toFixed(2)} GB` : n > 2 ** 20 ? `${(n / 2 ** 20).toFixed(1)} MB` : `${(n / 1024).toFixed(1)} KB`);
const inr = (n: number | null | undefined) => (n == null ? '—' : `₹${Math.round(n).toLocaleString('en-IN')}`);
const levelTone = (l: string) => (l === 'critical' || l === 'high' ? 'bad' : l === 'medium' ? 'warn' : undefined);

/** Outputs (data lake, SIEM tier, optional sinks) and the measured SIEM cost reduction. */
export const Outputs: React.FC = () => {
  const sinks = useApi(() => ulpf4.sinks(), [], 5000);
  const cost = useApi(() => ulpf4.cost(), [], 10000);
  const toggle = (name: string, body: Record<string, unknown>) => ulpf4.updateSink(name, body).then(sinks.reload);
  return (
    <Page title="Outputs & SIEM cost" subtitle="Every event goes to the data lake in full. The SIEM tier keeps security events whole and summarises routine allowed traffic, each summary pointing back to the lake: less licence volume, nothing lost.">
      <Async state={cost}>{(c) => (
        <div className="grid grid-cols-2 md:grid-cols-6 gap-2">
          <Stat label="SIEM ingest reduced by" value={c.reductionPct == null ? '—' : `${fmtNum(c.reductionPct, 1)}%`} tone="good" hint="measured bytes, same events" />
          <Stat label="Without tiering" value={bytes(c.fullBytes)} hint={c.fullGbPerDay != null ? `${fmtNum(c.fullGbPerDay, 2)} GB/day` : `${c.eventsIn.toLocaleString()} events`} />
          <Stat label="Sent to the SIEM" value={bytes(c.siemBytes)} hint={c.siemGbPerDay != null ? `${fmtNum(c.siemGbPerDay, 2)} GB/day` : `${c.eventsOut.toLocaleString()} records`} />
          <Stat label="Saved per month" value={inr(c.savedInrPerMonth)} hint={`at ${inr(c.ratePerGbInr)} per GB ingested (setting)`} tone="good" />
          <Stat label="Events in / records out" value={`${c.eventsIn.toLocaleString()} / ${c.eventsOut.toLocaleString()}`} hint="summaries carry their event count" />
          <Stat label="Data lake (full fidelity)" value={c.lake.exported.toLocaleString()} hint="events written as Parquet" />
        </div>
      )}</Async>
      <Panel title="Sinks" dense>
        <Async state={sinks}>{(rows) => (
          <Table head={['Sink', 'Type', 'Receives', 'Enabled', 'DPDP pseudonymised', 'Exported', 'Last success', 'Last error', 'Target']}>
            {rows.map((s) => (
              <tr key={s.name}>
                <Td mono>{s.name}</Td><Td>{s.kind}</Td>
                <Td>{s.tier === 'siem' ? 'SIEM tier' : 'every event'}</Td>
                <Td><Button variant="ghost" onClick={() => toggle(s.name, { enabled: !s.enabled })}>{s.enabled ? <Badge tone="good">on</Badge> : <Badge>off</Badge>}</Button></Td>
                <Td><Button variant="ghost" onClick={() => toggle(s.name, { tokenize: !s.tokenize })}>{s.tokenize ? <Badge tone="info">tokenised</Badge> : <Badge>clear</Badge>}</Button></Td>
                <Td mono>{s.exported.toLocaleString()}</Td>
                <Td>{s.last_ok ? ago(s.last_ok) : '—'}</Td>
                <Td className="text-[#B83A3A] text-[11px] max-w-[260px]">{s.last_error && s.last_error_at && (!s.last_ok || s.last_error_at > s.last_ok) ? s.last_error : '—'}</Td>
                <Td mono className="text-[11px]">{String(s.config.url ?? s.config.host ?? s.config.endpoint ?? s.config.path ?? s.config.bootstrap ?? '')}</Td>
              </tr>
            ))}
          </Table>
        )}</Async>
      </Panel>
    </Page>
  );
};

/** Sigma detections running on OCSF: one rule, every vendor. */
export const Detections: React.FC<{ navigate: Navigate }> = ({ navigate }) => {
  const rules = useApi(() => ulpf4.rules(), [], 15000);
  const [rule, setRule] = useState<string | undefined>();
  const hits = useApi(() => ulpf4.hits(rule), [rule], 5000);
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Page title="Detections" subtitle="Sigma rules evaluated on normalized OCSF events, so the same rule fires on Palo Alto, FortiGate, Cisco, Check Point, sshd or VPN logs alike.">
      <Panel title="Rules" dense>
        <Async state={rules}>{(rs) => (
          <Table head={['Rule', 'Title', 'Level', 'ATT&CK', 'Detections', 'Last hit', '']}>
            {rs.map((r) => (
              <tr key={r.id} className={rule === r.id ? 'bg-[#EEF4F5]' : ''}>
                <Td mono>{r.id}</Td><Td>{r.title}</Td><Td><Badge tone={levelTone(r.level)}>{r.level}</Badge></Td>
                <Td mono className="text-[11px]">{r.tags.filter((t) => t.startsWith('attack.t')).join(' ')}</Td>
                <Td mono>{r.detections}</Td><Td>{r.last_hit ? ago(r.last_hit) : '—'}</Td>
                <Td className="whitespace-nowrap">
                  <Button variant="ghost" onClick={() => setRule(rule === r.id ? undefined : r.id)}>{rule === r.id ? 'All' : 'Hits'}</Button>
                  <Button variant="ghost" onClick={() => setOpen(open === r.id ? null : r.id)}>Sigma</Button>
                </Td>
              </tr>
            ))}
          </Table>
        )}</Async>
        {open && rules.data && <pre className="m-3 font-code text-[11.5px] bg-[#F7F8F6] border border-[#E6E8E4] rounded-[3px] p-2">{rules.data.find((r) => r.id === open)?.yaml}</pre>}
      </Panel>
      <Panel title={rule ? `Detections of ${rule}` : 'Latest detections'} dense>
        <Async state={hits} empty={(d) => (d.length ? null : <Empty title="No detections yet">Run the attack scenario from Pipeline health → Fault lab (device "attacker").</Empty>)}>{(hs) => (
          <Table head={['Last seen', 'Rule', 'Level', 'Group (by)', 'Events', 'Distinct', 'Vendors', 'Sources', 'Evidence']}>
            {hs.map((h) => (
              <tr key={h.id}>
                <Td mono>{fmtTime(h.last_seen)}</Td><Td className="max-w-[300px]">{h.rule_title}</Td><Td><Badge tone={levelTone(h.level)}>{h.level}</Badge></Td>
                <Td mono>{h.group_key}</Td><Td mono>{h.count}</Td><Td mono>{h.distinct_count ?? '—'}</Td>
                <Td>{h.vendors.map((v) => <span key={v} className="inline-block mr-1 mb-0.5"><Badge tone={h.vendors.length > 1 ? 'info' : undefined}>{v}</Badge></span>)}</Td>
                <Td mono className="text-[11px]">{h.sources.join(', ')}</Td>
                <Td>{h.sample_uids[0] && <Button variant="ghost" onClick={() => navigate(`/log-events/${encodeURIComponent(h.sample_uids[h.sample_uids.length - 1])}`)}>Event</Button>}</Td>
              </tr>
            ))}
          </Table>
        )}</Async>
      </Panel>
    </Page>
  );
};

/** DPDP Act 2023: pseudonymised exports, audited detokenisation. */
export const Privacy: React.FC = () => {
  const recent = useApi(() => ulpf.events({ classUid: 3002, limit: 30 }), []);
  const [uid, setUid] = useState('');
  const [preview, setPreview] = useState<PrivacyPreview | null>(null);
  const [token, setToken] = useState('');
  const [reason, setReason] = useState('');
  const [revealed, setRevealed] = useState<string | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const audit = useApi(() => ulpf4.privacyAudit(), [revealed], 15000);
  const load = (u: string) => { setUid(u); setError(null); ulpf4.preview(u).then(setPreview).catch(setError); };
  return (
    <Page title="Privacy (DPDP Act 2023)" subtitle="Exports carry deterministic, format-preserving tokens instead of personal data (users, e-mails, mobile and Aadhaar numbers, internal IPs and hosts). The vault keeps the original for forensics; revealing a token needs a reason and is audited.">
      {error && <ErrorBox error={error} />}
      <Panel title="Pick an event">
        <div className="flex flex-wrap gap-2 items-end">
          <Field label="Recent authentication events">
            <select className={inputClass} value={uid} onChange={(e) => load(e.target.value)}>
              <option value="">choose…</option>
              {(recent.data ?? []).map((e) => <option key={e.uid} value={e.uid}>{fmtTime(e.received_at)} {e.source_id} {e.user_name ?? ''} {e.src_ip ?? ''}</option>)}
            </select>
          </Field>
          <Field label="…or an event uid"><input className={inputClass} value={uid} onChange={(e) => setUid(e.target.value)} /></Field>
          <Button variant="primary" onClick={() => load(uid)} disabled={!uid}>Show</Button>
        </div>
      </Panel>
      {preview && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          <Panel title="As stored (access-controlled)"><pre className="font-code text-[11px] bg-[#F7F8F6] border border-[#E6E8E4] rounded-[3px] p-2 overflow-auto max-h-[480px]">{JSON.stringify(trim(preview.original), null, 2)}</pre></Panel>
          <Panel title={`As exported (${preview.changed.length} field(s) pseudonymised)`}><pre className="font-code text-[11px] bg-[#F3F7F5] border border-[#E6E8E4] rounded-[3px] p-2 overflow-auto max-h-[480px]">{JSON.stringify(trim(preview.tokenized), null, 2)}</pre></Panel>
        </div>
      )}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Panel title="Reveal a token (audited)">
          <div className="flex flex-wrap gap-2 items-end">
            <Field label="Token"><input className={`${inputClass} w-56`} value={token} onChange={(e) => setToken(e.target.value)} placeholder="u-3f9a… / 100.64.x.y" /></Field>
            <Field label="Reason (recorded)"><input className={`${inputClass} w-72`} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="incident LOG-1007 investigation" /></Field>
            <Button onClick={() => { setError(null); ulpf4.detokenize(token, reason).then((r) => setRevealed(`${r.kind}: ${r.value}`)).catch(setError); }}>Reveal</Button>
          </div>
          {revealed && <div className="mt-2 text-[12px] font-code">{revealed}</div>}
        </Panel>
        <Panel title="Detokenisation audit (append-only)" dense>
          <Async state={audit}>{(rows) => (
            <Table head={['Time', 'Who', 'Token', 'Reason', 'Granted']}>
              {rows.slice(0, 12).map((r) => <tr key={r.id}><Td mono>{fmtTime(r.at)}</Td><Td>{r.actor}</Td><Td mono>{r.token}</Td><Td>{r.reason}</Td><Td>{r.granted ? 'yes' : 'no'}</Td></tr>)}
            </Table>
          )}</Async>
        </Panel>
      </div>
    </Page>
  );
};

function trim(e: Record<string, unknown>) {
  const out: Record<string, unknown> = {};
  for (const k of ['class_name', 'time', 'actor', 'src_endpoint', 'dst_endpoint', 'device', 'action', 'message', 'unmapped', 'ulpf']) {
    if (e[k] !== undefined) out[k] = k === 'ulpf' ? { uid: (e.ulpf as Record<string, unknown>)?.uid, privacy: (e.ulpf as Record<string, unknown>)?.privacy } : e[k];
  }
  return out;
}

/** CERT-In compliance, evidence packs and signed data-diode bundles. */
export const Compliance: React.FC = () => {
  const report = useApi(() => ulpf4.compliance(), [], 30000);
  const bundles = useApi(() => ulpf4.bundles(), [], 10000);
  const keys = useApi(() => ulpf4.keys(), []);
  const [busy, setBusy] = useState<string | null>(null);
  const [verify, setVerify] = useState<(BundleVerify & { title: string }) | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [ingest, setIngest] = useState(false);
  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label); setError(null);
    try { await fn(); } catch (e) { setError(e as Error); } finally { setBusy(null); bundles.reload(); }
  };
  const lastExport = (bundles.data ?? []).find((b) => b.direction === 'EXPORT');
  return (
    <Page title="Compliance & evidence" subtitle="CERT-In Directions (28 April 2022) checked continuously from measurements, one-click incident evidence, and signed bundles for one-way (data-diode) transfer."
          actions={<Button onClick={() => ulpf4.compliance(true).then(() => report.reload())}>Re-check now</Button>}>
      {error && <ErrorBox error={error} />}
      <Async state={report}>{(r) => (
        <Panel title={`${r.framework} · ${r.passed} passed, ${r.failed} failed · checked ${ago(r.at)}`} dense>
          <Table head={['Check', 'Direction', 'Result', 'Measured', 'Per source']}>
            {r.checks.map((c) => (
              <tr key={c.id}>
                <Td>{c.title}<div className="text-[11px] text-[#5E6561]">{c.detail}</div></Td>
                <Td>{c.direction}</Td>
                <Td><Badge tone={c.passed ? 'good' : 'bad'}>{c.passed ? 'pass' : 'fail'}</Badge></Td>
                <Td className="text-[12px]">{c.measured}</Td>
                <Td className="text-[11px]">{c.sources.filter((s) => !s.passed).map((s) => <div key={s.source} className="text-[#B83A3A]">{s.source}: {s.value}</div>)}
                  {c.sources.length > 0 && c.sources.every((s) => s.passed) && <span className="text-[#2F7D5C]">all {c.sources.length} ok</span>}</Td>
              </tr>
            ))}
          </Table>
        </Panel>
      )}</Async>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Panel title="Evidence & data-diode bundles">
          <div className="flex flex-wrap gap-2 mb-2">
            <Button variant="primary" disabled={!!busy} onClick={() => run('evidence', () => ulpf4.createBundle({ minutes: 60, purpose: 'CERT_IN_EVIDENCE' }))}>{busy === 'evidence' ? 'Building…' : 'CERT-In evidence pack (last 60 min)'}</Button>
            <Button disabled={!!busy} onClick={() => run('diode', () => ulpf4.createBundle({ minutes: 10, purpose: 'DATA_DIODE' }))}>{busy === 'diode' ? 'Building…' : 'Export signed bundle (10 min)'}</Button>
            <Button variant="danger" disabled={!lastExport || !!busy} onClick={() => run('tamper', async () => setVerify({ title: `Tampered copy of ${lastExport?.file}`, ...(await ulpf4.tamperTest(lastExport!.id)) }))}>Tamper test (flip 1 byte)</Button>
          </div>
          <div className="flex flex-wrap gap-2 items-center mb-2 text-[12px]">
            <span>Import a bundle:</span>
            <input type="file" accept=".gz" onChange={(e) => { const f = e.target.files?.[0]; if (f) run('import', async () => setVerify({ title: `Import of ${f.name}`, ...(await ulpf4.importBundle(f, ingest)) })); }} />
            <label className="flex items-center gap-1"><input type="checkbox" checked={ingest} onChange={(e) => setIngest(e.target.checked)} /> replay into this pipeline after verification</label>
          </div>
          {verify && (
            <div className="border border-[#E6E8E4] rounded-[3px] p-2 mb-2">
              <div className="mb-1 text-[12px] font-semibold">{verify.title}: {verify.ok ? <Badge tone="good">VERIFIED</Badge> : <Badge tone="bad">REJECTED</Badge>}</div>
              {verify.checks.map((c) => <div key={c.check} className="text-[12px]">{c.passed ? '✓' : '✗'} {c.check}</div>)}
              {verify.problems.map((p) => <div key={p} className="text-[12px] text-[#B83A3A]">{p}</div>)}
              {verify.ingested ? <div className="text-[12px]">{verify.ingested} events replayed</div> : null}
            </div>
          )}
          <Async state={keys}>{(k) => <div className="text-[11px] text-[#5E6561]">Signing key {k.signer} (Ed25519). Trusted keys: {k.trusted.join(', ')}</div>}</Async>
        </Panel>
        <Panel title="Bundle history" dense>
          <Async state={bundles} empty={(d) => (d.length ? null : <Empty title="No bundles yet" />)}>{(rows) => (
            <Table head={['Time', 'Direction', 'Purpose', 'Events', 'Size', 'Verified', '']}>
              {rows.map((b) => (
                <tr key={b.id}>
                  <Td mono>{fmtDateTime(b.created_at)}</Td><Td>{b.direction}</Td><Td>{b.purpose}</Td><Td mono>{b.events ?? '—'}</Td><Td mono>{bytes(b.bytes)}</Td>
                  <Td>{b.verified == null ? '—' : b.verified ? <Badge tone="good">yes</Badge> : <Badge tone="bad">rejected</Badge>}</Td>
                  <Td>{b.direction === 'EXPORT' && <a className="text-[#286B78] text-[12px]" href={`/api/ulpf/bundles/${b.id}/download`}>Download</a>}</Td>
                </tr>
              ))}
            </Table>
          )}</Async>
        </Panel>
      </div>
    </Page>
  );
};

/** Throughput, measured on this machine. */
export const Benchmarks: React.FC = () => {
  const list = useApi(() => ulpf4.benchmarks(), [], 15000);
  const stats = useApi(() => ulpf.stats(), [], 5000);
  const [busy, setBusy] = useState(false);
  return (
    <Page title="Benchmarks" subtitle="Measured, not estimated: one worker's processing path in-process, and end-to-end through the running intake (scripts/ulpf/bench.py)."
          actions={<Button variant="primary" disabled={busy} onClick={() => { setBusy(true); ulpf4.runBenchmark(10).finally(() => { setBusy(false); list.reload(); }); }}>{busy ? 'Running (10 s)…' : 'Run processing benchmark'}</Button>}>
      <Async state={stats}>{(s) => (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <Stat label="Live rate (last minute)" value={`${s.eventsPerSecond}/s`} />
          <Stat label="Workers alive" value={s.workers.filter((w) => w.alive).length} />
          <Stat label="Queue" value={s.backlog} />
          <Stat label="Conservation" value={s.conservation.balanced ? 'balanced' : `${s.conservation.unaccounted} off`} tone={s.conservation.balanced ? 'good' : 'warn'} />
        </div>
      )}</Async>
      <Panel title="Results" dense>
        <Async state={list} empty={(d) => (d.length ? null : <Empty title="No benchmark yet" />)}>{(rows) => (
          <Table head={['Time', 'Kind', 'Events/s', 'Events/day', 'Lossless', 'Details']}>
            {rows.map((b) => {
              const r = b.result as Record<string, number | string | Record<string, unknown>>;
              const eps = (r.eventsPerSecondPerWorker ?? r.storedPerSecond) as number;
              const perDay = (r.eventsPerDayPerWorker ?? r.eventsPerDay) as number;
              return (
                <tr key={b.id}>
                  <Td mono>{fmtDateTime(b.at)}</Td><Td>{b.kind === 'PROCESSING' ? 'per worker (in-process)' : 'end to end'}</Td>
                  <Td mono>{eps?.toLocaleString()}</Td><Td mono>{perDay ? `${(perDay / 1e6).toFixed(1)} M` : '—'}</Td>
                  <Td mono>{r.losslessPct != null ? `${r.losslessPct}%` : r.conservationBalanced != null ? (r.conservationBalanced ? 'balanced' : 'check') : '—'}</Td>
                  <Td mono className="text-[11px]">{b.kind === 'PROCESSING' ? `${r.events} events in ${r.seconds} s, ${r.avgEventBytes} B avg, ${String((r.machine as Record<string, unknown>)?.cpu ?? '')}`
                    : `${r.sent} sent over TCP in ${r.sendSeconds} s, ${r.storedEvents} stored in ${r.seconds} s, ${r.workers} workers`}</Td>
                </tr>
              );
            })}
          </Table>
        )}</Async>
      </Panel>
      <Panel title="Scaling">
        <KeyValue items={[
          ['Workers', 'stateless consumers of one Redis stream group: add replicas with docker compose up -d --scale ulpf-worker=N'],
          ['Throughput', 'grows with workers until the database insert rate is the limit; the lake and SIEM sinks run in a separate exporter'],
          ['Zero loss', 'events stay in the stream until acknowledged; a crashed worker\'s events are reclaimed by the others'],
        ]} />
      </Panel>
    </Page>
  );
};
