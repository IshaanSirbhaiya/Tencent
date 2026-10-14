import { PLATFORM_LABEL, plural } from '@/lib/format';
import type { KnowledgeMap } from '@/lib/types';
import { PlatformIcon } from '../PlatformIcon';

/** "You can open 867 items in 17 places across 4 platforms", straight from the map's totals. */
export function TotalsSentence({ map, who = 'You', className }: { map: KnowledgeMap; who?: string; className?: string }) {
  const t = map.totals;
  return (
    <p className={className} data-testid="map-totals">
      {who} can open <strong className="font-semibold text-fg tabular">{t.items.toLocaleString()}</strong> {t.items === 1 ? 'item' : 'items'} in{' '}
      <strong className="font-semibold text-fg tabular">{t.containers.toLocaleString()}</strong> {t.containers === 1 ? 'place' : 'places'} across{' '}
      <strong className="font-semibold text-fg tabular">{t.platforms}</strong> {t.platforms === 1 ? 'platform' : 'platforms'}.
    </p>
  );
}

/** Compact per-platform summary (compare page). Lists only the platforms this person can open. */
export function MapSummary({ map, who }: { map: KnowledgeMap; who: string }) {
  const max = Math.max(1, ...map.platforms.map((p) => p.count));
  return (
    <div className="space-y-2" data-testid="map-summary">
      <TotalsSentence map={map} who={who} className="text-xs text-muted" />
      <ul className="space-y-1.5">
        {map.platforms.map((p) => (
          <li key={p.platform} className="grid grid-cols-[minmax(0,8.5rem)_minmax(0,1fr)_auto] items-center gap-2 text-xs">
            <PlatformIcon platform={p.platform} className="min-w-0 truncate" />
            <span className="h-1.5 rounded-full bg-raised" aria-hidden>
              <span className="block h-1.5 rounded-full bg-accent/60" style={{ width: `${Math.max(2, (p.count / max) * 100)}%` }} />
            </span>
            <span className="tabular text-fg">
              {p.count.toLocaleString()}
              <span className="sr-only"> items in {PLATFORM_LABEL[p.platform] ?? p.platform}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
