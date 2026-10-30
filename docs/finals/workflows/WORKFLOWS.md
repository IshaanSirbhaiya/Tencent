# Walnut demo workflows: scenarios, problems, features and scripts

Three recorded workflows for the Tencent Cloud AI CAN DO IT Hackathon finals (FinTech track, Aspire's
"Internal Brain" brief). Each is about 60 seconds. Written 30 Oct 2026.

All people, companies, cases and records are fictional: synthetic Company A, plus the Atlas project.

---

## 1. The story in one line

**Alex** shows that Walnut understands a company and builds the interface you need. **Amy and Rachel**
show that what you get depends on who you are. **Maya** shows that every answer is on a sealed record
that can be proved.

| # | Workflow | Persona | The problem | What it proves |
|---|---|---|---|---|
| 1 | First day on a project | Alex Morgan, incoming product lead | Project knowledge is scattered across tools and people | Generated pages, cited answers, recommended actions |
| 2 | The call | Amy Lim (account manager), then Rachel Tan (MLRO) | A customer asks about a frozen account during a confidential investigation | Permissions decide the answer; nothing restricted leaks |
| 3 | The audit | Maya Fernandes, compliance officer | The regulator wants proof of who saw what | Plain-English audit search, sealed log, tamper detection |

**How it maps to Aspire's brief:**
- **Context-aware knowledge:** workflow 1.
- **RBAC (role-based access):** workflow 2.
- **Security logging and audit trail:** workflow 3.

**Record in this order:** 1, then 2, then 3. Workflow 3 searches the records that workflow 2 creates.

---

## 2. Setup

**Start the app** (from the `Tencent/` folder), on the world that holds Company A plus Atlas, with Claude on:

```bash
W="$PWD/databridge/fixtures/scenarios/atlas_onboarding/world.json"
IB_WORLD="$W" MOCK_WORLD_PATH="$W" IB_VAR=var/ib-atlas IB_FORCE_OFFLINE=1 bash databridge/scripts/demo_up.sh start
```

- **App:** http://127.0.0.1:3100. Use light mode.
- **Chrome full screen:** ⌃⌘F. To hide the address bar, keep the mouse away from the top of the screen, or turn off View → Always Show Toolbar in Full Screen.
- **Slides:** `docs/finals/slides/alex-persona.html`, `amy-persona.html` and `maya-persona.html`. Click anywhere on a slide, or press F, for full screen.
- **Sources:** the Brain reads Slack, Jira, Confluence and Google Drive (all mock servers). Emails and a WhatsApp export are imported files in Drive.
- **Models:**
  - **Claude** (`claude-opus-5-5`) writes the chat answers and composes the pages while Tencent Hy3 is not live.
  - **Hy3** comes first in the chain as soon as TokenHub is activated.
  - **Hard spend cap:** US$10 across all Claude calls, with per-person and service-wide rate limits.
- **Editing:** cut every loading wait in the edit, keeping about one second of "thinking".
- **Restarting the website** (for example after a UI change) does not reset the audit log. The log lives in `var/ib-atlas`. Starting with `--fresh` would wipe it.

---

## 3. Workflow 1: First day on a project (Alex)

### The scenario
Alex Morgan has just joined as product lead of **Project Atlas**, a merchant-onboarding pilot for
Singapore SME customers:
- **Team and history:** twelve people and months of history.
- **Where it lives:** spread across a product brief in Confluence, tickets in Jira, decisions in Slack, a team roster and research in Drive, handover emails and a WhatsApp export.
- **What Alex needs:** what the project is, who did what, what changed, and what needs attention this week.

### The problem
On day one, a new lead has to:
- find the right channels and documents;
- work out who owns what;
- compare dates that have changed;
- ask colleagues to repeat history.

It takes days, and outdated information (an old pilot date, a fixed bug) is easy to mistake for current.

### What Walnut does
1. **One question, as Alex.** Walnut searches only what Alex is allowed to open.
2. **A cited chat answer**, written by the model and checked against the sources. A sentence that can't be tied to a source is dropped.
3. **A generated page beside the chat.** It opens by itself when the question needs more than a paragraph, with the project overview, status, team and contributions, decisions, blockers, timeline and first-week checklist. Every value is checked against its cited source.
4. **Recommended actions** under the answer: Draft a reply, Create follow-up ticket, Table, Chart, Analysis. There are also suggested follow-up questions.
5. **Change this page:** Alex says how to lay it out differently, and the page is rebuilt on the spot.

### What Walnut should find (ground truth from the Atlas pack)
- **Goal:** cut median onboarding from 5 business days to 1. This is a **target**, not a result.
- **Pilot scope:** 10 invited Singapore merchants. Status: **Amber**.
- **Pilot date:** the original 6 November date is **superseded**, and **12 November** is **provisional**, conditional on QA and support readiness.
- **QA:** 7 callback failures were found, **5 fixed and 2 remaining**.
- **Owners:** Elena owns the fixes; Lucas owns QA sign-off (3 Nov); the go/no-go meeting is 3 Nov at 16:00 SGT.

### Exact inputs
- **Sign in as:** Alex Morgan (`alex.product`), first on the sign-in screen.
- **Question:** it's also Alex's first suggested question.
  > Hi, I'm Alex Morgan, the new product lead for Project Atlas. Bring me up to speed with an onboarding brief: purpose, status, team table and contributions, decisions, blockers, timeline and first-week checklist. Use all connected sources, cite every claim and flag outdated or missing information.
- **Page change** (Change this page):
  > Put the blockers and my first-week checklist at the top, and show the team as cards

### Steps and script

| Time | Do | Say |
|---|---|---|
| 0:00–0:12 | Alex slide, full screen | "Meet Alex. It's Alex's first day leading Project Atlas: twelve people, months of history, and a pilot about to launch. Normally that means digging through Slack, emails, client contracts, documentation, databases and code. Instead, Alex asks Walnut once." |
| 0:12–0:24 | Walnut tab, signed in as Alex. Type the start of the question, cut to it complete, press Enter | "I'm signed in as Alex, so Walnut only searches what Alex is allowed to open. I ask for a full onboarding brief, with every claim cited." |
| 0:24–0:36 | The page opens beside the chat; scroll slowly | "And here is the full information related to Project Atlas, all on one page, every claim linked to its source." |
| 0:36–0:48 | Scroll to the action buttons under the answer | "Walnut also recommends what to do next: draft a reply, create a follow-up ticket, or turn it into a table, chart or analysis. And it suggests the next questions to ask." |
| 0:48–1:00 | Type the page change in Change this page, press Update, hold on the result | "Want it laid out differently? Just say so. Walnut can generate it in any format or representation you want: tables, charts, cards, timelines, whatever format you want. It creates the UI you want, at the moment you ask." |

### Checks
- The slide lists Slack, emails, contracts, databases and code as places Alex would *normally* search. In this demo, Walnut connects to Slack, Jira, Confluence and Drive only. Say *"instead of searching…"*, never *"Walnut searches all of these"*.
- Building the page and rebuilding it each take up to about 30 seconds. Cut both waits.
- **Tested:** run on screen by Mehul on 30 Oct with Claude on. The answer cited ATL-101, the Atlas overview and #proj-atlas, and the page opened beside it. Response times were not measured for this workflow.

---

## 4. Workflow 2: The call (Amy, then Rachel)

### The scenario
- **Amy Lim**, an account manager, looks after about forty business customers.
- **The call:** one of them, **Velquarry Trading**, calls. Its account has been frozen without warning and the director wants to know why.
- **What Amy doesn't know:** the financial crime team is investigating Velquarry (case **FC-2026-0147**) for suspicious transfers, and the case is confidential.
- **Rachel Tan**, the MLRO (head of financial crime), can see the whole case.

### The problem
- **Amy can't answer quickly.** She'd have to put the customer on hold, search across tools and ask compliance.
- **She can't say the wrong thing either.** Hinting that there is an investigation is **tipping-off**, an offence under Singapore law (CDSA section 57). A company search tool that shows Amy the case, or even admits it exists, would create exactly that risk.

### What Walnut does
1. **Same question, different answers, decided by who is asking.** Walnut searches only what the signed-in person may open (Gate 1), then re-checks each source live with Slack, Jira, Confluence or Drive before using it (Gate 2).
2. **Restricted means non-existent.** Amy's answer doesn't refuse, hint or say "you don't have access". The case simply isn't there for her. She gets the approved customer script.
3. **Draft a reply** builds a reply only from what the answer cited. It's marked "Draft for your review" and nothing is sent.
4. **Rachel's page:** the whole case on one page, every number linked to its source.

### Exact inputs
- **Question, identical for both people:**
  > Why has Velquarry Trading's account been restricted, and what should I tell the customer?
- **Sign in as:** Amy Lim (`amy.am`), then Rachel Tan (`mlro.tan`). To switch, tap the name at the top right, then **Switch person**.

### Steps and script

| Time | Do | Say |
|---|---|---|
| 0:00–0:15 | Amy slide, full screen | "Meet Amy. She's an account manager at a Singapore payments company, looking after about forty business customers. One of them, Velquarry Trading, has just called. Their account has been frozen without warning, the director is angry, and he wants to know why, right now. What Amy doesn't know is that behind the scenes, the financial crime team is investigating Velquarry for suspicious transfers. That case is confidential. So Amy is stuck. She has to answer the customer, but if she even hints that there's an investigation, that's called tipping-off, and in Singapore that's a crime." |
| 0:15–0:22 | Walnut tab: sign in as **Amy Lim**. Type the start of the question, paste the rest, press Enter | "So Amy asks Walnut. She's signed in as Amy, so Walnut only searches what Amy is allowed to open." |
| 0:22–0:29 | Scroll to the source card "Customer account review: approved customer script" with **Checked live**. Keep the model badge out of frame | "She gets exactly what she's allowed to say: the approved customer script, linked to its source. Nothing about the case. It doesn't even admit there is one." |
| 0:29–0:35 | Tap **Draft a reply**; the "Draft for your review" sheet opens; close it | "One click drafts a reply in the approved wording. Nothing is sent until Amy sends it." |
| 0:35–0:45 | Name at the top right → **Switch person** → **Rachel, MLRO (FinCrime)**. Paste the same question, press Enter | "Now Rachel, who runs financial crime, asks the exact same question, and gets a completely different answer: the whole case, from the case file, the transaction analysis and the investigation thread." |
| 0:45–0:56 | The page opens on the right (tap **Open as page** if it doesn't). Scroll to **37** and **nine days** | "And it's all on one page: thirty-seven transfers in nine days, why the account is restricted, and what the customer may be told. Every number linked to its source." |
| 0:56–1:00 | Hold the split view | "Same brain. Same question. Two different answers, because they're two different people." |

### Tested live on 30 Oct (Claude on)
- **Amy:** only the approved customer script, about 4 s. Her answer contains no case words. Her badge read "Quoted from sources (no model)", which is why the script says to frame it out.
- **Rachel:**
  - Claude wrote the answer from 5 sources in about 6 s: the FC-2026-0147 case page (Confluence), the transaction analysis (Drive), the #fincrime-cases thread (Slack), the tipping-off safeguards and the STR decision record.
  - Her page built in about 5 s, with a summary, a callout, the key numbers and the people. It contained "37" and "nine".

### Checks
- Amy's answer has no "investigation", "FinCrime" or "37".
- Rachel's page shows **37** and **nine days**. Retake if either is missing.
- Don't say an STR was filed, or quote the "11 of 37 on one device" figure. They're in Rachel's answer but not reliably on the page.
- Paste the question rather than dictating it, so "Velquarry" is spelled right.

---

## 5. Workflow 3: The audit (Maya)

### The scenario
After the Velquarry case, the regulator asks the company three things:
1. Who saw the case file?
2. Did anything reach someone outside the financial crime team?
3. Can you prove your records haven't been changed?

**Maya Fernandes**, the compliance officer, has to answer.

### The problem
Proving access normally means pulling logs from every system, matching user IDs, collecting screenshots
and asking each team, over weeks. Even then, an ordinary log can be edited quietly, so it proves little.

### What Walnut does
1. **Audit console, compliance only.** Anyone else who opens it sees a lock card.
2. **Plain-English audit search.** Rules turn the question into a typed filter, shown as removable chips *before* the results ("Interpreted as"). No model ever sees audit results.
3. **Every request end to end.** Each row shows who asked, what they asked, every access decision, and a record hash. Opening a row shows exactly which sources were released to that person.
4. **Write-ahead, sealed log.** Every question and answer is recorded before it is shown. Each record is sealed and hash-chained to the previous one, and the chain's head is also published outside the system.
5. **Verify audit chain** re-checks every link and seal and compares the head with the external copy.
6. **Tamper demo** attacks a *copy* of the log in several ways and shows which attacks are detected. The real log is never modified. One row honestly reads "Not detectable: the stated window" (a rollback after the last external publication).

### Exact inputs
- **Sign in as:** Maya Fernandes (`maya.compliance`). Then **Developer** (top left), then **Audit**.
- **Audit question 1** (first suggestion chip):
  > Who accessed the Velquarry STR decision record in the last 30 days?
- **Audit question 2** (second suggestion chip):
  > Show me everything amy.am accessed in the last 7 days
- Then **Verify audit chain**, then **Tamper demo** → **Run the tamper demo on a copy**.

### Steps and script

| Step | Do | Say |
|---|---|---|
| 1 | Maya slide, full screen | "Meet Maya, our compliance officer. After the Velquarry case, the regulator has questions: who saw the case file? Did anything reach someone outside the financial crime team, like Amy? And can we prove our records haven't been changed? Normally that means weeks of pulling logs from every system. Maya asks Walnut." |
| 2 | Walnut tab: Switch person → **Maya, Compliance officer** (cut this). Tap **Developer**, then **Audit** | "Only compliance can open the audit console. Everyone else sees a lock." |
| 3 | Click the chip **"Who accessed the Velquarry STR decision record in the last 30 days?"** (then **Interpret** if needed). Hold on "Interpreted as", then the rows | "Maya asks in plain English. Walnut turns it into a precise filter, shown right here, and finds every access. Only Rachel's financial crime team opened the case file. No one else." |
| 4 | Click the chip **"Show me everything amy.am accessed in the last 7 days"**. Click the top row with Amy's Velquarry question; the side panel opens | "And Amy? Her question is on the record too, with exactly what she was given: the approved script, and not a single case document. Every question and answer is recorded before anyone sees it." |
| 5 | Close the panel. Click **Verify audit chain**; hold on **Chain intact** | "One click proves the record is complete and untouched. Every entry is sealed, chained and checked against a copy kept outside the system." |
| 6 | Click **Tamper demo** → **Run the tamper demo on a copy**. Point at the red row "Edit one old record" → "Chain broken at entry …" | "And if anyone quietly edits even one old record, Walnut catches it straight away. This runs on a copy; the real log is never touched." |
| 7 | Hold on the results | "Every answer, provable. That's Walnut." |

### Tested live on 30 Oct (Atlas world)
- **Question 1:** resolves to "FC-2026-0147 Velquarry Trading Pte Ltd: STR decision record" over the last 30 days, parsed by rules. Every row belongs to `mlro.tan` (Rachel).
- **Question 2:** returns Amy's activity, including her Velquarry questions (5 on record at 22:16, from Mehul's takes and test runs). Each released the approved customer script and some unrelated #product notes, but **no case document**.
- **Verify audit chain:** OK. The internal chain is intact and the external head matches.
- **Tamper demo:** "Edit one old record" was flagged ("record 964: its seal or digest does not verify"), and the response confirmed the real log was not modified.

### Checks
- Several rows per person is fine. Click the top (most recent) Amy row.
- Use the exact wording above. *"Who accessed the FC-2026-0147 case files…"* finds nothing, because a case number alone doesn't resolve to a document.
- If Amy's Velquarry row is missing, workflow 2 wasn't recorded on this running app. Ask Amy's question once, then redo step 4.

---

## 6. Feature reference (what each workflow shows)

| Feature | What it is | Workflows |
|---|---|---|
| Sign-in as a person | Identity comes only from the signed-in session; typing a name in a question changes nothing | 1, 2, 3 |
| Gate 1: permitted search | Search runs only over documents the person may open; even ranking statistics ignore everything else | 1, 2 |
| Gate 2: live source check | Each source is re-checked with its platform before use ("Checked live", with version) | 1, 2 |
| Restricted means non-existent | No hint, no "access denied" for content a person can't open; it simply isn't there | 2 |
| Cited answers and grounding check | Every sentence carries a citation; a sentence that binds to no source is dropped; an invented name or value refuses the model's text and falls back to quoting | 1, 2 |
| Follow-up rewriting | A follow-up becomes a standalone search question; the chat shows "Searched for:" | — (available) |
| Generated pages (A2UI) | The model decides chat or page; the page is composed live and rendered with Google's A2UI format in Walnut's own components; every value is checked against its source | 1, 2 |
| Change this page | A request in plain words rebuilds the page ("make the table black", "show people as cards") | 1 |
| Recommended actions | Draft a reply, Create follow-up ticket, Table, Chart, Analysis; drafts are never sent automatically | 1, 2 |
| Audit console | Plain-English search to a visible typed filter, per-request rows, row drawer | 3 |
| Sealed, chained log | Write-ahead records, each sealed and hash-chained; head published externally | 3 |
| Verify audit chain | Re-checks every record and the external head | 3 |
| Tamper demo | Attacks a copy of the log and shows what is detected, including the one honest limit | 3 |
| Spend cap and rate limits | US$10 hard cap shared by all Claude calls; per-person and global limits | All (background) |

---

## 7. What not to claim on stage

- **Sources:** Walnut connects to Slack, Jira, Confluence and Google Drive in this demo. Email, contracts, databases, SharePoint and code appear only as the places people would *normally* have to search.
- **Models:** Hy3 is wired in first, but TokenHub is not activated yet. Claude is the model running in the recording.
- **Data:** the demo data is synthetic, and there are no live enterprise accounts.
- **Time savings:** don't quote numbers; none were measured.
- **Workflow 2:** don't say an STR was filed, or "11 of 37 on one device".
