"""R-028 LEDGER (Codex consistency review #2, #4, #5, #7, #9, #11) for plans and mappings.

* #2  the plan and mapping approval logs are the common decision ledger: sealed under the
      deployment key, chained, anchored. A chain rewritten WITHOUT the key (every hash recomputed —
      what the old unkeyed chain could not detect) is refused; a legacy log is refused, then
      superseded by the next approval.
* #5  every mapping read -> verify -> log -> save runs under one cross-process mapping lock, and
      mappings are written whole (temp + `os.replace`).
* #7  every decision record carries the actor object.
* #9  linking uses the SUBJECT predicate (`vocab.is_subject`), not the person one.
* #11 refusals carry the standard codes.

Expectations are derived from what the code wrote or from the estates' own data.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import estates
from conftest import test_principal

from databridge_core import decision_ledger as dl

pytestmark = pytest.mark.real_approval

ESTATE_NAMES = ("workspace", "finance")


def _lines(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _rechain_without_key(p: Path, edit) -> None:
    """Rewrite the WHOLE log the way the old unkeyed chain allowed: edit a record, recompute
    every hash and prev from there on. The seal cannot be recomputed without the key."""
    recs = _lines(p)
    edit(recs[0])
    prev = "genesis"
    for r in recs:
        r["prev"] = prev
        r["hash"] = dl.digest_of(r, exclude=("hash", "seal"))
        prev = r["hash"]
    p.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in recs), encoding="utf-8")


# --- plans -----------------------------------------------------------------------------------------
def _approved_plan(sid="src"):
    from test_fxi3_sources import _Server, _conn, _plan, _tools

    from databridge_mcp_ingest.plans import approve_plan

    _conn(sid)
    tools = _tools()
    _plan(sid, tools)
    return approve_plan(sid, principal=test_principal("dana"), client=_Server(tools)), tools


def test_the_plan_approval_log_is_sealed_chained_and_anchored(walnut_env):
    from databridge_mcp_ingest.plans import load_plan, plan_ledger, verify_plan_approval

    plan, _ = _approved_plan()
    led = plan_ledger("src")
    [rec] = _lines(led.path)
    assert rec["seal"] == dl.seal_of(led.key, rec["hash"]) and rec["seq"] == 1
    anchor, why = dl.AnchorLedger(led.key, led.anchors_path).latest(led.log_id)
    assert not why and (anchor["seq"], anchor["head"]) == (1, rec["hash"])
    got = verify_plan_approval(load_plan("src"))
    # the actor object (#7) is on the decision record, whole
    assert {k: got[k] for k in dl.ACTOR_FIELDS} == dl.actor_of(got)
    assert plan.approval["log_hash"] == rec["hash"]


def test_a_plan_log_rechained_without_the_key_is_refused(walnut_env):
    from databridge_mcp_ingest.plans import ApprovalRefused, load_plan, plan_approvals_path, \
        verify_plan_approval

    _approved_plan()
    _rechain_without_key(plan_approvals_path("src"), lambda r: r.update(dev_only=True))
    with pytest.raises(ApprovalRefused) as e:
        verify_plan_approval(load_plan("src"))
    assert e.value.code == dl.LEDGER_INTEGRITY_FAILED


def test_a_legacy_plan_log_is_refused_then_superseded_by_the_next_approval(walnut_env):
    from test_fxi3_sources import _Server

    from databridge_mcp_ingest.plans import (ApprovalRefused, approve_plan, load_plan,
                                             plan_approvals_path, plan_ledger,
                                             verify_plan_approval)

    _, tools = _approved_plan()
    p = plan_approvals_path("src")
    led = plan_ledger("src")
    # a pre-R-028 deployment: the same records, unsealed, never anchored
    legacy = [{k: v for k, v in r.items() if k not in ("seal", "seq")} for r in _lines(p)]
    p.write_text("".join(json.dumps(r) + "\n" for r in legacy), encoding="utf-8")
    led.anchors_path.unlink()
    legacy_bytes = p.read_bytes()
    with pytest.raises(ApprovalRefused, match="re-approve") as e:
        verify_plan_approval(load_plan("src"))
    assert e.value.code == dl.LEDGER_INTEGRITY_FAILED
    from test_fxi3_sources import _plan

    _plan("src", tools)                                                 # proposed again
    approve_plan("src", principal=test_principal("erin"), client=_Server(tools))
    assert verify_plan_approval(load_plan("src"))["principal_id"] == "erin"
    raw = plan_ledger("src").read(raw=True)
    assert raw[0][dl.LEDGER_EVENT] == "superseded_legacy"
    assert (p.parent / raw[0]["legacy_file"]).read_bytes() == legacy_bytes
    assert raw[0]["legacy_sha256"] == hashlib.sha256(legacy_bytes).hexdigest()


def test_a_machine_principal_answers_the_standard_code(walnut_env):
    from databridge_mcp_ingest.plans import ApprovalRefused, require_authenticated_approver

    machine = test_principal(f"{dl.MACHINE_PREFIXES[0]}bot")
    with pytest.raises(ApprovalRefused) as e:
        require_authenticated_approver(machine)
    assert e.value.code == dl.MACHINE_ACTOR
    with pytest.raises(ApprovalRefused) as e:
        require_authenticated_approver("alice")
    assert e.value.code == dl.APPROVER_NOT_AUTHENTICATED
    with pytest.raises(ApprovalRefused) as e:
        require_authenticated_approver(test_principal("alice (dev token)"))
    assert e.value.code == dl.APPROVER_NOT_AUTHENTICATED


def test_plan_staleness_maps_to_the_standard_code():
    from databridge_mcp_ingest.plans import PlanChanged, PlanStale

    for cls in (PlanChanged, PlanStale):
        assert dl.standard_code(cls.code) == cls.standard_code == dl.DECISION_STALE


# --- mappings: the ledger ------------------------------------------------------------------------
@pytest.fixture
def ws_vocab():
    from databridge_graph.packs import deployment

    with deployment("workspace") as vocab:
        yield vocab


def _approved_mapping(sid, vocab, who="alice"):
    from test_fxi4_tabular_drift import _proposed_mapping

    from databridge_mcp_ingest.mapping import approve_mapping

    _proposed_mapping(sid, vocab)
    return approve_mapping(sid, principal=test_principal(who), vocab=vocab)


def test_a_mapping_log_rechained_without_the_key_honours_nothing(walnut_env, ws_vocab):
    from databridge_mcp_ingest.mapping import (_record_digest, approval_log_path,
                                               approval_problem, load_mapping)

    m = _approved_mapping("m1", ws_vocab)
    tables = sorted(m.tables)
    assert all(approval_problem("m1", t, m.tables[t], ws_vocab) is None for t in tables)

    def forge(r):
        r["principal_id"] = "mallory"
        body = {k: v for k, v in r.items() if k not in ("seq", "prev", "hash", "seal",
                                                       "record_digest")}
        r["record_digest"] = _record_digest(body)

    _rechain_without_key(approval_log_path("m1"), forge)
    now = load_mapping("m1")
    assert all("append-only" in (approval_problem("m1", t, now.tables[t], ws_vocab) or "")
               for t in tables)


def test_a_legacy_mapping_log_honours_nothing_until_approved_again(walnut_env, ws_vocab):
    from databridge_mcp_ingest.mapping import (approval_log_path, approval_problem,
                                               approve_mapping, effective_mapping, load_mapping,
                                               mapping_ledger)

    _approved_mapping("m2", ws_vocab)
    p = approval_log_path("m2")
    legacy = [{k: v for k, v in r.items() if k not in ("seal", "seq")} for r in _lines(p)]
    p.write_text("".join(json.dumps(r) + "\n" for r in legacy), encoding="utf-8")
    mapping_ledger("m2").anchors_path.unlink()
    m = load_mapping("m2")
    eff = effective_mapping(m, ws_vocab)
    assert eff.tables and all(t.status == "proposed" for t in eff.tables.values())
    approve_mapping("m2", principal=test_principal("bob"), vocab=ws_vocab)
    again = load_mapping("m2")
    assert all(approval_problem("m2", t, tm, ws_vocab) is None for t, tm in again.tables.items())
    raw = mapping_ledger("m2").read(raw=True)
    assert raw[0][dl.LEDGER_EVENT] == "superseded_legacy"


def test_every_mapping_decision_record_carries_the_actor_object(walnut_env, ws_vocab):
    from databridge_mcp_ingest.mapping import mapping_ledger

    _approved_mapping("m3", ws_vocab, who="carol")
    recs = mapping_ledger("m3").read()
    assert recs                                                         # non-vacuous
    for r in recs:
        assert {k: r[k] for k in dl.ACTOR_FIELDS} == dl.actor_of(r)
        assert r["principal_id"] == "carol"


# --- mappings: one lock, whole writes (#5) ------------------------------------------------------
def test_approve_mapping_holds_the_mapping_lock_across_read_log_and_save(walnut_env, ws_vocab,
                                                                         monkeypatch):
    from test_fxi4_tabular_drift import _proposed_mapping

    from databridge_mcp_ingest import mapping as mp

    _proposed_mapping("m4", ws_vocab)
    lock = mp.mapping_path("m4").with_name("m4.mapping.yaml.lock")
    seen: list[tuple[str, bool]] = []
    real_load, real_append, real_save = mp.load_mapping, mp._append_approval, mp.save_mapping
    monkeypatch.setattr(mp, "load_mapping", lambda *a, **k: (
        seen.append(("load", dl.lock_held(lock))), real_load(*a, **k))[1])
    monkeypatch.setattr(mp, "_append_approval", lambda *a, **k: (
        seen.append(("log", dl.lock_held(lock))), real_append(*a, **k))[1])
    monkeypatch.setattr(mp, "save_mapping", lambda *a, **k: (
        seen.append(("save", dl.lock_held(lock))), real_save(*a, **k))[1])
    mp.approve_mapping("m4", principal=test_principal("dana"), vocab=ws_vocab)
    assert {s for s, _ in seen} == {"load", "log", "save"}
    assert all(held for _, held in seen), seen


def test_the_mapping_lock_is_cross_process_and_a_blocked_writer_writes_nothing(walnut_env,
                                                                              ws_vocab,
                                                                              tmp_path,
                                                                              monkeypatch):
    from test_fxi4_tabular_drift import _proposed_mapping

    from databridge_mcp_ingest import mapping as mp
    from databridge_mcp_ingest.plans import PlanError

    _proposed_mapping("m5", ws_vocab)
    before = mp.mapping_path("m5").read_bytes()
    lock = mp.mapping_path("m5").with_name("m5.mapping.yaml.lock")
    ready = tmp_path / "ready"
    child = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import time, pathlib
        from databridge_core.decision_ledger import file_lock
        with file_lock(pathlib.Path({str(lock)!r}), timeout=10):
            pathlib.Path({str(ready)!r}).write_text("1")
            time.sleep(4)
    """)])
    try:
        for _ in range(200):
            if ready.exists():
                break
            __import__("time").sleep(0.05)
        assert ready.exists()
        monkeypatch.setenv("WALNUT_PLAN_LOCK_TIMEOUT_S", "0.3")
        with pytest.raises(PlanError, match="timed out"):
            mp.approve_mapping("m5", principal=test_principal("dana"), vocab=ws_vocab)
    finally:
        child.wait(timeout=30)
    assert mp.mapping_path("m5").read_bytes() == before
    assert not mp.approval_log_path("m5").exists()


def test_a_failed_mapping_write_leaves_the_previous_mapping_whole(walnut_env, ws_vocab,
                                                                 monkeypatch):
    from test_fxi4_tabular_drift import _proposed_mapping

    from databridge_mcp_ingest import mapping as mp

    _proposed_mapping("m6", ws_vocab)
    p = mp.mapping_path("m6")
    before = p.read_bytes()

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(dl.os, "replace", boom)
    m = mp.load_mapping("m6")
    with pytest.raises(OSError):
        mp.save_mapping(m.model_copy(update={"approved_by": "x"}))
    assert p.read_bytes() == before
    assert not [f for f in p.parent.iterdir() if f.name.endswith(".tmp")]


# --- the subject predicate (#9) -------------------------------------------------------------------
@pytest.fixture(scope="module")
def estate_snaps(tmp_path_factory):
    out = {}
    for name in ESTATE_NAMES:
        d = tmp_path_factory.mktemp(f"r028_{name}")
        db = estates.build_estate(name, d / "e.sqlite")
        out[name] = estates.snapshots(db, name)
        db.close()
    return out


class _PersonlessVocab:
    """The estate's vocabulary, with a pack that says NO subject is a person (an `is_person`
    line drawn so that subjects remain that are not people)."""

    def __init__(self, v):
        self._v = v

    def __getattr__(self, name):
        return getattr(self._v, name)

    def is_person(self, kind):
        return False


@pytest.mark.parametrize("name", ESTATE_NAMES)
def test_a_link_never_points_into_a_subject_kind_even_when_it_is_not_a_person(name,
                                                                            estate_snaps):
    from databridge_graph.packs import deployment

    from databridge_mcp_ingest.mapping import (_key_values, propose_links, propose_rules,
                                               validate_table_mapping)

    snaps = estate_snaps[name]
    with deployment(estates.ESTATES[name]["pack"]) as vocab:
        rules = propose_rules(snaps, vocab)
        subject_targets = {t for t, tm in rules.items() if tm.node and _key_values(snaps[t])
                           and vocab.is_subject(tm.node["kind"])}
        # strip each declared FK into a subject table in turn: the undeclared reference that
        # remains is measured, and never proposed as a link
        from test_tab6_finance_fixes import _strip_fk

        into_subject = []
        for t, s in sorted(snaps.items()):
            if not (rules.get(t) and rules[t].node) or len(s["rows"]) > 5000:
                continue
            for fk in s.get("foreign_keys") or []:
                target = (f"{fk['ref_schema']}.{fk['ref_table']}" if fk.get("ref_schema")
                          else fk.get("ref_table"))
                if target not in subject_targets or not fk.get("column"):
                    continue
                stripped = _strip_fk(snaps, t, fk["column"])
                got = propose_links("src", stripped, propose_rules(stripped, vocab),
                                    _PersonlessVocab(vocab)).get(t, [])
                mine = [lk for lk in got if lk["column"] == fk["column"]
                        and lk["table"] == target]
                # R-029 F-11: a split target is measured per kind; a link may name the kind its
                # NON-subject rows are built as — never a subject kind
                assert not [lk for lk in mine if lk["status"] == "proposed"
                            and vocab.is_subject(lk["target_kind"])], mine
                into_subject += [lk for lk in mine if vocab.is_subject(lk["target_kind"])]
        assert into_subject                                             # non-vacuous
        assert all(lk["status"] == "not_proposed" and lk["edge"] is None
                   and "subject" in lk["why"] for lk in into_subject), into_subject
        # validation refuses an approved-link edge INTO a subject kind as well
        refused = 0
        for t, tm in rules.items():
            if not tm.node or vocab.is_subject(tm.node["kind"]):
                continue
            for target in sorted(subject_targets):
                kind = rules[target].node["kind"]
                own = f"{tm.node['kind']}:{{walnut_row_key}}"
                fk_col = next(iter(tm.node.get("attrs") or {}), None)
                if fk_col is None:
                    continue
                edge = {"kind": sorted(vocab.edge_kinds)[0], "src": own, "dst": f"{kind}:{{walnut_fk_{fk_col}}}",
                        "evidence": [fk_col],
                        "ref": {"source": "src", "table": target, "key": "id"}}
                bad = tm.model_copy(update={"edges": [*(tm.edges or []), edge]})
                probs = validate_table_mapping(t, bad, _PersonlessVocab(vocab))
                assert any("subject kind" in p for p in probs), probs
                refused += 1
                break
        assert subject_targets and refused                                    # non-vacuous
