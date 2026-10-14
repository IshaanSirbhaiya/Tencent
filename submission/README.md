# Submission pack

Everything to attach to the Tencent Cloud AI CAN DO IT submission form, in one place. The form
answers themselves are in [docs/tencent/SUBMISSION.md](../docs/tencent/SUBMISSION.md).

| Folder | What's in it | Use it for |
|---|---|---|
| `01_codebuddy/` | One overview and one full-page screenshot per CodeBuddy session (CB1–CB6), rendered from the saved CLI transcripts (`docs/tencent/evidence/`) | The mandatory CodeBuddy proof (attach at least 3 overviews: CB2, CB5, CB6) |
| `02_workbuddy/` | Tencent WorkBuddy with the three Internal Brain connectors: `workbuddy_mcp_panel.png` (the connectors, all enabled), `workbuddy_compare.png` (one question through Jordan's and Ken's connectors, two different answers), `workbuddy_compliance.png` (Maya's connector lists Jordan's access and verifies the audit chain); `raw/` holds the uncomposed window shots | The mandatory WorkBuddy proof |
| `03_app_scenes/internal_brain/` | The Internal Brain web app: answer with checks, map, compare, tour, info pop-ups | Extra screenshots, slides |
| `03_app_scenes/walnut_original/` | The original Walnut screens on Company A: knowledge graph, node provenance, brain time slider, ontology, provenance chain, guided tour | Extra screenshots, slides |
| `04_evidence/` | Real outputs: the tamper demo run | Proof the audit log catches edits |
| `05_video/cards/` | The video's still cards (title, why, architecture, trust boundaries, tamper, numbers, closing) | The video, slides |
| `05_video/clips/` | One screen recording per video scene, plus `<scene>.marks.json` (on-screen labels and the loading waits the edit trims) | The video |
| `05_video/narration_cues.md`, `narration.srt` | What to say and when, generated from the finished video's timeline (`timeline.json`) | Recording the voice-over |
| `05_video/walnut_internal_brain_demo.mp4` | The assembled demo video (about 5:50), silent, timed to the narration script | Record the voice-over on top, then upload |
| `06_cover/` | The cover image, made in Miora (to be added by Ishaan after the freeze) | The form's cover field |
| `07_deck/` | The pitch deck: `walnut_internal_brain_deck.pdf` and `.pptx` (13 slides, speaker notes in the .pptx) | Attach to the submission; present at Demo Day |

Re-create any of it:
- CodeBuddy pages: `python docs/tencent/evidence/render_transcripts.py`, then `node docs/tencent/evidence/shoot_sessions.cjs`
- Video cards: `python docs/tencent/video/make_cards.py`, then `node docs/tencent/video/shoot_cards.cjs`
- Video clips (stack running): `node docs/tencent/video/record_scenes.cjs [scene ...]`
- Video: `python docs/tencent/video/assemble.py [--voice narration.m4a]`
- Narration cue sheet + subtitles: `python docs/tencent/video/narration.py`
- Deck: `python docs/tencent/deck/make_deck.py html`, `node docs/tencent/deck/shoot_deck.cjs`, `python docs/tencent/deck/make_deck.py pack`

Nothing in this folder contains a key, token or real person's data.
