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


# ------------------------------------------------------------------------------------------------
# 3. write-ahead: the extra documents (ids only) before they are read; /ib/rewrite before the call
# ------------------------------------------------------------------------------------------------
class _Index:
    def __init__(self, docs):
        self.docs = docs

    def readable(self, user, epoch):
        return frozenset(self.docs)

    def docs_in_container(self, platform, container):
        return [d for d in self.docs if d.startswith(platform + ":")]

    def doc(self, d):
        return {"updated_at": "2026-10-01"}

    def search(self, topic, readable, k=80):
        return []


class _Ledger:
    def __init__(self, order, fail=False):
        self.order, self.fail, self.rows = order, fail, []

    def append(self, ev):
        if self.fail:
            raise RuntimeError("ledger down")
        self.order.append("ledger")
        self.rows.append(ev)


def _expand_service(order, *, fail_ledger=False):
    from types import SimpleNamespace
    from databridge_ib.views.service import ViewService
    led = _Ledger(order, fail_ledger)
    svc = ViewService(actions=None, ledger=led, projection=None,
                      index=_Index(["slack:a", "slack:b", "slack:c"]), connectors={},
                      directory=SimpleNamespace(epoch=1), composer=object(), enabled=True)
    cites = {"C1": SimpleNamespace(doc_id="slack:a", platform="slack", container="x", title="t")}
    return svc, led, cites


@pytest.mark.parametrize("authorize_fails", [False, True])
def test_extra_documents_are_ledgered_before_they_are_read(monkeypatch, authorize_fails):
    from types import SimpleNamespace
    from databridge_ib.views import service as vsvc
    order: list = []
    svc, led, cites = _expand_service(order)

    def authorize(user, ids, **kw):
        order.append(("read", tuple(ids)))
        if authorize_fails:
            raise RuntimeError("source down")
        return {}, None
    monkeypatch.setattr(vsvc.authz, "authorize", authorize)
    out = svc._expand(SimpleNamespace(id="jdoe"), cites, "q", req_id="r" * 32, vid="v" * 32)
    assert out == cites                                     # nothing new passed (or the read failed)
    assert order == ["ledger", ("read", ("slack:b", "slack:c"))]
    row = led.rows[0]
    assert (row.kind, row.stage, row.deliverable, row.req_id, row.deliverable_id) == (
        "deliverable.requested", "sources", "view", "r" * 32, "v" * 32)
    assert row.doc_ids == ("slack:b", "slack:c")
    assert set(json.loads(row.model_dump_json())) >= {"doc_ids"}   # ids and counts, no text field


def test_ledger_down_means_the_extra_documents_are_never_read(monkeypatch):
    from types import SimpleNamespace
    from databridge_ib.deliverables.service import Unavailable
    from databridge_ib.views import service as vsvc
    order: list = []
    svc, _led, cites = _expand_service(order, fail_ledger=True)
    monkeypatch.setattr(vsvc.authz, "authorize", lambda *a, **k: order.append("read"))
    with pytest.raises(Unavailable):
        svc._expand(SimpleNamespace(id="jdoe"), cites, "q", req_id="r" * 32, vid="v" * 32)
    assert order == []


def test_rewrite_writes_ahead_and_ledger_down_means_no_model_call(tmp_path):
    from test_deliverables import _fail
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    fake, _ = _with_claude(app, svc, lambda kind, text: "What is the SME FX margin?")
    _fail(svc, {"deliverable.requested"})
    r = jd.post("/ib/rewrite", json={"question": "and the margin?", "previous": "SME pricing"})
    assert r.status_code == 503 and fake.calls == []


# ------------------------------------------------------------------------------------------------
# 4. analytics: view rows are counted apart; the deliverables totals do not move
# ------------------------------------------------------------------------------------------------
def test_stats_count_view_rows_apart_from_deliverables():
    import datetime as dt
    from types import SimpleNamespace
    from databridge_ib.audit.stats import compute_stats
    now = dt.datetime(2026, 10, 30, tzinfo=dt.timezone.utc)
    t = (now - dt.timedelta(hours=1)).isoformat()
    rows = [("deliverable.requested", {"deliverable": "table"}),
            ("deliverable.created", {"deliverable": "table", "status": "ready"}),
            ("deliverable.requested", {"deliverable": "view"}),
            ("deliverable.requested", {"deliverable": "view", "stage": "sources",
                                       "doc_ids": ["slack:b"]}),
            ("deliverable.created", {"deliverable": "view", "status": "ready"}),
            ("deliverable.requested", {"deliverable": "view"}),
            ("deliverable.created", {"deliverable": "view", "status": "page"}),
            ("deliverable.requested", {"deliverable": "view", "stage": "rewrite",
                                       "input_chars": 12}),
            ("deliverable.failed", {"deliverable": "view", "stage": "lookup"})]
    evs = [SimpleNamespace(kind=k, data={"event_time": t, "sub": "jdoe", **d}, seq=i, hash="h",
                           recorded_at=t) for i, (k, d) in enumerate(rows, 1)]
    tot = compute_stats(evs, now=now)["totals"]
    assert (tot["deliverables_requested"], tot["deliverables_created"],
            tot["deliverables_failed"]) == (1, 1, 0)
    assert (tot["views_requested"], tot["views_created"], tot["views_failed"]) == (2, 2, 1)
    assert (tot["views_decided_page"], tot["views_decided_chat"]) == (1, 0)
    assert (tot["views_sources_checked"], tot["views_rewrites"]) == (1, 1)


# ------------------------------------------------------------------------------------------------
# 5. untagged labels are grounded
# ------------------------------------------------------------------------------------------------
_SENT = "Indonesian SME customers are priced at a 0.4% FX margin."


def _page_with(app, svc, title="", heading="", eyebrow=""):
    def reply(kind, text):
        if kind == "decide":
            return json.dumps({"render": "page", "title": title, "reason": "asked"})
        tag = _tag_of(text, "SME pricing by market")
        return json.dumps({"title": title, "accent": "blue", "blocks": [
            {"type": "hero", "title": "", "eyebrow": eyebrow,
             "summary": [{"text": _SENT, "tags": [tag]}]},
            {"type": "prose", "heading": heading, "items": [{"text": _SENT, "tags": [tag]}]}]})
    return _with_claude(app, svc, reply)


def _labels(v):
    """Everything the page shows as its own (not ``meta.instruction``: the person's words echoed
    back as "You asked for")."""
    data = dict(v["a2ui"][2]["updateDataModel"]["value"])
    data.pop("meta", None)
    return json.dumps([data, v["title"]])


@pytest.mark.parametrize("title", ["Priya approved 2.5M budget", "Priya approved budget"])
def test_the_change_this_page_words_never_become_a_title(tmp_path, title):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]
    _page_with(app, svc, title=title, heading=title, eyebrow="Priya")
    v = _build(jd, rid, f"title it {title}").json()
    assert v["generated"] == "model" and v["title"] != title
    assert "approved" not in _labels(v) and "2.5M" not in _labels(v)
    d = _decide(jd, rid, f"Show me a report, title it {title}").json()
    assert d["title"] != title


def test_a_heading_stitched_from_two_sources_is_rejected(tmp_path):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]
    # "conversion" is only in the fee page, "customers" only in the pricing sheet
    _page_with(app, svc, heading="Conversion customers")
    v = _build(jd, rid, "").json()
    assert v["generated"] == "model" and "Conversion customers" not in _labels(v)
    _page_with(app, svc, heading="Conversion fee")          # one source's words: kept
    v = _build(jd, rid, "").json()
    assert "Conversion fee" in _labels(v)


def test_label_rule_units():
    from databridge_ib.deliverables.evidence import Evidence  # noqa: F401  (shape only)
    from types import SimpleNamespace
    from databridge_ib.views.pagecheck import PAGE_WORDS, LabelRule, grounded_label
    ev = {"C1": SimpleNamespace(units=[SimpleNamespace(text="the cutover moved to friday, said Ken")]),
          "C2": SimpleNamespace(units=[SimpleNamespace(text="replication lag on the shard")])}
    allowed = {"cutover", "moved", "friday", "said", "ken", "replication", "lag", "shard",
               "status"} | PAGE_WORDS
    rule = LabelRule(ev, "status of the migration", set(), allowed)
    assert grounded_label("Cutover status", rule) == "Cutover status"
    assert grounded_label("Key dates", rule) == "Key dates"
    assert grounded_label("Ken cutover", rule) is None                 # a capitalised name
    assert grounded_label("Cutover lag", rule) is None                 # stitched from two sources
    assert grounded_label("Cutover 2", rule) is None                   # a digit
    named = LabelRule(ev, "status", {"ken"}, allowed)
    assert grounded_label("Ken cutover", named) == "Ken cutover"       # a tagged person's name


def test_a_view_chart_heading_uses_only_its_own_labels(tmp_path):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]

    def page(heading):
        def reply(kind, text):
            tag = _tag_of(text, "SME pricing by market")
            pts = [{"x": x, "value": v, "raw": r, "tag": tag}
                   for x, v, r in (("SG", 0.35, "0.35%"), ("ID", 0.4, "0.4%"))]
            return json.dumps({"title": "", "accent": "blue", "blocks": [
                {"type": "chart", "heading": heading, "kind": "bar", "series_name": "FX margin",
                 "points": pts}]})
        return reply
    _with_claude(app, svc, page("FX margin"))
    v = _build(jd, rid, "a chart").json()
    charts = [b for b in v["a2ui"][2]["updateDataModel"]["value"]["blocks"] if b["type"] == "chart"]
    assert charts and charts[0]["heading"] == "FX margin"
    _with_claude(app, svc, page("FX margin by customers"))    # "customers": not a chart label
    v = _build(jd, rid, "a chart").json()
    charts = [b for b in v["a2ui"][2]["updateDataModel"]["value"]["blocks"] if b["type"] == "chart"]
    assert charts and charts[0]["heading"] == ""


# ------------------------------------------------------------------------------------------------
# 6. the spend cap fails closed; a settle error never leaks a slot; per-person limit on answers
# ------------------------------------------------------------------------------------------------
def _chat_fake():
    return FakeClaude(lambda kind, text: json.dumps({"render": "chat", "title": "x", "reason": "y"}),
                      usage=(10, 10))


@pytest.mark.parametrize("content", ["{not json", '{"usd": "lots", "calls": 1, "entries": []}',
                                     '{"calls": 3, "entries": []}', '{"usd": 1, "calls": 1}',
                                     '{"usd": -5, "calls": 1, "entries": []}', "[]"])
def test_a_corrupt_spend_file_fails_closed_and_is_not_overwritten(tmp_path, content):
    from databridge_ib.model_guard import guard_dir
    guard_dir().mkdir(parents=True)
    (guard_dir() / "spend.json").write_text(content, encoding="utf-8")
    fake = _chat_fake()
    c = cmod.ClaudeComposer(state_dir=tmp_path, cap_usd=10, client=fake)
    assert c.decide("jdoe", "q", "a", "ctx")[2] == "budget" and fake.calls == []
    assert c.status()["available"] is False
    assert (guard_dir() / "spend.json").read_text(encoding="utf-8") == content


def test_an_unreadable_spend_file_fails_closed(tmp_path):
    from databridge_ib.model_guard import guard_dir
    (guard_dir() / "spend.json").mkdir(parents=True)        # a directory: read_text raises OSError
    fake = _chat_fake()
    c = cmod.ClaudeComposer(state_dir=tmp_path, cap_usd=10, client=fake)
    assert c.decide("jdoe", "q", "a", "ctx")[2] == "budget" and fake.calls == []


def test_a_settle_write_error_never_leaks_a_slot_and_closes_the_cap(tmp_path, monkeypatch):
    fake = _chat_fake()
    c = cmod.ClaudeComposer(state_dir=tmp_path, cap_usd=10, client=fake)
    real = cmod.os.replace

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(cmod.os, "replace", broken)
    for _ in range(cmod.MAX_INFLIGHT + 2):
        c.decide("jdoe", "q", "a", "ctx")
    monkeypatch.setattr(cmod.os, "replace", real)
    # every slot came back (a leak would read "busy"), and the unrecorded spend closed the cap
    assert c._slots.acquire(blocking=False) and c._slots._value == cmod.MAX_INFLIGHT - 1
    c._slots.release()
    assert len(fake.calls) == 1
    assert c.decide("jdoe", "q", "a", "ctx")[2] == "budget" and c.status()["available"] is False


def test_the_answer_backend_has_a_per_person_limit(tmp_path, monkeypatch):
    from databridge_ib.claude import ClaudeBackend
    from databridge_ib.llm import for_person
    from databridge_ib.model_guard import REFUSED, ModelGuard
    monkeypatch.setenv("IB_CLAUDE_USER_LIMIT", "2")             # the alias, read at start-up
    fake = FakeClaude(lambda kind, text: "ok", usage=(10, 10))
    be = ClaudeBackend(ModelGuard(tmp_path / "g"), client=fake)
    msgs = [{"role": "user", "content": "hi"}]

    def call(sub):
        with for_person(sub):
            try:
                be.generate(model="anthropic/x", messages=msgs)
                return "ok"
            except RuntimeError as exc:
                return str(exc)
    assert [call("jdoe") for _ in range(3)] == ["ok", "ok", REFUSED]
    assert call("priya") == "ok"

