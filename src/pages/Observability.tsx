import React, { useMemo, useState } from 'react';
import { api } from '../api/causalops';
import { useApi } from '../hooks/useApi';
import { useEnv } from '../context/AppContext';
import { LineChart, type Series } from '../components/LineChart';
import { seriesOf } from './Services';
import { Async, Badge, Button, Empty, Field, Page, Panel, Table, Td, fmtTime, inputClass } from '../components/ui';

const COLORS = ['#286B78', '#B83A3A', '#B7791F', '#6B4FA0', '#2F7D5C', '#C2410C', '#4B5563'];
const METRICS = [
  ['p99Latency', 'p99 latency (ms)'], ['p95Latency', 'p95 latency (ms)'], ['p50Latency', 'p50 latency (ms)'],
  ['errorRate', 'error rate (%)'], ['requestRate', 'traffic (req/s)'], ['anomalyScore', 'anomaly score'],
  ['dbLatency', 'DB client p99 (ms)'], ['poolUtilization', 'connection pool use (%)'],
] as const;

export const Metrics: React.FC = () => {
  const { env, envId } = useEnv();
  const services = useApi(() => api.services(envId), [envId]);
  const [metric, setMetric] = useState<(typeof METRICS)[number][0]>('p99Latency');
  const [minutes, setMinutes] = useState(30);
  const names = (services.data ?? []).map((s) => s.name).filter((n) => !(env?.config.externalNodes ?? []).includes(n));
  const all = useApi(async () => Promise.all(names.map((n) => api.metrics(envId, n, minutes))), [envId, names.join(','), minutes], 10000);
  const series: Series[] = (all.data ?? []).map((m, i) => ({ name: m.service, color: COLORS[i % COLORS.length], points: seriesOf(m.samples, metric) }))
    .filter((s) => s.points.some(([, v]) => v !== null));
  return (
    <Page title="Metrics" subtitle="Stored measurements (from Prometheus via the ingestion pipeline), all services on one chart."
          actions={<>
            <select className={inputClass} value={metric} onChange={(e) => setMetric(e.target.value as typeof metric)}>
              {METRICS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
            <select className={inputClass} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
              {[15, 30, 60, 180, 720].map((v) => <option key={v} value={v}>last {v >= 60 ? `${v / 60} h` : `${v} min`}</option>)}
            </select>
          </>}>
      <Panel title={METRICS.find(([k]) => k === metric)?.[1]}>
        <Async state={all}>{() => (series.length ? <LineChart series={series} height={340} /> : <Empty title="No samples for this metric" />)}</Async>
      </Panel>
    </Page>
  );
};

export const Logs: React.FC<{ query?: URLSearchParams }> = ({ query }) => {
  const { env, envId } = useEnv();
  const services = useApi(() => api.services(envId), [envId]);
  const [service, setService] = useState(query?.get('service') ?? '');
  const [contains, setContains] = useState('');
  const [traceId, setTraceId] = useState(query?.get('traceId') ?? '');
  const [minutes, setMinutes] = useState(60);
  const [submitted, setSubmitted] = useState({ service, contains, traceId, minutes });
  const logs = useApi(() => api.logs(envId, { ...submitted, limit: 300 }), [envId, JSON.stringify(submitted)], 15000);
  return (
    <Page title="Logs" subtitle="Live from Loki (OpenTelemetry logs, correlated with traces).">
      <Panel>
        <form className="grid grid-cols-1 md:grid-cols-5 gap-2 items-end" onSubmit={(e) => { e.preventDefault(); setSubmitted({ service, contains, traceId, minutes }); }}>
          <Field label="Service">
            <select className={`${inputClass} w-full`} value={service} onChange={(e) => setService(e.target.value)}>
              <option value="">all</option>
              {(services.data ?? []).filter((s) => s.kind === 'service' && !(env?.config.externalNodes ?? []).includes(s.name)).map((s) => <option key={s.name}>{s.name}</option>)}
            </select>
          </Field>
          <Field label="Contains"><input className={`${inputClass} w-full`} value={contains} onChange={(e) => setContains(e.target.value)} placeholder="text" /></Field>
          <Field label="Trace id"><input className={`${inputClass} w-full font-code`} value={traceId} onChange={(e) => setTraceId(e.target.value.trim())} /></Field>
          <Field label="Window">
            <select className={`${inputClass} w-full`} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
              {[15, 60, 180, 720, 1440].map((v) => <option key={v} value={v}>last {v >= 60 ? `${v / 60} h` : `${v} min`}</option>)}
            </select>
          </Field>
          <Button variant="primary" type="submit">Search</Button>
        </form>
      </Panel>
      <Panel dense>
        <Async state={logs} empty={(d) => (d.length ? null : <Empty title="No log lines in this window">The reference services log at startup and on errors; widen the window or inject a fault.</Empty>)}>
          {(lines) => (
            <div className="font-code text-[11px] max-h-[640px] overflow-y-auto divide-y divide-[#F3F4F1]">
              {lines.map((l, i) => (
                <div key={i} className="px-3 py-1 flex gap-2">
                  <span className="text-[#858C87] shrink-0">{fmtTime(l.timestamp)}</span>
                  <Badge tone={l.level === 'ERROR' ? 'bad' : l.level === 'WARN' ? 'warn' : 'muted'}>{l.level ?? '—'}</Badge>
                  <span className="text-[#286B78] shrink-0">{l.service}</span>
                  <span className="break-all">{l.message}</span>
                  {l.traceId && <a className="text-[#286B78] shrink-0 ml-auto" href={`#/traces/${l.traceId}`}>trace</a>}
                </div>
              ))}
            </div>
          )}
        </Async>
      </Panel>
    </Page>
  );
};

interface Span { name: string; service: string; start: number; end: number; error: boolean; spanId: string; parent: string | null }

function flatten(trace: Record<string, unknown>): Span[] {
  const spans: Span[] = [];
  const batches = (trace.batches ?? trace.resourceSpans ?? []) as Record<string, unknown>[];
  for (const b of batches) {
    const attrs = ((b.resource as { attributes?: { key: string; value: { stringValue?: string } }[] })?.attributes) ?? [];
    const service = attrs.find((a) => a.key === 'service.name')?.value.stringValue ?? '?';
    const scopes = (b.scopeSpans ?? b.instrumentationLibrarySpans ?? []) as { spans?: Record<string, unknown>[] }[];
    for (const sc of scopes) for (const s of sc.spans ?? []) {
      spans.push({ name: String(s.name), service, start: Number(s.startTimeUnixNano) / 1e6, end: Number(s.endTimeUnixNano) / 1e6,
        error: (s.status as { code?: unknown })?.code === 2 || (s.status as { code?: unknown })?.code === 'STATUS_CODE_ERROR',
        spanId: String(s.spanId), parent: (s.parentSpanId as string) || null });
    }
  }
  return spans.sort((a, b) => a.start - b.start);
}

export const Traces: React.FC<{ id?: string }> = ({ id }) => {
  const { env, envId } = useEnv();
  const services = useApi(() => api.services(envId), [envId]);
  const [service, setService] = useState('');
  const [minDuration, setMinDuration] = useState(0);
  const traces = useApi(() => api.traces(envId, { service: service || undefined, minDurationMs: minDuration || undefined, minutes: 60, limit: 50 }),
    [envId, service, minDuration], 15000);
  const [selected, setSelected] = useState<string | undefined>(id);
  const detail = useApi(() => (selected ? api.trace(selected) : Promise.resolve(null)), [selected]);
  const spans = useMemo(() => (detail.data ? flatten(detail.data) : []), [detail.data]);
  const t0 = spans.length ? Math.min(...spans.map((s) => s.start)) : 0;
  const t1 = spans.length ? Math.max(...spans.map((s) => s.end)) : 1;
  return (
    <Page title="Traces" subtitle="Live from Tempo.">
      <div className="grid grid-cols-1 xl:grid-cols-[480px_1fr] gap-3">
        <Panel title="Recent traces" dense actions={<>
          <select className={inputClass} value={service} onChange={(e) => setService(e.target.value)}>
            <option value="">all services</option>
            {(services.data ?? []).filter((s) => s.kind === 'service' && !(env?.config.externalNodes ?? []).includes(s.name)).map((s) => <option key={s.name}>{s.name}</option>)}
          </select>
          <select className={inputClass} value={minDuration} onChange={(e) => setMinDuration(Number(e.target.value))}>
            {[0, 100, 500, 1000].map((v) => <option key={v} value={v}>{v ? `≥ ${v} ms` : 'any duration'}</option>)}
          </select></>}>
          <Async state={traces} empty={(d) => (d.length ? null : <Empty title="No traces in the last hour" />)}>
            {(list) => (
              <Table head={['Start', 'Root', 'Duration']}>
                {list.map((t) => (
                  <tr key={t.traceId} className={`cursor-pointer hover:bg-[#F7F7F5] ${selected === t.traceId ? 'bg-[#E9EDE9]' : ''}`} onClick={() => setSelected(t.traceId)}>
                    <Td mono>{fmtTime(t.startTime)}</Td><Td><div>{t.rootService}</div><div className="text-[10.5px] text-[#858C87] font-code">{t.rootOperation}</div></Td>
                    <Td mono>{t.durationMs} ms</Td>
                  </tr>
                ))}
              </Table>
            )}
          </Async>
        </Panel>
        <Panel title={selected ? `Trace ${selected}` : 'Trace'}>
          {!selected ? <Empty title="Select a trace" /> : (
            <Async state={detail}>
              {() => (
                <div className="space-y-1">
                  {spans.map((s) => (
                    <div key={s.spanId} className="grid grid-cols-[220px_1fr_70px] gap-2 items-center text-[11px]">
                      <div className="truncate"><span className="text-[#286B78]">{s.service}</span> <span className="font-code">{s.name}</span></div>
                      <div className="relative h-3 bg-[#F1F2F0] rounded-sm">
                        <div className={`absolute h-3 rounded-sm ${s.error ? 'bg-[#B83A3A]' : 'bg-[#286B78]'}`}
                             style={{ left: `${((s.start - t0) / (t1 - t0 || 1)) * 100}%`, width: `${Math.max(0.5, ((s.end - s.start) / (t1 - t0 || 1)) * 100)}%` }} />
                      </div>
                      <div className="font-code text-right">{(s.end - s.start).toFixed(1)} ms</div>
                    </div>
                  ))}
                  <a className="text-[11.5px] text-[#286B78]" href={`#/logs?traceId=${selected}`}>Logs for this trace →</a>
                </div>
              )}
            </Async>
          )}
        </Panel>
      </div>
    </Page>
  );
};
