"""Ports and host allow-listing for the two MCP processes (``serving.py``).

Nothing here binds a socket, starts a container or talks to the aggregator. Registration with
MCPJungle is a deployment step; a test that needs a running container is a test that rots, and this
repo already asserts the front-desk flow contacts no external origin.

What is worth testing without a network is exactly what went wrong by hand: the allow-list must
follow the configured port (a hardcoded one drifts and produces a ``421`` that points nowhere near
the cause), the rebinding protection must stay ON, and the two processes must not land on the same
port — two servers sharing a port is the capability split undone by a config default.
"""
from __future__ import annotations

import pytest

import brain_reader_server
import brain_writer_server
import serving

pytest.importorskip("mcp", reason="the MCP SDK is optional; the tool layer is not")


def test_the_two_processes_default_to_different_ports():
    assert serving.DEFAULT_READER_PORT != serving.DEFAULT_WRITER_PORT


def test_ports_come_from_the_environment(monkeypatch):
    monkeypatch.setenv(serving.READER_PORT_ENV, "9111")
    monkeypatch.setenv(serving.WRITER_PORT_ENV, "9222")
    assert brain_reader_server.reader_port() == 9111
    assert brain_writer_server.writer_port() == 9222


def test_an_unset_or_empty_port_falls_back_to_the_default(monkeypatch):
    monkeypatch.delenv(serving.READER_PORT_ENV, raising=False)
    assert brain_reader_server.reader_port() == serving.DEFAULT_READER_PORT
    monkeypatch.setenv(serving.READER_PORT_ENV, "   ")
    assert brain_reader_server.reader_port() == serving.DEFAULT_READER_PORT


@pytest.mark.parametrize("bad", ["8080; rm -rf /", "notaport", "0", "70000", "-1"])
def test_a_malformed_port_is_refused_not_ignored(monkeypatch, bad):
    """Falling back silently would start the server on a port the operator is not expecting — and
    with two processes whose separation is the point, that is a server behind the wrong endpoint."""
    monkeypatch.setenv(serving.READER_PORT_ENV, bad)
    with pytest.raises(ValueError):
        brain_reader_server.reader_port()


def test_the_allow_list_is_derived_from_the_port_in_use():
    security = serving.transport_security_for(9999)
    assert set(security.allowed_hosts) == {f"{h}:9999" for h in serving.LOCAL_HOSTNAMES}
    assert "host.docker.internal:9999" in security.allowed_hosts, (
        "MCPJungle runs in a container and sends host.docker.internal as the Host header; without "
        "it the SDK answers 421 Misdirected Request and the cause is invisible from the host")


def test_dns_rebinding_protection_stays_enabled():
    """The allow-list is the fix; switching the check off is not. It is what stops a browser page
    from being tricked into driving a local MCP server holding the clinic graph."""
    assert serving.transport_security_for(8765).enable_dns_rebinding_protection is True


def test_nothing_outside_this_machine_is_allow_listed():
    for host in serving.LOCAL_HOSTNAMES:
        assert host in ("127.0.0.1", "localhost", "host.docker.internal"), host


def test_the_bind_host_is_loopback_unless_deliberately_widened(monkeypatch):
    monkeypatch.delenv(serving.BIND_HOST_ENV, raising=False)
    assert serving.bind_host() == "127.0.0.1"
    monkeypatch.setenv(serving.BIND_HOST_ENV, "0.0.0.0")
    assert serving.bind_host() == "0.0.0.0"


def test_the_servers_are_built_on_their_own_ports_with_the_matching_allow_list(monkeypatch):
    monkeypatch.setenv(serving.READER_PORT_ENV, "9111")
    monkeypatch.setenv(serving.WRITER_PORT_ENV, "9222")
    reader = brain_reader_server.build_reader_server(
        brain_reader_server.build_reader_brain(_seeded_reader_tools()))
    writer = brain_writer_server.build_writer_server(_seeded_writer_tools())

    assert reader.settings.port == 9111 and writer.settings.port == 9222
    assert "host.docker.internal:9111" in reader.settings.transport_security.allowed_hosts
    assert "host.docker.internal:9222" in writer.settings.transport_security.allowed_hosts
    # …and the writer's port is not in the reader's allow-list of Hosts, so a client aimed at one
    # cannot be answered by the other on a mistyped Host header.
    assert not (set(reader.settings.transport_security.allowed_hosts)
                & set(writer.settings.transport_security.allowed_hosts))


def test_the_streamable_http_app_assembles_on_the_path_the_aggregator_registers():
    """MCPJungle is registered with ``--url http://host.docker.internal:<port>/mcp``, so the ASGI
    app has to exist and mount there. Building it binds nothing — no socket is opened here."""
    reader = brain_reader_server.build_reader_server(
        brain_reader_server.build_reader_brain(_seeded_reader_tools()))
    assert reader.settings.streamable_http_path == "/mcp"
    assert "/mcp" in [getattr(route, "path", None) for route in reader.streamable_http_app().routes]


def _seeded_reader_tools():
    from brain_server import BrainTools

    tools = BrainTools(harness=None, sources={})
    tools.load_seed()
    return tools


def _seeded_writer_tools():
    from brain_server import BrainTools
    from databridge_evals.e_intake import FIXTURE_TEXT, gold_harness

    tools = BrainTools(harness=gold_harness(), sources={"demo_sample.txt": FIXTURE_TEXT})
    tools.load_seed()
    return tools
