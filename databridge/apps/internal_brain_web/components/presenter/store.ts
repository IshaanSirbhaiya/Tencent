'use client';
// Presenter state: a tiny module store (useSyncExternalStore), so it survives client navigation
// between /compare, /dev/* and /present/*. Persisted in sessionStorage (this tab only, cleared when
// the tab closes). Client state only: nothing here is ever sent to the server (PRESENTER_KEYS §9).
import { useSyncExternalStore } from 'react';

export interface PresenterState {
  /** Presenter keys are live (WCAG 2.1.4: off unless armed). */
  armed: boolean;
  /** P was pressed: a step is current. */
  started: boolean;
  step: number;
  /** The Stage theme (light, 150 %). */
  stage: boolean;
  /** The bar is hidden (Esc). Keys still work. */
  hidden: boolean;
  /** A step action is running. */
  busy: boolean;
  /** The last step action did not finish (Space tries it again, → skips it). */
  failed: boolean;
  /** Reset was confirmed and waits for the running step to let go (not persisted). */
  resetting: boolean;
  /** Stage: the key legend row is showing (3 s after P, or toggled with ?). Not persisted. */
  legend: boolean;
  clipOpen: boolean;
  /** B on a step with no recording: the short "No recording for this step" note (not persisted). */
  clipNote: boolean;
  resetOpen: boolean;
  /** persona id → the latest request id that persona's column produced (for the journey step). */
  reqs: Record<string, string>;
  /** Status dots (not persisted): null until checked. */
  health: Health | null;
}

export type Dot = 'ok' | 'down' | 'unknown' | 'live' | 'quoting';
export interface Health {
  api: Dot;
  model: Dot;
  ledger: Dot;
}

const KEY = 'ib.presenter';
const INITIAL: PresenterState = {
  armed: false,
  started: false,
  step: 0,
  stage: false,
  hidden: false,
  busy: false,
  failed: false,
  resetting: false,
  legend: false,
  clipOpen: false,
  clipNote: false,
  resetOpen: false,
  reqs: {},
  health: null,
};

let state: PresenterState = INITIAL;
let loaded = false;
const listeners = new Set<() => void>();

function load() {
  if (loaded || typeof window === 'undefined') return;
  loaded = true;
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (raw) {
      const s = JSON.parse(raw) as Partial<PresenterState>;
      state = {
        ...INITIAL,
        armed: !!s.armed,
        started: !!s.started,
        step: typeof s.step === 'number' && s.step >= 0 ? Math.floor(s.step) : 0,
        stage: !!s.stage,
        reqs: s.reqs && typeof s.reqs === 'object' ? (s.reqs as Record<string, string>) : {},
      };
    }
  } catch {
    /* storage unavailable: start clean */
  }
  // Build-time or per-browser opt-in (PRESENTER_KEYS §1).
  if (process.env.NEXT_PUBLIC_IB_PRESENTER === '1') state = { ...state, armed: true };
  try {
    if (window.localStorage.getItem(KEY) === '1') state = { ...state, armed: true };
  } catch {
    /* storage unavailable */
  }
}

function persist() {
  try {
    const { armed, started, step, stage, reqs } = state;
    window.sessionStorage.setItem(KEY, JSON.stringify({ armed, started, step, stage, reqs }));
  } catch {
    /* storage unavailable: state lasts for this page only */
  }
}

export function getPresenter(): PresenterState {
  load();
  return state;
}

export function setPresenter(patch: Partial<PresenterState> | ((s: PresenterState) => Partial<PresenterState>)) {
  load();
  const p = typeof patch === 'function' ? patch(state) : patch;
  state = { ...state, ...p };
  persist();
  listeners.forEach((l) => l());
}

/** Forget everything except "armed" (Reset). */
export function clearPresenter() {
  load();
  state = { ...INITIAL, armed: state.armed, stage: state.stage, health: state.health };
  persist();
  listeners.forEach((l) => l());
}

/** Remember the request a persona's column just produced (only while presenting). */
export function rememberRequest(persona: string, reqId: string) {
  load();
  if (!state.armed) return;
  setPresenter((s) => ({ reqs: { ...s.reqs, [persona]: reqId } }));
}

/**
 * Forget the requests these personas' columns produced. Compare calls it when a run STARTS, so a
 * failed or cancelled run can never leave an earlier rehearsal's request behind for a later step.
 */
export function forgetRequests(personas: string[]) {
  load();
  if (!personas.some((p) => p in state.reqs)) return;
  setPresenter((s) => {
    const reqs = { ...s.reqs };
    for (const p of personas) delete reqs[p];
    return { reqs };
  });
}

const SERVER = INITIAL;
export function usePresenter(): PresenterState {
  return useSyncExternalStore(
    (l) => {
      load();
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => getPresenter(),
    () => SERVER,
  );
}
