# Demo video: script and shot list

**Length:** about 5:55 (the limit is 8:00). **Format:** 1920×1080, dark theme.
**How it's made:** the screen recording is produced automatically (one clip per scene, so a scene can be
re-shot on its own). Ishaan records the voice-over on top. Read it like you're showing a friend something
you built: relaxed, not salesy. Pauses are fine.

Lines marked **[if Hy3 is on]** depend on Tencent approving the account in time. If it isn't on,
read the alternative line instead.

## Pacing: why it should feel like a person, not a robot

Judges watch a lot of videos. A demo that drags, or that moves like a script, loses them. So:

| What | How |
|---|---|
| Length per scene | Each scene is about as long as its narration plus a breath (~2.6 spoken words a second). No scene sits still waiting for the voice. |
| The cursor | A visible pointer moves on gentle curves, slows into its target and shows a soft ring on each click (`record_scenes.cjs`). |
| Typing | Uneven rhythm: quicker inside words, a beat after commas and full stops, the odd hesitation. Suggested questions are clicked, not typed, as people do. |
| Reading | After an answer appears, the pointer drifts along the lines being talked about, so the eye knows where to look. |
| Scrolling | Small wheel steps, never a jump. |
| Speed | Clips play at real speed. The edit never speeds a clip up by more than 1.2×, so no one notices. |
| Labels | Small captions at the lower left say who is signed in and what just happened ("Ken · outside contractor"), so the video makes sense even on mute. |
| Cuts | Short cross-fades between scenes. Still cards drift in slowly instead of freezing. |
| Text size | The app is shown at a laptop size (1536×864) and scaled up, so text is about 25% larger than on a full-HD screen. |

Exact scene times are in `submission/05_video/narration_cues.md`, generated from the finished video.

---

### 1 · Hello (0:00–0:20)
**On screen:** title card, then the sign-in page with the people at Company A. The pointer passes over
Jordan, Ken and Maya, then picks Jordan.

> Hi! This is the Walnut Internal Brain, our entry for Aspire's FinTech challenge. Ask one question
> across Confluence, Jira, Slack and Google Drive, and it answers only from what *you* are allowed to
> open, with every step on an audit trail. We built it with Tencent CodeBuddy, and it plugs into
> Tencent WorkBuddy.

### 2 · Why this matters (0:20–0:49)
**On screen:** the problem card: the search-time number, then three short lines on fintech risks.

> We all lose hours looking for things: the answer is in a ticket, a Slack thread, a runbook,
> somewhere. An AI assistant should fix that. But in a fintech, an assistant that can read everything
> can also *leak* everything, and showing the wrong person a fraud case can be a crime. So we built it
> around one rule: the model never sees anything the person asking can't see.

### 3 · One question, three platforms (0:48–1:24)
**On screen:** signed in as **jdoe** (backend engineer). He types *"What's the status of the database
migration project and were there any blockers raised in Slack last week?"*. The pointer reads along the
answer, touches the citations, then the "How this was checked" strip and the sources panel.

> Meet Jordan, a backend engineer. He asks one plain question, and gets one answer, stitched together
> from Slack, Jira and Confluence, with every sentence linked to its source.
> Look at the strip under the answer: only sources Jordan can open, each one re-checked with the source
> system just before it was used, and the whole thing logged.
> There's also a blocker discussed in a leads-only channel Jordan isn't in. It's not here. It never even
> reached the model.

### 4 · The map of what you can see (1:24–1:40)
**On screen:** the **Map** page as jdoe (867 items, 4 platforms); a click opens one channel's items. Then
the same page as **ken.ext** (24 items, 2 platforms).

> This is Jordan's map: everything he can open. Eight hundred and sixty-seven items, each checked
> live with its source. Ken, a contractor, sees twenty-four. And there's no "plus 843 hidden": for Ken,
> the rest simply doesn't exist.

### 5 · Same question, two people (1:40–1:58)
**On screen:** the **Compare** page, jdoe and ken.ext, "Ask for both", two answers side by side.

> Here's the same question asked by Jordan and by Ken, side by side. Jordan gets the full picture.
> Ken gets the polite "I couldn't find that in the sources you can access", with no hint that there's
> anything more to find.

### 5b · A guided tour for anyone new (1:58–2:12)
**On screen:** the "Take the tour" button, then the first two pop-ups of the tour.

> And if you're new, there's a short guided tour that walks you through all of this in about a
> minute.

### 6 · Restricted looks exactly like non-existent (2:11–2:28)
**On screen:** as ken.ext, ask about "the security incident report from the Q3 breach" (real, restricted),
then the Q7 breach (made up).

> One more test. Ken asks about a real, restricted incident report, then about one we made up. The two
> answers are byte-for-byte identical. We test this on four hundred pairs of questions, and there's no
> difference in any of them.

### 7 · The fintech part (2:28–2:55)
**On screen:** as **amy.am** (account manager), two suggested questions: why Velquarry Trading's account is
restricted (a real customer under review), then the same for Bayfront Spice Traders (a company that does
not exist). Then, as **leo.ops**, "Can we offer multi-currency accounts to an Indonesian SME?".

> The big fintech case is "what can I tell this customer?". Amy, an account manager, asks why a
> customer's account is restricted. She gets the approved script, never the case notes, and it reads
> exactly like the answer for a company that doesn't exist. And "what can we offer this business?"
> gets the answer for its own market, from the approved policy, never a draft.

### 8 · Access changes, answers change (2:55–3:25)
**On screen:** as **admin.it** on the Sim page: "Remove jdoe from #db-migration". Back as jdoe, ask again.
Then "Edit failover runbook (new version)" and, as priya.sre, ask for the latest runbook (version 8).

> Permissions change all the time. Here we remove Jordan from a Slack channel. His very next question
> no longer uses that channel, even if Slack's notification about it gets lost, because we check with
> the source every time.
> And when the runbook owner adds a new step, Priya's next question shows version 8, live. Nothing
> stale.

### 9 · The audit trail (3:24–4:08)
**On screen:** as **maya.compliance** on the Audit page. She types *"Everything jdoe accessed in the
payment-gateway space, last 30 days"*. The filter chips and rows appear. She opens one request and reveals
its answer with a reason. Then **Verify audit chain**. Then the tamper card.

> Now the compliance view. Maya asks in plain English: everything Jordan accessed in the payment
> gateway space, last thirty days. She sees each question, what was considered, what was allowed or
> denied and why, and which model answered.
> She can't read answers by default. To see one, she has to give a reason, and that reveal is logged
> too.
> The log is chained and sealed. "Verify audit chain" checks every record and every stored answer.
> And if someone edits a single line on disk, or deletes one, verification catches it.

### 10 · Ask from WorkBuddy (4:08–4:31)
**On screen:** WorkBuddy's connector list with three Internal Brain connectors (as Jordan, as Ken, and for
compliance). Then one WorkBuddy chat asking the same question through Jordan's and Ken's connectors, two
different answers. Then the compliance connector listing Jordan's access and verifying the chain.

> People don't want another app, so the Brain also lives inside Tencent WorkBuddy. Each connector
> carries its own signed token, so the AI in the middle can't decide who you are: Jordan's connector gets
> Jordan's answer, Ken's gets Ken's. And Maya can run her audit checks right from WorkBuddy too.

### 11 · What's underneath (4:31–5:07)
**On screen:** the original Walnut screens on Company A's data: the knowledge graph (a node opens), the
brain view, the provenance chain of one value, the ontology.

> Under the hood is Walnut, the evidence engine we built before this hackathon. Every value it touches
> carries a pointer back to where it came from, and you can follow that pointer, like here, from a
> fact to the exact document. The Internal Brain reuses that engine as it is.

### 12 · How it fits together (5:07–5:29)
**On screen:** the trust-boundary card.

> Two checks before the model sees anything, a grounding check after it answers, and an audit record
> written before anything is shown.
> **[if Hy3 is on]** The answers are written by Tencent Hy3 through TokenHub.
> **[otherwise]** Here the answers are quoted straight from the sources; with a TokenHub key, Tencent
> Hy3 writes them instead, through exactly the same checks.

### 13 · The numbers (5:29–5:40)
**On screen:** the results card.

> We attacked it on purpose: nearly six thousand adversarial requests, zero leaks; four hundred paired
> questions, zero differences; and every scenario covered by automated tests.

### 14 · How we built it, and thanks (5:39–5:55)
**On screen:** the closing card.

> We used CodeBuddy to plan the trust boundaries, build the WorkBuddy connector, write tests and review
> our security, and it found real bugs in our own code. Six sessions, every transcript kept in the
> repository. Thanks for watching!

---

**Fallback:** every scene is its own clip; if one breaks, re-shoot just that one
(`node docs/tencent/video/record_scenes.cjs 07_fintech`, then `python docs/tencent/video/assemble.py`).
Never fake a result.
