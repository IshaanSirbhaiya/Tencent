# Finals video, plan v4 (supersedes v3): all team inputs merged

**30 Oct 2026, ~21:45 SGT.** Inputs: Aaruni's intro, the slides recording, Dev's Atlas film, and the
teammates' "The Call" screen and speech docs. **Target ≈ 6:30–7:30**, under 8:00. The core of the 16 Oct video
is kept (who sees what, cited answers, the audit trail, Tencent tools, the proof, the close).

## 0. Running order

| # | Part | Length | Voice | Source |
|---|---|---|---|---|
| 1 | **Intro**: "Hi, we're Team Walnut…" | 0:10 | Aaruni (on camera) | ✅ `IMG_3816.MOV` (9.5 s) |
| 1b | **AI-generated film (Higgsfield): the problem.** Company knowledge scattered across tools, teams and people; asking around and waiting; why AI can't simply be switched on | ~1:00 (TBC) | as produced | ⏳ coming from the team |
| 2 | **Feature slides**: access and evidence, just-in-time UI, the audit trail → "…someone on their first day leading a project." | 1:20 | Aaruni (recorded in file) | ✅ `Screen Recording 2026-10-30 210819.mp4` (79.8 s) |
| 3 | **Workflow 1, "Day one on Atlas"**: Alex asks once and a cited onboarding page opens | ~1:30 | Speaker B (as Alex) | **Dev's film** (`…9.14.45 PM.mp4`, 89 s), used as recorded (founder decision, option A: see §1) |
| 4 | **Hinge card** (3 s): "It found the project context. Can it keep a secret?" | 0:04 | caption only | title card |
| 5 | **Workflow 2, "The Call"**: Amy gets only the script; Rachel gets the case page | ~1:15 | **Aaruni** (the team's speech doc) | **recorded by the team** (their screen doc); provider-label blur check in the edit |
| 6 | **The company brain**: the map (Rachel vs Ken), identical refusals, the journey, Verify and tamper, the platform, **live WorkBuddy**, the proof | ~2:00 | Speaker B; Aaruni for the Tencent line | recorded on our build; **WorkBuddy recorded live on the laptop** |
| 7 | **Close**: "Answers you can prove." | 0:15 | Aaruni | title card |

Total ≈ 6:35 + the AI film (~1:00) ≈ **7:35**. If the film runs longer than 1:15, trim the brain section first (platform screens, then the map), never The Call or the tamper.

## 1. Atlas: Dev's film, used as recorded (founder decision, option A)

- **Blur the provider label** under "Answer" (the line naming the model, roughly 0:25–0:55), wherever it
  is on screen; check every frame of the final cut.
- **Cut the page-loading wait** (around 1:15, "Checking your sources again…"): jump to the loaded page or
  hold on the chat.
- **The company stays "Company A"** (the founder cancelled the Pandan Ledger rename). Caption once at the
  start of the workflows: "Company A · a fictional Singapore fintech · every record is synthetic". The
  voice-over never names the company.
- The Call and the brain scenes are recorded on our build, with neutral model labels.

## 2. What the team's "The Call" docs change (they win over v3)

- The narrator is **Aaruni**, with her speech text (~70–75 s). The screen follows their 11 segments.
- Kept from our fixes: the badge is now correct, Amy's script is quoted exactly (no model variance), and
  the page may open by itself. **We still click "Open as page"** as their doc says (reliable on camera).
- Truth edits to their speech (minimal):
  - [7] "Customer handled in under a minute" → **"Amy has her answer in seconds, and nothing is given
    away."** The minute is unmeasured; the seconds are measured.
  - [6] "with every line linked to its source" → **"with its sources linked"** (2 source cards, not one per
    line).
  - Everything else stays word for word.

## 3. Live WorkBuddy on the laptop (founder approved)

- Record **Tencent WorkBuddy** live, as in the first video: Jordan (on call) asks the migration question in
  WorkBuddy through our MCP connector, gets the cited answer, then the new audit row with channel
  `workbuddy-mcp`. This replaces the in-app stand-in clip C.
- Needs: WorkBuddy open on the laptop, the connector config from `WORKBUDDY_GUI_TEST.md`, and screen
  control (approved). Recorded with Windows screen capture of the WorkBuddy window only (notifications off,
  Do Not Disturb on).

## 4. Recording rules (unchanged)

- Only after the Story 1 fixes and the Atlas branch are merged and green.
- Every take is checked automatically; a wrong state is refused.
- Model ON with neutral labels. Never "Hy3 live" unless TokenHub is live. No provider name anywhere.
- No run outputs in git. Media and takes live in `submission/finals_video/v3/` (git-ignored).
- Light mode, 1440-wide, one consistent look. Waits are cut, keeping ~1 s of "thinking".

## 5. Speakers and scripts

- **Aaruni:** the intro (done), the slides (done), **The Call** (team speech, with the edits above), the
  Tencent line and the close.
- **Speaker B (Mehul or Dev):** Atlas (as Alex) and the company brain.
- The read-aloud text for both is in `NARRATION_SCRIPT_V5.md`.

## 6. Production order (laptop)

1. Merge feat/story1-fixes + feat/atlas-workflow + feat/pandan-ledger + feat/video-v3 → all tests and the
   leak eval green.
2. Start the stack with the Atlas world, the model ON, a fresh reset; dry-run every scene (automatic checks).
3. Record: Atlas → The Call → the brain scenes (headless browser), then live WorkBuddy (screen capture).
4. Speakers record their blocks (placeholder TTS cut first, for timing).
5. Assemble: intro → slides → Atlas → hinge → The Call → the brain → close; captions and .srt; loudness
   −16 LUFS; 1080p30.
6. Review: storytelling, claims, and a Codex "make it win" pass. Founder approves, then upload.
