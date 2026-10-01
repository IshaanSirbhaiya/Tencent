"""`IngestService` — the one seam between adapter agents and `packages/mcp_ingest`.

The manager never imports `databridge_mcp_ingest` directly. It codes against this protocol, and
`McpIngestService` adapts the real module (imported lazily) to it. Tests inject a fake.

Run history is read from the **§5.2 artefacts on disk** (`<ingest>/<source>/<run>/report.json`
plus the governance ledgers beside it), because §5.2 is the frozen contract; the shape of the
in-memory `RunReport` object is not. `normalize_report` accepts a dict, a pydantic model or a
dataclass, and is deliberately tolerant about field names — but it never invents a value: a count
the report does not carry is `None` ("unknown"), never `0` (P17).

**Status is never defaulted to success.** Only an explicit success word (`ok`, `completed`, …) is
`completed`; an explicit failure word is `failed`; everything else — a missing status, `empty`,
`unverified`, `partial`, `running`, an unfamiliar word — is `unverified`, carried with the raw word
in `status_raw`. `mcp_ingest` itself says an `empty` run "is not a success" and an `unverified` run
failed reconciliation; neither may be read as freshness evidence (P7, P8).

**Artefacts are read through `databridge_mcp_ingest.records`** — the shared typed format of what
ingestion writes (`read_run(run_dir)`). Nothing here re-parses a ledger line or a refusal sentence
by hand. A line that fails its typed format is never evidence; it is still COUNTED (exclusions by
its own rule, refusals as ungrounded with the reason) and listed in `artefact_problems`.

**Refusals** come from the run's `refused*.jsonl` ledgers when the run directory has them (the §5.2
artefact is authoritative); a list embedded in the report is read only when there is no ledger, so
one refusal is never counted twice. `mcp_ingest` writes each refusal as
`{"item_id": <sha256 of the full path>, "reason": "chunk <n>: entity '<name>': unknown kind '<k>' …"}`;
the chunk's real span is joined from `governance_chunks.jsonl` and its capture digest from
`captured.jsonl`, so the evidence pointer carries exactly the fields `mcp_ingest.build.span_pointer`
mints — nothing is guessed.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import inspect
import json
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol, runtime_checkable

from . import paths

COUNT_KEYS = ("inventory", "captured", "excluded", "blocked", "failed", "extracted", "built",
              "entities", "relations", "refused", "nodes", "edges")
#: Alternative names a report might use for the same count. Generic across sources — never keyed
#: by a source or a server.
_COUNT_ALIASES: dict[str, tuple[str, ...]] = {
    "inventory": ("inventory", "inventoried", "items", "listed"),
    "captured": ("captured", "captures"),
    "excluded": ("excluded",),
    "blocked": ("blocked",),
    "failed": ("failed",),
    "extracted": ("extracted", "extract_ok"),
    "built": ("built",),
    "entities": ("entities",),
    "relations": ("relations",),
    "refused": ("refused",),
    "nodes": ("nodes", "nodes_written", "graph_nodes"),
    "edges": ("edges", "edges_written", "graph_edges"),
}
#: Explicit terminal-success words. Nothing else is success — not a missing status, not `partial`,
#: not `empty` (mcp_ingest: "EMPTY RUN … Not a success"), not `unverified`.
_OK = frozenset({"ok", "completed", "complete", "success", "succeeded"})
_FAILED = frozenset({"failed", "error", "errored", "aborted"})
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
#: Ledgers whose records are exclusions (P3). Found BY PATTERN, so a tagged variant
#: (`excluded.retry.jsonl`, `excluded.drift.jsonl`) is counted rather than silently missed.
EXCLUSION_LEDGERS = ("excluded", "blocked", "extract_skipped")


def records_module():
    """`databridge_mcp_ingest.records` — the shared typed format (imported, never re-derived)."""
    try:
        from databridge_mcp_ingest import records
    except ImportError as exc:
        raise IngestUnavailable(f"databridge_mcp_ingest.records is not importable: {exc}") \
            from None
    return records


def parse_ts(value: Any) -> Optional[dt.datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        t = value
    else:
        try:
            t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


@dataclasses.dataclass(frozen=True)
class IngestRunReport:
    """One ingest run of one connection, normalised."""

    source_id: str
    run_id: Optional[str]
    status: str                                   # completed | failed | unverified | incomplete
    started: Optional[dt.datetime]
    ended: Optional[dt.datetime]
    counts: dict[str, Optional[int]]
    exclusions_by_rule: dict[str, dict[str, int]]  # ledger -> rule -> n
    refusals: tuple[dict[str, Any], ...]
    run_dir: Optional[str] = None
    error: Optional[str] = None
    status_raw: Optional[str] = None               # the word the report used, verbatim
    produced_by: Optional[Any] = None              # §5.8, when the ingest report records it
    #: lines of the run's artefacts that failed the typed format (`records.ArtefactProblem`)
    artefact_problems: tuple[dict[str, Any], ...] = ()
    #: every item the ingestion SCREEN kept out of the graph (governance.jsonl `block` / `review`),
    #: with the rule(s) that fired and the reversal text — P3: an exclusion is listed, not counted
    #: only (E2E F-14)
    exclusion_reasons: tuple[dict[str, Any], ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    @property
    def when(self) -> Optional[dt.datetime]:
        return self.ended or self.started

    def as_dict(self) -> dict[str, Any]:
        return {"source_id": self.source_id, "run_id": self.run_id, "status": self.status,
                "started": self.started.isoformat() if self.started else None,
                "ended": self.ended.isoformat() if self.ended else None,
                "counts": dict(self.counts), "exclusions_by_rule": self.exclusions_by_rule,
                "refusals": len(self.refusals), "run_dir": self.run_dir, "error": self.error,
                "status_raw": self.status_raw, "produced_by": self.produced_by,
                "artefact_problems": len(self.artefact_problems),
                "exclusion_reasons": [dict(x) for x in self.exclusion_reasons]}


def _as_mapping(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, Mapping):
        return dict(obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    raise TypeError(f"cannot read an ingest report from {type(obj).__name__}")


def _first(d: Mapping[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _count(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (list, tuple, dict)):
        return len(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_refusal(item: Any, *, source_id: str, run_id: Optional[str]) -> Optional[dict[str, Any]]:
    """A refusal as `{axis, kind, document_id, uri, capture_digest, char_start, char_end,
    pointer, text}`, or `None` when it is not an off-ontology KIND refusal (grounding failures,
    wrong endpoints and determinations are not new kinds and are not proposal material)."""
    rec = dict(item) if isinstance(item, Mapping) else {"text": str(item)}
    raw_text = str(rec.get("text") or rec.get("reason") or rec.get("message") or "")
    # mcp_ingest's own sentence parser (`records.parse_refusal_reason`): the chunk prefix, then
    # the entity / relation wording — one parser, shared with the writer's package
    parsed = records_module().parse_refusal_reason(raw_text)
    text = parsed.text
    chunk = rec.get("chunk")
    if chunk is None:
        chunk = parsed.chunk
    axis, kind = rec.get("axis"), rec.get("kind") or rec.get("rel_type")
    if not (axis and kind) and parsed.axis:
        axis, kind = parsed.axis, parsed.kind
        if parsed.name is not None:
            rec.setdefault("name", parsed.name)
    if axis not in ("node", "edge") or not kind:
        return None
    return {"axis": axis, "kind": str(kind), "text": text, "name": rec.get("name"),
            "document_id": rec.get("document_id") or rec.get("item_id"),
            "uri": rec.get("uri"), "capture_digest": rec.get("capture_digest"),
            "char_start": rec.get("char_start"), "char_end": rec.get("char_end"),
            "src_kind": rec.get("src_kind"), "dst_kind": rec.get("dst_kind"),
            "pointer": rec.get("pointer"), "chunk": chunk if isinstance(chunk, int) else None,
            # The connection supplies `source_id` (mcp_ingest's `source_ref` is exactly the
            # connection id). A record that names a DIFFERENT source is kept as
            # `claimed_source_id` and rejected downstream — never re-labelled.
            "claimed_source_id": rec.get("source_id"), "source_id": source_id,
            "produced_by": rec.get("produced_by"),
            "run_id": rec.get("run_id") or run_id}


def _exclusions(arts: Any) -> dict[str, dict[str, int]]:
    """ledger -> rule -> n, from the typed artefacts (tagged variants and invalid lines counted)."""
    return {k: dict(v) for k, v in arts.exclusions_by_rule().items()}


#: governance decision -> the exclusion "ledger" name it is counted under
SCREEN_LEDGERS = {"block": "screen_blocked", "review": "screen_review"}


def _fired_rules(rec: Any) -> list[str]:
    out = []
    for f in getattr(rec, "fired", None) or []:
        rule = f.get("rule") if isinstance(f, dict) else getattr(f, "rule", None)
        if rule:
            out.append(str(rule))
    return sorted(set(out))


def _screen_exclusions(arts: Any) -> tuple[dict[str, dict[str, int]],
                                           tuple[dict[str, Any], ...]]:
    """(ledger -> rule -> n, the listed reasons) for the items the ingestion screen BLOCKED or held
    for REVIEW (`governance.jsonl`). One count per item, under the rule(s) that fired joined by
    `+`, so the counts add up to the items kept out. A line that failed the typed format is counted
    under `<record failed validation>` (never dropped)."""
    counts: dict[str, dict[str, int]] = {}
    reasons: list[dict[str, Any]] = []
    for rec in arts.records("governance"):
        ledger = SCREEN_LEDGERS.get(rec.decision)
        if ledger is None:
            continue
        rules = _fired_rules(rec)
        key = "+".join(rules) or "<no rule recorded>"
        counts.setdefault(ledger, {})[key] = counts.get(ledger, {}).get(key, 0) + 1
        reasons.append({"item_id": rec.item_id, "uri": rec.uri, "decision": rec.decision,
                        "ledger": ledger, "rules": rules, "rule": key,
                        "reason": rec.rationale, "reverse": rec.reverse})
    for prob in arts.invalid("governance"):
        raw = prob.raw or {}
        ledger = SCREEN_LEDGERS.get(str(raw.get("decision")))
        if ledger is None:
            continue
        key = "<record failed validation>"
        counts.setdefault(ledger, {})[key] = counts.get(ledger, {}).get(key, 0) + 1
        reasons.append({"item_id": raw.get("item_id"), "uri": raw.get("uri"),
                        "decision": raw.get("decision"), "ledger": ledger, "rules": [],
                        "rule": key, "reason": prob.error, "reverse": raw.get("reverse")})
    return counts, tuple(reasons)


def _ledger_exclusions(run_dir: Path) -> dict[str, dict[str, int]]:
    return _exclusions(records_module().read_run(run_dir))


def _run_dir_index(arts: Any) -> tuple[dict[str, dict], dict[tuple[str, int], tuple[int, int]]]:
    """(item_id -> {sha256, uri}, (item_id, chunk) -> (char_start, char_end)) from the run's own
    typed ledgers — the facts a refusal line does not repeat."""
    items: dict[str, dict] = {}
    for name in ("captured", "extracted"):
        for rec in arts.records(name):
            if rec.item_id not in items and rec.sha256:
                items[rec.item_id] = {"sha256": rec.sha256, "uri": rec.uri}
    spans: dict[tuple[str, int], tuple[int, int]] = {}
    for rec in arts.records("governance_chunks"):
        if rec.item_id and isinstance(rec.chunk, int) and isinstance(rec.char_start, int) and \
                isinstance(rec.char_end, int):
            spans.setdefault((rec.item_id, rec.chunk), (rec.char_start, rec.char_end))
    return items, spans


def _problem_dicts(arts: Any) -> tuple[dict[str, Any], ...]:
    return tuple({"file": p.file, "line": p.line, "error": p.error} for p in arts.problems)


def _enrich(ref: dict[str, Any], items: dict[str, dict],
            spans: dict[tuple[str, int], tuple[int, int]]) -> dict[str, Any]:
    """Join a refusal with its run's ledgers. Fills only what the refusal left empty, from a
    record keyed by the SAME item id — a join, never an inference."""
    doc = ref.get("document_id")
    if not doc:
        return ref
    item = items.get(doc) or {}
    if ref.get("capture_digest") is None and item.get("sha256"):
        ref["capture_digest"] = item["sha256"]
    if ref.get("uri") is None and item.get("uri"):
        ref["uri"] = item["uri"]
    if ref.get("char_start") is None and ref.get("chunk") is not None:
        span = spans.get((doc, ref["chunk"]))
        if span:
            ref["char_start"], ref["char_end"] = span
            ref["span_basis"] = "chunk"      # the chunk the refused kind was read from
    return ref


def _refusal_key(r: dict[str, Any]) -> tuple:
    return (r.get("document_id"), r.get("chunk"), r.get("char_start"), r.get("char_end"),
            r.get("axis"), r.get("kind"), r.get("name"), r.get("text"))


def normalize_report(raw: Any, *, source_id: str, run_dir: Optional[Path] = None,
                     default_status: Optional[str] = None) -> IngestRunReport:
    d = _as_mapping(raw)
    counts_src = d.get("counts") if isinstance(d.get("counts"), Mapping) else {}
    counts = {k: _count(_first(counts_src, *_COUNT_ALIASES[k]) if counts_src else None) for k in
              COUNT_KEYS}
    for k in COUNT_KEYS:
        if counts[k] is None:
            counts[k] = _count(_first(d, *_COUNT_ALIASES[k]))
    run_id = _first(d, "run_id", "id")
    rdir = run_dir or (Path(d["run_dir"]) if d.get("run_dir") else None)
    raw_word = _first(d, "status", "state") or default_status
    status_raw = str(raw_word).strip().lower() if raw_word is not None else None
    if status_raw in _FAILED:
        status = "failed"
    elif status_raw in _OK:
        status = "completed"
    else:
        status = "unverified"        # missing, empty, unverified, partial, running, unfamiliar

    has_dir = rdir is not None and rdir.is_dir()
    arts = records_module().read_run(rdir) if has_dir else None
    excl = d.get("exclusions_by_rule")
    reported: Optional[dict[str, dict[str, int]]] = None
    if isinstance(excl, Mapping) and excl and all(isinstance(v, Mapping) for v in excl.values()):
        reported = {str(k): {str(r): int(n) for r, n in v.items()} for k, v in excl.items()}
    elif isinstance(excl, Mapping) and excl:
        reported = {"excluded": {str(r): int(n) for r, n in excl.items()}}
    disagreements: list[dict[str, Any]] = []
    if arts is not None:
        # the typed run artefacts are AUTHORITATIVE when the run directory exists (R-019 #10): a
        # ledger written after report.json (the adapter's `excluded.drift.jsonl`) still counts. A
        # rule only the report names is kept; a count the two disagree on is said, ledger used.
        exclusions = _exclusions(arts)
        for ledger, rules in (reported or {}).items():
            if not rules:
                continue
            have = exclusions.setdefault(ledger, {})
            for rule, n in rules.items():
                if rule not in have:
                    have[rule] = n
                elif have[rule] != n:
                    disagreements.append({"file": "report.json", "line": None, "error": (
                        f"exclusions_by_rule[{ledger!r}][{rule!r}] is {n} in the report and "
                        f"{have[rule]} in the ledgers; the ledgers are used")})
    else:
        exclusions = reported or {}
    screen_reasons: tuple[dict[str, Any], ...] = ()
    if arts is not None:
        screen, screen_reasons = _screen_exclusions(arts)
        for ledger, rules in screen.items():
            if ledger not in exclusions or not exclusions[ledger]:
                exclusions[ledger] = rules
    # A ledger count stands in for a report that omitted the number — it is measured, not guessed.
    for ledger in ("excluded", "blocked"):
        if counts[ledger] is None and ledger in exclusions and rdir is not None:
            counts[ledger] = sum(exclusions[ledger].values())

    ledger_files = sorted(rdir.glob("refused*.jsonl")) if has_dir else []
    raw_refusals: list[Any] = []
    invalid_refusals: list[tuple[dict, str]] = []
    if ledger_files and arts is not None:
        # the §5.2 artefact is authoritative, read through its typed format
        raw_refusals.extend(r.model_dump(mode="json") for r in arts.records("refused"))
        invalid_refusals = [(p.raw or {}, p.error) for p in arts.invalid("refused")]
    else:
        embedded = d.get("refused")
        if not isinstance(embedded, (list, tuple)):  # mcp_ingest's `counts.refused` is an int
            embedded = d.get("refusals")
        raw_refusals = list(embedded) if isinstance(embedded, (list, tuple)) else []
    items, spans = _run_dir_index(arts) if arts is not None else ({}, {})
    seen: set[tuple] = set()
    parsed: list[dict[str, Any]] = []
    candidates = [(x, None) for x in raw_refusals] + list(invalid_refusals)
    for x, invalid in candidates:
        r = parse_refusal(x, source_id=source_id, run_id=run_id)
        if r is None:
            continue
        if invalid is not None:
            # a line that failed the typed format is COUNTED (as ungrounded) but never evidence
            r["invalid_record"] = invalid
        r = _enrich(r, items, spans)
        key = _refusal_key(r)
        if key in seen:
            continue
        seen.add(key)
        parsed.append(r)
    refusals = tuple(parsed)
    error = _first(d, "error", "failure")
    if status == "unverified" and not error:
        error = (f"ingest reported status {status_raw!r}, which is not a success"
                 if status_raw else "the ingest report carries no status")
        if d.get("message"):
            error += f": {d['message']}"
    return IngestRunReport(
        source_id=str(d.get("source_id") or source_id), run_id=str(run_id) if run_id else None,
        status=status, started=parse_ts(_first(d, "started", "started_at", "start")),
        ended=parse_ts(_first(d, "ended", "ended_at", "finished_at", "completed_at", "end")),
        counts=counts, exclusions_by_rule=exclusions, refusals=refusals,
        run_dir=str(rdir) if rdir else None, error=error, status_raw=status_raw,
        produced_by=d.get("produced_by"),
        artefact_problems=(_problem_dicts(arts) + tuple(disagreements)) if arts is not None
        else (), exclusion_reasons=screen_reasons)


def read_run_reports(source_id: str, ingest_root: Optional[Path] = None) -> list[IngestRunReport]:
    """Every run of `source_id` from the §5.2 artefacts, oldest first. A run directory with no
    `report.json` is `incomplete` (still running, or it died) — shown, never counted as success."""
    base = (ingest_root or paths.ingest_dir()) / source_id
    if not base.is_dir():
        return []
    out: list[IngestRunReport] = []
    for run_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        rp = run_dir / "report.json"
        if rp.is_file():
            typed = records_module().read_report(run_dir, problems=[])
            if typed is not None:
                raw = typed.model_dump(mode="json")
            else:
                # not the typed format: the status word is still READ (so a failure is not
                # mislabelled), and a missing status stays unverified — never success
                try:
                    raw = json.loads(rp.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                    # unreadable is UNKNOWN — not ok, and not a claim that the run failed
                    raw = {"status": None, "error": f"report.json unreadable: {exc}"}
                if not isinstance(raw, dict):
                    raw = {"status": None, "error": "report.json is not a JSON object"}
            raw.setdefault("run_id", run_dir.name)
            out.append(normalize_report(raw, source_id=source_id, run_dir=run_dir))
        else:
            mtime = dt.datetime.fromtimestamp(run_dir.stat().st_mtime, dt.timezone.utc)
            out.append(IngestRunReport(source_id=source_id, run_id=run_dir.name,
                                       status="incomplete", started=mtime, ended=None,
                                       counts={k: None for k in COUNT_KEYS},
                                       exclusions_by_rule=_ledger_exclusions(run_dir),
                                       refusals=(), run_dir=str(run_dir),
                                       error="no report.json (running, or the run died)"))
    epoch = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    out.sort(key=lambda r: (r.when or epoch, r.run_id or ""))
    return out


@runtime_checkable
class IngestService(Protocol):
    """What the manager needs from ingestion. `packages/mcp_ingest` is adapted to it below."""

    def plan_status(self, source_id: str) -> Optional[str]:
        """`"approved"`, `"proposed"`, or `None` when no crawl plan exists."""

    def run_ingest(self, source_id: str, *, store: Any, harness: Any,
                   progress_cb: Optional[Callable[[dict], None]] = None) -> Any:
        """Run one staged ingest into `store`; return its report (any shape `normalize_report`
        reads). Raises on a run that could not start or crashed."""

    def list_runs(self, source_id: str) -> list[IngestRunReport]:
        """Every run of `source_id`, oldest first."""


class IngestUnavailable(RuntimeError):
    """`databridge_mcp_ingest` is not importable, or lacks the entry point this adapter needs."""


class McpIngestService:
    """Adapter from the real `databridge_mcp_ingest` module to `IngestService`.

    Expected upstream API (BUILD_PLAN §6 B): `plans.load_plan(source_id) -> CrawlPlan(status=…)`,
    `run_ingest(source_id, store, harness, progress_cb) -> RunReport`, artefacts per §5.2.
    """

    def __init__(self, *, sources_root: Optional[Path] = None,
                 ingest_root: Optional[Path] = None) -> None:
        self.sources_root = sources_root
        self.ingest_root = ingest_root

    @staticmethod
    def _module(name: str):
        import importlib

        try:
            return importlib.import_module(name)
        except ImportError as exc:
            raise IngestUnavailable(f"{name} is not importable: {exc}") from None

    def plan_status(self, source_id: str) -> Optional[str]:
        plans = self._module("databridge_mcp_ingest.plans")
        plan_path = getattr(plans, "plan_path", None)
        if callable(plan_path) and not plan_path(source_id, self.sources_root).is_file():
            return None
        # A malformed plan RAISES: an unreadable plan is not "no plan", and it is not approved.
        plan = plans.load_plan(source_id, self.sources_root)
        return str(getattr(plan, "status", None) or "") or None

    def _run_fn(self) -> Callable[..., Any]:
        for mod in ("databridge_mcp_ingest", "databridge_mcp_ingest.run",
                    "databridge_mcp_ingest.pipeline", "databridge_mcp_ingest.runner"):
            try:
                fn = getattr(self._module(mod), "run_ingest", None)
            except IngestUnavailable:
                continue
            if callable(fn):
                return fn
        raise IngestUnavailable("databridge_mcp_ingest exposes no run_ingest yet")

    #: per-run options an adapter agent's spec sets (`extract.passages` -> `passages`), passed on
    #: when `run_ingest` takes them (E2E F-11)
    RUN_OPTIONS = ("passages",)

    def run_ingest(self, source_id: str, *, store: Any, harness: Any,
                   progress_cb: Optional[Callable[[dict], None]] = None,
                   **options: Any) -> Any:
        fn = self._run_fn()
        params = inspect.signature(fn).parameters
        wanted = {"store": store, "harness": harness, "progress_cb": progress_cb}
        kwargs = {k: v for k, v in wanted.items() if k in params}
        missing = sorted(set(wanted) - set(kwargs) - {"progress_cb"})
        var_kw = any(p.kind is p.VAR_KEYWORD for p in params.values())
        if missing and not var_kw:
            raise IngestUnavailable(f"run_ingest{inspect.signature(fn)} takes none of {missing}")
        if var_kw:
            kwargs = wanted
        unknown = sorted(k for k in options if k not in self.RUN_OPTIONS)
        if unknown:
            raise TypeError(f"unknown run option(s) {unknown}")
        for k, v in options.items():
            if v is not None and (k in params or var_kw):
                kwargs[k] = v
        return fn(source_id, **kwargs)

    def list_runs(self, source_id: str) -> list[IngestRunReport]:
        return read_run_reports(source_id, self.ingest_root)

    # --- the adaptation seam (optional: a service without these simply does not adapt) ----------
    #: artefact name -> (module, load fn, save fn, model, path fn)
    _CONFIG = {"connection": ("connections", "load_connection", "save_connection",
                              "SourceConnection", "connection_path"),
               "plan": ("plans", "load_plan", "save_plan", "CrawlPlan", "plan_path"),
               "mapping": ("mapping", "load_mapping", "save_mapping", "SourceMapping",
                           "mapping_path")}

    def probe_shape(self, source_id: str):
        """The source's live shape (`drift.probe_source`), read-only, over its own session."""
        return self._module("databridge_mcp_ingest.drift").probe_source(source_id,
                                                                        root=self.sources_root)

    def run_shape(self, source_id: str, run_dir: Path):
        """File types / mimes one run saw (`drift.shape_from_run`, from the typed artefacts)."""
        return self._module("databridge_mcp_ingest.drift").shape_from_run(Path(run_dir),
                                                                          source_id)

    def config_path(self, source_id: str, artifact: str) -> Path:
        mod, _, _, _, path_fn = self._CONFIG[artifact]
        return getattr(self._module(f"databridge_mcp_ingest.{mod}"), path_fn)(source_id,
                                                                             self.sources_root)

    def read_config(self, source_id: str, artifact: str) -> Optional[dict[str, Any]]:
        """The declaration as it stands (`None` when there is none). A mapping file that does not
        exist yet reads as `None`, not as an empty mapping."""
        if not self.config_path(source_id, artifact).is_file():
            return None
        mod, load_fn, _, _, _ = self._CONFIG[artifact]
        obj = getattr(self._module(f"databridge_mcp_ingest.{mod}"), load_fn)(source_id,
                                                                            self.sources_root)
        return obj.model_dump(mode="json")

    def write_config(self, source_id: str, artifact: str, data: dict[str, Any]) -> str:
        """Validate `data` against mcp_ingest's own model and save it through its own writer."""
        mod_name, _, save_fn, model, _ = self._CONFIG[artifact]
        mod = self._module(f"databridge_mcp_ingest.{mod_name}")
        obj = getattr(mod, model).model_validate(data)
        return str(getattr(mod, save_fn)(obj, self.sources_root))

    # --- approvals: ONLY through mcp_ingest's own approval API (INT3) ---------------------------
    def approve_plan(self, source_id: str, *, principal: Any, client: Any = None) -> Any:
        """`plans.approve_plan` for an AUTHENTICATED principal object (a name is refused there)."""
        plans = self._module("databridge_mcp_ingest.plans")
        return plans.approve_plan(source_id, principal=principal, root=self.sources_root,
                                  client=client)

    def approve_mapping(self, source_id: str, *, principal: Any,
                        tables: Optional[list[str]] = None) -> Any:
        """`mapping.approve_mapping` for an AUTHENTICATED principal object."""
        mapping = self._module("databridge_mcp_ingest.mapping")
        return mapping.approve_mapping(source_id, principal=principal, tables=tables,
                                       vocab=self.vocabulary(), root=self.sources_root)

    def approve_delegated_mapping(self, source_id: str, table: str, entry: dict[str, Any], *,
                                  delegation: Any) -> Any:
        """`mapping.approve_delegated_additive`: writes and approves one table's entry under an
        adapter agent's delegated `auto_additive` approval — refused unless additive."""
        mapping = self._module("databridge_mcp_ingest.mapping")
        return mapping.approve_delegated_additive(source_id, table, entry, delegation=delegation,
                                                  vocab=self.vocabulary(),
                                                  root=self.sources_root)

    def plan_tools(self, source_id: str) -> dict[str, list[str]]:
        """step id -> the tools that step calls, from the APPROVED plan (`StepSpec.tools()`), plus
        the listing fields its read templates use (`step id#fields`)."""
        plans = self._module("databridge_mcp_ingest.plans")
        plan = plans.load_plan(source_id, self.sources_root)
        out: dict[str, list[str]] = {}
        for s in plan.steps:
            out[s.id] = list(s.tools())
            fields: set[str] = set()
            if s.read is not None:
                fields |= plans.template_fields(s.read.text_args)
                fields |= plans.template_fields(s.read.binary_args)
            if s.list is not None:
                fields |= plans.template_fields(s.list.args)
            out[f"{s.id}#fields"] = sorted(fields)
        return out

    def propose_table_mappings(self, source_id: str, refs: list[str], *, vocab: Any,
                               harness: Any = None) -> dict[str, dict[str, Any]]:
        """Proposed mapping entries for new tables: measured profile → `mapping.propose_rules`,
        then `mapping.propose_model` for what no rule mapped (constrained to the vocabulary)."""
        drift = self._module("databridge_mcp_ingest.drift")
        mapping = self._module("databridge_mcp_ingest.mapping")
        snaps = drift.profile_tables(source_id, list(refs), root=self.sources_root)
        current = mapping.load_mapping(source_id, self.sources_root)
        proposed = mapping.propose_rules(snaps, vocab)
        unmapped = [t for t, tm in proposed.items() if tm.status == "unmapped"
                    and not (snaps[t].get("history_snapshot") or {}).get("is")]
        proposed.update(mapping.propose_model(snaps, unmapped, {**current.tables, **proposed},
                                              vocab, harness))
        return {t: tm.model_dump(mode="json") for t, tm in proposed.items()}

    def propose_column_mapping(self, source_id: str, table: dict[str, Any], *, vocab: Any
                               ) -> dict[str, Any]:
        """What the RULES would map for one table snapshot (`mapping.propose_rules`) — used to
        take the edge a new foreign-key column would get, never to replace a human decision."""
        mapping = self._module("databridge_mcp_ingest.mapping")
        ref = table.get("ref") or ""
        out = mapping.propose_rules({ref: table}, vocab)
        return out[ref].model_dump(mode="json") if ref in out else {}

    def propose_plan_version(self, source_id: str) -> dict[str, Any]:
        drift = self._module("databridge_mcp_ingest.drift")
        return drift.propose_plan_version(source_id, root=self.sources_root).model_dump(
            mode="json")

    def vocabulary(self) -> Any:
        """The deployment's active vocabulary (closed kinds, subjects and their identifiers)."""
        from databridge_graph.packs import active_vocabulary

        return active_vocabulary()

    def extractor_exists(self, ext: str) -> bool:
        return bool(self._module("databridge_mcp_ingest.drift").extractor_exists(ext))

    def item_identity(self, source_id: str, uri: str) -> str:
        return self._module("databridge_mcp_ingest.crawl").item_identity(source_id, uri)

    def holds_honoured(self) -> bool:
        """True once the runner reads the holds file itself (P's hook, `HONOURS_DRIFT_HOLDS`)."""
        return bool(getattr(self._module("databridge_mcp_ingest.runner"),
                            "HONOURS_DRIFT_HOLDS", False))

    def write_holds(self, source_id: str, holds: dict[str, Any]) -> str:
        import yaml

        rec = self._module("databridge_mcp_ingest.records")
        obj = rec.DriftHolds.model_validate({"source_id": source_id, **holds})
        p = rec.holds_path(source_id, self.sources_root)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(yaml.safe_dump(obj.model_dump(mode="json"), sort_keys=True),
                       encoding="utf-8")
        import os

        os.replace(tmp, p)
        return str(p)

    def captured_table(self, sha256: str) -> Optional[dict[str, Any]]:
        """A captured table snapshot (rows as captured), for grounding a table_cell pointer."""
        cap = self._module("databridge_mcp_ingest.capture")
        try:
            return json.loads(cap.get_bytes(sha256))
        except Exception:                                             # noqa: BLE001
            return None


__all__ = ["IngestService", "McpIngestService", "IngestRunReport", "IngestUnavailable",
           "normalize_report", "read_run_reports", "parse_refusal", "parse_ts", "COUNT_KEYS",
           "EXCLUSION_LEDGERS", "is_hashed_path_id"]


def is_hashed_path_id(value: Any) -> bool:
    """P4 / §5.3: a document id is the sha256 (hex) of the source-qualified full path."""
    return isinstance(value, str) and bool(_HEX64.match(value))
