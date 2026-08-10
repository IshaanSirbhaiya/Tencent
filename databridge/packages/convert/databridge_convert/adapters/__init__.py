"""adapters/ — format adapters (C03/C04). Importing this package REGISTERS every adapter
into the router registry (each module calls ``register_adapter`` at import time)."""
from __future__ import annotations

from . import (  # noqa: F401
    arrow, csv, documents, excel, html_tables, json, jsonl, md_tables, parquet, xml, yaml,
)

__all__ = ["csv", "json", "jsonl", "parquet", "arrow", "yaml", "xml", "html_tables",
           "md_tables", "excel", "documents"]
