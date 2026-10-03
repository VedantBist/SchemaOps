import React from 'react';
import { ApiError } from '../api/client';

// ── formatting ───────────────────────────────────────────────────────────────
export const fmtMs = (v: number | null | undefined) =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${v.toFixed(v < 10 ? 1 : 0)} ms`;
export const fmtPct = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : `${v.toFixed(digits)}%`;
export const fmtRate = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${v.toFixed(2)}/s`);
export const fmtNum = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : v.toFixed(digits);
export const fmtSeconds = (s: number | null | undefined) => {
  if (s === null || s === undefined) return '—';
  if (s < 60) return `${s.toFixed(0)} s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m} min ${Math.round(s % 60)} s` : `${Math.floor(m / 60)} h ${m % 60} min`;
};
export const fmtTime = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleTimeString() : '—');
export const fmtDateTime = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString() : '—');
export const ago = (iso: string | null | undefined) => {
  if (!iso) return '—';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${Math.max(0, Math.round(s))}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};

// ── status colours ───────────────────────────────────────────────────────────
const TONES = {
  good: 'bg-[#2F7D5C]/10 text-[#2F7D5C] border-[#2F7D5C]/30',
  warn: 'bg-[#B7791F]/10 text-[#9A6412] border-[#B7791F]/30',
  bad: 'bg-[#B83A3A]/10 text-[#B83A3A] border-[#B83A3A]/30',
  info: 'bg-[#286B78]/10 text-[#286B78] border-[#286B78]/30',
  muted: 'bg-[#E9EBE7] text-[#5E6561] border-[#D9DCD8]',
};
export type Tone = keyof typeof TONES;

export function toneOf(value: string | null | undefined): Tone {
  const v = (value ?? '').toUpperCase();
  if (['HEALTHY', 'UP', 'ACTIVE', 'RESOLVED', 'VERIFIED', 'PASS', 'FRESH', 'SUCCEEDED', 'MITIGATED', 'LOW', 'CHAMPION', 'APPROVED', 'AUTO_REMEDIATED'].includes(v)) return 'good';
  if (['CRITICAL', 'DOWN', 'FAILED', 'ERROR', 'ROLLBACK_FAILED', 'HIGH', 'BLOCKED', 'MANUAL_INTERVENTION', 'STALE', 'REJECTED'].includes(v)) return 'bad';
  if (['DEGRADED', 'WARN', 'MEDIUM', 'CALIBRATED', 'AWAITING_APPROVAL', 'ROLLED_BACK', 'LEARNING', 'VERIFYING', 'EXECUTING', 'REMEDIATING', 'RUNNING', 'DRY_RUN'].includes(v)) return 'warn';
  if (['DETECTED', 'RCA_IDENTIFIED', 'PROPOSED', 'INFO'].includes(v)) return 'info';
  return 'muted';
}

export const Badge: React.FC<{ children: React.ReactNode; tone?: Tone; title?: string }> = ({ children, tone, title }) => (
  <span title={title} className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-[2px] border font-code text-[10px] font-semibold uppercase tracking-wide whitespace-nowrap ${TONES[tone ?? toneOf(String(children))]}`}>
    {children}
  </span>
);

export const Dot: React.FC<{ tone: Tone; pulse?: boolean }> = ({ tone, pulse }) => {
  const c = { good: 'bg-[#2F7D5C]', warn: 'bg-[#B7791F]', bad: 'bg-[#B83A3A]', info: 'bg-[#286B78]', muted: 'bg-[#858C87]' }[tone];
  return <span className={`inline-block w-1.5 h-1.5 rounded-full ${c} ${pulse ? 'animate-pulse' : ''}`} />;
};

// ── layout ───────────────────────────────────────────────────────────────────
export const Page: React.FC<{ title: string; subtitle?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode }> = ({
  title, subtitle, actions, children,
}) => (
  <div className="p-4 space-y-4 max-w-[1600px] w-full">
    <div className="flex flex-wrap items-end justify-between gap-2">
      <div>
        <h1 className="text-[17px] font-semibold text-[#171A19]">{title}</h1>
        {subtitle && <div className="text-[12px] text-[#5E6561] mt-0.5">{subtitle}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
    {children}
  </div>
);

export const Panel: React.FC<{ title?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; className?: string; dense?: boolean }> = ({
  title, actions, children, className = '', dense,
}) => (
  <section className={`bg-white border border-[#D9DCD8] rounded-[3px] ${className}`}>
    {(title || actions) && (
      <header className="flex items-center justify-between gap-2 px-3 h-9 border-b border-[#E6E8E4]">
        <div className="font-section text-[10.5px] font-semibold text-[#5E6561]">{title}</div>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </header>
    )}
    <div className={dense ? '' : 'p-3'}>{children}</div>
  </section>
);

export const Stat: React.FC<{ label: string; value: React.ReactNode; hint?: React.ReactNode; tone?: Tone }> = ({ label, value, hint, tone }) => (
  <div className="bg-white border border-[#D9DCD8] rounded-[3px] px-3 py-2.5">
    <div className="font-section text-[10px] text-[#858C87] font-semibold">{label}</div>
    <div className={`font-metric text-[20px] font-semibold mt-1 ${tone === 'bad' ? 'text-[#B83A3A]' : tone === 'warn' ? 'text-[#9A6412]' : tone === 'good' ? 'text-[#2F7D5C]' : 'text-[#171A19]'}`}>{value}</div>
    {hint && <div className="text-[11px] text-[#5E6561] mt-0.5">{hint}</div>}
  </div>
);

export const Button: React.FC<React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'danger' | 'ghost' }> = ({
  variant = 'secondary', className = '', ...props
}) => {
  const styles = {
    primary: 'bg-[#286B78] text-white border-[#286B78] hover:bg-[#1F5560]',
    secondary: 'bg-white text-[#171A19] border-[#D9DCD8] hover:bg-[#F1F2F0]',
    danger: 'bg-[#B83A3A] text-white border-[#B83A3A] hover:bg-[#9C2F2F]',
    ghost: 'bg-transparent text-[#286B78] border-transparent hover:bg-[#E9EDE9]',
  }[variant];
  return (
    <button
      {...props}
      className={`h-7 px-2.5 rounded-[3px] border text-[12px] font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${styles} ${className}`}
    />
  );
};

export const inputClass = 'h-7 px-2 rounded-[3px] border border-[#D9DCD8] bg-white text-[12px] focus:outline-none focus:border-[#286B78]';

export const Field: React.FC<{ label: string; hint?: string; children: React.ReactNode }> = ({ label, hint, children }) => (
  <label className="block space-y-1">
    <span className="block text-[11px] font-semibold text-[#5E6561]">{label}</span>
    {children}
    {hint && <span className="block text-[10.5px] text-[#858C87]">{hint}</span>}
  </label>
);

// ── states ───────────────────────────────────────────────────────────────────
export const Loading: React.FC<{ label?: string }> = ({ label = 'Loading…' }) => (
  <div className="py-8 text-center text-[12px] text-[#858C87] font-code">{label}</div>
);

export const Empty: React.FC<{ title: string; children?: React.ReactNode }> = ({ title, children }) => (
  <div className="py-8 px-4 text-center">
    <div className="text-[13px] font-semibold text-[#5E6561]">{title}</div>
    {children && <div className="text-[12px] text-[#858C87] mt-1 max-w-[520px] mx-auto">{children}</div>}
  </div>
);

export const ErrorBox: React.FC<{ error: ApiError | Error | null; onRetry?: () => void }> = ({ error, onRetry }) =>
  error ? (
    <div className="flex items-start justify-between gap-3 px-3 py-2 rounded-[3px] border border-[#B83A3A]/40 bg-[#B83A3A]/5 text-[12px] text-[#8E2B2B]">
      <div>
        <span className="font-semibold">{error instanceof ApiError && error.status ? `HTTP ${error.status}: ` : ''}</span>
        {error.message}
      </div>
      {onRetry && <Button onClick={onRetry}>Retry</Button>}
    </div>
  ) : null;

/** Renders loading / error / empty states around data, so views never invent values. */
export function Async<T>({ state, empty, children }: {
  state: { data: T | null; error: ApiError | null; loading: boolean; reload: () => void };
  empty?: (data: T) => React.ReactNode | null;
  children: (data: T) => React.ReactNode;
}) {
  if (state.error && !state.data) return <ErrorBox error={state.error} onRetry={state.reload} />;
  if (!state.data) return <Loading />;
  const e = empty?.(state.data);
  return (
    <>
      {state.error && <ErrorBox error={state.error} onRetry={state.reload} />}
      {e ?? children(state.data)}
    </>
  );
}

export const Table: React.FC<{ head: React.ReactNode[]; children: React.ReactNode }> = ({ head, children }) => (
  <div className="overflow-x-auto">
    <table className="w-full text-[12px]">
      <thead>
        <tr className="text-left border-b border-[#E6E8E4]">
          {head.map((h, i) => (
            <th key={i} className="px-3 py-2 font-section text-[10px] font-semibold text-[#858C87] whitespace-nowrap">{h}</th>
          ))}
        </tr>
      </thead>
      <tbody className="divide-y divide-[#F0F1EE]">{children}</tbody>
    </table>
  </div>
);

export const Td: React.FC<{ children?: React.ReactNode; className?: string; mono?: boolean }> = ({ children, className = '', mono }) => (
  <td className={`px-3 py-1.5 align-top ${mono ? 'font-code' : ''} ${className}`}>{children}</td>
);

export const KeyValue: React.FC<{ items: [string, React.ReactNode][] }> = ({ items }) => (
  <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-[12px]">
    {items.map(([k, v]) => (
      <React.Fragment key={k}>
        <dt className="text-[#858C87]">{k}</dt>
        <dd className="font-code text-[#171A19] break-all">{v}</dd>
      </React.Fragment>
    ))}
  </dl>
);
