# Finals video, plan v3: intro, film, slides, two workflows, company brain

**Status:** plan and script, 30 Oct 2026 night. **C1 and C3 are recorded** (see §7, Inputs received); the
AI film (C2) is the only missing input; C4–C7 are not recorded yet.
**Target:** main cut ≤ 8:00 (5–8 min was the stated range for the 16 Oct form; the finals limit is not
confirmed). Planned total ≈ 7:41 at a 1:20 film, which leaves ~19 s of buffer.
**Spine:** this keeps the 16 Oct video's core (who sees what, cited answers, the audit trail, Tencent tools,
the proof, the close). The two workflows are the new "show, don't tell" middle.

## 0. Structure at a glance

| # | Part | Length | Who / how | On screen |
|---|---|---|---|---|
| C1 | Intro | 0:00–0:10 (9.5 s) | **Aaruni on camera** · **recorded** | Aaruni, plain background (848×464, upscaled onto a 1920×1080 canvas) |
| C2 | The idea film | 0:10–~1:30 (≈1:20, **TBC**) | **AI-generated film** (made by the team; the only missing input) | The scattered-knowledge problem → one company brain |
| C3 | Feature slides | ~1:30–2:50 (1:20, 79.8 s) | **Aaruni** voice-over · **recorded** (narration in the file) | `walnut-feature-slides.html`, 3 slides |
| C4 | Workflow 1: Atlas | 2:50–4:20 (1:30) | **Speaker B** (as Alex, first person) | Real app: Ask → page beside the chat |
| C5 | Workflow 2: The Call | 4:20–5:24 (1:04) | **Speaker B** | Real app: Amy, then Rachel, the same question |
| C6 | The company brain | 5:24–7:26 (≈2:02) | **Speaker B** (Aaruni on Tencent) | Real app: map, engine, journey, audit and tamper, WorkBuddy, Tencent tools, proof |
| C7 | Close | 7:26–7:41 | **Aaruni** | Title card: "Walnut DataBridge. Answers you can prove." |

Total ≈ 7:41 at a 1:20 film (C1 and C3 are real lengths; C4–C7 are planned slots), so the buffer under
8:00 is ~19 s: the film may run to about 1:39 before anything has to go. **Cut order if C2 runs longer**
(each step is independent; take them top to bottom until the main cut is ≤ 8:00):
1. C5 the optional blurred FinCrime cut (−8 s; fold its line into the first row).
2. C6 row 5: show the engine view only, not the three platform screens (−8 s; keep the words about them
   out too).
3. C6 row 1: the map compare (−15 s; row 2 still makes the "nothing is hidden" point on its own).
4. C4: the hold at 1:25 and the typing beat at 0:10 (−8 s).
5. C6 row 6: the Miora sentence (−3 s).
C3 is a recorded file, so it is no longer a place to cut. Never cut C5 proper, C6 row 4 (the tamper) or
C6 row 7 (the proof): those carry criteria 1, 7, 9 and 4.

The storytelling thread, so every part earns its place: C1 says what Walnut is; C2 shows the problem;
C3 explains the three ideas; **C4 proves "it knows everything"**; **C5 proves "it knows who's asking"**
(the FinTech hook, the twist); C6 proves you can check all of it; C7 asks. The two workflows stay in that
order: Atlas is the set-up (breadth, the page), The Call is the pay-off (the same question, two answers).
C5 must therefore never be the part that gets squeezed.

## 1. Decisions the founder must make (blocking)

1. **Company name on screen.** The app and both workflows' sources say "**Company A**" (e.g. "Project
   Atlas is Company A's merchant-onboarding workspace"). If the narration says "Pandan Ledger", viewers
   see "Company A" on screen, which is a mismatch. Options:
   - **(a)** Narration says "Company A, our fictional Singapore fintech". No data change. *(Recommended
     for tonight.)*
   - **(b)** Rename Company A → Pandan Ledger in the synthetic worlds. That means regenerating the data,
     then re-running the tests and the leak eval (~1–2 h), and any missed string becomes a visible
     mismatch.
   - **(c) Never speak the company's name at all** *(reviewer's recommendation; cheaper than (a))*. No
     spoken line in C3–C7 needs it: Alex talks about "Atlas", Amy's story is about "Velquarry", the slides
     already say "Illustrative example using fictional Company A records". The only place a name could
     appear is the film (C2): keep the film about "your company" (no fictional name on screen or in its
     voice-over). Then put **one** lower-left caption at the start of C4, "Company A · a fictional
     Singapore fintech · every record is synthetic", which also covers repo rule 7 and criterion 9. If the
     film is already rendered with "Pandan Ledger" visible and cannot be changed, fall back to (a) and add
     the caption "(called Pandan Ledger in the film)" once, at the start of C4; never leave the two names
     unexplained.
2. **The AI-generated film (C2):** its length and content, and who produces it. The plan assumes ~1:20.
3. **Speakers:** Speaker A = **Aaruni** (C1, C3, C7, and the Tencent line in C6); Speaker B = **the
   second person** (C4, C5, C6). Confirm the names.

## 2. Recording rules (founder)

- Record **only after** the Story 1 fixes and the Atlas integration land, and a live re-run passes.
- **Every screen must show exactly the right behaviour.** The recorder refuses a take whose state isn't
  as expected, and wrong takes are never used.
- The model is ON (neutral label "AI model"); never show or say Hy3/Hunyuan as live unless TokenHub is
  live.
- **No run outputs in the repo** (results, real answers, raw screenshots, logs). Takes and working files
  stay outside git.
- Light mode, 1440-wide window, the same look across C4–C6. Waits are cut in the edit, but keep ~1 s of
  "thinking" so it never looks faked.
- **No provider name anywhere on screen.** Before every C4–C6 take, check the answer badge, the journey's
  Model card, the engine view and Mission control (if it is ever in frame): the stand-in may appear only as
  "AI model". The recorder already refuses a take whose model chain names a third party; the same rule
  applies to anything framed by hand. No spoken line names a provider either (the Tencent names in C6
  row 6 are tools, not the answering model).
- **A lower-left caption on every C4–C6 shot**, as in the 16 Oct video, so the video makes sense on mute:
  who is signed in and what just happened ("Amy · account manager", "Rachel · MLRO · the same question").
  The captions each part needs are listed under its table.
- **"Checked live" means checked with the source system of the demo**: the platforms are real-API-shaped
  mocks with synthetic data. Nothing spoken or captioned may suggest a live Slack, Confluence, Jira or
  Google account, a native email or WhatsApp connector, or a live Tencent Cloud instance.

## 3. Word-for-word script

### C1 · Intro (Aaruni, on camera, 9.5 s, **recorded**, ~30 words)
> "Hi, we're Team Walnut, and we're building a company knowledge and intelligence platform. We're excited
> to show you what we've been working on. Let's take a look."

*Edit notes for the recorded file (§7):* 848×464 at 30 fps, so upscale it onto the 1920×1080 canvas,
centred, with a soft blurred fill of the same frame behind it (never stretch it to full width). The audio
is quiet (mean −39.9 dB): normalise loudness to the same target as the other voices (about −16 LUFS
integrated) so the first word is not the quietest in the video.

*Only if C1 is ever re-shot (optional hook variant, same length; it plants the two workflows' thesis so the
judges know what to watch for during the film and the slides):*
> "Hi, we're Team Walnut. We're building a company brain that knows everything about your company, and
> knows who's asking. Let's take a look."

### C2 · The idea film (AI-generated, ~1:20)
Made by the team. Suggested beats: knowledge scattered across tools and teams → people ask colleagues
and wait → one connected company brain → answers and useful interfaces, with evidence you can check.
Suggested voice-over (Aaruni, if the film needs one; ~15 s for the opening line):
> "Your company's knowledge already exists. It's just scattered across tools and teams. We're building
> Walnut to connect it into one company brain, and turn that knowledge into answers and useful
> interfaces, with evidence you can check."

### C3 · Feature slides (Aaruni, voice-over, 1:20, **recorded**)
The slides and the narration are one file (§7): 79.8 s, 1918×1080, narration mean −31 dB. Edit notes:
scale to 1920 wide (2 px), normalise loudness to the common target, and either mask the top bar (the
mouse cursor and the "Present" button's focus ring are visible there) with a light crop of about 60 px
that is re-scaled, or keep it as is: the bar is quiet and a judge will not notice it at normal speed. The
spoken text below is the slides' own speaker notes and is kept for the subtitles (.srt); check the
recording against it word for word before captioning.

**Slide 1, "Your company. Your access rules." (~45 s)**
> "Walnut brings the information together while keeping your company's access rules in place. It
> searches only the sources that the person asking is allowed to open, and checks those permissions again
> before using them. So different people can ask the same question and receive information suited to
> their access.
> Next, Walnut makes answers easier to check. Citations let you open the original passage and see where
> a claim came from. It also checks names and numbers against the evidence. If it cannot support an
> answer, it can quote the source or say it could not find enough information. These checks help reduce
> mistakes."

**Slide 2, "A question. The right view." (~25 s)**
> "The interface also adapts to what you need. A simple question can stay in chat. A broader request can
> open a page with a team table, timeline or next steps, with sources attached. That's what we mean by
> just-in-time UI: an interface built around your question."

*Pace check (only matters if C3 is re-recorded):* slide 1 is ~105 words in 45 s (2.3 words/s, no room
for a breath; the 16 Oct rule was 2.5 words/s against the slot minus 1 s). If it is ever redone, drop "So
different people can ask the same question and receive information suited to their access." It is also
C5's twist, said flatly 1:40 before C5 shows it.

**Slide 3, "A company brain. With a memory." (~30 s)**
> "Finally, Walnut keeps an audit trail. An authorised reviewer can trace the question, the sources used
> and the access decisions behind the answer.
> Let's see how these features help someone on their first day leading a project."

*Claims check:* the slide-3 visual shows "snapshots" (what the brain held on 23 / 29 / 30 Oct). In the
app, snapshots exist in the **Walnut engine view** (the time slider, "Snapshot: Company A as {name} sees
it"), not as per-project dated briefs. The spoken line therefore covers the audit trail only. The slide
still shows "Snapshots · 23 / 29 / 30 Oct" while nothing is said about them, so C6 row 5 now shows the
engine time slider and says one sentence about it ("rewind what the company knew to an earlier date"),
which is what the app does. If the founder wants the slide's snapshot sentence back here, it must be the
engine's claim, not a per-project brief: "Snapshots let you look back at what the brain held on an
earlier date."

### C4 · Workflow 1: "Your first day leading Project Atlas" (Speaker B as Alex, 1:30)
Source: Dev's `atlas-onboarding-workflow.md`, being integrated on `feat/atlas-workflow`. Signed in as
`alex.product`. Question (exact, from `prompt.txt`):
> "Hi, I'm Alex Morgan, the new product lead for Project Atlas. Bring me up to speed with an onboarding
> brief: purpose, status, team table and contributions, decisions, blockers, timeline and first-week
> checklist. Use all connected sources, cite every claim and flag outdated or missing information."

| Time | Screen | Say |
|---|---|---|
| 0:00–0:10 | Alex on the Ask page | "It's my first day leading Atlas. The project already has months of context spread across documents, tickets and conversations." |
| 0:10–0:22 | Type the start of the question, cut to it finished | "I need to understand what we're building, who's involved and what needs my attention." |
| 0:22–0:35 | Retrieval; the chat shifts left, the page opens | "Walnut brings the connected project information into one workspace, with evidence attached." |
| 0:35–0:48 | Purpose and status (Amber) | "Here's the project, its scope and where it stands today." |
| 0:48–1:00 | Team table (12) | "I can see who contributed, what they delivered and what each person owns next." |
| 1:00–1:14 | Revised date + QA blocker; open a citation | "The original pilot date has changed. The new date is conditional, and two callback failures still block sign-off. I can open the source behind that." |
| 1:14–1:25 | First-week checklist | "Now I know who to meet, which decisions to confirm and what to prioritise." |
| 1:25–1:30 | Hold | "One question. A useful starting point for my first day." |

Must show: Amber; 6 Nov superseded → 12 Nov **provisional**; 2 of 7 failures remain (118/120); Elena owns
the fix; the go/no-go on 3 Nov. Never say the 1-day target is achieved, never claim time saved, and never
call email/WhatsApp native connectors (they're imported archives in Drive).

Captions (lower left): 0:00 "Company A · a fictional Singapore fintech · every record is synthetic" (the
one place the company is named, see §1.1); 0:22 "Alex · product lead · signed in as alex.product: the
question does not decide who he is"; 0:48 "12 people, from the Drive roster and their Jira tickets"; 1:00
"Email and WhatsApp: imported archives in Drive, not live connectors" (only if such a citation is on
screen); 1:14 "Every claim cited; outdated and missing items flagged".

### C5 · Workflow 2: "The Call" (Speaker B, 1:04)
Source: `story1-the-call-workflow.md`. Signed in as `amy.am`, then `mlro.tan`. The same question, word for
word:
> "Why has Velquarry Trading's account been restricted, and what should I tell the customer?"

The hinge: C4 showed that Walnut knows everything; C5 shows that it knows who's asking. That sentence is
the whole video's thesis and was not spoken anywhere, so it now opens C5 (the first row grows by 4 s).

| Time | Screen | Say |
|---|---|---|
| 0:00–0:12 | Cut from Alex's workspace to Amy's Ask page; caption "Velquarry Trading · on the line" | "Walnut knew everything about Atlas. Now the harder test: does it know who's asking? A customer calls Amy. His account's been frozen, and he wants to know why. Amy doesn't know." |
| 0:12–0:20 | (Optional) blurred FinCrime title, "what Amy can't see" | "Upstairs, compliance is investigating him for money laundering. If Amy even hints at it, that's tipping-off. A crime." |
| 0:20–0:31 | Mic, then cut to the full question; the approved script with 2 sources "Checked live" | "So she asks Walnut. It gives her the approved script, with its source. Nothing about the case. It doesn't even admit there is one." |
| 0:31–0:39 | Draft a reply → "Draft for your review" | "One tap drafts her reply. Amy sends it, not the AI. The customer's handled in a minute." |
| 0:39–0:45 | Rachel, the same question; her cited answer | "Now Rachel, who runs financial crime, asks the exact same question." |
| 0:45–0:57 | The case page opens (by itself, after the fix, or via **Open as page**): the red summary, "What to tell the customer", Key numbers **37**; tap `[1]` | "She gets the whole case: thirty-seven transfers in nine days, laid out for her, and every number linked to its source." |
| 0:57–1:04 | Hold the split view | "Same brain. Same question. Two different answers, because they're two different people." |

Pace: the draft row was 18 words in 6 s (3 words/s); it now has 8 s. "The customer's handled in a minute"
is a story line, not a measurement; if the founder would rather not claim speed, say "The customer gets a
straight answer, and the right one." Rachel's row at 0:39 has 6 s of slack for the real "thinking" beat.

Don't say: "the STR has been filed"; "11 of 37 on one device"; "asking by phone" (the mic is browser
dictation; a phone-line agent is not built). Frame out the model badge if it reads wrong (the badge fix is
in progress). Never use **Open as page** on Amy's answer.

Captions (lower left): 0:00 "Amy · account manager"; 0:20 "Dictated into the box; nothing is asked until
she presses Ask" (criterion 8, and it is what `voice.spec.ts` covers); 0:31 "Draft for your review · not
sent"; 0:39 "Rachel · MLRO · the same question, word for word"; 0:45 "Every number links to its source";
0:57 "Who you are comes from the sign-in, never from the question".

### C6 · The company brain (Speaker B; Aaruni for the Tencent lines; ~2:02)
This is the 16 Oct core, shown on today's screens. Rows 5–7 were over-packed (row 6 was 51 words in 15 s,
3.4 words/s; row 5 was 40 words and four screens in 15 s); rows 1 and 3 gave back the time.

| # | Time | Screen | Say |
|---|---|---|---|
| 1 | 0:00–0:15 | Developer → Graph, Compare: **Rachel vs Ken** (not Amy: Amy cannot open the case either, so Amy-vs-Ken would show no contrast) | "Underneath is a map of what each person can open. For Ken, an outside contractor, the restricted case simply isn't there." |
| 2 | 0:15–0:30 | Ken asks about Velquarry, then a made-up company; "Identical responses" | "Ask about a real restricted company, or one that doesn't exist: the answers are identical, word for word. Walnut doesn't just hide the secret. It hides that a secret exists." |
| 3 | 0:30–0:45 | Journey of Rachel's request (the Model card must read "AI model" only) | "Every answer leaves a trail: who asked, which sources were allowed, and every check before the model saw anything." |
| 4 | 0:45–1:05 | Audit → **Verify** "Chain intact" → tamper on a copy → "Chain broken at entry n" → "real log not modified" | "When the regulator asks who saw the case, the compliance officer can prove it. We try to cheat on a copy of the log, and Walnut catches it. The real log was never touched. That's what we mean by answers you can prove." |
| 5 | 1:05–1:30 | Engine view (entity graph, then the time slider dragged back and forward) + the platform screens (connectors, adapter builder, agents), each captioned "The platform:" | "We built the whole platform: connectors to Slack, Confluence, Jira and Drive, AI agents that build new adapters, a federated engine across systems, and an evidence graph you can rewind to what the company knew on an earlier date. The answers you saw run on its core: the grounding wall and the sealed audit trail." |
| 6 | 1:30–1:50 | WorkBuddy stills/clip; CodeBuddy evidence pages; the Miora cover; a card "Packaged for Tencent Cloud · no live instance yet" | (Aaruni) "We built it with Tencent CodeBuddy, nine recorded sessions, including security reviews that found real bugs. It plugs into Tencent WorkBuddy, so people can ask from where they already work. *[optional, −3 s]* Our cover was designed in Tencent Miora. And it's wired for Tencent Hunyuan through TokenHub, packaged to deploy on Tencent Cloud." |
| 7 | 1:50–2:02 | Proof slide (`/present/proof`; its caption carries the test counts, read from `eval_summary.json`) | "Seven thousand three hundred and seventy-four attack questions: zero leaks. Four hundred secret-versus-made-up pairs: zero differences." |

Truth notes for this part: "connectors to every source" was an overclaim (there are four connectors);
"packaged to deploy" must sit next to a caption that says no instance is live; the rewind sentence is
backed by the engine view's time slider (`engine.spec.ts`), which is the only place snapshots exist; the
platform screens are "the platform", never "how the answer was found" (the Internal Brain's answers never
go through the adapter agents or the federated engine). Row 7's numbers were measured with a capturing test
model; the line does not say "with the model on", and must not until a re-run with the stand-in says so.

Captions (lower left): row 1 "A map of what each person can open, built from the same permission checks";
row 2 "Ken · outside contractor · a restricted company vs a made-up one"; row 3 "Rachel's request: every
stage, three sealed records before she saw a word"; row 4 "Maya · compliance officer · the tamper runs on a
copy, never the real log"; row 5 "The platform: shown, not used to answer"; row 6 "Tencent WorkBuddy · real
window capture (15 Oct)" and "Packaged for Tencent Cloud · no live instance yet"; row 7 the slide's own
test-count caption.

### C7 · Close (Aaruni, ~15 s)
> "Every company's AI will be asked to prove itself. Walnut DataBridge. Answers you can prove. We'd love
> to pilot it with one team at one regulated company. Thank you."

## 4. Claims check (every spoken number is true today)

| Claim | Evidence |
|---|---|
| 0 leaks / 7,374; 0 / 400 | eval_summary.json (measured with a capturing test model and the quoted path; re-run with the real model sample before saying "with the model on") |
| Nine CodeBuddy sessions | video_constants.json; TENCENT_TOOLS_REPORT |
| WorkBuddy | MCP connectors, 33/33 programmatic test |
| Hy3 | "wired for", never "runs on" (TokenHub not activated) |
| Atlas facts | the scenario's ground truth (Dev's pack + verify.py) |
| Velquarry facts | world.json (F1–F6 verified) |
| "Identical responses" | the UI label exists (cmp.identical) |
| "connectors to Slack, Confluence, Jira and Drive" (C6 row 5) | the four connectors in `databridge/apps/internal_brain/`; email and WhatsApp are imported archives in Drive (Atlas pack), never spoken as connectors |
| "rewind to what the company knew on an earlier date" (C6 row 5) | the engine view's time slider, `tests/e2e/engine.spec.ts`; the only snapshot feature that exists |
| "packaged to deploy on Tencent Cloud" (C6 row 6) | `deploy/lighthouse/README.md`: written, no live instance; the caption says so on screen |
| "Checked live" (C5) | Gate 2 against the mock platforms (real-API-shaped, synthetic data); never "live Slack/Jira/Drive accounts" |
| Amy's mic (C5) | browser dictation, `voice.spec.ts` (stubbed); never "by phone" |
| The model | on screen only as "AI model"; no spoken or shown provider name anywhere in C1–C7; never Hy3/Hunyuan "answered" |
| "The customer's handled in a minute" (C5) | a story line, not a measurement; the softer alternative is in C5 |

## 5. Production steps (laptop)

1. **Land the fixes:** feat/story1-fixes (Amy's script quoted exactly, the badge, Rachel's facts and page,
   the warm-up) + feat/atlas-workflow (Atlas data and the onboarding scoping), merged, all tests green.
2. **Start the stack** with the Atlas world switch on and the model on; `Reset Walnut Demo` (fresh).
3. **Dry-run both workflows once.** The recorder checks every expected state and refuses a take if any is
   wrong.
4. **Record the screens** (I take over the screen with your OK, ~20–30 min): C4, C5 and C6 shots, and the
   slide deck animation in C3.
5. **Speakers record the voice-over** from the cue sheets (one per speaker; generated from this script).
6. **Assemble:** C1 (Aaruni's camera file, upscaled and loudness-normalised) + C2 (the team's film) + C3
   (the recorded slides, scaled and normalised) + C4–C7. One loudness target for every voice (about −16
   LUFS integrated), since the three sources were recorded at very different levels (−39.9, −31 and the
   cue-sheet takes). Then the captions, the subtitles (.srt) and a main cut ≤ 8:00.
7. **Review:** a storytelling review against the rubric, plus a claims check; then the final render.

## 6. What I need from the team

- The AI film (C2), as a video file, with its length (C1 and C3 have arrived, §7).
- The speakers' names, and when Speaker B can record C4–C6.
- The company-name decision (§1.1; (c) needs nothing from the data).

## 7. Inputs received (30 Oct night; video files stay outside git)

| Part | File | Measured | Edit notes |
|---|---|---|---|
| C1 intro | `C:\Users\Ishaan Sirbhaiya\OneDrive\Desktop\IMG_3816.MOV` | 9.5 s, 848×464, 30 fps, audio mean −39.9 dB | Upscale onto the 1920×1080 canvas, centred, soft blurred fill behind; loudness-normalise (it is the quietest source) |
| C3 slides + narration | `C:\Users\Ishaan Sirbhaiya\OneDrive\Desktop\Screen Recording 2026-10-30 210819.mp4` | 79.8 s, 1918×1080, narration mean −31 dB, recorded in the file | Scale to 1920 wide; normalise; the cursor and the "Present" button's focus ring show in the top bar: light crop/mask, or keep as is |
| C2 film | not yet received | planned ≈ 1:20 (TBC) | The only missing input; the cut order in §0 covers a longer film |

Neither file goes into the repo (recording rule, §2). Copy them into the working folder outside git that
`assemble_finals.py` reads from, and record their paths and lengths in the cue sheet header.
