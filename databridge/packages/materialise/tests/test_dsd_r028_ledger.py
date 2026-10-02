"""R-028 LEDGER (Codex consistency review #2, #4) for dataset designs, on BOTH estates.

The design log is the common decision ledger. Its anchor must EQUAL the log (length and head): an
event appended but never anchored (an interrupted append) is NOT trusted on read, and the next
change of the design completes it explicitly (a recorded ``recovered`` ledger event). Expectations
are derived from what the store wrote.
"""
from __future__ import annotations

import json

import pytest

import dsd_estates as de
from test_dsd_delegated_datasets import _env, _proposed, estate  # noqa: F401

from databridge_core import decision_ledger as dl


def _anchor_of(store, design_id):
    led = store._ledger(design_id)
    a, why = dl.AnchorLedger(led.key, led.anchors_path).latest(design_id)
    assert not why
    return led, a


def test_the_anchor_equals_the_design_log_after_every_event(estate):  # noqa: F811
    from databridge_materialise.delegated import DesignStore

    m, _svc, doc, _ = _proposed(estate, model=False)
    m.approve_design(doc["id"], digest=doc["digest"], principal=de.issued_principal(), by="staff")
    store = DesignStore(m.out_root)
    led, a = _anchor_of(store, doc["id"])
    events = [json.loads(x) for x in led.path.read_text(encoding="utf-8").splitlines()]
    assert len(events) >= 2                                            # non-vacuous
    assert (a["seq"], a["head"]) == (len(events), events[-1]["record_digest"])


def test_an_unanchored_approval_is_not_trusted_until_the_next_change_recovers_it(
        estate, monkeypatch):  # noqa: F811
    from databridge_materialise.delegated import DesignStore

    m, _svc, doc, _ = _proposed(estate, model=False)
    real = dl.AnchorLedger.anchor

    def crash(self, *a, **k):
        raise OSError("the process died between the event and its anchor")

    monkeypatch.setattr(dl.AnchorLedger, "anchor", crash)
    with pytest.raises(OSError):
        m.approve_design(doc["id"], digest=doc["digest"], principal=de.issued_principal(),
                         by="staff")
    monkeypatch.setattr(dl.AnchorLedger, "anchor", real)
    store = DesignStore(m.out_root)
    led, a = _anchor_of(store, doc["id"])
    n = len(led.path.read_text(encoding="utf-8").splitlines())
    assert a["seq"] == n - 1                                           # the approval is unanchored
    v = m.design(doc["id"], by="staff")
    assert v["status"] == "integrity_failed" and v["approval"] is None   # never trusted on read
    assert len(led.path.read_text(encoding="utf-8").splitlines()) == n   # a read wrote nothing
    # the next change completes the interrupted append explicitly, then decides
    after = m.approve_design(doc["id"], digest=doc["digest"], principal=de.issued_principal(),
                             by="staff")
    assert after["status"] == "approved"
    raw = store._ledger(doc["id"]).read(raw=True)
    assert [r.get(dl.LEDGER_EVENT) for r in raw].count("recovered") == 1
    led, a = _anchor_of(store, doc["id"])
    assert (a["seq"], a["head"]) == (len(raw), raw[-1]["record_digest"])
