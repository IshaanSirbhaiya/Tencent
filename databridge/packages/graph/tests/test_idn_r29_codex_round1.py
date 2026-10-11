"""IDN, Codex round 1 on R-029: hard constraint 1 (a person is never merged) on EVERY path.

1. (Critical) two records of a person kind that carry the same grounded identifier are never
   merged by it: an identifier proposes a candidate for a named human, it never joins.
2. (Critical) person protection is FAIL-CLOSED: an entity-bound kind is a person unless its pack
   says `person: false` — a pack that forgets, or that never names the kind as an actor, cannot
   switch the protection off by omission.
3. (High) an upsert of any person kind reports `requires_human_selection`.
4. (High) a prefix join is refused while any OTHER open record begins with the same words.
5. (High) a merge's `merged_record_ids` cites every pointer that grounds the ids it lists.
6. (Medium) a clash between an identity rule and a structurally person-like kind is a PackError.
7. (Medium) one extraction reads one vocabulary snapshot.
"""
from __future__ import annotations

import datetime as dt
import textwrap

import pytest

from databridge_core import SourcePointer
from databridge_graph.packs import PackError, build_vocabulary, clear_caches, deployment

NOW = dt.datetime(2026, 10, 5, tzinfo=dt.timezone.utc)


def _cell(rid, col="id", src="db", table="t"):
    return SourcePointer(source_id=src, kind="table_cell", table=table, column=col, row_id=rid)


def _node(kind, nid, label, *, key=None, src="db", attrs=None, attr_sources=None, sources=None):
    from databridge_graph.kinds import NodeKind
    from databridge_graph.schema import GraphNode

    a_src = dict(attr_sources or {})
    if key is not None:
        a_src.setdefault("canonical_id", _cell(nid, "key", src))
    return GraphNode(id=nid, kind=NodeKind(kind), label=label, canonical_id=key,
                     attrs=dict(attrs or {}), attr_sources=a_src,
                     sources=tuple(sources or (_cell(nid, "id", src),)),
                     valid_from=NOW, system_from=NOW)


def _pack(root, name, body):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "ontology.yaml").write_text(textwrap.dedent(body), encoding="utf-8")


@pytest.fixture
def store(tmp_path):
    from databridge_graph.store import GraphStore

    return GraphStore(str(tmp_path / "g.db"))


# --- 1 ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("pack", ["workspace", "finance", "government", "healthcare"])
def test_1_a_shared_identifier_never_merges_two_person_records(pack, store):
    from databridge_graph.merge import resolve_existing, upsert_node

    clear_caches()
    with deployment(pack) as vocab:
        kinds = sorted(vocab.person_kinds & set(vocab.node_kinds))
        assert kinds
        for kind in kinds:
            a = _node(kind, f"{kind}:a", "Rowan Achterberg", key="P123", src="register-a")
            b = _node(kind, f"{kind}:b", "Rowan Achterberg", key="P123", src="register-b")
            upsert_node(store, a, vocab, id_only=True)
            row, how = resolve_existing(store, b, vocab)
            assert row is None, (kind, how)
            out = upsert_node(store, b, vocab)
            assert out.action == "inserted" and out.id == b.id
            assert out.requires_human_selection is True
            # re-sighting the SAME record is still bookkeeping
            again = upsert_node(store, a, vocab)
            assert again.id == a.id and again.action == "corroborated"
    clear_caches()


# --- 2 ---------------------------------------------------------------------------------------------
SILENT = """
    pack: silent
    version: "1.0"
    node_kinds:
      - name: employee
        binds_to: entity
      - name: gizmo
        binds_to: entity
        person: false
      - name: firm
        binds_to: organisation
      - name: memo
        binds_to: document
    edge_kinds:
      - name: about
        binds_to: about
        src: [memo]
        dst: [employee, gizmo, firm]
      - name: references
        binds_to: references
        src: ["*"]
        dst: ["*"]
"""


@pytest.fixture
def silent(tmp_path, monkeypatch):
    root = tmp_path / "packs"
    _pack(root, "silent", SILENT)
    monkeypatch.setenv("DATABRIDGE_PACKS_ROOT", str(root))
    clear_caches()
    with deployment("silent") as vocab:
        yield vocab
    clear_caches()


def test_2_an_entity_kind_is_a_person_unless_its_pack_says_otherwise(silent, store):
    """`employee` appears only as a destination and under a wildcard: no topology marks it, and
    no subject declares it. It is protected all the same; `gizmo`, declared `person: false`, and
    the organisation are not."""
    from databridge_graph.merge import name_prefix_join, resolve_existing, upsert_node

    vocab = silent
    assert vocab.is_person("employee") and "employee" in vocab.never_fuzzy_merge
    assert not vocab.is_person("gizmo") and not vocab.is_person("firm")
    upsert_node(store, _node("employee", "employee:e-1", "Yolanda Delacroix", key="E-1"),
                vocab, id_only=True)
    mention = _node("employee", "employee:yolanda delacroix", "Yolanda Delacroix",
                    sources=(SourcePointer(source_id="f", kind="doc_span", document_id="d",
                                           char_start=0, char_end=17),))
    assert resolve_existing(store, mention, vocab) == (None, "")
    assert name_prefix_join(store, mention.model_copy(update={"label": "Yolanda"}), vocab) is None
    assert upsert_node(store, mention, vocab).requires_human_selection is True
    # control: a declared non-person entity still merges by name similarity
    upsert_node(store, _node("gizmo", "gizmo:g-1", "Flux Capacitor", key="G-1"), vocab,
                id_only=True)
    row, how = resolve_existing(store, _node("gizmo", "gizmo:flux capacitor", "Flux Capacitor"),
                                vocab)
    assert row is not None and how == "fuzzy"


def test_2_a_pack_may_not_mark_a_person_kind_non_person(tmp_path):
    _pack(tmp_path, "liar", """
        pack: liar
        version: "1.0"
        node_kinds:
          - {name: clerk, binds_to: entity, person: false}
          - {name: office, binds_to: organisation}
        edge_kinds:
          - {name: works_at, binds_to: role_of, src: [clerk], dst: [office]}
    """)
    with pytest.raises(PackError, match="person"):
        build_vocabulary(["liar"], tmp_path)
    _pack(tmp_path, "orgperson", """
        pack: orgperson
        version: "1.0"
        node_kinds:
          - {name: office, binds_to: organisation, person: true}
        edge_kinds: []
    """)
    with pytest.raises(PackError, match="person"):
        build_vocabulary(["orgperson"], tmp_path)
    _pack(tmp_path, "subj", """
        pack: subj
        version: "1.0"
        node_kinds:
          - {name: member, binds_to: entity, person: false}
        edge_kinds: []
        subject_kinds:
          - {kind: member, identifiers: [member_no], attestation: in_person,
             grant_type: MemberReadGrant}
    """)
    with pytest.raises(PackError, match="person"):
        build_vocabulary(["subj"], tmp_path)


# --- 3 ---------------------------------------------------------------------------------------------
def test_3_every_upsert_of_a_person_kind_requires_human_selection(silent, store):
    from databridge_graph.merge import upsert_node

    vocab = silent
    assert not vocab.is_subject("employee")
    n = _node("employee", "employee:e-9", "Ines Marchetti-Holm", key="E-9")
    assert upsert_node(store, n, vocab, id_only=True).requires_human_selection is True
    assert upsert_node(store, n, vocab, id_only=True).requires_human_selection is True


# --- 4 ---------------------------------------------------------------------------------------------
def test_4_a_prefix_join_is_refused_while_another_record_begins_so(store):
    from databridge_graph.merge import name_prefix_join, upsert_node

    clear_caches()
    with deployment("workspace") as vocab:
        org = next(k for k in vocab.node_kinds if not vocab.is_person(k)
                   and vocab.node_kinds[k].binds_to.value == "organisation")
        rec = _node(org, f"{org}:c-1002", "Corriedale Grocers Co-operative", key="C-1002",
                    attrs={"name": "Corriedale Grocers Co-operative"},
                    attr_sources={"name": _cell("C-1002", "name")})
        upsert_node(store, rec, vocab, id_only=True)
        short = _node(org, f"{org}:corriedale", "Corriedale")
        assert name_prefix_join(store, short, vocab) is not None          # control
        # another open record that begins "Corriedale": the short form is ambiguous now
        upsert_node(store, _node(org, f"{org}:corriedale grocers", "Corriedale Grocers"), vocab,
                    id_only=True)
        assert name_prefix_join(store, short, vocab) is None
    clear_caches()


# --- 5 ---------------------------------------------------------------------------------------------
def test_5_merged_record_ids_cite_every_pointer_behind_the_ids(store):
    from databridge_graph.merge import MERGED_IDS_ATTR, _absorb
    from databridge_graph.packs import IdentityRule
    from databridge_graph.store import row_to_node
    from databridge_graph.merge import upsert_node

    clear_caches()
    with deployment("finance") as vocab:
        kind = next(iter(vocab.identity_rules))
        rule = vocab.identity_rules[kind]
        assert isinstance(rule, IdentityRule)
        prior_ptr = SourcePointer(source_id="earlier", kind="derived",
                                  derived_from=[_cell("X-0", src="earlier")], derivation="prior")
        surv = _node(kind, f"{kind}:s", "Fairmont Reach Property LLC", key="S-1")
        absd = _node(kind, f"{kind}:a", "Fairmont Reach Property LLC", key="A-1",
                     attrs={MERGED_IDS_ATTR: [f"{kind}:x0"]},
                     attr_sources={MERGED_IDS_ATTR: prior_ptr},
                     sources=(_cell("a-row", "id", "crm"), _cell("a-row", "name", "crm")))
        for n in (surv, absd):
            upsert_node(store, n, vocab, id_only=True)
        _absorb(store, surv, [absd], rule)
        node = row_to_node(store, store.open_node_row(surv.id))
        assert node.attrs[MERGED_IDS_ATTR] == [absd.id, f"{kind}:x0"]
        basis = node.attr_sources[MERGED_IDS_ATTR].derived_from
        for p in (absd.attr_sources["canonical_id"], prior_ptr, *absd.sources):
            assert p in basis, p
    clear_caches()


# --- 6 ---------------------------------------------------------------------------------------------
def test_6_a_rule_on_a_kind_another_pack_makes_a_person_is_a_pack_error(tmp_path):
    _pack(tmp_path, "rules", """
        pack: rules
        version: "1.0"
        node_kinds:
          - {name: holder, binds_to: entity, person: false}
        edge_kinds: []
        identity_rules:
          - {kind: holder, business_keys: [holder_no], exact_name: true, similarity_merge: false}
    """)
    _pack(tmp_path, "roles", """
        pack: roles
        version: "1.0"
        node_kinds:
          - {name: holder, binds_to: entity}
          - {name: desk, binds_to: organisation}
        edge_kinds:
          - {name: sits_at, binds_to: role_of, src: [holder], dst: [desk]}
    """)
    build_vocabulary(["rules"], tmp_path)                      # alone: fine
    with pytest.raises(PackError, match="natural persons"):
        build_vocabulary(["rules", "roles"], tmp_path)


# --- 7 ---------------------------------------------------------------------------------------------
def test_7_one_extraction_reads_one_vocabulary_snapshot(monkeypatch):
    import databridge_graph.extractor as ex
    from databridge_convert.ir import Block, DocumentRecord
    from databridge_core.provenance import SnapshotIdentity

    calls = []
    real = ex.active_vocabulary

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    class _Model:
        def complete_structured(self, task, messages, schema, seed=None, source_id=None):
            class R:
                parsed = schema.model_validate({"entities": [], "relations": []})
            return R()

    clear_caches()
    with deployment("workspace"):
        monkeypatch.setattr(ex, "active_vocabulary", counting)
        doc = DocumentRecord(document_id="d", sha256="0" * 64,
                             snapshot=SnapshotIdentity(source_id="s", as_of=NOW,
                                                       content_hash="0" * 64, schema_hash=""),
                             blocks=[Block(kind="Paragraph", text="nothing here")])
        ex.extract_graph(doc, _Model(), store=None, valid_from=NOW, system_from=NOW)
    clear_caches()
    assert len(calls) == 1
