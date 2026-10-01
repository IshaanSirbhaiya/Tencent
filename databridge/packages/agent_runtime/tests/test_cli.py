"""The CLI fails in plain words, never with a traceback, and never runs without a model."""
from __future__ import annotations

from agent_runtime_helpers import followup_spec, make_pair, refs_in, scripted_harness

from databridge_agent_runtime import cli, run_agent
from databridge_agent_runtime.runs import find_run


def test_run_without_a_model_says_so(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert cli.main(["run", "anything"]) == 2
    assert "No model is configured" in capsys.readouterr().err


def test_show_unknown_run_is_a_plain_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WALNUT_RUNS_DIR", str(tmp_path))
    assert cli.main(["show", "run-nope"]) == 1
    assert "no run" in capsys.readouterr().err


def test_pending_and_reject_through_the_cli(store, gateway, compile_inputs, tmp_path,
                                            monkeypatch, capsys):
    monkeypatch.setenv("WALNUT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("WALNUT_CAPTURE_DIR", str(tmp_path / "capture"))
    spec, decl = make_pair(followup_spec(), save_followup_draft=False)

    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": "source.query:larkspur-db", "args": {"sql": "select 1"}}]}
        if i == 1:
            return {"actions": [{"name": "log_followup_task", "args": {"sql": "INSERT 1"},
                                 "derived_from": refs_in(obs)[:1]}]}
        return {"final": {"claims": []}}

    harness, _ = scripted_harness(script)
    rec = run_agent(spec, decl, store=store, harness=harness, gateway=gateway,
                    compile_inputs=compile_inputs)
    assert find_run(rec.run_id).is_file()
    assert cli.main(["pending"]) == 0
    assert rec.run_id in capsys.readouterr().out
    # a typed name is a claim, not a principal (R-017 t02 finding 3)
    assert cli.main(["reject", rec.run_id, "a1", "--by", "Dana Lee"]) == 1
    assert "not an authenticated principal" in capsys.readouterr().err
    # R-021 #5: the CLI cannot authenticate, so without the explicit DEV flag it decides nothing
    monkeypatch.setattr(cli, "os_account", lambda: "dana.lee")
    assert cli.main(["reject", rec.run_id, "a1"]) == 1
    assert "--dev-os-principal" in capsys.readouterr().err
    monkeypatch.setattr(cli, "os_account", lambda: "system")                # not a person
    assert cli.main(["reject", rec.run_id, "a1", "--dev-os-principal"]) == 1
    monkeypatch.setattr(cli, "os_account", lambda: "dana.lee")
    assert cli.main(["reject", rec.run_id, "a1", "--dev-os-principal"]) == 0
    out = capsys.readouterr().out
    assert '"rejected"' in out and '"dana.lee"' in out
