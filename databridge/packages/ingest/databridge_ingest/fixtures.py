"""Static SchemaExtract fixtures — a deliberate shortcut around the live PG connector.

PROMPT 3 needs a SchemaExtract to build the schema graph against, but the live
PostgreSQL connector (T03) is a LATER prompt (PROMPT 7). This loads a hand-written
JSON fixture so T06 can be tested end-to-end now. The live connector will produce the
SAME ``SchemaExtract`` type, so nothing downstream changes when it lands — only the
producer swaps from "load JSON" to "read information_schema".
"""
from __future__ import annotations

from pathlib import Path

from .schema_extract import SchemaExtract

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> SchemaExtract:
    """Load a SchemaExtract fixture by stem (e.g. ``"fintech_schema_extract"``)."""
    path = FIXTURES_DIR / (name if name.endswith(".json") else f"{name}.json")
    if not path.exists():
        raise FileNotFoundError(f"no SchemaExtract fixture at {path}")
    return SchemaExtract.model_validate_json(path.read_text(encoding="utf-8"))


def fintech_fixture() -> SchemaExtract:
    """The fintech reference schema: users, merchants, transactions, disputes,
    support_messages (02_TRD Task 3 fixture description)."""
    return load_fixture("fintech_schema_extract")


def available_fixtures() -> list[str]:
    return sorted(p.stem for p in FIXTURES_DIR.glob("*.json"))
