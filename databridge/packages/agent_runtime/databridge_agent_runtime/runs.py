"""Run records (§5.7) and the append-only action history beside them.

    WALNUT_RUNS_DIR/<kind>/<id>/<run_id>.json            the run record — EXACTLY the §5.7 keys
    WALNUT_RUNS_DIR/<kind>/<id>/<run_id>.actions.jsonl   every action event, append-only
    WALNUT_RUNS_DIR/notifications.jsonl                  human_notified notices (default notifier)

The record's key set is closed (`RunRecord` forbids extras) so what `/trace` and the approval
inbox read cannot drift from the contract. Things §5.7 has no key for live where they naturally
belong without widening it: the verbatim question in ``trigger.question`` (and at the top of
``answer.markdown``), the stop reason in ``coverage.reasons``, action arguments and the full
proposed → approved → executed history in the ``.actions.jsonl`` sidecar. The record's ``actions``
list is the CURRENT state of each action; the sidecar is the history, and it is never rewritten.

``action_id`` is positional — ``a1`` is ``actions[0]`` — so it needs no key of its own.

§5.8 (runtime traceability) widens the record by exactly three keys: ``steps[].produced_by`` and
``answer.produced_by`` — ``{agent, agent_version, task_class, model, prompt_version, run_id, at}``
from the harness result of the planner turn that produced them (``null`` for a step no model
produced) — and the header's ``models_used``, every model the run called, in first-use order.
E2E F-33 adds the header's ``tokens`` and ``cost_usd`` (the run's metered totals).

Decisions on one run's actions are serialised by `RunLock` (an ``O_EXCL`` lock file beside the
record), so two approvals cannot both claim one action (R-017 t02 finding 7).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Any, Iterator, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from . import paths

RUN_KIND_AGENT = "agent"
ACTION_STATUSES = ("proposed", "approved", "executed", "rejected", "reversed")
RUN_STATUSES = ("completed", "stopped", "failed", "refused")


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Step(_Closed):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    result_pointers: list[dict] = Field(default_factory=list)
    policy_decision: dict[str, Any] = Field(default_factory=dict)
    ms: int = 0
    produced_by: Optional[dict[str, Any]] = None


class ActionEntry(_Closed):
    name: str
    automation_mode: Literal["human_required", "human_notified", "autonomous"]
    status: Literal["proposed", "approved", "executed", "rejected", "reversed"]
    by: Optional[str] = None
    pointers: list[dict] = Field(default_factory=list)


class AnswerBlock(_Closed):
    markdown: str = ""
    citations: list[dict] = Field(default_factory=list)
    #: ``[{ref, pointer}]``: each cited ref WITH its pointer (additive; readers pair by this, never
    #: by the order of a rendered Sources list).
    citation_refs: list[dict] = Field(default_factory=list)
    unsupported_claims: list[dict] = Field(default_factory=list)
    produced_by: Optional[dict[str, Any]] = None
    #: ``[{text, citations, supported, reason, subject_candidate, requires_human_selection}]`` —
    #: every claim with its STRUCTURAL candidate flag (additive, R-021 / FED answer contract): a
    #: reader never infers "needs a person to select" from the rendered markdown.
    claims: list[dict] = Field(default_factory=list)


class Coverage(_Closed):
    searched: list[str] = Field(default_factory=list)
    skipped: list[dict] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


#: Skipped-read reason codes that do NOT reduce coverage (N-12): pointers of sources OUTSIDE the
#: agent's declared scope are withheld by declaration — every declared source was still read. They
#: stay listed in ``coverage.skipped`` (never silent), as notes. A failed or refused tool call that
#: a later call of the same tool recovered is marked ``recovered`` and does not reduce it either;
#: an action refusal (``action`` set) is about a write, not about what was read.
NON_REDUCING_SKIPS = frozenset({"out_of_scope"})


def reduces_coverage(entry: dict) -> bool:
    return not (entry.get("reason_code") in NON_REDUCING_SKIPS or entry.get("recovered")
                or entry.get("action"))


class RunOutcome(_Closed):
    """What the run's answer AMOUNTS to, as fields an evaluator asserts (E2E run 2 N-12; the
    golden set's ``outcome``, packs/workspace/evals/agent_contract.jsonl). Computed by the runtime
    from structure — the checked claims, the coverage, the withheld register, the question —
    never copied from the model's prose.

    * ``reason_code`` — ``answered`` | ``value_absent`` | ``identity_candidate`` |
      ``withheld_by_screen`` | ``source_not_connected`` | ``no_answer`` | ``agent_not_runnable``
    * ``human_confirmation_required`` — a person must decide something (any candidate named)
    * ``coverage_status`` — ``complete`` | ``reduced`` (`reduces_coverage` over ``skipped``)
    * ``candidates`` — ``[{subject_kind, value, cites}]``: people offered for a human to confirm
    * ``withheld`` — ``[{source, item, decision, rules}]`` the screen held back that the answer met
    * ``not_connected`` — sources the question names that no tool of this agent reaches
    """

    reason_code: str
    human_confirmation_required: bool = False
    coverage_status: Literal["complete", "reduced"] = "complete"
    candidates: list[dict] = Field(default_factory=list)
    withheld: list[dict] = Field(default_factory=list)
    not_connected: list[str] = Field(default_factory=list)


class RunRecord(_Closed):
    run_id: str
    spec_digest: str
    trigger: dict[str, Any]
    started: str
    ended: Optional[str] = None
    status: Literal["completed", "stopped", "failed", "refused"]
    models_used: list[str] = Field(default_factory=list)
    #: §5.8 run header (E2E F-33): the planner's metered tokens and cost across every model call
    #: of this run, from the harness results (a cached call counts 0). Additive keys.
    tokens: int = 0
    cost_usd: float = 0.0
    steps: list[Step] = Field(default_factory=list)
    actions: list[ActionEntry] = Field(default_factory=list)
    answer: AnswerBlock = Field(default_factory=AnswerBlock)
    coverage: Coverage = Field(default_factory=Coverage)
    #: N-12 (additive): the structural outcome. ``None`` on records written before it existed.
    outcome: Optional[RunOutcome] = None

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(when: dt.datetime) -> str:
    return when.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds")


def _root(runs_dir: Optional[Path]) -> Path:
    return Path(runs_dir) if runs_dir is not None else paths.runs_dir()


def run_path(run_id: str, *, agent: str, kind: str = RUN_KIND_AGENT,
             runs_dir: Optional[Path] = None) -> Path:
    return _root(runs_dir) / kind / agent / f"{run_id}.json"


def actions_path(record_path: Path) -> Path:
    return record_path.with_name(record_path.name[: -len(".json")] + ".actions.jsonl")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_run(record: RunRecord, path: Path) -> Path:
    _atomic_write(path, json.dumps(record.to_dict(), indent=2, ensure_ascii=False))
    return path


def read_run(path: Path) -> RunRecord:
    return RunRecord.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def find_run(run_id: str, runs_dir: Optional[Path] = None) -> Path:
    if not run_id or any(c in run_id for c in "/\\*?[]") or run_id.startswith("."):
        raise FileNotFoundError(f"{run_id!r} is not a run id")
    hits = sorted(_root(runs_dir).glob(f"*/*/{run_id}.json"))
    if not hits:
        raise FileNotFoundError(f"no run {run_id!r} under {_root(runs_dir)}")
    if len(hits) > 1:
        raise FileNotFoundError(f"run id {run_id!r} is ambiguous: {[str(h) for h in hits]}")
    return hits[0]


def list_runs(*, agent: Optional[str] = None, kind: str = RUN_KIND_AGENT,
              runs_dir: Optional[Path] = None) -> list[Path]:
    base = _root(runs_dir) / kind
    pattern = f"{agent}/*.json" if agent else "*/*.json"
    return sorted(base.glob(pattern)) if base.is_dir() else []


class ActionLog:
    """``<run_id>.actions.jsonl``. One ``write()`` of one line per event, flushed; never rewritten."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def append(self, event: dict) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str) + "\n"
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
        return event

    def events(self, action_id: Optional[str] = None) -> list[dict]:
        if not self.path.is_file():
            return []
        out = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()
               if line.strip()]
        return [e for e in out if action_id is None or e.get("action_id") == action_id]

    def proposal(self, action_id: str) -> Optional[dict]:
        return next((e for e in self.events(action_id) if e.get("event") == "proposed"), None)


class RunLocked(RuntimeError):
    """Another decision on this run holds its lock."""


class RunLock:
    """``<run_id>.lock`` created with ``O_CREAT | O_EXCL`` — atomic on every OS this runs on.

    Held only across a read-modify-write of the record and its sidecar, never across a call to a
    source. A lock that outlives its holder (a crashed process) is NOT broken automatically: a
    stuck decision is visible and safe; a broken lock is how an action runs twice. The error names
    the file so an operator can remove it after checking.
    """

    def __init__(self, record_path: Path, *, timeout_s: float = 10.0) -> None:
        self.path = Path(record_path).with_suffix(".lock")
        self.timeout_s = timeout_s
        self._fd: Optional[int] = None

    def __enter__(self) -> "RunLock":
        deadline = time.monotonic() + self.timeout_s
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, f"{os.getpid()} {iso(utcnow())}".encode("utf-8"))
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise RunLocked(f"another decision on this run is in progress (lock file "
                                    f"{self.path}); try again. If no decision is running, check "
                                    f"the action history before removing the file") from None
                time.sleep(0.02)

    def __exit__(self, *_exc: Any) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass


def action_id_for(index: int) -> str:
    return f"a{index + 1}"


def index_for(action_id: str) -> int:
    if not (action_id.startswith("a") and action_id[1:].isdigit() and int(action_id[1:]) >= 1):
        raise KeyError(f"{action_id!r} is not an action id (a1, a2, …)")
    return int(action_id[1:]) - 1


def iter_action_logs(*, agent: Optional[str] = None,
                     runs_dir: Optional[Path] = None) -> Iterator[tuple[Path, ActionLog]]:
    base = _root(runs_dir) / RUN_KIND_AGENT
    pattern = f"{agent}/*.actions.jsonl" if agent else "*/*.actions.jsonl"
    if not base.is_dir():
        return
    for p in sorted(base.glob(pattern)):
        yield p.with_name(p.name[: -len(".actions.jsonl")] + ".json"), ActionLog(p)


def default_notifier(runs_dir: Optional[Path] = None):
    """Append a notice to ``notifications.jsonl`` — what a UI or a mail dispatcher tails."""
    def _notify(event: dict) -> None:
        ActionLog(_root(runs_dir) / "notifications.jsonl").append(event)
    return _notify


__all__ = ["RUN_KIND_AGENT", "ACTION_STATUSES", "RUN_STATUSES", "Step", "ActionEntry",
           "RunLock", "RunLocked",
           "AnswerBlock", "Coverage", "RunOutcome", "NON_REDUCING_SKIPS", "reduces_coverage",
           "RunRecord", "run_path", "actions_path", "write_run",
           "read_run", "find_run", "list_runs", "ActionLog", "action_id_for", "index_for",
           "iter_action_logs", "default_notifier", "utcnow", "iso"]
