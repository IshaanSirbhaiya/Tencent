'use client';
import { useEffect, useRef, useSyncExternalStore } from 'react';

// The guided tours: a tiny module store (useSyncExternalStore) plus a page-action channel.
//
// Two tours, one per mode (components/tour/steps.ts holds the steps). They are separate (DECISIONS
// D-36): neither one starts, resumes or offers the other; each offers itself on first entry to its mode.
//   user  "Try it", 6 steps: sign in as Jordan, ask, open citation [1], open the answer as a page
//         beside the chat, change that page in plain words, Ken's two refusals; then a 3-item
//         checklist card (the page steps add no checklist item).
//   dev   5 steps: Mission control, the journey of your last answer, the knowledge graph, the Trust
//         centre, and (compliance) analytics + audit verify; Finish on the last step ends it.
//
// Storage (every access in try/catch; blocked storage still runs the tour, it just forgets):
//   sessionStorage ib-tour            the running tour (survives a full reload)
//   sessionStorage ib-tour-last-req   the request the tour asked, for the dev tour's journey step
//   localStorage   ib-tour-offer      {"user":true,"dev":false}: that mode's first-visit offer was answered;
//                                      the legacy value 'dismissed' (tests, the video recorder) means both
//   localStorage   ib-tour-done       {"user":true,"dev":false}; the legacy value '1' means both
//   localStorage   ib-tour-checklist  {"asked":true,...}

export type TourId = 'user' | 'dev';

/** Number of steps per tour (the user tour also has a closing checklist card at index TOUR_LENGTH.user). */
export const TOUR_LENGTH: Record<TourId, number> = { user: 6, dev: 5 };

export interface TourState {
  open: boolean;
  tour: TourId;
  /** 0-based step; for the user tour, TOUR_LENGTH.user is the checklist card. */
  step: number;
  /**
   * How the tour was entered:
   *  'signin'  from the sign-in page link: sign in as Jordan straight away;
   *  'start'   a fresh start: a first step that is already satisfied is skipped;
   *  null      normal navigation between steps.
   */
  entry: 'signin' | 'start' | null;
  /** Step ids completed in this run. */
  done: string[];
}

const KEY = 'ib-tour';
const OFFER_KEY = 'ib-tour-offer';
const DONE_KEY = 'ib-tour-done';
const CHECK_KEY = 'ib-tour-checklist';
const LAST_KEY = 'ib-tour-last-req';

const CLOSED: TourState = { open: false, tour: 'user', step: 0, entry: null, done: [] };
let state: TourState = CLOSED;
let loaded = false;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

function store(kind: 'local' | 'session'): Storage | null {
  try {
    return kind === 'local' ? window.localStorage : window.sessionStorage;
  } catch {
    return null;
  }
}
function read(kind: 'local' | 'session', key: string): string | null {
  try {
    return store(kind)?.getItem(key) ?? null;
  } catch {
    return null;
  }
}
function write(kind: 'local' | 'session', key: string, value: string | null) {
  try {
    const s = store(kind);
    if (!s) return;
    if (value === null) s.removeItem(key);
    else s.setItem(key, value);
  } catch {
    /* storage unavailable: the tour still runs, it just forgets */
  }
}

function load() {
  if (loaded || typeof window === 'undefined') return;
  loaded = true;
  const raw = read('session', KEY);
  if (!raw) return;
  try {
    const s = JSON.parse(raw) as Partial<TourState>;
    const tour: TourId = s.tour === 'dev' ? 'dev' : 'user';
    const max = TOUR_LENGTH[tour] + (tour === 'user' ? 1 : 0);
    if (typeof s.step === 'number' && s.step >= 0 && s.step < max) {
      state = { open: !!s.open, tour, step: s.step, entry: null, done: Array.isArray(s.done) ? s.done.filter((x) => typeof x === 'string') : [] };
    }
  } catch {
    /* ignore a malformed value */
  }
}

function set(next: TourState) {
  state = next;
  write('session', KEY, JSON.stringify(state));
  emit();
}

export function useTour(): TourState {
  return useSyncExternalStore(
    (l) => {
      load();
      listeners.add(l);
      if (state.open) queueMicrotask(l);
      return () => listeners.delete(l);
    },
    () => state,
    () => CLOSED,
  );
}

/** The tour for the page's mode: /dev/* is Developer mode; legacy pages keep the stored mode. */
function tourForPath(path: string): TourId {
  if (path === '/dev' || path.startsWith('/dev/')) return 'dev';
  if (/^\/(compare|history|settings|login)?(\/.*)?$/.test(path)) return 'user';
  return read('local', 'ib-mode') === 'dev' ? 'dev' : 'user';
}

/**
 * Start a tour.
 *  - startTour('user' | 'dev'): that tour from its first step (the header button passes the mode).
 *  - startTour(n): the current page's tour at step n (Settings → Replay tour, the sign-in link).
 *    From the sign-in page, step 0 signs in as Jordan straight away.
 */
export function startTour(arg: number | TourId = 0) {
  if (typeof arg === 'number') {
    const path = typeof window === 'undefined' ? '/' : window.location.pathname;
    const tour = tourForPath(path);
    dismissOffer(tour);
    const step = Math.max(0, Math.min(arg, TOUR_LENGTH[tour] - 1));
    set({ open: true, tour, step, entry: path === '/login' && tour === 'user' && step === 0 ? 'signin' : 'start', done: [] });
  } else {
    dismissOffer(arg);
    set({ open: true, tour: arg, step: 0, entry: 'start', done: [] });
  }
}

export function closeTour() {
  set({ ...state, open: false, entry: null });
}

/** Go to a step (beyond the last closes the tour; the user tour's index TOUR_LENGTH.user is the checklist). */
export function goToStep(step: number) {
  if (step < 0) return;
  const max = TOUR_LENGTH[state.tour] + (state.tour === 'user' ? 1 : 0);
  if (step >= max) closeTour();
  else set({ ...state, open: true, step, entry: null });
}

/** Clear the entry flag once the overlay has acted on it. */
export function consumeEntry() {
  if (state.entry) set({ ...state, entry: null });
}

export function markStepDone(id: string) {
  if (!state.done.includes(id)) set({ ...state, done: [...state.done, id] });
}

/* ------------------------------------------------------------ completion and the offer */

function doneMap(): Record<TourId, boolean> {
  const raw = read('local', DONE_KEY);
  if (raw === '1' || raw === 'true') return { user: true, dev: true };
  try {
    const v = raw ? (JSON.parse(raw) as Partial<Record<TourId, boolean>>) : {};
    return { user: !!v.user, dev: !!v.dev };
  } catch {
    return { user: false, dev: false };
  }
}

export function isTourDone(t: TourId): boolean {
  return doneMap()[t];
}

export function markTourDone(t: TourId) {
  const m = doneMap();
  if (m[t]) return;
  m[t] = true;
  write('local', DONE_KEY, JSON.stringify(m));
  emit();
}

function offerMap(): Record<TourId, boolean> {
  const raw = read('local', OFFER_KEY);
  if (raw === 'dismissed') return { user: true, dev: true };
  try {
    const v = raw ? (JSON.parse(raw) as Partial<Record<TourId, boolean>>) : {};
    return { user: !!v.user, dev: !!v.dev };
  } catch {
    return { user: false, dev: false };
  }
}

/** One-time gentle offer per tour, remembered per browser. No storage: do not nag on every page. */
export function offerDismissed(t: TourId): boolean {
  if (!store('local')) return true;
  return offerMap()[t];
}

/** Answer one tour's first-visit offer ("Not now", Take the tour, Skip). The other tour's offer is untouched. */
export function dismissOffer(t: TourId) {
  const m = offerMap();
  if (m[t]) return;
  m[t] = true;
  write('local', OFFER_KEY, JSON.stringify(m));
  emit();
}

/** Whether the first-visit offer for this tour should show (reactive: "Not now" hides it at once). */
export function useOfferOpen(t: TourId): boolean {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => !offerDismissed(t) && !isTourDone(t),
    () => false,
  );
}

/* ------------------------------------------------------------ the user tour's checklist */

// User mode only: switching to Developer mode is not an item (the tours are separate, D-36).
export type ChecklistItem = 'asked' | 'source' | 'identical';
export const CHECKLIST: ChecklistItem[] = ['asked', 'source', 'identical'];
export type Checklist = Record<ChecklistItem, boolean>;

const EMPTY_CHECKS: Checklist = { asked: false, source: false, identical: false };
let checks: Checklist | null = null;

function loadChecks(): Checklist {
  if (checks) return checks;
  checks = { ...EMPTY_CHECKS };
  const raw = read('local', CHECK_KEY);
  if (raw) {
    try {
      const v = JSON.parse(raw) as Partial<Checklist>;
      for (const k of CHECKLIST) checks[k] = !!v[k];
    } catch {
      /* ignore */
    }
  }
  return checks;
}

export function tick(item: ChecklistItem) {
  const c = loadChecks();
  if (c[item]) return;
  checks = { ...c, [item]: true };
  write('local', CHECK_KEY, JSON.stringify(checks));
  emit();
}

export function useChecklist(): Checklist {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => (typeof window === 'undefined' ? EMPTY_CHECKS : loadChecks()),
    () => EMPTY_CHECKS,
  );
}

/* ------------------------------------------------------------ the request the tour asked */

/** Remember the latest answer's request id (and who asked it) for the dev tour's journey step. */
export function rememberRequest(req: string, sub: string) {
  write('session', LAST_KEY, JSON.stringify({ req, sub }));
}

export function lastRequest(): { req: string; sub: string } | null {
  const raw = read('session', LAST_KEY);
  if (!raw) return null;
  try {
    const v = JSON.parse(raw) as { req?: unknown; sub?: unknown };
    return typeof v.req === 'string' && typeof v.sub === 'string' ? { req: v.req, sub: v.sub } : null;
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------ page actions */

// The tour asks a page to do something (ask the sample question, run the compare, verify the
// chain); the page performs it exactly as if the visitor had pressed the button.
const EVENT = 'ib-tour-action';
export function emitTourAction(action: string) {
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(EVENT, { detail: action }));
}
export function useTourAction(action: string, run: () => void) {
  const ref = useRef(run);
  ref.current = run;
  useEffect(() => {
    const h = (e: Event) => {
      if ((e as CustomEvent).detail === action) ref.current();
    };
    window.addEventListener(EVENT, h);
    return () => window.removeEventListener(EVENT, h);
  }, [action]);
}
