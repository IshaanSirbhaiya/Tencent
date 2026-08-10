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

export const NODE_STYLE: Record<NodeKind, NodeStyle> = {
  person: { color: "#4a9eff", label: "Person" },
  org: { color: "#a855f7", label: "Org" },
  decision: { color: "#f59e0b", label: "Decision" },
  project: { color: "#10b981", label: "Project" },
  fact: { color: "#ef4444", label: "Fact" },
  document: { color: "#6b7280", label: "Document", dim: true },
};

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

// edges into these relationship kinds get an arrowhead (Obsidian draws most edges undirected).
export const ARROW_KINDS = new Set(["decided", "blocks"]);

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
