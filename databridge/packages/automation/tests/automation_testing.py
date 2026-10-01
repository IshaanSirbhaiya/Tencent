"""Shared test fixtures for databridge_automation: a synthetic catalogue, a purpose vocabulary
and a scratch repo root with a golden set, so a generated declaration can really COMPILE."""
from __future__ import annotations

import json
from pathlib import Path

from databridge_automation import Catalogue, ConnectionInfo, Governance, ToolRef, classify_tool

PACK = "workspace"
PURPOSE = "workspace.operations"

FILES = ConnectionInfo(
    id="larkspur-files", title="Larkspur files", kind="files",
    tools=(classify_tool("list_directory"), classify_tool("read_text_file"),
           classify_tool("write_file"), classify_tool("move_file")),
    locations=("Contracts", "Follow-ups", "Invoices", "Invoices/2026"),
    plan_status="approved")
DB = ConnectionInfo(
    id="larkspur-db", title="Larkspur CRM database", kind="database",
    tools=(ToolRef("query"), classify_tool("insert_row")),
    locations=("public.invoices", "public.customers", "public.tasks"),
    aliases=("crm",), plan_status="approved")
DRIVE = ConnectionInfo(
    id="larkspur-drive", title="Larkspur Google Drive", kind="drive",
    tools=(classify_tool("search_files"), classify_tool("read_file")),
    locations=("Legal/Contracts", "Board"), plan_status="proposed")

KINDS = ("person", "organisation", "document", "invoice", "contract", "project", "meeting")


def catalogue(*conns: ConnectionInfo, golden: str | None = None) -> Catalogue:
    return Catalogue(connections=tuple(conns) or (FILES, DB), kinds=KINDS,
                     subject_kinds=("person",),
                     governance=Governance(pack=PACK, purpose=PURPOSE, golden_set=golden))


def purposes():
    from databridge_adapter.purposes import PurposeDecl, PurposeVocabulary
    return PurposeVocabulary({PURPOSE: PurposeDecl(id=PURPOSE, pack=PACK,
                                                   description="synthetic test purpose",
                                                   subject_scoped=False)}, [PACK])


def repo_with_golden(tmp: Path, rel: str = "packs/workspace/evals/automations.jsonl") -> Path:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"question": "synthetic", "expect": "cited"}) + "\n", encoding="utf-8")
    return tmp
