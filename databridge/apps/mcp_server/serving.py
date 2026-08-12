"""How the two clinic MCP servers are served over streamable HTTP — ports, and host allow-listing.

The aggregator (MCPJungle, `REMEDIATION.md` §3.2) registers a server by URL and speaks
streamable HTTP to it, so each process listens on its own port and each is registered — or, for the
writer and the sender, deliberately *not* registered — separately. **Three ports, because three
processes: that is the capability boundary made operational.** Point the aggregator at the reader's
port only. The writer (8766) and the send server (8767) are addressable and are never registered;
what an agent can call is decided by which socket the aggregator was pointed at.

Two things that bite, both learned the hard way against a running MCPJungle:

1. **The MCP SDK's DNS-rebinding protection rejects the Host header a container sends.** MCPJungle
   runs in Docker and reaches the host as ``host.docker.internal``, which the SDK answers with
   ``421 Misdirected Request — Invalid Host header``. From the host, ``curl`` works perfectly, which
   makes it look like anything but what it is. The fix is to **allow-list** that host, never to
   disable the check: the check exists so a browser page cannot be tricked into driving a local MCP
   server, which is a real attack against a machine with the clinic graph loaded.
2. **Docker's bridge cannot reach a service bound to host loopback** on Linux hosts. The default
   bind here is ``127.0.0.1`` (Docker Desktop on Windows/macOS routes ``host.docker.internal`` to
   it, and loopback keeps the surface off the LAN). If the aggregator cannot connect, set
   ``DATABRIDGE_MCP_BIND_HOST=0.0.0.0`` knowingly — that publishes the graph to every interface on
   the machine, so it is an explicit act, not a default.

Nothing here opens a connection or registers anything; registration is a deployment step, and the
test suite must never need a running container to pass.
"""
from __future__ import annotations

import os
from typing import Any

# Distinct, non-privileged, and away from the gateway (8000) and the frontend (3000). Different
# ports are not the boundary — different processes are — but they are how the aggregator addresses
# one without addressing the other.
DEFAULT_READER_PORT = 8765
DEFAULT_WRITER_PORT = 8766
# The send server. A THIRD port because a third process: reachable by the dispatcher only, and
# never registered with the aggregator the agent reads from (`mail_send_server.py`).
DEFAULT_SEND_PORT = 8767

READER_PORT_ENV = "DATABRIDGE_MCP_READER_PORT"
WRITER_PORT_ENV = "DATABRIDGE_MCP_WRITER_PORT"
SEND_PORT_ENV = "DATABRIDGE_MCP_SEND_PORT"
BIND_HOST_ENV = "DATABRIDGE_MCP_BIND_HOST"

DEFAULT_BIND_HOST = "127.0.0.1"

# The names a client may legitimately use to reach a server on this machine. ``host.docker.internal``
# is here because the aggregator is containerised; nothing beyond the local machine is listed, and
# the rebinding check stays ON.
LOCAL_HOSTNAMES = ("127.0.0.1", "localhost", "host.docker.internal")


def port_from_env(env_var: str, default: int) -> int:
    """Read a port from the environment, defaulting, and refusing anything that is not one.

    A silently ignored malformed value would start the server on the default port while the
    operator believes it is on theirs — and, with two servers whose separation is the point, a
    server on an unexpected port is a server behind an unexpected endpoint.
    """
    raw = os.environ.get(env_var)
    if raw is None or raw.strip() == "":
        return default
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"{env_var}={raw!r} is not a port number") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{env_var}={port} is outside 1–65535")
    return port


def bind_host() -> str:
    return os.environ.get(BIND_HOST_ENV) or DEFAULT_BIND_HOST


def transport_security_for(port: int):
    """DNS-rebinding protection, kept enabled, with the local names allow-listed for this port.

    Derived from ``port`` so the number is written once: an allow-list hardcoded beside a
    configurable port drifts the first time somebody changes the port, and the failure it produces
    (``421``) points nowhere near the cause.
    """
    from mcp.server.transport_security import TransportSecuritySettings  # type: ignore

    return TransportSecuritySettings(
        allowed_hosts=[f"{h}:{port}" for h in LOCAL_HOSTNAMES],
        allowed_origins=[f"http://{h}" for h in LOCAL_HOSTNAMES]
        + [f"http://{h}:{port}" for h in LOCAL_HOSTNAMES],
    )


def fastmcp_kwargs(port: int) -> dict[str, Any]:
    """The construction settings both servers share: bind address, port, and the allow-list.

    ``streamable_http_path`` keeps the SDK default ``/mcp``, which is the path the aggregator is
    registered with (``register --url http://host.docker.internal:<port>/mcp``).
    """
    return {"host": bind_host(), "port": port, "transport_security": transport_security_for(port)}


def serve_streamable_http(server: Any) -> None:  # pragma: no cover (entrypoint)
    """Run a built FastMCP server over streamable HTTP on the port it was constructed with."""
    server.run(transport="streamable-http")
