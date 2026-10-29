'use client';
// Step 7 happens outside the app: Aaruni asks in the WorkBuddy window, signed in as Jordan. The card
// tells the room what is happening and gives the driver the exact question to paste.
import { Check, Copy, MonitorUp } from 'lucide-react';
import { useState } from 'react';
import { Button } from '@/components/ui';
import { useMessages } from '@/lib/i18n';
import { presenterMessages } from '@/lib/i18n/presenter';
import { Q_WORKBUDDY } from './script';

export function WorkBuddyCard() {
  const t = useMessages(presenterMessages);
  const [copied, setCopied] = useState(false);
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-[calc(max(0.75rem,2.5vh)+7.5rem)] z-40 flex justify-center px-3 print:hidden">
      <section
        aria-labelledby="wb-title"
        data-testid="workbuddy-card"
        className="pointer-events-auto w-full max-w-[44rem] rounded-xl border border-line-strong bg-overlay p-5 text-fg shadow-pop"
      >
        <p className="inline-flex items-center gap-1.5 text-micro font-semibold uppercase tracking-wider text-subtle">
          <MonitorUp aria-hidden className="h-3.5 w-3.5" />
          {t('pr.wb.eyebrow')}
        </p>
        <h2 id="wb-title" className="mt-1 text-title text-fg">
          {t('pr.wb.title')}
        </h2>
        <p className="mt-2 text-body-sm text-muted">{t('pr.wb.body')}</p>
        <blockquote className="mt-2 rounded-lg border border-line bg-surface px-4 py-3 text-body text-fg" data-testid="workbuddy-question">
          {Q_WORKBUDDY}
        </blockquote>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <p className="max-w-md text-caption text-muted">{t('pr.wb.back')}</p>
          <Button
            size="sm"
            icon={copied ? Check : Copy}
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(Q_WORKBUDDY);
                setCopied(true);
                setTimeout(() => setCopied(false), 1600);
              } catch {
                /* clipboard blocked: the text is on screen */
              }
            }}
          >
            {copied ? t('pr.wb.copied') : t('pr.wb.copy')}
          </Button>
        </div>
      </section>
    </div>
  );
}
