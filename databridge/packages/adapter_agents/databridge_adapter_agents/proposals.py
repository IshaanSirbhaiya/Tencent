"""Ontology proposals from recurring off-ontology refusals — FILED, never applied.

Extraction is closed-vocabulary (§5.1): a kind the ontology does not hold is refused and recorded.
When an adapter agent keeps meeting the same refused kind — at least `min_occurrences` times across
at least `min_documents` distinct documents — it files ONE proposal for that kind, carrying the
evidence pointers that motivated it, for a human to decide on `/ontology`.

What this module will never do:

* **apply** a proposal. It writes a record with `status: "filed"`, `applied: false`; nothing here
  touches a pack, `ontology.yaml`, or the gateway's ontology state.
* **approve** one. Every record carries `requires_human_approval: true` and
  `auto_approvable: false`. A proposal that touches a declared **subject** kind is additionally
  flagged `touches_subject: true` with the reason, so a downstream forwarder can see it is outside
  every auto-approve envelope (CLAUDE.md hard constraint 1, spine D4b; growth
  `STRUCTURAL_ESCALATIONS`).
* **trust** the refused kind name. It came out of a model reading untrusted content; it is reduced
  to the pack kind grammar (`^[a-z][a-z0-9_]*$`) or dropped, and it never selects a code path.
* **count an ungrounded refusal.** An occurrence contributes to either threshold only when it
  carries a valid `SourcePointer` built from exactly the fields `mcp_ingest` mints
  (`source_id` = the connection, `document_id` = the sha256 of the full path, a real span, the
  capture digest) — P4. A refusal naming no document, a URI or digest instead of the hashed id, a
  different source, or no span is recorded as `ungrounded` with its reason and never proposed.
  So no proposal is ever filed without evidence.

Recurrence ACCUMULATES across runs: every grounded occurrence is persisted (deduplicated by its
pointer + kind + text) in `_proposals/occurrences/<agent>.jsonl`, so a kind met once per run over
three runs reaches a threshold of three even after old run directories are pruned.

Records append to `<adapter_agents_dir>/_proposals/ontology_proposals.jsonl`. Each carries a
`request` block shaped exactly like the gateway's `POST /ontology/proposals` body, so the gateway
(or a human) can forward it through the route that owns the decision.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Protocol

from databridge_workflow_spec import AdapterAgentSpec

from . import paths
from .events import now_iso
from .ingest_service import IngestRunReport, is_hashed_path_id

_KIND_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_MAX_EVIDENCE = 10


@dataclasses.dataclass(frozen=True)
class ProposalPolicy:
    min_occurrences: int = 3
    min_documents: int = 2
    #: The meta-kind a proposal is filed against. A refusal carries no meta-kind, so this is a
    #: labelled default (`binds_to_basis`) for the reviewer to confirm — not a claim about the data.
    default_binds_to: dict[str, str] = dataclasses.field(
        default_factory=lambda: {"node": "entity", "edge": "references"})

    def __post_init__(self) -> None:
        if self.min_occurrences < 1 or self.min_documents < 1:
            raise ValueError("proposal thresholds must be at least 1")


class VocabularyView(Protocol):
    def known_kinds(self, axis: str) -> set[str]: ...
    def subject_kinds(self) -> set[str]: ...


class ActiveVocabularyView:
    """The deployment's active vocabulary (`databridge_graph.packs.active_vocabulary`), read
    lazily. If it cannot load, both sets are empty and `problem` says why — nothing is approved
    either way, so an unreadable vocabulary can only cause a duplicate proposal, never an
    unreviewed kind."""

    def __init__(self) -> None:
        self.problem: Optional[str] = None
        self._vocab = None

    def _load(self):
        if self._vocab is None and self.problem is None:
            try:
                from databridge_graph.packs import active_vocabulary

                self._vocab = active_vocabulary()
            except Exception as exc:                                   # noqa: BLE001
                self.problem = f"vocabulary unavailable: {exc}"
        return self._vocab

    def known_kinds(self, axis: str) -> set[str]:
        v = self._load()
        if v is None:
            return set()
        return {str(k) for k in (v.node_kinds if axis == "node" else v.edge_kinds)}

    def subject_kinds(self) -> set[str]:
        v = self._load()
        if v is None:
            return set()
        return {str(s.kind) for s in v.subject_kinds.values()}


def _clean_kind(raw: str) -> Optional[str]:
    k = re.sub(r"[^a-z0-9_]+", "_", str(raw).strip().lower()).strip("_")
    return k if _KIND_NAME.match(k) and len(k) <= 64 else None


def _identity_fn() -> Optional[Callable[[str, str], str]]:
    """`mcp_ingest`'s own identity rule, when importable — never re-derived here."""
    try:
        from databridge_mcp_ingest.crawl import item_identity
    except Exception:                                                  # noqa: BLE001
        return None
    return item_identity


def ground(r: dict[str, Any], *, identity: Optional[Callable[[str, str], str]] = None
           ) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """(validated SourcePointer as JSON, None) or (None, why it is not evidence).

    The pointer is exactly what `mcp_ingest.build.span_pointer` mints: `source_id` is the
    connection, `document_id` the hashed full path, the span real, the digest the capture's."""
    from databridge_core import SourcePointer

    source = r.get("source_id")
    if not source:
        return None, "no source"
    claimed = r.get("claimed_source_id")
    if claimed and claimed != source:
        return None, f"names source {claimed!r}, not the connection it was read from"
    doc = r.get("document_id")
    if isinstance(r.get("pointer"), dict):
        try:
            ptr = SourcePointer.model_validate(r["pointer"])
        except Exception as exc:                                       # noqa: BLE001
            return None, f"pointer does not validate: {type(exc).__name__}"
        if ptr.source_id != source:
            return None, f"pointer names source {ptr.source_id!r}, not the connection"
        if ptr.kind == "table_cell":
            # a captured table row (an unmapped table's evidence): the cell must be addressable
            # — table, column, row and the capture it was read from — and the document id the
            # hashed full table reference (P4)
            if not (ptr.table and ptr.column and ptr.row_id and ptr.capture_digest):
                return None, "table_cell pointer lacks table/column/row/capture digest"
            if not is_hashed_path_id(doc):
                return None, "document id is not the hashed full path (P4)"
            return ptr.model_dump(mode="json", exclude_none=True), None
        if ptr.kind != "doc_span":
            return None, f"pointer kind {ptr.kind!r} does not localise a document span"
        if doc and ptr.document_id != doc:
            return None, "pointer document differs from the refusal's document"
        doc = ptr.document_id
        if not is_hashed_path_id(doc):
            return None, "document id is not the hashed full path (P4)"
        if ptr.char_start is None or ptr.char_end is None or ptr.char_end <= ptr.char_start:
            return None, "pointer has no real span"
        return ptr.model_dump(mode="json", exclude_none=True), None
    if not doc:
        return None, "no document id"
    if not is_hashed_path_id(doc):
        return None, "document id is not the hashed full path (P4)"
    if identity is not None and r.get("uri"):
        try:
            if identity(source, str(r["uri"])) != doc:
                return None, "document id does not match the hash of its full path"
        except Exception as exc:                                       # noqa: BLE001
            return None, f"identity check failed: {type(exc).__name__}"
    cs, ce = r.get("char_start"), r.get("char_end")
    if not (isinstance(cs, int) and isinstance(ce, int)) or isinstance(cs, bool) or \
            not 0 <= cs < ce:
        return None, "no real span (the refusal cannot be located in its document)"
    try:
        ptr = SourcePointer(source_id=str(source), kind="doc_span", document_id=str(doc),
                            char_start=cs, char_end=ce, capture_digest=r.get("capture_digest"),
                            trust="untrusted")
    except Exception as exc:                                           # noqa: BLE001
        return None, f"pointer does not validate: {type(exc).__name__}"
    return ptr.model_dump(mode="json", exclude_none=True), None


def _occurrence_key(axis: str, name: str, r: dict[str, Any], pointer: dict[str, Any]) -> str:
    basis = {"axis": axis, "name": name, "entity": r.get("name"), "text": r.get("text"),
             "pointer": pointer}
    return hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()


def collect_occurrences(reports: Iterable[IngestRunReport]
                        ) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, int]]]:
    """(grounded occurrences, ungrounded tally {(axis, name): {reason: n}}) for kind refusals."""
    identity = _identity_fn()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    ungrounded: dict[tuple[str, str], dict[str, int]] = {}
    for rep in reports:
        for r in rep.refusals:
            name = _clean_kind(r["kind"])
            if name is None:
                continue
            ptr, why = ground(r, identity=identity)
            if r.get("invalid_record") and ptr is not None:
                # a line that failed mcp_ingest's typed format is never evidence
                ptr, why = None, (f"the refusal line does not match the typed format "
                                  f"({str(r['invalid_record'])[:120]})")
            if ptr is None:
                tally = ungrounded.setdefault((r["axis"], name), {})
                tally[str(why)] = tally.get(str(why), 0) + 1
                continue
            key = _occurrence_key(r["axis"], name, r, ptr)
            if key in seen:
                continue
            seen.add(key)
            out.append({"key": key, "axis": r["axis"], "name": name,
                        "raw_kind": str(r["kind"])[:80], "entity": r.get("name"),
                        "document_id": ptr["document_id"], "uri": r.get("uri"),
                        "source_id": ptr["source_id"], "run_id": r.get("run_id"),
                        "pointer": ptr, "span_basis": r.get("span_basis") or "refusal",
                        "src_kind": _clean_kind(r["src_kind"]) if r.get("src_kind") else None,
                        "dst_kind": _clean_kind(r["dst_kind"]) if r.get("dst_kind") else None,
                        "text": str(r.get("text") or "")[:200],
                        "produced_by": r.get("produced_by")})
    return out, ungrounded


class OccurrenceStore:
    """Append-only JSONL of grounded occurrences one agent has seen, so recurrence accumulates
    across runs (and survives pruning of old run directories)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.unreadable = 0

    def all(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        self.unreadable = 0
        if not self.path.is_file():
            return out
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                self.unreadable += 1
                continue
            if isinstance(rec, dict) and rec.get("key"):
                out.setdefault(rec["key"], rec)
        return out

    def merge(self, occurrences: Iterable[dict[str, Any]], *, at: str
              ) -> tuple[dict[str, dict], int]:
        """(every occurrence known after the merge, how many were new)."""
        known = self.all()
        new = [o for o in occurrences if o["key"] not in known]
        if new:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "ab") as fh:
                for o in new:
                    rec = {**o, "first_seen": at}
                    fh.write(json.dumps(rec, sort_keys=True, default=str).encode("utf-8") + b"\n")
                    known[o["key"]] = rec
                fh.flush()
        return known, len(new)


@dataclasses.dataclass
class Candidate:
    axis: str
    name: str
    raw_names: set[str]
    occurrences: int = 0
    documents: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)
    evidence: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    src_kinds: set[str] = dataclasses.field(default_factory=set)
    dst_kinds: set[str] = dataclasses.field(default_factory=set)
    examples: list[str] = dataclasses.field(default_factory=list)
    evidence_produced_by: list[Any] = dataclasses.field(default_factory=list)


def find_candidates(occurrences: Iterable[dict[str, Any]], policy: ProposalPolicy, *,
                    known: Callable[[str], set[str]],
                    ungrounded: Optional[dict[tuple[str, str], dict[str, int]]] = None
                    ) -> tuple[list[Candidate], list[dict]]:
    """Group GROUNDED occurrences; return (candidates meeting the thresholds, below-threshold
    notes). Only grounded occurrences count; the ungrounded tally is reported beside them."""
    ungrounded = ungrounded or {}
    groups: dict[tuple[str, str], Candidate] = {}
    for o in sorted(occurrences, key=lambda o: (o.get("first_seen") or "", o["key"])):
        c = groups.setdefault((o["axis"], o["name"]), Candidate(o["axis"], o["name"], set()))
        c.raw_names.add(str(o.get("raw_kind") or o["name"])[:80])
        c.occurrences += 1
        c.documents.setdefault(o["document_id"], {"document_id": o["document_id"],
                                                  "uri": o.get("uri"),
                                                  "source_id": o.get("source_id"),
                                                  "run_id": o.get("run_id")})
        if len(c.evidence) < _MAX_EVIDENCE and o["pointer"] not in c.evidence:
            c.evidence.append(o["pointer"])
        for side, bucket in (("src_kind", c.src_kinds), ("dst_kind", c.dst_kinds)):
            if o.get(side):
                bucket.add(o[side])
        if o.get("text") and len(c.examples) < 3:
            c.examples.append(o["text"])
        if o.get("produced_by") and o["produced_by"] not in c.evidence_produced_by:
            c.evidence_produced_by.append(o["produced_by"])
    out, below = [], []
    names = sorted(set(groups) | set(ungrounded))
    for axis, name in names:
        c = groups.get((axis, name))
        ung = ungrounded.get((axis, name), {})
        if name in known(axis):
            below.append({"axis": axis, "name": name, "why": "the ontology now declares it"})
            continue
        if c is not None and c.occurrences >= policy.min_occurrences and \
                len(c.documents) >= policy.min_documents:
            out.append(c)
            continue
        below.append({"axis": axis, "name": name,
                      "occurrences": c.occurrences if c else 0,
                      "documents": len(c.documents) if c else 0,
                      "ungrounded": sum(ung.values()), "ungrounded_reasons": dict(ung),
                      "why": f"below threshold (needs >= {policy.min_occurrences} grounded "
                             f"occurrences across >= {policy.min_documents} documents)"})
    return out, below


class ProposalLog:
    """Append-only JSONL of filed proposals."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or (paths.adapter_agents_dir() / "_proposals" / "ontology_proposals.jsonl")

    def all(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "ab") as fh:
            fh.write(json.dumps(record, sort_keys=True).encode("utf-8") + b"\n")
            fh.flush()


def file_proposals(spec: AdapterAgentSpec, reports: Iterable[IngestRunReport], *,
                   policy: ProposalPolicy, log: ProposalLog,
                   vocabulary: VocabularyView,
                   occurrences: Optional[OccurrenceStore] = None,
                   produced_by: Optional[dict[str, Any]] = None,
                   extra_occurrences: Optional[Iterable[dict[str, Any]]] = None
                   ) -> dict[str, Any]:
    """Collect grounded occurrences from `reports`, merge them into this agent's persisted
    occurrences, dedupe against what this agent already filed, and file new candidates."""
    pack = spec.extract.pack
    at = now_iso()
    store = occurrences or OccurrenceStore(log.path.parent / "occurrences" / f"{spec.id}.jsonl")
    fresh, ungrounded = collect_occurrences(reports)
    # grounded occurrences from other rules (e.g. adaptation: a table no kind maps), each already
    # passed through `ground()` by its producer — same thresholds, same dedupe, same record
    fresh = fresh + [o for o in (extra_occurrences or []) if o.get("pointer") and o.get("key")]
    everything, n_new = store.merge(fresh, at=at)
    candidates, below = find_candidates(everything.values(), policy,
                                        known=vocabulary.known_kinds, ungrounded=ungrounded)
    subjects = vocabulary.subject_kinds()
    proposer = f"adapter_agent:{spec.id}"
    # §5.8: the proposal itself is a deterministic rule over recorded refusals — no model call.
    who = {"agent": proposer, "agent_version": None, "task_class": None, "model": None,
           "prompt_version": None, "run_id": None, "at": at, "mode": "rules",
           **dict(produced_by or {})}
    already = {(p.get("pack"), p.get("axis"), p.get("name")) for p in log.all()
               if p.get("proposed_by") == proposer and p.get("status") == "filed"}
    filed, skipped = [], []
    for c in candidates:
        if (pack, c.axis, c.name) in already:
            skipped.append({"axis": c.axis, "name": c.name, "why": "already filed by this agent"})
            continue
        if not c.evidence:                      # unreachable: every counted occurrence is grounded
            skipped.append({"axis": c.axis, "name": c.name, "why": "no grounded evidence"})
            continue
        src, dst = sorted(c.src_kinds), sorted(c.dst_kinds)
        touched = sorted(({c.name} if c.axis == "node" else set()) | (set(src) | set(dst)))
        touched_subjects = sorted(set(touched) & subjects)
        binds_to = policy.default_binds_to[c.axis]
        description = (f"Refused {c.occurrences} time(s) across {len(c.documents)} document(s) "
                       f"by adapter agent {spec.id!r} because the ontology has no "
                       f"{c.axis} kind {c.name!r}.")
        request = {"pack": pack, "axis": c.axis, "name": c.name, "binds_to": binds_to,
                   "src": src, "dst": dst, "description": description,
                   "proposed_by": proposer, "declares_subject": False,
                   "evidence": list(c.evidence)}
        content = json.dumps({k: request[k] for k in ("pack", "axis", "name", "proposed_by")},
                             sort_keys=True)
        record = {
            "proposal_id": "aap_" + hashlib.sha256(content.encode()).hexdigest()[:16],
            "filed_at": at, "status": "filed", "applied": False,
            "requires_human_approval": True, "auto_approvable": False,
            "touches_subject": bool(touched_subjects),
            "subject_reason": (f"names declared subject kind(s) {touched_subjects}; never "
                               f"auto-approved (spine D4b)") if touched_subjects else None,
            "pack": pack, "axis": c.axis, "name": c.name, "proposed_by": proposer,
            "adapter_agent": spec.id, "binds_to": binds_to,
            "binds_to_basis": "default for the axis — a refusal carries no meta-kind; the "
                              "reviewer confirms or changes it",
            "occurrences": c.occurrences, "document_count": len(c.documents),
            "ungrounded_refusals": sum(ungrounded.get((c.axis, c.name), {}).values()),
            "documents": list(c.documents.values())[:_MAX_EVIDENCE],
            "evidence": list(c.evidence), "evidence_ungrounded": False,
            "raw_names": sorted(c.raw_names), "examples": c.examples,
            "produced_by": who, "evidence_produced_by": c.evidence_produced_by or None,
            "request": request,
            "forward_to": "POST /ontology/proposals",
        }
        log.append(record)
        filed.append(record)
    return {"filed": filed, "already_filed": skipped, "below_threshold": below,
            "vocabulary_problem": getattr(vocabulary, "problem", None),
            "occurrences_total": len(everything), "occurrences_new": n_new,
            "ungrounded": sum(sum(v.values()) for v in ungrounded.values()),
            "occurrence_log_unreadable_lines": store.unreadable}


__all__ = ["ProposalPolicy", "ProposalLog", "VocabularyView", "ActiveVocabularyView",
           "find_candidates", "file_proposals", "Candidate", "OccurrenceStore",
           "collect_occurrences", "ground"]
