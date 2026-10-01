"""The seam between the agent runtime and delegated cross-source querying (FED).

The runtime does not import `databridge_federated` at module load — that package builds ON this
one (it reuses the answer contract, the pointer registry and the read-only SQL check). Instead the
runtime talks to anything shaped like `Federation`, and `databridge_federated.FederatedService` is
the real one.

Admission is the CALLER's. A delegate answers a sub-query only through a `ReadAdmission` handed to
it by whoever asked: inside an agent run that is `ToolLayerAdmission` — the agent's declared
sources (behaviour half) AND its compiled grants (governance half). A delegate therefore cannot
read a source the calling agent is not granted; a refusal comes back as COVERAGE, never as a silent
gap.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from databridge_agentdef import AmbientAuthorityRefused

#: How a delegate reads a connection. Closed.
READ_CHANNELS = ("facts", "documents", "live")


@runtime_checkable
class ReadAdmission(Protocol):
    """Whether the CALLER may read ``connection`` through ``channel``. ``(ok, detail)``."""

    principal: str

    def admit_read(self, connection: str, channel: str) -> tuple[bool, str]: ...


@runtime_checkable
class Federation(Protocol):
    """What the tool layer needs from a federated service."""

    def available(self, adapter_agent: str) -> bool: ...

    def ask_delegate(self, adapter_agent: str, subtask: dict, *, admission: ReadAdmission,
                     run_id: Optional[str] = None) -> Any: ...


class ToolLayerAdmission:
    """An agent run's admission: a connection is readable by a delegate only when it is one of the
    agent's declared ``sources`` AND the compiled grants hold ``delegate.<aa>`` (already checked
    by `ToolLayer.admit` before the delegate is reached). A LIVE read additionally needs the
    connection's own read grant (``<conn>.read_any`` or ``<conn>.sql``), exactly as a direct
    ``source.read`` would."""

    def __init__(self, layer: Any, adapter_agent: str) -> None:
        self._layer = layer
        self.adapter_agent = adapter_agent
        self.principal = f"agent:{layer.spec.agent}"
        self.purpose = getattr(layer.authority, "purpose", None)

    def admit_read(self, connection: str, channel: str) -> tuple[bool, str]:
        from .mapping import MappingError, READ_ANY_CAPABILITY, SQL_CAPABILITY, source_id_for  # noqa: PLC0415

        if channel not in READ_CHANNELS:
            return False, f"unknown read channel {channel!r}"
        if connection not in self._layer.scope:
            return False, (f"connection {connection!r} is not one of agent "
                           f"{self._layer.spec.agent!r}'s declared sources")
        if channel != "live":
            return True, f"declared source of {self._layer.spec.agent!r}, delegate grant held"
        # R-021 #10: a LIVE read needs the behaviour half too — the workflow must list a live read
        # tool on this connection (`source.read:<conn>…` or `source.query:<conn>`); a grant alone
        # is authority nobody declared a use for.
        from databridge_workflow_spec import parse_tool  # noqa: PLC0415
        live_tools = [t for t in self._layer.spec.tools
                      if parse_tool(t)["family"] == "source"
                      and parse_tool(t)["verb"] in ("read", "query")
                      and parse_tool(t)["connection"] == connection]
        if not live_tools:
            return False, (f"a live read of {connection!r} needs a workflow read tool on it "
                           f"(source.read:{connection} or source.query:{connection}); agent "
                           f"{self._layer.spec.agent!r} declares none")
        try:
            sid = source_id_for(connection)
        except MappingError as e:
            return False, str(e)
        for cap in (f"{sid}.{READ_ANY_CAPABILITY}", f"{sid}.{SQL_CAPABILITY}"):
            try:
                grant = self._layer.authority.require(cap)
            except AmbientAuthorityRefused:
                continue
            return True, f"granted by {grant.ref}"
        return False, (f"a live read of {connection!r} needs a compiled grant for "
                       f"{sid}.read_any or {sid}.sql, which this agent does not hold")


def federated_answer(question: str, **kwargs: Any) -> Any:
    """``databridge_federated.answer(question, …)`` — the path /brain or the orchestrator calls.
    Imported lazily so the runtime keeps working where the federated package is absent."""
    from databridge_federated import answer  # type: ignore  # noqa: PLC0415
    return answer(question, **kwargs)


__all__ = ["READ_CHANNELS", "ReadAdmission", "Federation", "ToolLayerAdmission",
           "federated_answer"]
