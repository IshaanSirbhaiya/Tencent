"""D-003: a node kind declares no `id_pattern`.

`wiki/20-decisions/D-003-node-kind-id-pattern.md` decides that a pack may NOT declare an id shape
(a regex) on a node kind. A business key reaches a node only as a value the source itself holds,
under a mapping a person approved (`canonical_id`), never because a string looked like a key.

These tests pin that decision at the boundary that enforces it: the pack loader's closed set of
node-kind keys. If someone adds `id_pattern` to that set without superseding D-003, they fail.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from databridge_graph import packs
from databridge_graph.packs import PackError, clear_caches, load_pack


@pytest.fixture(autouse=True)
def _isolated_caches():
    clear_caches()
    yield
    clear_caches()


def _pack_with_node_entry_extra(root: Path, name: str, extra: str) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    body = textwrap.dedent(f"""
        pack: {name}
        version: "1.0"
        node_kinds:
          - name: invoice
            binds_to: entity
            description: An invoice.
            {extra}
        edge_kinds:
          - name: references
            binds_to: references
            description: a cites b.
    """)
    (d / "ontology.yaml").write_text(body, encoding="utf-8")


def test_id_pattern_is_not_a_node_kind_key():
    # Derived from the loader's own closed set, not restated.
    assert packs._NODE_KIND_KEYS, "the closed node-kind key set is empty — the guard is vacuous"
    assert "id_pattern" not in packs._NODE_KIND_KEYS


def test_a_pack_declaring_an_id_pattern_is_refused(tmp_path):
    root = tmp_path / "packs"
    _pack_with_node_entry_extra(root, "shaped", 'id_pattern: "^INV-[0-9]{4}$"')
    with pytest.raises(PackError, match="id_pattern"):
        load_pack("shaped", root)


def test_the_same_pack_without_it_loads(tmp_path):
    """Non-vacuous control: the refusal above is about `id_pattern`, not a malformed fixture."""
    root = tmp_path / "packs"
    _pack_with_node_entry_extra(root, "plain", "")
    pack = load_pack("plain", root)
    assert pack is not None
