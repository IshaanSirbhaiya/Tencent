"""`/builder` — one pane compiles for real, and the other says plainly that it cannot.

`DEMO_AND_FRONTEND.md` §2.1: two panes, one compiler, and **the moment that sells it is a
refusal**. So the tests are about which half of that is a measurement:

* the ADAPTER pane runs `load_mcp_declaration`, and the refusals it shows are the compiler's own —
  delete the written justification behind a `trusted` field and the compile is refused, by name,
  with the reason. That is the analogue of §2.1's "delete the `citation:` line".
* the AGENT pane runs `databridge_agentdef.compile_agent`. Deleting the `citation:` line from
  `packs/finance/agents/credit_memo_drafter.yaml` — §2.1's own example — comes back
  `REFUSED / no_citation_rule` with the compiler's sentence, and *every* reason travels rather
  than only the first. Where that package is not importable the route answers **501 naming what
  is missing**, and the test asserts the 501 body carries nothing that could pass for a compile.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import AUTH_HEADERS

DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]
for _p in ("packages/adapter", "packages/agentdef", "packages/core"):
    _path = str(DATABRIDGE_ROOT / _p)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from databridge_gateway.builder_routes import (                                # noqa: E402
    AGENT_MISSING_COMPONENTS,
    register_builder_routes,
)

#: The shipped declaration the pane opens with.
DECLARATION = (DATABRIDGE_ROOT / "fixtures" / "packs" / "government" / "adapters"
               / "permits_registry_mcp.yaml")

#: §2.1's own worked example.
AGENT_DECLARATION = DATABRIDGE_ROOT / "packs" / "finance" / "agents" / "credit_memo_drafter.yaml"

#: Anything that would let a 501 be mistaken for a compile result.
COMPILE_SHAPED = ("compiles_to", "grants", "sink_policy", "budget", "eval_binding", "compiled")


@pytest.fixture()
def client():
    app = FastAPI()
    register_builder_routes(app)
    with TestClient(app, headers=AUTH_HEADERS) as c:
        yield c


@pytest.fixture()
def source() -> str:
    return DECLARATION.read_text(encoding="utf-8")


# =======================================================================================
# the adapter pane — a real compiler
# =======================================================================================
def test_the_shipped_declarations_are_discovered_not_listed(client):
    body = client.get("/builder/adapter/declarations").json()
    ids = [d["id"] for d in body["declarations"]]
    assert "fixtures/packs/government/adapters/permits_registry_mcp.yaml" in ids
    assert body["default"] in ids


def test_a_declaration_is_served_by_path_and_only_from_the_repo(client):
    body = client.get("/builder/adapter/declaration",
                      params={"id": "fixtures/packs/government/adapters/permits_registry_mcp.yaml"})
    assert body.status_code == 200
    assert "adapter: permits_registry_mcp" in body.json()["declaration"]

    escape = client.get("/builder/adapter/declaration", params={"id": "../../../etc/passwd"})
    assert escape.status_code == 404


def test_the_real_declaration_compiles_and_reports_what_it_compiles_to(client, source):
    body = client.post("/builder/adapter/compile", json={"declaration": source}).json()
    assert body["compiled"] is True, body.get("refusal")
    out = body["compiles_to"]
    assert out["source_id"] == "permits_registry_mcp"
    assert out["pinning"]["tier"] == "T3"
    # Capability grants: a closed read allowlist and an empty write set.
    assert out["grants"]["read"], "an adapter that may invoke nothing is not a grant"
    assert out["grants"]["write"] == []
    # Sink policy: egress empty by declaration.
    assert out["sink_policy"]["egress_allow"] == []
    # Every trusted field carries the written justification the loader demanded.
    for field in out["trust"]:
        if field["trust"] == "trusted":
            assert field["justification"], f"{field['field']} is trusted with no reason recorded"


def test_deleting_the_written_justification_is_a_reasoned_refusal(client, source):
    """§2.1's money shot, in the adapter's vocabulary: remove the reason, lose the compile."""
    raw = yaml.safe_load(source)
    raw["trusted_justification"].pop("status")
    body = client.post("/builder/adapter/compile",
                       json={"declaration": yaml.safe_dump(raw)}).json()
    assert body["compiled"] is False
    refusal = body["refusal"]
    assert refusal["kind"] == "McpDeclarationRefused"
    assert refusal["code"] == "MCP_DECLARATION_REFUSED"
    assert "status" in refusal["message"], "a refusal that does not name the line is not a review"
    assert "compiles_to" not in body


def test_a_write_capability_is_refused_by_name(client, source):
    raw = yaml.safe_load(source)
    raw["capabilities"]["write"] = ["permit_update"]
    body = client.post("/builder/adapter/compile",
                       json={"declaration": yaml.safe_dump(raw)}).json()
    assert body["compiled"] is False
    assert body["refusal"]["kind"] == "WriteCapabilityRefused"
    assert body["refusal"]["message"]


def test_a_server_reference_in_the_launch_position_is_refused(client, source):
    raw = yaml.safe_load(source)
    raw["mcp"]["vendored_path"] = "https://registry.example/permits-mcp.py"
    body = client.post("/builder/adapter/compile",
                       json={"declaration": yaml.safe_dump(raw)}).json()
    assert body["compiled"] is False
    assert body["refusal"]["code"] == "VENDORED_PATH_NOT_CONTAINED"


def test_the_compile_route_will_not_digest_a_file_outside_the_vendored_roots(client, source):
    """Otherwise the compile button is a file-existence oracle over the host."""
    raw = yaml.safe_load(source)
    raw["mcp"]["vendored_path"] = "../../../../Windows/System32/drivers/etc/hosts"
    body = client.post("/builder/adapter/compile",
                       json={"declaration": yaml.safe_dump(raw)}).json()
    assert body["compiled"] is False
    assert body["refusal"]["code"] == "VENDORED_PATH_NOT_CONTAINED"


def test_a_changed_artefact_quarantines_rather_than_recompiling(client, source):
    raw = yaml.safe_load(source)
    raw["mcp"]["digest"] = "sha256:" + "0" * 64
    body = client.post("/builder/adapter/compile",
                       json={"declaration": yaml.safe_dump(raw)}).json()
    assert body["compiled"] is False
    assert body["refusal"]["kind"] == "ServerQuarantined"


def test_invalid_yaml_is_a_refusal_with_a_stage_not_a_500(client):
    body = client.post("/builder/adapter/compile", json={"declaration": "a: [1, 2\n  b: 3"})
    assert body.status_code == 200
    assert body.json()["compiled"] is False
    assert body.json()["refusal"]["stage"] == "parse"


def test_a_refusal_is_a_result_and_not_an_http_error(client, source):
    """A 4xx would make the browser treat the most important answer this surface gives as a bug."""
    raw = yaml.safe_load(source)
    raw["capabilities"]["write"] = ["anything"]
    r = client.post("/builder/adapter/compile", json={"declaration": yaml.safe_dump(raw)})
    assert r.status_code == 200


# =======================================================================================
# the agent pane — a real compiler, and the refusal that sells the surface
# =======================================================================================
@pytest.fixture()
def agent_source() -> str:
    return AGENT_DECLARATION.read_text(encoding="utf-8")


def test_the_agent_declarations_are_discovered(client):
    body = client.get("/builder/agent/declarations").json()
    ids = [d["id"] for d in body["declarations"]]
    assert "packs/finance/agents/credit_memo_drafter.yaml" in ids


def test_a_real_agent_declaration_compiles_into_four_artefacts(client, agent_source):
    body = client.post("/builder/agent/compile", json={"declaration": agent_source})
    assert body.status_code == 200, body.text
    out = body.json()
    assert out["compiled"] is True, out.get("refusals")
    assert out["outcome"] == "compiled"
    assert out["refusals"] == [], "there is no 'compiled with reservations'"
    compiled = out["compiles_to"]
    # Capability grants, sink policy, budget admission, eval binding — §2.1's four headings.
    assert compiled["grants"]["grants"], "an agent with no grant cannot run"
    assert compiled["sink_policy"]
    assert compiled["budget"]["tokens_per_run"] > 0
    assert compiled["evals"]["records"] > 0
    assert out["declaration_digest"] and out["catalogue_digest"]


def test_deleting_the_citation_line_is_the_refusal(client, agent_source):
    """`DEMO_AND_FRONTEND.md` §2.1's money shot, executed rather than illustrated."""
    raw = yaml.safe_load(agent_source)
    del raw["citation"]
    out = client.post("/builder/agent/compile",
                      json={"declaration": yaml.safe_dump(raw, sort_keys=False)}).json()
    assert out["compiled"] is False
    assert out["outcome"] == "refused"
    codes = [r["code"] for r in out["refusals"]]
    assert "no_citation_rule" in codes
    detail = next(r["detail"] for r in out["refusals"] if r["code"] == "no_citation_rule")
    assert "SourcePointer" in detail, "the refusal must say what the rule protects"
    assert "compiles_to" not in out or out["compiles_to"] is None


def test_every_reason_travels_not_only_the_first(client, agent_source):
    """Fixing one and meeting the next as a surprise is how a reviewer stops believing the tool."""
    raw = yaml.safe_load(agent_source)
    del raw["citation"]
    del raw["budget"]
    out = client.post("/builder/agent/compile",
                      json={"declaration": yaml.safe_dump(raw, sort_keys=False)}).json()
    codes = {r["code"] for r in out["refusals"]}
    assert "no_citation_rule" in codes
    assert len(codes) > 1, codes


def test_an_agent_refusal_is_a_result_and_not_an_http_error(client, agent_source):
    raw = yaml.safe_load(agent_source)
    del raw["citation"]
    r = client.post("/builder/agent/compile",
                    json={"declaration": yaml.safe_dump(raw, sort_keys=False)})
    assert r.status_code == 200


def test_a_shape_error_is_reported_at_the_parse_stage(client):
    """A typo and a governance decision must not look like the same kind of answer."""
    out = client.post("/builder/agent/compile",
                      json={"declaration": "agent: x\nversion: '3'\n"})
    assert out.status_code == 200
    assert out.json()["compiled"] is False
    assert out.json()["stage"] == "parse"


def test_the_agent_status_route_reports_the_compiler_is_reachable(client):
    body = client.get("/builder/agent/status").json()
    assert body["available"] is True
    assert body["endpoint"] == "POST /builder/agent/compile"
    assert body["missing"] == []


def test_without_the_compiler_the_route_is_a_501_naming_what_is_missing(client, monkeypatch,
                                                                       agent_source):
    """The honest-placeholder path, exercised rather than only described.

    `sys.modules[name] = None` makes `import name` raise `ImportError`, which is exactly the state
    a checkout without `packages/agentdef` on the path is in. The route must then name the missing
    components and the endpoint that will serve them — and must return nothing a reader could
    mistake for a compile result.
    """
    monkeypatch.setitem(sys.modules, "databridge_agentdef", None)

    r = client.post("/builder/agent/compile", json={"declaration": agent_source})
    assert r.status_code == 501
    refusal = r.json()["refusal"]
    assert refusal["code"] == "AGENT_COMPILER_NOT_IMPLEMENTED"
    assert refusal["missing"] == list(AGENT_MISSING_COMPONENTS)
    assert refusal["missing"], "a 501 that does not say what is missing is just a failure"
    assert refusal["endpoint"] == "POST /builder/agent/compile"
    assert not set(refusal) & set(COMPILE_SHAPED)

    status = client.get("/builder/agent/status").json()
    assert status["available"] is False
    assert status["missing"] == list(AGENT_MISSING_COMPONENTS)
