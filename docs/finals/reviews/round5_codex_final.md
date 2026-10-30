# Round 5: final Codex review of the round-4 fixes

**Status: complete. This was a single run, as the Codex budget required.** It reviewed tonight's fixes to the round-4 findings, which are listed in `round4_dispositions.md`.

- **Reviewed:** branch `final_v1.2`, HEAD `54c80fb`, plus the uncommitted working tree. That is the `git diff -U10` of the in-scope files and the new `components/presenter/session.ts`.
- **In scope:** `databridge_ib/{actions,app,models,sim,live,extract_files,uploads}.py`, `audit/{ledger,stats}.py`, `deliverables/{export,service,verify,build,routes}.py` and `connectors/drive.py`. Also `scripts/workbuddy_setup.py` and the mock platforms' `state.py`, `jira.py` and `uploads.py`. On the web side: `app/api/ib/[...path]/route.ts`, `app/present/preflight/page.tsx` and `components/presenter/{session,controller}.ts`. `deliver.ts` and `Providers.tsx` were added for the session-queue context.
- **Method:** one `codex exec --sandbox read-only` run (codex-cli 0.153.4), with a single 277 KB stdin bundle. The bundle held:
  - the original round-4 findings and the in-scope dispositions;
  - the names of the new and changed tests;
  - the diffs, and `session.ts` in full.

  Codex was asked two questions: (a) is each claimed fix correct and complete; (b) did a fix introduce a new critical, high or medium problem? It used about 99k tokens. It also opened a few source files read-only for context. The log shows it opened no `.env*` file.
- **Verification:** `verify_codex_output.py` passed both the last-message file (11,282 chars) and the full log.
- **Constraints kept:** no servers were started or stopped, no tests were run, no code was changed and no commit was made.
- **Spot checks:** the Claude seat read the source for each finding. **[checked]** means the code matches Codex's description. Where the seat reads the severity differently, it says so in a note. The severities are still Codex's.
- **Line numbers:** working tree. Paths are under `databridge/`.

**Counts:** 0 critical, 7 high, 2 medium. Six claimed fixes are partial: A2, B5, B6, B7, B10 and B11. Three findings are regressions: #7, #8 and #9.

## Findings

| # | Severity | file:line | Issue | Concrete scenario | Fix |
|---|---|---|---|---|---|
| 1 | high | `apps/internal_brain/databridge_ib/actions.py:219-233` (`supported`) | **Incomplete fix of A2.** A name only has to appear somewhere in the cited facts, not in the anchor sentence. Negation is checked as one yes/no flag for the whole sentence. **[checked]**: `known` is built from all the cited sentences (line 219), and the names are checked against it (220-221). | Facts: "Staff can approve customer refund requests. Mallory manages billing." Draft: "Mallory can approve customer refund requests." Most content words match sentence 1, and "Mallory" is in sentence 2. Neither sentence is negated, so the swapped name passes. Moving a "not" between clauses also keeps the flag the same. | Check names (and numbers) against the anchor sentence(s) only. Or make draft sentences extractive. |
| 2 | high | `apps/internal_brain/databridge_ib/deliverables/verify.py:241-249` (`claim_tokens`) | **Incomplete fix of B5.** When a value is bound to its words, lower-case words under 4 letters are dropped. **[checked]**: line 248 keeps only `len(w) >= 4` lower-case words. | Evidence: "The gas cost is 10. The oil cost is 20." Proposed: "The gas cost is 20." "gas" is dropped, so the claim is only `{cost}`. The oil clause holds "cost" and 20, so the wrong pairing passes. | Keep every alphabetic entity or metric token in the binding, whatever its length. Or fall back to the extractive sentence. |
| 3 | high | `apps/internal_brain/databridge_ib/deliverables/verify.py:445-465` (`check_chart`) | **Incomplete fix of B6.** Each point's metric is required only if that point's own evidence holds the series name. The series name is then kept by a check over the combined evidence. **[checked]**: line 447 sets `metric = ""` when the point's evidence lacks the name. | A "Revenue" series has two revenue points from C1 and two cost points from C2. C2 has no "Revenue", so its points are checked with no metric and pass. C1 keeps the name "Revenue", so C2's costs are released as revenue. | Decide the series name first, then require every point to bind to it. Otherwise drop the series or rename it to "Values". |
| 4 | high | `scripts/workbuddy_setup.py:210-214` (`_write_private`) | **Incomplete fix of B7.** `mkstemp` creates the file with the folder's inherited ACL, and `_owner_only` restricts it afterwards. A handle opened before the restriction keeps its access. **[checked]**: `mkstemp`, then `close`, then `_owner_only`, then reopen. | Another account with read access to the folder opens the empty temp file before `icacls` runs. It keeps the handle and reads the token after it is written. *Claude-seat note: this needs a second local account with read access to the founder's own workspace folder. On the single-user demo laptop the practical exposure is low.* | Create the file with a restrictive security descriptor from the start (`win32security`), or inside a folder already private to the account. |
| 5 | high | `scripts/workbuddy_setup.py:211-213` | **Incomplete fix of B10.** The exclusive create is undone: the descriptor is closed and the temp path is opened again by name. **[checked]**. | Someone who can replace files in the folder swaps the temp file after the ACL check and before `os.open`, so the token goes into their file. The later `_safe_target` checks the destination, not the temp file. *Same threat-model note as #4.* | Keep the original descriptor and write through it. Check the file's identity by handle (for example `fstat`/`lstat` inode or file ID) before `os.replace`. |
| 6 | medium | `apps/internal_brain/databridge_ib/extract_files.py:465-466` (`_XL_TAG`) | **Incomplete fix of B11.** The extent regex skips any valid start tag whose attributes run past 4,096 bytes. There is still no worker memory ceiling. **[checked]**: `([^>]{0,4096})>`. | A small `.xlsx` has A1 and XFD1048576 and declares the dimension as A1. The far `row` and `c` tags are padded with more than 4,096 spaces. The scan misses them, openpyxl parses them, and the dense grid is built. The 15 s kill is the only backstop. | Measure the extent with a bounded streaming XML parser (iterparse on the sheet) and fail closed on anything not handled. Add a memory ceiling to the worker. |
| 7 | high | `apps/internal_brain/databridge_ib/uploads.py:452-488` (`/upload-folders`) | **Regression (new folder picker, D-34).** `_folders` lists Drive folders and reads live permissions before the first ledger row. A missing ledger silently allows the release. **[checked]**: `_folders` runs at 474. `_ledger_folders` runs only afterwards, at 483, and returns quietly when `svc.ledger is None` (453-455). | With the ledger down, the picker still makes the Drive listing and permission calls before answering 503. That breaks rule 3: the accepted row must come before any source read. With `svc.ledger=None`, the folder list is released with no audit row. That is the same shape as B8, which was fixed in `sim.py`. | Append an accepted row before `_folders`, and answer 503 with no source call if that append fails. Treat a missing ledger as unavailable. Keep the release row before the response. |
| 8 | high | `apps/internal_brain/databridge_ib/live.py:240-249` (`changes`) | **Regression (B2 live half).** The write-ahead `checking` row is written only when Gate 1 returns candidates. During a ledger outage, a poll with a candidate answers 503, and a poll without one gets the normal empty reply. **[checked]**: `if not cands: return empty` (241-242) comes before `_ledger(…, outcome="checking")` (247). | A document that the stale index still lists as readable changed in the window, but it has been revoked at the source. During a ledger outage that poll is a 503, while an equivalent window without the document is the normal empty reply. Restricted and absent then look different (rule 4). *Claude-seat note: the asker cannot cause a ledger outage, and the signal is only about documents Gate 1 still lists as readable for that asker. The seat would rate this medium.* | Make the reply during an outage independent of the candidates: probe or append before the `cands` branch, but keep R4's "no row for a candidate-less poll" (for example, a ledger health check with no row). Alternatively, answer a 503 to every poll while the ledger is down. |
| 9 | medium | `apps/internal_brain_web/components/presenter/deliver.ts:171-183` (`openDeliverables`) | **Regression (Opus #1 follow-on).** D has no ownership check between its warm-up and its queued switch to Leo. **[checked]**: `owner` is read only after `api.switchTo(DELIVER_PERSONA)` inside `serial` (180-181). Nothing compares ownership from before the warm-up or before the switch. | D starts a fresh warm-up. While it is restoring the previous person, the presenter clicks Sign out in the shell. The logout is queued, and D's switch is queued after it, so Leo is signed back in and the panel opens after the sign-out. Clearing sessionStorage does not clear D's local `cache`. | Take `const o = sessionOwner()` when D starts. Check it again after the warm-up, inside the `serial` callback before `switchTo`, and before `set({open:true})`. Stop if it changed. |

## Fixes confirmed

These are Codex's results. File references are to the working tree.

- **A1:** confirmed. The `accepted` and `decided` rows come before the draft's source check and model call, and a replay appends before release (`actions.py:610`, `:824`).
- **A3:** confirmed. An explicitly empty create ACL no longer gets the Developers default (`mock_platforms/state.py:282`).
- **A4:** confirmed. Access-check exceptions are separated from ledger failures and give the unknown-token 404 (`app.py:545`).
- **A5:** confirmed. The same-answer hold/in-flight check and the claim happen under one lock (`actions.py:777`).
- **A6:** confirmed. `outcome_unknown` selects the unknown-outcome state, and the API client reads the body's `error` field (`ActSheet.tsx:28`, `lib/api.ts:73`).
- **A7:** confirmed. The capacity check and the insert are atomic, and release trims the person's other pending proposals (`actions.py:767`, `:809`).
- **A8:** confirmed. Expired unknown holds are swept on eviction and on hold checks (`actions.py:491`).
- **A9:** confirmed. The docstrings now match the code (`actions.py:54`, `mock_platforms/jira.py:14`).
- **R1:** confirmed. Only the first over-limit refusal per window is ledgered, `/ib/open` is rate-limited, and the token map is cached per index version (`actions.py:453`, `:469`).
- **B1:** confirmed. Control characters are stripped before the guard, and the chart workbook uses `write_string` (`deliverables/export.py:143`, `:150`).
- **B2:** confirmed for the original ordering defects. Export and candidate-bearing polls now append before the live checks (`deliverables/service.py:414`, `live.py:245`). Finding #8 is a new distinction this fix introduced.
- **B3:** confirmed. The title is re-checked against the surviving evidence (`deliverables/service.py:369`).
- **B4:** confirmed. Re-authorisation also covers the fallback after invalid model output, and the deterministic content is rebuilt from the surviving evidence (`deliverables/service.py:239`, `:375`).
- **B8:** confirmed. A missing ledger raises before the external write (`sim.py:194`).
- **B9:** confirmed. WRITE is checked again after extraction, and the mock checks it under the insert lock (`uploads.py:375`, `mock_platforms/uploads.py:115`).
- **B12:** confirmed. The rate limit comes before the body is read, and the body is capped while it streams (`deliverables/routes.py:35`, `:112`).
- **B13:** confirmed for the extraction download path. A declared size over the cap is refused, and the streamed body is checked as it arrives (`connectors/drive.py:318`).
- **B14:** confirmed. Title, Sources, table and chart slides get separate verification notes (`deliverables/export.py:104`, `:210`).
- **Opus #1:** confirmed for the original warm-up restore race. Shell changes claim ownership at once and run through the shared queue (`session.ts:37`, `deliver.ts:114`). Finding #9 is D's open path.
- **Opus #2:** confirmed. The preflight page needs loopback plus a session the IB confirms, and the deploy file sets `IB_DEPLOYED` (`app/present/preflight/page.tsx:60`, `deploy/lighthouse/docker-compose.yml:101`).
- **Opus #5:** confirmed. On a browser, the open link requires document navigation with user activation (`app/api/ib/[...path]/route.ts:222`).
- **Opus #6:** confirmed. Offline links use the request's origin, and the mock handler checks permission (`lib/mock/extra/act.ts:225`, `:345`).
- **Opus #13:** confirmed for the original later-step restore case. Closing D skips the restore once ownership has changed (`deliver.ts:192`).

## Not confirmed

- **A2, B5, B6, B7, B10, B11:** partial. See findings #1 to #6.
- **Opus #7:** partial, and this is accepted. The 12-minute cache limits staleness but does not re-check permission. A revocation right after warm-up still leaves D showing cached content until the cache expires (`deliver.ts:22`, `:171`). The disposition already says so, so this is not a new finding.

## Verdict (Codex)

"Do not freeze." Codex found high-severity defects that remain in grounding and token protection, plus new regressions in audit and the presenter session.

**Claude-seat reading, for the coordinator:**
- **The two hard-rule regressions are the priority, and both are small, local fixes:** #7 breaks rule 3 and #8 breaks rule 4, though only during an outage.
- **#1 to #3 tighten the grounding checks.** None of them lets a passage the asker cannot open reach the model or the asker. They let a permitted value be paired with the wrong name or metric.
- **#4 and #5 need a second local account** with access to the founder's own folder.
- **#6 and #9 are medium.**
