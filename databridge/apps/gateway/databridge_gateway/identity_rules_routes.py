"""`/ontology/identity-rules` — a person approves (or revokes) a pack's declared identity rule
(LINK7 for GRAPH6, FIN-04; `wiki/20-decisions/D-004`).

    GET  /ontology/identity-rules           every bound pack's declared rules, each rule type's
                                            decision state, and a PREVIEW of what approving it
                                            would merge in the graph as it is now
    POST /ontology/identity-rules/decide    approve / revoke ONE rule type as the signed-in
                                            principal; an approval then runs
                                            `merge.consolidate_identity` on the graph

A pack may declare, for a NON-subject kind (an organisation), that records sharing a business key
or an exact normalised legal name are one record. The rule is INERT until a named human approves
it. A person (a subject kind) can carry no rule, and `ontology_state.record_identity_rule_decision`
refuses one; this route never decides on anyone's behalf: the principal is the AUTHENTICATED
session's, checked by the shared approval gate (a blank or unauthenticated principal is refused),
and an approval made with the public dev token is marked as such.

The preview runs `consolidate_identity` on an in-memory COPY of the graph, so it is the engine's
own answer and it changes nothing. Nothing here names a pack, a kind or a key.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import Body, Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .auth import AuthenticatedPrincipal, require_principal
from .public_errors import error_ref, exception_refusal, refusal_body

log = logging.getLogger("databridge.gateway.identity_rules")

#: How many merge groups a preview shows by name.
SAMPLE_GROUPS = 5


class IdentityRuleDecision(BaseModel):
    """Which declared rule, and the decision. Who decides is the signed-in principal; a body name
    is ignored (``extra="ignore"``)."""
    model_config = ConfigDict(extra="ignore")
    pack: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    rule: str = Field(min_length=1)
    decision: Literal["approved", "revoked"] = "approved"


#: Fixed public sentences (R-029 TIDY2, CONS standard 4): never an exception's text. The detail
#: is in the server log under the payload's ``error_ref``.
RULE_REFUSED = ("that identity rule cannot be decided: only a rule a bound pack declares, on a "
                "kind that is not a person, can be approved")
RULES_UNREADABLE = "the identity rules could not be read"
PREVIEW_FAILED = "the preview could not be computed"


def _refusal(code: str, message: str, status: int, *, ref: Optional[str] = None) -> JSONResponse:
    return JSONResponse(refusal_body(code, message, ref=ref), status_code=status)


def _store(app: FastAPI) -> Any:
    store = getattr(app.state, "graph_store", None)
    if store is None:
        brain = getattr(app.state, "brain", None)
        store = getattr(brain, "store", None)
    return store


def audit_path() -> Path:
    """The gateway's own append-only record of HOW each decision's principal signed in (the
    ontology state records who and when; this adds `authenticated_by` / `dev_only`)."""
    from databridge_graph.ontology_state import state_path        # noqa: PLC0415

    return state_path().with_name("identity_rule_decisions.audit.jsonl")


def _audit_rows() -> list[dict]:
    p = audit_path()
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _append_audit(rec: dict) -> None:
    p = audit_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


def _decisions(packs) -> dict[tuple[str, str, str], dict]:
    """The LAST decision per (pack, kind, rule), with how its principal signed in."""
    from databridge_graph.ontology_state import read_state         # noqa: PLC0415

    audit = {(a.get("pack"), a.get("kind"), a.get("rule"), a.get("principal"), a.get("at")): a
             for a in _audit_rows()}
    last: dict[tuple[str, str, str], dict] = {}
    for rec in read_state().get("identity_rule_decisions", []) or []:
        if rec.get("pack") in packs:
            a = audit.get((rec["pack"], rec["kind"], rec["rule"], rec["principal"], rec["at"]))
            last[(rec["pack"], rec["kind"], rec["rule"])] = {
                **rec, "dev_only": (a or {}).get("dev_only"),
                "authenticated_by": (a or {}).get("authenticated_by")}
    return last


def _labels(store, ids: list[str]) -> list[str]:
    out = []
    for i in ids:
        row = store.open_node_row(i)
        if row is None:
            row = next(iter(store.conn.execute(
                "SELECT label FROM nodes WHERE id = ? ORDER BY rowid_pk DESC LIMIT 1", (i,))),
                None)
        out.append(row["label"] if row is not None else i)
    return out


def preview(store, vocab, kind: str, rule: str, approved_now: frozenset[str]) -> dict:
    """What the graph would look like if ``rule`` were approved for ``kind`` on top of what is
    approved now: `consolidate_identity` on an in-memory copy of the store."""
    from databridge_graph.merge import consolidate_identity        # noqa: PLC0415
    from databridge_graph.store import GraphStore                  # noqa: PLC0415

    copy = GraphStore(":memory:")
    store.conn.backup(copy.conn)
    report = consolidate_identity(copy, vocab, approved={kind: approved_now | {rule}})
    merged = [m for m in report.merged if m["kind"] == kind]
    refused = [r for r in report.refused if r["kind"] == kind]
    groups = []
    for m in merged[:SAMPLE_GROUPS]:
        ids = [m["survivor"], *m["absorbed"]]
        groups.append({"survivor": m["survivor"], "records": ids, "labels": _labels(store, ids),
                       "rules": m["rules"]})
    return {"groups": len(merged), "records_absorbed": sum(len(m["absorbed"]) for m in merged),
            "sample_groups": groups, "refused": len(refused),
            "refused_samples": [{"records": r["records"][:SAMPLE_GROUPS],
                                 "reason": r["reason"]} for r in refused[:SAMPLE_GROUPS]]}


def _is_approved(decision: Optional[dict]) -> bool:
    return (decision or {}).get("decision") == "approved"


def listing(app: FastAPI) -> dict:
    from databridge_graph.packs import active_vocabulary           # noqa: PLC0415

    vocab = active_vocabulary()
    store = _store(app)
    decided = _decisions(vocab.packs)
    rules = []
    for kind, rule in sorted(vocab.identity_rules.items()):
        approved_now = frozenset(t for t in rule.rule_types
                                 if _is_approved(decided.get((rule.pack, kind, t))))
        types = []
        for t in rule.rule_types:
            d = decided.get((rule.pack, kind, t))
            entry = {"rule": t, "state": (d or {}).get("decision") or "not_approved",
                     "decided_by": (d or {}).get("principal"), "decided_at": (d or {}).get("at"),
                     "dev_only": (d or {}).get("dev_only"), "preview": None,
                     "preview_note": None}
            if t not in approved_now:
                if store is None:
                    entry["preview_note"] = "no graph is loaded in this gateway"
                else:
                    try:
                        entry["preview"] = preview(store, vocab, kind, t, approved_now)
                    except Exception as exc:                        # noqa: BLE001
                        # a fixed sentence and a reference: never the exception's text
                        entry["preview_note"] = PREVIEW_FAILED
                        entry["preview_error_ref"] = error_ref(
                            log, "identity rule preview", exc, kind=kind, rule=t)
            types.append(entry)
        rules.append({"pack": rule.pack, "kind": kind,
                      "business_keys": list(rule.business_keys), "exact_name": rule.exact_name,
                      "similarity_merge": rule.similarity_merge, "rules": types})
    return {"packs": list(vocab.packs), "identity_rules": rules}


def register_identity_rules_routes(app: FastAPI) -> None:
    if getattr(app.state, "_identity_rules_routes", False):
        return
    app.state._identity_rules_routes = True

    @app.get("/ontology/identity-rules")
    def identity_rules_list(_p: AuthenticatedPrincipal = Depends(require_principal)):
        try:
            return listing(app)
        except Exception as exc:                                    # noqa: BLE001
            return _refusal("IDENTITY_RULES_UNREADABLE", RULES_UNREADABLE, 500,
                            ref=error_ref(log, "identity rules listing", exc))

    @app.post("/ontology/identity-rules/decide")
    def identity_rules_decide(body: IdentityRuleDecision = Body(...),
                              principal: AuthenticatedPrincipal = Depends(require_principal)):
        from databridge_graph.merge import consolidate_identity    # noqa: PLC0415
        from databridge_graph.ontology_state import record_identity_rule_decision  # noqa: PLC0415
        from databridge_graph.packs import active_vocabulary       # noqa: PLC0415
        from databridge_mcp_ingest.plans import (ApprovalRefused,  # noqa: PLC0415
                                                 require_authenticated_approver)

        try:
            who = require_authenticated_approver(principal, what="identity rule decision")
        except ApprovalRefused as exc:
            # the standard codes (CONS standard 3), from the gate's own code: MACHINE_ACTOR for a
            # machine principal, APPROVER_NOT_AUTHENTICATED otherwise; a fixed sentence, never
            # the gate's text
            return exception_refusal(log, exc, default_code="APPROVER_NOT_AUTHENTICATED",
                                     status=403, context="identity rule decision")
        vocab = active_vocabulary()
        at = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            rec = record_identity_rule_decision(pack=body.pack, kind=body.kind, rule=body.rule,
                                                principal=who["principal_id"],
                                                decision=body.decision, vocab=vocab, at=at)
        except ValueError as exc:
            return _refusal("IDENTITY_RULE_REFUSED", RULE_REFUSED, 409,
                            ref=error_ref(log, "identity rule decision refused", exc,
                                          pack=body.pack, kind=body.kind, rule=body.rule))
        _append_audit({"pack": rec["pack"], "kind": rec["kind"], "rule": rec["rule"],
                       "decision": rec["decision"], "principal": rec["principal"],
                       "at": rec["at"], "authenticated_by": who.get("authenticated_by"),
                       "dev_only": bool(who.get("dev_only"))})
        report = None
        if body.decision == "approved":
            store = _store(app)
            if store is not None:
                report = consolidate_identity(store, vocab).as_record()
                reload = getattr(app.state, "reload_graph", None)
                if callable(reload):
                    try:
                        reload()
                    except Exception:                               # noqa: BLE001
                        log.exception("graph reload after an identity-rule approval failed")
        return {"decision": {**rec, "dev_only": bool(who.get("dev_only")),
                             "authenticated_by": who.get("authenticated_by")},
                "consolidation": report,
                "note": (None if body.decision == "approved" else
                         "a revocation stops FUTURE merges; records already merged stay one "
                         "record (closed versions are kept, never deleted)")}
