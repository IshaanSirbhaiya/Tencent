'use client';
// The staged status line while a question runs (PLAN §3.1, D-08, D-20).
//
// Live: POST /ib/ask/stream (SSE). Each `stage` event moves the line; the answer arrives only after
// grounding, so no unverified text ever reaches the screen.
// Fallback (mock mode, or the stream returns 404/405, or is not text/event-stream): POST /ib/ask,
// and the line advances on elapsed time until the real response arrives.
//
// Either way the line is PROGRESS, not data: it never shows a count, a source or a reason.
//
// Stream pacing: the IB emits `gate1` and `gate2` back to back once the live checks are done (no
// permission-sensitive timing on the public stream). So while waiting for the first events the line
// may move from "Checking your access" to "Searching your sources" on a short timer, and no further:
// every later step is shown only after its own event arrived. Steps that arrive together are shown
// one after another, each for a short moment, so the line reads naturally instead of jumping.
//
// Cancellation: a new run, reset() and unmount abort the in-flight fetch and invalidate its
// generation, so a stale stream can never move the line or resolve as the current answer. The
// backend still finishes and ledgers an accepted request; only the UI lets go of it.
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, api, askStream, slotApi, type AskStreamStage, type Slot } from '@/lib/api';
import type { AskResponse } from '@/lib/types';

/** The five steps the visitor sees, in order (keys into lib/i18n/user.ts). */
export const ASK_STEPS = ['progress.access', 'progress.search', 'progress.recheck', 'progress.write', 'progress.verify'] as const;
export type AskStepKey = (typeof ASK_STEPS)[number];

/** SSE stage → index of the step that is now running. */
const STAGE_STEP: Record<AskStreamStage, number> = {
  accepted: 0,
  gate1: 1,
  gate2: 2,
  sealed: 3,
  composing: 3,
  grounded: 4,
  released: 5,
};

/** Elapsed-time fallback: when each step starts (ms). The last one holds until the answer. */
const TIMELINE = [0, 450, 1000, 1700, 3000];

/** Stream mode: the only step the line may reach on time alone ("Searching your sources"). */
const STREAM_TIMER_STEP = 1;
const STREAM_TIMER_MS = 450;
/** Stream mode: the shortest time one step stays on screen when several arrive together. */
const STREAM_DWELL_MS = 350;

// A little longer than the proxy's own 90 s deadline (as lib/api.ts), so the proxy's 503 arrives first.
const CLIENT_TIMEOUT_MS = 95_000;

const MOCK = process.env.NEXT_PUBLIC_IB_MOCK === '1';

const CANCELLED = 'cancelled';

/** True for the rejection of an ask that was superseded (new run, reset, unmount): ignore it. */
export function isAskCancelled(e: unknown): boolean {
  return e instanceof ApiError && e.status === 0 && e.message === CANCELLED;
}

const cancelled = () => new ApiError(0, CANCELLED);

/** Settle with `p`, or reject as cancelled as soon as `signal` aborts (the work itself may go on). */
function untilAborted<T>(p: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(cancelled());
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(cancelled());
    signal.addEventListener('abort', onAbort, { once: true });
    p.then(
      (v) => {
        signal.removeEventListener('abort', onAbort);
        resolve(v);
      },
      (e) => {
        signal.removeEventListener('abort', onAbort);
        reject(e);
      },
    );
  });
}

export type AskPhase = 'idle' | 'running' | 'done' | 'error';

export interface AskProgress {
  /**
   * Ask a question. Resolves with the verified answer; rejects with an ApiError. When the run is
   * superseded (a newer run, reset() or unmount) it rejects with an error for which
   * `isAskCancelled` is true: the caller should ignore it.
   */
  run: (question: string) => Promise<AskResponse>;
  phase: AskPhase;
  /** Index into ASK_STEPS of the step now running (5 = all done). */
  step: number;
  /** The current step's i18n key (the last one once done). */
  stepKey: AskStepKey;
  /** 'stream' when driven by real stage events, 'timer' for the elapsed-time fallback. */
  source: 'stream' | 'timer' | null;
  /**
   * Stop showing progress for an in-flight ask and let go of it: its fetch is aborted and its result
   * is ignored (the request itself is not cancelled server-side).
   */
  reset: () => void;
}

/**
 * One in-flight question at a time per hook instance. Compare uses one instance per column and
 * passes its slot (`'b'` / `'c'`) so the right session cookie is used.
 */
export function useAskProgress(opts: { slot?: Slot } = {}): AskProgress {
  const slot = opts.slot;
  const [phase, setPhase] = useState<AskPhase>('idle');
  const [step, setStep] = useState(0);
  const [source, setSource] = useState<'stream' | 'timer' | null>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const runId = useRef(0);
  const inFlight = useRef<AbortController | null>(null);
  // Stream pacing: the step reached by events (target) and the step on screen (shown).
  const pace = useRef({ target: 0, shown: 0, changedAt: 0, timer: null as ReturnType<typeof setTimeout> | null });

  const clearTimers = () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    if (pace.current.timer) clearTimeout(pace.current.timer);
    pace.current.timer = null;
  };

  /** Invalidate the current generation and abort its fetch. */
  const letGo = () => {
    runId.current += 1;
    clearTimers();
    inFlight.current?.abort();
    inFlight.current = null;
  };

  useEffect(() => letGo, []);

  const startTimer = (id: number) => {
    clearTimers();
    setSource('timer');
    TIMELINE.forEach((at, i) => {
      if (i === 0) return;
      timers.current.push(
        setTimeout(() => {
          if (runId.current === id) setStep((s) => Math.max(s, i));
        }, at),
      );
    });
  };

  /** Stream mode: show the next step once the current one has been up for STREAM_DWELL_MS. */
  const pump = (id: number) => {
    const p = pace.current;
    if (p.timer || runId.current !== id || p.shown >= p.target) return;
    const wait = Math.max(0, p.changedAt + STREAM_DWELL_MS - Date.now());
    p.timer = setTimeout(() => {
      p.timer = null;
      if (runId.current !== id) return;
      p.shown = Math.min(p.shown + 1, p.target);
      p.changedAt = Date.now();
      setStep(p.shown);
      pump(id);
    }, wait);
  };
  const reach = (id: number, n: number) => {
    if (runId.current !== id) return;
    const capped = Math.min(n, ASK_STEPS.length - 1);
    if (capped <= pace.current.target) return;
    pace.current.target = capped;
    pump(id);
  };

  const run = useCallback(
    async (question: string): Promise<AskResponse> => {
      letGo();
      const id = runId.current;
      const live = () => runId.current === id;
      const ac = new AbortController();
      inFlight.current = ac;
      let timedOut = false;
      const deadline = setTimeout(() => {
        timedOut = true;
        ac.abort();
      }, CLIENT_TIMEOUT_MS);
      pace.current = { target: 0, shown: 0, changedAt: Date.now(), timer: null };
      setPhase('running');
      setStep(0);
      setSource(null);
      const plainAsk = () => (slot && slot !== 'a' ? slotApi(slot).ask(question) : api.ask(question));
      try {
        let res: AskResponse | null = null;
        if (!MOCK) {
          setSource('stream');
          // Only "Searching your sources" may come on time alone; the rest waits for its event.
          timers.current.push(setTimeout(() => reach(id, STREAM_TIMER_STEP), STREAM_TIMER_MS));
          res = await askStream(question, {
            slot,
            signal: ac.signal,
            onStage: (name) => {
              const n = STAGE_STEP[name];
              if (typeof n === 'number') reach(id, n);
            },
          });
        }
        if (!live()) throw cancelled();
        if (!res) {
          startTimer(id);
          res = await untilAborted(plainAsk(), ac.signal);
        }
        if (!live()) throw cancelled();
        clearTimers();
        setStep(ASK_STEPS.length);
        setPhase('done');
        return res;
      } catch (e) {
        // Superseded (new run, reset, unmount): leave the screen to whoever superseded us.
        if (!live() || (ac.signal.aborted && !timedOut)) throw cancelled();
        clearTimers();
        setPhase('error');
        throw e instanceof ApiError && !isAskCancelled(e) ? e : new ApiError(0, 'network');
      } finally {
        clearTimeout(deadline);
        if (inFlight.current === ac) inFlight.current = null;
      }
    },
    // letGo/reach/startTimer only touch refs and state setters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [slot],
  );

  const reset = useCallback(() => {
    letGo();
    setPhase('idle');
    setStep(0);
    setSource(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stepKey = ASK_STEPS[Math.min(step, ASK_STEPS.length - 1)];
  return { run, phase, step, stepKey, source, reset };
}
