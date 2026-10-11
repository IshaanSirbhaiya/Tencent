"""R-027 DSD — codex round 1 findings, each reproduced on BOTH estates before its fix.

1 (Critical) a design approval was a mutable JSON file: it could be forged.
2 (Critical) stale writes could restore a revoked approval; nothing was re-checked before publication.
3 (High)     a partial documents read became an authoritative count.
4 (High)     base rows could be dropped silently.
5 (High)     an invented label (an aggregate, a count, several "label" roles) was accepted.
6 (High)     `str(key)` joins: 1 and "1" collided; several matches were resolved by overwrite.
7 (High)     an unrelated edit cleared open questions.
8 (Medium)   an incomplete source reference picked the first table of that name.

Same estates as `test_dsd_delegated_datasets.py` (real seeds, real facts stores, a mapping approved
by an authenticated person, captured documents). Expectations are derived from them.
"""
from __future__ import annotations

import copy
import json

import pytest

import dsd_estates as de
from test_dsd_delegated_datasets import _env, _proposed, _csv, _receipt, estate  # noqa: F401


def _approved(estate, *, model=False, request=None):
    m, svc, doc, _ = _proposed(estate, model=model, request=request)
    doc = m.approve_design(doc["id"], digest=doc["digest"], principal=de.issued_principal(),
                           by="staff")
    return m, svc, doc


def _adm():
    from databridge_federated import CatalogueAdmission
    return CatalogueAdmission("staff")


# ------------------------------------------------------------------------------------------------
# 1. approvals are SEALED events in an append-only chain; a forged one is never honoured
# ------------------------------------------------------------------------------------------------
def test_f1_a_forged_or_replayed_approval_is_never_honoured(estate):  # noqa: F811
    from databridge_materialise.delegated import DesignRefused
    m, svc, doc, _ = _proposed(estate, model=False)
    d = m.out_root / "designs" / doc["id"]
    log = d / "events.jsonl"
    clean = log.read_bytes()
    # (a) a hand-written "approved" line (what the old store accepted as an approval)
    forged = {"action": "approved", "design_id": doc["id"], "rev": doc["rev"],
              "digest": doc["digest"], "principal_id": "mallory",
              "authenticated_by": "gateway", "seq": 2,
              "prev": json.loads(clean.decode().splitlines()[-1])["record_digest"]}
    # an attacker can compute the record digest (it is a plain sha256); not the seal (keyed)
    from databridge_materialise.delegated import DesignStore
    forged["at"] = "2026-10-02T00:00:00+00:00"
    forged["record_digest"] = DesignStore._record_digest(forged)
    forged["seal"] = "f" * 64
    log.write_bytes(clean + (json.dumps(forged) + "\n").encode())
    v = m.design(doc["id"], by="staff")
    assert v["status"] == "integrity_failed" and v["approval"] is None
    with pytest.raises(DesignRefused) as e:
        m.build_design(doc["id"], digest=doc["digest"], by="staff", service=svc, admission=_adm(), actor=de.build_actor())
    assert e.value.code == "DESIGN_INTEGRITY_FAILED"
    log.write_bytes(clean)
    # (b) a genuine approval line REPLAYED after an edit (seal valid, chain broken)
    ok = m.approve_design(doc["id"], digest=doc["digest"], principal=de.issued_principal(),
                          by="staff")
    approval_line = log.read_bytes().decode().splitlines()[-1]
    cols = ok["design"]["columns"][:-2]
    m.edit_design(doc["id"], {"columns": cols}, by="staff", service=svc, rev=ok["rev"])
    log.write_bytes(log.read_bytes() + (approval_line + "\n").encode())
    assert m.design(doc["id"], by="staff")["status"] == "integrity_failed"
    with pytest.raises(DesignRefused) as e:
        m.build_design(doc["id"], digest=ok["digest"], by="staff", service=svc, admission=_adm(), actor=de.build_actor())
    assert e.value.code == "DESIGN_INTEGRITY_FAILED"
    # (c) the old attack: a mutable state file saying "approved" — not an approval
    (m.out_root / "designs" / f"{doc['id']}.json").write_text(json.dumps(
        {**ok, "status": "approved"}), encoding="utf-8")
    log.write_bytes(log.read_bytes().rsplit(b"\n", 2)[0] + b"\n")   # drop the replayed line
    v = m.design(doc["id"], by="staff")
    assert v["status"] == "edited" and v["approval"] is None
    # (d) a revision rewritten on disk no longer hashes to the digest its sealed event names
    rp = d / f"rev-{v['rev']}.json"
    raw = json.loads(rp.read_text("utf-8"))
    raw["design"]["grain_text"] += " (tampered)"
    rp.write_text(json.dumps(raw), encoding="utf-8")
    assert m.design(doc["id"], by="staff")["status"] == "integrity_failed"


def test_f1_the_seal_needs_the_deployment_key(estate, monkeypatch):  # noqa: F811
    """The same log read under another key does not verify: the seal is the key's, not the
    file's."""
    m, _svc, doc, _ = _proposed(estate, model=False)
    assert m.design(doc["id"], by="staff")["integrity"] == "verified"
    monkeypatch.setenv("WALNUT_APPROVAL_KEY", "another-deployment-key-0123456789abcdef")
    # not one line verifies, so not even its owner is known: it is not served to anyone
    assert m.design(doc["id"], by="staff") is None
    assert m.designs(by="staff") == [] or all(x["id"] != doc["id"]
                                              for x in m.designs(by="staff"))


# ------------------------------------------------------------------------------------------------
# 2. compare-and-set revisions; a build never restores state; re-checked before publication
# ------------------------------------------------------------------------------------------------
def test_f2_stale_writes_and_mid_build_edits_cannot_restore_approval(estate, monkeypatch):  # noqa: F811
    import databridge_materialise.delegated as dl
    from databridge_materialise.delegated import DesignRefused
    m, svc, doc = _approved(estate)
    # an edit made on a superseded revision is refused (compare-and-set)
    cols = doc["design"]["columns"][:-2]
    m.edit_design(doc["id"], {"columns": cols}, by="staff", service=svc, rev=doc["rev"])
    with pytest.raises(DesignRefused) as e:
        m.edit_design(doc["id"], {"columns": cols[:-1]}, by="staff", service=svc,
                      rev=doc["rev"])
    assert e.value.code == "STALE_REVISION"
    # approve the new revision, then EDIT IT WHILE IT IS BEING BUILT
    cur = m.design(doc["id"], by="staff")
    ok = m.approve_design(doc["id"], digest=cur["digest"], principal=de.issued_principal(),
                          by="staff", rev=cur["rev"])
    real = dl.build_raw

    def edit_midway(*a, **k):
        out = real(*a, **k)
        m.edit_design(doc["id"], {"columns": ok["design"]["columns"][:-1]}, by="staff",
                      service=svc, rev=ok["rev"])
        return out
    monkeypatch.setattr(dl, "build_raw", edit_midway)
    before = set(p.name for p in m.out_root.iterdir())
    with pytest.raises(DesignRefused) as e:
        m.build_design(doc["id"], digest=ok["digest"], by="staff", service=svc,
                       admission=_adm(), actor=de.build_actor())
    assert e.value.code == "DESIGN_NOT_APPROVED"
    after = set(p.name for p in m.out_root.iterdir())
    assert {n for n in after - before if not n.startswith(".")} == set(), "nothing published"
    v = m.design(doc["id"], by="staff")
    assert v["status"] == "edited" and v["approval"] is None and v["rev"] == ok["rev"] + 1
    # a completed build is an EVENT; it never rewrites the design's state
    monkeypatch.setattr(dl, "build_raw", real)
    cur = m.design(doc["id"], by="staff")
    ok2 = m.approve_design(doc["id"], digest=cur["digest"], principal=de.issued_principal(),
                           by="staff")
    m.build_design(doc["id"], digest=ok2["digest"], by="staff", service=svc, admission=_adm(), actor=de.build_actor())
    v2 = m.design(doc["id"], by="staff")
    assert v2["rev"] == ok2["rev"] and v2["status"] == "approved" and len(v2["builds"]) == 1
    assert [h["action"] for h in v2["history"]][-1] == "built"


# ------------------------------------------------------------------------------------------------
# 3. document counts only from an exhaustive read, and the resolver re-runs the full selection
# ------------------------------------------------------------------------------------------------
def test_f3_a_partial_document_read_is_never_a_count(estate):  # noqa: F811
    from databridge_core import SourcePointer
    from databridge_materialise.delegated import documents_count_pointer
    m, svc, doc = _approved(estate, model=False)
    res = m.build_design(doc["id"], digest=doc["digest"], by="staff", service=svc,
                         admission=_adm(), actor=de.build_actor())
    d = doc["design"]
    docs = [c for c in d["columns"] if c["via"] and next(
        lk for lk in d["links"] if lk["id"] == c["via"])["kind"] == "documents"]
    assert docs
    rec = _receipt(m, res)["receipt"]
    side = m.out_root / res["plan_hash"] / rec["pointer_manifest"]["uri"]
    cells = [json.loads(x) for x in side.read_text("utf-8").splitlines()]
    counted = [c for c in cells if c["column"] in {x["name"] for x in docs}
               and c["state"] == "grounded"]
    assert counted, "the estate links documents to some rows"
    reader = m.reader(principal="staff")
    # the full pointer resolves; the same count over FEWER documents (a partial read) does not
    many = max(counted, key=lambda c: len(c["pointer"]["derived_from"]))
    full = SourcePointer.model_validate(many["pointer"])
    assert reader.resolve(full) == len(full.derived_from)
    p = json.loads(full.predicate)
    if len(full.derived_from) > 1:
        partial = documents_count_pointer(
            connection=p["connection"], link=p["link"], value=p["value"],
            leaves=list(full.derived_from[:-1]), entity_kind=p["entity_kind"],
            doc_kinds=tuple(p["doc_kinds"]), edge_kinds=tuple(p["edge_kinds"]))
        assert reader.resolve(partial) is None
    # a one-document claim about a row that has MORE linked documents does not resolve either
    lone = documents_count_pointer(
        connection=p["connection"], link=p["link"], value=p["value"],
        leaves=[full.derived_from[0]], entity_kind=p["entity_kind"],
        doc_kinds=tuple(p["doc_kinds"]), edge_kinds=tuple(p["edge_kinds"]))
    assert (reader.resolve(lone) is None) == (len(full.derived_from) > 1)
    # a RANKED text search is not a census: the same design with a passage filter emits no count
    lk = next(lk for lk in d["links"] if lk["kind"] == "documents")
    word = sorted({w for dd in estate.docs for w in dd["text"].split() if w.isalpha()
                   and len(w) > 4}, key=lambda w: -sum(w in dd["text"] for dd in estate.docs))[0]
    links = [({**x, "text_contains": word} if x["id"] == lk["id"] else x) for x in d["links"]]
    ed = m.edit_design(doc["id"], {"links": links}, by="staff", service=svc, rev=doc["rev"])
    assert next(x for x in ed["design"]["links"] if x["id"] == lk["id"])["text_contains"] == word
    ok = m.approve_design(doc["id"], digest=ed["digest"], principal=de.issued_principal(),
                          by="staff")
    res2 = m.build_design(doc["id"], digest=ok["digest"], by="staff", service=svc,
                          admission=_adm(), actor=de.build_actor())
    rec2 = _receipt(m, res2)["receipt"]
    for c in rec2["columns"]:
        if c["via"] == lk["id"]:
            assert c["grounded"] == 0 and any("not exhaustive" in why for why in c["unmatched"])


# ------------------------------------------------------------------------------------------------
# 4. base rows are never dropped silently
# ------------------------------------------------------------------------------------------------
def test_f4_a_base_row_the_delegate_could_not_use_refuses_the_build(estate, monkeypatch):  # noqa: F811
    from databridge_materialise.delegated import DesignRefused
    m, svc, doc = _approved(estate)
    real = svc.run_subtask

    def excluding(task, **kw):
        r = real(task, **kw)
        if task.id == "base":
            cov = r.coverage.model_copy(update={"excluded": (
                "1 row(s) came back without a pointer for every cell and were not used",)})
            return r.model_copy(update={"rows": r.rows[1:], "coverage": cov})
        return r
    monkeypatch.setattr(svc, "run_subtask", excluding)
    with pytest.raises(DesignRefused) as e:
        m.build_design(doc["id"], digest=doc["digest"], by="staff", service=svc,
                       admission=_adm(), actor=de.build_actor())
    assert e.value.code == "BASE_INCOMPLETE"

    def dropping(task, **kw):                 # a row lost WITHOUT a word: the count proves it
        r = real(task, **kw)
        return r.model_copy(update={"rows": r.rows[1:]}) if task.id == "base" else r
    monkeypatch.setattr(svc, "run_subtask", dropping)
    with pytest.raises(DesignRefused) as e:
        m.build_design(doc["id"], digest=doc["digest"], by="staff", service=svc,
                       admission=_adm(), actor=de.build_actor())
    assert e.value.code == "BASE_UNPROVEN"
    monkeypatch.setattr(svc, "run_subtask", real)
    res = m.build_design(doc["id"], digest=doc["digest"], by="staff", service=svc,
                         admission=_adm(), actor=de.build_actor())
    rows = _receipt(m, res)["receipt"]["rows"]
    grain = doc["design"]["grain"]["entity"]
    assert rows["emitted"] == rows["base_rows_counted"] == len(estate.rows[grain])
    assert rows["dropped"] == []


# ------------------------------------------------------------------------------------------------
# 5. one label, a source-recorded field
# ------------------------------------------------------------------------------------------------
def test_f5_a_computed_or_extra_label_is_not_a_label(estate):  # noqa: F811
    from databridge_materialise.delegated import DesignDraft, validate_design
    m, svc, doc, _ = _proposed(estate, model=False)
    d = doc["design"]
    cat = m.design_catalogue(svc)
    agg = next(c for c in d["columns"] if c["aggregate"])
    feat = next(c for c in d["columns"] if c["role"] == "feature" and c["via"] is None)
    body = copy.deepcopy(d)
    body["columns"] = [({**c, "role": "label"} if c["name"] == feat["name"] else c)
                       for c in body["columns"]]
    body["label"] = {"column": agg["name"], "definition": "a computed target"}
    out = validate_design(DesignDraft(**body), cat, request=d["request"])
    assert out.label is None
    assert any(agg["name"] in q and "computed" in q for q in out.questions)
    assert [c.name for c in out.columns if c.role == "label"] == []
    assert any(feat["name"] in a and "feature" in a for a in out.assumptions)
    # a source-recorded field is accepted as THE one label
    body["label"] = {"column": feat["name"], "definition": "recorded"}
    out2 = validate_design(DesignDraft(**body), cat, request=d["request"])
    assert out2.label.column == feat["name"]
    assert [c.name for c in out2.columns if c.role == "label"] == [feat["name"]]


# ------------------------------------------------------------------------------------------------
# 6. typed join keys; several matches are refused, never overwritten
# ------------------------------------------------------------------------------------------------
def test_f6_join_keys_keep_their_type_and_ambiguity_is_refused(estate, monkeypatch):  # noqa: F811
    from databridge_materialise.delegated import DesignRefused, _tkey, _tkey_any
    assert _tkey(1) != _tkey("1") and _tkey(1) == _tkey(1.0)
    assert _tkey_any({_tkey("1"): 0}, 1) and not _tkey_any({_tkey("2"): 0}, 1)
    m, svc, doc = _approved(estate)
    lk = next(lk for lk in doc["design"]["links"] if lk["kind"] == "lookup")
    real = svc.dispatch

    def doubled(tasks, **kw):                  # a target key that names TWO rows
        out = real(tasks, **kw)
        for tid, r in out.items():
            if tid.startswith(lk["id"]) and r.rows:
                out[tid] = r.model_copy(update={"rows": (*r.rows, r.rows[0])})
        return out
    monkeypatch.setattr(svc, "dispatch", doubled)
    with pytest.raises(DesignRefused) as e:
        m.build_design(doc["id"], digest=doc["digest"], by="staff", service=svc,
                       admission=_adm(), actor=de.build_actor())
    assert e.value.code == "AMBIGUOUS_JOIN"

    def retyped(tasks, **kw):                  # the target's key comes back as another type
        out = real(tasks, **kw)
        for tid, r in out.items():
            if tid.startswith(lk["id"]):
                rows = []
                for row in r.rows:
                    k = row.cells[lk["to_field"]]
                    cells = {**row.cells, lk["to_field"]: k.model_copy(
                        update={"value": [k.value]})}
                    rows.append(row.model_copy(update={"cells": cells}))
                out[tid] = r.model_copy(update={"rows": tuple(rows)})
        return out
    monkeypatch.setattr(svc, "dispatch", retyped)
    import databridge_materialise.delegated as dl
    built = dl.build_raw(dl.TableDesign.model_validate(doc["design"]), m.design_catalogue(svc),
                         svc, admission=_adm(), by="staff")
    col = next(c for c in built.receipt["columns"] if c["via"] == lk["id"])
    assert col["grounded"] == 0


# ------------------------------------------------------------------------------------------------
# 7. open questions persist until answered
# ------------------------------------------------------------------------------------------------
def test_f7_an_unrelated_edit_does_not_answer_a_question(estate):  # noqa: F811
    from databridge_materialise.delegated import DesignRefused
    m, svc, doc, _ = _proposed(estate, model=True)
    assert doc["question_items"]
    cols = doc["design"]["columns"]
    renamed = [({**c, "name": c["name"] + "_x"} if i == 1 else c) for i, c in enumerate(cols)]
    ed = m.edit_design(doc["id"], {"columns": renamed}, by="staff", service=svc, rev=doc["rev"])
    assert {q["id"] for q in doc["question_items"]} <= {q["id"] for q in ed["question_items"]}
    with pytest.raises(DesignRefused) as e:
        m.approve_design(doc["id"], digest=ed["digest"], principal=de.issued_principal(),
                         by="staff")
    assert e.value.code == "DESIGN_HAS_QUESTIONS"
    with pytest.raises(DesignRefused) as e:
        m.edit_design(doc["id"], {"answers": {"q:000000000000": "x"}}, by="staff", service=svc,
                      rev=ed["rev"])
    assert e.value.code == "UNKNOWN_QUESTION"
    ans = m.edit_design(doc["id"], {"answers": {q["id"]: "use only approved links"
                                                for q in ed["question_items"]}},
                        by="staff", service=svc, rev=ed["rev"])
    assert ans["question_items"] == []
    assert any(a.startswith("answered by the editor") for a in ans["design"]["assumptions"])


# ------------------------------------------------------------------------------------------------
# 8. an incomplete source reference never picks the first table of that name
# ------------------------------------------------------------------------------------------------
def test_f8_an_ambiguous_grain_is_asked_not_guessed(estate):  # noqa: F811
    from dataclasses import replace

    from databridge_materialise.delegated import DesignDraft, validate_design
    m, svc, _doc, _ = _proposed(estate, model=False)
    cat = m.design_catalogue(svc)
    grain = next(t for t in cat.tables.values() if t.table == estate.case["grain"])
    twin = replace(grain, connection=grain.connection + "_replica",
                   adapter_agent=grain.adapter_agent + "_replica")
    cat.tables[(twin.adapter_agent, twin.connection, twin.table)] = twin
    out = validate_design(DesignDraft(grain={"entity": grain.table}), cat, request="x")
    assert any("Several sources" in q for q in out.questions)
    assert out.grain.entity == grain.table and out.grain.connection != grain.connection
    exact = validate_design(DesignDraft(grain=grain.ref.model_dump()), cat, request="x")
    assert exact.grain == grain.ref and not any("Several" in q for q in exact.questions)
