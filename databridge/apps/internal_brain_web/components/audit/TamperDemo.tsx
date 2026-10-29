'use client';
// The sandbox tamper demo (D-19, B11; backend_v1.md v2): POST /audit/tamper-demo copies the live
// ledger into a temporary directory, attacks the COPY in several ways, returns Verify's report for
// each copy and whether the real log changed (`real_log_modified`, re-checked byte for byte on the
// server). The real chain is then re-verified here too (GET /audit/verify).
// Red on this page means one thing only: tampering detected.
import { AlertOctagon, CheckCircle2, FlaskConical, Info, ShieldCheck, XCircle } from 'lucide-react';
import { forwardRef, useCallback, useEffect, useRef, useState } from 'react';
import { Badge, Button, CodeHash } from '@/components/ui';
import { ApiError, api, auditApi } from '@/lib/api';
import { cn } from '@/lib/cn';
import { useMessages } from '@/lib/i18n';
import { auditMessages } from '@/lib/i18n/audit';
import type { TamperDemoResult, TamperScenario, VerifyResult } from '@/lib/types';

type State =
  | { kind: 'idle' }
  | { kind: 'running' }
  | { kind: 'done'; res: TamperDemoResult; real: VerifyResult | null }
  | { kind: 'error'; status: number };

const detected = (s: TamperScenario) => !!s.detected;

/**
 * The progress line while the one POST runs (about 3 s live). It is PROGRESS, not data: the steps
 * are the three things the endpoint does, in order, advanced on elapsed time; the seconds are real.
 */
const RUN_STEPS = ['audit.tamper.run1', 'audit.tamper.run2', 'audit.tamper.run3'] as const;
const RUN_AT = [0, 700, 1700];

function RunningLine() {
  const t = useMessages(auditMessages);
  const [ms, setMs] = useState(0);
  useEffect(() => {
    const t0 = Date.now();
    const id = setInterval(() => setMs(Date.now() - t0), 250);
    return () => clearInterval(id);
  }, []);
  const step = RUN_AT.filter((at) => ms >= at).length - 1;
  return (
    <div
      role="progressbar"
      aria-label={t('audit.tamper.running')}
      aria-valuemin={0}
      aria-valuemax={RUN_STEPS.length}
      aria-valuenow={step + 1}
      aria-valuetext={t(RUN_STEPS[step])}
      data-testid="tamper-progress"
      data-step={step}
      className="space-y-2"
    >
      <p className="flex items-center gap-2.5 text-body font-medium text-fg">
        <span aria-hidden className="relative inline-flex h-2.5 w-2.5 shrink-0">
          <span className="absolute inset-0 rounded-full bg-accent/40 motion-safe:animate-ping" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-accent" />
        </span>
        <span>{t(RUN_STEPS[step])}…</span>
        <span className="font-mono text-caption tabular-nums text-subtle">{(ms / 1000).toFixed(0)} s</span>
      </p>
      <span aria-hidden className="grid w-full max-w-xs grid-cols-3 gap-1">
        {RUN_STEPS.map((k, i) => (
          <span key={k} className={cn('h-1 rounded-full', i < step ? 'bg-accent' : i === step ? 'bg-accent/45 motion-safe:animate-pulse' : 'bg-line-strong')} />
        ))}
      </span>
    </div>
  );
}
const asExpected = (s: TamperScenario) => s.ok && !s.skipped;

/** A few records around the break (or the head, when untouched). Decorative: the text says it all. */
function ChainStrip({ s }: { s: TamperScenario }) {
  const bad = detected(s);
  const k = typeof s.first_bad_seq === 'number' ? s.first_bad_seq : null;
  type Cell = { label: string; tone: 'ok' | 'bad' | 'gone' };
  const plain = (last: 'ok' | 'bad'): Cell[] => [
    { label: '', tone: 'ok' },
    { label: '', tone: 'ok' },
    { label: '', tone: 'ok' },
    { label: 'head', tone: last },
  ];
  const cells: Cell[] = k
    ? [-2, -1, 0, 1, 2]
        .map((o): Cell => ({ label: `#${k + o}`, tone: o < 0 ? 'ok' : o === 0 ? 'bad' : 'gone' }))
        .filter((c) => Number(c.label.slice(1)) > 0)
    : plain(bad ? 'bad' : 'ok');
  return (
    <span aria-hidden className="flex items-end gap-1">
      {cells.map((c, i) => (
        <span key={i} className="flex flex-col items-center gap-0.5">
          <span
            className={cn(
              'flex h-5 w-7 items-center justify-center rounded border',
              c.tone === 'ok' && 'border-ok/60 bg-ok/15',
              c.tone === 'bad' && 'border-bad bg-bad/20 text-bad',
              c.tone === 'gone' && 'border-dashed border-line-strong bg-transparent',
            )}
          >
            {c.tone === 'bad' ? <XCircle className="h-3.5 w-3.5" /> : null}
          </span>
          <span className={cn('font-mono text-[10px] leading-none tabular-nums', c.tone === 'bad' ? 'font-semibold text-bad' : 'text-subtle')}>{c.label || ' '}</span>
        </span>
      ))}
    </span>
  );
}

function Result({ s }: { s: TamperScenario }) {
  const t = useMessages(auditMessages);
  if (s.skipped) return <p className="text-body-sm text-muted">{t('audit.tamper.skipped')}</p>;
  if (detected(s)) {
    return (
      <div className="min-w-0">
        <p className="flex items-center gap-1.5 text-heading-sm font-semibold text-bad">
          <AlertOctagon aria-hidden className="h-4 w-4 shrink-0" />
          {typeof s.first_bad_seq === 'number' ? t('audit.tamper.brokenAt', { n: s.first_bad_seq.toLocaleString('en-SG') }) : t('audit.tamper.detected')}
        </p>
        {s.problems.length ? (
          <ul className="mt-1 space-y-0.5">
            {s.problems.map((p) => (
              <li key={p} className="text-2xs text-muted">
                {p}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    );
  }
  return (
    <p className="flex items-center gap-1.5 text-heading-sm font-semibold text-ok">
      <CheckCircle2 aria-hidden className="h-4 w-4 shrink-0" />
      {s.id === 'rollback-after-publication' ? <span className="text-fg">{t('audit.tamper.window')}</span> : t('audit.tamper.untouched')}
    </p>
  );
}

export const TamperDemo = forwardRef<HTMLElement, { autoRun?: boolean }>(function TamperDemo({ autoRun }, ref) {
  const t = useMessages(auditMessages);
  const [state, setState] = useState<State>({ kind: 'idle' });
  const [shown, setShown] = useState(false);

  const run = useCallback(async () => {
    setShown(false);
    setState({ kind: 'running' });
    try {
      const res = await auditApi.tamperDemo();
      // Prove the point: re-verify the REAL log right after the sandbox run.
      let real: VerifyResult | null = null;
      try {
        real = await api.verify();
      } catch {
        real = null;
      }
      setState({ kind: 'done', res, real });
    } catch (e) {
      setState({ kind: 'error', status: e instanceof ApiError ? e.status : 0 });
    }
  }, []);

  const started = useRef(false);
  useEffect(() => {
    if (autoRun && !started.current) {
      started.current = true;
      void run();
    }
  }, [autoRun, run]);

  const panelRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (state.kind !== 'done') return;
    const id = requestAnimationFrame(() => setShown(true));
    // Presenter mode: bring the first "Chain broken" row (the one read aloud) into view, clear of
    // the presenter bar; the panel itself when nothing broke.
    if (autoRun && panelRef.current) {
      const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches || document.documentElement.dataset.motion === 'reduce';
      const row = panelRef.current.querySelector<HTMLElement>('[data-detected="true"]');
      (row ?? panelRef.current).scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: row ? 'center' : 'start' });
    }
    return () => cancelAnimationFrame(id);
  }, [state.kind, autoRun]);

  const setRefs = (el: HTMLElement | null) => {
    panelRef.current = el;
    if (typeof ref === 'function') ref(el);
    else if (ref) ref.current = el;
  };

  const res = state.kind === 'done' ? state.res : null;
  const real = state.kind === 'done' ? state.real : null;

  return (
    <section
      ref={setRefs}
      id="tamper"
      aria-labelledby="tamper-heading"
      className="scroll-mt-20 overflow-hidden rounded-xl border border-line-strong bg-surface"
      data-testid="tamper-panel"
      data-state={state.kind}
    >
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line bg-raised/40 px-4 py-4 sm:px-5">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-micro font-semibold uppercase tracking-wide text-subtle">
            <FlaskConical aria-hidden className="h-3.5 w-3.5" />
            {t('audit.tamper.eyebrow')}
          </p>
          <h2 id="tamper-heading" className="mt-1 text-heading text-fg">
            {t('audit.tamper.heading')}
          </h2>
          <p className="mt-1 max-w-2xl text-body-sm text-muted">{t('audit.tamper.sub')}</p>
        </div>
        <Button variant={state.kind === 'done' ? 'secondary' : 'primary'} size="lg" icon={FlaskConical} busy={state.kind === 'running'} onClick={() => void run()}>
          {state.kind === 'done' ? t('audit.tamper.again') : t('audit.tamper.button')}
        </Button>
      </div>

      <div aria-live="polite" className="px-4 py-4 sm:px-5">
        {state.kind === 'idle' ? (
          <ol className="grid gap-2 text-body-sm text-muted sm:grid-cols-3">
            {[t('audit.tamper.step1'), t('audit.tamper.step2'), t('audit.tamper.step3')].map((s, i) => (
              <li key={s} className="flex items-start gap-2 rounded-lg border border-dashed border-line-strong px-3 py-2">
                <span className="font-mono text-2xs text-subtle tabular-nums">0{i + 1}</span>
                {s}
              </li>
            ))}
          </ol>
        ) : state.kind === 'running' ? (
          <div className="space-y-3">
            <RunningLine />
            <div className="ds-skeleton h-16 rounded-lg bg-raised" />
            <div className="ds-skeleton h-10 rounded-lg bg-raised" />
            <div className="ds-skeleton h-10 rounded-lg bg-raised" />
          </div>
        ) : state.kind === 'error' ? (
          <p role="alert" className="flex items-center gap-2 text-body text-fg">
            <Info aria-hidden className="h-4 w-4 text-muted" />
            {state.status === 409 ? t('audit.tamper.busy') : t('audit.tamper.error')}
          </p>
        ) : res ? (
          <div className="space-y-5">
            {/* The line that matters most: the real log is safe (the backend re-checked every byte). */}
            <div
              className={cn(
                'flex flex-col gap-4 rounded-xl border-2 p-4 sm:flex-row sm:items-center sm:p-5',
                res.real_log_modified === false ? 'border-ok/50 bg-ok/[0.07]' : 'border-bad/60 bg-bad/[0.07]',
              )}
              data-testid={res.real_log_modified === false ? 'tamper-safe' : 'tamper-real-changed'}
            >
              <span
                aria-hidden
                className={cn(
                  'inline-flex h-14 w-14 shrink-0 items-center justify-center rounded-full border-2',
                  res.real_log_modified === false ? 'border-ok bg-ok/15 text-ok' : 'border-bad bg-bad/15 text-bad',
                )}
              >
                {res.real_log_modified === false ? <ShieldCheck className="h-8 w-8" /> : <AlertOctagon className="h-8 w-8" />}
              </span>
              <div className="min-w-0 space-y-1">
                <p className="text-display text-fg">{res.real_log_modified === false ? t('audit.tamper.safe') : t('audit.tamper.changed')}</p>
                <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-body text-muted tabular-nums">
                  {typeof res.records_copied === 'number' ? <span>{t('audit.tamper.safeSub', { n: res.records_copied.toLocaleString('en-SG') })}</span> : null}
                  {res.head_before ? (
                    <span className="inline-flex items-center gap-1">
                      {t('audit.tamper.headBefore')} <code className="rounded border border-line bg-raised px-1 font-mono text-xs text-fg">{res.head_before}</code>
                    </span>
                  ) : null}
                </p>
                {real && typeof res.records_copied === 'number' && real.records > res.records_copied ? (
                  <p className="text-caption text-muted" data-testid="tamper-counts-differ">
                    {t('audit.tamper.countsDiffer', { n: (real.records - res.records_copied).toLocaleString('en-SG') })}
                  </p>
                ) : null}
                {real ? (
                  <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-body-sm" data-testid="tamper-real-check">
                    <span className="text-muted">{t('audit.tamper.recheck')}</span>
                    <span className={cn('inline-flex items-center gap-1 font-semibold', real.ok ? 'text-ok' : 'text-bad')}>
                      {real.ok ? <CheckCircle2 aria-hidden className="h-4 w-4" /> : <XCircle aria-hidden className="h-4 w-4" />}
                      {real.ok ? t('audit.verify.intact') : t('audit.verify.problems')}
                    </span>
                    <span className="text-muted tabular-nums">· {t('audit.tamper.records', { n: real.records.toLocaleString('en-SG') })} ·</span>
                    <CodeHash value={real.head} head={8} tail={4} label="real head hash" />
                  </p>
                ) : (
                  <p className="text-body-sm text-muted">{t('audit.tamper.recheckFail')}</p>
                )}
              </div>
            </div>

            <div>
              <div aria-hidden className="hidden grid-cols-[minmax(0,1.5fr)_7rem_minmax(0,1.6fr)_9rem] gap-4 border-b border-line px-3 pb-2 text-micro font-semibold uppercase tracking-wide text-subtle lg:grid">
                <span>{t('audit.tamper.scenario')}</span>
                <span>{t('audit.tamper.expected')}</span>
                <span>{t('audit.tamper.actual')}</span>
                <span className="text-right">{t('audit.tamper.copyCol')}</span>
              </div>
              <ol aria-label={t('audit.tamper.caption')} className="divide-y divide-line">
                {res.scenarios.map((s, i) => {
                  const bad = detected(s);
                  const exp = asExpected(s);
                  return (
                    <li
                      key={s.id}
                      data-testid={`tamper-row-${s.id}`}
                      data-detected={bad}
                      style={{ transitionDelay: shown ? `${i * 110}ms` : '0ms' }}
                      className={cn(
                        'grid gap-x-4 gap-y-2 px-3 py-3 transition-[opacity,transform] duration-slow ease-out lg:grid-cols-[minmax(0,1.5fr)_7rem_minmax(0,1.6fr)_9rem] lg:items-start',
                        shown ? 'translate-y-0 opacity-100' : 'translate-y-1 opacity-0',
                        bad && 'bg-bad/[0.035] shadow-[inset_3px_0_0_rgb(var(--bad))]',
                      )}
                    >
                      <div className="min-w-0">
                        <p className="text-body-sm font-medium text-fg">
                          <span className="sr-only">{t('audit.tamper.scenario')}: </span>
                          {s.title}
                        </p>
                        {s.what_we_did ? <p className="mt-0.5 text-caption text-muted">{s.what_we_did}</p> : null}
                        <p className="mt-0.5 font-mono text-2xs text-subtle">{s.id}</p>
                      </div>
                      <div>
                        <span className="sr-only">{t('audit.tamper.expected')}: </span>
                        <Badge tone="neutral" title={s.expected}>
                          {s.expected_detected ? t('audit.tamper.expDetected') : t('audit.tamper.expIntact')}
                        </Badge>
                      </div>
                      <div className="min-w-0">
                        <span className="sr-only">{t('audit.tamper.actual')}: </span>
                        <Result s={s} />
                      </div>
                      <div className="flex items-center justify-between gap-3 lg:flex-col lg:items-end">
                        <ChainStrip s={s} />
                        {exp ? (
                          <Badge tone="ok" icon={CheckCircle2}>
                            {t('audit.tamper.asExpected')}
                          </Badge>
                        ) : (
                          <Badge tone="warn" icon={AlertOctagon}>
                            {t('audit.tamper.unexpected')}
                          </Badge>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ol>
            </div>
            <p className="text-2xs text-subtle tabular-nums">
              POST /audit/tamper-demo
              {typeof res.elapsed_ms === 'number' ? ` · ${res.elapsed_ms.toLocaleString('en-SG')} ms` : ''}
              {res.external_head_copied === false ? ` · ${t('audit.tamper.noExternal')}` : ''}
            </p>
          </div>
        ) : null}
      </div>
    </section>
  );
});
