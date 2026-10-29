'use client';
import { ArrowUp, Info, Lock, RefreshCw, TriangleAlert } from 'lucide-react';
import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import { PlatformIcon } from '@/components/PlatformIcon';
import { Button, Popover, PopoverContent, PopoverTrigger } from '@/components/ui';
import { cn } from '@/lib/cn';
import { useMessages } from '@/lib/i18n';
import { userMessages } from '@/lib/i18n/user';
import { inferPlatforms } from './answerText';

export const MAX_QUESTION = 2000;
const SHOW_COUNT_FROM = 1600;

export interface ComposerHandle {
  focus: () => void;
}

/**
 * The one large composer. Enter sends, Shift+Enter adds a line (and IME composition is never cut
 * short). The character count appears near the 2,000 limit.
 */
export const Composer = forwardRef<
  ComposerHandle,
  { value: string; onChange: (v: string) => void; onSubmit: () => void; busy: boolean; placeholder: string; size?: 'lg' | 'md'; autoFocus?: boolean }
>(function Composer({ value, onChange, onSubmit, busy, placeholder, size = 'lg', autoFocus }, ref) {
  const t = useMessages(userMessages);
  const box = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(ref, () => ({ focus: () => box.current?.focus() }), []);

  // Grow with the text, up to a limit; then scroll.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [value]);

  const n = value.length;
  const canSend = !!value.trim() && !busy;
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (canSend) onSubmit();
      }}
      aria-label={t('ask.formLabel')}
      data-tour="ask-form"
      className={cn(
        'rounded-2xl border border-line-strong bg-surface transition-colors duration-fast has-[textarea:focus]:border-focus has-[textarea:focus]:ring-1 has-[textarea:focus]:ring-focus',
        size === 'lg' ? 'p-3 sm:p-4' : 'p-2.5 sm:p-3',
      )}
    >
      <label htmlFor="question" className="sr-only">
        {t('ask.label')}
      </label>
      <textarea
        id="question"
        ref={box}
        value={value}
        autoFocus={autoFocus}
        onChange={(e) => onChange(e.target.value.slice(0, MAX_QUESTION))}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (canSend) onSubmit();
          }
        }}
        rows={size === 'lg' ? 2 : 1}
        maxLength={MAX_QUESTION}
        aria-describedby="question-keys question-count"
        placeholder={placeholder}
        className={cn(
          'block w-full resize-none bg-transparent px-1.5 text-fg placeholder:text-subtle focus:outline-none focus-visible:outline-none',
          size === 'lg' ? 'min-h-[3.5rem] text-body leading-7 sm:text-[1.0625rem]' : 'min-h-[2rem] text-body leading-7',
        )}
      />
      <div className="mt-2 flex items-center justify-between gap-3 pl-1.5">
        <p id="question-keys" className="hidden text-caption text-subtle sm:block">
          {t('ask.keys')}
        </p>
        <div className="ml-auto flex items-center gap-3">
          <p
            id="question-count"
            aria-live="polite"
            className={cn('text-caption tabular-nums', n >= SHOW_COUNT_FROM ? (n >= MAX_QUESTION ? 'text-warn' : 'text-muted') : 'sr-only')}
          >
            {n >= SHOW_COUNT_FROM ? (
              <>
                <span aria-hidden>
                  {n.toLocaleString('en-SG')} / {MAX_QUESTION.toLocaleString('en-SG')}
                </span>
                <span className="sr-only">{t('ask.countSr', { n: n.toLocaleString('en-SG'), max: MAX_QUESTION.toLocaleString('en-SG') })}</span>
              </>
            ) : null}
          </p>
          <button
            type="submit"
            aria-label={t('ask.send')}
            title={t('ask.send')}
            disabled={!canSend}
            aria-busy={busy || undefined}
            className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-accent-solid text-accent-fg transition-colors duration-fast hover:bg-accent-solid/90 disabled:cursor-not-allowed disabled:bg-raised disabled:text-subtle"
          >
            <ArrowUp aria-hidden className="h-4 w-4" />
          </button>
        </div>
      </div>
    </form>
  );
});

/** "Answers only from what you can open" with a two-line explanation. */
export function ScopePill() {
  const t = useMessages(userMessages);
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          data-testid="scope-pill"
          className="inline-flex h-7 items-center gap-1.5 rounded-full border border-line bg-surface px-3 text-caption font-medium text-muted transition-colors duration-fast hover:border-line-strong hover:text-fg data-[state=open]:border-line-strong data-[state=open]:text-fg"
        >
          <Lock aria-hidden className="h-3 w-3" />
          {t('ask.scope')}
          <Info aria-hidden className="h-3 w-3 text-subtle" />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-80" aria-label={t('ask.scopeTitle')}>
        <p className="text-heading-sm text-fg">{t('ask.scopeTitle')}</p>
        <p className="mt-2 text-body-sm text-muted">{t('ask.scopeLine1')}</p>
        <p className="mt-2 text-body-sm text-muted">{t('ask.scopeLine2')}</p>
      </PopoverContent>
    </Popover>
  );
}

/** Up to four starter questions (from personas.ts), each with decorative platform hints. */
export function StarterCards({ questions, onPick, disabled }: { questions: string[]; onPick: (q: string) => void; disabled?: boolean }) {
  const t = useMessages(userMessages);
  if (!questions.length) return null;
  return (
    <section aria-labelledby="starters-heading">
      <h2 id="starters-heading" className="mb-3 text-micro font-semibold uppercase tracking-wider text-subtle">
        {t('ask.starters')}
      </h2>
      <ul className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        {questions.slice(0, 4).map((q) => (
          <li key={q} className="min-w-0">
            <button
              type="button"
              onClick={() => onPick(q)}
              disabled={disabled}
              data-testid="starter"
              className="group flex h-full w-full min-w-0 flex-col gap-3 rounded-xl border border-line bg-surface p-4 text-left transition-colors duration-fast hover:border-line-strong hover:bg-raised/60 disabled:opacity-60"
            >
              <span aria-hidden className="flex items-center gap-1.5">
                {inferPlatforms(q).map((p) => (
                  <PlatformIcon key={p} platform={p} tone="platform" size="sm" showLabel={false} />
                ))}
              </span>
              <span className="line-clamp-3 text-body-sm text-fg">{q}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** 503 and other failures: calm, with a retry. Nothing was answered. */
export function AskErrorCard({ status, onRetry }: { status: number; onRetry: () => void }) {
  const t = useMessages(userMessages);
  const body = status === 403 ? t('error.forbidden') : status === 422 ? t('error.invalid') : status === 0 || status >= 500 ? t('error.body') : t('error.generic');
  return (
    <div role="alert" data-testid="answer-error" className="flex flex-wrap items-start gap-3 rounded-xl border border-line bg-surface p-4">
      <span aria-hidden className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-line bg-raised text-warn">
        <TriangleAlert className="h-4 w-4" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-heading-sm text-fg">{t('error.title')}</p>
        <p className="mt-0.5 text-body-sm text-muted">{body}</p>
      </div>
      <Button size="sm" icon={RefreshCw} onClick={onRetry}>
        {t('error.retry')}
      </Button>
    </div>
  );
}
