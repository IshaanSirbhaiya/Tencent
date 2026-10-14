'use client';
import { Filter, MessageSquareText, RotateCcw, Search } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { AppShell } from '@/components/AppShell';
import { AuditDrawer } from '@/components/audit/AuditDrawer';
import { AuditTable } from '@/components/audit/AuditTable';
import { InfoTip } from '@/components/InfoTip';
import { VerifyPanel } from '@/components/audit/VerifyPanel';
import { Button, Field, inputClass } from '@/components/ui';
import { api } from '@/lib/api';
import { fmtDateTime } from '@/lib/format';
import { AUDIT_SUGGESTIONS, PERSONAS } from '@/lib/personas';
import type { AuditEvent, AuditFilter } from '@/lib/types';

const FILTER_LABEL: Record<string, string> = {
  user: 'User',
  container: 'Container',
  doc: 'Document',
  since: 'Since',
  until: 'Until',
  decision: 'Decision',
};

function chipValue(k: string, v: unknown): string {
  if ((k === 'since' || k === 'until') && typeof v === 'string') return fmtDateTime(v);
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

function AuditView() {
  const [form, setForm] = useState(EMPTY);
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nlq, setNlq] = useState('');
  const [parsed, setParsed] = useState<Record<string, unknown> | null>(null);
  const [nlBusy, setNlBusy] = useState(false);
  const [open, setOpen] = useState<AuditEvent | null>(null);
  const [status, setStatus] = useState('');

  const load = useCallback(async (f: typeof EMPTY) => {
    setLoading(true);
    setError(null);
    const filter: AuditFilter = {
      user: f.user || undefined,
      container: f.container || undefined,
      doc: f.doc || undefined,
      since: toIso(f.since, false),
      until: toIso(f.until, true),
      decision: f.decision || undefined,
    };
    try {
      const ev = await api.auditEvents(filter);
      setEvents(ev);
      setStatus(`${ev.length} request${ev.length === 1 ? '' : 's'} found.`);
    } catch {
      setError('Audit events are unavailable right now.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(EMPTY);
  }, [load]);

  async function askAudit(question: string) {
    const text = question.trim();
    if (!text) return;
    setNlq(text);
    setNlBusy(true);
    setError(null);
    setParsed(null);
    try {
      const r = await api.auditAsk(text);
      setParsed(r.filter ?? {});
      const f = r.filter as Record<string, string | undefined>;
      setForm({
        user: f.user ?? '',
        container: f.container ?? '',
        doc: f.doc ?? '',
        since: toDateInput(f.since),
        until: toDateInput(f.until),
        decision: f.decision ?? '',
      });
      setEvents(r.events ?? []);
      setStatus(`Interpreted the question as a filter; ${(r.events ?? []).length} requests found.`);
    } catch {
      setError('The audit question could not be interpreted right now.');
    } finally {
      setNlBusy(false);
    }
  }

  const set = (k: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const parsedEntries = parsed ? Object.entries(parsed).filter(([, v]) => v !== null && v !== undefined && v !== '') : [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Audit console</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted">
          Every request is written to the hash-chained ledger before an answer is released. This view shows metadata: who asked, what was considered, every access decision and what was released. Answer text needs a reasoned, logged reveal.
        </p>
      </div>

      <VerifyPanel />

      <section aria-labelledby="nl-heading" className="rounded-lg border border-line bg-surface p-4">
        <h2 id="nl-heading" className="flex items-center gap-2 text-sm font-semibold">
          <MessageSquareText aria-hidden className="h-4 w-4 text-muted" /> Ask the audit log
        </h2>
        <form
          className="mt-3 flex flex-col gap-2 sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault();
            void askAudit(nlq);
          }}
        >
          <label htmlFor="nl-question" className="sr-only">
            Audit question in plain English
          </label>
          <input
            id="nl-question"
            value={nlq}
            onChange={(e) => setNlq(e.target.value)}
            placeholder="e.g. Show every denied retrieval last week."
            className={`${inputClass} h-10`}
            aria-describedby="nl-hint"
          />
          <Button type="submit" variant="primary" icon={Search} busy={nlBusy} disabled={!nlq.trim()}>
            Interpret
          </Button>
        </form>
        <p id="nl-hint" className="mt-1.5 text-2xs text-muted">
          Turned into a closed, typed filter by a deterministic parser. You see the filter before the results; no model ever sees audit results.
        </p>
        <ul className="mt-3 flex flex-wrap gap-2" aria-label="Example audit questions">
          {AUDIT_SUGGESTIONS.map((s) => (
            <li key={s}>
              <button type="button" onClick={() => void askAudit(s)} className="rounded-md border border-line-strong px-2.5 py-1 text-left text-xs hover:bg-raised">
                {s}
              </button>
            </li>
          ))}
        </ul>
        {parsed ? (
          <div className="mt-3 rounded-md border border-line bg-raised/50 px-3 py-2" data-testid="parsed-filter">
            <p className="text-2xs font-semibold uppercase tracking-wide text-muted">Interpreted as</p>
            {parsedEntries.length === 0 ? (
              <p className="mt-1 text-xs">No filter recognised — showing all requests.</p>
            ) : (
              <ul className="mt-1.5 flex flex-wrap gap-1.5">
                {parsedEntries.map(([k, v]) => (
                  <li key={k} className="inline-flex items-center gap-1 rounded border border-accent/40 bg-accent/10 px-2 py-0.5 text-xs">
                    <span className="text-muted">{FILTER_LABEL[k] ?? k}:</span>
                    <span className="font-medium">{chipValue(k, v)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : null}
      </section>

      <section aria-labelledby="filters-heading" className="rounded-lg border border-line bg-surface p-4">
        <h2 id="filters-heading" className="flex items-center gap-2 text-sm font-semibold">
          <Filter aria-hidden className="h-4 w-4 text-muted" /> Filters
        </h2>
        <form
          className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-6"
          onSubmit={(e) => {
            e.preventDefault();
            setParsed(null);
            void load(form);
          }}
        >
          <Field id="f-user" label="User">
            <select id="f-user" value={form.user} onChange={set('user')} className={inputClass}>
              <option value="">Anyone</option>
              {PERSONAS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.id}
                </option>
              ))}
            </select>
          </Field>
          <Field id="f-container" label="Container">
            <input id="f-container" value={form.container} onChange={set('container')} placeholder="space, project, channel" className={inputClass} />
          </Field>
          <Field id="f-doc" label="Document">
            <input id="f-doc" value={form.doc} onChange={set('doc')} placeholder="doc id, e.g. confluence:cf-1001" className={inputClass} />
          </Field>
          <Field id="f-since" label="Since">
            <input id="f-since" type="date" value={form.since} onChange={set('since')} className={inputClass} />
          </Field>
          <Field id="f-until" label="Until">
            <input id="f-until" type="date" value={form.until} onChange={set('until')} className={inputClass} />
          </Field>
          <Field id="f-decision" label="Decision">
            <select id="f-decision" value={form.decision} onChange={set('decision')} className={inputClass}>
              <option value="">Any</option>
              <option value="allow">Allow</option>
              <option value="deny">Deny</option>
            </select>
          </Field>
          <div className="flex gap-2 sm:col-span-2 lg:col-span-6">
            <Button type="submit" variant="primary" icon={Filter} busy={loading}>
              Apply filters
            </Button>
            <Button
              type="button"
              icon={RotateCcw}
              onClick={() => {
                setForm(EMPTY);
                setParsed(null);
                void load(EMPTY);
              }}
            >
              Clear
            </Button>
          </div>
        </form>
      </section>

      <section aria-labelledby="results-heading" className="space-y-2" data-tour="audit-table">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1">
            <h2 id="results-heading" className="text-sm font-semibold">
              Requests
            </h2>
            <InfoTip title="Requests in the audit log" testId="infotip-audit">
              One row per question: who asked, which sources were considered, whether each one was allowed, and what the answer used. The answer text itself stays hidden unless you give a reason, and that reveal is logged too.
            </InfoTip>
          </div>
          {events ? (
            <span className="text-xs text-muted tabular" data-testid="result-count">
              {events.length} shown
            </span>
          ) : null}
        </div>
        <div role="status" aria-live="polite" className="sr-only">
          {status}
        </div>
        {error ? (
          <p role="alert" className="rounded-lg border border-line bg-surface p-4 text-sm">
            {error}
          </p>
        ) : !events ? (
          <p className="text-sm text-muted">Loading audit events…</p>
        ) : events.length === 0 ? (
          <p className="rounded-lg border border-line bg-surface p-4 text-sm text-muted">No requests match these filters.</p>
        ) : (
          <AuditTable events={events} onOpen={setOpen} />
        )}
      </section>

      <AuditDrawer event={open} onClose={() => setOpen(null)} />
    </div>
  );
}

export default function AuditPage() {
  return (
    <AppShell requireRole="compliance_officer">
      <AuditView />
    </AppShell>
  );
}
