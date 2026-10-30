# Demo Day runbook: Walnut DataBridge (Tue 3 Nov, Marina Bay Sands)

One page for the person at the laptop. Everything is a double-click in the `Tencent` folder
(`E:\Tenecent Hack_Walnut\Tencent`). Run **Make Desktop Shortcuts** once and the four buttons are on the
Desktop too. Background reading: `LIVE_DEMO_RESEARCH.md`. Script: `../rubric/demo_story/RUN_SHEET.md`.

## 1. The five buttons

| Double-click | What it does | Time (measured 30 Oct) |
|---|---|---|
| **Start Walnut Demo** | Checks the laptop, starts everything, asks every scripted question once (pre-warm), checks the audit chain, opens a clean full-screen browser on the title card, prints **READY** | 2.5 min from cold; about 15 s when already running |
| **Stop Walnut Demo** | Closes the demo browser and stops every demo service | about 30 s |
| **Reset Walnut Demo** | Back to a known clean state: Simulator reset, everyone signed out (fresh browser profile), questions warmed again. Asks "also wipe and reseed the history?" Press **N** (or wait 15 s). **Y** is only for a broken history (about 3 to 5 min) | about 35 s |
| **Start Walnut Demo (offline)** | Rung 3: the recorded-fixture build on port 3101. No services, no network. Same keys, same screens | about 15 s; the first run after a code change builds for 2 min |
| **Make Desktop Shortcuts** | Puts the four buttons above on the Desktop. Once | 2 s |

Running any button twice is safe. Closing the black window does **not** stop the demo. Each run writes a log
to `databridge\var\demo-day\logs\`.

## 2. What green looks like

- The black window ends with big green **READY** letters, a line like `PREFLIGHT GREEN: 20/20 OK` and a
  "Model:" line. Red **NOT READY** lists what failed: press Reset once; if still red, go down the ladder (§5).
- The browser shows the title card: "Walnut DataBridge. Two people ask their company AI the same question."
  If the taskbar is visible, click the window once and press **F11**.
- The board at **http://127.0.0.1:3100/present/preflight** is the same check in big type: Services,
  Model and audit chain, Scripted questions, This screen. OK = green, FIX = red, CHECK = amber.
- **Amber is normal for the model** when TokenHub is not live: "Model: extractive fallback". Then say the
  default line, "quoted from the sources, with the same checks", never "Hy3 wrote it". Only when the
  Model line says **Tencent Hunyuan LIVE** may you use the [ONLY IF HY3 LIVE] lines.

## 3. Checklists

**Night before (2 Nov), once:** apply the Windows settings in §6. Double-click Start, check READY, then
double-click **Start Walnut Demo (offline)** once (it builds the fallback), check READY, then Stop.
Copy the clips folder (`databridge\apps\internal_brain_web\public\clips\`) to the USB stick and phone.

**T-60 min (in the hall):** charger in. Phone hotspot on, laptop joined to it (venue Wi-Fi forgotten).
Double-click **Start Walnut Demo**. Wait for READY (about 2.5 min). Open the preflight board, check
"Screen 1920 x 1080" and "Scale and zoom 100 %" once the projector cable is in. Run the whole script once
(P, then Space through every step; B once to see a clip). Then double-click **Reset Walnut Demo**, press N.

**T-15 min:** double-click **Start Walnut Demo** again (re-warms the questions; about 15 s). READY?
Win+N: Do not disturb on. Close every other app except WorkBuddy (warm, Jordan signed in). Phone silent.

**T-2 min (walking on):** the browser is on the title card, full screen. Do not touch the mouse. Do not
press P until Ishaan says the first line. If anything looks wrong now: press **R** (reset) in the browser.

## 4. Presenter keys (in the demo browser)

| Key | Does |
|---|---|
| **P** | Start: Stage theme, title card, signs the personas in |
| **Space** | Next step. A failed step does not advance: Space tries again, **Right arrow** skips it |
| **→** (Right arrow) | Skip a failed step (otherwise the same as Space) |
| **Left arrow** | Back one step (the journey step signs Maya in again) |
| **B** | Play the backup clip for this step (muted, labelled "recording"); B again returns to live |
| **R** | Reset (asks to confirm): signs everyone out, back to the title card. Never edits the real ledger |
| **L** | English / Simplified Chinese (not in the 5:00; cut 30 Oct) |
| **D** | Q&A only ("Dev, D."): Leo's answer and its table, chart and analysis from the `P` warm-up. **Esc** closes it and signs the previous person back in |
| **2** | Open citation [2] on the current Compare page |
| **S** / **H** / **?** | Stage theme on/off / hide or show the bar / show the key legend |
| **Esc** | Closes an open sheet or dialog. Once the script has started it never hides the bar (it only shows a hidden one) |
| **Ctrl+Shift+P** | Turn the presenter keys off (and on) |
| **F11** | Full screen on/off |

## 5. If something fails: the ladder (use the lowest rung that works)

Rule: if a step shows nothing 8 seconds after the key, Dev steps down. Ishaan says one clause, never an
apology. A recording is never presented as live.

| Rung | When | Do this | Ishaan says |
|---|---|---|---|
| 1. Live | Default | Nothing | Nothing |
| 2. Reset | A step errors or the screen is in a strange state | Press **R** in the browser, then P. Between pitches: double-click **Reset Walnut Demo** (35 s) | "Let me restart that step." |
| 3. Offline mock | The live system is down and there are 20 s | Double-click **Start Walnut Demo (offline)** (about 15 s), then P and Space as usual | "This is the recorded fixture mode, same screens." Fixture answers carry the label "hy3": never say Hy3 wrote them |
| 4. Backup clips | A single step stalls, or no time for rung 3 | Press **B** (under 3 s). For the whole run: the full video on laptop B | "Let me show you the recording from this morning; same system, same data." |
| 5. Slides | Both laptops fail | Stills and the deck | "Here is what you would have seen." |

Hotspot dies: nothing changes on screen except the model label (the stack is local). WorkBuddy is the
only other thing that needs the network: use clip C (B) for that beat.

## 6. Windows settings to apply the night before (both laptops)

| Setting | Where |
|---|---|
| Do not disturb automatically when duplicating or full screen | Settings > System > Notifications > "Turn on do not disturb automatically": tick "When duplicating your display" and "When using an app in full-screen mode". On the day: Win+N, bell icon |
| App notifications off | Settings > System > Notifications > switch off each app under "Notifications from apps and other senders" |
| Windows Update paused | Settings > Windows Update > Pause updates > pick a date after 3 Nov. Install pending updates *before* pausing, not on the day |
| Hotspot as metered | Settings > Network & internet > Wi-Fi > the hotspot > Metered connection: On |
| Never sleep, screen never off | Settings > System > Power & battery > Screen, sleep & hibernate timeouts: Never when plugged in. Power mode: Best performance. Lid: "Do nothing" when plugged in |
| Display scaling 100 %, 1920 x 1080 | Settings > System > Display > select the projector > Scale 100 %, Display resolution 1920 x 1080. Win+P > **Duplicate** |
| Time zone Singapore (UTC+8) | Settings > Time & language > Date & time (the board checks it) |

The browser forces 100 % page scale by itself, but Windows scaling and Duplicate still decide what the
projector gets: check "Screen" and "Scale and zoom" on the preflight board after plugging in.

## 7. For the technical person only

`databridge\scripts\demo_day.ps1` does the work (actions `start | stop | reset | offline | status | shortcuts`;
flags `-DryRun -NoBrowser -Windowed -NoWarm -Rebuild -Fresh -Browser edge|chrome`). The .cmd files accept
`--dry-run`, `--no-browser`, `--windowed`, `--rebuild`, `--fresh`. Pre-warm and checks:
`databridge\scripts\demo_preflight.py` (writes `var\demo-day\preflight.json`, which the board reads). The
pre-warm questions are real asks and appear in the ledger as ordinary `ui` asks made before the demo.
The demo browser profile is `databridge\var\demo-browser` and is wiped on every Start and Reset.
