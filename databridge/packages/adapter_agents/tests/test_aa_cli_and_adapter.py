"""CLI over an injected manager, and the real-module adapter's plan-status read."""
import json

import pytest
import yaml

from databridge_adapter_agents.cli import main


def test_cli_create_list_sync_health(env, spec_factory, capsys, tmp_path):
    for c in ("files-a", "db-b"):
        env.service.configure(c, items={"d": c})
    path = tmp_path / "agent.yaml"
    path.write_text(yaml.safe_dump(spec_factory()), encoding="utf-8")

    assert main(["create", str(path)], manager=env.manager) == 0
    created = json.loads(capsys.readouterr().out)["created"]

    assert main(["list"], manager=env.manager) == 0
    listed = json.loads(capsys.readouterr().out)["agents"]
    assert [a["id"] for a in listed] == [created]

    assert main(["sync", created], manager=env.manager) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "completed" and out["trigger"]["via"] == "cli"

    assert main(["health", created], manager=env.manager) == 0
    assert json.loads(capsys.readouterr().out)["overall"] == "healthy"

    env.service.configure("db-b", raises=RuntimeError("down"))
    assert main(["sync", created], manager=env.manager) == 1
    capsys.readouterr()
    assert main(["sync", "no_such_agent"], manager=env.manager) == 2


def test_mcp_ingest_adapter_reads_plan_status(tmp_path):
    plans = pytest.importorskip("databridge_mcp_ingest.plans")
    from databridge_adapter_agents import McpIngestService

    svc = McpIngestService(sources_root=tmp_path)
    assert svc.plan_status("files-a") is None
    plans.save_plan(plans.CrawlPlan(source_id="files-a"), tmp_path)
    assert svc.plan_status("files-a") == "proposed"
    plans.save_plan(plans.CrawlPlan(source_id="files-a", status="approved",
                                    approved_by="staff:ops"), tmp_path)
    assert svc.plan_status("files-a") == "approved"
