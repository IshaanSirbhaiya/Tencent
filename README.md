# Walnut Internal Brain

**Tencent Cloud AI CAN DO IT Hackathon Singapore 2026 · FinTech Track (Aspire): "The Internal Brain".**

Ask one question across Confluence, Jira, Slack and Google Drive, and get a cited answer built **only
from what you're allowed to open**. Every source is permission-checked twice before the AI sees it, and
every access is written to an audit log that can't be quietly edited.

- **0 leaks** of restricted content in 5,958 adversarial requests; **0 differences** between
  "restricted" and "doesn't exist" across 400 paired questions ([results](databridge/evals/internal_brain/RESULTS.md)).
- Built on the **Walnut evidence engine** below, reused unchanged (93% of the code is the original base).
- Built **with CodeBuddy**, reachable **from WorkBuddy**, and configured for **Tencent Hy3** via TokenHub (not yet run live; answers are direct quotes without a key).

**Judges: start with [INTERNAL_BRAIN.md](INTERNAL_BRAIN.md)**: what to look at first, how to run it in
two commands, how it works, and the evidence for every claim.

| Look at | Why |
|---|---|
| **Compare** (same question, two people) | Restrictions you can see at a glance |
| **Map** | Knowledge across four platforms, shaped by each person's permissions |
| **Audit → Verify audit chain** | Who asked what, every allow/deny decision, tamper detection |
| **Original Walnut screens** | The engine's knowledge graph, time slider and provenance chain on the same data |

---

# Walnut

Walnut is an evidence substrate for AI systems: every value it emits carries a re-executable proof
of where it came from, and a value with no such proof is refused when it is constructed — not
logged as a warning, not flagged for later review, refused.

Most "provenance" in AI infrastructure today is lineage: dataset-level, computed after the fact,
and trusted because the vendor says so. Walnut's claim is narrower and checkable: provenance as a
property of the individual value, verifiable by an outsider re-running the pointer, independent of
whether anyone trusts the system that produced it.

This repository was built as a single vertical product (a healthcare intake flow) and then
generalised. The engine now knows no domain — healthcare, finance and government are peer
**packs**, YAML-declared, sitting on top of the same core. Everything below describes the general
system; where a claim is demonstrated in one pack rather than built into the engine, that is said
explicitly.

---

## The mechanism

These are not design goals. Each one is a type or a test in this repository, and each claim below
names the file that enforces it.

**A value is a `GroundedValue` carrying a `SourcePointer`, or it does not exist.**
`packages/core/databridge_core/provenance.py` defines the pointer as a sealed Pydantic model
(`frozen=True`, `extra="forbid"`, `revalidate_instances="always"`) with ten closed kinds
(`table_cell`, `doc_span`, `email_part`, `ocr_bbox`, `generated_claim`, …), each with its own
required locator fields, checked by a validator that runs at construction. A pointer missing its
required fields — or a caller adding a field the type doesn't declare — fails at the call site,
not three layers downstream as an `AttributeError`.

**A cell is one of four states, never a bare value.**
`Cell = Union[GroundedValue, Transformed, Withheld, Absent]`. Two states disclose a value and carry
proof of the source; two are non-answers and carry proof of what produced the non-answer instead —
a policy decision (`Withheld.record: WithholdingRecord`, naming the policy, the principal, the
purpose and the decision event) or a snapshot (`Absent.snapshot`, naming the source and the instant
asked). Nothing is ungrounded; what differs is which kind of proof is the right one. A `Withheld`
cell cannot carry a `SourcePointer` — that would vouch for something the consumer was never shown —
and `assert_withheld_disjoint` enforces that no field is both disclosed and withheld, and that a
withheld field never leaks into an `unresolved` list by accident.

**Trust is a label on where a value came from, not a confidence score.**
`SourcePointer.trust: Trust.TRUSTED | Trust.UNTRUSTED` is stamped by the adapter that reads the
source, from that source's own contract — an operator-controlled config table is `TRUSTED`; an
email body, a chat message, an OCR'd document or anything a model wrote is `UNTRUSTED`, regardless
of how accurate it is. Trust propagates by least-upper-bound (`trust_from_parents`): a value derived
from ten trusted facts and one untrusted one is untrusted, and a `generated_claim` — model output —
is untrusted by construction and can never be laundered into `TRUSTED` by wrapping it in an
aggregation. `effective_trust` re-derives the label from ancestry at *read* time, so a label written
by code that bypassed validation does not survive being used.

**Verification returns an eight-state lattice, never a boolean.**
`packages/adapter/databridge_adapter/verdicts.py`: `VERIFIED`, `VERIFIED_AS_OF_CAPTURE`,
`DRIFT_BENIGN`, `DRIFT_MATERIAL`, `EXPIRED`, `VANISHED`, `FORBIDDEN`, `UNVERIFIABLE`. A source is
classified into a pinning tier (T0 native snapshot, T1 monotonic cursor, T2 current-addressable, T3
unversioned), and `DRIFT_BENIGN` is a capability an adapter must *advertise* before it can claim it
— `assert_verdict_advertised` refuses a source reporting a verdict it has no signal to compute.

**The graph invalidates; it never deletes.** State is bi-temporal — every fact carries both when it
was true in the world and when the system learned it — and a correction is a new row with the old
one closed out, not an overwrite. `GraphStore` exposes only `current()` and `as_of()`; no temporal
predicate appears in application code.

**Identity resolution produces candidates. A named human attests.** No pack's resolver can construct
a result that treats identity as settled. In `packages/graph/databridge_graph/merge.py`,
`requires_human_selection` is computed from each enabled pack's declared `subject_kinds` (which
entity kind may never be auto-merged, and what attestation a human must perform) rather than a
hardcoded entity name — the healthcare pack's patient resolver
(`packages/intake/databridge_intake/resolve.py`) is the concrete demonstration: candidates are
ranked and flagged, never merged, and a name-only match without a corroborating identifier is
returned with a note rather than silently trusted.

**An adapter is five verbs, and natural language never crosses the boundary.**
`packages/adapter/databridge_adapter/port.py` defines `describe / snapshot / plan / resolve /
verify` as a runtime-checked `Protocol`; `assert_conforms` checks the full signature of every verb,
not just that the name exists, because a Protocol only checking attribute presence lets an unusable
adapter register. A model may propose a request (`CapabilityRequest`) that a deterministic validator
then accepts or rejects; it may never assign trust, decide a policy outcome, or select an identity
candidate.

---

## Architecture

Four planes, each with one job and one failure mode, plus an evidence plane that cuts through all
of them:

```
BUSINESS PLANE       objects · actions · measures — actions are the only write path
MATERIALISATION      Materialisation Plan → Receipt (the plan hash is the dataset's identity)
STATE PLANE          bi-temporal graph — join / time-travel / per-attribute citation
CONNECTION PLANE     adapter fleet — one adapter per source, Core/Policy deterministic, Shell thin
                      ────────────────────────────────────────────────────
EVIDENCE PLANE       GroundedValue · SourcePointer(+trust) · verdict lattice · ledger
                      (cuts vertically through all four)
```

Each adapter is three processes, not one: a **Core** that owns the driver, auth, pagination and
provenance emission (never a model); a **Policy** layer that runs the capability, grant, purpose and
budget checks in-process (never a model, never a per-cell network round trip); and a thin **Shell**
that turns an orchestrator's task into a validated request or an escalation, holding no credentials
and no driver handle — the only place a model runs inside the adapter fleet.

Full reasoning for these decisions — including which of the individually-commoditised pieces
(bi-temporal graphs, ontology-as-control-plane, connector fleets) Walnut is *not* trying to win on,
and why the per-value proof is the piece nobody else ships — is in
[`Foundational Research/design/00_SPINE.md`](Foundational%20Research/design/00_SPINE.md).

---

## Domain packs

`packs/healthcare/`, `packs/finance/` and `packs/government/` each declare their own node/edge
vocabulary, subject kinds and business rules in YAML. The engine (`packages/core`, `graph`,
`harness`, `convert`, `adapter`) contains no reference to any of them — `test_no_domain_branching`
(`packages/graph/tests/test_no_domain_branching.py`) fails the build on an `if pack == "..."` in
engine code. Adding a fourth pack is a YAML change plus fixtures, not a code change to the packages
above it.

---

## What's built

This section is checked against the code, not aspirational. See `STATUS.md` for the current,
line-by-line version of this list — it moves faster than this file and is the source of truth.

**Solid:**
- The provenance primitives above (`packages/core`) and the four-cell-state model, with property
  tests exercising every pointer kind and every laundering path the reviews found.
- An adapter fleet with four working adapters — Postgres (T0/T1), a mailbox adapter over raw
  `.eml` (T1/T3, everything it returns `untrusted`), a document/OCR adapter (T3, no past to return
  to), and one adapter wrapping a pinned, sandboxed MCP server (everything `untrusted`, digest-pinned,
  quarantined on a description change) — plus a content-addressed, write-once capture store that
  `email_part` and `ocr_bbox` pointers resolve against instead of the live source.
- A bi-temporal graph loaded from real-shaped synthetic estates (a finance estate and a government
  estate, each tens of thousands of rows/nodes), with injection payloads planted in both and
  demonstrably contained — cited and quotable, never reaching a control-flow sink.
- A gateway (`/evidence`, `/trace`, `/ontology`, `/builder`, `/materialise`, plus the healthcare
  pack's own `/intake` surface) and a frontend exposing the pointer inspector, the adapter
  communication graph, and the ontology library.
- A data agent and plan compiler that produce a tabular dataset (Receipt + Parquet + Croissant 1.1)
  from a request over the finance graph, with the compiler refusing plans on the wrong temporal axis
  or missing a pointer for any cell.

**Not built — named rather than hidden:**
- **Incremental join.** The decentralisation claim — a ninth source joining an eight-source graph
  with no rebuild — has no test coverage yet. This is the single largest untested architectural bet
  in the repository.
- **Actions as the only write path.** The business plane's write discipline is declared, not
  enforced: there is no invocation path yet that runs an action and no `automation.mode` gate.
  Writes today happen through pack-specific code (e.g. the healthcare pack's append-only ledger,
  `packages/intake/databridge_intake/ledger.py`, which does enforce insert-only via database
  triggers and a staff-attestation gate — but that is a pack demonstrating the pattern, not the
  engine enforcing it generically).
- **Postgres 19 + SQL/PGQ**, the intended production graph substrate. Everything above runs on
  SQLite today; the Postgres path is designed, not scheduled.
- **Conformal risk certificates.** The `unresolved` list exists; it has no attached error bound yet.

---

## Running it (the original Walnut platform)

> For the hackathon's **Internal Brain** (ports 8100/8200/3100), use [INTERNAL_BRAIN.md → Run it](INTERNAL_BRAIN.md#run-it) instead. This section starts the original Walnut gateway and frontend on ports 8000/3000.

```powershell
cd databridge
.\start-databridge.ps1        # frees ports 8000/3000, starts the gateway and the frontend
```

| | |
|---|---|
| `http://127.0.0.1:8000/health` | gateway health, and which LLM backend is in use |
| `http://localhost:3000` | the frontend — pack surfaces plus the platform surfaces above |

```powershell
cd databridge
.venv\Scripts\python -m pytest -m "not integration"
```

The marker is required — a plain `python -m pytest` hangs on a test that expects a local Ollama
instance. No API key is needed: in the original platform, with `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` set, a real model runs
the classification and extraction steps (the Internal Brain uses `TOKENHUB_API_KEY` instead); without one, a deterministic backend runs the same code
paths offline, and no test in the suite depends on a live model.

LLM access goes through `packages/harness` only — nothing else in the repository imports a vendor
SDK directly — and routing is a config file (`databridge/config/models.yaml`), so pointing the whole
system at a different provider, or air-gapping it onto local models, is a config change.

---

## Repository layout

```
databridge/
  packages/
    core/          GroundedValue / SourcePointer — the provenance primitives
    harness/       model-agnostic LLM layer; call sites name a task class, never a model
    convert/       document IR + the grounding wall (drops any extraction not verbatim in its source)
    graph/         the bi-temporal knowledge graph, pack loading, identity merge rules
    adapter/       the five-verb port, the three-layer adapter fleet, the capture store
    agentdef/      agent declaration → compiled capability grant set
    materialise/   materialisation plan compiler, receipts, Croissant emission
    agent/         a read-only agent loop over sealed adapters (screen → plan → read → compose → screen)
    intake/        the healthcare pack's product logic — classify → extract → ground → resolve →
                   eligibility → prefill → billing → ledger
    outbox, evals, ingest, semantic   supporting packages
  apps/
    gateway/       FastAPI backend — the platform surfaces plus each pack's own routes  (:8000)
    frontend/      Next.js — the platform surfaces plus each pack's own screens          (:3000)
    mcp_server/    the graph exposed to agents over MCP
    cli/           headless commands
  packs/
    healthcare/, finance/, government/    domain vocabulary + rules, YAML, no engine code
  fixtures/        synthetic estates and documents backing the packs above (no real data — see below)
```

---

## Where to look next

- **`CLAUDE.md`** — the operating rules for this repository, including the hard constraints
  (identity is never auto-confirmed, no value without a pointer, no per-domain branching in the
  engine, a human confirms before anything is written).
- **`PHASE_PLAN.md`** — the build organised into phases, with a "done when" for each.
- **`STATUS.md`** — the current, honest state of the build: what's landed, what's in flight, what
  reviews found and what was done about it.
- **`Foundational Research/design/00_SPINE.md`** — the design authority: the decisions everything
  else follows from, and the evidence behind each one.

---

## Data

Every dataset in this repository — patients, insurers, account holders, case files, correspondence —
is synthetic. No real personal data is used to build, test or demonstrate this system.
