import { asJson, get, post, put } from './client';

// ── shapes returned by the CausalOps API ─────────────────────────────────────
export type EnvStatus = 'LEARNING' | 'CALIBRATED' | 'ACTIVE' | 'DISABLED';

export interface EnvironmentRef { id: string; name: string; status: EnvStatus }

export interface Environment extends EnvironmentRef {
  statusReason: string | null;
  config: EnvironmentConfig;
  learningStartedAt: string;
  calibratedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface MetricTemplate { query: string; label?: string | null; scale?: number | null }
export interface Slo { latencyP99Ms: number; errorRatePct: number; criticalMultiplier: number }
export interface RemediationAction {
  id: string; name: string; executor: 'docker' | 'kubernetes' | 'webhook'; operation: string; tier: number;
  appliesTo: string[]; signals: string[]; params: Record<string, unknown>; description: string;
}
export interface RemediationSettings {
  dryRun: boolean; killSwitch: boolean; autoExecuteMaxTier: number; minRcaConfidence: number;
  maxTelemetryAgeSeconds: number; maxAutoActionsPerHour: number; cooldownMinutes: number;
  recommendationTtlMinutes: number; maxAutoBlastRadius: number;
  verification: { settleSeconds: number; windowSeconds: number; healthySamples: number };
  executors: Record<string, Record<string, unknown>>;
  targets: Record<string, Record<string, string>>;
  actions: RemediationAction[];
}
export interface EnvironmentConfig {
  endpoints: { prometheusUrl: string; tempoUrl?: string | null; lokiUrl?: string | null; collectorHealthUrl?: string | null };
  telemetry: {
    rateWindow: string; topologyWindow: string; staleAfterSeconds: number;
    serviceMetrics: Record<string, MetricTemplate>; dependencyNodeMetrics?: Record<string, MetricTemplate> | null;
    edgeMetrics?: Record<string, MetricTemplate> | null; topologyQuery: string;
  };
  slo: Slo;
  serviceSlos: Record<string, Slo>;
  detection: { breachSamples: number; recoverySamples: number };
  externalNodes: string[];
  calibration: { learningWindowHours: number; retrainIntervalDays: number; minLearningMinutes: number };
  analysis: Record<string, unknown>;
  remediation: RemediationSettings;
}

export interface Service {
  id: string; name: string; kind: string; source: string; status: string;
  latencyP99: number | null; errorRate: number | null; requestRate: number | null; baselineLatency: number | null;
  firstSeenAt: string; lastSeenAt: string | null; lastSampleAt: string | null; stale: boolean;
}

export interface Edge {
  source: string; target: string; origin: string; connectionType: string | null;
  callRate: number | null; failedRate: number | null; lastSeenAt: string | null; stale: boolean;
}

export interface Incident {
  id: string; incidentKey: string; environmentId: string; title: string; severity: string; status: string;
  detectionSource: string; openedAt: string; resolvedAt: string | null; updatedAt: string; summary: string;
  affectedServices: string[]; evidence: Record<string, unknown>[];
}

export interface TimelineEvent { id: string; occurredAt: string; eventType: string; payload: Record<string, unknown> }

export interface Sample {
  service: string; timestamp: string; p50Latency: number | null; p95Latency: number | null; p99Latency: number | null;
  errorRate: number | null; requestRate: number | null; dbLatency: number | null; poolUtilization: number | null;
  poolPending: number | null; anomalyScore: number | null;
}

export interface Prediction {
  id: string; service: string; probability: number; riskLevel: string; horizonSeconds: number;
  factors: { name: string; value: number; slo?: number }[]; createdAt: string; method?: string; modelVersion?: string;
}

export interface SystemStatus {
  environment: EnvironmentRef | null; status: string;
  ingestion: { lastSampleAt: string | null; ageSeconds: number | null; status: string };
  topology: { nodes: number; edges: number };
  components: Record<string, string>; checkedAt: string;
}

export interface CalibrationRun {
  id: string; mode: string; trigger: string; status: string; startedAt: string; finishedAt: string | null;
  dataFrom: string | null; dataTo: string | null; modelVersion: string | null; promoted: boolean | null;
  qualityPassed: boolean | null; decision: string | null; error: string | null; metrics: CalibrationMetrics | null;
}

export interface CalibrationMetrics {
  data?: Record<string, number | string>;
  gate?: { heldout_false_positive_rate?: number | null; heldout_samples?: number; note?: string };
  episodes?: {
    count: number; single_root: number; detection_recall: number; median_detection_delay_s: number | null;
    rca_top1_accuracy: number | null; rca_top2_accuracy: number | null;
    rca_by_fault_type?: Record<string, { n: number; top1: number }>;
    per_episode?: { type: string; target: string; detected: boolean; detection_delay_s: number | null;
      expected: string[]; predicted: string[]; top1: boolean; concurrent: boolean }[];
  };
  scm?: { median_holdout_r2?: number | null; variables?: number; ensemble_members?: number; r2_method?: string };
  forecast?: Record<string, { method: string; note?: string; positives?: number }>;
}

export interface CalibrationStatus {
  environment: EnvironmentRef; statusReason: string | null;
  learning: { dataMinutes: number; learningWindowMinutes: number; minLearningMinutes: number; progressPct: number; canCalibrateNow: boolean };
  calibratedAt: string | null; nextRetrainDue: string | null; runs: CalibrationRun[];
  champion: ModelVersion | null;
}

export interface ModelVersion {
  version: string; status: string; createdAt: string; promotedAt: string | null; retiredAt: string | null;
  dataFrom: string; dataTo: string; checksum: string; metrics: CalibrationMetrics;
}

export interface RcaCandidate {
  service: string; score: number;
  detail: { kind: string; target: string; confidence: number; components: Record<string, number>;
    onset: string | null; explains?: Record<string, number>; signals: { variable: string; metric: string; max_z: number | null;
      max_residual_sigma: number | null; max_probability: number }[]; root_variable: string };
}

export interface Counterfactual {
  intervention: { unit: string; target: string; variables: string[]; magnitude: number; start: string | null; semantics: string };
  timestamps: string[];
  nodes: Record<string, Record<string, { observed: number[]; counterfactual: number[]; low: number[]; high: number[] }>>;
  entry_impact: Record<string, { peak_avoided_latency_ms: number; mean_avoided_latency_ms: number; peak_avoided_error_pct: number | null }>;
  restored_nodes: string[]; ensemble_members: number;
  validity: { status: string; warnings: string[]; pre_intervention_max_diff?: number; unreachable_max_diff?: number;
    propagation_median_holdout_r2?: number | null };
}

export interface RootCause {
  analysis: { id: string; completedAt: string; methodology: string; rootCause: string; rootCauseKind: string;
    confidence: number; evidence: { type: string; unit: string; statement: string; explained_by?: string[] }[]; modelVersion: string };
  candidates: RcaCandidate[];
  counterfactual: { id: string; createdAt: string; modelVersion: string; result: Counterfactual } | null;
}

export interface PolicyRule { id: string; safety: boolean; passed: boolean; detail: string }
export interface Recommendation {
  id: string; incidentId: string; rank: number; actionId: string; actionName: string; executor: string; operation: string;
  targetNode: string; targetKind: string; binding: string; tier: number; reversible: boolean; params: Record<string, unknown>;
  rcaConfidence: number; expectedBenefit: { meanAvoidedLatencyMs: number | null; peakAvoidedLatencyMs: number | null;
    validity: string; restoredNodes: number }; effectiveness: { successes: number; attempts: number; rate: number };
  score: number; rationale: string; status: string; policy: { mode?: string; summary?: string; rules?: PolicyRule[] };
  createdAt: string; expiresAt: string;
}

export interface Execution {
  id: string; incidentId: string; recommendationId: string; actionId: string; executor: string; operation: string;
  targetNode: string; binding: string; mode: string; approvedBy: string | null; dryRun: boolean; status: string;
  startedAt: string; executedAt: string | null; verifyDeadline: string | null; healthyStreak: number; finishedAt: string | null;
  result: Record<string, unknown>; rollbackState: unknown; verification: { watched?: Record<string, string>;
    healthyStreak?: number; requiredHealthySamples?: number }; rollbackResult: Record<string, unknown> | null;
}

export interface AuditEntry { id: number; at: string; actor: string; action: string; entityType: string; entityId: string | null;
  incidentId: string | null; detail: Record<string, unknown> }

export interface OutcomeMetrics {
  entryServices: string[]; sampleSeconds: number;
  summary: Record<string, { resolvedIncidents: number; medianMttrSeconds: number | null; meanMttrSeconds: number | null;
    medianDowntimeSeconds: number | null; medianTimeToMitigationSeconds: number | null; medianMttdSeconds: number | null }>;
  incidents: { incidentId: string; incidentKey: string; status: string; handling: string; openedAt: string; resolvedAt: string | null;
    mttdSeconds: number | null; timeToMitigationSeconds: number | null; mttrSeconds: number | null; downtimeSeconds: number }[];
}

export interface Fault {
  id: string; type: string; target: string; severity: string; durationSeconds: number; parameters: Record<string, unknown>;
  status: string; startedAt: string; stoppedAt: string | null;
}

export interface ChangeEvent { id: string; kind: string; target: string | null; startedAt: string; endedAt: string | null;
  source: string; description: string; referenceId: string | null }

export interface LogLine { timestamp: string; service: string | null; level: string | null; traceId: string | null; message: string }
export interface TraceSummary { traceId: string; rootService: string; rootOperation: string; startTime: string; durationMs: number }

export interface EnvironmentValidation {
  valid: boolean; configError?: string;
  endpoints?: Record<string, string>;
  metrics?: Record<string, { series: number; nodes?: string[]; error?: string }>;
  topology?: { nodes?: Record<string, string>; edges?: { client: string; server: string; callRate: number; connectionType: string }[]; error?: string };
  config?: EnvironmentConfig;
}

// ── calls ────────────────────────────────────────────────────────────────────
type Env = string | undefined;

const normIncident = (i: Incident): Incident => ({
  ...i,
  affectedServices: asJson<string[]>(i.affectedServices, []),
  evidence: asJson<Record<string, unknown>[]>(i.evidence, []),
});

export const api = {
  environments: () => get<Environment[]>('/environments'),
  environmentDefaults: () => get<EnvironmentConfig>('/environments/defaults'),
  createEnvironment: (name: string, config: EnvironmentConfig) => post<Environment>('/environments', { name, config }),
  updateEnvironmentConfig: (id: string, config: EnvironmentConfig) => put<Environment>(`/environments/${id}/config`, config),
  validateEnvironment: (config: EnvironmentConfig) => post<EnvironmentValidation>('/environments/validate', config),

  systemStatus: (environmentId: Env) => get<SystemStatus>('/system/status', { environmentId }),
  services: (environmentId: Env) => get<Service[]>('/services', { environmentId }),
  topology: (environmentId: Env) => get<{ nodes: Service[]; edges: Edge[] }>('/topology', { environmentId }),
  addNode: (environmentId: Env, name: string, kind: string) => post('/topology/nodes', { name, kind }, { environmentId }),
  addEdge: (environmentId: Env, source: string, target: string) => post('/topology/edges', { source, target }, { environmentId }),
  metrics: (environmentId: Env, service: string, minutes: number) =>
    get<{ service: string; samples: Sample[] }>('/metrics', { environmentId, service, minutes }),

  incidents: async (environmentId: Env, state: 'active' | 'resolved' | 'all', limit = 100) =>
    (await get<Incident[]>('/incidents', { environmentId, state, limit })).map(normIncident),
  incident: async (id: string) => normIncident(await get<Incident>(`/incidents/${id}`)),
  timeline: async (id: string) => (await get<TimelineEvent[]>(`/incidents/${id}/timeline`))
    .map((e) => ({ ...e, payload: asJson<Record<string, unknown>>(e.payload, {}) })),
  rootCause: (id: string) => get<RootCause>(`/incidents/${id}/root-cause`),
  runRca: (id: string) => post<RootCause>(`/incidents/${id}/rca`),

  predictions: async (environmentId: Env) => (await get<Prediction[]>('/predictions', { environmentId }))
    .map((p) => ({ ...p, factors: asJson<Prediction['factors']>(p.factors, []) })),
  counterfactual: (environmentId: string, start: string, end: string, unit: string, magnitude: number) =>
    post<Counterfactual>('/engine/counterfactual', { environment_id: environmentId, start, end, unit, magnitude }),

  calibrationStatus: (environmentId: Env) => get<CalibrationStatus>('/calibration/status', { environmentId }),
  runCalibration: (environmentId: Env) => post<{ runId: string }>('/calibration/run', undefined, { environmentId }),
  models: (environmentId: Env) => get<ModelVersion[]>('/models', { environmentId }),

  recommendations: (environmentId: Env, incidentId?: string, status?: string) =>
    get<Recommendation[]>('/remediation/recommendations', { environmentId, incidentId, status }),
  approve: (id: string, by: string, reason: string) => post<Execution>(`/remediation/recommendations/${id}/approve`, { by, reason }),
  reject: (id: string, by: string, reason: string) => post<Recommendation>(`/remediation/recommendations/${id}/reject`, { by, reason }),
  plan: (incidentId: string) => post<Recommendation[]>(`/remediation/incidents/${incidentId}/plan`),
  executions: (environmentId: Env, incidentId?: string) => get<Execution[]>('/remediation/executions', { environmentId, incidentId }),
  rollback: (id: string, by: string, reason: string) => post<Execution>(`/remediation/executions/${id}/rollback`, { by, reason }),
  policy: (environmentId: Env) => get<{ environmentStatus: string; globalKillSwitch: boolean; settings: RemediationSettings;
    enabledExecutors: string[] }>('/remediation/policy', { environmentId }),
  setAutonomy: (environmentId: Env, body: { autoExecuteMaxTier?: number; killSwitch?: boolean; dryRun?: boolean; changedBy: string }) =>
    put('/remediation/autonomy', body, { environmentId }),
  audit: (environmentId: Env, incidentId?: string) => get<AuditEntry[]>('/remediation/audit', { environmentId, incidentId }),
  outcomes: (environmentId: Env) => get<OutcomeMetrics>('/remediation/metrics', { environmentId }),

  faults: (environmentId: Env) => get<Fault[]>('/faults', { environmentId }),
  injectFault: (environmentId: Env, body: { type: string; target: string; severity: string; durationSeconds: number;
    parameters: Record<string, unknown> }) => post<Fault>('/faults', body, { environmentId }),
  stopFault: (id: string) => post(`/faults/${id}/stop`),
  clearFaults: (environmentId: Env) => post('/faults/clear', undefined, { environmentId }),

  changes: (environmentId: Env) => get<ChangeEvent[]>('/changes', { environmentId }),
  recordChange: (body: { environmentId?: string; kind: string; target?: string; startedAt?: string; endedAt?: string;
    source: string; description: string }) => post('/changes', body),

  logs: (environmentId: Env, params: { service?: string; contains?: string; traceId?: string; minutes?: number; limit?: number }) =>
    get<LogLine[]>('/logs', { environmentId, ...params }),
  traces: (environmentId: Env, params: { service?: string; minDurationMs?: number; minutes?: number; limit?: number }) =>
    get<TraceSummary[]>('/traces', { environmentId, ...params }),
  trace: (traceId: string) => get<Record<string, unknown>>(`/traces/${traceId}`),
};
