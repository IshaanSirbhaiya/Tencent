'use client';
// Q&A beat "Dev, D." (docs/finals/deliverables/review_fable.md §5): P's warm-up signs Leo in,
// asks Q_DELIVER and builds every deliverable kind the server offers for that one answer, then
// restores whoever was signed in. The result is cached in this tab for 12 minutes (the 5:00 run
// plus Q&A; round-4 review #7: a pre-built panel is never shown much later without a fresh,
// server-checked build; R rebuilds it). D signs Leo in and opens the cached panel at once: no model
// call inside a 15-second beat; Esc closes it and signs the previous person back in. Every build is
// still a real, ledgered request.
//
// Both only BORROW the main session (./session.ts): they put the previous person back only when no
// owner (the shell's Sign out or persona switch, a presenter step's sign-in, R) changed it
// meanwhile (round-4 review #1 and #13).
import { useSyncExternalStore } from 'react';
import { deliverablesStatus } from '@/components/user/DeliverSheet';
import { api, deliverApi } from '@/lib/api';
import { sessionOwner } from './session';
import type { AskResponse, Deliverable, DeliverableKind } from '@/lib/types';

export const DELIVER_PERSONA = 'leo.ops';
/** Cites the SG/HK/ID pricing sheet (drive:df-3) for Leo in the synthetic world (checked 30 Oct). */
export const Q_DELIVER = 'Can we offer multi-currency accounts to an Indonesian SME?';
export const DELIVER_TTL_MS = 12 * 60 * 1000;
const KEY = 'ib.presenter.deliver';

export interface DeliverCache {
  at: number;
  response: AskResponse;
  deliverables: Partial<Record<DeliverableKind, Deliverable>>;
  kinds: DeliverableKind[];
}

type Serial = <T>(fn: () => Promise<T>) => Promise<T>;

function readCache(): DeliverCache | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const c = JSON.parse(raw) as DeliverCache;
    return Date.now() - c.at < DELIVER_TTL_MS ? c : null;
  } catch {
    return null;
  }
}

function writeCache(c: DeliverCache | null) {
  try {
    if (c) sessionStorage.setItem(KEY, JSON.stringify(c));
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage blocked: D then builds on demand */
  }
}

export function clearDeliverCache() {
  epoch++;
  restore = null; // a sign-out or Reset owns the session now: nothing to put back
  writeCache(null);
  set({ open: false, cache: null });
}

// A warm-up that started before a sign-out must not write its result afterwards.
let epoch = 0;

// The person signing out (or switching persona from the shell) drops Leo's cached answer and
// deliverables: nothing is shown later without a fresh, server-checked build. The shell dispatches
// this event (components/shell/Providers.tsx); listening here avoids an import cycle.
if (typeof window !== 'undefined') window.addEventListener('ib:signed-out', () => clearDeliverCache());

let warming: Promise<DeliverCache | null> | null = null;

/** Build Leo's deliverables in the main session, then put the previous session back. */
export function warmDeliverables(serial: Serial): Promise<DeliverCache | null> {
  if (warming) return warming;
  const started = epoch;
  warming = serial(async () => {
    const owner = sessionOwner();
    // The shell signed out or switched persona meanwhile: stop, and never put anyone back over it.
    const taken = () => owner !== sessionOwner() || started !== epoch;
    let prev: string | null = null;
    try {
      prev = (await api.whoami()).sub;
    } catch {
      /* signed out */
    }
    let borrowed = false;
    try {
      if (taken()) return null;
      if (prev !== DELIVER_PERSONA) {
        borrowed = true;
        await api.switchTo(DELIVER_PERSONA);
      }
      const st = await deliverablesStatus(); // the kill switch is off: nothing to build
      if (!st || taken()) return null;
      const response = await api.ask(Q_DELIVER);
      if (!response.citations.length || taken()) return null;
      const deliverables: DeliverCache['deliverables'] = {};
      for (const k of st.kinds) {
        if (taken()) return null;
        try {
          deliverables[k] = await deliverApi.create(response.request_id, k);
        } catch {
          /* that tab builds on demand */
        }
      }
      const c: DeliverCache = { at: Date.now(), response, deliverables, kinds: st.kinds };
      if (taken()) return null; // signed out meanwhile
      writeCache(c);
      return c;
    } catch {
      return null;
    } finally {
      try {
        // Put back only what this warm-up changed, and only if nobody took the session over.
        if (borrowed && owner === sessionOwner()) {
          if (prev) await api.switchTo(prev);
          else await api.logout();
        }
      } catch {
        /* the next step signs its own persona in */
      }
    }
  }).finally(() => {
    warming = null;
  });
  return warming;
}

/* ------------------------------------------------------------- the overlay's state */

export interface DeliverOverlayState {
  open: boolean;
  cache: DeliverCache | null;
  kind: DeliverableKind;
}
let state: DeliverOverlayState = { open: false, cache: null, kind: 'table' };
const listeners = new Set<() => void>();
function set(next: Partial<DeliverOverlayState>) {
  state = { ...state, ...next };
  listeners.forEach((l) => l());
}
export function useDeliverOverlay(): DeliverOverlayState {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => state,
    () => state,
  );
}
export function setDeliverKind(kind: DeliverableKind) {
  set({ kind });
}
/** Puts back whoever was signed in before D (set by openDeliverables, used once on close). */
let restore: (() => Promise<unknown>) | null = null;

/**
 * Esc on the D overlay: close it and put the main session back to whoever was signed in before D
 * (round-3 review #2), as the warm-up does; otherwise ← to the journey would ask as Leo and read
 * "Request not found". Serialised, so a step's own sign-in after this always wins.
 */
export function closeDeliverOverlay() {
  set({ open: false });
  const r = restore;
  restore = null;
  if (r) void r().catch(() => undefined);
}

/**
 * D: Leo signed in, the cached panel open (Chart when the model wrote it, else Table).
 *
 * D only BORROWS the session, so it stops for good once anyone else changed it after D was pressed
 * (round-5 Codex #9): the shell's Sign out or persona switch (claimSession counts it at the click,
 * and its ib:signed-out bumps `epoch`), a presenter step's sign-in, or R. That is checked when D
 * starts its own queued turn, again after reading who is signed in and before switching to Leo,
 * and again before the panel opens. So D never signs Leo in, and never sets up a restore, after
 * a sign-out that was clicked while its warm-up was still building.
 */
export async function openDeliverables(serial: Serial, refresh: () => Promise<unknown> | undefined): Promise<boolean> {
  const startOwner = sessionOwner();
  const startEpoch = epoch;
  const changed = () => startOwner !== sessionOwner() || startEpoch !== epoch;
  const cache = readCache() ?? (await warmDeliverables(serial));
  if (!cache || changed()) return false;
  let prev: string | null = null;
  let owner = 0;
  let switched = false;
  await serial(async () => {
    if (changed()) return; // a Sign out queued ahead of this turn: leave it signed out
    try {
      prev = (await api.whoami()).sub;
    } catch {
      /* signed out */
    }
    if (changed()) return;
    if (prev !== DELIVER_PERSONA) await api.switchTo(DELIVER_PERSONA);
    owner = sessionOwner();
    switched = true;
    await refresh();
  });
  // A Sign out clicked while D's switch ran is queued after it and signs Leo out; D stays shut.
  if (!switched || changed()) return false;
  // D pressed again while open keeps the first "before" (restore is set only when none is pending)
  if (!restore && prev !== DELIVER_PERSONA) {
    const back = prev;
    restore = () =>
      serial(async () => {
        try {
          // A step, R or the shell signed someone in while the panel was open: that person stays.
          if (owner !== sessionOwner()) return;
          if (back) await api.switchTo(back);
          else await api.logout();
        } finally {
          await refresh();
        }
      });
  }
  const chart = cache.deliverables.chart;
  const kind: DeliverableKind = chart && chart.status === 'ready' && chart.generated === 'model' ? 'chart' : cache.kinds.includes('table') ? 'table' : cache.kinds[0];
  set({ open: true, cache, kind });
  return true;
}
