'use client';
// Verify chain: re-checks the live ledger (GET /audit/verify) and draws the result as a chain:
// genesis → every record → the head → the head published off-host. The values (record count, head
// hash, external match) come straight from the backend; the links between them are decoration.
import { CheckCircle2, CircleDashed, Link2, ShieldCheck, XCircle } from 'lucide-react';
import { forwardRef, useCallback, useEffect, useRef, useState } from 'react';
import { Badge, Button, CodeHash } from '@/components/ui';
import { api } from '@/lib/api';
import { cn } from '@/lib/cn';
import { fmtTime } from '@/lib/format';
import { useMessages } from '@/lib/i18n';
import { auditMessages } from '@/lib/i18n/audit';
import { useTourAction } from '@/lib/tour';
import type { VerifyResult } from '@/lib/types';

type State = { kind: 'idle' } | { kind: 'running' } | { kind: 'done'; res: VerifyResult; at: string } | { kind: 'error' };

const LINKS = 10;

/** A connector of small links that light up one after another when the chain verifies. */
function Links({ state, count = LINKS, className }: { state: 'idle' | 'running' | 'ok' | 'bad'; count?: number; className?: string }) {
  return (
    <span aria-hidden className={cn('flex min-w-0 flex-1 items-center gap-1 px-1', className)}>
      {Array.from({ length: count }, (_, i) => (
        <span
          key={i}
          style={{ transitionDelay: state === 'ok' ? `${i * 45}ms` : '0ms' }}
          className={cn(
            'h-1.5 min-w-[4px] flex-1 rounded-full transition-colors duration-slow ease-out',
            state === 'ok' ? 'bg-ok' : state === 'bad' ? 'bg-bad/70' : state === 'running' ? 'bg-accent/40 motion-safe:animate-pulse' : 'bg-line-strong',
          )}
        />
      ))}
    </span>
  );
}

function Block({ label, children, tone, className }: { label: string; children: React.ReactNode; tone: 'idle' | 'ok' | 'bad'; className?: string }) {
  return (
    <dl
      className={cn(
        'min-w-0 rounded-lg border px-3 py-2 transition-colors duration-slow',
        tone === 'ok' ? 'border-ok/50 bg-ok/[0.06]' : tone === 'bad' ? 'border-bad/50 bg-bad/[0.06]' : 'border-line bg-raised/60',
        className,
      )}
    >
      <dt className="text-micro font-semibold uppercase tracking-wide text-subtle">{label}</dt>
      <dd className="mt-1">{children}</dd>
    </dl>
  );
}

export const VerifyPanel = forwardRef<HTMLElement, { autoRun?: boolean }>(function VerifyPanel({ autoRun }, ref) {
  const t = useMessages(auditMessages);
  const [state, setState] = useState<State>({ kind: 'idle' });

  const run = useCallback(async () => {
    setState({ kind: 'running' });
    try {
      const res = await api.verify();
      setState({ kind: 'done', res, at: new Date().toISOString() });
    } catch {
      setState({ kind: 'error' });
    }
  }, []);

  useTourAction('verify', () => void run());
  const started = useRef(false);
  useEffect(() => {
    if (autoRun && !started.current) {
      started.current = true;
      void run();
    }
  }, [autoRun, run]);

  const res = state.kind === 'done' ? state.res : null;
  const tone: 'idle' | 'ok' | 'bad' = res ? (res.ok ? 'ok' : 'bad') : 'idle';
  const linkState = state.kind === 'running' ? 'running' : tone === 'ok' ? 'ok' : tone === 'bad' ? 'bad' : 'idle';

  return (
    <section
      ref={ref}
      id="verify"
      aria-labelledby="verify-heading"
      className="scroll-mt-20 rounded-xl border border-line bg-surface"
      data-testid="verify-panel"
      data-tour="verify-panel"
      data-state={state.kind === 'done' ? (res?.ok ? 'ok' : 'bad') : state.kind}
    >
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <h2 id="verify-heading" className="flex items-center gap-2 text-heading-sm text-fg">
            <Link2 aria-hidden className="h-4 w-4 text-muted" />
            {t('audit.verify.heading')}
          </h2>
          <p className="mt-0.5 max-w-2xl text-caption text-muted">{t('audit.verify.sub')}</p>
        </div>
        <Button variant="primary" size="lg" onClick={() => void run()} busy={state.kind === 'running'} icon={ShieldCheck}>
          {t('audit.verify.button')}
        </Button>
      </div>

      <div className="space-y-4 px-4 py-4">
        <div aria-live="polite" className="flex min-h-[3rem] items-center gap-3">
          <span
            aria-hidden
            className={cn(
              'inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full border-2 transition-colors duration-slow',
              tone === 'ok' ? 'border-ok bg-ok/10 text-ok' : tone === 'bad' ? 'border-bad bg-bad/10 text-bad' : 'border-dashed border-line-strong text-subtle',
            )}
          >
            {tone === 'ok' ? <CheckCircle2 className="h-6 w-6" /> : tone === 'bad' ? <XCircle className="h-6 w-6" /> : <CircleDashed className="h-5 w-5" />}
          </span>
          {state.kind === 'error' ? (
            <p role="alert" className="text-body text-fg">
              {t('audit.verify.error')}
            </p>
          ) : state.kind === 'running' ? (
            <p className="text-body text-muted">{t('audit.verify.running')}</p>
          ) : res ? (
            <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1" data-testid="verify-status">
              <span className={cn('text-heading font-semibold', res.ok ? 'text-ok' : 'text-bad')}>{res.ok ? t('audit.verify.intact') : t('audit.verify.problems')}</span>
              <span className="text-caption text-muted tabular-nums">{t('audit.verify.checked', { time: fmtTime(state.kind === 'done' ? state.at : null) })}</span>
            </p>
          ) : (
            <p className="text-body text-muted">{t('audit.verify.idle')}</p>
          )}
        </div>

        {/* The chain: genesis ─ records ─ head ═ external head */}
        <div className="flex flex-col items-stretch gap-2 md:flex-row md:items-center md:gap-0">
          <div aria-hidden className="hidden shrink-0 items-center gap-1.5 rounded-full border border-line px-2.5 py-1 text-micro font-semibold uppercase tracking-wide text-subtle md:flex">
            {t('audit.verify.genesis')}
          </div>
          <Links state={linkState} className="hidden md:flex" />
          <Block label={t('audit.verify.records')} tone={tone} className="md:w-36 md:shrink-0">
            <span className="text-kpi tabular-nums text-fg" data-testid="verify-records">
              {res ? res.records.toLocaleString('en-SG') : '—'}
            </span>
          </Block>
          <Links state={linkState} count={4} className="hidden md:flex md:max-w-[5rem]" />
          <Block label={t('audit.verify.head')} tone={tone} className="md:w-64 md:shrink-0">
            {res?.head ? <CodeHash value={res.head} head={10} tail={6} label="head hash" /> : <span className="font-mono text-xs text-subtle">—</span>}
          </Block>
          <span aria-hidden className={cn('mx-1 hidden h-0 w-8 shrink-0 border-t-2 border-double transition-colors duration-slow md:block', tone === 'ok' ? 'border-ok' : tone === 'bad' ? 'border-bad' : 'border-line-strong')} />
          <Block label={t('audit.verify.external')} tone={res ? (res.external_head_match === false ? 'bad' : res.external_head_match ? 'ok' : 'idle') : 'idle'} className="md:w-56 md:shrink-0">
            {!res ? (
              <span className="text-caption text-subtle">{t('audit.verify.published')}</span>
            ) : res.external_head_match === true ? (
              <Badge tone="ok" icon={CheckCircle2}>
                {t('audit.verify.match')}
              </Badge>
            ) : res.external_head_match === false ? (
              <Badge tone="bad" icon={XCircle}>
                {t('audit.verify.noMatch')}
              </Badge>
            ) : (
              <Badge icon={CircleDashed}>{t('audit.verify.noExternal')}</Badge>
            )}
          </Block>
        </div>

        {res && res.problems.length ? (
          <div className="rounded-lg border border-bad/40 bg-bad/[0.06] p-3">
            <h3 className="text-xs font-semibold text-bad">{t('audit.verify.problems')}</h3>
            <ul className="mt-1 list-inside list-disc space-y-0.5 text-xs text-fg">
              {res.problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </section>
  );
});
