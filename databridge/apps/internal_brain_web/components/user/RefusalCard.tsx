'use client';
import { CircleSlash, MessageSquareText, Users } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useMessages } from '@/lib/i18n';
import { userMessages } from '@/lib/i18n/user';

/**
 * The one refusal shape (rule 4). It shows the API's constant text exactly as returned, plus two
 * neutral next steps. It never says why, never counts anything and never hints that a source
 * exists: a restricted subject and a made-up one render byte-for-byte the same.
 */
export function RefusalCard({ text, compact = false, className }: { text: string; compact?: boolean; className?: string }) {
  const t = useMessages(userMessages);
  return (
    <div data-testid="refusal-card" className={cn('rounded-xl border border-line bg-surface', compact ? 'p-4' : 'p-5', className)}>
      <div className="flex items-start gap-3">
        <span aria-hidden className="mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-line bg-raised text-muted">
          <CircleSlash className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-heading-sm text-fg">{t('refusal.title')}</p>
          <p className="mt-1 whitespace-pre-wrap text-body text-fg" data-testid="refusal-text">
            {text}
          </p>
        </div>
      </div>
      <div className={cn('mt-4 border-t border-line pt-4', compact ? 'pl-0' : 'sm:pl-11')}>
        <p className="text-micro font-semibold uppercase tracking-wide text-subtle">{t('refusal.try')}</p>
        <ul className="mt-2 space-y-1.5 text-body-sm text-muted">
          <li className="flex items-start gap-2">
            <MessageSquareText aria-hidden className="mt-[3px] h-3.5 w-3.5 shrink-0" />
            <span>{t('refusal.rephrase')}</span>
          </li>
          <li className="flex items-start gap-2">
            <Users aria-hidden className="mt-[3px] h-3.5 w-3.5 shrink-0" />
            <span>{t('refusal.colleague')}</span>
          </li>
        </ul>
      </div>
    </div>
  );
}
