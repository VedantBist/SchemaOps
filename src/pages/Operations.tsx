import React, { useState } from 'react';
import { api } from '../api/causalops';
import { useApi } from '../hooks/useApi';
import { useEvents } from '../hooks/useEvents';
import { useAuth, useEnv } from '../context/AppContext';
import { Async, Badge, Button, Empty, ErrorBox, Field, Page, Panel, Table, Td, fmtDateTime, inputClass } from '../components/ui';

const FAULTS: { type: string; label: string; targets: 'service' | 'database' | 'any'; param?: { key: string; label: string; def: number } ; what: string }[] = [
  { type: 'SERVICE_FAILURE', label: 'Service failure (HTTP 503)', targets: 'service', what: 'The service answers every request with 503 until the fault ends (in-process).' },
  { type: 'ERROR_RATE', label: 'Error rate', targets: 'service', param: { key: 'errorRatePct', label: 'Failing requests (%)', def: 30 }, what: 'A share of requests fail (in-process).' },
  { type: 'SERVICE_LATENCY', label: 'Service latency', targets: 'service', param: { key: 'latencyMs', label: 'Added latency (ms)', def: 600 }, what: 'Every request is slowed down (in-process).' },
  { type: 'CONNECTION_POOL_SATURATION', label: 'Connection pool saturation', targets: 'service', what: 'Database connections are held so requests queue for the pool (in-process).' },
  { type: 'DB_LATENCY', label: 'Database latency', targets: 'database', param: { key: 'latencyMs', label: 'Added latency (ms)', def: 400 }, what: 'Real network delay on the database link (Toxiproxy).' },
  { type: 'NETWORK_LATENCY', label: 'Network latency into a service', targets: 'service', param: { key: 'latencyMs', label: 'Added latency (ms)', def: 450 }, what: 'Real network delay on every call into the service (Toxiproxy).' },
];

export const FaultLab: React.FC = () => {
  const { env, envId } = useEnv();
  const topo = useApi(() => api.topology(envId), [envId]);
  const faults = useApi(() => api.faults(envId), [envId], 5000);
  const [type, setType] = useState(FAULTS[0].type);
  const [target, setTarget] = useState('');
  const [duration, setDuration] = useState(300);
  const [param, setParam] = useState<number | ''>('');
  const [err, setErr] = useState<Error | null>(null);
  useEvents(['fault.started', 'fault.stopped', 'faults.cleared'], () => faults.reload());
  const spec = FAULTS.find((f) => f.type === type)!;
  const ext = env?.config.externalNodes ?? [];
  const targets = (topo.data?.nodes ?? []).filter((n) => !ext.includes(n.name) && (spec.targets === 'any' || n.kind === spec.targets)).map((n) => n.name);

  const inject = async () => {
    setErr(null);
    try {
      await api.injectFault(envId, { type, target: target || targets[0], severity: 'HIGH', durationSeconds: duration,
        parameters: spec.param ? { [spec.param.key]: param === '' ? spec.param.def : param } : {} });
    } catch (e) { setErr(e as Error); }
    faults.reload();
  };
  const run = async (fn: () => Promise<unknown>) => { setErr(null); try { await fn(); } catch (e) { setErr(e as Error); } faults.reload(); };

  return (
    <Page title="Fault lab" subtitle="Inject real faults into the reference system to watch detection, root-cause analysis and remediation end to end. Every fault is recorded as labelled ground truth for calibration.">
      <Panel title="Inject">
        <div className="grid grid-cols-1 md:grid-cols-5 gap-3 items-end">
          <Field label="Fault"><select className={`${inputClass} w-full`} value={type} onChange={(e) => { setType(e.target.value); setTarget(''); setParam(''); }}>
            {FAULTS.map((f) => <option key={f.type} value={f.type}>{f.label}</option>)}</select></Field>
          <Field label="Target"><select className={`${inputClass} w-full`} value={target || targets[0] || ''} onChange={(e) => setTarget(e.target.value)}>
            {targets.map((t) => <option key={t}>{t}</option>)}</select></Field>
          {spec.param ? (
            <Field label={spec.param.label}><input type="number" className={`${inputClass} w-full`} value={param === '' ? spec.param.def : param} onChange={(e) => setParam(Number(e.target.value))} /></Field>
          ) : <div />}
          <Field label="Duration (s)"><input type="number" min={10} max={3600} className={`${inputClass} w-full`} value={duration} onChange={(e) => setDuration(Number(e.target.value))} /></Field>
          <Button variant="danger" disabled={!targets.length} onClick={inject}>Inject fault</Button>
        </div>
        <div className="text-[11.5px] text-[#5E6561] mt-2">{spec.what}</div>
      </Panel>
      <ErrorBox error={err} />
      <Panel title="Faults" dense actions={<Button onClick={() => run(() => api.clearFaults(envId))}>Stop all</Button>}>
        <Async state={faults} empty={(d) => (d.length ? null : <Empty title="No faults injected yet" />)}>
          {(list) => (
            <Table head={['Started', 'Fault', 'Target', 'Parameters', 'Duration', 'Status', '']}>
              {list.slice(0, 50).map((f) => (
                <tr key={f.id}>
                  <Td mono>{fmtDateTime(f.startedAt)}</Td><Td mono>{f.type}</Td><Td mono>{f.target}</Td>
                  <Td mono className="text-[11px]">{JSON.stringify(f.parameters)}</Td><Td mono>{f.durationSeconds}s</Td><Td><Badge>{f.status}</Badge></Td>
                  <Td>{f.status === 'ACTIVE' && <Button onClick={() => run(() => api.stopFault(f.id))}>Stop</Button>}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Async>
      </Panel>
    </Page>
  );
};

export const Changes: React.FC = () => {
  const { envId } = useEnv();
  const { user, can } = useAuth();
  const changes = useApi(() => api.changes(envId), [envId], 15000);
  const [kind, setKind] = useState('DEPLOYMENT');
  const [target, setTarget] = useState('');
  const [description, setDescription] = useState('');
  const [minutes, setMinutes] = useState(10);
  const [err, setErr] = useState<Error | null>(null);
  const record = async () => {
    setErr(null);
    try {
      const now = Date.now();
      await api.recordChange({ environmentId: envId, kind, target: target || undefined, startedAt: new Date(now).toISOString(),
        endedAt: new Date(now + minutes * 60000).toISOString(), source: user?.username ?? 'operator', description });
      setDescription('');
    } catch (e) { setErr(e as Error); }
    changes.reload();
  };
  return (
    <Page title="Changes" subtitle="Deployments, restarts, maintenance and remediation actions. Calibration keeps these windows out of 'normal' data, automation waits for a human while one is in progress, and new incidents are correlated with them. CI/CD can post to POST /api/changes.">
      {can('OPERATOR') && (
        <Panel title="Record a change (starts now)">
          <div className="grid grid-cols-1 md:grid-cols-5 gap-3 items-end">
            <Field label="Kind"><select className={`${inputClass} w-full`} value={kind} onChange={(e) => setKind(e.target.value)}>
              {['DEPLOYMENT', 'RESTART', 'CONFIG', 'MAINTENANCE', 'OTHER'].map((k) => <option key={k}>{k}</option>)}</select></Field>
            <Field label="Target"><input className={`${inputClass} w-full`} value={target} onChange={(e) => setTarget(e.target.value)} placeholder="service or system" /></Field>
            <Field label="Description"><input className={`${inputClass} w-full`} value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
            <Field label="Expected duration (min)"><input type="number" className={`${inputClass} w-full`} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} /></Field>
            <Button variant="primary" disabled={!description} onClick={record}>Record</Button>
          </div>
        </Panel>
      )}
      <ErrorBox error={err} />
      <Panel dense>
        <Async state={changes} empty={(d) => (d.length ? null : <Empty title="No changes recorded in the last 7 days" />)}>
          {(list) => (
            <Table head={['Started', 'Ended', 'Kind', 'Target', 'Source', 'Description']}>
              {list.map((c) => (
                <tr key={c.id}><Td mono>{fmtDateTime(c.startedAt)}</Td><Td mono>{c.endedAt ? fmtDateTime(c.endedAt) : 'open'}</Td>
                  <Td><Badge tone="info">{c.kind}</Badge></Td><Td mono>{c.target ?? '—'}</Td><Td>{c.source}</Td>
                  <Td className="text-[11.5px] text-[#5E6561]">{c.description}</Td></tr>
              ))}
            </Table>
          )}
        </Async>
      </Panel>
    </Page>
  );
};

