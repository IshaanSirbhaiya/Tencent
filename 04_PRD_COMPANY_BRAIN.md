# PRD-04 — Data Availability Pillar: The Company Brain
# Deep specification · Companion to 01_PRD.md (principles P1–P7 apply) and 02_TRD.md
# Scope: ingestion harness, knowledge graph, retrieval, agent access

---

## 1. What This Pillar Is

The Company Brain is a **typed, versioned, bi-temporal knowledge graph** built automatically from every data source an organisation has, queryable by humans and AI agents, running entirely inside the customer's perimeter with any model (hosted or local).

It is NOT a wiki (documents with search), NOT a vector store (flat chunks with similarity), NOT a chatbot. The distinction that matters: **knowledge is graph-shaped**. "Customer owns Subscription, Subscription generates Revenue, Decision X changed Pricing on date Y" — these are typed, traversable, time-stamped relationships. A retrieved passage can't be joined, traversed, or version-diffed; a graph edge can.

**Design lineage (what we adopt and from where):**

| Pattern | Source | What we take |
|---|---|---|
| Git repo as system of record; Markdown as canonical knowledge format; deterministic "minions" vs LLM agents split | gbrain (Garry Tan, MIT) | The storage philosophy + the deterministic/generative split (our P3). gbrain's production numbers (146k pages, 97.6% R@5 on LongMemEval **with no LLM in the retrieval loop**) prove retrieval can be deterministic. |
| Bi-temporal edges: (t_valid, t_invalid, created_at, expired_at); invalidate-don't-delete | Graphiti (Zep) | The temporal model wholesale. "Who knew what when" is the company-brain killer feature and the audit requirement. |
| Flat graph + incremental set-union merge; dual local/global retrieval | LightRAG | Cost discipline: no Leiden community summarisation in POC (10–100× indexing cost per Microsoft GraphRAG experience). |
| Schema-constrained extraction (allowed entity/relation types as closed sets) | LlamaIndex SchemaLLMPathExtractor / Neo4j builder | Clean graphs over noisy open extraction. |
| Per-source document-level permission mirroring | Onyx | Stage 2 permissioning pattern. |
| Codebase → graph via tree-sitter (deterministic) + LLM labels (separated confidence) | Graphify | Adapter, not fork (Task 5). |

---

## 2. Component Architecture

```
                        ┌─────────────────────────────────────────────┐
                        │              SOURCE REGISTRY                 │
                        │  sources.yaml: id, kind, conn, schedule,     │
                        │  acl_mode, pii_policy                        │
                        └──────────────────┬──────────────────────────┘
                                           ▼
       ┌───────────────────────────────────────────────────────────────────┐
       │                        COLLECTOR LAYER                             │
       │  each collector OWNS: auth, rate limits, pagination, change       │
       │  detection, redaction. Emits ONLY normalised records:             │
       │                                                                   │
       │  pg_collector      → SchemaExtract + (lazy) RowBatches (Arrow)    │
       │  doc_collector     → DocumentRecord (MarkItDown→Docling routing)  │
       │  code_collector    → CodeGraphExtract (Graphify adapter)          │
       │  slack/gmail/drive → DocumentRecord (Airbyte, Stage 2)            │
       │  meetings          → TranscriptRecord (Recall.ai/faster-whisper)  │
       └──────────────────┬────────────────────────────────────────────────┘
                          ▼
       ┌───────────────────────────────────────────────────────────────────┐
       │                     EXTRACTION LAYER                               │
       │  per record-kind extractors → (Entity[], Relation[]) candidates   │
       │  - db: deterministic from AnnotatedSchema (zero LLM)              │
       │  - code: deterministic from Graphify EXTRACTED edges (zero LLM)   │
       │    + low-confidence semantic edges passthrough                    │
       │  - docs/transcripts: LLM schema-constrained extraction            │
       └──────────────────┬────────────────────────────────────────────────┘
                          ▼
       ┌───────────────────────────────────────────────────────────────────┐
       │                  RESOLUTION + UPSERT LAYER                         │
       │  entity resolution (Stage1: normalised exact; Stage2: blocking    │
       │  + LLM judge) → bi-temporal upsert → GraphStore                   │
       └──────────────────┬────────────────────────────────────────────────┘
                          ▼
       ┌───────────────────────────────────────────────────────────────────┐
       │   GRAPH STORE (interface)         RETRIEVAL                       │
       │   Neo4jStore | NetworkXStore      vector + BM25 + traversal       │
       │   (FalkorDBStore optional)        → RRF fusion → cited answers    │
       └──────────────────┬────────────────────────────────────────────────┘
                          ▼
                 CONSUMERS: CLI ask · MCP tools · Pillar 2 conversion plans
```

---

## 3. Data Model (the contract everything compiles against)

```python
# packages/graph/databridge_graph/schema.py
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum
from databridge_core.provenance import SourcePointer

class EntityType(str, Enum):
    PERSON = "person"; COMPANY = "company"; PROJECT = "project"
    SYSTEM = "system"; TABLE = "table"; CODE_UNIT = "code_unit"
    DOCUMENT = "document"; DECISION = "decision"; CONCEPT = "concept"
    MEETING = "meeting"          # Stage 2
    # Extension is config-per-customer via ADR, never ad hoc (P3/P6)

class RelationType(str, Enum):
    OWNS = "owns"; WORKS_ON = "works_on"; MENTIONS = "mentions"
    DECIDED_IN = "decided_in"; DEPENDS_ON = "depends_on"
    REFERENCES_TABLE = "references_table"; CALLS = "calls"
    IMPORTS = "imports"; FOREIGN_KEY = "foreign_key"
    PARTICIPATED_IN = "participated_in"; SUPERSEDES = "supersedes"

class Entity(BaseModel):
    entity_id: str                    # stable: f"{type}:{normalised_name}" (Stage 1)
    type: EntityType
    name: str
    normalised_name: str              # lowercase, punctuation-stripped, suffix-folded
    attrs: dict = Field(default_factory=dict)
    source_pointers: list[SourcePointer]   # corroboration appends; len() = quality signal
    created_at: datetime

class Relation(BaseModel):
    src: str; dst: str                # entity_ids
    rel_type: RelationType
    confidence: float                 # 1.0 deterministic (AST/FK); <1.0 LLM-extracted
    source_pointers: list[SourcePointer]
    # ---- bi-temporal core (Graphiti pattern) ----
    t_valid: datetime                 # when the fact BECAME true in the world
    t_invalid: datetime | None        # when it STOPPED being true (None = still true)
    created_at: datetime              # when WE learned it
    expired_at: datetime | None       # when WE invalidated it (None = active)
```

**Why four timestamps (the bi-temporal logic, spelled out):**
- *World time* (t_valid/t_invalid): "Alice owned Project X from Jan to Mar."
- *Knowledge time* (created_at/expired_at): "We learned this on Feb 2; we superseded it on Apr 1."
- Queries the model unlocks: "what did the graph believe on March 1" (audit/replay), "what was actually true on March 1" (history), "what changed between two ingests" (diffing). Deleting edges destroys all three. **Invalidate, never delete.**

---

## 4. The Agentic Harness (model-agnostic crawling)

The harness is the orchestrator that decides WHAT to ingest next, WHEN to re-ingest, and HOW to resolve conflicts — the genuinely agentic part (task_class `agentic_orchestration` → frontier model in cloud profile; best local model airgapped).

### 4.1 The crawl loop

```python
# packages/graph/databridge_graph/harness_loop.py — conceptual skeleton
class CrawlPlanner:
    """LLM plans; deterministic executors act. The LLM NEVER touches data directly —
    it selects from a menu of typed actions. (P3: generative chooses, deterministic does.)"""

    ACTIONS = ["ingest_source", "reingest_changed", "extract_pending",
               "resolve_entities", "skip_low_value", "ask_human"]

    def plan_next(self, world: CrawlWorldState) -> PlannedAction:
        # world = {sources: [{id, kind, last_ingest, change_signal, est_cost, est_value}],
        #          pending_extractions: n, unresolved_entity_pairs: n,
        #          budget_remaining_usd, open_human_questions}
        return self.harness.complete_structured(
            task_class="agentic_orchestration",
            messages=self._render_world(world),
            response_model=PlannedAction,    # action + target + reasoning, closed action set
            strict=True,
        )
```

**Decisions & tradeoffs:**
- *Plan/act separation.* The LLM emits a `PlannedAction` from a closed action vocabulary; executors are deterministic functions. This makes every crawl step replayable from the event log, keeps token cost to planning only, and means a weaker local model degrades planning quality, never data integrity.
- *Budget is a first-class input.* `budget_remaining_usd` (from E7 tracking) is in the world state; the planner is instructed to prefer zero-token actions (deterministic extractions, re-ingest of changed-hash sources) when budget is tight. This is how cost discipline becomes behaviour rather than hope.
- *`ask_human` is an action, not a failure.* Conflicting authoritative sources, ambiguous source value, or PII-policy uncertainty → the planner queues a question (lands in confirmation UI / STATE.md open questions) and moves on. P6 applied to crawling.
- *Change detection is deterministic:* content hashes per document, `pg_stat` + table checksums for DB (CDC in Stage 2), git HEAD for code. The planner consumes `change_signal: bool`; it never guesses whether something changed.

### 4.2 Scheduling without the agent

A boring cron handles the steady state (nightly re-hash, weekly full re-extract of changed sources). The agentic planner runs (a) on demand, (b) when the schedule surfaces >N changed sources and prioritisation matters, (c) when budget constraints force triage. **Reasoning:** an agent loop running 24/7 is cost without benefit; agency is for decisions under competing priorities, not for executing a timetable.

---

## 5. Extraction Layer — per source kind

### 5.1 Database → graph (zero LLM)
AnnotatedSchema (Pillar-2 semantic layer output) maps deterministically: each table → `Entity(type=TABLE)`; declared+confirmed FKs → `Relation(FOREIGN_KEY, confidence=1.0)`; tables annotated role=entity whose concept matches {person, company, ...} additionally project a typed entity per concept. **No LLM here** — the semantic layer already paid that cost once, with human confirmation; re-extracting would re-introduce error.

### 5.2 Code → graph (Graphify adapter, Task 5)
EXTRACTED edges (AST facts) → confidence 1.0; LLM-labelled semantic edges → confidence from Graphify, capped at 0.85 (uncorroborated single-model labels never reach auto-approve band).

### 5.3 Documents/transcripts → graph (the LLM extraction path)

```python
class ExtractionResult(BaseModel):
    entities: list[ExtractedEntity]        # name, type∈EntityType, char_start, char_end
    relations: list[ExtractedRelation]     # src_name, rel_type∈RelationType, dst_name,
                                           # evidence_char_start, evidence_char_end
# Prompt contract (schema-constrained, L3 where backend supports):
# - extract ONLY types in the vocabulary; unknown → skip (never invent a type)
# - every entity/relation MUST carry character offsets into the provided text;
#   offsets become SourcePointer(doc_span) — extraction without evidence is rejected
#   by a deterministic validator (offsets must contain the entity name, fuzzy ≥0.9)
```

**Chunking:** ~600-token chunks with 1-sentence overlap (GraphRAG's TextUnit sizing — small enough for precise offsets, large enough for relational context). Chunk → extraction calls are batched; per-chunk results merged by the resolution layer.

**Edge cases handled here, deliberately:**
- *Coreference* ("she approved it"): POC does NOT resolve pronouns (open extraction trap); only named mentions extract. Stage 2 adds a coref pass scoped to within-document.
- *Negation/hypothetical* ("we will NOT migrate to X", "if we acquired Y"): prompt instructs skip; eval set includes negation traps (extracting a negated relation = scored failure).
- *Tables inside documents:* Markdown tables route to a deterministic table-parser first (rows → attrs), LLM sees prose only.

---

## 6. Entity Resolution (the make-or-break layer)

Fragmentation ("Nike" / "Nike Inc" / "NIKE" as three nodes) silently destroys graph value. Staged approach with measured gates:

**Stage 1 (POC): deterministic normalisation only.**
`normalised_name = lowercase → strip punctuation → fold legal suffixes (inc, ltd, pte, corp) → collapse whitespace`. entity_id = `f"{type}:{normalised_name}"` so upsert dedups exact-normalised matches for free. Known limitation, documented: "Bob Smith" ≠ "Robert Smith", "Postgres" ≠ "PostgreSQL". Acceptable for one-customer fixture corpora; NOT acceptable beyond — hence the Stage-2 gate.

**Stage 2: blocking + LLM judge.**
```
candidate pairs = union(
    same normalised_name prefix block,
    embedding KNN (name+attrs embedding, cosine ≥ 0.85),
    shared rare attribute (same email, same table reference)
)  →  LLM judge per pair (task_class entity_resolution_judge):
      response_model = {same_entity: bool, confidence: float, reasoning: str}
  →  merge if confidence ≥ 0.9: union source_pointers, keep richer name as display,
      rewrite relations, record merge as event (reversible — merges are logged, and
      because relations are bi-temporal the pre-merge state is reconstructable)
```
**Gate:** precision AND recall >90% on a hand-labelled pair set (build the label set DURING Stage 1 from the naive system's near-misses). **Tradeoff:** blocking misses cross-block duplicates; judge cost scales with pair count — blocking is exactly the lever trading recall for cost, and the gate keeps us honest about where it's set.

---

## 7. Upsert & Conflict Logic

```python
def upsert_relation(store: GraphStore, new: Relation) -> UpsertOutcome:
    active = store.active_relations(new.src, new.dst, family_of(new.rel_type))
    for old in active:
        if contradicts(old, new):          # e.g. OWNS by different src for same dst,
                                           # or attr-valued relation with different value
            if new.created_at >= old.created_at:        # POC rule: newest wins
                store.invalidate(old, at=now())          # expired_at=now, t_invalid=new.t_valid
            else:
                return UpsertOutcome.KEPT_EXISTING
    if exact_duplicate(active, new):
        store.append_pointers(active_match, new.source_pointers)  # corroboration
        return UpsertOutcome.CORROBORATED
    store.insert(new)
    return UpsertOutcome.INSERTED
```
Stage 2 upgrades `newest wins` → source-authority ranking (HR system beats a Slack mention for employment facts; config per customer) + contradiction surfacing (both kept active, flagged, `ask_human`). The bi-temporal substrate makes this upgrade additive — which is precisely why bi-temporal ships in POC despite naive conflict logic (ADR-0003).

---

## 8. Retrieval & Answering

```
question ──► three rankers in parallel:
   (a) vector: question embedding vs entity-card embeddings
       (entity card = name + type + top attrs + 1-hop relation summary, re-embedded
        on change by a minion-style job — deterministic trigger, no LLM)
   (b) BM25: DuckDB FTS over entity cards + DocumentRecord text
   (c) graph: seed = (a)∪(b) top-k entities → 1–2 hop traversal, ACTIVE edges only
       (expired_at IS NULL) unless query carries as_of=timestamp → temporal filter
 ──► Reciprocal Rank Fusion (k=60) over the three lists
 ──► answer synthesis (task_class data_to_text or direct): MUST cite — every claim
     in the answer maps to entity/relation IDs whose source_pointers are returned.
     Uncited claims are stripped by a deterministic post-pass (P4 applied to answers).
```
**Why hybrid+RRF, not vector-only:** multi-hop questions ("which projects depend on systems Alice owns?") are traversals, unanswerable by chunk similarity; BM25 catches exact identifiers (ticket numbers, table names) embeddings blur; RRF is parameter-free fusion robust to any ranker being weak — three mediocre rankers fused beat one tuned ranker on heterogeneous corpora, and we have a maximally heterogeneous corpus.

**The gbrain proof point** shapes ambition here: 97.6% R@5 with NO LLM in the retrieval loop. Our retrieval is likewise LLM-free up to synthesis; the LLM only narrates already-retrieved, already-cited results. If synthesis is disabled (airgapped minimal mode), `graph ask` returns the raw cited entity/relation list — still useful, zero tokens.

---

## 9. Agent Access (MCP, Stage 2 — designed now)

Tools exposed: `brain_ask(question, as_of?)` → cited answer; `brain_search(query, types?)` → entity list; `brain_traverse(entity_id, rel_types?, hops≤2)`; `brain_provenance(claim_id)` → pointers. Read-only by design — agents query the brain; only the ingestion pipeline writes (single-writer integrity; an agent that "fixes" the graph mid-conversation is an audit nightmare). ACL: MCP session carries a principal; Stage-2 permissioning filters entities whose source ACLs exclude the principal (Onyx mirroring pattern).

---

## 10. Evaluation (binding gates from 01_PRD §6, specialised)

| Eval | Method | POC gate | Stage-2 gate |
|---|---|---|---|
| E4 retrieval | 30-Q set: 10 single-hop, 10 multi-hop, 10 temporal ("what was true before X") over fixture corpus | R@5 ≥70%, citations valid 100% | ≥85% |
| Extraction precision | 50 hand-checked extractions per source kind; negation-trap subset | precision ≥85%; negation traps 0 extracted | ≥92% |
| ER quality | labelled pair set | n/a (naive, documented) | P>90% AND R>90% |
| Temporal correctness | scripted ingest sequence with known supersessions; as_of queries | 100% (logic, not ML) | 100% |
| Incremental cost | re-ingest with 5% changed docs | n/a | <10% of full-build tokens/cost |
| E6 parity | E4 + extraction under airgapped profile (Qwen3-72B) | gap measured & published | gap <10pp |

---

## 11. Failure Modes Register (design-time, each with its mitigation hook)

1. **Graph noise compounding** — low-confidence LLM edges accumulate → retrieval degrades. Mitigation: confidence floor for traversal participation (default 0.7, config), corroboration count surfaced in ranking.
2. **Extraction cost runaway on big corpora** — chunk-count × model price. Mitigation: budget in world state (§4), per-source token caps, deterministic pre-filters (skip boilerplate docs by hash/template detection).
3. **Stale entity cards** — graph changed, embeddings didn't. Mitigation: card re-embed is hash-triggered in the same transaction batch as upsert; eval asserts card/graph consistency.
4. **Source authority confusion** — Slack rumour overwrites HR fact. POC: doesn't overwrite silently (corroboration vs contradiction logged); Stage 2: authority ranking.
5. **PII leakage into graph attrs** — collector redaction is the choke point (Task 3 pattern extended to all collectors); extraction prompt re-states the redaction tokens are not to be "guessed through."
