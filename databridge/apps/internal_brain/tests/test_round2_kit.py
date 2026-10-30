"""Round-2 review fixes in the demo kit scripts (imported by path; nothing here touches a network,
a server or a real output folder).

- scripts/hy3_check.py: only a real chat completion counts as live (Codex 2b #1);
- docs/finals/live_kit/check_absent.py --sim reads ``documents_containing`` (Opus #7);
- docs/finals/live_kit/templates/fill_template.py: --name is a plain file name inside out/, no
  overwrite without --force, CSV formula cells neutralised (Opus #13, Codex 2b #2).
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
DB = REPO / "databridge"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------------------------------------
# hy3_check
# ------------------------------------------------------------------------------------------------
class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.parametrize("reply,live", [
    ({"choices": [{"message": {"role": "assistant", "content": "OK"}}]}, True),
    ({}, False),
    ({"choices": []}, False),
    ({"choices": [{"message": {"content": ""}}]}, False),
    ({"choices": [{"message": {"content": "   "}}]}, False),
    ({"choices": [{"message": None}]}, False),
    ({"error": {"code": "401006", "message": "not activated"}}, False),
    ({"error": {"code": "x"}, "choices": [{"message": {"content": "OK"}}]}, False),
    ([], False),
])
def test_hy3_check_needs_a_real_completion(monkeypatch, reply, live):
    hy3 = _load(DB / "scripts" / "hy3_check.py", "hy3_check_under_test")
    monkeypatch.setattr(hy3.urllib.request, "urlopen",
                        lambda req, timeout=30: _Resp(json.dumps(reply).encode()))
    ok, why, _ = hy3.check("hy3", "k-not-real", "https://tokenhub.invalid/v1")
    assert ok is live, why
    if not live:
        assert why != "live"


def test_hy3_check_main_exits_1_on_an_http_200_error_envelope(monkeypatch, capsys):
    hy3 = _load(DB / "scripts" / "hy3_check.py", "hy3_check_main_under_test")
    monkeypatch.setattr(hy3, "_env", lambda: {"TOKENHUB_API_KEY": "k-not-real-123",
                                              "IB_MODELS": "hy3"})
    monkeypatch.setattr(hy3.urllib.request, "urlopen",
                        lambda req, timeout=30: _Resp(b'{"error": {"code": "401006"}}'))
    monkeypatch.setattr(sys, "argv", ["hy3_check.py"])
    assert hy3.main() == 1
    out = capsys.readouterr().out
    assert "not live" in out and "k-not-real-123" not in out


# ------------------------------------------------------------------------------------------------
# check_absent --sim
# ------------------------------------------------------------------------------------------------
def test_check_absent_sim_reads_documents_containing():
    ca = _load(REPO / "docs" / "finals" / "live_kit" / "check_absent.py", "check_absent_under_test")
    assert ca.sim_absent({"documents_containing": 0, "index_size": 412})[0] is True
    assert ca.sim_absent({"documents_containing": 2, "index_size": 412})[0] is False
    # the old field name, a missing count, or a non-integer count all fail closed
    for bad in ({"count": 0}, {}, {"documents_containing": None}, {"documents_containing": "0"},
                {"documents_containing": False}, None):
        assert ca.sim_absent(bad)[0] is False, bad


def test_check_absent_matches_the_sim_endpoint_field():
    src = (DB / "apps" / "internal_brain" / "databridge_ib" / "sim.py").read_text(encoding="utf-8")
    assert '"documents_containing": hits' in src


# ------------------------------------------------------------------------------------------------
# fill_template
# ------------------------------------------------------------------------------------------------
@pytest.fixture()
def ft():
    return _load(REPO / "docs" / "finals" / "live_kit" / "templates" / "fill_template.py",
                 "fill_template_under_test")


@pytest.mark.parametrize("name", ["../template_note.md", "..\\x.md", "/etc/passwd", "a/b.md",
                                  "C:\\x.md", "C:x.md", "..", ".", "", " x.md", "x..md"])
def test_fill_template_rejects_anything_but_a_plain_name(ft, tmp_path, name):
    with pytest.raises(ValueError):
        ft.safe_name(name, tmp_path / "out")


def test_fill_template_plain_name_stays_inside_out(ft, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    assert ft.safe_name("Audit calendar.md", out) == (out / "Audit calendar.md").resolve()


@pytest.mark.parametrize("cell,want", [
    ("=HYPERLINK(\"http://x\")", "'=HYPERLINK(\"http://x\")"),
    ("+1", "'+1"), ("-1", "'-1"), ("@SUM(A1)", "'@SUM(A1)"), ("\tx", "'\tx"), ("\rx", "'\rx"),
    ("  =1", "'  =1"), ("a, b", "a; b"), ("Vendor renewal", "Vendor renewal"),
])
def test_fill_template_csv_cells_are_never_formulas(ft, cell, want):
    assert ft.csv_cell(cell) == want


def test_fill_template_cli_refuses_traversal_and_overwrite(ft, tmp_path, monkeypatch, capsys):
    # run the CLI against a COPY of the templates so the real out/ folder is never written
    for f in ft.KINDS.values():
        (tmp_path / f).write_text((ft.HERE / f).read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(ft, "HERE", tmp_path)

    def run(*args):
        monkeypatch.setattr(sys, "argv", ["fill_template.py", *args])
        return ft.main()

    assert run("md", "The audit committee meets on 9 Dec 2026.", "--name", "../template_note.md") == 2
    assert "{{" in (tmp_path / "template_note.md").read_text(encoding="utf-8")   # untouched
    assert run("csv", "=cmd|' /C calc'!A0", "--name", "t.csv") == 0
    body = (tmp_path / "out" / "t.csv").read_text(encoding="utf-8")
    assert "'=cmd|' /C calc'!A0" in body and ",=cmd" not in body
    assert run("csv", "Second run.", "--name", "t.csv") == 1                      # no overwrite
    assert "Second run." not in (tmp_path / "out" / "t.csv").read_text(encoding="utf-8")
    assert run("csv", "Second run.", "--name", "t.csv", "--force") == 0
    assert "Second run." in (tmp_path / "out" / "t.csv").read_text(encoding="utf-8")


# ------------------------------------------------------------------------------------------------
# Round 4 (Opus web/kit review #9, #12)

@pytest.mark.parametrize("base,ok", [
    ("https://tokenhub-intl.tencentcloudmaas.com/v1", True),
    ("http://127.0.0.1:9999/v1", True), ("http://localhost:9999/v1", True), ("http://[::1]:9999/v1", True),
    ("http://tokenhub-intl.tencentcloudmaas.com/v1", False), ("http://10.0.0.5/v1", False),
    ("ftp://x/v1", False), ("tokenhub/v1", False), ("https://user:pw@tokenhub.example/v1", False),
])
def test_hy3_check_base_must_be_https_unless_loopback(base, ok):
    hy3 = _load(DB / "scripts" / "hy3_check.py", "hy3_check_base_under_test")
    assert (hy3.base_error(base) is None) is ok


def test_hy3_check_refuses_a_plain_http_base_before_sending_the_key(monkeypatch, capsys):
    hy3 = _load(DB / "scripts" / "hy3_check.py", "hy3_check_http_under_test")
    monkeypatch.setattr(hy3, "_env", lambda: {"TOKENHUB_API_KEY": "k-not-real-456",
                                              "TOKENHUB_BASE": "http://tokenhub.example/v1"})
    sent = []
    monkeypatch.setattr(hy3.urllib.request, "urlopen", lambda req, timeout=30: sent.append(req))
    monkeypatch.setattr(sys, "argv", ["hy3_check.py", "--quiet"])
    assert hy3.main() == 2
    assert sent == []
    cap = capsys.readouterr()
    assert "https" in cap.err and "k-not-real-456" not in cap.out + cap.err


@pytest.mark.parametrize("cell", [
    '"=HYPERLINK(""http://x"")"', '"+1', ' "-1', '"@SUM(A1)', '" =1',
    "＝1+1", "＋1", "－1", "＠SUM(A1)", " =1", "　＝1", ' "=1',
    "\t=1", "\r=1", "\tx", "\rx",
])
def test_fill_template_csv_guard_matches_the_server_guard(ft, cell):
    assert ft.csv_cell(cell).startswith("'"), cell


@pytest.mark.parametrize("cell", ['"quoted text"', "Vendor renewal", "Owner: Hana", "1+1"])
def test_fill_template_csv_guard_leaves_plain_text(ft, cell):
    assert not ft.csv_cell(cell).startswith("'"), cell


# check_absent --all --sim: /sim/check-absent allows 10 calls a minute (STAGE_QA_30OCT #6)

class _FakeClient:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def post(self, path, body):
        self.calls += 1
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _http_429(retry_after):
    import urllib.error
    from email.message import Message
    h = Message()
    if retry_after is not None:
        h["Retry-After"] = retry_after
    return urllib.error.HTTPError("http://127.0.0.1:8100/sim/check-absent", 429, "Too Many Requests", h, None)


@pytest.mark.parametrize("retry_after,want", [("7", 7.0), (None, 60.0), ("999", 65.0), ("soon", 60.0)])
def test_check_absent_sim_waits_and_retries_once_on_429(monkeypatch, capsys, retry_after, want):
    ca = _load(REPO / "docs" / "finals" / "live_kit" / "check_absent.py", "check_absent_retry_under_test")
    slept = []
    monkeypatch.setattr(ca, "_sleep", slept.append)
    c = _FakeClient([_http_429(retry_after), {"documents_containing": 0, "index_size": 9}])
    assert ca.sim_check(c, "phrase")["documents_containing"] == 0
    assert c.calls == 2 and slept == [want]
    assert f"waiting {want:.0f} s" in capsys.readouterr().out


def test_check_absent_sim_retries_only_once(monkeypatch):
    import urllib.error
    ca = _load(REPO / "docs" / "finals" / "live_kit" / "check_absent.py", "check_absent_retry1_under_test")
    monkeypatch.setattr(ca, "_sleep", lambda s: None)
    c = _FakeClient([_http_429("1"), _http_429("1")])
    with pytest.raises(urllib.error.HTTPError):
        ca.sim_check(c, "phrase")
    assert c.calls == 2
