'use client';
// Command palette (Ctrl/Cmd+K): go to pages of either mode, switch mode, theme, language, text size.
// cmdk handles filtering and arrow-key selection; Radix Dialog handles focus trap, Esc and focus return.
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { Command } from 'cmdk';
import { Code2, Globe, Lock, LogOut, Moon, Search, Sun, Type, User } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
import { useI18n } from '@/lib/i18n';
import { NAV, DEV_FOOTER, type NavItem } from './nav';
import { usePrefs, useSession } from './Providers';

const itemCls =
  'flex min-h-[2.25rem] cursor-default select-none items-center gap-2.5 rounded-md px-2.5 text-sm text-fg data-[selected=true]:bg-raised data-[disabled=true]:opacity-50';
const groupCls = '[&_[cmdk-group-heading]]:px-2.5 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:pt-2 [&_[cmdk-group-heading]]:text-micro [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-subtle';

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const router = useRouter();
  const { t, lang, setLang } = useI18n();
  const { mode, switchMode, theme, setPref, prefs } = usePrefs();
  const { me, signOut } = useSession();
  const roles = me?.roles ?? [];

  // Global shortcut.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        onOpenChange(!open);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onOpenChange]);

  const run = (fn: () => void) => {
    onOpenChange(false);
    fn();
  };

  const pageItem = (i: NavItem, m: 'user' | 'dev') => {
    if (i.show === false) return null;
    const locked = !!i.role && !roles.includes(i.role);
    if (m === 'user' && locked) return null;
    return (
      <Command.Item
        key={`${m}-${i.href}`}
        value={`${t(i.label)} ${m === 'dev' ? 'developer' : 'user'} ${i.href}`}
        onSelect={() =>
          run(() => {
            if (m !== mode) switchMode(m, { navigate: false });
            router.push(i.href);
          })
        }
        className={itemCls}
      >
        <i.icon aria-hidden className="h-4 w-4 text-subtle" />
        <span className="flex-1 truncate">{t(i.label)}</span>
        {locked ? <Lock aria-hidden className="h-3 w-3 text-subtle" /> : null}
        <span className="text-micro text-subtle">{m === 'dev' ? t('mode.dev') : t('mode.user')}</span>
      </Command.Item>
    );
  };

  const order: ('user' | 'dev')[] = mode === 'dev' ? ['dev', 'user'] : ['user', 'dev'];

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="ds-scrim fixed inset-0 z-40 bg-black/50" />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          className="ds-pop fixed left-1/2 top-[12vh] z-50 w-[calc(100vw-24px)] max-w-xl -translate-x-1/2 overflow-hidden rounded-xl border border-line bg-overlay text-fg shadow-pop outline-none"
          data-testid="command-palette"
        >
          <DialogPrimitive.Title className="sr-only">{t('shell.commandPalette')}</DialogPrimitive.Title>
          <Command label={t('shell.commandPalette')} loop>
            <div className="flex items-center gap-2 border-b border-line px-3">
              <Search aria-hidden className="h-4 w-4 shrink-0 text-subtle" />
              <Command.Input
                autoFocus
                placeholder={t('palette.placeholder')}
                className="h-12 w-full bg-transparent text-sm text-fg outline-none placeholder:text-subtle focus-visible:outline-none"
              />
            </div>
            <Command.List className="ds-scroll max-h-[min(60vh,420px)] overflow-y-auto p-1.5">
              <Command.Empty className="px-3 py-6 text-center text-sm text-muted">{t('palette.empty')}</Command.Empty>
              {order.map((m) => (
                <Command.Group key={m} heading={`${t('palette.pages')} · ${m === 'dev' ? t('mode.dev') : t('mode.user')}`} className={groupCls}>
                  {NAV[m].flatMap((g) => g.items).map((i) => pageItem(i, m))}
                  {m === 'dev' ? DEV_FOOTER.map((i) => pageItem(i, 'dev')) : null}
                </Command.Group>
              ))}
              <Command.Group heading={t('palette.actions')} className={groupCls}>
                <Command.Item className={itemCls} onSelect={() => run(() => switchMode(mode === 'dev' ? 'user' : 'dev'))}>
                  {mode === 'dev' ? <User aria-hidden className="h-4 w-4 text-subtle" /> : <Code2 aria-hidden className="h-4 w-4 text-subtle" />}
                  {mode === 'dev' ? t('palette.switchToUser') : t('palette.switchToDev')}
                </Command.Item>
                <Command.Item className={itemCls} onSelect={() => run(() => setPref('theme', theme === 'dark' ? 'light' : 'dark'))}>
                  {theme === 'dark' ? <Sun aria-hidden className="h-4 w-4 text-subtle" /> : <Moon aria-hidden className="h-4 w-4 text-subtle" />}
                  {t('palette.toggleTheme')}
                </Command.Item>
                <Command.Item className={itemCls} value="language english" onSelect={() => run(() => setLang('en'))} disabled={lang === 'en'}>
                  <Globe aria-hidden className="h-4 w-4 text-subtle" />
                  <span lang="en">{t('palette.useEnglish')}</span>
                </Command.Item>
                <Command.Item className={itemCls} value="language chinese 中文" onSelect={() => run(() => setLang('zh'))} disabled={lang === 'zh'}>
                  <Globe aria-hidden className="h-4 w-4 text-subtle" />
                  <span>{t('palette.useChinese')}</span>
                </Command.Item>
                {(['100', '115', '130'] as const).map((s) => (
                  <Command.Item key={s} className={itemCls} value={`text size ${s}`} disabled={prefs.text === s} onSelect={() => run(() => setPref('text', s))}>
                    <Type aria-hidden className="h-4 w-4 text-subtle" />
                    {t('settings.textSize')} {s}%
                  </Command.Item>
                ))}
                {me ? (
                  <Command.Item className={itemCls} onSelect={() => run(() => void signOut())}>
                    <LogOut aria-hidden className="h-4 w-4 text-subtle" />
                    {t('persona.signOut')}
                  </Command.Item>
                ) : null}
              </Command.Group>
            </Command.List>
          </Command>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
