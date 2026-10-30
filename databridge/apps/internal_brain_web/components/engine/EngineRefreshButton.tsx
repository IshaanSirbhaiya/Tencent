'use client';
// Demo-only engine refresh (D-24): rebuild the Walnut engine snapshot in the background and have the
// engine reload it. The browser only starts the job and reads its status (POST /sim/engine-refresh,
// /sim/engine-status through the IB); the IB asks the engine to reload SERVER-SIDE, so no engine
// token is ever in the browser. Shown only when demo features are on (server AND viewer).
//
// Accessibility (round-3 review H2, M9):
// - The live region carries only state and step changes ("Rebuilding… · Publishing", "done",
//   "failed"); the seconds counter ticks OUTSIDE it (aria-hidden), so a screen reader is not read a
//   new sentence every second.
// - While running the button is aria-disabled (not `disabled`), so keyboard focus stays on it.
// - A failed status poll keeps the last known state and retries with a back-off; after three misses
//   in a row it says so, but a running rebuild never silently vanishes.
import { Loader2, RefreshCw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { announce, Badge, Button } from '@/components/ui';
import { useDemoFeatures } from '@/components/sim/demo';
import { ApiError, liveApi } from '@/lib/api';
import { fmtTime } from '@/lib/format';
import { useMessages } from '@/lib/i18n';
import { graphMessages } from '@/lib/i18n/graph';
import type { EngineRefreshStatus } from '@/lib/types';

const POLL_MS = 2000;
const MAX_BACKOFF_MS = 10_000;
/** Status polls that may fail in a row before the line says it cannot read the status. */
export const MISSES_BEFORE_NOTICE = 3;

export function EngineRefreshButton() {
  const demo = useDemoFeatures();
  if (!demo.on) return null;
  return <Refresh />;
}

function Refresh() {
  const t = useMessages(graphMessages);
  const [st, setSt] = useState<EngineRefreshStatus | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [misses, setMisses] = useState(0);
  const [starting, setStarting] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const last = useRef<EngineRefreshStatus | null>(null);
  const missRef = useRef(0);
  const alive = useRef(true);

  const schedule = (ms: number) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void poll(), ms);
  };
  const poll = async () => {
    try {
      const s = await liveApi.engineStatus();
      if (!alive.current) return;
      missRef.current = 0;
      setMisses(0);
      last.current = s;
      setSt(s);
      if (s.state === 'running') schedule(POLL_MS);
    } catch {
      if (!alive.current) return;
      // keep the last known state; only a running rebuild is worth asking about again
      missRef.current += 1;
      setMisses(missRef.current);
      if (last.current?.state === 'running') schedule(Math.min(MAX_BACKOFF_MS, POLL_MS * 2 ** missRef.current));
    }
  };
  useEffect(() => {
    alive.current = true;
    void poll();
    const h = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      alive.current = false;
      clearInterval(h);
      if (timer.current) clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const running = st?.state === 'running';
  const locked = running || starting;

  const start = async () => {
    if (locked) return; // aria-disabled, not disabled: focus stays on the button
    setMsg(null);
    setStarting(true);
    try {
      const s = await liveApi.engineRefresh();
      missRef.current = 0;
      setMisses(0);
      last.current = s;
      setSt(s);
      schedule(POLL_MS);
    } catch (e) {
      const code = e instanceof ApiError ? e.status : 0;
      setMsg(code === 429 ? t('eng.tooSoon') : code === 404 ? t('eng.refused') : t('add.failed'));
    } finally {
      setStarting(false);
    }
  };

  const secs = running && st?.started_at ? Math.max(0, Math.round((now - Date.parse(st.started_at)) / 1000)) : 0;
  const step = st?.step === 'publishing' ? t('eng.publishing') : st?.step === 'reloading' ? t('eng.reloading') : t('eng.building');
  const lost = misses >= MISSES_BEFORE_NOTICE;
  useEffect(() => {
    if (lost) announce(t('eng.lost'));
  }, [lost, t]);
  return (
    <div className="flex flex-col items-end gap-1" data-testid="engine-refresh" data-state={st?.state ?? 'unknown'} data-misses={misses}>
      <div className="flex items-center gap-1.5">
        <Badge tone="warn">{t('demo.badge')}</Badge>
        <Button
          size="xs"
          variant="secondary"
          icon={locked ? undefined : RefreshCw}
          aria-disabled={locked || undefined}
          aria-busy={locked || undefined}
          onClick={() => void start()}
          title={t('eng.hint')}
          data-testid="engine-refresh-button"
        >
          {locked ? <Loader2 aria-hidden className="h-3.5 w-3.5 motion-safe:animate-spin" /> : null}
          {t('eng.refresh')}
        </Button>
      </div>
      <div className="flex items-baseline gap-1 text-2xs text-muted">
        {/* the live region: its text changes only when the state or the step changes */}
        <p role="status" data-testid="engine-refresh-status">
          {running ? (
            <>
              {t('eng.running')} · {step}
            </>
          ) : st?.state === 'done' && st.finished_at ? (
            <>
              {t('eng.done', { time: fmtTime(st.finished_at) })}{' '}
              <button
                type="button"
                className="rounded-sm text-accent underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                aria-label={t('eng.showLabel')}
                onClick={() => window.location.reload()}
              >
                {t('eng.show')}
              </button>
            </>
          ) : st?.state === 'failed' ? (
            t('eng.failed')
          ) : (
            msg ?? ''
          )}
        </p>
        {running ? (
          <span aria-hidden className="tabular-nums" data-testid="engine-refresh-timer">
            · {t('eng.secs', { s: secs })}
          </span>
        ) : null}
      </div>
      {lost ? (
        <p className="text-2xs text-muted" data-testid="engine-refresh-lost">
          {t('eng.lost')}
        </p>
      ) : null}
      {msg && (running || st?.state === 'done' || st?.state === 'failed') ? <p className="text-2xs text-muted">{msg}</p> : null}
    </div>
  );
}
