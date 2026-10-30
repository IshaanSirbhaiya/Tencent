# Finals demo video: script and shot list

**What it is:** the new demo video for the finals build of **Walnut DataBridge** (branch `final_v1.2`). It
replaces the 16 Oct video (https://youtu.be/fr9wqHWsijY, 6:28, made from the old UI) and keeps its pattern
exactly: Ishaan's camera intro, then a narrated screen demo of the real app, then the proof, the business case
and a short close, in the same friendly voice. Source of the pattern: `docs/tencent/VIDEO_SCRIPT.md`.

**Shape (founder's principle, 30 Oct evening).** The **16 Oct video is the core**: its beats, in its order, with
its messages, are the main body, re-shot on the new UI. Everything added since 16 Oct is an **enhancement** that
sits next to the beat it strengthens ("and now it also..."), never a feature tour of its own. Each scene below
names the original beat it carries (`docs/tencent/VIDEO_SCRIPT.md`, scenes 1 to 14). Two original beats were
dropped on 16 Oct's own advice (2 "Why this matters": the camera intro says it; 8 "Access changes": its message,
"checked with the source every time, nothing stale", now rides on live growth). The teammates' features (voice,
just-in-time UI, dots) are TODO placeholders until they are merged. Written for what exists **today**.

**Three cuts from the same takes** (`assemble_finals.py --cut short|main|full`; main is the default):
short about 6:18, **main about 7:56** (under the 8:00 of the 5 to 8 minute range in `docs/tencent/SUBMISSION.md`),
full about 9:43. Each scene below lists its seconds in the three cuts. Narration lines tagged **[MAIN]** are read
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

**Format of each scene.** Cuts (seconds) · Original beat (the 16 Oct scene this carries) · On screen (page,
person, action) · Caption (the small label at the lower left) · Proof (the test or file behind what is claimed) ·
then the voice-over, in `>` lines. Only `>` lines are spoken; `TODO` lines are placeholders and are never read.

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

# Part 1 · Hello, and one question with every sentence sourced (16 Oct beats 1 and 3)

### 00 · Camera intro
**Cuts:** 32 s in every cut.
**Original beat:** the same file as the 16 Oct video.
**On screen:** Ishaan to camera, letterboxed with the navy bars (`submission/05_video/voice/intro_final.mp4`,
reused from the first video, with its own sound).
**Caption:** "Ishaan · Team Walnut" (part of the file).
**Proof:** none needed; it is the same file as the 16 Oct video.

Already recorded. Nothing to read here. (What it says: an AI that can read everything can also leak everything,
so we built one that only answers from what you're allowed to see. "Let me show you.")

### 01 · Title
**Cuts:** 9 s in every cut.
**Original beat:** 1 "Hello" (the name, the challenge, and Tencent CodeBuddy and WorkBuddy named in the first line).
**On screen:** title card: "Walnut DataBridge. Answers you can prove." with the pills "Built with Tencent CodeBuddy"
and "Wired for Tencent Hunyuan (Hy3)" (made by `record_finals.cjs cards`).
**Caption:** none.
**Proof:** the product name and tagline from `docs/finals/DECISIONS.md` and the run sheet; CodeBuddy in
`docs/tencent/DEV_PROCESS.md` (nine sessions); Hunyuan Hy3 first in the model chain (`test_model_config.py`,
`llm.py`), wired through TokenHub, not live as of 30 Oct, which is why the line says "wired for".

> This is Walnut DataBridge, built with Tencent CodeBuddy and wired for Tencent's Hunyuan. Let's start with
> Aspire's own question.

### 04 · One question, every sentence sourced and checked live
**Cuts:** short 30 s · main 34 s · full 38 s.
**Original beat:** 3 "One question, three platforms" (one plain question, one answer, every sentence linked to its
source, only sources the asker can open, each re-checked with the source system). Upgraded: the asker is Rachel
with Aspire's question, the source sheet shows "Checked live at", and (new) the user screens work in 简体中文.
**On screen:** Rachel's own Ask page (`/`, User mode). She clicks her starter question about the Velquarry review.
The live progress line, then the cited answer. She clicks a citation: the source sheet shows the platform, the
version, "Checked live at" and the cited lines highlighted. **Esc**. Then the language menu: 简体中文, and back to
English.
**Caption:** "Rachel · MLRO: her own screen", then "The exact passage, its version, checked live seconds ago", then
"简体中文: the user screens, one click".
**Proof:** `tests/e2e/user.spec.ts` ("ask, staged progress, cited answer, source sheet, Esc returns focus");
`ask.spec.ts`; the live source check is Gate 2 (rule 1). Chinese covers the user screens only: Developer pages stay
English and say so (D-32), which is why the line says "the screens you ask from".

> Rachel is the money-laundering reporting officer at a fintech. She asks Aspire's question: why has Velquarry
> Trading's account been restricted?
> One answer, and every sentence points at a passage she can open right now.
> Click one, and you see the exact lines, the version, and when it was checked with the source: seconds ago.
> **[MAIN]** Anything she cannot open at that moment is left out of the answer.
> And now the screens you ask from work in Chinese too.
> **[FULL]** Developer pages stay in English, and say so.

---

# Part 2 · Who sees what (16 Oct beats 4 to 7, with the Act, Deliverables and tour enhancements)

### 06 · The map of what you can see
**Cuts:** short 20 s · main 20 s · full 26 s.
**Original beat:** 4 "The map of what you can see" (Jordan's map, everything he can open; Ken's is far smaller, and
there is no "plus 843 hidden": for Ken the rest simply doesn't exist). Upgraded: the knowledge graph with Compare
two people. The counts are on screen, not spoken: the world is growing.
**On screen:** Jordan, Developer → **Knowledge graph** (`/dev/graph`): every item he can open. A search for
"migration" opens one item's panel. Then **Compare two people**: Ken's graph beside Jordan's.
**Caption:** "Jordan · backend engineer: everything he can open", "Ken, signed in separately: a small corner, no
greyed-out boxes".
**Proof:** `tests/e2e/graph.spec.ts`, `graph-screens.spec.ts`; the map totals are permission-correct (rule 1).

> Here is everything Jordan, a backend engineer, can open, as one graph, each item checked with its source.
> **[FULL]** Company A is a synthetic company; every name here is made up.
> Compare two people, and Ken, an outside contractor, has a small corner of the company, with no greyed-out
> boxes.
> For Ken, the rest simply isn't there.

### 02 · Same question, two people
**Cuts:** short 36 s · main 38 s · full 43 s.
**Original beat:** 5 "Same question, two people" (Compare, side by side) and 7 "The fintech part" (what can I tell
this customer? Amy gets the approved script, never the case notes). Upgraded: Amy beside Rachel, the MLRO, on one
screen, and the tipping-off point said plainly.
**On screen:** `/compare?a=amy.am&b=mlro.tan`, "Same question · two people". Amy (account manager) left, Rachel
(MLRO) right. The pointer types *"Why has Velquarry Trading's account been restricted, and what should I tell
the customer?"*, presses **Ask both**, then reads along Amy's answer and Rachel's, and hovers a citation.
**Caption:** "Aspire's question · Amy (account manager) and Rachel (MLRO)", then "Amy · account manager: the
approved script, cited, nothing else", then "Rachel · MLRO: the case itself".
**Proof:** `tests/e2e/compare.spec.ts`; the two gates and the constant response shape (repo rules 1 and 4,
`databridge/apps/internal_brain/tests`); the "tipping off" point is stated as in the run sheet
(`docs/finals/rubric/demo_story/RUN_SHEET.md`, beat 0:15). It is a plain-language point, not legal advice.

> Now the same question from two people: Amy, an account manager, left; Rachel, right. And what should I tell
> the customer?
> Amy gets the approved customer script, with its sources, and nothing else.
> Rachel gets the actual case: the flagged transfers, and the review.
> Same system, two different truths.
> In Singapore, telling Amy that a suspicious-transaction report even exists is a criminal offence. So the
> system never does.
> **[MAIN]** The question is word for word the same. Only who is signed in is different.
> **[FULL]** Who you are comes from your signed sign-in, never from the question.

### 03 · Restricted looks exactly like nothing
**Cuts:** short 23 s · main 25 s · full 28 s.
**Original beat:** 6 "Restricted looks exactly like non-existent" (Ken asks about a real restricted file, then a
made-up one; byte-for-byte identical; 400 answers compared, none changes). Upgraded: the fintech example and the
live "Identical responses" marker.
**On screen:** `/compare?mode=person&p=ken.ext`, "Same person · two questions". Ken (outside contractor) asks
about Velquarry on the left and about "Bayfront Spice Traders", a company that does not exist, on the right. Two
refusal cards, then the marker "Identical responses: same text, same sources".
**Caption:** "Ken · outside contractor: a real customer vs a made-up one", then "Identical, word for word:
restricted looks like nothing".
**Proof:** `tests/e2e/compare.spec.ts` and `user.spec.ts` ("restricted and made-up subjects render identically");
`eval_summary.json` `differential`: 400 pairs, 0 mismatches.

> Now Ken on his own. On the left, he asks about Velquarry. On the right, about a company that doesn't exist.
> The answers are identical, word for word. A restricted file looks exactly like nothing.
> **[MAIN]** No count, and no hint that anything is hidden.
> We ran four hundred pairs like this: zero differences.
> **[FULL]** So nobody can find out which customers exist just by asking.

### 17 · And now the answer becomes the reply: Act
**Cuts:** short 18 s · main 22 s · full 26 s.
**Original beat:** enhancement to 7 "The fintech part" ("what can I tell this customer?" now becomes the reply
itself, never sent) and to 3 (Jordan's migration answer becomes a ticket).
**On screen:** Amy (account manager), User mode. She clicks her Velquarry starter (Aspire's question again). Under
the answer, the **Act** row: **Draft reply** opens the reply draft: the approved wording, each sentence from the
cited facts, "not sent". **Esc**. Then, signed in as Jordan (backend engineer) on the same page, his first starter
(the migration status); **Create ticket** opens a ticket proposal, and he presses **Confirm**: the ticket is created,
with a link.
**Caption:** "Amy · account manager: 'what should I tell the customer?' becomes a reply", "A draft reply: the
approved wording, built from the cited facts, never sent", "Jordan · backend engineer: a ticket proposal, nothing is
created until he confirms", "Confirmed: the ticket exists, with a link".
**Proof:** `tests/e2e/act.spec.ts` ("account manager: the reply draft is the approved wording, for review, never
sent"; "engineer: a follow-up ticket is proposed, confirmed and created"), `test_act.py`, `test_act_e2e.py`; D-28 (a
model-written draft falls back to the cited sentences). The scene follows the tests: the draft is Amy's, the ticket
is Jordan's. It writes a ticket into the mock Jira, so it is recorded late.

> And now Amy's answer can become the reply itself. She asks for a draft: the approved wording, built from the
> cited facts, and never sent.
> Jordan's answer offers a ticket, but it's only a proposal until he confirms.
> **[MAIN]** He confirms, and the ticket exists, with a link.
> **[FULL]** If a draft strays from the facts, the cited sentences are used instead.

### 18 · And now a file: Deliverables
**Cuts:** short 10 s · main 15 s · full 20 s.
**Original beat:** enhancement to 3 (the cited answer can also become a table, a chart or a .pptx, every figure
still pointing at its source).
**On screen:** Jordan, the same kind of answer. Under it, the **Deliverables** row: **Table** opens a table, each
figure pointing at its sources; the pointer rests on **.pptx** (hovered, not downloaded); then the **Chart** tab.
**Caption:** "Jordan · backend engineer: an answer becomes a file", "A table whose figures point at their sources",
"A .pptx of it, built with no model".
**Proof:** `test_deliverables.py`, `test_deliverables_verify.py`, `tests/e2e/deliverables.spec.ts`; D-29 (Slides
`.pptx` for ready tables and charts, with no model; a Summary kind is not built).

> Answers can also become files: a table whose figures point at their sources, and a chart from the same facts.
> **[MAIN]** Or a PowerPoint file, built without a model.
> **[FULL]** The table is built straight from the cited facts, with no model.

### 21 · New here: a tour, dictation, the value calculator
**Cuts:** not in the short cut · main 12 s · full 16 s.
**Original beat:** 5b "A guided tour for anyone new". Upgraded: dictation in the composer and the accessibility
settings as a caption; a short look at the value calculator.
**On screen:** Jordan, as someone new. One beat on the Ask page: the first-visit tour offer, then the microphone
button in the composer, hovered but not pressed, while a caption names the accessibility settings. Then a short
look at `/value`, the value calculator, carried by its caption. (The stage presenter is no longer in the video: it
is a stage tool, and the judges see it live on 3 Nov.)
**Caption:** "New here? A guided tour: a User track and a Developer track", "Dictate a question: it types into the
box; nothing is asked until Ask is pressed", "Text size, high contrast and reduce motion are settings; accessibility
checks run in the browser tests", "The value calculator: every input editable, the result an estimate".
**Proof:** `tests/e2e/tour.spec.ts`; `voice.spec.ts` (text goes in the box and nothing is asked, Esc stops, button
hidden without the speech API); the settings strings in `lib/i18n/en.ts` (`settings.textSize`, `highContrast`,
`reduceMotion`); axe runs in 13 of the 30 `tests/e2e/*.spec.ts` files, which is why the caption says "in the browser
tests", not "in every test" (rubric criterion 8); `value.spec.ts`.

> And if you're new here, there's a guided tour, and you can dictate a question; nothing is sent until you press
> Ask.
> **[FULL]** The value calculator lets you change every input; the result is an estimate.

---

# Part 3 · Nothing stale (16 Oct beat 8, carried by live growth and uploads)

### 06b · Live growth: the brain keeps up
**Cuts:** not in the short cut · main 22 s · full 28 s.
**Original beat:** 8 "Access changes, answers change" (checked with the source every time; nothing stale). Upgraded:
instead of removing Jordan from a channel, Jordan posts a note and it is in his graph at once, checked with Slack;
Ken's side shows nothing.
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
> A moment later Jordan asks about the note, and the answer quotes it, with its source. Nothing stale.
> **[FULL]** The post is checked with Slack and written to the audit log, like everything else.

### 19 · Add a file
**Cuts:** not in the short cut · main 10 s · full 15 s.
**Original beat:** enhancement to 8 (new content goes through the same checks and into the same log).
**On screen:** `/dev/graph?add=1`, Jordan. In the Add sheet, the file tab: a sample file is chosen, a folder is
picked from the list, **Upload**; the status line confirms.
**Caption:** "Add a file: it goes into a folder, through the same checks", "Uploaded and written to the audit log
like everything else".
**Proof:** `test_uploads.py`, `test_upload_folders.py`, `test_extract_files.py`; Opus L5 (successful source writes
are ledgered, round-3 dispositions). Writes a file into the mock Drive.

> New files can be added too, into a folder, and the upload is written to the audit log.
> **[FULL]** PDF, Office and text files are accepted.

---

# Part 4 · The compliance side (16 Oct beat 9, with the journey, analytics and Trust centre enhancements)

### 05 · Developer mode: one request, end to end
**Cuts:** short 30 s · main 35 s · full 40 s.
**Original beat:** 9 "The audit trail", first half (Maya sees each question, what was considered, what was allowed
or denied and why, and which model answered; she cannot read answers by default). Upgraded: the request journey
with real milliseconds and the three sealed records.
**On screen:** signed in as Maya (compliance officer), User mode. She switches to **Developer** at the top left,
opens **Journey** (`/dev/journey`) and picks Rachel's request from Recent requests. The waterfall with real
milliseconds, the three ledger seals, then the "Considered → Gate 2" table.
**Caption:** "Maya · compliance officer: Developer mode", "Rachel's request: every stage, real milliseconds",
"Three sealed, chained records, before she saw a word", "Every document considered: the verdict and the reason,
never the answer".
**Proof:** the journey page tests in `tests/e2e/` (`audit-trust.spec.ts`, `user.spec.ts` receipt flow) and the
write-ahead ledger rule 3 (`test_audit_ledger.py`). Maya sees verdicts and reasons, never the answer itself.

> Now the compliance side, as Maya.
> **[MAIN]** The app has two modes: User, for asking, and Developer, for seeing how an answer was made.
> This is Rachel's request, end to end.
> Each bar is a real timing: two permission checks first, then the answer is put together.
> **[FULL]** Those timings are measured, not drawn.
> These three records were sealed and chained before Rachel saw a word.
> And below, every document that was considered, with its verdict and the reason.
> Maya sees every decision. She never sees the answer itself.

### 08 · The sealed audit log, and the tamper demo
**Cuts:** short 40 s · main 43 s · full 50 s.
**Original beat:** 9 "The audit trail", second half (plain-English search, the chained and sealed log, Verify, and
"if someone edits a single line on disk, verification catches it"). Upgraded: the tamper demo now runs live on a
copy, and Verify points at the exact entry.
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
> That's what we mean by answers you can prove.

### 07 · Analytics
**Cuts:** short 12 s · main 12 s · full 15 s.
**Original beat:** enhancement to 9 (the same verified log, summed up).
**On screen:** Maya, **Analytics** (`/dev/analytics`): requests, answer rate, latency, Gate-2 deny rate; then
**Live only**.
**Caption:** "Maya · analytics, computed from the verified ledger", "Live only: seeded history left out".
**Proof:** `tests/e2e/analytics.spec.ts`; seeded history is labelled by the page.

> Maya's analytics come straight from the verified log: how many questions, how many answered, how fast.
> Seeded history is labelled, and one click leaves it out.
> **[FULL]** And how often the live check said no.

### 09 · The Trust centre
**Cuts:** 14 s in every cut.
**Original beat:** enhancement to 9 and to 12 "How it fits together" (the controls behind the demo, each proved by
a test).
**On screen:** Maya, **Trust centre** (`/dev/trust`): seven controls, each with the frameworks it maps to and the
tests that prove it.
**Caption:** "Trust centre: seven controls, each proved by a named test".
**Proof:** `tests/e2e/audit-trust.spec.ts`; the page lists the test behind each control.

> The Trust centre lists the seven controls behind all of this.
> Each one is enforced in code, mapped to Singapore's AI and data rules, and proved by a named test.

---

# Part 5 · WorkBuddy, what's underneath, how it fits together (16 Oct beats 10 to 12)

### 12 · Ask from Tencent WorkBuddy
**Cuts:** short 13 s · main 15 s · full 18 s.
**Original beat:** 10 "Ask from WorkBuddy" (people don't want another app; each connector carries its own signed
token, so the AI in the middle can't decide who you are). Same captures as the first video.
**On screen:** two real WorkBuddy window captures (15 Oct): the three DataBridge connectors, then one question
through Jordan's and Ken's connectors with two different answers.
**Caption:** "Tencent WorkBuddy · real window capture (15 Oct)", "One question, Jordan's and Ken's connectors: two
answers".
**Proof:** `submission/02_workbuddy/`, `test_mcp_untrusted_frame.py` (identity comes only from the signed
token, never from the agent's arguments). D-30 changed the compliance connector after these captures, so nothing
is said about it over these stills (READINESS_30OCT.md, scene 12).

> Nobody wants another app, so it also lives inside Tencent WorkBuddy. Each connector carries its own signed
> token, so Jordan's connector gets Jordan's answer, and Ken's gets Ken's.
> **[MAIN]** Same gates, same audit log.
> **[FULL]** The agent never decides who you are.

### 10 · The Walnut engine underneath
**Cuts:** short 20 s · main 18 s · full 25 s.
**Original beat:** 11 "What's underneath" (the Walnut evidence engine; every fact has a source; the time slider;
follow a value to the exact row). Same lines, re-shot inside DataBridge.
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

### 13 · How it fits together, and Tencent Hy3
**Cuts:** short 14 s · main 14 s · full 17 s.
**Original beat:** 12 "How it fits together" (two checks before the model, a grounding check after, an audit record
written before anything is shown; Hy3 through TokenHub, or quoted answers until then). Upgraded: the live model
chain on Mission control instead of a card.
**On screen:** Maya, **Mission control** (`/dev`) → the model chain: Hy3, then DeepSeek, then the quoted
fallback. **[otherwise]** The badge reads "Extractive only".
**Caption:** "Tencent Hy3 via TokenHub → DeepSeek → quoted fallback", then a line that follows the live status
("Hy3 not switched on yet: answers are quoted from the sources").
**Proof:** the model-chain tests (`test_model_config.py`); the recorder reads `/ib/status`, and `FINALS_HY3=0|1`
makes it refuse a take that disagrees. The frame line is rules 1, 3 and 5 of `CLAUDE.md`.

> **[FULL]** Two checks before any model, a grounding check after, the record written first.
> Tencent Hy3 is wired in through TokenHub, with a safe fallback.
> **[otherwise]** Until it is switched on, answers are quoted from the sources, through the same checks.
> **[ONLY IF HY3 LIVE]** The answers you saw were written by Hy3, inside exactly the same checks.

---

# Part 6 · The teammates' features (placeholders until the merge)

### S1 · Voice
**Cuts:** not in the short cut · main 8 s · full 20 s. **Left out until `clips/S1_voice.webm` exists.**
**Original beat:** enhancement (attach to the beat it strengthens once its page is known).
**On screen:** TODO(founder): page, person and action after the merge.
**Caption:** TODO.
**Proof:** TODO: the test or file behind it (rule 8).

> TODO(founder): the voice-over line for the voice feature, after the merge.

### S2 · Just-in-time UI
**Cuts:** not in the short cut · main 8 s · full 20 s. **Left out until `clips/S2_jit_ui.webm` exists.**
**Original beat:** enhancement.
**On screen:** TODO(founder).
**Caption:** TODO.
**Proof:** TODO.

> TODO(founder): the voice-over line for just-in-time UI, after the merge.

### S3 · Dots
**Cuts:** not in the short cut · main 6 s · full 15 s. **Left out until `clips/S3_dots.webm` exists.**
**Original beat:** enhancement.
**On screen:** TODO(founder).
**Caption:** TODO.
**Proof:** TODO.

> TODO(founder): the voice-over line for the dots, after the merge.

---

# Part 7 · The numbers, how we built it, thanks (16 Oct beats 13 and 14)

### 14 · The proof
**Cuts:** 20 s in every cut.
**Original beat:** 13 "The numbers" (we attacked it on purpose: adversarial requests, zero leaks; paired answers,
zero differences). Upgraded: the app's own proof slide, the 30 Oct numbers and the test counts.
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
**Cuts:** 21 s in every cut.
**Original beat:** new (the business case; the run sheet's business beat).
**On screen:** the app's business slide (`/present/business`): about US$1.3M a year (estimate), break-even about 7
minutes per person per week, and the inputs.
**Caption:** "Target host: Tencent Cloud. One-command deploy kit in the repo; no live instance yet" (a pill from
the assembler, late in the slide; rubric criterion 5).
**Proof:** `docs/finals/rubric/impact_feasibility/value_calculator.spec.json` (600 people, 90 min/week, US$31/h,
46 weeks, US$100,000/yr); `value.spec.ts`; the host line is the run sheet's (3:50) and `rubric/README.md` row 5
(deploy kit, no live instance).

> What's it worth? For a six-hundred-person fintech, if each person saves ninety minutes a week, that's about one
> point three million US dollars a year. It's an estimate, not a measurement. And it pays for itself at about
> seven minutes a week.

### 11 · Built with Tencent CodeBuddy
**Cuts:** short 10 s · main 12 s · full 14 s.
**Original beat:** 14 "How we built it, and thanks" (CodeBuddy mapped the trust boundaries, built the WorkBuddy
connector, wrote tests, reviewed security and found real bugs; every transcript in the repo). Upgraded: nine
sessions, not six.
**On screen:** card: one of the nine CodeBuddy session transcripts (the security review), and what CodeBuddy
built.
**Caption:** none (the card says "Nine CodeBuddy sessions. Every transcript is in the repo").
**Proof:** `docs/tencent/evidence/` (CB1 to CB9), `docs/tencent/DEV_PROCESS.md`; the count is in
`video_constants.json`.

> We built this with Tencent CodeBuddy, nine sessions in all.
> **[MAIN]** It built the WorkBuddy connectors, wrote tests, and its security review found real bugs.
> **[FULL]** All in the repo.

### 20 · Made with Miora
**Cuts:** not in the short cut · main 8 s · full 10 s.
**Original beat:** enhancement to 14 (how we built it).
**On screen:** card: the cover art and the Miora session from `submission/06_cover/`.
**Caption:** none (the card says "Made in Miora from our written brief").
**Proof:** `submission/06_cover/miora_session1.png`, `miora_session2.png`, `docs/finals/COVER_MIORA_PROMPT.md`.

> The cover art was made in Miora, from a written brief.
> **[FULL]** The prompts are in the repo.

### 16 · Close
**Cuts:** 13 s in every cut.
**Original beat:** 14, the thanks. Upgraded: the tagline and the ask.
**On screen:** the app's close slide (`/present/close`): "Answers you can prove." and the QR code to the repo.
**Caption:** none.
**Proof:** the repo link on the slide is `https://github.com/IshaanSirbhaiya/Tencent` (public).

> Every company's AI will be asked to prove itself. Walnut DataBridge. Answers you can prove. We'd love one
> pilot team at one regulated company. Thanks for watching!

---

## The 16 Oct spine, beat by beat

| 16 Oct beat | Message | Now | Enhancement attached |
|---|---|---|---|
| 1 Hello | name, challenge, Tencent named | 00, 01 | the tagline |
| 2 Why this matters | dropped on 16 Oct's own advice | (the camera intro says it) | |
| 3 One question, three platforms | one answer, every sentence sourced, only what you can open | 04 | 简体中文 (04), Act (17), Deliverables (18) |
| 4 The map | Jordan's map vs Ken's; no "hidden" count | 06 | Compare two people |
| 5 Same question, two people | Compare side by side | 02 | Amy and Rachel |
| 5b Guided tour | for anyone new | 21 | dictation, accessibility caption, value calculator |
| 6 Restricted looks like non-existent | identical, 400 pairs | 03 | the live marker |
| 7 The fintech part | what can I tell this customer | 02, 17 | the draft reply |
| 8 Access changes | checked with the source every time; nothing stale | 06b, 19 | live growth, uploads |
| 9 The audit trail | Maya, plain English, Verify, tamper | 05, 08 | the journey, analytics (07), Trust centre (09) |
| 10 WorkBuddy | another front door, signed tokens | 12 | |
| 11 What's underneath | the Walnut engine | 10 | |
| 12 How it fits together | two checks, grounding, audit; Hy3 | 13 | the live model chain |
| 13 The numbers | attacked on purpose | 14 | the business case (15) |
| 14 Built with, thanks | CodeBuddy; thanks | 11, 20, 16 | Miora; the ask |

## Which scenes the short cut (about 6:18) drops

06b live growth, 19 add a file, 21 new here (tour / dictation / value calculator), 20 Miora, and the three slots
S1 to S3. It also uses the shorter spoken lines (no **[MAIN]** or **[FULL]** lines) and shorter holds. The spine
(04, 06, 02, 03, 05, 08, 12, 10, 13, 14, 11, 16) stays in every cut.

## Lines that change if Hy3 goes live

| Scene | Line | Also re-shoot |
|---|---|---|
| 13 | "Until it is switched on, answers are quoted from the sources…" becomes "The answers you saw were written by Hy3…" | Scene 13 (the badge becomes "Live models") |
| 02, 04 | No spoken change, but the answer label on screen changes from "Quoted from sources (no model)" to the model's name | Scenes 02 and 04 |
| 05 | No spoken change ("two permission checks first" is true either way); the Model card shows Hy3 instead of "Extractive (no model)" | Scene 05 |

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
| "Wired for Tencent's Hunyuan" (01), "wired in through TokenHub" (13) | `STATUS.md`, `/ib/status`; Hy3 not live as of 30 Oct | If Hy3 answers live, say "running on Tencent's Hunyuan" in 01 and read the [ONLY IF HY3 LIVE] line in 13 |
| "Target host: Tencent Cloud … no live instance yet" (15, pill) | run sheet 3:50; `rubric/README.md` row 5 | If an instance goes live, change the pill text in `assemble_finals.py` |

**Fallback:** every scene is its own clip; if one breaks, re-shoot just that one
(`node docs/finals/video/record_finals.cjs 08_audit`, then `python docs/finals/video/assemble_finals.py`).
Never fake a result.
