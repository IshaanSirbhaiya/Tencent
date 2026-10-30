"""Claude as the page composer (views/composer.py, pagespec.py, pagecheck.py), with a FAKE client:
no network, no spend. The world is the deliverables world (``test_deliverables.py``): a pricing
sheet and a fee page jdoe can read, next to a RESTRICTED leads-only note on the same topic.

What is proven here: page or chat is decided after the write-ahead record and the live re-check;
a short question stays in the chat without a call; every value Claude proposes is checked
against its source and invented ones are dropped; styles are closed enums ("a table in black");
the extra documents a page may use come from the asker's permitted set only (paired worlds with
and without the restricted note give identical pages and prompts); the spend cap and the rate
limits stop calls and the rules take over; a refusal, an API error or a bad reply falls back.
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from databridge_ib.views import composer as cmod
from databridge_ib.views.pagespec import read_page

from test_deliverables import ALL, PUBLIC_ONLY, Q, SECRETS, _ask, _c, _events, _fail, _tag_of, _world

_CLOCKS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\+00:00|Z)?")


@pytest.fixture(autouse=True)
def _flag(monkeypatch):
    monkeypatch.setenv("IB_DELIVERABLES", "1")


class FakeClaude:
    """``client.with_options(...).beta.messages.create(...)`` returning what ``reply`` says.
    ``reply(kind, user_text) -> str | Exception``; kind is ``decide`` or ``compose``."""

    def __init__(self, reply, *, stop="end_turn", usage=(1_000, 400)):
        self.reply, self.stop, self.usage = reply, stop, usage
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def with_options(self, **_kw):
        return self

    def _create(self, **kw):
        self.calls.append(kw)
        system = kw["system"][0]["text"]
        kind = "decide" if "decide how an answer is shown" in system else "compose"
        out = self.reply(kind, kw["messages"][0]["content"])
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(
            stop_reason=self.stop, model=kw["model"],
            content=[SimpleNamespace(type="text", text=out)],
            usage=SimpleNamespace(input_tokens=self.usage[0], output_tokens=self.usage[1],
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0))


def _with_claude(app, svc, reply, **kw):
    """Connect a composer with a fake client to the app's view service."""
    fake = FakeClaude(reply, **kw)
    comp = cmod.ClaudeComposer(state_dir=svc.cfg.var_root / "views", cap_usd=10.0, client=fake)
    app.state.views.composer = comp
    return fake, comp


def _decide(cl, rid, q):
    return cl.post("/ib/views", json={"req_id": rid, "instruction": q, "mode": "decide"})


def _build(cl, rid, ins=""):
    return cl.post("/ib/views", json={"req_id": rid, "instruction": ins})


def _data(v):
    return v["a2ui"][2]["updateDataModel"]["value"]


def _page_reply(blocks, title="FX margin by market", accent="green"):
    def reply(kind, text):
        if kind == "decide":
            return json.dumps({"render": "page", "title": title, "reason": "a table was asked for"})
        tag = _tag_of(text, "SME pricing by market")
        return json.dumps({"title": title, "accent": accent, "blocks": blocks(tag)})
    return reply


# ------------------------------------------------------------------------------------------------
# decide
# ------------------------------------------------------------------------------------------------
def test_a_short_question_stays_in_the_chat_without_a_call_or_a_record(tmp_path):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]
    fake, _comp = _with_claude(app, svc, _page_reply(lambda tag: []))
    n = len(_events(svc))
    r = _decide(jd, rid, "FX margin?")
    assert r.status_code == 200 and r.json() == {"render": "chat", "title": "", "decided_by": "rules"}
    assert fake.calls == [] and len(_events(svc)) == n


def test_claude_decides_after_the_record_and_the_live_recheck(tmp_path):
    order: list = []
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]

    def reply(kind, text):
        order.append("claude")
        assert "Leads only" not in text and "CNRY" not in text
        return json.dumps({"render": "page", "title": "FX margin by market", "reason": "table"})
    _with_claude(app, svc, reply)
    real = svc.ledger.append
    svc.ledger.append = lambda ev: (order.append(ev.kind), real(ev))[1]
    d = _decide(jd, rid, "Show the Indonesian SME FX margin by market as a table").json()
    assert d["render"] == "page" and d["decided_by"] == "model" and d["title"] == "FX margin by market"
    assert [o for o in order if o == "claude" or o.startswith("deliverable.")] == [
        "deliverable.requested", "claude", "deliverable.created"]
    created = _events(svc, "deliverable.created")[-1]
    assert created.deliverable == "view" and created.status == "page"


def test_someone_elses_answer_is_the_one_404_for_decide_too(tmp_path):
    app, svc = _world(tmp_path)
    jd, pr = _c(app, svc, "jdoe"), _c(app, svc, "priya")
    theirs = _ask(pr)["request_id"]
    fake, _ = _with_claude(app, svc, _page_reply(lambda tag: []))
    long_q = "Tell me everything about the SME pricing by market as a report"
    outs = {(_decide(jd, rid, long_q).status_code, _decide(jd, rid, long_q).content)
            for rid in (theirs, "0" * 32)}
    assert outs == {(404, b'{"detail":"not found"}')} and fake.calls == []


# ------------------------------------------------------------------------------------------------
# build: every value checked
# ------------------------------------------------------------------------------------------------
def test_claude_page_values_are_checked_and_styles_are_enums(tmp_path):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]

    def blocks(tag):
        return [
            {"type": "hero", "title": "FX margin by market", "eyebrow": "Quintessa Corp",
             "summary": [{"text": "Indonesian SME customers are priced at a 0.4% FX margin.", "tags": [tag]},
                         {"text": "Fees rose 12% last year.", "tags": [tag]}]},
            {"type": "stats", "heading": "At a glance", "items": [
                {"label": "ID Growth price", "value": "0.4% FX margin", "tag": tag},
                {"label": "Invented", "value": "9.9% FX margin", "tag": tag}]},
            {"type": "table", "heading": "Prices", "style": {"tone": "black", "accent": "neon"},
             "columns": ["market", "price"], "rows": [
                 [{"text": "SG", "tags": [tag]}, {"text": "0.35% FX margin", "tags": [tag]}],
                 [{"text": "ID", "tags": [tag]}, {"text": "0.9% FX margin", "tags": [tag]}],
                 [{"text": "Zorblax", "tags": []}, {"text": "secret", "tags": ["C9"]}]]},
            {"type": "people", "heading": "People", "people": [
                {"name": "Zorblax Holdings", "role": "Leads", "items": [
                    {"text": "Indonesian SME customers are priced at a 0.4% FX margin.", "tags": [tag]}]}]},
            {"type": "widget", "heading": "Not a block"},
        ]
    _with_claude(app, svc, _page_reply(blocks))
    v = _build(jd, rid, "a table in black").json()
    assert v["status"] == "ready" and v["generated"] == "model"
    assert v["model"] == {"provider": "standin", "model": "standin-1", "fallback_used": False}
    data = _data(v)
    by = {b["type"]: b for b in data["blocks"]}
    assert data["meta"]["accent"] == "green"
    assert by["hero"]["eyebrow"] == "" and [i["text"] for i in by["hero"]["summary"]] == [
        "Indonesian SME customers are priced at a 0.4% FX margin."]
    assert [(i["label"], i["value"]) for i in by["stats"]["items"]] == [("ID Growth price", "0.4% FX margin")]
    assert by["table"]["style"] == {"tone": "black", "accent": "blue"}      # unknown accent: default
    assert [[c["text"] for c in r] for r in by["table"]["table"]["rows"]] == [
        ["SG", "0.35% FX margin"], ["ID", ""]]
    assert "people" not in by                                              # an invented person
    blob = json.dumps(v)
    for bad in ("12%", "9.9%", "0.9%", "Zorblax", "Quintessa", "secret", "widget"):
        assert bad not in blob, bad
    assert v["check"]["items_dropped"] >= 5


def test_restricted_documents_never_reach_claude_or_the_page(tmp_path):
    outs, prompts = [], []
    for name, docs in (("a", ALL), ("b", PUBLIC_ONLY)):
        app, svc = _world(tmp_path / name, docs)
        jd = _c(app, svc, "jdoe")
        rid = _ask(jd, "Tell me everything about SME pricing and FX margins by market")["request_id"]
        seen: list = []

        def reply(kind, text, seen=seen):
            seen.append(_CLOCKS.sub("T", text))
            if kind == "decide":
                return json.dumps({"render": "page", "title": "SME pricing", "reason": "overview"})
            return json.dumps({"title": "SME pricing", "accent": "blue", "blocks": [
                {"type": "sections", "heading": ""}]})
        _with_claude(app, svc, reply)
        d = _decide(jd, rid, "Tell me everything about SME pricing and FX margins by market").json()
        v = _build(jd, rid).json()
        for k in ("view_id", "req_id", "as_of"):
            v.pop(k, None)
        d.pop("view_id", None)
        d.pop("req_id", None)
        outs.append(_CLOCKS.sub("T", re.sub(r"[0-9a-f]{32}", "ID", json.dumps([d, v], sort_keys=True))))
        prompts.append("\n".join(seen))
    assert outs[0] == outs[1] and prompts[0] == prompts[1]
    for s in SECRETS:
        assert s not in outs[0] and s not in prompts[0]


def test_the_request_text_never_reaches_the_ledger(tmp_path):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]
    _with_claude(app, svc, _page_reply(lambda tag: [
        {"type": "prose", "heading": "", "items": [
            {"text": "Indonesian SME customers are priced at a 0.4% FX margin.", "tags": [tag]}]}]))
    ins = "make the Quintessa table black please"
    v = _build(jd, rid, ins).json()
    recs = "\n".join(x for x in svc.cfg.ledger_path.read_text(encoding="utf-8").splitlines()
                     if '"deliverable.' in x)
    for text in ("Quintessa", ins, v["title"], "0.4% FX margin"):
        assert text not in recs


# ------------------------------------------------------------------------------------------------
# bounded: cap, rate, failures fall back to the rules
# ------------------------------------------------------------------------------------------------
def test_the_spend_cap_stops_calls_and_survives_a_restart(tmp_path):
    # a realistic reply (no more input tokens than characters sent), so the reserve is an upper bound
    fake = FakeClaude(lambda kind, text: json.dumps({"render": "chat", "title": "x", "reason": "y"}),
                      usage=(900, 1_800))
    cap = 0.2
    c = cmod.ClaudeComposer(state_dir=tmp_path, cap_usd=cap, client=fake)
    got = [c.decide("jdoe", "q", "a", "ctx")[2] for _ in range(8)]
    assert got[0] == "none" and "budget" in got
    spent = c.status()["spent_usd"]
    assert 0 < spent <= cap                       # never over the cap
    again = cmod.ClaudeComposer(state_dir=tmp_path, cap_usd=cap, client=fake)   # a restart
    assert again.status()["spent_usd"] == spent
    n = len(fake.calls)
    assert again.decide("jdoe", "q", "a", "ctx")[2] == "budget" and len(fake.calls) == n
    assert again.status()["available"] is True or spent >= cap


def test_rate_limits_are_per_person_and_global(tmp_path, monkeypatch):
    from databridge_ib.model_guard import Caps, ModelGuard
    fake = FakeClaude(lambda kind, text: json.dumps({"render": "chat", "title": "x", "reason": "y"}),
                      usage=(10, 10))
    c = cmod.ClaudeComposer(client=fake, guard=ModelGuard(tmp_path / "g", Caps(user_rpm=2, rpm=4)))
    codes = [c.decide("jdoe", "q", "a", "ctx")[2] for _ in range(3)]
    assert codes == ["none", "none", "rate"]
    assert c.decide("priya", "q", "a", "ctx")[2] == "none"
    assert c.decide("lee", "q", "a", "ctx")[2] == "none"
    assert c.decide("sam", "q", "a", "ctx")[2] == "rate"         # the service-wide 4 a minute


@pytest.mark.parametrize("reply,stop,code", [
    (lambda kind, text: RuntimeError("boom"), "end_turn", "model_error"),
    (lambda kind, text: "not json at all", "end_turn", "invalid_output"),
    (lambda kind, text: json.dumps({"title": "x", "accent": "blue", "blocks": [{"type": "nope"}]}),
     "end_turn", "invalid_output"),
    (lambda kind, text: "{}", "refusal", "refusal"),
])
def test_failures_fall_back_to_the_rules(tmp_path, reply, stop, code):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]
    _with_claude(app, svc, reply, stop=stop)
    v = _build(jd, rid, "a table").json()
    assert v["generated"] == "rules" and v["fallback"] == code
    assert v["model"]["model"] == "view-rules-v1" and v["model"]["fallback_used"] is True
    assert v["layout"] == ["table", "sources"]


def test_ledger_down_before_claude_means_no_call(tmp_path):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]
    fake, _ = _with_claude(app, svc, _page_reply(lambda tag: []))
    _fail(svc, {"deliverable.requested"})
    r = _decide(jd, rid, "Show the Indonesian SME FX margin by market as a table please")
    assert r.status_code == 503 and fake.calls == []


def test_read_page_is_lenient_per_block():
    page, dropped = read_page("```json\n" + json.dumps({"title": "T" * 200, "accent": "neon", "blocks": [
        {"type": "prose", "items": [{"text": "x" * 900, "tags": ["C1"]}]},
        {"type": "table", "style": {"tone": "dark"}, "columns": ["a"], "rows": [[{"text": "b", "tags": []}]]},
        {"type": "mystery"}]}) + "\n```")
    assert dropped == 1 and page["accent"] == "blue" and len(page["title"]) == 80
    assert len(page["blocks"][0]["items"][0]["text"]) == 400
    assert page["blocks"][1]["style"] == {"tone": "default", "accent": "blue"}
    assert read_page("prose before {}")[0] is None


# ------------------------------------------------------------------------------------------------
# Claude as the answer model (claude.py) and the follow-up rewrite (/ib/rewrite)
# ------------------------------------------------------------------------------------------------
def test_rewrite_turns_a_follow_up_into_a_standalone_question(tmp_path):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    assert jd.post("/ib/rewrite", json={"question": "what was said in slack?"}).json() == {
        "question": "what was said in slack?", "rewritten": False}            # no Claude: as typed
    seen = []

    def reply(kind, text):
        seen.append(text)
        return "What was said in Slack about the database migration blockers?"
    fake, _ = _with_claude(app, svc, reply)
    n = len(_events(svc))
    r = jd.post("/ib/rewrite", json={"question": "what was said in slack?",
                                     "previous": "Status of the database migration project?"}).json()
    assert r == {"question": "What was said in Slack about the database migration blockers?",
                 "rewritten": True}
    assert "Status of the database migration project?" in seen[0]
    # nothing read; one counts-only write-ahead row before the model call, never the words
    new = _events(svc)[n:]
    assert [(e.kind, e.stage, e.deliverable, e.req_id, e.doc_ids) for e in new] == [
        ("deliverable.requested", "rewrite", "view", None, ())]
    assert new[0].input_chars == len("what was said in slack?") + len(
        "Status of the database migration project?")
    for body in ({"question": "x", "user": "priya"}, {"question": ""}):
        assert jd.post("/ib/rewrite", json=body).status_code == 422


def test_claude_answers_go_through_the_grounding_wall(tmp_path):
    """A grounded Claude answer is served and labelled; an answer with an invented name or value
    is refused by the wall as a whole and the quoted answer is served instead (and says so)."""
    from databridge_ib.claude import claude_chain

    for invent in (False, True):
        seen: list = []

        def reply(kind, text, seen=seen, invent=invent):
            seen.append(text)
            tag = _tag_of(text, "SME pricing by market")
            extra = f" Zorblax Holdings sets a 9.9% margin [{tag}]." if invent else ""
            return f"Indonesian SME customers are priced at a 0.4% FX margin [{tag}].{extra}"
        fake = FakeClaude(reply)
        chain = claude_chain(tmp_path / f"views{invent}", client=fake)
        app, svc = _world(tmp_path / f"w{invent}", chain=chain)
        ans = _ask(_c(app, svc, "jdoe"))
        assert seen and all("Leads only" not in x and "CNRY" not in x for x in seen)
        assert "Zorblax" not in ans["answer"] and "9.9%" not in ans["answer"]
        if invent:
            # the wall refused the model's text: the answer says so (Story 1 fix #2)
            assert ans["model"] == {"provider": "deterministic", "model": "extractive-v1",
                                    "fallback_used": True, "reason_code": "model_rejected_evidence"}
        else:
            assert ans["model"] == {"provider": "standin", "model": "standin-1",
                                    "fallback_used": False}
            # the wall tags each value where it stands ("0.4 [C1]% FX margin"); the web gathers them
            assert re.sub(r"\s*\[C\d+\]", "", ans["answer"]) == \
                "Indonesian SME customers are priced at a 0.4% FX margin."
        from databridge_ib.model_guard import guard_dir
        spent = json.loads((guard_dir() / "spend.json").read_text())
        assert spent["calls"] >= 1 and spent["usd"] > 0
        assert {e["kind"] for e in spent["entries"]} == {"answer"}


def test_claude_answer_over_the_cap_falls_back_to_quoting(tmp_path):
    from databridge_ib.claude import claude_chain
    from databridge_ib.model_guard import Caps, ModelGuard

    fake = FakeClaude(lambda kind, text: "anything")
    chain = claude_chain(client=fake, guard=ModelGuard(tmp_path / "g", Caps(total_usd=0.0)))
    app, svc = _world(tmp_path / "w", chain=chain)
    ans = _ask(_c(app, svc, "jdoe"))
    assert fake.calls == [] and ans["model"]["model"] == "extractive-v1"
