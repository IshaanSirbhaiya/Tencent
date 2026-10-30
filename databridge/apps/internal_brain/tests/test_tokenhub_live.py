"""IB_TOKENHUB_LIVE: a configured but NOT live TokenHub is never tried, and never called LIVE.

The launchers (scripts/demo_up.sh, and demo_day.ps1 through it) set ``IB_TOKENHUB_LIVE`` from the
real ``scripts/hy3_check.py`` result. ``bootstrap.select_chain`` builds TokenHub only when it is 1
(unset: a configured key, as before); else the opt-in stand-in; else none (quoted answers).
FAKE clients only: no network, no spend.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from test_deliverables import _ask, _c, _tag_of, _world
from test_views_composer import FakeClaude

ROOT = Path(__file__).resolve().parents[3]                     # databridge/


@pytest.fixture
def tokenhub_key_set(monkeypatch):
    """A TokenHub key is configured (as on the laptop); any TokenHub use fails the test."""
    import databridge_harness
    from databridge_ib.llm import ModelChain
    monkeypatch.setenv("TOKENHUB_API_KEY", "th-test-not-real")
    # set (not deleted) so that _prepare_env's setdefault never leaks into other tests
    monkeypatch.setenv("OPENAI_API_KEY", "th-test-not-real")
    monkeypatch.setenv("OPENAI_API_BASE", "https://tokenhub.invalid.tencentcloudmaas.com/v1")
    monkeypatch.setenv("IB_FORCE_OFFLINE", "0")
    calls: list[str] = []
    real = ModelChain.tokenhub.__func__

    def guarded(cls, *a, **kw):
        calls.append("built")
        return real(cls, *a, **kw)

    def no_call(*_a, **_kw):
        pytest.fail("a TokenHub model call was attempted")
    monkeypatch.setattr(ModelChain, "tokenhub", classmethod(guarded))
    monkeypatch.setattr(databridge_harness.LiteLLMBackend, "generate", no_call)
    return calls


def _reply(kind, text):
    tag = _tag_of(text, "SME pricing by market")
    return f"Indonesian SME customers are priced at a 0.4% FX margin [{tag}]."


# (a) TokenHub key set, not live, stand-in opted in: the stand-in answers, TokenHub never tried
def test_not_live_tokenhub_with_standin_answers_with_the_standin(tmp_path, monkeypatch, tokenhub_key_set):
    from databridge_ib.bootstrap import select_chain
    monkeypatch.setenv("IB_TOKENHUB_LIVE", "0")
    monkeypatch.setenv("IB_CLAUDE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    fake = FakeClaude(_reply)
    chain = select_chain(tmp_path / "views", standin_client=fake)
    assert chain is not None and chain.provider == "standin" and list(chain.models) == ["standin-1"]
    assert tokenhub_key_set == []                                  # not even built
    app, svc = _world(tmp_path / "w", chain=chain)
    jd = _c(app, svc, "jdoe")
    ans = _ask(jd)
    assert ans["model"]["model"] == "standin-1" and ans["model"]["provider"] == "standin"
    assert fake.calls                                              # the stand-in really answered
    m = jd.get("/ib/status").json()["models"]
    assert m["active_provider"] == "standin" and m["primary_live"] is False


# (b) the same with the stand-in off: quoted (extractive), TokenHub never tried
def test_not_live_tokenhub_without_standin_is_extractive(tmp_path, monkeypatch, tokenhub_key_set):
    from databridge_ib.bootstrap import select_chain
    monkeypatch.setenv("IB_TOKENHUB_LIVE", "0")
    monkeypatch.delenv("IB_CLAUDE", raising=False)
    chain = select_chain(tmp_path / "views")
    assert chain is None and tokenhub_key_set == []
    app, svc = _world(tmp_path / "w", chain=chain)
    jd = _c(app, svc, "jdoe")
    assert _ask(jd)["model"]["model"] == "extractive-v1"
    m = jd.get("/ib/status").json()["models"]
    assert m["active"] is False and m["active_provider"] is None


# (c) IB_TOKENHUB_LIVE=1: the TokenHub chain (built, not called); unset: as before (a key is used)
@pytest.mark.parametrize("live", ["1", None])
def test_live_tokenhub_is_the_chain(tmp_path, monkeypatch, tokenhub_key_set, live):
    from databridge_ib.bootstrap import select_chain
    if live is None:
        monkeypatch.delenv("IB_TOKENHUB_LIVE", raising=False)
    else:
        monkeypatch.setenv("IB_TOKENHUB_LIVE", live)
    monkeypatch.setenv("IB_CLAUDE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    chain = select_chain(tmp_path / "views", standin_client=FakeClaude(_reply))
    assert chain is not None and chain.provider == "tencent-tokenhub"
    assert list(chain.models)[0] == "hy3" and tokenhub_key_set == ["built"]


def test_force_offline_still_wins(tmp_path, monkeypatch, tokenhub_key_set):
    from databridge_ib.bootstrap import select_chain
    monkeypatch.setenv("IB_FORCE_OFFLINE", "1")
    monkeypatch.setenv("IB_TOKENHUB_LIVE", "1")
    monkeypatch.setenv("IB_CLAUDE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    assert select_chain(tmp_path / "views") is None


# (d) the preflight's model line: LIVE only for an active TokenHub chain that is live
def _preflight():
    spec = importlib.util.spec_from_file_location("demo_preflight_live", ROOT / "scripts" / "demo_preflight.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("models,live", [
    ({"active": True, "active_provider": "tencent-tokenhub", "active_chain": ["hy3"], "primary_live": True}, True),
    ({"active": True, "active_provider": "tencent-tokenhub", "active_chain": ["hy3"], "primary_live": False}, False),
    ({"active": True, "active_provider": "tencent-tokenhub", "active_chain": ["hy3"], "primary_live": None}, False),
    ({"active": True, "active_chain": ["hy3"], "primary_live": True}, False),          # provider not stated
    ({"active": True, "active_provider": "standin", "active_chain": ["standin-1"], "primary_live": True}, False),
    ({"active": True, "active_provider": "openai-compatible", "active_chain": ["x"], "primary_live": True}, False),
    ({"active": False, "active_provider": None, "active_chain": [], "primary_live": True}, False),
])
def test_model_line_says_live_only_for_live_tokenhub(models, live):
    info, (label, _status, detail) = _preflight().model_line({"models": models}, offline=False)
    assert info["live"] is live
    assert ("LIVE" in label) is live
    if not live:
        assert not re.search(r"hy3 lines are allowed", label + detail, re.I)
        assert label in ("Model: AI model active", "Model: Quoted from sources (no model)")
        assert label == ("Model: AI model active" if models["active"] else "Model: Quoted from sources (no model)")


def test_warm_up_answers_that_are_all_quoted_take_live_back():
    pf = _preflight()
    models = {"active": True, "active_provider": "tencent-tokenhub", "active_chain": ["hy3", "deepseek-v4.1-flash"],
              "primary_live": True}
    info, _ = pf.model_line({"models": models}, offline=False)
    assert pf.warm_confirms(dict(info), ["hy3", "extractive-v1"]) is None
    down = dict(info)
    line = pf.warm_confirms(down, ["extractive-v1", "extractive-v1"])
    assert line and "LIVE" not in line[0] and down["live"] is False


# (e) the launchers pass IB_TOKENHUB_LIVE and keep the Brain online for an opted-in stand-in
def test_demo_up_exports_tokenhub_live_and_never_forces_offline_for_the_standin():
    sh = (ROOT / "scripts" / "demo_up.sh").read_text(encoding="utf-8")
    assert re.search(r"^export IB_FORCE_OFFLINE IB_TOKENHUB_LIVE ", sh, re.M)
    assert re.search(r'^SETTLED_KEYS="IB_FORCE_OFFLINE IB_TOKENHUB_LIVE ', sh, re.M)   # reaches the Brain
    assert re.search(r'scripts/hy3_check\.py --quiet; then IB_TOKENHUB_LIVE=1; fi', sh)
    # the only place that sets IB_FORCE_OFFLINE=1 is the branch where neither model can answer
    block = sh[sh.index("IB_TOKENHUB_LIVE=0\n"):sh.index("export IB_FORCE_OFFLINE IB_TOKENHUB_LIVE")]
    sets_one = [ln for ln in block.splitlines() if re.search(r"\bIB_FORCE_OFFLINE=1\b", ln) and "!=" not in ln]
    assert len(sets_one) == 1
    before = block[:block.index(sets_one[0])]
    assert 'elif [ "$(standin_wanted)" = on ]' in before and "IB_FORCE_OFFLINE=0" in before
    # standin_wanted no longer turns off just because a TokenHub key is set
    fn = re.search(r"^standin_wanted\(\) \{.*?^\}", sh, re.M | re.S).group(0)
    assert "TOKENHUB_API_KEY" not in fn and "OPENAI_API_KEY" not in fn


def test_demo_day_hands_tokenhub_live_to_demo_up_and_never_forces_offline():
    ps = (ROOT / "scripts" / "demo_day.ps1").read_text(encoding="utf-8")
    assert "IB_TOKENHUB_LIVE" in ps
    assert "Remove-Item Env:IB_TOKENHUB_LIVE" in ps            # demo_up.sh decides it from hy3_check
    assert not re.search(r"\$env:IB_FORCE_OFFLINE\s*=\s*['\"]?1", ps)
    assert not re.search(r"IB_FORCE_OFFLINE=1", ps)
    assert "$r.model.live -eq $true" in ps


# the guard on the laptop's FIRST run: no guard dir yet -> created fresh, calls allowed, spend 0
def test_model_guard_first_run_creates_the_dir_and_allows_calls(tmp_path):
    from databridge_ib.model_guard import Caps, ModelGuard
    d = tmp_path / "databridge" / "var" / "model_guard"          # not even var/ exists yet
    assert not d.parent.exists()
    g = ModelGuard(d, Caps())
    assert d.is_dir() and (d / "spend.json").is_file()
    st = g.status()
    assert st["ok"] is True and st["available"] is True
    assert float(st["spent_usd"]) == 0.0 and int(st["calls"]) == 0
    ticket, why = g.admit("jdoe", "answer", 0.05)
    assert ticket is not None and why == "none"
    g.settle(ticket, 0.01)
    assert int(ModelGuard(d, Caps()).status()["calls"]) == 1      # a restart reads the same ledger
