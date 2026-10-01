"""`NEVER_FUZZY_MERGE` must not exist as a name that can be snapshotted (review R-001/A5).

The set of kinds name-similarity may never merge is **computed from the enabled packs' subject
declarations** (spine D4b), and the deployment binding is rebindable at runtime. A module-level
constant holding that set is therefore wrong in a way that is invisible:

    from databridge_graph.merge import NEVER_FUZZY_MERGE   # evaluated ONCE, at import

Python binds the value into the importing module and never looks again. A module `__getattr__`
does not save this — it makes *qualified* access dynamic and does nothing for a from-import.
Import under one pack, bind another, and the consumer goes on protecting the first pack's subject
while believing it is protected. That is a direct route to the one merge this system forbids, and
it produces no error anywhere.

So the tests below are not about one name. They are about the **shape**: any module-level
assignment in this package whose value is computed from the vocabulary is stale by construction,
and the AST test finds it wherever it reappears.
"""
from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

import databridge_graph

#: Functions whose answer depends on the deployment binding. A module-level assignment calling any
#: of them freezes an answer that was designed to be re-asked. Derived from the modules themselves
#: rather than typed out, so a new one is covered the day it is written.
_BINDING_DEPENDENT = frozenset({
    "active_vocabulary", "never_fuzzy_merge_kinds", "node_kinds_binding_to",
    "edge_kinds_binding_to", "claimed_identity_edges", "configured_packs", "build_vocabulary",
})

_PACKAGE_DIR = Path(databridge_graph.__file__).parent


def _module_names() -> list[str]:
    return [f"databridge_graph.{m.name}"
            for m in pkgutil.iter_modules([str(_PACKAGE_DIR)])]


def _source_files() -> list[Path]:
    return sorted(_PACKAGE_DIR.glob("*.py"))


def test_the_name_is_gone_from_every_module_in_the_package():
    for name in _module_names() + ["databridge_graph"]:
        mod = importlib.import_module(name)
        assert not hasattr(mod, "NEVER_FUZZY_MERGE"), (
            f"{name} exposes NEVER_FUZZY_MERGE. A from-import of it snapshots the frozenset at "
            f"import time and the consumer then protects whichever pack happened to be bound "
            f"then, forever (review R-001/A5). Use `never_fuzzy_merge_kinds()`, or better, take "
            f"the active `Vocabulary` as an argument")


def test_a_from_import_of_the_name_fails_loudly():
    with pytest.raises(ImportError):
        from databridge_graph.merge import NEVER_FUZZY_MERGE     # noqa: F401


def test_no_module_level_constant_freezes_a_binding_dependent_answer():
    """The general form. A constant is not the only way to snapshot one — this catches
    `SUBJECTS = active_vocabulary().subject_kinds` and every variant of it."""
    offenders = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:                       # MODULE level only; a call inside a
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):   # function is re-evaluated
                continue
            value = node.value
            if value is None:
                continue
            for call in (n for n in ast.walk(value) if isinstance(n, ast.Call)):
                fn = call.func
                fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if fname in _BINDING_DEPENDENT:
                    offenders.append(f"{path.name}:{node.lineno}: calls {fname}()")
    assert not offenders, (
        "module-level assignment freezes an answer that depends on the deployment binding:\n"
        + "\n".join(offenders))


def test_the_replacement_follows_the_binding():
    from databridge_graph.merge import never_fuzzy_merge_kinds
    from databridge_graph.packs import active_vocabulary, deployment

    with deployment("healthcare"):
        healthcare = never_fuzzy_merge_kinds()
    with deployment("finance"):
        finance = never_fuzzy_merge_kinds()
    assert healthcare and finance and healthcare != finance, (
        "the two packs must protect different subjects, or this test proves nothing")

    # And it accepts an explicit vocabulary, which is the form that cannot straddle two bindings
    # inside one request.
    with deployment("finance"):
        vocab = active_vocabulary()
    with deployment("healthcare"):
        assert never_fuzzy_merge_kinds(vocab) == finance
