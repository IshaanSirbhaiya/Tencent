# Personas and journeys: Walnut DataBridge (Internal Brain)

Team Walnut: Ishaan, Dev, Mehul, Aaruni. Source of the persona list: `databridge/apps/internal_brain_web/lib/personas.ts`.

## 0. Read this first: what these are, and what they are not

These nine people are **DESIGN PERSONAS**. We wrote them from the Aspire brief, the regulation we read, and our own experience. We did **not** interview these roles. They are assumptions we designed against.

The **TESTED** evidence is separate: five hallway testers on Saturday (see `HALLWAY_TEST_PROTOCOL.md` and `USABILITY_REPORT_TEMPLATE.md`). Do not call a persona "validated" on stage. Say "designed for", and show the test numbers only when the report is filled.

All company data is synthetic ("Company A", `fixtures/enterprise`).

## 1. The nine persona cards

Each card: role and context, a job-to-be-done line, the pain today, what the product does, and what that person is allowed to see. "Allowed to see" is taken from the `note` field in `personas.ts`.

| # | Persona (id) | Role and context | When I ___, I want ___, so I can ___ | Pain today (assumed) | What Walnut DataBridge does | Allowed to see (from personas.ts) |
|---|---|---|---|---|---|---|
| 1 | **Jordan Doe** (`jdoe`) | Backend engineer, payments engineering | When I am on a migration and the thread is scattered over Jira, Slack and Confluence, I want one cited status, so I can unblock my team without asking five people. | Searching four tools for one status | One cited answer stitched from the sources Jordan can already open | Payments engineering; member of private #db-migration |
| 2 | **Priya** (`priya.sre`) | On-call SRE | When an incident fires at 3 a.m., I want the latest runbook step with its source, so I can act without guessing which page is current. | Stale or duplicate runbooks; time pressure | Cited, current-version answer; the source passage opens beside the claim | Runbooks space, #incidents, DB migration leads |
| 3 | **Ken** (`ken.ext`) | External contractor (Slack Connect guest) | When I ask about my vendor work, I want an answer limited to my scope, so I can do my job without being given anything I should not have. | Guests either see too much or nothing useful | Answers only from #vendor-acme and the vendor share; a request outside scope gets the same refusal as a request about something that does not exist | #vendor-acme and the vendor share only |
| 4 | **Sara** (`sara.sec`) | Security | When an incident is open, I want the incident report and open CVEs, so I can contain and report accurately. | Sensitive material spread over private places | Cited answers from the SEC space, Jira security level and #sec-incident-q3 | SEC space, Jira security level, #sec-incident-q3 |
| 5 | **Maya** (`maya.compliance`) | Compliance officer | When a regulator or auditor asks, I want to see exactly what was asked, decided and released, so I can prove it. | Logs that can be edited; no per-request story | Developer mode: request journey, and a hash-chained audit with Verify; reveals are reasoned and logged | Audit console access |
| 6 | **Leo** (`leo.ops`) | Product and operations | When a customer asks about an offer, I want the current rule and price with its source, so I can reply without waiting for another team. | Pricing and policy pages disagree | Cited answers from the Product space and the "Pricing & Offers" folder | Product space, Drive "Pricing & Offers" |
| 7 | **Amy** (`amy.am`) | Account manager | When a customer asks why their account was restricted, I want the approved wording, so I can answer honestly without saying anything I must not. | Fear of a legal mistake; waiting for compliance | The approved customer script, cited, and nothing from FinCrime case files | Customer and product spaces; no FinCrime case access |
| 8 | **Rachel** (`mlro.tan`) | MLRO (FinCrime) | When a case is open, I want the findings and the filing status, so I can decide and keep to need-to-know. | Case material must stay restricted but still be findable | Cited case answer, with a classification label (for example "Internal · FinCrime") | FinCrime space, #fincrime-cases, case files; need-to-know only |
| 9 | **IT admin** (`admin.it`) | Platform simulator operator | When I run the demo platforms, I want to ask operational questions, so I can check the system. | Not a real end user | Ordinary answers; **no** audit access | Operates the demo platforms; no audit access |

Notes:
- Rachel's persona id is `mlro.tan`; the UI shows the name "Rachel".
- Card 9 is a test-and-demo role, not a customer persona. Do not present it as a user we researched.
- "Pain today" is our assumption. It becomes evidence only if a tester says it (record their words in the report).

## 2. Journey map format

Columns: Trigger, Ask, Read the answer, Verify the source, Act. Rows: user does, thinks and feels, pain today, Walnut helps, metric.

The "thinks and feels" row is a **design assumption**. It is not a measurement and must not be quoted as a finding.

## 3. Journey map: Amy (account manager)

Scenario: a customer calls about a restricted account. Amy asks: "Why has Velquarry Trading's account been restricted, and what should I tell the customer?"

| | Trigger | Ask | Read the answer | Verify the source | Act |
|---|---|---|---|---|---|
| **User does** | Customer is waiting on the phone | Types the question in the single ask box | Reads the answer on screen | Opens citation [n] to see the passage | Reads the approved wording to the customer |
| **Thinks / feels (assumed)** | Anxious: afraid of saying the wrong thing | Hopeful | Relieved if it is plain and short | Wants reassurance it is the approved text | Confident |
| **Pain today (assumed)** | Waits for compliance, or guesses | n/a | n/a | No way to tell if the script is current | Risk of an unsafe disclosure |
| **Walnut helps** | One box, no setup | The permission check runs before the model | The approved customer script, cited, and nothing from the case file | The passage opens beside the claim, checked live | The answer says nothing about a filing; the system never reveals that to Amy |
| **Metric to capture** | n/a | Time to first answer | Task success (unaided / assisted / failed) | Does the tester find the passage? | The tester's own words on trust, verbatim only |

Design point to say: Amy and Rachel ask the same question and get different, correct answers. Telling Amy that a suspicious-transaction report exists is the one thing the system must never do.

## 4. Journey map: Maya (compliance officer)

Scenario: after an answer was given to Rachel, Maya inspects that one request and checks the log is intact. Routes: `/dev/journey/<req>` then `/dev/audit` (Developer mode).

| | Trigger | Ask | Read the answer | Verify the source | Act |
|---|---|---|---|---|---|
| **User does** | An auditor asks "show me what happened" | Opens the request's journey by its id | Reads the stages, the model name and the sealed records | Presses Verify on the audit chain | Signs off, or escalates a break |
| **Thinks / feels (assumed)** | Under scrutiny | Wants one place, not five tools | Wants facts, not reassurance | Needs to see a break if there is one | Calm if intact, alert if broken |
| **Pain today (assumed)** | Logs can be edited unseen | Reconstructing one request takes a long time (not measured) | n/a | No proof the log was not altered | n/a |
| **Walnut helps** | The journey is a link away | A waterfall with real timings per stage (compliance role only) | Records show sequence number and hash | Verify says intact, or names the entry where the chain breaks | The tamper demo runs on a **copy**; the real chain is never edited |
| **Metric to capture** | n/a | Find the request: time, assisted? | Can the tester explain what the waterfall shows? | Can the tester say what a red Verify means? | Tester's own words, verbatim only |

Design point: the full millisecond waterfall appears only to a compliance officer (D-16, D-05). A normal user gets only a public-safe receipt.

## 5. Journey map: Ken (external contractor)

Scenario: Ken asks about a customer outside his scope, then about one that does not exist. Both must look identical.

| | Trigger | Ask | Read the answer | Verify the source | Act |
|---|---|---|---|---|---|
| **User does** | Curious or mistaken: asks about Velquarry Trading | Asks the same question about "Bayfront Spice Traders" | Reads a plain refusal | Looks for a source; there is none to open | Goes back to his vendor work, or asks his sponsor for access |
| **Thinks / feels (assumed)** | Curious | Testing the edge | Not confused; understands it is outside his scope | n/a | Knows what to do next |
| **Pain today (assumed)** | Guests see too much, or an error reveals that something exists | n/a | A different message for "no access" and "not found" leaks existence | n/a | n/a |
| **Walnut helps** | Ken's view is limited to #vendor-acme and the vendor share | The same gates as everyone | One response shape: a restricted subject and a made-up subject get the same refusal | No source is shown that Ken cannot open | The refusal says plainly that no answer is available to him and what he can do next |
| **Metric to capture** | n/a | Task: ask a restricted question | Can the tester say, in their words, why there was no answer and what they would do? | n/a | Success if the tester names a sensible next step |

Guardrail (D-07): the refusal must **not** say "something exists but is hidden", and the UI must **not** show how many sources were hidden. Do not add any such line to the journey, the tour or the tester briefing.

## 6. Where these appear

- Stage: Amy and Rachel (hook), Ken (identical refusals), Maya (journey and audit), Jordan (WorkBuddy beat). Priya, Sara, Leo and the IT admin appear only in the persona grid.
- Appendix and Q&A: this file.
- In-app persona cards (if built): check the build before promising them.
