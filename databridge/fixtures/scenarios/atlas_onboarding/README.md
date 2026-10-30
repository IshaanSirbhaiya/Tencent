# Workflow 1 — Your first day leading Project Atlas

**Company:** the existing synthetic Company A in Walnut's demo.  
**Scenario date:** 30 October 2026, 17:00 SGT.  
**Persona:** Alex Morgan, incoming product lead.  
**Project:** Atlas, a Singapore merchant-onboarding workspace.  
**All people, conversations, project facts and research results in this pack are fictional.**

## The story

Alex has just taken over Atlas. The product brief explains the original ambition. Jira holds engineering work. Slack contains the latest blockers and decisions. A Drive roster identifies the team. Handover emails explain the transfer of ownership, while a WhatsApp export contains a small piece of coordination history.

Alex would ordinarily have to find the right channel, search old messages, identify the owners, compare dates and ask colleagues to repeat the history. In this workflow Alex asks once. Walnut retrieves the project material Alex is allowed to read and opens a cited project onboarding workspace beside the conversation.

The reveal is practical: the old pilot date is superseded, the revised date is still conditional, and two callback failures are blocking QA sign-off. Alex leaves with owners and first-week priorities.

## Exact question to use

> Hi, I'm Alex Morgan, the new product lead for Project Atlas. Bring me up to speed with an onboarding brief: purpose, status, team table and contributions, decisions, blockers, timeline and first-week checklist. Use all connected sources, cite every claim and flag outdated or missing information.

This is the canonical runnable question in `prompt.txt`. Alex must actually be signed in as `alex.product`: typing a name in the question does not change identity or permissions.

Spoken setup before asking:

> “Imagine it's my first day leading a project. I need its history, the people behind it, and what needs my attention. Instead of searching across the company, I ask Walnut.”

## What happens on screen

1. Start on the light-mode Ask page, signed in as Alex. Use the clean layout in the supplied screenshot.
2. Paste or dictate the question. For the film, show a short natural typing moment, then complete the prompt with a cut; do not spend half the workflow watching text appear.
3. Walnut retrieves across the connected project sources, applying the existing permission checks.
4. A short cited response appears. Chat shifts left and the onboarding page opens on the right.
5. Show the project purpose and current pilot status.
6. Scroll to the team table: who did what and who owns the next work.
7. Show the revised pilot decision and remaining blocker.
8. Open a citation to demonstrate the underlying passage, its source and version.
9. Finish on the first-week checklist: Alex now knows where to start.
10. Optionally ask one concise follow-up about the pilot decision or callback blocker.

This is an onboarding brief with source coverage, not a promise to reproduce every record in the company.

## Intended page hierarchy

Use **“Project Atlas · Onboarding brief”** as the page identity.

| Section | Information to communicate | Source of truth |
|---|---|---|
| Project overview | Merchant onboarding for Singapore SMEs; goal is 5 business days to 1 | Confluence overview |
| Current status | Amber; pilot conditional on QA and support readiness | Current overview, QA results |
| Team and contributions | All 12 project members, roles, completed work, current focus | Drive team roster; individual Jira tickets |
| Decisions | 6 November superseded by provisional 12 November; scope remains 10 merchants | ADR-003, approval email, Slack decision |
| Blockers | 2 remaining callback-state failures; Elena owns the fix | ATL-108; QA result |
| Timeline | Original proposal, integration, QA findings, revised date, future gates | Dated source records |
| First week | Meet owners, confirm scope, settle support ownership, attend go/no-go | Product-lead handover |
| Open questions | Budget is not recorded; daily exception owner unconfirmed | Handover, SOP draft |
| Evidence | Claim-level source controls and source previews | Authorised source records |

The existing offline renderer uses its generic Key facts, Table, Steps, Timeline, Conversations and source sections. A model-composed arrangement of custom cards is a separate mode and has not been claimed as tested here.

## Ground truth: what Walnut should discover

- Atlas is Company A's merchant-onboarding workspace for Singapore SME customers.
- Reducing median onboarding from **5 business days to 1** is a **target**, not an achieved result.
- First pilot: **10 invited Singapore merchants**, document upload, application status and human operations review.
- Automated approval, overseas onboarding and WhatsApp notifications are outside this pilot scope.
- Status as of the snapshot: **Amber**.
- The initial **6 November 2026** date is superseded.
- **12 November 2026** is a revised **provisional** pilot date, conditional on readiness.
- Initial QA found **7 callback failures**. **5 are fixed; 2 remain**.
- The latest regression run passes **118 of 120** required cases.
- Elena Park owns the remaining callback fixes, targeting **2 November**.
- Lucas Reed owns the complete regression rerun and QA sign-off, planned for **3 November**.
- Go/no-go: **3 November at 16:00 SGT**, with Alex, Lucas, Leo and Sara.
- Leo owns the support SOP. The daily exception owner remains unconfirmed.
- No approved budget figure is available in the accessible project records.

Do not collapse a target, a completed test-run task and an approved release into the same thing.

## Team and personas

The existing Company A identities and platform IDs are preserved, including Jordan Doe, Priya Raman, Leo Hartono, Sara Lindqvist and Ken Adeyemi. The scenario adds Alex and project teammates rather than renaming the old company or replacing its staff.

The generated `manifest.json` has a persona entry for each of the 12 project members, with role, completed work, current focus and communication style. `team.csv` provides the compact roster used by the app. Ken is an existing external-contractor control; the existing admin identity remains an operator, not an Atlas contributor.

## Source material built

| Material | Added to Company A |
|---|---:|
| Project/reference Confluence pages, including one unrelated-project control and one restricted note | 7 |
| Jira delivery tickets | 12 |
| Slack threads | 10 |
| Individual Slack messages | 36 |
| Drive files and imported archives | 7 |
| Original-format synthetic handover emails | 2 |
| Project members in the roster | 12 |

The Drive records include the team roster, research, QA results, support SOP, two imported emails and a WhatsApp export.

Email and WhatsApp are **imported archives in Drive** for this workflow. The demo's native connected sources are Slack, Confluence, Jira and Drive. Do not present this as a native WhatsApp or Gmail integration.

## Recording script: 90-second first workflow

Times below are relative to the start of this workflow.

| Time | Screen/action | Narration |
|---|---|---|
| 0:00–0:10 | Alex on the Ask page; optional brief cut to the scattered records | “It's my first day leading Atlas. The project already has months of context spread across documents, tickets and conversations.” |
| 0:10–0:22 | Enter the question | “I need to understand what we're building, who's involved and what needs my attention.” |
| 0:22–0:35 | Retrieval, then chat shifts left and page opens | “Walnut brings the connected project information into one workspace, with evidence attached.” |
| 0:35–0:48 | Purpose and current status | “Here's the project, its scope and where it stands today.” |
| 0:48–1:00 | Team table | “I can see who contributed, what they delivered and what each person owns next.” |
| 1:00–1:14 | Revised date, QA blocker; open a citation | “The original pilot date has changed. The new date is conditional, and two callback failures still block sign-off. I can open the source behind that.” |
| 1:14–1:25 | First-week checklist | “Now I know who to meet, which decisions to confirm and what to prioritise.” |
| 1:25–1:30 | Hold the workspace | “One question. A useful starting point for my first day.” |

Avoid claiming measured time savings or that the demo has actually replaced weeks of onboarding.

## Fit with the overall video

The user's current structure is:
1. **0:00–0:15:** a person introduces Walnut.
2. **0:15–2:15:** the previously discussed two-minute film.
3. **2:15 onward:** workflows, beginning with this onboarding example.

The 90-second workflow would occupy **2:15–3:45**, leaving **4:15** of an eight-minute total for other workflows, proof and the close. These allocations are proposed production timings, not verified event rules.

Draft opening line for the first person, about 15 seconds:

> “Your company's knowledge already exists. It's just scattered across tools and teams. We're building Walnut to connect it into one company brain—and turn that knowledge into answers and useful interfaces, with evidence you can check.”

Keep the film and workflows complementary: the film explains the idea; the workflow demonstrates a specific job. Avoid repeating the whole introduction before Alex's question.

## Motion and visual direction

Use the supplied Ask screenshot as the starting composition. Light grey background, white surfaces, restrained blue, clear type and generous spacing. Keep the question visible through the split transition. Let the page arrive as a coherent workspace; use a few purposeful reveals rather than animating every line.

Focus attention on the team, changed decision and evidence. Use slow, brief emphasis and leave reading time. No electricity, particles, glowing brains or excessive 3D.

## Run it

The runnable scenario is in:

`Tencent/databridge/fixtures/scenarios/atlas_onboarding/`

From the repository's `Tencent/` directory:

```bash
databridge/.venv/bin/python databridge/fixtures/scenarios/atlas_onboarding/run_demo.py
```

Open [Atlas demo entry](http://localhost:8115/scenario/atlas), then **Open Walnut as Alex Morgan** and paste the question. Use localhost for this separate session; the original app uses 127.0.0.1.

- Scenario web app: `http://localhost:3115`.
- Separate Brain: port 8115; mock source APIs: port 8215.
- Separate state/logs: `databridge/var/atlas-onboarding/`.
- Existing servers are not stopped and `fixtures/enterprise/world.json` is not overwritten.
- The launcher uses the existing Walnut frontend and the additive Company A world.
- Ctrl-C stops only the processes started by the launcher.
- Default mode makes **no external model calls**. It shows “Quoted from sources (no model)” and rule-based layout labels.
- `--with-configured-model` enables the existing configured model path if keys are supplied through the environment. That path was not tested or invoked for this handoff.

Rebuild the generated data with `build.py`. The default `world.json` is existing Company A plus Atlas. `atlas-only.world.json` is a smaller isolated snapshot for inspection. Rebuilding reads the existing enterprise world; keep that original file unchanged.

## Verification and limits

The scenario's `verify.py` uses the actual mock platform API implementations, source connectors, retrieval, permission gates, answer storage, audit ledger and A2UI builder. It is not a hardcoded answer fixture.

Verified offline:
- Alex's signed-in identity comes from the directory.
- Initial evidence spans Slack, Confluence, Jira and Drive.
- The onboarding request is classified as a page.
- The generated workspace contains all 12 members, a table, checklist, timeline and conversations.
- Citation previews open.
- Another employee cannot reuse Alex's answer to create a page.
- A non-member contractor receives no Atlas citations.
- The restricted security note and unrelated project are excluded.

A small integration change scopes explicit new-lead onboarding requests to the named project, preventing the self-introduction and section words from becoming unrelated retrieval subjects. It keeps the existing permission gates. The rules page can expand authorised project evidence as well as the model path.

The demo is backed by synthetic platform servers, not live enterprise accounts. The model-composed page path and final video render remain untested/not created. All creative narration and timings above are drafts.

## Package contents

- `prompt.txt`: exact user question.
- `world.json`: additive Company A demo world.
- `atlas-only.world.json`: scenario-only inspection snapshot.
- `manifest.json`: personas, source catalogue, expected claims and permission controls.
- `team.csv`: team roster.
- `sources/`: readable Confluence pages, tickets, Slack conversations, Drive files and original-format emails.
- `build.py`, `run_demo.py`, `verify.py`: generator, isolated launcher and verification.
- `verification/offline-proof.json`: actual retrieved answer, page and checks.
- `SOURCEBOOK.md`: all source text and personas in one readable document.

The downloadable data pack is not a standalone Walnut application. Its launcher relies on this repository and the onboarding integration already added here.

