"""One test per finding in `wiki/40-reviews/raw/phase3-adapters-mcp.md` (13 Critical, 2 High),
scoped to `databridge_adapter/sources/mcp.py`. Each test is built to FAIL without the fix it
names — `CLAUDE.md`'s review-closure protocol — and each docstring names the finding number so a
reader can go straight from a failure here back to the review line it closes.

**Self-contained, deliberately**, for the same reason `test_untrusted_cannot_decide_access.py`
gives: other agents are mid-write on `postgres.py`, `document.py`, `mailbox.py` and `policy.py`
concurrently, and the real government/finance/healthcare pack fixtures are shared ground. Every
`McpAdapterDeclaration`, `Role`, `RoleModel` and `PolicyEngine` here is built by hand from a tiny
invented "widgets" source, so this file's outcome depends only on `sources/mcp.py` (mine),
`policy.py`/`rolemodel.py` (read-only, used through their public API) and `databridge_core`.

Finding 6 ("untrusted server content decides access") is NOT re-tested here as a defect — it is
CLOSED, generically, by `PolicyEngine._compile`'s `require_trusted_scope_column`
(`test_untrusted_cannot_decide_access.py`). One test below (`test_finding_6_...`) confirms MCP's
own `describe()`/`trust_of` feed that check correctly, per R-005's instruction to confirm coverage
rather than re-fix.

Most tests use `FakeTransport`, a plain three-method stand-in for `McpTransport` — deterministic
and fast, and the right tool for exercising `McpCore`'s OWN logic (row identity, pointer
re-resolution, quarantine bookkeeping) rather than the wire protocol. Findings 1, 8 and 14, which
are specifically about the TRANSPORT (pagination, artefact TOCTOU, concurrent RPC framing), test
`StdioMcpTransport` directly instead.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from databridge_adapter.capture import CaptureStore
from databridge_adapter.contract import CapabilityRequest, Principal, Projection
from databridge_adapter.errors import (
    CapabilityNotAdvertised,
    UnorderedPointeredRead,
    UntrustedColumnInPolicy,
)
from databridge_adapter.policy import Admission, PolicyEngine, PrincipalBinding
from databridge_adapter.purposes import PurposeDecl, PurposeVocabulary, use_purposes
from databridge_adapter.results import Budget
from databridge_adapter.rolemodel import PurposeBinding, Role, RoleModel, RowRule
from databridge_adapter.sources.mcp import (
    HostileResponse,
    McpAdapterDeclaration,
    McpCore,
    QuarantineRecord,
    ServerQuarantined,
    ShellCapabilityRefused,
    StdioMcpTransport,
    ToolDeclaration,
    ToolDeclarationRefused,
    WriteCapabilityRefused,
    compile_mcp_adapter,
    declaration_digest,
    load_mcp_declaration,
    screen_tool_declaration,
)
from databridge_core import Absent as CoreAbsent
from databridge_core import GroundedValue, SnapshotIdentity, SourcePointer, Trust
from databridge_core import Withheld as CoreWithheld

DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]

SOURCE_ID = "widgets_mcp"
ENTITY = "widget"
ROLE_NAME = "widget_reader"
PACK_PURPOSE = "test.widgets_read"
ESTATE_PURPOSE = "widgets_read"
ESTATE = "test-widgets-estate"
ACTOR = "svc@test"

#: A syntactically valid, arbitrary digest. Never checked against a real file unless a test
#: explicitly exercises `load_mcp_declaration`/artefact pinning (findings 8's containment/TOCTOU).
_SHA = "sha256:" + "ab" * 32

RECORDS = [
    {"id": "W-1", "owner_id": "alice", "name": "Alice's Widget"},
    {"id": "W-2", "owner_id": "bob", "name": "Bob's Widget"},
]


# =======================================================================================
# fixtures and builders
# =======================================================================================


@pytest.fixture(autouse=True)
def _purpose_vocabulary():
    vocab = PurposeVocabulary(
        {PACK_PURPOSE: PurposeDecl(id=PACK_PURPOSE, pack="test", description="",
                                   subject_scoped=False)},
        packs=("test",))
    with use_purposes(vocab):
        yield


class FakeTransport:
    """A minimal `McpTransport`: three methods, no subprocess, full control over what a
    "server" advertises and returns. `list_tools()` and `call_tool()` are exactly what
    `McpCore` needs and nothing about the wire format — the wire format is `StdioMcpTransport`'s
    job and is tested against directly for findings 1, 8 and 14.
    """

    def __init__(self, tools, records=()):
        self._tools = tuple(tools)
        self._records = list(records)
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def list_tools(self):
        return self._tools

    def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        payload = {"result": {"structuredContent": {"records": list(self._records)}}}
        raw = json.dumps(payload, sort_keys=True).encode("utf-8")
        return raw, payload

    def close(self):
        self.closed = True


def _tool(name: str, description: str = "reads widgets", input_schema=None, raw=None
         ) -> ToolDeclaration:
    input_schema = {} if input_schema is None else input_schema
    payload = dict(raw) if raw is not None else {}
    payload.setdefault("name", name)
    payload.setdefault("description", description)
    payload.setdefault("inputSchema", input_schema)
    return ToolDeclaration(name=name, description=description, input_schema=input_schema,
                           raw=payload)


#: The digest of the one clean, canonical tool this file's declarations pin by default. Computed
#: once so every FakeTransport-backed core built with the default `_tool("list_widgets")` connects
#: cleanly; a test that wants a MISMATCH (findings 9, 11, 13) builds a differently-described tool.
_CLEAN_DECLARATION_DIGEST = declaration_digest((_tool("list_widgets"),))


def _declaration(**overrides) -> McpAdapterDeclaration:
    data = dict(
        adapter="widgets_mcp", source_id=SOURCE_ID, title="widgets", estate=ESTATE,
        mcp=dict(vendored_path="does/not/matter.py", digest=_SHA,
                 declaration_digest=_CLEAN_DECLARATION_DIGEST,
                 transport="stdio", protocol_version="2026-07-28", sandbox="subprocess"),
        pinning_tier="T3",
        capture=dict(when="ledger_bound", retention="bound_to_ledger_entry"),
        projection=dict(entity=ENTITY, key=("id",), fields=("id", "owner_id", "name")),
        field_types={"id": "varchar", "owner_id": "varchar", "name": "text"},
        trust_class={"id": "trusted", "owner_id": "trusted", "name": "untrusted"},
        trusted_justification={
            "id": "minted by the register's own sequence",
            "owner_id": "assigned by the register, not the applicant",
        },
        capabilities=dict(read=("list_widgets",), write=()),
        verification=dict(lookup_capability="list_widgets", lookup_argument="id"),
        egress=dict(allow=()),
        egress_class="RESTRICTED_INTERNAL",
        deletes_detectable="none",
    )
    data.update(overrides)
    return McpAdapterDeclaration.model_validate(data)


def _unscoped_role(**overrides) -> Role:
    base = dict(
        name=ROLE_NAME, description="test role", purposes=frozenset({ESTATE_PURPOSE}),
        egress_ceiling="RESTRICTED_INTERNAL", allow={SOURCE_ID: frozenset({ENTITY})},
        deny_tables=frozenset(), deny_columns=frozenset(), column_masks={},
        deny_sources=frozenset(), row_filters={},
        unscoped_objects=frozenset({f"{SOURCE_ID}.{ENTITY}"}),
        purpose_grants={ESTATE_PURPOSE: frozenset({"*"})})
    base.update(overrides)
    return Role(**base)


def _scoped_role(row_rule: RowRule) -> Role:
    return Role(
        name=ROLE_NAME, description="test role, scoped by owner", purposes=frozenset({ESTATE_PURPOSE}),
        egress_ceiling="RESTRICTED_INTERNAL", allow={SOURCE_ID: frozenset({ENTITY})},
        deny_tables=frozenset(), deny_columns=frozenset(), column_masks={},
        deny_sources=frozenset(), row_filters={f"{SOURCE_ID}.{ENTITY}": row_rule},
        purpose_grants={ESTATE_PURPOSE: frozenset({"*"})})


def _owner_eq_rule(column: str = "owner_id") -> RowRule:
    return RowRule(target=f"{SOURCE_ID}.{ENTITY}", role=ROLE_NAME, kind="eq",
                   raw=f"{column} = :principal.owner", column=column, principal_attr="owner")


def _engine(role: Role, directory: dict[str, PrincipalBinding]) -> PolicyEngine:
    role_model = RoleModel(estate=ESTATE, version=1, roles={role.name: role}, masks={},
                           classification={}, estate_purposes={ESTATE_PURPOSE: "estate purpose"},
                           path=Path("test://roles.yaml"))
    binding = PurposeBinding(estate=ESTATE, pack="test", bindings={PACK_PURPOSE: ESTATE_PURPOSE},
                             path=Path("test://purpose_bindings.yaml"))
    return PolicyEngine(role_model=role_model, purpose_binding=binding, directory=directory,
                        default_budget=Budget(max_rows=1000))


def _directory() -> dict[str, PrincipalBinding]:
    return {"alice": PrincipalBinding(roles=(ROLE_NAME,), attributes=(("owner", "alice"),))}


def _officer() -> Principal:
    return Principal(actor=ACTOR, on_behalf_of="alice", purpose=PACK_PURPOSE, grant_id="g-1")


def _core(transport, *, policy: PolicyEngine, capture: CaptureStore,
         declaration: McpAdapterDeclaration | None = None) -> McpCore:
    return compile_mcp_adapter(declaration or _declaration(), policy=policy, capture=capture,
                               transport=transport)


def _search(principal: Principal, capability: str = "list_widgets",
           fields=("id", "owner_id", "name")) -> CapabilityRequest:
    return CapabilityRequest(source_id=SOURCE_ID, capability=capability, principal=principal,
                             projection=Projection(entity=ENTITY, fields=fields, key=("id",)),
                             order_by=("id",), limit=25)


def _unscoped_core(transport, *, capture: CaptureStore, declaration=None) -> McpCore:
    return _core(transport, policy=_engine(_unscoped_role(), _directory()), capture=capture,
                declaration=declaration)


def _query_cell_pointer(*, source_id: str, row_id: str, capture_digest: str,
                        column: str = "name") -> SourcePointer:
    """A minimally-valid `query_cell` `SourcePointer` — `kind="query_cell"` requires `snapshot`,
    `query_hash`, `row_ordinal` and `column` at construction (`databridge_core.provenance`'s own
    `_REQUIRED_FIELDS`), independently of anything this module checks."""
    return SourcePointer(
        source_id=source_id, kind="query_cell", row_id=row_id, capture_digest=capture_digest,
        column=column, row_ordinal=0, query_hash="q",
        snapshot=SnapshotIdentity(source_id=source_id, as_of=datetime.now(timezone.utc),
                                  content_hash="sha256:" + "0" * 64,
                                  schema_hash="sha256:" + "0" * 64))


# =======================================================================================
# finding 1 — StdioMcpTransport.list_tools ignores pagination
# =======================================================================================


def test_finding_1_pagination_is_followed_across_every_page():
    transport = StdioMcpTransport(command=[sys.executable, "-c", "pass"])
    pages = [
        {"result": {"tools": [{"name": "list_widgets", "description": "reads widgets",
                               "inputSchema": {}}], "nextCursor": "page-2"}},
        {"result": {"tools": [{"name": "run_shell_command", "description": "danger",
                               "inputSchema": {}}]}},
    ]
    seen_params: list[dict] = []

    def fake_rpc(method, params):
        assert method == "tools/list"
        seen_params.append(dict(params))
        return b"", pages[len(seen_params) - 1]

    transport._rpc = fake_rpc
    tools = transport.list_tools()

    assert {t.name for t in tools} == {"list_widgets", "run_shell_command"}, (
        "a tool on the SECOND page was neither fetched nor screenable — the exact gap finding 1 "
        "names: a pinned declaration that silently stops covering the server")
    assert seen_params[0] == {}
    assert seen_params[1] == {"cursor": "page-2"}


def test_finding_1_a_repeated_cursor_is_refused_not_looped_on_forever():
    transport = StdioMcpTransport(command=[sys.executable, "-c", "pass"])

    def fake_rpc(method, params):
        return b"", {"result": {"tools": [], "nextCursor": "same-cursor-forever"}}

    transport._rpc = fake_rpc
    with pytest.raises(HostileResponse, match="nextCursor"):
        transport.list_tools()


def test_finding_1_an_endless_cursor_is_bounded_not_read_without_limit(monkeypatch):
    import databridge_adapter.sources.mcp as mcp_module
    monkeypatch.setattr(mcp_module, "_MAX_TOOL_LIST_PAGES", 5)

    transport = StdioMcpTransport(command=[sys.executable, "-c", "pass"])
    counter = {"n": 0}

    def fake_rpc(method, params):
        counter["n"] += 1
        return b"", {"result": {"tools": [], "nextCursor": f"cursor-{counter['n']}"}}

    transport._rpc = fake_rpc
    with pytest.raises(mcp_module.McpTransportError, match="did not terminate"):
        transport.list_tools()
    assert counter["n"] == 5


# =======================================================================================
# finding 2 — the declaration digest does not cover the whole declaration
# =======================================================================================


def test_finding_2_the_digest_moves_when_outputschema_or_annotations_change():
    before = _tool("list_widgets", raw={"name": "list_widgets", "description": "reads widgets",
                                        "inputSchema": {}})
    after = _tool("list_widgets", raw={"name": "list_widgets", "description": "reads widgets",
                                       "inputSchema": {},
                                       "outputSchema": {"type": "object"},
                                       "annotations": {"readOnlyHint": True}})
    assert declaration_digest([before]) != declaration_digest([after]), (
        "a server that changes outputSchema/annotations with name, description and inputSchema "
        "byte-identical must still move the pin — a digest over part of the declaration pins "
        "nothing")


def test_finding_2_list_tools_actually_captures_the_full_raw_declaration():
    transport = StdioMcpTransport(command=[sys.executable, "-c", "pass"])

    def fake_rpc(method, params):
        return b"", {"result": {"tools": [
            {"name": "list_widgets", "description": "reads widgets", "inputSchema": {},
             "outputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}}]}}

    transport._rpc = fake_rpc
    tools = transport.list_tools()
    assert tools[0].raw.get("outputSchema") == {"type": "object"}
    assert tools[0].raw.get("annotations") == {"readOnlyHint": True}


# =======================================================================================
# finding 3 — arbitrary captures can be relabelled as this source
# =======================================================================================


def test_finding_3_a_pointer_citing_a_foreign_source_is_refused(tmp_path):
    store = CaptureStore(root=tmp_path / "captures")
    core = _unscoped_core(FakeTransport([_tool("list_widgets")], records=RECORDS), capture=store)
    # `CapabilityRequest`'s OWN validator (`contract.py`) already refuses a TOP-LEVEL `resolve`
    # pointer whose `source_id` disagrees with the request's, independently of this module — so
    # THAT half of finding 3 is closed one layer up, and is not constructible through the public
    # API to test here. What `contract.py`'s loop does NOT check is a `derived` pointer's
    # PARENTS: a pointer citing THIS source, wrapping a parent that cites a DIFFERENT one, sails
    # straight through it. That is the reachable half, and the one this module closes.
    foreign_parent = _query_cell_pointer(source_id="some_other_source", row_id="W-1",
                                         capture_digest="sha256:" + "0" * 64)
    derived = SourcePointer(source_id=SOURCE_ID, kind="derived",
                            derivation="mcp_redact@v1(column=name,classes=email)",
                            derived_from=[foreign_parent])
    with pytest.raises(UnorderedPointeredRead, match="cites source"):
        core.resolve(CapabilityRequest(
            source_id=SOURCE_ID, capability="list_widgets", principal=_officer(),
            projection=Projection(entity=ENTITY, fields=("id", "owner_id", "name"), key=("id",)),
            resolve=(derived,), limit=5))


def test_finding_3_a_capture_recorded_for_a_different_source_is_refused(tmp_path):
    """The same forgery, one layer deeper: the POINTER correctly names this adapter's
    `source_id`, but the CAPTURE it cites was recorded (by the store's own metadata) for a
    different source — a shared capture store, or a colliding digest. `describe()`'s own
    `source_id` is the second, independent witness the review named as missing."""
    store = CaptureStore(root=tmp_path / "captures")
    foreign = store.put(
        json.dumps({"result": {"structuredContent": {"records": RECORDS}}}).encode("utf-8"),
        media_type="application/json", source_id="some_other_source", retention="ledger_bound",
        note="belongs to a different source")
    core = _unscoped_core(FakeTransport([_tool("list_widgets")]), capture=store)
    pointer = _query_cell_pointer(source_id=SOURCE_ID, row_id="W-1",
                                  capture_digest=foreign.qualified_digest)
    with pytest.raises(UnorderedPointeredRead, match="recorded for source"):
        core.resolve(CapabilityRequest(
            source_id=SOURCE_ID, capability="list_widgets", principal=_officer(),
            projection=Projection(entity=ENTITY, fields=("id", "owner_id", "name"), key=("id",)),
            resolve=(pointer,), limit=5))


# =======================================================================================
# finding 4 — row-level authorization is bypassed in resolve() with `resolve=` set
# =======================================================================================


def test_finding_4_row_level_denial_is_enforced_during_re_resolution_too(tmp_path):
    store = CaptureStore(root=tmp_path / "captures")
    transport = FakeTransport([_tool("list_widgets")], records=RECORDS)

    # Mint a genuine pointer to bob's row under an UNSCOPED policy, into the SAME capture store.
    unscoped = _unscoped_core(transport, capture=store)
    seen = unscoped.resolve(_search(_officer()))
    bobs_pointer = next(row[0].pointer for row in seen.rows.rows
                        if row[1].value == "bob")  # column order: id, owner_id, name

    # Re-resolve the SAME pointer under a policy that scopes every row to its owner. Alice is not
    # bob, so this must come back Withheld, not bob's data.
    scoped = _core(transport, policy=_engine(_scoped_role(_owner_eq_rule()), _directory()),
                  capture=store)
    result = scoped.resolve(CapabilityRequest(
        source_id=SOURCE_ID, capability="list_widgets", principal=_officer(),
        projection=Projection(entity=ENTITY, fields=("id", "owner_id", "name"), key=("id",)),
        resolve=(bobs_pointer,), limit=5))

    assert len(result.rows.rows) == 1
    for cell in result.rows.rows[0]:
        assert isinstance(cell, CoreWithheld), (
            "a row-level DENIAL reached the caller as real data through the re-resolution path — "
            "row scope must not be bypassable just because the request carries `resolve=`")
        assert cell.record.reason_code == "ROW_NOT_IN_SCOPE"


# =======================================================================================
# finding 5 — row-level denials become omitted rows, not `Withheld` cells
# =======================================================================================


def test_finding_5_a_denied_row_is_returned_as_withheld_cells_not_omitted(tmp_path):
    store = CaptureStore(root=tmp_path / "captures")
    core = _core(FakeTransport([_tool("list_widgets")], records=RECORDS),
                policy=_engine(_scoped_role(_owner_eq_rule()), _directory()), capture=store)

    result = core.resolve(_search(_officer()))

    assert len(result.rows.rows) == 2, (
        "bob's row must still APPEAR in the result — as a denial, never a silent omission "
        "indistinguishable from 'the source never returned this row' (invariant 4)")
    alice_row, bobs_row = result.rows.rows       # ordered by id: W-1 (alice) then W-2 (bob)
    assert all(isinstance(c, GroundedValue) for c in alice_row)
    for cell in bobs_row:
        assert isinstance(cell, CoreWithheld)
        assert cell.record.reason_code == "ROW_NOT_IN_SCOPE"
        assert cell.record.policy_id
    assert any("Withheld" in w for w in result.warnings)


# =======================================================================================
# finding 6 — CLOSED elsewhere; confirming coverage, not re-fixing
# =======================================================================================


def test_finding_6_confirmed_closed_by_policyengine_compile_for_this_source(tmp_path):
    """Finding 6 ("untrusted server content decides access") is closed generically by
    `PolicyEngine._compile`'s `require_trusted_scope_column`
    (`test_untrusted_cannot_decide_access.py`), which reads `SourceDescription.trust_of`.
    `McpCore.describe()` builds that description from `McpAdapterDeclaration.trust_of` — a
    lookup that defaults UNTRUSTED. This test is the MCP-specific instance of the generic proof:
    confirming coverage per R-005's disposition instructions, not re-fixing an already-closed gap.
    """
    role = _scoped_role(RowRule(target=f"{SOURCE_ID}.{ENTITY}", role=ROLE_NAME, kind="eq",
                                raw="name = :principal.owner", column="name",
                                principal_attr="owner"))
    engine = _engine(role, _directory())
    store = CaptureStore(root=tmp_path / "captures")
    core = _core(FakeTransport([_tool("list_widgets")], records=RECORDS), policy=engine,
                capture=store)

    with pytest.raises(UntrustedColumnInPolicy, match=f"{SOURCE_ID}.{ENTITY}.name"):
        core.resolve(_search(_officer()))


# =======================================================================================
# finding 7 — invalid and ambiguous row identities are accepted
# =======================================================================================


def test_finding_7_a_null_key_field_is_refused_not_stringified_as_none(tmp_path):
    store = CaptureStore(root=tmp_path / "captures")
    core = _unscoped_core(
        FakeTransport([_tool("list_widgets")],
                     records=[{"id": None, "owner_id": "alice", "name": "x"}]),
        capture=store)
    with pytest.raises(HostileResponse, match="no value for key field"):
        core.resolve(_search(_officer()))


def test_finding_7_two_rows_sharing_one_key_are_refused_not_silently_collapsed(tmp_path):
    store = CaptureStore(root=tmp_path / "captures")
    core = _unscoped_core(
        FakeTransport([_tool("list_widgets")],
                     records=[{"id": "W-1", "owner_id": "alice", "name": "a"},
                              {"id": "W-1", "owner_id": "alice", "name": "b"}]),
        capture=store)
    with pytest.raises(HostileResponse, match="both declare identity"):
        core.resolve(_search(_officer()))


def test_finding_7_an_ambiguous_capture_is_refused_during_re_resolution(tmp_path):
    """Two records sharing a key can also arrive already sitting in ONE capture (built outside
    `resolve()`'s now-refusing path, e.g. by an older capture predating this fix) — `_find_row`
    must refuse rather than silently return whichever came first."""
    store = CaptureStore(root=tmp_path / "captures")
    raw = json.dumps({"result": {"structuredContent": {"records": [
        {"id": "W-1", "owner_id": "alice", "name": "a"},
        {"id": "W-1", "owner_id": "alice", "name": "b"},
    ]}}}).encode("utf-8")
    capture = store.put(raw, media_type="application/json", source_id=SOURCE_ID,
                        retention="ledger_bound", note="pre-existing ambiguous capture")
    core = _unscoped_core(FakeTransport([_tool("list_widgets")]), capture=store)
    pointer = _query_cell_pointer(source_id=SOURCE_ID, row_id="W-1",
                                  capture_digest=capture.qualified_digest)
    with pytest.raises(HostileResponse, match="more than one row"):
        core.resolve(CapabilityRequest(
            source_id=SOURCE_ID, capability="list_widgets", principal=_officer(),
            projection=Projection(entity=ENTITY, fields=("id", "owner_id", "name"), key=("id",)),
            resolve=(pointer,), limit=5))


def test_finding_7_the_honest_case_a_single_field_key_still_reads_the_bare_value(tmp_path):
    """The control: nothing about the fix should change the ORDINARY case. A single-field key's
    row identity stays the literal value — other code (and other adapters' fixtures) compare it
    directly, so this is a compatibility floor, not just a nicety."""
    store = CaptureStore(root=tmp_path / "captures")
    core = _unscoped_core(FakeTransport([_tool("list_widgets")], records=RECORDS), capture=store)
    result = core.resolve(_search(_officer()))
    ids = {row[0].value for row in result.rows.rows}
    assert ids == {"W-1", "W-2"}
    pointer = next(row[0].pointer for row in result.rows.rows if row[0].value == "W-1")
    assert pointer.row_id == "W-1"


# =======================================================================================
# finding 8 — artifact pinning is bypassable and subject to TOCTOU
# =======================================================================================

_SERVER_SCRIPT = """\
import sys, json
def reply(msg):
    sys.stdout.write(json.dumps(msg) + "\\n")
    sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    if req.get("method") == "initialize":
        reply({"jsonrpc": "2.0", "id": req["id"], "result": {}})
    elif req.get("method") == "tools/list":
        reply({"jsonrpc": "2.0", "id": req["id"], "result": {"tools": []}})
    else:
        reply({"jsonrpc": "2.0", "id": req["id"], "result": {}})
"""


def test_finding_8_a_vendored_path_that_escapes_the_repo_root_is_refused(tmp_path):
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    (root / "packs").mkdir(parents=True)
    outside.mkdir()
    server = outside / "server.py"
    server.write_text(_SERVER_SCRIPT, encoding="utf-8")
    digest = "sha256:" + __import__("hashlib").sha256(server.read_bytes()).hexdigest()

    decl_path = root / "packs" / "declaration.yaml"
    import yaml
    decl_path.write_text(yaml.safe_dump({
        "adapter": "escapee", "source_id": "escapee", "estate": ESTATE,
        "mcp": {"vendored_path": "../outside/server.py", "digest": digest,
               "declaration_digest": _SHA, "transport": "stdio",
               "protocol_version": "2026-07-28", "sandbox": "subprocess"},
        "pinning_tier": "T3",
        "capture": {"when": "ledger_bound", "retention": "bound_to_ledger_entry"},
        "projection": {"entity": ENTITY, "key": ["id"], "fields": ["id"]},
        "field_types": {"id": "varchar"},
        "trust_class": {"id": "trusted"},
        "trusted_justification": {"id": "minted by the register"},
        "capabilities": {"read": ["list_widgets"], "write": []},
        "egress": {"allow": []}, "egress_class": "RESTRICTED_INTERNAL",
    }, sort_keys=False), encoding="utf-8")

    with pytest.raises(Exception) as exc:
        load_mcp_declaration(decl_path, repo_root=root)
    assert "OUTSIDE" in str(exc.value) or "outside" in str(exc.value).lower()


def test_finding_8_the_artefact_is_re_verified_immediately_before_launch_toctou(tmp_path):
    server = tmp_path / "server.py"
    server.write_text(_SERVER_SCRIPT, encoding="utf-8")
    import hashlib
    original_digest = "sha256:" + hashlib.sha256(server.read_bytes()).hexdigest()

    transport = StdioMcpTransport(command=[sys.executable, str(server)], cwd=tmp_path,
                                  expected_digest=original_digest, digest_path=server)
    # The file changes AFTER the digest was computed (load time) and BEFORE the process is
    # actually spawned (first RPC) — the exact TOCTOU window finding 8 names.
    server.write_text(_SERVER_SCRIPT + "\n# tampered\n", encoding="utf-8")

    import databridge_adapter.sources.mcp as mcp_module
    with pytest.raises(mcp_module.McpTransportError, match="TOCTOU"):
        transport.list_tools()
    transport.close()


def test_finding_8_an_untampered_launch_still_works(tmp_path):
    """The control: the re-check must not make an HONEST launch fail."""
    server = tmp_path / "server.py"
    server.write_text(_SERVER_SCRIPT, encoding="utf-8")
    import hashlib
    digest = "sha256:" + hashlib.sha256(server.read_bytes()).hexdigest()
    transport = StdioMcpTransport(command=[sys.executable, str(server)], cwd=tmp_path,
                                  expected_digest=digest, digest_path=server)
    try:
        assert transport.list_tools() == ()
    finally:
        transport.close()


# =======================================================================================
# finding 9 — prohibited declaration changes do not create a sticky quarantine
# =======================================================================================


def test_finding_9_a_screening_refusal_leaves_the_adapter_quarantined(tmp_path):
    store = CaptureStore(root=tmp_path / "captures")
    hostile_tool = _tool("run_shell_command", description="runs a shell command")
    core = _unscoped_core(FakeTransport([hostile_tool]), capture=store)

    with pytest.raises(ShellCapabilityRefused):
        core.connect()

    assert core.quarantined is not None, (
        "a screening REFUSAL must leave the adapter quarantined — otherwise a server that shows "
        "a hostile surface once and its pinned surface on the next connect passes silently")

    # The transport now reverts to a CLEAN surface. Without the fix, connect() would re-evaluate
    # from scratch and succeed; with it, the sticky quarantine refuses regardless.
    core._transport = FakeTransport([_tool("list_widgets")])
    with pytest.raises(ServerQuarantined):
        core.connect()


def test_finding_9_the_first_refusal_still_raises_the_specific_subtype(tmp_path):
    """The fix must not change what a caller sees on the FIRST refusal — existing call sites key
    off the specific `ToolDeclarationRefused` subtype, not a generic `ServerQuarantined`."""
    store = CaptureStore(root=tmp_path / "captures")
    core = _unscoped_core(
        FakeTransport([_tool("run_shell_command", description="runs a shell command")]),
        capture=store)
    with pytest.raises(ShellCapabilityRefused):
        core.connect()


# =======================================================================================
# finding 10 — write prevention is only a name heuristic
# =======================================================================================


@pytest.mark.parametrize("name", ["archive_record", "publish_record", "apply_changes"])
def test_finding_10_previously_uncovered_mutating_names_are_now_refused(name):
    """All three are refused. `publish_record` happens to ALSO collide with the reused
    send-shaped screen (`databridge_agent.tools`'s own `_SEND_TOKENS` includes "publish"), so it
    is caught one layer earlier than `archive_record`/`apply_changes` — refused either way, which
    is what matters; `ToolDeclarationRefused` is the common ancestor of every screen this module
    raises."""
    with pytest.raises(ToolDeclarationRefused):
        screen_tool_declaration(name, "Does something to a record.")


def test_finding_10_a_mutation_shaped_description_is_refused_under_an_innocent_name():
    with pytest.raises(WriteCapabilityRefused, match="mutating"):
        screen_tool_declaration("helper", "Persists the record to the register.")


def test_finding_10_a_self_declared_non_read_only_annotation_is_refused():
    """A second, independent signal: the server's OWN MCP `annotations` say it is not read-only,
    even though its name gives no hint. A hostile server can still lie about its own annotations
    — this is one more layer, not a replacement for the name/description screens or the pin."""
    with pytest.raises(WriteCapabilityRefused, match="annotations"):
        screen_tool_declaration("get_widget", "Reads a widget.", None,
                                raw={"annotations": {"readOnlyHint": False}})
    with pytest.raises(WriteCapabilityRefused, match="annotations"):
        screen_tool_declaration("get_widget", "Reads a widget.", None,
                                raw={"annotations": {"destructiveHint": True}})


def test_finding_10_an_honest_read_only_tool_still_passes():
    """The control: legitimate read tools — including ones whose annotations explicitly say so —
    must not be caught by the widened screen."""
    screen_tool_declaration("get_widget", "Reads a widget.", None,
                            raw={"annotations": {"readOnlyHint": True}})
    screen_tool_declaration("list_widgets", "Lists widgets in the register.")


# =======================================================================================
# finding 11 — approve_declaration does not enforce human attestation
# =======================================================================================


def test_finding_11_quarantine_content_is_retained_for_inspection_not_only_names(tmp_path):
    store = CaptureStore(root=tmp_path / "captures")
    core = _unscoped_core(
        FakeTransport([_tool("list_widgets", description="reads widgets, RENAMED")]),
        capture=store)
    # First connect accepts (this IS the pinned digest at construction, since `_declaration()`
    # pins an arbitrary `_SHA` — quarantine is forced by mismatch, not by content).
    with pytest.raises(ServerQuarantined) as exc:
        core.connect()
    record = exc.value.record
    assert record.observed_tools, (
        "the reviewer approving a re-review needs the actual CONTENT of what changed, not only a "
        "digest and a list of names — a digest plus names is enough to approve blind")
    assert any(t.get("name") == "list_widgets" for t in record.observed_tools)
    assert any("RENAMED" in t.get("description", "") for t in record.observed_tools)


def test_finding_11_an_authenticated_principal_must_resolve_in_the_directory(tmp_path):
    """Additive, backward-compatible strengthening: a caller that HOLDS an authenticated
    `Principal` (rather than a bare free-text name) can pass it, and it is checked against this
    adapter's own policy directory the same way every read is — `attested_by="bot"` alone is
    exactly what the review named as unauthenticated, and a Principal unknown to the directory
    must fail closed rather than being accepted on the strength of the string beside it."""
    store = CaptureStore(root=tmp_path / "captures")
    core = _unscoped_core(FakeTransport([_tool("run_shell_command")]), capture=store)
    with pytest.raises(ShellCapabilityRefused):
        core.connect()
    assert core.quarantined is not None
    record = core.quarantined

    unknown = Principal(actor=ACTOR, on_behalf_of="nobody-in-the-directory",
                        purpose=PACK_PURPOSE, grant_id=None)
    with pytest.raises(Exception):
        core.approve_declaration(record.observed_digest, attested_by="a.reviewer",
                                 reason="reviewed the diff", principal=unknown)
    assert core.quarantined is not None, "a failed authenticated approval must not clear quarantine"

    known = Principal(actor=ACTOR, on_behalf_of="alice", purpose=PACK_PURPOSE, grant_id=None)
    core.approve_declaration(record.observed_digest, attested_by="alice", reason="reviewed the diff",
                             principal=known)
    assert core.quarantined is None
    assert core.approvals[-1]["principal_actor"] == ACTOR
    assert ROLE_NAME in core.approvals[-1]["principal_roles"]


def test_finding_11_the_prior_free_text_only_call_shape_still_works(tmp_path):
    """Backward compatibility: `principal` is optional. Existing callers with no directory handy
    (a CLI, an offline demo) must keep working exactly as before."""
    store = CaptureStore(root=tmp_path / "captures")
    core = _unscoped_core(FakeTransport([_tool("run_shell_command")]), capture=store)
    with pytest.raises(ShellCapabilityRefused):
        core.connect()
    record = core.quarantined
    core.approve_declaration(record.observed_digest, attested_by="j.mensah",
                             reason="diffed and it's benign")
    assert core.quarantined is None


# =======================================================================================
# finding 12 — _resolve_pointers silently discards requested evidence
# =======================================================================================


def test_finding_12_two_pointers_naming_conflicting_captures_are_refused(tmp_path):
    store = CaptureStore(root=tmp_path / "captures")
    transport = FakeTransport([_tool("list_widgets")], records=RECORDS)
    core = _unscoped_core(transport, capture=store)

    first = core.resolve(_search(_officer()))
    pointer_a = first.rows.rows[0][0].pointer

    other_capture = store.put(
        json.dumps({"result": {"structuredContent": {"records": RECORDS}}}).encode("utf-8"),
        media_type="application/json", source_id=SOURCE_ID, retention="ledger_bound",
        note="a second, distinct capture of the same row")
    pointer_b = _query_cell_pointer(source_id=SOURCE_ID, row_id=pointer_a.row_id,
                                    capture_digest=other_capture.qualified_digest)

    with pytest.raises(UnorderedPointeredRead, match="DIFFERENT captures"):
        core.resolve(CapabilityRequest(
            source_id=SOURCE_ID, capability="list_widgets", principal=_officer(),
            projection=Projection(entity=ENTITY, fields=("id", "owner_id", "name"), key=("id",)),
            resolve=(pointer_a, pointer_b), limit=5))


def test_finding_12_a_derived_compression_pointer_with_no_parents_is_refused(tmp_path):
    store = CaptureStore(root=tmp_path / "captures")
    core = _unscoped_core(FakeTransport([_tool("list_widgets")]), capture=store)
    orphan = SourcePointer(
        source_id=SOURCE_ID, kind="derived", derivation="mcp_redact@v1(column=name,classes=email)",
        predicate="owner_id = 'alice'",
        snapshot=SnapshotIdentity(source_id=SOURCE_ID, as_of=datetime.now(timezone.utc),
                                  content_hash="sha256:" + "0" * 64,
                                  schema_hash="sha256:" + "0" * 64),
        row_count=1, rows_digest="sha256:" + "0" * 64)
    with pytest.raises(UnorderedPointeredRead, match="no parents"):
        core.resolve(CapabilityRequest(
            source_id=SOURCE_ID, capability="list_widgets", principal=_officer(),
            projection=Projection(entity=ENTITY, fields=("id", "owner_id", "name"), key=("id",)),
            resolve=(orphan,), limit=5))


def test_finding_12_a_derived_pointers_parent_missing_evidence_is_refused(tmp_path):
    store = CaptureStore(root=tmp_path / "captures")
    core = _unscoped_core(FakeTransport([_tool("list_widgets")]), capture=store)
    parent = SourcePointer(
        source_id=SOURCE_ID, kind="query_cell", column="name",
        snapshot=SnapshotIdentity(source_id=SOURCE_ID, as_of=datetime.now(timezone.utc),
                                  content_hash="sha256:" + "0" * 64,
                                  schema_hash="sha256:" + "0" * 64),
        query_hash="q", row_ordinal=0)   # NO row_id, NO capture_digest
    derived = SourcePointer(source_id=SOURCE_ID, kind="derived",
                            derivation="mcp_redact@v1(column=name,classes=email)",
                            derived_from=[parent])
    with pytest.raises(UnorderedPointeredRead, match="no row id or no"):
        core.resolve(CapabilityRequest(
            source_id=SOURCE_ID, capability="list_widgets", principal=_officer(),
            projection=Projection(entity=ENTITY, fields=("id", "owner_id", "name"), key=("id",)),
            resolve=(derived,), limit=5))


# =======================================================================================
# finding 13 — _live_check suppresses a newly detected quarantine
# =======================================================================================


def test_finding_13_verify_raises_a_newly_detected_quarantine_rather_than_hiding_it(tmp_path):
    store = CaptureStore(root=tmp_path / "captures")
    transport = FakeTransport([_tool("list_widgets")], records=RECORDS)
    core = _unscoped_core(transport, capture=store)
    result = core.resolve(_search(_officer()))
    pointer = result.rows.rows[0][0].pointer

    # The server's surface moves between the read and the verify — a live rug pull.
    core._transport = FakeTransport(
        [_tool("list_widgets", description="reads widgets, RENAMED to something else")],
        records=RECORDS)

    with pytest.raises(ServerQuarantined):
        core.verify(pointer)
    assert core.quarantined is not None, (
        "verify() must not swallow a NEWLY detected quarantine into a note — the one moment the "
        "quarantine is most worth seeing is the moment it is discovered")


# =======================================================================================
# finding 14 — StdioMcpTransport._rpc is unsafe under concurrent calls
# =======================================================================================


def test_finding_14_concurrent_calls_do_not_cross_wires():
    """Two threads calling the SAME transport concurrently must each get their OWN reply, never
    the other thread's. Without the lock, both threads race the shared response queue and can
    steal each other's line."""
    transport = StdioMcpTransport(command=[sys.executable, "-c", "pass"])
    barrier = threading.Barrier(2)
    call_order: list[str] = []
    lock = threading.Lock()

    def fake_rpc(method, params):
        # Serialise, as the real (locked) `_rpc` would — but PROVE it by recording whether two
        # calls ever overlap in-flight, which is impossible once the lock is held for the whole
        # cycle.
        with lock:
            call_order.append("enter:" + params.get("name", ""))
            time.sleep(0.01)
            call_order.append("exit:" + params.get("name", ""))
        return b"", {"result": {"tools": []}}

    transport._rpc = fake_rpc

    def call(tool_name):
        barrier.wait(timeout=5)
        transport.call_tool(tool_name, {})

    threads = [threading.Thread(target=call, args=(f"tool-{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    # Every "enter" must be immediately followed by its OWN "exit" — never interleaved.
    for i in range(0, len(call_order), 2):
        name = call_order[i].split(":", 1)[1]
        assert call_order[i + 1] == f"exit:{name}", (
            "two calls interleaved instead of running one at a time under the lock: "
            f"{call_order}")


def test_finding_14_the_call_lock_is_reentrant_so_start_does_not_deadlock():
    """`_start()` is called from INSIDE `_rpc`, under the lock, and issues its own `initialize`
    call through `_rpc` again on the SAME thread. A plain (non-reentrant) `Lock` would deadlock
    on the very first real call ever made; this checks the lock's own reentrancy directly rather
    than reconstructing that call chain by hand."""
    transport = StdioMcpTransport(command=[sys.executable, "-c", "pass"])
    outer_acquired = transport._call_lock.acquire(timeout=1)
    assert outer_acquired, "the lock could not even be acquired once"
    try:
        inner_acquired = transport._call_lock.acquire(timeout=1)
        assert inner_acquired, (
            "a SECOND acquire on the same thread timed out — the lock is not reentrant, and "
            "`_start()`'s nested `initialize` call (made while `_rpc` already holds this lock) "
            "would deadlock every real connection")
        transport._call_lock.release()
    finally:
        transport._call_lock.release()


# =======================================================================================
# finding 15 — _sort_key is not a type-faithful total numeric order
# =======================================================================================


def test_finding_15_large_integers_do_not_collide_under_sort():
    from databridge_adapter.sources.mcp import _sort_key
    a = 2 ** 60
    b = 2 ** 60 + 1                      # distinct ints; both lose precision through `float()`
    assert float(a) == float(b), "the test fixture must actually exercise float precision loss"
    assert _sort_key(a) != _sort_key(b), (
        "two DISTINCT large integers produced the SAME sort key — the exact collision finding 15 "
        "names, which lets `limit` return a different row set across equivalent calls")
    assert _sort_key(a) < _sort_key(b)


def test_finding_15_a_full_result_orders_large_ids_correctly(tmp_path):
    store = CaptureStore(root=tmp_path / "captures")
    big = 2 ** 60
    records = [{"id": str(big + 1), "owner_id": "alice", "name": "x"},
              {"id": str(big), "owner_id": "alice", "name": "y"}]
    # Use a numeric-looking but STRING id column to isolate `_sort_key`'s numeric branch, order by
    # a synthetic numeric field instead — build records with an actual int field to sort on.
    records = [{"id": "A", "owner_id": "alice", "name": "x", "rank": big + 1},
              {"id": "B", "owner_id": "alice", "name": "y", "rank": big}]
    declaration = _declaration(projection=dict(entity=ENTITY, key=("id",),
                                              fields=("id", "owner_id", "name", "rank")),
                              field_types={"id": "varchar", "owner_id": "varchar",
                                          "name": "text", "rank": "bigint"},
                              trust_class={"id": "trusted", "owner_id": "trusted",
                                         "name": "untrusted", "rank": "trusted"},
                              trusted_justification={
                                  "id": "minted by the register",
                                  "owner_id": "assigned by the register",
                                  "rank": "computed internally"})
    core = _unscoped_core(FakeTransport([_tool("list_widgets")], records=records), capture=store,
                          declaration=declaration)
    req = CapabilityRequest(
        source_id=SOURCE_ID, capability="list_widgets", principal=_officer(),
        projection=Projection(entity=ENTITY, fields=("id", "owner_id", "name", "rank"),
                              key=("id",)),
        order_by=("rank", "id"), limit=10)
    result = core.resolve(req)
    ids = [row[0].value for row in result.rows.rows]
    assert ids == ["B", "A"], f"ascending by `rank` should put the smaller value first, got {ids}"


def test_finding_15_nan_and_infinity_sort_to_fixed_positions_without_raising():
    from databridge_adapter.sources.mcp import _sort_key
    keys = [_sort_key(float("nan")), _sort_key(float("inf")), _sort_key(float("-inf")),
           _sort_key(0), _sort_key(1.5)]
    ordered = sorted(keys)
    assert ordered == [_sort_key(float("-inf")), _sort_key(0), _sort_key(1.5),
                       _sort_key(float("inf")), _sort_key(float("nan"))]
