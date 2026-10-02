"""Finance dry run 2026-10-02 (wiki/40-reviews/FIN-live-dry-run-2026-10-02.md), fixed by ANS6.

* FIN-08 (the agent's answer contract): a correct value was dropped over its FORMATTING. An
  amount written with its currency code flush against it (``HKD33,600,000.00``) was read as
  ``600000`` and flagged; an identifier written with a typographic dash (``CM‑2026‑0029``) did not
  match the source's ``CM-2026-0029``. Values are compared by VALUE, never by spelling — and a
  different value is still refused.
* FIN-08's twin, found while reproducing: the run context's date and time grounded ANY number. On
  2026-10-02 the ``2`` of today's date made "the table holds 2 invoices" supported (the RT5 Q-05
  test failed that day, with no code changed). The run date now grounds only a date or a time.
* FIN-11 (the runtime): the agent listed the credit-memo folder as ``/``, the source refused it
  ("path outside the declared roots"), and the run spent its budget finding the folder another
  way. A path written from the top of the tree is now sent once more relative to the source's own
  root — the same read, the same grant.

Two estates, read from this repo's fixtures: the Larkspur workspace (`fixtures/workspace_demo`) and
the finance estate (`fixtures/packs/finance`). The file source is the real files_blob server's own
path rule (`Roots`), over the real fixture trees. Every expected value is read from the fixtures.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import re
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from agent_runtime_helpers import FakeGateway, briefing_spec, make_pair
from test_e2e_run2_rtf2 import _harness, dirs  # noqa: F401 - fixture

from databridge_agent_runtime import ConnectionAdvert, GatewayResult, ToolAdvert, run_agent
from databridge_agent_runtime.answer import Claim, FinalAnswer, check_answer, ungrounded_values
from databridge_agent_runtime.runtime import run_context
from databridge_agent_runtime.tools import PointerRegistry
from databridge_core import SourcePointer
from databridge_core.provenance import Trust

REPO = Path(__file__).resolve().parents[3]
FIN = REPO / "fixtures" / "packs" / "finance"
LARKSPUR = REPO / "fixtures" / "workspace_demo"


# ------------------------------------------------------------------------------------------------
# the two estates' rows, read from their seed SQL
# ------------------------------------------------------------------------------------------------
def _load(paths) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    for f in paths:
        sql = Path(f).read_text("utf-8")
        for m in re.finditer(r"INSERT INTO (?:\w+\.)?(\w+) \(([^)]*)\) VALUES(.*?);\s*$", sql,
                             re.S | re.M):
            table, cols, vals = m.groups()
            cols = [c.strip() for c in cols.split(",")]
            db.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ('
                       + ", ".join(f'"{c}"' for c in cols) + ")")
            db.execute(f'INSERT INTO "{table}" ({", ".join(cols)}) VALUES {vals}')
    return db


def _estates() -> dict[str, dict]:
    """One amount per estate: the row, its currency CODE and its amount, as the source holds them."""
    fin = _load(sorted((FIN / "core_banking" / "seed").glob("*.sql")))
    fid, amount, code = fin.execute(
        "SELECT facility_id, limit_amount, currency FROM facility ORDER BY facility_id "
        "LIMIT 1 OFFSET 301").fetchone()
    lark = _load([LARKSPUR / "db" / "01_schema_and_data.sql"])
    inv, total = lark.execute("SELECT invoice_no, total_gbp FROM invoices ORDER BY invoice_no "
                              "LIMIT 1").fetchone()
    # Larkspur's ledger states GBP in the COLUMN NAME (total_gbp): the code is that, as written
    memo = sorted(p.stem.split("_")[0] for p in (FIN / "credit_memos").rglob("CM-*.md"))[0]
    return {
        "finance": {"source": "finance_core_banking", "row": fid, "column": "limit_amount",
                    "value": amount, "code": code, "ident": memo},
        "larkspur": {"source": "larkspur-db", "row": inv, "column": "total_gbp", "value": total,
                     "code": "GBP", "ident": inv},
    }


ESTATES = _estates()


def _registry(e: dict) -> tuple[PointerRegistry, str]:
    reg = PointerRegistry()
    ptr = SourcePointer(source_id=e["source"], kind="table_cell", table="t", column=e["column"],
                        row_id=e["row"], trust=Trust.UNTRUSTED)
    # the cell as the source renders it: a raw number, no grouping, no code
    return reg, reg.add(ptr, f"{e['column']}: {e['value']}; currency: {e['code']}")


def _grouped(v: Decimal) -> str:
    return f"{v:,.2f}"


@pytest.mark.parametrize("estate", sorted(ESTATES))
def test_fin08_an_amount_flush_against_its_code_is_read_by_value(estate):
    e = ESTATES[estate]
    reg, ref = _registry(e)
    v = Decimal(str(e["value"]))
    same = [f"{e['code']}{_grouped(v)}", f"{e['code']} {_grouped(v)}", f"{e['code']}{v}"]
    other = [f"{e['code']}{_grouped(v + Decimal('0.01'))}", f"{e['code']}{_grouped(v * 10)}"]
    final = FinalAnswer(claims=[Claim(text=f"The {e['column']} of {e['row']} is {s}.",
                                      citations=[ref]) for s in same + other])
    got = [c.supported for c in check_answer(final, reg).claims]
    assert got == [True] * len(same) + [False] * len(other)
    # the same rule on an ACTION argument: the code is a token, the amount a value
    assert ungrounded_values({"amount": same[0]}, [reg.get(ref)]) == []
    assert ungrounded_values({"amount": other[0]}, [reg.get(ref)]) != []


@pytest.mark.parametrize("estate", sorted(ESTATES))
def test_fin08_an_identifier_written_with_a_typographic_dash_is_the_same_identifier(estate):
    e = ESTATES[estate]
    reg = PointerRegistry()
    ref = reg.add(SourcePointer(source_id=e["source"], kind="table_cell", table="t", column="id",
                                row_id=e["ident"], trust=Trust.UNTRUSTED), f"id: {e['ident']}")
    ev = [reg.get(ref)]
    assert "-" in e["ident"]
    for dash in ("‑", "–", "−"):
        assert ungrounded_values({"id": e["ident"].replace("-", dash)}, ev) == []
        # casing and spacing never changed what an identifier names; a different one is refused
        assert ungrounded_values({"id": e["ident"].lower()}, ev) == []
    digits = re.sub(r"\d(?=\D*$)", lambda m: str((int(m.group(0)) + 1) % 10), e["ident"])
    assert digits != e["ident"] and ungrounded_values({"id": digits}, ev) == [digits]


@pytest.mark.parametrize("estate", sorted(ESTATES))
def test_fin08_the_run_date_grounds_a_date_never_a_count(estate):
    e = ESTATES[estate]
    reg, ref = _registry(e)
    held = {str(int(n)) for n in re.findall(r"\d+", reg.get(ref).text)}
    # a day of the month that no evidence holds: what the run date alone would have "grounded"
    day = next(d for d in range(1, 29) if str(d) not in held)
    at = dt.datetime(2026, 10, day, 9, 30, tzinfo=dt.timezone.utc)
    literals = run_context("run-x", at, "UTC")
    assert f"2026-10-{day:02d}" in literals
    final = FinalAnswer(claims=[
        Claim(text=f"The table holds {day} rows for {e['row']}.", citations=[ref]),
        Claim(text=f"As of {day} October 2026, {e['row']} has {e['column']} {e['value']}.",
              citations=[ref]),
        Claim(text=f"Checked on 2026-10-{day:02d} at 09:30.", citations=[ref])])
    got = [c.supported for c in check_answer(final, reg, literals=literals).claims]
    assert got == [False, True, True]


# ------------------------------------------------------------------------------------------------
# FIN-11: a path written from the top of the tree, over the real files_blob path rule
# ------------------------------------------------------------------------------------------------
def _roots_class():
    pytest.importorskip("mcp")
    spec = importlib.util.spec_from_file_location(
        "files_blob_server", REPO / "packages" / "mcp_ingest" / "servers" / "files_blob" /
        "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Roots, mod.AccessDenied


def _readable_doc(root: Path) -> str:
    """The first ``.md`` the estate's own screening expectations do not withhold (a withheld item
    is answered with the screen's decision, not its text — a different test)."""
    import yaml
    held: set[str] = set()
    for exp in (root.parent / "expected_outcomes.yaml", root / "expected_outcomes.yaml"):
        if exp.is_file():
            files = (yaml.safe_load(exp.read_text("utf-8")) or {}).get("files") or {}
            held |= {k for k, v in files.items() if (v or {}).get("decision") != "allow"}
    return sorted(r for r in (p.relative_to(root).as_posix() for p in root.rglob("*.md"))
                  if r not in held)[0]


FILE_TREES = {"larkspur-files": LARKSPUR / "files", "finance_credit_memos": FIN / "credit_memos"}
PATH_SCHEMA = {"type": "object", "properties": {"path": {"type": "string"}},
               "required": ["path"]}


class FilesBlobGateway(FakeGateway):
    """The files_blob server's ``list_dir`` and ``read_bytes``, resolving every path with the
    server's OWN rule (`Roots.resolve`): relative to the single root, or absolute and inside it."""

    def __init__(self, conn: str, root: Path):
        super().__init__()
        Roots, self.denied = _roots_class()
        self.conn, self.roots = conn, Roots([str(root)])

    def advertise(self, connection):
        if connection == self.conn:
            return ConnectionAdvert(connection=connection, declared_in="var/sources/x.yaml",
                                    tools=(ToolAdvert("list_dir", writes=False,
                                                      input_schema=PATH_SCHEMA),
                                           ToolAdvert("read_bytes", writes=False,
                                                      input_schema=PATH_SCHEMA)))
        return super().advertise(connection)

    def call_tool(self, connection, tool, arguments):
        self.calls.append(("call_tool", connection, tool, dict(arguments)))
        try:
            real = Path(self.roots.resolve(arguments.get("path")))
        except self.denied as e:
            return GatewayResult(is_error=True, text=f"Error executing tool {tool}: {e}")
        if tool == "list_dir":
            return GatewayResult(text=json.dumps({"path": str(real), "entries": [
                {"name": p.name, "type": "dir" if p.is_dir() else "file", "path": str(p)}
                for p in sorted(real.iterdir())]}))
        return GatewayResult(data=real.read_bytes(), mime="text/markdown", uri=real.as_uri())


@pytest.mark.parametrize("conn", sorted(FILE_TREES))
def test_fin11_a_path_from_the_top_of_the_tree_is_read_from_the_sources_root(
        conn, store, compile_inputs, dirs):
    root = FILE_TREES[conn]
    doc = _readable_doc(root)
    folder = doc.rsplit("/", 1)[0] if "/" in doc else "."
    gw = FilesBlobGateway(conn, root)
    calls = [{"tool": f"source.read:{conn}:list_dir", "args": {"path": "/"}},
             {"tool": f"source.read:{conn}:list_dir", "args": {"path": f"/{folder}"}},
             {"tool": f"source.read:{conn}:read_bytes", "args": {"path": f"/{doc}"}},
             # never a way out: the retried form must still be inside the root
             {"tool": f"source.read:{conn}:list_dir", "args": {"path": "/../.."}}]

    def script(i, obs, messages):
        return {"tool_calls": calls} if i == 0 else {"final": {"claims": []}}

    spec, decl = make_pair(briefing_spec(
        sources=[conn], tools=["graph.search", f"source.read:{conn}:list_dir",
                               f"source.read:{conn}:read_bytes"]))
    harness, _ = _harness(script)
    rec = run_agent(spec, decl, store=store, harness=harness, gateway=gw,
                    trigger={"type": "manual", "by": "dana"}, compile_inputs=compile_inputs,
                    notifier=lambda _e: None, **dirs)
    steps = [s for s in rec.steps if s.tool.startswith("source.read:")]
    assert [s.args for s in steps[:3]] == [{"path": "."}, {"path": folder}, {"path": doc}]
    assert all(s.result_pointers for s in steps[:3])
    # the read is the file's bytes, captured: its text re-reads from the capture store
    ptr = steps[2].result_pointers[0]
    sha = ptr["capture_digest"]
    assert (dirs["capture_dir"] / sha[:2] / sha).read_bytes() == (root / doc).read_bytes()
    # the escape is refused both ways, and nothing outside the root was read
    assert not steps[3].result_pointers
    tried = [c[3]["path"] for c in gw.calls if c[0] == "call_tool"]
    assert tried.count("/") == 1 and "." in tried and "/../.." in tried and "../.." in tried
    assert "reduced" in rec.answer.markdown.lower() or "nothing usable" in rec.answer.markdown
