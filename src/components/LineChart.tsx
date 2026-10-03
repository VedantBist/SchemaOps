import React, { useMemo, useState } from 'react';

export interface Series {
  name: string;
  color: string;
  points: [number, number | null][]; // [epoch ms, value]
  dashed?: boolean;
  band?: { low: (number | null)[]; high: (number | null)[] }; // aligned with points
}

interface Props {
  series: Series[];
  height?: number;
  unit?: string;
  threshold?: { value: number; label: string };
  markers?: { at: number; label: string; color?: string }[];
  yMin?: number;
}

const W = 800;
const PAD = { l: 52, r: 12, t: 10, b: 24 };

/** Multi-series time chart in plain SVG (no chart library), with optional bands, threshold and markers. */
export const LineChart: React.FC<Props> = ({ series, height = 220, unit = '', threshold, markers = [], yMin }) => {
  const [hover, setHover] = useState<number | null>(null);
  const H = height;
  const { x0, x1, y0, y1 } = useMemo(() => {
    const xs: number[] = [];
    const ys: number[] = [];
    series.forEach((s) => s.points.forEach(([t, v], i) => {
      xs.push(t);
      if (v !== null) ys.push(v);
      const lo = s.band?.low[i];
      const hi = s.band?.high[i];
      if (lo !== null && lo !== undefined) ys.push(lo);
      if (hi !== null && hi !== undefined) ys.push(hi);
    }));
    if (threshold) ys.push(threshold.value);
    const lo = yMin ?? Math.min(0, ...ys);
    const hi = Math.max(...ys, lo + 1e-6);
    return { x0: Math.min(...xs), x1: Math.max(...xs), y0: lo, y1: hi + (hi - lo) * 0.08 };
  }, [series, threshold, yMin]);

  if (!series.some((s) => s.points.length > 0)) {
    return <div className="text-[12px] text-[#858C87] py-6 text-center">No samples in this window.</div>;
  }
  const sx = (t: number) => PAD.l + ((t - x0) / Math.max(x1 - x0, 1)) * (W - PAD.l - PAD.r);
  const sy = (v: number) => PAD.t + (1 - (v - y0) / Math.max(y1 - y0, 1e-9)) * (H - PAD.t - PAD.b);
  const path = (pts: [number, number | null][]) => {
    let d = '';
    let pen = false;
    for (const [t, v] of pts) {
      if (v === null || Number.isNaN(v)) { pen = false; continue; }
      d += `${pen ? 'L' : 'M'}${sx(t).toFixed(1)},${sy(v).toFixed(1)} `;
      pen = true;
    }
    return d;
  };
  const band = (s: Series) => {
    if (!s.band) return null;
    const top: string[] = [];
    const bottom: string[] = [];
    s.points.forEach(([t], i) => {
      const lo = s.band!.low[i];
      const hi = s.band!.high[i];
      if (lo === null || hi === null || lo === undefined || hi === undefined) return;
      top.push(`${sx(t).toFixed(1)},${sy(hi).toFixed(1)}`);
      bottom.unshift(`${sx(t).toFixed(1)},${sy(lo).toFixed(1)}`);
    });
    return top.length ? <polygon points={[...top, ...bottom].join(' ')} fill={s.color} opacity={0.12} /> : null;
  };
  const ticks = 4;
  const yTicks = Array.from({ length: ticks + 1 }, (_, i) => y0 + ((y1 - y0) * i) / ticks);
  const xTicks = Array.from({ length: 5 }, (_, i) => x0 + ((x1 - x0) * i) / 4);
  const fmt = (v: number) => (Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(1)}k` : Math.abs(v) >= 10 ? v.toFixed(0) : v.toFixed(2));

  const allTimes = series[0]?.points.map(([t]) => t) ?? [];
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * W;
    const t = x0 + ((x - PAD.l) / (W - PAD.l - PAD.r)) * (x1 - x0);
    let best = 0;
    allTimes.forEach((tt, i) => { if (Math.abs(tt - t) < Math.abs(allTimes[best] - t)) best = i; });
    setHover(allTimes.length ? best : null);
  };

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height }} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={PAD.l} x2={W - PAD.r} y1={sy(v)} y2={sy(v)} stroke="#EEF0EC" />
            <text x={PAD.l - 6} y={sy(v) + 3} textAnchor="end" fontSize={10} fill="#858C87" fontFamily="JetBrains Mono, monospace">{fmt(v)}</text>
          </g>
        ))}
        {xTicks.map((t, i) => (
          <text key={i} x={sx(t)} y={H - 6} textAnchor="middle" fontSize={10} fill="#858C87" fontFamily="JetBrains Mono, monospace">
            {new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
          </text>
        ))}
        {threshold && (
          <g>
            <line x1={PAD.l} x2={W - PAD.r} y1={sy(threshold.value)} y2={sy(threshold.value)} stroke="#B83A3A" strokeDasharray="4 3" />
            <text x={W - PAD.r} y={sy(threshold.value) - 3} textAnchor="end" fontSize={10} fill="#B83A3A">{threshold.label}</text>
          </g>
        )}
        {markers.map((m, i) => (
          <g key={i}>
            <line x1={sx(m.at)} x2={sx(m.at)} y1={PAD.t} y2={H - PAD.b} stroke={m.color ?? '#286B78'} strokeDasharray="2 2" />
            <text x={sx(m.at) + 3} y={PAD.t + 10} fontSize={10} fill={m.color ?? '#286B78'}>{m.label}</text>
          </g>
        ))}
        {series.map((s) => <g key={`b-${s.name}`}>{band(s)}</g>)}
        {series.map((s) => (
          <path key={s.name} d={path(s.points)} fill="none" stroke={s.color} strokeWidth={1.6} strokeDasharray={s.dashed ? '5 3' : undefined} />
        ))}
        {hover !== null && allTimes[hover] !== undefined && (
          <line x1={sx(allTimes[hover])} x2={sx(allTimes[hover])} y1={PAD.t} y2={H - PAD.b} stroke="#858C87" strokeWidth={0.5} />
        )}
      </svg>
      <div className="flex flex-wrap gap-3 mt-1 text-[11px]">
        {series.map((s) => {
          const v = hover !== null ? s.points[hover]?.[1] : s.points[s.points.length - 1]?.[1];
          return (
            <span key={s.name} className="flex items-center gap-1.5">
              <span className="inline-block w-3 h-[2px]" style={{ background: s.color }} />
              <span className="text-[#5E6561]">{s.name}</span>
              <span className="font-code text-[#171A19]">{v === null || v === undefined ? '—' : `${fmt(v)}${unit}`}</span>
            </span>
          );
        })}
        {hover !== null && allTimes[hover] !== undefined && (
          <span className="font-code text-[#858C87] ml-auto">{new Date(allTimes[hover]).toLocaleTimeString()}</span>
        )}
      </div>
    </div>
  );
};
