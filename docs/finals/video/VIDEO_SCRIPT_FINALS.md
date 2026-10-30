# Finals demo video: script and shot list

**What it is:** the new demo video for the finals build of **Walnut DataBridge** (branch `final_v1.2`). It
replaces the 16 Oct video (https://youtu.be/fr9wqHWsijY, 6:28, made from the old UI) and keeps its pattern
exactly: Ishaan's camera intro, then a narrated screen demo of the real app, then the proof, the business case
and a short close, in the same friendly voice. Source of the pattern: `docs/tencent/VIDEO_SCRIPT.md`.

**Shape (founder's direction, 30 Oct).** (1) The core story first, with the most time: same question, different
people, different answers; every value cited; restricted looks like nothing; the request journey; the sealed audit
log and the tamper demo. (2) Then everything added since 16 Oct, each shown clearly and briefly (10 to 25 s).
(3) The teammates' features (voice, just-in-time UI, dots) are TODO placeholders until they are merged.
Written for what exists **today**.

**Three cuts from the same takes** (`assemble_finals.py --cut short|main|full`; main is the default):
short about 6:04, **main about 7:56** (under the 8:00 of the 5 to 8 minute range in `docs/tencent/SUBMISSION.md`),
full about 9:39. Each scene below lists its seconds in the three cuts. Narration lines tagged **[MAIN]** are read
in main and full, **[FULL]** only in full. The exact time of every line is in
`submission/finals_video/narration_cues.md` (and `_short`, `_full`), generated from the picture.
**Format:** 1920×1080, dark theme.

**How it's made:** each scene is recorded on its own from the running app
(`docs/finals/video/record_finals.cjs`), so one scene can be re-shot without touching the rest.
`docs/finals/video/assemble_finals.py` joins them behind the camera intro. Ishaan then records the
voice-over against the cue sheet, and `assemble_finals.py --voice <file>` lays it under the picture.

**How to read it:** like you're showing a friend something you built. Relaxed, not salesy. Short sentences.
Pauses are fine; the picture has room for them. About 150 words a minute. Lines are in plain spoken English: if
one feels stiff, say it your own way, but keep the facts and the numbers.

**Format of each scene.** Cuts (seconds) · On screen (page, person, action) · Caption (the small label at the
lower left) · Proof (the test or file behind what is claimed) · then the voice-over, in `>` lines. Only `>` lines
are spoken; `TODO` lines are placeholders and are never read.

**Hy3 lines.** Hy3 is wired in but not live (TokenHub activation is pending; as of 30 Oct it has not answered
live), so the app labels every answer "Quoted from sources (no model)". A line marked **[ONLY IF HY3 LIVE]** is
read only after Hy3 has answered live in this app; otherwise read the **[otherwise]** line. (The assembler also
still accepts the old spelling "[if Hy3 is on]".) Keep each marked line on one line: `assemble_finals.py` reads
the script line by line. If Hy3 goes live, scenes 02, 04, 05 and 13 must also be re-shot, because the answer label
changes on screen.

## Pacing: why it should feel like a person, not a robot

Same rules as the first video (`docs/tencent/VIDEO_SCRIPT.md`, "Pacing"):

| What | How |
|---|---|
| Length per scene | Each scene is about as long as its narration plus a breath (~2.5 spoken words a second). No scene sits still waiting for the voice. |
| The cursor | A visible pointer moves on gentle curves, slows into its target and shows a soft ring on each click. |
| Typing | Uneven rhythm: quicker inside words, a beat after commas and full stops, the odd hesitation. Starter questions are clicked, not typed, as people do. |
| Reading | After an answer appears, the pointer drifts along the lines being talked about, so the eye knows where to look. |
| Scrolling | Small, smooth steps, never a jump. |
| Speed | Clips play at real speed. The edit never speeds a clip up by more than 1.2×. Waits (a page loading, an answer being checked) are trimmed to about a second, as a person editing a screen recording would. Answers themselves are never cut. |
| Labels | Small captions at the lower left say who is signed in and what just happened ("Ken · outside contractor"), so the video makes sense on mute. |
| Cuts | Short cross-fades (0.35 s). Still cards and slides drift in slowly instead of freezing. |
| Text size | The app is shown at a laptop size (1536×864) and scaled up, so text is about 25% larger than on a full-HD screen. |
| Theme | The normal dark theme, not the Stage theme (that one is for the venue projector). The closing slides are the app's own `/present` pages, also dark. |

---

# Part 1 · The core story (the most time)

### 00 · Camera intro
**Cuts:** 32 s in every cut.
**On screen:** Ishaan to camera, letterboxed with the navy bars (`submission/05_video/voice/intro_final.mp4`,
reused from the first video, with its own sound).
**Caption:** "Ishaan · Team Walnut" (part of the file).
**Proof:** none needed; it is the same file as the 16 Oct video.

Already recorded. Nothing to read here. (What it says: an AI that can read everything can also leak everything,
so we built one that only answers from what you're allowed to see. "Let me show you.")

### 01 · Title
**Cuts:** 6 s in every cut.
**On screen:** title card: "Walnut DataBridge. Answers you can prove." (made by `record_finals.cjs cards`).
**Caption:** none.
**Proof:** the product name and tagline from `docs/finals/DECISIONS.md` and the run sheet.

> This is Walnut DataBridge. And we'll start with Aspire's own question.

### 02 · Same question, two people
**Cuts:** short 40 s · main 45 s · full 50 s.
**On screen:** `/compare?a=amy.am&b=mlro.tan`, "Same question · two people". Amy (account manager) left, Rachel
(MLRO) right. The pointer types *"Why has Velquarry Trading's account been restricted, and what should I tell
the customer?"*, presses **Ask both**, then reads along Amy's answer and Rachel's, and hovers a citation.
**Caption:** "Aspire's question · Amy (account manager) and Rachel (MLRO)", then "Amy · account manager: the
approved script, cited, nothing else", then "Rachel · MLRO: the case itself".
**Proof:** `tests/e2e/compare.spec.ts`; the two gates and the constant response shape (repo rules 1 and 4,
`databridge/apps/internal_brain/tests`); the "tipping off" point is stated as in the run sheet
(`docs/finals/rubric/demo_story/RUN_SHEET.md`, beat 0:15). It is a plain-language point, not legal advice.

> Two people at a fintech ask the same thing: why has Velquarry Trading's account been restricted, and what
> should I tell the customer?
> Amy, an account manager, gets the approved customer script, with its sources, and nothing else.
> Rachel is the money-laundering reporting officer. She gets the actual case: the flagged transfers, and the
> review.
> Same system, two different truths.
> **[MAIN]** The question is word for word the same. Only who is signed in is different.
> In Singapore, telling Amy that a suspicious-transaction report even exists is a criminal offence. So the
> system never does.
> **[FULL]** Who you are comes only from your signed sign-in, never from the question.

### 03 · Restricted looks exactly like nothing
**Cuts:** short 23 s · main 25 s · full 28 s.
**On screen:** `/compare?mode=person&p=ken.ext`, "Same person · two questions". Ken (outside contractor) asks
about Velquarry on the left and about "Bayfront Spice Traders", a company that does not exist, on the right. Two
refusal cards, then the marker "Identical responses: same text, same sources".
**Caption:** "Ken · outside contractor: a real customer vs a made-up one", then "Identical, word for word:
restricted looks like nothing".
**Proof:** `tests/e2e/compare.spec.ts` and `user.spec.ts` ("restricted and made-up subjects render identically");
`eval_summary.json` `differential`: 400 pairs, 0 mismatches.

> Now Ken, an outside contractor. On the left, he asks about Velquarry. On the right, about a company that
> doesn't exist at all.
> The answers are identical, word for word. A restricted file looks exactly like nothing.
> **[MAIN]** No count, and no hint that anything is hidden.
> We tested that on four hundred pairs of questions: zero differences.
> **[FULL]** So nobody can find out which customers exist just by asking.

### 04 · Every sentence has a source, checked live
**Cuts:** short 25 s · main 30 s · full 35 s.
**On screen:** Rachel's own Ask page (`/`, User mode). She clicks her starter question about the Velquarry review.
The live progress line, then the cited answer. She clicks a citation: the source sheet shows the platform, the
version, "Checked live at" and the cited lines highlighted. **Esc**. Then the language menu: 简体中文, and back to
English.
**Caption:** "Rachel · MLRO: her own screen", then "The exact passage, its version, checked live seconds ago", then
"简体中文: the user screens, one click".
**Proof:** `tests/e2e/user.spec.ts` ("ask, staged progress, cited answer, source sheet, Esc returns focus");
`ask.spec.ts`; the live source check is Gate 2 (rule 1). Chinese covers the user screens only: Developer pages stay
English and say so (D-32), which is why the line says "the screens you ask from".

> Here's Rachel's own screen. Every sentence points at a passage she can open right now.
> Click one, and you see the exact lines, the version, and when it was checked with the source: seconds ago.
> **[MAIN]** Anything she cannot open at that moment is left out of the answer.
> And the screens you ask from speak Chinese too.
> **[FULL]** Developer pages stay in English, and say so.

### 05 · Developer mode: one request, end to end
**Cuts:** short 30 s · main 35 s · full 40 s.
**On screen:** signed in as Maya (compliance officer), User mode. She switches to **Developer** at the top left,
opens **Journey** (`/dev/journey`) and picks Rachel's request from Recent requests. The waterfall with real
milliseconds, the three ledger seals, then the "Considered → Gate 2" table.
**Caption:** "Maya · compliance officer: Developer mode", "Rachel's request: every stage, real milliseconds",
"Three sealed, chained records, before she saw a word", "Every document considered: the verdict and the reason,
never the answer".
**Proof:** the journey page tests in `tests/e2e/` (`audit-trust.spec.ts`, `user.spec.ts` receipt flow) and the
write-ahead ledger rule 3 (`test_audit_ledger.py`). Maya sees verdicts and reasons, never the answer itself.

> Now the developer side, as Maya from compliance.
> **[MAIN]** The app has two modes: User, for asking, and Developer, for seeing how an answer was made.
> This is Rachel's request, end to end.
> Each bar is a real timing: two permission checks before anything is composed, then the answer.
> **[FULL]** Those timings are measured, not drawn.
> These three records were sealed and chained before Rachel saw a word.
> And below, every document that was considered, with its verdict and the reason.
> Maya never sees the answer itself.

### 08 · The sealed audit log, and the tamper demo
**Cuts:** short 40 s · main 45 s · full 50 s.
**On screen:** Maya, **Audit** (`/dev/audit`). She types *"Everything mlro.tan accessed in the last 7 days"*,
presses **Interpret**: the filter, then the rows. **Verify audit chain**: "Chain intact". Then **Run the tamper
demo on a copy**: "Your real audit log was not modified", and the row "Chain broken at entry …".
**Caption:** "Maya · asks the audit log in plain English", "Verify: every link of the hash chain re-checked",
"Tamper demo: attacks a temporary copy, never the real log", "One decision changed 'forbidden' → 'verified': caught
at that entry".
**Proof:** `test_audit_tamper.py`, `test_tamper_demo_route.py`, `audit-integrity.spec.ts`, `audit.spec.ts`; the
real log is never written by the demo (D-19).

> Maya can also ask the audit log in plain English: everything Rachel accessed in the last seven days.
> She sees the filter first, then the rows.
> **[MAIN]** Every question anyone asks is written to this chain before the answer is released.
> Verify re-checks every link in the chain: intact.
> Now the fun part. The tamper demo copies the log and attacks the copy.
> It changes one old decision, from "forbidden" to "verified", and Verify points at the exact entry.
> Deleting or rolling back records is caught too.
> **[FULL]** And if the log cannot be written, there is no answer at all.
> The real log was never touched.

---

# Part 2 · Everything added since 16 Oct (each one short)

### 17 · Act: a draft and a ticket
**Cuts:** short 15 s · main 18 s · full 22 s.
**On screen:** Jordan (backend engineer), User mode. He asks his first starter (the migration status). Under the
answer, the **Act** row: **Draft reply** opens a draft whose sentences come from the cited facts; then
**Create ticket** opens a ticket proposal, and he presses **Confirm**: the ticket is created, with a link.
**Caption:** "Jordan · backend engineer: from an answer to an action", "A draft reply: every sentence from the cited
facts", "A ticket proposal: nothing is created until he confirms", "Confirmed: the ticket exists, with a link".
**Proof:** `test_act.py`, `test_act_e2e.py`, `tests/e2e/act.spec.ts`; D-28 (a model-written draft falls back to the
cited sentences). It writes a ticket into the mock Jira, so it is recorded late.

> From an answer to an action. Jordan asks for a draft reply, built only from the cited facts.
> A ticket is only a proposal until he confirms.
> **[MAIN]** Once he confirms, the ticket is created, with a link to it.
> **[FULL]** If a draft strays from the facts, it falls back to the cited sentences.

### 18 · Deliverables: a table, a chart, a file
**Cuts:** short 12 s · main 18 s · full 22 s.
**On screen:** Jordan, the same kind of answer. Under it, the **Deliverables** row: **Table** opens a table, each
figure pointing at its sources; the pointer rests on **.pptx** (hovered, not downloaded); then the **Chart** tab.
**Caption:** "Jordan · backend engineer: an answer becomes a file", "A table whose figures point at their sources",
"A .pptx of it, built with no model".
**Proof:** `test_deliverables.py`, `test_deliverables_verify.py`, `tests/e2e/deliverables.spec.ts`; D-29 (Slides
`.pptx` for ready tables and charts, with no model; a Summary kind is not built).

> Answers can become files: a table whose figures point at their sources, and a chart from the same facts.
> **[MAIN]** Or a PowerPoint file, built without a model.
> **[FULL]** The table is built straight from the cited facts, with no model.

### 06b · Live growth: the brain keeps up
**Cuts:** not in the short cut · main 22 s · full 28 s.
**On screen:** `/dev/graph?compare=ken.ext`: Jordan left, Ken right. Jordan opens **Add a document**, types a
Slack note (*"Heads-up: the ledger cutover dry run moves to Thursday 9am, owner Priya."*) and presses **Post to
Slack**. The note appears in his live feed. Ken's feed stays empty. Then, in User mode, Jordan asks *"When is the
ledger cutover dry run?"* and gets the note back, cited.
**Caption:** "Jordan (left) and Ken (right): Jordan posts a Slack note", "It appears in Jordan's graph, checked with
Slack", "Ken's side: nothing new, he cannot open that channel", "Asked straight away: answered from the new note,
cited".
**Proof:** `test_live_growth.py`, `test_sim_add_document.py`, `tests/e2e/grow.spec.ts`; the recorder refuses the
take if Ken's feed gets any item. Needs demo features on (D-24). It writes a real note into the mock Slack.

> And the brain keeps up. Jordan posts a note in Slack, and it is in his graph straight away.
> Ken's graph, right beside it, shows nothing new: he cannot open that channel.
> A moment later Jordan asks about the note, and the answer quotes it, with its source.
> **[FULL]** The post is checked with Slack and written to the audit log, like everything else.

### 19 · Add a file
**Cuts:** not in the short cut · main 10 s · full 15 s.
**On screen:** `/dev/graph?add=1`, Jordan. In the Add sheet, the file tab: a sample file is chosen, a folder is
picked from the list, **Upload**; the status line confirms.
**Caption:** "Add a file: it goes into a folder, through the same checks", "Uploaded and written to the audit log
like everything else".
**Proof:** `test_uploads.py`, `test_upload_folders.py`, `test_extract_files.py`; Opus L5 (successful source writes
are ledgered, round-3 dispositions). Writes a file into the mock Drive.

> New files can be added too, into a folder, and the upload is written to the audit log.
> **[FULL]** PDF, Office and text files are accepted.

### 21 · A tour, dictation, the value calculator, the presenter
**Cuts:** not in the short cut · main 18 s · full 25 s.
**On screen:** Jordan. (1) The first-visit tour offer on the Ask page. (2) The microphone button in the composer,
hovered but not pressed. (3) `/value`, the value calculator, with the annual figure and the break-even. (4) `/present`,
the stage presenter's landing page.
**Caption:** "New here? A guided tour is offered once, and any time after", "Dictate a question: it types into the
box, nothing is asked until Ask is pressed", "The value calculator: every input editable, the result an estimate",
"The stage presenter: one person runs the whole demo from the keyboard".
**Proof:** `tests/e2e/tour.spec.ts`; `voice.spec.ts` (text goes in the box and nothing is asked, Esc stops, button
hidden without the speech API); `value.spec.ts`; `presenter.spec.ts` (keys off unless armed, Space-only walk).

> New here? A guided tour is offered on first visit.
> You can dictate a question too; nothing is sent until you press Ask.
> The value calculator lets you change every input.
> And one person can run the whole stage demo from the keyboard.
> **[FULL]** The tour has a User track and a Developer track.

### 06 · The knowledge graph
**Cuts:** short 20 s · main 20 s · full 24 s.
**On screen:** Jordan, Developer → **Knowledge graph** (`/dev/graph`): every item he can open. A search for
"migration" opens one item's panel. Then **Compare two people**: Ken's graph beside Jordan's.
**Caption:** "Jordan · backend engineer: everything he can open", "Ken, signed in separately: a small corner, no
greyed-out boxes". The counts (867 and 24 on 30 Oct) are on screen, not spoken: the world is growing.
**Proof:** `tests/e2e/graph.spec.ts`, `graph-screens.spec.ts`; the map totals are permission-correct (rule 1).

> Everything Jordan can open, as one graph: four platforms, each item checked with its source.
> **[FULL]** Search a word, and the item opens to its passage.
> Compare two people, and Ken's graph is a small corner of the company, with no greyed-out boxes.
> For Ken, the rest simply isn't there.

### 10 · The Walnut engine underneath
**Cuts:** short 20 s · main 20 s · full 25 s.
**On screen:** Jordan, **Walnut engine** (`/dev/brain`): "932 entities · 2,246 links · 4,710 pointers" (30 Oct
counts, on screen only). **Company brain**: the time slider dragged back, then forward. **Provenance**: the DB
Migration Plan, one value traced to its row and file.
**Caption:** "The Walnut engine: Company A as Jordan sees it", "Rewind the company knowledge in time", "One value
traced to the exact row in the exact file".
**Proof:** `tests/e2e/engine.spec.ts`, `engine-proxy.spec.ts`; the engine is reused unchanged (`databridge/packages/**`).

> Underneath it all is Walnut, the evidence engine.
> **[FULL]** Here is Company A as Jordan sees it: every entity, every link, every value with a pointer.
> Drag the time slider, and the company's knowledge rewinds to any past date.
> And any value can be traced to the exact row in the exact file.

### 07 · Analytics
**Cuts:** short 12 s · main 12 s · full 15 s.
**On screen:** Maya, **Analytics** (`/dev/analytics`): requests, answer rate, latency, Gate-2 deny rate; then
**Live only**.
**Caption:** "Maya · analytics, computed from the verified ledger", "Live only: seeded history left out".
**Proof:** `tests/e2e/analytics.spec.ts`; seeded history is labelled by the page.

> Maya's analytics come straight from the verified log: how many questions, how many answered, how fast.
> Seeded history is labelled, and one click leaves it out.
> **[FULL]** And how often the live check said no.

### 09 · The Trust centre
**Cuts:** 14 s in every cut.
**On screen:** Maya, **Trust centre** (`/dev/trust`): seven controls, each with the frameworks it maps to and the
tests that prove it.
**Caption:** "Trust centre: seven controls, each proved by a named test".
**Proof:** `tests/e2e/audit-trust.spec.ts`; the page lists the test behind each control.

> The Trust centre lists the seven controls behind all of this.
> Each one is enforced in code, mapped to Singapore's AI and data rules, and proved by a named test.

---

# Part 3 · The Tencent tools

### 11 · Built with Tencent CodeBuddy
**Cuts:** short 10 s · main 12 s · full 14 s.
**On screen:** card: one of the nine CodeBuddy session transcripts (the security review), and what CodeBuddy
built.
**Caption:** none (the card says "Nine CodeBuddy sessions. Every transcript is in the repo").
**Proof:** `docs/tencent/evidence/` (CB1 to CB9), `docs/tencent/DEV_PROCESS.md`; the count is in
`video_constants.json`.

> We built this with Tencent CodeBuddy, nine sessions in all.
> **[MAIN]** It built our WorkBuddy connector and the model tests, and ran a security review that found real issues.
> **[FULL]** All in the repo.

### 12 · Ask from Tencent WorkBuddy
**Cuts:** short 12 s · main 14 s · full 17 s.
**On screen:** two real WorkBuddy window captures (15 Oct): the three DataBridge connectors, then one question
through Jordan's and Ken's connectors with two different answers.
**Caption:** "Tencent WorkBuddy · real window capture (15 Oct)", "One question, Jordan's and Ken's connectors: two
answers".
**Proof:** `submission/02_workbuddy/`, `test_mcp_untrusted_frame.py`, D-30 (the compliance connector is kept
separate).

> It also lives inside Tencent WorkBuddy. Each connector carries its own signed token, so Jordan's connector gets
> Jordan's answer, and Ken's gets Ken's.
> **[MAIN]** Same gates, same audit log.
> **[FULL]** The compliance connector is kept separate from the user ones.

### 20 · Made with Miora
**Cuts:** not in the short cut · main 8 s · full 10 s.
**On screen:** card: the cover art and the Miora session from `submission/06_cover/`.
**Caption:** none (the card says "Made in Miora from our written brief").
**Proof:** `submission/06_cover/miora_session1.png`, `miora_session2.png`, `docs/finals/COVER_MIORA_PROMPT.md`.

> The cover art was made in Miora, from a written brief.
> **[FULL]** The prompts are in the repo.

### 13 · Tencent Hy3
**Cuts:** short 12 s · main 12 s · full 14 s.
**On screen:** Maya, **Mission control** (`/dev`) → the model chain: Hy3, then DeepSeek, then the quoted
fallback. **[otherwise]** The badge reads "Extractive only".
**Caption:** "Tencent Hy3 via TokenHub → DeepSeek → quoted fallback", then a line that follows the live status
("Hy3 not switched on yet: answers are quoted from the sources").
**Proof:** the model-chain tests (`test_model_config.py`); the recorder reads `/ib/status`, and `FINALS_HY3=0|1`
makes it refuse a take that disagrees.

> Tencent Hy3 is wired in through TokenHub, with a safe fallback.
> **[otherwise]** Until it is switched on, answers are quoted from the sources, through the same checks.
> **[ONLY IF HY3 LIVE]** The answers you saw were written by Hy3, inside exactly the same checks.

---

# Part 4 · The teammates' features (placeholders until the merge)

### S1 · Voice
**Cuts:** not in the short cut · main 8 s · full 20 s. **Left out until `clips/S1_voice.webm` exists.**
**On screen:** TODO(founder): page, person and action after the merge.
**Caption:** TODO.
**Proof:** TODO: the test or file behind it (rule 8).

> TODO(founder): the voice-over line for the voice feature, after the merge.

### S2 · Just-in-time UI
**Cuts:** not in the short cut · main 8 s · full 20 s. **Left out until `clips/S2_jit_ui.webm` exists.**
**On screen:** TODO(founder).
**Caption:** TODO.
**Proof:** TODO.

> TODO(founder): the voice-over line for just-in-time UI, after the merge.

### S3 · Dots
**Cuts:** not in the short cut · main 6 s · full 15 s. **Left out until `clips/S3_dots.webm` exists.**
**On screen:** TODO(founder).
**Caption:** TODO.
**Proof:** TODO.

> TODO(founder): the voice-over line for the dots, after the merge.

---

# Part 5 · The proof, the business case and the close

### 14 · The proof
**Cuts:** 18 s in every cut.
**On screen:** the app's proof slide (`/present/proof`): 0 leaks in 7,374 adversarial requests, 0 differences in
400 paired questions, 0 hard failures in 1,152 prompt-injection attempts. Label: 1,131 backend + 166 browser tests
(from `eval_summary.json`; 41 browser tests are skipped, so say "none failing", not "all passing").
**Caption:** "Plus 1,131 backend and 166 browser tests passing, none failing".
**Proof:** `databridge/apps/internal_brain_web/public/eval_summary.json` (`requests`, `leaks`, `differential`,
`injection`, `tests`), `databridge/evals/internal_brain/RESULTS.md`, `docs/finals/TEST_REPORT.md`.

> We didn't just demo it, we attacked it. Seven thousand three hundred and seventy-four adversarial requests:
> zero leaks. Four hundred paired questions: zero differences. And one thousand one hundred and thirty-one backend
> tests, plus a hundred and sixty-six browser tests, none failing.

### 15 · What it's worth
**Cuts:** 19 s in every cut.
**On screen:** the app's business slide (`/present/business`): about US$1.3M a year (estimate), break-even about 7
minutes per person per week, and the inputs.
**Caption:** none (the slide says "estimate").
**Proof:** `docs/finals/rubric/impact_feasibility/value_calculator.spec.json` (600 people, 90 min/week, US$31/h,
46 weeks, US$100,000/yr); `value.spec.ts`.

> What's it worth? For a six-hundred-person fintech, if each person saves ninety minutes a week, that's about one
> point three million US dollars a year. It's an estimate, not a measurement. And it pays for itself at about
> seven minutes a week.

### 16 · Close
**Cuts:** 11 s in every cut.
**On screen:** the app's close slide (`/present/close`): "Answers you can prove." and the QR code to the repo.
**Caption:** none.
**Proof:** the repo link on the slide is `https://github.com/IshaanSirbhaiya/Tencent` (public).

> Walnut DataBridge. Answers you can prove. We'd love one pilot team at one regulated company. Thanks for
> watching!

---

## Which scenes the short cut (about 6:04) drops

06b live growth, 19 add a file, 21 tour / dictation / value calculator / presenter, 20 Miora, and the three slots
S1 to S3. It also uses the shorter spoken lines (no **[MAIN]** or **[FULL]** lines) and shorter holds. Core scenes
02, 03, 04, 05, 08 and the slides stay in every cut.

## Lines that change if Hy3 goes live

| Scene | Line | Also re-shoot |
|---|---|---|
| 13 | "Until it is switched on, answers are quoted from the sources…" becomes "The answers you saw were written by Hy3…" | Scene 13 (the badge becomes "Live models") |
| 02, 04 | No spoken change, but the answer label on screen changes from "Quoted from sources (no model)" to the model's name | Scenes 02 and 04 |
| 05 | No spoken change ("before anything is composed" is true either way); the Model card shows Hy3 instead of "Extractive (no model)" | Scene 05 |

Then run `python docs/finals/video/assemble_finals.py --hy3` so the cue sheet uses the Hy3 lines.

## Truth checks before recording the voice

| Line | Source | If it changed |
|---|---|---|
| 7,374 / 0 leaks; 400 / 0 differences; 1,152 injection attempts | `databridge/apps/internal_brain_web/public/eval_summary.json` (run 29 to 30 Oct) | Read the new numbers; the slide updates itself; `assemble_finals.py --self-test` fails if the script's words no longer match |
| 1,131 backend, 166 browser tests | `eval_summary.json` `tests.backend` / `tests.e2e`; `docs/finals/TEST_REPORT.md` (cloud run 30 Oct, commit 831297e: 1,131 passed; e2e 166 passed, 0 failed, 41 skipped) | Say the new counts; the scene 14 caption reads the file |
| Nine CodeBuddy sessions | `video_constants.json` `codebuddy_sessions`; `docs/tencent/evidence/` | Edit the constant and the spoken word |
| Graph and engine counts | Not spoken (the world is growing); the screen shows them | Nothing to change |
| US$1.3M, 7 minutes | `docs/finals/rubric/impact_feasibility/value_calculator.spec.json` | Match the slide |
| "Tipping off" line | Run sheet, beat 0:15 | Keep the wording as written |

**Fallback:** every scene is its own clip; if one breaks, re-shoot just that one
(`node docs/finals/video/record_finals.cjs 08_audit`, then `python docs/finals/video/assemble_finals.py`).
Never fake a result.
