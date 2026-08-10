"""AdapterInfo registry (09_FINAL_PRD §8/§9) — the single source of truth for formats.

Every adapter registers ``{format, modality, ir, read, write, lossiness, limits}``. The
Section-5 support matrix is GENERATED from this registry (doc-from-code), so the docs
cannot drift from the code. Routing resolves source/target adapters here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

Modality = Literal["structured", "document", "conversation", "dataset", "graph", "report"]


@dataclass(frozen=True)
class AdapterInfo:
    format: str                                  # registry key, e.g. "csv", "json"
    modality: Modality
    ir: str                                      # canonical IR name, e.g. "RowBatch"
    lossiness: Literal["lossless", "lossy"] = "lossless"
    limits: list[str] = field(default_factory=list)
    read: Optional[Callable[[bytes], object]] = None    # bytes → IR
    write: Optional[Callable[[object], bytes]] = None   # IR → bytes

    @property
    def can_read(self) -> bool:
        return self.read is not None

    @property
    def can_write(self) -> bool:
        return self.write is not None


class AdapterRegistry:
    def __init__(self) -> None:
        self._by_format: dict[str, AdapterInfo] = {}

    def register(self, info: AdapterInfo) -> None:
        self._by_format[info.format] = info

    def get(self, fmt: str) -> Optional[AdapterInfo]:
        return self._by_format.get(fmt)

    def formats(self) -> list[str]:
        return sorted(self._by_format)

    def all(self) -> list[AdapterInfo]:
        return [self._by_format[f] for f in self.formats()]


# The process-wide registry. Adapters call ``register_adapter`` at import time.
REGISTRY = AdapterRegistry()


def register_adapter(info: AdapterInfo) -> AdapterInfo:
    REGISTRY.register(info)
    return info
