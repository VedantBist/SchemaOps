import { get, post } from './client';

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
  entity: (key: string) => get<EntityDetail>('/ulpf/entity', { key }),
  onboarding: () => get<Onboarding>('/ulpf/onboarding'),
};
