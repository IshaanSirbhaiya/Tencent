# Run sheet: the adopted 5:00 pitch (Demo Day, Tue 3 Nov, Marina Bay Sands)

Source: `docs/finals/reviews/plan_fable.md` §3.2 (decision D-14). Tagline, said three times (T1, T2, T3): **"Answers you can prove."** Product name on stage: **Walnut DataBridge**; "Internal Brain" is the ask product inside it.

5:00 pitch plus 1:00 Q&A. About 470 spoken words; the rest is silence while the screen works.

## 0. Roles (the user may change these)

| Person | Role | Hands |
|---|---|---|
| **Ishaan** | Speaker: hook, tagline, business, close, Q&A lead | Clicker for slides only. Never touches the demo laptop |
| **Dev** | Driver: the only person on the laptop; says what he presses ("Space") | Keyboard by default; the bar's clickable controls and the mouse side buttons are the fallback (PRESENTER_KEYS.md section 2a) |
| **Mehul** | Compliance voice: the journey and the chain | None |
| **Aaruni** | Tencent voice: WorkBuddy beat, proof numbers; watches the timekeeper | Starts the WorkBuddy action |

Rules: one speaker at a time; hand-offs by name ("Mehul, over to the compliance officer's chair"); look at the judges first and the screen second; whoever is not speaking stands still with hands visible. If someone blanks, Ishaan says the first four words of the next line.

## 0b. Stage setup notes

- **One browser window only** on stage. No second pre-opened window; WorkBuddy is the only other window.
- **Mouse works too** (`PRESENTER_KEYS.md` §2a): the bar's ◀ ▶ buttons (in Stage behind "More"), the mouse's back/forward side buttons, "Exit presenter" and a "Show the presenter bar" handle when hidden; Dev still drives from the keyboard.
- **`R` is the recovery key**: after a confirm it signs every demo session in the browser out, clears the presenter state and returns to step 0. It restores nothing and never edits the real ledger (the tamper step only ever changes a copy).
- **WorkBuddy question, one exact string everywhere:** `What's the status of the database migration project and were there any blockers raised in Slack last week?`
- Every line that names Hy3 as the author of an answer is tagged [ONLY IF HY3 LIVE] and has a default alternative ("quoted from the sources, with the same checks").

## 1. Truth checks before you read any line

Lines marked **[V]** depend on a fact to re-check on the morning of 3 Nov. If the fact is false, use the replacement. The repo says today that Hy3 has not been run live (`STATUS.md`), and the platforms are mocks with real API shapes.

| Tag | Line depends on | If false, say instead |
|---|---|---|
| [HY3] | Hy3 answers live through TokenHub (not run live as of 30 Oct). The default lines say "quoted from the sources, with the same checks" | Use the [ONLY IF HY3 LIVE] alternates ("Hy3 wrote the text", "Hy3 wrote both answers") only after a live Hy3 answer has been seen. Otherwise keep the default and show the footer label |
| [TESTER] | A real FIX_LOG row exists ("one broke the source sheet") | Delete the whole tester sentence. Do not soften it |
| [NUM] | Proof numbers re-run that morning (7,374 requests, 0 leaks; 400 pairs, 0 differences) | Read the numbers from the latest `RESULTS.md` |
| [CB] | CodeBuddy built the connector and ran a security review | Say only what the repo says CodeBuddy did |
| [ENTRY] | The tamper break entry number | Say the number the screen shows, not "2,117" |
| [BIZ] | Business inputs on the one-pager | Match the one-pager exactly |

## 2. The sheet

Times are minutes:seconds from the first word. "Press" means Dev on the laptop (see `PRESENTER_KEYS.md`). Every line is spoken as written unless a tag applies.

The 5:00 path is the 11 steps in `script.ts` (HUD 00 to 10). The live-growth beat ("the brain grows") is not on this path: it is a Q&A walk-up only, in a tab opened with `?grow=1` (see `PRESENTER_KEYS.md` §6).

| Time | Beat | Who | Exact spoken line | Screen and action |
|---|---|---|---|---|
| 0:00–0:07 | Hook | Ishaan | "Two people at a fintech ask their company AI the same question: *Why has Velquarry Trading's account been restricted?*" | `/compare` already open, Stage theme: Amy (account manager) left, Rachel (MLRO) right. No greeting |
| 0:07–0:09 | | Ishaan | "Watch." | Dev presses `Space`; both questions run |
| 0:09–0:15 | | (silent) | | Staged status line, then both verified answers appear |
| 0:15–0:45 | Same question, two truths | Ishaan | "Amy gets the approved customer script, cited, and nothing else. Rachel, the money-laundering officer, gets the case. Same brain. In Singapore, telling Amy that a suspicious-transaction report even exists is a criminal offence. So the system never does." | Two cited answers side by side. There is no classification pill in the app, so do not point at one. Ishaan points with an open hand, Amy then Rachel |
| 0:45–0:50 | Restricted equals non-existent | Ishaan | "Now Ken, a contractor." Dev: "Space." | `Space` switches to the Ken view: same person, two questions |
| 0:50–1:00 | | Ishaan | "Velquarry on the left. On the right, a company that does not exist." | Left: Ken asks Velquarry. Right: Ken asks "Bayfront Spice Traders". Two refusal cards appear |
| 1:00–1:10 | | Ishaan | "Identical. To the byte. We proved it four hundred times." **T1:** "Walnut DataBridge. Answers you can prove." [NUM] | Hold on the two identical cards for two seconds of silence after "prove" |
| 1:10–1:15 | Passage, checked live | Ishaan | "Every sentence points at a passage the asker can open right now." Dev: "Space." | `Space` returns to the Compare view and opens citation [2] on Rachel's answer by itself (no second key) |
| 1:15–1:40 | | Ishaan | "Re-checked at the source seconds before release. Anything they cannot open never reached the model." | Sources sheet: highlighted passage, "checked live" age, version. `Esc` closes it and returns focus to [2] |
| 1:40–1:45 | Developer mode: the journey | Ishaan → Mehul | Ishaan: "Mehul, over to the compliance officer's chair." Dev: "Space." | `Space`: session switches to Maya; `/dev/journey/<req>` for Rachel's request |
| 1:45–2:10 | | Mehul | "Here is what happened inside, for that one request. Two permission gates before the model. The answer is quoted from the sources, with the same checks. [ONLY IF HY3 LIVE: say "Hy3 wrote the text" instead.] Three sealed records, chained, written before Rachel saw a word." | Waterfall with real milliseconds per stage, model name, three ledger records with sequence and hash. Mehul points at each |
| 2:10–2:20 | The chain breaks | Mehul | "I am the compliance officer. Verify: intact." Dev: "Space." | `/dev/audit`; `Space` runs Verify (step 05, `verify`); green |
| 2:20–2:28 | | Mehul | "Now I change one word in a copy of the log, denied to allowed. Verify." Dev: "Space." | One `Space` runs the sandbox tamper and then Verify on the copy (step 06, `tamper`). The screen names the change in the log's own words: a Gate-2 decision from 'forbidden' to 'verified' (`audit/tamper.py`); "denied to allowed" is the spoken form (`script.ts`) |
| 2:28–2:35 | | Mehul | (beat) "Broken at entry [ENTRY]. A key holder cannot rewrite history unseen either: the head hash is published off-host." **T2:** "Answers you can prove." | Red result at the exact sequence number. Say the number on the screen |
| 2:35–2:40 | Tencent, off-app | Mehul → Aaruni | Mehul: "Aaruni, Tencent." Aaruni: "Tencent is inside the product." | Dev presses `Space`: HUD says "WorkBuddy"; Dev switches window to WorkBuddy (off-app) |
| 2:40–2:55 | | Aaruni | "Jordan is on call at 3 a.m. and asks from WorkBuddy." (beat while the agent runs) | WorkBuddy window: Aaruni starts the action: "What's the status of the database migration project and were there any blockers raised in Slack last week?" |
| 2:55–3:12 | | Aaruni | "WorkBuddy's agent calls our tool with Jordan's signed token, not a name it chose. Same gates, same ledger, channel 'WorkBuddy'. Both answers are quoted from the sources, with the same checks. [ONLY IF HY3 LIVE: say "Hy3 wrote both answers" instead.] [CB] CodeBuddy built the connector and ran our security review." | Cited answer stitched from Jira, Slack and Confluence appears |
| 3:12–3:20 | | Aaruni | "Dev drives the whole demo from the keyboard." (say it only if that is true on the day; the bar's buttons are the fallback, so do not promise "no mouse") | Dev switches back to the app (Alt-Tab): `/dev/audit`. The newest row, channel `workbuddy-mcp`, appears when the window regains focus (`?latest=1`), not on a key. Delete the line if Dev used the mouse or the bar's buttons. No language flip here: it was cut (round-3 Fable #8; on `/dev/audit` Developer mode stays English and says so, so "the UI speaks the room's languages" would be contradicted on screen) |
| 3:20–3:30 | Proof | Aaruni | "We did not just demo it. We attacked it: 7,374 adversarial requests, zero leaks. Four hundred paired questions, zero differences." [NUM] | Proof slide [S] |
| 3:30–3:50 | | Aaruni | "[TESTER] Five people tested it on Saturday; one broke the source sheet, we fixed it. Numbers re-run this morning; every one maps to a test in the repo." | Same slide: three large numbers, one tester line, one CodeBuddy line |
| 3:50–4:30 | Business, in Aspire's words | Ishaan | "Aspire's team told us 'what can we offer this customer' takes time today, and the brief says about 30 % of the week goes to searching. Break-even is about seven minutes saved per person per week. Base case for a 600-person fintech: about 1.3 million US dollars a year; an estimate, inputs on the one-pager. The design targets Tencent Cloud (a one-command Lighthouse deploy kit is included; we have no live instance), and Hy3 is open-weight, so a bank could host it inside its own walls." [BIZ] | Business slide [S]: one number, four inputs, Tencent stack line |
| 4:30–4:50 | Close and ask | Ishaan | "A company's AI will be asked to prove itself. Walnut DataBridge. **T3:** Answers you can prove. We want one pilot team at one regulated company. Scan to try it." Stop. | Close slide [S]: QR to the GitHub repo and the tagline |
| 4:50–5:00 | Buffer | (silence) | | Hold the close slide |
| 5:00–6:00 | Q&A | Ishaan leads; hand-offs by name | See `QA_BANK.md` | Dev leaves `/dev/journey` on screen. Never name the other team |

Notes on the numbers in the lines:
- "Seven minutes" break-even: the repo research gives about 7 minutes at the US$100,000 price in the submission and about 5 minutes at US$72,000. Say whichever the one-pager shows. Estimate, not measured.
- "30 %" is from the brief as quoted by the plan. Re-check the wording against the brief.
- "Four hundred times" is the 400 paired questions in `databridge/evals/internal_brain/RESULTS.md` (0 mismatches).

## 3. Timing summary

| Block | Seconds |
|---|---|
| Compare, Amy vs Rachel | 0:00–0:45 (45) |
| Ken, identical refusals | 0:45–1:10 (25) |
| Passage | 1:10–1:40 (30) |
| Journey | 1:40–2:10 (30) |
| Chain and tamper | 2:10–2:35 (25) |
| WorkBuddy | 2:35–3:20 (45) |
| Proof | 3:20–3:50 (30) |
| Business | 3:50–4:30 (40) |
| Close | 4:30–4:50 (20) |
| Buffer | 4:50–5:00 (10) |

Target: three timed rehearsal runs between 4:40 and 5:00; none over 5:00.

## 4. Fallback cut order (if running long)

Cut in this order, stopping when back on time:

1. The CodeBuddy line (5 s).
2. The Hy3 open-weight line (6 s) [ONLY IF HY3 LIVE; default: skip it or say "the design does not depend on one model"].
3. The WorkBuddy audit-row cut (8 s).

The 简体中文 flip is no longer in the 5:00 (cut 30 Oct, round-3 Fable #8). If a judge asks about
languages, answer with `QA_BANK.md` Q21; `L` still works on any user page (for example `/compare`).

**Never cut:** the tamper, or the identical refusals.

If the network fails during the WorkBuddy beat, use clip C (see `BACKUP_PLAN.md`) and say in one clause that it is a recording.

## 5. Recovery lines (one clause, never an apology)

- Clip shown: "Let me show you the recording from this morning; same system, same data."
- App stalls: Ishaan says the next line's first four words; Dev presses `B`.
- A judge interrupts: answer in one sentence, "let me finish this step, then I will come back."
- A recording is never presented as live.

## 6. Alternate opener

If we follow the other FinTech team, use opener 2 in `OPENERS.md`, then continue at 0:09.
