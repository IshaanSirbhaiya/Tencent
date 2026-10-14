'use client';
import { MapPinned, RefreshCw } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { AppShell, useMe } from '@/components/AppShell';
import { InfoTip } from '@/components/InfoTip';
import { KnowledgeMapView, type MapSelection } from '@/components/map/KnowledgeMapView';
import { MapLoading } from '@/components/map/MapLoading';
import { TotalsSentence } from '@/components/map/MapSummary';
import { PlacePanel, itemMatches } from '@/components/map/PlacePanel';
import { placeKey } from '@/components/map/layout';
import { Button } from '@/components/ui';
import { ApiError, api } from '@/lib/api';
import { fmtDateTime } from '@/lib/format';
import type { KnowledgeMap } from '@/lib/types';

function MapView() {
  const me = useMe();
  const router = useRouter();
  const [map, setMap] = useState<KnowledgeMap | null>(null);
  const [failed, setFailed] = useState(false);
  const [selection, setSelection] = useState<MapSelection>(null);
  const [query, setQuery] = useState('');

  const load = useCallback(() => {
    setFailed(false);
    setMap(null);
    api
      .map()
      .then(setMap)
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 401) router.replace('/login');
        else setFailed(true);
      });
  }, [router]);
  useEffect(load, [load]);
  // A shared in-flight request could belong to whoever was signed in a moment ago: never show it.
  useEffect(() => {
    if (map && me && map.sub !== me.sub) load();
  }, [map, me, load]);

  const q = query.trim().toLowerCase();
  const matches = useMemo(() => {
    if (!map || !q) return undefined;
    const m = new Map<string, number>();
    for (const p of map.platforms) for (const c of p.containers) m.set(placeKey(p.platform, c.container), c.items.filter((it) => itemMatches(it, q)).length);
    return m;
  }, [map, q]);

  const shown = map && me && map.sub !== me.sub ? null : map;

  return (
    <div className="space-y-5">
      <div>
        <div className="flex items-center gap-1">
          <h1 className="text-xl font-semibold tracking-tight">Knowledge map</h1>
          <InfoTip title="Knowledge map" testId="infotip-map">
            Everything you can open across Confluence, Jira, Slack and Google Drive, grouped by space, project, channel and shared drive. It is different for every person, because it lists only what that person can open.
          </InfoTip>
        </div>
        {shown ? (
          <TotalsSentence map={shown} className="mt-1 text-base text-muted" />
        ) : (
          <p className="mt-1 text-sm text-muted">What you can open, across four platforms.</p>
        )}
      </div>

      {failed ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-surface p-4">
          <div>
            <p className="text-sm font-medium">The map didn&apos;t load.</p>
            <p className="mt-0.5 text-xs text-muted">The Internal Brain may be busy. Try again in a moment.</p>
          </div>
          <Button size="sm" icon={RefreshCw} onClick={load}>
            Try again
          </Button>
        </div>
      ) : !shown ? (
        <MapLoading />
      ) : shown.totals.items === 0 ? (
        <div className="rounded-lg border border-line bg-surface p-6" data-testid="map-empty">
          <p className="text-sm font-medium">There&apos;s nothing you can open yet.</p>
          <p className="mt-1 text-xs text-muted">Places appear here once you&apos;re added to a space, project, channel or shared drive.</p>
          <Link href="/" className="mt-3 inline-flex h-8 items-center rounded-md border border-line-strong px-3 text-xs font-medium hover:bg-raised">
            Go to Ask
          </Link>
        </div>
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_340px]" data-tour="map">
          <section aria-labelledby="map-canvas-heading" className="hidden min-w-0 rounded-lg border border-line bg-surface md:block" data-testid="map-canvas">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5">
              <h2 id="map-canvas-heading" className="flex items-center gap-2 text-sm font-semibold">
                <MapPinned aria-hidden className="h-4 w-4 text-muted" /> Where your items live
              </h2>
              <p className="text-2xs text-muted">Circle size shows how many items a place holds. Choose one to see them.</p>
            </div>
            <div className="px-2 py-3">
              <KnowledgeMapView platforms={shown.platforms} sub={shown.sub} name={me?.name} selection={selection} onSelect={setSelection} matches={matches} />
            </div>
          </section>
          <PlacePanel map={shown} selection={selection} onSelect={setSelection} query={query} onQuery={setQuery} />
        </div>
      )}

      {shown ? (
        <p className="text-2xs text-muted" data-testid="map-freshness">
          {shown.freshness_note} <span className="tabular">As of {fmtDateTime(shown.as_of)}.</span>
        </p>
      ) : null}
    </div>
  );
}

export default function MapPage() {
  return (
    <AppShell>
      <MapView />
    </AppShell>
  );
}
