# Walnut Internal Brain

**Tencent Cloud AI CAN DO IT Hackathon Singapore 2026 · FinTech Track (Aspire): "The Internal Brain".**

Ask one question across Confluence, Jira, Slack and Google Drive. Get a cited answer built **only from
what you are allowed to open**. Every source is permission-checked twice before the AI sees it, and every
access is written to an audit log that cannot be quietly edited.

## The proof

- **0 leaks** of restricted content in 5,958 adversarial requests.
- **0 differences** between "restricted" and "does not exist" across 400 paired questions.
  Both results are in [the eval results](databridge/evals/internal_brain/RESULTS.md).
- **93%** of the code is the original Walnut base, reused unchanged. The Internal Brain is a new app on top of it.
- Built **with CodeBuddy** and reachable **from WorkBuddy**.
- Configured for **Tencent Hy3** through TokenHub. It has not yet been run live. Without a key, answers
  are direct quotes from the permitted sources.

## Tencent tools we used

| Tencent tool | How we used it | Evidence |
|---|---|---|
| **CodeBuddy** | Six build sessions: drew the trust boundaries, built both WorkBuddy connectors and the token tool, wrote the model-chain tests, ran a security review, then fixed its own code after a Codex review | [DEV_PROCESS.md](docs/tencent/DEV_PROCESS.md), transcripts in `docs/tencent/evidence/`, screenshots in `submission/01_codebuddy/` |
| **WorkBuddy** | The Brain's second front door: three connectors (ask as Jordan, ask as Ken, compliance audit), each carrying its own signed token | [WorkBuddy guide](docs/tencent/workbuddy/README.md), screenshots in `submission/02_workbuddy/` |
| **Hunyuan Hy3** | The model behind CodeBuddy sessions CB1 and CB5; first in the Brain's answer chain | CodeBuddy transcripts; `databridge/apps/internal_brain/databridge_ib/llm.py` |
| **TokenHub** | The Brain's model gateway (Hy3, then DeepSeek as backup). Wired in and tested; live calls wait on Tencent's account verification | `llm.py`, `tests/test_llm_chain.py`, `tests/test_model_config.py` |
| **Lighthouse** | A hardened one-command deploy kit (HTTPS, passcode-gated demo, only ports 80/443 open); no live instance because Lighthouse was unavailable during the event | [deploy/lighthouse/](deploy/lighthouse/README.md) |
| **Miora** | Tencent's AI design agent made the cover art from our written brief (two rounds of prompts, four variations each); we added the title | `submission/06_cover/`: the Miora screenshots, its raw image and the final cover |

## Where to click

Start with [INTERNAL_BRAIN.md](INTERNAL_BRAIN.md). It says what to look at first, how to run the app, how
it works, and gives the evidence for every claim.

| Look at | Why |
|---|---|
| **Compare** (same question, two people) | The restrictions are visible at a glance |
| **Map** | Knowledge across four platforms, shaped by each person's permissions |
| **Audit → Verify audit chain** | Who asked what, every allow and deny decision, and tamper detection |
| **Original Walnut screens** | The engine's knowledge graph, time slider and provenance chain on the same data |

## Quick start

The run commands live in one place: [INTERNAL_BRAIN.md → Run it](INTERNAL_BRAIN.md#run-it). The app uses
ports 8100 (service), 8200 (mock platforms) and 3100 (web).

## Repository map

| Path | What is in it |
|---|---|
| `databridge/` | The code: the Internal Brain app, the web app, the mock platforms, and the Walnut engine packages |
| [`docs/tencent/`](docs/tencent/) | Architecture, security, responsible AI, and the MAS mapping |
| `submission/` | Screenshots, video and deck |
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

- [`INTERNAL_BRAIN.md`](INTERNAL_BRAIN.md): the hackathon entry. What it is, how to run it, and the
  evidence for every claim.
- [`STATUS.md`](STATUS.md): the current state of the build.
- [`docs/tencent/`](docs/tencent/): architecture, security, responsible AI and the MAS mapping.

## Data

Every dataset in this repository (patients, insurers, account holders, case files, correspondence) is
synthetic. No real personal data is used to build, test or demonstrate this system.
