'use client';
import { ArrowLeft, ExternalLink, Search } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { KIND_LABEL, PLACE_NOUN, PLATFORM_LABEL, fmtDate, placeLabel, plural, shortVersion } from '@/lib/format';
import type { KnowledgeMap, MapContainer, MapItem, MapPlatform } from '@/lib/types';
import { safeHref } from '@/lib/url';
import { PlatformIcon } from '../PlatformIcon';
import { Badge, inputClass } from '../ui';
import type { MapSelection } from './KnowledgeMapView';

const PAGE = 60;

export function itemMatches(it: MapItem, q: string): boolean {
  return it.title.toLowerCase().includes(q);
}

function ItemRow({ it, platform, place }: { it: MapItem; platform: string; place?: string }) {
  const v = shortVersion(it.version);
  const plat = PLATFORM_LABEL[platform] ?? platform;
  const href = safeHref(it.deep_link);
  return (
    <li className="px-3 py-2.5" data-testid="map-item">
      <p className="text-sm leading-5 text-fg">{it.title}</p>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-2xs text-muted">
        <Badge>{KIND_LABEL[it.kind] ?? it.kind}</Badge>
        {place ? <span>{place}</span> : null}
        {v.text ? (
          <>
            <span className="tabular" title={v.full}>
              {v.text}
            </span>
            <span aria-hidden>·</span>
          </>
        ) : null}
        <span className="tabular">Updated {fmtDate(it.updated_at)}</span>
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto inline-flex items-center gap-1 font-medium text-accent underline-offset-2 hover:underline"
            aria-label={`Open “${it.title}” in ${plat} (new tab)`}
          >
            Open in {plat}
            <ExternalLink aria-hidden className="h-3 w-3" />
          </a>
        ) : (
          <span className="ml-auto" data-testid="map-item-nolink">
            In {plat}
          </span>
        )}
      </div>
    </li>
  );
}

/** The side panel: all places (with a bar per place), one place's items, or search results. */
export function PlacePanel({
  map,
  selection,
  onSelect,
  query,
  onQuery,
}: {
  map: KnowledgeMap;
  selection: MapSelection;
  onSelect: (s: MapSelection) => void;
  query: string;
  onQuery: (q: string) => void;
}) {
  const [shown, setShown] = useState(PAGE);
  const q = query.trim().toLowerCase();
  const place = useMemo((): { p: MapPlatform; c: MapContainer } | null => {
    if (selection?.kind !== 'place') return null;
    const p = map.platforms.find((x) => x.platform === selection.platform);
    const c = p?.containers.find((x) => x.container === selection.container);
    return p && c ? { p, c } : null;
  }, [map, selection]);
  useEffect(() => setShown(PAGE), [selection, q]);

  const platforms = selection?.kind === 'platform' ? map.platforms.filter((p) => p.platform === selection.platform) : map.platforms;
  const maxCount = Math.max(1, ...map.platforms.flatMap((p) => p.containers.map((c) => c.count)));

  let heading: string;
  let sub: string;
  if (place) {
    heading = placeLabel(place.p.platform, place.c.name);
    sub = `${PLATFORM_LABEL[place.p.platform]} ${PLACE_NOUN[place.p.platform]?.[0] ?? 'place'} · ${plural(place.c.count, 'item')}`;
  } else if (selection?.kind === 'platform' && platforms[0]) {
    heading = PLATFORM_LABEL[platforms[0].platform] ?? platforms[0].platform;
    sub = `${plural(platforms[0].containers.length, PLACE_NOUN[platforms[0].platform]?.[0] ?? 'place', PLACE_NOUN[platforms[0].platform]?.[1])} · ${plural(platforms[0].count, 'item')}`;
  } else {
    heading = 'All places';
    sub = `${plural(map.totals.containers, 'place')} · ${plural(map.totals.items, 'item')}`;
  }

  const items = place ? (q ? place.c.items.filter((it) => itemMatches(it, q)) : place.c.items) : [];
  const results = !place && q
    ? platforms.flatMap((p) => p.containers.flatMap((c) => c.items.filter((it) => itemMatches(it, q)).map((it) => ({ it, p, c }))))
    : [];

  return (
    <section aria-labelledby="map-panel-heading" className="flex min-w-0 flex-col rounded-lg border border-line bg-surface lg:max-h-[calc(100vh-140px)]" data-testid="map-panel">
      <div className="space-y-3 border-b border-line px-4 py-3">
        {selection ? (
          <button
            type="button"
            onClick={() => onSelect(null)}
            className="-ml-1 inline-flex h-7 items-center gap-1 rounded px-1 text-xs text-muted hover:bg-raised hover:text-fg"
          >
            <ArrowLeft aria-hidden className="h-3.5 w-3.5" /> All places
          </button>
        ) : null}
        <div className="min-w-0">
          <h2 id="map-panel-heading" className="flex items-center gap-2 text-sm font-semibold">
            {place ? <PlatformIcon platform={place.p.platform} showLabel={false} /> : null}
            <span className="truncate">{heading}</span>
          </h2>
          <p className="mt-0.5 text-2xs text-muted tabular">{sub}</p>
        </div>
        <div className="relative">
          <label htmlFor="map-search" className="sr-only">
            Search items you can open
          </label>
          <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted" />
          <input
            id="map-search"
            type="search"
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            placeholder={place ? `Search ${heading}` : 'Search items by title'}
            className={`${inputClass} pl-8`}
            data-testid="map-search"
          />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {place ? (
          items.length === 0 ? (
            <p className="px-4 py-4 text-sm text-muted">No titles here match “{query.trim()}”.</p>
          ) : (
            <>
              <ul className="divide-y divide-line" data-testid="map-items">
                {items.slice(0, shown).map((it) => (
                  <ItemRow key={it.doc_id} it={it} platform={place.p.platform} />
                ))}
              </ul>
              <ShowMore shown={shown} total={items.length} onMore={() => setShown((n) => n + PAGE)} />
            </>
          )
        ) : q ? (
          results.length === 0 ? (
            <p className="px-4 py-4 text-sm text-muted">No titles match “{query.trim()}”.</p>
          ) : (
            <>
              <p className="px-4 pt-3 text-2xs text-muted tabular">{plural(results.length, 'match', 'matches')}</p>
              <ul className="divide-y divide-line" data-testid="map-items">
                {results.slice(0, shown).map(({ it, p, c }) => (
                  <ItemRow key={it.doc_id} it={it} platform={p.platform} place={placeLabel(p.platform, c.name)} />
                ))}
              </ul>
              <ShowMore shown={shown} total={results.length} onMore={() => setShown((n) => n + PAGE)} />
            </>
          )
        ) : (
          <div className="space-y-4 px-4 py-3">
            {platforms.map((p) => (
              <div key={p.platform}>
                <h3 className="mb-1.5 flex items-center justify-between text-xs font-semibold text-fg">
                  <PlatformIcon platform={p.platform} />
                  <span className="font-normal text-muted tabular">{plural(p.count, 'item')}</span>
                </h3>
                <ul className="space-y-0.5">
                  {[...p.containers]
                    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
                    .map((c) => (
                      <li key={c.container}>
                        <button
                          type="button"
                          onClick={() => onSelect({ kind: 'place', platform: p.platform, container: c.container })}
                          className="group w-full rounded px-2 py-1.5 text-left hover:bg-raised"
                          data-testid="map-place-row"
                        >
                          <span className="flex items-baseline justify-between gap-2 text-xs">
                            <span className="truncate text-fg group-hover:underline">{placeLabel(p.platform, c.name)}</span>
                            <span className="shrink-0 text-muted tabular">{c.count.toLocaleString()}</span>
                          </span>
                          <span className="mt-1 block h-1 w-full rounded-full bg-raised" aria-hidden>
                            <span className="block h-1 rounded-full bg-accent/60" style={{ width: `${Math.max(3, (c.count / maxCount) * 100)}%` }} />
                          </span>
                        </button>
                      </li>
                    ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function ShowMore({ shown, total, onMore }: { shown: number; total: number; onMore: () => void }) {
  if (total <= shown) return null;
  return (
    <div className="flex items-center justify-between gap-2 border-t border-line px-4 py-3 text-2xs text-muted">
      <span className="tabular">
        Showing {shown.toLocaleString()} of {total.toLocaleString()}
      </span>
      <button type="button" onClick={onMore} className="rounded-md border border-line-strong px-2.5 py-1 text-xs font-medium text-fg hover:bg-raised">
        Show more
      </button>
    </div>
  );
}
