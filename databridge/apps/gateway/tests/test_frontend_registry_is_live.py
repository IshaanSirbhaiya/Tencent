"""HC1 (2026-10-01), SWEEP hardcode audit #6/#7: the frontend reads the pack registry from the
gateway (`GET /packs`) and shows "registry unavailable" when it cannot — it never silently
substitutes the bundled `config/packs.json`, and the graph vocabulary comes from the live registry.

A source-level test because the frontend has no unit runner in this repo (`npx tsc --noEmit` is the
only frontend gate the fix brief allows); each assertion names the exact construct that made the
fallback silent, so restoring it fails here.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
PACKS_TS = FRONTEND / "lib" / "packs.ts"
LAYOUT_TS = FRONTEND / "lib" / "brain-layout.ts"
BUNDLED = FRONTEND / "config" / "packs.json"

_IMPORT_BUNDLED = re.compile(r"^\s*import\s+\w+\s+from\s+[\"'][^\"']*packs\.json[\"']", re.M)


def _code(path: Path) -> str:
    """The file with comments removed, so a sentence ABOUT packs.json does not count."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def test_the_bundled_registry_is_kept_but_not_imported():
    assert BUNDLED.is_file(), "unwire, don't delete: config/packs.json must stay on disk"
    for path in (PACKS_TS, LAYOUT_TS):
        assert not _IMPORT_BUNDLED.search(path.read_text(encoding="utf-8")), path.name
    # Non-vacuous: the pattern does match the import that used to be there.
    assert _IMPORT_BUNDLED.search('import registryJson from "@/config/packs.json";')


def test_a_failed_fetch_throws_rather_than_returning_a_copy():
    code = _code(PACKS_TS)
    body = code[code.index("export async function fetchPackRegistry"):]
    body = body[:body.index("\n}\n") + 3]
    assert body.count("throw new RegistryUnavailableError") >= 3      # network, status, shape
    assert "catch {" not in body and "FALLBACK_REGISTRY" not in body
    assert "publish(body)" in body


def test_the_graph_vocabulary_is_filled_from_the_live_registry():
    code = _code(LAYOUT_TS)
    assert "FALLBACK_REGISTRY" not in code
    assert "onLiveRegistry(applyRegistry)" in code


def test_the_unavailable_placeholder_names_no_domain():
    path = Path(__file__).resolve().parents[3] / "packages" / "graph" / "tests" / \
        "test_no_domain_branching.py"
    spec = importlib.util.spec_from_file_location("_ndb_terms_fe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    code = _code(PACKS_TS)
    start = code.index("const NEUTRAL_NOUNS")
    block = code[start:code.index("export const FALLBACK_REGISTRY")]
    assert "Registry unavailable" in block
    assert mod._WORD.findall(block) == []
