/**
 * Deterministic layout for the `/trace` communication graph.
 *
 * No physics and no randomness: the same request draws the same picture every time, because a
 * diagram that rearranges itself between two renders cannot be pointed at during a demo and cannot
 * be compared between two requests. Every node carries its own `col`/`row` in the payload; this
 * module turns that into geometry and routes the edges around whatever is in the way.
 *
 * Kept out of the component so the routing rules can be read — and tested — on their own.
 */
import type { TraceEdge, TraceNode } from "./platform-preview";

export const NODE_W = 200;
export const NODE_H = 58;
const COL_STRIDE = NODE_W + 92;
const ROW_STRIDE = 104;
const PAD = 20;
/** How far a detour bows out past a node's right edge when a straight line would cut through. */
const DETOUR = 46;
/** Lateral offset so a request and its reply do not draw on top of each other. */
const LANE = 24;

export interface PlacedNode extends TraceNode {
  x: number;
  y: number;
  cx: number;
  cy: number;
}

export interface RoutedEdge extends TraceEdge {
  path: string;
  /** Where the step badge sits. Computed with the path so the two cannot drift apart. */
  mid: { x: number; y: number };
}

export interface TraceLayout {
  nodes: PlacedNode[];
  edges: RoutedEdge[];
  width: number;
  height: number;
}

function place(node: TraceNode): PlacedNode {
  const x = PAD + node.col * COL_STRIDE;
  const y = PAD + node.row * ROW_STRIDE;
  return { ...node, x, y, cx: x + NODE_W / 2, cy: y + NODE_H / 2 };
}

/** Is there a node sitting between these two in the same column? */
function blocked(a: PlacedNode, b: PlacedNode, nodes: PlacedNode[]): boolean {
  const lo = Math.min(a.row, b.row);
  const hi = Math.max(a.row, b.row);
  return nodes.some((n) => n.col === a.col && n.row > lo && n.row < hi);
}

interface Route {
  path: string;
  mid: { x: number; y: number };
}

function route(a: PlacedNode, b: PlacedNode, nodes: PlacedNode[], lane: number): Route {
  // A message a participant sends to itself — the policy screen, the budget check. Drawn as a loop
  // on the right so it reads as work done inside that box rather than a link to somewhere else.
  if (a.id === b.id) {
    const reach = 52 + lane * 16;
    const top = a.y + 12;
    const bot = a.y + NODE_H - 12;
    const out = a.x + NODE_W;
    return {
      path: `M ${out} ${top} C ${out + reach} ${top - 14}, ${out + reach} ${bot + 14}, ${out} ${bot}`,
      mid: { x: out + reach * 0.75, y: a.cy },
    };
  }

  const down = b.row > a.row;

  if (a.col === b.col) {
    if (blocked(a, b, nodes)) {
      // Something is in the way, so go around the outside of the column rather than through it.
      const track = a.x + NODE_W + DETOUR + lane * 14;
      const from = { x: a.x + NODE_W, y: a.cy };
      const to = { x: b.x + NODE_W, y: b.cy };
      return {
        path: `M ${from.x} ${from.y} H ${track} V ${to.y} H ${to.x}`,
        mid: { x: track, y: (from.y + to.y) / 2 },
      };
    }
    // Adjacent in the same column: a straight line, nudged sideways so the reply does not sit on
    // top of the request it answers.
    const off = (down ? -1 : 1) * (LANE / 2 + lane * 13);
    const x = a.cx + off;
    const y1 = down ? a.y + NODE_H : a.y;
    const y2 = down ? b.y : b.y + NODE_H;
    return { path: `M ${x} ${y1} L ${x} ${y2}`, mid: { x, y: (y1 + y2) / 2 } };
  }

  // Across columns: leave the bottom edge going down, the top edge coming back up, with the two
  // directions on opposite lanes.
  const off = (down ? -1 : 1) * (LANE + lane * 15);
  const x1 = a.cx + off;
  const y1 = down ? a.y + NODE_H : a.y;
  const x2 = b.cx + off;
  const y2 = down ? b.y : b.y + NODE_H;
  // Parallel curves get progressively deeper bows as well as separate anchors, so two arrows
  // between the same pair never trace the same line.
  const bow = (down ? 1 : -1) * (46 + lane * 22);
  return {
    path: `M ${x1} ${y1} C ${x1} ${y1 + bow}, ${x2} ${y2 - bow}, ${x2} ${y2}`,
    mid: { x: (x1 + x2) / 2, y: (y1 + y2) / 2 },
  };
}

export function layoutTrace(nodes: TraceNode[], edges: TraceEdge[]): TraceLayout {
  const placed = nodes.map(place);
  const byId = new Map(placed.map((n) => [n.id, n]));

  // Several messages can flow between the same two participants — three CapabilityRequests to one
  // adapter, say. Without a lane each would be drawn on top of the last and the diagram would
  // under-report the traffic it exists to show.
  const lanes = new Map<string, number>();
  const routed: RoutedEdge[] = [];
  for (const e of edges) {
    const a = byId.get(e.from);
    const b = byId.get(e.to);
    // An edge naming a participant the payload did not send is dropped, not drawn to nowhere.
    if (!a || !b) continue;
    const key = `${e.from}>${e.to}`;
    const lane = lanes.get(key) ?? 0;
    lanes.set(key, lane + 1);
    routed.push({ ...e, ...route(a, b, placed, lane) });
  }

  const maxCol = placed.reduce((m, n) => Math.max(m, n.col), 0);
  const maxRow = placed.reduce((m, n) => Math.max(m, n.row), 0);
  return {
    nodes: placed,
    edges: routed,
    // The extra right margin is the self-loop, the detour lane, and the message label that sits
    // under a self-loop's badge — without it a "POLICY screen" caption is clipped by the viewBox.
    width: PAD * 2 + maxCol * COL_STRIDE + NODE_W + 110,
    height: PAD * 2 + maxRow * ROW_STRIDE + NODE_H,
  };
}
