# Finals demo video

The new demo video for **Walnut DataBridge** (finals build, branch `final_v1.2`). It replaces the 16 Oct
video (https://youtu.be/fr9wqHWsijY) and follows the same pattern: Ishaan's camera intro, then a
narrated demo of the real app.

## What's here

| File | What it is |
|---|---|
| `walnut_databridge_finals_SILENT.mp4` | The full picture, 6:08 in the test assembly, 1920×1080. Silent except the camera intro (0:00–0:32), which keeps Ishaan's own sound. Record the voice-over against this. |
| `walnut_databridge_finals.mp4` | Appears after step 4 below: the same picture with the voice-over mixed in. **This is the one to upload.** |
| `narration_cues.md` | What to say and when: every line with its start time, scene by scene. |
| `narration.srt` | The same lines as subtitles, timed to the picture, to read along while recording. |
| `timeline.json` | Where each segment starts and ends (written by the assembler). |
| `clips/` | One screen recording per scene (`*.webm`) and its `*.marks.json` (label pills and cut points). |
| `cards/` | The title card, the CodeBuddy card and the app's own proof, business and close slides (PNG). |

The script with every line and what is on screen: [docs/finals/video/VIDEO_SCRIPT_FINALS.md](../../docs/finals/video/VIDEO_SCRIPT_FINALS.md).
Old video vs new, scene by scene: [docs/finals/video/VIDEO_PLAN.md](../../docs/finals/video/VIDEO_PLAN.md).

> **Status (30 Oct):** these clips are **test recordings**. The synthetic world is being expanded and the
> stack restarted; re-record every scene after that (step 1) before recording the voice. Hy3 is not
> switched on, so the script uses the default ("quoted from the sources") lines.

## How Ishaan records and merges the voice-over

### 1. (Only if the app changed) re-record the picture

The stack must be running (`bash databridge/scripts/demo_up.sh start`, http://127.0.0.1:3100). From the
repo root:

```bash
node docs/finals/video/record_finals.cjs                      # every scene + the cards (about 6 minutes)
node docs/finals/video/record_finals.cjs 08_audit 10_engine   # or just some scenes
databridge/.venv/Scripts/python docs/finals/video/assemble_finals.py
```

The assembler prints each scene's start time and length, and rewrites `narration_cues.md` and
`narration.srt` to match the new picture. A failed scene is saved as `<scene>.FAILED.webm` and shows as
a "MISSING" card in the video, never silently.

### 2. Set up

1. A quiet room. **Wear headphones**, so the intro's sound is not picked up by the mic.
2. Use any recorder: a phone voice-memo app held 20–30 cm from your mouth, or Audacity / Voice Recorder
   with a headset mic. Mono or stereo, any format (`.m4a`, `.wav`, `.mp3`, even a phone video).
3. Open `walnut_databridge_finals_SILENT.mp4` in **VLC**, then *Subtitle → Add Subtitle File…* →
   `narration.srt`. Keep `narration_cues.md` open too (or printed) if you want to read ahead.
4. Read `narration_cues.md` once out loud before recording. Relaxed, friendly, like showing a friend
   something you built; about 150 words a minute. Pauses are fine.

### 3. Record

1. Start the recorder.
2. Press play on the video at 0:00. **Stay quiet during the camera intro** (it is already you).
3. When the title card appears (about 0:32), start reading. Read each subtitle as it appears. If you are a
   little ahead or behind, that's fine; keep going.
4. If you fluff a line, pause, say it again, and carry on; or stop and start again from the top.
5. Stop the recorder after "Thanks for watching!". Copy the file to the repo, e.g.
   `submission/finals_video/voice/narration_finals.m4a`.

### 4. Merge

```bash
databridge/.venv/Scripts/python docs/finals/video/assemble_finals.py --voice submission/finals_video/voice/narration_finals.m4a
```

This rebuilds the picture, then lines your recording up so your first word lands on the first cue (it
finds where you start speaking, so the time before you pressed play doesn't matter), removes rumble,
evens out the loudness (-16 LUFS, like YouTube expects) and mixes it in after the camera intro. The
result is `walnut_databridge_finals.mp4`.

Fine-tuning, if needed:

| Problem | Fix |
|---|---|
| The voice is a bit early or late everywhere | `--voice-at 33.2` places the start of your file at exactly 0:33.2 instead of the automatic match |
| A count-in, cough or "okay, go" at the start of the file | `--voice-skip 2.5` drops the first 2.5 s of the file first |
| You re-did one scene's lines in a separate file | Easiest: record the whole thing again; it's about five and a half minutes |
| Hy3 was switched on and the scenes re-shot | Add `--hy3` so the cue sheet uses the Hy3 lines (scene 13) |

Only the cue sheet (no re-render): `databridge/.venv/Scripts/python docs/finals/video/assemble_finals.py --cues-only`.

### 5. Check and upload

Watch `walnut_databridge_finals.mp4` once end to end. Then upload it (YouTube, unlisted) and send the link
with the project link and description.

## Scenes (test assembly, 30 Oct)

> Plan (founder, 30 Oct): the video may be longer than the first. Three cuts from the same takes: short about 6:00, main about 7:55, full about 9:15, with new scenes 17 to 20 (Act, Deliverables, Uploads, Miora) and slots for the teammates' voice, just-in-time UI and dots. See "The new timeline" in `docs/finals/video/VIDEO_PLAN.md`. The table below is what is assembled today.

| Time | Scene |
|---|---|
| 0:00 | 00 Camera intro (Ishaan, reused) |
| 0:31 | 01 Title |
| 0:37 | 02 Same question, two people (Amy vs Rachel) |
| 1:16 | 03 Restricted looks like nothing (Ken) |
| 1:37 | 04 A source, checked live (Rachel), 简体中文 |
| 2:03 | 05 Developer mode: the journey (Maya) |
| 2:37 | 06 The knowledge graph (Jordan vs Ken) |
| (optional) | 06b Watch the brain grow (when the feature lands) |
| 3:00 | 07 Analytics (Maya) |
| 3:18 | 08 Audit, Verify and the tamper demo (Maya) |
| 3:55 | 09 The Trust centre |
| 4:09 | 10 The Walnut engine |
| 4:38 | 11 Built with CodeBuddy |
| 4:51 | 12 Ask from WorkBuddy |
| 5:07 | 13 Tencent Hy3 |
| 5:20 | 14 The proof |
| 5:38 | 15 What it's worth |
| 5:57 | 16 Close (ends 6:08) |

The exact times are in `narration_cues.md`; they shift by a second or two on every re-shoot.

## Honest notes

- Every app scene is the real app, recorded live on synthetic data ("Company A"). Waits are trimmed;
  answers are never edited.
- The WorkBuddy shots (scene 12) are real window captures from 15 Oct, labelled as such.
- Hy3 is wired in but not switched on yet; the video says so (scene 13) and the app labels every answer
  "Quoted from sources (no model)".
- The business figure is an estimate with stated inputs, said as an estimate.
