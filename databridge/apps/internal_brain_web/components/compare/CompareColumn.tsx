'use client';
import { AlertCircle } from 'lucide-react';
import { Avatar, Skeleton } from '@/components/ui';
import { cn } from '@/lib/cn';
import { useMessages } from '@/lib/i18n';
import { presenterMessages, type PresenterKey } from '@/lib/i18n/presenter';
import { personaById } from '@/lib/personas';
import { AnswerCard, AskProgressLine } from './answerAdapter';
import type { DoneSide } from './cache';
import type { CompareMode } from './params';

export type SlotId = 'b' | 'c';

export type Side =
  | { kind: 'idle'; persona: string; question: string }
  | { kind: 'loading'; persona: string; question: string }
  | DoneSide
  | { kind: 'error'; persona: string; question: string; status: number };

export function useRole() {
  const t = useMessages(presenterMessages);
  return (persona: string) => {
    const key = `role.${persona}` as PresenterKey;
    const v = t(key);
    return v === key ? personaById(persona)?.role ?? '' : v;
  };
}

function ErrorBody({ status }: { status: number }) {
  const t = useMessages(presenterMessages);
  const [what, todo] =
    status === 404
      ? (['cmp.err.demo', 'cmp.err.demoTodo'] as const)
      : status === 401 || status === 403
        ? (['cmp.err.signin', 'cmp.err.signinTodo'] as const)
        : status === 503
          ? (['cmp.err.unavailable', 'cmp.err.genericTodo'] as const)
          : (['cmp.err.generic', 'cmp.err.genericTodo'] as const);
  return (
    <div role="alert" className="flex items-start gap-3 rounded-xl border border-line bg-surface p-5" data-testid="compare-error">
      <AlertCircle aria-hidden className="mt-0.5 h-5 w-5 shrink-0 text-bad" />
      <div>
        <p className="text-body font-medium text-fg">{t(what)}</p>
        <p className="mt-1 text-body-sm text-muted">{t(todo)}</p>
      </div>
    </div>
  );
}

/**
 * One side of the comparison: a header strip (who, their role, which session) and their answer.
 * In "same person" mode each column also shows its own question.
 */
export function CompareColumn({
  slot,
  side,
  mode,
  progress = 0,
  onCite,
}: {
  slot: SlotId;
  side: Side;
  mode: CompareMode;
  /** The staged progress step for this column's ask (components/user/useAskProgress). */
  progress?: number;
  onCite: (index: number, opener?: HTMLElement) => void;
}) {
  const t = useMessages(presenterMessages);
  const role = useRole();
  const p = personaById(side.persona);
  const name = side.kind === 'done' ? side.name : p?.name ?? side.persona;
  const headingId = `compare-${slot}-heading`;

  return (
    <section aria-labelledby={headingId} className="flex min-w-0 flex-col gap-4" data-testid={`compare-${slot}`} data-compare-col={slot} aria-busy={side.kind === 'loading'}>
      <header className="flex min-w-0 items-center gap-3 border-b border-line pb-3">
        <Avatar name={name} size="md" />
        <div className="min-w-0 flex-1">
          <h2 id={headingId} className="truncate text-heading text-fg" data-testid="compare-name">
            {name}
          </h2>
          <p className="text-body-sm text-muted">
            <span data-testid="compare-role">{role(side.persona)}</span>
            {side.kind === 'done' ? (
              <span className="text-caption" data-stage-hide>
                <span aria-hidden className="mx-2 text-subtle">
                  ·
                </span>
                {t('cmp.signedInAs', { sub: '' })}
                <span className="font-mono text-fg">{side.sub}</span>
              </span>
            ) : null}
          </p>
        </div>
      </header>

      {mode === 'person' ? (
        <p className="text-body text-fg" data-testid="compare-question">
          <span className="mr-2 text-micro font-semibold uppercase tracking-wider text-subtle">{t('cmp.asks')}</span>
          {side.question}
        </p>
      ) : null}

      {side.kind === 'loading' ? (
        <div className="space-y-3 rounded-xl border border-line bg-surface p-5" data-testid="compare-loading">
          <p className="text-body-sm text-muted">{t('cmp.loading', { name: p?.name ?? side.persona })}</p>
          <AskProgressLine step={progress} compact />
          <div aria-hidden className="space-y-2.5">
            <Skeleton className="h-3.5 w-full" />
            <Skeleton className="h-3.5 w-11/12" />
            <Skeleton className="h-3.5 w-2/3" />
          </div>
        </div>
      ) : side.kind === 'error' ? (
        <ErrorBody status={side.status} />
      ) : side.kind === 'done' ? (
        <AnswerCard response={side.res} onCite={onCite} compact question={side.question} idPrefix={`cmp-${slot}-`} showJourney={false} />
      ) : (
        <div aria-hidden className={cn('min-h-[8rem] rounded-xl border border-dashed border-line')} />
      )}
    </section>
  );
}
