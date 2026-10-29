'use client';
// The sources sheet for a Compare column. Each column belongs to a separately signed-in person, so
// the passage (GET /ib/doc, main session only) is fetched ONLY when the main session is that same
// person; fetching another person's source under the main session would check the wrong identity
// and record a document view for the wrong person. Otherwise the sheet shows what that person was
// already shown: the citation itself and the sentences of their answer that point at it.
//
// Visual language follows components/user/SourcesSheet (title, platform, place, version, the live
// check time, the highlighted passage). Esc closes and focus returns to the chip that opened it.
import { ChevronLeft, ChevronRight, ExternalLink, RefreshCw, ShieldCheck, TriangleAlert } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { PlatformIcon } from '@/components/PlatformIcon';
import { IconButton, Sheet, SheetContent, SkeletonText } from '@/components/ui';
import { fmtWhen, highlightRange, sentencesFor, versionText } from '@/components/user/answerText';
import { api } from '@/lib/api';
import { PLATFORM_LABEL } from '@/lib/format';
import { useLang, useMessages } from '@/lib/i18n';
import { presenterMessages } from '@/lib/i18n/presenter';
import { userMessages } from '@/lib/i18n/user';
import { safeHref } from '@/lib/url';
import type { AskResponse, DocView } from '@/lib/types';

type Load = { state: 'idle' } | { state: 'loading' } | { state: 'ok'; doc: DocView } | { state: 'none' };

export interface CompareSourceSheetProps {
  response: AskResponse | null;
  index: number | null;
  /** The column's signed-in person, and whether the main session is that same person. */
  personName: string;
  canFetchPassage: boolean;
  onIndexChange: (i: number) => void;
  onClose: () => void;
  returnFocusTo?: HTMLElement | null;
}

function Highlighted({ text, sentences, testId }: { text: string; sentences: string[]; testId: string }) {
  const range = highlightRange(text, sentences);
  return (
    <p className="whitespace-pre-wrap" data-testid={testId}>
      {range ? (
        <>
          {text.slice(0, range.start)}
          <mark className="rounded-sm bg-accent/20 px-0.5 text-fg shadow-[inset_0_-2px_0_rgb(var(--accent))]" data-testid="sheet-highlight">
            {text.slice(range.start, range.end)}
          </mark>
          {text.slice(range.end)}
        </>
      ) : (
        text
      )}
    </p>
  );
}

export function CompareSourceSheet({ response, index, personName, canFetchPassage, onIndexChange, onClose, returnFocusTo }: CompareSourceSheetProps) {
  const t = useMessages(presenterMessages);
  const tu = useMessages(userMessages);
  const { lang } = useLang();
  const open = !!response && index !== null && index >= 0 && index < response.citations.length;
  const cite = open ? response!.citations[index!] : null;
  const total = response?.citations.length ?? 0;
  const sentences = cite && response ? sentencesFor(response.answer, cite.tag) : [];
  const q = sentences.join(' ').slice(0, 500);
  const docId = open && canFetchPassage ? cite?.doc_id : undefined;
  const [load, setLoad] = useState<Load>({ state: 'idle' });

  useEffect(() => {
    if (!docId) {
      setLoad({ state: 'idle' });
      return;
    }
    let live = true;
    setLoad({ state: 'loading' });
    api
      .doc(docId, q || undefined)
      .then((doc) => live && setLoad({ state: 'ok', doc }))
      // Any failure: show what the person was already shown (never a reason, never "hidden").
      .catch(() => live && setLoad({ state: 'none' }));
    return () => {
      live = false;
    };
  }, [docId, q]);

  const returnRef = useRef<HTMLElement | null>(null);
  if (returnFocusTo) returnRef.current = returnFocusTo;

  if (!open || !cite) return <Sheet open={false} />;
  const doc = load.state === 'ok' ? load.doc : null;
  const pfLabel = PLATFORM_LABEL[cite.platform] ?? cite.platform;
  const href = safeHref(doc?.deep_link ?? cite.deep_link);
  const checkedAt = doc?.lineage.authz_checked_at ?? cite.authz_checked_at;
  const liveRead = doc ? doc.lineage.live_read : !!cite.live_read;

  const meta: [string, React.ReactNode][] = [
    [tu('sheet.platform'), <PlatformIcon key="p" platform={cite.platform} tone="platform" size="sm" />],
    [tu('sheet.location'), doc?.container_name ?? cite.container_name],
    [tu('sheet.version'), <span key="v" className="tabular-nums">{versionText(doc?.version ?? cite.version, lang, tu)}</span>],
    [tu('sheet.updated'), <span key="u" className="tabular-nums">{fmtWhen(doc?.updated_at ?? cite.updated_at, lang)}</span>],
    [
      tu('sheet.checked'),
      <span key="c" className="inline-flex items-center gap-1 tabular-nums">
        <ShieldCheck aria-hidden className="h-3.5 w-3.5 text-ok" />
        {fmtWhen(checkedAt, lang)}
      </span>,
    ],
  ];

  return (
    <Sheet open onOpenChange={(o) => (o ? undefined : onClose())}>
      <SheetContent
        width="lg"
        title={doc?.title ?? cite.title}
        closeLabel={t('sheet.close')}
        description={
          <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="font-semibold text-fg">{t('sheet.source', { n: index! + 1 })}</span>
            <span aria-hidden>·</span>
            <PlatformIcon platform={cite.platform} tone="platform" size="sm" />
            <span aria-hidden>·</span>
            <span>{doc?.container_name ?? cite.container_name}</span>
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
              <IconButton icon={ChevronLeft} label={tu('sheet.prev')} variant="secondary" disabled={index === 0} onClick={() => onIndexChange(index! - 1)} />
              <span className="text-caption tabular-nums text-muted" data-testid="sheet-position">
                {tu('sheet.position', { i: index! + 1, n: total })}
              </span>
              <IconButton icon={ChevronRight} label={tu('sheet.next')} variant="secondary" disabled={index === total - 1} onClick={() => onIndexChange(index! + 1)} />
            </div>
          ) : undefined
        }
      >
        <div className="space-y-6">
          <dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
            {meta.map(([k, v]) => (
              <div key={k} className="min-w-0">
                <dt className="text-micro font-semibold uppercase tracking-wide text-subtle">{k}</dt>
                <dd className="mt-0.5 truncate text-body-sm text-fg">{v}</dd>
              </div>
            ))}
          </dl>
          {liveRead ? (
            <p className="inline-flex items-center gap-1.5 text-caption text-accent">
              <RefreshCw aria-hidden className="h-3.5 w-3.5" />
              {tu('sheet.liveRead')}
            </p>
          ) : null}
          {cite.stale ? (
            <p className="inline-flex items-center gap-1.5 text-caption text-warn">
              <TriangleAlert aria-hidden className="h-3.5 w-3.5" />
              {tu('answer.stale')}
            </p>
          ) : null}

          {load.state === 'loading' ? <SkeletonText lines={5} label={t('sheet.loading')} /> : null}

          {doc ? (
            <section aria-labelledby="cmp-sheet-passage">
              <h3 id="cmp-sheet-passage" className="mb-2 text-micro font-semibold uppercase tracking-wider text-subtle">
                {t('sheet.passage')}
              </h3>
              <blockquote className="rounded-lg border border-line bg-surface px-4 py-3.5 text-body-sm leading-7 text-fg">
                <Highlighted text={doc.excerpt} sentences={sentences} testId="sheet-excerpt" />
              </blockquote>
              <p className="mt-2 text-caption text-muted">{t('sheet.passageFor', { name: personName })}</p>
            </section>
          ) : null}

          {load.state !== 'loading' && sentences.length ? (
            <section aria-labelledby="cmp-sheet-cited">
              <h3 id="cmp-sheet-cited" className="mb-2 text-micro font-semibold uppercase tracking-wider text-subtle">
                {t('sheet.cited')}
              </h3>
              <blockquote className="rounded-lg border border-line bg-surface px-4 py-3.5 text-body-sm leading-7 text-fg" data-testid="sheet-cited">
                {doc ? (
                  <p className="whitespace-pre-wrap">{sentences.join(' ')}</p>
                ) : (
                  <p className="whitespace-pre-wrap">
                    <mark className="rounded-sm bg-accent/20 px-0.5 text-fg shadow-[inset_0_-2px_0_rgb(var(--accent))]" data-testid="sheet-highlight">
                      {sentences.join(' ')}
                    </mark>
                  </p>
                )}
              </blockquote>
              <p className="mt-2 text-caption text-muted">{t('sheet.citedNote')}</p>
            </section>
          ) : null}

          {href ? (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-9 items-center gap-2 rounded-md border border-line-strong bg-surface px-3.5 text-sm font-medium text-fg hover:bg-raised"
              data-testid="sheet-open"
            >
              <ExternalLink aria-hidden className="h-4 w-4" />
              {t('sheet.open', { platform: pfLabel })}
              <span className="font-normal text-muted">{tu('sheet.demo')}</span>
            </a>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}
