"""HC1 (2026-10-01), SWEEP hardcode audit #5: the `/intelligence` console's sentences are the bound
pack's (`agent_wording.yaml` → `console_notes`), and `intelligence_routes.py` names no domain in any
string it can serve."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from databridge_gateway import intelligence_routes as ir

REPO = Path(__file__).resolve().parents[3]


def _word():
    path = REPO / "packages" / "graph" / "tests" / "test_no_domain_branching.py"
    spec = importlib.util.spec_from_file_location("_ndb_terms_console", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._WORD


def test_console_notes_are_read_from_the_bound_wording():
    from databridge_agent.wording import CONSOLE_NOTE_KEYS, wording

    notes = ir.console_notes()
    assert set(notes) == set(CONSOLE_NOTE_KEYS) and len(notes) >= 1
    assert dict(notes) == dict(wording().console_notes)
    assert all(ir.console_note(k).text == notes[k] for k in CONSOLE_NOTE_KEYS)


def test_intelligence_routes_serves_no_domain_word_from_code():
    tree = ast.parse(Path(ir.__file__).read_text(encoding="utf-8"))
    doc = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body \
                and isinstance(node.body[0], ast.Expr) \
                and isinstance(node.body[0].value, ast.Constant):
            doc.add(id(node.body[0].value))
    strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
               and isinstance(n.value, str) and id(n) not in doc]
    assert len(strings) >= 1
    word = _word()
    assert [s for s in strings if word.search(s)] == []
