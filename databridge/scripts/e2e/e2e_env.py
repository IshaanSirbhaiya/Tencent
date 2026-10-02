"""E2E harness env: point EVERY Walnut runtime location at a private scratch dir.

Usage (in-process):   from e2e_env import setup_env; E = setup_env()
The scratch dir comes from WALNUT_E2E_DIR (default: databridge/var/e2e-20261001T0356, run 1).

Run 2+ (E2E2): the operator's declarations file (infra/mcp_servers.yaml) is the ONLY file
`POST /sources {mode: import}` may read, and it addresses the corpus and the follow-ups folder
through ${WALNUT_ROOT}. A run therefore isolates itself the way an operator would deploy a second
instance: WALNUT_ROOT points at <run>/root, a deployment root whose `packages` is a junction to the
real code and whose `fixtures/workspace_demo/files` is the run's private corpus copy. When
<run>/root does not exist (run 1) WALNUT_ROOT stays the repo.
Keys are loaded from databridge/.env with python-dotenv and are never printed.

**Either estate** (2026-10-02, after the finance dry run — see
`wiki/40-reviews/LESSON-2026-10-02-completeness.md`): `WALNUT_E2E_ESTATE` (or `setup_env(estate=)`)
selects `workspace` (the default, runs 1-4) or `finance`. The preset names the packs, the graph
file, the agent purpose, the golden set and the operator declarations — the same values the
estate's own launcher sets (`start-walnut-finance.ps1`). Four runs on one small estate missed
everything that only shows at the second estate's size and shape.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]            # databridge/
PACKAGES = ("packages/core", "packages/harness", "packages/ingest", "packages/semantic",
            "packages/convert", "packages/graph", "packages/intake", "packages/agent",
            "packages/adapter", "packages/agentdef", "packages/materialise",
            "packages/outbox", "packages/evals", "packages/orchestrator",
            "packages/workflow_spec", "packages/mcp_ingest", "packages/retrieval",
            "packages/agent_runtime", "packages/automation", "packages/adapter_agents",
            "apps/gateway", "apps/mcp_server", "apps/dispatcher")


#: Per-estate deployment settings: what the estate's launcher sets. `mcp_servers` is relative to
#: the run dir unless it starts with `infra/` (then relative to the repo). Harness configuration,
#: not engine code — the engine reads none of this.
ESTATES: dict[str, dict[str, str]] = {
    "workspace": {"packs": "workspace", "graph": "workspace.db",
                  "agent_purpose": "workspace.agent_run",
                  "golden_set": "packs/workspace/evals/agent_contract.jsonl",
                  "mcp_servers": "mcp_servers.yaml", "passages": "", "live_db": "larkspur"},
    "finance": {"packs": "finance", "graph": "finance.db",
                "agent_purpose": "lending.portfolio_reporting",
                "golden_set": "packs/finance/evals/credit_memo_v3.jsonl",
                "mcp_servers": "infra/mcp_servers.finance.yaml", "passages": "1",
                "live_db": "finance_demo"},
}


def estate_name(estate: str | None = None) -> str:
    name = estate or os.environ.get("WALNUT_E2E_ESTATE") or "workspace"
    if name not in ESTATES:
        raise ValueError(f"unknown e2e estate {name!r}; known: {sorted(ESTATES)}")
    return name


def graph_db(E: Path, estate: str | None = None) -> Path:
    """The run's graph file for the estate."""
    return Path(E) / "graph" / ESTATES[estate_name(estate)]["graph"]


def e2e_dir() -> Path:
    return Path(os.environ.get("WALNUT_E2E_DIR") or (ROOT / "var" / "e2e-20261001T0356")).resolve()


def corpus_dir(E: Path) -> Path:
    """The run's private corpus copy (run 2+: inside the deployment root)."""
    dr = E / "root" / "fixtures" / "workspace_demo" / "files"
    return dr if (E / "root").is_dir() else E / "corpus" / "files"


def followups_dir(E: Path) -> Path:
    """Where larkspur-followups writes (run 2+: the declaration's ${WALNUT_ROOT}/var/... path)."""
    return (E / "root" / "var" / "workspace_demo" / "Follow-ups") if (E / "root").is_dir()         else E / "followups"


def setup_env(load_keys: bool = True, estate: str | None = None) -> Path:
    E = e2e_dir()
    preset = ESTATES[estate_name(estate)]
    servers = preset["mcp_servers"]
    servers_path = ROOT / servers if servers.startswith("infra/") else E / servers
    deploy_root = E / "root"
    for p in PACKAGES:
        sp = str(ROOT / p)
        if sp not in sys.path:
            sys.path.insert(0, sp)
    if load_keys:
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env", override=False)
        except ImportError:
            pass
    env = {
        "WALNUT_ROOT": str(deploy_root if deploy_root.is_dir() else ROOT),
        "WALNUT_PYTHON": sys.executable,
        "DATABRIDGE_PACKS": preset["packs"],
        "WALNUT_E2E_ESTATE": estate_name(estate),
        "WALNUT_TIMEZONE": "Asia/Singapore",
        "WALNUT_AGENT_PURPOSE": preset["agent_purpose"],
        "WALNUT_AGENT_GOLDEN_SET": str(ROOT / preset["golden_set"]),
        "WALNUT_GRAPH_DB_PATH": str(graph_db(E, estate)),
        "WALNUT_CAPTURE_DIR": str(E / "capture"),
        "WALNUT_SOURCES_DIR": str(E / "sources"),
        "WALNUT_INGEST_DIR": str(E / "ingest"),
        "WALNUT_EVENTS_PATH": str(E / "events" / "events.jsonl"),
        "WALNUT_EVENTS_OFFSETS_DIR": str(E / "events" / "offsets"),
        "WALNUT_RUNS_DIR": str(E / "runs"),
        "WALNUT_PASSAGES_DIR": str(E / "passages"),
        "WALNUT_CACHE_DIR": str(E / "cache"),
        "WALNUT_ADAPTER_AGENTS_DIR": str(E / "adapter_agents"),
        "WALNUT_AGENTS_DIR": str(E / "agents"),
        "WALNUT_AUTOMATION_DIR": str(E / "automation"),
        "WALNUT_FACTS_DIR": str(E / "facts"),
        "WALNUT_ONTOLOGY_STATE_PATH": str(E / "ontology" / "state.json"),
        "WALNUT_ONTOLOGY_AUDIT_PATH": str(E / "ontology" / "audit.jsonl"),
        "WALNUT_ACT_REGISTRY_PATH": str(E / "ontology" / "act_registry.json"),
        "WALNUT_MCP_STATE": str(E / "mcp_state"),
        "WALNUT_MCP_SERVERS": str(servers_path),
        "WALNUT_FOLLOWUPS_DIR": str(followups_dir(E)),
        "WALNUT_E2E_CORPUS": str(corpus_dir(E)),
        "WALNUT_DEMO_PG_PORT": "5433",
        "MCPJUNGLE_URL": "http://127.0.0.1:8080",
        "DATABRIDGE_OUTBOX_DB": str(E / "outbox.sqlite"),
        "WALNUT_MATERIALISE_DIR": str(E / "materialise"),
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    if preset["passages"]:
        env["WALNUT_INGEST_PASSAGES"] = preset["passages"]
    for k, v in env.items():
        os.environ[k] = v
    for d in ("graph", "capture", "sources", "ingest", "events", "runs", "passages", "cache",
              "adapter_agents", "agents", "automation", "facts", "ontology", "followups",
              "artefacts", "logs", "materialise"):
        (E / d).mkdir(parents=True, exist_ok=True)
    return E


def key_presence() -> dict:
    return {k: bool(os.environ.get(k)) for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
