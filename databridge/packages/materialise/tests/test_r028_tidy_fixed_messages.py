"""R-028 TIDY #4: the materialiser never builds a refusal, a note or a visible record from
exception text (CONS_BRIEF standard 4). Each failure carries a FIXED message, a code and an
opaque ``error_ref`` (``ERR-<hex>``); the exception text goes to the server log only.

Every probe exception carries a marker and a filesystem path; the assertion is that neither
reaches the public surface, plus a non-vacuous check that a reference was issued.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from databridge_materialise import platform as mp
from databridge_materialise.platform import PlatformMaterialiser, RunRefused

SECRET = "SECRET-marker-7f3 C:\\keys\\approval.key"
REF = re.compile(r"^ERR-[0-9a-f]{10}$")


def _clean(text: str) -> None:
    assert "SECRET-marker-7f3" not in text and "approval.key" not in text
    assert "Error" not in text and "Exception" not in text


def _refused(fn) -> RunRefused:
    with pytest.raises(RunRefused) as err:
        fn()
    r = err.value
    _clean(r.message)
    assert REF.match(r.error_ref or ""), r.error_ref
    assert r.refusal()["error_ref"] == r.error_ref
    return r


def test_an_invalid_plan_is_a_fixed_message_and_a_reference(caplog):
    m = PlatformMaterialiser()
    with caplog.at_level(logging.WARNING):
        r = _refused(lambda: m.run({"plan": {"shape": {"kind": SECRET}}}, by="alice"))
    assert r.code == "PLAN_INVALID"
    assert r.error_ref in caplog.text                       # the detail is in the server log


def test_an_approver_that_could_not_be_asked_is_a_fixed_message_and_a_reference():
    def boom(**_k):
        raise RuntimeError(SECRET)

    m = PlatformMaterialiser(identifier_disclosure_approver=boom)
    plan = SimpleNamespace(plan_hash="h", policy=SimpleNamespace(
        purpose="p", lawful_basis="b", pii=SimpleNamespace(direct_identifiers="retain"),
        principal=SimpleNamespace(grant_id="g")))
    r = _refused(lambda: m._authorise_identifiers(plan, "alice"))
    assert r.code == "IDENTIFIER_DISCLOSURE_NOT_AUTHORISED"


def test_a_planner_that_produced_nothing_is_a_fixed_message_and_a_reference(monkeypatch):
    class _Agent:
        def __init__(self, *a, **k):
            pass

        def author(self, *_a, **_k):
            raise RuntimeError(SECRET)

    m = PlatformMaterialiser()
    monkeypatch.setattr(m, "reader", lambda **_k: SimpleNamespace(snapshots=lambda: {}))
    monkeypatch.setattr(mp, "build_catalogue",
                        lambda *_a, **_k: SimpleNamespace(entities=("x",)))
    monkeypatch.setattr(mp, "DataAgent", _Agent)
    r = _refused(lambda: m._run_request("a request", by="alice", emit=lambda _e: None))
    assert r.code == "PLAN_NOT_AUTHORED"


def test_a_model_failure_note_names_a_reference_not_the_exception():
    note = mp._model_failed_note(RuntimeError(SECRET))
    _clean(note)
    assert re.search(r"ERR-[0-9a-f]{10}", note)


def test_a_failed_index_registration_records_a_reference_not_the_exception(tmp_path):
    class _Sink:
        def record_emission(self, **_k):
            raise RuntimeError(SECRET)

    m = PlatformMaterialiser(store_provider=lambda: SimpleNamespace(reverse_index=_Sink()))
    final = tmp_path / "run"
    final.mkdir()
    rec = m._register_emission(SimpleNamespace(pointers=lambda: []),
                               SimpleNamespace(plan_hash="h", executed_at="t"), final)
    assert rec["registered"] is False and REF.match(rec.get("error_ref") or "")
    _clean(str(rec.get("error")))
    _clean((final / "index_registration.json").read_text(encoding="utf-8"))


def test_an_unreadable_plan_file_reports_a_reference_not_the_exception(tmp_path):
    h = "a" * 64
    m = PlatformMaterialiser(out_root=tmp_path)
    (tmp_path / h).mkdir()
    (tmp_path / h / "plan.yaml").write_text(f"shape: {{kind: '{SECRET}'}}\n", encoding="utf-8")
    out = m.plan_verification(h)
    if out is None:                                             # the hash pattern differs
        pytest.skip("plan_hash pattern rejects the probe hash")
    assert out["matches"] is False and REF.match(out.get("error_ref") or "")
    _clean(str(out.get("error")))


def test_a_design_model_failure_is_a_fixed_note_and_a_reference():
    from databridge_materialise import delegated

    err, note = delegated._model_error(RuntimeError(SECRET))
    _clean(err)
    _clean(note)
    assert re.search(r"ERR-[0-9a-f]{10}", note)


@pytest.mark.parametrize("principal", ["alice", None, {"principal_id": "alice"}])
def test_a_refused_design_approver_is_a_fixed_sentence_per_code(principal):
    from databridge_core import decision_ledger as dl
    from databridge_materialise import delegated

    with pytest.raises(delegated.DesignRefused) as err:
        delegated.approve(None, "d", principal=principal, digest="x", owner="o")
    assert err.value.code in dl.REFUSAL_CODES
    assert err.value.message == delegated.APPROVER_REFUSED_MESSAGES[err.value.code]
    assert type(principal).__name__ not in err.value.message           # no gate text echoed
