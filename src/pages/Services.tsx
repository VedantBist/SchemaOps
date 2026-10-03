import React, { useState } from 'react';
import { api, type Sample } from '../api/causalops';
import { useApi } from '../hooks/useApi';
import { useEnv } from '../context/AppContext';
import { LineChart } from '../components/LineChart';
import { TopologyGraph } from '../components/TopologyGraph';
import { Async, Badge, Empty, KeyValue, Page, Panel, Table, Td, ago, fmtMs, fmtPct, fmtRate, inputClass } from '../components/ui';

export const Topology: React.FC<{ navigate: (p: string) => void }> = ({ navigate }) => {
  const { env, envId } = useEnv();
  const topo = useApi(() => api.topology(envId), [envId], 10000);
  const [selected, setSelected] = useState<string | null>(null);
  const node = topo.data?.nodes.find((n) => n.name === selected);
  return (
    <Page title="Service topology" subtitle="Nodes and call edges discovered from traces (OpenTelemetry service graph); manual additions are marked.">
      <div className="grid grid-cols-1 xl:grid-cols-[1fr_340px] gap-3">
        <Panel title="Graph">
          <Async state={topo}>
            {(t) => <TopologyGraph nodes={t.nodes} edges={t.edges} externalNodes={env?.config.externalNodes} selected={selected} onSelect={setSelected} />}
          </Async>
        </Panel>
        <Panel title={node ? node.name : 'Select a node'}>
          {node ? (
            <div className="space-y-3">
              <KeyValue items={[
                ['Kind', node.kind], ['Status', <Badge key="s">{node.stale ? 'STALE' : node.status}</Badge>], ['Source', node.source],
                ['p99 latency', fmtMs(node.latencyP99)], ['Baseline p99', fmtMs(node.baselineLatency)], ['Error rate', fmtPct(node.errorRate)],
                ['Traffic', fmtRate(node.requestRate)], ['First seen', ago(node.firstSeenAt)], ['Last sample', ago(node.lastSampleAt)],
              ]} />
              <div>
                <div className="text-[11px] font-semibold text-[#5E6561] mb-1">Calls</div>
                <ul className="text-[11.5px] font-code space-y-0.5">
                  {topo.data!.edges.filter((e) => e.source === node.name || e.target === node.name).map((e) => (
                    <li key={e.source + e.target}>{e.source} → {e.target} <span className="text-[#858C87]">{e.callRate?.toFixed(2) ?? '—'}/s</span></li>
                  ))}
                </ul>
              </div>
              <button className="text-[11.5px] text-[#286B78]" onClick={() => navigate(`/services/${node.name}`)}>Open metrics →</button>
            </div>
          ) : <Empty title="No node selected">Click a node in the graph to inspect it.</Empty>}
        </Panel>
      </div>
      <Panel title="Edges" dense>
        <Async state={topo}>
          {(t) => (
            <Table head={['Caller', 'Callee', 'Type', 'Calls/s', 'Failed/s', 'Origin', 'Last seen']}>
              {t.edges.map((e) => (
                <tr key={e.source + e.target}>
                  <Td mono>{e.source}</Td><Td mono>{e.target}</Td><Td>{e.connectionType ?? 'call'}</Td>
                  <Td mono>{e.callRate?.toFixed(2) ?? '—'}</Td><Td mono>{e.failedRate?.toFixed(2) ?? '—'}</Td>
                  <Td>{e.origin}</Td><Td mono>{e.stale ? <Badge tone="muted">stale</Badge> : ago(e.lastSeenAt)}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Async>
      </Panel>
    </Page>
  );
};

export const Services: React.FC<{ navigate: (p: string) => void; id?: string }> = ({ navigate, id }) => {
  const { env, envId } = useEnv();
  const services = useApi(() => api.services(envId), [envId], 10000);
  if (id) return <ServiceDetail name={id} navigate={navigate} />;
  return (
    <Page title="Services" subtitle="Every node CausalOps measures in this environment, with its latest sample.">
      <Panel dense>
        <Async state={services} empty={(d) => (d.length ? null : <Empty title="Nothing discovered yet">Services appear when they send OpenTelemetry traces to the collector.</Empty>)}>
          {(list) => (
            <Table head={['Service', 'Kind', 'Status', 'p99', 'Baseline', 'Errors', 'Traffic', 'Last sample']}>
              {list.filter((s) => !(env?.config.externalNodes ?? []).includes(s.name)).map((s) => (
                <tr key={s.name} className="hover:bg-[#F7F7F5] cursor-pointer" onClick={() => navigate(`/services/${s.name}`)}>
                  <Td className="font-semibold">{s.name}</Td><Td>{s.kind}</Td><Td><Badge>{s.stale ? 'STALE' : s.status}</Badge></Td>
                  <Td mono>{fmtMs(s.latencyP99)}</Td><Td mono className="text-[#858C87]">{fmtMs(s.baselineLatency)}</Td>
                  <Td mono>{fmtPct(s.errorRate)}</Td><Td mono>{fmtRate(s.requestRate)}</Td><Td mono>{ago(s.lastSampleAt)}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Async>
      </Panel>
    </Page>
  );
};

export const seriesOf = (samples: Sample[], key: keyof Sample) =>
  [...samples].reverse().map((s) => [new Date(s.timestamp).getTime(), (s[key] as number | null) ?? null] as [number, number | null]);

export const ServiceDetail: React.FC<{ name: string; navigate: (p: string) => void }> = ({ name, navigate }) => {
  const { env, envId } = useEnv();
  const [minutes, setMinutes] = useState(30);
  const m = useApi(() => api.metrics(envId, name, minutes), [envId, name, minutes], 10000);
  const slo = env?.config.serviceSlos?.[name] ?? env?.config.slo;
  const samples = m.data?.samples ?? [];
  const hasDb = samples.some((s) => s.dbLatency !== null);
  const hasPool = samples.some((s) => s.poolUtilization !== null);
  return (
    <Page title={name} subtitle="Measured from Prometheus every ingestion cycle; anomaly score from the calibrated gate."
          actions={<>
            <select className={inputClass} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
              {[15, 30, 60, 180, 720].map((v) => <option key={v} value={v}>last {v >= 60 ? `${v / 60} h` : `${v} min`}</option>)}
            </select>
            <button className="text-[11.5px] text-[#286B78]" onClick={() => navigate(`/logs?service=${name}`)}>Logs →</button>
          </>}>
      <Async state={m} empty={(d) => (d.samples.length ? null : <Empty title="No samples in this window" />)}>
        {() => (
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
            <Panel title="Latency (ms)">
              <LineChart unit=" ms" threshold={slo ? { value: slo.latencyP99Ms, label: `SLO p99 ${slo.latencyP99Ms} ms` } : undefined}
                         series={[{ name: 'p99', color: '#B83A3A', points: seriesOf(samples, 'p99Latency') },
                           { name: 'p95', color: '#B7791F', points: seriesOf(samples, 'p95Latency') },
                           { name: 'p50', color: '#286B78', points: seriesOf(samples, 'p50Latency') }]} />
            </Panel>
            <Panel title="Errors (%) and traffic (req/s)">
              <LineChart threshold={slo ? { value: slo.errorRatePct, label: `SLO ${slo.errorRatePct}%` } : undefined}
                         series={[{ name: 'error %', color: '#B83A3A', points: seriesOf(samples, 'errorRate') },
                           { name: 'req/s', color: '#286B78', points: seriesOf(samples, 'requestRate') }]} />
            </Panel>
            <Panel title="Anomaly score (calibrated gate)">
              <LineChart yMin={0} series={[{ name: 'anomaly', color: '#6B4FA0', points: seriesOf(samples, 'anomalyScore') }]} />
            </Panel>
            {(hasDb || hasPool) && (
              <Panel title="Database client">
                <LineChart series={[
                  ...(hasDb ? [{ name: 'db p99 ms', color: '#B7791F', points: seriesOf(samples, 'dbLatency') }] : []),
                  ...(hasPool ? [{ name: 'pool use %', color: '#286B78', points: seriesOf(samples, 'poolUtilization') }] : []),
                ]} />
              </Panel>
            )}
          </div>
        )}
      </Async>
    </Page>
  );
};
