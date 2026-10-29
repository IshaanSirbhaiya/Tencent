'use client';
import { ChevronLeft, ChevronRight, ExternalLink, EyeOff, RefreshCw, ShieldCheck, TriangleAlert } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { PlatformIcon } from '@/components/PlatformIcon';
import { Button, IconButton, SheetContent, Sheet, SkeletonText } from '@/components/ui';
import { ApiError, api } from '@/lib/api';
import { cn } from '@/lib/cn';
import { PLATFORM_LABEL, placeLabel } from '@/lib/format';
import { useLang, useMessages } from '@/lib/i18n';
import { userMessages } from '@/lib/i18n/user';
import { safeHref } from '@/lib/url';
import type { AskResponse, DocView } from '@/lib/types';
import { fmtWhen, highlightRange, sentencesFor, versionText } from './answerText';

type Load = { state: 'loading' } | { state: 'ok'; doc: DocView } | { state: 'gone' } | { state: 'error' };

export interface SourcesSheetProps {
  /** The answer whose sources are browsed (null = closed). */
  response: AskResponse | null;
  /** 0-based index into response.citations; null = closed. */
  index: number | null;
  onIndexChange: (i: number) => void;
  onClose: () => void;
  /** Element to give focus back to on close (the chip or card that opened the sheet). */
  returnFocusTo?: HTMLElement | null;
}

/**
 * Right sheet with one cited source: title, platform, container, version, author, the live check
 * time and the passage with the cited sentence highlighted. Calls GET /ib/doc/{id} every time it
 * opens (access is re-checked). Any 404 shows one sentence, whatever the reason.
 */
export function SourcesSheet({ response, index, onIndexChange, onClose, returnFocusTo }: SourcesSheetProps) {
  const t = useMessages(userMessages);
  const { lang } = useLang();
  const open = !!response && index !== null && index >= 0 && index < response.citations.length;
  const cite = open ? response!.citations[index!] : null;
  const total = response?.citations.length ?? 0;
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const sentences = cite && response ? sentencesFor(response.answer, cite.tag) : [];
  const q = sentences.join(' ').slice(0, 500);
  const docId = cite?.doc_id;

  useEffect(() => {
    if (!docId) return;
    let live = true;
    setLoad({ state: 'loading' });
    api
      .doc(docId, q || undefined)
      .then((doc) => live && setLoad({ state: 'ok', doc }))
      .catch((e) => {
        if (!live) return;
        setLoad(e instanceof ApiError && e.status === 404 ? { state: 'gone' } : { state: 'error' });
      });
    return () => {
      live = false;
    };
  }, [docId, q, attempt]);

  // Keep the opener after the parent clears it on close: focus goes back to it on unmount.
  const returnRef = useRef<HTMLElement | null>(null);
  if (returnFocusTo) returnRef.current = returnFocusTo;

  const title = load.state === 'ok' ? load.doc.title : cite?.title ?? t('sheet.loading');
  const platform = (load.state === 'ok' ? load.doc.platform : cite?.platform) ?? 'confluence';
  const pfLabel = PLATFORM_LABEL[platform] ?? platform;

  return (
    <Sheet open={open} onOpenChange={(o) => (o ? undefined : onClose())}>
      {open && cite ? (
        <SheetContent
          width="lg"
          title={title}
          closeLabel={t('sheet.close')}
          description={
            <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-0.5">
              <PlatformIcon platform={platform} tone="platform" size="sm" />
              <span aria-hidden>·</span>
              <span>{placeLabel(platform, load.state === 'ok' ? load.doc.container_name : cite.container_name)}</span>
            </span>
          }
          data-testid="sources-sheet"
          onCloseAutoFocus={(e) => {
            const el = returnRef.current;
            if (el && el.isConnected) {
              e.preventDefault();
              el.focus();
            }
          }}
          footer={
            total > 1 ? (
              <div className="flex w-full items-center justify-between gap-2">
                <IconButton icon={ChevronLeft} label={t('sheet.prev')} variant="secondary" disabled={index === 0} onClick={() => onIndexChange(index! - 1)} />
                <span className="text-caption tabular-nums text-muted" data-testid="sheet-position">
                  {t('sheet.position', { i: index! + 1, n: total })}
                </span>
                <IconButton icon={ChevronRight} label={t('sheet.next')} variant="secondary" disabled={index === total - 1} onClick={() => onIndexChange(index! + 1)} />
              </div>
            ) : undefined
          }
        >
          {load.state === 'loading' ? (
            <SkeletonText lines={6} label={t('sheet.loading')} />
          ) : load.state === 'gone' ? (
            <div className="flex flex-col items-center px-4 py-12 text-center" data-testid="sheet-unavailable">
              <span aria-hidden className="mb-3 inline-flex h-10 w-10 items-center justify-center rounded-lg border border-line bg-raised text-muted">
                <EyeOff className="h-5 w-5" />
              </span>
              <p className="text-heading-sm text-fg">{t('sheet.unavailable')}</p>
              <p className="mt-1 max-w-sm text-body-sm text-muted">{t('sheet.unavailableHint')}</p>
            </div>
          ) : load.state === 'error' ? (
            <div role="alert" className="flex flex-col items-center px-4 py-12 text-center">
              <TriangleAlert aria-hidden className="mb-3 h-6 w-6 text-warn" />
              <p className="text-body-sm text-fg">{t('sheet.error')}</p>
              <Button className="mt-4" size="sm" icon={RefreshCw} onClick={() => setAttempt((a) => a + 1)}>
                {t('error.retry')}
              </Button>
            </div>
          ) : (
            <DocBody doc={load.doc} sentences={sentences} lang={lang} pfLabel={pfLabel} />
          )}
        </SheetContent>
      ) : null}
    </Sheet>
  );
}

function DocBody({ doc, sentences, lang, pfLabel }: { doc: DocView; sentences: string[]; lang: 'en' | 'zh'; pfLabel: string }) {
  const t = useMessages(userMessages);
  const range = highlightRange(doc.excerpt, sentences);
  const href = safeHref(doc.deep_link);
  const meta: [string, React.ReactNode][] = [
    [t('sheet.platform'), <PlatformIcon key="p" platform={doc.platform} tone="platform" size="sm" />],
    [t('sheet.location'), placeLabel(doc.platform, doc.container_name)],
    [t('sheet.version'), <span key="v" className="tabular-nums">{versionText(doc.version, lang, t, doc.updated_at)}</span>],
    [t('sheet.author'), doc.author?.name ?? t('sheet.authorNone')],
    [t('sheet.updated'), <span key="u" className="tabular-nums">{fmtWhen(doc.updated_at, lang)}</span>],
    [
      t('sheet.checked'),
      <span key="c" className="inline-flex items-center gap-1 tabular-nums">
        <ShieldCheck aria-hidden className="h-3.5 w-3.5 text-ok" />
        {fmtWhen(doc.lineage.authz_checked_at, lang)}
      </span>,
    ],
  ];
  return (
    <div className="space-y-6">
      <dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
        {meta.map(([k, v]) => (
          <div key={k} className="min-w-0">
            <dt className="text-micro font-semibold uppercase tracking-wide text-subtle">{k}</dt>
            <dd className="mt-0.5 truncate text-body-sm text-fg">{v}</dd>
          </div>
        ))}
      </dl>
      {doc.lineage.live_read ? (
        <p className="inline-flex items-center gap-1.5 text-caption text-accent">
          <RefreshCw aria-hidden className="h-3.5 w-3.5" />
          {t('sheet.liveRead')}
        </p>
      ) : null}

      <section aria-labelledby="sheet-passage">
        <h3 id="sheet-passage" className="mb-2 text-micro font-semibold uppercase tracking-wider text-subtle">
          {t('sheet.passage')}
        </h3>
        <blockquote className="rounded-lg border border-line bg-surface px-4 py-3.5 text-body-sm leading-7 text-fg" data-testid="sheet-excerpt">
          <p className="whitespace-pre-wrap">
            {range ? (
              <>
                {doc.excerpt.slice(0, range.start)}
                <mark className="rounded-sm bg-accent/20 px-0.5 text-fg shadow-[inset_0_-2px_0_rgb(var(--accent))]" data-testid="sheet-highlight">
                  {doc.excerpt.slice(range.start, range.end)}
                </mark>
                {doc.excerpt.slice(range.end)}
              </>
            ) : (
              doc.excerpt
            )}
          </p>
        </blockquote>
        <p className="mt-2 text-caption text-muted">
          {range ? t('sheet.highlightNote') : t('sheet.noHighlight')}
          {doc.excerpt_truncated ? ` ${t('sheet.truncated')}` : ''}
        </p>
      </section>

      {doc.links.length ? (
        <section aria-labelledby="sheet-links">
          <h3 id="sheet-links" className="mb-2 text-micro font-semibold uppercase tracking-wider text-subtle">
            {t('sheet.links')}
          </h3>
          <ul className="divide-y divide-line rounded-lg border border-line">
            {doc.links.map((l) => (
              <li key={l.doc_id} className="flex min-w-0 items-center gap-2 px-3 py-2 text-body-sm">
                <PlatformIcon platform={l.platform} tone="platform" size="sm" showLabel={false} />
                <span className="min-w-0 flex-1 truncate text-fg">{l.title}</span>
                <span className="shrink-0 truncate text-caption text-muted">{placeLabel(l.platform, l.container_name)}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className={cn('inline-flex h-9 items-center gap-2 rounded-md border border-line-strong bg-surface px-3.5 text-sm font-medium text-fg hover:bg-raised')}
          data-testid="sheet-open"
        >
          <ExternalLink aria-hidden className="h-4 w-4" />
          {t('sheet.open', { platform: pfLabel })}
          <span className="font-normal text-muted">{t('sheet.demo')}</span>
        </a>
      ) : null}
    </div>
  );
}
