"""The stand-in model's guardrails (model_guard.py, D-42), with FAKE clients only: no network, no spend.

1. one persistent spend ledger outside the demo state; append-style; per-test dir (conftest);
2. every cap is enforced (total, daily UTC, service-wide rpm, per-person rpm) and means no call;
3. the ledger fails closed: corrupt, unreadable, unwritable, or missing in an existing dir;
4. caps come from the environment at start-up only, with the aliases (the lower wins);
5. no reset path touches it: /sim/reset, demo_up.sh --fresh (its wipe guard), demo_day.ps1;
6. a refused call falls back: a quoted answer, a page from the rules, the rewrite as asked;
7. /ib/status shows the numbers to admin and compliance only, never the provider.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from databridge_ib import model_guard as mg
from databridge_ib.model_guard import Caps, ModelGuard

from test_deliverables import _ask, _c, _events, _world
from test_views_composer import FakeClaude, _build, _with_claude

ROOT = Path(__file__).resolve().parents[3]                     # databridge/
PROVIDER_WORDS = re.compile(r"claude|anthropic|opus|sonnet|haiku", re.I)
T0 = datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _flag(monkeypatch):
    monkeypatch.setenv("IB_DELIVERABLES", "1")


class _Clock:
    def __init__(self, t=T0):
        self.t, self.m = t, 1_000.0

    def now(self):
        return self.t

    def mono(self):
        return self.m


def _guard(d: Path, clock: _Clock | None = None, **caps) -> ModelGuard:
    c = clock or _Clock()
    return ModelGuard(d, Caps(**{"total_usd": 11.0, "daily_usd": 5.0, "rpm": 100, "user_rpm": 100,
                                 **caps}), now=c.now, monotonic=c.mono)


def _one(g: ModelGuard, sub="jdoe", kind="answer", worst=0.5, actual=0.5) -> str:
    t, why = g.admit(sub, kind, worst)
    if t is not None:
        g.settle(t, actual)
    return why


# ------------------------------------------------------------------------------------------------
# 1. the ledger
# ------------------------------------------------------------------------------------------------
def test_the_default_dir_is_outside_every_ib_var_root_and_tests_use_their_own(monkeypatch):
    from databridge_ib.config import IbConfig
    assert ROOT / "var" not in mg.guard_dir().parents          # conftest: a per-test tmp dir
    assert mg.DEFAULT_GUARD_DIR == ROOT / "var" / "model_guard"
    monkeypatch.delenv("IB_VAR", raising=False)
    assert mg.DEFAULT_GUARD_DIR not in IbConfig().var_root.parents
    assert IbConfig().var_root != mg.DEFAULT_GUARD_DIR
    for state in ("ib", "ib-final"):
        assert ROOT / "var" / state not in mg.DEFAULT_GUARD_DIR.parents
    assert "/var/" in (ROOT / ".gitignore").read_text(encoding="utf-8").split()


def test_a_new_dir_starts_an_empty_ledger_and_every_call_is_appended(tmp_path):
    clock = _Clock()
    g = _guard(tmp_path / "new" / "guard", clock)
    path = tmp_path / "new" / "guard" / "spend.json"
    assert json.loads(path.read_text()) == {"usd": 0.0, "calls": 0, "entries": []}
    assert _one(g, "jdoe", "answer", 0.5, 0.25) == "none"
    clock.t += timedelta(minutes=1)
    assert _one(g, "priya", "page", 0.5, 0.0) == "none"
    d = json.loads(path.read_text())
    assert d["usd"] == pytest.approx(0.25) and d["calls"] == 2
    assert [e["kind"] for e in d["entries"]] == ["answer", "page"]
    assert d["entries"][0] == {"at": "2026-10-30T09:00:00+00:00", "usd": 0.25,
                               "who": mg.persona_hash("jdoe"), "kind": "answer"}
    assert "jdoe" not in path.read_text() and "priya" not in path.read_text()
    assert not list(path.parent.glob("*.tmp"))                 # written atomically


def test_the_total_only_goes_up_across_restarts(tmp_path):
    g = _guard(tmp_path / "g")
    _one(g, actual=1.0)
    again = _guard(tmp_path / "g")                              # a restart reads the same ledger
    assert again.status()["spent_usd"] == pytest.approx(1.0) and again.status()["calls"] == 1
    t, _ = again.admit("jdoe", "answer", 0.1)
    again.settle(t, -5.0)                                      # a negative cost never lowers it
    assert again.status()["spent_usd"] == pytest.approx(1.0)


# ------------------------------------------------------------------------------------------------
# 2. every cap
# ------------------------------------------------------------------------------------------------
def test_the_total_cap(tmp_path):
    g = _guard(tmp_path / "g", total_usd=1.0, daily_usd=100)
    assert _one(g, worst=0.6, actual=0.6) == "none"
    assert _one(g, worst=0.6, actual=0.6) == "cap_total"      # 0.6 + 0.6 > 1.0: refused before
    assert g.status()["calls"] == 1 and g.status()["available"] is True


def test_the_daily_cap_is_a_utc_day_from_the_entries(tmp_path):
    clock = _Clock(datetime(2026, 10, 30, 23, 50, tzinfo=timezone.utc))
    g = _guard(tmp_path / "g", clock, daily_usd=1.0)
    assert _one(g, worst=0.9, actual=0.9) == "none"
    assert _one(g, worst=0.2, actual=0.2) == "cap_daily"
    assert g.status()["today_usd"] == pytest.approx(0.9)
    clock.t += timedelta(minutes=15)                           # 00:05 UTC: a new day
    assert g.status()["today_usd"] == 0
    assert _one(g, worst=0.2, actual=0.2) == "none"
    assert g.status()["spent_usd"] == pytest.approx(1.1)       # the total still counts both days


def test_the_global_rate(tmp_path):
    clock = _Clock()
    g = _guard(tmp_path / "g", clock, rpm=3)
    assert [_one(g, f"p{i}", worst=0.01, actual=0.0) for i in range(4)] == \
        ["none", "none", "none", "rate_global"]
    clock.m += 61                                              # a minute later
    assert _one(g, "p9", worst=0.01, actual=0.0) == "none"


def test_the_per_person_rate(tmp_path):
    g = _guard(tmp_path / "g", user_rpm=2)
    assert [_one(g, "jdoe", worst=0.01, actual=0.0) for _ in range(3)] == ["none", "none", "rate_person"]
    assert _one(g, "priya", worst=0.01, actual=0.0) == "none"


def test_a_refused_call_reaches_no_client_and_the_composer_says_budget_or_rate(tmp_path):
    from databridge_ib.views import composer as cmod
    fake = FakeClaude(lambda kind, text: json.dumps({"render": "chat", "title": "x", "reason": "y"}),
                      usage=(10, 10))
    c = cmod.ClaudeComposer(client=fake, guard=_guard(tmp_path / "g", total_usd=0.0))
    assert c.decide("jdoe", "q", "a", "ctx")[2] == "budget" and fake.calls == []
    c = cmod.ClaudeComposer(client=fake, guard=_guard(tmp_path / "h", daily_usd=0.0))
    assert c.rewrite("jdoe", "q", "p")[2] == "budget" and fake.calls == []
    c = cmod.ClaudeComposer(client=fake, guard=_guard(tmp_path / "i", rpm=0))
    assert c.decide("jdoe", "q", "a", "ctx")[2] == "rate" and fake.calls == []


# ------------------------------------------------------------------------------------------------
# 3. fail closed
# ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("content", ["{not json", "[]", '{"usd": 1, "calls": 1}',
                                     '{"usd": "x", "calls": 1, "entries": []}',
                                     '{"usd": NaN, "calls": 1, "entries": []}',
                                     '{"usd": 1, "calls": 1.5, "entries": []}',
                                     '{"usd": 1, "calls": 1, "entries": [{"usd": 1}]}'])
def test_a_corrupt_ledger_blocks_calls_and_is_never_overwritten(tmp_path, content):
    (tmp_path / "g").mkdir()
    (tmp_path / "g" / "spend.json").write_text(content, encoding="utf-8")
    g = _guard(tmp_path / "g")
    assert _one(g) == "closed"
    assert g.status()["ok"] is False and g.status()["available"] is False
    assert (tmp_path / "g" / "spend.json").read_text(encoding="utf-8") == content


def test_an_unreadable_ledger_blocks_calls(tmp_path):
    (tmp_path / "g" / "spend.json").mkdir(parents=True)      # a directory: reading it fails
    assert _one(_guard(tmp_path / "g")) == "closed"


def test_a_missing_ledger_in_an_existing_dir_blocks_calls(tmp_path):
    (tmp_path / "g").mkdir()                                   # someone deleted only the file
    g = _guard(tmp_path / "g")
    assert _one(g) == "closed" and not (tmp_path / "g" / "spend.json").exists()


def test_an_unwritable_ledger_closes_the_guard_after_the_call(tmp_path):
    g = _guard(tmp_path / "g")
    before = (tmp_path / "g" / "spend.json").read_bytes()
    (tmp_path / "g" / "spend.json.tmp").mkdir()                # the atomic write cannot happen
    t, why = g.admit("jdoe", "answer", 0.1)
    assert why == "none"
    with pytest.raises(OSError):
        g.settle(t, 0.05)
    assert _one(g) == "closed" and g.status()["available"] is False
    assert (tmp_path / "g" / "spend.json").read_bytes() == before


def test_a_dir_that_cannot_be_created_blocks_calls(tmp_path):
    (tmp_path / "file").write_text("x")
    assert _one(_guard(tmp_path / "file" / "guard")) == "closed"


# ------------------------------------------------------------------------------------------------
# 4. caps from the environment, at start-up, with the aliases
# ------------------------------------------------------------------------------------------------
def test_the_defaults():
    assert Caps.from_env() == Caps(total_usd=11.0, daily_usd=5.0, rpm=20, user_rpm=6)


@pytest.mark.parametrize("env,total", [({"IB_MODEL_CAP_USD": "7"}, 7.0),
                                       ({"IB_CLAUDE_BUDGET_USD": "4"}, 4.0),
                                       ({"IB_MODEL_CAP_USD": "11", "IB_CLAUDE_BUDGET_USD": "20"}, 11.0),
                                       ({"IB_MODEL_CAP_USD": "11", "IB_CLAUDE_BUDGET_USD": "3"}, 3.0),
                                       ({"IB_MODEL_CAP_USD": "lots"}, 0.0),
                                       ({"IB_MODEL_CAP_USD": "-1"}, 0.0)])
def test_the_total_cap_alias_the_lower_wins_and_garbage_is_closed(monkeypatch, env, total):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert Caps.from_env().total_usd == total


def test_the_other_caps_and_the_per_person_alias(monkeypatch):
    monkeypatch.setenv("IB_MODEL_DAILY_CAP_USD", "2.5")
    monkeypatch.setenv("IB_MODEL_RPM", "9")
    monkeypatch.setenv("IB_CLAUDE_USER_LIMIT", "4")
    assert Caps.from_env() == Caps(total_usd=11.0, daily_usd=2.5, rpm=9, user_rpm=4)
    monkeypatch.setenv("IB_MODEL_USER_RPM", "3")
    assert Caps.from_env().user_rpm == 3
    monkeypatch.setenv("IB_MODEL_USER_RPM", "8")
    assert Caps.from_env().user_rpm == 4                       # both set: the lower wins


def test_caps_are_read_once_at_start_up(monkeypatch):
    monkeypatch.setenv("IB_MODEL_CAP_USD", "2")
    g = ModelGuard.shared()
    monkeypatch.setenv("IB_MODEL_CAP_USD", "500")              # no runtime raise
    assert ModelGuard.shared() is g and ModelGuard.shared().caps.total_usd == 2.0


def test_there_is_no_route_that_changes_caps_or_spend(tmp_path):
    from databridge_ib.app import iter_routes
    app, _svc = _world(tmp_path)
    paths = {getattr(r, "path", "") for r in iter_routes(app)}
    assert not [p for p in paths if re.search(r"budget|spend|guard|cap", p, re.I)]


# ------------------------------------------------------------------------------------------------
# 5. no reset touches it
# ------------------------------------------------------------------------------------------------
def test_sim_reset_leaves_the_ledger_byte_identical(tmp_path_factory):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from databridge_ib.sim import make_sim_router
    from e2e_world import ADMIN, E2E, WORLD, _Client

    g = ModelGuard.shared()
    _one(g, "jdoe", "answer", 0.2, 0.1)
    path = mg.guard_dir() / "spend.json"
    before = path.read_bytes()
    e = E2E(tmp_path_factory.mktemp("sim-reset-guard"))
    e.ingestor.ledger = e.ledger
    app = FastAPI()
    app.include_router(make_sim_router(
        SimpleNamespace(world_path=WORLD, platform_base={}, admin_token=ADMIN),
        lambda role: (lambda: SimpleNamespace(id="admin.it", roles=(role,))),
        client_factory=lambda: _Client(e.mock, base_url="http://testserver",
                                       headers={"Authorization": f"Bearer {ADMIN}"}),
        ingestor=e.ingestor))
    r = TestClient(app).post("/sim/reset")
    assert r.status_code == 200, r.text
    assert path.read_bytes() == before
    assert sorted(p.name for p in mg.guard_dir().iterdir()) == ["spend.json"]


def _wipe_guard(tmp_path: Path, state: str, guard_env: str | None = None) -> int:
    """Run scripts/wipe_guard.sh's check (what demo_up.sh runs before any start or --fresh wipe)
    in a scratch copy of databridge/ without .env.ib.local."""
    root = tmp_path / "db"
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "var" / "ib-final").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "scripts" / "wipe_guard.sh", root / "scripts")
    env = {"PATH": "/usr/bin:/bin"} | ({"IB_MODEL_GUARD_DIR": guard_env} if guard_env else {})
    return subprocess.run(["bash", "-c", f'. scripts/wipe_guard.sh; refuse_guard_overlap "{state}"'],
                          cwd=root, env=env, capture_output=True, timeout=30).returncode


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
@pytest.mark.parametrize("state,guard,code", [("var/ib-final", None, 0), ("var/ib", None, 0),
                                              ("var/model_guard", None, 2), ("var", None, 2),
                                              ("var/model_guard/x", None, 2),
                                              ("./var/model_guard/", None, 2),
                                              ("var/ib-final", "var/ib-final/guard", 2),
                                              ("var/ib-final", "elsewhere/guard", 0)])
def test_demo_up_refuses_a_state_that_overlaps_the_guard_dir(tmp_path, state, guard, code):
    assert _wipe_guard(tmp_path, state, guard) == code


def test_demo_up_fresh_wipes_only_the_state_after_the_guard_check():
    sh = (ROOT / "scripts" / "demo_up.sh").read_text(encoding="utf-8")
    assert re.search(r'^STATE="\$\{IB_VAR:-var/ib-final\}"$', sh, re.M)
    assert ". scripts/wipe_guard.sh" in sh
    check = sh.index('refuse_guard_overlap "$STATE" || exit 2')
    fresh = sh.index('if [ "$FRESH" = "1" ]; then')
    assert check < fresh
    wipes = re.findall(r"rm -rf ([^ ;\n]+)", sh)
    assert wipes == ['"$STATE"']                               # the one wipe, of the state only
    for k in ("IB_MODEL_CAP_USD", "IB_MODEL_DAILY_CAP_USD", "IB_MODEL_RPM", "IB_MODEL_USER_RPM",
              "IB_MODEL_GUARD_DIR"):
        assert k in sh.split('IB_ONLY_KEYS="', 1)[1].split('"', 1)[0]


def test_demo_day_reset_never_targets_the_guard_dir():
    ps = (ROOT / "scripts" / "demo_day.ps1").read_text(encoding="utf-8")
    assert re.search(r"\$ModelGuardDir = .*Join-Path \$Db 'var\\model_guard'", ps)
    # every recursive delete is of the browser profile, behind the guard check
    removes = re.findall(r"Remove-Item -Recurse[^\n]*", ps)
    assert removes and all("$BrowserProfile" in r for r in removes)
    assert re.search(r"\$BrowserProfile = Join-Path \$Db 'var\\demo-browser'", ps)
    assert ps.index("Test-TouchesModelGuard $BrowserProfile") < ps.index("Remove-Item -Recurse")
    # -Fresh wipes through demo_up.sh --fresh only (var\ib-final), after the same check
    reset = ps[ps.index("function Reset-Demo"):]
    reset = reset[:reset.index("\n}\n")]
    assert "Test-TouchesModelGuard (Join-Path $Db 'var\\ib-final')" in reset
    assert "Invoke-DemoUp 'start --fresh'" in reset and "Remove-Item" not in reset
    assert "model_guard" not in ps.replace("var\\model_guard'", "")


# ------------------------------------------------------------------------------------------------
# 6. a refused call falls back, with a counts-only ledger row
# ------------------------------------------------------------------------------------------------
def test_answers_are_quoted_when_the_daily_cap_is_used(tmp_path):
    from databridge_ib.claude import claude_chain
    fake = FakeClaude(lambda kind, text: "anything")
    chain = claude_chain(client=fake, guard=_guard(tmp_path / "g", daily_usd=0.0))
    app, svc = _world(tmp_path / "w", chain=chain)
    ans = _ask(_c(app, svc, "jdoe"))
    # a cap refusal is "no model reply": the label says the model was unavailable (Story 1 fix #2)
    assert fake.calls == [] and ans["model"] == {"provider": "deterministic", "model": "extractive-v1",
                                                 "fallback_used": True, "reason_code": "model_unavailable"}
    assert ans["citations"]                                    # quoted from the sources
    rel = _events(svc, "answer.released")[-1]
    assert rel.model.model == "extractive-v1" and rel.model.fallback_used is True


def test_a_page_falls_back_to_the_rules_at_the_cap(tmp_path):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]
    fake, comp = _with_claude(app, svc, lambda kind, text: "{}")
    comp.guard = _guard(tmp_path / "g", total_usd=0.0)
    v = _build(jd, rid, "a table").json()
    assert fake.calls == [] and v["generated"] == "rules" and v["fallback"] == "budget"
    created = _events(svc, "deliverable.created")[-1]
    assert created.fallback == "budget"


def test_a_rewrite_at_the_rate_limit_is_returned_as_asked_with_a_counts_only_row(tmp_path):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    fake, comp = _with_claude(app, svc, lambda kind, text: "What is the SME FX margin?")
    comp.guard = _guard(tmp_path / "g", user_rpm=0)
    r = jd.post("/ib/rewrite", json={"question": "and the margin?", "previous": "SME pricing"})
    assert r.json() == {"question": "and the margin?", "rewritten": False} and fake.calls == []
    failed = _events(svc, "deliverable.failed")[-1]
    assert (failed.stage, failed.reason_code) == ("generate", "model_rate_rewrite")
    assert "margin" not in failed.model_dump_json()


# ------------------------------------------------------------------------------------------------
# 7. /ib/status: admin and compliance only, numbers only
# ------------------------------------------------------------------------------------------------
def test_status_shows_the_budget_to_admin_and_compliance_only(tmp_path):
    app, svc = _world(tmp_path)
    for uid, role in (("priya", "admin_it"), ("ken.ext", "compliance_officer")):
        svc.directory._users[uid] = svc.directory.get(uid).model_copy(update={"roles": (role,)})
    _one(ModelGuard.shared(), "jdoe", "answer", 0.3, 0.25)
    for uid in ("priya", "ken.ext"):
        st = _c(app, svc, uid).get("/ib/status").json()
        b = st["model_budget"]
        assert b["label"] == "Model budget" and b["ok"] is True and b["available"] is True
        assert (b["spent_usd"], b["calls"], b["total_cap_usd"], b["daily_cap_usd"]) == (0.25, 1, 11.0, 5.0)
        assert b["today_usd"] == 0.25 or b["today_usd"] == 0   # the clock may have just crossed midnight
        assert not PROVIDER_WORDS.search(json.dumps(b))
    st = _c(app, svc, "jdoe").get("/ib/status").json()
    assert "model_budget" not in st and "Model budget" not in json.dumps(st)


def test_the_preflight_budget_line(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("demo_preflight_budget",
                                                  ROOT / "scripts" / "demo_preflight.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.budget_line({}) is None
    ok = {"ok": True, "available": True, "spent_usd": 1.5, "total_cap_usd": 11, "today_usd": 0.5,
          "daily_cap_usd": 5, "calls": 12}
    assert mod.budget_line({"model_budget": ok}) == (
        "Model budget", "pass", "US$1.50 of US$11.00 spent; today US$0.50 of US$5.00; 12 calls")
    assert mod.budget_line({"model_budget": {**ok, "available": False}})[1] == "warn"
    assert mod.budget_line({"model_budget": {"ok": False}})[1] == "warn"
