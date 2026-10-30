"""Round 2b finding 2: ``workbuddy_setup.py`` never writes the compliance connector into the same
WorkBuddy connector list as the employee connectors (tests the merge rule; does not run WorkBuddy)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "workbuddy_setup.py"


def _load():
    spec = importlib.util.spec_from_file_location("workbuddy_setup_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ALL = {"ib-as-jdoe": {"x": 1}, "ib-as-ken": {"x": 2}, "ib-compliance": {"x": 3}}


def test_employee_mode_drops_compliance_even_a_stale_entry():
    ws = _load()
    current = {"mcpServers": {"other": {"keep": True}, "ib-compliance": {"stale": True}}}
    out = ws.merge_user_servers(current, ALL, compliance=False)
    assert set(out) == {"other", "ib-as-jdoe", "ib-as-ken"}
    assert out["other"] == {"keep": True}


def test_compliance_mode_writes_only_compliance_and_removes_employees():
    ws = _load()
    current = {"mcpServers": {"other": {"keep": True}, "ib-as-jdoe": {"old": 1}}}
    out = ws.merge_user_servers(current, ALL, compliance=True)
    assert set(out) == {"other", "ib-compliance"}


def test_flags_are_mutually_exclusive(capsys):
    ws = _load()
    try:
        ws.main(["--user-config", "--compliance-user-config"])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover
        raise AssertionError("combining the two flags must be refused")
