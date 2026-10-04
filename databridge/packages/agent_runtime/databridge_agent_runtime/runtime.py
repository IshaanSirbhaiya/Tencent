"""`run_agent` — one governed run of a user-defined agent — and the approval API.

    load both halves → they must agree → compile_agent (no override) → GrantedAuthority
    → planner loop via the harness (task_class "agent"), every tool call admitted by the grants,
      bounded by max_steps / max_tool_calls / tokens_per_run (the smaller of the workflow's limit
      and the compiled BudgetAdmission)
    → answer contract (P15) → actions become ActionProposals gated by automation mode
    → run record (§5.7) + action history + trace steps

What is enforced where, so a reviewer can find it:

* **An agent that does not compile does not run.** `run_agent` records status ``refused`` with the
  compiler's reasons and never calls the model.
* **Read-only means no write path.** A read-only workflow declares no actions (spec + compiler);
  the tool layer refuses every ``source.write`` call; an action request from the model is refused
  and recorded. Nothing the model says reaches a write.
* **human_required** stays ``proposed`` until `approve(run_id, action_id, by)` where ``by`` is an
  `Approver` minted and sealed by a registered `Authenticator` (R-021 #5) — never a free-text
  name (R-017 t02 finding 3) nor a hand-built principal — and that principal is a person (`databridge_outbox`'s machine-actor
  rule). **human_notified** executes, then notifies. **autonomous** executes only when a reversal
  is declared (checked again here, not only at compile). Every action records the pointers it was
  derived from; a proposal citing none is refused, and an automatic action whose arguments carry a
  number or address its evidence does not hold is refused (finding 4).
* **An approval executes once, under current authority.** The approval is claimed under a per-run
  lock (finding 7); before the write is sent the agent is reloaded, its digest compared with the
  proposing run's, and the declaration recompiled (finding 8); a write that succeeded is recorded
  ``executed`` even if capturing its result fails, and a write whose outcome is unknown is never
  retried automatically (finding 9).
* **Identity is never auto-confirmed.** Person-kind nodes reach the model and the answer as
  candidates; an action that touches a subject is ``human_required`` by the compiler's rule.
* **Which model produced what** (§5.8): every step and the answer carry ``produced_by``; the run
  header lists ``models_used``.
"""
from __future__ import annotations

import datetime as dt
import functools
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Union

from databridge_agentdef import (
    AgentDeclaration,
    CompilationDecision,
    GrantedAuthority,
    Outcome,
    compile_agent,
)
from databridge_workflow_spec import WorkflowSpec, load_spec, parse_tool

from .answer import (
    _evidence_text,
    _md,
    _value_in,
    ActionRequest,
    Derivation,
    CheckedAnswer,
    PlannerTurn,
    check_action_claims,
    check_answer,
    render_markdown,
    ungrounded_values,
    verify_derivations,
)
from .shape import describe_schema, shape_args
from .write_paths import normalise_write_paths, path_derivations
from .subtask_shape import delegate_holdings, describe_subtask, summarise_error
from .capture import capture_live_read
from . import public_errors
from .public_errors import scrub as _scrub
from .mapping import (
    DELEGATE_SOURCE,
    FACTS_SOURCE,
    ConnectionAdvert,
    MappingError,
    build_catalogue,
    load_agent,
    load_declaration_file,
    pair_problems,
    parse_declaration_text,
    spec_digest,
)
from .runs import (
    RUN_KIND_AGENT,
    ActionEntry,
    RunOutcome,
    reduces_coverage,
    ActionLog,
    AnswerBlock,
    Coverage,
    RunLock,
    RunLocked,
    RunRecord,
    Step,
    action_id_for,
    actions_path,
    default_notifier,
    find_run,
    index_for,
    iso,
    iter_action_logs,
    read_run,
    run_path,
    utcnow,
    write_run,
)
from .tools import (
    FactsStore,
    PointerRegistry,
    Retriever,
    SourceGateway,
    ToolLayer,
    ToolOutcome,
    default_facts,
    pointer_id,
)

#: The routed task class for the planner. Routing is config (`config/models.yaml`), never a name.
AGENT_TASK_CLASS = "agent"

#: The deployment zone when neither the agent's schedule nor `WALNUT_TIMEZONE` names one (E2E F-29).
DEFAULT_TIMEZONE = "Asia/Singapore"

#: Version of the planner prompt TEMPLATE (`_system_prompt`). Bump it when the template changes;
#: ``produced_by.prompt_version`` also carries a digest of the exact prompt text sent (§5.8).
PLANNER_PROMPT_VERSION = "agent_runtime.planner/5"

#: Failed tool calls (refused, failed, or arguments that do not fit the tool) one run may make
#: before the planner is told to stop calling tools and answer from what it has (N-11).
MAX_FAILED_TOOL_CALLS = 5
#: E2E run 5 F-09: every run is bounded in wall-clock seconds (``WALNUT_AGENT_RUN_SECONDS``,
#: default below). Past WRAP_AT of it the planner is told to answer from what it has; at the
#: budget the run stops and says so.
RUN_SECONDS_ENV = "WALNUT_AGENT_RUN_SECONDS"
DEFAULT_RUN_SECONDS = 300.0
WRAP_AT = 0.75
_clock = time.monotonic


def run_seconds() -> float:
    """The deployment's wall-clock budget for one run, in seconds (a bad value: the default)."""
    try:
        v = float(os.environ.get(RUN_SECONDS_ENV, "") or DEFAULT_RUN_SECONDS)
    except ValueError:
        return DEFAULT_RUN_SECONDS
    return v if v > 0 else DEFAULT_RUN_SECONDS


class ActionRefused(ValueError):
    """An approval / rejection / reversal that may not happen. Plain words, plus a closed
    ``code`` the action log records (ACT: every refused decision is logged)."""

    def __init__(self, message: str = "", *, code: str = "refused") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Approver:
    """WHO decides an action: a principal MINTED by the application's authentication boundary.

    An `Approver` is only valid when an `Authenticator` issued it (`Authenticator.issue`): it then
    carries a ``seal`` — an HMAC over ``(principal_id, authenticated_by, dev_only)`` under a key
    that exists only inside that authenticator, created in this process when the authenticating
    code registered it (`register_authenticator`). The runtime verifies the seal on every approve /
    reject / reverse (R-021 #5): ``Approver("alice", "made-up")`` typed by a caller, a dict, or any
    other object with a ``principal_id`` is a CLAIM, not an authentication, and is refused.

    ``dev_only`` marks a principal a development path vouched for (the CLI's OS login): it is
    recorded on every event it produces, so an audit can tell it from a production principal.
    """

    principal_id: str
    authenticated_by: str
    dev_only: bool = False
    seal: str = field(default="", repr=False, compare=False)


class Authenticator:
    """The capability to mint `Approver`s for ONE authentication mechanism. Held by the code that
    authenticates (the gateway's auth layer; the CLI's dev-only OS login) — never by a caller."""

    def __init__(self, mechanism: str, *, dev_only: bool, key: bytes) -> None:
        self.mechanism = mechanism
        self.dev_only = dev_only
        self.__key = key

    def _mac(self, principal_id: str) -> str:
        msg = json.dumps([principal_id, self.mechanism, self.dev_only]).encode("utf-8")
        return hmac.new(self.__key, msg, hashlib.sha256).hexdigest()

    def issue(self, principal_id: str) -> Approver:
        """The principal THIS mechanism authenticated, sealed so the runtime can verify it."""
        pid = str(principal_id or "").strip()
        if not pid:
            raise ActionRefused("an authenticator cannot issue a principal with no id")
        return Approver(principal_id=pid, authenticated_by=self.mechanism,
                        dev_only=self.dev_only, seal=self._mac(pid))

    def verifies(self, by: Approver) -> bool:
        return by.authenticated_by == self.mechanism and by.dev_only == self.dev_only and \
            hmac.compare_digest(by.seal or "", self._mac(by.principal_id))


_AUTHENTICATORS: dict[str, Authenticator] = {}
_AUTH_LOCK = threading.Lock()


def register_authenticator(mechanism: str, *, dev_only: bool = False) -> Authenticator:
    """Register an authentication mechanism and return the ONLY object that can mint approvers
    for it. Called once, at boot, by the code that authenticates requests (e.g. the gateway's
    auth layer: ``AUTH = register_authenticator("gateway")`` then ``AUTH.issue(principal_id)``
    per authenticated request). Registering the same mechanism again returns the same
    authenticator (an app built twice in one process); a mechanism cannot change its ``dev_only``
    marking."""
    name = str(mechanism or "").strip()
    if not name:
        raise ValueError("say which mechanism authenticates these principals")
    with _AUTH_LOCK:
        have = _AUTHENTICATORS.get(name)
        if have is not None:
            if have.dev_only != bool(dev_only):
                raise ValueError(f"authenticator {name!r} is already registered with "
                                 f"dev_only={have.dev_only}")
            return have
        auth = Authenticator(name, dev_only=bool(dev_only), key=secrets.token_bytes(32))
        _AUTHENTICATORS[name] = auth
        return auth


def _principal(by: Any, what: str) -> str:
    """The authenticated principal id behind ``by``, or `ActionRefused`. ``by`` must be an
    `Approver` a registered `Authenticator` issued (R-021 #5)."""
    if isinstance(by, str) or by is None:
        raise ActionRefused(
            f"{what} REFUSED: {by!r} is a name, not an authenticated principal. Pass the "
            f"principal the application authenticated (an Approver its Authenticator issued); a "
            f"caller-typed name cannot approve anything (CLAUDE.md hard constraint 4)",
            code="principal_not_authenticated")
    if type(by) is not Approver:
        raise ActionRefused(
            f"{what} REFUSED: a {type(by).__name__} is not a principal an authenticator issued; "
            f"an object that merely carries a principal_id is a claim, not an authentication",
            code="principal_not_authenticated")
    pid = by.principal_id
    if not isinstance(pid, str) or not pid.strip():
        raise ActionRefused(f"{what} REFUSED: the principal carries no principal_id",
                            code="principal_not_authenticated")
    auth = _AUTHENTICATORS.get(by.authenticated_by)
    if auth is None or not auth.verifies(by):
        raise ActionRefused(
            f"{what} REFUSED: {pid!r} was not issued by a registered authenticator "
            f"({by.authenticated_by!r} is {'not registered' if auth is None else 'registered, but did not issue this principal'}); "
            f"a principal is minted by the application's authentication layer "
            f"(register_authenticator(...).issue(...)), never constructed by a caller",
            code="principal_not_authenticated")
    return _person(pid, what)


def _decided_by(by: Approver) -> dict:
    """How the decider was authenticated — recorded on every event it produces."""
    out = {"authenticated_by": by.authenticated_by}
    if by.dev_only:
        out["dev_only"] = True
    return out


@dataclass(frozen=True)
class CompileInputs:
    """The facts `compile_agent` needs besides the catalogue. Inputs, never waivers."""

    purposes: Any = None
    packs_dir: Optional[Path] = None
    repo_root: Optional[Path] = None


# ================================================================================================
# helpers
# ================================================================================================
def _as_spec(workflow: Union[WorkflowSpec, str, Path, dict]) -> WorkflowSpec:
    if isinstance(workflow, WorkflowSpec):
        return workflow
    spec = load_spec(workflow)
    if not isinstance(spec, WorkflowSpec):
        raise ValueError("not a workflow/v1 spec")
    return spec


def _as_decl(declaration: Union[AgentDeclaration, str, Path]) -> AgentDeclaration:
    if isinstance(declaration, AgentDeclaration):
        return declaration
    if isinstance(declaration, Path) or (isinstance(declaration, str) and "\n" not in declaration
                                         and declaration.endswith((".yaml", ".yml"))):
        return load_declaration_file(Path(declaration))
    return parse_declaration_text(str(declaration), declared_in="<inline>")


def _trigger(trigger: Optional[Mapping[str, Any]], at: dt.datetime) -> dict:
    t = dict(trigger or {})
    t.setdefault("type", "manual")
    t.setdefault("by", None)
    t.setdefault("question", None)
    t.setdefault("fired_at", iso(at))
    return t


def _tokens(result: Any) -> int:
    ev = getattr(result, "event", None)
    return int(getattr(ev, "tokens", 0) or 0)


def _cost(result: Any) -> float:
    ev = getattr(result, "event", None)
    return float(getattr(ev, "cost_usd", 0.0) or 0.0)


def run_timezone(spec: WorkflowSpec) -> str:
    """The zone "today" is read in (E2E F-29): the agent's own schedule zone, else the deployment's
    `WALNUT_TIMEZONE`, else `DEFAULT_TIMEZONE`. Never a source's clock (a database's UTC
    CURRENT_DATE)."""
    trig = getattr(spec, "trigger", None)
    sched = getattr(trig, "schedule", None) if trig is not None else None
    zone = getattr(sched, "timezone", None) if sched is not None else None
    return str(zone or os.environ.get("WALNUT_TIMEZONE") or DEFAULT_TIMEZONE)


def _local(at: dt.datetime, zone: str) -> tuple[dt.datetime, str]:
    """``at`` in ``zone`` and the zone as it may be SHOWN: an unknown zone falls back to UTC and
    is said as a quoted repr, so a configured value can never carry text into the prompt."""
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415
        return at.astimezone(ZoneInfo(zone)), zone
    except Exception:                                        # noqa: BLE001 - unknown zone: say so
        return at.astimezone(dt.timezone.utc), f"UTC (the configured zone {zone!r} is unknown)"


def run_context(run_id: str, at: dt.datetime, zone: str, withheld_text: str = "", *,
                agent: Optional[str] = None) -> str:
    """The per-run planner context (E2E F-27/F-29/F-30/F-34). Its own message, so the system
    prompt — and `prompt_version` — stays one prompt across firings, while every firing's
    messages (the harness cache key) differ: a run is never replayed from another run's cache.
    The agent id, run id and date here are GROUNDED values a write may carry (N-05)."""
    local, zone = _local(at, zone)
    who = f"agent id {agent}; " if agent else ""
    lines = [f"Run context: {who}run id {run_id}; today is {local.date().isoformat()} "
             f"({local:%A}), local time {local:%H:%M}, timezone {zone}.",
             "Use THIS date for 'today', 'overdue' and 'due within' — pass it into any query as a "
             "literal date; never use a source's CURRENT_DATE / NOW(), which runs in the source's "
             "own zone. Where a write needs this run's id or the agent's id, use the ones above."]
    if withheld_text:
        lines.append(withheld_text)
    return "\n".join(lines)


def _ptr_json(p: Any) -> dict:
    return p.model_dump(mode="json")


def _refs_in(obj: Any) -> list[str]:
    """Every pointer ref an observation carries (``ref`` / ``refs`` keys, at any depth)."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "ref" and isinstance(v, str):
                out.append(v)
            elif k == "refs" and isinstance(v, list):
                out.extend(x for x in v if isinstance(x, str))
            else:
                out.extend(_refs_in(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_refs_in(v))
    return out


#: The source id of a run's own captured context (its clock): not a connection, the run itself.
RUN_CONTEXT_SOURCE = "agent_run"


class RunClock:
    """The run context the planner was given (run id, agent id, TODAY'S DATE), as evidence for the
    one thing it may ground: a DATE rule's input that is the run date (E2E run 4 Q-08 - "due
    within the next 30 days" is bounded by today + 30). Captured into the capture store the first
    time a derivation rests on it, so the bound replays from a content-addressed record of the
    clock, never from the model's word. Capture unavailable: no pointer, and the derivation
    refuses as before (fail closed)."""

    def __init__(self, text: str, *, uri: str, root: Optional[Path] = None) -> None:
        self.text = text
        self._uri = uri
        self._root = root
        self._ptr: Any = None
        self._tried = False

    @property
    def pointer(self) -> Any:
        if not self._tried:
            self._tried = True
            try:
                _sha, self._ptr = capture_live_read(connection=RUN_CONTEXT_SOURCE, uri=self._uri,
                                                    text=self.text, mime="text/plain",
                                                    root=self._root)
            except Exception:                            # noqa: BLE001 - no capture, no pointer
                self._ptr = None
        return self._ptr


class _NullTrace:
    """No-op stand-in for a gateway `TraceHandle` (same method names, duck-typed)."""

    def __getattr__(self, _name: str) -> Callable[..., "_NullTrace"]:
        return lambda *a, **k: self


def _connection_adverts(spec: WorkflowSpec, gateway: Optional[SourceGateway]) -> list:
    needed = sorted({parse_tool(t)["connection"] for t in
                     [*spec.tools, *(a.tool for a in spec.actions)]
                     if parse_tool(t)["family"] == "source"})
    out = []
    for conn in needed:
        if gateway is None:
            out.append(ConnectionAdvert(connection=conn, reachable=False,
                                        problem="no source gateway is connected"))
            continue
        try:
            out.append(gateway.advertise(conn))
        except Exception as e:                               # noqa: BLE001 - reported
            out.append(ConnectionAdvert(connection=conn, reachable=False, problem=str(e)))
    return out


def compile_for_run(spec: WorkflowSpec, decl: AgentDeclaration, *, store: Any,
                    retriever: Optional[Retriever], gateway: Optional[SourceGateway],
                    inputs: Optional[CompileInputs] = None,
                    now: Optional[Callable[[], dt.datetime]] = None,
                    facts: Optional[FactsStore] = None, federation: Any = None
                    ) -> tuple[Optional[CompilationDecision], list[str]]:
    """Compile the declaration against what THIS deployment advertises. ``(decision, problems)``;
    problems that are not the compiler's (halves disagree, a source unreachable) come back too.
    ``facts`` says which connections have a facts store (default: the real one, when present)."""
    inputs = inputs or CompileInputs()
    facts = facts if facts is not None else default_facts()
    problems = pair_problems(spec, decl)
    adverts = _connection_adverts(spec, gateway)
    wanted = sorted({parse_tool(t)["connection"] for t in spec.tools
                     if parse_tool(t)["family"] == FACTS_SOURCE})
    have_facts = []
    for conn in wanted:
        try:
            ok = facts is not None and bool(facts.available(conn))
        except Exception:                                    # noqa: BLE001 - reported below
            ok = False
        if ok:
            have_facts.append(conn)
        else:
            problems.append(f"facts.query:{conn} needs the read-only facts store for {conn!r}, "
                            f"which does not exist yet (run the source's tabular stage)")
    # FED: `delegate.ask:<aa>` is advertised only for adapter agents a federated service can
    # actually answer for right now — a tool with nothing behind it is not a capability.
    have_delegates = []
    for aa in sorted({parse_tool(t)["connection"] for t in spec.tools
                      if parse_tool(t)["family"] == DELEGATE_SOURCE}):
        try:
            ok = federation is not None and bool(federation.available(aa))
        except Exception:                                    # noqa: BLE001 - reported below
            ok = False
        if ok:
            have_delegates.append(aa)
        else:
            problems.append(f"delegate.ask:{aa} needs a federated service that can answer for "
                            f"adapter agent {aa!r}, and none is connected (or {aa!r} is not "
                            f"registered)")
    catalogue, cat_problems = build_catalogue(
        pack=decl.pack, connections=adverts, graph=store is not None,
        retrieval=retriever is not None, facts=have_facts, delegates=have_delegates)
    problems.extend(cat_problems)
    # Actions are not capabilities, so the compiler never screens their tools. The gateway must
    # still say the tool exists (and is a write) — an action nobody can execute is not declared.
    by_conn = {a.connection: a for a in adverts}
    for action in spec.actions:
        p = parse_tool(action.tool)
        adv = by_conn.get(p["connection"])
        if adv is None or not adv.reachable:
            continue                                  # already reported by build_catalogue
        if p["verb"] == "write":
            t = adv.tool(p["tool"] or "")
            if t is None or not t.writes:
                problems.append(f"action {action.name!r} uses {action.tool!r}, which "
                                f"{p['connection']!r} does not advertise as a write tool")
    decision = compile_agent(decl, catalogue=catalogue, purposes=inputs.purposes,
                             packs_dir=inputs.packs_dir, repo_root=inputs.repo_root, now=now)
    return decision, problems


def _tool_schemas(spec: WorkflowSpec, gateway: Optional[SourceGateway],
                  advert: Callable[[str], Any]) -> dict[str, str]:
    """``{tool: one-line argument schema}`` for every live tool this agent may call or write
    through, read from what the connection ADVERTISES (N-11): the planner is shown the schema it
    will be held to. A connection that cannot be described contributes nothing."""
    if gateway is None:
        return {}
    out: dict[str, str] = {}
    names = [*spec.tools, *(a.tool for a in spec.actions)]
    for name in names:
        try:
            p = parse_tool(name)
        except Exception:                                    # noqa: BLE001 - not a tool name
            continue
        if p["family"] != "source" or p["verb"] not in ("read", "write"):
            continue
        try:
            adv = advert(p["connection"])
        except Exception:                                    # noqa: BLE001 - reported at call
            continue
        tools = [adv.tool(p["tool"])] if p["tool"] else             [t for t in (getattr(adv, "tools", ()) or ()) if not t.writes][:40]
        for t in tools:
            if t is None:
                continue
            key = name if p["tool"] else f"{name}:{t.name}"
            out[key] = describe_schema(getattr(t, "input_schema", None))
    return out


def _delegate_holdings(spec: WorkflowSpec, federation: Any) -> dict[str, Any]:
    """``{adapter agent: what its delegate can read}`` for every ``delegate.ask`` tool (P-02)."""
    if federation is None:
        return {}
    out: dict[str, Any] = {}
    for t in spec.tools:
        try:
            p = parse_tool(t)
        except Exception:                                    # noqa: BLE001 - not a tool name
            continue
        if p["family"] == DELEGATE_SOURCE and p["connection"]:
            out[p["connection"]] = delegate_holdings(federation, p["connection"])
    return out


def _system_prompt(spec: WorkflowSpec, authority: GrantedAuthority,
                   schemas: Optional[Mapping[str, str]] = None,
                   delegates: Optional[Mapping[str, Any]] = None) -> str:
    from .derive import RULES                                # noqa: PLC0415
    schemas = dict(schemas or {})
    tools = [t for t in spec.tools]
    acts = [f"- {a.name} ({a.automation_mode}) via {a.tool}: {a.description or ''}".rstrip(": ")
            + (f" — args {schemas[a.tool]}" if a.tool in schemas else "")
            for a in spec.actions]
    live = [f"- {k} {v}" for k, v in schemas.items()
            if not any(k == a.tool for a in spec.actions)]
    return "\n".join([
        f"You are the Walnut agent {spec.agent!r} ({spec.title}). You work ONLY from what the "
        f"tools return; if it did not come from a tool result, do not say it.",
        "Each turn, return JSON with EITHER `tool_calls` [{tool, args}] OR, once you have seen "
        "their results, `actions` [{name, args, derived_from, derivations}] and/or — when you "
        "are done — `final` {claims, clarifications, outcome}. Actions and a final answer sent "
        "in the same turn as tool calls are NOT processed: you may cite only refs you have "
        "already been shown.",
        "A claim that reports on an action carries `actions` [{action_id, status}] with the status "
        "the observation gave it (executed | proposed | execution_failed | refused); it is checked "
        "against the action log.",
        "Every claim cites the pointer refs (p1, p2 …) it rests on. Never invent a ref. When the "
        "data cannot settle a fork, answer on the most defensible reading and add a clarification "
        "(assumption + alternatives + the citations any fact in it rests on). People found in the "
        "data are CANDIDATES, never confirmed; do not name people the question does not ask "
        "about. An action's values (numbers, addresses, ids, names) must come from the evidence "
        "it cites or from the run context (agent id, run id, today's date).",
        "Pointer-valued arguments: where an argument records WHERE a value came from (a "
        "source_pointer column, a 'Source:' line in a draft), write the ref (p3). The runtime "
        "replaces it with that pointer's stable id (ptr:sha256:…) before anything is checked or "
        "written, and adds the pointer to the action's derived_from. A ref is never written as "
        "data.",
        "Computed values: a value you COMPUTE from the evidence rather than copy from it is "
        "declared in `derivations` [{value, rule, inputs, args}] on the action or claim that "
        "states it, and the runtime replays it. rule: "
        + "; ".join(f"{k} ({v})" for k, v in RULES.items())
        + ". Each input is written exactly as it appears in the cited evidence or the run "
        "context. A value that does not replay, or whose inputs are not in the cited evidence, "
        "is refused. Copy values; compute only what the question needs.",
        "When you finish, set `final.outcome` {reason_code, withheld, not_connected}: "
        "reason_code answered | value_absent (the sources do not hold it) | identity_candidate "
        "(the answer is a person a human must confirm) | withheld_by_screen | "
        "source_not_connected; `withheld` lists the withheld items (exactly as listed in the run "
        "context) the question needed; `not_connected` lists sources the question names that no "
        "tool of yours reaches. When you cannot answer, say so in a clarification, not in an "
        "uncited claim.",
        "Tools you may call (args): graph.search {query,k}, graph.node {node_id}, graph.neighbors "
        "{node_id,depth<=2}, graph.path {src,dst}, retrieve.search {query,k}, "
        "source.query:<conn> {sql — one read-only statement}, source.read:<conn>:<tool> {tool args "
        "— exactly its schema below}, "
        "facts.query:<conn> {sql, params?, question?} — use facts.query for EVERY counting or "
        "listing question (how many, list all): it is exact and dated; never count from search.",
        *(["delegate.ask:<adapter agent> {subtask: " + describe_subtask() + ", "
           "answers?: {\"fed:<id>\": \"<the user's answer>\"}} — a read-only sub-query "
           "answered by that adapter agent over ITS sources; values come back with refs, "
           "computed exactly (never do arithmetic yourself). One sub-task reads ONE entity kind "
           "(a facts table or a document kind the delegate holds, below); field names are that "
           "kind's plain column names; a question spanning two kinds is one delegate.ask per "
           "kind. Each sub-task is checked against this contract before it is sent; one that "
           "does not fit is not asked. `answers` carries the user's answers to the federated "
           "planner's questions (ids fed:<sha1>) that your instructions record."]
          if any(parse_tool(t)["family"] == DELEGATE_SOURCE for t in tools) else []),
        *([f"What delegate.ask:{aa} can read (names only): "
           + json.dumps(held, ensure_ascii=False, default=str)
           for aa, held in sorted((delegates or {}).items()) if held]),
        *(["Live tool arguments (each call is checked against its schema before it is sent; a "
           "call that does not fit is not made):\n" + "\n".join(live)] if live else []),
        "Available to this agent: " + (", ".join(tools) or "(none)"),
        ("Declared actions (each needs derived_from refs; a human may have to approve):\n"
         + "\n".join(acts)) if acts else "This agent is READ-ONLY: it has no actions.",
        f"Purpose: {authority.purpose}.",
        "Instructions:\n" + spec.instructions.strip(),
    ])


# ================================================================================================
# action execution (shared by run_agent and approve)
# ================================================================================================
@dataclass(frozen=True)
class _Execution:
    """What happened to one write. ``outcome``:

    * ``executed``   — the source accepted it (``capture_error`` set if its result could not be
      captured; the write still happened and is recorded as such — R-017 t02 finding 9);
    * ``not_sent``   — nothing reached the source (no gateway, no statement, tool not offered);
    * ``source_error`` — the source answered with an error;
    * ``unknown``    — the call raised: the write may or may not have happened. Never retried
      automatically; a person checks the source.
    """

    outcome: str
    detail: str
    pointers: tuple = ()
    capture_error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.outcome == "executed"


_ERROR_KEYS = ("error", "errors", "exception", "fault")
_FAILED_STATUSES = frozenset({"error", "failed", "failure", "fail", "rejected", "denied"})


def _error_shape(obj: Any) -> Optional[str]:
    """Why a structured reply says the call FAILED, or None (R-021 #12): a non-empty ``error`` /
    ``errors`` / ``exception`` / ``fault``, ``isError: true``, ``success`` / ``ok`` false, or a
    failure ``status``. Shape-based and source-independent: no server is named."""
    if isinstance(obj, (list, tuple)) and len(obj) == 1:
        obj = obj[0]
    if not isinstance(obj, Mapping):
        return None
    for k in _ERROR_KEYS:
        v = obj.get(k)
        if v not in (None, False, "", [], {}):
            return f"{k}: {str(v)[:300]}"
    for k in ("isError", "is_error"):
        if obj.get(k) is True:
            return f"{k}: true"
    for k in ("success", "ok"):
        if obj.get(k) is False:
            return f"{k}: false"
    st = obj.get("status")
    if isinstance(st, str) and st.strip().lower() in _FAILED_STATUSES:
        return f"status: {st}"
    return None


def _reply_error(res: Any) -> Optional[str]:
    """The failure a write's reply reports in its STRUCTURED content or as a JSON text body."""
    why = _error_shape(getattr(res, "structured", None))
    if why:
        return why
    text = (getattr(res, "text", "") or "").strip()
    if text[:1] in ("{", "["):
        try:
            return _error_shape(json.loads(text))
        except (ValueError, TypeError):
            return None
    return None


def _scrub_public(reasons: list, skipped: list) -> None:
    """W-08: no coverage note or skipped detail a person reads carries an exception's text (each
    such sentence becomes a fixed one with a reference; the original is logged)."""
    reasons[:] = [_scrub(r) for r in reasons]
    for s in skipped:
        if isinstance(s, dict) and isinstance(s.get("detail"), str):
            s["detail"] = _scrub(s["detail"])


def _load_screen(screen: Any) -> tuple[Any, Optional[str]]:
    if screen is not None:
        return screen, None
    try:
        from databridge_mcp_ingest.screen import load_screen   # noqa: PLC0415 - one screen
        return load_screen(), None
    except Exception as e:                                   # noqa: BLE001 - fail closed
        return None, public_errors.failed("the ingestion screen could not be loaded",
                                          "screen not loaded", e)


def _execute(tool: str, args: dict, gateway: Optional[SourceGateway],
             capture_root: Optional[Path], screen: Any = None) -> _Execution:
    """Run one declared write through the gateway, then capture its result — SCREENED: a result
    the ingestion screen would withhold is not persisted, and a masked one is persisted masked
    (R-021 #6). The write itself happened either way and is recorded as executed."""
    if gateway is None:
        return _Execution("not_sent", "no source gateway is connected, so the write could not be "
                                      "sent")
    parts = parse_tool(tool)
    conn = parts["connection"]
    if parts["family"] != "source" or parts["verb"] != "write" or not parts["tool"]:
        return _Execution("not_sent", f"{tool!r} is not a source.write tool; an action is only "
                                      f"ever a declared write")
    try:
        adv = gateway.advertise(conn)
        t = adv.tool(parts["tool"])
        if t is None or not t.writes:
            return _Execution("not_sent", f"{conn!r} does not advertise {parts['tool']!r} as a "
                                          f"write tool")
        send = lambda: gateway.call_tool(conn, parts["tool"], dict(args))   # noqa: E731
        arg_hash = hashlib.sha256(json.dumps(args, sort_keys=True, default=str)
                                  .encode("utf-8")).hexdigest()[:16]
        uri = str(args.get("path") or args.get("uri") or "") or \
            f"mcp://{conn}/{parts['tool']}?{arg_hash}"
    except Exception as e:                                   # noqa: BLE001 - nothing sent yet
        return _Execution("not_sent", public_errors.failed(
            "the source could not be described", "write: advertise failed", e, tool=tool))
    try:
        res = send()
    except Exception as e:                                   # noqa: BLE001 - reported
        return _Execution("unknown", public_errors.failed(
            "the source call failed", "write: call failed", e, tool=tool)
            + "; whether the write happened is unknown")
    if res.is_error:
        return _Execution("source_error", (res.text or "the source returned an error")[:500])
    prefixes = _write_error_prefixes(gateway, conn)
    head = (res.text or "").lstrip()
    if any(p and head.startswith(p) for p in prefixes):
        # E2E F-02: postgres-mcp reports a failed statement as text with isError false. A reply
        # that opens with a DECLARED error prefix is the source saying no — never `executed`.
        return _Execution("source_error", f"the source answered with an error: "
                                          f"{(res.text or '')[:480]}")
    shaped = _reply_error(res)
    if shaped:
        # R-021 #12: `{"error": "duplicate key"}` with isError false is the source saying no.
        return _Execution("source_error", f"the source answered with an error ({shaped}): "
                                          f"{(res.text or '')[:400]}")
    if not (res.text or "").strip() and not res.data and getattr(res, "structured", None) is None:
        return _Execution("unknown", "the source returned nothing at all, so there is no evidence "
                                     "the write took effect")
    cfg, problem = _load_screen(screen)
    if cfg is None:
        return _Execution("executed", "executed; its result was not captured",
                          capture_error=f"the ingestion screen could not be loaded ({problem}); a "
                                        f"result is never persisted unscreened")
    body = res.text or ""
    if not body and res.data:
        try:
            body = res.data.decode("utf-8")
        except UnicodeDecodeError:
            return _Execution("executed", "executed; its result was not captured",
                              capture_error="the result is binary and cannot be screened, so it "
                                            "was not persisted")
    if not body and getattr(res, "structured", None) is not None:
        body = json.dumps(res.structured, sort_keys=True, default=str)
    try:
        from databridge_mcp_ingest.screen import screen_text   # noqa: PLC0415
        verdict = screen_text(body, cfg)
    except Exception as e:                                   # noqa: BLE001 - fail closed
        return _Execution("executed", "executed; its result was not captured",
                          capture_error=f"the result could not be screened ({type(e).__name__}); "
                                        f"it was not persisted")
    if verdict.decision != "allow":
        rules = ", ".join(str(f.get("rule")) for f in verdict.fired) or "no rule id"
        return _Execution("executed", "executed; its result was withheld by the ingestion screen",
                          capture_error=f"the result was not persisted: the ingestion screen said "
                                        f"{verdict.decision} ({rules})")
    masked = verdict.masked_text
    try:
        if masked != body or not res.data:
            data, mime = masked.encode("utf-8"), "text/plain"   # only what the screen let through
        else:
            data, mime = res.data, res.mime                   # nothing masked: the source's bytes
        _sha, ptr = capture_live_read(connection=conn, uri=uri, text=masked, data=data,
                                      mime=mime, root=capture_root)
    except Exception as e:                                   # noqa: BLE001 - the write happened
        return _Execution("executed", "executed; its result could not be captured",
                          capture_error=public_errors.failed(
                              "the result could not be captured", "write: capture failed", e,
                              tool=tool))
    return _Execution("executed", "executed", (_ptr_json(ptr),))


def _write_error_prefixes(gateway: Any, conn: str) -> tuple[str, ...]:
    """What the gateway says a failed WRITE on ``conn`` starts with (declared data: SQL dialect
    and profile ``error_prefixes``). A gateway that declares none contributes none."""
    fn = getattr(gateway, "write_error_prefixes", None)
    if fn is None:
        return ()
    try:
        return tuple(str(p) for p in (fn(conn) or ()) if p)
    except Exception:                                        # noqa: BLE001 - none declared
        return ()


def args_schema_problems(args: Any, schema: Optional[Mapping[str, Any]]) -> list[str]:
    """Why ``args`` would not be accepted by a tool whose MCP ``inputSchema`` is ``schema`` (E2E
    F-02). ``jsonschema`` validates when it is installed; otherwise the parts of the schema every
    MCP server uses are checked (required keys, unknown keys under ``additionalProperties:
    false``, top-level JSON types). No schema -> nothing can be checked -> no problems."""
    if not isinstance(schema, Mapping) or not schema:
        return []
    if not isinstance(args, Mapping):
        return ["the arguments are not an object"]
    try:
        import jsonschema                                      # noqa: PLC0415
        v = jsonschema.validators.validator_for(dict(schema))(dict(schema))
        return sorted(f"{'/'.join(str(x) for x in e.path) or '(arguments)'}: {e.message}"
                      for e in v.iter_errors(dict(args)))
    except ImportError:
        pass
    except Exception as e:                                   # noqa: BLE001 - a broken schema
        return [public_errors.failed("the tool's input schema could not be applied",
                                     "tool input schema unusable", e)]
    out: list[str] = []
    props = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
    for key in schema.get("required") or []:
        if key not in args:
            out.append(f"'{key}' is a required property")
    if schema.get("additionalProperties") is False:
        out += [f"'{k}' is not an argument this tool accepts" for k in args if k not in props]
    types = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
             "object": Mapping, "array": (list, tuple)}
    for k, v in args.items():
        want = (props.get(k) or {}).get("type") if isinstance(props.get(k), Mapping) else None
        if isinstance(want, str) and want in types and v is not None and \
                not isinstance(v, types[want]):
            out.append(f"{k}: expected {want}")
    return out


def _approval_gate_problems(proposal: Mapping[str, Any]) -> list[str]:
    """Why a recorded proposal may not be approved, read from the proposal itself (hard constraint
    2 — no value without a SourcePointer). The UI hides Approve for these; this is the gate."""
    out: list[str] = []
    args = proposal.get("args") or {}
    if args and not (proposal.get("derived_from") or []):
        out.append("its arguments carry values but the proposal names no input pointers "
                   "(derived_from is empty)")
    guessed = [str(v) for v in (proposal.get("ungrounded_values") or [])]
    if guessed:
        out.append(f"its arguments carry values that appear in none of its cited evidence "
                   f"(ungrounded values: {guessed})")
    return out


def _executed_event(action_id: str, ex: _Execution, by: Optional[str]) -> dict:
    ev = {"event": "executed", "action_id": action_id, "by": by, "detail": ex.detail,
          "result_pointers": list(ex.pointers), "at": iso(utcnow())}
    if ex.capture_error:
        ev["capture_error"] = ex.capture_error
    return ev


def _failed_event(action_id: str, ex: _Execution, by: Optional[str]) -> dict:
    return {"event": "execution_failed", "action_id": action_id, "by": by, "detail": ex.detail,
            "outcome": ex.outcome, "at": iso(utcnow())}


def _escalations_today(agent: str, runs_dir: Optional[Path], today: dt.date) -> int:
    n = 0
    for _rec, log in iter_action_logs(agent=agent, runs_dir=runs_dir):
        for e in log.events():
            if e.get("event") == "proposed" and e.get("automation_mode") == "human_required" \
                    and str(e.get("at", ""))[:10] == today.isoformat():
                n += 1
    return n


#: A citation ref written into an argument: ``p3``, ``[p3]``, ``[^p3]`` — a whole word only.
_REF_IN_ARG = re.compile(r"\[\^?(p\d+)\]|(?<![A-Za-z0-9_])(p\d+)(?![A-Za-z0-9_])")


def _resolve_refs(args: Any, registry: PointerRegistry) -> tuple[Any, list[str]]:
    """``(args, refs)`` with every DISCLOSED citation ref inside a string argument replaced by its
    pointer's stable id (`tools.pointer_id`) — N-05: a run-local ref (``p1``) means nothing in a
    database column; the pointer's id does. A ref the run never disclosed is left as written (and
    so stays an ungrounded value)."""
    used: list[str] = []

    def sub(m: "re.Match[str]") -> str:
        ref = m.group(1) or m.group(2)
        ev = registry.get(ref)
        if ev is None:
            return m.group(0)
        if ref not in used:
            used.append(ref)
        return pointer_id(ev.pointer)

    def walk(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, str):
            return _REF_IN_ARG.sub(sub, v)
        return v

    return walk(args), used


def _norm_name(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def _gathered(steps: list) -> list[str]:
    """What a run that stopped without an answer DID read: per tool, how many pointers came back.
    Counts only — never a value (N-11: an honest partial answer)."""
    out: dict[str, int] = {}
    for st in steps:
        if (st.policy_decision or {}).get("decision") == "permit" and st.result_pointers:
            out[st.tool] = out.get(st.tool, 0) + len(st.result_pointers)
    return [f"{tool}: {n} pointer(s)" for tool, n in out.items()]


def _register_match(name: str, register: list[dict]) -> list[dict]:
    """Register entries ``name`` refers to: the item itself, or its file name."""
    want = str(name or "").strip().replace("\\", "/").strip("/").lower()
    if not want:
        return []
    out = []
    for w in register:
        item = str(w.get("item") or "").replace("\\", "/").strip("/").lower()
        if item and (want == item or want.endswith("/" + item) or item.endswith("/" + want)
                     or want.rsplit("/", 1)[-1] == item.rsplit("/", 1)[-1]):
            out.append(w)
    return out


def _run_outcome(*, final: Any, checked: Optional[CheckedAnswer], skipped: list[dict],
                 reasons: list[str], register: list[dict], question: str, spec: WorkflowSpec,
                 registry: PointerRegistry) -> RunOutcome:
    """The run's STRUCTURAL outcome (N-12). The planner's ``final.outcome`` is a claim; each part
    is accepted only when structure bears it out, and what is accepted is also written into
    ``skipped`` so coverage reflects it:

    * ``withheld`` — an item the planner names must be on the screen's withheld register (or a
      read this run was withheld); it is recorded from the REGISTER (source, item, decision,
      rules), never from the planner's words;
    * ``not_connected`` — a source the planner names must appear in the question and be none of
      this agent's sources or connections;
    * ``identity_candidate`` needs a candidate the answer names or cites; ``answered`` needs a
      supported claim. A reason code that is not borne out is replaced by the one structure
      gives, and the replacement is said in ``reasons``.

    ``candidates`` come from the checked answer (the people it names or cites, with their
    pointers); ``human_confirmation_required`` is set whenever the answer names a candidate."""
    from .answer import OUTCOME_REASON_CODES, _fold, _in_pool          # noqa: PLC0415

    declared = getattr(final, "outcome", None) if final is not None else None
    withheld: list[dict] = []

    def add_withheld(w: dict) -> bool:
        key = (w.get("source"), w.get("item"))
        if any((x.get("source"), x.get("item")) == key for x in withheld):
            return False
        withheld.append({"source": w.get("source"), "item": w.get("item"),
                         "decision": w.get("decision"), "rules": list(w.get("rules") or [])})
        return True

    for sk in skipped:                                   # reads THIS run the screen withheld
        if sk.get("reason_code") == "withheld_by_screen":
            add_withheld({"source": parse_tool(sk["tool"])["connection"]
                          if "." in str(sk.get("tool")) else None, "item": sk.get("item"),
                          "decision": sk.get("decision"), "rules": sk.get("rules")})
    named_withheld = False
    for name in (getattr(declared, "withheld", None) or []):
        hits = _register_match(name, register)
        if not hits:
            reasons.append(f"the answer names {str(name)[:160]!r} as withheld, but the screen's "
                           f"register does not list it; not recorded")
            continue
        for w in hits:
            named_withheld = True
            if add_withheld(w):
                detail = (f"{w.get('source')}: {w.get('item')} withheld by the ingestion screen "
                          f"({w.get('decision')}: {', '.join(w.get('rules') or []) or 'no rule id'})")
                skipped.append({"tool": "withheld_register", "reason_code": "withheld_by_screen",
                                "detail": detail, "source": w.get("source"),
                                "item": w.get("item"), "decision": w.get("decision"),
                                "rules": list(w.get("rules") or [])})
                reasons.append(detail)
    own = {_norm_name(s) for s in spec.sources}
    for t in [*spec.tools, *(a.tool for a in spec.actions)]:
        try:
            own.add(_norm_name(parse_tool(t)["connection"]))
        except Exception:                                    # noqa: BLE001 - not a tool name
            pass
    own.discard("")
    not_connected: list[str] = []
    q = _fold(question)
    for name in (getattr(declared, "not_connected", None) or []):
        nm = " ".join(str(name or "").split())[:80]
        n = _norm_name(nm)
        if not n or nm in not_connected:
            continue
        if not _in_pool(nm, q):
            reasons.append(f"the answer says {nm!r} is not connected, but the question does not "
                           f"name it; not recorded")
            continue
        if any(n == o or n in o or o in n for o in own):
            reasons.append(f"the answer says {nm!r} is not connected, but it is one of this "
                           f"agent's sources; not recorded")
            continue
        not_connected.append(nm)
        detail = f"{nm} is named in the question but is not a source this agent can read"
        skipped.append({"tool": nm, "reason_code": "source_not_connected", "detail": detail})
        reasons.append(detail)
    candidates: list[dict] = []
    items = [*(checked.claims if checked else []), *(checked.clarifications if checked else [])]
    for c in items:
        for cand in getattr(c, "candidates", None) or []:
            if any(str(x["value"]).lower() == str(cand["value"]).lower() for x in candidates):
                continue
            cites = [ev.pointer.model_dump(mode="json") for r in cand.get("refs") or []
                     if (ev := registry.get(r)) is not None]
            candidates.append({"subject_kind": cand.get("subject_kind"), "value": cand["value"],
                               "cites": cites})
    hcr = bool(candidates) or any(getattr(c, "subject_candidate", False) for c in items)
    supported = any(c.supported for c in (checked.claims if checked else []))
    borne = {"answered": supported, "value_absent": True,
             "identity_candidate": bool(candidates), "withheld_by_screen": named_withheld,
             "source_not_connected": bool(not_connected)}
    said = getattr(declared, "reason_code", None)
    said = str(said).strip() if said else None
    if final is None:
        code = "no_answer"
    else:
        derived = ("source_not_connected" if not_connected else
                   "withheld_by_screen" if named_withheld else
                   "answered" if supported else "value_absent")
        code = said if said in OUTCOME_REASON_CODES and borne.get(said) else derived
        if said and code != said:
            reasons.append(f"the answer's outcome {said[:40]!r} is not borne out by the run; "
                           f"recorded as {code!r}")
    status = "reduced" if final is None or any(reduces_coverage(sk) for sk in skipped) \
        else "complete"
    return RunOutcome(reason_code=code, human_confirmation_required=hcr,
                      coverage_status=status, candidates=candidates, withheld=withheld,
                      not_connected=not_connected)


# ================================================================================================
# the run
# ================================================================================================
def run_agent(workflow: Union[WorkflowSpec, str, Path, dict],
              declaration: Union[AgentDeclaration, str, Path], *,
              store: Any = None, harness: Any, gateway: Optional[SourceGateway] = None,
              retriever: Optional[Retriever] = None, trigger: Optional[Mapping[str, Any]] = None,
              trace: Any = None, runs_dir: Optional[Path] = None,
              capture_dir: Optional[Path] = None, compile_inputs: Optional[CompileInputs] = None,
              notifier: Optional[Callable[[dict], None]] = None,
              vocabulary: Any = None, facts: Optional[FactsStore] = None,
              federation: Any = None, screen: Any = None,
              withheld: Optional[Any] = None) -> RunRecord:
    """Run one agent once and return its `RunRecord` (also written under ``WALNUT_RUNS_DIR``).

    ``trace`` is anything with the gateway `TraceHandle` method names (``recorder.begin(...)``);
    it is closed with the run's status. ``trigger`` is ``{type, by, question, …}`` — the question
    is kept verbatim. ``screen`` is the ingestion screen config every live read passes (default:
    ingestion's own `load_screen()`); it is injectable for tests, never skippable.
    ``withheld`` is the ingestion screen's register of withheld items for this agent's sources
    (`withheld.screen_withheld`, the default); the planner is told their names and rule ids only.
    """
    t_start = time.perf_counter()
    t_clock = _clock()
    budget_s = run_seconds()
    started = utcnow()
    tr = trace if trace is not None else _NullTrace()
    spec = _as_spec(workflow)
    decl = _as_decl(declaration)
    trig = _trigger(trigger, started)
    run_id = f"run-{started.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    path = run_path(run_id, agent=spec.agent, kind=RUN_KIND_AGENT, runs_dir=runs_dir)
    log = ActionLog(actions_path(path))
    record = RunRecord(run_id=run_id, spec_digest=spec_digest(spec, decl), trigger=trig,
                       started=iso(started), status="failed")
    question = trig.get("question")
    heading, verbatim = ("Question", str(question)) if question else \
        ("Standing instruction", spec.created_from)

    def finish(status: str, md: str) -> RunRecord:
        record.status = status                                        # type: ignore[assignment]
        record.ended = iso(utcnow())
        if not record.answer.markdown:
            record.answer = AnswerBlock(markdown=md, citations=record.answer.citations,
                                        unsupported_claims=record.answer.unsupported_claims)
        write_run(record, path)
        tr.note(key="run_record", text=str(path))
        tr.close({"completed": "answered"}.get(status, status))
        return record

    # --- 1. both halves, then the compiler. No override. ------------------------------------
    tr.note(key="workflow", text=f"{spec.agent} mode={spec.mode} spec_digest={record.spec_digest}")
    facts = facts if facts is not None else default_facts()
    decision, problems = compile_for_run(spec, decl, store=store, retriever=retriever,
                                         gateway=gateway, inputs=compile_inputs, facts=facts,
                                         federation=federation)
    if not spec.sources and not getattr(spec, "delegates", None):
        # F-09: an agent's scope IS its declared sources - one that declares none (and delegates
        # to no adapter agent) can read nothing, so it is not run at all
        problems = [*problems, "it declares no source to read and delegates to no adapter agent, "
                               "so it can see nothing (its scope is its declared sources); name "
                               "the source(s) it should read"]
    if problems or decision is None or decision.outcome is not Outcome.COMPILED:
        reasons = list(problems)
        if decision is not None and decision.outcome is not Outcome.COMPILED:
            reasons += [f"{r.code.value}: {r.detail}" for r in decision.refusals]
        record.coverage = Coverage(searched=[], skipped=[{"tool": "*", "reason_code":
                                                          "agent_not_runnable"}],
                                   reasons=[f"the agent cannot run: {r}" for r in reasons])
        tr.escalation(to="agent owner", reason="declaration did not compile or does not match "
                                               "its workflow", blocking=True)
        md = "\n".join([f"**{heading} (verbatim):** {_md(verbatim)}", "",
                        "**This agent cannot run.** Nothing was read and no model was called:"]
                       + [f"- {_md(r)}" for r in reasons])
        record.outcome = RunOutcome(reason_code="agent_not_runnable", coverage_status="reduced")
        return finish("refused", md)
    authority = GrantedAuthority.from_decision(decision)
    compiled = authority.compiled
    tr.principal(actor=f"agent:{compiled.ref}", on_behalf_of=trig.get("by"),
                 purpose=authority.purpose, grant_id=None)
    token_cap = min(spec.limits.tokens_per_run, compiled.budget.tokens_per_run)
    tr.budget(max_llm_tokens=token_cap, max_steps=spec.limits.max_steps,
              max_tool_calls=spec.limits.max_tool_calls,
              admission_id=compiled.budget.admission_id)
    tr.note(key="eval_binding", text=f"{compiled.evals.golden_set} ({compiled.evals.records} "
                                     f"records) is bound but not run by this runtime")

    registry = PointerRegistry(track_disclosure=True)          # R-021 #2 / #13
    #: Text a written value may come from besides its cited evidence: what the agent's OWNER
    #: declared (pinned by spec_digest) and this run's own context (R-021 #3).
    owner_text = "\n".join([spec.title or "", spec.created_from or "", spec.instructions or "",
                            *(a.description or "" for a in spec.actions)])
    #: The run context the runtime itself gives the planner: run id, AGENT id (N-05) and date.
    run_literals = "\n".join([run_id, spec.agent,
                              _local(started, run_timezone(spec))[0].date().isoformat()])
    grounding_literals = f"{owner_text}\n{run_literals}"
    #: Q-08: the run's own clock, as the planner was told it, captured (on first use) so a date
    #: rule over the run date replays from evidence (`verify_derivations(clock=)`).
    clock = RunClock(run_context(run_id, started, run_timezone(spec), agent=spec.agent),
                     uri=f"run://{spec.agent}/{run_id}/context", root=capture_dir)
    tools = ToolLayer(spec=spec, authority=authority, registry=registry, store=store,
                      retriever=retriever, gateway=gateway, capture_root=capture_dir,
                      vocabulary=vocabulary, facts=facts, federation=federation,
                      screen=screen)
    notify = notifier or default_notifier(runs_dir)

    steps: list[Step] = []
    skipped: list[dict] = []
    reasons: list[str] = []
    searched: set[str] = set()
    action_entries: list[ActionEntry] = []
    #: every action the model ASKED for, with what became of it — the log claims are checked
    #: against (E2E F-07). Refused requests never become an ActionEntry, so they live here.
    action_outcomes: list[dict] = []
    #: failed / args-invalid skipped entries per tool, until a later call of it works (N-11)
    failed_by_tool: dict[str, list[dict]] = {}
    tokens_used = 0
    cost_used = 0.0
    model_calls = 0
    tool_calls = 0
    stop_reason: Optional[str] = None
    final = None
    final_by: Optional[dict] = None
    failed = False
    system_prompt = _system_prompt(spec, authority, _tool_schemas(spec, gateway, tools._advert),
                                   _delegate_holdings(spec, federation))
    prompt_version = (f"{PLANNER_PROMPT_VERSION}+sha256:"
                      f"{hashlib.sha256(system_prompt.encode('utf-8')).hexdigest()[:12]}")
    turn_by: dict[str, Optional[dict]] = {"current": None}     # produced_by of the live turn

    def produced_by(result: Any) -> dict:
        ev = getattr(result, "event", None)
        model = getattr(ev, "model_used", None)
        if model and model not in record.models_used:
            record.models_used.append(model)
        return {"agent": spec.agent, "agent_version": decl.version,
                "task_class": getattr(ev, "task_class", None) or AGENT_TASK_CLASS,
                "model": model, "prompt_version": prompt_version, "run_id": run_id,
                "at": iso(utcnow())}

    def record_tool(o: ToolOutcome) -> None:
        cap = o.capability or ""
        sid, _, cname = cap.partition(".")
        tr.capability(source_id=sid or None, capability=cname or o.tool)
        tr.policy(withheld=not o.permitted, policy_id=(o.grant_id or "agentdef.grant_set"),
                  reason_code=o.reason_code, source_id=sid or None, field_path=o.tool)
        for p in o.pointers:
            tr.from_source_pointer(p, origin=o.origin)
        steps.append(Step(tool=o.tool, args=o.args, result_pointers=[_ptr_json(p) for p in
                                                                      o.pointers],
                          policy_decision=o.policy_decision(), ms=o.ms,
                          produced_by=turn_by["current"]))
        if not o.permitted:
            skipped.append({"tool": o.tool, "reason_code": o.reason_code, "detail": o.detail})
            reasons.append(f"{o.tool} was refused ({o.reason_code}): {o.detail}")
        elif not o.ok:
            obs = o.observation if isinstance(o.observation, dict) else {}
            err = _scrub(summarise_error(obs.get("error", "no result")))  # P-02 / W-08
            code = "tool_args_invalid" if obs.get("args_invalid") else "tool_failed"
            entry = {"tool": o.tool, "reason_code": code, "detail": err}
            skipped.append(entry)
            failed_by_tool.setdefault(o.tool, []).append(entry)
            reasons.append(f"{o.tool} returned nothing usable: {err}")
        else:
            # N-11/N-12: a later call of the same tool that worked RECOVERS the earlier failures —
            # still listed, no longer reducing coverage.
            for entry in failed_by_tool.pop(o.tool, []):
                entry["recovered"] = True
            p = parse_tool(o.tool)
            searched.add(p["connection"] if p["family"] == "source" else str(p["family"]))
            if p["family"] == DELEGATE_SOURCE and isinstance(o.observation, dict):
                searched.update(o.observation.get("sources_read") or [])
                for sk in o.observation.get("delegate_skipped") or []:
                    detail = (f"{sk.get('connection') or sk.get('adapter_agent') or '?'} "
                              f"({sk.get('channel') or 'delegate'}): {sk.get('reason')}")
                    skipped.append({"tool": o.tool, "reason_code": "delegate_skipped",
                                    "detail": detail})
                    reasons.append(f"{o.tool} could not read {detail}")
            withheld = (o.observation or {}).get("withheld_out_of_scope") if isinstance(
                o.observation, dict) else None
            if withheld:
                reasons.append(f"{o.tool}: {withheld} pointer(s) from sources outside this "
                               f"agent's declared sources were withheld")
                skipped.append({"tool": o.tool, "reason_code": "out_of_scope",
                                "detail": f"{withheld} pointer(s) withheld"})
            screened = (o.observation or {}).get("withheld_by_screen") if isinstance(
                o.observation, dict) else None
            if screened:
                rules = ", ".join(str(r) for r in screened.get("rules") or []) or "no rule id"
                what = (f"{screened['rows']} row(s)" if screened.get("rows") else "the item")
                detail = (f"{what} withheld by the ingestion screen ({screened.get('decision')}: "
                          f"{rules})")
                reasons.append(f"{o.tool}: {detail}")
                skipped.append({"tool": o.tool, "reason_code": "withheld_by_screen",
                                "detail": detail, "decision": screened.get("decision"),
                                "rules": list(screened.get("rules") or []),
                                "item": str((o.args or {}).get("path") or (o.args or {}).get("uri")
                                            or o.tool)})
            no_ptr = (o.observation or {}).get("excluded_no_pointer") if isinstance(
                o.observation, dict) else None
            if no_ptr:
                reasons.append(f"{o.tool}: {no_ptr} result(s) carried no source pointer and were "
                               f"excluded (not evidence)")
                skipped.append({"tool": o.tool, "reason_code": "no_pointer",
                                "detail": f"{no_ptr} result(s) excluded"})
        if tools.vocab_problem and not any("vocabulary" in r for r in reasons):
            reasons.append(f"{tools.vocab_problem}; every entity was therefore shown as a "
                           f"candidate needing human selection")

    def refuse_action(req: ActionRequest, code: str, detail: str) -> None:
        tool = next((a.tool for a in spec.actions if a.name == req.name), f"action:{req.name}")
        tr.policy(withheld=True, policy_id="agent_runtime.action_gate", reason_code=code,
                  field_path=f"action:{req.name}")
        steps.append(Step(tool=tool, args={"action": req.name, **req.args},
                          policy_decision={"decision": "refuse", "reason_code": code,
                                           "detail": detail, "capability": None,
                                           "grant_id": None},
                          produced_by=turn_by["current"]))
        skipped.append({"tool": tool, "reason_code": code, "detail": detail, "action": req.name})
        reasons.append(f"action {req.name!r} was refused ({code}): {detail}")
        action_outcomes.append({"name": req.name, "args": dict(req.args), "status": "refused",
                                "detail": f"{code}: {detail}"})

    def _action_schema(tool: str) -> Optional[Mapping[str, Any]]:
        """The write tool's published ``inputSchema`` (E2E F-02, N-11), or None."""
        p = parse_tool(tool)
        if gateway is None or p["family"] != "source" or not p["tool"]:
            return None
        try:
            t = tools._advert(p["connection"]).tool(p["tool"])
        except Exception:                                    # noqa: BLE001 - checked at execute
            return None
        return getattr(t, "input_schema", None) if t else None

    def propose(req: ActionRequest) -> Optional[dict]:
        if spec.mode == "read_only" or not compiled.actions:
            refuse_action(req, "read_only_agent", "this agent is read-only; it has no write path")
            return None
        decl_action = next((a for a in compiled.actions if a.name == req.name), None)
        spec_action = next((a for a in spec.actions if a.name == req.name), None)
        if decl_action is None or spec_action is None:
            refuse_action(req, "action_not_declared", f"{req.name!r} is not a declared action")
            return None
        # N-05: a run-local citation ref written INTO an argument (a source_pointer column, a
        # "Source: p1" line) becomes that pointer's stable id, and the pointer joins derived_from.
        args, arg_refs = _resolve_refs(req.args, registry)
        refs = list(dict.fromkeys([*req.derived_from, *arg_refs]))
        evidence = [registry.get(r) for r in refs]
        if not req.derived_from or not evidence or any(e is None for e in evidence):
            refuse_action(req, "action_without_input_pointers",
                          "an action must name the pointers (refs issued this run) it is based on")
            return None
        schema = _action_schema(spec_action.tool)
        args, shaped = shape_args(args, schema)                     # N-11
        # P-04: a file write lands directly in its connection's root (no folder assumed)
        args, path_notes = normalise_write_paths(args, schema)
        shaped = [*shaped, *path_notes]
        schema_problems = args_schema_problems(args, schema) if schema else []
        if schema_problems:
            refuse_action(req, "action_args_do_not_match_tool",
                          f"{spec_action.tool} would not accept these arguments: "
                          f"{'; '.join(schema_problems)}. It takes {describe_schema(schema)}")
            return None
        mode = decl_action.automation_mode or "human_required"
        if mode == "autonomous" and not (decl_action.reversal or "").strip():
            refuse_action(req, "autonomous_without_reversal",
                          "an autonomous action must declare how it is reversed")
            return None
        # N-06: a value DERIVED from the evidence is grounded when its derivation is declared and
        # replays from cited inputs (the derived-pointer rule); nothing else computed is.
        # P-04: a file name the runtime can assemble from grounded pieces is offered to the SAME
        # replay gate as the planner's own derivations — accepted only if it replays.
        def _grounded(token: str) -> bool:
            return any(_value_in(token, _evidence_text(e)) for e in evidence) or                 _value_in(token, grounding_literals)

        auto = [Derivation(**d) for d in path_derivations(args, schema, _grounded)]
        verified, derivation_problems = verify_derivations([*req.derivations, *auto], evidence,
                                                           literals=grounding_literals,
                                                           clock=clock)
        ids = "\n".join(pointer_id(e.pointer) for e in evidence)      # type: ignore[union-attr]
        guessed = ungrounded_values(args, evidence, literals=f"{grounding_literals}\n{ids}",
                                    derived=[v.value for v in verified])
        if guessed and mode != "human_required":
            why = (f"; declared derivations that did not replay: {derivation_problems}"
                   if derivation_problems else "")
            refuse_action(req, "action_value_not_in_evidence",
                          f"{mode} actions write without a person looking first, and these "
                          f"values appear in none of the cited evidence: {guessed}{why}. Copy "
                          f"values exactly as the evidence or the run context gives them; declare "
                          f"a computed value in `derivations` (rule + inputs from the evidence); "
                          f"write a pointer as its ref (p1)")
            return None
        if mode == "human_required":
            used = _escalations_today(spec.agent, runs_dir, started.date())
            if used >= compiled.budget.escalations_per_day:
                refuse_action(req, "escalations_per_day_reached",
                              f"{used} approvals already requested today; the declared ceiling "
                              f"is {compiled.budget.escalations_per_day}")
                return None
        idx = len(action_entries)
        ptrs = [_ptr_json(e.pointer) for e in evidence]               # type: ignore[union-attr]
        ptrs = list({json.dumps(x, sort_keys=True): x for x in ptrs}.values())
        entry = ActionEntry(name=req.name, automation_mode=mode, status="proposed", by=None,
                            pointers=ptrs)
        action_entries.append(entry)
        aid = action_id_for(idx)
        proposal = {"event": "proposed", "action_id": aid, "run_id": run_id, "agent": spec.agent,
                    "name": req.name, "automation_mode": mode, "tool": spec_action.tool,
                    "args": args, "derived_from": ptrs, "reversal": decl_action.reversal,
                    "touches_subject": decl_action.touches_subject,
                    "spec_digest": record.spec_digest, "produced_by": turn_by["current"],
                    "at": iso(utcnow())}
        if guessed:                          # human_required only: the approver sees them first
            proposal["ungrounded_values"] = guessed
        if derivation_problems:
            proposal["derivation_problems"] = derivation_problems
        if args != req.args:                 # what the planner asked for, beside what is sent
            proposal["requested_args"] = req.args
        if arg_refs:
            proposal["resolved_refs"] = [{"ref": r, "pointer_id": pointer_id(
                registry.get(r).pointer)} for r in arg_refs]          # type: ignore[union-attr]
        if verified:
            proposal["derivations"] = [v.to_json() for v in verified]
        if auto:
            proposal["runtime_derivations"] = [d.value for d in auto]   # P-04: built, then replayed
        if shaped:
            proposal["arguments_shaped"] = shaped
        log.append(proposal)
        steps.append(Step(tool=spec_action.tool, args={"action": req.name, **args},
                          result_pointers=[],
                          policy_decision={"decision": "propose", "reason_code": mode,
                                           "detail": f"action {aid} proposed", "capability": None,
                                           "grant_id": None},
                          produced_by=turn_by["current"]))
        if mode == "human_required":
            tr.escalation(to="approver", reason=f"action {aid} {req.name} needs approval",
                          blocking=False)
            action_outcomes.append({"name": req.name, "args": dict(args),
                                    "status": "proposed", "action_id": aid,
                                    "detail": "awaiting human approval"})
            return {"action_id": aid, "status": "proposed", "awaiting": "human approval"}
        ex = _execute(spec_action.tool, args, gateway, capture_dir, screen=screen)
        action_outcomes.append({"name": req.name, "args": dict(args), "action_id": aid,
                                "status": "executed" if ex.ok else "execution_failed",
                                "detail": ex.detail})
        if not ex.ok:
            log.append(_failed_event(aid, ex, None))
            what = ("it waits for a person to approve a retry or reject it"
                    if ex.outcome != "unknown" else
                    "whether it happened is UNKNOWN; a person must check the source before "
                    "anything else is done")
            reasons.append(f"action {aid} {req.name} could not be executed ({ex.detail}); {what}")
            return {"action_id": aid, "status": "proposed", "error": ex.detail}
        action_entries[idx] = entry.model_copy(update={"status": "executed"})
        log.append(_executed_event(aid, ex, None))
        result_ptrs = list(ex.pointers)
        if ex.capture_error:
            reasons.append(f"action {aid} {req.name} executed, but its result could not be "
                           f"captured ({ex.capture_error})")
        if mode == "human_notified":
            notice = {"event": "notified", "action_id": aid, "run_id": run_id,
                      "agent": spec.agent, "name": req.name, "tool": spec_action.tool,
                      "result_pointers": result_ptrs, "at": iso(utcnow())}
            log.append(notice)
            try:
                notify(notice)
            except Exception as e:                           # noqa: BLE001 - reported
                reasons.append(public_errors.failed(
                    f"the notification for action {aid} could not be delivered",
                    "notification failed", e))
            tr.escalation(to="owner (notified)", reason=f"action {aid} {req.name} executed",
                          blocking=False)
        return {"action_id": aid, "status": "executed", "result_pointers": len(result_ptrs)}

    try:
        # --- 2. the planner loop ----------------------------------------------------------------
        if withheld is None:
            from .withheld import screen_withheld  # noqa: PLC0415
            withheld = screen_withheld(sorted(
                {str(parse_tool(t)["connection"]) for t in spec.tools
                 if parse_tool(t)["family"] in ("source", "facts") and parse_tool(t)["connection"]}))
        from .withheld import describe as _describe_withheld  # noqa: PLC0415
        register = list(withheld or [])
        withheld_text = _describe_withheld(register)
        #: what the runtime itself told the planner — a claim may state it (N-13)
        answer_literals = f"{run_literals}\n{withheld_text}"
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": run_context(run_id, started, run_timezone(spec),
                                                    withheld_text, agent=spec.agent)},
            {"role": "user", "content": f"{heading} (verbatim): {verbatim}"},
        ]
        #: N-11: why the planner must stop calling tools and answer from what it has (one turn)
        wrap_up: Optional[str] = None
        wrapping = False
        last_turn_tokens = 0
        failed_calls = 0
        for step_no in range(spec.limits.max_steps):
            if tokens_used >= token_cap:
                stop_reason = f"the token budget for one run ({token_cap}) was used up"
                break
            # F-09: the wall clock bounds every run; near it the planner answers from what it has
            spent = _clock() - t_clock
            if spent >= budget_s:
                stop_reason = f"the time budget for one run ({budget_s:g} s) was used up"
                break
            if wrap_up is None and step_no > 0 and spent >= WRAP_AT * budget_s:
                wrap_up = f"the time budget for one run ({budget_s:g} s) is nearly used up"
            if wrap_up is None and step_no > 0:
                if step_no == spec.limits.max_steps - 1:
                    wrap_up = f"the step limit ({spec.limits.max_steps}) is reached after this turn"
                elif tokens_used + last_turn_tokens >= token_cap:
                    wrap_up = f"the token budget for one run ({token_cap}) is nearly used up"
            if wrap_up is not None and not wrapping:
                wrapping = True
                tr.note(key="planner_wrap_up", text=wrap_up)
                messages.append({"role": "user", "content": (
                    f"WRAP UP NOW: {wrap_up}. No tool will be run from here on. Return `final` "
                    f"from what you have been shown: answer what the evidence settles, cite it, "
                    f"and say in a clarification what could not be checked.")})
            try:
                result = harness.complete_structured(AGENT_TASK_CLASS, messages, PlannerTurn)
            except Exception as e:                               # noqa: BLE001 - plain words
                name = type(e).__name__
                if name == "BudgetExceeded":
                    stop_reason = public_errors.failed("the model budget was reached",
                                                       "model budget reached", e)
                else:
                    stop_reason = public_errors.failed("the model could not be asked",
                                                       "planner call failed", e)
                    failed = True
                break
            model_calls += 1
            last_turn_tokens = _tokens(result)
            tokens_used += last_turn_tokens
            cost_used += _cost(result)
            record.tokens, record.cost_usd = tokens_used, round(cost_used, 6)
            turn_by["current"] = produced_by(result)
            if getattr(getattr(result, "event", None), "cached", False):
                # E2E F-27 (defence in depth behind EF4's per-run context): a planner turn the
                # harness REPLAYED from its cache was not decided in this run; nothing it says is
                # executed or answered.
                stop_reason = ("the model's reply was replayed from the harness cache, not "
                               "produced for this run; nothing in it was acted on")
                failed = True
                break
            turn: PlannerTurn = result.parsed
            observations: list[dict] = []
            if turn.tool_calls and not wrapping:
                for call in turn.tool_calls:
                    if tool_calls >= spec.limits.max_tool_calls:
                        wrap_up = (f"the tool-call limit ({spec.limits.max_tool_calls}) was "
                                   f"reached")
                        observations.append({"protocol": f"not run: {wrap_up}"})
                        break
                    if failed_calls >= MAX_FAILED_TOOL_CALLS:
                        wrap_up = (f"{failed_calls} tool calls failed or were refused in this "
                                   f"run (the limit is {MAX_FAILED_TOOL_CALLS})")
                        observations.append({"protocol": f"not run: {wrap_up}"})
                        break
                    tool_calls += 1
                    outcome = tools.call(call.tool, call.args)
                    record_tool(outcome)
                    if not outcome.ok:
                        failed_calls += 1
                    observations.append({"tool": call.tool, "permitted": outcome.permitted,
                                         **({"refused": outcome.detail} if not outcome.permitted
                                            else {"result": outcome.observation})})
                    if wrap_up is None and failed_calls >= MAX_FAILED_TOOL_CALLS:
                        wrap_up = (f"{failed_calls} tool calls failed or were refused in this "
                                   f"run (the limit is {MAX_FAILED_TOOL_CALLS})")
                    elif wrap_up is None and tool_calls >= spec.limits.max_tool_calls:
                        wrap_up = (f"the tool-call limit ({spec.limits.max_tool_calls}) was "
                                   f"reached")
                # R-021 #2: actions and a final answer in the SAME turn as tool calls were written
                # before the model saw those results; they could cite refs the calls were about to
                # mint. They are not processed — the model sends them again, having seen them.
                held_actions = []
                for req in turn.actions:
                    if spec.mode == "read_only" or not compiled.actions:
                        propose(req)            # refused outright: read-only needs no refs to say no
                        observations.append({"action": req.name,
                                             "refused": skipped[-1]["detail"]})
                        continue
                    held_actions.append(req)
                    tr.policy(withheld=True, policy_id="agent_runtime.action_gate",
                              reason_code="action_before_observation",
                              field_path=f"action:{req.name}")
                    steps.append(Step(
                        tool=next((a.tool for a in spec.actions if a.name == req.name),
                                  f"action:{req.name}"),
                        args={"action": req.name, **req.args},
                        policy_decision={"decision": "refuse",
                                         "reason_code": "action_before_observation",
                                         "detail": "sent in the same turn as tool calls, before "
                                                   "their results were seen; not processed",
                                         "capability": None, "grant_id": None},
                        produced_by=turn_by["current"]))
                held = ([f"{len(held_actions)} action(s)"] if held_actions else []) + \
                    (["the final answer"] if turn.final is not None else [])
                if held:
                    note = (f"not processed: {' and '.join(held)} came in the same turn as tool "
                            f"calls. Send them again in a later turn; cite only refs you have "
                            f"been shown")
                    observations.append({"protocol": note})
                    tr.note(key="planner_protocol", text=note)
            else:
                if turn.tool_calls:
                    # wrapping up: no tool is run, so nothing new can be cited — the actions and
                    # the final answer of this turn rest only on refs already shown.
                    note = (f"not run: {len(turn.tool_calls)} tool call(s) asked for after the "
                            f"wrap-up ({wrap_up})")
                    observations.append({"protocol": note})
                    tr.note(key="planner_protocol", text=note)
                for req in turn.actions:
                    res = propose(req)
                    observations.append({"action": req.name,
                                         **(res if res else {"refused": skipped[-1]["detail"]})})
                if turn.final is not None:
                    final = turn.final
                    final_by = turn_by["current"]
                    break
                if wrapping:
                    stop_reason = f"{wrap_up}; asked to answer from what it had, the model did not"
                    break
                if not turn.actions:
                    stop_reason = "the model returned no tool call, action or answer"
                    break
            messages.append({"role": "assistant", "content": turn.model_dump_json()})
            messages.append({"role": "user", "content": "Observations:\n" +
                             json.dumps(observations, ensure_ascii=False, default=str)})
            registry.disclose(_refs_in(observations))       # R-021 #2 / #13: shown, so citable
        else:
            stop_reason = stop_reason or f"the step limit ({spec.limits.max_steps}) was reached"

        # --- 3. answer contract -----------------------------------------------------------------
        checked: Optional[CheckedAnswer] = check_answer(
            final, registry, literals=answer_literals, owner_text=owner_text,
            question=str(question or ""), clock=clock, by_sentence=True) if final else None
        if checked is not None:
            check_action_claims(checked, action_outcomes)          # E2E F-07
        if stop_reason:
            reasons.append(f"stopped: {stop_reason}")
        if final is None and not stop_reason:
            stop_reason = "no answer was produced"
        # --- 4. the structural outcome (N-12) -----------------------------------------------------
        record.outcome = _run_outcome(final=final, checked=checked, skipped=skipped,
                                      reasons=reasons, register=register,
                                      question=str(question or ""), spec=spec, registry=registry)
        elapsed = int((time.perf_counter() - t_start) * 1000)
        tr.cost(llm_tokens=tokens_used, wall_ms=elapsed, model_calls=model_calls)
        record.steps = steps
        record.actions = action_entries
        _scrub_public(reasons, skipped)                                    # W-08: the net
        record.coverage = Coverage(searched=sorted(searched), skipped=skipped, reasons=reasons)
        audit = (f"Measured: {model_calls} model call(s), {tool_calls} tool call(s), "
                 f"{len(searched)} source(s) searched ({', '.join(sorted(searched)) or 'none'}), "
                 f"{len(registry)} pointer(s) returned, {tokens_used} token(s), {elapsed} ms.")
        md = render_markdown(heading=heading, verbatim=verbatim, checked=checked, registry=registry,
                             coverage=record.coverage.model_dump(),
                             stop_reason=stop_reason if final is None else None, audit=audit,
                             actions=[{**a.model_dump(), "action_id": action_id_for(i)}
                                      for i, a in enumerate(action_entries)]
                             + [{"name": o["name"], "automation_mode": next(
                                 (a.automation_mode for a in spec.actions
                                  if a.name == o["name"]), "declared"),
                                 "status": "refused", "detail": o["detail"]}
                                for o in action_outcomes if o["status"] == "refused"],
                             coverage_status=record.outcome.coverage_status,
                             gathered=_gathered(steps) if final is None else None)
        record.answer = AnswerBlock(markdown=md,
                                    citations=checked.citations if checked else [],
                                    citation_refs=checked.citation_refs if checked else [],
                                    unsupported_claims=checked.unsupported if checked else [],
                                    produced_by=final_by if final is not None else None,
                                    claims=[{"text": c.text, "citations": list(c.refs),
                                             "supported": c.supported, "reason": c.reason,
                                             "subject_candidate": c.subject_candidate,
                                             "requires_human_selection":
                                                 c.requires_human_selection,
                                             **({"derivations": c.derivations}
                                                if c.derivations else {})}
                                            for c in (checked.claims if checked else [])])
        status = "failed" if failed else ("completed" if final is not None else "stopped")
        return finish(status, md)
    except Exception as e:                                   # noqa: BLE001 - a run never vanishes
        # W-08: the answer says the run failed and names the reference; the text is logged
        why = public_errors.failed("the run failed", "agent run failed", e, run_id=run_id)
        reasons.append(why)
        record.steps = steps
        record.actions = action_entries
        _scrub_public(reasons, skipped)
        record.coverage = Coverage(searched=sorted(searched), skipped=skipped, reasons=reasons)
        record.outcome = RunOutcome(reason_code="no_answer", coverage_status="reduced")
        tr.unrecordable(what="the remainder of this run", because=why)
        return finish("failed", f"**{heading} (verbatim):** {_md(verbatim)}\n\n**The run "
                                f"failed:** {_md(why)}")


# ================================================================================================
# the approval API
# ================================================================================================
def _person(by: str, what: str) -> str:
    from databridge_outbox.models import NotApproved, refuse_machine_actor   # noqa: PLC0415
    try:
        return refuse_machine_actor(by, what=what)
    except NotApproved as e:
        raise ActionRefused(str(e), code="principal_not_a_person") from None


def _load(run_id: str, action_id: str, runs_dir: Optional[Path]):
    path = find_run(run_id, runs_dir)
    record = read_run(path)
    try:
        idx = index_for(action_id)
        entry = record.actions[idx]
    except (KeyError, IndexError):
        raise ActionRefused(f"run {run_id} has no action {action_id!r}",
                            code="unknown_action") from None
    return path, record, idx, entry, ActionLog(actions_path(path))


def _save(path: Path, record: RunRecord, idx: int, **update: Any) -> RunRecord:
    record.actions[idx] = record.actions[idx].model_copy(update=update)
    write_run(record, path)
    return record


#: Events that open an execution attempt, and those that close one.
_ATTEMPT_OPEN = frozenset({"approved", "retry_approved"})
_ATTEMPT_CLOSE = frozenset({"executed", "execution_failed", "rejected"})


def _attempt_state(log: ActionLog, action_id: str) -> tuple[bool, Optional[dict]]:
    """``(an attempt is open with no outcome recorded, the last execution_failed event)``."""
    open_attempt = False
    last_failure: Optional[dict] = None
    for e in log.events(action_id):
        kind = e.get("event")
        if kind in _ATTEMPT_OPEN:
            open_attempt = True
        elif kind in _ATTEMPT_CLOSE:
            open_attempt = False
            last_failure = e if kind == "execution_failed" else None
    return open_attempt, last_failure


#: Stand-ins that make `compile_for_run` advertise the runtime's own tool families when rechecking
#: an approval (the action's authority is what is rechecked; the store is not read).
_PRESENT = object()


class _PresentFederation:
    """The same stand-in for `delegate.ask` tools: an approval rechecks the ACTION's authority;
    delegated reads are not re-run, so the delegate advert is taken as present."""

    @staticmethod
    def available(_adapter_agent: str) -> bool:
        return True


def _Refused(code: str, message: str) -> ActionRefused:
    return ActionRefused(message, code=code)


def _recheck_authority(record: RunRecord, proposal: dict, action_id: str, *,
                       gateway: Optional[SourceGateway], agents_dir: Optional[Path],
                       compile_inputs: Optional[CompileInputs],
                       facts: Optional[FactsStore] = None) -> None:
    """Refuse (`ActionRefused`) unless the action is STILL authorised as proposed (R-017 t02
    finding 8): the agent still exists, both halves are byte-identical to the proposing run's
    (``spec_digest``), the action is still declared with the same tool and automation mode, and
    the declaration still compiles against what the deployment advertises today."""
    agent = str(proposal.get("agent") or "")
    try:
        spec, decl = load_agent(agent, agents_dir)
    except (MappingError, ValueError, OSError) as e:
        raise _Refused("authority_changed", f"action {action_id} was not executed: its agent {agent!r} can no "
                            f"longer be loaded ({e})") from None
    now_digest = spec_digest(spec, decl)
    if now_digest != record.spec_digest:
        raise _Refused("authority_changed", f"action {action_id} was not executed: agent {agent!r} changed since "
                            f"it was proposed ({record.spec_digest} -> {now_digest}). Reject it "
                            f"and re-run the agent under its current definition")
    spec_action = next((a for a in spec.actions if a.name == proposal.get("name")), None)
    decl_action = next((a for a in decl.actions if a.name == proposal.get("name")), None)
    if spec_action is None or decl_action is None or spec_action.tool != proposal.get("tool") \
            or (decl_action.automation_mode or "human_required") != proposal.get("automation_mode"):
        raise _Refused("authority_changed", f"action {action_id} was not executed: {proposal.get('name')!r} is no "
                            f"longer declared as it was proposed")
    decision, problems = compile_for_run(spec, decl, store=_PRESENT, retriever=_PRESENT,
                                         gateway=gateway, inputs=compile_inputs, facts=facts,
                                         federation=_PresentFederation())
    if problems or decision is None or decision.outcome is not Outcome.COMPILED:
        why = list(problems)
        if decision is not None and decision.outcome is not Outcome.COMPILED:
            why += [f"{r.code.value}: {r.detail}" for r in decision.refusals]
        raise _Refused("authority_changed", f"action {action_id} was not executed: agent {agent!r} no longer "
                            f"compiles: {'; '.join(why)}")


def _approval_schema_problems(proposal: Mapping[str, Any],
                              gateway: Optional[SourceGateway]) -> list[str]:
    """The proposal's arguments against the tool's input schema AS ADVERTISED NOW (ACT): a schema
    that changed since the proposal, or a hand-edited log, is refused before anything is sent. No
    gateway / no advert -> `_execute` reports it; nothing to check here."""
    if gateway is None:
        return []
    try:
        p = parse_tool(str(proposal.get("tool") or ""))
        t = gateway.advertise(p["connection"]).tool(p["tool"] or "")
    except Exception:                                        # noqa: BLE001 - _execute reports
        return []
    return args_schema_problems(proposal.get("args") or {}, getattr(t, "input_schema", None)) \
        if t is not None else []


def is_issued_principal(by: Any) -> bool:
    """Whether ``by`` is an `Approver` a registered `Authenticator` issued (seal verified). For
    other packages that accept this runtime's principals (e.g. adapter_agents' `principal_of`)."""
    if type(by) is not Approver:
        return False
    auth = _AUTHENTICATORS.get(by.authenticated_by)
    return auth is not None and auth.verifies(by)


_verified = is_issued_principal


def _log_refusal(run_id: str, action_id: str, decision: str, by: Any, err: ActionRefused,
                 runs_dir: Optional[Path]) -> None:
    """Append ``decision_refused`` to the run's action log (ACT: the Activity timeline reads it).
    Append-only, like every other event. A principal that did not verify is recorded as a CLAIM
    (``claimed_by``), never as ``by``. Nothing is written when the run itself does not exist."""
    try:
        path = find_run(run_id, runs_dir)
    except (FileNotFoundError, OSError, ValueError):
        return
    ev: dict = {"event": "decision_refused", "decision": decision, "action_id": action_id,
                "reason_code": err.code, "detail": str(err)[:1000], "at": iso(utcnow())}
    if _verified(by):
        ev["by"] = by.principal_id.strip()
        ev.update(_decided_by(by))
    else:
        claimed = by if isinstance(by, str) else getattr(by, "principal_id", None)
        ev["by"] = None
        ev["claimed_by"] = None if claimed is None else str(claimed)[:200]
    try:
        ActionLog(actions_path(path)).append(ev)
    except OSError:                                          # the refusal still stands
        pass


def _logs_refusals(decision: str):
    """Every refused approve / reject / reverse is logged before the refusal is raised. A write
    that failed AFTER approval is already logged as ``execution_failed`` and is not repeated."""
    def wrap(fn: Callable[..., RunRecord]) -> Callable[..., RunRecord]:
        @functools.wraps(fn)
        def decided(run_id: str, action_id: str, by: Any, **kw: Any) -> RunRecord:
            try:
                return fn(run_id, action_id, by, **kw)
            except ActionRefused as e:
                if e.code != "execution_failed":
                    _log_refusal(run_id, action_id, decision, by, e, kw.get("runs_dir"))
                raise
        return decided
    return wrap


@_logs_refusals("approve")
def approve(run_id: str, action_id: str, by: Any, *, gateway: Optional[SourceGateway],
            runs_dir: Optional[Path] = None, capture_dir: Optional[Path] = None,
            agents_dir: Optional[Path] = None,
            compile_inputs: Optional[CompileInputs] = None,
            facts: Optional[FactsStore] = None, screen: Any = None) -> RunRecord:
    """An authenticated person approves a proposed action; it is executed now, once.

    ``by`` is an `Approver` issued by a registered `Authenticator` — a bare name, a hand-built
    Approver or any other object is refused (R-021 #5). In order: the principal is checked; the action's CURRENT authority is rechecked
    (`_recheck_authority`); under the run's lock the approval is recorded as an open attempt —
    a second approval arriving now finds that attempt open and is refused; the write is sent
    (outside the lock); under the lock again its outcome is recorded. Allowed from ``proposed``,
    and from ``approved`` to retry only after a failure the source REPORTED — never after an
    attempt whose outcome is unknown.
    """
    who = _principal(by, "action approval")
    path = find_run(run_id, runs_dir)
    lock = RunLock(path)
    try:
        with lock:
            path, record, idx, entry, log = _load(run_id, action_id, runs_dir)
            proposal = _claimable(record, entry, log, action_id)
    except RunLocked as e:
        raise ActionRefused(str(e), code="run_locked") from None
    gate = _approval_gate_problems(proposal)
    if gate:
        raise _Refused("action_not_grounded",
                       f"action {action_id} cannot be approved: {'; '.join(gate)}. A value with "
                       f"no source pointer is never written (CLAUDE.md hard constraint 2); reject "
                       f"it and re-run the agent")
    _recheck_authority(record, proposal, action_id, gateway=gateway, agents_dir=agents_dir,
                       compile_inputs=compile_inputs, facts=facts)
    schema = _approval_schema_problems(proposal, gateway)
    if schema:
        raise _Refused("action_args_do_not_match_tool",
                       f"action {action_id} was not executed: {proposal.get('tool')} would not "
                       f"accept its arguments now: {'; '.join(schema)}")
    try:
        with lock:                               # claim: re-read, re-check, open the attempt
            path, record, idx, entry, log = _load(run_id, action_id, runs_dir)
            if _approval_gate_problems(_claimable(record, entry, log, action_id)):
                raise _Refused("action_not_grounded", f"action {action_id} cannot be approved: "
                                                      f"its proposal is not grounded")
            first = entry.status == "proposed"
            log.append({"event": "approved" if first else "retry_approved",
                        "action_id": action_id, "by": who, **_decided_by(by),
                        "idempotency_key": f"{run_id}/{action_id}", "at": iso(utcnow())})
            if first:
                record = _save(path, record, idx, status="approved", by=who)
    except RunLocked as e:
        raise ActionRefused(str(e), code="run_locked") from None
    ex = _execute(proposal["tool"], proposal.get("args") or {}, gateway, capture_dir,
                  screen=screen)
    with RunLock(path, timeout_s=60.0):          # record the outcome; never skipped
        path, record, idx, _entry, log = _load(run_id, action_id, runs_dir)
        if not ex.ok:
            log.append(_failed_event(action_id, ex, who))
            if ex.outcome == "unknown":
                raise _Refused("execution_failed",
                               f"approved by {who}, but {ex.detail}. It will not be retried "
                               f"automatically: check the source, then reject the action if the "
                               f"write did not happen")
            raise _Refused("execution_failed",
                           f"approved by {who}, but the write failed: {ex.detail}. It stays "
                           f"approved; approve again to retry, or reject it")
        log.append(_executed_event(action_id, ex, who))
        return _save(path, record, idx, status="executed", by=who)


def _claimable(record: RunRecord, entry: ActionEntry, log: ActionLog, action_id: str) -> dict:
    """The proposal, if this action may be approved now; else `ActionRefused`."""
    if entry.status not in ("proposed", "approved"):
        raise _Refused("not_claimable", f"action {action_id} is {entry.status}; only a proposed action can be "
                            f"approved")
    proposal = log.proposal(action_id)
    if proposal is None:
        raise _Refused("not_claimable", f"action {action_id} has no recorded proposal; nothing to execute")
    open_attempt, last_failure = _attempt_state(log, action_id)
    if open_attempt:
        raise _Refused("not_claimable", f"action {action_id} is already being executed by another approval, "
                            f"or an earlier attempt ended without a recorded outcome; it will not "
                            f"be sent again. Check the source and the action history")
    if last_failure is not None and last_failure.get("outcome") == "unknown":
        raise _Refused("not_claimable", f"action {action_id}: the last attempt's outcome is unknown "
                            f"({last_failure.get('detail')}); it will not be retried. Check the "
                            f"source, then reject the action if the write did not happen")
    if entry.status == "approved" and last_failure is None and \
            not any(e.get("event") == "execution_failed" for e in log.events(action_id)):
        raise _Refused("not_claimable", f"action {action_id} is approved with no failed attempt to retry")
    return proposal


@_logs_refusals("reject")
def reject(run_id: str, action_id: str, by: Any, *, reason: str = "",
           runs_dir: Optional[Path] = None) -> RunRecord:
    who = _principal(by, "action rejection")
    path = find_run(run_id, runs_dir)
    try:
        with RunLock(path):
            path, record, idx, entry, log = _load(run_id, action_id, runs_dir)
            if entry.status not in ("proposed", "approved"):
                raise _Refused("not_claimable", f"action {action_id} is {entry.status}; it can "
                                                f"no longer be rejected")
            if _attempt_state(log, action_id)[0]:
                raise _Refused("attempt_in_progress", f"action {action_id} is being executed "
                                                      f"right now; it cannot be rejected until "
                                                      f"its outcome is recorded")
            log.append({"event": "rejected", "action_id": action_id, "by": who,
                        **_decided_by(by), "reason": reason, "at": iso(utcnow())})
            return _save(path, record, idx, status="rejected", by=who)
    except RunLocked as e:
        raise ActionRefused(str(e), code="run_locked") from None


@_logs_refusals("reverse")
def reverse(run_id: str, action_id: str, by: Any, *, note: str = "",
            runs_dir: Optional[Path] = None) -> RunRecord:
    """Record that a person carried out the action's declared reversal. The runtime does not
    compute a compensating write on its own; the declared reversal says what a person does."""
    who = _principal(by, "action reversal")
    path = find_run(run_id, runs_dir)
    try:
        with RunLock(path):
            path, record, idx, entry, log = _load(run_id, action_id, runs_dir)
            if entry.status != "executed":
                raise _Refused("not_reversible", f"action {action_id} is {entry.status}; only an "
                                                 f"executed action can be reversed")
            proposal = log.proposal(action_id) or {}
            log.append({"event": "reversed", "action_id": action_id, "by": who,
                        **_decided_by(by), "note": note,
                        "declared_reversal": proposal.get("reversal"), "at": iso(utcnow())})
            return _save(path, record, idx, status="reversed", by=who)
    except RunLocked as e:
        raise ActionRefused(str(e), code="run_locked") from None


def pending_actions(*, agent: Optional[str] = None,
                    runs_dir: Optional[Path] = None) -> list[dict]:
    """Every action still awaiting a person — what an approval inbox shows (with the arguments a
    person must see before approving)."""
    out = []
    for rec_path, log in iter_action_logs(agent=agent, runs_dir=runs_dir):
        if not rec_path.is_file():
            continue
        record = read_run(rec_path)
        for i, entry in enumerate(record.actions):
            if entry.status not in ("proposed", "approved"):
                continue
            aid = action_id_for(i)
            prop = log.proposal(aid) or {}
            out.append({"run_id": record.run_id, "action_id": aid, "agent": prop.get("agent"),
                        "name": entry.name, "automation_mode": entry.automation_mode,
                        "status": entry.status, "tool": prop.get("tool"),
                        "args": prop.get("args"), "derived_from": entry.pointers,
                        "reversal": prop.get("reversal"), "proposed_at": prop.get("at"),
                        "ungrounded_values": prop.get("ungrounded_values") or [],
                        "history": log.events(aid)})
    return out


__all__ = ["AGENT_TASK_CLASS", "PLANNER_PROMPT_VERSION", "ActionRefused", "Approver",
           "Authenticator", "register_authenticator", "is_issued_principal",
           "CompileInputs", "compile_for_run", "run_agent", "args_schema_problems",
           "approve", "reject", "reverse", "pending_actions"]
