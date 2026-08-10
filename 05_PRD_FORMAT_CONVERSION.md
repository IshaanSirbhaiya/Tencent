# PRD-05 — Data Format Conversion Pillar: The Compiler
# Deep specification · Companion to 01_PRD.md and 02_TRD.md
# Scope: router, rule engine, code/SQL agents, grounded generation, pipeline artifacts, validators

---

## 1. What This Pillar Is

A conversion engine that transforms data between any source and target format, **routed by mechanism, not by wishful generality**. The foundational insight: the three conversion directions are three different engineering problems with different failure modes, and must never share a code path:

| Direction | Mechanism | Failure mode | Guard |
|---|---|---|---|
| Structured → Structured | Declarative rules (JSONata / SQL), zero LLM at execution | type coercion bugs, schema drift | typed coercion table, schema-hash pinning, round-trip tests |
| Unstructured → Structured | Code agents generating SQL/parsers with self-correction | extraction errors, hallucinated fields | execution validation, source-grounding check, Pydantic enforcement |
| Structured → Unstructured | Grounded LLM generation (slot-filling) | hallucination | provenance completeness (E2), distribution checks (E3), adversarial verifier |

The shared principle across all three: **the LLM controls syntax; the source data controls semantics.** Any value in any output traceable to no input location is a defect (P4).

---

## 2. The Router

```python
# packages/convert/databridge_convert/router.py
class TargetSpec(BaseModel):
    family: Literal["structured", "text", "ml_dataset"]
    format: str            # "parquet" | "json:<schema_ref>" | "csv" | "sft:sharegpt"
                           # | "sft:openai" | "features:tabular" | "graph:pyg" | "rag:chunks"
    schema_ref: str | None # registered target schema (JSON Schema / Pydantic / SQL DDL)
    options: dict = {}

class ConversionRequest(BaseModel):
    source_ref: str        # registered source or graph query result handle
    target: TargetSpec
    mode: Literal["plan", "execute", "infer"]   # plan→human review; infer→single record

def route(req: ConversionRequest, src_meta: SourceMeta) -> Subsystem:
    if src_meta.shape == "structured" and req.target.family == "structured":
        return RULE_ENGINE
    if src_meta.shape == "unstructured" and req.target.family == "structured":
        return EXTRACTION_AGENT          # doc/text → records (Pydantic path)
    if src_meta.shape == "structured" and req.target.family == "text":
        return GROUNDED_GENERATOR
    if req.target.family == "ml_dataset":
        return ML_TEMPLATES              # composes the above + ML-specific logic (§7)
    raise UnroutableConversion(req)      # loud, never best-effort (P3 corollary)
```

**Decision:** `mode` is part of the request because the three lifecycles differ: `plan` produces a reviewable artifact draft (P6 — new mappings need human eyes), `execute` runs a confirmed artifact over bulk data, `infer` applies it to one record on the serving path. One request type, three modes — this is what makes training-serving consistency a property of the API rather than a discipline.

---

## 3. Subsystem A — Rule Engine (structured → structured)

### 3.1 The propose → validate → confirm → execute lifecycle

```
source AnnotatedSchema + target schema
   │
   ▼ (LLM once, task_class sql_generation-adjacent: "rule_proposal")
PROPOSER: emits MappingDraft{language: jsonata|sql, expr, coercions[], assumptions[]}
   │   prompt receives: annotated source columns (semantic types!), target field specs,
   │   the coercion catalogue (§3.3) as the ONLY permitted transforms, and instruction:
   │   "if a target field has no defensible source, emit it in `unmappable` — never guess"
   ▼
VALIDATOR (deterministic):
   syntactic: parse expr via jsonata lib / sqlglot          → reject on parse error
   semantic:  run on 50-row samples →
              output validates against target schema (types, required, enums)
              E3: zero invented categorical values; KS sanity on 1:1 numeric maps
              row-count law: |output| == f(|input|) per declared cardinality
   ▼
HUMAN CONFIRM (only for new mappings; library hits skip to execute)
   ▼
ENGINE: DuckDB executes (SQL natively; JSONata via embedded runtime over Arrow batches)
   ▼
ARTIFACT: serialised, versioned, hash-pinned (§6)
```

**Why two languages (and exactly two):** JSONata for record-shaped/JSON reshaping (its filter/map/reduce/higher-order functions cover nesting elegantly); SQL-on-DuckDB for relational work (joins, window aggregations, group-bys) where JSONata is miserable. The proposer chooses by target shape. Both are **declarative text** — diffable in review, replayable forever, zero-token to re-run. Adding a third language requires an ADR; language sprawl is how transformation logic becomes unauditable.

### 3.2 Why the LLM proposes but never executes

Execution determinism is the entire value: same artifact + same input bytes ⇒ same output bytes (the determinism test in Task 9 evals). An LLM in the execution path would make every run a fresh roll of the dice and every audit question unanswerable. The 2025 schema-mapping literature converged on exactly this division (LLM generates JSONata, library validates, engine executes) — we adopt it as law, not preference.

### 3.3 The coercion catalogue (the edge-case zoo, centralised)

```python
# packages/convert/databridge_convert/rules/coercions.py
# EVERY transform a mapping may use is a NAMED, TESTED function here.
# Mappings reference coercions by name → the artifact records exactly what ran.
COERCIONS = {
  "str_to_decimal":        ...,  # handles "1,234.56" AND "1.234,56" via locale param
  "epoch_to_iso":          ...,  # s vs ms vs µs detected by magnitude, logged
  "iso_normalise":         ...,  # tz-aware → UTC; naive → policy param (reject|assume_utc)
  "sentinel_to_null":      ...,  # -999, "N/A", "", "NULL", "null" → None, per-column list
  "bool_coerce":           ...,  # {Y,N,1,0,true,false,T,F} with explicit mapping table
  "enum_map":              ...,  # source code → target enum, REQUIRES full value table;
                                 #   unseen value at execute time → quarantine row, never guess
  "currency_minor_units":  ...,  # cents↔major with currency-aware decimals (JPY=0, BHD=3)
  "id_cast":               ...,  # numeric-string ids preserved as strings (leading zeros!)
}
```
**Reasoning:** scattering coercion fixes across mappings is how the same locale bug gets fixed five times and shipped broken a sixth. Centralised + named means tested once, audited by name, and the proposer's action space is a menu (closed set — the recurring DataBridge pattern).

**Schema drift policy (hard line):** artifact pins `source_schema_hash`. Execute-time mismatch ⇒ hard stop + structured diff report (columns added/removed/retyped) + suggested artifact migration. Never best-effort on drift: silent drift is the mechanism by which production models rot (training-serving skew, PRD §2). Quarantine, don't drop: rows failing coercion at execute time land in `quarantine.parquet` with reason codes; the run report shows quarantine rate; a configurable threshold (default 1%) fails the run.

---

## 4. Subsystem B — Extraction & SQL Agents (unstructured → structured)

Two distinct paths under one subsystem:

### 4.1 Document/text → records (the Pydantic extraction path)

```
DocumentRecord → (optional deterministic pre-parse: markdown tables, key-value
                  patterns, regex-able fields — P3 first, often 30–60% of fields free)
              → harness.complete_structured(response_model=TargetRecordModel, strict)
              → GROUNDING VALIDATOR (deterministic):
                  every extracted value must appear in source text
                  (exact for ids/numbers after canonical formatting;
                   fuzzy ≥0.9 token-set ratio for names/free text)
                  fail ⇒ field nulled + flagged, or record quarantined if required-field
              → cross-field consistency rules from target schema annotations
                  (start≤end dates, amount≥0, enum membership)
              → per-field confidence → P6 routing (auto / spot-check / review)
```
**The grounding check is the load-bearing wall:** an extracted value absent from the source text was invented, full stop. This single deterministic check converts the dominant LLM-extraction failure mode (plausible fabrication) into a visible, countable event. E2 logic reused.

### 4.2 Text → SQL (Stage 2, T17 — designed now because the semantic layer changes its odds)

```
question + AnnotatedSchema
   ▼ SCHEMA LINKING (deterministic + cheap-LLM): select only relevant tables/columns
     — our annotations make this unusually strong: semantic types + table roles +
     relationship names mean linking operates on MEANING, not name string-matching.
     (Literature: tight schema context is worth +15–20% accuracy by itself.)
   ▼ GENERATE (task_class sql_generation) with dynamic few-shot:
     embed question → retrieve K confirmed past (question, SQL) pairs from the
     mapping library → in-context. The library compounds: every confirmed query
     makes the next one better (same flywheel as annotation corrections).
   ▼ EXECUTE against the database (read-only role, statement timeout, row cap)
   ▼ FIXER loop (≤2): syntax/runtime errors → error text fed back verbatim
   ▼ REVISER loop (≤1, task_class sql_revision): semantic checks —
     result shape vs question intent (asked for "per merchant" but no GROUP BY?),
     suspicious cardinalities (0 rows on a question implying existence),
     aggregate sanity (NULL-heavy outputs)
   ▼ result + the SQL itself returned; SQL is the provenance (derived pointer
     whose derivation field IS the query text)
```
**Gate honesty:** SOTA on BIRD is 81.67% execution accuracy vs 92.96% human; our E5 gate is ≥70% on customer-like schemas — simpler schemas than BIRD, but we hold the gate until measured, and the number ships in customer docs. Overclaiming text-to-SQL reliability is the fastest way to lose the technical buyer (PRD GTM: the buyer is sceptical and will test).

**Safety rails (non-negotiable):** generated SQL runs as a read-only DB role; `EXPLAIN` cost ceiling before execution; denylist (no DDL/DML even if the role would somehow permit); every executed query logged as a PipelineEvent.

---

## 5. Subsystem C — Grounded Generator (structured → unstructured)

Full architecture specified in TRD Task 10; this section records the deeper logic and the edge-case ledger.

### 5.1 The two paths, restated as a rule
- **Real-log formatting** (support logs → SFT pairs): reformatting, zero generation, prefer ALWAYS when logs exist. Quality filtering is deterministic proxy signals (resolution flag, CSAT≥4, ≥min_turns, no policy-violation flags).
- **Synthesis** (augmentation/gap-filling): slot-filling only. The generator's prompt receives facts as `[F1]..[Fn]` GroundedValues; output contract requires `facts_used` per turn; the deterministic validator cross-checks every number/date/name in content against cited facts; the adversarial verifier (different model, task_class verification_pass) judges claim support; anything unsupported ⇒ quarantine with reason.

### 5.2 Deterministic formatters — the unsung dependency
String-matching facts in generated text only works if facts render deterministically: `$127.50 SGD`, `9 May 2026`, names title-cased once. `formatters.py` (sibling of coercions.py) owns canonical rendering; the generator is instructed to use rendered forms verbatim. Without this, the E2 validator drowns in false negatives ("May 9th" vs "9 May") and someone "fixes" it by loosening matching — which is how fabrications start slipping through. The formatter layer is hallucination defence, not cosmetics.

### 5.3 Edge-case ledger (each has a test)
- Fact value is a substring of another fact ("$12" inside "$127.50") → matching is longest-match-first over rendered forms.
- Model paraphrases a number ("about a hundred") → validator treats any numeric token not exactly matching a cited rendered fact as violation; "about/approximately" + number = violation (training data must be exact).
- Absent-fact temptation (the adversarial suite's core): question implies a field not in facts → pass = "let me check that for you"; any value = fail.
- Near-duplicate synthetic pairs → MinHash dedup at 0.9 Jaccard before output.
- Distribution drift in synthetic conversations (every customer angry, every amount round) → E3 extended: turn-length distribution, sentiment proxy, amount-distribution KS vs source — synthetic sets failing drift checks are flagged for mix-rate reduction.

---

## 6. Pipeline Artifacts — the training-serving-skew killer

```
artifact/{id}/
├── manifest.json     # artifact_id, created_at, subsystem, source_schema_hash,
│                     # target_schema_ref+hash, databridge_version, eval scorecard ref
├── transform/        # mapping.jsonata | query.sql | extraction_model_spec.json
├── coercions.json    # named coercions + params actually used
├── fitted/           # Stage 3: scalers, encoders, vocab maps (pickle + spec json)
└── lineage.json      # input PipelineEvent hashes → reproducibility chain
```
Invariants:
1. `infer` mode loads an artifact and applies the IDENTICAL transform to one record — the serving path exists from day one (TRD Task 9) even before anyone serves.
2. Artifacts are immutable; changes create a new version with `supersedes`. A model registry entry pins an artifact version; serving with a mismatched pair is a refusable error, not a warning.
3. Replay determinism is an eval (same artifact + same input bytes ⇒ byte-identical output), run in CI on every artifact-touching change.

**Why this is product, not plumbing:** training-serving skew is a leading silent killer of production ML (PRD §2). Every competitor leaves transform parity as customer discipline; we make it a load-bearing artifact. It is also the lock-in mechanism (PRD business case): the artifact sits in the inference path.

---

## 7. ML Dataset Templates (Stage 3, composed from the above)

- **Tabular features:** rule-engine SQL for joins + temporal aggregations (rolling windows computed `as_of` the example's timestamp — the no-future-leakage law); leakage detector flags features with |corr|>0.95 to label and any timestamp-after-label-event columns (the semantic layer's timestamp_* annotations make this detectable mechanically); time-based splits ONLY for temporal data; class-imbalance report with recommended `scale_pos_weight`.
- **GNN export:** AnnotatedSchema's entity/relationship roles → PyG `HeteroData` (node feature matrices per entity table via the tabular path; FK/junction tables → typed `edge_index`; event timestamps → edge attrs for temporal GNNs); masks generated from the same time-based split logic.
- **SFT:** Subsystem C output in ShareGPT + OpenAI formats from one internal form.
- **RAG chunks:** DocumentRecords → chunker (recursive default; semantic optional) → metadata-rich chunks (source pointers as metadata!) → embedding via harness → pgvector/FAISS export; companion eval-set generator produces (question, context, ground_truth) triples FROM THE SAME documents for RAGAS-style evaluation — same-source consistency is the tokenizer principle applied to RAG eval.
- **Label discovery hook (P5):** templates consume label candidates from the semantic layer (chargeback⇒fraud, cancellation⇒churn derivations) — surfaced as SQL derivations for human confirmation, never silently applied.

---

## 8. Evaluation (binding, per subsystem)

| Suite | Subsystem | Gate |
|---|---|---|
| Round-trip determinism (15 cases incl. locale decimals, epoch variants, sentinel nulls, leading-zero ids) | A | 100%; byte-identical replay |
| Drift hard-stop | A | mutated-schema fixture must refuse with correct diff |
| Quarantine accounting | A | seeded bad rows all land in quarantine with right reason codes |
| Grounding validator | B.1 | seeded fabrications: 100% caught; clean extractions: <2% false-positive nulling |
| E5 execution accuracy | B.2 | ≥70% on customer-like eval schemas; 0 write statements ever executed |
| E2 provenance completeness | C | 100%, hard gate |
| Adversarial absent-fact suite | C | 0 fabrications escape |
| E3 distribution fidelity | C + A | 0 invented categoricals; drift metrics within bounds |
| Artifact replay | §6 | byte-identical across runs and machines |

---

## 9. Failure Modes Register

1. **Coercion silently lossy** (id leading zeros, currency minor units) → catalogue tests are exhaustive per coercion; new coercion without tests fails CI.
2. **Proposer overreach** (maps a field with no defensible source) → `unmappable` channel in the contract + validator's row/sample checks + human confirm; "never guess" is in the prompt AND enforced after it.
3. **Validator loosening pressure** (E2 false negatives tempt threshold relaxation) → formatters fix root cause (§5.2); E2 threshold is in the one yaml CI reads — loosening is a visible, reviewable diff, not a local hack.
4. **SQL agent against prod load** → read-only role, EXPLAIN ceiling, row caps, statement timeout; agent connects to a replica when one exists (sources.yaml flag).
5. **Artifact/model version drift in serving** → registry pinning + refusal invariant (§6.2).
