// Deterministic "packed galaxy" layout. No randomness and no iterative physics, so the same dataset
// always lands on the same coordinates, instantly:
//   1. each place (space / project / channel / drive) is a disc: its documents on a phyllotaxis
//      (sunflower) spiral around the place's hub, sorted by id;
//   2. the discs of one platform are packed around that platform's hub by a greedy outward spiral
//      (largest first, never overlapping);
//   3. the platform groups are packed the same way around the origin, in a fixed platform order;
//   4. people sit at the weighted centre of the places they write in, nudged out of any disc.
// Runs in a Web Worker (layout.worker.ts); the main-thread fallback calls the same function.
// (ForceAtlas2 was measured first: 57 s for 909 nodes in the dev bundle, so it is not used.)
import type { LayoutInputEdge, LayoutInputNode } from './model';

export const LAYOUT_VERSION = 'galaxy-v2';
const ORDER = ['slack', 'jira', 'confluence', 'drive'];
const GOLDEN = Math.PI * (3 - Math.sqrt(5));
const DOC_STEP = 2.1;
const GAP = 6;

export type Positions = Record<string, [number, number]>;

export interface Circle {
  id: string;
  r: number;
  x: number;
  y: number;
}

/** Sunflower slot k of a disc centred on (cx, cy): where its k-th document (sorted by id) sits. */
export const docSlot = (cx: number, cy: number, k: number): [number, number] => {
  const r = DOC_STEP * Math.sqrt(k + 1) + 2.2;
  const a = (k + 1) * GOLDEN;
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
};

/** The radius of a place's disc holding `count` documents. */
export const discRadius = (count: number): number => DOC_STEP * Math.sqrt(count + 1) + 4;

export const LAYOUT_GAP = GAP;

/**
 * The first free spot for a circle of radius `r` on the same outward spiral `pack()` uses (angle
 * step 0.21, distance step 0.45), starting `dist` away from `origin`. Places a NEW disc next to an
 * existing galaxy without moving anything (live growth, docs/finals/live_growth/PLAN.md §5).
 */
export function placeAround(origin: [number, number], dist: number, r: number, obstacles: Circle[], startAngle: number): [number, number] {
  const free = (x: number, y: number) => obstacles.every((c) => Math.hypot(c.x - x, c.y - y) >= c.r + r + GAP);
  for (let k = 0; k < 20000; k++) {
    const a = startAngle + k * 0.21;
    const d = dist + k * 0.45;
    const x = origin[0] + d * Math.cos(a);
    const y = origin[1] + d * Math.sin(a);
    if (free(x, y)) return [x, y];
  }
  return [origin[0] + dist * 3, origin[1]];
}

/** Greedy deterministic packing: biggest first, each on the first free spot of an outward spiral. */
function pack(items: { id: string; r: number }[], hubR: number, startAngle: number): Circle[] {
  const placed: Circle[] = [];
  const sorted = [...items].sort((a, b) => b.r - a.r || (a.id < b.id ? -1 : 1));
  const free = (x: number, y: number, r: number) => (hubR <= 0 || Math.hypot(x, y) >= hubR + r + GAP) && placed.every((c) => Math.hypot(c.x - x, c.y - y) >= c.r + r + GAP);
  sorted.forEach((it, i) => {
    if (i === 0 && hubR <= 0) {
      placed.push({ ...it, x: 0, y: 0 });
      return;
    }
    const base = hubR > 0 ? hubR + it.r + GAP : (placed[0]?.r ?? 0) + it.r + GAP;
    for (let k = 0; k < 20000; k++) {
      const a = startAngle + k * 0.21;
      const d = base + k * 0.45;
      const x = d * Math.cos(a);
      const y = d * Math.sin(a);
      if (free(x, y, it.r)) {
        placed.push({ ...it, x, y });
        return;
      }
    }
    placed.push({ ...it, x: base * 3, y: 0 });
  });
  return placed;
}

export function computeLayout(nodes: LayoutInputNode[], edges: LayoutInputEdge[]): Positions {
  const out: Positions = {};
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const docsOf = new Map<string, string[]>();
  for (const n of nodes) {
    if (n.kind !== 'doc') continue;
    const k = n.container && byId.has(n.container) ? n.container : '';
    (docsOf.get(k) ?? docsOf.set(k, []).get(k)!).push(n.id);
  }
  const discR = discRadius;

  // 1-2: discs packed per platform (relative to the platform hub)
  const containers = nodes.filter((n) => n.kind === 'container');
  const platforms = new Set<string>([...nodes.filter((n) => n.kind === 'platform').map((n) => n.platform ?? ''), ...containers.map((c) => c.platform ?? '')]);
  const groups: { platform: string; circles: Circle[]; r: number }[] = [];
  const rank = (p: string) => (ORDER.includes(p) ? ORDER.indexOf(p) : 99);
  const platformList = [...platforms].sort((a, b) => rank(a) - rank(b) || (a < b ? -1 : 1));
  platformList.forEach((pl, gi) => {
    const items = containers.filter((c) => (c.platform ?? '') === pl).map((c) => ({ id: c.id, r: discR(docsOf.get(c.id)?.length ?? 0) }));
    const circles = pack(items, 9, gi * 1.3);
    const r = circles.reduce((m, c) => Math.max(m, Math.hypot(c.x, c.y) + c.r), 12);
    groups.push({ platform: pl, circles, r });
  });

  // 3: platform groups packed around the origin
  const placedGroups = pack(groups.map((g) => ({ id: g.platform, r: g.r })), 0, -0.6);
  const centre = new Map(placedGroups.map((c) => [c.id, c]));
  const discs: Circle[] = [];
  for (const g of groups) {
    const c = centre.get(g.platform)!;
    const pid = `platform:${g.platform}`;
    if (byId.has(pid)) out[pid] = [c.x, c.y];
    for (const d of g.circles) {
      const x = c.x + d.x;
      const y = c.y + d.y;
      out[d.id] = [x, y];
      discs.push({ id: d.id, r: d.r, x, y });
    }
  }

  // documents: sunflower spiral inside their disc
  for (const [cid, ids] of docsOf) {
    const [cx, cy] = out[cid] ?? [0, 0];
    ids.sort();
    ids.forEach((id, k) => {
      out[id] = docSlot(cx, cy, k);
    });
  }

  // 4: people at the weighted centre of the places they write in, pushed out of any disc
  const nbr = new Map<string, { id: string; w: number }[]>();
  for (const e of edges) {
    (nbr.get(e.s) ?? nbr.set(e.s, []).get(e.s)!).push({ id: e.t, w: e.w });
    (nbr.get(e.t) ?? nbr.set(e.t, []).get(e.t)!).push({ id: e.s, w: e.w });
  }
  const people = nodes.filter((n) => n.kind === 'person').sort((a, b) => (a.id < b.id ? -1 : 1));
  people.forEach((p, i) => {
    const ns = (nbr.get(p.id) ?? []).filter((n) => out[n.id]);
    let x = 0;
    let y = 0;
    let w = 0;
    for (const n of ns) {
      x += out[n.id][0] * n.w;
      y += out[n.id][1] * n.w;
      w += n.w;
    }
    if (w > 0) {
      x /= w;
      y /= w;
    }
    const a = i * GOLDEN;
    if (ns.length === 1) {
      const d = discs.find((c) => c.id === ns[0].id);
      const rr = (d?.r ?? 6) + 4;
      x += rr * Math.cos(a);
      y += rr * Math.sin(a);
    } else {
      x += 3 * Math.cos(a);
      y += 3 * Math.sin(a);
    }
    for (let pass = 0; pass < 3; pass++) {
      for (const c of discs) {
        const dx = x - c.x;
        const dy = y - c.y;
        const d = Math.hypot(dx, dy);
        if (d < c.r + 2.5) {
          const ux = d > 0.01 ? dx / d : Math.cos(a);
          const uy = d > 0.01 ? dy / d : Math.sin(a);
          x = c.x + ux * (c.r + 3);
          y = c.y + uy * (c.r + 3);
        }
      }
    }
    out[p.id] = [x, y];
  });
  // keep people apart from each other, so their names never sit on top of one another
  for (let pass = 0; pass < 6; pass++) {
    for (let i = 0; i < people.length; i++) {
      for (let j = i + 1; j < people.length; j++) {
        const a = out[people[i].id];
        const b = out[people[j].id];
        const dx = b[0] - a[0];
        const dy = b[1] - a[1];
        const d = Math.hypot(dx, dy);
        if (d >= 12) continue;
        const ux = d > 0.01 ? dx / d : Math.cos(j * GOLDEN);
        const uy = d > 0.01 ? dy / d : Math.sin(j * GOLDEN);
        const push = (12 - d) / 2;
        out[people[i].id] = [a[0] - ux * push, a[1] - uy * push];
        out[people[j].id] = [b[0] + ux * push, b[1] + uy * push];
      }
    }
  }

  // anything left (unknown kinds, orphan documents): a small ring at the origin
  nodes.forEach((n, i) => {
    if (!out[n.id]) out[n.id] = [6 * Math.cos(i * GOLDEN), 6 * Math.sin(i * GOLDEN)];
  });
  for (const k of Object.keys(out)) out[k] = [Math.round(out[k][0] * 100) / 100, Math.round(out[k][1] * 100) / 100];
  return out;
}
