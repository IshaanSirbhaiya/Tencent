"""The CI gate for the D3 migration: no ``SourcePointer`` may be built without a ``trust``.

**What this is, corrected.** An earlier version of this docstring said that "the runtime
validators in ``provenance.py`` are the defence" for the paths this scan cannot see. That was
wrong, and review R-003 finding 6 was right to say so: ``trust`` is ``Optional[Trust] = None``,
so a pointer built by a factory, or by ``model_validate`` on a dict that simply omits the key,
validated perfectly happily with no label at all. The scan was the only thing looking, and it
looks at syntax.

So state the division of labour honestly, because it is not the one you would guess:

* **The runtime validators enforce the trust LATTICE** — a ``generated_claim`` is untrusted, a
  pointer over untrusted ancestry cannot be trusted, ancestry may not be dropped to dodge the
  join, and (since R-003) ``model_copy``/``model_construct``/``model_validate`` cannot walk
  around any of it. That is the security control, it is runtime, and it holds on every path.
* **This scan enforces the MIGRATION** — that a call site *names* the label at all. It is a
  syntax check, it is best-effort, and it cannot be a security control: nothing stops a label
  arriving from a variable this file cannot evaluate. The security control for "unlabelled" is
  the end state D3 actually specifies: ``trust: Trust``, required, no default, with an explicit
  versioned loader that quarantines legacy records rather than defaulting them.

**Why the field is still optional, and what it costs.** Making it required today fails 121
construction sites across ``intake``, ``convert``, ``graph``, ``outbox``, ``gateway``, ``agent``,
``dispatcher``, ``mcp_server``, ``adapter`` and ``evals`` — measured, not estimated, by running
``scan()`` over the repo. That migration is a cross-package change and it belongs with the
adapter Core that can stamp labels from source contracts (Phase 3), not with this file. Until
then: ``trust_from_parents`` joins an unlabelled member as UNTRUSTED, ``effective_trust``
preserves ``None`` rather than promoting it, and ``generated_claim`` — the one kind whose label
is knowable from its kind alone — is required to be explicitly ``UNTRUSTED``.

**Why a source scan and not only a runtime check.** A runtime check fires only on the paths a
test happens to exercise; an *unexercised* construction site also has to fail. Same shape as
``test_no_domain_branching``: a scan, not a hope.

**What it can now see** (widened for R-003 finding 6):

* ``SourcePointer(...)`` and attribute forms ending in ``.SourcePointer(...)``;
* a **name rebinding** — ``SP = SourcePointer`` then ``SP(...)``, including ``import ... as``,
  resolved per module in a pre-pass;
* ``SourcePointer.model_validate(...)`` / ``.model_validate_json(...)`` /
  ``.model_construct(...)``, on the target or on an alias;
* ``functools.partial(SourcePointer, ...)``.

**What it still cannot see, stated plainly.** It reasons about syntax:

* ``SourcePointer(**kwargs)`` — the keys are not knowable statically. Reported as a violation
  with reason ``SPLAT``, because "we cannot tell" must not read as "it is fine". A
  ``model_validate`` whose argument is not a dict literal is reported the same way.
* a pointer built by a factory function in another module, or a subclass — invisible here. So is
  a label that arrives through a variable. These are why the paragraph above says this file is
  not the security control.

**Exemptions** are inline and must be justified in the comment itself: put
``# trust-gate: exempt — <reason>`` anywhere in the call's source lines. Tests that
deliberately construct an unlabelled pointer (to prove backwards compatibility still holds)
are the legitimate case. The exemption is deliberately noisy so it cannot be sprinkled.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

EXEMPT_MARKER = "trust-gate: exempt"

_TARGET = "SourcePointer"

#: Classmethods that build an instance without going through ``__init__``. ``model_construct``
#: is refused at runtime now (see ``provenance._Sealed``); it stays listed so a call site that
#: still tries fails the build rather than only the test that happens to run it.
_CONSTRUCTING_METHODS = frozenset({"model_validate", "model_validate_json", "model_construct"})


@dataclass(frozen=True)
class TrustGateViolation:
    """One construction site that does not name ``trust``."""

    path: Path
    lineno: int
    reason: str          # "OMITTED" | "SPLAT" | "UNSCANNABLE"
    snippet: str

    def __str__(self) -> str:                                   # pragma: no cover - display
        return f"{self.path}:{self.lineno} [{self.reason}] {self.snippet}"


def _iter_python_files(roots: Iterable[Path]) -> Iterator[Path]:
    skip = {".venv", "venv", "site-packages", "__pycache__", "node_modules", ".git"}
    for root in roots:
        root = Path(root)
        candidates = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in candidates:
            if path.suffix != ".py":
                continue
            if skip & set(path.parts):
                continue
            yield path


def _tail_name(node: ast.expr) -> str | None:
    """The last name in a call target: ``SourcePointer``, ``mod.SourcePointer``, ``SP``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _aliases_in(tree: ast.AST) -> set[str]:
    """Every local name that refers to ``SourcePointer`` in this module.

    ``SP = SourcePointer`` and ``from ... import SourcePointer as SP`` were both invisible to the
    old scan, which meant the gate could be walked around by typing two extra characters.
    Resolved transitively so ``A = SourcePointer; B = A`` is caught as well.
    """
    names = {_TARGET}
    for _ in range(4):                       # transitive closure; chains are never deep
        before = len(names)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == _TARGET and alias.asname:
                        names.add(alias.asname)
            elif isinstance(node, ast.Assign) and _tail_name(node.value) in names:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
        if len(names) == before:
            break
    return names


def _dict_names_trust(node: ast.expr) -> bool | None:
    """``True``/``False`` if the argument is a dict literal we can read; ``None`` if opaque."""
    if isinstance(node, ast.Dict):
        keys = [k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        if len(keys) != len(node.keys):       # a computed key: we cannot be sure
            return None
        return "trust" in keys
    if isinstance(node, ast.Call) and _tail_name(node.func) == "dict":
        return "trust" in {kw.arg for kw in node.keywords}
    return None


def _classify(node: ast.Call, names: set[str]) -> str | None:
    """The violation reason for this call, or ``None`` if it is not a construction site."""
    func = node.func

    # SourcePointer(...) / mod.SourcePointer(...) / SP(...)
    if _tail_name(func) in names and not isinstance(func, ast.Attribute):
        pass
    elif isinstance(func, ast.Attribute) and func.attr == _TARGET:
        pass
    elif (isinstance(func, ast.Attribute) and func.attr in _CONSTRUCTING_METHODS
            and _tail_name(func.value) in names):
        # SourcePointer.model_validate({...}) and friends
        if not node.args:
            return "UNSCANNABLE"
        if func.attr == "model_construct":
            return "OMITTED" if "trust" not in {kw.arg for kw in node.keywords} else None
        named = _dict_names_trust(node.args[0])
        if named is None:
            return "UNSCANNABLE"
        return None if named else "OMITTED"
    elif (_tail_name(func) == "partial"
            and node.args and _tail_name(node.args[0]) in names):
        # functools.partial(SourcePointer, ...) — the label may be bound here or at the call
        return "UNSCANNABLE" if "trust" not in {kw.arg for kw in node.keywords} else None
    else:
        return None

    keywords = {kw.arg for kw in node.keywords}
    if None in keywords:
        return "SPLAT"
    return None if "trust" in keywords else "OMITTED"


def scan(roots: Iterable[Path]) -> list[TrustGateViolation]:
    """Return every ``SourcePointer`` construction site under ``roots`` that omits ``trust``."""
    violations: list[TrustGateViolation] = []
    for path in _iter_python_files(roots):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError):                            # pragma: no cover - unreadable
            continue
        lines = source.splitlines()
        names = _aliases_in(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            reason = _classify(node, names)
            if reason is None:
                continue
            start = node.lineno
            end = getattr(node, "end_lineno", start) or start
            span = lines[start - 1:end]
            if any(EXEMPT_MARKER in line for line in span):
                continue
            violations.append(TrustGateViolation(
                path=path, lineno=start, reason=reason, snippet=span[0].strip()[:120]))
    return violations


def format_report(violations: Iterable[TrustGateViolation]) -> str:
    """A failure message a human can act on without opening the scanner."""
    listed = "\n".join(f"  {v}" for v in violations)
    return (
        "SourcePointer construction sites without a `trust` label (spine D3):\n"
        f"{listed}\n"
        "Stamp the label from the source contract — who could WRITE those bytes, not who "
        "owns the table. If the site legitimately must stay unlabelled (a backwards-"
        f"compatibility test), add an inline `# {EXEMPT_MARKER} — <reason>` comment."
    )
