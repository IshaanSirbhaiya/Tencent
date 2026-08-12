"""Ready-made sealed read adapters.

Kept out of ``sealed.py`` for a reason worth knowing: ``SealedReadAdapter.__init_subclass__``
scans the source of the module a subclass is defined in, and ``sealed.py`` names the write-shaped
patterns it looks for — so it correctly refuses to host an adapter of its own. The wall applying to
the file that defines the wall is a good sign; splitting the concrete adapter out is the fix.

``StaticReadAdapter`` covers the honest common case: rows a caller has already grounded, handed to
the agent unchanged. Tests use it, and so does any fixture-backed demo. It adds no capability —
the capability is the one passed in, from the closed set in ``sealed.py``.
"""
from __future__ import annotations

from typing import Any

from .results import ToolResult
from .sealed import ReadCapability, SealedReadAdapter


class StaticReadAdapter(SealedReadAdapter):
    """An adapter over rows already in memory. The base for tests and for fixture-backed demos.

    It exists so the common honest case — "here are rows a caller already grounded" — does not
    require a new module and a new scan for every tool.
    """

    def __init__(self, capability: ReadCapability, rows: tuple = (), note: str = "",
                 *, name: str = "", description: str = "",
                 parameters: dict = None, log: list = None) -> None:
        if name:
            # Per-instance overrides, so one class can back several declared tools. The class-level
            # scan has already run; nothing here can add a capability.
            self.name = name
        if description:
            self.description = description
        if parameters is not None:
            self.parameters = parameters
        super().__init__(capability)
        self._rows = tuple(rows)
        self._note = note
        self.calls: list = log if log is not None else []

    def read(self, **arguments: Any) -> ToolResult:
        self.calls.append(dict(arguments))
        return ToolResult(tool=self.name, rows=self._rows, note=self._note)
