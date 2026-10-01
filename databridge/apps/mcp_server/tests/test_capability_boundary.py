"""The read/write capability boundary between the two clinic MCP servers (R-34).

`REMEDIATION.md` §3.2 chose a self-hosted aggregator so that read tools and mutating tools can live
in different processes, and plans to register "our read-only clinic server" behind the endpoint an
agent reaches. This file is what makes "read-only" a claim that can be falsified rather than a
sentence in a header — which is exactly how R-34 got in: the header said read-only while
``brain_ingest`` sat in the same server.

The tests are built so they can fail on a tool **nobody has written yet**, which is the point of
`STATUS.md`'s "The ones worth never re-learning" item 7 — a test that only checks today's names
cannot fail:

* ``test_no_read_capability_mutates_the_graph`` runs every tool on the read list against a real
  seeded graph with a working ingest harness present, and compares a full SQLite dump before and
  after. Add a mutating tool to ``READ_CAPABILITIES`` and this goes red, whatever it is called.
* ``test_the_mutation_detector_sees_the_write_tool`` runs the same machinery over the write list
  and asserts the dump **does** change. If the detector ever stops detecting — a digest that
  ignores the tables, a seed that never loaded — the boundary test above would be vacuously green,
  and this one turns red instead.
* ``test_every_public_method_is_classified`` fails when a new public ``BrainTools`` method exists
  that nobody has put on a list. Undecided is not read-only.
* every argument is supplied from ``ARGUMENT_RECIPES`` by parameter name, and a read tool with a
  parameter that has no recipe fails the suite. So a new tool cannot be added to the read list and
  silently skipped by the checks that police it.
"""
from __future__ import annotations

import hashlib
import inspect
import re

import pytest

import brain_reader_server
import brain_writer_server
import capabilities
from brain_server import BrainTools
from capabilities import (
    READ_CAPABILITIES,
    UNEXPOSED_METHODS,
    WRITE_CAPABILITIES,
    Capability,
    CapabilityBoundaryError,
    ReadOnlyBrain,
    capability_names,
    registered_tool_names,
    seal_registry,
)
from databridge_evals.e_intake import FIXTURE_TEXT, gold_harness

MERIDIAN = "Meridian Life Assurance Pte Ltd"
DEMO_SOURCE = "demo_sample.txt"

# One value per parameter NAME, shared by every tool. Every parameter of every declared capability
# must appear here — see ``_arguments_for``. That is deliberate friction: adding a tool means
# deciding how it gets exercised, in the same commit.
ARGUMENT_RECIPES: dict[str, object] = {
    "question": "What does the WELL2 package include?",
    "as_of": "2026-08-12T00:00:00+00:00",
    "entity": MERIDIAN,
    "depth": 1,
    "from_entity": MERIDIAN,
    "to_entity": "Chest X-Ray",
    "source_path": DEMO_SOURCE,
    # brain_ingest reads its text from the registered source when this is None — the path a caller
    # over MCP takes, since the tool signature there exposes ``source_path`` only.
    "text": None,
}


def _tools() -> BrainTools:
    """A seeded BrainTools that *can* write.

    The harness and the registered source are essential: a read tool must be shown not to mutate a
    store where mutation was possible. Against a store with no harness, ``brain_ingest`` refuses
    and every "nothing changed" assertion below would hold for the wrong reason.
    """
    t = BrainTools(harness=gold_harness(), sources={DEMO_SOURCE: FIXTURE_TEXT})
    t.load_seed()
    return t


def _digest(tools: BrainTools) -> str:
    """A hash of the entire SQLite database — schema and every row of nodes, edges and sources.

    Not ``store.counts()``: a tool that rewrote a label, closed a version, or attached a pointer
    would leave the counts alone. The dump catches any byte of state that moved.
    """
    dump = "\n".join(tools.store.conn.iterdump())
    return hashlib.sha256(dump.encode("utf-8")).hexdigest()


def _arguments_for(method) -> dict:
    params = [p for name, p in inspect.signature(method).parameters.items()
              if name not in ("self",) and p.kind is not p.VAR_KEYWORD]
    missing = [p.name for p in params if p.name not in ARGUMENT_RECIPES]
    assert not missing, (
        f"{method.__qualname__} has parameters with no entry in ARGUMENT_RECIPES: {missing}. "
        f"Every declared capability must be callable from this file, or the boundary checks "
        f"quietly stop covering it.")
    return {p.name: ARGUMENT_RECIPES[p.name] for p in params}


def _invoke(tools: BrainTools, cap: Capability) -> object:
    return getattr(tools, cap.method)(**_arguments_for(getattr(tools, cap.method)))


# --- the manifest itself ------------------------------------------------------------

def test_there_are_capabilities_to_check():
    """Guards every test below: empty lists would make all of them vacuously green."""
    assert READ_CAPABILITIES and WRITE_CAPABILITIES
    assert len(capability_names(READ_CAPABILITIES)) == len(set(capability_names(READ_CAPABILITIES)))


def test_read_and_write_capabilities_are_disjoint():
    assert not (set(capability_names(READ_CAPABILITIES))
                & set(capability_names(WRITE_CAPABILITIES)))


def test_every_public_method_is_classified():
    """A new public method on ``BrainTools`` has to be put on a list before the suite goes green.

    Silence is how ``brain_ingest`` ended up behind a server calling itself read-only. This makes
    the next addition a decision someone records, not one nobody makes.
    """
    public = {name for name, member in inspect.getmembers(BrainTools, inspect.isfunction)
              if not name.startswith("_")}
    classified = (set(capability_names(READ_CAPABILITIES))
                  | {c.method for c in READ_CAPABILITIES}
                  | {c.method for c in WRITE_CAPABILITIES}
                  | set(UNEXPOSED_METHODS))
    assert public <= classified, (
        f"unclassified BrainTools methods: {sorted(public - classified)}. Add each to "
        f"READ_CAPABILITIES, WRITE_CAPABILITIES or UNEXPOSED_METHODS in capabilities.py.")


def test_the_manifest_names_methods_that_exist():
    for cap in (*READ_CAPABILITIES, *WRITE_CAPABILITIES):
        assert callable(getattr(BrainTools, cap.method, None)), cap


# --- the behavioural boundary -------------------------------------------------------

def test_no_read_capability_mutates_the_graph():
    """THE test. Every tool on the agent-reachable list, run against a writable seeded graph.

    Fails for any future tool that writes, no matter what it is named or how it is labelled —
    which is the property a name-checking test does not have.
    """
    tools = _tools()
    before = _digest(tools)
    for cap in READ_CAPABILITIES:
        out = _invoke(tools, cap)
        # A tool that errored out touched nothing and would pass the digest check for the wrong
        # reason. Each read has to have actually done its work.
        assert isinstance(out, dict) and "error" not in out, (cap.name, out)
        assert _digest(tools) == before, (
            f"{cap.name} is on READ_CAPABILITIES but changed the graph. Read tools are the ones an "
            f"agent can reach (R-34); move it to WRITE_CAPABILITIES and the writer process.")


def test_the_mutation_detector_sees_the_write_tool():
    """The twin of the test above: prove the digest actually detects a write.

    Without this, a digest that silently stopped covering the tables — or a seed that failed to
    load — would leave the boundary test green while checking nothing.
    """
    tools = _tools()
    before = _digest(tools)
    out = _invoke(tools, WRITE_CAPABILITIES[0])
    assert out["entities_added"] >= 1, out
    assert _digest(tools) != before, "brain_ingest wrote nothing — the detector proves nothing"


def test_the_digest_sees_a_write_that_adds_no_node_or_edge():
    """A pointer attached to an existing entity adds no node and no edge — and a boundary check
    watching the graph's shape would miss it. The dump does not."""
    from databridge_core import SourcePointer

    tools = _tools()
    before = _digest(tools)
    shape_before = (tools.store.counts()["nodes"], tools.store.counts()["edges"])
    tools.store.append_sources(tools.store.open_nodes()[0]["id"], [SourcePointer(
        source_id="capability_boundary_probe.txt", kind="doc_span",
        document_id="capability_boundary_probe.txt", char_start=0, char_end=1)])
    assert (tools.store.counts()["nodes"], tools.store.counts()["edges"]) == shape_before
    assert _digest(tools) != before


# --- the static second layer --------------------------------------------------------
#
# Shallow by design: it reads the bodies of the BrainTools methods a read capability reaches, not
# the graph package they call. The digest test above is what covers the depth; this one names the
# line, and catches a write that the recipe arguments happen not to trigger.

MUTATION_MARKERS = (
    r"\.add_node\(", r"\.add_edge\(", r"\.append_sources\(", r"\.replace_node\(",
    r"\.close_node\(", r"\.close_edge\(", r"\.upsert_node\(", r"\.upsert_edge\(",
    r"extract_graph\(", r"load_clinic_seed\(", r"\.load_seed\(", r"\.commit\(",
    r"\.executescript\(", r"\.executemany\(", r"\bINSERT\s+INTO\b", r"\bUPDATE\s+\w+\s+SET\b",
    r"\bDELETE\s+FROM\b", r"\bDROP\s+TABLE\b",
)
_MUTATION_RE = re.compile("|".join(MUTATION_MARKERS), re.I)


def _mutation_hits(source: str) -> list[str]:
    return [line.strip() for line in source.splitlines() if _MUTATION_RE.search(line)]


def _reachable_sources(method_name: str, seen: set[str] | None = None) -> dict[str, str]:
    """The method's source plus, transitively, every other ``BrainTools`` method it calls."""
    seen = seen if seen is not None else set()
    if method_name in seen or not hasattr(BrainTools, method_name):
        return {}
    seen.add(method_name)
    src = inspect.getsource(getattr(BrainTools, method_name))
    out = {method_name: src}
    for callee in set(re.findall(r"self\.(\w+)\(", src)):
        out.update(_reachable_sources(callee, seen))
    return out


def test_no_read_capability_reaches_a_mutating_call():
    for cap in READ_CAPABILITIES:
        for name, src in _reachable_sources(cap.method).items():
            assert not _mutation_hits(src), (
                f"{cap.name} reaches BrainTools.{name}, which mutates: {_mutation_hits(src)}")


@pytest.mark.parametrize("line", [
    "        self.store.add_node(node)",
    "        self.store.append_sources(nid, ptrs)",
    "        res = extract_graph(doc, self.harness, store=self.store)",
    "        self.store.conn.execute('DELETE FROM nodes')",
])
def test_the_static_scanner_catches_a_planted_write(line):
    """The scan above is only worth its runtime if it fires on what it claims to catch."""
    assert _mutation_hits(line), line


def test_the_static_scanner_ignores_prose():
    """Over-broadness would push people to stop writing about the rule in the file that keeps it."""
    assert not _mutation_hits('"""Never calls store.add_node; reads only."""\n# no INSERT here\n')


def test_the_write_capability_is_caught_by_the_static_scan_too():
    """Anchors the scan against the one method we know writes."""
    assert _mutation_hits(inspect.getsource(BrainTools.brain_ingest))


# --- the reader's attribute surface -------------------------------------------------

def test_the_read_only_view_exposes_exactly_the_read_capabilities():
    brain = ReadOnlyBrain(_tools())
    for cap in READ_CAPABILITIES:
        assert callable(getattr(brain, cap.name))
    assert set(dir(brain)) == set(capability_names(READ_CAPABILITIES)) | {"calls"}


@pytest.mark.parametrize("name", sorted(
    set(capability_names(WRITE_CAPABILITIES)) | set(UNEXPOSED_METHODS)))
def test_the_read_only_view_has_no_write_method_by_name(name):
    """Not "is not registered" — not present as an attribute at all."""
    brain = ReadOnlyBrain(_tools())
    with pytest.raises(AttributeError):
        getattr(brain, name)


def test_the_read_only_registry_cannot_be_extended_after_construction():
    """R-07's finding against packages/agent, applied here before it can happen."""
    brain = ReadOnlyBrain(_tools())
    tools = _tools()
    with pytest.raises(CapabilityBoundaryError):
        brain.brain_ingest = tools.brain_ingest
    with pytest.raises(TypeError):
        brain.calls["brain_ingest"] = tools.brain_ingest   # the mapping is a read-only proxy
    with pytest.raises(AttributeError):
        getattr(brain, "brain_ingest")


def test_the_read_only_view_still_runs_the_same_shared_implementation():
    """No logic fork: the view delegates to BrainTools, it does not reimplement it."""
    tools = _tools()
    brain = ReadOnlyBrain(tools)
    assert brain.brain_query("What does the WELL2 package include?")["answer"] == \
        tools.brain_query("What does the WELL2 package include?")["answer"]


def test_a_view_over_an_object_missing_a_read_method_refuses_construction():
    class Partial:
        def brain_query(self, question, as_of=None):
            return {}

    with pytest.raises(CapabilityBoundaryError):
        ReadOnlyBrain(Partial())


# --- the two processes' registries --------------------------------------------------
#
# The SDK is an optional dependency, so these skip without it — but as a DECORATOR, never a
# module-level ``importorskip``: that skips the whole file, and the checks above (which need no
# SDK) are the ones that must never be able to disappear quietly.

try:
    import mcp as _mcp_sdk                       # noqa: F401  (probe only)
    HAVE_MCP_SDK = True
except ImportError:                              # pragma: no cover — SDK is installed here
    HAVE_MCP_SDK = False

needs_sdk = pytest.mark.skipif(not HAVE_MCP_SDK, reason="the MCP SDK is not installed")


@needs_sdk
def test_the_reader_server_registers_exactly_the_read_allow_list():
    server = brain_reader_server.build_reader_server(
        brain_reader_server.build_reader_brain(_tools()))
    assert registered_tool_names(server) == set(capability_names(READ_CAPABILITIES))


@needs_sdk
def test_no_write_tool_appears_in_the_reader_registry():
    """The R-34 assertion, at the level the aggregator sees: the endpoint an agent connects to."""
    server = brain_reader_server.build_reader_server(
        brain_reader_server.build_reader_brain(_tools()))
    registered = registered_tool_names(server)
    assert not (registered & set(capability_names(WRITE_CAPABILITIES)))
    assert not (registered & set(UNEXPOSED_METHODS))


@needs_sdk
def test_the_writer_server_registers_only_ingestion():
    server = brain_writer_server.build_writer_server(_tools())
    assert registered_tool_names(server) == set(capability_names(WRITE_CAPABILITIES))


def test_the_two_servers_have_different_names():
    """They are separate processes; an aggregator registers them under separate names, and only
    one of the two is put in front of the agent."""
    assert brain_reader_server.SERVER_NAME != brain_writer_server.SERVER_NAME


@needs_sdk
def test_the_seal_refuses_a_tool_outside_the_allow_list():
    """The build-time guard, shown failing. Without this the seal could be a no-op and every
    registry test above would still pass."""
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("planted")
    tools = _tools()

    @server.tool()
    def brain_query(question: str) -> dict:
        return tools.brain_query(question)

    @server.tool()
    def brain_ingest(source_path: str) -> dict:        # the R-34 shape, planted
        return tools.brain_ingest(source_path)

    with pytest.raises(CapabilityBoundaryError) as err:
        seal_registry(server, READ_CAPABILITIES, process="reader")
    assert "brain_ingest" in str(err.value)


@needs_sdk
def test_the_seal_refuses_a_declared_tool_that_was_never_registered():
    """The other direction: a read the agent silently cannot call is also a broken registry."""
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("empty")
    with pytest.raises(CapabilityBoundaryError):
        seal_registry(server, READ_CAPABILITIES, process="reader")


def test_the_reader_process_has_no_ingestion_capability_configured():
    """Defence in depth: the reader's own BrainTools has no harness and no registered source, so
    the path it does not expose has nothing to run with either."""
    tools = brain_reader_server.default_reader_tools()
    assert tools.harness is None and tools.sources == {}
    refusal = tools.brain_ingest(DEMO_SOURCE)
    assert refusal["error"] and refusal["entities_added"] == 0
    # The reader no longer seeds its store (Codex f1 #4: seeding is a write, and the writers own
    # it), so the refusal is checked against the store being left exactly as it was: empty.
    assert tools.store.counts()["nodes"] == 0


def test_the_combined_server_builder_is_gone():
    """A caller reaching for the old builder gets an explanation, not a server with both mouths."""
    import brain_server

    with pytest.raises(RuntimeError, match="R-34"):
        brain_server.build_mcp_server()


def test_the_capability_record_carries_no_effect_label():
    """R-07: an ``effect="read"`` field on an arbitrary callable asserts nothing, because nothing
    checks it. Membership of a list that decides a process is what this file can actually test."""
    fields = set(capabilities.Capability.__dataclass_fields__)
    assert fields == {"name", "method", "summary"}


def test_every_exposed_tool_carries_the_manifest_description():
    """A tool with no description is a tool a model cannot choose.

    The reader registered four tools with EMPTY descriptions and every test still passed — the
    boundary held, the schemas were right, and the surface was unusable: an aggregator listed
    four bare names. Taking the text from the manifest also means it cannot drift from the
    allow-list entry that authorises the tool, because they are the same object.
    """
    from capabilities import READ_CAPABILITIES
    from brain_reader_server import build_reader_server

    server = build_reader_server()
    registered = {t.name: t for t in server._tool_manager.list_tools()}
    assert set(registered) == {c.name for c in READ_CAPABILITIES}
    for capability in READ_CAPABILITIES:
        described = (registered[capability.name].description or "").strip()
        assert described, f"{capability.name} is exposed with no description"
        assert described == capability.summary.strip(), (
            f"{capability.name}'s description has drifted from its manifest entry")
