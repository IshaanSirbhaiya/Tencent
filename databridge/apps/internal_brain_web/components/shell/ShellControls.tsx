'use client';
// Top-bar controls: ModeSwitch, LanguageSwitch, ThemeSwitch, PersonaMenu, SearchButton.
import { Check, Code2, Globe, LogOut, Moon, MoreHorizontal, Search, Settings, Sun, User, UserRoundCog } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';
import {
  Avatar,
  Badge,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
  IconButton,
  Kbd,
  SegmentedControl,
} from '@/components/ui';
import { cn } from '@/lib/cn';
import { useI18n, type Lang } from '@/lib/i18n';
import { PERSONA_ZH } from '@/lib/i18n/user';
import { PERSONAS, personaById, type Persona } from '@/lib/personas';
import type { Mode } from '@/lib/prefs';
import { loginHref } from './nextPath';
import { usePrefs, useSession } from './Providers';

/** User / Developer segmented control (radio group). Changing it routes to the mode's home. */
export function ModeSwitch({ className, iconOnly }: { className?: string; iconOnly?: boolean }) {
  const { mode, switchMode } = usePrefs();
  const { t } = useI18n();
  return (
    <SegmentedControl<Mode>
      label={t('mode.label')}
      value={mode}
      onChange={(m) => m !== mode && switchMode(m)}
      testId="mode-switch"
      className={className}
      options={[
        { value: 'user', label: t('mode.user'), icon: User, title: t('mode.userHint'), iconOnly },
        { value: 'dev', label: t('mode.dev'), icon: Code2, title: t('mode.devHint'), iconOnly },
      ]}
    />
  );
}

export function LanguageSwitch() {
  const { lang, setLang, t } = useI18n();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={`${t('lang.label')}: ${t(lang === 'zh' ? 'lang.zh' : 'lang.en')}`}
          className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs font-medium text-muted hover:bg-raised hover:text-fg data-[state=open]:bg-raised data-[state=open]:text-fg"
        >
          <Globe aria-hidden className="h-4 w-4" />
          <span aria-hidden className="hidden xl:inline">
            {lang === 'zh' ? '中文' : 'EN'}
          </span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[10rem]">
        <DropdownMenuLabel>{t('lang.label')}</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={lang} onValueChange={(v) => setLang(v as Lang)}>
          <DropdownMenuRadioItem value="en" lang="en">
            English
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="zh" lang="zh-Hans">
            简体中文
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** One-click dark/light toggle (System lives in Settings). */
export function ThemeSwitch() {
  const { theme, setPref } = usePrefs();
  const { t } = useI18n();
  const next = theme === 'dark' ? 'light' : 'dark';
  return (
    <IconButton
      icon={theme === 'dark' ? Sun : Moon}
      label={t('theme.switchTo', { theme: t(next === 'light' ? 'theme.light' : 'theme.dark').toLowerCase() })}
      onClick={() => setPref('theme', next)}
      data-testid="theme-switch"
    />
  );
}

/** Opens the command palette; shows the shortcut. */
export function SearchButton({ onOpen, compact }: { onOpen: () => void; compact?: boolean }) {
  const { t } = useI18n();
  const [mac] = useState(() => typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform));
  if (compact)
    return (
      <IconButton icon={Search} label={t('shell.commandPalette')} onClick={onOpen} aria-keyshortcuts="Control+K Meta+K" className="hidden md:inline-flex" />
    );
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={t('shell.commandPalette')}
      aria-keyshortcuts="Control+K Meta+K"
      className="hidden h-8 w-56 items-center gap-2 rounded-md border border-line bg-surface px-2.5 text-left text-xs text-subtle hover:border-line-strong hover:text-muted md:inline-flex"
    >
      <Search aria-hidden className="h-3.5 w-3.5" />
      <span className="flex-1 truncate">{t('shell.search')}</span>
      <span aria-hidden className="flex gap-0.5">
        <Kbd>{mac ? '⌘' : 'Ctrl'}</Kbd>
        <Kbd>K</Kbd>
      </span>
    </button>
  );
}

/**
 * Language and theme folded into one "More options" menu, used by the top bar when it has no room
 * for them (large text sizes, browser zoom, narrow windows).
 */
export function ShellOverflowMenu({ className }: { className?: string }) {
  const { lang, setLang, t } = useI18n();
  const { theme, setPref } = usePrefs();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={t('shell.moreActions')}
          title={t('shell.moreActions')}
          data-testid="shell-overflow"
          className={cn(
            'inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted hover:bg-raised hover:text-fg data-[state=open]:bg-raised data-[state=open]:text-fg',
            className,
          )}
        >
          <MoreHorizontal aria-hidden className="h-4 w-4" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[12rem]">
        <DropdownMenuLabel>{t('lang.label')}</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={lang} onValueChange={(v) => setLang(v as Lang)}>
          <DropdownMenuRadioItem value="en" lang="en">
            English
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="zh" lang="zh-Hans">
            简体中文
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <DropdownMenuLabel>{t('theme.label')}</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={theme} onValueChange={(v) => setPref('theme', v as 'dark' | 'light')}>
          <DropdownMenuRadioItem value="dark">{t('theme.dark')}</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="light">{t('theme.light')}</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** The persona's job title in the current language (PERSONA_ZH holds the 简体中文 roles). */
export function personaRole(p: Persona, lang: Lang): string {
  return lang === 'zh' ? PERSONA_ZH[p.id]?.role ?? p.role : p.role;
}

/** Who am I, roles, switch persona, settings, sign out. data-testid="whoami" holds name + sub. */
export function PersonaMenu({ compact }: { compact?: boolean }) {
  const { me, signOut, switchPersona } = useSession();
  const { t, lang } = useI18n();
  const [busy, setBusy] = useState(false);
  if (!me) return null;
  const persona = personaById(me.sub);
  const name = persona?.name ?? me.name;
  const roleText = me.roles.map((r) => t(`role.${r}` as Parameters<typeof t>[0]) ?? r).join(', ');
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={t('persona.menu', { name })}
          className="inline-flex h-9 max-w-[16rem] items-center gap-2 rounded-md px-1 text-left hover:bg-raised data-[state=open]:bg-raised md:pr-2"
        >
          <Avatar name={name} />
          <span data-testid="whoami" className={cn('sr-only min-w-0 leading-tight', !compact && 'md:not-sr-only md:block')}>
            <span className="block truncate text-xs font-medium text-fg">{persona ? `${persona.name} — ${personaRole(persona, lang)}` : me.name}</span>
            <span className="block truncate font-mono text-micro text-subtle">{me.sub}</span>
          </span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72">
        <div className="flex items-start gap-3 px-2 py-2">
          <Avatar name={name} size="lg" />
          <div className="min-w-0">
            <p className="text-micro font-semibold uppercase tracking-wide text-subtle">{t('persona.signedInAs')}</p>
            <p className="truncate text-sm font-medium text-fg">{name}</p>
            {persona ? <p className="truncate text-caption text-muted">{personaRole(persona, lang)}</p> : null}
            <p className="truncate font-mono text-micro text-subtle">{me.sub}</p>
            <div className="mt-1.5 flex flex-wrap gap-1" aria-label={`${t('persona.roles')}: ${roleText}`}>
              {me.roles.map((r) => (
                <Badge key={r} tone={r === 'employee' ? 'neutral' : 'accent'}>
                  {t(`role.${r}` as Parameters<typeof t>[0])}
                </Badge>
              ))}
            </div>
          </div>
        </div>
        <DropdownMenuSeparator />
        <DropdownMenuSub>
          <DropdownMenuSubTrigger disabled={busy}>
            <UserRoundCog aria-hidden className="h-4 w-4 text-muted" />
            {t('persona.switch')}
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent className="max-h-[70vh] w-64 overflow-y-auto">
            {PERSONAS.map((p) => (
              <DropdownMenuItem
                key={p.id}
                disabled={busy}
                onSelect={async () => {
                  if (p.id === me.sub) return;
                  setBusy(true);
                  try {
                    await switchPersona(p.id);
                  } catch {
                    window.location.assign(loginHref(`${window.location.pathname}${window.location.search}`));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                <Avatar name={p.name} size="sm" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm">{p.name}</span>
                  <span className="block truncate text-micro text-subtle">{personaRole(p, lang)}</span>
                </span>
                {p.id === me.sub ? (
                  <>
                    <Check aria-hidden className="h-3.5 w-3.5 text-accent" />
                    <span className="sr-only">{t('persona.current')}</span>
                  </>
                ) : null}
              </DropdownMenuItem>
            ))}
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        <DropdownMenuItem asChild>
          <Link href="/settings">
            <Settings aria-hidden className="h-4 w-4 text-muted" />
            {t('nav.settings')}
          </Link>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => void signOut()}>
          <LogOut aria-hidden className="h-4 w-4 text-muted" />
          {t('persona.signOut')}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

