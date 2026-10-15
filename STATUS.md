# STATUS: where the build is

**Last updated:** 15 Oct 2026, 09:50 SGT (submission freeze 10:00 SGT; deadline Fri 16 Oct 23:59 SGT).

This branch is Walnut's entry to the Tencent Cloud AI CAN DO IT Hackathon (FinTech track, Aspire):
the **Internal Brain**. Start at [INTERNAL_BRAIN.md](INTERNAL_BRAIN.md).

## Done

| Area | State | Evidence |
|---|---|---|
| Internal Brain service (two access checks, grounding, write-ahead audit, one response shape) | Built | `databridge/apps/internal_brain/` |
| Web app: ask, map, compare, audit, sim, guided tour, info pop-ups | Built | `databridge/apps/internal_brain_web/` |
| Original Walnut screens on Company A (graph, brain, evidence, ontology, demo tour) | Running on the same data | `databridge/apps/frontend/`, `scripts/run_walnut_original.sh` |
| WorkBuddy connectors (ask as a person; compliance audit) | Built and shown in WorkBuddy | `docs/tencent/workbuddy/`, `submission/02_workbuddy/` |
| CodeBuddy sessions (6) with saved transcripts | Done | `docs/tencent/evidence/`, `submission/01_codebuddy/` |
| Leak evaluation | 0 leaks in 5,958 adversarial requests; 0 differences in 400 pairs; 0 injection failures; 100% recall | `databridge/evals/internal_brain/RESULTS.md` |
| Automated tests (Internal Brain) | 634 passing | `databridge/apps/internal_brain/tests/` |
| Reviews | Codex tracks 1–7, CodeBuddy security review, two Claude Opus/Sonnet/Haiku rounds; every finding fixed or explained | `docs/tencent/SECURITY.md` §9–§14, `docs/tencent/reviews/` |
| Original-code share | 93% of today's code lines are unchanged Walnut (93.0% at the final commit) | `python databridge/scripts/measure_original_share.py` |
| Deck (13 slides, PDF + PPTX) | Done | `submission/07_deck/` |
| Demo video (silent, 5:55) + voice-over cue sheet and subtitles | Done | `submission/05_video/` |

## Waiting on

| Item | Owner | Note |
|---|---|---|
| Tencent Hy3 through TokenHub | Tencent (account verification) | Until approved the demo answers by quoting sources (same checks). Switch: `IB_FORCE_OFFLINE=0`. |
| Lighthouse server | Tencent (sold out) | Optional; local run is the documented path. |
| Voice-over, Miora cover, form submission, teammates' confirmation | Ishaan | After the freeze |
