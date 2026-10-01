"""SEALED delegation records (AA3; R-022 f2 CROSS-TRACK 2, INT3 founder question 2).

An adapter agent's ``auto_additive`` policy is a delegated approval of additive mapping changes by
the person who ACTIVATED it. Before AA3 the delegator was read from the registry's history file,
which anyone with disk access can edit. Now activation by an AUTHENTICATED principal (`create` as
active, `activate`, `resume`) mints a sealed record in an append-only, hash-chained log:

    <adapter_agents_dir>/_delegations/<agent>.delegations.jsonl

Each line is written with mcp_ingest's `plans.append_chained` and read back with `read_chained`
(the FXJ3 approval-log chain), so an edited, removed or inserted line breaks the chain and NO
record in the log is trusted. A sealed record holds the principal (id, `authenticated_by`, type),
the policy and its digest, the declared-scope digest, the spec digest, the registry history line
it was minted beside, and the time.

A delegation is in force only while ALL of these hold (`AdapterAgentManager.delegation_status`):
  * the log's chain verifies and its LAST line is a seal (a later revocation ends it);
  * the agent's adapt policy is still the sealed one (policy digest);
  * its declared scope is still the sealed one (connections, extract pack, screen pack, and each
    connection's declared `scope`);
  * the registry history still holds the activation line the seal names, and no later line
    changed the policy or the scope (a change that was changed back is still a change).
Otherwise nothing is delegated and every change is held for a person until a person re-activates
(pause, then resume). An agent with no seal (every agent activated before AA3, or activated with
a typed name rather than a principal object) delegates nothing.

`adapt.delegation_of` re-verifies the seal against this log every time mcp_ingest accepts a
delegated approval (`mapping.approve_delegated_additive` -> `plans.require_delegated_approver`),
so the acceptance itself requires the record. Without a signing key the chain is tamper-EVIDENT,
not tamper-proof: a writer who rewrites every line from the edited one to the end is not detected
(the same limit as the approval logs, R-022)."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Optional

#: the one scope a delegation may carry (mcp_ingest `mapping.DELEGATION_SCOPE`)
DELEGATION_SCOPE = "additive_within_declared_scope"
SEALED = "delegation_sealed"
REVOKED = "delegation_revoked"
#: registry history actions that make an agent active by a person's act
ACTIVATING = ("create", "activate", "resume")


class DelegationRefused(ValueError):
    """No delegation may be sealed or used. Plain words."""


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def spec_scope(spec_dump: dict[str, Any]) -> dict[str, Any]:
    """The declared-scope part of a spec (as dumped, or as a registry history line holds it)."""
    return {"connections": sorted(str(c) for c in spec_dump.get("connections") or []),
            "extract_pack": (spec_dump.get("extract") or {}).get("pack"),
            "screen": dict(spec_dump.get("screen") or {})}


def scope_digest(spec_dump: dict[str, Any], connection_scopes: dict[str, Any]) -> str:
    """sha256 over the spec's declared scope plus each managed connection's declared `scope`."""
    return "sha256:" + _sha({"spec": spec_scope(spec_dump),
                             "connections": {str(k): connection_scopes[k]
                                             for k in sorted(connection_scopes)}})


def _chain() -> tuple[Callable[..., dict], Callable[..., list], type]:
    from databridge_mcp_ingest.plans import PlanError, append_chained, read_chained  # noqa: PLC0415

    return append_chained, read_chained, PlanError


class DelegationLog:
    """The append-only, hash-chained delegation log of every adapter agent under `root_fn()`."""

    def __init__(self, root_fn: Callable[[], Path]) -> None:
        self._root_fn = root_fn

    @property
    def root(self) -> Path:
        return Path(self._root_fn())

    def path(self, agent_id: str) -> Path:
        return self.root / f"{agent_id}.delegations.jsonl"

    def _lock(self, agent_id: str):
        from .adapt import _FileLock                                  # noqa: PLC0415

        return _FileLock(self.root / f".{agent_id}.delegations.lock")

    def records(self, agent_id: str) -> list[dict[str, Any]]:
        """Every record, after the WHOLE chain verified; `DelegationRefused` when it does not."""
        return read_log(self.path(agent_id))

    def seal(self, *, agent_id: str, who: dict[str, Any], policy: str, policy_digest: str,
             scope_digest: str, spec_digest: str, history_line: dict[str, Any],
             history_n: int, at: Optional[str] = None) -> dict[str, Any]:
        """Append a sealed record. `who` is `adapt.principal_of(...)` of the activating person."""
        pid = str(who.get("principal_id") or "").strip()
        if not pid:
            raise DelegationRefused("a delegation is sealed only for an authenticated person")
        if not str(policy_digest).startswith("sha256:") or \
                not str(scope_digest).startswith("sha256:"):
            raise DelegationRefused("a delegation is sealed only to a policy and a scope digest")
        rec = {"event": SEALED, "agent_id": agent_id, "principal_id": pid,
               "authenticated_by": who.get("authenticated_by"),
               "principal_type": who.get("principal_type"), "policy": policy,
               "policy_digest": policy_digest, "scope": DELEGATION_SCOPE,
               "scope_digest": scope_digest, "spec_digest": spec_digest,
               "history_n": int(history_n),
               "history_line": {k: history_line.get(k) for k in ("ts", "action", "by",
                                                                  "spec_digest", "status")},
               "at": at or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        append_chained, _, _ = _chain()
        with self._lock(agent_id):
            self.records(agent_id)            # a broken chain is never extended silently
            return append_chained(self.path(agent_id), rec)

    def revoke(self, agent_id: str, *, by: str, reason: str,
               at: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Append a revocation when a seal is in force (None otherwise: nothing to revoke)."""
        append_chained, _, _ = _chain()
        with self._lock(agent_id):
            try:
                recs = self.records(agent_id)
            except DelegationRefused:
                recs = []                     # broken: nothing in it is trusted anyway; still say so
            if recs and recs[-1].get("event") != SEALED:
                return None
            if not recs and not self.path(agent_id).is_file():
                return None
            return append_chained(self.path(agent_id), {
                "event": REVOKED, "agent_id": agent_id, "by": by or None, "reason": reason,
                "at": at or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})

    def in_force(self, agent_id: str) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        """(the sealed record in force, None) or (None, why there is none)."""
        try:
            recs = self.records(agent_id)
        except DelegationRefused as exc:
            return None, str(exc)
        if not recs:
            return None, ("no sealed delegation: no person activated this agent with an "
                          "authenticated principal")
        last = recs[-1]
        if last.get("event") != SEALED:
            return None, (f"the delegation was revoked at {last.get('at')} ({last.get('reason')}); "
                          f"a person must re-activate the agent (pause, then resume)")
        if last.get("agent_id") != agent_id:
            return None, "the delegation log names another agent"
        return last, None


def read_log(path: Path) -> list[dict[str, Any]]:
    """Every chained record of a delegation log, the whole chain verified. Unchained lines are never
    evidence (`read_chained` skips them, and they break the chain of any line after them)."""
    _, read_chained, PlanError = _chain()
    try:
        return read_chained(Path(path), what="delegation log")
    except PlanError as exc:
        raise DelegationRefused(str(exc)) from None


def verify_sealed(log_path: str, seal_hash: str, *, agent_id: str, delegated_by: str,
                  policy_digest: str) -> dict[str, Any]:
    """The sealed record `seal_hash`, re-read from its log NOW: it must be the LAST line of a
    verifying chain (nothing revoked it since) and match the delegation that cites it.
    `DelegationRefused` otherwise."""
    if not log_path or not seal_hash:
        raise DelegationRefused("the delegation cites no sealed delegation record; an agent "
                                "nobody activated with an authenticated principal delegates "
                                "nothing")
    recs = read_log(Path(log_path))
    if not recs:
        raise DelegationRefused("the delegation's sealed record is not in the delegation log")
    last = recs[-1]
    if last.get("hash") != seal_hash:
        raise DelegationRefused("the sealed delegation record this approval cites is no longer the "
                                "one in force (revoked or superseded)")
    if last.get("event") != SEALED or last.get("agent_id") != agent_id or \
            last.get("principal_id") != delegated_by or \
            last.get("policy_digest") != policy_digest or last.get("scope") != DELEGATION_SCOPE:
        raise DelegationRefused("the delegation does not match its sealed record (agent, "
                                "principal, policy digest or scope)")
    return last


__all__ = ["DelegationLog", "DelegationRefused", "DELEGATION_SCOPE", "SEALED", "REVOKED",
           "ACTIVATING", "scope_digest", "spec_scope", "verify_sealed", "read_log"]
