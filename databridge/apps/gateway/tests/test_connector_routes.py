"""`/connectors` — configure-by-search, not paste-YAML.

Four steps, one compiler: search the vendored catalogue, choose read or write, attach Skill
selections, add per-source extraction context — and the output is a declaration that compiles
through the exact same `load_mcp_declaration` path `/builder` uses. So the tests that matter here
are not about the search box; they are about whether the four steps actually produce something the
real compiler accepts, and whether the compiler's own refusal — not a hand-written one — is what
comes back when the configuration is unsafe.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import AUTH_HEADERS

DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]
for _p in ("packages/adapter", "packages/core", "packages/agent", "packages/intake"):
    _path = str(DATABRIDGE_ROOT / _p)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from databridge_gateway.connector_routes import (                              # noqa: E402
    CATALOGUE,
    SKILLS,
    register_connector_routes,
)


@pytest.fixture()
def client():
    app = FastAPI()
    register_connector_routes(app)
    with TestClient(app, headers=AUTH_HEADERS) as c:
        yield c


VENDORED_ID = "permits_registry_mcp"
NOT_VENDORED_ID = next(e["id"] for e in CATALOGUE if e["status"] == "not_yet_vendored")


# =======================================================================================
# the catalogue — search, never fetched
# =======================================================================================
def test_the_catalogue_lists_more_than_one_entry_and_both_states_are_present():
    statuses = {e["status"] for e in CATALOGUE}
    assert statuses == {"vendored", "not_yet_vendored"}
    assert len(CATALOGUE) >= 5, "search over one entry is not search"


def test_every_catalogue_entry_has_a_digest_pinned_artefact_or_an_honest_reason():
    for e in CATALOGUE:
        if e["status"] == "vendored":
            assert (DATABRIDGE_ROOT / e["template"]).is_file(), e["id"]
            assert (DATABRIDGE_ROOT / e["vendored_path"]).is_file(), e["id"]
        else:
            assert e.get("not_vendored_reason"), f"{e['id']} has no honest reason recorded"


def test_search_matches_name_summary_and_tags(client):
    body = client.get("/connectors/catalogue", params={"q": "permit"}).json()
    ids = [s["id"] for s in body["servers"]]
    assert VENDORED_ID in ids

    body = client.get("/connectors/catalogue", params={"q": "zz-nothing-matches-zz"}).json()
    assert body["servers"] == []

    # A well-known integration with no reviewed artefact is still FOUND — it is not hidden, only
    # honestly marked.
    body = client.get("/connectors/catalogue", params={"q": "slack"}).json()
    assert any(s["status"] == "not_yet_vendored" for s in body["servers"])


def test_vendored_entries_sort_before_not_yet_vendored(client):
    body = client.get("/connectors/catalogue").json()
    statuses = [s["status"] for s in body["servers"]]
    first_not_vendored = statuses.index("not_yet_vendored") if "not_yet_vendored" in statuses \
        else len(statuses)
    assert all(s == "vendored" for s in statuses[:first_not_vendored])


def test_a_single_entry_is_served_by_id(client):
    body = client.get(f"/connectors/catalogue/{VENDORED_ID}")
    assert body.status_code == 200
    assert body.json()["id"] == VENDORED_ID

    missing = client.get("/connectors/catalogue/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["refusal"]["code"] == "UNKNOWN_SERVER"


def _documented_skills() -> dict[str, str]:
    """WS id → skill name, read from the catalogue document's own section headings (HC1: the
    expectation is derived from the source the table cites, never a count written here)."""
    import re
    from pathlib import Path

    doc = Path(__file__).resolve().parents[4] / "GOA Submission" / "11_SKILL_CATALOGUE.md"
    text = doc.read_text(encoding="utf-8")
    return dict(re.findall(r"^## \d+\. (WS-\d{2}) — `([^`]+)`", text, flags=re.M))


def test_the_skill_vocabulary_matches_the_catalogue_document(client):
    documented = _documented_skills()
    assert len(documented) >= 1, "no WS sections parsed — the derivation would be vacuous"
    body = client.get("/connectors/skills").json()
    assert {s["id"]: s["skill"] for s in body["skills"]} == documented
    assert {s["id"]: s["skill"] for s in SKILLS} == documented


# =======================================================================================
# compiling a real connector — the same loader `/builder` uses
# =======================================================================================
def test_a_vendored_server_compiles_read_only_by_default(client):
    body = client.post("/connectors/compile", json={"server_id": VENDORED_ID}).json()
    assert body["compiled"] is True, body.get("refusal")
    out = body["compiles_to"]
    assert out["source_id"] == VENDORED_ID
    assert out["grants"]["read"], "an adapter that may invoke nothing is not a grant"
    assert out["grants"]["write"] == []
    assert "adapter:" in body["declaration"]


def test_skills_and_extraction_context_land_on_the_generated_declaration_inertly(client):
    plain = client.post("/connectors/compile", json={"server_id": VENDORED_ID}).json()
    body = client.post("/connectors/compile", json={
        "server_id": VENDORED_ID,
        "skills": ["WS-01", "WS-02"],
        "extraction_context": "Prefer the status field over any status mentioned in notes.",
    }).json()
    assert body["compiled"] is True, body.get("refusal")
    assert "_connector" in body["declaration"]
    assert "WS-01" in body["declaration"]
    assert "Prefer the status field" in body["declaration"]
    # Recorded back for the UI, AND the compiled grants are untouched by it — the free text
    # cannot have widened what the adapter may read.
    assert body["connector"]["skills"] == ["WS-01", "WS-02"]
    assert body["compiles_to"]["grants"]["read"] == plain["compiles_to"]["grants"]["read"]


def test_an_unknown_skill_is_refused_before_anything_compiles(client):
    # A pre-validation refusal, same class as `/builder`'s DECLARATION_TOO_LARGE: it never reaches
    # the loader, so it is not the "the compile itself found something wrong" 200 shape.
    body = client.post("/connectors/compile", json={
        "server_id": VENDORED_ID, "skills": ["WS-99-not-real"],
    })
    assert body.status_code == 422
    out = body.json()
    assert out["refusal"]["code"] == "UNKNOWN_SKILL"
    assert "compiles_to" not in out


def test_extraction_context_over_the_limit_is_refused(client):
    body = client.post("/connectors/compile", json={
        "server_id": VENDORED_ID, "extraction_context": "x" * 5000,
    }).json()
    assert body["refusal"]["code"] == "CONTEXT_TOO_LARGE"


# ---------------------------------------------------------------------------------------------
# the write toggle — the moment the founder's spec is about
# ---------------------------------------------------------------------------------------------
def test_choosing_write_is_refused_by_the_real_compiler_naming_what_is_missing(client):
    body = client.post("/connectors/compile", json={
        "server_id": VENDORED_ID, "mode": "write",
    }).json()
    assert body["compiled"] is False
    refusal = body["refusal"]
    assert refusal["kind"] == "WriteCapabilityRefused"
    assert refusal["code"] == "WRITE_CAPABILITY_REFUSED"
    # It must NAME what is missing, not just say no.
    for term in ("ActionType", "automation.mode", "reversal"):
        assert term in refusal["message"], refusal["message"]
    # The generated (refused) declaration is still shown — nothing the operator configured is lost.
    assert body["declaration"] is not None
    assert "write" in body["declaration"]


def test_the_write_refusal_is_a_200_result_not_a_transport_error(client):
    r = client.post("/connectors/compile", json={"server_id": VENDORED_ID, "mode": "write"})
    assert r.status_code == 200


def test_write_mode_asks_for_exactly_the_read_allowlist_never_a_silent_noop(client):
    """The switch must not lie: it asks for real write access to what read would have reached,
    not a placeholder tool name that could never be confused with a genuine request."""
    read_only = client.post("/connectors/compile", json={"server_id": VENDORED_ID}).json()
    write = client.post(
        "/connectors/compile", json={"server_id": VENDORED_ID, "mode": "write"}).json()
    assert write["compiled"] is False
    assert "capabilities:" in write["declaration"]
    for tool in read_only["compiles_to"]["grants"]["read"]:
        assert tool in write["declaration"]


# ---------------------------------------------------------------------------------------------
# an honestly not-yet-vendored server
# ---------------------------------------------------------------------------------------------
def test_a_not_yet_vendored_server_refuses_before_reaching_the_loader(client):
    body = client.post("/connectors/compile", json={"server_id": NOT_VENDORED_ID}).json()
    assert body["compiled"] is False
    assert body["refusal"]["code"] == "SERVER_NOT_VENDORED"
    assert body["refusal"]["stage"] == "catalogue"
    assert body["declaration"] is None
    entry = next(e for e in CATALOGUE if e["id"] == NOT_VENDORED_ID)
    assert entry["not_vendored_reason"] in body["refusal"]["message"]


def test_an_unknown_server_id_is_a_404(client):
    body = client.post("/connectors/compile", json={"server_id": "not-a-real-id"})
    assert body.status_code == 404
    assert body.json()["refusal"]["code"] == "UNKNOWN_SERVER"


def test_a_custom_connector_name_renames_adapter_and_source_id(client):
    body = client.post("/connectors/compile", json={
        "server_id": VENDORED_ID, "name": "my_permits_connector",
    }).json()
    assert body["compiled"] is True, body.get("refusal")
    assert body["compiles_to"]["source_id"] == "my_permits_connector"


# ---------------------------------------------------------------------------------------------
# the MCP config modal — real template-derived fields, and an env NAME (never a value)
# ---------------------------------------------------------------------------------------------
def test_catalogue_entries_carry_real_tools_and_transport_from_the_reviewed_template(client):
    body = client.get(f"/connectors/catalogue/{VENDORED_ID}").json()
    assert body["tools_exposed"] == ["permit_lookup", "permit_search"]
    assert body["transport"] == "stdio"
    assert body["sandbox"] == "subprocess"
    assert body["digest"] and body["digest"].startswith("sha256:")

    not_vendored = client.get(f"/connectors/catalogue/{NOT_VENDORED_ID}").json()
    assert not_vendored["tools_exposed"] == []
    assert not_vendored["transport"] is None


def test_declared_env_names_land_on_the_real_mcp_env_field_with_empty_values(client):
    body = client.post("/connectors/compile", json={
        "server_id": VENDORED_ID, "env_names": ["AIRTABLE_API_KEY", "AIRTABLE_BASE_ID"],
    }).json()
    assert body["compiled"] is True, body.get("refusal")
    assert "AIRTABLE_API_KEY" in body["declaration"]
    # A NAME, never a value: the declaration must not contain anything that looks like a filled-in
    # secret, and the field the form writes to is `mcp.env`, not the inert `_connector` block.
    assert "mcp:" in body["declaration"]
    assert body["connector"]["env_names"] == ["AIRTABLE_API_KEY", "AIRTABLE_BASE_ID"]


def test_a_lowercase_env_name_is_refused_before_compiling(client):
    body = client.post("/connectors/compile", json={
        "server_id": VENDORED_ID, "env_names": ["not_upper_snake"],
    })
    assert body.status_code == 422
    assert body.json()["refusal"]["code"] == "INVALID_ENV_NAME"


def test_declared_adapter_agent_configuration_is_recorded_inertly(client):
    plain = client.post("/connectors/compile", json={"server_id": VENDORED_ID}).json()
    body = client.post("/connectors/compile", json={
        "server_id": VENDORED_ID,
        "agent_row_limit": 50,
        "agent_named_queries": "recent_denials: permits denied in the last 30 days",
    }).json()
    assert body["compiled"] is True, body.get("refusal")
    assert "recent_denials" in body["declaration"]
    assert body["connector"]["agent"]["row_limit"] == 50
    # Declared configuration for the Shell, not a widened grant: the compiled capability grants
    # are identical to the plain compile.
    assert body["compiles_to"]["grants"]["read"] == plain["compiles_to"]["grants"]["read"]


def test_oversized_named_queries_is_refused(client):
    body = client.post("/connectors/compile", json={
        "server_id": VENDORED_ID, "agent_named_queries": "x" * 3000,
    }).json()
    assert body["refusal"]["code"] == "AGENT_NAMED_QUERIES_TOO_LARGE"
