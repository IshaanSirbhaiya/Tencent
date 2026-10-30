# Rehearsal checklist: Monday 2 Nov, 16:00 to 17:00 (mandatory)

Order matters: freeze the build, record the clips, then rehearse. Tick every box with a name and a time.

## Before the rehearsal (Sun 1 Nov evening to Mon 2 Nov 15:30)

- [ ] Build frozen (freeze is Mon 12:00); all tests green; evals re-run; proof numbers copied from the latest `RESULTS.md` [NUM]
- [ ] Presenter mode passes its acceptance test on laptop A and laptop B (`PRESENTER_KEYS.md` §5)
- [ ] Run sheet truth checks done: [HY3], [TESTER], [NUM], [CB], [BIZ] (`RUN_SHEET.md` §1). Edit the lines now, not on stage
- [ ] Usability report and fix log filled, or the tester line removed
- [ ] Clips A, B and C recorded on the frozen build, plus the full 5:00 video and stills (`BACKUP_PLAN.md`)
- [ ] Clips copied to both laptops, a USB stick and a phone
- [ ] WorkBuddy open, warm, Jordan's connector trusted, one dry run done on the hotspot
- [ ] Leave-behind printed (50) and the QR scanned from 3 metres; QR printed on a small stand
- [ ] Repo public and the QR points at `https://github.com/IshaanSirbhaiya/Tencent`
- [ ] Do Not Disturb on, updates off, notifications off on both laptops

## At the venue

- [ ] Draw the lot or ask the organisers: our position in the order. Note who presents before us
- [ ] Choose the opener: default, or "after the other FinTech team" (`OPENERS.md`)
- [ ] Walk from the real entry to the real spot; time the changeover (target under 30 s to the first word)
- [ ] Projector: resolution, aspect ratio, text readable from the back row, the Stage theme in the real light; decide light or dark on the real projector
- [ ] Network test at the stage; hotspot latency to TokenHub; run the whole in-app path offline once
- [ ] Agree the timekeeper's signal and who watches it (Aaruni)

## Timed runs

- [ ] Run 1 full: stopwatch. Pass is 4:40 to 5:00
- [ ] Run 2 full
- [ ] Run 3 full. No run over 5:00. Record times: TO FILL, TO FILL, TO FILL
- [ ] Forced-failure run: Dev kills the network at the WorkBuddy beat and presses `B`; check the recovery line
- [ ] Laptop swap run: change to laptop B in under a minute and resume
- [ ] Q&A drill: a teammate asks 10 questions from `QA_BANK.md`; stop at 1:00
- [ ] Walk-up `D`: after a full run press `D` then `R` once (confirm). `D` opens Leo's panel; `Esc` closes it and signs the previous person back in (`←` to the journey shows the waterfall); `R` rebuilds the `D` cache
- [ ] Walk-up grow: reset (`R`, or **Reset Walnut Demo**) and run `python docs/finals/live_kit/check_absent.py --all` first (must end `RESULT: all checks passed`); then open the `?grow=1` tab (`/present?present=1&grow=1`) after a sign-in exists, before Q&A. The HUD must read `/ 13`. Drive it with `P` and `Space` only (`QA_BANK.md`, walk-ups)
- [ ] The Esc rule, said aloud once: `Esc` only when a sheet is visible (it closes that sheet). Once `P` has started the script, `Esc` never hides the bar; `H` hides it on purpose and `Esc` or `H` brings it back
- [ ] Record one run on a phone; watch it; note back-turns, filler words and rushed lines

## Agree and write down

- [ ] Who speaks which line (`RUN_SHEET.md` §0); hand-off phrases by name
- [ ] Who presses what; that Dev says each key aloud
- [ ] Fallback switch decisions: Dev decides, Ishaan announces
- [ ] A judge interrupts: Ishaan answers in one sentence, then "let me finish this step, then I will come back"
- [ ] No new tools or UI changes after the rehearsal

## Tue 3 Nov morning

- [ ] Charge everything; battery above 90%
- [ ] Pre-warm: press `P`, run the path once, check the three status dots (API, model, ledger)
- [ ] Re-check every [V] number against the latest repo output; delete anything untraceable
- [ ] Final playback test of clips A, B, C and the full video
- [ ] Read the script aloud once, then stop practising
- [ ] Finalists arrive from 15:35; confirm the time with the organisers
