# PRD-06 — The Semantic Layer: Schema Understanding & Label Discovery
# Deep specification · Companion to 01_PRD.md, 02_TRD.md
# Scope: schema graph analysis, LLM annotation, confidence routing, human confirmation,
#        label discovery, the corrections flywheel
# This layer is THE MOAT. Both pillars consume its output. Its accuracy is survival (PRD §8).

---

## 1. What This Layer Is

The semantic layer converts raw schema metadata into a **versioned, human-confirmed, machine-consumable map of what an organisation's data MEANS**: every column gets a semantic type, every table a real-world concept and role, every relationship a name and cardinality, every business outcome usable as an ML label gets surfaced as a derivation.

Why it exists (the competitive logic, restated as engineering): everything else in the data ecosystem handles data **structurally** — Databricks gives you the lakehouse but you define meaning; feature stores serve features you defined; Palantir builds the semantic ontology but with consultants over 12–18 months at $5–20M. The semantic layer does that ontology-building automatically, in hours, with human confirmation instead of human construction. The difference between "automatic, confirmed" and "manual, constructed" is the entire business.

The accuracy bar is asymmetric: a wrong annotation that reaches production cascades — wrong semantic type → wrong feature engineering → silently broken model → two weeks of customer debugging → churn and reputation damage in a small market (PRD §8: FATAL severity). Hence the architecture below is built around **never letting a low-confidence guess travel without a human checkpoint**, and around making every human correction compound into future accuracy.

---

## 2. The Pipeline

```
SchemaExtract (Task 3 output: tables, columns, samples, stats, FKs)
   │
   ▼ STAGE A — DETERMINISTIC PRE-ANNOTATION (zero LLM; resolves 30–50% of columns)
   │   schema_graph_builder: NetworkX DiGraph → structural roles
   │   airtight rules: declared PK int → entity_id; declared FK → foreign_key;
   │                   100%-ISO8601 samples → timestamp_*; bool dtype → boolean_flag
   │   cheap classifiers (hints, not answers): currency-shaped decimals, email regex,
   │                   geo patterns, URL patterns, JSON-blob detection
   │
   ▼ STAGE B — PER-TABLE LLM ANNOTATION (task_class schema_annotation → sonnet-class)
   │   one call per table; prompt = structural hints + column metas + ≤10 sample rows
   │   + closed vocabulary + retrieved few-shots from corrections store (§6)
   │   response_model = AnnotatedTable (strict, L3 where backend supports)
   │
   ▼ STAGE C — CROSS-TABLE INFERENCE (task_class cross_table_inference → frontier-class)
   │   inputs: table-level summaries ONLY (concepts, roles, FK graph) — not raw columns
   │   outputs: relationship naming ("user PLACES order"), graph-shape detection
   │   ("accounts→transactions→accounts ⇒ bipartite financial graph ⇒ fraud-GNN viable"),
   │   LABEL CANDIDATES with derivation pseudocode (§5), domain classification
   │
   ▼ STAGE D — CONFIDENCE BLENDING + ROUTING (deterministic, §4)
   │   ≥0.95 auto-approve · 0.80–0.95 spot-check queue · <0.80 mandatory review
   │   optional second-model vote for the <0.80 band before burdening the human
   │
   ▼ STAGE E — HUMAN CONFIRMATION (CLI TUI now; web UI Stage 2)
   │   accept / edit per column; every edit recorded to corrections store
   │
   ▼ OUTPUT: AnnotatedSchema v{n}.json — append-only versions, diffable
            (consumed by: graph ingest_db, rule-engine proposer, SQL schema linking,
             ML templates' leakage detection, label discovery)
```

---

## 3. Data Model

```python
# packages/semantic/databridge_semantic/models.py
class ColumnAnnotation(BaseModel):
    column: str
    semantic_type: SemanticType            # closed enum from core (15 values + unknown)
    description: str                        # one sentence, human-readable
    is_pii: bool
    pii_kind: Literal["email","phone","name","national_id","address","none"] = "none"
    ml_role: Literal["feature","label_candidate","timestamp","identifier","ignore"]
    confidence: float                       # BLENDED (§4), not raw model self-report
    confidence_sources: dict                # {model: .9, structural_agreement: +.05, ...}
    provenance: Literal["deterministic","llm","llm+vote","human"]   # who decided

class TableAnnotation(BaseModel):
    table: str
    concept: str                            # "financial transaction", "support message"
    role: TableRole                         # entity|event|relationship|lookup|log
    columns: list[ColumnAnnotation]
    summary: str                            # ≤2 sentences; this is what Stage C sees

class RelationshipAnnotation(BaseModel):
    src_table: str; dst_table: str; via: str
    name: str                               # "user PLACES order"
    cardinality: Literal["1:1","N:1","M:N"]
    declared: bool                          # FK constraint vs heuristic-proposed
    confidence: float

class LabelCandidate(BaseModel):            # §5 — the P5 mechanism
    name: str                               # "fraud", "churn"
    outcome_description: str                # "customer disputed the transaction"
    derivation_sql: str                     # human-reviewable pseudocode/SQL
    source_tables: list[str]
    positive_rate_estimate: float | None    # from samples, if computable
    caveats: list[str]                      # "label lags event by ~30d (chargeback window)"
    status: Literal["proposed","confirmed","rejected"] = "proposed"

class AnnotatedSchema(BaseModel):
    source_id: str
    version: int                            # append-only; never mutate a version
    tables: list[TableAnnotation]
    relationships: list[RelationshipAnnotation]
    label_candidates: list[LabelCandidate]
    domain: str                             # "fintech", "ecommerce", ...
    created_at: datetime
    confirmed_by: str | None                # human principal; None = not yet usable
                                            # downstream consumers REFUSE unconfirmed
                                            # schemas in execute/infer modes (P6 enforced
                                            # at the type level, not by convention)
```

**The `provenance` field on every annotation** is the audit answer to "why does the system think this column is money?" — deterministic rule, LLM, LLM-with-vote, or human. It also drives eval slicing (E1 reported per provenance class shows exactly where the LLM earns or loses its keep).

---

## 4. Confidence: blended, never self-reported alone

LLM self-reported confidence is miscalibrated and gameable. The router consumes a **blend**:

```python
def blended_confidence(col, llm_out, hints) -> float:
    c = llm_out.self_confidence                       # base
    if hints.structural_prelabel == llm_out.semantic_type:
        c = min(1.0, c + 0.07)                        # structural agreement bonus
    if hints.dtype_compatible(llm_out.semantic_type):  # amount_money on numeric(12,2) ✓
        c = min(1.0, c + 0.03)                        #               ...on varchar ✗ penalty
    else:
        c -= 0.15
    if col.null_fraction > 0.6 and llm_out.semantic_type in REQUIRES_DENSITY:
        c -= 0.10                                     # sparse "entity_id" is suspicious
    if llm_out.semantic_type == "unknown":
        c = 0.0                                       # unknown ALWAYS routes to human
    return max(0.0, c)
```

For the <0.80 band, an optional **second-model vote** (cheap local model, same prompt) runs before human routing: agreement promotes to spot-check; disagreement stays mandatory-review with both answers shown. **Tradeoff:** vote costs tokens to save scarcer human attention — the right trade everywhere except airgapped-minimal deployments, where it's config-off.

Calibration is itself evaluated: E1 results bucketed by blended confidence must show monotonically increasing accuracy per bucket (reliability diagram in the eval scorecard). If the 0.9-bucket isn't ≥90% accurate, the blend weights are wrong — fix the blend, never the thresholds.

---

## 5. Label Discovery (P5 made mechanical)

The highest-leverage output for the ML buyer: surfacing ground truth that already exists as business outcomes. The Stage-C prompt frames it explicitly:

> "Identify columns, or conditions derivable across these tables, that record BUSINESS OUTCOMES usable as ML labels — disputes/chargebacks (fraud), cancellations + inactivity (churn), resolution + satisfaction scores (quality), missed payments (default). Output derivation logic as SQL-like pseudocode over the named tables/columns. Do NOT invent outcomes; if none exist, return an empty list. For each candidate, state the label's time-lag caveat if the outcome trails the event it labels."

Every candidate then passes **deterministic plausibility checks** before a human sees it: derivation parses (sqlglot); referenced tables/columns exist; estimated positive rate from samples is computed and sanity-bounded (a "fraud" candidate at 40% positive rate is flagged as probably-mislabelled logic). Confirmed candidates become first-class inputs to the ML templates (PRD-05 §7) — and the time-lag caveat travels with them, because training on labels inside their maturation window (e.g. transactions <30 days old can't have chargebacks yet) is a classic silent-leakage bug the system must refuse, not merely warn about.

**The honest-routing rule:** when no candidates exist for the customer's stated goal, the system says so and presents the three legitimate paths (weak supervision via rule aggregation, self-supervised reformulation, human annotation referral) — never an LLM-generated label. This sentence appears verbatim in customer-facing docs; it is a sales asset with the sceptical technical buyer, not an admission.

---

## 6. The Corrections Flywheel (the compounding moat)

Every human edit in Stage E is stored:

```python
class Correction(BaseModel):
    context_embedding_key: str       # embed(table_summary + column_meta) for retrieval
    input_context: dict              # column meta + hints + sample digest (PII-redacted)
    predicted: ColumnAnnotation
    corrected: ColumnAnnotation
    schema_domain: str
    created_at: datetime
```

Consumption, staged:
- **Now (POC):** dynamic few-shot — Stage B retrieves the K nearest corrections by context embedding and includes them as in-context examples. This is the same compounding mechanism as the SQL agent's confirmed-query library (PRD-05 §4.2): every correction makes the next annotation better, and corrections from fintech customer A measurably help fintech customer B (the schema-level network effect from the business case, implemented as a vector lookup).
- **Stage 3:** corrections become eval-set growth (every correction is by definition a case the model got wrong — gold for E1 hardening) and, at sufficient volume, fine-tuning data for a small local annotation model — which directly attacks the E6 local-parity gap where it matters most.

**Privacy boundary (hard line):** the corrections store contains metadata and redacted samples only, and is partitioned per-deployment by default. Cross-customer pooling of corrections is opt-in, contractual, and excludes anything sample-derived — the flywheel must never become the leak.

---

## 7. The Confirmation Experience (P6 as a designed surface, not a dialog box)

POC TUI (rich/prompt-toolkit), ordered for human efficiency:
1. **Schema overview first** — table list with roles/concepts and confidence colouring; the human calibrates on the forest before the trees.
2. **Review queues by band** — mandatory-review items first, grouped by table; spot-check queue samples 10% of the auto-approve band (the human checks the checker).
3. **Per-column card** — name, dtype, stats, redacted samples, hints, the LLM's answer + description, second-model vote if any, and single-keystroke accept/edit (edit = pick from the closed vocabulary; free-text only for description).
4. **Label candidates last** — derivation SQL shown verbatim with the plausibility-check results; confirm/reject/edit.
5. **Session summary** — N accepted / M edited / corrections recorded / schema version written. The TUI refuses to emit a `confirmed_by` schema if mandatory-review items remain.

Design intent: a competent data engineer confirms a 30-table schema in **under 25 minutes**. That number is a product KPI (it IS the demo: "give us one table, see annotated output in your environment in 48 hours" lives or dies on confirmation not being a chore), and it's measured — the TUI logs review time per item into PipelineEvents.

---

## 8. Evaluation (E1 deep spec)

Three held-out labelled schemas, deliberately spanning difficulty:
- **fintech-fixture** (clean, modern naming) — the floor; expect ≥92%.
- **oss-app** (real open-source schema, e.g. an Odoo/ERPNext subset) — realistic mid; gate ≥85%.
- **legacy-cryptic** (abbreviated columns: `acct_stat_cd`, `flg_ind_prcs`, `dt_efctv_strt`) — the honest stressor; REPORTED but not gated in POC; Stage-2 gate ≥75% with corrections-flywheel few-shots active.

Reported slices, every run: per-schema, per-semantic-type (confusion matrix — `amount_money` vs `amount_count` and `timestamp_created` vs `timestamp_event` are the known confusable pairs), per-provenance-class, per-confidence-bucket (calibration, §4), and per-profile (cloud vs airgapped = E6). Cost per schema (E7) on the same scorecard, because an accuracy win bought with 10× tokens is a different decision than a free one.

**Drift tripwire:** any merge touching prompts, vocabulary, blending weights, or few-shot retrieval REQUIRES an on-demand E1 run (CODEOWNERS + label rule from TRD Task 11); nightly runs catch what slips.

---

## 9. Failure Modes Register

1. **Plausible-but-wrong on cryptic schemas** (the FATAL-severity path) → legacy-cryptic eval slice keeps the weakness visible; confidence blend penalises dtype incompatibility; mandatory-review band is the backstop; corrections flywheel is the cure.
2. **Vocabulary pressure** ("just add a type for this weird column") → `unknown` + human description is the escape valve; vocabulary changes require an ADR because every consumer (L3 grammars, E1 sets, coercion menus, ML templates) is coupled to the closed set.
3. **Confirmation fatigue → rubber-stamping** → queues ordered by uncertainty, spot-check sampling of auto-approvals, review-time telemetry (a human averaging <2s/item on mandatory-review triggers a process flag, not silently degraded ground truth).
4. **Few-shot retrieval poisoning** (a wrong "correction" propagates) → corrections carry the correcting principal; a correction contradicted by a later correction on near-identical context is auto-flagged for adjudication; retrieval excludes flagged items.
5. **Sample non-representativeness** (50 rows miss the weird 1%) → stats (null fraction, unique counts, min/max) come from full-table aggregates, not samples — the LLM sees both and the prompt says which is which.
