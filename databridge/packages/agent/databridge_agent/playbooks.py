"""Loader for ``config/agent_playbooks.yaml`` — the agent's operational table.

The same argument as ``coverage_rules.yaml``: which tools a recurring question needs, and which
role owns the follow-up, is a **clinic policy**, and a clinic changing its escalation path should
not need a Python diff (CLAUDE.md §Hard constraints 3). Adding an insurer stays a YAML change —
in fact it stays *no* change here, because an unlisted code falls through to a default.

Two things this loader refuses, because a config file is edited more casually than code:

* **A playbook naming a tool the agent may not have.** Every ``tools`` entry goes through
  ``tools.screen_declaration``, so a row naming ``send_email`` fails at load rather than at the
  moment the agent tries to call it. Config must not be a back door around a capability the code
  refuses to hold.
* **A playbook that does not require a human.** ``requires_human_approval`` is ``Literal[True]``.
  There is no value that turns the confirmation gate off, so the YAML cannot express an agent
  acting alone (CLAUDE.md §Hard constraints 4) — the same device as
  ``ResolutionResult.requires_human_selection``.
"""
from __future__ import annotations

import functools
import pathlib
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from .paths import playbooks_path
from .tools import screen_declaration


class PlaybookError(RuntimeError):
    """The playbook config is missing or malformed. Fail loud; never run on a partial table."""


class Playbook(BaseModel):
    """One recurring situation: what to read, how to answer it, who owns what happens next."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str
    when: str
    tools: tuple[str, ...] = ()
    escalate_to: str
    guidance: str = ""
    requires_human_approval: Literal[True] = True

    @model_validator(mode="after")
    def _tools_are_read_only(self) -> "Playbook":
        for name in self.tools:
            screen_declaration(name)          # raises SendCapabilityRefused / IdentityToolRefused
        return self


class IssuerRouting(BaseModel):
    """Who owns follow-up for one payer. A ROLE, never an address — see the YAML header."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    escalate_to: str


class IssuerTable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    default_escalate_to: str
    overrides: tuple[IssuerRouting, ...] = ()

    def escalate_to(self, issuer_code: Optional[str]) -> str:
        """The role that owns follow-up for ``issuer_code``.

        A lookup, not a branch: every code resolves through the same two lines whether or not it
        has a row, which is what makes "adding an insurer is a config change" true rather than
        aspirational (cf. ``rules.py``).
        """
        for row in self.overrides:
            if issuer_code and row.code == issuer_code:
                return row.escalate_to
        return self.default_escalate_to


class Defaults(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    escalate_to: str
    requires_human_approval: Literal[True] = True


class Playbooks(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    defaults: Defaults
    playbooks: tuple[Playbook, ...] = ()
    issuers: IssuerTable

    @model_validator(mode="after")
    def _ids_are_unique(self) -> "Playbooks":
        ids = [p.id for p in self.playbooks]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(
                f"playbook ids must be unique; duplicated: {dupes}. A shadowed playbook is one "
                f"whose escalation nobody can find by reading the file.")
        return self

    def by_id(self, playbook_id: str) -> Playbook:
        for p in self.playbooks:
            if p.id == playbook_id:
                return p
        raise PlaybookError(
            f"no playbook {playbook_id!r}; known: {[p.id for p in self.playbooks]}")

    def escalation_for(self, issuer_code: Optional[str]) -> str:
        return self.issuers.escalate_to(issuer_code)

    def tool_names(self) -> tuple[str, ...]:
        """Every tool any playbook needs — what a composition root has to supply."""
        seen: list[str] = []
        for p in self.playbooks:
            for name in p.tools:
                if name not in seen:
                    seen.append(name)
        return tuple(seen)


def load_playbooks(path: Optional[pathlib.Path] = None) -> Playbooks:
    config_path = pathlib.Path(path) if path is not None else playbooks_path()
    if not config_path.exists():
        raise PlaybookError(f"agent playbooks not found at {config_path}")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise PlaybookError(f"{config_path} did not parse to a mapping")
    return Playbooks.model_validate(data)


@functools.lru_cache(maxsize=1)
def playbooks() -> Playbooks:
    """The shipped table, cached. Pass a path to ``load_playbooks`` for a test fixture."""
    return load_playbooks()
