import React, { useState, useEffect } from 'react';
import { useDemoState } from '../../context/DemoStateContext';
import { causalOpsApi } from '../../api/client';

export type AppPage =
  | 'overview'
  | 'topology'
  | 'services'
  | 'active-incidents'
  | 'incident-history'
  | 'root-cause'
  | 'predictions'
  | 'simulation'
  | 'metrics'
  | 'logs'
  | 'traces';

interface AppShellProps {
  currentPage: AppPage;
  onNavigate: (page: AppPage) => void;
  children: React.ReactNode;
}

const PAGE_TITLES: Record<AppPage, string> = {
  overview: 'Command Center',
  topology: 'Service Topology',
  services: 'Services Fleet',
  'active-incidents': 'Active Incidents',
  'incident-history': 'Incident History',
  'root-cause': 'Root Cause Analysis — INC-8941',
  predictions: 'Failure Predictions',
  simulation: 'Counterfactual Simulation',
  metrics: 'Observability Metrics',
  logs: 'Logs Explorer',
  traces: 'Distributed Traces',
};

export const AppShell: React.FC<AppShellProps> = ({ currentPage, onNavigate, children }) => {
  const { activeFault } = useDemoState();
  const isAuthFail = activeFault === 'auth-gateway';

  const [healthStatus, setHealthStatus] = useState({
    api: 'HEALTHY' as 'HEALTHY' | 'DEGRADED' | 'DOWN',
    aiEngine: 'HEALTHY' as 'HEALTHY' | 'DEGRADED' | 'DOWN',
    database: 'HEALTHY' as 'HEALTHY' | 'DEGRADED' | 'DOWN',
    telemetry: 'HEALTHY' as 'HEALTHY' | 'DEGRADED' | 'DOWN',
  });

  useEffect(() => {
    let mounted = true;
    const fetchHealth = async () => {
      try {
        const ai = await causalOpsApi.aiHealth();
        if (mounted && ai) {
          setHealthStatus((prev) => ({
            ...prev,
            aiEngine: ai.status === 'UP' ? 'HEALTHY' : 'DEGRADED',
          }));
        }
      } catch {
        if (mounted) {
          setHealthStatus((prev) => ({ ...prev, aiEngine: 'DEGRADED' }));
        }
      }
      try {
        const sys = await causalOpsApi.systemHealth();
        if (mounted && sys) {
          setHealthStatus((prev) => ({
            ...prev,
            api: 'HEALTHY',
            database: 'HEALTHY',
            telemetry: (sys.components?.telemetry as any) ?? 'HEALTHY',
          }));
        }
      } catch {
        // Fallback default remains healthy in mock/demo mode
      }
    };

    fetchHealth();
    const timer = setInterval(fetchHealth, 15000);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="min-h-screen bg-[#F7F7F5] text-[#171A19] flex select-none font-sans">
      {/* PERSISTENT 224px LEFT NAVIGATION RAIL */}
      <aside className="fixed left-0 top-0 bottom-0 w-[224px] bg-[#F7F7F5] border-r border-[#D9DCD8] z-50 flex flex-col justify-between">
        <div className="flex flex-col flex-1 overflow-y-auto">
          {/* Logo Header */}
          <div
            className="h-11 px-4 border-b border-[#D9DCD8] flex flex-col justify-center cursor-pointer hover:bg-[#F1F2F0] transition-colors"
            onClick={() => onNavigate('overview')}
          >
            <div className="flex items-baseline gap-1.5">
              <span className="font-bold text-[14px] uppercase tracking-wider text-[#171A19]">CausalOps</span>
              <span className="font-code text-[11px] text-[#00535f] font-medium tracking-tight">v2.4</span>
            </div>
            <div className="font-code text-[9.5px] text-[#858C87] uppercase tracking-widest leading-none mt-0.5">
              AI Operations
            </div>
          </div>

          {/* Navigation Sections */}
          <nav className="px-2 py-3 space-y-3">
            {/* COMMAND */}
            <div className="space-y-0.5">
              <div className="px-2 py-1 font-section text-[10px] text-[#858C87] font-semibold">Command</div>
              <div className="space-y-0.5">
                <button
                  onClick={() => onNavigate('overview')}
                  className={`w-full flex items-center h-6 px-2 rounded-[3px] text-[12px] transition-colors text-left ${
                    currentPage === 'overview'
                      ? 'bg-[#E9EDE9] text-[#286B78] font-semibold border-l-2 border-[#286B78]'
                      : 'text-[#5E6561] hover:bg-[#EAECE8] hover:text-[#171A19]'
                  }`}
                >
                  Overview
                </button>
                <button
                  onClick={() => onNavigate('topology')}
                  className={`w-full flex items-center h-6 px-2 rounded-[3px] text-[12px] transition-colors text-left ${
                    currentPage === 'topology'
                      ? 'bg-[#E9EDE9] text-[#286B78] font-semibold border-l-2 border-[#286B78]'
                      : 'text-[#5E6561] hover:bg-[#EAECE8] hover:text-[#171A19]'
                  }`}
                >
                  Topology
                </button>
                <button
                  onClick={() => onNavigate('services')}
                  className={`w-full flex items-center h-6 px-2 rounded-[3px] text-[12px] transition-colors text-left ${
                    currentPage === 'services'
                      ? 'bg-[#E9EDE9] text-[#286B78] font-semibold border-l-2 border-[#286B78]'
                      : 'text-[#5E6561] hover:bg-[#EAECE8] hover:text-[#171A19]'
                  }`}
                >
                  Services
                </button>
              </div>
            </div>

            {/* INCIDENTS */}
            <div className="space-y-0.5">
              <div className="px-2 py-1 font-section text-[10px] text-[#858C87] font-semibold">Incidents</div>
              <div className="space-y-0.5">
                <button
                  onClick={() => onNavigate('active-incidents')}
                  className={`w-full flex items-center justify-between h-6 px-2 rounded-[3px] text-[12px] transition-colors text-left ${
                    currentPage === 'active-incidents'
                      ? 'bg-[#E9EDE9] text-[#286B78] font-semibold border-l-2 border-[#286B78]'
                      : 'text-[#5E6561] hover:bg-[#EAECE8] hover:text-[#171A19]'
                  }`}
                >
                  <span>Active</span>
                  <span className="h-3.5 min-w-[14px] px-1 bg-[#B83A3A] text-white font-code text-[9px] leading-[14px] text-center rounded-[2px] font-semibold">
                    3
                  </span>
                </button>
                <button
                  onClick={() => onNavigate('incident-history')}
                  className={`w-full flex items-center justify-between h-6 px-2 rounded-[3px] text-[12px] transition-colors text-left ${
                    currentPage === 'incident-history'
                      ? 'bg-[#E9EDE9] text-[#286B78] font-semibold border-l-2 border-[#286B78]'
                      : 'text-[#5E6561] hover:bg-[#EAECE8] hover:text-[#171A19]'
                  }`}
                >
                  <span>History</span>
                  <span className="font-code text-[9.5px] text-[#858C87]">247</span>
                </button>
              </div>
            </div>

            {/* INTELLIGENCE */}
            <div className="space-y-0.5">
              <div className="px-2 py-1 font-section text-[10px] text-[#858C87] font-semibold">Intelligence</div>
              <div className="space-y-0.5">
                <button
                  onClick={() => onNavigate('root-cause')}
                  className={`w-full flex items-center h-6 px-2 rounded-[3px] text-[12px] transition-colors text-left ${
                    currentPage === 'root-cause'
                      ? 'bg-[#E9EDE9] text-[#286B78] font-semibold border-l-2 border-[#286B78]'
                      : 'text-[#5E6561] hover:bg-[#EAECE8] hover:text-[#171A19]'
                  }`}
                >
                  Root Cause
                </button>
                <button
                  onClick={() => onNavigate('predictions')}
                  className={`w-full flex items-center h-6 px-2 rounded-[3px] text-[12px] transition-colors text-left ${
                    currentPage === 'predictions'
                      ? 'bg-[#E9EDE9] text-[#286B78] font-semibold border-l-2 border-[#286B78]'
                      : 'text-[#5E6561] hover:bg-[#EAECE8] hover:text-[#171A19]'
                  }`}
                >
                  Predictions
                </button>
                <button
                  onClick={() => onNavigate('simulation')}
                  className={`w-full flex items-center h-6 px-2 rounded-[3px] text-[12px] transition-colors text-left ${
                    currentPage === 'simulation'
                      ? 'bg-[#E9EDE9] text-[#286B78] font-semibold border-l-2 border-[#286B78]'
                      : 'text-[#5E6561] hover:bg-[#EAECE8] hover:text-[#171A19]'
                  }`}
                >
                  Simulation
                </button>
              </div>
            </div>

            {/* OBSERVABILITY */}
            <div className="space-y-0.5">
              <div className="px-2 py-1 font-section text-[10px] text-[#858C87] font-semibold">Observability</div>
              <div className="space-y-0.5">
                <button
                  onClick={() => onNavigate('metrics')}
                  className={`w-full flex items-center h-6 px-2 rounded-[3px] text-[12px] transition-colors text-left ${
                    currentPage === 'metrics'
                      ? 'bg-[#E9EDE9] text-[#286B78] font-semibold border-l-2 border-[#286B78]'
                      : 'text-[#5E6561] hover:bg-[#EAECE8] hover:text-[#171A19]'
                  }`}
                >
                  Metrics
                </button>
                <button
                  onClick={() => onNavigate('logs')}
                  className={`w-full flex items-center h-6 px-2 rounded-[3px] text-[12px] transition-colors text-left ${
                    currentPage === 'logs'
                      ? 'bg-[#E9EDE9] text-[#286B78] font-semibold border-l-2 border-[#286B78]'
                      : 'text-[#5E6561] hover:bg-[#EAECE8] hover:text-[#171A19]'
                  }`}
                >
                  Logs
                </button>
                <button
                  onClick={() => onNavigate('traces')}
                  className={`w-full flex items-center h-6 px-2 rounded-[3px] text-[12px] transition-colors text-left ${
                    currentPage === 'traces'
                      ? 'bg-[#E9EDE9] text-[#286B78] font-semibold border-l-2 border-[#286B78]'
                      : 'text-[#5E6561] hover:bg-[#EAECE8] hover:text-[#171A19]'
                  }`}
                >
                  Traces
                </button>
              </div>
            </div>
          </nav>
        </div>

        {/* System Health Area (Phase 7 Productionization) */}
        <div className="p-2.5 border-t border-[#D9DCD8] bg-[#F1F2F0] space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-code text-[9.5px] text-[#858C87] uppercase font-bold tracking-wider">System Health</span>
            <span className={`flex items-center gap-1 font-code text-[9.5px] font-semibold ${isAuthFail ? 'text-[#B83A3A]' : 'text-[#2F7D5C]'}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${isAuthFail ? 'bg-[#B83A3A] animate-pulse' : 'bg-[#2F7D5C]'}`}></span>
              <span>{isAuthFail ? 'DEGRADED' : 'OPERATIONAL'}</span>
            </span>
          </div>

          <div className="grid grid-cols-2 gap-1 text-[9.5px] font-code">
            <div className="flex items-center justify-between bg-white px-1.5 py-0.5 rounded border border-[#E0E2DF]">
              <span className="text-[#5E6561]">API</span>
              <span className="flex items-center gap-1 text-[#2F7D5C] font-semibold">
                <span className="w-1 h-1 rounded-full bg-[#2F7D5C]" />
                {healthStatus.api}
              </span>
            </div>
            <div className="flex items-center justify-between bg-white px-1.5 py-0.5 rounded border border-[#E0E2DF]">
              <span className="text-[#5E6561]">AI</span>
              <span className={`flex items-center gap-1 font-semibold ${healthStatus.aiEngine === 'HEALTHY' ? 'text-[#2F7D5C]' : 'text-[#B83A3A]'}`}>
                <span className={`w-1 h-1 rounded-full ${healthStatus.aiEngine === 'HEALTHY' ? 'bg-[#2F7D5C]' : 'bg-[#B83A3A]'}`} />
                {healthStatus.aiEngine}
              </span>
            </div>
            <div className="flex items-center justify-between bg-white px-1.5 py-0.5 rounded border border-[#E0E2DF]">
              <span className="text-[#5E6561]">DB</span>
              <span className="flex items-center gap-1 text-[#2F7D5C] font-semibold">
                <span className="w-1 h-1 rounded-full bg-[#2F7D5C]" />
                {healthStatus.database}
              </span>
            </div>
            <div className="flex items-center justify-between bg-white px-1.5 py-0.5 rounded border border-[#E0E2DF]">
              <span className="text-[#5E6561]">Telemetry</span>
              <span className="flex items-center gap-1 text-[#2F7D5C] font-semibold">
                <span className="w-1 h-1 rounded-full bg-[#2F7D5C]" />
                {healthStatus.telemetry}
              </span>
            </div>
          </div>

          <div className="text-[9px] font-code text-[#858C87] text-center">
            47 services · 183 deps · 5 nodes
          </div>
        </div>
      </aside>

      {/* MAIN VIEWPORT CONTAINER */}
      <div className="pl-[224px] flex-1 flex flex-col min-w-0">
        {/* PERSISTENT TOP HEADER BAR */}
        <header className="fixed top-0 left-[224px] right-0 h-11 bg-[#FFFFFF] border-b border-[#D9DCD8] z-40 flex items-center justify-between px-4 select-none">
          <div className="flex items-center gap-2">
            <span className="font-code text-[11px] uppercase tracking-wider text-[#858C87] font-semibold">CAUSALOPS</span>
            <span className="text-[#D9DCD8]">/</span>
            <span className="font-semibold text-[13px] text-[#171A19]">{PAGE_TITLES[currentPage]}</span>
          </div>

          <div className="flex items-center gap-3">
            {/* Demo Status Indicator */}
            {isAuthFail ? (
              <div
                className="hidden xl:flex items-center gap-1.5 px-2 py-0.5 rounded-[2px] bg-[#B83A3A]/10 border border-[#B83A3A]/40 text-[#B83A3A] font-code text-[10px] font-bold tracking-tight"
                title="Active Fault: Run ./demo-restore.sh to heal system"
              >
                <span className="w-1.5 h-1.5 rounded-full bg-[#B83A3A] animate-ping" />
                <span>FAULT: auth-gateway · (restore: ./demo-restore.sh)</span>
              </div>
            ) : (
              <div
                className="hidden xl:flex items-center gap-1.5 px-2 py-0.5 rounded-[2px] bg-[#286B78]/10 border border-[#286B78]/30 text-[#286B78] font-code text-[10px] font-medium"
                title="Baseline Healthy: Run ./demo-fail-auth.sh in terminal to inject failure"
              >
                <span className="w-1.5 h-1.5 rounded-full bg-[#286B78]" />
                <span>DEMO READY · (inject: ./demo-fail-auth.sh)</span>
              </div>
            )}

            {/* System Status */}
            <div className="hidden md:flex items-center gap-2.5 pl-3 border-l border-[#D9DCD8]">
              <div className="flex items-center gap-1.5 font-code text-[11px] text-[#5E6561]">
                <span className={`w-1.5 h-1.5 rounded-full ${isAuthFail ? 'bg-[#B83A3A] animate-pulse' : 'bg-[#2F7D5C]'}`}></span>
                <span className="text-[#171A19] font-medium">Status:</span>
                <span className={isAuthFail ? 'text-[#B83A3A] font-semibold' : 'text-[#2F7D5C] font-semibold'}>
                  {isAuthFail ? 'Degraded (INC-8945)' : 'Operational'}
                </span>
              </div>
              <span className="text-[#D9DCD8]">·</span>
              <span className="font-code text-[11px] text-[#858C87]">47 services | 183 dependencies</span>
            </div>

            {/* LIVE Badge */}
            <div className="flex items-center gap-1 px-1.5 py-0.5 rounded-[2px] bg-[#2F7D5C]/10 border border-[#2F7D5C]/30 text-[#2F7D5C] font-code text-[9.5px] font-semibold tracking-wider uppercase">
              <span className="relative flex h-1.5 w-1.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#2F7D5C] opacity-75"></span>
                <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-[#2F7D5C]"></span>
              </span>
              <span>LIVE</span>
            </div>

            {/* Active Incident Pill Alert (Clickable) */}
            <button
              onClick={() => onNavigate('active-incidents')}
              className="flex items-center gap-1.5 px-2 py-0.5 rounded-[2px] bg-[#B83A3A]/10 border border-[#B83A3A]/30 text-[#B83A3A] font-code text-[10px] font-semibold tracking-wide hover:bg-[#B83A3A]/20 transition-colors cursor-pointer"
              title={isAuthFail ? "Jump to Active Incident INC-8945 (auth-gateway)" : "Jump to Active Incident INC-8941"}
            >
              <span className="w-1.5 h-1.5 rounded-full bg-[#B83A3A] animate-pulse"></span>
              <span>{isAuthFail ? 'INC-8945 (CRITICAL) · auth-gateway' : 'INC-8941 (CRITICAL)'}</span>
            </button>

            {/* User Avatar */}
            <div className="w-6 h-6 rounded-full bg-[#00535f] flex items-center justify-center text-white" title="SRE Operator">
              <span className="material-symbols-outlined text-white text-[14px]">person</span>
            </div>
          </div>
        </header>

        {/* WORKSPACE CONTENT AREA */}
        <main className="pt-11 flex-1 flex flex-col">{children}</main>
      </div>
    </div>
  );
};
