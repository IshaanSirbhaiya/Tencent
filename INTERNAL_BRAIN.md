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

**Start here:** sign in, open **Compare**, and ask the same question as Jordan (engineer) and Ken
(contractor). Jordan gets the answer. Ken gets a polite "I couldn't find that", with no hint that more
exists. That one screen shows the three things Aspire asked for: knowledge across platforms,
restrictions per person, and an audit trail of both.

Everything runs on a synthetic fintech called **Company A** (32 people, 209 Confluence pages, 251 Jira
issues, 602 Slack threads, 81 Drive files). No real company or customer data is used anywhere.

**What's real and what's simulated.** The four platforms are mock servers that speak the real APIs'
shapes (their permission models, webhooks, errors and rate limits); we have not run against a live
tenant. Answers in the demo are quoted straight from the sources; Tencent Hy3 through TokenHub is wired
in and waiting on Tencent's account verification, and it would go through exactly the same checks.
**Built for this hackathon:** the permission compilers, the two checks, the audit log and console, the
web app, the fintech rules and the WorkBuddy connectors. **Reused unchanged:** the Walnut evidence
engine underneath (which is why 93% of today's code lines are the original base).

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
| **Ask** | anyone (try `jdoe`) | One cited answer from several platforms, a sources panel with each source's version and freshness, and a "Checks on this answer" strip |
| **Map** | anyone | Everything *you* can open, by platform and space. `jdoe` sees 867 items on 4 platforms; `ken.ext` sees 24 on 2 |
| **Compare** | anyone | The same question asked by two people, side by side |
| **Audit** | `maya.compliance` | Plain-English audit search, every allow/deny decision, a reasoned reveal of an answer, and **Verify audit chain** |
| **Sim** (change access) | `admin.it` | Change access live (remove someone from a channel, restrict a page, edit a runbook) and watch the next answer change |
| **Tour** | button in the header | A short guided walk through the screens above |
| **WorkBuddy** | three ready-made workspaces | Ask the Brain from Tencent WorkBuddy, as an engineer, a contractor, or the compliance officer |
| **Original Walnut screens** | separate launcher (below) | The Walnut engine's own views on Company A, built as `jdoe` sees it: the knowledge graph (932 items and people), the company brain with a time slider, the ontology and the provenance chain of every value, with Walnut's guided demo |

## Run it

**You need:** [Python 3.13](https://www.python.org/downloads/), [Node 20](https://nodejs.org/) and, on
Windows, [Git for Windows](https://git-scm.com/download/win) (for Git Bash). Ports 3100, 8100 and 8200
must be free.

One-time setup, from `databridge/`:

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

Then two terminals (Git Bash):

```bash
# 1 · mock Slack/Confluence/Jira/Drive (:8200) + the Brain (:8100)
cd databridge && bash scripts/run_local_demo.sh
```

```bash
# 2 · the web app on http://localhost:3100
cd databridge/apps/internal_brain_web && npm run dev
```

- The Brain indexes the whole world on start (about a minute). It is ready when terminal 1 prints
  `Uvicorn running on http://127.0.0.1:8100`. Then open http://localhost:3100 and pick a person: there
  is no password (the demo sign-in stands in for your company's single sign-on).
- Answers are deterministic quotes from the sources by default. To use Tencent Hy3, create
  `databridge/.env.ib.local` with `TOKENHUB_API_KEY=<your key>` and `IB_FORCE_OFFLINE=0` (the launcher
  reads that file); Hy3 then writes the answers, with `deepseek-v4.1-flash` as backup, through exactly
  the same checks. The same file holds `IB_TOKEN_KEY` / `IB_LEDGER_KEY` (32+ random characters each) if
  you want your own signing keys; without them the local demo uses built-in development keys.
- **Original Walnut screens** on Company A: `bash databridge/scripts/run_walnut_original.sh`, then open
  http://localhost:3300 (details in `databridge/apps/frontend/README.md`).
In the commands below, `PY` is `.venv\Scripts\python` on Windows and `.venv/bin/python` on macOS/Linux,
run from `databridge/`:

- **WorkBuddy:** `PY scripts/workbuddy_setup.py --user-config` adds three Internal Brain connectors to
  WorkBuddy (as Jordan, as Ken, and for compliance); restart WorkBuddy and press **Trust** on each
  (guide: `docs/tencent/workbuddy/README.md`).
- **Tests:** `PY -m pytest apps/internal_brain/tests -q`
- **Leak evaluation:** `PY -m evals.internal_brain.run` (about 1–10 minutes depending on the machine;
  add `--limit 60` for a one-minute check)
- **Tamper demo:** `PY scripts/tamper_demo.py`

Demo people (all fictional): `jdoe` backend engineer · `priya.sre` on-call SRE · `ken.ext` contractor
(Slack Connect guest) · `sara.sec` security · `leo.ops` product/ops · `amy.am` account manager ·
`mlro.tan` money-laundering reporting officer · `maya.compliance` compliance officer · `admin.it` IT admin.

## How it works

1. **Who's asking.** A signed token from the web app or WorkBuddy. No request body, tool argument or
   model output can name a person.
2. **Gate 1 (first check).** Search only documents this person may open, using each platform's own permission model
   (Confluence page restrictions, Jira project roles and issue security, Slack private and Connect
   channels, Drive inheritance). Ranking statistics come from those documents only, so restricted
   content can't even shift the results.
3. **Gate 2 (second check).** Ask the platform again, live, for every passage about to be used. An error, timeout or
   rate limit means no. A document that changed is re-read and re-checked.
4. **Answer.** The model sees only the passages that passed, and must cite one for every sentence.
   Walnut's grounding check drops any number or name that isn't in the evidence; if the answer fails, the
   Brain quotes the evidence instead.
5. **Record.** The audit log is written before retrieval, before the model call and before the answer is
   shown. If it can't be written, nothing is answered.

Diagrams: [the request path](docs/tencent/ARCHITECTURE.md) and
[trust boundaries](docs/tencent/diagrams/trust_boundaries.mmd) (drawn with CodeBuddy).

## Proof

| What | Result | Where |
|---|---|---|
| Restricted content in any answer, prompt or response | **0 leaks** in 5,958 adversarial requests (2,979 probes × 2 answer paths; 95% upper bound 0.10% per request) | `databridge/evals/internal_brain/RESULTS.md` |
| "Restricted" vs "doesn't exist" | **0 differences** across 400 paired questions | same |
| Prompt injection planted on all four platforms | **0 failures** | same |
| Recall for what people *are* allowed to see | **100%** of positive probes | same |
| Map identical with and without restricted documents | test | `tests/test_knowledge_map.py` |
| Audit log edits, deletions, truncation, rollback, edited stored answers | detected | `tests/test_audit_tamper.py`, `tests/test_audit_verify_content.py` |
| Automated tests, Internal Brain | **634** passing | `databridge/apps/internal_brain/tests/` |

The evaluation's own canaries prove it can fail: a deliberately leaky configuration is caught, and a
deny-everything configuration scores zero usefulness.

## Aspire's five scenarios

| # | Scenario | Try it | Proven by |
|---|---|---|---|
| 1 | One question, answered from several platforms, private channel omitted | Ask as `jdoe`: "What's the status of the database migration project and were there any blockers raised in Slack last week?" | `test_scenarios_e2e.py::test_canonical` |
| 2 | Fresh content is answerable quickly, citing the new version | Sim → "Edit failover runbook", then ask as `priya.sre` | `test_scenarios_e2e.py::test_s2_freshness_via_webhook` |
| 3 | A contractor gets the same refusal for a restricted report and an invented one | Ask as `ken.ext` about the Q3 breach report, then the Q7 one | `test_scenarios_e2e.py::test_s3_existing_vs_nonexistent_identical` |
| 4 | Access removed at the source is honoured on the very next question, even if the webhook is lost | Sim → "Remove jdoe from #db-migration", then ask again | `test_scenarios_e2e.py::test_s4_revocation_live`, `::test_s4_revocation_without_webhook` |
| 5 | Compliance reconstructs everything one person accessed in a space over 30 days | Audit as `maya.compliance`: "Show me everything jdoe accessed related to the payment-gateway space in the last 30 days" | `test_audit_query.py::test_s5_reconstruction` |

Test paths are under `databridge/apps/internal_brain/tests/`. The end-to-end tests run the real mock
platforms, the real connectors with signed webhooks, and the full pipeline over the generated world.

## The fintech layer

Added as data and configuration on top of the general system:

- **FinCrime compartment.** Suspicious-transaction case notes are readable only by the money-laundering
  reporting officer. An account manager asking about that customer gets the approved customer script,
  and the answer reads exactly like the answer for a company that doesn't exist (anti-tipping-off).
- **Market-specific answers.** "What can we offer this SME?" is answered per market (Singapore,
  Indonesia), each citing that market's approved policy.
- **Approved policies only.** A draft fee schedule with different numbers is never cited.
- **Card and ID masking.** Card numbers, CVVs, IBANs and NRICs are masked before indexing, so they never
  reach the index or a model.

Mapping to MAS TRM, the MAS AI Risk Management Guidelines, PDPA and PCI DSS, with limits:
[docs/tencent/MAS_TRM_MAPPING.md](docs/tencent/MAS_TRM_MAPPING.md).

## Built with Tencent

- **CodeBuddy** built the WorkBuddy connector, the token tool and the model-chain tests, drew the trust
  boundaries, and ran a security review on Tencent Hunyuan `hy3`; then it fixed its own code after a Codex
  review. Every session's transcript is kept: [docs/tencent/DEV_PROCESS.md](docs/tencent/DEV_PROCESS.md).
- **WorkBuddy** is the second front door, including an audit assistant for compliance.
- **TokenHub** (Singapore) serves Tencent Hunyuan `hy3`, with `deepseek-v4.1-flash` as backup.
- **Lighthouse:** a hardened deploy kit (Docker, HTTPS, passcode-gated demo) is in `deploy/lighthouse/`.
- **Miora** made the cover.

## Honest limits

- The platforms are **mocked** with their real API shapes and permission rules. The connectors are real
  code on the same path a real tenant would use, but have not been run against a real tenant.
- Sign-in is a **demo login** standing in for SSO. A deployed build refuses to start without a passcode.
- The grounding check verifies values and names, **not the relations between them**.
- The ledger detects tampering by anyone who doesn't hold its sealing key; beyond the last off-host
  published head, a key holder could rewrite records (see [SECURITY.md](docs/tencent/SECURITY.md) §3).
- The knowledge map re-checks every item live before listing it, so a big map takes a few seconds.
- The relevance gate answered 7 of the evaluation's expected-refusal questions from related pages the
  asker can open (no leak; listed in `RESULTS.md`).
- A real-model (`hy3`) evaluation run is pending Tencent account verification. All numbers above come
  from a capturing test model and the deterministic path.
- 5,958 requests are 2,979 probes, each run on both answer paths. Cited recall (the answer cites the
  expected source) is 87.8–96.4%; 100% is the share of allowed documents that reach the answer step.

## What was built here, and what was already there

The Walnut engine (provenance pointers, the sealed ledger, the grounding wall, the model harness) existed
before the event (base commit `4ab54d4`) and is **reused unchanged**. The Internal Brain, the mock
platforms, the web app, the Company A world, the evaluation and the original-screen configuration were
built during the hackathon, beside it. **93% of the code is the original Walnut base**
(`python databridge/scripts/measure_original_share.py`: unchanged original lines / all code lines, 15 Oct).

## Everything else

| Doc | What it covers |
|---|---|
| [Pitch deck](submission/07_deck/walnut_internal_brain_deck.pdf) | 13 slides: the problem, the idea, how it works, proof, the fintech layer, the rollout path |
| [Submission pack](submission/README.md) | Screenshots, CodeBuddy evidence, video, cards and deck in one folder |
| [DEV_PROCESS.md](docs/tencent/DEV_PROCESS.md) | How it was built: CodeBuddy sessions, reviews, what went wrong |
| [ARCHITECTURE.md](docs/tencent/ARCHITECTURE.md) | Components and the request path |
| [SECURITY.md](docs/tencent/SECURITY.md) | Threat model, ledger attacker model, injection, every review finding and its fix |
| [RESPONSIBLE_AI.md](docs/tencent/RESPONSIBLE_AI.md) | Grounding, abstention, citations, oversight, evaluation |
| [MAS_TRM_MAPPING.md](docs/tencent/MAS_TRM_MAPPING.md) | Regulatory mapping, with limits |
| [DATA_FLOWS.md](docs/tencent/DATA_FLOWS.md) | What leaves the system and where state lives |
| [RESULTS.md](databridge/evals/internal_brain/RESULTS.md) | The leak and injection evaluation |
| [SUBMISSION.md](docs/tencent/SUBMISSION.md) | The submission form answers |
| [README.md](README.md) | Walnut, the engine underneath |
