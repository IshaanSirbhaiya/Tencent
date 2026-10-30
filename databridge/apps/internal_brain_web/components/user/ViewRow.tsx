'use client';
// "Make this a page": a just-in-time view of a released answer (POST /ib/views). The person says how
// they want the answer laid out ("a table, then the timeline and the Slack thread") and the page is
// built from the answer's cited sources only, checked on the server, and rendered from its A2UI
// messages (components/views/). Nothing here computes or rewrites a value.
//
// Two placements. On the Ask page the page opens in the canvas on the right (`onBuild`, ViewCanvas);
// anywhere else it opens under the answer. When the question itself asks for a layout ("…as a report
// with a timeline"), the newest answer builds its page once by itself, with the question as the
// request. Rule 4: a 404 is one sentence ("nothing to build") whatever the reason.
import { Check, Copy, Expand, Info, LayoutTemplate, PanelRight, RefreshCw, TriangleAlert } from 'lucide-react';
import dynamic from 'next/dynamic';
import { useEffect, useId, useRef, useState } from 'react';
import { Button, Sheet, SheetContent, Skeleton } from '@/components/ui';
import { viewNumbering } from '@/components/views/numbering';
import { ApiError, analyticsApi, viewApi } from '@/lib/api';
import { cn } from '@/lib/cn';
import { useMessages } from '@/lib/i18n';
import { viewMessages, type ViewMessageKey } from '@/lib/i18n/view';
import type { AskResponse, JitView, ViewsStatus } from '@/lib/types';
import { toast } from './Toast';

// A2UI defines custom elements when it loads, so the renderer is browser only (never server-rendered).
const ViewSurface = dynamic(() => import('@/components/views/ViewSurface').then((m) => m.ViewSurface), {
  ssr: false,
  loading: () => <Skeleton className="h-40 w-full motion-reduce:animate-none" />,
});

export type ViewT = (key: ViewMessageKey, vars?: Record<string, string | number>) => string;
type Fail = 'nothing' | 'busy' | 'error';
export type ViewLoad = { state: 'idle' } | { state: 'loading'; since: number } | { state: 'ok'; v: JitView } | { state: 'fail'; kind: Fail };
export const viewFailOf = (e: unknown): Fail =>
  e instanceof ApiError && e.status === 404 ? 'nothing' : e instanceof ApiError && e.status === 429 ? 'busy' : 'error';

export const VIEW_PRESETS = ['report', 'table', 'timeline', 'conversation', 'byDoc', 'steps'] as const;

/** A question that asks for a layout itself (EN + 简体中文). "page" is left out: Confluence pages. */
const WANTS_VIEW = /\b(report|dashboard|one-pager|table|tabular|chart|graph|timeline|checklist|layout|by document)\b|报告|表格|图表|时间线|页面/i;
export function wantsView(question: string | undefined): boolean {
  return !!question && WANTS_VIEW.test(question);
}

/* ------------------------------------------------------------------------------- status */

let statusOnce: Promise<ViewsStatus | null> | null = null;
/** /ib/status's views block, fetched once per page load (the switch needs an IB restart). */
export function viewsStatus(): Promise<ViewsStatus | null> {
  if (!statusOnce) {
    statusOnce = analyticsApi
      .status()
      .then((s) => (s.views && s.views.enabled ? s.views : null))
      .catch(() => {
        statusOnce = null; // try again next time
        return null;
      });
  }
  return statusOnce;
}

export function useViews(): ViewsStatus | null {
  const [st, setSt] = useState<ViewsStatus | null>(null);
  useEffect(() => {
    let live = true;
    void viewsStatus().then((s) => live && setSt(s));
    return () => {
      live = false;
    };
  }, []);
  return st;
}

/** One build at a time per owner: a newer request replaces an older one still in flight. */
export function useViewBuild(reqId: string) {
  const [load, setLoad] = useState<ViewLoad>({ state: 'idle' });
  const seq = useRef(0);
  const build = (instruction: string) => {
    const my = ++seq.current;
    setLoad({ state: 'loading', since: Date.now() });
    viewApi
      .create(reqId, instruction.trim())
      .then((v) => my === seq.current && setLoad({ state: 'ok', v }))
      .catch((e) => my === seq.current && setLoad({ state: 'fail', kind: viewFailOf(e) }));
  };
  return { load, build };
}

/** Request ids whose automatic page was already asked for (one ledgered build per answer, also
 *  under React's development double effect and when the card re-mounts). */
const autoBuilt = new Set<string>();

/* ---------------------------------------------------------------------------------- row */

export interface ViewRowProps {
  response: AskResponse;
  question?: string;
  /** the newest answer: it may build its page by itself when the question asks for a layout */
  auto?: boolean;
  /** Ask page: build into the canvas instead of under the answer (`auto`: the question asked for it) */
  onBuild?: (instruction: string, auto?: boolean) => void;
  /** Ask page: this answer's page is the one open in the canvas */
  shownInCanvas?: boolean;
  /** Ask page: bring this answer's page back into the canvas */
  onShow?: () => void;
}

export function ViewRow({ response, question, auto, onBuild, shownInCanvas, onShow }: ViewRowProps) {
  const t = useMessages(viewMessages);
  const st = useViews();
  const headingId = useId();
  const inputId = useId();
  const [text, setText] = useState('');
  const [full, setFull] = useState(false);
  const local = useViewBuild(response.request_id);
  const resultRef = useRef<HTMLDivElement>(null);
  const inline = !onBuild;
  const load = local.load;

  const build = (instruction: string, auto = false) => {
    if (onBuild) onBuild(instruction, auto);
    else {
      local.build(instruction);
      requestAnimationFrame(() => resultRef.current?.focus());
    }
  };

  useEffect(() => {
    if (!st || !auto || !wantsView(question) || autoBuilt.has(response.request_id)) return;
    autoBuilt.add(response.request_id);
    setText(question!);
    build(question!, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st, auto, question, response.request_id]);

  if (!st) return null;
  const busy = inline && load.state === 'loading';
  const chip =
    'inline-flex h-8 items-center rounded-full border border-line bg-surface px-3.5 text-caption font-medium text-fg transition-colors duration-fast hover:border-line-strong hover:bg-raised disabled:opacity-50';
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-2.5" data-testid="view-row">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <h3 id={headingId} data-part="label" className="inline-flex items-center gap-1.5 text-caption font-medium text-muted">
          <LayoutTemplate aria-hidden className="h-3.5 w-3.5" />
          {t('view.heading')}
        </h3>
        <span data-part="hint" className="text-caption text-subtle">
          {t('view.hint')}
        </span>
        {shownInCanvas ? (
          <span className="ml-auto inline-flex items-center gap-1 text-caption text-accent" data-testid="view-in-canvas">
            <PanelRight aria-hidden className="h-3.5 w-3.5" />
            {t('view.inCanvas')}
          </span>
        ) : onShow ? (
          <button type="button" onClick={onShow} className="ml-auto inline-flex items-center gap-1 rounded text-caption font-medium text-accent hover:underline" data-testid="view-show">
            <PanelRight aria-hidden className="h-3.5 w-3.5" />
            {t('view.show')}
          </button>
        ) : null}
      </div>
      <form
        className="flex flex-col gap-2 sm:flex-row"
        onSubmit={(e) => {
          e.preventDefault();
          if (!busy) build(text);
        }}
      >
        <label htmlFor={inputId} className="sr-only">
          {t('view.label')}
        </label>
        <input
          id={inputId}
          value={text}
          maxLength={300}
          onChange={(e) => setText(e.target.value)}
          placeholder={t('view.placeholder')}
          data-testid="view-input"
          className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 text-body-sm text-fg placeholder:text-subtle focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus/25"
        />
        <Button type="submit" size="sm" variant="primary" icon={LayoutTemplate} disabled={busy} data-testid="view-build">
          {load.state === 'ok' || shownInCanvas ? t('view.rebuild') : t('view.build')}
        </Button>
      </form>
      <div role="group" aria-label={t('view.presets')} className="flex flex-wrap gap-1.5">
        {VIEW_PRESETS.map((p) => (
          <button
            key={p}
            type="button"
            className={chip}
            disabled={busy}
            data-testid={`view-preset-${p}`}
            onClick={() => {
              const ins = t(`view.preset.${p}.text` as ViewMessageKey);
              setText(ins);
              build(ins);
            }}
          >
            {t(`view.preset.${p}` as ViewMessageKey)}
          </button>
        ))}
      </div>

      {inline && load.state !== 'idle' ? (
        <div ref={resultRef} tabIndex={-1} className="mt-1 rounded-xl border border-line bg-bg/40 p-4 outline-none sm:p-5" data-testid="view-result">
          <ViewResult response={response} load={load} t={t} onRetry={() => local.build(text)} onFull={() => setFull(true)} />
        </div>
      ) : null}

      {inline ? (
        <Sheet open={full && load.state === 'ok'} onOpenChange={(o) => setFull(o)}>
          {full && load.state === 'ok' ? (
            <SheetContent width="xl" title={t('view.fullTitle')} description={t('view.fullDesc')} closeLabel={t('view.close')} data-testid="view-sheet">
              <ViewSurface response={response} view={load.v} />
            </SheetContent>
          ) : null}
        </Sheet>
      ) : null}
    </section>
  );
}

/* ------------------------------------------------------------------------------- result */

function useElapsed(since: number | null): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (since === null) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [since]);
  return since === null ? 0 : now - since;
}

function Notice({ testId, text, hint, icon: Icon = Info, action }: { testId: string; text: string; hint?: string; icon?: typeof Info; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center px-4 py-10 text-center" data-testid={testId}>
      <span aria-hidden className="mb-3 inline-flex h-10 w-10 items-center justify-center rounded-xl bg-raised text-muted">
        <Icon className="h-5 w-5" />
      </span>
      <p className="max-w-md text-heading-sm text-fg">{text}</p>
      {hint ? <p className="mt-1 max-w-sm text-body-sm text-muted">{hint}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

function Loading({ since, t }: { since: number; t: ViewT }) {
  const elapsed = useElapsed(since);
  return (
    <div role="status" data-testid="view-loading" className="flex flex-col gap-4">
      <p className="text-body-sm text-muted">{elapsed > 8000 ? t('view.slow') : t('view.loading')}</p>
      <div aria-hidden className="flex flex-col gap-2.5">
        <Skeleton className="h-6 w-1/2 motion-reduce:animate-none" />
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-4 w-full motion-reduce:animate-none" />
        ))}
        <Skeleton className="mt-3 h-32 w-full motion-reduce:animate-none" />
      </div>
    </div>
  );
}

/**
 * The states of one view: loading, nothing to build, busy or error (with a retry), the layout the
 * sources cannot fill, and the page. `actions` = false hides the button row (the canvas has its own).
 */
export function ViewResult({
  response,
  load,
  t,
  onRetry,
  onFull,
  actions = true,
}: {
  response: AskResponse;
  load: ViewLoad;
  t: ViewT;
  onRetry: () => void;
  onFull?: () => void;
  actions?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  if (load.state === 'idle') return null;
  if (load.state === 'loading') return <Loading since={load.since} t={t} />;
  if (load.state === 'fail') {
    if (load.kind === 'nothing') return <Notice testId="view-nothing" text={t('view.nothing')} hint={t('view.nothingHint')} />;
    return (
      <div role="alert">
        <Notice
          testId="view-error"
          icon={TriangleAlert}
          text={load.kind === 'busy' ? t('view.busy') : t('view.error')}
          action={
            <Button size="sm" icon={RefreshCw} onClick={onRetry}>
              {t('view.retry')}
            </Button>
          }
        />
      </div>
    );
  }
  const v = load.v;
  if (v.status !== 'ready') return <Notice testId="view-insufficient" text={t('view.insufficient')} hint={t('view.insufficientHint')} />;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(viewPlainText(response, v));
      setCopied(true);
      toast(t('view.copied'));
      setTimeout(() => setCopied(false), 1600);
    } catch {
      toast(t('view.copyFailed'));
    }
  };
  return (
    <div className="flex flex-col gap-4">
      <ViewSurface response={response} view={v} />
      {v.generated === 'rules' && v.fallback !== 'no_model' && v.fallback !== 'none' ? (
        <p className="text-caption text-subtle" data-testid="view-fallback">
          {t('view.by.modelFailed')}
        </p>
      ) : null}
      {actions ? (
        <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
          {onFull ? (
            <Button size="sm" icon={Expand} onClick={onFull} data-testid="view-open">
              {t('view.open')}
            </Button>
          ) : null}
          <Button size="sm" icon={copied ? Check : Copy} onClick={() => void copy()} data-testid="view-copy">
            {t('view.copy')}
          </Button>
          <ViewRecorded v={v} t={t} className="ml-auto" />
        </div>
      ) : (
        <ViewRecorded v={v} t={t} className="border-t border-line pt-3" />
      )}
    </div>
  );
}

export function ViewRecorded({ v, t, className }: { v: JitView; t: ViewT; className?: string }) {
  return (
    <p className={cn('text-caption text-subtle', className)} data-testid="view-recorded">
      {t('view.recorded')} <span className="font-mono">{v.view_id.slice(0, 12)}</span>
    </p>
  );
}

/* ------------------------------------------------------------------------------- copy */

type Cell = { text: string; tags: string[] };
type AnyBlock = {
  type: string;
  heading?: string;
  title?: string;
  items?: (Cell & { when?: string; label?: string })[];
  table?: { columns: string[]; rows: Cell[][] };
  chart?: { series: { name: string; points: { x: string; raw: string; tag: string }[] }[] };
  threads?: { title: string; messages: { speaker: string; text: string; tags: string[] }[] }[];
  docs?: { title: string; parts: { heading: string; items: string[] }[] }[];
  wanted?: string;
};

/** The Copy text: the page's own text as returned, with [n] marks; headings are the server's or ours. */
export function viewPlainText(response: AskResponse, v: JitView): string {
  const num = viewNumbering(response, v);
  const mark = (tags: string[]) => [...new Set(tags)].map((tag) => `[${num.get(tag)?.n ?? '?'}]`).join('');
  const data = (v.a2ui.find((m) => 'updateDataModel' in m) as { updateDataModel?: { value?: { blocks?: AnyBlock[] } } } | undefined)?.updateDataModel?.value;
  const lines: string[] = [v.title, ''];
  for (const b of data?.blocks ?? []) {
    if (b.type === 'missing' || b.type === 'sources') continue;
    lines.push((b.heading || b.type).toUpperCase());
    if (b.items) for (const it of b.items) lines.push(`${it.when ? `${it.when} · ` : ''}${it.label ? `${it.label}: ` : ''}${it.text} ${mark(it.tags)}`.trim());
    if (b.table) {
      lines.push(b.table.columns.join('\t'));
      for (const r of b.table.rows) lines.push([...r.map((c) => c.text), mark(r.flatMap((c) => c.tags))].join('\t'));
    }
    if (b.chart) for (const s of b.chart.series) for (const p of s.points) lines.push(`${s.name}\t${p.x}\t${p.raw}\t${mark([p.tag])}`);
    if (b.threads) for (const th of b.threads) for (const m of th.messages) lines.push(`${m.speaker}: ${m.text} ${mark(m.tags)}`);
    if (b.docs)
      for (const d of b.docs) {
        lines.push(d.title);
        for (const p of d.parts) {
          if (p.heading) lines.push(`  ${p.heading}`);
          for (const x of p.items) lines.push(`  ${x}`);
        }
      }
    lines.push('');
  }
  for (const c of v.citations) lines.push(`[${c.n}] ${c.title} · ${c.container_name}`);
  return lines.join('\n');
}
