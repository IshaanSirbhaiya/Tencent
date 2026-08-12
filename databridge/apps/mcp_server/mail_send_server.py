"""The clinic MCP **send** server — reachable by the DISPATCHER only.

**THIS SERVER MUST NEVER BE REGISTERED IN THE AGGREGATOR THE AGENT READS FROM.** Not under a
read-only-sounding name, not "just for the demo", not behind a flag. `MCP_RUNBOOK.md` §3 registers
``walnut_clinic_read`` and only ``walnut_clinic_read``; the writer (8766) already exists, runs, and
is deliberately not registered, and this process (8767) is the same decision one notch more serious,
because ingestion is recoverable and **outbound mail is not undoable**
(`GRAPH_AGENT_AND_MCP.md` §5).

One tool, ``capabilities.SEND_CAPABILITIES``:

    send_email(to, subject, body) -> {"message_id": ...}

What it does NOT do is the interesting half. It does not read the outbox, evaluate a condition,
choose a recipient, or know what an intake is. It takes three strings that a named human already
approved — the dispatcher read them off an ``approved`` outbox row — and hands them to
``apps/dispatcher/smtp_transport.py``. If this tool were somehow reached by a model, the worst it
could do is send text the caller already had; it cannot discover a patient, an address, or a
document, because there is nothing here to discover them from.

**Why it exists at all, given the dispatcher can call SMTP in-process.** `GRAPH_AGENT_AND_MCP.md`
§4.2 puts the send tools behind MCP so the capability boundary is a process boundary, which is what
makes the Copilot Studio portability argument a demonstration rather than a claim: the same tool
surface behind a different agent runtime is a connector swap. The dispatcher may use either — the
in-process transport, or this server over MCP — and both go through the one ``SmtpTransport``.

**Transport security stays ON.** ``serving.fastmcp_kwargs`` derives the DNS-rebinding allow-list
from the port, including ``host.docker.internal:8767`` for a containerised caller, because the check
exists so a browser page cannot be tricked into driving a local MCP server — and of the three
servers, this is the one where that would matter most (MCP_RUNBOOK.md §4.4).

Run it, against Mailpit, only when something actually needs to send:

    DATABRIDGE_SMTP_HOST=127.0.0.1 DATABRIDGE_SMTP_PORT=1025 \\
      python apps/mcp_server/mail_send_server.py
"""
from __future__ import annotations

from typing import Any, Optional

from capabilities import SEND_CAPABILITIES, seal_registry
from serving import (
    DEFAULT_SEND_PORT,
    SEND_PORT_ENV,
    fastmcp_kwargs,
    port_from_env,
    serve_streamable_http,
)

SERVER_NAME = "databridge-clinic-send"

#: Said in the tool's own description as well as here, because a tool description is what an
#: aggregator lists and what a model reads. If this ever appears in an agent's tool list, the
#: sentence the agent sees should be the one saying it should not be there.
DISPATCHER_ONLY = ("Reachable by the model-free dispatcher only. This server is never registered "
                   "with the aggregator an agent reads from.")


def default_transport() -> Any:
    """The one send implementation, borrowed from the dispatcher.

    Imported lazily and from ``apps/dispatcher`` rather than reimplemented here: a second SMTP
    client would be a second place for a header-injection screen to be missing, and the send
    capability having exactly one implementation is worth more than tidy package boundaries. It
    lives with the dispatcher because the dispatcher is the only thing allowed to reach it.
    """
    from smtp_transport import SmtpTransport

    return SmtpTransport()


def send_port() -> int:
    return port_from_env(SEND_PORT_ENV, DEFAULT_SEND_PORT)


def build_send_server(transport: Optional[Any] = None, *, port: Optional[int] = None):
    """Construct the send-only MCP server. SDK imported lazily, as in the reader and the writer.

    It listens on its OWN port. **Never register this URL with the aggregator** — see the module
    docstring, and `MCP_RUNBOOK.md` §6 ("the writer is never registered"), which applies here with
    more force.
    """
    from mcp.server.fastmcp import FastMCP  # type: ignore

    transport = transport if transport is not None else default_transport()
    mcp = FastMCP(SERVER_NAME, **fastmcp_kwargs(port if port is not None else send_port()))
    summary = next(c.summary for c in SEND_CAPABILITIES if c.name == "send_email")

    @mcp.tool(description=f"{summary} {DISPATCHER_ONLY}")
    def send_email(to: str, subject: str, body: str) -> dict:
        # No outbox handle, no store, no draft id. Three strings in, a message id out. Everything
        # that decides whether this SHOULD happen already happened, in a different process, in
        # front of a human.
        return {"message_id": transport.send(to=to, subject=subject, body=body)}

    return seal_registry(mcp, SEND_CAPABILITIES, process="send")


def main() -> None:  # pragma: no cover (entrypoint)
    """Serve the send tool over streamable HTTP on its own port.

    Whoever may reach this port may send mail as the clinic, so who may reach it is a deployment
    decision and the answer is "the dispatcher". Do not register it with MCPJungle.
    """
    serve_streamable_http(build_send_server())


if __name__ == "__main__":  # pragma: no cover
    main()
