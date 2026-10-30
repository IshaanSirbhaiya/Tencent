'use client';
import { Database, Lock, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect } from 'react';
import { restoreNavRail, setNavRail, useNavRail } from '@/lib/navRail';
import { Tooltip } from '@/components/ui';
import { cn } from '@/lib/cn';
import { useI18n } from '@/lib/i18n';
import { DEV_FOOTER, isActive, visibleNav, type NavItem } from './nav';
import { useMode, useSession } from './Providers';

function NavLink({ item, locked, onNavigate }: { item: NavItem; locked: boolean; onNavigate?: () => void }) {
  const pathname = usePathname() ?? '/';
  const { t } = useI18n();
  const active = isActive(item, pathname);
  const Icon = item.icon;
  const roleName = item.role ? t(`role.${item.role}` as Parameters<typeof t>[0]) : '';
  const link = (
    <Link
      href={item.href}
      onClick={onNavigate}
      aria-current={active ? 'page' : undefined}
      data-nav={item.id}
      title={t(item.label)}
      className={cn(
        'group flex min-h-[2rem] items-center gap-2.5 rounded-md px-2.5 text-body-sm transition-colors duration-fast',
        active ? 'bg-raised font-medium text-fg shadow-[inset_2px_0_0_rgb(var(--accent))]' : 'text-muted hover:bg-raised/70 hover:text-fg',
      )}
    >
      <Icon aria-hidden className={cn('h-4 w-4 shrink-0', active ? 'text-accent' : 'text-subtle group-hover:text-muted')} />
      <span className="nav-label min-w-0 flex-1 truncate">{t(item.label)}</span>
      {locked ? (
        <>
          <Lock aria-hidden className="h-3 w-3 shrink-0 text-subtle" />
          <span className="sr-only">({t('nav.needsRole', { role: roleName })})</span>
        </>
      ) : null}
    </Link>
  );
  return locked ? (
    <Tooltip content={t('nav.needsRole', { role: roleName })} side="right">
      {link}
    </Tooltip>
  ) : (
    link
  );
}

/** Mode-specific navigation. Rendered in the desktop sidebar and inside the mobile sheet. */
export function SideNav({ onNavigate, className }: { onNavigate?: () => void; className?: string }) {
  const mode = useMode();
  const { me } = useSession();
  const { t } = useI18n();
  const groups = visibleNav(mode, me?.roles ?? []);
  return (
    <nav aria-label={t('shell.primaryNav')} className={cn('flex h-full flex-col', className)} data-testid="side-nav" data-mode={mode}>
      <div className="flex-1 space-y-5 overflow-y-auto px-3 py-4">
        {groups.map((g) => (
          <div key={g.label}>
            <p className="nav-label mb-1.5 px-2.5 text-micro font-semibold uppercase tracking-wider text-subtle">{t(g.label)}</p>
            <ul className="space-y-0.5">
              {g.items.map((i) => (
                <li key={i.href}>
                  <NavLink item={i} locked={i.locked} onNavigate={onNavigate} />
                </li>
              ))}
            </ul>
          </div>
        ))}
        {mode === 'dev' ? (
          <ul className="space-y-0.5 border-t border-line pt-3">
            {DEV_FOOTER.map((i) => (
              <li key={i.href}>
                <NavLink item={i} locked={false} onNavigate={onNavigate} />
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <div className="flex items-center gap-1.5 border-t border-line px-3 py-2">
        <p className="nav-label flex min-w-0 flex-1 items-center gap-1.5 px-2 text-micro text-subtle">
          <Database aria-hidden className="h-3 w-3 shrink-0" />
          <span className="min-w-0">{t('app.syntheticData')}</span>
        </p>
        {onNavigate ? null : <RailToggle />}
      </div>
    </nav>
  );
}

/** Desktop only: shrink the sidebar to its icons, or open it again (remembered). */
function RailToggle() {
  const rail = useNavRail();
  const { t } = useI18n();
  useEffect(() => restoreNavRail(), []);
  const label = rail ? t('shell.navExpand') : t('shell.navCollapse');
  return (
    <button
      type="button"
      onClick={() => setNavRail(!rail)}
      aria-label={label}
      title={label}
      aria-pressed={rail}
      data-testid="nav-rail-toggle"
      className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-subtle transition-colors duration-fast hover:bg-raised hover:text-fg"
    >
      {rail ? <PanelLeftOpen aria-hidden className="h-4 w-4" /> : <PanelLeftClose aria-hidden className="h-4 w-4" />}
    </button>
  );
}
