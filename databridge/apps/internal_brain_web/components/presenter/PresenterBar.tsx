'use client';
// The floating presenter bar (bottom centre, fixed): the current step, a preview of the next one,
// the keys, three status dots and the honest "synthetic data" label. Speaker cues show only when
// the Stage theme is off (rehearsal), so they never reach the projector.
import { Loader2, TriangleAlert } from 'lucide-react';
import { Kbd } from '@/components/ui';
import { cn } from '@/lib/cn';
import { useMessages } from '@/lib/i18n';
import { presenterMessages, type PresenterKey } from '@/lib/i18n/presenter';
import { STEPS, STEP_KEY } from './script';
import { usePresenter, type Dot } from './store';

const DOT_TONE: Record<Dot, string> = {
  ok: 'bg-ok',
  live: 'bg-ok',
  quoting: 'bg-warn',
  down: 'bg-bad',
  unknown: 'bg-subtle',
};
const DOT_TEXT: Record<Dot, PresenterKey> = {
  ok: 'pr.status.ok',
  live: 'pr.status.live',
  quoting: 'pr.status.quoting',
  down: 'pr.status.down',
  unknown: 'pr.status.unknown',
};

function Key({ k, label }: { k: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      <Kbd>{k}</Kbd>
      <span>{label}</span>
    </span>
  );
}

export function PresenterBar() {
  const t = useMessages(presenterMessages);
  const s = usePresenter();
  const last = STEPS.length - 1;
  const step = s.started ? STEPS[s.step] : null;
  const next = s.started ? STEPS[s.step + 1] : STEPS[0];
  const title = step ? t(STEP_KEY[step.id]) : t('pr.pressP');
  const h = s.health;

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-40 flex justify-center px-3 pb-[max(0.75rem,2.5vh)] print:hidden">
      <section
        aria-label={t('pr.region')}
        data-testid="presenter-bar"
        data-step={step ? step.id : 'none'}
        className="pointer-events-auto w-full max-w-[60rem] rounded-xl border border-line-strong bg-overlay text-fg shadow-pop"
      >
        <div className="flex min-w-0 items-center gap-4 px-4 py-3">
          <p className="flex shrink-0 items-baseline gap-1 tabular-nums" aria-label={step ? t('pr.step', { n: s.step, total: last }) : undefined}>
            <span className="text-heading text-fg" data-testid="presenter-step-number">
              {step ? String(s.step).padStart(2, '0') : '—'}
            </span>
            <span className="text-caption text-subtle">/ {String(last).padStart(2, '0')}</span>
          </p>
          <span aria-hidden className="h-8 w-px shrink-0 bg-line" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-heading-sm text-fg" data-testid="presenter-step-title">
              {title}
            </p>
            <p className="flex min-w-0 items-center gap-1.5 truncate text-caption text-muted" data-testid="presenter-next">
              {s.busy ? (
                <>
                  <Loader2 aria-hidden className="h-3.5 w-3.5 shrink-0 motion-safe:animate-spin" />
                  {t('pr.working')}
                </>
              ) : s.failed ? (
                <>
                  <TriangleAlert aria-hidden className="h-3.5 w-3.5 shrink-0 text-warn" />
                  <span className="truncate text-fg">{t('pr.failed')}</span>
                </>
              ) : !step ? (
                t('pr.armed')
              ) : next ? (
                <>
                  <span className="shrink-0 font-semibold uppercase tracking-wider text-subtle">{t('pr.next')}</span>
                  <span className="truncate">{t(STEP_KEY[next.id])}</span>
                </>
              ) : (
                t('pr.end')
              )}
            </p>
          </div>
          {h ? (
            <ul aria-label={t('pr.status')} className="hidden shrink-0 items-center gap-3 text-caption text-muted md:flex" data-testid="presenter-status">
              {(
                [
                  ['pr.status.api', h.api],
                  ['pr.status.model', h.model],
                  ['pr.status.ledger', h.ledger],
                ] as [PresenterKey, Dot][]
              ).map(([k, d]) => (
                <li key={k} className="inline-flex items-center gap-1.5 whitespace-nowrap">
                  <span aria-hidden className={cn('h-2 w-2 rounded-full', DOT_TONE[d])} />
                  <span>
                    {t(k)} <span className="text-fg">{t(DOT_TEXT[d])}</span>
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-line px-4 py-2 text-caption text-muted">
          {step ? (
            <>
              <Key k="Space" label={t('pr.key.next')} />
              <Key k="←" label={t('pr.key.back')} />
              <Key k="B" label={t('pr.key.clip')} />
              <Key k="R" label={t('pr.key.reset')} />
              <Key k="L" label={t('pr.key.lang')} />
              <Key k="Esc" label={t('pr.key.hide')} />
            </>
          ) : (
            <>
              <Key k="P" label={t('pr.pressP')} />
              <Key k="S" label={t('pr.key.stage')} />
              <Key k="Esc" label={t('pr.key.hide')} />
            </>
          )}
          <span className="ml-auto whitespace-nowrap text-subtle" data-testid="presenter-demo-data">
            {t('pr.demoData')}
          </span>
        </div>
        {step && !s.stage ? (
          <p className="border-t border-line px-4 py-2 text-caption text-muted" data-testid="presenter-say">
            {step.say}
          </p>
        ) : null}
        <p role="status" aria-live="polite" className="sr-only">
          {step ? t('pr.announce', { n: s.step, title }) : ''}
        </p>
      </section>
    </div>
  );
}
