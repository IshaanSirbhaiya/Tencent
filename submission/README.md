# Submission pack

Everything to attach to the Tencent Cloud AI CAN DO IT submission form. The form answers are in
[docs/tencent/SUBMISSION.md](../docs/tencent/SUBMISSION.md). The repo is public:
https://github.com/IshaanSirbhaiya/Tencent.

| Folder | What is in it | Use it for |
|---|---|---|
| `01_codebuddy/` | `conversation_history_codebuddy_workbuddy_miora.pdf` (one 16-page file for the form: CB1-CB9, including the CB8 resume, the three WorkBuddy shots and the two Miora sessions; rebuilt 30 Oct by `docs/tencent/evidence/build_conversation_pdf.py`). An overview and a full-page screenshot for each CodeBuddy session (CB1 to CB6 from the first round; CB7, CB8, CB8 resumed and CB9 on the finals code), plus `html/` sources. Rendered from the saved transcripts in `docs/tencent/evidence/`. | The CodeBuddy proof. Attach at least three overviews (CB2, CB5, CB6). |
| `02_workbuddy/` | `workbuddy_mcp_panel.png` (the three connectors, enabled), `workbuddy_compare.png` (one question through two people's connectors, two different answers), `workbuddy_compliance.png` (the compliance connector lists one person's access and verifies the audit chain). `raw/` holds the uncomposed window shots. | The WorkBuddy proof. |
| `03_app_scenes/` | `internal_brain/`: the web app (answer with checks, map, compare, tour, audit). `walnut_original/`: the original Walnut screens on Company A. | Extra screenshots and slides. |
| `04_evidence/` | `tamper_demo_output.txt`: the output of the tamper demo. | Shows the audit log catches edits. |
| `05_video/` | `walnut_internal_brain_demo.mp4` (6:28: camera intro, then the narrated demo), `narration_cues.md`, `narration.srt`, `timeline.json`, `cards/` (still cards), `clips/` (one recording per scene, with `.marks.json`). | The video to upload. |
| `06_cover/` | `cover_380x216.png` (the form's size) and `cover_1920x1080.png`; `cover_master_miora.png` is Miora's raw art, `miora_session1.png` / `miora_session2.png` show the Miora prompts and results; `youtube_thumbnail_miora.png` (and a 1280-wide copy) is the YouTube thumbnail, also made in Miora | The form's cover field; proof of Miora use |
| `07_deck/` | `walnut_internal_brain_deck.pdf` and `.pptx` (23 slides, speaker notes in the PPTX), plus `html/` and `png/`. | The submission and Demo Day. |
| `finals/` | Finals (30 Oct): `leave_behind_A4.pdf` (with a QR code to the repo), `judge_cards.pdf` (one card per rubric criterion), `presenter_cheatsheet.pdf`; `handouts_src/` holds the HTML and the generator, which reads the numbers from `eval_summary.json`, `STATUS.md` and `ROI_MODEL.md` | Print for Demo Day |
| `finals_video/` | *In progress, not committed:* cards and raw clips for the new finals video ([script](../docs/finals/video/VIDEO_SCRIPT_FINALS.md)) | The new video, when finished |

## Regenerate

Run from the repo root.

| Item | Commands |
|---|---|
| CodeBuddy pages | `python docs/tencent/evidence/render_transcripts.py`, then `node docs/tencent/evidence/shoot_sessions.cjs` |
| Video cards | `python docs/tencent/video/make_cards.py`, then `node docs/tencent/video/shoot_cards.cjs` |
| Video clips (the stack must be running) | `node docs/tencent/video/record_scenes.cjs [scene ...]` |
| Video | `python docs/tencent/video/assemble.py [--voice narration.m4a]` |
| Narration cues and subtitles | `python docs/tencent/video/narration.py` |
| Finals handouts | `node submission/finals/handouts_src/build_handouts.mjs` (needs the web app's `node_modules`) |
| Deck | `python docs/tencent/deck/make_deck.py html`, then `node docs/tencent/deck/shoot_deck.cjs`, then `python docs/tencent/deck/make_deck.py pack` |

The WorkBuddy screenshots are real window captures. They are not regenerated; see
[the WorkBuddy guide](../docs/tencent/workbuddy/README.md) to set up the same view.

## No secrets

Nothing in this folder holds a key, a token or a real person's data. All people and companies are synthetic.


## Finals cover (30 Oct)

The new Miora cover and every size are in [06_cover/finals/](06_cover/finals/README.md). For the form use `06_cover/finals/cover_finals_380x216.png`; for YouTube, `youtube_thumbnail_finals_1280.png`.
