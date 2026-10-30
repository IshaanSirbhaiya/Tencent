'use client';
// The Walnut DataBridge frame: skip link target, top bar (banner), mode-specific left nav,
// main landmark, mobile nav sheet and the command palette.
//
// Usage
//   - Route layouts (app/dev/layout.tsx, app/(user)/layout.tsx) render <AppShell container={false}>
//     and their pages render <Page title=…>…</Page>.
//   - Legacy pages still wrap themselves in <AppShell requireRole=…> (via components/AppShell.tsx).
//   - Nesting is safe: an AppShell inside another renders only its role gate and children.
import { Languages, Loader2, Menu, RefreshCw } from 'lucide-react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { MockBanner, Wordmark } from '@/components/Brand';
import { TourButton } from '@/components/tour/Tour';
import { Button, IconButton, SegmentedControl, Sheet, SheetContent } from '@/components/ui';
import { cn } from '@/lib/cn';
import { useI18n } from '@/lib/i18n';
import { MODE_HOME, type Lang } from '@/lib/prefs';
import { CommandPalette } from './CommandPalette';
import type { Role } from './nav';
import { loginHref } from './nextPath';
import { usePrefs, useSession } from './Providers';
import { RequireRole } from './RoleGate';
import { LanguageSwitch, ModeSwitch, PersonaMenu, SearchButton, ShellOverflowMenu, ThemeSwitch } from './ShellControls';
import { SideNav } from './SideNav';

const FrameContext = createContext(false);

/** After this long without an answer from the session check, offer "Check again". */
const SESSION_SLOW_MS = 8000;

/**
 * Developer pages that have no 简体中文 text at all (Developer mode stays English by design, D-12).
 * In 简体中文 their <main> is marked lang="en" so screen readers switch voice. The other Developer
 * pages (audit, graph, engine, simulator, trust, value) mix translated chrome with English data,
 * so they keep the document language.
 */
const DEV_ENGLISH_ONLY = /^\/dev(\/(journey|analytics|styleguide)(\/.*)?)?$/;

/**
 * Top-bar density. The bar measures itself and steps down until nothing overflows, so it holds at
 * any width, at Text size 115/130 % and at browser zoom up to 400 %:
 *   0 full · 1 icon-only search, account and tour · 2 language + theme folded into "More options",
 *   icon-only mode switch · 3 the mode switch moves into the navigation sheet.
 */
type Density = 0 | 1 | 2 | 3;
const useIsoLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

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
  const { t, lang } = useI18n();
  const { mode } = usePrefs();
  const { me, status, refresh } = useSession();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const isPublic = variant === 'public';

  // Revalidate the identity on every page (a persona switch or sign-out elsewhere must show).
  // Redirect only on the answer to THIS page's check, never on a stale cached status (the tour
  // signs in without going through the session, then navigates here): `checked` is reset first.
  const [checked, setChecked] = useState(false);
  useEffect(() => {
    if (isPublic) return;
    let live = true;
    setChecked(false);
    void refresh().then(() => live && setChecked(true));
    return () => {
      live = false;
    };
  }, [pathname, isPublic, refresh]);

  // Signed out: go to sign-in and come BACK here afterwards (?next= carries the same-site path).
  useEffect(() => {
    if (isPublic || !checked || status !== 'signed-out') return;
    router.replace(loginHref(`${window.location.pathname}${window.location.search}`));
  }, [status, checked, isPublic, router]);

  // A check that has not answered after 8 s shows a message and a "Check again" button.
  const waiting = !isPublic && !me && status !== 'error';
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (!waiting) {
      setSlow(false);
      return;
    }
    const id = setTimeout(() => setSlow(true), SESSION_SLOW_MS);
    return () => clearTimeout(id);
  }, [waiting, pathname]);
  const retry = useCallback(() => {
    setSlow(false);
    setChecked(false);
    void refresh({ force: true }).then(() => setChecked(true));
  }, [refresh]);

  const showNav = !isPublic && !!me;

  /* top-bar density: measure, then step down until the bar fits */
  const headerRef = useRef<HTMLElement>(null);
  const [density, setDensity] = useState<Density>(0);
  const [measureKey, setMeasureKey] = useState(0);
  useEffect(() => {
    const again = () => {
      setDensity(0);
      setMeasureKey((k) => k + 1);
    };
    window.addEventListener('resize', again);
    // Text size and language live on <html> (data-text, lang): re-measure when they change.
    const mo = new MutationObserver(again);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-text', 'lang', 'data-mode'] });
    return () => {
      window.removeEventListener('resize', again);
      mo.disconnect();
    };
  }, []);
  useEffect(() => {
    setDensity(0);
    setMeasureKey((k) => k + 1);
  }, [showNav, me?.sub, lang]);
  useIsoLayoutEffect(() => {
    const el = headerRef.current;
    if (!el) return;
    if (el.scrollWidth > el.clientWidth + 1 && density < 3) setDensity((d) => (d < 3 ? ((d + 1) as Density) : d));
  }, [density, measureKey, showNav]);

  // Developer mode in 简体中文: English-only pages say so and carry lang="en".
  const devEnglish = showNav && mode === 'dev' && lang === 'zh';
  const mainLang = devEnglish && DEV_ENGLISH_ONLY.test(pathname) ? 'en' : undefined;

  let body: React.ReactNode;
  if (isPublic) body = children;
  else if (!me && status === 'error')
    body = (
      <div role="alert" className="m-6 flex max-w-xl flex-wrap items-center gap-3 rounded-lg border border-line bg-surface p-4 text-sm">
        <p className="min-w-0 flex-1">{t('shell.unavailable')}</p>
        <Button size="sm" icon={RefreshCw} onClick={retry} data-testid="session-retry">
          {t('shell.retrySession')}
        </Button>
      </div>
    );
  else if (!me)
    body = (
      <div className="m-6 max-w-xl space-y-3 text-sm" data-testid="session-check">
        <p className="flex items-center gap-2 text-muted" role="status">
          <Loader2 aria-hidden className="h-4 w-4 motion-safe:animate-spin" /> {t('shell.checkingSession')}
        </p>
        {slow ? (
          <div role="alert" className="flex flex-wrap items-center gap-3 rounded-lg border border-line bg-surface p-4" data-testid="session-slow">
            <p className="min-w-0 flex-1 text-fg">{t('shell.slowSession')}</p>
            <Button size="sm" icon={RefreshCw} onClick={retry} data-testid="session-retry">
              {t('shell.retrySession')}
            </Button>
          </div>
        ) : null}
      </div>
    );
  else {
    const gated = requireRole ? <RequireRole role={requireRole}>{children}</RequireRole> : children;
    body = container ? <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6">{gated}</div> : gated;
  }

  const showHamburger = showNav;
  return (
    <div
      className="flex min-h-screen min-w-0 flex-col"
      data-shell={variant}
      data-density={density}
      style={process.env.NEXT_PUBLIC_IB_MOCK === '1' ? ({ '--banner-h': '1.5625rem' } as React.CSSProperties) : undefined}
    >
      {/* A named region, so the mock-mode line is inside a landmark like everything else. */}
      {process.env.NEXT_PUBLIC_IB_MOCK === '1' ? (
        <section aria-label={t('shell.mockNotice')}>
          <MockBanner />
        </section>
      ) : null}
      <header
        ref={headerRef}
        data-testid="topbar"
        className="sticky top-0 z-30 flex h-topbar min-w-0 shrink-0 items-center gap-2 border-b border-line bg-bg px-3 sm:gap-3 sm:px-4"
      >
        {showHamburger ? (
          <IconButton
            icon={Menu}
            label={t('shell.openNav')}
            onClick={() => setNavOpen(true)}
            className={cn('shrink-0', density < 3 && 'lg:hidden')}
            aria-expanded={navOpen}
          />
        ) : null}
        <Link href={isPublic ? '/login' : MODE_HOME[mode]} className="shrink-0 rounded-md" aria-label={`${t('app.name')}, ${t('shell.home')}`}>
          <Wordmark compact />
        </Link>
        {showNav && density < 3 ? (
          <>
            <span aria-hidden className="mx-1 hidden h-5 w-px shrink-0 bg-line sm:block" />
            <ModeSwitch className="hidden sm:inline-flex" iconOnly={density >= 2} />
          </>
        ) : null}
        <div className="ml-auto flex shrink-0 items-center gap-1 sm:gap-1.5">
          {showNav ? <SearchButton onOpen={() => setPaletteOpen(true)} compact={density >= 1} /> : null}
          {density >= 2 ? (
            <ShellOverflowMenu className={showNav ? 'hidden lg:inline-flex' : undefined} />
          ) : (
            <div className={cn('items-center gap-1', showNav ? 'hidden lg:flex' : 'flex')}>
              <LanguageSwitch />
              <ThemeSwitch />
            </div>
          )}
          {/* At density 1+ the tour button keeps its name for screen readers but shows only its icon. */}
          <TourButton className={cn('shrink-0', density >= 1 && 'w-8 justify-center gap-0 px-0 [&>span]:sr-only')} />
          {showNav ? <PersonaMenu compact={density >= 1} /> : null}
        </div>
      </header>

      <div className="flex min-h-0 min-w-0 flex-1">
        {showNav ? (
          <aside aria-label={t('shell.sidebar')} data-sidebar className="sticky top-topbar hidden h-[calc(100dvh-var(--topbar-h)-var(--banner-h,0px))] w-sidenav shrink-0 overflow-hidden border-r border-line bg-bg transition-[width] duration-slow ease-out lg:block">
            <SideNav />
          </aside>
        ) : null}
        <main id="main" tabIndex={-1} lang={mainLang} className="min-w-0 flex-1 focus:outline-none">
          {devEnglish ? (
            <p
              lang="zh-Hans"
              data-testid="dev-english-note"
              className="flex items-center gap-1.5 border-b border-line bg-surface px-4 py-1.5 text-caption text-muted sm:px-6 lg:px-8"
            >
              <Languages aria-hidden className="h-3.5 w-3.5 shrink-0 text-subtle" />
              {t('shell.devEnglishOnly')}
            </p>
          ) : null}
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
