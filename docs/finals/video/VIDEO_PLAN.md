# Finals video plan: the first video, side by side with the new one

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
      1,152 injection attempts / 0 hard failures; 1,131 backend and 166 browser tests; nine CodeBuddy sessions).
- [ ] **Display** at 1920×1080, 100% scaling (Windows Settings → Display). The recorder is headless at
      1536×864 and does not depend on the screen, but ffmpeg, the playback check and the voice-over session
      do. Close other windows.
- [ ] **Do Not Disturb on** (Windows Focus / Do not disturb), phone silent, and the recorder machine plugged in
      (low CPU matters: a busy laptop makes the clips stutter).
- [ ] **Voice-over room:** quiet, headphones on, mic tested (README in `submission/finals_video/`, section 2).

**Which scenes, in which order.** Order matters: scene 02 creates Rachel's request that scene 05 clicks, and
03 to 05 give scenes 07 and 08 something to show.

1. Cards first: `node docs/finals/video/record_finals.cjs cards` (the CodeBuddy card must say "Nine
   CodeBuddy sessions"; the proof, business and close slides are the app's own pages).
2. Scenes: `FINALS_HY3=0 node docs/finals/video/record_finals.cjs 02_compare_amy_rachel 03_ken_identical
   04_source_live 05_journey 06_graph 07_analytics 08_audit 09_trust 10_engine 13_hy3_status`
3. Last: `06b_brain_grow` (it posts a real note into the mock Slack of the stack; reset afterwards with the
   step above). Then Act, Deliverables and Uploads (scenes 17 to 19, once their `SCENES` entries exist; they also
   change the stack's data) and any teammate slot.
4. `databridge\.venv\Scripts\python docs/finals/video/assemble_finals.py`, then play the SILENT file once
   end to end. Then Ishaan's voice-over, then `--voice`.

**Expected length.** Today's test assembly is 6:08 (intro 0:32 + demo 5:36) against **6:28** for the first video.
The founder's direction (30 Oct) is that the new video may be **longer** than the first, because there is more to
show: see "The new timeline" below (short cut about 6:00, main cut about 7:55, full cut about 9:15). The assembler
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
| Length | 6:28 (intro 0:32 + demo 5:56) | 6:08 in the test assembly (intro 0:32 + demo 5:36); about 6:35 with the optional scene |
| Name on screen | Walnut Internal Brain | Walnut DataBridge ("Internal Brain" is the ask product inside it) |
| Opening story | Jordan asks about the DB migration | Aspire's own question: why is Velquarry's account restricted? Amy vs Rachel |
| Tagline | none | "Answers you can prove." |
| Scripts | `docs/tencent/video/record_scenes.cjs`, `assemble.py`, `narration.py` | `docs/finals/video/record_finals.cjs`, `assemble_finals.py` (cue sheet built in) |

## The new timeline: core story first, then the new value

Founder's direction (30 Oct): show everything, give the most time and emphasis to what matters most, keep each
secondary feature short (10 to 25 s), same pattern as the first video (camera intro, then a narrated screen
demo; same toolchain). Three cuts are built from the **same takes**: a scene is either in a cut or not, and a
clip is stretched or trimmed to its slot (never sped up more than 1.2x, see the Pacing table in the script).

**Length limit.** No video length limit for the finals was found in the repo (`docs/finals/*`,
`ORGANISER_UPDATE.md`, `PLAN.md`, the finalist notes; D-01 only says the old video stays unless the new UI is
ready). The only stated range is **5 to 8 minutes**, for the 16 Oct submission form
(`docs/tencent/SUBMISSION.md:253`). So the **main cut is built to stay at or under 8:00**; the full cut is for
YouTube or anyone who asks for the whole tour. Confirm with the organisers whether a limit applies.

Seconds on screen: S = short cut (about 6:00), M = main cut (about 7:55), F = full cut (about 9:15); a dash
means the scene is left out of that cut. Crossfades (0.35 s each) are already allowed for in the totals.

| # | Scene | What it proves | S | M | F | Status |
|---|---|---|---|---|---|---|
| 00 | Camera intro (reused) | | 32 | 32 | 32 | recorded |
| 01 | Title card | | 6 | 6 | 6 | card |
| | **CORE: the most screen time** | | **158** | **180** | **203** | |
| 02 | Same question, different people: Amy vs Rachel | different people, different answers | 40 | 45 | 50 | recorded (test take) |
| 03 | Ken: a restricted file looks exactly like nothing | restricted equals non-existent | 23 | 25 | 28 | recorded |
| 04 | Every value cited: the source sheet, checked live; then EN / 简体中文 | no value without a pointer | 25 | 30 | 35 | recorded |
| 05 | Developer mode: the request journey | how it was decided | 30 | 35 | 40 | recorded |
| 08 | The sealed audit log, Verify, the tamper demo | it cannot be edited unseen | 40 | 45 | 50 | recorded |
| | **NEW VALUE: short and sharp (10 to 25 s each)** | | | | | |
| 17 | Act: a cited draft reply and a ticket, with confirm | the AI does work, safely | 15 | 18 | 22 | **to record** (test ids `act-draft`, `act-ticket`, `act-sources` exist) |
| 18 | Deliverables: a cited table, a chart, a .pptx | answers become files | 12 | 18 | 22 | **to record** |
| 06b | Live growth: Jordan posts a Slack note, it appears; Ken's graph stays empty | the brain keeps up, permissions hold | - | 22 | 28 | **to record**; the Ken half is new |
| 19 | Uploads: a file added, checked, searchable | | - | 10 | 15 | **to record** |
| 06 | The knowledge graph (Jordan; Compare two people) | | 20 | 20 | 24 | recorded |
| 10 | The Walnut engine: time slider, provenance | the evidence engine underneath | 20 | 22 | 25 | recorded |
| 07 | Analytics (Live only) | | 12 | 12 | 15 | recorded |
| 09 | Trust centre | seven controls, each proved by a test | 14 | 14 | 14 | recorded |
| | **TENCENT TOOLS** | | | | | |
| 11 | Built with CodeBuddy (nine sessions) | | 10 | 12 | 13 | card (re-run) |
| 12 | Ask from WorkBuddy | | 12 | 14 | 17 | stills (15 Oct) |
| 20 | Miora: the cover art and its brief | | - | 8 | 10 | **to make**: a card from `submission/06_cover/` and the Miora sessions |
| 13 | Tencent Hy3 (wired, not live) | honest status | 10 | 12 | 14 | recorded |
| | **TEAMMATE SLOTS** (merge before recording; left out if not landed) | | | | | |
| S1 | Voice | | - | 12 | 20 | **slot** |
| S2 | Just-in-time UI | | - | 12 | 20 | **slot** |
| S3 | "Dots" | | - | 10 | 15 | **slot** |
| 14 | The proof (7,374 / 400 / 1,152; 1,131 + 166 tests) | | 18 | 18 | 18 | slide |
| 15 | What it's worth | | 19 | 19 | 19 | slide |
| 16 | Close | | 11 | 11 | 11 | slide |
| | **Total, with crossfades** | | **about 6:03** | **about 7:55** | **about 9:15** | |

Where the time goes: the core story (02 to 05 and 08) is 2:38 in the short cut, 3:00 in the main cut and 3:23 in
the full cut, which is the largest block in every cut. No new-value scene is over 28 s. EN and 简体中文 is a beat
inside scene 04 (user screens only, D-32), not a scene of its own.

Order: core first (02, 03, 04, 05, 08), then the new value, the Tencent tools, and the proof, business and close
slides. In the current assembler the audit scene (08) still sits after the graph and analytics. Moving it up,
adding scenes 17 to 20 and the three slots, and a `--cut short|main|full` choice are **not built yet**: each is a
`SEGMENTS` change in `assemble_finals.py` (each row gets its three lengths), a `SCENES` entry in the recorder,
and a `### NN` section with `>` lines in `VIDEO_SCRIPT_FINALS.md`. The longer core scenes need extra spoken lines
(about 5 to 12 s each), to be written with the first re-shoot.

**Teammate slots.** Each slot is a `SEGMENTS` row marked optional plus a `SCENES` entry; until its clip exists the
assembler leaves it out (the main cut is then about 7:20). Get the feature name, route and exact on-screen strings
from the teammate before writing the scene's lines, and do not say anything a test does not prove (repo rule 8).

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
| 5:41–6:02 | 12 How it fits together: trust-boundary card; Hy3 line | Two checks, grounding, audit; [otherwise] answers quoted until a TokenHub key is added | → | **05** + **09** (live), and **13** Tencent Hy3 (5:07–5:21) | Mission control's model chain: Hy3 → DeepSeek → quoted fallback, "Extractive only" | "Answers are quoted from the sources until Hy3 is switched on, through exactly the same checks" | **Changed** (live status instead of a card; honest wording kept) |
| 6:02–6:12 | 13 The numbers card | ~6,000 adversarial requests, 0 leaks; 400 pairs, 0 differences | → | **14** The proof (5:20–5:38) | The app's proof slide (0 / 7,374; 0 / 400; 0 / 1,152) + label: 1,131 backend and 166 browser tests (30 Oct, commit 831297e) | Exact numbers plus the test counts | **Changed** (new slide, updated numbers) |
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
| 10 | "Underneath it all is Walnut, the evidence engine." | Scene 11, verbatim |
| 10 | "Drag the time slider, and the company's knowledge rewinds to any past date." | Scene 11, verbatim |
| 12 | "Each connector carries its own signed token, so Jordan's connector gets Jordan's answer, and Ken's gets Ken's." | Scene 10, nearly verbatim |
| 13 | "…through exactly the same checks." | Scene 12 [otherwise], same ending |

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
