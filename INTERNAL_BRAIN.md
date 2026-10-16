# Walnut Internal Brain

**Ask your whole company a question, and get an answer built only from what you're allowed to open.**

Tencent Cloud AI CAN DO IT Hackathon Singapore 2026 · FinTech Track · Aspire: *"The Internal Brain:
a context-aware enterprise knowledge system with RBAC, security logging and an audit trail."*

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
| Restrictions per person | Each person's answer uses only what they can open; "restricted" looks exactly like "doesn't exist" | **Compare** (`jdoe` beside `ken.ext`), **Map** | 0 leaks in 5,958 requests; 0 of 400 paired answers differ |
| Security logging and an audit trail | Every question and decision is logged before the answer is shown, and the log is tamper-evident | **Audit** as `maya.compliance` → **Verify audit chain** | `test_audit_tamper.py`, `test_audit_query.py::test_s5_reconstruction` |

Everything runs on a synthetic fintech called **Company A** (32 people, 209 Confluence pages, 251 Jira
issues, 602 Slack threads, 81 Drive files). No real company or customer data is used anywhere.

**What's real and what's simulated.** The four platforms are mock servers that speak the real APIs'
shapes (their permission models, webhooks, errors and rate limits); we have not run against a live
tenant. Answers in the demo are quoted straight from the sources. Tencent Hy3 through TokenHub is wired
in but has not been run live yet, because Tencent's account verification is pending; when it runs, it
goes through exactly the same checks. The full list is in [Honest limits](#honest-limits).

## Two-minute tour for judges

Start the app first ([Run it](#run-it)), then follow these steps in order. Sign out from the header to
switch person. There is no password.

| # | Do this | What to notice |
|---|---|---|
| 1 | Sign in as anyone, open **Compare**, press **Ask for both** (it starts on `jdoe` and `ken.ext` with the database migration question) | Jordan gets a cited answer. Ken gets "I couldn't find an answer to that in the sources you can access.", with no hint that more exists |
| 2 | Open **Map** as `jdoe`, then as `ken.ext` | Jordan can open 867 items on 4 platforms, Ken 24 on 2. Platforms Ken can't open simply don't appear |
| 3 | **Ask** as `jdoe`: "What backfill throttle are we using for the ledger copy job?" | The answer cites a #db-migration Slack thread. The sources panel shows each source's version and when access was checked; the "How this was checked" strip sits under the answer |
| 4 | Sign in as `admin.it`, **Simulator** → "Remove jdoe from #db-migration"; sign in as `jdoe` and ask step 3 again | Jordan now gets the plain refusal, on the very next question. Press "Reset demo" afterwards |
| 5 | Sign in as `maya.compliance`, open **Audit**, press **Verify audit chain** | The questions above and their allow/deny decisions are in the log, and the result reads "Chain intact" |
| 6 | Start the original Walnut screens (below) and open http://localhost:3300 | The Walnut engine underneath: the knowledge graph of Company A as `jdoe` sees it, with the pointer behind every value |

**Take the tour** in the header gives the same walk inside the app.

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

| Screen | Who to sign in as | What it shows |
|---|---|---|
| **Ask** | anyone (try `jdoe`) | One cited answer from several platforms, a sources panel with each source's version and freshness, and a "How this was checked" strip |
| **Map** | anyone | Everything *you* can open, by platform and space. `jdoe` sees 867 items on 4 platforms; `ken.ext` sees 24 on 2 |
| **Compare** | anyone | The same question asked by two people, side by side |
| **Audit** | `maya.compliance` | Plain-English audit search, every allow/deny decision, a reasoned reveal of an answer, and **Verify audit chain** |
| **Simulator** | `admin.it` | Change access live (remove someone from a channel, restrict a page, edit a runbook) and watch the next answer change |
| **Take the tour** | button in the header | A short guided walk through the screens above |
| **WorkBuddy** | three ready-made workspaces | Ask the Brain from Tencent WorkBuddy, as an engineer, a contractor, or the compliance officer |
| **Original Walnut screens** | separate launcher (below) | The Walnut engine's own views on Company A, built as `jdoe` sees it: the knowledge graph (932 items and people), the company brain with a time slider, the ontology and the provenance chain of every value, with Walnut's guided demo |

## Run it

**You need:**
- [Python 3.13](https://www.python.org/downloads/) and [Node 20](https://nodejs.org/).
- On Windows, [Git for Windows](https://git-scm.com/download/win), for Git Bash. The start script is a
  bash script.
- Free ports: **3100** (web app), **8100** (the Brain) and **8200** (mock platforms).

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

**2. Start it** in two terminals (Git Bash on Windows, any terminal on macOS/Linux), from the repo root:

```bash
# Terminal 1: mock Slack/Confluence/Jira/Drive (:8200) + the Brain (:8100)
cd databridge && bash scripts/run_local_demo.sh
```

```bash
# Terminal 2: the web app on http://localhost:3100
cd databridge/apps/internal_brain_web && npm run dev
```

**3. Wait for the ready signal.** The Brain indexes the whole world on start, which takes about a
minute. It is ready when terminal 1 prints:

```text
Uvicorn running on http://127.0.0.1:8100
```

**4. Sign in.** Open http://localhost:3100 and pick a person. There is no password, and the "Demo
passcode" field stays empty when you run it locally. The demo sign-in stands in for your company's
single sign-on.

**If something goes wrong:**
- *A port is in use:* stop whatever holds 3100, 8100 or 8200 and start again.
- *The web app loads but answers fail:* terminal 1 hasn't printed the ready line yet. Wait, then ask
  again.
- *Ctrl+C in terminal 1* stops both the Brain and the mock platforms.

**Demo people** (all fictional): `jdoe` backend engineer · `priya.sre` on-call SRE · `ken.ext` contractor
(Slack Connect guest) · `sara.sec` security · `leo.ops` product/ops · `amy.am` account manager ·
`mlro.tan` money-laundering reporting officer · `maya.compliance` compliance officer · `admin.it` IT admin.

### Optional extras

- **Use a real model (Tencent Hy3 or your own).** By default answers are quoted straight from the
  sources. To have a model write them, create `databridge/.env.ib.local` (the launcher reads it; it is
  git-ignored) with one of these, then restart terminal 1:

  ```bash
  # Tencent TokenHub: Hy3, with deepseek-v4.1-flash as backup
  TOKENHUB_API_KEY=<your TokenHub key>
  IB_FORCE_OFFLINE=0
  ```

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
- **Original Walnut screens** on Company A: from `databridge/`, run
  `bash scripts/run_walnut_original.sh`, then open http://localhost:3300. It uses ports 3300 and 8300
  and never touches the Brain's ports. The first run builds the graph and the web app (a few minutes);
  details in [databridge/apps/frontend/README.md](databridge/apps/frontend/README.md).

In the commands below, `PY` is `.venv\Scripts\python` on Windows and `.venv/bin/python` on macOS/Linux,
run from `databridge/`:

- **WorkBuddy:** `PY scripts/workbuddy_setup.py --user-config` adds three Internal Brain connectors to
  WorkBuddy (as Jordan, as Ken, and for compliance). Restart WorkBuddy and press **Trust** on each
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

All evaluation numbers are from [RESULTS.md](databridge/evals/internal_brain/RESULTS.md). Test paths are
under `databridge/apps/internal_brain/tests/` unless shown otherwise.

| What | Result | Where |
|---|---|---|
| Restricted content in any answer, prompt or response | **0 leaks** in 5,958 adversarial requests (2,979 probes, each run on both answer paths; 95% upper bound 0.10% per request on each path) | `RESULTS.md` |
| "Restricted" vs "doesn't exist" | **0 differences** across 400 paired answers | `RESULTS.md` |
| Prompt injection planted on all four platforms | **0 failures** in 976 injection-relevant requests | `RESULTS.md` |
| Allowed sources reaching the answer step | **100%** of 196 positive probes; the answer cites the expected source in 87.8% (quoted path) to 96.4% (model path) | `RESULTS.md` |
| Map identical with and without restricted documents | test | `test_knowledge_map.py::test_map_is_identical_with_and_without_restricted_docs` |
| Audit log edits, deletions, truncation, rollback, edited stored answers | detected | `test_audit_tamper.py`, `test_audit_verify_content.py` |
| Automated tests, Internal Brain | **634** passing | `databridge/apps/internal_brain/tests/` |
| Web end-to-end tests (Playwright, including accessibility checks at desktop and phone width) | **38** | `databridge/apps/internal_brain_web/tests/e2e/` |

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
- **Hy3 has not been run live.** The TokenHub call is wired in and tested with a capturing test model,
  but the real-model run is waiting on Tencent account verification. All numbers above come from that
  capturing test model and the deterministic quoting path. Demo answers are quoted from the sources.
- **Sign-in is a demo login** standing in for SSO. A deployed build refuses to start without a passcode.
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
  step. The share of answers that cite the expected source is 87.8% to 96.4%.

## What was built here, and what was already there

The Walnut engine (provenance pointers, the sealed ledger, the grounding wall, the model harness) existed
before the event (base commit `078572e`) and is **reused unchanged**. Built during the hackathon, beside
it: the permission compilers, the two checks, the audit log and console, the web app, the mock
platforms, the Company A world, the fintech rules, the WorkBuddy connectors, the evaluation and the
original-screen configuration. **93% of the code is the original Walnut base**
(`python databridge/scripts/measure_original_share.py`: unchanged original lines / all code lines, 15 Oct).

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
| [Pitch deck](submission/07_deck/walnut_internal_brain_deck.pdf) | 23 slides: the problem, the idea, how it works, proof, the fintech layer, the rollout path |
| [Submission pack](submission/README.md) | Screenshots, CodeBuddy evidence, video, cards and deck in one folder |
| [DEV_PROCESS.md](docs/tencent/DEV_PROCESS.md) | How it was built: CodeBuddy sessions, reviews, what went wrong |
| [ARCHITECTURE.md](docs/tencent/ARCHITECTURE.md) | Components and the request path |
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
