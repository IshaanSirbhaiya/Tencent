// Graph layout + node visual encoding for the knowledge graph (Obsidian redesign 16.5d Part 3).
//
// Layout is d3-force, but run DETERMINISTICALLY to a settled state: initial positions are seeded by
// sorted node index (golden-angle spiral — never Math.random) and the simulation is ticked a fixed
// number of times with .stop(). Same (nodes, edges) → identical picture every render (skill §7.2),
// while still looking organically force-directed (and clustering Engineering / Sales / C-suite /
// Documents). Node colour = node kind, size = connection count — encodings of backend truth, not
// derived values (D-FE-2).
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
import type { BrainEdge, BrainNode } from "./brain-types";
import { onLiveRegistry, type Pack, type PackRegistry } from "./packs";

export interface NodeStyle {
  color: string; // glowing Obsidian palette
  label: string;
  dim?: boolean; // documents glow dimmer
}

// The graph vocabulary is PACK CONFIG, not a constant in this file (`graph_vocabulary` in each
// pack's `ui.yaml`, served by `GET /packs`). HC1 (2026-10-01): it used to be read from the BUNDLED
// `config/packs.json` and never from the live registry, so a deployment's own colours and labels
// could not reach the graph. It is now filled from the live registry when one arrives; until then
// every kind renders in the neutral placeholder style, which is honest about not knowing. One vertical's node kinds compiled into the platform's layout module is the
// exact coupling Phase 1 removes — and it would break the first time two packs disagreed about
// what a kind is called.
//
// Muted, not neon. The first palette was full-saturation tailwind-500s each with a coloured glow
// behind it, which on a near-black canvas reads as a nightclub rather than a tool — and at 70 nodes
// the glows bleed into each other until you cannot see the edges. The palette lives in config with
// the labels because a colour that does not survive a pack switch is not an encoding of anything.

/** Every pack's node styles, merged. Used where the caller has a kind but no pack in hand.
 *  Filled IN PLACE from the live registry, so existing imports see the update. */
export const NODE_STYLE: Record<string, NodeStyle> = {};

function refill<T>(
  target: Record<string, T>,
  packs: readonly Pack[],
  pick: (p: Pack) => Record<string, T> | undefined,
): void {
  for (const key of Object.keys(target)) delete target[key];
  for (const pack of packs) Object.assign(target, pick(pack) ?? {});
}

function applyRegistry(registry: PackRegistry): void {
  refill(NODE_STYLE, registry.packs, (p) => p.graph_vocabulary?.node_kinds);
  refill(EDGE_LABEL, registry.packs, (p) => p.graph_vocabulary?.edge_labels);
}

/** One pack's node styles, in declaration order — what a legend should render. */
export function nodeStylesFor(pack: Pack): Record<string, NodeStyle> {
  return pack.graph_vocabulary?.node_kinds ?? {};
}

const UNKNOWN_STYLE: NodeStyle = { color: "#7d8492", label: "Unknown", dim: true };

/** Style for a node kind, degrading to a neutral placeholder rather than throwing.
 *  A kind missing here used to dereference `undefined` and take the whole /brain page down on
 *  first render; the frontend is deployed separately from the backend and must not white-screen
 *  when it is a version — or a pack — behind. */
export function nodeStyle(kind: string): NodeStyle {
  return NODE_STYLE[kind] ?? UNKNOWN_STYLE;
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

// Every relationship in every pack vocabulary is directional, so an arrowhead is the DEFAULT and
// this set names the exceptions.
//
// This used to be an allow-list of relationship kinds. After a vocabulary rename it matched
// nothing, so every edge rendered undirected and the graph became a hairball you could not read a
// direction out of. An allow-list is the wrong shape here: adding a kind to a pack would silently
// make it undirected. A deny-list fails the other way, which is the safe way.
const UNDIRECTED_KINDS = new Set<string>([]);

export function hasArrow(kind: string): boolean {
  return !UNDIRECTED_KINDS.has(kind);
}

/** Human-readable edge label. The raw kind (`includes_test`) is a wire format, not a caption.
 *  Sourced from pack config for the same reason the node palette is. */
export const EDGE_LABEL: Record<string, string> = {};

onLiveRegistry(applyRegistry);

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
