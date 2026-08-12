// Graph layout + node visual encoding for the Company Brain (T08, Obsidian redesign 16.5d Part 3).
//
// Layout is d3-force, but run DETERMINISTICALLY to a settled state: initial positions are seeded by
// sorted node index (golden-angle spiral — never Math.random) and the simulation is ticked a fixed
// number of times with .stop(). Same (nodes, edges) → identical picture every render (skill §7.2),
// while still looking organically force-directed (and clustering Engineering / Sales / C-suite /
// Customers / Documents). Node colour = EntityType, size = connection count — encodings of backend
// truth, not derived values (D-FE-2).
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
import type { BrainEdge, BrainNode, NodeKind } from "./brain-types";

export interface NodeStyle {
  color: string; // glowing Obsidian palette
  label: string;
  dim?: boolean; // documents glow dimmer
}

// Clinic vocabulary (ADR-0009). Must stay in step with `NodeKind` in databridge_graph/schema.py —
// a kind missing here used to dereference `undefined` and take the whole /brain page down on
// first render, so unknown kinds now fall back through `nodeStyle()` instead of crashing.
// Muted, not neon. The first palette was full-saturation tailwind-500s (#4a9eff, #a855f7, #10b981,
// #ef4444) each with a coloured glow behind it, which on a near-black canvas reads as a nightclub
// rather than a clinic tool — and at 70 nodes the glows bleed into each other until you cannot see
// the edges. These are the same hues pulled toward grey: still distinguishable at 8px, no longer
// competing with the highlight colour that actually means something.
export const NODE_STYLE: Record<NodeKind, NodeStyle> = {
  patient: { color: "#6f9fd8", label: "Patient" },
  org: { color: "#9a86c4", label: "Org" },
  package: { color: "#5fa98d", label: "Package" },
  test: { color: "#c2a163", label: "Test" },
  visit: { color: "#c088a4", label: "Visit" },
  clinic: { color: "#63a3a3", label: "Clinic" },
  coverage_rule: { color: "#c4827a", label: "Coverage rule" },
  document: { color: "#7d8492", label: "Document", dim: true },
};

const UNKNOWN_STYLE: NodeStyle = { color: "#7d8492", label: "Unknown", dim: true };

/** Style for a node kind, degrading to a neutral placeholder rather than throwing.
 *  The graph vocabulary is closed, but the frontend is deployed separately from the backend and
 *  must not white-screen if it is a version behind. */
export function nodeStyle(kind: string): NodeStyle {
  return NODE_STYLE[kind as NodeKind] ?? UNKNOWN_STYLE;
}

export function degreeMap(edges: BrainEdge[]): Map<string, number> {
  const deg = new Map<string, number>();
  for (const e of edges) {
    deg.set(e.source, (deg.get(e.source) ?? 0) + 1);
    deg.set(e.target, (deg.get(e.target) ?? 0) + 1);
  }
  return deg;
}

// Obsidian-style small nodes: 8px leaf → grows with connections, capped at 32px (the hubs).
export function nodeRadius(degree: number): number {
  return Math.min(8 + degree * 2.5, 32);
}

// Every relationship in the clinic vocabulary (ADR-0009) is directional: a coverage rule
// `authorises` a package, a document is `for_patient`, an org `issued` a document. So an arrowhead
// is the DEFAULT, and this set names the exceptions.
//
// This used to be an allow-list reading `{"decided", "blocks"}` — kinds from the retired Acme
// vocabulary. After ADR-0009 renamed everything, it matched nothing, so all 114 edges rendered
// undirected and the graph became a hairball you could not read a direction out of. An allow-list
// of relationship kinds is the wrong shape here: adding a kind to the vocabulary would silently
// make it undirected. A deny-list fails the other way, which is the safe way.
const UNDIRECTED_KINDS = new Set<string>([]);

export function hasArrow(kind: string): boolean {
  return !UNDIRECTED_KINDS.has(kind);
}

/** Human-readable edge label. The raw kind (`includes_test`) is a wire format, not a caption. */
export const EDGE_LABEL: Record<string, string> = {
  includes_test: "includes",
  requests_test: "requests",
  authorises: "authorises",
  for_patient: "for patient",
  issued: "issued",
  references: "references",
  at_clinic: "at clinic",
};

export function edgeLabel(kind: string): string {
  return EDGE_LABEL[kind] ?? kind.replace(/_/g, " ");
}

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

const W = 1200;
const H = 760;
const GA = Math.PI * (3 - Math.sqrt(5)); // golden angle

export function computeLayout(nodes: BrainNode[], edges: BrainEdge[]): Map<string, Pos> {
  const ids = nodes.map((n) => n.id).sort(); // stable order, independent of API ordering
  const n = ids.length;
  const pos = new Map<string, Pos>();
  if (n === 0) return pos;

  // deterministic seed: golden-angle spiral (no RNG → d3 never injects its own random seed)
  const simNodes: SimNode[] = ids.map((id, i) => {
    const r = Math.sqrt((i + 0.5) / n) * Math.min(W, H) * 0.45;
    return { id, x: W / 2 + r * Math.cos(i * GA), y: H / 2 + r * Math.sin(i * GA) };
  });
  const byId = new Map(simNodes.map((s) => [s.id, s]));
  // sort links too (float-add order matters): the ONLY input that wasn't already pinned by id sort.
  const links: SimLink[] = edges
    .filter((e) => byId.has(e.source) && byId.has(e.target))
    .map((e) => ({ source: e.source, target: e.target }))
    .sort((a, b) => `${a.source}|${a.target}`.localeCompare(`${b.source}|${b.target}`));

  // prompt params: repulsion 300, linkDistance 80, gravity 0.05, 300 iterations, then freeze.
  const sim = forceSimulation<SimNode>(simNodes)
    .force("charge", forceManyBody<SimNode>().strength(-300))
    .force("link", forceLink<SimNode, SimLink>(links).id((d) => d.id).distance(80))
    .force("center", forceCenter<SimNode>(W / 2, H / 2))
    .force("x", forceX<SimNode>(W / 2).strength(0.05))
    .force("y", forceY<SimNode>(H / 2).strength(0.05))
    .stop();
  for (let i = 0; i < 300; i++) sim.tick();

  for (const s of simNodes) pos.set(s.id, { x: s.x, y: s.y });
  return pos;
}
