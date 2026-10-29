'use client';
// History: my questions from GET /ib/history, grouped Today / Yesterday / Earlier (Singapore time),
// searchable. Each row opens how it was answered (the request journey, Developer mode).
import { ArrowUpRight, History, MessageSquareText, RefreshCw, Search } from 'lucide-react';
import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Page } from '@/components/shell/Page';
import { Button, EmptyState, Input, SkeletonText } from '@/components/ui';
import { fmtDay, fmtWhen, sgDay } from '@/components/user/answerText';
import { ApiError, api } from '@/lib/api';
import { useLang, useMessages } from '@/lib/i18n';
import { userMessages, type UserKey } from '@/lib/i18n/user';
import type { HistoryItem } from '@/lib/types';

/** GET /ib/history rows (backend_v1: `channel` and `synthetic_replay` were added). */
type Row = HistoryItem & { channel?: string | null; synthetic_replay?: boolean };

const CHANNEL_KEY: Record<string, UserKey> = {
  ui: 'history.channel.ui',
  'workbuddy-mcp': 'history.channel.workbuddy-mcp',
  api: 'history.channel.api',
  replay: 'history.channel.replay',
};

export default function HistoryPage() {
  const t = useMessages(userMessages);
  const { lang } = useLang();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState(false);
  const [q, setQ] = useState('');

  const load = useCallback(() => {
    setError(false);
    setRows(null);
    api
      .history()
      .then((h) => setRows((h.items ?? []) as Row[]))
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) return; // the shell redirects to sign-in
        setError(true);
      });
  }, []);
  useEffect(load, [load]);

  const groups = useMemo(() => {
    if (!rows) return [];
    const needle = q.trim().toLowerCase();
    const shown = needle ? rows.filter((r) => r.question.toLowerCase().includes(needle)) : rows;
    const today = sgDay(Date.now());
    const yesterday = sgDay(Date.now() - 86400_000);
    const out: { key: 'history.today' | 'history.yesterday' | 'history.earlier'; rows: Row[] }[] = [
      { key: 'history.today', rows: [] },
      { key: 'history.yesterday', rows: [] },
      { key: 'history.earlier', rows: [] },
    ];
    for (const r of [...shown].sort((a, b) => (a.event_time < b.event_time ? 1 : -1))) {
      const d = sgDay(r.event_time);
      out[d === today ? 0 : d === yesterday ? 1 : 2].rows.push(r);
    }
    return out.filter((g) => g.rows.length);
  }, [rows, q]);

  return (
    <Page title={t('history.title')} description={t('history.subtitle')} width="narrow">
      {rows && rows.length ? (
        <div className="relative mb-6">
          <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" />
          <label htmlFor="history-search" className="sr-only">
            {t('history.search')}
          </label>
          <Input
            id="history-search"
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t('history.search')}
            className="h-10 rounded-lg pl-9 text-body-sm"
            data-testid="history-search"
          />
        </div>
      ) : null}

      {error ? (
        <EmptyState
          icon={History}
          title={t('error.title')}
          description={t('history.error')}
          action={
            <Button icon={RefreshCw} onClick={load}>
              {t('error.retry')}
            </Button>
          }
        />
      ) : !rows ? (
        <SkeletonText lines={6} label={t('history.loading')} />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={MessageSquareText}
          title={t('history.empty')}
          description={t('history.emptyBody')}
          action={
            <Button asChild variant="primary">
              <Link href="/">{t('history.askNow')}</Link>
            </Button>
          }
        />
      ) : groups.length === 0 ? (
        <p className="py-10 text-center text-body-sm text-muted" role="status">
          {t('history.noMatch', { q: q.trim() })}
        </p>
      ) : (
        <div className="space-y-8" data-testid="history-list">
          {groups.map((g) => (
            <section key={g.key} aria-labelledby={`h-${g.key}`}>
              <h2 id={`h-${g.key}`} className="mb-2 text-micro font-semibold uppercase tracking-wider text-subtle">
                {t(g.key)}
              </h2>
              <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface">
                {g.rows.map((r) => {
                  const when = g.key === 'history.earlier' ? `${fmtDay(r.event_time, lang)} · ${fmtWhen(r.event_time, lang, false)}` : fmtWhen(r.event_time, lang, false);
                  const channel = r.channel && r.channel !== 'ui' && r.channel !== 'replay' ? CHANNEL_KEY[r.channel] : undefined;
                  return (
                    <li key={r.req_id} data-testid="history-row" data-replay={r.synthetic_replay ? 'true' : undefined}>
                      <Link
                        href={`/dev/journey/${encodeURIComponent(r.req_id)}`}
                        aria-label={t('history.rowLabel', { q: r.question, when })}
                        className="group flex min-w-0 items-start gap-3 px-4 py-3.5 transition-colors duration-fast hover:bg-raised/60"
                      >
                        <MessageSquareText aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-subtle" />
                        <span className="min-w-0 flex-1">
                          <span className="line-clamp-2 text-body-sm text-fg">{r.question}</span>
                          <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-muted">
                            <span className="tabular-nums">{when}</span>
                            {channel ? (
                              <>
                                <span aria-hidden>·</span>
                                <span>{t(channel)}</span>
                              </>
                            ) : null}
                            {r.synthetic_replay ? (
                              <span
                                className="rounded-full border border-line-strong bg-raised px-1.5 py-px text-micro font-medium text-muted"
                                title={t('history.seededTip')}
                                data-testid="seeded-tag"
                              >
                                {t('history.seeded')}
                              </span>
                            ) : null}
                          </span>
                        </span>
                        <ArrowUpRight aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-subtle transition-colors group-hover:text-fg" />
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      )}
      {rows && rows.some((r) => r.synthetic_replay) ? <p className="mt-6 text-caption text-subtle">{t('history.seededTip')}</p> : null}
    </Page>
  );
}
