/**
 * A small, deterministic force-directed layout (Fruchterman–Reingold) for the knowledge graph (JAN-334).
 * Dependency-free on purpose: the graph is capped at a few hundred nodes, so an O(n²) pass per iteration
 * is cheap, and the same input always yields the same picture (no random seed), which keeps stories and
 * screenshots stable.
 */

export interface LayoutPoint {
  x: number;
  y: number;
}

export interface LayoutOptions {
  width: number;
  height: number;
  /** Space kept free along every border, for node labels. */
  padding?: number;
  iterations?: number;
}

/** Positions for `nodeIds` (by id) inside `width` × `height`; edges with unknown ends are ignored. */
export function layoutGraph(
  nodeIds: string[],
  edges: { source: string; target: string }[],
  { width, height, padding = 40, iterations = 250 }: LayoutOptions,
): Map<string, LayoutPoint> {
  const count = nodeIds.length;
  const positions = new Map<string, LayoutPoint>();
  if (count === 0) return positions;
  const centerX = width / 2;
  const centerY = height / 2;
  if (count === 1) {
    positions.set(nodeIds[0], { x: centerX, y: centerY });
    return positions;
  }
  const index = new Map(nodeIds.map((id, position) => [id, position]));
  const links = edges
    .map((edge) => [index.get(edge.source), index.get(edge.target)] as const)
    .filter((pair): pair is readonly [number, number] => pair[0] !== undefined && pair[1] !== undefined && pair[0] !== pair[1]);
  const innerWidth = Math.max(width - 2 * padding, 1);
  const innerHeight = Math.max(height - 2 * padding, 1);
  const k = Math.sqrt((innerWidth * innerHeight) / count) * 0.8;
  // Start on a golden-angle spiral: spread out and deterministic.
  const xs = new Float64Array(count);
  const ys = new Float64Array(count);
  for (let i = 0; i < count; i += 1) {
    const radius = Math.sqrt((i + 0.5) / count) * Math.min(innerWidth, innerHeight) * 0.45;
    const angle = i * 2.399963229728653;
    xs[i] = centerX + radius * Math.cos(angle);
    ys[i] = centerY + radius * Math.sin(angle);
  }
  const dx = new Float64Array(count);
  const dy = new Float64Array(count);
  let temperature = Math.min(innerWidth, innerHeight) / 8;
  const cooling = temperature / (iterations + 1);
  for (let step = 0; step < iterations; step += 1) {
    dx.fill(0);
    dy.fill(0);
    for (let i = 0; i < count; i += 1) {
      for (let j = i + 1; j < count; j += 1) {
        const ox = xs[i] - xs[j] || 0.01;
        const oy = ys[i] - ys[j] || 0.01;
        const distance = Math.max(Math.hypot(ox, oy), 0.01);
        const force = (k * k) / distance;
        dx[i] += (ox / distance) * force;
        dy[i] += (oy / distance) * force;
        dx[j] -= (ox / distance) * force;
        dy[j] -= (oy / distance) * force;
      }
    }
    for (const [a, b] of links) {
      const ox = xs[a] - xs[b];
      const oy = ys[a] - ys[b];
      const distance = Math.max(Math.hypot(ox, oy), 0.01);
      const force = (distance * distance) / k;
      dx[a] -= (ox / distance) * force;
      dy[a] -= (oy / distance) * force;
      dx[b] += (ox / distance) * force;
      dy[b] += (oy / distance) * force;
    }
    for (let i = 0; i < count; i += 1) {
      // A light pull to the center keeps disconnected nodes on screen.
      dx[i] += (centerX - xs[i]) * 0.05;
      dy[i] += (centerY - ys[i]) * 0.05;
      const length = Math.max(Math.hypot(dx[i], dy[i]), 0.01);
      const move = Math.min(length, temperature);
      xs[i] = Math.min(width - padding, Math.max(padding, xs[i] + (dx[i] / length) * move));
      ys[i] = Math.min(height - padding, Math.max(padding, ys[i] + (dy[i] / length) * move));
    }
    temperature = Math.max(temperature - cooling, 0.5);
  }
  nodeIds.forEach((id, i) => positions.set(id, { x: xs[i], y: ys[i] }));
  return positions;
}
