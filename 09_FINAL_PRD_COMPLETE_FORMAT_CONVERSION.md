# 09 — FINAL PRD: Complete Data Format Conversion Pillar (Stage 1)
# Version 2.0 · SUPERSEDES the conversion scope in 01_PRD §5, 02_TRD Tasks 9–10/Part C, 05_PRD §§4.2/7
# Status: build-ready · All principles P1–P7 from 01_PRD remain binding
# Audience: Claude Code implementation sessions + engineering team. No undocumented context required.

---

## 1. Executive Summary

This document expands the Format Conversion pillar to **full Stage 1 coverage**: all four conversion directions (S→S, U→S, S→U, U→U), first-class Excel and SQL subsystems including natural-language-to-SQL, a complete format-support matrix with honest lossiness declarations, the canonical intermediate representation (IR) architecture that prevents N×N adapter explosion, extended provenance down to Excel cells and OCR bounding boxes, one immutable PipelineArtifact contract for every direction, a full validation/quarantine framework, schema-drift handling, security hardening, named evaluation suites with CI gates, 17 mandatory end-to-end demonstrations, an ordered 24-task implementation plan with dependency graph, and a revised honest timeline (the old 6-week estimate is **withdrawn**; see §30).

The core decision driving everything: **Stage 1 establishes the complete conversion architecture.** Later stages improve scale, connector count, permissions, and enterprise hardening — they never introduce a missing conversion direction. A customer in week one can convert in any direction, with reduced format breadth, not reduced capability class.

---

## 2. Product Promise

> Point DataBridge at data in any supported format. It understands what the data means, converts it into any supported target format — deterministically where possible, grounded where generation is required — and every output value carries a verifiable pointer to its source. The same confirmed artifact runs in bulk, in preview, in replay, and on a single record at inference time, producing identical results. It runs entirely inside your perimeter with any model, including local ones.

The promise has four load-bearing words: *understands* (semantic layer), *traceable* (provenance, P4), *identical* (artifact determinism), *any model* (P1/P2). Every section below exists to keep one of those words true.

---

## 3. Stage 1 Scope

**In scope (complete capability classes):**
- All four conversion directions with separate engineering mechanisms (§§10–13)
- Format adapters per the support matrix (§5) — every row marked "S1"
- Excel as a first-class subsystem (§14)
- SQL subsystem including NL→SQL with safety validation (§15)
- Canonical IRs and the adapter architecture (§7–8)
- Typed routing with fail-loud semantics (§9)
- Extended provenance (§17), PipelineArtifact v1 (§18), validation/quarantine (§19)
- Schema-drift safe-refusal + migration reports (§20)
- Security baseline (§21), performance baseline (§22)
- All evaluation suites (§23) wired into CI
- CLI + thin REST API (§24); the Phase-A demo layer from 08_PRD remains optional garnish

**Stage 2/3 (improvement, never new direction):** Airbyte connector breadth, meetings ingestion at scale, entity resolution upgrades, permissioning/RBAC, web confirmation UI, distributed execution (Spark/Ray), CDC/streaming sources beyond chunked batch, FHIR/ISO-20022 domain adapters, fine-tuned local annotation models, artifact-server HA deployment.

---

## 4. Explicit Non-Goals

1. **Universal lossless conversion.** Impossible; the matrix (§5) declares lossiness per pair, and lossy conversions emit loss reports. Claims otherwise are bugs in marketing, not features in engineering.
2. **Macro/VBA execution.** Never. Detected → stripped → warned (§14.11, §21).
3. **Write support for legacy binary formats** (.xls write, .doc, .msg write). Read-only or refused.
4. **OCR model training.** We route to existing OCR engines; we do not improve them.
5. **A general workflow orchestrator.** Conversions are jobs; scheduling beyond simple batch belongs to the customer's orchestrator (Airflow et al.) calling our CLI/API.
6. **LLM-executed structured transforms.** P3. Confirmed transforms replay deterministically forever.
7. **Editing source systems.** All source access is read-only (§21.1). DataBridge writes only its own outputs and artifacts.

---

## 5. Format-Support Matrix

Legend — **R**: read, **W**: write, **S1**: Stage 1, **IR**: which canonical IR it maps to (§7). **Lossless** means round-trip through the IR preserves all data values and declared structure; formatting/styling loss is itemised. Unsupported features ⇒ explicit warning (job continues, loss report) or refusal (job stops) as marked.

### 5.1 Structured & semi-structured

| Format | R | W | IR | Lossless? | Library (S1) | Notes / explicit limitations |
|---|---|---|---|---|---|---|
| CSV | S1 | S1 | RowBatch | values yes; no types in format | Arrow CSV reader (+ chardet for encoding sniff) | dialect sniffing (delimiter, quoting); encoding fallback chain utf-8→utf-8-sig→latin-1 with warning; CSV-injection guard on write (§21.8) |
| TSV | S1 | S1 | RowBatch | as CSV | same | treated as CSV dialect |
| JSON | S1 | S1 | TypedRecord | yes (within JSON types) | stdlib + orjson | top-level array vs object handled; numbers >2^53 preserved as Decimal with warning |
| JSONL/NDJSON | S1 | S1 | TypedRecord stream | yes | orjson, line-streamed | malformed line ⇒ row quarantine, never abort batch |
| YAML | S1 | S1 | TypedRecord | anchors/aliases EXPANDED (lossy, warned); custom tags refused | ruamel.yaml (safe loader only) | billion-laughs guard via depth/alias limits (§21.9) |
| XML | S1 | S1 | TypedRecord | structure yes; mixed-content text+children flagged lossy-ordering | lxml with resolve_entities=False | XXE disabled hard (§21.9); attribute/element mapping convention documented in adapter |
| Excel .xlsx | S1 | S1 | WorkbookRecord | see §14 fidelity table | python-calamine (read), openpyxl (write + metadata) | full subsystem §14 |
| Excel .xls | S1 (R only) | — | WorkbookRecord | values+cached formulas only | python-calamine | no styles, no write; warned |
| Excel .xlsb | S1 (R only) | — | WorkbookRecord | values only | pyxlsb | warned: formulas as cached values only |
| Parquet | S1 | S1 | RowBatch | yes | PyArrow | the preferred bulk interchange |
| Arrow IPC/Feather | S1 | S1 | RowBatch | yes (it IS the IR) | PyArrow | zero-copy |
| Avro | S1 | S1 | RowBatch | yes incl. schema evolution metadata | fastavro | logical types mapped to Arrow types; unsupported logical type ⇒ refusal |
| ORC | S1 (R) | S1 (W, basic) | RowBatch | yes | PyArrow ORC | write: no bloom-filter/stripe tuning S1 (warned) |
| SQL tables / query results | S1 | S1 | RowBatch | yes | SQLAlchemy + driver; Arrow ADBC where available | §15 |
| PostgreSQL | S1 | S1 | RowBatch | yes | psycopg3 (+ADBC) | read-only role enforced for sources |
| SQLite | S1 | S1 | RowBatch | yes | stdlib sqlite3 / SQLAlchemy | |
| DuckDB | S1 | S1 | RowBatch | yes | duckdb | also the execution engine (§10) |
| Generic SQLAlchemy source | S1 (R) | S1 (W where dialect supports) | RowBatch | type fidelity per-dialect; exotic types → string with warning | SQLAlchemy 2.x | "technically defensible" = dialect in sqlglot's dialect list AND types map to Arrow; else refusal naming the dialect |
| HTML tables | S1 (R) | S1 (W) | RowBatch | values yes; colspan/rowspan normalised (warned) | lxml/pandas.read_html equivalent via lxml | one RowBatch per table; table index in provenance |
| Markdown tables | S1 | S1 | RowBatch | values yes (all strings unless coerced) | own parser (trivial grammar) | alignment row ignored; ragged rows quarantined |

### 5.2 Unstructured documents

| Format | R | W | IR | Notes |
|---|---|---|---|---|
| PDF (text) | S1 | S1 (controlled, §13.4) | DocumentRecord | pypdfium2 text+layout; layout-aware blocks; per-char→span mapping retained |
| PDF (scanned) | S1 via OCR | — | DocumentRecord(+MediaRecord pages) | OCR routing §11.3; bbox provenance |
| DOCX | S1 | S1 | DocumentRecord | python-docx both ways; tracked changes: accepted-state only, warned |
| PPTX | S1 (R) | — | DocumentRecord | python-pptx; slide→section, shapes reading-order heuristic, warned lossy |
| Markdown | S1 | S1 | DocumentRecord | the document IR's native serialisation |
| Plain text | S1 | S1 | DocumentRecord | encoding sniff as CSV |
| HTML pages | S1 | S1 | DocumentRecord | readability-style main-content extraction; full-DOM mode flag |
| Email .eml | S1 | — | DocumentRecord + attachments fan-out | stdlib email; headers→metadata; each attachment re-enters routing as its own source (§11.6) |
| Email .msg | S1 (R only) | — | same | extract-msg lib; warned best-effort |
| Scanned images (png/jpg/tiff) | S1 via OCR | — | DocumentRecord | §11.3 |
| Audio with transcript available | S1 | — | TranscriptRecord | transcript file or sidecar consumed; **audio STT itself is Stage 2** — S1 requires an existing transcript (assumption marked) |

### 5.3 AI & data targets

| Target | W | Built from | Lossless w.r.t. inputs | Notes |
|---|---|---|---|---|
| OpenAI chat/SFT JSONL | S1 | internal ConversationSet | n/a (generated/reformatted) | §12 |
| ShareGPT | S1 | same | n/a | one internal form, two serialisers |
| Instruction/input/output datasets | S1 | same | n/a | alpaca-style |
| RAG chunks + metadata + provenance | S1 | DocumentRecord | n/a | chunker §12.4 |
| Tabular ML dataset (features+labels) | S1 | RowBatch + semantics | deterministic | §10.9 minimal-viable in S1 (see reconciliation §31) |
| Train/val/test splits | S1 | dataset | deterministic | time-based for temporal data, enforced |
| Feature tables (Parquet + manifest) | S1 | RowBatch | yes | |
| PyTorch-compatible tabular (tensors via Arrow→torch) | S1 | feature table | yes | export script + manifest; no torch dependency in core (optional extra) |
| Graph node/edge tables | S1 | RowBatch + relationship annotations | yes | §10.10 |
| PyTorch Geometric export | S1 (minimal) | node/edge tables | yes | HeteroData builder as optional extra; CPU-only |
| Knowledge-graph import (our graph store ingest format; GraphML export) | S1 | GraphRecord | GraphML: attrs stringified (warned) | |
| API request/response JSON per JSON Schema | S1 | TypedRecord + schema | yes | schema-validated serialisation |
| Human-readable reports | S1 | ReportIR (§13.4) | n/a | |
| Markdown summaries | S1 | ReportIR | n/a | |
| DOCX reports | S1 | ReportIR→python-docx | n/a | |
| Excel reports | S1 | WorkbookRecord builder | n/a | §14.10 |
| PDF reports | S1 | ReportIR→HTML→PDF | n/a | renderer choice = open decision OD-2 (§33); default WeasyPrint, bundled fonts (airgap) |

**Matrix rules:** any (source,target) pair routes via IRs; no direct format-to-format code paths exist (§7). Any request touching a cell of this matrix not marked S1 ⇒ refusal naming the unsupported feature and the nearest supported alternative.

---

## 6. Conversion-Direction Matrix

| Direction | Mechanism (the only one) | LLM role | LLM forbidden from | Primary validators | §§ |
|---|---|---|---|---|---|
| S→S | DuckDB SQL / JSONata over IRs; coercion catalogue | propose mapping once (plan mode) | execution path of confirmed transforms | round-trip, row-count law, E3, drift | 10 |
| U→S | deterministic pre-extract → Pydantic structured extraction → grounding validator | extract fields from genuinely unstructured spans | inventing values absent from source; bypassing grounding | source-grounding, cross-field, schema | 11 |
| S→U | fact slots → template-first grounded generation → claim verification → deterministic rendering | compose language over supplied facts; choose templates | introducing any fact; altering numbers/dates/names | E2 provenance, claim verifier, E3 drift | 12 |
| U→U | container conversion (deterministic) ∣ prose generation (grounded) — two sub-modes, never merged | summarise/rewrite/translate with citation preservation | silent meaning change; dropping citations | loss report, citation-preservation, length/coverage | 13 |

---

## 7. Canonical Intermediate Representations

The IR layer is the answer to N×N explosion: **A adapters in + B adapters out = A+B implementations, not A×B**, because every adapter speaks only to an IR. Conversions are IR→IR (the four directions) plus adapter work at the edges.

```python
# packages/convert/ir/models.py — all Pydantic v2, frozen where noted
class RowBatch:                  # tables. Backed by Apache Arrow RecordBatch.
    schema: ArrowSchemaRef       # + per-column SemanticType when annotated
    data: pa.RecordBatch
    snapshot: SnapshotIdentity   # §17.2
    column_provenance: dict[str, SourcePointer]   # column-level default pointer
    # row-level pointers derived: snapshot + row ordinal/PK

class TypedRecord:               # JSON-like trees with types preserved
    root: JsonValue              # str|int|Decimal|bool|None|list|dict, depth-limited
    json_schema_ref: str | None
    pointer_map: dict[JsonPath, SourcePointer]    # sparse; root pointer mandatory

class DocumentRecord:            # canonical doc = Markdown + structural blocks
    document_id: str; sha256: str
    blocks: list[Block]          # Block = Heading|Paragraph|Table|ListBlock|Code|
                                 #         Image(ref MediaRecord)|PageBreak
    char_index: CharIndex        # block→(char_start,char_end) in canonical text
    page_map: list[PageSpan] | None      # PDFs: page→char range
    source_meta: dict            # headers for email, etc.

class WorkbookRecord:            # §14 — NOT a DocumentRecord
    workbook_id: str; sha256: str
    sheets: list[SheetRecord]    # SheetRecord: name, visibility, used_range,
                                 #   cells (typed), merged_ranges, named_ranges_local,
                                 #   tables (ListObjects), header_inference: HeaderGuess
    named_ranges_global: dict
    metadata: WorkbookMeta       # author, created, app, has_macros: bool

class TranscriptRecord:
    transcript_id: str; segments: list[Segment]   # Segment: speaker, t_start_ms,
                                                  #   t_end_ms, text, char span
class MediaRecord:               # images/page rasters for OCR provenance
    media_id: str; sha256: str; kind: Literal["image","page_raster"]
    width: int; height: int; page_no: int | None

class GraphRecord:               # nodes/edges tabular form feeding graph exports
    nodes: RowBatch; edges: RowBatch
    node_id_col: str; src_col: str; dst_col: str

class ConversationSet:           # internal SFT form; serialised to OpenAI/ShareGPT/alpaca
    conversations: list[Conversation]   # turns carry facts_used + pointers

class ReportIR:                  # §13.4 — approved grounded content, render-ready
    blocks: list[Block]          # same Block grammar as DocumentRecord
    claims: list[Claim]          # claim text span → fact pointers (frozen post-approval)

class DatasetManifest:           # accompanies every dataset output
    rows: int; columns: list[ColumnSpec]; splits: dict | None
    label_spec: LabelCandidateRef | None; lineage: LineageRef; scorecard: EvalRef
```

`GroundedValue` and `SourcePointer` come from core (extended in §17). **Every IR carries provenance natively** — an adapter that constructs an IR without pointers fails construction, not validation.

### Architecture flow (the spine every conversion follows)

```
source adapter → canonical IR → semantic interpretation (consume AnnotatedSchema;
   refuse-if-unconfirmed rules §16) → conversion planner (deterministic template
   selection + optional LLM proposal in plan mode) → execution mechanism
   (DuckDB | JSONata | Pydantic-extract | template+slot | renderer | graph exporter)
   → validators (§19) → target adapter → versioned PipelineArtifact (§18)
```

---

## 8. System Architecture

```
packages/convert/
├── router/        # ConversionRequest, capability resolution, fail-loud (§9)
├── ir/            # §7 models + Arrow utilities + CharIndex
├── adapters/      # source/ & target/ — one module per format; AdapterInfo
│   ├── source/    #   csv.py json.py yaml.py xml.py excel.py sqlsrc.py pdf.py
│   │              #   docx.py pptx.py html.py email.py image_ocr.py md.py ...
│   └── target/    #   csv.py json.py parquet.py excel.py sqltgt.py sft.py
│                  #   rag.py report_md.py report_docx.py report_pdf.py graph.py ...
├── rules/         # S→S: proposer, validator, coercions, engine (DuckDB/JSONata)
├── extract/       # U→S: pre-extractors, ocr_routing, pydantic_extract, grounding
├── generate/      # S→U: fact_slots, templates, synthesiser, claim_verifier
├── transform_u2u/ # U→U: container vs prose sub-modes, loss reports
├── sqlsub/        # §15: nl2sql, sqlglot safety, dialects, query library
├── excelsub/      # §14: calamine reader, openpyxl writer, header inference
├── render/        # ReportIR → md/docx/pdf/xlsx renderers (deterministic)
├── artifacts/     # PipelineArtifact store, replay, infer, migration
├── validators/    # §19 framework; policy engine
├── quarantine/    # row/record/claim quarantine store + reports
└── evals/         # §23 suites
```

Adapters register `AdapterInfo{format, modality, read:bool, write:bool, ir, lossiness, limits[]}` — the router's capability table IS this registry serialised; the §5 matrix is generated from it (doc-from-code, cannot drift).

---

## 9. Routing

```python
class TargetSpec(BaseModel):
    modality: Literal["structured","document","conversation","dataset","graph","report"]
    format: str                              # registry key, e.g. "parquet","sft:openai"
    schema_ref: str | None                   # JSON Schema | Pydantic ref | SQL DDL
    options: dict = {}

class ConversionRequest(BaseModel):
    source_ref: str                          # registered source or prior-output handle
    target: TargetSpec
    mode: Literal["plan","preview","execute","infer","validate"]
    execution: Literal["batch","streaming"] = "batch"
    artifact_id: str | None                  # required for execute/infer of confirmed plans
    as_of: datetime | None                   # snapshot pinning

class RoutingDecision(BaseModel):
    direction: Literal["s2s","u2s","s2u","u2u"]
    subsystem: str
    needs_semantic_confirmation: bool        # §16 rules
    needs_generation: bool
    lossiness: Literal["lossless","lossy"]; loss_items: list[str]
    needs_human_confirmation: bool           # new mapping/template/SQL ⇒ True
    refusals: list[Refusal]                  # non-empty ⇒ raise UnroutableConversion
```

Routing logic, in order: (1) resolve source adapter + IR + modality from registry; (2) resolve target; (3) direction = (source modality × target modality); (4) consult semantic-confirmation rules (§16) — semantic-dependent + unconfirmed schema ⇒ refusal `SEMANTICS_UNCONFIRMED`; (5) lossiness computed from both adapters' declarations — lossy ⇒ proceed with loss report UNLESS `options.require_lossless` ⇒ refusal; (6) generation-needed ⇒ verifier pipeline mandatory; (7) mode gates: `execute`/`infer` REQUIRE a confirmed artifact (or a syntactic-only fast path, §16.3); `plan` may call the LLM proposer; `preview` runs the plan on samples only; `validate` runs validators against existing output.

**Fail-loud guarantees:** no silent downgrade (a refused lossless request never silently becomes lossy); unknown format ⇒ refusal listing supported formats; streaming requested where an adapter is batch-only ⇒ refusal naming the adapter limit. Every refusal is a typed `Refusal{code, message, nearest_alternative}` — testable, and the frontend renders it verbatim.

---

## 10. Structured → Structured (complete spec)

**Purpose:** reshape, retype, combine, and re-serialise structured data with zero generation. **Inputs:** RowBatch/TypedRecord(+WorkbookRecord sheets) sources. **Outputs:** any structured/dataset target. **Mechanism:** DuckDB SQL for relational ops; JSONata for tree reshaping; both produced in `plan`, executed deterministically forever after (P3).

### 10.1 Operation catalogue (each maps to a tested pattern in the engine)
column mapping/renaming · type coercion (catalogue §10.3) · filtering · sorting · joining (inner/left/anti; key type-compat checked) · grouping/aggregation · window calculations (DuckDB windows; frame spec recorded) · flattening (struct→columns; JSONata `**`) · nesting (columns→struct) · pivot/unpivot (DuckDB PIVOT) · exploding arrays (UNNEST; ordinality column added for provenance) · deduplication (declared keys; full-row hash fallback; dropped rows → quarantine class `dedup`, counted never silent) · referential-integrity checks (FK orphan detection → quarantine or hard fail per policy) · enum mapping (full value table required; unseen value at execute ⇒ row quarantine) · null/sentinel handling (per-column sentinel lists) · currency (minor-units aware: JPY=0, BHD=3) and locale numerals ("1.234,56") · time zones (tz-aware→UTC canonical; naive ⇒ policy reject|assume_utc, recorded) · unit conversion (named factors only — e.g. `kg_to_lb`; arbitrary math in mappings is refused by the proposal validator).

### 10.2 Lifecycle
`plan` (LLM proposer constrained to: operation catalogue + coercion names + `unmappable[]` channel) → deterministic validation (sqlglot/JSONata parse; sample run; target-schema validation; row-count law per declared cardinality; E3 checks) → human confirm (P6; library hit skips) → `execute` via DuckDB over Arrow (chunked, §22) → artifact.

### 10.3 Coercion catalogue
Central, named, exhaustively tested (`rules/coercions.py`): `str_to_decimal(locale)`, `epoch_to_iso(unit_detect)`, `iso_normalise(tz_policy)`, `sentinel_to_null(list)`, `bool_coerce(map)`, `enum_map(table)`, `currency_minor_units(ccy)`, `id_cast` (leading zeros preserved — ids are strings), `unit_convert(name)`, `trim_normalise_ws`, `case_fold`. Mappings reference coercions **by name**; the artifact records exactly which ran with which params. Adding a coercion without tests fails CI.

### 10.4 Schema evolution & incremental
Artifact pins source/target schema hashes (§20). Incremental conversion S1 = chunked re-run over changed partitions identified by snapshot diff (content hash per partition); true CDC is Stage 2. Streaming = Arrow batch iterator end-to-end where both adapters declare streaming support; else refusal.

### 10.5 Row-level quarantine & round-trip
Any row failing coercion/integrity ⇒ `quarantine.parquet` + reason code; threshold (default 1%) fails the run. Round-trip validation: for declared-lossless pairs, A→IR→A' must be value-identical (eval EV-RT).

### 10.9 Tabular ML dataset (S1 minimal-viable)
Composes 10.1: joins + as-of temporal aggregations (no-future-leakage law: every aggregate window ends at the example's anchor timestamp) + label attachment from a **confirmed** LabelCandidate + time-based split + DatasetManifest + imbalance report. Leakage detector S1 = |corr|>0.95 flag + timestamp-after-label refusal using semantic timestamp annotations. (Reconciliation with old Stage-3 placement: §31-C4.)

### 10.10 Graph export (S1 minimal-viable)
AnnotatedSchema roles → GraphRecord: entity tables → node tables (typed features via 10.9 column pipeline); FK/junction → edge tables with typed edges; event timestamps → edge attrs. PyG `HeteroData` builder behind optional extra `databridge[pyg]`; GraphML export with attr-stringification warning.

**Failure modes:** silent lossy coercion (→ catalogue tests), proposer overreach (→ unmappable channel + validators + confirm), drift (→ §20 hard stop), dedup hiding data loss (→ quarantine accounting eval EV-QC).

---

## 11. Unstructured → Structured (complete Stage 1 subsystem)

**Purpose:** documents/emails/scans/transcripts → typed records against a supplied target schema (JSON Schema, Pydantic model, or SQL DDL — all three compiled to one internal `TargetRecordModel`). 

### 11.1 Deterministic pre-extraction (always first, P3)
Markdown/HTML tables → RowBatch directly; key-value patterns (`Label: value` blocks, definition lists); regex-able fields (emails, phones, ISO dates, currency amounts, invoice-number patterns supplied per schema field as optional `pattern`); DOCX/PDF embedded tables via layout extraction. Pre-extraction typically resolves 30–60% of fields at zero tokens; resolved fields are LOCKED — the LLM is told their values and forbidden to contradict them.

### 11.2 LLM structured extraction
`harness.complete_structured(response_model=TargetRecordModel, strict)` over the relevant DocumentRecord spans (span selection: schema-field descriptions embedded → retrieve top blocks; whole-doc fallback under a size ceiling). Required vs optional fields from the schema; missing required after all passes ⇒ record quarantine. Repeating records (line items): schema declares `x-repeats: row_anchor`; extraction returns arrays validated per-item. Multi-page records: page_map joins spans across pages; a record's pointers may span pages. Nested objects: Pydantic nesting natively.

### 11.3 OCR routing
`needs_ocr = page has < N extractable chars AND raster present`. S1 engine: **RapidOCR (ONNX, fully local — airgap-safe default) with Tesseract fallback**; both emit word boxes. DocumentRecord gains OCR blocks whose CharIndex maps to `bbox` provenance (§17). Image preprocessing deterministic: deskew, binarise (opencv-headless). Confidence per word retained → per-field confidence (§11.5). *(Engine choice = OD-3 if team prefers docling-bundled OCR.)*

### 11.4 Grounding validator (the load-bearing wall)
Every extracted value must appear in source: exact match for ids/numbers/dates after canonical formatting; fuzzy ≥0.9 token-set ratio for names/free text; for OCR sources, match against OCR text with confusion-tolerant comparison (0/O, 1/l) at ≥0.85 plus bbox existence. Fail ⇒ field nulled+flagged (optional field) or record quarantined (required). **No extracted value bypasses grounding. Ever.**

### 11.5 Confidence & review
`field_conf = f(extraction self-report, grounding match score, OCR word conf, pre-extract lock=1.0)` blended as in 06_PRD §4. Routing bands identical to semantic layer: ≥0.95 auto / 0.80–0.95 spot-check / <0.80 review. Cross-field validation from schema annotations (date ordering, sums-of-line-items≈total within tolerance, enum membership).

### 11.6 Email + attachments
.eml → DocumentRecord (body) + metadata (headers typed: from/to/cc/date/message-id) + each attachment **re-enters the router** as its own source with parent pointer — an invoice PDF inside an email yields records whose provenance chains email→attachment→page→span.

### 11.7 Batch & evaluation
Batch = parallel per-document with shared schema; per-doc outcome ∈ {ok, partial(flagged fields), quarantined}; batch report aggregates. Eval datasets: §23 EV-DX/EV-OCR with field-level P/R/F1.

**Stage 1 use cases (all are demos §27):** PDF invoice→validated JSON · DOCX contract→typed clauses · email+attachment→CRM record · irregular Excel→clean relational tables (via §14 + this subsystem for header chaos) · Markdown/HTML tables→Parquet · scanned form→typed record with bbox provenance.

---

## 12. Structured → Unstructured (complete spec)

**Purpose:** generate language artefacts whose every fact is supplied, never invented.

### 12.1 Fact slots
`fact_extractor` turns source rows/aggregates into `[F1..Fn] GroundedValue`s rendered by **deterministic formatters** (`render/formatters.py`: money, dates, names, percentages — canonical forms the validators string-match; hallucination defence, not cosmetics).

### 12.2 Template-first grounded generation
Generation order: (1) pick template (LLM may choose from a template library; templates are versioned artefacts); (2) slot-fill deterministic fields; (3) LLM composes connective prose with the contract: cite `facts_used` per sentence/turn; absent-fact ⇒ write the designated fallback ("data not available" / "let me check"), never a value. Style controls (tone, length, audience) are prompt parameters **walled off from facts**: the claim verifier runs after styling, so style cannot alter factual meaning undetected.

### 12.3 Verification stack (all outputs)
(a) deterministic claim check: every number/date/name token must exactly match a cited rendered fact (longest-match-first; "approximately"+number = violation in dataset outputs); (b) independent verifier model (different alias, task_class `verification_pass`): claim-level supported/unsupported judgement; unsupported ⇒ rejection to quarantine with reason; (c) E2 provenance completeness 100% hard gate; (d) E3 drift for synthetic sets (turn-length, sentiment proxy, amount KS).

### 12.4 Targets
- **SFT** (real-log reformatting path preferred — zero generation; synthesis path full stack) → ConversationSet → openai/sharegpt/alpaca serialisers.
- **RAG chunks**: deterministic chunker (recursive default, semantic optional), metadata = SourcePointers; companion eval-triple generator from the same documents.
- **Reports/summaries/emails/data dictionaries/API docs**: grounded content compiled into **ReportIR**, claims frozen at approval, then §13.4 deterministic rendering to md/docx/pdf/xlsx. Data dictionary = a template over AnnotatedSchema itself (the system documents the customer's data — cheap, loved demo). API docs = template over JSON Schemas.

---

## 13. Unstructured → Unstructured (complete spec)

Two sub-modes with different validators — conflating them is forbidden:

### 13.1 Container conversion (deterministic; no LLM)
PDF/DOCX/PPTX/HTML/Email → Markdown (source adapter → DocumentRecord → md serialiser); Document → another document format (DocumentRecord → docx/pdf renderer). Modes: `layout-preserving` (keep page breaks, table geometry where target allows) vs `semantic` (reading-order content, structure normalised). **Loss report mandatory**: itemised unsupported features (tracked changes flattened, PPTX animations dropped, merged-cell geometry simplified) attached to the output manifest. Validator: structural — block counts, table cell counts, char-coverage ratio ≥ threshold vs source IR.

### 13.2 Prose generation (grounded; LLM)
Summaries, cleanups, rewrites, translation. The source DocumentRecord's spans are the fact universe; the output must carry **citation preservation**: every output claim maps to source spans (same claim contract as §12.3); summaries additionally validated for coverage (key-entity recall vs source entities) and length bounds. Translation: meaning-preservation spot-check via back-translation similarity ≥ threshold (flag, not gate, S1) + citations survive (span pointers unchanged — they point at the SOURCE).

### 13.4 Rendering (shared with §12)
ReportIR → renderers: Markdown (native), DOCX (python-docx), XLSX (§14.10), PDF (ReportIR→HTML→WeasyPrint, bundled fonts, no network — OD-2). Rendering is deterministic: same ReportIR ⇒ byte-identical md/docx; PDF byte-identity is best-effort (timestamp fields zeroed; if the renderer can't guarantee it, the manifest says so — honest, per §2).

---

## 14. Excel Subsystem (first-class)

Excel is a WorkbookRecord, never "a document". Libraries (decision, justified): **python-calamine** for ALL reads (.xlsx/.xls/.xlsb; Rust-fast, handles huge workbooks, returns typed cells + cached formula values), **openpyxl** for .xlsx metadata enrichment (named ranges, tables/ListObjects, merged ranges, hidden flags, comments — calamine doesn't expose all) and ALL .xlsx writes, **pyxlsb** only if calamine's xlsb path proves insufficient (fallback, not default), **DuckDB/PyArrow** for bulk sheet→RowBatch transforms. *Why not pandas.read_excel:* hides typing decisions we must own.

Per-requirement spec:
1. **Multiple sheets / selection:** WorkbookRecord carries all sheets; `options.sheets=[...]|"all"|"visible"`; default visible-only with warning listing hidden.
2. **Header detection:** deterministic heuristic (first row with ≥80% non-null distinct strings over typed-data rows below) → `HeaderGuess{row_idx, confidence}`; confidence <0.9 ⇒ human confirm in plan mode (P6). Multi-row headers: detected (merged top row) → flattened with `_` join, recorded.
3. **Typed cells:** calamine types preserved → Arrow types; mixed-type columns ⇒ column-level warning + per-cell coercion via catalogue or string fallback (recorded).
4. **Empty rows/cols:** trimmed at used-range edges (recorded); interior empties preserved as nulls.
5. **Formulas vs cached values:** S1 reads **cached values** (calamine), `cell.has_formula=True` flag retained; recalculation is OUT of scope (no formula engine) — manifest warns "values as last saved". Refusal if `options.require_recalculated`.
6. **Dates/serials:** calamine date detection + explicit 1900/1904 epoch from workbook props; ambiguous numeric-that-might-be-date columns flagged, never auto-converted without semantic type `timestamp_*` or human confirm.
7. **Named ranges:** read into WorkbookRecord; addressable as sources (`source_ref="wb#name:Invoices"`).
8. **Excel tables (ListObjects):** preferred extraction unit when present (headers authoritative, no inference).
9. **Merged cells:** value lives at anchor; policy `fill_down|anchor_only|refuse` (default fill_down for header context, anchor_only for data, recorded per range).
10. **Hidden sheets/rows/cols:** read but flagged; included only by explicit option (audit honesty — hidden often means stale).
11. **Locale numbers / leading zeros:** text-formatted cells stay strings; `id_cast` preserves zeros; locale decimal handled by `str_to_decimal(locale)` with workbook-locale hint.
12. **Error cells (#N/A, #VALUE!, #DIV/0!):** typed as `ExcelError(code)` → policy null+warn (default) | quarantine row | hard fail.
13. **Cross-sheet references:** as formulas — cached values only; the reference text is retained in provenance derivation.
14. **Metadata:** author/created/modified/app captured into manifest.
15. **Output formatting / multi-sheet export (§14.10):** WorkbookRecordBuilder — typed columns, header style, freeze panes, number formats from semantic types (money→currency format, timestamps→date format), one RowBatch per sheet, TOC sheet optional. Formula injection guard on write (§21.8).
16. **Large workbooks:** calamine streams; per-sheet RowBatch chunking; 1M-row sheet target §22.
17. **Macros/VBA:** never executed; `has_macros=True` ⇒ warning + macro payload **not** copied to any output (.xlsm in → .xlsx out, recorded as loss).
18. **Password-protected:** refusal with code `ENCRYPTED_WORKBOOK` (no password handling S1; OD-4 whether to accept supplied passwords).
19. **Provenance:** `SourcePointer(kind="workbook_cell", workbook_sha, sheet, cell="B7"|range="A2:D90")` — cell-level for extracted fields, range-level for batch loads.

**Fidelity table (honest):** values+types lossless; formulas → cached values (lossy, warned); styles/charts/pivots → dropped (lossy, warned); macros → stripped (warned); comments → metadata sidecar.

---

## 15. SQL Subsystem (fully disambiguated)

1. **SQL as source:** SQLAlchemy/ADBC readers → RowBatch with SnapshotIdentity (server, db, query, as-of timestamp, content hash of result). Read-only credentials REQUIRED for external DBs (connection refused if the role can write — checked via dialect-specific privilege probe; probe failure ⇒ warning + proceed flagged).
2. **SQL as target:** writers emit INSERT batches / COPY / CREATE TABLE AS against customer-designated target schemas; never DDL/DML against source connections (separate target credential).
3. **SQL as deterministic transformation language:** DuckDB executes confirmed transforms over IRs (§10) — this SQL never touches customer servers.
4. **NL→SQL (Stage 1 — decision reversed from T17/Stage 2, see §31-C2):** schema linking from AnnotatedSchema (semantic types + roles + relationship names — linking on MEANING) → generation (task_class `sql_generation`) with dynamic few-shot from the confirmed-query library → sqlglot parse → safety validation (5) → read-only EXPLAIN cost ceiling → execute → Fixer ≤2 (syntax/runtime errors fed back verbatim) → Reviser ≤1 (semantic: GROUP BY vs "per X" intent, zero-row suspicion, NULL-heavy aggregates) → result + SQL-as-provenance (derived pointer whose derivation IS the query).
5. **Generated-SQL validation:** sqlglot AST allow-list — SELECT-only grammar: no DDL/DML/DCL nodes, no INTO, no system functions outside an allow-list, no multi-statement, identifier existence checked against the linked schema, LIMIT injected if absent.
6. **Self-correction:** as (4); every loop iteration logged as PipelineEvent; loop exhaustion ⇒ typed failure with the last error, never a guess.
7. **Read-only execution:** read-only role + sqlglot allow-list + statement timeout + row cap — four independent walls.
8. **Cost/timeouts:** EXPLAIN-based cost ceiling where the dialect provides it (PG yes; else heuristic: table stat row counts × join count bound); statement_timeout (default 30s) and row cap (default 1M) per source config.
9. **Schema linking:** §15.4; linking output recorded (the "schema slice" hash is part of the artifact).
10. **Dialects:** sqlglot for parse + transpile (e.g., confirmed-query reuse across PG/SQLite/DuckDB where transpilable); dialect not in sqlglot ⇒ refusal naming it (§5 rule).
11. **Injection prevention:** NL never interpolated into SQL strings; all literals bound parameters where the path is templated; generated SQL passes the AST allow-list which excludes stacked statements and comments-smuggling (`;--` patterns are unparseable into the allowed grammar).
12. **Training-serving reuse:** confirmed (question, schema-slice, SQL) triples are artifacts; `infer` mode re-binds parameters and re-executes the SAME SQL — NL→SQL runs ONCE per question shape, deterministically thereafter (the tokenizer principle applied to queries).

---

## 16. Semantic-Layer Integration

The conversion system consumes from AnnotatedSchema (06_PRD): **semantic types** (drive coercion defaults, Excel number formats, leakage detection, fact formatting) · **table roles** (graph export node/edge mapping; join sanity) · **relationship annotations** (join keys + names; graph edges) · **PII annotations** (redaction in samples/templates; PII columns excluded from generation fact slots unless explicitly confirmed) · **label candidates** (10.9; only `status=confirmed` usable; time-lag caveat enforced as a window refusal) · **human corrections** (few-shot retrieval improving proposers/extractors) · **confirmed schema versions** (artifacts pin them).

**Refusal rule (16.1):** any conversion whose plan depends on inferred semantics (semantic-type-driven coercion, label use, relationship-driven joins, PII-sensitive generation) REFUSES execute/infer until the relevant tables' annotations carry `confirmed_by` — error `SEMANTICS_UNCONFIRMED` listing the unconfirmed tables.

**Syntactic bypass (16.3):** conversions that are purely structural — declared-lossless container/format hops with no semantic decisions (CSV→Parquet with explicit dtypes, JSON→YAML, PDF→Markdown container mode, Arrow→ORC) — may run without semantic confirmation. The routing decision records `semantic_dependency: none`, and the bypass list is the closed set above; anything else defaults to dependent.

---

## 17. Provenance & Lineage (extended)

### 17.1 SourcePointer v2 (extends core; additive)
New kinds beyond 02_TRD: `query_cell` (snapshot + query hash + row ordinal + column) · `workbook_cell` (workbook_sha, sheet, cell/range) · `ocr_bbox` (media_id, page, x0,y0,x1,y1, ocr_conf) · `email_part` (message_id, part: body|header:<name>|attachment:<idx> + child pointer) · `transcript_span` (existing, + segment idx) · `generated_claim` (claim_id → list[fact pointers]). `derived` gains `agg_window` and **provenance compression**: aggregations over >K rows store `{snapshot, predicate, row_count, sample_row_ids[≤20], rows_digest}` instead of K pointers — verifiable (re-run predicate, compare digest) without storing millions of pointers.

### 17.2 Snapshot identity & hashes
`SnapshotIdentity{source_id, as_of, content_hash, schema_hash, query_hash?}` — every IR carries one; artifacts pin the schema_hash and declare snapshot requirements (e.g., "any snapshot with schema_hash=X").

### 17.3 Lineage DAG
PipelineEvents already hash inputs/outputs; `lineage.json` per run links event hashes into a DAG: source snapshots → IR constructions → plan → execution → validation → outputs. Output-to-input mapping: structured outputs carry column/row pointer maps; generated outputs carry claim-level citations. **A value without resolvable provenance = failed conversion** (validator EV-PV, hard gate) — restated as the pillar's first law.

---

## 18. Pipeline Artifacts (one contract, all directions)

```python
class PipelineArtifact(BaseModel, frozen=True):
    artifact_id: str; version: int; supersedes: str | None
    direction: Literal["s2s","u2s","s2u","u2u"]
    source_adapter: AdapterRef           # name + semver
    target_adapter: AdapterRef
    source_schema_hash: str; target_schema_hash: str
    semantic_schema: SemanticSchemaRef   # source_id + version + confirmed_by
    transform: TransformSpec             # sql|jsonata|extraction_spec|template_set|render_spec
    coercions: list[CoercionUse]         # name + params actually used
    parser_config: dict                  # adapter options (sheet selection, OCR engine...)
    model_routing: dict                  # task_class→model snapshot AT CONFIRMATION
    prompt_template_hashes: dict
    validation_rules: ValidationProfile  # thresholds + policies bound to this artifact
    fitted_params: FittedRef | None      # safetensors for arrays + params.json for scalars/
                                         # vocab maps — NEVER pickle (decision: unsafe
                                         # deserialisation is a §21 attack class)
    input_snapshot_requirements: SnapshotReq
    output_schema: SchemaRef
    eval_scorecard: EvalRef              # the scores at confirmation time
    approved_by: str; created_at: datetime
    code_commit: str; dependency_lock_hash: str
    checksum: str                        # sha256 over canonical serialisation
    compatibility: CompatRules           # which drift classes auto-migrate (§20)
    migration_history: list[MigrationRecord]
```
One artifact supports all five lifecycles: **preview** (samples), **bulk execute**, **replay** (determinism eval), **single-record infer** (the serving path — exists from day one), **audit** (render the artifact as a human-readable report — itself a §12 template), plus **migration** (§20). Immutability: changes create version n+1 with `supersedes`; serving refuses artifact/model-registry mismatches.

---

## 19. Validation & Quarantine Framework

`validators/` = a policy engine: each validator declares `{check, scope: value|row|record|column|dataset|output, default_policy}`; artifacts bind a ValidationProfile overriding defaults within allowed ranges.

**Deterministic validators:** syntax (parse target) · schema compliance · type correctness · required fields · enum validity · row-count law · cardinality (join fan-out bounds) · referential integrity · range checks · statistical distribution (KS, categorical containment) · round-trip (lossless pairs) · provenance completeness · duplicate records (declared keys) · missing records (count vs source per cardinality) · leakage (corr flag + timestamp-after-label refusal) · PII handling (PII-annotated values absent from outputs unless authorised) · schema drift (§20) · encoding corruption (mojibake heuristics: replacement-char rate, bigram sanity) · locale errors (mixed decimal conventions in one column) · date/time (impossible dates, tz mixing) · output readability (re-open every written file with its own reader — the cheapest most-skipped check).

**Model-assisted validators (flag, never silently fix):** claim verification (§12.3b) · summary coverage (§13.2) · extraction plausibility second-vote (<0.80 band).

**Failure policies (the only allowed outcomes):** `hard_fail` · `warning` (+ loss/issue report) · `row_quarantine` · `field_null+flag` · `human_review` · `retry(model path only, bounded)` · `artifact_migration_required`. **No silent dropping anywhere:** every excluded row/field/claim is counted in the run report with a reason code; quarantine stores carry the original payload + reasons; thresholds (default 1% rows, 0% provenance, 0% fabricated claims) fail runs.

---

## 20. Schema Drift & Evolution

Detection: execute/infer compares live source schema_hash + per-column fingerprints to artifact pins. Behaviour matrix:

| Drift class | Default | Auto-migration allowed when |
|---|---|---|
| Column added | warning + proceed (column ignored, recorded) | always (additive) |
| Column removed (unused by transform) | warning | always |
| Column removed (used) | **refusal + migration report** | never auto |
| Type widened (int→bigint, varchar(n)→text) | auto-migrate, record | declared-compatible in CompatRules |
| Type narrowed/changed | refusal + report | never auto |
| Enum value added | refusal at row level → quarantine unseen values; report suggests enum_map update | human-confirmed map update ⇒ artifact v+1 |
| Excel sheet renamed | refusal naming old/new candidates (fuzzy match suggestion) | human confirm |
| JSON nesting changed | refusal + JSONPath diff | never auto |
| Document template changed (U→S layouts) | extraction confidence collapse triggers review flood ⇒ run flagged, report | re-confirm extraction spec |
| SQL dialect changed | sqlglot transpile attempt → preview diff → human confirm | transpile clean + tests pass |
| Target schema evolved | new artifact version required | — |

The **migration report** is structured (machine-readable diff + suggested artifact edits) and is itself a §12 grounded document. Safe refusal is the default; auto-migration is the narrow, declared exception.

---

## 21. Security (Stage 1 baseline)

1. **Read-only source credentials** (privilege probe §15.1) · 2. **SQL allow-listing** (sqlglot AST grammar §15.5) · 3. **Timeouts/row caps** (§15.8) · 4. **Generated-code sandboxing:** the Python parser sandbox (used only when an extraction genuinely needs bespoke parsing) runs subprocess-isolated: no network (socket-denied), read-only FS except workdir, CPU/mem/time rlimits, allow-listed imports — and every sandbox program is a reviewed artifact, not ad-hoc execution · 5. **Decompression bombs:** size/ratio/depth limits on zip-likes (xlsx, docx ARE zips) · 6. **Malicious spreadsheets:** macros never executed (§14.17); external-link refresh disabled; formula payloads inert (cached values only) · 7. **Formula/CSV injection on WRITE:** cells beginning `= + - @ \t` get apostrophe-prefix guard in csv/xlsx writers (test EV-SEC) · 8. **XML attacks:** lxml entity resolution off, DTD off, huge-tree limits · 9. **Path traversal:** all adapter file access through a jailed resolver (no `..`, symlink escape checks) · 10. **Unsafe deserialisation:** no pickle anywhere in artifacts or IPC (safetensors/JSON, §18) · 11. **Password-protected files:** refused (OD-4) · 12. **PII redaction:** collector + sample redaction (02_TRD) extended to all adapters; PII annotations gate outputs (§19) · 13. **Egress policy:** airgapped profile = harness refusal + socket-deny CI test (07_PRD) now ALSO covers OCR (local engines only) and PDF rendering (bundled fonts) · 14. **Air-gapped execution:** every S1 capability functions with local models + local engines; the matrix column "works airgapped" is implicit YES for all S1 rows — any exception is a refusal, not a degradation.

---

## 22. Performance & Scalability (POC targets — assumptions marked)

Assumed hardware: 8-core/32GB single node (the POC machine). Arrow chunked streaming end-to-end; DuckDB out-of-core for spills; process-pool for document batches; content-hash caching (07_PRD) keyed per IR construction and per LLM call.

| Workload | S1 target | Mechanism |
|---|---|---|
| Small files (<10MB) | <2s end-to-end | direct |
| CSV/Parquet 10GB | sustained ≥150MB/s convert (S→S, no LLM) | Arrow streaming + DuckDB |
| Excel 1M rows × 30 cols | read→Parquet <60s | calamine streaming |
| PDF 500 pages (text) | →Markdown <30s | pypdfium2, pooled |
| OCR scanned page | <3s/page CPU | RapidOCR ONNX |
| DB table 50M rows | chunked 100k-row batches, bounded memory <4GB | ADBC/server cursors |
| U→S extraction batch | throughput = model-bound; pipeline overhead <15% | measured via E7 |
| Backpressure/cancel/resume | all batch jobs checkpoint per chunk via event log; cancel-safe; resume skips completed chunk hashes | events as checkpoints |

Parallelism defaults: min(cores, 4) for doc adapters; DuckDB self-managed; LLM concurrency per-backend config. These are *targets with assumptions*, validated by EV-PERF; misses are reported, not hidden.

---

## 23. Evaluation Suites (named, gated)

Every suite: fixtures described, metric, threshold, deliberately-broken **canary** (must fail or the run is invalid), CI tier per 07_PRD §8. Field-level P/R/F1 wherever extraction is judged.

| Suite | Fixtures | Metric | Gate | Canary |
|---|---|---|---|---|
| EV-SF structured-format correctness | 40 cases across §5.1 pairs incl. encodings, big ints, tz | value-exactness | 100% | corrupted UTF-16 file must fail |
| EV-XL Excel fidelity | the Gauntlet Workbook: multi-sheet, merged, hidden, serial dates 1900/1904, errors, locale numbers, leading zeros, ListObjects, named ranges, 1M-row sheet | cell-value accuracy; header-detect accuracy | 100% values; ≥95% header auto (rest confirmed) | swapped-epoch fixture must flag |
| EV-SQL safety + accuracy | 60 NL questions over 2 eval schemas + injection corpus | execution accuracy; unsafe-SQL block rate | ≥70%; 100% blocked | stacked-statement fixture must refuse |
| EV-DX document extraction | 30 docs: invoices, contracts, emails+attachments | field P/R/F1 | F1 ≥0.85 (text-PDF), per-field reported | fabricated-field seed must be caught by grounding |
| EV-OCR | 20 scans (clean/skewed/noisy) | field F1 + bbox presence | F1 ≥0.75; bbox 100% of extracted fields | O/0 confusion trap measured |
| EV-PV provenance completeness | every generated/converted output in CI | resolvable-pointer rate | 100% hard | pointer-stripped seed must fail |
| EV-GF grounded-generation factuality | adversarial absent-fact + numeric paraphrase suites | fabrications escaping | 0 | seeded fabrication must be caught |
| EV-RT round-trip | all declared-lossless pairs | value identity | 100% | lossy-pair mislabel must fail |
| EV-SD drift detection | mutation suite per §20 classes | correct class + correct policy | 100% | silent-proceed seed must fail |
| EV-DET determinism/replay | every artifact type, cache-on vs off, two machines | byte-identity (PDF: manifest-declared) | 100% | clock-dependent template must fail |
| EV-LP local parity | EV-DX/EV-SQL/EV-GF under airgapped profile | gap vs cloud | measured, published; gap <15pp S1 | — |
| EV-COST | event-log analysis | $/doc, $/schema, $/1k rows trends | regression >20% fails | — |
| EV-QC quarantine correctness | seeded bad rows/records/claims | right bucket + right reason | 100% | mis-bucketed seed must fail |
| EV-SEC adversarial security | zip bombs, XXE, CSV/formula injection, traversal paths, pickle payloads, macro docs | block rate | 100% | one live exploit fixture per class |

---

## 24. API & CLI Contracts

CLI (extends 02_TRD T12): `databridge convert plan|preview|execute|infer|validate -r request.yaml` · `databridge formats list` (renders §5 from the registry) · `databridge artifact show|replay|migrate <id>` · `databridge quarantine list|inspect <run>` · `databridge sql ask "<question>" --source pg_main` · `databridge excel inspect <file>` (header guesses, sheets, warnings). REST (08_PRD gateway): `POST /convert` (ConversionRequest), `GET /artifacts/{id}`, `GET /runs/{id}/report`, `GET /formats`, SSE `/runs/{id}/events`. All responses include refusals verbatim and run-report links. Auth: none locally, token in shared deployments (Stage 2 RBAC unchanged).

---

## 25. Repository Layout

As §8, plus: `packages/convert/sqlsub/`, `excelsub/`, `transform_u2u/`, `render/` are siblings (not buried) because each has an owner-able eval suite; `tests/fixtures/` hosts the Gauntlet Workbook, document corpus, injection corpus, drift mutations — fixtures are versioned product assets.

---

## 26. Ordered Implementation Tasks (Stage 1)

Each task follows the 02_TRD format (Context/Deps/Architecture/Interfaces/Data models/Logic/Libraries/Trade-offs/Security/Tests/Eval/Done-when) — headers given here; full prose follows the established pattern and the relevant §§ above. Sized for single Claude Code sessions.

**Foundation (existing 02_TRD T1–T2 unchanged, then):**
- **C01 ir/**: IR models + Arrow utils + CharIndex. Deps: T1. Done: property tests; IR-without-provenance unconstructable.
- **C02 router/** + adapter registry + Refusal types. Deps: C01. Done: capability table generated; refusal suite green.
- **C03 adapters: CSV/TSV/JSON/JSONL/Parquet/Arrow** (R+W). Deps: C01. Done: EV-SF subset; encoding fixtures.
- **C04 adapters: YAML/XML/HTML-tables/Markdown-tables**. Deps: C03. Security: XXE/anchor limits. Done: EV-SF + EV-SEC(xml).
- **C05 coercion catalogue + validators core + quarantine store**. Deps: C01. Done: per-coercion exhaustive tests; EV-QC seeds.
- **C06 rules/ S→S engine** (DuckDB+JSONata exec, proposer, validator, confirm flow). Deps: C02,C03,C05,T7(semantic). Done: operation catalogue tests; demo D3.
- **C07 artifacts/** (PipelineArtifact v1, store, replay, infer, audit render stub). Deps: C06. Done: EV-DET on s2s; infer single-record path.
- **C08 sqlsub/ part 1**: sources/targets, snapshot identity, read-only probe, DuckDB transform integration. Deps: C03. Done: PG fixture R/W; probe tests.
- **C09 sqlsub/ part 2: NL→SQL** (linking, generation, sqlglot safety, fixer/reviser, query library, artifact reuse). Deps: C08,T7. Done: EV-SQL ≥70%; injection corpus 100% blocked. **Critical path item.**
- **C10 excelsub/ reader** (calamine+openpyxl enrichment → WorkbookRecord; header inference; error/merged/hidden policies). Deps: C01. Done: Gauntlet values 100%.
- **C11 excelsub/ writer + Excel reports**. Deps: C10,C05. Security: formula-injection guard. Done: EV-XL write half; demo D1.
- **C12 document adapters: MD/TXT/HTML/PDF-text/DOCX(R+W)/PPTX(R)** → DocumentRecord + loss reports. Deps: C01. Done: container-mode round-trips; loss reports populated.
- **C13 OCR routing + image adapter** (RapidOCR/Tesseract, bbox CharIndex). Deps: C12. Done: EV-OCR floor.
- **C14 email adapter** (+attachment fan-out re-routing). Deps: C12,C02. Done: demo D8 fixture chain.
- **C15 extract/ U→S core** (pre-extractors, span selection, Pydantic extraction, grounding validator, confidence, review queues, repeating/multi-page). Deps: C12,T2,T7. Done: EV-DX gate; grounding canary. **Critical path item.**
- **C16 extract/ Excel-irregular + scanned-form paths**. Deps: C15,C10,C13. Done: demos D2,D7.
- **C17 generate/ S→U core** (fact slots, formatters, template-first synthesis, claim verifier, ConversationSet serialisers). Deps: T2,C05. Done: EV-GF zero-escape; demo D10.
- **C18 render/ ReportIR + md/docx renderers; data-dictionary + report templates**. Deps: C17,C12. Done: deterministic byte-identity; demo D11(md/docx).
- **C19 render/ PDF + Excel report renderers**. Deps: C18,C11. Done: airgap font test; D11(pdf).
- **C20 transform_u2u/** (container vs prose modes, citation preservation, summaries, loss/coverage validators). Deps: C12,C17. Done: EV suite for U→U; translation flag path.
- **C21 RAG chunker + eval-triple generator**. Deps: C12,T2. Done: demo D12.
- **C22 dataset templates** (tabular ML minimal §10.9 + splits + manifest + leakage; graph export §10.10 + PyG extra). Deps: C06,T7. Done: demos D13,D14.
- **C23 drift engine + migration reports**. Deps: C07. Done: EV-SD full matrix; demo D16.
- **C24 evals wiring + perf harness + security fixtures + demo runner**. Deps: all. Done: §23 board green; §27 all demos scripted; §28 gates evaluated.

**Dependency graph (critical path bolded):**
```
T1→T2→┬ C01→C02→C03→C04
       │        └→C05→C06→C07→C23
       │  C03→C08→**C09**
       │  C01→C10→C11
       │  C01→C12→C13, C12→C14, C12→**C15**→C16
       │  T7──────────┘(C06,C09,C15,C22 all need confirmed semantics)
       │  C17→C18→C19; C12+C17→C20; C12→C21; C06→C22
       └→ C24 (terminal)
Critical path: T1→T2→C01→C12→C15→C16→C24  and  T7→C09 (parallel-limited by semantic layer readiness)
```

---

## 27. End-to-End Demonstrations (all 17 mandatory; each ships as a scripted run)

| # | Demo | Fixtures | Command sketch | Acceptance |
|---|---|---|---|---|
| D1 | PostgreSQL → joined+aggregated Excel workbook | fintech fixture DB | `convert execute -r d1.yaml` | workbook opens; 3 sheets; semantic number formats; range provenance in manifest |
| D2 | Multi-sheet irregular Excel → clean Parquet+JSON | Gauntlet-lite workbook | `convert plan/execute` | headers confirmed; merged/hidden handled per policy; values 100% |
| D3 | Nested JSON → flattened CSV | API dump fixture | rules path | round-trip values; injection-guarded CSV |
| D4 | CSV → typed PG-compatible records | messy CSV (locales, sentinels) | rules + coercions | typed RowBatch; quarantine report for seeded bad rows |
| D5 | XML → JSON | order-feed XML | container path | XXE fixture refused; mapping convention validated |
| D6 | PDF invoice → validated JSON | 5 invoices | extract path | field F1≥0.9 on these; grounding 100% |
| D7 | Scanned form → typed record + OCR provenance | 3 scans | extract+OCR | bbox pointers resolvable; confusion-trap reported |
| D8 | Email+attachment → CRM record | .eml with PDF | email fan-out | provenance chain email→attachment→span |
| D9 | NL → safe SQL → validated result | "top merchants by disputed amount last quarter" | `sql ask` | sqlglot-clean; read-only; result + SQL provenance; injection attempt refused |
| D10 | DB rows → grounded SFT JSONL | support_messages | generate path | E2 100%; adversarial absent-fact passes; both serialisations |
| D11 | DB+document facts → grounded MD/DOCX report | mixed facts | report path | every claim cited; deterministic re-render |
| D12 | Documents → RAG chunks | 10-doc corpus | chunker | pointers in metadata; eval triples emitted |
| D13 | Relational → tabular ML dataset | fintech + confirmed fraud label | dataset template | time split; leakage refusal on seeded leak; manifest complete |
| D14 | Relational graph → node/edge tables + minimal PyG | same | graph export | HeteroData loads; typed edges |
| D15 | Confirmed artifact → single-record infer | D4 artifact | `convert infer` | byte-identical transform of one record |
| D16 | Schema drift → safe refusal + migration report | mutated fixture | execute | correct class, structured report, no partial output |
| D17 | Unsupported conversion → clear refusal | xls write request | router | typed Refusal + nearest alternative; nothing fabricated |

---

## 28. Stage 1 Release Gates

All §23 suites at threshold with canaries firing · all 17 demos green in CI · airgapped full-flow socket-deny pass · EV-DET byte-identity across two machines · semantic-refusal rule (§16.1) covered by tests · no pickle, no AGPL/BSL in deps (licence CI) · run reports + quarantine accounting present for every demo · §30 timeline retrospective written (estimate vs actual feeds the wiki).

---

## 29. Risks & Mitigations (delta to prior registers)

| Risk | Mitigation |
|---|---|
| Scope expansion blows the schedule | §30 honest re-estimate; critical path protected; format breadth (not direction count) is the only legal cut |
| NL→SQL in S1 underdelivers vs promise | gate is 70% with published number; query-library flywheel; refusal-not-guess on loop exhaustion |
| OCR quality on bad scans | EV-OCR floor at 0.75 + confidence routing to review; honest per-fixture reporting |
| PDF render determinism | manifest-declared best-effort; OD-2 keeps renderer swappable |
| calamine/openpyxl metadata gaps | enrichment layer isolates; pyxlsb fallback reserved |
| Validator-loosening pressure under deadline | thresholds.yaml CODEOWNERS + canaries make leniency loud (07_PRD) |

---

## 30. Timeline & Staffing (honest; old 6-week claim withdrawn)

Engineering-effort basis: 24 C-tasks + T1–T12 carryovers ≈ **34 focused task-sessions**, avg 1.5–2.5 dev-days each with Claude Code (including tests/evals/fixtures), + integration/demo hardening ≈ 15% overhead.

| Team | Calendar | Effort | Notes |
|---|---|---|---|
| 1 engineer + Claude Code | **13–16 weeks** | ~70–85 dev-days | serial critical path dominates; semantic layer (T6–T7) gates C09/C15/C22 |
| 2 engineers + Claude Code | **8–10 weeks** | same effort, parallel tracks: (A) structured+SQL+Excel, (B) documents+extract+generate | weekly integration days mandatory |
| 3 engineers | **6.5–8 weeks** | adds track (C) render+U2U+datasets+evals | coordination overhead real; below 6.5 weeks not credible at this scope |

Assumptions (explicit): fixtures built alongside tasks (included); design-partner schemas arrive by week 3 for E1/EV-DX realism; no Stage-2 work smuggled in; Fable/Sonnet routing per 02_TRD A.4.

---

## 31. Conflicts With Existing PRDs (reconciled)

- **C1 — U→S incomplete in S1.** 01_PRD §5 Stage 1 listed "TWO conversion modes only… (a) structured→structured; (b) structured→unstructured" and out-of-scoped the rest; 02_TRD Part B has no U→S/U→U tasks. **Conflict:** missing direction classes in S1. **Decision:** all four directions in S1 (this doc). 
- **C2 — NL→SQL postponed.** 01_PRD §5 "Out of scope for POC… text-to-SQL agent"; 02_TRD T17 "Stage 2"; 05_PRD §4.2 "(Stage 2, T17 — designed now)". **Decision:** S1 (§15.4), gate unchanged ≥70%.
- **C3 — Excel not first-class.** 04/02 route spreadsheets via MarkItDown→Markdown (xlsx as a "document"). **Decision:** dedicated WorkbookRecord subsystem (§14); MarkItDown no longer touches .xls*/.xlsb.
- **C4 — Tabular-ML/GNN exports postponed.** 02_TRD T20 "Stage 3"; 05_PRD §7 "(Stage 3…)". **Decision:** minimal-viable in S1 (§10.9–10.10); Stage 3 keeps scale/feature-store depth.
- **C5 — Frontend vs CLI scope.** 08_PRD Phase A optional/demo, product UI Stage 2 — **no conflict; reaffirmed**: S1 surface is CLI+thin API; Phase A demo remains optional and unchanged.
- **C6 — Six-week estimate unrealistic.** 01_PRD §5 "Weeks 1–6", 02_TRD task count. **Decision:** withdrawn; §30 supersedes anywhere a duration is cited.

## 32. Required Edits to Existing Documents

| File | Section | Replacement wording (insert/replace) |
|---|---|---|
| 01_PRD.md | §5 Stage 1 | Replace scope paragraph with: "Stage 1 delivers the complete conversion architecture: all four conversion directions (S→S, U→S, S→U, U→U), the Excel and SQL subsystems including natural-language-to-SQL, per 09_FINAL_PRD. Format breadth and enterprise hardening expand in later stages; no conversion direction is deferred." Delete the out-of-scope bullets for text-to-SQL/U→S; replace "Weeks 1–6" with "see 09_FINAL_PRD §30". |
| 02_TRD.md | Part B intro + T9/T10 | Add: "Conversion tasks are superseded and expanded by 09_FINAL_PRD §26 (C01–C24); T9/T10 remain valid as the s2s/s2u cores within that plan." Part C: mark T17/T20 "moved to Stage 1 — see 09 §15.4/§10.9–10.10". |
| 04_PRD | §2 collectors / 02_TRD T4 | Append: "Spreadsheet formats (.xlsx/.xls/.xlsb) bypass MarkItDown and route to the Excel subsystem (09 §14); MarkItDown handles documents only." |
| 05_PRD | §4.2 heading; §7 heading | "(Stage 2, T17…)" → "(Stage 1 — see 09 §15)"; "(Stage 3, composed…)" → "(Stage 1 minimal-viable — see 09 §10.9–10.10; Stage 3 adds scale/feature-store depth)". |
| 03_LIVING_WIKI | bootstrap checklist | Add: "Write ADR-0006: Stage-1 full-conversion-pillar decision (supersedes prior staging); seed 50-research/excel-formats.md and sql-safety.md from 09." |
| 07_PRD | §7 suites | Append the §23 suite names to the suites tree; thresholds.yaml gains their gates. |
| 08_PRD | §1 table | No change required; add link: "Conversion Studio renders 09 §9 Refusals and §20 migration reports verbatim." |

## 33. Open Decisions Requiring Human Confirmation

- **OD-1:** SQLAlchemy "generic source" allow-list — confirm initial dialect set {postgres, sqlite, duckdb, mysql, mssql}. 
- **OD-2:** PDF renderer — WeasyPrint (default; HTML path, heavier deps) vs ReportLab (programmatic, lighter) vs Typst (nicest output, newer toolchain). Affects C19 only.
- **OD-3:** OCR engine default — RapidOCR (proposed) vs docling-bundled stack. Affects C13.
- **OD-4:** Password-protected workbooks — strict refusal (proposed) vs accept user-supplied password with audit log.
- **OD-5:** Translation in §13.2 — keep as S1 flag-only validation (proposed) vs gate.
- **OD-6:** Per-customer extension of the syntactic-bypass list (§16.3) — fixed closed set (proposed) vs config.

---

## 34. Completeness Audit (prompt requirement → coverage)

Four directions fully specified, separate mechanisms ✔ §§6,10–13 · per-direction 16-point template ✔ woven through §§9–13,18–19,23,27 (purpose/inputs/outputs/routing §§6,9; deterministic preprocessing §§10.1,11.1,13.1; LLM allowed/forbidden §6; validation §19; provenance §17; human review §§11.5,16, P6; errors/quarantine §19; artifacts §18; inference §18/§15.12; eval §23; acceptance §27) · mandatory format lists ✔ §5 (every named format rowed, incl. .xls library+limits, audio-transcript assumption marked) · support-class distinctions (R/W, lossy, IR-mediated, refusals) ✔ §5 legend+rules · no universal-lossless claim ✔ §4.1 · Excel 19-point spec + libraries justified ✔ §14 · SQL 12-point disambiguation + NL→SQL in S1 + sqlglot ✔ §15 · canonical IRs incl. all named records + flow diagram + N×N avoidance ✔ §7 · routing with all listed considerations + fail-loud/no-silent-weaker-path ✔ §9 · P3 mechanism table ✔ §6 + per-direction; no new languages ✔ (DuckDB SQL/JSONata/Pydantic/templates/renderers/graph exporter only) · U→S full subsystem incl. OCR, bboxes, repeating/multi-page/nested, six use cases ✔ §11 · S→U beyond SFT incl. reports/emails/data dictionaries/API docs, citation contracts, verifier, style firewall, deterministic rendering ✔ §12 · U→U incl. all listed hops, two modes, loss reports, container-vs-prose validator split ✔ §13 · S→S full operation list + coercion catalogue ✔ §10 · semantic integration + refusal-until-confirmed + syntactic bypass ✔ §16 · provenance extensions (all listed kinds, compression, snapshot identity, lineage DAG, no-provenance=failure) ✔ §17 · PipelineArtifact full field list, no-pickle, five lifecycles ✔ §18 · validation framework (all listed checks) + failure policies + no silent dropping ✔ §19 · drift matrix incl. every listed case + safe-refusal default ✔ §20 · security 17 items ✔ §21 · performance incl. all listed behaviours + explicit assumptions ✔ §22 · eval suites named w/ fixtures/metrics/thresholds/canaries/CI + field-level P/R/F1 ✔ §23 · 17 demos w/ fixtures/commands/acceptance ✔ §27 · repo layout ✔ §§8,25 · ordered tasks w/ session-sized scope + dep graph + critical path ✔ §26 · reconciliation of all six named conflicts w/ quotes, decisions, file edits, wording ✔ §§31–32 · staffing 1/2/3 + calendar-vs-effort + six-week withdrawal defended ✔ §30 · 33-section structure ✔ (this doc) · open decisions marked ✔ §33 · no implementation code, types/pseudocode only ✔ throughout.
