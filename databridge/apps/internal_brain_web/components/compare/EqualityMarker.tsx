'use client';
import { Equal, EqualNot } from 'lucide-react';
import { useMessages } from '@/lib/i18n';
import { presenterMessages } from '@/lib/i18n/presenter';

/**
 * Sits between the two columns when both responses are the same refusal, byte for byte. It states
 * only what the viewer can already see on both sides; it never says anything about why.
 */
export function EqualityMarker() {
  const t = useMessages(presenterMessages);
  return (
    <div className="flex items-center justify-center gap-3 lg:h-full lg:flex-col lg:gap-0 lg:px-1" data-testid="identical-marker">
      <span aria-hidden className="h-px flex-1 bg-line lg:h-auto lg:w-px lg:flex-1" />
      <div className="flex flex-col items-center gap-1.5 text-center lg:py-3">
        <span aria-hidden className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-line-strong bg-surface text-fg">
          <Equal className="h-5 w-5" />
        </span>
        <p className="text-body-sm font-semibold text-fg lg:max-w-[7rem]">{t('cmp.identical')}</p>
        <p className="text-caption text-muted lg:max-w-[7rem]">{t('cmp.identicalSub')}</p>
      </div>
      <span aria-hidden className="h-px flex-1 bg-line lg:h-auto lg:w-px lg:flex-1" />
    </div>
  );
}

/**
 * Sits between the two columns when both answered and the responses differ (text, scope note or
 * sources). Like the identical marker, it states only what is visible on both sides.
 */
export function DifferenceMarker() {
  const t = useMessages(presenterMessages);
  return (
    <div className="flex items-center justify-center gap-3 lg:h-full lg:flex-col lg:gap-0 lg:px-1" data-testid="different-marker">
      <span aria-hidden className="h-px flex-1 bg-line lg:h-auto lg:w-px lg:flex-1" />
      <div className="flex flex-col items-center gap-1.5 text-center lg:py-3">
        <span aria-hidden className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-line bg-surface text-muted">
          <EqualNot className="h-5 w-5" />
        </span>
        <p className="text-body-sm font-semibold text-fg lg:max-w-[7rem]">{t('cmp.different')}</p>
        <p className="text-caption text-muted lg:max-w-[7rem]">{t('cmp.differentSub')}</p>
      </div>
      <span aria-hidden className="h-px flex-1 bg-line lg:h-auto lg:w-px lg:flex-1" />
    </div>
  );
}
