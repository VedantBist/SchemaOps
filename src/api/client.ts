const env = typeof import.meta !== 'undefined' ? (import.meta as any).env : undefined;
const base = env?.VITE_API_BASE_URL ?? 'http://localhost:8080/api';
const aiBase = env?.VITE_AI_ENGINE_URL ?? 'http://localhost:8000';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isCausalRoute =
    path.startsWith('/causal') ||
    path.startsWith('/remediation') ||
    path.startsWith('/system') ||
    path.startsWith('/observability') ||
    path === '/health';

  // For causal/remediation routes, query AI Engine (8000) directly first
  if (isCausalRoute) {
    try {
      const aiResponse = await fetch(aiBase + path, {
        headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
        ...init,
      });
      if (aiResponse.ok) {
        return aiResponse.status === 204 ? (undefined as T) : (aiResponse.json() as Promise<T>);
      }
      let detail = '';
      try { detail = await aiResponse.text(); } catch { /* ignore */ }
      // If AI Engine returned 404, try Gateway proxy as fallback
      if (aiResponse.status === 404) {
        try {
          const gwResponse = await fetch(base + path, {
            headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
            ...init,
          });
          if (gwResponse.ok) {
            return gwResponse.status === 204 ? (undefined as T) : (gwResponse.json() as Promise<T>);
          }
        } catch { /* ignore fallback error */ }
      }
      throw new Error(`AI Engine HTTP ${aiResponse.status}: ${detail || aiResponse.statusText}`);
    } catch (aiErr: any) {
      // If AI engine network call failed, try Gateway fallback
      try {
        const gwResponse = await fetch(base + path, {
          headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
          ...init,
        });
        if (gwResponse.ok) {
          return gwResponse.status === 204 ? (undefined as T) : (gwResponse.json() as Promise<T>);
        }
        let detail = '';
        try { detail = await gwResponse.text(); } catch { /* ignore */ }
        throw new Error(`Gateway HTTP ${gwResponse.status}: ${detail || gwResponse.statusText}`);
      } catch (gwErr: any) {
        throw new Error(aiErr?.message || gwErr?.message || 'CausalOps backend connection failed');
      }
    }
  }

  // Standard routes: Gateway (8080/api) first
  try {
    const response = await fetch(base + path, {
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
      ...init,
    });
    if (response.ok) {
      return response.status === 204 ? (undefined as T) : (response.json() as Promise<T>);
    }
    let detail = '';
    try { detail = await response.text(); } catch { /* ignore */ }
    throw new Error(`CausalOps API HTTP ${response.status}: ${detail || response.statusText}`);
  } catch (err: any) {
    throw new Error(err?.message || 'CausalOps API connection failed');
  }
}

export interface ServiceSummary {
  id: string;
  name: string;
  displayName: string;
  type: string;
  status: 'healthy' | 'degraded' | 'critical' | 'warning';
  latencyP99: number;
  baselineLatency: number;
  errorRate: number;
  throughput: string;
  causalRole: string;
}

export interface TopologyEdge {
  id: string;
  source: string;
  target: string;
  direction: string;
  type: string;
}

export interface TopologyData {
  nodes: ServiceSummary[];
  edges: TopologyEdge[];
}

export interface ActiveIncident {
  id: string;
  incidentKey: string;
  title: string;
  severity: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';
  status: string;
  openedAt: string;
  summary: string;
  affectedServices: string; // JSON text from DB
}

export interface RcaAnalysis {
  analysis: {
    id: string;
    incident_id: string;
    completed_at: string;
    methodology: string;
    root_cause: string;
    confidence: number;
    evidence: string;
  };
  candidates: {
    service: string;
    score: number;
    signals: string;
  }[];
}

export interface Prediction {
  id: string;
  service: string;
  probability: number;
  riskLevel: 'CRITICAL' | 'HIGH' | 'ELEVATED' | 'LOW';
  horizonSeconds: number;
  factors: string;
  createdAt: string;
}

export interface TelemetrySample {
  service: string;
  timestamp: string;
  p50Latency: number;
  p95Latency: number;
  p99Latency: number;
  errorRate: number;
  requestRate: number;
  dbLatency?: number;
  poolUtilization?: number;
  anomalyScore: number;
}

export interface MetricsResponse {
  service: string | null;
  samples: TelemetrySample[];
}

export interface SimulationResult {
  id: string;
  methodology: string;
  results: {
    service: string;
    baseline: number;
    counterfactual: number;
    classification: string;
    label: string;
  }[];
}

export interface OverviewData {
  systemStatus: string;
  activeIncidents: ActiveIncident[];
  services: ServiceSummary[];
  topology: TopologyData;
}

export const causalOpsApi = {
  overview: () => request<OverviewData>('/overview'),
  topology: () => request<TopologyData>('/topology'),
  services: () => request<ServiceSummary[]>('/services'),
  service: (id: string) => request<ServiceSummary>(`/services/${id}`),

  incidents: () => request<ActiveIncident[]>('/incidents/active'),
  incidentHistory: () => request<ActiveIncident[]>('/incidents/history'),
  incident: (id: string) => request<ActiveIncident>(`/incidents/${id}`),
  incidentRca: (id: string) => request<RcaAnalysis>(`/incidents/${id}/root-cause`),

  predictions: () => request<Prediction[]>('/predictions'),
  prediction: (serviceId: string) => request<Prediction[]>(`/predictions/${serviceId}`),

  metrics: (service?: string) =>
    request<MetricsResponse>(`/metrics${service ? `?service=${encodeURIComponent(service)}` : ''}`),

  logs: () => request<unknown[]>('/logs'),
  traces: () => request<unknown[]>('/traces'),

  injectFault: (fault: {
    type: string;
    target: string;
    severity: string;
    durationSeconds: number;
    parameters: Record<string, unknown>;
  }) => request<unknown>('/faults', { method: 'POST', body: JSON.stringify(fault) }),

  clearFaults: () => request<void>('/faults/clear', { method: 'POST' }),

  simulate: (input: { target: string; reductionPercent: number }) =>
    request<SimulationResult>('/simulations', { method: 'POST', body: JSON.stringify(input) }),

  runCounterfactualSimulation: async (
    req: CausalCounterfactualRequest
  ): Promise<CausalCounterfactualResponse> => {
    const raw = await request<CausalCounterfactualResponse>('/causal/counterfactual', {
      method: 'POST',
      body: JSON.stringify(req),
    });
    return validateSimulationResult(raw, req);
  },

  events: (onEvent: (event: unknown) => void): (() => void) => {
    const stream = new EventSource(base + '/events/stream');
    stream.onmessage = (event) => {
      try { onEvent(JSON.parse(event.data)); } catch { /* ignore malformed */ }
    };
    stream.onerror = () => { /* reconnection handled by browser */ };
    return () => stream.close();
  },

  approveRemediation: (data: {
    recommendation_id: string;
    approved_by: string;
    warning_acknowledged?: boolean;
    ttl_seconds?: number;
  }) => request<RemediationApproval>('/remediation/approve', {
    method: 'POST',
    body: JSON.stringify(data),
  }),

  executeRemediation: (data: {
    recommendation_id: string;
    approval_id: string;
    environment?: string;
    dry_run?: boolean;
    simulation_mode?: boolean;
  }) => request<ClosedLoopExecutionRecord>('/remediation/execute', {
    method: 'POST',
    body: JSON.stringify(data),
  }),

  getExecution: (executionId: string) =>
    request<ClosedLoopExecutionRecord>(`/remediation/executions/${executionId}`),

  listExecutions: () =>
    request<ClosedLoopExecutionRecord[]>('/remediation/executions'),

  orchestratedIncidents: () => request<OrchestratedIncident[]>('/incidents'),
  orchestratedIncident: (id: string) => request<OrchestratedIncident>(`/incidents/${id}`),
  incidentTimeline: (id: string) => request<Array<{
    transition_id: string;
    from_state: string;
    to_state: string;
    timestamp: string;
    actor: string;
    reason: string;
  }>>(`/incidents/${id}/timeline`),
  incidentHealth: (id: string) => request<Record<string, unknown>>(`/incidents/${id}/health`),
  incidentConflicts: (id: string) => request<IncidentConflictData>(`/incidents/${id}/conflicts`),
  acknowledgeIncident: (id: string, operator_id?: string) =>
    request<OrchestratedIncident>(`/incidents/${id}/acknowledge`, {
      method: 'POST',
      body: JSON.stringify({ operator_id: operator_id || 'oncall-sre@causalops.local' }),
    }),
  systemHealth: () => request<SystemHealthData>('/system/health'),
  observabilityMetrics: () => request<Record<string, number>>('/observability/metrics'),
  aiHealth: () => request<{ status: string; counterfactual_engine: string }>('/health'),
};

export interface RemediationApproval {
  approval_id: string;
  recommendation_id: string;
  incident_id: string;
  action_id: string;
  approved_by: string;
  approved_at: string;
  expires_at: string;
  approval_status: 'PENDING' | 'APPROVED' | 'REJECTED' | 'EXPIRED' | 'CONSUMED';
  warning_acknowledged: boolean;
  metadata?: Record<string, unknown>;
}

export interface ExecutionTimelineEntry {
  event_id: string;
  timestamp: string;
  execution_id: string;
  incident_id: string;
  action_id: string;
  target_service: string;
  previous_state: string;
  new_state: string;
  actor: string;
  policy_result?: Record<string, unknown>;
  execution_result?: Record<string, unknown>;
  verification_result?: Record<string, unknown>;
  rollback_result?: Record<string, unknown>;
  error?: string | null;
}

export interface VerificationResult {
  verified: boolean;
  target_improved: boolean;
  severity_decreased: boolean;
  health_restored: boolean;
  no_downstream_regression: boolean;
  stability_confirmed: boolean;
  metrics_before: {
    gateway_p99_latency_ms: number;
    gateway_error_rate_pct: number;
    target_variable: number;
  };
  metrics_after: {
    gateway_p99_latency_ms: number;
    gateway_error_rate_pct: number;
    target_variable: number;
  };
  metrics_delta: {
    gateway_latency_reduction_ms?: number;
    gateway_error_rate_reduction_pct?: number;
    target_variable_reduction?: number;
  };
  summary: string;
  verified_at: string;
  verification_window_steps: number;
}

export interface RollbackResult {
  execution_id: string;
  action_id: string;
  target_service: string;
  rollback_success: boolean;
  status: string;
  started_at: string;
  completed_at: string;
  mutation_summary: string;
  error?: string | null;
}

export interface ClosedLoopExecutionRecord {
  execution_id: string;
  incident_id: string;
  recommendation_id: string;
  approval_id: string;
  action_id: string;
  target_service: string;
  target_variable: string;
  state: string;
  policy_decision: {
    allowed: boolean;
    rule_results: Record<string, boolean>;
    denial_reasons: string[];
    validated_at: string;
  };
  pre_snapshot: {
    snapshot_id: string;
    captured_at: string;
    gateway_p99_latency_ms: number;
    gateway_error_rate_pct: number;
    metrics: Record<string, number>;
  };
  execution_result?: {
    execution_success: boolean;
    mutation_summary: string;
    started_at: string;
    completed_at: string;
  };
  verification_result?: VerificationResult;
  rollback_result?: RollbackResult;
  timeline: ExecutionTimelineEntry[];
  error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface OrchestratedIncident {
  incident_id: string;
  correlation_id: string;
  correlation_group: string;
  created_at: string;
  updated_at: string;
  detection_source: string;
  affected_services: string[];
  root_cause: string | null;
  fault_signature: string;
  current_state: string;
  severity: string;
  is_downstream_symptom: boolean;
  parent_incident_id?: string | null;
  recommendation_id?: string | null;
  approval_id?: string | null;
  execution_id?: string | null;
  repetition_count: number;
  telemetry_health?: {
    freshness: string;
    quality: string;
    can_execute_remediation: boolean;
    reasons: string[];
  };
  recovery_status?: string | null;
  oscillation_detected: boolean;
  acknowledged_by?: string | null;
  acknowledged_at?: string | null;
  metadata?: Record<string, unknown>;
  timeline?: Array<{
    transition_id: string;
    from_state: string;
    to_state: string;
    timestamp: string;
    actor: string;
    reason: string;
  }>;
}

export interface SystemHealthData {
  components: {
    telemetry: string;
    rca_engine: string;
    gnn_engine: string;
    causal_scm: string;
    recommendation_engine: string;
    execution_engine: string;
    verification_engine: string;
  };
  errors: Record<string, string>;
  overall_status: 'HEALTHY' | 'DEGRADED' | 'DOWN';
  evaluated_at: string;
}

export interface IncidentConflictData {
  has_conflict: boolean;
  conflict_type: string | null;
  candidate_incident_id: string;
  blocked_action_ids: string[];
  explanation: string;
}

export interface ServiceTimeFrame {
  observed: Record<string, number>;
  counterfactual: Record<string, number>;
  effect_delta: Record<string, number>;
  state: 'OPERATIONAL' | 'DEGRADED' | 'CRITICAL';
}

export interface SimulationTimeFrame {
  time_seconds: number;
  step: number;
  is_intervened: boolean;
  root_cause_observed: number;
  root_cause_counterfactual: number;
  gateway_latency_observed: number;
  gateway_latency_counterfactual: number;
  gateway_error_rate_observed: number;
  gateway_error_rate_counterfactual: number;
  services: Record<string, ServiceTimeFrame>;
}

export interface CausalCounterfactualRequest {
  experiment_id?: string;
  incident_id?: string;
  root_cause?: string | number | Record<string, unknown>;
  intervention?: string | Record<string, unknown>;
  intervention_spec?: string | Record<string, unknown>;
  intervention_magnitude?: number;
  start_step?: number;
  horizon?: number;
  simulation_resolution?: number;
  telemetry_array?: number[][][];
}

export interface AvoidedImpactData {
  evaluation_window_steps: number;
  gateway_latency: {
    unit: string;
    peak_avoided_latency_ms: number;
    mean_avoided_latency_ms: number;
    cumulative_avoided_latency_ms_samples: number;
  };
  gateway_error_rate: {
    unit: string;
    peak_avoided_error_rate_pct: number;
    mean_avoided_error_rate_pct: number;
    cumulative_avoided_error_exposure_pct_seconds: number;
    estimated_avoided_failed_requests: number;
    failed_requests_estimation_basis: string;
  };
  per_service_avoided_impact: Record<
    string,
    {
      peak_avoided_p99_latency_ms: number;
      mean_avoided_p99_latency_ms: number;
      peak_avoided_error_rate_pct: number;
      mean_avoided_error_rate_pct: number;
    }
  >;
  gateway_latency_avoided_ms?: number;
  gateway_latency_reduction_pct?: number;
  gateway_error_rate_avoided_pct?: number;
  affected_services_count?: number;
  mitigation_success?: boolean;
  restored_services?: string[];
  remaining_degraded_services?: string[];
}

export interface ValidityMetadata {
  causal_validation_status: string;
  extrapolation_status: string;
  physical_validity: string;
  temporal_validity: string;
  branch_validity: string;
  calibration_reference: string;
  nonlinear_risk: string;
  warnings: string[];
  clamping_events_recorded?: number;
  is_valid?: boolean;
  counterfactual_in_bounds?: boolean;
  r2_score?: number;
  confidence_level?: string;
  [key: string]: unknown;
}

export interface CausalCounterfactualResponse {
  experiment_id: string;
  root_cause: string;
  intervention_metadata: {
    root_cause?: string;
    intervention_type?: string;
    intervention_variable?: string;
    intervention_start_step?: number;
    horizon?: number;
    actual_mean?: number;
    counterfactual_value?: number;
    delta?: number;
    nominal_baseline_mean?: number;
    [key: string]: unknown;
  };
  avoided_impact: AvoidedImpactData;
  validity_metadata: ValidityMetadata;
  warnings: string[];
  counterfactual_trajectory: number[][][];
  observed_trajectory: number[][][];
  effect_trajectory: number[][][];
  visualization_data: {
    timesteps: number[];
    root_cause_series: { observed: number[]; counterfactual: number[] };
    gateway_latency_series: { observed: number[]; counterfactual: number[] };
    gateway_error_rate_series: { observed: number[]; counterfactual: number[] };
  };
  simulation_resolution: number;
  total_horizon_seconds: number;
  timeline: SimulationTimeFrame[];
}

export function validateSimulationResult(
  data: unknown,
  _req?: CausalCounterfactualRequest
): CausalCounterfactualResponse {
  if (!data || typeof data !== 'object') {
    throw new Error('Simulation response is not a valid object');
  }

  const res = data as Partial<CausalCounterfactualResponse>;

  if (!res.timeline || !Array.isArray(res.timeline) || res.timeline.length === 0) {
    throw new Error('Simulation response missing or empty timeline array');
  }

  if (!res.visualization_data || typeof res.visualization_data !== 'object') {
    throw new Error('Simulation response missing visualization_data object');
  }

  const { timesteps, root_cause_series, gateway_latency_series, gateway_error_rate_series } =
    res.visualization_data;
  if (!Array.isArray(timesteps) || timesteps.length === 0) {
    throw new Error('Simulation response missing or empty visualization timesteps');
  }

  if (
    !root_cause_series?.observed ||
    !root_cause_series?.counterfactual ||
    !gateway_latency_series?.observed ||
    !gateway_latency_series?.counterfactual ||
    !gateway_error_rate_series?.observed ||
    !gateway_error_rate_series?.counterfactual
  ) {
    throw new Error('Simulation response missing required visualization series arrays');
  }

  const len = res.timeline.length;
  if (timesteps.length !== len) {
    throw new Error(
      `Timeline length (${len}) does not match timesteps length (${timesteps.length})`
    );
  }

  // Validate monotonicity, finiteness, and structure of timeline
  for (let i = 0; i < len; i++) {
    const frame = res.timeline[i];
    if (typeof frame.time_seconds !== 'number' || !Number.isFinite(frame.time_seconds)) {
      throw new Error(`Timeline frame ${i} has invalid time_seconds: ${frame.time_seconds}`);
    }
    if (i > 0 && frame.time_seconds < res.timeline[i - 1].time_seconds) {
      throw new Error(
        `Timeline timestamps are not monotonic: frame ${i} (${frame.time_seconds}) < frame ${i - 1} (${res.timeline[i - 1].time_seconds})`
      );
    }
    if (
      !Number.isFinite(frame.root_cause_observed) ||
      !Number.isFinite(frame.root_cause_counterfactual)
    ) {
      throw new Error(`Timeline frame ${i} contains non-finite root_cause values`);
    }
    if (
      !Number.isFinite(frame.gateway_latency_observed) ||
      !Number.isFinite(frame.gateway_latency_counterfactual)
    ) {
      throw new Error(`Timeline frame ${i} contains non-finite gateway_latency values`);
    }
    if (
      !Number.isFinite(frame.gateway_error_rate_observed) ||
      !Number.isFinite(frame.gateway_error_rate_counterfactual)
    ) {
      throw new Error(`Timeline frame ${i} contains non-finite gateway_error_rate values`);
    }
    if (!frame.services || typeof frame.services !== 'object') {
      throw new Error(`Timeline frame ${i} missing services object`);
    }
  }

  if (res.counterfactual_trajectory && !Array.isArray(res.counterfactual_trajectory)) {
    throw new Error('counterfactual_trajectory must be an array');
  }
  if (res.observed_trajectory && !Array.isArray(res.observed_trajectory)) {
    throw new Error('observed_trajectory must be an array');
  }

  return res as CausalCounterfactualResponse;
}

