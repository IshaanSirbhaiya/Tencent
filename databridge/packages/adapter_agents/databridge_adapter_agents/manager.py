"""The adapter-agent manager: sync, health, plan refresh, ontology proposals.

`sync(agent_id)` runs, for each managed connection whose crawl plan is approved, one ingest into
the shared store — **each connection isolated**: one failing (in ingest, in reading its report, or
in writing its event) does not stop the next, and a failure to write the run record still returns
the summary with the error on it. A connection whose plan is only proposed (in the spec or on disk)
is skipped and the reason is written down (P5: declared scope, approved plan — never discovered).

**Success is established, never assumed.** A connection is `completed` only when the service's
own run history (`list_runs`, i.e. the §5.2 `report.json` on disk for `McpIngestService`) holds
the run and says it succeeded. A run the history does not hold, or whose status is missing, `empty`,
`unverified`, `partial` or unfamiliar, is `unverified` — never success, never freshness evidence.

**Events (§5.3, §12 ownership rule).** `mcp_ingest` emits `run_completed` / `run_failed` for every
run it starts. This manager emits a `run_failed` ONLY when a run failed before ingestion could
emit — i.e. `run_ingest` raised and left no report for the attempt. Events carry
`attrs.emitted_by`. The whole sync writes a §5.7 run record whose steps carry §5.8 `produced_by`.

**Adaptation (§12, 2026-10-01).** Before each connection runs, the agent checks the source's
SHAPE against the approved baseline (`adapt.AdaptationEngine`): additive drift adapts as a new
plan / mapping / connection VERSION under `adapt.policy: auto_additive`; breaking drift holds only
the affected table / step and files an approval request (`pending_adaptations`,
`approve_adaptation`, `reject_adaptation`). A connection held as a whole is skipped with the reason.
`on_list_changed(connection_id)` re-checks when an MCP server says its tool list changed.

`health(agent_id)` is computed from the service's run history (report timestamps decide freshness)
plus this manager's own records only for attempts that never produced a report. A connection with
no successful run is stale — "never" is not fresh (P7).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional, Union

from databridge_workflow_spec import AdapterAgentSpec

from . import events as ev
from . import paths
from .adapt import AdaptationEngine, AdaptationRefused, Policy
from .delegations import DelegationLog, DelegationRefused, scope_digest, spec_scope
from .ingest_service import COUNT_KEYS, IngestRunReport, IngestService, normalize_report, parse_ts
from .proposals import (ActiveVocabularyView, ProposalLog, ProposalPolicy, VocabularyView,
                        file_proposals)
from .registry import AdapterAgentRegistry, RegistryError, spec_digest
from .trace import RecordingHarness, models_used, produced_by

RUN_KIND = "adapter_agent"
_LOCK_STALE_S = 6 * 3600
#: A report timestamp this far ahead of the clock is a clock/timestamp fault, not freshness.
FUTURE_SKEW = dt.timedelta(minutes=5)
_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()

#: Codex R-027 AAX f1 #6: a crawl plan is approved FOR a revision of its connection (the gateway's
#: binding notes). This check says why `(service, connection)`'s approved plan may not run against
#: the connection as it is now, or None. The gateway installs its shared check
#: (`sources_routes.plan_stale_reason`) with `set_plan_stale_check`; a manager may carry its own
#: (`plan_stale_check=`). Applied at activate, resume and EVERY sync (`_plan_approved`).
_PLAN_STALE_CHECK: Optional[Callable[[Any, str], Optional[str]]] = None


def set_plan_stale_check(fn: Optional[Callable[[Any, str], Optional[str]]]) -> None:
    """Install (or, with None, remove) the process-wide plan staleness check."""
    global _PLAN_STALE_CHECK
    _PLAN_STALE_CHECK = fn


@dataclasses.dataclass
class ConnectionResult:
    connection: str
    status: str                       # completed | failed | unverified | skipped
    reason: Optional[str] = None      # why skipped / failed / unverified, in plain words
    ingest_run_id: Optional[str] = None
    started: Optional[str] = None
    ended: Optional[str] = None
    ms: Optional[int] = None
    counts: dict[str, Optional[int]] = dataclasses.field(default_factory=dict)
    exclusions_by_rule: dict[str, dict[str, int]] = dataclasses.field(default_factory=dict)
    event_id: Optional[str] = None
    event_error: Optional[str] = None           # the §5.3 write failed; the outcome still stands
    report_status: Optional[str] = None         # the word the ingest report used, verbatim
    event_owner: Optional[str] = None           # "mcp_ingest" | "adapter_agent" | None
    produced_by: Optional[dict[str, Any]] = None
    adaptation: dict[str, Any] = dataclasses.field(default_factory=dict)   # shape check, §12

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _sum_or_unknown(values: list[Optional[int]]) -> Optional[int]:
    """A total over parts one of which is unknown is unknown — never the known subtotal."""
    if not values or any(v is None for v in values):
        return None
    return sum(values)                                                # type: ignore[arg-type]


@dataclasses.dataclass
class SyncSummary:
    agent_id: str
    run_id: str
    #: completed | completed_with_warnings | partial | failed | unverified | nothing_to_run |
    #: refused | busy
    status: str
    trigger: dict[str, Any]
    started: str
    ended: str
    connections: list[ConnectionResult]
    reason: Optional[str] = None
    proposals: dict[str, Any] = dataclasses.field(default_factory=dict)
    record_path: Optional[str] = None
    warnings: list[str] = dataclasses.field(default_factory=list)
    record_error: Optional[str] = None
    #: FIN-03: the screen policy the sync ran under (`mcp_ingest.screen.screen_policy`):
    #: `pack` (a pack's screen.yaml) or `engine_default` (the bound pack declares none)
    screen_policy: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def ran(self) -> list[str]:
        return [c.connection for c in self.connections if c.status != "skipped"]

    @property
    def skipped(self) -> list[str]:
        return [c.connection for c in self.connections if c.status == "skipped"]

    def totals(self) -> dict[str, Optional[int]]:
        done = [c for c in self.connections if c.status == "completed"]
        return {k: _sum_or_unknown([c.counts.get(k) for c in done]) for k in COUNT_KEYS}

    def as_dict(self) -> dict[str, Any]:
        return {"agent_id": self.agent_id, "run_id": self.run_id, "status": self.status,
                "reason": self.reason, "trigger": self.trigger, "started": self.started,
                "ended": self.ended, "ran": self.ran, "skipped": self.skipped,
                "totals": self.totals(),
                "connections": [c.as_dict() for c in self.connections],
                "proposals": {"filed": [p["proposal_id"] for p in self.proposals.get("filed", [])],
                              "already_filed": self.proposals.get("already_filed", []),
                              "below_threshold": self.proposals.get("below_threshold", []),
                              "vocabulary_problem": self.proposals.get("vocabulary_problem"),
                              "ungrounded": self.proposals.get("ungrounded"),
                              "occurrences_total": self.proposals.get("occurrences_total"),
                              "error": self.proposals.get("error")},
                "warnings": list(self.warnings), "record_error": self.record_error,
                "record_path": self.record_path, "screen_policy": dict(self.screen_policy)}


def _iso(t: Optional[dt.datetime]) -> Optional[str]:
    return ev.now_iso(t) if t else None


class _SpecView:
    """A history line's spec, as `Policy.of` reads it (its `adapt` block only)."""

    def __init__(self, adapt: Any) -> None:
        self.adapt = adapt


class AdapterAgentManager:
    """The Python API the gateway wraps. Everything is injectable; nothing reaches the network."""

    def __init__(self, *, service: IngestService,
                 registry: Optional[AdapterAgentRegistry] = None,
                 store: Any = None, store_factory: Optional[Callable[[], Any]] = None,
                 harness: Any = None,
                 runs_root: Optional[Path] = None, events_path: Optional[Path] = None,
                 proposal_policy: Optional[ProposalPolicy] = None,
                 proposal_log: Optional[ProposalLog] = None,
                 vocabulary: Optional[VocabularyView] = None,
                 clock: Optional[Callable[[], dt.datetime]] = None,
                 progress_cb: Optional[Callable[[dict], None]] = None,
                 adaptation: Optional[AdaptationEngine] = None,
                 plan_stale_check: Optional[Callable[[Any, str], Optional[str]]] = None) -> None:
        self.service = service
        self.plan_stale_check = plan_stale_check
        self.registry = registry or AdapterAgentRegistry()
        self._store = store
        self._store_factory = store_factory
        self.harness = harness
        self._runs_root = runs_root
        self._events_path = events_path
        self.proposal_policy = proposal_policy or ProposalPolicy()
        self.proposal_log = proposal_log or ProposalLog(
            self.registry.root / "_proposals" / "ontology_proposals.jsonl")
        self.vocabulary = vocabulary or ActiveVocabularyView()
        self._clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self.progress_cb = progress_cb
        #: AA3: sealed delegation records (append-only, hash-chained)
        self.delegations = DelegationLog(lambda: self.registry.root / "_delegations")
        self.adaptation = adaptation or AdaptationEngine(
            root_fn=lambda: self.registry.root / "_adapt", service=service,
            harness_fn=lambda: self.harness, clock=self._clock, digest_fn=spec_digest,
            seal_fn=self.delegation_status,
            seal_log_fn=lambda spec: self.delegations.path(spec.id))
        #: grounded occurrences of unmapped tables seen this sync (ontology-proposal material)
        self._extra_occurrences: list[dict[str, Any]] = []

    def delegator(self, spec: AdapterAgentSpec) -> Optional[str]:
        """The principal whose SEALED delegation is in force for this agent (`delegation_status`),
        or None - then every change is held for a person."""
        seal, _ = self.delegation_status(spec)
        return seal["principal_id"] if seal is not None else None

    def scope_digest(self, spec: AdapterAgentSpec) -> str:
        """The declared scope a delegation is sealed to: the spec's connections, extract pack and
        screen pack, plus each managed connection's declared `scope` as mcp_ingest holds it.
        Raises when a connection's declaration cannot be read (no delegation is then possible)."""
        scopes: dict[str, Any] = {}
        for c in spec.connections:
            cfg = self.service.read_config(c, "connection")
            if cfg is None:
                raise DelegationRefused(f"connection {c!r} has no declaration to bind a scope to")
            scopes[c] = cfg.get("scope")
        return scope_digest(spec.model_dump(mode="json"), scopes)

    def delegation_status(self, spec: AdapterAgentSpec
                          ) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        """(the sealed delegation record in force, None) or (None, why none is), AA3. In force
        only when the log's chain verifies and ends in a seal, the agent is active under
        `auto_additive`, its policy digest and declared-scope digest are the sealed ones, and the
        registry history still holds the activation line the seal names with no later line that
        changed the policy or the scope. Never raises."""
        from .adapt import _MACHINE, DELEGATED_POLICY, policy_digest

        try:
            pol = Policy.of(spec)
            if pol.policy != DELEGATED_POLICY:
                return None, f"adapt.policy {pol.policy!r} delegates nothing"
            if spec.status != "active":
                return None, f"the agent is {spec.status!r}, not active"
            seal, why = self.delegations.in_force(spec.id)
            if seal is None:
                return None, why
            pid = str(seal.get("principal_id") or "").strip()
            if not pid or pid.lower().startswith(_MACHINE):
                return None, "the sealed record names no person"
            if seal.get("policy_digest") != policy_digest(pol):
                return None, (f"the adapt policy changed after {pid} activated the agent at "
                              f"{seal.get('at')}; a person must re-activate it (pause, then "
                              f"resume)")
            try:
                now_scope = self.scope_digest(spec)
            except Exception as exc:                                  # noqa: BLE001
                return None, f"the declared scope could not be read ({type(exc).__name__}: {exc})"
            if seal.get("scope_digest") != now_scope:
                return None, (f"the declared scope changed after {pid} activated the agent at "
                              f"{seal.get('at')}; a person must re-activate it (pause, then "
                              f"resume)")
            history = self.registry.history(spec.id)
            n = int(seal.get("history_n") or 0)
            line = history[n - 1] if 0 < n <= len(history) else None
            want = seal.get("history_line") or {}
            if line is None or any(line.get(k) != want.get(k) for k in
                                   ("ts", "action", "by", "spec_digest", "status")):
                return None, ("the registry history no longer holds the activation this "
                              "delegation was sealed beside; a person must re-activate the agent")
            sealed_scope = spec_scope(line.get("spec") or {})
            sealed_pol = policy_digest(Policy.of(_SpecView((line.get("spec") or {}).get("adapt"))))
            for later in history[n:]:
                then = later.get("spec") or {}
                if policy_digest(Policy.of(_SpecView(then.get("adapt")))) != sealed_pol or \
                        spec_scope(then) != sealed_scope:
                    return None, (f"the policy or the declared scope was changed after the "
                                  f"activation ({later.get('action')} at {later.get('ts')}); a "
                                  f"change that was changed back is still a change: a person "
                                  f"must re-activate the agent")
            return seal, None
        except Exception as exc:                                      # noqa: BLE001
            return None, f"the delegation could not be verified ({type(exc).__name__}: {exc})"

    def delegation_seal(self, spec: AdapterAgentSpec) -> Optional[dict[str, Any]]:
        return self.delegation_status(spec)[0]

    @staticmethod
    def _actor(by: Any, what: str) -> tuple[str, Optional[dict[str, Any]]]:
        """(the name the registry records, the authenticated principal or None). A typed name is
        recorded as before but authenticates nothing, so it seals no delegation. An object that is
        not a principal the authentication layer minted is refused outright."""
        from .adapt import principal_of

        if by is None or isinstance(by, str):
            return (by or ""), None
        try:
            who = principal_of(by, what)
        except AdaptationRefused as exc:
            raise RegistryError(str(exc)) from None
        return who["principal_id"], who

    def _seal(self, spec: AdapterAgentSpec, who: Optional[dict[str, Any]]) -> None:
        """Mint the sealed delegation for an activation by an authenticated person, under
        `auto_additive` only. Nothing is sealed for a name, a machine or another policy. A seal
        that cannot be minted (an unreadable connection declaration) leaves the agent active with
        NO delegation: every change is then held for a person."""
        from .adapt import _MACHINE, DELEGATED_POLICY, policy_digest

        if who is None or spec.status != "active":
            return
        pol = Policy.of(spec)
        if pol.policy != DELEGATED_POLICY or who["principal_id"].lower().startswith(_MACHINE):
            return
        history = self.registry.history(spec.id)
        try:
            scope = self.scope_digest(spec)
        except Exception:                                             # noqa: BLE001
            return
        self.delegations.seal(agent_id=spec.id, who=who, policy=pol.policy,
                              policy_digest=policy_digest(pol), scope_digest=scope,
                              spec_digest=spec_digest(spec), history_line=history[-1],
                              history_n=len(history))

    # --- registry pass-throughs (the gateway's create/update/pause/resume/delete) ------------
    def list(self, **kw) -> list[AdapterAgentSpec]:
        return self.registry.list(**kw)

    def get(self, agent_id: str) -> AdapterAgentSpec:
        return self.registry.get(agent_id)

    def create(self, source: Union[AdapterAgentSpec, dict, str, Path], *, by: Any = ""):
        """`by`: the authenticated principal object (an active `auto_additive` agent then gets a
        sealed delegation, AA3), or a name (recorded; it delegates nothing)."""
        name, who = self._actor(by, "create adapter agent")
        spec = self.registry.create(source, by=name)
        self._seal(spec, who)
        return spec

    def update(self, agent_id: str, changes: dict, *, by: Any = ""):
        """A change to the adapt policy or the declared scope REVOKES the sealed delegation in
        the append-only log (AA3); a person must re-activate the agent (pause, then resume)."""
        from .adapt import policy_digest

        name, _ = self._actor(by, "update adapter agent")
        before = self.registry.get(agent_id).model_dump(mode="json")
        spec = self.registry.update(agent_id, changes, by=name)
        after = spec.model_dump(mode="json")
        changed = [what for what, x, y in (
            ("adapt policy", policy_digest(Policy.of(_SpecView(before.get("adapt")))),
             policy_digest(Policy.of(_SpecView(after.get("adapt"))))),
            ("declared scope", spec_scope(before), spec_scope(after))) if x != y]
        if changed:
            self.delegations.revoke(agent_id, by=name, reason=f"{' and '.join(changed)} changed "
                                                              f"by {name or 'an unnamed caller'}")
        return spec

    def pause(self, agent_id: str, *, by: Any = ""):
        name, _ = self._actor(by, "pause adapter agent")
        return self.registry.pause(agent_id, by=name)

    def _person_or_refuse(self, spec: AdapterAgentSpec, by: Any, what: str
                          ) -> tuple[str, Optional[dict[str, Any]]]:
        """Codex R-027 AAX f2 #2 - an INVARIANT of activation and resumption: a machine actor
        (an agent, a workflow, the system; by name or by principal) never activates or resumes an
        agent, and a SETUP-owned agent is activated or resumed only by an authenticated person."""
        from .adapt import _MACHINE

        name, who = self._actor(by, what)
        if str(name or "").strip().lower().startswith(_MACHINE):
            raise RegistryError(f"{what} REFUSED: {name!r} is a machine actor; only a person "
                                f"activates or resumes an adapter agent")
        if who is None and self._setup_source(spec) is not None:
            raise RegistryError(f"{what} REFUSED: {spec.id!r} was set up for a source; only an "
                                f"authenticated person (not a typed name) activates it")
        return name, who

    def _setup_source(self, spec: AdapterAgentSpec) -> Optional[str]:
        try:
            from .setup import setup_source_of

            return setup_source_of(self.registry.root, spec.id)
        except Exception:                                             # noqa: BLE001
            return "\x00unreadable-setup-mark"

    def _setup_problems(self, spec: AdapterAgentSpec, *, activating: bool) -> list[str]:
        """Codex R-027 AAX f2 #1: a SETUP-owned agent is activated only under a person's hand-off
        authorisation that covers the setup exactly as it is now, and runs (resume, every sync)
        only with that authorisation on record - and, once active, only with an in-force sealed
        delegation (#2). Enforced here, so the generic routes cannot bypass Set up."""
        src = self._setup_source(spec)
        if src is None:
            return []
        try:
            from .setup import authorization_problems

            stale = None
            check = getattr(self, "plan_stale_check", None) or _PLAN_STALE_CHECK
            if check is not None and activating:
                stale = check(self.service, src)
            out = authorization_problems(self.registry.root, src, spec.id, plan_stale=stale,
                                         activating=activating)
        except Exception as exc:                                      # noqa: BLE001
            return [f"the setup hand-off of {spec.id!r} could not be verified "
                    f"({type(exc).__name__}: {exc})"]
        if not out and not activating and spec.status == "active":
            seal, why = self.delegation_status(spec)
            if seal is None:
                out.append(f"{spec.id!r} was set up for {src!r} and runs only under a sealed "
                           f"delegation in force: {why}")
        return [f"setup hand-off: {p}" for p in out]

    def authorize_setup_handoff(self, agent_id: str, *, by: Any, binding: dict) -> dict:
        """Record a PERSON's authorisation to hand `agent_id` (set up for a source) off to the
        build, bound to `binding` (`setup.gate_binding`). The authenticated principal is required
        and a machine is refused; `activate` then verifies it against the setup as it is."""
        from .adapt import _MACHINE, principal_of
        from .setup import record_authorization

        spec = self.registry.get(agent_id)
        src = self._setup_source(spec)
        if src is None:
            raise RegistryError(f"{agent_id!r} was not set up for a source")
        try:
            who = principal_of(by, "authorise a setup hand-off")
        except AdaptationRefused as exc:
            raise RegistryError(str(exc)) from None
        if who["principal_id"].lower().startswith(_MACHINE):
            raise RegistryError(f"authorise a setup hand-off REFUSED: {who['principal_id']!r} is "
                                f"a machine actor")
        # R-028 TIDY #1: the FULL actor object - a dev-token principal stays marked `dev_only`
        # (the same field `plans.require_authenticated_approver` adds); `record_authorization`
        # runs it through `decision_ledger.require_person`.
        who["dev_only"] = bool(getattr(by, "dev_only", False))
        return record_authorization(self.registry.root, src, agent_id, who=who, binding=binding)

    def activate(self, agent_id: str, *, by: Any = ""):
        """A DRAFT becomes active only when every connection's crawl plan is approved (the spec's
        record AND the plan on disk, P5), by a PERSON (f2 #2), and - for an agent set up for a
        source - under that person's hand-off authorisation (f2 #1). Refused otherwise."""
        spec = self.registry.get(agent_id)
        if spec.status != "draft":
            raise RegistryError(f"adapter agent {agent_id!r} is {spec.status!r}; only a draft is "
                                f"activated")
        name, who = self._person_or_refuse(spec, by, "activate adapter agent")
        blocked = self.agent_gate(spec, activating=True)
        if blocked:
            raise RegistryError(f"adapter agent {agent_id!r} cannot be activated: "
                                + "; ".join(blocked))
        spec = self.registry.activate(agent_id, by=name)
        self._seal(spec, who)
        return spec

    def agent_gate(self, spec: AdapterAgentSpec, *, activating: bool = False) -> list[str]:
        """Why this agent may NOT run at all (empty: it may). EVERY crawl plan it references must
        be human-approved — the spec's record and the plan on disk (P5) — and its `adapt` policy
        must be one the engine knows. Enforced here, not only in the UI (a UI gate is bypassable):
        resume, activate and every sync, manual or scheduled, check it (Codex round 3, R-019)."""
        reasons = [str(why) for c in spec.connections
                   for ok, why in [self._plan_approved(spec, c)] if not ok]
        why = self.screen_pack_refusal(spec)
        if why:
            reasons.append(why)
        reasons.extend(self._setup_problems(spec, activating=activating))
        try:
            pol = Policy.of(spec)
            if pol.policy not in ("auto_additive", "ask_all", "off"):
                reasons.append(f"adapt.policy {pol.policy!r} is not a policy the adapter agent "
                               f"knows (auto_additive | ask_all | off)")
        except Exception as exc:                                      # noqa: BLE001
            reasons.append(f"the adapt policy could not be read ({type(exc).__name__}: {exc})")
        return reasons

    @staticmethod
    def screen_pack_refusal(spec: AdapterAgentSpec) -> Optional[str]:
        """R-021 D2: a spec that names its screening pack (``screen: {pack: <p>}``) runs only
        where the deployment's RESOLVED screen (`mcp_ingest.screen.load_screen`) comes from that
        pack's ``screen.yaml``. The contract has no I/O, so this is checked here, at activate,
        resume and every sync. A spec that names no pack is screened by whatever the deployment
        resolves, as before. A screen that cannot be loaded refuses (fail closed).

        FIN-03: a pack that ships NO ``screen.yaml`` is screened by the engine default policy
        (`defaults/screen.yaml`: credential gate, prompt-injection hold, PII masks). Such a spec
        runs where the deployment resolves that same default — the sync reports it
        (`SyncSummary.screen_policy`). It still refuses where another pack's screen is in force,
        and for a pack that does not exist."""
        want = (getattr(spec, "screen", None) or {}).get("pack")
        if not want:
            return None
        try:
            from databridge_mcp_ingest import paths as _ipaths  # noqa: PLC0415
            from databridge_mcp_ingest.screen import (  # noqa: PLC0415
                is_engine_default, load_screen, pack_screen_file, screen_pack_of)

            cfg = load_screen()
            got = screen_pack_of(cfg)
            declared = pack_screen_file(want)
            # a pack is ONE plain directory name carrying an ontology (never a path)
            exists = (isinstance(want, str) and want.replace("_", "").replace("-", "").isalnum()
                      and (_ipaths.packs_dir() / want / "ontology.yaml").is_file())
            engine_default = is_engine_default(cfg)
        except Exception as exc:                                      # noqa: BLE001
            return (f"spec.screen.pack is {want!r} but the deployment's screen could not be "
                    f"resolved ({type(exc).__name__}: {exc})")
        if declared is None:
            if not exists:
                return (f"spec.screen.pack is {want!r} but no such pack exists; change the spec")
            if got is None and engine_default:
                return None                    # the pack's policy IS the engine default
            return (f"spec.screen.pack is {want!r}, which declares no screen.yaml (it is "
                    f"screened by the engine default policy), but this deployment screens with "
                    f"{'packs/' + got + '/screen.yaml' if got else cfg.source}; change the spec "
                    f"or the deployment's packs")
        if got != want:
            return (f"spec.screen.pack is {want!r} but this deployment screens with "
                    f"{got + ' (packs/' + got + '/screen.yaml)' if got else 'the bundled defaults'}"
                    f" ({cfg.source}); enable the pack, or change the spec")
        return None

    @staticmethod
    def _screen_policy() -> dict[str, Any]:
        """FIN-03: what the sync reports about its screen — `engine_default` when no enabled pack
        declares one. Never raises: a screen that cannot be resolved is reported as such (the
        gate above has already refused a spec that names a pack)."""
        try:
            from databridge_mcp_ingest.screen import load_screen, screen_policy  # noqa: PLC0415

            return screen_policy(load_screen())
        except Exception as exc:                                      # noqa: BLE001
            return {"policy": "unresolved", "error": f"{type(exc).__name__}: {exc}"}

    def resume(self, agent_id: str, *, by: Any = ""):
        """PAUSED -> active. By an authenticated principal under `auto_additive`, this is the
        re-activation that seals a fresh delegation (AA3)."""
        spec = self.registry.get(agent_id)
        name, who = self._person_or_refuse(spec, by, "resume adapter agent")
        blocked = self.agent_gate(spec)
        if blocked:
            raise RegistryError(f"adapter agent {agent_id!r} cannot be resumed: "
                                + "; ".join(blocked))
        spec = self.registry.resume(agent_id, by=name)
        self._seal(spec, who)
        return spec

    def delete(self, agent_id: str, *, by: Any = "", reason: str = ""):
        name, _ = self._actor(by, "delete adapter agent")
        spec = self.registry.delete(agent_id, by=name, reason=reason)
        self.delegations.revoke(agent_id, by=name, reason="the agent was deleted")
        return spec

    # --- locations ----------------------------------------------------------------------------
    @property
    def runs_root(self) -> Path:
        return self._runs_root or paths.runs_dir()

    def _agent_runs_dir(self, agent_id: str) -> Path:
        return self.runs_root / RUN_KIND / agent_id

    @property
    def store(self) -> Any:
        if self._store is None and self._store_factory is not None:
            self._store = self._store_factory()
        return self._store

    # --- plan gate ------------------------------------------------------------------------
    def plan_gate(self, spec: AdapterAgentSpec, connection: str) -> tuple[bool, Optional[str]]:
        """Runnable only when BOTH the spec and the crawl plan on disk say approved, AND no shape
        drift holds the connection as a whole. Anything else is a hold, and the reason says
        which."""
        ok, why = self._plan_approved(spec, connection)
        if not ok:
            return ok, why
        hold = self._connection_hold(spec, connection)
        if hold:
            return False, f"skipped {connection!r}: {hold}"
        return True, None

    def _connection_hold(self, spec: AdapterAgentSpec, connection: str) -> Optional[str]:
        try:
            return self.adaptation.connection_hold(spec.id, connection)
        except Exception as exc:                                      # noqa: BLE001
            return f"held: the adaptation state could not be read ({type(exc).__name__}: {exc})"

    def _plan_approved(self, spec: AdapterAgentSpec, connection: str
                       ) -> tuple[bool, Optional[str]]:
        declared = spec.plans.get(connection)
        if declared != "approved":
            what = "no plan status" if declared is None else f"plan status {declared!r}"
            return False, (f"skipped {connection!r}: the adapter agent records {what}; a crawl plan "
                           f"runs only once it is approved (P5)")
        try:
            on_disk = self.service.plan_status(connection)
        except Exception as exc:                                      # noqa: BLE001
            return False, f"skipped {connection!r}: its crawl plan could not be read ({exc})"
        if on_disk is None:
            return False, (f"skipped {connection!r}: no crawl plan exists for it — propose and "
                           f"approve one first (P5)")
        if on_disk != "approved":
            return False, (f"skipped {connection!r}: its crawl plan is {on_disk!r}, not approved "
                           f"— approve it before it can run (P5)")
        check = getattr(self, "plan_stale_check", None) or _PLAN_STALE_CHECK
        if check is not None:
            # Codex R-027 AAX f1 #6: approved, but for THIS revision of the connection? Fail closed
            try:
                stale = check(self.service, connection)
            except Exception as exc:                                  # noqa: BLE001
                return False, (f"skipped {connection!r}: whether its approved crawl plan still "
                               f"covers the connection as it is now could not be checked "
                               f"({type(exc).__name__}: {exc})")
            if stale:
                return False, f"skipped {connection!r}: {stale} (P5)"
        return True, None

    def refresh_plans(self, agent_id: str, *, by: str = "") -> AdapterAgentSpec:
        """Mirror each connection's on-disk plan status into the spec. Mirrors only — approval
        itself happens upstream (`approve_plan`, which records who approved)."""
        spec = self.registry.get(agent_id)
        plans = dict(spec.plans)
        for c in spec.connections:
            status = self.service.plan_status(c)
            if status in ("proposed", "approved"):
                plans[c] = status
            else:
                plans.pop(c, None)
        if plans == dict(spec.plans):
            return spec
        return self.registry.update(agent_id, {"plans": plans}, by=by, action="refresh_plans")

    # --- locking --------------------------------------------------------------------------
    def _acquire(self, agent_id: str) -> Optional[Path]:
        with _PROCESS_LOCKS_GUARD:
            lock = _PROCESS_LOCKS.setdefault(agent_id, threading.Lock())
        if not lock.acquire(blocking=False):
            return None
        path = self._agent_runs_dir(agent_id) / ".sync.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return path
            except FileExistsError:
                try:
                    if time.time() - path.stat().st_mtime > _LOCK_STALE_S:
                        path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                break
        lock.release()
        return None

    def _release(self, agent_id: str, path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        _PROCESS_LOCKS[agent_id].release()

    # --- sync -----------------------------------------------------------------------------
    def sync(self, agent_id: str, *, trigger: Optional[dict[str, Any]] = None,
             spec: Optional[AdapterAgentSpec] = None,
             connections: Optional[list[str]] = None) -> SyncSummary:
        trigger = dict(trigger or {"type": "manual"})
        trigger.setdefault("type", "manual")
        spec = spec or self.registry.get(agent_id)
        run_id = f"{self._clock().strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        started = ev.now_iso(self._clock())

        refusal = None
        if spec.status == "paused":
            refusal = f"adapter agent {spec.id!r} is paused; resume it to sync"
        elif spec.status != "active":
            # fail closed: a draft never syncs, whatever the trigger — it is activated first,
            # with its checks (R-019, UI-builder deferred #1)
            refusal = (f"adapter agent {spec.id!r} is {spec.status!r}; only an active agent syncs"
                       + (" — activate the draft first (its plans must be approved)"
                          if spec.status == "draft" else ""))
        else:
            blocked = self.agent_gate(spec)
            if blocked:
                refusal = (f"adapter agent {spec.id!r} does not sync until every crawl plan it "
                           f"references is approved: " + "; ".join(blocked))
        if refusal:
            summary = SyncSummary(spec.id, run_id, "refused", trigger, started,
                                  ev.now_iso(self._clock()), [], reason=refusal)
            self._write_record_safe(spec, summary)
            return summary

        lock = self._acquire(spec.id)
        if lock is None:
            summary = SyncSummary(spec.id, run_id, "busy", trigger, started,
                                  ev.now_iso(self._clock()), [],
                                  reason=f"a sync of {spec.id!r} is already running")
            self._write_record_safe(spec, summary)
            return summary
        try:
            results: list[ConnectionResult] = []
            self._extra_occurrences = []
            for c in [c for c in spec.connections if connections is None or c in connections]:
                try:
                    results.append(self._sync_one(spec, c, run_id, trigger))
                except Exception as exc:                              # noqa: BLE001
                    # isolation holds even for a fault in the manager's own bookkeeping
                    results.append(ConnectionResult(
                        c, "failed", ended=ev.now_iso(self._clock()),
                        reason=f"{c!r} failed: the manager could not complete it: "
                               f"{type(exc).__name__}: {exc}"))
            warnings = [f"{r.connection!r}: the §5.3 event could not be written: {r.event_error}"
                        for r in results if r.event_error]
            proposals: dict[str, Any] = {}
            if spec.propose_ontology_additions:
                proposals = self._scan_proposals(spec, run_id, warnings)
            ran = [r for r in results if r.status != "skipped"]
            ok = [r for r in ran if r.status == "completed"]
            if not ran:
                status = "nothing_to_run"
            elif len(ok) == len(results):
                status = "completed_with_warnings" if warnings else "completed"
            elif ok:
                status = "partial"
            elif any(r.status == "failed" for r in ran):
                status = "failed"
            else:
                status = "unverified"
            reasons = [r.reason for r in results if r.reason] + warnings
            reason = None if status == "completed" else ("; ".join(reasons) or None)
            summary = SyncSummary(spec.id, run_id, status, trigger, started,
                                  ev.now_iso(self._clock()), results, reason=reason,
                                  proposals=proposals, warnings=warnings,
                                  screen_policy=self._screen_policy())
            self._write_record_safe(spec, summary)
            return summary
        finally:
            self._release(spec.id, lock)

    def _scan_proposals(self, spec: AdapterAgentSpec, run_id: str,
                        warnings: list[str]) -> dict[str, Any]:
        """Scan EVERY retained run of every connection (recurrence accumulates across runs; the
        proposal module also persists what it has seen). A failure here is serialised and turns
        the sync into `completed_with_warnings` — it is never swallowed."""
        history: list[IngestRunReport] = []
        for c in spec.connections:
            try:
                history.extend(r for r in self.service.list_runs(c) if r.status != "incomplete")
            except Exception as exc:                                  # noqa: BLE001
                warnings.append(f"proposal scan could not read the run history of {c!r}: "
                                f"{type(exc).__name__}: {exc}")
        try:
            proposals = file_proposals(spec, history, policy=self.proposal_policy,
                                       log=self.proposal_log, vocabulary=self.vocabulary,
                                       produced_by={"agent_version": spec_digest(spec),
                                                    "run_id": run_id},
                                       extra_occurrences=list(self._extra_occurrences))
        except Exception as exc:                                      # noqa: BLE001
            proposals = {"error": f"proposal scan failed: {type(exc).__name__}: {exc}"}
            warnings.append(proposals["error"])
            return proposals
        if proposals.get("vocabulary_problem"):
            warnings.append(f"proposal scan: {proposals['vocabulary_problem']}")
        return proposals

    def _run_options(self, spec: AdapterAgentSpec) -> dict[str, Any]:
        """The spec's per-run ingest options (`extract.passages`, E2E F-11), for a service whose
        `run_ingest` takes them. A service that does not take an option is not sent it."""
        import inspect

        wanted = {"passages": bool(getattr(spec.extract, "passages", False))}
        try:
            params = inspect.signature(self.service.run_ingest).parameters
        except (TypeError, ValueError):
            return {}
        if any(p.kind is p.VAR_KEYWORD for p in params.values()):
            return wanted
        return {k: v for k, v in wanted.items() if k in params}

    def _history(self, conn: str) -> Optional[list[IngestRunReport]]:
        try:
            return list(self.service.list_runs(conn))
        except Exception:                                             # noqa: BLE001
            return None

    def _adapt_before(self, spec: AdapterAgentSpec, conn: str, run_id: str,
                      trigger: dict[str, Any]):
        try:
            return self.adaptation.before_run(spec, conn, run_id=run_id, trigger=trigger), None
        except Exception as exc:                                      # noqa: BLE001
            return None, f"the shape check failed: {type(exc).__name__}: {exc}"

    def _adapt_after(self, spec: AdapterAgentSpec, conn: str, pre, run_id: str,
                     run_dir: Optional[str], error: Optional[str]) -> dict[str, Any]:
        if pre is None:
            return {"checked": False, "error": error} if error else {}
        try:
            out = self.adaptation.after_run(spec, conn, pre, run_id=run_id,
                                            run_dir=Path(run_dir) if run_dir else None)
            if run_dir and spec.propose_ontology_additions:
                self._extra_occurrences.extend(
                    self.adaptation.unmapped_occurrences(spec, conn, Path(run_dir)))
            return out
        except Exception as exc:                                      # noqa: BLE001
            return {**pre.summary(), "error": f"after-run adaptation failed: "
                                              f"{type(exc).__name__}: {exc}"}

    def _sync_one(self, spec: AdapterAgentSpec, conn: str, agent_run_id: str,
                  trigger: dict[str, Any]) -> ConnectionResult:
        runnable, why = self._plan_approved(spec, conn)
        if not runnable:
            return ConnectionResult(conn, "skipped", reason=why)
        # the shape check runs BEFORE the hold gate, so a reverted source releases its hold
        pre, adapt_error = self._adapt_before(spec, conn, agent_run_id, trigger)
        screening = adapt_error or (pre.screening_failed if pre is not None else None)
        if screening:
            # drift screening FAILS CLOSED (R-019 #4): a shape that could not be checked is not
            # ingested — the change it hides could be a write tool or a subject
            if pre is None:
                try:
                    self.adaptation.store.screening_failed(spec.id, {
                        "at": ev.now_iso(self._clock()), "connection": conn, "why": adapt_error})
                except Exception:                                     # noqa: BLE001
                    pass
            summary = pre.summary() if pre is not None else {
                "checked": False, "error": adapt_error, "screening_failed": adapt_error}
            return ConnectionResult(conn, "skipped",
                                    reason=(f"skipped {conn!r}: held — the source's shape could "
                                            f"not be screened ({screening}); it is ingested once "
                                            f"the check completes"),
                                    adaptation=summary)
        hold = self._connection_hold(spec, conn)
        if hold:
            summary = self._adapt_after(spec, conn, pre, agent_run_id, None, adapt_error)
            n = len((self.adaptation.store.holds(spec.id).get(conn) or {}))
            return ConnectionResult(conn, "skipped", reason=f"skipped {conn!r}: {hold}",
                                    exclusions_by_rule={"excluded": {
                                        "DRIFT_PENDING_APPROVAL": n}},
                                    adaptation=summary)
        t0 = time.monotonic()
        started = ev.now_iso(self._clock())
        attrs = {"adapter_agent": spec.id, "agent_run_id": agent_run_id,
                 "trigger": trigger.get("type")}
        harness = RecordingHarness(self.harness) if self.harness is not None else None
        before_runs = self._history(conn)
        before = None if before_runs is None else {r.run_id for r in before_runs}

        def progress(p: dict) -> None:
            if self.progress_cb:
                self.progress_cb({"adapter_agent": spec.id, "connection": conn, **dict(p)})

        def trace(ingest: Any = None) -> dict[str, Any]:
            calls = harness.calls if harness is not None else []
            return produced_by(agent=f"adapter_agent:{spec.id}", agent_version=spec_digest(spec),
                               run_id=agent_run_id, at=ev.now_iso(self._clock()), calls=calls,
                               model_assisted=bool(calls), ingest=ingest)

        try:
            raw = self.service.run_ingest(conn, store=self.store, harness=harness,
                                          progress_cb=progress, **self._run_options(spec))
        except Exception as exc:                                      # noqa: BLE001
            ms = int((time.monotonic() - t0) * 1000)
            reason = f"{conn!r} failed: {type(exc).__name__}: {exc}"
            after = self._history(conn) or []
            new = [r for r in after if before is not None and r.run_id not in before]
            reported = [r for r in new if r.status != "incomplete"]
            attempt = reported[-1] if reported else (new[-1] if new else None)
            adaptation = self._adapt_after(spec, conn, pre, agent_run_id, None, adapt_error)
            result = ConnectionResult(
                conn, "failed", reason=reason, started=started,
                ended=ev.now_iso(self._clock()), ms=ms,
                ingest_run_id=attempt.run_id if attempt else None,
                report_status=attempt.status_raw if reported else None,
                produced_by=trace(attempt.produced_by if reported else None),
                adaptation=adaptation)
            if reported:
                # ingestion wrote a report for this attempt, so it emitted run_failed (§12)
                result.event_owner = "mcp_ingest"
            else:
                # failed before ingestion could emit: this manager owns the event (§12)
                self._emit(result, "run_failed", conn,
                           run_id=result.ingest_run_id or agent_run_id,
                           attrs={**attrs, "error": reason,
                                  "ingest_run_id": result.ingest_run_id})
            return result

        ms = int((time.monotonic() - t0) * 1000)
        try:
            returned: Optional[IngestRunReport] = normalize_report(raw, source_id=conn)
        except Exception:                                             # noqa: BLE001
            returned = None
        # the run directory the drift ledger goes into: the returned report's, else the one the
        # AUTHORITATIVE history holds for this run (R-019 #9) — resolved BEFORE the after-run
        # adaptation, which runs before the history is read for the counts, so the run's drift
        # ledger is counted with its exclusions
        run_dir = returned.run_dir if returned else None
        if run_dir is None:
            hist = self._history(conn) or []
            if returned is not None and returned.run_id:
                mine = next((r for r in hist if r.run_id == returned.run_id), None)
            else:
                new = [r for r in hist if before is not None and r.run_id not in before]
                mine = new[0] if len(new) == 1 else None
            run_dir = mine.run_dir if mine is not None else None
        adaptation = self._adapt_after(spec, conn, pre, agent_run_id, run_dir, adapt_error)
        governance_error = adaptation.get("error") or adaptation.get("records_error")
        after = self._history(conn)
        report: Optional[IngestRunReport] = None
        if after is not None:
            if returned is not None and returned.run_id:
                report = next((r for r in after if r.run_id == returned.run_id), None)
            elif before is not None:
                new = [r for r in after if r.run_id not in before]
                report = new[0] if len(new) == 1 else None
        rid = (report.run_id if report else None) or (returned.run_id if returned else None)
        if report is None:
            where = ("its run history could not be read" if after is None else
                     f"its run history holds no report for run {rid!r}")
            return ConnectionResult(
                conn, "unverified", started=started, ended=ev.now_iso(self._clock()), ms=ms,
                reason=(f"{conn!r} unverified: ingestion returned, but {where} — completion is "
                        f"not established (§5.2), so this is not a success"),
                ingest_run_id=rid, report_status=returned.status_raw if returned else None,
                counts={k: None for k in COUNT_KEYS}, produced_by=trace(None),
                adaptation=adaptation)
        status = ("completed" if report.ok else "failed" if report.status == "failed"
                  else "unverified")
        reason = None
        if status == "failed":
            reason = f"{conn!r} failed: {report.error or 'ingest reported status failed'}"
        elif status == "unverified":
            reason = f"{conn!r} unverified: {report.error or 'the report is not a success'}"
        elif governance_error:
            # a hold whose exclusion record was not written is data kept back silently (P3):
            # the run is not a success until the record exists (R-019 #9)
            status = "unverified"
            reason = (f"{conn!r} unverified: the adaptation's governance records were not "
                      f"written ({governance_error}); held data would have no exclusion record")
        return ConnectionResult(
            conn, status, reason=reason, ingest_run_id=report.run_id,
            started=_iso(report.started) or started,
            ended=_iso(report.ended) or ev.now_iso(self._clock()), ms=ms,
            counts=dict(report.counts), exclusions_by_rule=report.exclusions_by_rule,
            report_status=report.status_raw, event_owner="mcp_ingest",
            produced_by=trace(report.produced_by), adaptation=adaptation)

    def _emit(self, result: ConnectionResult, type_: str, conn: str, *, run_id: str,
              attrs: dict[str, Any]) -> None:
        """Write one §5.3 event; a write failure is recorded on the result, never raised."""
        try:
            e = ev.emit(type_, source_id=conn, run_id=run_id, attrs=attrs,
                        path=self._events_path)
            result.event_id = e["event_id"]
            result.event_owner = "adapter_agent"
        except Exception as exc:                                      # noqa: BLE001
            result.event_error = f"{type(exc).__name__}: {exc}"

    # --- run records (§5.7) ----------------------------------------------------------------
    def _write_record_safe(self, spec: AdapterAgentSpec, summary: SyncSummary) -> None:
        try:
            self._write_record(spec, summary)
        except Exception as exc:                                      # noqa: BLE001
            summary.record_error = f"run record not written: {type(exc).__name__}: {exc}"

    def _write_record(self, spec: AdapterAgentSpec, summary: SyncSummary) -> None:
        steps = [{"tool": f"ingest.run:{c.connection}",
                  "args": {"connection": c.connection},
                  "result_pointers": [],
                  "policy_decision": ("allowed: crawl plan approved" if c.status != "skipped"
                                      else f"refused: {c.reason}"),
                  "ms": c.ms, "status": c.status, "ingest_run_id": c.ingest_run_id,
                  "reason": c.reason, "counts": c.counts,
                  "exclusions_by_rule": c.exclusions_by_rule, "event_id": c.event_id,
                  "event_owner": c.event_owner, "event_error": c.event_error,
                  "report_status": c.report_status, "produced_by": c.produced_by,
                  "started": c.started, "ended": c.ended, "adaptation": c.adaptation}
                 for c in summary.connections]
        digest = spec_digest(spec)
        record = {"run_id": summary.run_id, "kind": RUN_KIND, "agent_id": spec.id,
                  "spec_digest": digest, "trigger": summary.trigger,
                  "started": summary.started, "ended": summary.ended, "status": summary.status,
                  "reason": summary.reason, "steps": steps, "actions": [],
                  "answer": None,
                  "produced_by": {"agent": f"adapter_agent:{spec.id}", "agent_version": digest,
                                  "run_id": summary.run_id, "at": summary.ended},
                  "models_used": models_used(steps),
                  "coverage": {"searched": summary.ran, "skipped": summary.skipped,
                               "reasons": {c.connection: c.reason for c in summary.connections
                                           if c.reason}},
                  "totals": summary.totals(), "warnings": list(summary.warnings),
                  "screen_policy": dict(summary.screen_policy),
                  "proposals": summary.as_dict()["proposals"],
                  "adaptation": {c.connection: c.adaptation for c in summary.connections
                                 if c.adaptation}}
        d = self._agent_runs_dir(spec.id)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{summary.run_id}.json"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(record, indent=2, sort_keys=True, default=str),
                       encoding="utf-8")
        os.replace(tmp, path)
        summary.record_path = str(path)

    def run_records(self, agent_id: str) -> list[dict[str, Any]]:
        d = self._agent_runs_dir(agent_id)
        if not d.is_dir():
            return []
        out = []
        for p in d.glob("*.json"):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            # FEAT8: a Set up step's Activity record lives beside the syncs; it is not a sync
            if isinstance(rec, dict) and rec.get("kind", RUN_KIND) != RUN_KIND:
                continue
            out.append(rec)
        out.sort(key=lambda r: (r.get("started") or "", r.get("run_id") or ""))
        return out

    # --- health ---------------------------------------------------------------------------
    def health(self, agent_id: str) -> dict[str, Any]:
        spec = self.registry.get(agent_id, include_deleted=True)
        now = self._clock()
        max_age = spec.freshness.max_age_hours
        records = self.run_records(spec.id)
        conns = []
        issues: list[str] = []
        for c in spec.connections:
            conns.append(self._connection_health(spec, c, records, now, max_age))
        for h in conns:
            issues.extend(h["issues"])
        if self.registry.is_deleted(spec.id):
            overall = "deleted"
        elif spec.status == "paused":
            overall = "paused"
        elif any(h["consecutive_failures"] for h in conns):
            overall = "failing"
        elif all(h["last_success_at"] is None for h in conns):
            overall = "never_synced"
        elif any(h["stale"] for h in conns):
            overall = "stale"
        elif any(not h["runnable"] for h in conns):
            overall = "awaiting_approval"
        else:
            overall = "healthy"
        try:
            adaptation = self.adaptation.health(spec)
        except Exception as exc:                                      # noqa: BLE001
            adaptation = {"error": f"{type(exc).__name__}: {exc}"}
        for p in adaptation.get("pending") or []:
            issues.append(f"{p['connection']!r}: the source's shape changed ({', '.join(p['kinds'])}"
                          f") — adaptation {p['adaptation_id']} awaits approval: {p['action']}")
        for c, why in (adaptation.get("screening_failed") or {}).items():
            issues.append(f"{c!r}: not ingested at the last check — {why}")
        if adaptation.get("pending") and overall == "healthy":
            overall = "awaiting_approval"
        totals = {k: _sum_or_unknown([h["counts"].get(k) for h in conns]) for k in COUNT_KEYS}
        last = next((r for r in reversed(records) if r.get("status") not in ("refused", "busy")),
                    None)
        return {"agent_id": spec.id, "title": spec.title, "status": spec.status,
                "deleted": self.registry.is_deleted(spec.id), "overall": overall,
                "generated_at": ev.now_iso(now), "freshness": {"max_age_hours": max_age},
                "sync": spec.sync.model_dump(mode="json"),
                "last_sync": ({"run_id": last["run_id"], "status": last["status"],
                               "started": last["started"], "ended": last["ended"]}
                              if last else None),
                "connections": conns, "totals": totals, "issues": issues,
                "adaptation": adaptation}

    def _connection_health(self, spec: AdapterAgentSpec, conn: str, records: list[dict],
                           now: dt.datetime, max_age: int) -> dict[str, Any]:
        runnable, why = self.plan_gate(spec, conn)
        try:
            reports = self.service.list_runs(conn)
        except Exception as exc:                                      # noqa: BLE001
            reports = []
            why = why or f"run history unreadable: {exc}"
        # Timeline, one entry per ATTEMPT. The run history is authoritative for every run it
        # holds; this manager's own steps are added only for attempts the history does not hold
        # a report for (a failure before ingestion wrote one). Correlation is by ingest run id,
        # so one failed attempt is never counted twice. Skips are not attempts.
        by_id = {r.run_id: r for r in reports if r.run_id}
        timeline: list[tuple[str, str, Optional[str], Optional[str]]] = []
        for rep in reports:
            if rep.status == "incomplete":
                continue
            timeline.append((_iso(rep.when) or "", rep.status, rep.run_id, rep.error))
        for r in records:
            for s in r.get("steps", []):
                if s.get("args", {}).get("connection") != conn or s.get("status") == "skipped":
                    continue
                rid = s.get("ingest_run_id")
                held = by_id.get(rid) if rid else None
                if held is not None and held.status != "incomplete":
                    continue                        # the report speaks for this attempt
                status = s.get("status")
                if status == "completed":
                    status = "unverified"           # a success with no report is not one
                timeline.append((s.get("ended") or r.get("ended") or "", status, rid,
                                 s.get("reason")))
        timeline.sort(key=lambda x: x[0])
        consecutive = 0
        for _, status, _, _ in reversed(timeline):
            if status == "completed":
                break
            consecutive += 1
        ok_reports = [r for r in reports if r.ok]
        latest_ok = ok_reports[-1] if ok_reports else None
        last_success = latest_ok.when if latest_ok else None
        issues = []
        future = last_success is not None and last_success > now + FUTURE_SKEW
        age_h: Optional[float] = None
        if last_success is None:
            stale = True
        elif future:
            stale = True
        else:
            age_s = max(0.0, (now - last_success).total_seconds())
            stale = age_s > max_age * 3600          # compare UNROUNDED; round only for display
            age_h = round(age_s / 3600, 2)
        if not runnable and why:
            issues.append(why)
        if last_success is None:
            issues.append(f"{conn!r} has never synced successfully — stale by definition")
        elif future:
            issues.append(f"{conn!r}: its last successful report is dated "
                          f"{_iso(last_success)}, after now ({ev.now_iso(now)}) — a clock or "
                          f"timestamp fault; not accepted as freshness")
        elif stale:
            issues.append(f"{conn!r} is stale: last successful sync {age_h}h ago, target "
                          f"{max_age}h")
        if consecutive:
            issues.append(f"{conn!r} has failed {consecutive} time(s) in a row"
                          + (f": {timeline[-1][3]}" if timeline and timeline[-1][3] else ""))
        in_flight = [r for r in reports if r.status == "incomplete"]
        last = timeline[-1] if timeline else None
        return {
            "connection": conn, "runnable": runnable, "skip_reason": why,
            "plan": {"spec": spec.plans.get(conn), "ingest": self._safe_plan(conn)},
            "last_run": ({"at": last[0] or None, "status": last[1], "ingest_run_id": last[2],
                          "reason": last[3]} if last else None),
            "last_success_at": _iso(last_success), "age_hours": age_h, "stale": stale,
            "future_timestamp": future,
            "consecutive_failures": consecutive,
            "counts": dict(latest_ok.counts) if latest_ok else {k: None for k in COUNT_KEYS},
            "exclusions_by_rule": latest_ok.exclusions_by_rule if latest_ok else {},
            "exclusion_reasons": ([dict(x) for x in latest_ok.exclusion_reasons]
                                  if latest_ok else []),
            "incomplete_runs": [r.run_id for r in in_flight],
            "issues": issues,
        }

    # --- adaptation API (what the gateway wraps) ---------------------------------------------
    def pending_adaptations(self, agent_id: str) -> list[dict[str, Any]]:
        self.registry.get(agent_id, include_deleted=True)
        return self.adaptation.pending(agent_id)

    def adaptations(self, agent_id: str) -> list[dict[str, Any]]:
        self.registry.get(agent_id, include_deleted=True)
        return sorted(self.adaptation.records(agent_id).values(),
                      key=lambda r: (r.get("created_at") or "", r["adaptation_id"]))

    def approve_adaptation(self, agent_id: str, adaptation_id: str, *, by: Any,
                           note: Optional[str] = None,
                           changes_digest: Optional[str] = None) -> dict[str, Any]:
        """Apply the proposed new version, release the hold, advance the baseline. `by` is the
        AUTHENTICATED principal (an object with `principal_id`); a bare name is refused.
        `changes_digest` (optional): the digest of the changes the decider was shown; approval is
        refused unless it names exactly the adaptation's changes (R-025)."""
        return self.adaptation.approve(self.registry.get(agent_id), adaptation_id, by=by,
                                       note=note, changes_digest=changes_digest)

    def on_plan_approved(self, connection_id: str, replanned: list[str], *,
                         by: Any) -> list[dict[str, Any]]:
        """`plans.approve_plan(principal=...)` dropped or changed the plan steps `replanned`.
        Every agent managing the connection decides each open adaptation whose held steps were
        ALL re-planned (`AdaptationEngine.resolve_by_plan`, which validates the principal) —
        whether it was held per step or, because it held every step, as the whole connection."""
        out = []
        for spec in self.registry.list():
            if connection_id not in spec.connections:
                continue
            for aid, rec in sorted(self.adaptation.records(spec.id).items()):
                if rec.get("connection") != connection_id:
                    continue
                got = self.adaptation.resolve_by_plan(spec, aid, by=by, steps=replanned)
                if got is not None:
                    out.append(got)
        return out

    def reject_adaptation(self, agent_id: str, adaptation_id: str, *, by: Any,
                          reason: str) -> dict[str, Any]:
        """Record the rejection; the affected part STAYS held (reversal: revert the source)."""
        return self.adaptation.reject(self.registry.get(agent_id), adaptation_id, by=by,
                                      reason=reason)

    def on_list_changed(self, connection_id: str, *, kind: str = "tools") -> list[dict[str, Any]]:
        """An MCP server said its tool / resource list changed (`notifications/*/list_changed`).
        Every ACTIVE agent managing that connection whose `adapt.watch.on_list_changed` is on
        re-checks — and syncs — that one connection now. Synchronous: the client hook calls this
        from a worker thread (see `install_list_changed_hook`), never from its event loop."""
        out = []
        for spec in self.registry.list():
            if connection_id not in spec.connections:
                continue
            pol = Policy.of(spec)
            note = {"at": ev.now_iso(self._clock()), "connection": connection_id, "kind": kind}
            if not pol.on_list_changed or spec.status != "active":
                why = ("adapt.watch.on_list_changed is off" if not pol.on_list_changed
                       else f"the agent is {spec.status}")
                self.adaptation.store.notify(spec.id, {**note, "action": "ignored", "why": why})
                out.append({"agent_id": spec.id, "status": "ignored", "reason": why})
                continue
            self.adaptation.store.notify(spec.id, {**note, "action": "sync"})
            summary = self.sync(spec.id, trigger={"type": "list_changed",
                                                  "connection": connection_id, "kind": kind},
                                connections=[connection_id])
            out.append(summary.as_dict())
        return out

    def _safe_plan(self, conn: str) -> Optional[str]:
        try:
            return self.service.plan_status(conn)
        except Exception:                                             # noqa: BLE001
            return None


__all__ = ["AdapterAgentManager", "SyncSummary", "ConnectionResult", "RUN_KIND", "RegistryError",
           "AdaptationRefused"]
