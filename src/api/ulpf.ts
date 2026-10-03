import { get, post, put, request } from './client';

// ── shapes returned by the ULPF service (through /api/ulpf) ──────────────────
export interface UlpfStats {
  received: number; receivedByTransport: Record<string, number>; stored: number; normalized: number; partial: number;
  quarantined: number; losslessVerified: number; bytes: number; sources: number; backlog: number;
  dropped: Record<string, number>; eventsPerSecond: number;
  conservation: { received: number; stored: number; inFlight: number; unaccounted: number; balanced: boolean };
  workers: { name: string; lastHeartbeatSecondsAgo: number; alive: boolean; processed: number }[];
}

export interface LogSource {
  id: string; host: string | null; vendor: string | null; product: string | null; format: string;
  first_seen: string; last_seen: string; last_peer: string | null; transport: string | null; events: number;
  normalized: number; partial: number; quarantined: number; lossless_ok: number; bytes: number;
  silent_seconds: number; normalizedPct: number; losslessPct: number; fillPct: number | null; pack_id: string | null;
}

export interface UlpfEventRow {
  uid: string; source_id: string; received_at: string; event_time: string; class_uid: number; class_name: string;
  status: 'NORMALIZED' | 'PARTIAL' | 'QUARANTINED'; format: string; product: string | null; src_ip: string | null;
  dst_ip: string | null; user_name: string | null; action: string | null; message: string | null; lossless: boolean;
  parser: string;
}

export interface UlpfEventDetail {
  uid: string; sourceId: string; receivedAt: string; raw: string; rawBase64: string;
  vault: { segment: string; offset: number; sha256: string; chain: string };
  event: Record<string, unknown> & { ulpf: { fields: Record<string, string>; skeleton: (string | [string])[]; [k: string]: unknown } };
}

export interface LosslessProof {
  uid: string; sha256: string; rebuiltSha256: string | null; lossless: boolean;
  checks: { vaultHashMatches: boolean; vaultRecordIsThisEvent: boolean; rebuiltFromNormalizedMatches: boolean };
}

export interface VaultVerify {
  ok: boolean; records: number; seconds: number;
  writers: { writer: string; segments: number; records: number; ok: boolean; errors: string[] }[];
}

export interface ParseResult {
  format: string; status: string; lossless: boolean;
  fields: { name: string; value: string; start: number; end: number }[];
  event: Record<string, unknown>;
}

export interface EventQuery {
  source?: string; classUid?: number; status?: string; ip?: string; user?: string; q?: string; minutes?: number; limit?: number;
}

export const ulpf = {
  stats: () => get<UlpfStats>('/ulpf/stats'),
  sources: () => get<LogSource[]>('/ulpf/sources'),
  events: (q: EventQuery) => get<UlpfEventRow[]>('/ulpf/events', { ...q }),
  event: (uid: string) => get<UlpfEventDetail>(`/ulpf/events/${encodeURIComponent(uid)}`),
  verify: (uid: string) => post<LosslessProof>(`/ulpf/events/${encodeURIComponent(uid)}/verify`),
  verifyVault: () => post<VaultVerify>('/ulpf/vault/verify'),
  parse: (raw: string, source?: string) => post<ParseResult>('/ulpf/parse', { raw, source }),
};

export const OCSF_CLASSES: Record<number, string> = {
  0: 'Base Event', 2004: 'Detection Finding', 3002: 'Authentication', 4001: 'Network Activity',
  4002: 'HTTP Activity', 4003: 'DNS Activity', 4004: 'DHCP Activity',
};

// ── packs, studio, entities, onboarding ──────────────────────────────────────
export interface PackRow {
  id: string; version: number; status: 'CHAMPION' | 'SHADOW' | 'PROPOSED' | 'RETIRED'; vendor: string | null;
  product: string | null; origin: string; created_at: string; created_by: string | null; notes: string | null;
  test: TestSummary | null; bound_sources: number;
}
export interface PackDetail extends PackRow { yaml: string }
export interface TestSummary { normalizedPct: number; fillPct: number; losslessPct: number; matchPct: number }
export interface AnalyzeResult {
  bodyFormat: string;
  samples: { sample: string; format: string; pack: string | null; status: string; fill: number | null; lossless: boolean; classUid: number }[];
  proposal: { kind: 'drain' | 'auto-map'; yaml: string; templates: { template: string; count: number; pattern: string; fields: string[] }[] };
}
export interface TestResult {
  pack: string; summary: TestSummary; champion: TestSummary | null;
  details: { sample: string; status: string; fill: number | null; lossless: boolean; classUid: number;
    fields: { name: string; value: string }[]; ocsf: Record<string, unknown> }[];
}
export interface EntityRow {
  key: string; kind: 'ip' | 'host' | 'mac' | 'user'; value: string; country: string | null; is_private: boolean | null;
  events: number; source_count: number; sources: string[]; first_seen: string; last_seen: string;
}
export interface EntityLink { a: string; b: string; kind: string; events: number; sources: string[]; last_seen: string }
export interface EntityDetail {
  entity: EntityRow; asset: string[]; nodes: { key: string; kind: string; value: string; country: string | null; events: number; sources: string[] }[];
  links: EntityLink[]; events: (UlpfEventRow & { product: string | null })[]; eventsBySourceLastHour: { source_id: string; n: number }[];
}
export interface Onboarding {
  syslogUdpPort: number; syslogTcpPort: number; syslogTlsPort: number; tlsCertificate: string | null;
  tlsFingerprintSha256: string | null; httpIngestPath: string; geoip: string;
}

export const ulpf2 = {
  packs: () => get<PackRow[]>('/ulpf/packs'),
  pack: (id: string, version: number) => get<PackDetail>(`/ulpf/packs/${encodeURIComponent(id)}/${version}`),
  savePack: (body: { yaml: string; activate: boolean; bindSource?: string; samples?: string[]; notes?: string; origin?: string }) =>
    post<{ id: string; version: number; status: string; test: TestSummary | null }>('/ulpf/packs', body),
  activate: (id: string, version: number) => post(`/ulpf/packs/${encodeURIComponent(id)}/${version}/activate`),
  samples: (source: string, limit = 100) => get<{ source: string; samples: string[] }>('/ulpf/studio/samples', { source, limit }),
  analyze: (samples: string[], source?: string) => post<AnalyzeResult>('/ulpf/studio/analyze', { samples, source }),
  test: (yaml: string, samples: string[]) => post<TestResult>('/ulpf/studio/test', { yaml, samples }),
  entities: (q?: string, kind?: string) => get<EntityRow[]>('/ulpf/entities', { q, kind, limit: 200 }),
  entity: (key: string, minutes?: number) => get<EntityDetail>('/ulpf/entity', { key, minutes: minutes ?? 0 }),
  onboarding: () => get<Onboarding>('/ulpf/onboarding'),
};

// ── quality, incidents, re-normalization, scenarios, settings (U3) ───────────
export interface QualityBucket { bucket: string; events: number; normalized: number; partial: number; quarantined: number; lossless_ok: number; fill_avg: number | null; skew_ms: number | null; pack: string | null }
export interface BaselineRow { metric: string; median: number; scale: number; threshold: number; samples: number; fittedAt?: string; fitted_at?: string }
export interface QualitySource {
  id: string; pack_id: string | null; skew_ms: number | null; last_seen: string; silent_seconds: number; buckets: number;
  baselines: BaselineRow[] | null; latest: { events: number; fill: number | null; normalized: number; skewMs: number | null; bucket: string } | null;
  open_incidents: number; learningProgress: number; state: 'LEARNING' | 'WATCHED';
}
export interface UlpfSettings { autoExecuteMaxTier: number; dryRun: boolean; learningMinutes: number; bucketSeconds: number; consecutiveBuckets: number; maxFalsePositiveRate: number; zFloor: number; promotionCooldownMinutes: number; skewThresholdSeconds: number }
export interface UlpfIncident {
  id: string; incident_key: string; kind: 'PARSER_DRIFT' | 'SOURCE_SILENT' | 'CLOCK_SKEW' | 'PIPELINE' | 'SECURITY_CORRELATION';
  source_id: string | null; severity: string; status: string; title: string; summary: string; onset_at: string | null; detected_at: string;
  mitigated_at: string | null; resolved_at: string | null; mttd_seconds: number | null; mttr_seconds: number | null; evidence?: Record<string, unknown>;
}
export interface PolicyRule { rule: string; kind: 'safety' | 'autonomy'; passed: boolean; detail: string }
export interface UlpfAction {
  id: string; action: string; tier: number; status: string; automatic: boolean; params: Record<string, unknown>; policy: PolicyRule[];
  result: Record<string, unknown> | null; created_at: string; executed_at: string | null; finished_at: string | null; decided_by: string | null;
}
export interface RenormJob {
  id: string; source_id: string; incident_id: string | null; window_from: string | null; window_to: string | null; only_degraded: boolean;
  status: string; requested_by: string; processed: number; changed: number; improved: number; fields_recovered: number; lossless_ok: number;
  error: string | null; created_at: string; started_at: string | null; finished_at: string | null;
}
export interface IncidentDetailU { incident: UlpfIncident; timeline: { at: string; type: string; payload: Record<string, unknown> }[]; actions: UlpfAction[]; jobs: RenormJob[] }
export interface EventVersion { revision: number; parser: string; status: string; fill: number | null; event: Record<string, unknown>; replaced_at: string; job_id: string | null }
export interface Scenarios { devices: string[]; kinds: Record<string, string>; active: Record<string, Record<string, unknown> & { remainingSeconds: number }> }

export const ulpf3 = {
  overview: () => get<{ settings: UlpfSettings; sources: QualitySource[] }>('/ulpf/quality/overview'),
  quality: (source: string, minutes = 30) => get<{ source: string; buckets: QualityBucket[]; baselines: Record<string, BaselineRow> }>('/ulpf/quality', { source, minutes }),
  incidents: (status?: 'active' | 'resolved') => get<UlpfIncident[]>('/ulpf/incidents', { status }),
  incident: (id: string) => get<IncidentDetailU>(`/ulpf/incidents/${encodeURIComponent(id)}`),
  decide: (actionId: string, decision: 'approve' | 'reject') => post(`/ulpf/actions/${actionId}/${decision}`),
  renormalize: (source: string, onlyDegraded = true) => post<{ id: string }>('/ulpf/renormalize', { source, onlyDegraded }),
  jobs: () => get<RenormJob[]>('/ulpf/jobs'),
  versions: (uid: string) => get<EventVersion[]>(`/ulpf/events/${encodeURIComponent(uid)}/versions`),
  scenarios: () => get<Scenarios>('/ulpf/scenarios'),
  inject: (device: string, kind: string, value: unknown, durationSeconds: number) => post('/ulpf/scenarios', { device, kind, value, durationSeconds }),
  clear: (device: string) => request('DELETE', `/ulpf/scenarios/${encodeURIComponent(device)}`),
  settings: () => get<UlpfSettings>('/ulpf/settings'),
  saveSettings: (s: Partial<UlpfSettings>) => put<UlpfSettings>('/ulpf/settings', s),
};

// ── outputs, detections, privacy, bundles, compliance, benchmarks (U4) ───────
export interface SinkRow {
  name: string; kind: string; tier: 'lake' | 'siem' | 'all'; enabled: boolean; tokenize: boolean; config: Record<string, unknown>;
  cursor_seq: number; exported: number; last_ok: string | null; last_error: string | null; last_error_at: string | null;
}
export interface CostReport {
  ratePerGbInr: number; eventsIn: number; eventsOut: number; fullBytes: number; siemBytes: number; reductionPct: number | null;
  measuredSeconds: number; fullGbPerDay?: number; siemGbPerDay?: number; savedInrPerMonth?: number;
  byDay: { sink: string; day: string; events_in: number; events_out: number; bytes: number; full_bytes: number; errors: number }[];
  lake: { exported: number };
}
export interface SigmaRule { id: string; title: string; level: string; tags: string[]; enabled: boolean; yaml: string; detections: number; last_hit: string | null }
export interface SigmaHit {
  id: string; rule_id: string; rule_title: string; level: string; group_key: string; count: number; distinct_count: number | null;
  sources: string[]; vendors: string[]; sample_uids: string[]; first_seen: string; last_seen: string;
}
export interface PrivacyPreview { uid: string; original: Record<string, unknown>; tokenized: Record<string, unknown>; changed: string[] }
export interface BundleRow {
  id: string; direction: 'EXPORT' | 'IMPORT'; purpose: string; file: string | null; bytes: number | null; events: number | null;
  sha256: string | null; key_id: string | null; verified: boolean | null; detail: Record<string, unknown>; created_by: string; created_at: string;
}
export interface BundleVerify { ok: boolean; checks: { check: string; passed: boolean }[]; problems: string[]; manifest?: Record<string, unknown>; ingested?: number }
export interface ComplianceCheck { id: string; title: string; direction: string; passed: boolean; measured: string; detail: string; sources: { source: string; passed: boolean; value: string }[] }
export interface ComplianceReport { framework: string; at: string; passed: number; failed: number; checks: ComplianceCheck[] }
export interface Benchmark { id: number; at: string; kind: string; result: Record<string, unknown> }

export const ulpf4 = {
  sinks: () => get<SinkRow[]>('/ulpf/sinks'),
  updateSink: (name: string, body: Partial<SinkRow>) => put<SinkRow[]>(`/ulpf/sinks/${encodeURIComponent(name)}`, body),
  cost: () => get<CostReport>('/ulpf/cost'),
  rules: () => get<SigmaRule[]>('/ulpf/sigma/rules'),
  hits: (rule?: string) => get<SigmaHit[]>('/ulpf/sigma/hits', { rule, limit: 200 }),
  preview: (uid: string) => get<PrivacyPreview>(`/ulpf/privacy/preview/${encodeURIComponent(uid)}`),
  detokenize: (token: string, reason: string) => post<{ token: string; kind: string; value: string }>('/ulpf/privacy/detokenize', { token, reason }),
  privacyAudit: () => get<{ id: number; at: string; actor: string; token: string; reason: string; granted: boolean }[]>('/ulpf/privacy/audit'),
  bundles: () => get<BundleRow[]>('/ulpf/bundles'),
  createBundle: (body: { minutes?: number; source?: string; purpose?: string; incident?: unknown }) =>
    post<BundleRow & { seconds: number; manifest: Record<string, unknown> }>('/ulpf/bundles', body),
  tamperTest: (id: string) => post<BundleVerify>(`/ulpf/bundles/${id}/tamper-test`),
  importBundle: async (file: File, ingest: boolean): Promise<BundleVerify> => {
    const res = await fetch(`/api/ulpf/bundles/import?ingest=${ingest}`, { method: 'POST', body: file, headers: { 'Content-Type': 'application/gzip' } });
    return res.json();
  },
  keys: () => get<{ signer: string; publicKey: string; trusted: string[] }>('/ulpf/keys'),
  compliance: (refresh = false) => get<ComplianceReport>('/ulpf/compliance', { refresh }),
  benchmarks: () => get<Benchmark[]>('/ulpf/benchmarks'),
  runBenchmark: (seconds = 10) => post<Record<string, unknown>>(`/ulpf/benchmarks/processing?seconds=${seconds}`),
};
