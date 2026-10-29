# Evidence notes

How to read the numbers in these deliverables, and where each comes from. Shared by
[impact_feasibility/](README.md) and [ai_innovation/](../ai_innovation/README.md).

## 1. "0 leaks in 5,958": what the upper bound means

Source: `databridge/evals/internal_brain/RESULTS.md` (run of 2026-10-14).

**What was run.** 2,979 adversarial probes. Each probe was run **twice**, once on each answer path:
- the **model path** (a capturing test model standing in for Hy3, behind the same gates and grounding wall);
- the **extractive path** (deterministic quotes, no model).

2,979 × 2 = 5,958 requests. Zero leaked anything.

**The rule of three.** When you see 0 events in *n* independent trials, the one-sided 95% upper bound on the
true rate is about 3 ÷ *n*.

| Reading | *n* | Rule of three | Exact (Clopper-Pearson, one-sided 95%) | Two-sided 95% | Use it? |
|---|---|---|---|---|---|
| **Per answer path** (what our materials say) | 2,979 | **0.10%** | 0.1005% | 0.124% | **Yes. This is the claim.** |
| Both paths pooled | 5,958 | 0.05% | 0.0503% | 0.062% | No. Arithmetic only |
| Requests where the model actually received passages | 683 | 0.44% | 0.438% | 0.539% | Yes, as the tighter question "when the model saw text" |

**Why we say 0.10% and not 0.05%.** The 5,958 requests are the same 2,979 probes asked twice, on two
different paths. They are not 5,958 independent tries at one system. Pooling them would count each probe
twice and make the bound look twice as tight as the evidence allows. So the honest statement is
**"0 in 2,979 on each path; 95% upper bound about 0.10% per request on each path."** The 0.05% figure is what
someone gets if they divide 3 by 5,958. If a judge computes it, say: "That pools the two paths; they share
probes, so we quote 0.10% per path."

**What the bound does not cover.** It is about this synthetic estate and this probe mix, on mock platforms.
It says nothing yet about a live tenant. Pilot weeks 2-4 re-run the harness on real permissions.

**The harness can fail.** A deliberately leaky configuration (both gates off) is caught on 18 of 20 probes;
a deny-everything configuration scores 0% recall (`RESULTS.md`, "Eval canaries").

## 2. Other measured numbers

| Claim | Number | Source |
|---|---|---|
| Restricted vs non-existent | 0 mismatches in 400 paired responses (control rebuild also 0 / 400) | `RESULTS.md`, "P2 differential" |
| Prompt injection | 0 hard failures in 976 injection-relevant requests | `RESULTS.md`, "Injection" |
| Allowed documents reaching the answer step | 100% of 196 positive probes, both paths | `RESULTS.md`, "Utility" |
| Answer cites the expected source | 87.8% (extractive) to 96.4% (model path) | same |
| Expected refusals that were answered | 7 per path, all from documents the asker could open (relevance, not a leak) | `RESULTS.md`, "Findings" |
| Tamper attacks caught | 6 / 6 | `submission/04_evidence/tamper_demo_output.txt` |
| Backend tests | 733 passing (commit 8582f4b, 30 Oct); 639 at the 16 Oct submission. Quote the green run only | `databridge/apps/internal_brain/tests/` |
| Browser tests | 38 at submission | `databridge/apps/internal_brain_web/tests/e2e/` |
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
