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
import { announce } from '@/components/ui/announce';
import { callPresenter } from './bus';
import { clearDeliverCache, openDeliverables, warmDeliverables } from './deliver';
import { STEPS, stepUrl, type StepCtx } from './script';
import { markSessionOwned, serialSession, sessionOwner } from './session';
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
// Session-changing work (the main demo sign-in, Reset's sign-outs, and the shell's Sign out and
// persona switch) runs one at a time, in order, through ./session.ts (round-4 review #1), so Reset
// can never interleave with a sign-in that is halfway through.

/** The shell signed out or switched persona while this step was running: it stops, it does not override. */
class SessionTaken extends Error {
  constructor() {
    super('session-taken');
  }
}

function ctx(gen: number): StepCtx {
  const isLive = () => gen === generation;
  const live = () => {
    if (!isLive()) throw new Superseded();
  };
  // Who owned the main session when this step began; a shell change after that wins.
  let owned = sessionOwner();
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
        if (sessionOwner() !== owned) throw new SessionTaken();
        markSessionOwned();
        owned = sessionOwner();
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
    waitFor: async (selector, timeoutMs = 15_000) => {
      const end = Date.now() + timeoutMs;
      for (;;) {
        live();
        if (document.querySelector(selector)) return true;
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
/** A click inside the legend row keeps it open (the 3 s flash after P no longer closes it). */
export function pinLegend() {
  if (!legendTimer) return;
  clearTimeout(legendTimer);
  legendTimer = null;
  setPresenter({ legend: true });
}
export function toggleLegend() {
  if (legendTimer) clearTimeout(legendTimer);
  legendTimer = null;
  setPresenter({ legend: !getPresenter().legend });
}

/** Run step `i`: forward runs its action; back re-opens its page (and, for some, its session). */
export async function runStep(i: number, dir: 'forward' | 'back' = 'forward') {
  const step = STEPS[i];
  if (!step) return;
  const gen = ++generation;
  setPresenter({ step: i, started: true, busy: true, failed: false, clipOpen: false });
  try {
    const c = ctx(gen);
    if (dir === 'back') await (step.back ? step.back(c) : c.go(stepUrl(step, c)));
    else await step.action(c);
    if (gen === generation) setPresenter({ busy: false });
  } catch {
    // A superseded action says nothing: the newer one owns the bar.
    if (gen === generation) setPresenter({ busy: false, failed: true });
  }
  if (dir === 'forward' && gen === generation) warmAfter(i);
}

/*
 * D's warm-up waits until the first Compare step has settled (round-5 review cross-check #3): it
 * runs one more live ask and its deliverables, so it must not compete with the two live Compare
 * asks of step 01. It starts once step 01 has finished (or failed, or was skipped past), while the
 * presenter speaks over step 01's answers; Compare uses its own slot sessions, so borrowing the
 * main session then is invisible. D before that still builds on demand.
 */
let warmPending = false;
function warmAfter(i: number) {
  if (!warmPending) return;
  const compare = STEPS.findIndex((st) => st.id === 'compare-people');
  if (compare < 0 || i < compare) return;
  warmPending = false;
  void warmDeliverables(serialSession).then(() => refreshHealth());
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
  // The title card first (stage QA 30 Oct #6: building Leo's deliverables first made P take 4.7 to
  // 6.5 s). Q&A beat D's warm-up runs once step 01's Compare has settled (warmAfter), in the
  // background, through the same session queue, so a step's sign-in that comes later still waits
  // for it and wins.
  warmPending = true;
  const first = runStep(0, 'forward');
  void refreshHealth();
  // /ib/status is 401 until someone is signed in: read the dots again once step 0 is done.
  void first.then(() => refreshHealth());
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
    await serialSession(() => {
      markSessionOwned(); // R owns the session now: nothing D borrowed is put back
      return Promise.allSettled([api.logout(), signSlotsOut()]);
    });
    clearCompareCache();
    clearPresenter();
    clearDeliverCache();
  } finally {
    resetting = false;
    setPresenter({ resetting: false });
  }
  warmPending = true; // R rebuilds the D cache, once step 01's Compare has settled (warmAfter)
  await runStep(0, 'forward');
  void refreshHealth();
}

/** The presenter's own slides (00 and 08 to 10): no app navigation on them. */
const SLIDE_PATH = /^\/present(\/(proof|business|close))?\/?$/;

export function setArmed(on: boolean) {
  setPresenter({ armed: on, hidden: false });
  if (!on) {
    applyStage(false);
    setPresenter({ stage: false, clipOpen: false, resetOpen: false });
    // Exit on a slide would leave a page with no links (stage QA re-run): go to the app's home.
    if (typeof window !== 'undefined' && SLIDE_PATH.test(window.location.pathname)) host?.push('/');
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

/**
 * A dialog that has the keyboard (sources sheet, reset confirm, recording, D's panel). The grow
 * step's Add sheet is presenter-owned (`data-presenter-owned`, round-3 review #1): the script put it
 * there and Space keeps driving the script over it.
 */
const openDialog = () => document.querySelector('[role="dialog"]:not([data-presenter-owned]),[role="alertdialog"]');
const anyDialog = () => document.querySelector('[role="dialog"],[role="alertdialog"]');

let lastAdvance = 0;
let swallowSpaceUp = false;

/* --------------------------------------------- actions shared by keys, bar buttons and mouse */
// The bar's buttons and the mouse's side buttons call exactly these, so a click and a key press
// can never disagree (same busy, debounce and failed-step rules).

/**
 * Space / ▶ / mouse forward: the next step. A failed step is tried again (`skip` false), or skipped
 * (`skip` true: → and the bar's Skip). Ignored while a step runs, before P, and within 400 ms of
 * the last advance (a double press or double click).
 */
export function advance(skip = false) {
  const s = getPresenter();
  if (!s.started || s.busy) return;
  const now = Date.now();
  if (now - lastAdvance < 400) return; // debounce a double press
  lastAdvance = now;
  closeSourcesSheet();
  // A failed step does not advance on Space: Space tries it again, the right arrow skips it.
  if (s.failed && !skip) void runStep(s.step, 'forward');
  else if (s.step + 1 < STEPS.length) void runStep(s.step + 1, 'forward');
}

/**
 * Step 03 leaves the sources sheet open. Space, → and the bar's Next close it (through its own Esc
 * handler, which restores focus) and go on, as over the grow step's Add sheet (stage QA re-run).
 * No other dialog is closed this way.
 */
const sourcesSheet = () => document.querySelector('[role="dialog"][data-testid="sources-sheet"]');
function closeSourcesSheet() {
  if (!sourcesSheet()) return;
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true, cancelable: true }));
}

/** ← / ◀ / mouse back: re-open the previous step. Ignored while a step runs, before P and at step 00. */
export function goBack() {
  const s = getPresenter();
  if (!s.started || s.busy || s.step === 0) return;
  void runStep(s.step - 1, 'back');
}

/** P / Start (never a second run on top of the current one). */
export function startOnce() {
  const s = getPresenter();
  if (s.busy || s.resetting) return;
  start();
}

let clipNoteTimer: ReturnType<typeof setTimeout> | null = null;

/**
 * B: the backup recording on or off. Stage QA 30 Oct: over a sheet (the sources sheet in step 03,
 * D's panel) B closes that sheet first and then plays the recording (#2); the reset confirm is
 * never closed this way. On a step with no recording (the slides) B leaves the slide on screen and
 * shows a short "No recording for this step" note instead of a black screen (#4).
 */
export function toggleClip() {
  const s = getPresenter();
  if (s.clipOpen) {
    setPresenter({ clipOpen: false });
    return;
  }
  if (s.resetOpen || s.resetting) return;
  if (!STEPS[s.step]?.clip) {
    if (clipNoteTimer) clearTimeout(clipNoteTimer);
    setPresenter({ clipNote: true });
    clipNoteTimer = setTimeout(() => {
      clipNoteTimer = null;
      setPresenter({ clipNote: false });
    }, 2_500);
    return;
  }
  const sheet = openDialog();
  if (sheet) {
    // Close it through its own Esc handler (it restores focus and, for D, the session), then play.
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true, cancelable: true }));
    setTimeout(() => setPresenter({ clipOpen: true }), 0);
    return;
  }
  setPresenter({ clipOpen: true, clipNote: false });
}

/** R: open the reset confirm (the reset itself runs only from the confirm). */
export function askReset() {
  if (getPresenter().resetting) return;
  setPresenter({ resetOpen: true });
}

/** H: hide or show the bar on purpose. */
export function toggleHidden(hidden = !getPresenter().hidden) {
  setPresenter({ hidden });
}

/** Ctrl+Shift+P off / Exit presenter: keys off, Stage off, the app is an ordinary website again. */
export function exitPresenter() {
  setArmed(false);
}

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

  // A dialog has the keyboard (sources sheet, reset confirm, recording): only B works there. It
  // closes the recording, or closes a sheet and plays the recording (toggleClip; never the reset confirm).
  if (openDialog()) {
    if (key.toLowerCase() === 'b' && !e.repeat) {
      e.preventDefault();
      toggleClip();
    } else if ((key === ' ' || key === 'Spacebar' || key === 'ArrowRight') && sourcesSheet() && openDialog() === sourcesSheet()) {
      // Step 03's sources sheet: Space and → close it and go on (advance closes it).
      e.preventDefault();
      if (key !== 'ArrowRight') swallowSpaceUp = true;
      if (!e.repeat) advance(key === 'ArrowRight');
    }
    return;
  }
  // Esc with a presenter-owned sheet open closes that sheet (its own handler), never the HUD.
  if (key === 'Escape' && anyDialog()) return;
  const inComposite = e.target instanceof Element && !!e.target.closest(COMPOSITE);

  switch (key) {
    case ' ':
    case 'Spacebar':
    case 'ArrowRight': {
      if (key === 'ArrowRight' && inComposite) return;
      e.preventDefault();
      if (key !== 'ArrowRight') swallowSpaceUp = true;
      if (e.repeat) return;
      advance(key === 'ArrowRight');
      return;
    }
    case 'ArrowLeft':
      if (inComposite) return;
      e.preventDefault();
      if (e.repeat) return;
      goBack();
      return;
  }
  if (e.repeat) return;
  switch (key.toLowerCase()) {
    case 'p':
      e.preventDefault();
      startOnce(); // never a second run on top of the current one
      return;
    case 'b':
      e.preventDefault();
      toggleClip();
      return;
    case 'r':
      e.preventDefault();
      askReset();
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
      // nothing cached (switch off, no citations, signed out): say so instead of a dead key press
      void openDeliverables(serialSession, () => host?.refreshSession()).then((ok) => {
        if (!ok) announce('Deliverables are not ready for this answer.');
      });
      return;
    case 'escape':
      // The Esc rule (PRESENTER_KEYS.md §2): with a sheet or dialog open, Esc closes it (above). Once
      // the script has started, Esc never hides the HUD: an Esc pressed one time too many must not
      // leave the driver without the bar and step number. It only brings a hidden HUD back.
      // H hides it on purpose; before P, Esc still hides or shows it.
      e.preventDefault();
      if (s.started && !s.hidden) return;
      setPresenter({ hidden: !s.hidden });
      return;
    case 'h':
      e.preventDefault();
      toggleHidden();
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

/* ------------------------------------------------------------------ mouse */
// The mouse's back and forward side buttons (MouseEvent.button 3 and 4) are ◀ and ▶ while presenter
// mode is armed, and the browser does not also go back or forward (preventDefault on mousedown,
// mouseup and auxclick; the step moves once, on mouseup). Only these two buttons are touched: an
// ordinary click (links, the app's own navigation, answer cards, the bar's buttons) is never
// prevented or stopped, so the app stays an ordinary website under the mouse.

let lastSideButton = 0;

function onSideButton(e: MouseEvent) {
  const dir = e.button === 3 ? 'back' : e.button === 4 ? 'forward' : null;
  if (!dir || !getPresenter().armed) return;
  e.preventDefault();
  if (e.type !== 'mouseup') return;
  lastSideButton = Date.now();
  if (openDialog()) return; // as for the keys: an open dialog has the input
  if (dir === 'back') goBack();
  else advance();
}

/*
 * Browser history (the toolbar's back button, Alt+←, a side button the browser handled anyway).
 * Rule (PRESENTER_KEYS.md, "Mouse"): when the browser goes back to the PREVIOUS step's page, that is
 * ◀: the script steps back with it (re-opening that page, and its session where the step needs
 * one), so the bar and the page agree. Any other history move (forward, or back to a page the
 * driver opened by clicking around) is left to the browser as on any website: the step number
 * stays, and the next ▶ / Space opens its own page as usual. Ignored while a step runs (the step
 * owns the page and re-opens it, or says "did not finish") and just after a side button already
 * moved the script.
 */
/**
 * Whether the page is at a step's URL: the same path AND every query parameter the step's URL sets
 * (round-5 review cross-check #1). Steps 05 to 07 all live on /dev/audit (?verify=1, ?tamper=1, plain),
 * so a path-only match took Back from step 07's ?latest=1 entry to step 07's own /dev/audit entry as
 * "back to step 06" and re-ran the tamper demo. Extra parameters the page adds itself are allowed.
 */
export function atStepUrl(stepHref: string, here: Pick<Location, 'origin' | 'pathname' | 'search'> = window.location): boolean {
  let want: URL;
  try {
    want = new URL(stepHref, here.origin);
  } catch {
    return false;
  }
  if (want.pathname !== here.pathname) return false;
  const have = new URLSearchParams(here.search);
  let all = true;
  want.searchParams.forEach((v, k) => {
    if (have.get(k) !== v) all = false;
  });
  return all;
}

function onPopState() {
  const s = getPresenter();
  if (!s.armed || !s.started || s.busy || s.step === 0) return;
  if (Date.now() - lastSideButton < 1_000) return;
  const prev = STEPS[s.step - 1];
  if (!prev) return;
  const reqOnly = { req: (persona: string) => getPresenter().reqs[persona] ?? null } as StepCtx;
  if (!atStepUrl(stepUrl(prev, reqOnly))) return;
  // After the router has handled its own popstate, so the step's navigation is the last word.
  setTimeout(goBack, 0);
}

let installed = false;
export function installPresenterKeys() {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  window.addEventListener('keydown', onKeyDown, true);
  window.addEventListener('keyup', onKeyUp, true);
  window.addEventListener('focus', onFocus);
  window.addEventListener('mousedown', onSideButton, true);
  window.addEventListener('mouseup', onSideButton, true);
  window.addEventListener('auxclick', onSideButton, true);
  window.addEventListener('popstate', onPopState);
}
