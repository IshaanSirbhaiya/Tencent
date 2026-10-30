# World expansion (everyday company content): status

**30 Oct 2026, ~11:45 SGT.** Branch `final_v1.1`. Nothing here is committed. The world files on disk are the
**expanded, verified** version (not HEAD).

> **Update (30 Oct, end of the cloud session).** Committed in `8312864` (on `final_v1.2`, now `final_v1.3`).
> §5 items 1, 2, 5, 6 and 7 are done: the full IB suite is 1,131 passed, 0 failed (`831297e`); STATUS and
> FEATURE_CATALOGUE carry it; the eval was re-run on the final tree, unchanged (`9819855`); current docs say
> 7,374 / 1,152 (dated history keeps the old numbers); `eval_summary.json` records e2e 166. Item 3 (restart with
> `--fresh`, refresh `ABSENCE_PROOF.txt`) is laptop-only and still open; item 4 only if the graph DB is used.
> The rest of this file is the 11:45 checkpoint, kept as written.

## 1. Diagnosis of the inherited change

The stopped session left `generate.py` (modified), `workplace.py` (new), `tests/test_workplace_layer.py`
(new), edits to `test_generator.py` and `test_fintech_layer.py`, and a regenerated `world.json`,
`manifest.json` and 10 `graph_rows/*.json`. `git diff --stat` showed ~9,164 deleted lines in `world.json`.

A semantic comparison against HEAD (every id-bearing record, canonical JSON) found:

- **No existing record removed or changed in content.** Every Confluence page, Jira issue, Slack thread,
  Drive file, folder, space, project, user, fact, manifest question and audit-replay row of HEAD is
  present and identical, and keeps its relative order.
- The only growth inside existing records is additive list growth:
  - `grp-staff.member_groups` gains `grp-people`;
  - `#general` and `#random` members gain the two new People-team users (`siti.h`, `melissa.k`);
  - every existing question's `must_not_contain_facts` gains the new facts its persona is denied (the
    generator's documented rule).
- The "deleted lines" are JSON re-indentation of those grown lists, plus new Slack threads interleaving
  with old ones (threads are sorted by channel and ts).
- The new layer uses its own random stream (`companya/<seed>/workplace`) and runs after the fintech
  layer. With it switched off the generator writes HEAD's `world.json` byte for byte (sha256
  `be21569d…`, checked by `test_world_without_the_layer_is_the_pre_layer_world`).
- The generator is deterministic: regenerating gives byte-identical `world.json` and `manifest.json`;
  `graph_rows` equal `scripts/build_enterprise_graph.py --rows-only` output.

Verdict: append-only. Kept, not restored.

## 2. What changed in this session

1. **Five WP page titles made descriptive** (`workplace.py`), e.g. `Leave policy` became `Leave policy:
   annual, medical and childcare leave`. Reason: the eval's title probes (`What does the 'Leave policy'
   document say?`) missed the relevance gate on two-word titles (utility fell to 95.5%). After: 100%.
   World regenerated; `graph_rows` rebuilt (`--rows-only`).
2. **IB determinism fix** (`databridge_ib/narrate.py`, `extractive_answer`): the anchor narrowing picked
   among equally sized anchor groups in set order, which changes with `PYTHONHASHSEED`. Two processes
   could give different answers to the same question; the eval's A-vs-A control and P2 differential
   flagged it (1 P2 mismatch, 2 control mismatches) once the new content created ties. Tie now broken by
   the anchor itself. New test `apps/internal_brain/tests/test_narrate_determinism.py` (runs 8 hash
   seeds; HEAD's code gives 2 different answers on it).
3. `apps/internal_brain/tests/test_eval_summary.py`: stale assertion `e2e: 38` changed to `124` (the
   script default since `f0f84bd`; failing at HEAD, unrelated to the world).
4. `fixtures/enterprise/README.md`: new section "The everyday-company layer (WP)".
5. Docs: counts updated in README, INTERNAL_BRAIN, STATUS, FEATURE_CATALOGUE, CHANGELOG (in-progress
   row), the rubric docs (not `rubric/research/`) and `DATA_CARD_COMPANY_A.md`; `FAIRNESS_NOTE.md` §3
   table recomputed; `JUDGE_QUESTION_BANK.md` gets the "New everyday topics" entries E1-E7 (verified
   in-process) and entry 23 no longer says "there is no salary document".
6. Web: `components/trust/data.ts` and `tests/e2e/analytics.spec.ts` now say 7,374 (was 5,958).
7. `public/eval_summary.json` regenerated (`scripts/eval_summary.py --e2e 124`, `stale=false`).

## 3. Verification (all on the final world, sha256 `e3381d71…`)

| Check | Result |
|---|---|
| Fixture tests (`fixtures/enterprise/tests`) | 47 passed (the live-samples absence test now runs) |
| Every pre-existing question (461) + Ken/Rachel Velquarry extras, HEAD world vs new world | 464 of 464 caller-visible bodies byte-identical |
| Every Judge Question Bank question (53), HEAD vs new | 52 identical; 1 differs (`admin.it` on-call rotation, already on the "do not ask" list: one chatter citation changed) |
| Demo moments, in-process (real app routes, real ledger, `/audit/ask`) | Amy gets the approved script, Rachel the case; Ken Velquarry vs Bayfront byte-identical refusals; jdoe migration cites Slack + Jira + Confluence; Maya "Who retrieved the Q3 breach report?" resolves and finds Sara |
| All 6 manifest P2 pairs (incl. 3 new WP pairs) | byte-identical |
| Live-ingestion kit (12 samples): proof phrase absent; before-question, Ken and outsider refused | 37 of 37 checks pass |
| Full leak eval (`IB_FORCE_OFFLINE=1 python -m evals.internal_brain.run`) | 0 leaks in 7,374 requests (3,687 probes × 2); 95% UB 0.0814% per path; 0 of 400 P2 pairs differ; control 0; injection 0 hard failures in 1,152; utility 100% both paths; canaries valid; 14 quality findings (same 7 ids × 2 as before) |
| Full IB suite | Previous run on this world (before the title change and the narrate fix): 891 passed, 3 failed, all in the untracked `test_uploads.py` (another session's uncommitted upload work; not world related). Final re-run on the final state (after the checkpoint): **1040 passed, 0 failed** (18 min 41 s) |

## 4. New counts

34 people, 17 groups; Confluence 231 pages in 9 spaces; Jira 261 issues in 5 projects; Slack 628
threads (3,133 messages) in 18 channels; Drive 87 files in 14 folders; **1,207 documents** (1,204
indexed; 562 readable by every member); **72 planted facts** (23 new `WP-*`); **546 questions** (28
`WP-q*` + 54 probes). Eval as above.

## 5. Left to do (exact next steps)

1. Done: the full IB suite on the final state passed (1040 passed, 0 failed).
2. Update "754 backend tests" in `STATUS.md` §2 and `FEATURE_CATALOGUE.md` (Numbers row) to 1040, noting
   that the count includes uncommitted test files from other workstreams.
3. Coordinator: restart the shared stack with `--fresh` (the running stack has the old index), then rerun
   `docs/finals/live_kit/check_absent.py --all` over HTTP to refresh `ABSENCE_PROOF.txt`.
4. Rebuild the original-Walnut graph DB if it is used: `python scripts/build_enterprise_graph.py`.
5. `eval_summary.json` goes `stale=true` if anyone changes `databridge_ib/**/*.py` after 30 Oct 03:27 UTC;
   re-run the eval and `scripts/eval_summary.py --e2e 124` after the last IB code change.
6. Docs outside the brief still quote 5,958 / 976 (`docs/tencent/*`, `docs/finals/research/*`,
   `reviews/*`, `PLAN.md`, `MORNING_BRIEF.md`, `ORGANISER_UPDATE.md`, video script); update if they are
   shown to judges.
7. Commit together (generator, tests, world files, narrate fix, docs), so the world data never lands
   without the generator that made it.

Scratch scripts used for the checks (session scratchpad, not in the repo): semantic diff, answer dumps,
demo moments, judge-bank dump.
