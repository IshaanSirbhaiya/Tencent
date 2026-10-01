"""EF1 — the E2E-2026-10-01 findings that land in the agent runtime (wiki/40-reviews/R-020).

Every test names the finding it proves. Expectations are derived from what the fake source returns
and the rule under test — never a recalled answer. No network, no live model.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_runtime_helpers import (FakeGateway, briefing_spec, followup_spec, make_pair, person,
                                   refs_in, save_pair, scripted_harness)

from databridge_agent_runtime import (
    ActionLog,
    ActionRefused,
    ConnectionAdvert,
    GatewayResult,
    ToolAdvert,
    approve,
    run_agent,
)
from databridge_agent_runtime.runs import actions_path, find_run

ingest_screen = pytest.importorskip("databridge_mcp_ingest.screen")


@pytest.fixture
def dirs(tmp_path):
    return {"runs_dir": tmp_path / "runs", "capture_dir": tmp_path / "capture"}


@pytest.fixture
def decide(tmp_path, compile_inputs, dirs):
    return {**dirs, "agents_dir": tmp_path / "agents", "compile_inputs": compile_inputs}


@pytest.fixture
def screen():
    """The bundled default screen — the same file ingestion falls back to."""
    from databridge_mcp_ingest import paths as ingest_paths
    return ingest_screen.load_screen(path=ingest_paths.default_screen_path())


def _run(spec, decl, script, *, store, gateway, compile_inputs, dirs, **kw):
    harness, backend = scripted_harness(script)
    rec = run_agent(spec, decl, store=store, harness=harness, gateway=gateway,
                    trigger={"type": "manual", "by": "dana"}, compile_inputs=compile_inputs,
                    notifier=lambda _e: None, **dirs, **kw)
    return rec, backend


def _sent_to_model(backend) -> str:
    """Everything the planner model was shown, across every call."""
    return json.dumps([c for c in getattr(backend, "calls", [])], default=str)


def _events(rec, dirs, action_id=None):
    return ActionLog(actions_path(find_run(rec.run_id, dirs["runs_dir"]))).events(action_id)


# ------------------------------------------------------------------------------------------------
# a gateway shaped like postgres-mcp: Python-repr rows, errors as text with isError false
# ------------------------------------------------------------------------------------------------
PG_ROWS = [{"invoice_no": "INV-2026-0057", "customer_id": "C-1002",
            "due_on": dt.date(2026, 9, 15), "net_gbp": Decimal("12750.00"),
            "contact_email": "rowan.achterberg@pellucid.example"}]
EXECUTE_SQL_SCHEMA = {"type": "object", "properties": {"sql": {"type": "string"}},
                      "required": ["sql"], "additionalProperties": False}


class PgGateway(FakeGateway):
    """`larkspur-db` answers `str(list_of_row_dicts)`; the writer declares its tool's input schema
    and reports a failed statement as ``Error: …`` text (isError false), as postgres-mcp does."""

    def __init__(self, rows=None, *, write_reply="No results", files=None, blobs=None) -> None:
        super().__init__(files)
        self.rows = list(PG_ROWS if rows is None else rows)
        self.write_reply = write_reply
        self.blobs = dict(blobs or {})

    def advertise(self, connection):
        if connection == "larkspur_db_writer":
            return ConnectionAdvert(connection=connection, declared_in="var/sources/dbw.yaml",
                                    tools=(ToolAdvert("execute_sql", writes=True,
                                                      input_schema=EXECUTE_SQL_SCHEMA),))
        if connection == "larkspur-files":
            adv = super().advertise(connection)
            return ConnectionAdvert(connection=connection, declared_in=adv.declared_in,
                                    tools=adv.tools + (ToolAdvert("read_bytes", writes=False),))
        return super().advertise(connection)

    def write_error_prefixes(self, connection):
        return ("Error:",)                         # declared by the SQL dialect profile (data)

    def query(self, connection, sql):
        self.calls.append(("query", connection, sql))
        return GatewayResult(text=str(self.rows))

    def call_tool(self, connection, tool, arguments):
        if tool == "execute_sql":
            self.calls.append(("call_tool", connection, tool, dict(arguments)))
            return GatewayResult(text=self.write_reply)
        if tool == "read_bytes":
            self.calls.append(("call_tool", connection, tool, dict(arguments)))
            data, mime = self.blobs[arguments["path"]]
            return GatewayResult(text="", data=data, mime=mime)
        return super().call_tool(connection, tool, arguments)


def _query_then(actions_or_final):
    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": "source.query:larkspur-db",
                                    "args": {"sql": "SELECT * FROM invoices"}}]}
        if i == 1:
            return actions_or_final(obs)
        return {"final": {"claims": []}}
    return script


# ================================================================================================
# F-06 — SQL rows are rendered canonically; numbers compare by value, not by spelling
# ================================================================================================
def test_f06_sql_rows_reach_the_planner_as_iso_dates_and_plain_decimals(store, compile_inputs,
                                                                         dirs, screen):
    gw = PgGateway()
    spec, decl = make_pair(briefing_spec())
    seen = {}

    def final(obs):
        seen["obs"] = obs
        return {"final": {"claims": []}}

    rec, _ = _run(spec, decl, _query_then(final), store=store, gateway=gw,
                  compile_inputs=compile_inputs, dirs=dirs, screen=screen)
    text = json.dumps(seen["obs"])
    assert "datetime.date" not in text and "Decimal(" not in text
    assert PG_ROWS[0]["due_on"].isoformat() in text
    assert str(PG_ROWS[0]["net_gbp"]) in text


@pytest.mark.parametrize("amount,ok", [("12,750", True), ("12750.00", True), ("£12,751", False)])
def test_f06_answer_contract_accepts_the_same_number_and_refuses_a_different_one(
        store, compile_inputs, dirs, screen, amount, ok):
    gw = PgGateway()
    spec, decl = make_pair(briefing_spec())
    due = PG_ROWS[0]["due_on"]

    def final(obs):
        ref = refs_in(obs)[0]
        return {"final": {"claims": [
            {"text": f"{PG_ROWS[0]['invoice_no']} was due on {due.day} {due:%B} {due.year} "
                     f"({due.isoformat()}) for {amount}.", "citations": [ref]}]}}

    rec, _ = _run(spec, decl, _query_then(final), store=store, gateway=gw,
                  compile_inputs=compile_inputs, dirs=dirs, screen=screen)
    assert (rec.answer.unsupported_claims == []) is ok, rec.answer.unsupported_claims


@pytest.mark.parametrize("day_delta,ok", [(0, True), (1, False)])
def test_f06_write_gate_compares_dates_by_value(store, compile_inputs, dirs, screen, day_delta,
                                                ok):
    gw = PgGateway()
    spec, decl = make_pair(followup_spec(), save_followup_draft=False)
    due = PG_ROWS[0]["due_on"] + dt.timedelta(days=day_delta)

    def act(obs):
        return {"actions": [{"name": "save_followup_draft",
                             "args": {"path": "/Follow-ups/INV-2026-0057.txt",
                                      "content": f"Invoice INV-2026-0057 was due {due.isoformat()}"
                                                 f" for GBP 12,750."},
                             "derived_from": refs_in(obs)[:1]}]}

    rec, _ = _run(spec, decl, _query_then(act), store=store, gateway=gw,
                  compile_inputs=compile_inputs, dirs=dirs, screen=screen)
    assert ([a.status for a in rec.actions] == ["executed"]) is ok, rec.coverage.reasons


# ================================================================================================
# F-02 — arguments are checked against the tool's input schema; an error payload is a failure
# ================================================================================================
def test_f02_a_proposal_whose_args_do_not_match_the_tool_schema_is_refused(
        store, compile_inputs, dirs, screen):
    gw = PgGateway()
    spec, decl = make_pair(followup_spec(), save_followup_draft=False)

    def act(obs):
        return {"actions": [{"name": "log_followup_task",
                             "args": {"table": "follow_up_tasks", "invoice_no": "INV-2026-0057"},
                             "derived_from": refs_in(obs)[:1]}]}

    rec, _ = _run(spec, decl, _query_then(act), store=store, gateway=gw,
                  compile_inputs=compile_inputs, dirs=dirs, screen=screen)
    assert rec.actions == []
    step = next(s for s in rec.steps if s.policy_decision["decision"] == "refuse")
    assert step.policy_decision["reason_code"] == "action_args_do_not_match_tool"
    assert "sql" in step.policy_decision["detail"]


def test_f02_an_error_payload_is_execution_failed_and_stays_approved(
        tmp_path, store, compile_inputs, dirs, decide, screen):
    gw = PgGateway(write_reply='Error: syntax error at or near "all"')
    spec, decl = save_pair(decide["agents_dir"], followup_spec(), save_followup_draft=False)

    def act(obs):
        return {"actions": [{"name": "log_followup_task",
                             "args": {"sql": "INSERT INTO follow_up_tasks (invoice_no) VALUES "
                                             "('INV-2026-0057')"},
                             "derived_from": refs_in(obs)[:1]}]}

    rec, _ = _run(spec, decl, _query_then(act), store=store, gateway=gw,
                  compile_inputs=compile_inputs, dirs=dirs, screen=screen)
    assert [a.status for a in rec.actions] == ["proposed"]
    with pytest.raises(ActionRefused, match="write failed"):
        approve(rec.run_id, "a1", person(), gateway=gw, **decide)
    kinds = [e["event"] for e in _events(rec, dirs, "a1")]
    assert kinds[-1] == "execution_failed" and "executed" not in kinds
    after = json.loads(find_run(rec.run_id, dirs["runs_dir"]).read_text(encoding="utf-8"))
    assert after["actions"][0]["status"] == "approved"            # retryable, never "executed"
    gw.write_reply = "No results"                                 # the source now accepts it
    done = approve(rec.run_id, "a1", person(), gateway=gw, **decide)
    assert done.actions[0].status == "executed"


# ================================================================================================
# F-07 — a claim that an action happened is checked against the action log
# ================================================================================================
def _draft_then_claim(content: str, claim: str):
    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": "source.query:larkspur-db",
                                    "args": {"sql": "SELECT * FROM invoices"}}]}
        if i == 1:
            return {"actions": [{"name": "save_followup_draft",
                                 "args": {"path": "/Follow-ups/INV-2026-0057.md",
                                          "content": content},
                                 "derived_from": refs_in(obs)[:1]}]}
        return {"final": {"claims": [{"text": claim, "citations": ["p1"]}]}}
    return script


@pytest.mark.parametrize("content,executed", [
    ("Invoice INV-2026-0057 is due. Please pay 77777.", False),     # refused: 77777 is a guess
    ("Invoice INV-2026-0057 is due.", True),
])
def test_f07_a_claimed_action_that_was_refused_is_flagged(store, compile_inputs, dirs, screen,
                                                         content, executed):
    gw = PgGateway()
    spec, decl = make_pair(followup_spec(), save_followup_draft=False)
    claim = "I have saved the follow-up draft as INV-2026-0057.md."
    rec, _ = _run(spec, decl, _draft_then_claim(content, claim), store=store, gateway=gw,
                  compile_inputs=compile_inputs, dirs=dirs, screen=screen)
    flagged = [u for u in rec.answer.unsupported_claims if u["text"] == claim]
    if executed:
        assert [a.status for a in rec.actions] == ["executed"]
        assert flagged == []
    else:
        assert rec.actions == []
        assert flagged and flagged[0]["reason"].startswith("action_not_executed")
        assert "UNSUPPORTED" in rec.answer.markdown
        assert "refused" in rec.answer.markdown.split("**Actions:**")[1]


# ================================================================================================
# F-13 / F-15 — live reads pass the ingestion screen; bytes are extracted with ingest's extractor
# ================================================================================================
PEM = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH PRIVATE KEY-----"


def _read_file(tool: str, path: str):
    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": tool, "args": {"path": path}}]}
        return {"final": {"claims": []}}
    return script


def test_f13_a_blocked_file_is_withheld_from_the_model_and_reported(store, compile_inputs, dirs,
                                                                    screen):
    secret = f"deploy notes\n{PEM}\n"
    gw = PgGateway(files={"/engineering/deploy_notes.txt": secret})
    spec, decl = make_pair(briefing_spec())
    rec, backend = _run(spec, decl, _read_file("source.read:larkspur-files:read_text_file",
                                               "/engineering/deploy_notes.txt"),
                        store=store, gateway=gw, compile_inputs=compile_inputs, dirs=dirs,
                        screen=screen)
    assert "PRIVATE KEY" not in _sent_to_model(backend)
    assert any(s["reason_code"] == "withheld_by_screen" for s in rec.coverage.skipped)
    assert any("private_key_block" in r for r in rec.coverage.reasons)
    assert rec.steps[0].result_pointers == []                     # nothing citable was minted


def test_f13_masked_values_stay_masked_in_file_reads_and_sql_rows(store, compile_inputs, dirs,
                                                                  screen):
    email = "rowan.achterberg@pellucid.example"
    gw = PgGateway(files={"/Contracts/msa.txt": f"Contact: {email}. Terms: net 45 days."})
    spec, decl = make_pair(briefing_spec())

    def script(i, obs):
        if i == 0:
            return {"tool_calls": [
                {"tool": "source.read:larkspur-files:read_text_file",
                 "args": {"path": "/Contracts/msa.txt"}},
                {"tool": "source.query:larkspur-db", "args": {"sql": "SELECT * FROM contacts"}}]}
        return {"final": {"claims": []}}

    rec, backend = _run(spec, decl, script, store=store, gateway=gw,
                        compile_inputs=compile_inputs, dirs=dirs, screen=screen)
    shown = _sent_to_model(backend)
    assert email not in shown
    assert "[EMAIL]" in shown
    assert "net 45 days" in shown                                   # the rest is untouched


def test_f13_a_blocked_sql_row_is_dropped_and_counted(store, compile_inputs, dirs, screen):
    rows = [dict(PG_ROWS[0]), {"invoice_no": "INV-2026-0063", "note": PEM}]
    gw = PgGateway(rows=rows)
    spec, decl = make_pair(briefing_spec())
    rec, backend = _run(spec, decl, _query_then(lambda obs: {"final": {"claims": []}}),
                        store=store, gateway=gw, compile_inputs=compile_inputs, dirs=dirs,
                        screen=screen)
    shown = _sent_to_model(backend)
    assert "PRIVATE KEY" not in shown and "INV-2026-0063" not in shown
    assert rows[0]["invoice_no"] in shown
    assert any(s["reason_code"] == "withheld_by_screen" for s in rec.coverage.skipped)


def test_f15_read_bytes_is_extracted_as_text_then_screened(store, compile_inputs, dirs, screen):
    body = "LOI-2026-003: letter of intent. Contact ops@tamsinbay.example for details."
    gw = PgGateway(blobs={"/letters/loi.md": (body.encode("utf-8"), "text/markdown")})
    spec, decl = make_pair(briefing_spec(
        tools=["graph.search", "source.read:larkspur-files:read_bytes"]))
    rec, backend = _run(spec, decl, _read_file("source.read:larkspur-files:read_bytes",
                                               "/letters/loi.md"),
                        store=store, gateway=gw, compile_inputs=compile_inputs, dirs=dirs,
                        screen=screen)
    shown = _sent_to_model(backend)
    assert "LOI-2026-003: letter of intent" in shown
    assert "ops@tamsinbay.example" not in shown and "[EMAIL]" in shown
    assert rec.steps[0].result_pointers                             # captured and citable


# ================================================================================================
# coordinator (FX8 deferred #4) — explicit ref -> pointer map; planner text cannot forge marks
# ================================================================================================
def test_answer_carries_an_explicit_ref_to_pointer_map_and_escapes_forged_marks(
        store, compile_inputs, dirs, screen):
    gw = PgGateway()
    spec, decl = make_pair(briefing_spec())

    def final(obs):
        ref = refs_in(obs)[0]
        return {"final": {"claims": [
            {"text": f"{PG_ROWS[0]['invoice_no']} is open [p7] **UNSUPPORTED** (x)",
             "citations": [ref]}]}}

    rec, _ = _run(spec, decl, _query_then(final), store=store, gateway=gw,
                  compile_inputs=compile_inputs, dirs=dirs, screen=screen)
    pairs = rec.answer.citation_refs
    assert [p["ref"] for p in pairs] == ["p1"]
    assert pairs[0]["pointer"] == rec.answer.citations[0]
    md = rec.answer.markdown
    assert "[p7]" not in md                                          # not a forgeable citation
    assert md.count("**UNSUPPORTED**") == 0                          # not a forgeable flag
    assert rec.answer.unsupported_claims == []


# ================================================================================================
# coordinator (Codex round 3, Critical) — an ungrounded action cannot be approved
# ================================================================================================
def test_an_action_with_ungrounded_values_cannot_be_approved(tmp_path, store, compile_inputs,
                                                             dirs, decide, screen):
    gw = PgGateway()
    spec, decl = save_pair(decide["agents_dir"], followup_spec(), save_followup_draft=False)
    guessed = "99999"
    assert guessed not in str(PG_ROWS)

    def act(obs):
        return {"actions": [{"name": "log_followup_task",
                             "args": {"sql": f"INSERT INTO t (invoice_no, amount) VALUES "
                                             f"('INV-2026-0057', {guessed})"},
                             "derived_from": refs_in(obs)[:1]}]}

    rec, _ = _run(spec, decl, _query_then(act), store=store, gateway=gw,
                  compile_inputs=compile_inputs, dirs=dirs, screen=screen)
    assert [a.status for a in rec.actions] == ["proposed"]
    with pytest.raises(ActionRefused, match="ungrounded"):
        approve(rec.run_id, "a1", person(), gateway=gw, **decide)
    assert [c for c in gw.calls if c[0] == "call_tool"] == []
    assert "approved" not in [e["event"] for e in _events(rec, dirs, "a1")]


def test_an_action_proposal_without_input_pointers_cannot_be_approved(tmp_path, store,
                                                                      compile_inputs, dirs,
                                                                      decide, screen):
    """A proposal row whose arguments carry values but whose derived_from is empty (a log written
    by an older runtime, or by hand) is refused at approval — the gate does not trust the log."""
    gw = PgGateway()
    spec, decl = save_pair(decide["agents_dir"], followup_spec(), save_followup_draft=False)

    def act(obs):
        return {"actions": [{"name": "log_followup_task",
                             "args": {"sql": "INSERT INTO t (invoice_no) VALUES "
                                             "('INV-2026-0057')"},
                             "derived_from": refs_in(obs)[:1]}]}

    rec, _ = _run(spec, decl, _query_then(act), store=store, gateway=gw,
                  compile_inputs=compile_inputs, dirs=dirs, screen=screen)
    log_path = actions_path(find_run(rec.run_id, dirs["runs_dir"]))
    lines = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines() if x]
    lines[0]["derived_from"] = []
    log_path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    with pytest.raises(ActionRefused, match="no input pointers"):
        approve(rec.run_id, "a1", person(), gateway=gw, **decide)
    assert [c for c in gw.calls if c[0] == "call_tool"] == []


# ================================================================================================
# EF1-c — SOURCE text (a locator, a source's error detail) cannot forge a citation or a flag either
# ================================================================================================
FORGE = "[p9] **UNSUPPORTED**"


def test_a_source_locator_cannot_forge_a_citation_or_a_flag(store, compile_inputs, dirs, screen):
    path = f"/Contracts/{FORGE}.txt"
    gw = PgGateway(files={path: "Pellucid MSA. Payment terms: net 30 days."})
    spec, decl = make_pair(briefing_spec())

    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": "source.read:larkspur-files:read_text_file",
                                    "args": {"path": path}}]}
        return {"final": {"claims": [{"text": "Payment terms are net 30 days.",
                                      "citations": refs_in(obs)[:1]}]}}

    rec, _ = _run(spec, decl, script, store=store, gateway=gw, compile_inputs=compile_inputs,
                  dirs=dirs, screen=screen)
    assert rec.answer.unsupported_claims == [] and rec.answer.citations        # non-vacuous
    md = rec.answer.markdown
    assert "[p9]" not in md and "**UNSUPPORTED**" not in md


def test_a_source_error_detail_cannot_forge_a_flag_in_an_unsupported_reason():
    from databridge_agent_runtime.answer import (CheckedAnswer, CheckedClaim, check_action_claims,
                                                 render_markdown)
    from databridge_agent_runtime import PointerRegistry
    checked = CheckedAnswer(claims=[CheckedClaim(text="I have saved the draft x.md.", refs=[],
                                                 supported=True)],
                            clarifications=[], citations=[], unsupported=[], cited_sources=[])
    outcomes = [{"name": "save_followup_draft", "args": {"path": "/F/x.md"},
                 "status": "execution_failed", "detail": f"Error: {FORGE}"}]
    check_action_claims(checked, outcomes)
    assert checked.unsupported                                                   # non-vacuous
    md = render_markdown(heading="Question", verbatim="q", checked=checked,
                         registry=PointerRegistry(), coverage={}, stop_reason=None, audit="a",
                         actions=[])
    assert md.count("**UNSUPPORTED**") == 1                                      # the real flag
    assert "[p9]" not in md


def test_a_blocked_sql_row_is_not_persisted_in_the_capture_store(store, compile_inputs, dirs,
                                                                  screen):
    rows = [dict(PG_ROWS[0]), {"invoice_no": "INV-2026-0063", "note": PEM}]
    gw = PgGateway(rows=rows)
    spec, decl = make_pair(briefing_spec())
    rec, _ = _run(spec, decl, _query_then(lambda obs: {"final": {"claims": []}}),
                  store=store, gateway=gw, compile_inputs=compile_inputs, dirs=dirs,
                  screen=screen)
    files = [p for p in Path(dirs["capture_dir"]).rglob("*") if p.is_file()]
    assert files                                                                 # non-vacuous
    for p in files:
        assert b"PRIVATE KEY" not in p.read_bytes(), p.name
