'use client';
// The floating presenter bar (bottom centre, fixed): the current step, a preview of the next one,
// the keys, three status dots and the honest "synthetic data" label. Speaker cues show only when
// the Stage theme is off (rehearsal), so they never reach the projector.
//
// In Stage the bar is ONE row (step, title, next, dots, synthetic-data label); the key legend shows
// for 3 s after P, or while toggled with ?. Off Stage (rehearsal) it keeps its full two rows.
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
  const compact = s.stage;
  const showKeys = !compact || s.legend || !step;

  const status = s.busy ? (
    <>
      <Loader2 aria-hidden className="h-3.5 w-3.5 shrink-0 motion-safe:animate-spin" />
      <span className="truncate" data-testid="presenter-working">
        {t(s.resetting ? 'pr.resetting' : 'pr.working')}
      </span>
    </>
  ) : s.failed ? (
    <>
      <TriangleAlert aria-hidden className="h-3.5 w-3.5 shrink-0 text-warn" />
      <span className="truncate text-fg" data-testid={compact ? undefined : 'presenter-failed'}>
        {t('pr.failed')}
      </span>
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
  );

  const demoData = (
    <span className={cn('whitespace-nowrap text-subtle', compact ? 'shrink-0 text-caption' : 'ml-auto')} data-testid="presenter-demo-data">
      {t('pr.demoData')}
    </span>
  );

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-40 flex justify-center px-3 pb-[max(0.75rem,2.5vh)] print:hidden">
      <section
        aria-label={t('pr.region')}
        data-testid="presenter-bar"
        data-step={step ? step.id : 'none'}
        data-state={s.resetting ? 'resetting' : s.busy ? 'busy' : s.failed ? 'failed' : 'ready'}
        data-compact={compact ? 'true' : undefined}
        className="pointer-events-auto w-full max-w-[60rem] rounded-xl border border-line-strong bg-overlay text-fg shadow-pop"
      >
        <div className={cn('flex min-w-0 items-center', compact ? 'gap-3 px-3 py-1.5' : 'gap-4 px-4 py-3')}>
          <p className="flex shrink-0 items-baseline gap-1 tabular-nums" aria-label={step ? t('pr.step', { n: s.step, total: last }) : undefined}>
            <span className={cn('text-fg', compact ? 'text-heading-sm' : 'text-heading')} data-testid="presenter-step-number">
              {step ? String(s.step).padStart(2, '0') : '—'}
            </span>
            <span className="text-caption text-subtle">/ {String(last).padStart(2, '0')}</span>
          </p>
          <span aria-hidden className={cn('w-px shrink-0 bg-line', compact ? 'h-5' : 'h-8')} />
          {compact ? (
            // One line: title, then what comes next (or the step's state).
            <div className="flex min-w-0 flex-1 items-baseline gap-3">
              <p className="min-w-0 shrink truncate text-body-sm font-semibold text-fg" data-testid="presenter-step-title">
                {title}
              </p>
              {/* A failed step says so on its own row below (it must be read in full). */}
              <p className="flex min-w-[6.5rem] flex-1 items-center gap-1.5 truncate text-caption text-muted" data-testid="presenter-next">
                {s.failed && !s.busy ? (
                  <>
                    <TriangleAlert aria-hidden className="h-3.5 w-3.5 shrink-0 text-warn" />
                    <span className="truncate text-fg">{t('pr.failedShort')}</span>
                  </>
                ) : (
                  status
                )}
              </p>
            </div>
          ) : (
            <div className="min-w-0 flex-1">
              <p className="truncate text-heading-sm text-fg" data-testid="presenter-step-title">
                {title}
              </p>
              <p className="flex min-w-0 items-center gap-1.5 truncate text-caption text-muted" data-testid="presenter-next">
                {status}
              </p>
            </div>
          )}
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
          {compact ? demoData : null}
        </div>
        {compact && s.failed && !s.busy ? (
          <p className="flex items-center gap-1.5 border-t border-line px-4 py-1.5 text-caption text-fg" data-testid="presenter-failed">
            {t('pr.failed')}
          </p>
        ) : null}
        {showKeys ? (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-line px-4 py-2 text-caption text-muted" data-testid="presenter-keys">
            {step ? (
              <>
                <Key k="Space" label={t('pr.key.next')} />
                <Key k="←" label={t('pr.key.back')} />
                <Key k="B" label={t('pr.key.clip')} />
                <Key k="R" label={t('pr.key.reset')} />
                <Key k="L" label={t('pr.key.lang')} />
                <Key k="Esc" label={t('pr.key.hide')} />
                <Key k="?" label={t('pr.key.legend')} />
              </>
            ) : (
              <>
                <Key k="P" label={t('pr.pressP')} />
                <Key k="S" label={t('pr.key.stage')} />
                <Key k="Esc" label={t('pr.key.hide')} />
              </>
            )}
            {compact ? null : demoData}
          </div>
        ) : null}
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
