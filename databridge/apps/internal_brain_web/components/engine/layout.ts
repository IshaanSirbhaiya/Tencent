// Deterministic force layout for the engine canvases. Same technique and the same tuning constants
// as the original Walnut screens (apps/frontend/components/graph/layout.ts, lib/brain-layout.ts):
// seeded on a golden-angle spiral over SORTED ids (never Math.random), ticked a bounded number of
// times, then frozen. Same (nodes, edges) in → the same picture out, so screenshots reproduce and
// expanding a node does not move the nodes already on screen (they are pinned with fx/fy).
import {
  forceCenter,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from 'd3-force';

export interface Pos {
  x: number;
  y: number;
}

interface SimNode extends SimulationNodeDatum {
  id: string;
  x: number;
  y: number;
}
type SimLink = SimulationLinkDatum<SimNode>;

export const CANVAS_W = 1200;
export const CANVAS_H = 760;
const GA = Math.PI * (3 - Math.sqrt(5));
const MAX_TICKS = 300;

export function computeLayout(
  nodeIds: string[],
  edges: { source: string; target: string }[],
  pinned?: Map<string, Pos>,
  opts: { charge?: number; distance?: number } = {},
): Map<string, Pos> {
  const ids = [...nodeIds].sort();
  const n = ids.length;
  const pos = new Map<string, Pos>();
  if (n === 0) return pos;

  const neighbourOf = new Map<string, string>();
  if (pinned && pinned.size > 0) {
    for (const e of edges) {
      if (pinned.has(e.source) && !pinned.has(e.target) && !neighbourOf.has(e.target)) neighbourOf.set(e.target, e.source);
      if (pinned.has(e.target) && !pinned.has(e.source) && !neighbourOf.has(e.source)) neighbourOf.set(e.source, e.target);
    }
  }
  const simNodes: SimNode[] = ids.map((id, i) => {
    const prior = pinned?.get(id);
    if (prior) return { id, x: prior.x, y: prior.y, fx: prior.x, fy: prior.y };
    const anchor = neighbourOf.get(id);
    const ap = anchor ? pinned?.get(anchor) : undefined;
    if (ap) return { id, x: ap.x + 40 * Math.cos(i * GA), y: ap.y + 40 * Math.sin(i * GA) };
    const r = Math.sqrt((i + 0.5) / n) * Math.min(CANVAS_W, CANVAS_H) * 0.45;
    return { id, x: CANVAS_W / 2 + r * Math.cos(i * GA), y: CANVAS_H / 2 + r * Math.sin(i * GA) };
  });
  const byId = new Map(simNodes.map((s) => [s.id, s]));
  const links: SimLink[] = edges
    .filter((e) => byId.has(e.source) && byId.has(e.target))
    .map((e) => ({ source: e.source, target: e.target }))
    .sort((a, b) => `${a.source}|${a.target}`.localeCompare(`${b.source}|${b.target}`));

  const sim = forceSimulation<SimNode>(simNodes)
    .force('charge', forceManyBody<SimNode>().strength(opts.charge ?? -300))
    .force('link', forceLink<SimNode, SimLink>(links).id((d) => d.id).distance(opts.distance ?? 80))
    .force('center', forceCenter<SimNode>(CANVAS_W / 2, CANVAS_H / 2))
    .force('x', forceX<SimNode>(CANVAS_W / 2).strength(0.05))
    .force('y', forceY<SimNode>(CANVAS_H / 2).strength(0.05))
    .stop();
  let ticks = 0;
  while (ticks < MAX_TICKS && sim.alpha() >= sim.alphaMin()) {
    sim.tick();
    ticks += 1;
  }
  for (const s of simNodes) pos.set(s.id, { x: s.x, y: s.y });
  return pos;
}

/** Links per node among the edges ON SCREEN; it sizes the dot, it is not shown as a figure. */
export function degreeMap(edges: { source: string; target: string }[]): Map<string, number> {
  const deg = new Map<string, number>();
  for (const e of edges) {
    deg.set(e.source, (deg.get(e.source) ?? 0) + 1);
    deg.set(e.target, (deg.get(e.target) ?? 0) + 1);
  }
  return deg;
}

/** 10 px leaf, growing with connections, capped at 30 px (the original's formula, slightly smaller). */
export function nodeSize(degree: number): number {
  return Math.min(10 + degree * 2, 30);
}
