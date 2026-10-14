'use client';
import { clsx } from 'clsx';
import { Loader2, LogOut, ShieldAlert } from 'lucide-react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { ApiError, api } from '@/lib/api';
import { personaById } from '@/lib/personas';
import type { WhoAmI } from '@/lib/types';
import { BrandMark, MockBanner, PRODUCT } from './Brand';
import { ThemeToggle } from './ThemeToggle';
import { TourButton, TourOffer } from './tour/Tour';

const MeContext = createContext<WhoAmI | null>(null);
export const useMe = () => useContext(MeContext);

export const ROLE_LABEL: Record<string, string> = {
  employee: 'Employee',
  compliance_officer: 'Compliance officer',
  admin_it: 'IT admin',
};

/** "Same question, two people" needs demo sign-in, which is always on in this demo build. */
export const DEMO_COMPARE = process.env.NEXT_PUBLIC_IB_DEMO_COMPARE !== '0';

export function AppShell({ children, requireRole }: { children: React.ReactNode; requireRole?: 'compliance_officer' | 'admin_it' }) {
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = useState<WhoAmI | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [signingOut, setSigningOut] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .whoami()
      .then((w) => live && setMe(w))
      .catch((e: unknown) => {
        if (!live) return;
        if (e instanceof ApiError && e.status === 401) router.replace('/login');
        else setFailed('The Internal Brain is temporarily unavailable. Try reloading in a moment.');
      });
    return () => {
      live = false;
    };
  }, [router]);

  const signOut = useCallback(async () => {
    setSigningOut(true);
    try {
      await api.logout();
    } catch {
      /* the session may already be gone */
    }
    router.replace('/login');
  }, [router]);

  const nav = [
    { href: '/', label: 'Ask', show: true },
    { href: '/map', label: 'Map', show: true },
    { href: '/compare', label: 'Compare', show: DEMO_COMPARE },
    { href: '/audit', label: 'Audit', show: !!me?.roles.includes('compliance_officer') },
    { href: '/sim', label: 'Simulator', show: !!me?.roles.includes('admin_it') },
  ].filter((n) => n.show);

  const persona = personaById(me?.sub);
  const who = persona ? `${persona.name} — ${persona.role}` : me?.name;

  return (
    <MeContext.Provider value={me}>
      <MockBanner />
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5 sm:px-6">
          <Link href="/" className="flex items-center gap-2.5 rounded-md">
            <BrandMark />
            <span className="text-sm font-semibold tracking-tight">{PRODUCT}</span>
          </Link>
          {me ? (
            <nav aria-label="Primary" className="order-3 w-full sm:order-none sm:w-auto">
              <ul className="flex flex-wrap items-center gap-1">
                {nav.map((n) => {
                  const active = n.href === '/' ? pathname === '/' : pathname.startsWith(n.href);
                  return (
                    <li key={n.href}>
                      <Link
                        href={n.href}
                        aria-current={active ? 'page' : undefined}
                        className={clsx(
                          'inline-flex h-8 items-center rounded-md px-3 text-sm',
                          active ? 'bg-raised font-medium text-fg' : 'text-muted hover:bg-raised hover:text-fg',
                        )}
                      >
                        {n.label}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </nav>
          ) : null}
          <div className="ml-auto flex items-center gap-2">
            {me ? (
              <div className="hidden text-right leading-tight sm:block" data-testid="whoami">
                <div className="text-sm font-medium">{who}</div>
                <div className="text-2xs text-muted">
                  Signed in as <span className="font-mono">{me.sub}</span> · {me.roles.map((r) => ROLE_LABEL[r] ?? r).join(', ')}
                </div>
              </div>
            ) : null}
            <TourButton />
            <ThemeToggle />
            {me ? (
              <button
                type="button"
                onClick={signOut}
                disabled={signingOut}
                className="inline-flex h-8 items-center gap-1.5 rounded-md border border-line-strong px-2.5 text-xs font-medium text-fg hover:bg-raised"
              >
                <LogOut aria-hidden className="h-3.5 w-3.5" />
                Sign out
              </button>
            ) : null}
          </div>
          {me ? (
            <p className="order-4 w-full text-2xs text-muted sm:hidden">
              {who} · <span className="font-mono">{me.sub}</span>
            </p>
          ) : null}
        </div>
      </header>
      <main id="main" tabIndex={-1} className="mx-auto max-w-7xl px-4 py-6 focus:outline-none sm:px-6">
        {failed ? (
          <p role="alert" className="rounded-lg border border-line bg-surface p-4 text-sm">
            {failed}
          </p>
        ) : !me ? (
          <p className="flex items-center gap-2 text-sm text-muted" role="status">
            <Loader2 aria-hidden className="h-4 w-4 motion-safe:animate-spin" /> Checking your session…
          </p>
        ) : requireRole && !me.roles.includes(requireRole) ? (
          <div role="alert" className="flex max-w-xl items-start gap-3 rounded-lg border border-line bg-surface p-4">
            <ShieldAlert aria-hidden className="mt-0.5 h-5 w-5 text-muted" />
            <div>
              <h1 className="text-base font-semibold">Not available for your role</h1>
              <p className="mt-1 text-sm text-muted">
                This page requires the {ROLE_LABEL[requireRole]} role.{' '}
                <Link className="text-accent underline underline-offset-2" href="/">
                  Go to Ask
                </Link>
              </p>
            </div>
          </div>
        ) : (
          <>
            <TourOffer className="mb-6" />
            {children}
          </>
        )}
      </main>
    </MeContext.Provider>
  );
}
