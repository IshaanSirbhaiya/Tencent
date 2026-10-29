'use client';
// Presenter keys and step runner. Module-level on purpose: once any page that mounts
// <PresenterRoot> has been visited, the keys keep working through client navigation to pages that
// do not render the bar (/dev/journey, /dev/audit), because the listener lives on `window`.
//
// WCAG 2.1.4: single-key shortcuts do nothing unless presenter mode is armed (?present=1,
// Ctrl+Shift+P, NEXT_PUBLIC_IB_PRESENTER=1 or localStorage["ib.presenter"]="1"). They are ignored
// while typing in a field, inside composite widgets for the arrow keys, and with Ctrl/Alt/Meta held.
import { clearCompareCache } from '@/components/compare/cache';
import { analyticsApi, api, ibHealth, slotLogout } from '@/lib/api';
import { callPresenter } from './bus';
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

function ctx(): StepCtx {
  return {
    go: async (url) => {
      host?.push(url);
      await sleep(30);
    },
    call: callPresenter,
    signInMain: async (persona) => {
      let sub: string | null = null;
      try {
        sub = (await api.whoami()).sub;
      } catch {
        /* signed out */
      }
      if (sub !== persona) await api.switchTo(persona);
      await host?.refreshSession();
    },
    req: (persona) => getPresenter().reqs[persona] ?? null,
  };
}

let runId = 0;

/** Run step `i`: forward runs its action; back only re-opens its page. */
export async function runStep(i: number, dir: 'forward' | 'back' = 'forward') {
  const step = STEPS[i];
  if (!step) return;
  const id = ++runId;
  setPresenter({ step: i, started: true, busy: true, failed: false, clipOpen: false });
  try {
    const c = ctx();
    if (dir === 'back') await c.go(stepUrl(step, c));
    else await step.action(c);
    if (id === runId) setPresenter({ busy: false });
  } catch {
    if (id === runId) setPresenter({ busy: false, failed: true });
  }
}

/** P: Stage on, back to step 0, check the status dots. Does not touch any ledger or sandbox. */
export function start() {
  applyStage(true);
  setPresenter({ armed: true, stage: true, hidden: false });
  void runStep(0, 'forward');
  void refreshHealth();
}

/** R (after the confirm): sign every demo session out, forget presenter state, back to step 0. */
export async function resetDemo() {
  runId++;
  setPresenter({ resetOpen: false, clipOpen: false, busy: true });
  await Promise.allSettled([api.logout(), slotLogout('b'), slotLogout('c')]);
  clearCompareCache();
  clearPresenter();
  await runStep(0, 'forward');
  void refreshHealth();
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
  let model: Dot = 'unknown';
  let ledger: Dot = 'unknown';
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
      if (s.step + 1 < STEPS.length) void runStep(s.step + 1, 'forward');
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
      start();
      return;
    case 'b':
      e.preventDefault();
      setPresenter({ clipOpen: !s.clipOpen });
      return;
    case 'r':
      e.preventDefault();
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
      void callPresenter('compare-open-citation', { n: 2 }, 0);
      return;
    case 'escape':
      e.preventDefault();
      setPresenter({ hidden: !s.hidden });
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
