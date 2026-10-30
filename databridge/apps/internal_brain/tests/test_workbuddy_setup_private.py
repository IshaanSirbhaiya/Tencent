"""Round 4 Codex B7 and B10, round 5 Codex #4 and #5: ``workbuddy_setup.py`` writes token files
that are born inside a verified one-account folder and written through one descriptor, refuses when the restriction cannot be applied and verified, and never writes through a
symlink or junction. Runs on temporary folders only; tokens are fake (``mint_for`` is replaced, so
no key file is read) and WorkBuddy is never started."""
from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "workbuddy_setup.py"
WIN = os.name == "nt"


def _load():
    spec = importlib.util.spec_from_file_location("workbuddy_setup_private_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _leftovers(folder: Path) -> list[str]:
    """Temporary files or private folders left behind (both end in .tmp)."""
    return sorted(p.name for p in folder.iterdir() if p.name.endswith(".tmp"))


def _junction(link: Path, target: Path) -> bool:
    if not WIN:
        return False
    import _winapi
    try:
        _winapi.CreateJunction(str(target), str(link))
    except OSError:
        return False
    return True


# ------------------------------------------------------------------------------ restriction first
def test_file_is_born_inside_a_verified_private_folder_and_never_reopened(tmp_path, monkeypatch):
    """Round 5 Codex #4, #5: the private folder is restricted and VERIFIED before the token file
    exists; the file is created once, exclusively, inside that folder and never opened again by
    name; the folder is gone afterwards."""
    ws = _load()
    events: list[tuple] = []
    real_run, real_open = ws.subprocess.run, ws.os.open
    real_private = ws._private_dir

    def run(cmd, **kw):
        events.append(("icacls", Path(cmd[1]), tuple(cmd[2:])))
        return real_run(cmd, **kw)

    def opener(path, flags, *a, **kw):
        events.append(("open", Path(path), flags))
        return real_open(path, flags, *a, **kw)

    def private(parent):
        d = real_private(parent)
        assert list(d.iterdir()) == []                                 # empty when handed over
        events.append(("private", d))
        return d
    monkeypatch.setattr(ws.subprocess, "run", run)
    monkeypatch.setattr(ws.os, "open", opener)
    monkeypatch.setattr(ws, "_private_dir", private)
    target = tmp_path / ".workbuddy" / "mcp.json"
    target.parent.mkdir()
    ws._write_private(target, '{"IB_TOKEN": "fake"}', root=tmp_path)

    priv = next(e[1] for e in events if e[0] == "private")
    opens = [e for e in events if e[0] == "open" and e[1].parent == priv]
    assert len(opens) == 1 and opens[0][1].name == "mcp.json"         # created once, never reopened
    flags = opens[0][2]
    assert flags & os.O_CREAT and flags & os.O_EXCL and flags & os.O_WRONLY
    i_open = events.index(opens[0])
    assert events.index(("private", priv)) < i_open                    # folder verified first
    if WIN:
        dir_calls = [e for e in events[:i_open] if e[0] == "icacls" and e[1] == priv]
        assert any("/inheritance:r" in e[2] for e in dir_calls)
        assert dir_calls[-1][2] == ()                                  # last step: the verify listing
    assert target.read_text(encoding="utf-8") == '{"IB_TOKEN": "fake"}'
    assert not priv.exists() and _leftovers(target.parent) == []


@pytest.mark.skipif(not WIN, reason="icacls is Windows-only")
def test_real_icacls_private_folder_is_only_the_current_account(tmp_path):
    ws = _load()
    d = ws._private_dir(tmp_path)
    try:
        out = subprocess.run(["icacls", str(d)], capture_output=True, check=False).stdout
        assert ws._only_owner(out, d)
        assert b"(I)" not in out and b"SYSTEM" not in out and b"Administrators" not in out
        f = d / "x"
        fd = os.open(f, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)   # born with the folder's ACL
        os.close(fd)
        born = subprocess.run(["icacls", str(f)], capture_output=True, check=False).stdout
        assert born.count(b":(") == 1 and b"(I)(F)" in born            # one inherited entry: this account
    finally:
        ws._remove_private(d)
    assert not d.exists()


def test_a_planted_file_with_the_same_name_is_never_written(tmp_path, monkeypatch):
    """If the token file's name already exists in the private folder, the exclusive create fails:
    refused, nothing published, the planted file is not written to."""
    ws = _load()
    real_private = ws._private_dir

    def planted(parent):
        d = real_private(parent)
        (d / "mcp.json").write_text("attacker", encoding="utf-8")
        return d
    monkeypatch.setattr(ws, "_private_dir", planted)
    target = tmp_path / "mcp.json"
    with pytest.raises(ws.Refused):
        ws._write_private(target, '{"IB_TOKEN": "fake"}', root=tmp_path)
    assert not target.exists() and _leftovers(tmp_path) == []


def test_a_swapped_file_before_publish_is_refused(tmp_path, monkeypatch):
    """If the name stops pointing at the file that was written (a swap after close), refused."""
    ws = _load()
    real_owner = ws._owner_only

    def swap(path):
        real_owner(path)
        os.replace(path, Path(str(path) + ".gone"))
        Path(path).write_text("attacker", encoding="utf-8")
    monkeypatch.setattr(ws, "_owner_only", swap)
    target = tmp_path / "mcp.json"
    with pytest.raises(ws.Refused):
        ws._write_private(target, '{"IB_TOKEN": "fake"}', root=tmp_path)
    assert not target.exists() and _leftovers(tmp_path) == []


def test_a_file_acl_failure_refuses_and_cleans_up(tmp_path, monkeypatch):
    ws = _load()

    def bad(path):
        raise ws.Refused("file ACL not verified")
    monkeypatch.setattr(ws, "_owner_only", bad)
    target = tmp_path / "mcp.json"
    with pytest.raises(ws.Refused):
        ws._write_private(target, '{"IB_TOKEN": "fake"}', root=tmp_path)
    assert not target.exists() and _leftovers(tmp_path) == []


@pytest.mark.skipif(not WIN, reason="icacls is Windows-only")
def test_real_icacls_leaves_only_the_current_account(tmp_path):
    ws = _load()
    target = tmp_path / "mcp.json"
    ws._write_private(target, "{}", root=tmp_path)
    out = subprocess.run(["icacls", str(target)], capture_output=True, check=False).stdout
    assert ws._only_owner(out, target)
    assert b"(I)" not in out                                          # no inherited entry


@pytest.mark.skipif(WIN, reason="POSIX mode bits")
def test_posix_file_is_0600_and_folder_0700(tmp_path):
    ws = _load()
    d = ws._private_dir(tmp_path)
    assert (d.stat().st_mode & 0o777) == 0o700
    ws._remove_private(d)
    target = tmp_path / "mcp.json"
    ws._write_private(target, "{}", root=tmp_path)
    assert (target.stat().st_mode & 0o777) == 0o600


# ------------------------------------------------------------------------------ icacls failures
class _Run:
    """Fake ``subprocess.run`` for icacls: the grant's return code and the listing it prints."""

    def __init__(self, grant_rc: int = 0, listing: bytes | None = None):
        self.grant_rc, self.listing, self.calls = grant_rc, listing, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if "/grant:r" in cmd:
            return subprocess.CompletedProcess(cmd, self.grant_rc, b"", b"")
        listing = self.listing if self.listing is not None else (
            f"{cmd[1]} {os.environ.get('USERDOMAIN', 'PC')}\\{os.environ.get('USERNAME', 'me')}:(F)"
            "\r\n\r\nSuccessfully processed 1 files; Failed processing 0 files\r\n").encode()
        return subprocess.CompletedProcess(cmd, 0, listing, b"")


def _as_windows(ws, monkeypatch, run):
    monkeypatch.setattr(ws, "_WINDOWS", True)
    monkeypatch.setenv("USERNAME", "me")
    monkeypatch.setenv("USERDOMAIN", "PC")
    monkeypatch.setattr(ws.subprocess, "run", run)


def test_icacls_failure_refuses_and_publishes_nothing(tmp_path, monkeypatch):
    ws = _load()
    run = _Run(grant_rc=5)
    _as_windows(ws, monkeypatch, run)
    target = tmp_path / "mcp.json"
    with pytest.raises(ws.Refused):
        ws._write_private(target, '{"IB_TOKEN": "fake"}', root=tmp_path)
    assert not target.exists() and _leftovers(tmp_path) == []
    assert run.calls and "/grant:r" in run.calls[0]


def test_a_wider_acl_after_icacls_refuses_and_publishes_nothing(tmp_path, monkeypatch):
    """icacls said yes, but the folder's listing shows another principal: not verified, so
    refused before any token file exists."""
    ws = _load()

    def wide(cmd, **kw):
        if "/grant:r" in cmd:
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        return subprocess.CompletedProcess(
            cmd, 0, f"{cmd[1]} PC\\me:(F)\r\n    Everyone:(R)\r\n\r\n".encode(), b"")
    _as_windows(ws, monkeypatch, wide)
    target = tmp_path / "mcp.json"
    with pytest.raises(ws.Refused):
        ws._write_private(target, '{"IB_TOKEN": "fake"}', root=tmp_path)
    assert not target.exists() and _leftovers(tmp_path) == []


def test_mocked_icacls_success_publishes(tmp_path, monkeypatch):
    ws = _load()
    run = _Run()
    _as_windows(ws, monkeypatch, run)
    target = tmp_path / "mcp.json"
    ws._write_private(target, "{}", root=tmp_path)
    assert target.read_text(encoding="utf-8") == "{}" and _leftovers(tmp_path) == []
    # folder: restrict, verify; file: make its one entry explicit, verify
    assert [c[2:3] for c in run.calls] == [["/inheritance:r"], [], ["/inheritance:d"], []]


def test_only_owner_parses_icacls_listings(tmp_path):
    ws = _load()
    p = tmp_path / "x.json"
    ok = f"{p} PC\\me:(F)\r\n\r\nSuccessfully processed 1 files\r\n".encode()
    assert ws._only_owner(ok, p, who="PC\\me", user="me")
    assert ws._only_owner(f"{p} pc\\ME:(F)\r\n".encode(), p, who="PC\\me", user="me")
    for bad in (f"{p} PC\\me:(I)(F)\r\n", f"{p} PC\\me:(F)\r\n    Everyone:(R)\r\n",
                f"{p} PC\\other:(F)\r\n", "garbage\r\n", ""):
        assert not ws._only_owner(bad.encode(), p, who="PC\\me", user="me"), bad


def test_file_icacls_failure_refuses_and_publishes_nothing(tmp_path, monkeypatch):
    """The folder is fine but making the file's entry explicit fails: refused, nothing published."""
    ws = _load()
    inner = _Run()

    def run(cmd, **kw):
        if "/inheritance:d" in cmd:
            return subprocess.CompletedProcess(cmd, 5, b"", b"")
        return inner(cmd, **kw)
    _as_windows(ws, monkeypatch, run)
    target = tmp_path / "mcp.json"
    with pytest.raises(ws.Refused):
        ws._write_private(target, '{"IB_TOKEN": "fake"}', root=tmp_path)
    assert not target.exists() and _leftovers(tmp_path) == []


def test_main_exits_non_zero_when_icacls_fails(tmp_path, monkeypatch, capsys):
    ws = _load()
    monkeypatch.setattr(ws, "mint_for", lambda persona, hours: "fake-token")
    _as_windows(ws, monkeypatch, _Run(grant_rc=5))
    out = tmp_path / "wb"
    assert ws.main(["--out", str(out)]) != 0
    assert not list(out.rglob("mcp.json")) and not list(out.rglob("*.tmp"))


# ------------------------------------------------------------------------------ links and junctions
def test_a_symlinked_target_file_is_refused(tmp_path):
    ws = _load()
    outside = tmp_path / "outside.json"
    outside.write_text("keep", encoding="utf-8")
    folder = tmp_path / "ws"
    folder.mkdir()
    link = folder / "mcp.json"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create a symlink here")
    with pytest.raises(ws.Refused):
        ws._write_private(link, "secret", root=tmp_path)
    assert outside.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(not WIN, reason="junctions are Windows-only")
def test_a_junctioned_folder_is_refused(tmp_path):
    ws = _load()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    if not _junction(root / ".workbuddy", elsewhere):
        pytest.skip("cannot create a junction here")
    with pytest.raises(ws.Refused):
        ws._write_private(root / ".workbuddy" / "mcp.json", "secret", root=root)
    assert list(elsewhere.iterdir()) == []


@pytest.mark.skipif(not WIN, reason="junctions are Windows-only")
def test_main_refuses_a_workspace_junction_into_another_folder(tmp_path, monkeypatch):
    ws = _load()
    monkeypatch.setattr(ws, "mint_for", lambda persona, hours: "fake-token")
    out = tmp_path / "wb"
    folder = ws.WORKSPACES[0][0]
    (out / folder).mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    if not _junction(out / folder / ".workbuddy", elsewhere):
        pytest.skip("cannot create a junction here")
    assert ws.main(["--out", str(out)]) != 0
    assert list(elsewhere.iterdir()) == []


def test_a_path_outside_the_expected_folder_is_refused(tmp_path):
    ws = _load()
    (tmp_path / "a").mkdir()
    with pytest.raises(ws.Refused):
        ws._write_private(tmp_path / "a" / ".." / "b.json", "secret", root=tmp_path / "a")
    assert not (tmp_path / "b.json").exists()
