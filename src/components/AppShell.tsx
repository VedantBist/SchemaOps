import React, { useState } from 'react';
import { api } from '../api/causalops';
import { useApi } from '../hooks/useApi';
import { useEvents } from '../hooks/useEvents';
import { useAuth, useEnv } from '../context/AppContext';
import { Badge, Dot, toneOf } from './ui';

export const NAV: { section: string; items: { page: string; label: string; role?: 'OPERATOR' | 'ADMIN' }[] }[] = [
  { section: 'Command', items: [{ page: 'overview', label: 'Overview' }, { page: 'topology', label: 'Topology' }, { page: 'services', label: 'Services' }] },
  { section: 'Log pipeline', items: [{ page: 'log-sources', label: 'Log sources' }, { page: 'log-events', label: 'Event explorer' },
    { page: 'log-entities', label: 'Entities' }, { page: 'log-studio', label: 'Parser studio' }, { page: 'log-packs', label: 'Parser packs' }] },
  { section: 'Incidents', items: [{ page: 'incidents', label: 'Active' }, { page: 'history', label: 'History' }] },
  { section: 'Remediation', items: [{ page: 'remediation', label: 'Remediation center' }] },
  { section: 'Intelligence', items: [{ page: 'predictions', label: 'Predictions' }, { page: 'simulation', label: 'Simulation' }, { page: 'calibration', label: 'Calibration' }] },
  { section: 'Observability', items: [{ page: 'metrics', label: 'Metrics' }, { page: 'logs', label: 'Logs' }, { page: 'traces', label: 'Traces' }] },
  { section: 'Operations', items: [{ page: 'faults', label: 'Fault lab', role: 'OPERATOR' }, { page: 'changes', label: 'Changes' },
    { page: 'setup', label: 'Setup wizard', role: 'ADMIN' }, { page: 'users', label: 'Users', role: 'ADMIN' }] },
];

export const AppShell: React.FC<{ page: string; navigate: (p: string) => void; title: string; children: React.ReactNode }> = ({
  page, navigate, title, children,
}) => {
  const { user, logout, can, authDisabled } = useAuth();
  const { environments, env, envId, select } = useEnv();
  const [live, setLive] = useState<string | null>(null);
  const status = useApi(() => api.systemStatus(envId), [envId], 15000);
  const active = useApi(() => api.incidents(envId, 'active', 20), [envId], 10000);
  const pending = useApi(() => api.recommendations(envId, undefined, 'AWAITING_APPROVAL'), [envId], 10000);

  useEvents(['telemetry.ingested', 'incident.created', 'incident.updated', 'incident.resolved', 'remediation.approval_required',
    'remediation.verified', 'remediation.escalated'], (type) => {
    setLive(new Date().toISOString());
    if (type.startsWith('incident')) active.reload();
    if (type.startsWith('remediation')) { pending.reload(); active.reload(); }
  });

  const incidents = active.data ?? [];
  const approvals = pending.data?.length ?? 0;
  const components = status.data?.components ?? {};
  const overall = status.error ? 'DOWN' : status.data?.status ?? '…';
  const worst = incidents[0];

  return (
    <div className="min-h-screen bg-[#F7F7F5] text-[#171A19] flex font-sans">
      <aside className="fixed left-0 top-0 bottom-0 w-[224px] bg-[#F7F7F5] border-r border-[#D9DCD8] z-50 flex flex-col">
        <div className="h-11 px-4 border-b border-[#D9DCD8] flex flex-col justify-center cursor-pointer" onClick={() => navigate('/overview')}>
          <span className="font-bold text-[14px] uppercase tracking-wider">CausalOps</span>
          <span className="font-code text-[9.5px] text-[#858C87] uppercase tracking-widest leading-none mt-0.5">Causal AIOps</span>
        </div>
        <nav className="flex-1 overflow-y-auto px-2 py-3 space-y-3">
          {NAV.map((s) => (
            <div key={s.section} className="space-y-0.5">
              <div className="px-2 py-1 font-section text-[10px] text-[#858C87] font-semibold">{s.section}</div>
              {s.items.filter((i) => (!i.role || can(i.role)) && !(authDisabled && i.page === 'users')).map((i) => (
                <button key={i.page} onClick={() => navigate(`/${i.page}`)}
                        className={`w-full flex items-center justify-between h-6 px-2 rounded-[3px] text-[12px] text-left transition-colors ${
                          page === i.page ? 'bg-[#E9EDE9] text-[#286B78] font-semibold border-l-2 border-[#286B78]'
                            : 'text-[#5E6561] hover:bg-[#EAECE8] hover:text-[#171A19]'}`}>
                  <span>{i.label}</span>
                  {i.page === 'incidents' && incidents.length > 0 && (
                    <span className="h-3.5 min-w-[14px] px-1 bg-[#B83A3A] text-white font-code text-[9px] leading-[14px] text-center rounded-[2px] font-semibold">{incidents.length}</span>
                  )}
                  {i.page === 'remediation' && approvals > 0 && (
                    <span className="h-3.5 min-w-[14px] px-1 bg-[#B7791F] text-white font-code text-[9px] leading-[14px] text-center rounded-[2px] font-semibold" title="awaiting approval">{approvals}</span>
                  )}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="p-2.5 border-t border-[#D9DCD8] bg-[#F1F2F0] space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="font-code text-[9.5px] text-[#858C87] uppercase font-bold tracking-wider">Platform health</span>
            <Badge tone={toneOf(overall)}>{overall}</Badge>
          </div>
          <div className="grid grid-cols-2 gap-1 text-[9.5px] font-code">
            {Object.entries(components).map(([k, v]) => (
              <div key={k} className="flex items-center justify-between bg-white px-1.5 py-0.5 rounded border border-[#E0E2DF]" title={`${k}: ${v}`}>
                <span className="text-[#5E6561] truncate">{k.replace('telemetryIngestion', 'ingestion')}</span>
                <Dot tone={toneOf(v)} />
              </div>
            ))}
          </div>
          {status.data && (
            <div className="text-[9.5px] font-code text-[#858C87] text-center">
              {status.data.topology.nodes} nodes · {status.data.topology.edges} edges · sample {status.data.ingestion.ageSeconds?.toFixed(0) ?? '—'}s old
            </div>
          )}
        </div>
      </aside>

      <div className="pl-[224px] flex-1 flex flex-col min-w-0">
        <header className="fixed top-0 left-[224px] right-0 h-11 bg-white border-b border-[#D9DCD8] z-40 flex items-center justify-between px-4 gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <span className="font-code text-[11px] uppercase tracking-wider text-[#858C87] font-semibold">CausalOps</span>
            <span className="text-[#D9DCD8]">/</span>
            <span className="font-semibold text-[13px] truncate">{title}</span>
          </div>
          <div className="flex items-center gap-3">
            {environments.length > 0 && (
              <div className="flex items-center gap-1.5">
                <select value={envId} onChange={(e) => select(e.target.value)}
                        className="h-6 px-1 border border-[#D9DCD8] rounded-[3px] text-[11.5px] bg-white font-code">
                  {environments.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
                </select>
                {env && <Badge title={env.statusReason ?? ''}>{env.status}</Badge>}
              </div>
            )}
            <span className="flex items-center gap-1 font-code text-[9.5px] text-[#2F7D5C] font-semibold uppercase" title={live ? `last event ${live}` : 'waiting for events'}>
              <Dot tone={live ? 'good' : 'muted'} pulse={!!live} /> live
            </span>
            {worst ? (
              <button onClick={() => navigate(`/incidents/${worst.id}`)}
                      className="flex items-center gap-1.5 px-2 py-0.5 rounded-[2px] bg-[#B83A3A]/10 border border-[#B83A3A]/30 text-[#B83A3A] font-code text-[10px] font-semibold">
                <Dot tone="bad" pulse /> {worst.incidentKey} · {worst.severity} · {worst.status}
              </button>
            ) : (
              <span className="font-code text-[10px] text-[#2F7D5C]">No open incidents</span>
            )}
            {user && (
              <div className="flex items-center gap-2 pl-3 border-l border-[#D9DCD8]">
                <span className="text-[11.5px]">{user.username}</span>
                <Badge tone="info">{user.role}</Badge>
                {authDisabled ? <span className="text-[10.5px] text-[#9A6412]" title="Sign-in and roles arrive with Phase 6">auth not enabled</span>
                  : <button onClick={logout} className="text-[11px] text-[#286B78] hover:underline">Sign out</button>}
              </div>
            )}
          </div>
        </header>
        <main className="pt-11 flex-1 flex flex-col">{children}</main>
      </div>
    </div>
  );
};
