'use client';
import { AlertCircle, Check, Copy, Cpu, ExternalLink, Info, Lock, RefreshCw, ScrollText, ShieldCheck, TriangleAlert, UserCheck } from 'lucide-react';
import { forwardRef, useState } from 'react';
import { fmtDate, fmtDateTime, fmtTime, modelLabel } from '@/lib/format';
import type { AskResponse, Citation } from '@/lib/types';
import { InfoTip } from './InfoTip';
import { PlatformIcon } from './PlatformIcon';
import { Badge, Button } from './ui';

const TAG_RE = /\[(C\d+(?:\s*,\s*C\d+)*)\]/g;

function jumpTo(tag: string, prefix = '') {
  const el = document.getElementById(`${prefix}cite-${tag}`);
  if (!el) return;
  document.querySelectorAll('[data-active="true"]').forEach((n) => n.removeAttribute('data-active'));
  el.setAttribute('data-active', 'true');
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'nearest' });
  el.focus({ preventScroll: true });
}

/** Renders answer text verbatim; each [C<n>] tag becomes an accessible chip linked to its citation. */
export function AnswerText({ text, citations, idPrefix = '' }: { text: string; citations: Citation[]; idPrefix?: string }) {
  const byTag = new Map(citations.map((c) => [c.tag, c]));
  const parts: React.ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const m of text.matchAll(TAG_RE)) {
    const idx = m.index ?? 0;
    if (idx > last) parts.push(text.slice(last, idx));
    const tags = m[1].split(',').map((t) => t.trim());
    if (tags.every((t) => byTag.has(t))) {
      parts.push(
        <span key={`g${key++}`} className="whitespace-nowrap">
          {tags.map((t) => {
            const c = byTag.get(t)!;
            return (
              <a
                key={t}
                href={`#${idPrefix}cite-${t}`}
                onClick={(e) => {
                  e.preventDefault();
                  jumpTo(t, idPrefix);
                }}
                aria-label={`${t}: ${c.title}, ${c.container_name}`}
                className="mx-0.5 inline-flex h-5 items-center rounded border border-accent/50 bg-accent/10 px-1 align-[1px] font-mono text-2xs font-semibold text-accent hover:bg-accent/20"
                data-testid="citation-chip"
              >
                {t}
              </a>
            );
          })}
        </span>,
      );
    } else {
      parts.push(m[0]); // unknown tag: shown as written, never resolved
    }
    last = idx + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <p className="whitespace-pre-wrap text-[15px] leading-7 text-fg">{parts}</p>;
}

function versionLabel(v: string): string {
  if (/^\d+$/.test(v)) return `Version ${v}`;
  if (/^\d{4}-\d{2}-\d{2}T/.test(v)) return `Revision of ${fmtDate(v)}`;
  return `Revision ${v.length > 18 ? `${v.slice(0, 18)}…` : v}`;
}

export function CitationList({ citations, idPrefix = '', tour = false }: { citations: Citation[]; idPrefix?: string; tour?: boolean }) {
  if (citations.length === 0) {
    return <p className="text-sm text-muted">No sources were cited for this answer.</p>;
  }
  return (
    <ol className="divide-y divide-line rounded-md border border-line" data-testid="citations" data-tour={tour ? 'citations' : undefined}>
      {citations.map((c) => (
        <li key={c.tag} id={`${idPrefix}cite-${c.tag}`} tabIndex={-1} className="cite-target px-3 py-3 focus:outline-none focus-visible:outline-2">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
            <span className="font-mono font-semibold text-accent">{c.tag}</span>
            <PlatformIcon platform={c.platform} />
            <span aria-hidden>·</span>
            <span>{c.container_name}</span>
          </div>
          <a
            href={c.deep_link}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-1 inline-flex items-start gap-1 text-sm font-medium text-fg underline decoration-line-strong underline-offset-4 hover:decoration-accent"
          >
            <span>{c.title}</span>
            <ExternalLink aria-hidden className="mt-1 h-3 w-3 shrink-0 text-muted" />
            <span className="sr-only">(opens {c.platform} in a new tab)</span>
          </a>
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-2xs text-muted">
            <span className="tabular">{versionLabel(c.version)}</span>
            <span aria-hidden>·</span>
            <span className="tabular">Updated {fmtDate(c.updated_at)}</span>
            <Badge tone="ok" icon={ShieldCheck} title={`Access re-checked against ${c.platform} at ${c.authz_checked_at}`}>
              Access re-checked {fmtTime(c.authz_checked_at)}
            </Badge>
            {c.live_read ? (
              <Badge tone="accent" icon={RefreshCw} title={`Content re-read from ${c.platform} at ${c.content_checked_at}`}>
                Live re-read
              </Badge>
            ) : null}
            {c.stale ? (
              <Badge tone="warn" icon={TriangleAlert} title="This source is syncing behind its freshness target; newer edits may not be reflected">
                Stale: source sync behind target
              </Badge>
            ) : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function ModelBadge({ model }: { model: AskResponse['model'] }) {
  return (
    <Badge tone="neutral" icon={Cpu} title={`${model.provider} / ${model.model}${model.fallback_used ? ' (fallback used)' : ''}`}>
      <span data-testid="model-badge">{modelLabel(model)}</span>
    </Badge>
  );
}

function CopyId({ id }: { id: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="inline-flex h-6 w-6 items-center justify-center rounded text-muted hover:bg-raised hover:text-fg"
      aria-label={done ? 'Request ID copied' : 'Copy request ID'}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(id);
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        } catch {
          /* clipboard unavailable */
        }
      }}
    >
      {done ? <Check aria-hidden className="h-3.5 w-3.5" /> : <Copy aria-hidden className="h-3.5 w-3.5" />}
    </button>
  );
}

interface AnswerCardProps {
  res: AskResponse;
  question: string;
  /** Prefix for element ids, so two answers can sit on one page (compare). */
  idPrefix?: string;
  /** The signed-in identity this answer was produced for (from whoami or the slot's login). */
  signedInAs?: string;
  /** Mark this card's parts as guided-tour anchors (the Ask page only). */
  tour?: boolean;
  headingLevel?: 'h2' | 'h3';
}

export const AnswerCard = forwardRef<HTMLHeadingElement, AnswerCardProps>(function AnswerCard(
  { res, question, idPrefix = '', signedInAs, tour = false, headingLevel = 'h2' },
  ref,
) {
  const H = headingLevel;
  const Sub = headingLevel === 'h2' ? 'h3' : 'h4';
  return (
    <article
      className="rounded-lg border border-line bg-surface"
      aria-labelledby={`${idPrefix}answer-heading`}
      data-testid="answer"
      data-tour={tour ? 'answer' : undefined}
    >
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
        <div className="flex items-center gap-1">
          <H id={`${idPrefix}answer-heading`} ref={ref} tabIndex={-1} className="text-sm font-semibold focus:outline-none focus-visible:outline-2">
            Answer
          </H>
          <InfoTip title="The answer" testId="infotip-answer">
            Written only from sources this person can open right now. Each tag like C1 jumps to the page, ticket, thread or file the sentence came from.
          </InfoTip>
        </div>
        <ModelBadge model={res.model} />
      </header>
      <div className="space-y-5 px-4 py-4">
        <p className="text-xs text-muted">
          <span className="sr-only">Question: </span>
          <span aria-hidden>Q · </span>
          {question}
        </p>
        <AnswerText text={res.answer} citations={res.citations} idPrefix={idPrefix} />
        <section aria-labelledby={`${idPrefix}sources-cited-heading`}>
          <div className="mb-2 flex items-center gap-1">
            <Sub id={`${idPrefix}sources-cited-heading`} className="text-xs font-semibold uppercase tracking-wide text-muted">
              Sources cited
            </Sub>
            <InfoTip title="Sources cited">
              Every source behind this answer, with its version and a link to the original. &ldquo;Access re-checked&rdquo; is when we asked the platform itself, just before use, whether this person can still open it.
            </InfoTip>
          </div>
          <CitationList citations={res.citations} idPrefix={idPrefix} tour={tour} />
        </section>
      </div>
      <footer className="space-y-2 border-t border-line px-4 py-3">
        <p className="flex items-start gap-2 text-xs text-fg" data-testid="scope-note">
          <Info aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" />
          <span>{res.scope_note}</span>
        </p>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-muted">
          <span className="inline-flex items-center gap-1">
            Request ID <span className="font-mono text-fg" data-testid="request-id">{res.request_id}</span>
            <CopyId id={res.request_id} />
          </span>
          <span className="tabular">As of {fmtDateTime(res.as_of)}</span>
        </div>
      </footer>
      <ChecksStrip res={res} signedInAs={signedInAs} tour={tour} />
    </article>
  );
});

/**
 * "Checks on this answer": the core guarantees, stated with the data that backs them for THIS
 * answer: who it was for, that every cited source was re-checked live (count and latest check time
 * come from the citations), and the request id under which it sits in the audit ledger.
 */
export function ChecksStrip({ res, signedInAs, tour = false }: { res: AskResponse; signedInAs?: string; tour?: boolean }) {
  const cited = res.citations.length;
  const checked = res.citations.filter((c) => !!c.authz_checked_at);
  const latest = checked.map((c) => c.authz_checked_at).sort().at(-1);
  return (
    <section
      aria-label="Checks on this answer"
      className="rounded-b-lg border-t border-line bg-raised/40 px-4 py-2.5"
      data-testid="checks-strip"
      data-tour={tour ? 'checks' : undefined}
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-2xs text-fg">
        <span className="inline-flex items-center gap-0.5 font-semibold uppercase tracking-wide text-muted">
          Checks on this answer
          <InfoTip title="Checks on this answer">
            The safeguards that ran for this answer. Access was checked before anything reached the model, and the question was recorded in the audit log before the answer was shown.
          </InfoTip>
        </span>
        <ul className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1.5">
          {signedInAs ? (
            <li className="inline-flex items-center gap-1.5">
              <UserCheck aria-hidden className="h-3.5 w-3.5 text-muted" />
              <span>
                Signed in as <span className="font-mono">{signedInAs}</span>
              </span>
            </li>
          ) : null}
          <li className="inline-flex items-center gap-1.5">
            <Lock aria-hidden className="h-3.5 w-3.5 text-muted" />
            Only sources you can open
          </li>
          <li className="inline-flex items-center gap-1.5">
            <ShieldCheck aria-hidden className="h-3.5 w-3.5 text-ok" />
            {cited === 0 ? (
              'No sources were used'
            ) : (
              <span>
                Every source checked live just before use{' '}
                <span className="tabular text-muted">
                  ({checked.length} of {cited}
                  {latest ? `, latest ${fmtTime(latest)}` : ''})
                </span>
              </span>
            )}
          </li>
          <li className="inline-flex min-w-0 items-center gap-1.5">
            <ScrollText aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted" />
            <span className="min-w-0 break-all">
              Recorded in the audit log <span className="text-muted">(request <span className="font-mono">{res.request_id}</span>)</span>
            </span>
          </li>
        </ul>
      </div>
    </section>
  );
}

export function AnswerError({ status, onRetry }: { status: number; onRetry: () => void }) {
  const msg =
    status === 503 || status === 0 || status >= 500
      ? 'The Internal Brain is temporarily unavailable — nothing was answered.'
      : status === 403
        ? 'Your account is not permitted to ask questions — nothing was answered.'
        : status === 422
          ? 'Questions must be between 1 and 2,000 characters — nothing was answered.'
          : 'Something went wrong — nothing was answered.';
  return (
    <div role="alert" className="flex flex-wrap items-start gap-3 rounded-lg border border-line bg-surface p-4" data-testid="answer-error">
      <AlertCircle aria-hidden className="mt-0.5 h-5 w-5 shrink-0 text-bad" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{msg}</p>
        <p className="mt-1 text-xs text-muted">No answer text was released. You can try again in a moment.</p>
      </div>
      <Button size="sm" onClick={onRetry} icon={RefreshCw}>
        Try again
      </Button>
    </div>
  );
}

export function AnswerLoading() {
  return (
    <div className="rounded-lg border border-line bg-surface p-4" aria-hidden data-testid="answer-loading">
      <div className="space-y-2.5">
        <div className="h-3 w-1/3 rounded bg-raised motion-safe:animate-pulse" />
        <div className="h-3 w-full rounded bg-raised motion-safe:animate-pulse" />
        <div className="h-3 w-5/6 rounded bg-raised motion-safe:animate-pulse" />
        <div className="h-3 w-2/3 rounded bg-raised motion-safe:animate-pulse" />
      </div>
    </div>
  );
}
