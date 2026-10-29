# Backup plan: the five-rung ladder

Use the lowest rung that works. Dev decides when to step down; Ishaan announces it in one clause. A recording is never presented as live: say it is a recording.

## The ladder

| Rung | What | Trigger | Switch time | What Ishaan says |
|---|---|---|---|---|
| 1 | Live, normal | Default | n/a | Nothing |
| 2 | Live with the extractive answer | The model call is slow or fails (4 second timeout). Automatic; it is the product's own behaviour | 0 s | Nothing. The model label shows honestly |
| 3 | Local clip for the current step (`B`) | A page errors, a step stalls, or the network fails | under 3 s | "Let me show you the recording from this morning; same system, same data." |
| 4 | Full 5:00 video on laptop B, paused at 0:00 | The whole app is dead | 10 to 20 s | "The live system has failed; here is the recording of the same run." Narrate over it |
| 5 | Stills and spoken explanation | Both laptops fail | 10 s | "Here is what you would have seen." Walk through the stills |

Laptop swap (cable move to laptop B) takes 10 to 20 s; rehearse it.

## The three fallback clips

Folder: `Tencent/submission/finals/clips/`. Record on 2 Nov **after the freeze**, on the real build, with the Stage theme, 1080p, 30 fps, cursor highlighted. Re-record if any UI changes later. H.264, under 200 MB each, no audio (muted). Copy to both laptops, a USB stick and a phone. Test in the player used on stage.

| Clip | File name | Covers | Run sheet time | Step keys |
|---|---|---|---|---|
| A | `clipA_compare_amy_rachel_ken.mp4` | Amy and Rachel on Velquarry, then Ken's identical refusals, then the sources sheet [2] | 0:00 to 1:40 | steps 0 to 3b |
| B | `clipB_journey_audit_tamper.mp4` | Maya's journey for Rachel's request, Verify green, sandbox tamper, Verify red at the entry | 1:40 to 2:35 | steps 4 to 6b |
| C | `clipC_workbuddy_jordan.mp4` | Jordan asks in WorkBuddy, the cited answer, then the new row in `/dev/audit` with channel `workbuddy-mcp` | 2:35 to 3:20 | steps 7 to 8 |

Also record (not one of the three clips):

| File name | What |
|---|---|
| `full_5min_run.mp4` | The whole pitch's screen sequence, used for rung 4 |
| `still_01_compare.png` to `still_06_audit_broken.png` | Stills for rung 5 |

The clips must be a real recording of the real system on the real seed. Do not edit results. Label "recording" in the overlay corner.

## Failure table

| Risk | Mitigation |
|---|---|
| Venue Wi-Fi fails | We do not rely on it. Everything runs locally except the model and WorkBuddy calls, via a phone hotspot |
| TokenHub slow or rate-limited | 4 second timeout, then the extractive answer; pre-warm |
| WorkBuddy fails | Clip C; cut order in `RUN_SHEET.md` §4 puts the audit-row cut first among the WorkBuddy items |
| Browser or app error | `B`; a second browser window is pre-opened at each step |
| Laptop or display failure | Laptop B, identical build, adapters |
| Projector aspect problem | Design is 1280 x 720 safe; tested at rehearsal |
| Notification or sound | Do Not Disturb; airplane mode except the hotspot |
| Dead battery | Mains plugged in; battery above 90%; extension lead |
| Lost voice or blank | Script on a phone face-up; Ishaan bridges with the next line's first four words |
| Real-chain damage by the tamper demo | Not possible by design: the tamper runs on a copy (D-19). `R` restores the sandbox |

## Decision rule

If a step has not shown its result 8 seconds after the key press, Dev presses `B`. Do not wait longer; dead air costs more than a labelled clip.
