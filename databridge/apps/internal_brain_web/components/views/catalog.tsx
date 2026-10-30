'use client';
// The Walnut A2UI catalog: the components a just-in-time view may use (A2UI v0.9, @a2ui/react).
//
// The server sends a component tree that only BINDS paths ({path: "/blocks/0"}) and a data model
// it wrote from checked sources (databridge_ib/views/a2ui.py). Each component here takes its block
// through one `data` binding and the page's sources through `sources`, re-validates the shape with
// zod (a block that does not parse is not shown), and renders INERT: every string is a React text
// node, there are no actions, no links out and no HTML. A chip moves focus to that source in the
// page's own source list (it never opens a second modal over the page; DeliverSheet review M3).
import { createComponentImplementation } from '@a2ui/react/v0_9';
import { Catalog, CommonSchemas } from '@a2ui/web_core/v0_9';
import { AlertTriangle, CheckCircle2, ChevronDown, CircleDashed, Info, MessagesSquare, ShieldCheck } from 'lucide-react';
import { createContext, Fragment, useContext, useId } from 'react';
import { z } from 'zod';
import { PlatformIcon } from '@/components/PlatformIcon';
import { CitationChip } from '@/components/user/CitationChip';
import { ChartView, TableView, type Numbering, type T as DeliverT } from '@/components/user/DeliverSheet';
import { speakerName, speakerParts } from '@/components/user/answerText';
import { cn } from '@/lib/cn';
import { placeLabel } from '@/lib/format';
import type { ViewMessageKey } from '@/lib/i18n/view';
import type { DeliverChart, DeliverTable } from '@/lib/types';

/** Must equal databridge_ib/views/a2ui.py CATALOG_ID (an identifier; never fetched). */
export const CATALOG_ID = 'https://walnut.sg/a2ui/catalogs/view/v1.json';

type VT = (key: ViewMessageKey, vars?: Record<string, string | number>) => string;
export interface ViewEnv {
  num: Numbering;
  onJump: (n: number) => void;
  t: VT;
  td: DeliverT;
  lang: string;
  listId: string;
}
export const ViewEnvContext = createContext<ViewEnv | null>(null);
const useEnv = () => {
  const env = useContext(ViewEnvContext);
  if (!env) throw new Error('ViewEnvContext missing');
  return env;
};

/* ------------------------------------------------------------------------------- shapes */

const Tags = z.array(z.string().regex(/^C\d{1,2}$/)).max(6);
const Item = z.object({ text: z.string().max(2000), tags: Tags });
const Facts = z.object({ type: z.literal('facts'), heading: z.string().optional(), items: z.array(Item).max(20) });
const Steps = z.object({ type: z.literal('steps'), heading: z.string().optional(), items: z.array(Item).max(20) });
const Cell = z.object({ text: z.string(), tags: Tags, numeric: z.boolean().optional() });
const Style = z.object({ tone: z.enum(['default', 'black', 'white', 'tinted']), accent: z.string() });
const TableShape = z.object({
  type: z.literal('table'),
  heading: z.string().optional(),
  style: Style.optional(),
  table: z.object({ columns: z.array(z.string()).max(8), rows: z.array(z.array(Cell)).max(40), truncated: z.boolean().optional() }).passthrough(),
});
const ChartShape = z.object({
  type: z.literal('chart'),
  heading: z.string().optional(),
  title: z.string().optional(),
  chart: z
    .object({
      type: z.enum(['bar', 'line']),
      series: z.array(z.object({ name: z.string(), points: z.array(z.object({ x: z.string(), value: z.number(), raw: z.string(), tag: z.string() })) })),
    })
    .passthrough(),
});
const When = z.object({
  when: z.string().max(40),
  dated_by: z.enum(['record', 'source']),
  text: z.string().max(2000),
  tags: Tags,
  label: z.string().optional(),
  platform: z.string().optional(),
});
const Timeline = z.object({ type: z.literal('timeline'), heading: z.string().optional(), items: z.array(When).max(20) });
const Msg = z.object({ speaker: z.string().max(60), text: z.string().max(2000), tags: Tags });
const Conversation = z.object({
  type: z.literal('conversation'),
  heading: z.string().optional(),
  threads: z.array(z.object({ tag: z.string(), title: z.string(), platform: z.string(), place: z.string(), messages: z.array(Msg).max(20) })).max(10),
});
const Sections = z.object({
  type: z.literal('sections'),
  heading: z.string().optional(),
  docs: z
    .array(
      z.object({
        tag: z.string(),
        title: z.string(),
        platform: z.string(),
        place: z.string(),
        updated: z.string(),
        parts: z.array(z.object({ heading: z.string(), items: z.array(z.string()) })),
        table: TableShape.shape.table.nullable().optional(),
      }),
    )
    .max(12),
});
const Missing = z.object({ type: z.literal('missing'), wanted: z.string() });
const Source = z.object({
  tag: z.string(),
  n: z.number(),
  doc_id: z.string(),
  platform: z.string(),
  container_name: z.string(),
  title: z.string(),
  version: z.string(),
  checked_at: z.string().optional(),
  changed_since_answer: z.boolean().optional(),
  cited_version: z.string().optional(),
});
const Sources = z.array(Source).max(40);
const Meta = z.object({
  label: z.string(),
  generated: z.enum(['model', 'rules', 'none']),
  model: z.object({ provider: z.string(), model: z.string(), fallback_used: z.boolean() }).nullable(),
  instruction: z.string(),
  accent: z.string().optional(),
  requested: z.array(z.string()),
});
export type ViewMeta = z.infer<typeof Meta>;

/* ------------------------------------------------------------------------------- pieces */

function Chips({ tags }: { tags: string[] }) {
  const { num, onJump, lang } = useEnv();
  return (
    <span lang={lang}>
      {[...new Set(tags)].map((tag) => {
        const e = num.get(tag);
        if (!e) return null;
        if (e.c) return <CitationChip key={tag} citation={e.c} n={e.n} onOpen={() => onJump(e.n)} className="ml-1" />;
        return (
          <span key={tag} data-testid="view-cite" className="ml-1 inline-flex h-5 min-w-5 items-center justify-center rounded border border-line-strong bg-raised px-1 align-middle text-caption font-semibold tabular-nums text-muted">
            {e.n}
          </span>
        );
      })}
    </span>
  );
}

/** Source text with a chat "handle: " prefix shown as a "<name> said" label (the text is unchanged). */
function Said({ text }: { text: string }) {
  const { t, lang } = useEnv();
  return (
    <>
      {speakerParts(text).map((p, i) =>
        typeof p === 'string' ? (
          <Fragment key={i}>{p}</Fragment>
        ) : (
          <span key={i} lang={lang} className="mr-1 rounded bg-raised px-1 py-px text-caption font-medium text-muted">
            {t('view.said', { name: p.name })}
          </span>
        ),
      )}
    </>
  );
}

function Block({ kind, heading, children, testId }: { kind: string; heading?: string; children: React.ReactNode; testId?: string }) {
  const { t } = useEnv();
  const id = useId();
  return (
    <section aria-labelledby={id} className="min-w-0" data-testid={testId ?? `view-block-${kind}`} data-block={kind}>
      <h4 id={id} className="mb-3 text-body-sm font-semibold text-fg" {...(heading ? { lang: 'en' } : {})}>
        {heading || t(`view.block.${kind}` as ViewMessageKey)}
      </h4>
      {children}
    </section>
  );
}

function Invalid() {
  return null; // a block that does not parse is not shown (the server never sends one)
}

/* ---------------------------------------------------------------------------- components */

const DataApi = (name: string) => ({
  name,
  schema: z.object({ data: CommonSchemas.DynamicValue, sources: CommonSchemas.DynamicValue }),
});

const PageApi = {
  name: 'WalnutPage',
  schema: z.object({ title: CommonSchemas.DynamicString, meta: CommonSchemas.DynamicValue, children: CommonSchemas.ChildList }),
};

const WalnutPage = createComponentImplementation(PageApi, ({ props, buildChild }) => {
  const { t, lang } = useEnv();
  const meta = Meta.safeParse(props.meta);
  const m = meta.success ? meta.data : null;
  const by =
    !m || m.generated !== 'model' || !m.model
      ? t('view.by.rules')
      : m.model.fallback_used
        ? t('view.by.fallbackModel', { model: m.model.model })
        : t('view.by.model', { model: m.model.model });
  const kids = (Array.isArray(props.children) ? props.children : []) as unknown[];
  return (
    <article
      className="flex min-w-0 flex-col gap-7"
      data-testid="view-page"
      data-accent={m?.accent ?? 'blue'}
      style={{ ['--page-accent' as string]: ACCENT[m?.accent ?? 'blue'] ?? ACCENT.blue }}
    >
      <header className="flex flex-col gap-1.5 border-b border-line pb-3">
        <h3 lang="en" className="text-heading-sm font-semibold text-fg" data-testid="view-title">
          {String(props.title ?? '')}
        </h3>
        <div className="flex flex-col gap-1 text-caption text-muted" lang={lang} data-testid="view-meta">
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span>{t('view.draftLabel')}</span>
            <span aria-hidden className="text-subtle">
              ·
            </span>
            <span data-testid="view-by">{by}</span>
          </p>
          {/* the request, unless it is the title already (a question that asked for its own layout) */}
          {m?.instruction && m.instruction !== String(props.title ?? '') ? (
            <p className="min-w-0" data-testid="view-asked">
              {t('view.asked')}: <q className="text-fg">{m.instruction}</q>
            </p>
          ) : null}
        </div>
      </header>
      {kids.map((c, i) => (
        <Fragment key={i}>{buildChild(c as string)}</Fragment>
      ))}
    </article>
  );
});

const WalnutFacts = createComponentImplementation(DataApi('WalnutFacts'), ({ props }) => {
  const b = Facts.safeParse(props.data);
  if (!b.success) return <Invalid />;
  return (
    <Block kind="facts" heading={b.data.heading}>
      <ul className="flex flex-col gap-2" lang="en">
        {b.data.items.map((it, i) => (
          <li key={i} className="flex gap-2.5 text-body leading-7 text-fg" data-testid="view-fact">
            <span aria-hidden className="mt-[0.7rem] h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
            <span className="min-w-0">
              <Said text={it.text} />
              <Chips tags={it.tags} />
            </span>
          </li>
        ))}
      </ul>
    </Block>
  );
});

const WalnutTable = createComponentImplementation(DataApi('WalnutTable'), ({ props }) => {
  const { num, onJump, td, t } = useEnv();
  const b = TableShape.safeParse(props.data);
  if (!b.success) return <Invalid />;
  const title = b.data.heading || t('view.block.table');
  const tone = b.data.style?.tone ?? 'default';
  const table = <TableView table={b.data.table as unknown as DeliverTable} title={title} num={num} onJump={onJump} t={td} />;
  return (
    <Block kind="table" heading={b.data.heading}>
      {tone === 'black' ? (
        // "a table in black": the dark tokens for this table only (globals.css data-theme-scope)
        <div data-theme-scope="dark" data-tone="black" className="overflow-hidden rounded-lg bg-[rgb(17,17,19)] text-fg [&_[data-testid=deliver-table]]:border-[rgb(44,44,48)]">
          {table}
        </div>
      ) : tone === 'tinted' ? (
        <div data-tone="tinted" className="overflow-hidden rounded-lg [&_thead]:bg-[color-mix(in_srgb,var(--page-accent)_12%,transparent)]">
          {table}
        </div>
      ) : (
        <div data-tone={tone}>{table}</div>
      )}
      {b.data.table.truncated ? <p className="mt-2 text-caption text-muted">{td('deliver.truncated')}</p> : null}
    </Block>
  );
});

const WalnutChart = createComponentImplementation(DataApi('WalnutChart'), ({ props }) => {
  const { num, onJump, td, t } = useEnv();
  const titleId = useId();
  const b = ChartShape.safeParse(props.data);
  if (!b.success || !b.data.chart.series.some((s) => s.points.length)) return <Invalid />;
  const title = b.data.title || b.data.heading || t('view.block.chart');
  return (
    <Block kind="chart" heading={b.data.heading}>
      <p id={titleId} lang="en" className="mb-1 text-body-sm font-medium text-fg">
        {title}
      </p>
      <ChartView chart={b.data.chart as unknown as DeliverChart} title={title} titleId={titleId} num={num} onJump={onJump} t={td} />
    </Block>
  );
});

const WalnutSteps = createComponentImplementation(DataApi('WalnutSteps'), ({ props }) => {
  const b = Steps.safeParse(props.data);
  if (!b.success) return <Invalid />;
  return (
    <Block kind="steps" heading={b.data.heading}>
      <ol className="flex flex-col gap-2" lang="en">
        {b.data.items.map((it, i) => (
          <li key={i} className="flex gap-3 rounded-lg border border-line bg-surface px-3 py-2.5 text-body-sm leading-6 text-fg" data-testid="view-step">
            <CircleDashed aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-subtle" />
            <span className="min-w-0">
              {it.text}
              <Chips tags={it.tags} />
            </span>
          </li>
        ))}
      </ol>
    </Block>
  );
});

const WalnutTimeline = createComponentImplementation(DataApi('WalnutTimeline'), ({ props }) => {
  const { t, lang } = useEnv();
  const b = Timeline.safeParse(props.data);
  if (!b.success) return <Invalid />;
  return (
    <Block kind="timeline" heading={b.data.heading}>
      <ol className="relative ml-1.5 flex flex-col gap-4 border-l border-line pl-5">
        {b.data.items.map((it, i) => (
          <li key={i} className="relative" data-testid="view-when">
            <span aria-hidden className="absolute -left-[1.6rem] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-accent bg-canvas" />
            <p className="flex flex-wrap items-center gap-x-2 text-caption text-muted">
              <span className="font-semibold tabular-nums text-fg" lang="en">
                {it.when}
              </span>
              <span lang={lang}>{t(it.dated_by === 'record' ? 'view.dated.record' : 'view.dated.source')}</span>
              {it.label ? (
                <span className="inline-flex min-w-0 items-center gap-1" lang="en">
                  {it.platform ? <PlatformIcon platform={it.platform as never} tone="platform" size="sm" showLabel={false} /> : null}
                  <span className="truncate">{it.label}</span>
                </span>
              ) : null}
            </p>
            {it.text ? (
              <p className="mt-1 text-body-sm leading-6 text-fg" lang="en">
                <Said text={it.text} />
                <Chips tags={it.tags} />
              </p>
            ) : (
              <Chips tags={it.tags} />
            )}
          </li>
        ))}
      </ol>
    </Block>
  );
});

const WalnutConversation = createComponentImplementation(DataApi('WalnutConversation'), ({ props }) => {
  const b = Conversation.safeParse(props.data);
  if (!b.success) return <Invalid />;
  return (
    <Block kind="conversation" heading={b.data.heading}>
      <div className="flex flex-col gap-4">
        {b.data.threads.map((th) => (
          <div key={th.tag} className="rounded-lg border border-line bg-surface" data-testid="view-thread">
            <p className="flex min-w-0 items-center gap-2 border-b border-line px-3 py-2 text-caption text-muted" lang="en">
              <MessagesSquare aria-hidden className="h-3.5 w-3.5 shrink-0" />
              <PlatformIcon platform={th.platform as never} tone="platform" size="sm" showLabel={false} />
              <span className="truncate">{placeLabel(th.platform as never, th.place)}</span>
              <span className="ml-auto shrink-0">
                <Chips tags={[th.tag]} />
              </span>
            </p>
            <ul className="flex flex-col gap-3 px-3 py-3" lang="en">
              {th.messages.map((m, i) => {
                const name = speakerName(m.speaker);
                return (
                  <li key={i} className="flex gap-2.5" data-testid="view-message">
                    <span aria-hidden className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-raised text-caption font-semibold text-muted">
                      {name.slice(0, 1).toUpperCase()}
                    </span>
                    <span className="min-w-0">
                      <span className="block text-caption font-semibold text-fg">{name}</span>
                      <span className="block text-body-sm leading-6 text-fg">{m.text}</span>
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </Block>
  );
});

const WalnutSections = createComponentImplementation(DataApi('WalnutSections'), ({ props }) => {
  const { num, onJump, td, t, lang } = useEnv();
  const b = Sections.safeParse(props.data);
  if (!b.success) return <Invalid />;
  return (
    <Block kind="sections" heading={b.data.heading}>
      <div className="flex flex-col gap-4">
        {b.data.docs.map((d) => (
          <div key={d.tag} className="rounded-lg border border-line bg-surface px-4 py-3" data-testid="view-doc">
            <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-caption text-muted">
              <Chips tags={[d.tag]} />
              <PlatformIcon platform={d.platform as never} tone="platform" size="sm" showLabel={false} />
              <span className="min-w-0 truncate" lang="en">
                {placeLabel(d.platform as never, d.place)}
              </span>
              {d.updated ? <span lang={lang}>{t('view.updated', { date: d.updated })}</span> : null}
            </p>
            <h5 className="mt-1 text-body font-semibold text-fg" lang="en">
              {d.title}
            </h5>
            <div className="mt-2 flex flex-col gap-3" lang="en">
              {d.parts.map((p, i) => (
                <div key={i}>
                  {p.heading ? <p className="mb-1 text-caption font-semibold text-muted">{p.heading}</p> : null}
                  {p.items.map((x, j) => (
                    <p key={j} className="text-body-sm leading-6 text-fg">
                      <Said text={x} />
                    </p>
                  ))}
                </div>
              ))}
              {d.table ? <TableView table={d.table as unknown as DeliverTable} title={d.title} num={num} onJump={onJump} t={td} /> : null}
            </div>
          </div>
        ))}
      </div>
    </Block>
  );
});

const WalnutSources = createComponentImplementation(DataApi('WalnutSources'), ({ props }) => {
  const { listId, t, lang } = useEnv();
  const s = Sources.safeParse(props.sources);
  if (!s.success || !s.data.length) return <Invalid />;
  const seen = new Set<string>();
  const rows = s.data.filter((c) => (seen.has(c.doc_id) ? false : (seen.add(c.doc_id), true)));
  return (
    <Block kind="sources">
      <ol id={listId} className="flex flex-col gap-1.5" data-testid="view-sources">
        {rows.map((c) => {
          const ns = [...new Set(s.data.filter((x) => x.doc_id === c.doc_id).map((x) => x.n))];
          return (
            <li
              key={c.doc_id}
              tabIndex={-1}
              data-ns={ns.join(' ')}
              className="flex min-w-0 items-center gap-2 rounded-md border border-line bg-surface px-2.5 py-2 text-body-sm outline-none focus:border-accent focus:ring-2 focus:ring-accent/40"
            >
              <span className="inline-flex h-5 min-w-5 items-center justify-center rounded border border-line-strong px-1 text-caption font-semibold tabular-nums">{c.n}</span>
              <PlatformIcon platform={c.platform as never} tone="platform" size="sm" showLabel={false} />
              <span className="min-w-0 flex-1 truncate text-fg" lang="en">
                {c.title}
              </span>
              <span className="hidden shrink-0 truncate text-caption text-muted sm:inline" lang="en">
                {placeLabel(c.platform as never, c.container_name)}
              </span>
              {c.checked_at ? (
                <span className="inline-flex shrink-0 items-center gap-1 text-caption text-muted" lang={lang} title={c.checked_at}>
                  <ShieldCheck aria-hidden className="h-3.5 w-3.5 text-ok" />
                  <span className="hidden md:inline">{t('view.checked')}</span>
                </span>
              ) : null}
            </li>
          );
        })}
      </ol>
    </Block>
  );
});

const WalnutMissing = createComponentImplementation(DataApi('WalnutMissing'), ({ props }) => {
  const { t } = useEnv();
  const b = Missing.safeParse(props.data);
  if (!b.success) return <Invalid />;
  const known = ['facts', 'table', 'chart', 'steps', 'timeline', 'conversation', 'sections'].includes(b.data.wanted);
  const block = known ? t(`view.block.${b.data.wanted}` as ViewMessageKey).toLowerCase() : b.data.wanted;
  return (
    <p className="flex items-center gap-2 rounded-lg border border-dashed border-line px-3 py-2.5 text-body-sm text-muted" data-testid="view-missing" data-wanted={b.data.wanted}>
      <Info aria-hidden className={cn('h-4 w-4 shrink-0 text-subtle')} />
      {t('view.missing', { block })}
    </p>
  );
});


/* ------------------------------------------------------------------- composed pages (Claude) */

/** The accent a page asked for, as a colour (each AA with white text). */
export const ACCENT: Record<string, string> = {
  blue: '#0A64D6', indigo: '#4F46E5', teal: '#0F766E', green: '#15803D', amber: '#B45309',
  orange: '#C2410C', red: '#B91C1C', pink: '#BE185D', purple: '#7E22CE', gray: '#4B5563', black: '#111113',
};

const Hero = z.object({ type: z.literal('hero'), title: z.string(), eyebrow: z.string(), summary: z.array(Item).max(6), accent: z.string().optional() });
const Stats = z.object({ type: z.literal('stats'), heading: z.string().optional(), items: z.array(z.object({ label: z.string(), value: z.string(), tags: Tags })).max(8) });
const Prose = z.object({ type: z.literal('prose'), heading: z.string().optional(), items: z.array(Item).max(20) });
const People = z.object({
  type: z.literal('people'),
  heading: z.string().optional(),
  people: z.array(z.object({ name: z.string(), role: z.string(), items: z.array(Item).max(20) })).max(40),
});
const Cards = z.object({ type: z.literal('cards'), heading: z.string().optional(), cards: z.array(z.object({ title: z.string(), items: z.array(Item).max(10) })).max(16) });
const Callout = z.object({ type: z.literal('callout'), heading: z.string().optional(), tone: z.enum(['info', 'warning', 'success']), items: z.array(Item).max(8) });

const WalnutHero = createComponentImplementation(DataApi('WalnutHero'), ({ props }) => {
  const b = Hero.safeParse(props.data);
  if (!b.success) return <Invalid />;
  return (
    <section className="relative overflow-hidden rounded-2xl border border-line bg-surface px-6 py-6 sm:px-7" data-block="hero" data-testid="view-block-hero" lang="en">
      <span aria-hidden className="absolute inset-y-0 left-0 w-1 bg-[var(--page-accent)]" />
      {b.data.eyebrow ? <p className="text-caption font-semibold text-[var(--page-accent)]">{b.data.eyebrow}</p> : null}
      {b.data.title ? <h4 className="mt-1 text-[1.5rem] font-semibold leading-tight tracking-[-0.015em] text-fg">{b.data.title}</h4> : null}
      {b.data.summary.length ? (
        <p className="mt-3 max-w-[64ch] text-body leading-7 text-fg">
          {b.data.summary.map((it, i) => (
            <span key={i}>
              <Said text={it.text} />
              <Chips tags={it.tags} />{' '}
            </span>
          ))}
        </p>
      ) : null}
    </section>
  );
});

const WalnutStats = createComponentImplementation(DataApi('WalnutStats'), ({ props }) => {
  const b = Stats.safeParse(props.data);
  if (!b.success) return <Invalid />;
  return (
    <Block kind="stats" heading={b.data.heading}>
      <dl className="grid grid-cols-2 gap-3 lg:grid-cols-3" lang="en">
        {b.data.items.map((it, i) => (
          <div key={i} className="rounded-xl border border-line bg-surface px-4 py-3.5" data-testid="view-stat">
            <dt className="text-caption text-muted">{it.label}</dt>
            <dd className="mt-1 flex items-baseline gap-1 text-[1.375rem] font-semibold tabular-nums tracking-[-0.01em] text-fg">
              {it.value}
              <span className="text-body-sm font-normal">
                <Chips tags={it.tags} />
              </span>
            </dd>
          </div>
        ))}
      </dl>
    </Block>
  );
});

const WalnutProse = createComponentImplementation(DataApi('WalnutProse'), ({ props }) => {
  const b = Prose.safeParse(props.data);
  if (!b.success) return <Invalid />;
  return (
    <Block kind="prose" heading={b.data.heading}>
      <div className="flex max-w-[68ch] flex-col gap-2 text-body leading-7 text-fg" lang="en">
        {b.data.items.map((it, i) => (
          <p key={i}>
            <Said text={it.text} />
            <Chips tags={it.tags} />
          </p>
        ))}
      </div>
    </Block>
  );
});

function initials(name: string): string {
  const parts = name.split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? '') + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
}

const WalnutPeople = createComponentImplementation(DataApi('WalnutPeople'), ({ props }) => {
  const { t } = useEnv();
  const b = People.safeParse(props.data);
  if (!b.success) return <Invalid />;
  return (
    <Block kind="people" heading={b.data.heading}>
      <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface" lang="en">
        {b.data.people.map((p, i) => (
          <li key={i}>
            <details className="group" open={i === 0} data-testid="view-person">
              <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-3 transition-colors duration-fast hover:bg-raised/60 [&::-webkit-details-marker]:hidden">
                <span aria-hidden className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--page-accent)] text-caption font-semibold text-white">
                  {initials(p.name)}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-body-sm font-semibold text-fg">{p.name}</span>
                  {p.role ? <span className="block truncate text-caption text-muted">{p.role}</span> : null}
                </span>
                <span className="shrink-0 rounded-full bg-raised px-2 py-0.5 text-caption tabular-nums text-muted" lang={undefined}>
                  {p.items.length === 1 ? t('view.contribution') : t('view.contributions', { n: p.items.length })}
                </span>
                <ChevronDown aria-hidden className="h-4 w-4 shrink-0 text-subtle transition-transform duration-fast group-open:rotate-180" />
              </summary>
              <ul className="flex flex-col gap-2 px-4 pb-4 pl-[3.75rem]">
                {p.items.map((it, j) => (
                  <li key={j} className="relative text-body-sm leading-6 text-fg before:absolute before:-left-3 before:top-[0.6rem] before:h-1 before:w-1 before:rounded-full before:bg-subtle">
                    <Said text={it.text} />
                    <Chips tags={it.tags} />
                  </li>
                ))}
              </ul>
            </details>
          </li>
        ))}
      </ul>
    </Block>
  );
});

const WalnutCards = createComponentImplementation(DataApi('WalnutCards'), ({ props }) => {
  const b = Cards.safeParse(props.data);
  if (!b.success) return <Invalid />;
  return (
    <Block kind="cards" heading={b.data.heading}>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2" lang="en">
        {b.data.cards.map((c, i) => (
          <div key={i} className="rounded-xl border border-line bg-surface px-4 py-3.5" data-testid="view-card">
            {c.title ? <p className="text-body-sm font-semibold text-fg">{c.title}</p> : null}
            <ul className="mt-1.5 flex flex-col gap-1.5">
              {c.items.map((it, j) => (
                <li key={j} className="text-body-sm leading-6 text-fg">
                  <Said text={it.text} />
                  <Chips tags={it.tags} />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </Block>
  );
});

const CALLOUT = {
  info: { icon: Info, cls: 'border-[#0A64D6]/30 bg-[#0A64D6]/[0.06]', ico: 'text-[#0A64D6]' },
  warning: { icon: AlertTriangle, cls: 'border-[#B45309]/35 bg-[#B45309]/[0.07]', ico: 'text-[#B45309]' },
  success: { icon: CheckCircle2, cls: 'border-[#15803D]/30 bg-[#15803D]/[0.06]', ico: 'text-[#15803D]' },
} as const;

const WalnutCallout = createComponentImplementation(DataApi('WalnutCallout'), ({ props }) => {
  const b = Callout.safeParse(props.data);
  if (!b.success) return <Invalid />;
  const c = CALLOUT[b.data.tone];
  const Icon = c.icon;
  return (
    <section className={cn('flex gap-3 rounded-xl border px-4 py-3.5', c.cls)} data-block="callout" data-testid="view-block-callout" data-tone={b.data.tone} lang="en">
      <Icon aria-hidden className={cn('mt-0.5 h-4 w-4 shrink-0', c.ico)} />
      <div className="min-w-0">
        {b.data.heading ? <h4 className="text-body-sm font-semibold text-fg">{b.data.heading}</h4> : null}
        <ul className={cn('flex flex-col gap-1', b.data.heading && 'mt-1')}>
          {b.data.items.map((it, j) => (
            <li key={j} className="text-body-sm leading-6 text-fg">
              <Said text={it.text} />
              <Chips tags={it.tags} />
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
});

/** The catalog every view surface uses: these components and no others. */
export function walnutCatalog() {
  return new Catalog(CATALOG_ID, 'v0.9', [
    WalnutPage,
    WalnutFacts,
    WalnutTable,
    WalnutChart,
    WalnutSteps,
    WalnutTimeline,
    WalnutConversation,
    WalnutSections,
    WalnutSources,
    WalnutMissing,
    WalnutHero,
    WalnutStats,
    WalnutProse,
    WalnutPeople,
    WalnutCards,
    WalnutCallout,
  ]);
}
