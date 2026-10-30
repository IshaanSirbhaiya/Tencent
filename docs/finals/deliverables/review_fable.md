# Deliverables plan: product-design and pitch review (Fable), 30 Oct 2026

Reviewer: Fable 5.1, as product designer and pitch coach. Read-only; this file is the only output.

What I read: `docs/finals/deliverables/PLAN.md` (the plan under review), `RUN_SHEET.md`,
`live_growth/PLAN.md`, my own `reviews/final_fable.md`, `QA_BANK.md`, `PRESENTER_KEYS.md` §1-3,
`lib/i18n/act.ts` and `ActSheet.tsx` (the Act surface the panel must sit next to), the `df-3` record in
`databridge/fixtures/enterprise/world.json`, and `STATUS.md` (Hy3 line). No screens exist yet, so this
reviews the spec, not pixels.

Evidence tags as in the plan: **[R]** repo fact, **[I]** judgement, **[V]** check before relying on it.

---

## 0. The verdict in five lines

1. **Yes, it can impress, but only one screen of it will ever be seen.** It is a Q&A beat of about 15 s.
   Judges will see one kind, one panel, one pill. Build for that screen; everything that is not on it is
   invisible to the rubric on 3 Nov.
2. **The idea that scores is the pill, not the chart.** "3 of 3 numbers found in your sources", and the
   removal count when the model invents one, is the only new thing in the plan that no other team will
   have, and it answers the AI-Interaction judge's objection from `final_fable.md` §1 row 3 ("no model
   visible") with the model *and* a check on it in one glance.
3. **The hero chart is flat.** `df-3` has one numeric column: `0.35%`, `0.35%`, `0.4%` [R `world.json`
   `df-3.content`]. Three bars of which two are equal and the third is 14 % taller is not a picture; it
   is a table drawn badly. For *this* data the Table is the stronger screen, and it works with no model.
4. **Scope is 2x what the day can carry.** Six kinds, two export formats, 13 backend tests and a
   Playwright suite in about 3 h 30, on a day that also has live growth, clips and three timed runs.
   Cut to three kinds (Table, Chart, Analysis), no exports, and the plan fits in about 2 h 15.
5. **The 15-second script has a 10 to 20 s model call inside it** (plan §11 risk 3). Pre-build the
   deliverable during `P`'s warm-up and have `D` open a cached result; otherwise the beat is dead air.

Rubric movement if the top five below land and Hy3 is live: AI Interaction +2 (5 → 7), Innovation +1,
Responsible AI +0.5. If Hy3 is not live: Innovation +1 only, from the table and the honest label. It
never moves Demo & Storytelling, because it is not in the 5:00.

---

## 1. Will this impress judges?

**What lands [I]:**

- A value-level check that a judge can *see*: every bar or cell carries a citation chip, the pill counts
  what was checked, and the amber line counts what the model invented. No team at this hackathon will
  show the model being caught in the act and the catch being counted. This is exactly the "AI you can
  trust" story the Responsible-AI judge wants, and it is the first place in the demo where Hy3 does a
  job more visible than writing prose.
- "Draft for your review" and "Written by Hy3" in the same line as the pill: the model is named and
  bounded in one glance. Good.
- It turns Aspire's brief ("what can we offer this customer") into the artefact an account manager
  actually sends: a comparison table by market. That is the Impact link `final_fable.md` §1 row 1 asked
  for and the 5:00 does not make explicitly.

**What does not land [I]:**

- Exports. A `.pptx` cannot be seen on stage without leaving the browser; a `.csv` download is a
  toast. Both cost about 35 min plus a lock-file risk (`python-pptx` not pinned [R plan §6.1]) for zero
  seconds of judge attention. Ship them after Demo Day.
- Six chips. The plan's row plus Act's row puts eight actions under one answer. The judge who walks up
  in Q&A sees a toolbar, not a product. Email duplicates Act's "Draft a reply" (same path, same panel;
  the `reply`/`message` difference is invisible). Summary is Analysis with different headings; the two
  closed lists overlap (`Risks` is in both) and a judge cannot tell them apart.
- A chart whose three bars are 0.35, 0.35 and 0.4. If the chart stays the hero, it needs value labels
  on the bars so it reads as numbers (§7), or a different question whose answer has spread [V: a
  5-minute scan of `world.json` for a CSV-shaped document with more than one numeric column; I did not
  do it].

### 1.1 Rank of the kinds (judge value per build minute)

| Rank | Kind | Why | Hy3 needed | Build |
|---|---|---|---|---|
| 1 | **Table** | Works with no model (`source-table-v1` from `df-3`, 4 rows x 5 columns including `availability` text such as "not offered"); it *is* Aspire's deliverable; the fallback for every other kind; cheapest to make look finished | No | S/M |
| 2 | **Chart** | The pill, the hover, the drop count: the AI-Interaction beat. Weak data in `df-3`; needs value labels. Needs the deterministic path (plan §4.4) to exist at all when Hy3 is down | Yes (or §4.4) | M |
| 3 | **Analysis** | The one kind where Hy3 does something a table cannot: "Risks: the 0.55 % figure is a draft, not approved [C3]". The best Q&A answer to "what if the model makes something up?" Prose, so low on screen | Yes | M (shares `TextView`) |
| 4 | Email | Free (Act's draft) but a duplicate chip. Drop the chip; Act already has it | — | 0 |
| 5 | Summary | Indistinguishable from Analysis on stage | Yes | drop for 3 Nov |
| 6 | Slides | Cannot be seen on stage; lock-file risk; 35 min | Yes | drop for 3 Nov |

If there is time for only one: **Table**, then the deterministic Chart from it. The model kinds are
worth building only after `hy3_check.py` has returned a live answer once [R `STATUS.md` line 23].

---

## 2. Panel UX

### 2.1 The row

- **Heading.** "Make from this answer" is a verb phrase that does not read as English on its own.
  "Turn this answer into" with chips **Chart · Table · Analysis** reads as a sentence
  ("Turn this answer into a chart"). Micro caps as Act's "Act on this answer" so the two rows are
  siblings [R `act.ts` line 7].
- **Three chips, not six** (§1). Chips show only what the server says is possible:
  `/ib/status.deliverables.model === false` → Table (and Chart if §4.4 ships) only. A chip must never
  lead to a screen that says the product's AI is unavailable (§3).
- **Hint line.** "Built only from this answer and the sources you can open." is right. Keep the wording
  parallel to Act's hint [R `act.ts` line 10] in length and tone.

### 2.2 Tabs

Tabs are fine for the person; they are a risk for the judge. In Q&A a judge who reaches over and clicks
Analysis triggers a 10 to 20 s model call with a skeleton. Two mitigations, both cheap:

- Pre-build every kind the row offers during `P` warm-up, for Leo's stage question only (§5). Then
  every tab is instant for that one answer.
- Tabs show only the kinds the row offers. No disabled tabs, no "coming soon".

Keep Radix Tabs; no new component.

### 2.3 Badges and the pill

The plan stacks three badges, an amber line and a "source changed" line above the content (§2.2 items
2 to 4). At 150 % that is five rows of chrome before the first number. Collapse to **one metadata line**:

```text
Draft for your review · Written by Hy3 · 3 of 3 numbers found in your sources
```

- The pill is the only element with a tone; the other two are plain text. Tone **neutral, not green**.
  Green reads as "verified", which is exactly the claim the plan rightly refuses to make (§2.5).
- When something was removed, fold it into the pill: **"2 of 3 numbers found · 1 removed"**, and put
  the full sentence ("1 number the model wrote was not in your sources, so it was removed.") in the
  pill's tooltip and in the accessible name. Amber tone only in that case.
- **Deterministic builds: no numbers pill.** "4 of 4 found" is trivially true when every cell was
  copied; a judge who thinks about it for two seconds sees a vanity metric. Say instead:
  "Copied cell by cell from [2], no model." That sentence is the honest label and the better story.
- "Source [2] was updated since your answer (v4 → v5); this uses v5." Keep, one line, below the content
  not above it. On stage it will not fire (`df-3` is at v9 and nothing edits it).
- Wording consistency: the plan says "Recorded in the access log" and "access ledger"; Act says
  "Recorded in the audit trail before it was shown." [R `act.ts` line 27]. Use Act's words. The product
  has one ledger and the slides call it the audit trail.

### 2.4 Chart content

- The figcaption data table is right for accessibility and should stay visible. Keep it to the three
  columns the plan names and no caption text above it beyond "Data"; otherwise the chart tab looks like
  the table tab with a picture on top.
- **Hover is invisible from the judges' table.** The plan's citation lives in the tooltip. Put it on
  the chart: a value label above each bar with the `raw` string ("0.35%") and the chip beside the
  x label ("SG Growth [2]"). The hover becomes a bonus for the judge who walks up, not the only proof.
- Tooltip copy: "0.4% FX margin · found in [2] Pricing & Offers" is good. Not "verified".

### 2.5 States

The table in plan §2.4 is complete and the copy is honest. Three notes:

- **Loading.** "Checking your sources again and building…" is good. Add the kind: "…building the
  chart". No spinner over 5 s without a sentence change: at 8 s, "Still working; Hy3 is slow right
  now." (the plan's 30 s budget means a judge could stare at a skeleton for half a minute).
- **Too little to chart.** "Not enough numbers from your sources to draw a chart." Good. The button
  should read "Show the table instead" to match the other fallback button.
- **Error.** "Something went wrong. Nothing was saved." Act says "This is temporarily unavailable.
  Nothing was sent or created." [R `act.ts` line 60]. Use one sentence across both.

---

## 3. Is "Needs the model" acceptable?

**As a state, yes. As a screen a judge sees, no.**

The copy is honest and the product should have it: a model that fails mid-call must say so and offer
the table. But the AI-Interaction judge's worst screen is the product announcing "This needs the model
(Hy3). It isn't available right now." That is the `final_fable.md` §6 risk 1 problem ("every answer says
no model") made explicit with a button.

Rules for 3 Nov:

1. When no model is **configured** (`deliverables.model: false`), the row offers only the kinds that
   work. Not offering a feature that is not configured is normal product behaviour, not concealment;
   the HUD's "Model quoting" dot already tells the truth about the model's state.
2. `needs_model` is reached only when a model was asked and failed (`fallback: model_error / timeout /
   nothing_passed`). The screen then says what happened in the plan's words and offers the table.
3. **Say yes to the deterministic chart (plan §4.4, 15 min).** It is the single change that makes the
   Chart chip safe to show on a no-Hy3 morning, it is fully checked by the same rules, and its label
   ("Charted from the table in [2], no model") is itself a Responsible-AI line. The brief's "offline
   produces only a table" was written before anyone knew Hy3 would still be at 402 on 30 Oct
   [R `STATUS.md` line 40]; the brief should yield to the day.
4. If Hy3 is live, keep the deterministic path as the fallback, not the default: "Written by Hy3" on the
   chart is worth more than "no model" to this rubric, and the drop count only means something when a
   model wrote the numbers.

---

## 4. In the 5:00 or Q&A only?

**Q&A only.** The plan's §10.3 is right, for three reasons beyond "the sheet is full":

1. The 5:00 has one spine, said three times: "Answers you can prove." Live growth extends that spine
   (the same rule on brand-new data). A chart feature is a second product story; told in 20 s it dilutes
   T1 to T2, and the judge remembers neither.
2. The beat depends on Hy3 for its best version. Nothing Hy3-dependent belongs in the 5:00 until a live
   answer has been seen (the run sheet's own [HY3] rule).
3. Live growth has already spent the only funded cuts (`live_growth/PLAN.md` §11.2). Two optional beats
   fighting for the same 20 s is how a 5:00 becomes 5:25.

**But make the question come.** A Q&A beat that nobody asks for scores nothing. Three doors are already
in `QA_BANK.md`:

- **Q10 "What if the model makes something up?"** is the natural door, and the best: the answer *is*
  the pill. Rewrite Q10's second half: "…and the same check runs on anything we build from an answer.
  Dev, D." Then the 15 s below.
- Q7 "Why Hy3?" and Q18 "How did you use Tencent products?": add one clause, "Hy3 also writes charts
  and analyses from an answer, under the same checks", and offer to show it.
- If none of the three comes by 5:40, Ishaan closes Q&A with "One more thing we did not have time for"
  and takes the 15 s. Judges remember the last screen.

**Session state.** `D` signs in `leo.ops` as the main session (plan §10.2). Q&A otherwise sits on
`/dev/journey` as Maya (`RUN_SHEET.md` 5:00 line). After the beat, `Esc` closes the panel and the screen
is Leo's ask page, not the journey. Acceptable; but `R` must still return cleanly (final_fable.md §6
risk 2), so rehearse `D` then `R` once.

---

## 5. The 15-second Q&A script

Target: 37 to 40 spoken words, about 2.5 words a second. The plan's line is about 55 words with the
"Dev: D" and will run 22 s.

**Precondition (non-negotiable).** During `P` warm-up: sign in Leo, ask `Q_DELIVER`, and build the
Chart, Table and Analysis for that `req_id` into the store. `D` then navigates to Leo's answer and opens
the panel on a **cached** deliverable (well under 2 s). Raise the store TTL from 30 min to 90 min or
rebuild on `R`; `P` may be pressed 20 min before the slot and Q&A ends at about 6:00 after it. Without
this, the script below has a 10 to 20 s hole in the middle.

### 5.1 Hy3 live (chart)

| Who | Line | Screen |
|---|---|---|
| Ishaan | "Dev, D." | Panel opens on Chart: three bars with value labels `0.35%` `0.35%` `0.4%`, each x label with its `[2]` chip; pill "3 of 3 numbers found in your sources" |
| Ishaan | "Leo's answer, as a chart. Every bar is a number found in a source he can open, on the line that names it." | Dev hovers the ID bar: "0.4% FX margin · found in [2] Pricing & Offers" |
| Ishaan | "Anything the model invents is removed, and the pill says so. Sealed in the audit trail before he saw it." | Dev scrolls one notch: footer "Recorded in the audit trail before it was shown" |

39 words. If a judge asks "removed how?", Dev clicks Analysis (cached): "Risks: the 0.55 % figure is a
draft, not approved [3]." Ishaan: "It copies, it never calculates, and it flags drafts."

### 5.2 Hy3 not live (table)

| Who | Line | Screen |
|---|---|---|
| Ishaan | "Dev, D." | Panel opens on Table: 4 rows x 5 columns from the pricing sheet, every cell with `[2]`; line "Copied cell by cell from [2], no model" |
| Ishaan | "Leo's answer, as the comparison table: copied cell by cell from the pricing sheet, every cell cited." | Dev clicks one chip: source sheet opens with `0.4% FX margin` highlighted; `Esc` |
| Ishaan | "With the model on, the same checks write the chart and the analysis. Nothing it invents survives them." | Hold on the table |

31 words. Never say "the model is down". The HUD already says "Model quoting"; the line says what the
design does, which is the strength `final_fable.md` §6 risk 1 asks for.

---

## 6. Design details: premium, not AI-generated

What makes an "artifact panel" look like every chat product's canvas: a rounded card with a gradient
header, a sparkle icon, a cluster of coloured badges, a Recharts default chart in `#8884d8` purple and
`#82ca9d` green with a dashed grid and a white tooltip box, a model-written title in Title Case, and
prose that opens with "Overall". Avoid each, specifically:

1. **Typography is the answer's.** The panel title uses the page's H2 style (Inter, the same weight as
   the answer card's heading), one hairline rule under the metadata line, no card-in-card. The
   `SheetContent` already carries the system; add nothing.
2. **Chart: strip Recharts.** `isAnimationActive={false}` always (not only under reduced motion);
   no `CartesianGrid` or horizontal hairlines only, 1 px, the `border` token; one series colour, the
   theme accent; `maxBarSize={56}` so three bars do not become three planks across 900 px; bar radius
   2 or 0; no legend for one series; `LabelList` with the `raw` string above each bar in tabular
   numerals; y axis from 0 with at most four ticks, `0.0%` format; tooltip restyled to the app's popover
   (same tokens as the citation chip hover), never the default white box with a black border.
3. **Numbers in tabular figures everywhere** (`font-variant-numeric: tabular-nums`): bar labels, table
   cells, the pill. The app already does this on the journey; keep it.
4. **Titles.** Model titles are the loudest AI tell ("Comparative Analysis of Multi-Currency Account
   Offerings Across Markets"). Rules in code, not in the prompt: sentence case; at most 60 characters;
   fall back to the fixed title when the model's title contains any of `Comprehensive`, `Comparative`,
   `Overview`, `Analysis of`, `Insights`, `Leveraging`, `Deep dive`, or ends with a colon. For the stage
   chart the fixed title "FX margin by market" beats anything Hy3 will write.
5. **Prose.** The checks drop invented values; they do not drop filler. Drop a sentence that starts with
   `Overall`, `In conclusion`, `In summary`, `Notably`, `It is important to note`, `It is worth noting`;
   cap a sentence at 30 words (the schema's 300 characters allows 50). Ten minutes in `verify.py`,
   and it is the difference between a memo and a chatbot.
6. **One tone colour on the whole panel.** The pill (neutral), the chips (as the answer), the accent on
   the bars. Amber appears only for a removal. No green anywhere: green means "correct", and the panel
   never claims that.
7. **Chips are the answer's chips.** Same component, same numbering, same hover; nothing new to learn.
   A judge who has just seen `[2]` open a source at 1:10 should see the same `[2]` here.
8. **Empty cells read as words, not dashes.** "Not stated in your sources" in the muted token. In
   `df-3` the ID Starter price is `-` in the source; copy it verbatim as `-` (it is in the source) but
   never render our own empty cell as `-`.
9. **Footer order.** Content, then actions (Copy · Regenerate), then "Cited in this chart" (Act's
   heading pattern [R `act.ts` line 17]), then the scope note, then "Recorded in the audit trail before
   it was shown · d7c1e0a94b2f". Same order as `ActSheet`, so the two panels are one family.
10. **Loading skeleton** in the shape of the kind (three bars for Chart, four rows for Table), the
    plan's one honest sentence, no shimmer animation under reduced motion.
11. **Stage theme check at 150 %.** `sm:max-w-4xl` is 896 px CSS; at the Stage root scale the panel is
    most of a 1280 viewport, which is right. Check that the pill does not wrap and that the chart's
    value labels are at least 24 px (`PRESENTER_KEYS.md` §3 floor). Screenshot into
    `Tencent/submission/` as the evidence rule says.

---

## 7. Ranked recommendations with effort

Effort: **S** under 15 min, **M** 15 to 45 min, **L** over 45 min. "Saves" is time removed from the plan.

| # | Recommendation | Why | Effort |
|---|---|---|---|
| 1 | **Cut to three kinds: Table, Chart, Analysis. Drop Email (duplicate of Act), Summary (duplicate of Analysis) and Slides (invisible on stage).** No exports on 3 Nov; `.csv` and `.pptx` are post-hackathon. Keep the API's `kind` enum as designed so nothing is renamed later | §1. One screen is seen; eight actions under an answer is a toolbar | Saves about 75 min (steps 7, part of 9, T11) |
| 2 | **Pre-build Leo's deliverables during `P` warm-up; `D` opens a cached result.** Store TTL 90 min or rebuild on `R` | §5. A 10 to 20 s model call cannot sit inside a 15 s beat | M (presenter warm-up hook, one handler) |
| 3 | **Say yes to the deterministic chart (plan §4.4); offer only working kinds when no model is configured; keep `needs_model` for mid-call failures only** | §3. The Chart chip must never open "the AI is unavailable" in front of a judge | S (15 min, as the plan costs it) + S (row filter) |
| 4 | **One metadata line; neutral pill; fold the removal into the pill ("2 of 3 found · 1 removed"); no pill on deterministic builds ("Copied cell by cell from [2], no model")** | §2.3. Five rows of chrome at 150 % before the first number; green would claim "verified" | S |
| 5 | **Chart that reads from the judges' table: value labels with the `raw` string, chip beside each x label, strip Recharts defaults, no animation, `maxBarSize`, one accent** | §2.4, §6.2. Hover is invisible from three metres; the demo data is 0.35 / 0.35 / 0.4 | M |
| 6 | **Rewrite QA_BANK Q10 to end in "Dev, D." and add the clause to Q7 and Q18; the 15 s scripts of §5 go into `QA_BANK.md` and `PRESENTER_KEYS.md` with the [DELIVER] truth tag** | §4. A Q&A beat nobody asks for is not seen | S |
| 7 | **Title and filler rules in `verify.py`** (sentence case, 60 chars, banned openers, 30-word cap) | §6.4 to 6.5. The loudest AI tells are not numbers | S |
| 8 | **Copy consistency with Act:** "audit trail" not "access log"; one error sentence; "Turn this answer into" as the row heading; "Cited in this chart/table/analysis" as the sources heading | §2.1, §2.3, §2.5 | S |
| 9 | **[V] today, not on the morning: Leo's answer to `Q_DELIVER` cites `df-3`; and a 5-minute scan of `world.json` for a CSV-shaped document with spread, in case a better chart exists** | §1. The whole beat hangs on one citation; the current chart is flat | S |
| 10 | **Test order for the day:** T1 to T7, T9, T10 mandatory before the flag is on; T8, T12, T13 if time; T11 drops with the exports. UI (step 9) starts no later than 1 h 45 in | Rule 4 first, but the judge sees only the UI | 0 (ordering) |
| 11 | **Loading copy that changes at 8 s** ("Still working; Hy3 is slow right now") and kind-shaped skeletons | §2.5. A 30 s budget is a long stare | S |
| 12 | **Rehearse `D` then `R` once** on the stage laptop so the session switch to Leo and back is known to work | §4 session state; `final_fable.md` §6 risk 2 | S |

What the plan already gets right and should not change: building on `ActionService.context` (one rule
for every "do something with my answer" feature); the field-by-field co-location check; write-ahead
before the model call; no generated text in the ledger; "found in your sources", never "verified";
EN + 简体中文 from the start; the figcaption data table; the Q&A-only decision; the feature flag rather
than demo-only. Decision 10 in particular is the right instinct: "demo-only" would say "we only trust this
on stage", and the Responsible-AI judge would hear it.

---

## 8. Top five, in one breath

1. Three kinds (Table, Chart, Analysis), no exports: saves 75 min, loses nothing a judge can see.
2. Pre-build on `P`, open cached on `D`: the 15 s beat has no 15 s hole.
3. Deterministic chart on, model-only chips hidden when no model is configured: the Chart chip is safe
   on a no-Hy3 morning and "Needs the model" is never a stage screen.
4. One metadata line with a neutral pill that also carries the removal count; no pill on copied tables.
5. A chart that reads from three metres: `raw` value labels on the bars, chips at the x labels, Recharts
   defaults stripped, one accent, no animation; plus Q10 rewritten to end in "Dev, D." so the question
   actually arrives.
