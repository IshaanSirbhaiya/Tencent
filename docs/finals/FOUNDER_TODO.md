# Ishaan's to-do list (only you can do these)

Tick each one as you go. The why for every demo-day item is in
[demo_day/FOUNDER_CHECKLIST.md](demo_day/FOUNDER_CHECKLIST.md).

## Today, Fri 30 Oct (evening)

- [ ] **Send the organiser email to Cindy before 23:59**: [ORGANISER_UPDATE.md](ORGANISER_UPDATE.md). The draft is ready and links **`final_v1.3`** (on GitHub it is at `1076517`, with the verified numbers: 1,131 backend tests, 166 browser tests, 0 leaks in 7,374). Hy3 is not live, so send the Hy3 line as written. Tonight's docs pass (this file, README branch name, WorkBuddy test) is not on `final_v1.3` yet: if you want judges to see it, say so and I'll commit it and move `final_v1.3` before you send.
- [ ] **Activate Hy3 in TokenHub.** Still 402 today. Console → TokenHub → Online Inference → activate **Hy3** and **DeepSeek V4 Flash** (balance or voucher needed). Then tell me "activated": I run `hy3_check.py`, switch it on and re-run the eval. Until then every answer says "Quoted from sources (no model)". Steps are in [MORNING_BRIEF.md](MORNING_BRIEF.md).
- [ ] **Ask Xin Yi** whether the extra 5,000 credits cover TokenHub (message text is in the chat).
- [ ] **Confirm the cloud decisions D-26 to D-33** in [DECISIONS.md](DECISIONS.md) (taken while you were away; say "OK" or which to undo):
  - D-26: the grow steps only in a tab opened with `/present?grow=1`; the default script is 11 steps (00–10), never grow on the Space path;
  - D-27: `opencv-python-headless` and `XlsxWriter` pinned in the lock file;
  - D-28: a model-written Act draft sentence must take 80% of its content words from its own sources;
  - D-29: the Summary deliverable is not built; Table, Chart and Slides (.pptx) are;
  - D-30: the compliance WorkBuddy connector never goes in the shared WorkBuddy config (its own workspace, or `--compliance-user-config` on its own seat);
  - D-31: Act's rate-limit and proposal state stays in memory (lost on restart);
  - D-32: the 简体中文 `L` flip is cut from the 5:00;
  - D-33: once `P` has started, `Esc` never hides the HUD; `H` does.
- [x] **Upload folder picker**: built and tested on 30 Oct (D-34): the sheet lists, by name, only the folders the person can write to.
- [x] **New cover in Miora** (done 30 Oct: option 2 chosen, every size in `submission/06_cover/finals/`): paste [COVER_MIORA_PROMPT.md](COVER_MIORA_PROMPT.md) and send me the best image.

## After the teammate merge

- [ ] **Remind Claude:** after the just-in-time UI merge, extend the **User-mode tour** with its steps (noted in [MERGE_PLAN.md](MERGE_PLAN.md)).

## Sat 31 Oct

- [ ] **Hallway test**: 5 people × 20 min ([rubric/hcd_ux/HALLWAY_TEST_PROTOCOL.md](rubric/hcd_ux/HALLWAY_TEST_PROTOCOL.md)). Send me the notes; I'll write the report, the fix log and the tester line on the proof slide. No test, no line: the `[TESTER]` sentence is deleted from the run sheet (never invented).
- [ ] **Hallway kit** (script, task cards, notes form, results template): [rubric/hcd_ux/hallway_kit/](rubric/hcd_ux/hallway_kit/FACILITATOR_SCRIPT.md). Print TASK_CARDS and one NOTES_FORM per tester; do the 5-minute dry run in the script's setup first.
- [x] **Laptop checks** (done 30 Oct evening): the cloud work was pulled; the Windows suites passed (1,131 backend); `demo_day.ps1` was run for real (a port bug was found and fixed); `--fresh` reset passed; `B` works at every step; the absence proof was refreshed (12/12); the Codex review of the cloud work was run.
- [ ] **Video: ON HOLD until teammates' updates land** (your call). When you're ready, tell me; I re-record the scenes (about 15 min), then you record the narration against `submission/finals_video/narration_cues.md` and I merge it, exactly like last time.
- [ ] **Decide the stage roles** (default: Ishaan speaks, Dev drives, Mehul is the compliance voice, Aaruni is the Tencent voice) and rehearse twice with [rubric/demo_story/RUN_SHEET.md](rubric/demo_story/RUN_SHEET.md).
- [ ] **Allow me screen control once** for the WorkBuddy GUI test (I'll ask first). The steps are in [WORKBUDDY_GUI_TEST.md](rubric/ai_innovation/WORKBUDDY_GUI_TEST.md): prompts 1–4 use the two employee connectors; prompts 5–6 run from the `IB compliance` workspace folder, because the compliance connector is no longer in the shared WorkBuddy list (D-30).

## Before Mon 2 Nov (rehearsal at 16:00)

- [ ] Double-click **`Make Desktop Shortcuts.cmd`** once (in `E:\Tenecent Hack_Walnut\Tencent\`).
- [ ] Run **Start Walnut Demo** once, then **Start Walnut Demo (offline)** once (so the fallback is built), then **Stop**.
- [ ] Decide a **demo passcode** (set `IB_DEMO_PASSCODE` before Start); it stops anyone on the venue network signing in.
- [ ] Print the handouts in `submission/finals/` (leave-behind, judge cards, presenter cheat-sheet).

## Night before Demo Day (Mon 2 Nov, ~10 min)

- [ ] Do not disturb / Focus on, including while presenting or duplicating the display.
- [ ] App notifications off.
- [ ] Windows Update paused until after 3 Nov.
- [ ] Phone hotspot set as a metered connection.
- [ ] Power: plugged in, never sleep; closing the lid does nothing.
- [ ] Display scale 100% at 1920×1080.
- [ ] Time zone UTC+8 (Singapore).
- [ ] Laptop and phone charged; HDMI/USB-C adapter packed.

## Morning of Demo Day, Tue 3 Nov (before leaving)

- [ ] After the last full rehearsal, double-click **Reset Walnut Demo** with `--fresh` (about 3 min). This keeps the audit log small, so step 01 stays at about 1.5 s; a big log slows every write. Then do one short check only. Close any graph or Grow tabs while presenting Compare. See [demo_day/STEP01_PERF_DIAGNOSIS.md](demo_day/STEP01_PERF_DIAGNOSIS.md).

## At the venue, Tue 3 Nov

- [ ] Plug in the projector, Win+P → **Duplicate**.
- [ ] Double-click **Start Walnut Demo**; wait for **READY**.
- [ ] Preflight board: everything green, **Screen 1920×1080**, **100%**. Read the **Model** line: if it isn't "Hy3 live", use the default lines, not the "ONLY IF HY3 LIVE" ones.
- [ ] Press **F11** if the browser isn't full screen.
- [ ] Check the title card is readable from the back of the room.
