"""With the stand-in model ON (IB_CLAUDE=1, FAKE client), every label a person sees is neutral.

The browser keys off ``/ib/status``: ``models.active`` (any model) and ``models.active_provider``
("tencent-tokenhub" | "standin" | ...). The rules checked here:

1. ``/ib/status`` and the answer name only the neutral ``standin`` / ``standin-1`` (and the
   answer is never labelled extractive while the stand-in wrote it);
2. the preflight script says "AI model active" for the stand-in, Hy3 only for TokenHub, and
   "quoted (no model)" only when no model is active;
3. demo_up.sh turns the Brain online for the stand-in only when no TokenHub key is set (a configured
   TokenHub key takes precedence in the Brain, so a dead one would be labelled as Hy3);
4. the web sources: Hy3/TokenHub lines and labels key off ``active_provider === 'tencent-tokenhub'``,
   "no model" lines off ``!active``; the stand-in's labels are neutral in English and Chinese.
These are source checks for the web (Playwright is not run here): they prove the conditions in the
code, not a rendered page.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_deliverables import _ask, _c, _tag_of, _world
from test_views_composer import FakeClaude

ROOT = Path(__file__).resolve().parents[3]                     # databridge/
WEB = ROOT / "apps" / "internal_brain_web"
PROVIDER = re.compile(r"claude|anthropic|opus|sonnet|haiku", re.I)
HY3 = re.compile(r"hy3|hunyuan|tokenhub|混元", re.I)


def _read(rel: str) -> str:
    return (WEB / rel).read_text(encoding="utf-8")


# ------------------------------------------------------------------------------------------------
# 1. the API
# ------------------------------------------------------------------------------------------------
def test_status_and_answer_are_neutral_with_the_standin_on(tmp_path, monkeypatch):
    from databridge_ib.claude import claude_chain
    monkeypatch.setenv("IB_DELIVERABLES", "1")

    def reply(kind, text):
        tag = _tag_of(text, "SME pricing by market")
        return f"Indonesian SME customers are priced at a 0.4% FX margin [{tag}]."
    chain = claude_chain(tmp_path / "views", client=FakeClaude(reply))
    app, svc = _world(tmp_path / "w", chain=chain)
    jd = _c(app, svc, "jdoe")
    st = jd.get("/ib/status").json()
    m = st["models"]
    assert m["active"] is True and m["active_provider"] == "standin"
    assert m["active_chain"] == ["standin-1"]
    ans = _ask(jd)
    assert ans["model"] == {"provider": "standin", "model": "standin-1", "fallback_used": False}
    # models.chain is the configured TokenHub default (shown only when no chain is active); every
    # other field, and the answer, names neither the provider nor Hy3/TokenHub
    shown = {**st, "models": {k: v for k, v in m.items() if k != "chain"}}
    for body in (json.dumps(shown), json.dumps(ans)):
        assert not PROVIDER.search(body), PROVIDER.search(body)
        assert not HY3.search(body), HY3.search(body)


# ------------------------------------------------------------------------------------------------
# 2. the preflight script
# ------------------------------------------------------------------------------------------------
def _preflight():
    spec = importlib.util.spec_from_file_location("demo_preflight_labels",
                                                  ROOT / "scripts" / "demo_preflight.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("models,label,status,live,ai", [
    ({"active": True, "active_provider": "standin", "active_chain": ["standin-1"]},
     "Model: AI model active", "pass", False, True),
    ({"active": True, "active_provider": "tencent-tokenhub", "active_chain": ["hy3"]},
     "Model: Tencent Hunyuan LIVE via TokenHub", "pass", True, False),
    ({"active": False, "active_provider": None, "active_chain": []},
     "Model: extractive fallback (quoted from the sources)", "warn", False, False),
])
def test_preflight_model_line(models, label, status, live, ai):
    info, (lab, stat, detail) = _preflight().model_line({"models": models}, offline=False)
    assert (lab, stat, info["live"], info["ai"]) == (label, status, live, ai)
    assert not PROVIDER.search(lab + detail)
    if models["active"] and models["active_provider"] != "tencent-tokenhub":
        assert "quoted" not in (lab + detail).lower() and "no model" not in (lab + detail).lower()
        assert "standin" not in lab


# ------------------------------------------------------------------------------------------------
# 3. demo_up.sh: when the stand-in may answer
# ------------------------------------------------------------------------------------------------
def _standin_wanted(tmp_path: Path, env_file: str) -> str:
    sh = (ROOT / "scripts" / "demo_up.sh").read_text(encoding="utf-8")
    fn = re.search(r"^standin_wanted\(\) \{.*?^\}", sh, re.M | re.S).group(0)
    (tmp_path / ".env.ib.local").write_text(env_file, encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in ("IB_CLAUDE", "ANTHROPIC_API_KEY", "TOKENHUB_API_KEY", "OPENAI_API_KEY")}
    out = subprocess.run(["bash", "-c", fn + "\nstandin_wanted"], cwd=tmp_path, env=env,
                         capture_output=True, text=True, timeout=30)
    return out.stdout.strip()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("env_file,want", [
    ("IB_CLAUDE=1\nANTHROPIC_API_KEY=sk-test-not-real\n", "on"),
    ("IB_CLAUDE=0\nANTHROPIC_API_KEY=sk-test-not-real\n", "off"),
    ("IB_CLAUDE=1\n", "off"),
    # a TokenHub key takes precedence in the Brain: never force online for a dead TokenHub chain
    ("IB_CLAUDE=1\nANTHROPIC_API_KEY=sk-test-not-real\nTOKENHUB_API_KEY=x\n", "off"),
])
def test_demo_up_forces_online_for_the_standin_only_without_tokenhub(tmp_path, env_file, want):
    assert _standin_wanted(tmp_path, env_file) == want


def test_demo_up_uses_standin_wanted_after_the_hy3_check():
    sh = (ROOT / "scripts" / "demo_up.sh").read_text(encoding="utf-8")
    assert re.search(r'hy3_check\.py --quiet; then IB_FORCE_OFFLINE=0\n\s*elif \[ "\$\(standin_wanted\)" = on \]; '
                     r'then IB_FORCE_OFFLINE=0\n\s*else IB_FORCE_OFFLINE=1; fi', sh)
    banner = sh[sh.index("Walnut DataBridge is up."):]
    assert not PROVIDER.search(banner)


# ------------------------------------------------------------------------------------------------
# 4. the web sources
# ------------------------------------------------------------------------------------------------
def test_answer_badge_for_the_standin_is_neutral_in_both_languages():
    ans = _read("components/user/answerText.ts")
    # standin-1 is none of hy3/deepseek/extractive, so it is 'ai'
    assert "if (m.provider === 'deterministic' || id.startsWith('extractive')) return 'extractive';" in ans
    assert "return 'ai';" in ans and "ai: 'answer.model.ai'" in ans
    user = _read("lib/i18n/user.ts")
    assert "'answer.model.ai': 'AI-generated from your cited sources'" in user
    assert "'answer.model.ai': '由 AI 根据您引用的来源生成'" in user


def test_presenter_hud_says_quoting_only_without_a_model_and_slides_name_hy3_only_for_tokenhub():
    ctl = _read("components/presenter/controller.ts")
    assert "model = s.value.models?.active ? 'live' : 'quoting';" in ctl
    pr = _read("lib/i18n/presenter.ts")
    assert "'pr.status.live': 'live'" in pr and "'pr.status.live': '在线'" in pr
    assert not HY3.search(re.search(r"'pr\.status\.live': '[^']*'", pr).group(0))
    slides = _read("components/presenter/slides/Slides.tsx")
    assert ("s?.models?.active === true && s.models.active_provider === 'tencent-tokenhub'") in slides


def test_preflight_board_names_hy3_only_for_tokenhub():
    board = _read("components/preflight/PreflightBoard.tsx")
    assert "const hy3 = live && s.models?.active_provider === 'tencent-tokenhub';" in board
    assert "hy3 ? 'Model now: Tencent Hunyuan live' : live ? 'Model now: AI model active'" in board


def test_deliverables_name_no_model_for_the_standin():
    deliver = _read("lib/i18n/deliver.ts")
    assert not HY3.search(deliver)
    assert "'deliver.by.ai': 'AI-generated from your cited sources'" in deliver
    assert "'deliver.by.ai': '由 AI 根据您引用的来源生成'" in deliver
    sheet = _read("components/user/DeliverSheet.tsx")
    assert ("if (!['hy3', 'deepseek'].includes(modelKind(m))) return t(m.fallback_used ? "
            "'deliver.by.fallbackAi' : 'deliver.by.ai');") in sheet


def test_pipeline_and_status_panel_do_not_claim_hy3_answers():
    pipe = _read("components/analytics/pipeline.ts")
    assert "Hy3 → DeepSeek → extractive" not in pipe and "Hy3 (Tencent Hunyuan, through TokenHub) answers first" not in pipe
    status = _read("components/analytics/SystemStatus.tsx")
    assert "'standin-1': 'AI model'" in status
    assert "status.models?.active ? status.models.active_provider : null" in status


def test_user_facing_web_text_never_names_the_standin_provider():
    hits = []
    for d in ("lib/i18n", "components/trust", "components/analytics", "components/preflight",
              "components/presenter/slides", "public/finals"):
        for p in [*(WEB / d).rglob("*.ts"), *(WEB / d).rglob("*.tsx"), *(WEB / d).rglob("*.json")]:
            if PROVIDER.search(p.read_text(encoding="utf-8")):
                hits.append(str(p.relative_to(WEB)))
    assert hits == []


def test_drafts_name_no_model_for_the_standin():
    act = _read("lib/i18n/act.ts")
    assert "'act.draft.by.ai': 'AI-generated from your cited sources, checked against them'" in act
    assert "'act.draft.by.ai': '由 AI 根据您引用的来源生成，并已对照来源检查'" in act
    sheet = _read("components/user/ActSheet.tsx")
    assert "['hy3', 'deepseek'].includes(modelKind(d.model))" in sheet and "t('act.draft.by.ai')" in sheet
