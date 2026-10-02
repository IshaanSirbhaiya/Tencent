"""SCR6 / FIN-03 — an adapter agent bound to a pack that ships no `screen.yaml` syncs under the
ENGINE DEFAULT screening policy, and says so.

The dry run: "keep it in the graph, hourly" compiled for the finance pack, activated, and every
sync was REFUSED ("spec.screen.pack is 'finance' but this deployment screens with the bundled
defaults") — because finance declares no screen. A pack that declares none is screened by the
engine default (credential gate, prompt-injection hold, PII masks); the gate accepts that, still
refuses where ANOTHER pack's screen is in force or the pack does not exist, and the sync reports
the policy. Both estates' packs: finance (declares none) and workspace (declares one) — derived
from the packs on disk, never typed in.
"""
from __future__ import annotations

import json
import shutil
from types import SimpleNamespace

import pytest

from aa_drift_kit import OWNER, agent_spec, drift_env, write_db

CONN = "crm"


def _packs():
    from databridge_mcp_ingest import paths, screen

    root = paths.packs_dir()
    packs = sorted(p.name for p in root.iterdir() if (p / "ontology.yaml").is_file())
    without = [p for p in packs if screen.pack_screen_file(p) is None]
    with_ = [p for p in packs if screen.pack_screen_file(p) is not None]
    assert "finance" in without and "workspace" in with_          # the two estates' packs
    return without, with_


def _refusal(pack):
    from databridge_adapter_agents.manager import AdapterAgentManager

    return AdapterAgentManager.screen_pack_refusal(SimpleNamespace(screen={"pack": pack}))


def test_fin03_under_a_deployment_whose_packs_declare_no_screen_their_specs_run():
    from databridge_graph.packs import deployment
    from databridge_mcp_ingest import screen

    without, with_ = _packs()
    with deployment("finance"):
        cfg = screen.load_screen()                            # the REAL resolution, no patching
        assert screen.screen_policy(cfg)["policy"] == "engine_default"
        for p in without:
            assert _refusal(p) is None, p                     # the dry run's refusal is gone
        for p in with_:                                       # a pack WITH a screen still must
            why = _refusal(p)                                 # be the one in force
            assert why and "bundled defaults" in why, p
        for bad in ("no_such_pack_scr6", "..", "../" + without[0]):
            why = _refusal(bad)
            assert why and "no such pack" in why, bad


def test_fin03_another_packs_screen_in_force_still_refuses_a_pack_that_declares_none():
    from databridge_graph.packs import deployment

    without, with_ = _packs()
    with deployment("workspace"):
        assert _refusal("workspace") is None
        for p in without:
            why = _refusal(p)
            assert why and "declares no screen.yaml" in why and "workspace" in why, p


@pytest.fixture
def env_no_pack_screen(tmp_path, monkeypatch):
    """A deployment of the workspace pack in which the pack declares NO screen: the packs root
    is a copy with `workspace/screen.yaml` removed, so mcp_ingest and the graph both resolve the
    engine default for real (nothing patched)."""
    from databridge_mcp_ingest import paths

    root = tmp_path / "packs"
    shutil.copytree(paths.packs_dir(), root,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (root / "workspace" / "screen.yaml").unlink()
    monkeypatch.setenv("DATABRIDGE_PACKS_ROOT", str(root))
    yield from drift_env(tmp_path, monkeypatch)


def test_fin03_the_sync_runs_and_reports_the_engine_default_policy(env_no_pack_screen, tmp_path):
    env = env_no_pack_screen
    db = tmp_path / "crm.sqlite"
    write_db(db)
    import databridge_mcp_ingest as mi

    from aa_drift_kit import connection

    mi.register_connection(connection(CONN, db=db))
    mi.propose_plan(CONN)
    mi.approve_plan(CONN, approved_by=OWNER.principal_id, principal=OWNER)
    env.approve_first_mapping(CONN)
    spec = env.manager.create({**agent_spec(CONN), "screen": {"pack": "workspace"}}, by=OWNER)
    assert env.manager.agent_gate(spec) == []
    s = env.sync(spec.id)
    assert s.status == "completed", s.reason
    assert s.screen_policy["policy"] == "engine_default"
    assert s.as_dict()["screen_policy"]["policy"] == "engine_default"
    record = json.loads(open(s.record_path, encoding="utf-8").read())
    assert record["screen_policy"]["policy"] == "engine_default"
    # the ingest run it drove reports the same policy, and says it in its notes
    from databridge_mcp_ingest.screen import DEFAULT_POLICY_NOTE

    report = json.loads((env.run_dir(s, CONN) / "report.json").read_text(encoding="utf-8"))
    assert report["screen_policy"]["policy"] == "engine_default"
    assert any(DEFAULT_POLICY_NOTE in n for n in report["notes"])
