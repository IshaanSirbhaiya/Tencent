"""R-029 TIDY2 item 2 (design reader): reading a design never creates the approval key, or anything.

Before, `DesignStore._key` went through `screen._approval_key`, which CREATED the key file on first
use, so a design READ (the page, Activity's design family) wrote a secret to disk. Now a design whose
log exists but whose key is not provisioned reads as NOT READABLE (fail closed: integrity failed, no
approval honoured); a change refuses explicitly (`APPROVAL_KEY_UNAVAILABLE`); nothing is written.
On both estates.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import dsd_estates as de
from test_dsd_delegated_datasets import _env, _proposed, estate  # noqa: F401


def _tree(root: Path) -> dict:
    return {str(p): (p.is_dir(), p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(Path(root).rglob("*"))}


def test_reading_a_design_without_a_key_creates_nothing_and_reads_as_not_readable(
        estate, monkeypatch, tmp_path):  # noqa: F811
    from databridge_materialise.delegated import DesignRefused, DesignStore

    m, _svc, doc, _ = _proposed(estate, model=False)
    m.approve_design(doc["id"], digest=doc["digest"], principal=de.issued_principal(), by="staff")
    store = DesignStore(m.out_root)
    assert store.view(doc["id"])["approval"] is not None                    # non-vacuous
    # take the key away: no environment key, a key FILE that does not exist
    kp = tmp_path / "nokey" / "approval.key"
    monkeypatch.delenv("WALNUT_APPROVAL_KEY", raising=False)
    monkeypatch.setenv("WALNUT_APPROVAL_KEY_FILE", str(kp))
    ev = Path(m.out_root) / "designs" / doc["id"] / "events.jsonl"
    log_before = ev.read_bytes()
    before = _tree(m.out_root)
    v = DesignStore(m.out_root).view(doc["id"])
    assert v["status"] == "integrity_failed" and v["approval"] is None
    assert "not readable" in v["integrity"]
    events, why = DesignStore(m.out_root).verified_events(doc["id"])
    assert events == [] and "not readable" in why
    assert not kp.exists() and not kp.parent.exists()
    assert _tree(m.out_root) == before
    # a change refuses explicitly, and writes no event
    with pytest.raises(DesignRefused) as e:
        # a complete actor (DSX R-029 r1 #4 makes it required), so the refusal measured is the
        # missing KEY, not a missing actor
        DesignStore(m.out_root).record_build(doc["id"], by="staff", build_id="b-1", rev=1,
                                             digest=doc["digest"],
                                             actor={"principal_id": "staff",
                                                    "authenticated_by": "gateway",
                                                    "principal_type": "person",
                                                    "dev_only": False})
    assert e.value.code == "APPROVAL_KEY_UNAVAILABLE"
    assert not kp.exists()
    assert ev.read_bytes() == log_before
