# Workflow test plan: Story 1, "The Call" (core journeys, real model answers)

**Status:** PLAN ONLY (30 Oct 2026, ~20:20 SGT). Nothing is built yet. Runs on the founder's laptop after the
stand-in model is switched on (key in `databridge/.env.ib.local`, never in git), on `final_v1.4`.
**Why:** prove that the story told on stage is true, end to end, with real model answers, not just the
extractive path. Each step below is a check with a pass rule. A failure blocks that beat from the video
and the stage until it is fixed.

## 0. Scope

- **In scope (the core):** the five beats of the story (Amy, Rachel, Ken, the regulator, the numbers),
  each through the real API and the real UI.
- **Out of scope:** every other journey (the full regression is in the cloud's end-to-end run), voice
  recognition quality (browser speech, not ours), and the live-growth and upload demos.
- **Personas** (from `lib/personas.ts`):
  - `amy.am`: account manager
  - `mlro.tan`: Rachel Tan, MLRO
  - `ken.ext`: outside contractor
  - `maya.compliance`: compliance officer

## 1. Facts the story relies on (check first, against `fixtures/enterprise/world.json`)

| # | Story fact | In the world? | Action |
|---|---|---|---|
| F1 | Case FC-2026-0147 | Yes (4 mentions) | none |
| F2 | 37 inbound transfers just under SGD 20,000 in nine days | Yes ("37 inbound transfers of just under SGD 20,000 each … within nine days") | none |
| F3 | 11 of the 37 accounts share one device fingerprint | Yes ("links 11 of the 37 remitting accounts to a single device fingerprint") | none |
| F4 | The director couldn't explain the source of funds on a due-diligence call | Yes ("could not explain the source of the third-party deposits on the enhanced due diligence call") | **Checked (cloud, 30 Oct):** the source is the #fincrime Slack message by mlro.tan in `fixtures/enterprise/world.json` `slack.threads[170]`, ts 1791256800 = **6 Oct 2026, 11:20 SGT**. The date is in the message timestamp (shown on the citation), not in its text, so the answer may not say "6 October" unless it quotes the source date. Pass rule: the cited source is that message and its date reads 6 Oct. The fact is in the world, but the date wasn't found next to it. If it isn't there, drop the date from the script |
| F5 | The STR is filed | Yes ("lodged with STRO under the synthetic reference STRO-SYN-2026-0912") | none |
| F6 | The approved customer script, "routine review in line with our regulatory obligations…" | Yes ("routine review", 2 mentions) | Check that the exact wording matches the world text. The script must quote the world, not the story |
| F7 | "Bayfront Spice Traders" does not exist | Yes, only in the demo questions | none |
| F8 | "About US$1.3M a year" | Projection: 600 × 90 min/wk × US$31/h × 46 wk ≈ **US$1.28M gross** (value_defaults.json) | Always say "**projected**", never "saved". The slide labels it "Projection, not a measurement" |
| F9 | "0 leaks in 7,374; 0 of 400" | Yes (eval_summary.json); measured **without** the model | After this test, re-run the eval WITH the model on, then say it |

## 2. The checks (in story order)

Each check runs through the API (scripted, repeatable) **and** once through the UI (screenshots into
`docs/finals/screens/workflow_story1/`).

### Beat A: Amy takes the call
| # | Action | Pass rule |
|---|---|---|
| A1 | `amy.am` asks the exact question: "Why has Velquarry Trading's account been restricted, and what should I tell the customer?" | 200; the answer contains the approved script (F6) |
| A2 | Citations | ≥1 citation; every cited doc is openable by `amy.am` (re-check `/ib/doc/{id}` → 200) |
| A3 | No tipping-off | The answer and its sources contain **none** of: "FinCrime", "investigation", "suspicious", "STR", "STRO", "37", "device", "fingerprint", "due diligence", "FC-2026", "Rachel", "MLRO", "case". The scope note is the constant one |
| A4 | Model label | When the model wrote the answer, the footer and `/ib/status` show the neutral model label: never "Hy3", never "no model", never the provider |
| A5 | Draft a reply (Act) | "Draft a reply" works; the draft uses only the approved wording (the 80% floor and anchor rules); nothing is sent; the ledger shows `action.requested` → `action.drafted` |
| A6 | Voice (UI only) | The mic button puts dictated text in the box and never auto-asks (existing e2e with a speech stub). A real voice test is optional and manual |
| A7 | Time | The answer appears in < 5 s with the model on (record p50 and max over 3 runs) |

### Beat B: Rachel, the same question
| # | Action | Pass rule |
|---|---|---|
| B1 | `mlro.tan` asks the exact same question | 200; the answer covers the review, the restriction and the STR status, with citations |
| B2 | Facts | The answer or page shows 37 transfers, nine days, 11 of 37 on one device, and STR filed. Each number is **traceable to a cited source** (F2, F3, F5) |
| B3 | "Pages beside the chat" | A case page or dashboard is composed; **every value on it passes the page check** (no unverified number); each value links to its source (case page / Slack thread / Drive analysis) |
| B4 | Amy gets no dashboard | Amy's same question composes **no** case page (only the script); the response shape gives no hint |
| B5 | Compare | Compare Amy vs Rachel shows two different answers, and the UI says so |

### Beat C: Ken, restricted ≡ non-existent
| # | Action | Pass rule |
|---|---|---|
| C1 | `ken.ext` asks about Velquarry | The constant refusal |
| C2 | `ken.ext` asks about "Bayfront Spice Traders" | The constant refusal |
| C3 | Identical | C1 and C2 are **byte-identical** after stripping request_id, as_of and *_checked_at (the same rule as the P2 eval); the UI shows "Identical responses" (**verified:** `cmp.identical` = "Identical responses", zh 回答完全相同, sub-line "Same text, same sources"; rendered by `components/compare/EqualityMarker.tsx`; lib/i18n/presenter.ts:39) |
| C4 | No model leak | With the model on, the model received **no** Velquarry passage for Ken (the Gate-1/Gate-2 decision row shows 0 permitted docs) |

### Beat D: the regulator (Maya)
| # | Action | Pass rule |
|---|---|---|
| D1 | `maya.compliance` opens the audit log and finds the requests from beats A–C | The rows exist for Amy, Rachel and Ken (asker, decision, released) with no answer text in the ledger |
| D2 | Verify | "Chain intact" |
| D3 | Tamper (sandbox) | Edit one old record on the copy → "Chain broken at entry n"; delete records → caught; "Your real audit log was not modified" (the real chain still verifies) |
| D4 | Journey | Rachel's request opens in the journey view (not "Request not found") |

### Beat E: the numbers
| # | Action | Pass rule |
|---|---|---|
| E1 | Proof slide | It shows the eval numbers from eval_summary.json (7,374 / 0; 400 / 0). After the model-on eval, it shows the model-on numbers |
| E2 | Business slide | It shows the projection (≈ US$1.28M gross), clearly labelled "Projection" |

### Cross-checks (every beat)
- No stand-in provider name appears anywhere in the UI or answers.
- Every answer has a write-ahead ledger trail (`request.accepted` → `retrieval.decided` → `answer.released`).
- With the model OFF (key removed), the same script passes on the extractive path (the fallback is proven).
- Network failure: with the model unreachable, answers fall back to quoted extracts with no error on screen.

## 3. How it will run (to build after approval)

1. **Data file:** `databridge/scripts/workflows/story1_the_call.yaml`, listing the steps above (persona,
   question, expectations, forbidden terms).
2. **Runner:** the cloud's data-driven `databridge/scripts/workflow_check.py` (API checks), plus a Playwright
   spec `tests/e2e/workflow_story1.spec.ts` for the UI beats and screenshots, headless.
3. **Order on the laptop:**
   1. Pull `final_v1.4`.
   2. The founder adds the key.
   3. `Reset Walnut Demo` (fresh).
   4. Run the workflow with the model ON (3 repeats).
   5. Run it with the model OFF (1 run).
   6. Run the leak eval with the model ON.
4. **Output:** `docs/finals/demo_day/WORKFLOW_STORY1_RESULTS.md`, with pass/fail per check, the real answer text
   (synthetic data only), timings and screenshots. Any red check is fixed before recording.
5. **Time:** about 20–30 min of runs on the laptop, plus fixes.

## 4. Open points for the founder

1. F4: the story says the due-diligence call was "on 6 October". **Resolved:** the world dates that message 6 Oct 2026 11:20 SGT (timestamp only, not in the text); say "on 6 October" in the story only as the source's date.
2. F8: the stage line becomes "a **projected** US$1.3M a year".
3. F9: say "0 leaks" with the model on only after the model-on eval passes.
4. Voice on stage uses the browser's speech service (honest limit #9). A typed fallback is ready.

## 5. How to run the checker

The generic runner is `databridge/scripts/workflow_check.py` (built 30 Oct 2026). It reads a steps file
(JSON, or YAML with PyYAML) and runs every step through the real HTTP API of a running Internal Brain:
demo sign-in, `/ib/ask`, `/ib/status`, `/ib/graph/openable` + `/ib/doc/{id}` (citation re-check), the
act / deliverable / view routes, and Maya's `/audit/journey/{req_id}` and `/audit/verify`. The full step
schema is in the script's docstring; `databridge/scripts/workflow_steps/sample_no_model.json` is a worked
example (Amy/Rachel/Ken on Velquarry, Ken's Bayfront refusal byte-identical to his Velquarry one,
Jordan's starter, a deliverable, Maya's journey checks, FinCrime canaries that must never appear).

Step fields: `id`, `persona`, `question`, `same_as` (byte-identical after stripping `request_id`,
`as_of`, `*_checked_at`), and `expect.{contains, not_contains, refusal, citations_min, cite_recheck,
model_label: "neutral", model_answered, max_latency_ms, ledger}`, plus an optional `action`
(`draft` | `ticket_propose` | `deliverable` | `view`) with its own `expect`. A `not_contains` term written
`/…/` is a regex (use `/\bSTR\b/` so "restricted" is not a hit).

On the laptop, model ON (from `databridge/`, with the Brain already started by `Start Walnut Demo`
and the stand-in switched on per the private checklist):

```bash
# env names only; values come from databridge/.env.ib.local, never typed into a doc
#   IB_DEMO_LOGIN=1, IB_DELIVERABLES=1 (server side), IB_DEMO_PASSCODE (if the server sets one)
#   IB_BASE_URL (optional; default http://127.0.0.1:8100)
.venv/Scripts/python scripts/workflow_check.py scripts/workflow_steps/sample_no_model.json \
    --out ../docs/finals/demo_day/workflow_results/run1.json
.venv/Scripts/python scripts/workflow_check.py STEPS.json --only amy_velquarry,ken_bayfront
```

- With the model on, add `"model_answered": true` to the steps whose answer must come from the model
  (the sample leaves it out, so the same file passes on both paths); `model_label: "neutral"` then
  checks that the label is neither the provider, nor Hy3/TokenHub (unless `/ib/status` says TokenHub
  is active), nor "no model".
- Exit code 0 only when every step passed; 1 when one failed; 2 for a bad steps file or no server.
  Run it 3 times for A7 (`max_latency_ms` checks one ask; this script's own wall clock).
- In the cloud it runs in-process with no model: `apps/internal_brain/tests/test_workflow_check.py`.

**What `cite_recheck` proves, and what it does not:** every cited document is openable by that persona
*at check time* (the API's own Gate 1 + live Gate 2 check, `/ib/graph/openable`, and `/ib/doc/{id}` 200).
It does not prove that each sentence is supported by its source (that is the grounding step inside
the Brain), nor what was openable at answer time, nor that no restricted passage reached the model
(C4: read `considered`/`released` in Maya's journey or the leak eval). Also: a draft ledgers
`action.proposed` on the request's journey (`action.requested` carries no request id, so it is not
in the journey).
