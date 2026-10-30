'use client';
import { ArrowUpRight, Check, Copy, CornerDownRight, Cpu, RefreshCw, ShieldCheck, ThumbsUp, TriangleAlert, Unlink2, History, type LucideIcon } from 'lucide-react';
import Link from 'next/link';
import { forwardRef, useEffect, useState } from 'react';
import { BrandMark } from '@/components/Brand';
import { PlatformIcon } from '@/components/PlatformIcon';
import { Tooltip } from '@/components/ui';
import { cn } from '@/lib/cn';
import { placeLabel } from '@/lib/format';
import { useLang, useMessages } from '@/lib/i18n';
import { userMessages } from '@/lib/i18n/user';
import type { AskResponse, Citation } from '@/lib/types';
import { MODEL_KEY, TAG_RE, checkedAgoKey, displayAnswer, followUpQuestion, followUps, isRefusal, modelKind, plainAnswer, secondsSince, speakerParts, versionText } from './answerText';
import { ActRow } from './ActSheet';
import { DeliverRow } from './DeliverSheet';
import { CitationChip } from './CitationChip';
import { getFeedback, setFeedback, type FeedbackKind } from './local';
import { RefusalCard } from './RefusalCard';
import { toast } from './Toast';
import { useNow } from './useNow';

export interface AnswerCardProps {
  response: AskResponse;
  /** Open the source at `index` (0-based into response.citations); `opener` gets focus back. */
  onCite: (index: number, opener?: HTMLElement) => void;
  /** Tighter layout for side-by-side use (Compare): no follow-ups, no feedback. */
  compact?: boolean;
  /** The question asked (used by copy and the follow-up templates). */
  question?: string;
  /** Ask a follow-up (the chips are hidden without it). */
  onFollowUp?: (question: string) => void;
  /** The newest answer on the page carries the stable test ids and `#answer-heading`. */
  latest?: boolean;
  /** Prefix for element ids when several cards share a page. */
  idPrefix?: string;
  /** Show the "How this was answered" link (default true). */
  showJourney?: boolean;
}

/** Plain text with inline [n] chips. Never HTML: every part is a React text node or a chip. */
export function AnswerText({ response, onCite, className }: { response: AskResponse; onCite: AnswerCardProps['onCite']; className?: string }) {
  const t = useMessages(userMessages);
  const { citations } = response;
  // Display chrome only (see displayAnswer): the cited passages and the copied text are unchanged.
  const answer = displayAnswer(response.answer);
  const byTag = new Map(citations.map((c, i) => [c.tag, { c, i }]));
  const parts: React.ReactNode[] = [];
  let last = 0;
  let key = 0;
  // Plain text, with a Slack "handle: " prefix shown as a subtle "<person's name> said" label.
  const pushText = (s: string) => {
    for (const p of speakerParts(s)) {
      if (typeof p === 'string') parts.push(p);
      else
        parts.push(
          <span key={`s${key++}`} className="mr-1 rounded bg-raised px-1 py-px text-caption font-medium text-muted" data-testid="answer-speaker" data-handle={p.speaker}>
            {t('answer.speaker', { handle: p.name })}
          </span>,
        );
    }
  };
  for (const m of answer.matchAll(TAG_RE)) {
    const idx = m.index ?? 0;
    if (idx > last) pushText(answer.slice(last, idx));
    const tags = m[1].split(',').map((t) => t.trim());
    if (tags.every((t) => byTag.has(t))) {
      parts.push(
        <span key={`g${key++}`} className="whitespace-nowrap">
          {tags.map((t) => {
            const { c, i } = byTag.get(t)!;
            return <CitationChip key={t} citation={c} n={i + 1} onOpen={(el) => onCite(i, el)} />;
          })}
        </span>,
      );
    } else {
      parts.push(m[0]); // an unknown tag is shown as written, never resolved
    }
    last = idx + m[0].length;
  }
  if (last < answer.length) pushText(answer.slice(last));
  return (
    <p className={cn('whitespace-pre-wrap text-fg', className)} data-testid="answer-text">
      {parts}
    </p>
  );
}

/** Model label from response.model; the AI note is added for model-written answers. */
export function ModelLabel({ model, className }: { model: AskResponse['model']; className?: string }) {
  const t = useMessages(userMessages);
  const kind = modelKind(model);
  const label = kind === 'other' ? `${model.provider} · ${model.model}` : t(MODEL_KEY[kind]);
  const isModel = kind !== 'extractive';
  return (
    <span className={cn('inline-flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-caption text-muted', className)}>
      <span className="inline-flex items-center gap-1" title={model.fallback_used ? t('answer.model.fallbackTip') : `${model.provider} / ${model.model}`}>
        <Cpu aria-hidden className="h-3.5 w-3.5 text-subtle" />
        <span data-testid="model-badge">{label}</span>
      </span>
      {isModel ? (
        <>
          <span aria-hidden className="text-subtle">
            ·
          </span>
          <span data-testid="ai-note">{t('answer.aiNote')}</span>
        </>
      ) : null}
    </span>
  );
}

/** 1-based number of the first earlier card citing the same document, if any (two passages of one page). */
export function firstOfDoc(citations: Citation[], i: number): number | undefined {
  const j = citations.findIndex((c) => c.doc_id === citations[i].doc_id);
  return j >= 0 && j < i ? j + 1 : undefined;
}

/** One source card under the answer. The whole card opens the passage. */
export function SourceCard({ c, n, onOpen, compact, samePageAs }: { c: Citation; n: number; onOpen: (el: HTMLElement) => void; compact?: boolean; /** an earlier card cites the same document (another passage of it) */ samePageAs?: number }) {
  const t = useMessages(userMessages);
  const { lang } = useLang();
  const now = useNow(5000);
  const s = secondsSince(c.authz_checked_at, now);
  const ago = s === null ? null : checkedAgoKey(s);
  const version = versionText(c.version, lang, t, c.updated_at);
  return (
    <button
      type="button"
      onClick={(e) => onOpen(e.currentTarget)}
      aria-label={t('answer.cardLabel', { n, title: c.title })}
      data-testid="source-card"
      className={cn(
        'group flex h-full w-full min-w-0 flex-col gap-2 rounded-lg border border-line bg-surface text-left transition-colors duration-fast hover:border-line-strong hover:bg-raised/60',
        compact ? 'px-3 py-2.5' : 'px-3.5 py-3',
      )}
    >
      <span className="flex min-w-0 items-center gap-2 text-caption text-muted">
        <span className="inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded border border-line-strong px-1 font-semibold tabular-nums text-fg">{n}</span>
        <PlatformIcon platform={c.platform} tone="platform" size="sm" showLabel={false} />
        <span className="truncate">{placeLabel(c.platform, c.container_name)}</span>
      </span>
      <span className="line-clamp-2 text-body-sm font-medium text-fg group-hover:underline group-hover:decoration-line-strong group-hover:underline-offset-4">{c.title}</span>
      <span className="mt-auto flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-muted">
        {version !== '—' && version ? <span className="tabular-nums">{version}</span> : null}
        {samePageAs ? <span className="tabular-nums">{t('answer.samePage', { n: samePageAs })}</span> : null}
        {ago ? (
          <span className="inline-flex items-center gap-1 tabular-nums" title={c.authz_checked_at}>
            <ShieldCheck aria-hidden className="h-3.5 w-3.5 text-ok" />
            {t(ago.key, { n: ago.n })}
          </span>
        ) : null}
        {c.live_read ? (
          <span className="inline-flex items-center gap-1 text-accent">
            <RefreshCw aria-hidden className="h-3 w-3" />
            {t('answer.liveRead')}
          </span>
        ) : null}
        {c.stale ? (
          <span className="inline-flex items-center gap-1 text-warn">
            <TriangleAlert aria-hidden className="h-3 w-3" />
            {t('answer.stale')}
          </span>
        ) : null}
      </span>
    </button>
  );
}

const FEEDBACK: { kind: FeedbackKind; key: 'answer.fb.helpful' | 'answer.fb.wrong' | 'answer.fb.outdated'; icon: LucideIcon }[] = [
  { kind: 'helpful', key: 'answer.fb.helpful', icon: ThumbsUp },
  { kind: 'wrong', key: 'answer.fb.wrong', icon: Unlink2 },
  { kind: 'outdated', key: 'answer.fb.outdated', icon: History },
];

function ActionsRow({ response, compact, showJourney }: { response: AskResponse; compact?: boolean; showJourney: boolean }) {
  const t = useMessages(userMessages);
  const [fb, setFb] = useState<FeedbackKind | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => setFb(getFeedback(response.request_id)), [response.request_id]);
  const refused = isRefusal(response);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(plainAnswer(response));
      setCopied(true);
      toast(t('answer.copied'));
      setTimeout(() => setCopied(false), 1600);
    } catch {
      toast(t('answer.copyFailed'));
    }
  };
  const btn =
    'inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-caption font-medium text-muted transition-colors duration-fast hover:bg-raised hover:text-fg aria-pressed:bg-raised aria-pressed:text-fg';

  return (
    <div className="flex flex-wrap items-center gap-x-1 gap-y-1">
      {!refused ? (
        <button type="button" onClick={copy} className={btn} data-testid="copy-answer">
          {copied ? <Check aria-hidden className="h-3.5 w-3.5 text-ok" /> : <Copy aria-hidden className="h-3.5 w-3.5" />}
          {t('answer.copy')}
        </button>
      ) : null}
      {!compact && !refused ? (
        <div role="group" aria-label={t('answer.feedback')} className="flex flex-wrap items-center gap-x-1">
          <span aria-hidden className="mx-1 hidden h-4 w-px bg-line sm:block" />
          {FEEDBACK.map(({ kind, key, icon: Icon }) => (
            <button
              key={kind}
              type="button"
              aria-pressed={fb === kind}
              data-feedback={kind}
              className={btn}
              onClick={() => {
                const next = fb === kind ? null : kind;
                setFb(next);
                setFeedback(response.request_id, next);
                toast(t(next ? 'answer.fb.thanks' : 'answer.fb.removed'));
              }}
            >
              <Icon aria-hidden className={cn('h-3.5 w-3.5', fb === kind && 'text-accent')} />
              {t(key)}
            </button>
          ))}
        </div>
      ) : null}
      {showJourney ? (
        <Tooltip content={t('answer.howHint')}>
          <Link
            href={`/dev/journey/${encodeURIComponent(response.request_id)}`}
            className={cn(btn, 'sm:ml-auto')}
            data-testid="how-answered"
          >
            {t('answer.how')}
            <ArrowUpRight aria-hidden className="h-3.5 w-3.5" />
          </Link>
        </Tooltip>
      ) : null}
    </div>
  );
}

/**
 * A verified answer: model label, plain text with inline chips, the source row, actions,
 * follow-ups and the constant scope note. A response with no citations renders the RefusalCard.
 */
export const AnswerCard = forwardRef<HTMLHeadingElement, AnswerCardProps>(function AnswerCard(
  { response, onCite, compact = false, question, onFollowUp, latest = true, idPrefix = '', showJourney = true },
  ref,
) {
  const t = useMessages(userMessages);
  const refused = isRefusal(response);
  const headingId = latest ? `${idPrefix}answer-heading` : `${idPrefix}answer-heading-${response.request_id}`;
  const sourcesId = `${idPrefix}sources-${response.request_id}`;
  const fus = !compact && onFollowUp && question && !refused ? followUps(question, response.citations) : [];

  return (
    <article
      aria-labelledby={headingId}
      data-testid={latest ? 'answer' : 'answer-previous'}
      data-request={response.request_id}
      data-tour={latest && !compact ? 'answer' : undefined}
      className="min-w-0 motion-safe:animate-[ds-in_220ms_cubic-bezier(0.2,0,0,1)]"
    >
      <header className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
        <h2 ref={ref} id={headingId} tabIndex={-1} className="inline-flex items-center gap-2 rounded text-caption font-semibold text-fg">
          <BrandMark className="!h-6 !w-6" />
          {t('answer.heading')}
        </h2>
        {!refused ? <ModelLabel model={response.model} /> : null}
      </header>

      {refused ? (
        <RefusalCard text={response.answer} compact={compact} className="mt-3" />
      ) : (
        <>
          <AnswerText
            response={response}
            onCite={onCite}
            className={cn('mt-3', compact ? 'text-body-sm leading-6' : 'text-body leading-[1.75] sm:text-[1.0625rem]')}
          />
          <section aria-labelledby={sourcesId} className={compact ? 'mt-4' : 'mt-5'} data-tour={latest && !compact ? 'citations' : undefined}>
            <h3 id={sourcesId} className="mb-2 text-micro font-semibold uppercase tracking-wider text-subtle">
              {t('answer.sources')}
            </h3>
            <ol data-testid="citations" className={cn('grid gap-2', compact ? 'grid-cols-1' : 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3')}>
              {response.citations.map((c, i) => (
                <li key={c.tag} id={`${idPrefix}cite-${latest ? '' : `${response.request_id}-`}${c.tag}`} className="min-w-0">
                  <SourceCard c={c} n={i + 1} onOpen={(el) => onCite(i, el)} compact={compact} samePageAs={firstOfDoc(response.citations, i)} />
                </li>
              ))}
            </ol>
          </section>
        </>
      )}

      <div className={cn('flex flex-col gap-3', compact ? 'mt-3' : 'mt-4')}>
        <ActionsRow response={response} compact={compact} showJourney={showJourney} />
        {!compact && !refused ? <ActRow response={response} /> : null}
        {!compact && !refused ? <DeliverRow response={response} onCite={onCite} /> : null}
        {fus.length ? (
          <div>
            <h3 className="sr-only">{t('answer.followups')}</h3>
            <ul className="flex flex-wrap gap-2" data-testid="followups">
              {fus.map((k) => (
                <li key={k} className="min-w-0 max-w-full">
                  <button
                    type="button"
                    onClick={() => onFollowUp!(followUpQuestion(t(k), question!))}
                    className="inline-flex min-h-[2rem] max-w-full items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1 text-left text-body-sm text-fg transition-colors duration-fast hover:border-line-strong hover:bg-raised"
                  >
                    <CornerDownRight aria-hidden className="h-3.5 w-3.5 shrink-0 text-subtle" />
                    <span className="truncate">{t(k)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <p className="flex flex-col gap-0.5 border-t border-line pt-3 text-caption text-subtle">
          <span data-testid={latest ? 'scope-note' : undefined}>{response.scope_note}</span>
          <span>
            {t('answer.request')}{' '}
            <span className="font-mono" data-testid={latest ? 'request-id' : undefined}>
              {response.request_id}
            </span>
          </span>
        </p>
      </div>
    </article>
  );
});

