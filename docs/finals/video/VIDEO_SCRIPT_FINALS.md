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
short about 6:31, **main about 7:58** (under the 8:00 of the 5 to 8 minute range in `docs/tencent/SUBMISSION.md`),
full about 9:52 (planned slot lengths, every teammate slot counted; `--self-test` prints them). Each scene below lists its seconds in the three cuts. Narration lines tagged **[MAIN]** are read
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

**Model lines (founder, 31 Oct: the stand-in model is ON for the demo and the video).** Hy3 is wired in through
TokenHub but not live (as of 30 Oct it has not answered live). With the stand-in on (D-41, its switch set in `.env.ib.local`), the app
labels every answer "AI-generated from your cited sources": the model sees only passages that passed Gate 1 and
Gate 2, and the grounding check runs on what it writes. Say it that way. Never say an answer was "quoted from the
sources (no model)" (that is the model-OFF label), never say Hy3 or Hunyuan answered, and never name the stand-in's
maker or any other model: on screen it is only "AI model". Lines marked **[IF MODEL OFF]** are kept for a model-off
take only (`assemble_finals.py --model-off`); **[IF MODEL ON]** lines are the default. A line marked
**[ONLY IF HY3 LIVE]** is read only after Hy3 has answered live in this app; otherwise read the **[otherwise]**
line. (The assembler also still accepts the old spelling "[if Hy3 is on]".) Keep each marked line on one line:
`assemble_finals.py` reads the script line by line. Scenes 02, 04, 05, 13 and 06b show the answer label, so record
them with the stand-in on (`FINALS_MODEL=1` makes the recorder refuse a scene-13 take that disagrees).

## What each piece does (every line must agree with this)

| Role | Pieces | How the video may describe it |
|---|---|---|
| **Used to make every answer** | the signed token (who you are); the search index; Gate 1, the index permission filter; the connectors for Slack, Confluence, Jira and Drive (Gate 2: each source is read through its own connector, with permissions checked live); the model step (today: the stand-in, "AI-generated from your cited sources", only from passages that passed both gates); the Walnut engine's grounding wall and source pointers; the write-ahead sealed ledger | "each source is read through its own connector, with permissions checked live"; "two permission checks first"; "the record is sealed before you see a word" |
| **Another way to ask, through the same pipeline** | WorkBuddy (an MCP agent: same gates, written to the same sealed log); Compare | "the same gates, the same sealed log" |
| **Views that do not answer** | the knowledge graph (a map of what each person can open, built from the same permission checks); the Walnut engine graph (Dev → Brain); the journey; audit; analytics; the Trust centre | "a map of what each person can open"; the engine graph is "shown, not used to answer". Never "how the answer is found" |
| **On top of an answer** | Act; Deliverables; the page beside the chat (slot S2) | "the answer can become a reply / a file / a page" |
| **The wider platform, not used to answer** | the original POC's adapter agents and adapter builder, the federated engine, the original Walnut screens, the engine gateway | scene 10 only, captioned "the platform", never "answering this question" |

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
**Cuts:** short 36 s · main 37 s · full 40 s.
**Original beat:** 3 "One question, three platforms" (one plain question, one answer, every sentence linked to its
source, only sources the asker can open, each re-checked with the source system). Upgraded: the asker is Rachel
with Aspire's question, the source sheet shows "Checked live at", and (new) the user screens work in 简体中文.
The source sheet carries the pipeline line: each source is read through its own connector (Slack, Confluence,
Jira, Drive), with permissions checked live (Gate 2).
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
> Click one, and you see the exact lines, the version, and when it was checked: seconds ago.
> Each source is read through its own connector, with her permissions checked live.
> **[MAIN]** Anything she cannot open at that moment is left out of the answer.
> And now the screens you ask from work in Chinese too.
> **[FULL]** Developer pages stay in English, and say so.

---

# Part 2 · Who sees what (16 Oct beats 4 to 7, with the Act, Deliverables and tour enhancements)

### 06 · The map of what you can see
**Cuts:** short 20 s · main 20 s · full 26 s.
**Original beat:** 4 "The map of what you can see" (Jordan's map, everything he can open; Ken's is far smaller, and
there is no "plus 843 hidden": for Ken the rest simply doesn't exist). Upgraded: the knowledge graph with Compare
two people. The counts are on screen, not spoken: the world is growing. The graph is a **view**: a map of what
each person can open, built from the same permission checks as the answers. It is never described as how an
answer is found.
**On screen:** Jordan, Developer → **Knowledge graph** (`/dev/graph`): every item he can open. A search for
"migration" opens one item's panel. Then **Compare two people**: Ken's graph beside Jordan's.
**Caption:** "Jordan · backend engineer: a map of what he can open", "Ken, signed in separately: a small corner,
no greyed-out boxes".
**Proof:** `tests/e2e/graph.spec.ts`, `graph-screens.spec.ts`; the map totals are permission-correct (rule 1).

> Here is a map of what Jordan, a backend engineer, can open, built from the same permission checks.
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
**Cuts:** short 10 s · main 13 s · full 18 s.
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
> **[FULL]** The table's figures come straight from the cited facts.

### 21 · New here: a tour, dictation, the value calculator
**Cuts:** not in the short cut · main 10 s · full 15 s.
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
**Cuts:** not in the short cut · main 9 s · full 13 s.
**Original beat:** enhancement to 8 (new content goes through the same checks and into the same log).
**On screen:** `/dev/graph?add=1`, Leo (product and operations, who may add files to "Pricing & Offers"; Jordan may add to no
folder). In the Add sheet, the file tab: a sample file is chosen, the folder **Pricing & Offers** is picked from the
list by name (never Vendor share, which Ken can read), **Upload**; the status line confirms.
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
> Each bar is a real timing: the index filter and the live source checks first, then the answer.
> **[FULL]** Those timings are measured, not drawn.
> These three records were sealed and chained before Rachel saw a word.
> And below, every document that was considered, with its verdict and the reason.
> Maya sees every decision. She never sees the answer itself.

### 08 · The sealed audit log, and the tamper demo
**Cuts:** short 40 s · main 42 s · full 50 s.
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
**Cuts:** short 13 s · main 13 s · full 14 s.
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
**Cuts:** short 15 s · main 16 s · full 19 s.
**Original beat:** 10 "Ask from WorkBuddy" (people don't want another app; each connector carries its own signed
token, so the AI in the middle can't decide who you are). Shown as in the first video: real WorkBuddy window
stills, the connector list first, then one question through two people's connectors, with small captions.
**On screen:** two real WorkBuddy window captures (15 Oct): the three DataBridge connectors, then one question
through Jordan's and Ken's connectors with two different answers. **TODO(laptop): re-capture both stills (or a
short clip) in WorkBuddy on the laptop, same two shots, and save them over `submission/02_workbuddy/workbuddy_mcp_panel.png`
and `workbuddy_compare.png`; until then the 15 Oct stills are used.** WorkBuddy is another way to ask through the
same pipeline: an MCP agent, the same two gates, every question written to the same sealed log.
**Caption:** "Tencent WorkBuddy · real window capture (15 Oct)" (change the date when re-captured), "One question,
Jordan's and Ken's connectors: two answers".
**Proof:** `submission/02_workbuddy/`, `databridge/apps/mcp_server/`, `test_mcp_untrusted_frame.py` (identity comes
only from the signed token, never from the agent's arguments). D-30 changed the compliance connector after these
captures, so nothing is said about it over these stills (READINESS_30OCT.md, scene 12).

> It plugs into Tencent WorkBuddy, so people can ask from where they already work.
> It's an MCP agent: each connector carries one person's signed token, so Jordan's gets Jordan's answer.
> **[MAIN]** Same gates, same sealed log.
> **[FULL]** The agent never decides who you are.

### 10 · What's underneath: the platform, and the Walnut engine
**Cuts:** short 26 s · main 26 s · full 36 s (two clips: the platform, then the engine).
**Original beat:** 11 "What's underneath". Shown as in the first video: a screen recording of the ORIGINAL Walnut
screens (`databridge/apps/frontend` on :3300, the engine gateway on :8300) running on Company A, one quick shot per
screen, small captions, the pointer resting on each; then (new) the same engine inside DataBridge.
**On screen, clip 1 (`10a_platform`, the platform):** the original screens, started with
`bash databridge/scripts/run_walnut_original.sh`: the **Builder**, "Adapter agents" tab (an example request fills
the box; nothing is submitted), then its "Agents" tab; **Connectors** (the source list); **Data** (the data tools,
convert); the **engine gateway** on :8300 (its API page, the federated engine's `/federated/ask` route); the
**knowledge graph** of Company A (`/graph`). Every caption starts "The platform:" and none says these answer a
question: the Internal Brain's answers never go through the adapter agents, the builder or the federated engine.
**On screen, clip 2 (`10_engine`, the engine graph inside DataBridge):** Jordan, **Walnut engine** (`/dev/brain`):
the snapshot counts (on screen only), **Company brain** with the time slider dragged back, then forward;
**Provenance**: the DB Migration Plan, one value traced to its row and file. This graph is shown, not used to answer.
**Caption:** clip 1: "The platform: agents that build new adapters", "The platform: agents, built from a plain
request", "The platform: a connector for each source", "The platform: data tools", "The platform: the engine
gateway, with its federated engine across systems", "The platform: the evidence graph of Company A". Clip 2: "The
Walnut engine's graph, inside DataBridge: shown here, not used to answer", "Rewind the company knowledge in time",
"One value traced to the exact row in the exact file".
**Proof:** `databridge/apps/frontend/README.md` (the original screens on Company A; `tests/showcase/capture_company_a.cjs`),
`databridge/apps/frontend/e2e/builder.spec.ts`, `data.spec.ts`, `sources.spec.ts`; the gateway's routes
(`apps/gateway/databridge_gateway/federated_routes.py`); `tests/e2e/engine.spec.ts`, `engine-proxy.spec.ts`. What the
Internal Brain uses from the engine: the grounding wall, the source pointers and the sealed audit ledger, imported
unchanged (`docs/tencent/DEV_PROCESS.md`, "Foundation"). **Unrecorded:** clip 1 needs the original screens running;
record it with `node docs/finals/video/record_finals.cjs 10a_platform`.

> We built the whole platform: connectors to every source, AI agents that build new adapters, a federated engine
> across systems, and an evidence graph.
> The Internal Brain runs on its core: the grounding wall and the sealed audit trail.
> Inside DataBridge, Jordan can look at the engine's own graph. It's shown here; it isn't used to answer.
> **[FULL]** Drag the time slider, and the company's knowledge rewinds to any past date.
> **[FULL]** And any value can be traced to the exact row in the exact file.

### 13 · How it fits together, and Tencent Hy3
**Cuts:** short 15 s · main 15 s · full 19 s (the model chain, then the deploy kit card).
**Original beat:** 12 "How it fits together" (two checks before the model, a grounding check after, an audit record
written before anything is shown; Hy3 through TokenHub, or quoted answers until then). Upgraded: the live model
chain on Mission control instead of a card, then a still card of the Tencent Cloud deploy kit in the same card
style as the first video's.
**On screen:** Maya, **Mission control** (`/dev`) → the model chain: Hy3 through TokenHub first, then the fallbacks
(the stand-in shows only as "AI model", then the quoted answer). Record with the stand-in on; the chain on screen
must name no third-party model, and the recorder refuses a take whose chain does.
**[IF MODEL OFF]** take: the badge reads "Extractive only". Then the card `13b_deploy.png`
(`record_finals.cjs cards`): the Lighthouse deploy kit in `deploy/lighthouse/` (its files and the one-VM diagram),
"written; no live instance yet".
**Caption:** "The model chain: Tencent Hy3 via TokenHub first, then the fallbacks", then a line that follows the
live status ("Hy3 not switched on yet: answers are AI-generated from the cited sources, through the same checks";
model-off take: "…answers are quoted from the sources"). The card says "Packaged for Tencent Cloud Lighthouse".
**Proof:** the model-chain tests (`test_model_config.py`); the recorder reads `/ib/status`, and `FINALS_HY3=0|1`
makes it refuse a take that disagrees (`FINALS_MODEL=0|1` the same for the stand-in). The frame line is rules 1, 3 and 5 of `CLAUDE.md`. The deploy kit:
`deploy/lighthouse/README.md` (status: written, no live instance).

> **[FULL]** Two checks before any model, a grounding check after, the record written first.
> **[otherwise]** And it's wired for Tencent Hunyuan through TokenHub, packaged to deploy on Tencent Cloud.
> **[ONLY IF HY3 LIVE]** And it runs on Tencent Hunyuan through TokenHub, packaged to deploy on Tencent Cloud.
> **[otherwise]** **[IF MODEL ON]** Until Hy3 is switched on, answers are AI-generated from your cited sources, through the same checks.
> **[otherwise]** **[IF MODEL OFF]** Until Hy3 is switched on, answers are quoted from the sources, through the same checks.

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
**Status:** merged into `final_v1.4` from `final_mehulupdates` (980b17b), WIP and not verified
(`docs/finals/MERGE_ANALYSIS_final_mehulupdates.md`). Record only after that branch is verified.
**Original beat:** enhancement to 3 (the cited answer can also open as a page beside the chat, built only from
that answer's sources).
**On screen:** Jordan, the same kind of answer. **Open as page** under it: the page opens on the right and the
sidebar shrinks to icons. In the box under the page, type "make the table black" and press **Update**; the page
is rebuilt and only its layout changes.
**Caption:** "Jordan · backend engineer: an answer becomes a page", "Built only from this answer's sources",
"Change the layout in plain words".
**Proof:** `databridge_ib/views/` (`pagecheck.py`, `service.py`), `tests/test_views.py`,
`tests/test_views_composer.py`, `tests/e2e/views.spec.ts`, `tests/e2e/tour.spec.ts` (the page steps). Say nothing
about which model laid out the page: the badge and the narration stay neutral (QA_BANK Q51).

> And now an answer can also open as a page beside the chat, built only from that answer's sources and checked again.
> **[FULL]** Ask for a change in plain words, and only the layout changes; the words and numbers stay those of the sources.

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
400 paired questions, 0 hard failures in 1,152 prompt-injection attempts. Label: 1,278 backend + 185 browser tests
(from `eval_summary.json`: 1,278 backend tests collected, 1,274 pass and 4 are skipped; 42 browser tests are skipped, so say "none failing", not "all passing").
**Caption:** "Plus 1,278 backend and 185 browser tests, none failing".
**Proof:** `databridge/apps/internal_brain_web/public/eval_summary.json` (`requests`, `leaks`, `differential`,
`injection`, `tests`), `databridge/evals/internal_brain/RESULTS.md`, `docs/finals/TEST_REPORT.md`.

> We didn't just demo it, we attacked it. Seven thousand three hundred and seventy-four adversarial requests:
> zero leaks. Four hundred paired questions: zero differences. And one thousand two hundred and seventy-eight backend
> tests, plus one hundred and eighty-five browser tests, none failing.

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
**Cuts:** short 10 s · main 11 s · full 14 s.
**Original beat:** 14 "How we built it, and thanks" (CodeBuddy mapped the trust boundaries, built the WorkBuddy
connector, wrote tests, reviewed security and found real bugs; every transcript in the repo). Shown as in the first
video: one still card, no screen recording. Upgraded: nine sessions, not six, and the session evidence on the card.
**On screen:** card: a CodeBuddy session screenshot from `submission/01_codebuddy/` (CB7, the finals security
review, beside CB5, the first one), and what CodeBuddy built. The full transcripts are also in
`submission/01_codebuddy/conversation_history_codebuddy_workbuddy_miora.pdf`.
**Caption:** none (the card says "Nine CodeBuddy sessions. Every transcript is in the repo").
**Proof:** `README.md` ("CodeBuddy | Nine build sessions (CB1–CB9)"), `docs/tencent/DEV_PROCESS.md` (two security
reviews by CodeBuddy: CB5, 7 findings, fixed in CB6 and by Claude, `SECURITY.md` §10; CB7 on the finals backend,
F7 "Real, small", fixed with tests, `docs/tencent/reviews/codebuddy_finals_review.md`), the transcripts in
`docs/tencent/evidence/` (`codebuddy_cb1.jsonl` to `codebuddy_cb9.jsonl`), screenshots in `submission/01_codebuddy/`;
the count is in `video_constants.json`.

> We built it with Tencent CodeBuddy: nine recorded sessions, including security reviews that found real bugs.
> **[MAIN]** It also built the WorkBuddy connectors and wrote tests.
> **[FULL]** Every transcript is in the repo.

### 20 · Made with Tencent Miora
**Cuts:** not in the short cut · main 5 s · full 8 s.
**Original beat:** enhancement to 14 (how we built it). One still card, as in the first video.
**On screen:** card: the finals cover (`submission/06_cover/finals/cover_finals_1920x1080.png`, Miora output 2,
the chosen one) beside the Miora session screenshot (`miora_session_finals.png`) as the evidence.
**Caption:** none (the card says "Designed in Tencent Miora from our written brief").
**Proof:** `submission/06_cover/finals/README.md` ("Finals cover (made in Miora, 30 Oct 2026)"; the session
screenshot with the prompt; both outputs; Miora's "Generated by Miora" mark kept on the image),
`docs/finals/COVER_MIORA_PROMPT.md` (Miora is a Tencent tool).

> The cover was designed in Tencent Miora.
> **[FULL]** The prompt and both results are in the repo.

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
| 4 The map | Jordan's map vs Ken's; no "hidden" count | 06 (a map of what each person can open: a view, not how answers are found) | Compare two people |
| 5 Same question, two people | Compare side by side | 02 | Amy and Rachel |
| 5b Guided tour | for anyone new | 21 | dictation, accessibility caption, value calculator |
| 6 Restricted looks like non-existent | identical, 400 pairs | 03 | the live marker |
| 7 The fintech part | what can I tell this customer | 02, 17 | the draft reply |
| 8 Access changes | checked with the source every time; nothing stale | 06b, 19 | live growth, uploads |
| 9 The audit trail | Maya, plain English, Verify, tamper | 05, 08 | the journey, analytics (07), Trust centre (09) |
| 10 WorkBuddy | another front door, signed tokens | 12 (same stills pattern; re-capture on the laptop) | an MCP agent: same gates, same sealed log |
| 11 What's underneath | the Walnut engine (a recording of the original Walnut screens) | 10 (the same kind of recording: the original screens as "the platform", then the engine graph inside DataBridge, shown, not used to answer) | adapter agents, builder, connectors, data tools, federated engine via the gateway |
| 12 How it fits together | two checks, grounding, audit; Hy3 | 13 | the live model chain; the Tencent Cloud deploy kit card |
| 13 The numbers | attacked on purpose | 14 | the business case (15) |
| 14 Built with, thanks | CodeBuddy; thanks | 11, 20, 16 | CodeBuddy session evidence; the Miora cover; the ask |

## Which scenes the short cut (about 6:31) drops

06b live growth, 19 add a file, 21 new here (tour / dictation / value calculator), 20 Miora, and the three slots
S1 to S3. It also uses the shorter spoken lines (no **[MAIN]** or **[FULL]** lines) and shorter holds. The spine
(04, 06, 02, 03, 05, 08, 12, 10, 13, 14, 11, 16) stays in every cut.

## Lines that change if Hy3 goes live

| Scene | Line | Also re-shoot |
|---|---|---|
| 13 | "And it's wired for Tencent Hunyuan through TokenHub…" becomes "And it runs on Tencent Hunyuan through TokenHub…"; the "Until Hy3 is switched on…" line is dropped. No line ever says Hy3 wrote the answers | Scene 13 (the badge becomes "Live models") |
| 02, 04 | No spoken change, but the answer label on screen changes from "AI-generated from your cited sources" to "Tencent Hunyuan Hy3" | Scenes 02 and 04 |
| 05 | No spoken change ("the index filter and the live source checks first" is true either way); the Model card shows Hy3 instead of the neutral stand-in label | Scene 05 |

Then run `python docs/finals/video/assemble_finals.py --hy3` so the cue sheet uses the Hy3 lines.

## Truth checks before recording the voice

| Line | Source | If it changed |
|---|---|---|
| 7,374 / 0 leaks; 400 / 0 differences; 1,152 injection attempts | `databridge/apps/internal_brain_web/public/eval_summary.json` (run 29 to 30 Oct) | Read the new numbers; the slide updates itself; `assemble_finals.py --self-test` fails if the script's words no longer match |
| 1,278 backend, 185 browser tests | `eval_summary.json` `tests.backend` / `tests.e2e`; `docs/finals/TEST_REPORT.md` (cloud clean checkout, 30 Oct evening, branch `final_v1.2-cloud`: 1,274 passed, 0 failed, 4 skipped; e2e 185 passed, 0 failed, 42 skipped) | Say the new counts; the scene 14 caption reads the file |
| Nine CodeBuddy sessions | `video_constants.json` `codebuddy_sessions`; `docs/tencent/evidence/` | Edit the constant and the spoken word |
| Graph and engine counts | Not spoken (the world is growing); the screen shows them | Nothing to change |
| US$1.3M, 7 minutes | `docs/finals/rubric/impact_feasibility/value_calculator.spec.json` | Match the slide |
| "Tipping off" line | Run sheet, beat 0:15 | Keep the wording as written |
| "AI-generated from your cited sources" (13) | the answer label `answer.model.ai` in `lib/i18n/user.ts`; the stand-in on (D-41, switched on in `.env.ib.local`), named only `standin/standin-1` | If the model is off for the take, run the cue sheet with `--model-off` |
| "Wired for Tencent's Hunyuan" (01), "wired in through TokenHub" (13) | `STATUS.md`, `/ib/status`; Hy3 not live as of 30 Oct | If Hy3 answers live, say "running on Tencent's Hunyuan" in 01 and read the [ONLY IF HY3 LIVE] line in 13 |
| "nine recorded sessions, including security reviews that found real bugs" (11) | `README.md` CodeBuddy table (CB1–CB9); `DEV_PROCESS.md` (CB5: 7 findings, fixed; CB7: F7 fixed with tests) | Match the README |
| "The cover was designed in Tencent Miora" (20) | `submission/06_cover/finals/README.md` (made in Miora, 30 Oct) | If the cover changes, update the card |
| "packaged to deploy on Tencent Cloud" (13) | `deploy/lighthouse/README.md` (written; no live instance) | Never say it is deployed until an instance is live |
| "Target host: Tencent Cloud … no live instance yet" (15, pill) | run sheet 3:50; `rubric/README.md` row 5 | If an instance goes live, change the pill text in `assemble_finals.py` |

**Fallback:** every scene is its own clip; if one breaks, re-shoot just that one
(`node docs/finals/video/record_finals.cjs 08_audit`, then `python docs/finals/video/assemble_finals.py`).
Never fake a result.
