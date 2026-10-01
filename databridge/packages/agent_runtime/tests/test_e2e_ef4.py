"""EF4 — the E2E-2026-10-01 findings that land in the agent runtime's planner context and run header
(wiki/40-reviews/R-020-EF4-compiler-runtime.md): F-27 (planner replayed from cache across runs),
F-29 ("today" in the deployment zone), F-30 (run id available to the planner), F-33 (tokens and
cost on the run header) and F-34 (screen exclusions surfaced as "withheld (rule id)").

Expectations are derived from the inputs (the clock, the env, the fake backend's metered usage,
the ingest ledgers the test writes) — never a recalled answer. No network, no live model.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from agent_runtime_helpers import HARNESS_CONFIG, briefing_spec, followup_spec, make_pair
from databridge_harness import FakeBackend, Harness

from databridge_agent_runtime import run_agent
from databridge_agent_runtime.runs import read_run, find_run

FINAL = json.dumps({"final": {"claims": [], "clarifications": []}})


@pytest.fixture
def dirs(tmp_path):
    return {"runs_dir": tmp_path / "runs", "capture_dir": tmp_path / "capture"}


def _harness(*, cache=None, cost=0.0, responses=FINAL):
    backend = FakeBackend(responses, tokens_in=7, tokens_out=11, cost_usd=cost)
    return Harness(backend, config=HARNESS_CONFIG, profile="cloud", cache=cache), backend


def _run(spec, decl, harness, *, store, gateway, compile_inputs, dirs, **kw):
    return run_agent(spec, decl, store=store, harness=harness, gateway=gateway,
                     trigger={"type": "manual", "by": "dana"}, compile_inputs=compile_inputs,
                     notifier=lambda _e: None, withheld=kw.pop("withheld", ()), **dirs, **kw)


def _sent(backend, call: int = 0) -> str:
    return json.dumps(backend.calls[call]["messages"], ensure_ascii=False)


# ================================================================================================
# F-27 — an agent run is never replayed from the harness cache of an earlier firing
# ================================================================================================
def test_f27_a_second_firing_calls_the_model_again_even_with_a_shared_cache(
        store, gateway, compile_inputs, dirs):
    shared: dict = {}                          # the gateway keeps ONE harness (and cache) alive
    harness, backend = _harness(cache=shared)
    spec, decl = make_pair(briefing_spec())
    first = _run(spec, decl, harness, store=store, gateway=gateway,
                 compile_inputs=compile_inputs, dirs=dirs)
    calls_after_first = backend.call_count
    second = _run(spec, decl, harness, store=store, gateway=gateway,
                  compile_inputs=compile_inputs, dirs=dirs)
    assert calls_after_first >= 1
    assert first.run_id != second.run_id
    # the second firing reached the backend: nothing was replayed from the first run's cache
    assert backend.call_count == 2 * calls_after_first
    assert second.tokens > 0


# ================================================================================================
# F-29 / F-30 — the planner is told this run's id and today's date in the deployment's zone
# ================================================================================================
@pytest.mark.parametrize("zone", ["Pacific/Kiritimati", "Pacific/Pago_Pago"])
def test_f29_f30_planner_sees_the_run_id_and_the_deployment_date(
        store, gateway, compile_inputs, dirs, monkeypatch, zone):
    monkeypatch.setenv("WALNUT_TIMEZONE", zone)          # UTC+14 / UTC-11: never the UTC date
    before = dt.datetime.now(ZoneInfo(zone)).date().isoformat()
    harness, backend = _harness()
    spec, decl = make_pair(briefing_spec())
    rec = _run(spec, decl, harness, store=store, gateway=gateway,
               compile_inputs=compile_inputs, dirs=dirs)
    after = dt.datetime.now(ZoneInfo(zone)).date().isoformat()
    sent = _sent(backend)
    assert rec.run_id in sent
    assert zone in sent
    assert before in sent or after in sent


def test_f29_a_scheduled_agent_uses_its_schedule_zone(store, gateway, compile_inputs, dirs,
                                                      monkeypatch):
    monkeypatch.setenv("WALNUT_TIMEZONE", "Pacific/Pago_Pago")
    spec_d = followup_spec()
    zone = spec_d["trigger"]["schedule"]["timezone"]
    harness, backend = _harness()
    spec, decl = make_pair(spec_d, save_followup_draft=False, log_followup_task=False)
    _run(spec, decl, harness, store=store, gateway=gateway, compile_inputs=compile_inputs,
         dirs=dirs)
    sent = _sent(backend)
    assert f"timezone {zone}" in sent and "timezone Pacific/Pago_Pago" not in sent


def test_f29_default_zone_when_nothing_is_configured(store, gateway, compile_inputs, dirs,
                                                     monkeypatch):
    from databridge_agent_runtime.runtime import DEFAULT_TIMEZONE
    monkeypatch.delenv("WALNUT_TIMEZONE", raising=False)
    harness, backend = _harness()
    spec, decl = make_pair(briefing_spec())
    _run(spec, decl, harness, store=store, gateway=gateway, compile_inputs=compile_inputs,
         dirs=dirs)
    assert f"timezone {DEFAULT_TIMEZONE}" in _sent(backend)


def test_f29_the_run_context_does_not_change_the_prompt_version(store, gateway, compile_inputs,
                                                                dirs):
    """The per-run context is its own message: the system prompt (and so `prompt_version`) stays
    the same across firings, so §5.8's prompt_version still names ONE prompt."""
    harness, backend = _harness()
    spec, decl = make_pair(briefing_spec())
    a = _run(spec, decl, harness, store=store, gateway=gateway, compile_inputs=compile_inputs,
             dirs=dirs)
    b = _run(spec, decl, harness, store=store, gateway=gateway, compile_inputs=compile_inputs,
             dirs=dirs)
    assert a.answer.produced_by["prompt_version"] == b.answer.produced_by["prompt_version"]
    assert backend.calls[0]["messages"][0] == backend.calls[1]["messages"][0]


# ================================================================================================
# F-33 — the run header carries tokens and cost (§5.8), not only the answer footer
# ================================================================================================
def test_f33_run_header_carries_tokens_and_cost(store, gateway, compile_inputs, dirs):
    per_call_cost = 0.0125
    scripted = [json.dumps({"tool_calls": [{"tool": "graph.search",
                                            "args": {"query": "Northwind", "k": 3}}]}), FINAL]
    harness, backend = _harness(cost=per_call_cost, responses=scripted)
    spec, decl = make_pair(briefing_spec())
    rec = _run(spec, decl, harness, store=store, gateway=gateway, compile_inputs=compile_inputs,
               dirs=dirs)
    calls = backend.call_count
    assert calls == 2
    on_disk = read_run(find_run(rec.run_id, dirs["runs_dir"]))
    assert on_disk.tokens == calls * (7 + 11)
    assert on_disk.cost_usd == pytest.approx(calls * per_call_cost)
    # the footer and the header agree
    assert f"{on_disk.tokens} token(s)" in on_disk.answer.markdown


def test_f33_a_refused_run_has_zero_tokens_not_a_missing_key(store, compile_inputs, dirs):
    harness, backend = _harness()
    spec, decl = make_pair(briefing_spec())
    rec = run_agent(spec, decl, store=store, harness=harness, gateway=None,
                    trigger={"type": "manual"}, compile_inputs=compile_inputs,
                    notifier=lambda _e: None, withheld=(), **dirs)
    assert rec.status == "refused" and backend.call_count == 0
    d = rec.to_dict()
    assert d["tokens"] == 0 and d["cost_usd"] == 0.0


# ================================================================================================
# F-34 — items the ingestion screen withheld are named to the planner, with rule ids, no content
# ================================================================================================
SECRET = "Band C: 71,000 - 84,000"       # what the blocked file says; it must never reach the model


def _ingest_run(root: Path, source: str, run: str, items: list[dict]) -> None:
    d = root / source / run
    d.mkdir(parents=True)
    inv, gov = [], []
    for it in items:
        inv.append({"item_id": it["id"], "source_id": source, "kind": "document",
                    "uri": it["uri"], "rel": it["rel"], "decision": "collect",
                    "name": it["rel"].rsplit("/", 1)[-1]})
        if "decision" in it:
            gov.append({"item_id": it["id"], "uri": it["uri"], "sha256": "0" * 64,
                        "rules_source": "screen.yaml", "at": "2026-09-30T20:00:00Z",
                        "decision": it["decision"],
                        "fired": [{"rule": r, "kind": "decisive", "count": 1} for r in it["rules"]],
                        "rationale": f"matched {SECRET}", "reverse": "fix it", "score": None,
                        "redactions": [], "kind": "document"})
    (d / "inventory.jsonl").write_text("".join(json.dumps(r) + "\n" for r in inv), "utf-8")
    (d / "governance.jsonl").write_text("".join(json.dumps(r) + "\n" for r in gov), "utf-8")


def test_f34_screen_withheld_items_are_named_to_the_planner_without_content(
        tmp_path, store, gateway, compile_inputs, dirs, monkeypatch):
    pytest.importorskip("databridge_mcp_ingest.records")
    from databridge_agent_runtime.withheld import screen_withheld

    root = tmp_path / "ingest"
    comp = {"id": "a1", "uri": "C:/corpus/hr/Compensation_Bands_2026.md",
            "rel": "hr/Compensation_Bands_2026.md", "decision": "block",
            "rules": ["confidentiality_marking"]}
    policy = {"id": "a2", "uri": "C:/corpus/hr/Remote_Working_Policy.docx",
              "rel": "hr/Remote_Working_Policy.docx", "decision": "allow", "rules": []}
    card = {"id": "a3", "uri": "C:/corpus/sales/Rate_Card.md", "rel": "sales/Rate_Card.md",
            "decision": "review", "rules": ["pricing_heading", "currency_table"]}
    gone = {"id": "a4", "uri": "C:/corpus/old/Removed.md", "rel": "old/Removed.md",
            "decision": "block", "rules": ["private_key_block"]}
    _ingest_run(root, "larkspur-files", "20260930T190000Z-aaaaaa", [comp, policy, card, gone])
    # a later incremental run: the removed file is no longer inventoried; the card was approved
    later_card = {**card, "decision": "allow", "rules": []}
    unchanged_policy = {k: v for k, v in policy.items() if k not in ("decision", "rules")}
    _ingest_run(root, "larkspur-files", "20260930T200000Z-bbbbbb",
                [comp, later_card, unchanged_policy])
    monkeypatch.setenv("WALNUT_INGEST_DIR", str(root))

    register = screen_withheld(["larkspur-files", "larkspur-db"])
    # derived from the ledgers: the latest decision per inventoried item, allow excluded
    expected = {(comp["rel"], "block", tuple(comp["rules"]))}
    assert {(w["item"], w["decision"], tuple(w["rules"])) for w in register} == expected
    assert all(w["source"] == "larkspur-files" for w in register)

    harness, backend = _harness()
    spec, decl = make_pair(briefing_spec())
    rec = run_agent(spec, decl, store=store, harness=harness, gateway=gateway,
                    trigger={"type": "manual", "question": "summarise the compensation bands"},
                    compile_inputs=compile_inputs, notifier=lambda _e: None, **dirs)
    sent = _sent(backend)
    assert rec.status == "completed"
    assert comp["rel"] in sent and "confidentiality_marking" in sent and "withheld" in sent
    assert SECRET not in sent                      # the rationale (content-bearing) is never sent
    assert comp["uri"] not in sent                 # nor the absolute path on the ingest host
    assert policy["rel"] not in sent and card["rel"] not in sent and gone["rel"] not in sent


def test_f34_no_ingest_ledgers_means_an_empty_register(tmp_path, monkeypatch):
    pytest.importorskip("databridge_mcp_ingest.records")
    from databridge_agent_runtime.withheld import screen_withheld
    monkeypatch.setenv("WALNUT_INGEST_DIR", str(tmp_path / "nothing-here"))
    assert screen_withheld(["larkspur-files"]) == []
