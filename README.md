# Walnut DataBridge

**Answers you can prove.**

Walnut DataBridge gives staff at a regulated fintech one place to ask a question across Confluence,
Jira, Slack and Google Drive. **Internal Brain**, the ask product inside it, answers only from what the
asker may open, checks each source live, and writes every step to a tamper-evident audit log.

Tencent Cloud AI CAN DO IT Hackathon Singapore 2026 · FinTech track (Aspire) · Team Walnut, finalist.
This is the **finals build** (branch `final_v1.0`) for Demo Day, 3 Nov 2026. The judge's guide is
[INTERNAL_BRAIN.md](INTERNAL_BRAIN.md).

| Read this first | Fact |
|---|---|
| Data | Synthetic only: a made-up fintech, "Company A" (32 people; 209 Confluence pages, 251 Jira issues, 602 Slack threads, 81 Drive files) |
| Platforms | Mock servers that use the real APIs' shapes, permission models and webhooks. Not run against a live tenant |
| Tencent Hy3 | Wired in through TokenHub and tested with a capturing test model. **Live run pending** (TokenHub activation). Until then, answers are quoted from the sources, with the same checks, and every answer says so |

## 1. Try it in 5 minutes

**You need:** Python 3.13, Node 20, and on Windows Git for Windows (the launcher is a bash script).
Ports 3100, 8100, 8200, 8300 and 3300 must be free.

**One-time setup** (from `databridge/`; a few minutes, not counted in the 5):

```bash
# macOS / Linux
python3.13 -m venv .venv && .venv/bin/python -m pip install -r requirements-lock.txt
(cd apps/internal_brain_web && npm install)
```

```powershell
# Windows (PowerShell)
py -3.13 -m venv .venv; .venv\Scripts\python -m pip install -r requirements-lock.txt
cd apps\internal_brain_web; npm install; cd ..\..
```

**Start everything** (from the repo root, in Git Bash on Windows or any terminal on macOS/Linux):

```bash
bash databridge/scripts/demo_up.sh start --fresh
```

It starts five things in order and prints the URLs when ready
([`databridge/scripts/demo_up.sh`](databridge/scripts/demo_up.sh)):

| # | What | Port |
|---|---|---|
| 1 | Mock Slack, Confluence, Jira and Drive | 8200 |
| 2 | Internal Brain service (indexes the whole world on start) | 8100 |
| 3 | `--fresh` only: wipes `databridge/var/ib-final` and seeds about 300 labelled replay questions through the real pipeline (about 2 minutes) | |
| 4 | Walnut engine gateway (only the Walnut engine page needs it) and the original Walnut screens | 8300, 3300 |
| 5 | Web app: a production build, then `next start` | 3100 |

| Open | URL |
|---|---|
| The app (User mode; the switch top-left goes to Developer mode) | http://127.0.0.1:3100 |
| Developer mode | http://127.0.0.1:3100/dev |
| Presenter mode (press `P`, then `Space` for each step) | http://127.0.0.1:3100/compare?present=1 |

Sign in by picking a person. There is no password; the demo sign-in stands in for single sign-on.
`bash databridge/scripts/demo_up.sh status` shows the ports; `... stop` stops everything. Logs are in
`databridge/var/ib-final/logs/`. Without `--fresh`, an existing demo history is kept.

**Live Tencent AI.** Put `TOKENHUB_API_KEY=<key>` in `databridge/.env.ib.local` (git-ignored) and start
with `IB_FORCE_OFFLINE=0`. The chain is Hy3, then DeepSeek V4 Flash, then the quoted answer. This has
not yet run live (see the table above).

### Fallback: start each part by hand

If the launcher does not suit your machine, the original two-terminal path still works (from the repo
root, Git Bash):

```bash
# Terminal 1: mock platforms (:8200) + the Brain (:8100). Ready at "Uvicorn running on http://127.0.0.1:8100"
cd databridge && bash scripts/run_local_demo.sh
```

```bash
# Terminal 2: the web app on http://127.0.0.1:3100
cd databridge/apps/internal_brain_web && npm run dev
```

Pure Python and npm, no bash (PowerShell, from `databridge/`, one shell each):

```powershell
$env:PYTHONPATH="apps/mock_platforms"; .venv\Scripts\python -m mock_platforms --port 8200
$env:PYTHONPATH="apps/internal_brain;packages/core;packages/harness;packages/adapter;packages/retrieval"; $env:IB_DEMO_LOGIN="1"; .venv\Scripts\python -m databridge_ib
cd apps\internal_brain_web; npm run dev
```

These paths start no demo history (Analytics and Audit then hold only your own questions) and no engine
gateway. To look at the screens with no backend at all: `npm run dev:mock` in
`databridge/apps/internal_brain_web` (built-in mock data, clearly not live).

## 2. What judges can see

Every number on these pages comes from the backend. Organisation-wide views need the compliance role;
other people see a lock card that explains why.

| Mode | Page | URL | Sign in as | What it shows |
|---|---|---|---|---|
| User | **Ask** | `/` | anyone | One composer. Live stage events while the request runs, then a cited answer with `[n]` chips. A source opens a sheet with the passage, version and "checked live" time. Refusals are one constant text |
| User | **Compare** | `/compare` | anyone | Same question, two people (Amy vs Rachel on Velquarry); or one person, two questions (Ken: a real company vs a made-up one, marked "Identical responses") |
| User | **History** | `/history` | anyone | Your own questions by day; a row opens your request receipt |
| User | **Settings** | `/settings` | anyone | English / 简体中文, theme, text size, reduce motion, high contrast, replay tour |
| User | **Value** | `/value` | anyone | ROI calculator; every input is visible and the result is labelled an estimate |
| Developer | **Mission control** | `/dev` | anyone; numbers need `maya.compliance` | The pipeline with live p50 per stage, source freshness, ledger head, model status |
| Developer | **Journey** | `/dev/journey` | `maya.compliance` (any request) or yourself (own receipt) | Compliance: millisecond waterfall, Gate-2 verdicts with reason codes, three sealed records. Everyone else: a receipt of their own request |
| Developer | **Knowledge graph** | `/dev/graph` | anyone (try `jdoe`) | Everything you can open, as one WebGL graph (Jordan 867 items, Ken 24). Search, expand, a node panel with passage and lineage, a table view, and "Compare two people" as two separately signed-in graphs |
| Developer | **Walnut engine** | `/dev/brain` | `jdoe` or `maya.compliance` | The evidence engine underneath: entity graph (932 entities, 2,246 links, 4,710 pointers), time slider, provenance to the row, ontology. Read-only |
| Developer | **Analytics** | `/dev/analytics` | `maya.compliance` | Requests, answer rate, latency p50/p95, Gate-2 reasons, grounding drops, model fallback, from the verified ledger. Replay history is labelled; "Live only" removes it |
| Developer | **Audit** | `/dev/audit` | `maya.compliance` | Plain-English audit search, reveal-with-reason, **Verify chain**, and the **sandbox tamper demo**: attacks run on a copy of the log, the real log is re-verified and shown unmodified |
| Developer | **Simulator** | `/dev/sim` | `admin.it` | Change access at the source (revoke a channel, restrict a page, edit a runbook) and watch the next answer change |
| Developer | **Trust centre** | `/dev/trust` | anyone | Seven controls, each with what it is, why it matters, the framework mapping and the test that proves it |
| Presenter | **Presenter mode** | `/compare?present=1` | the script signs in for you | The 10-step Demo Day script on the keyboard (`Space` next, `B` recording, `R` reset, `L` 中文, `S` Stage theme), plus `/present` slides |
| Both | **Tours** | "Take the tour" in the header | anyone | User tour: 4 steps and a checklist (sign in, ask, open a source, Ken's identical refusals). Developer tour: 5 steps (pipeline, journey, graph, trust, analytics or the lock card) |

The pre-finals Map (`/map`) is still in the User menu. The old `/audit` and `/sim` addresses redirect to
the Developer pages.

## 3. Where the evidence is

[docs/finals/rubric/README.md](docs/finals/rubric/README.md) maps each of the 10 judging criteria to what
is seen live, what to open in the app, and the documents behind it.

| # | Criterion | Start with |
|---|---|---|
| 1 | Impact & Relevance | [IMPACT_SHEET.md](docs/finals/rubric/impact_feasibility/IMPACT_SHEET.md), [ROI_MODEL.md](docs/finals/rubric/impact_feasibility/ROI_MODEL.md), `/value` |
| 2 | Human-Centered Design | [PERSONAS_AND_JOURNEYS.md](docs/finals/rubric/hcd_ux/PERSONAS_AND_JOURNEYS.md), [HALLWAY_TEST_PROTOCOL.md](docs/finals/rubric/hcd_ux/HALLWAY_TEST_PROTOCOL.md) (test pending) |
| 3 | AI Interaction | [AI_USAGE_MAP.md](docs/finals/rubric/ai_innovation/AI_USAGE_MAP.md), the journey view |
| 4 | Technical Execution | [ARCHITECTURE_ONE_PAGER.md](docs/finals/rubric/technical/ARCHITECTURE_ONE_PAGER.md), [EVIDENCE_CARD.md](docs/finals/rubric/technical/EVIDENCE_CARD.md), [ARCHITECTURE.md](docs/tencent/ARCHITECTURE.md) |
| 5 | Feasibility | [SCALE_ARCHITECTURE.md](docs/finals/rubric/impact_feasibility/SCALE_ARCHITECTURE.md), [PILOT_PLAN.md](docs/finals/rubric/impact_feasibility/PILOT_PLAN.md), [COST_PER_ANSWER.md](docs/finals/rubric/impact_feasibility/COST_PER_ANSWER.md) |
| 6 | Demo & Storytelling | [RUN_SHEET.md](docs/finals/rubric/demo_story/RUN_SHEET.md), [PRESENTER_KEYS.md](docs/finals/rubric/demo_story/PRESENTER_KEYS.md) |
| 7 | Innovation & Creativity | [INNOVATION_ONE_PAGER.md](docs/finals/rubric/ai_innovation/INNOVATION_ONE_PAGER.md), Compare, the tamper demo |
| 8 | UX & Accessibility | [ACCESSIBILITY_STATEMENT.md](docs/finals/rubric/hcd_ux/ACCESSIBILITY_STATEMENT.md), `/settings` |
| 9 | Responsible AI & Ethics | [SYSTEM_CARD.md](docs/finals/rubric/responsible_ai/SYSTEM_CARD.md), [FRAMEWORK_CROSSWALK.md](docs/finals/rubric/responsible_ai/FRAMEWORK_CROSSWALK.md), `/dev/trust` |
| 10 | Overall Quality | [LEAVE_BEHIND.md](docs/finals/rubric/demo_story/LEAVE_BEHIND.md), [DEMO_RUNBOOK.md](docs/finals/rubric/technical/DEMO_RUNBOOK.md) |

The whole finals folder (plan, decisions, research, reviews, contracts, QA, screenshots) is indexed in
[docs/finals/README.md](docs/finals/README.md).

## 4. Numbers

Evaluation numbers come from [RESULTS.md](databridge/evals/internal_brain/RESULTS.md), run on
30 Oct 2026 03:27 SGT on the finals pipeline, and its summary
[eval_summary.json](databridge/apps/internal_brain_web/public/eval_summary.json) (`stale: false`).
They use a capturing test model and the quoted-answer path, not live Hy3.

| What | Result |
|---|---|
| Restricted content in any prompt, answer or response | **0 leaks** in 5,958 adversarial requests (2,979 probes × 2 answer paths; 95% upper bound 0.10% per request on each path) |
| "Restricted" vs "does not exist" | **0 differences** across 400 paired questions (control rebuild: 0) |
| Prompt injection planted on all four platforms | **0 hard failures** in 976 injection-relevant requests |
| Allowed sources reaching the answer step | **100%** on both paths; the answer cites the expected source in 87.8% (quoted) to 96.4% (model) |
| Eval canaries | A leaky configuration is caught; deny-everything scores 0% usefulness |
| Backend tests (Internal Brain) | **733 passed** (commit 8582f4b, 30 Oct 2026, Windows, Python 3.13) · from `databridge/`: `.venv/bin/python -m pytest apps/internal_brain/tests -q` (Windows: `.venv\Scripts\python`) |
| Web end-to-end tests (Playwright, axe checks) | 38 of 38 pre-finals tests passed earlier; the suite is now 23 spec files; full e2e run: see docs/finals/TEST_REPORT.md |
| Live QA against the real backend | 32 checks of page and persona, 11 bugs found and fixed, 4 open issues listed: [QA_LIVE.md](docs/finals/QA_LIVE.md) |

## 5. Honest limits

- **Hy3 has not run live.** Wired; live run pending TokenHub activation.
- **The platforms are mocks** with the real API shapes. The connector code would be the same against a
  real tenant, but it has not been run against one.
- **Demo history is replay.** About 300 questions were asked through the real pipeline by a script, with
  past dates, and are labelled `replay` in the ledger and the UI.
- **Sign-in is a demo login** standing in for SSO.
- **The audit log has a key-holder window**: records after the last off-host head publication can be
  rewritten by someone with the sealing key ([SECURITY.md](docs/tencent/SECURITY.md) §3). The tamper
  demo shows this row as "Not detectable: the stated window".
- **Open issues** from live QA (the simulator Reset revocation bug was fixed in 8582f4b) are
  in [QA_LIVE.md §4](docs/finals/QA_LIVE.md). The full list of limits is in
  [INTERNAL_BRAIN.md → Honest limits](INTERNAL_BRAIN.md#honest-limits).

## 6. Tencent tools we used

| Tencent tool | How we used it | Evidence |
|---|---|---|
| **CodeBuddy** | Six build sessions: drew the trust boundaries, built both WorkBuddy connectors and the token tool, wrote the model-chain tests, ran a security review, then fixed its own code after a Codex review | [DEV_PROCESS.md](docs/tencent/DEV_PROCESS.md), transcripts in `docs/tencent/evidence/`, screenshots in `submission/01_codebuddy/` |
| **WorkBuddy** | The Brain's second front door: three connectors (ask as Jordan, ask as Ken, compliance audit), each carrying its own signed token | [WorkBuddy guide](docs/tencent/workbuddy/README.md), screenshots in `submission/02_workbuddy/` |
| **Hunyuan Hy3** | The model behind CodeBuddy sessions CB1 and CB5; configured as the first model in the Brain's answer chain and as the audit search's fallback parser (rules run first, the model only when they recognise nothing); wired, not yet run live | CodeBuddy transcripts; `databridge/apps/internal_brain/databridge_ib/llm.py`, `audit/nl_llm.py` |
| **TokenHub** | The Brain's model gateway (Hy3, then DeepSeek as backup). Wired in and tested; live run pending activation | `llm.py`, `tests/test_llm_chain.py`, `tests/test_model_config.py` |
| **Lighthouse** | A hardened one-command deploy kit (HTTPS, passcode-gated demo, only ports 80/443 open); no live instance because Lighthouse was unavailable during the event | [deploy/lighthouse/](deploy/lighthouse/README.md) |
| **Miora** | Tencent's AI design agent made the cover art from our written brief; we added the title | `submission/06_cover/` |

## 7. Repository map

| Path | What is in it |
|---|---|
| `databridge/apps/internal_brain/` | The Internal Brain service (`databridge_ib`), port 8100 |
| `databridge/apps/internal_brain_web/` | The Walnut DataBridge web app (Next.js), port 3100 |
| `databridge/apps/mock_platforms/` | Mock Slack, Confluence, Jira and Drive, port 8200 |
| `databridge/evals/internal_brain/` | The leak and injection evaluation |
| `databridge/packages/`, `databridge/packs/` | The Walnut engine and its domain packs (used by import, not modified) |
| [`docs/finals/`](docs/finals/README.md) | The finals plan, decisions, research, reviews, rubric evidence, contracts, QA and screenshots |
| [`docs/tencent/`](docs/tencent/) | Architecture, security, responsible AI, and the MAS mapping |
| `submission/` | The 16 Oct submission: screenshots, video and deck |
| `deploy/lighthouse/` | The cloud deploy kit |

## Why this exists

The data a company needs already exists. Getting to it is the problem.

| | |
|---|---|
| **"The biggest obstacle to using advanced data analysis isn't skill base or technology; it's plain old access to the data."** | Harvard Business Review |
| **25%** of a team's time goes to searching for answers | Atlassian, *State of Teams 2025* (12,000 knowledge workers) |
| **5.3 hours** a week, per person, spent waiting on colleagues for information or recreating work that already exists | Panopto with YouGov, 2018 (1,001 employees) |
| **42%** of an organisation's know-how is held only by individual employees | same |
| **60%** of AI projects unsupported by AI-ready data will be abandoned through 2026 | Gartner, Feb 2025 |

In a fintech the problem has a second edge. An assistant that can read everything can leak everything,
and telling the wrong person that a suspicious-transaction report exists is a criminal offence. So the
answer has to be **useful and permission-safe at the same time**. That is what the Internal Brain does.

## One connected record, whatever the source or format

Underneath the Internal Brain is the **Walnut / DataBridge** engine. It turns an organisation's scattered
data into one connected, checkable record that every team can use.

| Step | What it does |
|---|---|
| **Connect** | Reads data where it sits: work tools (Confluence, Jira, Slack, Google Drive in this entry), SQL databases (PostgreSQL connector), data-lake and warehouse exports in open formats (Parquet, Arrow, CSV, JSON / JSONL), spreadsheets (Excel), documents (PDF, including scanned pages through OCR, Word, PowerPoint, HTML, Markdown, plain text, XML, YAML), email and mailboxes, and any system that offers an MCP server |
| **Understand** | Recognises the same customer, supplier, project or person across every source and links them into one knowledge graph |
| **Convert** | Serves that record in the shape each job needs: answers, reports, analytics tables, AI context and datasets |
| **Prove** | Every value points to the cell, line or page it came from; a value without that proof is refused |
| **Protect** | Runs inside your own environment with the model you choose. Each person sees only what their role allows, and every access is logged |

The Internal Brain is the permission-aware **answer layer** on that record, specialised for a fintech:
it adds live permission checks at the source, a tamper-evident audit log, the FinCrime compartment and
market-specific answers. Warehouses such as Snowflake or BigQuery connect through their MCP servers or
their open-format exports; a dedicated warehouse connector is on the roadmap, not in this repository.

---

# The Walnut engine

Walnut is an evidence layer for AI systems. Every value it emits carries a proof of where it came from,
and anyone can re-run that proof. A value with no proof is refused when it is created. It is not logged
as a warning or flagged for later review.

Most provenance in AI infrastructure today is lineage. It works at dataset level, is computed after the
fact, and is trusted because the vendor says so. Walnut's claim is narrower and checkable: provenance is
a property of each individual value, and an outsider can verify it by re-running the pointer.

The repository began as one product, a healthcare intake flow, and was then generalised. The engine now
knows no domain. Healthcare, finance and government are **packs**, declared in YAML, on top of the same
core. Where a claim is shown in one pack rather than built into the engine, the text says so.

## How it works

Each point below is a type or a test in this repository, and names the file that enforces it.

**Every value is a `GroundedValue` with a `SourcePointer`, or it does not exist.**
`packages/core/databridge_core/provenance.py` defines the pointer as a sealed Pydantic model
(`frozen=True`, `extra="forbid"`, `revalidate_instances="always"`). It has ten closed kinds, such as
`table_cell`, `doc_span`, `email_part`, `ocr_bbox` and `generated_claim`. Each kind has its own required
locator fields, checked when the object is built. A pointer with a missing field, or an extra field the
type does not declare, fails at the call site. It does not fail three layers later.

**A cell is one of four states, never a bare value.**
`Cell = Union[GroundedValue, Transformed, Withheld, Absent]`. Two states disclose a value and carry proof
of the source. Two are non-answers and carry proof of what produced the non-answer: a policy decision
(`Withheld.record`, naming the policy, the principal, the purpose and the decision event) or a snapshot
(`Absent.snapshot`, naming the source and the moment asked). A `Withheld` cell cannot carry a
`SourcePointer`, because that would vouch for something the reader never saw. `assert_withheld_disjoint`
checks that no field is both disclosed and withheld, and that a withheld field never leaks into an
`unresolved` list.

**Trust is a label on where a value came from, not a confidence score.**
`SourcePointer.trust` is `TRUSTED` or `UNTRUSTED`. The adapter that reads the source sets it from that
source's own contract. An operator-controlled config table is `TRUSTED`. An email body, a chat message,
an OCR'd page or anything a model wrote is `UNTRUSTED`, however accurate it is. Trust combines by
least-upper-bound (`trust_from_parents`): a value derived from ten trusted facts and one untrusted fact
is untrusted. A `generated_claim` (model output) is untrusted by construction and cannot be turned into
`TRUSTED` by wrapping it in an aggregation. `effective_trust` re-derives the label from ancestry at read
time, so a label written by code that skipped validation does not survive use.

**Verification returns one of eight states, never a boolean.**
In `packages/adapter/databridge_adapter/verdicts.py`: `VERIFIED`, `VERIFIED_AS_OF_CAPTURE`,
`DRIFT_BENIGN`, `DRIFT_MATERIAL`, `EXPIRED`, `VANISHED`, `FORBIDDEN`, `UNVERIFIABLE`. Each source falls
into a pinning tier: T0 native snapshot, T1 monotonic cursor, T2 current-addressable, T3 unversioned.
`DRIFT_BENIGN` is a capability an adapter must advertise before it can claim it.
`assert_verdict_advertised` refuses a source that reports a verdict it has no signal to compute.

**The graph closes facts; it never deletes them.**
State is bi-temporal. Every fact records when it was true in the world and when the system learned it.
A correction is a new row, and the old row is closed out, not overwritten. `GraphStore` exposes only
`current()` and `as_of()`, so no temporal predicate appears in application code.

**Identity resolution proposes candidates. A named human confirms.**
No pack's resolver can return a result that treats identity as settled. In
`packages/graph/databridge_graph/merge.py`, `requires_human_selection` is computed from each enabled
pack's declared `subject_kinds`, not from a hardcoded entity name. The healthcare pack's patient resolver
(`packages/intake/databridge_intake/resolve.py`) shows it: candidates are ranked and flagged, never
merged, and a name-only match with no matching identifier comes back with a note instead of being trusted.

**An adapter is five verbs, and natural language never crosses the boundary.**
`packages/adapter/databridge_adapter/port.py` defines `describe`, `snapshot`, `plan`, `resolve` and
`verify` as a runtime-checked `Protocol`. `assert_conforms` checks the full signature of every verb, not
only the name, because a name-only check lets an unusable adapter register. A model may propose a
request (`CapabilityRequest`) that a deterministic validator then accepts or rejects. A model may never
assign trust, decide a policy outcome or pick an identity candidate.

## Architecture

Four planes, each with one job and one failure mode. An evidence plane cuts through all of them.

```
BUSINESS PLANE       objects · actions · measures — actions are the only write path
MATERIALISATION      Materialisation Plan → Receipt (the plan hash is the dataset's identity)
STATE PLANE          bi-temporal graph — join / time-travel / per-attribute citation
CONNECTION PLANE     adapter fleet — one adapter per source, Core/Policy deterministic, Shell thin
                      ────────────────────────────────────────────────────
EVIDENCE PLANE       GroundedValue · SourcePointer(+trust) · verdict lattice · ledger
                      (cuts vertically through all four)
```

Each adapter has three parts:

- **Core** owns the driver, auth, pagination and provenance emission. It never runs a model.
- **Policy** runs the capability, grant, purpose and budget checks in-process. It never runs a model and
  never makes a per-cell network call.
- **Shell** turns an orchestrator's task into a validated request or an escalation. It holds no
  credentials and no driver handle. It is the only place a model runs inside the adapter fleet.

The design documents in [`docs/tencent/`](docs/tencent/) give the full reasoning, including which parts
(bi-temporal graphs, connector fleets) Walnut does not try to win on, and why per-value proof is the part
it does.

## Domain packs

`packs/healthcare/`, `packs/finance/` and `packs/government/` each declare their own node and edge
vocabulary, subject kinds and business rules in YAML. The engine (`packages/core`, `graph`, `harness`,
`convert`, `adapter`) refers to none of them. `test_no_domain_branching`
(`packages/graph/tests/test_no_domain_branching.py`) fails the build on any `if pack == "..."` in engine
code. A fourth pack is a YAML change plus fixtures, not a code change.

## What is built

This list is checked against the code. [`STATUS.md`](STATUS.md) is the current, line-by-line version and
is the source of truth.

**Working:**
- The provenance primitives (`packages/core`) and the four-cell-state model, with property tests for
  every pointer kind and every laundering path the reviews found.
- Four adapters: Postgres (T0/T1); a mailbox adapter over raw `.eml` files (T1/T3, everything it returns
  is `untrusted`); a document and OCR adapter (T3, no past to return to); and one adapter that wraps a
  pinned, sandboxed MCP server (everything `untrusted`, digest-pinned, quarantined if its description
  changes). A content-addressed, write-once capture store sits behind them, and `email_part` and
  `ocr_bbox` pointers resolve against it instead of the live source.
- A bi-temporal graph loaded from realistic synthetic estates: one finance, one government, each with tens
  of thousands of rows or nodes. Injection payloads are planted in both and contained. They can be cited
  and quoted, and they never reach a control-flow sink.
- A gateway (`/evidence`, `/trace`, `/ontology`, `/builder`, `/materialise`, plus the healthcare pack's
  `/intake` surface) and a frontend with a pointer inspector, an adapter communication graph and an
  ontology library.
- A data agent and plan compiler that produce a tabular dataset (Receipt, Parquet and Croissant 1.1) from
  a request over the finance graph. The compiler refuses plans on the wrong temporal axis or with a
  missing pointer for any cell.

**Not built:**
- **Incremental join.** The claim that a ninth source can join an eight-source graph with no rebuild has
  no test yet. It is the largest untested architectural bet in the repository.
- **Actions as the only write path.** The rule is declared but not enforced. No invocation path runs an
  action yet, and there is no `automation.mode` gate. Writes today go through pack-specific code. The
  healthcare pack's append-only ledger (`packages/intake/databridge_intake/ledger.py`) does enforce
  insert-only with database triggers and a staff-attestation gate, but that is one pack showing the
  pattern, not the engine enforcing it.
- **Postgres 19 with SQL/PGQ**, the intended production graph store. Everything runs on SQLite today.
- **Conformal risk certificates.** The `unresolved` list exists but has no error bound attached.

## Run the original Walnut platform

For the hackathon's Internal Brain, use [INTERNAL_BRAIN.md → Run it](INTERNAL_BRAIN.md#run-it). This
section starts the original Walnut gateway and frontend on ports 8000 and 3000.

```powershell
cd databridge
.\start-databridge.ps1        # frees ports 8000/3000, starts the gateway and the frontend
```

| URL | What it is |
|---|---|
| `http://127.0.0.1:8000/health` | Gateway health, and which LLM backend is in use |
| `http://localhost:3000` | The frontend: pack screens plus the platform screens |

```powershell
cd databridge
.venv\Scripts\python -m pytest -m "not integration"
```

Keep the marker. A plain `python -m pytest` hangs on a test that expects a local Ollama instance.

No API key is needed. In the original platform, setting `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` makes a
real model run the classification and extraction steps. The Internal Brain uses `TOKENHUB_API_KEY`
instead. Without a key, a deterministic backend runs the same code paths offline, and no test in the suite
needs a live model.

LLM access goes through `packages/harness` only. Nothing else in the repository imports a vendor SDK.
Routing is a config file, [`databridge/config/models.yaml`](databridge/config/models.yaml), so pointing
the system at another provider, or onto local models with no network, is a config change.

## Engine layout

```
databridge/
  packages/
    core/          GroundedValue / SourcePointer: the provenance primitives
    harness/       model-agnostic LLM layer; call sites name a task class, never a model
    convert/       document IR + the grounding wall (drops any extraction not verbatim in its source)
    graph/         the bi-temporal knowledge graph, pack loading, identity merge rules
    adapter/       the five-verb port, the three-part adapter fleet, the capture store
    agentdef/      agent declaration → compiled capability grant set
    materialise/   materialisation plan compiler, receipts, Croissant output
    agent/         a read-only agent loop over sealed adapters (screen → plan → read → compose → screen)
    intake/        the healthcare pack's product logic: classify → extract → ground → resolve →
                   eligibility → prefill → billing → ledger
    outbox, evals, ingest, semantic   supporting packages
  apps/
    gateway/       FastAPI backend: platform routes plus each pack's own routes   (:8000)
    frontend/      Next.js: platform screens plus each pack's own screens         (:3000)
    mcp_server/    the graph exposed to agents over MCP
    cli/           headless commands
  packs/
    healthcare/, finance/, government/    domain vocabulary and rules, YAML, no engine code
  fixtures/        synthetic estates and documents behind the packs (no real data)
```

## Where to look next

- [`INTERNAL_BRAIN.md`](INTERNAL_BRAIN.md): the judge's guide. The 10-minute walk-through, how to run
  it, how it works, and the evidence for every claim.
- [`STATUS.md`](STATUS.md): the current state of the build.
- [`docs/finals/README.md`](docs/finals/README.md): everything written for the finals.
- [`docs/tencent/`](docs/tencent/): architecture, security, responsible AI and the MAS mapping.

## Data

Every dataset in this repository (patients, insurers, account holders, case files, correspondence) is
synthetic. No real personal data is used to build, test or demonstrate this system.
