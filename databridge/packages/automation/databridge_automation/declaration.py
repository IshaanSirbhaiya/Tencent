"""The governance half: an agentdef declaration generated from a `WorkflowSpec`, then COMPILED.

A `WorkflowSpec` says what an agent does; an agentdef declaration says what it may do, under
which purpose, with which citation rule, budget and eval gates. `compile_agent` turns the latter
into grants or refuses — and an agent that does not compile cannot run (D11).

**One generator, one naming scheme, and neither lives here (W2, 2026-10-01).** Both are owned by
`databridge_agent_runtime.mapping`: `declaration_from_workflow(spec, pack, Governance)` writes the
declaration, `capability_for_tool` names each capability, and `build_catalogue` builds the
catalogue `compile_for_run` screens it against before every run. This package used to carry its
own generator and its own capability names (``larkspur_files.list_dir`` vs the runtime's
``larkspur_files.tool_list_dir``); a draft compiled with those passed here and was refused at RUN
time by `pair_problems`. This module is now a thin adapter:

* `runtime_governance` translates the deployment's `catalogue.Governance` (what an English
  sentence cannot supply) into the runtime's `Governance`;
* `declaration_from_workflow` calls the runtime generator and returns its YAML **verbatim** —
  the text `save_draft` writes is byte-for-byte what the runtime generated;
* `compile_declaration` runs the real `compile_agent` against `to_adapter_catalogue`, which is the
  runtime's `build_catalogue` over the live connections.

There is no fallback generator. If the runtime refuses to write a declaration (a `MappingError`:
an unanswered ``touches_subject``, an id that is not snake_case), the draft is `not_compiled` with
that reason — a second, divergent generator is exactly the bug this replaced.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Union

import yaml

from databridge_agent_runtime import Governance as RuntimeGovernance
from databridge_agent_runtime import capability_for_tool  # noqa: F401 - re-exported, the one namer
from databridge_agent_runtime import declaration_from_workflow as runtime_declaration
from databridge_agent_runtime import parse_declaration_text
from databridge_workflow_spec import WorkflowSpec

from .catalogue import CatalogueLike, Governance, runtime_catalogue

#: Recorded on every draft as `declaration_source`: which generator wrote the declaration.
GENERATOR = "databridge_agent_runtime"
#: The citation rule the runtime generator writes (it has no knob for it).
RUNTIME_CITATION = "required_per_field"


@dataclass
class Compilation:
    outcome: str                                   # compiled | refused | not_compiled
    refusals: list[dict] = field(default_factory=list)
    reason: str = ""
    decision: Any = None
    problems: list[str] = field(default_factory=list)
    """Catalogue problems the runtime's `build_catalogue` met (e.g. two tools folding to one
    capability). Not refusals — the compiler never saw those tools — but a reason a grant is absent."""

    @property
    def compiled(self) -> bool:
        return self.outcome == "compiled"

    def as_record(self) -> dict:
        return {"outcome": self.outcome, "refusals": list(self.refusals), "reason": self.reason,
                "problems": list(self.problems)}


def runtime_governance(spec: WorkflowSpec, governance: Governance, *,
                       touches_subject: Optional[Mapping[str, bool]] = None) -> RuntimeGovernance:
    """The deployment defaults as the runtime generator's `Governance`. Nothing is guessed:

    * ``purpose`` unset -> ``""`` (the declaration is then `not_compiled` with a plain reason by
      `compile_declaration`; `compile_agent` would refuse it anyway);
    * ``golden_set`` unset -> the agent's own ``packs/<pack>/evals/<agent>.jsonl``, which
      `compile_agent` digests and refuses when it does not exist;
    * ``touches_subject`` is the compiler's answer for every action (identity is a property of the
      whole request, R-017 #3) — passed explicitly so the runtime never has to default it.
    """
    return RuntimeGovernance(
        purpose=governance.purpose or "",
        golden_set=governance.golden_set or f"packs/{governance.pack}/evals/{spec.agent}.jsonl",
        escalations_per_day=governance.escalations_per_day,
        cost_ceiling_usd_per_day=governance.cost_ceiling_usd_per_day,
        trust_ceiling=governance.trust_ceiling,
        touches_subject=dict(touches_subject or {}),
        description=f"Compiled from English: {spec.created_from}")


def declaration_from_workflow(spec: WorkflowSpec, governance: Governance, *,
                              touches_subject: Optional[Mapping[str, bool]] = None) -> str:
    """The declaration YAML for ``spec``, written by the runtime's generator. Pure; no I/O.

    Raises `databridge_agent_runtime.MappingError` when the runtime will not write one."""
    return runtime_declaration(spec, governance.pack,
                               runtime_governance(spec, governance,
                                                  touches_subject=touches_subject))


def dump_declaration(decl: Union[dict, str]) -> str:
    if isinstance(decl, str):
        return decl
    return yaml.safe_dump(decl, sort_keys=False, allow_unicode=True)


def compile_declaration(decl: Union[dict, str], catalogue: CatalogueLike, *,
                        purposes: Any = None, repo_root: Optional[Path] = None,
                        packs_dir: Optional[Path] = None) -> Compilation:
    """Parse ``decl`` (YAML text, or a mapping) and run `databridge_agentdef.compile_agent` against
    the catalogue the runtime would build (`runtime_catalogue`).

    Never raises for a governance problem: a malformed declaration, a missing purpose or a refusal
    all come back as a `Compilation` the UI can show. ``purposes`` (a `PurposeVocabulary`) and
    ``repo_root`` are inputs for tests and for a gateway that binds its own; by default the
    compiler reads the declaring pack's `purposes.yaml` and the repo's golden sets."""
    try:
        from databridge_agentdef import compile_agent
        from databridge_agentdef.errors import DeclarationError
    except ImportError as exc:                              # pragma: no cover - env problem
        return Compilation("not_compiled", reason=f"the agent compiler is not importable: {exc}")
    text = dump_declaration(decl)
    try:
        data = decl if isinstance(decl, dict) else (yaml.safe_load(text) or {})
    except yaml.YAMLError as exc:
        return Compilation("not_compiled", reason=f"the declaration is not YAML: {exc}")
    if not isinstance(data, dict):
        return Compilation("not_compiled", reason="the declaration is not a mapping")
    if not data.get("purpose"):
        return Compilation(
            "not_compiled",
            reason=(f"no purpose is configured for pack {data.get('pack')!r}. A purpose is the "
                    f"lawful basis for every read and must be declared in "
                    f"packs/{data.get('pack')}/purposes.yaml (set WALNUT_AGENT_PURPOSE when the "
                    f"pack declares several)."))
    try:
        # the runtime's parser: same strict YAML and the same digest `load_agent` will compute
        parsed = parse_declaration_text(text, declared_in=f"var/agents/{data.get('agent')}"
                                                          f".agent.yaml")
    except (DeclarationError, ValueError, yaml.YAMLError) as exc:
        return Compilation("not_compiled", reason=str(exc))
    adapter_catalogue, problems = runtime_catalogue(catalogue)
    kwargs: dict[str, Any] = {"catalogue": adapter_catalogue}
    if purposes is not None:
        kwargs["purposes"] = purposes
    if repo_root is not None:
        kwargs["repo_root"] = repo_root
    if packs_dir is not None:
        kwargs["packs_dir"] = packs_dir
    try:
        decision = compile_agent(parsed, **kwargs)
    except Exception as exc:                                  # noqa: BLE001 - reported, not hidden
        return Compilation("not_compiled", reason=f"{type(exc).__name__}: {exc}",
                           problems=problems)
    refusals = [{"code": r.code.value, "detail": r.detail} for r in decision.refusals]
    return Compilation(decision.outcome.value, refusals=refusals, decision=decision,
                       problems=problems)


#: An injected generator (tests, a gateway binding its own): ``(spec, governance) -> YAML | dict``.
DeclarationBuilder = Callable[[WorkflowSpec, Governance], Union[str, dict]]


__all__ = ["Compilation", "capability_for_tool", "declaration_from_workflow", "dump_declaration",
           "compile_declaration", "runtime_governance", "GENERATOR", "RUNTIME_CITATION",
           "DeclarationBuilder"]
