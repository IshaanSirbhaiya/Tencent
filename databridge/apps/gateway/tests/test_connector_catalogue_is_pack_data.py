"""HC1 (2026-10-01), SWEEP hardcode audit #8: the connector catalogue is pack data
(`packs/<pack>/connectors.yaml`), not a Python literal in the gateway."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest
import yaml

from databridge_gateway import connector_routes as cr

PACKS = cr.DATABRIDGE_ROOT / "packs"


def _domain_word():
    path = cr.DATABRIDGE_ROOT / "packages" / "graph" / "tests" / "test_no_domain_branching.py"
    spec = importlib.util.spec_from_file_location("_ndb_terms_cat", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._WORD


def test_the_catalogue_is_exactly_what_the_pack_files_list():
    files = sorted(PACKS.glob(f"*/{cr.CATALOGUE_FILENAME}"))
    assert len(files) >= 2
    expected = {}
    for path in files:
        pack = path.parent.name
        for row in yaml.safe_load(path.read_text(encoding="utf-8"))["connectors"]:
            expected[row["id"]] = None if pack == cr.NEUTRAL_CATALOGUE_PACK else pack
    assert {e["id"]: e["pack"] for e in cr.CATALOGUE} == expected
    assert any(p is not None for p in expected.values()), "no pack-owned entry: vacuous"


def test_the_gateway_module_carries_no_catalogue_rows_or_domain_words_in_code():
    tree = ast.parse(Path(cr.__file__).read_text(encoding="utf-8"))
    doc = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body \
                and isinstance(node.body[0], ast.Expr) \
                and isinstance(node.body[0].value, ast.Constant):
            doc.add(id(node.body[0].value))
    strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
               and isinstance(n.value, str) and id(n) not in doc]
    ids = {e["id"] for e in cr.CATALOGUE}
    assert ids and not (ids & set(strings)), "a catalogue id is written in the gateway code"
    word = _domain_word()
    assert [s for s in strings if word.search(s)] == []


@pytest.mark.parametrize("bad", [
    {"id": "x", "name": "x", "status": "vendored"},                       # vendored, no paths
    {"id": "x", "name": "x", "status": "not_yet_vendored"},               # no reason
    {"id": "x", "name": "x", "status": "maybe", "not_vendored_reason": "r"},
    {"id": "x", "name": "x", "status": "not_yet_vendored", "not_vendored_reason": "r",
     "pack": "healthcare"},                                               # pack is never declared
])
def test_a_malformed_entry_fails_loud(tmp_path, bad):
    (tmp_path / "somepack").mkdir()
    (tmp_path / "somepack" / cr.CATALOGUE_FILENAME).write_text(
        yaml.safe_dump({"connectors": [bad]}), encoding="utf-8")
    with pytest.raises(cr.CatalogueError):
        cr.load_catalogue(tmp_path)


def test_a_duplicate_id_across_packs_fails_loud(tmp_path):
    row = {"id": "dup", "name": "d", "status": "not_yet_vendored", "not_vendored_reason": "r"}
    for pack in ("a", "b"):
        (tmp_path / pack).mkdir()
        (tmp_path / pack / cr.CATALOGUE_FILENAME).write_text(
            yaml.safe_dump({"connectors": [row]}), encoding="utf-8")
    with pytest.raises(cr.CatalogueError):
        cr.load_catalogue(tmp_path)
