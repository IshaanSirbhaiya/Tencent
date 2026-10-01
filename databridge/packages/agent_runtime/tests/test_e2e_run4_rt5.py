"""E2E run 4 (wiki/40-reviews/E2E-2026-10-01-run4.md) - Q-02, Q-05, Q-08, fixed by RT5.

Each test drives the real code path (`run_agent`, the real harness behind a scripted planner, the
real tool layer and answer contract) with the SHAPE of the failing input from the run-4 evidence
(databridge/var/e2e-20261001T1255/):

* Q-02 - `agents/account_briefing.workflow.yaml` grants `source.read:larkspur_db:execute_sql`
  (vouched as a read); `artefacts/p3_briefing.json` shows every call refused `sql_not_read_only`.
* Q-05 - `artefacts/s5_golden_larkspur_qa.json` ac-008: a grouped count over `public.invoices`,
  one claim holding the cited count and an uncited total, cited to the SQL result span only.
* Q-08 - `runs/agent/overdue_invoice_follow_up/run-20261001T130408-a346c24d.json`: the window end
  (run date + the instruction's 30 days) declared as `add_days` and refused.

The live source is a real SQL engine (sqlite, in memory) holding synthetic rows shaped like the
run's invoices, so every expectation - which rows a group covers, which statements are read-only -
is computed from that engine and the rule under test, never written down. No network, no model.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3

import pytest

from agent_runtime_helpers import FakeGateway, briefing_spec, make_pair, refs_in
from test_e2e_run2_rtf2 import _harness, dirs  # noqa: F401 - fixture

from databridge_agent_runtime import ConnectionAdvert, GatewayResult, ToolAdvert, run_agent
from databridge_agent_runtime.answer import Claim, FinalAnswer, _numbers, check_answer
from databridge_agent_runtime.query_rows import expansion_for, group_members
from databridge_agent_runtime.runtime import RUN_CONTEXT_SOURCE
from databridge_agent_runtime.tools import PointerRegistry, sql_is_read_only
from databridge_core import SourcePointer
from databridge_core.provenance import Trust

pytest.importorskip("databridge_mcp_ingest.screen")

DB = "larkspur-db"
SQL_TOOL = "execute_sql"
#: Synthetic invoices shaped like run 4's (invoice_no, customer_id, status, total_gbp).
INVOICES = [("INV-2026-0041", "C-1001", "paid", 11100.0),
            ("INV-2026-0057", "C-1002", "open", 15300.0),
            ("INV-2026-0063", "C-1001", "open", 11100.0)]
#: ac-008's statement, as run 4's planner wrote it (an exact match on the id: sqlite has no ILIKE)
GROUPED = ("SELECT customer_id, status, COUNT(*) AS n FROM public.invoices WHERE customer_id = "
           "'C-1001' GROUP BY customer_id, status ORDER BY customer_id, status")


class SqlGateway(FakeGateway):
    """`larkspur-db` is a real SQL engine. Its SQL tool is `execute_sql`, advertised as a READ (an
    approved crawl plan vouched for it, as postgres-mcp's was in run 4) and named as the
    connection's `sql_tool`. `call_tool` on it is the unchecked path: it must never be used."""

    def __init__(self):
        super().__init__()
        self.db = sqlite3.connect(":memory:")
        self.db.execute("ATTACH DATABASE ':memory:' AS public")
        self.db.execute("CREATE TABLE public.invoices (invoice_no TEXT, customer_id TEXT, "
                        "status TEXT, total_gbp REAL)")
        self.db.executemany("INSERT INTO public.invoices VALUES (?, ?, ?, ?)", INVOICES)

    def advertise(self, connection):
        if connection == DB:
            return ConnectionAdvert(connection=connection, declared_in="var/sources/db.yaml",
                                    tools=(ToolAdvert(SQL_TOOL, writes=False),
                                           ToolAdvert("list_schemas", writes=False)),
                                    sql=True, sql_tool=SQL_TOOL)
        return super().advertise(connection)

    def rows(self, sql, params=()):
        cur = self.db.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def query(self, connection, sql):
        self.calls.append(("query", connection, sql))
        try:
            return GatewayResult(text=json.dumps(self.rows(sql)))
        except sqlite3.Error as e:
            return GatewayResult(is_error=True, text=f"error: {e}")

    def call_tool(self, connection, tool, arguments):
        self.calls.append(("call_tool", connection, tool, dict(arguments)))
        return GatewayResult(text="[]")


def _run(spec_over, script, *, store, compile_inputs, dirs, gateway=None, question=None):
    gw = gateway or SqlGateway()
    spec, decl = make_pair(briefing_spec(**spec_over))
    harness, _ = _harness(script)
    rec = run_agent(spec, decl, store=store, harness=harness, gateway=gw,
                    trigger={"type": "manual", "by": "dana", "question": question},
                    compile_inputs=compile_inputs, notifier=lambda _e: None, **dirs)
    return rec, gw


def _today(messages) -> str:
    ctx = next(m["content"] for m in messages if m["role"] == "user"
               and m["content"].startswith("Run context:"))
    return ctx.split("today is ", 1)[1][:10]


def _cited_text(pointer: dict, capture_dir) -> str:
    """What a citation re-reads: the capture's text at the pointer's span, after checking the
    capture's bytes hash to its digest (the golden harness's `cited_text` + `check_pointer`)."""
    sha = pointer["capture_digest"]
    blob = capture_dir / sha[:2] / sha
    assert hashlib.sha256(blob.read_bytes()).hexdigest() == sha
    raw = (capture_dir / sha[:2] / f"{sha}.txt").read_text(encoding="utf-8")
    s, e = pointer["char_start"], pointer["char_end"]
    assert 0 <= s <= e <= len(raw)
    return raw[s:e]


# ================================================================================================
# Q-02 - the compiled SQL tool runs through the checked query path; read-only never writes
# ================================================================================================
#: The run-4 briefing's grants for the database (agents/account_briefing.workflow.yaml).
RUN4_TOOLS = ["graph.search", f"source.query:{DB}", f"source.read:{DB}:list_schemas",
              f"source.read:{DB}:{SQL_TOOL}"]
STATEMENTS = [
    "SELECT customer_id, status, total_gbp FROM public.invoices WHERE customer_id = 'C-1001'",
    "INSERT INTO public.invoices VALUES ('INV-X', 'C-9', 'open', 1)",
    "SELECT 1; DELETE FROM public.invoices",
    "DELETE FROM public.invoices WHERE 1 = 1",
]


def test_q02_the_run4_grant_is_routed_and_only_read_only_statements_run(store, compile_inputs,
                                                                       dirs):
    calls = [{"tool": f"source.read:{DB}:{SQL_TOOL}", "args": {"sql": s}} for s in STATEMENTS]

    def script(i, obs, messages):
        return {"tool_calls": calls} if i == 0 else {"final": {"claims": []}}

    rec, gw = _run({"tools": RUN4_TOOLS}, script, store=store, compile_inputs=compile_inputs,
                   dirs=dirs)
    want = ["granted" if sql_is_read_only(s)[0] else "sql_not_read_only" for s in STATEMENTS]
    assert "granted" in want and "sql_not_read_only" in want                 # non-vacuous
    assert [s.policy_decision["reason_code"] for s in rec.steps] == want
    # nothing went through the unchecked tool path, and only the read-only statements ran
    assert [c for c in gw.calls if c[0] == "call_tool"] == []
    assert [c[2] for c in gw.calls if c[0] == "query"] == \
        [s for s in STATEMENTS if sql_is_read_only(s)[0]]
    assert len(gw.rows("SELECT * FROM public.invoices")) == len(INVOICES)   # nothing written
    ok = next(s for s in rec.steps if s.policy_decision["reason_code"] == "granted")
    assert ok.result_pointers and ok.result_pointers[0]["capture_digest"]


def test_q02_a_complete_answer_is_not_marked_reduced(store, compile_inputs, dirs):
    """The run-4 symptom: a read-only statement through the vouched grant used to be refused and
    put 'Reduced coverage' on a complete answer."""
    def script(i, obs, messages):
        if i == 0:
            return {"tool_calls": [{"tool": f"source.read:{DB}:{SQL_TOOL}",
                                    "args": {"sql": STATEMENTS[0]}}]}
        if i == 1:
            ref = refs_in(obs)[0]
            return {"final": {"claims": [{"text": "C-1001 has invoices on record.",
                                          "citations": [ref]}]}}
        return {"final": {"claims": []}}

    rec, _ = _run({"tools": RUN4_TOOLS}, script, store=store, compile_inputs=compile_inputs,
                  dirs=dirs)
    assert rec.status == "completed" and rec.outcome.coverage_status == "complete"
    assert "Reduced coverage" not in rec.answer.markdown and not rec.coverage.skipped


@pytest.mark.parametrize("tools,granted", [
    ([f"source.query:{DB}"], True),                       # compiled after Q-02: the query grant
    ([f"source.read:{DB}:{SQL_TOOL}"], True),             # compiled before: the vouched grant
    ([f"source.read:{DB}:list_schemas"], False),          # neither: refused, nothing is sent
])
def test_q02_routing_is_admitted_by_a_grant_never_around_one(tools, granted, store,
                                                            compile_inputs, dirs):
    def script(i, obs, messages):
        if i == 0:
            return {"tool_calls": [{"tool": f"source.read:{DB}:{SQL_TOOL}",
                                    "args": {"sql": STATEMENTS[0]}}]}
        return {"final": {"claims": []}}

    rec, gw = _run({"tools": ["graph.search", *tools]}, script, store=store,
                   compile_inputs=compile_inputs, dirs=dirs)
    assert (rec.steps[0].policy_decision["reason_code"] == "granted") is granted
    assert bool([c for c in gw.calls if c[0] == "query"]) is granted
    assert [c for c in gw.calls if c[0] == "call_tool"] == []


def test_q02_no_statement_or_two_statements_are_not_guessed(store, compile_inputs, dirs):
    bad = [{}, {"sql": STATEMENTS[0], "query": "SELECT status FROM public.invoices"}]

    def script(i, obs, messages):
        if i == 0:
            return {"tool_calls": [{"tool": f"source.read:{DB}:{SQL_TOOL}", "args": a}
                                   for a in bad]}
        return {"final": {"claims": []}}

    rec, gw = _run({"tools": RUN4_TOOLS}, script, store=store, compile_inputs=compile_inputs,
                   dirs=dirs)
    assert len(rec.steps) == len(bad) and all(not s.result_pointers for s in rec.steps)
    assert [c for c in gw.calls if c[0] in ("query", "call_tool")] == []


# ================================================================================================
# Q-05 - each sentence judged on its own; an aggregate is cited to the rows it counted
# ================================================================================================
def _ac008_answer(gw: SqlGateway) -> tuple[str, str, int, int]:
    """ac-008's claim shape: the cited open count, then an uncited total. Both numbers come from
    the engine, never from this file."""
    open_n = gw.rows("SELECT COUNT(*) AS n FROM public.invoices WHERE customer_id = 'C-1001' AND "
                     "status = 'open'")[0]["n"]
    total = gw.rows("SELECT COUNT(*) AS n FROM public.invoices WHERE customer_id = "
                    "'C-1001'")[0]["n"]
    counted = (f"Customer C-1001 has {open_n} open invoice.")
    added = f"The invoices table holds {total} invoices for this customer."
    return counted, added, open_n, total


def test_q05_the_cited_count_stays_supported_and_only_the_uncited_total_is_flagged():
    gw = SqlGateway()
    reg = PointerRegistry()
    result = json.dumps(gw.rows(GROUPED))
    ref = reg.add(SourcePointer(source_id=DB, kind="doc_span", document_id="d", char_start=0,
                                char_end=len(result), trust=Trust.UNTRUSTED), result)
    counted, added, open_n, total = _ac008_answer(gw)
    assert str(total) not in _numbers(result)          # non-vacuous: the total is NOT in evidence
    final = FinalAnswer(claims=[Claim(text=f"{counted} {added}", citations=[ref])])
    checked = check_answer(final, reg, by_sentence=True)
    assert [(c.text, c.supported) for c in checked.claims] == [(counted, True), (added, False)]
    assert [u["text"] for u in checked.unsupported] == [added]
    assert str(total) in checked.unsupported[0]["reason"]
    # a claim whose every sentence is flagged stays ONE flagged claim; an all-supported one too
    both_bad = FinalAnswer(claims=[Claim(text=f"{added} {added}", citations=[ref])])
    assert len(check_answer(both_bad, reg, by_sentence=True).claims) == 1
    both_ok = FinalAnswer(claims=[Claim(text=f"{counted} {counted}", citations=[ref])])
    assert [c.supported for c in check_answer(both_ok, reg, by_sentence=True).claims] == [True]
    # off by default: a caller pairing its own data with claims BY POSITION sees one per claim
    assert len(check_answer(final, reg).claims) == len(final.claims)


def test_q05_sentences_split_on_their_ends_never_inside_a_number_or_a_name():
    from databridge_agent_runtime.answer import _sentences
    text = "Total GBP 15300.00 per public.invoices. It is open! Due 2026-10-15? Yes."
    assert _sentences(text) == ["Total GBP 15300.00 per public.invoices.", "It is open!",
                                "Due 2026-10-15?", "Yes."]


def test_q05_an_aggregate_is_cited_to_the_rows_it_counted(store, compile_inputs, dirs):
    gw = SqlGateway()
    counted, added, _o, _t = _ac008_answer(gw)
    seen: dict = {}

    def script(i, obs, messages):
        if i == 0:
            return {"tool_calls": [{"tool": f"source.query:{DB}", "args": {"sql": GROUPED}}]}
        if i == 1:
            seen["obs"] = obs
            agg = refs_in(obs)[0]
            return {"final": {"claims": [{"text": f"{counted} {added}", "citations": [agg]}]}}
        return {"final": {"claims": []}}

    rec, _ = _run({"tools": ["graph.search", f"source.query:{DB}"]}, script, store=store,
                  compile_inputs=compile_inputs, dirs=dirs, gateway=gw)
    # ac-008 end to end: the cited count is supported, only the uncited total is flagged
    assert [(c["text"], c["supported"]) for c in rec.answer.claims] == \
        [(counted, True), (added, False)], rec.answer.markdown
    cited = " ".join(_cited_text(p, dirs["capture_dir"]) for p in rec.answer.citations)
    # every row the grouped result covers (computed by the engine) is in what the answer cites
    behind = [r["invoice_no"] for r in gw.rows(
        "SELECT invoice_no FROM public.invoices WHERE customer_id = 'C-1001'")]
    assert behind and all(i in cited for i in behind)
    others = [r[0] for r in INVOICES if r[0] not in behind]
    assert others and not any(i in cited for i in others)                   # and nothing else
    # ... and a reader sees every cited row in the answer's Sources
    sources = rec.answer.markdown.split("**Sources:**", 1)[1]
    assert len(rec.answer.citation_refs) == len(behind) + 1
    assert all(f"[{c['ref']}]" in sources for c in rec.answer.citation_refs)
    # the planner was shown which rows each group covers, keyed by the group's own values
    groups = json.dumps(seen["obs"])
    for g in gw.rows(GROUPED):
        assert g["status"] in groups
    # the contributing-rows read was itself checked read-only before it was sent
    reads = [c[2] for c in gw.calls if c[0] == "query"]
    assert len(reads) == 2 and all(sql_is_read_only(s)[0] for s in reads)


@pytest.mark.parametrize("sql", [
    "SELECT i.status, COUNT(*) FROM public.invoices i JOIN public.customers c ON c.id = "
    "i.customer_id GROUP BY i.status",                                   # a join
    "SELECT status, COUNT(*) FROM (SELECT * FROM public.invoices) t GROUP BY status",  # subquery
    "SELECT status, COUNT(*) FILTER (WHERE total_gbp > 1) FROM public.invoices GROUP BY status",
    "SELECT status, COUNT(*) OVER (PARTITION BY status) FROM public.invoices",   # window
    "SELECT status, COUNT(*) FROM public.invoices GROUP BY 1",           # an ordinal
    "SELECT lower(status), COUNT(*) FROM public.invoices GROUP BY lower(status)",  # an expression
    "SELECT status FROM public.invoices UNION SELECT status FROM public.invoices",
    "SELECT invoice_no, status FROM public.invoices",                    # not an aggregate
])
def test_q05_what_cannot_be_mapped_exactly_is_not_mapped(sql):
    assert expansion_for(sql) is None


def test_q05_the_expansion_keeps_the_table_and_condition_and_an_aliased_group_is_not_paired():
    gw = SqlGateway()
    exp = expansion_for(GROUPED)
    assert exp is not None and exp.group_by == ("customer_id", "status")
    assert "public.invoices" in exp.sql and "customer_id = 'C-1001'" in exp.sql
    assert sql_is_read_only(exp.sql)[0]
    # the expansion covers exactly the rows the WHERE selects (the engine decides)
    assert len(gw.rows(exp.sql)) == len(gw.rows(
        "SELECT * FROM public.invoices WHERE customer_id = 'C-1001'"))
    aliased = "SELECT status AS s, COUNT(*) AS n FROM public.invoices GROUP BY status"
    e2 = expansion_for(aliased)
    assert e2 is not None and group_members(gw.rows(aliased), gw.rows(e2.sql), e2.group_by) is None


# ================================================================================================
# Q-08 - a window bound from the run clock and the instruction's duration is a replayable derivation
# ================================================================================================
INSTRUCTION = ("Every weekday at 9am, find open invoices that are overdue or due within the next "
               "30 days.")
DAYS = next(n for n in sorted(_numbers(INSTRUCTION), key=len) if n not in ("9",))


def _window_run(make_derivation, *, store, compile_inputs, dirs):
    state: dict = {}

    def script(i, obs, messages):
        if i == 0:
            return {"tool_calls": [{"tool": f"source.query:{DB}", "args": {"sql": STATEMENTS[0]}}]}
        if i == 1:
            today = _today(messages)
            value, derivation = make_derivation(today)
            state["value"] = value
            return {"final": {"claims": [{"text": f"The follow-up window ends on {value}.",
                                          "citations": refs_in(obs)[:1],
                                          "derivations": [derivation]}]}}
        return {"final": {"claims": []}}

    rec, _ = _run({"tools": ["graph.search", f"source.query:{DB}"], "created_from": INSTRUCTION},
                  script, store=store, compile_inputs=compile_inputs, dirs=dirs)
    return rec, state["value"]


def _plus(today: str, days: int) -> str:
    return (dt.date.fromisoformat(today) + dt.timedelta(days=days)).isoformat()


def test_q08_today_plus_the_instructions_days_is_grounded_on_the_captured_clock(
        store, compile_inputs, dirs):
    rec, value = _window_run(
        lambda t: (_plus(t, int(DAYS)), {"value": _plus(t, int(DAYS)), "rule": "add_days",
                                         "inputs": [t], "args": {"days": DAYS}}),
        store=store, compile_inputs=compile_inputs, dirs=dirs)
    claim = rec.answer.claims[0]
    assert claim["supported"], claim
    assert value not in json.dumps([r[3] for r in INVOICES])        # non-vacuous: not in the data
    ptr = SourcePointer.model_validate(claim["derivations"][0]["pointer"])
    clock = [p for p in ptr.derived_from if p.source_id == RUN_CONTEXT_SOURCE]
    assert len(clock) == 1
    today = claim["derivations"][0]["inputs"][0]
    assert today in _cited_text(clock[0].model_dump(mode="json"), dirs["capture_dir"])


@pytest.mark.parametrize("make", [
    # a duration the instruction does not state
    lambda t: (_plus(t, int(DAYS) + 1), {"value": _plus(t, int(DAYS) + 1), "rule": "add_days",
                                         "inputs": [t], "args": {"days": str(int(DAYS) + 1)}}),
    # the declared value does not replay
    lambda t: (_plus(t, int(DAYS) - 1), {"value": _plus(t, int(DAYS) - 1), "rule": "add_days",
                                         "inputs": [t], "args": {"days": DAYS}}),
    # a date that is not the run's own (and not in the evidence)
    lambda t: (_plus(t, int(DAYS) - 1), {"value": _plus(t, int(DAYS) - 1), "rule": "add_days",
                                         "inputs": [_plus(t, -1)], "args": {"days": DAYS}}),
    # only a DATE rule rests on the clock: joining the run date into a string grounds nothing
    lambda t: (f"{t}_{t}", {"value": f"{t}_{t}", "rule": "concat", "inputs": [t, t],
                            "args": {"sep": "_"}}),
    # a NUMBER rule never rests on the clock: a sum of the date's parts grounds nothing
    lambda t: (str(int(t[5:7]) + int(t[8:10])), {"value": str(int(t[5:7]) + int(t[8:10])),
                                                  "rule": "sum", "inputs": [t[5:7], t[8:10]]}),
])
def test_q08_what_the_clock_does_not_replay_still_refuses(make, store, compile_inputs, dirs):
    rec, _ = _window_run(make, store=store, compile_inputs=compile_inputs, dirs=dirs)
    assert not rec.answer.claims[0]["supported"], rec.answer.claims[0]
