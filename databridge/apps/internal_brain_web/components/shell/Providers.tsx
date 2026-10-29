'use client';
// App-wide client state, mounted once in app/layout.tsx so it survives navigation:
//   - prefs (theme, language, text size, contrast, motion, mode) mirrored onto <html>;
//   - the session (`me` from /auth/whoami), cached between pages and revalidated by the shell;
//   - the Radix tooltip provider.
import { usePathname, useRouter } from 'next/navigation';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { TooltipProvider } from '@/components/ui/overlays';
import { ApiError, api } from '@/lib/api';
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
  /** Re-read /auth/whoami (after sign-in, a persona switch, or on page change). */
  refresh: () => Promise<WhoAmI | null>;
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

  const refresh = useCallback(() => {
    if (!inflight.current) {
      inflight.current = api
        .whoami()
        .then((w) => {
          setMe(w);
          setStatus('ready');
          return w;
        })
        .catch((e: unknown) => {
          if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
            setMe(null);
            setStatus('signed-out');
          } else {
            setStatus('error');
          }
          return null;
        })
        .finally(() => {
          inflight.current = null;
        });
    }
    return inflight.current;
  }, []);

  const clear = useCallback(() => {
    setMe(null);
    setStatus('signed-out');
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      /* the session may already be gone */
    }
    setMe(null);
    setStatus('signed-out');
    router.replace('/login');
  }, [router]);

  const switchPersona = useCallback(
    async (id: string) => {
      await api.switchTo(id);
      await refresh();
      router.refresh();
    },
    [refresh, router],
  );

  const sessionValue = useMemo<SessionState>(
    () => ({ me, status, refresh, clear, signOut, switchPersona, hasRole: (r: string) => !!me?.roles.includes(r) }),
    [me, status, refresh, clear, signOut, switchPersona],
  );

  return (
    <PrefsContext.Provider value={prefsValue}>
      <LangContext.Provider value={langValue}>
        <SessionContext.Provider value={sessionValue}>
          <TooltipProvider delayDuration={300}>{children}</TooltipProvider>
        </SessionContext.Provider>
      </LangContext.Provider>
    </PrefsContext.Provider>
  );
}
