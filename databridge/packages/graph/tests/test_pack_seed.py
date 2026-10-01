"""Pack-declared demo seeds (Codex f2 #6): the engine runs what the ENABLED packs declare.

Expectations are derived from the packs on disk (which ones ship `demo_seed.yaml`), never from a
written-down pack name.
"""
from __future__ import annotations

import pytest

from databridge_graph import GraphStore
from databridge_graph.pack_seed import (
    DEMO_SEED_FILE,
    declared_demo_seeds,
    demo_seed_sources,
    load_demo_seeds,
    read_demo_seed,
)
from databridge_graph.packs import PackError, available_packs, deployment, packs_root


def _packs_with_a_seed() -> list[str]:
    return [p for p in available_packs() if (packs_root() / p / DEMO_SEED_FILE).is_file()]


def _packs_without_a_seed() -> list[str]:
    return [p for p in available_packs()
            if p != "common" and not (packs_root() / p / DEMO_SEED_FILE).is_file()]


def test_at_least_one_shipped_pack_declares_a_seed():
    assert _packs_with_a_seed(), "no pack declares a demo seed — every test below is vacuous"


@pytest.mark.parametrize("pack", _packs_with_a_seed())
def test_a_declared_seed_loads_under_its_own_binding(pack):
    with deployment(pack):
        store = GraphStore(":memory:")
        out = load_demo_seeds(store)
        assert out["loaded"] is True and out["seeded_packs"] == [pack]
        assert store.counts()["nodes"] > 0
        # every declared source resolves to text
        for name, text in demo_seed_sources().items():
            assert isinstance(text, str) and text, name


@pytest.mark.parametrize("pack", _packs_without_a_seed())
def test_a_binding_that_declares_no_seed_writes_nothing_and_says_why(pack):
    with deployment(pack):
        store = GraphStore(":memory:")
        out = load_demo_seeds(store)
        assert out["loaded"] is False and DEMO_SEED_FILE in out["refusal"]
        assert store.counts()["nodes"] == 0
        assert declared_demo_seeds() == () and demo_seed_sources() == {}


def test_a_seed_file_is_shape_checked(tmp_path):
    (tmp_path / "p").mkdir()
    f = tmp_path / "p" / DEMO_SEED_FILE
    f.write_text("loader: not a locator\n", encoding="utf-8")
    with pytest.raises(PackError, match="locator"):
        read_demo_seed("p", tmp_path)
    f.write_text("loader: a.b:c\nextra: 1\n", encoding="utf-8")
    with pytest.raises(PackError, match="unknown key"):
        read_demo_seed("p", tmp_path)
    f.write_text("loader: a.b:c\nsources: {x: 'no colon'}\n", encoding="utf-8")
    with pytest.raises(PackError, match="locator"):
        read_demo_seed("p", tmp_path)


def test_two_packs_may_not_declare_the_same_source_name(tmp_path):
    for p in ("a", "b"):
        (tmp_path / p).mkdir()
        (tmp_path / p / DEMO_SEED_FILE).write_text(
            "loader: os.path:join\nsources: {same.txt: 'os:sep'}\n", encoding="utf-8")
    with pytest.raises(PackError, match="both"):
        demo_seed_sources(["a", "b"], tmp_path)


def test_the_engine_module_names_no_pack():
    """The locator lives in the pack's YAML; the engine module holds no pack's module name."""
    import inspect

    import databridge_graph.pack_seed as mod

    src = inspect.getsource(mod)
    for pack in available_packs():
        assert f"{pack}_seed" not in src and f"packs/{pack}" not in src, pack
