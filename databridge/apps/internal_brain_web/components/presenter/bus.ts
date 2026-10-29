'use client';
// A small action channel between the Presenter and the pages it drives (the Compare page today).
// A page registers a named handler; the Presenter calls it and waits until the page is mounted,
// shows what the step expects, and accepts. A handler returns false for "not ready (yet)".
import { useEffect, useRef } from 'react';

type Handler = (arg: unknown) => boolean | Promise<boolean>;
const handlers = new Map<string, Handler>();

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
 * Call the handler `name` until it accepts or `timeoutMs` passes (0: one try). Resolves true when
 * a handler accepted (after its own work finished), false otherwise. Never throws.
 */
export async function callPresenter(name: string, arg?: unknown, timeoutMs = 0): Promise<boolean> {
  const end = Date.now() + timeoutMs;
  for (;;) {
    const h = handlers.get(name);
    if (h) {
      try {
        if (await h(arg)) return true;
      } catch {
        return false;
      }
    }
    if (Date.now() >= end) return false;
    await sleep(100);
  }
}
