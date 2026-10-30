# Walnut DataBridge: judge's guide

**Answers you can prove.** Ask your whole company a question, and get an answer built only from what
you're allowed to open.

Tencent Cloud AI CAN DO IT Hackathon Singapore 2026 · FinTech Track · Aspire: *"The Internal Brain:
a context-aware enterprise knowledge system with RBAC, security logging and an audit trail."*
**Walnut DataBridge** is the product; **Internal Brain** is the ask product inside it.

Every feature, who can use it, where it is and the test that proves it:
[FEATURE_CATALOGUE.md](docs/finals/FEATURE_CATALOGUE.md). Every change since the 16 Oct submission:
[CHANGELOG.md](docs/finals/CHANGELOG.md).

---

## Finals build

This is the build for Demo Day (3 Nov 2026), on branch `final_v1.3`. Since the 16 Oct submission, one
web app now has two modes, switched at the top left:

- **User mode** for staff: Ask (with optional voice dictation), Compare, History, Settings and the
  Value calculator.
- **Developer mode** for engineers and compliance: Mission control, Request journey, Knowledge graph,
  Walnut engine, Analytics, Audit (with a sandbox tamper demo), Simulator and Trust centre.
- **Presenter mode** runs the Demo Day script from the keyboard, with a recorded backup clip for each
  live step (`B`), and **tours** teach each mode.

The backend rules did not change: two permission gates before any model, identity only from the signed
token, a write-ahead sealed log, and one response shape. The new developer views follow the same rules
([ARCHITECTURE.md → Developer views](docs/tencent/ARCHITECTURE.md#8-developer-views-and-their-guarantees),
[SECURITY.md §15](docs/tencent/SECURITY.md#15-developer-views-and-the-finals-v2-additions-30-oct)).

Two facts first. **Hy3 is wired in but has not run live** (TokenHub activation pending), so answers
show "Quoted from sources (no model)". **The platforms are mock servers** with the real APIs' shapes.

### The 10-minute walk-through

Start the stack first: `bash databridge/scripts/demo_up.sh start --fresh` ([Run it](#run-it)), then
open http://127.0.0.1:3100. To change person, use **Sign out** in the menu at the top right and pick
someone else. There is no password.

It follows the Demo Day story: Amy and Rachel ask the same question; Ken gets identical refusals; a
source is checked live; the journey is read as compliance (Maya); Verify and the tamper demo; then the
knowledge graph and analytics.

**Part A: User mode (about 4 minutes)**

| # | Time | Person | Click | What to notice |
|---|---|---|---|---|
| 1 | 0:00 | **Amy** (`amy.am`, account manager) | On the sign-in page, pick Amy | Each card shows the person's role and what they can open |
| 2 | 0:30 | Amy | **Ask** → the suggestion "Why has Velquarry Trading's account been restricted, and what should I tell the customer?" | Live stage events while it runs, then a cited answer: the approved customer script, and nothing about the case |
| 3 | 1:00 | Amy | Type "Has a suspicious transaction report been filed on Velquarry Trading?" (also one of her starter cards) | The constant refusal: "I couldn't find an answer to that in the sources you can access." No count, no hint that a case exists |
| 4 | 1:30 | Amy | **Compare** → "Same question · two people". First person **Amy**, second person **Rachel** (`mlro.tan`); paste the Velquarry question; **Ask both** | Same brain, two truths. Amy gets the script; Rachel, the money-laundering reporting officer, gets the case (synthetic case FC-2026-0147). Each side is its own signed-in session |
| 5 | 2:15 | (Compare) | In Rachel's answer, press the chip **[2]** | The source sheet: platform, version, "Checked live at", and the cited sentences highlighted. **Esc** closes it and returns focus to the chip |
| 6 | 2:45 | (Compare) | "Same person · two questions". Person **Ken** (`ken.ext`, contractor); left: the Velquarry question; right: the same question about "Bayfront Spice Traders" (a company that does not exist); **Ask both** | Two refusals and the marker "Identical responses: same text, same sources". Restricted looks exactly like non-existent |
| 7 | 3:30 | Amy | **EN** in the header (or **Settings** → Language) | The interface flips to 简体中文. Settings also has text size, reduce motion and high contrast |

**Part B: Developer mode (about 6 minutes)**

| # | Time | Person | Click | What to notice |
|---|---|---|---|---|
| 8 | 4:00 | **Maya** (`maya.compliance`, compliance officer) | Sign out, pick Maya, then **Developer** at the top left. **Mission control** opens | The pipeline: signed identity, ledger, Gate 1, relevance, Gate 2, ledger, model chain, grounding, ledger, answer. Each step shows its live p50 from sealed timings. Select a step to read its guarantee |
| 9 | 4:45 | Maya | **Journey** → the row with `mlro.tan` and the Velquarry question | The request end to end as compliance sees it: a millisecond waterfall (Accept, Gate 1, Gate 2, Seal, Compose, Release), the Gate-2 verdict for each document with its reason code, the model, and the three sealed ledger records on the same axis. Never the answer text |
| 10 | 5:45 | Maya | **Audit** → **Verify audit chain** | "Chain intact", and the head matches the copy published off-host |
| 11 | 6:15 | Maya | Same page, **Tamper demo** → **Run the tamper demo on a copy** | "Your real audit log was not modified." One row per attack on the copy: editing one old record gives "Chain broken at entry n"; deleting, restoring an old backup and deleting the anchors are flagged. One row honestly reads "Not detectable: the stated window" (a rollback after the last off-host publication) |
| 12 | 7:00 | Maya | **Analytics** | Requests, answer rate, latency p50/p95, Gate-2 deny rate and reasons, grounding drops, fallback use, all from the verified ledger. The banner says how many requests are seeded replay; **Live only** removes them |
| 13 | 7:45 | **Jordan** (`jdoe`, backend engineer) | Sign out, pick Jordan, Developer → **Knowledge graph** | Everything Jordan can open as one graph: 867 items, 17 places, 4 platforms. Search "migration", select a node: the panel shows the passage and its lineage, each re-checked live |
| 14 | 8:30 | Jordan | **Compare two people** (top right of the graph) → Ken | A second, separately signed-in graph: Ken can open 24 items on 2 platforms, and none of Jordan's. No greyed placeholders for what Ken cannot open |
| 15 | 9:00 | Jordan | **Journey** | Jordan sees only a receipt of his own requests: steps ticked, total time rounded, the sources he can still open, the record hashes. No other person's requests, no Gate-2 reasons. **Analytics** and **Audit** show a lock card |
| 16 | 9:30 | Jordan | **Walnut engine**, then **Trust centre** | The engine underneath (932 entities, 2,246 links, 4,710 pointers; read-only). The Trust centre lists seven controls, each with the test that proves it |

**Optional.** Presenter mode: open http://127.0.0.1:3100/compare?present=1, press `P`, then `Space` for
each of the 11 steps ([PRESENTER_KEYS.md](docs/finals/rubric/demo_story/PRESENTER_KEYS.md)); `B` plays
the step's backup clip, always labelled "Recording · not live". The
tours: **Take the tour** in the header (User mode 4 steps, Developer mode 5 steps). Simulator as
`admin.it`: see [Aspire's five scenarios](#aspires-five-scenarios), and note that Reset now reconciles revocations (fixed in 8582f4b; see
[Honest limits](#honest-limits)).

### Screenshots

From the live backend (30 Oct 2026), in [docs/finals/screens/live/](docs/finals/screens/live/):

| # | Screen | File |
|---|---|---|
| 1 | Sign-in: pick a person | [login.png](docs/finals/screens/live/login.png) |
| 2 | Amy asks about a suspicious-transaction report: the constant refusal | [user-amy.am-answers.png](docs/finals/screens/live/user-amy.am-answers.png) |
| 3 | Compare: Amy vs Rachel on Velquarry | [compare-amy-rachel.png](docs/finals/screens/live/compare-amy-rachel.png) |
| 4 | The source sheet: version, checked live, cited sentences | [compare-amy-rachel-sheet.png](docs/finals/screens/live/compare-amy-rachel-sheet.png) |
| 5 | Ken: Velquarry vs a made-up company, identical | [compare-ken-identical.png](docs/finals/screens/live/compare-ken-identical.png) |
| 6 | Mission control as compliance | [dev-maya-mission.png](docs/finals/screens/live/dev-maya-mission.png) |
| 7 | Journey waterfall as compliance | [dev-maya-journey-fresh.png](docs/finals/screens/live/dev-maya-journey-fresh.png) |
| 8 | Journey of a real Gate-2 denial (seeded replay) | [dev-maya-journey-seeded-denial.png](docs/finals/screens/live/dev-maya-journey-seeded-denial.png) |
| 9 | Journey receipt as Jordan (own request only) | [dev-jdoe-receipt.png](docs/finals/screens/live/dev-jdoe-receipt.png) |
| 10 | Sandbox tamper demo | [dev-maya-audit-tamper.png](docs/finals/screens/live/dev-maya-audit-tamper.png) |
| 11 | Knowledge graph: Jordan and Ken side by side | [dev-jdoe-graph-compare.png](docs/finals/screens/live/dev-jdoe-graph-compare.png) |
| 12 | Analytics as compliance | [dev-maya-analytics.png](docs/finals/screens/live/dev-maya-analytics.png) |
| 13 | Walnut engine | [dev-jdoe-brain.png](docs/finals/screens/live/dev-jdoe-brain.png) |
| 14 | Trust centre | [dev-jdoe-trust.png](docs/finals/screens/live/dev-jdoe-trust.png) |
| 15 | Presenter mode, Stage theme, step 1 | [presenter-step-01-compare-people.png](docs/finals/screens/live/presenter-step-01-compare-people.png) |

All 97 live screenshots, and how they were taken, are listed in [QA_LIVE.md](docs/finals/QA_LIVE.md).
The 16 Oct submission screenshots are in [submission/](submission/README.md).

---

## In two minutes

The Internal Brain answers plain-English questions across **Confluence, Jira, Slack and Google Drive**.
Before anything reaches the AI model, every source is checked twice: once against the permissions copied
from each platform, and again live with the platform itself. The answer cites every source with its
version and the time access was checked. Every question, every allow/deny decision and every answer is
written to an audit log that is chained and sealed, so an edit or a deletion is detectable.

Aspire asked for three things. This is where to see each one:

| Aspire asked for | What the Brain does | Where to see it | Proof |
|---|---|---|---|
| Knowledge across tools | One cited answer from all four platforms | **Ask** as `jdoe` | `test_scenarios_e2e.py::test_canonical` |
| Restrictions per person | Each person's answer uses only what they can open; "restricted" looks exactly like "doesn't exist" | **Compare** (Amy beside Rachel; Ken's two questions), **Knowledge graph** | 0 leaks in 7,374 requests; 0 of 400 paired answers differ |
| Security logging and an audit trail | Every question and decision is logged before the answer is shown, and the log is tamper-evident | **Audit** as `maya.compliance` → **Verify audit chain** and the tamper demo; **Journey** | `test_audit_tamper.py`, `test_tamper_demo_route.py`, `test_audit_query.py::test_s5_reconstruction` |

Everything runs on a synthetic fintech called **Company A** (34 people, 231 Confluence pages, 261 Jira
issues, 628 Slack threads, 87 Drive files). No real company or customer data is used anywhere.

**What's real and what's simulated.** The four platforms are mock servers that speak the real APIs'
shapes (their permission models, webhooks, errors and rate limits); we have not run against a live
tenant. Answers in the demo are quoted straight from the sources. Tencent Hy3 through TokenHub is wired
in but has not been run live yet: TokenHub activation is pending. When it runs, it goes through exactly
the same checks. The full list is in [Honest limits](#honest-limits).

## Why it matters

- **Search costs real time.** Aspire's brief puts it at about 30% of the work week, and their Head of AI
  told us the "what can we offer this customer?" question in particular "takes time right now".
- **An assistant that can read everything can leak everything.** Copilot oversharing, prompt injection
  through retrieved documents (EchoLeak, Slack AI) and permission sync lags of 30 minutes to a day are
  all on record. For a payments company supervised by MAS, that blocks rollout entirely.
- **In fintech a leak can be a crime.** Telling a customer that a suspicious-transaction report exists is
  tipping-off (CDSA s.57, s.48 before the 2020 Revised Edition; MAS Notice PSN01). Card numbers pasted
  into a ticket must never reach an index or a model (PCI DSS 3.4.1).

**What the Brain changes:** people get cited answers in seconds; the model never sees what the asker
can't see; and compliance can reconstruct any access, with proof the record hasn't been altered.

## What you can see

| Mode | Screen | Who to sign in as | What it shows |
|---|---|---|---|
| User | **Ask** (`/`) | anyone (try `jdoe` or `amy.am`) | One cited answer from several platforms, live stage events while it runs, and a source sheet with each source's version and "checked live" time. A microphone button dictates the question (browser speech; you still press Ask) |
| User | **Compare** (`/compare`) | anyone | The same question asked by two people side by side, or one person asking two questions, each in its own signed session |
| User | **History** (`/history`) | anyone | Your own questions by day; a row opens your receipt |
| User | **Settings** (`/settings`) | anyone | English / 简体中文, theme, text size, reduce motion, high contrast, replay the tour |
| User | **Value** (`/value`) | anyone | ROI calculator; inputs visible, result labelled an estimate |
| User | **Map** (`/map`) | anyone | The pre-finals map: everything *you* can open, by platform and space. `jdoe` sees 867 items on 4 platforms; `ken.ext` sees 24 on 2 |
| Developer | **Mission control** (`/dev`) | anyone; organisation numbers need `maya.compliance` | The pipeline with live p50 per stage, source freshness, ledger head, model status |
| Developer | **Journey** (`/dev/journey`) | `maya.compliance` for any request; anyone for their own | Compliance: the full waterfall with Gate-2 reasons. Everyone else: a receipt of their own request |
| Developer | **Knowledge graph** (`/dev/graph`) | anyone | Your permitted documents, places, people and links as one graph; compare two separately signed-in people |
| Developer | **Walnut engine** (`/dev/brain`) | `jdoe` only | The engine's entity graph of Company A as `jdoe` sees it, the time slider, provenance to the row, the ontology. Read-only. The snapshot is not re-checked live and its reads are not logged, so everyone else, compliance included, sees a lock card |
| Developer | **Analytics** (`/dev/analytics`) | `maya.compliance` | End-to-end numbers from the verified ledger; replay history labelled |
| Developer | **Audit** (`/dev/audit`) | `maya.compliance` | Plain-English audit search, reveal-with-reason, **Verify audit chain**, and the sandbox tamper demo |
| Developer | **Simulator** (`/dev/sim`) | `admin.it` | Change access live (remove someone from a channel, restrict a page, edit a runbook) and watch the next answer change |
| Developer | **Trust centre** (`/dev/trust`) | anyone | Seven controls, their framework mappings and the tests that prove them |
| Both | **Take the tour** | header button | A guided walk: 4 steps in User mode, 5 in Developer mode |
| Presenter | **Presenter mode** (`/compare?present=1`, then `P`) | the script signs in | The 11-step Demo Day script (HUD 00 to 10) on the keyboard, with a Stage theme for projectors |
| Outside the app | **WorkBuddy** | three ready-made workspaces | Ask the Brain from Tencent WorkBuddy, as an engineer, a contractor, or the compliance officer |
| Outside the app | **Original Walnut screens** (http://localhost:3300) | started by `demo_up.sh` | The Walnut engine's own views on Company A, with Walnut's guided demo |

## Run it

**You need:**
- [Python 3.13](https://www.python.org/downloads/) and [Node 20](https://nodejs.org/).
- On Windows, [Git for Windows](https://git-scm.com/download/win), for Git Bash. The launcher is a bash
  script.
- Free ports: **3100** (web app), **8100** (the Brain), **8200** (mock platforms), and **8300** / **3300**
  (the Walnut engine gateway and its original screens).

**1. One-time setup**, from the `databridge/` folder:

```powershell
# Windows (PowerShell)
py -3.13 -m venv .venv; .venv\Scripts\python -m pip install -r requirements-lock.txt
cd apps\internal_brain_web; npm install; cd ..\..
```

```bash
# macOS / Linux
python3.13 -m venv .venv && .venv/bin/python -m pip install -r requirements-lock.txt
(cd apps/internal_brain_web && npm install)
```

**2. Start everything with one command**, from the repo root (Git Bash on Windows):

```bash
bash databridge/scripts/demo_up.sh start --fresh
```

[`demo_up.sh`](databridge/scripts/demo_up.sh) starts, in order: the mock platforms (:8200), the Brain
(:8100, which indexes the whole world first), the seeded demo history (`--fresh` only: about 300
labelled replay questions through the real pipeline, about 2 minutes), the Walnut engine gateway
(:8300), and a production build of the web app (:3100). It prints this when ready:

| Open | URL |
|---|---|
| The app (User mode; switch to Developer at the top left) | http://127.0.0.1:3100 |
| Developer mode | http://127.0.0.1:3100/dev |
| Presenter mode | http://127.0.0.1:3100/compare?present=1 |

`bash databridge/scripts/demo_up.sh status` lists the ports; `bash databridge/scripts/demo_up.sh stop`
stops everything. Logs are in `databridge/var/ib-final/logs/`. Without `--fresh`, the existing demo
history is kept. Everything binds to 127.0.0.1.

**3. Sign in.** Pick a person. There is no password, and the "Demo passcode" field stays empty when you
run it locally. The demo sign-in stands in for your company's single sign-on.

**If something goes wrong:**
- *A port is in use:* stop whatever holds it, or run `demo_up.sh stop`, and start again.
- *"web build failed":* read `databridge/var/ib-final/logs/web-build.log`; usually `npm install` was
  not run.
- *Only the Walnut engine page fails:* the engine gateway did not start; see
  `databridge/var/ib-final/logs/engine.log`. Every other page works without it.

**Fallback: start each part by hand** (two terminals, from the repo root, Git Bash). This path starts no
demo history and no engine gateway:

```bash
# Terminal 1: mock Slack/Confluence/Jira/Drive (:8200) + the Brain (:8100)
cd databridge && bash scripts/run_local_demo.sh
```

```bash
# Terminal 2: the web app on http://127.0.0.1:3100
cd databridge/apps/internal_brain_web && npm run dev
```

The Brain is ready when terminal 1 prints `Uvicorn running on http://127.0.0.1:8100`. Ctrl+C in
terminal 1 stops both the Brain and the mock platforms. The same three processes can be started without
bash; the commands are in [README.md → Fallback](README.md#fallback-start-each-part-by-hand).

**Demo people** (all fictional): `jdoe` backend engineer · `priya.sre` on-call SRE · `ken.ext` contractor
(Slack Connect guest) · `sara.sec` security · `leo.ops` product/ops · `amy.am` account manager ·
`mlro.tan` (Rachel) money-laundering reporting officer · `maya.compliance` compliance officer ·
`admin.it` IT admin.

### Optional extras

- **Use a real model (Tencent Hy3 or your own).** By default answers are quoted straight from the
  sources. To have a model write them, create `databridge/.env.ib.local` (the launcher reads it; it is
  git-ignored) with one of these, then restart (`demo_up.sh stop`, then `start`; or restart terminal 1):

  ```bash
  # Tencent TokenHub: Hy3, with deepseek-v4.1-flash as backup
  TOKENHUB_API_KEY=<your TokenHub key>
  ```

  With a TokenHub key, `demo_up.sh` checks TokenHub first (`scripts/hy3_check.py --quiet`) and turns
  the model on by itself only if it answers; set `IB_FORCE_OFFLINE=0` or `1` to decide yourself. To
  check by hand: `PY scripts/hy3_check.py` (exit 0 live, 1 not live, 2 no key; today it reports
  401006, "not activated").

  ```bash
  # Any OpenAI-compatible endpoint: OpenAI, or a local Ollama / vLLM server
  OPENAI_API_BASE=http://localhost:11434/v1      # e.g. Ollama; https://api.openai.com/v1 for OpenAI
  OPENAI_API_KEY=ollama                          # your key (any text for Ollama)
  IB_MODELS=qwen2.5:7b,llama3.1:8b               # tried in order; the first that answers is used
  IB_FORCE_OFFLINE=0
  ```

  Whichever model you use, it only ever sees the passages that passed both checks, the grounding check
  still runs on its answer, and the answer says which model wrote it (`tests/test_model_config.py`,
  `tests/test_llm_chain.py`). If every model fails, you get the quoted answer instead. The same file can
  hold `IB_TOKEN_KEY` and `IB_LEDGER_KEY` (32+ random characters each) for your own signing keys;
  without them the local demo uses built-in development keys.
- **Original Walnut screens** on Company A: `demo_up.sh` starts them. On the fallback path, from
  `databridge/`, run `bash scripts/run_walnut_original.sh`, then open http://localhost:3300. It uses ports 3300 and 8300
  and never touches the Brain's ports. The first run builds the graph and the web app (a few minutes);
  details in [databridge/apps/frontend/README.md](databridge/apps/frontend/README.md).

In the commands below, `PY` is `.venv\Scripts\python` on Windows and `.venv/bin/python` on macOS/Linux,
run from `databridge/`:

- **WorkBuddy:** `PY scripts/workbuddy_setup.py --user-config` adds the two employee Internal Brain
  connectors to WorkBuddy (as Jordan and as Ken). The compliance connector never goes in the same list:
  use the `IB compliance` workspace folder, or `--compliance-user-config` on the compliance seat.
  Restart WorkBuddy and press **Trust** on each
  (guide: [docs/tencent/workbuddy/README.md](docs/tencent/workbuddy/README.md)).
- **Tests:** `PY -m pytest apps/internal_brain/tests -q`
- **Leak evaluation:** `PY -m evals.internal_brain.run` (about 1 to 10 minutes depending on the machine;
  add `--limit 60` for a one-minute check)
- **Tamper demo:** `PY scripts/tamper_demo.py`
- **Web end-to-end tests:** from `databridge/apps/internal_brain_web`, `npm run e2e`. They build and run
  the web app in its built-in mock mode on port 3101, so no Brain is needed. On a fresh machine, run
  `npx playwright install chromium` once first.

## How it works

1. **Who's asking.** A signed token from the web app or WorkBuddy. No request body, tool argument or
   model output can name a person.
2. **First check (Gate 1).** Search only documents this person may open, using each platform's own
   permission model (Confluence page restrictions, Jira project roles and issue security, Slack private
   and Connect channels, Drive inheritance). Ranking statistics come from those documents only, so
   restricted content can't even shift the results.
3. **Second check (Gate 2).** Ask the platform again, live, for every passage about to be used. An error,
   timeout or rate limit means no. A document that changed is re-read and re-checked.
4. **Answer.** The model sees only the passages that passed, and must cite one for every sentence.
   Walnut's grounding check drops any number or name that isn't in the evidence; if the answer fails, the
   Brain quotes the evidence instead.
5. **Record.** The audit log is written before retrieval, before the model call and before the answer is
   shown. If it can't be written, nothing is answered.

Diagrams: [the request path](docs/tencent/ARCHITECTURE.md) and
[trust boundaries](docs/tencent/diagrams/trust_boundaries.mmd) (drawn with CodeBuddy).

## Proof

All evaluation numbers are from [RESULTS.md](databridge/evals/internal_brain/RESULTS.md), re-run on
30 Oct 2026 (06:09 SGT; 29 Oct 22:09 UTC) on the finals pipeline. Test paths are
under `databridge/apps/internal_brain/tests/` unless shown otherwise.

| What | Result | Where |
|---|---|---|
| Restricted content in any answer, prompt or response | **0 leaks** in 7,374 adversarial requests (3,687 probes, each run on both answer paths; 95% upper bound 0.08% per request on each path) | `RESULTS.md` |
| "Restricted" vs "doesn't exist" | **0 differences** across 400 paired answers | `RESULTS.md` |
| Prompt injection planted on all four platforms | **0 failures** in 1,152 injection-relevant requests | `RESULTS.md` |
| Allowed sources reaching the answer step | **100%** of 442 positive probes; the answer cites the expected source in 93.2% (quoted path) to 97.1% (model path) | `RESULTS.md` |
| Map identical with and without restricted documents | test | `test_knowledge_map.py::test_map_is_identical_with_and_without_restricted_docs` |
| Audit log edits, deletions, truncation, rollback, edited stored answers | detected | `test_audit_tamper.py`, `test_audit_verify_content.py` |
| Knowledge graph and document view identical with and without restricted documents; restricted and unknown documents give the same 404 | tests | `test_graph_doc.py::test_graph_is_identical_with_and_without_restricted_docs`, `::test_doc_restricted_and_unknown_are_the_same_404` |
| Employee receipt lists only cited sources still openable; journey and stats are compliance-only | tests | `test_graph_doc.py::test_trace_lists_only_cited_docs_still_openable`, `test_dev_views.py::test_journey_is_compliance_only_and_has_no_answer_text` |
| Tamper demo runs on a copy; the live log's bytes are unchanged | tests | `test_tamper_demo_route.py` |
| Live stage events never time the permission checks (Gate 1 and Gate 2 are sent only once the decision is sealed); a foreign `Host` header is refused before any route | tests | `test_final_review_fixes.py::test_gate1_and_gate2_are_held_until_the_checks_are_sealed`, `::test_a_foreign_host_header_is_refused_before_any_route` |
| The engine snapshot is readable only by the persona it was built for | tests | `internal_brain_web/tests/e2e/engine-proxy.spec.ts`, `engine.spec.ts` |
| Automated tests, Internal Brain | **1,131 passed**, 0 failed (commit 831297e, 30 Oct 2026, cloud: Linux, Python 3.13, `IB_FORCE_OFFLINE=1`; 639 at submission) | `databridge/apps/internal_brain/tests/` |
| Web end-to-end tests (Playwright, including axe accessibility checks) | 166 passed, 0 failed, 41 skipped (the skipped specs need the live stack, the engine proxy or Hy3); mock mode, every spec `playwright.config.ts` runs, including `act`, `grow`, `deliverables` and `voice` (30 Oct 2026, cloud: Linux, commit 831297e; [TEST_REPORT](docs/finals/TEST_REPORT.md)) | `databridge/apps/internal_brain_web/tests/e2e/` |
| Live QA of every page against the real backend | 32 checks, 11 bugs fixed, 4 open issues listed | [QA_LIVE.md](docs/finals/QA_LIVE.md) |

The evaluation's own canaries prove it can fail: a deliberately leaky configuration (both checks
switched off) is caught on 18 of 20 probes, and a deny-everything configuration scores 0% usefulness.

## Aspire's five scenarios

| # | Scenario | Try it | Proven by |
|---|---|---|---|
| 1 | One question, answered from several platforms, private channel omitted | Ask as `jdoe`: "What's the status of the database migration project and were there any blockers raised in Slack last week?" | `test_scenarios_e2e.py::test_canonical` |
| 2 | Fresh content is answerable quickly, citing the new version | Simulator → "Edit failover runbook (new version)", then ask as `priya.sre`: "What's the latest runbook for the payment-service incident?" | `test_scenarios_e2e.py::test_s2_freshness_via_webhook` |
| 3 | A contractor gets the same refusal for a restricted report and an invented one | Ask as `ken.ext`: "Show me the security incident report from the Q3 breach.", then the same with Q7 | `test_scenarios_e2e.py::test_s3_existing_vs_nonexistent_identical` |
| 4 | Access removed at the source is honoured on the very next question, even if the webhook is lost | Simulator → "Remove jdoe from #db-migration", then ask as `jdoe`: "What backfill throttle are we using for the ledger copy job?" | `test_scenarios_e2e.py::test_s4_revocation_live`, `::test_s4_revocation_without_webhook` |
| 5 | Compliance reconstructs everything one person accessed in a space over 30 days | Audit as `maya.compliance`: "Show me everything jdoe accessed related to the payment-gateway space in the last 30 days" | `test_audit_query.py::test_s5_reconstruction` |

Test paths are under `databridge/apps/internal_brain/tests/`. The end-to-end tests run the real mock
platforms, the real connectors with signed webhooks, and the full pipeline over the generated world.

## The fintech layer

Added as data and configuration on top of the general system. Tests are in
`databridge/apps/internal_brain/tests/test_fintech_scenarios.py` unless shown otherwise.

| Rule | What it does | Proven by |
|---|---|---|
| **FinCrime compartment** | Suspicious-transaction case notes are readable only by the money-laundering reporting officer. An account manager asking about that customer gets the approved customer script, and the answer reads exactly like the answer for a company that doesn't exist (anti-tipping-off) | `::test_fc_real_vs_nonexistent_customer_is_byte_identical`, `::test_fc_account_manager_gets_only_the_approved_script` |
| **Market-specific answers** | "What can we offer this SME?" is answered per market (Singapore, Indonesia), each citing that market's approved policy | `::test_mk_singapore_and_indonesia_answers_cite_different_market_facts` |
| **Approved policies only** | A draft fee schedule with different numbers is never cited | `::test_fee_draft_is_never_indexed_considered_or_released` |
| **Card and ID masking** | Card numbers, CVVs, IBANs and NRICs are masked before indexing, so they never reach the index or a model | `::test_sens_raw_values_appear_nowhere_in_the_index`, `test_sensitive.py` |

Mapping to MAS TRM, the MAS AI Risk Management Guidelines, PDPA and PCI DSS, with limits:
[docs/tencent/MAS_TRM_MAPPING.md](docs/tencent/MAS_TRM_MAPPING.md).

## Honest limits

- **The platforms are mocked**, with their real API shapes and permission rules. The connectors are real
  code on the same path a real tenant would use, but have not been run against a real tenant.
- **Hy3 has not been run live.** The TokenHub call is wired in and tested with a capturing test model;
  the live run is pending TokenHub activation. All numbers above come from that capturing test model and
  the deterministic quoting path. Demo answers are quoted from the sources and say so.
- **Demo history is replay.** `--fresh` seeds about 300 questions through the real pipeline with past
  dates. They are labelled `replay` in the ledger and in Analytics, which offers "Live only".
- **Simulator Reset** now runs a full reconciliation, so a revocation is undone at once (fixed in 8582f4b,
  [QA_LIVE.md §4](docs/finals/QA_LIVE.md)).
- **Plain-English audit search (offline)** now resolves document titles from the local index (fixed in
  8582f4b). Hy3 is wired as the model fallback parser and has not run live.
- **Sign-in is a demo login** standing in for SSO. A deployed build refuses to start without a passcode.
- **The backup clips are interim.** Recorded 30 Oct without the Stage theme; clip C is an in-app
  stand-in for the WorkBuddy step. They are re-recorded after the Monday freeze
  ([BACKUP_PLAN.md](docs/finals/rubric/demo_story/BACKUP_PLAN.md)).
- **Voice dictation uses the browser's own speech service** (Chrome, Edge); the button is hidden
  elsewhere. The app stores no audio.
- **The grounding check verifies values and names, not the relations between them.**
- **The audit log has a key holder window.** It detects tampering by anyone who doesn't hold its sealing
  key; beyond the last off-host published head, a key holder could rewrite records (see
  [SECURITY.md](docs/tencent/SECURITY.md) §3).
- **The map is slower than a search.** It re-checks every item live before listing it, so a big map
  takes a few seconds.
- **Relevance is lexical.** The relevance check answered 7 of the evaluation's expected-refusal
  questions (on each answer path) from related pages the asker can open. None was a leak; all are listed
  in `RESULTS.md`.
- **Recall depends on what you count.** 100% is the share of allowed documents that reach the answer
  step. The share of answers that cite the expected source is 93.2% to 97.1%.

## What's inside

- **The Walnut evidence engine** underneath: provenance pointers, the sealed ledger, the grounding wall
  and the model harness. The Internal Brain uses it by import and does not modify it.
- **The Internal Brain** on top: the permission compilers, the two checks, the audit log and console,
  the web app, the mock platforms, the Company A world, the fintech rules, the WorkBuddy connectors, the
  evaluation and the original-screen configuration.

**Built with Tencent:**
- **CodeBuddy** built the WorkBuddy connector, the token tool and the model-chain tests, drew the trust
  boundaries, and ran a security review on Tencent Hunyuan `hy3`; then it fixed its own code after a Codex
  review. Every session's transcript is kept: [docs/tencent/DEV_PROCESS.md](docs/tencent/DEV_PROCESS.md).
- **WorkBuddy** is the second front door, including an audit assistant for compliance.
- **TokenHub** (Singapore) serves Tencent Hunyuan `hy3`, with `deepseek-v4.1-flash` as backup (wired in,
  not yet run live; see [Honest limits](#honest-limits)).
- **Lighthouse:** a hardened deploy kit (Docker, HTTPS, passcode-gated demo) is in
  [deploy/lighthouse/](deploy/lighthouse/README.md).
- **Miora** made the cover.

## Everything else

| Doc | What it covers |
|---|---|
| [FEATURE_CATALOGUE.md](docs/finals/FEATURE_CATALOGUE.md) | Every feature and backend capability: who, where, how, guarantees, tests, screenshots |
| [CHANGELOG.md](docs/finals/CHANGELOG.md) | Every change since the 16 Oct submission, by area, with commit links, and what is in progress |
| [docs/finals/README.md](docs/finals/README.md) | Everything written for the finals: plan, decisions, research, reviews, rubric evidence, contracts, QA, screenshots |
| [Rubric evidence](docs/finals/rubric/README.md) | Each of the 10 judging criteria: what is seen live, what to open, what to read |
| [Pitch deck](submission/07_deck/walnut_internal_brain_deck.pdf) | 23 slides: the problem, the idea, how it works, proof, the fintech layer, the rollout path |
| [Submission pack](submission/README.md) | Screenshots, CodeBuddy evidence, video, cards and deck in one folder |
| [DEV_PROCESS.md](docs/tencent/DEV_PROCESS.md) | How it was built: CodeBuddy sessions, reviews, what went wrong |
| [ARCHITECTURE.md](docs/tencent/ARCHITECTURE.md) | Components, the request path, and the developer views' guarantees |
| [SECURITY.md](docs/tencent/SECURITY.md) | Threat model, ledger attacker model, injection, every review finding and its fix |
| [RESPONSIBLE_AI.md](docs/tencent/RESPONSIBLE_AI.md) | Grounding, abstention, citations, oversight, evaluation |
| [MAS_TRM_MAPPING.md](docs/tencent/MAS_TRM_MAPPING.md) | Regulatory mapping, with limits |
| [DATA_FLOWS.md](docs/tencent/DATA_FLOWS.md) | What leaves the system and where state lives |
| [RESULTS.md](databridge/evals/internal_brain/RESULTS.md) | The leak and injection evaluation |
| [SUBMISSION.md](docs/tencent/SUBMISSION.md) | The submission form answers |
| [Original Walnut screens](databridge/apps/frontend/README.md) | How to start them and what each screen shows on Company A |
| [WorkBuddy guide](docs/tencent/workbuddy/README.md) | Connecting the three Internal Brain workspaces |
| [Lighthouse deploy kit](deploy/lighthouse/README.md) | Deploying the demo on Tencent Cloud Lighthouse |
| [README.md](README.md) | Walnut, the engine underneath |
