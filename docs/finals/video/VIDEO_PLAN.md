# Finals video plan: the first video, side by side with the new one

## Shot list (a two-minute read)

**The video in one line:** the 16 Oct video's spine, beat for beat and in its order, re-shot on the new UI, with
every new feature attached to the beat it strengthens ("and now it also..."), never as a block of its own
(founder's principle, 30 Oct evening). Main cut 7:58 (under the 8:00 limit of the 5 to 8 minute range), short cut
6:31, full cut 9:52. The first video was 6:28.

1. **Camera intro (0:32).** Ishaan, the same file as before. Stay quiet. [16 Oct beat 1]
2. **Title (9 s).** "Walnut DataBridge. Answers you can prove." Spoken: built with Tencent CodeBuddy, wired for Tencent's
   Hunyuan (true per `DEV_PROCESS.md` and the model chain; Hy3 is not live). Tencent is named 38 s in, as on 16 Oct. [beat 1]
3. **One question, every sentence sourced (34 s).** Rachel asks Aspire's question; every sentence opens to its exact
   passage, checked live seconds ago; and now the user screens work in 简体中文. [beat 3]
4. **The map (20 s).** A map of what Jordan can open, built from the same permission checks (a view, not how answers
   are found); Ken's corner beside it, no greyed-out boxes: for Ken the rest isn't there. [beat 4]
5. **Same question, two people (38 s).** Amy and Rachel, side by side: the approved script vs the case; tipping-off is a
   crime, so the system never hints. [beats 5 and 7]
6. **Restricted looks like nothing (25 s).** Ken, a customer in the demo data vs a made-up one: identical; 400 pairs, zero
   differences. [beat 6]
7. **And now it also... (49 s in the main cut).** Act: Amy's answer becomes the reply draft, never sent; Jordan's a ticket
   with confirm (22 s) · Deliverables: table, chart, .pptx (15 s) · new here: tour, dictation, accessibility caption, value
   calculator (12 s). [enhancements to beats 7, 3 and 5b]
8. **Nothing stale (32 s).** Live growth: Jordan posts, it is in his graph at once, Ken sees nothing (22 s) · add a file
   (10 s). [beat 8]
9. **The compliance side (1:44).** Maya: Rachel's request as a waterfall, three sealed records, every verdict (35 s) · the
   audit log in plain English, Verify, the tamper demo on a copy (43 s) · analytics from the verified log (12 s) · Trust
   centre, seven controls, each proved by a test (14 s). [beat 9]
10. **WorkBuddy (16 s), what's underneath (26 s), how it fits together and Hy3 (15 s).** WorkBuddy stills (it uses our MCP server,
    same gates, same sealed log; re-capture on the laptop); the original Walnut screens as "the platform" (adapter agents,
    builder, connectors, data tools, the federated engine on the gateway), then the engine graph inside DataBridge, shown,
    not used to answer; the model chain, Hy3 wired but not live, said plainly, and the Tencent Cloud deploy kit card.
    [beats 10 to 12]
11. **Teammates' features (placeholders, 22 s in the main cut).** Voice, just-in-time UI, dots: added after the merge;
    left out until their clips exist.
12. **The numbers, how we built it, thanks (1:14).** 7,374 requests with 0 leaks, 400 pairs with 0 differences, 1,278
    backend tests and 185 browser tests, none failing (20 s) · about US$1.3M a year, an estimate, break-even about 7 minutes a
    week, Tencent Cloud as the target host (21 s) · built with CodeBuddy, nine recorded sessions (11 s) · the cover, designed in Tencent Miora (5 s) · "Answers
    you can prove", one pilot team, thanks (13 s). [beats 13 and 14]

**The short cut (6:31)** drops live growth, add a file, the new-here beat, Miora and the slots, and uses only the
base spoken lines. **The full cut (9:52)** gives every scene its longest slot and adds the **[FULL]** lines.

**Recording order** is unchanged (it is about stack state, not the edit): `record_finals.cjs cards`, then 02, 03, 04,
05, 06, 07, 08, 09, 10, 13, then the scenes that write to the stack (06b, 17, 18, 19), then 21, then the teammates'
slots. Details in the checklist below.

## Pre-record checklist (tonight, after 18:30 SGT)

Tick top to bottom. Nothing here is automatic: the recorder is headless (it never opens a window or plays a
sound), but it uses the same stack the team is using, so it needs the founder's go-ahead.

- [ ] **Tell the founder before the stack is taken over.** The recorder logs in as five personas (amy.am, mlro.tan, ken.ext, maya.compliance, jdoe) and asks real
      questions on the shared stack (3100 / 8100 / 8200 / 8300), which adds rows to the real audit ledger. Say
      when it starts and roughly when it ends (about 8 to 10 minutes for all scenes), and that nobody should
      reset, restart or sign people out in that time.
- [ ] **Stack reset to a known state:** double-click `Reset Walnut Demo.cmd`, or
      `powershell -File databridge\scripts\demo_day.ps1 reset -NoBrowser` (no `-Fresh`: that wipes and reseeds
      the history, about 3 minutes, and is only for a broken stack). Then
      `powershell -File databridge\scripts\demo_day.ps1 status`: every line green. The reset never edits the
      real ledger.
- [ ] **Hy3 status known:** open `http://127.0.0.1:3100/api/ib/ib/status` once, or just run scene 13 with
      `FINALS_HY3=0` (not live) so the recorder refuses the take if the stack says otherwise. If Hy3 went live,
      use `FINALS_HY3=1`, re-shoot 02, 04, 05 and 13, and run the assembler with `--hy3`.
- [ ] **Truth check:** `python docs/finals/video/assemble_finals.py --self-test` passes (it compares every
      number the script says with `eval_summary.json`: 7,374 requests / 0 leaks; 400 pairs / 0 differences;
      1,152 injection attempts / 0 hard failures; 1,278 backend and 185 browser tests, none failing; nine CodeBuddy sessions).
- [ ] **Display** at 1920×1080, 100% scaling (Windows Settings → Display). The recorder is headless at
      1536×864 and does not depend on the screen, but ffmpeg, the playback check and the voice-over session
      do. Close other windows.
- [ ] **Do Not Disturb on** (Windows Focus / Do not disturb), phone silent, and the recorder machine plugged in
      (low CPU matters: a busy laptop makes the clips stutter).
- [ ] **Voice-over room:** quiet, headphones on, mic tested (README in `submission/finals_video/`, section 2).

**Which scenes, in which order.** Order matters: scene 02 creates Rachel's request that scene 05 clicks, and
02 to 05 give scenes 07 and 08 something to show. Scenes that write to the stack come last.

1. Cards first: `node docs/finals/video/record_finals.cjs cards` (title, the CodeBuddy card that must say "Nine
   CodeBuddy sessions", the Miora card, and the app's proof, business and close slides).
2. Core and read-only scenes: `FINALS_HY3=0 node docs/finals/video/record_finals.cjs 02_compare_amy_rachel
   03_ken_identical 04_source_live 05_journey 06_graph 07_analytics 08_audit 09_trust 10_engine 13_hy3_status`
3. Scenes that write to the stack (mock Jira ticket, mock Slack note, mock Drive file): `node
   docs/finals/video/record_finals.cjs 17_act 18_deliverables 06b_brain_grow 19_upload`, then `21_extras`. Reset
   afterwards (step 1 of this checklist) and tell the founder.
4. The teammates' slots, once merged: add their `SCENES` entries (`S1_voice`, `S2_jit_ui`, `S3_dots`), then record them.
5. Assemble: `databridge\.venv\Scripts\python docs/finals/video/assemble_finals.py` (main), then `--cut short` and
   `--cut full`; play each SILENT file once end to end. Then Ishaan's voice-over from the matching
   `narration_cues*.md`, then `--voice` with the same `--cut`.

**Expected length.** The planned cuts (cue sheet from the planned slot lengths, `--cues-only --plan`) are short 6:31, main 7:58 and full 9:52, against **6:28** for the first video.
The founder's direction (30 Oct) is that the new video may be **longer** than the first, because there is more to
show: see "The new timeline" below (short cut about 6:30, main cut about 8:00, full cut about 9:50). The assembler
prints each scene's real length; if a clip runs well over its slot, re-shoot that scene.

---

The founder's direction (30 Oct): keep the first video's pattern exactly (they liked it), just better and
on the latest UI. So the new video keeps the same shape: **Ishaan's camera intro first, then a narrated
screen demo** made with the same toolchain (Playwright scenes at 1536×864 scaled to 1080p, the drawn
cursor with eased movement, typing jitter, cut marks on slow waits, label pills, slowly drifting still
cards, 0.35 s cross-fades, `timeline.json`, a cue sheet and `.srt`, and a `--voice` merge with loudness
normalisation). The pacing rules are the same table ("Pacing" in
[VIDEO_SCRIPT_FINALS.md](VIDEO_SCRIPT_FINALS.md)).

| | First video (16 Oct) | New video (finals) |
|---|---|---|
| Link | https://youtu.be/fr9wqHWsijY | `submission/finals_video/walnut_databridge_finals.mp4` (after the voice-over) |
| Length | 6:28 (intro 0:32 + demo 5:56) | Planned: short 6:31, main 7:58, full 9:52 (`assemble_finals.py --self-test`); nothing is recorded yet |
| Name on screen | Walnut Internal Brain | Walnut DataBridge ("Internal Brain" is the ask product inside it) |
| Opening story | Jordan asks about the DB migration | Aspire's own question: why is Velquarry's account restricted? Amy vs Rachel |
| Tagline | none | "Answers you can prove." |
| Scripts | `docs/tencent/video/record_scenes.cjs`, `assemble.py`, `narration.py` | `docs/finals/video/record_finals.cjs`, `assemble_finals.py` (cue sheet built in) |

## The new timeline: core story first, then everything new

<!-- generated from SEGMENTS in assemble_finals.py (31 Oct); if you change a length there, change it here -->

Founder's direction (30 Oct): the video may be longer than the first, because there is more to show. Show
everything, give the most time and emphasis to what matters most, keep each secondary feature short (10 to 25 s),
same pattern as the first video (camera intro, then a narrated screen demo; same toolchain). Three cuts are built
from the **same takes** with `assemble_finals.py --cut short|main|full` (main is the default); a scene is in a cut
or not, and a clip is stretched or trimmed to its slot (never sped up more than 1.2x).

**Length limit.** No video length limit for the finals was found in the repo (`docs/finals/*`,
`ORGANISER_UPDATE.md`, `PLAN.md`, the finalist notes; D-01 only says the old video stays unless the new UI is
ready). The only stated range is **5 to 8 minutes**, for the 16 Oct submission form
(`docs/tencent/SUBMISSION.md:253`). So the **main cut stays under 8:00 even with every teammate slot filled**
(7:58); the full cut (9:52) is for YouTube or anyone who asks for the whole tour. Confirm with the
organisers whether a limit applies.

Seconds on screen: S = short cut (6:31), M = main cut (7:58 with the three teammate slots,
7:37 without them), F = full cut (9:52, 8:58 without the slots). A dash means the
scene is left out of that cut. Crossfades (0.35 s each) are allowed for in the totals.

| # | Scene | 16 Oct beat | S | M | F | Status |
|---|---|---|---|---|---|---|
| 00 | Camera intro | 1 | 31 | 31 | 31 | recorded |
| 01 | Title | 1 | 9 | 9 | 9 | card; re-run cards (new pill) |
| | **THE 16 OCT SPINE, UPGRADED: one question, then who sees what** | | | | | |
| 04 | One question, checked live (+ "each source read through its own connector") | 3 | 36 | 37 | 40 | recorder ready; re-record |
| 06 | The map of what each person can open (a view) | 4 | 20 | 20 | 26 | recorder ready; re-record (new caption) |
| 02 | Same question, two people | 5 + 7 | 36 | 38 | 43 | recorder ready; re-record |
| 03 | Restricted looks like nothing | 6 | 23 | 25 | 28 | recorder ready; re-record |
| | **ENHANCEMENTS NEXT TO THE BEAT THEY STRENGTHEN** | | | | | |
| 17 | Act: a draft and a ticket | +7, +3 | 18 | 22 | 26 | recorder ready |
| 18 | Deliverables: a table, a chart, a file | +3 | 10 | 13 | 18 | recorder ready |
| 21 | New here: tour, dictation, value calculator | 5b | - | 10 | 15 | recorder ready |
| | **NOTHING STALE (16 Oct beat 8)** | | | | | |
| 06b | Watch the brain grow | 8 | - | 22 | 28 | recorder ready |
| 19 | Add a file | +8 | - | 9 | 13 | recorder ready |
| | **THE COMPLIANCE SIDE (16 Oct beat 9)** | | | | | |
| 05 | Developer mode: the journey | 9 | 30 | 35 | 40 | recorder ready; re-record |
| 08 | Audit, Verify and the tamper demo | 9 | 40 | 42 | 50 | recorder ready; re-record |
| 07 | Analytics | +9 | 12 | 12 | 15 | recorder ready; re-record |
| 09 | The Trust centre | +9, +12 | 13 | 13 | 14 | recorder ready; re-record |
| | **WORKBUDDY, WHAT'S UNDERNEATH, HOW IT FITS (16 Oct beats 10 to 12)** | | | | | |
| 12 | WorkBuddy uses our MCP server: same gates, same sealed log | 10 | 15 | 16 | 19 | stills; **re-capture on the laptop** |
| 10 | The platform (original Walnut screens, :3300 + gateway :8300) | 11 | 16 | 16 | 18 | **new recorder scene `10a_platform`, unrecorded** |
| 10 | The Walnut engine graph inside DataBridge (shown, not used to answer) | 11 | 10 | 10 | 18 | recorder ready; re-record (new caption) |
| 13 | Tencent Hy3: the model chain, then the deploy kit card | 12 | 15 | 15 | 19 | recorder ready (record with `IB_MODELS=hy3`); new card `13b_deploy.png` |
| | **TEAMMATES' FEATURES** | | | | | |
| S1 | Voice | slot | - | 8 | 20 | slot, TODO |
| S2 | Pages beside the chat | slot | - | 8 | 20 | slot, lines written; record after verification |
| S3 | Dots | slot | - | 6 | 15 | slot, TODO |
| | **THE NUMBERS, HOW WE BUILT IT, THANKS (16 Oct beats 13 and 14)** | | | | | |
| 14 | The proof | 13 | 20 | 20 | 20 | slide |
| 15 | What it's worth | new | 21 | 21 | 21 | slide (+ host pill) |
| 11 | Built with CodeBuddy (CB7 session evidence) | 14 | 10 | 11 | 14 | card; re-run cards |
| 20 | Designed in Tencent Miora (the finals cover) | +14 | - | 5 | 8 | card; re-run cards |
| 16 | Close | 14 | 13 | 13 | 13 | slide |
| | **Total, with crossfades** | | **6:31** | **7:58** | **9:52** | |

Where the time goes: the 16 Oct spine (04, 06, 02, 03, 05, 08, 12, 10, 13, 14, 11, 16) is 4:31 of the 5:47 of demo in the
main cut; no enhancement scene is over 22 s, and each sits next to the beat it strengthens. EN and 简体中文 is a beat inside
scene 04 (user screens only, D-32).

**What is built (30 Oct).** The recorder has scenes 17, 18, 19, 21 and a rewritten 06b, and a Miora card (20);
the assembler has `--cut`, the new order (audit before the graph), optional rows for every new scene and the three
teammate slots (`S1_voice.webm`, `S2_jit_ui.webm`, `S3_dots.webm`: skipped, with a printed line, until the clip
exists); the script has all the lines, with **[MAIN]** and **[FULL]** extras for the longer cuts. Nothing has been
recorded. Cue sheets for each cut, from the planned lengths:
`python docs/finals/video/assemble_finals.py --cues-only --plan --cut short|main|full` (main writes
`narration_cues.md`, the others `narration_cues_short.md` / `_full.md`).

**Teammate slots.** Each slot needs: the feature name, the page, the person, the exact on-screen strings, and the
test behind every claim (repo rule 8). Then three edits: a `SCENES` entry in `record_finals.cjs` named like the
clip file (`S1_voice`, `S2_jit_ui`, `S3_dots`), the `### S1 ·` section in `VIDEO_SCRIPT_FINALS.md` (replace the
TODO lines), and nothing in the assembler (the rows are already there).

## How the 16 Oct video showed the platform and the Tencent tools, and how the new one does (31 Oct)

Founder's direction: show the platform pieces and the Tencent tools the same way the 16 Oct video did (the same
kind of shot, order, caption style and pacing), upgrading only the screens. Sources: `docs/tencent/VIDEO_SCRIPT.md`,
`submission/05_video/timeline.json`, `submission/05_video/cards/`, `docs/tencent/video/assemble.py`.

| 16 Oct beat | How it was shown (16 Oct) | How the new one shows it |
|---|---|---|
| 1 Hello (Tencent named) | Title card with the product name; CodeBuddy and Hy3 named in the first line | **01** the same kind of title card, pills "Built with Tencent CodeBuddy" and "Wired for Tencent Hunyuan (Hy3)"; the first line names both |
| 10 Ask from WorkBuddy (23 s) | Three real WorkBuddy window stills (connectors 5 s, two people 11 s, compliance 8 s), a caption pill on each | **12** the same stills pattern, connectors then two people (the compliance still is dropped: D-30 changed that connector); to be re-captured on the laptop. New words: an MCP agent, same gates, same sealed log |
| 11 What's underneath (36 s) | A screen recording of the ORIGINAL Walnut screens on :3300 (graph with a node opened, brain + time slider, evidence chain, ontology), small captions | **10** the same kind of recording of the original screens (`10a_platform`: Builder adapter agents and agents, Connectors, Data, the engine gateway :8300 with the federated engine, the graph), every caption "The platform: …"; then the engine's graph inside DataBridge (`10_engine`), captioned "shown here, not used to answer" |
| 12 How it fits together (22 s) | The architecture / trust-boundary card, Hy3 line ("[otherwise] quoted until a TokenHub key is added") | **13** the live model chain on Mission control (Hy3 through TokenHub, then the quoted fallback), then a still card in the same card style: the Tencent Cloud Lighthouse deploy kit ("written; no live instance yet") |
| 13 The numbers (11 s) | The results card | **14** the app's proof slide (+ **15** the business slide with the "Target host: Tencent Cloud" pill) |
| 14 How we built it, thanks (16 s) | The closing card; CodeBuddy named over it (nine sessions, security reviews found real bugs) | **11** a still card with a CodeBuddy session screenshot (CB7, the finals security review) and what it built, "nine recorded sessions"; **20** a still card with the Miora finals cover and the Miora session screenshot; **16** the close slide |

## Scene by scene (first video vs the 30 Oct test assembly)

Old times are from `submission/05_video/timeline.json` plus the 0:32 intro (the old demo file started
after it). New times are from the latest test assembly; they move a little with every re-shoot, and
`narration_cues.md` always has the real ones.

| Old time | Old scene: what was shown | Old narration (gist) | → | New scene | What the new UI shows | New narration (gist) | Status |
|---|---|---|---|---|---|---|---|
| 0:00–0:32 | Camera intro | Hi, I'm Ishaan; an AI that can read everything can leak everything; let me show you | → | **00** Camera intro (0:00–0:32) | The same file, `voice/intro_final.mp4`, with its own sound | Unchanged | **Keep** (reused as is) |
| 0:32–0:52 | 1 Hello: title card, sign-in page, picks Jordan | This is the Walnut Internal Brain… | → | **01** Title (0:31–0:37) | New title card: "Walnut DataBridge. Answers you can prove." | "This is Walnut DataBridge. And we'll start with Aspire's own question." | **Changed** (new name, shorter) |
| 0:52–1:21 | 2 Why this matters: problem card | Hours lost hunting; in a fintech the wrong person seeing a fraud case is a crime | → | (dropped) | | The intro already says it; finals judges asked us to skip background | **Dropped** |
| 1:21–1:57 | 3 One question, three platforms: Jordan asks about the DB migration | One answer from Slack, Jira, Confluence, each sentence cited | → | **02** Same question, two people (0:37–1:16) | Compare: Amy (account manager) and Rachel (MLRO) ask "Why has Velquarry Trading's account been restricted…?" | Amy gets the approved script; Rachel gets the case; tipping-off is an offence, so the system never hints | **New** (the run sheet's hook) |
| 1:57–2:13 | 4 Map: Jordan 867 items, Ken 24 | No "plus 843 hidden": for Ken the rest doesn't exist | → | **06** Knowledge graph (2:37–3:01) | Developer → Knowledge graph as Jordan; search "migration"; **Compare two people** with Ken side by side | "For Ken, the rest simply isn't there" (the old idea, nearly the old words) | **Changed** (new view; counts no longer spoken because the world is growing) |
| 2:13–2:31 | 5 Compare: Jordan vs Ken, same question | Jordan gets the full picture; Ken a polite refusal | → | folded into **02** and **03** | | | **Changed** |
| 2:31–2:45 | 5b Guided tour | A short tour for anyone new | → | (dropped for time) | Tours still exist in the app (User and Developer) | | **Dropped** (could return as a 10 s beat if the cut runs short) |
| 2:45–3:02 | 6 Restricted vs non-existent: Ken, Q3 vs Q7 breach | Byte-for-byte identical; 400 answers, none changes | → | **03** Restricted looks like nothing (1:16–1:38) | Compare "Same person · two questions": Ken asks about Velquarry and about "Bayfront Spice Traders" (made up); the "Identical responses" marker | Identical, word for word; 400 pairs, zero differences | **Changed** (same claim, fintech example, live marker) |
| 3:02–3:29 | 7 The fintech part: Amy (Velquarry, Bayfront), Leo (Indonesia offer) | What can I tell this customer? Approved script, never case notes | → | folded into **02** and **03** | | | **Changed** |
| — | (no equivalent) | | → | **04** A source, checked live (1:37–2:04) | Rachel's own Ask page: live progress, cited answer, the source sheet (version, "Checked live at", highlighted lines), then 简体中文 and back | Every sentence points at a passage she can open now, checked seconds ago; the user screens speak Chinese (Developer pages stay English, D-32) | **New** |
| 3:29–3:59 | 8 Access changes: IT removes Jordan from a channel; Priya sees runbook v8 | Checked with the source every time; nothing stale | → | (dropped for time) | Still in the app: Developer → Simulator as `admin.it` | | **Dropped** (first candidate to bring back; the claim is also in the Trust centre, scene 09) |
| — | (no equivalent) | | → | **05** Developer mode: the journey (2:03–2:37) | Maya switches to Developer mode → Journey → Rachel's request: millisecond waterfall, three ledger seals, the Gate-2 table | Two checks before anything is composed; three records sealed before Rachel saw a word; Maya never sees the answer | **New** |
| — | (no equivalent) | | → | **06b** Watch the brain grow (OPTIONAL) | Jordan posts a Slack note at `/dev/graph?add=1`; it appears in his graph; he asks and gets it back cited | The brain keeps up, checked with Slack like everything else | **New, optional** (being built; left out automatically until its clip exists) |
| — | (no equivalent) | | → | **07** Analytics (3:00–3:19) | Maya: requests, answer rate, latency, Gate-2 deny rate; **Live only** | Straight from the verified log; seeded history labelled | **New** |
| 3:58–4:33 + 4:33–4:42 | 9 Audit trail: Maya's plain-English search, a reasoned reveal, Verify; then a still "tamper" card | Ask in plain English; reveals need a reason; Verify catches an edit | → | **08** Audit, Verify and the tamper demo (3:18–3:55) | Maya types "Everything mlro.tan accessed in the last 7 days" → Interpret → rows; **Verify** (intact); **Run the tamper demo on a copy** live: "Chain broken at entry …", "Your real audit log was not modified" | Plain-English search; Verify intact; the tamper demo changes 'forbidden' → 'verified' on a copy and Verify points at the entry | **Changed** (the tamper is now live in the app, not a still card) |
| — | (no equivalent) | | → | **09** Trust centre (3:55–4:09) | Seven controls, the frameworks they map to, the tests that prove them | Each control enforced in code, mapped to Singapore's AI and data rules, proved by a named test | **New** |
| 4:41–5:04 | 10 WorkBuddy: three stills (connectors, two people, compliance) | People don't want another app; each connector has its own signed token | → | **12** Ask from WorkBuddy (4:51–5:08) | The same real window captures (connectors; two people) | "A second front door… Jordan's connector gets Jordan's answer, and Ken's gets Ken's" | **Keep** (same captures; the compliance still is dropped for time) |
| 5:04–5:41 | 11 What's underneath: the original Walnut screens at :3300 (graph, brain + time slider, evidence chain, ontology) | Every fact has a source; drag the slider and the knowledge rewinds; follow a value to the exact row | → | **10** The Walnut engine (4:09–4:39) | The same engine, now inside DataBridge (Developer → Walnut engine): snapshot counts, Company brain time slider, Provenance chain | Same lines, mostly verbatim | **Changed** (re-shot in the new UI; the old clip `05_video/clips/11_walnut_original.webm` still works as a fallback) |
| 5:41–6:02 | 12 How it fits together: trust-boundary card; Hy3 line | Two checks, grounding, audit; Hy3 wired, not live: with the stand-in model on, "answers are AI-generated from your cited sources" ([IF MODEL OFF] variant: quoted from the sources) | → | **05** + **09** (live), and **13** Tencent Hy3 (5:07–5:21) | Mission control's model chain: Hy3 through TokenHub, then the quoted fallback (recorded with `IB_MODELS=hy3`, so no other model is named), "Extractive only"; then the Tencent Cloud deploy kit card | "Until Hy3 is switched on, answers are AI-generated from your cited sources, through the same checks" ([IF MODEL OFF]: quoted from the sources) | **Changed** (live status instead of a card; honest wording kept) |
| 6:02–6:12 | 13 The numbers card | ~6,000 adversarial requests, 0 leaks; 400 pairs, 0 differences | → | **14** The proof (5:20–5:38) | The app's proof slide (0 / 7,374; 0 / 400; 0 / 1,152) + label: 1,278 backend and 185 browser tests, none failing (30 Oct evening, cloud clean checkout: 1,278 collected, 1,274 passed, 4 skipped) | Exact numbers plus the test counts | **Changed** (new slide, updated numbers) |
| — | (no equivalent) | | → | **15** What it's worth (5:38–5:57) | The app's business slide: ≈ US$1.3M a year (estimate), break-even about 7 min/week | Estimate, not a measurement; pays for itself at about seven minutes a week | **New** |
| 6:12–6:28 | 14 Built with, thanks: closing card | CodeBuddy mapped boundaries, built the connector, reviewed security; thanks | → | **11** Built with CodeBuddy (4:38–4:51) and **16** Close (5:57–6:08) | CodeBuddy card (the security-review session); the app's close slide with the QR code | CodeBuddy lines moved next to the other Tencent beats; close with the tagline and the ask | **Changed** |

## Can Ishaan reuse the old voice-over?

The old recording (`submission/05_video/voice/narration_clean.m4a`) was read against a different picture,
and most lines changed, so **record the whole demo voice-over again** in one sitting. That keeps one
sound and one pace from start to finish, and the cue sheet is timed to the new picture. The camera intro
is reused as it is, with its own sound.

Lines that are the same as, or nearly the same as, the first video (easy to read; no new wording):

| New scene | Line | First video |
|---|---|---|
| 06 | "For Ken, the rest simply isn't there." | Scene 4: "for Ken, the rest simply doesn't exist" |
| 10 | "Drag the time slider, and the company's knowledge rewinds to any past date." | Scene 11, verbatim |
| 12 | "…each person's connector carries their own signed token, so Jordan's gets Jordan's answer." | Scene 10, nearly verbatim |
| 13 | "…through the same checks." | Scene 12 [otherwise], same ending |

Everything else is new wording for the new screens: scenes 01–05, 06b, 07–09, 11, 13–16.

## Keeping it modular

- One clip per scene; re-shoot one with `node docs/finals/video/record_finals.cjs <scene>` and re-run
  `assemble_finals.py`. The edit adapts each clip to its target length (±10–15%) without visible speed-up.
- **How takes are kept** (fix for Codex round 2b #3). Each recording goes into its own folder,
  `submission/finals_video/clips/takes/<scene>/<take>/` (the video plus `take.json` with its marks); a
  failed take is kept there as `<scene>.FAILED.webm`. Only a take that finished and passes the check
  (every label the scene shows, one start mark, durations above 0, the video on disk) replaces the
  current good take, `clips/<scene>.marks.json`, in one rename; that file names the take's video, so
  the video and its marks always come from the same take. A failed re-record leaves the previous good
  take untouched, and the recorder exits 1. `assemble_finals.py` uses only the current good take and
  refuses, before rendering anything, if a scene's take is incomplete or is an old-style marks list (all
  clips recorded before 30 Oct are, so re-record them). Old takes are not deleted automatically.
  `--self-test` on either script checks this without a browser or ffmpeg.
- Teammates may add features before the freeze. A new scene is three edits: a `SCENES` entry in
  `record_finals.cjs`, a `SEGMENTS` row in `assemble_finals.py` (mark it `{"optional": True}` while it is
  unfinished), and a `### NN · Title` section with `>` lines in `VIDEO_SCRIPT_FINALS.md`.
- The synthetic world is growing (more documents) and the stack will be restarted: **no clip is final
  until it is re-recorded after that restart**. Spoken lines no longer quote the graph or engine counts,
  so they stay true; the screen shows whatever the counts are.
