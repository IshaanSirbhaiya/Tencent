'use client';
// The floating presenter bar (bottom centre, fixed): the current step, a preview of the next one,
// the keys, three status dots and the honest "synthetic data" label. Speaker cues show only when
// the Stage theme is off (rehearsal), so they never reach the projector.
//
// In Stage the bar is ONE row (step, title, next, dots, synthetic-data label); the key legend shows
// for 3 s after P, or while toggled with ?. Off Stage (rehearsal) it keeps its full two rows.
//
// Mouse controls (PRESENTER_KEYS.md, "Mouse"): icon buttons at the end of the first row call the
// SAME controller actions as the keys (controller.ts: advance, goBack, startOnce, toggleClip,
// askReset, toggleHidden, exitPresenter), so a click obeys the same busy and debounce rules as a
// key. A busy step makes the Next button aria-disabled (not `disabled`), so a focused button keeps
// the focus. Space on a focused bar button still means Next (its keyup is swallowed, controller.ts);
// Enter or a click activates the button itself. In Stage the one row has no room to spare (the
// title, the next step, the dots and the synthetic-data label), so it keeps only "More": every
// control sits in the key-legend row, which More (or ?) opens and which stays open until closed.
import { ChevronLeft, ChevronRight, EyeOff, Film, Loader2, LogOut, MoreHorizontal, PanelBottomOpen, Play, RotateCcw, SkipForward, TriangleAlert, type LucideIcon } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { IconButton, Kbd } from '@/components/ui';
import { announce } from '@/components/ui/announce';
import { cn } from '@/lib/cn';
import { useMessages } from '@/lib/i18n';
import { presenterMessages, type PresenterKey } from '@/lib/i18n/presenter';
import { advance, askReset, exitPresenter, goBack, pinLegend, startOnce, toggleClip, toggleHidden, toggleLegend } from './controller';
import { STEPS, STEP_KEY } from './script';
import { setPresenter, usePresenter, type Dot } from './store';

/** Where the focus goes after Hide or Show was CLICKED (not after H), so a keyboard user is not dropped. */
let focusNext: 'show' | 'hide' | null = null;

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

interface CtlProps {
  icon: LucideIcon;
  label: string;
  /** The key, as the tooltip shows it. */
  keyName: string;
  /** aria-keyshortcuts value. */
  shortcut: string;
  onClick: () => void;
  disabled?: boolean;
  primary?: boolean;
  compact: boolean;
  testId: string;
  btnRef?: React.Ref<HTMLButtonElement>;
  /** Disclosure buttons (More). */
  expanded?: boolean;
  controls?: string;
}

/** One bar control: an icon button with its key in the tooltip, at least 32 px, the global focus ring. */
function Ctl({ icon, label, keyName, shortcut, onClick, disabled, primary, compact, testId, btnRef, expanded, controls }: CtlProps) {
  const t = useMessages(presenterMessages);
  return (
    <IconButton
      ref={btnRef}
      icon={icon}
      label={label}
      title={t('pr.ctl.withKey', { label, key: keyName })}
      aria-keyshortcuts={shortcut}
      aria-disabled={disabled || undefined}
      aria-expanded={expanded}
      aria-controls={controls}
      variant={primary ? 'secondary' : 'ghost'}
      data-testid={testId}
      className={cn('min-h-[32px] min-w-[32px] aria-disabled:cursor-not-allowed aria-disabled:opacity-55', compact ? 'h-6 w-6' : 'h-8 w-8')}
      onClick={() => {
        if (!disabled) onClick();
      }}
    />
  );
}

/** The small handle that brings a hidden bar back (H does the same). On screen whenever the bar is hidden. */
export function PresenterShowHandle({ onShow = () => toggleHidden(false) }: { onShow?: () => void }) {
  const t = useMessages(presenterMessages);
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (focusNext === 'show') ref.current?.focus();
    focusNext = null;
  }, []);
  const label = t('pr.ctl.show');
  return (
    <div className="pointer-events-none fixed bottom-0 right-0 z-40 p-[max(0.75rem,2.5vh)] print:hidden">
      <IconButton
        ref={ref}
        icon={PanelBottomOpen}
        label={label}
        title={t('pr.ctl.withKey', { label, key: 'H' })}
        aria-keyshortcuts="H"
        variant="secondary"
        data-testid="presenter-show"
        className="pointer-events-auto min-h-[32px] min-w-[32px] bg-overlay shadow-pop"
        onClick={() => {
          focusNext = 'hide';
          onShow();
        }}
      />
    </div>
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

  const hideRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (focusNext === 'hide') {
      // In Stage, Hide sits in the More row: open it so the focus lands where the click came from.
      if (!hideRef.current) setPresenter({ legend: true });
      requestAnimationFrame(() => hideRef.current?.focus());
    }
    focusNext = null;
  }, []);
  const space = t('pr.keyname.space');
  const atEnd = s.started && s.step >= last;
  const keysId = 'presenter-keys-row';
  const primaryControls = (
    <>
      {step ? (
        <>
          <Ctl icon={ChevronLeft} label={t('pr.ctl.back')} keyName="←" shortcut="ArrowLeft" onClick={goBack} disabled={s.busy || s.resetting || s.step === 0} compact={compact} testId="presenter-ctl-back" />
          <Ctl
            icon={ChevronRight}
            label={t('pr.ctl.next')}
            keyName={space}
            shortcut="Space"
            onClick={() => advance(false)}
            disabled={s.busy || s.resetting || (atEnd && !s.failed)}
            primary
            compact={compact}
            testId="presenter-ctl-next"
          />
          {s.failed && !s.busy && !atEnd ? (
            <Ctl icon={SkipForward} label={t('pr.ctl.skip')} keyName="→" shortcut="ArrowRight" onClick={() => advance(true)} compact={compact} testId="presenter-ctl-skip" />
          ) : null}
          {step.clip ? <Ctl icon={Film} label={t('pr.ctl.clip')} keyName="B" shortcut="B" onClick={toggleClip} compact={compact} testId="presenter-ctl-clip" /> : null}
        </>
      ) : (
        <Ctl icon={Play} label={t('pr.ctl.start')} keyName="P" shortcut="P" onClick={startOnce} disabled={s.busy || s.resetting} primary compact={compact} testId="presenter-ctl-start" />
      )}
    </>
  );
  const secondaryControls = (
    <>
      <Ctl icon={RotateCcw} label={t('pr.ctl.reset')} keyName="R" shortcut="R" onClick={askReset} disabled={s.resetting} compact={compact} testId="presenter-ctl-reset" />
      <Ctl
        icon={EyeOff}
        label={t('pr.ctl.hide')}
        keyName="H"
        shortcut="H"
        onClick={() => {
          focusNext = 'show';
          toggleHidden(true);
        }}
        compact={compact}
        testId="presenter-ctl-hide"
        btnRef={hideRef}
      />
      <Ctl
        icon={LogOut}
        label={t('pr.ctl.exit')}
        keyName="Ctrl+Shift+P"
        shortcut="Control+Shift+P"
        onClick={() => {
          exitPresenter();
          announce(t('pr.ctl.exited'));
        }}
        compact={compact}
        testId="presenter-ctl-exit"
      />
    </>
  );
  const group = (children: React.ReactNode, testId: string) => (
    <div role="group" aria-label={t('pr.ctl.group')} className="flex shrink-0 items-center gap-0.5" data-testid={testId}>
      {children}
    </div>
  );
  // Stage: the row keeps only More; every control lives in the legend row (always shown before P).
  const rowControls = compact
    ? group(
        <>
          {step ? (
            <Ctl
              icon={MoreHorizontal}
              label={t('pr.ctl.more')}
              keyName="?"
              shortcut="?"
              onClick={toggleLegend}
              expanded={showKeys}
              controls={showKeys ? keysId : undefined}
              compact={compact}
              testId="presenter-ctl-more"
            />
          ) : null}
        </>,
        'presenter-controls',
      )
    : group(
        <>
          {primaryControls}
          {secondaryControls}
        </>,
        'presenter-controls',
      );

  const demoData = (
    <span className={cn('whitespace-nowrap text-subtle', compact ? 'shrink-0 text-caption' : 'ml-auto')} data-testid="presenter-demo-data">
      {t('pr.demoData')}
    </span>
  );

  return (
    // Above the sources sheet's scrim and panel (z-40, z-50) while one is open, so ▶ Next can be clicked over it (stage QA re-run).
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-40 flex justify-center px-3 pb-[max(0.75rem,2.5vh)] print:hidden [html:has([data-testid=sources-sheet])_&]:z-[55]">
      <section
        aria-label={t('pr.region')}
        data-testid="presenter-bar"
        data-step={step ? step.id : 'none'}
        data-state={s.resetting ? 'resetting' : s.busy ? 'busy' : s.failed ? 'failed' : 'ready'}
        data-compact={compact ? 'true' : undefined}
        className="pointer-events-auto w-full max-w-[60rem] rounded-xl border border-line-strong bg-overlay text-fg shadow-pop"
      >
        <div className={cn('flex min-w-0 items-center', compact ? 'gap-2 px-3 py-1.5' : 'gap-4 px-4 py-3')}>
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
              <p className="min-w-[3.5rem] shrink truncate text-body-sm font-semibold text-fg" data-testid="presenter-step-title">
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
          {!compact || step ? (
            <>
              <span aria-hidden className={cn('w-px shrink-0 bg-line', compact ? 'h-5' : 'h-8')} />
              {rowControls}
            </>
          ) : null}
        </div>
        {compact && s.failed && !s.busy ? (
          <p className="flex items-center gap-1.5 border-t border-line px-4 py-1.5 text-caption text-fg" data-testid="presenter-failed">
            {t('pr.failed')}
          </p>
        ) : null}
        {showKeys ? (
          <div id={keysId} className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-line px-4 py-2 text-caption text-muted" data-testid="presenter-keys">
            {step ? (
              <>
                <Key k="Space" label={t('pr.key.next')} />
                <Key k="←" label={t('pr.key.back')} />
                <Key k="B" label={t('pr.key.clip')} />
                <Key k="R" label={t('pr.key.reset')} />
                <Key k="L" label={t('pr.key.lang')} />
                <Key k="H" label={t('pr.key.hide')} />
                <Key k="?" label={t('pr.key.legend')} />
              </>
            ) : (
              <>
                <Key k="P" label={t('pr.pressP')} />
                <Key k="S" label={t('pr.key.stage')} />
                <Key k="Esc" label={t('pr.key.hide')} />
              </>
            )}
            {compact ? (
              <div className="ml-auto" onClickCapture={pinLegend}>
                {group(
                  <>
                    {primaryControls}
                    {secondaryControls}
                  </>,
                  'presenter-controls-more',
                )}
              </div>
            ) : (
              demoData
            )}
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
