'use client';
import { BookOpen, FolderOpen, Hash, SquareKanban, type LucideIcon } from 'lucide-react';
import { useMemo, useState } from 'react';
import { PLACE_NOUN, PLATFORM_LABEL, placeLabel, plural } from '@/lib/format';
import type { MapPlatform } from '@/lib/types';
import { PERSON_R, PILL_H, PILL_W, layoutMap, placeKey } from './layout';

const ICONS: Record<string, LucideIcon> = { confluence: BookOpen, jira: SquareKanban, slack: Hash, drive: FolderOpen };

export type MapSelection = { kind: 'place'; platform: string; container: string } | { kind: 'platform'; platform: string } | null;

function initials(sub: string, name?: string): string {
  const src = (name || sub).replace(/[._-]+/g, ' ').trim();
  const parts = src.split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? '') + (parts[1]?.[0] ?? '')).toUpperCase() || '?';
}

function clip(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, Math.max(1, n - 1))}…` : s;
}

/**
 * The visual map. Everything drawn comes from GET /ib/map for the signed-in person: platforms the
 * person cannot open are simply absent, and nothing hints at what is not listed.
 */
export function KnowledgeMapView({
  platforms,
  sub,
  name,
  selection,
  onSelect,
  matches,
}: {
  platforms: MapPlatform[];
  sub: string;
  name?: string;
  selection: MapSelection;
  onSelect: (s: MapSelection) => void;
  /** When searching: number of matching items per place key (absent = not searching). */
  matches?: Map<string, number>;
}) {
  const L = useMemo(() => layoutMap(platforms), [platforms]);
  const [hover, setHover] = useState<string | null>(null);
  const selPlace = selection?.kind === 'place' ? placeKey(selection.platform, selection.container) : null;
  const selPlatform = selection?.platform ?? null;

  const activate = (s: MapSelection) => (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onSelect(s);
    }
  };

  const pillById = new Map(L.platforms.map((p) => [p.platform, p]));

  return (
    <svg
      viewBox={`0 0 ${L.width} ${L.height}`}
      className="block h-auto w-full select-none"
      role="group"
      aria-labelledby="map-svg-title"
      data-testid="map-svg"
    >
      <title id="map-svg-title">{`Map of what ${sub} can open: ${platforms.length} platforms. Choose a place to list its items.`}</title>

      {/* edges: person → platform → place */}
      <g fill="none" strokeLinecap="round">
        {L.platforms.map((p) => {
          const on = selPlatform === p.platform;
          return (
            <line
              key={`e-${p.platform}`}
              x1={L.cx + p.dir * PERSON_R}
              y1={L.cy}
              x2={p.x - (p.dir * PILL_W) / 2}
              y2={p.y}
              style={{ stroke: on ? 'rgb(var(--accent))' : 'rgb(var(--line-strong))' }}
              strokeWidth={on ? 2 : 1.5}
            />
          );
        })}
        {L.places.map((n) => {
          const p = pillById.get(n.platform)!;
          const x1 = p.x + (p.dir * PILL_W) / 2;
          const x2 = n.x - n.dir * n.r;
          const mid = (x1 + x2) / 2;
          const on = selPlace === n.key || hover === n.key;
          const dim = matches && !(matches.get(n.key) ?? 0);
          return (
            <path
              key={`e-${n.key}`}
              d={`M ${x1} ${p.y} C ${mid} ${p.y}, ${mid} ${n.y}, ${x2} ${n.y}`}
              style={{ stroke: on ? 'rgb(var(--accent))' : 'rgb(var(--line-strong))', opacity: dim ? 0.25 : on ? 1 : 0.7 }}
              strokeWidth={on ? 1.75 : 1}
            />
          );
        })}
      </g>

      {/* the person */}
      <g aria-hidden>
        <circle cx={L.cx} cy={L.cy} r={PERSON_R + 8} style={{ fill: 'rgb(var(--accent) / 0.08)' }} />
        <circle cx={L.cx} cy={L.cy} r={PERSON_R} style={{ fill: 'rgb(var(--accent-solid))' }} />
        <text x={L.cx} y={L.cy + 6} textAnchor="middle" fontSize={18} fontWeight={600} style={{ fill: 'rgb(var(--accent-fg))' }}>
          {initials(sub, name)}
        </text>
        <text x={L.cx} y={L.cy + PERSON_R + 26} textAnchor="middle" fontSize={13} fontWeight={600} style={{ fill: 'rgb(var(--fg))' }}>
          You
        </text>
        <text x={L.cx} y={L.cy + PERSON_R + 42} textAnchor="middle" fontSize={11} fontFamily="ui-monospace, Menlo, Consolas, monospace" style={{ fill: 'rgb(var(--muted))' }}>
          {sub}
        </text>
      </g>

      {/* platforms */}
      {L.platforms.map((p) => {
        const Icon = ICONS[p.platform] ?? FolderOpen;
        const on = selPlatform === p.platform && selection?.kind === 'platform';
        const label = PLATFORM_LABEL[p.platform] ?? p.platform;
        return (
          <g
            key={p.platform}
            role="button"
            tabIndex={0}
            className="map-node cursor-pointer"
            aria-pressed={on}
            aria-label={`${label}: ${plural(p.count, 'item')} in ${plural(p.places, PLACE_NOUN[p.platform]?.[0] ?? 'place', PLACE_NOUN[p.platform]?.[1])}`}
            onClick={() => onSelect(on ? null : { kind: 'platform', platform: p.platform })}
            onKeyDown={activate(on ? null : { kind: 'platform', platform: p.platform })}
            data-testid={`map-platform-${p.platform}`}
          >
            <rect
              className="node-ring"
              x={p.x - PILL_W / 2}
              y={p.y - PILL_H / 2}
              width={PILL_W}
              height={PILL_H}
              rx={10}
              style={{ fill: on ? 'rgb(var(--accent) / 0.14)' : 'rgb(var(--raised))', stroke: on ? 'rgb(var(--accent))' : 'rgb(var(--line-strong))' }}
              strokeWidth={1.25}
            />
            <Icon x={p.x - PILL_W / 2 + 12} y={p.y - 8} width={16} height={16} style={{ color: 'rgb(var(--muted))' }} aria-hidden />
            <text x={p.x - PILL_W / 2 + 36} y={p.y - 2} fontSize={13} fontWeight={600} style={{ fill: 'rgb(var(--fg))' }}>
              {label}
            </text>
            <text x={p.x - PILL_W / 2 + 36} y={p.y + 13} fontSize={11} className="tabular" style={{ fill: 'rgb(var(--muted))' }}>
              {plural(p.count, 'item')}
            </text>
          </g>
        );
      })}

      {/* places */}
      {L.places.map((n) => {
        const on = selPlace === n.key;
        const hot = hover === n.key;
        const m = matches?.get(n.key);
        const dim = matches && !m;
        const label = placeLabel(n.platform, n.name);
        const lx = n.x + n.dir * (n.r + 9);
        const room = n.dir === 1 ? L.width - lx - 6 : lx - 6;
        const maxChars = Math.max(8, Math.floor(room / 7.8));
        const sel: MapSelection = { kind: 'place', platform: n.platform, container: n.container };
        return (
          <g
            key={n.key}
            role="button"
            tabIndex={0}
            className="map-node cursor-pointer"
            aria-pressed={on}
            aria-label={`${label}, ${PLATFORM_LABEL[n.platform] ?? n.platform}: ${plural(n.count, 'item')}${m !== undefined ? `, ${plural(m, 'match', 'matches')}` : ''}`}
            onClick={() => onSelect(on ? null : sel)}
            onKeyDown={activate(on ? null : sel)}
            onMouseEnter={() => setHover(n.key)}
            onMouseLeave={() => setHover(null)}
            style={{ opacity: dim ? 0.35 : 1 }}
            data-testid="map-place"
            data-place={n.container}
          >
            <circle cx={n.x} cy={n.y} r={Math.max(n.r + 8, 16)} style={{ fill: 'transparent' }} />
            <circle
              className="node-ring"
              cx={n.x}
              cy={n.y}
              r={n.r}
              style={{
                fill: on ? 'rgb(var(--accent-solid))' : hot ? 'rgb(var(--accent) / 0.4)' : 'rgb(var(--accent) / 0.22)',
                stroke: on || hot ? 'rgb(var(--accent))' : 'rgb(var(--accent) / 0.75)',
              }}
              strokeWidth={1.25}
            />
            <text
              x={lx}
              y={n.y - 1}
              textAnchor={n.dir === 1 ? 'start' : 'end'}
              fontSize={14}
              fontWeight={on ? 600 : 500}
              style={{ fill: 'rgb(var(--fg))' }}
            >
              {clip(label, maxChars)}
            </text>
            <text x={lx} y={n.y + 15} textAnchor={n.dir === 1 ? 'start' : 'end'} fontSize={12} className="tabular" style={{ fill: 'rgb(var(--muted))' }}>
              {plural(n.count, 'item')}
              {m !== undefined ? ` · ${plural(m, 'match', 'matches')}` : ''}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
