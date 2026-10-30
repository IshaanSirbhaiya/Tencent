'use client';
// "Act on this answer": the row under an answer, and the right-hand sheet for a reply draft or a
// follow-up ticket proposal (POST /ib/act/*). Everything shown comes from the API. The draft is
// never sent by the Brain; a ticket is created only after Confirm. Rule 4: a 404 is one sentence
// ("nothing to act on") whatever the reason, and a 409 is one polite sentence, never a reason.
import { Check, CircleCheck, Copy, ExternalLink, FilePenLine, Info, MessageSquareReply, RefreshCw, RotateCcw, ShieldCheck, Ticket, TriangleAlert } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { PlatformIcon } from '@/components/PlatformIcon';
import { Badge, Button, Sheet, SheetContent, SkeletonText, inputClass } from '@/components/ui';
import { ApiError, actApi } from '@/lib/api';
import { cn } from '@/lib/cn';
import { placeLabel } from '@/lib/format';
import { personaById } from '@/lib/personas';
import { useLang, useMessages } from '@/lib/i18n';
import { actMessages, type ActKey } from '@/lib/i18n/act';
import type { ActCitation, ActDraft, AskResponse, TicketCreated, TicketProposal } from '@/lib/types';
import { safeHref } from '@/lib/url';
import { toast } from './Toast';

export type ActMode = 'draft' | 'ticket';

// 'unknown' = a 409 outcome_unknown: an earlier create from this answer may have happened (never
// "nothing was changed", on propose as on confirm; round 4 Codex A6). 'rate' = a 429.
type Fail = 'nothing' | 'unavailable' | 'unknown' | 'rate' | 'error';
const failOf = (e: unknown): Fail => {
  if (!(e instanceof ApiError)) return 'error';
  if (e.status === 404) return 'nothing';
  if (e.status === 409) return e.message === 'outcome_unknown' ? 'unknown' : 'unavailable';
  if (e.status === 429) return 'rate';
  return 'error';
};

/** "priya.sre: Blocker ..." (a chat line quoted in an answer): the speaker, shown as a name. */
const SPEAKER = /^([a-z][a-z0-9._-]{1,40}):\s+(\S[\s\S]*)$/;
function Spoken({ text }: { text: string }) {
  const m = SPEAKER.exec(text);
  if (!m) return <>{text}</>;
  const who = personaById(m[1]);
  return (
    <>
      <span className="font-medium" data-testid="act-draft-speaker">
        {who?.fullName ?? m[1]}
      </span>
      {': '}
      {m[2]}
    </>
  );
}

/** The draft label the server returns for every draft (translated in the UI; anything else is shown as is). */
const SERVER_DRAFT_LABEL = 'Draft for your review';

/** In 简体中文 only: one line saying the draft and the ticket text are the server's English, as returned. */
function ServerLangNote() {
  const t = useMessages(actMessages);
  const { lang } = useLang();
  if (lang !== 'zh') return null;
  return (
    <p className="mt-2 text-2xs text-muted" data-testid="act-lang-note">
      {t('act.serverLang')}
    </p>
  );
}

/** The two buttons under a released answer. */
export function ActRow({ response }: { response: AskResponse }) {
  const t = useMessages(actMessages);
  const [mode, setMode] = useState<ActMode | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const headingId = useId();
  const btn =
    'inline-flex h-8 items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 text-caption font-medium text-fg transition-colors duration-fast hover:border-line-strong hover:bg-raised';
  return (
    <section aria-labelledby={headingId} className="flex flex-wrap items-center gap-x-2 gap-y-1.5" data-testid="act-row">
      <h3 id={headingId} className="mr-1 text-micro font-semibold uppercase tracking-wider text-subtle">
        {t('act.heading')}
      </h3>
      <button
        type="button"
        className={btn}
        data-testid="act-draft"
        onClick={(e) => {
          opener.current = e.currentTarget;
          setMode('draft');
        }}
      >
        <MessageSquareReply aria-hidden className="h-3.5 w-3.5 text-muted" />
        {t('act.draft')}
      </button>
      <button
        type="button"
        className={btn}
        data-testid="act-ticket"
        onClick={(e) => {
          opener.current = e.currentTarget;
          setMode('ticket');
        }}
      >
        <Ticket aria-hidden className="h-3.5 w-3.5 text-muted" />
        {t('act.ticket')}
      </button>
      <span className="basis-full text-caption text-subtle sm:basis-auto">{t('act.hint')}</span>
      <ActSheet response={response} mode={mode} onClose={() => setMode(null)} onMode={setMode} returnFocusTo={opener.current} />
    </section>
  );
}

/** 1-based number of a tag in the answer's own citation list (the same numbers as its chips). */
function numberOf(response: AskResponse, tag: string, fallback: ActCitation[]): number {
  const i = response.citations.findIndex((c) => c.tag === tag);
  if (i >= 0) return i + 1;
  return fallback.findIndex((c) => c.tag === tag) + 1;
}

function SourceBadge({ n, c }: { n: number; c?: ActCitation }) {
  return (
    <span
      className="mx-[1px] inline-flex h-5 min-w-5 items-center justify-center rounded border border-line-strong bg-raised px-1 align-middle text-caption font-semibold tabular-nums leading-none text-muted"
      title={c ? `${c.title} · ${c.container_name}` : undefined}
      aria-label={c ? `[${n}] ${c.title}` : `[${n}]`}
      data-testid="act-cite"
    >
      {n}
    </span>
  );
}

function SourceList({ response, citations, title }: { response: AskResponse; citations: ActCitation[]; title: string }) {
  const seen = new Set<string>();
  const rows = citations.filter((c) => (seen.has(c.doc_id) ? false : (seen.add(c.doc_id), true)));
  if (!rows.length) return null;
  return (
    <section className="mt-5">
      <h3 className="mb-2 text-micro font-semibold uppercase tracking-wider text-subtle">{title}</h3>
      <ol className="flex flex-col gap-1.5" data-testid="act-sources">
        {rows.map((c) => (
          <li key={c.doc_id} className="flex min-w-0 items-center gap-2 rounded-md border border-line bg-surface px-2.5 py-2 text-body-sm">
            <span className="inline-flex h-5 min-w-5 items-center justify-center rounded border border-line-strong px-1 text-caption font-semibold tabular-nums">
              {numberOf(response, c.tag, citations)}
            </span>
            <PlatformIcon platform={c.platform} tone="platform" size="sm" showLabel={false} />
            <span className="min-w-0 flex-1 truncate text-fg">{c.title}</span>
            <span className="hidden shrink-0 truncate text-caption text-muted sm:inline">{placeLabel(c.platform, c.container_name)}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function Failure({ kind, onRetry, onDraft, t }: { kind: Fail; onRetry: () => void; onDraft?: () => void; t: (key: ActKey) => string }) {
  if (kind === 'error' || kind === 'rate') {
    return (
      <div role="alert" className="flex flex-col items-center px-4 py-12 text-center" data-testid={kind === 'rate' ? 'act-rate' : 'act-error'}>
        <TriangleAlert aria-hidden className="mb-3 h-6 w-6 text-warn" />
        <p className="text-body-sm text-fg">{kind === 'rate' ? t('act.rateLimited') : t('act.error')}</p>
        <Button className="mt-4" size="sm" icon={RefreshCw} onClick={onRetry}>
          {t('act.retry')}
        </Button>
      </div>
    );
  }
  if (kind === 'unknown') {
    // An earlier create from this answer may have happened: never "nothing was changed".
    return (
      <div role="alert" className="flex flex-col items-center px-4 py-12 text-center" data-testid="act-outcome-unknown">
        <TriangleAlert aria-hidden className="mb-3 h-6 w-6 text-warn" />
        <p className="max-w-sm text-body-sm text-fg">{t('act.ticket.outcomeUnknown')}</p>
      </div>
    );
  }
  if (kind === 'unavailable') {
    // One sentence, no reason (rule 4), and the way on: the reply draft is still there.
    return (
      <div className="flex flex-col items-center px-4 py-10 text-center" data-testid="act-unavailable">
        <p role="status" className="max-w-sm text-heading-sm text-fg" data-testid="act-unavailable-text">
          {t('act.ticket.blocked')}
        </p>
        {onDraft ? (
          <Button className="mt-4" size="sm" icon={MessageSquareReply} onClick={onDraft} data-testid="act-unavailable-draft">
            {t('act.draft')}
          </Button>
        ) : null}
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center px-4 py-12 text-center" data-testid="act-nothing">
      <span aria-hidden className="mb-3 inline-flex h-10 w-10 items-center justify-center rounded-lg border border-line bg-raised text-muted">
        <Info className="h-5 w-5" />
      </span>
      <p className="max-w-sm text-heading-sm text-fg">{t('act.nothing')}</p>
      <p className="mt-1 max-w-sm text-body-sm text-muted">{t('act.nothingHint')}</p>
    </div>
  );
}

export interface ActSheetProps {
  response: AskResponse;
  mode: ActMode | null;
  onClose: () => void;
  /** Switch the open sheet to the other action (the "Draft a reply" way on from a blocked ticket). */
  onMode?: (mode: ActMode) => void;
  returnFocusTo?: HTMLElement | null;
}

export function ActSheet({ response, mode, onClose, onMode, returnFocusTo }: ActSheetProps) {
  const t = useMessages(actMessages);
  const returnRef = useRef<HTMLElement | null>(null);
  // The ticket can't be made: the header drops "Create…" and "Nothing is created until you confirm"
  // so the one status sentence in the body is not contradicted (round 4 Sonnet UX).
  const [blocked, setBlocked] = useState(false);
  useEffect(() => setBlocked(false), [mode]);
  if (returnFocusTo) returnRef.current = returnFocusTo;
  const open = mode !== null;
  return (
    <Sheet open={open} onOpenChange={(o) => (o ? undefined : onClose())}>
      {open ? (
        <SheetContent
          width="lg"
          title={mode === 'draft' ? t('act.draft.sheetTitle') : blocked ? t('act.ticket.titleBlocked') : t('act.ticket.title')}
          description={mode === 'draft' ? t('act.draft.desc') : blocked ? undefined : t('act.ticket.desc')}
          closeLabel={t('act.close')}
          data-testid={mode === 'draft' ? 'act-draft-sheet' : 'act-ticket-sheet'}
          onCloseAutoFocus={(e) => {
            const el = returnRef.current;
            if (el && el.isConnected) {
              e.preventDefault();
              el.focus();
            }
          }}
        >
          {mode === 'draft' ? (
            <DraftView response={response} />
          ) : (
            <TicketView response={response} onDone={onClose} onBlocked={setBlocked} onDraft={onMode ? () => onMode('draft') : undefined} />
          )}
        </SheetContent>
      ) : null}
    </Sheet>
  );
}

/**
 * One request per sheet open and attempt. Each call writes a sealed ledger record (and a ticket
 * proposal), so React's development double effect must not send it twice: the second run of the
 * effect reuses the first run's promise (refs survive StrictMode's simulated remount).
 */
function useOnce<T>() {
  const ref = useRef<{ key: string; p: Promise<T> } | null>(null);
  return (key: string, run: () => Promise<T>): Promise<T> => {
    if (ref.current?.key !== key) ref.current = { key, p: run() };
    return ref.current.p;
  };
}

/* ------------------------------------------------------------------------------------ draft */

type DraftLoad = { state: 'loading' } | { state: 'ok'; d: ActDraft } | { state: 'fail'; kind: Fail };

function DraftView({ response }: { response: AskResponse }) {
  const t = useMessages(actMessages);
  const [load, setLoad] = useState<DraftLoad>({ state: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState('');
  const [copied, setCopied] = useState(false);
  const editId = useId();
  const once = useOnce<ActDraft>();

  useEffect(() => {
    let live = true;
    setLoad({ state: 'loading' });
    once(`${response.request_id}:${attempt}`, () => actApi.draft(response.request_id, 'reply'))
      .then((d) => {
        if (!live) return;
        setLoad({ state: 'ok', d });
        setText(d.text);
      })
      .catch((e) => live && setLoad({ state: 'fail', kind: failOf(e) }));
    return () => {
      live = false;
    };
  }, [response.request_id, attempt]);

  if (load.state === 'loading') return <SkeletonText lines={7} label={t('act.draft.loading')} />;
  if (load.state === 'fail') return <Failure kind={load.kind} onRetry={() => setAttempt((a) => a + 1)} t={t} />;
  const d = load.d;
  const byCite = new Map(d.citations.map((c) => [c.tag, c]));
  const how =
    d.model.model === 'approved-script-v1'
      ? t('act.draft.by.script')
      : d.model.provider === 'deterministic'
        ? d.model.fallback_used
          ? t('act.draft.by.fallback')
          : t('act.draft.by.template')
        : t('act.draft.by.model', { model: d.model.model });

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(editing ? text : d.text);
      setCopied(true);
      toast(t('act.draft.copied'));
      setTimeout(() => setCopied(false), 1600);
    } catch {
      toast(t('act.draft.copyFailed'));
    }
  };

  return (
    <div data-testid="act-draft-view" data-model={d.model.model}>
      <div className="flex flex-wrap items-center gap-2">
        {/* The server's fixed label is translated; any other label is shown as returned (English). */}
        {d.label === SERVER_DRAFT_LABEL ? (
          <Badge tone="accent" data-testid="act-draft-label">
            {t('act.draft.title')}
          </Badge>
        ) : (
          <Badge tone="accent" data-testid="act-draft-label" lang="en">
            {d.label}
          </Badge>
        )}
        <span className="inline-flex items-center gap-1 text-caption text-muted" data-testid="act-draft-how">
          <ShieldCheck aria-hidden className="h-3.5 w-3.5 text-ok" />
          {how}
        </span>
      </div>
      <ServerLangNote />

      {editing ? (
        <div className="mt-4 flex flex-col gap-1">
          <label htmlFor={editId} className="text-xs font-medium text-fg">
            {t('act.draft.editLabel')}
          </label>
          <textarea
            id={editId}
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={12}
            className={cn(inputClass, 'h-auto min-h-[14rem] resize-y py-2 leading-6')}
            lang="en"
            data-testid="act-draft-editor"
            aria-describedby={`${editId}-hint`}
          />
          <p id={`${editId}-hint`} className="text-2xs text-muted">
            {t('act.draft.editing')}
          </p>
        </div>
      ) : (
        <div lang="en" className="mt-4 rounded-lg border border-line bg-surface px-4 py-3.5 text-body leading-7 text-fg" data-testid="act-draft-text">
          <p>{d.greeting}</p>
          <p className="mt-3">
            {d.opener}{' '}
            {d.body.map((b, i) => (
              <span key={i} data-testid="act-draft-sentence">
                <Spoken text={b.text} />
                {b.tags.map((tag) => (
                  <SourceBadge key={tag} n={numberOf(response, tag, d.citations)} c={byCite.get(tag)} />
                ))}{' '}
              </span>
            ))}
          </p>
          <p className="mt-3 whitespace-pre-wrap">{d.closing}</p>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="primary" icon={copied ? Check : Copy} onClick={() => void copy()} data-testid="act-draft-copy">
          {t('act.draft.copy')}
        </Button>
        {editing ? (
          <Button size="sm" icon={RotateCcw} onClick={() => setText(d.text)} disabled={text === d.text}>
            {t('act.draft.reset')}
          </Button>
        ) : (
          <Button size="sm" icon={FilePenLine} onClick={() => setEditing(true)} data-testid="act-draft-edit">
            {t('act.draft.edit')}
          </Button>
        )}
        <span className="text-caption text-muted">{t('act.draft.notSent')}</span>
      </div>

      <SourceList response={response} citations={d.citations} title={t('act.draft.sources')} />
      <p className="mt-4 text-caption text-subtle">
        {t('act.draft.recorded')} <span className="font-mono">{d.action_id.slice(0, 12)}</span>
      </p>
    </div>
  );
}

/* ----------------------------------------------------------------------------------- ticket */

type TicketLoad =
  | { state: 'loading' }
  | { state: 'ok'; p: TicketProposal }
  | { state: 'created'; r: TicketCreated }
  | { state: 'expired' }
  | { state: 'fail'; kind: Fail };

function TicketView({
  response,
  onDone,
  onBlocked,
  onDraft,
}: {
  response: AskResponse;
  onDone: () => void;
  onBlocked?: (blocked: boolean) => void;
  onDraft?: () => void;
}) {
  const t = useMessages(actMessages);
  const [load, setLoad] = useState<TicketLoad>({ state: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const [summary, setSummary] = useState('');
  const [project, setProject] = useState('');
  const [busy, setBusy] = useState<'confirm' | 'cancel' | null>(null);
  // After Confirm: 'unknown' = Jira's outcome is unknown (409 outcome_unknown; never "nothing was changed"),
  // 'pending' = the service failed after Confirm (503 or no answer): the outcome is not confirmed yet.
  const [confirmErr, setConfirmErr] = useState<Fail | 'pending' | null>(null);
  const blocked = load.state === 'fail' && load.kind === 'unavailable';
  useEffect(() => onBlocked?.(blocked), [blocked]);
  const ids = { project: useId(), summary: useId(), desc: useId() };
  const doneRef = useRef<HTMLHeadingElement | null>(null);
  const once = useOnce<TicketProposal>();

  useEffect(() => {
    let live = true;
    setLoad({ state: 'loading' });
    setConfirmErr(null);
    once(`${response.request_id}:${attempt}`, () => actApi.proposeTicket(response.request_id))
      .then((p) => {
        if (!live) return;
        setLoad({ state: 'ok', p });
        setSummary(p.summary);
        setProject(p.project.key);
      })
      .catch((e) => live && setLoad({ state: 'fail', kind: failOf(e) }));
    return () => {
      live = false;
    };
  }, [response.request_id, attempt]);

  useEffect(() => {
    if (load.state === 'created') doneRef.current?.focus();
  }, [load.state]);

  if (load.state === 'loading') return <SkeletonText lines={6} label={t('act.ticket.loading')} />;
  if (load.state === 'fail') return <Failure kind={load.kind} onRetry={() => setAttempt((a) => a + 1)} onDraft={onDraft} t={t} />;
  if (load.state === 'expired') {
    return (
      <div className="px-4 py-12 text-center" data-testid="act-expired">
        <p className="text-body-sm text-fg">{t('act.ticket.expired')}</p>
      </div>
    );
  }
  if (load.state === 'created') {
    const r = load.r;
    const href = safeHref(r.issue.url);
    return (
      <div className="flex flex-col items-center px-4 py-10 text-center" data-testid="act-created">
        <CircleCheck aria-hidden className="mb-3 h-8 w-8 text-ok" />
        <h3 ref={doneRef} tabIndex={-1} className="text-heading-sm text-fg" role="status">
          {t('act.ticket.created', { key: r.issue.key })}
        </h3>
        <p className="mt-1 max-w-sm text-body-sm text-muted">{t('act.ticket.createdBody', { key: r.issue.key, project: r.project.name })}</p>
        <p className="mt-2 max-w-sm text-body-sm text-fg">{r.summary}</p>
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noreferrer noopener"
            className="mt-4 inline-flex h-9 items-center gap-1.5 rounded-md border border-line-strong bg-surface px-3.5 text-sm font-medium text-fg hover:bg-raised"
            data-testid="act-created-link"
          >
            {t('act.ticket.open', { key: r.issue.key })}
            <ExternalLink aria-hidden className="h-3.5 w-3.5" />
          </a>
        ) : (
          <span className="mt-4 font-mono text-body-sm">{r.issue.key}</span>
        )}
        <p className="mt-4 text-caption text-subtle">
          {t('act.draft.recorded')} <span className="font-mono">{r.action_id.slice(0, 12)}</span>
        </p>
      </div>
    );
  }

  const p = load.p;
  const max = p.summary_max;
  const trimmed = summary.trim();
  const confirm = async () => {
    setBusy('confirm');
    setConfirmErr(null);
    try {
      const edits: { summary?: string; project?: string } = {};
      if (trimmed !== p.summary) edits.summary = trimmed;
      if (project !== p.project.key) edits.project = project;
      const r = await actApi.confirmTicket(p.proposal_id, edits);
      setLoad({ state: 'created', r });
    } catch (e) {
      const k = failOf(e);
      if (k === 'nothing') setLoad({ state: 'expired' });
      else if (k === 'error') setConfirmErr('pending');
      else setConfirmErr(k);
    } finally {
      setBusy(null);
    }
  };
  const cancel = async () => {
    // After a Confirm whose outcome is not known, closing must not claim "nothing was created".
    if (confirmErr === 'unknown' || confirmErr === 'pending') {
      onDone();
      return;
    }
    setBusy('cancel');
    try {
      await actApi.cancelTicket(p.proposal_id);
      toast(t('act.ticket.cancelled'));
    } catch {
      /* nothing was created either way; closing is safe */
    } finally {
      setBusy(null);
      onDone();
    }
  };

  return (
    <div data-testid="act-ticket-view">
      <div className="flex flex-col gap-4">
        <div className="flex min-w-0 flex-col gap-1">
          <label htmlFor={ids.project} className="text-xs font-medium text-fg">
            {t('act.ticket.project')}
          </label>
          {p.projects.length > 1 ? (
            <select
              id={ids.project}
              value={project}
              onChange={(e) => setProject(e.target.value)}
              className={inputClass}
              data-testid="act-ticket-project"
              aria-describedby={`${ids.project}-hint`}
            >
              {p.projects.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.name} ({o.key})
                </option>
              ))}
            </select>
          ) : (
            <p id={ids.project} className="flex h-9 items-center gap-2 text-body-sm text-fg" data-testid="act-ticket-project">
              <PlatformIcon platform="jira" tone="platform" size="sm" showLabel={false} />
              {p.project.name} <span className="font-mono text-caption text-muted">{p.project.key}</span>
            </p>
          )}
          <p id={`${ids.project}-hint`} className="text-2xs text-muted">
            {t('act.ticket.projectHint')}
          </p>
        </div>

        <div className="flex min-w-0 flex-col gap-1">
          <label htmlFor={ids.summary} className="text-xs font-medium text-fg">
            {t('act.ticket.summary')}
          </label>
          <input
            id={ids.summary}
            value={summary}
            maxLength={max}
            onChange={(e) => setSummary(e.target.value)}
            className={inputClass}
            data-testid="act-ticket-summary"
            aria-describedby={`${ids.summary}-hint`}
          />
          <p id={`${ids.summary}-hint`} className="text-2xs tabular-nums text-muted">
            {t('act.ticket.summaryHint', { n: summary.length, max })}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-body-sm">
          <span className="text-xs font-medium text-fg">{t('act.ticket.type')}</span>
          <Badge tone="neutral" lang="en" data-testid="act-ticket-type">
            {p.issue_type}
          </Badge>
        </div>

        <div className="flex min-w-0 flex-col gap-1">
          <span id={ids.desc} className="text-xs font-medium text-fg">
            {t('act.ticket.description')}
          </span>
          {/* Focusable so keyboard users can scroll it (axe scrollable-region-focusable); the text is
              the server's, in English, so it is marked lang="en" (WCAG 3.1.2). */}
          <pre
            tabIndex={0}
            role="region"
            aria-label={t('act.ticket.descriptionRegion')}
            lang="en"
            className="max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-md border border-line bg-bg/60 px-3 py-2 font-mono text-caption leading-5 text-muted"
            data-testid="act-ticket-description"
          >
            {p.description}
          </pre>
          {/* The link rule is stated once, in the description itself (round 4 Sonnet UX). */}
          <ServerLangNote />
        </div>
      </div>

      <SourceList response={response} citations={p.citations} title={t('act.ticket.sources')} />

      {confirmErr ? (
        <p role="alert" className="mt-4 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-body-sm text-fg" data-testid="act-confirm-error">
          {confirmErr === 'unavailable'
            ? t('act.ticket.unavailable')
            : confirmErr === 'unknown'
              ? t('act.ticket.outcomeUnknown')
              : confirmErr === 'pending'
                ? t('act.ticket.notConfirmed')
                : confirmErr === 'rate'
                  ? t('act.rateLimited')
                  : t('act.error')}
        </p>
      ) : null}

      {/* At phone width the confirm row stays in view at the bottom of the sheet (sticky). */}
      <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-line bg-overlay pt-4 max-sm:sticky max-sm:bottom-0 max-sm:-mb-4 max-sm:pb-4" data-testid="act-ticket-actions">
        <Button
          variant="primary"
          icon={Ticket}
          busy={busy === 'confirm'}
          disabled={!trimmed || busy !== null || confirmErr === 'unavailable' || confirmErr === 'unknown'}
          onClick={() => void confirm()}
          data-testid="act-ticket-confirm"
        >
          {busy === 'confirm' ? t('act.ticket.creating') : t('act.ticket.confirmIn', { key: project || p.project.key })}
        </Button>
        <Button busy={busy === 'cancel'} disabled={busy !== null} onClick={() => void cancel()} data-testid="act-ticket-cancel">
          {t('act.ticket.cancel')}
        </Button>
        <span className="text-caption text-muted">{t('act.ticket.confirmNote')}</span>
      </div>
    </div>
  );
}
