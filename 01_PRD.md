# DataBridge — Product Requirements Document (PRD)
# Version 1.0 · For Claude Code · Read this FIRST before TRD

---

## 0. How To Read This Document

This PRD defines WHAT DataBridge is and WHY every component exists. The TRD (02_TRD.md) defines HOW to build each component. The Living Wiki doc (03_LIVING_WIKI.md) defines how the team retains context across sessions, models, and people.

Claude Code: before implementing any task, read the relevant PRD section for intent, then the TRD task for implementation. Never implement from the TRD alone — the WHY constrains the HOW.

---

## 1. Product Definition

DataBridge is **data infrastructure** with two pillars:

**Pillar 1 — Data Availability (the Company Brain).** A model-agnostic agentic harness that connects to every data source an organisation has — databases, codebases, documents, emails, meetings, chat — and unifies it into a queryable, temporal knowledge graph. The harness must run with ANY model: hosted APIs (Anthropic, OpenAI) or local models (Ollama, vLLM, llama.cpp), because regulated customers (banks, hospitals) cannot let data leave their perimeter.

**Pillar 2 — Format Conversion (the Compiler).** A conversion engine that transforms data between any formats using semantic understanding as the guide:
- Structured → Structured: deterministic rule engine (NO LLM)
- Unstructured → Structured: code agents that generate SQL/parsers with self-correction
- Structured → Unstructured: grounded LLM generation (e.g., conversational SFT data from logs)

The two pillars connect: Pillar 1 gathers and understands; Pillar 2 converts the unified data into whatever a downstream system needs — ML training data (tabular features, GNN tensors, SFT JSONL, RAG chunks), analytics formats, or operational formats.

**The one-line definition:** A compiler between how organisations store data and how systems (AI models, analytics, operations) consume data.

---

## 2. The Problem (validated, quantified)

These numbers anchor every design decision. They are the reason each component exists.

| Bottleneck | Evidence | Which component solves it |
|---|---|---|
| Data silos / availability | 68% of orgs cite silos as top concern (DATAVERSITY 2024); avg enterprise runs 897 apps, only 29% integrated (MuleSoft 2025) | Pillar 1 ingestion + knowledge graph |
| Format incompatibility | 80% of orgs cite data integration as their biggest AI obstacle (MuleSoft); ML teams spend 60–80% of time on data prep (Anaconda surveys) | Pillar 2 conversion engine |
| Semantic understanding gap | Gartner: "inconsistency in data across sources" is the most challenging data-quality problem | Semantic annotation layer (shared by both pillars) |
| Hidden ground truth | Companies believe they lack labels; labels exist as business outcomes (chargebacks=fraud, cancellations=churn, CSAT=quality) | Label discovery in semantic layer |
| Privacy/compliance | Regulated industries cannot use cloud-only tools; data egress kills deals | Model-agnostic harness + VPC/local deployment |
| Data quality | Bad data costs avg $12.9M/yr (Gartner); 59% of orgs don't measure quality | Validation layer |
| Training-serving skew | Same transformations must run at train AND inference time or models fail silently | Versioned pipeline artifacts |

---

## 3. Product Principles (non-negotiable constraints)

These principles override convenience. If an implementation choice violates one, the choice is wrong.

**P1 — No data egress by default.** All processing runs where the data lives. The LLM may be local. Only metadata and samples ever reach a hosted model, and only with explicit configuration. This is the wedge into regulated industries and is architectural, not a feature flag bolted on later.

**P2 — Model-agnostic always.** Every LLM call goes through one abstraction layer (LiteLLM). No component may import an Anthropic/OpenAI SDK directly. A customer must be able to swap Claude for Qwen3-72B on Ollama by changing one config value.

**P3 — Deterministic before generative.** If a transformation can be done with rules/code, it MUST be done with rules/code. LLMs are reserved for semantic understanding, ambiguity resolution, and natural-language generation. This is the gbrain "Minions" insight: deterministic ops run at ~753ms and zero token cost; LLM agents handle only non-deterministic reasoning. Cost, reproducibility, and auditability all depend on this.

**P4 — Every output value is traceable.** Provenance is mandatory. Any value in any output must point back to a source (table.column.row, document span, transcript timestamp). A value without provenance is a bug, not a feature. This is the grounding rule that prevents hallucination in both conversion directions.

**P5 — Labels come from business outcomes, never from LLMs.** The system finds ground truth (chargebacks, cancellations, resolutions); it never fabricates it. When ground truth genuinely doesn't exist, the system says so honestly and routes to weak supervision, self-supervised reformulation, or human annotation.

**P6 — Human confirmation for semantic decisions.** The LLM proposes; a human confirms. Wrong semantic annotation cascades into broken models. The confirmation UI is a survival requirement, not a UX nicety. Confidence scoring routes high-confidence annotations to auto-approve and low-confidence to review.

**P7 — Incremental by design.** Re-indexing everything on every change is unaffordable (GraphRAG-style full builds run 10–100× standard RAG indexing cost). Graph updates use bi-temporal invalidation (Graphiti pattern) or set-union merge (LightRAG pattern) — never full rebuilds.

---

## 4. Users and Jobs-To-Be-Done

**Primary user: ML/Data Engineer at a mid-market company (50–500 staff).**
JTBD: "Turn our scattered data into training data for [fraud model / support chatbot / churn model] without building a pipeline from scratch."
Success: usable training dataset in days; same pipeline artifact serves inference.

**Secondary user: Head of AI / CTO at regulated enterprise.**
JTBD: "Adopt AI on our internal data without any data leaving our environment and with auditable transformations."
Success: full deployment in their VPC, local model option, audit trail per transformation.

**Tertiary user: AI agents (internal).**
JTBD: agents operating inside the company need organisational context.
Success: the knowledge graph is queryable via MCP so any agent (Claude Code, custom agents) can ask "who owns project X" or "what was decided in the March pricing meeting."

---

## 5. Scope by Stage (build gates — do not skip ahead)

### Stage 1 — POC (Weeks 1–6)
**Goal:** prove the core thesis end-to-end on one real dataset.

In scope:
- Ingestion from exactly 3 source types: PostgreSQL, a folder of documents (via MarkItDown), one codebase (via Graphify)
- LiteLLM harness with at least 2 backends configured (Anthropic API + Ollama local)
- Semantic annotation of one database schema with human confirmation CLI
- Knowledge graph in Neo4j Community (Graphiti-style bi-temporal edges)
- TWO conversion modes only: (a) structured→structured via JSONata rule engine; (b) structured→unstructured producing SFT conversation pairs with full provenance
- Evaluation harness (see §6) running from day one

Out of scope for POC (explicitly): entity resolution at scale, Airbyte integration, meetings ingestion, text-to-SQL agent, UI (CLI only), permissioning, incremental updates beyond naive re-ingest of changed sources.

**Gate to Stage 2:** end-to-end "ingest → graph → NL query with citations" works on one real dataset; semantic annotation ≥85% accuracy on held-out schema columns; SFT output passes provenance check at 100% (zero untraceable facts).

### Stage 2 — MVP (Months 2–3)
Adds: Airbyte connector integration (Slack, Google Drive, Gmail), Recall.ai/Whisper meeting ingestion, text-to-SQL conversion mode with self-correction loop, entity resolution (LLM dedup + blocking), incremental graph updates, Onyx-style per-source permissioning, web UI for confirmation flow, MCP server exposing graph queries.

**Gate to Stage 3:** incremental update cost <10% of full rebuild; entity-resolution precision AND recall >90% measured on labelled pairs; text-to-SQL >70% execution accuracy on customer-like schemas.

### Stage 3 — Production (Months 3–6)
Adds: tabular-ML and GNN conversion templates, versioned pipeline artifacts deployed to inference path, audit logs, RBAC, FHIR/ISO-20022 domain adapters (if a design partner needs them), cost optimisation (caching, model routing), SOC 2 groundwork.

---

## 6. Evaluation Requirements (every stage ships with evals)

No component is "done" without its eval. Evals are code, run in CI, with thresholds.

**E1 — Semantic annotation accuracy.** Hold out N schemas with human-labelled semantic types. Metric: per-column semantic-type accuracy; per-table role accuracy. Threshold: ≥85% (POC), ≥92% (MVP). Track per-domain (fintech vs generic) to detect domain weakness.

**E2 — Provenance completeness.** For every generated output (SFT pairs, narratives), automated check that every factual token span maps to a provenance pointer. Threshold: 100%. Any failure blocks the output.

**E3 — Distribution fidelity.** For structured outputs derived from structured inputs, KS test on numeric columns and set-containment on categoricals (no invented categorical values). Threshold: zero invented values; KS p>0.05 or flagged.

**E4 — Graph retrieval quality.** Curated Q&A set per ingested corpus ("who is mentioned in connection with X", multi-hop questions). Metric: recall@5 with citations. Track against gbrain's published 97.6% R@5 on LongMemEval as the aspirational bar — POC threshold: ≥70% on our own eval set.

**E5 — Text-to-SQL execution accuracy (Stage 2+).** Execute generated SQL against the actual database; compare result sets. Threshold ≥70% (context: SOTA Agentar-Scale-SQL hits 81.67% on BIRD vs 92.96% human; our schemas are simpler but our bar accounts for that).

**E6 — Model-agnostic parity.** Run E1 and E4 with both a hosted model and a local model (Qwen3 family recommended for structured output). Document the gap; the product promise is "works with local models," so the gap must be known and communicated, not hidden.

**E7 — Cost per operation.** Log tokens and dollars per ingest/annotation/conversion via LiteLLM tracking. Regression alarm if cost-per-document rises >20% between commits.

---

## 7. Anti-Goals

- NOT a vector-RAG chatbot product. Retrieval is hybrid (vector+graph+BM25) and serves the conversion/brain mission, not a chat UI.
- NOT a data warehouse or storage layer. We read from and write to the customer's systems; we are not a system of record (exception: the knowledge graph itself, which is our derived artifact).
- NOT an agent orchestration framework. We expose tools (MCP); we don't compete with LangGraph.
- NOT a labelling service. P5 applies.
- NOT dependent on any single model vendor. P2 applies.

---

## 8. Key Risks and Mitigations (product-level)

| Risk | Severity | Mitigation baked into requirements |
|---|---|---|
| Wrong semantic annotation silently breaks customer model | FATAL | P6 confirmation flow + E1 thresholds + confidence routing |
| Hallucinated facts in generated training data | FATAL | P4 provenance + E2/E3 hard gates |
| Graph build cost explodes | HIGH | P7 incremental; LightRAG-style flat graph first, Leiden communities only when needed |
| Local-model quality gap undermines privacy promise | HIGH | E6 parity tracking; Qwen-family default for structured output; constrained decoding (Outlines) to guarantee valid JSON regardless of model |
| KuzuDB-style dependency death | MEDIUM | Storage behind an interface; Neo4j Community default, FalkorDB optional; NetworkX for tests |
| AGPL/BSL contamination | MEDIUM | Dependency licence check in CI; allowed: MIT/Apache/BSD; forbidden in core: AGPL (Logseq, Khoj), BSL (Memgraph, ArangoDB) |
