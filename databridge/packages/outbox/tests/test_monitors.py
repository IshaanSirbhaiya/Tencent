"""Automations are config, not code — the third hard constraint, pointed at workflows.

`coverage_rules.yaml` makes "adding an insurer is a YAML change" true rather than aspirational, and
`test_no_issuer_branching` is what keeps it true. This is the same test for monitors: a new clinic
automation must be a row, and `monitors.py` must contain no branch on which row it is.
"""
from __future__ import annotations

import inspect
import re

import pytest

from databridge_core import GroundedValue, SourcePointer
import databridge_outbox.monitors as monitors_module
from databridge_outbox.monitors import (
    Condition,
    Monitor,
    MonitorError,
    Monitors,
    Observation,
    load_monitors,
    monitors_path,
)

PTR = SourcePointer(source_id="probe.txt", kind="doc_span", document_id="probe.txt",
                    char_start=0, char_end=1)


def fact(value) -> GroundedValue:
    return GroundedValue(value=value, pointer=PTR)


def observation(**facts) -> Observation:
    return Observation(intake_id="intake:probe",
                       facts={k: fact(v) for k, v in facts.items()})


# --- the shipped table -----------------------------------------------------------------


def test_the_package_does_not_shadow_the_monitors_submodule():
    """`from .monitors import monitors` in the package __init__ would bind the accessor over the
    submodule, and `import databridge_outbox.monitors as m` would hand back a function. It bit
    this file once; the accessor is re-exported as `clinic_monitors` instead."""
    import databridge_outbox

    assert databridge_outbox.monitors is monitors_module
    assert databridge_outbox.clinic_monitors().version == monitors_module.monitors().version


def test_the_shipped_table_loads_and_has_monitors_to_check(table):
    """Guards every test below: an empty table would make them vacuously green."""
    assert table.version == 1
    assert {m.id for m in table.monitors} == {
        "intake_blocked_too_long", "coverage_conflict_on_file", "questionnaire_outstanding"}


def test_every_monitor_requires_a_human_and_declares_a_door(table):
    for monitor in table.monitors:
        assert monitor.requires_human_approval is True
        assert monitor.recipient_from in ("committed_ledger", "typed_by_human")


def test_no_monitor_can_name_an_address_or_a_channel_id(table):
    """`escalate_to` is a ROLE. The same rule `agent_playbooks.yaml` states in its header."""
    raw = monitors_path().read_text(encoding="utf-8")
    assert not monitors_module.ADDRESS_SHAPED.search(raw)
    for monitor in table.monitors:
        assert "@" not in monitor.escalate_to and "://" not in monitor.escalate_to


# --- no branching on a monitor id --------------------------------------------------------


def test_the_loader_does_not_branch_on_which_monitor_it_is():
    """The constraint-3 test, transposed. Adding an automation must be a config change.

    Written against every monitor id in the shipped table rather than a hardcoded string, so a
    monitor added tomorrow is covered by it too.
    """
    source = inspect.getsource(monitors_module)
    for monitor_id in {m.id for m in load_monitors(monitors_path()).monitors}:
        assert monitor_id not in source, (
            f"monitors.py names {monitor_id!r}; a monitor the code knows about by name is not a "
            f"config row, it is a branch with a YAML file beside it")
    # A comparison against a STRING LITERAL is the branch. `by_id`'s `monitor.id == monitor_id`
    # is a lookup over rows — the same shape `rules.py` and `playbooks.py` use, and the thing that
    # makes adding a row change behaviour without changing code.
    assert not re.search(r"\.id\s*==\s*[\"']", source), "monitors.py branches on a monitor id"
    assert not re.search(r"recipient_from\s*==\s*[\"']", source)


def test_a_new_automation_is_a_row_and_nothing_else(tmp_path):
    """The proof: a monitor nobody wrote code for loads, evaluates and fires."""
    config = tmp_path / "clinic_monitors.yaml"
    config.write_text("""
version: 1
defaults:
  channel: email
  requires_human_approval: true
monitors:
  - id: interpreter_needed
    title: The patient needs an interpreter and none is booked
    when: A language was recorded and no interpreter is on the visit.
    condition: {fact: interpreter_language, op: is_present}
    recipient_from: typed_by_human
    escalate_to: front_desk_lead
    subject_template: An interpreter for your visit
    body_template: We are arranging a {interpreter_language} interpreter for your visit.
""", encoding="utf-8")
    table = load_monitors(config)
    fired = table.due(observation(interpreter_language="Tamil"))
    assert [m.id for m in fired] == ["interpreter_needed"]
    assert table.by_id("interpreter_needed").placeholders("body_template") == \
        ("interpreter_language",)


# --- what the loader refuses ---------------------------------------------------------------


def _config(tmp_path, body: str):
    path = tmp_path / "clinic_monitors.yaml"
    path.write_text("version: 1\ndefaults: {channel: email, requires_human_approval: true}\n"
                    + body, encoding="utf-8")
    return path


def test_an_address_anywhere_in_the_file_is_refused(tmp_path):
    """The scan is over the whole file, so it covers a field nobody has invented yet."""
    path = _config(tmp_path, """
monitors:
  - id: leaky
    title: t
    when: w
    condition: {fact: x, op: is_true}
    recipient_from: typed_by_human
    escalate_to: billing_lead@clinic.example
    subject_template: s
    body_template: b
""")
    with pytest.raises(MonitorError, match="shaped like an address"):
        load_monitors(path)


def test_a_webhook_url_is_refused_too(tmp_path):
    path = _config(tmp_path, """
monitors:
  - id: leaky
    title: t
    when: https://hooks.example.test/abc
    condition: {fact: x, op: is_true}
    recipient_from: typed_by_human
    escalate_to: billing_lead
    subject_template: s
    body_template: b
""")
    with pytest.raises(MonitorError, match="shaped like an address"):
        load_monitors(path)


def test_a_monitor_that_does_not_need_a_human_cannot_be_expressed(tmp_path):
    path = _config(tmp_path, """
monitors:
  - id: rogue
    title: t
    when: w
    condition: {fact: x, op: is_true}
    recipient_from: typed_by_human
    escalate_to: billing_lead
    subject_template: s
    body_template: b
    requires_human_approval: false
""")
    with pytest.raises(Exception):
        load_monitors(path)


def test_a_monitor_cannot_declare_an_action(tmp_path):
    """`extra="forbid"`: the only verb this file can express is "draft something"."""
    path = _config(tmp_path, """
monitors:
  - id: rogue
    title: t
    when: w
    condition: {fact: x, op: is_true}
    recipient_from: typed_by_human
    escalate_to: billing_lead
    subject_template: s
    body_template: b
    action: send
""")
    with pytest.raises(Exception):
        load_monitors(path)


def test_duplicate_monitor_ids_are_refused(tmp_path):
    path = _config(tmp_path, """
monitors:
  - {id: a, title: t, when: w, condition: {fact: x, op: is_true},
     recipient_from: typed_by_human, escalate_to: r, subject_template: s, body_template: b}
  - {id: a, title: t, when: w, condition: {fact: y, op: is_true},
     recipient_from: typed_by_human, escalate_to: r, subject_template: s, body_template: b}
""")
    with pytest.raises(Exception, match="unique"):
        load_monitors(path)


def test_a_missing_config_is_loud():
    with pytest.raises(MonitorError, match="not found"):
        load_monitors(monitors_path().parent / "nope.yaml")


# --- conditions -----------------------------------------------------------------------------


def test_an_unknown_operator_is_refused():
    with pytest.raises(Exception):
        Condition(fact="x", op="regex_match", value=".*")     # type: ignore[arg-type]


def test_a_comparison_operator_needs_a_value():
    with pytest.raises(Exception, match="needs a `value`"):
        Condition(fact="x", op="at_least")


def test_a_unary_operator_refuses_a_value_nothing_would_read():
    with pytest.raises(Exception, match="takes no `value`"):
        Condition(fact="x", op="is_true", value=3)


@pytest.mark.parametrize("op,value,got,expected", [
    ("at_least", 20, 25, True),
    ("at_least", 20, 15, False),
    ("at_least", 20, "not a number", False),
    ("at_most", 20, 15, True),
    ("equals", "PEE226", "pee226", True),
    ("not_equals", "PEE226", "WELL2", True),
    ("contains", "ecg", "Electrocardiogram (ECG)", True),
    ("is_true", None, True, True),
    ("is_false", None, False, True),
    ("is_present", None, "x", True),
    ("is_present", None, "  ", False),
    ("is_absent", None, "", True),
])
def test_the_operators_do_what_they_say(op, value, got, expected):
    condition = Condition(fact="f", op=op, value=value)
    assert condition.holds(observation(f=got)) is expected


def test_a_missing_fact_does_not_fire_and_does_not_crash_the_sweep():
    """A monitor should not fire on data nobody has, and should not take down the other intakes."""
    assert Condition(fact="absent", op="at_least", value=1).holds(observation()) is False
    assert Condition(fact="absent", op="is_true").holds(observation()) is False
    assert Condition(fact="absent", op="is_absent").holds(observation()) is True


def test_due_returns_only_the_monitors_whose_condition_holds(table):
    fired = table.due(observation(minutes_blocked=45, blocked_on="an in-person identity check"))
    assert [m.id for m in fired] == ["intake_blocked_too_long"]
    assert table.due(observation(minutes_blocked=5)) == ()


# --- templates -------------------------------------------------------------------------------


def test_placeholders_are_read_off_the_template_not_declared_beside_it(table):
    monitor = table.by_id("coverage_conflict_on_file")
    assert set(monitor.placeholders("body_template")) == {"unauthorised_test", "package_code"}
    assert monitor.placeholders("subject_template") == ()


def test_a_template_with_an_expression_in_it_is_refused():
    with pytest.raises(Exception, match="brace"):
        Monitor(id="x", title="t", when="w",
                condition=Condition(fact="f", op="is_true"),
                recipient_from="typed_by_human", escalate_to="r",
                subject_template="s", body_template="{patient.name!r:>20}")


def test_a_monitor_pointer_resolves_to_the_config_row_it_names(table):
    monitor = table.by_id("questionnaire_outstanding")
    pointer = monitor.pointer("body_template")
    assert (pointer.source_id, pointer.table, pointer.column, pointer.row_id) == \
        ("clinic_monitors.yaml", "monitors", "body_template", "questionnaire_outstanding")
    # And the cell it names really holds that text.
    import yaml

    rows = yaml.safe_load(monitors_path().read_text(encoding="utf-8"))["monitors"]
    row = next(r for r in rows if r["id"] == pointer.row_id)
    assert " ".join(row[pointer.column].split()) == " ".join(monitor.body_template.split())


def test_the_table_refuses_an_unknown_monitor_id_rather_than_returning_nothing(table):
    with pytest.raises(MonitorError, match="no monitor"):
        table.by_id("does_not_exist")


def test_an_observation_fact_must_be_grounded():
    """`facts: Mapping[str, GroundedValue]` — a bare string cannot be observed."""
    with pytest.raises(Exception):
        Observation(intake_id="i", facts={"x": "just a string"})   # type: ignore[dict-item]


def test_the_monitors_type_is_closed():
    with pytest.raises(Exception):
        Monitors.model_validate({"version": 1,
                                 "defaults": {"channel": "email",
                                              "requires_human_approval": True},
                                 "monitors": [], "send_via": "smtp"})
