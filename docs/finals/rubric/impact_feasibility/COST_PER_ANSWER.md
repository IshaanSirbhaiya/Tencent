# Cost per answer (Hy3 on TokenHub)

**Status: estimate.** Prices are Tencent's published list prices. Token sizes are an assumption from
`docs/tencent/SUBMISSION.md` §4 (about 4,000 tokens in, 300 out). Hy3 has not answered live in the Brain
yet, so no token count here is measured. Replace the assumption after the live run (§5).

## 1. Prices

Tencent Cloud model pricing page, updated 26 Oct 2026, US$ per million tokens
([source](https://www.tencentcloud.com/document/product/1300/78937), [primary]):

| Model | Input | Output | Cached input |
|---|---|---|---|
| **Hy3** | **0.132** | **0.528** | 0.033 |
| Hy4 preview (comparison only; we have not tested it) | 0.834 | 2.501 | 0.042 |

Both regional tables on that page show the same Hy3 price. The page does not state free-trial terms.
The DeepSeek fallback price did not render for us; it is not priced here [verify].

## 2. Cost per answer

| Case | Input tokens | Output tokens | Arithmetic | Per answer (US$) | Per 1,000 answers |
|---|---|---|---|---|---|
| Typical, Hy3 | 4,000 | 300 | 4,000 × 0.132e-6 + 300 × 0.528e-6 | **0.00069** | US$0.69 |
| Typical, 1,500-token system prompt cached | 1,500 cached + 2,500 | 300 | 0.0000495 + 0.00033 + 0.000158 | 0.00054 | US$0.54 |
| Long answer, Hy3 | 12,000 | 800 | 0.001584 + 0.000422 | 0.0020 | US$2.01 |
| Typical, Hy4 preview | 4,000 | 300 | 0.003336 + 0.000750 | 0.0041 | US$4.09 |
| **Refusal** (Gate 1, relevance gate or Gate 2 leaves nothing) | 0 | 0 | no model call, by design | **0** | 0 |
| **Extractive fallback** (model down or grounding fails) | 0 | 0 | deterministic quote | **0** | 0 |
| NL audit question | 0 | 0 | rule-based parser; no model is wired in (`databridge_ib/bootstrap.py`) | 0 | 0 |

Cached-input saving depends on TokenHub caching our system prompt; not tested [verify].

## 3. Per seat and per year [estimate]

| Usage | Answers per seat per month | Per seat per month | 600 seats per year |
|---|---|---|---|
| 10 answers a day × 21 days | 210 | 210 × 0.00069 = **US$0.14** | 600 × 210 × 12 × 0.0006864 ≈ **US$1,040** |
| 50 answers a day × 21 days | 1,050 | US$0.72 | about US$5,190 |
| 50 a day, all long answers | 1,050 | US$2.11 | about US$15,200 |

These figures assume every question reaches the model. Refusals cost nothing, so real spend is lower.
(In the adversarial eval, 2,640 of 3,687 requests were refused before any model call. That mix is an
attack set, not a usage forecast: `RESULTS.md`, "Per mode".)

## 4. What this means

- **One line for stage:** "An answer costs under a tenth of a US cent on Hy3. Refusals cost nothing,
  because they never reach the model."
- **The model is not the cost driver.** Support, implementation and connector API quotas are
  ([SCALE_ARCHITECTURE.md](SCALE_ARCHITECTURE.md) §3, [RISK_REGISTER.md](RISK_REGISTER.md) R2).
- Hosting at pilot size is a separate estimate: about US$150-400 a month
  ([SCALE_ARCHITECTURE.md](SCALE_ARCHITECTURE.md) §2).

## 5. How to replace the estimate (after Hy3 runs live)

1. Activate Hy3 in the TokenHub console and confirm `GET https://tokenhub-intl.tencentcloudmaas.com/v1/models`
   lists `hy3` as online.
2. Record the `usage` fields (prompt and completion tokens) of each live answer. The pipeline does not store
   them today (no `usage` field in `databridge_ib/`); add it to the journey, not to the asker's response.
3. Ask 50 starter questions across personas. Report mean and p95 tokens in and out, and p50/p95 latency.
4. Recompute §2 and §3 with the measured means. Update the `tokens_in` and `tokens_out` defaults in
   [value_calculator.spec.json](value_calculator.spec.json).
5. Until then, say "estimate" out loud.
