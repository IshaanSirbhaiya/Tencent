# Voice-over cue sheet

Play `walnut_internal_brain_demo.mp4` with `narration.srt` loaded as subtitles and read each line as it appears. Relaxed pace; pauses are fine.

## 0:00–0:20 · scene 1

- **0:00** Hi!
- **0:02** This is the Walnut Internal Brain, our entry for Aspire's FinTech challenge.
- **0:06** Ask one question across Confluence, Jira, Slack and Google Drive, and it answers only from what you are allowed to open, with every step on an audit trail.
- **0:16** We built it with Tencent CodeBuddy, and it plugs into Tencent WorkBuddy.

## 0:20–0:49 · scene 2

- **0:20** We all lose hours looking for things: the answer is in a ticket, a Slack thread, a runbook, somewhere.
- **0:27** An AI assistant should fix that.
- **0:30** But in a fintech, an assistant that can read everything can also leak everything, and showing the wrong person a fraud case can be a crime.
- **0:40** So we built it around one rule: the model never sees anything the person asking can't see.

## 0:48–1:24 · scene 3

- **0:49** Meet Jordan, a backend engineer.
- **0:51** He asks one plain question, and gets one answer, stitched together from Slack, Jira and Confluence, with every sentence linked to its source.
- **0:59** Look at the strip under the answer: only sources Jordan can open, each one re-checked with the source system just before it was used, and the whole thing logged.
- **1:11** There's also a blocker discussed in a leads-only channel Jordan isn't in.
- **1:15** It's not here.
- **1:17** It never even reached the model.

## 1:24–1:40 · scene 4

- **1:24** This is Jordan's map: everything he can open.
- **1:28** Eight hundred and sixty-seven items, each checked live with its source.
- **1:32** Ken, a contractor, sees twenty-four.
- **1:34** And there's no "plus 843 hidden": for Ken, the rest simply doesn't exist.

## 1:40–1:58 · scene 5

- **1:40** Here's the same question asked by Jordan and by Ken, side by side.
- **1:45** Jordan gets the full picture.
- **1:47** Ken gets the polite "I couldn't find that in the sources you can access", with no hint that there's anything more to find.

## 1:58–2:12 · scene 5b

- **1:58** And if you're new, there's a short guided tour that walks you through all of this in about a minute.

## 2:11–2:28 · scene 6

- **2:12** One more test.
- **2:13** Ken asks about a real, restricted incident report, then about one we made up.
- **2:19** The two answers are byte-for-byte identical.
- **2:21** We test this on four hundred pairs of questions, and there's no difference in any of them.

## 2:28–2:55 · scene 7

- **2:29** The big fintech case is "what can I tell this customer?".
- **2:33** Amy, an account manager, asks why a customer's account is restricted.
- **2:37** She gets the approved script, never the case notes, and it reads exactly like the answer for a company that doesn't exist.
- **2:45** And "what can we offer this business?"
- **2:48** gets the answer for its own market, from the approved policy, never a draft.

## 2:55–3:25 · scene 8

- **2:55** Permissions change all the time.
- **2:57** Here we remove Jordan from a Slack channel.
- **3:00** His very next question no longer uses that channel, even if Slack's notification about it gets lost, because we check with the source every time.
- **3:10** And when the runbook owner adds a new step, Priya's next question shows version 8, live.
- **3:16** Nothing stale.

## 3:24–4:08 · scene 9

- **3:25** Now the compliance view.
- **3:27** Maya asks in plain English: everything Jordan accessed in the payment gateway space, last thirty days.
- **3:33** She sees each question, what was considered, what was allowed or denied and why, and which model answered.
- **3:40** She can't read answers by default.
- **3:42** To see one, she has to give a reason, and that reveal is logged too.
- **3:48** The log is chained and sealed.
- **3:50** "Verify audit chain" checks every record and every stored answer.
- **3:54** And if someone edits a single line on disk, or deletes one, verification catches it.

## 4:08–4:31 · scene 10

- **4:08** People don't want another app, so the Brain also lives inside Tencent WorkBuddy.
- **4:13** Each connector carries its own signed token, so the AI in the middle can't decide who you are: Jordan's connector gets Jordan's answer, Ken's gets Ken's.
- **4:23** And Maya can run her audit checks right from WorkBuddy too.

## 4:31–5:07 · scene 11

- **4:31** Under the hood is Walnut, the evidence engine we built before this hackathon.
- **4:36** Every value it touches carries a pointer back to where it came from, and you can follow that pointer, like here, from a fact to the exact document.
- **4:47** The Internal Brain reuses that engine as it is.

## 5:07–5:29 · scene 12

- **5:07** Two checks before the model sees anything, a grounding check after it answers, and an audit record written before anything is shown.
- **5:16** Here the answers are quoted straight from the sources; with a TokenHub key, Tencent Hy3 writes them instead, through exactly the same checks.

## 5:29–5:40 · scene 13

- **5:29** We attacked it on purpose: nearly six thousand adversarial requests, zero leaks; four hundred paired questions, zero differences; and every scenario covered by automated tests.

## 5:39–5:55 · scene 14

- **5:40** We used CodeBuddy to plan the trust boundaries, build the WorkBuddy connector, write tests and review our security, and it found real bugs in our own code.
- **5:50** Six sessions, every transcript kept in the repository.
- **5:53** Thanks for watching!
