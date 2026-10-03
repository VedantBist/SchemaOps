/**
 * Layered layout for a discovered service graph: callers to the left, callees to the right.
 * Layer = longest call path from an entry node (cycles are cut), order within a layer by the
 * mean position of each node's callers (one barycenter pass) to reduce edge crossings.
 */
export interface LayoutNode { name: string; layer: number; order: number; x: number; y: number }

export function layoutGraph(nodes: string[], edges: { source: string; target: string }[],
                            width = 900, rowHeight = 92): { nodes: Record<string, LayoutNode>; height: number; layers: number } {
  const names = [...new Set(nodes)].sort();
  const known = new Set(names);
  const out = new Map<string, string[]>();
  const indeg = new Map<string, number>(names.map((n) => [n, 0]));
  for (const e of edges) {
    if (!known.has(e.source) || !known.has(e.target) || e.source === e.target) continue;
    out.set(e.source, [...(out.get(e.source) ?? []), e.target]);
    indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1);
  }
  // Longest-path layering with a guard against cycles (each node relaxed at most |V| times).
  const layer = new Map<string, number>(names.map((n) => [n, 0]));
  const roots = names.filter((n) => (indeg.get(n) ?? 0) === 0);
  const queue = roots.length ? [...roots] : names.slice(0, 1);
  const visits = new Map<string, number>();
  while (queue.length) {
    const n = queue.shift()!;
    const v = (visits.get(n) ?? 0) + 1;
    visits.set(n, v);
    if (v > names.length) continue;
    for (const m of out.get(n) ?? []) {
      if ((layer.get(m) ?? 0) < (layer.get(n) ?? 0) + 1 && (layer.get(n) ?? 0) + 1 < names.length) {
        layer.set(m, (layer.get(n) ?? 0) + 1);
        queue.push(m);
      }
    }
  }
  const layers = Math.max(0, ...layer.values()) + 1;
  const byLayer: string[][] = Array.from({ length: layers }, () => []);
  names.forEach((n) => byLayer[layer.get(n) ?? 0].push(n));

  const pos = new Map<string, number>();
  byLayer.forEach((group, li) => {
    if (li > 0) {
      const callers = (n: string) => edges.filter((e) => e.target === n && pos.has(e.source)).map((e) => pos.get(e.source)!);
      group.sort((a, b) => {
        const ca = callers(a), cb = callers(b);
        const ma = ca.length ? ca.reduce((s, x) => s + x, 0) / ca.length : Infinity;
        const mb = cb.length ? cb.reduce((s, x) => s + x, 0) / cb.length : Infinity;
        return ma - mb || a.localeCompare(b);
      });
    }
    group.forEach((n, i) => pos.set(n, i));
  });

  const maxRows = Math.max(1, ...byLayer.map((g) => g.length));
  const height = maxRows * rowHeight + 20;
  const colW = layers > 1 ? (width - 180) / (layers - 1) : 0;
  const result: Record<string, LayoutNode> = {};
  byLayer.forEach((group, li) => {
    const offset = (height - group.length * rowHeight) / 2;
    group.forEach((n, i) => {
      result[n] = { name: n, layer: li, order: i, x: 90 + li * colW, y: offset + i * rowHeight + rowHeight / 2 };
    });
  });
  return { nodes: result, height, layers };
}
