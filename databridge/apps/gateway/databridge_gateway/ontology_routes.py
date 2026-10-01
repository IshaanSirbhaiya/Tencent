"""``/ontology`` — the pack's node and edge library, its growth proposals, and the edits.

Three things on one surface (`PHASE_PLAN.md` Phase 4, `DEMO_AND_FRONTEND.md` §2):

* **what the active pack declares** — every node and edge kind, with its description, the
  domain-free meta-kind it binds to, and whether it may be edited at all;
* **pending growth proposals** — an adapter agent proposing a kind the pack does not have, each
  carrying the reason it was escalated rather than auto-approved;
* **the auto-approve setting** — the toggle `PHASE_PLAN` Phase 4 asks for, and the two classes of
  proposal it can never reach.

The refusal this module exists for
----------------------------------

**A determination kind and a subject-touching kind can never be auto-approved, and the refusal is
here — at the API boundary — not in the UI.** A guardrail that lives in a React component is a
guardrail that a `curl` walks around, and this is the exact guardrail that decides whether a
machine may coin the edge on which an identity determination is written.

Both classes are *computed*, never listed:

* a **determination kind** is an edge binding to a meta-kind in
  ``databridge_graph.meta.ATTESTED_META_KINDS`` — the imported set, so if the meta-model grows a
  second attested kind this refusal covers it the same day;
* a **claim kind** is an edge binding to ``CLAIM_META_KINDS`` — the edges that carry an unsettled
  identity, and therefore the caveat every surface must render;
* a **subject-touching kind** is any proposal naming a kind the active packs declared a *subject*
  (spine D4b) at either end, or a node proposal that asks to be a subject itself.

A proposal in any of those classes is recorded as ``escalated`` with its reason, and it stays
escalated whatever the auto-approve toggle says. A named human can approve it — that is what
escalation *is* — but nothing else can.

Edits write back to pack YAML
-----------------------------

The edit path is **propose → decide → write**, and there is deliberately no route that edits a
kind in place. Every change to the vocabulary therefore has a proposer, a decider and a reason
recorded beside it before it reaches a file — which is the property a growth protocol exists to
have, and an in-place `PATCH` would be the one way to get a vocabulary change with none of them.
Each kind carries `editable_via` naming the route that can change it, so `editable: true` is a
claim this API backs rather than a colour on a screen.

An approval appends the kind to ``packs/<pack>/ontology.yaml``, in the right block, with a comment
naming who approved it and when. Appending text rather than re-dumping the file is deliberate: the
pack files carry the reasoning behind every kind in comments, and a round-trip through
``yaml.safe_dump`` would delete all of it. After the write the pack is re-loaded; if it will not
load, the previous file content is restored and the request is refused. A half-written ontology is
a vocabulary the engine cannot bind.

State that is not the pack's — the proposals themselves and the toggle — lives in
``.databridge/ontology_state.json`` (git-ignored operator state), overridable with
``WALNUT_ONTOLOGY_STATE_PATH``.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import pathlib
import re
import contextlib
import threading
import unicodedata
from typing import Any, Iterator, Optional

import yaml
from fastapi import Body, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from databridge_graph.ontology_state import (
    OntologyStateError, atomic_write_text, file_lock, read_state, state_lock_path, write_state)

from .auth import AuthRequired, AuthenticatedPrincipal, install_auth_handler, require_principal

log = logging.getLogger("databridge.gateway.ontology")

_LOCK = threading.RLock()

#: A kind name is the node-id prefix, so it carries the loader's own rule.
_KIND_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

#: The two axes a proposal may be on.
AXES = ("node", "edge")


#: INT1 (2026-10-01): every route in this module requires an authenticated principal — the gateway
#: serves nothing anonymously except `/health` (`test_every_route_on_the_app_is_gated`).
_AUTH = [Depends(require_principal)]


def _refusal(code: str, message: str, status: int = 422,
             nearest_alternative: Optional[str] = None, **extra: Any) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message,
                            "nearest_alternative": nearest_alternative}
    body.update(extra)
    return JSONResponse(status_code=status, content={"refusal": body})


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _repo_root() -> pathlib.Path:
    for parent in pathlib.Path(__file__).resolve().parents:
        if (parent / "Hack4Health").is_dir() and (parent / "databridge").is_dir():
            return parent
    return pathlib.Path.cwd()                                        # pragma: no cover


def state_path() -> pathlib.Path:
    # One resolver for the writer (here) and the reader (extraction, via databridge_graph), so a
    # deprecation made on this page is the one extraction honours.
    from databridge_graph.ontology_state import state_path as _shared_state_path  # noqa: PLC0415
    return _shared_state_path()


EMPTY_STATE: dict[str, Any] = {
    # Off by default, and the default is the decision: a deployment that has never made a choice
    # must not be one where a machine may widen the vocabulary unattended.
    "auto_approve": {"enabled": False, "set_by": None, "set_at": None},
    "proposals": [],
    # Removal, honestly. A kind is never deleted from `ontology.yaml` through this API: the store
    # reads a node's kind back through `NodeKind(row["kind"])`, which resolves through the CURRENT
    # vocabulary — so deleting a declaration a node already used would make every existing node of
    # that kind unreadable, not merely un-addable. That is the opposite of what "remove" is asked
    # for here. So "remove" DEPRECATES: the kind stays declared (existing graph rows keep reading
    # back exactly as they always did), and this list is the operator-visible record of which kinds
    # are no longer offered for new proposals. `propose_kind` below refuses an edge proposal that
    # would extend a deprecated node kind, so the deprecation has real effect on what can grow next,
    # without this module ever touching a graph row it cannot safely reason about.
    "deprecated_kinds": [],
    # The recoverable-operation journal (R-019 findings 3 and 4): a multi-file change (pack YAML
    # + this state) records its intent here BEFORE touching the pack, and its completion after.
    # An entry left ``in_progress`` is an operation a crash interrupted; retrying the same request
    # resumes it idempotently.
    "operations": [],
}


def load_state() -> dict[str, Any]:
    """The operator state, merged over `EMPTY_STATE`.

    An ABSENT file is empty state. A file that exists but cannot be read raises
    `OntologyStateError` (review R-017 finding 8): it used to read as empty, which lifted every
    deprecation for extraction and let the next write here overwrite the damaged file from an empty
    baseline — losing the proposal and deprecation history. The routes answer 503
    ``ONTOLOGY_STATE_UNREADABLE`` instead and write nothing."""
    data = read_state()
    merged = json.loads(json.dumps(EMPTY_STATE))
    merged.update({k: v for k, v in data.items() if k in EMPTY_STATE})
    if not isinstance(merged.get("auto_approve"), dict):
        raise OntologyStateError(f"ontology state at {state_path()}: 'auto_approve' is not an object")
    return merged


def save_state(state: dict[str, Any]) -> None:
    """Atomic replace (temp + fsync + ``os.replace``). Read-modify-write callers use `state_txn`."""
    write_state(state)


@contextlib.contextmanager
def state_txn() -> Iterator[dict[str, Any]]:
    """One read-modify-write of the operator state: the thread lock AND a cross-process file lock
    are held from the read to the atomic write, so two workers cannot lose each other's records.
    The state is saved only if the body completes without raising."""
    with _LOCK, file_lock(state_lock_path()):
        state = load_state()
        yield state
        save_state(state)


# --- the guardrail -------------------------------------------------------------------------


class GuardrailVerdict(BaseModel):
    """Why a proposal may not be auto-approved, or that nothing objected."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    auto_approvable: bool
    reasons: tuple[str, ...] = ()
    classes: tuple[str, ...] = ()
    #: The engine's own outcome — ``approved`` / ``escalated`` / ``refused``. Kept, not collapsed
    #: into `auto_approvable` (review R-017 finding 3): a REFUSED proposal is not a question for a
    #: human, and collapsing it into "not auto-approvable" turned it into an approvable escalation.
    outcome: str = "escalated"
    #: Why the engine refused, when it did. Refusals carry no trigger findings, only reasoning.
    refusal_reason: Optional[str] = None


def _subject_kinds() -> set[str]:
    from databridge_graph.packs import active_vocabulary

    return set(active_vocabulary().subject_kinds)


def _guardrail_vocab(pack: str):
    """The vocabulary a proposal against `pack` is judged against.

    `evaluate_proposal(proposal)` with no `vocab` defaults to `active_vocabulary()` — the
    DEPLOYMENT's bound packs, whichever pack the proposal actually names. That is right when
    `pack` IS the bound one (the only case the existing tests exercise) and wrong the moment this
    surface lets an operator view a pack that exists on disk but is not part of this deployment's
    binding — `common`, or `finance`/`government` before they are enabled. A proposal against
    THAT pack would be checked against someone else's subjects and someone else's near-duplicate
    list instead of its own, which is not a hole an attacker walks through so much as a check that
    silently answers the wrong question.

    So the check runs against the UNION of the named pack and whatever the deployment has bound —
    never a SMALLER vocabulary than `active_vocabulary()` alone would give (nothing already
    protected becomes less protected), and never blind to the pack actually being edited.

    Review R-019 finding 1 (Critical): this used to return ``None`` on any failure, and the caller
    then fell back to the deployment's vocabulary ALONE. When the named pack declares a kind as a
    SUBJECT that a bound pack declares as an ordinary node, the union cannot build — and the
    fallback judged the proposal against the vocabulary in which that kind is NOT a subject, so a
    subject-touching proposal could auto-approve. Now see `_guardrail_vocabs`: a union that cannot
    merge is judged against EACH vocabulary separately (the strictest verdict wins), and any
    other failure raises `GuardrailVocabularyUnavailable` — never a fallback.
    Kept for callers that want one vocabulary: the union, or the exception.
    """
    vocabs = _guardrail_vocabs(pack)
    if len(vocabs) != 1:
        raise GuardrailVocabularyUnavailable(
            f"the vocabulary of pack {pack!r} cannot be merged with the deployment's bound packs")
    return vocabs[0]


class GuardrailVocabularyUnavailable(RuntimeError):
    """The vocabulary a proposal must be judged against could not be established. The guardrail
    FAILS CLOSED on this: the proposal is refused with ``GUARDRAIL_VOCABULARY_UNAVAILABLE`` and
    nothing is recorded — it is never judged against some other vocabulary instead."""


def _guardrail_vocabs(pack: str) -> tuple:
    """The vocabularies a proposal against ``pack`` must satisfy — ALL of them (R-019 finding 1).

    * the union of the deployment's bound packs and ``pack``, when it builds (the common case);
    * otherwise — the named pack conflicts with a bound pack, e.g. one declares ``person`` a
      subject and the other an ordinary node — the bound vocabulary AND the named pack's own,
      separately, so a kind that is a subject in EITHER is a subject for the guardrail;
    * a binding that cannot be read, or either vocabulary failing to build on its own, raises
      `GuardrailVocabularyUnavailable`. There is no fallback vocabulary."""
    from databridge_graph.packs import PackError, build_vocabulary, configured_packs, packs_root

    root = packs_root()
    try:
        bound = tuple(configured_packs(root))
    except PackError as exc:
        raise GuardrailVocabularyUnavailable(
            f"the deployment binding could not be read, so there is no vocabulary to judge the "
            f"proposal against: {exc}") from exc
    names = tuple(dict.fromkeys((*bound, pack))) if pack else bound
    if not names:
        raise GuardrailVocabularyUnavailable("no pack is bound and none was named")
    try:
        return (build_vocabulary(names, root),)
    except PackError as union_exc:
        if not pack or pack in bound:
            raise GuardrailVocabularyUnavailable(
                f"the bound vocabulary does not build: {union_exc}") from union_exc
        try:
            return (build_vocabulary(bound, root), build_vocabulary((pack,), root))
        except PackError as exc:
            raise GuardrailVocabularyUnavailable(
                f"pack {pack!r} and the bound packs {list(bound)} cannot be merged "
                f"({union_exc}), and one of them does not build on its own: {exc}") from exc


def guardrail(*, axis: str, binds_to: str, src: tuple[str, ...] = (),
              dst: tuple[str, ...] = (), declares_subject: bool = False,
              name: str = "proposed_kind", pack: str = "", proposed_by: str = "api",
              evidence: tuple = ()) -> GuardrailVerdict:
    """Does this proposal fire the guardrail? **Delegates to the engine's evaluator.**

    This function used to reimplement the escalation rules against the meta-model. Both
    implementations were honest — neither used a hardcoded list — and they still diverged:
    `growth.evaluate_proposal` collects **ten** triggers, this checked **three**. So a proposal
    carrying ungrounded or untrusted evidence, or naming an undeclared endpoint, was
    auto-approvable through the API and escalated by the engine.

    That is the worst possible shape for a security control: the *front door* is more permissive
    than the thing it fronts, and every test of the engine keeps passing while the gap is open.
    Two implementations of "what a human must decide" will always drift, because only one of them
    is where anybody looks when the rule changes.

    So there is now one evaluator, and this is a thin translation of its verdict into the shape the
    route already returns. The structural escalations (determination, identity claim,
    subject-touching, subject-declaring) remain unwaivable there, computed by subtracting them
    before `auto_approve` is read — see `growth.STRUCTURAL_ESCALATIONS`.
    """
    from databridge_graph.growth import (
        GrowthProposal, ProposalAxis, evaluate_proposal)

    from databridge_graph.growth import Outcome

    proposal = GrowthProposal(
        axis=ProposalAxis(axis),
        name=name,
        binds_to=binds_to,
        description="(guardrail pre-check)",
        pack=pack or _pack_for_guardrail(),
        proposed_by=proposed_by,
        evidence=tuple(evidence),
        src=tuple(src),
        dst=tuple(dst),
        proposes_subject=declares_subject,
    )
    # Judged against EVERY vocabulary `_guardrail_vocabs` returns; the strictest verdict wins and
    # every finding is kept (R-019 finding 1). It raises rather than falling back.
    decisions = [evaluate_proposal(proposal, vocab=v) for v in _guardrail_vocabs(proposal.pack)]
    severity = {Outcome.APPROVED: 0, Outcome.ESCALATED: 1, Outcome.REFUSED: 2}
    decision = max(decisions, key=lambda d: severity[d.outcome])
    all_findings = [f for d in decisions for f in d.findings]

    # `classes` is the API's coarser vocabulary; map the engine's triggers onto it so the route's
    # response shape is unchanged for existing consumers. An unmapped trigger passes through under
    # its own name rather than being dropped — a trigger the API cannot name is still a trigger,
    # and silently discarding it would restore exactly the permissiveness this delegation removed.
    _CLASS_OF = {
        "binds_to_attested_meta_kind": "determination",
        "binds_to_claim_meta_kind": "identity_claim",
        "touches_subject_kind": "subject_touching",
        "proposes_a_subject": "subject_touching",
    }
    classes: list[str] = []
    reasons: list[str] = []
    for finding in all_findings:
        code = getattr(finding.code, "value", str(finding.code))
        if finding.detail not in reasons:
            reasons.append(finding.detail)
        classes.append(_CLASS_OF.get(code, code))

    # Only APPROVED is auto-approvable. ESCALATED needs a human; REFUSED is not a question at all
    # (a name that could not load, or a kind the library already holds) — and neither may pass.
    return GuardrailVerdict(
        auto_approvable=decision.outcome is Outcome.APPROVED,
        reasons=tuple(reasons),
        classes=tuple(dict.fromkeys(classes)),
        outcome=decision.outcome.value,
        refusal_reason=(decision.reasoning if decision.outcome is Outcome.REFUSED else None))



def _evidence_pointers(raw: tuple[dict, ...]) -> tuple:
    """Parse evidence dicts into real `SourcePointer`s.

    A malformed pointer RAISES rather than being skipped: silently dropping one turns a grounded
    proposal into an ungrounded one, which changes the decision from approvable to escalated (or,
    worse, the other way if the drop went unnoticed). The proposer is told what was wrong.
    """
    from databridge_core import SourcePointer

    out = []
    for i, item in enumerate(raw):
        try:
            out.append(SourcePointer.model_validate(item))
        except Exception as exc:                                     # noqa: BLE001
            raise ValueError(f"evidence[{i}] is not a valid SourcePointer: {exc}") from None
    return tuple(out)


def _pack_for_guardrail() -> str:
    """The pack a bare guardrail pre-check is evaluated against.

    `deployment()` is a context MANAGER for temporarily binding packs, not an accessor for the
    current binding — reading it as one returned an empty name, and `GrowthProposal` correctly
    refuses a proposal that names no pack. The active binding is on the `Vocabulary`.

    Which pack is chosen barely matters here: this pre-check asks about meta-kinds and subject
    kinds, which are properties of the deployment as a whole. It matters that it is a REAL enabled
    pack, because the evaluator resolves the vocabulary through it.
    """
    from databridge_graph.packs import active_vocabulary

    packs = active_vocabulary().packs
    if not packs:
        raise RuntimeError(
            "no pack is bound, so no proposal can be evaluated. A guardrail that answered anyway "
            "would be answering about a vocabulary that does not exist.")
    return packs[0]


# --- reading the pack ----------------------------------------------------------------------


def _approved_names(state: dict[str, Any], pack: str) -> set[tuple[str, str]]:
    """``(axis, name)`` of every approved proposal in ``pack``. The axis is part of the identity: a
    pack may declare a node and an edge of the same name, and approving one says nothing about the
    other (R-019 finding 11)."""
    return {(str(p.get("axis")), str(p.get("name"))) for p in state.get("proposals", [])
            if p.get("status") == "approved" and p.get("pack") == pack}


def _deprecation(state: dict[str, Any], pack: str, axis: str, kind: str) -> Optional[dict[str, Any]]:
    """The most recent deprecation record for this (pack, axis, kind), or ``None``.

    Last-wins rather than "the first ever", so a deprecate → reactivate → deprecate history reads
    as its current state without the earlier rows being erased from `deprecated_kinds` (append-only,
    same spirit as the proposals list and the evidence ledger)."""
    hit = None
    for d in state.get("deprecated_kinds", []):
        if d.get("pack") == pack and d.get("axis") == axis and d.get("kind") == kind:
            hit = d
    # A reactivation is appended as its own record (append-only, matching the proposals list), and
    # if it is the LAST one for this key the kind is currently offered again — the history stays
    # in the list, but "currently deprecated" reads as False.
    if hit is None or hit.get("reactivated"):
        return None
    return hit


def _pack_view(pack_name: str, state: dict[str, Any]) -> dict[str, Any]:
    """The node/edge library of one pack, with the derived columns the surface renders."""
    from databridge_graph.meta import ATTESTED_META_KINDS, CLAIM_META_KINDS
    from databridge_graph.packs import load_pack

    pack = load_pack(pack_name)
    locked_edge_metas = {m.value for m in ATTESTED_META_KINDS} | {m.value for m in CLAIM_META_KINDS}
    proposed = _approved_names(state, pack_name)

    nodes = []
    for name, decl in sorted(pack.node_kinds.items()):
        subject = pack.subject_kinds.get(name)
        dep = _deprecation(state, pack_name, "node", name)
        nodes.append({
            "kind": name,
            "binds_to": decl.binds_to.value,
            "description": decl.description,
            # The pack schema declares no cardinality, and the store has one shape for every kind:
            # ids are `kind:slug`, unbounded. So this is a property of the store, reported, rather
            # than a per-kind declaration that does not exist.
            "cardinality": "many",
            "identity": (f"attested identifiers: {', '.join(subject.identifiers)}"
                         if subject else f"node id: {name}:<slug>"),
            "source": "agent-proposed" if ("node", name) in proposed else "predefined",
            "subject": subject is not None,
            "attestation": subject.attestation if subject else None,
            # A subject kind is not editable through this API: editing it is editing the identity
            # guarantee, and that is an ADR, not an HTTP request. `editable_via` names the route
            # that can change it, so "editable: true" is a claim the API actually backs rather
            # than a colour on a screen.
            "editable": subject is None,
            "identity_protected": subject is not None,
            "identifiers": list(subject.identifiers) if subject else [],
            "grant_type": subject.grant_type if subject else None,
            "editable_via": ("POST /ontology/kinds/* with confirm_identity: true"
                             if subject is not None else
                             "POST /ontology/kinds/* (owner) or POST /ontology/proposals (agent)"),
            "pack": pack_name,
            "deprecated": dep is not None,
            "deprecated_by": dep.get("by") if dep else None,
            "deprecated_at": dep.get("at") if dep else None,
            "deprecated_reason": dep.get("reason") if dep else None,
        })

    edges = []
    for name, decl in sorted(pack.edge_kinds.items()):
        locked = decl.binds_to.value in locked_edge_metas
        dep = _deprecation(state, pack_name, "edge", name)
        edges.append({
            "kind": name,
            "binds_to": decl.binds_to.value,
            "from": list(decl.src),
            "to": list(decl.dst),
            "description": decl.description,
            "source": "agent-proposed" if ("edge", name) in proposed else "predefined",
            "editable": not locked,
            "identity_protected": locked,
            "editable_via": ("POST /ontology/kinds/* with confirm_identity: true" if locked else
                             "POST /ontology/kinds/* (owner) or POST /ontology/proposals (agent)"),
            "carries_identity_rule": locked,
            "pack": pack_name,
            "deprecated": dep is not None,
            "deprecated_by": dep.get("by") if dep else None,
            "deprecated_at": dep.get("at") if dep else None,
            "deprecated_reason": dep.get("reason") if dep else None,
        })
    return {"pack": pack_name, "title": pack.title, "version": pack.version,
            "node_kinds": nodes, "edge_kinds": edges}


def _meta_kind_catalog() -> dict[str, Any]:
    """The closed meta-model, as the API's one source of truth for it.

    The frontend must never hardcode which edge meta-kinds are identity-bearing — that is exactly
    the fact this whole surface exists to make unmissable, and a copy of it in TypeScript would be
    a second place for `ATTESTED_META_KINDS`/`CLAIM_META_KINDS` to drift from `meta.py`. So it is
    served, computed from the same enums the guardrail itself reads.
    """
    from databridge_graph.meta import (
        ATTESTED_META_KINDS,
        CLAIM_META_KINDS,
        SUBJECT_ELIGIBLE_META_KINDS,
        EdgeMetaKind,
        NodeMetaKind,
    )

    subject_eligible = {m.value for m in SUBJECT_ELIGIBLE_META_KINDS}
    attested = {m.value for m in ATTESTED_META_KINDS}
    claim = {m.value for m in CLAIM_META_KINDS}
    explain = meta_kind_explanations()
    return {
        "node": [{"value": m.value, "subject_eligible": m.value in subject_eligible,
                  "explanation": explain.get(m.value)}
                 for m in NodeMetaKind],
        "edge": [{"value": m.value, "attested": m.value in attested, "claim": m.value in claim,
                  "explanation": explain.get(m.value)}
                 for m in EdgeMetaKind],
    }


def _attestation_modes() -> list[str]:
    from databridge_graph.packs import ATTESTATION_MODES

    return sorted(ATTESTATION_MODES)


def meta_kind_explanations() -> dict[str, str]:
    """The plain-English line for each meta-kind, read from ``databridge_graph.meta``'s own
    docstring — the one place those meanings are written down. Parsed, not copied: a second copy in
    this module (or in TypeScript) would be a place for the meaning to drift from the meta-model.
    A meta-kind the docstring does not explain simply has no entry, and the UI says so."""
    from databridge_graph import meta

    out: dict[str, str] = {}
    for line in (meta.__doc__ or "").splitlines():
        m = re.match(r"^\s{2}([A-Z][A-Z_]*)\s{2,}(\S.*)$", line)
        if m:
            out.setdefault(m.group(1).lower(), m.group(2).strip())
    return out


# --- writing the pack ----------------------------------------------------------------------


class PackWriteError(RuntimeError):
    """The pack file could not be extended safely. The file is left as it was."""


def append_kind_to_pack(pack: str, axis: str, entry: dict[str, Any], *,
                        by: str, at: str, root: Optional[pathlib.Path] = None) -> pathlib.Path:
    """Append one approved kind to ``packs/<pack>/ontology.yaml`` through the SAME verified writer
    as an owner edit (`owner_add_entries` → `_verify_and_commit`).

    This used to be a second, weaker writer (review R-017 findings 1, 4, 5): it interpolated the
    approver's name into a YAML comment unescaped — a ``decided_by`` carrying a newline injected a
    second, unreviewed list entry — wrote with ``Path.write_text`` (truncate-then-write, so a failed
    write could leave the file half-written), checked only that the one pack loaded (never that the
    deployment's bound vocabulary still built), and never compared the parsed file with what was
    approved. Now: every comment line is refused if it carries a control character, the parsed file
    must differ from the original by exactly the approved entry, the write is atomic, and the bound
    vocabulary must still build — or the original bytes are restored and `PackWriteError` raised.
    Text-level, never a re-dump, so the comments carrying each kind's reasoning survive.
    """
    block_key = "node_kinds" if axis == "node" else "edge_kinds"
    if _CONTROL.search(by) or _CONTROL.search(at):
        # Checked on the VALUES, before the comment is assembled: the comment's own line breaks
        # are legitimate, but one arriving inside an identity would be split into lines that
        # misattribute the approval (and, before R-017, became YAML).
        raise PackWriteError("refusing to write an approver identity containing a control or "
                             "line-separator character into the pack file")
    comment = (f"Added through POST /ontology, approved by {by} at {at}. The approval is the\n"
               f"signed-in principal's; see .databridge/ontology_state.json for the proposal it\n"
               f"came from and why it was escalated or not.")
    return owner_add_entries(pack, [(block_key, dict(entry))], comment=comment, root=root)


# --- owner edits: rendering, endpoint rewrites, verification -------------------------------
#
# The owner-edit path (`POST /ontology/kinds`, `/kinds/rename`, `/kinds/endpoints`) writes the pack
# file directly — the founder's requirement is that *the owner defines* the vocabulary and the
# system maps only to it — but under the same three disciplines as an approved proposal:
#
# 1. text-level edits, never a YAML re-dump, so the comments carrying each kind's reasoning survive;
# 2. after the edit, the file is re-parsed and compared with what it was: the ONLY difference
#    allowed is the one the request asked for. Anything else (a stray indent that moved an entry
#    into the wrong block, a comment that swallowed a line) restores the original and refuses;
# 3. the pack must still load on its own AND, when bound, the deployment's vocabulary must still
#    build — a new subject kind that conflicts with another bound pack's subject of the same name
#    is exactly the conflict `build_vocabulary` refuses, and it must be refused here, not at the
#    next boot.


def _scalar(value: Any) -> str:
    """One YAML scalar. JSON's string/number/bool spellings are valid YAML, and quoting every
    string means a name like ``*`` or ``on`` is never re-read as an alias or a boolean. A plain
    snake_case name that YAML cannot misread is left bare, matching how the shipped packs read."""
    if (isinstance(value, str) and _KIND_NAME.match(value)
            and value not in _YAML_RESERVED):
        return value
    # JSON escapes C0 controls, quotes and backslashes; YAML additionally treats NEL, LS and PS as
    # line breaks and refuses other C1 controls, which JSON leaves raw with ensure_ascii=False.
    # Escape those as \uXXXX (valid in a JSON string and a YAML double-quoted scalar alike), so a
    # description can never break out of its scalar (review R-017 finding 1).
    return _YAML_UNSAFE.sub(lambda m: "\\u%04x" % ord(m.group()),
                            json.dumps(value, ensure_ascii=False))


#: Characters YAML reads as a line break or refuses as non-printable, that JSON leaves raw.
_YAML_UNSAFE = re.compile("[\u007f-\u009f  ﻿￾￿\ud800-\udfff]")

#: Anything that may end a line (or confuse a reader) in text written into a YAML comment. Tab is
#: the one control character allowed.
_CONTROL = re.compile("[\u0000-\u0008\u000a-\u001f\u007f-\u009f  ]")


def _comment_lines(comment: str, indent: str) -> list[str]:
    """``comment`` as YAML comment lines at ``indent``. Split on the caller's own ``\\n`` only, then
    every line is checked: a control character or a Unicode line/paragraph separator surviving the
    split could let text escape the comment and become YAML, so it is REFUSED — never silently
    rewritten, because the file must say exactly what was approved (review R-017 finding 1)."""
    out: list[str] = []
    for ln in comment.split("\n"):
        if _CONTROL.search(ln):
            raise PackWriteError(
                "refusing to write a comment containing a control or line-separator character — "
                "it could escape the comment and inject YAML")
        out.append(f"{indent}# {ln}\n" if ln else f"{indent}#\n")
    return out


#: Plain scalars YAML 1.1 (PyYAML) resolves to something other than a string.
_YAML_RESERVED = frozenset({"y", "n", "yes", "no", "on", "off", "true", "false", "null"})


def render_entry(entry: dict[str, Any], indent: str = "  ") -> str:
    """One list entry of a pack block, lists in flow style (``src: ["a", "b"]``) — the style every
    shipped pack uses, and the one `_rewrite_list_key` knows how to edit later."""
    out = []
    for i, (key, value) in enumerate(entry.items()):
        prefix = f"{indent}- " if i == 0 else f"{indent}  "
        if isinstance(value, (list, tuple)):
            rendered = "[" + ", ".join(_scalar(v) for v in value) + "]"
        else:
            rendered = _scalar(value)
        out.append(f"{prefix}{key}: {rendered}\n")
    return "".join(out)


def _block_span(lines: list[str], block_key: str) -> Optional[tuple[int, int]]:
    """``(start, end)`` line indexes of a top-level block (``start`` is the ``key:`` line)."""
    start = next((i for i, line in enumerate(lines) if line.startswith(f"{block_key}:")), None)
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^[A-Za-z_]", lines[i]):
            end = i
            break
    return start, end


def _entry_span(lines: list[str], block: tuple[int, int], key: str,
                name: str) -> Optional[tuple[int, int]]:
    """The lines of the entry ``- <key>: <name>`` inside ``block``."""
    start, end = block
    pat = re.compile(rf"^(\s*)-\s+{key}:\s*[\"']?{re.escape(name)}[\"']?\s*(#.*)?$")
    for i in range(start + 1, end):
        m = pat.match(lines[i].rstrip("\r\n"))
        if not m:
            continue
        dash_indent = len(m.group(1))
        stop = end
        for j in range(i + 1, end):
            text = lines[j].rstrip("\r\n")
            if not text.strip() or text.lstrip().startswith("#"):
                continue
            ind = len(text) - len(text.lstrip())
            if ind <= dash_indent:
                stop = j
                break
        # Trailing blank/comment lines belong to whatever follows, not to this entry.
        while stop - 1 > i and (not lines[stop - 1].strip()
                                or lines[stop - 1].lstrip().startswith("#")):
            stop -= 1
        return i, stop
    return None


def _rewrite_list_key(lines: list[str], entry: tuple[int, int], key: str,
                      values: list[str]) -> list[str]:
    """Replace ``key:``'s value inside one entry with a flow list, whatever style it was in
    (single-line flow, multi-line flow, or block list). Inserts the key if the entry lacks it."""
    start, stop = entry
    key_pat = re.compile(rf"^(\s*)(-\s+)?{key}:(.*)$")
    rendered_value = "[" + ", ".join(_scalar(v) for v in values) + "]"
    for i in range(start, stop):
        m = key_pat.match(lines[i].rstrip("\r\n"))
        if not m:
            continue
        lead = m.group(1) + ("  " if m.group(2) else "")
        rest = m.group(3).strip()
        j = i + 1
        if rest.startswith("["):
            depth = rest.count("[") - rest.count("]")
            while depth > 0 and j < stop:
                depth += lines[j].count("[") - lines[j].count("]")
                j += 1
        elif rest == "" or rest.startswith("#"):
            base = len(lines[i]) - len(lines[i].lstrip())
            while j < stop:
                t = lines[j].rstrip("\r\n")
                if t.strip() and (len(t) - len(t.lstrip()) >= base) and t.lstrip().startswith("- "):
                    j += 1
                    continue
                break
        prefix = (m.group(1) + m.group(2)) if m.group(2) else lead
        return lines[:i] + [f"{prefix}{key}: {rendered_value}\n"] + lines[j:]
    # Absent: insert after the entry's first line, at the entry's key indentation.
    first = lines[start]
    dash = len(first) - len(first.lstrip())
    return lines[:start + 1] + [f"{' ' * (dash + 2)}{key}: {rendered_value}\n"] + lines[start + 1:]


def _verify_and_commit(pack: str, root: pathlib.Path, path: pathlib.Path, original: str,
                       updated: str, expect) -> None:
    """Prove ``updated`` is exactly the requested change, then commit it atomically.

    Order (review R-017 finding 4 — the file can never be left truncated or unloadable):

    1. parse ``updated`` IN MEMORY and check ``expect(before, after)`` (it raises on any difference
       beyond the requested one) — before a byte touches the disk;
    2. under a cross-process lock, refuse if the file changed since ``original`` was read, so a
       concurrent writer's edit is never silently overwritten;
    3. atomic replace (temp file + fsync + ``os.replace``), keeping the file's own line endings;
    4. the pack must load on its own AND, when bound, the deployment vocabulary must still build
       (a cross-pack conflict is refused here, not discovered at the next boot — finding 5);
    5. any failure in 4 restores the original bytes, again atomically. If even that fails, the
       original is kept in a sibling ``.restore-*`` file and the error names it.
    """
    from databridge_graph.packs import build_vocabulary, clear_caches, configured_packs, load_pack

    try:
        before = yaml.safe_load(original) or {}
        after = yaml.safe_load(updated) or {}
        expect(before, after)
    except Exception as exc:                                         # noqa: BLE001
        raise PackWriteError(
            f"the edit was not applied (the file was not touched): {exc}") from exc

    with file_lock(_pack_lock_path(path)):
        raw = path.read_bytes()
        if raw.decode("utf-8").replace("\r\n", "\n") != original.replace("\r\n", "\n"):
            raise PackWriteError(
                f"{path} changed while this edit was being prepared; nothing was written — "
                f"reload and try again")
        newline = "\r\n" if b"\r\n" in raw else "\n"
        try:
            atomic_write_text(path, updated, newline=newline)
        except Exception as exc:                                     # noqa: BLE001
            clear_caches()
            raise PackWriteError(
                f"the edit could not be written; the file is unchanged: {exc}") from exc
        try:
            clear_caches()
            load_pack(pack, root, use_cache=False)
            # R-019 finding 7: a binding that cannot be read is a FAILED check, not "nothing is
            # bound" — swallowing it committed the edit without ever proving the deployment still
            # builds. `configured_packs` never returns an empty binding (it raises), so there is
            # no legitimate "no packs" case being lost here.
            bound = tuple(configured_packs(root))
            if pack in bound:
                build_vocabulary(bound, root)
        except Exception as exc:                                     # noqa: BLE001
            try:
                _restore(path, raw)
            except Exception as restore_exc:                         # noqa: BLE001
                keep = path.with_name(f"{path.name}.restore-{_now().replace(':', '')}")
                with contextlib.suppress(Exception):
                    keep.write_bytes(raw)
                clear_caches()
                raise PackWriteError(
                    f"the edit broke the vocabulary ({exc}) AND restoring the original failed "
                    f"({restore_exc}); the original bytes are kept at {keep}") from exc
            clear_caches()
            raise PackWriteError(
                f"the edit was not applied and the file has been restored: {exc}") from exc
    clear_caches()


def _pack_lock_path(path: pathlib.Path) -> pathlib.Path:
    """The cross-process lock for one pack file lives beside the operator state, never in the pack
    directory (which is content, and may be read-only or shared)."""
    import hashlib                                                   # noqa: PLC0415

    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
    return state_lock_path().with_name(f"ontology_pack_{digest}.lock")


def _restore(path: pathlib.Path, raw: bytes) -> None:
    """Put ``raw`` back at ``path`` atomically, byte-for-byte, with the file's current permission
    bits (and owner, where permitted) — R-019 finding 8."""
    import tempfile                                                  # noqa: PLC0415

    from databridge_graph.ontology_state import copy_metadata        # noqa: PLC0415

    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".restore", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        copy_metadata(path, pathlib.Path(tmp))
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _strip(data: dict[str, Any], block: str, key: str, name: str) -> dict[str, Any]:
    """``data`` with one named entry removed from ``block`` — used to state "nothing else moved"."""
    copy = json.loads(json.dumps(data, default=str))
    copy[block] = [e for e in (copy.get(block) or []) if not (isinstance(e, dict)
                                                               and e.get(key) == name)]
    return copy


def _pack_file(pack: str, root: Optional[pathlib.Path]) -> tuple[pathlib.Path, pathlib.Path]:
    from databridge_graph.packs import packs_root

    r = pathlib.Path(root) if root is not None else packs_root()
    path = r / pack / "ontology.yaml"
    if not path.is_file():
        raise PackWriteError(f"no ontology.yaml for pack {pack!r} at {path}")
    return r, path


def _lines_with_additions(lines: list[str], path: pathlib.Path,
                          additions: list[tuple[str, dict[str, Any]]],
                          comment: str) -> list[str]:
    """``lines`` with each ``(block_key, entry)`` appended to its block (text-level)."""
    lines = list(lines)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    for block_key, entry in additions:
        span = _block_span(lines, block_key)
        text = _comment_lines(comment, "  ")
        text.append(render_entry(entry))
        if span is None:
            if block_key != "subject_kinds":
                raise PackWriteError(f"{path} has no top-level {block_key!r} block to extend")
            lines += ["\n", "subject_kinds:\n", *text]
            continue
        _, end = span
        # Insert before any trailing blank lines of the block so spacing stays as it was.
        insert_at = end
        while insert_at - 1 > span[0] and (not lines[insert_at - 1].strip()
                                            or lines[insert_at - 1].startswith("#")):
            insert_at -= 1
        lines = lines[:insert_at] + ["\n", *text] + lines[insert_at:]
    return lines


def _lines_with_endpoints(lines: list[str], path: pathlib.Path, edge: str, src: list[str],
                          dst: list[str], comment: str) -> list[str]:
    """``lines`` with one edge kind's ``src``/``dst`` rewritten in place (text-level)."""
    block = _block_span(lines, "edge_kinds")
    if block is None:
        raise PackWriteError(f"{path} has no edge_kinds block")
    entry = _entry_span(lines, block, "name", edge)
    if entry is None:
        raise PackWriteError(f"could not locate edge kind {edge!r} in {path}")
    lines = _rewrite_list_key(lines, entry, "src", src)
    block = _block_span(lines, "edge_kinds")
    entry = _entry_span(lines, block, "name", edge) if block else None
    if entry is None:
        raise PackWriteError(f"lost edge kind {edge!r} while rewriting {path}")
    lines = _rewrite_list_key(lines, entry, "dst", dst)
    block = _block_span(lines, "edge_kinds")
    entry = _entry_span(lines, block, "name", edge) if block else None
    if entry is not None:
        dash = len(lines[entry[0]]) - len(lines[entry[0]].lstrip())
        note = _comment_lines(comment, " " * dash)
        lines = lines[:entry[0]] + note + lines[entry[0]:]
    return lines


def owner_apply_changes(pack: str, *, additions: list[tuple[str, dict[str, Any]]] = (),
                        endpoints: list[tuple[str, list[str], list[str]]] = (),
                        comment: str, endpoint_comment: Optional[str] = None,
                        root: Optional[pathlib.Path] = None) -> pathlib.Path:
    """Any number of entry additions AND edge-endpoint rewrites, as ONE verified atomic write.

    Review R-019 finding 4: a rename used to be one write per relation, each committed on its own,
    so a failure on the third relation left the new kind and two widened relations behind with
    the old kind still active (``RENAME_PARTIAL``). Now the complete resulting file is built in
    memory, the parsed result is compared with the parsed original — it must differ by EXACTLY
    the listed additions and endpoint changes, relation order unchanged — and it is committed by
    `_verify_and_commit` in one ``os.replace``, or not at all."""
    r, path = _pack_file(pack, root)
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    for edge, src, dst in endpoints:
        lines = _lines_with_endpoints(lines, path, edge, list(src), list(dst),
                                      endpoint_comment if endpoint_comment is not None
                                      else comment)
    lines = _lines_with_additions(lines, path, list(additions), comment)

    def expect(before: dict, after: dict) -> None:
        before_n = json.loads(json.dumps(before, default=str))
        after_n = json.loads(json.dumps(after, default=str))
        expected = json.loads(json.dumps(before_n))
        for edge, src, dst in endpoints:
            got = [e for e in (after_n.get("edge_kinds") or []) if isinstance(e, dict)
                   and e.get("name") == edge]
            was = [e for e in (before_n.get("edge_kinds") or []) if isinstance(e, dict)
                   and e.get("name") == edge]
            if len(got) != 1 or len(was) != 1:
                raise PackWriteError(f"edge kind {edge!r} is not declared exactly once after "
                                     f"the edit")
            want = {**was[0], "src": list(src), "dst": list(dst)}
            if got[0] != want:
                raise PackWriteError(f"edge kind {edge!r} did not read back as {want}")
            expected["edge_kinds"] = [want if (isinstance(e, dict) and e.get("name") == edge)
                                      else e for e in expected.get("edge_kinds") or []]
        stripped = after_n
        for block_key, entry in additions:
            key = "kind" if block_key == "subject_kinds" else "name"
            name = entry[key]
            got = [e for e in (after_n.get(block_key) or []) if isinstance(e, dict)
                   and e.get(key) == name]
            if got != [json.loads(json.dumps(entry))]:
                raise PackWriteError(f"{block_key} entry {name!r} did not read back as written")
            stripped = _strip(stripped, block_key, key, name)
        if "subject_kinds" not in expected and stripped.get("subject_kinds") == []:
            stripped.pop("subject_kinds")
        if stripped != expected:
            raise PackWriteError("the edit changed more of the file than the change it named")

    _verify_and_commit(pack, r, path, original, "".join(lines), expect)
    return path


def owner_add_entries(pack: str, additions: list[tuple[str, dict[str, Any]]], *, comment: str,
                      root: Optional[pathlib.Path] = None) -> pathlib.Path:
    """Append one or more entries (``(block_key, entry)``) to a pack file in one verified write.

    ``subject_kinds`` is created as a top-level block if the pack has none yet. Every other block
    must already exist — a pack without ``node_kinds`` or ``edge_kinds`` does not load at all.
    """
    return owner_apply_changes(pack, additions=additions, comment=comment, root=root)


def owner_set_endpoints(pack: str, edge: str, src: list[str], dst: list[str], *,
                        comment: str, root: Optional[pathlib.Path] = None) -> pathlib.Path:
    """Rewrite one edge kind's ``src``/``dst`` lists in place, verified."""
    return owner_apply_changes(pack, endpoints=[(edge, list(src), list(dst))], comment=comment,
                               root=root)


# --- audit: append-only, beside the operator state ------------------------------------------


def audit_path() -> pathlib.Path:
    override = os.environ.get("WALNUT_ONTOLOGY_AUDIT_PATH")
    if override:
        return pathlib.Path(override)
    return state_path().with_name("ontology_audit.jsonl")


class AuditWriteError(RuntimeError):
    """The audit journal could not durably take a record. Raised BEFORE any mutation when it is
    the intent record that failed, so the route changes nothing (503 ``AUDIT_UNAVAILABLE``)."""


def _audit_lock_path() -> pathlib.Path:
    p = audit_path()
    return p.with_name(p.name + ".lock")


def new_op_id() -> str:
    import uuid                                                      # noqa: PLC0415

    return f"op-{uuid.uuid4().hex}"


def append_audit(entry: dict[str, Any]) -> dict[str, Any]:
    """One JSON line per vocabulary change. Opened in append mode, one ``write`` per line — there
    is no code path in this module that rewrites or truncates this file.

    Review R-019 finding 5: serialised across PROCESSES (an OS file lock, not only the in-process
    `_LOCK`), flushed AND fsynced, and a failure raises `AuditWriteError` rather than an arbitrary
    exception. Every mutating route first writes an ``intent`` record (`audit_intent`) naming an
    ``op_id``; the mutation runs only if that record is durable, and the completion record carries
    the same ``op_id`` — so a change can never exist without a durable record linked to it."""
    record = {"at": _now(), **entry}
    path = audit_path()
    try:
        line = json.dumps(record, sort_keys=True, default=str) + "\n"
        with _LOCK, file_lock(_audit_lock_path()):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
    except (OSError, OntologyStateError, TypeError, ValueError) as exc:
        raise AuditWriteError(
            f"the ontology audit journal at {path} could not be written ({exc})") from exc
    return record


def audit_intent(action: str, **fields: Any) -> str:
    """Durably record that ``action`` is ABOUT to happen, before anything changes; returns the
    ``op_id`` the completion record must carry. Raises `AuditWriteError` — and the caller then
    changes nothing — if the journal cannot take it."""
    op_id = new_op_id()
    append_audit({"action": "intent", "intended_action": action, "op_id": op_id,
                  "phase": "intent", **fields})
    return op_id


def complete_audit(op_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    """The completion record for ``op_id``. The mutation has ALREADY happened when this runs, so a
    journal failure here does not un-happen it: it is reported on the record (``completed:
    False``) and the durable intent line stays the change's link to the ledger — `read_audit`
    serves an intent with no completion as an incomplete operation."""
    try:
        return append_audit({**entry, "op_id": op_id, "phase": "completed"})
    except AuditWriteError as exc:
        log.error("audit completion for %s failed after the change was applied: %s", op_id, exc)
        return {**entry, "op_id": op_id, "phase": "completed", "completed": False,
                "error": str(exc), "at": _now()}


def read_audit(pack: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
    """Newest first. An ``intent`` line whose ``op_id`` has a completion is folded away (the
    completion carries everything); an intent with NO completion is served, marked
    ``incomplete: true`` — a change that may have been applied without its completion record."""
    path = audit_path()
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and (pack is None or row.get("pack") == pack):
                    rows.append(row)
    except OSError:
        return []
    completed = {r.get("op_id") for r in rows if r.get("phase") != "intent" and r.get("op_id")}
    out = []
    for r in rows:
        if r.get("phase") == "intent":
            if r.get("op_id") in completed:
                continue
            r = {**r, "incomplete": True}
        out.append(r)
    return out[-limit:][::-1]


# --- what the data says: per-kind counts, and what ingestion refused -----------------------


def _resolve_store(app: FastAPI):
    """The shared store (``app.state.graph_store``, else ``app.state.brain.store``), per request —
    a reload may swap the object, so it is never cached here."""
    store = getattr(app.state, "graph_store", None)
    origin = "app.state.graph_store"
    if store is None:
        brain = getattr(app.state, "brain", None)
        store = getattr(brain, "store", None) if brain is not None else None
        origin = "app.state.brain.store"
    return store, (origin if store is not None else None)


def store_kind_counts(store) -> dict[str, Any]:
    """Current (not superseded) nodes and edges per kind, straight from SQL."""
    if store is None:
        return {"available": False, "nodes": {}, "edges": {}}
    try:
        nodes = {str(r["kind"]): int(r["n"]) for r in store.fetchall(
            "SELECT kind, COUNT(DISTINCT id) AS n FROM nodes WHERE system_to IS NULL "
            "GROUP BY kind")}
        edges = {str(r["kind"]): int(r["n"]) for r in store.fetchall(
            "SELECT kind, COUNT(DISTINCT id) AS n FROM edges WHERE system_to IS NULL "
            "GROUP BY kind")}
    except Exception as exc:                                         # noqa: BLE001
        log.warning("could not count kinds in the graph store: %s", exc)
        return {"available": False, "nodes": {}, "edges": {}, "error": str(exc)}
    return {"available": True, "nodes": nodes, "edges": edges}


#: The refusal sentences `databridge_graph.extractor.extract_graph` writes into `refused`. Matched
#: by shape, not by kind name — the kind is whatever the sentence names.
_UNKNOWN_NODE = re.compile(r"unknown kind '([A-Za-z0-9_]+)'")
_UNKNOWN_EDGE = re.compile(r"relation '([A-Za-z0-9_]+)': unknown type")
_WRONG_ENDPOINT = re.compile(r"wrong endpoint kinds for '([A-Za-z0-9_]+)'")
_MAX_REFUSALS_READ = 20_000


def _refusal_texts(obj: Any, depth: int = 0) -> list[str]:
    """Every string under any ``refused`` key, however the report nests or shapes it."""
    out: list[str] = []
    if depth > 6:
        return out
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "refused" or key == "refusals":
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            out.append(item)
                        elif isinstance(item, dict):
                            for k in ("reason", "message", "detail", "refusal", "text"):
                                if isinstance(item.get(k), str):
                                    out.append(item[k])
                                    break
            elif isinstance(value, (dict, list)):
                out.extend(_refusal_texts(value, depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_refusal_texts(item, depth + 1))
    return out


def ingest_refusals(root: Optional[pathlib.Path] = None) -> dict[str, Any]:
    """Off-ontology refusals from the LATEST ingestion run of each source.

    Reads ``var/ingest/<source>/<run>/report.json`` (and the run's ``extract_skipped.jsonl``) —
    defensively: an absent tree, an unreadable file or a report with no ``refused`` field is simply
    nothing to count. The latest run only, because an earlier run's refusals were refused against
    an earlier vocabulary.
    """
    if root is None:
        raw = os.environ.get("WALNUT_INGEST_DIR", "").strip()
        root = pathlib.Path(raw) if raw else (
            pathlib.Path(__file__).resolve().parents[3] / "var" / "ingest")
    result: dict[str, Any] = {"available": False, "runs": [], "edge_endpoint_refusals": {},
                              "unknown_node_kinds": {}, "unknown_edge_kinds": {}, "total": 0}
    if not root.is_dir():
        return result
    texts: list[str] = []
    try:
        for source_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            runs = sorted(p for p in source_dir.iterdir() if p.is_dir())
            if not runs:
                continue
            run = runs[-1]
            result["runs"].append(f"{source_dir.name}/{run.name}")
            report = run / "report.json"
            if report.is_file():
                try:
                    texts.extend(_refusal_texts(json.loads(report.read_text(encoding="utf-8"))))
                except (OSError, ValueError):
                    pass
            skipped = run / "extract_skipped.jsonl"
            if skipped.is_file():
                try:
                    with skipped.open("r", encoding="utf-8") as fh:
                        for i, line in enumerate(fh):
                            if i >= _MAX_REFUSALS_READ:
                                break
                            try:
                                texts.extend(_refusal_texts(json.loads(line)))
                            except ValueError:
                                continue
                except OSError:
                    pass
            if len(texts) >= _MAX_REFUSALS_READ:
                break
    except OSError:
        return result
    result["available"] = bool(result["runs"])
    for text in texts[:_MAX_REFUSALS_READ]:
        for pat, bucket in ((_WRONG_ENDPOINT, "edge_endpoint_refusals"),
                            (_UNKNOWN_EDGE, "unknown_edge_kinds"),
                            (_UNKNOWN_NODE, "unknown_node_kinds")):
            m = pat.search(text)
            if m:
                counts = result[bucket]
                counts[m.group(1)] = counts.get(m.group(1), 0) + 1
                result["total"] += 1
                break
    return result


def identity_classes(*, axis: str, binds_to: str, src: tuple[str, ...] = (),
                     dst: tuple[str, ...] = (), declares_subject: bool = False,
                     subjects: set[str] = frozenset()) -> list[str]:
    """Which identity-protected classes an owner edit falls in — computed from the meta-model and
    the declared subjects, never listed. Any class at all means the edit needs ``confirm_identity``.
    """
    from databridge_graph.meta import ATTESTED_META_KINDS, CLAIM_META_KINDS
    from databridge_graph.packs import ANY_KIND

    out: list[str] = []
    if axis == "node" and declares_subject:
        out.append("subject_declaration")
    if axis == "edge":
        if binds_to in {m.value for m in ATTESTED_META_KINDS}:
            out.append("determination")
        if binds_to in {m.value for m in CLAIM_META_KINDS}:
            out.append("identity_claim")
        ends = set(src) | set(dst)
        if (ends & set(subjects)) or (ANY_KIND in ends and subjects):
            out.append("subject_touching")
    return out


# --- who may do what: derived from the authenticated principal, never from the body ----------
#
# Review R-017 finding 2. `auth.require_principal` answers "who is calling"; it carries no role.
# This module needs one more fact — is that principal an ONTOLOGY OWNER (a person who may define
# the vocabulary and decide proposals) or anything else (an agent, a service, a read-only user)?
# It is read from operator configuration, never from a request:
#
# * ``WALNUT_ONTOLOGY_OWNERS`` — the principal ids (JSON list, or comma-separated) that are owners;
# * unset — only the local-demo dev principal (``principal.dev_mode``, which `auth` grants solely
#   under ``WALNUT_DEV_AUTH=1``) is an owner. Every other principal is NOT: fail closed. A
#   deployment that has not named its owners has no one who may widen its vocabulary unattended.
#
# Owners alone may: define/rename/re-endpoint kinds, deprecate/reactivate, approve/reject
# proposals, and submit ``origin: owner`` proposals. Any authenticated principal may PROPOSE.

OWNERS_ENV = "WALNUT_ONTOLOGY_OWNERS"


def ontology_owners() -> Optional[frozenset[str]]:
    """The configured owner principal ids, or ``None`` when unconfigured. Read per call."""
    raw = os.environ.get(OWNERS_ENV, "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = [p for p in raw.split(",")]
    if isinstance(parsed, str):
        parsed = [parsed]
    if not isinstance(parsed, list):
        log.error("%s must be a JSON list or a comma-separated list; treating as NO owners",
                  OWNERS_ENV)
        return frozenset()
    return frozenset(str(p).strip() for p in parsed if str(p).strip())


def is_owner(principal: AuthenticatedPrincipal) -> bool:
    owners = ontology_owners()
    if owners is None:
        return bool(principal.dev_mode)
    return principal.principal_id in owners


def require_owner(principal: AuthenticatedPrincipal = Depends(require_principal)
                  ) -> AuthenticatedPrincipal:
    """`require_principal`, plus: the principal must be an ontology owner. 403 otherwise."""
    if not is_owner(principal):
        raise AuthRequired(
            "ONTOLOGY_OWNER_REQUIRED",
            f"principal {principal.principal_id!r} is authenticated but is not an ontology owner. "
            f"Defining, changing, deprecating or deciding vocabulary is an owner's act; an agent "
            f"or service may only PROPOSE (POST /ontology/proposals). Owners are configured by "
            f"the operator in {OWNERS_ENV}, never by a request.", status_code=403)
    return principal


#: Longest display label accepted. A label is a name, not a document.
_MAX_LABEL = 200


def clean_label(value: Optional[str], field: str) -> tuple[Optional[str], Optional[str]]:
    """``(label, error)``. A display label is one line of printable text: control characters and
    Unicode line/paragraph separators are REFUSED (not stripped) — review R-017 finding 1."""
    text = (value or "").strip()
    if not text:
        return None, None
    if _CONTROL.search(text) or "\t" in text:
        return None, f"{field} must be a single line of printable text"
    if any(unicodedata.category(ch) in {"Cs", "Co", "Cn"} for ch in text):
        return None, f"{field} contains characters that are not text"
    if len(text) > _MAX_LABEL:
        return None, f"{field} is longer than {_MAX_LABEL} characters"
    return text, None


def _clean_produced_by(pb: Optional[ProducedBy]) -> tuple[Optional[dict], Optional[str]]:
    if pb is None:
        return None, None
    out: dict[str, str] = {}
    for key, value in pb.model_dump().items():
        label, err = clean_label(value, f"produced_by.{key}")
        if err:
            return None, err
        if label is not None:
            out[key] = label
    return (out or None), None


# --- request bodies ------------------------------------------------------------------------


class ProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack: Optional[str] = None
    axis: str = Field(default="node")
    name: str = Field(default="")
    binds_to: str = Field(default="")
    src: tuple[str, ...] = ()
    dst: tuple[str, ...] = ()
    description: str = ""
    #: WHO proposed it. Required, and not defaulted to something like "agent": a proposal nobody
    #: can attribute is not a proposal, it is an anonymous change to the vocabulary.
    proposed_by: str = ""
    #: A node proposal asking to become a subject. Always escalates.
    declares_subject: bool = False
    #: The pointers into the data that motivated the kind — the rows or spans the proposer could
    #: not express. Optional in the payload, but a proposal carrying none can never be
    #: auto-approved: `growth.Trigger.EVIDENCE_UNGROUNDED` escalates it, because an ungrounded
    #: schema change is one no reviewer can check. Before the guardrail delegated to the engine
    #: this field did not exist, and the API happily auto-approved evidence-free proposals that
    #: the engine would have escalated — the gap that delegation closed.
    evidence: tuple[dict, ...] = ()
    #: WHERE the suggestion came from: ``owner`` (a person on the page), ``agent`` or
    #: ``adapter_agent``. Anything but ``owner`` — including an absent value, which defaults to
    #: ``agent`` — is NEVER auto-applied, even with auto-approve on: agent-suggested kinds always
    #: wait for a named human (founder's rule). The body may only NARROW the origin: ``owner`` is
    #: honoured solely when the AUTHENTICATED principal is an ontology owner (`is_owner`); anyone
    #: else claiming it is refused with ``ORIGIN_NOT_PERMITTED`` (review R-017 finding 2).
    origin: str = "agent"
    #: §5.8 runtime traceability: which agent and which model produced this suggestion, when the
    #: proposer knows. Recorded on the proposal and in the audit line; never used to decide.
    produced_by: Optional["ProducedBy"] = None


class ProducedBy(BaseModel):
    """`BUILD_PLAN` §5.8 ``produced_by`` — every field optional, all plain single-line strings."""

    model_config = ConfigDict(extra="forbid")

    agent: Optional[str] = None
    agent_version: Optional[str] = None
    task_class: Optional[str] = None
    model: Optional[str] = None
    prompt_version: Optional[str] = None
    run_id: Optional[str] = None
    at: Optional[str] = None


ProposalRequest.model_rebuild()


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: A DISPLAY label only. The decider of record is always the signed-in principal (review R-017
    #: finding 9); this is kept beside it as ``decided_by_label`` and never written into YAML.
    decided_by: str = ""
    reason: str = ""


class AutoApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    #: Display label only; ``set_by`` of record is the signed-in principal.
    set_by: str = ""


class DeprecateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack: Optional[str] = None
    axis: str = Field(default="node")
    kind: str = Field(default="")
    #: WHO. Same reasoning as `DecisionRequest.decided_by`: removing a kind from what the graph
    #: may grow into is a vocabulary decision, and an unattributed one is not a record of a claim.
    by: str = ""
    reason: str = ""
    #: A structural kind (a subject, or an edge carrying the identity rule) may be deprecated only
    #: with this set — the explicit human confirmation every identity-protected change requires.
    confirm_identity: bool = False


class SubjectDeclaration(BaseModel):
    """What makes a node kind a SUBJECT (spine D4b): what a human checks, and how."""

    model_config = ConfigDict(extra="forbid")

    identifiers: tuple[str, ...] = ()
    attestation: str = ""
    grant_type: str = ""


class DefineKindRequest(BaseModel):
    """An OWNER's direct edit. Applied immediately, audited; no proposal queue."""

    model_config = ConfigDict(extra="forbid")

    pack: Optional[str] = None
    axis: str = Field(default="node")
    name: str = ""
    binds_to: str = ""
    description: str = ""
    src: tuple[str, ...] = ()
    dst: tuple[str, ...] = ()
    #: Present ⇒ this node kind is declared a subject (identity-protected).
    subject: Optional[SubjectDeclaration] = None
    #: A display name for the audit line; the authenticated principal is always recorded too.
    by: str = ""
    reason: str = ""
    #: Required whenever the edit is identity-protected (see `identity_classes`).
    confirm_identity: bool = False


class RenameKindRequest(BaseModel):
    """Rename = add the new kind with the old one's declaration, then deprecate the old one."""

    model_config = ConfigDict(extra="forbid")

    pack: Optional[str] = None
    axis: str = Field(default="node")
    kind: str = ""
    new_name: str = ""
    description: Optional[str] = None
    #: Node rename only: add the new name beside the old one in every relation naming the old one,
    #: so the renamed kind can be used everywhere the old one could.
    carry_relations: bool = True
    by: str = ""
    reason: str = ""
    confirm_identity: bool = False


class EndpointsRequest(BaseModel):
    """Set which node kinds a relation may connect."""

    model_config = ConfigDict(extra="forbid")

    pack: Optional[str] = None
    kind: str = ""
    src: tuple[str, ...] = ()
    dst: tuple[str, ...] = ()
    by: str = ""
    reason: str = ""
    confirm_identity: bool = False


# --- routes --------------------------------------------------------------------------------


def register_ontology_routes(app: FastAPI) -> None:
    """Attach ``/ontology`` and its edit routes. Every WRITE route requires an authenticated
    principal (`auth.require_principal`); reads stay open so the vocabulary is inspectable.
    Every route that DEFINES or DECIDES vocabulary additionally requires an ontology owner
    (`require_owner`)."""
    install_auth_handler(app)

    async def _state_unreadable(_request: Request, exc: OntologyStateError) -> JSONResponse:
        # Fail closed (review R-017 finding 8): no read pretends the state is empty and no write
        # proceeds from an empty baseline.
        log.error("ontology state refused: %s", exc)
        return _refusal("ONTOLOGY_STATE_UNREADABLE", str(exc), status=503)

    app.add_exception_handler(OntologyStateError, _state_unreadable)

    async def _guardrail_unavailable(_request: Request,
                                     exc: GuardrailVocabularyUnavailable) -> JSONResponse:
        # Fail closed (review R-019 finding 1): no guardrail question is answered against a
        # vocabulary other than the one it must be answered against.
        log.error("guardrail vocabulary unavailable: %s", exc)
        return _refusal("GUARDRAIL_VOCABULARY_UNAVAILABLE", str(exc), status=503)

    app.add_exception_handler(GuardrailVocabularyUnavailable, _guardrail_unavailable)

    async def _audit_unavailable(_request: Request, exc: AuditWriteError) -> JSONResponse:
        # The attribution ledger could not take the intent record, so nothing was changed
        # (review R-019 finding 5).
        log.error("ontology audit unavailable: %s", exc)
        return _refusal("AUDIT_UNAVAILABLE", str(exc), status=503)

    app.add_exception_handler(AuditWriteError, _audit_unavailable)

    def _reload(pack: str) -> dict[str, Any]:
        """After any vocabulary change: drop every pack/vocabulary cache, then call whichever reload
        hooks this gateway exposes, so the NEXT extraction binds the edited vocabulary. Reports
        what it did, and whether the pack is part of the bound vocabulary at all — an edit to an
        unbound pack is saved but changes nothing extraction sees, and the response says so."""
        from databridge_graph.packs import active_vocabulary, binding_generation, clear_caches

        clear_caches()
        called: list[str] = ["databridge_graph.packs.clear_caches"]
        failed: list[dict[str, str]] = []
        for hook in ("reload_vocabulary", "reload_graph"):
            fn = getattr(app.state, hook, None)
            if callable(fn):
                try:
                    fn()
                    called.append(f"app.state.{hook}")
                except Exception as exc:                             # noqa: BLE001
                    log.error("reload hook %s failed: %s", hook, exc)
                    failed.append({"hook": f"app.state.{hook}", "error": str(exc)})
        try:
            bound = pack in active_vocabulary().packs
        except Exception as exc:                                     # noqa: BLE001
            # The committed vocabulary does not build in this process: that is a failed reload,
            # not "the pack is unbound".
            failed.append({"hook": "databridge_graph.packs.active_vocabulary", "error": str(exc)})
            bound = False
        return {"reloaded": called, "reload_failed": failed,
                "vocabulary_generation": binding_generation(), "pack_bound": bound}

    def _with_reload(body: dict[str, Any], pack: str):
        """``body`` plus the reload report — or, if ANY reload step failed, a 503
        ``VOCABULARY_RELOAD_FAILED`` (R-019 finding 10). The edit is committed (the response says
        so, with ``committed: true`` and the full result), but the running process may still hold
        the previous vocabulary, so the caller must not be told the change is live."""
        info = _reload(pack)
        if info["reload_failed"]:
            return _refusal(
                "VOCABULARY_RELOAD_FAILED",
                "the change was committed to the pack file and recorded, but this gateway could "
                "not reload it: " + "; ".join(f"{f['hook']}: {f['error']}"
                                              for f in info["reload_failed"])
                + ". Extraction in this process may still use the previous vocabulary until "
                  "the reload succeeds (restart the gateway or POST the reload again).",
                status=503, committed=True, result=body, **info)
        return {**body, **info}

    def _in_vocab(axis: str, name: str) -> bool:
        from databridge_graph.packs import active_vocabulary

        try:
            v = active_vocabulary()
        except Exception:                                            # noqa: BLE001
            return False
        return name in (v.node_kinds if axis == "node" else v.edge_kinds)

    def _label_or_refusal(value: Optional[str], field: str
                          ) -> tuple[Optional[str], Optional[JSONResponse]]:
        label, err = clean_label(value, field)
        if err:
            return None, _refusal("BAD_TEXT", err)
        return label, None

    def _active_pack(requested: Optional[str]) -> tuple[Optional[str], Optional[JSONResponse]]:
        from databridge_graph.packs import available_packs, configured_packs, packs_root

        root = packs_root()
        try:
            bound = tuple(configured_packs(root))
        except Exception as exc:                                     # noqa: BLE001
            return None, _refusal("NO_DEPLOYMENT_BINDING", str(exc), status=503)
        if requested:
            if requested not in available_packs(root):
                return None, _refusal("UNKNOWN_PACK",
                                      f"no pack {requested!r} under {root}", status=404)
            return requested, None
        if not bound:
            return None, _refusal("NO_DEPLOYMENT_BINDING",
                                  "this deployment binds no pack, so there is no active "
                                  "ontology", status=503)
        return bound[0], None

    def _annotate_with_data(view: dict[str, Any]) -> dict[str, Any]:
        """Per kind: how many current nodes/edges of it the shared store holds, and how many
        extractions were refused against it. Mutates ``view``'s kind rows in place; returns the
        page-level summary. Never fails the read — a store or ingest tree that cannot be read is
        reported as unavailable, not as zero."""
        store, origin = _resolve_store(app)
        counts = store_kind_counts(store)
        refusals = ingest_refusals()
        declared_nodes = {k["kind"] for k in view["node_kinds"]}
        declared_edges = {k["kind"] for k in view["edge_kinds"]}
        for row in view["node_kinds"]:
            row["count"] = counts["nodes"].get(row["kind"], 0) if counts["available"] else None
        for row in view["edge_kinds"]:
            row["count"] = counts["edges"].get(row["kind"], 0) if counts["available"] else None
            row["refused_off_ontology"] = (refusals["edge_endpoint_refusals"].get(row["kind"], 0)
                                           if refusals["available"] else None)
        without = [k["kind"] for k in (*view["node_kinds"], *view["edge_kinds"])
                   if counts["available"] and not k["count"]]
        return {
            "store": origin,
            "store_available": counts["available"],
            "nodes_total": sum(counts["nodes"].values()),
            "edges_total": sum(counts["edges"].values()),
            "kinds_without_data": without,
            # Kinds the store holds that THIS pack does not declare — another bound pack's, or
            # a kind since removed from the file. Shown, never hidden.
            "undeclared_in_store": {
                "nodes": {k: n for k, n in counts["nodes"].items() if k not in declared_nodes},
                "edges": {k: n for k, n in counts["edges"].items() if k not in declared_edges},
            },
            "ingest": refusals,
        }

    @app.get("/ontology/packs", dependencies=_AUTH)
    def list_ontology_packs():
        """Every pack this checkout can show, and which ones this deployment actually binds.

        Separate from `GET /ontology`'s `pack` query param, which already accepts any name here —
        this is what lets the UI OFFER those names instead of requiring the operator to know them.
        `available` is a directory scan (`available_packs`); `enabled` is the deployment binding
        (`packs/deployment.yaml` / `DATABRIDGE_PACKS`), which may fail to resolve (no binding
        configured) without that being a reason to hide the packs that exist on disk.
        """
        from databridge_graph.packs import PackError, available_packs, configured_packs, packs_root

        root = packs_root()
        available = list(available_packs(root))
        try:
            enabled = list(configured_packs(root))
        except PackError:
            enabled = []
        return {"packs": available, "enabled": enabled,
                "default": (enabled[0] if enabled else (available[0] if available else None))}

    @app.get("/ontology", dependencies=_AUTH)
    def get_ontology(pack: Optional[str] = None):
        name, refused = _active_pack(pack)
        if refused is not None:
            return refused
        state = load_state()
        try:
            view = _pack_view(str(name), state)
        except Exception as exc:                                     # noqa: BLE001
            log.exception("pack %r did not load", name)
            return _refusal("PACK_DID_NOT_LOAD", str(exc), status=503)
        # `applying` is shown with the pending ones: a decided approval whose pack write was not
        # confirmed (a crash between the two). Approving it again resumes it (R-019 finding 3).
        pending = [p for p in state["proposals"]
                   if p.get("pack") == name
                   and p.get("status") in ("pending", "escalated", "applying")]
        data = _annotate_with_data(view)
        return {
            **view,
            "data": data,
            "pending_proposals": pending,
            # Multi-step operations a crash interrupted; repeating the request finishes them.
            "incomplete_operations": [op for op in state["operations"]
                                      if op.get("pack") == name
                                      and op.get("status") == "in_progress"],
            "auto_approve": state["auto_approve"],
            "meta_kinds": _meta_kind_catalog(),
            # What a subject's attestation may be — from the loader, so the form cannot offer a
            # mode the pack loader would refuse.
            "attestation_modes": _attestation_modes(),
            # Stated in the payload, not only in the UI, so a client that never renders the toggle
            # still learns that two classes are outside it.
            "auto_approve_never_covers": [
                "determination kinds (edges binding to an attested meta-kind)",
                "identity-claim kinds (edges binding to a claim meta-kind)",
                "any kind naming a declared subject at either end, or asking to be one",
            ],
        }

    @app.post("/ontology/proposals")
    def propose_kind(payload: ProposalRequest = Body(...),
                     principal: AuthenticatedPrincipal = Depends(require_principal)):
        """Record a growth proposal. Decides auto-approve vs escalation, and never the other way.

        Any authenticated principal may propose. The ORIGIN is derived from the principal: the
        body may narrow it (``agent`` / ``adapter_agent``) but ``owner`` is honoured only for an
        ontology owner (`is_owner`), and only an owner-origin proposal can ever auto-apply."""
        name, refused = _active_pack(payload.pack)
        if refused is not None:
            return refused
        pack_name = str(name)
        if payload.axis not in AXES:
            return _refusal("BAD_AXIS", f"axis must be one of {list(AXES)}")
        if not _KIND_NAME.match(payload.name or ""):
            return _refusal("BAD_KIND_NAME",
                            "a kind name is lowercase snake_case: it becomes the node-id prefix, "
                            "so it must be unambiguous")
        proposed_by, bad = _label_or_refusal(payload.proposed_by, "proposed_by")
        if bad is not None:
            return bad
        if not proposed_by:
            return _refusal("PROPOSER_REQUIRED",
                            "name who proposed this. A change to the vocabulary that nobody can "
                            "be attributed to is not a proposal")
        produced_by, pb_err = _clean_produced_by(payload.produced_by)
        if pb_err:
            return _refusal("BAD_TEXT", pb_err)
        origin = (payload.origin or "").strip() or "agent"
        if origin not in ("owner", "agent", "adapter_agent"):
            return _refusal("BAD_ORIGIN", "origin must be one of ['owner', 'agent', 'adapter_agent']")
        owner_origin = origin == "owner"
        if owner_origin and not is_owner(principal):
            return _refusal(
                "ORIGIN_NOT_PERMITTED",
                f"origin 'owner' is derived from the signed-in principal, not asserted by the "
                f"request; {principal.principal_id!r} is not an ontology owner, so this proposal "
                f"can only be filed as agent-origin (and waits for a human)", status=403)
        if payload.declares_subject:
            # Review R-017 finding 7. This shape carries no identifiers, attestation mode or grant
            # type, so an approval could only ever write an ORDINARY node — a "subject" without
            # the identity protection that makes it one. Declaring a subject is an owner's direct,
            # identity-confirmed edit, which carries the full declaration.
            return _refusal(
                "SUBJECT_DECLARATION_NOT_PROPOSABLE",
                "a subject (identity-protected) kind cannot be declared through a proposal: the "
                "proposal carries no identifiers, attestation or grant type, so approving it "
                "would create an unprotected node. An owner declares subjects with POST "
                "/ontology/kinds (subject: {identifiers, attestation, grant_type}, "
                "confirm_identity: true)")
        from databridge_graph.meta import EdgeMetaKind, NodeMetaKind

        valid = ({m.value for m in NodeMetaKind} if payload.axis == "node"
                 else {m.value for m in EdgeMetaKind})
        if payload.binds_to not in valid:
            return _refusal("BAD_META_KIND",
                            f"binds_to must be one of {sorted(valid)}; the meta-model is closed "
                            f"and grows only by ADR")
        pre_state = load_state()
        try:
            view = _pack_view(pack_name, pre_state)
        except Exception as exc:                                     # noqa: BLE001
            return _refusal("PACK_DID_NOT_LOAD", str(exc), status=503)
        existing = {k["kind"] for k in
                    (view["node_kinds"] if payload.axis == "node" else view["edge_kinds"])}
        if payload.name in existing:
            return _refusal("KIND_ALREADY_DECLARED",
                            f"pack {pack_name!r} already declares {payload.axis} kind "
                            f"{payload.name!r}")

        # An edge may not be proposed onto a node kind this pack has deprecated: deprecation stops
        # the vocabulary from growing AROUND a kind that is no longer offered, which is the only
        # teeth it has without ever touching `ontology.yaml` or a graph row (see EMPTY_STATE).
        if payload.axis == "edge":
            deprecated_nodes = {n["kind"] for n in view["node_kinds"] if n["deprecated"]}
            touched = sorted(set(payload.src) & deprecated_nodes | set(payload.dst) & deprecated_nodes)
            if touched:
                return _refusal("ENDPOINT_DEPRECATED",
                                f"endpoint kind(s) {touched} are deprecated in pack {pack_name!r} — "
                                f"not removed, but no longer offered for new proposals. Reactivate "
                                f"the kind first (POST /ontology/kinds/reactivate) if this edge "
                                f"genuinely needs it")

        try:
            evidence = _evidence_pointers(payload.evidence)
        except ValueError as exc:
            return _refusal("BAD_EVIDENCE", str(exc))

        # Raises GuardrailVocabularyUnavailable (→ 503) rather than judging against another
        # vocabulary; nothing has been recorded yet (R-019 finding 1).
        verdict = guardrail(axis=payload.axis, binds_to=payload.binds_to,
                            src=tuple(payload.src), dst=tuple(payload.dst),
                            declares_subject=payload.declares_subject,
                            name=payload.name, pack=pack_name,
                            proposed_by=proposed_by,
                            evidence=evidence)
        # The validated pointers are KEPT (R-019 finding 2): on the record the reviewer sees, in
        # the audit line, and re-read when the approval re-evaluates the proposal.
        evidence_json = [p.model_dump(mode="json") for p in evidence]

        trace = {"principal": principal.principal_id, "origin": origin, "pack": pack_name,
                 "axis": payload.axis, "kind": payload.name, "by": principal.principal_id,
                 "proposed_by": proposed_by, "evidence": evidence_json}
        if not owner_origin:
            # §5.8: an agent-origin suggestion names the agent and model that produced it.
            trace["agent"] = (produced_by or {}).get("agent")
            trace["model"] = (produced_by or {}).get("model")
        if verdict.outcome == "refused":
            # Review R-017 finding 3. REFUSED is not a harsher ESCALATED: the engine found nothing
            # for a human to decide (it could not load, or it conflicts with a kind the bound
            # vocabulary already holds). It is refused here and never enters the approval queue.
            append_audit({"action": "propose_refused", **trace,
                          "reason": verdict.refusal_reason})
            return _refusal("PROPOSAL_REFUSED", verdict.refusal_reason or "refused by the "
                            "growth evaluator", status=422)

        # Durable intent BEFORE the state changes (R-019 finding 5): if the journal cannot take
        # it, AuditWriteError → 503 and nothing is recorded.
        op_id = audit_intent("propose", **trace)
        with state_txn() as state:
            proposal_id = _next_proposal_id(state)
            auto_on = state["auto_approve"].get("enabled") is True
            record: dict[str, Any] = {
                "id": proposal_id,
                "origin": origin,
                "pack": pack_name,
                "axis": payload.axis,
                "name": payload.name,
                "binds_to": payload.binds_to,
                "src": list(payload.src),
                "dst": list(payload.dst),
                "description": payload.description,
                "declares_subject": False,
                "proposed_by": proposed_by,
                "proposed_by_principal": principal.principal_id,
                "produced_by": produced_by,
                "evidence": evidence_json,
                "proposed_at": _now(),
                "auto_approvable": verdict.auto_approvable,
                "guardrail_outcome": verdict.outcome,
                "guardrail_classes": list(verdict.classes),
                "escalation_reason": " ".join(verdict.reasons) or None,
                "decided_by": None,
                "decided_at": None,
            }
            if not verdict.auto_approvable:
                # The toggle is not even consulted. Auto-approve is a setting about the ORDINARY
                # case; these two classes are not the ordinary case, and reading the toggle here
                # would be one refactor away from honouring it.
                record["status"] = "escalated"
                record["auto_approve_was_enabled"] = auto_on
            elif auto_on and not owner_origin:
                # The founder's rule: a kind an agent or adapter agent SUGGESTS is always a
                # proposal a human decides. The toggle covers owner-origin proposals only — and
                # owner origin is only ever granted to an owner principal (above).
                record["status"] = "pending"
                record["auto_approve_bypassed"] = (
                    f"origin {origin!r}: kinds suggested by agents always wait for a human, "
                    f"whatever auto-approve is set to")
            elif auto_on:
                # APPLYING, not approved (R-019 finding 3): the decision is made, the pack write
                # is not yet confirmed. `approved` is published only after the write verifies.
                record["status"] = "applying"
                record["op_id"] = op_id
                # The decider of record is the owner who proposed it, under the gate the named
                # setter opened — both principals, neither a request-supplied label.
                record["decided_by"] = principal.principal_id
                record["decision_basis"] = (
                    f"auto-approve (enabled by {state['auto_approve'].get('set_by')})")
                record["decided_at"] = _now()
            else:
                record["status"] = "pending"
            state["proposals"].append(record)

        audit = complete_audit(op_id, {"action": "propose", **trace, "proposal_id": record["id"],
                                       "status": record["status"],
                                       "classes": list(verdict.classes)})
        if record["status"] == "applying":
            return _finish_approval(dict(record), principal, label=None, reason=None,
                                    op_id=op_id, audit_action="auto_approve_apply")
        return {**record, "audit": audit}

    def _next_proposal_id(state: dict[str, Any]) -> str:
        """``prop-NNNN``, one past the highest existing number — never a count, which would reuse
        an id if a record were ever missing (ids must be unique; `validate_state` refuses dups)."""
        top = 0
        for p in state["proposals"]:
            m = re.fullmatch(r"prop-(\d+)", str(p.get("id")))
            if m:
                top = max(top, int(m.group(1)))
        return f"prop-{top + 1:04d}"

    def _entry_for(record: dict[str, Any]) -> dict[str, Any]:
        entry: dict[str, Any] = {"name": record["name"], "binds_to": record["binds_to"]}
        if record["axis"] == "edge":
            entry["src"] = list(record["src"])
            entry["dst"] = list(record["dst"])
        if (record.get("description") or "").strip():
            entry["description"] = " ".join(str(record["description"]).split())
        return entry

    def _already_applied(record: dict[str, Any], entry: dict[str, Any]
                         ) -> tuple[Optional[str], Optional[str]]:
        """``(path, None)`` when the pack file already holds exactly ``entry`` (a write that
        committed before a crash), ``(None, None)`` when it holds no kind of that name, and
        ``(None, why)`` when it holds a DIFFERENT declaration of that name."""
        from databridge_graph.packs import packs_root

        path = packs_root() / record["pack"] / "ontology.yaml"
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:                                     # noqa: BLE001
            return None, f"the pack file could not be read to check the approval: {exc}"
        block = "node_kinds" if record["axis"] == "node" else "edge_kinds"
        same = [e for e in (data.get(block) or []) if isinstance(e, dict)
                and e.get("name") == entry["name"]]
        if not same:
            return None, None
        if same == [json.loads(json.dumps(entry))]:
            return str(path), None
        return None, (f"pack {record['pack']!r} already declares a different {record['axis']} "
                      f"kind {entry['name']!r}")

    def _set_proposal(proposal_id: str, **fields: Any) -> dict[str, Any]:
        with state_txn() as state:
            for p in state["proposals"]:
                if p["id"] == proposal_id:
                    p.update(fields)
                    return dict(p)
        raise OntologyStateError(f"proposal {proposal_id!r} vanished from the state file")

    def _finish_approval(record: dict[str, Any], principal: AuthenticatedPrincipal, *,
                         label: Optional[str], reason: Optional[str], op_id: str,
                         audit_action: str):
        """Take an ``applying`` record to ``approved`` (or ``write_failed``). Idempotent: when the
        pack already holds exactly this entry — a write that committed before a crash — nothing
        is written again and the record is simply published as approved (R-019 finding 3)."""
        entry = _entry_for(record)
        written, conflict = _already_applied(record, entry)
        already = written is not None
        error: Optional[JSONResponse] = None
        if conflict is not None:
            final = _set_proposal(record["id"], status="write_failed", write_error=conflict)
            error = _refusal("PACK_WRITE_FAILED", conflict, status=503)
        elif not already:
            try:
                written = str(append_kind_to_pack(record["pack"], record["axis"], entry,
                                                  by=str(record["decided_by"]),
                                                  at=str(record["decided_at"])))
            except Exception as exc:                                 # noqa: BLE001
                log.exception("could not extend pack %r", record["pack"])
                final = _set_proposal(record["id"], status="write_failed", write_error=str(exc))
                error = _refusal("PACK_WRITE_FAILED", str(exc), status=503)
        if error is None:
            final = _set_proposal(record["id"], status="approved", written_to=written,
                                  applied_at=_now(), already_present=already)
        audit = complete_audit(op_id, {
            "action": audit_action, "pack": record["pack"], "axis": record["axis"],
            "kind": record["name"], "principal": principal.principal_id,
            "by": principal.principal_id, "by_label": label, "proposal_id": record["id"],
            "decided_by": record.get("decided_by"),
            "origin": record.get("origin"),
            "agent": (record.get("produced_by") or {}).get("agent"),
            "model": (record.get("produced_by") or {}).get("model"),
            "evidence": record.get("evidence") or [],
            "reason": reason, "written_to": written, "already_present": already,
            "status": final["status"],
            "error": None if error is None else "PACK_WRITE_FAILED"})
        if error is not None:
            return error
        return _with_reload({**final, "audit": audit}, record["pack"])

    def _still_approvable(record: dict[str, Any]) -> Optional[str]:
        """Why an approval must NOT write this record, re-checked at decision time, or ``None``.

        The vocabulary may have moved since the proposal was filed (another pack enabled, a kind
        added), and records filed before review R-017 never stored the engine's outcome. So the
        evaluator is asked again — WITH the evidence the proposal was filed with (R-019 finding
        2) — and a proposal it now REFUSES is refused, whatever its stored status (finding 3). A
        stored subject declaration is refused outright (finding 7). A vocabulary that cannot be
        established raises `GuardrailVocabularyUnavailable` (503, nothing decided) rather than
        refusing the proposal forever over a transient failure."""
        if record.get("declares_subject"):
            return ("this proposal asks to declare a subject, which a proposal cannot carry "
                    "(no identifiers, attestation or grant type) — an owner declares it with "
                    "POST /ontology/kinds and confirm_identity: true")
        if record.get("guardrail_outcome") == "refused":
            return "the growth evaluator refused this proposal when it was filed"
        try:
            evidence = _evidence_pointers(tuple(record.get("evidence") or ()))
        except ValueError as exc:
            return f"the evidence stored with this proposal no longer validates: {exc}"
        try:
            verdict = guardrail(axis=record["axis"], binds_to=record["binds_to"],
                                src=tuple(record.get("src") or ()),
                                dst=tuple(record.get("dst") or ()),
                                name=record["name"], pack=record["pack"],
                                proposed_by=str(record.get("proposed_by") or "unknown"),
                                evidence=evidence)
        except GuardrailVocabularyUnavailable:
            raise
        except Exception as exc:                                     # noqa: BLE001
            return f"the proposal could not be re-evaluated against the current vocabulary: {exc}"
        if verdict.outcome == "refused":
            return verdict.refusal_reason or "the growth evaluator now refuses this proposal"
        return None

    @app.post("/ontology/proposals/{proposal_id}/approve")
    def approve_proposal(proposal_id: str, payload: DecisionRequest = Body(...),
                         principal: AuthenticatedPrincipal = Depends(require_owner)):
        """An OWNER approves. This is the only route an escalated proposal can pass through.

        The approver of record is the signed-in principal (review R-017 finding 9); a
        ``decided_by`` in the body is kept only as a display label beside it.

        Crash-consistent (R-019 finding 3): ``pending|escalated → applying → approved``. The
        decision is durable as ``applying`` before the pack is touched; ``approved`` is published
        only after the write verifies. A record left ``applying`` (a crash between the two) is
        RESUMED by approving it again — idempotently: a write that already committed is detected
        and not repeated. The original decider stays the decider of record."""
        label, bad = _label_or_refusal(payload.decided_by, "decided_by")
        if bad is not None:
            return bad
        peek = next((p for p in load_state()["proposals"] if p["id"] == proposal_id), None)
        if peek is None:
            return _refusal("UNKNOWN_PROPOSAL", f"no proposal {proposal_id!r}", status=404)
        if peek["status"] not in ("pending", "escalated", "applying"):
            return _refusal("PROPOSAL_ALREADY_DECIDED",
                            f"proposal {proposal_id!r} is {peek['status']!r}")
        op_id = audit_intent("approve_proposal", pack=peek["pack"], axis=peek["axis"],
                             kind=peek["name"], proposal_id=proposal_id,
                             principal=principal.principal_id)
        why_not: Optional[str] = None
        resumed = False
        with state_txn() as state:
            record = next((p for p in state["proposals"] if p["id"] == proposal_id), None)
            if record is None:
                return _refusal("UNKNOWN_PROPOSAL", f"no proposal {proposal_id!r}", status=404)
            if record["status"] == "applying":
                resumed = True
                record["resumed_by"] = principal.principal_id
                record["resumed_at"] = _now()
            elif record["status"] not in ("pending", "escalated"):
                complete_audit(op_id, {"action": "approve_not_applicable",
                                       "pack": record["pack"], "proposal_id": proposal_id,
                                       "principal": principal.principal_id,
                                       "status": record["status"]})
                return _refusal("PROPOSAL_ALREADY_DECIDED",
                                f"proposal {proposal_id!r} is {record['status']!r}")
            else:
                why_not = _still_approvable(record)
                if why_not is not None:
                    record["status"] = "refused"
                    record["refused_reason"] = why_not
                    record["refused_at"] = _now()
                    record["refused_on_approval_by"] = principal.principal_id
                else:
                    record["status"] = "applying"
                    record["op_id"] = op_id
                    record["decided_by"] = principal.principal_id
                    record["decided_by_label"] = label
                    record["decided_at"] = _now()
                    record["decision_reason"] = payload.reason or None
            snapshot = dict(record)
        if why_not is not None:
            complete_audit(op_id, {"action": "approve_refused", "pack": snapshot["pack"],
                                   "axis": snapshot["axis"], "kind": snapshot["name"],
                                   "principal": principal.principal_id,
                                   "by": principal.principal_id, "by_label": label,
                                   "proposal_id": proposal_id, "reason": why_not})
            return _refusal("PROPOSAL_REFUSED", why_not, status=409)
        return _finish_approval(snapshot, principal, label=label,
                                reason=payload.reason or None, op_id=op_id,
                                audit_action=("approve_proposal_resumed" if resumed
                                              else "approve_proposal"))

    @app.post("/ontology/proposals/{proposal_id}/reject")
    def reject_proposal(proposal_id: str, payload: DecisionRequest = Body(...),
                        principal: AuthenticatedPrincipal = Depends(require_owner)):
        label, bad = _label_or_refusal(payload.decided_by, "decided_by")
        if bad is not None:
            return bad
        op_id = audit_intent("reject_proposal", proposal_id=proposal_id,
                             principal=principal.principal_id)
        with state_txn() as state:
            record = next((p for p in state["proposals"] if p["id"] == proposal_id), None)
            if record is None:
                complete_audit(op_id, {"action": "reject_not_applicable",
                                       "proposal_id": proposal_id, "status": None})
                return _refusal("UNKNOWN_PROPOSAL", f"no proposal {proposal_id!r}", status=404)
            if record["status"] not in ("pending", "escalated"):
                complete_audit(op_id, {"action": "reject_not_applicable",
                                       "proposal_id": proposal_id, "status": record["status"]})
                return _refusal("PROPOSAL_ALREADY_DECIDED",
                                f"proposal {proposal_id!r} is {record['status']!r}")
            record["status"] = "rejected"
            record["decided_by"] = principal.principal_id
            record["decided_by_label"] = label
            record["decided_at"] = _now()
            record["decision_reason"] = payload.reason or None
            snapshot = dict(record)
        complete_audit(op_id, {"action": "reject_proposal", "pack": snapshot["pack"],
                               "axis": snapshot["axis"], "kind": snapshot["name"],
                               "principal": principal.principal_id, "by": principal.principal_id,
                               "by_label": label, "proposal_id": proposal_id,
                               "origin": snapshot.get("origin"),
                               "agent": (snapshot.get("produced_by") or {}).get("agent"),
                               "model": (snapshot.get("produced_by") or {}).get("model"),
                               "evidence": snapshot.get("evidence") or [],
                               "reason": payload.reason or None})
        return snapshot

    @app.put("/ontology/auto_approve")
    def set_auto_approve(payload: AutoApproveRequest = Body(...),
                         principal: AuthenticatedPrincipal = Depends(require_principal)):
        """Turn the human gate off for ORDINARY proposals. It has never covered the two classes
        above and turning it on does not widen it — the propose route does not read it for them.

        Deliberately `require_principal`, not `require_owner`: the toggle only ever lets an
        OWNER-origin proposal auto-apply, and owner origin is granted solely to owner principals,
        who can already apply directly with POST /ontology/kinds. So a non-owner flipping it gains
        nothing it could not already do; agent suggestions wait for a human either way. The
        setter of record is the signed-in principal (finding 9)."""
        label, bad = _label_or_refusal(payload.set_by, "set_by")
        if bad is not None:
            return bad
        op_id = audit_intent("set_auto_approve", enabled=bool(payload.enabled),
                             principal=principal.principal_id)
        with state_txn() as state:
            state["auto_approve"] = {"enabled": bool(payload.enabled),
                                     "set_by": principal.principal_id,
                                     "set_by_label": label,
                                     "set_at": _now()}
            current = dict(state["auto_approve"])
        complete_audit(op_id, {"action": "set_auto_approve", "pack": None,
                               "enabled": bool(payload.enabled),
                               "principal": principal.principal_id, "by": principal.principal_id,
                               "by_label": label})
        return {
            "auto_approve": current,
            "never_covers": [
                "determination kinds (edges binding to an attested meta-kind)",
                "identity-claim kinds (edges binding to a claim meta-kind)",
                "any kind naming a declared subject at either end, or asking to be one",
            ],
        }

    # --- removal, honestly: deprecate, never delete (see EMPTY_STATE) -----------------------

    def _find_kind(view: dict[str, Any], axis: str, kind: str) -> Optional[dict[str, Any]]:
        collection = view["node_kinds"] if axis == "node" else view["edge_kinds"]
        return next((k for k in collection if k["kind"] == kind), None)

    def _kind_identity_classes(entry: dict[str, Any], axis: str, pack_name: str) -> list[str]:
        """The identity-protected classes an EXISTING kind falls in — the same computation the
        owner edits use, so deprecate and reactivate gate exactly what define/rename gate."""
        if axis == "node":
            return ["subject"] if entry.get("subject") else []
        # No `except: subjects = set()` — that answered "touches no subject" whenever the subjects
        # could not be established, lifting the identity gate (R-019 finding 1). It raises, and
        # the route answers 503 GUARDRAIL_VOCABULARY_UNAVAILABLE.
        subjects = _subjects_for(pack_name)
        classes = identity_classes(axis="edge", binds_to=entry["binds_to"],
                                   src=tuple(entry.get("from") or ()),
                                   dst=tuple(entry.get("to") or ()), subjects=subjects)
        if not entry.get("editable", True) and not classes:
            classes = ["identity_rule"]
        return classes

    def _locked_refusal(payload: DeprecateRequest, entry: dict[str, Any], classes: list[str],
                        verb: str) -> JSONResponse:
        why = (entry.get("identity") if payload.axis == "node"
               else "carries or touches the identity rule")
        return _refusal("KIND_NOT_EDITABLE",
                        f"{payload.kind!r} is identity-protected ({', '.join(classes)}) — {why}. "
                        f"{verb} it needs an explicit identity confirmation "
                        f"(confirm_identity: true) from an ontology owner",
                        identity_classes=classes)

    @app.post("/ontology/kinds/deprecate")
    def deprecate_kind(payload: DeprecateRequest = Body(...),
                       principal: AuthenticatedPrincipal = Depends(require_owner)):
        """Stop offering a kind for new proposals. Never touches `ontology.yaml` or a graph row.

        Refuses exactly where the library already refuses editing: an identity-protected kind (a
        subject, an edge carrying the identity rule, or an edge touching a subject) needs
        ``confirm_identity: true``. The deprecator of record is the signed-in owner.
        """
        name, refused = _active_pack(payload.pack)
        if refused is not None:
            return refused
        pack_name = str(name)
        if payload.axis not in AXES:
            return _refusal("BAD_AXIS", f"axis must be one of {list(AXES)}")
        label, bad = _label_or_refusal(payload.by, "by")
        if bad is not None:
            return bad
        try:
            view = _pack_view(pack_name, load_state())
        except OntologyStateError:
            raise
        except Exception as exc:                                     # noqa: BLE001
            return _refusal("PACK_DID_NOT_LOAD", str(exc), status=503)
        entry = _find_kind(view, payload.axis, payload.kind)
        if entry is None:
            return _refusal("UNKNOWN_KIND",
                            f"pack {pack_name!r} declares no {payload.axis} kind {payload.kind!r}",
                            status=404)
        classes = _kind_identity_classes(entry, payload.axis, pack_name)
        if classes and not payload.confirm_identity:
            return _locked_refusal(payload, entry, classes, "Removing")
        if entry["deprecated"]:
            return _refusal("ALREADY_DEPRECATED", f"{payload.kind!r} is already deprecated")
        at = _now()
        op_id = audit_intent("deprecate", pack=pack_name, axis=payload.axis, kind=payload.kind,
                             principal=principal.principal_id)
        with state_txn() as state:
            state.setdefault("deprecated_kinds", []).append({
                "pack": pack_name, "axis": payload.axis, "kind": payload.kind,
                "by": principal.principal_id, "by_label": label, "at": at,
                "reason": payload.reason or None, "principal": principal.principal_id,
                "identity_classes": classes, "identity_confirmed": bool(classes),
                "op_id": op_id,
            })
        audit = complete_audit(op_id, {
            "action": "deprecate", "pack": pack_name, "axis": payload.axis,
            "kind": payload.kind, "principal": principal.principal_id,
            "by": principal.principal_id, "by_label": label,
            "reason": payload.reason or None, "identity_classes": classes,
            "identity_confirmed": bool(classes)})
        return _with_reload({"audit": audit, **{
            "pack": pack_name, "axis": payload.axis, "kind": payload.kind,
            "deprecated": True, "deprecated_by": principal.principal_id,
            "deprecated_by_label": label, "deprecated_at": at,
            "note": (
                "Removed from what this pack offers for new proposals only. The declaration in "
                "ontology.yaml is untouched, so every node or edge already written with this kind "
                "reads back exactly as it did before — this action never deletes or invalidates "
                "graph data. New edge proposals naming this kind as an endpoint are refused "
                "(ENDPOINT_DEPRECATED) until it is reactivated."
            ),
        }}, pack_name)

    @app.post("/ontology/kinds/reactivate")
    def reactivate_kind(payload: DeprecateRequest = Body(...),
                        principal: AuthenticatedPrincipal = Depends(require_owner)):
        """Offer a deprecated kind for new proposals again. The inverse of `deprecate`, and just as
        guarded: reactivating an identity-protected kind changes what extraction may map to, so it
        needs the same ``confirm_identity: true`` its deprecation did (review R-017 finding 6)."""
        name, refused = _active_pack(payload.pack)
        if refused is not None:
            return refused
        pack_name = str(name)
        if payload.axis not in AXES:
            return _refusal("BAD_AXIS", f"axis must be one of {list(AXES)}")
        label, bad = _label_or_refusal(payload.by, "by")
        if bad is not None:
            return bad
        try:
            view = _pack_view(pack_name, load_state())
        except OntologyStateError:
            raise
        except Exception as exc:                                     # noqa: BLE001
            return _refusal("PACK_DID_NOT_LOAD", str(exc), status=503)
        entry = _find_kind(view, payload.axis, payload.kind)
        if entry is None:
            return _refusal("UNKNOWN_KIND",
                            f"pack {pack_name!r} declares no {payload.axis} kind {payload.kind!r}",
                            status=404)
        if not entry["deprecated"]:
            return _refusal("NOT_DEPRECATED", f"{payload.kind!r} is not deprecated")
        classes = _kind_identity_classes(entry, payload.axis, pack_name)
        if classes and not payload.confirm_identity:
            return _locked_refusal(payload, entry, classes, "Reactivating")
        at = _now()
        op_id = audit_intent("reactivate", pack=pack_name, axis=payload.axis, kind=payload.kind,
                             principal=principal.principal_id)
        with state_txn() as state:
            state.setdefault("deprecated_kinds", []).append({
                "pack": pack_name, "axis": payload.axis, "kind": payload.kind,
                "by": principal.principal_id, "by_label": label, "at": at,
                "reason": payload.reason or None, "reactivated": True,
                "principal": principal.principal_id, "identity_classes": classes,
                "identity_confirmed": bool(classes), "op_id": op_id,
            })
        audit = complete_audit(op_id, {
            "action": "reactivate", "pack": pack_name, "axis": payload.axis,
            "kind": payload.kind, "principal": principal.principal_id,
            "by": principal.principal_id, "by_label": label,
            "reason": payload.reason or None, "identity_classes": classes,
            "identity_confirmed": bool(classes)})
        return _with_reload({"pack": pack_name, "axis": payload.axis, "kind": payload.kind,
                             "deprecated": False, "reactivated_by": principal.principal_id,
                             "reactivated_by_label": label, "reactivated_at": at,
                             "audit": audit}, pack_name)

    # --- owner edits: define, rename, set endpoints --------------------------------------------
    #
    # The founder's requirement: "the ontology page allows me to define the relations and entities
    # and the system only maps to these." An OWNER's edit therefore applies directly — no queue —
    # with an append-only audit line naming the authenticated principal. Proposals remain the path
    # for kinds an agent suggests (`POST /ontology/proposals`). Identity-protected edits (declaring
    # or renaming a subject, a determination or claim relation, a relation touching a subject)
    # additionally require `confirm_identity: true`: never implied, never defaulted.

    def _subjects_for(pack_name: str) -> set[str]:
        from databridge_graph.packs import load_pack

        subjects = set(load_pack(pack_name).subject_kinds)
        # A subject in ANY vocabulary the guardrail judges against is a subject here; a vocabulary
        # that cannot be established raises (fail closed — R-019 finding 1), never "no subjects".
        for vocab in _guardrail_vocabs(pack_name):
            subjects |= set(vocab.subject_kinds)
        return subjects

    def _check_endpoints(view: dict[str, Any], pack_name: str, src: tuple[str, ...],
                         dst: tuple[str, ...]) -> Optional[JSONResponse]:
        from databridge_graph.packs import ANY_KIND

        if not src or not dst:
            return _refusal("ENDPOINTS_REQUIRED",
                            "a relation names at least one source kind and one target kind — "
                            "extraction refuses a relation whose endpoints the ontology does not "
                            "declare. Use '*' deliberately if any kind may appear")
        nodes = {n["kind"]: n for n in view["node_kinds"]}
        unknown = sorted({k for k in (*src, *dst) if k != ANY_KIND and k not in nodes})
        if unknown:
            return _refusal("UNKNOWN_ENDPOINT_KIND",
                            f"endpoint kind(s) {unknown} are not entity kinds in pack "
                            f"{pack_name!r}. Define the entity kind first",
                            nearest_alternative=", ".join(sorted(nodes)[:12]) or None)
        stale = sorted({k for k in (*src, *dst) if k in nodes and nodes[k]["deprecated"]})
        if stale:
            return _refusal("ENDPOINT_DEPRECATED",
                            f"endpoint kind(s) {stale} are deprecated in pack {pack_name!r}; "
                            f"reactivate them first if this relation genuinely needs them")
        return None

    def _identity_gate(classes: list[str], confirmed: bool) -> Optional[JSONResponse]:
        if classes and not confirmed:
            return _refusal(
                "IDENTITY_CONFIRMATION_REQUIRED",
                "this change is identity-protected (" + ", ".join(classes) + "). Subjects are the "
                "entities whose identity this system never auto-confirms; changing what they are, "
                "or what may point at them, needs an explicit confirmation from a named human "
                "(confirm_identity: true)",
                status=409, identity_classes=classes)
        return None

    def _comment(action: str, who: str, principal: AuthenticatedPrincipal, at: str,
                 reason: str) -> str:
        # `who` is the signed-in principal (never a request label — review R-017 finding 9). An id
        # carrying a control character is written JSON-escaped (pure ASCII, one line) so it can
        # neither break the comment nor be split into lines that misattribute the edit.
        if _CONTROL.search(who):
            who = json.dumps(who)
        text = (f"{action} on /ontology by {who} at {at}. Audit: ontology_audit.jsonl.")
        if reason.strip():
            text += "\nReason: " + " ".join(reason.split())
        return text

    @app.post("/ontology/kinds")
    def define_kind(payload: DefineKindRequest = Body(...),
                    principal: AuthenticatedPrincipal = Depends(require_owner)):
        """Define an entity kind or a relation kind. Applies directly, audited, reloads."""
        from databridge_graph.meta import (
            SUBJECT_ELIGIBLE_META_KINDS, EdgeMetaKind, NodeMetaKind)
        from databridge_graph.packs import ATTESTATION_MODES

        name, refused = _active_pack(payload.pack)
        if refused is not None:
            return refused
        pack_name = str(name)
        if payload.axis not in AXES:
            return _refusal("BAD_AXIS", f"axis must be one of {list(AXES)}")
        if not _KIND_NAME.match(payload.name or ""):
            return _refusal("BAD_KIND_NAME",
                            "a kind name is lowercase snake_case (e.g. purchase_order): it becomes "
                            "the node-id prefix, so it must be unambiguous")
        valid = ({m.value for m in NodeMetaKind} if payload.axis == "node"
                 else {m.value for m in EdgeMetaKind})
        if payload.binds_to not in valid:
            return _refusal("BAD_META_KIND",
                            f"binds_to must be one of {sorted(valid)}; the meta-model is closed "
                            f"and grows only by ADR")
        try:
            view = _pack_view(pack_name, load_state())
        except OntologyStateError:
            raise
        except Exception as exc:                                     # noqa: BLE001
            return _refusal("PACK_DID_NOT_LOAD", str(exc), status=503)
        existing = _find_kind(view, payload.axis, payload.name)
        if existing is not None:
            hint = (" It is deprecated — reactivate it instead of redefining it."
                    if existing["deprecated"] else "")
            return _refusal("KIND_ALREADY_DECLARED",
                            f"pack {pack_name!r} already declares {payload.axis} kind "
                            f"{payload.name!r}.{hint}")
        src, dst = tuple(dict.fromkeys(payload.src)), tuple(dict.fromkeys(payload.dst))
        if payload.axis == "edge":
            bad = _check_endpoints(view, pack_name, src, dst)
            if bad is not None:
                return bad
        elif src or dst:
            return _refusal("ENDPOINTS_ON_ENTITY",
                            "source/target kinds belong to a relation, not an entity kind")
        if payload.subject is not None:
            if payload.axis != "node":
                return _refusal("SUBJECT_ON_RELATION", "only an entity kind can be a subject")
            if payload.binds_to not in {m.value for m in SUBJECT_ELIGIBLE_META_KINDS}:
                return _refusal("SUBJECT_NOT_ELIGIBLE",
                                f"a subject must bind to one of "
                                f"{sorted(m.value for m in SUBJECT_ELIGIBLE_META_KINDS)} — "
                                f"{payload.binds_to!r} has no identity a human could attest to")
            idents = [i.strip() for i in payload.subject.identifiers if i and i.strip()]
            if not idents or len(set(idents)) != len(idents):
                return _refusal("SUBJECT_IDENTIFIERS_REQUIRED",
                                "a subject names at least one identifier a human checks the "
                                "person against (e.g. email, employee_id), each once")
            if payload.subject.attestation not in ATTESTATION_MODES:
                return _refusal("BAD_ATTESTATION",
                                f"attestation must be one of {sorted(ATTESTATION_MODES)}")
            if not payload.subject.grant_type.strip():
                return _refusal("GRANT_TYPE_REQUIRED",
                                "a subject names the capability an attestation mints (grant_type)")
        try:
            subjects = _subjects_for(pack_name)
        except Exception as exc:                                     # noqa: BLE001
            return _refusal("PACK_DID_NOT_LOAD", str(exc), status=503)
        classes = identity_classes(axis=payload.axis, binds_to=payload.binds_to, src=src,
                                   dst=dst, declares_subject=payload.subject is not None,
                                   subjects=subjects)
        gate = _identity_gate(classes, payload.confirm_identity)
        if gate is not None:
            return gate

        label, bad = _label_or_refusal(payload.by, "by")
        if bad is not None:
            return bad
        who, at = principal.principal_id, _now()
        entry: dict[str, Any] = {"name": payload.name, "binds_to": payload.binds_to}
        if payload.axis == "edge":
            entry["src"], entry["dst"] = list(src), list(dst)
        if payload.description.strip():
            entry["description"] = " ".join(payload.description.split())
        additions: list[tuple[str, dict[str, Any]]] = [
            ("node_kinds" if payload.axis == "node" else "edge_kinds", entry)]
        subject_entry: Optional[dict[str, Any]] = None
        if payload.subject is not None:
            subject_entry = {
                "kind": payload.name,
                "identifiers": [i.strip() for i in payload.subject.identifiers if i.strip()],
                "never_fuzzy_merge": True,
                "attestation": payload.subject.attestation,
                "grant_type": payload.subject.grant_type.strip(),
            }
            additions.append(("subject_kinds", subject_entry))
        op_id = audit_intent("define", pack=pack_name, axis=payload.axis, kind=payload.name,
                             principal=principal.principal_id, entry=entry,
                             subject=subject_entry)
        try:
            with _LOCK:
                path = owner_add_entries(pack_name, additions,
                                         comment=_comment("Defined", who, principal, at,
                                                          payload.reason))
        except PackWriteError as exc:
            complete_audit(op_id, {"action": "define_failed", "pack": pack_name,
                                   "axis": payload.axis, "kind": payload.name,
                                   "principal": principal.principal_id, "error": str(exc)})
            return _refusal("PACK_WRITE_FAILED", str(exc), status=422)
        audit = complete_audit(op_id, {
            "action": "define", "pack": pack_name, "axis": payload.axis, "kind": payload.name,
            "principal": principal.principal_id, "by": who, "by_label": label,
            "reason": payload.reason or None,
            "entry": entry, "subject": subject_entry,
            "identity_classes": classes, "identity_confirmed": bool(classes), "file": str(path)})
        return _with_reload({"pack": pack_name, "axis": payload.axis, "kind": payload.name,
                             "entry": entry, "subject": subject_entry is not None,
                             "identity_classes": classes, "written_to": str(path),
                             "audit": audit,
                             "in_active_vocabulary": _in_vocab(payload.axis, payload.name)},
                            pack_name)

    @app.post("/ontology/kinds/rename")
    def rename_kind(payload: RenameKindRequest = Body(...),
                    principal: AuthenticatedPrincipal = Depends(require_owner)):
        """Rename = add ``new_name`` with the old declaration, then deprecate the old kind. The old
        declaration stays in the file (deprecate-never-delete), so every stored node or edge of the
        old kind still reads back."""
        from databridge_graph.packs import load_pack

        name, refused = _active_pack(payload.pack)
        if refused is not None:
            return refused
        pack_name = str(name)
        if payload.axis not in AXES:
            return _refusal("BAD_AXIS", f"axis must be one of {list(AXES)}")
        if not _KIND_NAME.match(payload.new_name or ""):
            return _refusal("BAD_KIND_NAME", "the new name must be lowercase snake_case")
        try:
            view = _pack_view(pack_name, load_state())
            pack = load_pack(pack_name)
            subjects = _subjects_for(pack_name)
        except OntologyStateError:
            raise
        except Exception as exc:                                     # noqa: BLE001
            return _refusal("PACK_DID_NOT_LOAD", str(exc), status=503)
        old = _find_kind(view, payload.axis, payload.kind)
        if old is None:
            return _refusal("UNKNOWN_KIND",
                            f"pack {pack_name!r} declares no {payload.axis} kind {payload.kind!r}",
                            status=404)
        if old["deprecated"]:
            return _refusal("ALREADY_DEPRECATED",
                            f"{payload.kind!r} is deprecated; reactivate it before renaming")
        # A rename a crash interrupted AFTER its pack write committed (R-019 finding 4): the
        # journal holds it `in_progress` and the new kind is already in the file. Retrying the
        # same rename resumes it — only the deprecation step is left — instead of refusing
        # KIND_ALREADY_DECLARED and stranding the old kind active beside its replacement.
        resume_op = _open_rename_op(pack_name, payload.axis, payload.kind, payload.new_name)
        new_exists = _find_kind(view, payload.axis, payload.new_name) is not None
        if new_exists and resume_op is None:
            return _refusal("KIND_ALREADY_DECLARED",
                            f"pack {pack_name!r} already declares {payload.axis} kind "
                            f"{payload.new_name!r}")
        description = (payload.description if payload.description is not None
                       else old["description"]) or ""
        entry: dict[str, Any] = {"name": payload.new_name, "binds_to": old["binds_to"]}
        carried: list[tuple[str, list[str], list[str]]] = []
        if payload.axis == "edge":
            entry["src"], entry["dst"] = list(old["from"]), list(old["to"])
            classes = identity_classes(axis="edge", binds_to=old["binds_to"],
                                       src=tuple(old["from"]), dst=tuple(old["to"]),
                                       subjects=subjects)
        else:
            classes = ["subject_rename"] if old["subject"] else []
            if payload.carry_relations:
                for e in view["edge_kinds"]:
                    if payload.new_name in e["from"] or payload.new_name in e["to"]:
                        continue                      # already widened (a resumed rename)
                    if payload.kind in e["from"] or payload.kind in e["to"]:
                        new_src = e["from"] + ([payload.new_name] if payload.kind in e["from"]
                                               else [])
                        new_dst = e["to"] + ([payload.new_name] if payload.kind in e["to"]
                                             else [])
                        carried.append((e["kind"], new_src, new_dst))
                        # The widened relation touches a subject if either end already did, or
                        # if the renamed kind is itself a subject.
                        classes += identity_classes(
                            axis="edge", binds_to=e["binds_to"], src=tuple(new_src),
                            dst=tuple(new_dst),
                            subjects=subjects | ({payload.new_name} if old["subject"] else set()))
        if description.strip():
            entry["description"] = " ".join(description.split())
        classes = list(dict.fromkeys(classes))
        gate = _identity_gate(classes, payload.confirm_identity)
        if gate is not None:
            return gate

        label, bad = _label_or_refusal(payload.by, "by")
        if bad is not None:
            return bad
        who, at = principal.principal_id, _now()
        comment = _comment(f"Renamed from {payload.kind!r}", who, principal, at, payload.reason)
        additions: list[tuple[str, dict[str, Any]]] = [
            ("node_kinds" if payload.axis == "node" else "edge_kinds", entry)]
        subject_decl = pack.subject_kinds.get(payload.kind) if payload.axis == "node" else None
        if subject_decl is not None:
            additions.append(("subject_kinds", {
                "kind": payload.new_name, "identifiers": list(subject_decl.identifiers),
                "never_fuzzy_merge": True, "attestation": subject_decl.attestation,
                "grant_type": subject_decl.grant_type}))
        rename_fields = {"pack": pack_name, "axis": payload.axis, "kind": payload.kind,
                         "new_name": payload.new_name, "principal": principal.principal_id}
        from databridge_graph.packs import packs_root

        path = packs_root() / pack_name / "ontology.yaml"
        if resume_op is not None and new_exists:
            # Resume: the single atomic pack write already committed; finish the state step.
            op_id = resume_op["op_id"]
            carried = []
        else:
            op_id = audit_intent("rename", **rename_fields,
                                 relations_to_widen=[c[0] for c in carried])
            with state_txn() as state:
                for op in state["operations"]:
                    if (op.get("type") == "rename" and op.get("status") == "in_progress"
                            and op.get("pack") == pack_name and op.get("kind") == payload.kind
                            and op.get("axis") == payload.axis):
                        op["status"] = "superseded"
                        op["superseded_by"] = op_id
                state["operations"].append({"op_id": op_id, "type": "rename",
                                            "status": "in_progress", "started_at": at,
                                            **rename_fields})
            # ONE verified atomic write: the new kind (and its subject declaration) AND every
            # widened relation, or nothing (R-019 finding 4).
            try:
                with _LOCK:
                    path = owner_apply_changes(
                        pack_name, additions=additions, endpoints=carried, comment=comment,
                        endpoint_comment=_comment(
                            f"Endpoints widened for rename {payload.kind!r} -> "
                            f"{payload.new_name!r}", who, principal, at, ""))
            except PackWriteError as exc:
                _set_op(op_id, status="failed", error=str(exc))
                complete_audit(op_id, {"action": "rename_failed", **rename_fields,
                                       "error": str(exc)})
                return _refusal("PACK_WRITE_FAILED",
                                f"the rename was not applied — nothing in the pack changed and "
                                f"{payload.kind!r} is still active: {exc}", status=422)
        try:
            with state_txn() as state:
                state.setdefault("deprecated_kinds", []).append({
                    "pack": pack_name, "axis": payload.axis, "kind": payload.kind, "by": who,
                    "by_label": label, "at": at, "reason": f"renamed to {payload.new_name}"
                    + (f" — {payload.reason}" if payload.reason else ""),
                    "principal": principal.principal_id, "renamed_to": payload.new_name,
                    "op_id": op_id})
                for op in state["operations"]:
                    if op.get("op_id") == op_id:
                        op["status"] = "done"
                        op["finished_at"] = _now()
        except OntologyStateError as exc:
            return _refusal(
                "RENAME_INCOMPLETE",
                f"{payload.new_name!r} and its relations were written to the pack, but deprecating "
                f"{payload.kind!r} could not be recorded ({exc}). The operation {op_id} is "
                f"journalled as in progress: repeat this same rename to finish it.",
                status=503, op_id=op_id)
        widened = [c[0] for c in carried]
        audit = complete_audit(op_id, {
            "action": "rename", **rename_fields, "by": who, "by_label": label,
            "reason": payload.reason or None, "relations_widened": widened,
            "resumed": resume_op is not None and new_exists,
            "identity_classes": classes, "identity_confirmed": bool(classes), "file": str(path)})
        return _with_reload({"pack": pack_name, "axis": payload.axis, "kind": payload.kind,
                             "new_name": payload.new_name, "old_deprecated": True,
                             "relations_widened": widened, "identity_classes": classes,
                             "written_to": str(path), "audit": audit, "op_id": op_id,
                             "in_active_vocabulary": _in_vocab(payload.axis, payload.new_name)},
                            pack_name)

    def _open_rename_op(pack: str, axis: str, kind: str, new_name: str
                        ) -> Optional[dict[str, Any]]:
        for op in reversed(load_state()["operations"]):
            if (op.get("type") == "rename" and op.get("status") == "in_progress"
                    and op.get("pack") == pack and op.get("axis") == axis
                    and op.get("kind") == kind and op.get("new_name") == new_name):
                return op
        return None

    def _set_op(op_id: str, **fields: Any) -> None:
        with state_txn() as state:
            for op in state["operations"]:
                if op.get("op_id") == op_id:
                    op.update(fields)

    @app.post("/ontology/kinds/endpoints")
    def set_endpoints(payload: EndpointsRequest = Body(...),
                      principal: AuthenticatedPrincipal = Depends(require_owner)):
        """Set which entity kinds a relation may connect — what extraction enforces on every edge.
        """
        name, refused = _active_pack(payload.pack)
        if refused is not None:
            return refused
        pack_name = str(name)
        try:
            view = _pack_view(pack_name, load_state())
            subjects = _subjects_for(pack_name)
        except OntologyStateError:
            raise
        except Exception as exc:                                     # noqa: BLE001
            return _refusal("PACK_DID_NOT_LOAD", str(exc), status=503)
        edge = _find_kind(view, "edge", payload.kind)
        if edge is None:
            return _refusal("UNKNOWN_KIND", f"pack {pack_name!r} declares no relation "
                            f"{payload.kind!r}", status=404)
        src = tuple(dict.fromkeys(payload.src))
        dst = tuple(dict.fromkeys(payload.dst))
        bad = _check_endpoints(view, pack_name, src, dst)
        if bad is not None:
            return bad
        if list(src) == edge["from"] and list(dst) == edge["to"]:
            return _refusal("NO_CHANGE", "those are already the relation's endpoints")
        classes = list(dict.fromkeys(
            identity_classes(axis="edge", binds_to=edge["binds_to"], src=src, dst=dst,
                             subjects=subjects)
            + identity_classes(axis="edge", binds_to=edge["binds_to"],
                               src=tuple(edge["from"]), dst=tuple(edge["to"]),
                               subjects=subjects)))
        gate = _identity_gate(classes, payload.confirm_identity)
        if gate is not None:
            return gate
        label, bad = _label_or_refusal(payload.by, "by")
        if bad is not None:
            return bad
        who, at = principal.principal_id, _now()
        op_id = audit_intent("set_endpoints", pack=pack_name, axis="edge", kind=payload.kind,
                             principal=principal.principal_id,
                             after={"src": list(src), "dst": list(dst)})
        try:
            with _LOCK:
                path = owner_set_endpoints(pack_name, payload.kind, list(src), list(dst),
                                           comment=_comment("Endpoints set", who, principal, at,
                                                            payload.reason))
        except PackWriteError as exc:
            complete_audit(op_id, {"action": "set_endpoints_failed", "pack": pack_name,
                                   "axis": "edge", "kind": payload.kind,
                                   "principal": principal.principal_id, "error": str(exc)})
            return _refusal("PACK_WRITE_FAILED", str(exc), status=422)
        audit = complete_audit(op_id, {
            "action": "set_endpoints", "pack": pack_name, "axis": "edge", "kind": payload.kind,
            "principal": principal.principal_id, "by": who, "by_label": label,
            "reason": payload.reason or None,
            "before": {"src": edge["from"], "dst": edge["to"]},
            "after": {"src": list(src), "dst": list(dst)},
            "identity_classes": classes, "identity_confirmed": bool(classes), "file": str(path)})
        return _with_reload({"pack": pack_name, "kind": payload.kind, "src": list(src),
                             "dst": list(dst), "identity_classes": classes,
                             "written_to": str(path), "audit": audit}, pack_name)

    @app.get("/ontology/audit")
    def get_audit(pack: Optional[str] = None, limit: int = 100,
                  _principal: AuthenticatedPrincipal = Depends(require_principal)):
        """The append-only record of every vocabulary change, newest first. Gated: it names
        principals."""
        return {"path": str(audit_path()),
                "entries": read_audit(pack, limit=max(1, min(int(limit), 1000)))}
