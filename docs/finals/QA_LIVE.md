# Live integration QA: Walnut DataBridge web against the live stack

30 Oct 2026, integration QA pass. Every page was built against mocks; this pass ran each one against the
LIVE backend and fixed what broke.

## 1. Setup

| Item | Value |
|---|---|
| Stack | mock platforms :8200, Internal Brain :8100 (`IB_FORCE_OFFLINE=1`, about 300 seeded replay questions, 2 real Gate-2 denials), Walnut gateway :8300, production web :3100 (not touched) |
| QA dev server | `IB_API_URL=http://127.0.0.1:8100 WALNUT_PROXY=1 NEXT_DIST_DIR=.next-qa npx next dev -p 3120` |
| QA production build | `NEXT_DIST_DIR=.next-qa-prod npx next build`, then `next start -p 3121` with the same env |
| Driver | Playwright 1.48 Chromium (headless, dark, 1440 × 900) plus the desktop app's built-in browser for the graph |
| Console rule | every page: zero `console.error`, zero React key/ref warnings, zero failed requests, no HTTP 4xx/5xx except intended lock cards |
| Screenshots | `docs/finals/screens/live/` (97 PNGs). `prod-*` are from the production build. `presenter-step-*` are in the Stage theme, because P switches Stage on |
| Typecheck | `npx tsc --noEmit` passes after every fix |

Each ask in this pass wrote real ledger events (replay label `false`, channel `ui`).

## 2. QA log

Result: **Pass** = works live with a clean console. **Fixed** = a bug was found and fixed in this pass. **Open** = still an issue (see §4).

| # | Page | Persona | Result | Bug | Fix | File |
|---|---|---|---|---|---|---|
| 1 | `/dev/graph` | jdoe | Fixed (hardened) | Blank canvas reported on the production build, with one JS chunk apparently hanging. Not reproducible in this pass (headless Chromium and the built-in browser, dev and prod, full load and client navigation all drew 867 documents). The worker chunk served 200 in 4 ms. Still, a hung or failed layout worker would have left the page on its loading state for good, and a cached layout was trusted without checks | Layout worker gets a 3 s deadline. On timeout, error or `messageerror` the worker is dropped for the session and the layout runs on the main thread (about 10 ms; `computeLayout` is now a static import, so no second lazy chunk can hang). Cached or worker positions are used only if every node has a finite position. Sigma re-measures and refreshes on its first 3 frames, so a container with no size yet still draws. Verified on the prod build with a stubbed `Worker` that never replies: the graph draws about 3 s later than normal (12.9 s vs 10.2 s) | `components/graph/layout.ts`, `components/graph/GraphCanvas.tsx` |
| 2 | Ask: source cards, sources sheet | all six | Fixed | Live Slack versions are `"<ts>|<edits>|<hash>"` (the mock had a bare `"<ts>"`), so cards and sheets showed `rev 1791337320…` | Slack versions read "Message of 7 Oct 2026" (zh: 2026年10月7日 的消息); Jira reads "Revision of 8 Oct 2026" | `components/user/answerText.ts` (`versionText`) |
| 3 | Journey receipt `/dev/journey/[req]` | jdoe | Fixed | Same raw Slack revision id; Jira version was hidden | `shortVersion` formats Slack as "Message of …" and Jira as "Revision of …"; shown in tabular figures, not mono | `lib/format.ts`, `components/journey/Receipt.tsx` |
| 4 | Graph node panel (overview, passage caption, lineage) | jdoe | Fixed | "v1791335520.000101|…" and "v2026-10-08 17:00" (a "v" glued onto every version) | Own `shortVersion`: v6 / Revision of … / Message of … | `components/graph/NodePanel.tsx` |
| 5 | `/dev/audit?latest=1` | maya.compliance, Presenter step 7 | Fixed | The parameter was ignored | `?latest=1` reloads the table (also when the presenter pushes the URL while the page is already open), marks the newest request with a "Newest" badge and the selected-row style, and scrolls it into view | `components/audit/AuditConsole.tsx`, `components/audit/AuditTable.tsx` |
| 6 | Sources sheet passage highlight (Ask) and "Cited in this answer" (Compare) | all | Fixed | Live answers put the tag after the full stop ("…reason. [C1] Next…"). `sentencesFor` split the tag into its own empty fragment, so the Compare sheet's "Cited in this answer" box was an empty mark and the User sheet never highlighted the passage | Tag-only fragments attach their tags to the sentence before them | `components/user/answerText.ts` (`sentencesFor`) |
| 7 | Ask and Compare source cards | amy.am, priya.sre, leo.ops | Fixed | Two citations of one document (two passages, e.g. C1 and C3 of the customer script) rendered as two identical-looking cards | The repeat card says "Another passage of source 1" (zh: 来源 1 的另一段) | `components/user/AnswerCard.tsx`, `lib/i18n/user.ts` |
| 8 | Audit table (first load, `?latest=1`) | maya.compliance | Fixed | The table showed only 9 requests. The limit counts ledger EVENTS, and the live ledger is mostly `ingest.sync`, `auth.login`, `graph.viewed` and `doc.viewed` (the mock had only request rows) | `auditApi.events` asks for the API maximum, `limit=500` (about 70 to 100 requests) | `lib/api.ts` (audit block) |
| 9 | Audit plain-English search chips | maya.compliance | Fixed | "Who retrieved the Q3 breach report?" showed Since 30 Oct 04:30, Until 30 Oct 04:30 (it looked like an empty window). The real window was 365 days; the year was missing | Since/Until chips show the year ("30 Oct 2025, 04:34:18 SGT"). The parser's own note ("no time range named: the last 365 days") is shown under the chips | `components/audit/AuditConsole.tsx` |
| 10 | Journey waterfall: ledger seals | maya.compliance | Fixed | `retrieval.decided` (228 ms) and `answer.released` (229 ms) labels printed over each other ("#9#944") | Seals within 5 % of the axis share one tick, labelled "#943 · #944"; the screen-reader text keeps each seal | `components/journey/Waterfall.tsx` |
| 11 | Compare status line (`compare-announce`) | Presenter step 3 | Fixed | A result restored from the cache kept the previous run's status: Amy vs Rachel announced "The two responses are identical." (left over from Ken's step) | Restoring from the cache recomputes the status; nothing restored clears it | `components/compare/CompareView.tsx` |
| 12 | Ask (suggestions), history, settings | jdoe, priya.sre, ken.ext, amy.am, mlro.tan, leo.ops | Pass | All 26 suggestions answered or refused in 0.9 to 1.9 s; every citation opens a live `/ib/doc` passage; 0 console issues | | |
| 13 | Refusals | ken.ext | Pass | Velquarry and Bayfront refusals are byte-identical (text, sources, layout); no count or hint | | |
| 14 | 简体中文 | jdoe | Pass | Shell, greeting, composer, refusal card, sources, versions, history and settings flip to Chinese. The server's refusal sentence and scope note stay English (backend constants) | | |
| 15 | History row | jdoe | Pass (by design) | A row opens `/dev/journey/<req>` (Developer mode), as documented in `app/(user)/history/page.tsx` | | |
| 16 | Compare | jdoe viewing amy.am vs mlro.tan | Pass | Amy gets the approved customer script (cited); Rachel gets the case (STR filed, FC-2026-0147) | | |
| 17 | Compare, same person | ken.ext, Velquarry vs Bayfront | Pass | Identical marker shown | | |
| 18 | Mission control `/dev` | jdoe | Pass | Lock card for organisation numbers; pipeline, source freshness, model status | | |
| 19 | Mission control `/dev` | maya.compliance | Pass | Live p50 per stage (accept 64, Gate 1 5, Gate 2 17, seal 64 ms; end to end 157 ms); 347 requests, 82.4 % answered | | |
| 20 | Graph explore, search, select, expand, tabs, table, compare | jdoe (vs ken.ext) | Pass | Search "migration" gives 7 local hits plus the server search; Slack doc panel: Passage, Lineage, Links from `/ib/doc`; Compare: Jordan 867 items, Ken 24, "Ken can open none of the items on the left" | | |
| 21 | Walnut engine `/dev/brain` | jdoe | Pass | Live through the gateway: 932 entities, 2,246 links, 4,710 pointers | | |
| 22 | Trust centre `/dev/trust` | jdoe | Pass | | | |
| 23 | Journey waterfall, seeded denial | maya.compliance | Pass | `6600daa8…` (replay): Gate 2 Denied · forbidden · `not_permitted` on "Settlement keys rotation"; nothing released | | |
| 24 | Journey waterfall, fresh request | maya.compliance | Pass | `708fb11c…` asked in this pass: 6 stages, 3 seals, 7 allowed documents | | |
| 25 | Analytics `/dev/analytics` | maya.compliance | Pass | Every panel populated; Gate-2 reasons: verified 1,305, forbidden 3 (`not_member` 2, `not_permitted` 1); replay banner (304 replay requests) and Live-only toggle | | |
| 26 | Audit: reveal with reason, Verify, sandbox tamper | maya.compliance | Pass | Reveal needs a reason and returns the answer; Verify: chain intact, external head matches; tamper: every scenario as expected, real log not modified | | |
| 27 | Value calculator `/value` | maya.compliance | Pass | | | |
| 28 | Simulator `/dev/sim` | admin.it | Pass, with an open backend issue | Revoke → Jordan's DB-migration answer loses the Slack source (Compare). **Reset does not bring it back** (§4.1) | Restored by hand for the demo: silent remove plus a webhook-sending add on the mock (see §4.1) | backend |
| 29 | Presenter `/compare?present=1` | (signs in as mlro.tan, then maya.compliance) | Pass | P, then Space through all 10 steps: title, Amy vs Rachel, Ken identical, citation [2] sheet (live passage), journey as Maya, Verify, tamper, WorkBuddy (focus → `/dev/audit?latest=1` with the Newest row), proof, business, close. S toggles Stage | | |
| 30 | Slides `/present`, `/present/proof`, `/present/business`, `/present/close` | | Pass | | | |
| 31 | Legacy `/login`, `/map`, `/audit`, `/sim`, `/dev/styleguide` | | Pass | | | |
| 32 | All Developer pages (dev server) | | Pass (dev-only warnings) | React Flow warning 002 ("new nodeTypes or edgeTypes object") on `/dev` and `/dev/brain`; React 18.3 "Support for defaultProps will be removed" (`console.error`) for Recharts `XAxis`/`YAxis` on `/dev`, `/dev/analytics`, `/dev/styleguide` | None needed: the types are already module constants (React Flow 11's StrictMode double-render false positive) and Recharts 2.12.7 is pinned. Both are development-only: **0 console issues on the production build** for `/dev`, `/dev/analytics`, `/dev/brain`, `/dev/styleguide`, `/dev/trust`, `/value`, journey and audit | |

Hard rules checked on every fix: no hidden counts or denial details reach an asker (the "Another passage"
label and the merged seal tick show only what the asker or officer already had); restricted and made-up
questions still render identically (rows 13, 17).

## 3. Files changed in this pass

| File | Owner area | Change |
|---|---|---|
| `components/graph/layout.ts` | Graph | worker deadline, fallback, position validation |
| `components/graph/GraphCanvas.tsx` | Graph | re-measure on first frames |
| `components/graph/NodePanel.tsx` | Graph | version labels |
| `components/user/answerText.ts` | User | Slack version regex; `sentencesFor` tag attachment |
| `components/user/AnswerCard.tsx`, `lib/i18n/user.ts` | User | "Another passage of source n" |
| `lib/format.ts` | coordinator | `shortVersion` for Slack and Jira |
| `components/journey/Receipt.tsx`, `components/journey/Waterfall.tsx` | Journey | version style; merged seal ticks (already committed in `ac94931` with the tour work) |
| `components/audit/AuditConsole.tsx`, `components/audit/AuditTable.tsx` | Audit | `?latest=1`, chip years, parser notes |
| `lib/api.ts` (audit block) | Audit | `limit=500` |
| `components/compare/CompareView.tsx` | Compare | status on cache restore |

## 4. Open issues (not fixed here)

1. **Simulator Reset does not undo a revocation in the index (backend).** Revoke sends a
   `membership.changed` webhook, and the IB removes jdoe from the channel ACL in its index. `/admin/reset`
   (mock) reloads the world **without** webhooks, and the incremental poll compares the platform with
   its own cursor, which still lists jdoe as a member, so it sees no change. Jordan stays out of
   #db-migration in Gate 1 until a full sync (checked: more than 5 minutes later, still revoked). This
   breaks the demo's headline answer after a rehearsal. Restored for now with a silent remove
   (`?webhook=0`) followed by a normal add on the mock, after which Jordan's answer cites Slack again.
   Fix options: make mock `/admin/reset` emit `membership.changed`/`acl.changed` for whatever it
   restores, or make IB `/sim/reset` run `sync_all(full=True)` after the reset.
2. **Plain-English audit search, offline.** With `IB_FORCE_OFFLINE=1` the rule parser does not recognise
   documents: "Who retrieved the Q3 breach report?" becomes "the last 365 days" (100 events) and lists
   unrelated requests. The parser note now says so. With Hy3 live, the model fallback should apply.
3. **Graph blank canvas: root cause not reproduced.** The hardening covers a hung or failed worker, an
   unchecked cached layout and a zero-size container. If it recurs, capture the browser, the dist dir
   and the network panel.
4. **Development-only console noise** (React Flow 002, Recharts `defaultProps`): absent in production.
   Upgrading Recharts to 2.13 or later removes the second; that is a `package.json` change (coordinator).
5. **`limit` chip** in the audit "Interpreted as" row shows the raw key `limit: 100`. Cosmetic.
6. **Language.** The server's refusal sentence and scope note stay English in 简体中文 mode (backend
   constants, and the e2e tests require them verbatim).
