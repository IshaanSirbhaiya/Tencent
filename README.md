# Walnut

**An AI front-desk copilot for Parkway Shenton clinics.** Hack4Health 2026, Technical Track
(Microsoft Copilot Build Challenge).

A patient's referring document — an insurer referral letter, a health-check voucher, an
underwriting memo, a TPA or government authorisation form, an HR email, or nothing at all for a
walk-in — is read **before** they reach the registration counter. Walnut classifies it, extracts a
normalised intake record in which **every field points back to the span of the source it came
from**, resolves the patient against the clinic registry, determines coverage and package from a
config table, pre-fills the correct questionnaire, drafts the billing determination, and hands the
front desk **one summary card to verify and confirm**.

Today that work is ~23–32 minutes of admin per patient, done sequentially at one counter while a
queue forms. Measured against the ten fixture documents in this repo: a **27.5 min** baseline,
**16.3 min saved per patient (59%)**, roughly **11 hours** across a 40-patient morning. Those
figures are recomputed from the corpus by `packages/intake/tests/test_submission_figures.py`, not
asserted in prose.

---

## The four rules this system will not break

These are not aspirations. Each is enforced in code, and each has tests whose job is to fail if it
stops being true.

1. **Identity verification is never automated.** Not for a demo, not behind a confidence score.
   Resolution returns *candidates*; a named human selects one after checking the document against
   the person, in person. `ResolutionResult.requires_human_selection` is `Literal[True]` — there is
   no code path that sets it `False`.
2. **No value is emitted without a source pointer.** Every value is a `GroundedValue` carrying a
   `SourcePointer`. If it is not in a document or the registry it is `None` and its path is named
   in `unresolved` — never guessed, inferred or defaulted. The grounding wall drops any extracted
   value that does not appear in the source, and graph nodes and edges refuse construction without
   one.
3. **No per-issuer branching.** Insurers, packages, tests and billing rules live in
   `coverage_rules.yaml`. Adding an insurer is a YAML change with zero code changes, enforced by
   `test_no_issuer_branching`.
4. **A human confirms before anything is recorded.** The evidence ledger is append-only — SQLite
   triggers reject `UPDATE` and `DELETE`; corrections are new rows. Confirmation refuses without a
   staff attestation.

**All data in this repository is synthetic.** Every patient, insurer and document is fictional.

---

## Start here

| If you want to… | Read |
|---|---|
| Understand the whole project, end to end | [`docs/hackathon/PROJECT_CONTEXT.md`](docs/hackathon/PROJECT_CONTEXT.md) |
| Work on the code | [`CLAUDE.md`](CLAUDE.md) — the operating rules — then [`REMEDIATION.md`](REMEDIATION.md) |
| Know where the build stands | [`STATUS.md`](STATUS.md) |
| See what is planned | [`PLAN.md`](PLAN.md), [`TASKS.md`](TASKS.md) |
| Try it yourself | [`RUNNING.md`](RUNNING.md), then [`TESTING.md`](TESTING.md) — 66 trials, 63 of them executed against the live system — or [`TRY.md`](TRY.md) for copy-paste commands |
| Record the demo video | [`SCRIPT.md`](SCRIPT.md) — shot by shot, with the command backing every claim |
| Move it to Microsoft Copilot Studio | [`docs/hackathon/COPILOT_STUDIO_PORTABILITY.md`](docs/hackathon/COPILOT_STUDIO_PORTABILITY.md) for the mapping, [`docs/hackathon/COPILOT_TRANSITION_GUIDE.md`](docs/hackathon/COPILOT_TRANSITION_GUIDE.md) for the order of work |

`PROJECT_CONTEXT.md` §11 lists the places where two documents in this repo disagree, and which to
trust. That list is kept rather than tidied away — a repo that hides its own inconsistencies is
harder to trust than one that names them.

---

## Running it

```powershell
cd databridge
.\start-databridge.ps1        # frees ports 8000/3000, starts the gateway and the frontend
```

| | |
|---|---|
| <http://localhost:3000/frontdesk> | the front-desk summary card — the thing to look at first |
| <http://127.0.0.1:8000/health> | gateway health, and which LLM backend is in use |

```powershell
cd databridge
python -m pytest -m "not integration"      # the filter matters; see CLAUDE.md
```

**No API key is needed.** With `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` set, a real model is used;
without one, a deterministic backend runs the entire demo and the entire test suite offline. No
test depends on a live model.

---

## Layout

```
databridge/
  packages/
    core/      GroundedValue / SourcePointer — the provenance primitives
    harness/   model-agnostic LLM layer; call sites name a task, never a model
    convert/   document IR and the grounding wall
    graph/     bi-temporal clinic knowledge graph
    intake/    the product: classify → extract → ground → resolve → eligibility
               → prefill → billing → ledger → metrics
    agent/     a read-only clinic agent (built, deliberately not yet wired)
    evals/     eval suites
  apps/
    gateway/   FastAPI — /intake, /evidence, /intelligence, /brain   (:8000)
    frontend/  Next.js — /frontdesk, /intake, /evidence, /impact     (:3000)
    mcp_server/ the clinic graph exposed to agents over MCP
```

Walnut is built on **DataBridge**, a data-conversion and evidence-ledger POC. DataBridge is the
engine; Walnut is the product. The provenance model, the bi-temporal graph, the grounding wall and
the model-agnostic harness are reused as they were.

LLM access goes through `packages/harness` only, and routing is configuration
(`databridge/config/models.yaml`) — so moving to Azure OpenAI for the Copilot Studio story is a
config change, not a rewrite.

---

## State of the build

Phases 0–6a and 6.5 are complete and Phase 7 is largely built. The current block of work is
**fixing review findings, not adding features**: six work tracks shipped in parallel and every one
came back with Critical findings, which turned out to be five recurring *shapes* rather than
eighteen separate bugs. [`REMEDIATION.md`](REMEDIATION.md) is the ledger of what is open, in the
order to fix it.

Two things are designed and not yet built: a real agent behind `/intelligence`, and MCP servers
through a self-hosted aggregator. Both are blocked on safety work that has to land first — putting
an agent that could inherit an identity decision onto the surface judges are meant to look at is
the wrong order.

Known limitations are stated plainly in `STATUS.md` under "Accepted limitations (say these, don't
hide them)". There is no authentication behind `staff_id`, the ledger defaults to in-memory, and
identity documents are deliberately never read.
