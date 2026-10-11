"""R-029 SETX W-08 (E2E run 5, both estates, 2026-10-02).

1. An agent's answer opened with ``facts.query:larkspur_db returned nothing usable: the facts query
   failed: FactsQueryError: refused or failed: no such table: public.invoices`` - an exception
   class and SQLite's text in front of a person. Every failure now reaches the answer as a fixed
   sentence plus an opaque ``error_ref``; the detail is in the server log under that reference.
2. ``facts.query`` could not find ``public.invoices``, the name the catalogue and the plan use: the
   facts store names it ``invoices``. A schema-qualified and an unqualified name now resolve to the
   same facts table (the store's own catalogue says which).

The facts stores are the run's OWN (`var/e2e5-*/facts/*_facts.{sqlite,json}`), COPIED into a temp
directory (a query writes its artefact beside the store; the evidence is never written). Skipped
when the evidence is absent; the resolver's rule is also tested on a store built here.
Expectations are derived from the store's own catalogue, never written down.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest

from agent_runtime_helpers import briefing_spec, make_pair, scripted_harness

ROOT = Path(__file__).resolve().parents[3]                          # databridge/
RUNS = {"workspace": (ROOT / "var" / "e2e5-workspace-20261002T1515", "larkspur_db"),
        "finance": (ROOT / "var" / "e2e5-finance-20261002T1551", "finance_core_banking")}
#: an exception's class name followed by its text, as `str(exc)` renders it
EXC_TEXT = re.compile(r"\b[A-Z]\w*(?:Error|Exception)\b\s*[:(]")


def _copy_store(run: Path, sid: str, dest: Path) -> Path:
    src = run / "facts"
    if not (src / f"{sid}_facts.sqlite").is_file():
        pytest.skip(f"the run-5 evidence {run.name} is not on this machine")
    dest.mkdir(parents=True, exist_ok=True)
    for name in (f"{sid}_facts.sqlite", f"{sid}_facts.json"):
        shutil.copyfile(src / name, dest / name)
    return dest


def _catalogue(facts_dir: Path, sid: str) -> list[tuple[str, str]]:
    """(source table, facts table) as the STORE's own catalogue table records them."""
    con = sqlite3.connect(f"file:{(facts_dir / f'{sid}_facts.sqlite').as_posix()}?mode=ro",
                          uri=True)
    try:
        held = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return [(s, f) for s, f in con.execute(
            "SELECT source_table, facts_table FROM _facts_tables ORDER BY facts_table")
            if f in held]
    finally:
        con.close()


@pytest.mark.parametrize("estate", sorted(RUNS))
def test_w08_a_schema_qualified_table_resolves_to_its_facts_table(estate, tmp_path, monkeypatch):
    from databridge_agent_runtime.tools import McpFactsStore

    run, sid = RUNS[estate]
    facts_dir = _copy_store(run, sid, tmp_path / "facts")
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(facts_dir))
    pairs = [(s, f) for s, f in _catalogue(facts_dir, sid) if "." in s]
    assert pairs                                                        # non-vacuous
    store = McpFactsStore()
    for source_table, facts_table in pairs:
        schema, table = source_table.rsplit(".", 1)
        want = store.query(sid, f'SELECT COUNT(*) AS n FROM "{facts_table}"').rows
        for spelled in (source_table, f'"{schema}"."{table}"', f"{schema.upper()}.{table}"):
            got = store.query(sid, f"SELECT COUNT(*) AS n FROM {spelled}")
            assert got.rows == want, (spelled, got.rows, want)
        if facts_table.lower() == table.lower():
            assert store.query(sid, f"SELECT COUNT(*) AS n FROM {table}").rows == want


def test_w08_the_resolver_never_touches_a_string_a_column_or_an_unknown_name():
    from databridge_agent_runtime.tools import resolve_table_names

    names = {"public.invoices": "invoices", "s.foo": "s__foo", "x.foo": "x__foo"}
    sql = ("SELECT i.status, 'public.invoices' AS label FROM public.invoices i "
           "JOIN s.foo f ON f.id = i.id -- public.invoices\nWHERE other.t = 1")
    out = resolve_table_names(sql, names)
    assert out.count('"invoices"') == 1 and '"s__foo"' in out
    assert "'public.invoices'" in out and "-- public.invoices" in out     # literal and comment
    assert "i.status" in out and "other.t" in out and "f.id" in out
    # an ambiguous bare name stays as written (its error lists the tables)
    assert resolve_table_names("SELECT * FROM foo", names) == "SELECT * FROM foo"
    assert resolve_table_names("SELECT 1", {}) == "SELECT 1"


@pytest.mark.parametrize("estate", sorted(RUNS))
def test_w08_a_failed_facts_query_reaches_the_answer_without_exception_text(
        estate, tmp_path, monkeypatch, store, gateway, compile_inputs, caplog):
    """The run-5 path: the planner queries a table the store does not have; the answer, the
    coverage and the observation the planner sees carry a fixed sentence and a reference."""
    import logging

    from databridge_agent_runtime import run_agent
    from databridge_agent_runtime.tools import McpFactsStore

    run, sid = RUNS[estate]
    facts_dir = _copy_store(run, sid, tmp_path / "facts")
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(facts_dir))
    tables = [f for _, f in _catalogue(facts_dir, sid)]
    missing = "no_such_table_" + "x" * 3
    assert missing not in tables
    spec, decl = make_pair(briefing_spec(sources=[sid], tools=[f"facts.query:{sid}"]))
    seen: list[dict] = []

    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": f"facts.query:{sid}",
                                    "args": {"sql": f"SELECT COUNT(*) AS n FROM {missing}"}}]}
        seen.extend(obs)
        return {"final": {"claims": []}}

    harness, _backend = scripted_harness(script)
    with caplog.at_level(logging.ERROR):
        rec = run_agent(spec, decl, store=store, harness=harness, gateway=gateway,
                        trigger={"type": "manual", "by": "dana", "question": None},
                        compile_inputs=compile_inputs, notifier=lambda _e: None,
                        runs_dir=tmp_path / "runs", capture_dir=tmp_path / "capture",
                        facts=McpFactsStore())
    public = json.dumps({"md": rec.answer.markdown, "coverage": rec.coverage.model_dump()})
    assert "no such table" not in public.lower() and not EXC_TEXT.search(public), public
    skipped = [s for s in rec.coverage.skipped if s["tool"] == f"facts.query:{sid}"]
    assert skipped and all(re.search(r"ERR-[0-9a-f]{10}", s["detail"]) for s in skipped)
    ref = re.search(r"ERR-[0-9a-f]{10}", skipped[0]["detail"]).group(0)
    assert ref in caplog.text and "no such table" in caplog.text          # the detail is logged
    # the planner is told which tables exist (the store's own catalogue), never the exception
    told = json.dumps(seen)
    assert not EXC_TEXT.search(told) and all(t in told for t in tables), told


def test_w08_any_tool_failure_is_a_fixed_sentence_and_a_reference(store, gateway, compile_inputs,
                                                                  tmp_path):
    """Not just facts: a tool that raises reaches the planner and the answer as a sentence and a
    reference (the generic tool path), never as the exception's text."""
    from databridge_agent_runtime import run_agent

    secret = "Secret-Path-C:\\srv\\x.db"

    class Boom:
        def available(self, connection):
            return True

        def query(self, connection, sql, *, params=None, question=None):
            raise RuntimeError(f"cannot open {secret}")

    spec, decl = make_pair(briefing_spec(tools=["facts.query:larkspur-db"]))

    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": "facts.query:larkspur-db",
                                    "args": {"sql": "SELECT name FROM t"}}]}
        return {"final": {"claims": []}}

    harness, backend = scripted_harness(script)
    rec = run_agent(spec, decl, store=store, harness=harness, gateway=gateway,
                    trigger={"type": "manual", "by": "dana", "question": None},
                    compile_inputs=compile_inputs, notifier=lambda _e: None,
                    runs_dir=tmp_path / "runs", capture_dir=tmp_path / "capture", facts=Boom())
    public = json.dumps({"md": rec.answer.markdown, "coverage": rec.coverage.model_dump()})
    sent = json.dumps(getattr(backend, "calls", []), default=str)
    for text in (public, sent):
        assert secret not in text and "RuntimeError" not in text
    assert re.search(r"ERR-[0-9a-f]{10}", public)


# --- R-029 Codex f1 #4: exception text at the catch site, early refusal path included --------
SECRET = "C:\srv\creds\token=hunter2"


def test_f1_4_an_unreachable_connection_never_shows_its_exception(store, compile_inputs,
                                                                  tmp_path):
    from agent_runtime_helpers import FakeGateway

    from databridge_agent_runtime import run_agent

    class Down(FakeGateway):
        def advertise(self, connection):
            raise RuntimeError(f"spawn failed at {SECRET}")

    spec, decl = make_pair(briefing_spec())
    harness, _ = scripted_harness(lambda i, obs: {"final": {"claims": []}})
    rec = run_agent(spec, decl, store=store, harness=harness, gateway=Down(),
                    trigger={"type": "manual", "by": "dana", "question": None},
                    compile_inputs=compile_inputs, notifier=lambda _e: None,
                    runs_dir=tmp_path / "runs", capture_dir=tmp_path / "capture")
    public = json.dumps({"md": rec.answer.markdown, "coverage": rec.coverage.model_dump()})
    assert SECRET not in public and "hunter2" not in public and not EXC_TEXT.search(public)


def test_f1_4_the_mcp_gateway_advert_carries_a_reference_not_the_text(monkeypatch):
    from databridge_agent_runtime.tools import McpSourceGateway

    gw = McpSourceGateway()

    def boom(connection):
        raise RuntimeError(f"cannot start {SECRET}")

    monkeypatch.setattr(gw, "_client", boom)
    adv = gw.advertise("any-source")
    assert adv.reachable is False and "hunter2" not in str(adv.problem)
    assert re.search(r"ERR-[0-9a-f]{10}", str(adv.problem))


def test_f1_7_only_table_positions_are_rewritten():
    """R-029 Codex f1 #7: with `sales.total` a source table, `sales.total` used as an
    alias-qualified COLUMN is not a table and is left exactly as written."""
    from databridge_agent_runtime.tools import resolve_table_names

    names = {"sales.total": "total", "public.invoices": "invoices", "s.a": "s__a",
             "x.b": "x__b"}
    sql = "SELECT sales.total, COUNT(*) FROM ledger AS sales GROUP BY sales.total"
    assert resolve_table_names(sql, names) == sql
    got = resolve_table_names("SELECT i.no FROM public.invoices i, s.a AS a2 "
                              "JOIN x.b ON x.b.id = i.id WHERE public.invoices.no > 0", names)
    assert 'FROM "invoices" i, "s__a" AS a2 JOIN "x__b" ON x.b.id' in got, got
    assert "WHERE public.invoices.no > 0" in got                    # a column ref, untouched
    assert resolve_table_names("SELECT * FROM (SELECT * FROM public.invoices) t", names) == \
        'SELECT * FROM (SELECT * FROM "invoices") t'


@pytest.mark.parametrize("estate", sorted(RUNS))
def test_tidy2_every_facts_pointer_an_agent_cites_resolves_from_disk(
        estate, tmp_path, monkeypatch, store, gateway, compile_inputs):
    """R-029 TIDY2 follow-up: a facts READ writes nothing, so the agent's `facts.query` - a
    PUBLISHER of `query_cell` pointers - persists the query artefact of every answer it returns;
    each pointer it returned then re-runs from its query hash alone."""
    from databridge_agent_runtime import run_agent
    from databridge_agent_runtime.tools import McpFactsStore
    from databridge_mcp_ingest.tabular import count as count_mod

    run, sid = RUNS[estate]
    facts_dir = _copy_store(run, sid, tmp_path / "facts")
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(facts_dir))
    count_mod.clear_digest_cache()
    table = _catalogue(facts_dir, sid)[0][1]
    spec, decl = make_pair(briefing_spec(sources=[sid], tools=[f"facts.query:{sid}"]))

    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": f"facts.query:{sid}",
                                    "args": {"sql": f'SELECT COUNT(*) AS n FROM "{table}"'}}]}
        return {"final": {"claims": []}}

    harness, _ = scripted_harness(script)
    rec = run_agent(spec, decl, store=store, harness=harness, gateway=gateway,
                    trigger={"type": "manual", "by": "dana", "question": None},
                    compile_inputs=compile_inputs, notifier=lambda _e: None,
                    runs_dir=tmp_path / "runs", capture_dir=tmp_path / "capture",
                    facts=McpFactsStore())
    from databridge_core import SourcePointer

    ptrs = [SourcePointer.model_validate(p) for s in rec.steps for p in s.result_pointers]
    assert ptrs                                                          # non-vacuous
    for p in ptrs:
        count_mod.resolve_query_cell(p, facts_dir=facts_dir)             # raises if not saved
