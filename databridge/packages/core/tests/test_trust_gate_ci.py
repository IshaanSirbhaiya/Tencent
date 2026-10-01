"""The migration gate: no `SourcePointer` may be constructed in packages/core without `trust`.

The spine's D3 end state is `trust: Trust` — required, no default. A default would be a
fail-open (every unmigrated site silently acquires a label nobody chose), and shipping the
required field in one step would break every construction site in the repo at once. The
specified migration is therefore: `Optional[Trust] = None` on the model, *plus a source scan
that fails the build on an omission*, then the field becomes required.

This is the scan half. It is deliberately scoped to `packages/core` for this phase — widening
it to the whole repo is the Phase 3 step, once the adapter Core exists to stamp labels from
source contracts. Widening is a one-line change to `_ROOTS` below, so the scope is visible
rather than buried.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from databridge_core.trust_gate import EXEMPT_MARKER, format_report, scan

_CORE = Path(__file__).resolve().parents[1]          # packages/core


def test_no_pointer_is_constructed_in_core_without_a_trust_label():
    violations = scan([_CORE])
    assert not violations, format_report(violations)


def test_the_gate_actually_detects_an_omission(tmp_path: Path):
    """If this passes vacuously, the gate above proves nothing. So prove the gate first."""
    f = tmp_path / "offender.py"
    f.write_text(textwrap.dedent('''
        from databridge_core import SourcePointer
        p = SourcePointer(source_id="s", kind="table_cell", table="t", column="c", row_id="1")
    '''), encoding="utf-8")
    violations = scan([tmp_path])
    assert len(violations) == 1
    assert violations[0].reason == "OMITTED"


def test_the_gate_accepts_a_labelled_site(tmp_path: Path):
    f = tmp_path / "good.py"
    f.write_text(textwrap.dedent('''
        from databridge_core import SourcePointer, Trust
        p = SourcePointer(source_id="s", kind="table_cell", table="t", column="c",
                          row_id="1", trust=Trust.TRUSTED)
    '''), encoding="utf-8")
    assert scan([tmp_path]) == []


def test_a_splat_is_reported_rather_than_assumed_innocent(tmp_path: Path):
    """`SourcePointer(**kwargs)` is not statically knowable. "We cannot tell" must not read
    as "it is fine" — the site has to be labelled or explicitly exempted by a human."""
    f = tmp_path / "splat.py"
    f.write_text(textwrap.dedent('''
        from databridge_core import SourcePointer
        p = SourcePointer(**kwargs)
    '''), encoding="utf-8")
    violations = scan([tmp_path])
    assert len(violations) == 1 and violations[0].reason == "SPLAT"


def test_an_inline_exemption_suppresses_a_site(tmp_path: Path):
    f = tmp_path / "exempt.py"
    f.write_text(textwrap.dedent(f'''
        from databridge_core import SourcePointer
        # {EXEMPT_MARKER} - proves an unlabelled pointer still constructs
        p = SourcePointer(source_id="s", kind="table_cell", table="t",
                          column="c", row_id="1")
    '''), encoding="utf-8")
    # The marker is on the line ABOVE the call, so it must fall inside the call's span to
    # count. It does not — which is the behaviour we want: the marker goes ON the call.
    assert len(scan([tmp_path])) == 1

    f.write_text(textwrap.dedent(f'''
        from databridge_core import SourcePointer
        p = SourcePointer(source_id="s", kind="table_cell", table="t",
                          column="c", row_id="1")   # {EXEMPT_MARKER} - backwards compat
    '''), encoding="utf-8")
    assert scan([tmp_path]) == []


def test_the_exemption_count_in_core_stays_small():
    """An exemption mechanism nobody counts becomes an exemption mechanism everybody uses.

    Every exempt site in packages/core is a deliberate backwards-compatibility proof, or a test
    that attacks the gate on purpose. If this number climbs, the migration has stalled and
    someone should notice here.

    The cap went 6 -> 12 when R-003 widened the scan to see `model_validate`,
    `model_validate_json`, `model_construct`, aliases and `functools.partial`. Those five new
    exemptions are all sites that exercise exactly the paths the widening added - the legacy-JSON
    round trip and the four tests that prove the escape hatches are closed. Widening a scan and
    then exempting production code would be the bad version of this; that is not what happened,
    and the next reader should check which kind an added exemption is.
    """
    exempt = sum(
        line.count(EXEMPT_MARKER)
        for path in _CORE.rglob("*.py")
        if "__pycache__" not in path.parts and path.name != "trust_gate.py"
        for line in path.read_text(encoding="utf-8").splitlines()
    )
    assert exempt <= 12, f"{exempt} trust-gate exemptions in packages/core — justify or remove"


def test_the_gate_skips_unparseable_and_vendored_trees(tmp_path: Path):
    (tmp_path / "site-packages").mkdir()
    (tmp_path / "site-packages" / "vendored.py").write_text(
        'SourcePointer(source_id="s", kind="table_cell", table="t", column="c", row_id="1")',
        encoding="utf-8")
    (tmp_path / "broken.py").write_text("def (:", encoding="utf-8")
    assert scan([tmp_path]) == []


# --- the bypasses R-003 finding 6 listed as invisible -----------------------------------------
# Each of these was a two-character walk around the gate. They are caught now. The point is NOT
# that the scan is now complete - it cannot be, and the module docstring says so - it is that
# the ordinary ways of not being seen no longer work.

def _write(tmp_path: Path, body: str) -> Path:
    f = tmp_path / "case.py"
    f.write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


def test_a_name_rebinding_no_longer_hides_a_site(tmp_path: Path):
    """`SP = SourcePointer; SP(...)` - the cheapest bypass there was."""
    root = _write(tmp_path, '''
        from databridge_core import SourcePointer
        SP = SourcePointer
        p = SP(source_id="s", kind="table_cell", table="t", column="c", row_id="1")
    ''')
    violations = scan([root])
    assert len(violations) == 1 and violations[0].reason == "OMITTED"


def test_an_import_alias_no_longer_hides_a_site(tmp_path: Path):
    root = _write(tmp_path, '''
        from databridge_core import SourcePointer as SP
        p = SP(source_id="s", kind="table_cell", table="t", column="c", row_id="1")
    ''')
    assert len(scan([root])) == 1


def test_a_transitive_rebinding_is_followed(tmp_path: Path):
    root = _write(tmp_path, '''
        from databridge_core import SourcePointer
        A = SourcePointer
        B = A
        p = B(source_id="s", kind="table_cell", table="t", column="c", row_id="1")
    ''')
    assert len(scan([root])) == 1


def test_model_validate_on_a_dict_literal_is_read(tmp_path: Path):
    """The path the old docstring wrongly claimed runtime validators covered."""
    root = _write(tmp_path, '''
        from databridge_core import SourcePointer
        p = SourcePointer.model_validate({"source_id": "s", "kind": "table_cell",
                                          "table": "t", "column": "c", "row_id": "1"})
    ''')
    violations = scan([root])
    assert len(violations) == 1 and violations[0].reason == "OMITTED"


def test_model_validate_naming_trust_is_accepted(tmp_path: Path):
    root = _write(tmp_path, '''
        from databridge_core import SourcePointer
        p = SourcePointer.model_validate({"source_id": "s", "kind": "table_cell",
                                          "table": "t", "column": "c", "row_id": "1",
                                          "trust": "trusted"})
    ''')
    assert scan([root]) == []


def test_an_opaque_argument_is_reported_rather_than_assumed_innocent(tmp_path: Path):
    """Same rule as SPLAT: "we cannot tell" must not read as "it is fine"."""
    root = _write(tmp_path, '''
        from databridge_core import SourcePointer
        p = SourcePointer.model_validate(payload)
        q = SourcePointer.model_validate_json(blob)
    ''')
    violations = scan([root])
    assert len(violations) == 2
    assert {v.reason for v in violations} == {"UNSCANNABLE"}


def test_model_construct_is_still_reported_even_though_it_now_raises(tmp_path: Path):
    """It is refused at runtime, but the build should fail on the call site rather than on
    whichever test happens to execute it."""
    root = _write(tmp_path, '''
        from databridge_core import SourcePointer
        p = SourcePointer.model_construct(source_id="s", kind="table_cell")
    ''')
    assert len(scan([root])) == 1


def test_functools_partial_is_reported_unless_it_binds_the_label(tmp_path: Path):
    root = _write(tmp_path, '''
        import functools
        from databridge_core import SourcePointer
        make = functools.partial(SourcePointer, kind="table_cell")
    ''')
    assert len(scan([root])) == 1
    root = _write(tmp_path, '''
        import functools
        from databridge_core import Trust, SourcePointer
        make = functools.partial(SourcePointer, kind="table_cell", trust=Trust.UNTRUSTED)
    ''')
    assert scan([root]) == []


def test_the_scan_does_not_fire_on_unrelated_calls(tmp_path: Path):
    """A gate that cries wolf gets an ignore rule written for it within the week."""
    root = _write(tmp_path, '''
        import json
        from databridge_core import GroundedValue
        x = json.loads("{}")
        y = GroundedValue.model_validate({"value": 1})
        z = dict(trust=None)
    ''')
    assert scan([root]) == []


def test_the_docstring_no_longer_claims_a_runtime_backstop_it_does_not_have(tmp_path: Path):
    """R-003 finding 6: the module used to say the runtime validators defended the paths this
    scan cannot see. They do not - `trust` is optional, so an omission validates fine. If that
    sentence comes back, this fails."""
    import databridge_core.trust_gate as gate
    doc = gate.__doc__ or ""
    assert "enforces the MIGRATION" in doc
    assert "cannot be a security control" in doc
