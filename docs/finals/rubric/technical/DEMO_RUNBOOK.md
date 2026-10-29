# Demo runbook: start everything, check it, and what to do when something fails

> **Status.** 30 Oct 2026, for the rehearsal (Mon 2 Nov 16:00) and Demo Day (Tue 3 Nov). The demo runs on
> the laptop; the phone hotspot is only for TokenHub (`docs/finals/DECISIONS.md` D-03). The web UI is being
> rebuilt this week: re-check every step in this file against the frozen build on Mon 2 Nov 12:00
> (`docs/finals/PLAN.md` §1).
>
> All commands run from the repo root unless shown. On Windows use **Git Bash** for `.sh` scripts.
> `PY` = `.venv\Scripts\python` (Windows) or `.venv/bin/python`, run from `databridge/`.

## 1. One-time setup (the day before)

| Step | Command | Check |
|---|---|---|
| Python env | `cd databridge && py -3.13 -m venv .venv && .venv/Scripts/python -m pip install -r requirements-lock.txt` | `PY --version` is 3.13 |
| Web deps | `cd databridge/apps/internal_brain_web && npm install` | no errors |
| Tests green | `cd databridge && PY -m pytest apps/internal_brain/tests -q` | 733 passed on 30 Oct at commit 8582f4b |
| Optional: Hy3 | Create `databridge/.env.ib.local` (git-ignored) with `TOKENHUB_API_KEY=<key>` and `IB_FORCE_OFFLINE=0` | See §4, failure 1 |
| Optional: WorkBuddy | `cd databridge && PY scripts/workbuddy_setup.py --user-config`; restart WorkBuddy; press **Trust** on each connector | Three connectors appear (`docs/tencent/workbuddy/README.md`) |

Source: `INTERNAL_BRAIN.md:78-175`; `CLAUDE.md:75-89`.

## 2. Start the services (on the day, about 10 minutes before)

| # | Service | Port | Command | Ready when |
|---|---|---|---|---|
| 1 | Mock platforms + Internal Brain | 8200 + 8100 | Terminal 1: `cd databridge && bash scripts/run_local_demo.sh` | It prints `Uvicorn running on http://127.0.0.1:8100` (indexing takes about a minute, `INTERNAL_BRAIN.md:112-117`) |
| 2 | Web app | 3100 | Terminal 2: `cd databridge/apps/internal_brain_web && npm run dev` | http://localhost:3100 shows the sign-in page |
| 3 | Original Walnut engine (optional) | 8300 + 3300 | Terminal 3: `cd databridge && bash scripts/run_walnut_original.sh` | http://localhost:3300 loads; first run builds for a few minutes (`INTERNAL_BRAIN.md:158-161`) |
| 4 | WorkBuddy (optional) | stdio | Open WorkBuddy; the connectors start on demand | `whoami` answers in a WorkBuddy chat |

`run_local_demo.sh` sets `IB_DEMO_LOGIN=1` and defaults `IB_FORCE_OFFLINE=1` (deterministic quoted
answers) unless `.env.ib.local` overrides it (`databridge/scripts/run_local_demo.sh:9-21`). Its state lives
in `databridge/var/ib-demo/` (`databridge/scripts/run_local_demo.sh:13`). Ctrl+C in terminal 1 stops the
Brain and the mock platforms (`databridge/scripts/run_local_demo.sh:26`).

## 3. Pre-flight checks (5 minutes before)

| Check | Command | Expect |
|---|---|---|
| Brain up | `curl -s http://127.0.0.1:8100/health` | `{"ok":true}` (`databridge/apps/internal_brain/databridge_ib/app.py:87-89`) |
| Mocks up | `curl -s http://127.0.0.1:8200/health` | 200 |
| Warm the path | Sign in as `jdoe`, ask "What backfill throttle are we using for the ledger copy job?" | Cited answer from #db-migration |
| Reset after warm-up | `admin.it` → **Simulator** → **Reset demo** | jdoe is back in #db-migration |
| Chain intact | `maya.compliance` → **Audit** → **Verify audit chain** | "Chain intact" |
| Model label | Look at the answer's model line | `extractive-v1` (offline) or `hy3` (live). Say which, out loud. |
| Laptop | Close heavy apps; power plugged in; notifications off | Gate 2 has a 2 s budget; a loaded CPU can cause refusals |

## 4. Failures and fallbacks

| # | What fails | What you see | Do this | Say this |
|---|---|---|---|---|
| 1 | **TokenHub / Hy3** (no key, 402, Wi-Fi) | Answers labelled `deepseek-v4.1-flash` or `extractive-v1` | Nothing: the chain falls back automatically (`databridge/apps/internal_brain/databridge_ib/llm.py:88-104`). To stop all model calls: set `IB_FORCE_OFFLINE=1` in `.env.ib.local` and restart terminal 1 | "Hy3 is wired in; right now the answer is quoted straight from the sources, which is the safer path." Never imply Hy3 ran if it did not |
| 2 | **Brain not ready** | Answers fail; web shows a fixed error | Wait for the ready line in terminal 1 | "It indexes the whole company on start, about a minute." |
| 3 | **Port in use** | Start-up error | Stop whatever holds 3100, 8100 or 8200 (on Windows, run `netstat -ano` and look for `:8100`), then restart | — |
| 4 | **Unexpected refusal** for a question that worked in rehearsal | The constant refusal | Likely a Gate 2 timeout under CPU load (fail closed). Ask again once. Then check the Simulator was reset | "When it can't confirm your access in two seconds, the answer is no. That's the design." |
| 5 | **Ledger cannot be written** (disk, permissions) | 503 "temporarily unavailable" | Restart terminal 1 with a fresh state dir: `bash scripts/run_local_demo.sh var/ib-demo2` | Use it as the fail-closed moment: "No log, no answer." (`databridge/apps/internal_brain/tests/test_app_routes.py::test_ledger_failure_is_503`) |
| 6 | **Web app crashes** | Browser error | Ctrl+C terminal 2, `npm run dev` again. If dev mode is flaky, `npm run build && npm run start` | — |
| 7 | **Backend dead, no time to fix** | — | Web app in built-in mock mode: `npm run dev:mock` (fixtures, no Brain needed; it is what the Playwright tests use, `databridge/apps/internal_brain_web/playwright.config.ts:5, 21-22`). Check it still works in the new UI on Mon 2 Nov | "This is the recorded fixture mode." Say it |
| 8 | **Everything fails** | — | Play the backup video: `submission/05_video/walnut_internal_brain_demo.mp4` (re-record if the new UI is ready, `docs/finals/DECISIONS.md` D-01) | "Here's the same flow recorded on this laptop." |
| 9 | **Tamper moment fails to run** | — | Show `databridge/apps/internal_brain/tests/test_audit_tamper.py` passing, or the recorded output of `PY scripts/tamper_demo.py` | — |
| 10 | **Projector too dark / small** | — | Use presenter mode (press `P`) and large text; browser zoom 125% also works | — |

## 5. Fixed persona order (rehearse it this way)

1. `jdoe` asks the database-migration question (cross-platform, cited).
2. Compare: `jdoe` and `ken.ext` side by side; then `ken.ext` asks about the Q3 breach report and the made-up Q7 one (same refusal).
3. `admin.it` → Simulator → remove jdoe from #db-migration → `jdoe` asks again → refusal → **Reset demo**.
4. `maya.compliance` → Audit → Verify audit chain → tamper demo.
5. WorkBuddy: the same question from the WorkBuddy connector.

Source: `INTERNAL_BRAIN.md:35-49`, `INTERNAL_BRAIN.md:215-223`; `docs/finals/PLAN.md` §7.

## 6. After the demo

Stop terminals with Ctrl+C. `bash scripts/run_walnut_original.sh stop` stops only ports 8300 and 3300
(`databridge/scripts/run_walnut_original.sh:6`). The demo ledger in `var/ib-demo/` is synthetic and
git-ignored; keep it until the results are announced in case a judge asks to see it.
