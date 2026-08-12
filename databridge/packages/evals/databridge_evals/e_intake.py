"""EV-INTAKE — clinic knowledge-graph eval suite (replaces EV-BRAIN, ADR-0009).

Five gates, each reported and never silently adjusted, mirroring the EV-* house pattern:

  1. Entity extraction P/R/F1 on a clinic fixture   target F1 ≥ 0.80
  2. Merge correctness: 5 cross-source cases         all must resolve correctly
  3. Provenance completeness: 100% of nodes          HARD gate (P4)
  4. Anti-fabrication canary                          REFUSED = pass
  5. NL-query factuality: 5 clinic questions          0 fabrications

Like E1, the extraction metric runs against a deterministic gold handler (FakeBackend) so CI proves
the pipeline assembles correctly; the accuracy number on a live model is a separate run.

**Gate 2 merges organisations, never patients.** Entity merging is right for insurers and employers
— the fixtures print "Coral Harbour Pte Ltd" three ways — and wrong for people, which is why
`resolve.py` matches registry names exactly and returns candidates a human selects. Two of the five
cases below are negative controls asserting that near-miss names stay apart.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

from databridge_core import SnapshotIdentity, SourcePointer
from databridge_harness import FakeBackend, Harness

from databridge_graph import (
    GraphNode,
    GraphStore,
    NodeKind,
    extract_graph,
    gold_extraction_handler,
    nl_query,
    node_id,
    upsert_node,
)
from databridge_graph.clinic_seed import SEED_SYSTEM_TIME, load_clinic_seed

_UTC = dt.timezone.utc
_VF = dt.datetime(2026, 1, 1, tzinfo=_UTC)

# --- the extraction fixture (text + gold entities/relations) ----------------------
#
# **This fixture extracts NO patient and asserts NO patient fact, deliberately.**
#
# It is not just an eval input: `FIXTURE_TEXT` is the DEFAULT registered ingest source for both the
# gateway (`main.py`) and the MCP server (`build_mcp_server`), so anything it emits gets written
# into the live graph the moment someone calls `brain_ingest("demo_sample.txt")`. An earlier draft
# extracted a PATIENT and related it with `covered_by` / `employed_by` — the exact three edge kinds
# `clinic_seed.py` reserves as unwritable before a human attestation, arriving through the back
# door of a demo fixture. It also keyed the patient on a raw NRIC, producing a second node for a
# person the registry already had under a `pat:` id.
#
# Document, issuer, employer and test extraction demonstrates the same machinery — grounding wall,
# closed vocabulary, merge, provenance — without any of that. Patient nodes enter the graph from
# the registry via `clinic_seed`, where they are keyed correctly and linked only by CLAIM edges.
_FIX_TEXT = ("Referral MRD707314 issued by Meridian Life Assurance Pte Ltd. "
             "Please perform a Chest X-Ray and a Treadmill ECG. "
             "The policyholder is Coral Harbour Pte Ltd.")
FIXTURE_TEXT = _FIX_TEXT          # public: the gateway's demo /brain/ingest payload


def _span(needle: str) -> dict:
    start = _FIX_TEXT.find(needle)
    assert start >= 0, f"fixture drift: {needle!r} not in EV-INTAKE fixture text"
    return {"char_start": start, "char_end": start + len(needle)}


_GOLD_ENTITIES = [
    {"name": "Referral MRD707314", "kind": "document", "canonical_id": "referral-mrd707314",
     **_span("Referral MRD707314")},
    {"name": "Meridian Life Assurance Pte Ltd", "kind": "org",
     "canonical_id": "MRDEB", **_span("Meridian Life Assurance Pte Ltd")},
    {"name": "Chest X-Ray", "kind": "test", "canonical_id": "chest_xray",
     **_span("Chest X-Ray")},
    {"name": "Treadmill ECG", "kind": "test", "canonical_id": "treadmill_ecg",
     **_span("Treadmill ECG")},
    {"name": "Coral Harbour Pte Ltd", "kind": "org", "canonical_id": "coral-harbour",
     **_span("Coral Harbour Pte Ltd")},
]
_GOLD_RELATIONS = [
    {"src_name": "Meridian Life Assurance Pte Ltd", "rel_type": "issued",
     "dst_name": "Referral MRD707314",
     "evidence_char_start": 0, "evidence_char_end": _FIX_TEXT.find("Please")},
    {"src_name": "Referral MRD707314", "rel_type": "requests_test", "dst_name": "Chest X-Ray",
     "evidence_char_start": 0, "evidence_char_end": _FIX_TEXT.find("and a")},
    {"src_name": "Referral MRD707314", "rel_type": "references",
     "dst_name": "Coral Harbour Pte Ltd",
     "evidence_char_start": 0, "evidence_char_end": len(_FIX_TEXT)},
]

# Edge kinds that state a fact about a SPECIFIC patient. No extraction fixture may emit one — see
# the comment above and ADR-0009. Asserted by `test_ev_intake.py`.
PATIENT_FACT_EDGE_KINDS = frozenset({"covered_by", "employed_by", "scheduled_as"})
# public aliases (the gateway demo-ingest wiring reuses this gold fixture as one source)
FIXTURE_ENTITIES = _GOLD_ENTITIES
FIXTURE_RELATIONS = _GOLD_RELATIONS

_GOLD_ENT_KEYS = {(e["name"], e["kind"]) for e in _GOLD_ENTITIES}
_GOLD_REL_KEYS = {(r["src_name"], r["rel_type"], r["dst_name"]) for r in _GOLD_RELATIONS}


def _fixture_doc():
    from databridge_convert.ir.models import Block, DocumentRecord
    snap = SnapshotIdentity(source_id="ev_intake_fix",
                            as_of=dt.datetime(2026, 3, 1, tzinfo=_UTC),
                            content_hash="h", schema_hash="s")
    return DocumentRecord(document_id="ev_intake_fix", sha256="fix", snapshot=snap,
                          blocks=[Block(kind="Paragraph", text=_FIX_TEXT)])


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f1


@dataclass
class EvIntakeResult:
    entity_f1: float
    entity_precision: float
    entity_recall: float
    relation_f1: float
    merge_cases: list = field(default_factory=list)         # {name, pass, detail}
    merge_all_pass: bool = False
    provenance_complete: bool = False
    provenance_ratio: float = 0.0
    fabrication_canary_refused: bool = False
    nl_questions: int = 0
    nl_fabrications: int = 0
    nl_all_grounded: bool = False

    def gates(self) -> dict:
        return {
            "extraction_f1>=0.80": self.entity_f1 >= 0.80,
            "merge_correctness_all": self.merge_all_pass,
            "provenance_completeness_100": self.provenance_complete,
            "anti_fabrication_canary": self.fabrication_canary_refused,
            "nl_factuality_0_fabrications": self.nl_fabrications == 0 and self.nl_all_grounded,
        }

    def passed(self) -> bool:
        return all(self.gates().values())

    def to_dict(self) -> dict:
        d = asdict(self)
        d["gates"] = self.gates()
        d["passed"] = self.passed()
        return d


# --- gate 1+3: extraction F1 + provenance completeness ----------------------------
def _score_extraction(harness: Harness):
    doc = _fixture_doc()
    store = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    res = extract_graph(doc, harness, store=store)

    got_ents = {(n.label, n.kind.value) for n in res.entities}
    e_p, e_r, e_f1 = _prf(len(got_ents & _GOLD_ENT_KEYS),
                          len(got_ents - _GOLD_ENT_KEYS), len(_GOLD_ENT_KEYS - got_ents))
    # relation key by resolved endpoints' labels
    label_by_id = {n.id: n.label for n in res.entities}
    got_rels = {(label_by_id.get(e.src_id, e.src_id), e.kind.value,
                 label_by_id.get(e.dst_id, e.dst_id))
                for e in res.relations}
    _, _, r_f1 = _prf(len(got_rels & _GOLD_REL_KEYS),
                      len(got_rels - _GOLD_REL_KEYS), len(_GOLD_REL_KEYS - got_rels))

    # provenance completeness: every open node has ≥1 SourcePointer
    open_nodes = store.open_nodes()
    grounded = sum(1 for r in open_nodes if store.sources_for(r["id"]))
    ratio = grounded / len(open_nodes) if open_nodes else 1.0
    return e_p, e_r, e_f1, r_f1, ratio == 1.0, ratio


# --- gate 2: merge correctness (5 cross-source cases) -----------------------------
def _n(kind: NodeKind, label: str, *, canonical=None, attrs=None, source="s1") -> GraphNode:
    ptr = SourcePointer(source_id=source, kind="doc_span", document_id=source,
                        char_start=0, char_end=5)
    return GraphNode(id=node_id(kind, (canonical or label).lower()), kind=kind, label=label,
                     canonical_id=canonical, attrs=attrs or {}, sources=[ptr],
                     valid_from=_VF, system_from=SEED_SYSTEM_TIME)


def _merge_cases() -> list[dict]:
    cases = []

    # 1) same issuer code, different printed name → canonical merge (1 node)
    s = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    upsert_node(s, _n(NodeKind.ORG, "Meridian Life Assurance Pte Ltd", canonical="MRDEB",
                      source="chit_01"))
    o = upsert_node(s, _n(NodeKind.ORG, "Meridian Life Assurance", canonical="MRDEB",
                          source="chit_09"))
    cases.append({"name": "same issuer-code canonical merge",
                  "pass": s.counts()["nodes"] == 1 and o.action in ("corroborated", "superseded"),
                  "detail": o.action})

    # 2) same employer, legal-suffix variation, no canonical → fuzzy merge (1 node)
    s = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    upsert_node(s, _n(NodeKind.ORG, "Coral Harbour Pte Ltd", source="chit_01"))
    o = upsert_node(s, _n(NodeKind.ORG, "Coral Harbour Pte Limited", source="chit_03"))
    cases.append({"name": "fuzzy employer merge (Pte Ltd/Pte Limited)",
                  "pass": s.counts()["nodes"] == 1, "detail": f"{o.action}/{o.matched_by}"})

    # 3) NEGATIVE CONTROL — two issuers of the same group, different codes → must NOT merge.
    #    Merging these bills the wrong payer.
    s = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    upsert_node(s, _n(NodeKind.ORG, "Everwell Insurance Group", canonical="EVWPA"))
    upsert_node(s, _n(NodeKind.ORG, "Everwell Insurance Group", canonical="EVWME"))
    cases.append({"name": "distinct payers not merged", "pass": s.counts()["nodes"] == 2,
                  "detail": f"{s.counts()['nodes']} nodes"})

    # 4) idempotent re-add → corroborated (1 node, 2 source rows)
    s = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    upsert_node(s, _n(NodeKind.ORG, "Northstar Life Assurance", canonical="NSTNBU", source="a"))
    o = upsert_node(s, _n(NodeKind.ORG, "Northstar Life Assurance", canonical="NSTNBU",
                          source="b"))
    cases.append({"name": "idempotent corroboration",
                  "pass": s.counts()["nodes"] == 1 and o.action == "corroborated"
                  and s.counts()["sources"] == 2,
                  "detail": f"{o.action}, {s.counts()['sources']} sources"})

    # 5) NEGATIVE CONTROL — two PATIENTS with near-identical names must never merge. This is the
    #    case the whole architecture exists to get right: "Lim Arjun" and "Lim Arjuna" are
    #    different people, and a merge here is a patient record silently attached to a stranger.
    s = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    upsert_node(s, _n(NodeKind.PATIENT, "Lim Arjun", canonical="T2221854H"))
    upsert_node(s, _n(NodeKind.PATIENT, "Lim Arjuna", canonical="S1234567A"))
    cases.append({"name": "near-identical patients not merged", "pass": s.counts()["nodes"] == 2,
                  "detail": f"{s.counts()['nodes']} nodes"})
    return cases


# --- gate 4+5: anti-fabrication canary + NL factuality ----------------------------
def _fabrication_canary() -> bool:
    """A node with NO SourcePointer must be REFUSED at construction (never stored)."""
    try:
        GraphNode(id="x", kind=NodeKind.TEST, label="fabricated", sources=[],
                  valid_from=_VF, system_from=SEED_SYSTEM_TIME)
        return False                                      # constructed ⇒ wall breached
    except Exception:
        return True


_NL_QUESTIONS = [
    "What does the WELL2 package include?",
    "What does the PEE226 package include?",
    "Who is Meridian Life Assurance?",
    "Which package includes the Chest X-Ray?",
    "What is the Full Blood Count?",
]


# The forms a cited node may legitimately contribute to an answer, as judged INDEPENDENTLY of
# `query._allow_forms`. Keeping the two implementations identical (which an earlier revision did,
# widening both to "every attribute value" in one change) makes this gate vacuous: it would agree
# with the thing it exists to check no matter how permissive that thing became.
#
# So this list is deliberately NARROWER — the label, plus only the attributes a clinic answer has
# any business quoting. If `nl_query` ever starts emitting a quantity from some other attribute,
# this check fails, which is the intended alarm rather than a nuisance.
_QUOTABLE_ATTRS = ("name", "eligibility", "category", "package_code", "issuer_code", "billed_to",
                   "questionnaire", "kind", "appointment", "date_of_birth")


def _allow_forms(node) -> list[str]:
    return [node.label, *(str(node.attrs[k]) for k in _QUOTABLE_ATTRS
                          if node.attrs.get(k) is not None)]


def _nl_factuality():
    """Non-vacuous: independently RE-CHECK every grounded answer's quantities against ONLY its
    cited nodes (a number in the answer absent from the citations = a fabrication that escaped),
    and run a positive-control canary that MUST be caught (proves the wall actually fires)."""
    from databridge_convert.generate.claim_check import check_claims
    from databridge_graph import row_to_node

    store = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    load_clinic_seed(store)
    grounded, fabrications = 0, 0
    for q in _NL_QUESTIONS:
        a = nl_query(store, q)
        if not a.grounded:
            continue
        grounded += 1
        # rebuild the allowed forms from the CITED nodes only (independent of nl_query internals)
        allowed: list[str] = []
        for c in a.citations:
            row = store.open_node_row(c.entity_id)
            if row is not None:
                allowed.extend(_allow_forms(row_to_node(store, row)))
        if not check_claims(a.answer, facts=[], allow=allowed).ok:
            fabrications += 1                       # a quantity not traceable to a cited node
    # positive control: a blatantly fabricated answer MUST be flagged; if not, the wall is broken
    canary = check_claims("Revenue was $9,999,999.00 at a rate of 87% on 2099-12-31.",
                          facts=[], allow=[])
    if canary.ok:
        fabrications += 1
    all_grounded = grounded == len(_NL_QUESTIONS)
    return len(_NL_QUESTIONS), fabrications, all_grounded


def run_ev_intake(harness: Harness) -> EvIntakeResult:
    e_p, e_r, e_f1, r_f1, prov_ok, prov_ratio = _score_extraction(harness)
    cases = _merge_cases()
    nq, nfab, n_all = _nl_factuality()
    return EvIntakeResult(
        entity_f1=round(e_f1, 4), entity_precision=round(e_p, 4), entity_recall=round(e_r, 4),
        relation_f1=round(r_f1, 4),
        merge_cases=cases, merge_all_pass=all(c["pass"] for c in cases),
        provenance_complete=prov_ok, provenance_ratio=round(prov_ratio, 4),
        fabrication_canary_refused=_fabrication_canary(),
        nl_questions=nq, nl_fabrications=nfab, nl_all_grounded=n_all,
    )


def gold_harness() -> Harness:
    """A FakeBackend harness wired to the EV-INTAKE gold extraction (deterministic CI)."""
    return Harness(FakeBackend(gold_extraction_handler(_GOLD_ENTITIES, _GOLD_RELATIONS)),
                   profile="cloud")
