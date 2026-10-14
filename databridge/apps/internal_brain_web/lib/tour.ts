'use client';
import { useEffect, useRef, useSyncExternalStore } from 'react';

// The guided tour: a tiny module store (useSyncExternalStore) plus a page-action channel. Pattern
// ported from the original Walnut frontend (lib/demo.ts), made step-by-step instead of timed: the
// visitor moves with Next / Back, and each step highlights a real element marked data-tour="…".

export interface TourStep {
  id: string;
  /** Page the step lives on. */
  route: string;
  /** data-tour value of the element to highlight. */
  anchor: string;
  title: string;
  body: string;
  /** Sign in as this persona (normal demo login, main session) before showing the step. */
  persona?: string;
  /** Page action to run when the anchor is not on screen yet (e.g. ask the sample question). */
  action?: string;
  /** data-tour value that shows the page is mounted and listening for `action`. */
  ready?: string;
  /** Label for the Next button on this step. */
  next?: string;
  /** Shown while waiting for the anchor. */
  waiting?: string;
}

// Six steps on purpose: completion drops sharply past five or so. The simulator is a final
// "more to try" step. Captions: at most two short sentences, the first says what to look at.
export const TOUR_STEPS: TourStep[] = [
  {
    id: 'personas',
    route: '/login',
    anchor: 'personas',
    title: 'Pick a person',
    body: 'These are people at one made-up company, and each can open different things. We will start as Jordan, a backend engineer.',
    next: 'Sign in as Jordan',
  },
  {
    id: 'answer',
    route: '/',
    persona: 'jdoe',
    anchor: 'answer',
    ready: 'ask-form',
    action: 'ask-sample',
    waiting: 'Asking about the database migration…',
    title: 'Ask, and see where the answer came from',
    body: 'Look at the C-tags and the checks line at the bottom. Every source was checked live for Jordan, and the question is now in the audit log.',
  },
  {
    id: 'map',
    route: '/map',
    persona: 'jdoe',
    anchor: 'map',
    waiting: 'Checking each item with its source…',
    title: 'Everything Jordan can open',
    body: 'Each circle is a space, project, channel or shared drive, sized by how much is in it. Click one to see the items.',
  },
  {
    id: 'compare',
    route: '/compare',
    persona: 'jdoe',
    anchor: 'compare-results',
    ready: 'compare-form',
    action: 'compare-run',
    waiting: 'Asking for both people…',
    title: 'Same question, two people',
    body: "Compare Jordan's answer with Ken's, an outside contractor. Ken's answer uses only what Ken can open and gives no hint that more exists.",
    next: 'Continue as Maya',
  },
  {
    id: 'verify',
    route: '/audit',
    persona: 'maya.compliance',
    anchor: 'verify-panel',
    ready: 'verify-panel',
    action: 'verify',
    title: 'Check the audit log',
    body: 'Look at the result of Verify audit chain. It re-checks every entry, so an edited or deleted record would show up here.',
  },
  {
    id: 'sim',
    route: '/sim',
    persona: 'admin.it',
    anchor: 'sim-revoke',
    title: 'More to try: change access live',
    body: 'As the IT admin, remove Jordan from a Slack channel. Then sign in as Jordan and ask again: that channel no longer appears.',
    next: 'Done',
  },
];

export interface TourState {
  open: boolean;
  step: number;
}

const KEY = 'ib-tour';
const OFFER_KEY = 'ib-tour-offer';
let state: TourState = { open: false, step: 0 };
let loaded = false;
const listeners = new Set<() => void>();

function load() {
  if (loaded || typeof window === 'undefined') return;
  loaded = true;
  try {
    const raw = sessionStorage.getItem(KEY);
    if (raw) {
      const s = JSON.parse(raw) as TourState;
      if (typeof s.step === 'number' && s.step >= 0 && s.step < TOUR_STEPS.length) state = { open: !!s.open, step: s.step };
    }
  } catch {
    /* storage unavailable */
  }
}

function set(next: TourState) {
  state = next;
  try {
    sessionStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    /* storage unavailable */
  }
  listeners.forEach((l) => l());
}

const SERVER: TourState = { open: false, step: 0 };
export function useTour(): TourState {
  return useSyncExternalStore(
    (l) => {
      load();
      listeners.add(l);
      if (state.open) queueMicrotask(l);
      return () => listeners.delete(l);
    },
    () => state,
    () => SERVER,
  );
}

export function startTour(step = 0) {
  dismissOffer();
  set({ open: true, step });
}
export function closeTour() {
  set({ ...state, open: false });
}
export function goToStep(step: number) {
  if (step < 0) return;
  if (step >= TOUR_STEPS.length) closeTour();
  else set({ open: true, step });
}

// One-time gentle offer, remembered per browser.
export function offerDismissed(): boolean {
  try {
    return localStorage.getItem(OFFER_KEY) === 'dismissed';
  } catch {
    return true; // no storage: do not nag on every page
  }
}
export function dismissOffer() {
  try {
    localStorage.setItem(OFFER_KEY, 'dismissed');
  } catch {
    /* storage unavailable */
  }
  listeners.forEach((l) => l());
}

// Page actions: the tour asks a page to do something (ask the sample question, run the compare,
// verify the chain); the page performs it exactly as if the visitor had pressed the button.
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
