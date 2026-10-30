'use client';
// Live growth (docs/finals/live_growth/PLAN.md §3, §6): poll GET /ib/graph/changes for ONE session and
// hand each non-empty delta to the explorer. A demo-proof feature (D-24): mounted only when the
// server and the viewer both turned demo features on.
//
// - Every 2 s in the Stage theme, 3 s otherwise; only while enabled, not paused and the tab is visible.
// - The cursor is opaque. A missing or stale one simply starts a new, empty window on the server.
// - 401, 403 or 404 (signed out, forbidden, feature off) stop polling; anything else backs off
//   (x2 up to 30 s) and reports "temporarily unavailable".
import { useEffect, useRef, useState } from 'react';
import { ApiError, liveApi, type Slot } from '@/lib/api';
import type { GraphChanges } from '@/lib/types';

export type LiveState = 'live' | 'paused' | 'unavailable' | 'stopped';

/** Notes this browser posted (doc id → Date.now() at post), to label "your note" and time its arrival. */
const posted = new Map<string, number>();
export function rememberPost(docId: string, at = Date.now()) {
  posted.set(docId, at);
}
export function postedAt(docId: string): number | null {
  return posted.get(docId) ?? null;
}

export function useLiveChanges(opts: {
  enabled: boolean;
  slot: Slot;
  intervalMs: number;
  /** Changes when the explorer reloads its galaxy: the cursor restarts with it. */
  epoch: unknown;
  onDelta: (d: GraphChanges) => void;
}): { state: LiveState; paused: boolean; setPaused: (p: boolean) => void } {
  const { enabled, slot, intervalMs, epoch } = opts;
  const [paused, setPaused] = useState(false);
  const [state, setState] = useState<LiveState>('live');
  const cursor = useRef<string | null>(null);
  const onDelta = useRef(opts.onDelta);
  onDelta.current = opts.onDelta;

  useEffect(() => {
    cursor.current = null;
  }, [epoch, slot]);

  useEffect(() => {
    if (!enabled) return;
    if (paused) {
      setState('paused');
      return;
    }
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let backoff = 0;
    const tick = async () => {
      if (!alive) return;
      if (typeof document !== 'undefined' && document.hidden) {
        timer = setTimeout(tick, intervalMs);
        return;
      }
      try {
        const r = await liveApi.changes(cursor.current, slot);
        if (!alive) return;
        cursor.current = r.cursor;
        backoff = 0;
        setState('live');
        if (r.nodes.length || r.edges.length) onDelta.current(r);
        timer = setTimeout(tick, intervalMs);
      } catch (e) {
        if (!alive) return;
        const st = e instanceof ApiError ? e.status : 0;
        if (st === 401 || st === 403 || st === 404) {
          setState('stopped');
          return;
        }
        backoff = Math.min(30_000, backoff ? backoff * 2 : intervalMs * 2);
        setState('unavailable');
        timer = setTimeout(tick, backoff);
      }
    };
    setState('live');
    void tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [enabled, paused, slot, intervalMs, epoch]);

  return { state, paused, setPaused };
}
