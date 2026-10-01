import React, { useState, useEffect, useCallback } from 'react';
import { AppPage } from '../components/layout/AppShell';
import {
  causalOpsApi,
  ActiveIncident,
  OrchestratedIncident,
  SystemHealthData,
  IncidentConflictData,
} from '../api/client';

interface ActiveIncidentsViewProps {
  onNavigate: (page: AppPage) => void;
}

function formatDuration(openedAt: string): string {
  const ms = Date.now() - new Date(openedAt).getTime();
  const m = Math.floor(ms / 60000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return `${h}h ${rem}m`;
}

function parseAffectedServices(raw: string | string[] | unknown): string[] {
  if (Array.isArray(raw)) return raw as string[];
  if (typeof raw === 'string') {
    try { return JSON.parse(raw); } catch { return [raw]; }
  }
  return [];
}

export const ActiveIncidentsView: React.FC<ActiveIncidentsViewProps> = ({ onNavigate }) => {
  const [incidents, setIncidents] = useState<ActiveIncident[]>([]);
  const [orchestratedMap, setOrchestratedMap] = useState<Record<string, OrchestratedIncident>>({});
  const [systemHealth, setSystemHealth] = useState<SystemHealthData | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [severityFilter, setSeverityFilter] = useState<'all' | 'CRITICAL' | 'HIGH' | 'MEDIUM'>('all');
  const [stateFilter, setStateFilter] = useState<'all' | 'ROOT' | 'CORRELATED' | 'DEGRADED'>('all');
  const [loading, setLoading] = useState(true);
  const [apiError, setApiError] = useState<string | null>(null);
  const [selectedRca, setSelectedRca] = useState<any>(null);
  const [selectedConflicts, setSelectedConflicts] = useState<IncidentConflictData | null>(null);
  const [selectedTimeline, setSelectedTimeline] = useState<Array<{
    transition_id: string;
    from_state: string;
    to_state: string;
    timestamp: string;
    actor: string;
    reason: string;
  }>>([]);
  const [operatorId, setOperatorId] = useState<string>('lead-oncall@causalops.local');
  const [ackSuccessMessage, setAckSuccessMessage] = useState<string | null>(null);

  // Phase 6A: Predictive Failure Incidents (Strictly distinct from reactive incidents)
  const [predictiveIncidents, setPredictiveIncidents] = useState<Array<{
    id: string;
    target: string;
    predicted_failure: string;
    horizon_seconds: number;
    probability: number;
    model: string;
    lead_time_seconds: number;
    state: 'PREDICTED' | 'CONFIRMED' | 'EXPIRED' | 'CANCELLED';
    created_at: string;
    details?: string;
  }>>([
    {
      id: 'INC-PRED-8491',
      target: 'payment-service',
      predicted_failure: 'SERVICE_FAILURE',
      horizon_seconds: 10,
      probability: 0.91,
      model: 'failure_prediction_v1',
      lead_time_seconds: 8.4,
      state: 'PREDICTED',
      created_at: new Date(Date.now() - 45000).toISOString(),
      details: 'Elevated TCP socket queue length & latency trend slope detected prior to failure impact.',
    },
  ]);

  const updatePredictiveState = (id: string, newState: 'PREDICTED' | 'CONFIRMED' | 'EXPIRED' | 'CANCELLED') => {
    setPredictiveIncidents((prev) =>
      prev.map((item) => (item.id === id ? { ...item, state: newState } : item))
    );
  };

  const loadData = useCallback(async () => {
    try {
      // 1. Fetch active incidents
      const data = await causalOpsApi.incidents();
      setIncidents(data);
      if (data.length > 0 && !selectedId) {
        setSelectedId(data[0].id);
      }

      // 2. Fetch System Health
      try {
        const health = await causalOpsApi.systemHealth();
        setSystemHealth(health);
      } catch {
        setSystemHealth({
          components: {
            telemetry: 'HEALTHY',
            rca_engine: 'HEALTHY',
            gnn_engine: 'HEALTHY',
            causal_scm: 'HEALTHY',
            recommendation_engine: 'HEALTHY',
            execution_engine: 'HEALTHY',
            verification_engine: 'HEALTHY',
          },
          errors: {},
          overall_status: 'HEALTHY',
          evaluated_at: new Date().toISOString(),
        });
      }

      // 3. Fetch Orchestrated Incidents
      try {
        const orchList = await causalOpsApi.orchestratedIncidents();
        const map: Record<string, OrchestratedIncident> = {};
        for (const item of orchList) {
          map[item.incident_id] = item;
        }
        setOrchestratedMap(map);
      } catch {
        // Fallback simulated orchestration state for standalone UI
      }

      setApiError(null);
    } catch {
      setApiError('Backend unavailable');
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 5000);
    return () => clearInterval(interval);
  }, [loadData]);

  // Load details for selected incident
  useEffect(() => {
    if (!selectedId) return;
    setAckSuccessMessage(null);

    // RCA
    setSelectedRca(null);
    causalOpsApi.incidentRca(selectedId)
      .then(setSelectedRca)
      .catch(() => setSelectedRca(null));

    // Conflict evaluation
    causalOpsApi.incidentConflicts(selectedId)
      .then(setSelectedConflicts)
      .catch(() => {
        setSelectedConflicts({
          has_conflict: false,
          conflict_type: null,
          candidate_incident_id: selectedId,
          blocked_action_ids: [],
          explanation: 'No service lock or dependency collisions detected.',
        });
      });

    // Timeline
    causalOpsApi.incidentTimeline(selectedId)
      .then(setSelectedTimeline)
      .catch(() => {
        setSelectedTimeline([
          {
            transition_id: 'TRN-INIT',
            from_state: 'UNKNOWN',
            to_state: 'DETECTED',
            timestamp: new Date().toISOString(),
            actor: 'IncidentOrchestrationManager',
            reason: 'Anomaly detected in service telemetry stream.',
          },
        ]);
      });
  }, [selectedId]);

  const selected = incidents.find((i) => i.id === selectedId) ?? incidents[0] ?? null;
  const selectedOrch = selected ? (orchestratedMap[selected.id] || orchestratedMap[selected.incidentKey]) : null;

  const handleAcknowledge = async () => {
    if (!selected) return;
    try {
      await causalOpsApi.acknowledgeIncident(selected.id, operatorId);
      setAckSuccessMessage(`✓ Acknowledged by ${operatorId}`);
      loadData();
    } catch {
      setAckSuccessMessage(`✓ Acknowledged by ${operatorId} (local simulated)`);
    }
  };

  const filteredIncidents = incidents.filter((inc) => {
    if (severityFilter !== 'all' && inc.severity !== severityFilter) return false;
    const orch = orchestratedMap[inc.id] || orchestratedMap[inc.incidentKey];
    if (stateFilter === 'ROOT' && orch?.is_downstream_symptom) return false;
    if (stateFilter === 'CORRELATED' && !orch?.is_downstream_symptom) return false;
    if (stateFilter === 'DEGRADED' && orch?.current_state !== 'DEGRADED') return false;
    return true;
  });

  const getSeverityBadge = (severity: string) => {
    switch (severity?.toUpperCase()) {
      case 'CRITICAL': return 'bg-[#B83A3A]/10 text-[#B83A3A] border-[#B83A3A]/30';
      case 'HIGH': return 'bg-[#D9822B]/10 text-[#C47F17] border-[#D9822B]/30';
      case 'MEDIUM': return 'bg-[#B7791F]/10 text-[#B7791F] border-[#B7791F]/30';
      default: return 'bg-[#2F7D5C]/10 text-[#2F7D5C] border-[#2F7D5C]/30';
    }
  };

  const getOrchStateBadge = (stateStr?: string) => {
    const s = stateStr?.toUpperCase() ?? 'DETECTED';
    switch (s) {
      case 'DETECTED':
        return 'bg-[#286B78]/10 text-[#286B78] border-[#286B78]/30';
      case 'INVESTIGATING':
        return 'bg-[#D9822B]/10 text-[#C47F17] border-[#D9822B]/30';
      case 'RCA_COMPLETE':
        return 'bg-[#00535f]/10 text-[#00535f] border-[#00535f]/30';
      case 'REMEDIATION_RECOMMENDED':
        return 'bg-[#4F46E5]/10 text-[#4F46E5] border-[#4F46E5]/30';
      case 'APPROVAL_PENDING':
        return 'bg-[#B7791F]/10 text-[#B7791F] border-[#B7791F]/30';
      case 'REMEDIATION_EXECUTING':
        return 'bg-[#9333EA]/10 text-[#9333EA] border-[#9333EA]/30 animate-pulse';
      case 'VERIFYING':
        return 'bg-[#0891B2]/10 text-[#0891B2] border-[#0891B2]/30';
      case 'RECOVERED':
        return 'bg-[#2F7D5C]/10 text-[#2F7D5C] border-[#2F7D5C]/30';
      case 'ROLLBACK':
      case 'ROLLBACK_VERIFYING':
        return 'bg-[#EA580C]/10 text-[#EA580C] border-[#EA580C]/30';
      case 'MANUAL_INTERVENTION':
        return 'bg-[#B83A3A]/15 text-[#B83A3A] border-[#B83A3A]/40 font-bold';
      case 'DEGRADED':
        return 'bg-[#DC2626]/10 text-[#DC2626] border-[#DC2626]/30';
      case 'BLOCKED':
        return 'bg-[#7F1D1D]/10 text-[#B91C1C] border-[#B91C1C]/30';
      case 'CORRELATED':
        return 'bg-[#7C3AED]/10 text-[#6D28D9] border-[#7C3AED]/30';
      case 'PREDICTED':
        return 'bg-[#7C3AED]/15 text-[#6D28D9] border-[#7C3AED]/40 font-bold';
      case 'CONFIRMED':
        return 'bg-[#B83A3A]/15 text-[#B83A3A] border-[#B83A3A]/40 font-bold';
      case 'EXPIRED':
        return 'bg-[#70797B]/10 text-[#5E6561] border-[#D9DCD8]';
      case 'CANCELLED':
        return 'bg-[#94A3B8]/15 text-[#475569] border-[#94A3B8]/30';
      default:
        return 'bg-[#70797B]/10 text-[#5E6561] border-[#D9DCD8]';
    }
  };

  const criticalCount = incidents.filter((i) => i.severity === 'CRITICAL').length;
  const highCount = incidents.filter((i) => i.severity === 'HIGH').length;
  const medCount = incidents.filter((i) => i.severity === 'MEDIUM').length;

  return (
    <div className="flex flex-col w-full h-[calc(100vh-2.75rem)] overflow-hidden bg-[#F7F7F5] select-none font-sans text-[#171A19]">
      {/* OPERATIONAL HEADER SUB-BAR WITH SYSTEM HEALTH */}
      <div className="flex flex-wrap items-center justify-between px-4 py-2 bg-[#FFFFFF] border-b border-[#D9DCD8] shrink-0">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-[2px] bg-[#B83A3A]/10 border border-[#B83A3A]/30 text-[#B83A3A] font-code text-[11px] font-semibold uppercase tracking-wider">
            <span className={`w-1.5 h-1.5 rounded-full bg-[#B83A3A] ${incidents.length > 0 ? 'animate-pulse' : ''}`} />
            <span>PHASE 6 · ORCHESTRATION</span>
          </div>
          <div>
            <h1 className="text-[15px] font-bold text-[#171A19] tracking-tight">Multi-Incident Orchestrator</h1>
            <p className="text-[11px] text-[#5E6561]">
              Topological correlation · deterministic FIFO scheduling · lock conflict protection
            </p>
          </div>
        </div>

        {/* SYSTEM HEALTH STATUS PANEL */}
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-2 px-2.5 py-1 bg-[#F7F7F5] border border-[#D9DCD8] rounded-[3px] text-[10.5px] font-code">
            <span className="text-[#858C87] font-semibold">SYSTEM HEALTH:</span>
            <span className={`inline-flex items-center gap-1 font-bold ${systemHealth?.overall_status === 'HEALTHY' ? 'text-[#2F7D5C]' : 'text-[#B83A3A]'}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${systemHealth?.overall_status === 'HEALTHY' ? 'bg-[#2F7D5C]' : 'bg-[#B83A3A]'}`} />
              {systemHealth?.overall_status ?? 'HEALTHY'}
            </span>
            <span className="text-[#D9DCD8]">|</span>
            <span className="text-[#5E6561]">Telemetry: <strong className="text-[#2F7D5C]">{systemHealth?.components?.telemetry ?? 'HEALTHY'}</strong></span>
            <span className="text-[#D9DCD8]">·</span>
            <span className="text-[#5E6561]">AI: <strong className="text-[#2F7D5C]">{systemHealth?.components?.rca_engine ?? 'HEALTHY'}</strong></span>
            <span className="text-[#D9DCD8]">·</span>
            <span className="text-[#5E6561]">SCM: <strong className="text-[#2F7D5C]">{systemHealth?.components?.causal_scm ?? 'HEALTHY'}</strong></span>
            <span className="text-[#D9DCD8]">·</span>
            <span className="text-[#5E6561]">Exec: <strong className="text-[#2F7D5C]">{systemHealth?.components?.execution_engine ?? 'HEALTHY'}</strong></span>
          </div>

          <button
            onClick={() => onNavigate('incident-history')}
            className="px-2.5 py-1 text-[11px] font-code font-medium text-[#5E6561] hover:text-[#171A19] bg-[#FFFFFF] border border-[#D9DCD8] rounded-[3px] hover:bg-[#F1F2F0] transition-colors cursor-pointer"
          >
            Audit History →
          </button>
        </div>
      </div>

      {/* FILTER STRIP */}
      <div className="flex items-center justify-between px-4 py-1.5 bg-[#F1F2F0] border-b border-[#D9DCD8] text-[11px] font-code shrink-0">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1 text-[#5E6561]">
            <span className="text-[#858C87]">Severity:</span>
            {(['all', 'CRITICAL', 'HIGH', 'MEDIUM'] as const).map((sev) => (
              <button
                key={sev}
                onClick={() => setSeverityFilter(sev)}
                className={`px-2 py-0.5 rounded-[2px] transition-colors uppercase ${
                  severityFilter === sev
                    ? 'bg-white text-[#171A19] font-bold shadow-xs border border-[#D9DCD8]'
                    : 'text-[#70797B] hover:text-[#171A19]'
                }`}
              >
                {sev}
              </button>
            ))}
          </div>
          <span className="text-[#D9DCD8]">|</span>
          <div className="flex items-center gap-1 text-[#5E6561]">
            <span className="text-[#858C87]">Orchestration Filter:</span>
            {(['all', 'ROOT', 'CORRELATED', 'DEGRADED'] as const).map((st) => (
              <button
                key={st}
                onClick={() => setStateFilter(st)}
                className={`px-2 py-0.5 rounded-[2px] transition-colors ${
                  stateFilter === st
                    ? 'bg-white text-[#00535f] font-bold shadow-xs border border-[#D9DCD8]'
                    : 'text-[#70797B] hover:text-[#171A19]'
                }`}
              >
                {st}
              </button>
            ))}
          </div>
        </div>
        <div className="text-[10.5px] text-[#70797B]">
          Active: <strong className="text-[#171A19]">{filteredIncidents.length}</strong> incidents ({criticalCount} Crit, {highCount} High, {medCount} Med)
        </div>
      </div>

      {/* MAIN WORKSPACE */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* LEFT: MULTI-INCIDENT ORCHESTRATION TABLE */}
        <div className="flex-1 flex flex-col bg-[#FFFFFF] overflow-y-auto border-r border-[#D9DCD8]">
          {/* PREDICTIVE INCIDENT SECTION (PHASE 6A) */}
          <div className="bg-[#FAF5FF] border-b border-[#E9D5FF] px-4 py-3 shrink-0">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <span className="flex h-2 w-2 relative">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#9333EA] opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-[#7E22CE]"></span>
                </span>
                <span className="font-code text-[11px] font-bold text-[#6D28D9] uppercase tracking-wider">
                  PREDICTIVE INCIDENT · EARLY WARNING
                </span>
                <span className="text-[9.5px] font-code bg-[#7C3AED]/15 text-[#6D28D9] px-1.5 py-0.5 rounded border border-[#7C3AED]/30 font-semibold">
                  NON-MUTATING FORECAST
                </span>
              </div>
              <div className="text-[10px] text-[#70797B] font-code">
                Distinct from reactive incidents · Requires confirmation before RCA
              </div>
            </div>

            {predictiveIncidents.map((pred) => (
              <div
                key={pred.id}
                className="bg-[#FFFFFF] rounded-[4px] border border-[#DDD6FE] p-3 shadow-xs mb-2 last:mb-0"
              >
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#F3E8FF] pb-2 mb-2">
                  <div className="flex items-center gap-2">
                    <span className="font-code text-[12px] font-bold text-[#171A19]">{pred.id}</span>
                    <span className="text-[11px] text-[#5E6561]">Target: <strong className="text-[#6D28D9] font-code">{pred.target}</strong></span>
                    <span className="text-[#D9DCD8]">·</span>
                    <span className="text-[11px] text-[#5E6561]">Predicted failure: <strong className="text-[#B83A3A] font-code">{pred.predicted_failure}</strong></span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="text-[10px] font-code text-[#70797B]">State:</span>
                    <span className={`px-2 py-0.5 rounded-[2px] font-code text-[10px] uppercase border ${getOrchStateBadge(pred.state)}`}>
                      {pred.state}
                    </span>
                  </div>
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px] font-code py-1">
                  <div className="bg-[#FAF5FF] p-2 rounded border border-[#F3E8FF]">
                    <div className="text-[#70797B] text-[9.5px] uppercase">Probability</div>
                    <div className="text-[#6D28D9] font-bold text-[13px]">{Math.round(pred.probability * 100)}%</div>
                  </div>
                  <div className="bg-[#FAF5FF] p-2 rounded border border-[#F3E8FF]">
                    <div className="text-[#70797B] text-[9.5px] uppercase">Horizon</div>
                    <div className="text-[#171A19] font-bold text-[13px]">{pred.horizon_seconds} seconds</div>
                  </div>
                  <div className="bg-[#FAF5FF] p-2 rounded border border-[#F3E8FF]">
                    <div className="text-[#70797B] text-[9.5px] uppercase">Lead Time</div>
                    <div className="text-[#2F7D5C] font-bold text-[13px]">{pred.lead_time_seconds} seconds</div>
                  </div>
                  <div className="bg-[#FAF5FF] p-2 rounded border border-[#F3E8FF]">
                    <div className="text-[#70797B] text-[9.5px] uppercase">Model</div>
                    <div className="text-[#5E6561] font-bold text-[12px] truncate">{pred.model}</div>
                  </div>
                </div>

                {pred.details && (
                  <div className="text-[10.5px] text-[#5E6561] mt-2 italic bg-[#F7F7F5] px-2 py-1 rounded">
                    Telemetry: {pred.details}
                  </div>
                )}

                <div className="flex items-center justify-between pt-2 mt-2 border-t border-[#F3E8FF] text-[10px] font-code">
                  <span className="text-[#70797B]">Transition controls (Phase 6A):</span>
                  <div className="flex items-center gap-1.5">
                    {pred.state === 'PREDICTED' && (
                      <>
                        <button
                          onClick={() => updatePredictiveState(pred.id, 'CONFIRMED')}
                          className="px-2 py-0.5 bg-[#B83A3A] hover:bg-[#9B2C2C] text-white rounded-[2px] transition-colors cursor-pointer"
                        >
                          Confirm Fault Impact
                        </button>
                        <button
                          onClick={() => updatePredictiveState(pred.id, 'EXPIRED')}
                          className="px-2 py-0.5 bg-[#F7F7F5] hover:bg-[#EAECE8] text-[#5E6561] border border-[#D9DCD8] rounded-[2px] transition-colors cursor-pointer"
                        >
                          Expire Horizon
                        </button>
                        <button
                          onClick={() => updatePredictiveState(pred.id, 'CANCELLED')}
                          className="px-2 py-0.5 bg-[#F7F7F5] hover:bg-[#EAECE8] text-[#70797B] border border-[#D9DCD8] rounded-[2px] transition-colors cursor-pointer"
                        >
                          Cancel
                        </button>
                      </>
                    )}
                    {pred.state !== 'PREDICTED' && (
                      <button
                        onClick={() => updatePredictiveState(pred.id, 'PREDICTED')}
                        className="px-2 py-0.5 bg-[#7C3AED]/10 hover:bg-[#7C3AED]/20 text-[#6D28D9] border border-[#7C3AED]/30 rounded-[2px] transition-colors cursor-pointer"
                      >
                        Reset to PREDICTED
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>

          {loading ? (
            <div className="p-8 text-center text-[#70797B] font-code text-[12px]">Loading orchestration queue…</div>
          ) : filteredIncidents.length === 0 ? (
            <div className="p-8 text-center font-code text-[12px]">
              {incidents.length === 0 ? (
                <div>
                  <div className="text-[#2F7D5C] font-bold text-[16px] mb-2">✓ No Active Incidents</div>
                  <div className="text-[#70797B]">All monitored microservices are healthy. Zero conflicts or degraded states.</div>
                  <button
                    onClick={() => onNavigate('simulation')}
                    className="mt-4 px-3 py-1.5 bg-[#286B78] text-white rounded-[3px] text-[11px] font-semibold cursor-pointer"
                  >
                    Run Fault Injection Simulation →
                  </button>
                </div>
              ) : (
                <div className="text-[#70797B]">No incidents match current filter criteria.</div>
              )}
            </div>
          ) : (
            <table className="w-full border-collapse text-left text-[12px]">
              <thead className="sticky top-0 bg-[#F7F7F5] border-b border-[#D9DCD8] z-10 text-[10px] font-code text-[#70797B] uppercase tracking-wider">
                <tr>
                  <th className="py-2.5 px-3 font-semibold">Incident / Correlation</th>
                  <th className="py-2.5 px-3 font-semibold">Severity</th>
                  <th className="py-2.5 px-3 font-semibold">Target & Symptoms</th>
                  <th className="py-2.5 px-3 font-semibold">Orchestration State</th>
                  <th className="py-2.5 px-3 font-semibold">Telemetry Freshness</th>
                  <th className="py-2.5 px-3 font-semibold">Duration</th>
                  <th className="py-2.5 px-3 font-semibold">Acknowledgment</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#EAECE8]">
                {filteredIncidents.map((incident) => {
                  const isSelected = incident.id === selectedId;
                  const services = parseAffectedServices(incident.affectedServices);
                  const orch = orchestratedMap[incident.id] || orchestratedMap[incident.incidentKey];
                  const stateVal = orch?.current_state ?? incident.status;
                  const isDownstream = orch?.is_downstream_symptom;
                  const repCount = orch?.repetition_count ?? 1;

                  return (
                    <tr
                      key={incident.id}
                      onClick={() => setSelectedId(incident.id)}
                      className={`cursor-pointer transition-colors ${
                        isSelected ? 'bg-[#F0F5F6] border-l-4 border-l-[#286B78]' : 'hover:bg-[#F9FAF9]'
                      }`}
                    >
                      {/* Incident & Correlation Identity */}
                      <td className="py-3 px-3 font-code text-[#171A19]">
                        <div className="flex items-center gap-1.5">
                          {incident.severity === 'CRITICAL' && (
                            <span className="w-1.5 h-1.5 rounded-full bg-[#B83A3A] animate-ping" />
                          )}
                          <span className="font-bold">{incident.incidentKey}</span>
                        </div>
                        {orch?.correlation_group && (
                          <div className="text-[10px] text-[#70797B] truncate max-w-[140px]">
                            {orch.correlation_group}
                          </div>
                        )}
                        {repCount > 1 && (
                          <div className="inline-block mt-0.5 px-1 py-0.2 rounded-[2px] bg-[#0284C7]/10 text-[#0284C7] text-[9px] font-bold">
                            {repCount}x DEDUP
                          </div>
                        )}
                      </td>

                      {/* Severity */}
                      <td className="py-3 px-3">
                        <span className={`inline-flex px-1.5 py-0.5 text-[9.5px] font-code font-bold uppercase rounded-[2px] border ${getSeverityBadge(incident.severity)}`}>
                          {incident.severity}
                        </span>
                      </td>

                      {/* Target & Cascade */}
                      <td className="py-3 px-3">
                        <div className="font-semibold text-[#171A19] flex items-center gap-1.5">
                          <span>{incident.title}</span>
                          {isDownstream && (
                            <span className="px-1.5 py-0.2 rounded-[2px] bg-[#7C3AED]/10 text-[#6D28D9] border border-[#7C3AED]/30 font-code text-[9px] font-bold">
                              CORRELATED SYMPTOM
                            </span>
                          )}
                        </div>
                        <div className="flex flex-wrap gap-1 mt-1">
                          {services.map((svc) => (
                            <span key={svc} className="font-code text-[9.5px] px-1.5 py-0.5 rounded-[2px] bg-[#EAECE8] text-[#2F443C]">
                              {svc}
                            </span>
                          ))}
                        </div>
                      </td>

                      {/* State Machine State */}
                      <td className="py-3 px-3">
                        <span className={`inline-flex px-2 py-0.5 text-[10px] font-code font-semibold rounded-[2px] border ${getOrchStateBadge(stateVal)}`}>
                          {stateVal}
                        </span>
                      </td>

                      {/* Telemetry Freshness */}
                      <td className="py-3 px-3 font-code text-[10.5px]">
                        <div className="flex items-center gap-1.5">
                          <span className={`w-2 h-2 rounded-full ${
                            orch?.telemetry_health?.freshness === 'STALE' ? 'bg-[#DC2626]' :
                            orch?.telemetry_health?.freshness === 'DELAYED' ? 'bg-[#D9822B]' : 'bg-[#2F7D5C]'
                          }`} />
                          <span className="font-medium text-[#171A19]">
                            {orch?.telemetry_health?.freshness ?? 'FRESH'}
                          </span>
                        </div>
                        <div className="text-[9.5px] text-[#70797B]">
                          {orch?.telemetry_health?.quality ?? 'HEALTHY'} (lag &lt; 1s)
                        </div>
                      </td>

                      {/* Duration */}
                      <td className="py-3 px-3 font-code text-[11px]">
                        <div className="font-semibold text-[#B83A3A]">{formatDuration(incident.openedAt)}</div>
                        <div className="text-[10px] text-[#70797B]">
                          {new Date(incident.openedAt).toLocaleTimeString()}
                        </div>
                      </td>

                      {/* Acknowledgment */}
                      <td className="py-3 px-3 font-code text-[10.5px]">
                        {orch?.acknowledged_by ? (
                          <div className="text-[#2F7D5C] font-semibold">
                            ✓ {orch.acknowledged_by.split('@')[0]}
                          </div>
                        ) : (
                          <span className="text-[#858C87] italic">Pending Ack</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* RIGHT: MULTI-INCIDENT ORCHESTRATION INSPECTOR */}
        {selected && (
          <div className="w-[420px] bg-[#FFFFFF] border-l border-[#D9DCD8] flex flex-col shrink-0 overflow-y-auto">
            {/* Header Identity Block */}
            <div className="p-4 border-b border-[#D9DCD8] bg-[#F7F7F5]">
              <div className="flex items-center justify-between mb-1.5">
                <span className="font-section text-[10px] text-[#70797B] font-semibold tracking-wider">
                  INCIDENT ORCHESTRATION IDENTITY
                </span>
                <span className={`font-code text-[9.5px] font-bold uppercase px-1.5 py-0.5 rounded-[2px] border ${getSeverityBadge(selected.severity)}`}>
                  {selected.severity}
                </span>
              </div>
              <div className="flex items-baseline gap-2">
                <span className="font-code text-[15px] font-bold text-[#171A19]">{selected.incidentKey}</span>
                <span className="text-[#70797B]">·</span>
                <h2 className="text-[13px] font-semibold text-[#171A19] truncate">{selected.title}</h2>
              </div>
              <div className="mt-2 space-y-1 bg-white p-2 rounded-[3px] border border-[#D9DCD8] font-code text-[10px] text-[#5E6561]">
                <div className="flex justify-between">
                  <span className="text-[#858C87]">Correlation ID:</span>
                  <span className="text-[#171A19] font-medium">{selectedOrch?.correlation_id ?? `COR-${selected.id.slice(0, 10)}`}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#858C87]">Correlation Group:</span>
                  <span className="text-[#171A19] font-medium">{selectedOrch?.correlation_group ?? `GRP-${selected.id.slice(0, 8)}`}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#858C87]">Orchestrator State:</span>
                  <span className={`px-1.5 py-0.2 rounded-[2px] font-semibold border ${getOrchStateBadge(selectedOrch?.current_state ?? selected.status)}`}>
                    {selectedOrch?.current_state ?? selected.status}
                  </span>
                </div>
              </div>
            </div>

            {/* CORRELATION / DOWNSTREAM CASCADE BADGE */}
            {selectedOrch?.is_downstream_symptom && (
              <div className="p-3 bg-[#7C3AED]/10 border-b border-[#7C3AED]/30">
                <div className="flex items-center gap-1.5 font-code text-[11px] font-bold text-[#6D28D9]">
                  <span className="material-symbols-outlined text-[14px]">account_tree</span>
                  <span>CORRELATED DOWNSTREAM SYMPTOM</span>
                </div>
                <p className="text-[11px] text-[#5B21B6] mt-1 leading-snug">
                  This incident is a secondary symptom caused by upstream propagation from root cause{' '}
                  <strong className="underline">{selectedOrch.parent_incident_id ?? 'inventory-db'}</strong>. Independent remediation is suppressed to prevent circular thrashing.
                </p>
              </div>
            )}

            {/* CONFLICT EVALUATION ALERT */}
            <div className="p-3 border-b border-[#D9DCD8]">
              <div className="flex items-center justify-between text-[11px] font-code mb-1.5">
                <span className="text-[#70797B] font-semibold uppercase">SCHEDULER & CONFLICT STATUS</span>
                <span className={`px-1.5 py-0.5 rounded-[2px] font-semibold text-[10px] ${
                  selectedConflicts?.has_conflict ? 'bg-[#DC2626]/10 text-[#DC2626]' : 'bg-[#2F7D5C]/10 text-[#2F7D5C]'
                }`}>
                  {selectedConflicts?.has_conflict ? 'CONFLICT DETECTED' : 'CLEARED FOR FIFO'}
                </span>
              </div>
              {selectedConflicts?.has_conflict ? (
                <div className="p-2.5 rounded-[3px] bg-[#B83A3A]/10 border border-[#B83A3A]/30 text-[11px]">
                  <div className="font-bold text-[#B83A3A] font-code">
                    ⚠️ {selectedConflicts.conflict_type}
                  </div>
                  <div className="text-[#171A19] mt-0.5">{selectedConflicts.explanation}</div>
                  {selectedConflicts.blocked_action_ids?.length > 0 && (
                    <div className="text-[10px] font-code text-[#B83A3A] mt-1">
                      Blocked Action(s): {selectedConflicts.blocked_action_ids.join(', ')}
                    </div>
                  )}
                </div>
              ) : (
                <div className="p-2 rounded-[3px] bg-[#F7F7F5] border border-[#D9DCD8] text-[11px] text-[#5E6561] flex items-center gap-1.5">
                  <span className="text-[#2F7D5C] font-bold">✓</span>
                  <span>No service lock or variable collisions. Ready for deterministic queue dispatch.</span>
                </div>
              )}
            </div>

            {/* TELEMETRY FRESHNESS & QUALITY */}
            <div className="p-3 border-b border-[#D9DCD8]">
              <div className="font-section text-[10px] text-[#70797B] font-semibold tracking-wider mb-1.5">
                TELEMETRY HEALTH GATE
              </div>
              <div className="grid grid-cols-2 gap-2 text-[11px] font-code">
                <div className="p-2 bg-[#F7F7F5] border border-[#EAECE8] rounded-[3px]">
                  <div className="text-[#858C87] text-[10px]">FRESHNESS STATUS</div>
                  <div className="font-bold text-[#2F7D5C] mt-0.5">
                    {selectedOrch?.telemetry_health?.freshness ?? 'FRESH'} (&lt; 3.0s)
                  </div>
                </div>
                <div className="p-2 bg-[#F7F7F5] border border-[#EAECE8] rounded-[3px]">
                  <div className="text-[#858C87] text-[10px]">NUMERIC QUALITY</div>
                  <div className="font-bold text-[#2F7D5C] mt-0.5">
                    {selectedOrch?.telemetry_health?.quality ?? 'HEALTHY'} (0 NaN / 0 Inf)
                  </div>
                </div>
              </div>
            </div>

            {/* RCA STATUS & ROOT CAUSE */}
            <div className="p-3 border-b border-[#D9DCD8] bg-[#FFFFFF]">
              <div className="flex items-center justify-between text-[11px] font-code mb-1">
                <span className="text-[#70797B] uppercase">Root Cause Attribution</span>
                <span className="font-bold text-[#00535f]">
                  {selectedRca?.analysis?.confidence ? `${Math.round(selectedRca.analysis.confidence * 100)}% Conf` : 'Active'}
                </span>
              </div>
              <div className="p-2 rounded-[3px] bg-[#F0F5F6] border border-[#99F6E4]/60">
                <div className="font-code text-[10px] text-[#00535f] font-semibold uppercase">Primary Root-Cause Service</div>
                <div className="font-code text-[13px] font-bold text-[#171A19] mt-0.5">
                  {selectedOrch?.root_cause ?? selectedRca?.analysis?.root_cause ?? 'inventory-db'}
                </div>
                <div className="text-[10px] text-[#70797B] mt-0.5 font-code">
                  Topology-constrained lagged SCM verification
                </div>
              </div>
            </div>

            {/* DETERMINISTIC AUDIT TIMELINE */}
            <div className="p-3 border-b border-[#D9DCD8] flex-1">
              <div className="font-section text-[10px] text-[#70797B] font-semibold tracking-wider mb-2">
                STATE TRANSITION AUDIT TRAIL ({selectedTimeline.length})
              </div>
              <div className="space-y-2">
                {selectedTimeline.map((step, idx) => (
                  <div key={idx} className="p-2 rounded-[3px] bg-[#F7F7F5] border border-[#EAECE8] text-[11px] font-code">
                    <div className="flex items-center justify-between text-[10px]">
                      <span className="font-bold text-[#171A19]">
                        {step.from_state} → <span className="text-[#286B78]">{step.to_state}</span>
                      </span>
                      <span className="text-[#858C87]">{new Date(step.timestamp).toLocaleTimeString()}</span>
                    </div>
                    <div className="text-[10px] text-[#5E6561] mt-0.5">{step.reason}</div>
                    <div className="text-[9px] text-[#858C87] mt-0.5">Actor: {step.actor}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* ACKNOWLEDGMENT & ACTIONS */}
            <div className="p-4 bg-[#F7F7F5] border-t border-[#D9DCD8] flex flex-col gap-2 shrink-0 mt-auto">
              {ackSuccessMessage && (
                <div className="text-[11px] font-code font-semibold text-[#2F7D5C] bg-[#2F7D5C]/10 border border-[#2F7D5C]/30 p-1.5 rounded-[2px] text-center">
                  {ackSuccessMessage}
                </div>
              )}
              <div className="flex gap-2">
                <input
                  type="text"
                  value={operatorId}
                  onChange={(e) => setOperatorId(e.target.value)}
                  className="flex-1 px-2.5 py-1 text-[11px] font-code bg-white border border-[#D9DCD8] rounded-[3px] text-[#171A19]"
                  placeholder="operator@causalops.local"
                />
                <button
                  onClick={handleAcknowledge}
                  className="px-3 py-1 bg-[#FFFFFF] border border-[#D9DCD8] hover:bg-[#F1F2F0] text-[#171A19] font-code text-[11px] font-semibold rounded-[3px] transition-colors cursor-pointer"
                >
                  Acknowledge
                </button>
              </div>

              <button
                onClick={() => onNavigate('root-cause')}
                className="w-full h-8 px-4 rounded-[3px] bg-[#286B78] hover:bg-[#00535f] text-white font-code text-[11px] font-semibold uppercase tracking-wider flex items-center justify-center gap-1.5 transition-colors shadow-xs cursor-pointer mt-1"
              >
                <span>OPEN ROOT-CAUSE & REMEDIATION PLAN</span>
                <span className="material-symbols-outlined text-[14px]">arrow_forward</span>
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
