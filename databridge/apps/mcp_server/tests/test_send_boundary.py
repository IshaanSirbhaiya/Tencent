"""The send server exists, and the agent cannot reach it (R-34's shape, one notch more serious).

`test_capability_boundary.py` established the read/write split: a mutating tool must not sit behind
the endpoint an agent connects to. This file does the same job for the third process, where the
stakes are higher — ingestion is recoverable and **outbound mail is not undoable**
(`GRAPH_AGENT_AND_MCP.md` §5).

Four independent reasons ``send_email`` is out of reach, each tested here so that losing one is
visible:

1. it is not on ``READ_CAPABILITIES``, so it is not in the reader's registry — the registry the
   aggregator is registered against (`MCP_RUNBOOK.md` §3);
2. it lives in a different **process**, on a different port, and the runbook registers the reader
   and only the reader;
3. ``packages/agent``'s own declaration screen refuses a tool named ``send_email`` at registration,
   before any model has spoken — so even a mis-wired composition root fails loudly;
4. the send server holds no brain, no graph, no outbox and no ledger, so a caller who did reach it
   would find three strings and nothing to discover.
"""
from __future__ import annotations

import inspect
import re

import pytest

import brain_reader_server
import brain_writer_server
import capabilities
import mail_send_server
import serving
from capabilities import (
    READ_CAPABILITIES,
    SEND_CAPABILITIES,
    SEND_TOOL_NAMES,
    WRITE_CAPABILITIES,
    CapabilityBoundaryError,
    capability_names,
    registered_tool_names,
    seal_registry,
)


class NullTransport:
    """A transport that would notice being called. It is never called in this file."""

    name = "null"

    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def send(self, *, to: str, subject: str, body: str) -> str:
        self.sent.append((to, subject, body))
        return "<null@invalid>"


try:
    import mcp as _mcp_sdk                       # noqa: F401  (probe only)
    HAVE_MCP_SDK = True
except ImportError:                              # pragma: no cover — SDK is installed here
    HAVE_MCP_SDK = False

needs_sdk = pytest.mark.skipif(not HAVE_MCP_SDK, reason="the MCP SDK is not installed")


# --- the manifest ---------------------------------------------------------------------


def test_there_are_send_capabilities_to_check():
    """Guards everything below: an empty list would make it all vacuously green."""
    assert SEND_CAPABILITIES and capability_names(SEND_CAPABILITIES) == ("send_email",)


def test_the_three_capability_lists_are_disjoint():
    read, write, send = (set(capability_names(c))
                         for c in (READ_CAPABILITIES, WRITE_CAPABILITIES, SEND_CAPABILITIES))
    assert not (read & send) and not (write & send) and not (read & write)


def test_no_send_tool_is_on_the_readers_capability_list():
    """THE assertion, at the level the aggregator sees: the list the agent's endpoint is built
    from does not contain this tool, and could not contain it silently."""
    assert not (SEND_TOOL_NAMES & capabilities.READ_TOOL_NAMES)
    for capability in SEND_CAPABILITIES:
        assert capability.name not in capabilities.READ_TOOL_NAMES
        assert capability.name not in capabilities.WRITE_TOOL_NAMES


def test_no_send_tool_is_a_brain_method():
    """Sending is not something the clinic brain does. If ``BrainTools`` ever grew a `send_email`,
    ``test_every_public_method_is_classified`` would demand it be classified — and this says which
    answer is wrong."""
    from brain_server import BrainTools

    for capability in SEND_CAPABILITIES:
        assert not hasattr(BrainTools, capability.method)


# --- the registries -------------------------------------------------------------------


@needs_sdk
def test_the_reader_registry_contains_no_send_tool():
    server = brain_reader_server.build_reader_server(
        brain_reader_server.build_reader_brain())
    assert not (registered_tool_names(server) & SEND_TOOL_NAMES)


@needs_sdk
def test_the_writer_registry_contains_no_send_tool():
    from databridge_evals.e_intake import FIXTURE_TEXT, gold_harness
    from brain_server import BrainTools

    tools = BrainTools(harness=gold_harness(), sources={"demo_sample.txt": FIXTURE_TEXT})
    tools.load_seed()
    server = brain_writer_server.build_writer_server(tools)
    assert not (registered_tool_names(server) & SEND_TOOL_NAMES)


@needs_sdk
def test_the_send_server_registers_exactly_the_send_allow_list():
    server = mail_send_server.build_send_server(NullTransport())
    assert registered_tool_names(server) == set(capability_names(SEND_CAPABILITIES))


@needs_sdk
def test_the_send_server_refuses_a_tool_outside_its_allow_list():
    """The build-time seal, shown failing. Without this the seal could be a no-op."""
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("planted")

    @server.tool()
    def send_email(to: str, subject: str, body: str) -> dict:
        return {}

    @server.tool()
    def brain_query(question: str) -> dict:       # a read tool, planted into the send process
        return {}

    with pytest.raises(CapabilityBoundaryError) as err:
        seal_registry(server, SEND_CAPABILITIES, process="send")
    assert "brain_query" in str(err.value)


@needs_sdk
def test_the_send_tool_carries_a_description_saying_who_may_call_it():
    """A tool with no description is a tool a model cannot choose — and this one's description is
    the sentence somebody should read if it ever appears in an agent's tool list."""
    server = mail_send_server.build_send_server(NullTransport())
    tool = next(t for t in server._tool_manager.list_tools() if t.name == "send_email")
    described = (tool.description or "").strip()
    assert described
    assert "dispatcher" in described.lower()
    assert "never registered" in described.lower()


# --- three processes, three ports ------------------------------------------------------


def test_the_three_servers_have_different_names_and_different_ports():
    names = {brain_reader_server.SERVER_NAME, brain_writer_server.SERVER_NAME,
             mail_send_server.SERVER_NAME}
    assert len(names) == 3
    ports = {serving.DEFAULT_READER_PORT, serving.DEFAULT_WRITER_PORT, serving.DEFAULT_SEND_PORT}
    assert ports == {8765, 8766, 8767}


def test_the_send_port_is_configurable_and_refuses_nonsense(monkeypatch):
    monkeypatch.setenv(serving.SEND_PORT_ENV, "9100")
    assert mail_send_server.send_port() == 9100
    monkeypatch.setenv(serving.SEND_PORT_ENV, "not-a-port")
    with pytest.raises(ValueError):
        mail_send_server.send_port()


def test_dns_rebinding_protection_stays_on_for_the_send_port():
    """Allow-list, never disable — the check exists so a browser page cannot be tricked into
    driving a local MCP server, and of the three this is the one where that would matter most
    (MCP_RUNBOOK.md §4.4)."""
    settings = serving.transport_security_for(serving.DEFAULT_SEND_PORT)
    assert f"host.docker.internal:{serving.DEFAULT_SEND_PORT}" in settings.allowed_hosts
    assert f"127.0.0.1:{serving.DEFAULT_SEND_PORT}" in settings.allowed_hosts
    assert getattr(settings, "enable_dns_rebinding_protection", True) is True
    # And nothing beyond this machine is listed.
    for host in settings.allowed_hosts:
        assert host.split(":")[0] in serving.LOCAL_HOSTNAMES


# --- the agent's own screen would refuse this tool --------------------------------------


def test_the_agents_declaration_screen_refuses_the_send_tool_by_name():
    """Layer three. `packages/agent` refuses a send-SHAPED name at registration, before any model
    has spoken — so a mis-wired composition root fails loudly rather than quietly widening the
    agent's surface."""
    from databridge_agent.tools import SendCapabilityRefused, screen_declaration

    for capability in SEND_CAPABILITIES:
        with pytest.raises(SendCapabilityRefused):
            screen_declaration(capability.name, capability.summary)


def test_no_agent_playbook_names_a_send_tool():
    """Config must not be a back door around a capability the code refuses to hold."""
    from databridge_agent.playbooks import playbooks

    assert not (set(playbooks().tool_names()) & SEND_TOOL_NAMES)


# --- the send process holds nothing worth reaching ----------------------------------------


def test_the_send_server_module_says_it_is_dispatcher_only_and_never_aggregated():
    """The runbook makes docstrings load-bearing: registration is a deployment step performed by a
    human reading this file, so the file has to say the thing that must not be done."""
    doc = (mail_send_server.__doc__ or "").lower()
    assert "dispatcher" in doc
    assert "never be registered" in doc or "never registered" in doc
    assert "aggregator" in doc


def test_the_send_server_imports_no_brain_no_graph_and_no_outbox():
    """It takes three strings a human already approved. If it could read an outbox, a ledger or the
    graph, reaching it would be worth something to an attacker; as written, it is not."""
    source = inspect.getsource(mail_send_server)
    for forbidden in ("brain_server", "BrainTools", "databridge_graph", "databridge_outbox",
                      "databridge_intake", "EvidenceLedger", "OutboxStore"):
        assert forbidden not in source, (
            f"mail_send_server.py references {forbidden} — the send process decides nothing and "
            f"must have nothing to decide with")


def test_the_send_tool_takes_three_strings_and_nothing_else():
    """No draft id, no intake id, no store handle: nothing through which it could look something
    up, and nothing through which it could be pointed at a different person."""
    source = inspect.getsource(mail_send_server)
    signature = re.search(r"def send_email\((.*?)\)\s*->", source, re.S)
    assert signature, "send_email's signature has changed shape — re-read this test"
    params = [p.strip() for p in signature.group(1).split(",")]
    assert params == ["to: str", "subject: str", "body: str"], params


def test_the_send_server_does_not_import_a_transport_at_module_scope():
    """Importing this module — which the tests above do — must not open smtplib. The transport is
    a dependency of RUNNING it."""
    source = inspect.getsource(mail_send_server)
    module_level = [line for line in source.splitlines()
                    if re.match(r"^(?:import|from)\s", line)]
    assert not any("smtp" in line for line in module_level), module_level


def test_the_send_server_borrows_the_dispatchers_one_transport():
    """One send implementation. A second SMTP client would be a second place for the
    header-injection screen to be missing."""
    source = inspect.getsource(mail_send_server.default_transport)
    assert "from smtp_transport import SmtpTransport" in source
