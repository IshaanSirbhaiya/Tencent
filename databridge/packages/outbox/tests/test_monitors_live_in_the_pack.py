"""HC1 (2026-10-01), SWEEP hardcode audit #11: the monitor table is a pack's rules, read from the
active pack's `config/` directory — the outbox engine package ships no rows of its own."""
from __future__ import annotations

import pathlib

import databridge_outbox
from databridge_intake.paths import config_dir as pack_config_dir
from databridge_outbox import monitors as monitors_module
from databridge_outbox.monitors import MONITORS_SOURCE_ID, monitors, monitors_path


def test_the_monitor_table_is_read_from_the_active_pack():
    path = monitors_path()
    assert path.parent.resolve() == pack_config_dir().resolve()
    assert path.name == MONITORS_SOURCE_ID
    assert len(monitors().monitors) >= 1


def test_the_engine_package_ships_no_rule_table():
    pkg = pathlib.Path(databridge_outbox.__file__).resolve().parent
    shipped = sorted(p.relative_to(pkg).as_posix() for p in pkg.rglob("*.yaml"))
    assert shipped == [], f"the outbox engine ships rule tables: {shipped}"
    # Non-vacuous: the scan would have seen a YAML file had one been there.
    assert any(pkg.rglob("*.py"))


def test_the_cached_table_follows_the_file(tmp_path, monkeypatch):
    """`monitors()` is keyed by the file it read, so a rebinding is not answered from the old
    pack's table."""
    src = monitors_path().read_text(encoding="utf-8")
    alt = tmp_path / MONITORS_SOURCE_ID
    alt.write_text(src.replace("version: 1", "version: 2", 1), encoding="utf-8")
    first = monitors().version
    monkeypatch.setattr(monitors_module, "monitors_path", lambda: alt)
    assert monitors().version != first
