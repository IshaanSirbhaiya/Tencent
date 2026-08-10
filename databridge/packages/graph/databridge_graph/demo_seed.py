"""Acme Corp demo seed (T08) — the investor-demo payload, 04_PRD §12.4 narrative.

A dense, fully-connected, pre-grounded company graph that loads into in-memory SQLite in <2s. The
roster (32 people / 8 orgs / 12 projects / 20 decisions / 16 facts / 15 source documents) is wired
by WORKS_AT / DECIDED / OWNS / FUNDS·BLOCKS·ENABLES / MENTIONED / DEPENDS_ON / REFERENCES edges so
the rendered graph is impressively interconnected.

Invariants (verified by tests): every node carries ≥1 SourcePointer (P4), every node has ≥1 edge
(no orphans), and the test-critical entities (Sarah Chen=CEO, Tom Walsh owns Project Atlas, the
BigClient $2.4M contract fact, the $180k burn-rate fact, …) keep their exact ids/labels/values.

Documents are first-class DOCUMENT nodes; each REFERENCES the entities it mentions, which both
grounds those entities and gives the documents their place in the graph (the provenance cluster).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from databridge_core import SourcePointer

from .merge import upsert_edge, upsert_node
from .schema import EdgeKind, GraphEdge, GraphNode, NodeKind, node_id
from .store import GraphStore

# When DataBridge "learned" the whole fixture (system_time). Fixed → reproducible counts/timeline.
SEED_SYSTEM_TIME = datetime(2024, 6, 1, tzinfo=timezone.utc)
_EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)        # default valid_from for standing facts

# --- source documents (15; first-class DOCUMENT nodes) ----------------------------
EMAILS = [f"email_{i:03d}.eml" for i in range(1, 7)]          # 6
MEETINGS = [f"meeting_{i:03d}.txt" for i in range(1, 5)]      # 4
CONTRACTS = [f"contract_{i:03d}.pdf" for i in range(1, 4)]    # 3
REPORTS = [f"report_{i:03d}.pdf" for i in range(1, 3)]        # 2
DOCS = EMAILS + MEETINGS + CONTRACTS + REPORTS               # 15

# back-compat aliases (older modules/tests import these names)
EMAIL_001, MEETING_001, CONTRACT_001 = EMAILS[0], MEETINGS[0], CONTRACTS[0]


def _email_ptr(part: str = "body", source: str = EMAIL_001) -> SourcePointer:
    return SourcePointer(source_id=source, kind="email_part",
                         message_id=f"<{source}@acmecorp.com>", part=part)


def _doc_ptr(source: str, start: int = 0, end: int = 40) -> SourcePointer:
    return SourcePointer(source_id=source, kind="doc_span",
                         document_id=source, char_start=start, char_end=end)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# --- node builders ----------------------------------------------------------------
def _person(name: str, role: str, email: str, src: list[SourcePointer]) -> GraphNode:
    return GraphNode(id=node_id(NodeKind.PERSON, email), kind=NodeKind.PERSON, label=name,
                     canonical_id=email, attrs={"role": role, "email": email},
                     sources=src, valid_from=_EPOCH, system_from=SEED_SYSTEM_TIME)


def _org(name: str, domain: str, otype: str, attrs: dict, src: list[SourcePointer]) -> GraphNode:
    return GraphNode(id=node_id(NodeKind.ORG, domain), kind=NodeKind.ORG, label=name,
                     canonical_id=domain, attrs={"type": otype, **attrs},
                     sources=src, valid_from=_EPOCH, system_from=SEED_SYSTEM_TIME)


def _project(name: str, status: str, src: list[SourcePointer]) -> GraphNode:
    return GraphNode(id=node_id(NodeKind.PROJECT, _slug(name)), kind=NodeKind.PROJECT, label=name,
                     attrs={"status": status}, sources=src,
                     valid_from=_EPOCH, system_from=SEED_SYSTEM_TIME)


def _decision(text: str, made_by: str, made_at: str, src: list[SourcePointer]) -> GraphNode:
    vf = datetime.fromisoformat(made_at).replace(tzinfo=timezone.utc)
    return GraphNode(id=node_id(NodeKind.DECISION, _slug(text)[:40]), kind=NodeKind.DECISION,
                     label=text, attrs={"made_by": made_by, "made_at": made_at},
                     sources=src, valid_from=vf, system_from=SEED_SYSTEM_TIME)


def _fact(claim: str, rendered: str, value, unit: str, src: list[SourcePointer],
          as_of: str = "2024-01-01") -> GraphNode:
    label = f"{claim}: {rendered}"
    vf = datetime.fromisoformat(as_of).replace(tzinfo=timezone.utc)
    return GraphNode(id=node_id(NodeKind.FACT, _slug(claim)), kind=NodeKind.FACT, label=label,
                     attrs={"claim": claim, "value": value, "unit": unit, "rendered": rendered,
                            "as_of_date": as_of},
                     sources=src, valid_from=vf, system_from=SEED_SYSTEM_TIME)


def _document(doc_id: str, title: str) -> GraphNode:
    kind = "email" if doc_id.endswith(".eml") else "pdf" if doc_id.endswith(".pdf") else "transcript"
    return GraphNode(id=node_id(NodeKind.DOCUMENT, doc_id), kind=NodeKind.DOCUMENT, label=doc_id,
                     attrs={"title": title, "kind": kind},
                     sources=[_doc_ptr(doc_id, 0, 1)],      # a document is grounded in itself
                     valid_from=_EPOCH, system_from=SEED_SYSTEM_TIME)


def _edge(src_id: str, kind: EdgeKind, dst_id: str, src: list[SourcePointer]) -> GraphEdge:
    return GraphEdge(id=f"{src_id}|{kind.value}|{dst_id}", src_id=src_id, kind=kind, dst_id=dst_id,
                     sources=src, valid_from=_EPOCH, system_from=SEED_SYSTEM_TIME)


# id helpers ------------------------------------------------------------------------
def _pid(email: str) -> str: return node_id(NodeKind.PERSON, email)
def _oid(domain: str) -> str: return node_id(NodeKind.ORG, domain)
def _prj(name: str) -> str: return node_id(NodeKind.PROJECT, _slug(name))
def _dec(text: str) -> str: return node_id(NodeKind.DECISION, _slug(text)[:40])
def _fid(claim: str) -> str: return node_id(NodeKind.FACT, _slug(claim))
def _did(doc_id: str) -> str: return node_id(NodeKind.DOCUMENT, doc_id)


# --- data tables ------------------------------------------------------------------
# people: (name, role, email, org_domain, primary_doc). The first 8 keep their original
# emails/sources so existing tests resolve (Sarah/James/Priya/Tom/Lisa/Alex/David/Maria).
ACME = "acmecorp.com"
PEOPLE = [
    # C-suite
    ("Sarah Chen", "CEO", "sarah@acmecorp.com", ACME, EMAIL_001),
    ("James Miller", "CTO", "james@acmecorp.com", ACME, EMAIL_001),
    ("Priya Patel", "VP Sales", "priya@acmecorp.com", ACME, CONTRACT_001),
    ("Lisa Park", "CFO", "lisa@acmecorp.com", ACME, MEETING_001),
    ("Marcus Johnson", "VP Engineering", "marcus@acmecorp.com", ACME, MEETINGS[1]),
    ("Diana Torres", "Chief of Staff", "diana@acmecorp.com", ACME, MEETINGS[1]),
    # Engineering
    ("Tom Walsh", "Senior Engineer", "tom@acmecorp.com", ACME, MEETING_001),
    ("Alex Kumar", "Engineer", "alex@acmecorp.com", ACME, MEETING_001),
    ("Rachel Lee", "Engineer", "rachel@acmecorp.com", ACME, MEETINGS[1]),
    ("Kevin Zhang", "Engineer", "kevin@acmecorp.com", ACME, MEETINGS[1]),
    ("Sophie Martin", "Engineer", "sophie@acmecorp.com", ACME, MEETINGS[2]),
    ("David Park", "DevOps", "davidp@acmecorp.com", ACME, MEETINGS[2]),
    ("Nina Patel", "QA Lead", "ninap@acmecorp.com", ACME, MEETINGS[2]),
    ("Chris Brown", "Engineer", "chris@acmecorp.com", ACME, MEETINGS[2]),
    ("Emma Wilson", "Senior Engineer", "emma@acmecorp.com", ACME, MEETINGS[3]),
    ("Liam O'Brien", "Engineer", "liam@acmecorp.com", ACME, MEETINGS[3]),
    # Sales
    ("Jake Thompson", "Account Executive", "jake@acmecorp.com", ACME, EMAILS[1]),
    ("Mia Rodriguez", "Account Executive", "mia@acmecorp.com", ACME, EMAILS[1]),
    ("Ethan Clark", "SDR", "ethan@acmecorp.com", ACME, EMAILS[2]),
    ("Olivia Kim", "SDR", "olivia@acmecorp.com", ACME, EMAILS[2]),
    ("Noah White", "Sales Ops", "noah@acmecorp.com", ACME, EMAILS[2]),
    ("Ava Martinez", "Customer Success", "ava@acmecorp.com", ACME, EMAILS[3]),
    # Finance
    ("Ryan Chen", "Controller", "ryan@acmecorp.com", ACME, REPORTS[0]),
    ("Hannah Lee", "FP&A", "hannah@acmecorp.com", ACME, REPORTS[0]),
    ("Oscar Webb", "Accountant", "oscar@acmecorp.com", ACME, REPORTS[0]),
    # Customers / external
    ("David Chen", "Director", "david@bigclient.com", "bigclient.com", EMAIL_001),
    ("Maria Garcia", "CTO", "maria@techcorp.io", "techcorp.io", CONTRACT_001),
    ("Robert Kim", "CEO", "robert@startupco.com", "startupco.com", EMAILS[3]),
    ("Jennifer Wu", "VP", "jennifer@enterprisex.com", "enterprisex.com", EMAILS[4]),
    ("Michael Brown", "Director", "michael@govclient.gov", "govclient.gov", CONTRACTS[1]),
    ("Ashley Davis", "CISO", "ashley@healthco.com", "healthco.com", EMAILS[5]),
]

# orgs: (name, domain, type, attrs, primary_doc)
ORGS = [
    ("Acme Corp", ACME, "internal", {}, MEETING_001),
    ("BigClient Inc", "bigclient.com", "customer", {"contract_value": 2400000}, CONTRACT_001),
    ("TechCorp IO", "techcorp.io", "prospect", {"deal_value": 800000}, CONTRACT_001),
    ("StartupCo", "startupco.com", "churned", {"contract_value": 120000}, EMAILS[3]),
    ("EnterpriseX", "enterprisex.com", "upsell", {"deal_value": 1800000}, EMAILS[4]),
    ("GovClient", "govclient.gov", "customer", {"contract_value": 600000}, CONTRACTS[1]),
    ("HealthCo", "healthco.com", "prospect", {"deal_value": 950000}, EMAILS[5]),
    ("AWS", "aws.amazon.com", "partner", {}, REPORTS[1]),
]

# projects: (name, status, owner_email, primary_doc)
PROJECTS = [
    ("Project Atlas", "active", "tom@acmecorp.com", MEETING_001),
    ("Data Migration", "paused", "alex@acmecorp.com", MEETING_001),
    ("Q4 Sales Push", "active", "priya@acmecorp.com", MEETING_001),
    ("Security Audit", "active", "ninap@acmecorp.com", MEETINGS[2]),
    ("API v3 Launch", "active", "emma@acmecorp.com", MEETINGS[3]),
    ("Mobile App", "planning", "rachel@acmecorp.com", MEETINGS[1]),
    ("Customer Portal", "done", "kevin@acmecorp.com", MEETINGS[1]),
    ("Data Warehouse", "active", "davidp@acmecorp.com", MEETINGS[2]),
    ("SOC2 Compliance", "active", "marcus@acmecorp.com", REPORTS[1]),
    ("Sales Automation", "active", "noah@acmecorp.com", EMAILS[2]),
    ("Partner Program", "planning", "jake@acmecorp.com", EMAILS[1]),
    ("Churn Reduction", "active", "mia@acmecorp.com", EMAILS[1]),
]

# decisions: (text, made_by_email, made_at, primary_doc)
DECISIONS = [
    ("Migrate to cloud infrastructure by Q1", "james@acmecorp.com", "2024-01-15", MEETING_001),
    ("Expand sales team by 4 reps", "sarah@acmecorp.com", "2024-02-01", MEETING_001),
    ("Prioritise BigClient renewal over new deals", "sarah@acmecorp.com", "2024-03-10", EMAIL_001),
    ("Approve $340K budget for Project Atlas", "lisa@acmecorp.com", "2024-03-15", MEETING_001),
    ("Delay Data Migration to focus on Atlas", "james@acmecorp.com", "2024-04-01", MEETING_001),
    ("Launch API v3 in Q3", "james@acmecorp.com", "2024-04-15", MEETINGS[3]),
    ("Hire DevOps lead", "marcus@acmecorp.com", "2024-04-20", MEETINGS[2]),
    ("Pursue TechCorp deal", "priya@acmecorp.com", "2024-05-01", CONTRACTS[2]),
    ("Freeze hiring in Q3", "sarah@acmecorp.com", "2024-05-15", MEETINGS[1]),
    ("Achieve SOC2 certification by Q4", "lisa@acmecorp.com", "2024-05-20", REPORTS[1]),
    ("Reduce churn to below 5%", "sarah@acmecorp.com", "2024-06-01", EMAILS[1]),
    ("Automate sales outreach", "noah@acmecorp.com", "2024-06-05", EMAILS[2]),
    ("Expand to EMEA in 2025", "sarah@acmecorp.com", "2024-06-10", MEETINGS[1]),
    ("Renegotiate AWS contract", "lisa@acmecorp.com", "2024-06-15", REPORTS[1]),
    ("Build a partner program", "jake@acmecorp.com", "2024-06-20", EMAILS[1]),
    ("Deprioritise GovClient", "priya@acmecorp.com", "2024-07-01", CONTRACTS[1]),
    ("Launch mobile app beta in Q1 2025", "rachel@acmecorp.com", "2024-07-05", MEETINGS[1]),
    ("Implement a data warehouse", "davidp@acmecorp.com", "2024-07-10", MEETINGS[2]),
    ("Complete the customer portal", "kevin@acmecorp.com", "2024-07-15", MEETINGS[1]),
    ("Prioritise the HealthCo deal", "priya@acmecorp.com", "2024-08-01", EMAILS[5]),
]

# facts: (claim, rendered, value, unit, primary_doc)
FACTS = [
    ("BigClient contract value", "$2,400,000", 2400000, "USD", CONTRACT_001),
    ("TechCorp deal value", "$800,000", 800000, "USD", CONTRACTS[2]),
    ("EnterpriseX upsell potential", "$400,000", 400000, "USD", EMAILS[4]),
    ("Q4 revenue target", "$1,200,000", 1200000, "USD", MEETING_001),
    ("Current ARR", "$4,870,000", 4870000, "USD", REPORTS[0]),
    ("Current burn rate", "$180,000/month", 180000, "USD/month", MEETING_001),
    ("Runway", "14 months", 14, "months", REPORTS[0]),
    ("Headcount", "32", 32, "people", REPORTS[0]),
    ("Churn rate", "8.2%", 8.2, "percent", EMAILS[1]),
    ("NPS score", "42", 42, "score", EMAILS[3]),
    ("Project Atlas budget", "$340,000", 340000, "USD", MEETING_001),
    ("AWS monthly spend", "$28,000", 28000, "USD/month", REPORTS[1]),
    ("Sales pipeline", "$3,200,000", 3200000, "USD", EMAILS[1]),
    ("Win rate", "24%", 24, "percent", EMAILS[2]),
    ("Average deal size", "$420,000", 420000, "USD", EMAILS[2]),
    ("Project Atlas estimated completion", "2024-08-31", "2024-08-31", "date", MEETING_001),
]

DOC_TITLES = {
    EMAILS[0]: "BigClient renewal discussion", EMAILS[1]: "Q2 sales review",
    EMAILS[2]: "Sales automation rollout", EMAILS[3]: "Customer success update",
    EMAILS[4]: "EnterpriseX upsell thread", EMAILS[5]: "HealthCo intro",
    MEETINGS[0]: "Q1 planning meeting", MEETINGS[1]: "Product strategy offsite",
    MEETINGS[2]: "Engineering all-hands", MEETINGS[3]: "API v3 kickoff",
    CONTRACTS[0]: "BigClient MSA renewal", CONTRACTS[1]: "GovClient agreement",
    CONTRACTS[2]: "TechCorp order form", REPORTS[0]: "Q2 board report",
    REPORTS[1]: "Infrastructure & compliance review",
}

# decision → project causality (decision_text, edge_kind, project_name)
CAUSALITY = [
    ("Approve $340K budget for Project Atlas", EdgeKind.FUNDS, "Project Atlas"),
    ("Migrate to cloud infrastructure by Q1", EdgeKind.ENABLES, "Project Atlas"),
    ("Delay Data Migration to focus on Atlas", EdgeKind.BLOCKS, "Data Migration"),
    ("Launch API v3 in Q3", EdgeKind.ENABLES, "API v3 Launch"),
    ("Hire DevOps lead", EdgeKind.ENABLES, "Data Warehouse"),
    ("Achieve SOC2 certification by Q4", EdgeKind.ENABLES, "SOC2 Compliance"),
    ("Automate sales outreach", EdgeKind.FUNDS, "Sales Automation"),
    ("Reduce churn to below 5%", EdgeKind.ENABLES, "Churn Reduction"),
    ("Build a partner program", EdgeKind.ENABLES, "Partner Program"),
    ("Launch mobile app beta in Q1 2025", EdgeKind.ENABLES, "Mobile App"),
    ("Implement a data warehouse", EdgeKind.FUNDS, "Data Warehouse"),
    ("Complete the customer portal", EdgeKind.ENABLES, "Customer Portal"),
    ("Freeze hiring in Q3", EdgeKind.BLOCKS, "API v3 Launch"),
    ("Renegotiate AWS contract", EdgeKind.ENABLES, "Data Warehouse"),
]

# person → person mentions (build cluster cross-links)
MENTIONS = [
    ("sarah@acmecorp.com", "david@bigclient.com"), ("priya@acmecorp.com", "maria@techcorp.io"),
    ("james@acmecorp.com", "tom@acmecorp.com"), ("marcus@acmecorp.com", "emma@acmecorp.com"),
    ("sarah@acmecorp.com", "james@acmecorp.com"), ("priya@acmecorp.com", "jennifer@enterprisex.com"),
    ("lisa@acmecorp.com", "ryan@acmecorp.com"), ("noah@acmecorp.com", "jake@acmecorp.com"),
    ("mia@acmecorp.com", "ava@acmecorp.com"), ("marcus@acmecorp.com", "davidp@acmecorp.com"),
    ("priya@acmecorp.com", "ashley@healthco.com"), ("diana@acmecorp.com", "sarah@acmecorp.com"),
]

# project → project / decision dependencies (a_name, b_name, kind) — DEPENDS_ON
DEPENDS = [
    ("API v3 Launch", "Data Warehouse"), ("Mobile App", "API v3 Launch"),
    ("Customer Portal", "API v3 Launch"), ("SOC2 Compliance", "Security Audit"),
    ("Churn Reduction", "Customer Portal"), ("Sales Automation", "Data Warehouse"),
    ("Partner Program", "Q4 Sales Push"), ("Data Warehouse", "Data Migration"),
]


# --- build ------------------------------------------------------------------------
def build_seed_nodes() -> list[GraphNode]:
    nodes: list[GraphNode] = []
    for doc_id in DOCS:
        nodes.append(_document(doc_id, DOC_TITLES.get(doc_id, doc_id)))
    for name, role, email, _org_dom, doc in PEOPLE:
        # the two email-sourced principals keep an email_part pointer (provenance kind coverage)
        src = [_email_ptr("body", doc)] if doc.endswith(".eml") and email in (
            "sarah@acmecorp.com", "james@acmecorp.com", "david@bigclient.com") else [_doc_ptr(doc)]
        nodes.append(_person(name, role, email, src))
    for name, domain, otype, attrs, doc in ORGS:
        nodes.append(_org(name, domain, otype, attrs, [_doc_ptr(doc)]))
    for name, status, _owner, doc in PROJECTS:
        nodes.append(_project(name, status, [_doc_ptr(doc)]))
    for text, made_by, made_at, doc in DECISIONS:
        src = [_email_ptr("body", doc)] if doc.endswith(".eml") else [_doc_ptr(doc)]
        nodes.append(_decision(text, made_by, made_at, src))
    for claim, rendered, value, unit, doc in FACTS:
        nodes.append(_fact(claim, rendered, value, unit, [_doc_ptr(doc)]))
    return nodes


def build_seed_edges() -> list[GraphEdge]:
    e: list[GraphEdge] = []
    email_to_doc = {p[2]: p[4] for p in PEOPLE}

    def psrc(email: str) -> list[SourcePointer]:
        d = email_to_doc.get(email, MEETING_001)
        return [_email_ptr("body", d)] if d.endswith(".eml") else [_doc_ptr(d)]

    # WORKS_AT (every person → their org)
    for name, role, email, org_dom, doc in PEOPLE:
        e.append(_edge(_pid(email), EdgeKind.WORKS_AT, _oid(org_dom), psrc(email)))
    # DECIDED (every decision → its decider)
    for text, made_by, made_at, doc in DECISIONS:
        src = [_email_ptr("body", doc)] if doc.endswith(".eml") else [_doc_ptr(doc)]
        e.append(_edge(_pid(made_by), EdgeKind.DECIDED, _dec(text), src))
    # OWNS (every project → its owner)
    for name, status, owner, doc in PROJECTS:
        e.append(_edge(_pid(owner), EdgeKind.OWNS, _prj(name), [_doc_ptr(doc)]))
    # FUNDS / BLOCKS / ENABLES (decision → project causality)
    for text, kind, proj in CAUSALITY:
        e.append(_edge(_dec(text), kind, _prj(proj), [_doc_ptr(MEETING_001)]))
    # MENTIONED (person → person)
    for a, b in MENTIONS:
        e.append(_edge(_pid(a), EdgeKind.MENTIONED, _pid(b), psrc(a)))
    # DEPENDS_ON (project → project)
    for a, b in DEPENDS:
        e.append(_edge(_prj(a), EdgeKind.DEPENDS_ON, _prj(b), [_doc_ptr(REPORTS[1])]))
    # REFERENCES (each document → every entity whose primary doc it is) — grounds entities AND
    # gives every node an edge (no orphans) + makes documents the provenance cluster.
    for name, role, email, _o, doc in PEOPLE:
        e.append(_edge(_did(doc), EdgeKind.REFERENCES, _pid(email), [_doc_ptr(doc)]))
    for name, domain, _t, _a, doc in ORGS:
        e.append(_edge(_did(doc), EdgeKind.REFERENCES, _oid(domain), [_doc_ptr(doc)]))
    for name, _s, _o, doc in PROJECTS:
        e.append(_edge(_did(doc), EdgeKind.REFERENCES, _prj(name), [_doc_ptr(doc)]))
    for text, _mb, _ma, doc in DECISIONS:
        e.append(_edge(_did(doc), EdgeKind.REFERENCES, _dec(text), [_doc_ptr(doc)]))
    for claim, _r, _v, _u, doc in FACTS:
        e.append(_edge(_did(doc), EdgeKind.REFERENCES, _fid(claim), [_doc_ptr(doc)]))
    return e


def load_demo_seed(store: GraphStore) -> dict:
    """Load Acme Corp into ``store`` (idempotent). Returns {nodes_created, edges_created,
    sources_attached}. Loads in well under 2s (all in-memory SQLite writes)."""
    before = store.counts()
    for node in build_seed_nodes():
        upsert_node(store, node)
    for edge in build_seed_edges():
        upsert_edge(store, edge)
    after = store.counts()
    return {
        "nodes_created": after["nodes"] - before["nodes"],
        "edges_created": after["edges"] - before["edges"],
        "sources_attached": after["sources"] - before["sources"],
    }


def fresh_demo_store() -> GraphStore:
    """A new in-memory store with the demo seed loaded and a pinned system clock."""
    store = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    load_demo_seed(store)
    return store
