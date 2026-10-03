import React, { useEffect, useState } from 'react';
import { api, type EnvironmentConfig, type EnvironmentValidation } from '../api/causalops';
import { useEnv } from '../context/AppContext';
import { Badge, Button, ErrorBox, Field, KeyValue, Page, Panel, Table, Td, inputClass } from '../components/ui';

/** Defaults carry no endpoints (they are per environment); start from the bundled stack's addresses. */
const withEndpoints = (c: EnvironmentConfig): EnvironmentConfig => ({
  ...c,
  endpoints: c.endpoints ?? { prometheusUrl: 'http://prometheus:9090', tempoUrl: 'http://tempo:3200', lokiUrl: 'http://loki:3100',
    collectorHealthUrl: 'http://otel-collector:13133' },
});

const STEPS = ['Connect telemetry', 'Confirm topology', 'Service objectives', 'Remediation', 'Learning & create'];

/**
 * Onboards any system that sends OpenTelemetry to a collector with Prometheus behind it:
 * endpoints -> live validation and discovered topology -> SLOs -> executors and autonomy ->
 * learning window. Creates the environment in LEARNING; calibration follows automatically.
 */
export const Setup: React.FC<{ navigate: (p: string) => void }> = ({ navigate }) => {
  const { environments, refresh, select } = useEnv();
  const [step, setStep] = useState(0);
  const [name, setName] = useState('');
  const [cfg, setCfg] = useState<EnvironmentConfig | null>(null);
  const [editing, setEditing] = useState<string>('');           // existing environment id, or '' for a new one
  const [check, setCheck] = useState<EnvironmentValidation | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);

  useEffect(() => {
    api.environmentDefaults().then((d) => setCfg(withEndpoints(d))).catch((e) => setErr(e));
  }, []);

  const loadExisting = (id: string) => {
    setEditing(id);
    const env = environments.find((e) => e.id === id);
    if (env) { setName(env.name); setCfg(withEndpoints(env.config)); } else { setName(''); api.environmentDefaults().then((d) => setCfg(withEndpoints(d))); }
    setCheck(null);
    setStep(0);
  };

  if (!cfg) return <Page title="Setup wizard"><ErrorBox error={err} /></Page>;
  const set = (patch: Partial<EnvironmentConfig>) => setCfg({ ...cfg, ...patch });
  const rem = cfg.remediation;
  const setRem = (patch: Partial<typeof rem>) => set({ remediation: { ...rem, ...patch } });
  const setExecutor = (kind: string, patch: Record<string, unknown>) =>
    setRem({ executors: { ...rem.executors, [kind]: { ...(rem.executors[kind] ?? {}), ...patch } } });

  const validate = async () => {
    setBusy(true); setErr(null);
    try { setCheck(await api.validateEnvironment(cfg)); } catch (e) { setErr(e as Error); } finally { setBusy(false); }
  };
  const save = async () => {
    setBusy(true); setErr(null);
    try {
      const env = editing ? await api.updateEnvironmentConfig(editing, cfg) : await api.createEnvironment(name, cfg);
      await refresh();
      select(env.id);
      navigate('/calibration');
    } catch (e) { setErr(e as Error); } finally { setBusy(false); }
  };

  const nodes = Object.entries(check?.topology?.nodes ?? {});
  return (
    <Page title="Setup wizard" subtitle="Connect any system instrumented with OpenTelemetry. CausalOps discovers its topology, learns its normal behaviour (≤ 24 h, or sooner with labelled faults) and then detects, explains and remediates incidents. Models retrain on schedule."
          actions={<select className={inputClass} value={editing} onChange={(e) => loadExisting(e.target.value)}>
            <option value="">New environment</option>
            {environments.map((e) => <option key={e.id} value={e.id}>Edit {e.name}</option>)}
          </select>}>
      <div className="flex flex-wrap gap-1">
        {STEPS.map((s, i) => (
          <button key={s} onClick={() => setStep(i)} className={`px-2.5 h-7 rounded-[3px] text-[12px] border ${i === step ? 'bg-[#286B78] text-white border-[#286B78]' : 'bg-white border-[#D9DCD8] text-[#5E6561]'}`}>
            {i + 1}. {s}
          </button>
        ))}
      </div>
      <ErrorBox error={err} />

      {step === 0 && (
        <Panel title="Where the telemetry lives">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Field label="Environment name" hint="lowercase letters, digits and dashes">
              <input className={`${inputClass} w-full`} value={name} disabled={!!editing} onChange={(e) => setName(e.target.value)} placeholder="checkout-prod" />
            </Field>
            {(['prometheusUrl', 'tempoUrl', 'lokiUrl', 'collectorHealthUrl'] as const).map((k) => (
              <Field key={k} label={k.replace('Url', ' URL').replace('collectorHealth', 'OTel collector health')}>
                <input className={`${inputClass} w-full font-code`} value={cfg.endpoints[k] ?? ''}
                       onChange={(e) => set({ endpoints: { ...cfg.endpoints, [k]: e.target.value } })} />
              </Field>
            ))}
          </div>
          <div className="text-[11.5px] text-[#5E6561] mt-3">
            Metric templates default to OpenTelemetry span metrics and the service-graph connector (any language, any framework). Edit them in the
            environment's JSON config if your naming differs. See docs/INTEGRATION_GUIDE.md.
          </div>
          <div className="flex gap-2 mt-3"><Button variant="primary" disabled={busy} onClick={validate}>{busy ? 'Checking…' : 'Check connection'}</Button>
            <Button onClick={() => setStep(1)}>Next →</Button></div>
          {check && (
            <div className="mt-3 space-y-2">
              {check.configError && <ErrorBox error={new Error(check.configError)} />}
              <div className="flex flex-wrap gap-1.5">{Object.entries(check.endpoints ?? {}).map(([k, v]) => <Badge key={k} tone={v === 'UP' ? 'good' : v === 'NOT_CONFIGURED' ? 'muted' : 'bad'}>{k}: {v}</Badge>)}</div>
              <Table head={['Metric', 'Series', 'Nodes']}>
                {Object.entries(check.metrics ?? {}).map(([k, v]) => (
                  <tr key={k}><Td mono>{k}</Td><Td mono>{v.series}</Td><Td className="text-[11px] font-code">{v.error ?? v.nodes?.join(', ')}</Td></tr>
                ))}
              </Table>
              <Badge tone={check.valid ? 'good' : 'bad'}>{check.valid ? 'telemetry found' : 'no request-rate series yet — is traffic flowing through the collector?'}</Badge>
            </div>
          )}
        </Panel>
      )}

      {step === 1 && (
        <Panel title="Discovered topology (from the service graph)">
          {!check ? <div className="text-[12px] text-[#5E6561]">Run “Check connection” in step 1 first.</div> : check.topology?.error
            ? <ErrorBox error={new Error(check.topology.error)} /> : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <Table head={['Node', 'Kind']}>{nodes.map(([n, k]) => <tr key={n}><Td mono>{n}</Td><Td>{k}</Td></tr>)}</Table>
              <Table head={['Caller', 'Callee', 'Calls/s']}>{(check.topology?.edges ?? []).map((e) => (
                <tr key={e.client + e.server}><Td mono>{e.client}</Td><Td mono>{e.server}</Td><Td mono>{e.callRate.toFixed(2)}</Td></tr>))}</Table>
            </div>
          )}
          <div className="text-[11.5px] text-[#5E6561] mt-3">Components that send no traces (queues, external APIs) can be added on the Topology API (POST /api/topology/nodes and /edges) after creation.</div>
          <div className="mt-3"><Field label="Pseudo-nodes to ignore (callers outside your system)">
            <input className={`${inputClass} w-full font-code`} value={cfg.externalNodes.join(', ')}
                   onChange={(e) => set({ externalNodes: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) })} /></Field></div>
          <div className="flex gap-2 mt-3"><Button onClick={() => setStep(0)}>← Back</Button><Button onClick={() => setStep(2)}>Next →</Button></div>
        </Panel>
      )}

      {step === 2 && (
        <Panel title="Service-level objectives">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 max-w-[720px]">
            <Field label="p99 latency (ms)"><input type="number" className={`${inputClass} w-full`} value={cfg.slo.latencyP99Ms} onChange={(e) => set({ slo: { ...cfg.slo, latencyP99Ms: Number(e.target.value) } })} /></Field>
            <Field label="Error rate (%)"><input type="number" className={`${inputClass} w-full`} value={cfg.slo.errorRatePct} onChange={(e) => set({ slo: { ...cfg.slo, errorRatePct: Number(e.target.value) } })} /></Field>
            <Field label="Critical at × SLO"><input type="number" className={`${inputClass} w-full`} value={cfg.slo.criticalMultiplier} onChange={(e) => set({ slo: { ...cfg.slo, criticalMultiplier: Number(e.target.value) } })} /></Field>
            <Field label="Breach samples before an incident"><input type="number" className={`${inputClass} w-full`} value={cfg.detection.breachSamples} onChange={(e) => set({ detection: { ...cfg.detection, breachSamples: Number(e.target.value) } })} /></Field>
            <Field label="Healthy samples to resolve"><input type="number" className={`${inputClass} w-full`} value={cfg.detection.recoverySamples} onChange={(e) => set({ detection: { ...cfg.detection, recoverySamples: Number(e.target.value) } })} /></Field>
          </div>
          <div className="text-[11.5px] text-[#5E6561] mt-2">Per-service objectives can be set in the config (serviceSlos). The anomaly gate also detects deviations from learned baselines before an SLO is crossed.</div>
          <div className="flex gap-2 mt-3"><Button onClick={() => setStep(1)}>← Back</Button><Button onClick={() => setStep(3)}>Next →</Button></div>
        </Panel>
      )}

      {step === 3 && (
        <Panel title="Remediation executors and autonomy">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
            <div className="space-y-2 border border-[#E6E8E4] rounded-[3px] p-3">
              <label className="flex items-center gap-2 text-[12px] font-semibold"><input type="checkbox" checked={!!rem.executors.docker?.enabled} onChange={(e) => setExecutor('docker', { enabled: e.target.checked })} /> Docker</label>
              <Field label="Compose project" hint="only containers with this com.docker.compose.project label are touched">
                <input className={`${inputClass} w-full font-code`} value={String(rem.executors.docker?.project ?? '')} onChange={(e) => setExecutor('docker', { project: e.target.value })} /></Field>
            </div>
            <div className="space-y-2 border border-[#E6E8E4] rounded-[3px] p-3">
              <label className="flex items-center gap-2 text-[12px] font-semibold"><input type="checkbox" checked={!!rem.executors.kubernetes?.enabled} onChange={(e) => setExecutor('kubernetes', { enabled: e.target.checked })} /> Kubernetes</label>
              <Field label="Namespace"><input className={`${inputClass} w-full font-code`} value={String(rem.executors.kubernetes?.namespace ?? '')} onChange={(e) => setExecutor('kubernetes', { namespace: e.target.value })} /></Field>
              <Field label="API URL" hint="empty = in-cluster ServiceAccount"><input className={`${inputClass} w-full font-code`} value={String(rem.executors.kubernetes?.apiUrl ?? '')} onChange={(e) => setExecutor('kubernetes', { apiUrl: e.target.value || null })} /></Field>
            </div>
            <div className="space-y-2 border border-[#E6E8E4] rounded-[3px] p-3">
              <label className="flex items-center gap-2 text-[12px] font-semibold"><input type="checkbox" checked={!!rem.executors.webhook?.enabled} onChange={(e) => setExecutor('webhook', { enabled: e.target.checked })} /> Webhook runbooks</label>
              <Field label="Runbook URL"><input className={`${inputClass} w-full font-code`} value={String(rem.executors.webhook?.url ?? '')} onChange={(e) => setExecutor('webhook', { url: e.target.value })} /></Field>
              <Field label="Secret env var on the AI engine" hint="HMAC-SHA256 signature in X-CausalOps-Signature"><input className={`${inputClass} w-full font-code`} value={String(rem.executors.webhook?.secretEnv ?? '')} onChange={(e) => setExecutor('webhook', { secretEnv: e.target.value })} /></Field>
            </div>
          </div>
          <div className="mt-3 grid grid-cols-1 md:grid-cols-3 gap-3 max-w-[720px]">
            <Field label="Run automatically up to tier" hint="0 = always ask; 1 = restarts/scale-out; 2 = resource changes; 3 = runbooks">
              <select className={`${inputClass} w-full`} value={rem.autoExecuteMaxTier} onChange={(e) => setRem({ autoExecuteMaxTier: Number(e.target.value) })}>
                {[0, 1, 2, 3].map((t) => <option key={t} value={t}>{t}</option>)}</select></Field>
            <Field label="Dry run"><select className={`${inputClass} w-full`} value={String(rem.dryRun)} onChange={(e) => setRem({ dryRun: e.target.value === 'true' })}>
              <option value="false">off — act</option><option value="true">on — record only</option></select></Field>
            <Field label="Max automatic actions per hour"><input type="number" className={`${inputClass} w-full`} value={rem.maxAutoActionsPerHour} onChange={(e) => setRem({ maxAutoActionsPerHour: Number(e.target.value) })} /></Field>
          </div>
          <div className="text-[11.5px] text-[#5E6561] mt-2">Node names map to executor targets one to one by default; override in config.remediation.targets (e.g. a deployment with a different name).</div>
          <div className="flex gap-2 mt-3"><Button onClick={() => setStep(2)}>← Back</Button><Button onClick={() => setStep(4)}>Next →</Button></div>
        </Panel>
      )}

      {step === 4 && (
        <Panel title="Learning and retraining">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 max-w-[720px]">
            <Field label="Learning window (hours)" hint="calibrates automatically when it completes"><input type="number" className={`${inputClass} w-full`} value={cfg.calibration.learningWindowHours} onChange={(e) => set({ calibration: { ...cfg.calibration, learningWindowHours: Number(e.target.value) } })} /></Field>
            <Field label="Earliest manual calibration (minutes)"><input type="number" className={`${inputClass} w-full`} value={cfg.calibration.minLearningMinutes} onChange={(e) => set({ calibration: { ...cfg.calibration, minLearningMinutes: Number(e.target.value) } })} /></Field>
            <Field label="Retrain every (days)"><input type="number" className={`${inputClass} w-full`} value={cfg.calibration.retrainIntervalDays} onChange={(e) => set({ calibration: { ...cfg.calibration, retrainIntervalDays: Number(e.target.value) } })} /></Field>
          </div>
          <div className="mt-3"><KeyValue items={[['Name', editing ? environments.find((e) => e.id === editing)?.name ?? '' : name || '—'], ['Prometheus', cfg.endpoints.prometheusUrl],
            ['SLO', `p99 ${cfg.slo.latencyP99Ms} ms, errors ${cfg.slo.errorRatePct}%`],
            ['Executors', Object.entries(rem.executors).filter(([, v]) => v?.enabled).map(([k]) => k).join(', ') || 'none (recommendations only)'],
            ['Autonomy', `automatic up to tier ${rem.autoExecuteMaxTier}${rem.dryRun ? ', dry run' : ''}`]]} /></div>
          <div className="text-[11.5px] text-[#5E6561] mt-2">After creation the environment is LEARNING: telemetry and topology are collected immediately; models are fitted when the window completes
            (or earlier from the Calibration page once enough data exists). Injecting labelled faults in staging validates RCA before ACTIVE.</div>
          <div className="flex gap-2 mt-3"><Button onClick={() => setStep(3)}>← Back</Button>
            <Button variant="primary" disabled={busy || (!editing && !name)} onClick={save}>{editing ? 'Save changes' : 'Create environment'}</Button></div>
        </Panel>
      )}
    </Page>
  );
};
