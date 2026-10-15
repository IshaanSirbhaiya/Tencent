# Submission pack

Everything to attach to the Tencent Cloud AI CAN DO IT submission form. The form answers are in
[docs/tencent/SUBMISSION.md](../docs/tencent/SUBMISSION.md). The repo is public:
https://github.com/IshaanSirbhaiya/Tencent.

| Folder | What is in it | Use it for |
|---|---|---|
| `01_codebuddy/` | An overview and a full-page screenshot for each of the six CodeBuddy sessions (CB1 to CB6), plus `html/` sources. Rendered from the saved transcripts in `docs/tencent/evidence/`. | The CodeBuddy proof. Attach at least three overviews (CB2, CB5, CB6). |
| `02_workbuddy/` | `workbuddy_mcp_panel.png` (the three connectors, enabled), `workbuddy_compare.png` (one question through two people's connectors, two different answers), `workbuddy_compliance.png` (the compliance connector lists one person's access and verifies the audit chain). `raw/` holds the uncomposed window shots. | The WorkBuddy proof. |
| `03_app_scenes/` | `internal_brain/`: the web app (answer with checks, map, compare, tour, audit). `walnut_original/`: the original Walnut screens on Company A. | Extra screenshots and slides. |
| `04_evidence/` | `tamper_demo_output.txt`: the output of the tamper demo. | Shows the audit log catches edits. |
| `05_video/` | `walnut_internal_brain_demo.mp4` (5:55, silent), `narration_cues.md`, `narration.srt`, `timeline.json`, `cards/` (still cards), `clips/` (one recording per scene, with `.marks.json`). | The video. Record the voice-over from the cue sheet, then upload. |
| `06_cover/` | The cover image. Empty for now; it is added after the freeze. | The form's cover field. |
| `07_deck/` | `walnut_internal_brain_deck.pdf` and `.pptx` (13 slides, speaker notes in the PPTX), plus `html/` and `png/`. | The submission and Demo Day. |

## Regenerate

Run from the repo root.

| Item | Commands |
|---|---|
| CodeBuddy pages | `python docs/tencent/evidence/render_transcripts.py`, then `node docs/tencent/evidence/shoot_sessions.cjs` |
| Video cards | `python docs/tencent/video/make_cards.py`, then `node docs/tencent/video/shoot_cards.cjs` |
| Video clips (the stack must be running) | `node docs/tencent/video/record_scenes.cjs [scene ...]` |
| Video | `python docs/tencent/video/assemble.py [--voice narration.m4a]` |
| Narration cues and subtitles | `python docs/tencent/video/narration.py` |
| Deck | `python docs/tencent/deck/make_deck.py html`, then `node docs/tencent/deck/shoot_deck.cjs`, then `python docs/tencent/deck/make_deck.py pack` |

The WorkBuddy screenshots are real window captures. They are not regenerated; see
[the WorkBuddy guide](../docs/tencent/workbuddy/README.md) to set up the same view.

## No secrets

Nothing in this folder holds a key, a token or a real person's data. All people and companies are synthetic.
