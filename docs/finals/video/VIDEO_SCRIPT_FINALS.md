# Finals demo video: script and shot list

**What it is:** the new demo video for the finals build of **Walnut DataBridge** (branch `final_v1.0`). It
replaces the 16 Oct video (https://youtu.be/fr9wqHWsijY, 6:28, made from the old UI) and keeps its
pattern: Ishaan's camera intro, then a narrated screen demo of the real app.

**Length:** 6:08 in total in the test assembly of 30 Oct (the camera intro is 0:32, the screen demo 5:36); about 6:35 with the optional scene 06b. **Format:**
1920×1080, dark theme. The exact times of every line are in `submission/finals_video/narration_cues.md`,
generated from the finished picture.

**How it's made:** each scene is recorded on its own from the running app
(`docs/finals/video/record_finals.cjs`), so one scene can be re-shot without touching the rest.
`docs/finals/video/assemble_finals.py` joins them behind the camera intro. Ishaan then records the
voice-over against the cue sheet, and `assemble_finals.py --voice <file>` lays it under the picture.

**How to read it:** like you're showing a friend something you built. Relaxed, not salesy. Pauses are
fine; the picture has room for them. About 150 words a minute.

**Hy3 lines.** Hy3 is wired in but not switched on yet (TokenHub activation is pending), so the app
labels every answer "Quoted from sources (no model)". A line marked **[if Hy3 is on]** is read only after
Hy3 has answered live in this app; otherwise read the **[otherwise]** line. Keep each marked line on one
line: `assemble_finals.py` reads the script line by line. If Hy3 goes live, scenes 02, 04 and 13 must
also be re-shot, because the answer label changes on screen.

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
| Theme | The normal dark theme, not the Stage theme (that one is for the venue projector). The three closing slides are the app's own `/present` pages, also dark. |

---

### 00 · Camera intro (0:00–0:31)
**On screen:** Ishaan to camera, letterboxed with the navy bars and the "Ishaan · Team Walnut" label
(`submission/05_video/voice/intro_final.mp4`, reused from the first video, with its own sound).

Already recorded. Nothing to read here. (What it says: an AI that can read everything can also leak
everything, so we built one that only answers from what you're allowed to see. "Let me show you.")

### 01 · Title (0:31–0:37)
**On screen:** title card: "Walnut DataBridge. Answers you can prove."

> This is Walnut DataBridge. And we'll start with Aspire's own question.

### 02 · Same question, two people (0:37–1:16)
**On screen:** Compare, "Same question · two people". Amy (account manager) on the left, Rachel (MLRO) on
the right. The pointer types *"Why has Velquarry Trading's account been restricted, and what should I tell
the customer?"*, presses **Ask both**, then reads along Amy's answer and Rachel's.

> Two people at a fintech ask the same thing: why has Velquarry Trading's account been restricted, and
> what should I tell the customer?
> Amy, an account manager, gets the approved customer script, with its sources, and nothing else.
> Rachel is the money-laundering reporting officer. She gets the actual case: the flagged transfers,
> and the review.
> Same system, two different truths. In Singapore, telling Amy that a suspicious-transaction report even
> exists is a criminal offence. So the system never does.

### 03 · Restricted looks exactly like nothing (1:16–1:38)
**On screen:** Compare, "Same person · two questions". Ken (outside contractor) asks about Velquarry on the
left and about "Bayfront Spice Traders", a company that does not exist, on the right. Two refusals, and
the marker "Identical responses: same text, same sources".

> Now Ken, an outside contractor. On the left, he asks about Velquarry. On the right, about a company
> that doesn't exist at all.
> The answers are identical, word for word. A restricted file looks exactly like nothing. We tested that
> on four hundred pairs of questions: zero differences.

### 04 · Every sentence has a source, checked live (1:37–2:04)
**On screen:** Rachel's own Ask page. She clicks her starter question about the Velquarry review. The live
progress line, then the cited answer. She clicks a citation: the source sheet shows the platform, the
version, "Checked live at" and the cited lines highlighted. **Esc**. Then the language menu: 简体中文, and
back to English.

> Here's Rachel's own screen. Every sentence points at a passage she can open right now. Click one,
> and you see the exact lines, the version, and when it was checked with the source: seconds ago.
> And the whole app also speaks Chinese.

### 05 · Developer mode: one request, end to end (2:03–2:37)
**On screen:** signed in as Maya (compliance officer). She switches to **Developer** at the top left,
opens **Journey** and picks Rachel's request. The waterfall, the three ledger seals, then the "Considered →
Gate 2" table.

> Now the developer side, as Maya from compliance. This is Rachel's request, end to end. Each bar is a
> real timing: two permission checks before anything is composed, then the answer.
> These three records were sealed and chained before Rachel saw a word.
> And below, every document that was considered, with its verdict and the reason. Maya never sees the
> answer itself.

### 06 · The knowledge graph (2:37–3:01)
**On screen:** signed in as Jordan (backend engineer). **Knowledge graph**: every item he can open (867
items, 17 places, 4 platforms on 30 Oct; the count grows with the world). A search for "migration" opens
one item's panel. Then **Compare two people**: Ken's graph beside Jordan's (24 items on 30 Oct).

> This is everything Jordan, a backend engineer, can open, as one graph: every item on four platforms,
> each one checked with its source.
> Compare two people, and Ken's graph sits right beside it: a small corner of the company, and no
> greyed-out boxes for the rest. For Ken, the rest simply isn't there.

### 06b · Watch the brain grow (OPTIONAL: only if the feature has landed and the clip is recorded)
**On screen:** Jordan, **Knowledge graph** with the "add" form (`/dev/graph?add=1`). He posts a Slack note,
"Heads-up: the ledger cutover dry run moves to Thursday 9am, owner Priya.", and it appears in his graph.
Then, in User mode, he asks *"When is the ledger cutover dry run?"* and gets it back with a citation.

> And the brain keeps up. Jordan posts a note in Slack, and it's in his graph straight away, checked
> with Slack like everything else.
> He asks about it a moment later, and the answer quotes his note, with the source.

### 07 · Analytics (3:00–3:19)
**On screen:** Maya, **Analytics**: requests, answer rate, latency, Gate-2 deny rate, grounding drops,
fallback use; then **Live only**.

> Maya's analytics come straight from the verified log: how many questions, how many were answered,
> how fast, and how often the live check said no. Seeded history is labelled, and one click leaves it out.

### 08 · The audit log, and the tamper demo (3:18–3:55)
**On screen:** Maya, **Audit**. She types *"Everything mlro.tan accessed in the last 7 days"*, presses
**Interpret**: the filter, then the rows. **Verify audit chain**: "Chain intact". Then **Run the tamper
demo on a copy**: "Your real audit log was not modified", and the row "Chain broken at entry …".

> Maya can also ask the audit log in plain English: everything Rachel accessed in the last seven days.
> She sees the filter first, then the rows.
> Verify re-checks every link in the chain: intact.
> Now the fun part. The tamper demo copies the log and attacks the copy. It changes one old decision,
> from "forbidden" to "verified", and Verify points at the exact entry. Deleting or rolling back records
> is caught too. And the real log was never touched.

### 09 · The Trust centre (3:55–4:09)
**On screen:** **Trust centre**: seven controls, each with the frameworks it maps to and the tests that
prove it.

> The Trust centre lists the seven controls behind all of this. Each one is enforced in code, mapped to
> Singapore's AI and data rules, and proved by a named test.

### 10 · The Walnut engine underneath (4:09–4:39)
**On screen:** **Walnut engine**: "932 entities · 2,246 links · 4,710 pointers". **Company brain**: the time
slider dragged back, then forward. **Provenance**: the DB Migration Plan, one value traced to its row and
file.

> Underneath it all is Walnut, the evidence engine. Here's Company A as Jordan sees it: every entity,
> every link, and every value with a pointer back to its source.
> Drag the time slider, and the company's knowledge rewinds to any past date.
> And any value can be traced, step by step, to the exact row in the exact file.

### 11 · Built with Tencent CodeBuddy (4:38–4:51)
**On screen:** card: one of the nine CodeBuddy session transcripts (the security review), and what
CodeBuddy built.

> We built this with Tencent CodeBuddy. It built our WorkBuddy connector and the model tests, and ran a
> security review that found real issues. Every session is in the repo.

### 12 · Ask from Tencent WorkBuddy (4:51–5:08)
**On screen:** two real WorkBuddy window captures (15 Oct): the three DataBridge connectors, then one
question through Jordan's and Ken's connectors with two different answers.

> And it lives inside Tencent WorkBuddy too, as a second front door. Each connector carries its own
> signed token, so Jordan's connector gets Jordan's answer, and Ken's gets Ken's.

### 13 · Tencent Hy3 (5:07–5:21)
**On screen:** Maya, **Mission control** → the model chain: Hy3, then DeepSeek, then the quoted fallback.
**[otherwise]** The badge reads "Extractive only".

> Tencent Hy3 is wired in through TokenHub, with a backup model and a safe fallback.
> **[otherwise]** Answers are quoted from the sources until Hy3 is switched on, through exactly the same checks.
> **[if Hy3 is on]** The answers you saw were written by Hy3, inside exactly the same checks.

### 14 · The proof (5:20–5:38)
**On screen:** the app's proof slide: 0 leaks in 7,374 adversarial requests, 0 differences in 400 paired
questions, 0 hard failures in 1,152 prompt-injection attempts. Label: 1,131 backend + 166 browser tests.

> We didn't just demo it, we attacked it. Five thousand nine hundred and fifty-eight adversarial
> requests: zero leaks. Four hundred paired questions: zero differences. And one thousand one hundred and
> thirty-one backend tests, plus a hundred and sixty-six browser tests, all passing.

### 15 · What it's worth (5:38–5:57)
**On screen:** the app's business slide: about US$1.3M a year (estimate), break-even about 7 minutes per
person per week, and the inputs.

> What's it worth? For a six-hundred-person fintech, if each person saves ninety minutes a week, that's
> about one point three million US dollars a year. It's an estimate, not a measurement. And it pays for
> itself at about seven minutes a week.

### 16 · Close (5:57–6:08)
**On screen:** the app's close slide: "Answers you can prove." and the QR code to the repo.

> Walnut DataBridge. Answers you can prove. We'd love one pilot team at one regulated company. Thanks
> for watching!

---

## Lines that change if Hy3 goes live

| Scene | Line | Also re-shoot |
|---|---|---|
| 13 | "Answers are quoted from the sources until Hy3 is switched on…" becomes "The answers you saw were written by Hy3…" | Scene 13 (the badge becomes "Live models") |
| 02, 04 | No spoken change, but the answer label on screen changes from "Quoted from sources (no model)" to the model's name | Scenes 02 and 04 |
| 05 | No spoken change ("before anything is composed" is true either way); the Model card shows Hy3 instead of "Extractive (no model)" | Scene 05 |

Then run `python docs/finals/video/assemble_finals.py --hy3` so the cue sheet uses the Hy3 lines.

## Truth checks before recording the voice

| Line | Source | If it changed |
|---|---|---|
| 7,374 / 0 leaks; 400 / 0 differences; 1,152 injection attempts | `databridge/apps/internal_brain_web/public/eval_summary.json` (run 29–30 Oct) | Read the new numbers; the slide updates itself |
| 1,131 backend, 166 browser tests | `eval_summary.json` `tests.backend` / `tests.e2e`; `docs/finals/TEST_REPORT.md` (cloud run 30 Oct, commit 831297e: 1,131 passed; e2e 166 passed, 0 failed, 41 skipped) | Say the new counts and edit the scene 14 label in `assemble_finals.py` |
| Graph and engine counts | Not spoken any more (the world is growing); the screen shows them | Nothing to change |
| US$1.3M, 7 minutes | `docs/finals/rubric/impact_feasibility/value_calculator.spec.json` (600 people, 90 min/week, US$31/h, 46 weeks, US$100,000/yr) | Match the slide |
| "Tipping off" line | Run sheet, beat 0:15 | Keep the wording as written |

**Fallback:** every scene is its own clip; if one breaks, re-shoot just that one
(`node docs/finals/video/record_finals.cjs 08_audit`, then `python docs/finals/video/assemble_finals.py`).
Never fake a result.
