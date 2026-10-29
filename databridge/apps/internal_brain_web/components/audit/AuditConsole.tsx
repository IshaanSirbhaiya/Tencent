'use client';
// /dev/audit (compliance_officer). Search the ledger in plain English, read the results as a dense
// table, open any request end to end, verify the chain, and run the sandbox tamper demo.
// Query params for Presenter mode: ?verify=1 runs Verify, ?tamper=1 runs the tamper demo.
import { Filter, ListChecks, MessageSquareText, RotateCcw, Search, Sparkles, X } from 'lucide-react';
import { useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Page } from '@/components/shell/Page';
import { Button, Field, SkeletonText, inputClass } from '@/components/ui';
import { auditApi } from '@/lib/api';
import { fmtDate, fmtDateTime, fmtTime } from '@/lib/format';
import { useMessages } from '@/lib/i18n';
import { auditMessages, type AuditKey } from '@/lib/i18n/audit';
import { AUDIT_SUGGESTIONS, PERSONAS } from '@/lib/personas';
import type { AuditFilter, AuditRow } from '@/lib/types';
import { AuditDrawer } from './AuditDrawer';
import { AuditTable } from './AuditTable';
import { TamperDemo } from './TamperDemo';
import { VerifyPanel } from './VerifyPanel';

const FILTER_KEYS = ['user', 'container', 'doc', 'since', 'until', 'decision'] as const;
type FilterKey = (typeof FILTER_KEYS)[number];
const LABEL_KEY: Record<FilterKey, AuditKey> = {
  user: 'audit.filters.user',
  container: 'audit.filters.container',
  doc: 'audit.filters.doc',
  since: 'audit.filters.since',
  until: 'audit.filters.until',
  decision: 'audit.filters.decision',
};

function chipValue(k: string, v: unknown): string {
  // with the year: "the last 365 days" spans two years, and without it since and until looked equal
  if ((k === 'since' || k === 'until') && typeof v === 'string') return `${fmtDate(v)}, ${fmtTime(v)}`;
  if (Array.isArray(v)) return v.join(', ');
  if (v && typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

/** <input type="date"> value ⇄ ISO at the Singapore day boundary. */
const toIso = (d: string, end: boolean) => (d ? `${d}T${end ? '23:59:59' : '00:00:00'}+08:00` : undefined);
const toDateInput = (iso?: string) => {
  if (!iso) return '';
  const x = new Date(iso);
  if (Number.isNaN(x.getTime())) return '';
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Singapore' }).format(x);
};

const EMPTY = { user: '', container: '', doc: '', since: '', until: '', decision: '' };
type Form = typeof EMPTY;

function formFromFilter(f: Record<string, unknown>): Form {
  const s = (k: string) => (typeof f[k] === 'string' ? (f[k] as string) : '');
  return { user: s('user'), container: s('container'), doc: s('doc'), since: toDateInput(s('since')), until: toDateInput(s('until')), decision: s('decision') };
}

function filterFromForm(f: Form): AuditFilter {
  return {
    user: f.user || undefined,
    container: f.container || undefined,
    doc: f.doc || undefined,
    since: toIso(f.since, false),
    until: toIso(f.until, true),
    decision: f.decision || undefined,
  };
}

/** The interpreted filter as the /audit/events query (closed keys only, strings only). */
function asQuery(p: Record<string, unknown>): AuditFilter {
  const out: AuditFilter = {};
  for (const k of FILTER_KEYS) if (typeof p[k] === 'string' && p[k]) out[k] = p[k] as string;
  return out;
}

/** The parser model as a person would name it ("hy3" → "Tencent Hunyuan Hy3"). */
function modelName(m?: string | null): string {
  if (!m) return 'a model';
  if (/^hy3/i.test(m)) return 'Tencent Hunyuan Hy3';
  if (/deepseek/i.test(m)) return `DeepSeek (${m})`;
  return m;
}

/** Scroll a panel to the top of the view (instantly when motion is reduced). */
export function scrollToPanel(el: HTMLElement | null) {
  if (!el) return;
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches || document.documentElement.dataset.motion === 'reduce';
  el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
}

export function AuditConsole() {
  const t = useMessages(auditMessages);
  const params = useSearchParams();
  const autoVerify = params.get('verify') === '1';
  const autoTamper = params.get('tamper') === '1';

  const [form, setForm] = useState<Form>(EMPTY);
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nlq, setNlq] = useState('');
  const [parsed, setParsed] = useState<Record<string, unknown> | null>(null);
  const [parser, setParser] = useState<{ by: string; model?: string | null; fallback?: boolean | null; notes?: string[] } | null>(null);
  const [nlBusy, setNlBusy] = useState(false);
  const [open, setOpen] = useState<AuditRow | null>(null);
  // Kept as a key + count and rendered with t(), so it follows a language switch (it used to stay English).
  const [status, setStatus] = useState<{ key: 'audit.status.found' | 'audit.status.interpreted' | 'audit.table.empty'; n: number } | null>(null);
  const verifyRef = useRef<HTMLElement>(null);
  const tamperRef = useRef<HTMLElement>(null);

  const load = useCallback(
    async (filter: AuditFilter) => {
      setLoading(true);
      setError(null);
      try {
        const ev = await auditApi.events(filter);
        setRows(ev);
        setStatus({ key: 'audit.status.found', n: ev.length });
      } catch {
        setError(t('audit.table.error'));
      } finally {
        setLoading(false);
      }
    },
    [t],
  );

  // ?latest=1 (Presenter step 7, after the WorkBuddy ask): reload, then mark and reveal the newest
  // request. Re-runs when the presenter pushes the URL again while this page is already open.
  const wantLatest = params.get('latest') === '1';
  const [latestId, setLatestId] = useState<string | null>(null);
  const [latestTick, setLatestTick] = useState(0);
  const paramKey = params.toString();

  useEffect(() => {
    let live = true;
    if (!wantLatest) {
      setLatestId(null);
      void load({});
      return;
    }
    setForm(EMPTY);
    setParsed(null);
    setParser(null);
    setNlq('');
    setRows(null);
    void (async () => {
      setLoading(true);
      setError(null);
      try {
        const ev = await auditApi.events({});
        if (!live) return;
        setRows(ev);
        // newest request first; pick by time so the choice never depends on the server's order
        const newest = ev.reduce<AuditRow | null>((m, r) => (!m || (r.event_time ?? '') > (m.event_time ?? '') ? r : m), null);
        setLatestId(newest?.req_id ?? null);
        setLatestTick((n) => n + 1);
        setStatus({ key: newest ? 'audit.status.found' : 'audit.table.empty', n: ev.length });
      } catch {
        if (live) setError(t('audit.table.error'));
      } finally {
        if (live) setLoading(false);
      }
    })();
    return () => {
      live = false;
    };
    // first load, and again whenever the URL's query changes; later loads come from the search and filters
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wantLatest, paramKey]);

  // bring the highlighted row into view once it is in the table
  useEffect(() => {
    if (!latestId) return;
    const raf = requestAnimationFrame(() => {
      const row = document.querySelector<HTMLElement>(`[data-testid="audit-table"] [data-latest]`)?.closest<HTMLElement>('tr');
      if (row) {
        const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches || document.documentElement.dataset.motion === 'reduce';
        row.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'center' });
      }
    });
    return () => cancelAnimationFrame(raf);
  }, [latestId, latestTick]);

  // Presenter mode: bring the panel being run into view once the table above it has its height.
  const loaded = rows !== null || error !== null;
  useEffect(() => {
    if (loaded) scrollToPanel(autoTamper ? tamperRef.current : autoVerify ? verifyRef.current : null);
  }, [loaded, autoTamper, autoVerify]);

  async function askAudit(question: string) {
    const text = question.trim();
    if (!text) return;
    setNlq(text);
    setNlBusy(true);
    setError(null);
    setParsed(null);
    try {
      const r = await auditApi.ask(text);
      setParsed(r.filter ?? {});
      const notes = (Array.isArray(r.notes) ? r.notes : r.notes ? [r.notes] : []).filter((n) => typeof n === 'string' && n);
      setParser(r.parser ? { by: r.parser, model: r.parser_model, fallback: r.parser_fallback, notes } : null);
      setForm(formFromFilter(r.filter ?? {}));
      setRows(r.events);
      setStatus({ key: 'audit.status.interpreted', n: r.events.length });
    } catch {
      setError(t('audit.table.askError'));
    } finally {
      setNlBusy(false);
    }
  }

  function removeChip(k: string) {
    if (!parsed) return;
    const next = { ...parsed };
    delete next[k];
    setParsed(next);
    setForm(formFromFilter(next));
    void load(asQuery(next));
  }

  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const parsedEntries = parsed ? Object.entries(parsed).filter(([, v]) => v !== null && v !== undefined && v !== '') : [];
  // The drawer has no Radix trigger, so remember what opened it (row or Details button) for Esc.
  const opener = useRef<HTMLElement | null>(null);
  const onOpen = useCallback((r: AuditRow) => {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setOpen(r);
  }, []);

  return (
    <Page eyebrow={t('audit.eyebrow')} title={t('audit.title')} description={t('audit.description')} width="wide">
      <div className="space-y-6">
        {/* Search: plain English → a closed filter, shown before the results */}
        <section aria-labelledby="nl-heading" className="rounded-xl border border-line bg-surface">
          <div className="p-4">
            <h2 id="nl-heading" className="flex items-center gap-2 text-heading-sm text-fg">
              <MessageSquareText aria-hidden className="h-4 w-4 text-muted" /> {t('audit.search.heading')}
            </h2>
            <form
              className="mt-3 flex flex-col gap-2 sm:flex-row"
              onSubmit={(e) => {
                e.preventDefault();
                void askAudit(nlq);
              }}
            >
              <label htmlFor="nl-question" className="sr-only">
                {t('audit.search.label')}
              </label>
              <div className="relative min-w-0 flex-1">
                <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" />
                <input
                  id="nl-question"
                  value={nlq}
                  onChange={(e) => setNlq(e.target.value)}
                  placeholder={t('audit.search.placeholder')}
                  className={`${inputClass} h-10 pl-9`}
                  aria-describedby="nl-hint"
                />
              </div>
              <Button type="submit" variant="primary" size="lg" busy={nlBusy} disabled={!nlq.trim()}>
                {t('audit.search.submit')}
              </Button>
            </form>
            <p id="nl-hint" className="mt-1.5 text-2xs text-muted">
              {t('audit.search.hint')}
            </p>
            <ul className="mt-3 flex flex-wrap gap-2" aria-label={t('audit.search.examples')}>
              {AUDIT_SUGGESTIONS.map((s) => (
                <li key={s}>
                  {/* The examples are English on purpose (the audit parser reads English): tagged lang="en". */}
                  <button
                    type="button"
                    lang="en"
                    onClick={() => void askAudit(s)}
                    className="min-h-[1.75rem] rounded-full border border-line-strong px-3 py-1 text-left text-xs text-fg hover:border-accent/50 hover:bg-accent/5"
                  >
                    {s}
                  </button>
                </li>
              ))}
            </ul>
            {parsed ? (
              <div className="mt-3 rounded-lg border border-accent/30 bg-accent/[0.05] px-3 py-2" data-testid="parsed-filter">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-micro font-semibold uppercase tracking-wide text-subtle">{t('audit.search.interpreted')}</p>
                  {parser ? (
                    <span className="inline-flex items-center gap-1 text-caption text-muted" data-testid="audit-parser">
                      {parser.by === 'llm' ? <Sparkles aria-hidden className="h-3.5 w-3.5" /> : <ListChecks aria-hidden className="h-3.5 w-3.5" />}
                      {parser.by === 'llm' ? t('audit.search.byModel', { model: modelName(parser.model) }) : t('audit.search.byRules')}
                    </span>
                  ) : null}
                </div>
                {parser?.fallback ? <p className="mt-1 text-caption text-muted">{t('audit.search.fallback')}</p> : null}
                {!parser?.fallback && parser?.notes?.length ? (
                  <p className="mt-1 text-caption text-muted" data-testid="audit-parser-notes">
                    {parser.notes.join(' · ')}
                  </p>
                ) : null}
                {parsedEntries.length === 0 ? (
                  <p className="mt-1 text-xs">{t('audit.search.noFilter')}</p>
                ) : (
                  <ul className="mt-1.5 flex flex-wrap gap-1.5">
                    {parsedEntries.map(([k, v]) => {
                      const label = FILTER_KEYS.includes(k as FilterKey) ? t(LABEL_KEY[k as FilterKey]) : k;
                      return (
                        <li key={k} className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-surface py-0.5 pl-2.5 pr-0.5 text-xs">
                          <span className="text-muted">{label}:</span>
                          <span className="font-medium">{chipValue(k, v)}</span>
                          <button
                            type="button"
                            onClick={() => removeChip(k)}
                            aria-label={t('audit.search.remove', { label: `${label}: ${chipValue(k, v)}` })}
                            className="ml-0.5 inline-flex h-6 w-6 items-center justify-center rounded-full text-muted hover:bg-raised hover:text-fg"
                          >
                            <X aria-hidden className="h-3 w-3" />
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            ) : null}
          </div>

          {/* Facet filters */}
          <form
            aria-labelledby="filters-heading"
            className="grid gap-3 border-t border-line bg-raised/30 px-4 py-3 sm:grid-cols-2 lg:grid-cols-[repeat(6,minmax(0,1fr))_auto]"
            onSubmit={(e) => {
              e.preventDefault();
              setParsed(null);
              void load(filterFromForm(form));
            }}
          >
            <h2 id="filters-heading" className="flex items-center gap-1.5 text-micro font-semibold uppercase tracking-wide text-subtle sm:col-span-2 lg:col-span-7">
              <Filter aria-hidden className="h-3.5 w-3.5" /> {t('audit.filters.heading')}
            </h2>
            <Field id="f-user" label={t('audit.filters.user')}>
              <select id="f-user" value={form.user} onChange={set('user')} className={inputClass}>
                <option value="">{t('audit.filters.anyone')}</option>
                {PERSONAS.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.id}
                  </option>
                ))}
              </select>
            </Field>
            <Field id="f-container" label={t('audit.filters.container')}>
              <input id="f-container" value={form.container} onChange={set('container')} placeholder={t('audit.filters.containerPh')} className={inputClass} />
            </Field>
            <Field id="f-doc" label={t('audit.filters.doc')}>
              <input id="f-doc" value={form.doc} onChange={set('doc')} placeholder={t('audit.filters.docPh')} className={inputClass} />
            </Field>
            <Field id="f-since" label={t('audit.filters.since')}>
              <input id="f-since" type="date" value={form.since} onChange={set('since')} className={inputClass} />
            </Field>
            <Field id="f-until" label={t('audit.filters.until')}>
              <input id="f-until" type="date" value={form.until} onChange={set('until')} className={inputClass} />
            </Field>
            <Field id="f-decision" label={t('audit.filters.decision')}>
              <select id="f-decision" value={form.decision} onChange={set('decision')} className={inputClass}>
                <option value="">{t('audit.filters.any')}</option>
                <option value="allow">{t('audit.filters.allow')}</option>
                <option value="deny">{t('audit.filters.deny')}</option>
              </select>
            </Field>
            <div className="flex items-end gap-2 sm:col-span-2 lg:col-span-1">
              <Button type="submit" icon={Filter} busy={loading}>
                {t('audit.filters.apply')}
              </Button>
              <Button
                type="button"
                variant="ghost"
                icon={RotateCcw}
                onClick={() => {
                  setForm(EMPTY);
                  setParsed(null);
                  setParser(null);
                  setNlq('');
                  void load({});
                }}
              >
                {t('audit.filters.clear')}
              </Button>
            </div>
          </form>
        </section>

        {/* Results */}
        <section aria-labelledby="results-heading" className="space-y-2" data-tour="audit-table">
          <div className="flex items-baseline justify-between gap-2">
            <h2 id="results-heading" className="text-heading-sm text-fg">
              {t('audit.table.heading')}
            </h2>
            {rows ? (
              <span className="text-caption text-muted tabular-nums" data-testid="result-count">
                {t('audit.table.shown', { n: rows.length })}
              </span>
            ) : null}
          </div>
          <div role="status" aria-live="polite" className="sr-only">
            {status ? t(status.key, { n: status.n.toLocaleString('en-SG') }) : ''}
          </div>
          {error ? (
            <p role="alert" className="rounded-lg border border-line bg-surface p-4 text-body-sm">
              {error}
            </p>
          ) : !rows ? (
            <div className="rounded-lg border border-line bg-surface p-4">
              <SkeletonText lines={6} label={t('audit.table.loading')} />
            </div>
          ) : (
            <AuditTable rows={rows} onOpen={onOpen} openId={open?.req_id} latestId={latestId} />
          )}
        </section>

        {/* Integrity: verify the live chain, then attack a copy of it */}
        <VerifyPanel ref={verifyRef} autoRun={autoVerify} />
        <TamperDemo ref={tamperRef} autoRun={autoTamper} />
      </div>

      <AuditDrawer row={open} onClose={() => setOpen(null)} returnFocus={() => opener.current} />
    </Page>
  );
}
