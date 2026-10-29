# Technical evidence pack

Walnut DataBridge (Internal Brain) · Demo Day 3 Nov 2026. Every number links to the file, test or eval
that proves it. Eval numbers come from
[RESULTS.md](../../../../databridge/evals/internal_brain/RESULTS.md) (run of 14 Oct) and are marked
**re-run pending** until the eval is re-run after the 30 Oct pipeline edits.

| File | What it answers | Use it when a judge asks… |
|---|---|---|
| [ARCHITECTURE_ONE_PAGER.md](ARCHITECTURE_ONE_PAGER.md) | One diagram, the request path step by step, the trust boundaries, Tencent products, what is mocked | "How does it work?" |
| [EVIDENCE_CARD.md](EVIDENCE_CARD.md) | Every headline number → its proof → the command to reproduce it; review findings and dispositions | "How do you know?" |
| [COMPLETENESS_CHECKLIST.md](COMPLETENESS_CHECKLIST.md) | 22 enterprise-RAG capabilities: 10 Done, 8 Partial, 4 Missing | "Is this production-ready?" |
| [COST_AND_LATENCY.md](COST_AND_LATENCY.md) | Measured latency; how often the model is called; cost per answer (estimates marked) | "What does it cost? How fast is it?" |
| [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md) | Start every service; pre-flight checks; ten failures and their fallbacks | (Team use, before and during the demo) |

CI: [`.github/workflows/ci.yml`](../../../../.github/workflows/ci.yml) runs the Internal Brain pytest
suite (Python 3.13) and the web app typecheck (Node 20) on every push and pull request to `final_v1.0` and
`Tencent`.

Responsible AI evidence: [../responsible_ai/](../responsible_ai/README.md), including the list of claims
we never make.

## Three numbers and their caveats

| Number | Caveat |
|---|---|
| **0 leaks in 5,958** adversarial requests | Capturing fake model + extractive path; Hy3 not live; re-run pending |
| **645 backend tests** passing (30 Oct) | Run locally on Windows; CI run on GitHub not yet recorded |
| **~20 ms** pipeline latency | Model time excluded; mock platforms on localhost |
