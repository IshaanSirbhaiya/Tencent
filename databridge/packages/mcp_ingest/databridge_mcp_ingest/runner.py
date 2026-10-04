"""The staged run: collect → extract → screen → build → verify → report (P10).

Every stage writes its artefacts under `WALNUT_INGEST_DIR/<source>/<run_id>/`, created at the
start of the run so they exist EVEN WHEN EMPTY (P3):

    plan.snapshot.yaml   the approved plan this run executed
    inventory.jsonl      every item seen (item_id = sha256 of the source-qualified full path)
    excluded.jsonl       out of declared scope — rule + how to reverse
    blocked.jsonl        blocked patterns — rule + how to reverse
    failed.jsonl         could not be listed / fetched — error in plain words
    captured.jsonl       bytes captured (sha256, size, mime)
    unchanged.jsonl      content hash already ingested for this source — skipped (incremental)
    extracted.jsonl      text extracted (chars, extractor)
    extract_skipped.jsonl  no extractor / extractor unavailable / no text / failed
    governance.jsonl     ONE screen decision per extracted item (allow | review | block)
    governance_chunks.jsonl  the per-chunk screen (P1: the gate runs twice)
    coverage.jsonl       partial coverage (a table read up to its row cap) — P7
    mapping.snapshot.yaml  the table mapping this run used (tabular sources)
    build.jsonl          per item: chunks, entities, relations, anchor, model calls
    build_skipped.jsonl  allowed items that produced nothing, with the reason
    refused.jsonl        extraction refusals (off-ontology, ungrounded) — never silently dropped
    pointers.jsonl       every pointer minted this run, for verification
    verify.json          reconciliation + pointer resolution
    removed.jsonl        items gone from the source: the graph nodes/edges whose ONLY source was
                         that item, CLOSED on the system axis (bi-temporal — never deleted)
    timings.jsonl        per item: stage, milliseconds, and what the time went on
    index.json           the retrieval hook: passages indexed, or why not
    report.json          measured counts and durations; status ok | empty | failed | unverified;
                         `timings` = per-stage breakdown (MCP connect/list/read, capture, docling,
                         screen, model calls/tokens/latency, graph write, retrieval, verify)
    stages.json          per stage: seconds, and a `breakdown` of the same buckets
    checkpoint.json      progress, every 20 items or 120 s (whichever first)

Speed (BUILD_PLAN §11a P, measured in `bench/`): model calls run on a bounded worker pool
(`concurrency`, default 4, env `WALNUT_EXTRACT_CONCURRENCY`) behind an on-disk model-output cache
keyed by content (`llm.ModelOutputCache`); docling conversions are cached by content hash
(`extract.extract_text_cached`); graph writes happen on ONE thread, in order, one transaction per
document (`build.store_batch`) — so the graph is identical to a serial run's.

Per-source state (`<source>/state.json`) holds the content hash last BUILT for each item, which
is what incremental runs key on (P10's sharp edge: key on content, never on id).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from . import capture, paths
from .build import (ProseBuilder, anchor_id_for, assert_may_build, build_tables,
                    capture_only as _capture_only_guard, consolidate_identity_after_run,
                    document_kind, store_batch,
                    unbuilt_completeness)
from .client import McpClientError, ReadOnlyClient, ToolChanged, open_client
from .connections import SourceConnection, load_connection
from .crawl import (CrawlError, Decision, Item, decide, enumerate_documents, enumerate_resources,
                    enumerate_tables, fetch_document, fetch_table, item_identity, table_text)
from .extract import ExtractionFailed, extract_text_cached, extractor_for, text_digest
from .ledger import (emit_event, item_event_id, now_iso, public_text, read_jsonl, scrub,
                     typed_ledger)
from .plans import CrawlPlan, PlanError, load_plan
from .records import DRIFT_PENDING_APPROVAL, DriftHolds, RunReportRecord, load_holds
from .screen import (ScreenResult, apply_masks, load_overrides, load_screen, screen_policy,
                     screen_text)

#: The runner reads `<sources>/<id>.holds.yaml` (written by adapter agents) and holds exactly the
#: table, path or plan step a pending shape drift affects — the rest of the source keeps syncing.
#: A held table / path is CAPTURED (inventoried, so removal inference never closes it, and the
#: drift probe still sees it) but NOT BUILT; a held step is not enumerated and the run's listing
#: is marked incomplete. Both are recorded with rule `DRIFT_PENDING_APPROVAL` and the reversal
#: (`excluded.drift.jsonl`, `build_skipped.jsonl`, `failed.jsonl`). `adapter_agents` reads this
#: flag to move from its coarser fallbacks (mapping status, whole connection) to precise holds.
HONOURS_DRIFT_HOLDS = True
#: The run-directory ledger of items captured but held (typed as `records.ExclusionRecord`, found
#: by pattern as a tagged variant of `excluded`, so `verify_run`'s partition is unaffected).
HELD_LEDGER = "excluded.drift"
#: INT5 (R-022 sources cross-track 5): a plan step whose tool CHANGED on the server since the
#: plan was approved (or is no longer offered) is refused by `ReadOnlyClient` (`ToolChanged`).
#: That is reported as a HOLD awaiting a re-proposed, re-approved plan — the way a shape drift is
#: — not as a failure of the source: the step/item goes to `failed.jsonl` with this rule and the
#: reversal, the listing is marked incomplete (nothing is inferred removed), and the report says
#: so in its message, counts (`tool_changed`) and notes.
TOOL_CHANGED = "TOOL_CHANGED_PENDING_REPLAN"
_TOOL_CHANGED_REVERSE = ("re-propose the crawl plan against the server's current tools and have "
                         "a person approve it (propose_plan, then approve_plan)")

#: The rule an item whose locator carries a credential is blocked under (Codex f2 #4).
CREDENTIAL_IN_LOCATOR = "credential_in_locator"

CHECKPOINT_ITEMS = 20
CHECKPOINT_SECONDS = 120.0

LEDGERS = ("inventory", "excluded", "blocked", "failed", "captured", "unchanged", "extracted",
           "extract_skipped", "governance", "governance_chunks", "coverage", "build",
           "build_skipped", "refused", "pointers", "removed", "timings")
STAGES = ("collect", "extract", "screen", "build", "index", "verify", "report")
DEFAULT_CONCURRENCY = 4


class Timings:
    """Named time buckets (ms + count), thread-safe. Bucket names are `<stage>.<what>`; a stage's
    buckets are copied into its `stages.json` entry as `breakdown`, and all of them into
    `report.json["timings"]`. Per-item rows go to `timings.jsonl`."""

    def __init__(self, ledger=None) -> None:
        self._lock = threading.Lock()
        self.buckets: dict[str, dict] = {}
        self.ledger = ledger
        self.items: list[tuple[float, str, str]] = []

    def add(self, bucket: str, seconds: float, n: int = 1) -> None:
        with self._lock:
            b = self.buckets.setdefault(bucket, {"ms": 0.0, "n": 0})
            b["ms"] += max(0.0, seconds) * 1000.0
            b["n"] += n

    def item(self, item_id: Optional[str], stage: str, seconds: float, **detail: Any) -> None:
        ms = round(max(0.0, seconds) * 1000.0, 2)
        with self._lock:
            self.items.append((ms, stage, str(item_id)))
        if self.ledger is not None:
            self.ledger.write({"item_id": item_id, "stage": stage, "ms": ms, **detail})

    def stage(self, stage: str) -> dict:
        with self._lock:
            return {k.split(".", 1)[1]: {"ms": round(v["ms"], 1), "n": v["n"]}
                    for k, v in sorted(self.buckets.items()) if k.startswith(stage + ".")}

    def as_dict(self, model: Optional[dict] = None) -> dict:
        with self._lock:
            slow = sorted(self.items, reverse=True)[:5]
            return {"buckets": {k: {"ms": round(v["ms"], 1), "n": v["n"]}
                                for k, v in sorted(self.buckets.items())},
                    "model": model or {},
                    "slowest_items": [{"ms": ms, "stage": st, "item_id": iid}
                                      for ms, st, iid in slow]}


class _LazyLedger:
    """A typed ledger whose file is created on its first record (a TAGGED variant — e.g.
    `excluded.drift.jsonl` — exists only when the run has something to say in it)."""

    def __init__(self, path: Path, *, truncate: bool) -> None:
        self.path = path
        self._truncate = truncate           # a fresh run: the first write starts the file
        self._inner = None

    def write(self, record: dict) -> None:
        if self._inner is None:
            self._inner = typed_ledger(self.path, truncate=self._truncate)
        self._inner.write(record)

    def close(self) -> None:
        if self._inner is not None:
            self._inner.close()


def _timed_iter(it, timings: Timings, bucket: str):
    """Charge the time spent INSIDE an iterator's `next()` (for a lazy listing: the list calls)."""
    it = iter(it)
    while True:
        t0 = time.perf_counter()
        try:
            value = next(it)
        except StopIteration:
            timings.add(bucket, time.perf_counter() - t0, n=0)
            return
        timings.add(bucket, time.perf_counter() - t0)
        yield value


def _configure_local_store(store, report) -> None:
    """WAL + NORMAL sync for the LOCAL, file-backed store while ingest writes to it (principles
    trap 7: never WAL on a shared/network file — a published copy is a separate DELETE-mode file,
    not this one). `WALNUT_INGEST_SQLITE_WAL=0` turns it off. One writer: every graph write in a
    run happens on the run's own thread, under the store's lock."""
    conn = getattr(store, "conn", None)
    path = str(getattr(store, "path", "") or "")
    if conn is None or not path or path == ":memory:":
        return
    if os.environ.get("WALNUT_INGEST_SQLITE_WAL", "1") == "0":
        report.notes.append("sqlite: WAL disabled by WALNUT_INGEST_SQLITE_WAL=0")
        return
    if path.startswith(("\\\\", "//")):
        report.notes.append("sqlite: store is on a network share — journal mode left as is (P11)")
        return
    lock = getattr(store, "_lock", None) or threading.RLock()
    try:
        with lock:
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA cache_size=-32000")
            conn.execute("PRAGMA busy_timeout=10000")
        report.notes.append(f"sqlite: journal_mode={mode}, synchronous=NORMAL, busy_timeout=10s")
    except Exception as exc:                                         # noqa: BLE001
        report.notes.append(f"sqlite: pragmas not applied ({_plain_error(exc)})")


def _pool_snapshot() -> Optional[dict]:
    """`client.get_pool().snapshot()` when the client offers a session pool, else None."""
    try:
        from .client import get_pool

        snap = get_pool().snapshot()
        return dict(snap) if isinstance(snap, dict) else None
    except Exception:                                                # noqa: BLE001
        return None


def _resolve_concurrency(value: Optional[int]) -> int:
    if value is None:
        try:
            value = int(os.environ.get("WALNUT_EXTRACT_CONCURRENCY", DEFAULT_CONCURRENCY))
        except ValueError:
            value = DEFAULT_CONCURRENCY
    return max(1, int(value))


def _passages_enabled(value: Optional[bool]) -> bool:
    if value is not None:
        return bool(value)
    return os.environ.get("WALNUT_INGEST_PASSAGES", "0") == "1"


class PlanNotApproved(PlanError):
    pass


class NotAnIngestConnection(PlanError):
    """A write-capable (`purpose: agent_write`) connection is never crawled."""


class RunInProgress(PlanError):
    """Another run of the same source holds its run lock (Codex f1 #6: one writer per source)."""


class ResumeRefused(PlanError):
    """A resume was asked for under a different plan / screen / overrides / holds than the run
    started with (Codex f1 #2). Start a new run; the interrupted one stays on disk as it was."""


def _require_plan_approval(plan, sources_root: Optional[Path]) -> Optional[str]:
    """Codex f1 #1: `status: approved` / `approved_by` are fields anyone can type into the YAML.
    The plan's approval is verified by `plans.verify_plan_approval(plan, root=...)` — the
    authenticated principal's stored approval, bound to the digest of the plan as it is NOW —
    which raises (`ApprovalRefused` / `PlanError`) when it is missing, not a principal's, or for
    different content. Any refusal becomes `PlanNotApproved`, before anything runs.

    Codex f2 #1: FAIL CLOSED. A build without the verifier refuses to run — a note is not an
    authorisation control. (The return value stays for callers; it is always None now.)"""
    from . import plans as _plans

    verify = getattr(_plans, "verify_plan_approval", None)
    if not callable(verify):
        raise PlanNotApproved(f"the plan for {plan.source_id!r} cannot be verified: "
                              f"plans.verify_plan_approval is not available in this build, and "
                              f"status/approved_by are typed fields, not proof. Nothing runs")
    try:
        verify(plan, root=sources_root)
    except PlanError as exc:
        raise PlanNotApproved(f"the plan for {plan.source_id!r} is not approved by an "
                              f"authenticated principal for its current content: {exc}") from None
    return None


#: source key -> [owning thread ident, depth]. Re-entrant for the OWNING thread only, so the
#: tabular recipe can hold the lock around its whole sync and call the core run inside it
#: (Codex f2 #9); any other thread or process is refused.
_LOCAL_RUNS: dict[str, list] = {}
_LOCAL_RUNS_GUARD = threading.Lock()


class _source_lock:
    """One run per source at a time — across threads (an in-process set) and processes (an OS
    byte-range lock on `<source>/.run.lock`, released by the OS if the process dies, so a crash
    never leaves a stale lock). A second run raises `RunInProgress` instead of racing the first
    on `state.json`, `decided.json` and the run's ledgers."""

    def __init__(self, source_id: str, *, reentrant: bool = False) -> None:
        self.reentrant = reentrant
        self.dir = source_dir(source_id)
        self.source_id = source_id
        self._fh = None
        self._key = ""
        self._nested = False

    def __enter__(self) -> "_source_lock":
        self.dir.mkdir(parents=True, exist_ok=True)
        self._key = os.path.normcase(str(self.dir.resolve()))
        me = threading.get_ident()
        with _LOCAL_RUNS_GUARD:
            held = _LOCAL_RUNS.get(self._key)
            if held is not None and held[0] == me and self.reentrant:
                held[1] += 1                    # the same thread, one level deeper
                self._nested = True
                return self
            if held is not None:
                raise RunInProgress(f"a run of {self.source_id!r} is already in progress in this "
                                    f"process; wait for it to finish")
            _LOCAL_RUNS[self._key] = [me, 1]
        try:
            self._fh = open(self.dir / ".run.lock", "a+b")
            try:
                if os.name == "nt":
                    import msvcrt

                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RunInProgress(f"a run of {self.source_id!r} is already in progress in "
                                    f"another process; wait for it to finish") from None
        except BaseException:
            self._release()
            raise
        return self

    def _release(self) -> None:
        if self._nested:
            with _LOCAL_RUNS_GUARD:
                held = _LOCAL_RUNS.get(self._key)
                if held is not None:
                    held[1] -= 1
            self._nested = False
            return
        if self._fh is not None:
            try:
                if os.name == "nt":
                    import msvcrt

                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            self._fh.close()
            self._fh = None
        with _LOCAL_RUNS_GUARD:
            _LOCAL_RUNS.pop(self._key, None)

    def __exit__(self, *exc) -> None:
        self._release()


#: the public name of the per-source run lock (one run per source): a caller that must not race
#: a run's mapping proposals (the setup's explanation-bound mapping approval) holds it
source_run_lock = _source_lock


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _override_allows(overrides, item_id: str, digest: Optional[str]) -> bool:
    """Does an owner override cover THIS item AT THIS content digest? (R-022 build cross-track 2)
    Only an `OverrideSet` (from `load_overrides`) can say; a bare set of ids binds no content, so
    it never admits anything (fail closed)."""
    allows = getattr(overrides, "allows", None)
    return bool(callable(allows) and allows(item_id, digest))


def _overrides_fingerprint(overrides) -> list:
    """What an override approval covers: (item, its approved digests). A changed approval (a new
    digest, a revocation) changes the run binding, so `decided.json` is never reused across it."""
    digests = getattr(overrides, "digests", None)
    if isinstance(digests, dict):
        return sorted((str(k), sorted(str(d) for d in v)) for k, v in digests.items())
    return sorted(str(o) for o in (overrides or ()))


def _run_binding(plan, screen_cfg, overrides, holds: DriftHolds) -> dict:
    """What a run is bound to: digests of the plan, the screen rules, the owner overrides and the
    drift holds in force when it started (`updated_at` of the holds is not content)."""
    return {"plan": _digest(plan.model_dump(mode="json")),
            "screen": screen_fingerprint(screen_cfg),
            "overrides": _digest(_overrides_fingerprint(overrides)),
            "holds": _digest(holds.model_dump(mode="json", exclude={"updated_at"}))}


def _bind_run(run_dir: Path, binding: dict, *, resuming: bool, stages_done: dict) -> None:
    """Write `binding.json` on a run's first start; on a resume, refuse any difference."""
    path = run_dir / "binding.json"
    if resuming and path.is_file():
        try:
            before = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            before = {}
        changed = sorted(k for k in binding if before.get(k) != binding[k])
        if before.get("mode") != binding.get("mode") and "mode" not in changed:
            # FEAT8 (R-027 AAX f1 #1): a capture-only discovery is never resumed as a build, and
            # a build is never resumed as a discovery
            changed.append("mode")
        if changed:
            raise ResumeRefused(
                f"run {run_dir.name} cannot resume: its {', '.join(changed)} changed since it "
                f"started. Start a new run (the interrupted one is kept as it is)")
        return
    inv = run_dir / "inventory.jsonl"
    if resuming and (stages_done or (inv.is_file() and inv.stat().st_size > 0)):
        # a run from before binding existed, part-way through: nothing proves what it ran under
        raise ResumeRefused(f"run {run_dir.name} predates run binding and cannot be resumed "
                            f"safely; start a new run")
    _atomic_text(path, json.dumps({**binding, "at": now_iso(),
                                   # the source-state generation this run started on (f2 #8)
                                   "state_gen": load_state_gen(run_dir.parent.name).get("gen")},
                                  sort_keys=True, indent=1))


def _atomic_text(path: Path, text: str) -> None:
    """A control file (stages, checkpoint, binding, state) written whole or not at all: a unique
    temp file, fsynced, then `os.replace` (Codex f2 #14 — a crash never leaves torn JSON)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:12]}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(text.encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _mapping_fingerprint(mapping) -> str:
    """What a table build is bound to: every APPROVED table of the effective mapping, with the
    digest of its node/edges and the approval (principal, time) that made it count."""
    from .mapping import mapping_digest

    return _digest(sorted(
        (t, mapping_digest(tm), (tm.approval or {}).get("principal_id"),
         (tm.approval or {}).get("at"))
        for t, tm in mapping.tables.items() if tm.status == "approved"))


MAPPING_BINDING = "mapping.binding.json"


def _bind_mapping(run_dir: Path, mapping) -> None:
    """Codex f2 #3: record the approved mapping on the run's FIRST table build; on any later
    attempt of the same run, refuse a different one (`ResumeRefused`)."""
    path = run_dir / MAPPING_BINDING
    now = _mapping_fingerprint(mapping)
    if path.is_file():
        try:
            before = json.loads(path.read_text(encoding="utf-8")).get("mapping")
        except ValueError:
            before = None
        if before != now:
            raise ResumeRefused(f"run {run_dir.name} cannot resume: its approved table mapping "
                                f"changed since it first built tables. Start a new run (the "
                                f"interrupted one is kept as it is)")
        return
    _atomic_text(path, json.dumps({"mapping": now, "at": now_iso()}, sort_keys=True))


def _check_mapping_binding(run_dir: Path, source_id: str, vocab, sources_root) -> None:
    """On a resume, BEFORE any ledger is touched: a run that already built tables under one
    approved mapping is refused if the approved mapping now differs."""
    if not (run_dir / MAPPING_BINDING).is_file():
        return
    from .mapping import effective_mapping, load_mapping

    _bind_mapping(run_dir, effective_mapping(load_mapping(source_id, sources_root), vocab,
                                             sources_root))


# --- source-state generations (Codex f2 #8) ------------------------------------------------------
def state_gen_path(source_id: str) -> Path:
    return source_dir(source_id) / "state.gen.json"


def load_state_gen(source_id: str) -> dict:
    p = state_gen_path(source_id)
    try:
        raw = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except (OSError, ValueError):
        raw = {}
    return raw if isinstance(raw, dict) else {}


def _check_state_generation(source_id: str, run_id: str, binding_file: Path) -> None:
    """A resumed run may advance `state.json` only if nobody else has since: the current state
    generation must be the one the run STARTED on, or one this run wrote itself. Otherwise a
    newer run's state would be overwritten with this run's older hashes (Codex f2 #8)."""
    try:
        started_on = json.loads(binding_file.read_text(encoding="utf-8")).get("state_gen")
    except (OSError, ValueError):
        started_on = None
    gen = load_state_gen(source_id)
    if gen.get("gen") == started_on or gen.get("run_id") == run_id:
        return
    raise ResumeRefused(f"run {run_id} cannot resume: run {gen.get('run_id')!r} advanced this "
                        f"source's state after it started, and resuming would roll that back. "
                        f"Start a new run (the interrupted one is kept as it is)")


def _retire_unfinished_ledgers(run_dir: Path, names: list[str]) -> None:
    """Move the ledgers of stages that will re-run to `attempts/<n>/` (never truncate — Codex f1
    #3). `governance.jsonl` also holds the decisions the EXTRACT stage carried forward (F-31);
    when extraction is not re-running those lines are copied back, so they are not lost."""
    present = [n for n in names if (run_dir / f"{n}.jsonl").is_file()
               and (run_dir / f"{n}.jsonl").stat().st_size > 0]
    if not present:
        return
    base = run_dir / "attempts"
    n = 1
    while (base / str(n)).exists():
        n += 1
    dest = base / str(n)
    dest.mkdir(parents=True)
    extract_rerun = "extracted" in names
    for name in present:
        src = run_dir / f"{name}.jsonl"
        keep = ([r for r in read_jsonl(src) if r.get("carried_forward")]
                if name == "governance" and not extract_rerun else [])
        os.replace(src, dest / src.name)
        if keep:
            with typed_ledger(src) as led:
                for r in keep:
                    led.write(r)


#: Set while `tabular.ingest_database` runs a sync on this thread, so the core run it calls back
#: into keeps the options of the sync that started it (and hands its report back).
_TABULAR = threading.local()


def _inside_tabular_recipe() -> bool:
    """True when `tabular.ingest_database` is already on this call stack — it calls run_ingest for
    its graph stage and must not be started again from inside itself. (Belt and braces: the
    recipe can also pass `tabular=False`.)"""
    import sys

    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code.co_name == "ingest_database" and \
                frame.f_globals.get("__name__", "").endswith("tabular.pipeline"):
            return True
        frame = frame.f_back
    return False


def _has_tabular_step(plan) -> bool:
    return any(s.strategy == "tabular" and getattr(s, "sql", None) is not None
               for s in plan.steps)


@dataclass
class RunReport:
    source_id: str
    run_id: str
    run_dir: str
    status: str = "running"
    message: str = ""
    counts: dict = field(default_factory=dict)
    durations: dict = field(default_factory=dict)
    verify: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    plan: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)
    #: FIN-03: the screen policy this run ran under (`screen.screen_policy`)
    screen_policy: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def as_dict(self) -> dict:
        return {"source_id": self.source_id, "run_id": self.run_id, "run_dir": self.run_dir,
                "status": self.status, "message": self.message, "counts": self.counts,
                "durations_s": self.durations, "verify": self.verify, "notes": self.notes,
                "started_at": self.started_at, "finished_at": self.finished_at,
                "plan": self.plan, "timings": self.timings, "screen_policy": self.screen_policy}


# --- locations -----------------------------------------------------------------------------------
def source_dir(source_id: str) -> Path:
    """`<ingest>/<source_id>` — the id must be ONE safe path segment (Codex f1 #4)."""
    return paths.contained(paths.ingest_dir(), source_id, what="source id")


def run_dir_for(source_id: str, run_id: str) -> Path:
    """`<ingest>/<source_id>/<run_id>`, both ids checked as single safe segments and the result
    proven to lie under the ingest root — the ONE way a caller-supplied run id becomes a path."""
    return paths.contained(paths.ingest_dir(), source_id, run_id, what="source or run id")


def state_path(source_id: str) -> Path:
    return source_dir(source_id) / "state.json"


def load_state(source_id: str) -> dict:
    p = state_path(source_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def decided_path(source_id: str) -> Path:
    """`decided.json`: items the screen BLOCKED or sent to REVIEW, by content hash and screen
    fingerprint (E2E F-31) — so an unchanged, still-decided item is not re-extracted every sync."""
    return source_dir(source_id) / "decided.json"


def load_decided(source_id: str) -> dict:
    p = decided_path(source_id)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except (OSError, ValueError):
        return {}


def screen_fingerprint(cfg) -> str:
    """Changes whenever a rule, weight or threshold does: a decision made under other rules is
    re-made, never carried."""
    blob = json.dumps({
        "source": getattr(cfg, "source", ""),
        "thresholds": [cfg.block_threshold, cfg.review_threshold, cfg.min_signals_to_block],
        "rules": [[k, r.id, r.pattern.pattern, r.weight, r.min_count, r.validate]
                  # FIN-17: the prompt-injection hold is part of the rule set a decision was made
                  # under — a change to it re-screens, never carries
                  for k in ("decisive", "signals", "redactions", "quarantine")
                  for r in getattr(cfg, k, ())]}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def save_state(source_id: str, state: dict, *, run_id: Optional[str] = None) -> None:
    """Atomically replace `state.json`, then stamp a NEW state generation naming the run that
    wrote it (`state.gen.json`, Codex f2 #8) — what a resume checks before it may write again."""
    _atomic_text(state_path(source_id), json.dumps(state, sort_keys=True, indent=1))
    _atomic_text(state_gen_path(source_id),
                 json.dumps({"gen": uuid.uuid4().hex, "run_id": run_id, "at": now_iso()},
                            sort_keys=True))


def list_runs(source_id: str) -> list[dict]:
    """Every run of the source, oldest first, labelled by what is ON DISK (FIN-14):

    * a directory with `report.json` — that report's status;
    * no report, and the source's run lock is held in this process — `running` (the newest such
      directory only: a run in flight, not an `incomplete` one);
    * no report and no core-run ledgers, only the tabular recipe's own artefacts (profile,
      facts manifest, cards, stage log) — NOT a run: it is the recipe's side directory left by
      the run-id bug FIN-14 fixed, and it is not listed;
    * otherwise `incomplete` (a run that stopped without a report)."""
    base = source_dir(source_id)
    if not base.is_dir():
        return []
    dirs = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name)
    running = _run_in_progress(base)
    newest_open = next((d.name for d in reversed(dirs) if not (d / "report.json").is_file()
                        and _is_core_run_dir(d)), None)
    out = []
    for d in dirs:
        rep = d / "report.json"
        if not rep.is_file() and not _is_core_run_dir(d):
            continue
        try:
            data = json.loads(rep.read_text(encoding="utf-8")) if rep.is_file() else {}
        except (OSError, ValueError):
            data = {}
        status = data.get("status") or ("running" if running and d.name == newest_open
                                         else "incomplete")
        out.append({"run_id": d.name, "run_dir": str(d), "status": status,
                    "message": data.get("message", ""), "counts": data.get("counts", {}),
                    "finished_at": data.get("finished_at")})
    return out


#: Files only the CORE run writes (`_run_core`): a directory holding none of them and no report is
#: not a run.
_CORE_RUN_MARKERS = ("binding.json", "stages.json", "checkpoint.json", "inventory.jsonl",
                     "captured.jsonl", "plan.snapshot.yaml")


def _is_core_run_dir(d: Path) -> bool:
    return any((d / f).exists() for f in _CORE_RUN_MARKERS)


def _run_in_progress(base: Path) -> bool:
    """Is a run of this source in flight IN THIS PROCESS (the gateway runs syncs in-process)? Read
    from the run-lock registry; never takes the lock itself, so it cannot race a starting run."""
    try:
        key = os.path.normcase(str(base.resolve()))
    except OSError:
        return False
    with _LOCAL_RUNS_GUARD:
        return key in _LOCAL_RUNS


def _new_run_id() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


# --- the run -------------------------------------------------------------------------------------
class _Progress:
    def __init__(self, cb: Optional[Callable[[dict], None]], verbose: bool) -> None:
        self.cb = cb
        self.verbose = verbose
        self.t0 = time.monotonic()

    def __call__(self, stage: str, **info: Any) -> None:
        elapsed = time.monotonic() - self.t0
        event = {"stage": stage, "elapsed_s": round(elapsed, 2), **info}
        if self.cb is not None:
            self.cb(event)
        if self.verbose:
            done = info.get("done")
            rate = f" {done / elapsed:.1f}/s" if done and elapsed > 0 else ""
            detail = " ".join(f"{k}={v}" for k, v in info.items())
            print(f"[{elapsed:7.1f}s] {stage:8s} {detail}{rate}", flush=True)


class _Checkpoint:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.n = 0
        self.last = time.monotonic()

    def tick(self, payload: dict, *, force: bool = False) -> bool:
        self.n += 1
        if force or self.n >= CHECKPOINT_ITEMS or time.monotonic() - self.last >= CHECKPOINT_SECONDS:
            _atomic_text(self.path, json.dumps({**payload, "at": now_iso()}, sort_keys=True))
            self.n = 0
            self.last = time.monotonic()
            return True
        return False


PENDING_VERIFICATION = "pending_verification"


def _provisional(entry: dict) -> dict:
    """A state entry for an item BUILT but not yet verified (Codex f2 #10): it carries the content
    hash (so removal inference and `replaced_hash` still work) but `graph: false`, so an
    incremental run never treats it as unchanged until `_promote_state` confirms it."""
    if entry.get("graph") is False:
        return entry
    return {**entry, "graph": False, "reason": PENDING_VERIFICATION}


def _promote_state(state: dict, run_dir: Path, *, verified: bool) -> dict:
    """After verification: every item this run built (its `build.jsonl` outbox) whose state is
    still this run's PROVISIONAL entry is promoted to the final entry when the run verified, and
    left provisional (rebuilt next sync) when it did not."""
    out = {"promoted": 0, "withheld": 0}
    for r in read_jsonl(run_dir / "build.jsonl"):
        box = r.get("outbox") if isinstance(r.get("outbox"), dict) else {}
        final = box.get("state") if isinstance(box.get("state"), dict) else None
        iid = r.get("item_id")
        if not iid or final is None or final.get("graph") is False:
            continue
        cur = state.get(iid) or {}
        if cur.get("reason") != PENDING_VERIFICATION or \
                cur.get("content_hash") != final.get("content_hash"):
            continue
        if verified:
            state[iid] = final
            out["promoted"] += 1
        else:
            out["withheld"] += 1
    return out


def _run_started(run_dir: Path) -> dt.datetime:
    """The run's start time from `binding.json` - the ONE `retrieved_at` every graph write of
    the run carries, so a resumed attempt re-applies identical pointers (Codex f2 #7)."""
    try:
        at = json.loads((run_dir / "binding.json").read_text(encoding="utf-8")).get("at")
        return dt.datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except (OSError, ValueError, TypeError):
        return dt.datetime.now(dt.timezone.utc)


def _done_ids(run_dir: Path, *names: str) -> set[str]:
    out: set[str] = set()
    for n in names:
        out.update(r["item_id"] for r in read_jsonl(run_dir / f"{n}.jsonl") if r.get("item_id"))
    return out


def run_ingest(source_id: str, store=None, harness=None,
               progress_cb: Optional[Callable[[dict], None]] = None, *,
               run_id: Optional[str] = None, client=None, sources_root: Optional[Path] = None,
               verbose: bool = False, vocab=None, concurrency: Optional[int] = None,
               passages: Optional[bool] = None, cache: bool = True,
               bridges: bool = True, tabular: Optional[bool] = None,
               event_attrs: Optional[dict] = None, capture_only: bool = False) -> RunReport:
    """Run an APPROVED plan for one source end to end. `run_id` of an existing run resumes it:
    stages already marked done are skipped, collection skips items already decided, and the
    build skips items already built. `harness=None` builds document anchors only and says so.

    `concurrency`  model calls in flight at once (default 4, env `WALNUT_EXTRACT_CONCURRENCY`);
                   1 is fully serial. The graph written is the same either way.
    `passages`     index each built document into `databridge_retrieval` after the build (the
                   adapter agent's `extract.passages`); default off unless
                   `WALNUT_INGEST_PASSAGES=1`. Skipped — with the reason recorded — when off or
                   when the package is not importable.
    `cache`        use the on-disk model-output and conversion caches (`var/cache/`).
    `bridges`      recover relations whose ends fall in adjacent chunks (one extra, screened,
                   grounded window per chunk boundary that has such a pair).
    `tabular`      a plan with a SQL (`tabular`) step runs the database recipe around this run —
                   profile → mapping → [this run] → facts store → schema cards
                   (`tabular.ingest_database`) — so a normal sync refreshes facts and cards too.
                   Default on when the plan has such a step; False runs the core stages only.
    `event_attrs`  extra attributes merged into the run's `run_completed` / `run_failed` event
                   (e.g. an adapter agent's `{"shape_changed": ...}`). The §5.3 event TYPES are
                   unchanged, and the run's own attributes (status, message, counts, error) win
                   over a caller's key of the same name.

    Drift holds (`<sources>/<id>.holds.yaml`, see `HONOURS_DRIFT_HOLDS`) are honoured on every
    run: a held table / path is captured but not built, a held step is not enumerated.

    `capture_only` a DISCOVERY run (FEAT8, Codex R-027 AAX f1 #1): collect, extract, screen, and
                   propose the table mapping from the captured snapshots - and BUILD NOTHING, for
                   tables and documents alike. No graph store is opened; every graph-writing
                   entry point refuses on this thread (`build.capture_only`); the source's
                   incremental state is not advanced; no item or run event is emitted; the
                   database recipe (facts store, schema cards) does not run. The run writes
                   `discovery.json` (each captured table's capture digest and the mapping digest
                   proposed from it) and reports `discovered` (not `ok`: it is no sync, and never
                   freshness evidence) when it captured something and verified. See
                   `latest_discovery`."""
    conn = load_connection(source_id, sources_root)
    if getattr(conn, "purpose", "ingest") != "ingest":
        raise NotAnIngestConnection(
            f"connection {source_id!r} is declared purpose={conn.purpose!r}: it is the "
            f"write-capable route agents act through and is never crawled. Ingest the source "
            f"through a separate purpose=ingest connection with read-only credentials")
    plan = load_plan(source_id, sources_root)
    if plan.status != "approved":
        raise PlanNotApproved(
            f"the plan for {source_id!r} is {plan.status!r}; a signed-in person approves it "
            f"before it runs (auto_approve approves nothing)")
    approval_note = _require_plan_approval(plan, sources_root)
    if capture_only:
        if getattr(_TABULAR, "kwargs", None) is not None or _inside_tabular_recipe():
            raise PlanError("a capture-only discovery cannot run inside the database recipe")
        with _source_lock(source_id), _capture_only_guard():
            return _run_core(source_id, conn, plan, None, harness, progress_cb, run_id=run_id,
                             client=client, sources_root=sources_root, verbose=verbose,
                             vocab=vocab, concurrency=concurrency, passages=False, cache=cache,
                             bridges=bridges, nested=False, event_attrs=event_attrs,
                             approval_note=approval_note, discovery=True)
    outer = getattr(_TABULAR, "kwargs", None)
    nested = outer is not None and outer.get("source_id") == source_id
    if nested:
        # called back by `ingest_database` for the sync that started it: keep that sync's options
        # FIN-14: the run id the recipe ALLOCATED (and passes here) wins over the sync's option
        # (None for a fresh sync) — otherwise the core run took a second id and the recipe's
        # directory was left beside it as a phantom "incomplete" run
        run_id = run_id if run_id is not None else outer["run_id"]
        concurrency, passages = outer["concurrency"], outer["passages"]
        cache, bridges, verbose = outer["cache"], outer["bridges"], outer["verbose"]
        event_attrs = event_attrs if event_attrs is not None else outer.get("event_attrs")
    elif tabular is not False and _has_tabular_step(plan) and not _inside_tabular_recipe():
        # Codex f2 #9: the WHOLE recipe (profile, mapping, core run, facts, cards) runs under the
        # source's run lock - two syncs never profile or publish over each other
        with _source_lock(source_id):
            _TABULAR.locked = source_id
            try:
                return _run_with_tabular(
                    source_id, store, harness, progress_cb, client=client,
                    sources_root=sources_root, vocab=vocab,
                    options={"source_id": source_id, "run_id": run_id,
                             "concurrency": concurrency, "passages": passages, "cache": cache,
                             "bridges": bridges, "verbose": verbose,
                             "event_attrs": event_attrs})
            finally:
                _TABULAR.locked = None
    # re-entered only by THIS thread's own tabular recipe for the same source (f2 #9)
    with _source_lock(source_id, reentrant=getattr(_TABULAR, "locked", None) == source_id):
        return _run_core(source_id, conn, plan, store, harness, progress_cb, run_id=run_id,
                         client=client, sources_root=sources_root, verbose=verbose, vocab=vocab,
                         concurrency=concurrency, passages=passages, cache=cache,
                         bridges=bridges, nested=nested, event_attrs=event_attrs,
                         approval_note=approval_note)


def _run_core(source_id: str, conn, plan, store, harness, progress_cb, *, run_id, client,
              sources_root, verbose, vocab, concurrency, passages, cache, bridges, nested,
              event_attrs, approval_note, discovery: bool = False) -> RunReport:
    """The body of `run_ingest`, entered holding the source's run lock. `discovery`: the
    capture-only mode (no store is opened; nothing is built; see `run_ingest`)."""
    extra_attrs = dict(event_attrs or {})
    if discovery:
        store = None                             # a discovery never holds a graph store
    elif store is None:
        from databridge_graph.store import GraphStore

        db = paths.graph_db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        store = GraphStore(str(db))
    if vocab is None:
        from databridge_graph.packs import active_vocabulary

        vocab = active_vocabulary()

    # a directory the tabular recipe created for THIS sync (its profile and stage log only) is
    # not a run to resume: a resume needs a core run's own records (FIN-14)
    resuming = run_id is not None and _is_core_run_dir(run_dir_for(source_id, run_id))
    run_id = run_id or _new_run_id()
    run_dir = run_dir_for(source_id, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    report = RunReport(source_id=source_id, run_id=run_id, run_dir=str(run_dir),
                       started_at=now_iso(),
                       plan={"origin": plan.origin, "profiles": plan.profiles,
                             "approved_by": plan.approved_by,
                             "steps": [s.id for s in plan.steps]})
    if approval_note:
        report.notes.append(approval_note)
    if discovery and not resuming:
        # the manifest exists from the first moment, so a discovery that fails is the LATEST
        # discovery (reported failed by its report), never hidden behind an older success
        _atomic_text(run_dir / DISCOVERY_FILE, json.dumps(
            {"mode": DISCOVERY_MODE, "source_id": source_id, "run_id": run_id,
             "plan_digest": plan_digest(plan), "status": "running", "tables": {},
             "documents": {}, "built": 0, "at": now_iso(), "started_ns": time.time_ns()},
            sort_keys=True, indent=1))
    progress = _Progress(progress_cb, verbose)
    workers = _resolve_concurrency(concurrency)
    stages_file = run_dir / "stages.json"
    stages_done: dict = (json.loads(stages_file.read_text(encoding="utf-8"))
                         if resuming and stages_file.is_file() else {})
    # Codex f1 #2: a run is BOUND to the plan, screen rules, owner overrides and drift holds it
    # started under. A resume under anything else is refused BEFORE a ledger is touched (start a
    # new run instead) — a crashed run never finishes under rules nobody reviewed it with.
    # Read ONCE per run: a malformed holds file fails the run loudly (never "no holds").
    try:
        holds = load_holds(conn.id, sources_root)
        screen_cfg = load_screen()
        # Codex f2 #11: the owner overrides are read ONCE; this exact object is bound and passed
        # to every stage, so an approval or revocation mid-run cannot change what this run does
        overrides = load_overrides(source_id, sources_root)
        binding = _run_binding(plan, screen_cfg, overrides, holds)
        if discovery:
            binding["mode"] = DISCOVERY_MODE
        _bind_run(run_dir, binding, resuming=resuming, stages_done=stages_done)
        if resuming:
            # f2 #8 / #3: nobody advanced the source's state since, and the approved table
            # mapping is the one this run already built under - checked before a ledger is opened
            _check_state_generation(source_id, run_id, run_dir / "binding.json")
            _check_mapping_binding(run_dir, source_id, vocab, sources_root)
    except ResumeRefused:
        raise                            # the interrupted run is left exactly as it was
    except Exception as exc:
        report.status = "failed"
        report.message = _plain_error(exc, limit=1000)
        _write_report(report, run_dir)                  # durable first, then the event (f2 #13)
        if not discovery:
            emit_event("run_failed", source_id=source_id, run_id=run_id,
                       attrs={**extra_attrs, "error": report.message})
        if nested:
            _TABULAR.report = report
        raise
    snapshot = run_dir / "plan.snapshot.yaml"
    if not (resuming and snapshot.is_file()):
        # written ONCE: a resume never overwrites the plan the run was collected under
        snapshot.write_text(yaml.safe_dump(plan.model_dump(mode="json"), sort_keys=False),
                            encoding="utf-8")
    _configure_local_store(store, report)

    def mark(stage: str, seconds: float) -> None:
        # Codex f2 #14: every ledger line written so far is made durable BEFORE the stage is
        # recorded done, and `stages.json` is replaced atomically (never torn)
        for led in ledgers.values():
            sync = getattr(led, "sync", None)
            if callable(sync):
                sync()
        stages_done[stage] = {"done_at": now_iso(), "seconds": round(seconds, 3),
                              "breakdown": timings.stage(stage)}
        _atomic_text(stages_file, json.dumps(stages_done, sort_keys=True))
        report.durations[stage] = round(seconds, 3)

    # On resume, collect and build ledgers are kept (both resume item by item); the ledgers of
    # a stage that had not finished are started again, since that stage re-runs whole. Codex f1
    # #3: an abandoned attempt's ledger is MOVED to `attempts/<n>/` intact — never truncated —
    # so what an interrupted stage wrote stays on disk as history.
    stage_of = {"unchanged": "extract", "extracted": "extract", "extract_skipped": "extract",
                "governance": "screen"}
    if resuming:
        _retire_unfinished_ledgers(run_dir, [n for n, st in stage_of.items()
                                             if st not in stages_done])
    # every ledger with a typed format (`records.py`) is written THROUGH its model; a fresh run's
    # directory is new, so nothing is ever truncated
    ledgers = {n: typed_ledger(run_dir / f"{n}.jsonl") for n in LEDGERS}
    # created on the first held item only: its presence alone says "something was held this run"
    ledgers["held"] = _LazyLedger(run_dir / f"{HELD_LEDGER}.jsonl", truncate=False)
    checkpoint = _Checkpoint(run_dir / "checkpoint.json")
    timings = Timings(ledgers["timings"])
    meter = None
    state = load_state(source_id)
    # E2E run 5 F-01/F-02: the screen stage's verdict on every table row, reused by the build in
    # this process (a resumed build has none and screens the rows itself)
    row_verdicts: dict = {}
    report.notes.extend(screen_cfg.notes)
    report.notes.append(f"screen rules: {screen_cfg.source}")
    report.screen_policy = screen_policy(screen_cfg)
    enumeration_complete = True
    try:
        # ---------------------------------------------------------------- collect
        if "collect" not in stages_done:
            t0 = time.monotonic()
            pool_before = _pool_snapshot()
            enumeration_complete = _collect(conn, plan, ledgers, run_dir, client, progress,
                                            checkpoint, resuming, timings,
                                            holds=holds)
            mark("collect", time.monotonic() - t0)
            stages_done["collect"]["enumeration_complete"] = enumeration_complete
            pool_after = _pool_snapshot()
            if pool_after is not None:
                # the process-wide MCP session pool (client.py): cumulative counters, and what
                # THIS collect added to them (a warm lease is a hit; a spawn is a miss)
                stages_done["collect"]["mcp_pool"] = {
                    "after": pool_after,
                    "this_run": {k: v - (pool_before or {}).get(k, 0)
                                 for k, v in pool_after.items()
                                 if isinstance(v, (int, float)) and not isinstance(v, bool)
                                 and k not in ("idle", "leased", "keys", "max_idle",
                                               "idle_timeout_s")}}
                report.timings["mcp_pool"] = stages_done["collect"]["mcp_pool"]
            _atomic_text(stages_file, json.dumps(stages_done, sort_keys=True))
        else:
            enumeration_complete = bool(stages_done["collect"].get("enumeration_complete"))
        captured = read_jsonl(run_dir / "captured.jsonl")

        # ---------------------------------------------------------------- extract
        if "extract" not in stages_done:
            t0 = time.monotonic()
            decided = load_decided(source_id)
            # R-029 TIDY2: the anchor kind a document would be built under NOW (a connection's
            # `document_kind` may have been named since it was last built)
            anchor_now = document_kind(vocab, conn.document_kind)[0] if vocab is not None \
                else None
            _extract(captured, state, ledgers, progress, timings, cache, decided=decided,
                     fingerprint=screen_fingerprint(screen_cfg), overrides=overrides,
                     source_id=source_id, sources_root=sources_root, anchor_kind=anchor_now)
            mark("extract", time.monotonic() - t0)
        extracted = read_jsonl(run_dir / "extracted.jsonl")

        # ---------------------------------------------------------------- screen (pre-model)
        if "screen" not in stages_done:
            t0 = time.monotonic()
            _screen(extracted, screen_cfg, overrides, ledgers, progress, timings,
                    source_id=source_id, row_verdicts=row_verdicts)
            _record_decided(source_id, run_id, run_dir, screen_fingerprint(screen_cfg))
            mark("screen", time.monotonic() - t0)
        decisions = {r["item_id"]: r for r in read_jsonl(run_dir / "governance.jsonl")}

        # ---------------------------------------------------------------- discover
        # FEAT8 / Codex R-027 AAX f1 #1: a capture-only discovery proposes the table mapping from
        # what was captured and screened, and skips build, index and identity below - nothing
        # is built, indexed, consolidated or written to the source's state
        found: dict = {}
        if discovery:
            t0 = time.monotonic()
            found = _discover(conn, vocab, harness, captured, decisions, ledgers, run_dir,
                              sources_root, report)
            mark("discover", time.monotonic() - t0)

        # ---------------------------------------------------------------- build
        if not discovery and "build" not in stages_done:
            t0 = time.monotonic()
            doc_kind, note = document_kind(vocab, conn.document_kind)
            report.notes.append(note)
            if harness is None:
                report.notes.append("no model configured: document anchors only, no entity "
                                    "extraction (configure a key or pass a harness)")
            model_harness = harness
            if harness is not None:
                from .capture import cache_root
                from .llm import CachedHarness, ModelOutputCache

                rps = os.environ.get("WALNUT_EXTRACT_MAX_RPS")
                model_harness = CachedHarness(
                    harness, cache=ModelOutputCache(cache_root() / "extract") if cache else None,
                    max_concurrent=workers, max_per_second=float(rps) if rps else None)
                meter = model_harness.meter
            report.notes.append(f"extraction: {workers} worker(s); model-output cache "
                                f"{'on' if cache and harness is not None else 'off'}; "
                                f"cross-chunk bridges {'on' if bridges else 'off'}")
            built = _build(conn, store, model_harness, vocab, doc_kind, screen_cfg, extracted,
                           decisions, state, ledgers, run_dir, progress, checkpoint,
                           sources_root, report, timings=timings, workers=workers,
                           bridges=bridges, overrides=overrides,
                           retrieved_at=_run_started(run_dir), row_verdicts=row_verdicts)
            t_rm = time.perf_counter()
            _emit_item_events(conn.id, run_id, built, state, run_dir, enumeration_complete,
                              report, store=store, doc_kind=doc_kind, ledger=ledgers["removed"],
                              holds=holds, resuming=resuming)
            timings.add("build.removals", time.perf_counter() - t_rm)
            # built items are saved PROVISIONAL (graph: false, pending_verification) - they are
            # promoted only after verify passes (f2 #10); a crash before that rebuilds them
            save_state(source_id, state, run_id=run_id)
            mark("build", time.monotonic() - t0)

        # ---------------------------------------------------------------- index (retrieval)
        if not discovery and "index" not in stages_done:
            t0 = time.monotonic()
            _index_passages(conn.id, run_dir, _passages_enabled(passages), harness, timings,
                            report)
            mark("index", time.monotonic() - t0)

        # ---------------------------------------------------------------- identity (FIN-04)
        # the pack's APPROVED identity rules recognise as one the organisations this run wrote
        # and the records already in the graph; inert until a named human approves a rule, never
        # touching a subject kind. Before verify, so verification checks the consolidated graph.
        if not discovery and "identity" not in stages_done:
            t0 = time.monotonic()
            consolidation = consolidate_identity_after_run(store, vocab)
            _atomic_text(run_dir / IDENTITY_FILE, json.dumps(consolidation, sort_keys=True,
                                                             default=str, indent=1))
            report.notes.append(_identity_note(consolidation))
            mark("identity", time.monotonic() - t0)

        for led in ledgers.values():
            led.close()

        # ---------------------------------------------------------------- verify
        t0 = time.monotonic()
        report.verify = verify_run(source_id, run_id, store=store)
        (run_dir / "verify.json").write_text(json.dumps(report.verify, sort_keys=True,
                                                        indent=1), encoding="utf-8")
        timings.add("verify.reconcile", time.monotonic() - t0)
        mark("verify", time.monotonic() - t0)
        report.timings = {**report.timings,
                          **timings.as_dict(meter.as_dict() if meter is not None else None)}
        report.counts = report.verify.get("counts", {})
        _count_backfill(report, run_dir)
        _note_holds(report, run_dir)
        _finalise_status(report, enumeration_complete)
        if discovery:
            _seal_discovery(report, run_dir, found, plan)
        # Codex f2 #10: incremental state advances ONLY for a run that verified (a discovery
        # built nothing, so it never advances it)
        state = {} if discovery else load_state(source_id)
        promoted = _promote_state(state, run_dir, verified=bool(report.verify.get("ok")))
        if promoted["promoted"]:
            save_state(source_id, state, run_id=run_id)
            if any(r.get("kind") == "table" for r in read_jsonl(run_dir / "build.jsonl")):
                _refresh_links_elsewhere(source_id, vocab, sources_root, report)
        if promoted["withheld"]:
            report.notes.append(f"state NOT advanced for {promoted['withheld']} item(s): "
                                f"verification failed, so the next sync rebuilds and re-verifies "
                                f"them")
    except Exception as exc:
        for led in ledgers.values():
            led.close()
        report.status = "failed"
        report.message = _plain_error(exc, limit=1000)
        report.timings = {**report.timings,
                          **timings.as_dict(meter.as_dict() if meter is not None else None)}
        _write_report(report, run_dir)                  # durable first, then the event (f2 #13)
        if not discovery:
            emit_event("run_failed", source_id=source_id, run_id=run_id,
                       attrs={**extra_attrs, "error": report.message})
        if nested:
            _TABULAR.report = report
        raise
    # Codex f2 #13: report.json is durable BEFORE the completion event; f2 #9: inside the tabular
    # recipe the event waits until the recipe's own outcome is known (`_run_with_tabular`)
    _write_report(report, run_dir)
    if discovery:
        # a discovery is no sync: no run event (nothing downstream may act on it as new data)
        progress("report", status=report.status, message=report.message)
        return report
    final = ("run_completed" if report.status in ("ok", "empty") else "run_failed",
             {**extra_attrs, "status": report.status, "message": report.message,
              **{k: report.counts.get(k) for k in ("inventory", "captured", "built")}})
    if getattr(_TABULAR, "defer_event", None) == source_id:
        _TABULAR.pending_event = final
    else:
        emit_event(final[0], source_id=source_id, run_id=run_id, attrs=final[1])
    if nested:
        _TABULAR.report = report
    progress("report", status=report.status, message=report.message)
    return report


def _run_with_tabular(source_id: str, store, harness, progress_cb, *, client, sources_root,
                      vocab, options: dict) -> RunReport:
    """A sync of a source whose plan has a SQL step: the database recipe (S2's
    `tabular.ingest_database`) on one session, with THIS module's core run inside it. The core
    run's report is returned, annotated with the tabular outcome. If the recipe fails BEFORE the
    core run, the core run happens anyway (the graph must not go stale because profiling broke)
    and the failure is recorded; if it fails AFTER, the core report stands with the failure noted;
    if the core run itself fails, that failure is raised as always."""
    try:
        from .tabular.pipeline import ingest_database
    except ImportError as exc:
        report = run_ingest(source_id, store, harness, progress_cb, client=client,
                            sources_root=sources_root, vocab=vocab, tabular=False,
                            event_attrs=options.get("event_attrs"),
                            **{k: options[k] for k in ("run_id", "concurrency", "passages",
                                                       "cache", "bridges", "verbose")})
        report.notes.append(f"tabular: recipe not available here ({exc}); core stages only")
        _write_report(report, Path(report.run_dir))
        return report
    # f2 #9: the core run's completion event is HELD until the recipe's own outcome is known;
    # a recipe that fails (before or after the graph run) makes the sync `unverified`
    _TABULAR.kwargs, _TABULAR.report = options, None
    _TABULAR.defer_event, _TABULAR.pending_event = source_id, None
    t0 = time.monotonic()
    try:
        try:
            out = ingest_database(source_id, store, harness, client=client, vocab=vocab,
                                  sources_root=sources_root,
                                  index=_passages_enabled(options["passages"]),
                                  progress_cb=progress_cb, run_id=options.get("run_id"))
        except Exception as exc:                                     # noqa: BLE001
            report = getattr(_TABULAR, "report", None)
            _TABULAR.kwargs = _TABULAR.report = None
            if report is None:
                report = run_ingest(source_id, store, harness, progress_cb, client=client,
                                    sources_root=sources_root, vocab=vocab, tabular=False,
                                    event_attrs=options.get("event_attrs"),
                                    **{k: options[k] for k in ("run_id", "concurrency",
                                                               "passages", "cache", "bridges",
                                                               "verbose")})
                _tabular_failed(report, f"the database recipe failed before the graph run "
                                        f"({_plain_error(exc)}); core stages ran alone - no "
                                        f"facts or schema cards this run")
                return report
            if report.status == "failed":
                raise
            _tabular_failed(report, f"the database recipe failed after the graph run - "
                                    f"{_plain_error(exc)}")
            return report
        report = _TABULAR.report
        _TABULAR.report = None
        tab = out.get("tabular") or {}
        report.notes.append(f"tabular: {tab.get('tables_profiled', 0)} table(s) profiled, facts "
                            f"{(tab.get('facts') or {}).get('status')}, {tab.get('cards', 0)} "
                            f"schema card(s)")
        report.timings["tabular"] = {**(out.get("timings") or {}),
                                     "wall_s": round(time.monotonic() - t0, 3)}
        _write_report(report, Path(report.run_dir))
        _emit_pending(report)
        if progress_cb is not None:
            progress_cb({"stage": "report", "status": report.status,
                         "message": report.message})
        return report
    finally:
        _TABULAR.kwargs = None
        _TABULAR.defer_event = None
        _TABULAR.pending_event = None


def _emit_pending(report: RunReport) -> None:
    """Emit the core run's held completion event (if it produced one), AFTER report.json."""
    pending = getattr(_TABULAR, "pending_event", None)
    _TABULAR.pending_event = None
    if pending is None:
        return
    kind, attrs = pending
    if report.status not in ("ok", "empty"):
        kind = "run_failed"
    emit_event(kind, source_id=report.source_id, run_id=report.run_id,
               attrs={**attrs, "status": report.status, "message": report.message})


def _tabular_failed(report: RunReport, why: str) -> None:
    """A sync whose database recipe failed is NOT a success (f2 #9): an `ok` graph run becomes
    `unverified` (facts / schema cards were not refreshed), the reason is the message, the report
    is written, and only then is the held event emitted (as `run_failed`)."""
    report.notes.append(f"tabular: {why}")
    if report.status == "ok":
        report.status = "unverified"
        report.message = f"graph run ok, but {why}"
    _write_report(report, Path(report.run_dir))
    _emit_pending(report)


#: What produced a refusal the model never saw (e.g. "no model configured"): this package.
INGEST_PRODUCER = {"component": "databridge_mcp_ingest", "kind": "rule"}


def _run_produced_by(report: RunReport, run_dir: Path) -> dict:
    """§5.8 for the run as a whole (E2E F-33): the component, the run, and every distinct model
    stamp the build recorded (`build.jsonl` `produced_by`) — none when no model was called."""
    stamps: dict[str, dict] = {}
    for r in read_jsonl(run_dir / "build.jsonl"):
        for st in r.get("produced_by") or []:
            if isinstance(st, dict):
                stamps[json.dumps(st, sort_keys=True, default=str)] = st
    return {**INGEST_PRODUCER, "run_id": report.run_id,
            "models_used": [stamps[k] for k in sorted(stamps)]}


def _write_report(report: RunReport, run_dir: Path) -> None:
    report.finished_at = now_iso()
    # built THROUGH the typed format its readers use (`records.RunReportRecord`)
    body = json.loads(json.dumps(report.as_dict(), default=str))
    # f1 #5: nothing a person reads back from report.json carries a credential
    # R-029 TIDY2: ... nor a server path (a rules file, a store, a source's own directory)
    body["message"] = public_text(scrub(body.get("message") or ""))
    body["notes"] = [public_text(scrub(n)) for n in body.get("notes") or []]
    body["produced_by"] = _run_produced_by(report, run_dir)
    body = RunReportRecord.model_validate(body).model_dump(mode="json", exclude_unset=True)
    _atomic_text(run_dir / "report.json", json.dumps(body, sort_keys=True, indent=1,
                                                     default=str))


def _count_backfill(report: RunReport, run_dir: Path) -> None:
    """E2E run-2 N-01: the passage back-fill this run did, in report.json's counts."""
    try:
        bf = json.loads((run_dir / "index.json").read_text(encoding="utf-8")).get("backfill")
    except (OSError, ValueError):
        bf = None
    if isinstance(bf, dict):
        report.counts["passages_backfilled"] = bf.get("indexed", 0)
        report.counts["passages_backfill_pending"] = bf.get("pending", 0)


def _note_holds(report: RunReport, run_dir: Path) -> None:
    """Say what drift holds did this run (P3: a hold is never silent)."""
    held = held_items(run_dir)
    failed = read_jsonl(run_dir / "failed.jsonl")
    steps = [r for r in failed if r.get("rule") == DRIFT_PENDING_APPROVAL]
    changed = [r for r in failed if r.get("rule") == TOOL_CHANGED]
    if changed:
        report.counts["tool_changed"] = len(changed)
        report.notes.append(
            f"tool changed ({TOOL_CHANGED}): {len(changed)} step(s)/item(s) HELD because a tool "
            f"the plan calls changed on the server since the plan was approved — "
            f"{_TOOL_CHANGED_REVERSE}; nothing was inferred removed")
    if held or steps:
        report.counts["held"] = len(held)
        report.counts["held_steps"] = len(steps)
        report.notes.append(
            f"drift holds ({DRIFT_PENDING_APPROVAL}): {len(held)} item(s) captured but not "
            f"built, {len(steps)} step(s) not enumerated — they await a person's decision on "
            f"a shape change (approve_adaptation / reject_adaptation); everything else synced")


def _finalise_status(report: RunReport, enumeration_complete: bool) -> None:
    c = report.counts
    problems = report.verify.get("problems") or []
    if problems:
        report.status = "unverified"
        report.message = f"verification found {len(problems)} problem(s): {problems[0]}"
    elif c.get("tool_changed") and not c.get("captured"):
        report.status = "empty"
        report.message = (f"HELD ({TOOL_CHANGED}): {c.get('tool_changed')} step(s)/item(s) were "
                          f"not read because a tool the approved plan calls changed on the "
                          f"server since it was approved. Nothing was inferred removed; "
                          f"{_TOOL_CHANGED_REVERSE}")
    elif not c.get("inventory") and c.get("held_steps") and \
            c.get("held_steps") == c.get("failed_listing"):
        report.status = "empty"
        report.message = (f"EMPTY RUN: every listed step ({c.get('held_steps')}) is HELD "
                          f"({DRIFT_PENDING_APPROVAL}) — the tool it calls changed shape and a "
                          f"person has not decided yet (approve_adaptation / reject_adaptation). "
                          f"Nothing was inferred removed")
    elif not c.get("inventory"):
        report.status = "empty"
        report.message = ("EMPTY RUN: the source listed nothing. This is not a success — check the "
                          "roots and scope, and that the server can see the files")
    elif not c.get("captured"):
        report.status = "empty"
        report.message = (f"EMPTY RUN: {c.get('inventory')} item(s) seen, none captured "
                          f"({c.get('excluded', 0)} excluded, {c.get('blocked', 0)} blocked, "
                          f"{c.get('failed', 0)} failed). Not a success — see the ledgers")
    else:
        report.status = "ok"
        report.message = (f"{c.get('inventory')} seen, {c.get('captured')} captured, "
                          f"{c.get('built', 0)} built, {c.get('unchanged', 0)} unchanged")
        if c.get("extract_failed"):
            # FIN-01: honest partial coverage - the source synced, these items did not
            allowed = (c.get("governance") or {}).get("allow", 0)
            report.message = (f"PARTIAL COVERAGE: {c['extract_failed']} of {allowed} allowed "
                              f"item(s) failed model extraction and are not in the graph (see "
                              f"build_skipped.jsonl, reason {EXTRACT_FAILED}; they are retried "
                              f"next sync). " + report.message)
    if not enumeration_complete:
        report.notes.append("listing did not complete (see failed.jsonl); removals were not "
                            "inferred from this run")


# --- stages --------------------------------------------------------------------------------------
def _collect(conn: SourceConnection, plan: CrawlPlan, ledgers: dict, run_dir: Path, client,
             progress, checkpoint: _Checkpoint, resuming: bool,
             timings: Optional[Timings] = None, holds: Optional[DriftHolds] = None) -> bool:
    timings = timings or Timings()
    holds = holds or DriftHolds(source_id=conn.id)
    already = _done_ids(run_dir, "captured", "excluded", "blocked") if resuming else set()
    complete = True
    counts = {"seen": 0, "captured": 0}

    def record(item: Item, decision: Decision) -> None:
        rec = item.as_record()
        ledgers["inventory"].write({**rec, "decision": decision.outcome})
        if decision.outcome in ("excluded", "blocked"):
            ledgers[decision.outcome].write({"item_id": item.item_id, "uri": item.uri,
                                             "rel": item.rel, "rule": decision.rule,
                                             "reverse": decision.reverse, "at": now_iso()})

    def on_dir(info: dict) -> None:
        iid = item_identity(conn.id, info["uri"])
        if iid in already:
            return
        ledgers["inventory"].write({"item_id": iid, "source_id": conn.id, "kind": "folder",
                                    "uri": info["uri"], "rel": info["rel"],
                                    "decision": info["outcome"]})
        ledgers[info["outcome"]].write({"item_id": iid, "uri": info["uri"], "rel": info["rel"],
                                        "rule": info["rule"], "reverse": info["reverse"],
                                        "at": now_iso()})

    def outside_roots(step_id: str):
        def record_outside(count: int, roots: list) -> None:
            # ONE aggregate line: the individual resources are outside what the owner declared,
            # so their names are not written down — only that they were not captured, and why
            ledgers["excluded"].write({"item_id": None, "step": step_id, "rule": "outside_roots",
                                       "count": count, "roots": roots,
                                       "reverse": "declare a scope.roots entry that contains "
                                                  "them (or remove scope.roots to take all)",
                                       "at": now_iso()})
        return record_outside

    def run_with(c) -> None:
        nonlocal complete
        for step in plan.steps:
            step_aid = holds.step_hold(step.id)
            if step_aid:
                # never enumerated, and the listing is marked incomplete, so nothing this step
                # would have listed is inferred removed while the drift awaits a person
                complete = False
                ledgers["failed"].write({
                    "item_id": None, "step": step.id, "stage": "enumerate",
                    "error": f"{DRIFT_PENDING_APPROVAL}: step {step.id!r} is held by adaptation "
                             f"{step_aid} (the tool it calls changed shape)",
                    "rule": DRIFT_PENDING_APPROVAL, "adaptation_id": step_aid,
                    "reverse": holds.reverse_text(step_aid), "at": now_iso()})
                continue
            try:
                if step.strategy == "documents":
                    items = enumerate_documents(c, step, conn.scope, conn.id, on_dir=on_dir)
                elif step.strategy == "resources":
                    items = enumerate_resources(c, step, conn.id, scope=conn.scope,
                                                on_outside=outside_roots(step.id))
                else:
                    items = enumerate_tables(c, step, conn.id)
                for item in _timed_iter(items, timings, "collect.mcp_list"):
                    if item.item_id in already:
                        continue
                    counts["seen"] += 1
                    if scrub(item.uri) != item.uri:
                        # f2 #4: a locator that carries a credential (userinfo, a signed or
                        # token-bearing URL) is never fetched or captured; the ledgers keep only
                        # its scrubbed form
                        record(item, Decision("blocked", CREDENTIAL_IN_LOCATOR,
                                              "expose the item through a locator without "
                                              "credentials (the connection holds them)"))
                        continue
                    decision = decide(item, conn.scope, collected_so_far=counts["captured"],
                                      holds=holds)
                    aid = decision.hold                 # held: captured below, never built
                    if decision.outcome != "collect" and not aid:
                        record(item, decision)
                        continue
                    got = _fetch_one(c, step, item, conn, ledgers, record, counts, timings)
                    if aid and got:
                        # captured (so it stays inventoried and observable), NOT built
                        ledgers["held"].write({
                            "item_id": item.item_id, "uri": item.uri, "rel": item.rel,
                            "rule": DRIFT_PENDING_APPROVAL, "reverse": holds.reverse_text(aid),
                            "adaptation_id": aid, "at": now_iso(),
                            "note": "captured, NOT built: held until a person decides"})
                    checkpoint.tick({"stage": "collect", **counts})
                    if counts["seen"] % CHECKPOINT_ITEMS == 0:
                        progress("collect", done=counts["seen"], captured=counts["captured"])
            except (McpClientError, CrawlError, Exception) as exc:      # noqa: BLE001
                complete = False
                ledgers["failed"].write({"item_id": None, "step": step.id, "stage": "enumerate",
                                         "error": _plain_error(exc), "at": now_iso(),
                                         **_tool_changed_hold(exc)})

    if client is not None:
        run_with(ReadOnlyClient(client, plan=plan))
    else:
        t0 = time.perf_counter()
        with open_client(conn) as c:
            timings.add("collect.mcp_connect", time.perf_counter() - t0)
            run_with(ReadOnlyClient(c, plan=plan))
    checkpoint.tick({"stage": "collect", **counts}, force=True)
    progress("collect", done=counts["seen"], captured=counts["captured"])
    return complete


def _tool_changed_hold(exc: BaseException) -> dict:
    """The hold fields for a `ToolChanged` refusal anywhere in `exc`'s cause chain, else {}."""
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, ToolChanged):
            return {"rule": TOOL_CHANGED, "reverse": _TOOL_CHANGED_REVERSE}
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return {}


def _plain_error(exc: BaseException, limit: int = 300) -> str:
    """One readable line: a server's multi-kilobyte validation dump is not a reason a person can
    act on. The full text is not lost — it is the server's to log."""
    text = " ".join(scrub(str(exc)).split())
    return f"{type(exc).__name__}: {text[:limit]}{'…' if len(text) > limit else ''}"


def _fetch_one(client, step, item: Item, conn: SourceConnection, ledgers: dict, record,
               counts: dict, timings: Optional[Timings] = None) -> bool:
    """Fetch and capture one item. True when it was captured."""
    timings = timings or Timings()
    t0 = time.perf_counter()
    try:
        if item.kind == "table":
            data, snapshot = fetch_table(client, step, item, limit=conn.scope.max_rows_per_table,
                                         page_rows=conn.scope.table_page_rows)
            mime = "application/json"
        else:
            data, mime = fetch_document(client, step, item)
            snapshot = None
    except Exception as exc:                                         # noqa: BLE001
        timings.add("collect.mcp_read", time.perf_counter() - t0)
        ledgers["inventory"].write({**item.as_record(), "decision": "failed"})
        ledgers["failed"].write({"item_id": item.item_id, "uri": item.uri, "stage": "fetch",
                                 "error": _plain_error(exc), "at": now_iso(),
                                 **_tool_changed_hold(exc)})
        return False
    t_read = time.perf_counter() - t0
    timings.add("collect.mcp_read", t_read)
    if item.kind != "table" and len(data) > conn.scope.max_bytes:
        record(item, Decision("excluded", "size_cap",
                              f"raise scope.max_bytes above {len(data)}"))
        return False
    t1 = time.perf_counter()
    sha = capture.put_bytes(data)
    meta = {"source": conn.id, "uri": item.uri, "mime": mime, "title": item.name,
            "retrieved_at": now_iso()}
    if item.attrs:
        meta["attrs"] = dict(item.attrs)          # e.g. modified time + source-native mime
    capture.put_meta(sha, meta)
    if snapshot is not None:
        try:
            capture.put_text(sha, table_text(snapshot))
        except capture.TextConflict:
            pass        # write-once (f2 #5): the stored rendering is kept; extract records ITS digest
        total, limit = snapshot.get("row_count_total"), snapshot.get("limit")
        read = len(snapshot.get("rows") or [])
        if total is not None and total > read:
            # C-2: never silently short — "N of M rows", and why
            capped = limit is not None and read >= int(limit)
            ledgers["coverage"].write({
                "item_id": item.item_id, "uri": item.uri,
                "rule": "row_cap" if capped else "rows_short",
                "rows_total": total, "rows_read": read,
                "detail": f"{read} of {total} rows read",
                "reverse": (f"raise or remove scope.max_rows_per_table ({limit}) on the "
                            f"connection" if capped else
                            "the table changed while it was read, or the dialect cannot page "
                            "(no select_page); re-run the source")})
    t_cap = time.perf_counter() - t1
    timings.add("collect.capture_write", t_cap)
    timings.item(item.item_id, "collect", t_read + t_cap, read_ms=round(t_read * 1000, 2),
                 capture_ms=round(t_cap * 1000, 2), bytes=len(data))
    ledgers["inventory"].write({**item.as_record(), "decision": "collect"})
    ledgers["captured"].write({"item_id": item.item_id, "uri": item.uri, "name": item.name,
                               "kind": item.kind, "sha256": sha, "bytes": len(data),
                               "mime": mime, "step": item.step_id, "at": now_iso(),
                               # anchors the capture's `.json` to this run (R-019 deferred #5)
                               "meta_digest": capture.stored_meta_digest(sha),
                               **({"attrs": dict(item.attrs)} if item.attrs else {})})
    counts["captured"] += 1
    return True


def held_items(run_dir: Path) -> dict[str, dict]:
    """item_id -> hold record, for the items this run captured but holds (`excluded.drift.jsonl`,
    rule DRIFT_PENDING_APPROVAL)."""
    return {r["item_id"]: r for r in read_jsonl(run_dir / f"{HELD_LEDGER}.jsonl")
            if r.get("item_id") and r.get("rule") == DRIFT_PENDING_APPROVAL}


def _extract(captured: list[dict], state: dict, ledgers: dict, progress,
             timings: Optional[Timings] = None, cache: bool = True, *,
             decided: Optional[dict] = None, fingerprint: str = "",
             overrides: Optional[set] = None, source_id: Optional[str] = None,
             sources_root: Optional[Path] = None, anchor_kind: Optional[str] = None) -> None:
    from .extract import extract_text

    timings = timings or Timings()
    decided, overrides = decided or {}, overrides or set()
    # E2E run 5 F-03: unchanged tables that must be rebuilt anyway (a referenced table was built,
    # a held row was lifted) — decided over the WHOLE run's captures first, so order is moot
    rebuild = (_rebuild_for_references(captured, state, source_id, overrides,
                                       approved=_approved_digests(source_id, sources_root))
               if source_id else {})
    for i, rec in enumerate(captured, 1):
        iid, sha = rec["item_id"], rec["sha256"]
        prev = state.get(iid)
        # R-029 TIDY2: a DOCUMENT built under another anchor kind than the one it would get now
        # (an operator named the connection's `document_kind` after it was built with none) is
        # rebuilt, so it is anchored. A state written before the kind was recorded is not.
        if prev and rec.get("kind") != "table" and "anchor_kind" in prev \
                and prev.get("anchor_kind") != anchor_kind:
            rebuild[iid] = "anchor_kind_changed"
        # an item seen but NOT built (F-23: a table awaiting its mapping) is never "unchanged":
        # it is re-read until it is built
        if prev and prev.get("content_hash") == sha and prev.get("graph", True) is not False \
                and iid not in rebuild:
            ledgers["unchanged"].write({"item_id": iid, "uri": rec["uri"], "sha256": sha,
                                        "last_run": prev.get("run_id")})
            continue
        dec = decided.get(iid)
        if dec and dec.get("content_hash") == sha and dec.get("screen") == fingerprint \
                and not _override_allows(overrides, iid, sha):
            # E2E F-31: blocked / sent to review, unchanged, same rules, no owner override —
            # the decision stands. Not re-extracted; the decision is carried into this run's
            # governance ledger so exclusion counts stay truthful.
            ledgers["unchanged"].write({"item_id": iid, "uri": rec["uri"], "sha256": sha,
                                        "last_run": dec.get("run_id"),
                                        "decided": dec.get("decision")})
            ledgers["governance"].write({**(dec.get("record") or {}), "item_id": iid,
                                         "uri": rec["uri"], "sha256": sha,
                                         "decision": dec.get("decision"),
                                         "carried_forward": True,
                                         "decided_in_run": dec.get("run_id"), "at": now_iso()})
            continue
        if rec.get("kind") == "table":
            text = capture.get_text(sha) or ""
            ledgers["extracted"].write({"item_id": iid, "uri": rec["uri"], "sha256": sha,
                                        **({"rebuild": rebuild[iid]} if iid in rebuild else {}),
                                        "kind": "table", "chars": len(text),
                                        "extractor": "table", "text_sha256": text_digest(text),
                                        # f2 #15: tables carry the metadata anchor too
                                        **({"meta_digest": rec["meta_digest"]}
                                           if rec.get("meta_digest") else {})})
            continue
        # the extractor is chosen BEFORE the bytes are read (P10)
        kind = extractor_for(rec.get("name") or rec["uri"], rec.get("mime"))
        if kind is None:
            ledgers["extract_skipped"].write({"item_id": iid, "uri": rec["uri"], "sha256": sha,
                                              "reason": "no_extractor", "mime": rec.get("mime")})
            continue
        t0 = time.perf_counter()
        hit = False
        try:
            data = capture.get_bytes(sha)
            name = rec.get("name") or rec["uri"]
            if cache:
                got, hit = extract_text_cached(data, sha=sha, name=name, mime=rec.get("mime"),
                                               source_id=iid)
            else:
                got = extract_text(data, name=name, mime=rec.get("mime"), source_id=iid)
        except ExtractionFailed as exc:
            timings.add(f"extract.{'docling' if kind not in ('text', 'email') else kind}_failed",
                        time.perf_counter() - t0)
            reason = ("extractor_unavailable" if "extractor_unavailable" in str(exc)
                      else "extract_failed")
            ledgers["extract_skipped"].write({"item_id": iid, "uri": rec["uri"], "sha256": sha,
                                              "reason": reason, "detail": _plain_error(exc)})
            continue
        took = time.perf_counter() - t0
        bucket = kind if kind in ("text", "email") else ("convert_cache_hit" if hit else "docling")
        timings.add(f"extract.{bucket}", took)
        timings.item(iid, "extract", took, extractor=got.extractor, cache_hit=hit)
        if not got.text.strip():
            ledgers["extract_skipped"].write({"item_id": iid, "uri": rec["uri"], "sha256": sha,
                                              "reason": "no_text", "detail": got.extractor})
            continue
        # the text digest goes into the ledger AND a `<sha>.txt.json` sidecar, so materialise
        # verifies the text without docling (R-022 build cross-track 5)
        text_sha, chars, extractor = got.text_sha256, len(got.text), got.extractor
        try:
            capture.put_text(sha, got.text, text_sha256=got.text_sha256, extractor=got.extractor)
        except capture.TextConflict as exc:
            # f2 #5: the text is WRITE-ONCE per capture - existing pointers index the stored one.
            # This run uses the stored text, provided it still matches its own sidecar digest.
            side = capture.get_text_meta(sha) or {}
            if side.get("text_sha256") != exc.existing_digest:
                ledgers["extract_skipped"].write({
                    "item_id": iid, "uri": rec["uri"], "sha256": sha, "reason": "text_conflict",
                    "detail": "a different extraction of these bytes is stored and does not "
                              "match its recorded digest; nothing built from it"})
                continue
            stored = capture.get_text(sha, expect_text_digest=exc.existing_digest) or ""
            text_sha, chars = exc.existing_digest, len(stored)
            extractor = str(side.get("extractor") or extractor)
        ledgers["extracted"].write({"item_id": iid, "uri": rec["uri"], "sha256": sha,
                                    "kind": rec.get("kind", "document"), "name": rec.get("name"),
                                    "chars": chars, "extractor": extractor,
                                    "text_sha256": text_sha,
                                    "warnings": list(got.warnings), "cache_hit": hit,
                                    # carried so the build verifies the capture's `.json`
                                    **({"meta_digest": rec["meta_digest"]}
                                       if rec.get("meta_digest") else {})})
        if i % CHECKPOINT_ITEMS == 0:
            progress("extract", done=i, total=len(captured))
    progress("extract", done=len(captured), total=len(captured))


def _record_decided(source_id: str, run_id: str, run_dir: Path, fingerprint: str) -> None:
    """Persist this run's FRESH block / review decisions (F-31); an item screened `allow` (or
    no longer decided) is dropped, so it is never skipped on stale grounds."""
    ledgers_fresh = [r for r in read_jsonl(run_dir / "governance.jsonl")
                     if not r.get("carried_forward")]
    if not ledgers_fresh:
        return
    decided = load_decided(source_id)
    keep = ("decision", "score", "fired", "rationale", "reverse", "kind", "rules_source")
    for r in ledgers_fresh:
        iid = r.get("item_id")
        if not iid or r.get("kind") in ("table", TABLE_ROW):
            continue
        if r.get("decision") in ("block", "review"):
            decided[iid] = {"content_hash": r.get("sha256"), "decision": r.get("decision"),
                            "screen": fingerprint, "run_id": run_id,
                            "record": {k: r.get(k) for k in keep}}
        else:
            decided.pop(iid, None)
    p = decided_path(source_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".decided.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(decided, sort_keys=True, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, p)


#: governance `kind` of one table ROW the screen did not allow (E2E run 5 F-01). Its `item_id` is
#: the row's `row_ref` and its `sha256` the row's `row_digest`: what an owner's override names
TABLE_ROW = "table_row"
#: the screen stage reports progress through a table every this many rows (E2E run 5 F-02)
SCREEN_PROGRESS_ROWS = 5000
#: the verdict kept for the build on a row the screen ALLOWED (its text is not kept twice)
_ROW_ALLOWED = ScreenResult("allow", 0.0, [], [], "", "allowed by this run's screen", "")


def _screen_table(rec: dict, cfg, overrides, ledgers: dict, progress, *, source_id: str,
                  verdicts: Optional[dict] = None) -> None:
    """E2E run 5 F-01: a TABLE is screened row by row — one planted cell never holds the whole
    table. The table's own metadata (names, types, comments, keys: everything but its rows) is
    screened as the item: when IT is not allowed, the whole table is held, since every row is read
    through it. Otherwise the table is `allow`ed, every row the screen does not allow is written as
    its OWN governance decision (`kind: table_row`, `item_id` = its `row_ref`, `sha256` = its
    `row_digest` — liftable one by one by an owner's override, never as a table), and the build
    withholds exactly those rows. The table's decision counts them (`rows_held`), so a held row is
    never silent. `verdicts` collects every row's verdict for the build of this same run."""
    from .build import _render_pk
    from .screen import row_digest, row_ref

    snapshot = json.loads(capture.get_bytes(rec["sha256"]))
    tref = (f"{snapshot.get('schema')}.{snapshot.get('table')}" if snapshot.get("schema")
            else str(snapshot.get("table")))
    meta = {k: v for k, v in snapshot.items() if k != "rows"}
    result = screen_text(json.dumps(meta, sort_keys=True, default=str), cfg,
                         override_allow=_override_allows(overrides, rec["item_id"],
                                                         rec["sha256"]))
    rows = snapshot.get("rows") or []
    pk = list(snapshot.get("primary_key") or [])
    held = {"review": 0, "block": 0}
    for ordinal, row in enumerate(rows):
        pk_value = _render_pk(row, pk)
        row_id = pk_value if pk_value is not None else f"#{ordinal}"
        ref, digest = row_ref(source_id, tref, row_id), row_digest(row)
        verdict = screen_text(json.dumps(row, sort_keys=True, default=str), cfg,
                              override_allow=_override_allows(overrides, ref, digest))
        if verdicts is not None:
            # an allowed row's verdict is only its decision (the build reads nothing else)
            verdicts[ref] = (digest, verdict if verdict.decision != "allow" else _ROW_ALLOWED)
        if ordinal and ordinal % SCREEN_PROGRESS_ROWS == 0:
            progress("screen", table=tref, rows_done=ordinal, rows_total=len(rows))
        if verdict.decision == "allow":
            continue
        held[verdict.decision] = held.get(verdict.decision, 0) + 1
        # a key value the screen masks is never written out raw, not even in a ledger line
        sensitive = any(isinstance(row.get(c), str) and apply_masks(row[c], cfg.redactions)[1]
                        for c in pk)
        shown = ref if sensitive else row_id
        line = verdict.as_record()
        if verdict.decision == "review":
            line["reverse"] = ("an authenticated owner approves THIS row (an override naming "
                               "its row_ref as the item and its row_digest) and the source is "
                               "re-run; the rest of the table is built meanwhile")
        ledgers["governance"].write({**line, "item_id": ref, "uri": f"{tref}#{shown}",
                                     "sha256": digest, "kind": TABLE_ROW, "table": tref,
                                     "row_id": shown, "table_item_id": rec["item_id"],
                                     "rules_source": cfg.source, "at": now_iso()})
    ledgers["governance"].write({"item_id": rec["item_id"], "uri": rec["uri"],
                                 "sha256": rec["sha256"], "kind": rec.get("kind"),
                                 "rules_source": cfg.source, "at": now_iso(),
                                 **result.as_record(),
                                 # what the decision covers: the metadata; the rows one by one
                                 "screened": "metadata; rows one by one",
                                 "rows_screened": len(rows), "rows_held": held})


def _screen(extracted: list[dict], cfg, overrides: set[str], ledgers: dict, progress,
            timings: Optional[Timings] = None, *, source_id: Optional[str] = None,
            row_verdicts: Optional[dict] = None) -> None:
    timings = timings or Timings()
    fingerprint = screen_fingerprint(cfg)
    for i, rec in enumerate(extracted, 1):
        t0 = time.perf_counter()
        if rec.get("kind") == "table" and source_id is not None:
            _screen_table(rec, cfg, overrides, ledgers, progress, source_id=source_id,
                          verdicts=row_verdicts)
            timings.add("screen.table", time.perf_counter() - t0)
            progress("screen", done=i, total=len(extracted))
            continue
        # f2 #5: screened = extracted; a text that changed since raises (the stage fails loudly)
        text = capture.get_text(rec["sha256"], expect_text_digest=rec.get("text_sha256")) or ""
        override = _override_allows(overrides, rec["item_id"], rec["sha256"])
        # an email's extracted text is its DECODED preferred body: the injection hold also reads
        # the captured MIME source (an HTML-only hidden span or comment is not in that body)
        also = ([capture.get_bytes(rec["sha256"]).decode("utf-8", errors="replace")]
                if rec.get("extractor") == "email" else [])
        result = screen_text(text, cfg, override_allow=override, also=also)
        masked: dict = {}
        if rec.get("kind") != "table":
            # Codex f1 #8: the masked view is keyed by (content, rule set) and written once; its
            # digest goes into THIS decision, so the build reads exactly what was screened here
            variant = capture.redaction_variant(fingerprint, override)
            _, digest = capture.put_redacted_variant(rec["sha256"], variant, result.masked_text)
            capture.put_redacted(rec["sha256"], result.masked_text)   # shared §5.2 copy
            masked = {"redaction_variant": variant, "masked_sha256": digest}
        timings.add("screen.document", time.perf_counter() - t0)
        if i % CHECKPOINT_ITEMS == 0:
            progress("screen", done=i, total=len(extracted))
        ledgers["governance"].write({"item_id": rec["item_id"], "uri": rec["uri"],
                                     "sha256": rec["sha256"], "kind": rec.get("kind"),
                                     "rules_source": cfg.source, "at": now_iso(),
                                     **masked, **result.as_record()})
    progress("screen", done=len(extracted))


class _Now:
    """A completed future, for the serial path. Like a pool future, a failure is held and raised
    by `result()` (FIN-01: where the item's failure is isolated), never at submission."""

    def __init__(self, fn, *args) -> None:
        self._error: Optional[BaseException] = None
        self._value = None
        try:
            self._value = fn(*args)
        except Exception as exc:                                     # noqa: BLE001
            self._error = exc

    def result(self):
        if self._error is not None:
            raise self._error
        return self._value


#: FIN-01: an item whose model extraction fails is recorded and skipped; this many items failing
#: IN A ROW means the model (not one document) is the problem, and the run stops.
EXTRACT_FAILED = "extract_failed"
MAX_CONSECUTIVE_FAILURES = 5


def _max_consecutive_failures() -> int:
    try:
        return max(1, int(os.environ.get("WALNUT_EXTRACT_MAX_CONSECUTIVE_FAILURES", "")))
    except ValueError:
        return MAX_CONSECUTIVE_FAILURES


class ExtractionStalled(RuntimeError):
    """Every one of the last N items failed model extraction: the run stops, saying so."""


def _masked_for_build(rec: dict, raw: str, decision: dict, screen_cfg,
                      override_allow: bool) -> Optional[str]:
    """The masked text THIS run's screen decision recorded (Codex f1 #8): the rule-set-keyed,
    write-once variant, verified against the decision's `masked_sha256`. If that file is missing
    or altered, the text is re-masked with the run's own rules and accepted only if it reproduces
    the recorded digest. None: nothing verifiable - the caller skips the item (no model call).
    A decision written before digests (no `masked_sha256`) is re-masked under this run's rules."""
    sha = rec["sha256"]
    variant, want = decision.get("redaction_variant"), decision.get("masked_sha256")
    if variant and want:
        try:
            return capture.get_redacted_variant(sha, variant, expect_digest=want)
        except capture.CaptureCorrupt:
            pass
    masked = screen_text(raw, screen_cfg, override_allow=override_allow).masked_text
    if want and capture.sha256_bytes(masked.encode("utf-8")) != want:
        return None
    return masked


def _build(conn, store, harness, vocab, doc_kind, screen_cfg, extracted, decisions, state,
           ledgers, run_dir, progress, checkpoint, sources_root, report, *,
           timings: Optional[Timings] = None, workers: int = 1,
           bridges: bool = True, overrides=None,
           retrieved_at: Optional[dt.datetime] = None,
           row_verdicts: Optional[dict] = None) -> list[dict]:
    """Documents: prepare (chunk + screen) → extract on a pool of `workers` → apply in order,
    one transaction per document. Items whose screen decision is not `allow` never reach this
    loop's model calls. Tables: the approved mapping, deterministically (no model per row)."""
    assert_may_build("the build stage")
    from .mapping import ensure_mapping

    timings = timings or Timings()
    already = _done_ids(run_dir, "build", "build_skipped")
    held = held_items(run_dir)
    if overrides is None:                       # a direct caller; the run passes its bound set
        overrides = load_overrides(conn.id, sources_root)
    texts = {r["item_id"]: r.get("text_sha256") for r in read_jsonl(run_dir / "extracted.jsonl")
             if r.get("item_id")}
    rels = {r["item_id"]: r.get("rel") for r in read_jsonl(run_dir / "inventory.jsonl")
            if r.get("item_id")}
    caps = {r["item_id"]: r for r in read_jsonl(run_dir / "captured.jsonl") if r.get("item_id")}
    builder = ProseBuilder(store, harness, source_id=conn.id, vocab=vocab, doc_kind=doc_kind,
                           screen=screen_cfg, chunk_ledger=ledgers["governance_chunks"],
                           bridges=bridges, run_id=run_dir.name)
    built: list[dict] = []
    # Codex f1 #6: a build line carries its OUTBOX - the event it owes and the state it advances -
    # written in the same line, AFTER the item's graph transaction. A resume replays the outboxes
    # of the items it skips, so a crash between the graph write and `save_state` neither loses
    # their state nor their events (and item event ids are deterministic, so none is doubled).
    for r in (*read_jsonl(run_dir / "build.jsonl"), *read_jsonl(run_dir / "build_skipped.jsonl")):
        box = r.get("outbox")
        if isinstance(box, dict) and r.get("item_id"):
            if isinstance(box.get("event"), dict):
                built.append(box["event"])
            if isinstance(box.get("state"), dict):
                state[r["item_id"]] = _provisional(box["state"])
    tables: list[tuple[dict, str, dict, dict]] = []
    not_built: list[tuple[dict, str]] = []          # captured tables the screen kept out
    retrieved = retrieved_at or dt.datetime.now(dt.timezone.utc)
    pool = (ThreadPoolExecutor(max_workers=workers, thread_name_prefix="extract")
            if workers > 1 and harness is not None else None)

    def submit(plan, job):
        return pool.submit(builder.extract, plan, job) if pool else _Now(builder.extract, plan,
                                                                          job)

    streak = {"failed": 0, "last": ""}
    max_streak = _max_consecutive_failures()

    def finish(i: int, rec: dict, meta: dict, plan, futures: list, t_prep: float) -> None:
        iid = rec["item_id"]
        t_wait = time.perf_counter()
        try:
            results = [f.result() for f in futures]
            more = builder.bridge_jobs(plan, results)
            if more:
                plan.jobs.extend(more)
                results += [f.result() for f in [submit(plan, j) for j in more]]
        except Exception as exc:                                     # noqa: BLE001
            # FIN-01: ONE document the model cannot read (it returned nothing, or output that
            # failed validation after the harness's bounded retries) never fails the source.
            # The item is recorded with its reason, nothing of it is written to the graph, and
            # its state is NOT advanced, so the next sync tries it again. Retries stay the
            # harness's own ladder - no item-level loop on top of it.
            timings.add("build.model_wait", time.perf_counter() - t_wait)
            why = _plain_error(exc, limit=500)
            ledgers["build_skipped"].write({
                "item_id": iid, "uri": rec["uri"], "reason": EXTRACT_FAILED,
                "stage": "entity_extraction", "detail": why, "model_jobs": len(plan.jobs),
                "reverse": ("re-run the source (the item is retried; its state was not "
                            "advanced); if it keeps failing, inspect the item - its content "
                            "may be adversarial or unreadable to the model")})
            streak["failed"] += 1
            streak["last"] = why
            if streak["failed"] >= max_streak:
                raise ExtractionStalled(
                    f"{streak['failed']} consecutive item(s) failed model extraction - the model, "
                    f"not one document, looks unavailable; stopping the run. Last error: "
                    f"{why}") from exc
            if i % CHECKPOINT_ITEMS == 0:
                progress("build", done=i, total=len(extracted))
            return
        streak["failed"] = 0
        timings.add("build.model_wait", time.perf_counter() - t_wait)
        out = builder.apply(plan, results)
        timings.add("build.graph_write", out.write_ms / 1000.0)
        timings.item(iid, "build", t_prep + out.write_ms / 1000.0 + out.model_ms / 1000.0,
                     prepare_ms=round(t_prep * 1000, 2), model_ms=round(out.model_ms, 2),
                     write_ms=round(out.write_ms, 2), chunks=out.chunks,
                     model_calls=out.model_called, model_cached=out.model_cached,
                     bridges=out.bridges)
        for p in out.pointers:
            ledgers["pointers"].write({"item_id": iid, **p})
        for r in out.refused:
            # §5.8 (E2E F-33): the refused claim's producer — the model stamps of this item, or
            # this package when no model produced anything
            ledgers["refused"].write({"item_id": iid, "reason": r,
                                      "produced_by": list(out.produced_by) or [INGEST_PRODUCER]})
        entry = {"item_id": iid, "uri": rec["uri"], "sha256": rec["sha256"], "kind": "document",
                 # f1 #7: the capture's metadata anchor travels on to indexing and verification
                 **({"meta_digest": rec["meta_digest"]} if rec.get("meta_digest") else {}),
                 "anchor": out.anchor_id, "chunks": out.chunks,
                 "chunks_blocked": out.chunks_blocked, "entities": out.entities,
                 "relations": out.relations, "anchor_edges": out.anchor_edges,
                 "model_calls": out.model_called, "bridges": out.bridges,
                 "bridge_relations": out.bridge_relations, "produced_by": out.produced_by,
                 "subject_mentions": out.subject_mentions,
                 "subject_candidates": out.subject_candidates,
                 # E2E run-3 P-05: mentions joined to a keyed record by its grounded name
                 "name_joins": out.name_joins,
                 # f2 #6: every graph row this item wrote, each verified to carry a pointer
                 "graph_ids": _graph_ids(out)}
        event = {**entry, "name": rec.get("name"),
                 # f2 #12: the mime THIS source captured it as, not the first sighting's
                 "mime": (caps.get(iid) or {}).get("mime") or meta.get("mime"),
                 "replaced_hash": (state.get(iid) or {}).get("content_hash")}
        prior_kind = (state.get(iid) or {}).get("anchor_kind")
        if prior_kind and prior_kind != doc_kind:
            # R-029 TIDY2: rebuilt under another anchor kind - the anchor it had is closed (kept
            # as history), never left open beside the new one
            close_prior_anchor(store, source_id=conn.id, item_id=iid, kind=prior_kind)
        new_state = {"content_hash": rec["sha256"], "uri": rec["uri"], "rel": rels.get(iid),
                     "run_id": run_dir.name, "built_at": now_iso(),
                     # R-029 TIDY2: the anchor kind it was built under (None: no anchor written)
                     "anchor_kind": doc_kind}
        ledgers["build"].write({**entry, "outbox": {"event": event, "state": new_state}})
        if out.model_called and not out.entities:
            ledgers["build_skipped"].write({"item_id": iid, "uri": rec["uri"],
                                            "reason": "zero_graph_output",
                                            "detail": f"{out.model_called} chunk(s) sent to the "
                                                      f"model; nothing survived the wall"})
        elif out.chunks and out.chunks == out.chunks_blocked:
            ledgers["build_skipped"].write({"item_id": iid, "uri": rec["uri"],
                                            "reason": "all_chunks_screened_out"})
        built.append(event)
        state[iid] = _provisional(new_state)
        if checkpoint.tick({"stage": "build", "done": i}):
            save_state(conn.id, state, run_id=run_dir.name)
        if i % CHECKPOINT_ITEMS == 0:
            progress("build", done=i, total=len(extracted))

    # documents in flight at once: enough to keep every worker busy, bounded for memory
    window = max(2, workers * 2)
    pending: list[tuple] = []
    try:
        for i, rec in enumerate(extracted, 1):
            iid = rec["item_id"]
            if iid in already:
                continue
            decision = decisions.get(iid, {}).get("decision")
            if decision != "allow":
                if rec.get("kind") == "table":
                    # counted in completeness: its rows are not in the graph (never silent)
                    not_built.append((rec, f"screen_{decision or 'undecided'}"))
                continue                          # excluded by the screen; governance says why
            if iid in held:
                # captured, NOT built: a pending shape drift holds it. The state is not advanced,
                # so once the hold is released the next sync builds it (never "unchanged").
                h = held[iid]
                if rec.get("kind") == "table":
                    not_built.append((rec, DRIFT_PENDING_APPROVAL))
                ledgers["build_skipped"].write({
                    "item_id": iid, "uri": rec["uri"], "reason": DRIFT_PENDING_APPROVAL,
                    "detail": (f"held by adaptation {h.get('adaptation_id')}: the source's shape "
                               f"changed here and a person has not decided yet"),
                    "reverse": h.get("reverse") or "", "adaptation_id": h.get("adaptation_id")})
                continue
            if rec.get("kind") == "table":
                snapshot = json.loads(capture.get_bytes(rec["sha256"]))
                tables.append((snapshot, rec["sha256"], rec, {}))
                continue
            t0 = time.perf_counter()
            try:
                # f2 #5: the text the pointers will index is the one this run EXTRACTED
                raw = capture.get_text(rec["sha256"], expect_text_digest=texts.get(iid)
                                       or rec.get("text_sha256")) or ""
            except capture.CaptureCorrupt as exc:
                ledgers["build_skipped"].write({
                    "item_id": iid, "uri": rec["uri"], "reason": "text_mismatch",
                    "detail": _plain_error(exc), "reverse": "start a new run (it re-extracts)"})
                continue
            masked = _masked_for_build(rec, raw, decisions.get(iid) or {}, screen_cfg,
                                       _override_allows(overrides, iid, rec["sha256"]))
            if masked is None:
                ledgers["build_skipped"].write({
                    "item_id": iid, "uri": rec["uri"], "reason": "masked_text_mismatch",
                    "detail": "the masked text on disk is not the one this run's screen decision "
                              "recorded, and re-masking does not reproduce it - never sent to a "
                              "model", "reverse": "start a new run (it re-screens)"})
                continue
            meta = capture.get_meta(rec["sha256"],
                                    expect_digest=rec.get("meta_digest")) or {}
            plan = builder.prepare(item_id=iid, sha=rec["sha256"], raw_text=raw,
                                   masked_text=masked,
                                   title=rec.get("name") or meta.get("title") or rec["uri"],
                                   uri=rec["uri"], retrieved_at=retrieved,
                                   override_allow=_override_allows(overrides, iid,
                                                                   rec["sha256"]))
            t_prep = time.perf_counter() - t0
            timings.add("build.prepare_and_chunk_screen", t_prep)
            pending.append((i, rec, meta, plan, [submit(plan, j) for j in plan.jobs], t_prep))
            while len(pending) > window:
                finish(*pending.pop(0))
        while pending:
            finish(*pending.pop(0))
    finally:
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)

    if tables:
        t_tab = time.perf_counter()
        snaps = {(f"{s.get('schema')}.{s.get('table')}" if s.get("schema")
                  else str(s.get("table"))): s for s, _, _, _ in tables}
        # Codex f2 #2: a connection flag is never an approval - always False here; a table is
        # built only under an authenticated principal's digest-bound approval (mapping.py)
        # R-029 F-01: a row the screen held never reaches the mapper
        mapping = ensure_mapping(conn.id, {t: _without_held_rows(sn, conn.id, decisions)
                                           for t, sn in snaps.items()},
                                 vocab, harness=harness, auto_approve=False, root=sources_root)
        # C-3: links no foreign key declares, measured against this source's and the other
        # sources' captured keys — PROPOSED in the mapping for a person; nothing is built from a
        # proposal. Never fatal: a failure is noted and the build goes on.
        # E2E run 5 F-11: measured over EVERY table this run captured (an unchanged one too),
        # so a link is proposed whichever source or table was built first
        _refresh_links(conn.id, _own_table_snapshots(conn.id, caps), vocab, sources_root,
                       report)
        # Codex f2 #3: the run is BOUND to the approved mapping it first built tables under. The
        # binding is written BEFORE any table row is written; a later attempt of the same run
        # under a different approved mapping is refused, never mixed. The snapshot is written once.
        _bind_mapping(run_dir, mapping)
        snap_path = run_dir / "mapping.snapshot.yaml"
        if not snap_path.is_file():
            _atomic_text(snap_path, yaml.safe_dump(mapping.model_dump(mode="json"),
                                                   sort_keys=False))
        ready = []
        # every captured table of this run (and of each source an approved link names): what a
        # reference may point into, and what completeness counts a reference against
        context = _reference_context(conn.id, run_dir, caps, mapping)
        for snapshot, sha, rec, _ in tables:
            tref = (f"{snapshot.get('schema')}.{snapshot.get('table')}"
                    if snapshot.get("schema") else str(snapshot.get("table")))
            tm = mapping.tables.get(tref)
            if tm is None or tm.status != "approved" or not (tm.node or tm.association):
                reason = ("unmapped" if tm is not None and tm.status == "unmapped"
                          else "mapping_not_approved")
                skipped_line = ({
                    "item_id": rec["item_id"], "uri": rec["uri"], "reason": reason,
                    "detail": "; ".join(tm.rationale) if tm else "",
                    "reverse": ("approve the mapping (`approve --mapping`) and re-run"
                                if reason == "mapping_not_approved"
                                else "map the table to a kind in <source>.mapping.yaml")})
                # E2E F-23: the table's content is SEEN even though its rows are not (yet) in
                # the graph — a change is still an item_changed event (attrs.graph = false), and
                # the state remembers the hash WITHOUT marking it built, so the day its mapping
                # is approved the next sync builds it.
                prev = state.get(rec["item_id"]) or {}
                event = None
                if prev.get("content_hash") != sha:
                    event = {"item_id": rec["item_id"], "uri": rec["uri"], "sha256": sha,
                             "kind": "table", "name": snapshot.get("table"),
                             "mime": "application/json",
                             "replaced_hash": prev.get("content_hash"),
                             "graph": False, "reason": reason}
                    built.append(event)
                new_state = {"content_hash": sha, "uri": rec["uri"],
                             "rel": rels.get(rec["item_id"]), "run_id": run_dir.name,
                             "built_at": None, "graph": False, "reason": reason}
                state[rec["item_id"]] = new_state
                skipped_line["outbox"] = {"event": event, "state": new_state}
                ledgers["build_skipped"].write(skipped_line)
                _write_completeness(run_dir, unbuilt_completeness(
                    snapshot, source_id=conn.id, snaps=context, reason=reason,
                    node_kind=(tm.node or {}).get("kind") if tm is not None else None))
                continue
            ready.append((snapshot, sha, tm.model_dump(), rec))
        if ready:
            from .mapping import mapping_digest, split_kinds

            result = build_tables(store, vocab, source_id=conn.id,
                                  tables=[(s, sha, tm) for s, sha, tm, _ in ready],
                                  retrieved_at=retrieved, mask_cfg=screen_cfg,
                                  screen_cfg=screen_cfg, overrides=overrides,
                                  item_ids={sha: rec["item_id"] for _, sha, _, rec in ready},
                                  context=context,
                                  # FIN-04: a reference into a split table meets the row under
                                  # the kind the approved split built it as
                                  kind_splits={t: tm.split for t, tm in mapping.tables.items()
                                               if tm.status == "approved" and tm.split},
                                  row_verdicts=row_verdicts,
                                  progress=lambda **kw: progress("build", **kw))
            # E2E run 5 F-03: what each table still waits for — references whose target row
            # is not in the graph yet, and rows held for a person — so the day either changes
            # the table is rebuilt (`_rebuild_for_references`), never left "unchanged"
            waits = _table_waits(conn.id, result, ready, state, run_dir.name)
            for rec_c in result.get("completeness") or []:
                _write_completeness(run_dir, rec_c)
            for p in result["pointers"]:
                ledgers["pointers"].write(p)
            for b in result["blocked_rows"]:
                ledgers["governance_chunks"].write({"item_id": None, **b})
            # E2E F-03 / F-19: a subject row whose declared identifier equals another record's
            # is a CANDIDATE for a human (never merged); listed here with its reversal
            if result.get("candidate_links"):
                with open(run_dir / "identity_candidates.jsonl", "a", encoding="utf-8") as fh:
                    for c in result["candidate_links"]:
                        fh.write(json.dumps(c, sort_keys=True, default=str) + "\n")
            report.notes.append(
                f"tables: {result['nodes']} nodes, {result['edges']} edges, "
                f"{result['dangling_edges']} dangling edges dropped (counted), "
                f"{result['rows_blocked']} rows blocked by the screen, "
                f"{result['rows_review']} rows held for a person (each liftable on its own), "
                f"{len(result['redactions'])} cells masked, "
                f"{len(result.get('canonical_masked') or [])} canonical id(s) withheld because "
                f"the value was masked (those rows merge by row key only), "
                f"{len(result.get('merged') or [])} row node(s) merged onto an existing node by "
                f"exact id, {len(result.get('candidate_links') or [])} identity candidate(s) "
                f"listed for a human")
            per_table_nodes = result["report"].get("nodes_by_kind", {})
            table_ids: dict[str, set] = {}
            for ptr in result["pointers"]:
                if ptr.get("item_id") and ptr.get("entity"):
                    table_ids.setdefault(ptr["item_id"], set()).add(ptr["entity"])
            for snapshot, sha, tm, rec in ready:
                entry = {"item_id": rec["item_id"], "uri": rec["uri"], "sha256": sha,
                         "kind": "table", "rows": len(snapshot.get("rows") or []),
                         "node_kind": (tm.get("node") or {}).get("kind"),
                         "nodes_of_kind_this_run": sum(per_table_nodes.get(k, 0)
                                                       for k in split_kinds(tm)),
                         **({"node_kinds": split_kinds(tm)} if tm.get("split") else {}),
                         **({"meta_digest": rec["meta_digest"]} if rec.get("meta_digest")
                            else {}),
                         "graph_ids": sorted(table_ids.get(rec["item_id"], ()))}
                event = {**entry, "name": snapshot.get("table"), "mime": "application/json",
                         "replaced_hash": (state.get(rec["item_id"]) or {}).get("content_hash")}
                new_state = {"content_hash": sha, "uri": rec["uri"],
                             "rel": rels.get(rec["item_id"]), "run_id": run_dir.name,
                             "built_at": now_iso(), **waits.get(rec["item_id"], {}),
                             # F-11: the approved mapping it was built under
                             "mapping_digest": mapping_digest(mapping.tables[rec["uri"]])
                             if rec["uri"] in mapping.tables else None}
                ledgers["build"].write({**entry, "outbox": {"event": event, "state": new_state}})
                if not snapshot.get("rows"):
                    ledgers["build_skipped"].write({"item_id": rec["item_id"],
                                                    "uri": rec["uri"], "reason": "no_rows"})
                built.append(event)
                state[rec["item_id"]] = _provisional(new_state)
        timings.add("build.tables", time.perf_counter() - t_tab)
    elif any(c.get("kind") == "table" for c in caps.values()):
        # nothing to build this run: links are still measured against every captured table
        _refresh_links(conn.id, _own_table_snapshots(conn.id, caps), vocab, sources_root,
                       report)
    _carry_completeness(conn.id, run_dir, caps, state)
    if not_built:
        from .mapping import load_mapping

        ctx = _reference_context(conn.id, run_dir, caps, load_mapping(conn.id, sources_root))
        for rec, reason in not_built:
            try:
                snapshot = json.loads(capture.get_bytes(rec["sha256"]))
            except Exception:                                        # noqa: BLE001
                continue
            _write_completeness(run_dir, unbuilt_completeness(
                snapshot, source_id=conn.id, snaps=ctx, reason=reason))
    progress("build", done=len(extracted), total=len(extracted))
    return built


# --- capture-only discovery (FEAT8, Codex R-027 AAX f1 #1, #5, #8) -------------------------------
#: `binding.json` mode of a discovery run (a build run's binding carries no mode)
DISCOVERY_MODE = "discover"
#: the discovery's manifest, written by the run itself
DISCOVERY_FILE = "discovery.json"
#: the report status of a discovery that captured something and verified. Deliberately NOT `ok`:
#: a discovery is no sync, so nothing that reads `ok` as a successful sync or as freshness
#: evidence can mistake one for it.
DISCOVERED = "discovered"
#: the `build_skipped` reason of an allowed item a discovery captured and screened but never built
DISCOVERY_ONLY = "discovery_capture_only"


def plan_digest(plan) -> str:
    """The digest a run binds its plan by (`binding.json` `plan`): what a discovery is tied to."""
    return _digest(plan.model_dump(mode="json"))


def _table_ref(snapshot: dict) -> str:
    return (f"{snapshot.get('schema')}.{snapshot.get('table')}" if snapshot.get("schema")
            else str(snapshot.get("table")))


def _discover(conn, vocab, harness, captured: list[dict], decisions: dict, ledgers: dict,
              run_dir: Path, sources_root, report: RunReport) -> dict:
    """The discovery stage: every captured item is accounted for (an allowed one is recorded
    `discovery_capture_only` in `build_skipped`, so verification still balances), and the table
    mapping is PROPOSED from the captured snapshots the screen did not block - the same
    `ensure_mapping` (never approving) and link proposals a build's first run makes. Returns what
    `discovery.json` records: per table its capture digest, screen decision and the digest of the
    mapping entry proposed from it; per document its capture digest and decision. Writes nothing
    to a graph.

    Codex R-027 AAX f2 #3: only material the screen ALLOWED (or an earlier sync built) reaches the
    mapper with its rows and a model. A table the screen holds for REVIEW is proposed from its
    SCHEMA alone (`schema_only`: names, types and keys, no row) and with no model at all; link
    proposals, which measure values, read allowed tables only."""
    from .mapping import ensure_mapping, mapping_digest

    held = held_items(run_dir)
    unchanged = {r["item_id"] for r in read_jsonl(run_dir / "unchanged.jsonl")
                 if r.get("item_id") and not r.get("decided")}
    # R-029 TIDY2: a document is NAMED in the manifest (the setup page shows it) by its path
    # relative to the source's root, never by the server's absolute path
    rels = {r["item_id"]: r.get("rel") for r in read_jsonl(run_dir / "inventory.jsonl")
            if r.get("item_id")}
    tables: dict[str, dict] = {}
    snaps: dict[str, dict] = {}
    held_snaps: dict[str, dict] = {}
    docs: dict = {"captured": 0, "allowed": 0, "items": []}
    for rec in captured:
        iid, sha = rec.get("item_id"), rec.get("sha256")
        if not iid or not sha:
            continue
        decision = (decisions.get(iid) or {}).get("decision")
        if decision is None and iid in unchanged:
            decision = "unchanged"                     # built and verified by an earlier sync
        if decision == "allow":
            ledgers["build_skipped"].write({
                "item_id": iid, "uri": rec.get("uri"), "reason": DISCOVERY_ONLY,
                "detail": "captured and screened by a capture-only discovery; nothing is built "
                          "until a person approves the mapping and hands the source off",
                "reverse": "hand the source off (or run it from Sources) to build it"})
        if rec.get("kind") != "table":
            docs["captured"] += 1
            docs["allowed"] += decision == "allow"
            docs["items"].append({"item_id": iid, "sha256": sha, "decision": decision,
                                  "held": iid in held,
                                  "uri": rels.get(iid) or public_text(rec.get("uri") or "")})
            continue
        try:
            snapshot = json.loads(capture.get_bytes(sha))
        except Exception as exc:                                     # noqa: BLE001
            report.notes.append(f"discovery: capture {sha[:12]} unreadable ({_plain_error(exc)})")
            continue
        ref = _table_ref(snapshot)
        tables[ref] = {"item_id": iid, "sha256": sha, "decision": decision,
                       "held": iid in held, "rows": len(snapshot.get("rows") or [])}
        if iid in held:
            continue
        if decision in ("allow", "unchanged"):
            # R-029 F-01: a row the screen held (its own decision) never reaches the mapper
            snaps[ref] = _without_held_rows(snapshot, conn.id, decisions)
            tables[ref]["rows_held"] = len(snapshot.get("rows") or []) - len(snaps[ref]["rows"])
        elif decision == "review":
            # held for review: its mapping is metadata a person decides, so it is still proposed
            # - from the schema only, never its rows, and never by a model (f2 #3)
            held_snaps[ref] = schema_only(snapshot)
    mapping = None
    if snaps:
        mapping = ensure_mapping(conn.id, snaps, vocab, harness=harness, auto_approve=False,
                                 root=sources_root)
        try:
            from .mapping import refresh_link_proposals

            refresh_link_proposals(conn.id, snaps, vocab, root=sources_root)
        except Exception as exc:                                     # noqa: BLE001
            report.notes.append(f"links: proposal skipped ({_plain_error(exc)})")
    if held_snaps:
        mapping = ensure_mapping(conn.id, held_snaps, vocab, harness=None, auto_approve=False,
                                 root=sources_root)
    for ref in (*snaps, *held_snaps):
        tm = mapping.tables.get(ref) if mapping is not None else None
        if tm is not None:
            tables[ref]["mapping_digest"] = mapping_digest(tm)
            tables[ref]["mapping_status"] = tm.status
    return {"tables": tables, "documents": docs}


#: the keys of a table snapshot that carry no row value (`schema_only`)
_SCHEMA_KEYS = ("schema", "table", "columns", "primary_key", "foreign_keys")


def _without_held_rows(snapshot: dict, source_id: str, decisions: dict) -> dict:
    """R-029 F-01: the snapshot without the rows this run's screen did not allow (each a
    `table_row` decision keyed by its `row_ref`): what the mapper may read of an allowed table."""
    from .build import _render_pk
    from .screen import row_ref

    held = {i for i, d in decisions.items()
            if (d or {}).get("kind") == TABLE_ROW and (d or {}).get("decision") != "allow"}
    if not held:
        return snapshot
    tref = _table_ref(snapshot)
    pk = list(snapshot.get("primary_key") or [])
    keep = []
    for ordinal, row in enumerate(snapshot.get("rows") or []):
        key = _render_pk(row, pk)
        if row_ref(source_id, tref, key if key is not None else f"#{ordinal}") not in held:
            keep.append(row)
    return {**snapshot, "rows": keep}


def schema_only(snapshot: dict) -> dict:
    """A table snapshot with every row value removed: names, types and declared keys only. What
    the mapper may see of material the screen has not allowed (Codex R-027 AAX f2 #3)."""
    out = {k: snapshot.get(k) for k in _SCHEMA_KEYS if k in snapshot}
    out["columns"] = [{"name": c.get("name"), "type": c.get("type")}
                      for c in snapshot.get("columns") or [] if isinstance(c, dict)]
    out["rows"] = []
    return out


def _seal_discovery(report: RunReport, run_dir: Path, found: dict, plan) -> None:
    """Turn the finalised status into a discovery's (`ok` -> `discovered`; anything else stands)
    and write `discovery.json`. The report written after it is the authority on the status."""
    if report.status == "ok":
        report.status = DISCOVERED
    report.message = ("DISCOVERY (capture only, nothing built): " + (report.message or ""))[:2000]
    report.plan = {**report.plan, "mode": DISCOVERY_MODE}
    report.notes.append("discovery: capture-only - collect, extract, screen and mapping proposal; "
                        "no graph store was opened, nothing was built or indexed, the source's "
                        "state was not advanced and no run event was emitted")
    try:
        started_ns = json.loads((run_dir / DISCOVERY_FILE).read_text(encoding="utf-8")
                                ).get("started_ns")
    except (OSError, ValueError):
        started_ns = None
    manifest = {"mode": DISCOVERY_MODE, "source_id": report.source_id, "run_id": report.run_id,
                "started_ns": started_ns,
                "plan_digest": plan_digest(plan), "status": report.status,
                "tables": found.get("tables") or {}, "documents": found.get("documents") or {},
                "built": 0, "at": now_iso()}
    _atomic_text(run_dir / DISCOVERY_FILE, json.dumps(manifest, sort_keys=True, indent=1,
                                                      default=str))


def latest_discovery(source_id: str) -> Optional[dict]:
    """The NEWEST capture-only discovery of the source, or None. Its `status` is read from the
    run's own `report.json` (the authority; `incomplete` without one), never from the manifest
    alone, and a manifest naming another run or source is reported `invalid`. A gate accepts it
    only when `status == DISCOVERED` and its `plan_digest` is the current plan's."""
    base = source_dir(source_id)
    if not base.is_dir():
        return None
    found: list[tuple[int, str, Path, Optional[dict]]] = []
    for d in (x for x in base.iterdir() if x.is_dir()):
        man_path = d / DISCOVERY_FILE
        if not man_path.is_file():
            continue
        try:
            man = json.loads(man_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            man = None
        # newest first by the moment the run started (run ids of one second do not sort)
        found.append((int((man or {}).get("started_ns") or 0), d.name, d, man))
    for _, _, d, man in sorted(found, key=lambda x: (x[0], x[1]), reverse=True):
        if man is None:
            return {"run_id": d.name, "status": "invalid", "tables": {},
                    "message": "discovery.json is unreadable"}
        try:
            rep = json.loads((d / "report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rep = {}
        status = rep.get("status") or "incomplete"
        if man.get("run_id") != d.name or man.get("source_id") != source_id or                 man.get("mode") != DISCOVERY_MODE or rep.get("run_id") not in (None, d.name):
            status = "invalid"
        return {**man, "run_id": d.name, "status": status,
                "message": rep.get("message", ""), "finished_at": rep.get("finished_at")}
    return None


COMPLETENESS_LEDGER = "completeness.jsonl"
#: FIN-04: what the post-run identity consolidation did (`build.consolidate_identity_after_run`)
IDENTITY_FILE = "identity_consolidation.json"


def _reconciled_brief(rec: dict) -> Optional[dict]:
    """IDN cross-track 2: the post-run keyless reconciliation (`consolidate_identity_after_run`'s
    `reconciled`), in brief — a run that only reconciled still reports what it joined."""
    r = rec.get("reconciled")
    if not isinstance(r, dict):
        return None
    return {"status": r.get("status"), "merged": len(r.get("merged") or []),
            "absorbed": r.get("absorbed", 0), "refused": len(r.get("refused") or []),
            **({"error_ref": r["error_ref"]} if r.get("error_ref") else {})}


def _identity_note(rec: dict) -> str:
    r = _reconciled_brief(rec)
    tail = (f"; keyless mentions reconciled: {r['absorbed']} absorbed into {r['merged']} "
            f"record(s), {r['refused']} refused" if r and r.get("status") == "ran"
            else (f"; keyless reconciliation {r['status']}" if r else ""))
    return _identity_head(rec) + tail


def _identity_head(rec: dict) -> str:
    status = rec.get("status")
    if status == "ran":
        approved = {k: v for k, v in (rec.get("kinds") or {}).items() if v}
        if not approved:
            return ("identity: no identity rule is approved for this deployment; nothing was "
                    "merged (a named human approves a rule on the ontology)")
        return (f"identity: approved rules {approved} merged {rec.get('absorbed', 0)} record(s) "
                f"into {len(rec.get('merged') or [])} node(s); {len(rec.get('refused') or [])} "
                f"group(s) refused (listed in {IDENTITY_FILE})")
    if status == "no_rules":
        return "identity: the bound packs declare no identity rules; nothing to consolidate"
    return f"identity: consolidation {status} ({rec.get('error', '')}); nothing was merged"


def identity_summary(run_dir: Path) -> Optional[dict]:
    """The run's identity consolidation in brief (FIN-04), for `report.json → counts`."""
    p = Path(run_dir) / IDENTITY_FILE
    if not p.is_file():
        return None
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                                # noqa: BLE001
        return {"status": "unreadable"}
    return {"status": rec.get("status"), "kinds": rec.get("kinds") or {},
            "merged": len(rec.get("merged") or []), "absorbed": rec.get("absorbed", 0),
            "refused": len(rec.get("refused") or []),
            "merges": [{"kind": m.get("kind"), "survivor": m.get("survivor"),
                        "absorbed": m.get("absorbed"), "rules": m.get("rules")}
                       for m in rec.get("merged") or []],
            **({"reconciled": _reconciled_brief(rec)} if _reconciled_brief(rec) else {}),
            **({"error": rec["error"]} if rec.get("error") else {})}


def _write_completeness(run_dir: Path, rec: dict) -> None:
    """One table's completeness line (the 2026-10-02 dry run's lesson): rows at the source vs read vs
    built vs nodes, declared FKs vs edges, unresolved references. `verify_run` totals them into
    the run report (`counts.completeness`), so an E2E can assert on them."""
    with open(run_dir / COMPLETENESS_LEDGER, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True, default=str) + "\n")


def _own_table_snapshots(source_id: str, caps: dict) -> dict[str, dict]:
    """{table: snapshot} of every table this run captured (built, unchanged or held alike)."""
    return {ref: snap for (src, ref), snap in _captured_tables_of(source_id, caps).items()}


def _captured_tables_of(source_id: str, caps: dict) -> dict:
    out: dict = {}
    for cap in caps.values():
        if cap.get("kind") != "table" or not cap.get("sha256"):
            continue
        try:
            snap = json.loads(capture.get_bytes(cap["sha256"]))
        except Exception:                                            # noqa: BLE001
            continue
        if isinstance(snap, dict) and snap.get("table"):
            ref = (f"{snap.get('schema')}.{snap.get('table')}" if snap.get("schema")
                   else str(snap.get("table")))
            out[(source_id, ref)] = snap
    return out


def _refresh_links(source_id: str, snaps: dict[str, dict], vocab, sources_root,
                   report: "RunReport") -> None:
    """C-3 / F-11: propose links for this source's tables (never fatal: a failure is noted)."""
    if not snaps:
        return
    try:
        from .mapping import refresh_link_proposals

        m = refresh_link_proposals(source_id, snaps, vocab, root=sources_root)
        n_links = sum(1 for tm in m.tables.values() for lk in tm.links or []
                      if lk.get("status") == "proposed")
        if n_links:
            report.notes.append(f"links: {n_links} proposed link(s) where no foreign key is "
                                f"declared, awaiting a person (mapping links)")
    except Exception as exc:                                         # noqa: BLE001
        report.notes.append(f"links: proposal skipped ({_plain_error(exc)})")


def _refresh_links_elsewhere(source_id: str, vocab, sources_root, report: "RunReport") -> None:
    """E2E run 5 F-11, order independence ACROSS sources: once this run's tables are promoted, the
    columns of every OTHER ingest source are measured against them too — a link from a source
    that was built FIRST is proposed now, not on that source's next sync. Each source's mapping
    is written under its own lock, one at a time (never two mapping locks held together). Never
    fatal: a failure is noted."""
    from .connections import list_connections
    from .mapping import captured_tables, load_mapping, refresh_link_proposals

    try:
        conns = list_connections(sources_root)
    except Exception as exc:                                         # noqa: BLE001
        report.notes.append(f"links: other sources not measured ({_plain_error(exc)})")
        return
    touched = []
    for conn in conns:
        if conn.id == source_id or getattr(conn, "purpose", "ingest") != "ingest":
            continue
        try:
            before = load_mapping(conn.id, sources_root)
            if not any(tm.node for tm in before.tables.values()):
                continue
            snaps = captured_tables(conn.id)
            if not snaps:
                continue
            after = refresh_link_proposals(conn.id, snaps, vocab, root=sources_root)
        except Exception as exc:                                     # noqa: BLE001
            report.notes.append(f"links: {conn.id} not measured ({_plain_error(exc)})")
            continue

        def proposed(m) -> set:
            return {(t, lk.get("column"), lk.get("source"), lk.get("table"))
                    for t, tm in m.tables.items() for lk in tm.links or []
                    if lk.get("status") == "proposed"}

        new = proposed(after) - proposed(before)
        if new:
            touched.append(f"{conn.id} +{len(new)}")
    if touched:
        report.notes.append(f"links: newly proposed in other sources against this run's tables: "
                            f"{', '.join(touched)} (awaiting a person)")


def _table_waits(source_id: str, result: dict, ready: list, state: dict, run_id: str
                 ) -> dict[str, dict]:
    """E2E run 5 F-03 / F-01: {table item id: state fields} — what a built table still waits for.

    * `pending_refs`: each reference (declared FK or approved link) of which at least one edge
      DANGLED because its target row is not in the graph, with the target's state as this build
      saw it (`seen_run`, `seen_hash`; for a target built in THIS run, this run). When the target
      is rebuilt later — in this source or another — the table is rebuilt so the edges resolve.
    * `held_rows`: the [row_ref, row_digest] of each row the screen held for a person; when an
      owner lifts one, the table is rebuilt so the row (and the edges into it) reach the graph."""
    from .build import _snap_ref

    built_now = {_snap_ref(s): sha for s, sha, _, _ in ready}
    by_uri = {e.get("uri"): e for e in state.values() if isinstance(e, dict)}
    others: dict[str, dict] = {}
    held: dict[str, list] = {}
    for b in result.get("blocked_rows") or []:
        if b.get("decision") == "review" and b.get("row_ref") and b.get("row_digest"):
            held.setdefault(str(b.get("table")), []).append([b["row_ref"], b["row_digest"]])
    out: dict[str, dict] = {}
    for snapshot, _, _, rec in ready:
        tref = _snap_ref(snapshot)
        line = next((c for c in result.get("completeness") or [] if c.get("table") == tref), {})
        pending = []
        for fk in line.get("foreign_keys") or []:
            if not fk.get("edge_kind") or not int(fk.get("dangling") or 0):
                continue
            ref = str(fk.get("references") or "")
            src, _, tab = ref.partition(":") if fk.get("origin") == "approved_link" and                 ":" in ref else (source_id, "", ref)
            if src == source_id and tab in built_now:
                seen = {"seen_run": run_id, "seen_hash": built_now[tab]}
            else:
                if src == source_id:
                    cur = by_uri.get(tab) or {}
                else:
                    if src not in others:
                        others[src] = {e.get("uri"): e for e in load_state(src).values()
                                       if isinstance(e, dict)}
                    cur = others[src].get(tab) or {}
                seen = {"seen_run": cur.get("run_id") if cur.get("graph", True) is not False
                        else None, "seen_hash": cur.get("content_hash")}
            pending.append({"source": src, "table": tab, "column": fk.get("column"),
                            "dangling": int(fk.get("dangling") or 0), **seen})
        waits = {}
        if pending:
            waits["pending_refs"] = pending
        if held.get(tref):
            waits["held_rows"] = held[tref]
        if waits:
            out[rec["item_id"]] = waits
    return out


def _target_built_since(cur: Optional[dict], pending: dict) -> bool:
    """Was the referenced table BUILT (and verified) since the table holding the reference recorded it?"""
    if not cur or cur.get("graph", True) is False or not cur.get("built_at"):
        return False
    return (cur.get("run_id"), cur.get("content_hash")) != (pending.get("seen_run"),
                                                           pending.get("seen_hash"))


def _approved_digests(source_id: str, sources_root) -> dict[str, str]:
    """{table: digest} of the source's APPROVED table mappings now (empty when unreadable)."""
    try:
        from .mapping import load_mapping, mapping_digest

        return {t: mapping_digest(tm) for t, tm in load_mapping(source_id, sources_root)
                .tables.items() if tm.status == "approved"}
    except Exception:                                                # noqa: BLE001
        return {}


def _rebuild_for_references(captured: list[dict], state: dict, source_id: str, overrides,
                            approved: Optional[dict] = None) -> dict[str, str]:
    """E2E run 5 F-03: {item id: why} — UNCHANGED tables this run re-reads and rebuilds anyway.

    0. a table whose APPROVED mapping changed since it was built (`mapping_digest`: e.g. a
       person approved a link, F-11) — the new edges are built without waiting for new rows;
    1. a table one of whose held rows an owner has since lifted (`held_rows`, F-01);
    2. a table with `pending_refs` whose target is rebuilt in this run (its content changed, it
       was never built, or 1. applies to it), or was built since in any run — of this source
       or, for an approved link, of the other source.
    Order-independent: the whole run's captures are looked at before any item is decided, so the
    target may be captured before or after the table that references it."""
    tables = [r for r in captured if r.get("kind") == "table"]

    def unchanged(rec: dict) -> bool:
        prev = state.get(rec["item_id"]) or {}
        return prev.get("content_hash") == rec["sha256"] and prev.get("graph", True) is not False

    out: dict[str, str] = {}
    for rec in tables:
        prev = state.get(rec["item_id"]) or {}
        now = (approved or {}).get(rec["uri"])
        if unchanged(rec) and now and prev.get("mapping_digest") \
                and prev["mapping_digest"] != now:
            out[rec["item_id"]] = "its approved mapping changed since it was built"
            continue
        lifted = [r for r, d in prev.get("held_rows") or []
                  if _override_allows(overrides, r, d)]
        if unchanged(rec) and lifted:
            out[rec["item_id"]] = f"{len(lifted)} held row(s) lifted by an owner"
    moving = {r["uri"] for r in tables if not unchanged(r) or r["item_id"] in out}
    by_uri = {e.get("uri"): e for e in state.values() if isinstance(e, dict)}
    others: dict[str, dict] = {}
    for rec in tables:
        iid = rec["item_id"]
        if iid in out or not unchanged(rec):
            continue
        for p in _pending_refs_of(source_id, state.get(iid) or {}, rec["uri"]):
            src, tab = p.get("source") or source_id, p.get("table")
            if src == source_id:
                if tab in moving:
                    out[iid] = f"references {tab}, rebuilt this run"
                    break
                cur = by_uri.get(tab)
            else:
                if src not in others:
                    try:
                        others[src] = {e.get("uri"): e for e in load_state(src).values()
                                       if isinstance(e, dict)}
                    except Exception:                                # noqa: BLE001
                        others[src] = {}
                cur = others[src].get(tab)
            if _target_built_since(cur, p):
                out[iid] = f"references {src}:{tab}, built since"
                break
    return out


def _pending_refs_of(source_id: str, entry: dict, tref: str) -> list[dict]:
    """A built table's `pending_refs`. A table built BEFORE they were recorded (no
    `mapping_digest` in its state — e.g. the run-5 finance deployment) has them derived once from
    its last build's completeness record: every reference with dangling edges, target state
    unseen — so the next run that finds the target built rebuilds it (F-03, live states)."""
    if "pending_refs" in entry or "mapping_digest" in entry or not entry.get("run_id"):
        return list(entry.get("pending_refs") or [])
    try:
        lines = read_jsonl(run_dir_for(source_id, entry["run_id"]) / COMPLETENESS_LEDGER)
    except Exception:                                                # noqa: BLE001
        return []
    last = next((r for r in reversed(lines) if r.get("table") == tref), None) or {}
    out = []
    for fk in last.get("foreign_keys") or []:
        if not fk.get("edge_kind") or not int(fk.get("dangling") or 0):
            continue
        ref = str(fk.get("references") or "")
        src, _, tab = ref.partition(":") if fk.get("origin") == "approved_link" and ":" in ref \
            else (source_id, "", ref)
        out.append({"source": src, "table": tab, "column": fk.get("column"),
                    "dangling": int(fk["dangling"]), "seen_run": None, "seen_hash": None})
    return out


def _carry_completeness(source_id: str, run_dir: Path, caps: dict, state: dict) -> None:
    """E2E run 5 F-03: a table this run left UNCHANGED keeps its place in the run's completeness
    — its last build's record, marked `carried_forward` with the run it came from — so a run that
    built nothing still reports every table, and a gap a table still has (edges missing, rows
    held) never disappears from the report because nothing changed."""
    done = {r.get("table") for r in read_jsonl(run_dir / COMPLETENESS_LEDGER)}
    unchanged = {r["item_id"] for r in read_jsonl(run_dir / "unchanged.jsonl")
                 if r.get("item_id")}
    cache: dict[str, list[dict]] = {}
    for iid, cap in caps.items():
        if cap.get("kind") != "table" or iid not in unchanged:
            continue
        prev = state.get(iid) or {}
        rid, tref = prev.get("run_id"), cap.get("uri")
        if not rid or not tref or tref in done or rid == run_dir.name:
            continue
        if rid not in cache:
            cache[rid] = read_jsonl(run_dir_for(source_id, rid) / COMPLETENESS_LEDGER)
        last = next((r for r in reversed(cache[rid]) if r.get("table") == tref), None)
        if last is None:
            continue
        _write_completeness(run_dir, {**last, "carried_forward": True,
                                      "from_run": last.get("from_run") or rid})


def _reference_context(source_id: str, run_dir: Path, caps: dict, mapping) -> dict:
    """{(source, table): snapshot}: every table THIS run captured from the source, and the captured
    tables of each other source an approved link names (read-only, from their last verified
    state). What a reference is resolved and counted against."""
    out: dict = {}
    for cap in caps.values():
        if cap.get("kind") != "table" or not cap.get("sha256"):
            continue
        try:
            snap = json.loads(capture.get_bytes(cap["sha256"]))
        except Exception:                                            # noqa: BLE001
            continue
        if isinstance(snap, dict) and snap.get("table"):
            ref = (f"{snap.get('schema')}.{snap.get('table')}" if snap.get("schema")
                   else str(snap.get("table")))
            out[(source_id, ref)] = snap
    others = {(e.get("ref") or {}).get("source") for tm in mapping.tables.values()
              if tm.status == "approved" for e in tm.edges or []
              if (e.get("ref") or {}).get("source")}
    if others - {source_id}:
        from .mapping import captured_tables

        for other in sorted(others - {source_id}):
            for ref, snap in captured_tables(other).items():
                out[(other, ref)] = snap
    return out


def _graph_ids(out) -> list[str]:
    """Every node / edge id one document's build wrote: what `build.py` reports
    (`DocBuild.graph_ids`: anchor, entities, anchor edges, relations, bridge relations) plus the
    anchor and every entity a pointer was ledgered for. `verify_run` requires each to carry a
    resolving pointer (Codex f2 #6). INT5: read directly — FXJ2 exposes it on every DocBuild, so
    a build result without it is an error, never read as "wrote nothing"."""
    ids = set(out.graph_ids)
    if getattr(out, "anchor_id", None):
        ids.add(out.anchor_id)
    ids.update(p["entity"] for p in getattr(out, "pointers", None) or () if p.get("entity"))
    return sorted(ids)


def _emit_item_events(source_id: str, run_id: str, built: list[dict], prev_state_live: dict,
                      run_dir: Path, enumeration_complete: bool, report: RunReport, *,
                      store=None, doc_kind: Optional[str] = None, ledger=None,
                      holds: Optional[DriftHolds] = None, resuming: bool = False) -> None:
    """§5.3 events. `prev_state_live` has already been updated for built items, so the previous
    hash is recovered from the build's own record of what it replaced. A removed item's graph
    footprint is CLOSED (not deleted) before its event is emitted — see `close_removed_item`."""
    assert_may_build("item events and removals")
    prior = {r["item_id"]: r for r in read_jsonl(run_dir / "unchanged.jsonl")}
    counts = {"item_added": 0, "item_changed": 0, "item_removed": 0}
    closed_total = {"nodes": 0, "edges": 0}
    # Codex f1 #6: item event ids are deterministic; a resumed run skips the ones already logged
    from .ledger import event_ids_for_run

    emitted = event_ids_for_run(run_id) if resuming else set()
    for b in built:
        kind = "item_changed" if b.get("replaced_hash") else "item_added"
        attrs = {"mime": b.get("mime"), "title": b.get("name")}
        if b.get("graph") is False:
            attrs.update(graph=False, reason=b.get("reason"))
        eid = item_event_id(kind, source_id=source_id, run_id=run_id, item_id=b["item_id"],
                            content_hash=b["sha256"])
        counts[kind] += 1
        if eid in emitted:
            continue
        emit_event(kind, source_id=source_id, run_id=run_id, item_id=b["item_id"],
                   uri=b["uri"], content_hash=b["sha256"], attrs=attrs, event_id=eid)
        emitted.add(eid)
    if enumeration_complete:
        seen = {r["item_id"] for r in read_jsonl(run_dir / "inventory.jsonl") if r.get("item_id")}
        gone_ids = sorted(set(prev_state_live) - seen - set(prior))
        # an item a drift hold names (e.g. a table the source dropped, held until a person
        # decides) is NOT inferred removed: closing its graph footprint is the decision pending
        # Codex f1 #11: a path hold is a glob on the scope-RELATIVE path, so the state's `rel`
        # is what it is matched against (the uri only for a state entry written before `rel`)
        kept_held = [i for i in gone_ids if holds is not None and
                     holds.absent_hold(str(prev_state_live[i].get("uri") or ""),
                                       prev_state_live[i].get("rel"))]
        if kept_held:
            report.notes.append(f"drift holds: {len(kept_held)} item(s) absent from the source "
                                f"but held ({DRIFT_PENDING_APPROVAL}) — not inferred removed")
        for iid in [i for i in gone_ids if i not in kept_held]:
            gone = prev_state_live.pop(iid)
            attrs: dict = {}
            if store is not None:
                live_hashes = {v.get("content_hash") for v in prev_state_live.values()}
                closed = close_removed_item(store, source_id=source_id, item_id=iid,
                                            content_hash=gone.get("content_hash"),
                                            doc_kind=doc_kind, live_hashes=live_hashes)
                closed_total["nodes"] += len(closed["nodes_closed"])
                closed_total["edges"] += len(closed["edges_closed"])
                attrs = {"nodes_closed": len(closed["nodes_closed"]),
                         "edges_closed": len(closed["edges_closed"])}
                if ledger is not None:
                    ledger.write({"item_id": iid, "uri": gone.get("uri"),
                                  "content_hash": gone.get("content_hash"), "at": now_iso(),
                                  **closed})
            eid = item_event_id("item_removed", source_id=source_id, run_id=run_id,
                                item_id=iid, content_hash=gone.get("content_hash"))
            if eid not in emitted:
                emit_event("item_removed", source_id=source_id, run_id=run_id, item_id=iid,
                           uri=gone.get("uri"), content_hash=gone.get("content_hash"),
                           attrs=attrs, event_id=eid)
            counts["item_removed"] += 1
    report.notes.append(f"events: {counts}")
    if counts["item_removed"]:
        report.notes.append(f"removals: {closed_total['nodes']} node(s) and "
                            f"{closed_total['edges']} edge(s) closed (system_to set; history "
                            f"kept)")


def close_prior_anchor(store, *, source_id: str, item_id: str, kind: str) -> list[str]:
    """Close — never delete — the item's anchor node under ``kind`` (its id is the item's own,
    `anchor_id_for`) and every open edge touching it; returns what was closed. For a document
    rebuilt under another anchor kind (R-029 TIDY2)."""
    aid = anchor_id_for(kind, source_id, item_id)
    if store.open_node_row(aid) is None:
        return []
    at = store.now()
    closed = [aid]
    with store_batch(store):
        store.close_node(aid, at=at, _commit=False)
        for row in store.fetchall("SELECT DISTINCT id FROM edges WHERE (src_id = ? OR "
                                  "dst_id = ?) AND system_to IS NULL", (aid, aid)):
            store.close_edge(row["id"], at=at, _commit=False)
            closed.append(row["id"])
    return closed


def close_removed_item(store, *, source_id: str, item_id: str, content_hash: Optional[str],
                       doc_kind: Optional[str] = None,
                       live_hashes: Optional[set] = None) -> dict:
    """Close — never delete — the graph rows whose ONLY evidence was a removed item.

    A pointer is the item's when it carries the connection id AND the item identity
    (`document_id`, for prose spans), or — for a table cell, which has no document id — the
    item's last content hash, provided no other live item of the source has the same bytes. A
    derived pointer is the item's when all of its basis is. An OPEN node or edge is closed only
    if EVERY pointer on its open version is the item's; an entity also seen in another document
    or source stays open, untouched. An open edge left touching a closed node is closed too (it
    would otherwise dangle). `system_to` is set; `valid_to` is left as it was — the file going
    away is not evidence of when the fact stopped being true. One transaction."""
    live_hashes = live_hashes or set()

    def mine(p) -> bool:
        if p.kind == "derived":
            return bool(p.derived_from) and all(mine(b) for b in p.derived_from)
        if p.source_id != source_id:
            return False
        if p.document_id == item_id:
            return True
        return (p.kind == "table_cell" and bool(content_hash)
                and p.capture_digest == content_hash and content_hash not in live_hashes)

    patterns = [f"%{item_id}%"] + ([f"%{content_hash}%"] if content_hash else [])
    where = " OR ".join("source_pointer_json LIKE ?" for _ in patterns)
    candidates = sorted({r["entity_id"] for r in store.fetchall(
        f"SELECT DISTINCT entity_id FROM sources WHERE {where}", tuple(patterns))})
    if doc_kind:
        candidates = sorted(set(candidates) | {anchor_id_for(doc_kind, source_id, item_id)})
    nodes_closed: list[str] = []
    edges_closed: list[str] = []
    kept: list[str] = []
    at = store.now()
    with store_batch(store):
        for eid in candidates:
            is_node = store.open_node_row(eid) is not None
            is_edge = not is_node and store.fetchone(
                "SELECT 1 AS x FROM edges WHERE id = ? AND system_to IS NULL", (eid,)) is not None
            if not (is_node or is_edge):
                continue                                     # already closed, or never written
            ptrs = store.sources_for(eid)
            if not ptrs or not all(mine(p) for p in ptrs):
                kept.append(eid)
                continue
            if is_node:
                store.close_node(eid, at=at, _commit=False)
                nodes_closed.append(eid)
            else:
                store.close_edge(eid, at=at, _commit=False)
                edges_closed.append(eid)
        for nid in nodes_closed:
            for row in store.fetchall("SELECT DISTINCT id FROM edges WHERE (src_id = ? OR "
                                      "dst_id = ?) AND system_to IS NULL", (nid, nid)):
                store.close_edge(row["id"], at=at, _commit=False)
                edges_closed.append(row["id"])
    return {"nodes_closed": nodes_closed, "edges_closed": edges_closed,
            "kept_other_evidence": len(kept)}


#: At most this many already-built documents are back-filled into the passage index per run
#: (E2E run-2 N-01); the rest are reported `pending` and taken by the next sync.
BACKFILL_MAX_DEFAULT = 500


def _backfill_max() -> int:
    try:
        return max(0, int(os.environ.get("WALNUT_INGEST_BACKFILL_MAX", BACKFILL_MAX_DEFAULT)))
    except ValueError:
        return BACKFILL_MAX_DEFAULT


def _indexed_digests(retrieval, source_id: str) -> Optional[dict]:
    """The passage index's OWN record of what it holds for this source: doc_id -> (capture
    digest, chunker version), read-only from the published store. None: no store yet."""
    from databridge_retrieval import store as rst
    from databridge_retrieval.paths import passages_dir

    p = passages_dir() / rst.store_filename(source_id, "default")
    if not p.is_file():
        return None
    conn = rst.connect(p, readonly=True)
    try:
        return {r["doc_id"]: (r["sha256"], r["chunker"]) for r in conn.execute(
            "SELECT doc_id, sha256, chunker FROM documents")}
    finally:
        conn.close()


def _backfill_candidates(retrieval, source_id: str, run_dir: Path, built_ids: set) -> list[dict]:
    """E2E run-2 N-01: documents this run did NOT build, but which an earlier run built at the
    SAME content digest this run captured, and which the passage index does not hold at that
    digest (or holds under an older chunker). Incremental by digest; sorted by item id, so the
    per-run bound always makes progress."""
    state = load_state(source_id)
    have = _indexed_digests(retrieval, source_id) or {}
    out = []
    for rec in read_jsonl(run_dir / "captured.jsonl"):
        iid, sha = rec.get("item_id"), rec.get("sha256")
        if not iid or not sha or iid in built_ids or rec.get("kind", "document") != "document":
            continue
        st = state.get(iid) or {}
        if st.get("graph") is False or st.get("content_hash") != sha:
            continue                    # never built, held, unverified, or changed: not ours
        try:
            doc_id = retrieval.doc_id_for({"uri": rec["uri"]}, source_id=source_id)
        except ValueError:
            continue
        if have.get(doc_id) == (sha, retrieval.CHUNKER_VERSION):
            continue                    # the index already holds this digest
        out.append(rec)
    return sorted(out, key=lambda r: r["item_id"])


def _index_passages(source_id: str, run_dir: Path, enabled: bool, harness, timings: Timings,
                    report: RunReport) -> dict:
    """BUILD_PLAN §5.5: each document built this run is indexed into the retrieval store, and each
    removed item is dropped from it, in ONE `BuildSession` (single writer; published once).
    E2E run-2 N-01: documents built by EARLIER runs that the index does not hold at their current
    digest are back-filled in the same session (bounded per run, `WALNUT_INGEST_BACKFILL_MAX`).
    Writes `index.json` either way — with the reason when it did nothing."""
    assert_may_build("passage indexing")
    out: dict = {"enabled": enabled, "indexed": 0, "unchanged": 0, "removed": 0,
                 "other": {}, "status": "skipped", "reason": "", "at": now_iso(),
                 "backfill": {"candidates": 0, "indexed": 0, "unchanged": 0, "other": {},
                              "pending": 0, "max": _backfill_max()}}
    path = run_dir / "index.json"
    built = [r for r in read_jsonl(run_dir / "build.jsonl") if r.get("kind") == "document"]
    removed = read_jsonl(run_dir / "removed.jsonl")
    retrieval = None
    backfill: list[dict] = []
    if not enabled:
        out["reason"] = ("passages off: pass passages=True (adapter agent extract.passages) or "
                         "set WALNUT_INGEST_PASSAGES=1")
    else:
        try:
            import databridge_retrieval as retrieval
        except ImportError as exc:
            out["reason"] = f"databridge_retrieval is not importable here ({exc})"
        if retrieval is not None:
            try:
                backfill = _backfill_candidates(retrieval, source_id, run_dir,
                                                {r["item_id"] for r in built})
            except Exception as exc:                                 # noqa: BLE001
                report.notes.append(f"passages: back-fill check failed — {_plain_error(exc)}")
            bf = out["backfill"]
            bf["candidates"] = len(backfill)
            bf["pending"] = max(0, len(backfill) - bf["max"])
            backfill = backfill[:bf["max"]]
            if not built and not removed and not backfill:
                out["reason"] = "nothing built, removed or left to back-fill this run"
                retrieval = None
    if retrieval is not None:
        t0 = time.perf_counter()
        try:
            session = retrieval.BuildSession(source_id, harness=harness)
            with session:
                jobs = [(r, False) for r in built] + [(r, True) for r in backfill]
                for r, is_backfill in jobs:
                    t1 = time.perf_counter()
                    meta = capture.get_meta(r["sha256"],
                                            expect_digest=r.get("meta_digest")) or {}
                    # f2 #12: the title / mime THIS source saw (the build's outbox, or the
                    # capture line for a back-fill), not the first sighting's
                    own = ((r.get("outbox") or {}).get("event") or {}) if not is_backfill \
                        else {"name": r.get("name"), "mime": r.get("mime")}
                    res = session.index_capture(r["sha256"], {
                        "source_id": source_id, "item_id": r["item_id"], "uri": r["uri"],
                        "title": own.get("name") or meta.get("title"),
                        "mime": own.get("mime") or meta.get("mime")})
                    timings.item(r["item_id"], "index", time.perf_counter() - t1,
                                 status=res.status, chunks=res.chunks, backfill=is_backfill)
                    tally = out["backfill"] if is_backfill else out
                    if res.status in ("indexed", "unchanged"):
                        tally[res.status] += 1
                    else:
                        tally["other"][res.status] = tally["other"].get(res.status, 0) + 1
                for r in removed:
                    session.remove(r["item_id"])
                    out["removed"] += 1
            out["status"] = "ok"
        except Exception as exc:                                     # noqa: BLE001
            out["status"] = "failed"
            out["reason"] = _plain_error(exc)
            report.notes.append(f"passages: not published — {out['reason']}")
        timings.add("index.retrieval", time.perf_counter() - t0)
    if out["status"] == "skipped":
        report.notes.append(f"passages: skipped — {out['reason']}")
    elif out["status"] == "ok":
        bf = out["backfill"]
        report.notes.append(f"passages: {out['indexed']} indexed, {out['unchanged']} unchanged, "
                            f"{out['removed']} removed; back-filled {bf['indexed']} earlier-built "
                            f"document(s), {bf['pending']} pending")
    _atomic_text(path, json.dumps(out, sort_keys=True, indent=1))
    return out


# --- verification --------------------------------------------------------------------------------
class _PointerResolver:
    """Resolves one pointer record (the ledger's dict shape) against the capture store:
    `doc_span` (offsets within the captured text; a label must ground in the raw or masked slice
    by the extractor's own rule), `table_cell` (the row and column exist in the captured
    snapshot), and `derived` (Codex f1 #9: a non-empty `basis`, every leaf of which resolves)."""

    def __init__(self, masked_variant: Optional[dict] = None,
                 text_digests: Optional[dict] = None) -> None:
        self._rows: dict[str, dict] = {}
        self._text: dict[str, Optional[str]] = {}
        self._masked_variant = masked_variant or {}
        # f2 #5: capture sha -> the text digest the run's extraction recorded; a text that does
        # not match it resolves NOTHING (a changed text under existing offsets is not evidence)
        self._text_digests = text_digests or {}

    def _get_text(self, sha: str) -> Optional[str]:
        if sha not in self._text:
            try:
                self._text[sha] = capture.get_text(
                    sha, expect_text_digest=self._text_digests.get(sha))
            except capture.CaptureCorrupt:
                self._text[sha] = None
        return self._text[sha]

    def _masked(self, sha: str) -> Optional[str]:
        variant, digest = self._masked_variant.get(sha, (None, None))
        if not (variant and digest):
            return None
        try:
            return capture.get_redacted_variant(sha, variant, expect_digest=digest)
        except capture.CaptureCorrupt:
            return None

    def _table(self, sha: str) -> dict:
        if sha not in self._rows:
            index: dict = {}
            try:
                snap = json.loads(capture.get_bytes(sha))
                pk = snap.get("primary_key") or []
                for n, row in enumerate(snap.get("rows") or []):
                    vals = [row.get(c) for c in pk]
                    key = (",".join(str(v) for v in vals) if pk and all(v is not None
                                                                          for v in vals)
                           else f"#{n}")
                    index[key] = row
            except Exception:                                        # noqa: BLE001
                index = {}
            self._rows[sha] = index
        return self._rows[sha]

    def resolves(self, p: dict, depth: int = 0) -> bool:
        kind, sha = p.get("kind"), p.get("sha")
        if kind == "derived":
            basis = p.get("basis") or []
            return depth < 8 and bool(basis) and all(
                isinstance(b, dict) and self.resolves(b, depth + 1) for b in basis)
        if not sha:
            return False
        if kind == "doc_span":
            text = self._get_text(sha)
            s, e = p.get("start"), p.get("end")
            ok = text is not None and isinstance(s, int) and isinstance(e, int) and \
                0 <= s <= e <= len(text)
            if ok and p.get("label"):
                # E2E F-16: the SAME rule the extractor grounded the span with (case- and
                # whitespace-folded, boundary-aware) - imported, not restated.
                from databridge_graph.extractor import _span_grounds

                slices = [text[s:e]]
                masked = self._masked(sha)
                if masked is not None:
                    slices.append(masked[s:e])
                ok = any(_span_grounds(t, p["label"], 0, len(t)) is not None for t in slices)
            elif ok and e == s and len(text) > 0:
                ok = False
            return ok
        if kind == "table_cell":
            row = self._table(sha).get(str(p.get("row_id")))
            return row is not None and p.get("column") in row
        return False


def _pointer_record(ptr) -> dict:
    """A graph `SourcePointer` in the ledger's dict shape (for `_PointerResolver`)."""
    kind = getattr(ptr, "kind", None)
    kind = getattr(kind, "value", kind)
    if kind == "derived":
        return {"kind": "derived", "basis": [_pointer_record(b)
                                             for b in (getattr(ptr, "derived_from", None) or [])]}
    return {"kind": kind, "sha": getattr(ptr, "capture_digest", None),
            "start": getattr(ptr, "char_start", None), "end": getattr(ptr, "char_end", None),
            "row_id": getattr(ptr, "row_id", None), "column": getattr(ptr, "column", None)}


def _verify_graph_ids(store, build_records: list[dict], shas: list[str],
                      resolver: "_PointerResolver") -> dict:
    """Codex f2 #6: EVERY graph row a build record says it wrote (`graph_ids`) must exist in the
    store and carry at least one pointer that names one of this run's built captures and
    resolves. A row with no pointer, or only pointers elsewhere, is ungrounded output."""
    out = {"ids": 0, "ungrounded": 0, "examples": []}
    wanted = set(shas)

    def names_run_capture(rec: dict) -> bool:
        if rec.get("kind") == "derived":
            return bool(rec.get("basis")) and all(names_run_capture(b) for b in rec["basis"])
        return rec.get("sha") in wanted

    for r in build_records:
        for gid in r.get("graph_ids") or ():
            out["ids"] += 1
            try:
                ptrs = [_pointer_record(p) for p in store.sources_for(gid)]
                if not ptrs:
                    # FIN-04: a record an approved identity rule absorbed is closed; its pointers
                    # live on the ONE open node that lists it in `merged_record_ids`
                    survivor = _absorbing_node(store, gid) or _repointed_edge(store, gid)
                    if survivor is not None:
                        ptrs = [_pointer_record(p) for p in store.sources_for(survivor)]
            except Exception:                                        # noqa: BLE001
                ptrs = []
            if not any(names_run_capture(p) and resolver.resolves(p) for p in ptrs):
                out["ungrounded"] += 1
                if len(out["examples"]) < 5:
                    out["examples"].append(f"{r.get('item_id', '')[:12]}:{gid}")
    return out


def _repointed_edge(store, gid: str) -> Optional[str]:
    """R-029 (F-03 made FK edges into organisations real): an EDGE this run wrote whose endpoint
    an approved identity rule later absorbed was closed and re-opened on the survivor
    (`merge` re-points it as `src|kind|dst`). Its open successor, or None."""
    parts = gid.split("|")
    if len(parts) != 3:
        return None
    src, kind, dst = parts
    if store.open_node_row(src) is None:
        src = _absorbing_node(store, src) or src
    if store.open_node_row(dst) is None:
        dst = _absorbing_node(store, dst) or dst
    if f"{src}|{kind}|{dst}" == gid:
        return None
    row = store.find_open_edge(src, dst, kind)
    return row["id"] if row is not None else None


def _absorbing_node(store, gid: str) -> Optional[str]:
    """The ONE open node whose `merged_record_ids` lists `gid` (an identity-rule merge, FIN-04),
    else None — none, or two (ambiguous, never chosen by order)."""
    try:
        from databridge_graph.merge import MERGED_IDS_ATTR
    except ImportError:                                              # pragma: no cover
        return None
    hits = []
    for r in store.fetchall("SELECT id, attrs_json FROM nodes WHERE system_to IS NULL AND "
                            "valid_to IS NULL AND instr(attrs_json, ?) > 0 AND "
                            "instr(attrs_json, ?) > 0", (MERGED_IDS_ATTR, json.dumps(gid))):
        try:
            merged = json.loads(r["attrs_json"] or "{}").get(MERGED_IDS_ATTR) or []
        except ValueError:
            continue
        if gid in merged and r["id"] not in hits:
            hits.append(r["id"])
    return hits[0] if len(hits) == 1 else None


def _verify_store_provenance(store, shas: list[str], resolver: "_PointerResolver") -> dict:
    """Every OPEN node / edge whose pointers name one of `shas` (this run's built captures): each
    of its entity pointers that names such a capture must resolve (Codex f1 #10). Pointer kinds
    this package does not mint are counted as `unchecked_kinds`, never passed silently as
    resolved."""
    out = {"entities": 0, "pointers": 0, "unresolved": 0, "unchecked_kinds": 0,
           "examples": []}
    if not shas:
        return out
    try:
        entity_ids: set[str] = set()
        for sha in shas:
            for r in store.fetchall("SELECT DISTINCT entity_id FROM sources WHERE "
                                    "source_pointer_json LIKE ?", (f"%{sha}%",)):
                entity_ids.add(r["entity_id"])
    except Exception:                                                # noqa: BLE001
        return out
    wanted = set(shas)

    def names_run_capture(rec: dict) -> bool:
        if rec.get("kind") == "derived":
            return any(names_run_capture(b) for b in rec.get("basis") or [])
        return rec.get("sha") in wanted

    for eid in sorted(entity_ids):
        is_open = store.open_node_row(eid) is not None or store.fetchone(
            "SELECT 1 AS x FROM edges WHERE id = ? AND system_to IS NULL", (eid,)) is not None
        if not is_open:
            continue
        out["entities"] += 1
        for ptr in store.sources_for(eid):
            rec = _pointer_record(ptr)
            if not names_run_capture(rec):
                continue
            if rec.get("kind") not in ("doc_span", "table_cell", "derived"):
                out["unchecked_kinds"] += 1
                continue
            out["pointers"] += 1
            if not resolver.resolves(rec):
                out["unresolved"] += 1
                if len(out["examples"]) < 5:
                    out["examples"].append(f"{eid} {rec.get('kind')}")
    return out


def completeness_summary(lines: list[dict]) -> dict:
    """`counts.completeness` of a run report: one record per table (the LAST line per table wins,
    so a resumed build is not counted twice) and the totals an E2E asserts on — rows at the source
    vs captured vs built vs nodes (`graph_rows`: nodes, or a join table's edges), declared FKs vs
    those with an edge, edges expected vs built (`edges_missing`), and unresolved references —
    the field names of the independent audit (`scripts/e2e/completeness.py`)."""
    by_table: dict[str, dict] = {}
    for r in lines or []:
        if isinstance(r, dict) and r.get("table"):
            by_table[str(r["table"])] = r
    tables = [by_table[t] for t in sorted(by_table)]

    def total(k: str) -> int:
        return sum(int(t.get(k) or 0) for t in tables)

    return {"tables": tables, "totals": {
        "tables": len(tables), "tables_built": sum(1 for t in tables if t.get("built")),
        **{k: total(k) for k in ("source_rows", "captured_rows", "rows_not_read",
                                 "rows_withheld", "rows_built", "nodes", "graph_rows",
                                 "declared_fks", "fks_with_edge", "edges_expected",
                                 "edges_built", "edges_missing", "unresolved_references")},
        "capped_tables": sorted(t["table"] for t in tables if t.get("capped")),
        "tables_not_built": sorted(t["table"] for t in tables if not t.get("built"))}}


def verify_run(source_id: str, run_id: str, *, store=None) -> dict:
    """Reconcile the run's artefacts with each other and with the capture store (P8). Reads every
    `*.jsonl` in the run dir BY PATTERN, so a ledger added later is counted, not missed (trap 3)."""
    run_dir = run_dir_for(source_id, run_id)
    ledgers = {p.stem: read_jsonl(p) for p in sorted(run_dir.glob("*.jsonl"))}

    def ids(name: str) -> list[str]:
        return [r["item_id"] for r in ledgers.get(name, []) if r.get("item_id")]

    problems: list[str] = []
    inventory = ids("inventory")
    # an item appears once per decision line; the final outcome decides membership
    outcome = {r["item_id"]: r.get("decision") for r in ledgers.get("inventory", [])
               if r.get("item_id")}
    captured, excluded, blocked = set(ids("captured")), set(ids("excluded")), set(ids("blocked"))
    # Codex f1 #12: a fetch that failed and then succeeded on resume keeps its failed line as
    # history; the item's LATEST outcome (the last inventory line) decides where it belongs
    failed_all = set(ids("failed"))
    retried = {i for i in failed_all if outcome.get(i) != "failed"
               and i in (captured | excluded | blocked)}
    failed = failed_all - retried
    accounted = captured | excluded | blocked | failed
    inv = set(outcome)
    if inv != accounted:
        missing = sorted(inv - accounted)[:3]
        extra = sorted(accounted - inv)[:3]
        problems.append(f"inventory ({len(inv)}) != captured+excluded+blocked+failed "
                        f"({len(accounted)}); unaccounted {missing} not-inventoried {extra}")
    if len(captured) + len(excluded) + len(blocked) + len(failed) != len(accounted):
        problems.append("an item is in more than one of captured/excluded/blocked/failed")

    extracted, skipped, unchanged = set(ids("extracted")), set(ids("extract_skipped")), \
        set(ids("unchanged"))
    if captured != (extracted | skipped | unchanged) or \
            len(extracted) + len(skipped) + len(unchanged) != len(captured):
        problems.append(f"captured ({len(captured)}) != extracted ({len(extracted)}) + "
                        f"skipped ({len(skipped)}) + unchanged ({len(unchanged)})")
    # a CARRIED decision (F-31: unchanged, still blocked / in review) is not a fresh screen; it
    # must belong to an item this run recorded as unchanged-and-decided
    # E2E run 5 F-01: a held table ROW (`table_row`) is a decision inside its table's, not an item
    row_holds = [r for r in ledgers.get("governance", []) if r.get("kind") == TABLE_ROW]
    item_gov = [r for r in ledgers.get("governance", []) if r.get("kind") != TABLE_ROW]
    gov = [r["item_id"] for r in item_gov if r.get("item_id") and not r.get("carried_forward")]
    carried = {r["item_id"] for r in item_gov if r.get("carried_forward")}
    if sorted(gov) != sorted(extracted):
        problems.append(f"governance has {len(gov)} decision(s) for {len(extracted)} extracted "
                        f"item(s) — every extracted item needs exactly one")
    decided_unchanged = {r["item_id"] for r in ledgers.get("unchanged", []) if r.get("decided")}
    if carried != decided_unchanged:
        problems.append(f"{len(carried ^ decided_unchanged)} carried decision(s) do not match "
                        f"the items recorded unchanged-and-decided")
    decisions = {r["item_id"]: r.get("decision") for r in item_gov}
    allowed = {i for i, d in decisions.items() if d == "allow"}
    build_ids, bskip = set(ids("build")), set(ids("build_skipped"))
    if not allowed <= (build_ids | bskip):
        problems.append(f"{len(allowed - build_ids - bskip)} allowed item(s) have no build "
                        f"record and no recorded skip")
    zero = {r["item_id"] for r in ledgers.get("build", [])
            if r.get("model_calls") and not r.get("entities")}
    zero_recorded = {r["item_id"] for r in ledgers.get("build_skipped", [])
                     if r.get("reason") == "zero_graph_output"}
    if not zero <= zero_recorded:
        problems.append(f"{len(zero - zero_recorded)} item(s) produced no graph output and it "
                        f"was not recorded")

    # capture integrity (bytes AND the metadata anchor - Codex f1 #7) + pointer resolution
    # against the CAPTURED text / rows
    for rec in ledgers.get("captured", []):
        try:
            capture.get_bytes(rec["sha256"])
        except Exception as exc:                                     # noqa: BLE001
            problems.append(f"capture {rec['sha256'][:12]} unreadable or altered: {exc}")
            continue
        if not rec.get("meta_digest"):
            problems.append(f"capture {rec['sha256'][:12]} has no meta_digest anchor in "
                            f"captured.jsonl (its metadata cannot be verified)")
            continue
        try:
            capture.get_meta(rec["sha256"], expect_digest=rec["meta_digest"])
        except capture.CaptureCorrupt as exc:
            problems.append(f"capture {rec['sha256'][:12]} metadata altered: {exc}")
    # f2 #5: the extracted text each pointer indexes must be the text this run extracted
    text_digests: dict[str, str] = {}
    for rec in ledgers.get("extracted", []):
        want = rec.get("text_sha256")
        if not (rec.get("sha256") and want):
            continue
        text_digests[rec["sha256"]] = want
        try:
            capture.get_text(rec["sha256"], expect_text_digest=want)
        except capture.CaptureCorrupt as exc:
            problems.append(f"extracted text of {rec['sha256'][:12]} is not the text this run "
                            f"extracted: {exc}")
    masked_variant = {r["sha256"]: (r.get("redaction_variant"), r.get("masked_sha256"))
                      for r in ledgers.get("governance", [])
                      if r.get("sha256") and r.get("masked_sha256")}
    resolver = _PointerResolver(masked_variant, text_digests)
    resolved = 0
    unresolved: list[str] = []
    for p in ledgers.get("pointers", []):
        if resolver.resolves(p):
            resolved += 1
        elif len(unresolved) < 5:
            unresolved.append(f"{p.get('entity')} {p.get('kind')} "
                              f"{p.get('sha') and str(p.get('sha'))[:12]}")
    total_ptrs = len(ledgers.get("pointers", []))
    if resolved != total_ptrs:
        problems.append(f"{total_ptrs - resolved} of {total_ptrs} pointer(s) do not resolve "
                        f"against the capture store: {unresolved}")
    # Codex f1 #10: an item the build says produced graph output must have pointers in the ledger
    # (0 of 0 resolving is not proof), and - with a store - every OPEN graph row grounded in this
    # run's captures must carry pointers that resolve, whatever the ledger says.
    ptr_items = {p.get("item_id") for p in ledgers.get("pointers", [])}
    no_ptrs = sorted(r["item_id"] for r in ledgers.get("build", [])
                     if r.get("kind") == "document"
                     and (r.get("anchor") or r.get("entities") or r.get("relations"))
                     and r.get("item_id") not in ptr_items)
    if no_ptrs:
        problems.append(f"{len(no_ptrs)} built item(s) wrote graph rows but no pointers: "
                        f"{no_ptrs[:3]}")
    # f2 #6: a graph id a build record names must have pointers in the ledger too
    ptr_entities = {p.get("entity") for p in ledgers.get("pointers", []) if p.get("entity")}
    unledgered = sorted({g for r in ledgers.get("build", []) for g in r.get("graph_ids") or ()}
                        - ptr_entities)
    store_check = {"entities": 0, "pointers": 0, "unresolved": 0, "unchecked_kinds": 0,
                   "graph_ids": 0, "ungrounded": 0}
    if store is not None:
        built_shas = sorted({r["sha256"] for r in ledgers.get("build", []) if r.get("sha256")})
        store_check = {**store_check,
                       **_verify_store_provenance(store, built_shas, resolver)}
        if store_check["unresolved"]:
            problems.append(f"{store_check['unresolved']} pointer(s) on open graph rows grounded "
                            f"in this run's captures do not resolve: "
                            f"{store_check['examples']}")
        if store_check["unchecked_kinds"]:
            # f2 #6: a pointer kind this verifier cannot resolve is NOT a pass
            problems.append(f"{store_check['unchecked_kinds']} pointer(s) on open graph rows are "
                            f"of a kind verification cannot resolve")
        gid = _verify_graph_ids(store, ledgers.get("build", []), built_shas, resolver)
        store_check["graph_ids"], store_check["ungrounded"] = gid["ids"], gid["ungrounded"]
        if gid["ungrounded"]:
            problems.append(f"{gid['ungrounded']} graph row(s) written this run carry no "
                            f"resolving pointer into this run's captures: {gid['examples']}")
        # the ledger may legitimately lack an edge the builder wrote with its entity's pointer
        # (an anchor edge); with a store each such id was just proven grounded above
        unledgered = []
        store_check.pop("examples", None)
    if unledgered:
        problems.append(f"{len(unledgered)} graph id(s) in build.jsonl have no ledgered pointer: "
                        f"{unledgered[:3]}")

    by_reason: dict[str, int] = {}
    for r in ledgers.get("extract_skipped", []):
        by_reason[r.get("reason", "?")] = by_reason.get(r.get("reason", "?"), 0) + 1
    gov_counts: dict[str, int] = {}
    for d in decisions.values():
        gov_counts[d] = gov_counts.get(d, 0) + 1
    counts = {
        "inventory": len(inv), "inventory_lines": len(inventory), "captured": len(captured),
        "excluded": len(excluded), "blocked": len(blocked), "failed": len(failed),
        "failed_listing": sum(1 for r in ledgers.get("failed", []) if not r.get("item_id")),
        "unchanged": len(unchanged), "extracted": len(extracted),
        "extract_skipped": len(skipped), "extract_skipped_by_reason": by_reason,
        "governance": gov_counts, "built": len(build_ids),
        # F-01: rows the screen held inside allowed tables, by decision (each liftable alone)
        "table_rows_held": {d: sum(1 for r in row_holds if r.get("decision") == d)
                            for d in sorted({str(r.get("decision")) for r in row_holds})},
        "build_skipped": len(bskip), "zero_graph_output": len(zero),
        # FIN-01: allowed items whose model extraction failed - recorded, not built, retried
        "extract_failed": len({r["item_id"] for r in ledgers.get("build_skipped", [])
                               if r.get("reason") == EXTRACT_FAILED and r.get("item_id")}),
        "entities": sum(r.get("entities", 0) for r in ledgers.get("build", [])),
        "relations": sum(r.get("relations", 0) for r in ledgers.get("build", [])),
        "refused": len(ledgers.get("refused", [])), "pointers": total_ptrs,
        "pointers_resolved": resolved, "failed_retried": len(retried),
        "store_provenance": store_check,
        "partial_coverage": len(ledgers.get("coverage", [])),
        "completeness": completeness_summary(ledgers.get("completeness", [])),
        "ledgers_found": sorted(ledgers),
    }
    ident = identity_summary(run_dir)
    if ident is not None:
        counts["identity_consolidation"] = ident
    if store is not None:
        try:
            counts["store_totals"] = store.counts()
        except Exception:                                            # noqa: BLE001
            pass
    return {"ok": not problems, "problems": problems, "counts": counts, "checked_at": now_iso()}


__all__ = ["run_ingest", "verify_run", "list_runs", "RunReport", "PlanNotApproved",
           "latest_discovery", "plan_digest", "schema_only", "source_run_lock", "DISCOVERED", "DISCOVERY_MODE", "DISCOVERY_FILE",
           "DISCOVERY_ONLY",
           "NotAnIngestConnection", "RunInProgress", "ResumeRefused", "run_dir_for",
           "close_removed_item", "Timings", "DEFAULT_CONCURRENCY",
           "load_state", "state_path", "source_dir", "LEDGERS", "CHECKPOINT_ITEMS",
           "completeness_summary", "COMPLETENESS_LEDGER",
           "CHECKPOINT_SECONDS"]
