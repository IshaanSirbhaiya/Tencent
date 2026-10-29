# Pilot plan: 10 weeks with one Aspire team

**One line for stage:** "Ten-week pilot. The first two weeks after setup run in shadow mode: our leak test on
Aspire's real permissions, before a single person uses it."

This is a proposal. Nothing here is agreed with Aspire yet.

## 1. Shape

- **One team, one market, read-only.** Suggested: Customer Success or Operations, Singapore first.
- **Two sources.** Suggested: Confluence, and Slack as an internal app in Aspire's own workspace (federated;
  see [SCALE_ARCHITECTURE.md](SCALE_ARCHITECTURE.md) §1).
- **Hosting:** Tencent Cloud Singapore region, single tenant.
- **Data:** Aspire's real permissions and documents replace the synthetic Company A. Nothing is written back
  to any source.

## 2. Ten weeks

| Weeks | Step | Exit criterion |
|---|---|---|
| 0-2 | Security review (TRM and outsourcing questionnaire), data processing agreement, choose the two sources, SSO + SCIM, Singapore tenant up | Aspire security sign-off |
| 2-4 | **Shadow mode, no users.** Ingest, compile ACLs, run the leak harness (`python -m evals.internal_brain.run`) and the differential test on Aspire's real permissions | **0 leaks**; Gate 2 p95 measured; unmapped identities listed and fixed |
| 4-8 | Live with 30-50 people in one team, in the web app and in WorkBuddy | Weekly report: questions, answer acceptance, citation clicks, refusals, Gate 2 p95, cost per answer |
| 8-10 | Read-out to Aspire's AI lead and compliance: time saved against baseline, an audit pull ("who saw document X"), go or no-go for a second team and market | Break-even shown at ≥7 min/person/week, **or stop** |

## 3. Success metrics, set before week 4

| Metric | Target | How it is measured |
|---|---|---|
| Leaks on Aspire's permissions | **0** | Leak harness in shadow mode, then weekly |
| Median time-to-answer | Lower than baseline | 20 sampled tickets before go-live, then the same question types |
| Answers accepted | Aspire picks the threshold | "Helpful" feedback and citation clicks |
| Tamper verification | Passes daily | Verify chain on the ledger |
| p95 answer latency | Agreed with users in week 2 | Journey timings |
| Model cost per answer | Reported, not targeted | TokenHub usage ([COST_PER_ANSWER.md](COST_PER_ANSWER.md) §5) |

## 4. What we need from Aspire

| Role | Time | Why |
|---|---|---|
| Executive sponsor (AI lead) | 1 h a fortnight | Decisions and the read-out |
| IT admin | about 2 days in weeks 0-2 | SSO, SCIM, connector service accounts and app install |
| Compliance reviewer | 2 h a week | Reviews audit pulls; signs off the shadow-mode results |
| Team lead in the pilot team | 30 min a week | Collects feedback; picks the baseline tickets |

## 5. Stop rules

- Any leak in shadow mode stops go-live until it is fixed and the harness is re-run clean.
- Any leak after go-live: global kill switch (`IB_FORCE_OFFLINE=1` stops model use; the service can be
  stopped entirely), then root cause, then a re-run.
- Break-even not reached by week 10: stop, publish what we learned.

## 6. Certification during the pilot

See [CERTIFICATION_PATH.md](CERTIFICATION_PATH.md). In the pilot: third-party pen test and
AI Verify / Project Moonshot baseline tests.
