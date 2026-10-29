'use client';
import { forwardRef } from 'react';
import { cn } from '@/lib/cn';
import { PLATFORM_LABEL } from '@/lib/format';
import { useMessages } from '@/lib/i18n';
import { userMessages } from '@/lib/i18n/user';
import type { Citation } from '@/lib/types';

const TINT: Record<string, string> = {
  confluence: 'border-pf-confluence/45 bg-pf-confluence/10 text-pf-confluence hover:bg-pf-confluence/20',
  jira: 'border-pf-jira/45 bg-pf-jira/10 text-pf-jira hover:bg-pf-jira/20',
  slack: 'border-pf-slack/45 bg-pf-slack/10 text-pf-slack hover:bg-pf-slack/20',
  drive: 'border-pf-drive/45 bg-pf-drive/10 text-pf-drive hover:bg-pf-drive/20',
};

/**
 * Inline `[n]` citation chip, tinted with its platform colour (the number and the accessible name
 * carry the meaning; colour is only a cue). A real button: focusable, Enter/Space open the source.
 */
export const CitationChip = forwardRef<
  HTMLButtonElement,
  { citation: Citation; n: number; onOpen?: (el: HTMLButtonElement) => void; className?: string }
>(function CitationChip({ citation, n, onOpen, className }, ref) {
  const t = useMessages(userMessages);
  const platform = PLATFORM_LABEL[citation.platform] ?? citation.platform;
  return (
    <button
      ref={ref}
      type="button"
      onClick={(e) => onOpen?.(e.currentTarget)}
      aria-label={t('answer.chipLabel', { n, title: citation.title, platform })}
      title={`${citation.title} · ${citation.container_name}`}
      data-testid="citation-chip"
      data-tag={citation.tag}
      data-platform={citation.platform}
      className={cn(
        'mx-[1px] inline-flex h-[1.375rem] min-w-[1.5rem] items-center justify-center rounded-md border px-1 align-[2px] text-caption font-semibold tabular-nums leading-none transition-colors duration-fast',
        TINT[citation.platform] ?? 'border-line-strong bg-raised text-muted hover:bg-line',
        className,
      )}
    >
      {n}
    </button>
  );
});
