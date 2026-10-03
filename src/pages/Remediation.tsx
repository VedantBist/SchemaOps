import React, { useState } from 'react';
import { api } from '../api/causalops';
import { useApi } from '../hooks/useApi';
import { useEvents } from '../hooks/useEvents';
import { useAuth, useEnv } from '../context/AppContext';
import { Async, Badge, Button, Empty, ErrorBox, Page, Panel, Table, Td, ago, fmtSeconds } from '../components/ui';

const HANDLING_LABEL: Record<string, string> = {
  AUTO_REMEDIATED: 'Auto-remediated', APPROVED_REMEDIATION: 'Remediated after approval',
  REMEDIATION_UNSUCCESSFUL: 'Remediation tried, did not fix', NOT_REMEDIATED: 'No remediation',
};

export const Remediation: React.FC<{ navigate: (p: string) => void }> = ({ navigate }) => {
  const { envId } = useEnv();
  const { user, can } = useAuth();
  const policy = useApi(() => api.policy(envId), [envId], 15000);
  const pending = useApi(() => api.recommendations(envId, undefined, 'AWAITING_APPROVAL'), [envId], 8000);
  const execs = useApi(() => api.executions(envId), [envId], 8000);
  const outcomes = useApi(() => api.outcomes(envId), [envId], 20000);
  const [err, setErr] = useState<Error | null>(null);
  useEvents(['remediation.approval_required', 'remediation.executed', 'remediation.verified', 'remediation.failed', 'remediation.rolled_back', 'incident.resolved'],
    () => { pending.reload(); execs.reload(); outcomes.reload(); });

  const change = async (body: { autoExecuteMaxTier?: number; killSwitch?: boolean; dryRun?: boolean }) => {
    setErr(null);
    try {
      await api.setAutonomy(envId, { ...body, changedBy: user?.username ?? 'operator' });
    } catch (e) {
      setErr(e as Error);
    }
    policy.reload();
  };

  const s = policy.data?.settings;
  return (
    <Page title="Remediation center"
          subtitle="Tiered autonomy: low-risk, reversible fixes run on their own when every rule passes; everything else waits for an operator. Every decision is audited.">
      <ErrorBox error={err} />
      <Async state={policy}>
        {(p) => (
          <div className="grid grid-cols-1 xl:grid-cols-[1fr_1fr] gap-3">
            <Panel title="Autonomy">
              <div className="space-y-3 text-[12px]">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[#5E6561]">Run automatically up to tier</span>
                  {[0, 1, 2, 3].map((t) => (
                    <Button key={t} variant={s?.autoExecuteMaxTier === t ? 'primary' : 'secondary'} disabled={!can('ADMIN')}
                            onClick={() => change({ autoExecuteMaxTier: t })}>{t === 0 ? 'none' : t}</Button>
                  ))}
                </div>
                <div className="flex flex-wrap items-center gap-3">
                  <Button variant={s?.killSwitch ? 'danger' : 'secondary'} disabled={!can('OPERATOR')} onClick={() => change({ killSwitch: !s?.killSwitch })}>
                    {s?.killSwitch ? 'Kill switch ON — click to release' : 'Engage kill switch'}
                  </Button>
                  <Button variant={s?.dryRun ? 'primary' : 'secondary'} disabled={!can('ADMIN')} onClick={() => change({ dryRun: !s?.dryRun })}>
                    Dry run: {s?.dryRun ? 'on' : 'off'}
                  </Button>
                  {p.globalKillSwitch && <Badge tone="bad">global kill switch (env var) is on</Badge>}
                </div>
                <div className="text-[11.5px] text-[#5E6561]">
                  Environment {p.environmentStatus}: automatic execution also needs ACTIVE models, RCA confidence ≥ {s?.minRcaConfidence},
                  fresh telemetry, a valid counterfactual, the top root cause, no deployment in progress, cooldown {s?.cooldownMinutes} min,
                  ≤ {s?.maxAutoActionsPerHour} automatic actions/hour and a blast radius ≤ {Math.round((s?.maxAutoBlastRadius ?? 0) * 100)}%.
                  Verification: {s?.verification.healthySamples} healthy samples after {s?.verification.settleSeconds}s, within {s?.verification.windowSeconds}s.
                </div>
                <div className="flex flex-wrap gap-1.5 items-center text-[11.5px]">
                  <span className="text-[#5E6561]">Executors:</span>
                  {Object.keys(s?.executors ?? {}).map((k) => <Badge key={k} tone={p.enabledExecutors.includes(k) ? 'good' : 'muted'}>{k}</Badge>)}
                </div>
              </div>
            </Panel>
            <Panel title="Outcomes (measured from incident timestamps and stored telemetry)">
              <Async state={outcomes} empty={(o) => (Object.keys(o.summary).length ? null : <Empty title="No resolved incidents yet" />)}>
                {(o) => (
                  <Table head={['Handling', 'Resolved', 'Median MTTR', 'Median time to mitigation', 'Median downtime']}>
                    {Object.entries(o.summary).map(([k, v]) => (
                      <tr key={k}>
                        <Td>{HANDLING_LABEL[k] ?? k}</Td><Td mono>{v.resolvedIncidents}</Td><Td mono>{fmtSeconds(v.medianMttrSeconds)}</Td>
                        <Td mono>{fmtSeconds(v.medianTimeToMitigationSeconds)}</Td><Td mono>{fmtSeconds(v.medianDowntimeSeconds)}</Td>
                      </tr>
                    ))}
                  </Table>
                )}
              </Async>
              <div className="text-[10.5px] text-[#858C87] mt-2">Downtime = seconds an entry service ({outcomes.data?.entryServices.join(', ')}) violated its SLO during the incident.</div>
            </Panel>
          </div>
        )}
      </Async>

      <Panel title="Waiting for approval" dense>
        <Async state={pending} empty={(d) => (d.length ? null : <Empty title="Nothing waiting" />)}>
          {(list) => (
            <Table head={['Proposed', 'Action', 'Target', 'Tier', 'Why it needs a human', '']}>
              {list.map((r) => (
                <tr key={r.id}>
                  <Td mono>{ago(r.createdAt)}</Td><Td>{r.actionName}</Td><Td mono>{r.targetNode}</Td><Td mono>{r.tier}</Td>
                  <Td className="text-[11.5px] text-[#5E6561]">{r.policy?.summary}</Td>
                  <Td><Button onClick={() => navigate(`/incidents/${r.incidentId}`)}>Review →</Button></Td>
                </tr>
              ))}
            </Table>
          )}
        </Async>
      </Panel>

      <Panel title="All executions" dense>
        <Async state={execs} empty={(d) => (d.length ? null : <Empty title="No executions yet" />)}>
          {(list) => (
            <Table head={['Started', 'Action', 'Target', 'Mode', 'Status', 'Detail']}>
              {list.map((e) => (
                <tr key={e.id} className="hover:bg-[#F7F7F5] cursor-pointer" onClick={() => navigate(`/incidents/${e.incidentId}`)}>
                  <Td mono>{ago(e.startedAt)}</Td><Td>{e.actionId}</Td><Td mono>{e.binding}</Td>
                  <Td><Badge tone={e.mode === 'AUTO' ? 'info' : 'muted'}>{e.mode}</Badge></Td><Td><Badge>{e.status}</Badge></Td>
                  <Td className="text-[11.5px] text-[#5E6561]">{String((e.result as { detail?: string; error?: string }).detail ?? (e.result as { error?: string }).error ?? '')}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Async>
      </Panel>

      {s && (
        <Panel title="Action catalog (this environment)" dense>
          <Table head={['Action', 'Executor', 'Tier', 'Applies to', 'Addresses', 'Description']}>
            {s.actions.map((a) => (
              <tr key={a.id}>
                <Td><div className="font-semibold">{a.name}</div><div className="font-code text-[10.5px] text-[#858C87]">{a.id}</div></Td>
                <Td mono>{a.executor}.{a.operation}</Td><Td mono>{a.tier}</Td><Td>{a.appliesTo.join(', ')}</Td>
                <Td className="font-code text-[11px]">{a.signals.join(', ')}</Td><Td className="text-[11.5px] text-[#5E6561]">{a.description}</Td>
              </tr>
            ))}
          </Table>
        </Panel>
      )}
    </Page>
  );
};

