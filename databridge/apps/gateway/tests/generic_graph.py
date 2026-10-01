"""A synthetic, non-clinical graph for the generic `/evidence` and `/intelligence` tests.

Everything here is fictional and built at test time in a temp directory:

* a packs root holding two synthetic packs — ``deskpack`` (declares a SUBJECT kind and a claim
  edge to it) and ``plainpack`` (declares no subject at all) — so the surfaces are exercised under
  a vocabulary the engine has never seen, and never under the clinical pack;
* a content-addressed capture store laid out exactly as the ingestion contract says
  (``<sha[:2]>/<sha256>``, ``.txt``, ``.json``);
* an ingestion run directory with ledgers written under TAGGED names (``excluded_ocr.jsonl``) so a
  reader that looked ledgers up by a fixed list of names would miss them;
* a GraphStore whose nodes cite those captures with real spans.

Expectations in the tests are derived from what this module built (``GenericGraph``), never
written down as literals.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

DESKPACK = """
pack: deskpack
title: Synthetic desk
version: "1.0"
node_kinds:
  - {name: person, binds_to: entity}
  - {name: memo, binds_to: document}
  - {name: wiki_page, binds_to: document}
  - {name: team, binds_to: organisation}
  - {name: topic, binds_to: entity}
edge_kinds:
  - {name: wrote, binds_to: issued, src: [team], dst: [memo, wiki_page]}
  - {name: concerns, binds_to: about, src: [memo], dst: [person]}
  - {name: covers, binds_to: references, src: [memo, wiki_page], dst: [topic]}
  - {name: part_of_team, binds_to: part_of, src: [topic], dst: [team]}
subject_kinds:
  - kind: person
    identifiers: [staff_number]
    never_fuzzy_merge: true
    attestation: in_person
    grant_type: PersonReadGrant
"""

PLAINPACK = """
pack: plainpack
title: Synthetic library
version: "1.0"
node_kinds:
  - {name: page, binds_to: document}
  - {name: idea, binds_to: entity}
  - {name: group, binds_to: organisation}
edge_kinds:
  - {name: published, binds_to: issued, src: [group], dst: [page]}
  - {name: discusses, binds_to: references, src: [page], dst: [idea]}
"""

WHEN = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_capture(root: Path, text: str, meta: dict) -> str:
    raw = text.encode("utf-8")
    digest = sha(raw)
    folder = root / digest[:2]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / digest).write_bytes(raw)
    (folder / f"{digest}.txt").write_text(text, encoding="utf-8")
    (folder / f"{digest}.json").write_text(json.dumps(meta), encoding="utf-8")
    return digest


def write_packs(root: Path) -> Path:
    packs = root / "packs"
    for name, body in (("deskpack", DESKPACK), ("plainpack", PLAINPACK)):
        (packs / name).mkdir(parents=True, exist_ok=True)
        (packs / name / "ontology.yaml").write_text(body, encoding="utf-8")
    return packs


@contextmanager
def bound(packs_root: Path, *names: str) -> Iterator[None]:
    """Point the pack loader at the synthetic root and bind ``names`` for the block."""
    from databridge_graph.packs import clear_caches, deployment

    prior = os.environ.get("DATABRIDGE_PACKS_ROOT")
    os.environ["DATABRIDGE_PACKS_ROOT"] = str(packs_root)
    clear_caches()
    try:
        with deployment(*names):
            yield
    finally:
        if prior is None:
            os.environ.pop("DATABRIDGE_PACKS_ROOT", None)
        else:
            os.environ["DATABRIDGE_PACKS_ROOT"] = prior
        clear_caches()


def span(text: str, needle: str) -> tuple[int, int]:
    start = text.index(needle)
    return start, start + len(needle)


@dataclass
class GenericGraph:
    store: object
    capture_dir: Path
    ingest_dir: Path
    digests: dict[str, str] = field(default_factory=dict)
    texts: dict[str, str] = field(default_factory=dict)
    ids: dict[str, str] = field(default_factory=dict)
    excluded_reasons: dict[str, int] = field(default_factory=dict)


def doc_pointer(digest: str, text: str, needle: str, *, source: str, document_id: str):
    from databridge_core import SourcePointer

    start, end = span(text, needle)
    return SourcePointer(source_id=source, kind="doc_span", document_id=document_id,
                         char_start=start, char_end=end, capture_digest=f"sha256:{digest}")


def build_deskpack_graph(tmp: Path) -> GenericGraph:
    """Two sources: a wiki (no subject) and a memo drive (memos concern a person subject).

    Must be called INSIDE ``bound(packs, "deskpack")``.
    """
    from databridge_core import SourcePointer
    from databridge_graph.schema import GraphEdge, GraphNode, NodeKind, EdgeKind
    from databridge_graph.store import GraphStore

    captures, ingest = tmp / "capture", tmp / "ingest"
    g = GenericGraph(store=GraphStore(str(tmp / "graph.db")), capture_dir=captures,
                     ingest_dir=ingest)

    wiki_text = ("Harbour Lights runbook. The deployment freeze begins on Friday. "
                 "Owner: Platform Team. Topic: release trains.")
    memo_text = ("Memo to Quinn Vale (staff number SN-0042) about the release trains "
                 "rota change. From: Platform Team.")
    handbook_text = "Team handbook. Platform Team runs the release trains every fortnight."
    g.texts = {"wiki": wiki_text, "memo": memo_text, "handbook": handbook_text}
    g.digests["wiki"] = write_capture(captures, wiki_text, {
        "source": "wiki", "uri": "file:///synthetic/wiki/runbook.md", "mime": "text/markdown",
        "title": "Harbour Lights runbook", "retrieved_at": "2026-09-01T10:00:00Z"})
    g.digests["memo"] = write_capture(captures, memo_text, {
        "source": "memos", "uri": "file:///synthetic/memos/rota.txt", "mime": "text/plain",
        "title": "Rota memo", "retrieved_at": "2026-09-01T11:00:00Z"})

    g.digests["handbook"] = write_capture(captures, handbook_text, {
        "source": "handbook", "uri": "file:///synthetic/handbook/team.md", "mime": "text/markdown",
        "title": "Team handbook", "retrieved_at": "2026-09-01T12:00:00Z"})

    def handbook(needle: str):
        return doc_pointer(g.digests["handbook"], handbook_text, needle, source="handbook",
                           document_id="handbook/team.md")

    def wiki(needle: str):
        return doc_pointer(g.digests["wiki"], wiki_text, needle, source="wiki",
                           document_id="wiki/runbook.md")

    def memo(needle: str):
        return doc_pointer(g.digests["memo"], memo_text, needle, source="memos",
                           document_id="memos/rota.txt")

    def node(nid: str, kind: str, label: str, sources, **attrs_with_ptr):
        attrs = {k: v for k, (v, _p) in attrs_with_ptr.items()}
        attr_sources = {k: p for k, (_v, p) in attrs_with_ptr.items()}
        g.store.add_node(GraphNode(id=nid, kind=NodeKind(kind), label=label, sources=sources,
                                   attrs=attrs, attr_sources=attr_sources,
                                   valid_from=WHEN, system_from=WHEN))
        g.ids[label] = nid

    def edge(src: str, kind: str, dst: str, sources):
        g.store.add_edge(GraphEdge(id=f"{src}|{kind}|{dst}", src_id=src, dst_id=dst,
                                   kind=EdgeKind(kind), sources=sources,
                                   valid_from=WHEN, system_from=WHEN))

    node("wiki_page:wiki/runbook.md", "wiki_page", "Harbour Lights runbook",
         [wiki("Harbour Lights runbook")])
    node("memo:memos/rota.txt", "memo", "Rota memo", [memo("Memo to Quinn Vale")])
    node("wiki_page:handbook/team.md", "wiki_page", "Team handbook", [handbook("Team handbook")])
    # Cited by two sources that name no subject — a cross-source entity that prints. (Were one of
    # its pointers into the memo, it would be subject-bearing: mixed evidence fails closed.)
    node("team:platform", "team", "Platform Team",
         [wiki("Platform Team"), handbook("Platform Team")])
    node("topic:release-trains", "topic", "release trains", [wiki("release trains")],
         freeze_day=("Friday", wiki("Friday")))
    node("person:sn-0042", "person", "Quinn Vale", [memo("Quinn Vale")],
         staff_number=("SN-0042", memo("SN-0042")))
    # An entity grounded ONLY in the memo — ordinary kind, but its evidence names a subject.
    node("topic:rota-change", "topic", "rota change", [memo("rota change")])
    # An orphan: nothing links to it.
    node("topic:unlinked", "topic", "unlinked idea", [wiki("deployment freeze")])

    edge("team:platform", "wrote", "wiki_page:wiki/runbook.md", [wiki("Owner: Platform Team")])
    edge("team:platform", "wrote", "memo:memos/rota.txt", [memo("From: Platform Team")])
    edge("team:platform", "wrote", "wiki_page:handbook/team.md", [handbook("Platform Team runs")])
    edge("wiki_page:wiki/runbook.md", "covers", "topic:release-trains",
         [wiki("Topic: release trains")])
    edge("memo:memos/rota.txt", "covers", "topic:release-trains", [memo("release trains")])
    edge("memo:memos/rota.txt", "concerns", "person:sn-0042", [memo("Quinn Vale")])
    edge("topic:release-trains", "part_of_team", "team:platform",
         [SourcePointer(source_id="wiki", kind="doc_span", document_id="wiki/runbook.md",
                        char_start=0, char_end=10,
                        capture_digest=f"sha256:{g.digests['wiki']}")])

    # Ingestion runs, with ledgers under TAGGED names — found by pattern or not at all.
    for source, key, run in (("wiki", "wiki", "run-001"), ("memos", "memo", "run-001")):
        run_dir = ingest / source / run
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "report.json").write_text(json.dumps({"source": source, "run_id": run,
                                                         "status": "complete"}))
        (run_dir / "inventory.jsonl").write_text(json.dumps(
            {"id": f"{source}-1", "capture_digest": f"sha256:{g.digests[key]}",
             "downloaded": True}) + "\n")
    excluded = [{"id": "wiki-2", "rule": "blocked_pattern:pricing"},
                {"id": "wiki-3", "rule": "blocked_pattern:pricing"},
                {"id": "wiki-4", "rule": "too_large"}]
    (ingest / "wiki" / "run-001" / "excluded_ocr.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in excluded))
    (ingest / "wiki" / "run-001" / "governance.jsonl").write_text("")
    for row in excluded:
        reason = f"excluded: {row['rule']}"
        g.excluded_reasons[reason] = g.excluded_reasons.get(reason, 0) + 1
    return g
