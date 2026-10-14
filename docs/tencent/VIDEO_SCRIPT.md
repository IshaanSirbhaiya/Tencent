# Demo video: script and shot list

**Length:** about 7:30 (the limit is 8:00). **Format:** 1920×1080, dark theme, browser at 110%.
**How it's made:** the screen recording is produced automatically (one clip per scene, so a scene can be
re-shot on its own). Ishaan records the voice-over on top. Read it like you're showing a friend something
you built: relaxed, not salesy. Pauses are fine.

Lines marked **[if Hy3 is on]** depend on Tencent approving the account in time. If it isn't on,
read the alternative line instead.

---

### 1 · Hello (0:00–0:20)
**On screen:** title card, then the sign-in page with the personas.

> Hi! This is the Walnut Internal Brain, our entry for Aspire's FinTech challenge. It's one place to ask
> questions across Confluence, Jira, Slack and Google Drive, and it only ever answers from what *you* are
> allowed to open. Every question it handles is written to an audit trail that can't be quietly edited.

### 2 · Why this matters (0:20–0:55)
**On screen:** the problem slide: the search-time number, then three short lines on fintech risks.

> We all lose hours looking for things: the answer is in a ticket, a Slack thread, a runbook,
> somewhere. An AI assistant should fix that. But in a fintech, an assistant that can read everything
> can also *leak* everything, and showing the wrong person a fraud case can be a crime. So we built it
> around one rule: the model never sees anything the person asking can't see.

### 3 · One question, three platforms (0:55–1:40)
**On screen:** signed in as **jdoe** (backend engineer). Ask: *"What's the status of the database migration
project and were there any blockers raised in Slack last week?"* Hover the citations, then the "Checks on
this answer" strip.

> Meet Jordan, a backend engineer. He asks one plain question, and gets one answer, stitched together
> from Slack, Jira and Confluence, with every sentence linked to its source.
> Look at the strip under the answer: only sources Jordan can open, each one re-checked with the source
> system just before it was used, and the whole thing logged.
> There's also a blocker discussed in a leads-only channel Jordan isn't in. It's not here. It never even
> reached the model.

### 4 · The map of what you can see (1:40–2:15)
**On screen:** the **Map** page as jdoe (867 items, 4 platforms). Click a channel to open its items. Then
switch to **ken.ext** (24 items, 2 platforms).

> This is Jordan's map: everything he can open. Eight hundred and sixty-seven items, each checked
> live with its source. Ken, a contractor, sees twenty-four. And there's no "plus 843 hidden": for Ken,
> the rest simply doesn't exist.

### 5 · Same question, two people (2:15–2:50)
**On screen:** the **Compare** page, jdoe vs ken.ext, the same question, two answers side by side.

> Here's the same question asked by Jordan and by Ken, side by side. Jordan gets the full picture.
> Ken gets the polite "I couldn't find that in the sources you can access", with no hint that there's
> anything more to find.

### 5b · A guided tour for anyone new (about 15 seconds)
**On screen:** the "Take the tour" button, then the tour's pop-ups stepping across the screens.

> And if you're new, there's a short guided tour that walks you through all of this in about a
> minute.

### 6 · Restricted looks exactly like non-existent (2:50–3:15)
**On screen:** as ken.ext, ask about "the security incident report from the Q3 breach", then the Q7 breach,
which doesn't exist.

> One more test. Ken asks about a real, restricted incident report, then about one we made up. The two
> answers are byte-for-byte identical. We test this on four hundred pairs of questions, and there's no
> difference in any of them.

### 7 · The fintech part (3:15–3:45)
**On screen:** as **amy.am** (account manager): ask about the restricted customer, then the made-up one.
Then the "what can we offer this SME in Singapore / in Indonesia" pair.

> Aspire's team told us the big use case is "what can we offer this customer?". Amy, an account
> manager, gets the approved script for a customer under review, never the case notes, and it reads
> exactly like the answer for a company that doesn't exist. And the offer question gets a different,
> correct answer per market, always from the approved policy, never from the draft.

### 8 · Access changes, answers change (3:45–4:20)
**On screen:** as **admin.it** on the Sim page: "Remove jdoe from #db-migration". Back as jdoe, ask again.
Then "Edit the failover runbook" and, as priya.sre, ask for the latest runbook (shows version 8).

> Permissions change all the time. Here we remove Jordan from a Slack channel. His very next question
> no longer uses that channel, even if Slack's notification about it gets lost, because we check with
> the source every time.
> And when the runbook owner adds a new step, Priya's next question shows version 8, live. Nothing
> stale.

### 9 · The audit trail (4:20–5:20)
**On screen:** as **maya.compliance** on the Audit page. Type *"Show me everything jdoe accessed related to
the payment-gateway space in the last 30 days"*. Show the filter chips and rows. Open one request, reveal
its answer with a reason. Press **Verify audit chain**. Then the terminal: `python scripts/tamper_demo.py`.

> Now the compliance view. Maya asks in plain English: everything Jordan accessed in the payment
> gateway space, last thirty days. She sees each question, what was considered, what was allowed or
> denied and why, and which model answered.
> She can't read answers by default. To see one, she has to give a reason, and that reveal is logged
> too.
> The log is chained and sealed. "Verify audit chain" checks every record and every stored answer.
> And if someone edits a single line on disk, or deletes one, verification catches it.

### 10 · Ask from WorkBuddy (5:20–5:50)
**On screen:** WorkBuddy with two workspaces side by side ("IB as jdoe" and "IB as ken.ext"), the same
question, different answers. Then the "IB compliance" workspace verifying the chain.

> People don't want another app, so the Brain also lives inside Tencent WorkBuddy. Each person's
> connection carries their own signed token, so the model can't decide who you are. And Maya can run
> her audit checks right from WorkBuddy too.

### 11 · What's underneath (5:50–6:25)
**On screen:** the original Walnut screens on Company A's data: the knowledge graph (expand a node), the
provenance chain of one value, the ontology.

> Under the hood is Walnut, the evidence engine we built before this hackathon. Every value it touches
> carries a pointer back to where it came from, and you can follow that pointer, like here, from a
> fact to the exact document. The Internal Brain reuses that engine as it is.

### 12 · How it fits together (6:25–6:50)
**On screen:** the trust-boundary diagram.

> Two checks before the model sees anything, a grounding check after it answers, and an audit record
> written before anything is shown.
> **[if Hy3 is on]** The answers are written by Tencent Hy3 through TokenHub.
> **[otherwise]** Here the answers are quoted straight from the sources; with a TokenHub key, Tencent
> Hy3 writes them instead, through exactly the same checks.

### 13 · The numbers (6:50–7:10)
**On screen:** the RESULTS.md headline.

> We attacked it on purpose: nearly six thousand adversarial requests, zero leaks; four hundred paired
> questions, zero differences; and every scenario covered by automated tests.

### 14 · How we built it, and thanks (7:10–7:30)
**On screen:** CodeBuddy session screenshots, then the closing card.

> We used CodeBuddy to plan the trust boundaries, build the WorkBuddy connector, write tests and review
> our security, and we kept every session's log. Thanks for watching!

---

**Fallback:** every scene is its own clip; if one breaks, re-shoot just that one. Never fake a result.
