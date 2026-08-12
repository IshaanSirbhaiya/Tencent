"""Clinic monitors — the conditions that produce drafts, as DATA.

The same argument as ``coverage_rules.yaml`` and ``agent_playbooks.yaml``, pointed at workflows:
*which* situation is worth telling somebody about is a clinic policy, and a clinic that wants to
chase outstanding questionnaires after 24 hours instead of 48 should not need a Python diff
(CLAUDE.md §Hard constraints 3). Adding a monitor is a row in
``config/clinic_monitors.yaml``; there is no ``if monitor.id == ...`` anywhere in this package, and
``tests/test_monitors.py`` asserts that against the source.

Three things this loader refuses, because a config file is edited more casually than code:

* **An address.** ``escalate_to`` is a ROLE, and no field may contain something shaped like a
  mailbox or a URL — the whole file is scanned, not just the fields somebody remembered to check.
  Recipients come from ``recipients.py``'s two doors; an address in a config file would be a third
  door, opened by whoever was editing YAML that afternoon. This is the same rule
  ``agent_playbooks.yaml`` states in its header, enforced here rather than asserted there.
* **A monitor that sends.** ``requires_human_approval`` is ``Literal[True]`` and there is no
  ``action`` field at all. A monitor produces a DRAFT. The only verb this file can express is
  "draft something for a person to read".
* **A template nobody can fill.** Placeholders are extracted from the templates themselves rather
  than declared alongside them, so the two cannot drift; a placeholder with no grounded fact behind
  it fails at draft time (``drafting.py``), which is where the pointer requirement lives.

Conditions are a closed set of nine operators over named facts. Not an expression language: an
expression language in a config file is code with worse tooling, and this one would be evaluating
over patient data.
"""
from __future__ import annotations

import functools
import pathlib
import re
from typing import Any, Literal, Mapping, Optional

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from databridge_core import GroundedValue, SourcePointer

from .models import Channel, RecipientOrigin

#: The config file, as a source id, so a pointer into it resolves to a file a human can open.
MONITORS_SOURCE_ID = "clinic_monitors.yaml"
MONITORS_TABLE = "monitors"

#: ``{fact_name}`` and nothing more. No format specs, no attribute access, no nesting — a template
#: language with an expression in it is an expression language, and this one interpolates values
#: read off a patient's record.
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

#: Anything shaped like a mailbox or a URL. Scanned over the WHOLE file, so a new field cannot
#: quietly become a place to put an address.
ADDRESS_SHAPED = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+|\b[a-z][a-z0-9+.-]*://", re.I)

#: The closed operator set. Adding one is a code change with a test, which is the point: an
#: operator is a way of reading patient data, not a preference.
Op = Literal[
    "at_least", "at_most", "equals", "not_equals",
    "is_true", "is_false", "is_present", "is_absent", "contains",
]

#: Operators that compare against a literal, and therefore require ``value``. The rest are unary
#: and must NOT carry one — a ``value`` nothing reads is a setting somebody will believe in.
_NEEDS_VALUE = frozenset({"at_least", "at_most", "equals", "not_equals", "contains"})


class MonitorError(RuntimeError):
    """The monitor config is missing or malformed. Fail loud; never run on a partial table."""


class Observation(BaseModel):
    """What is true about one intake right now, as grounded values.

    Assembled by the composition root from committed rows, the intake queue and the config table —
    never by a model. Every fact is a ``GroundedValue``, so a fact that cannot say where it came
    from cannot be observed, and therefore cannot reach a draft: the grounding wall's argument
    carried one step further out, to the sentence a patient reads.
    """

    model_config = ConfigDict(frozen=True)

    intake_id: str
    facts: Mapping[str, GroundedValue] = {}

    def fact(self, name: str) -> Optional[GroundedValue]:
        return self.facts.get(name)

    def value(self, name: str) -> Any:
        got = self.facts.get(name)
        return None if got is None else got.value


class Condition(BaseModel):
    """One comparison against one named fact. Declarative, closed, and total.

    A missing fact makes the condition False rather than an error (except for ``is_absent``, where
    missing IS the thing being asked about). A monitor should not fire on data nobody has, and it
    should not crash the sweep for the other intakes either.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact: str
    op: Op
    value: Optional[Any] = None

    @model_validator(mode="after")
    def _value_matches_the_operator(self) -> "Condition":
        if self.op in _NEEDS_VALUE and self.value is None:
            raise ValueError(f"condition {self.op!r} on {self.fact!r} needs a `value` to compare to")
        if self.op not in _NEEDS_VALUE and self.value is not None:
            raise ValueError(
                f"condition {self.op!r} on {self.fact!r} takes no `value`; the one given would "
                f"never be read, and a setting nothing reads is a setting somebody will trust")
        return self

    def holds(self, observation: Observation) -> bool:
        present = observation.fact(self.fact) is not None
        got = observation.value(self.fact)
        if self.op == "is_present":
            return present and got is not None and str(got).strip() != ""
        if self.op == "is_absent":
            return not present or got is None or str(got).strip() == ""
        if not present:
            return False
        if self.op == "is_true":
            return bool(got)
        if self.op == "is_false":
            return not bool(got)
        if self.op == "contains":
            return str(self.value).strip().lower() in str(got).lower()
        if self.op in ("at_least", "at_most"):
            try:
                left, right = float(got), float(self.value)     # type: ignore[arg-type]
            except (TypeError, ValueError):
                # A numeric comparison against something that is not a number is not "False", it
                # is a config error — but raising here would take down a sweep over every other
                # intake. Not firing is the safe half of the two: it drafts nothing.
                return False
            return left >= right if self.op == "at_least" else left <= right
        normalised_left = str(got).strip().lower()
        normalised_right = str(self.value).strip().lower()
        return (normalised_left == normalised_right) is (self.op == "equals")


class Monitor(BaseModel):
    """One clinic condition worth drafting a message about.

    There is no ``action``, no ``send``, no ``recipient`` and no ``to``. The only outputs a monitor
    can describe are a subject, a body, and which of ``recipients.py``'s two doors the address must
    come through.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str
    #: Prose, for whoever reads the file. Not parsed.
    when: str
    condition: Condition
    #: WHICH DOOR, never an address. ``committed_ledger`` means "the person a human already
    #: attested to"; ``typed_by_human`` means "a member of staff will type it in the review UI, and
    #: until they do there is no draft".
    recipient_from: RecipientOrigin
    #: A clinic ROLE. Deliberately not an address — see the module docstring.
    escalate_to: str
    subject_template: str
    body_template: str
    channel: Channel = "email"
    requires_human_approval: Literal[True] = True

    @model_validator(mode="after")
    def _templates_are_fillable(self) -> "Monitor":
        for column in ("subject_template", "body_template"):
            text = getattr(self, column)
            if not text.strip():
                raise ValueError(f"monitor {self.id!r}: {column} is empty")
            stripped = PLACEHOLDER.sub("", text)
            if "{" in stripped or "}" in stripped:
                raise ValueError(
                    f"monitor {self.id!r}: {column} contains a brace that is not a simple "
                    f"{{fact_name}} placeholder. This template language has no expressions on "
                    f"purpose — it interpolates values read off a patient's record")
        return self

    def placeholders(self, column: str) -> tuple[str, ...]:
        """The fact names one template needs, in the order they appear.

        Read off the template rather than declared beside it, so a template and its declared inputs
        cannot drift — the drift being the case where a placeholder silently renders as itself.
        """
        seen: list[str] = []
        for name in PLACEHOLDER.findall(getattr(self, column)):
            if name not in seen:
                seen.append(name)
        return tuple(seen)

    def pointer(self, column: str) -> SourcePointer:
        """A ``table_cell`` into the config file: this monitor's row, this column.

        A list of rows with named fields is a table in every sense that matters, and it resolves to
        a place a human can open — the same call ``claim.py`` makes about ``coverage_rules.yaml``.
        """
        return SourcePointer(source_id=MONITORS_SOURCE_ID, kind="table_cell",
                             table=MONITORS_TABLE, column=column, row_id=self.id)


class Defaults(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    channel: Channel = "email"
    requires_human_approval: Literal[True] = True


class Monitors(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    defaults: Defaults
    monitors: tuple[Monitor, ...] = ()

    @model_validator(mode="after")
    def _ids_are_unique(self) -> "Monitors":
        ids = [m.id for m in self.monitors]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(
                f"monitor ids must be unique; duplicated: {dupes}. A shadowed monitor is one whose "
                f"condition nobody can find by reading the file.")
        return self

    def by_id(self, monitor_id: str) -> Monitor:
        for monitor in self.monitors:
            if monitor.id == monitor_id:
                return monitor
        raise MonitorError(
            f"no monitor {monitor_id!r}; known: {[m.id for m in self.monitors]}")

    def due(self, observation: Observation) -> tuple[Monitor, ...]:
        """Every monitor whose condition holds for this observation.

        A lookup over rows, not a branch: adding a monitor changes what this returns without
        changing a line of it, which is what makes "a new automation is a config change" true
        rather than aspirational.
        """
        return tuple(m for m in self.monitors if m.condition.holds(observation))


def config_dir() -> pathlib.Path:
    """``packages/outbox/databridge_outbox/config`` — resolved from ``__file__``, never the cwd, so
    pytest, uvicorn and the dispatcher all read the same file."""
    return pathlib.Path(__file__).resolve().parent / "config"


def monitors_path() -> pathlib.Path:
    return config_dir() / MONITORS_SOURCE_ID


def load_monitors(path: Optional[pathlib.Path] = None) -> Monitors:
    config_path = pathlib.Path(path) if path is not None else monitors_path()
    if not config_path.exists():
        raise MonitorError(f"clinic monitors not found at {config_path}")
    raw = config_path.read_text(encoding="utf-8")
    offending = ADDRESS_SHAPED.findall(raw)
    if offending:
        raise MonitorError(
            f"{config_path} contains something shaped like an address or a URL "
            f"({sorted(set(offending))}). Recipients come from a committed ledger row or from a "
            f"human typing one (recipients.py); an address here would be a third door, and the "
            f"person who opened it would be whoever was editing YAML that afternoon.")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise MonitorError(f"{config_path} did not parse to a mapping")
    return Monitors.model_validate(data)


@functools.lru_cache(maxsize=1)
def monitors() -> Monitors:
    """The shipped table, cached. Pass a path to ``load_monitors`` for a test fixture."""
    return load_monitors()
