# Plan review (Fable): how to win Demo Day with this plan

Reviewer: Fable 5.1, 30 Oct 2026. Read: `PLAN.md`, `DECISIONS.md`, `research/01, 02, 04, 05`,
`rubric/research/B, E, F`, plus (for context only) the rubric table in `docs/tencent/SUBMISSION.md`,
the five Aspire scenarios, and the internal `COMPETITIVE_NOTES.md`. Research `03` and rubric `A, C, D`
had not arrived. Read-only; this file is the only output.

Scoring convention: "as written" is my estimate of the score if the plan ships exactly as drafted and
Hy3 works on the day. "With the change" assumes the one addition named in that row. A judge scores on
what they **see in five minutes plus one minute of questions**, so every row ends with the artefact.

## 0. The verdict in six lines

1. The product thesis is already a winner: *"restricted" is indistinguishable from "does not exist",
   proven, and every answer is sealed before it is shown.* No team can build that in a weekend. The
   plan's danger is not the thesis; it is **scope**: two modes, 12 pages, a graph, an engine embed,
   analytics, i18n, a tour and ten backend items in about 60 hours. The likely failure is everything at
   70 %, and judges score the 30 % that is missing.
2. The demo in §7 is a **feature tour**. It should be a **story with two proofs**. Rewritten in §3.
3. The plan has **no human evidence** for Human-Centered Design. A two-hour hallway test with five
   people and a three-row fix log is the cheapest point on the board.
4. **Ask Aspire's own question on stage.** The sponsor's Head of AI said "what can we offer this
   customer" takes time today. The Velquarry / Bayfront pair hits Aspire scenarios 2 and 4 and the
   tipping-off law in one minute. The DB-migration question belongs in the WorkBuddy beat.
5. **Hy3 live is the single point of failure for AI Interaction and the Tencent beat.** WorkBuddy is
   the insurance: it runs Hy3 natively, so the WorkBuddy → MCP → Brain path shows Tencent AI live even
   if the Brain's own TokenHub call is still returning 402.
6. The design direction (Linear/Vercel restraint, Palantir density) is right **only if** three hero
   visuals carry the screen: the split-screen identical refusal, the request journey with the chain
   turning red, and the graph greying under "View as". Restraint without heroes reads as unfinished.

## 1. Scores by criterion

| # | Criterion | As written | With the change | The one highest-leverage change | What the judge must SEE |
|---|---|---|---|---|---|
| 1 | Impact & Relevance | 7 | 9 | Demo **Aspire's own question**: Amy (account manager) and Rachel (MLRO) ask "Why is Velquarry Trading's account restricted?" side by side. Say the tipping-off line once (CDSA s.57: revealing that a report exists is an offence). Keep the ROI to one number with its inputs on the leave-behind, labelled "estimate". | The Compare screen: Amy gets the approved customer script, Rachel gets the case, both cited. Then one slide line: "about 7 min saved per person per week is break-even; base case US$1.3M/yr for 600 staff (estimate)". |
| 2 | Human-Centered Design | 6 | 8 | **Run a 5-person hallway test on Saturday** (research B §3, two hours, four tasks, SUS) and keep a **fix log of three real changes**. Say one sentence on stage: "Five people tried it; one broke the source sheet; here is the fix." Nothing else in the plan is evidence that a human shaped the product. | A single "What testers changed" card (before/after screenshot, tester quote, commit) in `/dev/trust` or `/settings`, and the persona-as-sign-in screen with each person's "can open" line. |
| 3 | AI Interaction | 7 (5 if Hy3 is still down) | 9 | **Get Hy3 answering on Friday, record it, and name the model on every answer footer** ("Hy3 via TokenHub · 2.4 s"). Make the staged status line honest: emit stage events from the backend (the pipeline already records `stage_ms`) and hold the text until grounding passes (keeps D-08). Elapsed-time theatre is the one place a sharp judge can catch "is that real?". | Answer footer with model, latency and "verified citations"; the WorkBuddy window calling `ask_internal_brain` live with the same identity; three follow-up chips that are specific to the answer. |
| 4 | Technical Execution | 8 | 9 | **The request journey must be real to the millisecond**: waterfall from `stage_ms`, the three ledger records with `seq` and the first 12 hex of each hash, the model used, the grounding result. Show the test count from a run on Sunday, not from the docs (`645` is a doc claim until re-run). | `/dev/journey/[req]` for the request the audience just watched, opened in under two seconds. The audit console row that appears when the WorkBuddy question lands (channel `workbuddy-mcp`). |
| 5 | Feasibility | 7 | 8 | **A "Run it at Aspire" card**: three steps (connect identity groups, pilot one team, expand), cost per 1,000 cited answers from Hy3 list price and real token counts if the ledger has them, "engine reused unmodified" (`git diff --stat` against the base), and the honest line "connectors are real-API-shaped; the first live tenant is the pilot". Deployment on Lighthouse is optional (D-03); the pilot plan is what feasibility judges remember. | One card on the proof slide or in `/dev/trust`; the Hy3 price line; the pilot ask in the close. |
| 6 | Demo & Storytelling | 6 | 9 | **Replace the tour with the story in §3.** Open live at 0:05 with a person and a question, two proof moments (identical refusal; chain breaks), one Tencent beat, one proof slide, one business beat, tagline three times, finish at 4:50. Cut the generic-chatbot prop (see §3.3). | Presenter mode (`P`, `Space`, `B`, `R`), a run sheet with second-level cues, and three rehearsed timed runs on 2 Nov. |
| 7 | Innovation & Creativity | 7 | 9 | **Make "restricted ≡ non-existent" visible to the eye.** Ken (contractor) asks about Velquarry (real, restricted) and about Bayfront Spice Traders (fictional) in the two Compare columns: two identical cards. Then "View as Ken" on the graph greys the nodes with locks. These are the two visuals no rival can produce without the differential property. | Compare screen with two identical refusals; `/dev/graph` with "View as" greying nodes. One spoken sentence: "0 differences in 400 paired questions". |
| 8 | UX & Accessibility | 7 | 8 | **Show accessibility, do not claim it**: the driver runs the whole demo keyboard-only and the speaker says so once ("Dev has not touched the mouse"); flip the UI to 简体中文 for three seconds during the WorkBuddy wait. Fix the two likely WCAG 2.2 failures first (2.5.7 drag alternative on the graph; 2.4.11 sticky composer covering focus). Publish the evidence table with honest gaps. | `/settings` with text size, contrast, reduced motion, language; an `/accessibility` statement page with the per-criterion evidence table; the language flip on screen. |
| 9 | Responsible AI & Ethics | 8 | 9–10 | **Re-run the eval with Hy3 in the loop** (even `--limit` first, full run overnight) so "0 leaks" is no longer "with a fake model". Then the tamper demo on a **sandbox copy**: one word flipped, Verify, red at the exact `seq`. Explain the external anchor in one sentence ("the head hash is published off-host every 15 minutes, so even the key holder cannot rewrite history unseen"). | `/dev/audit` Verify → green; tamper → red at `seq N`; `/dev/analytics` eval panel with the run date and model named; `/dev/trust` with the MAS TRM / FEAT / OWASP LLM rows. |
| 10 | Overall Quality & Judge's Impression | 7 | 9 | **Freeze scope Saturday 22:00.** Pixel-clean the five screens on the demo path at 150 % zoom (login, compare, sources sheet, journey, audit). Everything off-path ships as "present, unpolished, not shown" or is cut. One brand mark, one accent, tabular numerals, no default React Flow chrome. | The five demo screens, the proof slide, the one-page leave-behind, and a team that hands off by name and finishes at 4:50. |

Likely total as written: **70 / 100**. With the ten changes: **86–88 / 100**. The gap between those two
numbers is almost entirely demo craft, human evidence and Hy3 live, not more features.

## 2. Where the plan is strong (do not touch)

1. The seven hard rules in §2, especially rule 4 and D-07's rejection of "N hidden" counts. Research B
   (H7) and research 02 (§1.2) both suggested a hidden-source notice. Keep rejecting it; it is the
   core proof and the tipping-off story depends on it.
2. D-08 (no token streaming before grounding). Right call, and a differentiator you can say out loud:
   "nothing unverified ever reaches the screen".
3. The write-ahead ledger with per-stage timings (B1–B4, done). The journey view will be the most
   credible 20 seconds of the demo for the CodeBuddy and WorkBuddy judges.
4. Seeded, labelled replay history (D-10). Analytics with real rows beats a mock-up and stays honest.
5. Compare as a kept screen. It is the best stage visual the product already has.
6. Presenter mode and the recorded fallback (§3.3, research E §5–6). Build these first in Wave 2, not
   last; they are what makes rehearsal possible.

## 3. The demo story, rewritten

### 3.1 Why §7 as drafted loses points

- It is a seven-beat **feature tour** (user mode, compare, dev mode, analytics, WorkBuddy, business).
  Judges remember a story, not a tour.
- The hook is a slogan ("Every fintech wants an AI brain…"), not a person with a question.
- The Tencent beat sits at 3:40, after the judges' attention has peaked; the organisers asked for
  Tencent products live, so it should land by 2:30.
- Business is the **last** thing the judges hear. The last thing should be the tagline and the ask.
- The DB-migration question is an engineering question. The sponsor's own pain is the account-manager
  question. Use both, in the right places.

### 3.2 The 5:00 run sheet

Tagline, said three times (T1–T3): **"Answers you can prove."** Roles: Speaker (storyteller), Driver
(keyboard only, says what they click), Compliance voice, Tencent voice. About 470 spoken words at
130 wpm, with silent beats while the screen works. Everything on screen is live via Presenter mode
unless marked [S] slide or [VID] fallback.

| Time | Beat | Spoken (exact) | Screen | Rubric |
|---|---|---|---|---|
| 0:00–0:15 | Hook, live, no greeting | "Two people at a fintech ask their company AI the same question: *Why is Velquarry Trading's account restricted?* Watch." Driver presses `Space`. | `/compare` already open: Amy (account manager) left, Rachel (MLRO) right. Both answers run. | Story, Impact |
| 0:15–0:45 | Same question, two truths | "Amy gets the approved customer script, cited, and nothing else. Rachel, the money-laundering officer, gets the case. Same brain. In Singapore, telling Amy that a suspicious-transaction report even exists is a criminal offence. So the system never does." | Two cited answers side by side. Classification pill on Rachel's ("Internal · FinCrime"). | Impact, Responsible AI, HCD |
| 0:45–1:10 | Restricted equals non-existent | "Now Ken, a contractor. Velquarry on the left; on the right, a company that does not exist. Identical. To the byte. We proved it four hundred times." **T1:** "Walnut DataBridge. Answers you can prove." | Driver switches the left column to Ken, asks Velquarry; right column Ken asks "Bayfront Spice Traders". Two identical refusal cards. | Innovation, Responsible AI |
| 1:10–1:40 | The passage, checked live | "Every sentence points at a passage the asker can open right now, re-checked at the source seconds before release. Anything they cannot open never reached the model." | Click `[2]` on Rachel's answer: sources sheet, highlighted passage, "checked live 3 s ago", version. `Esc` returns focus. | AI Interaction, UX |
| 1:40–2:10 | Developer mode: the journey | Compliance voice: "Here is what happened inside, for that one request. Two permission gates before the model. Hy3 wrote the text. Three sealed records, chained, written before Rachel saw a word." | `/dev/journey/[req]` for Rachel's request: waterfall with real ms per stage, model name, three ledger records with seq and hash. | Technical, AI Interaction |
| 2:10–2:35 | The chain breaks | "I am the compliance officer. Verify: intact. Now I change one word in a copy of the log, 'denied' to 'allowed'. Verify." (beat) "Broken at entry 2,117. The key holder cannot rewrite history unseen either: the head hash is published off-host." **T2:** "Answers you can prove." | `/dev/audit` Verify → green. Presenter step "tamper" (sandbox copy) → Verify → red at the exact seq. | Responsible AI, Technical |
| 2:35–3:20 | Tencent, live | Tencent voice: "Tencent is inside the product. Jordan is on call at 3 a.m. and asks from WorkBuddy." (beat while the agent runs) "WorkBuddy's agent calls our tool with Jordan's signed token, not a name it chose. Same gates, same ledger, channel 'WorkBuddy'. Hy3 wrote both answers. CodeBuddy built the connector and ran our security review." While waiting: "Dev has not touched the mouse all demo; and the UI speaks the room's languages." | WorkBuddy window: "What's the status of the DB migration and were any blockers raised in Slack last week?" → cited answer stitched from Jira, Slack, Confluence. Cut to `/dev/audit`: the new row with channel `workbuddy-mcp`. Three-second flip to 简体中文 and back. [VID C] if the network fails. | AI Interaction, Tencent, UX & Accessibility |
| 3:20–3:50 | Proof | "We did not just demo it. We attacked it: 5,958 adversarial requests, zero leaks. Four hundred paired questions, zero differences. Five people tested it on Saturday; one broke the source sheet, we fixed it. Numbers re-run this morning; every one maps to a test in the repo." | [S] three large numbers, one tester line, one CodeBuddy line. | Technical, Responsible AI, HCD |
| 3:50–4:30 | Business, in Aspire's words | Speaker: "Aspire's team told us 'what can we offer this customer' takes time today, and the brief says about 30 % of the week goes to searching. Break-even is about seven minutes saved per person per week. Base case for a 600-person fintech: about 1.3 million US dollars a year; an estimate, inputs on the one-pager. It runs on Tencent Cloud, and Hy3 is open-weight, so a bank can host it inside its own walls." | [S] one number, four inputs, Tencent stack line. | Impact, Feasibility |
| 4:30–4:50 | Close and ask | "A company's AI will be asked to prove itself. Walnut DataBridge." **T3:** "Answers you can prove. We want one pilot team at one regulated company. Scan to try it." Stop. | [S] QR + tagline. | Overall |
| 4:50–5:00 | Buffer | Silence. | | |
| 5:00–6:00 | Q&A | Research E §8 bank; hand-offs by name; never name the other team. | Driver leaves `/dev/journey` on screen. | all |

Cuts if running long, in order: the 简体中文 flip (3 s), the CodeBuddy line (5 s), the Hy3 open-weight
line (6 s), the WorkBuddy audit-row cut (8 s). Never cut the tamper or the identical refusal.

Alternate first sentence if you follow EdgeLab: "You have just seen an Internal Brain. We asked a
different question: not *can it answer*, but *can it prove what it answered*." Then continue at 0:00.

### 3.3 Three changes from both earlier drafts, and why

1. **No "generic chatbot leaks a salary" prop.** It is a strawman, it is fake data on screen (rule 5
   and rule 7 are about honesty in exactly this sense), and a judge will ask "which bot was that?".
   The product's own contrast (Amy vs Rachel, Ken vs non-existent) is a stronger hook and it is real.
2. **The sponsor's question first, the engineer's question through WorkBuddy.** Scenario 2 and 4 open
   the demo for Aspire; scenario 1 shows cross-platform stitching through the Tencent front door.
   Scenario 3 is the journey and the chain. Four of five scenarios in five minutes, without saying
   "scenario".
3. **Tencent at 2:35, not 3:40.** And the WorkBuddy beat doubles as insurance: if the Brain's own
   TokenHub call is still failing, WorkBuddy's agent is still Hy3, live, calling our tool. Label it
   honestly in the footer ("composed by WorkBuddy (Hy3) from the Brain's sealed answer").

### 3.4 Dependencies this story puts on the build (P0)

| Need | Owner in §6 | Note |
|---|---|---|
| Compare supports: two personas same question, and one persona two questions | User mode agent | Second mode is new; a toggle "same question / same person" |
| Classification pill from the strictest cited label | User mode + B6 | Copilot/Purview pattern (research 01 §3.3); cheap, convincing for a fintech |
| Journey opens for the request just shown, in under 2 s | Analytics/Journey agent + B3/B4 | Presenter step carries the `req_id` |
| Tamper on a **sandbox copy**, then `R` restores | Audit agent + `scripts/tamper_demo.py` | The WorkBuddy beat writes a real record *after* the tamper; the real chain must stay intact |
| WorkBuddy agent configured, warm, with Jordan's token; audit row shows channel | Human checklist | Rehearse the network path on the hotspot at the venue |
| Presenter mode keys `P`/`Space`/`B`/`R` and a 150 % Stage theme | Tour + Presenter agent | Build on Saturday morning, not Sunday night |
| Three fallback clips (A compare, B tamper, C WorkBuddy) recorded after freeze | Coordinator | Research E §6.3 |

## 4. Beating the other Aspire finalist

I have not seen EdgeLab's product. From the field notes of 13 Oct (`COMPETITIVE_NOTES.md`), table stakes
for this brief are: pre-model filtering, a hash chain, citations, a live demo, fintech framing, and at
least two rivals already have a WorkBuddy-over-MCP agent. So **"two front doors" is not a
differentiator; do not lead with it.** Lead with what the differential property gives you.

| Lever | Why it is hard to copy in a weekend | How it appears on stage |
|---|---|---|
| Restricted ≡ non-existent, **shown**, then "0 of 400" | Needs ranking statistics computed from the permitted corpus only and a differential harness; most RAG systems carry the IDF side channel | 0:45–1:10 and the proof slide |
| Tipping-off framed as law, not as policy | Sponsor-specific; shows you read MAS and CDSA, not just the brief | One sentence at 0:30 |
| Write-ahead ledger with an **off-host head**, tampered live | Rivals report hash chains; few can say what a chain cannot detect, or show the break at an entry | 2:10–2:35 |
| Journey with real per-stage milliseconds and the model named | Most teams have a log table; few have a waterfall tied to the record the judge just watched | 1:40–2:10 |
| Honest evidence: numbers re-run that morning, limits stated, tester fix log | Credibility compounds across the 10 criteria | Proof slide and Q&A |
| Identity only from the signed token, even inside WorkBuddy | A judge from the WorkBuddy team will ask "can the agent impersonate?" | One sentence at 2:50 |

Two more moves:

1. **Thirty minutes of recon on Friday.** Find EdgeLab's public repo or video (the 13 Oct notes list
   KnowBuddy, SoLah, ContextLedger and TenTen, none called EdgeLab; it may be one of them renamed).
   Check their refusal wording for an existence signal and whether their audit UI is wired. Prepare
   one neutral answer for "how are you different" (research E Q9) and never name them.
2. **Two openers, drawn by lot on 2 Nov** (§3.2). If you present first, set the bar: "proof, not
   claims". If second, the alternate sentence.

## 5. Design direction: will it look "very impressive" and stay enterprise-credible?

**Yes, if three conditions hold.** Linear/Vercel restraint plus Palantir density is the right register
for Aspire, AISG and Tencent architects: it says "product", not "hackathon". But restraint has a cost:
it only looks impressive when the craft is flawless and when something on screen *moves with meaning*.

1. **Three hero visuals, built first and polished last.** (a) The split-screen Compare with two
   identical refusal cards; (b) the request journey waterfall with the three hash-chained records,
   and the Verify state turning from green to red at one entry; (c) the permission graph with nodes
   greying under "View as". Research 01 §5 says the only motion that helps is the Verify tick and
   nodes greying out. Agree. Everything else is 120–200 ms fades.
2. **A Stage theme, decided on the venue projector.** PLAN §4 says dark default, projector-tested;
   research E §1.4 says most presenters switch to light on projectors. Do not decide now. Ship both as
   tokens, add a `Stage` variant (light, 150 % scale, 24 px body, graph minimap hidden, nothing within
   5 % of the edges) and pick at the 16:00 rehearsal. Dark neutral UIs on a washed-out expo projector
   become a grey smear; that is the biggest visual risk in the plan.
3. **One signature, consistently.** Today's accent is a generic blue (`accent 128 172 255`). Every AI
   product in the room will be blue or purple. Walnut has a name that suggests a warm neutral; a single
   warm accent (amber-brown family, AA-checked on both themes) for the brand mark, focus ring and
   primary action would make the screens recognisable at a glance and separate them from EdgeLab's.
   Keep semantic colour strictly for platform identity and permission state (full / partial / locked)
   as §4 already says. Remove React Flow's default handles, controls and minimap styling; the
   defaults are the quickest tell of "template".

Also:

- Typography carries density. Inter at 13–14 px in Developer mode with tabular numerals is right;
  the hash prefixes, seq numbers and ms values must line up in columns or the journey looks amateur.
- Platform icons as monochrome glyphs with the platform colour only on hover or selection, so the
  answer stays calm and the sources row still reads at a glance.
- The engine embed (`/dev/brain`, D-09) is the costliest item in Wave 1 and appears nowhere in the
  five minutes. Keep it (the user wants it) but as a thin, consistent frame around the existing
  screens, not a restyle, and use it only in Q&A ("what is underneath?"). Do not let it take hours
  from the three hero visuals.
- i18n: 简体中文 for User mode and navigation (D-12) is the right scope. Do not add Malay and Tamil
  machine-translated (research B §D); an unreviewed translation of permission wording is a liability
  in a fintech demo.

## 6. Risks the plan under-weights

| Risk | Why it matters | Mitigation |
|---|---|---|
| Hy3 never answers before Demo Day (TokenHub 402 today) | AI Interaction drops to a 5; "Tencent live" becomes a claim | Activate Friday morning; record one live answer the moment it works; make WorkBuddy the Tencent beat (it is Hy3 regardless); keep `deepseek-v4.1-flash` in the chain and label it |
| Scope: Wave 1 has six parallel agents and ten backend items | Everything at 70 % | Freeze Saturday 22:00; P0 = demo-path screens, presenter, WorkBuddy, Hy3; P1 = graph View-as, analytics, trust, i18n; P2 = engine embed, tour, history, settings extras |
| Eval numbers predate the pipeline change and used a fake model | Rule 7; a judge who reads `RESULTS.md` sees "hy3 PENDING" | Re-run Saturday night with Hy3 in the loop; update the slide from the output; say the date on stage |
| Tamper demo runs before the WorkBuddy record is written | A broken real chain during the Tencent beat | Sandbox copy only; `R` restores; rehearse the order |
| Status line driven by elapsed time | The one "fake" thing a sharp judge can spot | Emit stage events from the backend over SSE; hold text until grounding passes (D-08 intact) |
| Audit "last 30 days" uses the real clock against a world dated 13 Oct (B9) | Maya's beat or a judge's question returns nothing | B9 is listed; make it P0, not "todo" |
| Compare needs the Web Locks API | Fails on plain http from a non-localhost address | Demo on `localhost` only; keep a pre-opened second window |
| Slot arithmetic: 60 minutes for 10 teams | No walk-up time | Cold start: laptop mirrored, `/compare` open, first words within five seconds |
| The team hands the judges a feature tour in Q&A | Dilutes the memory | Every answer ends on a test or a file, then stops |

## 7. Conflicts between the research files that the plan should settle

1. **Hidden-source notice** (research B H7, research 02 §1.2 case 2) vs **rule 4 / D-07**. Settled:
   rule 4 wins. Also remove "noting that something exists but is hidden" from any tour copy.
2. **Streaming** (research 02 §1.2) vs **D-08**. Settled: D-08 wins; the staged status line replaces
   streaming, and should be driven by real stage events.
3. **Light stage theme** (research E §1.4) vs **dark default** (PLAN §4). Not settled: build both,
   decide at the rehearsal.
4. **Three live moments** (research 05 §5) vs **two** (research E §0). Two, plus one Tencent beat.
5. **Four UI languages** (research B §D) vs **two** (D-12). Two.
6. **Generic-bot prop** (research 05 and E hooks) vs **rule 5/7**. Drop the prop; the product is the
   contrast.

## 8. Top five recommendations

1. **Rewrite the demo as the §3.2 story and build Presenter mode first.** Open live at 0:05 with
   Amy and Rachel asking Aspire's own question; Ken's identical refusals at 0:45; the chain breaks at
   2:10; WorkBuddy at 2:35; proof, business, tagline, ask; finish at 4:50. Record the three fallback
   clips after the freeze.
2. **Make Hy3 live on Friday and make WorkBuddy the insurance.** One recorded live answer the moment
   TokenHub works; the model named on every answer footer; the WorkBuddy agent configured and
   rehearsed on the hotspot. If TokenHub stays down, WorkBuddy still shows Hy3 calling our tool.
3. **Run a five-person hallway test on Saturday and keep a three-row fix log.** Two hours. One
   sentence on stage, one card in the app. It is the only Human-Centered Design evidence in the plan,
   and it also feeds UX & Accessibility and Overall Quality.
4. **Freeze scope Saturday 22:00 and polish the five demo-path screens at 150 % zoom.** P0 is the
   demo path, Presenter, WorkBuddy, Hy3 and B9. Graph View-as, analytics, trust centre and i18n are
   P1. The engine embed, tour, history and settings extras are P2 and are not in the five minutes.
5. **Re-run the eval with Hy3 in the loop and show honesty as a feature.** Numbers dated on the
   slide; "0 leaks" no longer "with a fake model"; the tamper demo on a sandbox copy with the
   off-host anchor explained in one sentence; limits stated in Q&A. Credibility is the tie-breaker
   against a rival with the same brief.
