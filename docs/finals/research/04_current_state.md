# 04. Current state of the product (inventory for the UI rebuild)

Snapshot taken 2026-10-30 (read-only; no file was changed except this one). Paths are relative to the repo root `E:\Tenecent Hack_Walnut\Tencent\` unless shown absolute. Line numbers are as read on that date.

**Concurrent edits:** `databridge/apps/internal_brain/databridge_ib/pipeline.py` and `models.py` were modified at 2026-10-30 02:22-02:23, while this inventory was being written. The pipeline now records per-stage timings (`stage_ms`, pipeline.py:150-197). The line numbers below for pipeline.py match the 02:23 version.

**What the product is:** the Internal Brain (`databridge_ib`, port 8100) answers questions from four mock work platforms (Confluence, Jira, Slack, Drive), checks permissions twice before any passage reaches a model, and writes a hash-chained audit ledger. The web app (`internal_brain_web`, port 3100) is the front end. The Walnut original screens (`apps/frontend`, port 3300, gateway 8300) show the knowledge graph underneath.

---

## 1. Web app: `databridge/apps/internal_brain_web` (Next.js 14.2.15, React 18.3.1)

Stack: Next 14.2.15, React 18.3.1, Tailwind 3.4.13, `@radix-ui/react-dialog` 1.1.2, `lucide-react` 0.451.0, `clsx`. Tests: Playwright 1.48.0 and `@axe-core/playwright` 4.10.0. Scripts (`package.json`): `dev` (port 3100), `dev:mock` (mock on 3100), `build`, `build:mock` (`.next-mock`), `start`, `start:mock` (port 3101), `typecheck`, `e2e`.

### 1.1 Routes (`app/`)

| Route | File : line | What it shows | Signed-in roles |
|---|---|---|---|
| `/login` | `app/login/page.tsx` : `LoginPage` at 11 | Pick one of the 9 personas (`data-tour="personas"` at 85). Optional passcode field; the passcode is kept in `sessionStorage`. Admin-only IT users are sent to `/sim`, everyone else to `/`. | none |
| `/` | `app/page.tsx` : `AskPage` at 193 | "Ask" screen: question box, answer card with `[C#]` citation chips, checks strip ("How this was checked"), sources panel, history of the last questions (`api.history()` at 33). `ask()` at about 40-61, calls `api.ask()` at 47. Tour actions `ask-sample` (68). | any employee |
| `/map` | `app/map/page.tsx` : `MapPage` at 115 | Knowledge map of what the person may open: radial SVG, one circle per space or channel, list view on phones, search, place panel. Loads `api.map()` (line 30). 401 redirects to `/login`. | any employee |
| `/compare` | `app/compare/page.tsx` : `ComparePage` at 237 | "Same question, two people": two columns, each with its own session (slot b and slot c cookies). Starts with `jdoe` and `ken.ext`. Runs `slotApi` login, ask and map (line 117 uses `r.s.map()`). Tour action `compare-run` (144). Shown only when `NEXT_PUBLIC_IB_DEMO_COMPARE !== '0'`. | any employee |
| `/audit` | `app/audit/page.tsx` : `AuditPage` at 275 | Audit log table with plain-English search (`api.auditAsk`, line 87) or manual filters (`api.auditEvents`, line 65), a drawer per request, and "Verify audit chain". | `compliance_officer` only (nav at `AppShell.tsx:62`) |
| `/sim` | `app/sim/page.tsx` : `SimPage` at 142 | Simulator with four actions (`SIM_ACTIONS` list at about lines 17-50): "Edit failover runbook (new version)", "Remove jdoe from #db-migration", "Restrict 'Settlement keys rotation' page", "Reset demo". Each posts to `/api/ib/sim/<action>` and writes to a log. | `admin_it` only (`AppShell.tsx:63`) |
| `/api/ib/[...path]` | `app/api/ib/[...path]/route.ts` (252 lines) | Same-origin proxy (GET and POST) to the Brain's `IB_API_URL` (default `http://127.0.0.1:8100`, line 206). Mock mode serves fixtures instead. See 1.2. | n/a |

There is no separate landing page beyond `/`. Navigation items are built in `components/AppShell.tsx:58-64`: Ask (always), Map (always), Compare (flag), Audit (compliance), Simulator (admin IT).

### 1.2 The proxy (`app/api/ib/[...path]/route.ts`)

- Closed allowlist `ALLOWED` at lines 28-38, which covers: `health`; `POST auth/(demo-login|logout)`; `GET auth/whoami`; `POST ib/ask`; `GET ib/(history|sources|map)`; `GET audit/(events|verify)`; `GET audit/doc/<id>/accessors`; `POST audit/(ask|reveal)`; `POST sim/<action>`. Webhooks are not reachable.
- Compare slots: `x-ib-slot` header (`b` or `c`) forwards cookies `ib_session_b` or `ib_session_c`. `SLOT_ALLOWED` (47-52) only allows `auth/demo-login`, `auth/logout`, `auth/whoami`, `ib/ask` and `ib/map` in a slot.
- POST checks: same-origin (`sameOrigin`, 80-98, uses `IB_PUBLIC_ORIGIN` if set); JSON body only; 64 KiB limit (`MAX_BODY`, line 41).
- Deadline: 90 s on the proxy (`DEADLINE_MS`, line 42). The browser client has 95 s (`lib/api.ts:36`).
- Upstream 5xx bodies are replaced with a fixed envelope (`{"error":"temporarily unavailable"}`); only the two known 503 messages pass through.
- Mock switch: `NEXT_PUBLIC_IB_MOCK=1` or `IB_MOCK=1` (`isMock`, 164-166) hands the request to `lib/mock/server.ts`.

### 1.3 Components (`components/`)

| File | Lines | One-line purpose |
|---|---|---|
| `AnswerPanel.tsx` | 327 | Answer text with citation chips (`AnswerText` 24), citation list (`CitationList` 89), model badge (`ModelBadge` 128), answer card (`AnswerCard` 170), "How this was checked" strip (`ChecksStrip` 236), error and loading states (293, 316). |
| `AppShell.tsx` | 162 | Header, role-based nav, who-am-I, sign-out, theme and tour buttons. Provides `useMe()` (context). Exports `DEMO_COMPARE` (24). |
| `Brand.tsx` | 22 | Brand mark, `PRODUCT` ("Walnut Internal Brain"), `SUBTITLE`, and `MockBanner` (shown when mock mode is on). |
| `InfoTip.tsx` | 112 | Info popover used on each screen to explain it. |
| `PlatformIcon.tsx` | 24 | Platform icon and label (Slack, Confluence, Jira, Google Drive). |
| `SourcesPanel.tsx` | 107 | Per-platform sync status (last sync, lag, within SLO). Refreshes every 30 s (`setInterval`, line 38). |
| `ThemeToggle.tsx` | 30 | Dark or light theme toggle (`data-theme`). |
| `ui.tsx` | 85 | Shared primitives: `Button`, `Badge` (tones neutral, ok, warn, bad, accent), `Card`, `SrOnly`, `Field`, `inputClass`. |
| `audit/AuditTable.tsx` | 141 | Audit table, one row per request; `titleFor` (7), `Decisions` (24) show allow or deny per document. |
| `audit/AuditDrawer.tsx` | 191 | Side drawer with one request's details and the "reveal answer" flow (calls `api.reveal`). |
| `audit/VerifyPanel.tsx` | 112 | "Verify audit chain" panel (calls `api.verify`). |
| `map/KnowledgeMapView.tsx` | 211 | Radial SVG knowledge map (person in the centre, platforms either side). Exports `MapSelection` type (10). |
| `map/layout.ts` | 109 | Deterministic radial layout (`W = 1000`, `layoutMap`). |
| `map/PlacePanel.tsx` | 217 | Side panel for one space or channel: item list, search (`itemMatches`, 13). |
| `map/MapSummary.tsx` | 39 | "You can open N items on M platforms" sentence (`TotalsSentence`, `MapSummary`). |
| `map/MapLoading.tsx` | 29 | Indeterminate loading bar (the map takes seconds). |
| `tour/Tour.tsx` | 381 | Guided tour overlay (`TourOverlay` 36), tour button (324) and one-time offer (340). Highlights `data-tour` anchors and drives page actions. |

### 1.4 API client (`lib/api.ts`)

Base path `/api/ib` (line 17). Every call is same-origin with the httpOnly cookie; the module never sees a token. `call<T>()` is at 46-79. Timeout 95 s (line 36).

| Client function (line) | HTTP call (proxied to Brain) | Response type (`lib/types.ts`) |
|---|---|---|
| `api.login(persona, passcode?)` (141) | `POST /auth/demo-login` | `{sub, roles, name}` (inline) |
| `api.switchTo(persona)` (147) | `POST /auth/demo-login` (stored passcode) | same |
| `api.logout()` (148) | `POST /auth/logout` | `{ok}` |
| `api.whoami()` (149) | `GET /auth/whoami` | `WhoAmI` (36-41) |
| `api.ask(question)` (150) | `POST /ib/ask` | `AskResponse` (27-34) |
| `api.history()` (151) | `GET /ib/history` | `{items: HistoryItem[]}` (53-57) |
| `api.map()` (154-157) | `GET /ib/map` (deduplicated in flight) | `KnowledgeMap` (144-151) |
| `api.sources()` (158-161) | `GET /ib/sources` | `SourceStatus[]` (43-51); accepts `{sources}` or an array |
| `api.auditEvents(filter)` (162-165) | `GET /audit/events?...` | `AuditEvent[]` (73-88), rows grouped by request |
| `api.auditAsk(question)` (166-169) | `POST /audit/ask` | `AuditAskResponse` (94-97) |
| `api.reveal(req_id, reason)` (170-175) | `POST /audit/reveal` | `RevealResult` (107-110) |
| `api.verify()` (176) | `GET /audit/verify` | `VerifyResult` (99-105) |
| `api.sim(action)` (177) | `POST /sim/<action>` | `SimResult` (112-116) |
| `slotApi(slot).login / whoami / ask / map` (181-188) | same paths with `x-ib-slot` | same types |
| `withSlotLocks()` (197-205) | none (Web Locks API) | refuses to run without `navigator.locks` (plain http on a non-localhost address) |

Not called by the UI: `GET /audit/doc/<id>/accessors` (allowed by the proxy, used only by the MCP audit server) and `GET /health`.

### 1.5 Types (`lib/types.ts`)

`Platform` (4, slack, confluence, jira, drive); `Citation` (6-19: tag, doc_id, platform, container_name, title, deep_link, version, updated_at, content_checked_at, authz_checked_at, live_read, stale); `ModelInfo` (21-25: provider, model, fallback_used); `AskResponse` (27-34); `WhoAmI` (36-41); `SourceStatus` (43-51); `HistoryItem` (53-57); `Gate2Decision` (59-63); `ConsideredMeta` (65-70); `AuditEvent` (73-88); `AuditFilter` (90-92: user, container, doc, since, until, decision); `AuditAskResponse` (94-97); `VerifyResult` (99-105); `RevealResult` (107-110); `SimResult` (112-116); `ALLOW_VERDICTS` (118: verified, drift_material); `MapItem` (122-129), `MapContainer` (131-136), `MapPlatform` (138-142), `KnowledgeMap` (144-151).

### 1.6 Personas (`lib/personas.ts`)

Fixed list, because there is no persona endpoint (comment at line 9). `PERSONAS` runs from line 12 to 121 and has **9** entries. Each has `id`, `name`, `role`, `note` and four suggested questions.

| id | Shown name | Role |
|---|---|---|
| `jdoe` | Jordan Doe | Backend engineer (private #db-migration) |
| `priya.sre` | Priya | On-call SRE |
| `ken.ext` | Ken | External contractor (Slack Connect guest) |
| `sara.sec` | Sara | Security |
| `maya.compliance` | Maya | Compliance officer (audit access) |
| `leo.ops` | Leo | Product and ops |
| `amy.am` | Amy | Account manager (no FinCrime access) |
| `mlro.tan` | Rachel | MLRO (FinCrime) |
| `admin.it` | IT admin | Platform simulator |

`SAMPLE_QUESTION` (line 124) is the headline question: "What's the status of the database migration project and were there any blockers raised in Slack last week?". `AUDIT_SUGGESTIONS` (126-130) are three audit prompts.

### 1.7 Tour (`lib/tour.ts`)

`TOUR_STEPS` (lines 30-90) has 6 steps. The store is in `sessionStorage` (`ib-tour`) and a one-time offer is in `localStorage` (`ib-tour-offer`).

| # | id | Route | Persona | Anchor (`data-tour`) | Page action |
|---|---|---|---|---|---|
| 1 | personas | `/login` | none | `personas` | none |
| 2 | answer | `/` | jdoe | `answer` (ready `ask-form`) | `ask-sample` |
| 3 | map | `/map` | jdoe | `map` | none |
| 4 | compare | `/compare` | jdoe | `compare-results` (ready `compare-form`) | `compare-run` |
| 5 | verify | `/audit` | maya.compliance | `verify-panel` | `verify` |
| 6 | sim | `/sim` | admin.it | `sim-revoke` | none |

Helpers: `startTour` (141), `goToStep` (148), `emitTourAction` (174), `useTourAction` (177-187).

### 1.8 Mock mode (`lib/mock/`)

- Switch: `NEXT_PUBLIC_IB_MOCK=1` (or `IB_MOCK=1`). The proxy then calls `handleMock` in `lib/mock/server.ts` (route handler at 157-268: health 157, demo-login 159, logout 172, whoami 177, ask 179, history 205, map 214, sources 220, audit 231-265, sim 268).
- `server.ts`: an in-memory state and ledger on `globalThis.__ibMock` (34-36), so it resets when the Next process restarts. It hash-chains the ledger with SHA-256 (`head`, 114-118). Session cookie name `ib_session`.
- `fixtures.ts` (293 lines): `SCOPE_NOTE` (6), `REFUSAL_TEXT` (8), `ROLES` (10-19: names and roles for the 9 personas, for example "Ken Ito", "Alex Mercer"), `DOCS` (75-101), `SCENARIOS` (123), `citation()` (260), `SEED_HISTORY` (279: synthetic past questions, 40 items relative to today's date).
- `map.ts` (205 lines): synthetic Company A estate for `/ib/map`.
- Mock names differ from the persona names in `lib/personas.ts` (for example Ken Ito against "Ken"). The header uses the persona name, so this is cosmetic.

### 1.9 Tailwind theme and colours

- `tailwind.config.ts` (36 lines): `content` globs `app/`, `components/`, `lib/` (line 6). Colours use RGB CSS variables (`v()` helper, line 3): `bg`, `surface`, `raised`, `line`, `line-strong`, `fg`, `muted`, `accent`, `accent-fg`, `accent-solid`, `ok`, `warn`, `bad`, `focus` (lines 9-24). `fontFamily.sans` is Inter with system fallbacks, `mono` is a system mono stack (25-28). `fontSize['2xs']` is 0.75 rem with 1 rem line height (29-31). No plugins.
- `app/globals.css`: tokens on `:root` (7-25) are the default **dark** theme: bg 11 13 16, surface 18 21 26, raised 25 29 35, line 40 45 53, fg 231 233 236, muted 163 171 182, accent 128 172 255 (blue), accent-solid 47 100 208, ok 86 201 146, warn 232 184 88, bad 244 124 114. Light theme: `:root[data-theme='light']` (27-45), with bg 246 247 249, surface 255 255 255, accent 29 86 196, ok 20 110 68, warn 128 84 0, bad 176 34 24.
- Rules in `globals.css`: visible focus ring everywhere (63-67), reduced-motion override (69-78), skip link (80-93), citation target highlight (96-99), map node focus ring (102-108), indeterminate loader animation (111-121).
- Design rule noted in the file: `ok`, `warn` and `bad` are always paired with an icon and a text label.

### 1.10 End-to-end tests (`tests/e2e/`, 38 tests after expanding the viewport loops)

Config `playwright.config.ts`: runs against mock mode on port 3101 (`build:mock` then `start:mock`), serial (`workers: 1`), Desktop Chrome at 1280x860.

| File | Tests (names as written) |
|---|---|
| `a11y.spec.ts` | axe scan at desktop and mobile: "1 login", "2 ask with answer + 3 sources panel", "4 audit with verify result and drawer", "5 simulator". Also "light theme: ask and audit stay AA" and "no horizontal page overflow at 390px". |
| `ask.spec.ts` | login, ask, citations and scope note visible; refusal renders calmly with the same layout; 503 shows "nothing was answered"; model badges (fallback, extractive, live re-read, stale) are text; sources panel global status; simulator revocation and v8 runbook reflected on the next question; role gates (employees cannot open audit or simulator). |
| `audit.spec.ts` | NL question shows the parsed filter first; manual filter; verify; reveal. |
| `compare.spec.ts` | answers and maps differ, and the main session keeps its identity; slot-b login sets only `ib_session_b`; 390 px accessible with no overflow; a column whose slot turns out to be someone else shows an error, not their data. |
| `keyboard.spec.ts` | keyboard-only sign-in, ask and citation follow; keyboard-only audit filters, verify and drawer. |
| `map.spec.ts` | jdoe totals, four platforms, place opens its items, search narrows; ken.ext sees only his two places; 390 px list view; light theme with a place open and the tour stays AA. |
| `proxy.spec.ts` | dot segments and odd document ids never reach a route; cross-origin POST and non-JSON body refused; unknown `x-ib-slot` is a 400; non-http(s) source link shown as text. |
| `screenshots.spec.ts` | captures screenshots at 1280 and 390 px. |
| `tour.spec.ts` | tour opens, moves with Next and arrow keys, closes with Esc; fits at 390 px; "Not now" dismisses the offer; checks strip states who, live checks and audit request id. |

Unit tests for the web app: none found under `apps/internal_brain_web` (only Playwright).

---

## 2. Internal Brain API: `databridge/apps/internal_brain/databridge_ib`

FastAPI app built by `create_app` (`app.py:81`). Allowlisted prefixes (`app.py:33`): `/health`, `/auth/`, `/ib/`, `/webhooks/`, `/audit/`, `/sim/`. The app refuses to start if any other route is mounted (215-217).

### 2.1 HTTP routes

Auth column: **session** means the `ib_session` cookie or `Authorization: Bearer` token, checked by `principal_dependency` (`app.py:52-70`). Employee roles are `employee`, `compliance_officer` and `admin_it` (`EMPLOYEE_ROLES`, line 35).

| Method | Path | File : line | Purpose | Auth |
|---|---|---|---|---|
| GET | `/health` | `app.py:87` | liveness | none |
| POST | `/auth/demo-login` | `app.py:92-113` | demo sign-in as one persona; writes `auth.login` to the ledger first; sets httpOnly cookie for 8 h (`app.py:110-112`). Returns 404 unless `IB_DEMO_LOGIN=1`. If `IB_DEMO_PASSCODE` is set, it must match. | none (feature flag) |
| POST | `/auth/logout` | `app.py:115-130` | revokes the token's `jti`; 503 if the revocation cannot be recorded | session |
| GET | `/auth/whoami` | `app.py:132-135` | sub, name, roles, employment | session |
| POST | `/ib/ask` | `app.py:138-152` | the ask pipeline (section 2.3); body `AskRequest` (`question`, 1-2000 chars, `models.py:154-155`); returns `AskResponse` | session + employee role |
| GET | `/ib/map` | `app.py:154-163` | the person's knowledge map (`knowledge_map.py:33`); writes `map.viewed` first | session + employee role |
| GET | `/ib/history` | `app.py:165-174` | last 50 `request.accepted` events for this person, from the audit projection (empty if no projection) | session + employee role |
| GET | `/ib/sources` | `app.py:176-189` | per-platform last sync, lag, SLO (300 s in mock, 900 s when `IB_LIVE=1`) | session + employee role |
| POST | `/webhooks/{platform}` | `app.py:192-209` | source change feed; verified by HMAC `X-IB-Signature` plus timestamp window (`connectors/_http.py:10`, `204-208`, `225`); 401 on rejection, 404 for an unknown platform, 256 KiB limit | HMAC signature (not reachable from the web app) |
| GET | `/audit/events` | `audit/routes.py:111` (prefix `/audit`, line 85) | filtered ledger rows: user, container, doc, since, until, decision, kind, limit | compliance_officer |
| GET | `/audit/doc/{doc_id}/accessors` | `audit/routes.py:138` | who accessed a document | compliance_officer |
| POST | `/audit/ask` | `audit/routes.py:152` | plain-English question turned into an `AuditFilter` (`audit/nl_filter.py`) and run | compliance_officer |
| POST | `/audit/reveal` | `audit/routes.py:180` | reasoned reveal of a stored answer; a reason is required; the answer is checked against the sealed digest | compliance_officer |
| GET | `/audit/verify` | `audit/routes.py:221` | verifies the hash chain and the stored-answer digests; writes an `audit.query` event | compliance_officer |
| POST | `/sim/runbook-update` | `sim.py:50` | edits a Confluence page through the mock admin API (`demo_actions.runbook_update` in the world manifest) | admin_it (`sim.py:29`) |
| POST | `/sim/revoke-channel` | `sim.py:55` | removes a user from a Slack channel (mock admin) | admin_it |
| POST | `/sim/restrict-page` | `sim.py:60` | restricts a Confluence page (mock admin) | admin_it |
| POST | `/sim/reset` | `sim.py:66` | resets the mock platforms (`/admin/reset`) | admin_it |

Routers come from `make_audit_router` (`audit/routes.py:76`, mounted in `bootstrap.py:119-124`) and `make_sim_router` (`sim.py:26`, mounted in `bootstrap.py:125`). Both receive `require_role` from `app.py:73-78`.

**Not HTTP, but on the same API:** two MCP servers that call the HTTP API with a bearer token. `mcp_server.py`: `ask_internal_brain` (line 157) and `my_recent_questions` (199). `audit_mcp_server.py`: `audit_events` (178), `audit_doc_accessors` (214), `audit_verify` (238). A bearer call with `x-ib-channel: workbuddy-mcp` is recorded as channel `workbuddy-mcp` (`app.py:144-146`).

The mock platforms app (port 8200) has its own routes, mounted at `apps/mock_platforms/mock_platforms/app.py:60-64`: `/slack/api`, `/confluence/wiki`, `/jira/rest/api/3`, `/drive/v3`, and `/admin` (the admin routes include `POST /admin/reset` at `admin.py:46`, Slack member add and remove at 91 and 96, and Confluence restrict at 203).

### 2.2 Pipeline stages, in order

From the docstring (`pipeline.py:1-9`) and the code of `Pipeline._ask` (99-200):

1. **Authn** (`app.py` dependency `current`, 52-70; role check `EMPLOYEE_ROLES`, `app.py:140`). Identity comes only from the signed token.
2. **Ledger `request.accepted`** (write-ahead, `pipeline.py:107-110`).
3. **Parse time window** (`timewin.parse`, line 112). A ranking preference in ask. A hard filter in audit (`timewin.py:3-6`).
4. **Gate 1 and rank over the permitted corpus** (`index.readable(user, epoch)` at 116; `index.search(..., relevant_only=True)` at 117-118). Per-platform slots (`PLATFORM_HINTS`, 40-45; `PER_PLATFORM_K = 4`, line 37) are added at 119-126.
5. **Relevance gate** (the `relevant_only` flag in the same search call).
6. **Gate 2, live check per candidate** (`_gate2` at 135 calls `gates.check_all`, one shared budget of `gate2_timeout_s = 2.0` s at line 80; `gates.py:28-51`). A timeout, error or 429 is a DENY. `_read_through` (136) re-fetches a document whose version moved and admits it only if the new version still passes.
7. **Select and linked objects** (`_select` 139; `_linked` 141; second Gate 2 on linked ids 143-146; `MAX_LINKED = 4`).
8. **Ledger `retrieval.decided`** (write-ahead before any model call, 152-158).
9. **Generate**: `ModelChain.answer` (hy3, then deepseek-v4.1-flash, `llm.py:88-104`), or the deterministic extractive answer if no model is configured or every model fails (pipeline 162-179).
10. **Grounding wall**: `narrate.drop_addressed_to_ai_counted` (170), and the answer must cite at least one permitted passage (171-174), otherwise the extractive answer is served (175-179).
11. **Content store `put` then ledger `answer.released`** (write-ahead before the response, 184-197). Stage timings `stage_ms` (accept, gate1, gate2, seal, compose) are recorded at 187-188 and written at 197.
12. **Respond** with one response shape for every outcome (199-200).

Constants: `MAX_PASSAGES = 14`, `MAX_CHUNKS_PER_DOC = 2`, `MAX_LINKED = 4`, `PER_PLATFORM_K = 4` (`pipeline.py:34-37`); `TOP_K = 12` (`config.py:20`); `POLICY_VERSION = "ib-policy-1"` (`config.py:21`).

Any ledger failure aborts the request (fail closed): `app.py:149-151` returns 503.

### 2.3 Ledger event types

`EVENT_KINDS` (`models.py:192-193`), 13 kinds:

| Kind | Model (`models.py` line) | Written when |
|---|---|---|
| `request.accepted` | `RequestAccepted` (210) | ask starts: sub, channel (`ui`, `workbuddy-mcp`, `api`, `replay`), question text, question sha256, policy version, directory epoch |
| `retrieval.decided` | `RetrievalDecided` (220) | before any model call: time window, considered docs with platform, container and title, Gate 2 verdict and reason code per doc, released docs |
| `answer.released` | `AnswerReleased` (231) | before the response: model provider, model and fallback flag, prompt version, answer sha256, answer ref, refused flag, citations (doc id, version, two checked-at times), dropped sentence count, `latency_ms`, `stage_ms` |
| `request.failed` | `RequestFailed` (244) | an ask raised an exception (error class only) |
| `audit.query` | `AuditQueryEvent` (251) | any call to `/audit/*` (filter, outcome, result count) |
| `audit.reveal` | `AuditReveal` (278) | a reveal is requested (reason and target req id) |
| `auth.login` | `SessionStarted` (258) | demo sign-in (roles) |
| `map.viewed` | `MapViewed` (267) | the map is built (docs listed, items returned, containers) |
| `acl.changed`, `membership.changed`, `directory.changed`, `ingest.sync` | `SourceChangeEvent` (285) | source change feed or sync |
| `anchor.published` | `AnchorPublished` (291) | the ledger head is published off-host (`audit/anchor.py`) |

Storage: `var/ib/ledger/access.jsonl` (`config.py:72-73`), sealed with `IB_LEDGER_KEY`. Anchors in `var/ib/anchors/access.anchors.jsonl`. The read model is SQLite (`audit/query.py:119-140`, tables `meta`, `events`, `doc_events`, `docs`).

### 2.4 What an analytics dashboard could use, and what exists

**Data already recorded per request (no new event needed):**
- Latency: `answer.released.latency_ms` (total) and `stage_ms` per stage (accept, gate1, gate2, seal, compose). `stage_ms` appears in the 02:23 version of `pipeline.py` only.
- Model: `answer.released.model` (provider, model, fallback_used). Fallback and extractive use are visible here.
- Gate decisions: `retrieval.decided.gate2` (verdict and reason code per document, for example `verified`, `forbidden`, `timeout`, `drift_material`) and `considered` against `released`.
- Refusals: `answer.released.refused`; dropped sentences: `dropped_sentences_count`.
- Channel: `request.accepted.channel` (UI against MCP).
- Platform and container of each considered document: `considered_meta`.
- Sign-ins: `auth.login`. Map views: `map.viewed`. Audit activity: `audit.query`.
- Caution: the ledger stores question text in clear (`request.accepted.question`) and the user id (`sub`). A dashboard that shows questions per person is a privacy question.

**Aggregate endpoints that already exist:** none. `GET /audit/events` returns rows, not counts. Closest: `GET /ib/sources` (per-platform freshness, one row per platform), `GET /ib/map` (`totals`: items, containers, platforms, for the caller only) and `GET /audit/verify` (record count only). The SQLite read model (`audit/query.py`) is set up for filtered searches, so an aggregate query would be a small addition, behind the compliance role.

### 2.5 LLM configuration (`llm.py`)

- Provider: Tencent TokenHub, OpenAI-compatible endpoint `https://tokenhub-intl.tencentcloudmaas.com/v1` (`TOKENHUB_BASE`, line 29).
- Model chain: `DEFAULT_CHAIN = ("hy3", "deepseek-v4.1-flash")` (line 30). Models are reached through LiteLLM as `openai/<model>` (`_config_for`, 53-57).
- Chain override: `IB_MODELS` (comma-separated), line 45.
- Environment variables: `TOKENHUB_API_KEY` (lines 34, 61), `TOKENHUB_BASE` (64), `OPENAI_API_KEY` and `OPENAI_API_BASE` (35, 40; any OpenAI-compatible endpoint works), `IB_FORCE_OFFLINE=1` to disable the model (78).
- Any exception, empty answer or grounding failure moves to the next model, then to extractive (`llm.py:93-103`; `pipeline.py:175-179`). Each attempt uses `seed=7` (`llm.py:96`).
- Embeddings: `llm.py:56` configures an embed alias (`openai/kinfra-text-embedding-0.6b`), but `bootstrap.py:63` builds the index without an embedder, so the offline hashing embedder is used (`index.py:159-161`). **The model-based embedding is not in use.** The vector leg has little weight (`index.py:43` comment).
- Status: hy3 has **not** been run live. `RESULTS.md` says "PENDING — no TokenHub key in this environment". Live answers are the deterministic extractive path unless the environment is set.

### 2.6 Configuration (environment variables)

From `config.py` (lines given) and the other modules:

| Variable | Default | Where | Meaning |
|---|---|---|---|
| `IB_VAR` | `databridge/var/ib` | `config.py:33` | state folder (ledger, index, directory, revoked tokens) |
| `IB_WORLD` | `fixtures/enterprise/world.json` | `config.py:34`, `13` | world file |
| `IB_<PLATFORM>_BASE`, `IB_MOCK_BASE` | `http://127.0.0.1:8200` | `config.py:37` | platform API base |
| `IB_<PLATFORM>_PREFIX` | per platform | `bootstrap.py:48` | API path prefix |
| `MOCK_SVC_TOKEN` / `MOCK_ADMIN_TOKEN` / `MOCK_WEBHOOK_SECRET` | `svc-dev` / `admin-dev` / `whsec-dev` | `config.py:39-41` | dev credentials |
| `IB_TOKEN_KEY` / `IB_LEDGER_KEY` | built-in dev keys (`config.py:23-24`) | `config.py:42-43` | signing keys; a deployed build refuses the defaults (`config.py:48-66`) |
| `IB_DEPLOYED` | off | `config.py:44` | enables the deploy checks and `Secure` cookies |
| `IB_DEMO_LOGIN` | off | `config.py:67` | enables demo sign-in |
| `IB_DEMO_PASSCODE` | empty | `app.py:98` | passcode for demo sign-in; at least 8 chars when deployed |
| `IB_LIVE` | off | `config.py:68` | selects the live SLO (900 s instead of 300 s) |
| `IB_ANCHOR_FILE`, `IB_ANCHOR_INTERVAL_S` | `var/ib/external_anchor/heads.jsonl`, 900 | `config.py:45-46`, `bootstrap.py:90` | off-host head file (default is local, not off-host) |
| `IB_ANCHOR_SLACK_WEBHOOK` | unset | `bootstrap.py:92` | mirrors the anchor to Slack |
| `IB_PORT` / `IB_HOST` / `IB_POLL_S` | 8100 / 127.0.0.1 / 120 | `bootstrap.py:144-146` | server bind and source poll interval |
| `IB_MODELS`, `IB_FORCE_OFFLINE`, `TOKENHUB_API_KEY`, `OPENAI_*` | see 2.5 | `llm.py` | model chain |
| `IB_URL`, `IB_TOKEN` | `http://127.0.0.1:8100` (inferred) | `mcp_server.py:28-32`, `audit_mcp_server.py:31-36` | MCP servers |

Other constants: `GATE2_TIMEOUT_S = 2.0` (`config.py:19`), `SLO_MOCK_S = 300` and `SLO_LIVE_S = 900` (`config.py:16-17`), session lifetime 8 h (`app.py:110`), webhook window (`connectors/_http.py`, `WEBHOOK_WINDOW_S`).

---

## 3. Original Walnut screens: `databridge/apps/frontend` (port 3300)

- **Stack:** Next 14.2.15, React 18.3.1, Tailwind 3.4.13, `@tanstack/react-query` 5.59, `@tanstack/react-table` 8.20, Radix popover, tabs and dialog, `class-variance-authority`, `react-markdown` 9, `recharts` 2.12.7 (stats), `lucide-react`.
- **Graph libraries:** `reactflow` 11.11.4 draws the canvas (`components/graph/graph-canvas.tsx:31`, `components/brain/brain-graph.tsx:3`, `components/ontology/schema-view.tsx:13`). `d3-force` 3 computes the layout (`components/graph/layout.ts`, `lib/brain-layout.ts`, `components/brain/brain-graph.tsx`).
- **Launch:** `cd databridge; bash scripts/run_walnut_original.sh` (`start`, `status` or `stop`). It starts the gateway on `127.0.0.1:8300` (`apps/gateway/serve.py`) with `DATABRIDGE_PACKS=enterprise`, `WALNUT_GRAPH_DB_PATH=var/graph/enterprise.db`, `WALNUT_DEV_AUTH=1`, then builds (`next build`, with `NEXT_PUBLIC_GATEWAY_URL=http://127.0.0.1:8300` and `NEXT_PUBLIC_WALNUT_TOKEN=dev-local-only`, both baked in at build time) and serves on port 3300. It refuses to start on 8100, 8200 or 3100.
- **Data build:** `scripts/build_enterprise_graph.py` flattens `fixtures/enterprise/world.json` into row files (`fixtures/enterprise/graph_rows/`) and then into `var/graph/enterprise.db`. Default view is `jdoe` (`--as <persona>` to change). The graph is built for that one view, so the screens do not check access per request.
- **API called:** only the Walnut gateway, not the Brain. Base URL in `apps/frontend/lib/api.ts:9`, token header from `lib/api.ts:16`. Endpoints by client file:
  - `lib/graph-api.ts`: `/graph/facets` (471), `/graph/neighborhood` (474), `/graph/node/{id}` (486), `/graph/edge/{id}` (488), `/graph/path` (490), `/graph/search` (492), `/graph/reload` (493). Gateway handlers: `apps/gateway/databridge_gateway/graph_routes.py` 920, 947, 1107, 1233, 1306, 1360, 1411.
  - `lib/brain-api.ts`: `/brain/query` (`brainApi.query`, line 30), `/brain/timeline/{id}` (39), `/brain/node/{id}` (42), `/brain/node/{id}/sheet` (52). Handlers in `apps/gateway/databridge_gateway/main.py`: `/brain/graph` 691, `/brain/timeline` 722, `/brain/node/.../sheet` 729, `/brain/query` 762, `/brain/ingest` 913, `/brain/path` 937, `/brain/insights` 945, `/brain/node/{id}` 950.
  - `lib/evidence-api.ts`: `/evidence/trace/{id}` (237), `/evidence/recent` (238), `/evidence/sources`. Handlers in `evidence_routes.py` 827, 870, 879.
  - `lib/ontology-api.ts`: `/ontology`, `/ontology/packs`, `/ontology/proposals`, `/ontology/audit`. Handlers in `ontology_routes.py` 1598-2679.
  - `lib/trace-*`: `/trace` (`trace_routes.py` 105), `/trace/{request_id}` (115).
  - `/health` (`main.py:544`).

**Routes (`apps/frontend/app/`):**

| Route | What it shows on Company A |
|---|---|
| `/` (`app/page.tsx`) | redirects to `/trace` (`redirect("/trace")`). Note: `/trace` is empty here (see section 7). |
| `/graph` (`app/graph/page.tsx`, 27 lines) | the knowledge graph: 932 nodes and 2,246 links for `jdoe` (1,219 and 2,919 unrestricted, per the README). Search, expand on double-click, click for the pointer behind each value. Canvas: `components/graph/graph-view.tsx` and `graph-canvas.tsx:543`. `?run=<agent>/<run_id>` overlays an agent traversal. |
| `/brain` (`app/brain/page.tsx`, 232 lines) | the same graph as a company brain with a colour legend, counts, insight and stats panels, an ask panel (`/brain/query`), a drop zone for ingest, and export to Sheets. The **time slider** (`components/brain/time-slider.tsx:56`) hides everything created after the chosen date. Click a node for its evidence sheet. Canvas: `components/brain/brain-graph.tsx:407`. |
| `/ontology` (`app/ontology/page.tsx`) | the `enterprise` pack: 7 node kinds and 8 link kinds, how they connect, counts, proposals and the ontology audit log. |
| `/evidence` (`app/evidence/page.tsx`) | provenance for one node or link, step by step. Default example: `/evidence?entity=knowledge_page%3Acf-1001` (the DB Migration Plan page). |
| `/trace` (`app/trace/page.tsx`) | agent and adapter activity, the message trail. Empty on Company A because nothing is ingested live. |
| `/demo` (`app/demo/page.tsx`) | the original guided tour over the screens above (about 80 s, caption bar). |
| `/intelligence` | business-intelligence agent asks, subgraph, one-time tasks (`/intelligence*` endpoints). Not in the README's list. |
| `/agents`, `/artifacts`, `/builder`, `/connectors`, `/convert`, `/data`, `/evals`, `/frontdesk` (+ `[id]`), `/impact`, `/intake`, `/materialise` | other platform pages, not part of the Company A demo as written in the README. |
| `/ask`, `/schema` | retired. Each shows a "This screen was retired" notice (`app/ask/page.tsx`, 31 lines; `app/schema/page.tsx`, 32 lines). |

---

## 4. Data: `databridge/fixtures/enterprise` (Company A)

Counts from `world.json` and `manifest.json` (read-only):

| Item | Count | Notes |
|---|---|---|
| People (directory users) | 32 | `directory.users`; 16 groups |
| Confluence pages | 209 | 7 spaces; 25 restricted, 1 archived, 1 trashed |
| Jira issues | 251 | 4 projects; 14 with a security level |
| Slack threads | 602 | 3,061 messages in 15 channels; 5 private, 1 Slack Connect |
| Drive files | 81 | 12 folders; 2 shared drives |
| Raw documents total | 1,143 | 209 + 251 + 602 + 81. `RESULTS.md` says "1140 docs indexed" for shard A0. The gap of 3 is not reconciled here. |
| Planted facts | 49 | `manifest.json` `facts`; 17 id families |
| Questions (eval) | 464 | direct 135, paraphrase 135, metadata 135, canonical 32, aggregation 27; 3 with `applies_after` |
| Audit replay rows | 40 | `world.json` `audit_replay`. The running app does not load them (no consumer outside the generator and tests). |
| P2 pairs | 3 | `manifest.json` `p2_pairs` |

**Planted fact families** (ID prefix: count): ACL 6, S1 5, SENS 5, S1b 4, INJ 4 (injection), FINCRIME 4, MK 3, S1c 2, S4 2, S2 2, S3 2, Ops 2, SEC 2, VENDOR 2, FEE 2, COMP 1, CX 1. The fintech layer (FC, MK, FEE, SENS) is described in `fixtures/enterprise/README.md`.

**Named demo entities:** the fictional customer "Velquarry Trading Pte Ltd" (FinCrime case; the approved customer script is in space `cx`), "Bayfront Spice Traders" (the non-existent comparison name), `#db-migration` (private, jdoe is a member), the `DBMIG` tickets, and the "Settlement keys rotation" page (`cf-1004`, `demo_actions.restrict_page`).

**How the world is generated:**
- Generator: `fixtures/enterprise/generate.py` (stdlib only). Parameters: `--seed` (default 7), `--now` (default `2026-10-13T09:00:00+08:00`), `--scale` (`demo` or `company-a`), `--out`. Output is byte-identical for the same arguments.
- Command (from `databridge/`): `.venv\Scripts\python.exe fixtures\enterprise\generate.py --seed 7 --now 2026-10-13T09:00:00+08:00 --scale demo`. It **overwrites** `world.json` and `manifest.json` in the fixture folder by default (`--out` defaults to that folder, line 2076). Benchmark: `--scale company-a --out <dir>` (never committed, line 2080).
- Scale presets `SCALES` at `generate.py:50-54`: `demo` (200 pages, 250 issues, 600 threads, 80 files, no extras) and `company-a` (12,000 pages, 3,000 issues, 6,000 threads, 1,500 files, 45 extra spaces, 26 extra projects, 186 extra channels).
- Text pools: `content.py` (sentence and noun pools for filler).
- Builders: `build_directory` (363), `build_confluence` (502), `build_jira` (596), `build_slack` (726), `build_drive` (824), `build_planted` (915, the planted facts), `build_fintech` (1414, runs after filler so it only adds), `build_questions` (1738), `build_fintech_questions` (1843), `build_audit_replay` (1903), `build()` (1928), `main()` (2071).
- Access rules are in the generator's `Acl` class (see the fixture README). Tests check them against an independent evaluator.

**How to add more documents:**
1. Add a planted fact in `build_planted` (915) or a filler rule in the matching builder (502, 596, 726, 824), with a sentinel sentence, and an entry in `facts` with `permitted`, `denied` and the scenario tag.
2. Add questions in `build_questions` (1738) or `build_fintech_questions` (1843). Expected answers come from the fact manifest, not written by hand.
3. To add a person, add them to `build_directory` (363) and to `FINTECH_USERS` if they belong to the fintech layer, and add a persona to `lib/personas.ts` in the web app (the persona list is fixed there).
4. Re-run the generator with the same seed and `--now`. Then run `.venv\Scripts\python.exe -m pytest fixtures\enterprise\tests -q`.
5. Rebuild the Walnut graph (`scripts/build_enterprise_graph.py`), and re-run the eval. The world SHA-256 changes, so every number in `RESULTS.md` changes.
- Changing the world also breaks the hard-coded suggested questions in `lib/personas.ts` and the tour's anchor text, which are not generated.

---

## 5. Eval results: `databridge/evals/internal_brain/RESULTS.md`

Generated 2026-10-14 by `python -m evals.internal_brain.run` (from `databridge/`). Harness files: `run.py`, `probes.py`, `capture.py`, `oracle.py`, `report.py`, `results.json` (machine-readable). **Generated before the 2026-10-30 edits to `pipeline.py` and `models.py`; re-run before quoting.**

| Measure | Result |
|---|---|
| Leaks | **0** across **5,958** requests (2,979 per mode; both modes run for each probe) |
| 95% upper bound per request (fake-capture) | 0.1007% (rule of three) |
| Requests where the model received passages | 683; payload leak upper bound 0.4392% |
| Contract violations (ledger invariants) | 0 |
| P2 differential (restricted vs. not) | **400 compared, 0 mismatches** (control 0) |
| Injection hard failures | **0** across 976 injection-relevant requests (no signals) |
| Positive-probe released recall | **100%** in both modes (196 probes) |
| Cited recall (expected source cited) | **96.4%** fake-capture, **87.8%** extractive |
| Expected-refusal questions answered | 14 (7 per mode). Relevance gate, not a leak. |
| Canaries | leaky config: 18 of 20 probes flagged; deny-all: 0 utility (both valid) |
| Mean latency per request | 19-20 ms (local mock connectors; not representative of Gate 2 live) |
| Runtime | 61.9 s |

Caveats printed in the report: the "fake-capture" mode uses `FakeBackend` with a capturing responder (a test double, not hy3); hy3 is "PENDING". The eval's world is the demo scale. Per-persona probe counts are in the report's taxonomy table.

The Internal Brain test suite is claimed at **639 passing** (`INTERNAL_BRAIN.md`, "Proof"). Not re-run for this inventory.

---

## 6. How to run everything locally

Prerequisites: Python 3.13 and Node 20; Git for Windows (Git Bash) for the `.sh` launchers. Shared venv is at `databridge/.venv` (present).

| Port | What | Start |
|---|---|---|
| 8200 | mock platforms (Slack, Confluence, Jira, Drive APIs and admin) | `cd databridge && bash scripts/run_local_demo.sh` (starts both 8200 and 8100) |
| 8100 | Internal Brain API | same command. Ready when it prints `Uvicorn running on http://127.0.0.1:8100`. Indexes the world first (about 1 minute). |
| 3100 | Internal Brain web app | `cd databridge/apps/internal_brain_web && npm run dev` |
| 3101 | web app in mock mode, for tests | `npm run build:mock && npm run start:mock`, or `npm run e2e` (which starts it) |
| 3300 | Walnut original screens | `cd databridge && bash scripts/run_walnut_original.sh` (`status` and `stop` also work) |
| 8300 | Walnut gateway | started by the same script |
| 8000 | default gateway URL in `apps/frontend/lib/api.ts:9` (not used when the launcher is used) | none |

Settings from `scripts/run_local_demo.sh`: `IB_FORCE_OFFLINE=1` by default (answers are extractive), `IB_DEMO_LOGIN=1`, `IB_POLL_S=120`, `IB_VAR=var/ib-demo` (or the first argument). It sources `databridge/.env.ib.local` if present (git-ignored) for `TOKENHUB_API_KEY` and `IB_FORCE_OFFLINE=0`. Ctrl+C in that terminal stops both processes.

Other commands (from `INTERNAL_BRAIN.md` and `CLAUDE.md`, from `databridge/`; `PY` is `.venv\Scripts\python` on Windows):
- Tests: `PY -m pytest apps/internal_brain/tests -q`
- Web unit or typecheck: `npm run typecheck` (in `apps/internal_brain_web`)
- Web end-to-end: `npm run e2e` in `apps/internal_brain_web` (first time: `npx playwright install chromium`)
- Eval: `PY -m evals.internal_brain.run` (1 to 10 minutes; `--limit 60` for a quick check)
- Tamper demo: `PY scripts/tamper_demo.py`
- WorkBuddy connectors: `PY scripts/workbuddy_setup.py --user-config`
- Manual Brain start without the script (from `CLAUDE.md`): set `PYTHONPATH` to `apps/internal_brain;packages/core;packages/harness;packages/adapter;packages/retrieval`, set `IB_DEMO_LOGIN=1`, then `PY -m databridge_ib`.
- Mock platforms without the script: `PYTHONPATH=apps/mock_platforms` then `PY -m mock_platforms --port 8200`.
- Sign-in at http://localhost:3100 with any persona. No password; leave the passcode box empty.

Port 3101 is used only by the Playwright config (`playwright.config.ts:5`). Ports 8100, 8200 and 3100 are reserved for the Brain; the Walnut launcher refuses them.

---

## 7. Gaps and risks for a live demo

**Demo path and first impressions**
1. **Opening :3300 lands on an empty page.** `app/page.tsx` redirects to `/trace`, which is empty on Company A. The judges need to be told to open `/graph` or `/brain` (or `/demo`). `INTERNAL_BRAIN.md` says to open http://localhost:3300 and the README does not mention the redirect.
2. **Persona switch in Walnut does nothing.** The sign-in only labels the session. The graph is built for `jdoe` (`scripts/build_enterprise_graph.py --as`). Anyone who picks another persona will see the same graph.
3. **Live Audit tab starts empty.** The mock web app pre-fills 40 synthetic past requests (`lib/mock/fixtures.ts` `SEED_HISTORY`, line 279). The live Brain does not load the 40 `audit_replay` rows from `world.json`, so the live log only contains events from the current state directory. The walk-through step 5 works only after the judges have asked questions. A rehearsed state directory will also show earlier rehearsal events. (Not verified by running; from reading the code.)
4. **Audit "last 30 days" uses the real clock.** The clock is `datetime.now` (`pipeline.py:78`, `app.py:160`), while the world is dated to `--now 2026-10-13`. The latest Slack thread is around 2026-10-12. On 2026-10-30, a "last week" or "last 30 days" audit filter is a hard filter (`timewin.py:3-6`) and covers the last 30 real days, not the world's dates. In Ask, "last week" is only a ranking preference, so answers still come back.
5. **Refusal questions can get a loose answer.** RESULTS.md reports 14 cases where an expected refusal was answered from a related permitted page (for example "When is the next settlement key rotation?"). The relevance gate is lexical (`INTERNAL_BRAIN.md`, "Honest limits"). A judge who asks an off-script refusal question may get an irrelevant but permitted answer.

**Latency and reliability**
6. **The model has never run.** hy3 is pending a TokenHub key (`RESULTS.md`, `INTERNAL_BRAIN.md`). If a judge sets `IB_FORCE_OFFLINE=0`, latency and failure modes are unknown. The fallback is designed to work, but has not been timed.
7. **Start-up takes about a minute** because `build_app` syncs every platform before uvicorn starts (`bootstrap.py:126-127`, `146`). The port does not open until then, so a demo started late will show "temporarily unavailable" until it is ready.
8. **The map is slow by design.** Each item is checked live before listing (`INTERNAL_BRAIN.md`: "a big map takes a few seconds"). `api.map()` is deduplicated in the browser (`lib/api.ts:154-157`).
9. **Gate 2 budget.** One batch gets 2 s in total (`pipeline.py:80`, `gates.py:44`), and linked objects get a second 2 s check (`pipeline.py:143`). Slow platforms silently drop documents (fail closed, so the answer is smaller, not wrong).
10. **Compare needs the Web Locks API.** Over plain http on a non-localhost address (for example a phone on the LAN), the Compare page refuses to run (`lib/api.ts:190-199`).
11. **The tour waits on anchors.** Each step waits for its `data-tour` element (`components/tour/Tour.tsx:16`). A slow ask or map holds the tour on its "waiting" text.
12. **Unexpected model use.** `run_local_demo.sh` defaults to `IB_FORCE_OFFLINE=1`, but `python -m databridge_ib` does not set it. A shell with `TOKENHUB_API_KEY`, or with both `OPENAI_API_KEY` and `OPENAI_API_BASE`, will call a model when started directly (`llm.py:34-40`, `78`).
13. **Ranking is lexical.** The offline hashing embedder (`index.py:159-161`) is used; the model embedding alias is configured (`llm.py:56`) but not used (`bootstrap.py:63`). The vector leg is weighted almost to zero (`index.py:43`).
14. **Web mock state lives in the Node process** (`lib/mock/server.ts:34-36`) and resets on restart. Playwright is serial for this reason.

**Hard-coded values and dev secrets**
15. **Personas and suggested questions are fixed in the web code** (`lib/personas.ts:12-121`, `124-130`). Mock names differ from them (`lib/mock/fixtures.ts:10-19`). Changing the world breaks the suggestions silently.
16. **Dev secrets in the repo:** `config.py:23-24` (token and ledger key), `config.py:39-41` (service, admin and webhook tokens), `run_walnut_original.sh` (`dev-local-only`, baked into the Walnut web bundle). A deployed build refuses the Brain defaults (`config.py:48-66`). Walnut's gateway accepts `dev-local-only` whenever `WALNUT_DEV_AUTH=1`.
17. **Demo sign-in has no password.** `IB_DEMO_LOGIN=1` lets anyone sign in as any persona, including `admin.it` and `maya.compliance`, unless `IB_DEMO_PASSCODE` is set (`app.py:98-100`). The web app's passcode box is empty by default.
18. **Fixed world constants:** `TOP_K = 12`, `MAX_PASSAGES = 14`, `GATE2_TIMEOUT_S = 2.0`, `POLICY_VERSION = "ib-policy-1"`, and the SLO of 300 s or 900 s depending on `IB_LIVE` (`config.py:16-21`).
19. **Audit anchor defaults to a local file** (`config.py` and `bootstrap.py:90`, `var/ib/external_anchor/heads.jsonl`). `INTERNAL_BRAIN.md` states this is not off-host, but the Audit page's "Chain intact" can still look authoritative in a demo.

**Scope and claims**
20. **Platforms are mocks.** The connectors are real code on the real path, but no real tenant has been used (`INTERNAL_BRAIN.md`, "Honest limits").
21. **Walnut's retired pages stay routed** (`/ask`, `/schema`). They show a notice, not a 404. A judge typing them from the old docs will see the notice.
22. **Eval numbers predate the code change** (section 5). Re-run before quoting. The count of indexed documents (1,140 in the eval) does not match the raw count (1,143) and is not explained.
23. **The Internal Brain test count (639)** and the Playwright count (38) come from the docs. The Playwright count matches the expanded test list in section 1.10. The 639 was not checked here.

---

## 8. What I did not verify

- I read the code and did not run any of the apps, tests or evals. Behaviour in sections 6 and 7 comes from the source and the docs.
- I did not open `E:\Internship` (the user's private rule). Nothing in this inventory depends on it.
- I did not check the Walnut gateway's full route list (about 200 decorators across `apps/gateway/databridge_gateway/`); only the routes the Walnut screens call are listed.
- Line numbers in `lib/api.ts`, `lib/types.ts`, `lib/personas.ts`, `lib/tour.ts`, `tailwind.config.ts`, `app/globals.css` and the Brain modules are exact as read on 2026-10-30. Components with approximate line references are marked "about".
