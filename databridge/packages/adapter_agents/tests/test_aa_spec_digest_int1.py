"""INT1 item 6 — the adapter-agent spec digest has ONE definition: the workflow contract's.

`registry.spec_digest` used to hash `dump_spec` (the presentation YAML); the contract's
`databridge_workflow_spec.spec_digest` hashes `canonical_json`. Stored digests (the append-only
history, run records, `produced_by.agent_version`) therefore change once. A history line written
the OLD way must still be readable — recognised as legacy and re-digested, or flagged when it
matches neither rule — and never rejected.
"""
from __future__ import annotations

import hashlib
import json

from databridge_workflow_spec import AdapterAgentSpec, dump_spec
from databridge_workflow_spec import spec_digest as contract_digest

from databridge_adapter_agents import AdapterAgentRegistry
from databridge_adapter_agents import registry as reg_mod


def _spec() -> AdapterAgentSpec:
    return AdapterAgentSpec.model_validate({
        "id": "digest_aa", "title": "digest check", "created_from": "keep the crm right",
        "connections": ["crm"], "extract": {"pack": "workspace"}, "status": "draft"})


def _old_rule(spec: AdapterAgentSpec) -> str:
    """The retired rule, restated from its definition (sha256 over the presentation YAML)."""
    return "sha256:" + hashlib.sha256(dump_spec(spec).encode("utf-8")).hexdigest()


def test_registry_digest_is_the_contract_digest():
    spec = _spec()
    assert reg_mod.spec_digest(spec) == contract_digest(spec)
    assert _old_rule(spec) != contract_digest(spec)          # non-vacuous: the rules differ


def test_new_history_lines_carry_the_contract_digest(tmp_path):
    reg = AdapterAgentRegistry(tmp_path)
    spec = reg.create(_spec(), by="analyst-7")
    [line] = reg.history(spec.id)
    assert line["spec_digest"] == contract_digest(spec)
    assert line["digest_scheme"] == "canonical_json"


def _rewrite_history(reg: AdapterAgentRegistry, agent_id: str, digest: str) -> None:
    path = reg._history_path(agent_id)
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    for r in rows:
        r["spec_digest"] = digest
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")


def test_a_history_line_digested_the_old_way_is_read_and_re_digested(tmp_path):
    reg = AdapterAgentRegistry(tmp_path)
    spec = reg.create(_spec(), by="analyst-7")
    _rewrite_history(reg, spec.id, _old_rule(spec))           # as the old registry wrote it
    [line] = reg.history(spec.id)                             # readable, not rejected
    assert line["spec_digest"] == _old_rule(spec)             # the stored value is kept verbatim
    assert line["digest_scheme"] == "legacy_dump_spec"
    assert line["spec_digest_current"] == contract_digest(spec)
    assert reg.get(spec.id) == spec                           # the agent itself still loads
    # and the status walk that reads history still works over a legacy line
    reg.pause(spec.id, by="analyst-7")
    assert [r["action"] for r in reg.history(spec.id)] == ["create", "pause"]


def test_a_history_line_matching_neither_rule_is_flagged_not_rejected(tmp_path):
    reg = AdapterAgentRegistry(tmp_path)
    spec = reg.create(_spec(), by="analyst-7")
    forged = "sha256:" + "0" * 64
    _rewrite_history(reg, spec.id, forged)
    [line] = reg.history(spec.id)
    assert line["spec_digest"] == forged
    assert line["digest_scheme"] == "unverified"
    assert line["spec_digest_current"] == contract_digest(spec)
