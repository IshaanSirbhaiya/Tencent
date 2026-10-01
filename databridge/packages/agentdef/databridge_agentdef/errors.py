"""Failure modes of the agent plane, split by **who has to do something about it**.

Two families, and the split is the same one `growth.py` makes between a refusal and an
escalation:

* `AgentDefError` and its subclasses are raised when a declaration is *malformed* — not YAML, a
  key nobody declared, a version that is not a number. There is no governance question in a typo,
  and asking a reviewer to rule on one trains them to click through.
* A **refusal** is not an exception. It is a value: `compiler.Refusal`, carried on a
  `CompilationDecision` alongside the reasoning that produced it, because the thing a person needs
  is the comparison, not a traceback. `CompilationRefused` exists only for callers that want the
  decision as control flow (`compile_or_raise`), and it carries the whole decision.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:                                                   # pragma: no cover
    from .compiler import CompilationDecision


class AgentDefError(ValueError):
    """A declaration, catalogue or artefact is malformed in a way no decision could repair."""


class DeclarationError(AgentDefError):
    """An agent declaration file cannot be read as a declaration at all."""


class CatalogueError(AgentDefError):
    """An adapter advertisement cannot be read, so what an adapter offers is unknown.

    Deliberately not a refusal with a nice message: if the catalogue itself is broken then every
    capability check below it is vacuous, and a vacuous check that returns "compiled" is worse
    than a loud failure.
    """


class CompilationRefused(AgentDefError):
    """Raised by `compile_or_raise`. Carries the decision, never just a string.

    There is no ``force``/``override``/``allow_unsafe`` counterpart anywhere in this package, and
    `tests/test_no_ambient_authority.py` asserts that over every public signature and every
    dataclass field rather than over the three names we happened to think of. A flag that turns a
    refusal into a warning is how a governed system becomes ungoverned (AGENT_PLANE §3).
    """

    def __init__(self, decision: "CompilationDecision") -> None:
        super().__init__(decision.reasoning)
        self.decision = decision


class AmbientAuthorityRefused(AgentDefError):
    """A capability was requested at runtime that the declaration does not carry.

    The name is the point. This is not "permission denied" — the agent was never granted it, there
    is no path that could grant it now, and the only way to obtain it is a **new declaration
    version**, reviewed and recompiled (AGENT_PLANE §3: *"no ambient authority"*).
    """
