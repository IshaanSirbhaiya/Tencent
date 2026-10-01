"""WorkflowSpec / AdapterAgentSpec models (build plan §5.6)."""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Literal, Optional, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (AfterValidator, BaseModel, ConfigDict, Field, StrictInt, ValidationError,
                      WrapSerializer, model_validator)

#: Mirrors `databridge_agentdef.declaration.AUTOMATION_MODES` (D12). Duplicated rather than imported
#: so this contract has no dependency on the compiler; a test asserts the two sets are equal.
AUTOMATION_MODES = frozenset({"human_required", "human_notified", "autonomous"})

#: One identifier grammar for every agent / adapter-agent / delegate / connection id. Matched with
#: `fullmatch` (R-021 #3): `$` also matches before a trailing newline, so `"agent\n"` used to pass.
_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")
#: An action name: lower_snake_case, no leading/trailing/double underscore (R-017 finding 10).
_ACTION_NAME = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
#: An MCP tool name is an OPAQUE catalogue key in the MCP tool-name alphabet (letters, digits, `_`,
#: `-`, `.`), at most 128 characters. No path syntax — `/`, `\`, `..` and control characters are
#: refused (R-021 #3) — so no route, log or lookup can read a tool name as a path.
_MCP_TOOL = r"[A-Za-z0-9_][A-Za-z0-9_.\-]{0,127}"
#: Tool grammar:
#:   `graph.<op>`, `retrieve.<op>`                      the runtime's own read families
#:   `source.read:<connection>[:<mcp tool>]`            a live read
#:   `source.query:<connection>`                        one checked READ-ONLY statement, live (it
#:                                                      names no server tool — R-017 finding 2)
#:   `facts.query:<connection>`                         exact SQL over the connection's read-only
#:                                                      facts store — counting / listing (S2)
#:   `delegate.ask:<adapter agent>`                     READ-ONLY delegated query to an adapter
#:                                                      agent (FED, 2026-10-01); the id must be
#:                                                      declared in the workflow's `delegates`
#:   `source.write:<connection>:<mcp tool>`             a write; only ever an ACTION. A database
#:                                                      write goes through a separate writer
#:                                                      connection, e.g.
#:                                                      `source.write:larkspur_db_writer:execute_sql`
_TOOL = re.compile(
    r"(?:(?P<family>graph|retrieve)\.(?P<op>[a-z_]+)"
    r"|facts\.query:(?P<fconn>[a-z][a-z0-9_-]{0,63})"
    r"|delegate\.ask:(?P<daa>[a-z][a-z0-9_-]{0,63})"
    r"|source\.(?P<verb>read|query|write):(?P<conn>[a-z][a-z0-9_-]{0,63})"
    r"(?::(?P<tool>" + _MCP_TOOL + r"))?)"
)


def is_identifier(value: Any) -> bool:
    """True when ``value`` is, as a whole string, a well-formed agent / connection / delegate id."""
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _check_ids(values: Any, what: str) -> None:
    for v in values:
        if not is_identifier(v):
            raise ValueError(f"{what}: {v!r} is not an id — lower-case letters, digits, `_` and "
                             f"`-`, starting with a letter, at most 64 characters, with no path "
                             f"or control characters")


# ------------------------------------------------------------------------------------------------
# deep immutability (R-017 finding 1, R-021 #2). `frozen=True` stops field REASSIGNMENT only. Round
# 1 used list/dict SUBCLASSES whose mutators raised, but `list.append(spec.actions, w)` calls the
# base implementation and walked past them. Sequences are now TUPLES and mappings a read-only view
# over a private copy, so no container mutator exists to call. They still compare equal to plain
# lists and dicts and serialise as lists and dicts (`model_dump`, `dump_spec`, JSON).
#
# Honest limit: Python has no sealed objects. `object.__setattr__` can rebind an attribute of any
# instance, this one or the frozen pydantic model itself. That is not a container API. The defence
# against a spec that changed after validation is `spec_digest`, which approval re-checks.
# ------------------------------------------------------------------------------------------------
def _immutable(*_a: Any, **_k: Any) -> Any:
    raise TypeError("a validated spec is immutable; build a new one with load_spec(...)")


class FrozenList(tuple):
    """An immutable sequence that compares equal to a list holding the same items."""

    __slots__ = ()
    #: list's mutator NAMES raise a clear TypeError (the underlying tuple has none to bypass to)
    append = extend = insert = remove = pop = clear = sort = reverse = _immutable
    __setitem__ = __delitem__ = __iadd__ = __imul__ = _immutable

    def __eq__(self, other: object) -> bool:
        if isinstance(other, list):
            return list(self) == other
        return tuple.__eq__(self, other)

    def __ne__(self, other: object) -> bool:
        eq = self.__eq__(other)
        return eq if eq is NotImplemented else not eq

    __hash__ = tuple.__hash__

    def __add__(self, other: Any) -> list:                    # `spec.tools + [...]` -> a new list
        return list(self) + list(other)

    def __radd__(self, other: Any) -> list:
        return list(other) + list(self)

    def copy(self) -> list:
        return list(self)

    def __repr__(self) -> str:
        return f"FrozenList({list(self)!r})"


class FrozenDict(Mapping):
    """An immutable mapping (a read-only view over a private copy) that compares equal to a dict."""

    __slots__ = ("_m",)
    #: dict's mutator NAMES raise a clear TypeError (the read-only view has none to bypass to)
    __setitem__ = __delitem__ = __ior__ = pop = popitem = clear = update = setdefault = _immutable

    def __init__(self, data: Any = ()) -> None:
        object.__setattr__(self, "_m", MappingProxyType(dict(data)))

    def __getitem__(self, key: Any) -> Any:
        return self._m[key]

    def __iter__(self):
        return iter(self._m)

    def __len__(self) -> int:
        return len(self._m)

    def __setattr__(self, *_a: Any) -> None:
        raise TypeError("a validated spec is immutable; build a new one with load_spec(...)")

    def __delattr__(self, *_a: Any) -> None:
        raise TypeError("a validated spec is immutable; build a new one with load_spec(...)")

    def __reduce__(self):
        return (FrozenDict, (dict(self._m),))

    def __or__(self, other: Any) -> dict:
        return {**self._m, **dict(other)}

    def __ror__(self, other: Any) -> dict:
        return {**dict(other), **self._m}

    def copy(self) -> dict:
        return dict(self._m)

    def __repr__(self) -> str:
        return f"FrozenDict({dict(self._m)!r})"


def _thaw(value: Any) -> Any:
    """Plain lists / dicts for serialisation. Models inside are left to pydantic."""
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(v) for v in value]
    return value


def _serialise_plain(value: Any, handler: Any) -> Any:
    return handler(_thaw(value))


_PLAIN = WrapSerializer(_serialise_plain)


# ------------------------------------------------------------------------------------------------
# cron (R-017 finding 7, R-021): the grammar the trigger engine runs (`databridge_automation.cron`),
# duplicated so this contract has no dependency on it; tests assert the two agree.
# ------------------------------------------------------------------------------------------------
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_DAYS = {d: i for i, d in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}
_CRON_FIELDS = (("minute", 0, 59, None), ("hour", 0, 23, None), ("day-of-month", 1, 31, None),
                ("month", 1, 12, _MONTHS), ("day-of-week", 0, 7, _DAYS))
#: The longest each month ever runs (29 February exists in leap years: a Feb-29 cron still fires).
_MONTH_DAYS = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


def _cron_value(token: str, lo: int, hi: int, names: Optional[dict[str, int]], field: str) -> int:
    t = token.strip().lower()
    if names and t in names:
        return names[t]
    if not t.isdigit():
        raise ValueError(f"cron {field} value {token!r} is not a number"
                         + (f" or one of {sorted(names)}" if names else ""))
    v = int(t)
    if not lo <= v <= hi:
        raise ValueError(f"cron {field} value {v} is outside {lo}-{hi}")
    return v


def _cron_field(text: str, lo: int, hi: int, names: Optional[dict[str, int]], field: str
                ) -> tuple[frozenset[int], bool]:
    """The field's allowed values, and whether it is unrestricted — the trigger engine's reading
    (`databridge_automation.cron._field`), so the two agree on what can fire."""
    out: set[int] = set()
    star = False
    for part in text.split(","):
        if not part:
            raise ValueError(f"cron {field} has an empty list item in {text!r}")
        base, step = part, 1
        if "/" in part:
            base, _, s = part.partition("/")
            if not s.isdigit() or int(s) < 1:
                raise ValueError(f"cron {field} step {s!r} must be a positive integer")
            step = int(s)
        if base == "*":
            start, end = lo, hi
            star = star or step == 1
        elif "-" in base:
            a, _, b = base.partition("-")
            start, end = _cron_value(a, lo, hi, names, field), _cron_value(b, lo, hi, names, field)
            if start > end:
                raise ValueError(f"cron {field} range {base!r} runs backwards")
        else:
            start = _cron_value(base, lo, hi, names, field)
            end = hi if "/" in part else start
        out.update(range(start, end + 1, step))
    return frozenset(out), star and len(text.split(",")) == 1


def check_cron(expr: str) -> None:
    """Raise ``ValueError`` (message starts ``cron``) unless ``expr`` is a runnable 5-field cron:
    well formed, in range, AND able to fire at least once (R-021: ``0 0 31 2 *`` parsed but never
    ran). Days match by the Vixie rule the engine uses: when day-of-month and day-of-week are BOTH
    restricted a day matches if either does, otherwise both must."""
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron {expr!r} must have five fields (min hour day month weekday)")
    fields = [_cron_field(text, lo, hi, names, field)
              for text, (field, lo, hi, names) in zip(parts, _CRON_FIELDS)]
    (days, dom_star), (months, _), (_dows, dow_star) = fields[2], fields[3], fields[4]
    either_matches = not dom_star and not dow_star
    if not either_matches and not any(d <= _MONTH_DAYS[m] for m in months for d in days):
        raise ValueError(f"cron {expr!r} can never fire: none of its months has any of its days "
                         f"of the month")


def check_timezone(name: str) -> None:
    """Raise ``ValueError`` unless ``name`` is an IANA timezone this machine can resolve."""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"timezone {name!r} is not a known IANA timezone") from exc


class SpecError(ValueError):
    """A spec that is structurally invalid. The message is written for the person who wrote it."""


def parse_tool(name: str) -> dict[str, Optional[str]]:
    """Split a tool name into its parts, or raise `SpecError`."""
    m = _TOOL.fullmatch(name) if isinstance(name, str) else None
    if m and m.group("tool") and ".." in m.group("tool"):
        m = None
    if not m:
        raise SpecError(
            f"tool {name!r} is not a known tool shape: expected graph.<op>, retrieve.<op>, "
            f"facts.query:<connection>, delegate.ask:<adapter agent>, or "
            f"source.<read|query|write>:<connection>[:<tool>] (a tool name uses letters, digits, "
            f"`_`, `-`, `.` — never a path)")
    d = m.groupdict()
    if d.get("daa"):
        # FED: a delegated, read-only query answered by an adapter agent. `connection` carries the
        # adapter agent id so every consumer that keys on it keeps working unchanged.
        return {"family": "delegate", "op": "ask", "verb": None, "connection": d["daa"],
                "tool": None}
    if d["fconn"]:
        return {"family": "facts", "op": "query", "verb": None, "connection": d["fconn"],
                "tool": None}
    if d["verb"] == "query" and d["tool"]:
        raise SpecError(
            f"tool {name!r}: source.query:<connection> sends one checked statement and names no "
            f"server tool; a tool that changes the source is a source.write action")
    if d["family"]:
        return {"family": d["family"], "op": d["op"], "verb": None, "connection": None, "tool": None}
    return {"family": "source", "op": None, "verb": d["verb"], "connection": d["conn"], "tool": d["tool"]}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


StrList = Annotated[list[str], AfterValidator(FrozenList), _PLAIN]
StrMap = Annotated[dict[str, str], AfterValidator(FrozenDict), _PLAIN]


class ScheduleTrigger(_Strict):
    cron: str
    timezone: str = "UTC"

    @model_validator(mode="after")
    def _cron_shape(self) -> "ScheduleTrigger":
        check_cron(self.cron)
        check_timezone(self.timezone)
        return self


class EventTrigger(_Strict):
    source: str
    on: Annotated[list[Literal["item_added", "item_changed", "item_removed", "run_completed",
                               "run_failed"]], AfterValidator(FrozenList), _PLAIN]
    filter: StrMap = Field(default_factory=FrozenDict)

    @model_validator(mode="after")
    def _non_empty(self) -> "EventTrigger":
        if not self.on:
            raise ValueError("an event trigger must name at least one event type in `on`")
        return self


class Trigger(_Strict):
    type: Literal["manual", "schedule", "event"]
    schedule: Optional[ScheduleTrigger] = None
    event: Optional[EventTrigger] = None

    @model_validator(mode="after")
    def _consistent(self) -> "Trigger":
        if self.type == "schedule" and self.schedule is None:
            raise ValueError("trigger type 'schedule' needs a `schedule` block")
        if self.type == "event" and self.event is None:
            raise ValueError("trigger type 'event' needs an `event` block")
        # exactly one payload (R-017 finding 8): two consumers must not honour different blocks
        if self.type == "schedule" and self.event is not None:
            raise ValueError("a schedule trigger carries no `event` block")
        if self.type == "event" and self.schedule is not None:
            raise ValueError("an event trigger carries no `schedule` block")
        if self.type == "manual" and (self.schedule or self.event):
            raise ValueError("a manual trigger carries no schedule or event")
        return self


class ActionSpec(_Strict):
    name: str
    tool: str
    automation_mode: Literal["human_required", "human_notified", "autonomous"]
    reversal: Optional[str] = None
    description: str = ""

    @model_validator(mode="after")
    def _rules(self) -> "ActionSpec":
        if not _ACTION_NAME.fullmatch(self.name) or len(self.name) > 64:
            raise ValueError(f"action name {self.name!r} must be lower_snake_case")
        parts = parse_tool(self.tool)
        # One verb, one meaning (R-017 finding 2 / S2): `source.query` is ALWAYS a checked read,
        # so it can never be an action. A write names the writer connection's tool.
        if parts["family"] != "source" or parts["verb"] != "write" or not parts["tool"]:
            raise ValueError(f"action {self.name!r}: an action must use a "
                             f"source.write:<connection>:<tool> tool, got {self.tool!r}. A database "
                             f"write goes through a separate writer connection (e.g. "
                             f"source.write:larkspur_db_writer:execute_sql) — never source.query")
        if self.automation_mode == "autonomous" and not (self.reversal or "").strip():
            raise ValueError(f"action {self.name!r} is autonomous, so it must declare `reversal` "
                             f"— how it is undone (D12)")
        return self


def _json_value(value: Any, where: str = "value") -> Any:
    """A CLOSED JSON value (null, bool, int, float, str, list, str-keyed map), frozen all the way
    down (R-021 #2). Sets, bytes and arbitrary objects are refused, not carried unfrozen."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"derivation {where} {value!r} is not a JSON number")
        return value
    if isinstance(value, Mapping):
        out = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError(f"derivation {where} has a non-string key {k!r}")
            out[k] = _json_value(v, f"{where}.{k}")
        return FrozenDict(out)
    if isinstance(value, (list, tuple)):
        return FrozenList(_json_value(v, f"{where}[{i}]") for i, v in enumerate(value))
    raise ValueError(f"derivation {where} must be a JSON value (null, bool, number, string, list, "
                     f"map), not {type(value).__name__}")


#: The compiler's finer-grained ``by`` words, mapped onto `produced_by` by a CLOSED table (R-021 #4).
#: A word not in it is recorded as ``unknown`` — never guessed as ``rules``.
BY_TO_PRODUCED_BY: Mapping[str, str] = MappingProxyType({
    "rule": "rules", "rules": "rules", "default": "rules", "config": "rules",
    "catalogue": "rules",
    "model": "model", "model+answer": "model", "model+rule": "model",
    "answer": "user", "user": "user",
})
PRODUCED_BY = ("rules", "model", "user", "unknown")


class Derivation(_Strict):
    """Who produced one field of a spec (§5.8 traceability for the English compiler).

    ``produced_by`` is closed and required: ``rules`` (deterministic — a rule, a default, the
    catalogue, configuration), ``model`` (a model proposed it; ``model`` / ``task_class`` /
    ``prompt_version`` must name which), ``user`` (a person's answer), ``unknown`` (nobody recorded
    who — shown as unknown, never presented as deterministic). The compiler's finer-grained ``by``
    is accepted, mapped by `BY_TO_PRODUCED_BY`, and kept verbatim in ``basis``; a ``by`` word that
    table does not know is ``unknown``. A ``rules`` or ``user`` derivation carries its basis: a
    ``basis``, ``evidence`` or ``span``. A ``span`` is ``[start, end]`` into ``created_from``.
    """

    field: str
    produced_by: Literal["rules", "model", "user", "unknown"]
    basis: str = ""
    span: Optional[Annotated[list[StrictInt], AfterValidator(FrozenList), _PLAIN]] = None
    model: Optional[str] = None
    task_class: Optional[str] = None
    prompt_version: Optional[str] = None
    value: Annotated[Any, AfterValidator(_json_value), _PLAIN] = None
    evidence: str = ""

    @model_validator(mode="before")
    @classmethod
    def _from_compiler(cls, data: Any) -> Any:
        if isinstance(data, dict) and "by" in data and "produced_by" not in data:
            data = dict(data)
            by = str(data.pop("by") or "")
            data["basis"] = data.get("basis") or by
            data["produced_by"] = BY_TO_PRODUCED_BY.get(by, "unknown")
        return data

    @model_validator(mode="after")
    def _span_shape(self) -> "Derivation":
        if not self.field.strip():
            raise ValueError("a derivation names the field it produced")
        if self.span is not None and (len(self.span) != 2 or self.span[0] < 0
                                      or self.span[0] > self.span[1]):
            raise ValueError(f"derivation span {list(self.span)!r} must be [start, end] with "
                             f"0 <= start <= end")
        if self.produced_by == "model":
            missing = [k for k in ("model", "task_class", "prompt_version")
                       if not (getattr(self, k) or "").strip()]
            if missing:
                raise ValueError(f"derivation of {self.field!r} is produced_by model, so it must "
                                 f"name {', '.join(missing)} (§5.8)")
        if self.produced_by in ("rules", "user") and not (
                self.basis.strip() or self.evidence.strip() or self.span is not None):
            raise ValueError(f"derivation of {self.field!r} is produced_by {self.produced_by} but "
                             f"carries no basis, evidence or span — record which, or say unknown")
        return self


Derivations = Annotated[list[Derivation], AfterValidator(FrozenList), _PLAIN]


class _Derived(_Strict):
    """Shared by both spec kinds: derivations checked against the user's words, and a lookup that
    answers ``unknown`` for any field no derivation covers (unknown provenance stays unknown)."""

    def _check_derivations(self) -> None:
        n = len(self.created_from)  # type: ignore[attr-defined]
        for d in self.derivations:  # type: ignore[attr-defined]
            if d.span is not None and d.span[1] > n:
                raise ValueError(f"derivation of {d.field!r}: span {list(d.span)!r} runs past the "
                                 f"end of created_from ({n} characters)")

    def provenance_of(self, field: str) -> Derivation:
        """The derivation recorded for ``field``, else an explicit ``unknown`` one."""
        for d in self.derivations:  # type: ignore[attr-defined]
            if d.field == field:
                return d
        return Derivation(field=field, produced_by="unknown")


class Outputs(_Strict):
    """How a run's answer is shaped, and whether it is persisted.

    ``save_to`` is a GOVERNED write (R-021 #1): when set, it names one of the agent's declared
    ``actions`` — a ``source.write:<connection>:<tool>`` with its own ``automation_mode`` — and the
    answer is saved only through that action's approval path. It is never a path.
    """

    format: Literal["cited_markdown", "json"] = "cited_markdown"
    save_to: Optional[str] = None


class Limits(_Strict):
    max_steps: int = Field(20, ge=1, le=200)
    max_tool_calls: int = Field(60, ge=1, le=1000)
    tokens_per_run: int = Field(60000, ge=1000)


class WorkflowSpec(_Derived):
    spec: Literal["workflow/v1"] = "workflow/v1"
    agent: str
    title: str
    created_from: str = Field(..., description="the user's English, verbatim — never reworded")
    mode: Literal["read_only", "read_write"]
    sources: StrList = Field(default_factory=FrozenList)
    tools: StrList = Field(default_factory=FrozenList)
    actions: Annotated[list[ActionSpec], AfterValidator(FrozenList), _PLAIN] = Field(
        default_factory=FrozenList)
    trigger: Trigger = Trigger(type="manual")
    instructions: str
    outputs: Outputs = Outputs()
    limits: Limits = Limits()
    status: Literal["draft", "active", "paused"] = "draft"
    derivations: Derivations = Field(default_factory=FrozenList)
    #: Adapter agents this agent may DELEGATE read-only sub-queries to (`delegate.ask:<id>`, FED).
    #: Empty ⇒ none; never "all" implicitly. Whether each id exists is checked against the live
    #: adapter-agent registry by the runtime, not here (this module has no I/O).
    delegates: StrList = Field(default_factory=FrozenList)

    def save_action(self) -> Optional[ActionSpec]:
        """The declared action that persists the answer (``outputs.save_to``), or None."""
        if self.outputs.save_to is None:
            return None
        return next(a for a in self.actions if a.name == self.outputs.save_to)

    @model_validator(mode="after")
    def _rules(self) -> "WorkflowSpec":
        if not is_identifier(self.agent):
            raise ValueError(f"agent id {self.agent!r} must match {_ID.pattern}")
        if not self.created_from.strip():
            raise ValueError("created_from must hold the user's words")
        if not self.instructions.strip():
            raise ValueError("instructions must not be empty")
        _check_ids(self.sources, "sources")
        declared = set(self.sources)
        for d in self.delegates:
            if not is_identifier(d):
                raise ValueError(f"delegate {d!r} must be an adapter agent id matching "
                                 f"{_ID.pattern}")
        if len(set(self.delegates)) != len(self.delegates):
            raise ValueError("delegates must be unique")
        for t in self.tools:
            p = parse_tool(t)
            if p["family"] == "delegate" and p["connection"] not in set(self.delegates):
                raise ValueError(f"tool {t!r} delegates to adapter agent {p['connection']!r}, "
                                 f"which is not in `delegates`")
            if p["family"] == "facts" and p["connection"] not in declared:
                raise ValueError(f"tool {t!r} uses connection {p['connection']!r}, which is not "
                                 f"in `sources`")
            if p["family"] == "source":
                if p["verb"] == "write":
                    raise ValueError(f"{t!r}: write tools are declared as `actions`, not `tools`")
                if p["connection"] not in declared:
                    raise ValueError(f"tool {t!r} uses connection {p['connection']!r}, which is "
                                     f"not in `sources`")
        if self.mode == "read_only" and self.actions:
            raise ValueError("a read_only agent declares no actions")
        if self.mode == "read_only" and self.outputs.save_to is not None:
            raise ValueError("a read_only agent persists nothing: outputs.save_to must be null "
                             "(saving is a write; declare it as an action of a read_write agent)")
        names = [a.name for a in self.actions]
        if len(names) != len(set(names)):
            raise ValueError("action names must be unique")
        if self.outputs.save_to is not None and self.outputs.save_to not in names:
            raise ValueError(f"outputs.save_to {self.outputs.save_to!r} must name one of this "
                             f"agent's declared actions {sorted(names)} — saving is a governed "
                             f"source.write action with an automation_mode, never a path")
        for a in self.actions:
            conn = parse_tool(a.tool)["connection"]
            if conn not in declared:
                raise ValueError(f"action {a.name!r} uses connection {conn!r}, which is not in "
                                 f"`sources`")
        if self.trigger.type == "event" and self.trigger.event and \
                self.trigger.event.source not in declared:
            raise ValueError(f"event trigger listens to {self.trigger.event.source!r}, which is "
                             f"not in `sources`")
        self._check_derivations()
        return self


class ExtractPolicy(_Strict):
    pack: str
    prose: bool = True
    tabular: bool = True
    passages: bool = True


class Freshness(_Strict):
    max_age_hours: int = Field(24, ge=1)


#: Kinds of source-format drift an adapter agent can meet (founder-approved contract addition,
#: 2026-10-01; behaviour implemented in `packages/adapter_agents`).
DRIFT_KINDS = ("removed", "renamed", "type_changed", "tool_signature_changed", "new_write_tool",
               "subject_touching")
#: Always put to a person, whatever the policy; a spec that drops either is refused.
ALWAYS_ASKED = frozenset({"new_write_tool", "subject_touching"})
DriftKind = Literal["removed", "renamed", "type_changed", "tool_signature_changed",
                    "new_write_tool", "subject_touching"]


class AdaptWatch(_Strict):
    """When the adapter agent re-checks a source's shape."""

    on_sync: bool = True              # every sync
    on_list_changed: bool = True      # when the MCP server sends tools/resources list_changed


class AdaptPolicy(_Strict):
    """How an adapter agent adjusts to source-format changes at runtime.

    * ``auto_additive`` (default) — additive drift adapts on its own; every kind in
      ``always_ask`` is put to a person first.
    * ``ask_all`` — every drift is put to a person.
    * ``off`` — drift is detected and REPORTED, nothing adapts.

    ``always_ask`` defaults to every `DRIFT_KINDS` entry. A user may narrow or re-extend it, but
    never below `ALWAYS_ASKED` (``new_write_tool``, ``subject_touching``): those are asked under
    every policy — a new write path and anything that decides who a record is about are a
    person's call (CLAUDE.md hard constraints 1 and 4).
    """

    policy: Literal["auto_additive", "ask_all", "off"] = "auto_additive"
    watch: AdaptWatch = AdaptWatch()
    always_ask: Annotated[list[DriftKind], AfterValidator(FrozenList), _PLAIN] = Field(
        default_factory=lambda: FrozenList(DRIFT_KINDS))

    @model_validator(mode="after")
    def _unremovable(self) -> "AdaptPolicy":
        missing = sorted(ALWAYS_ASKED - set(self.always_ask))
        if missing:
            raise ValueError(f"adapt.always_ask must keep {missing}: a new write tool and anything "
                             f"that touches a subject are always asked, whatever the policy")
        if len(set(self.always_ask)) != len(self.always_ask):
            raise ValueError("adapt.always_ask lists a drift kind twice")
        return self


_PLANS_DESCRIPTION = (
    "NON-AUTHORITATIVE mirror of each connection's crawl-plan status, copied from the plan store "
    "by refresh_plans. It is never an approval: an unsigned YAML value cannot represent one. A "
    "connection runs only when the on-disk crawl plan carries an approval attributed to an "
    "authenticated principal (approve_plan); this mirror can only HOLD a connection back.")


class AdapterAgentSpec(_Derived):
    spec: Literal["adapter_agent/v1"] = "adapter_agent/v1"
    id: str
    title: str
    created_from: str
    connections: StrList
    plans: Annotated[dict[str, Literal["proposed", "approved"]], AfterValidator(FrozenDict),
                     _PLAIN] = Field(default_factory=FrozenDict, description=_PLANS_DESCRIPTION,
                                     json_schema_extra={"authoritative": False})
    sync: Trigger = Trigger(type="manual")
    extract: ExtractPolicy
    #: A REFERENCE, never rules (R-021 #5): empty, or exactly ``{pack: <extract.pack>}``, naming
    #: the pack whose `packs/<pack>/screen.yaml` ingestion screens with (plus the always-on default
    #: credential rules). Rules written here were never run, so any other key is refused rather
    #: than silently ignored. Stored specs (`screen: {}`, or the compiler's `{pack: ...}`) load.
    screen: StrMap = Field(default_factory=FrozenDict)
    freshness: Freshness = Freshness()
    adapt: AdaptPolicy = AdaptPolicy()
    propose_ontology_additions: bool = True
    status: Literal["draft", "active", "paused"] = "draft"
    derivations: Derivations = Field(default_factory=FrozenList)

    @model_validator(mode="after")
    def _rules(self) -> "AdapterAgentSpec":
        if not is_identifier(self.id):
            raise ValueError(f"adapter agent id {self.id!r} must match {_ID.pattern}")
        if not self.created_from.strip():
            raise ValueError("created_from must hold the user's words")
        if not self.connections:
            raise ValueError("an adapter agent manages at least one connection")
        _check_ids(self.connections, "connections")
        if len(set(self.connections)) != len(self.connections):
            raise ValueError("connections must be unique")
        stray = set(self.plans) - set(self.connections)
        if stray:
            raise ValueError(f"plans name connections this agent does not manage: {sorted(stray)}")
        rules = sorted(set(self.screen) - {"pack"})
        if rules:
            raise ValueError(f"screen may only name the screening pack (screen.pack: "
                             f"{self.extract.pack}): ingestion screens with "
                             f"packs/<pack>/screen.yaml and never reads rules from a spec, so "
                             f"{rules} would silently not run — put the rule in the pack's "
                             f"screen.yaml")
        if "pack" in self.screen and self.screen["pack"] != self.extract.pack:
            raise ValueError(f"screen.pack {self.screen['pack']!r} must be the pack this agent "
                             f"extracts with ({self.extract.pack!r}); one agent, one screening "
                             f"pack")
        if self.sync.type == "event" and self.sync.event and \
                self.sync.event.source not in self.connections:
            raise ValueError("an event sync must listen to one of this agent's connections")
        self._check_derivations()
        return self


Spec = Union[WorkflowSpec, AdapterAgentSpec]
_BY_TAG: dict[str, type[BaseModel]] = {"workflow/v1": WorkflowSpec, "adapter_agent/v1": AdapterAgentSpec}


def load_spec(source: Union[str, Path, dict[str, Any]]) -> Spec:
    """Parse a spec from a YAML path, YAML text, or a mapping. Raises `SpecError` with every
    problem in one message."""
    if isinstance(source, dict):
        data = source
    else:
        text = Path(source).read_text(encoding="utf-8") if isinstance(source, Path) or (
            isinstance(source, str) and "\n" not in source and Path(source).suffix in {".yaml", ".yml"}
        ) else source
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise SpecError("a spec must be a mapping")
    tag = data.get("spec")
    model = _BY_TAG.get(tag)
    if model is None:
        raise SpecError(f"unknown spec tag {tag!r}; expected one of {sorted(_BY_TAG)}")
    try:
        return model.model_validate(data)  # type: ignore[return-value]
    except ValidationError as e:
        msgs = "; ".join(f"{'.'.join(map(str, err['loc'])) or '<spec>'}: {err['msg']}"
                         for err in e.errors())
        raise SpecError(msgs) from None


def dump_spec(spec: Spec) -> str:
    """YAML text for a spec, fields in declaration order, defaults included (explicit is reviewable).

    PRESENTATION ONLY. Nested maps keep the order they were written in, so two equal specs can
    dump differently: never hash this — use `spec_digest` (R-021 #7)."""
    return yaml.safe_dump(spec.model_dump(mode="json"), sort_keys=False, allow_unicode=True)


def canonical_json(spec: Spec) -> str:
    """The canonical encoding of a spec, for digests (R-021 #7).

    JSON of ``model_dump(mode="json")`` (defaults included) with every mapping's keys sorted at
    every depth, ``(",", ":")`` separators, UTF-8 text kept as is (``ensure_ascii=False``) and no
    NaN/Infinity. Lists keep their order: every list in the contract is order-significant (the
    order of ``actions``, ``tools``, ``always_ask`` and ``derivations`` is what a reviewer read),
    so reordering one is a change. Reordering a mapping's keys is not."""
    return json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def spec_digest(spec: Spec) -> str:
    """``sha256:<hex>`` of `canonical_json` — the one authoritative spec digest."""
    return "sha256:" + hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()
