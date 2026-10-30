# Evidence notes

How to read the numbers in these deliverables, and where each comes from. Shared by
[impact_feasibility/](README.md) and [ai_innovation/](../ai_innovation/README.md).

## 1. "0 leaks in 7,374": what the upper bound means

Source: `databridge/evals/internal_brain/RESULTS.md` (run of 2026-10-30).

**What was run.** 3,687 adversarial probes. Each probe was run **twice**, once on each answer path:
- the **model path** (a capturing test model standing in for Hy3, behind the same gates and grounding wall);
- the **extractive path** (deterministic quotes, no model).

3,687 × 2 = 7,374 requests. Zero leaked anything.

**The rule of three.** When you see 0 events in *n* independent trials, the one-sided 95% upper bound on the
true rate is about 3 ÷ *n*.

| Reading | *n* | Rule of three | Exact (Clopper-Pearson, one-sided 95%) | Two-sided 95% | Use it? |
|---|---|---|---|---|---|
| **Per answer path** (what our materials say) | 3,687 | **0.08%** | 0.0812% | 0.100% | **Yes. This is the claim.** |
| Both paths pooled | 7,374 | 0.04% | 0.0406% | 0.050% | No. Arithmetic only |
| Requests where the model actually received passages | 1,047 | 0.29% | 0.286% | 0.352% | Yes, as the tighter question "when the model saw text" |

**Why we say 0.08% and not 0.04%.** The 7,374 requests are the same 3,687 probes asked twice, on two
different paths. They are not 7,374 independent tries at one system. Pooling them would count each probe
twice and make the bound look twice as tight as the evidence allows. So the honest statement is
**"0 in 3,687 on each path; 95% upper bound about 0.08% per request on each path."** The 0.04% figure is what
someone gets if they divide 3 by 7,374. If a judge computes it, say: "That pools the two paths; they share
probes, so we quote 0.08% per path."

**What the bound does not cover.** It is about this synthetic estate and this probe mix, on mock platforms.
It says nothing yet about a live tenant. Pilot weeks 2-4 re-run the harness on real permissions.

**The harness can fail.** A deliberately leaky configuration (both gates off) is caught on 18 of 20 probes;
a deny-everything configuration scores 0% recall (`RESULTS.md`, "Eval canaries").

## 2. Other measured numbers

| Claim | Number | Source |
|---|---|---|
| Restricted vs non-existent | 0 mismatches in 400 paired responses (control rebuild also 0 / 400) | `RESULTS.md`, "P2 differential" |
| Prompt injection | 0 hard failures in 1,152 injection-relevant requests | `RESULTS.md`, "Injection" |
| Allowed documents reaching the answer step | 100% of 442 positive probes, both paths | `RESULTS.md`, "Utility" |
| Answer cites the expected source | 93.2% (extractive) to 97.1% (model path) | same |
| Expected refusals that were answered | 7 per path, all from documents the asker could open (relevance, not a leak) | `RESULTS.md`, "Findings" |
| Tamper attacks caught | 6 / 6 | `submission/04_evidence/tamper_demo_output.txt` |
| Backend tests | 1,131 passed, 0 failed (cloud run, 30 Oct, Linux, Python 3.13, commit 831297e); 639 at the 16 Oct submission. Quote the green run only | `databridge/apps/internal_brain/tests/` |
| Browser tests | 166 passed, 0 failed, 41 skipped (Playwright, mock mode, 30 Oct, commit 831297e; skipped = need the live stack, the engine proxy or Hy3); 38 at submission | `databridge/apps/internal_brain_web/tests/e2e/` |
| Mean latency | 19-20 ms per request in-process (no network, no Hy3) | `RESULTS.md`, "Per mode" |

## 3. Things not measured yet

| Item | Status |
|---|---|
| Hy3 answering live in the Brain | **Pending** TokenHub activation (`STATUS.md`; `RESULTS.md` configuration: "hy3: PENDING") |
| Tokens per answer | Assumed 4,000 in / 300 out; the pipeline does not record usage yet |
| Gate 2 latency on real APIs | Not measured; mock platforms only |
| Hours saved per person | Projection; measured in the pilot |

## 4. Label key

**[primary]** checked on the publisher's own page · **[secondary]** press or analyst coverage of a source we
could not open · **[verify]** single or vendor-interested source; check before saying it on stage ·
**[estimate]** our own arithmetic or assumption; say "estimate" out loud.
