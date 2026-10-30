'use client';
// The three Demo Day slides plus the title card. Every number is read from a data file at view
// time: public/eval_summary.json (written by scripts/eval_summary.py), public/finals/value_defaults.json
// (the value calculator's defaults) and public/finals/fixlog.json / slides.json (claim lines that
// can be corrected without a rebuild). Nothing numeric is written in this file.
import { QRCodeSVG } from 'qrcode.react';
import { cn } from '@/lib/cn';
import { fmtDate } from '@/lib/format';
import { useEffect, useState } from 'react';
import { analyticsApi } from '@/lib/api';
import { useMessages } from '@/lib/i18n';
import { presenterMessages } from '@/lib/i18n/presenter';
import type { EvalSummary } from '@/lib/types';
import { BIG, SlideFrame, usePublicJson } from './SlideFrame';
import { computeValue, minutesRounded, readInputs, usdCompact, usdExact, usdRounded } from './value';

export const REPO_URL = 'https://github.com/IshaanSirbhaiya/Tencent';
const REPO_TEXT = REPO_URL.replace(/^https:\/\//, '');

const n = (v: number) => v.toLocaleString('en-SG');
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/**
 * Is Hy3 through TokenHub live right now (GET /ib/status, read at view time)? null while unknown or
 * unreachable: lines that name Hy3 or TokenHub are shown only on a definite yes. Any other active
 * model (the stand-in, a bring-your-own endpoint) is a no: those lines would then be untrue.
 */
function useModelLive(): boolean | null {
  const [live, setLive] = useState<boolean | null>(null);
  useEffect(() => {
    let on = true;
    analyticsApi
      .status()
      .then((s) => on && setLive(s?.models?.active === true && s.models.active_provider === 'tencent-tokenhub'))
      .catch(() => on && setLive(null));
    return () => {
      on = false;
    };
  }, []);
  return live;
}

/** A slides.json line, or its `_live` variant while the model is live. */
function claimLine(data: Record<string, unknown> | null, key: string, live: boolean | null): string {
  if (!data) return '';
  const pick = live === true && typeof data[`${key}_live`] === 'string' ? data[`${key}_live`] : data[key];
  return typeof pick === 'string' ? pick.trim() : '';
}

/* ------------------------------------------------------------------ title (step 0) */

export function TitleSlide() {
  const t = useMessages(presenterMessages);
  return (
    <SlideFrame eyebrow={t('sl.title.eyebrow')} footer={t('sl.team')}>
      <h1 className="text-[clamp(3rem,7vw,6.5rem)] font-semibold leading-[1.02] tracking-[-0.035em]">{t('sl.title.name')}</h1>
      <p className="mt-[3vh] max-w-[24ch] text-[clamp(1.5rem,3vw,2.75rem)] leading-tight tracking-[-0.015em] text-muted">{t('sl.title.hook')}</p>
    </SlideFrame>
  );
}

/* ------------------------------------------------------------------ proof */

function Figure({ value, word, of, source, testId }: { value: number | null; word: string; of: string | null; source: string | null; testId: string }) {
  const t = useMessages(presenterMessages);
  return (
    <div className="flex min-w-0 flex-col border-t border-line-strong pt-[3vh]" data-testid={testId}>
      {value === null ? (
        <>
          <p className="text-[clamp(1.75rem,3vw,2.75rem)] font-semibold leading-tight text-muted">{t('sl.proof.pending')}</p>
          <p className="mt-3 text-body text-muted">{t('sl.proof.pendingOf')}</p>
        </>
      ) : (
        <>
          <p className="flex items-baseline gap-3">
            <span className={BIG}>{n(value)}</span>
            <span className="text-[clamp(1.25rem,2.2vw,2rem)] font-medium text-fg">{word}</span>
          </p>
          {of ? <p className="mt-3 text-[clamp(1rem,1.6vw,1.5rem)] leading-snug text-muted">{of}</p> : null}
        </>
      )}
      {source ? <p className="mt-auto min-w-0 break-all pt-4 font-mono text-micro text-subtle">{source}</p> : null}
    </div>
  );
}

export function ProofSlide() {
  const t = useMessages(presenterMessages);
  const ev = usePublicJson<EvalSummary>('/eval_summary.json');
  const fix = usePublicJson<{ tester_lines?: unknown }>('/finals/fixlog.json');
  const lines = usePublicJson<Record<string, unknown>>('/finals/slides.json');
  const live = useModelLive();
  const e = ev.state === 'ok' ? ev.data : null;
  const src = e?.source && e.run_date ? t('sl.proof.source', { file: e.source, date: fmtDate(e.run_date) }) : e?.source ?? null;
  const tester = fix.state === 'ok' && Array.isArray(fix.data.tester_lines) ? (fix.data.tester_lines.find((x) => typeof x === 'string' && x.trim()) as string | undefined) : undefined;
  const cb = claimLine(lines.state === 'ok' ? lines.data : null, 'codebuddy_line', live);
  const smoke = e?.smoke as (EvalSummary['smoke'] & { differential?: { pairs?: number; mismatches?: number } }) | null | undefined;

  const leaks = isNum(e?.leaks) && isNum(e?.requests) ? e!.leaks! : null;
  const diffs = isNum(e?.differential?.mismatches) && isNum(e?.differential?.pairs) ? e!.differential!.mismatches! : null;
  const inj = isNum(e?.injection?.hard_failures) && isNum(e?.injection?.requests) ? e!.injection!.hard_failures! : null;

  return (
    <SlideFrame eyebrow={t('sl.proof.eyebrow')}>
      <h1 className="max-w-[28ch] text-[clamp(1.75rem,3.4vw,3rem)] font-semibold leading-tight tracking-[-0.02em]">{t('sl.proof.title')}</h1>
      {ev.state === 'loading' ? (
        <div aria-hidden className="mt-[5vh] h-[30vh]" />
      ) : (
        <div className="mt-[5vh] grid gap-x-[4vw] gap-y-8 md:grid-cols-3" data-testid="proof-figures">
          <Figure testId="proof-leaks" value={leaks} word={t('sl.proof.leaks')} of={leaks === null ? null : t('sl.proof.leaksOf', { n: n(e!.requests!) })} source={leaks === null ? null : src} />
          <Figure
            testId="proof-diffs"
            value={diffs}
            word={t('sl.proof.diffs')}
            of={diffs === null ? null : t('sl.proof.diffsOf', { n: n(e!.differential!.pairs!) })}
            source={diffs === null ? null : src}
          />
          <Figure
            testId="proof-injection"
            value={inj}
            word={t('sl.proof.inj')}
            of={inj === null ? null : t('sl.proof.injOf', { n: n(e!.injection!.requests!) })}
            source={inj === null ? null : src}
          />
        </div>
      )}
      <div className="mt-[5vh] max-w-5xl space-y-3 text-[clamp(1rem,1.5vw,1.375rem)] leading-snug">
        {e?.stale && e.note ? (
          <p className="text-muted" data-testid="proof-note">
            {e.note}
            {smoke && isNum(smoke.leaks) && isNum(smoke.requests) && smoke.run_date && isNum(smoke.differential?.mismatches) && isNum(smoke.differential?.pairs)
              ? ` ${t('sl.proof.smoke', {
                  date: fmtDate(smoke.run_date),
                  leaks: n(smoke.leaks),
                  n: n(smoke.requests),
                  diffs: n(smoke.differential!.mismatches!),
                  pairs: n(smoke.differential!.pairs!),
                })}`
              : ''}
          </p>
        ) : null}
        {tester ? (
          <p data-testid="proof-tester">
            <span className="mr-3 text-micro font-semibold uppercase tracking-wider text-subtle">{t('sl.proof.testers')}</span>
            {tester}
          </p>
        ) : null}
        {cb ? (
          <p data-testid="proof-codebuddy">
            <span className="mr-3 text-micro font-semibold uppercase tracking-wider text-subtle">{t('sl.proof.codebuddy')}</span>
            {cb}
          </p>
        ) : null}
      </div>
    </SlideFrame>
  );
}

/* ------------------------------------------------------------------ business */

export function BusinessSlide() {
  const t = useMessages(presenterMessages);
  const file = usePublicJson<{ status_label?: unknown; source?: unknown }>('/finals/value_defaults.json');
  const lines = usePublicJson<Record<string, unknown>>('/finals/slides.json');
  const live = useModelLive();
  const inputs = file.state === 'ok' ? readInputs(file.data) : null;
  const out = inputs ? computeValue(inputs) : null;
  const stack = claimLine(lines.state === 'ok' ? lines.data : null, 'stack_line', live);
  const status = file.state === 'ok' && typeof file.data.status_label === 'string' ? file.data.status_label : '';

  return (
    <SlideFrame
      eyebrow={t('sl.biz.eyebrow')}
      footer={inputs ? <span className="font-mono">{String(file.state === 'ok' ? file.data.source ?? '' : '')}</span> : null}
    >
      {file.state === 'loading' ? (
        <div aria-hidden className="h-[40vh]" />
      ) : !inputs || !out ? (
        <>
          <h1 className="text-[clamp(1.75rem,3.4vw,3rem)] font-semibold">{t('sl.biz.eyebrow')}</h1>
          <p className="mt-4 text-body text-muted" data-testid="business-missing">
            {t('sl.biz.pending')}
          </p>
        </>
      ) : (
        <>
          <h1 className="sr-only">{t('sl.biz.eyebrow')}</h1>
          <div className="grid gap-x-[5vw] gap-y-10 md:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
            <div className="min-w-0 border-t border-line-strong pt-[3vh]" data-testid="business-value">
              <p className="flex min-h-[2.5em] flex-wrap items-center gap-3 text-[clamp(1rem,1.6vw,1.5rem)] text-muted">
                {t('sl.biz.base')}
                <span className="rounded-full border border-line-strong px-3 py-0.5 text-[0.75em] font-semibold uppercase tracking-wider text-fg" data-testid="business-estimate">
                  {t('sl.biz.estimate')}
                </span>
              </p>
              {/* Big: the figure that is spoken. Small: the exact computed value. */}
              <p className={cn(BIG, 'mt-3 whitespace-nowrap')} data-testid="business-value-rounded">
                ≈&nbsp;{usdRounded(out.annual_value_usd)}
              </p>
              <p className="mt-1 text-[clamp(1rem,1.5vw,1.375rem)] leading-snug text-muted">{t('sl.biz.perYear')}</p>
              <p className="mt-2 font-mono text-caption tabular-nums text-muted" data-testid="business-value-exact">
                {t('sl.biz.exact', { v: usdExact(out.annual_value_usd), c: usdCompact(out.annual_value_usd) })}
              </p>
            </div>
            <div className="min-w-0 border-t border-line-strong pt-[3vh]" data-testid="business-break-even">
              <p className="flex min-h-[2.5em] items-center text-[clamp(1rem,1.6vw,1.5rem)] text-muted">{t('sl.biz.breakEven')}</p>
              <p className={cn(BIG, 'mt-3 whitespace-nowrap')} data-testid="business-break-even-rounded">
                <span className="mr-[0.25em] text-[0.4em] font-medium tracking-normal text-muted">{t('sl.biz.about')}</span>
                {minutesRounded(out.break_even_minutes_per_week)}
              </p>
              <p className="mt-1 text-[clamp(1rem,1.5vw,1.375rem)] leading-snug text-muted">{t('sl.biz.breakEvenUnit')}</p>
              <p className="mt-2 font-mono text-caption tabular-nums text-muted" data-testid="business-break-even-exact">
                {t('sl.biz.exactMin', { v: out.break_even_minutes_per_week.toFixed(1) })}
              </p>
            </div>
          </div>
          <div className="mt-[5vh]">
            <h2 className="text-micro font-semibold uppercase tracking-wider text-subtle">{t('sl.biz.inputs')}</h2>
            <ul className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-[clamp(1rem,1.5vw,1.375rem)] tabular-nums text-fg" data-testid="business-inputs">
              <li>{t('sl.biz.staff', { n: n(inputs.staff) })}</li>
              <li>{t('sl.biz.minutes', { n: n(inputs.minutes_saved_per_week) })}</li>
              <li>{t('sl.biz.cost', { n: n(inputs.hourly_cost_usd) })}</li>
              <li>{t('sl.biz.weeks', { n: n(inputs.working_weeks) })}</li>
              <li>{t('sl.biz.price', { n: n(inputs.annual_price_usd) })}</li>
            </ul>
            <p className="mt-3 max-w-5xl text-caption text-muted">
              {t('sl.biz.formula')} {status}
            </p>
            {stack ? (
              <p className="mt-4 text-[clamp(1rem,1.5vw,1.375rem)] text-fg" data-testid="business-stack">
                {stack}
              </p>
            ) : null}
          </div>
        </>
      )}
    </SlideFrame>
  );
}

/* ------------------------------------------------------------------ close */

export function CloseSlide() {
  const t = useMessages(presenterMessages);
  return (
    <SlideFrame footer={t('sl.team')}>
      <div className="grid items-center gap-x-[6vw] gap-y-10 md:grid-cols-[minmax(0,1fr)_auto]">
        <div className="min-w-0">
          <h1 className="text-[clamp(3rem,7.5vw,7rem)] font-semibold leading-[1.02] tracking-[-0.035em]" data-testid="close-tagline">
            {t('sl.close.tagline')}
          </h1>
          <p className="mt-[4vh] text-[clamp(1.25rem,2.4vw,2.25rem)] leading-tight text-muted" data-testid="close-ask">
            {t('sl.close.ask')}
          </p>
        </div>
        {/* Forced light so the code is dark-on-white in either theme (scannable from 3 m). */}
        <figure data-theme-scope="light" className="flex flex-col items-center gap-3 rounded-xl border border-line bg-surface p-[clamp(1rem,2vw,1.75rem)] text-fg">
          <QRCodeSVG
            value={REPO_URL}
            size={240}
            level="M"
            marginSize={0}
            fgColor="currentColor"
            bgColor="transparent"
            role="img"
            aria-label={t('sl.close.qr', { url: REPO_TEXT })}
            className="h-[clamp(9rem,22vw,15rem)] w-[clamp(9rem,22vw,15rem)]"
            data-testid="close-qr"
          />
          <figcaption className="text-center">
            <span className="block text-body-sm font-semibold">{t('sl.close.scan')}</span>
            <span className="block font-mono text-caption text-muted">{REPO_TEXT}</span>
          </figcaption>
        </figure>
      </div>
    </SlideFrame>
  );
}
