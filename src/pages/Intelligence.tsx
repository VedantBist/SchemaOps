import React, { useMemo, useState } from 'react';
import { api, type Counterfactual } from '../api/causalops';
import { useApi } from '../hooks/useApi';
import { useEvents } from '../hooks/useEvents';
import { useAuth, useEnv } from '../context/AppContext';
import { CounterfactualPanel } from './Incidents';
import { Async, Badge, Button, Empty, ErrorBox, Field, KeyValue, Page, Panel, Stat, Table, Td, ago, fmtDateTime, fmtNum, fmtPct, inputClass } from '../components/ui';

export const Predictions: React.FC = () => {
  const { envId } = useEnv();
  const preds = useApi(() => api.predictions(envId), [envId], 15000);
  const latest = useMemo(() => {
    const m = new Map<string, NonNullable<typeof preds.data>[number]>();
    (preds.data ?? []).forEach((p) => {
      const k = `${p.service}|${p.horizonSeconds}`;
      if (!m.has(k) || new Date(p.createdAt) > new Date(m.get(k)!.createdAt)) m.set(k, p);
    });
    return [...m.values()].sort((a, b) => b.probability - a.probability || a.service.localeCompare(b.service));
  }, [preds.data]);
  return (
    <Page title="Failure forecasts" subtitle="Probability that each service breaches its SLO within the horizon, from the environment's calibrated forecaster. The method column says whether a learned model or trend extrapolation produced it.">
      <Panel dense>
        <Async state={preds} empty={(d) => (d.length ? null : <Empty title="No forecasts yet">Forecasts are produced once the environment is calibrated.</Empty>)}>
          {() => (
            <Table head={['Service', 'Horizon', 'Probability', 'Risk', 'Method', 'Projected', 'Produced']}>
              {latest.map((p) => (
                <tr key={p.id}>
                  <Td className="font-semibold">{p.service}</Td><Td mono>{p.horizonSeconds}s</Td><Td mono>{fmtPct(p.probability * 100, 2)}</Td>
                  <Td><Badge>{p.riskLevel}</Badge></Td><Td mono className="text-[11px]">{p.method ?? '—'}</Td>
                  <Td className="text-[11px] font-code">{p.factors.map((f) => `${f.name.replace('_projected', '')} ${fmtNum(f.value, 1)}${f.slo !== undefined ? ` / SLO ${f.slo}` : ''}`).join(' · ')}</Td>
                  <Td mono>{ago(p.createdAt)}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Async>
      </Panel>
    </Page>
  );
};

export const Simulation: React.FC = () => {
  const { envId, env } = useEnv();
  const incidents = useApi(() => api.incidents(envId, 'all', 50), [envId]);
  const topo = useApi(() => api.topology(envId), [envId]);
  const [incidentId, setIncidentId] = useState('');
  const [unit, setUnit] = useState('');
  const [magnitude, setMagnitude] = useState(1);
  const [result, setResult] = useState<Counterfactual | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [running, setRunning] = useState(false);
  const inc = incidents.data?.find((i) => i.id === incidentId);
  const units = useMemo(() => {
    const t = topo.data;
    if (!t) return [];
    const ext = env?.config.externalNodes ?? [];
    return [...t.nodes.filter((n) => !ext.includes(n.name)).map((n) => n.name),
      ...t.edges.filter((e) => !ext.includes(e.source)).map((e) => `${e.source}->${e.target}`)];
  }, [topo.data, env]);

  const run = async () => {
    if (!inc || !unit || !envId) return;
    setRunning(true); setError(null); setResult(null);
    const start = new Date(new Date(inc.openedAt).getTime() - 5 * 60000).toISOString();
    const end = (inc.resolvedAt ? new Date(new Date(inc.resolvedAt).getTime() + 60000) : new Date()).toISOString();
    try {
      setResult(await api.counterfactual(envId, start, end, unit, magnitude));
    } catch (e) {
      setError(e as Error);
    } finally {
      setRunning(false);
    }
  };

  return (
    <Page title="Counterfactual simulation"
          subtitle="Pick an incident and a component: the environment's causal model (SCM) abducts the noise from what was observed, holds the component at its baseline and rolls the system forward. Nothing here is replayed or scripted.">
      <Panel title="Question">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3 items-end">
          <Field label="Incident">
            <select className={`${inputClass} w-full`} value={incidentId} onChange={(e) => setIncidentId(e.target.value)}>
              <option value="">Select…</option>
              {(incidents.data ?? []).map((i) => <option key={i.id} value={i.id}>{i.incidentKey} · {i.affectedServices.slice(0, 3).join(', ')}</option>)}
            </select>
          </Field>
          <Field label="Restore this component">
            <select className={`${inputClass} w-full`} value={unit} onChange={(e) => setUnit(e.target.value)}>
              <option value="">Select…</option>
              {units.map((u) => <option key={u}>{u}</option>)}
            </select>
          </Field>
          <Field label={`Share of its deviation removed: ${Math.round(magnitude * 100)}%`}>
            <input type="range" min={0.1} max={1} step={0.1} value={magnitude} onChange={(e) => setMagnitude(Number(e.target.value))} className="w-full" />
          </Field>
          <Button variant="primary" disabled={!inc || !unit || running} onClick={run}>{running ? 'Simulating…' : 'Run counterfactual'}</Button>
        </div>
      </Panel>
      <ErrorBox error={error} />
      {result ? <CounterfactualPanel cf={result} title={`What if ${result.intervention.unit} had been ${Math.round(magnitude * 100)}% restored?`} />
        : <Panel><Empty title="No simulation yet">Results include an ensemble uncertainty band and validity checks (unchanged before the intervention, unreachable nodes unaffected, model fit).</Empty></Panel>}
    </Page>
  );
};

export const Calibration: React.FC = () => {
  const { envId } = useEnv();
  const { can } = useAuth();
  const status = useApi(() => api.calibrationStatus(envId), [envId], 10000);
  const models = useApi(() => api.models(envId), [envId], 30000);
  const [err, setErr] = useState<Error | null>(null);
  useEvents(['calibration.started', 'calibration.completed', 'calibration.failed'], () => { status.reload(); models.reload(); });
  const run = async () => {
    setErr(null);
    try { await api.runCalibration(envId); } catch (e) { setErr(e as Error); }
    status.reload();
  };
  return (
    <Page title="Calibration" subtitle="The engine learns this environment from its own telemetry: baselines, the anomaly gate, the causal model and forecasters, validated on labelled incidents. Retrained on schedule; a new model only replaces the champion if it is not worse."
          actions={can('OPERATOR') && <Button variant="primary" onClick={run}>Calibrate now</Button>}>
      <ErrorBox error={err} />
      <Async state={status}>
        {(s) => {
          // When the latest run kept the champion, its re-evaluation on that run's data is the current truth.
          const latest = s.runs.find((r) => r.status === 'SUCCEEDED');
          const reeval = (latest?.metrics as { champion_evaluation?: { version: string } & Record<string, unknown> } | null)?.champion_evaluation;
          const base = s.champion?.metrics;
          const m = reeval && reeval.version === s.champion?.version
            ? { ...base, gate: reeval.gate as NonNullable<typeof base>['gate'], episodes: { ...base?.episodes, ...(reeval.episodes as object) } as NonNullable<typeof base>['episodes'] }
            : base;
          const ep = m?.episodes;
          return (
            <>
              <div className="grid grid-cols-2 lg:grid-cols-6 gap-3">
                <Stat label="Lifecycle" value={s.environment.status} tone={s.environment.status === 'ACTIVE' ? 'good' : 'warn'} />
                <Stat label="Learning data" value={`${Math.round(s.learning.dataMinutes)} min`} hint={`${s.learning.progressPct}% of the ${s.learning.learningWindowMinutes / 60} h window`} />
                <Stat label="Detection recall" value={ep ? fmtPct(ep.detection_recall * 100, 0) : '—'} hint={ep ? `${ep.count} labelled incidents, median ${ep.median_detection_delay_s}s` : undefined} />
                <Stat label="RCA top-1 / top-2" value={ep?.rca_top1_accuracy !== undefined && ep?.rca_top1_accuracy !== null ? `${fmtPct(ep.rca_top1_accuracy * 100, 0)} / ${fmtPct((ep.rca_top2_accuracy ?? 0) * 100, 0)}` : '—'} hint="leave-one-episode-out" />
                <Stat label="Gate false positives" value={m?.gate?.heldout_false_positive_rate !== undefined && m?.gate?.heldout_false_positive_rate !== null ? fmtPct(m.gate.heldout_false_positive_rate * 100, 2) : '—'} hint={reeval ? 'champion on the latest data' : 'held-out quiet data'} />
                <Stat label="Causal model fit" value={fmtNum(m?.scm?.median_holdout_r2, 3)} hint="median cross-validated R²" />
              </div>
              <Panel title="Current state">
                <KeyValue items={[['Reason', s.statusReason ?? '—'], ['Calibrated', fmtDateTime(s.calibratedAt)], ['Next retrain', fmtDateTime(s.nextRetrainDue)],
                  ['Champion', s.champion ? `${s.champion.version} (sha256 ${(s.champion.checksum ?? '').slice(0, 12)}…)` : 'none']]} />
              </Panel>
              {ep?.per_episode && (
                <Panel title="Validation on labelled incidents" dense>
                  <Table head={['Fault', 'Target', 'Detected', 'Delay', 'Expected', 'Ranked', 'Top-1']}>
                    {ep.per_episode.map((r, i) => (
                      <tr key={i}>
                        <Td mono>{r.type}</Td><Td mono>{r.target}</Td><Td><Badge tone={r.detected ? 'good' : 'bad'}>{r.detected ? 'yes' : 'no'}</Badge></Td>
                        <Td mono>{r.detection_delay_s === null ? '—' : `${r.detection_delay_s.toFixed(0)}s`}</Td><Td mono>{(r.expected ?? []).join(', ')}</Td>
                        <Td mono>{(r.predicted ?? []).slice(0, 2).join(', ')}</Td><Td>{r.concurrent ? <Badge tone="muted">concurrent</Badge> : <Badge tone={r.top1 ? 'good' : 'warn'}>{r.top1 ? '✓' : '2nd'}</Badge>}</Td>
                      </tr>
                    ))}
                  </Table>
                </Panel>
              )}
              <Panel title="Calibration runs" dense>
                <Table head={['Started', 'Mode', 'Status', 'Model', 'Decision']}>
                  {s.runs.map((r) => (
                    <tr key={r.id}><Td mono>{fmtDateTime(r.startedAt)}</Td><Td>{r.mode}</Td><Td><Badge>{r.status}</Badge></Td>
                      <Td mono>{r.modelVersion ?? '—'}</Td><Td className="text-[11.5px] text-[#5E6561]">{r.decision ?? r.error}</Td></tr>
                  ))}
                </Table>
              </Panel>
            </>
          );
        }}
      </Async>
      <Panel title="Model registry" dense>
        <Async state={models} empty={(d) => (d.length ? null : <Empty title="No models yet" />)}>
          {(list) => (
            <Table head={['Version', 'Status', 'Data', 'Created', 'Checksum']}>
              {list.map((m) => (
                <tr key={m.version}><Td mono>{m.version}</Td><Td><Badge>{m.status}</Badge></Td>
                  <Td mono className="text-[11px]">{fmtDateTime(m.dataFrom)} → {fmtDateTime(m.dataTo)}</Td><Td mono>{fmtDateTime(m.createdAt)}</Td>
                  <Td mono className="text-[10.5px]">{(m.checksum ?? '').slice(0, 16)}…</Td></tr>
              ))}
            </Table>
          )}
        </Async>
      </Panel>
    </Page>
  );
};
