# Handouts check, 30 Oct 2026 (final_v1.3)

The three printed handouts in `submission/finals/` were rebuilt from `handouts_src/build_handouts.mjs` and checked against the facts for branch `final_v1.3`.

## What changed in the source

| Item | Before | After |
|---|---|---|
| Repo link and QR target (`REPO_URL`) | `tree/final_v1.0`, "branch final_v1.0" | `tree/final_v1.3`, "branch final_v1.3" (the QR is generated from `REPO_URL`) |
| Backend test count | `eval_summary.json` `tests.backend` = 1,278 (tests collected) | 1,274 passed, 0 failed, read from the STATUS.md backend row (4 skipped; 1,278 collected) |
| Browser test count | 185 (from STATUS.md) | unchanged, now worded "185 passed, 0 failed (axe included)" |
| CI | not mentioned | "CI on GitHub is green" (leave-behind) and "CI green" (judge card 4) |
| CodeBuddy | "built and reviewed the code" | "in nine sessions (CB1–CB9)" |
| WorkBuddy | MCP connector | adds "live test 33/33" |
| Miora / Lighthouse | "Miora made the cover art" | "Tencent Miora: the cover was designed in it"; "Lighthouse deploy kit in the repo; no live deployment" |
| Judge card 6 | "driven from the keyboard" | "from the keyboard or the presenter bar's buttons" |
| Judge card 8 | "Keyboard-only demo" | "The demo runs from the keyboard alone, or with the mouse" |
| Presenter cheat sheet | keys only | notes that every key has a mouse control, plus one line mapping the bar controls and side buttons (PRESENTER_KEYS.md §2a) |
| Build | needed this checkout's `node_modules` | optional `HANDOUT_DEPS_DIR` points at another checkout's web app `node_modules` |

No "Six CodeBuddy sessions", 1,131 or 166 appeared in the handout sources; those older numbers were not present.

## Rebuild

`HANDOUT_DEPS_DIR=<other checkout>/databridge/apps/internal_brain_web CHROMIUM_PATH=/opt/pw-browsers/chromium node build_handouts.mjs`, 30 Oct 2026. Numbers it used: leaks 0, requests 7,374, pairs 400, differences 0, backend 1,274, browser 185, ≈ US$1.3M, break-even ~7 min.

| PDF | Pages | Size |
|---|---|---|
| `leave_behind_A4.pdf` | 1 | 214,560 bytes |
| `judge_cards.pdf` | 3 | 158,000 bytes |
| `presenter_cheatsheet.pdf` | 1 | 143,158 bytes |

## Text check (pdftotext)

- Not found in any PDF: `1,131`, `1131`, `166`, `Six CodeBuddy`, `final_v1.0`, `1,278`, `Keyboard-only`, `Claude`, `Anthropic`.
- Every number matches the facts: 0 leaks in 7,374 adversarial requests; 0 differences in 400 paired questions; 1,274 backend and 185 browser tests passed, 0 failed; CI green; nine CodeBuddy sessions (CB1–CB9); WorkBuddy 33/33; Hy3 via TokenHub wired, live run pending, answers quoted from the sources; mock platforms with real API shapes; synthetic data; Lighthouse kit, no live deployment. ROI figures are labelled estimates.
- No stand-in model is mentioned; Hy3 is never said to have answered.

## Open points

- The QR code was regenerated from the new URL but not scanned with a phone.
- `R` (Reset): the cheat sheet follows the code comment (checked against `controller.ts`, round 2b): confirm, sign every demo session out, step 00, restores nothing. PRESENTER_KEYS.md §2 still says R "restores the tamper sandbox copy ... re-warms the model". One of the two should be brought in line; the handout was not changed on this point.
- STATUS.md lists the latest CI run as 40990413962; run 41052653533 on final_v1.3 is newer. The handouts say only "green", with no run number.
