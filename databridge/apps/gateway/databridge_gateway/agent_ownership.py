"""Who may DECIDE an agent's proposed action: its owner, or an operator (E2E run 2, N-18).

Before this, any authenticated principal could approve, reject or reverse any agent's proposal —
E2E run 2 had a second principal reject the first principal's automation-run proposal. The decision
was recorded correctly under the second principal's name, which is what made it worth fixing: the
audit trail was honest about a decision that principal should never have been able to make.

The rule:

* An agent's **owner** is the principal who saved it through ``POST /agents``. It is recorded at
  save time, beside the agent's own files (``<agents_dir>/<id>.owner.json``), from the
  authenticated principal — never from a request body. A later save by someone else does not
  change it.
* An **operator** is named by operator configuration, ``WALNUT_OPERATORS`` (a JSON list or a
  comma-separated list of principal ids), read per call. Unset: only the local-demo dev principal
  (``principal.dev_mode``, which `auth` grants solely under ``WALNUT_DEV_AUTH=1``) is an operator.
  Fail closed — the same rule ``WALNUT_ONTOLOGY_OWNERS`` follows.
* An agent saved before owners were recorded has **no owner**: its decisions are operator-only.
  Nobody becomes its owner by deciding, and nobody but an operator may re-save it.

Approve, reject and reverse are all decisions on the action and all follow the rule. Who may SEE
runs and approvals is a separate question (``activity_routes``), left to its founder decision.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("databridge.gateway.agent_ownership")

OPERATORS_ENV = "WALNUT_OPERATORS"
OWNER_SUFFIX = ".owner.json"


def operators() -> Optional[frozenset[str]]:
    """The configured operator principal ids, or ``None`` when unconfigured. Read per call."""
    raw = os.environ.get(OPERATORS_ENV, "").strip()
    if not raw:
        return None
    try:
        parsed: Any = json.loads(raw)
    except ValueError:
        parsed = raw.split(",")
    if isinstance(parsed, str):
        parsed = [parsed]
    if not isinstance(parsed, list):
        log.error("%s must be a JSON list or a comma-separated list; treating as NO operators",
                  OPERATORS_ENV)
        return frozenset()
    return frozenset(str(p).strip() for p in parsed if str(p).strip())


def is_operator(principal: Any) -> bool:
    ops = operators()
    if ops is None:
        return bool(getattr(principal, "dev_mode", False))
    return principal.principal_id in ops


def _agents_dir() -> Path:
    from databridge_agent_runtime.paths import agents_dir             # noqa: PLC0415
    return agents_dir()


def owner_path(agent_id: str) -> Path:
    return _agents_dir() / f"{agent_id}{OWNER_SUFFIX}"


def recorded_owner(agent_id: str) -> Optional[str]:
    """The owner recorded for ``agent_id``, or ``None`` (never recorded, or unreadable)."""
    p = owner_path(agent_id)
    if not p.is_file():
        return None
    try:
        owner = json.loads(p.read_text(encoding="utf-8")).get("owner")
    except (OSError, ValueError, AttributeError):
        log.error("owner record %s is unreadable; the agent is treated as ownerless", p)
        return None
    return owner if isinstance(owner, str) and owner else None


def record_owner(agent_id: str, principal_id: str) -> str:
    """Record ``principal_id`` as the owner unless an owner is already recorded; returns the
    owner in effect. Written atomically; an existing owner is never replaced."""
    existing = recorded_owner(agent_id)
    if existing is not None:
        return existing
    p = owner_path(agent_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({
        "agent": agent_id, "owner": principal_id, "recorded_by": "POST /agents",
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")},
        indent=2), encoding="utf-8")
    os.replace(tmp, p)
    return principal_id


def agent_exists(agent_id: str) -> bool:
    return (_agents_dir() / f"{agent_id}.workflow.yaml").is_file()


def agent_of_run(run_id: str) -> Optional[str]:
    """The agent a run belongs to (``<runs>/agent/<agent>/<run_id>.json``), or ``None`` when the
    run is not an agent run. Raises ``FileNotFoundError`` for an unknown or malformed run id."""
    from databridge_agent_runtime.runs import RUN_KIND_AGENT, find_run  # noqa: PLC0415
    path = find_run(run_id)
    return path.parent.name if path.parent.parent.name == RUN_KIND_AGENT else None


def decision_refusal(principal: Any, run_id: str) -> Optional[tuple[str, str]]:
    """``None`` when ``principal`` may decide actions of ``run_id``'s agent, else
    ``(code, message)``. ``FileNotFoundError`` propagates for an unknown run."""
    agent = agent_of_run(run_id)
    if is_operator(principal):
        return None
    if agent is None:
        return ("NOT_AN_AGENT_RUN", f"run {run_id!r} is not an agent run; only an operator may "
                                    f"decide its actions")
    owner = recorded_owner(agent)
    if owner is None:
        return ("OPERATOR_REQUIRED",
                f"agent {agent!r} has no recorded owner (it was saved before owners were "
                f"recorded), so only an operator may decide its proposed actions. Operators are "
                f"configured in {OPERATORS_ENV}, never by a request.")
    if owner != principal.principal_id:
        return ("NOT_THE_AGENTS_OWNER",
                f"agent {agent!r} belongs to another principal; only its owner or an operator "
                f"may decide its proposed actions.")
    return None


def save_refusal(principal: Any, agent_id: str) -> Optional[tuple[str, str]]:
    """``None`` when ``principal`` may (re)save ``agent_id``, else ``(code, message)``. A new
    agent may be saved by anyone signed in; an existing one only by its owner or an operator."""
    if is_operator(principal):
        return None
    owner = recorded_owner(agent_id)
    if owner is not None and owner != principal.principal_id:
        return ("NOT_THE_AGENTS_OWNER", f"agent {agent_id!r} belongs to another principal; "
                                        f"only its owner or an operator may replace it.")
    if owner is None and agent_exists(agent_id):
        return ("OPERATOR_REQUIRED", f"agent {agent_id!r} exists and has no recorded owner; only "
                                     f"an operator may replace it.")
    return None


__all__ = ["OPERATORS_ENV", "operators", "is_operator", "owner_path", "recorded_owner",
           "record_owner", "agent_of_run", "decision_refusal", "save_refusal"]
