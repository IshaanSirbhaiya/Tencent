// Deterministic force layout for `/graph` — SAME technique, and the SAME tuning constants, as
// `lib/brain-layout.ts` (`computeLayout`): the founder asked for `/graph` to look exactly like the
// earlier `/brain` rebuild, and the layout's felt "aliveness" is as much about these numbers
// (repulsion, link distance, gravity, tick count) as about the code shape. Generalised over plain
// ids/edges instead of `BrainNode`/`BrainEdge` so this workstream owns its own copy rather than
// editing a shared lib file another agent is in — but every constant below is copied, not
// re-derived.
//
// Same discipline as the original: seeded by a golden-angle spiral (never `Math.random`), ticked a
// fixed number of times, then frozen — same (nodes, edges) in -> identical picture out, so a
// screenshot is reproducible and expanding a node does not jitter the nodes already on screen.
import {
  forceCenter,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";

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

// Same canvas size and golden-angle seed as `lib/brain-layout.ts`.
export const CANVAS_W = 1200;
export const CANVAS_H = 760;
const GA = Math.PI * (3 - Math.sqrt(5)); // golden angle

/**
 * `pinned` carries positions already assigned in a PRIOR call (e.g. the nodes already on screen
 * before an expand-on-click) so a redraw does not re-seed the whole picture from scratch — only
 * newly-arrived nodes get a fresh spiral position before the simulation relaxes everything.
 */
export function computeGraphLayout(
  nodeIds: string[],
  edges: { source: string; target: string }[],
  pinned?: Map<string, Pos>,
): Map<string, Pos> {
  const ids = [...nodeIds].sort();
  const n = ids.length;
  const pos = new Map<string, Pos>();
  if (n === 0) return pos;

  // A pinned node is FIXED (`fx`/`fy`), not merely seeded: when an expand merges new nodes into
  // the canvas, the nodes already on screen stay exactly where the reader last saw them and only
  // the newcomers relax around them. A merge that moved everything would lose the reader's place.
  // Newcomers start next to a pinned neighbour when they have one, so they settle beside it
  // rather than flying in from the spiral.
  const neighbourOf = new Map<string, string>();
  if (pinned && pinned.size > 0) {
    for (const e of edges) {
      if (pinned.has(e.source) && !pinned.has(e.target) && !neighbourOf.has(e.target))
        neighbourOf.set(e.target, e.source);
      if (pinned.has(e.target) && !pinned.has(e.source) && !neighbourOf.has(e.source))
        neighbourOf.set(e.source, e.target);
    }
  }
  const simNodes: SimNode[] = ids.map((id, i) => {
    const prior = pinned?.get(id);
    if (prior) return { id, x: prior.x, y: prior.y, fx: prior.x, fy: prior.y };
    const anchor = neighbourOf.get(id);
    const ap = anchor ? pinned?.get(anchor) : undefined;
    if (ap) {
      return { id, x: ap.x + 40 * Math.cos(i * GA), y: ap.y + 40 * Math.sin(i * GA) };
    }
    const r = Math.sqrt((i + 0.5) / n) * Math.min(CANVAS_W, CANVAS_H) * 0.45;
    return {
      id,
      x: CANVAS_W / 2 + r * Math.cos(i * GA),
      y: CANVAS_H / 2 + r * Math.sin(i * GA),
    };
  });
  const byId = new Map(simNodes.map((s) => [s.id, s]));
  const links: SimLink[] = edges
    .filter((e) => byId.has(e.source) && byId.has(e.target))
    .map((e) => ({ source: e.source, target: e.target }))
    .sort((a, b) => `${a.source}|${a.target}`.localeCompare(`${b.source}|${b.target}`));

  // Same prompt params as `lib/brain-layout.ts`: repulsion 300, linkDistance 80, gravity 0.05, 300
  // iterations, then freeze.
  const sim = forceSimulation<SimNode>(simNodes)
    .force("charge", forceManyBody<SimNode>().strength(-300))
    .force("link", forceLink<SimNode, SimLink>(links).id((d) => d.id).distance(80))
    .force("center", forceCenter<SimNode>(CANVAS_W / 2, CANVAS_H / 2))
    .force("x", forceX<SimNode>(CANVAS_W / 2).strength(0.05))
    .force("y", forceY<SimNode>(CANVAS_H / 2).strength(0.05))
    .stop();
  for (let i = 0; i < 300; i++) sim.tick();

  for (const s of simNodes) pos.set(s.id, { x: s.x, y: s.y });
  return pos;
}

export function degreeMap(edges: { source: string; target: string }[]): Map<string, number> {
  const deg = new Map<string, number>();
  for (const e of edges) {
    deg.set(e.source, (deg.get(e.source) ?? 0) + 1);
    deg.set(e.target, (deg.get(e.target) ?? 0) + 1);
  }
  return deg;
}

// Obsidian-style small nodes: 8px leaf → grows with connections, capped at 32px (the hubs). Same
// formula as `lib/brain-layout.ts`.
export function nodeRadius(degree: number): number {
  return Math.min(8 + degree * 2.5, 32);
}

// Colour-per-kind lives in `kind-colors.ts` — the live pack registry's colour wins where a pack
// declares a kind; a deterministic index-based hue covers everything no pack declares.
