"""`GET /packs` — the registry, served from the pack directory rather than bundled in the UI.

What is guarded:

1. **The route serves the real pack directory**, not a copy: every pack with an `ontology.yaml` on
   disk appears, and its ontology counts match what the loader reports.
2. **Availability is computed from the deployment binding**, not declared by the pack. A pack on
   disk that no binding enabled is `available: false` and says which binding excluded it — a pack
   that could declare itself available could claim to be enabled when nothing enabled it.
3. **The shape the frontend validates** (`apps/frontend/lib/packs.ts` `isRegistry`) is satisfied,
   so the bundled fallback becomes dead code rather than the thing actually rendering.
4. **A pack with no `ui.yaml` is reported, never invented.** Synthesised nouns would be words on a
   screen that no pack ever said.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import AUTH_HEADERS

from databridge_gateway.pack_routes import build_registry


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    from databridge_gateway.pack_routes import register_pack_routes

    register_pack_routes(app)
    with TestClient(app, headers=AUTH_HEADERS) as c:
        yield c


@pytest.fixture(scope="module")
def registry(client):
    r = client.get("/packs")
    assert r.status_code == 200, r.text
    return r.json()


def test_every_pack_on_disk_appears(registry):
    from databridge_graph.packs import available_packs

    assert {p["id"] for p in registry["packs"]} == set(available_packs())
    assert registry["packs"], "a checkout with no packs is a checkout that cannot extract anything"


def test_the_ontology_counts_come_from_the_loader_not_from_this_route(registry):
    """The route must not maintain its own idea of what a pack declares."""
    from databridge_graph.packs import load_pack

    for entry in registry["packs"]:
        pack = load_pack(entry["id"])
        assert entry["node_kind_count"] == len(pack.node_kinds)
        assert entry["edge_kind_count"] == len(pack.edge_kinds)
        assert entry["version"] == pack.version


def test_availability_is_the_deployment_binding_and_says_so(registry):
    from databridge_graph.packs import configured_packs

    bound = set(configured_packs())
    assert set(registry["enabled_packs"]) == bound
    for entry in registry["packs"]:
        assert entry["enabled_by_deployment"] == (entry["id"] in bound)
        if entry["id"] not in bound:
            assert entry["available"] is False
            # The note has to name the reason, not merely assert unavailability: "not available"
            # and "not enabled here" are different facts and a switcher must not merge them.
            assert "deployment" in entry["unavailable_note"]


def test_the_default_pack_is_one_the_deployment_actually_bound(registry):
    assert registry["default_pack"] in {p["id"] for p in registry["packs"]}
    chosen = next(p for p in registry["packs"] if p["id"] == registry["default_pack"])
    assert chosen["enabled_by_deployment"] is True


def test_the_payload_satisfies_the_frontends_own_validator(registry):
    """`isRegistry` in `apps/frontend/lib/packs.ts`, transcribed. If this fails the frontend
    silently falls back to its bundled file and the route is decoration."""
    assert isinstance(registry["default_pack"], str)
    assert isinstance(registry["packs"], list) and registry["packs"]
    assert all(isinstance(p["id"], str) and p.get("nouns") for p in registry["packs"])


def test_an_available_pack_carries_the_vocabulary_the_chrome_renders(registry):
    available = [p for p in registry["packs"] if p["available"]]
    assert available, "no pack is available; the app would have no surfaces to open"
    for entry in available:
        assert entry["nouns"]["subject"], "a pack must name the entity a determination is about"
        assert entry["surfaces"], "available means there is a screen to open"
        assert "ui_incomplete" not in entry


def test_a_pack_without_a_ui_file_is_reported_not_invented(tmp_path):
    """The refusal that matters: a missing declaration produces a reason, never a plausible noun."""
    (tmp_path / "solo").mkdir()
    (tmp_path / "solo" / "ontology.yaml").write_text(
        "pack: solo\nversion: '1.0'\n"
        "node_kinds:\n  - name: thing\n    binds_to: entity\n"
        "edge_kinds:\n  - name: references\n    binds_to: references\n",
        encoding="utf-8")
    out = build_registry(root=tmp_path, enabled=("solo",))
    entry = out["packs"][0]
    assert entry["id"] == "solo"
    assert entry["available"] is False
    assert "nouns" not in entry
    assert "ui.yaml" in entry["unavailable_note"]
    assert "nouns" in entry["ui_incomplete"]


def test_an_unknown_pack_is_a_404_not_an_empty_pack(client):
    r = client.get("/packs/not_a_pack")
    assert r.status_code == 404
    assert r.json()["refusal"]["code"] == "UNKNOWN_PACK"
