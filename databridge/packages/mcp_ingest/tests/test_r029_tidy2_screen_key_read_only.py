"""R-029 TIDY2 item 2 (screen overrides): the approval key is never created implicitly.

Before, `screen._approval_key` CREATED the key file on first use, so `load_overrides` (a READ, run on
every sync and every holds page) wrote a secret to disk, and a deleted key was silently replaced by
a new one. Now it is the one core rule: a read with no key honours nothing and writes nothing; an
override with no key is refused explicitly and writes nothing; provisioning is explicit.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from databridge_mcp_ingest import paths
from databridge_mcp_ingest.screen import (OverrideRefused, approve_override, load_overrides,
                                          override_log_path)

SOURCE = "crm_db"


def _tree(*roots: Path) -> dict:
    out = {}
    for root in roots:
        if root.exists():
            for p in sorted(root.rglob("*")):
                out[str(p)] = (p.is_dir(), p.stat().st_size, p.stat().st_mtime_ns)
    return out


@pytest.fixture
def owner():
    from databridge_agent_runtime import register_authenticator

    return register_authenticator("test:tidy2").issue("dana.owner")


@pytest.fixture
def key_file(tmp_path, monkeypatch):
    monkeypatch.delenv("WALNUT_APPROVAL_KEY", raising=False)
    kp = tmp_path / "keys" / "approval.key"
    monkeypatch.setenv("WALNUT_APPROVAL_KEY_FILE", str(kp))
    return kp


def _digest(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def test_an_override_without_a_key_is_refused_and_writes_nothing(walnut_env, owner, key_file,
                                                                 tmp_path):
    before = _tree(tmp_path, paths.sources_dir())
    with pytest.raises(OverrideRefused) as e:
        approve_override(SOURCE, "x", digest=_digest("v"), principal=owner, reason="ok")
    assert "provision" in str(e.value) and str(key_file.parent) not in str(e.value)
    assert not key_file.exists() and not override_log_path(SOURCE).exists()
    assert _tree(tmp_path, paths.sources_dir()) == before


def test_reading_overrides_without_a_key_honours_nothing_and_writes_nothing(walnut_env, owner,
                                                                            key_file, tmp_path):
    from databridge_core.decision_ledger import provision_approval_key

    d = _digest("v1")
    provision_approval_key()
    approve_override(SOURCE, "real", digest=d, principal=owner, reason="ok")
    assert load_overrides(SOURCE).allows("real", d)                         # non-vacuous
    key_file.unlink()
    before = _tree(tmp_path, paths.sources_dir())
    got = load_overrides(SOURCE)
    assert not got.allows("real", d)
    assert not key_file.exists()
    assert _tree(tmp_path, paths.sources_dir()) == before
