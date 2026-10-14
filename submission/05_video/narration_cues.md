# Voice-over cue sheet

Play `walnut_internal_brain_demo.mp4` with `narration.srt` loaded as subtitles and read each line as it appears. Relaxed pace; pauses are fine.

## 0:00–0:20 · scene 1

- **0:00** Hi!
- **0:02** This is the Walnut Internal Brain, our entry for Aspire's FinTech challenge.
- **0:06** It's one place to ask questions across Confluence, Jira, Slack and Google Drive, and it only ever answers from what you are allowed to open.
- **0:15** Every question it handles is written to an audit trail that can't be quietly edited.

## 0:20–0:50 · scene 2

- **0:20** We all lose hours looking for things: the answer is in a ticket, a Slack thread, a runbook, somewhere.
- **0:27** An AI assistant should fix that.
- **0:30** But in a fintech, an assistant that can read everything can also leak everything, and showing the wrong person a fraud case can be a crime.
- **0:40** So we built it around one rule: the model never sees anything the person asking can't see.

## 0:50–1:20 · scene 3

- **0:50** Meet Jordan, a backend engineer.
- **0:52** He asks one plain question, and gets one answer, stitched together from Slack, Jira and Confluence, with every sentence linked to its source.
- **1:00** Look at the strip under the answer: only sources Jordan can open, each one re-checked with the source system just before it was used, and the whole thing logged.
- **1:11** There's also a blocker discussed in a leads-only channel Jordan isn't in.
- **1:16** It's not here.
- **1:17** It never even reached the model.

## 1:20–1:40 · scene 4

- **1:20** This is Jordan's map: everything he can open.
- **1:23** Eight hundred and sixty-seven items, each checked live with its source.
- **1:27** Ken, a contractor, sees twenty-four.
- **1:29** And there's no "plus 843 hidden": for Ken, the rest simply doesn't exist.

## 1:40–2:00 · scene 5

- **1:40** Here's the same question asked by Jordan and by Ken, side by side.
- **1:45** Jordan gets the full picture.
- **1:47** Ken gets the polite "I couldn't find that in the sources you can access", with no hint that there's anything more to find.

## 2:00–2:30 · scene 5b

- **2:00** And if you're new, there's a short guided tour that walks you through all of this in about a minute.

## 2:30–2:52 · scene 6

- **2:30** One more test.
- **2:32** Ken asks about a real, restricted incident report, then about one we made up.
- **2:37** The two answers are byte-for-byte identical.
- **2:39** We test this on four hundred pairs of questions, and there's no difference in any of them.

## 2:52–3:19 · scene 7

- **2:52** Aspire's team told us the big use case is "what can we offer this customer?".
- **2:58** Amy, an account manager, gets the approved script for a customer under review, never the case notes, and it reads exactly like the answer for a company that doesn't exist.
- **3:09** And the offer question gets a different, correct answer per market, always from the approved policy, never from the draft.

## 3:19–4:04 · scene 8

- **3:19** Permissions change all the time.
- **3:21** Here we remove Jordan from a Slack channel.
- **3:24** His very next question no longer uses that channel, even if Slack's notification about it gets lost, because we check with the source every time.
- **3:34** And when the runbook owner adds a new step, Priya's next question shows version 8, live.
- **3:40** Nothing stale.

## 4:04–5:01 · scene 9

- **4:04** Now the compliance view.
- **4:06** Maya asks in plain English: everything Jordan accessed in the payment gateway space, last thirty days.
- **4:12** She sees each question, what was considered, what was allowed or denied and why, and which model answered.
- **4:19** She can't read answers by default.
- **4:21** To see one, she has to give a reason, and that reveal is logged too.
- **4:27** The log is chained and sealed.
- **4:29** "Verify audit chain" checks every record and every stored answer.
- **4:33** And if someone edits a single line on disk, or deletes one, verification catches it.

## 5:01–5:31 · scene 10

- **5:01** People don't want another app, so the Brain also lives inside Tencent WorkBuddy.
- **5:06** Each person's connection carries their own signed token, so the model can't decide who you are.
- **5:12** And Maya can run her audit checks right from WorkBuddy too.

## 5:31–6:19 · scene 11

- **5:31** Under the hood is Walnut, the evidence engine we built before this hackathon.
- **5:36** Every value it touches carries a pointer back to where it came from, and you can follow that pointer, like here, from a fact to the exact document.
- **5:47** The Internal Brain reuses that engine as it is.

## 6:19–6:44 · scene 12

- **6:19** Two checks before the model sees anything, a grounding check after it answers, and an audit record written before anything is shown.
- **6:27** Here the answers are quoted straight from the sources; with a TokenHub key, Tencent Hy3 writes them instead, through exactly the same checks.

## 6:44–7:02 · scene 13

- **6:44** We attacked it on purpose: nearly six thousand adversarial requests, zero leaks; four hundred paired questions, zero differences; and every scenario covered by automated tests.

## 7:02–7:17 · scene 14

- **7:02** We used CodeBuddy to plan the trust boundaries, build the WorkBuddy connector, write tests and review our security, and we kept every session's log.
- **7:11** Thanks for watching!
