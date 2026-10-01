"""HC2 (2026-10-01), SWEEP hardcode audit #12, #13, #20: strings the engine SERVES — a download
name, a refusal, an MCP tool description, an MCP server name, an SMTP sender — name no domain.

Each of these files runs under every pack. The scan reads the code's string constants (docstrings
excluded: a docstring is read by a developer, not served) and matches them against the same
`DOMAIN_TERMS` that `test_no_domain_branching` enforces, so the vocabulary is not written down twice.
A second check also bans "frontdesk": a front desk is a healthcare surface, not an engine one.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest

APPS = Path(__file__).resolve().parents[2]
REPO = APPS.parent

SERVED = (
    APPS / "gateway" / "databridge_gateway" / "export_routes.py",
    APPS / "mcp_server" / "capabilities.py",
    APPS / "mcp_server" / "brain_reader_server.py",
    APPS / "mcp_server" / "brain_writer_server.py",
    APPS / "mcp_server" / "mail_send_server.py",
    APPS / "dispatcher" / "smtp_transport.py",
)


def _word() -> re.Pattern:
    path = REPO / "packages" / "graph" / "tests" / "test_no_domain_branching.py"
    spec = importlib.util.spec_from_file_location("_ndb_terms_hc2", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return re.compile(mod._WORD.pattern + r"|frontdesk", re.IGNORECASE)


def served_strings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    doc = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and node.body and isinstance(node.body[0], ast.Expr) \
                and isinstance(node.body[0].value, ast.Constant):
            doc.add(id(node.body[0].value))
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
            and isinstance(n.value, str) and id(n) not in doc]


@pytest.mark.parametrize("path", SERVED, ids=lambda p: p.name)
def test_no_served_string_names_a_domain(path):
    strings = served_strings(path)
    assert len(strings) >= 1, f"{path.name}: the scan found no strings — it would pass vacuously"
    word = _word()
    assert [s for s in strings if word.search(s)] == []


def test_the_scan_catches_the_strings_that_used_to_be_there():
    """Non-vacuous: the pattern matches each literal the audit named."""
    word = _word()
    for old in ("clinic-evidence-sheets-", "the clinic graph is not initialised",
                "Answer a question from the clinic graph, with a citation per value.",
                "databridge-clinic-send", "walnut-frontdesk@clinic.invalid"):
        assert word.search(old), old


def test_the_message_id_domain_is_the_senders():
    """The Message-Id's domain is computed from the sender, not written down separately."""
    import sys
    sys.path.insert(0, str(APPS / "dispatcher"))
    try:
        import smtp_transport as st
    finally:
        sys.path.remove(str(APPS / "dispatcher"))
    assert st.message_id_domain("ops@example.invalid") == "example.invalid"
    assert st.message_id_domain(st.DEFAULT_SENDER) == st.DEFAULT_SENDER.rpartition("@")[2]
    assert st.message_id_domain("Name <a@b.invalid>") == "b.invalid"
