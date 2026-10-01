"""The SHARED TYPED FORMAT of everything ingestion writes that other packages read (BUILD_PLAN §5.2).

One pydantic model per artefact, with the field names `runner.py` / `build.py` write TODAY (read off
a real fixture run, 2026-10-01). Consumers (`packages/adapter_agents`, the gateway, the trace view)
import these and call `read_run(run_dir)`; they never re-parse a dict or a reason string by hand.
That is the fix for the class of bug R-017 found: a consumer whose fake wrote a different shape from
the real writer, so every real refusal was silently ignored.

Models are `extra="allow"`: a writer may ADD a field (e.g. §5.8 `produced_by`) without breaking a
reader. A field a writer always writes is REQUIRED here, so a writer that stops writing it — or
renames it — fails the contract test (`tests/test_aa_records.py`) instead of degrading silently.

`read_run(run_dir)` loads every artefact of one run. Ledgers are found BY PATTERN (`excluded*.jsonl`
includes a tagged variant such as `excluded.drift.jsonl`, P3). A line that does not validate is
**kept as a problem** (`RunArtefacts.problems`), never dropped and never used as evidence;
`strict=True` raises on the first one instead.

Also here, because both ingestion and adapter agents read them:

* `DRIFT_PENDING_APPROVAL` — the exclusion rule an adapter agent writes when it holds part of a
  source whose shape changed until a human approves (see `drift.py`), and `DriftHolds`, the holds
  file (`<sources>/<id>.holds.yaml`) the runner honours once P wires the hook.
* `AdaptationRecord` — the `adaptations.json` record an adapter agent writes into a run directory.
"""
from __future__ import annotations

import json
import re as _re
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

#: The exclusion rule for items an adapter agent holds while a shape drift awaits approval.
DRIFT_PENDING_APPROVAL = "DRIFT_PENDING_APPROVAL"


class ArtefactError(ValueError):
    """An artefact line does not match its typed format (raised only with `strict=True`)."""


class _Rec(BaseModel):
    model_config = ConfigDict(extra="allow")


# --- ledgers (one JSON object per line) ------------------------------------------------------------
class InventoryRecord(_Rec):
    """Every item the collector saw (`inventory.jsonl`), with the decision taken on it. A folder
    that was blocked or depth-capped is inventoried too (`kind: folder`, no name/step)."""
    item_id: str
    source_id: str
    kind: str                                   # document | table | folder
    uri: str
    rel: str
    decision: Literal["collect", "excluded", "blocked", "failed"]
    step: Optional[str] = None
    name: Optional[str] = None
    mime: Optional[str] = None
    size: Optional[int] = None


class ExclusionRecord(_Rec):
    """`excluded.jsonl` / `blocked.jsonl` (and tagged variants): the rule that fired and how to
    reverse it (P3). An AGGREGATE line (e.g. `outside_roots`: resources outside the declared roots
    are counted, never named) carries `item_id: null`, no uri/rel, and a `count`."""
    item_id: Optional[str]
    rule: str
    reverse: str
    at: str
    uri: Optional[str] = None
    rel: Optional[str] = None

    @model_validator(mode="after")
    def _named_or_counted(self) -> "ExclusionRecord":
        if self.item_id is None:
            if not isinstance((self.model_extra or {}).get("count"), int):
                raise ValueError("an exclusion without an item_id must be an aggregate line "
                                 "carrying an integer `count`")
        elif self.uri is None or self.rel is None:
            raise ValueError("an exclusion naming an item must carry its uri and rel")
        return self


class FailedRecord(_Rec):
    """`failed.jsonl`. A listing failure names no item (`item_id: null`, `stage: enumerate`)."""
    item_id: Optional[str]
    stage: str                                  # enumerate | fetch
    error: str
    at: str
    uri: Optional[str] = None
    step: Optional[str] = None


class CapturedRecord(_Rec):
    item_id: str
    uri: str
    name: str
    kind: str
    sha256: str
    bytes: int
    mime: Optional[str]
    step: str
    at: str
    #: anchors the capture's `.json` to this run (R-019 deferred #5). Optional so a run written
    #: before digests still READS; `runner.verify_run` flags a captured line without one (f1 #7).
    meta_digest: Optional[str] = None


class ExtractedRecord(_Rec):
    item_id: str
    uri: str
    sha256: str
    kind: str
    chars: int
    extractor: str
    name: Optional[str] = None
    warnings: list[Any] = Field(default_factory=list)
    cache_hit: Optional[bool] = None


class ExtractSkippedRecord(_Rec):
    item_id: str
    uri: str
    sha256: str
    reason: str                                 # no_extractor | extractor_unavailable | ...
    mime: Optional[str] = None
    detail: Optional[str] = None


class GovernanceRecord(_Rec):
    """ONE screen decision per extracted item (`governance.jsonl`)."""
    item_id: str
    uri: str
    sha256: str
    rules_source: str
    at: str
    decision: Literal["allow", "review", "block"]
    fired: list[Any]
    rationale: str
    reverse: str
    score: Optional[float]                      # null when a decisive rule blocked
    redactions: list[Any] = Field(default_factory=list)
    kind: Optional[str] = None


class GovernanceChunkRecord(_Rec):
    """The per-chunk screen (`governance_chunks.jsonl`). A chunk of a document carries its span; a
    table row the screen blocked carries `item_id: null` and `table` / `row_id` instead."""
    item_id: Optional[str]
    decision: Literal["allow", "review", "block"]
    chunk: Optional[int | str] = None           # int, or `bridge:<k>` for a cross-chunk window
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    table: Optional[str] = None
    row_id: Optional[str] = None
    fired: list[Any] = Field(default_factory=list)
    rationale: Optional[str] = None
    reverse: Optional[str] = None
    score: Optional[float] = None
    redactions: list[Any] = Field(default_factory=list)


class RefusedRecord(_Rec):
    """An extraction refusal (`refused.jsonl`): `reason` is the extractor's own sentence, prefixed
    `chunk <n>: ` (or `bridge <n>/<n+1>: ` for a cross-chunk window) by the build stage.
    `refusal()` parses it — consumers call that, never a regex of their own."""
    item_id: str
    reason: str
    produced_by: Optional[Any] = None

    def refusal(self) -> "ParsedRefusal":
        return parse_refusal_reason(self.reason)


class ParsedRefusal(BaseModel):
    chunk: Optional[int] = None                     # a bridge window: the first chunk it spans
    bridge: bool = False                            # refused in a cross-chunk bridge window
    axis: Optional[Literal["node", "edge"]] = None   # None: not an off-ontology kind refusal
    kind: Optional[str] = None
    name: Optional[str] = None                      # the refused entity's surface text (node)
    text: str                                       # the reason with the chunk prefix removed


class UnchangedRecord(_Rec):
    """`unchanged.jsonl`: the content hash was already built for this source (incremental)."""
    item_id: str
    uri: str
    sha256: str
    last_run: Optional[str] = None


class CoverageRecord(_Rec):
    """`coverage.jsonl`: partial coverage (a table read up to its row cap) — P7."""
    item_id: str
    uri: str
    rule: str
    reverse: str
    rows_total: Optional[int] = None
    rows_read: Optional[int] = None


class BuildSkippedRecord(_Rec):
    """`build_skipped.jsonl`: an allowed item that produced nothing, and why. A held item
    (`reason: DRIFT_PENDING_APPROVAL`) also names the adaptation and how to release it."""
    item_id: str
    uri: str
    reason: str
    detail: Optional[str] = None
    reverse: Optional[str] = None


# --- report.json ----------------------------------------------------------------------------------
class RunReportRecord(_Rec):
    """`report.json`. `status` is ok | empty | failed | unverified (the core run) — a standalone
    tabular run (`kind: tabular`) writes ok | failed. Only `ok` is a success."""
    source_id: str
    run_id: str
    status: str
    message: str = ""
    counts: dict[str, Any] = Field(default_factory=dict)
    run_dir: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    notes: list[Any] = Field(default_factory=list)
    plan: dict[str, Any] = Field(default_factory=dict)
    verify: dict[str, Any] = Field(default_factory=dict)
    durations_s: dict[str, Any] = Field(default_factory=dict)
    timings: dict[str, Any] = Field(default_factory=dict)
    produced_by: Optional[Any] = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


# --- adapter-agent records (written by adapter agents, read by anyone) ------------------------------
class DriftHolds(BaseModel):
    """What an adapter agent holds for one source while a shape drift awaits approval
    (`<sources>/<id>.holds.yaml`). Read by the runner once the hook is wired (see the AA review
    for the diff): a held TABLE or PATH is inventoried and ledgered `excluded` with rule
    `DRIFT_PENDING_APPROVAL` (so removal inference never closes it); a held STEP is not enumerated
    and the run's enumeration is marked incomplete (so nothing is inferred removed)."""
    model_config = ConfigDict(extra="forbid")
    source_id: str
    tables: dict[str, str] = Field(default_factory=dict)     # table ref -> adaptation id
    paths: dict[str, str] = Field(default_factory=dict)      # glob on rel -> adaptation id
    steps: dict[str, str] = Field(default_factory=dict)      # plan step id -> adaptation id
    updated_at: str = ""
    # There is deliberately NO "resolved" entry here: a hold is only ever LIFTED by the adapter
    # agent deciding it with an authenticated principal (approve_adaptation, or a re-approved plan
    # handed over by `plans.approve_plan(principal=...)`). A line in a config file is a claim a
    # caller can type (R-019), so nothing in this file can release a hold.

    def reverse_text(self, adaptation_id: str) -> str:
        return (f"approve adaptation {adaptation_id} (adapter agent approve_adaptation), or revert "
                f"the source change; rejecting keeps it held")

    @staticmethod
    def is_active(adaptation_id: Optional[str]) -> bool:
        return bool(adaptation_id)

    def step_hold(self, step_id: str) -> Optional[str]:
        """The adaptation holding this plan step, or None."""
        aid = self.steps.get(step_id)
        return aid if self.is_active(aid) else None

    def item_hold(self, kind: str, uri: str, rel: str) -> Optional[str]:
        """The adaptation holding this item, or None: a table by its ref (`schema.table`, which is
        the table item's uri), anything else by a glob on its scope-relative path."""
        if kind == "table":
            aid = self.tables.get(uri)
            return aid if self.is_active(aid) else None
        from .crawl import _glob

        for pattern, aid in sorted(self.paths.items()):
            if self.is_active(aid) and _glob(rel, pattern):
                return aid
        return None

    def absent_hold(self, uri: str, rel: Optional[str] = None) -> Optional[str]:
        """The adaptation holding an item that is NO LONGER LISTED: a held table ref, or a path
        glob. Path globs are written against the scope-RELATIVE path (`item_hold` matches `rel`),
        so the SAME `rel` is used here when the state recorded it (Codex f1 #11). For a state
        entry written before `rel` was recorded, the glob is tried against the uri and every
        trailing run of its path segments — over-matching only keeps an item held (never
        inferred removed), which is the safe direction."""
        aid = self.tables.get(uri)
        if self.is_active(aid):
            return aid
        from .crawl import _glob

        if rel:
            candidates = [rel]
        else:
            path = str(uri or "").split("://", 1)[-1].replace("\\", "/")
            parts = [p for p in path.split("/") if p]
            candidates = [str(uri or "")] + ["/".join(parts[i:]) for i in range(len(parts))]
        for pattern, aid in sorted(self.paths.items()):
            if self.is_active(aid) and any(_glob(c, pattern) for c in candidates):
                return aid
        return None

    @property
    def empty(self) -> bool:
        return not any(self.is_active(a) for a in (*self.tables.values(), *self.paths.values(),
                                                    *self.steps.values()))


def holds_path(source_id: str, root: Optional[Path] = None) -> Path:
    from .connections import connection_path

    return connection_path(source_id, root).with_name(f"{source_id}.holds.yaml")


def load_holds(source_id: str, root: Optional[Path] = None) -> DriftHolds:
    import yaml

    p = holds_path(source_id, root)
    if not p.is_file():
        return DriftHolds(source_id=source_id)
    return DriftHolds.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")) or
                                     {"source_id": source_id})


def save_holds(holds: DriftHolds, root: Optional[Path] = None) -> Path:
    """Write the holds file atomically (validated through `DriftHolds`)."""
    import os

    import yaml

    holds = DriftHolds.model_validate(holds.model_dump(mode="json"))
    p = holds_path(holds.source_id, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(yaml.safe_dump(holds.model_dump(mode="json"), sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, p)
    return p


class AdaptationRecord(_Rec):
    """One adaptation an adapter agent applied or filed (`adaptations.json` in the run dir, and the
    agent's own `_adapt/<agent>/adaptations.jsonl`)."""
    adaptation_id: str
    agent_id: str
    connection: str
    status: Literal["applied", "pending", "approved", "rejected", "reported", "noted",
                    "superseded"]
    drift_ids: list[str]
    drifts: list[dict[str, Any]]
    action: str
    produced_by: dict[str, Any]
    created_at: str


# --- loading --------------------------------------------------------------------------------------
LEDGER_MODELS: dict[str, type[_Rec]] = {
    "inventory": InventoryRecord, "excluded": ExclusionRecord, "blocked": ExclusionRecord,
    "failed": FailedRecord, "captured": CapturedRecord, "extracted": ExtractedRecord,
    "extract_skipped": ExtractSkippedRecord, "governance": GovernanceRecord,
    "governance_chunks": GovernanceChunkRecord, "refused": RefusedRecord,
    "unchanged": UnchangedRecord, "coverage": CoverageRecord, "build_skipped": BuildSkippedRecord,
}


def line(model: type[BaseModel], record: dict[str, Any]) -> dict[str, Any]:
    """Build one artefact line THROUGH its typed model (the writer's side of the contract): the
    record is validated, and what is written is the model's own JSON dump of exactly the fields
    the writer set (`exclude_unset`, extras kept) — so a writer cannot emit a line its readers
    would reject. A bad record raises `ArtefactError` at write time, naming the model."""
    try:
        obj = model.model_validate(record)
    except ValidationError as exc:
        raise ArtefactError(f"{model.__name__}: a writer produced a record that does not match "
                            f"its typed format: {' '.join(str(exc).split())[:300]}") from None
    return obj.model_dump(mode="json", exclude_unset=True)


def model_for_ledger(stem: str) -> Optional[type[BaseModel]]:
    """The typed model of a ledger file stem (`excluded`, `excluded.drift`, ...), or None."""
    return _model_for(stem)


class ArtefactProblem(BaseModel):
    """A line (or file) that failed its typed format. `raw` keeps the parsed JSON object when there
    was one, so a consumer can still COUNT it (P3) — it is never used as evidence."""
    file: str
    line: Optional[int]
    error: str
    ledger: Optional[str] = None                # the ledger stem (`excluded`, `excluded.retry`)
    raw: Optional[dict[str, Any]] = None


class RunArtefacts(BaseModel):
    """Every typed artefact of one run. `ledgers[name]` holds the records of `<name>.jsonl`;
    `tagged[name]` those of tagged variants (`<name>.<tag>.jsonl`), keyed by the full stem."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    run_dir: str
    run_id: str
    report: Optional[RunReportRecord] = None       # None: no report.json (running, or it died)
    ledgers: dict[str, list[Any]] = Field(default_factory=dict)
    tagged: dict[str, list[Any]] = Field(default_factory=dict)
    adaptations: list[AdaptationRecord] = Field(default_factory=list)
    problems: list[ArtefactProblem] = Field(default_factory=list)

    def records(self, ledger: str) -> list[Any]:
        """The ledger's records INCLUDING its tagged variants (P3: found by pattern)."""
        out = list(self.ledgers.get(ledger, []))
        for stem, recs in sorted(self.tagged.items()):
            if stem.split(".", 1)[0] == ledger:
                out.extend(recs)
        return out

    @property
    def complete(self) -> bool:
        return self.report is not None

    def invalid(self, ledger: str) -> list[ArtefactProblem]:
        """Lines of this ledger (and its tagged variants) that failed the typed format."""
        return [p for p in self.problems
                if p.ledger is not None and p.ledger.split(".", 1)[0] == ledger]

    def exclusions_by_rule(self) -> dict[str, dict[str, int]]:
        """ledger -> rule -> n over excluded / blocked / extract_skipped, tagged variants included.
        A line that failed its format is still COUNTED (under its own rule when it names one, else
        `<record failed validation>`) — under-reporting an exclusion is as serious as
        over-collecting (P3); `problems` says which lines they were."""
        out: dict[str, dict[str, int]] = {}
        for ledger in ("excluded", "blocked", "extract_skipped"):
            rules: dict[str, int] = {}
            for r in self.records(ledger):
                rule = getattr(r, "rule", None) or getattr(r, "reason", None) or \
                    "<no rule recorded>"
                rules[str(rule)] = rules.get(str(rule), 0) + 1
            for p in self.invalid(ledger):
                raw = p.raw or {}
                rule = str(raw.get("rule") or raw.get("reason") or "<record failed validation>")
                rules[rule] = rules.get(rule, 0) + 1
            out[ledger] = rules
        return out


def _model_for(stem: str) -> Optional[type[_Rec]]:
    return LEDGER_MODELS.get(stem.split(".", 1)[0])


def read_jsonl_typed(path: Path, model: type[_Rec], *, strict: bool = False,
                     problems: Optional[list[ArtefactProblem]] = None) -> list[Any]:
    out: list[Any] = []
    if not path.is_file():
        return out
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        raw: Any = None
        try:
            raw = json.loads(line)
            out.append(model.model_validate(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            msg = " ".join(str(exc).split())[:300]
            if strict:
                raise ArtefactError(f"{path.name}:{n}: {msg}") from None
            if problems is not None:
                problems.append(ArtefactProblem(file=path.name, line=n, error=msg,
                                                ledger=path.stem,
                                                raw=raw if isinstance(raw, dict) else None))
    return out


def read_report(run_dir: Path, *, strict: bool = False,
                problems: Optional[list[ArtefactProblem]] = None) -> Optional[RunReportRecord]:
    p = Path(run_dir) / "report.json"
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("run_id", Path(run_dir).name)
        return RunReportRecord.model_validate(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        msg = " ".join(str(exc).split())[:300]
        if strict:
            raise ArtefactError(f"report.json: {msg}") from None
        if problems is not None:
            problems.append(ArtefactProblem(file="report.json", line=None, error=msg))
        return None


def read_run(run_dir: Path, *, strict: bool = False) -> RunArtefacts:
    """Load and validate every artefact of one run directory."""
    run_dir = Path(run_dir)
    problems: list[ArtefactProblem] = []
    ledgers: dict[str, list[Any]] = {}
    tagged: dict[str, list[Any]] = {}
    for p in sorted(run_dir.glob("*.jsonl")):
        model = _model_for(p.stem)
        if model is None:
            continue                    # a ledger with no typed format yet (build, timings, …)
        recs = read_jsonl_typed(p, model, strict=strict, problems=problems)
        (ledgers if "." not in p.stem else tagged)[p.stem] = recs
    adaptations: list[AdaptationRecord] = []
    ap = run_dir / "adaptations.json"
    if ap.is_file():
        try:
            data = json.loads(ap.read_text(encoding="utf-8"))
            adaptations = [AdaptationRecord.model_validate(r) for r in data.get("records", [])]
        except (OSError, json.JSONDecodeError, ValidationError, AttributeError) as exc:
            if strict:
                raise ArtefactError(f"adaptations.json: {exc}") from None
            problems.append(ArtefactProblem(file="adaptations.json", line=None,
                                            error=" ".join(str(exc).split())[:300]))
    return RunArtefacts(run_dir=str(run_dir), run_id=run_dir.name,
                        report=read_report(run_dir, strict=strict, problems=problems),
                        ledgers=ledgers, tagged=tagged, adaptations=adaptations,
                        problems=problems)


# --- the refusal sentence, parsed ONCE --------------------------------------------------------------
_CHUNK_PREFIX = _re.compile(r"^(?:chunk (?P<chunk>\d+)|bridge (?P<bridge>\d+)/\d+): ")
_ENTITY_UNKNOWN = _re.compile(r"^entity (?P<q>['\"])(?P<name>.*?)(?P=q): unknown kind "
                              r"(?P<q2>['\"])(?P<kind>[^'\"]+)(?P=q2)")
_RELATION_UNKNOWN = _re.compile(r"^relation (?P<q>['\"])(?P<kind>[^'\"]+)(?P=q): unknown type")


def parse_refusal_reason(reason: str) -> ParsedRefusal:
    """`chunk <n>: entity '<name>': unknown kind '<k>' …` → axis node; `relation '<k>': unknown
    type …` → axis edge; anything else (grounding failures, wrong endpoints) → axis None. A
    `bridge <n>/<n+1>: ` prefix (a cross-chunk window) is parsed the same, with `bridge=True`."""
    text = str(reason or "")
    chunk = None
    bridge = False
    m = _CHUNK_PREFIX.match(text)
    if m:
        bridge = m.group("bridge") is not None
        chunk, text = int(m.group("bridge") if bridge else m.group("chunk")), text[m.end():]
    m = _ENTITY_UNKNOWN.match(text)
    if m:
        return ParsedRefusal(chunk=chunk, bridge=bridge, axis="node", kind=m.group("kind"),
                             name=m.group("name"), text=text)
    m = _RELATION_UNKNOWN.match(text)
    if m:
        return ParsedRefusal(chunk=chunk, bridge=bridge, axis="edge", kind=m.group("kind"),
                             text=text)
    return ParsedRefusal(chunk=chunk, bridge=bridge, text=text)


def list_run_dirs(source_id: str, ingest_root: Optional[Path] = None) -> list[Path]:
    from . import paths

    base = paths.contained(ingest_root or paths.ingest_dir(), source_id, what="source id")
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir())


__all__ = ["DRIFT_PENDING_APPROVAL", "ArtefactError", "InventoryRecord", "ExclusionRecord",
           "FailedRecord", "CapturedRecord", "ExtractedRecord", "ExtractSkippedRecord",
           "GovernanceRecord", "GovernanceChunkRecord", "RefusedRecord", "ParsedRefusal",
           "RunReportRecord", "DriftHolds", "AdaptationRecord", "RunArtefacts", "ArtefactProblem",
           "LEDGER_MODELS", "read_run", "read_report", "read_jsonl_typed", "parse_refusal_reason",
           "holds_path", "load_holds", "save_holds", "list_run_dirs", "line", "model_for_ledger",
           "UnchangedRecord", "CoverageRecord", "BuildSkippedRecord"]
