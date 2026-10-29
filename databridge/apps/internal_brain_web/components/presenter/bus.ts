'use client';
// A small action channel between the Presenter and the pages it drives (the Compare page today).
// A page registers a named handler; the Presenter calls it and waits until the page is mounted,
// shows what the step expects, and accepts.
//
// A handler answers with one of three things:
//   false     "not ready (yet)": the caller tries again until its timeout;
//   true      accepted, and its work finished successfully;
//   'failed'  accepted, ran, and did NOT succeed: the caller stops at once (no automatic retry), so
//             the Presenter can show the step as failed.
import { useEffect, useRef } from 'react';

export type HandlerResult = boolean | 'failed';
type Handler = (arg: unknown) => HandlerResult | Promise<HandlerResult>;
const handlers = new Map<string, Handler>();

/** What a call came to: 'ok', 'failed' (ran unsuccessfully), 'not-ready' (never accepted), 'cancelled'. */
export type CallOutcome = 'ok' | 'failed' | 'not-ready' | 'cancelled';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Register `fn` under `name` while the calling component is mounted. Always calls the latest `fn`. */
export function usePresenterHandler(name: string, fn: Handler) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    const h: Handler = (arg) => ref.current(arg);
    handlers.set(name, h);
    return () => {
      if (handlers.get(name) === h) handlers.delete(name);
    };
  }, [name]);
}

/**
 * Call the handler `name` until it accepts or `timeoutMs` passes (0: one try). `live` (optional) is
 * checked before every try: once it says false the call stops with 'cancelled' (a superseded
 * presenter action). Never throws; a handler that throws counts as 'failed'.
 */
export async function callPresenter(name: string, arg?: unknown, timeoutMs = 0, live: () => boolean = () => true): Promise<CallOutcome> {
  const end = Date.now() + timeoutMs;
  for (;;) {
    if (!live()) return 'cancelled';
    const h = handlers.get(name);
    if (h) {
      let r: HandlerResult;
      try {
        r = await h(arg);
      } catch {
        return 'failed';
      }
      if (r === true) return 'ok';
      if (r === 'failed') return 'failed';
    }
    if (Date.now() >= end) return 'not-ready';
    await sleep(100);
  }
}
