'use client';
// The staged status line while a question runs (PLAN §3.1, D-08, D-20).
//
// Live: POST /ib/ask/stream (SSE). Each `stage` event moves the line; the answer arrives only after
// grounding, so no unverified text ever reaches the screen.
// Fallback (mock mode, or the stream returns 404/405, or is not text/event-stream): POST /ib/ask,
// and the line advances on elapsed time until the real response arrives.
//
// Either way the line is PROGRESS, not data: it never shows a count, a source or a reason.
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

const MOCK = process.env.NEXT_PUBLIC_IB_MOCK === '1';

export type AskPhase = 'idle' | 'running' | 'done' | 'error';

export interface AskProgress {
  /** Ask a question. Resolves with the verified answer; rejects with an ApiError. */
  run: (question: string) => Promise<AskResponse>;
  phase: AskPhase;
  /** Index into ASK_STEPS of the step now running (5 = all done). */
  step: number;
  /** The current step's i18n key (the last one once done). */
  stepKey: AskStepKey;
  /** 'stream' when driven by real stage events, 'timer' for the elapsed-time fallback. */
  source: 'stream' | 'timer' | null;
  /** Stop showing progress for an in-flight ask (the request itself is not cancelled server-side). */
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

  const clearTimers = () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  };
  useEffect(() => clearTimers, []);

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

  const run = useCallback(
    async (question: string): Promise<AskResponse> => {
      const id = ++runId.current;
      clearTimers();
      setPhase('running');
      setStep(0);
      setSource(null);
      const plainAsk = () => (slot && slot !== 'a' ? slotApi(slot).ask(question) : api.ask(question));
      try {
        let res: AskResponse | null = null;
        if (!MOCK) {
          setSource('stream');
          res = await askStream(question, {
            slot,
            onStage: (name) => {
              if (runId.current !== id) return;
              const n = STAGE_STEP[name];
              if (typeof n === 'number') setStep((s) => Math.max(s, Math.min(n, 4)));
            },
          });
        }
        if (!res) {
          startTimer(id);
          res = await plainAsk();
        }
        if (runId.current === id) {
          clearTimers();
          setStep(ASK_STEPS.length);
          setPhase('done');
        }
        return res;
      } catch (e) {
        if (runId.current === id) {
          clearTimers();
          setPhase('error');
        }
        throw e instanceof ApiError ? e : new ApiError(0, 'network');
      }
    },
    [slot],
  );

  const reset = useCallback(() => {
    runId.current += 1;
    clearTimers();
    setPhase('idle');
    setStep(0);
    setSource(null);
  }, []);

  const stepKey = ASK_STEPS[Math.min(step, ASK_STEPS.length - 1)];
  return { run, phase, step, stepKey, source, reset };
}
