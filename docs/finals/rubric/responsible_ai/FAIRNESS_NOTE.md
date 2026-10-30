# Fairness note

> **Status.** 30 Oct 2026. Self-assessed. Synthetic data only. Numbers come from
> [RESULTS.md](../../../../databridge/evals/internal_brain/RESULTS.md) (14 Oct). Headline numbers were
> re-run on the finals code on 30 Oct (`eval_summary.json`) and match; per-role figures are from the 14 Oct run.
>
> **References.** `databridge_ib/…` = `databridge/apps/internal_brain/databridge_ib/…`. Line numbers refer to
> commit `e5406b9`.

## 1. Why classic fairness metrics do not apply directly

Fairness metrics such as demographic parity, and MAS Veritas's fairness methodology, are built for
**decisions about people**: credit, insurance, hiring, marketing. The Internal Brain makes no decision about
anyone. It answers staff questions from documents they can already open, and it is scoped as "not a
decision-maker" (`docs/tencent/RESPONSIBLE_AI.md:14-15`). We also hold no protected attributes (age,
gender, race) for any persona, so we could not compute group metrics even if they applied.

So we do not claim to have "tested for bias". We say what fairness means for this kind of system and
measure what we can.

## 2. What fairness means here

| # | Meaning | How we measure or enforce it | Result |
|---|---|---|---|
| 1 | **Equal rules for everyone.** Every role goes through the same two gates, the same ranking and the same grounding. No role gets wider *content* access through the Brain, including compliance and IT. | Code path is the same for every principal (`databridge_ib/pipeline.py:99-200`); `docs/tencent/MAS_TRM_MAPPING.md:17` | By design |
| 2 | **Equal quality of service across roles.** People with the same access should get similarly good answers. | Per-persona released and cited recall (`RESULTS.md:118-141`) | Uneven for some roles (section 3) |
| 3 | **Fair exposure across sources.** One platform should not crowd out the others. | After Gate 2, passages are interleaved round-robin across platforms before the 14-passage cap (`databridge_ib/pipeline.py:260-270`) | By design; not separately measured |
| 4 | **Denial is not unfairness.** A contractor seeing less is the company's own access policy, mirrored from the source, not created by us. | Gate 1 compiles native permissions; Gate 2 asks the source (`docs/tencent/ARCHITECTURE.md:141-145`) | By design |
| 5 | **Equal refusal.** Everyone who is refused gets the same words, whatever the reason. | `databridge_ib/models.py:20-21` | P2: 0 / 400 (re-run 30 Oct) |

## 3. Quality of service by role (30 Oct run)

Released recall (the share of allowed documents that reach the answer step) is **100% for every persona
on both paths** (`RESULTS.md:118-141`). Cited recall (the answer cites the expected source) varies:

| Persona | Role | Positive probes | Cited recall, model path | Cited recall, extractive | World must-cite, model path |
|---|---|---|---|---|---|
| `admin.it` | IT admin | 51 | 98.0% | 94.1% | 100.0% (3 questions) |
| `jdoe` | Backend engineer | 85 | 96.5% | 91.8% | 86.7% |
| `ken.ext` | Contractor | 4 | 100.0% | 100.0% | 100.0% |
| `leo.ops` | Product / ops | 77 | 97.4% | 96.1% | 88.2% |
| `maya.compliance` | Compliance | 53 | 98.1% | 94.3% | 100.0% |
| `priya.sre` | On-call SRE | 87 | 96.6% | 92.0% | 100.0% |
| `sara.sec` | Security | 85 | 96.5% | 91.8% | 100.0% |
| `amy.am` | Account manager | 0 | n/a | n/a | 100.0% (6 questions) |
| `mlro.tan` | MLRO | 0 | n/a | n/a | 90.0% (10 questions) |
| **All** | | 442 | 97.1% | 93.2% | 93.6% |

Source: `RESULTS.md:118-141`. The positive probes grew from 196 to 442 when the everyday-company layer
(leave, expenses, IT helpdesk, office; open to every employee) was added to the world on 30 Oct.

**Where it is uneven, and why we think so.**
- World must-cite on the model path is lowest for `jdoe` (86.7%, 13 of 15) and `leo.ops` (88.2%, 15 of
  17). The extractive path scores 100% on the same questions for both (`RESULTS.md:134, 136`). We have not
  diagnosed the cause.
- The extractive path is weakest for `jdoe` and `sara.sec` (91.8%) and `priya.sre` (92.0%).
- `amy.am` and `mlro.tan` have no generated positive probes, only world questions. Their coverage is thin.
- The 7 expected-refusal questions answered from related pages (`RESULTS.md:24`) fall on `admin.it`,
  `amy.am`, `leo.ops`, `maya.compliance` and `mlro.tan`. This is a relevance issue, not access.

**Caveat.** All model-path numbers use a capturing fake model, not Hy3. Real-model quality per role is
not measured.

## 4. Inclusion gaps

| Gap | Effect | Plan |
|---|---|---|
| English only | Staff working in Bahasa Indonesia, Thai or Vietnamese get no tested service | Roadmap. The UI language switch (English / 简体中文) covers the interface, not answers (`docs/finals/PLAN.md` §4) |
| Market content for SG and ID only | Staff in TH and VN markets have no market-specific policies in the synthetic world | Synthetic-data limit ([DATA_CARD_COMPANY_A.md](DATA_CARD_COMPANY_A.md) §6) |
| Lexical retrieval | Questions phrased differently from the source text may miss; this affects people who do not know internal jargon | Semantic reranker on released passages is the next step (`../research/D_technical_responsible_ai.md` §1.6 Q5) |
| Accessibility | axe checks pass at desktop and phone width; no testing with assistive technology | `databridge/apps/internal_brain_web/tests/e2e/a11y.spec.ts`; `docs/tencent/RESPONSIBLE_AI.md:66-71` |

## 5. Known research on RAG fairness

Retrieval can introduce exposure bias: what gets retrieved shapes what gets said
([Dehghan & McDonald, 2026](https://arxiv.org/abs/2603.24218);
["Does RAG introduce unfairness in LLMs?"](https://arxiv.org/pdf/2409.19804)). Our round-robin across
platforms addresses one form of this (source crowding). We have not measured exposure across authors,
teams or document age.

## 6. What we would add with real data

1. Quality of service by team, market and language, with confidence intervals.
2. Exposure by author and team: who gets cited, and how often.
3. A feedback route ("wrong source", "out of date") and its rates by role. Not built.
