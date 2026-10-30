# Walnut DataBridge: feature catalogue

**Answers you can prove.**

Every user-visible feature and every backend capability of the finals build, with who can use it,
where it is, how to try it, the rules it keeps, the tests that prove it, and its screenshots.

Branch `final_v1.3` was verified at commit `1076517` (30 Oct 2026). **Updated on the laptop, 30 Oct evening
(branch `final_v1.2`):** the rows marked *evening* below (presenter mouse controls, the upload folder picker, Act
v3.2, the Deliverables wording and `.pptx` notes) describe round-4 fixes that are **in the working tree and not
committed yet**, and **no full test re-run covers them yet: re-run pending (final integration).** Test counts
quoted in this file (1,131 backend, 166 web) are from the cloud run at `831297e`. What changed and when:
[CHANGELOG.md](CHANGELOG.md) ("30 Oct 2026, evening (laptop)"). Planned and not built: the "Demo story" app mode
([section 13](#13-act-and-what-is-planned)).

## 0. Read this first

| Fact | Detail |
|---|---|
| Data | Synthetic only: "Company A", a made-up fintech (34 people; 231 Confluence pages, 261 Jira issues, 628 Slack threads, 87 Drive files) |
| Platforms | Mock Slack, Confluence, Jira and Drive with the real APIs' shapes, permission models and webhooks. Not run against a live tenant |
| Tencent Hy3 | Wired in through TokenHub and tested with a capturing test model. **Not live yet** (TokenHub activation pending; every model call returns HTTP 402 with code 401006). Until then answers say "Quoted from sources (no model)" |
| Sign-in | A demo login standing in for single sign-on. No password; optional passcode (`IB_DEMO_PASSCODE`) |
| Numbers | **Re-run pending (final integration): the counts below predate the evening round-4 fixes.** 1,131 backend tests pass, 0 fail (30 Oct, cloud: Linux, Python 3.13, commit `831297e`). Web e2e (Playwright, mock mode): 166 passed, 0 failed, 41 skipped; the skipped specs need the live stack, the engine proxy or Hy3 ([TEST_REPORT.md](TEST_REPORT.md)). Leak eval: 0 leaks in 7,374 requests, 0 of 400 pairs differ, 0 injection hard failures in 1,152 ([RESULTS.md](../../databridge/evals/internal_brain/RESULTS.md)) |

### The rules every feature keeps

From [PLAN.md §2](PLAN.md#2-hard-rules-unchanged-they-are-the-product). Entries below cite them as R1 to R7.

| Rule | What it means |
|---|---|
| **R1 Filter before the model** | Gate 1 (permissions copied from each platform) and Gate 2 (a live check with the platform) run before any model call. Any error means deny |
| **R2 Identity from the signed token only** | No request body, tool argument, header or model output can name a person |
| **R3 Write-ahead audit** | The ledger record is written before retrieval, before the model call and before the answer is shown. If it cannot be written, nothing is answered (503) |
| **R4 One response shape** | "Restricted" looks exactly like "does not exist". No count of hidden items, no "something is hidden" note, no Gate-2 reason code for the asker |
| **R5 Every number from the backend** | Charts aggregate on the server (`/audit/stats`), never in the browser |
| **R6 Synthetic data only** | |
| **R7 Never claim more than a test proves** | |

### People (all fictional)

| Persona | Role in the app | Use them for |
|---|---|---|
| `jdoe` (Jordan) | employee | Ask, graph, engine view (the engine snapshot is his view) |
| `amy.am` (Amy) | employee | The approved customer script on Velquarry |
| `mlro.tan` (Rachel) | employee (MLRO) | The FinCrime case on Velquarry |
| `ken.ext` (Ken) | employee (contractor) | Identical refusals; a 24-item graph |
| `priya.sre`, `sara.sec`, `leo.ops` | employee | Other views of the same company |
| `maya.compliance` (Maya) | `compliance_officer` | Journey (any request), Analytics, Audit, tamper demo |
| `admin.it` | `admin_it` | Simulator |

### Where the tests are

- Backend: `databridge/apps/internal_brain/tests/` ([folder](../../databridge/apps/internal_brain/tests/)). Run from `databridge/`: `.venv/Scripts/python -m pytest apps/internal_brain/tests -q`.
- Mock platforms: `databridge/apps/mock_platforms/tests/`.
- Web e2e (Playwright + axe, mock mode): `databridge/apps/internal_brain_web/tests/e2e/` ([folder](../../databridge/apps/internal_brain_web/tests/e2e/)). Run `npm run e2e` there.
- Screenshots: [screens/](screens/) (`live/` is the real backend; the other folders are mostly the mock build at 1280 and 390 px, dark and light).

### Contents

1. [User mode](#1-user-mode): U1 Sign-in · U2 Ask · U3 Voice dictation · U4 Cited answer and sources sheet · U5 Refusal · U6 History · U7 Settings · U8 Value calculator · U9 Map (pre-finals)
2. [Compare](#2-compare): C1 Two people, one question · C2 One person, two questions
3. [Presenter](#3-presenter): P1 Presenter mode · P1a Mouse controls · P2 Stage theme · P3 Slides · P4 Backup clips
4. [Developer mode](#4-developer-mode): D1 Mission control · D2 Request journey · D3 Knowledge graph · D4 Walnut engine · D5 Analytics · D6 Audit · D7 Tamper demo · D8 Simulator · D9 Trust centre · D10 Style guide
5. [Shell, tours and navigation](#5-shell-tours-and-navigation): S1 Mode switch · S2 Command palette · S3 Tours · S4 Deep links and session check
6. [Backend APIs](#6-backend-apis): B1 to B12
7. [Security controls](#7-security-controls): X1 to X10
8. [Accessibility and i18n](#8-accessibility-and-i18n): A1 to A6
9. [Tooling and scripts](#9-tooling-and-scripts): T1 to T10
10. [Evidence](#10-evidence)
11. [Demo-proof features](#11-demo-proof-features-behind-a-toggle-d-24): G1 Watch the brain grow · G2 Add a document · G3 Not in the brain yet · G4 Engine refresh · G5 The toggle
12. [Deliverables from an answer](#12-deliverables-from-an-answer-kill-switch-d-25): V1 Table, chart, analysis (P0) · V2 Slides export (P1)
13. [Act, and what is planned](#13-act-and-what-is-planned): K1 Act v3.2 · K2 Proxy open-link check · Planned: the "Demo story" app mode (not built)
14. [Pages beside the chat (just-in-time views)](#14-pages-beside-the-chat-just-in-time-views-d-37-d-38-d-39): J1 Page or chat · J2 The page, composed by Claude · J3 The canvas and the Xcode-like Ask

---

## 1. User mode

### U1. Sign-in (persona picker)

| | |
|---|---|
| What | Pick a person from a radio group of cards. Each card shows the role and what that person can open. A notice says every question is logged |
| Who | Anyone |
| Where | `/login` → `POST /auth/demo-login`, `GET /auth/whoami`, `POST /auth/logout` |
| How | 1. Open http://127.0.0.1:3100. 2. Pick a person (arrow keys work). 3. Sign out from the menu at the top right to switch |
| Guarantees | R2: the server mints an HttpOnly signed cookie; the browser never sees the token. A deployed build refuses to start without a passcode of 8+ characters (`config.py`) |
| Tests | `user.spec.ts` "sign-in is a persona radio group with the logging notice"; `keyboard.spec.ts`; backend `test_app_routes.py::test_demo_login_cookie_and_disabled`, `test_config_deployable.py` |
| Screens | [user/01-signin-1280-dark.png](screens/user/01-signin-1280-dark.png) · [live/login.png](screens/live/login.png) · [tour/01-user-step1-signin-light.png](screens/tour/01-user-step1-signin-light.png) |

### U2. Ask, with live stage events

| | |
|---|---|
| What | One composer. While the request runs, a progress line shows the real stages (accepted, permission checks, sealed, composing, grounded, released). Starter questions per person |
| Who | Any signed-in person (employee, compliance or IT admin role) |
| Where | `/` → `POST /api/ib/ib/ask/stream` (SSE), with `POST /ib/ask` as the plain JSON form |
| How | 1. Sign in as `jdoe`. 2. Press a starter card or type a question. 3. Press **Ask** and watch the progress line |
| Guarantees | R1, R3. R4: stage events carry names only, never a count, id or reason; Gate 1 and Gate 2 are **held** and released together only when the decision is sealed, so the stream cannot time the live checks ([§6, B3](#6-backend-apis)). No answer text before `released` |
| Tests | `user.spec.ts` "ask → staged progress → cited answer → source sheet → Esc returns focus"; `ask.spec.ts`; backend `test_ask_stream.py` (10 tests), `test_final_review_fixes.py::test_gate1_and_gate2_are_held_until_the_checks_are_sealed` |
| Screens | [user/02-ask-home-1280-dark.png](screens/user/02-ask-home-1280-dark.png) · [user/04-waiting-1280-dark.png](screens/user/04-waiting-1280-dark.png) · [user/03-scope-popover-1280-dark.png](screens/user/03-scope-popover-1280-dark.png) · [live/user-jdoe-home.png](screens/live/user-jdoe-home.png) |

### U3. Voice dictation (new, `1bc6b4c`)

| | |
|---|---|
| What | A microphone button in the composer. The browser's own speech service turns speech into text in the box, in English or 简体中文 (follows the app language) |
| Who | Anyone, in a browser that has the Web Speech API (Chrome, Edge). Hidden where unsupported |
| Where | `/` composer (`components/user/Composer.tsx`, `useDictation.ts`, `lib/i18n/voice.ts`) |
| How | 1. Press the microphone. 2. Speak. 3. Read the text, edit it, then press **Ask** yourself (Esc stops listening) |
| Guarantees | Never auto-sends: the person always presses Ask. No audio reaches our servers and nothing is stored by the app (the browser's speech service does the recognising). Accessible name and state; hidden, not broken, when the API is absent |
| Tests | `voice.spec.ts` (2 tests: text goes in the box and nothing is asked, Esc stops, axe clean; button hidden without the API). In the 30 Oct cloud run (166 passed) |
| Screens | None yet |

### U4. Cited answer and sources sheet

| | |
|---|---|
| What | The answer with `[n]` chips. A chip or source card opens a sheet: platform, version, "Checked live at", the cited sentences highlighted, and a link to the source. A model badge says who wrote it ("Tencent Hunyuan Hy3", "DeepSeek" or "Quoted from sources (no model)") |
| Who | Anyone |
| Where | `/`, also inside Compare; `GET /ib/doc/{id}` for the live passage |
| How | 1. Ask as `jdoe`: "What's the status of the database migration project…?" 2. Press chip **[1]**. 3. Press **Esc**: focus returns to the chip |
| Guarantees | R1 (only passages that passed both gates reach the model); grounding drops any number or name not in the evidence, else the answer is quoted; plain text only (no `dangerouslySetInnerHTML`); non-http(s) source links render as text |
| Tests | `ask.spec.ts` "login → ask → citations visible → scope note visible", "model badges…"; `keyboard.spec.ts` "follow a citation chip"; `proxy.spec.ts` "a source link that is not http(s) is shown as text"; backend `test_scenarios_e2e.py::test_canonical`, `test_graph_doc.py::test_doc_best_chunk_and_drift` |
| Screens | [user/05-answer-1280-dark.png](screens/user/05-answer-1280-dark.png) · [user/06-sources-sheet-1280-light.png](screens/user/06-sources-sheet-1280-light.png) · [live/user-jdoe-sheet-highlight.png](screens/live/user-jdoe-sheet-highlight.png) · [a11y/citation-chips-1280-dark.png](screens/a11y/citation-chips-1280-dark.png) |

### U5. Refusal and error states

| | |
|---|---|
| What | One constant refusal: "I couldn't find an answer to that in the sources you can access." A 503 says "temporarily unavailable. Nothing was answered." with retry |
| Who | Anyone |
| Where | `/` and Compare |
| How | 1. Sign in as `ken.ext`. 2. Ask "Show me the security incident report from the Q3 breach." 3. Ask the same about Q7 (does not exist): the text is identical |
| Guarantees | R4: no digits, counts or hints on the refusal card; restricted and made-up give byte-identical text. R3: a ledger failure is a 503 with no answer |
| Tests | `user.spec.ts` "Ken: the refusal is the constant text…", "restricted and made-up subjects render identically", "503 shows a calm error card"; `ask.spec.ts` "refusal renders calmly…"; backend `test_scenarios_e2e.py::test_s3_existing_vs_nonexistent_identical`, `test_fintech_scenarios.py::test_fc_real_vs_nonexistent_customer_is_byte_identical` |
| Screens | [user/07-refusal-ken-1280-dark.png](screens/user/07-refusal-ken-1280-dark.png) · [user/08-error-503-1280-light.png](screens/user/08-error-503-1280-light.png) · [live/user-ken.ext-answers.png](screens/live/user-ken.ext-answers.png) · [live/user-zh-refusal.png](screens/live/user-zh-refusal.png) |

### U6. History and the request receipt

| | |
|---|---|
| What | Your own questions grouped by day, each labelled live or replay; a row opens your receipt in Journey ([D2](#d2-request-journey)) |
| Who | Anyone, own requests only |
| Where | `/history` → `GET /ib/history`, `GET /ib/trace/{req}` |
| How | 1. Ask two questions. 2. Open **History**. 3. Press a row |
| Guarantees | R2 (own requests only, from the token); R4 (the receipt never shows the considered set or Gate-2 reasons) |
| Tests | `user.spec.ts` "history groups my questions and opens the journey"; backend `test_graph_doc.py::test_history_labels_channel_and_replay`, `test_dev_views.py::test_trace_is_own_requests_only_and_hides_gate2_reasons` |
| Screens | [user/09-history-1280-dark.png](screens/user/09-history-1280-dark.png) · [live/user-history.png](screens/live/user-history.png) · [live/user-history-open.png](screens/live/user-history-open.png) |

### U7. Settings

| | |
|---|---|
| What | Language (English / 简体中文), theme (dark / light), text size, reduce motion, high contrast, replay the tour. Stored in the browser only |
| Who | Anyone |
| Where | `/settings` |
| How | 1. Open **Settings**. 2. Switch language or text size. 3. Every page follows |
| Guarantees | No server state. Switches are named controls (WCAG 4.1.2) |
| Tests | `user.spec.ts` "language switch to 简体中文 and back"; `a11y-shell.spec.ts` "settings switches are named…" (own config) |
| Screens | [user/10-settings-1280-dark.png](screens/user/10-settings-1280-dark.png) · [a11y/settings-1280-light.png](screens/a11y/settings-1280-light.png) · [live/user-zh-settings.png](screens/live/user-zh-settings.png) |

### U8. Value calculator

| | |
|---|---|
| What | ROI calculator: annual value, model cost per answer and per year, net value, break-even minutes per week, payback weeks, and a break-even chart. Every input is visible and the result is labelled an estimate |
| Who | Anyone |
| Where | `/value` (defaults in `public/finals/value_defaults.json`; spec [value_calculator.spec.json](rubric/impact_feasibility/value_calculator.spec.json)) |
| How | 1. Open **Value**. 2. Pick a preset or type inputs. 3. Read payback and break-even |
| Guarantees | R7: labelled estimate; the maths matches six spec test vectors |
| Tests | `value.spec.ts` (10 tests: defaults and ranges, six vectors, payback null with no time saved, presets and keyboard input) |
| Screens | [analytics/value-compliance-1280-dark.png](screens/analytics/value-compliance-1280-dark.png) · [live/dev-maya-value.png](screens/live/dev-maya-value.png) |

### U9. Map (pre-finals, kept)

| | |
|---|---|
| What | Everything you can open, by platform and space. `jdoe` sees 867 items on 4 platforms; `ken.ext` 24 on 2 |
| Who | Anyone (in the User menu) |
| Where | `/map` → `GET /ib/map` |
| How | 1. Open **Map** from the menu. 2. Pick a place. 3. Search narrows the items |
| Guarantees | R4: identical with and without restricted documents; absent platforms simply do not appear. Each item is re-checked live (so it takes a few seconds) |
| Tests | `map.spec.ts` (4 tests); backend `test_knowledge_map.py::test_map_is_identical_with_and_without_restricted_docs`, `test_final_review_fixes.py::test_map_lists_the_checked_version_and_honours_a_drifted_acl` |
| Screens | [live/legacy-map.png](screens/live/legacy-map.png) |

## 2. Compare

### C1. Same question, two people

| | |
|---|---|
| What | Two people ask the same question side by side, each in their own signed-in session. The demo opener: Amy gets the approved customer script on Velquarry; Rachel (MLRO) gets the case (synthetic FC-2026-0147) |
| Who | Anyone (needs demo sign-in, on in this build) |
| Where | `/compare` (deep link `?a=…&b=…&q=…`). Proxy slots `x-ib-slot: b` / `c` use cookies `ib_session_b` / `ib_session_c` |
| How | 1. **Compare** → "Same question · two people". 2. Pick Amy and Rachel, paste the Velquarry question. 3. **Ask both**, then press chip **[2]** in Rachel's answer |
| Guarantees | R2: the slot header only chooses which signed cookie is forwarded; identity still comes from the token. Slots reach only the few routes Compare needs, never `/audit` or `/sim`. The main session is untouched. A failed re-run forgets earlier answers (no stale request id) |
| Tests | `compare.spec.ts` (9 tests, incl. "a slot-b demo-login sets ib_session_b only", "a column whose slot turns out to be someone else shows the error", "a failed re-run forgets the earlier answers"); `proxy.spec.ts` "an unknown x-ib-slot value is a 400"; backend `test_fintech_scenarios.py::test_fc_account_manager_gets_only_the_approved_script` |
| Screens | [live/compare-amy-rachel.png](screens/live/compare-amy-rachel.png) · [live/compare-amy-rachel-sheet.png](screens/live/compare-amy-rachel-sheet.png) · [user/13-amy-approved-script-1280-dark.png](screens/user/13-amy-approved-script-1280-dark.png) |

### C2. Same person, two questions ("Identical responses")

| | |
|---|---|
| What | One person asks two questions side by side. When both responses have the same text and the same sources, the marker reads "Identical responses: same text, same sources" |
| Who | Anyone |
| Where | `/compare` → "Same person · two questions" (deep link `?mode=person&p=…&q1=…&q2=…`) |
| How | 1. Pick Ken. 2. Left: the Velquarry question; right: the same about "Bayfront Spice Traders" (does not exist). 3. **Ask both** |
| Guarantees | R4, shown live: restricted looks exactly like non-existent. The marker is absent whenever the responses differ |
| Tests | `compare.spec.ts` "same person, two questions: a restricted and a made-up company give identical refusals…", "the marker is absent when the responses differ"; eval: 0 of 400 pairs differ |
| Screens | [live/compare-ken-identical.png](screens/live/compare-ken-identical.png) · [presenter/stage-02-ken-identical.png](screens/presenter/stage-02-ken-identical.png) · [presenter/mobile-390-ken-identical.png](screens/presenter/mobile-390-ken-identical.png) |

## 3. Presenter

### P1. Presenter mode (the Demo Day script on the keyboard)

| | |
|---|---|
| What | The 11-screen run sheet (title, then 10 steps) driven by keys by default (the same actions are also clickable buttons, [P1a](#p1a-presenter-mouse-controls-evening-30-oct)), so one person drives the demo. Signs the right people in, runs Compare, opens citation [2], the journey as Maya, Verify, the tamper demo, the WorkBuddy card, then slides |
| Who | The demo driver. Keys work only when armed |
| Where | http://127.0.0.1:3100/compare?present=1 (or `/present?present=1`). Script: `components/presenter/script.ts`; keys: `controller.ts` |
| How | 1. Open the URL. 2. Press **P** (arm and start). 3. Press **Space** for each step. Other keys: **←** back, **→** skip a failed step, **B** backup clip, **R** reset (asks first), **L** 中文, **S** Stage theme, **2** open citation [2], **Esc** hide the bar, **?** key legend, **Ctrl+Shift+P** disarm |
| Guarantees | WCAG 2.1.4: single keys do nothing unless armed, and are ignored while typing. Each step is generation-bound: a superseded step can never navigate or change a session. A failed step does not advance. Reset signs every slot out and returns to step 0. The real ledger is never edited |
| Tests | `presenter.spec.ts` (11 tests, incl. "P then Space walks the whole script from the keyboard", "a slot sign-in failure fails the Compare step…", "no stale request id after a failed run", "reset during a pending Compare step…") |
| Screens | [live/presenter-step-00-title.png](screens/live/presenter-step-00-title.png) to [live/presenter-step-10-close.png](screens/live/presenter-step-10-close.png) · [live/presenter-armed.png](screens/live/presenter-armed.png) · [presenter/stage-reset-confirm.png](screens/presenter/stage-reset-confirm.png) |
| Docs | [RUN_SHEET.md](rubric/demo_story/RUN_SHEET.md) · [PRESENTER_KEYS.md](rubric/demo_story/PRESENTER_KEYS.md) |

### P1a. Presenter mouse controls (evening, 30 Oct)

| | |
|---|---|
| What | Clickable controls on the presenter bar, so the demo can be driven or rescued with the mouse. **Keyboard-driven by default; the clickable controls are the fallback.** Next (= `Space`), Skip this step (= `→`, only on a failed step), Back (= `←`), Start (= `P`, before it), Recording (= `B`, only when the step has a clip), Reset the demo (= `R`, same confirm), Hide the bar (= `H`, a "Show the presenter bar" handle stays bottom-right), Exit presenter (= `Ctrl+Shift+P`), More controls (= `?`) |
| Who | The demo driver |
| Where | `components/presenter/PresenterBar.tsx`; each control calls the same function as its key in `controller.ts` (`advance`, `goBack`, `startOnce`, `toggleClip`, `askReset`, `toggleHidden`, `exitPresenter`) |
| Side buttons | The mouse's back and forward side buttons are Back and Next while armed; the browser does not also go back or forward. Ignored while a dialog has the input |
| Browser Back | Going back to the **previous step's page** steps the script back with it. Any other history move (Forward, or Back to a page the driver opened by clicking around) is left to the browser; the next Next opens its own page. Ignored while a step runs |
| Guarantees | Same busy, 400 ms debounce and failed-step rules as the keys. Ordinary clicks (links, navigation, answer cards) are never taken over. Every control has a tooltip with its key, an accessible name, `aria-keyshortcuts`, a focus ring and a 32 px target; English and 简体中文. On Stage the bar is one row and shows only "More" |
| Tests | `presenter.spec.ts` 23/23 (includes "armed: ordinary clicks stay ordinary"); *re-run pending (final integration)*. Spec: [PRESENTER_KEYS.md section 2a](rubric/demo_story/PRESENTER_KEYS.md) |
| Limits | Not committed yet at the time of writing. The side-button and browser-Back rules are tested in a headless browser, not with a physical mouse on the stage laptop |

### P2. Stage theme

| | |
|---|---|
| What | A projector theme: larger type, navigation and chrome hidden, a one-row HUD (step, title, next, status dots, "synthetic data" label), content inside a safe area |
| Who | The demo driver |
| Where | Any page while presenter mode is armed; key **S** |
| How | 1. Arm presenter mode. 2. Press **S**. 3. Press **?** to show the key legend for a moment |
| Guarantees | The "synthetic data" label is always visible. Speaker cues never show in Stage |
| Tests | `presenter.spec.ts` |
| Screens | [presenter/stage-01-compare-amy-rachel.png](screens/presenter/stage-01-compare-amy-rachel.png) · [live/presenter-stage-theme.png](screens/live/presenter-stage-theme.png) · [presenter/dark-00-title-rehearsal-bar.png](screens/presenter/dark-00-title-rehearsal-bar.png) |

### P3. Slides: title, proof, business, close

| | |
|---|---|
| What | Four slides with a QR code. Proof and business read their numbers from data files (`public/eval_summary.json`, `public/finals/slides.json`, `value_defaults.json`, `fixlog.json`) |
| Who | Anyone |
| Where | `/present`, `/present/proof`, `/present/business`, `/present/close` |
| How | 1. Run presenter mode to step 8, or open `/present/proof`. 2. Read the numbers. 3. Space moves on |
| Guarantees | R5/R7: a stale or missing eval summary says "re-run pending" instead of old numbers. Hy3 and TokenHub are named only while the model is live |
| Tests | `presenter.spec.ts` "slides read their numbers from data files…", "slides name Hy3 and TokenHub only while the model is live" |
| Screens | [presenter/stage-08-proof.png](screens/presenter/stage-08-proof.png) · [presenter/stage-09-business.png](screens/presenter/stage-09-business.png) · [presenter/stage-10-close.png](screens/presenter/stage-10-close.png) · [live/present-proof.png](screens/live/present-proof.png) |

### P4. Backup clips (new, `baf8a07`)

| | |
|---|---|
| What | Three muted local recordings that **B** plays full screen for the current step, always labelled "Recording · not live". A: Amy vs Rachel, Ken identical, citation [2] (23.5 s). B: Maya's journey, Verify, tamper on a copy (14.1 s). C: an in-app stand-in for the WorkBuddy step (Jordan's headline answer, 25.4 s) |
| Who | The demo driver |
| Where | `databridge/apps/internal_brain_web/public/clips/` (`clipA_compare_amy_rachel_ken.mp4`, `clipB_journey_audit_tamper.mp4`, `clipC_workbuddy_jordan.mp4`); `components/presenter/ClipOverlay.tsx` |
| How | 1. In presenter mode, if a step shows nothing after 8 s, press **B**. 2. The clip plays. 3. **B** or **Esc** returns to live |
| Guarantees | A recording is never presented as live (badge always on). No network. A missing file says "Clip not recorded yet" with the expected path |
| Tests | `presenter.spec.ts` "B plays the recording (or says it is not recorded yet) and B returns; L flips the language" |
| Screens | [presenter/stage-clip-overlay.png](screens/presenter/stage-clip-overlay.png) |
| Limits | Interim recordings from 30 Oct (Playwright, no Stage theme). Clip C is not WorkBuddy itself. Re-record after the Monday freeze ([BACKUP_PLAN.md](rubric/demo_story/BACKUP_PLAN.md#recorded-clips-status-30-oct-2026)) |

## 4. Developer mode

Developer mode shows every page to everyone. Pages that need a role show a lock card that explains why
(least privilege), never the data.

### D1. Mission control

| | |
|---|---|
| What | The ten-step pipeline (signed identity, ledger, Gate 1, relevance, Gate 2, ledger, model chain, grounding, ledger, answer), each with its live p50 from sealed timings and its guarantee; source freshness per platform; ledger head; model status |
| Who | Anyone; organisation numbers need `compliance_officer` (others see their own requests) |
| Where | `/dev` → `GET /ib/status`, `GET /ib/sources`, `GET /audit/stats` |
| How | 1. Sign in as Maya. 2. Switch to **Developer**. 3. Select a step to read its guarantee |
| Guarantees | R5 (numbers from the sealed ledger); `/ib/status` is authenticated and never returns a secret |
| Tests | `analytics.spec.ts` "mission control" (2 tests); `ask.spec.ts` "per-platform source status lives in Developer mode"; backend `test_dev_views.py::test_answer_release_records_stage_timings`, `test_graph_doc.py::test_status_is_authenticated_and_never_returns_a_secret` |
| Screens | [analytics/mission-compliance-1280-dark.png](screens/analytics/mission-compliance-1280-dark.png) · [analytics/mission-employee-1280-light.png](screens/analytics/mission-employee-1280-light.png) · [live/dev-maya-mission.png](screens/live/dev-maya-mission.png) |

### D2. Request journey

| | |
|---|---|
| What | **Compliance:** any request end to end: a millisecond waterfall (Accept, Gate 1, Gate 2, Seal, Compose, Release), the Gate-2 verdict per document with its reason code, the model, and the three sealed ledger records on the same axis. Never the answer text. **Everyone else:** a receipt of their own request: steps ticked, total time rounded, the cited sources they can still open, the record hashes |
| Who | `compliance_officer` for any request; anyone for their own |
| Where | `/dev/journey`, `/dev/journey/{req}` → `GET /audit/journey/{req}` (compliance), `GET /ib/trace/{req}` (own) |
| How | 1. As Maya, open **Journey**. 2. Pick Rachel's Velquarry request. 3. Read the waterfall and the three seals |
| Guarantees | R4: someone else's request and an unknown id look the same to an employee; a refused request's receipt shows no documents. R3: a trace or journey failure is ledgered before the 503/500 |
| Tests | `analytics.spec.ts` "request journey" (4 tests); `a11y-shell.spec.ts` journey tests; backend `test_dev_views.py::test_journey_is_compliance_only_and_has_no_answer_text`, `test_graph_doc.py::test_trace_lists_only_cited_docs_still_openable`, `::test_trace_restricted_question_equals_nonexistent_question`, `test_final_review_fixes.py::test_trace_reconstruction_failure_is_ledgered_then_503` |
| Screens | [analytics/journey-compliance-1280-dark.png](screens/analytics/journey-compliance-1280-dark.png) · [analytics/journey-employee-1280-light.png](screens/analytics/journey-employee-1280-light.png) · [live/dev-maya-journey-seeded-denial.png](screens/live/dev-maya-journey-seeded-denial.png) · [live/dev-jdoe-receipt.png](screens/live/dev-jdoe-receipt.png) · [a11y/journey-detail-1280x720-dark.png](screens/a11y/journey-detail-1280x720-dark.png) |

### D3. Knowledge graph

| | |
|---|---|
| What | Everything you can open as one WebGL graph (Sigma): Jordan 867 items, 17 places, 4 platforms; Ken 24. Search to focus, one-hop expand, a node panel (overview, passage, lineage, links), a table view, and "Compare two people" as two separately signed-in graphs |
| Who | Anyone |
| Where | `/dev/graph` → `GET /ib/graph`, `POST /ib/graph/openable`, `GET /ib/doc/{id}` |
| How | 1. As `jdoe`, open **Knowledge graph**. 2. Search "migration" and select a node. 3. **Compare two people** → Ken |
| Guarantees | R4: the graph and document view are identical with and without restricted documents; restricted and unknown documents give the same 404; no greyed placeholders. Edges come from authorised current versions; hidden counts are exact or absent; graph views are ledgered with counts only |
| Tests | `graph.spec.ts` (7 tests, incl. "ken.ext: only his own places; nothing hints that more exists", keyboard pan/zoom); backend `test_graph_doc.py` (27 tests), `test_final_review_fixes.py` graph tests (edges, focus budget, search re-check) |
| Screens | [graph/01-galaxy-dark.png](screens/graph/01-galaxy-dark.png) · [graph/03-panel-passage.png](screens/graph/03-panel-passage.png) · [graph/08-compare-jdoe-ken.png](screens/graph/08-compare-jdoe-ken.png) · [graph/11-mobile.png](screens/graph/11-mobile.png) · [live/dev-jdoe-graph-compare.png](screens/live/dev-jdoe-graph-compare.png) |

### D4. Walnut engine view

| | |
|---|---|
| What | The evidence engine underneath: the entity graph of Company A (932 entities, 2,246 links, 4,710 pointers), a time slider, provenance down to the file and row, and the ontology. Read-only |
| Who | **Only `jdoe`**, the persona the snapshot was built for (`WALNUT_GRAPH_PERSONA`). Everyone else, compliance included, sees a lock card (`bc8414c`) |
| Where | `/dev/brain` → `/api/walnut/*` → the Walnut gateway on 127.0.0.1:8300 (on only with `WALNUT_PROXY=1`, which `demo_up.sh` sets) |
| How | 1. Sign in as `jdoe`. 2. Open **Walnut engine**. 3. Try the time slider, then **Provenance** |
| Guarantees | The snapshot is not live-checked and its reads are not ledgered, so only its own persona may read it. Proxy: off by default (same 404 as an unknown path), always off when deployed, GET-only on an exact path allowlist, query parameters rebuilt, loopback Host and same-origin only, valid IB session required, no browser cookies forwarded, local file paths stripped |
| Tests | `engine.spec.ts` (15 tests, 14 need `WALNUT_PROXY=1`), incl. "only the snapshot persona may explore; anyone else (compliance included) sees the lock card"; `engine-proxy.spec.ts` (11 tests), incl. "compliance may NOT read the snapshot", "a foreign Host (DNS rebinding) or a cross-site fetch is refused" |
| Screens | [engine/live-01-graph-dark-1280.png](screens/engine/live-01-graph-dark-1280.png) · [engine/live-02-brain-rewound-dark-1280.png](screens/engine/live-02-brain-rewound-dark-1280.png) · [engine/live-03-provenance-dark-1280.png](screens/engine/live-03-provenance-dark-1280.png) · [engine/live-06-locked-ken-dark-1280.png](screens/engine/live-06-locked-ken-dark-1280.png) · [engine/07-proxy-off-dark-1280.png](screens/engine/07-proxy-off-dark-1280.png) |

### D5. Analytics

| | |
|---|---|
| What | Requests, answer rate, latency p50/p95, Gate-2 deny rate and reasons, grounding drops, model fallback, daily outcomes, a request Sankey, the eval panel; all from the verified ledger. A banner counts seeded replay; **Live only** removes it; a window switch (days) |
| Who | `compliance_officer`; others see a lock card |
| Where | `/dev/analytics` → `GET /audit/stats?days=&replay=include|exclude` |
| How | 1. As Maya, open **Analytics**. 2. Switch the window. 3. Press **Live only** |
| Guarantees | R5: computed on the server, cached on the verified ledger head; a ledger that does not verify gives 503 and is never cached. Every chart has a table. Each stats call is itself ledgered |
| Tests | `analytics.spec.ts` "analytics" (compliance, lock card, axe and reflow); backend `test_dev_views.py::test_stats_needs_compliance_and_is_ledgered`, `test_dev_views_hardening.py` (6 tests) |
| Screens | [analytics/analytics-compliance-1280-dark.png](screens/analytics/analytics-compliance-1280-dark.png) · [analytics/analytics-employee-1280-light.png](screens/analytics/analytics-employee-1280-light.png) · [live/dev-maya-analytics.png](screens/live/dev-maya-analytics.png) · [live/dev-jdoe-analytics-locked.png](screens/live/dev-jdoe-analytics-locked.png) |

### D6. Audit console

| | |
|---|---|
| What | Plain-English audit search ("Show me everything jdoe accessed related to the payment-gateway space in the last 30 days"), with the parsed filter shown as removable chips before the results; a manual filter; a row drawer that opens the journey; reveal-with-reason for a stored answer; **Verify audit chain** (record count, head, external match); who accessed a document |
| Who | `compliance_officer`; employees and IT admins see a lock card |
| Where | `/dev/audit` (old `/audit` redirects, keeping `?verify=1` / `?tamper=1`) → `POST /audit/ask`, `GET /audit/events`, `POST /audit/reveal`, `GET /audit/verify`, `GET /audit/doc/{id}/accessors` |
| How | 1. As Maya, open **Audit**. 2. Type the question above. 3. Press **Verify audit chain** |
| Guarantees | R3: every audit call is itself ledgered. Rules parse first; the Hy3 parser runs only when the rules recognise nothing, and falls back to the rules on bad output (Hy3 not live). Reveal needs a reason. Offline title resolution from the local index (`8582f4b`) |
| Tests | `audit.spec.ts`, `audit-integrity.spec.ts` (8 tests, incl. keyboard only, `?verify=1`), `audit-a11y.spec.ts`; backend `test_audit_query.py::test_s5_reconstruction`, `test_audit_nl_filter.py`, `test_audit_nl_llm.py` (11 tests), `test_audit_routes.py`, `test_audit_routes_real_principal.py` |
| Screens | [audit/01-audit-search-table-dark-1280.png](screens/audit/01-audit-search-table-dark-1280.png) · [audit/02-verify-green-light-1280.png](screens/audit/02-verify-green-light-1280.png) · [audit/05-audit-drawer-dark-1280.png](screens/audit/05-audit-drawer-dark-1280.png) · [live/dev-maya-audit-search.png](screens/live/dev-maya-audit-search.png) · [live/dev-maya-audit-reveal.png](screens/live/dev-maya-audit-reveal.png) · [live/dev-jdoe-audit-locked.png](screens/live/dev-jdoe-audit-locked.png) |

### D7. Sandbox tamper demo

| | |
|---|---|
| What | Runs attacks on a **copy** of the live audit log and shows one row per attack: editing one old record gives "Chain broken at entry n"; deleting the tail, restoring an old backup and deleting the anchors are flagged; one row honestly reads "Not detectable: the stated window" (a rollback after the last off-host publication). The banner says "Your real audit log was not modified" |
| Who | `compliance_officer` |
| Where | `/dev/audit` → **Tamper demo**, or `?tamper=1` → `POST /audit/tamper-demo` |
| How | 1. As Maya, open **Audit**. 2. **Tamper demo** → **Run the tamper demo on a copy**. 3. Read each row; Verify again shows the real chain intact |
| Guarantees | R3: ledgered write-ahead before it runs. The live log's bytes are unchanged; the temp copy is removed; runtime is bounded (budget skips instead of running long); a second run while busy is 409; a failed temp directory does not lock the demo |
| Tests | `audit-integrity.spec.ts` "tamper demo: a broken entry in red on the copy, and the real log was not modified"; backend `test_tamper_demo_route.py` (11 tests), `test_audit_tamper.py`, `test_audit_verify_content.py` |
| Screens | [audit/03-tamper-demo-dark-1280.png](screens/audit/03-tamper-demo-dark-1280.png) · [audit/08-tamper-demo-zh-dark-1280.png](screens/audit/08-tamper-demo-zh-dark-1280.png) · [live/dev-maya-audit-tamper.png](screens/live/dev-maya-audit-tamper.png) · [a11y/audit-tamper-en-1280-dark.png](screens/a11y/audit-tamper-en-1280-dark.png) |

### D8. Simulator

| | |
|---|---|
| What | Change access at the source and watch the next answer change: remove `jdoe` from #db-migration, restrict a Confluence page, edit the failover runbook (new version). Each action says what it proves and links to a prefilled Compare. **Reset** runs a full reconciliation, so a revocation is undone at once (`8582f4b`) |
| Who | `admin_it` only |
| Where | `/dev/sim` (old `/sim` redirects) → `POST /sim/revoke-channel`, `/sim/restrict-page`, `/sim/runbook-update`, `/sim/reset` |
| How | 1. Sign in as `admin.it`, open **Simulator**. 2. Press "Remove jdoe from #db-migration". 3. Ask as `jdoe`: "What backfill throttle are we using for the ledger copy job?" (refused); then **Reset** |
| Guarantees | R1: revocation is honoured on the very next question even if the webhook is lost (Gate 2 asks the platform live). A fixed set of admin actions only |
| Tests | `sim.spec.ts` (2 tests); `ask.spec.ts` "simulator: revocation and v8 runbook are reflected on the next question"; backend `test_scenarios_e2e.py::test_s4_revocation_live`, `::test_s4_revocation_without_webhook`, `::test_s2_freshness_via_webhook`, `test_sim_reset_reconcile.py` (3 tests), `test_revoke_concurrency.py` |
| Screens | [audit/06-sim-dark-1280.png](screens/audit/06-sim-dark-1280.png) · [live/dev-admin-sim-revoked.png](screens/live/dev-admin-sim-revoked.png) · [live/sim-compare-before.png](screens/live/sim-compare-before.png) · [live/sim-compare-after-revoke.png](screens/live/sim-compare-after-revoke.png) · [live/sim-compare-after-reset.png](screens/live/sim-compare-after-reset.png) |

### D9. Trust centre

| | |
|---|---|
| What | Seven controls (filter before the model, identity, write-ahead ledger, one response shape, grounding, reveal with a reason, masking), each with what it is, why it matters, the framework mapping (AI Verify, IMDA GenAI, MAS FEAT and more) and the test that proves it |
| Who | Anyone |
| Where | `/dev/trust` (`components/trust/data.ts`) |
| How | 1. Open **Trust centre**. 2. Expand a control. 3. Follow its test name |
| Guarantees | R7: crosswalk counts come from [FRAMEWORK_CROSSWALK.md](rubric/responsible_ai/FRAMEWORK_CROSSWALK.md); honest wording is tested |
| Tests | `audit-trust.spec.ts` "trust centre: seven controls, crosswalk counts…, honest wording"; `audit-a11y.spec.ts` "trust centre" |
| Screens | [audit/07-trust-dark-1280.png](screens/audit/07-trust-dark-1280.png) · [live/dev-jdoe-trust.png](screens/live/dev-jdoe-trust.png) |

### D10. Style guide

| | |
|---|---|
| What | The design tokens and primitives (colours, type, badges, cards, tables) in both themes |
| Who | Anyone (Developer mode footer) |
| Where | `/dev/styleguide` |
| How | 1. Developer mode. 2. **Style guide** at the bottom of the side nav. 3. Flip the theme |
| Guarantees | None beyond display |
| Tests | None specific |
| Screens | [live/dev-styleguide.png](screens/live/dev-styleguide.png) · [live/prod-styleguide.png](screens/live/prod-styleguide.png) |

## 5. Shell, tours and navigation

### S1. Mode switch and side navigation

| | |
|---|---|
| What | One app, two modes, switched at the top left. User mode hides pages a role cannot use; Developer mode shows them locked |
| Who | Anyone |
| Where | Every page (`components/shell/`) |
| How | 1. Press **Developer** at the top left. 2. Pick a page. 3. Press **User** to go back |
| Guarantees | Locking is a display convenience; the backend enforces every role (`require_role`) |
| Tests | `ask.spec.ts` "role gates: employees cannot open audit or simulator"; `audit-integrity.spec.ts` lock card tests |
| Screens | [live/user-jdoe-home.png](screens/live/user-jdoe-home.png) · [live/dev-jdoe-mission.png](screens/live/dev-jdoe-mission.png) |

### S2. Command palette

| | |
|---|---|
| What | **Ctrl/Cmd+K**: go to any page of either mode, switch mode, theme, language and text size |
| Who | Anyone |
| Where | Every page (`components/shell/CommandPalette.tsx`) |
| How | 1. Press Ctrl+K. 2. Type a page name. 3. Enter |
| Guarantees | Focus trap, Esc and focus return (Radix Dialog) |
| Tests | No dedicated spec |
| Screens | None |

### S3. Tours

| | |
|---|---|
| What | **User "Try it"** (4 steps and a checklist: sign in, ask, open a source, Ken's identical refusals) and **Developer** (5 steps: pipeline, journey, graph, trust, then analytics and Verify for compliance or the least-privilege note for others). Spotlight and "Do it for me". The two tours are separate (D-36): each mode offers its own on first entry, the header button starts the current mode's tour, and neither chains into the other. A first-visit offer (at the top on phones since `810ddbb`) |
| Who | Anyone |
| Where | "Take the tour" in the header; Settings → replay |
| How | 1. Press **Take the tour**. 2. Press **Do it for me** or do the step. 3. Arrows move, Esc closes |
| Guarantees | Keyboard only works end to end; a missing anchor centres the card with a note; a mode switch never carries a tour across |
| Tests | `tour.spec.ts` (11 tests); `tour-screens.spec.ts` (6, `TOUR_SCREENS=1`) |
| Screens | [tour/](screens/tour/) (36 files), e.g. [tour/07-user-step4-identical-dark.png](screens/tour/07-user-step4-identical-dark.png), [tour/17-dev-step5-locked-light.png](screens/tour/17-dev-step5-locked-light.png) |

### S4. Deep links and the session check

| | |
|---|---|
| What | A deep link survives sign-in (`?next=`), and only same-site paths are honoured. A slow session check shows a message and "Check again" after 8 s |
| Who | Anyone |
| Where | Every page (`components/shell/nextPath.ts`) |
| How | 1. Open `/dev/audit` signed out. 2. Sign in. 3. You land on `/dev/audit` |
| Guarantees | No open redirect |
| Tests | `a11y-shell.spec.ts` "deep link survives sign-in (?next=)…", "a slow session check shows a message…" |
| Screens | [a11y/session-slow-1280-dark.png](screens/a11y/session-slow-1280-dark.png) |

## 6. Backend APIs

Internal Brain service, `databridge/apps/internal_brain/databridge_ib/`, port 8100. Contract:
[contracts/backend_v1.md](contracts/backend_v1.md). The browser reaches it only through the web proxy
([X2](#x2-web-proxy-allowlist-to-the-brain)). Every non-public route needs the signed token
(`test_app_routes.py::test_every_non_public_route_requires_auth`, `::test_only_allowlisted_routes`).

| # | Endpoint | Role | What it does | Guarantees | Tests |
|---|---|---|---|---|---|
| B1 | `GET /health` | public | Liveness | — | `test_app_routes.py` |
| B2 | `POST /ib/ask` | employee roles | The full pipeline: Gate 1, live Gate 2, model chain, grounding, one response shape | R1–R4 | `test_pipeline_core.py`, `test_scenarios_e2e.py`, `test_fintech_scenarios.py` |
| B3 | `POST /ib/ask/stream` | employee roles | The same as SSE: `stage` events (`accepted, gate1, gate2, sealed, composing, grounded, released`), then `answer`, then `done`; any failure ends with `error` and no answer | Names only; gate1/gate2 held until `sealed`; dropped if the request fails before sealing; same ledger records as `/ib/ask` | `test_ask_stream.py`, `test_final_review_fixes.py::test_held_stages_are_dropped_when_the_request_fails_before_sealing` |
| B4 | `GET /ib/history` | employee roles | Own questions, labelled live or replay | R2 | `test_graph_doc.py::test_history_labels_channel_and_replay` |
| B5 | `GET /ib/trace/{req}` | own requests | The public receipt: stages, timings, model, grounding, cited documents still openable, record hashes | R4: never the considered set or Gate-2 reasons; failures ledgered | `test_graph_doc.py` trace tests, `test_final_review_fixes.py` trace tests |
| B6 | `GET /ib/graph`, `POST /ib/graph/openable` | employee roles | Permission-aware graph overview, focus, search; which ids the caller can open | R4; ledgered with counts only; bounded params | `test_graph_doc.py`, `test_final_review_fixes.py` |
| B7 | `GET /ib/doc/{id}` | employee roles | Whitelisted fields, best passage, permitted links, drift | Restricted and unknown are the same 404 | `test_graph_doc.py::test_doc_restricted_and_unknown_are_the_same_404` |
| B8 | `GET /ib/map`, `GET /ib/sources`, `GET /ib/status` | employee roles | Map; per-platform freshness; model and ledger status | No secret in status | `test_knowledge_map.py`, `test_graph_doc.py::test_status_is_authenticated_and_never_returns_a_secret` |
| B9 | `POST /auth/demo-login`, `POST /auth/logout`, `GET /auth/whoami` | public / session | Demo sign-in, sign-out, who am I | Passcode required when deployed | `test_app_routes.py`, `test_config_deployable.py` |
| B10 | `/audit/events`, `/audit/ask`, `/audit/reveal`, `/audit/verify`, `/audit/doc/{id}/accessors`, `/audit/stats`, `/audit/journey/{req}`, `/audit/tamper-demo` | `compliance_officer` | Audit search, reveal, verify, analytics, journey, tamper on a copy | Every call ledgered; R5 | `test_audit_*.py`, `test_dev_views*.py`, `test_tamper_demo_route.py` |
| B11 | `/sim/revoke-channel`, `/sim/restrict-page`, `/sim/runbook-update`, `/sim/reset` | `admin_it` | A fixed set of platform admin actions; reset reconciles | — | `test_sim_reset_reconcile.py`, `test_scenarios_e2e.py` |
| B12 | `POST /webhooks/{platform}` | signed webhook | Platform change events (membership, ACL, edits) update the index | Signature checked; size capped; Gate 2 still checks live if a webhook is lost | `test_connector_*.py`, `test_scenarios_e2e.py::test_s4_revocation_without_webhook` |

**Two MCP servers** (the WorkBuddy connectors, unchanged since submission): `mcp_server.py`
(`ask_internal_brain`, `my_recent_questions`) and `audit_mcp_server.py` (`audit_events`,
`audit_doc_accessors`, `audit_verify`). Each carries its own signed token. Tests:
`test_mcp_server.py`, `test_mint_token_cli.py`. Guide: [WorkBuddy](../tencent/workbuddy/README.md).

**Seeded replay** (`--fresh`): about 300 questions through the real pipeline, labelled
`channel="replay"` and `synthetic_replay=True`, plus two silent revocations that produce real Gate-2
denials. The replay switch is refused in a deployed config. Tests: `test_seed_plan.py`,
`test_seed_silent_revocations.py`, `test_graph_doc.py::test_replay_switch_refuses_every_unsafe_call`,
`::test_deployed_config_refuses_a_replay_key`.

## 7. Security controls

| # | Control | Where | Proof |
|---|---|---|---|
| X1 | **Host allowlist on the Brain and the mock platforms** (`7bf3246`): loopback always; the public origin's host when deployed; plus `IB_ALLOWED_HOSTS`. Anything else is a 400 before any route runs (DNS rebinding) | `databridge_ib/config.py::trusted_hosts`, `TrustedHostMiddleware` in `app.py`; `mock_platforms/app.py` | `test_final_review_fixes.py::test_a_foreign_host_header_is_refused_before_any_route`, `::test_trusted_hosts_follow_deployment`; `mock_platforms/tests/test_mock_auth_ratelimit.py::test_a_foreign_host_header_is_refused` |
| X2 | <a id="x2-web-proxy-allowlist-to-the-brain"></a>**Web proxy allowlist to the Brain**: closed path list (no `/webhooks`), plain segments only, POSTs same-origin with JSON ≤ 64 KiB, loopback Host and no cross-site GETs (`bc8414c`), only the session cookie forwarded, upstream 5xx replaced | `app/api/ib/[...path]/route.ts` | `proxy.spec.ts` (4 tests) |
| X3 | **Engine view gating** (`bc8414c`): the engine snapshot is readable only by its own persona; the proxy is off by default and GET-only | `app/api/walnut/[...path]/route.ts` | `engine-proxy.spec.ts`, `engine.spec.ts` ([D4](#d4-walnut-engine-view)) |
| X4 | **SSE held gate events** (`7bf3246`): gate1/gate2 sent only when the decision is sealed, so stream timing cannot tell a stale-denied document from an absent one | `app.py` `/ib/ask/stream` | `test_final_review_fixes.py::test_gate1_and_gate2_are_held_until_the_checks_are_sealed` |
| X5 | **Two gates before any model; deny on error** | `gates.py`, `pipeline.py` | `test_pipeline_core.py`; eval 0 leaks / 7,374 |
| X6 | **Write-ahead, hash-chained, sealed ledger** with off-host head publication | `audit/ledger.py`, `audit/anchor.py` | `test_audit_ledger.py`, `test_audit_tamper.py`, `test_audit_verify_content.py` |
| X7 | **Card and ID masking before indexing** | `sensitive.py` | `test_sensitive.py`, `test_fintech_scenarios.py::test_sens_raw_values_appear_nowhere_in_the_index` |
| X8 | **One deadline through read-through; caps after live auth** (`7bf3246`) | `authz.py`, `graph.py`, `docview.py` | `test_final_review_fixes.py::test_read_through_is_bounded_by_the_same_deadline`, `::test_checks_past_the_deadline_are_denied_and_undecided` |
| X9 | **Deployed config refuses dev keys and demo login without passcode** | `config.py` | `test_config_deployable.py` (5 tests) |
| X10 | **Prompt injection contained** on all four platforms | grounding, `narrate.py` | eval: 0 hard failures in 1,152 injection-relevant requests |

The full threat model and every review finding: [SECURITY.md](../tencent/SECURITY.md), in particular
[§15](../tencent/SECURITY.md#15-developer-views-and-the-finals-v2-additions-30-oct). Final reviews:
[reviews/final_opus_security.md](reviews/final_opus_security.md), [reviews/final_codex.md](reviews/final_codex.md).

## 8. Accessibility and i18n

Target WCAG 2.2 AA. Statement: [ACCESSIBILITY_STATEMENT.md](rubric/hcd_ux/ACCESSIBILITY_STATEMENT.md).

| # | Feature | Proof | Screens |
|---|---|---|---|
| A1 | **axe in the e2e suite**: no serious or critical issue on every page, dark and light, 1280 and 390 px | `a11y.spec.ts`, `audit-a11y.spec.ts`, `user.spec.ts`, `analytics.spec.ts`, `engine.spec.ts` axe tests | [a11y/](screens/a11y/) |
| A2 | **Keyboard only**: sign in, ask, citation chip, audit, graph pan and zoom, tours, presenter | `keyboard.spec.ts`, `audit-integrity.spec.ts`, `graph.spec.ts`, `tour.spec.ts`, `presenter.spec.ts` | [tour/02-user-step2-ask-dark.png](screens/tour/02-user-step2-ask-dark.png) |
| A3 | **English / 简体中文** across the User-mode interface (the server's refusal sentence and scope note stay English, [QA_LIVE.md §4](QA_LIVE.md) item 6); Developer mode notes that some technical terms stay English; English starter questions tagged `lang="en"` (WCAG 3.1.2, `ca7a2ff`); 简体中文 skip link and roles | `user.spec.ts` language test; `a11y-shell.spec.ts` "zh: skip link, persona role and `<html lang>`…" | [user/11-zh-home-1280-dark.png](screens/user/11-zh-home-1280-dark.png) · [a11y/zh-skip-link-1280-dark.png](screens/a11y/zh-skip-link-1280-dark.png) · [a11y/zh-dev-english-note-390-dark.png](screens/a11y/zh-dev-english-note-390-dark.png) |
| A4 | **Reflow and text size**: top bar reflows at 130% text and 200% zoom; no horizontal overflow at 390 px | `a11y-shell.spec.ts` reflow tests; `a11y.spec.ts` "no horizontal page overflow at 390px" | [a11y/reflow-text130-390-dark.png](screens/a11y/reflow-text130-390-dark.png) · [a11y/reflow-text130-zoom200-1280-dark.png](screens/a11y/reflow-text130-zoom200-1280-dark.png) |
| A5 | **Targets and live regions**: citation chips at least 24 × 24 px; one shared live region; unique landmarks; skip link | `a11y-shell.spec.ts` chip, live region and landmark tests | [a11y/citation-chips-1280-light.png](screens/a11y/citation-chips-1280-light.png) |
| A6 | **Voice dictation** as an alternative input ([U3](#u3-voice-dictation-new-1bc6b4c)) | `voice.spec.ts` | none yet |

`a11y-shell.spec.ts` runs with its own config (`npx playwright test -c tests/e2e/a11y-shell.config.ts`)
and is not in the 166 count ([TEST_REPORT.md §1](TEST_REPORT.md#not-in-this-run-a11y-shellspects)).
Not tested: a screen reader itself (NVDA / VoiceOver), and the hallway test with real people (pending).

## 9. Tooling and scripts

Run from `databridge/`. `PY` is `.venv/Scripts/python` on Windows, `.venv/bin/python` elsewhere.

| # | Tool | What it does | How | Proof |
|---|---|---|---|---|
| T1 | [`scripts/demo_up.sh`](../../databridge/scripts/demo_up.sh) | Starts the Demo Day stack on 127.0.0.1 in order: mock platforms :8200, the Brain :8100, the seed (`--fresh`), the Walnut engine gateway :8300 (and original screens :3300), a production web build :3100. **Turns live Hy3 on by itself** when `hy3_check.py --quiet` says TokenHub answers, unless `IB_FORCE_OFFLINE` is already set (`8b69cbd`) | `bash databridge/scripts/demo_up.sh start [--fresh]` · `status` · `stop`. Logs in `databridge/var/ib-final/logs/` | Used for every `live/` screenshot and the clips |
| T2 | [`scripts/hy3_check.py`](../../databridge/scripts/hy3_check.py) | Is Hy3 live on TokenHub for this account? One small call per configured model (`hy3`, then `deepseek-v4.1-flash`, or `IB_MODELS`). Prints a table with hints (401006: not activated; 400004: unknown model id). Never prints the key | `PY scripts/hy3_check.py` (exit 0 live, 1 not live, 2 no key); `--quiet` for exit code only | Today: exit 1 (401006) |
| T3 | [`scripts/seed_demo_activity.py`](../../databridge/scripts/seed_demo_activity.py) | About 300 labelled replay questions through the real pipeline, compliance audit calls, two simulator revocations restored, two silent revocations (real Gate-2 denials). Never puts a restricted title into a persona's own history | Called by `demo_up.sh --fresh`; `--dry-run` prints the plan | `test_seed_plan.py`, `test_seed_silent_revocations.py` |
| T4 | [`scripts/eval_summary.py`](../../databridge/scripts/eval_summary.py) | Turns the eval's `results.json` into `public/eval_summary.json` for the slides and Analytics. `stale: true` when the Brain's code digest differs from the one the eval ran on | `PY scripts/eval_summary.py [--e2e N]` | `test_eval_summary.py` (2 tests) |
| T5 | [`scripts/tamper_demo.py`](../../databridge/scripts/tamper_demo.py) | The command-line tamper demo: eight scenarios on copies of a ledger (baseline, edit-record, suffix-delete, directory-rollback, rollback-after-publication, remove-internal-anchors, external-head-missing, key-holder-rewrite). Shares its logic with `POST /audit/tamper-demo` | `PY scripts/tamper_demo.py` | `test_audit_tamper.py`, `test_tamper_demo_route.py` |
| T6 | `evals/internal_brain/run.py` | The leak, differential and injection evaluation | `PY -m evals.internal_brain.run` (`--limit 60` for a quick check) | [RESULTS.md](../../databridge/evals/internal_brain/RESULTS.md); canaries: a leaky config is caught, deny-all scores 0 |
| T7 | [`scripts/ib_mint_token.py`](../../databridge/scripts/ib_mint_token.py) | Mints a short-lived token for a persona (for WorkBuddy). Never printed unless `--print` | `PY scripts/ib_mint_token.py jdoe --out FILE` | `test_mint_token_cli.py` |
| T8 | [`scripts/workbuddy_setup.py`](../../databridge/scripts/workbuddy_setup.py) | Writes three WorkBuddy workspaces (Jordan, Ken, compliance); `--user-config` adds only the two employee connectors to WorkBuddy's own list, `--compliance-user-config` only the compliance one (never both in one list) | `PY scripts/workbuddy_setup.py --user-config` | [WorkBuddy guide](../tencent/workbuddy/README.md) |
| T9 | [`scripts/run_local_demo.sh`](../../databridge/scripts/run_local_demo.sh), [`run_walnut_original.sh`](../../databridge/scripts/run_walnut_original.sh) | Fallback two-terminal start; the original Walnut screens on :3300 | See [README.md](../../README.md#fallback-start-each-part-by-hand) | — |
| T10 | `npm run dev:mock`, `npm run e2e` (web app) | The screens with built-in mock data (clearly not live); the Playwright suite on :3101 | In `databridge/apps/internal_brain_web` | [TEST_REPORT.md](TEST_REPORT.md) |

Also: a CI workflow ([.github/workflows/ci.yml](../../.github/workflows/ci.yml)) defines the backend
tests (Python 3.13) and the web typecheck (Node 20); and a Lighthouse deploy kit
([deploy/lighthouse/](../../deploy/lighthouse/README.md)) with no live instance.

## 10. Evidence

| Evidence | What it holds | File |
|---|---|---|
| Leak evaluation | 0 leaks / 7,374; 0 / 400 differential; 0 injection hard failures / 1,152; generated 30 Oct 03:27 UTC (11:27 SGT) on the finals pipeline | [RESULTS.md](../../databridge/evals/internal_brain/RESULTS.md), [eval_summary.json](../../databridge/apps/internal_brain_web/public/eval_summary.json) |
| Backend tests | **Re-run pending (final integration).** 1,131 passed, 0 failed at `831297e` (30 Oct, cloud: Linux, Python 3.13) | `databridge/apps/internal_brain/tests/` |
| Web e2e | **Re-run pending (final integration).** 166 passed, 0 failed, 41 skipped (need the live stack, the engine proxy or Hy3); mock mode, every spec `playwright.config.ts` runs, including `act`, `grow` and `deliverables` (30 Oct, cloud, `831297e`) | [TEST_REPORT.md](TEST_REPORT.md) |
| Live QA | Every page × persona against the real backend: 32 checks, 11 bugs fixed, issues listed | [QA_LIVE.md](QA_LIVE.md) |
| Final reviews | Five models: security, UX, claims, code | [reviews/](reviews/final_opus_security.md) ([Opus security](reviews/final_opus_security.md) · [Sonnet UX](reviews/final_sonnet_ux.md) · [Haiku claims](reviews/final_haiku_claims.md) · [Codex](reviews/final_codex.md) · [Fable](reviews/final_fable.md)) |
| Rubric pack | Each of the 10 criteria: seen live, in the app, to read | [rubric/README.md](rubric/README.md) |
| Screenshots | 328 PNGs in nine folders | [screens/](screens/), index in [README.md §7](README.md#7-screenshots) |
| Backup clips | A, B and C for the presenter **B** key | `databridge/apps/internal_brain_web/public/clips/` ([P4](#p4-backup-clips-new-baf8a07)) |
| Printable handouts | A4 leave-behind with a QR code, one judge card per rubric criterion, the presenter cheat-sheet; numbers read from `eval_summary.json`, STATUS.md and ROI_MODEL.md by a generator | `submission/finals/` ([submission/README.md](../../submission/README.md)) |
| Submission (16 Oct) | Deck, video, cover, CodeBuddy and WorkBuddy evidence | [submission/README.md](../../submission/README.md) |

---

## 11. Demo-proof features (behind a toggle, D-24)

Stage props, not product surfaces: off for normal users. The server mounts their routes only with
`IB_DEMO_FEATURES=1` (set only by `databridge/scripts/demo_up.sh`; ignored when `IB_DEPLOYED=1`), and the
web shows them only when `/ib/status` says `demo_features: true` AND the viewer turned **Demo features**
on in Settings (or the Presenter is armed). Each control carries a "Demo" badge. Security notes:
`docs/tencent/SECURITY.md` §15.13; contracts: `docs/finals/contracts/backend_v1.md` "Demo-proof features";
plan: [live_growth/PLAN.md](live_growth/PLAN.md).

### G1. Watch the brain grow

| | |
|---|---|
| What | The knowledge graph polls a permission-filtered change feed; a new document fades into its place's disc at the next free slot (nothing else moves), pulses twice, keeps an accent for 10 s, and is listed under **Just added** ("Slack · #db-migration · new thread · just now", "your note · appeared 2.8 s after you posted"). A **Live updates** switch pauses polling. Reduced motion: a static accent and label for 30 s |
| Who | Anyone signed in, for what they can open |
| Where | `/dev/graph` (Explore and Compare) → `GET /ib/graph/changes` |
| How | 1. Settings → Demo features on. 2. As `jdoe`, open `/dev/graph?add=1` and post the note. 3. Watch the node arrive; **Show** flies to it |
| Guarantees | Ken's reply after a note he cannot open is byte-identical (cursor included) to a world without it, and his poll makes no source call; the cursor holds only the caller and the clock; the cap applies after the live check; polls are ledgered as `graph.viewed` `mode="changes"` (counts only) |
| Tests | `test_live_growth.py` (11 tests: L1 to L9, A6 to A9); `grow.spec.ts` (6: hidden by default, post and feed, pause stops polling, Compare: only the poster receives it, plus 3 layout unit tests: existing positions exactly unchanged, deterministic, `computeLayout` unchanged) |
| Screens | [grow/04-pulse-and-just-added.png](screens/grow/04-pulse-and-just-added.png) · [grow/05-show-new-node.png](screens/grow/05-show-new-node.png) · [grow/06-ken-nothing-new.png](screens/grow/06-ken-nothing-new.png) |

### G2. Add a document

| | |
|---|---|
| What | A sheet with two tabs: **Slack note** (channel list built only from the person's own map; 2,000-character counter) and **Upload a file** (Word, Excel, PowerPoint, PDF, CSV, Markdown, text, WhatsApp export; sample files listed) |
| Who | The signed-in person, as themselves (no author field); demo sign-in on |
| Where | `/dev/graph` toolbar → `POST /sim/add-document`, `POST /sim/upload-file` |
| Folder picker (evening, D-34; re-run pending) | The upload tab lists folders by name from `GET /sim/upload-folders`: only the folders the person may upload into, checked live; a collapsed "Advanced: folder ID" field only when the list is empty or fails (D-34; `test_upload_folders.py`, `grow.spec.ts`) |
| How | **Add a document** → pick `#db-migration` → paste the note → **Post to Slack** |
| Guarantees | Live membership checked at the source at post time; one 404 for every refusal; 5 per minute; the IB never indexes the text directly (signed webhook, re-fetch, mask); answerable at once with a citation by channel members, the constant refusal for Ken; reset removes it |
| Tests | `test_sim_add_document.py` (20 tests, 29 runs, incl. validation, live membership, A1 byte-identical refusals, rate limit, deployed); `test_live_growth.py::test_added_note_is_answerable_by_members_and_ken_gets_the_constant_refusal`, `::test_sensitive_values_in_a_note_are_masked_everywhere`; uploads: `test_uploads.py` |
| Screens | [grow/02-add-sheet-prefilled.png](screens/grow/02-add-sheet-prefilled.png) · [grow/03-posted.png](screens/grow/03-posted.png) · [grow/07-compare-jdoe-cited-ken-refused.png](screens/grow/07-compare-jdoe-cited-ken-refused.png) |

### G3. Not in the brain yet

| | |
|---|---|
| What | An operator's count of indexed documents containing a phrase, before and after a note: 0, then 1, then 0 after reset (live run, 30 Oct) |
| Who | `admin_it` only (it looks past permissions); demo sign-in on |
| Where | `POST /sim/check-absent` (no UI; used from the rehearsal script) |
| Guarantees | Count and index size only, never a title or id; the phrase is ledgered as its length and SHA-256 |
| Tests | `test_sim_add_document.py::test_check_absent_*` (3) |

### G4. Engine refresh

| | |
|---|---|
| What | **Refresh snapshot** on `/dev/brain` rebuilds the Walnut engine snapshot (`build_enterprise_graph.py --as jdoe`) in the background and the engine reloads it; the button shows the step and the seconds (34 s on the demo laptop) |
| Who | jdoe (the snapshot's person) or `admin_it`; one start per 2 minutes |
| Where | `/dev/brain` → `POST /sim/engine-refresh`, `POST /sim/engine-status`; the IB calls the gateway's `POST /graph/reload` server-side |
| Guarantees | Write-ahead `ingest.sync` record; a failed build never touches the live snapshot; the gateway token never reaches the browser; the snapshot is built from the world file, so a live note is not in it |
| Tests | `test_demo_features.py` (6 engine tests) |
| Screens | [grow/08-engine-refresh-running.png](screens/grow/08-engine-refresh-running.png) · [grow/09-engine-refresh-done.png](screens/grow/09-engine-refresh-done.png) |

### G5. The toggle

| | |
|---|---|
| What | Settings → **Demo features** (shown only on a server with them on); off by default, stored in this browser |
| Guarantees | Toggle off on the server: all five routes are the unknown-route 404, signed in or not; `/ib/status` says `demo_features`; with it on, the routes are allowlisted and need a session |
| Tests | `test_demo_features.py` (5 toggle tests); `grow.spec.ts` "hidden by default" |
| Screens | [grow/01-settings-demo-toggle.png](screens/grow/01-settings-demo-toggle.png) · [grow/10-after-reset.png](screens/grow/10-after-reset.png) |

## 12. Deliverables from an answer (kill switch, D-25)

A product feature behind a kill switch, not a demo prop: `IB_DELIVERABLES=1` (off by default in code, on
in `scripts/demo_up.sh` and `scripts/run_local_demo.sh`; off, both routes answer the not-found 404 and
record nothing). Security notes: `docs/tencent/SECURITY.md` §15.14; contracts:
`docs/finals/contracts/backend_v1.md` "v4 (30 Oct): Deliverables"; plan, reviews and status:
[deliverables/](deliverables/STATUS.md).

### V1. Table, chart, analysis from an answer (P0)

| | |
|---|---|
| What | Under a cited answer, **Turn this answer into**: Table, Chart or Analysis, in a wide sheet with `[n]` chips, one metadata line, Copy, **Download .csv**, Regenerate. A chart has value labels and a data table caption |
| Who | The asker, for their OWN released answer, using only cited documents they can still open |
| Where | `AnswerCard` → `DeliverSheet`; `POST /ib/deliverables`, `POST /ib/deliverables/export`; Presenter key `D` (cached warm-up on `P`) |
| Status | **Offline (no model), tested:** Table (copied from a CSV-shaped block of a cited source, else quoted source sentences) and Chart (from a numeric source column, e.g. "FX margin by market" SG 0.35 / HK 0.35 / ID 0.4). **Analysis says "Needs the model"** without one. The model path (Hy3 or DeepSeek, task `ib_deliver`) is tested with a fake model chain only; Hy3/TokenHub was not live for this feature's cloud run |
| Guarantees | Built on Act's context (ownership, sealed answer hash, live Gate 1 + Gate 2 on every cited document); one 404 for every not-found case; paired worlds with and without restricted content give identical outputs; every number lies exactly on a number span of its own cited source line, bound to its label, unit and currency worked out by our code (Opus review items 1-5); write-ahead `deliverable.requested/created/exported/failed` with counts and digests only; exports re-check the sources; formula guard on every CSV cell |
| Tests | `test_deliverables_verify.py` (70, incl. T14 `::test_adversarial_points_are_dropped`, `::test_label_binding_csv_row_and_prose_nearest`, `::test_csv_formula_guard`); `test_deliverables.py` (24, incl. `::test_offline_table_chart_and_needs_model`, `::test_restricted_question_equals_a_world_without_it`, `::test_someone_elses_unknown_and_refused_are_one_404`, `::test_ledger_outage_is_503_and_nothing_happens`, `::test_bodies_carry_no_identity_and_flag_off_is_404`); `test_deliverables_eval.py::test_no_leak_or_injection_through_deliverables_and_exports` (T16); web `deliverables.spec.ts` (4, mock, axe) |
| Wording (evening, 30 Oct) | The by-line reads "Copied cell by cell from source [2]" (table) or "Charted from the table in source [2]" (chart), and a quiet info line says "No AI model was used: this was built by code, straight from your sources." (it no longer says "no model" in the by-line). The four fixed sheet titles are translated in 简体中文; a title worded from the source is shown as returned, marked English. Source chips have a screen-reader name ("Source 2: title"), and a "Skip to Copy and downloads" button follows the title. Tests: `deliverables.spec.ts` (by-line, info line, skip, zh title) |
| Round-4 hardening (evening) | An export writes `deliverable.requested` (with `export_format`) before Gate 2 and the source read; a model title and a model-or-fallback build are re-checked after a revoked source; each analysis number must sit in ONE source row or clause with its names (identifiers and dates are checked for presence only); a table or chart label must be its column header or clause; the rate limit and a capped 4 KB body read come first. Tests: `test_deliverables.py::test_r4_b1_*` to `::test_r4_b12_*`, `test_deliverables_verify.py::test_r4_b5_*`, `::test_r4_b6_*`; *re-run pending (final integration)*. Detail: [round4_dispositions.md](reviews/round4_dispositions.md), SECURITY section 15.14 |
| Screens | [deliverables/mock-1-table-dark.png](screens/deliverables/mock-1-table-dark.png) · [deliverables/mock-2-chart-dark.png](screens/deliverables/mock-2-chart-dark.png) · [deliverables/mock-3-analysis-dark.png](screens/deliverables/mock-3-analysis-dark.png) · [deliverables/mock-4-chart-light.png](screens/deliverables/mock-4-chart-light.png) (mock data; the live UI run is not done) |

### V2. Slides export (.pptx, P1)

| | |
|---|---|
| What | **Download slides (.pptx)** for a ready table or chart: title slide, the native table or an editable native chart, a Sources slide, speaker notes saying what was checked (the button label changed in the evening pass; before it read "Download .pptx") |
| Speaker notes (evening, B14) | They differ by slide type and claim only what that type checked. Chart slide: each value was matched to a number in its cited source cell or line, bound to its label. Table slide: the same for its numbers, and its other cells were found in the same source row or sentence. Title and Sources slides: they name the deck and its sources and state no values. Test: `test_deliverables.py::test_r4_b14_speaker_notes_claim_only_what_each_slide_type_checked` (opens the `.pptx`) |
| Formula safety (evening, B1) | Control characters are stripped before the formula guard, and the chart's embedded workbook writes every string cell as a string, so a planted `{=SUM(1,1)}` cannot become an array formula. Tests open the produced `.pptx`, extract the embedded workbook and find no formula cell: `test_deliverables.py::test_r4_b1_*` |
| Status | Backend built and tested offline (no model needed); offered only when python-pptx and XlsxWriter are installed (not in the Lighthouse image). The web button shows when the response lists `pptx`; not yet clicked in a live browser. Summary (P1) is **not built** |
| Guarantees | Built from the stored, re-checked content; every string in the deck and in the chart's embedded workbook passes the formula guard (Opus review #4, T15); 2 MB cap; `deliverable.exported` before the bytes |
| Tests | `test_deliverables.py::test_pptx_embedded_workbook_has_no_formulas`, `::test_pptx_deck_is_built_from_the_checked_content_without_a_model`, `::test_pptx_export_rechecks_a_revoked_source_and_records_failures`; `test_deliverables_verify.py::test_pptx_writer_guards_planted_hyperlink_in_categories_series_and_text`; the T16 eval exports .pptx too |

## 13. Act, and what is planned

### K1. Act: draft a reply, or a follow-up ticket (v3.2, evening 30 Oct)

Built before the evening (`8312864`, v3.1 after rounds 2 and 2b); the rows below add what round 4 changed.
Code: `databridge_ib/actions.py`, `components/user/ActSheet.tsx`. Contract: [backend_v1.md](contracts/backend_v1.md)
"Act v3.2". Security: SECURITY section 15.11.

| | |
|---|---|
| What | Under a cited answer: **Draft the reply** (the Brain never sends it; Copy only) and **Create a follow-up ticket** (a Jira issue, created only after the person confirms). Source links in a ticket are `/ib/open/<token>` links that open only for people allowed to open that source |
| Who | The asker, for their OWN released answer; a ticket needs the Jira create right (by default the Developers role) |
| Where | `POST /ib/act/draft`, `/ib/act/ticket/propose`, `/ib/act/ticket/confirm`, `/ib/act/ticket/cancel`; `GET /ib/open/{token}` |
| Behaviour in v3.2 | **Ledger first:** `action.requested` is written (stages `accepted`, `decided`, `replayed`) before the live source check, the model call and a stored result; a ledger failure is the 503 before any of them. **Draft floor:** the 80 % rule stays, and a sentence also fails on a capitalised word not in its cited facts, a number not in its anchor sentence, or a negation that differs; a failing draft falls back to the cited sentences. **One create per answer:** two proposals confirmed together never both create; the second is `409 outcome_unknown` and stays pending. **Caps** hold under concurrent proposals; unknown-outcome holds are swept. **`/ib/open`:** an access-check error is the same 404 as an unknown token; 30 a minute per person; only the first over-limit call per window is ledgered |
| What the person sees | Cannot create: "You can't create a follow-up ticket from this answer. You can still draft a reply." plus a Draft a reply button (no "Create..." header). The draft sheet is titled "Reply draft"; a chat handle (`priya.sre: ...`) shows as the person's name in the view (the copied text is the server's). Ticket text states the link rule once and ends with `Reference: <req_id>`. A 429 reads "Too many requests in a short time. Try again in a minute." After an unknown outcome, re-opening the sheet says so |
| Tests | `test_act.py`, `test_act_round2.py`, `test_act_round4.py` (`::test_draft_ledger_down_is_503_before_any_source_check_or_model_call`, `::test_floor_negation_must_match_its_source`, `::test_two_proposals_confirmed_together_never_both_create`, `::test_the_store_cap_holds_under_concurrent_proposals`, `::test_only_the_first_over_limit_act_call_per_window_is_ledgered`), `test_open_route.py` (`::test_an_access_check_error_is_the_unknown_token_404`, `::test_open_is_rate_limited_per_person_and_ledgered_once_per_window`), `test_mock_jira_create.py::test_an_empty_create_acl_means_nobody_may_create`; web `act.spec.ts`. *Re-run pending (final integration).* The e2e "re-opening the ticket sheet after an unknown outcome says so" was written, not run (it needs a separate mock dev server) |
| Limits | The web mock's ticket description still has the old text. A lower-case or CJK name in a draft is caught only by the 80 % rule. Outcome state is in memory in one process |

### K2. Proxy open-link check (evening)

On `ib/open/<32 hex>`, when the browser sends Sec-Fetch headers, the proxy requires `Sec-Fetch-Dest: document`,
`Sec-Fetch-Mode: navigate` and `Sec-Fetch-User: ?1` (a real click); a scripted navigation, an iframe or a fetch is the
one 404. Without those headers (curl, tests) the Brain's rate limit applies. Test: `act.spec.ts` "engineer: a
follow-up ticket...". Offline mock tickets link to the page's own origin and the mock has an `ib/open` route.

### Planned, not built: "Demo story" app mode

A separate, mouse-driven way through the same 11 steps (chapters), for the booth and judges' hands-on time and
for rehearsal on another laptop. In a normal browser window, so the browser's Back and Forward work. **The 5:00
stays on the keyboard presenter**, which already has clickable controls ([P1a](#p1a-presenter-mouse-controls-evening-30-oct)).
Plan v2 after four reviews: [app_mode/PLAN_v2.md](app_mode/PLAN_v2.md). **Nothing is built; no D-35 is written.**
Build window Sat 31 Oct or Sun 1 Nov; if Phase 1 is not green by Sun 1 Nov 18:00 it does not ship.


---

## 14. Pages beside the chat (just-in-time views, D-37, D-38, D-39)

An intelligence layer on top of the Brain, in the Ask view only. Same kill switch as deliverables
(`IB_DELIVERABLES=1`; off, `POST /ib/views` answers the not-found 404 and records nothing). Branch
`final_mehulupdates`. Security notes: the `databridge_ib/views/` module docstrings.

### J1. Page or chat, decided per answer

| | |
|---|---|
| What | After each answer the Brain is asked whether it needs a page (`POST /ib/views` mode `decide`). A simple answer ("What is the SG FX fee?") stays in the chat. A question that needs structure ("I am the new project lead: tell me everything about the project, its people and what each person has done") opens a page in the canvas beside the chat. **Open as page** under any answer builds one on request |
| Who decides | **Claude** (`claude-opus-5-5`, effort low) when `ANTHROPIC_API_KEY` is set and `IB_CLAUDE` is not `0`; otherwise the rules (`views/plan.py` `wants_page`). A question under 7 words with no structure word stays in the chat without a call and without a record |
| Guarantees | Ownership first; `deliverable.requested` before the live re-check and before the call; the decision is recorded as `deliverable.created` with status `page`/`chat` (counts and digests only); someone else's or an unknown answer is the one 404 |
| Tests | `test_views_composer.py::test_a_short_question_stays_in_the_chat_without_a_call_or_a_record`, `::test_claude_decides_after_the_record_and_the_live_recheck`, `::test_someone_elses_answer_is_the_one_404_for_decide_too`; web `views.spec.ts` "a simple answer stays in the chat", "a question that asks for structure opens its page by itself" |

### J2. The page: composed by Claude, every value checked

| | |
|---|---|
| What | Claude lays the page out freely: a hero summary, stat tiles, prose, **a section per person with what each one did** (expandable), tables (tone `black`/`white`/`tinted`, so "a table in black" is a black table), cards, callouts, a timeline, a chart, the chat threads, every source under its own headings. Accent colour from a closed list. Without Claude the rules lay it out (facts, table, chart, steps, timeline, conversations, by document) |
| Sources | The answer's cited documents, then up to 12 more of the asker's documents on the same subject (the same Jira project, Slack channel, Confluence space or Drive folder first, then a search on the cited titles), all over the asker's permitted set only (Gate 1) and each checked live (Gate 2 + read-through) |
| Guarantees | Every sentence passes `check_sentence` against its own tags; every table cell's words and numbers occur in its tagged source; a stat's value occurs in its source; a person's name is in the sources (or is the display name of a handle that speaks in them); headings use the person's words, the sources' words or plain page words; anything else is dropped and counted. Styles are enums: no colour codes, CSS or HTML reach the page. Paired worlds with and without the restricted note give identical pages AND identical prompts. The request text never reaches the ledger |
| Bounds | A hard spend cap (`IB_CLAUDE_BUDGET_USD`, default 10, kept in `var/.../views/claude_spend.json`; a call is refused unless its upper-bound cost still fits); 6 calls a minute per person, 20 for the service, 2 in flight; one deadline per call (25 s decide, 120 s page). A refusal, an error, a timeout, a bad reply or the cap: the rules build the page |
| Measured | On the generated world, live (30 Oct): the decision 3 to 4 s; the project page 29 s with 10 sources, 4 people and their roles, 43 of 74 proposed items kept; about US$0.05 per page |
| Tests | `test_views_composer.py` (14, fake client): `::test_claude_page_values_are_checked_and_styles_are_enums`, `::test_restricted_documents_never_reach_claude_or_the_page`, `::test_the_request_text_never_reaches_the_ledger`, `::test_the_spend_cap_stops_calls_and_survives_a_restart`, `::test_rate_limits_are_per_person_and_global`, `::test_failures_fall_back_to_the_rules`, `::test_ledger_down_before_claude_means_no_call`, `::test_read_page_is_lenient_per_block`; `test_views.py` (25: the rules, A2UI, rule 4, the ledger) |

### J3. The canvas and the Xcode-like Ask

| | |
|---|---|
| What | The chat column glides left and a white canvas slides in on the right, with an Xcode-like toolbar (title, Copy, Full screen, Close) and **Change this page** under it ("make the table black", "show people first"). With a page open, a short request about its look typed in the chat changes the page instead of asking the Brain. The sidebar shrinks to an icon rail while a page is open (and can be collapsed by hand, remembered). Below 1024 px the page is a full-screen sheet. In light mode the Ask view has its own Xcode-like tokens; the composer is one rounded field (microphone and send inside); copy and feedback are icons; the actions are one row of pills |
| Where | `app/(user)/page.tsx`, `components/user/ViewCanvas.tsx`, `components/views/catalog.tsx` (the A2UI catalog), `components/shell/SideNav.tsx` + `lib/navRail.ts`, `app/globals.css` (`.ask-x`, `.ask-split`, the rail) |
| Guarantees | Pages render through A2UI v0.9 (`@a2ui/react` 0.13.0) with the Walnut catalog only; the tree binds paths, the data model holds checked values; every string is a React text node. Scoped: other pages, dark mode and high contrast keep their tokens; light text is at least 4.5:1 |
| Tests | `views.spec.ts` (7, mock, axe): page or chat; open as page; Change this page; a chat edit does not call `/ib/ask`; the rail; light; 390 px sheet; zh and no HTML |
