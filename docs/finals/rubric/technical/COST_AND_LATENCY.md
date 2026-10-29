# Cost and latency

> **Status.** 30 Oct 2026. Every number is labelled **Measured** (with its source) or **Estimate** (with
> its working). No real-model call has succeeded yet, so **every model cost and model latency figure is an
> estimate**. Eval counts were re-run on 30 Oct (`eval_summary.json`); latency figures are from the 14 Oct RESULTS.md and were not re-measured.
> `databridge_ib/…` = `databridge/apps/internal_brain/databridge_ib/…`. Line numbers refer to commit `e5406b9` (read them with `git show e5406b9:<path>`); the code is being edited this week, so the function or test name is the stable part.

## 1. Latency

| Item | Value | Type | Source / working |
|---|---|---|---|
| Mean pipeline latency, model path (fake model, so model time ≈ 0) | **19.8 ms** | Measured (14 Oct run; not re-measured) | `RESULTS.md:49` |
| Mean pipeline latency, extractive path | **19.0 ms** | Measured (14 Oct run; not re-measured) | `RESULTS.md:50` |
| Gate 2 worst case per answer | **2 s** hard budget for the whole batch, then deny | Design limit | `databridge_ib/gates.py:44`; `databridge_ib/pipeline.py:80` |
| Gate 2 against real platform APIs | Unknown | Not measured | Mock platforms on localhost only |
| Hy3 generation time | Unknown | Not measured | No live call yet |
| Map (re-checks every item live) | "a few seconds" for a big map; 45 s batch budget | Stated limit | `INTERNAL_BRAIN.md:255-256`; `docs/tencent/ARCHITECTURE.md:114-116` |
| Index build at start-up | About 1 minute (demo world) | Measured by hand | `INTERNAL_BRAIN.md:112-113` |
| Eval shard build / run | 11.5-15.9 s build; 0.3-44.3 s run per shard | Measured (14 Oct run; not re-measured) | `RESULTS.md:36` |

**Per-stage timings are now recorded** (30 Oct): every `answer.released` event carries `stage_ms` for
accept, gate1, gate2, seal and compose (`databridge_ib/pipeline.py:185-188`), and `/audit/stats` reports
p50 and p95 per stage (`databridge_ib/audit/stats.py`). These will give live per-stage numbers once the
demo history is seeded (`docs/finals/PLAN.md` §5, B7, todo). We have not yet published any p50/p95 from them.

**What dominates in production (estimate).** The model call. Our own pipeline is ~20 ms; Gate 2 is bounded
at 2 s; a hosted LLM writing a short cited answer typically takes seconds. This is an expectation, not a
measurement.

## 2. How often the model is called at all

| Item | Value | Type | Source |
|---|---|---|---|
| Fake-model requests refused with **no model call** | 2,296 of 2,979 (77%) | Measured 30 Oct (`eval_summary.json`) | `RESULTS.md:49` |
| Requests where the model received passages | 683 of 2,979 (23%) | Measured 30 Oct (`eval_summary.json`) | `RESULTS.md:12, 49` |
| Model answers replaced after grounding | 2 of 683 | Measured, **fake model** (14 Oct run; not re-measured) | `RESULTS.md:49` |

**Caveat.** The eval is adversarial: most probes ask for things the persona may not see, so it refuses
far more often than real users would. Do not use 77% as a forecast of real traffic.

## 3. Model cost per answered question (estimate)

| Input | Value | Type | Source / working |
|---|---|---|---|
| Max passages per question | 14 | Code | `databridge_ib/pipeline.py:34` |
| Max chunk size | 700 characters | Code | `databridge_ib/index.py:45` (`CHUNK_CHARS`) |
| Max context | 14 × (700 + ~80-char header) ≈ 11,000 characters ≈ **2,700 tokens** | Estimate | ~4 characters per English token (rule of thumb) |
| System prompt + question | ≈ 300 tokens | Estimate | `databridge_ib/narrate.py:21-35`; question ≤ 2,000 characters (`databridge_ib/models.py:155`) |
| **Input tokens, upper bound** | **≈ 3,000** | Estimate | sum of the two rows above |
| Output tokens | ≈ 200 (a few cited sentences) | Estimate | assumption |
| Hy3 list price | ≈ ¥1 per million input tokens, ¥4 per million output | Secondary source **[verify]** | `docs/finals/research/05_competition_pitch.md:48` |
| **Cost per answered question** | ≈ ¥0.003 + ¥0.0008 ≈ **¥0.004**, well under one Singapore cent | Estimate | arithmetic above |
| **Cost per 1,000 answered questions** | ≈ **¥4** | Estimate | × 1,000 |
| Refused questions | **¥0** | Code | no model call (`databridge_ib/pipeline.py:163`) |
| Embeddings | **¥0** | Code | local hashing embedder (`databridge_ib/index.py:159-168`) |
| DeepSeek fallback price | Not researched | — | — |

To verify on the first live run: actual input and output tokens per answer, and TokenHub's billed price.
Token accounting is not in the ledger today (`../research/D_technical_responsible_ai.md` §1.3 row 14).

## 4. Other running costs

| Item | Notes | Type |
|---|---|---|
| Compute | The whole demo (mock platforms, Brain, web app) runs on one laptop | Measured (it is how we demo) |
| Lighthouse VM | Deploy kit exists; no instance was bought (capacity sold out) | Not incurred |
| Storage | SQLite index and JSONL ledger under `var/ib/`; growth not measured | Not measured |
| Gate 2 API calls | One call per candidate document per question (≤ 12 hits + 4 per named platform + 4 linked) | Code (`databridge_ib/config.py:20`; `databridge_ib/pipeline.py:36-37`) |

## 5. Controls that bound cost

| Control | Evidence |
|---|---|
| No model call on refusal | `databridge_ib/pipeline.py:163` |
| ≤ 14 passages, ≤ 2 per document | `databridge_ib/pipeline.py:34-35` |
| Question ≤ 2,000 characters; proxy body ≤ 64 KiB | `databridge_ib/models.py:155`; `databridge/apps/internal_brain_web/app/api/ib/[...path]/route.ts:43` |
| Kill switch to stop all model calls | `databridge_ib/llm.py:78-79` |
| **Missing:** per-user rate limit, budget cap, token accounting | `docs/tencent/SECURITY.md:220-224` |
