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
| After §4.1, 4.2, 4.5, 4.6 and 4.7 (applied the same evening, see §6) | **8.5 / 10** on paper | Tencent named at 0:38, scene 21 is one beat with an accessibility caption, scene 17 matches the tests, the slides and the close have room. The last half point needs a take |
| Plus §4.4 (WorkBuddy re-capture, founder's screen) and a real tester line | **9 / 10** | |

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

## 6. Second pass, same evening: §4 applied (all but the WorkBuddy re-capture)

Done at the coordinator's request, after the first pass was committed. Both self-tests pass; `node --check` passes;
the three planned cue sheets were regenerated. Main cut stays **7:56** with all three teammate slots (7:35 without);
after the third pass (§7) short is 6:18, full 9:43. No TIGHT warning in any cut.

| Item | What changed | Where |
|---|---|---|
| §4.1 Tencent in the first 20 s | Title card 6 → 9 s. Line: "This is Walnut DataBridge, built with Tencent CodeBuddy and wired for Tencent's Hunyuan. Let's start with Aspire's own question." "Wired for" because Hy3 is first in the model chain through TokenHub but has not answered live (STATUS, scene 13); the card gets a fourth pill, "Wired for Tencent Hunyuan (Hy3)" | `SEGMENTS` 01, `titleHtml()`, script scene 01 |
| §4.2 Scene 21 | One story beat ("New here?"): tour offer, then the microphone button, with a new caption "Text size, high contrast and reduce motion are settings; accessibility checks run in the browser tests" (axe runs in 13 of 30 e2e files, so not "every test"); then a short look at the value calculator, carried by its caption. The presenter page is out of the video (a stage tool; the judges see it live). Spoken: one line in main, one [FULL] line. Slot 18 → 12 (main), 25 → 16 (full) | `record_finals.cjs` 21_extras (5 labels, test ids only), `SEGMENTS` 21, script scene 21 |
| §4.5 Room on the slides and Hy3 | 13: 12 → 14 (full 16); 14: 18 → 20 (pill moved to 10.5 s); 15: 19 → 21; 16: 11 → 13. Close restored to "We'd love one pilot team at one regulated company." Slack now 2.3 to 3.3 s on 13/14/15 and 1.3 s on 16 | `SEGMENTS`, script scenes 13 to 16 |
| §4.7 Scene 17 follows the tests | `act.spec.ts`: the reply draft is the account manager's, the ticket the engineer's. The scene now starts as Amy (Velquarry starter, the hook's question, "what should I tell the customer?" becomes a reply), Draft reply, Esc; then `login(page, 'jdoe')` on the same page, the migration starter, Create ticket, Confirm, the link. Four labels, test ids only. Slot 18 → 22 (main), 15 → 18 (short), 22 → 26 (full). Line: "Amy asks for a draft reply to the customer: the approved wording, built from the cited facts, and never sent. Jordan's answer offers a ticket, but it's only a proposal until he confirms." | `record_finals.cjs` 17_act, `SEGMENTS` 17, script scene 17 |
| Paying for it (main cut, +15 s) | 04: 30 → 28; 18: 18 → 15; 21: 18 → 12; 06: 20 → 18; 10: 20 → 18 (all had 3.5 s or more of slack). 02 and 08 untouched: they are the story | `SEGMENTS`; `VIDEO_PLAN.md` timeline table and shot list updated to match |

Not done: §4.3 (say "synthetic" in the main cut; still a [FULL] line in scene 06, founder's call) and §4.4 (WorkBuddy
re-capture; needs the founder's screen).

First-take checks added by this pass: 17 needs Amy's Velquarry answer to offer a draft (`act-draft-view`) and
Jordan's migration answer to offer a ticket (`act-ticket-view`); either missing fails the take with its reason. 21
still needs the speech API for the microphone button (else drop the dictation half of the line).

## 7. Third pass: the 16 Oct video is the core (founder's principle, 30 Oct evening)

Principle applied: the original video's beats, in its order, with its messages, are the main body; every new
feature is attached to the beat it strengthens ("and now it also..."), never a block of its own. `SEGMENTS` was
reordered, the self-test now asserts the spine order, the script sections were re-grouped and each scene names
its original beat, and `VIDEO_PLAN.md`'s shot list and timeline table were regenerated from `SEGMENTS`. Main cut
**7:56** with the three slots (7:35 without); short 6:18; full 9:43. Both self-tests pass; cues regenerated; no TIGHT.

### 7.1 Original vs new, beat by beat

| 16 Oct beat (time) | Its message | New scene (main cut s) | Same message, upgraded how | Enhancement attached ("and now it also...") |
|---|---|---|---|---|
| 1 Hello (0:32–0:52) | name, the challenge, CodeBuddy and WorkBuddy named | 00 + 01 (9) | title card and tagline; "built with Tencent CodeBuddy and wired for Tencent's Hunyuan" at 0:38 | |
| 2 Why this matters (0:52–1:21) | hours lost; the wrong person seeing a fraud case is a crime | dropped (the camera intro says it; 16 Oct's own call) | the crime point survives in 02 (tipping-off) | |
| 3 One question, three platforms (1:21–1:57) | one plain question, one answer, every sentence sourced, only what Jordan can open, re-checked with the source | 04 (34) | Rachel asks Aspire's question; source sheet shows the exact lines, the version, "checked live seconds ago" | the user screens work in 简体中文 (04); Act (17) and Deliverables (18) turn the same kind of answer into a reply, a ticket, a file |
| 4 The map (1:57–2:13) | Jordan 867 / Ken 24; no "plus 843 hidden" | 06 (20) | the knowledge graph; Compare two people; "for Ken, the rest simply isn't there" (near-verbatim); counts on screen, not spoken | |
| 5 Same question, two people (2:13–2:31) | Compare side by side | 02 (38) | Amy and Rachel, one screen | |
| 5b Guided tour (2:31–2:45) | for anyone new | 21 (12) | the tour offer | dictation, the accessibility caption, the value calculator |
| 6 Restricted vs non-existent (2:45–3:02) | byte-for-byte identical; 400 answers, none changes | 03 (25) | Ken, Velquarry vs Bayfront; the live "Identical responses" marker; "four hundred pairs, zero differences" | |
| 7 The fintech part (3:02–3:29) | what can I tell this customer? approved script, never case notes | 02 (with 5) | Amy vs Rachel; tipping-off said plainly | 17 Act: the answer becomes the reply draft, never sent |
| 8 Access changes (3:29–3:59) | checked with the source every time; nothing stale | 06b (22) + 19 (10) | Jordan posts a note, it is in his graph at once, Ken sees nothing; "Nothing stale" | uploads through the same checks |
| 9 The audit trail (3:59–4:42) | Maya, plain English; what was considered, allowed, denied and why; chained and sealed; Verify catches an edit | 05 (35) + 08 (43) | the request journey with real ms and three sealed records; the tamper demo live on a copy, Verify points at the entry | 07 analytics from the same log (12); 09 Trust centre (14) |
| 10 WorkBuddy (4:42–5:04) | people don't want another app; signed tokens; the AI can't decide who you are | 12 (15) | same captures; "Nobody wants another app, so it also lives inside Tencent WorkBuddy"; "The agent never decides who you are" | |
| 11 What's underneath (5:04–5:41) | the Walnut engine; time slider; follow a value to the row | 10 (18) | same lines, re-shot inside DataBridge | |
| 12 How it fits together (5:41–6:02) | two checks, grounding, audit record; Hy3 via TokenHub or quoted answers | 13 (14) | the live model chain; [FULL] "Two checks before any model, a grounding check after, the record written first" | |
| 13 The numbers (6:02–6:12) | attacked on purpose: ~6,000 requests, 0 leaks; 400 pairs, 0 differences | 14 (20) | the app's proof slide: 7,374 / 0, 400 / 0, test counts | 15 the business case (21), with a Tencent Cloud host pill |
| 14 Built with, thanks (6:12–6:28) | CodeBuddy mapped boundaries, built the connector, reviewed security; six sessions; thanks | 11 (12) + 20 (8) + 16 (13) | nine sessions; Miora; "Answers you can prove"; the ask | |

Every original beat except 2 (dropped on 16 Oct's own advice) is present, in the original order, with the same
message. The spine (04, 06, 02, 03, 05, 08, 12, 10, 13, 14, 11, 16) is 4:31 of the 5:47 of demo in the main cut.
Trade-off accepted: the hook is now Rachel's single cited answer (as 16 Oct opened on Jordan's), and the
two-people compare comes third; the question itself is still the first thing said after the title.

Lines changed for the new order: 04 opens with "Rachel is the money-laundering reporting officer at a fintech. She
asks Aspire's question..."; 06 introduces Jordan and Ken ("Here is everything Jordan, a backend engineer, can
open..."); 02 opens "Now the same question from two people: Amy, an account manager, left; Rachel, right. And what
should I tell the customer?"; 03 "Now Ken on his own."; 17 "And now Amy's answer can become the reply itself.";
18 "Answers can also become files"; 21 "And if you're new here..."; 06b ends "Nothing stale."; 05 "Now the
compliance side, as Maya."; 12 "Nobody wants another app, so it also lives inside Tencent WorkBuddy."; 13 gains
the [FULL] frame line. Slots: 04 28 → 34, 02 45 → 38, 06 18 → 20, 08 45 → 43, 12 14 → 15, 13 full 16 → 17.

### 7.2 The ten criteria: scene, the line or visual that earns it, the gap, the smallest truthful fix

| # | Criterion | Scene(s) | The line or visual that earns it | Gap | Smallest truthful fix |
|---|---|---|---|---|---|
| 1 | Impact & Relevance | 04, 02, 17, 15 | Aspire's question asked twice (Rachel, then Amy and Rachel); "In Singapore, telling Amy that a suspicious-transaction report even exists is a criminal offence. So the system never does."; Amy's answer becomes the customer reply; "about one point three million US dollars a year. It's an estimate" | none | |
| 2 | Human-Centered Design | 04, 02, 21, 17 | persona screens, plain answers, the source sheet; the tour offer; "not sent" and "nothing is created until he confirms" | no evidence of real users (the hallway test has not happened) | none truthful today. When `FIX_LOG.md` has a real row, one [MAIN] line in 21: "Five people tested it on Saturday; one broke the source sheet, we fixed it." Do not add it before |
| 3 | AI Interaction | 04, 05, 13, 12 | cited answer inside two gates; the model card on the journey; "Tencent Hy3 is wired in through TokenHub, with a safe fallback. Until it is switched on, answers are quoted from the sources"; WorkBuddy's agent with signed tokens | no model is live, and the video says so | only Hy3 going live (founder): then `--hy3`, re-shoot 02, 04, 05, 13 |
| 4 | Technical Execution | 05, 08, 10, 14 | "Each bar is a real timing"; "three records were sealed and chained before Rachel saw a word"; the tamper demo; the engine; "one thousand one hundred and thirty-one backend tests, plus a hundred and sixty-six browser tests, none failing" | none | |
| 5 | Feasibility | 15, 07, 09 | break-even "about seven minutes a week"; analytics from the verified log; the new pill "Target host: Tencent Cloud. One-command deploy kit in the repo; no live instance yet" | was: Tencent Cloud as the host never said | **done**: the pill on scene 15 (no spoken cost; wording from the run sheet and rubric row 5) |
| 6 | Demo & Storytelling | the whole arc; 08 → 16 | the 16 Oct spine in order; the tagline earned at the tamper demo ("That's what we mean by answers you can prove") and paid off at the close | nothing recorded yet | record |
| 7 | Innovation & Creativity | 03, 08, 06b | "A restricted file looks exactly like nothing"; the tamper on a copy, "Verify points at the exact entry"; "Ken's graph, right beside it, shows nothing new" | none | |
| 8 | UX & Accessibility | 04, 21 | "the screens you ask from work in Chinese too"; the dictation button; the new caption "Text size, high contrast and reduce motion are settings; accessibility checks run in the browser tests" | was: nothing beyond language | **done**: the caption in 21 (axe runs in 13 of 30 e2e files, so "in the browser tests", not "every test") |
| 9 | Responsible AI & Ethics | 02, 03, 05, 08, 09, 06 | tipping-off; identical refusals; "Maya sees every decision. She never sees the answer itself."; write-ahead chain; "seven controls ... mapped to Singapore's AI and data rules, and proved by a named test"; [FULL] "Company A is a synthetic company; every name here is made up" | the synthetic-data line is full-cut only | founder's call (§4.3): drop the [FULL] tag in 06 (main slack 1.5 s; needs +2 s on 06, taken from 02's 3.9 s) |
| 10 | Overall Quality & Judge's Impression | 01, 14, 15, 16 | one design system; honest numbers from `eval_summary.json`; "We'd love one pilot team at one regulated company" | nothing recorded yet | record, then watch each cut once silent |

No criterion is without a scene. The two filled this pass: 5 (host pill) and 8 (accessibility caption). The two
that cannot be filled truthfully today: 2 (needs the hallway test) and 3 beyond "quoted" (needs Hy3 live).

## 5. What not to touch

Scenes 02, 03, 08 and 14 are the video. Their words are now in the order the picture wants, every number is
checked by the self-test, and the tamper demo ends on the tagline. Re-shoot for the picture, not the words.
