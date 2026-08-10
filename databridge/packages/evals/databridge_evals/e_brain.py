"""EV-BRAIN — Company-Brain eval suite (T08; 04_PRD §10, specialised for the POC demo).

Five gates, each reported (never silently adjusted), mirroring the EV-* house pattern:

  1. Entity extraction P/R/F1 on a fixture     target F1 ≥ 0.80
  2. Merge correctness: 5 cross-source cases    all must resolve correctly
  3. Provenance completeness: 100% of nodes     HARD gate (P4)
  4. Anti-fabrication canary                     REFUSED = pass
  5. NL-query factuality: 5 questions            0 fabrications

Like E1, the extraction metric runs against a deterministic gold handler (FakeBackend) so CI
proves the pipeline assembles correctly; the accuracy number on a live model is a separate run.
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
from databridge_graph.demo_seed import SEED_SYSTEM_TIME, load_demo_seed

_UTC = dt.timezone.utc
_VF = dt.datetime(2024, 1, 1, tzinfo=_UTC)

# --- the extraction fixture (text + gold entities/relations) ----------------------
_FIX_TEXT = ("Sarah Chen, the CEO of Acme Corp, approved the cloud migration. "
             "James Miller leads engineering at Acme Corp. "
             "Tom Walsh owns Project Atlas.")
FIXTURE_TEXT = _FIX_TEXT          # public: the gateway's demo /brain/ingest payload

_GOLD_ENTITIES = [
    {"name": "Sarah Chen", "kind": "person", "char_start": 0, "char_end": 10,
     "canonical_id": "sarah@acmecorp.com"},
    {"name": "Acme Corp", "kind": "org", "char_start": _FIX_TEXT.find("Acme Corp"),
     "char_end": _FIX_TEXT.find("Acme Corp") + 9, "canonical_id": "acmecorp.com"},
    {"name": "James Miller", "kind": "person", "char_start": _FIX_TEXT.find("James Miller"),
     "char_end": _FIX_TEXT.find("James Miller") + 12, "canonical_id": "james@acmecorp.com"},
    {"name": "Tom Walsh", "kind": "person", "char_start": _FIX_TEXT.find("Tom Walsh"),
     "char_end": _FIX_TEXT.find("Tom Walsh") + 9},
    {"name": "Project Atlas", "kind": "project", "char_start": _FIX_TEXT.find("Project Atlas"),
     "char_end": _FIX_TEXT.find("Project Atlas") + 13},
]
_GOLD_RELATIONS = [
    {"src_name": "Sarah Chen", "rel_type": "works_at", "dst_name": "Acme Corp",
     "evidence_char_start": 0, "evidence_char_end": 33},
    {"src_name": "James Miller", "rel_type": "works_at", "dst_name": "Acme Corp",
     "evidence_char_start": _FIX_TEXT.find("James"), "evidence_char_end": _FIX_TEXT.find("Atlas")},
    {"src_name": "Tom Walsh", "rel_type": "owns", "dst_name": "Project Atlas",
     "evidence_char_start": _FIX_TEXT.find("Tom"), "evidence_char_end": len(_FIX_TEXT)},
]
# the gold key sets the metric scores against (entity = (name,kind); relation = (src,rel,dst))
# public aliases (the gateway demo-ingest wiring reuses the EV-BRAIN gold fixture as one source)
FIXTURE_ENTITIES = _GOLD_ENTITIES
FIXTURE_RELATIONS = _GOLD_RELATIONS

_GOLD_ENT_KEYS = {(e["name"], e["kind"]) for e in _GOLD_ENTITIES}
_GOLD_REL_KEYS = {(r["src_name"], r["rel_type"], r["dst_name"]) for r in _GOLD_RELATIONS}


def _fixture_doc():
    from databridge_convert.ir.models import Block, DocumentRecord
    snap = SnapshotIdentity(source_id="ev_brain_fix", as_of=dt.datetime(2024, 3, 1, tzinfo=_UTC),
                            content_hash="h", schema_hash="s")
    return DocumentRecord(document_id="ev_brain_fix", sha256="fix", snapshot=snap,
                          blocks=[Block(kind="Paragraph", text=_FIX_TEXT)])


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f1


@dataclass
class EvBrainResult:
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
    got_rels = {(label_by_id.get(e.src_id, e.src_id), e.kind.value, label_by_id.get(e.dst_id, e.dst_id))
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

    # 1) same email, different name spelling → canonical merge (1 node, corroborated)
    s = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    upsert_node(s, _n(NodeKind.PERSON, "Sarah Chen", canonical="sarah@acmecorp.com", source="email"))
    o = upsert_node(s, _n(NodeKind.PERSON, "Sarah J. Chen", canonical="sarah@acmecorp.com", source="hr"))
    cases.append({"name": "same-email canonical merge", "pass": s.counts()["nodes"] == 1
                  and o.action in ("corroborated", "superseded"), "detail": o.action})

    # 2) same name+kind, no canonical → fuzzy merge (1 node)
    s = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    upsert_node(s, _n(NodeKind.ORG, "Acme Corp", source="doc"))
    o = upsert_node(s, _n(NodeKind.ORG, "Acme Corporation", source="slack"))
    cases.append({"name": "fuzzy name merge (Acme Corp/Corporation)",
                  "pass": s.counts()["nodes"] == 1, "detail": f"{o.action}/{o.matched_by}"})

    # 3) same surname, different person → must NOT merge (2 nodes)
    s = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    upsert_node(s, _n(NodeKind.PERSON, "Sarah Chen", canonical="sarah@acmecorp.com"))
    upsert_node(s, _n(NodeKind.PERSON, "David Chen", canonical="david@bigclient.com"))
    cases.append({"name": "distinct people not merged", "pass": s.counts()["nodes"] == 2,
                  "detail": f"{s.counts()['nodes']} nodes"})

    # 4) idempotent re-add → corroborated (1 node, 2 source rows)
    s = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    upsert_node(s, _n(NodeKind.PERSON, "Tom Walsh", canonical="tom@acmecorp.com", source="a"))
    o = upsert_node(s, _n(NodeKind.PERSON, "Tom Walsh", canonical="tom@acmecorp.com", source="b"))
    cases.append({"name": "idempotent corroboration", "pass": s.counts()["nodes"] == 1
                  and o.action == "corroborated" and s.counts()["sources"] == 2,
                  "detail": f"{o.action}, {s.counts()['sources']} sources"})

    # 5) org domain canonical merge across two contracts (1 node)
    s = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    upsert_node(s, _n(NodeKind.ORG, "BigClient Inc", canonical="bigclient.com", source="c1"))
    o = upsert_node(s, _n(NodeKind.ORG, "BigClient", canonical="bigclient.com", source="c2"))
    cases.append({"name": "org domain canonical merge", "pass": s.counts()["nodes"] == 1,
                  "detail": o.matched_by})
    return cases


# --- gate 4+5: anti-fabrication canary + NL factuality ----------------------------
def _fabrication_canary() -> bool:
    """A node with NO SourcePointer must be REFUSED at construction (never stored)."""
    try:
        GraphNode(id="x", kind=NodeKind.FACT, label="fabricated", sources=[],
                  valid_from=_VF, system_from=SEED_SYSTEM_TIME)
        return False                                      # constructed ⇒ wall breached
    except Exception:
        return True


_NL_QUESTIONS = [
    "What is the BigClient contract value?",
    "Who decided to prioritise BigClient renewal?",
    "Who owns Project Atlas?",
    "What is our current burn rate?",
    "Who is Sarah Chen?",
]


def _allow_forms(node) -> list[str]:
    forms = [node.label]
    if node.attrs.get("rendered"):
        forms.append(str(node.attrs["rendered"]))
    if node.attrs.get("made_at"):
        forms.append(str(node.attrs["made_at"]))
    return forms


def _nl_factuality():
    """Non-vacuous: independently RE-CHECK every grounded answer's quantities against ONLY its
    cited nodes (a number in the answer absent from the citations = a fabrication that escaped),
    and run a positive-control canary that MUST be caught (proves the wall actually fires)."""
    from databridge_convert.generate.claim_check import check_claims
    from databridge_graph import row_to_node

    store = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    load_demo_seed(store)
    grounded, fabrications = 0, 0
    for q in _NL_QUESTIONS:
        a = nl_query(store, q)
        if not a.grounded:
            continue
        grounded += 1
        # rebuild the allowed forms from the CITED nodes only (independent of nl_query's internals)
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


def run_ev_brain(harness: Harness) -> EvBrainResult:
    e_p, e_r, e_f1, r_f1, prov_ok, prov_ratio = _score_extraction(harness)
    cases = _merge_cases()
    nq, nfab, n_all = _nl_factuality()
    return EvBrainResult(
        entity_f1=round(e_f1, 4), entity_precision=round(e_p, 4), entity_recall=round(e_r, 4),
        relation_f1=round(r_f1, 4),
        merge_cases=cases, merge_all_pass=all(c["pass"] for c in cases),
        provenance_complete=prov_ok, provenance_ratio=round(prov_ratio, 4),
        fabrication_canary_refused=_fabrication_canary(),
        nl_questions=nq, nl_fabrications=nfab, nl_all_grounded=n_all,
    )


def gold_harness() -> Harness:
    """A FakeBackend harness wired to the EV-BRAIN gold extraction (deterministic CI)."""
    return Harness(FakeBackend(gold_extraction_handler(_GOLD_ENTITIES, _GOLD_RELATIONS)),
                   profile="cloud")
