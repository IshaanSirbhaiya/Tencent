'use client';
// Right-hand node panel (a docked side sheet, not a modal: the graph stays usable beside it).
// Tabs: Overview · Passage · Lineage · Links. Passage and lineage come from GET /ib/doc/{id}, called
// fresh every time the panel opens; a 404 there reads "Not available to you right now", the same
// words for every reason (restricted, unknown, failed live check).
import { ArrowUpRight, ChevronsRight, Expand, Loader2, RefreshCw, Workflow, X } from 'lucide-react';
import { forwardRef, useEffect, useMemo, useState } from 'react';
import { Button, IconButton, PlatformIcon, Skeleton, Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui';
import { ApiError, api } from '@/lib/api';
import { cn } from '@/lib/cn';
import { fmtDate, fmtDateTime } from '@/lib/format';
import { useMessages } from '@/lib/i18n';
import { graphMessages } from '@/lib/i18n/graph';
import type { DocView } from '@/lib/types';
import { CopyId } from './bits';
import { displayLabel, fmtCount, kindLine, placeLabel, platformName, typeName } from './format';
import type { KGraph } from './model';
import { safeHref } from '@/lib/url';

type DocState = { kind: 'idle' } | { kind: 'loading' } | { kind: 'ok'; doc: DocView } | { kind: 'gone' } | { kind: 'error' };

interface Props {
  id: string;
  graph: KGraph;
  version: number;
  /** /ib/doc is a main-session call; the compare slot's panel shows the graph fields only. */
  docEnabled: boolean;
  query: string | null;
  expanding: boolean;
  onExpand: (id: string, limit?: number) => void;
  onWorkingSet: (id: string) => void;
  onSelect: (id: string) => void;
  onClose: () => void;
  notice: string | null;
  className?: string;
  testId?: string;
}

const REL_LABEL: Record<string, string> = { contains: 'Contains', authored: 'Authored', links: 'Links to', mentions: 'Mentions' };
const REL_LABEL_IN: Record<string, string> = { contains: 'In', authored: 'Written by', links: 'Linked from', mentions: 'Mentioned in' };

export const NodePanel = forwardRef<HTMLHeadingElement, Props>(function NodePanel(
  { id, graph, version, docEnabled, query, expanding, onExpand, onWorkingSet, onSelect, onClose, notice, className, testId },
  headingRef,
) {
  const t = useMessages(graphMessages);
  const a = graph.hasNode(id) ? graph.getNodeAttributes(id) : null;
  const isDoc = a?.kind === 'doc';
  const [tab, setTab] = useState('overview');
  const [doc, setDoc] = useState<DocState>({ kind: 'idle' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => setTab('overview'), [id]);
  useEffect(() => {
    if (!isDoc || !docEnabled) {
      setDoc({ kind: 'idle' });
      return;
    }
    let live = true;
    setDoc({ kind: 'loading' });
    api
      .doc(id, query ?? undefined)
      .then((d) => live && setDoc({ kind: 'ok', doc: d }))
      .catch((e) => live && setDoc(e instanceof ApiError && e.status === 404 ? { kind: 'gone' } : { kind: 'error' }));
    return () => {
      live = false;
    };
  }, [id, isDoc, docEnabled, query, attempt]);

  const neighbours = useMemo(() => {
    if (!graph.hasNode(id)) return [] as { id: string; rel: string; out: boolean }[];
    const list: { id: string; rel: string; out: boolean }[] = [];
    graph.forEachOutEdge(id, (_e, at, _s, tgt) => list.push({ id: tgt, rel: at.kind, out: true }));
    graph.forEachInEdge(id, (_e, at, src) => list.push({ id: src, rel: at.kind, out: false }));
    return list;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, graph, version]);

  if (!a) return null;
  const title = displayLabel(a);
  const exp = a.expanded;

  return (
    <aside
      aria-labelledby={`${id}-panel-title`}
      className={cn('flex min-h-0 flex-col border-l border-line bg-surface', className)}
      data-testid={testId}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation();
          onClose();
        }
      }}
    >
      <header className="flex items-start gap-2 border-b border-line px-4 py-3">
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-1.5 text-caption text-muted">
            {a.platform ? <PlatformIcon platform={a.platform} tone="platform" size="sm" showLabel={false} /> : <span aria-hidden className="h-2 w-2 rounded-full bg-muted" />}
            <span className="truncate">{kindLine(a)}</span>
          </p>
          <h2 ref={headingRef} id={`${id}-panel-title`} tabIndex={-1} className="mt-1 break-words text-heading-sm text-fg focus:outline-none" data-testid="graph-panel-title">
            {title}
          </h2>
        </div>
        <IconButton icon={X} label={t('panel.close')} size="sm" onClick={onClose} data-testid="graph-panel-close" />
      </header>

      <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-4 py-2.5">
        <Button size="sm" variant="secondary" icon={expanding ? Loader2 : Expand} busy={expanding} onClick={() => onExpand(id)} data-testid="graph-expand">
          {expanding ? t('panel.expanding') : exp ? t('panel.expanded') : t('panel.expand')}
        </Button>
        <Button size="sm" variant="ghost" icon={Workflow} onClick={() => onWorkingSet(id)} data-testid="graph-working-set">
          {t('panel.working')}
        </Button>
        {/* "+N more" only when the API gave an exact number (degree_hidden_safe is not null). */}
        {exp && typeof exp.hidden === 'number' && exp.hidden > 0 ? (
          <span className="inline-flex items-center gap-1.5 text-caption text-muted" data-testid="graph-more">
            <span className="rounded border border-line px-1.5 py-0.5 tabular-nums">{t('panel.more', { n: fmtCount(exp.hidden) })}</span>
            {exp.limit < 300 ? (
              <button type="button" className="text-accent underline-offset-2 hover:underline" onClick={() => onExpand(id, 300)}>
                {t('panel.loadMore')}
              </button>
            ) : null}
          </span>
        ) : null}
        {notice ? (
          <p role="status" className="w-full text-caption text-muted">
            {notice}
          </p>
        ) : null}
      </div>

      <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col">
        <TabsList aria-label={t('panel.tabs')} className="px-3">
          <TabsTrigger value="overview">{t('panel.overview')}</TabsTrigger>
          {isDoc ? <TabsTrigger value="passage">{t('panel.passage')}</TabsTrigger> : null}
          {isDoc ? <TabsTrigger value="lineage">{t('panel.lineage')}</TabsTrigger> : null}
          <TabsTrigger value="links">
            {t('panel.links')}
            <span className="tabular-nums text-subtle">{neighbours.length}</span>
          </TabsTrigger>
        </TabsList>
        <div className="ds-scroll min-h-0 flex-1 overflow-y-auto px-4 pb-6">
          <TabsContent value="overview">
            <SectionHead title={t('panel.overview')} copy={id} />
            <dl className="grid grid-cols-[7.5rem_1fr] gap-x-3 gap-y-2 text-body-sm">
              <Field k={t('node.kind')} v={typeName(a)} />
              {a.platform ? <Field k={t('node.platform')} v={platformName(a.platform)} /> : null}
              {isDoc ? <Field k={t('node.place')} v={placeLabel(a.containerName, a.platform) || '—'} /> : null}
              {isDoc ? <Field k={t('node.version')} v={a.version ? <span className="text-caption tabular-nums">{shortVersion(a.version)}</span> : '—'} /> : null}
              {isDoc ? <Field k={t('node.updated')} v={<span className="tabular-nums">{fmtDateTime(a.updatedAt)}</span>} /> : null}
              {isDoc && doc.kind === 'ok' ? <Field k={t('node.author')} v={doc.doc.author?.name ?? '—'} /> : null}
              {isDoc && doc.kind === 'ok' && doc.doc.created_at ? <Field k={t('node.created')} v={<span className="tabular-nums">{fmtDate(doc.doc.created_at)}</span>} /> : null}
              {a.kind !== 'doc' && a.count !== null ? (
                <Field k={a.kind === 'platform' ? t('node.places') : t('node.items')} v={<span className="tabular-nums">{fmtCount(a.count)}</span>} />
              ) : null}
              <Field k={t('node.id')} v={<span className="break-all font-mono text-caption text-muted">{id}</span>} />
            </dl>
            {isDoc && safeHref(a.deepLink) ? (
              <a
                href={safeHref(a.deepLink) ?? undefined}
                target="_blank"
                rel="noreferrer noopener"
                className="mt-4 inline-flex h-8 items-center gap-1.5 rounded-md border border-line-strong px-3 text-body-sm font-medium text-fg hover:bg-raised"
              >
                {t('panel.open', { platform: platformName(a.platform) })}
                <ArrowUpRight aria-hidden className="h-3.5 w-3.5" />
                <span className="sr-only">(new tab)</span>
              </a>
            ) : null}
            {isDoc && !docEnabled ? <p className="mt-4 text-caption text-subtle">{t('panel.mainOnly')}</p> : null}
          </TabsContent>

          {isDoc ? (
            <TabsContent value="passage">
              <SectionHead title={t('panel.passage')} copy={id} />
              <DocBody state={doc} enabled={docEnabled} onRetry={() => setAttempt((n) => n + 1)}>
                {(d) => (
                  <figure>
                    <blockquote className="whitespace-pre-wrap rounded-md border border-line bg-bg px-3 py-2.5 text-body-sm leading-relaxed text-fg" data-testid="graph-passage">
                      {d.excerpt}
                      {d.excerpt_truncated ? <span className="text-subtle"> …</span> : null}
                    </blockquote>
                    <figcaption className="mt-2 text-caption text-muted">
                      {d.excerpt_kind === 'best_chunk' ? `Best match for “${query}”` : 'Start of the document'} · {platformName(d.platform)} · {shortVersion(d.version)} ·{' '}
                      <span className="tabular-nums">{fmtDateTime(d.updated_at)}</span>
                    </figcaption>
                  </figure>
                )}
              </DocBody>
            </TabsContent>
          ) : null}

          {isDoc ? (
            <TabsContent value="lineage">
              <SectionHead title={t('panel.lineage')} copy={id} />
              <DocBody state={doc} enabled={docEnabled} onRetry={() => setAttempt((n) => n + 1)}>
                {(d) => <Lineage d={d} query={query} />}
              </DocBody>
            </TabsContent>
          ) : null}

          <TabsContent value="links">
            <SectionHead title={t('panel.links')} copy={id} />
            {neighbours.length ? (
              <ul className="space-y-0.5" data-testid="graph-links">
                {neighbours.map((n) => {
                  const na = graph.getNodeAttributes(n.id);
                  return (
                    <li key={`${n.rel}:${n.out}:${n.id}`}>
                      <button
                        type="button"
                        onClick={() => onSelect(n.id)}
                        className="group flex min-h-9 w-full items-center gap-2 rounded px-1.5 py-1 text-left hover:bg-raised"
                      >
                        <span className="w-[5.5rem] shrink-0 text-caption text-subtle">{(n.out ? REL_LABEL : REL_LABEL_IN)[n.rel] ?? n.rel}</span>
                        {na.platform ? <PlatformIcon platform={na.platform} tone="platform" size="sm" showLabel={false} /> : <span aria-hidden className="h-2 w-2 shrink-0 rounded-full bg-muted" />}
                        <span className="min-w-0 flex-1 truncate text-body-sm text-fg">{displayLabel(na)}</span>
                        <ChevronsRight aria-hidden className="h-3.5 w-3.5 shrink-0 text-subtle opacity-0 group-hover:opacity-100" />
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="text-body-sm text-muted">{t('panel.noLinks')}</p>
            )}
            {isDoc && doc.kind === 'ok' && doc.doc.links.length ? (
              <div className="mt-4">
                <h4 className="mb-1 text-micro font-semibold uppercase tracking-wide text-subtle">From /ib/doc · linked documents you can open</h4>
                <ul className="space-y-0.5">
                  {doc.doc.links.map((l) => (
                    <li key={l.doc_id}>
                      <button
                        type="button"
                        onClick={() => (graph.hasNode(l.doc_id) ? onSelect(l.doc_id) : onExpand(id))}
                        className="flex min-h-9 w-full items-center gap-2 rounded px-1.5 py-1 text-left hover:bg-raised"
                      >
                        <span className="w-[5.5rem] shrink-0 text-caption text-subtle">{REL_LABEL[l.relation]}</span>
                        <PlatformIcon platform={l.platform} tone="platform" size="sm" showLabel={false} />
                        <span className="min-w-0 flex-1 truncate text-body-sm text-fg">{l.title}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </TabsContent>
        </div>
      </Tabs>
    </aside>
  );
});

function shortVersion(v: string): string {
  // Confluence/Drive: a number ("v6"). Jira: the last-updated time. Slack: "<ts>" or
  // "<ts>|<edits>|<hash>" (live), shown as the message date, never as a raw epoch.
  if (!v) return '—';
  if (/^\d+$/.test(v)) return `v${v}`;
  if (/^\d{4}-\d{2}-\d{2}T/.test(v)) return `Revision of ${fmtDate(v)}`;
  const slack = v.match(/^(\d{9,11})\.\d+(\|.*)?$/);
  if (slack) return `Message of ${fmtDate(new Date(Number(slack[1]) * 1000).toISOString())}`;
  return v.length > 18 ? `rev ${v.slice(0, 12)}…` : `rev ${v}`;
}

function Field({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <>
      <dt className="text-muted">{k}</dt>
      <dd className="min-w-0 text-fg">{v}</dd>
    </>
  );
}

function SectionHead({ title, copy }: { title: string; copy: string }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-2">
      <h3 className="text-micro font-semibold uppercase tracking-wide text-subtle">{title}</h3>
      <CopyId value={copy} />
    </div>
  );
}

function DocBody({ state, enabled, onRetry, children }: { state: DocState; enabled: boolean; onRetry: () => void; children: (d: DocView) => React.ReactNode }) {
  const t = useMessages(graphMessages);
  if (!enabled) return <p className="text-body-sm text-muted">{t('panel.mainOnly')}</p>;
  if (state.kind === 'loading' || state.kind === 'idle')
    return (
      <div className="space-y-2" role="status" aria-label="Loading">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-11/12" />
        <Skeleton className="h-4 w-4/5" />
      </div>
    );
  if (state.kind === 'gone')
    return (
      <div className="rounded-md border border-line bg-bg px-3 py-3" data-testid="graph-doc-unavailable">
        <p className="text-body-sm font-medium text-fg">{t('panel.unavailable')}</p>
        <p className="mt-0.5 text-caption text-muted">{t('panel.unavailableHint')}</p>
      </div>
    );
  if (state.kind === 'error')
    return (
      <div className="rounded-md border border-line bg-bg px-3 py-3">
        <p className="text-body-sm text-fg">{t('panel.failed')}</p>
        <Button size="sm" variant="secondary" icon={RefreshCw} onClick={onRetry} className="mt-2">
          {t('panel.retry')}
        </Button>
      </div>
    );
  return <>{children(state.doc)}</>;
}

/** value ← passage ← document vX ← connector ← synced at, as pills (an ordered list for screen readers). */
function Lineage({ d, query }: { d: DocView; query: string | null }) {
  const steps: { k: string; v: React.ReactNode; mono?: boolean }[] = [
    { k: query ? 'Value' : 'Excerpt', v: query ? `“${query.length > 28 ? `${query.slice(0, 26)}…` : query}”` : d.excerpt_kind === 'best_chunk' ? 'best match' : 'document start' },
    { k: 'Passage', v: d.excerpt_kind === 'best_chunk' ? 'best chunk' : 'first 1,200 chars' },
    { k: 'Document', v: shortVersion(d.version) },
    { k: 'Connector', v: platformName(d.lineage.connector) },
    { k: 'Synced', v: d.lineage.last_sync_at ? fmtDateTime(d.lineage.last_sync_at) : '—' },
  ];
  return (
    <div>
      <ol className="flex flex-wrap items-center gap-y-2" aria-label="Provenance, from the value back to the source" data-testid="graph-lineage">
        {steps.map((s, i) => (
          <li key={s.k} className="flex items-center">
            {i > 0 ? (
              <span aria-hidden className="px-1.5 text-subtle">
                ←
              </span>
            ) : null}
            <span className="inline-flex h-7 items-center gap-1.5 rounded-full border border-line bg-bg px-2.5 text-caption">
              <span className="text-subtle">{s.k}</span>
              <span className={cn('text-fg', s.mono ? 'font-mono' : 'tabular-nums')}>{s.v}</span>
            </span>
          </li>
        ))}
      </ol>
      <dl className="mt-4 grid grid-cols-[9rem_1fr] gap-x-3 gap-y-2 text-body-sm">
        <dt className="text-muted">Content checked</dt>
        <dd className="tabular-nums text-fg">{fmtDateTime(d.lineage.content_checked_at)}</dd>
        <dt className="text-muted">Access checked</dt>
        <dd className="tabular-nums text-fg">{fmtDateTime(d.lineage.authz_checked_at)}</dd>
        <dt className="text-muted">Re-read live</dt>
        <dd className="text-fg">{d.lineage.live_read ? 'Yes: the source had a newer version' : 'No: the indexed version is current'}</dd>
        <dt className="text-muted">Place</dt>
        <dd className="text-fg">{placeLabel(d.container_name, d.platform)}</dd>
      </dl>
      <p className="mt-3 text-caption text-subtle">From GET /ib/doc · {d.scope_note}</p>
    </div>
  );
}
