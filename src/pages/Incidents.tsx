import React, { useState } from 'react';
import { api, type Counterfactual, type Execution, type Recommendation, type TimelineEvent } from '../api/causalops';
import { ApiError } from '../api/client';
import { useApi } from '../hooks/useApi';
import { useEvents } from '../hooks/useEvents';
import { useAuth, useEnv } from '../context/AppContext';
import { LineChart } from '../components/LineChart';
import { TopologyGraph } from '../components/TopologyGraph';
import { Async, Badge, Button, Empty, ErrorBox, KeyValue, Page, Panel, Stat, Table, Td, ago, fmtDateTime, fmtMs, fmtNum, fmtSeconds, fmtTime, inputClass } from '../components/ui';

export const IncidentList: React.FC<{ navigate: (p: string) => void; state: 'active' | 'resolved' }> = ({ navigate, state }) => {
  const { envId } = useEnv();
  const list = useApi(() => api.incidents(envId, state, 200), [envId, state], 10000);
  useEvents(['incident.created', 'incident.updated', 'incident.resolved'], () => list.reload());
  return (
    <Page title={state === 'active' ? 'Active incidents' : 'Incident history'}
          subtitle={state === 'active' ? 'Opened by an SLO breach or the calibrated anomaly gate; resolved when every affected service is healthy again.'
            : 'Resolved incidents with their measured time to resolve.'}>
      <Panel dense>
        <Async state={list} empty={(d) => (d.length ? null : <Empty title={state === 'active' ? 'No open incidents' : 'No resolved incidents yet'} />)}>
          {(items) => (
            <Table head={['Incident', 'Severity', 'Status', 'Affected', 'Detected by', 'Opened', state === 'active' ? 'Age' : 'Time to resolve']}>
              {items.map((i) => (
                <tr key={i.id} className="hover:bg-[#F7F7F5] cursor-pointer" onClick={() => navigate(`/incidents/${i.id}`)}>
                  <Td><div className="font-code font-semibold">{i.incidentKey}</div><div className="text-[11px] text-[#5E6561] max-w-[420px] truncate">{i.summary}</div></Td>
                  <Td><Badge>{i.severity}</Badge></Td>
                  <Td><Badge>{i.status}</Badge></Td>
                  <Td mono className="text-[11px]">{i.affectedServices.join(', ')}</Td>
                  <Td>{i.detectionSource === 'anomaly_gate' ? 'anomaly gate' : 'SLO breach'}</Td>
                  <Td mono>{fmtDateTime(i.openedAt)}</Td>
                  <Td mono>{i.resolvedAt ? fmtSeconds((new Date(i.resolvedAt).getTime() - new Date(i.openedAt).getTime()) / 1000) : ago(i.openedAt)}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Async>
      </Panel>
    </Page>
  );
};

const EVENT_LABEL: Record<string, string> = {
  DETECTED: 'Detected', SERVICES_AFFECTED: 'Spread to more services', ESCALATED: 'Severity escalated', SLO_BREACH_CONFIRMED: 'SLO breach confirmed',
  CHANGE_CORRELATED: 'Correlated with a recorded change', RCA_COMPLETED: 'Root cause identified', RCA_FAILED: 'RCA failed',
  REMEDIATION_RECOMMENDED: 'Remediation proposed', REMEDIATION_AWAITING_APPROVAL: 'Waiting for approval', REMEDIATION_APPROVED: 'Approved',
  REMEDIATION_REJECTED: 'Rejected', REMEDIATION_EXECUTING: 'Executing', REMEDIATION_EXECUTED: 'Executed', REMEDIATION_VERIFIED: 'Recovery verified',
  REMEDIATION_FAILED: 'Remediation failed', REMEDIATION_ROLLED_BACK: 'Rolled back', ROLLBACK_NOT_APPLICABLE: 'Nothing to roll back',
  REMEDIATION_UNAVAILABLE: 'No safe action available', REMEDIATION_BLOCKED: 'All actions blocked', REMEDIATION_DRY_RUN: 'Dry run',
  REMEDIATION_PLANNING_FAILED: 'Planning failed', RECOVERED: 'Recovered',
};

function describe(e: TimelineEvent): string {
  const p = e.payload ?? {};
  const s = (k: string) => (p[k] === undefined || p[k] === null ? '' : String(p[k]));
  switch (e.eventType) {
    case 'DETECTED': return `${s('source').replace('_', ' ')} on ${(p.services as string[] | undefined)?.join(', ') ?? ''}`;
    case 'SERVICES_AFFECTED': return `${(p.services as string[] | undefined)?.join(', ') ?? ''} (${s('severity')})`;
    case 'RCA_COMPLETED': return `${s('rootCause')} (${s('kind')}) · model ${s('modelVersion')}`;
    case 'REMEDIATION_RECOMMENDED': return `${s('count')} proposals · top: ${s('top')}`;
    case 'REMEDIATION_AWAITING_APPROVAL': return `${s('action')} on ${s('target')} — ${s('reason')}`;
    case 'REMEDIATION_EXECUTING': return `${s('action')} on ${s('target')} (${s('mode')}${p.dryRun ? ', dry run' : ''})`;
    case 'REMEDIATION_EXECUTED': return `${s('detail')} · verifying within ${s('verifyWithinSeconds')}s`;
    case 'REMEDIATION_VERIFIED': return Object.entries((p.watched as Record<string, string>) ?? {}).map(([k, v]) => `${k} ${v}`).join(', ');
    case 'REMEDIATION_FAILED': return `${s('stage')}: ${s('message') || Object.entries((p.watched as Record<string, string>) ?? {}).map(([k, v]) => `${k} ${v}`).join(', ')}`;
    case 'CHANGE_CORRELATED': return ((p.changes as { kind: string; target: string; description: string }[]) ?? []).map((c) => `${c.kind} ${c.target}: ${c.description}`).join('; ');
    default: return s('message') || s('detail') || s('by') || s('reason') || '';
  }
}

export const IncidentDetail: React.FC<{ id: string; navigate: (p: string) => void }> = ({ id, navigate }) => {
  const { env, envId } = useEnv();
  const { can } = useAuth();
  const incident = useApi(() => api.incident(id), [id], 10000);
  const timeline = useApi(() => api.timeline(id), [id], 10000);
  const rca = useApi(() => api.rootCause(id), [id], 20000);
  const recs = useApi(() => api.recommendations(envId, id), [envId, id], 10000);
  const execs = useApi(() => api.executions(envId, id), [envId, id], 5000);
  const audit = useApi(() => api.audit(envId, id), [envId, id], 15000);
  const topo = useApi(() => api.topology(envId), [envId], 15000);
  const [actionError, setActionError] = useState<Error | null>(null);

  useEvents(['incident.updated', 'incident.resolved', 'rca.completed', 'remediation.recommended', 'remediation.approval_required',
    'remediation.executing', 'remediation.executed', 'remediation.verified', 'remediation.failed', 'remediation.rolled_back', 'remediation.escalated'],
  (_t, data) => {
    const d = data as { entityId?: string };
    if (d?.entityId && d.entityId !== id) return;
    incident.reload(); timeline.reload(); rca.reload(); recs.reload(); execs.reload(); audit.reload();
  });

  const act = async (fn: () => Promise<unknown>) => {
    setActionError(null);
    try {
      await fn();
    } catch (e) {
      setActionError(e as Error);
    }
    recs.reload(); execs.reload(); incident.reload(); timeline.reload(); audit.reload();
  };

  const inc = incident.data;
  const root = rca.data?.analysis;
  return (
    <Page title={inc ? `${inc.incidentKey} · ${inc.title}` : 'Incident'}
          subtitle={inc ? <span className="flex gap-1.5 items-center"><Badge>{inc.severity}</Badge><Badge>{inc.status}</Badge>
            <span>opened {fmtDateTime(inc.openedAt)} by {inc.detectionSource === 'anomaly_gate' ? 'the anomaly gate' : 'an SLO breach'}</span></span> : undefined}
          actions={<Button onClick={() => navigate('/incidents')}>← Incidents</Button>}>
      <ErrorBox error={incident.error} onRetry={incident.reload} />
      {inc && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <Stat label="Root cause" value={root ? root.rootCause : '…'} hint={root ? `${root.rootCauseKind} · confidence ${(root.confidence * 100).toFixed(0)}%` : 'analysis pending'} />
          <Stat label="Affected" value={inc.affectedServices.length} hint={inc.affectedServices.join(', ')} />
          <Stat label="Time open" value={fmtSeconds(((inc.resolvedAt ? new Date(inc.resolvedAt).getTime() : Date.now()) - new Date(inc.openedAt).getTime()) / 1000)}
                hint={inc.resolvedAt ? `resolved ${fmtTime(inc.resolvedAt)}` : 'still open'} tone={inc.resolvedAt ? 'good' : 'warn'} />
          <Stat label="Remediation" value={execs.data?.[0]?.status ?? (recs.data?.length ? recs.data[0].status : 'none')}
                hint={execs.data?.[0] ? `${execs.data[0].actionId} on ${execs.data[0].targetNode}` : undefined} />
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_440px] gap-3">
        <div className="space-y-3">
          <Panel title="Where it is">
            <Async state={topo}>
              {(t) => <TopologyGraph nodes={t.nodes} edges={t.edges} externalNodes={env?.config.externalNodes}
                                     rootCause={root?.rootCause} affected={inc?.affectedServices} onSelect={(n) => navigate(`/services/${n}`)} />}
            </Async>
          </Panel>
          <RootCausePanel state={rca} onRerun={can('OPERATOR') ? () => act(() => api.runRca(id)) : undefined} />
          {rca.data?.counterfactual && <CounterfactualPanel cf={rca.data.counterfactual.result} />}
        </div>
        <Panel title="Timeline" dense>
          <Async state={timeline}>
            {(events) => (
              <ol className="relative px-3 py-2 space-y-2">
                {events.map((e) => (
                  <li key={e.id} className="text-[12px]">
                    <div className="flex items-center gap-2">
                      <span className="font-code text-[10.5px] text-[#858C87] w-[62px] shrink-0">{fmtTime(e.occurredAt)}</span>
                      <span className="font-semibold">{EVENT_LABEL[e.eventType] ?? e.eventType}</span>
                    </div>
                    <div className="pl-[70px] text-[11.5px] text-[#5E6561] break-words">{describe(e)}</div>
                  </li>
                ))}
              </ol>
            )}
          </Async>
        </Panel>
      </div>

      <ErrorBox error={actionError} />
      <RemediationPanel recs={recs} execs={execs} canOperate={can('OPERATOR')} act={act} />
      <Panel title="Audit trail" dense>
        <Async state={audit} empty={(d) => (d.length ? null : <Empty title="No remediation decisions recorded for this incident" />)}>
          {(entries) => (
            <Table head={['Time', 'Actor', 'Action', 'Entity', 'Detail']}>
              {entries.map((a) => (
                <tr key={a.id}>
                  <Td mono>{fmtTime(a.at)}</Td><Td>{a.actor}</Td><Td><Badge tone="muted">{a.action}</Badge></Td><Td>{a.entityType}</Td>
                  <Td className="text-[11px] text-[#5E6561] max-w-[560px]"><code className="break-all">{JSON.stringify(a.detail).slice(0, 260)}</code></Td>
                </tr>
              ))}
            </Table>
          )}
        </Async>
      </Panel>
    </Page>
  );
};

const RootCausePanel: React.FC<{ state: ReturnType<typeof useApi<import('../api/causalops').RootCause>>; onRerun?: () => void }> = ({ state, onRerun }) => {
  if (state.error && state.error.status === 404) {
    return <Panel title="Root cause analysis"><Empty title="Not analysed yet">The analysis runs automatically once the incident has had time to propagate (and the environment is calibrated).</Empty>
      {onRerun && <div className="text-center"><Button onClick={onRerun}>Run analysis now</Button></div>}</Panel>;
  }
  return (
    <Panel title="Root cause analysis" actions={onRerun && <Button onClick={onRerun}>Re-run</Button>}>
      <Async state={state}>
        {(r) => (
          <div className="space-y-3">
            <KeyValue items={[['Root cause', `${r.analysis.rootCause} (${r.analysis.rootCauseKind})`],
              ['Confidence', `${(r.analysis.confidence * 100).toFixed(0)}% of the ranked score`], ['Model', r.analysis.modelVersion],
              ['Method', r.analysis.methodology], ['Analysed', fmtDateTime(r.analysis.completedAt)]]} />
            <ul className="space-y-1">
              {r.analysis.evidence.map((e, i) => (
                <li key={i} className="text-[12px] flex gap-2"><Badge tone={e.type === 'root_cause' ? 'bad' : 'muted'}>{e.type === 'root_cause' ? 'cause' : 'symptom'}</Badge><span>{e.statement}</span></li>
              ))}
            </ul>
            <Table head={['Candidate', 'Score', 'Counterfactual', 'Unexplained', 'Anomaly', 'Precedence', 'Graph', 'Top signals']}>
              {r.candidates.map((c) => (
                <tr key={c.service}>
                  <Td mono className="font-semibold">{c.service}</Td><Td mono>{fmtNum(c.score, 3)}</Td>
                  {['counterfactual', 'residual', 'anomaly', 'precedence', 'graph'].map((k) => <Td key={k} mono>{fmtNum(c.detail?.components?.[k], 2)}</Td>)}
                  <Td className="text-[11px] font-code">{(c.detail?.signals ?? []).filter((s) => s.max_probability >= 0.5).slice(0, 3)
                    .map((s) => `${s.metric} z=${s.max_z ?? '—'}`).join(' · ') || '—'}</Td>
                </tr>
              ))}
            </Table>
          </div>
        )}
      </Async>
    </Panel>
  );
};

export const CounterfactualPanel: React.FC<{ cf: Counterfactual; title?: string }> = ({ cf, title }) => {
  const entries = Object.keys(cf.entry_impact ?? {});
  const [node, setNode] = useState(entries[0] ?? Object.keys(cf.nodes)[0]);
  const [metric, setMetric] = useState<'latency' | 'error'>('latency');
  const series = cf.nodes[node]?.[metric];
  const ts = cf.timestamps.map((t) => new Date(t).getTime());
  const impact = cf.entry_impact?.[node];
  return (
    <Panel title={title ?? `What if ${cf.intervention.unit} had been at its baseline? (SCM counterfactual)`}
           actions={<>
             <select className={inputClass} value={metric} onChange={(e) => setMetric(e.target.value as 'latency' | 'error')}>
               <option value="latency">p99 latency (ms)</option><option value="error">error rate (%)</option></select>
             <select className={inputClass} value={node} onChange={(e) => setNode(e.target.value)}>
               {Object.keys(cf.nodes).map((n) => <option key={n}>{n}</option>)}</select></>}>
      <div className="flex flex-wrap gap-2 mb-2 text-[11.5px]">
        <Badge>{cf.validity.status}</Badge>
        <span>{cf.ensemble_members} ensemble members</span>
        {impact && <span>avoided at {node}: peak <b>{fmtMs(impact.peak_avoided_latency_ms)}</b>, mean <b>{fmtMs(impact.mean_avoided_latency_ms)}</b>
          {impact.peak_avoided_error_pct !== null ? <>, errors up to <b>{impact.peak_avoided_error_pct.toFixed(1)} pts</b></> : null}</span>}
        <span>restored: {cf.restored_nodes.join(', ') || 'none'}</span>
        {cf.validity.warnings.map((w) => <span key={w} className="text-[#9A6412]">⚠ {w}</span>)}
      </div>
      {series ? (
        <LineChart unit={metric === 'latency' ? ' ms' : '%'} markers={cf.intervention.start ? [{ at: new Date(cf.intervention.start).getTime(), label: 'intervention' }] : []}
                   series={[{ name: 'observed', color: '#B83A3A', points: ts.map((t, i) => [t, series.observed[i]]) },
                     { name: 'counterfactual', color: '#286B78', dashed: true, points: ts.map((t, i) => [t, series.counterfactual[i]]),
                       band: { low: series.low, high: series.high } }]} />
      ) : <Empty title={`No ${metric} series for ${node}`} />}
      <div className="text-[10.5px] text-[#858C87] mt-1">{cf.intervention.semantics}; band = 5–95% across the bootstrap ensemble.</div>
    </Panel>
  );
};

const RemediationPanel: React.FC<{
  recs: ReturnType<typeof useApi<Recommendation[]>>; execs: ReturnType<typeof useApi<Execution[]>>;
  canOperate: boolean; act: (fn: () => Promise<unknown>) => Promise<void>;
}> = ({ recs, execs, canOperate, act }) => {
  const { user } = useAuth();
  const [open, setOpen] = useState<string | null>(null);
  const current = (recs.data ?? []).filter((r) => r.status !== 'SUPERSEDED');
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
      <Panel title="Recommended mitigations" dense>
        <Async state={recs} empty={() => (current.length ? null : <Empty title="No proposals">Proposals appear once the root cause is known.</Empty>)}>
          {() => (
            <ul className="divide-y divide-[#F0F1EE]">
              {current.map((r) => (
                <li key={r.id} className="px-3 py-2 space-y-1">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-[12.5px]"><span className="font-semibold">{r.actionName}</span> on <span className="font-code">{r.targetNode}</span></div>
                    <div className="flex gap-1"><Badge tone="muted">tier {r.tier}</Badge><Badge>{r.status}</Badge></div>
                  </div>
                  <div className="text-[11.5px] text-[#5E6561]">{r.rationale}</div>
                  <div className="text-[10.5px] font-code text-[#858C87]">score {fmtNum(r.score, 3)} · {r.executor}.{r.operation} → {r.binding} · {r.reversible ? 'reversible' : 'no persistent change'}
                    {' · '}<button className="text-[#286B78]" onClick={() => setOpen(open === r.id ? null : r.id)}>{r.policy?.summary ?? 'policy'}</button></div>
                  {open === r.id && (
                    <ul className="grid grid-cols-1 md:grid-cols-2 gap-x-3 gap-y-0.5 text-[11px] pt-1">
                      {(r.policy?.rules ?? []).map((p) => (
                        <li key={p.id} className="flex gap-1.5"><span className={p.passed ? 'text-[#2F7D5C]' : p.safety ? 'text-[#B83A3A]' : 'text-[#9A6412]'}>{p.passed ? '✓' : '✗'}</span>
                          <span className="font-code">{p.id}</span><span className="text-[#858C87] truncate" title={p.detail}>{p.detail}</span></li>
                      ))}
                    </ul>
                  )}
                  {canOperate && ['AWAITING_APPROVAL', 'PROPOSED'].includes(r.status) && (
                    <div className="flex gap-2 pt-1">
                      <Button variant="primary" onClick={() => act(() => api.approve(r.id, user?.username ?? 'operator', 'approved in UI'))}>Approve & execute</Button>
                      <Button onClick={() => act(() => api.reject(r.id, user?.username ?? 'operator', 'rejected in UI'))}>Reject</Button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Async>
      </Panel>
      <Panel title="Executions and verification" dense>
        <Async state={execs} empty={(d) => (d.length ? null : <Empty title="Nothing executed" />)}>
          {(list) => (
            <ul className="divide-y divide-[#F0F1EE]">
              {list.map((e) => (
                <li key={e.id} className="px-3 py-2 space-y-1">
                  <div className="flex items-center justify-between">
                    <div className="text-[12.5px]"><span className="font-semibold">{e.actionId}</span> on <span className="font-code">{e.binding}</span>
                      <span className="text-[11px] text-[#858C87]"> · {e.mode}{e.approvedBy ? ` by ${e.approvedBy}` : ''}{e.dryRun ? ' · dry run' : ''}</span></div>
                    <Badge>{e.status}</Badge>
                  </div>
                  <div className="text-[11.5px] text-[#5E6561]">{String((e.result as { detail?: string })?.detail ?? (e.result as { error?: string })?.error ?? '')}</div>
                  {e.verification?.watched && (
                    <div className="text-[11px] font-code">
                      healthy streak {e.verification.healthyStreak ?? e.healthyStreak}/{e.verification.requiredHealthySamples} ·{' '}
                      {Object.entries(e.verification.watched).map(([k, v]) => `${k}: ${v}`).join(', ')}
                    </div>
                  )}
                  {e.rollbackResult && <div className="text-[11px] text-[#9A6412]">rollback: {String((e.rollbackResult as { detail?: string; error?: string }).detail ?? (e.rollbackResult as { error?: string }).error)}</div>}
                  <div className="text-[10.5px] font-code text-[#858C87]">started {fmtTime(e.startedAt)}{e.finishedAt ? ` · finished ${fmtTime(e.finishedAt)}` : ''}</div>
                  {canOperate && e.rollbackState !== null && e.rollbackState !== undefined && ['VERIFIED', 'FAILED', 'VERIFYING'].includes(e.status) && (
                    <Button variant="danger" onClick={() => act(() => api.rollback(e.id, user?.username ?? 'operator', 'manual rollback from UI'))}>Roll back</Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Async>
      </Panel>
    </div>
  );
};

export { ApiError };
