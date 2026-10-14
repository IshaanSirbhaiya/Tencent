'use client';
import { CheckCircle2, CircleDashed, RefreshCw, TriangleAlert } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { PLATFORM_LABEL, fmtAgo, fmtDateTime, fmtDuration } from '@/lib/format';
import type { SourceStatus } from '@/lib/types';
import { InfoTip } from './InfoTip';
import { PlatformIcon } from './PlatformIcon';
import { Badge } from './ui';

function sloState(s: SourceStatus, now: number): boolean | null {
  if (typeof s.within_slo === 'boolean') return s.within_slo;
  const last = s.last_event_at ?? s.last_sync_at;
  if (typeof s.lag_s === 'number') return s.lag_s <= s.slo_s;
  if (!last) return null;
  return (now - new Date(last).getTime()) / 1000 <= s.slo_s;
}

/** Global per-platform sync status — the same for every user, never tied to a question (plan §4.6). */
export function SourcesPanel({ refreshKey }: { refreshKey?: number }) {
  const [rows, setRows] = useState<SourceStatus[] | null>(null);
  const [error, setError] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(() => {
    api
      .sources()
      .then((r) => {
        setRows(r);
        setError(false);
        setNow(Date.now());
      })
      .catch(() => setError(true));
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load, refreshKey]);

  return (
    <section aria-labelledby="sources-heading" className="rounded-lg border border-line bg-surface" data-testid="sources-panel" data-tour="sources-panel">
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <div className="flex items-center gap-1">
          <h2 id="sources-heading" className="text-sm font-semibold">
            Source sync status
          </h2>
          <InfoTip title="Source sync status">
            How up to date our copy of each platform is. Changes arrive within minutes; if a platform falls behind its target, answers mark its sources as stale.
          </InfoTip>
        </div>
        <button
          type="button"
          onClick={load}
          className="inline-flex h-7 w-7 items-center justify-center rounded text-muted hover:bg-raised hover:text-fg"
          aria-label="Refresh source sync status"
        >
          <RefreshCw aria-hidden className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="px-4 py-3">
        {error ? (
          <p className="text-xs text-muted" role="status">
            Sync status is unavailable right now.
          </p>
        ) : !rows ? (
          <p className="text-xs text-muted" role="status">
            Loading sync status…
          </p>
        ) : (
          <ul className="space-y-3">
            {rows.map((s) => {
              const ok = sloState(s, now);
              const last = s.last_event_at ?? s.last_sync_at;
              return (
                <li key={s.platform} className="flex flex-col gap-1">
                  <div className="flex items-center justify-between gap-2">
                    <PlatformIcon platform={s.platform} className="text-sm font-medium" />
                    {ok === null ? (
                      <Badge icon={CircleDashed}>Not synced yet</Badge>
                    ) : ok ? (
                      <Badge tone="ok" icon={CheckCircle2}>
                        Within target
                      </Badge>
                    ) : (
                      <Badge tone="warn" icon={TriangleAlert}>
                        Behind target
                      </Badge>
                    )}
                  </div>
                  <p className="text-2xs text-muted tabular">
                    <span title={fmtDateTime(last)}>Last change seen {fmtAgo(last, now)}</span>
                    {' · '}target ≤ {fmtDuration(s.slo_s)}
                    {s.state ? ` · ${s.state}` : ''}
                  </p>
                  <span className="sr-only">{`${PLATFORM_LABEL[s.platform] ?? s.platform} last synced ${fmtDateTime(s.last_sync_at)}`}</span>
                </li>
              );
            })}
          </ul>
        )}
        <p className="mt-3 border-t border-line pt-3 text-2xs text-muted">Global status for all users. It does not reflect which sources any one answer used.</p>
      </div>
    </section>
  );
}
