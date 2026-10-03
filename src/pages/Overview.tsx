import React from 'react';
import { api } from '../api/causalops';
import { useApi } from '../hooks/useApi';
import { useEvents } from '../hooks/useEvents';
import { useEnv } from '../context/AppContext';
import { TopologyGraph } from '../components/TopologyGraph';
import { Async, Badge, Empty, Page, Panel, Stat, Table, Td, ago, fmtMs, fmtPct, fmtRate, fmtSeconds, toneOf } from '../components/ui';

export const Overview: React.FC<{ navigate: (p: string) => void }> = ({ navigate }) => {
  const { env, envId } = useEnv();
  const topo = useApi(() => api.topology(envId), [envId], 10000);
  const incidents = useApi(() => api.incidents(envId, 'active', 10), [envId], 10000);
  const executions = useApi(() => api.executions(envId), [envId], 15000);
  const outcomes = useApi(() => api.outcomes(envId), [envId], 30000);
  const calib = useApi(() => api.calibrationStatus(envId), [envId], 30000);

  useEvents(['telemetry.ingested', 'incident.created', 'incident.resolved', 'remediation.verified'], (t) => {
    if (t === 'telemetry.ingested') topo.reload();
    else { incidents.reload(); executions.reload(); outcomes.reload(); }
  });

  const services = (topo.data?.nodes ?? []).filter((n) => !(env?.config.externalNodes ?? []).includes(n.name));
  const bad = services.filter((s) => ['degraded', 'critical'].includes(String(s.status).toLowerCase()));
  const first = incidents.data?.[0];
  const auto = outcomes.data?.summary?.AUTO_REMEDIATED;
  const manual = outcomes.data?.summary?.NOT_REMEDIATED;

  return (
    <Page title="Command center" subtitle={env ? `Environment ${env.name} · ${env.status}${env.statusReason ? ` — ${env.statusReason}` : ''}` : 'No environment configured'}>
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <Stat label="Services" value={services.length} hint={`${bad.length} degraded`} tone={bad.length ? 'warn' : 'good'} />
        <Stat label="Open incidents" value={incidents.data?.length ?? '—'} tone={(incidents.data?.length ?? 0) > 0 ? 'bad' : 'good'} />
        <Stat label="Models" value={calib.data?.environment.status ?? '—'} tone={toneOf(calib.data?.environment.status)}
              hint={calib.data?.nextRetrainDue ? `retrain ${new Date(calib.data.nextRetrainDue).toLocaleDateString()}` : undefined} />
        <Stat label="Median MTTR · auto-remediated" value={fmtSeconds(auto?.medianMttrSeconds)} hint={auto ? `${auto.resolvedIncidents} incidents` : 'none yet'} tone="good" />
        <Stat label="Median MTTR · no remediation" value={fmtSeconds(manual?.medianMttrSeconds)} hint={manual ? `${manual.resolvedIncidents} incidents` : 'none yet'} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_420px] gap-3">
        <Panel title="Service graph (discovered from traces)" actions={<button className="text-[11px] text-[#286B78]" onClick={() => navigate('/topology')}>Open topology →</button>}>
          <Async state={topo}>
            {(t) => (
              <TopologyGraph nodes={t.nodes} edges={t.edges} externalNodes={env?.config.externalNodes}
                             affected={first?.affectedServices} onSelect={(n) => navigate(`/services/${n}`)} />
            )}
          </Async>
        </Panel>
        <Panel title="Open incidents" dense>
          <Async state={incidents} empty={(d) => (d.length ? null : <Empty title="All quiet">No open incidents in this environment.</Empty>)}>
            {(list) => (
              <ul className="divide-y divide-[#F0F1EE]">
                {list.map((i) => (
                  <li key={i.id} className="px-3 py-2 cursor-pointer hover:bg-[#F7F7F5]" onClick={() => navigate(`/incidents/${i.id}`)}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-code text-[11.5px] font-semibold">{i.incidentKey}</span>
                      <span className="flex gap-1"><Badge>{i.severity}</Badge><Badge>{i.status}</Badge></span>
                    </div>
                    <div className="text-[11.5px] text-[#5E6561] mt-0.5 line-clamp-2">{i.summary}</div>
                    <div className="text-[10.5px] text-[#858C87] font-code mt-0.5">opened {ago(i.openedAt)} · {i.detectionSource}</div>
                  </li>
                ))}
              </ul>
            )}
          </Async>
        </Panel>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <Panel title="Live service health" dense>
          <Async state={topo}>
            {() => (
              <Table head={['Service', 'Status', 'p99', 'Baseline', 'Errors', 'Traffic']}>
                {services.map((s) => (
                  <tr key={s.name} className="hover:bg-[#F7F7F5] cursor-pointer" onClick={() => navigate(`/services/${s.name}`)}>
                    <Td className="font-semibold">{s.name}<span className="ml-1 text-[10px] text-[#858C87] font-code">{s.kind}</span></Td>
                    <Td><Badge>{s.stale ? 'STALE' : s.status}</Badge></Td>
                    <Td mono>{fmtMs(s.latencyP99)}</Td>
                    <Td mono className="text-[#858C87]">{fmtMs(s.baselineLatency)}</Td>
                    <Td mono>{fmtPct(s.errorRate)}</Td>
                    <Td mono>{fmtRate(s.requestRate)}</Td>
                  </tr>
                ))}
              </Table>
            )}
          </Async>
        </Panel>
        <Panel title="Recent remediation" dense actions={<button className="text-[11px] text-[#286B78]" onClick={() => navigate('/remediation')}>Remediation center →</button>}>
          <Async state={executions} empty={(d) => (d.length ? null : <Empty title="No actions yet">Executed remediations appear here with their verification verdict.</Empty>)}>
            {(list) => (
              <Table head={['When', 'Action', 'Target', 'Mode', 'Verdict']}>
                {list.slice(0, 8).map((e) => (
                  <tr key={e.id} className="hover:bg-[#F7F7F5] cursor-pointer" onClick={() => navigate(`/incidents/${e.incidentId}`)}>
                    <Td mono>{ago(e.startedAt)}</Td>
                    <Td>{e.actionId}</Td>
                    <Td mono>{e.targetNode}</Td>
                    <Td><Badge tone={e.mode === 'AUTO' ? 'info' : 'muted'}>{e.mode}</Badge></Td>
                    <Td><Badge>{e.status}</Badge></Td>
                  </tr>
                ))}
              </Table>
            )}
          </Async>
        </Panel>
      </div>
    </Page>
  );
};
