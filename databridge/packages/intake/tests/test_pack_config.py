"""The determination rules come from the ACTIVE PACK, not from a path inside this package.

Phase 1B moved `coverage_rules.yaml`, the questionnaire schemas, the assistant persona and the
time baseline out of `packages/intake/databridge_intake/config/` and into
`packs/<pack>/config/` — a peer of the `ontology.yaml` that declares the vocabulary they are
written in. The pipeline mechanics did not move; only the rows they read.

Three things are worth a test rather than a convention:

1. **The location is computed.** No module names a pack. `active_pack()` derives it from the
   deployment binding, so a finance deployment reads finance rules by binding finance.
2. **An ambiguous binding is refused.** Two packs shipping rules with nothing to choose between
   them must fail loudly. Taking the first would make which table the front desk bills against
   depend on the order of a list in a YAML file.
3. **The two readers agree.** This package may not import `databridge_graph` (the seam
   `test_packages_intake_never_imports_the_graph` pins), so the deployment binding is read twice,
   in two packages. They must answer identically or the vocabulary and the rules drift apart.
"""
from __future__ import annotations

import pytest
import yaml

from databridge_intake import pack as packcfg
from databridge_intake.paths import config_dir, package_root


@pytest.fixture(autouse=True)
def _unbound():
    """Every test starts from the deployment binding, and leaves it that way."""
    packcfg.bind_pack(None)
    yield
    packcfg.bind_pack(None)


def test_the_rules_live_in_a_pack_not_in_this_package():
    resolved = config_dir().resolve()
    assert resolved.is_dir(), f"{resolved} does not exist"
    assert resolved.parent.parent == packcfg.packs_root().resolve(), (
        f"{resolved} is not packs/<pack>/config — the rules must live beside the ontology")
    assert package_root().resolve() not in resolved.parents, (
        "the rule tables are back inside packages/intake; a vertical's config is a pack's")


def test_every_table_the_pipeline_reads_is_in_the_pack():
    """Named individually so a table that fails to move shows up here, not as a 500 at runtime."""
    root = config_dir()
    for rel in ("coverage_rules.yaml", "assistant.yaml", "time_baseline.yaml",
                "questionnaires"):
        assert (root / rel).exists(), f"{rel} is not in the active pack's config/"


def test_no_module_in_this_package_names_a_pack():
    """The location is computed. A literal pack name anywhere in the source is the hardcode this
    whole workstream exists to remove."""
    names = set(packcfg.available_pack_configs())
    assert names, "no pack on disk ships a config/ directory"
    offenders = []
    for py in sorted(package_root().rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        for name in names:
            # `pack.py` documents the mechanism; it still may not *name* a pack.
            if f'"{name}"' in text or f"'{name}'" in text:
                offenders.append(f"{py.name}: {name}")
    assert not offenders, f"module(s) name a pack as a literal: {offenders}"


def test_an_unbound_pack_is_refused_not_guessed(tmp_path, monkeypatch):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "deployment.yaml").write_text("enabled_packs: [alpha]\n", encoding="utf-8")
    monkeypatch.setenv("DATABRIDGE_PACKS_ROOT", str(tmp_path))
    monkeypatch.delenv("DATABRIDGE_PACKS", raising=False)
    monkeypatch.delenv("DATABRIDGE_PACK", raising=False)
    with pytest.raises(packcfg.PackConfigError, match="ships a config"):
        packcfg.active_pack()


def test_two_packs_with_rules_are_refused_until_one_is_named(tmp_path, monkeypatch):
    for name in ("alpha", "beta"):
        (tmp_path / name / "config").mkdir(parents=True)
    (tmp_path / "deployment.yaml").write_text("enabled_packs: [alpha, beta]\n", encoding="utf-8")
    monkeypatch.setenv("DATABRIDGE_PACKS_ROOT", str(tmp_path))
    monkeypatch.delenv("DATABRIDGE_PACKS", raising=False)
    monkeypatch.delenv("DATABRIDGE_PACK", raising=False)
    with pytest.raises(packcfg.PackConfigError, match="DATABRIDGE_PACK"):
        packcfg.active_pack()
    monkeypatch.setenv("DATABRIDGE_PACK", "beta")
    assert packcfg.active_pack() == "beta"
    assert packcfg.pack_config_dir() == tmp_path / "beta" / "config"


def test_a_pack_the_deployment_does_not_enable_is_refused(tmp_path, monkeypatch):
    (tmp_path / "alpha" / "config").mkdir(parents=True)
    (tmp_path / "gamma" / "config").mkdir(parents=True)
    (tmp_path / "deployment.yaml").write_text("enabled_packs: [alpha]\n", encoding="utf-8")
    monkeypatch.setenv("DATABRIDGE_PACKS_ROOT", str(tmp_path))
    monkeypatch.delenv("DATABRIDGE_PACKS", raising=False)
    monkeypatch.setenv("DATABRIDGE_PACK", "gamma")
    with pytest.raises(packcfg.PackConfigError, match="not enabled"):
        packcfg.active_pack()


def test_bind_pack_scopes_and_restores(tmp_path, monkeypatch):
    for name in ("alpha", "beta"):
        (tmp_path / name / "config").mkdir(parents=True)
    (tmp_path / "deployment.yaml").write_text("enabled_packs: [alpha, beta]\n", encoding="utf-8")
    monkeypatch.setenv("DATABRIDGE_PACKS_ROOT", str(tmp_path))
    monkeypatch.delenv("DATABRIDGE_PACKS", raising=False)
    monkeypatch.delenv("DATABRIDGE_PACK", raising=False)
    with packcfg.bound_packs(["alpha"]) as name:
        assert name == "alpha"
    with pytest.raises(packcfg.PackConfigError):
        packcfg.active_pack()


def test_pack_binding_matches_the_graph_loader():
    """The seam forbids importing the graph from `databridge_intake`, so the binding is read in
    two places. Pin that they agree — a change to one that is not made to the other fails here
    rather than silently billing against another pack's table."""
    from databridge_graph.packs import configured_packs, packs_root

    assert packcfg.packs_root().resolve() == packs_root().resolve()
    assert packcfg.enabled_packs() == configured_packs()
    assert packcfg.active_pack() in configured_packs()


def test_adding_an_insurer_is_still_a_yaml_change_in_the_pack():
    """The rule from CLAUDE.md §Hard constraints 3, now reached through the pack: the issuer
    catalogue is data in `packs/<pack>/config/coverage_rules.yaml`, and the loader reads whatever
    is there."""
    from databridge_intake.rules import load_rules

    data = yaml.safe_load((config_dir() / "coverage_rules.yaml").read_text(encoding="utf-8"))
    before = load_rules()
    assert len(before.issuers) == len(data["issuers"])
    assert all(i.code in {row["code"] for row in data["issuers"]} for i in before.issuers)
