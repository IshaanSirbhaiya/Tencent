"""Content pools for the Pandan Ledger synthetic estate (fictional fintech, pandanledger.example).

Everything here is invented. Text is assembled from these pools by ``generate.py`` with a seeded
``random.Random``; nothing reads the wall clock. Two rules the pools obey so the planted scenario
facts stay unambiguous:

* no pool sentence mentions a breach, a canary token (``CNRY-``) or a "Q7" anything;
* no pool sentence repeats a planted sentinel (those live only in ``generate.py``).
"""
from __future__ import annotations

# --------------------------------------------------------------------------------------------
# Areas: the nouns a filler sentence is built from. Each Confluence space, Jira project, Slack
# channel and Drive folder draws from one or more areas.
# --------------------------------------------------------------------------------------------
AREAS: dict[str, dict[str, list[str]]] = {
    "payments": {
        "component": ["card-auth service", "refund worker", "settlement batch job", "PSP router",
                      "payout scheduler", "chargeback service", "ledger writer", "3DS2 challenge flow",
                      "webhook dispatcher", "FX quote cache", "merchant onboarding API",
                      "NusaPay adapter", "OrchidCard network gateway", "idempotency store"],
        "concern": ["idempotency key handling", "retry jitter", "partial refund rounding",
                    "settlement cut-off times", "currency minor units", "duplicate webhook delivery",
                    "authorisation reversal", "timeout budgets", "reconciliation mismatches",
                    "merchant risk scoring", "payout batching", "error code mapping"],
        "symptom": ["a spike in 502s", "elevated p99 latency", "duplicate refund callbacks",
                    "stuck payouts in PENDING", "a burst of soft declines", "reconciliation drift",
                    "queue depth climbing", "intermittent TLS handshake failures"],
        "repo": ["payments-core", "psp-adapters", "ledger", "payouts", "merchant-api"],
        "metric": ["authorisation success rate", "refund latency", "settlement lag",
                   "webhook delivery rate", "decline ratio"],
        "legacy": ["PHP checkout", "cron-based settlement", "v1 refund API", "monolith ledger tables"],
    },
    "sre": {
        "component": ["Kubernetes node pool", "Grafana alerting", "PagerDuty rotation", "ingress tier",
                      "Postgres primary", "Redis cluster", "Kafka brokers", "CI runners",
                      "Terraform state", "log pipeline", "service mesh sidecars", "backup jobs"],
        "concern": ["alert fatigue", "capacity headroom", "pod disruption budgets",
                    "certificate renewal", "on-call handover", "SLO burn-rate alerts",
                    "disk pressure", "autoscaling thresholds", "backup restore drills",
                    "noisy neighbours", "runbook coverage"],
        "symptom": ["OOMKilled pods", "a flapping health check", "disk usage above 85%",
                    "consumer lag on the events topic", "slow DNS resolution",
                    "a page that auto-resolved after four minutes"],
        "repo": ["infra-live", "helm-charts", "terraform-modules", "runbooks", "alert-rules"],
        "metric": ["error budget burn", "MTTR", "pages per shift", "CPU saturation",
                   "replication lag"],
        "legacy": ["hand-rolled Nagios checks", "the old bastion host", "VM-based workers"],
    },
    "auth": {
        "component": ["login service", "session store", "MFA enrolment flow", "OAuth consent screen",
                      "API key service", "SSO bridge", "password reset flow", "admin console auth"],
        "concern": ["session TTLs", "token revocation", "MFA recovery codes", "rate limiting",
                    "audit events for logins", "SAML metadata rotation", "device binding",
                    "scope design"],
        "symptom": ["users logged out unexpectedly", "SSO redirects looping",
                    "MFA push notifications arriving late", "429s on the login endpoint"],
        "repo": ["auth-service", "identity-sdk", "admin-console"],
        "metric": ["login success rate", "MFA adoption", "token refresh errors"],
        "legacy": ["shared session cookies", "the monolith login controller"],
    },
    "security": {
        "component": ["WAF rules", "secrets manager", "dependency scanner", "SIEM pipeline",
                      "pen test tracker", "endpoint agents", "vulnerability backlog",
                      "access review tooling"],
        "concern": ["secret rotation", "least-privilege IAM", "dependency CVEs",
                    "phishing simulations", "access reviews", "logging coverage",
                    "third-party risk"],
        "symptom": ["a high-severity CVE in a transitive dependency", "an expired service credential",
                    "a misconfigured bucket policy flagged by the scanner"],
        "repo": ["sec-tooling", "waf-config", "iam-policies"],
        "metric": ["mean time to patch", "open criticals", "access review completion"],
        "legacy": ["long-lived IAM users", "static API keys"],
    },
    "product": {
        "component": ["SME onboarding funnel", "invoicing module", "corporate cards", "expense app",
                      "multi-entity dashboard", "pricing page", "referral programme",
                      "accounting integrations", "bill pay", "mobile app"],
        "concern": ["activation rate", "pricing tiers", "plan limits", "feature flags",
                    "customer interviews", "churn reasons", "localisation", "market launch checklist"],
        "symptom": ["a drop in week-one activation", "confusing plan names in user testing",
                    "support tickets about card limits"],
        "repo": ["web-app", "mobile-app", "pricing-config"],
        "metric": ["activation rate", "net revenue retention", "time to first transaction",
                   "NPS"],
        "legacy": ["the old onboarding wizard", "spreadsheet-based pricing"],
    },
    "compliance": {
        "component": ["KYC refresh programme", "transaction monitoring rules", "policy library",
                      "training tracker", "outsourcing register", "complaints log",
                      "data retention schedule", "record-keeping controls"],
        "concern": ["PDPA obligations", "technology risk management guidelines",
                    "sanctions screening", "customer due diligence", "audit evidence",
                    "board reporting", "vendor due diligence"],
        "symptom": ["an overdue policy attestation", "a screening alert backlog",
                    "missing evidence for a control test"],
        "repo": ["compliance-evidence", "policy-library"],
        "metric": ["alerts cleared within SLA", "training completion", "overdue reviews"],
        "legacy": ["email-based approvals", "the shared compliance spreadsheet"],
    },
    "data": {
        "component": ["Postgres 11 cluster", "logical replication slots", "read replicas",
                      "partitioned ledger tables", "backfill job", "dual-write shim",
                      "schema migration tool", "analytics warehouse"],
        "concern": ["vacuum tuning", "connection pooling", "index bloat", "long transactions",
                    "partition pruning", "replication slot retention", "schema drift"],
        "symptom": ["autovacuum falling behind", "connections maxing out at 500",
                    "a long-running transaction holding locks"],
        "repo": ["db-migrations", "ledger", "data-platform"],
        "metric": ["replication lag", "query p95", "dead tuple ratio"],
        "legacy": ["the self-managed Postgres 11 cluster", "nightly pg_dump backups"],
    },
    "general": {
        "component": ["all-hands", "office move", "hiring plan", "quarterly planning",
                      "engineering ladder", "lunch-and-learn", "onboarding buddy programme",
                      "team offsite", "benefits portal"],
        "concern": ["meeting-free Wednesdays", "travel policy", "laptop refresh",
                    "interview loops", "OKR drafts", "holiday coverage"],
        "symptom": ["a clash with the public holiday", "the room booking system double-booking"],
        "repo": ["handbook"],
        "metric": ["attendance", "survey response rate"],
        "legacy": ["the old wiki", "paper expense forms"],
    },
}

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
ENVS = ["staging", "sandbox", "prod-sg", "prod-id", "perf"]

# --------------------------------------------------------------------------------------------
# Long-form sentences (Confluence pages, Drive docs, Jira descriptions).
# --------------------------------------------------------------------------------------------
DOC_SENTENCES = [
    "The {component} handles roughly {n} requests per second at peak, with p99 latency near {ms} ms.",
    "We saw {symptom} on the {component} after the {day} deploy, and rolling back resolved it.",
    "{person} owns the {component}; escalate through the {team} on-call rotation if it pages.",
    "Open question: should the {component} own {concern}, or should that move to the {component2}?",
    "Next step is a {metric} dashboard for the {component}, alerting at {pct}% of the error budget.",
    "The {component} depends on the {component2} for {concern}, so a timeout there cascades in about {small} seconds.",
    "Configuration lives in the {repo} repo under deploy/{env}/values.yaml.",
    "Test coverage for the {component} sits at {pct}%, and the gap is mostly in {concern} paths.",
    "Rollout plan: canary at 5% in {env} for {small} hours, then 25%, then everyone.",
    "The {concern} logic was rewritten this year to remove the dependency on {legacy}.",
    "We agreed to keep {concern} out of scope for this iteration and revisit it after the next planning cycle.",
    "Known limitation: the {component} does not yet emit structured logs for {concern}.",
    "{person} measured {metric} at {pct}% over the last two weeks, which is within target.",
    "Before changing the {component}, check the dashboards for {metric} and post in the team channel.",
    "The main risk is {concern}; mitigation is a feature flag that can be flipped without a deploy.",
    "Dependencies: the {component2} team needs two days' notice before any schema change.",
    "The decision was reviewed by {person} and {person2} and recorded in the team's weekly notes.",
    "For {env}, the {component} runs {small} replicas with a pod disruption budget of one.",
    "Alerts for the {component} route to the {team} channel during business hours and page out of hours.",
    "Historical context: {legacy} was retired in stages, and a few call sites still need cleanup.",
    "Acceptance criteria: {metric} does not regress, and {concern} is covered by an integration test.",
    "We will review {metric} again in {small} weeks and decide whether to extend the change to {env}.",
]

DOC_HEADINGS = ["Overview", "Context", "Design", "Rollout", "Risks", "Open questions", "Ownership",
                "Monitoring", "Decision", "Background", "Next steps"]

PAGE_TITLES = {
    "payments": ["{Component} design notes", "{Component} runbook", "{Concern}: proposal",
                 "{Component} integration guide", "{Concern} in the {component}", "{Component} FAQ",
                 "{Component} v{small} rollout plan", "Retro: {component} {symptom}"],
    "sre": ["{Component} runbook", "{Concern} playbook", "{Component} upgrade checklist",
            "On-call guide: {component}", "{Concern} review", "Capacity plan: {component}"],
    "auth": ["{Component} design", "{Concern} decision record", "{Component} threat model notes",
             "{Component} migration plan"],
    "security": ["{Concern} standard", "{Component} operating procedure", "{Concern} review notes",
                 "{Component} configuration baseline"],
    "product": ["PRD: {component}", "{Component} launch checklist", "{Concern} research summary",
                "{Component} pricing notes", "{Concern} experiment results"],
    "compliance": ["{Concern} policy", "{Component} procedure", "{Concern} control test notes",
                   "{Component} annual review"],
    "data": ["{Component} tuning notes", "{Concern} investigation", "{Component} operating guide"],
    "general": ["{Component} notes", "{Concern} guidelines"],
}

# --------------------------------------------------------------------------------------------
# Jira
# --------------------------------------------------------------------------------------------
ISSUE_SUMMARIES = ["{Verb} {concern} in the {component}", "{Component}: {symptom}",
                   "{Verb} {metric} alerting for the {component}", "{Verb} {component} docs",
                   "{Component} returns errors under load in {env}",
                   "{Verb} {legacy} call sites"]
VERBS = ["Add", "Fix", "Investigate", "Refactor", "Harden", "Document", "Remove", "Migrate",
         "Speed up", "Clean up"]
STATUSES = ["To Do", "In Progress", "In Review", "Done", "Done", "Done", "Blocked"]

# --------------------------------------------------------------------------------------------
# Slack chatter
# --------------------------------------------------------------------------------------------
SLACK_OPENERS = [
    "anyone else seeing {symptom} on the {component}?",
    "heads up: deploying the {component} to {env} in 10 minutes",
    "PR is up for {concern} in {repo}, would appreciate a review today",
    "quick poll: should we move the {concern} sync to {day}?",
    "{metric} dipped this morning, looking into it",
    "is there a runbook for the {component}? can't find it on the wiki",
    "reminder: {concern} review is {day} at 3pm",
    "I opened {key} to track {concern}",
    "who owns the {component} these days?",
    "FYI {legacy} is finally gone from {env}",
    "can someone sanity-check the {component} config before I merge?",
    "post-deploy check: {metric} looks normal so far",
]
SLACK_REPLIES = [
    "yep, started around {hhmm}", "looking now", "I'll take it after standup",
    "lgtm, merging once CI is green", "rolled back, error rate is back to baseline",
    "{person} knows that area best", "added a note to {key}", "thanks!",
    "can we pair on this tomorrow?", "I think it's the {concern} change from last week",
    "same here, only in {env}", "ack", "dashboards look fine on my side",
    "let's discuss in the {day} sync", "resolved, it was {symptom}",
    "+1, would be good to document it", "on it", "done, see the thread in the team channel",
]

# --------------------------------------------------------------------------------------------
# Drive
# --------------------------------------------------------------------------------------------
FILE_TITLES = ["{Component} capacity plan", "{Concern} notes", "{Component} meeting minutes {small}",
               "{Concern} working draft", "{Component} metrics export", "{Component} review deck notes"]

AUDIT_TOPICS_PG = [
    "How do we handle idempotency keys in the refund worker?",
    "What is the webhook retry policy for the payment gateway?",
    "Where is the settlement batch design doc?",
    "What are the timeout budgets for card authorisation?",
    "How does the PSP router pick NusaPay versus OrchidCard?",
    "What's the rollout plan for the payout scheduler?",
    "Who owns the chargeback service?",
    "What changed in the 3DS2 challenge flow this quarter?",
    "What's the current schedule for settlement key rotation?",
    "Summarise the payment gateway ADRs from the last month.",
    "How do we reconcile ledger writer mismatches?",
    "What does the DB Migration Plan say about the ledger shards?",
]
AUDIT_TOPICS_OTHER = {
    "priya.sre": ["What's the latest runbook for the payment-service incident?",
                  "How do we drain a Kubernetes node pool safely?",
                  "What are the current SLO burn-rate alert thresholds?",
                  "Any blockers on the database migration this week?"],
    "sara.sec": ["What is the status of the Q3 incident containment?",
                 "Which dependency CVEs are still open?",
                 "When is the next secret rotation due?"],
    "leo.ops": ["Can we offer multi-currency accounts to an Indonesian SME customer?",
                "What are the pricing tiers for corporate cards?",
                "What did customers say about plan limits?"],
    "ken.ext": ["When will the OCR model update reach production?",
                "What is the support SLA for the KYC OCR service?"],
    "maya.compliance": ["When is the technology risk audit evidence pack due?",
                        "Where is the data retention schedule?"],
    "admin.it": ["Who is on the IT on-call rotation this week?"],
}

# --------------------------------------------------------------------------------------------
# Non-engineering areas get their own sentence pools (an engineering rollout sentence in a
# compliance policy reads as generated filler).
# --------------------------------------------------------------------------------------------
DOC_SENTENCES_BY_AREA = {
    "compliance": [
        "This procedure applies to every business unit that handles {concern} for customers in Singapore and Indonesia.",
        "{person} owns the {component} and reports status to the risk committee each quarter.",
        "Control owners must upload evidence for {concern} to the Compliance evidence folder before the review date.",
        "The {component} was last reviewed in {day}'s committee session and approved with two minor comments.",
        "Exceptions to this policy need written approval from the head of compliance and are logged in the {component}.",
        "We track {metric} monthly; the current figure is {pct}% against a target of 95%.",
        "Open item: {symptom}, which {person2} is following up with the control owner.",
        "Records relating to {concern} are kept for five years after the end of the customer relationship.",
        "Staff complete refresher training on {concern} every year; completion is tracked in the training tracker.",
        "Any departure from this procedure is escalated to {person} within one business day.",
        "The second line of defence samples {small} cases per month to test {concern}.",
        "This document replaces {legacy} as the system of record for approvals.",
    ],
    "product": [
        "The goal for the {component} this quarter is to lift {metric} by {small} points.",
        "Customer interviews ({small} SMEs in Singapore and Indonesia) pointed to {concern} as the main friction.",
        "{person} is the product owner for the {component}; design reviews happen on {day}s.",
        "We saw {symptom}, so the next experiment changes the copy on the plan comparison table.",
        "Launch checklist: legal sign-off, support macros, help-centre article, and a feature flag per market.",
        "Pricing for the {component} stays unchanged until the annual review; promotions need finance approval.",
        "Out of scope for this release: {concern}, which needs more research.",
        "Success metric: {metric}, measured four weeks after launch against the control group.",
        "The {component} replaces {legacy}, which will be switched off after customers are migrated.",
        "Feedback from the sales team: SMEs ask most often about {concern}.",
        "{person2} will share the experiment readout in the product review on {day}.",
    ],
    "general": [
        "The {component} is coordinated by {person}; questions go to the people ops channel.",
        "Please read the {concern} section before booking anything for next quarter.",
        "We moved the {component} to {day}s after feedback from the last survey.",
        "Attendance at the last {component} was strong, and the slides are linked from the handbook.",
        "Reminder: {concern} applies to everyone, including contractors on site.",
        "Volunteers for the {component} can sign up with {person2} by the end of the week.",
        "The handbook page on {concern} was refreshed and now replaces {legacy}.",
    ],
}
SLACK_OPENERS_BY_AREA = {
    "general": [
        "reminder: the {component} is on {day} this week",
        "who's coming to the {component}? need numbers by tomorrow",
        "the handbook page on {concern} has been updated, please give it a read",
        "anyone know who to ask about the {concern}?",
        "{person} is organising the {component}, ping them with ideas",
        "lost and found: a blue water bottle in the level 12 pantry",
    ],
    "compliance": [
        "control owners: evidence for {concern} is due {day}",
        "the {component} review moved to {day} at 2pm",
        "{symptom}, can someone pick it up?",
        "draft of the {concern} procedure is ready for comments",
    ],
    "product": [
        "readout from the {component} experiment is in, {metric} moved {small} points",
        "customer call notes on {concern} are in the drive",
        "can we get a decision on {concern} before the {day} review?",
        "{symptom} again this week, adding it to the backlog",
        "launch checklist for the {component} is up, please tick your items",
    ],
}
SLACK_REPLIES_BY_AREA = {
    "general": ["count me in", "thanks for organising!", "+1", "I'll check with my team",
                "is it remote-friendly?", "found it, thanks", "noted", "see you there"],
    "compliance": ["on it", "uploaded mine", "I'll chase the control owner", "thanks, noted",
                   "can we get an extension to {day}?", "comments added"],
    "product": ["nice result", "can you share the raw numbers?", "agree, let's prioritise it",
                "I'll add it to the roadmap review", "support is seeing the same thing",
                "let's discuss on {day}", "+1"],
}
