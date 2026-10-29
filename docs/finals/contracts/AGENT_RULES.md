# Rules for every Wave-1 page agent (read before you start)

Repo `E:\Tenecent Hack_Walnut\Tencent`, branch `final_v1.0`. The web app is
`databridge/apps/internal_brain_web`. Stay inside `E:\Tenecent Hack_Walnut`.

1. **Read first:**
   - `docs/finals/contracts/ui_foundation.md` (tokens, primitives, shell, i18n, how to build a page);
   - `docs/finals/PLAN.md` (§3, §4, §9 v2) and `docs/finals/DECISIONS.md`;
   - `databridge/.claude/skills/databridge-frontend-dashboards/SKILL.md` (the design law);
   - `docs/finals/contracts/backend_v1.md` if present (backend endpoint contracts; the backend is being
     finished in parallel, so code against the contract and the mock).
2. **Hard product rules (the product IS these):**
   - Never show an asker a count of hidden or denied items.
   - Never show "something exists but is hidden", or any Gate-2 reason code.
   - A restricted question and a made-up question must render IDENTICALLY.
   - `PermissionBadge` and denial details are Developer-mode, compliance-only.
   - Every number on screen comes from the backend (or the mock of it): no invented values, no
     client-side aggregation of audit data.
   - Answers render as plain text with `[n]` citation chips; never `dangerouslySetInnerHTML`.
3. **Own only your files** (listed in your brief):
   - Shared files belong to the coordinator: `package.json`, `tailwind.config.ts`, `app/globals.css`,
     `app/layout.tsx`, `components/shell/*`, `components/ui/*`, `lib/api.ts`, `lib/types.ts`,
     `lib/i18n/index.ts|en.ts|zh.ts`, `lib/mock/server.ts`, the proxy route.
   - If you need a change there, add it in your own folder if possible, or write the exact request at
     the end of your reply.
   - Two exceptions: you may ADD new functions/types to `lib/api.ts` / `lib/types.ts` at the END of the
     file, in a block commented `// ---- <your area> ----`. Do not edit other blocks.
   - Your mock handlers go in `lib/mock/extra/<your-area>.ts` only.
   - Your strings go in your own `lib/i18n/<area>.ts` via `defineMessages()`, English + 简体中文
     (User-mode strings must have both).
4. **Never** run `git` commands that change state, `git stash`, `git checkout --` or `git reset --hard`.
   Do not commit; the coordinator commits.
5. **Verifying your work:**
   - `npx tsc --noEmit` must pass.
   - Do NOT run `npm run build` or `next dev` on ports 3100/3101 (others are using them). Use your own
     port and dist dir: `NEXT_PUBLIC_IB_MOCK=1 NEXT_DIST_DIR=.next-<area> npx next dev -p <your port>`,
     and stop it when done.
   - Look at your pages with the built-in browser or Playwright screenshots.
   - Check dark and light, 1280 and 390 px, keyboard only, and axe (`@axe-core/playwright`, no serious
     or critical).
   - You may write e2e specs in `tests/e2e/<area>*.spec.ts` (mock mode) and run only your own spec file
     with `npx playwright test tests/e2e/<area>*.spec.ts`. Check `playwright.config.ts`: if it builds or
     starts the app itself on 3101, do NOT run it. Write the specs and say so.
6. **Quality bar:** Linear / Vercel / Stripe / Palantir.
   - Restraint, density, exact numbers in tabular numerals, provenance visible.
   - Dark default, and the light theme must look as good.
   - Accessible names, visible focus, 24 px targets, `prefers-reduced-motion`.
   - Every state is designed: loading skeletons, empty, error (503 "temporarily unavailable"), refusal,
     and locked (role).
7. **Reply when done with:** your file list, the screenshots you took (paths), the test status, and any
   requests for the coordinator.
