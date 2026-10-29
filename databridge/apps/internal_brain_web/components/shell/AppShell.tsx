'use client';
// The Walnut DataBridge frame: skip link target, top bar (banner), mode-specific left nav,
// main landmark, mobile nav sheet and the command palette.
//
// Usage
//   - Route layouts (app/dev/layout.tsx, app/(user)/layout.tsx) render <AppShell container={false}>
//     and their pages render <Page title=…>…</Page>.
//   - Legacy pages still wrap themselves in <AppShell requireRole=…> (via components/AppShell.tsx).
//   - Nesting is safe: an AppShell inside another renders only its role gate and children.
import { Loader2, Menu } from 'lucide-react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { createContext, useContext, useEffect, useState } from 'react';
import { MockBanner, Wordmark } from '@/components/Brand';
import { TourButton, TourOffer } from '@/components/tour/Tour';
import { IconButton, SegmentedControl, Sheet, SheetContent } from '@/components/ui';
import { cn } from '@/lib/cn';
import { useI18n } from '@/lib/i18n';
import { MODE_HOME, type Lang } from '@/lib/prefs';
import { CommandPalette } from './CommandPalette';
import type { Role } from './nav';
import { usePrefs, useSession } from './Providers';
import { RequireRole } from './RoleGate';
import { LanguageSwitch, ModeSwitch, PersonaMenu, SearchButton, ThemeSwitch } from './ShellControls';
import { SideNav } from './SideNav';

const FrameContext = createContext(false);

export interface AppShellProps {
  children: React.ReactNode;
  /** Only people with this role see the children; others get the lock card. */
  requireRole?: Role;
  /** 'app' needs a session (redirects to /login on 401); 'public' is for the sign-in page. */
  variant?: 'app' | 'public';
  /** Wrap children in the legacy max-w-7xl container (old pages). Route layouts pass false and use <Page>. */
  container?: boolean;
}

export function AppShell(props: AppShellProps) {
  const nested = useContext(FrameContext);
  if (nested) {
    return props.requireRole ? <RequireRole role={props.requireRole}>{props.children}</RequireRole> : <>{props.children}</>;
  }
  return (
    <FrameContext.Provider value={true}>
      <Frame {...props} />
    </FrameContext.Provider>
  );
}

function Frame({ children, requireRole, variant = 'app', container = true }: AppShellProps) {
  const router = useRouter();
  const pathname = usePathname() ?? '/';
  const { t } = useI18n();
  const { mode } = usePrefs();
  const { me, status, refresh } = useSession();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const isPublic = variant === 'public';

  // Revalidate the identity on every page (a persona switch or sign-out elsewhere must show).
  // Redirect only on the answer to THIS page's check, never on a stale cached status (the tour
  // signs in without going through the session, then navigates here).
  const [checked, setChecked] = useState(false);
  useEffect(() => {
    if (isPublic) return;
    let live = true;
    void refresh().then(() => live && setChecked(true));
    return () => {
      live = false;
    };
  }, [pathname, isPublic, refresh]);

  useEffect(() => {
    if (!isPublic && checked && status === 'signed-out') router.replace('/login');
  }, [status, checked, isPublic, router]);

  const showNav = !isPublic && !!me;

  let body: React.ReactNode;
  if (isPublic) body = children;
  else if (!me && status === 'error')
    body = (
      <p role="alert" className="m-6 rounded-lg border border-line bg-surface p-4 text-sm">
        {t('shell.unavailable')}
      </p>
    );
  else if (!me)
    body = (
      <p className="m-6 flex items-center gap-2 text-sm text-muted" role="status">
        <Loader2 aria-hidden className="h-4 w-4 motion-safe:animate-spin" /> {t('shell.checkingSession')}
      </p>
    );
  else {
    const gated = requireRole ? <RequireRole role={requireRole}>{children}</RequireRole> : children;
    body = container ? (
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        {mode === 'user' ? <TourOffer className="mb-6" /> : null}
        {gated}
      </div>
    ) : (
      <>
        {mode === 'user' ? (
          <div className="mx-auto max-w-6xl px-4 pt-6 sm:px-6 lg:px-8">
            <TourOffer />
          </div>
        ) : null}
        {gated}
      </>
    );
  }

  return (
    <div
      className="flex min-h-screen flex-col"
      data-shell={variant}
      style={process.env.NEXT_PUBLIC_IB_MOCK === '1' ? ({ '--banner-h': '1.5625rem' } as React.CSSProperties) : undefined}
    >
      <MockBanner />
      <header className="sticky top-0 z-30 flex h-topbar shrink-0 items-center gap-2 border-b border-line bg-bg px-3 sm:gap-3 sm:px-4">
        {showNav ? <IconButton icon={Menu} label={t('shell.openNav')} onClick={() => setNavOpen(true)} className="lg:hidden" aria-expanded={navOpen} /> : null}
        <Link href={isPublic ? '/login' : MODE_HOME[mode]} className="rounded-md" aria-label={`${t('app.name')}, ${t('shell.home')}`}>
          <Wordmark compact />
        </Link>
        {showNav ? (
          <>
            <span aria-hidden className="mx-1 hidden h-5 w-px bg-line sm:block" />
            <ModeSwitch className="hidden sm:inline-flex" />
          </>
        ) : null}
        <div className="ml-auto flex items-center gap-1 sm:gap-1.5">
          {showNav ? <SearchButton onOpen={() => setPaletteOpen(true)} /> : null}
          <div className={cn('items-center gap-1', showNav ? 'hidden lg:flex' : 'flex')}>
            <LanguageSwitch />
            <ThemeSwitch />
          </div>
          <TourButton />
          {showNav ? <PersonaMenu /> : null}
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {showNav ? (
          <aside className="sticky top-topbar hidden h-[calc(100dvh-var(--topbar-h)-var(--banner-h,0px))] w-sidenav shrink-0 border-r border-line bg-bg lg:block">
            <SideNav />
          </aside>
        ) : null}
        <main id="main" tabIndex={-1} className="min-w-0 flex-1 focus:outline-none">
          {body}
        </main>
      </div>

      {showNav ? (
        <>
          <Sheet open={navOpen} onOpenChange={setNavOpen}>
            <SheetContent side="left" width="sm" title={t('app.name')} closeLabel={t('shell.closeNav')} className="max-w-[20rem]" bodyClassName="p-0">
              <div className="flex flex-col gap-3 border-b border-line px-4 py-3">
                <ModeSwitch />
                <MobilePrefs />
              </div>
              <SideNav onNavigate={() => setNavOpen(false)} />
            </SheetContent>
          </Sheet>
          <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
        </>
      ) : null}
    </div>
  );
}

/** Language + theme inside the mobile nav sheet (the top bar has no room for them at 390px). */
function MobilePrefs() {
  const { t, lang, setLang } = useI18n();
  const { theme, setPref } = usePrefs();
  return (
    <div className="flex flex-wrap items-center gap-2">
      <SegmentedControl<Lang>
        size="sm"
        label={t('lang.label')}
        value={lang}
        onChange={setLang}
        options={[
          { value: 'en', label: 'English' },
          { value: 'zh', label: '简体中文' },
        ]}
      />
      <SegmentedControl<'dark' | 'light'>
        size="sm"
        label={t('theme.label')}
        value={theme}
        onChange={(v) => setPref('theme', v)}
        options={[
          { value: 'dark', label: t('theme.dark') },
          { value: 'light', label: t('theme.light') },
        ]}
      />
    </div>
  );
}
