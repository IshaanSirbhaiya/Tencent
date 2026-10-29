'use client';
import { cn } from '@/lib/cn';
import { useMessages } from '@/lib/i18n';
import { userMessages } from '@/lib/i18n/user';
import { ASK_STEPS } from './useAskProgress';

/**
 * The staged status line: "Checking your access → Searching your sources → … → Verifying
 * citations". It is exposed as a progressbar (status, not answer content); the current step is
 * its value text. Pair it with one polite announcement at the start (the page does that).
 */
export function AskProgressLine({ step, compact = false, className }: { step: number; compact?: boolean; className?: string }) {
  const t = useMessages(userMessages);
  const current = Math.min(step, ASK_STEPS.length - 1);
  return (
    <div
      role="progressbar"
      aria-label={t('progress.label')}
      aria-valuemin={0}
      aria-valuemax={ASK_STEPS.length}
      aria-valuenow={Math.min(step, ASK_STEPS.length)}
      aria-valuetext={t(ASK_STEPS[current])}
      data-testid="ask-progress"
      data-step={step}
      className={cn('flex flex-col gap-2.5', className)}
    >
      <p className={cn('flex items-center gap-2.5 font-medium text-fg', compact ? 'text-sm' : 'text-body')}>
        <span aria-hidden className="relative inline-flex h-2.5 w-2.5 shrink-0">
          <span className="absolute inset-0 rounded-full bg-accent/40 motion-safe:animate-ping" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-accent" />
        </span>
        <span>{t(ASK_STEPS[current])}…</span>
      </p>
      <span aria-hidden className={cn('grid w-full max-w-xs grid-cols-5 gap-1', compact && 'max-w-[12rem]')}>
        {ASK_STEPS.map((k, i) => (
          <span
            key={k}
            className={cn(
              'h-1 rounded-full transition-colors duration-slow',
              i < step ? 'bg-accent' : i === current ? 'bg-accent/45 motion-safe:animate-pulse' : 'bg-line-strong',
            )}
          />
        ))}
      </span>
    </div>
  );
}
