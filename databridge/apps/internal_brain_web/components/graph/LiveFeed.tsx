'use client';
// "Just added" (PLAN §2.3): the documents that arrived in THIS person's graph from the live feed,
// newest first, at most 5, each dropping off after 10 minutes. The empty sentence is the same for
// everyone (it never says "nothing you can see"). Ages are measured from when this browser received
// the item; the server sends no per-item time. The list is not a live region; the explorer announces
// one polite sentence per non-empty poll instead.
//
// Round-3 review: M7, below the `sm` breakpoint the feed is always compact (one line, one item) so
// it does not cover the graph; M8, when polling stops for good (401/403/404) the card says so and
// the switch reads off and is disabled; "unavailable" and "stopped" are announced once each; L5,
// every "Show" button is named after its item.
import { Radio } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { announce, Badge, Button, Switch } from '@/components/ui';
import { cn } from '@/lib/cn';
import { useMessages } from '@/lib/i18n';
import { graphMessages } from '@/lib/i18n/graph';
import type { Platform } from '@/lib/types';
import type { LiveState } from './useLiveChanges';

export interface FeedItem {
  id: string;
  label: string;
  platform: Platform | null;
  containerName: string | null;
  docKind: string | null;
  change: 'new' | 'updated';
  receivedAt: number;
  /** Seconds between this browser's post and the item's arrival (own notes only). */
  afterS: number | null;
}

export const FEED_MAX = 5;
export const FEED_TTL_MS = 10 * 60_000;

const PLATFORM_NAME: Record<string, string> = { slack: 'Slack', jira: 'Jira', confluence: 'Confluence', drive: 'Google Drive' };

/** True below Tailwind's `sm` breakpoint (640 px). */
function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const mq = window.matchMedia('(max-width: 639.98px)');
    const on = () => setNarrow(mq.matches);
    on();
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return narrow;
}

export function containerLabel(platform: Platform | null, name: string | null): string {
  if (!name) return '';
  return platform === 'slack' ? `#${name.replace(/^#/, '')}` : name;
}

export function LiveFeed({
  items,
  state,
  paused,
  onPause,
  onShow,
  compact: compactProp,
  testId,
}: {
  items: FeedItem[];
  state: LiveState;
  paused: boolean;
  onPause: (p: boolean) => void;
  onShow: (id: string) => void;
  compact?: boolean;
  testId: string;
}) {
  const t = useMessages(graphMessages);
  const narrow = useNarrow();
  const compact = compactProp || narrow;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const h = setInterval(() => setNow(Date.now()), 5_000);
    return () => clearInterval(h);
  }, []);
  const live = items.filter((i) => now - i.receivedAt < FEED_TTL_MS).slice(0, FEED_MAX);
  const age = (at: number) => {
    const s = Math.max(0, Math.round((now - at) / 1000));
    if (s < 10) return t('live.justNow');
    if (s < 60) return t('live.secAgo', { n: s });
    return t('live.minAgo', { n: Math.floor(s / 60) });
  };
  const what = (i: FeedItem) =>
    i.change === 'updated'
      ? t('live.updated')
      : t(i.docKind === 'thread' ? 'live.new.thread' : i.docKind === 'page' ? 'live.new.page' : i.docKind === 'issue' ? 'live.new.issue' : i.docKind === 'file' ? 'live.new.file' : 'live.new.doc');
  const headingId = `${testId}-title`;
  const stopped = state === 'stopped';
  const status = stopped ? t('live.stopped') : paused ? t('live.paused') : state === 'unavailable' ? t('live.unavailable') : null;
  // announce a problem once when it starts (not on every poll); the user's own pause is not announced
  const spoken = useRef<LiveState | null>(null);
  useEffect(() => {
    if (paused) return;
    if ((state === 'unavailable' || state === 'stopped') && spoken.current !== state) announce(state === 'stopped' ? t('live.stopped') : t('live.unavailable'));
    spoken.current = state;
  }, [state, paused, t]);

  return (
    <section
      aria-labelledby={headingId}
      className={cn('pointer-events-auto w-[min(22rem,calc(100vw-2rem))] shrink-0 rounded-lg border border-line bg-surface/95 shadow-pop backdrop-blur', compact ? 'px-2.5 py-1.5' : 'px-3 py-2.5')}
      data-testid={testId}
      data-state={stopped ? 'stopped' : paused ? 'paused' : state}
    >
      <header className="flex items-center gap-2">
        <Radio aria-hidden className={cn('h-3.5 w-3.5 shrink-0', !paused && state === 'live' ? 'text-accent' : 'text-subtle')} />
        <h2 id={headingId} className="text-caption font-semibold text-fg">
          {t('live.title')}
        </h2>
        <Badge tone="warn">{t('demo.badge')}</Badge>
        <label className="ml-auto flex items-center gap-1.5 text-caption text-muted">
          {t('live.switch')}
          <Switch checked={!paused && !stopped} disabled={stopped} onCheckedChange={(v) => onPause(!v)} aria-label={t('live.switch')} data-testid={`${testId}-switch`} />
        </label>
      </header>
      {status ? (
        <p className="mt-1.5 text-caption text-muted" data-testid={`${testId}-status`}>
          {status}
        </p>
      ) : null}
      {live.length === 0 ? (
        compact ? null : (
          <p className="mt-1.5 text-caption text-muted" data-testid={`${testId}-empty`}>
            {t('live.empty')}
          </p>
        )
      ) : (
        <ul className={cn('mt-1.5 space-y-1', compact && 'max-h-12 overflow-hidden')} data-testid={`${testId}-list`}>
          {(compact ? live.slice(0, 1) : live).map((i) => (
            <li key={`${i.id}:${i.receivedAt}`} className="flex items-start gap-2 rounded-md border border-line/60 bg-bg/60 px-2 py-1.5" data-testid={`${testId}-item`} data-doc={i.id}>
              <div className="min-w-0 flex-1">
                <p className="truncate text-caption font-medium text-fg" title={i.label}>
                  {i.label}
                </p>
                <p className="text-2xs text-muted">
                  {[PLATFORM_NAME[i.platform ?? ''] ?? i.platform, containerLabel(i.platform, i.containerName), what(i), age(i.receivedAt)].filter(Boolean).join(' · ')}
                </p>
                {i.afterS !== null ? (
                  <p className="text-2xs tabular-nums text-accent" data-testid={`${testId}-after`}>
                    {t('live.yours')} · {t('live.after', { s: i.afterS.toFixed(1) })}
                  </p>
                ) : null}
              </div>
              <Button size="xs" variant="ghost" onClick={() => onShow(i.id)} aria-label={t('live.showLabel', { label: i.label })}>
                {t('live.show')}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
