# Merge plan: the teammate's three feature branches into the finals build

Written Fri 30 Oct 2026. Repo `IshaanSirbhaiya/Tencent`, local `E:\Tenecent Hack_Walnut\Tencent`.
Our branch is `final_v1.2`; `final_v1.3` is its verified pointer. Helper: `databridge/scripts/merge_precheck.py`.

**Status (30 Oct):** final_mehulupdates merged into final_v1.4 (blocked on onboarding.py; resolved upstream by e740215, merged as e47069a). *(update 30 Oct: the Playwright run on final_v1.4 has since refreshed the act and deliverables screens, `75a0aa8`, `2c5aae3`; no pass count for final_v1.4 is recorded.)* Details: `MERGE_ANALYSIS_final_mehulupdates.md`.

The three branches are not on origin yet. Each one merges on its own, into a new branch:

| Order | Feature | New branch | Cut from |
|---|---|---|---|
| 1 | Voice model | `final_v1.4` | `origin/final_v1.3`, or the newest verified `final_v1.x` at that moment |
| 2 | Just-in-time UI (a table or UI built from a request when it is asked) | `final_v1.5` | `origin/final_v1.4`, once v1.4 is verified and pushed |
| 3 | Dots (a lot of configuration up front; details unknown) | `final_v1.6` | `origin/final_v1.5`, once v1.5 is verified and pushed |

Then the video is recorded on the newest verified branch.

Why this order: least risk first, so each step leaves a usable fallback.
- Voice is small, sits at the edge (the composer), and has a fallback.
- JIT UI overlaps our heaviest area (Deliverables) and needs the eval.
- Dots is unknown and is meant to stay off. Swap the order if a branch is not ready; the rules stay the same.

Placeholders used below: `<voice>`, `<jit>` and `<dots>` are the teammate's branch names (suggested:
`team/voice`, `team/jit-ui`, `team/dots`). `$PY` is the main tree's venv:

```powershell
$PY = "E:\Tenecent Hack_Walnut\Tencent\databridge\.venv\Scripts\python.exe"
```

---

## 0. Before the first merge: land our own work

1. **Our work in progress is not in `final_v1.3`.** On 30 Oct (evening), `final_v1.2` had moved
   past `origin/final_v1.3` (`1076517`) by 15 commits. The working tree also held about 95 changed or
   untracked files: Act round 4, uploads, presenter, the video kit, and the new tests.
   - A branch cut from `origin/final_v1.3` does not contain any of that.
   - If we merge the teammate first, our own work will later conflict with all three features.
2. **So, first:**
   - finish and commit the WIP on `final_v1.2`;
   - run the §5 matrix on it;
   - push it;
   - move the verified pointer to it, by cutting `final_v1.3` again or by using the newest verified
     `final_v1.x`.

   The teammate's branches then merge onto a base that already holds our work.
3. **Record the baseline.** Run the §5 matrix once on the base and write the counts into
   `docs/finals/TEST_REPORT.md`. Rule 5 ("pre-existing failure is a claim to verify") needs that
   baseline. The last recorded numbers are at `831297e`; tests have been added since.
4. **Never use `git stash`, `git checkout -- <path>` or `git reset --hard`** (`CLAUDE.md`, working rule 3).
   - If the tree is dirty, commit it.
   - Never push to `walnut-upstream`. Its push URL is already `DISABLED_DO_NOT_PUSH`; check with
     `git remote get-url --push walnut-upstream`.

## 1. Where the merge happens: a separate worktree

The main tree (`E:\Tenecent Hack_Walnut\Tencent`) is usually dirty, and agents work in it.
- `git switch -c` in a dirty tree carries the uncommitted changes into the new branch.
- So every merge happens in its own git worktree, a second checkout of the same repo.
- Use `git switch -c` in the main tree only when `git status --short` prints nothing and no agent is running.

The worktree can use the main tree's venv (it has no editable installs; everything runs on
`PYTHONPATH`). It needs its own `npm ci`. The Playwright browsers are shared.
`databridge\.env.ib.local` is git-ignored, so it is **not** in the worktree. That is intended: every
automated check runs offline.

## 2. Pre-merge checks (per branch, before any merge command)

### 2.1 Fetch

The teammate pushes the branch to origin (`IshaanSirbhaiya/Tencent`). Then, from the main tree:

```powershell
cd "E:\Tenecent Hack_Walnut\Tencent"
git fetch origin --prune
git branch -r --list "origin/*"                 # the teammate's branch must be listed
git log --oneline -15 origin/<voice>
```

If the teammate works in a fork, fetch it without adding a permanent remote:
`git fetch https://github.com/<their-account>/Tencent.git <branch>:refs/remotes/teammate/<branch>`.

If they send a bundle file:
- `git bundle verify <file>.bundle`;
- then `git fetch <file>.bundle <branch>:refs/remotes/teammate/<branch>`.

### 2.2 Run the helper (read-only)

```powershell
cd "E:\Tenecent Hack_Walnut\Tencent\databridge"
& $PY scripts/merge_precheck.py origin/<voice> --base origin/final_v1.3
# for v1.5: --base origin/final_v1.4 ; for v1.6: --base origin/final_v1.5
# add a company name for this check: --add-name "Some Bank"   (or MERGE_PRECHECK_NAMES="A,B")
```

Run it from the main tree, so that §7 "our side" includes our uncommitted work. It only runs git
read commands, never fetches, and never opens a working-tree file.

Exit code:
- 0: nothing blocking;
- 1: blocking (a secret, a secret-shaped file, a real company name, or a `packages/**` touch);
- 2: a usage or git error.

What each section answers:

| § | Check | Pass rule |
|---|---|---|
| 1 | **Branch base**: merge-base, ahead/behind, "verified pointer inside the branch" | The branch contains `origin/final_v1.3` or later. If it says **NO**, the teammate branched from an older `final_v1.x`. Ask them to merge the current base into their branch (not a rebase of pushed work), or expect more conflicts. Also check `merge commits on the branch` |
| 2 | **Diff summary by area**: IB backend, deliverables, web, i18n, proxy route, mock, fixtures, eval, kit, docs | No surprise areas. "backend changed" or "world changed" means the eval must be re-run (§5.6) |
| 3 | **Secrets** on added lines (masked), committed `.env*`, `*.pem`, `*.key`, `*.tok`, removed `.gitignore` guards | 0 blocking. Placeholder hits ("dev", "not-real", env lookups) are read by eye. Then also run `git log -p origin/<voice> --not origin/final_v1.3 -- "*.env*"`: a secret added then deleted inside the branch is still in its history. If found, the branch must be rewritten **by the teammate**, and the key rotated |
| 4 | **Real company names** (rule 7, synthetic data only) | 0 hits in `fixtures/`, mock data, UI copy, i18n and tests. The default list is short and generic; extend it per merge. Sponsor and platform names (Tencent, Aspire, Slack, Jira, Confluence, Google Drive) are not on it on purpose |
| 5 | **`databridge/packages/**`** (the Walnut engine, reused by import, never modified) | 0 files. Any touch is a stop: the change belongs in `apps/internal_brain` or in a pack |
| 6 | **Per-domain branching** (rule 6), heuristic | 0 domain hits that are real. A platform test outside `connectors/` is fine in display or simulator code, never in retrieval or gate logic. Also run `packages/graph/tests/test_no_domain_branching.py` (§5.1) |
| 6b | **Feature hints**: keys exposed to the browser (`NEXT_PUBLIC_*KEY`), code execution (`eval`, `new Function`, `dangerouslySetInnerHTML`, Python `exec`), a second speech stack, direct external `fetch` from the browser, reads of the local key file, new env names, new dependencies | Each hit is explained by the §3 checklist for that feature. Every new env name defaults off. Every new dependency is pinned and installs on Windows |
| 7 | **Conflict hot spots**: files both sides changed (ours = `8fff33c..HEAD` plus uncommitted), with the named hot spots (§4) marked `HOT` | Read every `HOT` row before merging. Resolve them with the §4 rules |

### 2.3 By hand, with the teammate

- **Run their own tests.** Ask which tests they added and what they ran. Run those tests in the
  worktree after the merge (§5).
- **Line endings.** This machine uses `core.autocrlf=true`. A file that shows `+N -N` with equal N
  is usually a line-ending rewrite. Check with:
  `git diff --ignore-cr-at-eol --stat origin/final_v1.3...origin/<voice>`.
  Ask them to fix it rather than merging the churn.
- **Generated files** are regenerated, never hand-merged:
  - `evals/internal_brain/results.json`;
  - `RESULTS.md`;
  - `apps/internal_brain_web/public/eval_summary.json`;
  - `package-lock.json`;
  - handout PDFs.
- **Ask the "dots" owner for a one-page note before the merge:**
  - what it is;
  - every config key and its default;
  - what it reads;
  - what it shows, and to whom.

  Without the note, the §3.3 checklist cannot be answered.

### 2.4 Self-test of the helper (30 Oct)

Run on `final_v1.2` against `final_v1.1`:

```
& $PY scripts/merge_precheck.py final_v1.2 --base final_v1.1 --verified final_v1.1
```

Result: exit 0.
- **Base:** 45 commits to merge, 0 behind, `final_v1.1` inside.
- **Diff:** 318 files, +52,527 −10,685. Backend and world both changed, so the eval is flagged.
- **Secrets:** 0 blocking. 2 placeholders: a fake key in `test_round2_kit.py:64` and an env expansion
  in `demo_up.sh:116`.
- **Company names:** 0 hits. **`packages/**`:** 0 files.
- **Domain branching:** 1 domain hit, a false positive (an e-mail domain compare in `uploads.py:222`).
  12 platform tests outside `connectors/` (simulator and graph display).
- **New env names:** 9 (`IB_DELIVERABLES`, `IB_DEMO_FEATURES` and others), all default off.
- **New dependencies:** 4 (`opencv-python-headless`, `python-pptx`, `XlsxWriter`, `pypdfium2`).
- **Overlap:** 227 files, `actions.py` and `DeliverSheet.tsx` at the top.

The detectors were also checked on synthetic strings:
- an OpenAI-style key, a Tencent `AKID…` id, a `.env.ib.local` path and a `.tok` file were caught;
- `.env.example`, `dbs/` and "grab the row" were not.

## 3. Hard-rule checklists per feature type

The repo rules apply to every merge (`CLAUDE.md` rules 1–8). Rules 1–5 are the ones a new UI or
model feature most easily breaks:
1. filter before the model;
2. identity only from the signed token;
3. write-ahead audit;
4. one response shape (restricted ≡ does not exist);
5. no value without a pointer.

Tick each box with a test name, a `file:line`, or a command output (rule 8).

### 3.1 Voice (`final_v1.4`)

- [ ] **The key is server-side only.**
  - The voice provider key is read on the server: the IB service, or a Next server route. On the
    laptop it lives in `databridge/.env.ib.local` (git-ignored, loaded by the existing kit:
    `demo_up.sh`, `demo_day.ps1`).
  - It is never a `NEXT_PUBLIC_*` variable, never in a client component, and never in the browser
    bundle or the network tab.
  - Precheck 6b is clean.
  - A test asserts the key is absent from every response and from `.next*/static`. Search the build
    output for the variable *name*; never for the value.
- [ ] **No vendor SDK outside `packages/harness`.** `scripts/check_vendor_imports.py` passes. Call the
  provider over HTTP from app code, as `llm.py` does through the harness; `packages/**` stays untouched.
- [ ] **Network down means a quiet fallback.** With `IB_FORCE_OFFLINE=1`, no key, a 402 or 429, a
  timeout, or the network unplugged:
  - the mic falls back to the existing browser dictation (`useDictation.ts`, Web Speech API), or to
    typing;
  - no error wall, no spinner that never ends, and the stage demo is never blocked.
  - Proven by an e2e test in mock mode, plus a backend test with the provider unreachable.
- [ ] **It does not duplicate the existing dictation.**
  - One mic button in `components/user/Composer.tsx`.
  - Voice is a provider inside or behind `components/user/useDictation.ts` (for example
    `provider: 'browser' | 'server'`), not a second hook or a second button.
  - It keeps today's behaviour:
    - never auto-sends;
    - sending stops the mic (round 2b finding 4);
    - `aria-pressed` toggles once;
    - strings come from `lib/i18n/voice.ts` (en and zh).
  - `tests/e2e/voice.spec.ts` still passes unchanged.
- [ ] **Identity and access are unchanged.**
  - A transcript is only text in the composer. Saying "ask as Rachel" changes nothing; identity comes
    from the token (rule 2).
  - If the feature *speaks answers* (TTS), it reads only the released, grounded answer text: never
    sources, never hidden items, and nothing before `answer.released` (rules 3–5).
- [ ] **The proxy route.**
  - Any new path is added to `ALLOWED` in `app/api/ib/[...path]/route.ts`, main session only (not in
    `SLOT_ALLOWED`).
  - An audio upload gets its own path-specific body limit, like `UPLOAD_PATH`. The global
    `MAX_BODY` (64 KB) is never raised.
  - The Host check is kept.
- [ ] **Abuse limits.**
  - The route requires a signed session.
  - It has a per-person rate limit (429), plus a maximum audio length and size.
  - Audio is not stored, or `SECURITY.md` says exactly where it goes and for how long.

### 3.2 Just-in-time UI (`final_v1.5`)

This overlaps `databridge_ib/deliverables/` and `components/user/DeliverSheet.tsx`. Build on them;
do not run beside them.

- [ ] **Data only through the ask pipeline.**
  - Every value comes from an answer's cited, permitted documents.
  - It is re-authorised the way Deliverables does it: `ActionService.context` runs the sealed-answer
    check plus live Gate 1 and Gate 2 on every cited document (`deliverables/service.py`, order steps
    1–7).
  - There is no new retrieval path, and no direct index, mock-platform or file read.
  - Any error means deny (rule 1).
- [ ] **A fixed component set, chosen as JSON.**
  - The model returns JSON that picks a layout from a closed enum of components we ship: table, bar
    chart, KPI tile, list, and so on.
  - The JSON is validated by a strict schema (`extra="forbid"`, like `deliverables/spec.py`).
  - No code execution:
    - no generated JSX, HTML, CSS or JS;
    - no `eval`, `new Function` or `dangerouslySetInnerHTML`;
    - no model-supplied URLs, and no Markdown rendered as HTML.
  - Model strings render as React text nodes only. An unknown component, or an invalid JSON reply,
    falls back to the deterministic table (`build.source_table` / `build.quoted_table`).
- [ ] **Every cell is traced to a source.**
  - Every cell, number, label and title goes through `deliverables/verify.py`: `check_cell`,
    `check_chart`, `check_sentence`, `check_short_text`, with the number-span, scale and locale
    rejects and the label binding (Opus review items 1–5).
  - It carries a `SourcePointer` the UI can open. A cell that fails is dropped, or the build falls back.
  - Reuse `verify.py`; do not fork it. A change to `verify.py` re-runs `test_deliverables_verify.py`
    and `test_deliverables_eval.py`.
- [ ] **Rule-4 parity.**
  - A request about restricted content returns exactly what a request about a non-existent topic
    returns: the same shape, the same empty state and the same timing class.
  - No "N rows hidden", no partial counts, no denial reasons.
  - Add a P2-style differential test next to `test_deliverables_eval.py`. The full eval's 0/400 P2
    must hold (§5.6).
- [ ] **Audit and identity.**
  - Write-ahead ledger events, before the source reads, the model call and the response, the same
    pattern as `deliverable.requested` and `deliverable.created`. A ledger failure gives a 503.
  - No `user` argument anywhere; identity comes from the token.
- [ ] **Off switch and scope.**
  - A kill switch defaults off (like `IB_DELIVERABLES`), or the feature reuses `IB_DELIVERABLES`.
  - It runs in the main session only, never in a Compare slot (`SLOT_ALLOWED`).
  - A per-person rate limit.
- [ ] **Mock and i18n parity.**
  - A `lib/mock/extra/*` entry, so the Playwright mock e2e covers it.
  - The mock labels say "Quoted from sources (no model)" unless the model is live (round-2 Opus #3).
  - Strings in en and zh.

### 3.3 Dots (`final_v1.6`)

- [ ] **Off by default behind a flag.**
  - One flag, for example `IB_DOTS` (server, `== "1"` in `config.py`) and, if needed, a matching UI
    flag.
  - Off in `config.py`, `demo_up.sh`, `demo_day.ps1`, the Playwright configs and `Start Walnut Demo.cmd`.
  - Precheck 6b lists every new env name; each one must default off.
- [ ] **The app is identical without it.** With the flag unset:
  - its routes are not mounted (unknown-route 404, as `/sim/upload-file` is when off);
  - no nav item, button, tour step, presenter key or extra network call;
  - every §5 suite passes with the **same counts as the base**, plus only the new flag-on tests;
  - the 11-step presenter run is unchanged.
- [ ] **The configuration up front.**
  - Only synthetic sample config is committed, as `*.example` or under `fixtures/`.
  - No secrets, no real names (precheck 3 and 4).
  - A missing or broken config never stops start-up; the feature just stays off.
  - No step is added to the demo-day checklist unless the flag is on.
- [ ] **With the flag on, rules 1–5 still hold.**
  - Anything that shows document content, counts or graph nodes goes through Gate 1 and Gate 2 for
    the viewer.
  - Restricted ≡ non-existent.
  - The ledger is written ahead.

## 4. Conflict hot spots and how to resolve them

These are the files our side changed most since `8fff33c` (`git diff --stat 8fff33c..HEAD` plus the
uncommitted work, 30 Oct; the helper recomputes this every run), plus the files every feature touches.

| File | Our churn | Why it clashes | Rule when resolving |
|---|---|---|---|
| `databridge_ib/actions.py` | 668 lines | Act round 2–4. JIT UI builds on `ActionService.context` | Keep ours; port their calls onto the current API |
| `components/user/DeliverSheet.tsx` | 363 | Deliverables P0/P1. JIT UI is the same surface | Keep ours. JIT UI becomes a mode or a section of the sheet, not a second sheet |
| `deliverables/export.py`, `service.py`, `verify.py`, `routes.py`, `build.py` | 235 / 187 / 176 / 83 / 42 | Round-3/4 fixes | Keep ours; never fork `verify.py` |
| `presenter/controller.ts`, `PresenterBar.tsx`, `script.ts`, `deliver.ts` | 270 / 200 / 43 / 88 | The demo and the video depend on these | No new step on the Space path (D-26). A teammate feature is at most an optional key |
| `uploads.py`, `extract_files.py`, `sim/AddDocumentSheet.tsx` | 233 / 196 / 180 | Upload hardening | Keep ours |
| `scripts/demo_up.sh`, `demo_day.ps1`, `demo_preflight.py`, `workbuddy_setup.py`, `hy3_check.py` | 116 / 47 / 51 / 240 / 50 | The demo kit | New flags go into the env pass-through list (`demo_up.sh` ~line 126) **and** the IB export (~line 198), defaulting off. Mirror them in `demo_day.ps1` |
| `app/api/ib/[...path]/route.ts` (the allowlist) | 51 | Every feature adds a path | The union of both `ALLOWED` lists. New paths are main session only. Keep the Host check, `MAX_BODY` and the one upload exception |
| `databridge_ib/app.py` | 85 | Routes and routers | Keep both. Mount new routers only when their flag is on. `tests/test_app_routes.py` walks `app.iter_routes()` |
| `databridge_ib/config.py` | (structural) | New flags | Append; every flag `== "1"` (default off) |
| `databridge_ib/models.py` | 28 | Ledger event models | Append; never rename or reshape an existing event |
| `lib/types.ts`, `lib/api.ts` | 5 / 2 since `8fff33c` (+198 since `final_v1.1`) | Every feature adds types and calls | Append-only. Never change an existing field. All calls go through `/api/ib` |
| `components/user/AnswerCard.tsx` | 12 | The action row (Act, Deliver) | One row; a new button hides when its flag is off |
| `components/user/Composer.tsx`, `useDictation.ts` | 33 / 3 | Voice | §3.1: one mic, one hook |
| `lib/i18n/*.ts` (presenter, graph, deliver, user, act, voice, en, zh) | 20–62 each | Every feature adds strings | The union of keys; every key in en and zh |
| `components/shell/Providers.tsx`, `lib/personas.ts` | 25 / 38 | Global providers and personas | Keep ours; a new provider renders nothing when its flag is off |
| `requirements-lock.txt`, `package.json` / `package-lock.json` | small | New dependencies | Pin them. Regenerate `package-lock.json` with `npm install`; never hand-merge it |
| `evals/internal_brain/results.json`, `RESULTS.md`, `public/eval_summary.json` | generated | Both sides re-ran the eval | Take either side (`git restore --ours <path>`), then regenerate (§5.6) |

## 5. The merge, then the post-merge test matrix

### 5.0 Merge commands (voice shown; v1.5 and v1.6 are the same, one number up)

```powershell
cd "E:\Tenecent Hack_Walnut\Tencent"
git fetch origin --prune
git worktree add "E:\Tenecent Hack_Walnut\merge_v1.4" -b final_v1.4 origin/final_v1.3   # or the then-latest verified final_v1.x
cd "E:\Tenecent Hack_Walnut\merge_v1.4"
git merge --no-ff --no-commit origin/<voice>
git status --short                     # conflicts show as UU
git diff --cached --stat               # what the merge brings in
# resolve each conflict with the §4 rules; then:
git add <resolved files>
git commit -m "merge: <voice> (voice model) into final_v1.4"
```

- To back out of a merge before its commit: `git merge --abort`. It only touches this new worktree.
- To back out after the commit and before the push: leave the branch unpushed and start again in a
  new worktree from the base (for example `final_v1.4-retry`). Never `reset --hard`.
- In a clean main tree only, the equivalent is `git switch -c final_v1.4 origin/final_v1.3`, then the
  same merge.

Then set up the worktree for tests:

```powershell
cd "E:\Tenecent Hack_Walnut\merge_v1.4\databridge"
$PY = "E:\Tenecent Hack_Walnut\Tencent\databridge\.venv\Scripts\python.exe"
$env:IB_FORCE_OFFLINE = "1"
```

### 5.1 Engine and rule guards

```powershell
git diff --stat origin/final_v1.3 HEAD -- databridge/packages                             # must print nothing
& $PY -m pytest packages/graph/tests/test_no_domain_branching.py -q -p no:cacheprovider   # 32 passed on 30 Oct
& $PY scripts/check_vendor_imports.py                                                       # [ok]
```

### 5.2 IB backend

```powershell
$env:PYTHONPATH = "apps/internal_brain;packages/core;packages/harness;packages/adapter;packages/retrieval"
& $PY -m pytest apps/internal_brain/tests -q -p no:cacheprovider
```

Expect the base count plus the new tests, 0 failed (last recorded: 1,274 passed, 0 failed, 4 skipped, clean checkout of `final_v1.2-cloud`, 30 Oct evening).

### 5.3 Mock platforms

```powershell
$env:PYTHONPATH = "apps/mock_platforms"
& $PY -m pytest apps/mock_platforms/tests -q -p no:cacheprovider
```

### 5.4 Fixtures (the synthetic world)

```powershell
Remove-Item Env:PYTHONPATH
& $PY -m pytest fixtures/enterprise/tests -q -p no:cacheprovider
```

### 5.5 Web: types and the Playwright mock e2e

```powershell
cd apps/internal_brain_web
npm ci
npx tsc --noEmit
$env:CI = "1"; npx playwright test      # builds the mock (.next-mock) and serves it on 3101
Remove-Item Env:CI
```

`CI=1` makes Playwright start its own mock server and not reuse one already on port 3101. Without
it, the config's `reuseExistingServer` could test an **old** build from the main tree.

Last recorded: 185 passed, 0 failed, 42 skipped (clean checkout of `final_v1.2-cloud`, 30 Oct evening; skipped specs need the live stack, the engine proxy or Hy3). The new
features' specs must not be in the skipped group without a reason.

### 5.6 The eval: only when the backend or the world changed

Run it when precheck §2 says "backend changed" or "world changed". It takes about 10 min.

```powershell
cd "E:\Tenecent Hack_Walnut\merge_v1.4\databridge"
$env:IB_FORCE_OFFLINE = "1"
& $PY -m evals.internal_brain.run
& $PY scripts/eval_summary.py --e2e <playwright passed count from 5.5>
```

Pass:
- 0 leaks over all probes (7,374 on the current world);
- 0/400 P2 differential;
- 0 hard failures in the injection-relevant requests (1,152);
- recall 100%.

A new world changes the probe count; record the new number. Commit the regenerated `results.json`,
`RESULTS.md` and `eval_summary.json`.

### 5.7 Commit, push, then the 11 demo steps live

1. Commit the fixes in the worktree, then push: `git push -u origin final_v1.4`. Only to origin.
2. The live stack needs the main tree. It has the venv, `.env.ib.local` and the demo kit's paths.
   - When the main tree is clean (our WIP committed) and no agent is running:
     `cd "E:\Tenecent Hack_Walnut\Tencent"; git switch final_v1.4`.
   - Then `Stop Walnut Demo.cmd`, and `Start Walnut Demo.cmd --rebuild`.
   - Add `--fresh` only if the world changed and §5.6 passed.
3. In the presenter window, press `P`, then `Space` through the 11 steps (00–10, `script.ts`):

   | # | Step |
   |---|---|
   | 00 | Title |
   | 01 | Compare: Amy vs Rachel |
   | 02 | Ken: Velquarry vs Bayfront |
   | 03 | Open citation [2] |
   | 04 | Journey of Rachel's request, as Maya |
   | 05 | Audit: Verify |
   | 06 | Sandbox tamper, then Verify |
   | 07 | WorkBuddy (off-app) |
   | 08 | Proof |
   | 09 | Business |
   | 10 | Close |

   Check each step:
   - the HUD dots are green, or show the expected offline state;
   - press `B` once per step;
   - `R` resets.

   Also check `D` (Deliverables), Act from an answer card by mouse (it has no presenter key), and the
   new feature with its flag on and then off. The
   whole run must take under 100 s with no typing (PRESENTER_KEYS §5).
4. Run `& $PY scripts/demo_preflight.py` from `databridge/`: it must be all green.
5. Only then is `final_v1.4` "verified". Write its line in §7. Cut `final_v1.5` from
   `origin/final_v1.4`, and remove the old worktree with `git worktree remove "E:\Tenecent Hack_Walnut\merge_v1.4"`
   (it must be clean).

## 6. Docs to update after each merge

The docs are always kept live. Update them in the merge branch before pushing:

1. `docs/finals/CHANGELOG.md`: the merge, with the commit link and the feature in one line.
2. `docs/finals/FEATURE_CATALOGUE.md`: the new feature. Who can use it, where, how, its flag, the
   rules it keeps, and its tests.
3. `docs/finals/TEST_REPORT.md`, root `STATUS.md`, `INTERNAL_BRAIN.md`: the new test counts from
   §5. Use the one-number set; no stale counts.
4. `docs/finals/DECISIONS.md`: one entry per merge. What was merged, its flag default, and anything
   deferred.
5. `docs/tencent/SECURITY.md`: a section for each feature with a key, audio, model output or a new
   route. Covers where the key lives, the fallback, rule-4 parity, and the ledger events.
6. `docs/finals/contracts/backend_v1.md`: new routes, bodies and ledger events.
7. `docs/finals/rubric/README.md` and `rubric/ai_innovation/AI_USAGE_MAP.md`: voice and JIT UI are AI
   features, so map each claim to a test.
8. `docs/finals/rubric/demo_story/RUN_SHEET.md`, `PRESENTER_KEYS.md`: only if a key or a step changed.
9. `docs/finals/video/VIDEO_SCRIPT_FINALS.md`: fill Part 4, "The teammates' features" (its
   placeholders).
10. `docs/finals/README.md`: the branch line (`final_v1.x`). Also `FOUNDER_TODO.md` and `CLOUD_HANDOFF.md`
    if they name a branch.
11. This file, §7: the merge log.

## 7. Rollback and merge log

- **The fallback.**
  - The previous `final_v1.x` stays on origin, untouched; nothing is ever force-pushed or deleted.
  - If `final_v1.4` fails live, the demo and the video run on `final_v1.3`; if `final_v1.5` fails,
    they run on `final_v1.4`; and so on.
  - To fall back on the laptop, with a clean main tree: `git switch final_v1.3`, then
    `Stop Walnut Demo.cmd`, then `Start Walnut Demo.cmd --rebuild`.
- **First-line rollback without a branch change:** turn the feature off.
  - Dots: its flag unset.
  - JIT UI: its kill switch, or `IB_DELIVERABLES` unset.
  - Voice: it falls back to browser dictation.

  §3 requires that each of these leaves the app as it was.
- **Undoing a pushed merge:** never rewrite the branch. Either cut the next branch from the last good
  pointer, or `git revert -m 1 <merge-sha>` on a new branch, then re-run §5.
- **The video** is recorded only on a branch whose §5 is complete and whose line below says verified.

| Branch | Feature | Base | Merge commit | Precheck | §5 suites | Eval | Live 11 steps | Verified |
|---|---|---|---|---|---|---|---|---|
| `final_v1.4` | Voice | | | | | | | |
| `final_v1.5` | JIT UI | | | | | | | |
| `final_v1.6` | Dots | | | | | | | |


## Reminder from the founder (30 Oct): extend the User-mode tour after the JIT UI merge

When the teammate's **just-in-time UI** branch is merged, extend the **User-mode interactive tour**
(`components/tour/steps.ts`, User section) with steps that show it:
- ask for a custom view, see the generated table or UI, and open a cell's source.

Keep the User and Developer tours separate (each starts on first entry to its own mode). Add the steps in
EN and 简体中文, extend `tests/e2e/tour.spec.ts`, keep axe clean, and add a matching beat in the video script's
S2 slot.
