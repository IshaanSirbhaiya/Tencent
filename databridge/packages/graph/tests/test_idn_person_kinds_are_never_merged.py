"""IDN (E2E run 5, F-04): a kind whose records are natural persons is never merged, in any pack.

Run 5 found 72 finance `contact` nodes (CRM people) that had absorbed email mentions BY NAME: the
pack had not declared `contact` a subject, so the engine's name-similarity step merged "Yolanda
Delacroix" in an email into whichever CRM contact of that name it met first. The pack's own comment
warned that a kind omitted from `subject_kinds` loses its protection silently.

Two walls, both tested here:
  1. every pack declares each person-like kind a subject (the pack audit);
  2. the engine never merges a person-like kind by similarity, prefix or identity rule even when a
     pack forgets (`Vocabulary.person_kinds`), and refuses an identity rule on one.

"Person-like" is derived STRUCTURALLY from the raw pack files — by this test's own statement of the
rule, not imported from the code under test: an `entity`-bound kind the pack names explicitly as
the actor (`src`) of a `role_of` or `issued` edge. Kind names are never read for meaning.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml

from databridge_graph.packs import (DEFAULT_PACKS_ROOT, PackError, available_packs,
                                    clear_caches, deployment)

FIN = Path(__file__).resolve().parents[3] / "fixtures" / "packs" / "finance"
ACTOR_EDGE_METAS = {"role_of", "issued"}


def _raw(pack: str) -> dict:
    return yaml.safe_load((DEFAULT_PACKS_ROOT / pack / "ontology.yaml").read_text(encoding="utf-8"))


def _person_like(data: dict) -> set[str]:
    """The test's own derivation: an entity that holds a role or issues an artefact."""
    binds = {n["name"]: n["binds_to"] for n in data.get("node_kinds") or []}
    out = set()
    for e in data.get("edge_kinds") or []:
        if e.get("binds_to") in ACTOR_EDGE_METAS:
            out |= {k for k in e.get("src") or [] if binds.get(k) == "entity"}
    return out


def _subjects(data: dict) -> set[str]:
    return {s["kind"] for s in data.get("subject_kinds") or []}


PACKS = tuple(p for p in available_packs() if (DEFAULT_PACKS_ROOT / p / "ontology.yaml").exists())


def test_every_pack_declares_its_person_kinds_subjects():
    """A pack that declares subjects at all must declare EVERY person-like kind one. (A pack that
    declares none is a vocabulary library: the next test shows the engine still protects it.)"""
    found, missing = 0, {}
    for pack in PACKS:
        data = _raw(pack)
        persons = _person_like(data)
        found += len(persons)
        if _subjects(data):
            gap = persons - _subjects(data)
            if gap:
                missing[pack] = sorted(gap)
    assert not missing, f"person-like kinds not declared subjects: {missing}"
    # non-vacuous: the rule finds people in most packs, including every declared human subject
    assert found >= len(PACKS)


@pytest.mark.parametrize("pack", PACKS)
def test_the_engine_treats_every_person_like_kind_as_a_person_even_undeclared(pack):
    clear_caches()
    with deployment(pack) as vocab:
        persons = _person_like(_raw(pack))
        assert persons <= set(vocab.person_kinds)
        assert persons <= set(vocab.never_fuzzy_merge)
        assert all(vocab.identity_rule_for(k) is None for k in persons)
        # an organisation-bound kind is never a person by this rule
        orgs = {n["name"] for n in _raw(pack)["node_kinds"] if n["binds_to"] == "organisation"}
        assert not (orgs - set(_subjects(_raw(pack)))) & set(vocab.person_kinds)
    clear_caches()


# --- the engine guard, on a pack that FORGETS (finance as run 5 had it) ----------------------------
def _contact_rows() -> list[dict]:
    sql = (FIN / "crm" / "seed" / "03_contact_01.sql").read_text(encoding="utf-8")
    cols = [c.strip() for c in re.search(r"INSERT INTO crm\.contact \(([^)]*)\)", sql).group(1)
            .split(",")]
    rows = []
    for t in re.findall(r"^\s*\((.*)\),?;?\s*$", sql, re.M):
        vals = [None if v == "NULL" else v.strip("'")
                for v in re.findall(r"'[^']*'|NULL|TRUE|FALSE", t)]
        rows.append(dict(zip(cols, vals)))
    return rows


@pytest.fixture
def forgetful_finance(tmp_path, monkeypatch):
    """The finance pack with `contact` and `relationship_manager` NOT declared subjects — the
    declaration run 5 ingested with."""
    root = tmp_path / "packs"
    shutil.copytree(DEFAULT_PACKS_ROOT / "finance", root / "finance")
    data = _raw("finance")
    persons = _person_like(data)
    keep = [s for s in data["subject_kinds"] if s["kind"] not in {"contact", "relationship_manager"}]
    data["subject_kinds"] = keep
    (root / "finance" / "ontology.yaml").write_text(yaml.safe_dump(data, sort_keys=False),
                                                    encoding="utf-8")
    monkeypatch.setenv("DATABRIDGE_PACKS_ROOT", str(root))
    clear_caches()
    with deployment("finance") as vocab:
        undeclared = persons - set(vocab.subject_kinds)
        assert undeclared, "the fixture must reproduce an undeclared person kind"
        yield vocab, sorted(undeclared)
    clear_caches()


def test_the_engine_protects_an_undeclared_person_kind(forgetful_finance):
    """Never merged by similarity, never given an identity rule — declared a subject or not."""
    vocab, undeclared = forgetful_finance
    assert set(undeclared) <= set(vocab.never_fuzzy_merge)
    assert all(vocab.is_person(k) and vocab.identity_rule_for(k) is None for k in undeclared)


def _node(kind, nid, label, *, canonical=None, src="crm"):
    import datetime as dt

    from databridge_core import SourcePointer
    from databridge_graph.kinds import NodeKind
    from databridge_graph.schema import GraphNode

    now = dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc)
    p = SourcePointer(source_id=src, kind="table_cell", table="t", column="c", row_id=nid)
    return GraphNode(id=nid, kind=NodeKind(kind), label=label, canonical_id=canonical,
                     attr_sources={"canonical_id": p} if canonical else {},
                     sources=(p,), valid_from=now, system_from=now)


def test_f04_a_mention_of_a_person_is_never_merged_into_a_registry_person(
        forgetful_finance, tmp_path):
    """The run-5 repro: CRM contacts first, then an email names one of them. Before the fix the
    mention fuzzy-merged (score 1.0) into the first same-named CRM contact."""
    from databridge_graph.merge import name_prefix_join, resolve_existing, upsert_node
    from databridge_graph.store import GraphStore

    vocab, undeclared = forgetful_finance
    kind = "contact" if "contact" in undeclared else undeclared[0]
    store = GraphStore(str(tmp_path / "g.db"))
    rows = _contact_rows()
    by_name: dict[str, list[dict]] = {}
    for r in rows:
        by_name.setdefault(f"{r['first_name']} {r['last_name']}", []).append(r)
    for r in rows[:300]:
        name = f"{r['first_name']} {r['last_name']}"
        upsert_node(store, _node(kind, f"{kind}:{r['crm_contact_id'].lower()}", name,
                                 canonical=r["crm_contact_id"]), vocab, id_only=True)
    named = [n for n, rs in by_name.items() if any(r in rows[:300] for r in rs)]
    assert len(named) >= 50                                              # non-vacuous
    for name in named:
        mention = _node(kind, f"{kind}:{name.lower()}", name, src="mailbox")
        row, how = resolve_existing(store, mention, vocab)
        assert row is None, (name, how)
        assert name_prefix_join(store, mention.model_copy(update={
            "label": name.split()[0]}), vocab) is None
        out = upsert_node(store, mention, vocab)
        assert out.action == "inserted" and out.id == mention.id


def test_an_identity_rule_on_a_person_like_kind_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "packs"
    shutil.copytree(DEFAULT_PACKS_ROOT / "workspace", root / "workspace")
    data = _raw("workspace")
    data["subject_kinds"] = []                      # forget the subject, keep the structure
    person = sorted(_person_like(data))[0]
    data["identity_rules"] = [{"kind": person, "business_keys": ["email"], "exact_name": True,
                               "similarity_merge": False}]
    (root / "workspace" / "ontology.yaml").write_text(yaml.safe_dump(data, sort_keys=False),
                                                      encoding="utf-8")
    monkeypatch.setenv("DATABRIDGE_PACKS_ROOT", str(root))
    clear_caches()
    try:
        with pytest.raises(PackError, match="natural persons"):
            with deployment("workspace"):
                pass
    finally:
        clear_caches()


def test_f04_the_extractor_scopes_an_undeclared_person_mention_to_its_span(forgetful_finance):
    """Before the fix an undeclared person kind got the id `kind:<name>`, which every document
    naming the person shares, and was upserted with the similarity step on."""
    import datetime as dt

    from databridge_convert.ir import Block, DocumentRecord
    from databridge_core.provenance import SnapshotIdentity
    from databridge_graph.extractor import extract_graph

    vocab, undeclared = forgetful_finance
    kind = undeclared[0]
    name = next(f"{r['first_name']} {r['last_name']}" for r in _contact_rows())
    text = f"Our new authorised signatory is {name}."

    class _Model:
        def complete_structured(self, task, messages, schema, seed=None, source_id=None):
            at = text.index(name)

            class R:
                parsed = schema.model_validate({"entities": [
                    {"name": name, "kind": kind, "char_start": at, "char_end": at + len(name)}]})
            return R()

    now = dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc)
    doc = DocumentRecord(document_id="msg-1", sha256="0" * 64,
                         snapshot=SnapshotIdentity(source_id="mailbox", as_of=now,
                                                   content_hash="0" * 64, schema_hash=""),
                         blocks=[Block(kind="Paragraph", text=text)])
    out = extract_graph(doc, _Model(), store=None, valid_from=now, system_from=now)
    ents = [n for n in out.entities if str(n.kind) == kind]
    assert len(ents) == 1 and "@msg-1#" in ents[0].id and ents[0].canonical_id is None


def test_every_shipped_entity_kind_is_classified_explicitly():
    """R-029 Codex round 1: in a pack that declares subjects, every entity-bound kind is either a
    subject or carries an explicit `person: false` — none is left to the fail-closed default."""
    unclassified, found = {}, 0
    for pack in PACKS:
        data = _raw(pack)
        if not _subjects(data):
            continue
        for n in data["node_kinds"]:
            if n["binds_to"] != "entity":
                continue
            found += 1
            if n["name"] not in _subjects(data) and n.get("person") is not False:
                unclassified.setdefault(pack, []).append(n["name"])
    assert found and not unclassified, unclassified
