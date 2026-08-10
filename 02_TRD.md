# DataBridge — Technical Requirements Document (TRD)
# Version 1.0 · For Claude Code · Companion to 01_PRD.md
# Structure: System Architecture → Task-by-task build guide
# Each task: CONTEXT → ARCHITECTURE → DECISIONS & TRADEOFFS → IMPLEMENTATION LOGIC → EVAL → DONE-WHEN

---

# PART A — SYSTEM ARCHITECTURE

## A.1 The Full System

```
┌──────────────────────── CUSTOMER PERIMETER (VPC / on-prem) ────────────────────────┐
│                                                                                     │
│  SOURCES                INGESTION LAYER              NORMALISED STORE               │
│  PostgreSQL ──────────► db_connector ──────┐                                        │
│  Documents  ──────────► markitdown_adapter ┼──► Arrow tables + Markdown docs        │
│  Codebases  ──────────► graphify_adapter ──┤    (DuckDB as embedded query engine)   │
│  Slack/Drive/Gmail ───► airbyte (Stage 2) ─┤                                        │
│  Meetings ────────────► recall/whisper (S2)┘                                        │
│                                                                                     │
│                          SEMANTIC LAYER (the moat)                                  │
│              schema_graph_builder ─► llm_annotator ─► confirmation ─►               │
│                       annotated_semantic_schema.json (versioned)                    │
│                                                                                     │
│   PILLAR 1: KNOWLEDGE GRAPH              PILLAR 2: CONVERSION ENGINE                │
│   entity_extraction                      router ─► rule_engine (JSONata)            │
│   bi-temporal edges (Graphiti style)            ─► sql_agent (self-correcting)      │
│   Neo4j Community / FalkorDB                    ─► grounded_generator (SFT etc.)    │
│   hybrid retrieval (vec+graph+BM25)      validator (provenance, distribution)       │
│   MCP server for agents                  pipeline_artifact (versioned, serialised)  │
│                                                                                     │
│                       MODEL HARNESS (LiteLLM proxy)                                 │
│        ┌──────────────┬──────────────┬──────────────┬──────────────┐               │
│        │ Anthropic API│  OpenAI API  │ Ollama local │  vLLM local  │               │
│        └──────────────┴──────────────┴──────────────┴──────────────┘               │
│                                                                                     │
│                       EVAL HARNESS (runs in CI, gates merges)                       │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

## A.2 Repository Layout (monorepo)

```
databridge/
├── pyproject.toml              # uv-managed; Python 3.11+
├── packages/
│   ├── core/                   # shared types, provenance, config
│   │   └── databridge_core/
│   │       ├── types.py        # Pydantic models used EVERYWHERE
│   │       ├── provenance.py   # Provenance, Fact, GroundedValue
│   │       ├── config.py       # single config object; model routing table
│   │       └── events.py       # typed event protocol for pipeline steps
│   ├── harness/                # model abstraction (LiteLLM wrapper)
│   ├── ingest/                 # connectors + adapters
│   ├── semantic/               # schema graph + annotator + confirmation
│   ├── graph/                  # knowledge graph (Pillar 1)
│   ├── convert/                # conversion engine (Pillar 2)
│   └── evals/                  # all evaluation suites
├── apps/
│   ├── cli/                    # POC interface (typer)
│   └── mcp_server/             # Stage 2: expose graph to agents
├── tests/
└── docs/                       # the living wiki seeds live here too
```

**Reasoning:** packages enforce the dependency direction core ← everything; harness ← {semantic, graph, convert}; nothing imports a vendor SDK except harness. This makes P2 (model-agnostic) structurally enforceable: a CI rule greps for `import anthropic|import openai` outside `packages/harness` and fails the build.

## A.3 The Single Most Important Type: GroundedValue

Everything in DataBridge flows through provenance-carrying values. Define once in core, use everywhere.

```python
# packages/core/databridge_core/provenance.py
from pydantic import BaseModel
from typing import Any, Literal, Optional

class SourcePointer(BaseModel):
    """Where a value came from. Exactly one locator family is set."""
    source_id: str                      # registered source (e.g. "pg_main", "docs_folder")
    kind: Literal["table_cell", "doc_span", "transcript_span", "code_node", "derived"]
    # table_cell
    table: Optional[str] = None
    column: Optional[str] = None
    row_id: Optional[str] = None
    # doc_span / transcript_span
    document_id: Optional[str] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    timestamp_ms: Optional[int] = None  # transcripts
    # code_node
    file_path: Optional[str] = None
    ast_node_id: Optional[str] = None
    # derived: derivation references other pointers
    derived_from: Optional[list["SourcePointer"]] = None
    derivation: Optional[str] = None    # human-readable rule, e.g. "SUM over 7d window"

class GroundedValue(BaseModel):
    value: Any
    pointer: SourcePointer
    confidence: float = 1.0             # 1.0 = directly copied; <1.0 = inferred
```

**Reasoning:** P4 says untraceable values are bugs. Making provenance a wrapper type (not a side-channel log) means the type checker enforces it: a converter that emits raw `str` instead of `GroundedValue` fails review. `derived` covers aggregations (a 7-day rolling count points at the rows it summed). Confidence enables P6 routing.

## A.4 Model Routing Table (config, not code)

```yaml
# config/models.yaml
routing:
  # task_class → model alias; aliases resolve via LiteLLM
  schema_annotation:        sonnet          # closed-vocab classification
  cross_table_inference:    frontier        # multi-hop reasoning → Fable/best local
  agentic_orchestration:    frontier
  entity_extraction:        sonnet
  entity_resolution_judge:  sonnet
  sql_generation:           sonnet
  sql_revision:             sonnet
  data_to_text:             sonnet
  verification_pass:        sonnet_alt      # DIFFERENT model than generator (adversarial)
aliases:
  sonnet:      {model: "anthropic/claude-sonnet-4-6"}
  frontier:    {model: "anthropic/claude-fable-5"}
  sonnet_alt:  {model: "openai/gpt-4.1"}        # or second local model
  local_main:  {model: "ollama_chat/qwen3:72b"}  # customer override target
profiles:
  cloud:   {}                                   # use aliases as-is
  airgapped:                                     # P1 deployment: every alias → local
    sonnet: local_main
    frontier: local_main
    sonnet_alt: {model: "ollama_chat/llama3.3:70b"}
```

**Reasoning:** The Fable-for-hard / Sonnet-for-routine split is encoded as task classes, so swapping the entire deployment to local models (P1) is a profile change. The verification model is deliberately different from the generation model — adversarial separation reduces correlated failure. Qwen family is the default local model because it is specifically strong at structured/JSON output.

## A.5 Structured Output Enforcement — three tiers, always

Every LLM call that expects structure uses this ladder:
1. **L1 — Pydantic schema** on the response (always).
2. **L2 — Instructor** retry-on-validation-failure (always; max 2 retries).
3. **L3 — Constrained decoding** when the backend supports it: vLLM `guided_json`, Ollama `format`, OpenAI strict mode. Anthropic tool-use does NOT guarantee schema compliance — L1/L2 carry the weight there.

**Tradeoff:** grammar constraints slow generation 30–80% but eliminate retry loops and guarantee valid syntax token-by-token. For batch jobs (annotation of 500 columns) the determinism is worth the latency; for interactive use, L2 alone may be acceptable. The harness exposes `structured=True/strict` per call.

---

# PART B — TASK-BY-TASK BUILD GUIDE (STAGE 1 / POC)

Tasks are ordered by dependency. Each is sized for one focused Claude Code session.

---

## TASK 1 — Core package: types, provenance, config, events

**CONTEXT.** Everything depends on shared types. The provenance model (A.3) is the legal system of the codebase: it makes hallucination structurally detectable. The event protocol gives every pipeline step a typed, replayable record — which Stage 3 audit logs and the living wiki's auto-update hooks both consume.

**ARCHITECTURE.** Pure-Python package, zero LLM/vendor imports, near-100% test coverage. Modules: `types.py` (SemanticType enum, TableRole enum, AnnotatedColumn, AnnotatedTable, AnnotatedSchema), `provenance.py` (A.3), `config.py` (loads `config/models.yaml` + `config/sources.yaml`, exposes `resolve_model(task_class) -> str` honouring active profile), `events.py` (PipelineEvent with `step`, `inputs_hash`, `outputs_hash`, `model_used`, `tokens`, `cost_usd`, `duration_ms`).

**DECISIONS & TRADEOFFS.**
- *Closed vocabularies as Enums.* SemanticType = {entity_id, foreign_key, timestamp_created, timestamp_event, amount_money, amount_count, status_categorical, free_text, geographic_location, personal_identifier, boolean_flag, score_rating, duration_seconds, url, json_blob}. TableRole = {entity, event, relationship, lookup, log}. Closed sets are what make L3 constrained decoding and E1 evaluation possible. Tradeoff: occasionally a real column fits nothing; include `unknown` and route to human (P6) rather than widening the vocabulary ad hoc.
- *Hashing in events.* `inputs_hash`/`outputs_hash` are content hashes enabling "same context + same seed" reproducibility claims later and cache keys now.

**IMPLEMENTATION LOGIC.** Pydantic v2 throughout; `model_config = ConfigDict(frozen=True)` on provenance types (immutability prevents pointer mutation after creation). Config resolution order: env var profile → yaml → defaults. Fail LOUD on unknown task_class (a typo silently routing to the wrong model is exactly the class of bug that's hard to find later).

**EVAL.** Unit tests only at this layer; property test that every SourcePointer kind validates required fields (table_cell requires table+column+row_id, etc.).

**DONE-WHEN.** `uv run pytest packages/core` green; CI grep rule for vendor imports in place; `resolve_model("schema_annotation")` returns different models under `cloud` vs `airgapped` profiles.

---

## TASK 2 — Model harness (LiteLLM wrapper)

**CONTEXT.** P2 in executable form. Every other package calls `harness.complete(task_class, messages, response_model=...)` and never knows which vendor served it. This is also where E7 cost tracking and the L1/L2/L3 structured-output ladder live.

**ARCHITECTURE.**
```
harness/
├── client.py          # complete(), complete_structured()
├── backends.py        # capability table: which backends support guided_json / json mode / tools
├── tracking.py        # per-call PipelineEvent emission (tokens, cost, latency)
└── testing.py         # FakeBackend: deterministic canned responses for tests
```
`complete_structured(task_class, messages, response_model: type[BaseModel], strict: bool)` → resolves model via core config → checks backend capability → applies L3 if available else L2 via Instructor → validates L1 → emits event → returns typed object.

**DECISIONS & TRADEOFFS.**
- *LiteLLM as the only abstraction.* It normalises tool/function calling across providers and speaks to Ollama/vLLM with the same OpenAI-format interface. Tradeoff: a dependency on LiteLLM's release cadence; acceptable because the alternative (hand-rolling N adapters) is exactly the connector-maintenance trap.
- *Capability table is explicit, not probed.* OpenAI strict mode guarantees schema compliance; Anthropic does not; Ollama `format=json` guarantees JSON but not your schema. Encoding this in `backends.py` makes the gap visible instead of discovered in production.
- *FakeBackend ships in Task 2,* not later. Every downstream package tests against it; no test ever hits a real API.

**IMPLEMENTATION LOGIC.** Retries: L2 max 2, with the validation error text appended to the retry prompt ("Your previous output failed validation: {err}. Return ONLY corrected JSON."). Timeout and fallback chains via LiteLLM config (`fallbacks: [sonnet → local_main]` in airgapped degraded mode). Temperature defaults: 0 for all structured tasks; configurable only via task_class config, never per-call literals (reproducibility).

**EVAL.** Integration test against Ollama with a small local model in CI (qwen3:4b) proving the structured ladder produces valid `AnnotatedColumn` objects; E7 events asserted present on every call.

**DONE-WHEN.** Same calling code yields valid structured output from both Anthropic backend and Ollama backend; cost/token events recorded; FakeBackend usable by Task 3+.

---

## TASK 3 — Ingestion: PostgreSQL connector

**CONTEXT.** First Pillar-1 source. The connector's job is NOT to copy data — it extracts schema metadata + bounded samples (P1: minimal movement; the semantic layer needs metadata, not the warehouse). Full-data reads happen later, lazily, only when a conversion plan demands specific tables.

**ARCHITECTURE.** `ingest/postgres.py` produces a `SchemaExtract`:
```python
class ColumnMeta(BaseModel):
    name: str; dtype: str
    null_fraction: float; unique_count: int
    sample_values: list[Any]            # ≤20, randomly sampled
    min_val: Any | None; max_val: Any | None
    is_primary_key: bool
class TableMeta(BaseModel):
    name: str; row_count: int
    columns: list[ColumnMeta]
    sample_rows: list[dict]             # ≤50 rows
    foreign_keys: list[ForeignKey]      # from information_schema + pg_constraint
    indexes: list[str]
class SchemaExtract(BaseModel):
    source_id: str; tables: list[TableMeta]; extracted_at: datetime
```
Implementation reads `information_schema` + `pg_catalog`; samples via `TABLESAMPLE SYSTEM` for big tables (avoids full scans); stats via one aggregate query per table.

**DECISIONS & TRADEOFFS.**
- *DuckDB as the local analysis engine.* Samples land in Arrow tables; DuckDB queries them in-process (the "single-node renaissance" — no Spark for a POC). Arrow is the internal interchange format everywhere (A.1).
- *Sample size 50 rows / 20 values per column.* Enough for an LLM to infer semantics; small enough that even under the strictest reading of P1, exposure is minimal and configurable to 0 external (airgapped profile sends samples only to local models).
- *FK extraction from constraints AND naming heuristics.* Many real schemas lack declared FKs. A heuristic pass (`*_id` columns whose values ⊆ a candidate PK column's values, tested on samples) proposes undeclared FKs with confidence <1.0 — routed to human confirmation, never assumed.

**IMPLEMENTATION LOGIC.** Connection via SQLAlchemy URL from `config/sources.yaml`. Never `SELECT *` without LIMIT. Redaction pass before anything leaves the connector: columns whose sampled values match PII regexes (email, phone, NRIC/SSN patterns) get `sample_values=["<REDACTED:email>"]` while keeping stats — semantics survive, raw PII doesn't travel.

**EVAL.** Fixture database (docker-compose postgres seeded with a realistic fintech schema: users, merchants, transactions, disputes, support_messages). Assert: FK graph correct; heuristic FK finds the one deliberately-undeclared FK; PII redaction catches seeded emails.

**DONE-WHEN.** `databridge ingest pg_main` writes `schema_extract.json` + Arrow samples; fixture evals green.

---

## TASK 4 — Ingestion: documents via MarkItDown (+ Docling fallback)

**CONTEXT.** Unstructured documents are the second source type. MarkItDown converts PDF/Word/PPT/Excel/images/audio → Markdown, which becomes the universal pre-LLM document format. Known limits shape the design: MarkItDown cannot process un-OCR'd PDFs, strips heading structure from PDFs, and has limited structure fidelity — so a quality gate routes hard documents to Docling.

**ARCHITECTURE.** `ingest/documents.py`:
```
file → sniff type → MarkItDown convert → quality_score(md)
     → if score < threshold AND type == pdf → Docling convert → re-score
     → DocumentRecord{document_id, source_path, markdown, char_index, quality, converter_used}
```
`char_index` preserves a mapping good enough for `SourcePointer.doc_span` provenance (we point into OUR markdown, and store the original file hash + path so the original is always recoverable).

**DECISIONS & TRADEOFFS.**
- *Markdown as the canonical doc format.* One format downstream (chunker, extractor, graph) instead of N parsers. Tradeoff: lossy for complex layouts — accepted for POC; Docling fallback covers tables-heavy financial/medical PDFs.
- *quality_score is deterministic (P3):* ratio of non-whitespace chars to file size, presence of ≥1 heading per N chars for docs known to have structure, table-row detection. No LLM judging quality of conversion — that would be slow, costly, and circular.
- *Audio in POC?* No. MarkItDown can transcribe, but meetings are Stage 2 (Recall.ai/faster-whisper). Keep POC surface small.

**IMPLEMENTATION LOGIC.** Process pool for batch conversion (CPU-bound). Every DocumentRecord carries `sha256` of source bytes — the dedup key and the provenance anchor. Store records as JSONL + a DuckDB view over them.

**EVAL.** Golden corpus: 10 documents (clean PDF, scanned PDF, docx with tables, xlsx, pptx). Assert converter routing (scanned → Docling), and that char offsets referenced by a sample SourcePointer resolve to the expected text.

**DONE-WHEN.** `databridge ingest docs_folder` produces DocumentRecords; routing + provenance evals green.

---

## TASK 5 — Ingestion: codebase via Graphify adapter

**CONTEXT.** Codebases are the third POC source and the cheapest win: Graphify (MIT, `pip install graphifyy`) already does tree-sitter parsing (deterministic, local, zero API cost — pure P3), Leiden clustering, and an LLM labelling pass, emitting `graph.json`. We adapt its output into our graph's node/edge types rather than re-implementing static analysis.

**ARCHITECTURE.** `ingest/code_graphify.py`: run Graphify against the repo path → parse `graphify-out/graph.json` → map nodes (functions/classes/files/concepts) to our `Entity` type and edges (calls/imports/semantic) to our `Relation` type, with `SourcePointer(kind="code_node", file_path, ast_node_id)` provenance and `confidence=1.0` for AST-extracted edges (Graphify tags these EXTRACTED — facts) vs lower confidence for LLM-labelled semantic edges (guesses; Graphify separates the two and so do we).

**DECISIONS & TRADEOFFS.**
- *Adapter, not fork.* Graphify moves fast (releases ~every other day); pin a version, treat `graph.json` as the contract, and keep the adapter thin. If the contract breaks, the adapter is one file.
- *Trust split.* The EXTRACTED/guessed separation maps directly onto our confidence model — preserve it; do not flatten facts and inferences into one confidence value.
- *Token-saving claims (71.5×) are context-dependent* — architect for correctness, measure savings in E7, promise nothing numeric.

**IMPLEMENTATION LOGIC.** Subprocess invocation with pinned version check; configurable LLM pass on/off (airgapped profile: Graphify's LLM labelling routes through our harness env vars or is disabled, keeping the graph AST-only — still useful).

**EVAL.** Run against our own monorepo as fixture; assert function-call edges exist for known call pairs; assert zero network calls when LLM pass disabled (socket-blocking test).

**DONE-WHEN.** Code entities/relations land in the same Entity/Relation tables as other sources, with correct provenance kinds.

---

## TASK 6 — Semantic layer part 1: schema graph builder (deterministic)

**CONTEXT.** Before any LLM sees a schema, structural analysis extracts free signal (P3). Graph topology alone reveals table roles: hub tables with many incoming FKs are entities; tables with 2 FKs and little else are relationships; FK-less timestamp-dense tables are events/logs. Feeding these hints to the annotator measurably improves accuracy and reduces tokens.

**ARCHITECTURE.** `semantic/schema_graph.py`: NetworkX DiGraph from SchemaExtract — nodes=tables (with ColumnMeta payload), edges=FKs (with cardinality inferred from sample uniqueness). Pure functions: `infer_structural_role(table, graph) -> (TableRole, confidence)`, `infer_cardinality(fk, samples)`, plus column-level cheap classifiers (regex/dtype-based pre-labels: ISO timestamps, currency-like decimals, email-shaped strings) emitted as `structural_hints`.

**DECISIONS & TRADEOFFS.**
- *NetworkX, not Neo4j, here.* The schema graph is small (tens–hundreds of tables), ephemeral, and analytical. The knowledge graph (Task 8) is the persistent store. Different jobs, different tools.
- *Hints are inputs to the LLM, never final answers* — except when a deterministic rule is airtight (a declared PRIMARY KEY integer column IS entity_id; don't spend tokens asking).

**IMPLEMENTATION LOGIC.** Cardinality from samples: if fk column unique_count ≈ row_count → one-to-one; else many-to-one; junction pattern (two FKs, composite PK) → many-to-many. Emit `SchemaGraphReport` consumed by Task 7.

**EVAL.** Fixture schema: structural role accuracy ≥ 4/5 tables correct with zero LLM calls.

**DONE-WHEN.** Report generated; deterministic-only role inference hits threshold; hints attached per column.

---

## TASK 7 — Semantic layer part 2: LLM annotator + human confirmation

**CONTEXT.** The moat. The annotator assigns each column a SemanticType, each table a concept + role, flags PII, and proposes label candidates (columns/derivations encoding business outcomes — chargebacks, cancellations, resolutions; P5's "find don't fabricate"). Wrong output here cascades into broken downstream models, hence the confirmation flow (P6) and the strictest eval (E1).

**ARCHITECTURE.**
```
SchemaGraphReport ─► per-table prompt builder ─► harness.complete_structured(
                       task_class="schema_annotation",
                       response_model=AnnotatedTable, strict=True)
                  ─► cross-table pass (task_class="cross_table_inference"):
                       relationship naming, graph-shape detection
                       (e.g. accounts→transactions→accounts ⇒ bipartite financial graph),
                       label-candidate discovery across tables
                  ─► confidence router:  ≥0.95 auto-approve
                                         0.80–0.95 spot-check queue
                                         <0.80 mandatory review
                  ─► confirmation CLI (rich): table-by-table review, accept/edit per column
                  ─► AnnotatedSchema v{n}.json  (append-only versions; diffs reviewable)
```
Prompt structure per table: structural hints + column metas + ≤10 sample rows + the closed vocabulary, instructing classification ONLY into the vocabulary (the Enum doubles as the L3 grammar).

**DECISIONS & TRADEOFFS.**
- *Per-table calls, not whole-schema.* Context discipline beats one mega-prompt: schema linking literature shows limiting context to relevant schema improves accuracy 15–20%; same principle applies to annotation. Cross-table reasoning gets its own pass with only the table-level summaries, not raw columns.
- *Two task classes.* Annotation is closed-vocab classification → sonnet-class. Cross-table inference is genuine multi-hop reasoning → frontier-class (Fable in cloud profile). This is the cost/quality routing decision from A.4 made concrete.
- *Confidence comes from the model AND from agreement.* Self-reported confidence is unreliable alone; blend with (a) structural-hint agreement (LLM says amount_money + dtype is numeric(12,2) → boost) and (b) optional second-model vote for the <0.80 band before human review (cheap insurance via local model).
- *Feedback is training data for the system.* Every human correction is stored as (input_context, wrong_pred, correction) — the seed corpus for future few-shot retrieval and eventual fine-tuning. Build the storage now even though nothing consumes it yet.

**IMPLEMENTATION LOGIC.** Deterministic short-circuits BEFORE the LLM (P3): declared PK → entity_id; declared FK → foreign_key; perfect ISO-8601 samples → timestamp_*; this typically resolves 30–50% of columns at zero cost. Label-candidate detection prompt explicitly frames P5: "Identify columns or derivable conditions that record BUSINESS OUTCOMES usable as ML labels (disputes, cancellations, resolutions, defaults). Output the derivation logic as SQL-like pseudocode, not values."

**EVAL.** E1: held-out labelled schemas (build 3: fintech fixture, an open-source app schema e.g. odoo subset, one cryptic legacy-style schema with abbreviated names). Per-column accuracy ≥85% overall; report per-schema so legacy weakness is visible. E6: run identical eval under airgapped profile (Qwen3-72B) and record the parity gap. Cost per schema via E7.

**DONE-WHEN.** Fixture schema annotates end-to-end with confirmation CLI; E1 ≥85%; corrections persisted; AnnotatedSchema versioned.

---

## TASK 8 — Knowledge graph core (Pillar 1)

**CONTEXT.** The Company Brain substrate. Entities and relations from ALL sources (DB schema annotations, document extraction, Graphify code graph) land in one persistent, bi-temporal graph. Bi-temporal (Graphiti's pattern) means every edge carries (t_valid, t_invalid, created_at, expired_at): new conflicting information INVALIDATES old edges rather than deleting them — "who knew what when" is preserved, which is exactly what a company brain must answer and what audit requires.

**ARCHITECTURE.**
```
graph/
├── store.py        # GraphStore interface; Neo4jStore + NetworkXStore(impl for tests)
├── schema.py       # Entity{id, type, name, attrs, source_pointers[]}
│                   # Relation{src, rel_type, dst, t_valid, t_invalid, created_at,
│                   #          expired_at, confidence, source_pointers[]}
├── extract.py      # doc/markdown → entities+relations via harness (Stage-1: simple
│                   #   schema-constrained extraction, LlamaIndex SchemaLLMPathExtractor-style:
│                   #   allowed entity types {Person, Company, Project, System, Decision,
│                   #   Document, Table, CodeUnit}, allowed relations enumerated)
├── ingest_db.py    # AnnotatedSchema → graph (tables⇒System/Table entities, FKs⇒relations)
├── ingest_code.py  # Task 5 adapter output → graph
├── upsert.py       # bi-temporal upsert + naive conflict rule (see logic)
└── retrieve.py     # hybrid retrieval: vector (pgvector or Neo4j vector index)
                    #   + BM25 (DuckDB FTS over entity/doc text) + 1–2 hop traversal,
                    #   reciprocal-rank-fusion
```
Storage behind `GraphStore` interface (PRD risk: dependency death — KuzuDB precedent). Neo4j Community is the default; NetworkX implementation makes the whole graph layer unit-testable without containers.

**DECISIONS & TRADEOFFS.**
- *Flat graph first, no Leiden communities in POC.* Microsoft-GraphRAG-style community summarisation costs 10–100× standard indexing and the POC questions don't need global summarisation. LightRAG's result — flat + dual-mode retrieval is competitive at a fraction of cost — justifies deferring communities to Stage 2/3 behind a flag.
- *Schema-constrained extraction, not open extraction.* Open-ended entity extraction produces noisy graphs needing heavy resolution. A fixed entity/relation vocabulary (mirroring Task 1's closed-set philosophy) yields cleaner graphs and enables L3 constrained decoding. Tradeoff: misses exotic entity types — acceptable; the vocabulary is config, extendable per customer.
- *Conflict handling in POC is "newest wins, old invalidated, both kept."* True resolution (source authority ranking, contradiction surfacing) is Stage 2. Bi-temporal storage makes the upgrade non-breaking — that's WHY bi-temporal goes in now even though POC conflict logic is naive.
- *Entity resolution in POC = exact + normalised-name match only.* Full ER (blocking + LLM judge) is Stage 2 with its own eval (E-gate: P/R >90%). Shipping naive ER knowingly, with the fragmentation risk documented, beats shipping late.

**IMPLEMENTATION LOGIC.** Upsert: match on (type, normalised_name); if incoming relation contradicts an active relation with same (src,rel_type,dst) family → set old `t_invalid=now, expired_at=now`, insert new. Every entity/relation keeps `source_pointers` (multi-source corroboration appends pointers — corroboration count is a cheap quality signal). Retrieval fusion: RRF over the three rankers, k=60, return entities + supporting pointers so answers ALWAYS cite (E4 requires citations).

**EVAL.** E4: 30-question Q&A set over the fixture corpus (10 single-hop, 10 multi-hop, 10 temporal "what was true before X"). Recall@5 ≥70% with valid citations. Store-swap test: full suite passes on NetworkXStore and Neo4jStore identically.

**DONE-WHEN.** All three POC sources flow into one graph; NL query CLI answers with citations; E4 green on both stores.

---

## TASK 9 — Conversion engine: router + rule engine (structured→structured)

**CONTEXT.** Pillar 2 begins. The router classifies a conversion request (source format/shape, target format) and dispatches to one of three subsystems. The rule engine handles structured→structured deterministically: the LLM may PROPOSE a mapping once, but execution is pure JSONata — auditable, re-runnable, zero-token (P3). This pattern (LLM generates declarative transform, library validates syntax, engine executes) is the 2025-validated approach to schema mapping.

**ARCHITECTURE.**
```
convert/
├── router.py          # ConversionRequest{source_ref, target_spec} → subsystem
├── rules/
│   ├── engine.py      # execute(jsonata_expr | dbt-style SQL, data) → data
│   ├── proposer.py    # LLM proposes mapping given source schema annot. + target schema
│   ├── validator.py   # syntactic: parse via jsonata lib; semantic: run on samples,
│   │                  #   type-check outputs against target schema, E3 distribution checks
│   └── library.py     # versioned store of confirmed mappings (reuse before re-propose)
└── pipeline_artifact.py  # serialise: mapping + fitted params + source/target schema
                          #   versions + content hashes  → the train/serve-skew killer
```
Flow: request → library hit? reuse : proposer → validator (auto) → human confirm (P6, only for new mappings) → engine executes over full data via DuckDB → artifact saved.

**DECISIONS & TRADEOFFS.**
- *JSONata for record-shaped transforms; SQL (DuckDB) for relational/aggregation transforms.* JSONata excels at JSON reshaping (filter/map/reduce/higher-order); window aggregations and joins are SQL's home turf. The proposer picks the language by target shape; both are declarative text artifacts — diffable, reviewable, versionable.
- *Type coercion table is explicit.* The edge-case zoo lives here: string-encoded numbers, epoch vs ISO timestamps, locale decimals ("1.234,56"), nulls vs empty strings vs sentinel values (-999). A central `coercions.py` with exhaustive tests beats scattering fixes; every coercion is named in the mapping so the artifact records EXACTLY what happened.
- *Schema drift policy:* artifact stores source schema hash; on execution, mismatch → hard stop with diff report, never silent best-effort. Silent drift is how trained models rot (PRD §2 training-serving skew).

**IMPLEMENTATION LOGIC.** Validator semantic pass: execute mapping on the 50-row samples; assert (a) output validates against target Pydantic/JSON-schema, (b) no invented categoricals (E3), (c) numeric distributions sane (KS vs source where the mapping is 1:1). Pipeline artifact = tar of {mapping.jsonata|sql, coercions.json, schemas.json, hashes.json, fitted_params.pkl (scalers etc. when Stage 3 adds them)} with a manifest; `databridge run-artifact <id> --mode infer` applies the identical transform to a single record — the inference path exists from day one even if unused.

**EVAL.** Round-trip suite: 15 conversion cases (flatten nested JSON→CSV, pg rows→target JSON API shape, csv→parquet with typed coercions, epoch→ISO, locale decimals). 100% pass; artifact replay produces byte-identical output (determinism test).

**DONE-WHEN.** End-to-end: annotated pg table → confirmed mapping → full conversion via DuckDB → artifact → replay determinism green.

---

## TASK 10 — Conversion engine: grounded generator (structured→unstructured / SFT)

**CONTEXT.** The flagship demo and highest hallucination risk: turn the fixture's `support_messages` + transaction context into SFT conversation pairs (ShareGPT/OpenAI format). Architecture is slot-filling: the LLM writes STRUCTURE; every fact is injected from GroundedValues; validators reject anything untraceable (P4, E2) or distributionally off (E3).

**ARCHITECTURE.**
```
convert/generate/
├── fact_extractor.py   # rows → list[GroundedValue] (facts with pointers)
├── sft_from_logs.py    # real conversations path: role-map senders, quality-filter
│                       #   (resolution flag, CSAT≥4, ≥min_turns), format ShareGPT/OpenAI;
│                       #   provenance = transcript/table spans per message. NO generation.
├── synthesiser.py      # synthetic path: template selection by LLM, slots filled from
│                       #   facts ONLY; prompt lists facts as [F1]..[Fn]; model must cite
│                       #   facts_used per turn; "if a needed fact is absent, say
│                       #   'let me check' — NEVER invent"
├── verifier.py         # second-model pass (task_class=verification_pass): every claim
│                       #   in output ↦ supported by listed facts? unsupported ⇒ reject
└── validators.py       # E2 provenance completeness (regex+span match of fact values in
                        #   text), E3 categorical containment, dedup (minhash)
```

**DECISIONS & TRADEOFFS.**
- *Two paths, clearly separated.* Real-log formatting is reformatting (zero hallucination risk — prefer it whenever logs exist). Synthesis is for augmentation/gap-filling only, and carries the full validator gauntlet. Conflating them is how training sets get poisoned.
- *Quality filtering by proxy signals, not LLM judgment, in POC.* CSAT/resolution flags are deterministic and defensible (P3). An LLM quality-scorer is a Stage-3 add-on with its own eval.
- *Verifier model ≠ generator model* (A.4 sonnet_alt): adversarial separation; correlated blind spots are the failure mode of self-verification.
- *Rejected ≠ discarded.* Rejected synthetics go to a quarantine file with the failure reason — they are the eval-hardening corpus and the debugging trail.

**IMPLEMENTATION LOGIC.** Fact citation contract: generator returns `{turns:[{role, content, facts_used:[...]}]}` (L3-constrained). Validator cross-checks: every fact value string (post-formatting: currency/dates rendered via deterministic formatters so string-match is reliable) appears in content of a turn that cites it; any content number/date/name NOT matching a cited fact ⇒ reject. Output writer emits both ShareGPT and OpenAI chat formats from one internal form.

**EVAL.** E2 at 100% (hard gate). Adversarial suite: prompts engineered to tempt invention (ask about a field deliberately absent from facts) — pass = model says "let me check," fail = any fabricated value escapes validators (must be 0). Human spot-check protocol documented: 20 random pairs per run rated for naturalness (target ≥4/5 avg) — quality is subjective; the gate is factuality, the dial is naturalness.

**DONE-WHEN.** Fixture produces ≥200 validated SFT pairs (real-log path) + ≥50 synthetic pairs with zero E2 failures; quarantine populated by adversarial suite as expected.

---

## TASK 11 — Evaluation harness + CI wiring

**CONTEXT.** PRD §6 says no component is done without its eval. This task makes that mechanical: one command runs every suite, produces a scorecard, and CI blocks merge on threshold regression. It also closes the loop for E6 (local-model parity) and E7 (cost), which only exist if measured continuously.

**ARCHITECTURE.** `packages/evals/`: each suite is a pytest module emitting a structured `EvalResult{suite, metric, value, threshold, pass}` to `evals/results/{commit}.json`; `databridge eval --all|--suite E1 --profile cloud|airgapped`; scorecard renderer (markdown table) appended to PRs by CI; threshold config in one yaml so PRD gates and CI gates cannot diverge.

**DECISIONS & TRADEOFFS.** Real-model evals (E1/E4/E6) run nightly + on-demand, not per-commit (cost); per-commit CI runs deterministic suites (provenance, round-trip, store-swap) against FakeBackend + local qwen3:4b smoke. Tradeoff: a bad prompt change can land between nightlies — mitigated by requiring on-demand eval before any merge touching `semantic/` or `convert/generate/` (CODEOWNERS + label rule).

**EVAL.** The harness evals itself: a seeded regression (deliberately broken annotator fixture) must turn the scorecard red.

**DONE-WHEN.** `databridge eval --all` produces the scorecard; CI gates wired; nightly job runs both profiles and posts the parity gap.

---

## TASK 12 — POC CLI + demo flow

**CONTEXT.** The 48-hour sales motion ("give us one table, see output in your environment") needs a scriptable spine. The CLI is the product surface for Stage 1; the Stage-2 web UI wraps the same commands.

**ARCHITECTURE.** typer app: `databridge init` (write config templates) · `ingest <source_id>` · `annotate <source_id>` (runs Tasks 6–7 incl. confirmation TUI) · `graph build` · `graph ask "<question>"` (cited answers) · `convert <request.yaml>` · `eval` · `status` (sources, schema versions, artifacts, last eval scorecard). Everything reads/writes the project workspace dir (`.databridge/`) so a demo is a directory you can zip.

**DECISIONS & TRADEOFFS.** TUI confirmation via `rich` tables + prompt-toolkit; no web stack in POC (speed; P6 needs a reviewable flow, not pixels). All commands idempotent and resumable (event log from Task 1 is the checkpoint record) — demos die on flaky wifi, resumability is a demo feature.

**DONE-WHEN.** Scripted demo: fixture pg + 10 docs + this repo → ingest → annotate (with 2 human corrections) → graph ask 3 questions (cited) → convert to SFT pairs → eval scorecard — runs start-to-finish in <30 min on a laptop with Ollama, and in cloud profile with Anthropic keys.

---

# PART C — STAGE 2/3 TASK SUMMARIES (for planning; full specs to be written at stage entry)

- **T13 Airbyte integration:** embed OSS Airbyte (or PyAirbyte) for Slack/Drive/Gmail; normalise into DocumentRecords/Arrow; OAuth per source; rate-limit ownership per collector.
- **T14 Meetings:** Recall.ai bot API (hosted profile) / faster-whisper on recordings (airgapped); diarised transcripts → transcript_span provenance → extraction into graph (Decision/Person/Project entities).
- **T15 Entity resolution:** blocking (normalised name, embedding KNN) → LLM judge pairs → merge with pointer union; eval on labelled pairs, gate P/R >90%.
- **T16 Incremental updates:** source change detection (hashes, CDC for pg) → affected-subgraph recompute via set-union merge; gate <10% of full-rebuild cost.
- **T17 Text-to-SQL agent:** schema-linking (annotated schema makes this strong) → generate → execute → Fixer (syntax) → Reviser (semantics, result-shape checks) loop ≤3; dynamic few-shot from confirmed-query library; gate E5 ≥70% execution accuracy.
- **T18 MCP server:** expose `graph_ask`, `graph_search`, `convert_run` as MCP tools so Claude Code/agents consume the brain directly.
- **T19 Web confirmation UI:** schema-graph visual (React Flow), annotation review queues, conversion-plan approval.
- **T20 Tabular-ML + GNN templates:** feature tables with temporal aggregations + leakage detection; PyG HeteroData export; time-based splits enforced.
- **T21 Pipeline artifacts in inference path:** artifact server applying identical transforms per-request; version pinning to model registry entries.
- **T22 Permissioning/RBAC + audit:** per-source ACL mirroring (Onyx pattern); event log → audit trail UI.
