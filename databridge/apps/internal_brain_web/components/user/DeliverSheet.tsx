'use client';
// "Turn this answer into": a table, a chart or a short analysis built from the asker's own answer
// and the cited sources they can still open (POST /ib/deliverables). Everything shown comes from
// the API, already checked on the server; nothing here computes, converts or rewrites a value.
// Rule 4: a 404 is one sentence ("nothing to build") whatever the reason. Fable review §6: one
// metadata line, a neutral pill (amber only when something was removed, never green), the source
// chip beside every x label and the value written on every bar, Recharts defaults stripped.
//
// Round-3 review: M1 the server's text (title, label, cells, sentences, scope note, source titles)
// is English, so it carries lang="en" (the fixed draft label is translated); M2 a table row whose
// cells all cite the same sources shows its chips once, at the end of the row; M3 a chip here does
// not open a second modal: it moves focus to that source in this panel's "Cited in" list; M6 after
// Regenerate / Try again focus goes to the loading line, then to the new title.
import { ChartColumn, Check, Copy, Download, FileText, Info, RefreshCw, Table2, TriangleAlert, type LucideIcon } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Bar, BarChart, LabelList, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipProps } from 'recharts';
import { PlatformIcon } from '@/components/PlatformIcon';
import { Button, Sheet, SheetContent, Skeleton, Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui';
import { ApiError, analyticsApi, deliverApi } from '@/lib/api';
import { cn } from '@/lib/cn';
import { placeLabel } from '@/lib/format';
import { useLang, useMessages } from '@/lib/i18n';
import { deliverMessages, type DeliverKey } from '@/lib/i18n/deliver';
import type {
  AskResponse,
  Citation,
  DeliverAnalysis,
  DeliverChart,
  DeliverCitation,
  DeliverTable,
  Deliverable,
  DeliverableKind,
  DeliverablesStatus,
} from '@/lib/types';
import { CitationChip } from './CitationChip';
import { toast } from './Toast';

type T = (key: DeliverKey, vars?: Record<string, string | number>) => string;
export type OnCite = (index: number, opener?: HTMLElement) => void;
/** Moves focus to a source's entry in this panel's "Cited in" list (by its answer number). */
type Jump = (n: number) => void;

/** The server's fixed draft label; any other label is shown as returned (English). */
const SERVER_DRAFT_LABEL = 'Draft for your review';

const KIND_ICON: Record<DeliverableKind, LucideIcon> = { table: Table2, chart: ChartColumn, analysis: FileText };
const KIND_ORDER: DeliverableKind[] = ['table', 'chart', 'analysis'];

/* ------------------------------------------------------------------------------- status */

let statusOnce: Promise<DeliverablesStatus | null> | null = null;
/** /ib/status's deliverables block, fetched once per page load (the switch needs an IB restart). */
export function deliverablesStatus(): Promise<DeliverablesStatus | null> {
  if (!statusOnce) {
    statusOnce = analyticsApi
      .status()
      .then((s) => (s.deliverables && s.deliverables.enabled ? s.deliverables : null))
      .catch(() => {
        statusOnce = null; // try again next time
        return null;
      });
  }
  return statusOnce;
}

function useDeliverables(): DeliverablesStatus | null {
  const [st, setSt] = useState<DeliverablesStatus | null>(null);
  useEffect(() => {
    let live = true;
    void deliverablesStatus().then((s) => live && setSt(s));
    return () => {
      live = false;
    };
  }, []);
  return st;
}

/* ---------------------------------------------------------------------------------- row */

/** The chips under a released answer: only the kinds this server offers (no model: no Analysis). */
// `onCite` is accepted for the AnswerCard's API, but chips inside the panel never open a second
// modal over it (review M3); they point at the panel's own source list instead.
export function DeliverRow({ response }: { response: AskResponse; onCite?: OnCite }) {
  const t = useMessages(deliverMessages);
  const st = useDeliverables();
  const [kind, setKind] = useState<DeliverableKind | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const headingId = useId();
  if (!st || !st.kinds.length) return null;
  const kinds = KIND_ORDER.filter((k) => st.kinds.includes(k));
  const btn =
    'inline-flex h-8 items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 text-caption font-medium text-fg transition-colors duration-fast hover:border-line-strong hover:bg-raised';
  return (
    <section aria-labelledby={headingId} className="flex flex-wrap items-center gap-x-2 gap-y-1.5" data-testid="deliver-row">
      <h3 id={headingId} className="mr-1 text-micro font-semibold uppercase tracking-wider text-subtle">
        {t('deliver.heading')}
      </h3>
      {kinds.map((k) => {
        const Icon = KIND_ICON[k];
        return (
          <button
            key={k}
            type="button"
            className={btn}
            data-testid={`deliver-${k}`}
            onClick={(e) => {
              opener.current = e.currentTarget;
              setKind(k);
            }}
          >
            <Icon aria-hidden className="h-3.5 w-3.5 text-muted" />
            {t(`deliver.${k}` as DeliverKey)}
          </button>
        );
      })}
      <span className="basis-full text-caption text-subtle sm:basis-auto">{t('deliver.hint')}</span>
      <DeliverSheet
        response={response}
        kinds={kinds}
        kind={kind}
        onKind={setKind}
        onClose={() => setKind(null)}
        returnFocusTo={opener.current}
      />
    </section>
  );
}

/* -------------------------------------------------------------------------------- sheet */

type Fail = 'nothing' | 'busy' | 'error';
type Load = { state: 'loading'; since: number } | { state: 'ok'; d: Deliverable } | { state: 'fail'; kind: Fail };
const failOf = (e: unknown): Fail =>
  e instanceof ApiError && e.status === 404 ? 'nothing' : e instanceof ApiError && e.status === 429 ? 'busy' : 'error';

export interface DeliverSheetProps {
  response: AskResponse;
  kinds: DeliverableKind[];
  kind: DeliverableKind | null;
  onKind: (k: DeliverableKind) => void;
  onClose: () => void;
  returnFocusTo?: HTMLElement | null;
  onCite?: OnCite;
  /** already-built deliverables for this answer (the presenter's warm-up cache): no request */
  preloaded?: Partial<Record<DeliverableKind, Deliverable>>;
}

export function DeliverSheet({ response, kinds, kind, onKind, onClose, returnFocusTo, preloaded }: DeliverSheetProps) {
  const t = useMessages(deliverMessages);
  const returnRef = useRef<HTMLElement | null>(null);
  if (returnFocusTo) returnRef.current = returnFocusTo;
  const open = kind !== null;
  return (
    <Sheet open={open} onOpenChange={(o) => (o ? undefined : onClose())}>
      {open ? (
        <SheetContent
          width="xl"
          title={t('deliver.title')}
          description={t('deliver.desc')}
          closeLabel={t('deliver.close')}
          data-testid="deliver-sheet"
          onCloseAutoFocus={(e) => {
            const el = returnRef.current;
            if (el && el.isConnected) {
              e.preventDefault();
              el.focus();
            }
          }}
        >
          <Panel response={response} kinds={kinds} kind={kind} onKind={onKind} preloaded={preloaded} />
        </SheetContent>
      ) : null}
    </Sheet>
  );
}

function Panel({
  response,
  kinds,
  kind,
  onKind,
  preloaded,
}: {
  response: AskResponse;
  kinds: DeliverableKind[];
  kind: DeliverableKind;
  onKind: (k: DeliverableKind) => void;
  preloaded?: Partial<Record<DeliverableKind, Deliverable>>;
}) {
  const t = useMessages(deliverMessages);
  const [loads, setLoads] = useState<Partial<Record<DeliverableKind, Load>>>(() => {
    const out: Partial<Record<DeliverableKind, Load>> = {};
    for (const k of kinds) {
      const d = preloaded?.[k];
      if (d && d.req_id === response.request_id) out[k] = { state: 'ok', d };
    }
    return out;
  });
  const [attempt, setAttempt] = useState<Partial<Record<DeliverableKind, number>>>({});
  // one request per kind and attempt: each call writes sealed ledger records, so React's
  // development double effect must not send it twice (the in-flight promise is reused)
  const inflight = useRef(new Map<string, Promise<Deliverable>>());

  useEffect(() => {
    const k = kind;
    if (loads[k] && loads[k]!.state !== 'loading') return;
    const key = `${response.request_id}:${k}:${attempt[k] ?? 0}`;
    let p = inflight.current.get(key);
    if (!p) {
      p = deliverApi.create(response.request_id, k);
      inflight.current.set(key, p);
    }
    let live = true;
    if (!loads[k]) setLoads((s) => ({ ...s, [k]: { state: 'loading', since: Date.now() } }));
    p.then((d) => live && setLoads((s) => ({ ...s, [k]: { state: 'ok', d } }))).catch(
      (e) => live && setLoads((s) => ({ ...s, [k]: { state: 'fail', kind: failOf(e) } })),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, response.request_id, attempt]);

  // the kind whose Regenerate / Try again was pressed: its views take focus as they mount (M6)
  const [focusKind, setFocusKind] = useState<DeliverableKind | null>(null);
  const retry = (k: DeliverableKind) => {
    setFocusKind(k);
    setLoads((s) => ({ ...s, [k]: { state: 'loading', since: Date.now() } }));
    setAttempt((a) => ({ ...a, [k]: (a[k] ?? 0) + 1 }));
  };

  return (
    <Tabs value={kind} onValueChange={(v) => onKind(v as DeliverableKind)}>
      <TabsList aria-label={t('deliver.kinds')}>
        {kinds.map((k) => {
          const Icon = KIND_ICON[k];
          return (
            <TabsTrigger key={k} value={k} data-testid={`deliver-tab-${k}`}>
              <Icon aria-hidden className="h-3.5 w-3.5" />
              {t(`deliver.${k}` as DeliverKey)}
            </TabsTrigger>
          );
        })}
      </TabsList>
      {kinds.map((k) => (
        <TabsContent key={k} value={k} className="pt-4" data-testid={`deliver-panel-${k}`}>
          <View
            response={response}
            kind={k}
            load={loads[k] ?? { state: 'loading', since: Date.now() }}
            onRetry={() => retry(k)}
            onShowTable={kinds.includes('table') && k !== 'table' ? () => onKind('table') : undefined}
            focus={focusKind === k}
            onFocused={() => setFocusKind(null)}
          />
        </TabsContent>
      ))}
    </Tabs>
  );
}

/* --------------------------------------------------------------------------------- view */

function useElapsed(since: number | null): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (since === null) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [since]);
  return since === null ? 0 : now - since;
}

/** Focuses `ref` once when it mounts with `on` set (after a retry), then calls `done` if given. */
function useFocusOnMount(ref: React.RefObject<HTMLElement | null>, on: boolean | undefined, done?: () => void) {
  useEffect(() => {
    if (!on) return;
    ref.current?.focus();
    done?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}

function Loading({ kind, since, t, focus }: { kind: DeliverableKind; since: number; t: T; focus?: boolean }) {
  const elapsed = useElapsed(since);
  const ref = useRef<HTMLDivElement>(null);
  useFocusOnMount(ref, focus);
  return (
    <div ref={ref} tabIndex={-1} role="status" data-testid="deliver-loading" className="flex flex-col gap-4 outline-none">
      <p className="text-body-sm text-muted">
        {elapsed > 8000 ? t('deliver.slow') : t('deliver.loading', { kind: t(`deliver.kind.${kind}` as DeliverKey) })}
      </p>
      {kind === 'chart' ? (
        <div aria-hidden className="flex h-48 items-end gap-6 px-6">
          {[60, 60, 75].map((h, i) => (
            <div key={i} className="w-14" style={{ height: `${h}%` }}>
              <Skeleton className="h-full w-full motion-reduce:animate-none" />
            </div>
          ))}
        </div>
      ) : (
        <div aria-hidden className="flex flex-col gap-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className={cn('h-4 motion-reduce:animate-none', kind === 'analysis' && i === 3 ? 'w-2/3' : 'w-full')} />
          ))}
        </div>
      )}
    </div>
  );
}

function Notice({ testId, text, hint, icon: Icon = Info, action }: { testId: string; text: string; hint?: string; icon?: LucideIcon; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center px-4 py-12 text-center" data-testid={testId}>
      <span aria-hidden className="mb-3 inline-flex h-10 w-10 items-center justify-center rounded-lg border border-line bg-raised text-muted">
        <Icon className="h-5 w-5" />
      </span>
      <p className="max-w-md text-heading-sm text-fg">{text}</p>
      {hint ? <p className="mt-1 max-w-sm text-body-sm text-muted">{hint}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

function View({
  response,
  kind,
  load,
  onRetry,
  onShowTable,
  focus,
  onFocused,
}: {
  response: AskResponse;
  kind: DeliverableKind;
  load: Load;
  onRetry: () => void;
  onShowTable?: () => void;
  focus?: boolean;
  onFocused?: () => void;
}) {
  const t = useMessages(deliverMessages);
  if (load.state === 'loading') return <Loading kind={kind} since={load.since} t={t} focus={focus} />;
  if (load.state !== 'ok' || load.d.status === 'needs_model' || load.d.status === 'insufficient' || !load.d.content)
    return <Settled key={load.state} kind={kind} load={load} onRetry={onRetry} onShowTable={onShowTable} focus={focus} onFocused={onFocused} />;
  return <Ready response={response} d={load.d} onRetry={onRetry} focus={focus} onFocused={onFocused} />;
}

/** The states without a deliverable (nothing, error, needs the model, insufficient). */
function Settled({
  load,
  onRetry,
  onShowTable,
  focus,
  onFocused,
}: {
  kind: DeliverableKind;
  load: Load;
  onRetry: () => void;
  onShowTable?: () => void;
  focus?: boolean;
  onFocused?: () => void;
}) {
  const t = useMessages(deliverMessages);
  const ref = useRef<HTMLDivElement>(null);
  useFocusOnMount(ref, focus, onFocused);
  return (
    <div ref={ref} tabIndex={-1} className="outline-none">
      <SettledBody load={load} onRetry={onRetry} onShowTable={onShowTable} t={t} />
    </div>
  );
}

function SettledBody({ load, onRetry, onShowTable, t }: { load: Load; onRetry: () => void; onShowTable?: () => void; t: T }) {
  if (load.state === 'loading') return null;
  if (load.state === 'fail') {
    if (load.kind === 'nothing') return <Notice testId="deliver-nothing" text={t('deliver.nothing')} hint={t('deliver.nothingHint')} />;
    return (
      <div role="alert">
        <Notice
          testId="deliver-error"
          icon={TriangleAlert}
          text={load.kind === 'busy' ? t('deliver.busy') : t('deliver.error')}
          action={
            <Button size="sm" icon={RefreshCw} onClick={onRetry}>
              {t('deliver.retry')}
            </Button>
          }
        />
      </div>
    );
  }
  const d = load.d;
  const tableBtn = onShowTable ? (
    <Button size="sm" icon={Table2} onClick={onShowTable} data-testid="deliver-show-table">
      {t('deliver.showTable')}
    </Button>
  ) : null;
  // no_model: no model is connected on this build (never "it ran and failed"); otherwise it ran and
  // nothing it wrote passed the source check (nothing_passed).
  if (d.status === 'needs_model')
    return <Notice testId="deliver-needs-model" text={t(d.fallback === 'no_model' ? 'deliver.noModel' : 'deliver.needsModel')} action={tableBtn} />;
  return <Notice testId="deliver-insufficient" text={t('deliver.insufficient')} action={tableBtn} />;
}

/* -------------------------------------------------------------------------------- ready */

/** The answer's own number for a tag (the same numbers as its chips). */
function useNumbering(response: AskResponse, d: Deliverable) {
  return useMemo(() => {
    const byTag = new Map<string, { n: number; c?: Citation; i: number; dc?: DeliverCitation }>();
    response.citations.forEach((c, i) => byTag.set(c.tag, { n: i + 1, c, i }));
    for (const dc of d.citations) {
      const cur = byTag.get(dc.tag);
      byTag.set(dc.tag, { n: dc.n || cur?.n || 0, c: cur?.c, i: cur?.i ?? -1, dc });
    }
    return byTag;
  }, [response, d]);
}

type Numbering = ReturnType<typeof useNumbering>;

function Chips({ tags, num, onJump }: { tags: string[]; num: Numbering; onJump?: Jump }) {
  const { lang } = useLang();
  // the chip's own label is UI text, inside content marked lang="en"
  return (
    <span lang={lang}>
      {tags.map((tag) => {
        const e = num.get(tag);
        if (!e) return null;
        if (e.c && onJump) return <CitationChip key={tag} citation={e.c} n={e.n} onOpen={() => onJump(e.n)} className="ml-1" />;
        return (
          <span
            key={tag}
            data-testid="deliver-cite"
            className="ml-1 inline-flex h-5 min-w-5 items-center justify-center rounded border border-line-strong bg-raised px-1 align-middle text-caption font-semibold tabular-nums text-muted"
            aria-label={`[${e.n}] ${e.dc?.title ?? ''}`}
          >
            {e.n}
          </span>
        );
      })}
    </span>
  );
}

function builtBy(d: Deliverable, num: Numbering, t: T): string {
  const m = d.model;
  if (d.generated === 'model' && m) return m.fallback_used ? t('deliver.by.fallbackModel', { model: m.model }) : t('deliver.by.model', { model: m.model });
  const from = (d.content as { from_tag?: string } | null)?.from_tag;
  const n = from ? num.get(from)?.n ?? 0 : 0;
  if (m?.model === 'source-table-v1' && n) return t('deliver.by.copied', { n });
  if (m?.model === 'source-chart-v1' && n) return t('deliver.by.charted', { n });
  return t('deliver.by.quoted');
}

function MetaLine({ d, num, t }: { d: Deliverable; num: Numbering; t: T }) {
  const c = d.check;
  const isModel = d.generated === 'model';
  const sentences = d.kind === 'analysis';
  const total = sentences ? c.items_proposed : c.values_proposed;
  const kept = sentences ? c.items_kept : c.values_kept;
  const removed = c.items_dropped;
  const full = removed ? (removed === 1 ? t('deliver.removed.one') : t('deliver.removed.many', { n: removed })) : '';
  const pill = isModel && total > 0;
  const pillText = sentences ? t('deliver.pillSentences', { kept, total }) : t('deliver.pill', { kept, total });
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-line pb-3 text-caption text-muted" data-testid="deliver-meta">
      {d.label === SERVER_DRAFT_LABEL ? (
        <span data-testid="deliver-label">{t('deliver.draftLabel')}</span>
      ) : (
        <span data-testid="deliver-label" lang="en">
          {d.label}
        </span>
      )}
      <span aria-hidden className="text-subtle">
        ·
      </span>
      <span data-testid="deliver-by">{builtBy(d, num, t)}</span>
      {pill ? (
        <>
          <span aria-hidden className="text-subtle">
            ·
          </span>
          <span
            data-testid="deliver-pill"
            title={full || undefined}
            aria-label={full ? `${pillText}. ${full}` : pillText}
            className={cn(
              'inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 font-medium tabular-nums',
              removed ? 'border-warn/50 bg-warn/10 text-warn' : 'border-line-strong bg-raised text-fg',
            )}
          >
            {pillText}
            {removed ? ` · ${t('deliver.pill.removed', { n: removed })}` : ''}
          </span>
        </>
      ) : null}
      {!isModel && d.fallback !== 'none' && d.fallback !== 'no_model' ? (
        <span className="basis-full text-subtle" data-testid="deliver-fallback">
          {t('deliver.by.modelFailed')}
        </span>
      ) : null}
    </div>
  );
}

function Ready({ response, d, onRetry, focus, onFocused }: { response: AskResponse; d: Deliverable; onRetry: () => void; focus?: boolean; onFocused?: () => void }) {
  const t = useMessages(deliverMessages);
  const num = useNumbering(response, d);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const titleId = useId();
  const listId = useId();
  const titleRef = useRef<HTMLHeadingElement>(null);
  useFocusOnMount(titleRef, focus, onFocused);
  const jump: Jump = (n) => {
    const el = document.getElementById(listId)?.querySelector<HTMLElement>(`[data-ns~="${n}"]`);
    if (!el) return;
    el.scrollIntoView({ block: 'nearest' });
    el.focus();
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(plainText(d, num, t));
      setCopied(true);
      toast(t('deliver.copied'));
      setTimeout(() => setCopied(false), 1600);
    } catch {
      toast(t('deliver.copyFailed'));
    }
  };
  const download = async (format: 'csv' | 'pptx') => {
    setBusy(true);
    try {
      const { blob, name } = await deliverApi.exportFile(d.deliverable_id, format);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = name;
      a.rel = 'noopener';
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      toast(t('deliver.downloaded', { name }));
    } catch (e) {
      toast(failOf(e) === 'nothing' ? t('deliver.nothing') : t('deliver.error'));
    } finally {
      setBusy(false);
    }
  };

  const changed = d.citations.filter((c) => c.changed_since_answer);
  return (
    <div data-testid="deliver-view" data-kind={d.kind} data-generated={d.generated} data-model={d.model?.model ?? ''}>
      <h3 ref={titleRef} id={titleId} tabIndex={-1} lang="en" className="text-heading-sm font-semibold text-fg outline-none" data-testid="deliver-title">
        {d.title}
      </h3>
      <div className="mt-1">
        <MetaLine d={d} num={num} t={t} />
      </div>

      <div className="mt-4">
        {d.kind === 'table' ? <TableView table={d.content as DeliverTable} title={d.title} num={num} onJump={jump} t={t} /> : null}
        {d.kind === 'chart' ? <ChartView chart={d.content as DeliverChart} title={d.title} titleId={titleId} num={num} onJump={jump} t={t} /> : null}
        {d.kind === 'analysis' ? <AnalysisView a={d.content as DeliverAnalysis} num={num} onJump={jump} /> : null}
      </div>

      {(d.content as DeliverChart).drawn_as_bar ? <p className="mt-2 text-caption text-muted">{t('deliver.drawnAsBar')}</p> : null}
      {(d.content as DeliverTable).truncated ? <p className="mt-2 text-caption text-muted">{t('deliver.truncated')}</p> : null}
      {changed.map((c) => (
        <p key={c.tag} className="mt-2 text-caption text-muted" data-testid="deliver-changed">
          {t('deliver.changed', { n: c.n, from: c.cited_version, to: c.version })}
        </p>
      ))}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="primary" icon={copied ? Check : Copy} onClick={() => void copy()} data-testid="deliver-copy">
          {t('deliver.copy')}
        </Button>
        {d.exports.includes('csv') ? (
          <Button size="sm" icon={Download} onClick={() => void download('csv')} disabled={busy} data-testid="deliver-csv">
            {t('deliver.csv')}
          </Button>
        ) : null}
        {d.exports.includes('pptx') ? (
          <Button size="sm" icon={Download} onClick={() => void download('pptx')} disabled={busy} data-testid="deliver-pptx">
            {t('deliver.pptx')}
          </Button>
        ) : null}
        <Button size="sm" icon={RefreshCw} onClick={onRetry} data-testid="deliver-regenerate">
          {t('deliver.regenerate')}
        </Button>
      </div>

      <SourceList d={d} num={num} title={t(`deliver.sources.${d.kind}` as DeliverKey)} listId={listId} />
      <p className="mt-4 text-caption text-subtle" lang="en" data-testid="deliver-scope">
        {d.scope_note}
      </p>
      <p className="mt-1 text-caption text-subtle" data-testid="deliver-recorded">
        {t('deliver.recorded')} <span className="font-mono">{d.deliverable_id.slice(0, 12)}</span>
      </p>
    </div>
  );
}

function SourceList({ d, num, title, listId }: { d: Deliverable; num: Numbering; title: string; listId: string }) {
  const seen = new Set<string>();
  const rows = d.citations.filter((c) => (seen.has(c.doc_id) ? false : (seen.add(c.doc_id), true)));
  if (!rows.length) return null;
  return (
    <section className="mt-5">
      <h3 className="mb-2 text-micro font-semibold uppercase tracking-wider text-subtle">{title}</h3>
      <ol id={listId} className="flex flex-col gap-1.5" data-testid="deliver-sources">
        {rows.map((c) => {
          const n = num.get(c.tag)?.n ?? c.n;
          // every number that points at this document (a chip may cite any of them)
          const ns = [...new Set(d.citations.filter((x) => x.doc_id === c.doc_id).map((x) => num.get(x.tag)?.n ?? x.n))];
          return (
            <li
              key={c.doc_id}
              tabIndex={-1}
              data-ns={ns.join(' ')}
              className="flex min-w-0 items-center gap-2 rounded-md border border-line bg-surface px-2.5 py-2 text-body-sm outline-none focus:border-accent focus:ring-2 focus:ring-accent/40"
            >
              <span className="inline-flex h-5 min-w-5 items-center justify-center rounded border border-line-strong px-1 text-caption font-semibold tabular-nums">{n}</span>
              <PlatformIcon platform={c.platform} tone="platform" size="sm" showLabel={false} />
              <span className="min-w-0 flex-1 truncate text-fg" lang="en">
                {c.title}
              </span>
              <span className="hidden shrink-0 truncate text-caption text-muted sm:inline" lang="en">
                {placeLabel(c.platform, c.container_name)}
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

/* --------------------------------------------------------------------------- the kinds */

/** True when every non-empty cell of the row cites exactly the same sources. */
function sameTags(cells: DeliverTable['rows'][number]): boolean {
  const key = (tags: string[]) => [...new Set(tags)].sort().join('|');
  const keys = new Set(cells.filter((c) => c.text).map((c) => key(c.tags)));
  return keys.size <= 1;
}

function TableView({ table, title, num, onJump, t }: { table: DeliverTable; title: string; num: Numbering; onJump?: Jump; t: T }) {
  const { lang } = useLang();
  return (
    <div className="max-w-full overflow-x-auto rounded-lg border border-line" data-testid="deliver-table">
      <table className="w-full border-collapse text-body-sm" lang="en">
        <caption className="sr-only">{title}</caption>
        <thead className="bg-raised">
          <tr>
            {table.columns.map((c, i) => (
              <th key={i} scope="col" className="border-b border-line px-3 py-2 text-left text-caption font-semibold text-muted">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((r, ri) => {
            // one set of chips per row when the whole row cites the same sources (review M2);
            // per cell only when a row mixes sources
            const perRow = sameTags(r);
            let lastFilled = -1;
            r.forEach((c, i) => {
              if (c.text) lastFilled = i;
            });
            return (
              <tr key={ri} className="border-b border-line last:border-b-0" data-chips={perRow ? 'row' : 'cell'}>
                {r.map((cell, ci) => {
                  const Tag = ci === 0 ? 'th' : 'td';
                  const chips = !perRow || ci === lastFilled;
                  return (
                    <Tag
                      key={ci}
                      {...(ci === 0 ? { scope: 'row' } : {})}
                      className={cn('px-3 py-2 align-top', ci === 0 ? 'text-left font-medium text-fg' : 'text-fg', cell.numeric && 'text-right tabular-nums')}
                      data-testid="deliver-cell"
                    >
                      {cell.text ? (
                        <>
                          {cell.text}
                          {chips ? <Chips tags={cell.tags} num={num} onJump={onJump} /> : null}
                        </>
                      ) : (
                        <span className="text-subtle" lang={lang}>
                          {t('deliver.empty')}
                        </span>
                      )}
                    </Tag>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function AnalysisView({ a, num, onJump }: { a: DeliverAnalysis; num: Numbering; onJump?: Jump }) {
  return (
    <div className="flex flex-col gap-4" data-testid="deliver-analysis" lang="en">
      {a.sections.map((s, i) => (
        <section key={i}>
          <h4 className="mb-1 text-micro font-semibold uppercase tracking-wider text-subtle">{s.heading}</h4>
          <p className="text-body leading-7 text-fg">
            {s.sentences.map((x, j) => (
              <span key={j} data-testid="deliver-sentence">
                {x.text}
                <Chips tags={x.tags} num={num} onJump={onJump} />{' '}
              </span>
            ))}
          </p>
        </section>
      ))}
    </div>
  );
}

function unitTick(chart: DeliverChart, v: number): string {
  const s = Number.isInteger(v) ? String(v) : String(Number(v.toFixed(6)));
  if (chart.unit === 'percent') return `${s}%`;
  if (chart.unit === 'currency' && chart.currency) return `${chart.currency} ${s}`;
  return s;
}

function ChartView({ chart, title, titleId, num, onJump, t }: { chart: DeliverChart; title: string; titleId: string; num: Numbering; onJump?: Jump; t: T }) {
  const series = chart.series;
  const xs: string[] = [];
  for (const s of series) for (const p of s.points) if (!xs.includes(p.x)) xs.push(p.x);
  // one row per label; blanks (never zeros) where a series has no point for it
  const data = xs.map((x) => {
    const row: Record<string, string | number | null> = { x };
    series.forEach((s, i) => {
      const p = s.points.find((q) => q.x === x);
      row[`v${i}`] = p ? p.value : null;
      row[`r${i}`] = p ? p.raw : '';
      row[`t${i}`] = p ? p.tag : '';
    });
    return row;
  });
  const values = series.flatMap((s) => s.points.map((p) => p.value));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const label =
    chart.type === 'line'
      ? t('deliver.chartLabelLine', { title, n: values.length, min: unitTick(chart, min), max: unitTick(chart, max) })
      : t('deliver.chartLabelBar', { title, n: values.length, min: unitTick(chart, min), max: unitTick(chart, max) });
  // up to three series are told apart by colour AND (for lines) dash; a 4th cycles, never undefined
  const palette = ['rgb(var(--accent-solid))', 'rgb(var(--fg))', 'rgb(var(--muted))'];
  const dashes = [undefined, '6 3', '2 3'];
  const colors = series.map((_, i) => palette[i % palette.length]);
  const tagOf = (x: string) => {
    for (const s of series) {
      const p = s.points.find((q) => q.x === x);
      if (p) return p.tag;
    }
    return '';
  };
  const Tick = (props: { x?: number; y?: number; payload?: { value: string } }) => {
    const { x = 0, y = 0, payload } = props;
    const v = payload?.value ?? '';
    const n = num.get(tagOf(v))?.n;
    const w = v.length * 7.5;
    return (
      <g transform={`translate(${x},${y + 14})`}>
        <text textAnchor="middle" x={n ? -9 : 0} fill="rgb(var(--fg))" fontSize={13} fontWeight={500}>
          {v}
        </text>
        {n ? (
          <g transform={`translate(${w / 2 - 2},-11)`}>
            <rect width={16} height={15} rx={3} fill="rgb(var(--raised))" stroke="rgb(var(--line-strong))" />
            <text x={8} y={11} textAnchor="middle" fontSize={10} fontWeight={600} fill="rgb(var(--muted))">
              {n}
            </text>
          </g>
        ) : null}
      </g>
    );
  };
  const TipBox = ({ active, payload }: TooltipProps<number, string>) => {
    if (!active || !payload?.length) return null;
    const row = payload[0].payload as Record<string, string>;
    return (
      <div className="rounded-md border border-line-strong bg-overlay px-2.5 py-1.5 text-caption text-fg shadow-pop">
        {series.map((s, i) => {
          if (!row[`r${i}`]) return null;
          const e = num.get(row[`t${i}`]);
          return (
            <p key={i} className="tabular-nums">
              <span className="font-semibold">{row[`r${i}`]}</span>
              {' · '}
              {t('deliver.foundIn', { n: e?.n ?? '', title: e?.dc?.container_name ?? e?.dc?.title ?? '' })}
            </p>
          );
        })}
      </div>
    );
  };
  const common = { data, margin: { top: 28, right: 16, bottom: 12, left: 4 }, accessibilityLayer: true };
  const yAxis = (
    <YAxis
      domain={[0, 'auto']}
      tickCount={4}
      tickFormatter={(v: number) => unitTick(chart, v)}
      tick={{ fill: 'rgb(var(--subtle))', fontSize: 12 }}
      axisLine={false}
      tickLine={false}
      width={64}
    />
  );
  const xAxis = <XAxis dataKey="x" tick={<Tick />} interval={0} axisLine={{ stroke: 'rgb(var(--line))' }} tickLine={false} height={36} />;
  return (
    <figure role="group" aria-labelledby={titleId} data-testid="deliver-chart" className="m-0">
      <div role="img" aria-label={label} className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          {chart.type === 'line' ? (
            <LineChart {...common}>
              {xAxis}
              {yAxis}
              <Tooltip content={<TipBox />} cursor={{ stroke: 'rgb(var(--line-strong))' }} isAnimationActive={false} />
              {series.map((s, i) => (
                <Line key={i} dataKey={`v${i}`} name={s.name} stroke={colors[i]} strokeDasharray={dashes[i % dashes.length]} strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} connectNulls={false}>
                  <LabelList dataKey={`r${i}`} position="top" fill="rgb(var(--fg))" fontSize={13} />
                </Line>
              ))}
            </LineChart>
          ) : (
            <BarChart {...common} barCategoryGap="30%">
              {xAxis}
              {yAxis}
              <Tooltip content={<TipBox />} cursor={{ fill: 'rgb(var(--raised))' }} isAnimationActive={false} />
              {series.map((s, i) => (
                <Bar key={i} dataKey={`v${i}`} name={s.name} fill={colors[i]} maxBarSize={56} radius={[2, 2, 0, 0]} isAnimationActive={false}>
                  <LabelList dataKey={`r${i}`} position="top" fill="rgb(var(--fg))" fontSize={14} fontWeight={600} className="tabular-nums" formatter={(v: string) => v.split(' ')[0]} />
                </Bar>
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
      {series.length > 1 ? (
        <ul className="mt-1 flex flex-wrap gap-3 text-caption text-muted">
          {series.map((s, i) => (
            <li key={i} className="inline-flex items-center gap-1.5" lang="en">
              <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: colors[i] }} />
              {s.name}
            </li>
          ))}
        </ul>
      ) : null}
      <figcaption className="mt-3">
        <p className="mb-1 text-micro font-semibold uppercase tracking-wider text-subtle">{t('deliver.data')}</p>
        <table className="w-full border-collapse text-body-sm" data-testid="deliver-chart-table">
          <thead>
            <tr className="border-b border-line">
              {series.length > 1 ? (
                <th scope="col" className="px-2 py-1.5 text-left text-caption font-semibold text-muted">
                  {t('deliver.col.series')}
                </th>
              ) : null}
              {chart.x_label ? (
                <th scope="col" lang="en" className="px-2 py-1.5 text-left text-caption font-semibold text-muted">
                  {chart.x_label}
                </th>
              ) : (
                <th scope="col" className="px-2 py-1.5 text-left text-caption font-semibold text-muted">
                  {t('deliver.col.label')}
                </th>
              )}
              <th scope="col" className="px-2 py-1.5 text-right text-caption font-semibold text-muted">
                {t('deliver.col.value')}
              </th>
              <th scope="col" className="px-2 py-1.5 text-left text-caption font-semibold text-muted">
                {t('deliver.col.source')}
              </th>
            </tr>
          </thead>
          <tbody>
            {series.flatMap((s, i) =>
              s.points.map((p) => (
                <tr key={`${i}-${p.x}`} className="border-b border-line last:border-b-0">
                  {series.length > 1 ? (
                    <td className="px-2 py-1.5 text-muted" lang="en">
                      {s.name}
                    </td>
                  ) : null}
                  <th scope="row" lang="en" className="px-2 py-1.5 text-left font-medium text-fg">
                    {p.x}
                  </th>
                  <td lang="en" className="px-2 py-1.5 text-right tabular-nums text-fg">
                    {p.raw}
                  </td>
                  <td className="px-2 py-1.5">
                    <Chips tags={[p.tag]} num={num} onJump={onJump} />
                  </td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </figcaption>
    </figure>
  );
}

/* ------------------------------------------------------------------------------- copy */

/** The Copy text. Headings come from the UI language (review M5); the deliverable's own text is as
 *  returned. The server's CSV keeps its fixed English headers (it is one file for every reader). */
function plainText(d: Deliverable, num: Numbering, t: T): string {
  const mark = (tags: string[]) => tags.map((tag) => `[${num.get(tag)?.n ?? '?'}]`).join('');
  const lines: string[] = [d.title, ''];
  if (d.kind === 'table') {
    const tb = d.content as DeliverTable;
    lines.push([...tb.columns, t('deliver.copySources')].join('\t'));
    for (const r of tb.rows) lines.push([...r.map((c) => c.text), mark([...new Set(r.flatMap((c) => c.tags))])].join('\t'));
  } else if (d.kind === 'chart') {
    const ch = d.content as DeliverChart;
    lines.push([t('deliver.col.series'), ch.x_label || t('deliver.col.label'), t('deliver.col.value'), t('deliver.col.source')].join('\t'));
    for (const s of ch.series) for (const p of s.points) lines.push([s.name, p.x, p.raw, mark([p.tag])].join('\t'));
  } else {
    const a = d.content as DeliverAnalysis;
    for (const s of a.sections) {
      lines.push(s.heading);
      for (const x of s.sentences) lines.push(`${x.text} ${mark(x.tags)}`);
      lines.push('');
    }
  }
  lines.push('', t('deliver.copySources'));
  for (const c of d.citations) lines.push(`[${num.get(c.tag)?.n ?? c.n}] ${c.title} · ${placeLabel(c.platform, c.container_name)}`);
  return lines.join('\n');
}
