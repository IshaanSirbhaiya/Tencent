// Navigation per mode. The shell renders this in the left nav, the mobile sheet and the command palette.
// Page agents: add nothing here; ask the coordinator (this file is shell-owned).
import {
  Activity,
  BarChart3,
  Calculator,
  Boxes,
  FlaskConical,
  GitCompareArrows,
  History,
  LayoutDashboard,
  Map as MapIcon,
  MessageSquare,
  Network,
  Palette,
  ScrollText,
  Settings,
  ShieldCheck,
  Waypoints,
  type LucideIcon,
} from 'lucide-react';
import type { CoreKey } from '@/lib/i18n/en';
import type { Mode } from '@/lib/prefs';

export type Role = 'employee' | 'compliance_officer' | 'admin_it';

export interface NavItem {
  href: string;
  label: CoreKey;
  icon: LucideIcon;
  /** Role needed to USE the page. User mode hides the item without it; Developer mode shows it locked. */
  role?: Role;
  /** Hide entirely when false (feature flags). */
  show?: boolean;
  /** Exact match only (home pages). */
  exact?: boolean;
  /** Stable hook for tests and the tour. */
  id: string;
}

export interface NavGroup {
  label: CoreKey;
  items: NavItem[];
}

/** "Same question, two people" needs demo sign-in, which is always on in this demo build. */
export const DEMO_COMPARE = process.env.NEXT_PUBLIC_IB_DEMO_COMPARE !== '0';

export const NAV: Record<Mode, NavGroup[]> = {
  user: [
    {
      label: 'nav.group.workspace',
      items: [
        { id: 'ask', href: '/', label: 'nav.ask', icon: MessageSquare, exact: true },
        { id: 'history', href: '/history', label: 'nav.history', icon: History },
        { id: 'compare', href: '/compare', label: 'nav.compare', icon: GitCompareArrows, show: DEMO_COMPARE },
        { id: 'settings', href: '/settings', label: 'nav.settings', icon: Settings },
      ],
    },
    {
      // Transitional: the pre-finals pages, kept working until their Developer-mode versions land.
      label: 'nav.group.tools',
      items: [
        { id: 'value', href: '/value', label: 'nav.value', icon: Calculator },
        { id: 'map', href: '/map', label: 'nav.map', icon: MapIcon },
        { id: 'audit', href: '/dev/audit', label: 'nav.audit', icon: ScrollText, role: 'compliance_officer' },
        { id: 'sim', href: '/dev/sim', label: 'nav.sim', icon: FlaskConical, role: 'admin_it' },
      ],
    },
  ],
  dev: [
    {
      label: 'nav.group.observe',
      items: [
        { id: 'dev-mission', href: '/dev', label: 'nav.dev.mission', icon: LayoutDashboard, exact: true },
        { id: 'dev-journey', href: '/dev/journey', label: 'nav.dev.journey', icon: Waypoints },
        { id: 'dev-graph', href: '/dev/graph', label: 'nav.dev.graph', icon: Network },
        { id: 'dev-brain', href: '/dev/brain', label: 'nav.dev.brain', icon: Boxes },
        { id: 'dev-analytics', href: '/dev/analytics', label: 'nav.dev.analytics', icon: BarChart3, role: 'compliance_officer' },
        { id: 'dev-value', href: '/value', label: 'nav.dev.value', icon: Calculator },
      ],
    },
    {
      label: 'nav.group.govern',
      items: [
        { id: 'dev-audit', href: '/dev/audit', label: 'nav.dev.audit', icon: ScrollText, role: 'compliance_officer' },
        { id: 'dev-sim', href: '/dev/sim', label: 'nav.dev.sim', icon: Activity, role: 'admin_it' },
        { id: 'dev-trust', href: '/dev/trust', label: 'nav.dev.trust', icon: ShieldCheck },
      ],
    },
  ],
};

/** Shown at the foot of the Developer nav. */
export const DEV_FOOTER: NavItem[] = [{ id: 'dev-styleguide', href: '/dev/styleguide', label: 'nav.dev.styleguide', icon: Palette }];

export function isActive(item: Pick<NavItem, 'href' | 'exact'>, pathname: string): boolean {
  if (item.exact) return pathname === item.href;
  return pathname === item.href || pathname.startsWith(`${item.href}/`);
}

/** Items visible to this person in this mode, with a `locked` flag for Developer mode. */
export function visibleNav(mode: Mode, roles: string[]): { label: CoreKey; items: (NavItem & { locked: boolean })[] }[] {
  return NAV[mode]
    .map((g) => ({
      label: g.label,
      items: g.items
        .filter((i) => i.show !== false)
        .map((i) => ({ ...i, locked: !!i.role && !roles.includes(i.role) }))
        .filter((i) => mode === 'dev' || !i.locked),
    }))
    .filter((g) => g.items.length > 0);
}
