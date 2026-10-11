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


#: The shape every served ``delegation`` takes: the vocabulary above, plus ``recorded: false``
#: when the record said the decision was delegated but kept none of it.
DELEGATION_SCHEMA_KEYS = DELEGATION_KEYS + ("recorded",)


def delegation_view(raw: Any) -> dict:
    """The served delegation: exactly the `DELEGATION_KEYS` the record holds as scalars (a bool is
    a bool, a number a number); anything else is not served. Nothing recorded:
    ``{"recorded": False}``."""
    src = raw if isinstance(raw, dict) else {}
    out = {k: src[k] for k in DELEGATION_KEYS
           if isinstance(src.get(k), (str, int, bool)) and not isinstance(src.get(k), float)}
    return out or {"recorded": False}


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


#: The ONE principal type every person is served as (E2E run 5 W-09). Producers record the class
#: of the principal they were handed (``AuthenticatedPrincipal``, ``Approver``,
#: ``DelegatedApproval``) or the word ``person``; for one signed-in person those differed between
#: families, so the same decision-maker read as two kinds of actor. The class is an implementation
#: detail of the producer, not a fact about the actor: a recorded type that is not a machine type
#: is served as ``person`` (a delegated decision keeps its ``delegation``).
PERSON = "person"


def _snake(name: str) -> str:
    """``AdapterAgent`` -> ``adapter_agent``; ``adapter-agent`` -> ``adapter_agent``."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).replace("-", "_").lower()


def canonical_type(principal_type: Any, principal_id: Any = None) -> Optional[str]:
    """The served principal type: a machine type (from the recorded type, or the machine prefix /
    core rule on the id — a machine id is never served as a person), else ``person``; ``None``
    when nothing was recorded and the id names no machine."""
    recorded = _text(principal_type)
    if recorded is not None and _snake(recorded) in MACHINE_TYPES:
        return _snake(recorded)
    machine = machine_type(principal_id)
    if machine:
        return machine
    return PERSON if recorded is not None else None


#: R-029 TIDY2: the gateway's mechanism has ONE definition now (`platform_runtime` uses the auth
#: layer's constants). Records written BEFORE that named the signed-in path ``gateway:auth`` in the
#: agent runtime's events; this is the name those records carry (history, not a second
#: definition), read as the auth layer's canonical name.
LEGACY_MECHANISMS: dict[str, str] = {"gateway:auth": "GATEWAY_MECHANISM"}

_MECHANISM_ALIASES: Optional[dict[str, str]] = None


def mechanism_aliases() -> dict[str, str]:
    """``{recorded mechanism name: canonical name}`` (E2E run 5 W-09). The gateway authenticates
    one way and names that mechanism ``auth.GATEWAY_MECHANISM`` (the dev token
    ``auth.DEV_MECHANISM``); since R-029 TIDY2 the agent runtime is registered under the SAME
    constants (`platform_runtime`), so only records written before carry another name
    (`LEGACY_MECHANISMS`, mapped onto the auth layer's constant by attribute name). Empty when the
    auth module cannot be imported (then nothing is renamed)."""
    global _MECHANISM_ALIASES
    if _MECHANISM_ALIASES is None:
        try:
            from . import auth, platform_runtime                          # noqa: PLC0415
            pairs = {platform_runtime.GATEWAY_MECHANISM: auth.GATEWAY_MECHANISM,
                     platform_runtime.GATEWAY_DEV_MECHANISM: auth.DEV_MECHANISM}
            pairs.update({old: getattr(auth, attr) for old, attr in LEGACY_MECHANISMS.items()})
            _MECHANISM_ALIASES = {k: v for k, v in pairs.items()
                                  if isinstance(k, str) and isinstance(v, str) and k != v}
        except Exception:                                                 # noqa: BLE001
            return {}
    return _MECHANISM_ALIASES


def canonical_mechanism(authenticated_by: Any) -> Optional[str]:
    mech = _text(authenticated_by)
    return mechanism_aliases().get(mech, mech) if mech is not None else None


def make_actor(principal_id: Any, principal_type: Any, *, authenticated_by: Any = None,
               dev_only: Any = None, delegation: Optional[dict] = None,
               why: str = NOT_RECORDED) -> dict:
    """The actor object — ONE schema for every family (E2E run 5 W-09). A field the producer did
    not record is the absent envelope. ``principal_type`` is ``person`` or a machine type
    (`canonical_type`); ``authenticated_by`` is the mechanism's canonical name
    (`canonical_mechanism`); for a machine actor, authentication and the dev token do not apply,
    so both are the ``machine_actor`` envelope whatever its producer wrote beside it."""
    ptype = canonical_type(principal_type, principal_id)
    machine = ptype in MACHINE_TYPES
    out: dict[str, Any] = {
        "principal_id": _text(principal_id) or absent(why),
        "principal_type": ptype or absent(why),
        "authenticated_by": (absent(MACHINE_ACTOR) if machine else
                             canonical_mechanism(authenticated_by) or absent(why)),
        "dev_only": (absent(MACHINE_ACTOR) if machine else
                     dev_only if isinstance(dev_only, bool) else absent(why)),
    }
    if delegation:
        out["delegation"] = delegation_view(delegation)
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
        # R-029 Codex f1 #6: ONLY the delegation vocabulary, each a scalar — never whatever else a
        # producer put beside it (a subject id, a note)
        delegation = delegation_view(raw_delegation)
    elif (auth or "").startswith("delegated:") or _text(rec.get("delegated_by")):
        delegation = delegation_view(rec)
    return make_actor(pid, ptype, authenticated_by=auth, dev_only=dev, delegation=delegation)


def actor_pid(actor: Any) -> Optional[str]:
    """The principal id of an actor object, when it names one."""
    return _text(actor.get("principal_id")) if isinstance(actor, dict) else None


__all__ = ["WITHHELD", "ABSENT", "NOT_RECORDED", "MACHINE_ACTOR", "NOT_IN_ALLOW_LIST", "PERSON",
           "canonical_type", "canonical_mechanism", "mechanism_aliases", "delegation_view",
           "DELEGATION_KEYS", "DELEGATION_SCHEMA_KEYS",
           "SENSITIVE_FIELD", "FREE_TEXT", "ERROR_TEXT", "TOO_DEEP", "UNEXPECTED_SHAPE",
           "SQL_WITH_LITERALS", "MACHINE_TYPES", "LEGACY_DEV_SUFFIX", "withheld", "absent",
           "is_envelope", "make_actor", "machine_type", "actor_from_record", "actor_pid"]
