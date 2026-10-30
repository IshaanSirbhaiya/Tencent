"""The vendor-import CI rule must be in place from T01 (P2 / ADR-0001, ADR-0002).

Today it passes vacuously (no harness yet); it must FAIL loudly the moment a forbidden
vendor import leaks into any package other than packages/harness.
"""
from __future__ import annotations

from pathlib import Path

from check_vendor_imports import EXCEPTIONS, REPO_ROOT, find_violations, scan_text


def test_repo_currently_has_no_vendor_imports():
    # vacuous pass: no anthropic/openai import exists outside packages/harness yet
    assert find_violations(REPO_ROOT) == []


def test_rule_catches_a_violation():
    assert scan_text("import anthropic\n")
    assert scan_text("from openai import OpenAI\n")
    assert scan_text("import anthropic.types\n")


def test_rule_does_not_false_positive():
    assert scan_text("import openairlines\n") == []         # prefix, not the SDK
    assert scan_text("# import anthropic in a comment\n") == []
    assert scan_text('msg = "import openai"\n') == []       # string literal, not an import


def test_harness_path_would_be_allowed(tmp_path):
    # a violation under packages/harness/ is NOT reported
    root = tmp_path
    harness = root / "packages" / "harness"
    harness.mkdir(parents=True)
    (harness / "client.py").write_text("import anthropic\n", encoding="utf-8")
    other = root / "packages" / "convert"
    other.mkdir(parents=True)
    (other / "bad.py").write_text("import openai\n", encoding="utf-8")
    viols = find_violations(root)
    flagged = {v[0] for v in viols}
    assert "packages/convert/bad.py" in flagged
    assert "packages/harness/client.py" not in flagged


def test_stand_in_exceptions_allow_exactly_one_import_in_exactly_those_files(tmp_path):
    # D-41: the two stand-in model files may each hold ONE `import anthropic`, nothing more
    assert set(EXCEPTIONS) == {"apps/internal_brain/databridge_ib/claude.py",
                               "apps/internal_brain/databridge_ib/views/composer.py"}
    root = tmp_path
    ok = root / "apps" / "internal_brain" / "databridge_ib" / "claude.py"
    ok.parent.mkdir(parents=True)
    ok.write_text("def f():\n    import anthropic\n", encoding="utf-8")
    assert find_violations(root) == []
    ok.write_text("def f():\n    import anthropic\n    import openai\n", encoding="utf-8")
    assert {v[2] for v in find_violations(root)} == {"import anthropic", "import openai"}
    other = root / "apps" / "internal_brain" / "databridge_ib" / "llm.py"
    other.write_text("import anthropic\n", encoding="utf-8")
    ok.write_text("def f():\n    import anthropic\n", encoding="utf-8")
    assert [v[0] for v in find_violations(root)] == ["apps/internal_brain/databridge_ib/llm.py"]
