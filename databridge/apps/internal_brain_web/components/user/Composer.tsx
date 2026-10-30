'use client';
import { ArrowUp, Info, Lock, Mic, RefreshCw, Square, TriangleAlert } from 'lucide-react';
import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import { PlatformIcon } from '@/components/PlatformIcon';
import { Button, Popover, PopoverContent, PopoverTrigger } from '@/components/ui';
import { cn } from '@/lib/cn';
import { useLang, useMessages } from '@/lib/i18n';
import { userMessages } from '@/lib/i18n/user';
import { DICTATION_LANG, voiceMessages } from '@/lib/i18n/voice';
import { inferPlatforms } from './answerText';
import { useDictation } from './useDictation';

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
  const tv = useMessages(voiceMessages);
  const { lang } = useLang();
  const box = useRef<HTMLTextAreaElement>(null);
  const valueRef = useRef(value);
  valueRef.current = value;
  const dict = useDictation({
    lang: DICTATION_LANG[lang] ?? DICTATION_LANG.en,
    getValue: () => valueRef.current,
    onText: (v) => onChange(v.slice(0, MAX_QUESTION)),
  });
  useImperativeHandle(ref, () => ({ focus: () => box.current?.focus() }), []);
  // Sending ends dictation (round 2b finding 4): the microphone goes off and a late recognition result
  // cannot rewrite the box after it is cleared. Busy covers a send that did not come from this form.
  const send = () => {
    dict.stop();
    onSubmit();
  };
  const stopDictation = dict.stop;
  useEffect(() => {
    if (busy) stopDictation();
  }, [busy, stopDictation]);

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
        if (canSend) send();
      }}
      aria-label={t('ask.formLabel')}
      data-tour="ask-form"
      className={cn(
        // one rounded field: the question on the left, the microphone and send on the right
        'grid grid-cols-[minmax(0,1fr)_auto] items-end gap-x-2 rounded-[1.75rem] border border-line bg-surface shadow-[var(--ask-shadow,none)] transition-[border-color,box-shadow] duration-fast has-[textarea:focus]:border-focus/60 has-[textarea:focus]:ring-4 has-[textarea:focus]:ring-focus/10',
        size === 'lg' ? 'py-2.5 pl-5 pr-2.5' : 'py-2 pl-4 pr-2',
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
        onChange={(e) => {
          // Typing while listening ends dictation, so the next result cannot overwrite the typed edit.
          if (dict.listening) dict.stop();
          onChange(e.target.value.slice(0, MAX_QUESTION));
        }}
        onKeyDown={(e) => {
          if (e.key === 'Escape' && dict.listening) {
            e.preventDefault();
            dict.stop();
            return;
          }
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (canSend) send();
          }
        }}
        rows={1}
        maxLength={MAX_QUESTION}
        aria-describedby="question-keys question-count"
        placeholder={placeholder}
        className={cn(
          'block w-full resize-none bg-transparent text-body leading-6 text-fg placeholder:text-subtle focus:outline-none focus-visible:outline-none',
          size === 'lg' ? 'min-h-[2.5rem] py-2' : 'min-h-[2.25rem] py-1.5',
        )}
      />
      <div className="contents">
        {/* read to screen readers; on screen the placeholder and the arrow say enough */}
        <p id="question-keys" className="sr-only">
          {t('ask.keys')}
        </p>
        <div className="flex items-center gap-1.5 pb-0.5">
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
          {dict.supported ? (
            <button
              type="button"
              data-testid="dictate"
              // One fixed name plus aria-pressed (a toggle button): a changing name as well would read as a
              // double state ("Stop dictating, pressed"). The tooltip still says what a click does.
              aria-pressed={dict.listening}
              aria-label={tv('voice.button')}
              title={dict.listening ? tv('voice.stop') : tv('voice.start')}
              aria-describedby="dictation-note"
              onClick={dict.toggle}
              onKeyDown={(e) => {
                if (e.key === 'Escape' && dict.listening) {
                  e.preventDefault();
                  dict.stop();
                } else if (e.key === ' ') {
                  // preventDefault stops the presenter's Space (advance) key and page scroll. The toggle runs
                  // on keyup only (below), where preventDefault also cancels the browser's own click, so a
                  // Space press toggles exactly once in every browser (Firefox clicks on keyup).
                  e.preventDefault();
                }
              }}
              onKeyUp={(e) => {
                if (e.key === ' ') {
                  e.preventDefault();
                  dict.toggle();
                }
              }}
              className={cn(
                'inline-flex h-9 w-9 items-center justify-center rounded-full border transition-colors duration-fast focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus',
                dict.listening ? 'border-focus bg-accent-solid/10 text-fg' : 'border-transparent bg-transparent text-muted hover:bg-raised hover:text-fg',
              )}
            >
              {dict.listening ? <Square aria-hidden className="h-3.5 w-3.5 fill-current" /> : <Mic aria-hidden className="h-4 w-4" />}
            </button>
          ) : null}
          <button
            type="submit"
            aria-label={t('ask.send')}
            title={t('ask.send')}
            disabled={!canSend}
            aria-busy={busy || undefined}
            className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-accent-solid text-accent-fg shadow-[0_1px_2px_rgb(0_0_0/0.12)] transition-colors duration-fast hover:bg-accent-solid/90 disabled:cursor-not-allowed disabled:bg-raised disabled:text-subtle disabled:shadow-none"
          >
            <ArrowUp aria-hidden className="h-4 w-4" />
          </button>
        </div>
      </div>
      {dict.supported ? (
        <p id="dictation-note" className="sr-only">
          {tv('voice.note')}
        </p>
      ) : null}
      <p role="status" aria-live="polite" data-testid="dictation-status" className={dict.state === 'idle' ? 'sr-only' : 'col-span-2 mt-1 text-caption text-muted'}>
        {dict.state === 'listening'
          ? tv('voice.listening')
          : dict.state === 'stopped'
            ? tv('voice.stopped')
            : dict.state === 'denied'
              ? tv('voice.denied')
              : dict.state === 'error'
                ? tv('voice.error')
                : ''}
      </p>
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
      <h2 id="starters-heading" className="mb-3 text-body-sm font-medium text-muted">
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
              className="group flex h-full w-full min-w-0 items-start gap-3 rounded-2xl border border-line bg-surface px-4 py-3.5 text-left transition-[border-color,background-color] duration-fast hover:border-line-strong hover:bg-raised/50 disabled:opacity-60"
            >
              <span aria-hidden className="mt-0.5 flex shrink-0 items-center gap-1">
                {inferPlatforms(q).map((p) => (
                  <PlatformIcon key={p} platform={p} tone="platform" size="sm" showLabel={false} />
                ))}
              </span>
              <span lang="en" className="line-clamp-3 text-body-sm leading-6 text-fg">{q}</span>
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
