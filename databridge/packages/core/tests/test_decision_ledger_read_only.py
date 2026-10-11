"""R-029 TIDY2 item 2: reading a decision log never creates or writes anything.

Before: `DecisionLedger.read` reached `approval_key()`, which CREATED the key file on first use, so a
read (Activity's plan, mapping and design families; the design reader) wrote a secret to disk. Now:

* the key is never created implicitly. A WRITE with no key is an explicit refusal
  (`APPROVAL_KEY_UNAVAILABLE`) and writes nothing; provisioning is its own, explicit step
  (`provision_approval_key`);
* a READ of a log that exists, with no key, is a clear "not readable" refusal, and writes nothing;
* a READ of a log that does not exist needs no key and returns nothing;
* a read never creates the append lock file.

Every check compares the whole directory tree (names, sizes, mtimes) before and after.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from databridge_core import decision_ledger as dl
from databridge_core.decision_ledger import DecisionLedger, DecisionRefused

KEY = "tidy2-read-only-test-key-0123456789abcdef"


def _who(pid="alice"):
    return {"principal_id": pid, "authenticated_by": "bearer:issuer", "principal_type": "Principal",
            "dev_only": False}


def _tree(*roots: Path) -> dict:
    out = {}
    for root in roots:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            st = p.stat()
            out[str(p)] = (p.is_dir(), st.st_size, st.st_mtime_ns)
    return out


@pytest.fixture
def no_key(tmp_path, monkeypatch):
    """No key in the environment; the key FILE path points at a file that does not exist."""
    monkeypatch.delenv(dl.APPROVAL_KEY_ENV, raising=False)
    monkeypatch.delenv(dl.ANCHORS_ENV, raising=False)
    kp = tmp_path / "keys" / "approval.key"
    monkeypatch.setenv(dl.APPROVAL_KEY_FILE_ENV, str(kp))
    return kp


def _sealed_log(tmp_path, monkeypatch) -> DecisionLedger:
    """A real sealed, anchored log written under a key that is then taken away."""
    monkeypatch.setenv(dl.APPROVAL_KEY_ENV, KEY)
    led = DecisionLedger(tmp_path / "logs" / "x.approvals.jsonl", what="test log")
    led.append({"event": "approved", **_who()})
    monkeypatch.delenv(dl.APPROVAL_KEY_ENV)
    return led


def test_reading_an_existing_log_without_a_key_refuses_and_writes_nothing(tmp_path, monkeypatch,
                                                                           no_key):
    led = _sealed_log(tmp_path, monkeypatch)
    before = _tree(tmp_path)
    assert led.path.is_file() and not no_key.exists()                    # non-vacuous
    for read in (lambda: DecisionLedger(led.path, what="test log").read(),
                 lambda: DecisionLedger(led.path, what="test log").read(raw=True)):
        with pytest.raises(DecisionRefused) as e:
            read()
        assert e.value.code == dl.APPROVAL_KEY_UNAVAILABLE
        assert "not readable" in e.value.message
    recs, why = DecisionLedger(led.path, what="test log").inspect()
    assert recs == [] and "not readable" in why
    ok, why = DecisionLedger(led.path, what="test log").verify()
    assert not ok and "not readable" in why
    assert not no_key.exists()
    assert _tree(tmp_path) == before


def test_reading_a_missing_log_needs_no_key_and_writes_nothing(tmp_path, no_key):
    led = DecisionLedger(tmp_path / "logs" / "never.approvals.jsonl", what="test log")
    before = _tree(tmp_path)
    assert led.read() == [] and led.inspect() == ([], "") and led.verify() == (True, "")
    assert not no_key.exists() and _tree(tmp_path) == before


def test_a_write_without_a_key_is_an_explicit_refusal_and_writes_nothing(tmp_path, no_key):
    led = DecisionLedger(tmp_path / "logs" / "x.approvals.jsonl", what="test log")
    before = _tree(tmp_path)
    with pytest.raises(DecisionRefused) as e:
        led.append({"event": "approved", **_who()})
    assert e.value.code == dl.APPROVAL_KEY_UNAVAILABLE and e.value.status == 503
    assert not no_key.exists() and _tree(tmp_path) == before
    with pytest.raises(DecisionRefused) as e:
        dl.approval_key()
    assert e.value.code == dl.APPROVAL_KEY_UNAVAILABLE
    assert str(no_key) not in e.value.message and str(no_key.parent) not in e.value.message
    assert not no_key.exists()


def test_provisioning_is_explicit_then_writes_and_reads_work(tmp_path, no_key):
    p = dl.provision_approval_key()
    assert p == no_key.resolve() and len(no_key.read_text(encoding="utf-8").strip()) >= 32
    first = no_key.read_text(encoding="utf-8")
    assert dl.provision_approval_key() == p and no_key.read_text(encoding="utf-8") == first
    led = DecisionLedger(tmp_path / "logs" / "x.approvals.jsonl", what="test log")
    rec = led.append({"event": "approved", **_who()})
    assert led.read() == [rec]


def test_provisioning_refuses_a_key_inside_a_protected_directory(tmp_path, monkeypatch, no_key):
    monkeypatch.setenv(dl.APPROVAL_KEY_FILE_ENV, str(tmp_path / "logs" / "approval.key"))
    with pytest.raises(DecisionRefused) as e:
        dl.provision_approval_key(protect=[tmp_path / "logs"])
    assert e.value.code == dl.APPROVAL_KEY_UNAVAILABLE
    assert not (tmp_path / "logs" / "approval.key").exists()


def test_reading_a_pending_log_never_creates_the_lock_file(tmp_path, monkeypatch):
    """A log with an unanchored tail: a read refuses it without creating the append lock (no
    writer can be mid-append when its lock file does not exist)."""
    monkeypatch.setenv(dl.APPROVAL_KEY_ENV, KEY)
    monkeypatch.delenv(dl.ANCHORS_ENV, raising=False)
    led = DecisionLedger(tmp_path / "logs" / "x.approvals.jsonl", what="test log")
    led.append({"event": "approved", **_who()})
    # a second sealed record written without its anchor: an interrupted append
    recs = [json.loads(x) for x in led.path.read_text(encoding="utf-8").splitlines()]
    nxt = {"event": "approved", **_who("bob"), "seq": 2, "prev": recs[-1]["hash"]}
    nxt["hash"] = dl.digest_of(nxt, exclude=("hash", "seal"))
    nxt["seal"] = dl.seal_of(KEY.encode(), nxt["hash"])
    with open(led.path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(nxt, sort_keys=True) + "\n")
    if led.lock_path.exists():
        os.remove(led.lock_path)
    before = _tree(tmp_path)
    with pytest.raises(DecisionRefused) as e:
        DecisionLedger(led.path, what="test log").read()
    assert e.value.code == dl.LEDGER_INTEGRITY_FAILED
    assert not led.lock_path.exists() and _tree(tmp_path) == before


# --- R-029 Codex round 1 (TIDY2) ---------------------------------------------------------------
@pytest.mark.parametrize("weak", [b"", b"   ", b"short-key", b"x" * 31])
def test_a_weak_or_empty_supplied_key_is_refused(tmp_path, monkeypatch, weak):
    """f1 #2: a key handed to the ledger directly meets the SAME minimum as the deployment key."""
    monkeypatch.setenv(dl.APPROVAL_KEY_ENV, KEY)
    led = DecisionLedger(tmp_path / "logs" / "x.approvals.jsonl", key=weak, what="test log")
    before = _tree(tmp_path)
    with pytest.raises(DecisionRefused) as e:
        led.append({"event": "approved", **_who()})
    assert e.value.code == dl.APPROVAL_KEY_UNAVAILABLE
    assert _tree(tmp_path) == before
    ok = DecisionLedger(tmp_path / "logs" / "y.approvals.jsonl", key=KEY.encode(), what="t")
    assert ok.append({"event": "approved", **_who()})["seq"] == 1           # non-vacuous


def test_a_key_deleted_after_use_refuses_the_next_write(tmp_path, no_key):
    """f1 #3: a long-lived ledger does not keep appending under a key that is gone."""
    dl.provision_approval_key()
    led = DecisionLedger(tmp_path / "logs" / "x.approvals.jsonl", what="test log")
    led.append({"event": "approved", **_who()})
    no_key.unlink()
    before = led.path.read_bytes()
    with pytest.raises(DecisionRefused) as e:
        led.append({"event": "approved", **_who("bob")})
    assert e.value.code == dl.APPROVAL_KEY_UNAVAILABLE
    assert led.path.read_bytes() == before
    with pytest.raises(DecisionRefused):
        led.read()


def test_provisioning_never_overwrites_a_key_created_meanwhile(tmp_path, no_key, monkeypatch):
    """f1 #7: another provisioner creates the key after our existence check; ours never replaces
    it, even where hard links are unsupported and rename overwrites (POSIX semantics)."""
    import os as _os
    from pathlib import Path as _P

    real_is_file = _P.is_file
    first = {"done": False}

    def racing_is_file(self):
        if self == no_key.resolve() and not first["done"]:
            first["done"] = True
            self.parent.mkdir(parents=True, exist_ok=True)
            self.write_text("k" * 64, encoding="utf-8")       # the other provisioner wins
            return False
        return real_is_file(self)

    monkeypatch.setattr(_P, "is_file", racing_is_file)
    monkeypatch.setattr(_os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError("no links")))
    monkeypatch.setattr(_os, "rename", _os.replace)
    dl.provision_approval_key()
    assert no_key.read_text(encoding="utf-8") == "k" * 64
