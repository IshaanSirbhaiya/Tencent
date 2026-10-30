# Walnut DataBridge

**Answers you can prove.**

*Company knowledge, connected. Intelligence you can put to work, with answers you can prove.*

Team Walnut · Tencent Cloud AI CAN DO IT Hackathon Singapore 2026 · FinTech track (Aspire: "The Internal Brain")

Code: https://github.com/IshaanSirbhaiya/Tencent/tree/main

| | |
|---|---|
| **0 leaks** | in 7,374 adversarial requests, judged by an independent checker that knew what each person must not see |
| **0 differences** | between a real secret and a made-up subject, across 400 paired questions |
| **0 hard failures** | in 1,152 prompt-injection attempts planted across all four tools |

---

## 1. The problem

Every company already has the knowledge it needs. Nobody can see all of it at once. It is scattered across
Slack, Jira, Confluence and shared drives.

- **25%** of a team's time goes to searching for answers (Atlassian, *State of Teams 2025*).
- **5.3 hours** a week per person are lost waiting on colleagues or recreating work (Panopto with YouGov).
- **42%** of company know-how is held only by individuals, and leaves with them (same study).

AI should fix this, but a regulated company cannot simply switch it on. **An assistant that can read
everything will also say everything.** Picture a Singapore payments company. An account manager asks why a
customer's account is frozen, and the assistant summarises the financial-crime investigation. Telling the
customer is **tipping-off**, a criminal offence. So fintechs either keep AI away from their real knowledge,
or take a risk they cannot audit.

## 2. What Walnut DataBridge is

A company brain with an intelligence layer on top, built for regulated fintechs.

1. **The company brain.** Connects Confluence, Jira, Slack and Google Drive into one record of the people,
   projects, customers and decisions behind the work.
2. **The intelligence layer.** People ask in their own words and get cited answers, pages built for their
   question, and drafts they can act on.
3. **Governance throughout.** Each person can use only what they are allowed to open, every claim links to
   its source, and every step goes into a sealed audit log that compliance can verify.

## 3. How it works: permissions first, then the model, then proof

1. **Connect.** Reads the four tools through their real API shapes, stores each document with its
   permissions, and masks card numbers, NRICs and IBANs before anything is saved.
2. **Check access, twice.** Search runs only over documents the person may open (role-based access from
   the source). Then, before any passage is used, Walnut asks the source **live** whether the person still
   has access. Any error or timeout counts as "no".
3. **Answer, with proof.** The answer is built only from passages that passed both checks. A grounding wall
   drops any sentence it cannot tie to a cited passage. Every claim carries a source chip that opens the
   original, with its version and when access was last checked.
4. **Build the right view.** A broad request opens a page beside the chat (status, team, decisions,
   blockers, timeline, next steps), built only from what that person may see.
5. **Act, with a person in control.** Walnut drafts replies from approved or cited wording and proposes
   follow-up tickets. Nothing is sent or created until a person confirms.
6. **Prove it.** The question, the access decision and the answer are each written to a sealed,
   hash-chained ledger **before** the step happens. If the log cannot be written, the request stops.

**Restricted looks exactly like non-existent.** Ask about a secret case you cannot see, or about a company
that does not exist, and you get the same reply. Walnut hides a secret, and it also hides that the secret
exists.

## 4. Three workflows, one brain

**1. "Day one on Atlas" (onboarding).** Alex has just taken over Project Atlas: 12 people and months of
history across the tools. Alex asks once, and Walnut opens a cited onboarding page: purpose, status, the
team and who did what, decisions, blockers, a timeline and a first-week checklist. It also flags what
changed: the original pilot date is superseded and the new date is still provisional.

**2. "The Call" (governance).** A customer calls account manager Amy because their account is frozen.
Financial crime is investigating, and Amy must not know. Amy gets only the approved customer script and a
draft reply, with no case data. Rachel, the financial-crime lead, asks the exact same question and gets the
full case. Same brain, same question, two different answers, because they are two different people.

**3. "The regulator is asking" (audit).** Maya in compliance asks the audit log in plain English, for
example everything one person opened in a space over the last 30 days. She presses **Verify** to check the
hash-chained ledger end to end, then runs the tamper demo on a copy: every edit is caught, and the real log
is never touched.

## 5. What makes it different

- **Permissions come before the AI, not after.** A model never sees a passage the person could not open, so
  it has nothing to leak.
- **Every claim is checkable.** It links to the exact passage, version and access check behind it.
- **The interface respects permissions too.** Pages, graphs and dashboards are built only from what that
  person may see.
- **Auditable by design.** The log is written before each step, sealed and tamper-evident.
- **Runs inside the company's own environment.** Without a model, Walnut still answers by quoting the cited
  sources, with the same checks.

## 6. Proof

All numbers come from the repository and were re-run on the finals code on 30 October 2026.

| Check | Result |
|---|---|
| Leak evaluation | **0 leaks in 7,374** adversarial requests (3,687 probes × 2 answer paths) |
| "Restricted" vs "does not exist" | **0 differences** across 400 paired questions |
| Prompt injection planted in all four tools | **0 hard failures** in 1,152 attempts |
| Tamper attacks on a copy of the ledger | **6 of 6** caught |
| Backend tests | **1,274 passed**, 0 failed (clean checkout, 30 Oct) |
| Browser end-to-end tests (with accessibility checks) | **185 passed**, 0 failed |
| WorkBuddy connector, driven by the official MCP client | **33 of 33** checks pass |

Sources: `databridge/evals/internal_brain/RESULTS.md`, `README.md`, `docs/finals/TEST_REPORT.md`.

**All data is synthetic.** "Company A" is a fictional Singapore payments fintech with 34 people and about
1,200 documents across the four tools, with 72 planted facts whose access rules are known in advance. The
tools are mock servers that use the real APIs' shapes. No real company or personal data is used.

## 7. Built with Tencent

- **CodeBuddy:** nine recorded build sessions. CodeBuddy built both WorkBuddy MCP connectors and the
  WorkBuddy skill, and ran two security reviews that found real bugs, fixed with tests.
- **WorkBuddy:** staff ask from inside WorkBuddy through our MCP connectors. The person is the signed
  token, never a name the agent picks; the same two access checks apply, and every question is logged.
- **Hunyuan (Hy3) through TokenHub:** wired in as the primary model. It is not live yet; it switches on
  once TokenHub access is activated. Until then, answers are quoted from the cited sources.
- **Tencent Cloud Lighthouse:** a deploy kit is ready; there is no live instance yet.
- **Miora:** made our cover.

## 8. How it meets the judging criteria

| Criterion | Where to see it |
|---|---|
| Impact and relevance | Aspire's own question: why is a customer's account restricted? Tipping-off avoided by design |
| Human-centred design | Persona sign-in, plain-language answers, a sources sheet, guided tours |
| AI interaction | Cited answers inside two access gates; pages built per question; asked from WorkBuddy too |
| Technical execution | Request journey with per-stage timings; 1,274 + 185 tests; CI |
| Feasibility | Runs on synthetic data with real API shapes; deploy kit for Lighthouse; pilot plan |
| Demo and storytelling | Three workflows, one story: onboarding, the call, the regulator |
| Innovation | Restricted looks exactly like non-existent; live tamper demo on a copy |
| UX and accessibility | Text size, reduced motion, high contrast, light and dark themes, English and 简体中文, voice dictation |
| Responsible AI | Filter before the model, identical refusals, write-ahead sealed log, human confirms every action |

## 9. The value

For a 600-person fintech, our base-case projection is about **US$1.3M a year** in time saved (range
US$0.44M to US$4.1M; formula in `docs/finals/rubric/impact_feasibility/ROI_MODEL.md`). This is a
projection, not a measurement. In a pilot we would measure one real workflow against the customer's own
baseline.

## 10. The team

Team Walnut, backed by BSF · SUTD VIE.

- **Dev** (CS and Business, NTU): AI and automation at Hitachi, Singlife and Bajaj Finserv; AI/ML engineer at Neurowyzr.
- **Mehul** (CS, NTU): production AI agent at Changi's AI/ML Lab; multi-agent research at A*STAR.
- **Ishaan** (CSD, SUTD): industrial cybersecurity research at SUTD iTrust; full-stack systems at Bajaj Finance.
- **Aaruni** (SUTD): research and design at SEC, HighSpan and WGS.

## In short

Walnut DataBridge is a company brain for regulated fintechs. It connects Confluence, Jira, Slack and Google
Drive and lets staff ask in their own words. Answers, and pages built for each question, use only what that
person may open, checked again live at the source. Every claim is cited and every step goes into a
tamper-evident log. A new project lead gets a cited onboarding page; an account manager gets the approved
script while the financial-crime lead gets the full case; compliance can ask the log in plain English and
prove it was never edited. Result: 0 leaks in 7,374 adversarial requests. Built with CodeBuddy, used inside
WorkBuddy, with Hunyuan wired in through TokenHub.
