# Finals video script: storytelling and judge-impact review (Fable, 30 Oct 2026)

Reviewer: Fable 5.1. Read: `VIDEO_SCRIPT_FINALS.md`, `VIDEO_PLAN.md`, `READINESS_30OCT.md`, the 16 Oct script
(`docs/tencent/VIDEO_SCRIPT.md`), `submission/finals_video/narration_cues.md`, the run sheet
(`docs/finals/rubric/demo_story/RUN_SHEET.md`), the rubric README and `docs/finals/reviews/round3_fable_judges.md`.
Headless and read-only on the app: no recording, no browser, no server. Nothing committed.

Line edits were made directly in `VIDEO_SCRIPT_FINALS.md` (scene ids, slot lengths, tags and the checked numbers
unchanged). Both self-tests pass after the edits; the three planned cue sheets were regenerated
(`--cues-only --plan --cut short|main|full`). The assembler's own TIGHT check (2.5 words a second against the
slot minus 0.9 s) now reports nothing in any cut; before the edits it flagged scene 21 and 11 (main) and 03 and 17
(full).

## 1. Score estimate: Demo & Storytelling (criterion 6)

| | Score | Why |
|---|---|---|
| Script as found (30 Oct, before this pass) | **7 / 10** | Right shape (core story first, 3:00 of 7:56), honest numbers, one tagline. Lost points to: the title line was filler, the hook's best line (tipping-off) came after a summary line, a list-shaped scene 21, a close with no last thought, four scenes with no room for a breath, one [FULL] line that READINESS said not to say, Tencent not named until 5:15 |
| After the line edits in this pass | **8 / 10** | Hook order fixed, tagline earned at the tamper demo and paid off at the close, every scene has room, honest synthetic-data line in the full cut |
| After the bigger changes in §4 (about 40 min of work, plus the recording itself) | **9 / 10** | Tencent named in the first minute, one fewer list scene, the WorkBuddy stills re-captured under the new name, a real tester line |

The whole-video ceiling is still set by what the picture shows: nothing has been recorded yet, and the new scenes
(17, 18, 19, 21, 06b) have never been seen in a browser. A script cannot earn the ninth point before a take exists.

## 2. What was changed, and why (clear wins, done)

| Scene | Before | After | Why |
|---|---|---|---|
| 01 | "This is Walnut DataBridge. And we'll start with Aspire's own question." | "This is Walnut DataBridge. Let's start with Aspire's own question." | Shorter, spoken form |
| 02 | Summary line, then the [MAIN] "word for word" line, then tipping-off | Amy, Rachel, "Same system, two different truths", **tipping-off**, then [MAIN] "word for word" | The criminal-offence line is the hook's payoff; it now lands right after Rachel's case, while both answers are on screen. The "word for word" line then explains the mechanism |
| 03 | "...a company that doesn't exist at all." / "We tested that on four hundred pairs of questions: zero differences." | "...a company that doesn't exist." / "We ran four hundred pairs like this: zero differences." | 4 words saved; the full cut was at the limit. "four hundred" kept for the self-test |
| 04 | "...speak Chinese too." | "...work in Chinese too." | Screens do not speak |
| 05 | "two permission checks before anything is composed" / "Maya never sees the answer itself." | "two permission checks first, then the answer is put together" / "Maya sees every decision. She never sees the answer itself." | "Composed" is jargon; the last line now says what Maya does see, so the point is a feature, not a gap |
| 08 | ended "The real log was never touched." | adds "That's what we mean by answers you can prove." | The tagline is earned at the strongest proof (T1, as the run sheet does), then paid off at the close (T2). 7 s of slack in every cut |
| 17 | "Once he confirms, the ticket is created, with a link to it." / "...it falls back to the cited sentences." | "He confirms, and the ticket exists, with a link." / "...the cited sentences are used instead." | 4 words saved (full cut at the limit); "falls back" is developer talk |
| 21 | "A guided tour is offered on first visit." / "You can dictate a question too; ..." | "There's a guided tour." / "You can dictate a question; ..." | Main cut was over by 0.1 s; now 1.9 s of slack |
| 06 | "...as one graph: four platforms, each item..." / [FULL] "Search a word, and the item opens to its passage." | "...as one graph, each item..." / **new [FULL] "Company A is a synthetic company; every name here is made up."** / [FULL] "Search a word, and the item opens." | Truth: the video never said the world is synthetic. Rule 7 and the rubric's honesty line both want it said once. Full cut only, so the main cut's length is untouched (see §4.3 for moving it to main) |
| 11 | "It built our WorkBuddy connector and the model tests, and ran a security review that found real issues." | "It built the WorkBuddy connectors, wrote tests, and its security review found real bugs." | Main cut was over by 0.1 s. Matches `DEV_PROCESS.md` (CB2/CB3/CB6/CB8 connectors, CB4 tests, CB5/CB7 reviews); there are two connectors, so plural |
| 12 | [FULL] "The compliance connector is kept separate from the user ones." | [FULL] "The agent in the middle never decides who you are." | READINESS scene 12 says to say nothing about the compliance connector over the 15 Oct stills (D-30 changed it since). The new line is the old video's best WorkBuddy sentence and is proved by `test_mcp_untrusted_frame.py`. Proof note updated |
| 16 | "Walnut DataBridge. Answers you can prove. We'd love one pilot team at one regulated company. Thanks for watching!" | "Every company's AI will be asked to prove itself. Walnut DataBridge. Answers you can prove. One pilot team, one regulated company. Thanks for watching!" | A last thought before the tagline (the run sheet's close), then the ask in six words. 24 words for 10.1 s of room: read it steadily |

Spoken words after the edits: short 672, main 891, full 1,062 (about 150 a minute against the picture in each cut).

## 3. Review by the seven questions

### 3.1 Story and hook

The first 20 s after the camera intro are the title (6 s) and the opening of scene 02. The hook is the question
itself, spoken over two people typing it: "why has Velquarry Trading's account been restricted, and what should I
tell the customer?" That is Aspire's own problem, said in Aspire's words, and it is the right hook. The payoff
(a criminal offence, so the system never hints) now comes while both answers are on screen.

The core proof gets 3:00 of 7:56 in the main cut and every piece is there: same question, two people (02); Ken
sees nothing, identical to the byte, 400 pairs (03); every value cited and checked live (04); the sealed records
and the request journey (05); the sealed log and the tamper demo (08). Scene 08 has the most time (45 s) and the
most slack, which is right: it is the beat judges remember.

One structural note: the camera intro (32 s) says "an AI that can read everything can also leak everything... let
me show you", and then the title card restarts with "This is Walnut DataBridge". Fine, but 38 s pass before a
single screen of the product. The 16 Oct video had the same shape and the founder liked it; keep it.

### 3.2 New features

Each of 17, 18, 06b and 19 says its value in one plain sentence ("From an answer to an action", "Answers can
become files", "the brain keeps up", "New files can be added too") and stays under 25 s. Scene 21 is the one
list: four unrelated things in 18 s (tour, dictation, value calculator, presenter). It reads as a feature tour and
is the weakest scene for the thread. See §4.2.

Order within Part 2 is good: Act and Deliverables first (closest to the Aspire question: "what should I tell the
customer" becomes a draft), then growth, then the graph and the engine (the "what's underneath" of the old
video), then analytics and Trust centre as the bridge to the Tencent part.

### 3.3 Spoken lines

Natural and short throughout; no hype words ("game-changing", "seamless", "revolutionary" do not appear). The
only lines over 20 words are the two question lines (02 and 08's "everything Rachel accessed..."), which are
quotes of what is typed, and the proof numbers in 14, which are mandated. "Now the fun part" (08) fits the
friendly voice. Every scene now has at least 0.3 s of slack at 2.5 words a second; the four with under 1 s
(07, 13, 14, 16 in the main cut) should be read briskly, or given 2 s more in `SEGMENTS` (see §4.5).

### 3.4 Truth

Checked against the proof cited in each scene and the repo:

- 7,374 / 0, 400 / 0, 1,131 / 166, nine CodeBuddy sessions: the self-test compares them with `eval_summary.json`
  and `video_constants.json`; passes.
- "the flagged transfers": the fixture statement says 37 inbound transfers were flagged (`fixtures/enterprise`);
  true.
- Hy3: scene 13 says "wired in through TokenHub, with a safe fallback" and "until it is switched on, answers are
  quoted from the sources". Honest, and the recorder refuses a take whose status disagrees. No other line credits
  a model with an answer.
- Platforms are mocks and the data is synthetic: the script never said so. Added as a [FULL] line in scene 06.
  No line says "real Slack" or "real Jira"; "checked with Slack" in 06b is true of the mock's API. Acceptable,
  but see §4.3.
- CodeBuddy: "found real bugs" matches `DEV_PROCESS.md` (CB5 dispositions in `SECURITY.md` §10, CB7 F7 fixed with
  tests). "Built the WorkBuddy connectors" matches CB2/CB3/CB6/CB8.
- WorkBuddy: the stills are labelled "real window capture (15 Oct)" on screen, and the compliance-connector line
  is gone (D-30). Still the weakest piece of evidence in the video: 15 Oct captures show the old product name.
- ".pptx, built without a model" (18): `test_deliverables.py` gates the export on `pptx_available()`; the recorder
  hovers, never downloads. If the stack lacks python-pptx the button is absent and the take fails cleanly. Check on
  the first take.
- "Maya sees every decision" (05): the journey shows the Considered → Gate 2 table with verdict and reason; true
  for that request.
- "mapped to Singapore's AI and data rules" (09): `FRAMEWORK_CROSSWALK.md` (AI Verify, IMDA GenAI, MAS FEAT, PDPA).
  True.
- Nothing claims a live Tencent Cloud instance, a hallway test, latency numbers, or Hy3 authorship.

### 3.5 Tencent tools

CodeBuddy 12 s, WorkBuddy 14 s, Miora 8 s, Hy3 12 s: 46 s in the main cut, all believable because each one
points at a file (nine transcripts, real captures, the Miora session PNGs, the model-chain tests) and none
over-claims. Two weaknesses: Tencent is first named at about 5:15, and the WorkBuddy evidence is two stills from
before the rename. §4.1 and §4.4.

### 3.6 Close

Now: a last thought ("Every company's AI will be asked to prove itself"), the name, the tagline, a six-word ask,
thanks. The QR code is on screen for the whole 11 s. Good enough; §4.5 suggests 3 s more so it is not read
against the clock.

### 3.7 Rubric mapping

| # | Criterion | Scenes that earn it | Gap |
|---|---|---|---|
| 1 | Impact & Relevance | 02 (Aspire's question), 17 (the draft reply), 15 (what it's worth) | none |
| 2 | Human-Centered Design | 02, 04 (persona screens, plain answers, source sheet), 21 (tour) | No tester line: the hallway test has not happened. Do not add one until `FIX_LOG.md` has a real row |
| 3 | AI Interaction | 04 (cited answer), 05 (model card), 13 (Hy3 chain), 12 (WorkBuddy agent) | Honest "no model" throughout; only Hy3 live changes this |
| 4 | Technical Execution | 05 (real timings, sealed records), 08 (chain), 10 (engine), 14 (tests) | none |
| 5 | Feasibility | 15 (break-even), 07 (live analytics), 09 | Tencent Cloud as the target host and the deploy kit are not mentioned (the run sheet says it). One clause in 15 if the slot grows by 2 s |
| 6 | Demo & Storytelling | the whole arc; 21 (presenter) | see §1 |
| 7 | Innovation & Creativity | 03 (identical refusals), 08 (tamper on a copy), 06b (Ken's side: nothing) | none |
| 8 | UX & Accessibility | 04 (简体中文), 21 (dictation, keyboard-only presenter) | Settings (text size, contrast, reduce motion) and axe-in-every-test are never shown or said. One caption in 21 would do it |
| 9 | Responsible AI & Ethics | 02 (tipping-off), 03, 05 (Maya never sees the answer), 08, 09 (Trust centre) | none |
| 10 | Overall Quality | 14, 15, 16; the one design system across every scene | none |

No criterion is without a scene. The two thinnest are 2 (no evidence of users) and 8 (accessibility beyond
language and voice).

## 4. Bigger suggestions (not done; each needs a slot change or a founder decision)

### 4.1 Name Tencent in the first minute (+0.5 on 6, +0.5 on 3)

The 16 Oct video said "Tencent CodeBuddy helped build it, and it works inside WorkBuddy too" in its first 20 s.
The new one waits until 5:15. The Tencent judges should hear their tools early. Cheapest fix: give the title card
9 s instead of 6 (`SEGMENTS` row "01_title.png") and read "This is Walnut DataBridge, built with Tencent
CodeBuddy. Let's start with Aspire's own question." Costs 3 s in every cut (main 7:59 with all three teammate
slots; 7:38 without). Alternative with no time cost: say it over scene 05's model card ("That model card says Hy3
through TokenHub; more on that later") but 05 has 0.9 s of slack in the main cut.

### 4.2 Scene 21 is a list; make it two beats or cut the presenter (+0.5 on 6)

Four things in 18 s. Option A: split into "21a tour and dictation" (10 s, user mode) and move the value calculator
into scene 15's lead-in ("the inputs are on `/value`; change any of them"), dropping the presenter beat from the
video (it is a stage tool; the judges see it live on 3 Nov anyway). Option B: keep 21 but let the captions carry
two of the four and speak only two. Either way the thread ("answers you can prove") is not what scene 21 is about,
so shorter is better.

### 4.3 Say "synthetic" in the main cut too (+0 to +0.5 on 9, 10)

The disclosure is a [FULL] line in scene 06 today. Judges who know rule 7 will respect hearing it in the main
cut. Scene 06 has 3.5 s of slack in the main cut: drop the [FULL] tag on "Company A is a synthetic company; every
name here is made up" (11 words, 4.4 s) and shorten "Compare two people, and Ken's graph is a small corner of the
company, with no greyed-out boxes" to "Compare two people: Ken's graph is a small corner, with no greyed-out
boxes" to pay for it. Founder's call: it costs nothing in time and removes the only "is this real data?" question.

### 4.4 Re-capture the WorkBuddy stills under the new name (+1 on 3 for the Tencent judges)

The two 15 Oct captures say "Internal Brain" and predate D-30 and the four finals connector tools (CB8). A fresh
capture of one question through Jordan's and Ken's connectors, with the answer footer "Quoted from sources", is
the single most convincing Tencent artefact the video could carry, and the line already written fits it. Needs the
founder's WorkBuddy account and the stack up; 20 minutes. If it cannot happen, keep the date pill (already there).

### 4.5 Two seconds more for the slides and the Hy3 scene

Scenes 13, 14, 15 and 16 have under 1.5 s of slack in every cut at 2.5 words a second; a relaxed reader runs at
2.2. Add 2 s each to `SEGMENTS` rows 13, 14, 15, 16 (8 s per cut: main 7:56 → 8:04 with the three teammate slots,
7:43 without). If the 8:00 range is binding and all three slots are filled, take the 8 s from scene 02 (6.5 s of
slack) and 08 (7.3 s) instead; both still clear.

### 4.6 One caption for accessibility in scene 21

"Text size, contrast and motion are settings; every browser test runs an accessibility check" as a caption while
the tour offer is on screen. No spoken words, so no time cost; earns criterion 8 a second piece of evidence.
`tests/e2e/*` run axe (rubric README row 8).

### 4.7 First-take checks that affect the words

- 17: the script has Jordan asking for a draft reply; `act.spec.ts` shows the draft on Amy's answer and the
  ticket on Jordan's. If Jordan's migration answer offers no "Draft reply", either record 17 as Amy (draft) then
  Jordan (ticket), or change the line to "Amy asks for a draft reply".
- 18: ".pptx" needs python-pptx on the stack; otherwise cut the [MAIN] PowerPoint line.
- 21: the microphone button needs the speech API in headless Chromium; otherwise cut "You can dictate a question".
- 06: "Ken can open N" anchor (READINESS :393) if persona names become role-only.

## 5. What not to touch

Scenes 02, 03, 08 and 14 are the video. Their words are now in the order the picture wants, every number is
checked by the self-test, and the tamper demo ends on the tagline. Re-shoot for the picture, not the words.
