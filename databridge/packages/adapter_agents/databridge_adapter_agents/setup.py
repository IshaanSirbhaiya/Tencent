"""Set up a source through its adapter agent (FEAT8, founder design 2026-10-02).

Once a read source is registered it is offered in the Builder's adapter-agent section as "ready to
set up". One "Set up" drives the EXISTING pipeline as the agent's steps:

    1. propose the crawl plan              (the Sources plan route, unchanged)
    2. a person approves the plan + vouches (the Sources approval route, the signed-in principal)
    3. a capture-only discovery              (`run_ingest(capture_only=True)`: tables AND documents
                                             are captured, screened and profiled; nothing is built)
    4. the mapping proposal                 (the mapper, run by that first run)
    5. the agent explains the mapping       (`explain.explain_mapping`, advisory)
    6. a person approves the mapping        (the Sources mapping/link/hold/identity routes)
    7. hand-off: the agent is activated by that person (the existing sealed delegation) and starts
       the build run; it is the source's keeper from then on (sync and drift as today)

This module holds what is the AGENT's: its id for a source, its draft spec, the hand-off gate, and
the Activity record of each step. It approves nothing — no function here takes an approval, and the
agent's own principal (``adapter_agent:<id>``) is a machine actor every approval gate refuses.

Activity records are written beside the agent's sync records (``<runs>/adapter_agent/<id>/``) with
``kind: "adapter_setup"``; `AdapterAgentManager.run_records` reads only sync records, so health and
the sync list are unchanged.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from databridge_core.decision_ledger import (DecisionLedger, DecisionRefused, actor_of,
                                             refusal, require_person)

from . import paths
from .registry import spec_digest

SETUP_KIND = "adapter_setup"
#: run-record kind the Activity feed reads for adapter agents (manager.RUN_KIND)
_RUNS_SUBDIR = "adapter_agent"
_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")


def keeper_id(source_id: str) -> str:
    """The id of the adapter agent Set up creates for `source_id`: ``keeper-<source>``, shortened
    with a digest when that would not be a valid id. Deterministic, so a second Set up finds it."""
    base = f"keeper-{source_id}".lower()
    if _ID.fullmatch(base):
        return base
    clean = re.sub(r"[^a-z0-9_-]", "-", base)[:50].rstrip("-")
    tail = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:8]
    return f"{clean}-{tail}" if _ID.fullmatch(f"{clean}-{tail}") else f"keeper-{tail}"


def agent_principal(agent_id: str) -> str:
    """How the agent is named as an actor. A machine actor: every approval gate refuses it."""
    return f"adapter_agent:{agent_id}"


def agent_actor(agent_id: str) -> dict[str, Any]:
    """The agent as an actor object (R-028 #7): a MACHINE principal, never a person."""
    return actor_of({"principal_id": agent_principal(agent_id), "authenticated_by": "agent_runtime",
                     "principal_type": "AdapterAgent", "dev_only": False})


def draft_spec(source_id: str, pack: str, *, title: Optional[str] = None) -> dict[str, Any]:
    """The DRAFT spec of a source's keeper. Draft: it never syncs until a person activates it at the
    hand-off, which the manager gates on an approved crawl plan. ``screen`` names no pack, so it is
    screened by whatever this deployment resolves (as a spec without one always was)."""
    return {"spec": "adapter_agent/v1", "id": keeper_id(source_id),
            "title": title or f"Keeper of {source_id}",
            "created_from": f"Set up {source_id} from the Builder: discover it, explain the "
                            f"mapping, build it once a person approves, then keep it in sync.",
            "connections": [source_id], "plans": {},
            "sync": {"type": "manual"},
            "extract": {"pack": pack, "prose": True, "tabular": True, "passages": True},
            "freshness": {"max_age_hours": 24}, "propose_ontology_additions": True,
            "status": "draft"}


#: cadences a person may pick at the hand-off -> the spec's sync trigger
CADENCES = {
    "manual": {"type": "manual"},
    "hourly": {"type": "schedule", "schedule": {"cron": "0 * * * *", "timezone": "UTC"}},
    "daily": {"type": "schedule", "schedule": {"cron": "0 6 * * *", "timezone": "UTC"}},
}


def setup_dir(registry_root: Path, source_id: str) -> Path:
    return Path(registry_root) / "_setup" / source_id


def save_explanation(registry_root: Path, source_id: str, explanation: dict) -> Path:
    d = setup_dir(registry_root, source_id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "explanation.json"
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(explanation, indent=2, sort_keys=True, default=str),
                   encoding="utf-8")
    os.replace(tmp, p)
    return p


def load_explanation(registry_root: Path, source_id: str) -> Optional[dict]:
    p = setup_dir(registry_root, source_id) / "explanation.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def record_step(runs_root: Optional[Path], spec: Any, *, step: str, status: str, by: str,
                reason: str, actor: str = "agent", counts: Optional[dict[str, Any]] = None,
                model: Optional[str] = None,
                who: Optional[dict[str, Any]] = None,
                requested_by: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Write ONE Activity record for a setup step. ``actor`` is ``agent`` (the agent proposed or
    did it, at ``by``'s request) or ``person`` (``by`` decided it). Returns the record.

    R-028 #7: the record carries the common actor object (``actor``): the agent's machine actor
    for an ``agent`` step; for a ``person`` step, the actor of ``who`` (what the authentication
    gate returned) — whose ``principal_id`` must be ``by``. A person step written without ``who``
    has ``actor: null`` (nothing is invented) and says so in ``actor_unrecorded``.

    R-029 TIDY2: the record's ``trigger`` carries the full common actor object (``actor``) of the
    PERSON behind the step, never only a name: for a ``person`` step that person; for an ``agent``
    step the person at whose request it ran (``requested_by``, whose ``principal_id`` must be
    ``by``). Without one, the trigger has no ``actor`` (nothing is invented)."""
    now = _now()
    stamp = now.strftime("%Y%m%dT%H%M%S")
    run_id = f"setup-{stamp}-{uuid.uuid4().hex[:6]}-{re.sub(r'[^a-z0-9]', '', step)[:20]}"
    at = now.isoformat(timespec="seconds")
    digest = spec_digest(spec)
    if actor == "person":
        person = actor_of(who) if who is not None else None
        if person is not None and person["principal_id"] != str(by):
            raise DecisionRefused("APPROVER_NOT_AUTHENTICATED", "a setup step names the person who "
                                  "decided it, and that person signed in", 403)
        step_actor = person
    else:
        step_actor = agent_actor(spec.id)
    if actor == "person":
        trigger_actor = step_actor
    elif requested_by is not None:
        trigger_actor = actor_of(requested_by)
        if trigger_actor["principal_id"] != str(by):
            raise DecisionRefused("APPROVER_NOT_AUTHENTICATED", "a setup step names the person "
                                  "who asked for it, and that person signed in", 403)
    else:
        trigger_actor = None
    who = agent_principal(spec.id)
    step_rec = {"tool": f"setup.{step}", "status": status, "reason": reason[:500],
                "started": at, "ended": at,
                "policy_decision": ({"decision": "allow", "reason_code": "person_decided",
                                     "rule": f"decided by {by}"} if actor == "person" else
                                    {"decision": "allow", "reason_code": "proposal_only",
                                     "rule": "the agent proposes; a person approves"}),
                "produced_by": {"agent": by if actor == "person" else who,
                                "agent_version": digest, "run_id": run_id, "at": at,
                                **({"model": model} if model else {})},
                "counts": {k: v for k, v in (counts or {}).items()
                           if isinstance(v, (int, str)) or v is None}}
    record = {"run_id": run_id, "kind": SETUP_KIND, "agent_id": spec.id, "spec_digest": digest,
              "recorded_at": now.isoformat(),
              "trigger": {"type": "setup", "by": by,
                          **({"actor": trigger_actor} if trigger_actor else {})},
              "started": at, "ended": at, "status": status,
              "reason": (f"{'Proposed by the agent' if actor == 'agent' else 'Decided by ' + by}: "
                         f"{reason}")[:600],
              "steps": [step_rec], "actions": [], "answer": None,
              "produced_by": {"agent": who, "agent_version": digest, "run_id": run_id, "at": at},
              "models_used": [model] if model else [], "totals": {}, "warnings": [],
              "actor": step_actor, "actor_kind": "person" if actor == "person" else "agent"}
    if actor == "person" and step_actor is None:
        record["actor_unrecorded"] = "the caller did not pass the signed-in principal"
    root = Path(runs_root) if runs_root is not None else paths.runs_dir()
    d = root / _RUNS_SUBDIR / spec.id
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{run_id}.json"
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, p)
    return record


def setup_records(runs_root: Optional[Path], agent_id: str) -> list[dict[str, Any]]:
    root = Path(runs_root) if runs_root is not None else paths.runs_dir()
    d = root / _RUNS_SUBDIR / agent_id
    out = []
    if d.is_dir():
        for p in d.glob("setup-*.json"):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if rec.get("kind") == SETUP_KIND:
                out.append(rec)
    out.sort(key=lambda r: (r.get("recorded_at") or r.get("started") or "",
                            r.get("run_id") or ""))
    return out


def approved_by_people(source_id: str, mapping: Any, vocab) -> tuple[list[str], list[str]]:
    """(tables a PERSON approved and whose approval still verifies, problems). An approval by a
    machine actor, by a delegation, or one that no longer verifies does not count: the hand-off
    builds only what a signed-in person approved."""
    from databridge_mcp_ingest.mapping import approval_problem, logged_approvals  # noqa: PLC0415

    from .adapt import _MACHINE

    logged = logged_approvals(source_id)
    ok, problems = [], []
    for table, tm in sorted((mapping.tables or {}).items()):
        if tm.status != "approved":
            continue
        a = tm.approval or {}
        pid = str(a.get("principal_id") or "")
        how = str(a.get("authenticated_by") or "")
        if not pid or pid.lower().startswith(_MACHINE) or how.startswith("delegated:"):
            problems.append(f"{table}: approved by {pid or 'nobody recorded'}"
                            f"{' through a delegation' if how.startswith('delegated:') else ''}, "
                            f"not a signed-in person")
            continue
        why = approval_problem(source_id, table, tm, vocab, logged=logged)
        if why:
            problems.append(f"{table}: {why}")
            continue
        ok.append(table)
    return ok, problems


def discovery_problems(discovery: Optional[dict], *, plan_digest: Optional[str]) -> list[str]:
    """Why the source does not count as DISCOVERED for the current plan (empty: it does). Only an
    authoritative SUCCESSFUL capture-only discovery counts (Codex R-027 AAX f1 #8): its status is
    the run's own report (`runner.latest_discovery`), it must be ``discovered`` (a failed,
    refused, partial, unverified, empty or incomplete run is not), and it must have run under the
    plan approved now."""
    from databridge_mcp_ingest.runner import DISCOVERED  # noqa: PLC0415

    if not discovery:
        return ["the source has not been discovered yet (no capture-only discovery run)"]
    out: list[str] = []
    if discovery.get("status") != DISCOVERED:
        out.append(f"the last discovery ({discovery.get('run_id')}) ended "
                   f"{discovery.get('status')!s}, not {DISCOVERED}: "
                   f"{str(discovery.get('message') or '')[:200]}; discover again")
    if not plan_digest or discovery.get("plan_digest") != plan_digest:
        out.append(f"the last discovery ({discovery.get('run_id')}) ran under a different crawl "
                   f"plan than the one approved now; discover again")
    return out


def explanation_problems(explanation: Optional[dict], mapping: Any,
                         discovery: Optional[dict]) -> list[str]:
    """Why the saved explanation is not the one for THIS proposal (empty: it is). It must exist
    (Codex R-027 AAX f1 #4), be bound to the latest discovery run and to the capture digest that
    discovery recorded for each table (#5), and its per-table mapping digests must equal the
    current proposal's exactly."""
    from databridge_mcp_ingest.mapping import mapping_digest  # noqa: PLC0415

    if not explanation:
        return ["the agent has not explained the mapping yet"]
    out: list[str] = []
    bound = explanation.get("discovery") or {}
    if not discovery or bound.get("run_id") != discovery.get("run_id"):
        out.append(f"the explanation was written from discovery {bound.get('run_id')!s}, not the "
                   f"latest one ({(discovery or {}).get('run_id')!s}); explain it again")
    tables = dict(getattr(mapping, "tables", None) or {})
    want = {t: mapping_digest(tm) for t, tm in tables.items()}
    got = {t.get("table"): t.get("mapping_digest") for t in explanation.get("tables") or []}
    if want != got:
        changed = sorted(t for t in set(want) | set(got) if want.get(t) != got.get(t))
        out.append(f"the mapping changed since it was explained ({', '.join(changed[:3])}); "
                   f"explain it again")
    # Codex R-027 AAX f2 #5: every table the discovery captured, and every table a person
    # approved (only those can ever be built), must be explained from EXACTLY the capture the
    # discovery recorded - a missing sha, a capture problem or another capture all refuse. A
    # table neither captured under this plan nor approved cannot be built and is not bound.
    recorded = {ref: (e or {}).get("sha256")
                for ref, e in ((discovery or {}).get("tables") or {}).items()}
    for t in explanation.get("tables") or []:
        name = t.get("table")
        tm = tables.get(name)
        if name not in recorded and getattr(tm, "status", None) != "approved":
            continue
        sha = t.get("capture_sha")
        if t.get("capture_problem"):
            out.append(f"{name}: the explanation could not read its capture "
                       f"({t['capture_problem']}); discover and explain it again")
        elif not sha or sha != recorded.get(name):
            out.append(f"{name}: the explanation cites capture {str(sha)[:12] or 'none'}, not "
                       f"the one the discovery recorded; explain it again")
    return out


#: a document an earlier sync built and verified, captured again with the same bytes (the
#: discovery's own word for it, `runner._discover`)
_KEPT = "unchanged"


def document_items(discovery: Optional[dict]) -> tuple[list[dict], list[dict]]:
    """(to build, to keep) of a discovery's documents. To BUILD: the screen allowed it now. To
    KEEP (E2E run 5 W-05): an earlier sync already built it and its bytes are unchanged - the
    keeper adopts it as it is, nothing is rebuilt. A held, blocked or review item is neither."""
    items = ((discovery or {}).get("documents") or {}).get("items") or []
    build = [d for d in items if d.get("decision") == "allow" and not d.get("held")]
    keep = [d for d in items if d.get("decision") == _KEPT and not d.get("held")]
    return build, keep


def documents_digest(discovery: Optional[dict]) -> Optional[str]:
    """The digest of the documents a hand-off would hand to the keeper: those the screen ALLOWED
    (built at the hand-off) and those an earlier sync already built (kept, W-05), or None when
    there are none. A build-only discovery digests exactly as it always did."""
    build, keep = document_items(discovery)
    items = sorted([*(f"{d.get('item_id')}:{d.get('sha256')}" for d in build),
                    *(f"{d.get('item_id')}:{d.get('sha256')}:keep" for d in keep)])
    if not items:
        return None
    return "sha256:" + hashlib.sha256("\n".join(items).encode("utf-8")).hexdigest()


#: the decisions a person can record on a discovery's documents (build them / keep the built ones)
DOCUMENT_DECISIONS = ("build", "adopt")


def documents_problems(decision: Optional[dict], discovery: Optional[dict]) -> list[str]:
    """Codex R-027 AAX f2 #7: documents are built only after a PERSON decided to build exactly
    the documents this discovery captured (their digest, this run). W-05: documents an earlier
    sync already built are KEPT under the same kind of decision - never adopted silently."""
    digest = documents_digest(discovery)
    if digest is None:
        return []
    build, keep = document_items(discovery)
    if decision and decision.get("refusal"):
        return [f"the documents decision log does not verify "
                f"({decision['refusal']['code']}); decide again"]
    if not decision or decision.get("decision") not in DOCUMENT_DECISIONS:
        what = ([f"to build the {len(build)} document(s) the discovery captured"] if build
                else []) + ([f"to keep the {len(keep)} document(s) an earlier run already built "
                             f"(nothing is rebuilt)"] if keep else [])
        return [f"a person has not decided {' and '.join(what)}"]
    if decision.get("discovery_run_id") != (discovery or {}).get("run_id") or \
            decision.get("documents_digest") != digest:
        return ["the documents decision was made on another discovery; decide again"]
    pid = str(decision.get("principal_id") or "")
    from .adapt import _MACHINE  # noqa: PLC0415
    if not pid or pid.lower().startswith(_MACHINE):
        return ["the documents decision was not made by a signed-in person"]
    return []


def handoff_problems(source_id: str, *, plan_status: Optional[str], plan_stale: Optional[str],
                     plan_digest: Optional[str], discovery: Optional[dict], mapping: Any,
                     explanation: Optional[dict], vocab,
                     documents_decision: Optional[dict] = None) -> list[str]:
    """Why the agent may NOT hand off to the build yet (empty: it may). The gate the route and the
    UI share; the manager's own activation gate runs again after it. Needs:

    * the crawl plan approved AND not stale for the connection as it is now (`plan_stale`, the
      Sources routes' shared check; Codex R-027 AAX f1 #6);
    * a successful capture-only discovery under that plan (#8);
    * when the discovery captured tables: a saved explanation bound to that discovery and to the
      current proposal (#4, #5), no table approved by a machine or a delegation, and every
      person-approved table captured by that discovery;
    * when it captured documents the screen allowed: a person's decision to build exactly those
      documents (f2 #7) - a document-only source has no table-mapping gate;
    * something a person approved to build: a table, or the documents.

    Pending links, holds, unapproved tables and identity rules are NOT problems here: they are
    `pending_decisions`, each of which the hand-off must name as deferred (f2 #8)."""
    out: list[str] = []
    if plan_status != "approved":
        out.append(f"the crawl plan is {plan_status or 'not proposed'!s}; a person approves it "
                   f"first")
    if plan_stale:
        out.append(f"the crawl plan is stale: {plan_stale}")
    disc = discovery_problems(discovery, plan_digest=plan_digest)
    out.extend(disc)
    if disc:
        return out
    has_tables = bool((discovery or {}).get("tables"))
    docs = documents_problems(documents_decision, discovery)
    has_docs = documents_digest(discovery) is not None
    if not has_tables and not has_docs:
        out.append("the discovery captured nothing the screen allowed to build")
        return out
    ok: list[str] = []
    if has_tables:
        if mapping is None or not (mapping.tables or {}):
            out.append("there is no mapping proposal to approve yet")
        else:
            out.extend(explanation_problems(explanation, mapping, discovery))
            ok, problems = approved_by_people(source_id, mapping, vocab)
            out.extend(problems)
            captured = set(((discovery or {}).get("tables") or {}))
            for t in ok:
                if t not in captured:
                    out.append(f"{t}: approved, but the discovery did not capture it; discover "
                               f"again")
    out.extend(docs)
    if not ok and not (has_docs and not docs):
        out.append("nothing has been approved to build: approve a table's mapping"
                   + (", or decide to build the documents" if has_docs else ""))
    return out


# --- what a person is shown of an item (E2E run 5 W-06) -------------------------------------------
_FS_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]|file:)")


def _segments(uri: str) -> list[str]:
    path = re.sub(r"^file:/*", "/", uri)
    return [p for p in re.split(r"[\\/]+", path) if p]


def display_ref(uri: Optional[str], uris: list[Optional[str]]) -> Optional[str]:
    """How a captured item is named to a person: a SERVER filesystem path loses the folders every
    captured item shares (the source's own root, which says where the server keeps its files),
    leaving its path inside the source; anything else (a table, an ``mcp://`` resource) is
    already source-relative and is shown as it is. Only a shared prefix is removed - when the
    item has no peer to bound the root, only its own name is shown."""
    if not uri or not _FS_PATH.match(str(uri)):
        return uri
    mine = _segments(str(uri))
    if not mine:
        return None
    peers = [_segments(str(u)) for u in uris if u and _FS_PATH.match(str(u))]
    peers = [p for p in peers if p != mine] + [mine]
    if len(peers) == 1:
        return mine[-1]
    common = 0
    for parts in zip(*peers):
        if len({p.lower() for p in parts}) != 1:
            break
        common += 1
    return "/".join(mine[min(common, len(mine) - 1):])


# --- pending decisions (Codex R-027 AAX f2 #8) ----------------------------------------------------
def _identity_decided(pack: str, kind: str) -> bool:
    try:
        from databridge_graph.ontology_state import read_state  # noqa: PLC0415

        return any(r.get("pack") == pack and r.get("kind") == kind
                   for r in read_state().get("identity_rule_decisions") or [])
    except Exception:                                                # noqa: BLE001
        return False                                   # unreadable: not decided (fail closed)


def pending_decisions(source_id: str, *, mapping: Any, discovery: Optional[dict], vocab,
                      overrides: Any = None) -> list[dict]:
    """Every decision a person has NOT made that the build would silently leave out. Each has a
    stable ``key``; the hand-off must name each one as deferred ("defer" / "keep held"), so an
    omission is always a decision:

    * ``link:`` a proposed link (no foreign key declares it) not yet approved or rejected;
    * ``hold:`` a table or document the screen holds for review (or a drift hold) whose owner has
      not lifted the hold for this content - it stays held;
    * ``table:`` a captured table whose mapping (and any per-row kind split) is still proposed -
      it is not built;
    * ``identity:`` a kind a person-approved table builds whose pack declares identity rules no
      one has decided - its records stay unmerged."""
    from databridge_mcp_ingest.mapping import split_kinds  # noqa: PLC0415

    if overrides is None:
        try:
            from databridge_mcp_ingest.screen import load_overrides  # noqa: PLC0415

            overrides = load_overrides(source_id)
        except Exception:                                            # noqa: BLE001
            overrides = None
    allows = getattr(overrides, "allows", None)

    def lifted(item_id: str, sha: str) -> bool:
        return bool(callable(allows) and allows(item_id, sha))

    out: list[dict] = []
    tables = dict(getattr(mapping, "tables", None) or {})
    for t, tm in sorted(tables.items()):
        for lk in tm.links or []:
            if lk.get("status") == "proposed":
                key = (f"link:{t}:{lk.get('column')}:{lk.get('source')}:{lk.get('table')}:"
                       f"{lk.get('key')}")
                out.append({"key": key, "kind": "link", "table": t,
                            "what": f"a proposed link from {t}.{lk.get('column')} to "
                                    f"{lk.get('table')} - not built unless approved",
                            "deferral": "defer"})
    disc_tables = (discovery or {}).get("tables") or {}
    items = [*({**e, "ref": t, "kind": "table"} for t, e in disc_tables.items()),
             *({**d, "ref": d.get("uri"), "kind": "document"}
               for d in ((discovery or {}).get("documents") or {}).get("items") or [])]
    uris = [e.get("ref") for e in items if e["kind"] == "document"]
    for e in sorted(items, key=lambda x: str(x.get("item_id"))):
        held = e.get("held") or e.get("decision") == "review"
        if held and not lifted(str(e.get("item_id")), str(e.get("sha256"))):
            # W-06: a document is named by its path inside the source, never the server's path
            ref = display_ref(e.get("ref"), uris) if e["kind"] == "document" else e.get("ref")
            out.append({"key": f"hold:{e.get('item_id')}", "kind": "hold", "table": ref,
                        "what": f"the {e['kind']} {ref} is held by the screen - it stays "
                                f"held (not built) until its owner lifts the hold",
                        "deferral": "keep held"})
    for t, tm in sorted(tables.items()):
        if t in disc_tables and tm.status == "proposed":
            out.append({"key": f"table:{t}", "kind": "table", "table": t,
                        "what": f"{t}'s mapping{' and kind split' if tm.split else ''} is still "
                                f"proposed - the table is not built",
                        "deferral": "defer"})
    ok, _ = approved_by_people(source_id, mapping, vocab) if tables else ([], [])
    kinds: set[str] = set()
    for t in ok:
        tm = tables[t]
        kinds.update(k for k in split_kinds(tm.model_dump()) if k)
    for kind in sorted(kinds):
        rule = vocab.identity_rule_for(kind) if hasattr(vocab, "identity_rule_for") else None
        if rule is not None and not _identity_decided(rule.pack, kind):
            out.append({"key": f"identity:{kind}", "kind": "identity", "table": None,
                        "what": f"no one has decided the identity rules the pack declares for "
                                f"{kind} - its records stay unmerged",
                        "deferral": "defer"})
    return out


# --- setup-owned agents and the hand-off authorisation (Codex R-027 AAX f2 #1, #2) ----------------
def _agents_dir(registry_root: Path) -> Path:
    return Path(registry_root) / "_setup" / "_agents"


def mark_setup_agent(registry_root: Path, agent_id: str, source_id: str, *, by: str) -> Path:
    """Mark `agent_id` as SET UP for `source_id`. From then on the manager activates it only
    under a person's hand-off authorisation, and syncs it only with that authorisation and an
    in-force sealed delegation (generic routes included)."""
    d = _agents_dir(registry_root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{agent_id}.json"
    if not p.is_file():
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps({"agent_id": agent_id, "source_id": source_id, "by": by,
                                   "at": _now().isoformat()}, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)
    return p


def setup_source_of(registry_root: Path, agent_id: str) -> Optional[str]:
    """The source a setup-owned agent was set up for, or None (not set up here). An unreadable
    mark fails CLOSED: it names a source no hand-off can satisfy."""
    p = _agents_dir(registry_root) / f"{agent_id}.json"
    if not p.is_file():
        return None
    try:
        return str(json.loads(p.read_text(encoding="utf-8"))["source_id"])
    except (OSError, ValueError, KeyError, TypeError):
        return "\x00unreadable-setup-mark"


def documents_ledger(registry_root: Path, source_id: str) -> DecisionLedger:
    """The setup documents decisions as an R-028 decision ledger: sealed under the deployment
    approval key, hash-chained, anchored, verified before every append and read."""
    return DecisionLedger(setup_dir(registry_root, source_id) / "decisions.jsonl",
                          what="setup documents decision log")


def handoff_ledger(registry_root: Path, source_id: str) -> DecisionLedger:
    """The setup hand-off authorisations as an R-028 decision ledger."""
    return DecisionLedger(setup_dir(registry_root, source_id) / "handoffs.jsonl",
                          what="setup hand-off log")


def _latest(ledger: DecisionLedger, keep) -> Optional[dict]:
    """The latest verified record ``keep`` selects, None when there is none, or a record carrying
    the refusal envelope (``refusal``: `LEDGER_INTEGRITY_FAILED`) when the log does not verify —
    never a record from a log that does not verify."""
    try:
        recs = [r for r in ledger.read() if keep(r)]
    except DecisionRefused as exc:
        return {**refusal(exc.code, exc.message), "integrity": "failed"}
    return recs[-1] if recs else None


def record_documents_decision(registry_root: Path, source_id: str, *, who: dict,
                              discovery: dict, decision: str = "build") -> dict:
    """A PERSON's decision on the documents a discovery captured: one sealed record in the
    decision ledger, carrying the actor object (R-028 #2/#7). It names how many it builds and how
    many already-built ones the keeper keeps (W-05; ``adopt`` when there is nothing to build). A machine actor or a delegation is
    refused (`MACHINE_ACTOR`); a log that does not verify refuses (`LEDGER_INTEGRITY_FAILED`)."""
    actor = require_person(who, what="a documents decision")
    build, keep = document_items(discovery)
    if decision == "build" and not build and keep:
        decision = "adopt"            # W-05: nothing new to build; the built ones are kept
    return documents_ledger(registry_root, source_id).append({
        "kind": "documents", "decision": decision, **actor,
        "discovery_run_id": discovery.get("run_id"),
        "documents_digest": documents_digest(discovery), "to_build": len(build),
        "to_keep": len(keep), "at": _now().isoformat()})


def documents_decision(registry_root: Path, source_id: str) -> Optional[dict]:
    return _latest(documents_ledger(registry_root, source_id),
                   lambda r: r.get("kind") == "documents")


def gate_binding(*, plan_digest: Optional[str], discovery: Optional[dict], mapping: Any,
                 explanation: Optional[dict], documents: Optional[dict],
                 deferred: list[str]) -> dict:
    """What a hand-off authorisation covers: the plan, the discovery, the explanation, every
    table's mapping digest, the documents decided and the decisions deferred."""
    from databridge_mcp_ingest.mapping import mapping_digest  # noqa: PLC0415

    return {"plan_digest": plan_digest, "discovery_run_id": (discovery or {}).get("run_id"),
            "explanation_digest": (explanation or {}).get("mapping_digest"),
            "mapping": {t: mapping_digest(tm)
                        for t, tm in sorted((getattr(mapping, "tables", None) or {}).items())},
            "documents_digest": (documents or {}).get("documents_digest"),
            "deferred": sorted(set(deferred))}


def record_authorization(registry_root: Path, source_id: str, agent_id: str, *, who: dict,
                         binding: dict) -> dict:
    """A PERSON authorises the hand-off: one sealed record in the decision ledger with the actor
    object (R-028 #2/#7). A machine actor or a delegation is refused (`MACHINE_ACTOR`)."""
    actor = require_person(who, what="a hand-off authorisation")
    return handoff_ledger(registry_root, source_id).append({
        "agent_id": agent_id, "source_id": source_id, **actor, "binding": binding,
        "at": _now().isoformat()})


def latest_authorization(registry_root: Path, source_id: str, agent_id: str) -> Optional[dict]:
    return _latest(handoff_ledger(registry_root, source_id),
                   lambda r: r.get("agent_id") == agent_id)


def load_gate(source_id: str, registry_root: Path, *, plan_stale: Optional[str]) -> dict:
    """Everything the hand-off gate reads, from disk: the plan, the latest discovery, the
    mapping, the explanation, the documents decision, the problems and the pending decisions."""
    from databridge_graph.packs import active_vocabulary  # noqa: PLC0415
    from databridge_mcp_ingest.mapping import load_mapping  # noqa: PLC0415
    from databridge_mcp_ingest.plans import load_plan  # noqa: PLC0415
    from databridge_mcp_ingest.runner import latest_discovery, plan_digest  # noqa: PLC0415

    try:
        plan = load_plan(source_id)
    except Exception:                                                # noqa: BLE001
        plan = None
    try:
        mapping = load_mapping(source_id)
    except Exception:                                                # noqa: BLE001
        mapping = None
    try:
        discovery = latest_discovery(source_id)
    except Exception as exc:                                         # noqa: BLE001
        discovery = {"run_id": None, "status": "unreadable", "message": str(exc), "tables": {}}
    vocab = active_vocabulary()
    explanation = load_explanation(registry_root, source_id)
    docs = documents_decision(registry_root, source_id)
    digest = plan_digest(plan) if plan is not None else None
    plan_status = getattr(plan, "status", None)
    return {"plan": plan, "plan_status": plan_status, "stale": plan_stale,
            "plan_digest": digest, "discovery": discovery, "mapping": mapping,
            "explanation": explanation, "documents_decision": docs, "vocab": vocab,
            "problems": handoff_problems(source_id, plan_status=plan_status,
                                         plan_stale=plan_stale, plan_digest=digest,
                                         discovery=discovery, mapping=mapping,
                                         explanation=explanation, vocab=vocab,
                                         documents_decision=docs),
            "pending": pending_decisions(source_id, mapping=mapping, discovery=discovery,
                                         vocab=vocab)}


def authorization_problems(registry_root: Path, source_id: str, agent_id: str, *,
                           plan_stale: Optional[str], activating: bool) -> list[str]:
    """Why the manager may not activate (``activating``) or keep running a SETUP agent: a person
    must have authorised its hand-off (append-only record) - and, to activate, that authorisation
    must cover exactly the setup as it is now (the gate passes, the binding is unchanged, every
    pending decision was deferred by name)."""
    from .adapt import _MACHINE  # noqa: PLC0415

    auth = latest_authorization(registry_root, source_id, agent_id)
    if auth is not None and auth.get("refusal"):
        return [f"the hand-off log of {agent_id} does not verify ({auth['refusal']['code']}); "
                f"nothing in it is trusted - hand off again"]
    if auth is None:
        return [f"no person has authorised the hand-off of {agent_id} for {source_id}; finish "
                f"Set up (it is activated there, by a person)"]
    pid = str(auth.get("principal_id") or "")
    if not pid or pid.lower().startswith(_MACHINE):
        return [f"the hand-off of {agent_id} was not authorised by a signed-in person"]
    if not activating:
        return []
    g = load_gate(source_id, registry_root, plan_stale=plan_stale)
    out = list(g["problems"])
    keys = sorted({d["key"] for d in g["pending"]})
    now = gate_binding(plan_digest=g["plan_digest"], discovery=g["discovery"],
                       mapping=g["mapping"], explanation=g["explanation"],
                       documents=g["documents_decision"], deferred=keys)
    if now != auth.get("binding"):
        changed = sorted(k for k in now if now.get(k) != (auth.get("binding") or {}).get(k))
        out.append(f"the setup changed since {pid} authorised the hand-off ({', '.join(changed)})"
                   f"; hand off again")
    return out


__all__ = ["CADENCES", "SETUP_KIND", "agent_principal", "agent_actor", "approved_by_people",
           "draft_spec", "documents_ledger", "handoff_ledger",
           "discovery_problems", "explanation_problems", "handoff_problems",
           "documents_digest", "documents_problems", "document_items", "display_ref",
           "DOCUMENT_DECISIONS", "pending_decisions", "mark_setup_agent",
           "setup_source_of", "record_documents_decision", "documents_decision", "gate_binding",
           "record_authorization", "latest_authorization", "load_gate",
           "authorization_problems", "keeper_id", "load_explanation", "record_step", "save_explanation",
           "setup_dir", "setup_records"]
