import React, { useMemo } from 'react';
import type { Edge, Service } from '../api/causalops';
import { layoutGraph } from './topologyLayout';
import { fmtMs, fmtPct } from './ui';

interface Props {
  nodes: Service[];
  edges: Edge[];
  selected?: string | null;
  onSelect?: (name: string) => void;
  rootCause?: string | null;        // node or "client->server" link
  affected?: string[];
  externalNodes?: string[];
}

const W = 900;
const STATUS_FILL: Record<string, string> = {
  healthy: '#2F7D5C', degraded: '#B7791F', critical: '#B83A3A', unknown: '#858C87', stale: '#858C87',
};

/** The discovered service graph with live health; highlights a root cause and the affected services. */
export const TopologyGraph: React.FC<Props> = ({ nodes, edges, selected, onSelect, rootCause, affected = [], externalNodes = [] }) => {
  const visible = nodes.filter((n) => !externalNodes.includes(n.name));
  const names = visible.map((n) => n.name);
  const shownEdges = edges.filter((e) => names.includes(e.source) && names.includes(e.target));
  const layout = useMemo(() => layoutGraph(names, shownEdges, W), [names.join(','), shownEdges.map((e) => e.source + e.target).join(',')]);
  const byName = Object.fromEntries(visible.map((n) => [n.name, n]));
  const rootLink = rootCause?.includes('->') ? rootCause : null;
  const rootNode = rootCause && !rootLink ? rootCause : null;

  if (!visible.length) {
    return <div className="text-[12px] text-[#858C87] py-10 text-center">No services discovered yet. Topology appears as soon as traces arrive.</div>;
  }
  return (
    <svg viewBox={`0 0 ${W} ${layout.height}`} className="w-full" style={{ maxHeight: 520 }}>
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#9AA19C" />
        </marker>
        <marker id="arrow-red" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#B83A3A" />
        </marker>
      </defs>
      {shownEdges.map((e) => {
        const a = layout.nodes[e.source];
        const b = layout.nodes[e.target];
        if (!a || !b) return null;
        const failing = (e.failedRate ?? 0) > 0 && (e.callRate ?? 0) > 0 && (e.failedRate ?? 0) / (e.callRate ?? 1) > 0.05;
        const isRoot = rootLink === `${e.source}->${e.target}`;
        const x1 = a.x + 70, x2 = b.x - 72;
        const mx = (x1 + x2) / 2;
        const color = isRoot ? '#B83A3A' : failing ? '#C2410C' : e.stale ? '#D9DCD8' : '#9AA19C';
        return (
          <g key={`${e.source}->${e.target}`}>
            <path d={`M${x1},${a.y} C${mx},${a.y} ${mx},${b.y} ${x2},${b.y}`} fill="none" stroke={color}
                  strokeWidth={isRoot ? 3 : 1.4} strokeDasharray={e.stale ? '4 3' : undefined}
                  markerEnd={`url(#${isRoot || failing ? 'arrow-red' : 'arrow'})`} />
            <text x={mx} y={(a.y + b.y) / 2 - 5} textAnchor="middle" fontSize={9.5} fill={isRoot ? '#B83A3A' : '#858C87'} fontFamily="JetBrains Mono, monospace">
              {e.callRate !== null ? `${e.callRate.toFixed(1)}/s` : ''}{failing ? ` · ${(((e.failedRate ?? 0) / (e.callRate ?? 1)) * 100).toFixed(0)}% err` : ''}{isRoot ? ' · ROOT CAUSE' : ''}
            </text>
          </g>
        );
      })}
      {names.map((name) => {
        const p = layout.nodes[name];
        const n = byName[name];
        const status = n.stale ? 'stale' : (n.status ?? 'unknown').toLowerCase();
        const isRoot = rootNode === name;
        const isAffected = affected.includes(name);
        const isSel = selected === name;
        return (
          <g key={name} transform={`translate(${p.x - 70},${p.y - 30})`} onClick={() => onSelect?.(name)} style={{ cursor: onSelect ? 'pointer' : 'default' }}>
            <rect width={140} height={60} rx={4} fill="#FFFFFF"
                  stroke={isRoot ? '#B83A3A' : isSel ? '#286B78' : isAffected ? '#B7791F' : '#D9DCD8'}
                  strokeWidth={isRoot || isSel ? 2.5 : isAffected ? 1.8 : 1} />
            <circle cx={11} cy={13} r={4} fill={STATUS_FILL[status] ?? '#858C87'} />
            <text x={20} y={17} fontSize={11.5} fontWeight={600} fill="#171A19">{name.length > 17 ? `${name.slice(0, 16)}…` : name}</text>
            <text x={8} y={33} fontSize={9.5} fill="#858C87" fontFamily="JetBrains Mono, monospace">{n.kind}{n.source === 'manual' ? ' · manual' : ''}</text>
            <text x={8} y={50} fontSize={10} fill="#5E6561" fontFamily="JetBrains Mono, monospace">
              p99 {fmtMs(n.latencyP99)} · {fmtPct(n.errorRate)}
            </text>
            {isRoot && <text x={136} y={12} textAnchor="end" fontSize={8.5} fontWeight={700} fill="#B83A3A">ROOT</text>}
          </g>
        );
      })}
    </svg>
  );
};
