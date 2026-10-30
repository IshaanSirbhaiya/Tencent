# E. Demo & Storytelling, and Overall Quality / Judge's Impression

> **Note (30 Oct):** this is research. The adopted cast and script are in [../demo_story/RUN_SHEET.md](../demo_story/RUN_SHEET.md): Amy (account manager) vs Rachel (MLRO) on Velquarry, Ken (contractor) on Velquarry vs Bayfront, Maya (compliance) for the journey and audit, Jordan via WorkBuddy. Priya below is an on-call SRE in the app, not an analyst.

Research date: 30 Oct 2026. Demo Day: 3 Nov 2026, Marina Bay Sands Expo, slot window 15:35-16:35.
Improves on the run sheet in `docs/finals/research/05_competition_pitch.md` (section 5).

Evidence labels used below: **[S]** = from a cited source; **[I]** = our inference or coaching practice, not a sourced fact; **[V]** = number or claim to re-verify against the repo before saying it on stage.

---

## 0. Read this first (the five decisions)

1. **The slot is tight.** 15:35-16:35 is 60 minutes for 10 finalists, so about 6 minutes each including changeover, which matches "5 min + 1 min Q&A" with almost no walk-up time [I, arithmetic]. The earlier sheet assumed a longer window (until 16:55). Plan for a **cold start: laptop already mirrored, app already logged in, first words within 5 seconds of being introduced.** Rehearse the 2 Nov changeover as part of the timing.
2. **Two live moments, not three.** Research and organiser advice both say: focus on the most important features, show Tencent products live, skip background. A 5-minute slot at ~130 wpm fits about 650 spoken words including narration of the demo. Three live scenarios plus a stack slide plus evidence plus ROI (the old sheet) is too much. New plan below: **one story, two live "wow" moments (same-question-three-people, tamper-the-log), one 25-second Tencent stack beat, one proof number, one ask.**
3. **Tagline said three times** (open, middle, close).
4. **Presenter mode in the app** (one key starts a scripted scenario on pre-warmed data) plus **a recorded fallback switchable in under 3 seconds.**
5. **Stand out against EdgeLab by showing what cannot be faked on stage:** a real log getting tampered and detected, and a refusal that looks identical to "does not exist".

---

## 1. What the research says (sources)

### 1.1 Pitch structures

| Source | What it says | Use for us |
|---|---|---|
| YC, Kevin Hale, "How to Pitch Your Startup" (Startup School video 77): chapters on being clear, examples, "X for Y", being concise. Summaries: investors ask "do I understand it, am I excited, do I like the team"; start with the problem, avoid jargon and buzzwords; Demo Day slots are about 2.5 minutes. [startupschool.org/videos/77](https://startupschool.org/videos/77), [summary](https://allcancode.com/blog/pitch-your-startup) [S, secondary summaries] | Understand / excited / like the team is the whole test. Our first 20 seconds must pass "do I understand it". |
| YC "A Guide to Demo Day Presentations" [ycombinator.com/blog/guide-to-demo-day-pitches](https://www.ycombinator.com/blog/guide-to-demo-day-pitches/) (found in search; page body not read in full) [S, not fully read] | Has a "Team" section. | Keep a team beat, short. |
| Techstars-style demo day advice (third-party blogs only, no official Techstars text found): 2-3 minute arc, roughly 300 spoken words for 2 minutes, hook > problem > solution > traction > market > team > ask; goal is a follow-up meeting, "entertain first, then impress, then inform". [Keen.io](https://keen.io/blog/how-to-write-your-demo-day-pitch/), [Stackmatix](https://www.stackmatix.com/blog/demo-day-pitch-checklist) [S, weak] | ~150 wpm planning figure; lead with proof (our "traction" is test evidence). The "ask" should tie the story together. |
| Sequoia business-plan outline (reproduced by many sites; original not retrieved): purpose in one plain sentence, problem, solution, why now, market, competition, product, business model, team. [PDF copy](https://fenix.tecnico.ulisboa.pt/downloadFile/1126518382322406/Sequoia%20Capital_pitchdecktemplate.pdf) [S, secondary] | "Purpose in one plain sentence" = our tagline. "Why now" = regulators and auditors now ask AI to prove its answers. |
| Guy Kawasaki 10/20/30: 10 slides, 20 minutes, 30pt minimum font. [Visible.vc](https://visible.vc/blog/guy-kawasaki-pitch-deck) [S] | Scale down: at most 3 slides in 5 minutes, nothing under 30pt on a slide; app text zoomed to match. |
| Hackathon demo advice (Reskilll, AngelHack, HackerEarth): about 3 min is typical; demo about half of runtime; show only working features; avoid code and terminal screens; record the demo as a backup (one says twice); expect Wi-Fi drops, rate limits, dead batteries; avoid localhost-only setups with flaky API calls; include a visible error-handling moment; walk through the demo repeatedly to find where it breaks. [Reskilll](https://blogs.reskilll.com/hackathon-demo-presentation-tips-pitch-3-minutes-win-2026/), [AngelHack](https://angelhack.com/blog/10-tips-to-help-you-rock-your-next-hackathon-demo/) [S, blogs, not formal rubrics] | Demo is ~60% of our 5 minutes. Our "error-handling moment" is the refusal. Rehearse until it never surprises us. |

The Devpost/MLH-specific guidance did not surface in search; the hackathon-blog findings above are generic and consistent with each other, not formal judge rubrics.

### 1.2 Pacing (words per minute)

- Common presentation guidance is 130-150 wpm. Measured TED talks run faster: a VirtualSpeech analysis of five talks averaged 173 wpm (range 154-201); a University of Sussex transcript study put TED at about 169 wpm against 145 for lectures. [VirtualSpeech](https://virtualspeech.com/blog/average-speaking-rate-and-words-per-minute), [Sussex](https://blogs.sussex.ac.uk/mah/files/2023/02/Matt-Walpole-transcript.pdf) [S, small samples; no peer-reviewed optimum found]
- **Our rule [I]:** script at **130 wpm** for spoken sections, and **leave silent beats** while the screen does the talking. At 130 wpm, 5:00 allows about 650 words total, but the live demo includes loading and reading time, so the script below is about **480 spoken words** plus ~90 seconds of on-screen action with sparse narration. This leaves 20-30 seconds of buffer. Overrunning is the most common failure; finishing at 4:45 is a win.
- Non-native-room factor [I]: the audience is international (Tencent, AISG, sponsors). Slower, plain English beats fast and clever.

### 1.3 Memory and impression

- Serial-position effect: first and last items are remembered best; for judging people, the primacy effect is stronger. Toastmasters warns presenters waste those slots on credentials, thanks and obligatory housekeeping. [Toastmasters 2025](https://toastmasters.org/magazine/magazine-issues/2025/april/maximize-your-speechs-beginnings-and-endings), [Critical Reasoning text](https://human.libretexts.org/Bookshelves/Philosophy/Logic_and_Reasoning/Critical_Reasoning:_A_User%27s_Manual_(Southworth_and_Swoyer)/08:_Memory_II-_Pitfalls_and_Remedies/8.09:_Primacy_and_Recency_Effects) [S]
- Implication [I]: **no "Hi we are team Walnut, thanks for having us".** Open with the scene; give the team name in the tagline line. Close on the tagline and the ask, not on a Q&A.
- TED-style story arc (hook, character, conflict, resolution) [I, standard storytelling practice, no single source fetched]: we map it as hook = analyst's question, character = Priya the new analyst (and the compliance officer who must prove it), conflict = the AI answers from everything it can read, resolution = answers you can prove.

### 1.4 Projector and venue practice

- Zoom the whole environment, not just the text; walk to the back of the room and check legibility; many instructors switch to a light theme on projectors (dark themes wash out), and prefer off-white over pure white to reduce glare; turn off notifications, close other apps and tabs; script the demo steps so they run predictably. [Substack on live coding](https://thinkingthroughteaching.substack.com/p/setting-up-to-live-code-in-class), [Carpentries tips](https://carpentries.org/blog/2016/04/tips-tricks-live-coding/), [Typeshare](https://typeshare.co/yabellini/posts/day-12-distilling-how-to-use-participatory-live-coding-in-person-and-online-tip-5), [Donn Felker](https://donnfelker.com/i-cant-read-your-screen-mr-presenter) [S, blogs; contrast advice varies; test in the room]
- Our Developer mode (graph, request journey) is dense. For the stage, **use a light "Stage" theme at 150% zoom, hide the graph minimap, and use large type** [I].

### 1.5 Past winners at Tencent Cloud events (thin evidence)

| Event | Winner | Memorable because | Source |
|---|---|---|---|
| Singapore station, May 2026 ("AI Coding Challenge") | 1st "Auntie AI" (Elena Xiao): phone-screen reader flags phishing texts for seniors, explains risk simply, warns in local dialects. 2nd "EyeDentify": low-cost wearable aid for visually impaired users. Judges: Tencent Cloud Chief Architect Michael Xu, SMU Associate Dean Feida Zhu, an AISG engineer; scored human-centered design, AI interaction, feasibility as well as technical execution. | A named human user, local relevance, a visible working product. | [AAP/PR Newswire](https://www.aap.com.au/aapreleases/cision20260513ae58232/) [S] |
| Hong Kong & Macau Demo Day, Aug 2026 | 1st HKUST "Anchor", 2nd HKU "Walking in Poetry", 3rd "LOONG SEASON". Themes: social good, cultural expression, narrative innovation. | Cultural and human stories; that track was a different brief (creative). | [AAP](https://www.aap.com.au/aapreleases/cision20260819ae29301/) [S] |
| AISG National AI Student Challenge | Public criteria: impact, novelty, performance/efficiency, presentation (2024). | Presentation is an explicit scored criterion. | [AISG NAISC 2024](https://learn.aisingapore.org/naisc2024/) [S] |
| Global finals / AISG demo day winner lists | Not found. | n/a | [V] |

Lessons [I]: the winners were **people-first, not architecture-first.** Our product is enterprise infrastructure, so the person must be on screen within 10 seconds, and the demo must show a **visible, emotional moment** (a blocked answer, a broken chain), not tables. The Tencent judges' scoring mentions "AI interaction" and "feasibility": the three-person demo covers interaction, the evidence line covers feasibility.

---

## 2. Criterion E1: Demo & Storytelling

### 2.1 What judges look for

1. Clarity in the first 20 seconds: what is it, for whom, why now (YC test: understand / excited / like the team). [S]
2. A working, live product on a real path, not slides about one. Organisers asked specifically for live Tencent products and the most important features only. [Brief]
3. A story with a person, a conflict and a payoff, in business-pitch order, no background section. [Brief]
4. Control of time, and grace under failure ("judges are more impressed by teams that handle glitches gracefully", Reskilll). [S, blog]
5. Legibility from the back of a large expo hall. [S, blogs]
6. Roles: the audience can tell who the founder, the builder, and the closer are. [I]

### 2.2 What winners did

- Opened on a person and a pain (Auntie AI: a senior and a scam text). [S, press-release level]
- Demonstrated one flow end-to-end rather than a feature tour. [I, consistent with hackathon advice]
- Used plain language for the explanation (Auntie AI's "explains the risk simply"). [S]
- Kept a recorded backup and switched without panic. [S, blogs]

### 2.3 Our concrete deliverables (E1)

All paths under `E:\Tenecent Hack_Walnut\Tencent\docs\finals\` unless noted. Rubric mapping in brackets.

| # | File | Contents | Rubric |
|---|---|---|---|
| E1.1 | `pitch/runsheet_5min.md` | The 5:00 sheet in section 3 below, second-level timing, exact script, cue column (who/what/screen). | Demo & Storytelling |
| E1.2 | `pitch/choreography.md` | Section 4: who speaks, who clicks, where each stands, hand-off words. | Demo & Storytelling, Overall |
| E1.3 | the presenter slides (route `/present`, see `docs/finals/rubric/demo_story/RUN_SHEET.md`) (3 slides max, 40pt+, light theme, one image each): S1 tagline + Priya, S2 Tencent stack (Hy3 / TokenHub / WorkBuddy-CodeBuddy / Tencent Cloud), S3 proof numbers + QR + ask. | Overall |
| E1.4 | App: **Presenter path** (section 5), `databridge/apps/internal_brain_web/` route `/stage`, key `P` to start, `Space` to advance, `B` to flip to fallback, `R` to reset. | Demo, Technical impression |
| E1.5 | `pitch/fallback/` : `demo_full_1080p.mp4` (the whole 5:00 as a voiced-over screen recording), `demo_clip_A_three_people.mp4`, `demo_clip_B_tamper.mp4`, `demo_clip_C_workbuddy.mp4` (each 25-40 s, silent, so we narrate live), `still_A.png`, `still_B.png` (static screenshots as last resort). Plan in section 6. | Demo (risk) |
| E1.6 | `pitch/leave_behind.pdf`: one A4 page (section 9). | Overall |
| E1.7 | `pitch/qa_bank.md`: 25 questions with ~15-second answers (section 8). | Overall |
| E1.8 | `pitch/rehearsal_checklist_2nov.md` (section 10) and `pitch/venue_tech_checklist.md` (section 7). | Demo (risk) |

---

## 3. The 5:00 run sheet (replaces section 5 of file 05)

Story: **Priya, a new analyst on day one, asks the company AI a question she should not be able to get answered, and the compliance officer later has to prove what the AI said.** Tagline (three repetitions marked **T**): **"Answers you can prove."**

Word count is about 480 spoken. Times are cumulative. "[S]" means a slide; "[LIVE]" means the real app via Presenter path; "[VID]" means the fallback clip.

| Time | Who | Beat | Exact script (spoken) | Screen / click | Rubric |
|---|---|---|---|---|---|
| 0:00-0:12 | Ishaan | Hook (no greeting) | "Day one. Priya joins a fintech as an analyst. She asks the company AI one question: 'What is the CEO's salary?' Watch what a normal assistant does." | [S1] Priya photo/illustration, then cut to [LIVE] a plain chatbot answering with a figure from a leaked doc (pre-recorded generic bot is acceptable; label "generic assistant"). | First 15 s |
| 0:12-0:25 | Ishaan | Problem + tagline **T1** | "It answered, because it can read everything. And later, nobody can prove what it said. We built the opposite: Walnut DataBridge, with Internal Brain. **Answers you can prove.**" | [S1] tagline appears. | Clarity, Overall |
| 0:25-1:25 | Dev (driver) with Ishaan narrating | LIVE 1: same question, three people | Ishaan: "Same question, three people." Dev presses `P` (Presenter path, step 1). Narration over actions: "Priya, the analyst: it says it cannot help with that, in plain words, and offers what she can see. Her manager: a cited answer from a document she is allowed to open. HR: the full answer, with the exact source. Notice it never says 'access denied' or counts what was hidden. To Priya, the secret simply is not there." | Three accounts switch in one click each (pre-logged tabs or a role switcher); cited answer with source chip; model label "Hy3 via TokenHub" visible in the answer footer. 20 s per person. | AI interaction, HCD |
| 1:25-2:20 | Mehul (compliance voice) / Dev clicks | LIVE 2: the proof | Mehul: "Now I am the compliance officer. Every question, every decision, every answer is written to a log before the answer is released. Each entry is chained to the one before." Dev edits one line in the log file or presses Presenter step 2 'tamper'. Mehul: "I change one word, from 'denied' to 'allowed'. Verify." Red banner: chain broken at entry 14. Mehul: "**Answers you can prove.** That is a log a regulator can trust." **T2** | Log view (Developer mode "request journey" for the one request, then audit chain view); tamper button; verify button; red break at the exact entry. | Responsible AI, Innovation, Technical |
| 2:20-2:50 | Aaruni | Tencent beat (live-ish) | "Tencent is inside the product, not just on our slide. Hy3 on TokenHub writes the answers. WorkBuddy is a second front door: same identity, same limits, same log." | [LIVE] ask the same question from WorkBuddy via the MCP connector, or [VID C] if network is poor; then [S2] stack slide for 5 s. | Tencent products, Innovation |
| 2:50-3:25 | Aaruni | Proof | "We did not just demo it, we attacked it. 7,374 adversarial requests, zero leaks. Four hundred paired questions asked as two roles, zero differences. Mapped to MAS TRM controls." **[V: re-check each number against `databridge/evals/internal_brain/` outputs the morning of 3 Nov.]** | [S3] three large numbers only. | Technical, Responsible AI |
| 3:25-4:00 | Ishaan | Business value + why now | "Regulated companies want to switch AI on and cannot, because they cannot prove what it did. Our base case for a 600-person fintech is about 1.3 million US dollars a year; it is an estimate, with the inputs on our leave-behind. The design targets Tencent Cloud (deploy kit included; no live instance), and Hy3 is open-weight, so a bank could host it in-house." **[V: ROI figure and assumptions]** | [S3] stays up. | Feasibility, Impact |
| 4:00-4:40 | Ishaan | Team and traction | "We are Ishaan, Dev, Mehul and Aaruni. Built with CodeBuddy and a Hy3 security review. The same engine, Walnut, already runs for healthcare and government; Internal Brain is its newest pack." **[V: only say this if the repo claims support it]** | [S3] team line (names, roles in 6 words each). | Overall |
| 4:40-5:00 | Ishaan | Close + ask **T3** | "A company's AI will be asked to prove itself. Walnut DataBridge: Internal Brain: **answers you can prove.** We are looking for a pilot with one team at a regulated company, and Tencent Cloud credits to host it. Scan to try it." | [S3] QR + tagline. Stop talking. Smile. | Overall, Impact |
| 5:00-6:00 | Q&A | Section 8 | Ishaan fields, Dev for technical, Mehul for compliance, Aaruni for Tencent stack. | Hands off the keyboard; Dev keeps the app on the Developer view. | Overall |

Buffers: if running long, **cut the 4:00-4:40 team beat first** (the names are on the leave-behind and on S3), then cut the WorkBuddy beat to 15 s.
Dependencies called out for the product team: a role switcher or three pre-authenticated tabs, a tamper-and-verify control, a visible model label, and a plain-bot comparison asset. If any is missing, the beat degrades to the matching video clip.

### 3.1 Alternate opener if we present immediately after EdgeLab

Presentation order is drawn by lots on 2 Nov. Prepare two first sentences, same second sentence:
- **Neutral opener (default):** as above.
- **After an Internal Brain:** "You just saw an Internal Brain. We asked a different question: not 'can it answer', but 'can it prove what it answered'." Then continue at 0:12. Never name them or criticise [I].

---

## 4. Stage choreography

| Person | Role on stage | Speaks | Hands | Position |
|---|---|---|---|---|
| Ishaan | Storyteller and closer (founder voice) | Hook, tagline, business, close, Q&A lead | Holds the clicker only for slides; never touches the demo laptop | Stage left, facing the audience, eye contact rotates across three zones of the room (left, centre, right) and the judges' table |
| Dev | Driver ("the hands") | Silent during LIVE 1 except a 3-word confirmation ("Priya. Manager. HR."); answers technical Q&A | Only person on the demo laptop; owns the Presenter path and fallback key | Seated or standing at the laptop, screen visible to judges |
| Mehul | Compliance/risk voice | LIVE 2 narration, regulator and MAS questions | None | Stage right near the screen |
| Aaruni | Tencent stack and evidence voice | Tencent beat, proof numbers, WorkBuddy/Hy3 questions | Starts the WorkBuddy action if live | Centre-right |

Rules [I]:
- **One speaker at a time**, hand-off by name and a phrase ("Mehul, over to the compliance officer's chair").
- Speakers look at the **judges first, screen second**. Point with an open hand, do not turn your back.
- Dev says out loud what he clicks ("I am switching to Priya") so the audience tracks, and so the narration survives a lag.
- Whoever is not speaking stands still, hands visible, looking at the speaker. Judges note team cohesion.
- If someone blanks, Ishaan says the next line's first four words as a bridge.

---

## 5. The demo "Presenter path" (spec for the app team)

Goal: one key starts a scripted scenario on **pre-warmed, deterministic data**, so the live demo is repeatable and fast.

| Feature | Behaviour | Why |
|---|---|---|
| Route `/stage` | Light Stage theme, 150% scale, hides nav, Developer mode panels collapsed by default except "request journey" and "audit chain". Fonts at 24px+ body, 40px+ answers. | Projector legibility |
| `P` start | Logs in as Priya (token pre-minted for demo accounts, `IB_DEMO_LOGIN=1`), focuses the ask box. | One-click start |
| Step 1 `Space` | Types the question into the box at a human pace (or pastes instantly: configurable), submits, shows the answer. Next press switches to the manager token and repeats, then HR. | Same question, three people in about 60 s |
| Step 2 | Opens the audit chain view, highlights the three entries just written, shows "Verified". Press again to **tamper**: edits one entry in a **sandbox copy of the log** (never the real ledger), then Verify shows the break at the entry number. | The memorable proof |
| `B` fallback | Full-screen overlay plays the local video clip for the current step, muted, from a local file; `B` again returns to live. Latency target under 300 ms, no network. | 3-second fallback |
| `R` reset | Restores the sandbox log and re-warms the model call. | Between rehearsals and runs |
| Warm-up | On load, the page sends one throwaway ask per role so caches, TLS and the model are warm. A status dot shows green/amber/red for: API, Hy3/TokenHub reachability, ledger. | Failure early warning |
| Determinism | A "demo seed" with fixed documents and fixed sample answers; the model call has a 4-second timeout, then serves the extractive answer (this is the product's own behaviour per `CLAUDE.md` rule 5, so it is honest). | No dead air |
| Honesty switch | Footer label "Demo data: synthetic Company A". | Constraint 7 |

Acceptance test: from a cold browser, `P` then `Space` x6 completes the whole live segment in under 100 seconds with no typing, and `B` at any step swaps to the clip in under 3 seconds.

---

## 6. Failure-proofing and the backup plan

### 6.1 Risk table

| Risk | Likelihood [I] | Mitigation | Switch time |
|---|---|---|---|
| Venue Wi-Fi fails or congested (large expo) | High | Do not depend on it. Run everything locally except the Hy3 call; phone **hotspot** (primary SIM) on laptop A, a **second carrier** hotspot on laptop B; Hy3 call has 4 s timeout then extractive answer. | 0 s (automatic) |
| TokenHub/Hy3 slow or rate-limited | Medium | Pre-warm; cached answer mode for the demo seed; show model label only when live. | 0 s (automatic) |
| Laptop crash / display adapter fails | Low-medium | **Two laptops** with identical builds, both mirrored to a splitter or both carrying the same adapter set; spare USB-C-to-HDMI and USB-C-to-DisplayPort; laptop B on the same demo seed. | 10-20 s swap cable |
| Browser or app error mid-demo | Medium | `B` key: local video overlay. Also a second browser window pre-opened at each step. | Under 3 s |
| Projector resolution or aspect problem (16:9 vs 16:10 vs 4:3) | Medium | Set laptop to 1920x1080 and test at the rehearsal; design `/stage` for 1280x720 safe area; no content in outer 5%. | n/a |
| Audio blare / notification | Medium | Do Not Disturb, airplane mode except hotspot, close mail/chat/updaters. | n/a |
| Dead battery | Low | Mains plugged in on stage, battery above 90%, power adapter, extension cable. | n/a |
| Lost voice / stage fright | Medium | Ishaan has the script on a phone, face-up; teammates have next-line cards. | n/a |

### 6.2 Fallback hierarchy (pick the lowest rung that works; switch decisions made by Dev, announced by Ishaan)

1. **Live, normal.**
2. **Live with cached answers** (automatic when Hy3 times out; no announcement).
3. **Local video clip for the current step** (`B`). Ishaan says: "Let me show you the recording from this morning; same system, same data." Never apologise at length [S, blogs: handle glitches gracefully].
4. **Full 5:00 video** (laptop B, preloaded in a player on the second screen, paused at 0:00). Used only if the whole app is dead.
5. **Still screenshots** (`still_A.png`, `still_B.png`) and spoken explanation.

Honesty rule: when a recording is shown, say so in one clause. Judges will forgive a clip; they will not forgive being passed off as live.

### 6.3 Recording plan (`pitch/fallback/`)

- Record at 1080p, 30 fps, cursor highlighted, with the exact Stage theme, using the real build on the real seed on **2 Nov** after the final build is frozen; re-record if any UI changes later.
- Record the full sequence once, and each clip separately. Export H.264, under 200 MB each, copy to both laptops and a USB stick and a phone.
- Test playback in the same player that will be on stage, with audio off.

---

## 7. Venue tech checklist (`pitch/venue_tech_checklist.md`)

Marina Bay Sands Expo is a large conference floor. Treat the room as unknown until the rehearsal.

**Display**
- [ ] Ask the AV desk on 2 Nov: resolution, aspect ratio, HDMI or other input, whether we get a clicker and a lectern mic, and confirm the screen's visible size from the back.
- [ ] Set laptop display to 1920x1080, mirrored (not extended), scaling at 100%, then browser zoom at **150%**; check that nothing is cut off at the edges.
- [ ] Walk to the back row and read a 24-pixel line of text and a source-chip label. If you cannot, zoom more.
- [ ] **Light Stage theme** is default; also test the dark theme on the venue projector and keep whichever reads better. [S, blogs: light recommended, but verify; conflicting views]
- [ ] Disable screensaver, sleep and notifications; hide bookmarks bar; one browser window, a few pre-opened tabs; full-screen (F11).

**Network**
- [ ] Test venue Wi-Fi at the stage position at the hour of the event (lots of devices at 15:30); do not rely on it.
- [ ] Two hotspots, different carriers, both tested at the stage; laptop A on hotspot 1, laptop B on hotspot 2; Wi-Fi forgotten on both laptops (so they do not hop).
- [ ] Hy3/TokenHub reachability test from the stage; note latency.
- [ ] Offline mode check: kill the network and run the whole Presenter path; the only difference should be the cached model answer and a dimmed model label.

**Equipment**
- [ ] Two laptops, each with charger, extension lead; adapters (USB-C to HDMI, DisplayPort, a mini-DisplayPort if the AV desk has odd inputs); a spare USB-C cable.
- [ ] Clicker with spare batteries; confirm it works on laptop A and B.
- [ ] Phone with the script, the 5:00 video, the QR code image; power bank.
- [ ] USB stick with all media and slides; PDF versions of the 3 slides.
- [ ] Printed 1-page leave-behind (50 copies) and printed QR on a small stand.

**Audio** (if we play any sound): ask for a line-in test; default is silent clips.

**Slot logistics**
- [ ] Where do we stand while the previous team clears? Who plugs in? Aim for a 30-second changeover with the app already open.
- [ ] Identify the timekeeper's signal (a card, a light) and agree who watches it (Aaruni).

---

## 8. Q&A bank (`pitch/qa_bank.md`): 25 questions, ~15-second answers

Use the **bridge**: answer in one sentence, then connect to the tagline. If you do not know, say "I do not know; here is how we would find out." Never bluff; every claim must map to a test or file (`CLAUDE.md` rule 8). **[V]** marks numbers to re-verify. Technical answers go to Dev, compliance to Mehul, Tencent stack to Aaruni, business to Ishaan.

| # | Question | 15-second answer |
|---|---|---|
| 1 | How do you know it does not leak? | "We attacked it: 7,374 adversarial requests, zero leaks, and 400 paired questions asked as two roles, zero differences. No test proves forever; the evals are in the repo and anyone can rerun them." [V] |
| 2 | Is the permission check in the model? | "No. The check runs before the model: documents the user cannot open never enter the prompt. The model cannot reveal what it was never given." |
| 3 | What if the source system's permissions change? | "A second live check at the source before release; any error or timeout means deny." |
| 4 | What does the audit trail give a regulator? | "Every ask, retrieval decision and answer with user, role and time, hash-chained so an edit is detectable. You saw tamper detection." |
| 5 | How does this map to MAS TRM? | "Controls are mapped to access control, logging, data protection and third-party/AI use in a document in the repo. It supports a bank's own assessment; it does not replace it." |
| 6 | What is the 1.3 million based on? | "Base case for 600 staff: search time, permission incidents avoided, audit preparation, with assumptions on our one-pager. It is an estimate, not a measurement." [V] |
| 7 | Why Hy3? | "Strong price and 256K context, open weights so a bank can self-host, and live on TokenHub. The design is model-agnostic." (Facts from file 05) |
| 8 | What about PDPA and data residency? | "Data stays in the customer's tenant; only retrieved snippets reach the model; open weights allow fully private hosting." |
| 9 | How is it different from the other Internal Brain? | "We lead with proof: measured leak tests, a tamper-evident log you watched break, and a second front door through WorkBuddy. Both of us are solving a hard challenge; this is the part we focused on." Never criticise. |
| 10 | What if the model hallucinates? | "Answers must cite permitted documents; with no source it says so; unsupported output is replaced by a deterministic extractive answer. Refusals are logged." |
| 11 | What did you build versus reuse? | "Reused: the Walnut evidence engine, unchanged. Built this weekend-scale: the Internal Brain service, connectors, web app and evals. CodeBuddy sped the building; we say what is generated in the repo." (be exact; avoid claiming more) |
| 12 | Cost per query? | "Hy3 list price is about 1 yuan per million input tokens and 4 per million output; a cited answer is a few thousand tokens, so a fraction of a cent." (file 05; check current price) [V] |
| 13 | How would Aspire adopt it? | "Connect to their identity groups, pilot with one team such as support, then expand. Next: SSO/SCIM and more connectors." |
| 14 | Can it scale beyond four sources? | "Connectors are the only per-platform code; the engine has no domain logic, and a test enforces that." |
| 15 | What about prompt injection in documents? | "Retrieved text is treated as data; the adversarial set includes injected documents; the permission gate is outside the model, so injection cannot widen access." |
| 16 | Why does Priya see nothing instead of 'access denied'? | "Because a denial leaks that the secret exists. One response shape: restricted looks the same as nonexistent." |
| 17 | Who audits the auditors / can an admin edit the log? | "Admins can append, not edit silently: any edit breaks the chain, and the sealed head hash can be anchored externally." (state only what the code does) [V] |
| 18 | How did you use Tencent products? | "Hy3 via TokenHub for answers, WorkBuddy as a second front door via MCP, CodeBuddy to build and review, Tencent Cloud for hosting." (be exact on what is live) |
| 19 | What is the hardest unsolved problem? | "Permission drift at the source and group-membership latency. We fail closed; a real deployment needs SCIM and event-driven revocation." |
| 20 | Is it just RAG with a filter? | "The filter is the easy part. The hard parts are the live source check, one-shape responses, the grounding wall and the tamper-evident log, each with tests." |
| 21 | What about multi-language or Singapore English/Chinese? | "Retrieval and the model handle multilingual text; our tests are English today. Honest limit." [V] |
| 22 | What happens if the ledger is down? | "The system returns 503. No ledger, no answer; write-ahead audit is a hard rule." |
| 23 | What is your business model? | "Per-seat licence for knowledge teams plus a deployment fee for regulated customers; the open-weight option lowers hosting objections." [I: confirm with team before saying] |
| 24 | Who is the first customer? | "A regulated fintech like Aspire's own partner banks. We are asking for one pilot team." |
| 25 | What would you do with a year? | "Ship SSO/SCIM, an admin review dashboard, more connectors, and an independent security assessment." |

Q&A technique [I]: pause one beat, look at the asker, repeat the question in six words (buys time, helps the room), answer in one sentence, stop. If the question is hostile, thank them and answer the part you can prove. Do not name the other team. Hand off by name ("Dev, can you add the detail?").

---

## 9. One-page leave-behind (`pitch/leave_behind.pdf`)

A4, light theme, Walnut logo and colours, large type. Sections in order:
1. Title: **Walnut DataBridge. Internal Brain. Answers you can prove.**
2. One sentence: what it is, for whom.
3. Three screenshots: the three-person answer, the refusal, the broken chain.
4. Three proof numbers with source file/test names. [V]
5. The Tencent stack line: Hy3 on TokenHub, WorkBuddy MCP, CodeBuddy, Tencent Cloud.
6. ROI base case with the four assumptions, labelled "estimate".
7. QR to a 60-second video and the live demo URL; repo/INTERNAL_BRAIN.md pointer.
8. Team names and roles. Ask: "pilot with one regulated team".
Do not list anything not proven by a test or file:line (`CLAUDE.md` rule 8).

---

## 10. Rehearsal checklist for 2 Nov (`pitch/rehearsal_checklist_2nov.md`)

Order matters: freeze the build, then record, then rehearse.

**Before the rehearsal (1 Nov evening)**
- [ ] Build frozen; tests green; evals re-run and numbers on S3 updated from the latest output. [V]
- [ ] Presenter path passes its acceptance test on laptop A and B.
- [ ] Videos recorded (section 6.3), copied to both laptops, USB, phone.
- [ ] Printed leave-behinds; QR works from 3 metres.

**At the rehearsal (2 Nov)**
- [ ] Draw the lot: note presentation position. Pick opener variant (neutral or "after an Internal Brain").
- [ ] Walk on from the actual entry to the actual spot; time the changeover (target under 30 seconds to first word).
- [ ] Check projector: resolution, text at the back, colour, the Stage theme in the actual light.
- [ ] Network test at the stage; run offline-mode demo once.
- [ ] Do **three timed full runs with a stopwatch**: pass = 4:40 to 5:00, no run over 5:00.
- [ ] Do one **forced-failure run**: Dev kills the network at LIVE 1, presses `B`; check that Ishaan's recovery line is smooth. Do a second where the laptop is swapped.
- [ ] Do one **Q&A drill**: a teammate asks 10 random questions from the bank; stop at 1:00.
- [ ] Record the rehearsal on a phone, watch it, and note any "um"s, back-turns, and rushed lines.
- [ ] Agree timekeeper signals and who watches the clock (Aaruni).
- [ ] Agree what to do if a judge interrupts mid-demo: Ishaan answers in one sentence and says "let me finish this step, then I will come back".
- [ ] Dress and badge plan; water; no new tools or UI changes after rehearsal.

**3 Nov morning**
- [ ] Pre-warm: log in, run the Presenter path once, check status dots.
- [ ] Charge everything; do a final video playback test.
- [ ] Final read of the script out loud, once, then stop practising.

---

## 11. The judge-impression layer (Overall Quality & Judge's Impression)

### 11.1 What judges look for [I where unsourced]

- A project that feels finished and credible: working demo, honest numbers, clear limits.
- A team that is clear, prepared, and coordinated.
- Fit with the organisers' emphasis: Tencent products used live, real-business framing, human-centered design, feasibility. [S: Singapore station criteria]
- Something they will remember and can repeat to a colleague in one sentence.

### 11.2 What winners did [S/I]

- Gave judges a repeatable one-liner (Auntie AI: "a scam-protection co-pilot for seniors" is easy to retell). [S, press release language]
- Chose a person, not a system, as the hero. [S]
- Used visible, plain-language outputs. [S]

### 11.3 Concrete deliverables

| Deliverable | Detail | Impression effect |
|---|---|---|
| First 15 seconds | Scripted in 3: person, question, normal bot leaks. No greeting, no credentials. | Primacy effect [S: Toastmasters] |
| Tagline x3 | "Answers you can prove." at 0:25, 2:15, 4:50. Same words each time. | Memory |
| Closing ask | Pilot with one regulated team, hosting credits, scan QR. Last words are the tagline. | Recency effect |
| Eye contact | Judges 60%, room 30%, screen 10% during speaking; rotate three zones. | Trust |
| Slides vs live | About 20% slides (3 slides, under 60 s total), about 60% live app, about 20% speaking to camera-less audience. | Credibility |
| Role split | Section 4. | Team cohesion |
| Q&A | Section 8; hand-offs by name; no naming EdgeLab. | Confidence |
| Leave-behind | Section 9, handed to judges and sponsors after. | Recall |

### 11.4 The single memorable line (options)

Pick one and say it three times. Ranked by my judgement [I]:

1. **"Answers you can prove."** (Short, cadence of three beats, covers permission + citation + audit. Recommended.)
2. "The AI that can show its work."
3. "Your company's AI should be able to say no, and prove it."
4. "Trust is what turns AI on." (Matches the earlier close in file 05.)
5. "Ask anything. See only what you are allowed. Prove everything."

### 11.5 Standing out against the other finalist (EdgeLab, "InternalBrain", same Aspire challenge)

I have not seen their product; differentiate on what is demonstrable [I].

| Lever | How we show it |
|---|---|
| Different job to be done | They answer; we **prove**. Open with Priya and the tamper demo. |
| Name | Say "Walnut DataBridge" before "Internal Brain"; keep logo and colours on every screen. |
| A moment they cannot copy in a day | The hash-chain break, live. |
| Plain-language refusal | The refusal indistinguishable from "does not exist". |
| Two front doors | Web app plus WorkBuddy MCP: deeper Tencent integration than a single UI. |
| Evidence | Three large numbers with a test behind them. |
| Regulation | MAS TRM mapping, spoken once. |
| Business | One ROI number with stated inputs. |
| Order | Two openers (3.1). If we go second, acknowledge neutrally; if first, set the standard ("proof, not claims"). |
| Tone | Gracious. Never criticise; let the demo do it. |

### 11.6 Rubric map (summary)

| Rubric area | Deliverables |
|---|---|
| Demo & Storytelling | E1.1-E1.8, run sheet, choreography, Presenter path, fallback video, Q&A bank, rehearsal and venue checklists |
| Overall Quality & Judge's Impression | Tagline x3, first-15-second hook, closing ask, leave-behind, role split, Q&A, honest limits ([V] tags), "do not overclaim" discipline |

---

## 12. Gaps and cautions

- Official Techstars, Devpost and MLH judging guidance was not found in search; hackathon advice above comes from blogs (Reskilll, AngelHack, HackerEarth) and is consistent but non-authoritative.
- The YC Hale lecture and the Sequoia outline were read through secondary summaries, not the primary text.
- No public winners list or judge feedback exists for the global finals or AISG demo days; the Tencent past-winner evidence is two press releases.
- Projector contrast advice conflicts between sources; **decide on the actual venue projector at the 2 Nov rehearsal.**
- All numbers marked [V] (7,374 / 0 leaks, 400 paired, test counts, ROI, MAS TRM mapping, "already runs for healthcare and government") must be re-checked against the repo outputs on the morning of 3 Nov; delete any that cannot be traced to a file or test.
- Tencent products live on stage depend on network and credentials; the WorkBuddy beat has a recorded fallback and can be cut first for time.
- The slot arithmetic (60 minutes, 10 teams) comes from the user's brief; file 05 quoted a longer window from the Luma page. Confirm with organisers at rehearsal.
