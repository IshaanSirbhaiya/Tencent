'use client';
// Presenter keys and step runner. Module-level on purpose: once any page that mounts
// <PresenterRoot> has been visited, the keys keep working through client navigation to pages that
// do not render the bar (/dev/journey, /dev/audit), because the listener lives on `window`.
//
// WCAG 2.1.4: single-key shortcuts do nothing unless presenter mode is armed (?present=1,
// Ctrl+Shift+P, NEXT_PUBLIC_IB_PRESENTER=1 or localStorage["ib.presenter"]="1"). They are ignored
// while typing in a field, inside composite widgets for the arrow keys, and with Ctrl/Alt/Meta held.
import { clearCompareCache } from '@/components/compare/cache';
import { ApiError, analyticsApi, api, ibHealth, slotLogout, withSlotLocks } from '@/lib/api';
import { callPresenter } from './bus';
import { clearDeliverCache, openDeliverables, warmDeliverables } from './deliver';
import { STEPS, stepUrl, type StepCtx } from './script';
import { applyStage } from './stage';
import { clearPresenter, getPresenter, setPresenter, type Dot } from './store';

export interface Host {
  push: (url: string) => void;
  refreshSession: () => Promise<unknown>;
  toggleLang: () => void;
}

let host: Host | null = null;
/** The mounted PresenterRoot hands over router, session and language (latest wins). */
export function setHost(h: Host) {
  host = h;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/* ---------------------------------------------------------- generations */
// Every step action runs under a generation number. Starting a step, P and Reset each begin a new
// generation; an action whose generation is no longer current stops at its next checkpoint (after
// every await, and before any navigation, session change or page call), so a superseded action can
// never navigate, sign someone in or drive a page after the presenter moved on.

let generation = 0;

/** Thrown inside a superseded action; never shown (the newer action owns the bar). */
class Superseded extends Error {
  constructor() {
    super('superseded');
  }
}

/* ------------------------------------------------- session serialisation */
// Session-changing work (the main demo sign-in, Reset's sign-outs) runs one at a time, in order,
// so Reset can never interleave with a sign-in that is halfway through.
let sessionChain: Promise<unknown> = Promise.resolve();
function serialSession<T>(fn: () => Promise<T>): Promise<T> {
  const next = sessionChain.then(fn, fn);
  sessionChain = next.catch(() => undefined);
  return next;
}

function ctx(gen: number): StepCtx {
  const isLive = () => gen === generation;
  const live = () => {
    if (!isLive()) throw new Superseded();
  };
  return {
    go: async (url) => {
      live();
      host?.push(url);
      await sleep(30);
      live();
    },
    call: async (name, arg, timeoutMs) => {
      live();
      const r = await callPresenter(name, arg, timeoutMs, isLive);
      live();
      return r;
    },
    signInMain: (persona) =>
      serialSession(async () => {
        live();
        let sub: string | null = null;
        try {
          sub = (await api.whoami()).sub;
        } catch {
          /* signed out */
        }
        live();
        if (sub !== persona) await api.switchTo(persona);
        live();
        await host?.refreshSession();
        live();
        // /ib/status needs a session: re-read the dots now that someone is signed in, so the HUD never
        // keeps the "unknown" it got from a fetch made before the sign-in (401).
        void refreshHealth();
      }),
    req: (persona) => getPresenter().reqs[persona] ?? null,
    reveal: async (selector, timeoutMs = 8_000) => {
      const end = Date.now() + timeoutMs;
      for (;;) {
        live();
        const el = document.querySelector<HTMLElement>(selector);
        if (el) {
          const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches || document.documentElement.dataset.motion === 'reduce';
          el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
          return true;
        }
        if (Date.now() >= end) return false;
        await sleep(100);
      }
    },
  };
}

/* ------------------------------------------------------------- key legend */
// In Stage the bar is one row; the key legend shows for 3 s after P, or while toggled with ?.
let legendTimer: ReturnType<typeof setTimeout> | null = null;
function flashLegend(ms = 3_000) {
  if (legendTimer) clearTimeout(legendTimer);
  setPresenter({ legend: true });
  legendTimer = setTimeout(() => {
    legendTimer = null;
    setPresenter({ legend: false });
  }, ms);
}
function toggleLegend() {
  if (legendTimer) clearTimeout(legendTimer);
  legendTimer = null;
  setPresenter({ legend: !getPresenter().legend });
}

/** Run step `i`: forward runs its action; back only re-opens its page. */
export async function runStep(i: number, dir: 'forward' | 'back' = 'forward') {
  const step = STEPS[i];
  if (!step) return;
  const gen = ++generation;
  setPresenter({ step: i, started: true, busy: true, failed: false, clipOpen: false });
  try {
    const c = ctx(gen);
    if (dir === 'back') await c.go(stepUrl(step, c));
    else await step.action(c);
    if (gen === generation) setPresenter({ busy: false });
  } catch {
    // A superseded action says nothing: the newer one owns the bar.
    if (gen === generation) setPresenter({ busy: false, failed: true });
  }
}

/**
 * P: Stage on, back to step 0, check the status dots. It touches no sandbox, but the deliverables
 * warm-up signs Leo in and runs one real, ledgered ask and its deliverables (deliver.ts).
 */
export function start() {
  // P while a step (or Reset) is running must not start a second run.
  const s = getPresenter();
  if (s.busy || s.resetting) return;
  applyStage(true);
  setPresenter({ armed: true, stage: true, hidden: false });
  flashLegend();
  // Q&A beat D: build Leo's deliverables first (serialised, so step 0's sign-in waits for it)
  const warm = warmDeliverables(serialSession);
  const first = runStep(0, 'forward');
  void refreshHealth();
  // /ib/status is 401 until someone is signed in: read the dots again once the warm-up and step 0 are done.
  void Promise.allSettled([warm, first]).then(() => refreshHealth());
}

/** Sign both compare slots out while holding their locks (never halfway through a Compare run). */
async function signSlotsOut() {
  const out = () => Promise.allSettled([slotLogout('b'), slotLogout('c')]);
  try {
    await withSlotLocks(['b', 'c'], out);
  } catch (e) {
    // No Web Locks (not a secure context): Compare cannot run there at all, so nothing holds them.
    if (e instanceof ApiError && e.message === 'no-locks') await out();
  }
}

let resetting = false;

/**
 * R (after the confirm): sign every demo session out, forget presenter state, back to step 0.
 * A step that is still running is superseded first and its pending Compare run is cancelled (its
 * results are dropped); the sign-outs then wait for the Compare slot locks and for any sign-in in
 * flight, so the sessions end up consistent with the presenter state. The bar says so meanwhile.
 */
export async function resetDemo() {
  if (resetting) return;
  resetting = true;
  generation++;
  setPresenter({ resetOpen: false, clipOpen: false, busy: true, failed: false, resetting: true });
  try {
    await callPresenter('compare-cancel');
    await serialSession(() => Promise.allSettled([api.logout(), signSlotsOut()]));
    clearCompareCache();
    clearPresenter();
    clearDeliverCache();
  } finally {
    resetting = false;
    setPresenter({ resetting: false });
  }
  const warm = warmDeliverables(serialSession); // R rebuilds the D cache
  await runStep(0, 'forward');
  void refreshHealth();
  void warm.then(() => refreshHealth());
}

export function setArmed(on: boolean) {
  setPresenter({ armed: on, hidden: false });
  if (!on) {
    applyStage(false);
    setPresenter({ stage: false, clipOpen: false, resetOpen: false });
  }
}

export function setStage(on: boolean) {
  applyStage(on);
  setPresenter({ stage: on });
}

/** The three status dots: API reachable, model live or quoting, ledger verified. */
export async function refreshHealth() {
  const [h, s] = await Promise.allSettled([ibHealth(), analyticsApi.status()]);
  const apiDot: Dot = h.status === 'fulfilled' && h.value?.ok ? 'ok' : 'down';
  // A status read that fails while the API is up (e.g. a 401 between sign-ins) keeps the last known
  // dots rather than dropping back to "unknown" on stage.
  const prev = getPresenter().health;
  let model: Dot = apiDot === 'ok' && prev?.model ? prev.model : 'unknown';
  let ledger: Dot = apiDot === 'ok' && prev?.ledger ? prev.ledger : 'unknown';
  if (s.status === 'fulfilled' && s.value) {
    model = s.value.models?.active ? 'live' : 'quoting';
    ledger = s.value.ledger?.ok === true ? 'ok' : s.value.ledger?.ok === false ? 'down' : 'unknown';
  }
  setPresenter({ health: { api: apiDot, model, ledger } });
}

/* ------------------------------------------------------------------ keys */

const COMPOSITE = '[role="radiogroup"],[role="tablist"],[role="listbox"],[role="menu"],[role="menubar"],[role="slider"],[role="grid"],[role="tree"]';

function typing(t: EventTarget | null): boolean {
  const el = t instanceof HTMLElement ? t : null;
  if (!el) return false;
  if (el.isContentEditable) return true;
  return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT';
}

const openDialog = () => document.querySelector('[role="dialog"],[role="alertdialog"]');

let lastAdvance = 0;
let swallowSpaceUp = false;

function onKeyDown(e: KeyboardEvent) {
  const s = getPresenter();
  const key = e.key;
  // Ctrl+Shift+P arms or disarms presenter mode (the documented way to turn the keys off).
  if (e.ctrlKey && e.shiftKey && !e.altKey && !e.metaKey && key.toLowerCase() === 'p') {
    e.preventDefault();
    setArmed(!s.armed);
    return;
  }
  if (!s.armed || e.ctrlKey || e.altKey || e.metaKey || e.defaultPrevented) return;
  if (typing(e.target)) return;

  // A dialog has the keyboard (sources sheet, reset confirm, recording): only B closes the recording.
  if (openDialog()) {
    if (key.toLowerCase() === 'b' && s.clipOpen && !e.repeat) {
      e.preventDefault();
      setPresenter({ clipOpen: false });
    }
    return;
  }
  const inComposite = e.target instanceof Element && !!e.target.closest(COMPOSITE);

  switch (key) {
    case ' ':
    case 'Spacebar':
    case 'ArrowRight': {
      if (key === 'ArrowRight' && inComposite) return;
      e.preventDefault();
      if (key !== 'ArrowRight') swallowSpaceUp = true;
      if (e.repeat || !s.started || s.busy) return;
      const now = Date.now();
      if (now - lastAdvance < 400) return; // debounce a double press
      lastAdvance = now;
      // A failed step does not advance on Space: Space tries it again, the right arrow skips it.
      if (s.failed && key !== 'ArrowRight') void runStep(s.step, 'forward');
      else if (s.step + 1 < STEPS.length) void runStep(s.step + 1, 'forward');
      return;
    }
    case 'ArrowLeft':
      if (inComposite) return;
      e.preventDefault();
      if (e.repeat || !s.started || s.busy || s.step === 0) return;
      void runStep(s.step - 1, 'back');
      return;
  }
  if (e.repeat) return;
  switch (key.toLowerCase()) {
    case 'p':
      e.preventDefault();
      if (s.busy || s.resetting) return; // never a second run on top of the current one
      start();
      return;
    case 'b':
      e.preventDefault();
      setPresenter({ clipOpen: !s.clipOpen });
      return;
    case 'r':
      e.preventDefault();
      if (s.resetting) return;
      setPresenter({ resetOpen: true });
      return;
    case 'l':
      e.preventDefault();
      host?.toggleLang();
      return;
    case 's':
      e.preventDefault();
      setStage(!s.stage);
      return;
    case '2':
      e.preventDefault();
      if (s.busy) return; // the running step owns the page
      void callPresenter('compare-open-citation', { n: 2 }, 0);
      return;
    case 'd':
      e.preventDefault();
      if (s.busy || s.resetting) return;
      void openDeliverables(serialSession, () => host?.refreshSession());
      return;
    case 'escape':
      e.preventDefault();
      setPresenter({ hidden: !s.hidden });
      return;
    case '?':
      e.preventDefault();
      toggleLegend();
      return;
  }
}

// A focused button activates on Space keyup: when Space advanced the script, swallow that too.
function onKeyUp(e: KeyboardEvent) {
  if ((e.key === ' ' || e.key === 'Spacebar') && swallowSpaceUp) {
    swallowSpaceUp = false;
    e.preventDefault();
  }
}

// Step 7 is off-app: when the driver comes back to this window, reload the audit view so the
// WorkBuddy record shows (the audit page reads ?latest=1 to highlight the newest row).
function onFocus() {
  const s = getPresenter();
  if (s.armed && s.started && STEPS[s.step]?.id === 'workbuddy' && !s.busy) host?.push('/dev/audit?latest=1');
}

let installed = false;
export function installPresenterKeys() {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  window.addEventListener('keydown', onKeyDown, true);
  window.addEventListener('keyup', onKeyUp, true);
  window.addEventListener('focus', onFocus);
}
