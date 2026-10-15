# Status

**Date:** 15 Oct 2026. Submission freeze is 10:00 SGT on 15 Oct. The deadline is Fri 16 Oct, 23:59 SGT.

This is Walnut's entry to the Tencent Cloud AI CAN DO IT Hackathon, FinTech track (Aspire): the **Internal Brain**.
Public repo: https://github.com/IshaanSirbhaiya/Tencent. Start at [INTERNAL_BRAIN.md](INTERNAL_BRAIN.md).

## Done

| Area | State | Where |
|---|---|---|
| Internal Brain service: two access checks, grounding, write-ahead audit, one response shape | Built | `databridge/apps/internal_brain/` |
| Web app: ask, map, compare, audit, simulator, guided tour | Built | `databridge/apps/internal_brain_web/` |
| Original Walnut screens on Company A | Running on the same data | `databridge/apps/frontend/` |
| WorkBuddy connectors (ask as a person; compliance audit) | Built, shown in WorkBuddy | `docs/tencent/workbuddy/`, `submission/02_workbuddy/` |
| CodeBuddy sessions (6) with saved transcripts | Done | `docs/tencent/evidence/`, `submission/01_codebuddy/` |
| Tests | 634 Internal Brain tests and 38 web end-to-end tests pass | `databridge/apps/internal_brain/tests/`, `databridge/apps/internal_brain_web/tests/e2e/` |
| Leak evaluation | 0 leaks in 5,958 requests (2,979 probes on 2 paths); 0 of 400 pairs differ | `databridge/evals/internal_brain/RESULTS.md` |
| Original code share | 93% of code lines are unchanged Walnut | `python databridge/scripts/measure_original_share.py` |
| Deck (13 slides, PDF and PPTX) | Done | `submission/07_deck/` |
| Demo video, 5:55, silent, with a narration cue sheet and subtitles | Done | `submission/05_video/` |
| Lighthouse deploy kit | Written; no live instance | `deploy/lighthouse/` |

## Waiting on

| Item | Who | Note |
|---|---|---|
| Tencent Hy3 through TokenHub | Tencent (account verification pending) | Hy3 is configured but has not been run live. Until it is approved, answers quote the sources, with the same checks. To switch: `IB_FORCE_OFFLINE=0`. |
| Lighthouse server | Tencent (sold out) | Optional. The local run is the documented path. |

## Next

1. Record the voice-over on the video, using `submission/05_video/narration_cues.md`.
2. Make the cover image and put it in `submission/06_cover/`.
3. Fill in the submission form ([answers](docs/tencent/SUBMISSION.md)) and attach the pack ([index](submission/README.md)).
4. Get the teammates' confirmation.
5. When Hy3 is approved, run it once and record the result here.
