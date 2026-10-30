'use client';
// The Demo Day preflight board (/present/preflight). Large green/red rows a presenter can read from
// a step back. Read-only: it re-checks what the browser can see (screen, zoom, clock, clips, live
// model status) and shows the last report of scripts/demo_preflight.py, which the one-click Start
// writes after its pre-warm. Every status is icon + word + colour, never colour alone.
import { AlertTriangle, CheckCircle2, Info, RefreshCw, XCircle } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { budgetLine } from '@/lib/budget';
import { cn } from '@/lib/cn';
import { translate } from '@/lib/i18n';
import { budgetMessages } from '@/lib/i18n/budget';
import { hunyuanLive } from '@/lib/modelLive';
import type { ModelBudget } from '@/lib/types';

export type Status = 'pass' | 'fail' | 'warn' | 'info';
export interface ServerCheck {
  key: string;
  label: string;
  status: Status;
  detail: string;
}
export interface ScriptItem extends ServerCheck {
  group: string;
}
export interface ScriptReport {
  generated_at: string;
  mode: 'live' | 'offline';
  web: string;
  ok: boolean;
  max_s: number;
  warmed: boolean;
  model?: { live?: boolean; chain?: string };
  items: ScriptItem[];
}

const CLIPS = ['clipA_compare_amy_rachel_ken.mp4', 'clipB_journey_audit_tamper.mp4', 'clipC_workbuddy_jordan.mp4'];
const STALE_MIN = 45;

const WORD: Record<Status, string> = { pass: 'OK', fail: 'FIX', warn: 'CHECK', info: 'NOTE' };
const TONE: Record<Status, string> = { pass: 'text-ok', fail: 'text-bad', warn: 'text-warn', info: 'text-muted' };
const ICON: Record<Status, typeof CheckCircle2> = { pass: CheckCircle2, fail: XCircle, warn: AlertTriangle, info: Info };

function Row({ c }: { c: ServerCheck }) {
  const Icon = ICON[c.status];
  return (
    <li className="flex items-start gap-3 border-t border-line py-3 first:border-t-0 sm:gap-4" data-testid={`pf-${c.key}`} data-status={c.status}>
      <Icon aria-hidden className={cn('mt-1 h-6 w-6 shrink-0 sm:h-8 sm:w-8', TONE[c.status])} />
      <span className={cn('w-[4.25rem] shrink-0 pt-0.5 text-[1.125rem] font-bold tracking-wide sm:w-[5.5rem] sm:text-[1.5rem]', TONE[c.status])}>{WORD[c.status]}</span>
      <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">
        <span className="block text-[1.125rem] font-semibold leading-snug text-fg sm:text-[1.5rem]">{c.label}</span>
        {c.detail ? <span className="block text-[1rem] leading-snug text-muted sm:text-[1.125rem]">{c.detail}</span> : null}
      </span>
    </li>
  );
}

function Section({ title, note, checks }: { title: string; note?: string; checks: ServerCheck[] }) {
  return (
    <section className="min-w-0 rounded-lg border border-line bg-surface px-4 py-4 sm:px-6">
      <h2 className="text-[1.375rem] font-semibold tracking-[-0.01em] text-fg sm:text-[1.75rem]">{title}</h2>
      {note ? <p className="text-[1rem] text-muted [overflow-wrap:anywhere] sm:text-[1.0625rem]">{note}</p> : null}
      <ul className="mt-2">{checks.map((c) => <Row key={c.key} c={c} />)}</ul>
    </section>
  );
}

const pad = (n: number) => String(n).padStart(2, '0');
const clock = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;

export function PreflightBoard({ services, report, serverNow, mock }: { services: ServerCheck[]; report: ScriptReport | null; serverNow: number; mock: boolean }) {
  const router = useRouter();
  const [now, setNow] = useState<Date | null>(null);
  const [skewMs] = useState(() => (typeof window === 'undefined' ? 0 : Date.now() - serverNow));
  const [local, setLocal] = useState<ServerCheck[]>([]);
  const [modelLive, setModelLive] = useState<ServerCheck | null>(null);
  const [budgetLive, setBudgetLive] = useState<ServerCheck | null>(null);
  const [checkedAt, setCheckedAt] = useState<Date | null>(null);

  const runClientChecks = useCallback(async () => {
    const out: ServerCheck[] = [];
    // Screen: physical pixels = CSS pixels x devicePixelRatio. Ratio 1 = Windows scaling 100% and browser zoom 100%.
    const dpr = window.devicePixelRatio || 1;
    const w = Math.round(window.screen.width * dpr);
    const h = Math.round(window.screen.height * dpr);
    out.push({
      key: 'screen',
      label: `Screen ${w} x ${h}`,
      status: w >= 1920 && h >= 1080 ? 'pass' : 'fail',
      detail: w >= 1920 && h >= 1080 ? '1920 x 1080 or better' : 'below 1920 x 1080: Settings > System > Display > Display resolution',
    });
    out.push({
      key: 'zoom',
      label: `Scale and zoom ${Math.round(dpr * 100)} %`,
      status: Math.abs(dpr - 1) < 0.01 ? 'pass' : 'warn',
      detail:
        Math.abs(dpr - 1) < 0.01
          ? `window ${window.innerWidth} x ${window.innerHeight} CSS px`
          : 'expected 100 %: press Ctrl+0 for browser zoom, or set Windows scaling to 100 % (the Start window forces it)',
    });
    // Clock: time zone and agreement with the web server's clock.
    const off = -new Date().getTimezoneOffset() / 60;
    out.push({
      key: 'tz',
      label: `Time zone UTC${off >= 0 ? '+' : ''}${off}`,
      status: off === 8 ? 'pass' : 'warn',
      detail: off === 8 ? 'Singapore time' : 'Singapore is UTC+8: Settings > Time & language > Date & time',
    });
    out.push({
      key: 'skew',
      label: 'Browser and server clocks agree',
      status: Math.abs(skewMs) < 5_000 ? 'pass' : 'warn',
      detail: `difference ${(skewMs / 1000).toFixed(1)} s (includes page load time)`,
    });
    // Clips: served by this web app (files added after the build are not served until a rebuild).
    const clipChecks = await Promise.all(
      CLIPS.map(async (name): Promise<ServerCheck> => {
        try {
          const r = await fetch(`/clips/${name}`, { method: 'HEAD', cache: 'no-store' });
          const mb = Number(r.headers.get('content-length') || 0) / 1e6;
          return { key: `clip-${name[4]}`, label: `Backup clip ${name[4]} (key B)`, status: r.ok ? 'pass' : 'fail', detail: r.ok ? `${name}, ${mb.toFixed(1)} MB` : `${name}: HTTP ${r.status}; rebuild (Stop, then Start)` };
        } catch {
          return { key: `clip-${name[4]}`, label: `Backup clip ${name[4]} (key B)`, status: 'fail', detail: `${name}: not reachable` };
        }
      }),
    );
    out.push(...clipChecks);
    setLocal(out);

    // Live model status, when this browser is signed in (the presenter signs in on P).
    try {
      const r = await fetch('/api/ib/ib/status', { cache: 'no-store', headers: { accept: 'application/json' } });
      if (r.ok) {
        const s = (await r.json()) as { models?: { active?: boolean; active_provider?: string | null; active_chain?: string[]; primary_live?: boolean | null }; ledger?: { ok?: boolean | null }; model_budget?: ModelBudget };
        const live = !!s.models?.active;
        // Hy3 is named only when the active chain IS TokenHub AND IB_TOKENHUB_LIVE=1; else neutral
        const hy3 = hunyuanLive(s.models);
        setModelLive({
          key: 'model-now',
          label: mock ? 'Model now: mock fixtures' : hy3 ? 'Model now: Tencent Hunyuan live' : live ? 'Model now: AI model active' : 'Model now: quoted from sources (no model)',
          status: mock ? 'info' : live ? 'pass' : 'warn',
          detail: mock
            ? 'offline build'
            : hy3
              ? (s.models?.active_chain ?? []).join(', ')
              : live
                ? 'an AI model answers from the cited sources; never claim Hy3'
                : 'answers are quoted from the sources; say so, never claim Hy3',
        });
        // the stand-in model's budget (numbers only; /ib/status sends it to admin and compliance)
        const b = s.model_budget;
        setBudgetLive(!mock && b ? { key: 'model-budget-now', ...budgetLine(b, (k, v) => translate(budgetMessages, 'en', k, v)) } : null);
      } else {
        setBudgetLive(null);
        setModelLive({ key: 'model-now', label: 'Model now: sign in to see', status: 'info', detail: 'this browser is not signed in; the Start script result is above' });
      }
    } catch {
      setModelLive({ key: 'model-now', label: 'Model now: not reachable', status: 'fail', detail: 'the web app could not reach the Internal Brain' });
    }
    setCheckedAt(new Date());
  }, [mock, skewMs]);

  const recheck = useCallback(() => {
    router.refresh();
    void runClientChecks();
  }, [router, runClientChecks]);

  useEffect(() => {
    setNow(new Date());
    void runClientChecks();
    const tick = setInterval(() => setNow(new Date()), 1000);
    const again = setInterval(recheck, 20_000);
    return () => {
      clearInterval(tick);
      clearInterval(again);
    };
  }, [runClientChecks, recheck]);

  const webApp: ServerCheck = { key: 'web', label: 'Web app (this page)', status: 'pass', detail: 'this page loaded from the web app' };
  const serviceChecks = [webApp, ...services];

  // The script's own model, ledger, history and pre-warm results. Services, clips and clock are re-checked live above.
  const ageMin = report ? (Date.now() - new Date(report.generated_at).getTime()) / 60_000 : null;
  const scriptChecks: ServerCheck[] = report
    ? report.items.filter((i) => i.group === 'model' || i.group === 'ledger')
    : [{ key: 'no-report', label: 'No Start report yet', status: 'warn', detail: 'double-click "Start Walnut Demo" (it writes the model, ledger and pre-warm results here)' }];
  if (modelLive) scriptChecks.push(modelLive);
  if (budgetLive) {
    // the live numbers replace the Start script's (older) model budget line
    const i = scriptChecks.findIndex((c) => c.key === 'model_budget');
    if (i >= 0) scriptChecks.splice(i, 1);
    scriptChecks.push(budgetLive);
  }
  const warmChecks: ServerCheck[] = report?.warmed
    ? report.items.filter((i) => i.group === 'warm')
    : [{ key: 'no-warm', label: 'Scripted questions not pre-warmed yet', status: 'warn', detail: 'the one-click Start asks each one once; run it before going on stage' }];
  if (report && ageMin !== null && ageMin > STALE_MIN)
    scriptChecks.unshift({ key: 'stale', label: `Start report is ${Math.round(ageMin)} minutes old`, status: 'warn', detail: 'run Start again (or Reset) so the first ask on stage is warm' });
  if (report && report.mode !== (mock ? 'offline' : 'live'))
    scriptChecks.unshift({ key: 'report-mode', label: `Start report is for the ${report.mode} build`, status: 'warn', detail: 'it does not describe this server; run the matching Start' });

  const all = [...serviceChecks, ...scriptChecks, ...warmChecks, ...local];
  const fails = all.filter((c) => c.status === 'fail').length;
  const warns = all.filter((c) => c.status === 'warn').length;
  const ready = fails === 0 && local.length > 0;

  return (
    // lang="en": the board is English-only (a backstage tool), so a zh-Hans page still reads it as English.
    <div lang="en" className="min-h-dvh bg-bg px-4 pb-40 pt-[3vh] text-fg sm:px-[4vw]">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <p className="text-[1rem] font-semibold uppercase tracking-[0.14em] text-subtle sm:text-[1.125rem]">Walnut DataBridge · Demo Day preflight</p>
          <h1
            className={cn('mt-1 text-[clamp(3rem,6vw,5.5rem)] font-bold leading-none tracking-[-0.03em]', local.length === 0 ? 'text-muted' : ready ? 'text-ok' : 'text-bad')}
            data-testid="pf-verdict"
            aria-live="polite"
          >
            {local.length === 0 ? 'Checking…' : ready ? 'READY' : `NOT READY: ${fails} to fix`}
          </h1>
          <p className="mt-2 text-[1.25rem] text-muted">
            {warns ? `${warns} to check (amber is allowed on stage when you know why). ` : ''}
            {mock ? 'Offline mock build on this port.' : 'Live build.'}
          </p>
        </div>
        <div className="text-right">
          <p className="font-mono text-[clamp(2.5rem,5vw,4.5rem)] font-semibold leading-none tabular-nums" aria-label="Laptop clock">
            {now ? clock(now) : '--:--:--'}
          </p>
          <p className="mt-1 text-[1.125rem] text-muted">
            {now ? now.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }) : ''}
            {checkedAt ? ` · checked ${clock(checkedAt)}` : ''}
          </p>
          <button
            type="button"
            onClick={recheck}
            className="mt-3 inline-flex items-center gap-2 rounded-md border border-line-strong bg-raised px-4 py-2 text-[1.125rem] font-medium text-fg hover:bg-overlay focus-visible:outline focus-visible:outline-2 focus-visible:outline-focus"
          >
            <RefreshCw aria-hidden className="h-5 w-5" /> Re-check
          </button>
        </div>
      </header>

      <div className="mt-6 grid grid-cols-1 gap-5 xl:grid-cols-2">
        <Section title="Services" note="Checked now by this server." checks={serviceChecks} />
        <Section
          title="Model and audit chain"
          note={report ? `From the Start script, as maya.compliance, ${Math.max(0, Math.round(ageMin ?? 0))} min ago.` : 'From the Start script.'}
          checks={scriptChecks}
        />
        <Section
          title="Scripted questions (pre-warm)"
          note={report?.warmed ? `Each asked once by Start, as the persona on stage; OK means the expected result in under ${report.max_s} s.` : undefined}
          checks={warmChecks}
        />
        <Section title="This screen and laptop" note="Checked now by this browser." checks={local.length ? local : [{ key: 'pending', label: 'Checking…', status: 'info', detail: '' }]} />
      </div>

      <p className="mt-6 text-[1.125rem] text-muted [overflow-wrap:anywhere]">
        Next: open <span className="font-mono text-fg">/present?present=1</span>, press <b>P</b>, then <b>Space</b> for each step. <b>B</b> plays the backup clip, <b>R</b> resets. Runbook:{' '}
        <span className="font-mono">docs/finals/demo_day/DEMO_DAY_RUNBOOK.md</span>
      </p>
    </div>
  );
}
