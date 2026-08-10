# PRD-07 — Model Harness & Evaluation System
# Deep specification · Companion to 01_PRD.md, 02_TRD.md
# Scope: the model-agnostic abstraction (P2/P1 in code), structured-output enforcement,
#        cost/observability, and the evaluation harness that gates every stage.
# These two share a file because they are two faces of one promise: ANY model, MEASURED.

---

# PART I — THE MODEL HARNESS

## 1. What It Is and Why It's Strict

Every LLM call in DataBridge — annotation, extraction, planning, generation, verification — goes through ONE package: `packages/harness`. No other package may import a vendor SDK (CI grep rule). This is the executable form of two principles:

- **P2 (model-agnostic):** a customer swaps Claude for Qwen3-72B-on-Ollama by changing one config profile. The product promise to regulated industries — "runs with local models, data never leaves" — is only true if it's true for EVERY call site, which is only verifiable if there's exactly one call site.
- **P1 (no egress):** the harness is the choke point where the airgapped profile is enforced. If the active profile is `airgapped` and a task_class would resolve to a hosted alias, the harness REFUSES (loud error), it does not fall back silently. A privacy promise with silent fallbacks is a lawsuit.

## 2. Interface

```python
# packages/harness/databridge_harness/client.py
class Harness:
    def complete_structured(
        self,
        task_class: str,                  # resolved via config routing table — NEVER
                                          # a model name at a call site (reproducibility:
                                          # routing changes are config diffs, not code hunts)
        messages: list[Message],
        response_model: type[BaseModel],
        strict: bool = True,              # True → demand L3 if backend has it
        seed: int | None = None,          # passed through where supported; logged always
        max_retries: int = 2,             # L2 budget
    ) -> StructuredResult: ...
        # StructuredResult = {parsed: T, raw: str, event: PipelineEvent,
        #                     ladder_used: "L3"|"L2"|"L1", retries: int}

    def complete_text(self, task_class, messages, **kw) -> TextResult: ...
        # only for synthesis/narration paths; anything machine-consumed uses structured
```

`ladder_used` and `retries` surface in events because they are quality telemetry: a task_class whose L2 retry rate climbs is a prompt or model regression announcing itself before accuracy evals catch it.

## 3. The Capability Table (explicit, not probed)

```python
# packages/harness/databridge_harness/backends.py
CAPABILITIES = {
  "anthropic/*":      Caps(json_mode=False, strict_schema=False, tools=True,
                           guided_decoding=False, seed=False),
        # Anthropic tool-use does NOT guarantee schema compliance → L1/L2 carry weight
  "openai/*":         Caps(json_mode=True,  strict_schema=True,  tools=True,
                           guided_decoding=False, seed=True),
        # strict mode ⇒ near-guaranteed schema compliance (<0.1% failure)
  "ollama_chat/*":    Caps(json_mode=True,  strict_schema=False, tools="varies",
                           guided_decoding="format_json", seed=True),
        # format=json guarantees JSON syntax, NOT your schema → L2 still required
  "hosted_vllm/*":    Caps(json_mode=True,  strict_schema=True,  tools=True,
                           guided_decoding="guided_json", seed=True),
        # vLLM guided_json = token-level grammar ⇒ schema guaranteed; the strongest
        # local option and the recommended airgapped serving stack
}
```

**Why explicit:** the gaps between backends are exactly where silent quality loss hides. Encoding them makes the asymmetry a reviewed fact. The ladder resolves per-call: `strict=True` + vLLM → guided_json (L3); same call on Anthropic → Instructor retries (L2) with validation-error feedback; the SAME calling code, different enforcement, both logged.

**The latency/determinism tradeoff, decided:** grammar-constrained decoding costs 30–80% generation speed but eliminates retry loops and guarantees parse success. Decision rule: batch jobs (annotating 500 columns, extracting 10k chunks) always take L3 where available — determinism and zero-retry beats latency when no human is waiting. Interactive paths may config down to L2. The default is strict; loosening is a per-task_class config decision with a comment.

## 4. Local Model Reality (E6's engineering counterpart)

The airgapped promise is only as good as local structured-output quality. Engineering positions taken:

- **Default local family: Qwen** (qwen3:72b primary, qwen3:4b for CI smoke) — specifically strong at JSON/structured output; this is why it's the default and not a neutral placeholder.
- **Serving recommendation: vLLM over Ollama for production airgapped** — guided_json's token-level schema guarantee converts the local-model schema-compliance problem from probabilistic to solved; Ollama remains the dev-laptop path.
- **Tool calling "varies" is handled by not depending on it:** the harness's structured path never uses native tool-calling for schema enforcement (response_model via prompt+grammar instead), because tool-call support across local models is the least consistent capability in the table. Tool calling is reserved for the MCP/agent surface where the model is known.
- **Parity is measured, never assumed:** E6 runs the real eval suites (E1 annotation, E4 retrieval, grounding validators) under the airgapped profile nightly. The gap is published in the scorecard and in customer docs. The sales line is "here is the measured local-model gap," not "works the same" — with the sceptical technical buyer, the measured number is the stronger pitch.

## 5. Cost, Caching, Observability (E7's plumbing)

- Every call emits a PipelineEvent with tokens in/out, cost (LiteLLM's pricing tables), latency, model actually used, ladder, retries, inputs_hash.
- **Content-hash caching:** `(task_class, model, inputs_hash, response_model_hash, seed)` keys a cache; re-running an unchanged annotation pass costs zero. This is what makes "re-run the whole pipeline after a prompt fix" affordable and what makes determinism testable (cache-off replay must equal cache-on result).
- **Budgets are enforceable, not advisory:** per-run and per-source token budgets in config; the harness raises `BudgetExceeded` rather than draining an account — and the crawl planner (PRD-04 §4) consumes remaining budget as world state, so cost pressure changes behaviour upstream instead of failing downstream.
- **Fallback chains are config and logged:** `sonnet → local_main` on provider outage is legitimate in cloud profile (and forbidden in airgapped, where fallback can only go local→local). Every fallback activation is an event — a run silently served by a different model than evaluated is a corrupted experiment.

---

# PART II — THE EVALUATION SYSTEM

## 6. Philosophy: evals are the product's immune system

PRD §6's rule — no component is done without its eval — is operationalised here. The deeper reasoning: DataBridge's FATAL risks (wrong annotation, hallucinated training data) are **silent** failures. Silent failures are only caught by machinery that runs constantly and refuses merges. The eval harness is therefore not QA tooling; it is the mechanism by which the product's central promises (accuracy, grounding, parity, cost) remain true over time.

## 7. Architecture

```
packages/evals/
├── suites/
│   ├── e1_annotation/        # 3 labelled schemas (fintech, oss-app, legacy-cryptic)
│   │   ├── data/             # SchemaExtract fixtures + gold AnnotatedSchema
│   │   └── suite.py
│   ├── e2_provenance/        # generated-output corpus + fabrication seeds
│   ├── e3_distribution/      # KS + categorical containment + drift metrics
│   ├── e4_retrieval/         # 30-Q cited-answer set (single/multi-hop/temporal)
│   ├── e5_text2sql/          # Stage 2: eval schemas + (question, gold-result) pairs
│   ├── e6_parity/            # = E1+E4 re-run under airgapped profile; emits gap
│   ├── e7_cost/              # event-log analysis; regression thresholds
│   └── adversarial/          # absent-fact temptations, negation traps, drift seeds
├── runner.py                  # databridge eval --all|--suite|--profile
├── scorecard.py               # markdown + json; reliability diagrams for calibration
├── thresholds.yaml            # ONE file; PRD gates and CI gates cannot diverge
└── regression_canary.py       # seeded-broken fixtures must turn the board red
```

```python
class EvalResult(BaseModel):
    suite: str; metric: str; value: float; threshold: float
    passed: bool; profile: str; model_map: dict      # task_class→model actually used
    slices: dict                                      # per-schema/type/bucket breakdowns
    commit: str; run_at: datetime; cost_usd: float
```

`model_map` in every result is non-negotiable: an eval score without the exact models that produced it is unreproducible noise, and the whole point of the routing table is that the map can change.

## 8. Cadence & Gating (cost-aware honesty)

| Tier | When | What | Why this split |
|---|---|---|---|
| Per-commit CI | every push | deterministic suites (provenance validators, round-trip determinism, store-swap, coercion tests) on FakeBackend + qwen3:4b smoke | fast, free, catches logic breaks |
| Pre-merge on sensitive paths | merges touching semantic/, convert/generate/, prompts, thresholds.yaml | on-demand E1/E2/E4 (cloud profile) | the FATAL-risk surfaces get real-model eyes before landing |
| Nightly | scheduled | full suite, BOTH profiles → parity gap + cost trends posted | catches drift between sensitive merges; produces the E6/E7 time series |
| Stage gates | stage transitions | full suite + manual review of slices and failure-mode registers | the PRD gates, executed |

The canary requirement (the harness evals itself): a deliberately-broken annotator fixture and a seeded fabrication MUST fail their suites; if the canary passes, the run is invalid. Eval systems rot by becoming lenient invisibly — the canary makes leniency loud.

## 9. Eval-Set Growth (the second flywheel)

Eval sets are living assets with defined growth sources:
- **Corrections → E1:** every human correction is a case the model got wrong; quarterly, corrections graduate into the gold sets (with adjudication — §PRD-06 ¶9.4 poisoning rules apply).
- **Quarantine → adversarial:** every fabrication caught by validators, every absent-fact failure, every drift flag becomes a permanent adversarial case. The system's failures become its tests; regression on a previously-caught failure is the one unforgivable class.
- **Customer schemas (with consent, redacted) → new E1 slices:** the legacy-cryptic slice grows from real encounters, keeping the hardest distribution honest rather than synthetic.

## 10. Failure Modes Register (harness + evals)

1. **Silent profile leak** (hosted call under airgapped) → harness refusal + a CI test that runs the full POC flow under airgapped with a socket-deny on non-local addresses; any escape fails the build.
2. **Score laundering via threshold edits** → thresholds.yaml is CODEOWNERS-protected; every change needs an ADR link in the PR description (enforced by template check).
3. **Eval overfitting** (prompts tuned to the three gold schemas) → held-out rotation: one slice of each gold set is sequestered, run only at stage gates, never visible in nightly scorecards.
4. **Cost telemetry gaps** (a call path that forgets events) → the harness emits events, not call sites; a coverage test asserts event count == call count in an instrumented run.
5. **Cache poisoning** (stale cache masks a prompt change) → response_model_hash and prompt-template hash are in the cache key; template changes auto-invalidate. The determinism test (cache-on == cache-off) runs nightly.
6. **Local smoke model false confidence** (qwen3:4b passes, 72b matters) → CI smoke gates only parse-ability and plumbing, never accuracy; accuracy gates run on the real profile models exclusively. The smoke tier's thresholds are deliberately labelled "plumbing-only" in thresholds.yaml.
