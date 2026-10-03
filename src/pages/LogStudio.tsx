import React, { useMemo, useState } from 'react';
import { ulpf, ulpf2, type AnalyzeResult, type EntityDetail, type TestResult, type TestSummary } from '../api/ulpf';
import { useApi } from '../hooks/useApi';
import {
  Async, Badge, Button, Empty, ErrorBox, Field, KeyValue, Page, Panel, Table, Td, ago, fmtDateTime, fmtNum, fmtTime, inputClass,
} from '../components/ui';
import { ApiError } from '../api/client';

type Navigate = (path: string) => void;
const textareaClass = 'w-full rounded-[3px] border border-[#D9DCD8] bg-white p-2 font-code text-[11.5px] leading-[1.5] focus:outline-none focus:border-[#286B78]';

const pctTone = (v: number | null | undefined) => (v == null ? undefined : v >= 99 ? 'good' : v >= 80 ? 'warn' : 'bad');

const SummaryRow: React.FC<{ label: string; s: TestSummary | null }> = ({ label, s }) => (
  <tr>
    <Td>{label}</Td>
    {s ? (['matchPct', 'normalizedPct', 'fillPct', 'losslessPct'] as const).map((k) => (
      <Td key={k}><Badge tone={pctTone(s[k])}>{fmtNum(s[k], 1)}%</Badge></Td>
    )) : <Td className="text-[#858C87]" >no current version for this pack id</Td>}
  </tr>
);

/** Onboard a new source: samples → analysis (format, pack match, Drain3/auto-map proposal) → test → activate. */
export const ParserStudio: React.FC<{ navigate: Navigate; query?: URLSearchParams }> = ({ navigate, query }) => {
  const sources = useApi(() => ulpf.sources(), []);
  const packList = useApi(() => ulpf2.packs(), []);
  const [source, setSource] = useState(query?.get('source') ?? '');
  const [raw, setRaw] = useState('');
  const [analysis, setAnalysis] = useState<AnalyzeResult | null>(null);
  const [yaml, setYaml] = useState('');
  const [result, setResult] = useState<TestResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const samples = useMemo(() => raw.split('\n').map((s) => s.trimEnd()).filter(Boolean), [raw]);

  const run = async <T,>(label: string, fn: () => Promise<T>) => {
    setBusy(label); setError(null);
    try { return await fn(); } catch (e) { setError(e as Error); return undefined; } finally { setBusy(null); }
  };
  const load = () => run('load', async () => {
    const r = await ulpf2.samples(source, 150);
    setRaw(r.samples.join('\n')); setAnalysis(null); setResult(null); setSaved(null);
  });
  const analyze = () => run('analyze', async () => {
    const a = await ulpf2.analyze(samples, source || undefined);
    setAnalysis(a); setYaml(a.proposal.yaml); setResult(null); setSaved(null);
  });
  const test = () => run('test', async () => setResult(await ulpf2.test(yaml, samples)));
  const editExisting = (id: string, version: number) => run('edit', async () => {
    const p = await ulpf2.pack(id, version);
    setYaml(p.yaml.replace(/^version:\s*\d+/m, `version: ${version + 1}`)); setResult(null);
  });
  const activate = () => run('save', async () => {
    const r = await ulpf2.savePack({ yaml, activate: true, bindSource: source || undefined, samples, notes: 'activated in Parser Studio' });
    setSaved(`${r.id}@${r.version} is now the champion${source ? ` and ${source} is bound to it` : ''}. Workers pick it up within 10 s, no restart.`);
    packList.reload();
  });
  const champions = (packList.data ?? []).filter((p) => p.status === 'CHAMPION');

  return (
    <Page title="Parser studio" subtitle="Onboard any log source without code: paste or load samples, let ULPF detect the format and propose a pack, test it, activate it."
          actions={<Button onClick={() => navigate('/log-packs')}>All packs</Button>}>
      <OnboardingPanel />
      {error && <ErrorBox error={error} />}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Panel title="1 · Samples" actions={<span className="text-[11px] text-[#5E6561]">{samples.length} lines</span>}>
          <div className="flex flex-wrap gap-2 items-end mb-2">
            <Field label="Load the latest raw events of a source (from the vault)">
              <select className={inputClass} value={source} onChange={(e) => setSource(e.target.value)}>
                <option value="">choose a source…</option>
                {(sources.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.id} {s.pack_id ? `(${s.pack_id})` : '(no pack)'}</option>)}
              </select>
            </Field>
            <Button disabled={!source || !!busy} onClick={load}>{busy === 'load' ? 'Loading…' : 'Load samples'}</Button>
            <Button variant="primary" disabled={!samples.length || !!busy} onClick={analyze}>{busy === 'analyze' ? 'Analyzing…' : 'Analyze'}</Button>
          </div>
          <textarea className={textareaClass} rows={14} value={raw} onChange={(e) => setRaw(e.target.value)}
                    placeholder="…or paste raw log lines here, one per line" spellCheck={false} />
        </Panel>
        <Panel title="2 · Pack (YAML)" actions={<>
          <select className={inputClass} value="" onChange={(e) => { const [id, v] = e.target.value.split('@'); if (id) editExisting(id, Number(v)); }}>
            <option value="">start from an existing pack…</option>
            {champions.map((p) => <option key={p.id} value={`${p.id}@${p.version}`}>{p.id} v{p.version}</option>)}
          </select>
          <Button disabled={!yaml || !samples.length || !!busy} onClick={test}>{busy === 'test' ? 'Testing…' : 'Test on samples'}</Button>
          <Button variant="primary" disabled={!result || !!busy} onClick={activate}>{busy === 'save' ? 'Saving…' : 'Activate'}</Button>
        </>}>
          <textarea className={textareaClass} rows={18} value={yaml} onChange={(e) => { setYaml(e.target.value); setResult(null); }}
                    placeholder="The proposed pack appears here after Analyze; edit it freely." spellCheck={false} />
          {saved && <div className="mt-2 text-[12px] text-[#2F7D5C]">{saved}</div>}
        </Panel>
      </div>

      {analysis && (
        <Panel title={`Analysis · body format ${analysis.bodyFormat} · proposal: ${analysis.proposal.kind === 'drain' ? 'Drain3 template mining' : 'automatic field mapping'}`} dense>
          {analysis.proposal.templates.length > 0 && (
            <Table head={['Template discovered', 'Events', 'Fields extracted']}>
              {analysis.proposal.templates.map((t) => (
                <tr key={t.template}><Td mono>{t.template}</Td><Td mono>{t.count}</Td><Td mono>{t.fields.join(', ')}</Td></tr>
              ))}
            </Table>
          )}
          <Table head={['Sample', 'Detected format', 'Current pack', 'Status', 'Fill', 'Lossless']}>
            {analysis.samples.slice(0, 12).map((s, i) => (
              <tr key={i}>
                <Td mono className="max-w-[640px] truncate">{s.sample}</Td>
                <Td mono>{s.format}</Td>
                <Td mono>{s.pack ?? '—'}</Td>
                <Td><Badge tone={s.status === 'NORMALIZED' ? 'good' : s.status === 'PARTIAL' ? 'warn' : 'bad'}>{s.status}</Badge></Td>
                <Td mono>{s.fill == null ? '—' : `${fmtNum(100 * s.fill, 0)}%`}</Td>
                <Td>{s.lossless ? 'yes' : 'no'}</Td>
              </tr>
            ))}
          </Table>
        </Panel>
      )}

      {result && (
        <Panel title={`3 · Test result for ${result.pack}`} dense>
          <Table head={['', 'Matches', 'Normalized', 'Fill (expected attributes)', 'Lossless']}>
            <SummaryRow label="Candidate" s={result.summary} />
            <SummaryRow label="Current champion" s={result.champion} />
          </Table>
          <Table head={['Sample', 'Status', 'Fields', 'OCSF']}>
            {result.details.slice(0, 8).map((d, i) => (
              <tr key={i}>
                <Td mono className="max-w-[380px] break-all">{d.sample}</Td>
                <Td><Badge tone={d.status === 'NORMALIZED' ? 'good' : 'warn'}>{d.status}</Badge></Td>
                <Td mono className="max-w-[360px]">{d.fields.map((f) => `${f.name}=${f.value}`).join('  ')}</Td>
                <Td mono className="max-w-[360px] break-all">{JSON.stringify(d.ocsf)}</Td>
              </tr>
            ))}
          </Table>
        </Panel>
      )}
    </Page>
  );
};

export const OnboardingPanel: React.FC = () => {
  const info = useApi(() => ulpf2.onboarding(), []);
  const host = window.location.hostname;
  return (
    <Panel title="Connect a device">
      <Async state={info}>{(o) => (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 text-[12px]">
          <KeyValue items={[
            ['Syslog UDP', `${host}:${o.syslogUdpPort}`],
            ['Syslog TCP (newline or RFC 6587)', `${host}:${o.syslogTcpPort}`],
            ['Syslog over TLS (RFC 5425)', `${host}:${o.syslogTlsPort}`],
            ['TLS certificate SHA-256', o.tlsFingerprintSha256 ?? 'TLS disabled'],
            ['HTTP', `POST ${window.location.origin}${o.httpIngestPath} (header X-Source names the source)`],
            ['GeoIP', o.geoip],
          ]} />
          <div className="space-y-2">
            <div className="text-[#5E6561]">Point the device's remote syslog at one of these endpoints. Known formats are normalized at once by a bundled pack; anything else is still stored losslessly and can be onboarded here.</div>
            <pre className="font-code text-[11px] bg-[#F7F8F6] border border-[#E6E8E4] rounded-[3px] p-2 overflow-auto">{`curl -X POST ${window.location.origin}${o.httpIngestPath} \\
  -H 'X-Source: my-app' --data-binary @app.log`}</pre>
            {o.tlsCertificate && (
              <a className="text-[#286B78] text-[11px]" download="causalops-ulpf.crt"
                 href={`data:application/x-pem-file;charset=utf-8,${encodeURIComponent(o.tlsCertificate)}`}>Download the TLS certificate</a>
            )}
          </div>
        </div>
      )}</Async>
    </Panel>
  );
};

export const PackList: React.FC<{ navigate: Navigate }> = ({ navigate }) => {
  const list = useApi(() => ulpf2.packs(), [], 15000);
  const [open, setOpen] = useState<string | null>(null);
  const detail = useApi(async () => {
    if (!open) return null;
    const [id, v] = open.split('@');
    return ulpf2.pack(id, Number(v));
  }, [open]);
  return (
    <Page title="Parser packs" subtitle="Declarative YAML packs: versioned, hot-reloaded by every worker, one champion per pack."
          actions={<Button variant="primary" onClick={() => navigate('/log-studio')}>Open parser studio</Button>}>
      <Panel dense>
        <Async state={list} empty={(d) => (d.length ? null : <Empty title="No packs yet" />)}>{(rows) => (
          <Table head={['Pack', 'Version', 'Status', 'Vendor / product', 'Origin', 'Bound sources', 'Saved', 'Test at save', '']}>
            {rows.map((p) => (
              <tr key={`${p.id}@${p.version}`} className={p.status === 'RETIRED' ? 'opacity-60' : ''}>
                <Td mono>{p.id}</Td>
                <Td mono>v{p.version}</Td>
                <Td><Badge tone={p.status === 'CHAMPION' ? 'good' : p.status === 'RETIRED' ? undefined : 'warn'}>{p.status}</Badge></Td>
                <Td>{[p.vendor, p.product].filter(Boolean).join(' · ') || '—'}</Td>
                <Td>{p.origin}</Td>
                <Td mono>{p.bound_sources}</Td>
                <Td>{fmtDateTime(p.created_at)}</Td>
                <Td mono>{p.test ? `norm ${p.test.normalizedPct}% · fill ${p.test.fillPct}% · lossless ${p.test.losslessPct}%` : '—'}</Td>
                <Td><Button variant="ghost" onClick={() => setOpen(open === `${p.id}@${p.version}` ? null : `${p.id}@${p.version}`)}>YAML</Button></Td>
              </tr>
            ))}
          </Table>
        )}</Async>
      </Panel>
      {open && (
        <Panel title={`${open} (YAML)`}>
          <Async state={detail}>{(d) => d && <pre className="font-code text-[11.5px] bg-[#F7F8F6] border border-[#E6E8E4] rounded-[3px] p-2 overflow-auto">{d.yaml}</pre>}</Async>
        </Panel>
      )}
    </Page>
  );
};

const KIND_COLOR: Record<string, string> = { ip: '#286B78', host: '#2F7D5C', mac: '#6B4FA0', user: '#C2410C' };

export const Entities: React.FC<{ navigate: Navigate; query?: URLSearchParams }> = ({ navigate, query }) => {
  const key = query?.get('key');
  const [q, setQ] = useState('');
  const [kind, setKind] = useState('');
  const [submitted, setSubmitted] = useState({ q: '', kind: '' });
  const list = useApi(() => ulpf2.entities(submitted.q || undefined, submitted.kind || undefined), [JSON.stringify(submitted)], 15000);
  if (key) return <EntityView entityKey={key} navigate={navigate} />;
  return (
    <Page title="Entities" subtitle="IPs, hostnames, MACs and users resolved across every vendor's logs into one graph.">
      <Panel>
        <form className="flex flex-wrap gap-2 items-end" onSubmit={(e) => { e.preventDefault(); setSubmitted({ q, kind }); }}>
          <Field label="Search"><input className={inputClass} value={q} onChange={(e) => setQ(e.target.value)} placeholder="arjun, 10.20.1., lt-…" /></Field>
          <Field label="Kind">
            <select className={inputClass} value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">all</option>{['user', 'ip', 'host', 'mac'].map((k) => <option key={k}>{k}</option>)}
            </select>
          </Field>
          <Button variant="primary" type="submit">Search</Button>
        </form>
      </Panel>
      <Panel dense>
        <Async state={list} empty={(d) => (d.length ? null : <Empty title="No entities yet" />)}>{(rows) => (
          <Table head={['Entity', 'Kind', 'Country', 'Events', 'Seen by sources', 'Last seen']}>
            {rows.map((e) => (
              <tr key={e.key} className="hover:bg-[#F7F8F6] cursor-pointer" onClick={() => navigate(`/log-entities?key=${encodeURIComponent(e.key)}`)}>
                <Td mono>{e.value}</Td>
                <Td><span style={{ color: KIND_COLOR[e.kind] }} className="font-semibold">{e.kind}</span></Td>
                <Td>{e.country ?? (e.is_private ? 'internal' : '—')}</Td>
                <Td mono>{e.events.toLocaleString()}</Td>
                <Td><Badge tone={e.source_count > 1 ? 'good' : undefined}>{e.source_count}</Badge> <span className="text-[#5E6561] text-[11px]">{e.sources.slice(0, 4).join(', ')}{e.sources.length > 4 ? '…' : ''}</span></Td>
                <Td>{ago(e.last_seen)}</Td>
              </tr>
            ))}
          </Table>
        )}</Async>
      </Panel>
    </Page>
  );
};

const EntityView: React.FC<{ entityKey: string; navigate: Navigate }> = ({ entityKey, navigate }) => {
  const [minutes, setMinutes] = useState(60);
  const detail = useApi(() => ulpf2.entity(entityKey, minutes), [entityKey, minutes], 15000);
  return (
    <Page title={`Entity ${entityKey}`} subtitle="Everything linked to this identifier, and its events across all vendors."
          actions={<>
            <select className={inputClass} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
              {[[60, 'last hour'], [360, 'last 6 h'], [1440, 'last 24 h'], [0, 'all history']].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
            <Button onClick={() => navigate('/log-entities')}>Back</Button>
          </>}>
      <Async state={detail}>{(d) => (
        <>
          <div className="grid grid-cols-1 xl:grid-cols-[1.2fr_1fr] gap-4">
            <Panel title="Entity graph"><EntityGraph d={d} onSelect={(k) => navigate(`/log-entities?key=${encodeURIComponent(k)}`)} /></Panel>
            <div className="space-y-4">
              <Panel title="Resolved asset (same machine across sources)">
                <KeyValue items={[
                  ['Identifiers', d.asset.join('  ·  ')],
                  ['Users seen', d.links.filter((l) => l.kind === 'user-ip').map((l) => (l.a.startsWith('user:') ? l.a : l.b).slice(5)).filter((v, i, a) => a.indexOf(v) === i).join(', ') || '—'],
                  ['Country', d.entity.country ?? (d.entity.is_private ? 'internal address' : '—')],
                  ['First / last seen', `${fmtDateTime(d.entity.first_seen)} → ${fmtDateTime(d.entity.last_seen)}`],
                ]} />
              </Panel>
              <Panel title={`Seen by (${minutes ? `last ${minutes >= 60 ? `${minutes / 60} h` : `${minutes} min`}` : 'all history'})`} dense>
                <Table head={['Source', 'Events']}>
                  {d.eventsBySourceLastHour.map((s) => <tr key={s.source_id}><Td mono>{s.source_id}</Td><Td mono>{s.n}</Td></tr>)}
                </Table>
              </Panel>
            </div>
          </div>
          <Panel title="Events across vendors" dense>
            <Table head={['Received', 'Source', 'Product', 'OCSF class', 'Source IP', 'Destination IP', 'User', 'Action']}>
              {d.events.map((e) => (
                <tr key={e.uid} className="hover:bg-[#F7F8F6] cursor-pointer" onClick={() => navigate(`/log-events/${encodeURIComponent(e.uid)}`)}>
                  <Td mono>{fmtTime(e.received_at)}</Td><Td mono>{e.source_id}</Td><Td>{e.product ?? '—'}</Td>
                  <Td>{e.class_uid} {e.class_name}</Td><Td mono>{e.src_ip ?? '—'}</Td><Td mono>{e.dst_ip ?? '—'}</Td>
                  <Td>{e.user_name ?? '—'}</Td><Td>{e.action ?? '—'}</Td>
                </tr>
              ))}
            </Table>
          </Panel>
        </>
      )}</Async>
    </Page>
  );
};

/** Radial layout: the selected entity in the centre, direct links on the inner ring, second hop outside. */
const EntityGraph: React.FC<{ d: EntityDetail; onSelect: (key: string) => void }> = ({ d, onSelect }) => {
  const W = 640, H = 420, cx = W / 2, cy = H / 2;
  const center = d.entity.key;
  const direct = new Set<string>();
  d.links.forEach((l) => { if (l.a === center) direct.add(l.b); if (l.b === center) direct.add(l.a); });
  const outer = new Set<string>();
  d.links.forEach((l) => { [l.a, l.b].forEach((k) => { if (k !== center && !direct.has(k)) outer.add(k); }); });
  const inner = [...direct].slice(0, 18);
  const ring2 = [...outer].slice(0, 26);
  const pos: Record<string, [number, number]> = { [center]: [cx, cy] };
  inner.forEach((k, i) => { const a = (2 * Math.PI * i) / Math.max(inner.length, 1); pos[k] = [cx + 115 * Math.cos(a), cy + 115 * Math.sin(a)]; });
  ring2.forEach((k, i) => { const a = (2 * Math.PI * i) / Math.max(ring2.length, 1) + 0.2; pos[k] = [cx + 195 * Math.cos(a), cy + 180 * Math.sin(a)]; });
  const kindOf = (k: string) => k.split(':')[0];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto">
      {d.links.filter((l) => pos[l.a] && pos[l.b]).map((l) => (
        <line key={`${l.a}|${l.b}`} x1={pos[l.a][0]} y1={pos[l.a][1]} x2={pos[l.b][0]} y2={pos[l.b][1]}
              stroke={l.kind === 'user-ip' ? '#E8B48F' : '#B8C7CB'} strokeWidth={Math.min(1 + Math.log10(l.events + 1), 4)}
              strokeDasharray={l.kind === 'user-ip' ? '4 3' : undefined} />
      ))}
      {Object.entries(pos).map(([k, [x, y]]) => (
        <g key={k} className="cursor-pointer" onClick={() => k !== center && onSelect(k)}>
          <circle cx={x} cy={y} r={k === center ? 13 : 8} fill={KIND_COLOR[kindOf(k)] ?? '#858C87'} stroke="#fff" strokeWidth={2} />
          <text x={x} y={y + (k === center ? 27 : 20)} textAnchor="middle" fontSize={k === center ? 11.5 : 10}
                fill="#171A19" fontFamily="JetBrains Mono, monospace">{k.slice(k.indexOf(':') + 1).slice(0, 22)}</text>
        </g>
      ))}
      <g fontSize={10} fill="#5E6561">
        {Object.entries(KIND_COLOR).map(([k, c], i) => (
          <g key={k} transform={`translate(${12 + i * 64}, 14)`}><circle r={5} fill={c} cy={-3} /><text x={9}>{k}</text></g>
        ))}
        <text x={W - 12} y={14} textAnchor="end">solid: same asset · dashed: user used IP</text>
      </g>
    </svg>
  );
};
