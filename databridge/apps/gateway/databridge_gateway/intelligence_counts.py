"""Exact `/intelligence` figures for a graph of ANY size, computed in the store (C-4).

**The finding.** The count cards withheld every figure once the graph passed 5,000 nodes or
20,000 relationships ("Graph figures unavailable"). The finance dry run's graph held 10,128 nodes,
so the page showed nothing at all, although nothing about a count gets less true as the graph
grows. The bound existed because `analytics.compute` walks the whole snapshot in Python, reads
every node's pointers with one query each, and returns every figure WITH its member ids, so its
cost and its payload both grew with the graph.

**What this module does instead.** Every number on the page is computed by the store, with SQL
aggregates over the OPEN graph (``system_to IS NULL``): node and edge counts, the per-kind and
per-meta-kind table, documents, the identity figures, coverage gaps, orphans, the issuer mix, the
two rankings (``ORDER BY … LIMIT``). The provenance figures (sources per entity) need each
pointer's LEAF sources: a plain pointer's ``source_id`` is read with ``json_extract`` and grouped
in SQL, so only DISTINCT ``(entity, source)`` pairs leave the database; a ``derived`` pointer is
unwound in Python (its leaves are nested), read in bounded keyset pages.

**What it never does.** It never truncates a count: every figure is the exact number, with its
exact denominator. It does not list the members of a figure (that is what made the payload grow
with the graph); a figure that has members says so (`MEMBERS_NOT_LISTED`), and the graph surface
pages through them. Nothing is written: every statement is a ``SELECT`` (no temp table).

The same vocabulary rules as `analytics.compute` decide which kinds are documents, subjects,
claims, issuers and agreements: they are read from the bound packs, never named here. The two
paths must agree number for number, and `tests/test_intelligence_counts_exact.py` asserts that
on two estates (workspace and finance) whenever both can run.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Iterable, Optional

from databridge_graph.meta import CLAIM_META_KINDS, EdgeMetaKind, NodeMetaKind
from databridge_graph.packs import Vocabulary, active_vocabulary

#: Said on a figure whose members exist but are not listed on this page (the counts are exact).
MEMBERS_NOT_LISTED = ("the count is exact; its members are not listed on this page at this graph "
                      "size. Open the graph surface to page through them")
#: Rows fetched per page when unwinding derived pointers.
PAGE = 2_000
#: How many ids go into one ``IN (…)`` list (SQLite's default variable limit is 999+).
_CHUNK = 500

# The labels `analytics.compute` gives its figures. The equality test compares whole payloads,
# labels included, so a drift between the two is a failing test rather than a quiet mismatch.
LABEL_SUBJECTS = "registry rows a document has claimed"
LABEL_AMBIGUITY = "documents naming more than one candidate"
LABEL_UNLINKED = "documents matching no registry row"
LABEL_CONFLICTS = "registry rows claimed by documents from more than one issuer"
LABEL_UNCOVERED = "referenced by a document, included by no agreement"
LABEL_ORPHANS = "non-subject entities with no relationship"
LABEL_EVIDENCE = "entities whose version-specific evidence could not be read"
LABEL_CROSS = "non-subject entities cited by two or more sources"


def supports_sql(store: Any) -> bool:
    """Only a reader that can answer ``SELECT``s can be counted in the store."""
    return callable(getattr(store, "fetchall", None)) and callable(getattr(store, "fetchone", None))


def _in(column: str, values: Iterable[str]) -> tuple[str, tuple]:
    """``column IN (?, …)`` — or a condition that matches nothing for an empty set (``IN ()`` is
    not SQL, and ``IN (NULL)`` turns ``NOT`` into "unknown")."""
    vals = tuple(sorted(set(values)))
    if not vals:
        return "0", ()
    return f"{column} IN ({','.join('?' for _ in vals)})", vals


def _count(label: str, n: int, of: int) -> dict:
    return {"label": label, "n": int(n), "of": int(of),
            "pct": round((n / of * 100.0) if of else 0.0, 1), "members": []}


def _leaf_sources(pointer: Any) -> set[str]:
    """The source ids a serialised pointer ultimately cites — `analytics._leaf_sources`'s rule
    over the stored JSON: a ``derived`` pointer with children cites its children's sources."""
    if not isinstance(pointer, dict):
        return set()
    children = list(pointer.get("derived_from") or [])
    children.extend(pointer.get("facts") or [])
    if pointer.get("kind") == "derived" and children:
        out: set[str] = set()
        for child in children:
            out |= _leaf_sources(child)
        return out
    sid = pointer.get("source_id")
    return {sid} if isinstance(sid, str) and sid else set()


#: The open graph: every open node version, and every open edge whose two endpoints are open
#: nodes (`analytics.take_snapshot` keeps the same edges and counts the others as dangling).
_LIVE = "live AS (SELECT rowid_pk, id, kind, label, system_from FROM nodes WHERE system_to IS NULL)"
_E = ("E AS (SELECT e.src_id, e.dst_id, e.kind, ls.kind AS sk, ld.kind AS dk FROM edges e "
      "JOIN live ls ON ls.id = e.src_id JOIN live ld ON ld.id = e.dst_id "
      "WHERE e.system_to IS NULL)")


def store_figures(store: Any, *, vocabulary: Optional[Vocabulary] = None,
                  top: Optional[int] = None) -> dict:
    """The `/intelligence` analytics payload for the OPEN graph, every count computed in the
    store. Same shape as `GraphAnalytics.to_dict()`; figure ``members`` are empty (the members
    of `issuer_mix` / `documents_by_source` are kept for naming, then dropped by the caller)."""
    from databridge_graph.analytics import TOP_N

    vocab = vocabulary or active_vocabulary()
    top = TOP_N if top is None else top
    one = lambda sql, p=(): (store.fetchone(sql, p) or [0])[0]          # noqa: E731
    rows = lambda sql, p=(): list(store.fetchall(sql, p))               # noqa: E731
    W = f"WITH {_LIVE}"
    WE = f"WITH {_LIVE}, {_E}"

    def meta_of(kind: str) -> Optional[NodeMetaKind]:
        decl = vocab.node_kinds.get(kind)
        return decl.binds_to if decl is not None else None

    subject_kinds = frozenset(vocab.subject_kinds)
    doc_kinds = {k for k, d in vocab.node_kinds.items() if d.binds_to is NodeMetaKind.DOCUMENT}
    agreement_kinds = {k for k, d in vocab.node_kinds.items()
                       if d.binds_to is NodeMetaKind.AGREEMENT}

    def edge_kinds(meta: EdgeMetaKind) -> set[str]:
        return {n for n, d in vocab.edge_kinds.items() if d.binds_to is meta}

    # --- size, kinds --------------------------------------------------------------------------
    nodes = int(one(f"{W} SELECT COUNT(*) FROM live"))
    open_edges = int(one("SELECT COUNT(*) FROM edges WHERE system_to IS NULL"))
    edges = int(one(f"{WE} SELECT COUNT(*) FROM E"))
    kinds, meta_counts = [], Counter()
    by_kind: dict[str, int] = {}
    for r in rows(f"{W} SELECT kind, COUNT(*) FROM live GROUP BY kind"):
        kind, n = r[0], int(r[1])
        by_kind[kind] = n
        decl = vocab.node_kinds.get(kind)
        meta = decl.binds_to.value if decl is not None else None
        meta_counts[meta or "undeclared"] += n
        kinds.append({"kind": kind, "n": n, "meta": meta,
                      "pack": decl.pack if decl is not None else None,
                      "subject": kind in subject_kinds, "declared": decl is not None})
    kinds.sort(key=lambda k: (-k["n"], k["kind"]))
    documents = sum(n for k, n in by_kind.items() if meta_of(k) is NodeMetaKind.DOCUMENT)
    n_subjects = sum(n for k, n in by_kind.items() if k in subject_kinds)
    rankable_total = nodes - n_subjects

    # --- issuer mix ---------------------------------------------------------------------------
    issued_sql, issued_p = _in("kind", edge_kinds(EdgeMetaKind.ISSUED))
    doc_dk, doc_dk_p = _in("dk", doc_kinds)
    issued_pairs = rows(f"{WE} SELECT DISTINCT src_id, dst_id FROM E WHERE {issued_sql} AND "
                        f"{doc_dk}", issued_p + doc_dk_p)
    issued_docs: dict[str, set[str]] = {}
    for r in issued_pairs:
        issued_docs.setdefault(r[0], set()).add(r[1])

    # --- identity work ------------------------------------------------------------------------
    claim_edge_kinds = {n for n, d in vocab.edge_kinds.items() if d.binds_to in CLAIM_META_KINDS}
    identity_applicable = bool(subject_kinds) and bool(claim_edge_kinds)
    subjects = ambiguity = unlinked = conflicts = None
    if identity_applicable:
        ck, ck_p = _in("kind", claim_edge_kinds)
        sk_doc, sk_doc_p = _in("sk", doc_kinds)
        dk_sub, dk_sub_p = _in("dk", subject_kinds)
        claims = (f"{WE}, C AS (SELECT DISTINCT src_id, dst_id FROM E WHERE {ck} AND {sk_doc} AND "
                  f"{dk_sub})")
        cp = ck_p + sk_doc_p + dk_sub_p
        claimed = int(one(f"{claims} SELECT COUNT(DISTINCT dst_id) FROM C", cp))
        linked = int(one(f"{claims} SELECT COUNT(DISTINCT src_id) FROM C", cp))
        n_ambiguous = int(one(f"{claims} SELECT COUNT(*) FROM (SELECT src_id FROM C GROUP BY src_id "
                              f"HAVING COUNT(*) > 1)", cp))
        claim_src_kinds: set[str] = set()
        for name in claim_edge_kinds:
            decl = vocab.edge_kinds[name]
            if "*" in decl.dst or set(decl.dst) & subject_kinds:
                claim_src_kinds |= set(decl.src)
        claimable_kinds = doc_kinds if "*" in claim_src_kinds else (doc_kinds & claim_src_kinds)
        cl_k, cl_p = _in("kind", claimable_kinds)
        claimable = int(one(f"{W} SELECT COUNT(*) FROM live WHERE {cl_k}", cl_p))
        n_unlinked = int(one(f"{claims} SELECT COUNT(*) FROM live WHERE {cl_k} AND id NOT IN "
                             f"(SELECT src_id FROM C)", cp + cl_p))
        n_conflicts = int(one(
            f"{claims}, I AS (SELECT DISTINCT src_id, dst_id FROM E WHERE {issued_sql} AND {doc_dk}) "
            f"SELECT COUNT(*) FROM (SELECT C.dst_id FROM C JOIN I ON I.dst_id = C.src_id "
            f"GROUP BY C.dst_id HAVING COUNT(DISTINCT I.src_id) > 1)",
            cp + issued_p + doc_dk_p))
        subjects = _count(LABEL_SUBJECTS, claimed, n_subjects)
        ambiguity = _count(LABEL_AMBIGUITY, n_ambiguous, linked)
        unlinked = _count(LABEL_UNLINKED, n_unlinked, claimable)
        conflicts = _count(LABEL_CONFLICTS, n_conflicts, claimed)

    # --- references no agreement includes -----------------------------------------------------
    includable: set[str] = set()
    for decl in vocab.edge_kinds.values():
        if decl.binds_to is not EdgeMetaKind.PART_OF:
            continue
        if not any(meta_of(k) is NodeMetaKind.AGREEMENT for k in decl.src if k != "*"):
            continue
        includable |= {k for k in decl.dst if k != "*" and k in vocab.node_kinds}
    po, po_p = _in("kind", edge_kinds(EdgeMetaKind.PART_OF))
    ag, ag_p = _in("sk", agreement_kinds)
    includable |= {r[0] for r in rows(f"{WE} SELECT DISTINCT dk FROM E WHERE {po} AND {ag}",
                                      po_p + ag_p)}
    uncovered = None
    if includable:
        ref, ref_p = _in("kind", edge_kinds(EdgeMetaKind.REFERENCES))
        sk_doc, sk_doc_p = _in("sk", doc_kinds)
        inc_dk, inc_dk_p = _in("dk", includable)
        n_uncovered = int(one(
            f"{WE} SELECT COUNT(*) FROM (SELECT DISTINCT dst_id FROM E WHERE {ref} AND {sk_doc} "
            f"AND {inc_dk} EXCEPT SELECT dst_id FROM E WHERE {po} AND {ag})",
            ref_p + sk_doc_p + inc_dk_p + po_p + ag_p))
        inc_k, inc_k_p = _in("kind", includable)
        catalogue = int(one(f"{W} SELECT COUNT(*) FROM live WHERE {inc_k}", inc_k_p))
        uncovered = _count(LABEL_UNCOVERED, n_uncovered, catalogue)

    # --- connectedness ------------------------------------------------------------------------
    subj, subj_p = _in("l.kind", subject_kinds)
    degree = (f"{WE}, D AS (SELECT id, SUM(c) AS deg FROM (SELECT src_id AS id, COUNT(*) AS c "
              f"FROM E GROUP BY src_id UNION ALL SELECT dst_id, COUNT(*) FROM E GROUP BY dst_id) "
              f"GROUP BY id)")
    n_orphans = int(one(f"{degree} SELECT COUNT(*) FROM live l LEFT JOIN D ON D.id = l.id "
                        f"WHERE NOT ({subj}) AND D.id IS NULL", subj_p))
    most_connected = [{"id": r[0], "degree": int(r[1])} for r in rows(
        f"{degree} SELECT l.id, D.deg FROM live l JOIN D ON D.id = l.id WHERE NOT ({subj}) "
        f"AND D.deg > 0 ORDER BY D.deg DESC, l.id ASC LIMIT ?", subj_p + (int(top),))]
    recently = [{"id": r[0], "system_from": r[1]} for r in rows(
        f"{W} SELECT l.id, l.system_from FROM live l WHERE NOT ({subj}) "
        f"ORDER BY COALESCE(l.system_from, '') DESC, l.id DESC LIMIT ?", subj_p + (int(top),))]

    # --- provenance structure -----------------------------------------------------------------
    # The pointers of each node's OPEN version (`store.sources_for(id, version_pk=…)`: entity
    # pointers of that version, plus legacy rows recorded with no version).
    scoped = ("FROM sources s JOIN live l ON l.id = s.entity_id WHERE s.field IS NULL AND "
              "(s.version_pk = l.rowid_pk OR s.version_pk IS NULL)")
    cited: dict[str, set[str]] = {}
    unreadable: set[str] = set()
    for r in rows(f"{W} SELECT DISTINCT s.entity_id, json_extract(s.source_pointer_json, "
                  f"'$.source_id') {scoped} AND json_valid(s.source_pointer_json) AND "
                  f"COALESCE(json_extract(s.source_pointer_json, '$.kind'), '') != 'derived'"):
        cited.setdefault(r[0], set())
        if isinstance(r[1], str) and r[1]:
            cited[r[0]].add(r[1])
    for r in rows(f"{W} SELECT DISTINCT s.entity_id {scoped} AND "
                  f"NOT json_valid(s.source_pointer_json)"):
        unreadable.add(r[0])
    # Derived pointers: nested leaves, unwound in Python, in keyset pages so no single read holds
    # every one of them.
    last = 0
    while True:
        page = rows(f"{W} SELECT s.rowid, s.entity_id, s.source_pointer_json {scoped} AND "
                    f"s.rowid > ? AND json_valid(s.source_pointer_json) AND "
                    f"json_extract(s.source_pointer_json, '$.kind') = 'derived' "
                    f"ORDER BY s.rowid LIMIT ?", (last, PAGE))
        if not page:
            break
        for r in page:
            last = r[0]
            try:
                leaves = _leaf_sources(json.loads(r[2]))
            except (TypeError, ValueError):
                unreadable.add(r[1])
                continue
            cited.setdefault(r[1], set()).update(leaves)
        if len(page) < PAGE:
            break
    for entity_id in unreadable:                       # compute: an unreadable entity cites none
        cited[entity_id] = set()
    kind_rows = {}
    for chunk in _chunks(sorted(cited)):
        marks = ",".join("?" for _ in chunk)
        for r in rows(f"SELECT id, kind FROM nodes WHERE system_to IS NULL AND id IN ({marks})",
                      tuple(chunk)):
            kind_rows[r[0]] = r[1]
    by_source_docs: dict[str, set[str]] = {}
    by_source_entities: Counter = Counter()
    n_multi = 0
    for entity_id, sources in cited.items():
        kind = kind_rows.get(entity_id)
        is_doc = kind is not None and meta_of(kind) is NodeMetaKind.DOCUMENT
        for source in sources:
            by_source_entities[source] += 1
            if is_doc:
                by_source_docs.setdefault(source, set()).add(entity_id)
        if len(sources) >= 2 and kind is not None and kind not in subject_kinds:
            n_multi += 1
    documents_by_source = sorted(
        ({"source": s, "documents": len(by_source_docs.get(s, ())),
          "entities": by_source_entities[s], "members": sorted(by_source_docs.get(s, ()))}
         for s in by_source_entities),
        key=lambda s: (-s["documents"], -s["entities"], s["source"]))

    # --- names for the ids the payload mentions -----------------------------------------------
    named = set(issued_docs) | {r["id"] for r in most_connected} | {r["id"] for r in recently}
    entities: dict[str, dict] = {}
    for chunk in _chunks(sorted(named)):
        marks = ",".join("?" for _ in chunk)
        for r in rows(f"SELECT id, kind, label FROM nodes WHERE system_to IS NULL AND id IN "
                      f"({marks})", tuple(chunk)):
            entities[r[0]] = {"id": r[0], "kind": r[1], "label": r[2]}
    mix = [{"issuer_id": oid, "issuer": entities.get(oid, {}).get("label", oid),
            "documents": len(docs), "members": sorted(docs)} for oid, docs in issued_docs.items()]
    mix.sort(key=lambda i: (-i["documents"], i["issuer"]))

    return {
        "nodes": nodes,
        "edges": edges,
        "documents": documents,
        "kinds": kinds,
        "meta_kinds": dict(sorted(meta_counts.items())),
        "subjects": subjects,
        "issuer_mix": mix,
        "ambiguity": ambiguity,
        "unlinked_documents": unlinked,
        "conflicts": conflicts,
        "uncovered_references": uncovered,
        "most_connected": most_connected,
        "recently_ingested": recently,
        "documents_by_source": documents_by_source,
        "cross_source": _count(LABEL_CROSS, n_multi, rankable_total),
        "orphans": _count(LABEL_ORPHANS, n_orphans, rankable_total),
        "evidence_unavailable": _count(LABEL_EVIDENCE, len(unreadable), nodes),
        "dangling_edges": open_edges - edges,
        "as_of": None,
        "snapshot_basis": "open",
        "vocabulary": {"packs": list(vocab.packs), "subject_kinds": sorted(subject_kinds),
                       "claim_edge_kinds": sorted(claim_edge_kinds),
                       "identity_figures": identity_applicable},
        "entities": entities,
    }


def _chunks(ids: list[str]) -> Iterable[list[str]]:
    for i in range(0, len(ids), _CHUNK):
        yield ids[i:i + _CHUNK]


def unlist_members(payload: dict, count_figures: Iterable[str]) -> dict:
    """Drop every member list from a figures payload, keeping every number exact.

    A figure that has members (``n > 0``) and was not already withholding them says so; the
    issuer and source rows keep their counts and lose their id lists. Applied AFTER the naming
    pass, so a source or issuer is still named by the same rule as on a small graph."""
    out = dict(payload)
    for key in count_figures:
        fig = out.get(key)
        if not isinstance(fig, dict):
            continue
        fig = dict(fig)
        if fig.get("members") or fig.get("n"):
            fig.setdefault("members_withheld", MEMBERS_NOT_LISTED)
        fig["members"] = []
        out[key] = fig
    out["issuer_mix"] = [dict(r, members=[]) for r in out.get("issuer_mix") or []]
    out["documents_by_source"] = [dict(r, members=[]) for r in out.get("documents_by_source") or []]
    out["members_listed"] = False
    out["members_note"] = MEMBERS_NOT_LISTED
    return out


__all__ = ["MEMBERS_NOT_LISTED", "store_figures", "supports_sql", "unlist_members"]
