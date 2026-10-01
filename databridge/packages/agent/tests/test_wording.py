"""HC1 (2026-10-01), SWEEP hardcode audit #2/#4: the agent's prompt and staff-facing copy come from
the bound pack's `agent_wording.yaml`. The engine package names no domain; a multi-vertical or
workspace deployment gets neutral wording; the healthcare pack keeps its own."""
from __future__ import annotations

import ast
import importlib.util
import pathlib

import pytest
import yaml

from databridge_agent import frame, render, wording as wording_mod
from databridge_agent.wording import (CONSOLE_NOTE_KEYS, HEADING_KEYS, NOTE_KEYS, WORDING_FILENAME,
                                      WordingError, load_wording, wording, wording_pack)

REPO = pathlib.Path(__file__).resolve().parents[3]              # databridge/
PACKS = REPO / "packs"
AGENT_SRC = REPO / "packages" / "agent" / "databridge_agent"


def _domain_word():
    """The SAME term list `test_no_domain_branching` enforces — read from it, not restated."""
    path = REPO / "packages" / "graph" / "tests" / "test_no_domain_branching.py"
    spec = importlib.util.spec_from_file_location("_ndb_terms", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert len(mod.DOMAIN_TERMS) >= 1
    return mod._WORD


_WORD = _domain_word()


def _all_text(w) -> str:
    return "\n".join([frame.system_prompt(w), w.question_label, *w.headings.values(),
                      *w.notes.values(), *w.console_notes.values()])


@pytest.fixture
def unpinned(monkeypatch):
    from databridge_intake import pack as pack_mod

    prior = pack_mod._BOUND
    pack_mod.bind_pack(None)
    monkeypatch.delenv("DATABRIDGE_PACK", raising=False)
    yield monkeypatch
    pack_mod.bind_pack(prior)


def _enabled_in_deployment_file() -> list[str]:
    data = yaml.safe_load((PACKS / "deployment.yaml").read_text(encoding="utf-8"))
    return list(data["enabled_packs"])


def test_a_multi_vertical_deployment_speaks_neutrally(unpinned):
    enabled = _enabled_in_deployment_file()
    assert len(enabled) >= 2, "the shipped deployment binds one pack; this case needs several"
    unpinned.setenv("DATABRIDGE_PACKS", ",".join(enabled))
    w = wording()
    assert w.pack == wording_mod.NEUTRAL_PACK
    hits = _WORD.findall(_all_text(w))
    assert hits == [], f"neutral wording names a domain: {sorted(set(hits))}"


def test_the_scanner_is_not_vacuous_the_healthcare_wording_does_name_its_domain():
    w = load_wording(PACKS / "healthcare" / WORDING_FILENAME)
    assert len(_WORD.findall(_all_text(w))) >= 1


def test_a_pinned_pack_speaks_in_its_own_words(unpinned):
    unpinned.setenv("DATABRIDGE_PACK", "healthcare")
    expected = load_wording(PACKS / "healthcare" / WORDING_FILENAME)
    assert wording_pack() == "healthcare"
    assert frame.system_prompt().startswith(expected.persona)
    assert render.note_text(render.HUMAN_SELECTION) == expected.notes["human_selection"]
    assert render.HEADING_VALUES == expected.headings["values"]


def test_a_workspace_deployment_gets_the_workspace_wording(unpinned):
    unpinned.setenv("DATABRIDGE_PACKS", "workspace")
    w = wording()
    assert w.pack == "workspace"
    assert _WORD.findall(_all_text(w)) == []
    assert frame.question_message("hello")["content"].startswith(w.question_label + ":")


def test_a_single_pack_without_wording_falls_back_to_neutral(unpinned):
    shipless = [p.name for p in PACKS.iterdir()
                if (p / "ontology.yaml").is_file() and not (p / WORDING_FILENAME).is_file()]
    assert len(shipless) >= 1, "every pack ships wording; this case cannot be exercised"
    unpinned.setenv("DATABRIDGE_PACKS", shipless[0])
    assert wording().pack == wording_mod.NEUTRAL_PACK


@pytest.mark.parametrize("path", sorted(PACKS.glob(f"*/{WORDING_FILENAME}"))
                         + [wording_mod.NEUTRAL_WORDING_PATH], ids=lambda p: p.parent.name)
def test_every_shipped_wording_file_is_complete_and_closed(path):
    w = load_wording(path)
    assert w.pack == (path.parent.name if path != wording_mod.NEUTRAL_WORDING_PATH
                      else wording_mod.NEUTRAL_PACK)
    assert set(w.notes) == set(NOTE_KEYS) and set(w.headings) == set(HEADING_KEYS)
    assert set(w.console_notes) == set(CONSOLE_NOTE_KEYS)


def test_shipped_wording_files_are_non_vacuous():
    assert len(list(PACKS.glob(f"*/{WORDING_FILENAME}"))) >= 2
    assert wording_mod.NEUTRAL_WORDING_PATH.is_file()


def test_a_packs_root_with_no_wording_at_all_still_speaks_neutrally(unpinned, tmp_path):
    """A custom packs root (tests, a new deployment) need not ship the neutral copy."""
    (tmp_path / "solo").mkdir()
    unpinned.setenv("DATABRIDGE_PACKS", "solo")
    w = wording(tmp_path)
    assert w.pack == wording_mod.NEUTRAL_PACK
    assert _WORD.findall(_all_text(w)) == []


@pytest.mark.parametrize("mutate", ["drop_note", "extra_note", "empty_note", "extra_top_level"])
def test_a_pack_cannot_drop_add_or_blank_a_note(tmp_path, mutate):
    data = yaml.safe_load(wording_mod.NEUTRAL_WORDING_PATH.read_text(encoding="utf-8"))
    key = NOTE_KEYS[0]
    if mutate == "drop_note":
        data["notes"].pop(key)
    elif mutate == "extra_note":
        data["notes"]["you_may_now_confirm"] = "The top candidate is the person."
    elif mutate == "empty_note":
        data["notes"][key] = "   "
    else:
        data["frame_open"] = "<<<"
    path = tmp_path / WORDING_FILENAME
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(WordingError):
        load_wording(path)


def _code_strings(path: pathlib.Path) -> list[str]:
    """Every string constant in a module that is NOT a docstring — i.e. text that can reach a
    model or a person. Comments and docstrings are commentary (Low in the audit)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]


@pytest.mark.parametrize("module", ["frame.py", "render.py", "wording.py"])
def test_the_engine_modules_carry_no_domain_wording_in_code(module):
    strings = _code_strings(AGENT_SRC / module)
    assert len(strings) >= 1
    hits = sorted({h.lower() for s in strings for h in _WORD.findall(s)})
    assert hits == [], f"{module} has domain words in code strings: {hits}"
