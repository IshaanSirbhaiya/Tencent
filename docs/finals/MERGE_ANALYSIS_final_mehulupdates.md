# Merge analysis: `final_mehulupdates` into `final_v1.4`

Written Fri 30 Oct 2026. Status: **WIP, not verified, blocked** (see §5).

| Item | Value |
|---|---|
| Source branch | `origin/final_mehulupdates`, one commit `980b17b` (author: the teammate, 30 Oct 18:31 +0800) |
| Merge base | `1076517` (docs: verified test counts everywhere ...) |
| Target | `final_v1.4` (pushed as `final_v1.4-wip`) |
| Merge commit | `cddd878` "merge origin/final_mehulupdates (980b17b) into final_v1.4: WIP, NOT VERIFIED" |
| Plan it deviates from | `docs/finals/MERGE_PLAN.md` put the just-in-time UI on `final_v1.5`, after voice on `final_v1.4`. Voice has not arrived, so the JIT branch went first, onto `final_v1.4`. |

Line numbers below are in the merge commit `cddd878` unless marked "working tree". Paths under
`databridge_ib/` are in `databridge/apps/internal_brain/`; web paths are in `databridge/apps/internal_brain_web/`.

---

## 1. What the branch adds

Source: `git show --stat 980b17b` and its commit message.

1. **Pages beside the chat.** After each answer the IB decides "page or chat" (`POST /ib/views` with
   `mode: "decide"`). A page opens in a canvas on the right of the Ask page (`components/user/ViewCanvas.tsx`);
   below 1024 px it opens as a full-screen sheet. Any answer can also be opened with **Open as page**
   (`components/user/AnswerCard.tsx`, `PageButton`, `data-testid="view-open-page"`).
2. **The page backend**, new package `databridge_ib/views/` (11 files): `plan.py` (rules planner, trigger
   words), `composer.py` (model-composed page, spend cap, rate limits), `pagespec.py`, `pagecheck.py` (every
   value checked against its cited source; drops are counted), `blocks.py`, `a2ui.py` (A2UI v0.9 messages),
   `service.py`, `routes.py`, `prompts.py`, `spec.py`.
3. **Extra documents for a page.** Up to `MAX_EXTRA = 12` more of the asker's own documents
   (`views/service.py:61`), each through Gate 1 and the live Gate 2 with read-through (`views/service.py:242-263`).
4. **Change this page** in plain words, in the canvas box or as a short chat request ("make the table black"):
   the page is rebuilt (`app/(user)/page.tsx`, `isPageEdit`; `ViewCanvas.tsx`, `data-testid="view-change"`).
5. **Model-written chat answers** through the existing grounding wall when Hy3 is not live
   (`databridge_ib/claude.py`, a harness backend), and **follow-up rewriting** (`/ib/rewrite`; the chat shows
   "Searched for").
6. **Bounds on spend:** a persisted US$10 cap shared by all third-party model calls, per-person and global rate
   limits, in-flight slots (`views/composer.py:62-131`, `:199`, `:231-240`).
7. **Ask view redesign:** new light tokens (`app/globals.css`, +181 lines), new composer, icon-only feedback, one
   row of action pills (Act, Deliverables, Open as page), sidebar that collapses to an icon rail while a page is
   open (`lib/navRail.ts`).
8. **Renderer:** `components/views/ViewSurface.tsx`, `catalog.tsx` (686 lines, the Walnut A2UI catalog),
   `numbering.ts`; strings in `lib/i18n/view.ts` (English and 简体中文); mock in `lib/mock/extra/view.ts`.
9. **Tests (as reported by the teammate, not re-run here):** IB suite 1,179 passed (`test_views.py` 25,
   `test_views_composer.py` 17, fake model client); web `views.spec.ts` 7 passed (mock); full web e2e 173 passed
   "before the answer change".
10. **Docs:** `FEATURE_CATALOGUE.md` section, `DECISIONS.md` 4 entries, `CHANGELOG.md` row, 3 screenshots.

## 2. Precheck result

Source: `databridge/scripts/merge_precheck.py` categories (`MERGE_PLAN.md` §1, rows 1 to 5) and a diff read.

| # | Check | Result |
|---|---|---|
| 1 | Secrets and secret-shaped files | 0. The model key is read only from the environment (`ANTHROPIC_API_KEY`); the commit message says it lives in the git-ignored `.env.ib.local`. |
| 2 | Real company or person names | 0 |
| 3 | `databridge/packages/**` | 0 files touched |
| 4 | New environment flags | `IB_CLAUDE`, `IB_CLAUDE_BUDGET_USD` (default 10), `IB_CLAUDE_ANSWER_EFFORT`, `IB_CLAUDE_FAST`, `IB_CLAUDE_EFFORT` (`claude.py:95-103`, `views/composer.py:189-206`); `scripts/demo_up.sh` prints the page composer state |
| 5 | New Python dependencies | `anthropic==1.15.0`, `docstring-parser==0.18.0` (`databridge/requirements-lock.txt`) |
| 6 | New web dependencies | `@a2ui/react` 0.13.0, `@a2ui/web_core` 0.13.0, `@a2ui/markdown-it` 0.2.1, `zod` 3.25.76 (`package.json`, exact pins) |

## 3. The 5 text conflicts and how each was resolved

| # | File | Conflict | Resolution |
|---|---|---|---|
| 1 | `components/user/ActSheet.tsx` | Both sides edited the sheet (his: 4 lines for the new Ask view; ours: Act fixes) | Kept both sides |
| 2 | `lib/mock/extra/index.ts` | Both added a handler to the mock list (his `view`, ours `uploads`) | One list: `[user, act, view, deliver, uploads, ...]` |
| 3 | `docs/finals/DECISIONS.md` | His D-34..D-37 collided with ours (D-34..D-36 already used, D-36 = separate tours) | His entries renumbered **D-37..D-40**; text unchanged |
| 4 | `docs/finals/FEATURE_CATALOGUE.md` | His new §13 collided with our §13 | His section renumbered **§14** |
| 5 | `docs/finals/CHANGELOG.md` | Both added a row at the top | Ours kept, his row added |

## 4. Open question for the founder (P2): the vendor SDK outside `packages/harness`

1. The branch imports the third-party SDK inside the app: `databridge_ib/claude.py:47` and
   `databridge_ib/views/composer.py:212` (`import anthropic`, lazily, inside `_api()`).
2. `databridge/README.md:75`, invariant 1: "No vendor SDK imports (`import anthropic`, `import openai`) outside
   `packages/harness`."
3. `docs/finals/MERGE_PLAN.md:110`: `databridge/packages/**` is "reused by import, never modified"; `:185`
   says a provider is called "over HTTP from app code, as `llm.py` does through the harness".
4. The two rules cannot both hold for this code: moving the import into `packages/harness` would touch
   `packages/**`; keeping it in the app breaks the README invariant.
5. **Recorded as an exception pending the founder.** Options: (a) accept the exception and note it in
   `DECISIONS.md`; (b) replace the SDK with plain HTTP calls in app code, as `llm.py` does; (c) remove the page
   composer and keep the rules planner only. Nothing was changed for this point in the merge.

## 5. Blocker: `databridge_ib/onboarding.py` is missing

1. `views/service.py:52` imports `from ..onboarding import in_project, project_topic`; the module is not in
   `980b17b`. Without it `databridge_ib.views` cannot import, so the IB does not start with the branch.
2. For local work only, a stub exists in the working tree (`onboarding.py` line 1: "LOCAL STUB (never
   committed)"), excluded through `.git/info/exclude`. It is not in any commit and must not be pushed.
3. Needed from the teammate: the real `onboarding.py` (and its tests, if any). Until then the merge stays WIP
   and no test count from this branch is quoted.

## 6. The 4-seat review: findings and dispositions

Four reviewers read the merged tree on 30 Oct. "In progress" = being fixed by the other agent in the working tree
at the time of writing (uncommitted); it is not verified until the suites run.

| # | Finding | Where | Disposition |
|---|---|---|---|
| 1 | The third-party model is on whenever a key is set (`IB_CLAUDE` defaults to "1") and is not stopped by the forced-offline switch | `claude.py:95`, `views/composer.py:206`, `scripts/demo_up.sh` | In progress: opt-in only (`IB_CLAUDE=1` plus a key) and never when the IB is forced offline (working tree `views/composer.py:50-51`, `claude.py:8`) |
| 2 | User-facing labels can name the provider | web strings and status labels | In progress: neutral wording ("an AI model") |
| 3 | PreflightBoard says "Model now: Tencent Hunyuan live" whenever `models.active` is true, which includes the stand-in model | `components/preflight/PreflightBoard.tsx:128-132` | In progress: a label that never claims Hy3 for another model |
| 4 | Extra documents read for a page and `/ib/rewrite` calls have no write-ahead record | `views/service.py:242-263`, `views/routes.py` | In progress: write-ahead records (no request text) |
| 5 | Page builds are recorded as `deliverable.*` events with `deliverable="view"`, so they mix with the Deliverables rows in the stats | analytics stats | In progress: views counted in their own rows |
| 6 | Label grounding hole in the page check (a label is not held to the same standard as the value next to it) | `views/pagecheck.py` | In progress |
| 7 | Spend cap does not fail closed in every error path, and an in-flight slot can leak | `views/composer.py:93-100` (`reserve`), `:231-283` (`_call`) | In progress: fail closed; the slot always released |
| 8 | The views status is cached once per page load (`statusOnce`), not per signed-in session, so a persona switch in the same tab keeps the previous state | `components/user/ViewRow.tsx:46-58` | In progress: a per-session cache |
| 9 | e2e specs likely broken by the Ask redesign: `act.spec.ts` and `deliverables.spec.ts` (row visibility now under the action pills), `a11y` at 390 px | `tests/e2e/` | **Open.** Not run (shared CPU). Run after the fixes land |
| 10 | Tour: the user tour does not show the new page feature | `components/tour/steps.ts` | Done in this pass: two steps "Open it as a page" and "Change it in plain words" (User tour only; the Developer tour unchanged); `TOUR_LENGTH.user` 4 → 6. `tests/e2e/tour.spec.ts` updated and a new test added, **not run**. `tests/e2e/tour-screens.spec.ts:45,60` still expect "of 4" and need the same update |

## 7. Hard-rule checklist (`CLAUDE.md` rules and the founder rules)

| # | Rule | State in this merge | Evidence |
|---|---|---|---|
| 1 | Two gates before any model; fail closed | Holds for pages: extra documents only from the permitted set, each through Gate 1 and live Gate 2 | `views/service.py:242-263`; `test_views_composer.py::test_restricted_documents_never_reach_claude_or_the_page` |
| 2 | Restricted looks identical to non-existent (one 404) | Holds in the tests | `test_views.py::test_someone_elses_unknown_and_refused_are_one_404`, `::test_restricted_question_equals_a_world_without_it` |
| 3 | Write-ahead ledger; no ledger, no answer | Holds for page builds; gaps for extra documents and `/ib/rewrite` (§6 row 4, in progress) | `test_views.py::test_write_ahead_order_and_no_text_in_ledger`, `::test_ledger_outage_is_503_and_nothing_is_shown`; `test_views_composer.py::test_ledger_down_before_claude_means_no_call` |
| 4 | Every value grounded in a cited source | Holds for values; label hole in progress (§6 row 6) | `views/pagecheck.py` docstring; `test_views.py::test_model_invents_values_names_and_headings` |
| 5 | `packages/**` untouched | Holds (0 files) | §2 row 3 |
| 6 | No vendor SDK outside `packages/harness` | **Exception pending the founder** | §4 |
| 7 | No secrets in the repo | Holds | §2 row 1 |
| 8 | User-facing text never names the model provider; the stand-in model is off by default | In progress (§6 rows 1 to 3) | working tree `views/composer.py:50-51` |
| 9 | Never claim Hy3 answered; never claim more than the tests prove | No test count from this branch is quoted as ours until the suites run on the merged tree | §1 item 9; §5 |
| 10 | User and Developer tours separate (D-36) | Holds: the new steps are in the User tour only and link to nothing in Developer mode | `components/tour/steps.ts` |

## 8. Next steps

1. Teammate: push `databridge_ib/onboarding.py` (§5).
2. Founder: decide §4 (a, b or c).
3. Land the §6 fixes, then run `pytest` for `databridge/apps/internal_brain`, the web e2e suites
   (including `tour.spec.ts`, `tour-screens.spec.ts`, `views.spec.ts`, `act.spec.ts`, `deliverables.spec.ts`,
   `a11y.spec.ts`) and the leak eval, and record the counts in `docs/finals/TEST_REPORT.md`.
4. Only then mark `final_v1.4` verified and use QA_BANK Q49 to Q51 and video slot S2.
