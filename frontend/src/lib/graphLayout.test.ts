import { describe, expect, it } from 'vitest';
import { layoutGraph } from './graphLayout';

const distance = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.hypot(a.x - b.x, a.y - b.y);

describe('layoutGraph', () => {
  it('handles empty and single-node graphs', () => {
    expect(layoutGraph([], [], { width: 100, height: 100 }).size).toBe(0);
    expect(layoutGraph(['a'], [], { width: 100, height: 80 }).get('a')).toEqual({ x: 50, y: 40 });
  });

  it('is deterministic and keeps every node inside the padded box', () => {
    const ids = Array.from({ length: 30 }, (_, i) => `n${i}`);
    const edges = ids.slice(1).map((id, i) => ({ source: ids[i], target: id }));
    const options = { width: 600, height: 400, padding: 30 };
    const first = layoutGraph(ids, edges, options);
    const second = layoutGraph(ids, edges, options);
    expect([...first.entries()]).toEqual([...second.entries()]);
    for (const point of first.values()) {
      expect(point.x).toBeGreaterThanOrEqual(30);
      expect(point.x).toBeLessThanOrEqual(570);
      expect(point.y).toBeGreaterThanOrEqual(30);
      expect(point.y).toBeLessThanOrEqual(370);
      expect(Number.isFinite(point.x) && Number.isFinite(point.y)).toBe(true);
    }
  });

  it('pulls linked nodes closer together than unlinked ones', () => {
    const ids = ['a', 'b', 'c', 'd', 'e', 'f'];
    const edges = [
      { source: 'a', target: 'b' },
      { source: 'c', target: 'd' },
      { source: 'e', target: 'f' },
      { source: 'a', target: 'missing' },
    ];
    const layout = layoutGraph(ids, edges, { width: 800, height: 600 });
    const linked = distance(layout.get('a')!, layout.get('b')!);
    const unlinked = distance(layout.get('a')!, layout.get('d')!);
    expect(linked).toBeLessThan(unlinked);
  });
});
