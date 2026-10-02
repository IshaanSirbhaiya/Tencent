"""The common actor object and the withheld / absent value envelope, as Activity serves them.

R-028 consistency standards 1 and 5 (CONS brief, 2026-10-02):

* **Actor object** — every Activity row names who acted as
  ``{principal_id, authenticated_by, principal_type, dev_only, delegation?}``. ``principal_id`` is
  never decorated: a decision made with the public development token is ``dev_only: true``, not
  ``"<id> (dev token)"``. A legacy record that decorated the id (the outbox before R-028) is read
  back into the structured form here, so the feed never shows the decoration as an identity.
* **Withheld / absent** — a value the feed does not serve, or that its producer did not record, is
  ``{"status": "withheld" | "absent", "reason": <code>}`` (the public form of the core
  `Withheld` / `Absent` cell states: a reason code and, where one exists, ``pointers``). Never a
  bare ``null`` standing for "hidden", never a dropped key, never a ``"redacted"`` string.

Pure functions, no I/O.
"""
from __future__ import annotations

import re
from typing import Any, Optional

try:                                       # THE platform rule for machine principals (R-028)
    from databridge_core.decision_ledger import is_machine_principal
except Exception:                          # noqa: BLE001 - core older than R-028: prefixes below
    is_machine_principal = None            # type: ignore[assignment]

#: The states of the envelope (the public names of `databridge_core.provenance.Withheld/Absent`).
WITHHELD, ABSENT = "withheld", "absent"
#: Closed reason codes this surface uses.
NOT_RECORDED = "not_recorded_by_producer"
MACHINE_ACTOR = "machine_actor"
NOT_IN_ALLOW_LIST = "not_in_allow_list"
SENSITIVE_FIELD = "sensitive_field"
FREE_TEXT = "free_text"
ERROR_TEXT = "error_text"
TOO_DEEP = "too_deep"
UNEXPECTED_SHAPE = "unexpected_shape"
SQL_WITH_LITERALS = "sql_with_literals"

#: Principal types that are machines, never people. Every other recorded type (the class name an
#: authenticator issued, or ``person``) is treated as a person on the page.
MACHINE_TYPES = frozenset({"agent", "adapter_agent", "automation"})
#: Principal-id prefixes producers use for machine actors (``adapter_agent:<id>``, …).
_MACHINE_PREFIXES = (("adapter_agent:", "adapter_agent"), ("agent:", "agent"),
                     ("automation:", "automation"), ("monitor:", "automation"))
#: The decoration the outbox wrote before R-028 (``outbox_routes.actor_for``). Read, never written.
LEGACY_DEV_SUFFIX = " (dev token)"
#: Every decoration `decision_ledger.Actor` refuses ("(dev token)", "(dev only)", …).
_DECORATED = re.compile(r"\s*\(\s*dev[\s_-]*(token|only|mode|login)\s*\)\s*$", re.IGNORECASE)
#: What a delegated decision records about its delegation (``plans.require_delegated_approver``).
DELEGATION_KEYS = ("delegated_by", "policy_digest", "agent_id", "adaptation_id", "scope",
                   "on_behalf_of")


def withheld(reason: str, pointers: Optional[list] = None) -> dict:
    """A value that exists and is not served here."""
    out: dict[str, Any] = {"status": WITHHELD, "reason": reason}
    if pointers:
        out["pointers"] = pointers
    return out


def absent(reason: str = NOT_RECORDED) -> dict:
    """A value the producer did not record (or that does not apply)."""
    return {"status": ABSENT, "reason": reason}


def is_envelope(value: Any) -> bool:
    return (isinstance(value, dict) and value.get("status") in (WITHHELD, ABSENT)
            and isinstance(value.get("reason"), str) and set(value) <= {"status", "reason",
                                                                         "pointers"})


def _text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def make_actor(principal_id: Any, principal_type: Any, *, authenticated_by: Any = None,
               dev_only: Any = None, delegation: Optional[dict] = None,
               why: str = NOT_RECORDED) -> dict:
    """The actor object. A field the producer did not record is the absent envelope; for a
    machine actor, authentication and the dev token do not apply (``machine_actor``)."""
    ptype = _text(principal_type)
    machine = ptype in MACHINE_TYPES
    out: dict[str, Any] = {
        "principal_id": _text(principal_id) or absent(why),
        "principal_type": ptype or absent(why),
        "authenticated_by": _text(authenticated_by) or absent(MACHINE_ACTOR if machine else why),
        "dev_only": dev_only if isinstance(dev_only, bool) else absent(
            MACHINE_ACTOR if machine else why),
    }
    if delegation:
        out["delegation"] = delegation
    return out


def machine_type(principal_id: Any) -> Optional[str]:
    """The machine type a prefixed principal id names (``adapter_agent:x`` -> adapter_agent)."""
    pid = _text(principal_id) or ""
    for prefix, kind in _MACHINE_PREFIXES:
        if pid.lower().startswith(prefix):
            return kind
    if is_machine_principal is not None and pid and is_machine_principal(pid):
        return "agent"                     # a workflow / system / rules principal
    return None


def actor_from_record(rec: Any, *, pid_key: str = "principal_id",
                      default_type: Optional[str] = None) -> Optional[dict]:
    """The actor object of a decision record, or None when it names nobody.

    Reads, in order: a structured ``actor`` the record carries (the R-028 shape), else the flat
    fields the producers write today (``principal_id``/``by``, ``authenticated_by`` or
    ``decided_authenticated_by``, ``principal_type``, ``dev_only`` or ``decided_dev_only``, the
    delegation fields). A legacy decorated id (``"x (dev token)"``) becomes ``x`` with
    ``dev_only: true``. Nothing is inferred beyond that: a field not recorded is absent."""
    if not isinstance(rec, dict):
        return None
    nested = rec.get("actor")
    if isinstance(nested, dict) and _text(nested.get("principal_id")):
        rec, pid_key = nested, "principal_id"
    pid = _text(rec.get(pid_key))
    if pid is None:
        return None
    auth = _text(rec.get("authenticated_by")) or _text(rec.get("decided_authenticated_by"))
    dev = rec.get("dev_only") if isinstance(rec.get("dev_only"), bool) else \
        rec.get("decided_dev_only") if isinstance(rec.get("decided_dev_only"), bool) else None
    if _DECORATED.search(pid):
        pid, dev = _DECORATED.sub("", pid).strip(), True
    ptype = _text(rec.get("principal_type")) or machine_type(pid) or default_type
    delegation = None
    raw_delegation = rec.get("delegation")
    if isinstance(raw_delegation, dict) and raw_delegation:
        delegation = {k: v for k, v in raw_delegation.items()
                      if isinstance(k, str) and (v is None or isinstance(v, (str, int, bool)))}
    elif (auth or "").startswith("delegated:") or _text(rec.get("delegated_by")):
        delegation = {k: rec[k] for k in DELEGATION_KEYS
                      if isinstance(rec.get(k), (str, int, bool))} or {"recorded": False}
    return make_actor(pid, ptype, authenticated_by=auth, dev_only=dev, delegation=delegation)


def actor_pid(actor: Any) -> Optional[str]:
    """The principal id of an actor object, when it names one."""
    return _text(actor.get("principal_id")) if isinstance(actor, dict) else None


__all__ = ["WITHHELD", "ABSENT", "NOT_RECORDED", "MACHINE_ACTOR", "NOT_IN_ALLOW_LIST",
           "SENSITIVE_FIELD", "FREE_TEXT", "ERROR_TEXT", "TOO_DEEP", "UNEXPECTED_SHAPE",
           "SQL_WITH_LITERALS", "MACHINE_TYPES", "LEGACY_DEV_SUFFIX", "withheld", "absent",
           "is_envelope", "make_actor", "machine_type", "actor_from_record", "actor_pid"]
