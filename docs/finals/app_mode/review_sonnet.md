# Review of the app-mode PLAN: UX and accessibility seat (Sonnet)

Reviewed 30 Oct 2026, read-only. Files read: `PLAN.md`, `components/presenter/PresenterBar.tsx`,
`ClipOverlay.tsx`, `WorkBuddyCard.tsx`, `components/ui/dialog.tsx`, `ui/announce.tsx`,
`shell/SkipLink.tsx`, z-index and `fixed` usage across `components/shell`, `presenter`, `tour`.
Not read in full: `ShellControls.tsx`, `lib/i18n/*` (only listed). No code was run.

## Verdict

**Approve with changes.** The architecture is sound (real URLs, `gd` marker, same `STEPS` actions,
no edits to `components/presenter/*`). Four items must change before build: F1 (the title cannot fit
in the pill), F2 (focus moves to `<main>` after every click), F3 (slides have no `<main>` to reserve
space), F4 (recordings need a text alternative). The rest are small.

## Findings

Severity: **High** = blocks a user or fails WCAG A/AA; **Med** = real friction; **Low** = polish.

| # | Sev | Finding | WCAG | Recommendation |
|---|---|---|---|---|
| F1 | High | **The pill cannot show the step title at 22 rem.** 22 rem is 352 px; minus padding about 320 px. "Step 3 of 11" (zh "第 3 步，共 11 步" is longer) plus Back, Next, More and Collapse at 36 px (about 150 px with gaps) leaves roughly 80 px for a title that is "truncated with a tooltip". The title is the part that tells a judge what chapter this is. A tooltip is hover-only (not on touch or keyboard). | 1.4.13, 1.4.10 | Two rows, about 88 px tall, not 56. Row 1: "Step 3 of 11", Collapse. Row 2: title, wrapping to 2 lines (no tooltip). Row 3: Back, Next (labelled "Next: <title>"), More. Fix the plan's "about 56 px" figure and the `--guided-h` assumption. The 390 bar can stay one row with the title only when expanded. |
| F2 | High | **"After Next/Back, focus moves to `<main>"` hurts the people who most need the pill.** The pill is mounted in `app/layout.tsx`, so it survives navigation and keeps focus by itself. Moving focus to `<main>` puts a keyboard or switch user at the top of a long page (Compare, Audit) and the pill is last in tab order, so reaching Next again costs many Tabs. A mouse click gives no reason to move focus at all. | 2.4.3, 3.2.2 (spirit) | Leave focus on the button that was activated. Use `aria-disabled` (not `disabled`) while busy, exactly as `PresenterBar.tsx` does (`Ctl`), so focus is never dropped. Announce the new chapter through the shared `announce()` (`ui/announce.tsx`), not a second live region (the plan's own region would double-speak). Add a skip link "Skip to Guided demo controls" right after `SkipLink` (visible on focus), because the pill follows `<main>` in the DOM. |
| F3 | High | **Slides have no shell, so the reserved padding does not exist there.** The plan pads `<main>`; `app/present/layout.tsx` has no `main#main` or AppShell (plan §2 item 8). Chapters 1, 9, 10, 11 are slides, so the pill will sit over slide content (footer, the "synthetic data" label, bottom of the headline). Test 13 only checks the headline. | 2.4.11, 1.4.10 | On `/present/*`, default the pill to the 40 px chip and let the user expand it; or give the slide layout a bottom inset equal to `--guided-h`. Extend test 13 to the slide footer and to the Text-size 200 % case. Also set `padding-bottom: env(safe-area-inset-bottom)` on the 390 bar (iOS home indicator). |
| F4 | High | **The recording is video-only content with no text alternative.** `ClipOverlay.tsx` says "no captions track is needed". True for captions, but a pre-recorded video-only clip still needs a text description or audio alternative. The new modal is also opened by judges, not only the presenter, so this is no longer a back-stage tool. Autoplay for more than 5 s needs a pause control. | 1.2.1 (A), 2.2.2 (A) | In the modal show one visible line "What this recording shows" (a sentence per clip, content-free per §8; same strings in zh). Keep `controls`. Do not autoplay under `prefers-reduced-motion` or `data-motion="reduce"`; otherwise autoplay muted is fine. Keep the "Recording · not live" badge inside the dialog body, not only in the title. |
| F5 | Med | **Focus return from the recordings modal breaks if it is opened from the More menu.** Radix returns focus to the trigger. A menu item unmounts when the menu closes, so focus falls to `<body>`. The plan test 7 ("focus returns to the opener") would pass only for the card button on `/demo`. | 2.4.3 | Open the dialog from a stable element: on desktop show **Watch recording** as its own pill button when the step has a clip (it is the failure fallback, so it must not be two taps away on stage). For the 390 menu case use `onCloseAutoFocus` to focus the More trigger. |
| F6 | Med | **Browser Back vs pill Back ping-pong.** Pill Back is a `push` of chapter n-1 (§4). After Next, Next, Back (pill), the browser Back button goes to chapter 3, not chapter 1. Users will read both "Back"s as the same thing. | 3.2.3 | When the previous history entry is chapter n-1 (`gd` check), pill Back calls `history.back()` (same page, cache restores answers). Otherwise `push`. Pill Next stays `push`. Then the two Backs agree in the common case. |
| F7 | Med | **Persona re-check on browser Back can show the wrong person's answers for a moment.** The page restores Rachel's cached answers while the session is still Maya (or the reverse) until `switchTo` finishes. Judges may see a restricted answer under a wrong identity label. | 4.1.3 | Show "Switching to Rachel…" in the pill and announce it. Render the page normally; if `switchTo` fails (demo login off, 401), show the failed state with Try again, never a silent blank. Test 4 should assert the final `whoami` and the pill text. |
| F8 | Med | **`gd` in the URL with no guided state is undefined.** A shared link, a new tab, or browser Back after End demo has `gd=n` but empty `sessionStorage`. §4 only covers "no gd". After End demo, browser Back would land on a chapter page with no pill (or a chip that revives the demo). | 3.2.x | Rule: `gd` plus passing gate (§7) activates the guided state (a shared link then works and the pill appears). End demo sets a "dismissed" flag for this tab; a later `gd` page shows no pill until the user opens a chapter again. Add a test for Back after End demo. |
| F9 | Med | **Disabled-while-busy vs "Next twice supersedes cleanly" contradict each other (§3.3 vs §13).** A stage mouse user double-clicks. | 3.2.2 | Pick one. Recommended: `aria-disabled` while busy, clicks ignored (same rule as the presenter bar), plus a 12 s timeout to the failed state so a stuck setup always offers Skip, Try again and Watch recording. Keep the generation counter for Overview and End demo. |
| F10 | Med | **Last chapter and first chapter.** Plan does not say what Next does on chapter 11 or Back on chapter 1. | 3.2.4 | Chapter 1: Back disabled (`aria-disabled`). Chapter 11: Next becomes "Finish", going to the Overview with "Demo complete" announced. |
| F11 | Med | **Judge alone: no obvious start.** Overview has 11 equal "Open" buttons; the plan has no single primary action. Entry via the user menu (between Settings and Sign out) is hard to find for someone who did not launch the app-mode `.cmd`. | 3.2.6 (consistent help), usability | Add one primary **Start at step 1** button above the cards; cards stay for jumping. Add the command palette entry (Q7: yes, it is cheap). Add a one-line link at the end of the existing Tour ("See the demo story"). Do not add a Settings card. |
| F12 | Med | **Target size on stage.** 36 px is fine for AA (2.5.8 needs 24) but Next is the one button a presenter hits 10 times, at the far corner on a 1920 projector. | 2.5.8 (met), 2.5.5 (AAA, advisory) | Make Next 44 px high and visually primary (filled), others 36 px. Keep at least 8 px between buttons. |
| F13 | Low | **Corner swap and collapse.** SC 2.5.7 (dragging) applies only if there is dragging. With no drag the criterion is not triggered. Reserved padding already protects content. | 2.5.7, 2.4.11 | Q6: no free drag. Keep Collapse (needed for slides, F3). Corner swap is optional; ship it only if time allows. It is the first thing to cut. |
| F14 | Low | **Stacking order is fragile.** The plan says pill `z-40` "under dialogs at z-50". But the dialog scrim (`dialog.tsx` Scrim) is also `z-40`, and the presenter bar, show handle and WorkBuddyCard are `z-40`. The Tour uses `z-[70]`/`z-[80]`, ClipOverlay `z-[60]`. The pill currently wins or loses by DOM order only. | 2.4.11 | Use `z-[45]` for the pill: above page and scrim-free content, below dialogs (z-50) and Tour. Under a modal the page is inert anyway, so the pill being dimmed is correct. |
| F15 | Low | **Landmark.** `<nav aria-label="Guided demo">` is read as site navigation. The presenter bar uses `<section aria-label>`. | 1.3.1, 4.1.2 | Use `<section aria-label="Guided demo">` (or `role="region"`); buttons inside `role="group"`. Keep icon-only Back/Next at 390 with `aria-label` that includes the target title. |
| F16 | Low | **zh and text size.** "第 3 步，共 11 步" and "更多" are longer or shorter than English; the plan tests axe in zh but not a layout overflow in zh at 200 % zoom. | 1.4.4, 1.4.10 | Add zh at 200 % zoom and 390 px to test 13. Spinner and slide-in use `motion-safe:` as `PresenterBar` does; also honour `data-motion="reduce"` for collapse/expand transitions. |
| F17 | Low | **"End demo" wording and effect.** For a judge "End demo" could mean sign out or quit; for Dev on stage it sounds final. | 3.3.2 | Rename "Exit guided demo" (matches the presenter's "Exit"). Move focus to the page heading and announce "Guided demo closed. You are still signed in as <persona>." (see Q11). No confirm dialog needed, it is non-destructive. |

## Specific questions asked

**Placement at 1280, 1920, 390.**
- 1280: pill at the right edge, x about 912 to 1264. Tour card sits bottom-left (`sm:right-auto`, 22 rem) so they do not meet. OK once F1 and F3 are fixed.
- 1920: the pill is small in a big window. Fine for a laptop-facing presenter; on a projector it is a distracting corner object. Collapse covers this; do not add a Stage toggle (Q9).
- 390: full-width bar at the bottom. Add safe-area padding. The Tour card is at `top-16` on small screens, so no clash. The bar plus reserved `<main>` padding must also clear Compare's Ask button.

**Focus order.** Page, then pill (DOM order). With F2 it works for a keyboard user only if the skip link is added. Tab order inside the pill: Back, Next, Watch recording, More, Collapse.

**Reduced motion.** Plan is right. Confirm `reveal` uses `behavior: 'auto'` and that the video does not autoplay (F4).

**Overlap with the new presenter mouse buttons.** `PresenterBar.tsx` already has Back, Next, Skip, Clip, Reset, Hide, Exit as icon buttons (the mouse controls the other agent added). Geometry: the bar is centred, up to 60 rem wide (960 px), so at 1280 it spans about x=160 to 1120 and would overlap a right-corner pill; the `PresenterShowHandle` is also bottom-right `z-40`. The plan's mitigation (pill hides while `usePresenter().armed`) is the right one. Check three edges: (1) armed set by `?present=1` after the pill exists, (2) leaving the presenter via its Exit button should bring the pill back as the chip, not the full pill, (3) no guided runner may start while armed. Add one e2e test: arm the presenter in a guided tab, assert the pill is not in the DOM, exit, assert the chip.

**Naming.** "Guided demo" next to "Tour" is confusing: both mean "something guides me". The plan itself notes the code calls Tour a "guided tour". Recommend **"Demo story"** (it is the RUN_SHEET word, and says what it is: the pitch story, not a UI tour). zh: 演示流程 (not 引导演示, which sounds like 导览). Pill label "Demo story, step 3 of 11". If the founder keeps "Guided demo", at least rename the Tour button to "UI tour".

**Back / Forward feel.** It should behave like a website: browser Back goes to the previous page and nothing re-asks (cache). Pill Back should agree with it (F6). Setup runs only on Open / Next / deep link, never on history navigation (plan is right). Add an announcement of the chapter on history changes (F7).

**Recordings modal.** Right component (Radix Dialog, X at 32 px, Esc, scrim click). Needs F4 and F5. Size `lg` (`max-w-3xl`) is enough. Keep it non-fullscreen so the persona label stays visible.

**End demo.** Fine as planned (see F8, F17).

## Answers to the plan's Review questions

1. **Stage role.** Booth and fallback tool only. The presenter bar now has full mouse controls, so the stage need for a mouse is already met by the keyboard presenter. A second stage path splits rehearsal time and a Dev under pressure will not know which to trust. Reword D-35 to "primary self-serve and booth path". Both must exist.
2. **Name.** "Demo story" (zh 演示流程). See above.
3. **Adapter or export.** Adapter. Keep presenter files untouched until after 3 Nov.
4. **Kit.** Not my seat. UX note: if Reset remembers the mode, print the mode name in the Reset message so a teammate is not surprised. One app-mode button is enough for judges; offline app mode is an edge case.
5. **Recordings and rule 4.** Do not hide. The files are static, synthetic and public at `/clips/*`, and hiding makes the button appear and disappear by persona, which is confusing. Keep the "Recording · not live" badge, and name the persona the clip shows ("Shown: Rachel, Compliance").
6. **Dragging.** Neither is required. Collapse yes, corner swap optional, no free drag (F13).
7. **Entry points.** User menu plus command palette plus a link from the end of the Tour. No Settings card (F11).
8. **Numbering.** "Step 3 of 11" in the pill; show the HUD number only on the overview cards in small text. Danger: the presenter bar shows `02 / 10` for the same step, so a judge who sees both will find the numbers disagree. Ensure they are never on screen together (pill hides when armed) and say "HUD 02" explicitly wherever it appears.
9. **Stage theme.** No toggle in the pill. Check contrast in the existing Stage theme through axe, but keep the control surface small.
10. **Gating.** The proxy is acceptable for build day. Gap: `demo_features` can be on while demo login is off, so Open fails after the page loads. Handle it with the failed state (F7) now; add an explicit `demo_login` field to `/ib/status` later.
11. **End demo.** Keep the session (no surprise sign-out in front of a judge), but say who is signed in when it ends. On a shared booth laptop consider restoring the persona that was signed in when the demo started; this is a nice-to-have, not a blocker.
12. **Cold chapter 5.** Accept the 3 s Compare run with the plan's status text ("Setting up: asking the question first"), announced politely, with Overview and Exit still enabled. Opening the journey list instead would break the story for the one chapter that is the product's proof.

## Test additions (on top of the plan's 13)

- Focus stays on the Next button after click (F2); skip link reaches the pill.
- Pill hidden while armed, chip after presenter exit.
- Back after End demo shows no pill (F8).
- Pill Back then browser Back agrees (F6).
- Chapter 1 Back and chapter 11 Next states (F10).
- zh at 200 % zoom and 390 px, slide pages with the chip (F3, F16).
- Recording modal: description line present, no autoplay under reduced motion, focus returns when opened from More (F4, F5).
