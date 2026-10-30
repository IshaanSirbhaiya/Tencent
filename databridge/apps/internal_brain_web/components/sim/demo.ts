'use client';
// Demo-proof features (D-24): live growth, "Add a document" and the engine refresh are shown ONLY
// when BOTH hold:
//   1. the server mounted them (GET /ib/status says demo_features: true, i.e. IB_DEMO_FEATURES=1 on
//      a machine that is not a deployment), and
//   2. this viewer turned "Demo features" on in Settings (localStorage, off by default) or the
//      Presenter is armed.
// Normal users never see them. The server enforces every rule on its own; this only hides controls.
import { useEffect, useSyncExternalStore } from 'react';
import { analyticsApi } from '@/lib/api';
import { usePresenter } from '@/components/presenter/store';

export const DEMO_KEY = 'ib-demo-features';
const EVENT = 'ib:demo-features';

let server: boolean | null = null;
let inflight: Promise<void> | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((f) => f());

function fetchServer(): Promise<void> {
  if (inflight) return inflight;
  const p = analyticsApi
    .status()
    .then((s: import('@/lib/types').IbStatus) => {
      server = s.demo_features === true;
    })
    .catch(() => {
      server = null; // unknown (signed out, offline): hidden, and asked again on the next mount
    })
    .finally(() => {
      inflight = null;
      emit();
    });
  inflight = p;
  return p;
}

function readLocal(): boolean {
  try {
    return window.localStorage.getItem(DEMO_KEY) === '1';
  } catch {
    return false;
  }
}

export function setDemoLocal(on: boolean) {
  try {
    if (on) window.localStorage.setItem(DEMO_KEY, '1');
    else window.localStorage.removeItem(DEMO_KEY);
  } catch {
    /* storage unavailable: the choice lasts for this page only */
  }
  localOverride = on;
  window.dispatchEvent(new Event(EVENT));
  emit();
}

let localOverride: boolean | null = null;

function subscribe(f: () => void) {
  listeners.add(f);
  const onStorage = (e: StorageEvent) => {
    if (e.key === DEMO_KEY) {
      localOverride = null;
      f();
    }
  };
  window.addEventListener('storage', onStorage);
  window.addEventListener(EVENT, f);
  return () => {
    listeners.delete(f);
    window.removeEventListener('storage', onStorage);
    window.removeEventListener(EVENT, f);
  };
}

const snapLocal = () => (localOverride ?? readLocal());
const snapServer = () => server;

export interface DemoFeatures {
  /** Show the demo controls (server on AND (Settings toggle on OR presenter armed)). */
  on: boolean;
  /** What /ib/status said: null until it answered. */
  server: boolean | null;
  /** The viewer's own Settings toggle. */
  local: boolean;
}

export function useDemoFeatures(): DemoFeatures {
  const local = useSyncExternalStore(subscribe, snapLocal, () => false);
  const srv = useSyncExternalStore(subscribe, snapServer, () => null);
  const presenter = usePresenter();
  useEffect(() => {
    if (server === null) void fetchServer();
  }, []);
  return { on: srv === true && (local || presenter.armed), server: srv, local };
}

/** Re-ask the server (after a sign-in change). */
export function refreshDemoFeatures() {
  server = null;
  void fetchServer();
}
