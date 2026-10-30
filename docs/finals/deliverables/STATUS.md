# Deliverables: build status (checkpoint, 30 Oct 2026)

Written at the coordinator's checkpoint call (laptop going offline). Branch `final_v1.1`, nothing
committed by this agent. Spec: `PLAN.md`; reviews applied: `review_codex.md` (highs + mediums),
`review_fable.md` (top 5 + §6), `review_opus.md` (findings 1-5 mandatory + mediums, T14-T16).

## 1. Done (P0)

### Backend (`databridge/apps/internal_brain/databridge_ib/deliverables/`, all new)
| File | What |
|---|---|
| `evidence.py` | Source-only evidence from `ActionService.context().cites` snapshots: lines filtered like `extractive_answer` (metadata, URL-only, AI-addressed dropped; inline URLs removed; tags neutralised), CSV blocks parsed with `csv.reader` (rectangular, header + >= 2 rows), prose split into sentences, 1,200 chars per doc (`docview.EXCERPT_CHARS`), 12,000 total. The answer's sentences go to the model as an untagged orientation block and are NEVER evidence (Opus #2, Codex #1/#2). |
| `verify.py` | Number check by SPAN (raw's number must sit exactly on a source number span, sign included, word boundaries); scale words/letters (`20k`, `1.8 million`, `bps`, `万`), ambiguous groupings (`IDR 20.000`, `0,35`, `20 000`), accounting `(500)` rejected; label bound to the same CSV row, or in prose every label token BEFORE the number with no other number between; >= 1 real word; unit + currency worked out by our code, one per series and per chart. Sentences: own tags only, unit-aware numbers, capitalised terms (sentence-initial too) as whole words, CJK runs verbatim, no URL/e-mail/phone/injection/identity assertion, filler openers and > 30 words dropped. Short fields (titles, series, columns, axis labels): every token in the cited units, else fixed text. |
| `spec.py` | Model output schemas (Pydantic, `extra="forbid"`, strict finite numbers, rectangular tables). Kinds: table, chart (bar/line), analysis. |
| `prompts.py` | System preamble + per-kind task (`PROMPT_VERSION ib-deliver-2026-10-31.1`, task `ib_deliver`). |
| `model.py` | `structured(chain, ...)`: global cap of 2 in-flight model calls (non-blocking, else `busy`), own pool, ONE 30 s deadline over the whole chain (no model started after it; late results discarded), `IB_JSON_MODE=0` whole-object parse with optional ```json fence. |
| `build.py` | Deterministic `source_table` (source-table-v1), `quoted_table` (quoted-table-v1, source sentences only), `source_chart` (source-chart-v1, "FX margin by market" from df-3), model-output checks, title rules (sentence case, <= 60, banned words), `recheck()` used after a model call and on export. |
| `export.py` | CSV only (UTF-8 BOM, CRLF), formula guard on every cell incl. Sources block (`= + - @` tab CR LF, full-width, after Unicode spaces; plain numbers like `-0.5%` exempt), `http(s)` links only, file name from fixed parts (`walnut-<kind>-<hex8|00000000>-<yyyymmdd>.csv`). |
| `service.py` | Order: rate limit (10/min create, 20/min export; ledgered `failed` stage `rate`, no ids) → `projection.owns` (no source reads; not mine = `failed` lookup with NO ids, 404) → LEDGER `deliverable.requested` → `context()` (live re-check) → build → re-authorise after a model call (revoked or version-moved docs removed, content re-checked) → canonical digest → LEDGER `deliverable.created` → only then published to the store (90 min, 300). Export: owner + TTL lookup first (one 404), then format (422), re-authorise + re-check against current sources, build from a COPY of the sealed spec, LEDGER `deliverable.exported`, bytes. |
| `routes.py` | `POST /ib/deliverables`, `POST /ib/deliverables/export`; always mounted; 401/403 then kill switch (off ⇒ same 404 body, nothing recorded). Export headers: exact `content-disposition`, `nosniff`, `no-store`. |

Shared-file edits (minimal, re-read first): `models.py` (4 event models + `EVENT_KINDS`),
`audit/ledger.py` (parse map), `audit/stats.py` (`deliverables_*` totals), `config.py`
(`IbConfig.deliverables` from `IB_DELIVERABLES`), `app.py` (mount + `/ib/status.deliverables`),
`llm.py` (one routing entry `ib_deliver`). `actions.py` NOT edited.
Kill switch ON in `scripts/demo_up.sh` and `scripts/run_local_demo.sh` (the `.cmd` launchers call
`demo_day.ps1` → `demo_up.sh`, so they inherit it). Turning it off needs an IB restart.

### Web (`databridge/apps/internal_brain_web/`)
- New: `components/user/DeliverSheet.tsx` (DeliverRow "Turn this answer into", wide sheet `width="xl"`,
  tabs, one metadata line, neutral pill / amber only on removal, no pill on deterministic builds,
  chart with raw value labels + chip beside each x label + figcaption data table, Recharts stripped,
  states loading/needs model/insufficient/nothing/error/busy, Copy, Download .csv, Regenerate),
  `lib/i18n/deliver.ts` (EN + 简体中文), `lib/mock/extra/deliver.ts` (mock), 
  `components/presenter/deliver.ts` + `DeliverOverlay.tsx` (P warm-up builds Leo's deliverables,
  cache 90 min in sessionStorage, R clears + rebuilds, D opens the cached panel).
- Edited (minimal): `AnswerCard.tsx` (one import + one line), `components/user/index.ts` (export),
  `components/ui/dialog.tsx` (`width="xl"`), `lib/types.ts` + `lib/api.ts` (appended blocks),
  `app/api/ib/[...path]/route.ts` (allowlist `ib/deliverables(/export)?`, main session only;
  `content-disposition` relayed ONLY for `ib/deliverables/export`, status 200, exact regex; plus
  `nosniff`), `components/journey/LedgerChain.tsx` (4 labels), `components/presenter/controller.ts`
  (D key, warm-up on P and R), `PresenterRoot.tsx` (mount overlay), `lib/mock/extra/index.ts`.

## 2. Tested

| Suite | Result |
|---|---|
| `tests/test_deliverables_verify.py` (69 tests: T14 adversarial list, label binding, units, schemas, CSV guard incl. Sources block, file names, JSON-off parsing) | **69 passed** |
| `tests/test_deliverables.py` (21 tests: T1-T13 + label/unit attacks from the model, revoked source + export recheck, source edit between create and export, rate limit, kill switch, budget/busy) | **21 passed** |
| `tests/test_deliverables_eval.py` (T16: eval detectors through deliverables + CSV with an adversarial echo model, small world, real connectors) | **1 passed** |
| `tests/test_act.py` after the shared edits | **19 passed** |
| Full IB suite | Run started; it had reached >90% with 2 `F` marks early (around 20-27%) when the checkpoint came; the run did not finish in time. **Not verified green. Next step 1.** |
| `npx tsc --noEmit` | **clean** |
| `tests/e2e/deliverables.spec.ts` (mock, headless, port 3128, `deliverables.config.ts`, axe in dark + light) | **4 passed**, 0 axe serious/critical |
| Live API check (scratch IB :8128 with `IB_DELIVERABLES=1`, same mock platforms :8200, `IB_FORCE_OFFLINE=1`) as `leo.ops`, "Can we offer multi-currency accounts to an Indonesian SME?" | cites `drive:df-3` as C2; Table = source-table-v1; Chart = source-chart-v1 "FX margin by market" SG 0.35 / HK 0.35 / ID 0.4; Analysis = `needs_model` (no model); both CSV exports 200 with `walnut-<kind>-217ff91e-20261030.csv`; receipt shows `deliverable.requested/created/exported` |
| Live UI (`deliverables.live.spec.ts`, DELIV_LIVE=1) | **Not completed**: the dev server on 3128 hit `EADDRINUSE` (the stopped mock dev server's node child still held the port), so the run hit the login page timeout. |

The running stack IB on :8100 has this code but `IB_DELIVERABLES` unset (status shows
`deliverables.enabled: false`): it needs the stack restarted via `demo_up.sh` to turn the switch on.

## 3. Left (exact next steps)

1. Re-run the full IB suite from `databridge/`:
   `PYTHONPATH="apps/internal_brain;packages/core;packages/harness;packages/adapter;packages/retrieval" .venv/Scripts/python -m pytest apps/internal_brain/tests -q -p no:cacheprovider`
   and check the two early failures against the baseline (could be pre-existing or from the other
   agents' in-flight edits; `test_eval_summary.py`, live-growth and upload tests are in flux).
2. Free port 3128 (kill the leftover node `next dev` listener), start
   `NEXT_DIST_DIR=.next-deliv WALNUT_PROXY=1 IB_API_URL=http://127.0.0.1:8100 npx next dev -H 127.0.0.1 -p 3128`
   once the stack IB runs with `IB_DELIVERABLES=1` (or use a scratch IB on another port as in §2),
   then `DELIV_LIVE=1 npx playwright test -c tests/e2e/deliverables.config.ts deliverables.live`
   → screenshots `docs/finals/screens/deliverables/live-*.png` (mock screenshots `mock-1..4` exist).
3. Docs (not written yet): `docs/tencent/SECURITY.md` §15 new subsection (deliverables: rules,
   limits from verify.py docstring, the 1,200-char re-read sent to TokenHub, restart-to-disable,
   tests T1-T16); `docs/finals/contracts/backend_v1.md` "v4: deliverables" (routes, bodies, statuses
   `ready|needs_model|insufficient`, ledger kinds, digest = sha256 of canonical JSON of
   `DIGEST_FIELDS` in `service.py`); `FEATURE_CATALOGUE.md`; `AI_USAGE_MAP.md`; DECISIONS D-25
   (kill switch, not demo-only; P0 scope = table/chart/analysis, CSV only); `QA_BANK.md` Q10
   rewritten to end "Dev, D." (Fable §5 scripts, with the [DELIVER] truth tag).
4. Rehearse D then R once on the stage laptop (Fable §4); confirm D's overlay with Hy3 live.
5. P1 (only after 1-4): Summary, Slides (.pptx native charts, notes, guard every chart string for
   the embedded workbook = Opus #4 / T15, 2 MB cap, add python-pptx + XlsxWriter to BOTH
   `requirements-lock.txt` and `deploy/lighthouse/requirements-ib.txt`). Email stays Act's draft.

## 4. Gotchas

- `next dev` with a new `NEXT_DIST_DIR` rewrites `tsconfig.json` (adds `.next-deliv/types/**/*.ts`
  and reformats the file); other agents' dist dirs are in there too. Harmless, but it is a shared file.
- Stopping the background `next dev` wrapper does not always stop its node child on Windows: check
  `netstat -ano | grep :3128` before restarting.
- Mock mode bypasses the proxy's header relay; the content-disposition filter is only exercised by
  the live spec (`deliverables.live.spec.ts`) and the proxy code.
- Prose label binding fails closed for "0.4% for ID" (label after the number): by design.
- The P warm-up switches the MAIN session to Leo and back (serialised with the presenter's own
  sign-ins). If nobody was signed in it signs out again at the end.
- Store is in memory: after an IB restart, exports of old deliverables are the one 404; Regenerate.
