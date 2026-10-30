# Watch the brain grow: status (30 Oct 2026, checkpoint before the move to the cloud session)

Branch `final_v1.1`, uncommitted (the coordinator commits). Plan: `PLAN.md` in this folder. Decision: D-24.
Security: `docs/tencent/SECURITY.md` §15.13. Contracts: `docs/finals/contracts/backend_v1.md`
"Demo-proof features (v4)". Catalogue: `docs/finals/FEATURE_CATALOGUE.md` §11.

## 1. Done

**Toggle (founder requirement).** `IB_DEMO_FEATURES=1` (config field `demo_features`, property
`demo_features_on` = flag and not `IB_DEPLOYED`). Off, these routes are NOT mounted (unknown-route 404):
`GET /ib/graph/changes`, `POST /sim/add-document`, `/sim/check-absent`, `/sim/engine-refresh`,
`/sim/engine-status`. `/ib/status` reports `demo_features`. Only `databridge/scripts/demo_up.sh` sets it
(plus `IB_ENGINE_URL`, `IB_ENGINE_TOKEN=dev-local-only`). Web shows the controls only when the server says
so AND Settings → "Demo features" is on (localStorage `ib-demo-features=1`) or the Presenter is armed;
each has a "Demo" badge.

**Backend** (`databridge/apps/internal_brain/databridge_ib/`):
- `live.py`: reviewed; L-1 (cursor `t` = mark at reply, newest-first 200 checks, cap 25 after the live
  check), L-2 (tracker is the clock; analysed: mark-before-window plus stamps ≥ last mark gives no gap),
  L-4 (`mode="changes"`), L-6 (one deadline) in place. Mounted only when the toggle is on (`bootstrap.py`).
- `models.py`: `GraphViewed.mode` gains `"changes"` (one-word edit).
- `sim.py`: add-document and check-absent mounted only when on; add-document now needs `IB_DEMO_LOGIN`
  for everyone (admin too); engine refresh routes added; `on_reset` hook rebaselines the tracker (L-3).
- `engine_refresh.py` (new): single-flight background job, 1 start / 2 min, jdoe or admin_it, build
  script `--as jdoe` into a side file, SQLite online-backup publish into the open `var/graph/enterprise.db`,
  then server-side `POST /graph/reload` to the gateway; write-ahead `ingest.sync` record.
- `config.py`: `demo_features`, `engine_graph_db`, `engine_url`, `engine_token`.
- `app.py`: `/ib/status` `demo_features`; NEW `iter_routes()` used by `mounted_paths()`. FastAPI 0.143
  keeps included routers as lazy `_IncludedRouter` entries, so the old allowlist check never saw `/sim/*`
  or `/audit/*` (pre-existing gap; all were on allowed prefixes). `tests/test_app_routes.py` still iterates
  `app.routes` and so still skips included routers (not changed; my L-5 test uses `iter_routes`).
- The upload line in `bootstrap.py` (`mount_uploads_router(app, svc, reset_hooks)`) is kept (upload agent).

**Web** (`databridge/apps/internal_brain_web/`): `components/graph/useLiveChanges.ts`, `LiveFeed.tsx`
(new); `model.ts` (`fresh` attr, `mergeDelta`); `layout-core.ts` (`docSlot`, `discRadius`, `placeAround`;
layout output proven identical); `GraphCanvas.tsx` (pulse, decay, reduced motion, `animate`, `freezeFrame`
via `setCustomBBox`); `GraphExplorer.tsx` (feed, Add button, sheet, `?add=1`, BroadcastChannel reset,
presenter handler `graph.addNote`); `components/sim/demo.ts`, `DemoFeatures.tsx`, `AddDocumentSheet.tsx`
(Slack note tab + Upload a file tab calling `POST /sim/upload-file`, sample files from
`public/finals/live_kit/files/`); `components/engine/EngineRefreshButton.tsx` (+ one line in
`EngineView.tsx`); Settings card (one line in `app/(user)/settings/page.tsx`); presenter steps `grow`,
`grow-post`, `grow-ask` (`script.ts`, spliced in only while demo features are on via `PresenterRoot.tsx`;
strings in `lib/i18n/presenter.ts`); strings in `lib/i18n/graph.ts`; `// ---- live-growth ----` blocks in
`lib/api.ts` (`liveApi`) and `lib/types.ts`; mock handlers in `lib/mock/extra/graph.ts` (status flag,
changes queue, add-document, engine refresh).

**Docs:** SECURITY §15.13, backend_v1 v4 section, DECISIONS D-24, FEATURE_CATALOGUE §11.

## 2. Tested

- `tests/test_live_growth.py`: 11/11 pass (L1–L3 byte-identical Ken + zero source calls, L4, L5/L6 cursor,
  L7, L8, L9, A6 masking, A7 answerable by jdoe and Priya / constant refusal for Ken, A8 reset +
  rebaseline, A9 ledger down, mode "changes"). Uses the agreed sample "shard3-resume".
- `tests/test_sim_add_document.py`: 29/29 runs pass (incl. A1 byte-identical refusals, A2 deployed, A3/A4
  validation, A5 rate limit, live membership, check-absent x3).
- `tests/test_demo_features.py` (new): 11/11 pass (toggle-off unknown-route 404 x5 routes, status flag,
  deployed never on, L-5 allowlist + auth with the bootstrap app, engine refresh: publish into an OPEN db,
  429 after 1 start / 2 min, write-ahead ledger, 503 when ledger down, jdoe/admin only, failed build
  leaves the snapshot, single flight).
- Full IB suite: **1036 passed, 2 failed** (22 min). The 2 failures are NOT this workstream:
  `test_connector_drive.py::test_markdown_and_csv` and `::test_crawl_full_then_quiet` (Drive connector
  returns None for the CSV/Markdown fixture `df-7`; `connectors/drive.py` belongs to the upload agent).
- `npx tsc --noEmit`: clean.
- `tests/e2e/grow.spec.ts` with `tests/e2e/grow.config.ts` (mock, port 3127, dist `.next-grow`,
  `WALNUT_PROXY=1` needed for /dev/brain): 6/6 pass (3 layout unit tests, hidden by default with zero
  polls, post → feed → Show → pause stops polling + axe 0 serious, Compare only the poster receives it +
  upload tab + engine refresh).
- **Live check** on the restarted shared stack (dev web on :3127 live mode → IB :8100): status
  `demo_features: true`; check-absent "resumes Thursday 5 Nov 2026" = 0; jdoe posted the note on
  `/dev/graph?add=1` (default channel C0DBMIG); "Just added" showed it 3.6 s after the click ("appeared
  2.8 s after you posted"); Ken's open graph: 0 items, "Nothing new yet"; jdoe's answer cites the new
  thread `[C1]`; Ken's reply is the constant refusal, identical to before the note; Ken `/ib/doc` 404;
  engine refresh 34 s, `reloaded: true`, second start 429, Ken 404; `/sim/reset` 200 (about 50 s), the
  note's `/ib/doc` 404 for jdoe, check-absent 0, index back to 1,204. Screens:
  `docs/finals/screens/grow/01..10-*.png`.

## 3. Left (exact next steps)

1. Upload agent: fix the 2 Drive connector failures; decide whether `/sim/upload-file` should also be
   UNMOUNTED when the toggle is off (today it is mounted and refuses with its own 404, so with no session
   it answers 401, not the unknown-route 404).
2. `tests/test_app_routes.py`: switch its two loops to `app.iter_routes(app)` (owner: whoever owns that
   file) so the auth check covers included routers.
3. `demo_up.sh stop`/`status` cannot see listeners on this laptop (netstat shows none); I stopped the
   stack by PID tree. Worth switching `port_pids` to PowerShell `Get-NetTCPConnection` or a health probe.
4. Demo machine: the stack came up with `IB_FORCE_OFFLINE=1` (hy3_check failed at 11:20 SGT), so answers
   were extractive. Re-check TokenHub before the rehearsal.
5. Optional (PLAN): add "Reset the world" to `REHEARSAL_CHECKLIST.md`; a PROPOSED line in
   `PRESENTER_KEYS.md` §6 for the grow steps; record clip D; set `IB_POLL_S` ≤ 10 s if the venue webhook
   is unreliable (left at 120 s).
6. Not done: a live (non-mock) Playwright spec for Compare polling; the PLAN's presenter warm-up warning
   for duplicate notes.

## 4. Gotchas

- Next dev on a new dist dir rewrites `tsconfig.json` `include` (`.next-grow/...`); I removed it again.
- Heredocs with apostrophes fail in this Bash tool; write files with the Write tool.
- `/compare?a=&b=&q=` does not auto-run; click "Ask both".
- `/sim/reset` takes about 50 s on the demo laptop (full reconciliation); give clients ≥ 120 s.
- The engine snapshot is built from `world.json`, so a live note is NOT in the engine view; the build
  rewrites `fixtures/enterprise/graph_rows/*.json`, verified byte-identical to the current rows.
- The shared stack (mock :8200, IB :8100, gateway :8300, Walnut web :3300, web :3100) was restarted with
  this code (not `--fresh`) and is left running. The note was reset away; the world is as generated.
