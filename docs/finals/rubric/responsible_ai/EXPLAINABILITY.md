# Explainability: why you got this answer, and why we never explain a refusal

> **Status.** 30 Oct 2026. Sections 1-3 describe what is built and tested. Section 4 lists what is in
> progress this week.
>
> **References.** `databridge_ib/…` = `databridge/apps/internal_brain/databridge_ib/…`; `tests/…` =
> `databridge/apps/internal_brain/tests/…`. Line numbers refer to commit `e5406b9` (read them with `git show e5406b9:<path>`); the code is being edited this week, so the function or test name is the stable part.

## 1. Three audiences, three levels of explanation

| Audience | What they can see | Why that level | Evidence |
|---|---|---|---|
| **The asker** | The answer; a citation per sentence with source, version, `content_checked_at`, `authz_checked_at` and `live_read`; the model that wrote it; one constant scope note | Enough to check every claim against its source | `databridge_ib/models.py:158-185` |
| **The asker, on request** (`/ib/trace/{req_id}`, own requests only) | Stages and per-stage timings, model, prompt version, grounding drops, released and cited documents, the sealed ledger records (seq, hash) | Shows *how* their answer was made, using only what they were already shown | `databridge_ib/app.py:176-203`; `tests/test_dev_views.py::test_trace_is_own_requests_only_and_hides_gate2_reasons` (line 93) |
| **Compliance** (`/audit/journey/{req_id}`, any request) | Everything above plus the considered set, every Gate 2 verdict and reason code; no answer text (that needs a reasoned reveal) | Investigations need the full decision trail | `databridge_ib/audit/routes.py:249-264`; `tests/test_dev_views.py::test_journey_is_compliance_only_and_has_no_answer_text` (line 76) |

## 2. What a citation tells you

Each citation (`databridge_ib/models.py:158-170`):

| Field | Meaning |
|---|---|
| `platform`, `container_name`, `title`, `deep_link` | Where the sentence came from; click through to the source |
| `version`, `updated_at` | Which version of the document was used |
| `content_checked_at` | When the text was read |
| `authz_checked_at` | When the source confirmed, live, that you may open it (Gate 2) |
| `live_read` | The document had changed, so it was re-fetched and re-checked at answer time (`tests/test_pipeline_core.py::test_drift_read_through`, line 191) |
| `stale` | The source's sync is behind its freshness target |

The model is told to tag every sentence with its passage (`databridge_ib/narrate.py:27`). Only tags that
match a released passage become citations (`databridge_ib/pipeline.py:181-183`). A model answer that cites
no released passage is replaced by the extractive answer (`databridge_ib/pipeline.py:171-179`).

**Extractive answers explain themselves.** They are up to four verbatim sentences from the released
passages, each tagged with its source (`databridge_ib/narrate.py:145-197`). Nothing is paraphrased.

## 3. The deliberate asymmetry: we never explain a refusal to the asker

An asker who gets the refusal sees the same text whether the content is restricted, does not exist, or is
just not relevant (`databridge_ib/models.py:20-21`). We do **not** show:

- how many documents were considered or hidden;
- that "something exists but is hidden";
- any Gate 2 reason code.

**Why.** In a bank, confirming that a suspicious-transaction file exists can itself be an offence
(tipping-off, CDSA s.57; `docs/tencent/MAS_TRM_MAPPING.md:53`). Any explanation of a denial leaks that
the thing exists. We tested this: restricted and non-existent requests give byte-identical responses
(`tests/test_scenarios_e2e.py::test_s3_existing_vs_nonexistent_identical`, line 53;
`tests/test_fintech_scenarios.py::test_fc_real_vs_nonexistent_customer_is_byte_identical`, line 100;
P2: 0 of 400 mismatches, `RESULTS.md:16`, re-run 30 Oct).

The new UI keeps this rule: research suggested "Searched N (K allowed, J hidden)" and we rejected it
(`docs/finals/DECISIONS.md` D-07). The own-request trace hides the considered set and reason codes, and
someone else's request id gives the same 404 as an unknown one (`tests/test_dev_views.py`, line 93-108).

**The explanation still exists, for the right person.** Compliance can reconstruct any refusal through
`/audit/journey`, and that access is itself logged.

## 4. Built (30 Oct)

| Item | Status | Evidence |
|---|---|---|
| Per-stage timings in `answer.released.stage_ms` | Built and tested | `databridge_ib/pipeline.py:185-188`; `tests/test_dev_views.py::test_answer_release_records_stage_timings` (line 37) |
| `/ib/trace`, `/audit/journey`, `/audit/stats` | Built and tested | `tests/test_dev_views.py` |
| "How this was answered" link under each answer → request journey waterfall | **Built** (web UI, `/dev/journey`) | `components/user/AnswerCard.tsx`; `docs/finals/PLAN.md` §3.1-3.2 |
| Permission-aware graph (`/ib/graph`) and document view (`/ib/doc`) with "why I can see it" | **Built** (`/ib/graph`, `/ib/doc`) | `tests/test_graph_doc.py` |

## 5. Limits

- Citations show *where* a claim came from, not *why* the model combined claims as it did. Relations
  between facts are not verified (`INTERNAL_BRAIN.md:251`).
- The trace shows timings and records, not the model's internal reasoning.
- Hy3 has not run live, so no real-model explanation has been observed.
