"""HC1 (2026-10-01), SWEEP hardcode audit #10: `/intake` (the determination surface) and `/outbox`
are mounted only when a bound pack declares them in its `gateway_routes.yaml`."""
from __future__ import annotations

import pytest
import yaml

from databridge_gateway import create_app
from databridge_gateway.intake_routes import (LEGACY_PREFIX, ROUTE_GROUPS, ROUTES_FILENAME,
                                              PackRoutesError, pack_declared_routes)
from databridge_graph.packs import deployment, packs_root


def _declaring_packs() -> dict[str, set[str]]:
    out = {}
    for path in packs_root().glob(f"*/{ROUTES_FILENAME}"):
        out[path.parent.name] = set(yaml.safe_load(path.read_text(encoding="utf-8"))["routes"])
    return out


def _non_declaring_pack() -> str:
    declaring = _declaring_packs()
    names = sorted(p.parent.name for p in packs_root().glob("*/ontology.yaml")
                   if p.parent.name not in declaring)
    assert names, "every pack declares routes; the negative case cannot be exercised"
    return names[0]


def _paths(app) -> set[str]:
    return {getattr(r, "path", "") for r in app.routes}


def test_the_shipped_declarations_are_non_vacuous():
    declaring = _declaring_packs()
    assert len(declaring) >= 1
    assert set().union(*declaring.values()) == set(ROUTE_GROUPS)


def test_a_pack_that_declares_nothing_mounts_neither_route_group():
    with deployment(_non_declaring_pack()):
        assert pack_declared_routes() == frozenset()
        paths = _paths(create_app())
    assert not any(p.startswith(LEGACY_PREFIX) or p.startswith("/determination") for p in paths)
    assert not any(p.startswith("/outbox") for p in paths)


@pytest.mark.parametrize("pack", sorted(_declaring_packs()))
def test_a_declaring_pack_mounts_what_it_declares(pack):
    declared = _declaring_packs()[pack]
    with deployment(pack):
        paths = _paths(create_app())
    assert any(p.startswith(LEGACY_PREFIX) for p in paths) == ("determination" in declared)
    assert any(p.startswith("/outbox") for p in paths) == ("outbox" in declared)


def test_an_unknown_route_group_fails_loud(tmp_path, monkeypatch):
    from databridge_graph import packs as graph_packs

    src = packs_root() / _non_declaring_pack()
    dst = tmp_path / src.name
    dst.mkdir()
    (dst / "ontology.yaml").write_text((src / "ontology.yaml").read_text(encoding="utf-8"),
                                       encoding="utf-8")
    (dst / ROUTES_FILENAME).write_text("routes: [determination, send_everything]\n",
                                       encoding="utf-8")
    monkeypatch.setattr(graph_packs, "packs_root", lambda: tmp_path)
    monkeypatch.setattr(graph_packs, "configured_packs", lambda root=None: (src.name,))
    with pytest.raises(PackRoutesError):
        pack_declared_routes()
