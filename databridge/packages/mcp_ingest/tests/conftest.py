"""Fixtures for packages/mcp_ingest. Everything is synthetic; nothing touches the network or a model.

**Import bootstrap.** `packages/mcp_ingest` is not yet on the root pytest `pythonpath` (adding it
means editing the root pyproject and `apps/gateway/serve.py` together, outside this workstream),
so this conftest puts the package — and its sibling dependencies — on `sys.path`. Run with
`python -m pytest packages/mcp_ingest` from `databridge/`.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[1]
_DATABRIDGE = _PKG.parents[1]
for _p in (_PKG, *(_DATABRIDGE / "packages" / n for n in
                   ("core", "harness", "convert", "graph", "intake", "adapter", "ingest"))):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

FIXTURE_SERVER = Path(__file__).with_name("fixture_server.py")

#: The synthetic entities the fake model "finds" — it computes offsets from the text it is sent,
#: so an entity is returned only where it really occurs (the grounding wall still checks).
ENTITIES = {"Aldermoor Logistics": "organisation", "Quillon Freight": "organisation",
            "Priya Venkataraman": "person", "Tomasz Brennick": "person"}

FAKE_KEY = ("-----BEGIN OPENSSH PRIVATE KEY-----\n"
            "b3BlbnNzaC1rZXktdjEAAAAAfakefakefakefakefakefakefakefakefake\n"
            "-----END OPENSSH PRIVATE KEY-----\n")


def _long_overview() -> str:
    para = ("Aldermoor Logistics coordinates regional freight planning with its partners. "
            "Priya Venkataraman leads the planning group and reviews each quarterly schedule. ")
    filler = ("The schedule covers depots, routes and staffing rotations for the coming season, "
              "and every change is logged for review. ")
    body = para + filler * 14 + "Quillon Freight joined the programme later in the year. " + \
        filler * 10 + "Contact: planning@aldermoor.example or +44 20 7946 0123.\n"
    return "# Overview\n\n" + body


def write_corpus(root: Path) -> dict[str, str]:
    """The synthetic corpus. Returns rel-path -> expected collect outcome (derived by the tests
    from THIS mapping, never hard-coded elsewhere)."""
    files = {
        "projects/alpha/notes.txt": ("Kick-off notes. Aldermoor Logistics will pilot the new "
                                     "route planner with Tomasz Brennick as sponsor.\n", "collect"),
        "projects/beta/notes.txt": ("Beta notes. Quillon Freight asked for weekly depot "
                                    "summaries; Priya Venkataraman agreed.\n", "collect"),
        "docs/overview.md": (_long_overview(), "collect"),
        "engineering/deploy.txt": ("Deploy steps for the staging box.\n\n" + FAKE_KEY,
                                   "collect"),
        "pricing/rates.md": ("# Rate card\n\nLane A: $120\n", "blocked"),
        "images/logo.bin": ("\x00\x01binary", "excluded"),
    }
    for rel, (content, _) in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content.encode("utf-8"))
    return {rel: outcome for rel, (_, outcome) in files.items()}


def write_db(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE organisations (id INTEGER PRIMARY KEY, name TEXT, domain TEXT);
        CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT, holder_email TEXT,
                               organisation_id INTEGER REFERENCES organisations(id));
        CREATE TABLE audit_log (id INTEGER PRIMARY KEY, happened TEXT, detail TEXT);
        INSERT INTO organisations VALUES (1, 'Aldermoor Logistics', 'aldermoor.example'),
                                         (2, 'Quillon Freight', 'quillon.example');
        INSERT INTO accounts VALUES (10, 'Aldermoor operating', 'ops@aldermoor.example', 1),
                                    (11, 'Quillon settlement', 'pay@quillon.example', 2),
                                    (12, 'Quillon reserve', NULL, 2);
        INSERT INTO audit_log VALUES (1, '2026-09-01', 'nightly export');
    """)
    conn.commit()
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("organisations", "accounts", "audit_log")}
    conn.close()
    return counts


def extraction_handler(calls: list):
    """A FakeBackend handler: extraction computes grounded offsets from the text it was SENT;
    other task classes return an empty draft. Every call's messages are recorded."""
    def handler(*, model, messages, guided_json) -> str:
        calls.append(messages)
        last = messages[-1]["content"]
        if last.startswith("DOCUMENT TEXT:\n"):
            text = last[len("DOCUMENT TEXT:\n"):]
            ents = []
            for name, kind in ENTITIES.items():
                i = text.find(name)
                if i >= 0:
                    ents.append({"name": name, "kind": kind, "char_start": i,
                                 "char_end": i + len(name)})
            return json.dumps({"entities": ents, "relations": []})
        return json.dumps({})
    return handler


@pytest.fixture
def walnut_env(tmp_path, monkeypatch):
    dirs = {"WALNUT_SOURCES_DIR": tmp_path / "sources", "WALNUT_INGEST_DIR": tmp_path / "ingest",
            "WALNUT_CAPTURE_DIR": tmp_path / "capture",
            "WALNUT_EVENTS_PATH": tmp_path / "events" / "events.jsonl",
            "WALNUT_GRAPH_DB_PATH": tmp_path / "graph" / "test.db"}
    for k, v in dirs.items():
        monkeypatch.setenv(k, str(v))
    monkeypatch.delenv("WALNUT_CRAWL_PROFILES_DIR", raising=False)
    return dirs


@pytest.fixture
def common_vocab():
    from databridge_graph.packs import deployment

    with deployment("common") as vocab:
        yield vocab


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    return root, write_corpus(root)


@pytest.fixture
def fixture_db(tmp_path):
    path = tmp_path / "fixture.sqlite"
    return path, write_db(path)


def fs_connection(conn_id: str, root: Path, *, db: Path | None = None, **scope) -> dict:
    args = [str(FIXTURE_SERVER), "--root", str(root)]
    if db is not None:
        args += ["--db", str(db)]
    return {"id": conn_id, "title": conn_id,
            "transport": {"type": "stdio", "command": sys.executable, "args": args},
            "scope": {"blocked": ["**/pricing/**"], **scope}, "auto_approve": True}


@pytest.fixture
def fake_harness():
    from databridge_harness import FakeBackend, Harness

    calls: list = []
    backend = FakeBackend(extraction_handler(calls))
    harness = Harness(backend, profile="cloud")
    harness.calls = calls          # type: ignore[attr-defined]
    harness.backend = backend      # type: ignore[attr-defined]
    return harness


#: The NAMED person who approves plans and mappings in these tests. Since EF3 (Codex round 3,
#: Critical) a connection's `auto_approve` flag no longer approves anything — only a person does.
#: Tests that are about ingestion rather than approval still declare `auto_approve: True` as their
#: way of saying "a human approves whatever is proposed"; this fixture is that human, recorded by
#: name on every approval, so no test silently depends on the removed flag. A test about the
#: approval gate itself opts out with `@pytest.mark.real_approval`.
TEST_APPROVER = "Test Approver (conftest)"


def test_principal(principal_id: str = TEST_APPROVER):
    """An agent_runtime `Approver` ISSUED by a registered (test) authenticator — what an
    approval that must be authenticated (mappings, dialects) needs (R-022 f1 #1)."""
    from databridge_agent_runtime import register_authenticator

    return register_authenticator("mcp_ingest-tests").issue(principal_id)


def pytest_configure(config):
    config.addinivalue_line("markers", "real_approval: no conftest approver; the test drives "
                                       "every approval itself")


@pytest.fixture(autouse=True)
def _named_test_approver(request, monkeypatch):
    if request.node.get_closest_marker("real_approval"):
        yield
        return
    import databridge_mcp_ingest as mi
    from databridge_mcp_ingest import mapping, plans

    real_propose, real_ensure = plans.propose_plan, mapping.ensure_mapping

    def _flag(source_id, root=None) -> bool:
        from databridge_mcp_ingest.connections import load_connection

        try:
            return bool(load_connection(source_id, root).auto_approve)
        except Exception:                                            # noqa: BLE001
            return False

    def propose(source_id, *a, **kw):
        plan = real_propose(source_id, *a, **kw)
        if _flag(source_id, kw.get("root")) and not plan.problems and plan.steps:
            # the test human also vouches, BY NAME, for any read tool nothing else vouches for
            # (e.g. server-memory's un-annotated `open_nodes`); writes are never vouchable.
            unknown = [t for t, c in plan.tool_classes.items()
                       if c.startswith("unclassified:unknown")]
            # R-022 f1 #1 (FXI3): a plan approval needs an AUTHENTICATED principal, and is
            # checked against the live server (the same client, when the test passed one)
            plan = plans.approve_plan(source_id, principal=test_principal(), root=kw.get("root"),
                                      vouch_read_tools=unknown, client=kw.get("client"))
        return plan

    def ensure(source_id, snapshots, vocab, **kw):
        m = real_ensure(source_id, snapshots, vocab, **kw)
        # FXJ1 (Codex f2 #2): the runner now always passes auto_approve=False, so the test human
        # keys on the CONNECTION's flag (the tests' way of saying "a person approves this")
        if (kw.get("auto_approve") or _flag(source_id, kw.get("root"))) \
                and any(t.status == "proposed" for t in m.tables.values()):
            # R-022 f1 #1: a mapping approval needs an AUTHENTICATED principal, not a name
            m = mapping.approve_mapping(source_id, principal=test_principal(), vocab=vocab,
                                        root=kw.get("root"))
            m = mapping.effective_mapping(m, vocab, kw.get("root"))
        return m

    monkeypatch.setattr(plans, "propose_plan", propose)
    monkeypatch.setattr(mi, "propose_plan", propose)
    monkeypatch.setattr(mapping, "ensure_mapping", ensure)
    yield


@pytest.fixture(autouse=True)
def _operator_registration_mode(monkeypatch):
    """FXJ3 (Codex f2 #10): registration is fail-closed (`remote`) unless the DEPLOYMENT declares
    operator mode. These suites register their own stdio fixture servers as the host operator, so
    they declare it here, explicitly; a test about the remote policy deletes it."""
    monkeypatch.setenv("WALNUT_SOURCE_REGISTRATION", "operator")


@pytest.fixture(autouse=True)
def _local_approval_key(monkeypatch):
    """INT5 (R-022 build cross-track 4): approvals are SEALED with the deployment approval key
    (`mcp_ingest.screen`). Tests use a LOCAL key file under `databridge/var/keys/` (gitignored,
    never committed), generated here if missing — never the developer's `~/.walnut` key. A test
    about key custody overrides `WALNUT_APPROVAL_KEY_FILE` itself."""
    import secrets as _secrets

    key = Path(__file__).resolve().parents[3] / "var" / "keys" / "approval.test.key"
    if not key.is_file():
        key.parent.mkdir(parents=True, exist_ok=True)
        tmp = key.with_name(f".{key.name}.{_secrets.token_hex(6)}.tmp")
        tmp.write_text(_secrets.token_hex(32), encoding="utf-8")
        try:
            tmp.replace(key) if not key.exists() else tmp.unlink()
        except OSError:
            tmp.unlink(missing_ok=True)
    monkeypatch.delenv("WALNUT_APPROVAL_KEY", raising=False)
    monkeypatch.setenv("WALNUT_APPROVAL_KEY_FILE", str(key))
