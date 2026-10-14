// Deterministic radial layout for the knowledge map: the person in the centre, platforms either
// side, and each platform's places (spaces / projects / channels / shared drives) on an arc beyond
// it. Same input → same picture, always. Labels run horizontally outward on both sides, so they
// never collide however many places there are (rows are spaced evenly; the canvas grows taller).
import type { MapPlatform, Platform } from '@/lib/types';

export const W = 1000;
const ROW = 46;
const GROUP_GAP = 0.7; // extra rows between platform groups
const PILL_X = 150;
export const PILL_W = 132;
export const PILL_H = 44;
const ARC_X = 268;
const ARC_BULGE = 58;
export const PERSON_R = 40;

const ORDER: Platform[] = ['confluence', 'jira', 'slack', 'drive'];

export interface PlaceNode {
  key: string;
  platform: Platform;
  container: string;
  name: string;
  count: number;
  x: number;
  y: number;
  r: number;
  dir: 1 | -1;
}
export interface PlatformNode {
  platform: Platform;
  count: number;
  places: number;
  x: number;
  y: number;
  dir: 1 | -1;
}
export interface MapLayout {
  width: number;
  height: number;
  cx: number;
  cy: number;
  platforms: PlatformNode[];
  places: PlaceNode[];
}

export const placeKey = (platform: string, container: string) => `${platform}:${container}`;

export function layoutMap(platforms: MapPlatform[]): MapLayout {
  // Balance the two sides by number of places (largest platform first, ties to the right).
  const sides: { right: MapPlatform[]; left: MapPlatform[] } = { right: [], left: [] };
  const load = { right: 0, left: 0 };
  const bySize = [...platforms].sort((a, b) => b.containers.length - a.containers.length || ORDER.indexOf(a.platform) - ORDER.indexOf(b.platform));
  for (const p of bySize) {
    const side = load.right <= load.left ? 'right' : 'left';
    sides[side].push(p);
    load[side] += p.containers.length;
  }
  for (const k of ['right', 'left'] as const) sides[k].sort((a, b) => ORDER.indexOf(a.platform) - ORDER.indexOf(b.platform));

  const rowsOf = (ps: MapPlatform[]) => ps.reduce((n, p) => n + p.containers.length, 0) + Math.max(0, ps.length - 1) * GROUP_GAP;
  const maxRows = Math.max(rowsOf(sides.right), rowsOf(sides.left), 1);
  const height = Math.max(440, Math.round((maxRows - 1) * ROW + 140));
  const cx = W / 2;
  const cy = height / 2;
  const half = height / 2 - 40;
  const maxCount = Math.max(1, ...platforms.flatMap((p) => p.containers.map((c) => c.count)));

  const outPlatforms: PlatformNode[] = [];
  const outPlaces: PlaceNode[] = [];
  for (const [k, dir] of [['right', 1], ['left', -1]] as const) {
    const ps = sides[k];
    const span = (rowsOf(ps) - 1) * ROW;
    let y = cy - span / 2;
    const groupYs: number[] = [];
    for (const p of ps) {
      const ys: number[] = [];
      const containers = [...p.containers].sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
      for (const c of containers) {
        const t = Math.max(-1, Math.min(1, (y - cy) / half));
        const x = cx + dir * (ARC_X + ARC_BULGE * (1 - t * t));
        outPlaces.push({
          key: placeKey(p.platform, c.container),
          platform: p.platform,
          container: c.container,
          name: c.name,
          count: c.count,
          x,
          y,
          r: 5 + 13 * Math.sqrt(c.count / maxCount),
          dir,
        });
        ys.push(y);
        y += ROW;
      }
      y += GROUP_GAP * ROW;
      groupYs.push(ys.length ? (ys[0] + ys[ys.length - 1]) / 2 : cy);
    }
    // Keep platform pills apart (min gap) while staying near their places.
    const minGap = PILL_H + 16;
    for (let i = 1; i < groupYs.length; i++) if (groupYs[i] - groupYs[i - 1] < minGap) groupYs[i] = groupYs[i - 1] + minGap;
    const overflow = groupYs.length ? groupYs[groupYs.length - 1] - (height - 40) : 0;
    if (overflow > 0) for (let i = 0; i < groupYs.length; i++) groupYs[i] -= overflow;
    ps.forEach((p, i) =>
      outPlatforms.push({ platform: p.platform, count: p.count, places: p.containers.length, x: cx + dir * PILL_X, y: groupYs[i], dir }),
    );
  }
  return { width: W, height, cx, cy, platforms: outPlatforms, places: outPlaces };
}
