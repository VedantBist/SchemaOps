import React, { useState } from 'react';
import { ulpf3, type IncidentDetailU, type UlpfAction, type UlpfIncident } from '../api/ulpf';
import { useApi } from '../hooks/useApi';
import { LineChart, type Series } from '../components/LineChart';
import {
  Async, Badge, Button, Empty, Field, KeyValue, Page, Panel, Stat, Table, Td, ago, fmtDateTime, fmtNum, fmtSeconds, fmtTime, inputClass,
} from '../components/ui';

type Navigate = (path: string) => void;

const statusTone = (s: string) => (s === 'RESOLVED' || s === 'VERIFIED' || s === 'DONE' ? 'good'
  : s === 'ESCALATED' || s === 'FAILED' || s === 'BLOCKED' || s === 'ROLLED_BACK' ? 'bad'
    : s === 'PROPOSED' ? 'warn' : 'info');
const KIND_LABEL: Record<string, string> = {
  PARSER_DRIFT: 'Parser drift', SOURCE_SILENT: 'Silent source', CLOCK_SKEW: 'Clock skew', PIPELINE: 'Pipeline', SECURITY_CORRELATION: 'Correlation',
};

/** Pipeline health: CausalOps watching the log pipeline (quality baselines, incidents, verified remediation). */
export const PipelineHealth: React.FC<{ navigate: Navigate; id?: string }> = ({ navigate, id }) => {
  if (id) return <IncidentView id={id} navigate={navigate} />;
  return <HealthHome navigate={navigate} />;
};

const HealthHome: React.FC<{ navigate: Navigate }> = ({ navigate }) => {
  const overview = useApi(() => ulpf3.overview(), [], 5000);
  const incidents = useApi(() => ulpf3.incidents(), [], 5000);
  const jobs = useApi(() => ulpf3.jobs(), [], 5000);
  const [selected, setSelected] = useState<string | null>(null);
  const resolved = (incidents.data ?? []).filter((i) => i.status === 'RESOLVED');
  const mttd = median(resolved.map((i) => i.mttd_seconds).filter((v): v is number => v != null));
  const mttr = median(resolved.map((i) => i.mttr_seconds).filter((v): v is number => v != null));
  return (
    <Page title="Pipeline health" subtitle="Each source's parsing quality, volume and clock are learned from its own history; drift, silence and clock skew open incidents that are repaired, verified and audited.">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
        <Stat label="Sources watched" value={`${(overview.data?.sources ?? []).filter((s) => s.state === 'WATCHED').length} / ${(overview.data?.sources ?? []).length}`} hint="baselines learned" />
        <Stat label="Open incidents" value={(incidents.data ?? []).filter((i) => i.status !== 'RESOLVED').length} tone={(incidents.data ?? []).some((i) => i.status === 'ESCALATED') ? 'bad' : undefined} />
        <Stat label="Resolved" value={resolved.length} />
        <Stat label="Median time to detect" value={mttd == null ? '—' : fmtSeconds(mttd)} hint="first bad event → incident" />
        <Stat label="Median time to resolve" value={mttr == null ? '—' : fmtSeconds(mttr)} hint="first bad event → resolved" />
      </div>
      <Panel title="Incidents" dense>
        <Async state={incidents} empty={(d) => (d.length ? null : <Empty title="No pipeline incidents">Inject a scenario below to see detection and repair.</Empty>)}>{(rows) => (
          <Table head={['Key', 'Kind', 'Source', 'Status', 'Severity', 'Title', 'Detected', 'MTTD', 'MTTR']}>
            {rows.map((i) => (
              <tr key={i.id} className="hover:bg-[#F7F8F6] cursor-pointer" onClick={() => navigate(`/log-health/${i.incident_key}`)}>
                <Td mono>{i.incident_key}</Td><Td>{KIND_LABEL[i.kind] ?? i.kind}</Td><Td mono>{i.source_id}</Td>
                <Td><Badge tone={statusTone(i.status)}>{i.status}</Badge></Td><Td>{i.severity}</Td>
                <Td className="max-w-[420px]">{i.title}</Td><Td>{fmtTime(i.detected_at)}</Td>
                <Td mono>{i.mttd_seconds == null ? '—' : fmtSeconds(i.mttd_seconds)}</Td>
                <Td mono>{i.mttr_seconds == null ? '—' : fmtSeconds(i.mttr_seconds)}</Td>
              </tr>
            ))}
          </Table>
        )}</Async>
      </Panel>
      <div className="grid grid-cols-1 2xl:grid-cols-[minmax(0,1fr)_400px] gap-4">
        <Panel title="Sources: learned normal and current quality" dense className="min-w-0">
          <Async state={overview}>{(o) => (
            <Table head={['Source', 'State', 'Pack', 'Fill now', 'Normal fill', 'Volume now', 'Normal volume', 'Clock offset', 'Correction', '']}>
              {o.sources.map((s) => {
                const b = Object.fromEntries((s.baselines ?? []).map((x) => [x.metric, x]));
                return (
                  <tr key={s.id} className={`hover:bg-[#F7F8F6] cursor-pointer ${selected === s.id ? 'bg-[#EEF4F5]' : ''}`} onClick={() => setSelected(s.id)}>
                    <Td mono>{s.id}</Td>
                    <Td>{s.state === 'WATCHED' ? <Badge tone="good">watched</Badge> : <Badge tone="warn">learning {fmtNum(100 * s.learningProgress, 0)}%</Badge>}</Td>
                    <Td mono>{s.pack_id ?? '—'}</Td>
                    <Td mono>{s.latest?.fill == null ? '—' : `${fmtNum(100 * s.latest.fill, 0)}%`}</Td>
                    <Td mono>{b.fill ? `${fmtNum(100 * b.fill.median, 0)}% (z>${fmtNum(b.fill.threshold, 1)})` : '—'}</Td>
                    <Td mono>{s.latest?.events ?? 0}</Td>
                    <Td mono>{b.volume ? `${fmtNum(b.volume.median, 0)} / ${o.settings.bucketSeconds}s` : '—'}</Td>
                    <Td mono>{s.latest?.skewMs == null ? '—' : `${fmtNum(s.latest.skewMs / 1000, 1)} s`}</Td>
                    <Td mono>{s.skew_ms ? `${fmtNum(s.skew_ms / 1000, 1)} s` : '—'}</Td>
                    <Td>{s.open_incidents > 0 && <Badge tone="bad">{s.open_incidents} open</Badge>}</Td>
                  </tr>
                );
              })}
            </Table>
          )}</Async>
        </Panel>
        <div className="grid grid-cols-1 md:grid-cols-2 2xl:grid-cols-1 gap-4 content-start">
          <ScenarioPanel />
          <SettingsPanel />
        </div>
      </div>
      {selected && <QualityChart source={selected} />}
      <Panel title="Re-normalization jobs" dense>
        <Async state={jobs} empty={(d) => (d.length ? null : <Empty title="No jobs yet" />)}>{(rows) => (
          <Table head={['Created', 'Source', 'Window', 'Status', 'Processed', 'Changed', 'Improved', 'Fields recovered', 'Lossless', 'By']}>
            {rows.map((j) => (
              <tr key={j.id}>
                <Td>{fmtTime(j.created_at)}</Td><Td mono>{j.source_id}</Td>
                <Td mono>{j.window_from ? `${fmtTime(j.window_from)} → ${fmtTime(j.window_to)}` : 'all history'}</Td>
                <Td><Badge tone={statusTone(j.status)}>{j.status}</Badge></Td>
                <Td mono>{j.processed}</Td><Td mono>{j.changed}</Td><Td mono>{j.improved}</Td><Td mono>{j.fields_recovered}</Td>
                <Td mono>{j.processed ? `${fmtNum(100 * j.lossless_ok / j.processed, 1)}%` : '—'}</Td><Td>{j.requested_by}</Td>
              </tr>
            ))}
          </Table>
        )}</Async>
      </Panel>
    </Page>
  );
};

function median(v: number[]): number | null {
  if (!v.length) return null;
  const s = [...v].sort((a, b) => a - b);
  return s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
}

const QualityChart: React.FC<{ source: string }> = ({ source }) => {
  const q = useApi(() => ulpf3.quality(source, 60), [source], 10000);
  return (
    <Panel title={`Quality of ${source} (last 60 min)`}>
      <Async state={q}>{(d) => {
        const t = (b: string) => new Date(b).getTime();
        const fill: Series = { name: 'fill %', color: '#286B78', points: d.buckets.map((b) => [t(b.bucket), b.fill_avg == null ? null : 100 * b.fill_avg]) };
        const norm: Series = { name: 'normalized %', color: '#2F7D5C', points: d.buckets.map((b) => [t(b.bucket), b.events ? (100 * b.normalized) / b.events : null]) };
        const vol: Series = { name: 'events / bucket', color: '#B7791F', points: d.buckets.map((b) => [t(b.bucket), b.events]) };
        const skew: Series = { name: 'clock offset (s)', color: '#B83A3A', points: d.buckets.map((b) => [t(b.bucket), b.skew_ms == null ? null : b.skew_ms / 1000]) };
        return (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
            <div><div className="text-[11px] text-[#5E6561] mb-1">Parsing quality</div><LineChart series={[fill, norm]} height={200} /></div>
            <div><div className="text-[11px] text-[#5E6561] mb-1">Volume {d.baselines.volume ? `(normal ${fmtNum(d.baselines.volume.median, 0)})` : ''}</div><LineChart series={[vol]} height={200} /></div>
            <div><div className="text-[11px] text-[#5E6561] mb-1">Device clock offset</div><LineChart series={[skew]} height={200} /></div>
          </div>
        );
      }}</Async>
    </Panel>
  );
};

const ScenarioPanel: React.FC = () => {
  const sc = useApi(() => ulpf3.scenarios(), [], 3000);
  const [device, setDevice] = useState('fgt-dc-01');
  const [skew, setSkew] = useState(240);
  const [minutes, setMinutes] = useState(10);
  const inject = (kind: string, value: unknown) => ulpf3.inject(device, kind, value, minutes * 60).then(sc.reload);
  return (
    <Panel title="Fault lab (demo traffic)">
      <Async state={sc}>{(s) => (
        <div className="space-y-2 text-[12px]">
          <div className="grid grid-cols-2 gap-2">
            <Field label="Device">
              <select className={`${inputClass} w-full`} value={device} onChange={(e) => setDevice(e.target.value)}>
                {s.devices.map((d) => <option key={d}>{d}</option>)}
              </select>
            </Field>
            <Field label="Duration (min)"><input type="number" className={`${inputClass} w-full`} value={minutes} min={1} onChange={(e) => setMinutes(Number(e.target.value))} /></Field>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => inject('firmware', true)} title={s.kinds.firmware}>Firmware format change</Button>
            <Button onClick={() => inject('silent', true)} title={s.kinds.silent}>Silence device</Button>
            <input type="number" className={`${inputClass} w-16`} value={skew} onChange={(e) => setSkew(Number(e.target.value))} />
            <Button onClick={() => inject('skewSeconds', skew)} title={s.kinds.skewSeconds}>Clock drift (s)</Button>
          </div>
          {Object.keys(s.active).length > 0 ? (
            <Table head={['Device', 'Active', 'Ends in', '']}>
              {Object.entries(s.active).map(([d, a]) => (
                <tr key={d}>
                  <Td mono>{d}</Td>
                  <Td mono>{Object.entries(a).filter(([k]) => !['until', 'remainingSeconds'].includes(k)).map(([k, v]) => `${k}=${String(v)}`).join(', ')}</Td>
                  <Td mono>{fmtSeconds(a.remainingSeconds)}</Td>
                  <Td><Button variant="ghost" onClick={() => ulpf3.clear(d).then(sc.reload)}>Stop</Button></Td>
                </tr>
              ))}
            </Table>
          ) : <div className="text-[#5E6561]">No scenario active.</div>}
        </div>
      )}</Async>
    </Panel>
  );
};

const SettingsPanel: React.FC = () => {
  const st = useApi(() => ulpf3.settings(), []);
  return (
    <Panel title="Autonomy">
      <Async state={st}>{(s) => (
        <div className="space-y-2 text-[12px]">
          <div className="flex flex-wrap gap-2 items-end">
            <Field label="Run automatically up to tier">
              <select className={inputClass} value={s.autoExecuteMaxTier} onChange={(e) => ulpf3.saveSettings({ autoExecuteMaxTier: Number(e.target.value) }).then(st.reload)}>
                <option value={0}>0 (approve everything)</option><option value={1}>1 (reversible repairs)</option>
              </select>
            </Field>
            <Field label="Dry run">
              <select className={inputClass} value={String(s.dryRun)} onChange={(e) => ulpf3.saveSettings({ dryRun: e.target.value === 'true' }).then(st.reload)}>
                <option value="false">off</option><option value="true">on</option>
              </select>
            </Field>
          </div>
          <KeyValue items={[
            ['Learning window', `${s.learningMinutes} min`], ['Bucket', `${s.bucketSeconds} s`],
            ['Alarm', `${s.consecutiveBuckets} buckets in a row beyond max(z ${s.zFloor}, ${(1 - s.maxFalsePositiveRate) * 100}% quantile of normal)`],
            ['Clock skew threshold', `${s.skewThresholdSeconds} s`], ['Promotion cooldown', `${s.promotionCooldownMinutes} min`],
            ['Kill switch', 'shared with CausalOps remediation (Remediation center)'],
          ]} />
        </div>
      )}</Async>
    </Panel>
  );
};

const IncidentView: React.FC<{ id: string; navigate: Navigate }> = ({ id, navigate }) => {
  const d = useApi(() => ulpf3.incident(id), [id], 3000);
  return (
    <Page title={`Pipeline incident ${id}`} subtitle="Evidence, every decision and its policy rules, verification on live data."
          actions={<Button onClick={() => navigate('/log-health')}>Back</Button>}>
      <Async state={d}>{(x) => <IncidentBody d={x} reload={d.reload} navigate={navigate} />}</Async>
    </Page>
  );
};

const IncidentBody: React.FC<{ d: IncidentDetailU; reload: () => void; navigate: Navigate }> = ({ d, reload, navigate }) => {
  const i: UlpfIncident = d.incident;
  const ev = (i.evidence ?? {}) as Record<string, unknown>;
  return (
    <>
      <div className="grid grid-cols-2 md:grid-cols-6 gap-2">
        <Stat label="Status" value={<Badge tone={statusTone(i.status)}>{i.status}</Badge>} />
        <Stat label="Kind" value={KIND_LABEL[i.kind] ?? i.kind} />
        <Stat label="Source" value={<span className="text-[13px]">{i.source_id}</span>} />
        <Stat label="First affected event" value={<span className="text-[13px]">{fmtTime(i.onset_at)}</span>} />
        <Stat label="Time to detect" value={i.mttd_seconds == null ? '—' : fmtSeconds(i.mttd_seconds)} />
        <Stat label="Time to resolve" value={i.mttr_seconds == null ? (i.status === 'RESOLVED' ? '—' : 'open') : fmtSeconds(i.mttr_seconds)} />
      </div>
      <Panel title={i.title}>
        <div className="text-[12.5px] text-[#171A19] leading-[1.55]">{i.summary}</div>
        {Array.isArray(ev.attributes) && (
          <div className="mt-3"><Table head={['Expected attribute', 'Present before', 'Present now']}>
            {(ev.attributes as { attribute: string; before: number; after: number }[]).map((a) => (
              <tr key={a.attribute}><Td mono>{a.attribute}</Td><Td mono>{fmtNum(100 * a.before, 0)}%</Td>
                <Td><Badge tone={a.after < 0.5 ? 'bad' : 'good'}>{fmtNum(100 * a.after, 0)}%</Badge></Td></tr>
            ))}
          </Table></div>
        )}
        {typeof ev.firstBadEvent === 'string' && (
          <div className="mt-2 text-[12px]">First drifted event: <button className="text-[#286B78] font-code" onClick={() => navigate(`/log-events/${encodeURIComponent(ev.firstBadEvent as string)}`)}>{ev.firstBadEvent as string}</button></div>
        )}
      </Panel>
      {d.actions.map((a) => <ActionCard key={a.id} a={a} reload={reload} />)}
      <Panel title="Timeline" dense>
        <Table head={['Time', 'Event', 'Detail']}>
          {d.timeline.map((t, k) => (
            <tr key={k}><Td mono>{fmtTime(t.at)}</Td><Td><Badge tone={statusTone(t.type.replace('ACTION_', ''))}>{t.type}</Badge></Td>
              <Td mono className="max-w-[900px] break-all text-[11px]">{JSON.stringify(t.payload).slice(0, 400)}</Td></tr>
          ))}
        </Table>
      </Panel>
    </>
  );
};

const ActionCard: React.FC<{ a: UlpfAction; reload: () => void }> = ({ a, reload }) => {
  const p = a.params as Record<string, unknown>;
  const changes = (p.changes as { attribute: string; field: string; replaces: string | null; evidence: string[] }[] | undefined) ?? [];
  const shadow = p.shadow as Record<string, Record<string, { fill: number; normalized: number; lossless: number; events: number }>> | undefined;
  return (
    <Panel title={<span>{a.action.replace('_', ' ')} · tier {a.tier} · {a.automatic ? 'automatic' : 'needs approval'}</span>}
           actions={<>
             <Badge tone={statusTone(a.status)}>{a.status}</Badge>
             {a.status === 'PROPOSED' && <>
               <Button variant="primary" onClick={() => ulpf3.decide(a.id, 'approve').then(reload)}>Approve</Button>
               <Button onClick={() => ulpf3.decide(a.id, 'reject').then(reload)}>Reject</Button>
             </>}
           </>}>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="space-y-3">
          {changes.length > 0 && (
            <Table head={['Repair', 'Evidence']}>
              {changes.map((c) => (
                <tr key={c.field}><Td mono>{c.field} → {c.attribute}{c.replaces ? ` (was ${c.replaces})` : ''}</Td><Td className="text-[11px]">{c.evidence.join('; ')}</Td></tr>
              ))}
            </Table>
          )}
          {shadow && (
            <Table head={['Shadow test', 'Fill on drifted events', 'Fill on earlier events', 'Lossless']}>
              {(['champion', 'challenger'] as const).map((k) => (
                <tr key={k}><Td>{k === 'champion' ? `current v${String(p.fromVersion)}` : `repaired v${String(p.toVersion)}`}</Td>
                  <Td mono>{fmtNum(100 * shadow[k].drifted.fill, 0)}% ({shadow[k].drifted.events})</Td>
                  <Td mono>{fmtNum(100 * shadow[k].previous.fill, 0)}% ({shadow[k].previous.events})</Td>
                  <Td mono>{fmtNum(100 * shadow[k].drifted.lossless, 0)}%</Td></tr>
              ))}
            </Table>
          )}
          {a.result && <KeyValue items={Object.entries(a.result).filter(([, v]) => v !== null).map(([k, v]) => [
            k === 'buckets' ? 'Verified on live buckets' : k,
            k === 'buckets' && Array.isArray(v)
              ? (v as { bucket: string; events: number; fill: number | null }[]).map((b) => `${fmtTime(b.bucket)} fill ${b.fill == null ? '—' : `${fmtNum(100 * b.fill, 0)}%`} (${b.events} events)`).join(' · ')
              : typeof v === 'object' ? JSON.stringify(v) : String(v),
          ] as [string, string])} />}
          <div className="text-[11px] text-[#5E6561]">Created {fmtDateTime(a.created_at)}{a.executed_at ? ` · executed ${fmtTime(a.executed_at)}` : ''}{a.finished_at ? ` · finished ${fmtTime(a.finished_at)}` : ''}{a.decided_by ? ` · by ${a.decided_by}` : ''}</div>
        </div>
        <Table head={['Policy rule', 'Result', 'Detail']}>
          {a.policy.map((r) => (
            <tr key={r.rule}><Td mono>{r.rule}</Td><Td><Badge tone={r.passed ? 'good' : r.kind === 'safety' ? 'bad' : 'warn'}>{r.passed ? 'pass' : 'fail'}</Badge></Td>
              <Td className="text-[11px]">{r.detail}</Td></tr>
          ))}
        </Table>
      </div>
    </Panel>
  );
};

export { ago };
