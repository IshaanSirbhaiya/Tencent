'use client';
// Demo-only engine refresh (D-24): rebuild the Walnut engine snapshot in the background and have the
// engine reload it. The browser only starts the job and reads its status (POST /sim/engine-refresh,
// /sim/engine-status through the IB); the IB asks the engine to reload SERVER-SIDE, so no engine
// token is ever in the browser. Shown only when demo features are on (server AND viewer).
import { RefreshCw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Badge, Button } from '@/components/ui';
import { useDemoFeatures } from '@/components/sim/demo';
import { ApiError, liveApi } from '@/lib/api';
import { fmtTime } from '@/lib/format';
import { useMessages } from '@/lib/i18n';
import { graphMessages } from '@/lib/i18n/graph';
import type { EngineRefreshStatus } from '@/lib/types';

const POLL_MS = 2000;

export function EngineRefreshButton() {
  const demo = useDemoFeatures();
  if (!demo.on) return null;
  return <Refresh />;
}

function Refresh() {
  const t = useMessages(graphMessages);
  const [st, setSt] = useState<EngineRefreshStatus | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = async () => {
    try {
      const s = await liveApi.engineStatus();
      setSt(s);
      if (s.state === 'running') timer.current = setTimeout(poll, POLL_MS);
    } catch {
      setSt(null);
    }
  };
  useEffect(() => {
    void poll();
    const h = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      clearInterval(h);
      if (timer.current) clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const start = async () => {
    setMsg(null);
    try {
      const s = await liveApi.engineRefresh();
      setSt(s);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(poll, POLL_MS);
    } catch (e) {
      const code = e instanceof ApiError ? e.status : 0;
      setMsg(code === 429 ? t('eng.tooSoon') : code === 404 ? t('eng.refused') : t('add.failed'));
    }
  };

  const running = st?.state === 'running';
  const secs = running && st?.started_at ? Math.max(0, Math.round((now - Date.parse(st.started_at)) / 1000)) : 0;
  const step = st?.step === 'publishing' ? t('eng.publishing') : st?.step === 'reloading' ? t('eng.reloading') : t('eng.building');
  return (
    <div className="flex flex-col items-end gap-1" data-testid="engine-refresh" data-state={st?.state ?? 'unknown'}>
      <div className="flex items-center gap-1.5">
        <Badge tone="warn">{t('demo.badge')}</Badge>
        <Button size="xs" variant="secondary" icon={RefreshCw} busy={running} disabled={running} onClick={() => void start()} title={t('eng.hint')} data-testid="engine-refresh-button">
          {t('eng.refresh')}
        </Button>
      </div>
      <p role="status" className="text-2xs text-muted" data-testid="engine-refresh-status">
        {running ? (
          <span className="tabular-nums">
            {t('eng.running', { s: secs })} · {step}
          </span>
        ) : st?.state === 'done' && st.finished_at ? (
          <>
            {t('eng.done', { time: fmtTime(st.finished_at) })}{' '}
            <button type="button" className="text-accent underline-offset-2 hover:underline" onClick={() => window.location.reload()}>
              {t('eng.show')}
            </button>
          </>
        ) : st?.state === 'failed' ? (
          t('eng.failed')
        ) : (
          msg ?? ''
        )}
      </p>
      {msg && (running || st?.state === 'done' || st?.state === 'failed') ? <p className="text-2xs text-muted">{msg}</p> : null}
    </div>
  );
}
