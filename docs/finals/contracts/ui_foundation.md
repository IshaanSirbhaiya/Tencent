# UI foundation contract: Walnut DataBridge web (Wave 0)

Written 30 Oct 2026 by the Foundation agent. App: `databridge/apps/internal_brain_web` (Next.js 14.2.15,
React 18.3.1, Tailwind 3.4, TypeScript). Read this before building any page. Design law:
`databridge/.claude/skills/databridge-frontend-dashboards/SKILL.md`. Plan: `docs/finals/PLAN.md` §3, §4, §6.

The live reference for everything below is the style guide at **`/dev/styleguide`** (every primitive,
both themes). Run it with `npm run dev:mock` and sign in as anyone.

## 1. What exists

| Area | Where |
|---|---|
| Design tokens | `app/globals.css` (CSS variables), `tailwind.config.ts` (Tailwind names) |
| Class helper | `lib/cn.ts` (`cn()` = clsx + tailwind-merge that knows our font-size names) |
| Primitives | `components/ui/*`, imported from `@/components/ui` |
| Shell | `components/shell/*`, imported from `@/components/shell` |
| Preferences | `lib/prefs.ts` (theme, language, text size, contrast, motion, mode) |
| i18n | `lib/i18n/index.ts`, `lib/i18n/en.ts`, `lib/i18n/zh.ts` |
| API client | `lib/api.ts` (+ types in `lib/types.ts`) |
| Mock fixtures for the new endpoints | `lib/mock/finals.ts` (wired in `lib/mock/server.ts`) |
| Route skeletons | `app/dev/**`, `app/(user)/history`, `app/(user)/settings` |

Dependencies added (exact pins): `@radix-ui/react-popover 1.1.2`, `react-tabs 1.1.1`, `react-tooltip 1.1.3`,
`react-dropdown-menu 2.1.2`, `react-switch 1.1.1`, `react-scroll-area 1.2.0`, `react-slot 1.1.0` (dialog stays
1.1.2), `cmdk 1.0.4`, `@tanstack/react-table 8.20.5`, `recharts 2.12.7`, `reactflow 11.11.4`, `d3-force 3.0.0`,
`@types/d3-force 3.0.10` (dev), `@fontsource-variable/inter 5.1.0`, `class-variance-authority 0.7.0`,
`tailwind-merge 2.5.4`. Do not add another component kit.

### 1.1 The `components/ui` decision

The old `components/ui.tsx` file was **moved into the `components/ui/` directory** (`index.ts` barrel) and
deleted, so `@/components/ui` has one meaning. Every old export is still there with a compatible API:
`Button` (variant `primary | secondary | ghost`, size `sm | md`, `icon`, `busy`), `Badge` (tone `neutral | ok |
warn | bad | accent`, `icon`, `title`), `Tone`, `Card`, `SrOnly`, `Field`, `inputClass`. All five old pages
compile unchanged. Two visible differences: `Button size="md"` is now 36 px high (was 40 px), and `busy` shows a
spinner in place of the icon. `Button` now defaults to `type="button"` (every old submit button already says
`type="submit"`).

## 2. Design tokens

### 2.1 Root attributes (set before first paint by `PREFS_INIT_SCRIPT` in `app/layout.tsx`)

| Attribute on `<html>` | Values | Set by |
|---|---|---|
| `data-theme` | `dark` (default) · `light` | theme switch, Settings, palette; storage key `ib-theme` (kept from before; the e2e tests use it). `system` is stored as a preference and resolved here |
| `data-contrast` | `more` or absent | Settings → High contrast (`ib-contrast`) |
| `data-text` | `100` · `115` · `130` | Settings → Text size (`ib-text`); scales the root font size, so every `rem` scales |
| `data-motion` | `reduce` or absent | Settings → Reduce motion (`ib-motion`); `prefers-reduced-motion` is also honoured |
| `data-mode` | `user` · `dev` | the route (`/dev/*` is dev) and the mode switch (`ib-mode`) |
| `lang` | `en` · `zh-Hans` | language switch (`ib-lang`) |

All storage access is inside try/catch; a blocked storage renders with the defaults.

### 2.2 Colours (Tailwind name → CSS variable)

Near-neutral greys and **one** accent (indigo). Every other hue is semantic and always comes with an icon
**and** text. Never write a hex value in a page; use these names (alpha works: `bg-accent/10`).

| Group | Tailwind names |
|---|---|
| Surfaces | `bg`, `surface`, `raised`, `overlay` (popovers, sheets), `line`, `line-strong` |
| Text | `fg`, `muted` (secondary, ≥ 6.6:1), `subtle` (tertiary, ≥ 4.6:1 on every surface) |
| Accent | `accent` (text/icons), `accent-solid` (filled buttons), `accent-fg`, `focus` |
| Status | `ok`, `warn`, `bad` (alias `error`) |
| Platform | `pf-confluence`, `pf-jira`, `pf-slack`, `pf-drive` (Okabe-Ito derived, muted; distinct in hue and lightness) |
| Permission | `perm-full`, `perm-partial`, `perm-locked` |

Contrast was computed for every text token on `bg`, `surface` and `raised` in both themes: all ≥ 4.5:1;
white on `accent-solid` is 4.7:1 (dark) and 6.2:1 (light). For chart fills use `PLATFORM_TONE[p].fill`
(`rgb(var(--pf-…))`) or `rgb(var(--accent))`, so charts follow the theme.

A subtree can be forced into a theme with `data-theme-scope="dark|light"` (the style guide uses it to show both).

### 2.3 Type

Inter Variable, bundled by `@fontsource-variable/inter` (no network). Chinese falls back to PingFang SC /
Noto Sans SC / Microsoft YaHei, with line-height 1.7 under `lang="zh-Hans"`.

| Class | Size | Use |
|---|---|---|
| `text-display` | 30 | rare hero numbers/titles |
| `text-title` | 22 | page `h1` (`<Page title>` uses it) |
| `text-heading` | 18 | section `h2` |
| `text-heading-sm` | 15 | card titles, `h3` |
| `text-body` | **15 px in User mode, 13 px in Developer mode** (`--fs-body`) | running text; `body` uses it |
| `text-body-sm` | 14 / 12 by mode | secondary text, table cells |
| `text-caption` | 12 | meta lines |
| `text-micro` | 11 | uppercase eyebrows, table headers |
| `text-kpi` | 28 | KPI values (with `tabular-nums`) |

Use `tabular-nums` (or the `.tabular` class) on every figure. Monospace (`font-mono`) only for ids, hashes
and model names.

### 2.4 Motion, focus, targets

- Durations `duration-fast` 120 ms, `duration-base` 160 ms, `duration-slow` 200 ms; easing `ease-out`.
  Floating layers use `.ds-pop` (fade + 4 px rise), sheets `.ds-sheet`, skeletons `.ds-skeleton`. All are
  stilled by `prefers-reduced-motion` and by `data-motion="reduce"`. No typewriter, shimmer or bounce.
- Focus: a 2 px `focus` outline with 2 px offset on every `:focus-visible`, never removed.
- Targets: every control is at least 24 px (icon buttons 28–36 px, nav rows 32 px, menu rows 32 px).

## 3. Primitives (`@/components/ui`)

| Component | Notes |
|---|---|
| `Button` | `variant`: primary (one per area) · secondary (default) · ghost · outline · danger · link; `size`: xs · sm · md · lg; `icon`, `iconRight`, `busy`, `asChild` (wrap a `next/link`) |
| `IconButton` | `icon`, **required `label`** (accessible name + native title; `noTitle` when wrapped in `Tooltip`); variant ghost · secondary · primary; size sm · md · lg |
| `Badge`, `Pill` | tones neutral · ok · warn · bad · accent · locked; `icon`. Semantic tones always with text |
| `StatusBadge` | `status` ok · warn · error · neutral, icon included |
| `PermissionBadge` | `state` full · partial · locked. **Developer views only** (rule 4 / D-07: never hint at hidden content in User mode) |
| `PlatformIcon` | `platform`, `showLabel`, `tone="platform"` for the platform tint, `size`. `PLATFORM_TONE` has the classes and chart fill |
| `Avatar` | initials, neutral; decorative unless `label` |
| `Card`, `CardHeader` (`actions`), `CardTitle`, `CardDescription`, `CardBody`, `CardFooter` | `Card as=` section (default) · div · aside · article · li; `interactive` hover |
| `SectionHeader` | `title`, `description`, `eyebrow`, `actions`, `as` h1 · h2 · h3 |
| `Separator`, `Kbd`, `Skeleton`, `SkeletonText` (has a `role="status"`) | |
| `EmptyState` | `icon`, `title`, `description`, `action`, `compact` |
| `KpiTile` | `label`, `value` (tabular), `unit`, `delta` (`{text, direction, sentiment, context}`), `footnote` (name the source), `sparkline` slot, `loading` |
| `Sparkline` | **import from `@/components/ui/sparkline`** (kept out of the barrel so Recharts loads only where used); decorative |
| `Stat` inside `StatGrid` | dense label/value pairs (valid `dl`) |
| `CodeHash` | truncated mono hash/id with copy; full value in title and for screen readers |
| `DataTable` | TanStack: `columns`, `data`, **required `caption`**, `getRowId`, `onRowClick` + `rowLabel` (rows become focusable, ↑/↓ moves, Enter/Space opens), `isRowSelected`, `initialSorting`, `empty`, `density`, `maxHeightClass` (sticky header, scrollable region). Column `meta: { numeric, className }` |
| `Tabs`, `TabsList` (`variant` underline · segmented, give it `aria-label`), `TabsTrigger`, `TabsContent` | Radix |
| `Tooltip` | `content`, single child that already has a name; never essential info |
| `Popover`, `PopoverTrigger`, `PopoverContent`, `PopoverClose` | Radix |
| `DropdownMenu*` | Content, Item (`destructive`), CheckboxItem, RadioGroup/RadioItem, Label, Separator, Sub/SubTrigger/SubContent |
| `Dialog`, `DialogTrigger`, `DialogContent` (`title` required, `description`, `footer`, `size`), `DialogClose` | Radix Dialog |
| `Sheet`, `SheetTrigger`, `SheetContent` (`side` right (default) · left, `width` sm · md · lg, `title` required, `description`, `headerActions`, `footer`, `bodyClassName`) | full width on phones; Esc returns focus to the trigger |
| `Switch`, `SwitchField` (label + hint + switch) | Radix |
| `SegmentedControl` | radio-group semantics, one Tab stop, arrows select; `label` required |
| `ScrollArea` | give `viewportLabel` (the viewport is focusable for keyboard scrolling) |
| `Field`, `Input`, `inputClass`, `SrOnly` | form basics |

Checked with axe (wcag2a/aa, 2.1, 2.2 AA tags): **0 serious or critical** on `/login`, `/`, `/settings`,
`/history`, `/dev`, `/dev/styleguide`, `/dev/sim` (lock card), `/dev/journey/[req]`, `/map`, `/audit`, in dark
and light, at 1280 and 390 px. No horizontal overflow at 390 px except `/audit` (see §10).

## 4. The shell (`@/components/shell`)

`app/layout.tsx` mounts `<Providers>` once (prefs, language, session, tooltip provider), so state survives
navigation. The frame is `<AppShell>`:

| Prop | Meaning |
|---|---|
| `variant` | `'app'` (default: needs a session; 401 → `/login`) · `'public'` (sign-in page: brand, language, theme, tour only) |
| `requireRole` | `'compliance_officer' \| 'admin_it'`: others see the lock card (D-05) |
| `container` | `true` (default) wraps children in the old `max-w-7xl` container; route layouts pass `false` and pages use `<Page>` |

Layout: skip link → `header` (banner: wordmark, User/Developer switch, search button `Ctrl/Cmd+K`, language,
theme, tour, persona menu) → left `nav "Primary"` (≥ 1024 px) → `main#main`. Below 1024 px the nav and the mode
switch, language and theme move into a left sheet opened by **Open navigation**; at 390 px the top bar holds
the menu button, the mark, **Tour** and the avatar. Nesting is safe: an `AppShell` inside another renders only
its role gate and children (so the old pages can move into a route group without edits).

Hooks:

```ts
const me = useMe();                                    // WhoAmI | null (old contract)
const { me, status, refresh, signOut, switchPersona, hasRole } = useSession();
const { prefs, setPref, mode, switchMode, theme } = usePrefs();   // mode: 'user' | 'dev'
const mode = useMode();
```

Components: `<Page title description eyebrow actions width="narrow|default|wide|full" bleed>`,
`<RequireRole role="compliance_officer">…</RequireRole>`, `<LockCard role=…/>`, `<Placeholder>` (skeleton only).

**Mode rules.** `/dev/*` is Developer mode; `/`, `/history`, `/settings`, `/compare`, `/login` are User mode;
the legacy `/map`, `/audit`, `/sim` keep the current mode. The switch stores the choice (`ib-mode`) and routes
to that mode's home (`/` or `/dev`). `?mode=dev` (or `?mode=user`) on any URL sets the mode and goes home.
Developer mode sets `data-mode="dev"`, which makes body text 13 px.

**Navigation** lives in `components/shell/nav.ts` (shell-owned):
- User mode: *Workspace* Ask `/`, History `/history`, Compare `/compare`, Settings `/settings`; and a
  transitional *Your tools* group: Map `/map`, Audit `/audit` (compliance only), Simulator `/sim` (IT admin
  only). Role items are hidden without the role.
- Developer mode: *Observe* Mission control `/dev`, Journey `/dev/journey`, Knowledge graph `/dev/graph`, Walnut
  engine `/dev/brain`, Analytics `/dev/analytics`; *Govern* Audit `/dev/audit`, Simulator `/dev/sim`, Trust
  centre `/dev/trust`; footer Style guide `/dev/styleguide`. Items needing a role the person lacks show a lock
  icon and "(Needs the … role)" in their name, and the page shows the lock card.
- Nav links carry `data-nav="<id>"` and `aria-current="page"` when active.

**Command palette** (`Ctrl/Cmd+K` or the search button): pages of both modes, switch mode, toggle theme,
language, text size, sign out.

**Persona menu**: name, role, sub, role badges; *Switch person* (calls `api.switchTo`, the passcode given at
sign-in); Settings; Sign out. The trigger contains `data-testid="whoami"` with "Name — role" and the sub.

## 5. i18n

```ts
import { useI18n } from '@/lib/i18n';
const { t, lang, setLang } = useI18n();
t('nav.ask');                       // core keys from lib/i18n/en.ts (typed)
t('nav.needsRole', { role: 'IT admin' });
```

Feature strings go in **your own files**, so no two agents edit the same dictionary:

```ts
// lib/i18n/user.en.ts
export const userEn = { 'ask.greeting': 'Good afternoon, {name}' } as const;
// lib/i18n/user.zh.ts
import type { userEn } from './user.en';
export const userZh: Record<keyof typeof userEn, string> = { 'ask.greeting': '下午好，{name}' };
// lib/i18n/user.ts
import { defineMessages } from '.';
export const userMessages = defineMessages({ en: userEn, zh: userZh });
// in a component
const t = useMessages(userMessages);  t('ask.greeting', { name })
```

TypeScript fails the build if a zh key is missing. `<html lang>` follows the language (`zh-Hans`). Developer
mode pages may stay English (PLAN §4). Core keys are owned by the coordinator; ask for additions.

## 6. Routes (old → new, owner)

| Route | Now | Final | Owner |
|---|---|---|---|
| `/login` | old page inside `AppShell variant="public"` | User-mode sign-in | User mode |
| `/` | old Ask page inside the shell | `app/(user)/page.tsx` (move it; delete `app/page.tsx`) | User mode |
| `/history` | skeleton `app/(user)/history` | | User mode |
| `/settings` | **working** prefs page `app/(user)/settings` (language, theme, text size, motion, contrast, Developer mode, replay tour) | add privacy notice, reset demo | User mode |
| `/compare` | old page inside the shell | restyle | Tour + Presenter + Compare |
| `/map` | old page inside the shell | superseded by `/dev/graph`; keep until the tour stops using it | Graph (decide) |
| `/audit` | old page inside the shell | redirect to `/dev/audit` **later** (not now) | Audit + Sim + Trust |
| `/sim` | old page inside the shell | redirect to `/dev/sim` **later** | Audit + Sim + Trust |
| `/dev` | skeleton | Mission control | Analytics + Mission control + Journey |
| `/dev/journey`, `/dev/journey/[req]` | skeletons | journey list + waterfall | Analytics + Mission control + Journey |
| `/dev/analytics` | skeleton, gated `compliance_officer` | | Analytics + Mission control + Journey |
| `/dev/graph` | skeleton | | Graph |
| `/dev/brain` | skeleton | | Engine (`app/api/walnut/*` is theirs) |
| `/dev/audit` | skeleton, gated `compliance_officer` | | Audit + Sim + Trust |
| `/dev/sim` | skeleton, gated `admin_it` | | Audit + Sim + Trust |
| `/dev/trust` | skeleton | | Audit + Sim + Trust |
| `/dev/styleguide` | style guide | | Foundation / coordinator |

`app/dev/layout.tsx` and `app/(user)/layout.tsx` render `<AppShell container={false}>`; they are shell-owned.

## 7. API client and mock

New typed calls in `lib/api.ts` (types in `lib/types.ts`):

| Call | Endpoint | Who | Type |
|---|---|---|---|
| `api.trace(req)` | `GET /ib/trace/{req}` | own requests (others: same 404) | `Trace` |
| `api.journey(req)` | `GET /audit/journey/{req}` | compliance | `Journey` |
| `api.auditStats({ days, replay })` | `GET /audit/stats?days=&replay=` | compliance | `AuditStats` |
| `api.graph({ focus, q, limit })` | `GET /ib/graph` | employees | `KnowledgeGraph` (**provisional**) |
| `api.doc(id, q?)` | `GET /ib/doc/{id}` | employees (restricted ≡ unknown: 404) | `DocView` (**provisional**) |

`docs/finals/contracts/backend_v1.md` did not exist when this was written, so the shapes were taken from the
Python: `audit/stats.py compute_stats`, `app.py trace`, `audit/query.py reconstruct`, and the in-progress
`graph.py build_graph` / `docview.py view_doc`. Re-check `GraphNode`, `KnowledgeGraph` and `DocView` against
backend_v1.md when it lands. `STAGES` (`accept, gate1, gate2, seal, compose`) is exported from `lib/types.ts`.

Proxy allowlist (`app/api/ib/[...path]/route.ts`) now also has `GET ^ib\/graph$` and
`GET ^ib\/doc\/${DOC_ID}$`. `POST /ib/graph/openable` (in the backend) is **not** allowlisted; ask the
coordinator if the graph's View-as needs it.

Mock mode (`npm run dev:mock`) serves all five from `lib/mock/finals.ts`, derived from the same synthetic
ledger and permission-correct map the other mock routes use (so stats agree with the audit console). Stage
timings in mock mode are a deterministic split of each request's latency; the live API seals measured values.

## 8. File ownership

- **Coordinator / shell-owned (request changes, do not edit):** `package.json`, `tailwind.config.ts`,
  `app/globals.css`, `app/layout.tsx`, `app/dev/layout.tsx`, `app/(user)/layout.tsx`, `components/shell/*`,
  `components/ui/*`, `components/AppShell.tsx`, `components/Brand.tsx`, `components/PlatformIcon.tsx`,
  `lib/api.ts`, `lib/types.ts`, `lib/prefs.ts`, `lib/cn.ts`, `lib/i18n/index.ts`, `lib/i18n/en.ts`,
  `lib/i18n/zh.ts`, `lib/mock/server.ts`, `lib/mock/finals.ts`, the proxy route.
- **Page agents** own their route folders and their component folders from PLAN §6 (`app/(user)/*` pages,
  `components/user/*`, `lib/i18n/user.*`; `app/dev/graph/*`, `components/graph/*`; and so on). Need a new
  primitive? Build it in your folder first; if two agents need it, ask the coordinator to promote it.
- Need a new endpoint call or type? Put a local typed wrapper in your folder (using `fetch('/api/ib/…')` is
  not allowed: ask for the `api.*` addition) and tell the coordinator.

## 9. How to build a page (example)

```tsx
// app/dev/analytics/page.tsx  (Analytics agent)
'use client';
import { BarChart3 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Page } from '@/components/shell/Page';
import { RequireRole } from '@/components/shell/RoleGate';
import { Card, CardBody, CardHeader, CardTitle, EmptyState, KpiTile, SkeletonText } from '@/components/ui';
import { Sparkline } from '@/components/ui/sparkline';
import { ApiError, api } from '@/lib/api';
import type { AuditStats } from '@/lib/types';

function Analytics() {
  const [stats, setStats] = useState<AuditStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.auditStats({ days: 30 }).then(setStats).catch((e) => setError(e instanceof ApiError ? e.message : 'unavailable'));
  }, []);
  return (
    <Page eyebrow="Observe" title="Analytics" description="Every number is computed on the server from the verified ledger.">
      {error ? <EmptyState icon={BarChart3} title="Analytics are unavailable" description="Try again in a moment." /> : null}
      {!stats && !error ? <SkeletonText lines={4} label="Loading analytics" /> : null}
      {stats ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <KpiTile
            label="Requests"
            value={stats.totals.requests.toLocaleString('en-SG')}
            sparkline={<Sparkline values={stats.days.map((d) => d.asked ?? 0)} />}
            footnote={`/audit/stats · last ${stats.window.days} days`}
          />
          {/* … */}
        </div>
      ) : null}
    </Page>
  );
}

export default function Page_() {
  return (
    <RequireRole role="compliance_officer">
      <Analytics />
    </RequireRole>
  );
}
```

Checklist for every page: render only backend values (no client-side aggregation); one `h1` (via `<Page
title>`); every control named; status = icon + text; `tabular-nums` on figures; no hidden-content hints in User
mode; works at 390 px with no horizontal scroll; axe clean in both themes; strings through i18n in User mode.

## 10. E2E impact (tests were not rewritten)

Results of the existing 38 Playwright tests against the new shell are listed below. The selectors the shell
keeps on purpose: the skip link is the first Tab stop; `data-testid="whoami"` contains the persona's sub
(visually hidden below 768 px, still in the DOM); the banner has exactly one **Take the tour** button
(label "Tour" below 640 px); maya sees a link named **Audit** in User mode; jdoe sees none; `tour-offer` is
shown in User mode; `ib-theme` is still the theme storage key.

**Result: 38 / 38 pass** (`npx playwright test`, mock build, 30 Oct). The first run had 1 failure,
`tests/e2e/audit.spec.ts:25` (`page.getByLabel('User')` also matched the brand link, then labelled
"Walnut DataBridge — User"); the brand link is now "Walnut DataBridge, home" and the spec passes unchanged.
No test file needs updating for the shell.

Watch-outs for the Tests agent when the pages move:
- `getByLabel('User')` in `audit.spec.ts:25` is loose; use `{ exact: true }` or `#f-user`.
- `ask.spec.ts:13` and `audit.spec.ts:6` rely on the transitional User-mode *Your tools* links (`/audit`,
  `/sim`). When `/audit` and `/sim` redirect to `/dev/*`, change them to the Developer nav (`data-nav="dev-audit"`)
  or go by URL.
- `tour.spec.ts:7` and `map.spec.ts:78` click `getByRole('banner').getByRole('button', { name: 'Take the tour' })`.
  At 390 px that button's name is "Tour".
- `helpers.ts:14` `login()` waits for `getByTestId('whoami')` to contain the sub: keep that test id on the
  persona menu trigger.
- `tests/e2e/screenshots.spec.ts` rewrites `screenshots/*.png`; running the suite now regenerates them with the
  new shell.

Known, not caused by the shell: on `/audit` at 390 px the visually hidden column-header labels inside the
horizontally scrolling audit table (`components/audit/AuditTable.tsx`) escape the scroll container, because
no ancestor is positioned, and widen the page by about 700 px. Fix (Audit agent): add `relative` to the
table's `overflow-x-auto` wrapper.

## 11. Leftovers

- The skip link text is English only (it lives in the server layout).
- `/map` and the old tour (6 steps over `/login`, `/`, `/map`, `/compare`, `/audit`, `/sim`) are unchanged; the
  Tour agent rebuilds the tour per mode.
- `/dev/journey` (index) is a skeleton; the nav links to it so the Journey agent can list recent requests.
- `POST /ib/graph/openable` is not in the proxy allowlist (see §7).
- No visual regression baseline yet; `tests/e2e/screenshots.spec.ts` captures the old screens.
