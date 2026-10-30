'use client';
// App-wide client state, mounted once in app/layout.tsx so it survives navigation:
//   - prefs (theme, language, text size, contrast, motion, mode) mirrored onto <html>;
//   - the session (`me` from /auth/whoami), cached between pages and revalidated by the shell;
//   - the Radix tooltip provider.
import { usePathname, useRouter } from 'next/navigation';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { LiveAnnouncer } from '@/components/ui/announce';
import { TooltipProvider } from '@/components/ui/overlays';
import { ApiError, api, forgetInFlight } from '@/lib/api';
import { claimSession } from '@/components/presenter/session';
import { LangContext } from '@/lib/i18n';
import { DEFAULT_PREFS, MODE_HOME, applyPrefs, loadPrefs, modeForPath, savePref, type Lang, type Mode, type Prefs } from '@/lib/prefs';
import type { WhoAmI } from '@/lib/types';

/* ---------------------------------------------------------------- prefs */

interface PrefsState {
  prefs: Prefs;
  /** Update one preference (persists, updates <html>). */
  setPref: <K extends keyof Prefs>(key: K, value: Prefs[K]) => void;
  /** The mode in effect on this page (the route wins over the stored choice). */
  mode: Mode;
  /** Switch mode, persist it and go to that mode's home page. */
  switchMode: (m: Mode, opts?: { navigate?: boolean }) => void;
  /** The resolved theme actually on screen. */
  theme: 'dark' | 'light';
}

const PrefsContext = createContext<PrefsState | null>(null);

export function usePrefs(): PrefsState {
  const v = useContext(PrefsContext);
  if (!v) throw new Error('usePrefs must be used inside <Providers>');
  return v;
}

/** Current mode ('user' | 'dev'). */
export function useMode(): Mode {
  return usePrefs().mode;
}

/* ---------------------------------------------------------------- session */

export type SessionStatus = 'loading' | 'ready' | 'signed-out' | 'error';

interface SessionState {
  me: WhoAmI | null;
  status: SessionStatus;
  /** Re-read /auth/whoami (after sign-in, a persona switch, or on page change). `force` starts a
   *  fresh request even while one is pending (the "Check again" button after a slow check). */
  refresh: (opts?: { force?: boolean }) => Promise<WhoAmI | null>;
  /** Forget the cached identity (sign-out, sign-in page). */
  clear: () => void;
  signOut: () => Promise<void>;
  /** Switch the main session to another demo persona (uses the passcode given at sign-in). */
  switchPersona: (id: string) => Promise<void>;
  hasRole: (role: string) => boolean;
}

const SessionContext = createContext<SessionState | null>(null);

export function useSession(): SessionState {
  const v = useContext(SessionContext);
  if (!v) throw new Error('useSession must be used inside <Providers>');
  return v;
}

/** The signed-in person, or null while loading / signed out. Same contract as the old useMe(). */
export function useMe(): WhoAmI | null {
  return useContext(SessionContext)?.me ?? null;
}

/* ---------------------------------------------------------------- identity caches */

/**
 * Forget what the previous person's session cached in this tab: the graph layout positions
 * (`ib-graph-layout:*` in sessionStorage name the nodes that person could see). Listeners such as
 * components/graph/layout.ts clear their in-memory copies on the `ib:identity-changed` event.
 */
/** An explicit sign-out or persona switch by the person: drops the presenter's cached Leo answer and
 *  deliverables (components/presenter/deliver.ts listens), so nothing is shown again unchecked. */
function announceSignedOut() {
  try {
    // the same key as components/presenter/deliver.ts (that module may not be loaded in this tab)
    window.sessionStorage.removeItem('ib.presenter.deliver');
  } catch {
    /* storage unavailable: nothing was cached there */
  }
  try {
    window.dispatchEvent(new Event('ib:signed-out'));
  } catch {
    /* no window (SSR) */
  }
}

function forgetIdentityCaches() {
  forgetInFlight();
  try {
    const s = window.sessionStorage;
    const drop: string[] = [];
    for (let i = 0; i < s.length; i++) {
      const k = s.key(i);
      if (k && k.startsWith('ib-graph-layout:')) drop.push(k);
    }
    drop.forEach((k) => s.removeItem(k));
  } catch {
    /* storage unavailable: nothing was cached there */
  }
  try {
    window.dispatchEvent(new Event('ib:identity-changed'));
  } catch {
    /* no window (SSR) */
  }
}

/* ---------------------------------------------------------------- provider */

export function Providers({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname() ?? '/';
  const [prefs, setPrefs] = useState<Prefs>(DEFAULT_PREFS);
  const [loaded, setLoaded] = useState(false);
  const [theme, setTheme] = useState<'dark' | 'light'>('dark');

  // Load stored prefs once (the inline init script already painted them).
  useEffect(() => {
    const p = loadPrefs();
    const q = new URLSearchParams(window.location.search).get('mode');
    if (q === 'dev' || q === 'user') {
      p.mode = q;
      savePref('mode', q);
      if (modeForPath(window.location.pathname, q) !== q) router.replace(MODE_HOME[q]);
    }
    setPrefs(p);
    setLoaded(true);
  }, [router]);

  useEffect(() => {
    if (!loaded) return;
    applyPrefs(prefs);
    setTheme(document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
  }, [prefs, loaded]);

  // Follow the OS theme while the preference is "system".
  useEffect(() => {
    if (prefs.theme !== 'system') return;
    let mq: MediaQueryList;
    try {
      mq = window.matchMedia('(prefers-color-scheme: light)');
    } catch {
      return;
    }
    const on = () => applyPrefs(prefs);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, [prefs]);

  const mode = modeForPath(pathname, prefs.mode);

  // The route decides the mode: remember it, and set data-mode for density.
  useEffect(() => {
    document.documentElement.setAttribute('data-mode', pathname.startsWith('/login') ? 'user' : mode);
    if (loaded && mode !== prefs.mode && (pathname === '/dev' || pathname.startsWith('/dev/'))) {
      savePref('mode', 'dev');
      setPrefs((p) => ({ ...p, mode: 'dev' }));
    }
  }, [mode, pathname, loaded, prefs.mode]);

  const setPref = useCallback(<K extends keyof Prefs>(key: K, value: Prefs[K]) => {
    savePref(key, value);
    setPrefs((p) => ({ ...p, [key]: value }));
  }, []);

  const switchMode = useCallback(
    (m: Mode, opts?: { navigate?: boolean }) => {
      savePref('mode', m);
      setPrefs((p) => ({ ...p, mode: m }));
      if (opts?.navigate !== false) router.push(MODE_HOME[m]);
    },
    [router],
  );

  const prefsValue = useMemo<PrefsState>(() => ({ prefs, setPref, mode, switchMode, theme }), [prefs, setPref, mode, switchMode, theme]);
  const langValue = useMemo(() => ({ lang: prefs.lang, setLang: (l: Lang) => setPref('lang', l) }), [prefs.lang, setPref]);

  /* session */
  const [me, setMe] = useState<WhoAmI | null>(null);
  const [status, setStatus] = useState<SessionStatus>('loading');
  const inflight = useRef<Promise<WhoAmI | null> | null>(null);

  const generation = useRef(0);

  const refresh = useCallback((opts?: { force?: boolean }) => {
    // `force` (the "Check again" button) drops a request that seems stuck and starts a new one;
    // only the answer to the LATEST request is applied, a late stale answer is ignored.
    if (opts?.force) inflight.current = null;
    if (!inflight.current) {
      const gen = ++generation.current;
      const p: Promise<WhoAmI | null> = api
        .whoami()
        .then((w) => {
          if (gen !== generation.current) return w;
          setMe(w);
          setStatus('ready');
          return w;
        })
        .catch((e: unknown) => {
          if (gen !== generation.current) return null;
          if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
            setMe(null);
            setStatus('signed-out');
          } else {
            setStatus('error');
          }
          return null;
        })
        .finally(() => {
          if (inflight.current === p) inflight.current = null;
        });
      inflight.current = p;
    }
    return inflight.current;
  }, []);

  const clear = useCallback(() => {
    setMe(null);
    setStatus('signed-out');
  }, []);

  const signOut = useCallback(async () => {
    try {
      // Queued with the presenter's session changes, last (round-4 Opus #1): a P warm-up running
      // now stops and never signs anyone back in after this.
      await claimSession(() => api.logout());
    } catch {
      /* the session may already be gone */
    }
    forgetIdentityCaches();
    announceSignedOut();
    setMe(null);
    setStatus('signed-out');
    router.replace('/login');
  }, [router]);

  const switchPersona = useCallback(
    async (id: string) => {
      await claimSession(() => api.switchTo(id)); // same queue as Sign out (round-4 Opus #1)
      forgetIdentityCaches();
      announceSignedOut();
      // force: a whoami that started before the switch must not win with the OLD identity.
      await refresh({ force: true });
      router.refresh();
    },
    [refresh, router],
  );

  // Any change of the signed-in person (including a fresh sign-in on /login) drops per-person caches.
  const lastSub = useRef<string | null>(null);
  useEffect(() => {
    const sub = me?.sub ?? null;
    if (sub && lastSub.current && sub !== lastSub.current) forgetIdentityCaches();
    if (sub) lastSub.current = sub;
  }, [me?.sub]);

  const sessionValue = useMemo<SessionState>(
    () => ({ me, status, refresh, clear, signOut, switchPersona, hasRole: (r: string) => !!me?.roles.includes(r) }),
    [me, status, refresh, clear, signOut, switchPersona],
  );

  return (
    <PrefsContext.Provider value={prefsValue}>
      <LangContext.Provider value={langValue}>
        <SessionContext.Provider value={sessionValue}>
          <TooltipProvider delayDuration={300}>
            {children}
            <LiveAnnouncer />
          </TooltipProvider>
        </SessionContext.Provider>
      </LangContext.Provider>
    </PrefsContext.Provider>
  );
}
