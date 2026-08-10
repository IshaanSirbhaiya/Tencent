"""adapters/target/ — document TARGET adapters (C12): DocumentRecord → bytes."""
from __future__ import annotations

from . import docx, html, md, txt  # noqa: F401

__all__ = ["md", "txt", "docx", "html"]
