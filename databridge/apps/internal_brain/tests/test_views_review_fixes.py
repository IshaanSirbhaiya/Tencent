"""The 4-seat review of the page canvas and the stand-in answer model, with FAKE clients only.

1. the stand-in model is opt-in (``IB_CLAUDE=1`` + a key) and never runs under IB_FORCE_OFFLINE=1;
2. nothing the browser sees (``/ib/status``, an answer, a page, a decision) names its provider;
3. write-ahead records before the extra documents are read and before ``/ib/rewrite`` calls a model;
4. view rows are counted apart from deliverables in the stats;
5. untagged page labels are grounded (no digits or names from the instruction);
6. the spend cap fails closed and never leaks a slot; the answer backend has a per-person limit.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from databridge_ib.views import composer as cmod

from test_deliverables import Q, _ask, _c, _events, _tag_of, _world
from test_views_composer import FakeClaude, _build, _decide, _page_reply, _with_claude

ROOT = Path(__file__).resolve().parents[3]                     # databridge/
PROVIDER_WORDS = re.compile(r"claude|anthropic|opus|sonnet|haiku|hunyuan|tokenhub|tencent", re.I)


@pytest.fixture(autouse=True)
def _flag(monkeypatch):
    monkeypatch.setenv("IB_DELIVERABLES", "1")


# ------------------------------------------------------------------------------------------------
# 1. opt-in
# ------------------------------------------------------------------------------------------------
def test_standin_is_off_by_default_even_with_a_key(tmp_path, monkeypatch):
    from databridge_ib.claude import claude_chain
    monkeypatch.delenv("IB_FORCE_OFFLINE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    assert cmod.claude_enabled() is False
    assert cmod.ClaudeComposer.from_env(tmp_path) is None and claude_chain(tmp_path) is None
    monkeypatch.setenv("IB_CLAUDE", "0")
    assert cmod.ClaudeComposer.from_env(tmp_path) is None and claude_chain(tmp_path) is None
    app, _svc = _world(tmp_path / "w")
    assert app.state.views.composer is None


def test_standin_is_off_under_force_offline(tmp_path, monkeypatch):
    from databridge_ib.claude import claude_chain
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("IB_CLAUDE", "1")
    monkeypatch.setenv("IB_FORCE_OFFLINE", "1")
    assert cmod.ClaudeComposer.from_env(tmp_path) is None and claude_chain(tmp_path) is None


def test_standin_is_on_only_with_the_switch_and_a_key(tmp_path, monkeypatch):
    from databridge_ib.claude import claude_chain
    monkeypatch.setenv("IB_FORCE_OFFLINE", "0")
    monkeypatch.setenv("IB_CLAUDE", "1")
    assert cmod.ClaudeComposer.from_env(tmp_path) is None              # no key
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    assert isinstance(cmod.ClaudeComposer.from_env(tmp_path), cmod.ClaudeComposer)
    chain = claude_chain(tmp_path)
    assert chain is not None and list(chain.models) == ["standin-1"] and chain.provider == "standin"


# ------------------------------------------------------------------------------------------------
# 2. no provider name reaches the browser
# ------------------------------------------------------------------------------------------------
def test_no_provider_name_in_status_answer_decision_or_page(tmp_path):
    from databridge_ib.claude import claude_chain

    def reply(kind, text):
        if kind == "decide":
            return json.dumps({"render": "page", "title": "FX margin by market", "reason": "table"})
        if "Design the page" in text:
            tag = _tag_of(text, "SME pricing by market")
            return json.dumps({"title": "FX margin by market", "accent": "green", "blocks": [
                {"type": "prose", "heading": "", "items": [
                    {"text": "Indonesian SME customers are priced at a 0.4% FX margin.",
                     "tags": [tag]}]}]})
        tag = _tag_of(text, "SME pricing by market")
        return f"Indonesian SME customers are priced at a 0.4% FX margin [{tag}]."
    chain = claude_chain(tmp_path / "views", client=FakeClaude(reply))
    app, svc = _world(tmp_path / "w", chain=chain)
    _with_claude(app, svc, reply)
    jd = _c(app, svc, "jdoe")
    st = jd.get("/ib/status").json()
    assert st["models"]["active"] is True and st["models"]["active_provider"] == "standin"
    assert st["views"]["composer"] == {"provider": "standin", "model": "standin-1",
                                       "available": True}
    ans = _ask(jd)
    assert ans["model"]["provider"] == "standin" and ans["model"]["model"] == "standin-1"
    d = _decide(jd, ans["request_id"], "Show the Indonesian SME FX margin by market as a table")
    v = _build(jd, ans["request_id"], "a table")
    assert d.json()["decided_by"] == "model" and v.json()["generated"] == "model"
    for body in (json.dumps(st), json.dumps(ans), d.text, v.text):
        assert not PROVIDER_WORDS.search(body), PROVIDER_WORDS.search(body)


def test_demo_up_keeps_standin_keys_in_the_brain_and_off_the_banner():
    sh = (ROOT / "scripts" / "demo_up.sh").read_text(encoding="utf-8")
    keys = re.search(r'^IB_ONLY_KEYS="([^"]+)"', sh, re.M).group(1).replace("\\\n", " ").split()
    for k in ("TOKENHUB_API_KEY", "ANTHROPIC_API_KEY", "IB_CLAUDE"):
        assert k in keys
    assert "unset TOKENHUB_API_KEY" not in sh and sh.count("unset $IB_ONLY_KEYS") == 5
    banner = sh[sh.index("Walnut DataBridge is up."):]
    assert "Claude" not in banner and "Anthropic" not in banner
