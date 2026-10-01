"""The Shell (workstream 3D): one model call, one validation, then a request or an escalation.

`ADAPTER_AGENT_SPEC` §1 gives the Shell one job and three prohibitions: it turns a task into a
`CapabilityRequest`, it holds no credentials, it holds no driver handle, and it never executes
anything. §10 adds the rule that decides whether the capability screen is real — a rejection is
never fed back to a model to try again.

The structural half of "no credentials" lives in `test_the_eight_acceptance_tests.py` (acceptance
7). This file is about behaviour: what the Shell does with a good plan, a bad one, and an
impossible one.

Every test here runs offline against `FakeBackend`. Nothing in the suite may depend on a live
model, and the Shell is the only layer that has one at all.
"""
from __future__ import annotations

import json

import pytest

from databridge_adapter import (
    CapabilityNotAdvertised,
    CapabilityRequest,
    EscalationRequired,
)
from databridge_adapter.shell import BRIEF_BUDGET_CHARS, AdapterShell, ProposedRead
from databridge_harness import FakeBackend, Harness


def shell_for(core, response) -> AdapterShell:
    text = response if isinstance(response, str) else json.dumps(response)
    return AdapterShell(description=core.describe(), harness=Harness(FakeBackend(text)))


# =======================================================================================
# The happy path
# =======================================================================================

def test_a_valid_plan_becomes_a_capability_request(core, analyst):
    shell = shell_for(core, {"capability": "row_read", "entity": "party",
                             "fields": ["party_id", "legal_name"],
                             "order_by": ["legal_name"], "limit": 50})
    req = shell.propose("Who are the borrowers in specialty chemicals?", principal=analyst)
    assert isinstance(req, CapabilityRequest)
    assert req.source_id == "core_banking" and req.capability == "row_read"
    assert req.projection.entity == "party"
    assert req.projection.key == ("party_id",)
    assert req.order_by[-1] == "party_id", "the ordering must END with the key to be total"
    assert req.limit == 50


def test_the_request_the_shell_produces_actually_executes(core, analyst):
    """The end-to-end shape of §1: Shell -> Policy -> Core. The Shell hands over a request and
    plays no further part; the Core obtains its own admission."""
    shell = shell_for(core, {"capability": "row_read", "entity": "party",
                             "fields": ["party_id", "legal_name"], "limit": 5})
    result = core.resolve(shell.propose("list borrowers", principal=analyst))
    assert result.rows.rows and result.rows.snapshot.query_hash


def test_the_key_is_projected_and_ordered_by_even_when_the_model_forgets(core, analyst):
    """Not the model's decision to make. A pointer cites the key, so a plan that omitted it would
    produce uncitable rows — and asking a model to remember that every single time is asking for
    the one thing it will eventually forget."""
    shell = shell_for(core, {"capability": "row_read", "entity": "party",
                             "fields": ["legal_name"], "order_by": ["legal_name"]})
    req = shell.propose("borrower names", principal=analyst)
    assert "party_id" in req.projection.fields
    assert req.order_by == ("legal_name", "party_id")


def test_the_shell_caps_the_limit_it_was_given(core, analyst):
    shell = shell_for(core, {"capability": "row_read", "entity": "party", "limit": 90_000})
    assert shell.propose("everything", principal=analyst, limit=25).limit == 25


# =======================================================================================
# Fail closed — never a retry loop
# =======================================================================================

def test_an_unadvertised_capability_is_rejected_once_and_not_retried(core, analyst):
    """§10: "reject; log; do not escalate to a model." A screen whose rejection is a prompt is a
    screen that eventually passes anything."""
    backend = FakeBackend(json.dumps({"capability": "bulk_export", "entity": "party"}))
    shell = AdapterShell(description=core.describe(), harness=Harness(backend))
    with pytest.raises(CapabilityNotAdvertised, match="never handed back to a model"):
        shell.propose("export the book", principal=analyst)
    assert backend.call_count == 1, (
        "the Shell called the model again after a rejection: that is the retry loop that turns a "
        "capability screen into a guessing game")


def test_an_unknown_entity_escalates_rather_than_guessing(core, analyst):
    shell = shell_for(core, {"capability": "row_read", "entity": "borrowers"})
    with pytest.raises(EscalationRequired, match="describes no entity"):
        shell.propose("borrowers please", principal=analyst)


def test_an_unknown_field_is_refused_rather_than_silently_dropped(core, analyst):
    """A projection quietly narrowed to the fields that happened to exist answers a DIFFERENT
    question than the one asked, and its citation looks just as good."""
    shell = shell_for(core, {"capability": "row_read", "entity": "party",
                             "fields": ["party_id", "credit_score"]})
    with pytest.raises(EscalationRequired, match="Refused rather than dropped"):
        shell.propose("borrower credit scores", principal=analyst)


def test_an_explicit_escalation_records_the_reason_and_acts_on_none_of_it(core, analyst):
    """§6/§9's slower, correct path: a human adds a named query to the pack, which is then
    reviewed once and deterministic forever."""
    shell = shell_for(core, {"capability": "row_read", "entity": "party", "escalate": True,
                             "escalation_reason": "needs a join across crm"})
    with pytest.raises(EscalationRequired) as exc:
        shell.propose("cross-source question", principal=analyst)
    assert "needs a join across crm" in str(exc.value)
    assert "pack-declared named query" in str(exc.value)


def test_the_validation_is_reachable_without_a_model_at_all(core, analyst):
    """`validate` is public so the screen can be tested, and audited, without inference."""
    shell = shell_for(core, {})
    req = shell.validate(ProposedRead(capability="row_read", entity="party",
                                      fields=("party_id",)), principal=analyst)
    assert req.projection.fields == ("party_id",)


def test_the_proposal_model_has_no_field_a_source_would_execute():
    """No `sql`, no `predicate`, no free text the system acts on. "A semantic layer fails
    explicitly. Text-to-SQL fails plausibly." (§6)"""
    fields = set(ProposedRead.model_fields)
    for smell in ("sql", "query", "predicate", "where", "filter", "expression", "raw"):
        assert not any(smell in name for name in fields), f"ProposedRead carries {smell!r}"
    assert fields == {"capability", "entity", "fields", "order_by", "limit", "escalate",
                      "escalation_reason"}


# =======================================================================================
# The token budget (§9a) — a design requirement, not an optimisation
# =======================================================================================

def test_the_brief_stays_within_its_token_budget(core):
    """> "Gate tool-definition tokens in CI. A per-adapter budget with a failing test, because
    > this number only ever grows and nobody notices until the bill does."

    Anthropic documents ~55,000 tokens of MCP tool definitions before any work happens. This
    budget is roughly 1,500 tokens for a whole adapter, which is where the number should sit.
    """
    brief = core and AdapterShell(description=core.describe(),
                                  harness=Harness(FakeBackend("{}"))).describe_for_model()
    assert len(brief) <= BRIEF_BUDGET_CHARS, (
        f"the capability brief is {len(brief)} chars against a {BRIEF_BUDGET_CHARS} budget; an "
        f"adapter's brief is presented on every planning call and this number only ever grows")


def test_the_shell_sees_its_own_adapter_and_never_the_fleet(core):
    """§9a: "Never present the whole fleet's verbs to a model at once." The process split enforces
    it physically; this asserts the Shell does not undo it by rendering a registry."""
    shell = AdapterShell(description=core.describe(), harness=Harness(FakeBackend("{}")))
    brief = json.loads(shell.describe_for_model())
    assert brief["source_id"] == "core_banking"
    assert set(brief["capabilities"]) <= {"row_read", "structural_read"}
    assert "crm" not in shell.describe_for_model()


def test_the_brief_carries_schema_and_trust_but_no_data(core):
    shell = AdapterShell(description=core.describe(), harness=Harness(FakeBackend("{}")))
    brief = json.loads(shell.describe_for_model())
    party = brief["entities"]["party"]
    assert party["key"] == ["party_id"]
    assert "untrusted" in party["fields"]["notes"], (
        "the planner must be able to see that a column is attacker-writable")
    assert "sensitive" in party["fields"]["date_of_birth"]
    # No values, no counts, no samples.
    text = shell.describe_for_model()
    for leaked in ("CB-P-0000", "Fairmont", "row_count", "sample"):
        assert leaked not in text


def test_the_shell_asks_for_a_task_class_and_never_a_model_name(core, analyst):
    """Repo rule: routing is config (`config/models.yaml`), so swapping the deployment to local
    models is a config diff rather than a code hunt."""
    seen = {}

    class Recording(FakeBackend):
        def generate(self, *, model, messages, **kwargs):
            seen["model"] = model
            return super().generate(model=model, messages=messages, **kwargs)

    shell = AdapterShell(
        description=core.describe(),
        harness=Harness(Recording(json.dumps({"capability": "row_read", "entity": "party"}))),
        task_class="agentic_orchestration")
    shell.propose("anything", principal=analyst)
    assert seen["model"], "the harness resolved a model from the task class"
    assert shell._task_class == "agentic_orchestration"
    import inspect

    source = inspect.getsource(AdapterShell)
    for vendor in ("claude", "gpt-", "sonnet", "anthropic/", "openai/"):
        assert vendor not in source.lower(), f"the Shell names a model: {vendor}"
