# Demo video script (target 7:15, hard limit 8:00)

Record at 1920×1080. Keep the browser at 110% zoom and the sign-in persona visible.

Before recording, run a demo reset: sign in as the IT admin and press **Reset demo**.

If TokenHub is configured, the answer badge reads "Tencent Hunyuan hy3". Otherwise it reads
"Deterministic", and you say so honestly.

| Time | Scene | On screen | Say (plain words) |
|---|---|---|---|
| 0:00–0:20 | Title | Cover + "FinTech Track — Aspire: The Internal Brain" | "We built the Internal Brain for Aspire's FinTech challenge: one place to ask questions across Confluence, Jira, Slack and Drive, where you only ever see what you're allowed to see, and every access is audited." |
| 0:20–1:00 | The problem | Sign-in page with personas; "Synthetic demo data — Company A" | "It's 3 AM. Jordan, a backend engineer, is fighting a payments incident. The answer is spread across a Jira ticket, a Slack thread and a Confluence runbook. Aspire told us the bigger use case is ops asking 'what can we offer this customer?'. The catch, for a regulated fintech: an AI that can read everything can leak everything." |
| 1:00–1:45 | **S1: unified answer** | As jdoe: ask the DB-migration question. Point at citations: Slack, Jira, Confluence, each with "Access re-checked". | "One answer, cited from three platforms. Notice what's missing: the leads-only blocker in a private channel Jordan isn't in. It never reached the model. We check every passage with the source system before the model sees it." |
| 1:45–2:15 | **Ops: what can we offer** | As amy.am or leo.ops: Singapore SME question, then Indonesian SME question | "Same question, two markets, two different cited answers, each from the approved policy. The draft v4 fee schedule with different numbers is never cited." |
| 2:15–2:50 | **S3: the negative case** | As ken.ext (contractor): "Show me the security incident report from the Q3 breach." Then the Q7 breach. | "A contractor asks about a real, restricted report, then a report that doesn't exist. The answers are byte-for-byte identical, so there is no hint the first one exists. We test this on 400 paired queries." |
| 2:50–3:20 | **FinCrime / tipping-off** | As amy.am: the Velquarry restricted-account question, then the made-up Bayfront Spice question | "In fintech, revealing that a customer is under a suspicious-transaction review is a criminal offence. The account manager gets the approved customer script, never the case notes, and the answer is identical for a made-up company." |
| 3:20–4:00 | **S4: live revocation** | As IT admin: "Remove jdoe from #db-migration". Switch to jdoe and re-ask. | "We remove Jordan from the channel in Slack. His next question no longer uses that thread. Even if the webhook is lost, the live check at the source denies it." |
| 4:00–4:30 | **S2: freshness** | As IT admin: "Edit failover runbook (v7 → v8)". As priya.sre, ask for the latest runbook; show "Version 8 / Live re-read". | "The runbook owner adds step 4b. Priya's next question shows version 8, live. A changed document is never served stale." |
| 4:30–5:30 | **S5: audit and tamper** | As maya.compliance: type "Show me everything jdoe accessed related to the payment-gateway space in the last 30 days" and show the filter chips, rows, allow/deny and model. Reveal one answer with a reason (logged). Press **Verify audit chain** (OK). Then run `python scripts/tamper_demo.py` in a terminal and show the detection. | "Compliance can reconstruct who asked what, what was considered, what was denied and why, and which model answered, without reading content unless they log a reason. The ledger is hash-chained and sealed; edit or delete one line and verification fails." |
| 5:30–6:00 | **WorkBuddy** | WorkBuddy side by side: "IB as jdoe" vs "IB as ken.ext", same question, different answers; then the compliance assistant | "Employees can ask from Tencent WorkBuddy. The connector carries each person's own token. The model can never choose who you are." |
| 6:00–6:30 | Architecture | `docs/tencent/ARCHITECTURE.md` diagram plus the trust-boundary diagram | "Two gates before the model, a grounding wall after it, a write-ahead ledger around it. Built on our Walnut evidence engine, unchanged, with Tencent Hunyuan for reasoning." |
| 6:30–6:55 | Numbers and Responsible AI | `RESULTS.md` headline | "Zero leaks in nearly 6,000 adversarial requests, zero differences across 400 paired queries, 61 of 61 scenario tests. Card numbers are masked before indexing. And we publish what we don't defend against." |
| 6:55–7:15 | Build reflection | CodeBuddy screenshots | "We planned the trust boundaries in CodeBuddy Plan mode, built the WorkBuddy connector and the model layer in CodeBuddy, and used it for a security review. Tip: give CodeBuddy whole, bounded slices with their tests." |

Fallback: record each scene separately (from Wed night) and stitch them together. Never fake a
result; if a step fails on camera, re-record that scene.
