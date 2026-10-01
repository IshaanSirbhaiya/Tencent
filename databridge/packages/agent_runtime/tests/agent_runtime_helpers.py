"""Fixtures for the agent runtime: a real compiler, a real graph store, a real harness over a
scripted FakeBackend, and a fake source gateway. No network, no live model.

The governance inputs (a `purposes.yaml`, a golden set) live in a temp repo root so these tests
never depend on — or write into — the shared `packs/` tree.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

_PACKAGES = Path(__file__).resolve().parents[2]
for _name in ("agent_runtime", "workflow_spec", "agentdef", "adapter", "outbox", "graph", "core",
              "harness"):
    _p = str(_PACKAGES / _name)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from databridge_core import SourcePointer                                 # noqa: E402
from databridge_core.provenance import Trust                              # noqa: E402
from databridge_harness import FakeBackend, Harness                       # noqa: E402

from databridge_agent_runtime import (                                    # noqa: E402
    CompileInputs,
    ConnectionAdvert,
    GatewayResult,
    Governance,
    ToolAdvert,
    declaration_from_workflow,
    parse_declaration_text,
)
from databridge_workflow_spec import WorkflowSpec                         # noqa: E402

PACK = "workspace"
PURPOSE = "workspace.agent_run"

HARNESS_CONFIG = {
    "routing": {"agent": "planner"},
    "aliases": {"planner": {"model": "fake/planner"}},
    "profiles": {"cloud": {}},
}


# ------------------------------------------------------------------------------------------
# governance inputs, in a temp tree
# ------------------------------------------------------------------------------------------
@pytest.fixture
def gov_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "packs" / PACK / "evals").mkdir(parents=True)
    (root / "packs" / PACK / "purposes.yaml").write_text(
        f"pack: {PACK}\npurposes:\n  - id: {PURPOSE}\n    description: run a user agent\n"
        f"    subject_scoped: false\n    lawful_basis: legitimate interests\n", encoding="utf-8")
    (root / "packs" / PACK / "evals" / "agent_contract.jsonl").write_text(
        json.dumps({"question": "q", "expect": "cited"}) + "\n", encoding="utf-8")
    return root


@pytest.fixture
def compile_inputs(gov_root: Path) -> CompileInputs:
    return CompileInputs(packs_dir=gov_root / "packs", repo_root=gov_root)


def governance(**touches: bool) -> Governance:
    return Governance(purpose=PURPOSE, golden_set=f"packs/{PACK}/evals/agent_contract.jsonl",
                      escalations_per_day=10, cost_ceiling_usd_per_day=5.0,
                      touches_subject=touches)


def make_pair(spec_dict: dict, **touches: bool):
    spec = WorkflowSpec.model_validate(spec_dict)
    text = declaration_from_workflow(spec, PACK, governance(**touches))
    return spec, parse_declaration_text(text, declared_in=f"var/agents/{spec.agent}.agent.yaml")


def save_pair(agents_dir: Path, spec_dict: dict, **touches: bool):
    """Write both §5.6 files, then load them back the way `approve` will (so the proposing run
    and the approval see the same agent)."""
    from databridge_agent_runtime import load_agent
    from databridge_workflow_spec import dump_spec

    spec = WorkflowSpec.model_validate(spec_dict)
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{spec.agent}.workflow.yaml").write_text(dump_spec(spec), encoding="utf-8")
    (agents_dir / f"{spec.agent}.agent.yaml").write_text(
        declaration_from_workflow(spec, PACK, governance(**touches)), encoding="utf-8")
    return load_agent(spec.agent, agents_dir)


def person(pid: str = "dana.lee"):
    """An authenticated principal, MINTED the way the application boundary mints one: by the
    authenticator the (test) identity provider registered (R-021 #5)."""
    from databridge_agent_runtime import register_authenticator
    return register_authenticator("test-idp").issue(pid)


# ------------------------------------------------------------------------------------------
# a fake live source gateway
# ------------------------------------------------------------------------------------------
class FakeGateway:
    """Three connections: `larkspur-files` (read_text_file, write_file), `larkspur-db` (read-only
    SQL) and `larkspur_db_writer` (`execute_sql`, a write tool — DB writes never go through
    source.query)."""

    def __init__(self, files: Optional[dict[str, str]] = None) -> None:
        self.files = dict(files or {"/Contracts/northwind_msa.txt":
                                    "Northwind MSA. Payment terms: net 45 days."})
        self.calls: list[tuple] = []

    def advertise(self, connection: str) -> ConnectionAdvert:
        if connection == "larkspur-files":
            return ConnectionAdvert(connection=connection, declared_in="var/sources/files.yaml",
                                    tools=(ToolAdvert("read_text_file", writes=False),
                                           ToolAdvert("list_directory", writes=False),
                                           ToolAdvert("write_file", writes=True)))
        if connection == "larkspur-db":
            return ConnectionAdvert(connection=connection, declared_in="var/sources/db.yaml",
                                    tools=(ToolAdvert("query", writes=False),), sql=True)
        if connection == "larkspur_db_writer":
            return ConnectionAdvert(connection=connection, declared_in="var/sources/dbw.yaml",
                                    tools=(ToolAdvert("execute_sql", writes=True),))
        return ConnectionAdvert(connection=connection, reachable=False, problem="unknown")

    def call_tool(self, connection: str, tool: str, arguments: dict) -> GatewayResult:
        self.calls.append(("call_tool", connection, tool, dict(arguments)))
        if tool == "read_text_file":
            path = arguments.get("path", "")
            if path not in self.files:
                return GatewayResult(is_error=True, text=f"no such file {path}")
            return GatewayResult(text=self.files[path], uri=f"file://{path}")
        if tool == "write_file":
            self.files[arguments["path"]] = arguments.get("content", "")
            return GatewayResult(text=f"wrote {arguments['path']}")
        if tool == "execute_sql":
            return GatewayResult(text="INSERT 0 1")
        return GatewayResult(text="[]")

    def query(self, connection: str, sql: str) -> GatewayResult:
        self.calls.append(("query", connection, sql))
        if sql.strip().lower().startswith("insert"):
            return GatewayResult(text="INSERT 0 1")
        return GatewayResult(text=json.dumps([{"invoice": "INV-1042", "customer": "Northwind",
                                               "days_overdue": 21, "amount": 12500}]))

    def writes(self) -> list[tuple]:
        return [c for c in self.calls if (c[0] == "call_tool" and c[2] in ("write_file",
                                                                        "execute_sql"))
                or (c[0] == "query" and not c[2].strip().lower().startswith("select"))]


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


# ------------------------------------------------------------------------------------------
# a scripted planner behind the REAL harness
# ------------------------------------------------------------------------------------------
def observations_of(messages: list[dict]) -> list[dict]:
    last = messages[-1]["content"]
    if isinstance(last, str) and last.startswith("Observations:\n"):
        return json.loads(last[len("Observations:\n"):])
    return []


def refs_in(obj: Any) -> list[str]:
    """Every pointer ref (``pN``) an observation carries, in order, without assuming numbering."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "ref" and isinstance(v, str):
                out.append(v)
            elif k == "refs" and isinstance(v, list):
                out.extend(x for x in v if isinstance(x, str))
            else:
                out.extend(refs_in(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(refs_in(v))
    return out


def scripted_harness(script: Callable[[int, list[dict]], dict], *,
                     tokens: int = 5) -> tuple[Harness, FakeBackend]:
    """``script(turn_index, observations) -> planner turn dict``."""
    state = {"i": 0}

    def respond(model, messages, guided_json):
        turn = script(state["i"], observations_of(messages))
        state["i"] += 1
        return json.dumps(turn)

    backend = FakeBackend(respond, tokens_in=tokens, tokens_out=tokens)
    return Harness(backend, config=HARNESS_CONFIG, profile="cloud", cache=None), backend


# ------------------------------------------------------------------------------------------
# a real graph store under the workspace vocabulary
# ------------------------------------------------------------------------------------------
@pytest.fixture
def workspace_vocab():
    from databridge_graph.packs import deployment
    with deployment(PACK) as vocab:
        yield vocab


def db_cell(table: str, column: str, row: str) -> SourcePointer:
    return SourcePointer(source_id="larkspur-db", kind="table_cell", table=table, column=column,
                         row_id=row, trust=Trust.UNTRUSTED)


@pytest.fixture
def store(workspace_vocab):
    from databridge_graph.kinds import EdgeKind, NodeKind
    from databridge_graph.schema import GraphEdge, GraphNode
    from databridge_graph.store import GraphStore

    s = GraphStore()
    t0 = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    org_ptr = db_cell("customers", "name", "7")
    s.add_node(GraphNode(
        id="organisation:northwind", kind=NodeKind("organisation"), label="Northwind Traders",
        attrs={"payment_terms_days": 45}, attr_sources={"payment_terms_days":
                                                        db_cell("customers", "terms", "7")},
        sources=(org_ptr,), valid_from=t0, system_from=t0))
    s.add_node(GraphNode(
        id="person:ana-ruiz", kind=NodeKind("person"), label="Ana Ruiz",
        sources=(db_cell("contacts", "name", "3"),), valid_from=t0, system_from=t0))
    s.add_node(GraphNode(
        id="organisation:elsewhere", kind=NodeKind("organisation"), label="Elsewhere Northwind Ltd",
        sources=(SourcePointer(source_id="other-src", kind="table_cell", table="t", column="c",
                               row_id="1", trust=Trust.UNTRUSTED),),
        valid_from=t0, system_from=t0))
    s.add_edge(GraphEdge(
        id="edge:ana-northwind", src_id="person:ana-ruiz", dst_id="organisation:northwind",
        kind=EdgeKind("works_for"), sources=(db_cell("contacts", "company_id", "3"),),
        valid_from=t0, system_from=t0))
    yield s
    s.close()


# ------------------------------------------------------------------------------------------
# workflow dicts
# ------------------------------------------------------------------------------------------
def briefing_spec(**over) -> dict:
    d = {
        "agent": "account_briefing", "title": "Account briefing",
        "created_from": "Brief me on a customer before a call.",
        "mode": "read_only", "sources": ["larkspur-db", "larkspur-files"],
        "tools": ["graph.search", "graph.node", "graph.neighbors", "source.query:larkspur-db",
                  "source.read:larkspur-files:read_text_file"],
        "instructions": "1. Find the customer. 2. Read the contract. 3. Brief with citations.",
        "limits": {"max_steps": 6, "max_tool_calls": 10, "tokens_per_run": 20000},
    }
    d.update(over)
    return d


def followup_spec(**over) -> dict:
    d = {
        "agent": "overdue_invoice_followup", "title": "Overdue invoice follow-up",
        "created_from": "Every weekday at 9, find overdue invoices, draft a reminder into the "
                        "Follow-ups folder and log a task. Ask me before logging anything; tell "
                        "me after drafts are saved.",
        "mode": "read_write", "sources": ["larkspur-db", "larkspur-files", "larkspur_db_writer"],
        "tools": ["graph.search", "source.query:larkspur-db"],
        "actions": [
            {"name": "save_followup_draft", "tool": "source.write:larkspur-files:write_file",
             "automation_mode": "human_notified", "reversal": "delete the written file"},
            {"name": "log_followup_task", "tool": "source.write:larkspur_db_writer:execute_sql",
             "automation_mode": "human_required"},
        ],
        "trigger": {"type": "schedule", "schedule": {"cron": "0 9 * * 1-5",
                                                     "timezone": "Asia/Singapore"}},
        "instructions": "1. Query overdue invoices. 2. Draft. 3. Log a task.",
        "limits": {"max_steps": 6, "max_tool_calls": 10, "tokens_per_run": 20000},
    }
    d.update(over)
    return d
