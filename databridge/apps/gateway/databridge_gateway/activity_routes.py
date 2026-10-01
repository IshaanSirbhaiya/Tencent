"""`/activity` — one read-only feed of what the agents did, merged from what is already on disk.

    GET /activity                     the timeline: agent runs, federated (delegated) runs,
                                      adapter-agent syncs, automation firings, approval decisions,
                                      adapter-agent drift proposals and ontology proposals —
                                      newest first, each with its actor, ``produced_by`` (agent +
                                      model), status, duration and tokens where recorded.
                                      ``?limit=`` (1..200), ``?before=<cursor>``, ``?kinds=a,b``
    GET /activity/runs/{run_id}       one run: the record (allow-listed), its action history, and
                                      — for a federated run — the DELEGATION TREE.
                                      ``?kind=&owner=`` pick one run when an id exists in more
                                      than one namespace you may see (409 ``AMBIGUOUS_RUN``)
    GET /activity/automations         every trigger, its firings (idempotency key, time), and the
                                      runs + approvals each firing produced

**There is no store here.** Every row is read, per request, from the file its producer already
writes — nothing is copied, cached or written back (build plan §5.7/§5.8):

    WALNUT_RUNS_DIR/agent/<agent>/<run_id>.json (+ .actions.jsonl)   `databridge_agent_runtime`
    WALNUT_RUNS_DIR/federated/<principal>/<run_id>.json               `databridge_federated`
    WALNUT_RUNS_DIR/adapter_agent/<id>/<run_id>.json                  `databridge_adapter_agents`
    WALNUT_AUTOMATION_DIR/firings.jsonl                               `databridge_automation`
    WALNUT_ADAPTER_AGENTS_DIR/_adapt/<id>/adaptations.jsonl           drift proposals (AA)
    WALNUT_ADAPTER_AGENTS_DIR/_proposals/ontology_proposals.jsonl     ontology proposals (M)
    WALNUT_ADAPTER_AGENTS_DIR/_history/<id>.jsonl                     who created an adapter agent
    WALNUT_AGENTS_DIR/<agent>.owner.json                              who saved a workflow agent
                                                                      (`agent_ownership`, BRAIN)

Tokens: the run record's own metered header (EF4) wins; otherwise, where the gateway's in-memory
`TraceRecorder` still holds the trace of a run, the tokens its ``cost`` steps measured are joined
in, with the ``request_id`` so the per-request view can open it. Otherwise ``tokens`` is ``null``
— never 0.

**Who sees what** (`Viewer`) — Codex f1 #2: ownership FAILS CLOSED.

* a **workflow-agent run** belongs to the agent's recorded owner (``agent_ownership``: the
  principal who saved it through ``POST /agents``) and to the person who started it
  (``trigger.by``);
* a **federated run** belongs to the principal who asked — ``trigger.by``, which must agree with
  the ``federated/<principal>/`` directory it was written to;
* an **adapter-agent** record (sync, drift proposal, ontology proposal, firing) belongs to the
  agent's creator — the ``by`` of its ``create`` history entry, when exactly one is recorded;
* an **automation firing** belongs to the owner of the agent it fired (and, for a workflow agent,
  to the person who fired it); an **approval** is visible when its run is;
* a record with **no owner, or an ambiguous one** (an agent saved before owners were recorded, an
  adapter agent with no or two creators, a federated record whose asker disagrees with its
  directory, an unreadable ownership record) is visible ONLY to an **operator** — the same rule
  BRAIN's ``agent_ownership.is_operator`` applies to decisions: ``WALNUT_OPERATORS``, and when that
  is unset only the dev principal. An operator sees every record. "Owner unknown" is never
  "everyone".

Every run is addressed by its NAMESPACE — ``(kind, owner directory, run_id)`` — never by ``run_id``
alone (Codex f1 #7). A trace joins a run only when it names that namespace AND its owner is one
of the run's owners AND the viewer may open it at ``/trace``.

**Bounded work** (Codex f1 #6). Only the sources the requested kinds need are read. Run
directories the viewer cannot own are not opened (a federated directory of another principal, an
adapter agent somebody else created). Files are ordered by the time in their name (else their file
time) without being opened; files newer than the cursor are skipped; a stream stops once it holds
a full page and the next file cannot reach it; and no request opens more than
``MAX_RECORDS_OPENED`` run records per run kind. A page cut short by that budget returns a cursor
(``next_cursor``) so the next request continues where this one stopped. Every record has a byte
limit, and every JSONL log is read from its tail within a byte and line limit. A count that could
not be completed is ``null`` (``counts_incomplete`` names the kinds) — never a smaller number
presented as the total. The directory LISTING is still one ``stat`` per file; no file is opened
for it.

**Nothing is passed through** (Codex f1 #5). The run record is projected through a per-kind
allow-list (`RECORD_SCHEMAS`); a key the projection does not name — at ANY depth — is not served,
and its path is listed in ``masked_fields``. Evidence, examples, documents, candidates, withheld
values and raw payloads are additionally dropped wherever they appear (`MASKED_KEYS`). Timeline
rows are built from named fields with scalar values only.

**Diagnostics are scoped** (Codex f1 #8). A problem names its file only when the record's
namespace is the viewer's (or the viewer is an operator); otherwise it is not reported at all. A
run you may not see, an ownerless one and a missing one get the SAME 404 body, byte for byte.

Nothing is fabricated: a missing field is ``null`` (and says "not recorded"), never 0. Identity is
never resolved here; candidates stay counts.

**Codex f2 (ACT3).** A firing is identified by ``(spec kind, spec id, idempotency key)``, never the
key alone, and a line contradicting its firing is not merged. Bytes read are budgeted per kind
(`MAX_BYTES_PER_KIND`), listings and owner lookups are capped (`MAX_DIR_ENTRIES`,
`MAX_OWNER_DIRS`). A record-derived id becomes a path only through `safe_name`. A record whose
``run_id`` (or adapter ``agent_id``) disagrees with its path is not served. Free text (error
messages, refusal and policy sentences, drift details, delegate notes, SQL holding a literal) is
not served; the code beside it is. A firing count from a log not read whole is ``null``.

Mounted by ``register_activity_routes(app)``.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from . import agent_ownership
from .auth import AuthenticatedPrincipal, install_auth_handler, require_principal
from .trace_routes import has_audit_role

log = logging.getLogger("databridge.gateway.activity")

#: The closed kind vocabulary of the feed.
ACTIVITY_KINDS = ("agent_run", "federated_run", "adapter_sync", "firing", "approval",
                  "drift_proposal", "ontology_proposal")
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
#: Firings shown per trigger on `/activity/automations`.
MAX_FIRINGS_PER_SPEC = 50
#: Run records ONE request may open per run kind (Codex f1 #6). Reaching it ends the page early
#: with a cursor; it never makes a page silently thinner.
MAX_RECORDS_OPENED = 1000
#: Bytes one run record may have to be read at all. Larger: reported, not read.
MAX_RECORD_BYTES = 2 * 1024 * 1024
#: Bytes read from the TAIL of one JSONL log; older lines are reported as not read.
MAX_JSONL_BYTES = 8 * 1024 * 1024
#: One JSONL line longer than this is skipped (and counted as unreadable).
MAX_JSONL_LINE_BYTES = 256 * 1024
#: An ownership history larger than this is not read: its owner is then UNKNOWN (operator-only).
MAX_HISTORY_BYTES = 1024 * 1024
#: How far a run's recorded start may sit from the timestamp in its name (every producer builds
#: the id from its start clock: `run-`/`fed-`/`<ts>Z-`). Skipping and early stop keep this margin.
NAME_TIME_SLACK = dt.timedelta(minutes=1)
#: Longest string served from a record (a longer one is cut and listed in ``masked_fields``).
MAX_STR = 20_000
#: Upper bound on traces scanned for the token / request_id join (the recorder holds fewer).
MAX_TRACE_SCAN = 10_000
#: The id half of a cursor that marks "everything at this time and older" (sorts after any id).
FRONTIER_ID = "~"
#: Bytes ONE request may read per run kind (records + their action logs), and for the drift logs
#: together (Codex f2 #6: a bound on files opened is not a bound on bytes read — one agent run's
#: action log alone may be `MAX_JSONL_BYTES`). Reaching it ends the stream exactly as
#: `MAX_RECORDS_OPENED` does: a frontier cursor, the kind marked incomplete.
MAX_BYTES_PER_KIND = 32 * 1024 * 1024
#: The byte budget stops a stream once the page holds a row (so paging always advances); with no
#: row yet it may run on to this multiple of the budget, and no further.
BYTE_BUDGET_CEILING = 4
#: Directory entries ONE request lists per run kind (Codex f2 #6: the listing was every file).
#: Past it the kind is incomplete and the feed says so; nothing past it is stat-ed.
MAX_DIR_ENTRIES = 20_000
#: Owner directories ONE request may consider per kind (adapter agents whose creator is looked
#: up; drift directories; workflow-agent directories a run-detail request probes). Past it: the
#: kind is incomplete (feed), or the run route asks for ``owner`` (a refusal that does not vary
#: with the run id, so it discloses nothing).
MAX_OWNER_DIRS = 2_000

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
#: A machine code (a reason or error code): served; anything else is free text and is not.
_CODE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
#: An exception's type, from the head of a producer's error text (``KeyError: ...``).
_ERROR_TYPE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.]{0,79})(?::|$)")


def safe_name(value: Any) -> Optional[str]:
    """A record-derived id that may name a file or directory (Codex f2 #8): one path segment of
    the namespace alphabet, no separator, no leading dot. Anything else is None — UNKNOWN."""
    ok = isinstance(value, str) and bool(_RUN_ID_RE.match(value)) and ".." not in value
    return value if ok else None


def _code(value: Any) -> Optional[str]:
    """A code-shaped value (``missed``, ``already_decided``) or None: free text is not served
    (Codex f2 #9 — a producer's sentence can carry a value the allow-list never saw)."""
    return value if isinstance(value, str) and _CODE_RE.match(value) else None


def _error_type(value: Any) -> Optional[str]:
    """The exception type a producer's error text starts with, or None. The text is not served."""
    m = _ERROR_TYPE_RE.match(value) if isinstance(value, str) else None
    return m.group(1) if m else None


def _nr(value: Any) -> str:
    """A value for a title: itself, or "(not recorded)" — never the string "None"."""
    v = _scalar(value)
    return "(not recorded)" if v is None or v == "" else str(v)
_SQL_RE = re.compile(r"\[SQL\]\s*(.+?)(?:\s+—\s+|$)", re.S)
_NAME_TS_RE = re.compile(r"(\d{8}T\d{6})")

#: Statuses that make a row LOUD on the timeline: something did not do what was asked.
_LOUD_RUN = {"failed", "refused", "partial", "busy", "unverified", "stopped"}
_LOUD_FIRING = {"failed", "dead_lettered"}

#: run directory kind -> the feed kinds read from it
_RUN_DIR_KINDS = {"agent": {"agent_run", "approval"}, "federated": {"federated_run"},
                  "adapter_agent": {"adapter_sync"}}
_ITEM_KIND = {"agent": "agent_run", "federated": "federated_run", "adapter_agent": "adapter_sync"}


def _refusal(code: str, message: str, status: int = 404, alternative: Any = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"refusal": {
        "code": code, "message": message, "nearest_alternative": alternative}})


def _not_found() -> JSONResponse:
    """The one 404 of the run route: identical for a missing run, another principal's run, an
    ownerless one and an unreadable one — nothing in it varies with the id (Codex f1 #8)."""
    return _refusal("UNKNOWN_RUN", "no run with that id that you can see")


# ================================================================================================
# where the records are
# ================================================================================================
@dataclass(frozen=True)
class ActivitySources:
    runs_dir: Path
    firings_path: Path
    adapter_agents_dir: Path


def resolve_sources(app: Any) -> ActivitySources:
    """The producers' own locations. The app's live platform (if one was ALREADY built — this never
    builds one) wins for the firing log and the adapter-agent root, because that is where its
    engine and manager write; otherwise each package's env-resolved default."""
    override = getattr(app.state, "activity_sources", None)
    if isinstance(override, ActivitySources):
        return override
    try:
        from databridge_agent_runtime.paths import runs_dir               # noqa: PLC0415
        runs = runs_dir()
    except Exception:                                                     # noqa: BLE001
        import os                                                         # noqa: PLC0415
        value = os.environ.get("WALNUT_RUNS_DIR")
        runs = Path(value) if value else Path(__file__).resolve().parents[3] / "var" / "runs"
    firings = aa_dir = None
    rt = getattr(app.state, "platform", None)
    engine = getattr(rt, "engine", None)
    manager = getattr(rt, "manager", None)
    if engine is not None:
        firings = getattr(getattr(engine, "firings", None), "path", None)
    if manager is not None:
        aa_dir = getattr(getattr(manager, "registry", None), "root", None)
    if firings is None:
        try:
            from databridge_automation.paths import firings_path          # noqa: PLC0415
            firings = firings_path()
        except Exception:                                                 # noqa: BLE001
            firings = runs.parent / "automation" / "firings.jsonl"
    if aa_dir is None:
        try:
            from databridge_adapter_agents.paths import adapter_agents_dir  # noqa: PLC0415
            aa_dir = adapter_agents_dir()
        except Exception:                                                 # noqa: BLE001
            aa_dir = runs.parent / "adapter_agents"
    return ActivitySources(Path(runs), Path(firings), Path(aa_dir))


# ================================================================================================
# small, honest helpers
# ================================================================================================
def parse_ts(value: Any) -> Optional[dt.datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        when = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when.astimezone(dt.timezone.utc)


def _fmt_key(when: dt.datetime) -> str:
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sort_key(value: Any) -> str:
    """A comparable UTC key; "" (sorts last) for a time that was not recorded."""
    when = parse_ts(value)
    return _fmt_key(when) if when else ""


def duration_ms(start: Any, end: Any) -> Optional[int]:
    a, b = parse_ts(start), parse_ts(end)
    if a is None or b is None or b < a:
        return None
    return int((b - a).total_seconds() * 1000)


def _scalar(v: Any) -> Any:
    """A JSON scalar (strings cut at `MAX_STR`), or None for anything structured."""
    if isinstance(v, str):
        return v[:MAX_STR]
    return v if v is None or isinstance(v, (int, float, bool)) else None


def _scalars(v: Any) -> Optional[list]:
    return [_scalar(x) for x in v if _scalar(x) is not None] if isinstance(v, list) else None


def _by(record: dict) -> Optional[str]:
    trig = record.get("trigger") if isinstance(record.get("trigger"), dict) else {}
    by = trig.get("by")
    return by if isinstance(by, str) and by else None


class Problems:
    """Diagnostics, scoped to the viewer (Codex f1 #8).

    ``report(visible, entry)``: ``visible`` is True only when the record's namespace is known to
    be the viewer's. Anything else is kept for an operator and NOT reported to anyone else — not a
    file name, not a count, not a line saying that something was hidden — so a private record's
    existence is never disclosed by the error it caused."""

    def __init__(self, operator: bool = False) -> None:
        self.operator = operator
        self.rows: list[dict] = []

    def report(self, visible: bool, entry: dict) -> None:
        if visible or self.operator:
            self.rows.append(entry)

    def add(self, entry: dict) -> None:
        """A message that names no private record."""
        self.rows.append(entry)

    def out(self, cap: int = 50) -> list[dict]:
        return list(self.rows[:cap])


def _read_json(path: Path, problems: Problems, *, visible: bool) -> Optional[dict]:
    """One record, if it is small enough to read. Problems are reported per `visible`."""
    where = {"source": path.parent.parent.name, "file": path.name}
    try:
        size = path.stat().st_size
        if size > MAX_RECORD_BYTES:
            problems.report(visible, {**where, "error": f"larger than {MAX_RECORD_BYTES} bytes; "
                                                        f"not read"})
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.report(visible, {**where, "error": f"unreadable: {type(exc).__name__}"})
        return None
    if not isinstance(data, dict):
        problems.report(visible, {**where, "error": "not a JSON object"})
        return None
    return data


def identity_matches(kind: str, owner_dir: str, path: Path, record: dict, problems: Problems,
                     *, visible: bool) -> bool:
    """Does the record say it IS the run its path names (Codex f2 #10)? Its ``run_id`` must equal
    the file name, and an adapter-agent sync's ``agent_id`` (when it names one) its directory.
    A record that names another run is not served at all: the response would otherwise join one
    run's trace and links to another run's file."""
    bad = None
    if record.get("run_id") != path.stem:
        bad = "its run_id is not its file name"
    elif kind == "adapter_agent" and record.get("agent_id") not in (None, owner_dir):
        bad = "its agent_id is not its directory"
    if bad:
        problems.report(visible, {"source": kind, "file": path.name,
                                  "error": f"not served: {bad}"})
        return False
    return True


def _tail_lines(path: Path, max_bytes: int) -> tuple[list[bytes], bool]:
    """The complete lines in the last `max_bytes` of a file, and whether older lines were cut."""
    size = path.stat().st_size
    with open(path, "rb") as fh:
        if size <= max_bytes:
            return fh.read().splitlines(), False
        fh.seek(size - max_bytes)
        data = fh.read()
    # the first line is (almost certainly) partial: drop it
    return (data.split(b"\n", 1)[1].splitlines() if b"\n" in data else []), True


def _read_jsonl(path: Path, problems: Problems, source: str, *,
                visible: bool = True) -> tuple[list[dict], bool]:
    """A JSONL log read from its tail within `MAX_JSONL_BYTES`: ``(records, lossy)``. ``lossy`` is
    True when lines were cut or unreadable — the caller then does not present its rows as all.
    ``visible=False`` for a log shared by every principal (firings, proposals): its problems are
    then operator-only, since a count of bad lines is a count about other people's records."""
    if not path.is_file():
        return [], False
    try:
        lines, cut = _tail_lines(path, MAX_JSONL_BYTES)
    except OSError as exc:
        problems.report(visible, {"source": source, "file": path.name,
                                  "error": f"unreadable: {type(exc).__name__}"})
        return [], True
    out: list[dict] = []
    bad = 0
    for raw in lines:
        if not raw.strip():
            continue
        if len(raw) > MAX_JSONL_LINE_BYTES:
            bad += 1
            continue
        try:
            rec = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            bad += 1
            continue
        if isinstance(rec, dict):
            out.append(rec)
    if cut:
        problems.report(visible, {"source": source, "file": path.name,
                                  "error": f"only the most recent {MAX_JSONL_BYTES} bytes of this "
                                           f"log were read; older entries are not shown"})
    if bad:
        problems.report(visible, {"source": source, "file": path.name,
                                  "error": f"{bad} line(s) are not JSON (or too long) and were "
                                           f"not read"})
    return out, bool(cut or bad)


def _produced_by(pb: Any) -> Optional[dict]:
    """The §5.8 identity of whatever produced a thing — only the keys the contract names (plus the
    ``by``/``method``/``adapter_agent`` a delegate records), scalars only. ``None`` when nothing
    was recorded."""
    if not isinstance(pb, dict) or not pb:
        return None
    keys = ("agent", "agent_version", "task_class", "model", "prompt_version", "run_id", "at",
            "by", "method", "adapter_agent", "mode", "model_calls", "on_behalf_of")
    return {k: _scalar(pb.get(k)) for k in keys if k in pb}


# ================================================================================================
# the run files of one kind: listed, ordered and bounded WITHOUT being opened
# ================================================================================================
def name_time(name: str) -> Optional[dt.datetime]:
    """The start time a run id carries in its name (``…YYYYmmddTHHMMSS…``), or None."""
    m = _NAME_TS_RE.search(name)
    if not m:
        return None
    try:
        return dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _mtime(path: Path) -> Optional[dt.datetime]:
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc)
    except OSError:
        return None


@dataclass(frozen=True)
class RunFile:
    kind: str
    owner: str
    path: Path
    #: the newest sort key any row read from this file can have
    upper: str
    #: the oldest sort key its run row can have; None when unknown (no time in its name)
    lower: Optional[str]


def _run_files(root: Path, kind: str, dirs: Optional[set[str]], *,
               with_actions: bool, cut: Optional[list] = None) -> list[RunFile]:
    """One kind's run records, newest first by the bound on their rows — from the time in the name
    (± `NAME_TIME_SLACK`) or, with none, the file's own time (a record is written when its run
    has started). With ``with_actions`` an agent run's rows include its approvals, which sort by
    their latest decision: that bound is the action log's file time. Nothing is opened here.

    ``dirs``: the only owner directories that are listed (None: all).

    Bounded (Codex f2 #6): at most `MAX_DIR_ENTRIES` entries are looked at (directories and
    files together); past that nothing more is listed or stat-ed and ``cut`` gets a marker, so the
    caller reports the kind as incomplete rather than presenting a partial listing as all."""
    base = root / kind
    if not base.is_dir():
        return []
    out: list[RunFile] = []
    seen = 0

    def over() -> bool:
        nonlocal seen
        seen += 1
        if seen > MAX_DIR_ENTRIES:
            if cut is not None:
                cut.append(kind)
            return True
        return False

    names = sorted(dirs) if dirs is not None else None
    dir_iter = ((base / n) for n in names) if names is not None else base.iterdir()
    for d in dir_iter:
        if over():
            break
        if not d.is_dir() or d.name.startswith((".", "_")):
            continue
        if dirs is not None and d.name not in dirs:
            continue
        stop = False
        for p in d.glob("*.json"):
            if over():
                stop = True
                break
            if p.name.startswith("."):
                continue
            nt = name_time(p.stem)
            if nt is not None:
                upper, lower = nt + NAME_TIME_SLACK, _fmt_key(nt - NAME_TIME_SLACK)
            else:
                m = _mtime(p)
                upper = (m + NAME_TIME_SLACK) if m else None
                lower = None
            if with_actions:
                am = _mtime(p.with_name(p.name[: -len(".json")] + ".actions.jsonl"))
                if am is not None and upper is not None:
                    upper = max(upper, am + NAME_TIME_SLACK)
            # a file whose time cannot be read at all sorts FIRST: it is never skipped
            ukey = _fmt_key(upper) if upper is not None else "9999"
            out.append(RunFile(kind, d.name, p, ukey, lower))
        if stop:
            break
    out.sort(key=lambda f: (f.upper, f.path.name), reverse=True)
    return out


# ================================================================================================
# who may see what
# ================================================================================================
class Viewer:
    """The one visibility rule (module docstring). Built per request for one principal.

    Fails closed (Codex f1 #2): a record whose owner is not recorded, cannot be read, or is
    ambiguous is visible only to an operator."""

    def __init__(self, principal_id: str, sources: ActivitySources, *,
                 operator: bool = False, trace_admin: bool = False) -> None:
        self.pid = principal_id
        self.sources = sources
        self.operator = operator
        self.trace_admin = trace_admin
        self._aa_owners: dict[str, Optional[str]] = {}
        self._agent_owners: dict[str, Optional[str]] = {}
        #: run kinds whose owner directories were not all considered (`MAX_OWNER_DIRS`)
        self.dirs_cut: set[str] = set()

    # -- recorded owners -----------------------------------------------------------------------
    def adapter_agent_owner(self, agent_id: str) -> Optional[str]:
        """The ``by`` of the adapter agent's ``create`` history entry when exactly ONE creator is
        recorded; ``None`` when none is, two are, or the history cannot be read. ``None`` is
        UNKNOWN, and unknown is operator-only — never public."""
        if agent_id in self._aa_owners:
            return self._aa_owners[agent_id]
        owner = None
        # Codex f2 #8: an id read from a record never becomes a path unless it is one segment of
        # the namespace alphabet AND the path it names stays under `_history`. Otherwise: unknown.
        hist_root = self.sources.adapter_agents_dir / "_history"
        name = safe_name(agent_id)
        hist = hist_root / f"{name}.jsonl" if name else None
        try:
            if hist is None or hist.resolve().parent != hist_root.resolve():
                raise OSError("not a history path")
            too_big = hist.is_file() and hist.stat().st_size > MAX_HISTORY_BYTES
        except OSError:
            too_big = True
        if not too_big:
            try:
                from databridge_adapter_agents.registry import AdapterAgentRegistry  # noqa: PLC0415
                history = AdapterAgentRegistry(self.sources.adapter_agents_dir).history(agent_id)
            except Exception:                                             # noqa: BLE001
                history = []
            creators = {rec["by"] for rec in history
                        if isinstance(rec, dict) and rec.get("action") == "create"
                        and isinstance(rec.get("by"), str) and rec["by"]}
            owner = next(iter(creators)) if len(creators) == 1 else None
        self._aa_owners[agent_id] = owner
        return owner

    def agent_owner(self, agent: str) -> Optional[str]:
        """The workflow agent's owner as BRAIN records it (`agent_ownership.recorded_owner`), or
        ``None`` (never recorded, unreadable)."""
        if agent not in self._agent_owners:
            try:
                # Codex f2 #8: a record-derived name reaches the owner file only when it is safe
                self._agent_owners[agent] = (agent_ownership.recorded_owner(agent)
                                             if safe_name(agent) else None)
            except Exception:                                             # noqa: BLE001
                self._agent_owners[agent] = None
        return self._agent_owners[agent]

    # -- the rule --------------------------------------------------------------------------------
    def run_principals(self, kind: str, owner_dir: str, record: dict) -> set[str]:
        """Who a run BELONGS to (may be empty: ownerless or ambiguous)."""
        by = _by(record)
        if kind == "federated":
            return {by} if by is not None and by == owner_dir else set()
        if kind == "adapter_agent":
            owner = self.adapter_agent_owner(owner_dir)
            return {owner} if owner else set()
        return {p for p in (self.agent_owner(owner_dir), by) if p}

    def run_visible(self, kind: str, owner_dir: str, record: dict) -> bool:
        return self.operator or self.pid in self.run_principals(kind, owner_dir, record)

    def adapter_agent_visible(self, agent_id: str) -> bool:
        return self.operator or self.adapter_agent_owner(agent_id) == self.pid

    def firing_visible(self, f: dict) -> bool:
        if self.operator:
            return True
        sid = f.get("spec_id")
        if not isinstance(sid, str) or not sid:
            return False
        if f.get("spec_kind") == "adapter_agent":
            return self.adapter_agent_owner(sid) == self.pid
        return self.agent_owner(sid) == self.pid or f.get("by") == self.pid

    def spec_visible(self, spec_kind: Any, spec_id: str) -> bool:
        if self.operator:
            return True
        if spec_kind == "adapter_agent":
            return self.adapter_agent_owner(spec_id) == self.pid
        return self.agent_owner(spec_id) == self.pid

    # -- pushdown: what is not even opened ------------------------------------------------------
    def readable_dirs(self, kind: str) -> Optional[set[str]]:
        """The owner directories of ``kind`` that can hold a record this viewer may see; ``None``
        = any (an operator; or a workflow agent, whose runs a non-owner may have started)."""
        if self.operator or kind == "agent":
            return None
        if kind == "federated":
            return {self.pid}
        base = self.sources.runs_dir / kind
        if not base.is_dir():
            return set()
        out: set[str] = set()
        # bounded (Codex f2 #6): at most `MAX_OWNER_DIRS` creators are looked up per request
        for n, d in enumerate(base.iterdir()):
            if n >= MAX_OWNER_DIRS:
                self.dirs_cut.add(kind)
                break
            if d.is_dir() and self.adapter_agent_owner(d.name) == self.pid:
                out.add(d.name)
        return out

    def namespace_owned(self, kind: str, owner_dir: str) -> bool:
        """For diagnostics BEFORE a record is read: is this namespace known to be the viewer's?"""
        if self.operator:
            return True
        if kind == "federated":
            return owner_dir == self.pid
        if kind == "adapter_agent":
            return self.adapter_agent_owner(owner_dir) == self.pid
        return self.agent_owner(owner_dir) == self.pid


# ================================================================================================
# the trace join (tokens + the per-request view's request_id), keyed by namespace
# ================================================================================================
def trace_index(app: Any, viewer: Viewer) -> dict[tuple[str, str, str], list[dict]]:
    """``(kind, owner_dir, run_id)`` -> ``[{request_id, tokens, owner}]`` (newest first) for every
    trace the recorder still holds that THIS viewer may open at ``/trace/{id}``. Read-only.

    A ``run_record`` note is the record's path, so it names ``<kind>/<owner_dir>/<run_id>``; a
    ``federated_run`` note names only the run id, and a federated run is written under its asker,
    who is the trace's owner."""
    try:
        from .trace_recorder import TraceRecorder                          # noqa: PLC0415
    except Exception:                                                     # noqa: BLE001
        return {}
    recorder = getattr(app.state, "traces", None)
    if not isinstance(recorder, TraceRecorder):
        return {}
    out: dict[tuple[str, str, str], list[dict]] = {}
    listing = recorder.list_for(viewer.pid, admin=viewer.trace_admin, limit=MAX_TRACE_SCAN)
    for row in listing.get("traces", []):
        trace = recorder.get_for(row["request_id"], viewer.pid, admin=viewer.trace_admin)
        if trace is None:
            continue
        key = None
        tokens: list[int] = []
        for step in trace.steps:
            if step.kind == "note" and step.detail.get("key") == "run_record":
                path = Path(str(step.detail.get("text") or ""))
                key = (path.parent.parent.name, path.parent.name, path.stem)
            elif step.kind == "note" and step.detail.get("key") == "federated_run":
                text = str(step.detail.get("text") or "")
                key = ("federated", trace.owner or "", text)
            elif step.kind == "cost" and step.detail.get("llm_tokens") is not None:
                tokens.append(int(step.detail["llm_tokens"]))
        if key and all(key):
            out.setdefault(key, []).append({"request_id": trace.request_id, "owner": trace.owner,
                                            "tokens": sum(tokens) if tokens else None})
    return out


# ================================================================================================
# record -> timeline item
# ================================================================================================
def _base(kind: str, key: str, at: Any, **rest: Any) -> dict:
    item = {"id": f"{kind}:{key}", "kind": kind, "at": _scalar(at), "sort_key": sort_key(at)}
    item.update(rest)
    item["cursor"] = f"{item['sort_key']}|{item['id']}"
    return item


def _started_by(trig: dict) -> Optional[dict]:
    """A person (``trigger.by``), an automation (a recorded trigger ``type`` other than manual and
    no person), or ``None`` — NOT RECORDED — when the record names neither (a missing trigger is
    not evidence that an automation started it)."""
    by = trig.get("by") if isinstance(trig.get("by"), str) and trig.get("by") else None
    if by:
        return {"type": "person", "id": by}
    t = trig.get("type")
    if isinstance(t, str) and t and t != "manual":
        return {"type": "automation", "id": t}
    return None


def _trigger_view(trig: dict) -> dict:
    return {k: _scalar(trig.get(k)) for k in ("type", "by", "scheduled_for", "event_id",
                                              "fired_at") if k in trig}


def _action_counts(record: dict) -> Optional[dict]:
    """Actions by status, or None when the record carries no ``actions`` list (not recorded)."""
    if not isinstance(record.get("actions"), list):
        return None
    counts: dict[str, int] = {}
    for a in record["actions"]:
        if isinstance(a, dict):
            s = str(a.get("status"))
            counts[s] = counts.get(s, 0) + 1
    return counts


def _coverage_reduced(record: dict) -> Optional[bool]:
    cov = record.get("coverage")
    if not isinstance(cov, dict) or ("skipped" not in cov and "reasons" not in cov):
        return None
    return bool(cov.get("skipped") or cov.get("reasons"))


def _metered(value: Any, kind: type) -> Any:
    """A run header's metered figure, only when it measured something: a positive number.

    `RunRecord` defaults `tokens`/`cost_usd` to 0, and writers that do not meter leave that default
    in place — so a zero is indistinguishable from "not measured" and is never served as a
    measurement (missing stays null, never zero)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return kind(value) if value > 0 else None


def run_usage(record: dict, joined: dict) -> dict:
    """``tokens`` / ``cost_usd`` for a run row, and where they came from.

    The run record's own header (EF4, §5.8: the planner's metered totals) wins; without it the
    tokens the recorder's trace measured are used (the trace carries no cost). ``usage_source``
    says which: ``run_record`` · ``trace`` · None (nothing measured)."""
    tokens, cost = _metered(record.get("tokens"), int), _metered(record.get("cost_usd"), float)
    if tokens is not None or cost is not None:
        return {"tokens": tokens, "cost_usd": cost, "usage_source": "run_record"}
    trace_tokens = joined.get("tokens")          # a trace `cost` step is a measurement, even 0
    if isinstance(trace_tokens, int) and not isinstance(trace_tokens, bool):
        return {"tokens": trace_tokens, "cost_usd": None, "usage_source": "trace"}
    return {"tokens": None, "cost_usd": None, "usage_source": None}


def run_item(kind: str, owner_dir: str, record: dict,
             traces: dict[tuple[str, str, str], list[dict]],
             principals: Iterable[str] = ()) -> dict:
    """One run record (agent | federated | adapter_agent) as a timeline row.

    Its id and every link to it carry the namespace ``(kind, owner_dir, run_id)``. A trace joins
    only when it names THIS namespace and is owned by one of the run's ``principals``."""
    trig = record.get("trigger") if isinstance(record.get("trigger"), dict) else {}
    run_id = str(record.get("run_id") or "")
    status = _scalar(record.get("status"))
    owners = set(principals)
    joined = next((t for t in traces.get((kind, owner_dir, run_id), [])
                   if t.get("owner") in owners), {})
    answer = record.get("answer") if isinstance(record.get("answer"), dict) else None
    models = _scalars(record.get("models_used"))
    steps_recorded = isinstance(record.get("steps"), list)
    steps = record["steps"] if steps_recorded else []
    reduced = _coverage_reduced(record)
    if kind == "agent":
        item_kind, actor = "agent_run", {"type": "agent", "id": owner_dir}
        title = _scalar(trig.get("question")) or f"{owner_dir} run"
        pb = _produced_by(answer.get("produced_by")) if answer else None
    elif kind == "federated":
        item_kind, actor = "federated_run", {"type": "agent", "id": "federated"}
        title = _scalar(trig.get("question")) or "federated question"
        pb = _produced_by(answer.get("produced_by")) if answer else None
    else:
        agent_id = _scalar(record.get("agent_id")) or owner_dir
        item_kind, actor = "adapter_sync", {"type": "adapter_agent", "id": agent_id}
        title = f"sync of {agent_id}"
        pb = _produced_by(record.get("produced_by"))
    delegations = [s for s in steps if isinstance(s, dict)
                   and str(s.get("tool") or "").startswith("delegate.ask")]
    refused = [s for s in delegations
               if (s.get("policy_decision") or {}).get("decision") == "refuse"
               or (s.get("policy_decision") or {}).get("reason_code") == "failed"]
    loud = (status in _LOUD_RUN) or bool(reduced) or bool(refused)
    return _base(
        item_kind, f"{owner_dir}/{run_id}", record.get("started"),
        run_id=run_id, run_kind=kind, run_owner=owner_dir,
        started=_scalar(record.get("started")), ended=_scalar(record.get("ended")),
        duration_ms=duration_ms(record.get("started"), record.get("ended")),
        status=status, actor=actor, started_by=_started_by(trig), trigger=_trigger_view(trig),
        title=str(title)[:300], produced_by=pb, models_used=models,
        steps=len(steps) if steps_recorded else None, actions=_action_counts(record),
        delegations=len(delegations) if delegations else None,
        refused_delegations=len(refused) if delegations else None,
        coverage_reduced=reduced,
        reason=_scalar(record.get("reason")) if kind == "adapter_agent" else None,
        **run_usage(record, joined), trace_request_id=joined.get("request_id"),
        loud=loud)


#: A firing's fields that never change across its lines. A line that contradicts one is NOT
#: folded into the firing (Codex f2 #1); the conflict is listed on the firing instead.
_FIRING_IMMUTABLE = ("firing_id", "trigger", "scheduled_for", "event_id", "fired_at", "alias_of")
#: Event fields served, each a scalar. ``reason`` is served only when code-shaped; a producer's
#: ``error`` TEXT is never served — only the exception type it names (Codex f2 #9).
_FIRING_EVENT_KEYS = ("status", "recorded_at", "attempt", "next_attempt_at", "final")


def _spec_kind(value: Any) -> str:
    """The namespace a firing's spec lives in: an adapter agent, or (anything else) an agent —
    the same split the visibility rule and the run link use."""
    return "adapter_agent" if value == "adapter_agent" else "agent"


def firing_namespace(f: dict) -> str:
    """The id of a firing row: ``<spec kind>/<spec id>/<idempotency key>``."""
    return f"{_spec_kind(f.get('spec_kind'))}/{f.get('spec_id') or '-'}/{f['idempotency_key']}"


def fold_firings(records: Iterable[dict]) -> list[dict]:
    """`firings.jsonl` lines -> one firing per ``(spec kind, spec id, idempotency key)``, with
    every line as history. ``by`` is read from the ``trigger_info`` the engine wrote on the
    pending line.

    Codex f2 #1: an idempotency key alone is NOT a firing's identity. Two specs (two owners) can
    write the same key; folding by the key let one owner's firing carry the other's status, errors
    and result. Lines are folded only within their own spec's namespace, and a line that
    contradicts a field already recorded for the firing (another ``firing_id``, ``fired_at`` …) is
    not merged: the firing lists the conflict in ``conflicts`` and is loud."""
    by_key: dict[tuple, dict] = {}
    for rec in records:
        key = rec.get("idempotency_key")
        if not isinstance(key, str) or not key:
            continue
        sid = rec.get("spec_id")
        sid = sid if isinstance(sid, str) and sid else None
        ident = (_spec_kind(rec.get("spec_kind")), sid, key)
        f = by_key.setdefault(ident, {"idempotency_key": key, "events": [], "firing_id": None,
                                      "spec_id": sid, "spec_kind": None, "trigger": None,
                                      "scheduled_for": None, "event_id": None, "fired_at": None,
                                      "by": None, "result": None, "alias_of": None,
                                      "conflicts": []})
        clash = [k for k in _FIRING_IMMUTABLE
                 if f[k] is not None and _scalar(rec.get(k)) is not None
                 and _scalar(rec.get(k)) != f[k]]
        if clash:
            f["conflicts"].append({"recorded_at": _scalar(rec.get("recorded_at")),
                                   "fields": clash})
            continue
        for k in _FIRING_IMMUTABLE + ("spec_kind",):
            if f[k] is None and _scalar(rec.get(k)) is not None:
                f[k] = _scalar(rec.get(k))
        info = rec.get("trigger_info") if isinstance(rec.get("trigger_info"), dict) else {}
        if f["by"] is None and isinstance(info.get("by"), str) and info.get("by"):
            f["by"] = info["by"]
        if rec.get("result") is not None:
            f["result"] = rec.get("result")
        event = {k: _scalar(rec.get(k)) for k in _FIRING_EVENT_KEYS
                 if _scalar(rec.get(k)) is not None}
        if _code(rec.get("reason")):
            event["reason"] = rec["reason"]
        elif rec.get("reason") is not None:
            event["reason_withheld"] = "free text; not served"
        if rec.get("error") is not None:
            event["error_type"] = _error_type(rec.get("error"))
            event["error_withheld"] = "error text; not served"
        f["events"].append(event)
    out = []
    for f in by_key.values():
        f["status"] = f["events"][-1].get("status") if f["events"] else None
        f["first_recorded_at"] = next((e.get("recorded_at") for e in f["events"]
                                       if e.get("recorded_at")), None)
        f["last_recorded_at"] = next((e.get("recorded_at") for e in reversed(f["events"])
                                      if e.get("recorded_at")), None)
        out.append(f)
    return out


def _firing_run(f: dict) -> tuple[Optional[str], str, Optional[str]]:
    """``(run_id, run_kind, run_owner)`` of the run a firing produced: in its spec's namespace."""
    run_id = f.get("result") if isinstance(f.get("result"), str) else None
    kind = "adapter_agent" if f.get("spec_kind") == "adapter_agent" else "agent"
    owner = f.get("spec_id") if isinstance(f.get("spec_id"), str) else None
    return run_id, kind, owner


def firing_item(f: dict) -> dict:
    at = f.get("fired_at") or f.get("first_recorded_at")
    run_id, run_kind, run_owner = _firing_run(f)
    return _base(
        "firing", firing_namespace(f), at,
        idempotency_key=f["idempotency_key"], firing_id=f.get("firing_id"),
        spec_id=f.get("spec_id"), spec_kind=f.get("spec_kind"), trigger_type=f.get("trigger"),
        scheduled_for=f.get("scheduled_for"), event_id=f.get("event_id"),
        status=f.get("status"),
        actor={"type": "automation", "id": f.get("spec_id")},
        started_by=({"type": "person", "id": f["by"]} if f.get("by")
                    else {"type": "automation", "id": f.get("trigger")} if f.get("trigger")
                    else None),
        title=f"{f.get('trigger') or 'firing'} of {_nr(f.get('spec_id'))}",
        run_id=run_id, run_kind=run_kind, run_owner=run_owner,
        duration_ms=duration_ms(f.get("first_recorded_at"), f.get("last_recorded_at"))
        if len(f.get("events") or []) > 1 else None,
        events=f.get("events"), alias_of=f.get("alias_of"),
        conflicts=f.get("conflicts") or None,
        produced_by=None, models_used=None, tokens=None,
        loud=f.get("status") in _LOUD_FIRING or bool(f.get("conflicts")))


#: Human decisions in an `.actions.jsonl`, and the outcome word each one leaves.
_DECISIONS = {"approved", "retry_approved", "rejected", "reversed"}
_OUTCOMES = {"executed": "executed", "execution_failed": "execution_failed",
             "rejected": "rejected", "reversed": "reversed"}
#: agent_runtime's `_log_refusal`: every refused approve / reject / reverse (who asked, the reason
#: code, when). It decides nothing, so it never names a decider and never overrides a terminal
#: outcome; on a still-open action whose latest attempt was refused, the row's outcome is REFUSED.
_REFUSED = "decision_refused"
#: ``detail`` (the runtime's sentence) is NOT served (Codex f2 #9): ``reason_code`` says why.
_REFUSAL_KEYS = ("decision", "reason_code", "by", "claimed_by", "at")
#: Keys of one action-log / history event that are served (scalars only). ``detail`` and every
#: payload (args, result pointers, rows) are NOT: read them where their own route masks them.
HISTORY_KEYS = ("event", "status", "by", "at", "recorded_at", "reason", "note", "outcome",
                "idempotency_key", "decision", "reason_code", "claimed_by", "action_id", "name",
                "tool", "automation_mode", "stage", "action", "attempt")


def public_history(events: Optional[list]) -> list[dict]:
    """Action-log / history events: `HISTORY_KEYS` only, scalars only."""
    return [{k: _scalar(e.get(k)) for k in HISTORY_KEYS if _scalar(e.get(k)) is not None}
            for e in (events or []) if isinstance(e, dict)]


def approval_items(run_id: str, agent: str, events: list[dict], record: dict) -> list[dict]:
    """One row per action of a run: its proposal, every human decision on it, and the outcome.

    The outcome vocabulary is ``executed | execution_failed | rejected | reversed``, plus
    ``approved`` (approved, no execution outcome recorded yet), ``awaiting_decision`` and
    ``refused``. A REFUSED decision (the runtime's ``ActionRefused``: stale authority, an
    unauthenticated asker, already decided) is logged as ``decision_refused``; every one is listed
    in ``refusals``. It is the row's outcome (with ``refusal`` = the latest) only while the action
    has no terminal outcome and no decision after it — a refused reject of an executed action
    leaves it executed."""
    by_action: dict[str, list[dict]] = {}
    for e in events:
        aid = e.get("action_id")
        if isinstance(aid, str):
            by_action.setdefault(aid, []).append(e)
    actions = record.get("actions") if isinstance(record.get("actions"), list) else []
    out = []
    for aid, evs in sorted(by_action.items()):
        proposal = next((e for e in evs if e.get("event") == "proposed"), {})
        decisions = [e for e in evs if e.get("event") in _DECISIONS]
        outcome_ev = next((e for e in reversed(evs) if e.get("event") in _OUTCOMES), None)
        refusals = [{k: _scalar(e.get(k)) for k in _REFUSAL_KEYS}
                    for e in evs if e.get("event") == _REFUSED]
        # the latest event that is a decision, a refusal of one, or an outcome (log order)
        latest = next((e for e in reversed(evs)
                       if e.get("event") in _DECISIONS or e.get("event") in _OUTCOMES
                       or e.get("event") == _REFUSED), None)
        refusal = None
        if outcome_ev is not None:
            outcome = _OUTCOMES[outcome_ev["event"]]
        elif latest is not None and latest.get("event") == _REFUSED:
            outcome, refusal = "refused", refusals[-1]
        elif decisions:
            outcome = "approved"
        else:
            outcome = "awaiting_decision"
        decider = next((e.get("by") for e in reversed(decisions)
                        if isinstance(e.get("by"), str) and e.get("by")), None)
        # the row sits at its LATEST recorded event: the outcome, else the decision, else the ask
        last = max((e for e in evs if parse_ts(e.get("at"))),
                   key=lambda e: sort_key(e.get("at")), default=proposal)
        try:
            entry = actions[int(aid[1:]) - 1] if aid[1:].isdigit() else {}
        except IndexError:
            entry = {}
        entry = entry if isinstance(entry, dict) else {}
        out.append(_base(
            "approval", f"{agent}/{run_id}/{aid}", last.get("at") or proposal.get("at"),
            run_id=run_id, run_kind="agent", run_owner=agent, action_id=aid, agent=agent,
            name=_scalar(proposal.get("name") or entry.get("name")),
            tool=_scalar(proposal.get("tool")),
            automation_mode=_scalar(proposal.get("automation_mode")
                                    or entry.get("automation_mode")),
            status=outcome, outcome=outcome,
            actor=({"type": "person", "id": decider} if decider
                   else {"type": "agent", "id": agent}),
            decided_by=decider, proposed_at=_scalar(proposal.get("at")),
            proposed_by=_produced_by(proposal.get("produced_by")),
            produced_by=_produced_by(proposal.get("produced_by")),
            title=f"{_scalar(proposal.get('name')) or aid} on {_nr(run_id)}",
            history=public_history(evs), tokens=None, models_used=None,
            refusals=refusals, refusal=refusal,
            duration_ms=duration_ms(proposal.get("at"), (outcome_ev or {}).get("at")),
            loud=outcome in ("execution_failed", "awaiting_decision", "refused")))
    return out


def drift_items(agent: str, records: dict[str, dict]) -> list[dict]:
    out = []
    for aid, rec in records.items():
        history = rec.get("history") if isinstance(rec.get("history"), list) else None
        # no `drifts` list recorded is "not recorded", not "0 drifts"; no classification is not
        # "0 asked"
        # Codex f2 #9: a drift's free-text ``detail`` is not served; its kind and unit say what
        drifts = ([{k: _scalar(d.get(k)) for k in ("kind", "unit", "subject_touching")}
                   for d in rec["drifts"] if isinstance(d, dict)]
                  if isinstance(rec.get("drifts"), list) else None)
        cls = rec.get("classification") if isinstance(rec.get("classification"), dict) else None
        asked = (sum(1 for c in cls.values() if isinstance(c, dict) and c.get("asked"))
                 if cls is not None else None)
        at = ((history[-1].get("at") if history and isinstance(history[-1], dict) else None)
              or rec.get("created_at") or rec.get("at"))
        status = _scalar(rec.get("status"))
        problems = rec.get("problems") if isinstance(rec.get("problems"), list) else None
        connection = _scalar(rec.get("connection"))
        out.append(_base(
            "drift_proposal", f"{agent}/{aid}", at,
            adaptation_id=aid, adapter_agent=agent, connection=connection,
            status=status, stage=_scalar(rec.get("stage")), action=_scalar(rec.get("action")),
            drifts=drifts, asked=asked, run_id=_scalar(rec.get("agent_run_id")),
            run_kind="adapter_agent", run_owner=agent,
            actor={"type": "adapter_agent", "id": agent},
            produced_by=_produced_by(rec.get("produced_by")),
            title=(f"{agent}: {len(drifts)} drift(s) on {_nr(connection)}" if drifts is not None
                   else f"{agent}: drift on {_nr(connection)} (drifts not recorded)"),
            history=public_history(history) if history is not None else None,
            created_at=_scalar(rec.get("created_at")),
            # the producer's problem sentences are not served (Codex f2 #9): how many there are
            problem_count=len(problems) if problems is not None else None,
            tokens=None, models_used=None, duration_ms=None,
            loud=status == "pending"))
    return out


def ontology_proposal_items(rows: list[dict]) -> list[dict]:
    """The adapter agents' filed ontology proposals, folded with their ``forwarded`` line.
    Evidence, examples and document lists are deliberately not carried."""
    filed: dict[str, dict] = {}
    forwards: dict[str, dict] = {}
    for rec in rows:
        pid = rec.get("proposal_id")
        if not isinstance(pid, str):
            continue
        if rec.get("event") == "forwarded":
            forwards[pid] = rec
        elif pid not in filed:
            filed[pid] = rec
    out = []
    for pid, rec in filed.items():
        fw = forwards.get(pid)
        agent = _scalar(rec.get("adapter_agent"))
        out.append(_base(
            "ontology_proposal", pid, rec.get("filed_at"),
            proposal_id=pid, adapter_agent=agent, axis=_scalar(rec.get("axis")),
            name=_scalar(rec.get("name")), pack=_scalar(rec.get("pack")),
            status="forwarded" if fw else _scalar(rec.get("status")),
            occurrences=_scalar(rec.get("occurrences")),
            document_count=_scalar(rec.get("document_count")),
            touches_subject=_scalar(rec.get("touches_subject")),
            requires_human_approval=_scalar(rec.get("requires_human_approval")),
            forwarded=({k: _scalar(fw.get(k)) for k in ("at", "by", "ontology_proposal_id",
                                                        "ontology_status")} if fw else None),
            actor={"type": "adapter_agent", "id": agent},
            produced_by=_produced_by(rec.get("produced_by")),
            title=(f"{_nr(agent)} proposes {_nr(rec.get('axis'))} kind "
                   f"{_nr(rec.get('name'))}"),
            run_id=None, tokens=None, models_used=None, duration_ms=None,
            loud=not fw))
    return out


# ================================================================================================
# the merge: bounded, pushed down, viewer-scoped
# ================================================================================================
@dataclass
class Collected:
    items: list[dict]
    firings: list[dict]
    problems: Problems
    #: kinds whose rows were NOT all read: their counts are lower bounds, not served as totals
    incomplete: set[str]
    #: a stream stopped at a full page: rows older than this page exist
    more: bool = False
    #: a stream stopped by the read budget: rows at or below this sort key were not read
    frontier: Optional[str] = None


def _cursor_parts(before: Optional[str]) -> Optional[tuple[str, str]]:
    if not before:
        return None
    key, _, ident = before.partition("|")
    return key, ident


def collect(app: Any, viewer: Viewer, kinds: Optional[set[str]] = None, *,
            limit: Optional[int] = None, before: Optional[str] = None) -> Collected:
    """Read only what the requested kinds need, only where the viewer can own something, and only
    as far as the page needs; return the rows this viewer may see (scalars, sorted)."""
    want = set(kinds or ACTIVITY_KINDS)
    src = viewer.sources
    problems = Problems(viewer.operator)
    incomplete: set[str] = set()
    items: list[dict] = []
    cursor = _cursor_parts(before)
    more = False
    frontier: Optional[str] = None
    traces: Optional[dict] = None

    for kind, feeds in _RUN_DIR_KINDS.items():
        stream = feeds & want
        if not stream:
            continue
        if traces is None:
            traces = trace_index(app, viewer)
        with_actions = kind == "agent" and "approval" in want
        qualifying: list[str] = []
        opened = 0
        spent = 0                     # bytes this stream has read (records + action logs)
        cut: list = []
        readable = viewer.readable_dirs(kind)
        files = _run_files(src.runs_dir, kind, readable, with_actions=with_actions, cut=cut)
        if cut or kind in viewer.dirs_cut:
            # the listing itself was bounded: rows beyond it exist unread (Codex f2 #6)
            incomplete |= stream
            # how busy the store is, is a fact about other principals: operator-only (f1 #8)
            problems.report(False, {"source": kind, "file": None,
                                    "error": "more run files than one request lists; the feed "
                                             "shows the ones it listed and its counts are not "
                                             "totals"})
        for f in files:
            if cursor and cursor[0] and f.lower is not None and f.lower > cursor[0]:
                incomplete |= stream                  # every row of it sorts before the cursor
                continue
            if limit is not None and len(qualifying) >= limit:
                qualifying.sort(reverse=True)
                if f.upper < qualifying[limit - 1]:   # nothing left can reach this page
                    more = True
                    incomplete |= stream
                    break
            log_path = f.path.with_name(f.path.name[: -len(".json")] + ".actions.jsonl")
            cost = _cost(f.path, MAX_RECORD_BYTES) + (
                _cost(log_path, MAX_JSONL_BYTES) if with_actions else 0)
            over = bool(opened) and spent + cost > MAX_BYTES_PER_KIND
            # the byte budget stops a stream only once a row ABOVE this file is held, so the page
            # it cuts is never empty and paging always advances; past `BYTE_BUDGET_CEILING` times
            # the budget it stops regardless (and a page that cannot advance says so)
            progress = any(q > f.upper for q in qualifying)
            ceiling = bool(opened) and spent + cost > BYTE_BUDGET_CEILING * MAX_BYTES_PER_KIND
            if opened >= MAX_RECORDS_OPENED or (over and progress) or ceiling:
                # the open budget or the BYTE budget (Codex f2 #6) ends the stream: the page is
                # cut at this frontier and the cursor resumes here
                frontier = max(frontier or "", f.upper)
                incomplete |= stream
                break
            opened += 1
            spent += cost
            owned = viewer.namespace_owned(kind, f.owner)
            record = _read_json(f.path, problems, visible=owned)
            if record is None or not identity_matches(kind, f.owner, f.path, record, problems,
                                                      visible=owned):
                continue
            if not viewer.run_visible(kind, f.owner, record):
                continue
            item = run_item(kind, f.owner, record, traces,
                            viewer.run_principals(kind, f.owner, record))
            rows = [item] if item["kind"] in want else []
            if with_actions:
                events, lossy = _read_jsonl(log_path, problems, "agent",
                                            visible=viewer.namespace_owned(kind, f.owner))
                if lossy:
                    incomplete.add("approval")
                rows.extend(approval_items(item["run_id"], f.owner, events, record))
            for r in rows:
                items.append(r)
                if cursor is None or (r["sort_key"], r["id"]) < cursor:
                    qualifying.append(r["sort_key"])

    firings: list[dict] = []
    if "firing" in want:
        raw, lossy = _read_jsonl(src.firings_path, problems, "automation", visible=False)
        if lossy:
            incomplete.add("firing")
        firings = [f for f in fold_firings(raw) if viewer.firing_visible(f)]
        items.extend(firing_item(f) for f in firings)

    adapt_root = src.adapter_agents_dir / "_adapt"
    if "drift_proposal" in want and adapt_root.is_dir():
        try:
            from databridge_adapter_agents.adapt import AdaptStore        # noqa: PLC0415
            store = AdaptStore(lambda: adapt_root)
        except Exception as exc:                                          # noqa: BLE001
            store = None
            problems.add({"source": "adapter_agents", "file": None,
                          "error": f"drift reader unavailable: {type(exc).__name__}"})
        drift_dirs: list[Path] = []
        for n, d in enumerate(adapt_root.iterdir()):
            if n >= MAX_OWNER_DIRS:          # bounded (Codex f2 #6)
                incomplete.add("drift_proposal")
                problems.report(False, {"source": "adapter_agents", "file": None,
                                        "error": "more adapter-agent drift logs than one request "
                                                 "reads; the drift proposals shown are not all "
                                                 "of them"})
                break
            if d.is_dir() and not d.name.startswith(("_", ".")):
                drift_dirs.append(d)
        drift_spent = 0
        for d in sorted(drift_dirs):
            if store is None or not viewer.adapter_agent_visible(d.name):
                continue
            log_file = d / "adaptations.jsonl"
            try:
                size = log_file.stat().st_size if log_file.is_file() else 0
                too_big = size > MAX_JSONL_BYTES
            except OSError:
                size, too_big = 0, True
            if not too_big and drift_spent + size > MAX_BYTES_PER_KIND:
                incomplete.add("drift_proposal")
                problems.add({"source": "adapter_agents", "file": f"{d.name}/adaptations.jsonl",
                              "error": f"not read: this request already read "
                                       f"{MAX_BYTES_PER_KIND} bytes of drift logs"})
                continue
            drift_spent += size
            if too_big:
                problems.add({"source": "adapter_agents", "file": f"{d.name}/adaptations.jsonl",
                              "error": f"larger than {MAX_JSONL_BYTES} bytes; not read"})
                incomplete.add("drift_proposal")
                continue
            try:
                recs = store.records(d.name)
            except Exception as exc:                                      # noqa: BLE001
                problems.add({"source": "adapter_agents", "file": f"{d.name}/adaptations.jsonl",
                              "error": f"unreadable: {type(exc).__name__}"})
                incomplete.add("drift_proposal")
                continue
            items.extend(drift_items(d.name, recs))

    if "ontology_proposal" in want:
        rows, lossy = _read_jsonl(
            src.adapter_agents_dir / "_proposals" / "ontology_proposals.jsonl",
            problems, "adapter_agents", visible=False)
        if lossy:
            incomplete.add("ontology_proposal")
        for it in ontology_proposal_items(rows):
            agent = it.get("adapter_agent")
            if isinstance(agent, str) and viewer.adapter_agent_visible(agent):
                items.append(it)

    items = [scrub(it) for it in items]
    items.sort(key=lambda i: (i["sort_key"], i["id"]), reverse=True)
    return Collected(items, firings, problems, incomplete, more, frontier)


def _cost(path: Path, cap: int) -> int:
    """Bytes reading ``path`` would take (at most ``cap``: records over it are not read, logs are
    read from their tail within it); 0 when it does not exist."""
    try:
        return min(path.stat().st_size, cap) if path.is_file() else 0
    except OSError:
        return 0


def paginate(items: list[dict], limit: int, before: Optional[str], *, more: bool = False,
             frontier: Optional[str] = None) -> tuple[list[dict], Optional[str]]:
    """Items are sorted by ``(sort_key, id)`` descending; ``before`` is the last row's cursor.

    ``more``: a stream stopped once it held a full page, so older rows exist even when exactly
    ``limit`` were read — the page still returns a cursor. ``frontier``: a stream stopped on its
    read budget; only rows newer than the frontier are certain, so the page ends there and the
    cursor resumes at the frontier."""
    cur = _cursor_parts(before)
    if cur:
        items = [i for i in items if (i["sort_key"], i["id"]) < cur]
    safe = [i for i in items if i["sort_key"] > frontier] if frontier is not None else items
    page = safe[:limit]
    if page and (len(safe) > limit or (more and len(page) == limit)):
        return page, page[-1]["cursor"]
    if frontier is not None:
        return page, f"{frontier}|{FRONTIER_ID}"
    return page, None


# ================================================================================================
# redaction (Codex f1 #5): an allow-list per record kind, recursive, masked by default
# ================================================================================================
#: Keys never served at any depth, whatever schema they sit in.
MASKED_KEYS = frozenset({
    "evidence", "excerpt", "excerpts", "examples", "example", "documents", "document_list",
    "document_ids", "documents_seen", "candidates", "candidate", "_candidate",
    "subject_candidates", "candidate_subjects", "withheld", "withheld_value", "withheld_values",
    "raw", "raw_value", "raw_values", "sample", "samples", "sample_values", "sample_row_ids",
    "secret", "payload", "args", "password", "token", "api_key"})
#: How deep a free-form value may nest before the rest is masked.
MAX_DEPTH = 8

# schema leaf types
S, SLIST, SVAL, SMAP, SMAP_LISTS, PB, PD, COUNT, PTR, OPS, SKIP = (
    "s", "slist", "sval", "smap", "smap_lists", "pb", "pd", "count", "ptr", "ops", "skip")

TRIGGER = {k: S for k in ("type", "by", "question", "scheduled_for", "event_id", "fired_at",
                          "firing_id", "idempotency_key")}
SKIP_ENTRY = {k: S for k in ("tool", "adapter_agent", "connection", "channel", "reason",
                             "reason_code", "recovered", "action", "source")}
POINTER_KEYS = ("source_id", "kind", "trust", "capture_digest", "table", "column", "row_id",
                "document_id", "char_start", "char_end", "timestamp_ms", "segment_idx",
                "file_path", "ast_node_id", "query_hash", "row_ordinal", "workbook_sha", "sheet",
                "cell", "cell_range", "media_id", "page", "x0", "y0", "x1", "y1", "ocr_conf",
                "message_id", "part", "claim_id", "derivation", "agg_window", "predicate",
                "row_count", "rows_digest", "ref", "pointer_id", "connection")
FILTER = {"field": S, "op": S, "value": SVAL}
AGGREGATE = {"op": S, "field": S, "group_by": SLIST}
TIME_WINDOW = {"axis": S, "field": S, "period": S, "from": S, "from_": S, "to": S}
REQUEST_SCHEMA = {"entity_kinds": SLIST, "filters": [FILTER], "fields": SLIST,
                  "time_window": TIME_WINDOW, "aggregate": AGGREGATE, "limit": S,
                  "freshness": S, "link_edges": SLIST}
COMBINE_SCHEMA = {
    "base": S, "filters": [FILTER], "group_by": SLIST, "aggregate": AGGREGATE,
    "links": [{"subtask": S, "key": S, "entity_kind": S, "other_field": S, "key_kind": S,
               "via": S, "max_hops": S, "edge_kinds": SLIST, "mode": S, "carry": SLIST}],
    "compare": {"axis": S, "field": S, "periods": SLIST},
    "order_by": S, "descending": S, "top_k": S, "single_source": S}
#: The keys of one combine operation (`databridge_federated.combine`'s ``steps``).
OPS_KEYS = frozenset({
    "step", "mode", "via", "hops", "on", "rows_in", "rows_out", "identifiers_on_other_side",
    "carry", "ambiguous_lookups", "entities_without_identifier", "filter", "by", "descending",
    "axis", "field", "periods", "rows_outside_periods", "rows_with_unknown_date", "groups", "op",
    "arithmetic", "method"})
CLAIM = {"text": S, "reason": S, "supported": S, "claim_id": S, "refs": SLIST,
         "citations": COUNT}

_RUN_57 = {
    "run_id": S, "spec_digest": S, "status": S, "started": S, "ended": S,
    "models_used": SLIST, "tokens": S, "cost_usd": S, "trigger": TRIGGER,
    "steps": [{"tool": S, "ms": S, "status": S, "reason": S, "policy_decision": PD,
               "produced_by": PB, "result_pointers": COUNT}],
    "actions": [{"name": S, "automation_mode": S, "status": S, "by": S, "pointers": COUNT}],
    "answer": {"markdown": S, "produced_by": PB, "citations": COUNT, "citation_refs": COUNT,
               "unsupported_claims": COUNT, "claims": COUNT},
    "coverage": {"searched": SLIST, "skipped": [SKIP], "reasons": SMAP},
    "outcome": {"reason_code": S, "human_confirmation_required": S, "coverage_status": S,
                "candidates": COUNT, "withheld": COUNT, "not_connected": SLIST},
}
#: The run record as served, per run kind. A key not named here is NOT served (its path goes in
#: ``masked_fields``). ``COUNT`` serves a list as its length.
RECORD_SCHEMAS: dict[str, dict] = {
    "agent": _RUN_57,
    "federated": _RUN_57,
    "adapter_agent": {
        "run_id": S, "kind": S, "agent_id": S, "spec_digest": S, "status": S, "reason": S,
        "started": S, "ended": S, "models_used": SLIST, "trigger": TRIGGER, "produced_by": PB,
        "steps": [{"tool": S, "ms": S, "status": S, "reason": S, "ingest_run_id": S,
                   "report_status": S, "started": S, "ended": S, "event_id": S,
                   "policy_decision": PD, "produced_by": PB, "result_pointers": COUNT,
                   "counts": SMAP}],
        "actions": COUNT, "answer": S,
        "coverage": {"searched": SLIST, "skipped": [SKIP], "reasons": SMAP},
        "totals": SMAP, "warnings": SLIST,
        "proposals": {"filed": COUNT, "already_filed": COUNT, "below_threshold": COUNT,
                      "error": S, "vocabulary_problem": S},
    },
}


class _Masked:
    pass


_MASK = _Masked()


def scrub(value: Any, _depth: int = 0) -> Any:
    """Schema-independent guard: scalars kept (strings cut), dicts and lists walked, any key in
    `MASKED_KEYS` dropped at ANY depth, anything deeper than `MAX_DEPTH` dropped."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_STR]
    if _depth >= MAX_DEPTH:
        return None
    if isinstance(value, dict):
        return {str(k): scrub(v, _depth + 1) for k, v in value.items()
                if str(k).lower() not in MASKED_KEYS}
    if isinstance(value, (list, tuple)):
        return [scrub(v, _depth + 1) for v in value]
    return None


def _is_scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def _project(value: Any, schema: Any, path: str, masked: set[str], depth: int = 0) -> Any:
    """`value` through `schema`; every path not served goes in `masked`. Returns `_MASK` for a
    value that is not served at all."""
    if depth > MAX_DEPTH:
        masked.add(path)
        return _MASK
    if schema == PB:
        return _produced_by(value)
    if schema == COUNT:
        if isinstance(value, list):
            return len(value)
        if value is None:
            return None
        masked.add(path)
        return _MASK
    if schema == S:
        if _is_scalar(value):
            if isinstance(value, str) and len(value) > MAX_STR:
                masked.add(f"{path} (truncated)")
            return _scalar(value)
        masked.add(path)
        return _MASK
    if schema in (SLIST, SVAL):
        if schema == SVAL and _is_scalar(value):
            return _scalar(value)
        if value is None:
            return None
        if isinstance(value, list) and all(_is_scalar(x) for x in value):
            return [_scalar(x) for x in value]
        masked.add(path)
        return _MASK
    if schema == SMAP:
        if value is None:
            return None
        if isinstance(value, list):
            return _project(value, SLIST, path, masked, depth)
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                if str(k).lower() in MASKED_KEYS or not _is_scalar(v):
                    masked.add(f"{path}.{k}")
                    continue
                out[str(k)] = _scalar(v)
            return out
        masked.add(path)
        return _MASK
    if schema == PD:                    # a dict on §5.7 steps, a sentence on adapter syncs
        # Codex f2 #9: the decision and its code are served; the sentence (``detail`` on §5.7
        # steps, the whole value on adapter syncs) is free text a delegate wrote and is NOT — it
        # can quote what the allow-list never saw. The tree serves its SQL only when it holds no
        # literal (`_literal_free_sql`).
        if value is None:
            return None
        if isinstance(value, str):
            masked.add(path)
            return _MASK
        return _project(value, {"decision": S, "reason_code": S}, path, masked, depth)
    if schema == SKIP:                  # a dict on §5.7 records, a connection name on syncs
        if _is_scalar(value):
            return _scalar(value)
        return _project(value, SKIP_ENTRY, path, masked, depth)
    if schema == PTR:
        if not isinstance(value, dict):
            masked.add(path)
            return _MASK
        out = {}
        for k, v in value.items():
            sub = f"{path}.{k}"
            if k == "pointer" or k == "child":
                got = _project(v, PTR, sub, masked, depth + 1)
            elif k in ("facts", "derived_from"):
                got = _project(v, [PTR], sub, masked, depth + 1)
            elif k in POINTER_KEYS and str(k).lower() not in MASKED_KEYS and _is_scalar(v):
                got = _scalar(v)
            else:
                masked.add(sub)
                continue
            if got is not _MASK:
                out[k] = got
        return out
    if schema == OPS:                   # a rules operation log: scalar data, two levels at most
        if not isinstance(value, list):
            return None if value is None else _MASK
        out_ops = []
        for v in value:
            if not isinstance(v, dict):
                masked.add(f"{path}[]")
                continue
            op = {}
            for k, x in v.items():
                sub = f"{path}[].{k}"
                # Codex f2 #9: the keys `databridge_federated.combine` writes, and no others
                if str(k).lower() in MASKED_KEYS or k not in OPS_KEYS:
                    masked.add(sub)
                elif _is_scalar(x):
                    op[str(k)] = _scalar(x)
                elif isinstance(x, list) and all(_is_scalar(y) for y in x):
                    op[str(k)] = [_scalar(y) for y in x]
                elif isinstance(x, dict):
                    got = _project(x, SMAP_LISTS, sub, masked, depth + 1)
                    if got is not _MASK:
                        op[str(k)] = got
                else:
                    masked.add(sub)
            out_ops.append(op)
        return out_ops
    if schema == SMAP_LISTS:            # {name: scalar | [scalars]}
        if not isinstance(value, dict):
            masked.add(path)
            return _MASK
        out = {}
        for k, x in value.items():
            if str(k).lower() in MASKED_KEYS:
                masked.add(f"{path}.{k}")
            elif _is_scalar(x):
                out[str(k)] = _scalar(x)
            elif isinstance(x, list) and all(_is_scalar(y) for y in x):
                out[str(k)] = [_scalar(y) for y in x]
            else:
                masked.add(f"{path}.{k}")
        return out
    if isinstance(schema, list):
        if value is None:
            return None
        if not isinstance(value, list):
            masked.add(path)
            return _MASK
        got = [_project(v, schema[0], f"{path}[]", masked, depth + 1) for v in value]
        return [g for g in got if g is not _MASK]
    if isinstance(schema, dict):
        if value is None:
            return None
        if not isinstance(value, dict):
            masked.add(path)
            return _MASK
        out = {}
        for k, v in value.items():
            sub = f"{path}.{k}" if path else str(k)
            # unknown => masked, by default; a deny-listed key is served only as a COUNT
            if k not in schema or (str(k).lower() in MASKED_KEYS and schema[k] != COUNT):
                masked.add(sub)
                continue
            got = _project(v, schema[k], sub, masked, depth + 1)
            if got is not _MASK:
                out[k] = got
        return out
    masked.add(path)
    return _MASK


def public_record(kind: str, record: dict) -> dict:
    """The run record as served: its kind's `RECORD_SCHEMAS` keys only, projected recursively; the
    path of every key that was NOT served is listed in ``masked_fields`` (sorted), so the reader
    knows something was withheld rather than believing the record held nothing more."""
    masked: set[str] = set()
    out = _project(record, RECORD_SCHEMAS.get(kind, {}), "", masked)
    out = out if isinstance(out, dict) else {}
    out["masked_fields"] = sorted(masked)
    return out


# ================================================================================================
# the delegation tree
# ================================================================================================
def _rows_returned(pointers: list, recorded: bool = True) -> tuple[Optional[int], str]:
    """The rows a delegate returned, counted from the IDENTITY its recorded pointers carry — never
    from a delegate's sentence. Each pointer shape names its own row unit:

    * a ``query_cell`` names a result row by ``(query_hash, row_ordinal)``;
    * an aggregate's ``derived`` pointer names its GROUP by ``rows_digest`` (and how many source
      rows the group covers by ``row_count``);
    * a ``table_cell`` names a row by ``(table, row_id)``;
    * a ``doc_span`` (directly, or as a ``derived`` pointer's parent) names a source document.

    E2E run 4 Q-11: an agent-run delegate's pointers are aggregate groups and document spans, so
    counting ``row_ordinal`` alone left every agent sub-task at "not recorded". An EMPTY recorded
    list is a recorded zero (no citable row came back); a list that was never recorded stays
    ``None``. The record keeps the pointers of at most 50 rows, so 50 or more means "at least 50"."""
    if not recorded:
        return None, "not recorded: this run record keeps no result pointers for the call"
    ptrs = [p for p in pointers if isinstance(p, dict)]
    if not ptrs:
        return 0, "no result pointer recorded: the delegate returned no citable row"
    rows: set = set()
    groups: dict = {}
    cells: set = set()
    docs: set = set()
    for p in ptrs:
        kind = p.get("kind")
        if kind == "query_cell" and p.get("row_ordinal") is not None:
            rows.add((str(p.get("query_hash") or ""), str(p.get("row_ordinal"))))
        elif kind == "derived" and p.get("rows_digest"):
            n = p.get("row_count")
            groups[str(p["rows_digest"])] = n if isinstance(n, int) and not isinstance(n, bool) \
                else None
        elif kind == "table_cell" and p.get("row_id") is not None:
            cells.add((str(p.get("table") or ""), str(p.get("row_id"))))
        for q in [p, *(p.get("derived_from") or [])]:
            if isinstance(q, dict) and q.get("kind") == "doc_span" and q.get("document_id"):
                docs.add(str(q["document_id"]))
    parts = []
    if rows:
        parts.append(f"{len(rows)} result row(s) by row ordinal")
    if groups:
        covered = sum(n for n in groups.values() if n is not None)
        known = all(n is not None for n in groups.values())
        parts.append(f"{len(groups)} aggregate group(s)"
                     + (f" over {covered} source row(s)" if known else ""))
    if cells:
        parts.append(f"{len(cells)} table row(s) by row id")
    # documents count only when the pointers name no row or group: a group's parent span is
    # where the group was read, not another returned row
    if docs and not (rows or groups or cells):
        parts.append(f"{len(docs)} source document(s)")
    total = (len(rows) + len(groups) + len(cells)) or len(docs)
    if not parts:
        return None, "not recorded: this delegate's pointers carry no row, group or document"
    if total >= 50:
        return None, "at least 50 (the run record keeps the pointers of 50 rows)"
    return total, "distinct " + "; ".join(parts) + " among the recorded pointers"


#: The parameter-count suffix the federated service appends to the SQL it ran.
_SQL_SUFFIX_RE = re.compile(r"\s+with \d+ bound parameter\(s\)(?: \(sha256 [0-9a-f]+…?\))?\s*$")
_SQL_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_\"])\d+(?:\.\d+)?(?![A-Za-z0-9_\"])")


def _literal_free_sql(sql: str) -> Optional[str]:
    """The SQL a delegate ran, served only when it carries NO literal (Codex f2 #9): every value
    is a bound ``?`` parameter. A quoted string or a bare number could be a value read from a
    source, so such SQL is not served (the request it was built from is, through the allow-list).
    Double-quoted names are identifiers, not values."""
    body = _SQL_SUFFIX_RE.sub("", sql)
    bare = re.sub(r'"[^"]*"', '""', body)
    if "'" in bare or _SQL_NUMBER_RE.search(bare):
        return None
    return sql


def delegation_tree(record: dict) -> Optional[dict]:
    """question -> plan -> one node per sub-task -> combine -> answer, from a §5.7 record.

    Built only from what the record holds, through the same allow-list projection as the record;
    everything the record does not hold is ``None`` with a note, so a missing piece reads as
    missing rather than empty. ``masked_fields`` lists what was not served."""
    masked: set[str] = set()

    def proj(value: Any, schema: Any, path: str) -> Any:
        got = _project(value, schema, path, masked)
        return None if got is _MASK else got

    steps = [s for s in (record.get("steps") or []) if isinstance(s, dict)] \
        if isinstance(record.get("steps"), list) else []
    plan_step = next((s for s in steps if s.get("tool") == "federated.plan"), None)
    delegations = [s for s in steps if str(s.get("tool") or "").startswith("delegate.ask")]
    if plan_step is None and not delegations:
        return None
    trig = record.get("trigger") if isinstance(record.get("trigger"), dict) else {}
    # coverage (and each of its lists) that was not recorded stays None — "skipped: none" and
    # "reduced: false" are claims the record did not make
    cov_recorded = isinstance(record.get("coverage"), dict)
    cov = record["coverage"] if cov_recorded else {}
    skipped_raw = cov.get("skipped")
    skipped_list = ([s for s in skipped_raw if isinstance(s, dict)]
                    if isinstance(skipped_raw, list) else None)
    skipped = skipped_list or []
    # Codex f2 #5: per sub-task skip information is a claim only when the record has a skipped list
    skip_recorded = skipped_list is not None
    reasons_raw = cov.get("reasons")
    reasons = ([str(r)[:MAX_STR] for r in reasons_raw if _is_scalar(r)]
               if isinstance(reasons_raw, list) else
               [f"{k}: {v}" for k, v in reasons_raw.items() if _is_scalar(v)]
               if isinstance(reasons_raw, dict) else None)
    searched = _scalars(cov.get("searched"))

    plan = None
    if plan_step is not None:
        pd = plan_step.get("policy_decision") if isinstance(plan_step.get("policy_decision"),
                                                            dict) else {}
        # E2E3 P-08: the federated service records the plan's assumptions and the questions it
        # asked as LISTS in the plan step's args. They are served through the allow-list as plain
        # string lists (`SLIST`: scalars only, each cut at MAX_STR); nothing else in ``args`` is.
        # The planner wrote them from the question, the catalogue's NAMES and the user's answers -
        # it never sees a source row - unlike a delegate's sentence. The step's ``detail``
        # sentence stays unserved (f2 #9): it is free text, not the recorded list.
        pargs = plan_step.get("args") if isinstance(plan_step.get("args"), dict) else {}
        assumptions = (proj(pargs["assumptions"], SLIST, "plan.assumptions")
                       if "assumptions" in pargs else None)
        questions = (proj(pargs["questions"], SLIST, "plan.questions")
                     if "questions" in pargs else None)
        if pd.get("detail") and questions is None:
            masked.add("plan.questions_text")       # the planner's sentence: free text (f2 #9)
        plan = {"method": _scalar(pd.get("reason_code")) or None,
                "questions": questions,
                "questions_text": ("; ".join(str(q) for q in questions) or None
                                   if questions is not None else None),
                "questions_note": ("no question was asked" if questions == [] else
                                   None if questions else
                                   "not recorded: this run record keeps no plan questions"),
                "assumptions": assumptions,
                "assumptions_note": ("the plan stated no assumption" if assumptions == [] else
                                     None if assumptions else
                                     "not recorded: this run record keeps no plan assumptions"),
                "ms": _scalar(plan_step.get("ms")),
                "produced_by": _produced_by(plan_step.get("produced_by"))}

    subtasks = []
    for i, s in enumerate(delegations):
        tool = str(s.get("tool"))
        args_recorded = isinstance(s.get("args"), dict)
        args = s["args"] if args_recorded else {}
        # E2E run 4 Q-11: an agent run records the sub-task it SENT nested under ``subtask``
        # (`agent_runtime.tools` writes the shaped sub-task back there); a federated run records
        # it flat. Both are the same SubTask contract and go through the same allow-list.
        if isinstance(args.get("subtask"), dict):
            args = {**{k: v for k, v in args.items() if k != "subtask"}, **args["subtask"]}
        agent = tool.partition(":")[2] or _scalar(args.get("adapter_agent"))
        pd = s.get("policy_decision") if isinstance(s.get("policy_decision"), dict) else {}
        method = _scalar(pd.get("reason_code")) or None
        mine = [k for k in skipped if k.get("tool") == tool]
        ptrs_recorded = isinstance(s.get("result_pointers"), list)
        ptrs = s["result_pointers"] if ptrs_recorded else []
        rows, rows_basis = _rows_returned(ptrs, ptrs_recorded)
        conns = sorted({str(p.get("source_id")) for p in ptrs
                        if isinstance(p, dict) and p.get("source_id")})
        pinned = _scalar(args.get("connection"))
        connection = pinned or next(
            (_scalar(k.get("connection")) for k in mine if k.get("connection")), None) or \
            (conns[0] if len(conns) == 1 else None)
        detail = str(_scalar(pd.get("detail")) or "")
        sql = _SQL_RE.search(detail)
        query = _literal_free_sql(sql.group(1).strip()) if sql else None
        if sql and query is None:
            masked.add(f"subtasks[{i}].query")
        if detail:
            masked.add(f"subtasks[{i}].delegate_note")
        refused = pd.get("decision") == "refuse"
        failed = method == "failed" or any(k.get("reason_code") == "delegate_failed"
                                           for k in mine)
        subtasks.append({
            "id": _scalar(args.get("id")), "adapter_agent": agent, "connection": connection,
            "connection_basis": ("the plan pinned it" if pinned else
                                 "the skipped read names it" if any(k.get("connection")
                                                                    for k in mine) else
                                 "the source of its recorded pointers" if connection else
                                 "not recorded"),
            # not recorded stays None — `{}` would claim an empty request (Codex f2 #5)
            "request": (proj({k: args[k] for k in REQUEST_SCHEMA if k in args}, REQUEST_SCHEMA,
                             f"subtasks[{i}].request") if args_recorded else None),
            "decision": _scalar(pd.get("decision")), "method": method,
            "pushed_down": (method == "facts_sql_pushdown") if method else None,
            "query": query,
            # the delegate's own sentence is free text and is not served (Codex f2 #9)
            "delegate_note": None,
            "rows_returned": rows, "rows_basis": rows_basis,
            "pointers_recorded": len(ptrs) if ptrs_recorded else None,
            "skipped": ([proj({k: v for k, v in sk.items() if k != "tool"}, SKIP_ENTRY,
                              f"subtasks[{i}].skipped[]") for sk in mine]
                        if skip_recorded else None),
            # "answered" claims nothing was skipped: only when the record says what was skipped
            "status": "refused" if refused else "failed" if failed else
                      "reduced" if mine else "answered" if skip_recorded else "not_recorded",
            "ms": _scalar(s.get("ms")),
            "produced_by": _produced_by(s.get("produced_by")),
        })

    combine = None
    cstep = next((s for s in steps if s.get("tool") == "federated.combine"), None)
    if cstep is not None:
        cargs = cstep.get("args") if isinstance(cstep.get("args"), dict) else None
        cpd = cstep.get("policy_decision") if isinstance(cstep.get("policy_decision"),
                                                         dict) else {}
        detail = cpd.get("detail")
        try:
            done = json.loads(detail) if isinstance(detail, str) and detail else None
        except ValueError:
            done = None
        failure = next((_scalar(k.get("reason")) for k in skipped
                        if k.get("tool") == "federated.combine"), None)
        cptrs = cstep.get("result_pointers")
        combine = {"spec": (proj(cargs, COMBINE_SCHEMA, "combine.spec")
                            if cargs is not None else None),
                   "steps": proj(done, OPS, "combine.steps") if isinstance(done, list) else None,
                   "failed": failure, "ms": _scalar(cstep.get("ms")),
                   "pointers_recorded": len(cptrs) if isinstance(cptrs, list) else None,
                   "produced_by": _produced_by(cstep.get("produced_by"))}

    nstep = next((s for s in steps if s.get("tool") == "federated.narrate"), None)
    answer = record.get("answer") if isinstance(record.get("answer"), dict) else {}
    citations = answer.get("citations")
    unsupported = answer.get("unsupported_claims")
    npd = (nstep.get("policy_decision") if nstep and isinstance(nstep.get("policy_decision"),
                                                                 dict) else {})
    return {
        "run_id": _scalar(record.get("run_id")), "question": _scalar(trig.get("question")),
        "asked_by": _scalar(trig.get("by")), "status": _scalar(record.get("status")),
        "started": _scalar(record.get("started")), "ended": _scalar(record.get("ended")),
        "duration_ms": duration_ms(record.get("started"), record.get("ended")),
        "models_used": _scalars(record.get("models_used")),
        "plan": plan,
        # an agent run delegates from its own planner's steps: there is no federated plan to show,
        # and saying so keeps a null plan from reading as a lost one (E2E run 4 Q-11)
        "plan_note": (None if plan is not None else
                      "no federated plan step: the agent's own planner chose each delegate call; "
                      "each call's sub-task is shown under it"),
        "subtasks": subtasks, "combine": combine,
        "narrate": ({"ms": _scalar(nstep.get("ms")), "method": _scalar(npd.get("reason_code")),
                     "produced_by": _produced_by(nstep.get("produced_by"))} if nstep else None),
        "answer": {"markdown": _scalar(answer.get("markdown")),
                   "citations": (proj(citations, [PTR], "answer.citations")
                                 if isinstance(citations, list) else None),
                   "unsupported_claims": (proj(unsupported, [CLAIM], "answer.unsupported_claims")
                                          if isinstance(unsupported, list) else None),
                   "produced_by": _produced_by(answer.get("produced_by"))},
        "coverage": {"searched": searched,
                     "skipped": (proj(skipped_list, [SKIP_ENTRY], "coverage.skipped")
                                 if skipped_list is not None else None),
                     "reasons": reasons,
                     # reduced is a claim only when the record recorded skipped reads or reasons
                     "reduced": (bool(skipped or reasons)
                                 if skipped_list is not None or reasons is not None else None)},
        "refused_subtasks": sum(1 for t in subtasks if t["status"] in ("refused", "failed")),
        "masked_fields": sorted(masked),
    }


# ================================================================================================
# the routes
# ================================================================================================
class OwnerRequired(Exception):
    """Too many owner directories to probe for one id: the caller must name the owner."""


def _run_owner_dirs(viewer: Viewer, kind: str, owner: Optional[str]) -> list[str]:
    """The owner directories of ``kind`` a run-detail request probes (one ``stat`` each). Bounded
    (Codex f2 #6): more than `MAX_OWNER_DIRS` raises `OwnerRequired` -- a refusal that depends only
    on the store's shape, never on the id, so it discloses nothing about any run."""
    base = viewer.sources.runs_dir / kind
    if not base.is_dir():
        return []
    allowed = viewer.readable_dirs(kind)
    if owner:
        names = [owner]
    elif allowed is not None:
        names = sorted(allowed)
    else:
        names = []
        for d in base.iterdir():
            if len(names) >= MAX_OWNER_DIRS:
                raise OwnerRequired(kind)
            if d.is_dir() and not d.name.startswith((".", "_")):
                names.append(d.name)
        names.sort()
    if len(names) > MAX_OWNER_DIRS or (not owner and kind in viewer.dirs_cut):
        raise OwnerRequired(kind)
    return [n for n in names if allowed is None or n in allowed]


def _open_run(viewer: Viewer, kind: str, owner_dir: str, run_id: str,
              problems: Problems) -> Optional[tuple[Path, dict]]:
    """The run at ``<kind>/<owner_dir>/<run_id>.json`` if this viewer may see it, else None."""
    path = viewer.sources.runs_dir / kind / owner_dir / f"{run_id}.json"
    if not path.is_file():
        return None
    record = _read_json(path, problems, visible=viewer.namespace_owned(kind, owner_dir))
    if record is None or not identity_matches(kind, owner_dir, path, record, problems,
                                              visible=viewer.namespace_owned(kind, owner_dir)):
        return None
    if not viewer.run_visible(kind, owner_dir, record):
        return None
    return path, record


def register_activity_routes(app: FastAPI) -> None:
    """Attach the three read routes. Nothing here writes."""
    install_auth_handler(app)

    def _viewer(principal: AuthenticatedPrincipal) -> Viewer:
        return Viewer(principal.principal_id, resolve_sources(app),
                      operator=agent_ownership.is_operator(principal),
                      trace_admin=has_audit_role(principal, "trace_admin"))

    @app.get("/activity")
    def activity(limit: int = DEFAULT_LIMIT, before: Optional[str] = None,
                 kinds: Optional[str] = None,
                 principal: AuthenticatedPrincipal = Depends(require_principal)):
        wanted: Optional[set[str]] = None
        if kinds:
            wanted = {k.strip() for k in kinds.split(",") if k.strip()}
            unknown = sorted(wanted - set(ACTIVITY_KINDS))
            if unknown:
                return _refusal("UNKNOWN_KIND", f"unknown activity kind(s) {unknown}; the kinds "
                                                f"are {list(ACTIVITY_KINDS)}", status=422)
        viewer = _viewer(principal)
        n = max(1, min(int(limit), MAX_LIMIT))
        got = collect(app, viewer, wanted, limit=n, before=before)
        page, nxt = paginate(got.items, n, before, more=got.more, frontier=got.frontier)
        cur = _cursor_parts(before)
        if nxt and cur and nxt.partition("|")[0] >= cur[0] and not page:
            # the read budget was spent before any row older than the cursor: say so, and do not
            # hand back a cursor that would ask for the same page again
            got.problems.add({"source": "activity", "file": None,
                              "error": f"more records sit at this point in time than one "
                                       f"request reads (at most {MAX_RECORDS_OPENED} records, "
                                       f"{MAX_BYTES_PER_KIND} bytes per kind); the feed cannot "
                                       f"page past it"})
            nxt = None
        counts: dict[str, int] = {}
        for it in got.items:
            counts[it["kind"]] = counts.get(it["kind"], 0) + 1
        return {"items": page, "next_cursor": nxt, "returned": len(page),
                # counts are the VIEWER's own; a kind not read to its end has no total
                "total_visible": None if got.incomplete else len(got.items),
                "counts": {k: v for k, v in counts.items() if k not in got.incomplete},
                "counts_incomplete": sorted(got.incomplete),
                "kinds": list(ACTIVITY_KINDS), "viewer": principal.principal_id,
                "problems": got.problems.out(),
                "visibility": "runs of agents you own or that you started; adapter agents you "
                              "created; federated questions you asked. A record whose owner is "
                              "not recorded, or is ambiguous, is shown only to an operator"
                              + (" (you are one, so you see every record)." if viewer.operator
                                 else ".")}

    @app.get("/activity/runs/{run_id}")
    def activity_run(run_id: str, kind: Optional[str] = None, owner: Optional[str] = None,
                     principal: AuthenticatedPrincipal = Depends(require_principal)):
        """One run. ``kind`` and ``owner`` (each activity row carries them as ``run_kind`` /
        ``run_owner``) select exactly one run when the same id exists in more than one namespace
        the viewer may see."""
        if not _RUN_ID_RE.match(run_id or ""):
            return _refusal("BAD_RUN_ID", f"{run_id!r} is not a run id", status=400)
        if kind is not None and kind not in _RUN_DIR_KINDS:
            return _refusal("BAD_RUN_KIND", f"kind must be one of {sorted(_RUN_DIR_KINDS)}",
                            status=400)
        if owner is not None and not _RUN_ID_RE.match(owner):
            return _refusal("BAD_RUN_OWNER", "owner is not a namespace name", status=400)
        viewer = _viewer(principal)
        problems = Problems(viewer.operator)
        found: list[tuple[str, str, Path, dict]] = []
        for k in ([kind] if kind else list(_RUN_DIR_KINDS)):
            try:
                owners = _run_owner_dirs(viewer, k, owner)
            except OwnerRequired:
                return _refusal("OWNER_REQUIRED",
                                "too many namespaces to search for one id; pass kind and owner "
                                "(each activity row carries them as run_kind and run_owner)",
                                status=400)
            for o in owners:
                hit = _open_run(viewer, k, o, run_id, problems)
                if hit is not None:
                    found.append((k, o, *hit))
        if not found:
            return _not_found()
        if len(found) > 1:
            return _refusal("AMBIGUOUS_RUN",
                            "more than one run you can see has that id; pass kind and owner "
                            "(each activity row carries them as run_kind and run_owner)",
                            status=409,
                            alternative=[{"kind": k, "owner": o} for k, o, _p, _r in found])
        k, o, path, record = found[0]
        item = scrub(run_item(k, o, record, trace_index(app, viewer),
                              viewer.run_principals(k, o, record)))
        history: list[dict] = []
        approvals: list[dict] = []
        if k == "agent":
            log_path = path.with_name(path.name[: -len(".json")] + ".actions.jsonl")
            raw, _lossy = _read_jsonl(log_path, problems, "agent",
                                      visible=viewer.namespace_owned(k, o))
            history = public_history(raw)
            approvals = [scrub(a) for a in approval_items(run_id, o, raw, record)]
        return {"kind": k, "owner": o, "item": item,
                "record": public_record(k, record), "action_history": history,
                "approvals": approvals, "delegation_tree": delegation_tree(record),
                "problems": problems.out()}

    @app.get("/activity/automations")
    def activity_automations(per_spec: int = 20,
                             principal: AuthenticatedPrincipal = Depends(require_principal)):
        viewer = _viewer(principal)
        problems = Problems(viewer.operator)
        n = max(1, min(int(per_spec), MAX_FIRINGS_PER_SPEC))
        raw, lossy = _read_jsonl(viewer.sources.firings_path, problems, "automation",
                                 visible=False)
        if lossy:
            # Codex f2 #3: the log was not read whole, so no count from it is a total. Said to
            # every viewer, naming no file and no number (the log is shared by all principals).
            problems.add({"source": "automation", "file": None,
                          "error": "the firing log was not read in full; firing counts are not "
                                   "totals and older firings may be missing"})
        firings = [f for f in fold_firings(raw) if viewer.firing_visible(f)]
        engine_specs: dict[tuple[str, str], dict] = {}
        rt = getattr(app.state, "platform", None)
        engine = getattr(rt, "engine", None)
        if engine is not None:
            try:
                # keyed by NAMESPACE (Codex f2 #1): an agent and an adapter agent may share an id
                engine_specs = {(_spec_kind(s.get("kind")), str(s["id"])): s
                                for s in engine.status().get("specs", [])}
            except Exception as exc:                                      # noqa: BLE001
                problems.add({"source": "automation", "file": None,
                              "error": f"engine status unreadable: {type(exc).__name__}"})
        traces: Optional[dict] = None
        specs: dict[tuple[str, str], dict] = {}
        for f in sorted(firings, key=lambda f: sort_key(f.get("fired_at")
                                                        or f.get("first_recorded_at")),
                        reverse=True):
            sid = str(f.get("spec_id"))
            ns = (_spec_kind(f.get("spec_kind")), sid)
            spec = specs.setdefault(ns, {"spec_id": sid, "spec_kind": f.get("spec_kind"),
                                         "trigger": None, "firings": [], "firing_count": 0,
                                         "trigger_types_seen": []})
            spec["firing_count"] += 1
            if f.get("trigger") and f["trigger"] not in spec["trigger_types_seen"]:
                spec["trigger_types_seen"].append(f["trigger"])
            if len(spec["firings"]) >= n:
                continue
            row = firing_item(f)
            run_id, run_kind, run_owner = _firing_run(f)
            hit = None
            # the run is opened directly in its spec's namespace — one file, never a scan
            if run_id and run_owner and _RUN_ID_RE.match(run_id) and _RUN_ID_RE.match(run_owner):
                hit = _open_run(viewer, run_kind, run_owner, run_id, problems)
            run_row, approvals = None, []
            if hit is not None:
                path, record = hit
                if traces is None:
                    traces = trace_index(app, viewer)
                run_row = run_item(run_kind, run_owner, record, traces,
                                   viewer.run_principals(run_kind, run_owner, record))
                if run_kind == "agent":
                    events, _l = _read_jsonl(
                        path.with_name(path.name[: -len(".json")] + ".actions.jsonl"), problems,
                        "agent", visible=viewer.namespace_owned(run_kind, run_owner))
                    approvals = approval_items(run_id, run_owner, events, record)
            row["run"] = run_row
            row["run_note"] = (None if run_row or not run_id else
                               "the run this firing names is not visible to you or was not "
                               "found")
            row["approvals"] = approvals
            spec["firings"].append(scrub(row))
        for ns, spec in specs.items():
            es = engine_specs.get(ns)
            spec["trigger"] = ({k: _scalar(es.get(k)) for k in ("trigger", "status", "next_fire",
                                                                "kind")} if es else None)
        for ns, es in engine_specs.items():
            if ns in specs or not viewer.spec_visible(es.get("kind"), ns[1]):
                continue
            specs[ns] = {"spec_id": ns[1], "spec_kind": es.get("kind"),
                         "trigger": {k: _scalar(es.get(k)) for k in ("trigger", "status",
                                                                     "next_fire", "kind")},
                         "firings": [], "firing_count": 0, "trigger_types_seen": []}
        for spec in specs.values():
            # a count from a log that was not read whole is not a total: null, not a number
            # (Codex f2 #3) -- and that includes the 0 of a spec with no firing read
            spec["firing_count"] = None if lossy else spec["firing_count"]
        return {"automations": sorted(specs.values(),
                                      key=lambda s: (s["spec_id"], str(s["spec_kind"]))),
                "firings_complete": not lossy,
                "engine": {"available": engine is not None,
                           "note": None if engine is not None else
                           "the trigger engine is not attached to this gateway; triggers are "
                           "shown from their recorded firings only"},
                "viewer": principal.principal_id, "problems": problems.out()}


__all__ = ["register_activity_routes", "ACTIVITY_KINDS", "ActivitySources", "Viewer",
           "collect", "delegation_tree", "fold_firings", "approval_items", "run_item",
           "paginate", "resolve_sources", "trace_index", "MAX_LIMIT", "public_record",
           "public_history", "scrub", "Problems", "MASKED_KEYS", "RECORD_SCHEMAS",
           "MAX_RECORDS_OPENED", "NAME_TIME_SLACK"]
