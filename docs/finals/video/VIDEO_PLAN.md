# Finals video plan: the first video, side by side with the new one

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

## Scene by scene

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
| — | (no equivalent) | | → | **04** A source, checked live (1:37–2:04) | Rachel's own Ask page: live progress, cited answer, the source sheet (version, "Checked live at", highlighted lines), then 简体中文 and back | Every sentence points at a passage she can open now, checked seconds ago; the app speaks Chinese | **New** |
| 3:29–3:59 | 8 Access changes: IT removes Jordan from a channel; Priya sees runbook v8 | Checked with the source every time; nothing stale | → | (dropped for time) | Still in the app: Developer → Simulator as `admin.it` | | **Dropped** (first candidate to bring back; the claim is also in the Trust centre, scene 09) |
| — | (no equivalent) | | → | **05** Developer mode: the journey (2:03–2:37) | Maya switches to Developer mode → Journey → Rachel's request: millisecond waterfall, three ledger seals, the Gate-2 table | Two checks before anything is composed; three records sealed before Rachel saw a word; Maya never sees the answer | **New** |
| — | (no equivalent) | | → | **06b** Watch the brain grow (OPTIONAL) | Jordan posts a Slack note at `/dev/graph?add=1`; it appears in his graph; he asks and gets it back cited | The brain keeps up, checked with Slack like everything else | **New, optional** (being built; left out automatically until its clip exists) |
| — | (no equivalent) | | → | **07** Analytics (3:00–3:19) | Maya: requests, answer rate, latency, Gate-2 deny rate; **Live only** | Straight from the verified log; seeded history labelled | **New** |
| 3:58–4:33 + 4:33–4:42 | 9 Audit trail: Maya's plain-English search, a reasoned reveal, Verify; then a still "tamper" card | Ask in plain English; reveals need a reason; Verify catches an edit | → | **08** Audit, Verify and the tamper demo (3:18–3:55) | Maya types "Everything mlro.tan accessed in the last 7 days" → Interpret → rows; **Verify** (intact); **Run the tamper demo on a copy** live: "Chain broken at entry …", "Your real audit log was not modified" | Plain-English search; Verify intact; the tamper demo changes 'forbidden' → 'verified' on a copy and Verify points at the entry | **Changed** (the tamper is now live in the app, not a still card) |
| — | (no equivalent) | | → | **09** Trust centre (3:55–4:09) | Seven controls, the frameworks they map to, the tests that prove them | Each control enforced in code, mapped to Singapore's AI and data rules, proved by a named test | **New** |
| 4:41–5:04 | 10 WorkBuddy: three stills (connectors, two people, compliance) | People don't want another app; each connector has its own signed token | → | **12** Ask from WorkBuddy (4:51–5:08) | The same real window captures (connectors; two people) | "A second front door… Jordan's connector gets Jordan's answer, and Ken's gets Ken's" | **Keep** (same captures; the compliance still is dropped for time) |
| 5:04–5:41 | 11 What's underneath: the original Walnut screens at :3300 (graph, brain + time slider, evidence chain, ontology) | Every fact has a source; drag the slider and the knowledge rewinds; follow a value to the exact row | → | **10** The Walnut engine (4:09–4:39) | The same engine, now inside DataBridge (Developer → Walnut engine): snapshot counts, Company brain time slider, Provenance chain | Same lines, mostly verbatim | **Changed** (re-shot in the new UI; the old clip `05_video/clips/11_walnut_original.webm` still works as a fallback) |
| 5:41–6:02 | 12 How it fits together: trust-boundary card; Hy3 line | Two checks, grounding, audit; [otherwise] answers quoted until a TokenHub key is added | → | **05** + **09** (live), and **13** Tencent Hy3 (5:07–5:21) | Mission control's model chain: Hy3 → DeepSeek → quoted fallback, "Extractive only" | "Answers are quoted from the sources until Hy3 is switched on, through exactly the same checks" | **Changed** (live status instead of a card; honest wording kept) |
| 6:02–6:12 | 13 The numbers card | ~6,000 adversarial requests, 0 leaks; 400 pairs, 0 differences | → | **14** The proof (5:20–5:38) | The app's proof slide (0 / 5,958; 0 / 400; 0 / 976) + label: 754 backend and 124 browser tests | Exact numbers plus the test counts | **Changed** (new slide, updated numbers) |
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
- Teammates may add features before the freeze. A new scene is three edits: a `SCENES` entry in
  `record_finals.cjs`, a `SEGMENTS` row in `assemble_finals.py` (mark it `{"optional": True}` while it is
  unfinished), and a `### NN · Title` section with `>` lines in `VIDEO_SCRIPT_FINALS.md`.
- The synthetic world is growing (more documents) and the stack will be restarted: **no clip is final
  until it is re-recorded after that restart**. Spoken lines no longer quote the graph or engine counts,
  so they stay true; the screen shows whatever the counts are.
