"""A5 access ledger: round trip, fail-closed appends, and answer text kept out of the ledger."""
from __future__ import annotations

import datetime as dt
import json

import pytest
from databridge_ib.audit.content import AnswerStore, sha256_text
from databridge_ib.audit.ledger import AccessLedger, LedgerUnavailable
from databridge_ib.models import EVENT_KINDS, IB_EVENT, AuditQueryEvent, EventBase

from test_audit_support import NOW, SPECS, events_in_seed, make_cfg, seed


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    root = tmp_path_factory.mktemp("audit-ledger")
    cfg, ledger, store = seed(root)
    return cfg, ledger, store


def _q(sub="sara.sec") -> AuditQueryEvent:
    return AuditQueryEvent(sub=sub, filter={"op": "t"}, result_count=0, event_time=NOW)


def test_append_verify_round_trip(seeded):
    cfg, ledger, _ = seeded
    evs = list(ledger.events())
    assert len(evs) == events_in_seed()
    assert [e.seq for e in evs] == sorted(e.seq for e in evs)
    assert all(e.event is not None and e.kind in EVENT_KINDS for e in evs)
    assert all(e.recorded_at for e in evs)
    # backdated event_time is kept, recorded time is the real append time
    first = evs[0]
    assert first.data["event_time"] == SPECS[0].event_time.isoformat()
    assert dt.datetime.fromisoformat(first.recorded_at) > SPECS[0].event_time
    rep = ledger.verify()
    assert rep.ok and rep.internal_ok and rep.problems == []
    assert rep.records == len(evs)
    assert rep.head == evs[-1].hash
    assert rep.external_head_match is None              # no sink configured
    # the payload shape of CONTRACTS §4
    line = json.loads(cfg.ledger_path.read_text(encoding="utf-8").splitlines()[0])
    assert set(line) == {IB_EVENT, "recorded_at", "seq", "prev", "hash", "seal"}


def test_anchor_ledger_lives_in_another_directory(seeded):
    cfg, ledger, _ = seeded
    assert ledger.anchors_path.parent != ledger.path.parent
    assert ledger.anchors_path.is_file()


def test_missing_key_fails_closed(tmp_path):
    ledger = AccessLedger(make_cfg(tmp_path, key=""))
    with pytest.raises(LedgerUnavailable):
        ledger.append(_q())
    assert not ledger.path.exists()


def test_short_key_fails_closed(tmp_path):
    ledger = AccessLedger(make_cfg(tmp_path, key="short-key"))
    with pytest.raises(LedgerUnavailable) as ei:
        ledger.append(_q())
    assert ei.value.code == "APPROVAL_KEY_UNAVAILABLE"
    assert not ledger.path.exists()


def test_wrong_key_cannot_read_or_extend(tmp_path):
    cfg = make_cfg(tmp_path)
    AccessLedger(cfg).append(_q())
    other = AccessLedger(make_cfg(tmp_path, key="another-key-of-sufficient-length-0123456789"))
    with pytest.raises(LedgerUnavailable):
        other.append(_q())
    with pytest.raises(LedgerUnavailable):
        list(other.events())
    assert not other.verify().ok


def test_corrupt_log_fails_closed(tmp_path):
    cfg = make_cfg(tmp_path)
    ledger = AccessLedger(cfg)
    ledger.append(_q())
    ledger.append(_q())
    before = cfg.ledger_path.read_bytes()
    lines = before.decode().splitlines()
    cfg.ledger_path.write_text(lines[0] + "\n{not json}\n" + lines[1] + "\n", encoding="utf-8")
    with pytest.raises(LedgerUnavailable) as ei:
        ledger.append(_q())
    assert ei.value.code == "LEDGER_INTEGRITY_FAILED"
    with pytest.raises(LedgerUnavailable):
        list(ledger.events())
    assert not ledger.verify().ok


def test_non_event_is_refused(tmp_path):
    ledger = AccessLedger(make_cfg(tmp_path))
    with pytest.raises(LedgerUnavailable):
        ledger.append({"kind": "request.accepted"})                      # type: ignore[arg-type]
    with pytest.raises(LedgerUnavailable):
        ledger.append(EventBase(kind="not.a.kind", event_time=NOW))


def test_answer_text_never_in_ledger(seeded):
    cfg, ledger, store = seeded
    blob = cfg.ledger_path.read_text(encoding="utf-8") + \
        cfg.anchors_path.read_text(encoding="utf-8")
    answered = [sp for sp in SPECS if not sp.failed]
    assert answered
    for sp in answered:
        assert sp.answer not in blob
        assert sha256_text(sp.answer) in blob            # only the digest is ledgered
    assert "SECRET-ANSWER-TEXT" not in blob
    # and the content store returns it, intact, by its ref
    rel = [e for e in ledger.events() if e.kind == "answer.released"]
    got = store.get(rel[0].data["answer_ref"])
    assert got is not None and got.intact and got.sha256 == rel[0].data["answer_sha256"]


def test_content_store_is_write_once(tmp_path):
    store = AnswerStore(make_cfg(tmp_path))
    store.put("r1", "a")
    from databridge_ib.audit.content import ContentStoreUnavailable
    with pytest.raises(ContentStoreUnavailable):
        store.put("r1", "b")


def test_subscriber_failure_never_fails_append(tmp_path):
    ledger = AccessLedger(make_cfg(tmp_path))
    seen = []
    ledger.subscribe(lambda le: (_ for _ in ()).throw(RuntimeError("boom")))
    ledger.subscribe(seen.append)
    rec = ledger.append(_q())
    assert seen and seen[0].seq == rec["seq"]
