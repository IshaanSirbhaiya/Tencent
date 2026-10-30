# Morning brief for Ishaan (Fri 30 Oct)

Branch `final_v1.0`, everything committed and pushed. **The finals build is complete**: every page is
built, tested live against the real backend, reviewed by five models and fixed.

## Things only you can do

Demo-day laptop steps, with the reason for each: [demo_day/FOUNDER_CHECKLIST.md](demo_day/FOUNDER_CHECKLIST.md).
 (in this order)

| # | What | Why | Time |
|---|---|---|---|
| 1 | **Activate Hy3 in TokenHub** (steps below), then tell me "activated" | Every model currently answers 402 (code 401006, "service ID does not exist"). Live Tencent AI is worth points on AI Interaction, Innovation and Overall. I'll switch it on (`IB_FORCE_OFFLINE=0`), test it live, re-run the eval with Hy3 and update every "live pending" line | 10 min |
| 2 | **Send the organiser email to Cindy before 23:59 tonight** | It's ready: [ORGANISER_UPDATE.md](ORGANISER_UPDATE.md). If Hy3 is live by then I'll update its one Hy3 line first | 5 min |
| 3 | **Try the app yourself**: `bash databridge/scripts/demo_up.sh start` (it's already running on this laptop at http://127.0.0.1:3100). For the demo path: http://127.0.0.1:3100/present?present=1, then press **P**, then **Space** for each step | So you know the flow before rehearsal | 15 min |
| 4 | **Hallway test, Saturday**: 5 people × 20 min | The only Human-Centered Design evidence we can't produce ourselves. Protocol: [rubric/hcd_ux/HALLWAY_TEST_PROTOCOL.md](rubric/hcd_ux/HALLWAY_TEST_PROTOCOL.md). Put the real results in FIX_LOG.md and `databridge/apps/internal_brain_web/public/finals/fixlog.json`, and the proof slide shows the tester line | 2 h |
| 5 | **Decide the stage roles.** The default is Ishaan speaking, Dev driving, Mehul as the compliance voice, Aaruni as the Tencent voice. Script: [rubric/demo_story/RUN_SHEET.md](rubric/demo_story/RUN_SHEET.md) | Rehearse twice before Monday | — |
| 6 | **Set a demo passcode on the laptop** (`IB_DEMO_PASSCODE=...` before `demo_up.sh start`) | Security review: stops anyone on the venue network from signing in | 1 min |
| 8 | **New cover image in Miora**: paste the prompt in [COVER_MIORA_PROMPT.md](COVER_MIORA_PROMPT.md), then send me the best image. I'll make every size and swap it in | The cover and YouTube thumbnail should show the finals name and tagline | 10 min |
| 9 | **Record the narration for the new video**: script and cues in `docs/finals/video/` and `submission/finals_video/` (being produced now); record like last time and send me the audio | The judges get the video; the new UI should be in it | 30 min |
| 7 | Ask Xin Yi about the extra 5,000 credits, and whether they cover TokenHub | Only if step 1 needs a balance | 2 min |

### Activating Hy3 on TokenHub (international console)

1. Open the Tencent Cloud console (international) → **TokenHub** (Model as a Service).
2. Find **Online Inference** / **Model services** (the error text says "Online Inference Service list").
3. **Activate** or create a service for **Hy3**, and also **DeepSeek V4 Flash** as the backup. Accept the pay-as-you-go terms if asked; a balance or voucher is needed.
4. If the console shows a **service ID**, tell me; some accounts need it instead of the model id `hy3`.
5. Tell me "activated".

## What exists now

As of commit `f0f84bd`. Every feature with its tests and screenshots: [FEATURE_CATALOGUE.md](FEATURE_CATALOGUE.md);
every commit since the submission: [CHANGELOG.md](CHANGELOG.md).

| Area | State |
|---|---|
| **User mode** | Persona sign-in; Ask with real stage-by-stage progress and **voice dictation** (English / 简体中文, never sends by itself); cited answers with sources checked live; honest refusals; history; settings (text size, contrast, motion, theme); English / 简体中文 |
| **Compare** | Two people, same question; one person, two questions; "Identical responses" marker |
| **Presenter mode** | The 10-step demo script on the keyboard, Stage theme for the projector, proof / business / close slides with a QR code. **Backup clips A, B, C** play on `B`, labelled "Recording · not live" (interim; clip C is an in-app stand-in for WorkBuddy) |
| **Developer mode** | Mission control (live pipeline); request journey (compliance waterfall + employee receipt); permission-aware **knowledge graph** (two people side by side); **Walnut engine** (graph, time slider, provenance, ontology; Jordan only); **analytics**; **audit** (plain-English search, Verify chain, **sandbox tamper demo**); simulator; **Trust centre**; value calculator |
| **Tours** | User "Try it" (45 s) and Developer tours, with "Do it for me" |
| **Hy3** | Wired, not live (401006). `scripts/hy3_check.py` checks TokenHub; `demo_up.sh` turns Hy3 on by itself as soon as it answers |
| **Backend** | **754 tests pass**; full eval re-run on the final code: **0 leaks / 5,958**, **0 / 400** differential, 0 injection failures |
| **Web e2e** | **124 passed, 0 failed**, 38 environment-gated skips, three runs in a row ([TEST_REPORT.md](TEST_REPORT.md)); `voice.spec.ts` added since |
| **Security** | Five-model final review: no high or critical issue left; all findings fixed (Host allowlist against DNS rebinding, held gate events in the stream, stale graph edges, engine view only for its own persona, and more) |
| **Evidence** | Every one of the 10 rubric criteria has deliverables: [rubric/README.md](rubric/README.md). Printable handouts (leave-behind, judge cards, presenter cheat-sheet) in `submission/finals/` |
| **Docs** | README quick start, INTERNAL_BRAIN 10-minute walk-through, STATUS, feature catalogue, changelog, ARCHITECTURE §8, SECURITY §15 |
| **Being built now** | Act (draft a reply / follow-up ticket), live graph growth, the one-click demo kit, CodeBuddy CB7–CB9 and new WorkBuddy tools, the new demo video: [CHANGELOG.md → In progress](CHANGELOG.md#in-progress) |

## Still to do (me)

- Hy3 switch-on and eval re-run, as soon as you activate it.
- Re-record the three backup clips after the Monday freeze (interim ones are in place), and the real WorkBuddy clip C on the stage laptop.
- Update the docs and the slide data with real hallway-test results.

## Decisions taken for you (change any)

All 23 decisions and the reasons for them are in [DECISIONS.md](DECISIONS.md).
- **Demo story:** opens on Aspire's own question, why Velquarry's account is restricted, with Amy and Rachel side by side. Then Ken: real vs made-up, identical. Then a source checked live, the journey, a tamper on a copy, Tencent through WorkBuddy, proof, business, close. Tagline: **"Answers you can prove."**
