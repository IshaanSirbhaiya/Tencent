"""R-028 LEDGER: the one decision ledger, the actor object and the standard refusal codes.

Every expectation is derived from what the ledger wrote (its records, its anchor), never typed in.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from databridge_core import decision_ledger as dl
from databridge_core.decision_ledger import (DecisionLedger, DecisionRefused,
                                             LEDGER_INTEGRITY_FAILED, actor_of, require_person)

KEY = "r028-ledger-test-key-0123456789abcdef0123"


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv(dl.APPROVAL_KEY_ENV, KEY)
    monkeypatch.delenv(dl.ANCHORS_ENV, raising=False)


def _who(pid="alice", **kw):
    return {"principal_id": pid, "authenticated_by": "bearer:issuer", "principal_type": "Principal",
            "dev_only": False, **kw}


def _lines(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _ledger(tmp_path, name="x.approvals.jsonl", **kw) -> DecisionLedger:
    return DecisionLedger(tmp_path / "logs" / name, what="test log", **kw)


# --- the chain, the seal and the anchor -------------------------------------------------------------
def test_records_are_sealed_chained_and_anchored(tmp_path):
    led = _ledger(tmp_path)
    a = led.append({"event": "approved", **_who()})
    b = led.append({"event": "approved", **_who("bob")})
    raw = _lines(led.path)
    assert raw == [a, b] and [r["seq"] for r in raw] == [1, 2]
    assert raw[0]["prev"] == led.genesis and raw[1]["prev"] == raw[0]["hash"]
    assert all(r["seal"] == dl.seal_of(KEY.encode(), r["hash"]) for r in raw)
    anchor, why = dl.AnchorLedger(KEY.encode(), led.anchors_path).latest(led.log_id)
    assert not why and anchor["seq"] == len(raw) and anchor["head"] == raw[-1]["hash"]
    assert led.read() == raw


def test_an_edited_record_refuses_every_read_and_every_append(tmp_path):
    led = _ledger(tmp_path)
    led.append({"event": "approved", **_who()})
    led.append({"event": "approved", **_who("bob")})
    recs = _lines(led.path)
    recs[0]["principal_id"] = "mallory"
    recs[0]["hash"] = dl.digest_of(recs[0], exclude=("hash", "seal"))     # recomputed, no key
    led.path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    before = led.path.read_bytes()
    with pytest.raises(DecisionRefused) as e:
        led.read()
    assert e.value.code == LEDGER_INTEGRITY_FAILED
    with pytest.raises(DecisionRefused):
        led.append({"event": "x", **_who()})
    assert led.path.read_bytes() == before                             # nothing was written


def test_a_rolled_back_log_is_refused(tmp_path):
    led = _ledger(tmp_path)
    for p in ("alice", "bob"):
        led.append({"event": "approved", **_who(p)})
    first = led.path.read_text(encoding="utf-8").splitlines()[0]
    led.path.write_text(first + "\n", encoding="utf-8")               # drop the revocation-like tail
    with pytest.raises(DecisionRefused, match="rolled back"):
        led.read()


def test_a_deleted_anchored_log_is_refused(tmp_path):
    led = _ledger(tmp_path)
    led.append({"event": "approved", **_who()})
    led.path.unlink()
    with pytest.raises(DecisionRefused, match="missing"):
        led.read()


def test_an_unanchored_tail_is_refused_until_the_next_append_recovers_it(tmp_path, monkeypatch):
    """#4: the anchor must EQUAL the log; an interrupted append is completed explicitly."""
    led = _ledger(tmp_path)
    led.append({"event": "approved", **_who()})
    real = dl.AnchorLedger.anchor

    def crash(self, *a, **k):
        raise OSError("crash between the record and its anchor")

    monkeypatch.setattr(dl.AnchorLedger, "anchor", crash)
    with pytest.raises(OSError):
        led.append({"event": "approved", **_who("bob")})
    monkeypatch.setattr(dl.AnchorLedger, "anchor", real)
    n = len(_lines(led.path))
    anchor, _ = dl.AnchorLedger(KEY.encode(), led.anchors_path).latest(led.log_id)
    assert anchor["seq"] == n - 1                                       # the tail is unanchored
    with pytest.raises(DecisionRefused, match="interrupted append") as e:
        led.read()                                                      # a read never repairs
    assert e.value.code == LEDGER_INTEGRITY_FAILED and len(_lines(led.path)) == n
    led.append({"event": "approved", **_who("carol")})
    raw = led.read(raw=True)
    events = [r.get(dl.LEDGER_EVENT) for r in raw]
    assert events.count("recovered") == 1
    rec = raw[events.index("recovered")]
    assert rec["completed_seq"] == n
    assert [r["principal_id"] for r in led.read()] == ["alice", "bob", "carol"]


def test_a_torn_last_line_is_moved_aside_by_recovery(tmp_path):
    led = _ledger(tmp_path)
    led.append({"event": "approved", **_who()})
    with open(led.path, "ab") as fh:
        fh.write(b'{"event": "approved", "principal_id": "bo')         # crash mid-write
    with pytest.raises(DecisionRefused, match="interrupted"):
        led.read()
    got = led.recover()
    assert [r.get(dl.LEDGER_EVENT) for r in got] == [None, "recovered"]
    assert (led.path.parent / got[-1]["torn_file"]).read_bytes().startswith(b'{"event"')
    assert [r["principal_id"] for r in led.read()] == ["alice"]


def test_a_tail_appended_without_the_key_is_not_recovered(tmp_path):
    led = _ledger(tmp_path)
    led.append({"event": "approved", **_who()})
    last = _lines(led.path)[-1]
    forged = {"event": "approved", **_who("mallory"), "seq": 2, "prev": last["hash"]}
    forged["hash"] = dl.digest_of(forged, exclude=("hash", "seal"))
    forged["seal"] = "0" * 64
    with open(led.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(forged) + "\n")
    with pytest.raises(DecisionRefused):
        led.read()
    with pytest.raises(DecisionRefused):
        led.append({"event": "approved", **_who("bob")})


# --- legacy logs: refused, then superseded -------------------------------------------------------------
def test_a_legacy_unsealed_log_is_refused_then_superseded_by_the_next_decision(tmp_path):
    led = _ledger(tmp_path)
    led.path.parent.mkdir(parents=True)
    legacy = [{"event": "approved", **_who(), "prev": "genesis", "hash": "h1"}]
    led.path.write_text("".join(json.dumps(r) + "\n" for r in legacy), encoding="utf-8")
    legacy_bytes = led.path.read_bytes()
    with pytest.raises(DecisionRefused, match="before decisions were sealed") as e:
        led.read()
    assert e.value.code == LEDGER_INTEGRITY_FAILED and "re-approve" in e.value.message
    led.append({"event": "approved", **_who("bob")})
    raw = led.read(raw=True)
    assert raw[0][dl.LEDGER_EVENT] == "superseded_legacy"
    kept = led.path.parent / raw[0]["legacy_file"]
    assert kept.read_bytes() == legacy_bytes                           # kept, never evidence
    assert [r["principal_id"] for r in led.read()] == ["bob"]


def test_an_anchored_log_stripped_of_its_seals_is_not_treated_as_legacy(tmp_path):
    led = _ledger(tmp_path)
    led.append({"event": "approved", **_who()})
    recs = [{k: v for k, v in r.items() if k != "seal"} for r in _lines(led.path)]
    led.path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    with pytest.raises(DecisionRefused):
        led.append({"event": "approved", **_who("mallory")})
    assert _lines(led.path) == recs


def test_the_key_is_refused_inside_the_logs_directory(tmp_path, monkeypatch):
    monkeypatch.delenv(dl.APPROVAL_KEY_ENV, raising=False)
    led = _ledger(tmp_path)
    led.path.parent.mkdir(parents=True)
    monkeypatch.setenv(dl.APPROVAL_KEY_FILE_ENV, str(led.path.parent / "approval.key"))
    with pytest.raises(DecisionRefused) as e:
        led.append({"event": "x", **_who()})
    assert e.value.code == dl.APPROVAL_KEY_UNAVAILABLE and not led.path.exists()


def test_a_different_key_verifies_nothing(tmp_path, monkeypatch):
    led = _ledger(tmp_path)
    led.append({"event": "approved", **_who()})
    monkeypatch.setenv(dl.APPROVAL_KEY_ENV, "another-deployment-key-0123456789abcdef")
    with pytest.raises(DecisionRefused):
        DecisionLedger(led.path, what="test log").read()


def test_the_append_lock_is_cross_process(tmp_path):
    led = _ledger(tmp_path)
    led.path.parent.mkdir(parents=True)
    ready = tmp_path / "ready"
    child = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import time, pathlib
        from databridge_core.decision_ledger import file_lock
        with file_lock(pathlib.Path({str(led.lock_path)!r}), timeout=10):
            pathlib.Path({str(ready)!r}).write_text("1")
            time.sleep(3)
    """)])
    try:
        for _ in range(200):
            if ready.exists():
                break
            __import__("time").sleep(0.05)
        assert ready.exists()
        with pytest.raises(DecisionRefused) as e:
            with led.lock(timeout=0.3):
                pass
        assert e.value.code == "LEDGER_BUSY"
    finally:
        child.wait(timeout=30)
    led.append({"event": "x", **_who()})                                # free again


# --- the actor object and the codes ---------------------------------------------------------------------
def test_the_actor_object_is_whole_and_undecorated():
    assert actor_of(_who()) == {k: _who()[k] for k in dl.ACTOR_FIELDS}
    for bad in (_who("alice (dev token)"), {**_who(), "authenticated_by": ""},
                {**_who(), "principal_type": None}, {"principal_id": "alice"}):
        with pytest.raises(DecisionRefused) as e:
            actor_of(bad)
        assert e.value.code == dl.APPROVER_NOT_AUTHENTICATED


def test_a_delegated_decision_carries_its_delegation_and_is_never_a_person():
    who = {**_who(), "authenticated_by": "delegated:sha256:abc", "policy_digest": "sha256:abc",
           "agent_id": "keeper-x", "scope": "additive_within_declared_scope"}
    a = actor_of(who)
    assert a["delegation"] == {k: who[k] for k in ("policy_digest", "agent_id", "scope")}
    with pytest.raises(DecisionRefused) as e:
        require_person(who)
    assert e.value.code == dl.MACHINE_ACTOR


@pytest.mark.parametrize("prefix", dl.MACHINE_PREFIXES)
def test_a_machine_principal_is_refused_where_a_person_is_required(prefix):
    with pytest.raises(DecisionRefused) as e:
        require_person(_who(f"{prefix}x"))
    assert e.value.code == dl.MACHINE_ACTOR
    assert require_person(_who())["principal_id"] == "alice"


def test_older_codes_map_onto_the_standard_ones():
    assert set(dl.LEGACY_CODES.values()) <= dl.REFUSAL_CODES
    for code in dl.REFUSAL_CODES:
        assert dl.standard_code(code) == code
        assert dl.refusal(code, "m") == {"refusal": {"code": code, "message": "m"}}
