"""FEAT8 (2026-10-02): approve crawl plans for every source at once, after ONE review.

The batch must be a loop over the per-source approval, never a second one. These tests pin:

* the batch route on BOTH estates (each estate's own database source, read from its manifest);
* an unauthenticated caller is refused on every batch route;
* a plan calling a write tool is never batch-approved, whatever is vouched;
* a vouch applies only to the tool ticked, on the source it was ticked for;
* per-source results are honest — each outcome is what is on disk afterwards, and a source the
  body does not name is not touched;
* each batch approval lands in the SAME hash-chained approval log as a single approval, with the
  same record shape, attributed to the principal and marked when the dev token made it;
* "run all approved" starts runs on approved read sources only, never on a write connection.

Every expectation is derived from the shared classifier (`plans.classify_plan_tools`) and the
package's own tool rule, never written down.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from databridge_gateway import auth

from conftest import auth_headers
from databridge_gateway.public_errors import STANDARD_FOR  # noqa: E402  (R-028 #11)
from test_fin_approvals_and_origins import DB_SOURCES, EXECUTOR, _sql_plan, dev_auth  # noqa: F401
from test_platform_routes import _RealApproveIngest, _cmd, _mount, approve, env  # noqa: F401  (fixtures)

#: Read-shaped names the shared classifier may classify as reads; the test uses the first that it
#: actually does (asserted non-vacuous), so nothing here decides what is a read.
_READ_CANDIDATES = ("read_text_file", "list_directory", "read_file", "get_file", "list_files")


def _live(monkeypatch, source_id, tools):
    """Patch the package's live tool listing for ``source_id`` (no server is launched), sharing
    the map `_sql_plan` uses so several sources can be live at once."""
    from databridge_mcp_ingest import plans

    live = dict(getattr(plans._live_tools, "_int3_tools", {}))
    live[source_id] = tools

    def _fake_live(conn, client, _live=live):
        return list(_live[conn.id])
    _fake_live._int3_tools = live
    monkeypatch.setattr(plans, "_live_tools", _fake_live)


def _doc_plan(source_id, list_tool, read_tool, monkeypatch) -> dict:
    """A proposed DOCUMENTS plan whose classes are the shared classifier's, against the live list
    the approval will see. Returns the classes."""
    from databridge_mcp_ingest import plans
    from databridge_mcp_ingest.client import ToolInfo
    from databridge_mcp_ingest.plans import CrawlPlan, ListSpec, ReadSpec, StepSpec, save_plan

    tools = [ToolInfo(name=t, description="", input_schema={"type": "object"})
             for t in dict.fromkeys((list_tool, read_tool))]
    _live(monkeypatch, source_id, tools)
    steps = [StepSpec(id="walk", strategy="documents", list=ListSpec(tool=list_tool),
                      read=ReadSpec(text_tool=read_tool))]
    classes = plans.classify_plan_tools(steps, tools, [])
    from test_platform_routes import bound_notes
    save_plan(CrawlPlan(source_id=source_id, status="proposed", steps=steps,
                        tool_classes=classes, tools_digest=plans.tools_digest(tools),
                        notes=bound_notes(source_id)))
    return classes


def _read_tool() -> str:
    from databridge_mcp_ingest import plans
    from databridge_mcp_ingest.client import ToolInfo
    from databridge_mcp_ingest.plans import ListSpec, ReadSpec, StepSpec

    for name in _READ_CANDIDATES:
        t = ToolInfo(name=name, description="", input_schema={"type": "object"})
        steps = [StepSpec(id="w", strategy="documents", list=ListSpec(tool=name),
                          read=ReadSpec(text_tool=name))]
        if plans.classify_plan_tools(steps, [t], [])[name].startswith("read:"):
            return name
    pytest.fail(f"the shared classifier classifies none of {_READ_CANDIDATES} as a read")


def _unclassified_tools(n: int) -> list[str]:
    """``n`` tool names the shared classifier leaves ``unclassified:unknown`` (derived)."""
    from databridge_mcp_ingest import plans
    from databridge_mcp_ingest.client import ToolInfo
    from databridge_mcp_ingest.plans import ListSpec, ReadSpec, StepSpec

    out = []
    for name in ("open_nodes", "fetch_blob", "walk_tree", "pull_item", "nodes", "blob"):
        t = ToolInfo(name=name, description="", input_schema={"type": "object"})
        steps = [StepSpec(id="w", strategy="documents", list=ListSpec(tool=name),
                          read=ReadSpec(text_tool=name))]
        if plans.classify_plan_tools(steps, [t], [])[name].startswith("unclassified:unknown"):
            out.append(name)
    assert len(out) >= n, out
    return out[:n]


def _review(c) -> dict:
    r = c.get("/source-batch/plans")
    assert r.status_code == 200, r.text
    return {row["source_id"]: row for row in r.json()["pending"]}


def _items(review: dict, vouch: dict | None = None, only=None) -> list[dict]:
    return [{"source_id": sid, "plan_digest": row["plan_digest"],
             "vouch_read_tools": list((vouch or {}).get(sid, []))}
            for sid, row in review.items() if only is None or sid in only]


def _log(source_id) -> list[dict]:
    from databridge_mcp_ingest.plans import plan_approvals_path, read_chained
    p = plan_approvals_path(source_id)
    return read_chained(p) if p.is_file() else []


# --- both estates ------------------------------------------------------------------------------
@pytest.mark.parametrize("estate", sorted(DB_SOURCES))
def test_batch_review_and_approve_on_each_estate(env, monkeypatch, estate):
    """The estate's database source (execute_sql, unclassified) and a read-only documents source
    are pending together; the review lists each one's calls and classes and offers a vouch for the
    executor only; one batch with the executor ticked approves both, as the principal."""
    db = DB_SOURCES[estate]
    docs = f"{estate}-docs"
    read = _read_tool()
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, db).status_code == 200 and _cmd(c, docs).status_code == 200
        db_class = _sql_plan(db, EXECUTOR, monkeypatch)
        doc_classes = _doc_plan(docs, read, read, monkeypatch)
        review = _review(c)
        assert set(review) == {db, docs}
        # each source's calls and classes are the plan's own
        for sid, classes in ((db, {EXECUTOR: db_class}), (docs, doc_classes)):
            listed = {t["tool"]: t["class"] for t in review[sid]["tools"]}
            assert listed == classes
            calls = [call for s in review[sid]["steps"] for call in s["calls"]]
            assert calls and all(call["class"] == classes[call["tool"]] for call in calls)
        # a vouch is offered exactly for the tools the plan records as unclassified
        want = {sid: sorted(t for t, cls in (({EXECUTOR: db_class} if sid == db else doc_classes)
                                              .items()) if cls.startswith("unclassified:unknown"))
                for sid in review}
        assert {sid: sorted(v["tool"] for v in row["vouchable"])
                for sid, row in review.items()} == want
        assert want[db] == [EXECUTOR]                                    # non-vacuous
        assert all(v["reason"] for row in review.values() for v in row["vouchable"])
        assert all(not row["blocked"] for row in review.values())

        r = c.post("/source-batch/plans/approve",
                   json={"items": _items(review, vouch=want), "approved_by": "mallory"})
        assert r.status_code == 200, r.text
        body = r.json()
    results = {x["source_id"]: x for x in body["results"]}
    assert {sid: x["outcome"] for sid, x in results.items()} == {db: "approved", docs: "approved"}
    assert body["by"] == "analyst-7" and body["dev_only"] is False
    for sid in (db, docs):
        plan = rt.ingest.load_plan(sid)
        assert plan.status == "approved" and plan.approved_by == "analyst-7"
    assert results[db]["tool_classes"][EXECUTOR] == "read:sql_guarded:vouched_by:analyst-7"


# --- authentication ----------------------------------------------------------------------------
def test_every_batch_route_refuses_an_unauthenticated_caller(env, monkeypatch):
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    sid = sorted(DB_SOURCES.values())[0]
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, sid).status_code == 200
        _sql_plan(sid, EXECUTOR, monkeypatch)
        digest = _review(c)[sid]["plan_digest"]
    item = {"source_id": sid, "plan_digest": digest, "vouch_read_tools": [EXECUTOR]}
    with TestClient(app) as anon:
        assert anon.get("/source-batch/plans").status_code == 401
        assert anon.post("/source-batch/plans/propose").status_code == 401
        assert anon.post("/source-batch/plans/approve", json={"items": [item]}).status_code == 401
        assert anon.post("/source-batch/runs").status_code == 401
    with TestClient(app, headers={"Authorization": "Bearer not-a-token"}) as forged:
        assert forged.post("/source-batch/plans/approve",
                           json={"items": [item]}).status_code == 401
    assert rt.ingest.load_plan(sid).status == "proposed" and _log(sid) == []


# --- a write is never batch-approved -----------------------------------------------------------
@pytest.mark.parametrize("estate", sorted(DB_SOURCES))
def test_a_write_tool_is_never_batch_approved(env, monkeypatch, estate):
    sid = DB_SOURCES[estate]
    write_tool = "write_rows"
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, sid).status_code == 200
        cls = _sql_plan(sid, write_tool, monkeypatch)
        assert not cls.startswith("read:")                            # the classifier's verdict
        review = _review(c)
        row = review[sid]
        assert row["batch_approvable"] is False and row["blocked"]
        assert write_tool not in {v["tool"] for v in row["vouchable"]}   # never offered a vouch
        # ...and ticking it anyway changes nothing
        ticked = c.post("/source-batch/plans/approve",
                        json={"items": _items(review, vouch={sid: [write_tool]})})
        plain = c.post("/source-batch/plans/approve", json={"items": _items(review)})
    for r in (ticked, plain):                    # ticked or not, the batch never approves it
        assert r.status_code == 200, r.text
        [res] = r.json()["results"]
        assert res["outcome"] == "needs_individual_review"
        assert res["code"] == "NEEDS_INDIVIDUAL_REVIEW"
        assert write_tool in res["reason"]
    assert rt.ingest.load_plan(sid).status == "proposed" and _log(sid) == []


# --- a vouch applies only to what was ticked -----------------------------------------------------
def test_a_vouch_applies_only_to_the_tool_and_source_it_was_ticked_for(env, monkeypatch):
    """`a` and `b` both call the executor; only `a`'s is ticked. `pair` has two unclassified
    tools; only one is ticked. `stray` is a read-only plan whose body vouches for a tool it does not
    call. Only `a` is approved, and only `a`'s log records a vouch."""
    a, b = sorted(DB_SOURCES.values())
    assert a != b
    two = _unclassified_tools(2)
    read = _read_tool()
    pair, stray = "pair-src", "stray-src"
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        for sid in (a, b, pair, stray):
            assert _cmd(c, sid).status_code == 200
        _sql_plan(a, EXECUTOR, monkeypatch)
        _sql_plan(b, EXECUTOR, monkeypatch)
        classes = _doc_plan(pair, two[0], two[1], monkeypatch)
        assert all(classes[t].startswith("unclassified:unknown") for t in two)
        _doc_plan(stray, read, read, monkeypatch)
        review = _review(c)
        assert {v["tool"] for v in review[pair]["vouchable"]} == set(two)
        assert review[stray]["vouchable"] == [] and review[stray]["batch_approvable"]
        r = c.post("/source-batch/plans/approve", json={"items": _items(
            review, vouch={a: [EXECUTOR], pair: [two[0]], stray: [EXECUTOR]})})
        assert r.status_code == 200, r.text
    out = {x["source_id"]: x for x in r.json()["results"]}
    assert {s: x["outcome"] for s, x in out.items()} == {
        a: "approved", b: "needs_individual_review", pair: "needs_individual_review",
        stray: "needs_individual_review"}
    assert out[a]["approval"]["vouched_read_tools"] == [EXECUTOR]
    assert EXECUTOR in out[b]["reason"]                      # b's executor was never ticked
    assert two[1] in out[pair]["reason"]                     # the pair tool that was NOT ticked
    assert EXECUTOR in out[stray]["reason"]                  # a vouch for a tool it does not call
    for sid in (b, pair, stray):
        assert rt.ingest.load_plan(sid).status == "proposed" and _log(sid) == [], sid
    assert [x["vouched_read_tools"] for x in _log(a)] == [[EXECUTOR]]


# --- per-source results are honest --------------------------------------------------------------
def test_per_source_results_match_what_is_on_disk(env, monkeypatch):
    read = _read_tool()
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    ok, changed, untouched, empty = "ok-src", "changed-src", "untouched-src", "noplan-src"
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        for sid in (ok, changed, untouched, empty):
            assert _cmd(c, sid).status_code == 200
        assert c.post("/sources", json={"mode": "connection", "connection": {
            "id": "writer", "purpose": "agent_write",
            "transport": {"type": "stdio", "command": "npx"}}}).status_code == 200
        for sid in (ok, changed, untouched):
            _doc_plan(sid, read, read, monkeypatch)
        listing = c.get("/source-batch/plans").json()
        assert {x["source_id"] for x in listing["not_crawled"]} == {"writer"}
        assert {x["source_id"] for x in listing["no_plan"]} == {empty}
        review = {row["source_id"]: row for row in listing["pending"]}
        items = _items(review, only={ok, changed})
        # the plan changes after the review: it is not what the person saw
        from databridge_mcp_ingest.plans import load_plan, save_plan
        p = load_plan(changed)
        save_plan(p.model_copy(update={"notes": [*p.notes, "edited after review"]}))
        items += [{"source_id": empty, "plan_digest": "x"},
                  {"source_id": "writer", "plan_digest": "x"},
                  {"source_id": "nope", "plan_digest": "x"},
                  {"source_id": ok, "plan_digest": review[ok]["plan_digest"]}]
        r = c.post("/source-batch/plans/approve", json={"items": items})
        assert r.status_code == 200, r.text
    rows = r.json()["results"]
    assert [x["source_id"] for x in rows] == [i["source_id"] for i in items]   # one row each
    codes = {(x["source_id"], x["outcome"], x.get("code"), x.get("reason_code")) for x in rows}
    # R-028 #11: a stale decision answers the standard code; the specific one is the reason_code
    assert codes == {(ok, "approved", None, None),
                     (changed, "refused", STANDARD_FOR["PLAN_CHANGED"], "PLAN_CHANGED"),
                     (empty, "refused", "NO_PLAN", None),
                     ("writer", "refused", "NOT_CRAWLED", None),
                     ("nope", "refused", "UNKNOWN_SOURCE", None),
                     (ok, "refused", "DUPLICATE", None)}
    assert all(x["reason"] for x in rows if x["outcome"] != "approved")
    # each reported outcome is the state on disk; a source the body did not name is untouched
    for sid in (ok, changed, untouched):
        on_disk = rt.ingest.load_plan(sid).status
        assert (on_disk == "approved") is (sid == ok), sid
        assert bool(_log(sid)) is (sid == ok), sid
    counts = r.json()["counts"]
    assert sum(counts.values()) == len(rows) and counts["approved"] == 1


# --- the same hash-chained approval log ----------------------------------------------------------
@pytest.mark.parametrize("estate", sorted(DB_SOURCES))
def test_a_batch_approval_is_logged_like_a_single_one(env, monkeypatch, dev_auth, estate):
    """One source approved singly, one in a batch, both with the dev token: both lines are in each
    source's hash-chained log (the chain verifies), carry the same fields, name the principal and
    are marked dev-only; the plan's recorded approval points at its log line."""
    db = DB_SOURCES[estate]
    single = f"{estate}-single"
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    with TestClient(app, headers=dev_auth) as c:
        assert _cmd(c, db).status_code == 200 and _cmd(c, single).status_code == 200
        _sql_plan(db, EXECUTOR, monkeypatch)
        _sql_plan(single, EXECUTOR, monkeypatch)
        assert approve(c, single, vouch_read_tools=[EXECUTOR]).status_code == 200
        review = _review(c)
        assert set(review) == {db}
        r = c.post("/source-batch/plans/approve",
                   json={"items": _items(review, vouch={db: [EXECUTOR]})})
        assert r.status_code == 200, r.text
    assert r.json()["dev_only"] is True
    [res] = r.json()["results"]
    assert res["outcome"] == "approved"
    [batch_line], [single_line] = _log(db), _log(single)            # read_chained verifies
    assert set(batch_line) == set(single_line)
    for line in (batch_line, single_line):
        assert line["event"] == "plan_approved"
        assert line["principal_id"] == auth.DEV_PRINCIPAL_ID
        assert line["dev_only"] is True and line["authenticated_by"] == auth.DEV_MECHANISM
    assert res["approval"]["log_hash"] == batch_line["hash"]


# --- propose all / run all -----------------------------------------------------------------------
def test_propose_all_skips_approved_and_write_connections(env):
    app, rt, _ = _mount(env)                       # FakeIngest: proposal never launches a server
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        for sid in ("p1", "p2", "done"):
            assert _cmd(c, sid).status_code == 200
        assert c.post("/sources", json={"mode": "connection", "connection": {
            "id": "writer", "purpose": "agent_write",
            "transport": {"type": "stdio", "command": "npx"}}}).status_code == 200
        assert c.post("/sources/done/plan").status_code == 200
        assert approve(c, "done").status_code == 200
        r = c.post("/source-batch/plans/propose")
        assert r.status_code == 200, r.text
    out = {x["source_id"]: (x["outcome"], x.get("code")) for x in r.json()["results"]}
    assert out == {"p1": ("proposed", None), "p2": ("proposed", None),
                   "done": ("skipped", "ALREADY_APPROVED"), "writer": ("skipped", "NOT_CRAWLED")}
    assert rt.ingest.load_plan("done").status == "approved"
    assert "writer" not in rt.ingest.plans


def test_run_all_starts_only_approved_read_sources(env):
    app, rt, _ = _mount(env)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        for sid in ("go1", "go2", "waiting"):
            assert _cmd(c, sid).status_code == 200
            assert c.post(f"/sources/{sid}/plan").status_code == 200
        for sid in ("go1", "go2"):
            assert approve(c, sid).status_code == 200
        assert c.post("/sources", json={"mode": "connection", "connection": {
            "id": "writer", "purpose": "agent_write",
            "transport": {"type": "stdio", "command": "npx"}}}).status_code == 200
        r = c.post("/source-batch/runs")
        assert r.status_code == 202, r.text
        rows = {x["source_id"]: x for x in r.json()["results"]}
        assert {s: x["outcome"] for s, x in rows.items()} == {
            "go1": "started", "go2": "started", "waiting": "skipped", "writer": "skipped"}
        assert rows["writer"]["code"] == "NOT_CRAWLED"
        for sid in ("go1", "go2"):
            run = c.get(f"/sources/{sid}/runs/{rows[sid]['run_id']}").json()
            assert run["started_by"] == "analyst-7"
    assert not rt.ingest.run_dir("writer", "x").parent.exists() or \
        not any(rt.ingest.run_dir("writer", "x").parent.iterdir())


# =================================================================================================
# Codex round 1 (R-027-PLX): purpose under the lock, the reviewed digest, an atomic approval, and
# one worker per run.
# =================================================================================================
def _writer(rt, sid="writer"):
    """A write connection, registered through the facade (no tool advert, so no server)."""
    rt.ingest.register_connection({"id": sid, "purpose": "agent_write", "transport": {
        "type": "stdio", "command": "npx"}})
    assert rt.ingest.load_connection(sid).purpose == "agent_write"


def _digest(c, sid) -> str:
    r = c.get(f"/sources/{sid}")
    assert r.status_code == 200, r.text
    assert r.json()["plan_digest"], r.json()                      # non-vacuous: a plan is shown
    return r.json()["plan_digest"]


def _bound_plan(rt, sid, status):
    """A FakeIngest plan bound (proposed AND, when approved, approved) to the connection as it is
    now, so the only thing left to refuse it is the gate under test."""
    from databridge_gateway.platform_runtime import PLAN_APPROVED_NOTE, PLAN_REVISION_NOTE
    from databridge_gateway.sources_routes import connection_revision

    from test_platform_routes import FakePlan
    rev = connection_revision(rt.ingest.load_connection(sid))
    notes = [f"{PLAN_REVISION_NOTE}{rev}"] + ([f"{PLAN_APPROVED_NOTE}{rev}"]
                                              if status == "approved" else [])
    rt.ingest.plans[sid] = FakePlan(source_id=sid, status=status, steps=["documents"],
                                    approved_by="someone" if status == "approved" else None,
                                    notes=notes)


def test_a_write_connection_is_never_proposed_approved_or_run_by_the_shared_helpers(env):
    """Codex r1 #1: the purpose is enforced INSIDE propose/approve/run (FakeIngest's own package
    calls do not check it, so only the gateway helper can refuse)."""
    app, rt, _ = _mount(env)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        _writer(rt)
        r = c.post("/sources/writer/plan")
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "NOT_CRAWLED", r.text
        assert "writer" not in rt.ingest.plans
        _bound_plan(rt, "writer", "proposed")
        r = c.post("/sources/writer/plan/approve",
                   json={"plan_digest": _digest(c, "writer")})
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "NOT_CRAWLED", r.text
        assert rt.ingest.approvals == [] and rt.ingest.plans["writer"].status == "proposed"
        _bound_plan(rt, "writer", "approved")
        r = c.post("/sources/writer/runs")
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "NOT_CRAWLED", r.text
    assert rt.jobs.list("ingest", "writer") == []


def test_run_all_rechecks_the_purpose_under_the_source_lock(env, monkeypatch):
    """Codex r1 #1 (race): run-all's listing says `ingest`, but by the time the source is admitted
    the connection on disk is a write connection with an approved, bound plan. Admission re-reads
    it under the lock and refuses; nothing runs."""
    app, rt, _ = _mount(env)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, "flip").status_code == 200
        snapshot = list(rt.ingest.list_connections())
        assert [getattr(x, "purpose", None) for x in snapshot] == ["ingest"]   # non-vacuous
        rt.ingest.register_connection({"id": "flip", "purpose": "agent_write", "transport": {
            "type": "stdio", "command": "npx"}}, overwrite=True)
        assert rt.ingest.load_connection("flip").purpose == "agent_write"
        _bound_plan(rt, "flip", "approved")
        monkeypatch.setattr(rt.ingest, "list_connections", lambda: snapshot)
        r = c.post("/source-batch/runs")
        assert r.status_code == 202, r.text
    [row] = r.json()["results"]
    assert (row["outcome"], row["code"]) == ("refused", "NOT_CRAWLED"), row
    assert rt.jobs.list("ingest", "flip") == []


def test_an_individual_approval_needs_the_reviewed_digest(env):
    """Codex r1 #2: no digest → refused; a plan re-proposed after the review → refused and
    nothing approved; the digest of the plan on screen → approved."""
    app, rt, _ = _mount(env)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, "rv").status_code == 200
        first = c.post("/sources/rv/plan").json()
        seen = _digest(c, "rv")
        assert first["plan_digest"] == seen                       # the proposal shows it too
        r = c.post("/sources/rv/plan/approve")
        assert r.status_code == 422 and r.json()["refusal"]["code"] == "PLAN_DIGEST_REQUIRED"
        r = c.post("/sources/rv/plan/approve", json={"vouch_read_tools": []})
        assert r.status_code == 422 and r.json()["refusal"]["code"] == "PLAN_DIGEST_REQUIRED"
        rt.ingest.tools = [*rt.ingest.tools, "another_tool"]       # plan B differs from plan A
        assert c.post("/sources/rv/plan").status_code == 200
        assert _digest(c, "rv") != seen                           # non-vacuous
        r = c.post("/sources/rv/plan/approve", json={"plan_digest": seen})
        assert r.status_code == 409 and (r.json()["refusal"]["code"], r.json()["refusal"].get("reason_code")) == (STANDARD_FOR["PLAN_CHANGED"], "PLAN_CHANGED"), r.text
        assert rt.ingest.approvals == [] and rt.ingest.load_plan("rv").status == "proposed"
        r = c.post("/sources/rv/plan/approve", json={"plan_digest": _digest(c, "rv")})
        assert r.status_code == 200, r.text
    assert rt.ingest.approvals == [("rv", "analyst-7")]


@pytest.mark.parametrize("estate", sorted(DB_SOURCES))
def test_an_individual_approval_of_a_changed_real_plan_is_refused(env, monkeypatch, estate):
    """Codex r1 #2 on each estate's database source, with the package's real approval."""
    from databridge_mcp_ingest.plans import load_plan, save_plan

    sid = DB_SOURCES[estate]
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, sid).status_code == 200
        _sql_plan(sid, EXECUTOR, monkeypatch)
        seen = _digest(c, sid)
        p = load_plan(sid)
        save_plan(p.model_copy(update={"notes": [*p.notes, "edited after review"]}))
        r = c.post(f"/sources/{sid}/plan/approve",
                   json={"plan_digest": seen, "vouch_read_tools": [EXECUTOR]})
        assert r.status_code == 409 and (r.json()["refusal"]["code"], r.json()["refusal"].get("reason_code")) == (STANDARD_FOR["PLAN_CHANGED"], "PLAN_CHANGED"), r.text
        assert load_plan(sid).status == "proposed" and _log(sid) == []
        r = c.post(f"/sources/{sid}/plan/approve",
                   json={"plan_digest": _digest(c, sid), "vouch_read_tools": [EXECUTOR]})
        assert r.status_code == 200, r.text


@pytest.mark.parametrize("estate", sorted(DB_SOURCES))
def test_approval_status_binding_and_log_are_one_write(env, monkeypatch, estate):
    """Codex r1 #3: the package writes status, the revision binding, the approval record and the
    chained log line in ONE approval — the gateway's separate stamp is no longer on the path (a
    failing stamp changes nothing) — and a failed save leaves nothing approved."""
    from databridge_mcp_ingest import plans
    from databridge_mcp_ingest.plans import load_plan, verify_plan_approval

    from databridge_gateway.platform_runtime import PLAN_APPROVED_NOTE, plan_binding
    from databridge_gateway.sources_routes import connection_revision

    sid = DB_SOURCES[estate]
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    real_stamp = rt.ingest.stamp_plan

    def _stamp(source_id, prefix, value):
        if prefix == PLAN_APPROVED_NOTE:
            raise OSError("stamp failed")
        return real_stamp(source_id, prefix, value)
    monkeypatch.setattr(rt.ingest, "stamp_plan", _stamp)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, sid).status_code == 200
        _sql_plan(sid, EXECUTOR, monkeypatch)
        # a save that fails: refused, and nothing on disk says approved
        real_save = plans.save_plan
        calls = []

        def _failing_save(plan, root=None):
            calls.append(plan.status)
            if plan.status == "approved":
                raise OSError("disk full")
            return real_save(plan, root)
        monkeypatch.setattr(plans, "save_plan", _failing_save)
        r = c.post(f"/sources/{sid}/plan/approve",
                   json={"plan_digest": _digest(c, sid), "vouch_read_tools": [EXECUTOR]})
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "PLAN_NOT_APPROVABLE"
        assert "approved" in calls                                 # non-vacuous: it was tried
        assert load_plan(sid).status == "proposed"
        assert c.post(f"/sources/{sid}/runs").status_code == 409
        monkeypatch.setattr(plans, "save_plan", real_save)
        # the real approval, with the gateway's approval stamp broken: approved AND bound
        r = c.post(f"/sources/{sid}/plan/approve",
                   json={"plan_digest": _digest(c, sid), "vouch_read_tools": [EXECUTOR]})
        assert r.status_code == 200, r.text
        rev = connection_revision(rt.ingest.load_connection(sid))
    on_disk = load_plan(sid)
    assert on_disk.status == "approved"
    assert plan_binding(on_disk, PLAN_APPROVED_NOTE) == rev
    logged = verify_plan_approval(on_disk)                          # log line == plan record
    assert logged["bind_notes"] == [f"{PLAN_APPROVED_NOTE}{rev}"]
    assert r.json()["plan"]["approval"]["log_hash"] == _log(sid)[-1]["hash"]


def test_a_failing_hold_listener_does_not_misreport_a_committed_approval(env, monkeypatch):
    """Codex r1 #3: once the approval is saved, a failure handing re-planned steps to the adapter
    agents does not turn it into a refusal (the holds stand; they are decided elsewhere)."""
    from databridge_mcp_ingest import plans

    sid = sorted(DB_SOURCES.values())[0]
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])

    def _boom(*a, **k):
        raise RuntimeError("listener failed")
    monkeypatch.setattr(plans, "resolve_step_holds", _boom)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, sid).status_code == 200
        _sql_plan(sid, EXECUTOR, monkeypatch)
        r = c.post(f"/sources/{sid}/plan/approve",
                   json={"plan_digest": _digest(c, sid), "vouch_read_tools": [EXECUTOR]})
    assert r.status_code == 200, r.text
    assert rt.ingest.load_plan(sid).status == "approved"


@pytest.mark.parametrize("estate", sorted(DB_SOURCES))
def test_a_plan_save_that_fails_midway_leaves_the_previous_plan(env, monkeypatch, estate):
    """Codex r1 #3: `save_plan` is whole-or-nothing. A write that dies half-way (simulated: half
    the text lands, then the disk fails) leaves the previous plan readable and unchanged."""
    import pathlib

    from databridge_mcp_ingest.plans import load_plan, plan_path, save_plan

    sid = DB_SOURCES[estate]
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, sid).status_code == 200
        _sql_plan(sid, EXECUTOR, monkeypatch)
    before = plan_path(sid).read_bytes()
    real_write = pathlib.Path.write_text

    def _dies_halfway(self, data, *a, **k):
        real_write(self, data[: len(data) // 2], *a, **k)
        raise OSError("disk failed mid-write")
    monkeypatch.setattr(pathlib.Path, "write_text", _dies_halfway)
    p = load_plan(sid)
    with pytest.raises(OSError):
        save_plan(p.model_copy(update={"status": "approved", "approved_by": "nobody"}))
    monkeypatch.setattr(pathlib.Path, "write_text", real_write)
    assert plan_path(sid).read_bytes() == before
    assert load_plan(sid).status == "proposed"
    assert not [x for x in plan_path(sid).parent.iterdir() if x.name.endswith(".tmp")]


def test_an_approved_plan_without_its_approval_binding_never_runs(env):
    """Codex r1 #3: fail closed — an approved plan that does not say which revision it was
    approved for is not run (by the route, or by the job at start); a merely proposed plan with
    no approval binding is not called stale."""
    from databridge_gateway.platform_runtime import PLAN_APPROVED_NOTE
    from databridge_gateway.sources_routes import (_ingest_job, connection_revision,
                                                   plan_stale_reason)

    app, rt, _ = _mount(env)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, "nb").status_code == 200
        c.post("/sources/nb/plan")
        rev = connection_revision(rt.ingest.load_connection("nb"))
        assert plan_stale_reason(rt.ingest.load_plan("nb"), rev, for_run=True) is None
        assert c.post("/sources/nb/plan/approve",
                      json={"plan_digest": _digest(c, "nb")}).status_code == 200
        p = rt.ingest.plans["nb"]
        assert any(n.startswith(PLAN_APPROVED_NOTE) for n in p.notes)       # non-vacuous
        rt.ingest.plans["nb"] = p.model_copy(update={
            "notes": [n for n in p.notes if not n.startswith(PLAN_APPROVED_NOTE)]})
        r = c.post("/sources/nb/runs")
        assert r.status_code == 409 and (r.json()["refusal"]["code"], r.json()["refusal"].get("reason_code")) == (STANDARD_FOR["PLAN_STALE"], "PLAN_STALE"), r.text
    job = rt.jobs.start("ingest", "nb", run_id="r-direct")
    _ingest_job(app, "nb", "r-direct", job["job_id"], rev)
    assert rt.jobs.get(job["job_id"])["status"] == "refused"


def _approved_fake(c, sid):
    assert _cmd(c, sid).status_code == 200
    assert c.post(f"/sources/{sid}/plan").status_code == 200
    assert c.post(f"/sources/{sid}/plan/approve",
                  json={"plan_digest": _digest(c, sid)}).status_code == 200


def test_run_all_runs_each_source_on_its_own_worker(env, monkeypatch):
    """Codex r1 #4: two runs meet at a barrier — they can only both pass if they execute at the
    same time. Serially, the first waits out the barrier and both fail."""
    import threading

    app, rt, _ = _mount(env)
    barrier = threading.Barrier(2, timeout=5)
    real = rt.ingest.run_ingest

    def _meet(sid, **kw):
        barrier.wait()
        return real(sid, **kw)
    monkeypatch.setattr(rt.ingest, "run_ingest", _meet)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        for sid in ("w1", "w2"):
            _approved_fake(c, sid)
        r = c.post("/source-batch/runs")
        assert r.status_code == 202, r.text
    rows = {x["source_id"]: x for x in r.json()["results"]}
    assert {s: x["outcome"] for s, x in rows.items()} == {"w1": "started", "w2": "started"}
    for sid in ("w1", "w2"):
        [job] = rt.jobs.list("ingest", sid)
        assert job["status"] == "completed", job


def test_a_run_is_reported_started_only_when_its_worker_accepted_it(env, monkeypatch):
    """Codex r1 #4: a worker that cannot be started is a refusal, the job is not left running,
    and the source is free again; a source already running stays single-flight."""
    from databridge_gateway import sources_routes as sr

    app, rt, _ = _mount(env)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        for sid in ("ok1", "busy1"):
            _approved_fake(c, sid)
        rt.jobs.start("ingest", "busy1", run_id="r-inflight")            # already running
        real = sr.dispatch_run

        def _refuse(app_, source_id, *a, **k):
            if source_id == "ok1":
                raise RuntimeError("cannot start a new thread")
            return real(app_, source_id, *a, **k)
        monkeypatch.setattr(sr, "dispatch_run", _refuse)
        r = c.post("/source-batch/runs")
        assert r.status_code == 202, r.text
    rows = {x["source_id"]: x for x in r.json()["results"]}
    assert (rows["ok1"]["outcome"], rows["ok1"]["code"]) == ("refused", "RUN_NOT_STARTED")
    assert (rows["busy1"]["outcome"], rows["busy1"]["code"]) == ("refused", "RUN_IN_PROGRESS")
    [job] = rt.jobs.list("ingest", "ok1")
    assert job["status"] == "failed" and not rt.jobs.busy("ingest", "ok1")



# =================================================================================================
# Codex round 2 (R-027-PLX): dual-use tools, the approval TOCTOU, a broken chain, stray vouches and
# an atomic proposal.
# =================================================================================================
def _dual_use_tool(name="files"):
    """A tool whose MODE argument is a closed set holding a read value and a write value, decided
    by the shared write rule (`plans.writes_by_name`) — asserted, never assumed."""
    from databridge_mcp_ingest.client import ToolInfo
    from databridge_mcp_ingest.plans import writes_by_name

    modes = ["list", "delete"]
    assert [writes_by_name(m) for m in modes] == [False, True]          # non-vacuous
    assert not writes_by_name(name)                                     # its NAME says nothing
    return ToolInfo(name=name, description="", input_schema={
        "type": "object", "properties": {"action": {"type": "string", "enum": modes},
                                         "path": {"type": "string"}}}), modes


def _dual_use_plan(sid, monkeypatch, *, list_mode, read_mode):
    from databridge_mcp_ingest import plans
    from databridge_mcp_ingest.plans import CrawlPlan, ListSpec, ReadSpec, StepSpec, save_plan

    from test_platform_routes import bound_notes
    tool, _modes = _dual_use_tool()
    _live(monkeypatch, sid, [tool])
    steps = [StepSpec(id="walk", strategy="documents",
                      list=ListSpec(tool=tool.name, args={"action": list_mode}),
                      read=ReadSpec(text_tool=tool.name, text_args={"action": read_mode,
                                                                    "path": "{path}"}))]
    classes = plans.classify_plan_tools(steps, [tool], [])
    save_plan(CrawlPlan(source_id=sid, status="proposed", steps=steps, tool_classes=classes,
                        tools_digest=plans.tools_digest([tool]), notes=bound_notes(sid)))
    return tool, classes


def test_dual_use_is_detected_from_the_schema_not_a_name():
    """Structural: a closed set of values (enum / const / oneOf / anyOf) holding a value the shared
    write rule calls a write marks the argument a mode selector. A closed set with no write value
    (a sort order) does not."""
    from databridge_mcp_ingest.client import ToolInfo
    from databridge_mcp_ingest.plans import dual_use_selectors

    tool, modes = _dual_use_tool()
    assert dual_use_selectors(tool) == {"action": [m for m in modes if m == "delete"]}
    for schema in ({"oneOf": [{"const": "read"}, {"const": "purge_all"}]},
                   {"anyOf": [{"enum": ["get"]}, {"enum": ["remove"]}]},
                   {"type": "array", "items": {"enum": ["get", "update"]}}):
        t = ToolInfo(name="thing", input_schema={"properties": {"op": schema}})
        assert dual_use_selectors(t), schema
    sort = ToolInfo(name="thing", input_schema={"properties": {"order": {"enum": ["asc", "desc"]}}})
    assert dual_use_selectors(sort) == {}


@pytest.mark.parametrize("estate", sorted(DB_SOURCES))
def test_a_dual_use_tool_is_never_vouchable_single_or_batch(env, monkeypatch, estate):
    """Codex r2 #1: `files(action: list|delete)` is never offered a vouch, a vouch for it is
    refused singly and in a batch, and nothing is approved — even when every fixed mode reads."""
    sid = f"{estate}-files"
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, sid).status_code == 200
        tool, classes = _dual_use_plan(sid, monkeypatch, list_mode="list", read_mode="list")
        assert classes[tool.name].startswith("write:"), classes
        review = _review(c)
        assert review[sid]["vouchable"] == [] and review[sid]["blocked"]
        r = c.post("/source-batch/plans/approve",
                   json={"items": _items(review, vouch={sid: [tool.name]})})
        assert r.json()["results"][0]["outcome"] == "needs_individual_review", r.text
        r = c.post(f"/sources/{sid}/plan/approve",
                   json={"plan_digest": _digest(c, sid), "vouch_read_tools": [tool.name]})
        assert r.status_code == 409, r.text
    assert rt.ingest.load_plan(sid).status == "proposed" and _log(sid) == []


def test_a_step_that_fixes_a_mutating_mode_is_refused_by_validation():
    """Codex r2 #1: a step whose fixed arguments select a write value — or leave the mode to the
    server — is a problem; a step that fixes a read value is not."""
    from databridge_mcp_ingest.plans import ListSpec, ReadSpec, StepSpec, validate_steps

    tool, modes = _dual_use_tool()
    read, write = modes

    def steps(list_args):
        return [StepSpec(id="walk", strategy="documents", list=ListSpec(tool=tool.name,
                                                                        args=list_args),
                         read=ReadSpec(text_tool=tool.name,
                                       text_args={"action": read, "path": "{path}"}))]
    bad = validate_steps(steps({"action": write}), [tool], has_resources=False)
    assert any(write in p and "action" in p for p in bad), bad
    unset = validate_steps(steps({}), [tool], has_resources=False)
    assert any("action" in p for p in unset), unset
    templated = validate_steps(steps({"action": "{name}"}), [tool], has_resources=False)
    assert any("action" in p for p in templated), templated
    ok = validate_steps(steps({"action": read}), [tool], has_resources=False)
    assert not any("action" in p for p in ok), ok


def test_every_crawl_call_refuses_a_mutating_mode():
    """Codex r2 #1, at call time: `ReadOnlyClient` reads the LIVE schema and refuses a call that
    selects a write value, or leaves the selector out, whatever any plan said."""
    from databridge_mcp_ingest.client import McpClientError, ReadOnlyClient, ToolResult

    tool, modes = _dual_use_tool()
    read, write = modes
    calls = []

    class Inner:
        def list_tools(self):
            return [tool]

        def call_tool(self, name, arguments):
            calls.append((name, dict(arguments)))
            return ToolResult(parts=[], is_error=False)

    client = ReadOnlyClient(Inner())
    for args in ({"action": write, "path": "a"}, {"path": "a"}, {"action": [write]}):
        with pytest.raises(McpClientError):
            client.call_tool(tool.name, args)
    assert calls == []
    client.call_tool(tool.name, {"action": read, "path": "a"})
    assert calls == [(tool.name, {"action": read, "path": "a"})]


def test_the_batch_review_shows_every_fixed_argument_and_the_sql_configuration(env, monkeypatch):
    """Codex r2 #1 (UI): each call carries the step's complete fixed arguments; a SQL call its
    statement argument, dialect, parse mode and extra arguments — as stored in the plan."""
    from databridge_mcp_ingest.plans import load_plan

    db = sorted(DB_SOURCES.values())[0]
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, db).status_code == 200 and _cmd(c, "files-x").status_code == 200
        _sql_plan(db, EXECUTOR, monkeypatch)
        _dual_use_plan("files-x", monkeypatch, list_mode="list", read_mode="list")
        review = _review(c)
    for sid in (db, "files-x"):
        plan = load_plan(sid)
        shown = {(s["step"], call["role"]): call for s in review[sid]["steps"]
                 for call in s["calls"]}
        assert shown                                                   # non-vacuous
        for step in plan.steps:
            if step.list:
                assert shown[(step.id, "list")]["args"] == step.list.args
            if step.read and step.read.text_tool:
                assert shown[(step.id, "read text")]["args"] == step.read.text_args
            if step.sql:
                assert shown[(step.id, "sql")]["sql"] == step.sql.model_dump(mode="json")


@pytest.mark.parametrize("estate", sorted(DB_SOURCES))
def test_a_plan_changed_after_the_gateway_check_is_not_approved_by_the_package(
        env, monkeypatch, estate):
    """Codex r2 #2: a writer that does not take the gateway's lock (the CLI, another process)
    saves plan B between the gateway's digest check and the package's load. The package compares
    the digest and the connection revision against what IT loaded, so B is not approved."""
    from databridge_mcp_ingest.plans import load_plan, save_plan

    sid = DB_SOURCES[estate]
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    real = rt.ingest.approve_plan
    sneak = {"what": "plan"}

    def _racing(source_id, **kw):
        if sneak["what"] == "plan":
            p = load_plan(source_id)
            save_plan(p.model_copy(update={"notes": [*p.notes, "plan B, saved by the CLI"]}))
        else:
            rt.ingest.register_connection({"id": source_id, "transport": {
                "type": "stdio", "command": "npx", "args": ["-y", "other"]}}, overwrite=True)
        return real(source_id, **kw)
    monkeypatch.setattr(rt.ingest, "approve_plan", _racing)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, sid).status_code == 200
        _sql_plan(sid, EXECUTOR, monkeypatch)
        r = c.post(f"/sources/{sid}/plan/approve",
                   json={"plan_digest": _digest(c, sid), "vouch_read_tools": [EXECUTOR]})
        assert r.status_code == 409 and (r.json()["refusal"]["code"], r.json()["refusal"].get("reason_code")) == (STANDARD_FOR["PLAN_CHANGED"], "PLAN_CHANGED"), r.text
        assert load_plan(sid).status == "proposed" and _log(sid) == []
        sneak["what"] = "connection"
        r = c.post(f"/sources/{sid}/plan/approve",
                   json={"plan_digest": _digest(c, sid), "vouch_read_tools": [EXECUTOR]})
        assert r.status_code == 409 and (r.json()["refusal"]["code"], r.json()["refusal"].get("reason_code")) == (STANDARD_FOR["PLAN_STALE"], "PLAN_STALE"), r.text
    assert load_plan(sid).status == "proposed" and _log(sid) == []


def _child_env():
    """This process's environment (the test's state dirs) and its import path."""
    import os
    import sys
    return {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}


def _hold_lock_in_another_process(sid, seconds):
    """A second PROCESS that takes the plan lock for ``sid`` and holds it."""
    import os
    import subprocess
    import sys

    code = ("import sys,time; from databridge_mcp_ingest.plans import plan_lock\n"
            "with plan_lock(sys.argv[1]):\n"
            "    print('held', flush=True); time.sleep(float(sys.argv[2]))\n")
    proc = subprocess.Popen([sys.executable, "-c", code, sid, str(seconds)],
                            stdout=subprocess.PIPE, text=True, env=_child_env())
    assert proc.stdout.readline().strip() == "held"
    return proc


def test_plan_writers_share_one_interprocess_lock(env, monkeypatch):
    """Codex r2 #2: every plan save takes the same OS file lock, across processes; a writer that
    cannot get it fails closed (nothing written) instead of writing unlocked."""
    from databridge_mcp_ingest.plans import PlanError, load_plan, plan_path, save_plan

    sid = sorted(DB_SOURCES.values())[0]
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, sid).status_code == 200
        _sql_plan(sid, EXECUTOR, monkeypatch)
    before = plan_path(sid).read_bytes()
    monkeypatch.setenv("WALNUT_PLAN_LOCK_TIMEOUT_S", "0.5")
    proc = _hold_lock_in_another_process(sid, 4)
    try:
        p = load_plan(sid)
        with pytest.raises(PlanError):
            save_plan(p.model_copy(update={"notes": [*p.notes, "written while locked"]}))
        assert plan_path(sid).read_bytes() == before
    finally:
        proc.wait(timeout=30)
    save_plan(load_plan(sid).model_copy(update={"notes": ["after the lock"]}))
    assert load_plan(sid).notes == ["after the lock"]


def test_approval_holds_the_plan_lock_from_load_to_save(env, monkeypatch):
    """Codex r2 #2: while the package approves (here: while it lists the live server), another
    process cannot save the plan — so the plan it verified is the plan it saves."""
    import os
    import subprocess
    import sys

    from databridge_mcp_ingest import plans

    sid = sorted(DB_SOURCES.values())[0]
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    real_state = plans._live_state
    outcome = {}

    def _state_while_another_process_saves(conn, client, plan):
        code = ("import os,sys; os.environ['WALNUT_PLAN_LOCK_TIMEOUT_S']='0.5'\n"
                "from databridge_mcp_ingest.plans import load_plan, save_plan\n"
                "p = load_plan(sys.argv[1])\n"
                "save_plan(p.model_copy(update={'notes': [*p.notes, 'other process']}))\n")
        r = subprocess.run([sys.executable, "-c", code, conn.id], capture_output=True,
                           text=True, env=_child_env(), timeout=60)
        outcome["rc"], outcome["err"] = r.returncode, r.stderr
        return real_state(conn, client, plan)
    monkeypatch.setattr(plans, "_live_state", _state_while_another_process_saves)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, sid).status_code == 200
        _sql_plan(sid, EXECUTOR, monkeypatch)
        r = c.post(f"/sources/{sid}/plan/approve",
                   json={"plan_digest": _digest(c, sid), "vouch_read_tools": [EXECUTOR]})
        assert r.status_code == 200, r.text
    assert outcome["rc"] != 0 and "lock" in outcome["err"], outcome  # the other writer was held off
    on_disk = plans.load_plan(sid)
    assert on_disk.status == "approved" and "other process" not in on_disk.notes


@pytest.mark.parametrize("estate", sorted(DB_SOURCES))
def test_an_approval_onto_a_broken_chain_is_refused_and_changes_nothing(env, monkeypatch, estate):
    """Codex r2 #3: an earlier approval line was edited. The next approval verifies the whole
    chain first and is refused: the plan stays proposed and the log is not appended to."""
    from databridge_mcp_ingest.plans import (PlanError, append_chained, load_plan,
                                             plan_approvals_path)

    sid = DB_SOURCES[estate]
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, sid).status_code == 200
        _sql_plan(sid, EXECUTOR, monkeypatch)
        assert c.post(f"/sources/{sid}/plan/approve", json={
            "plan_digest": _digest(c, sid), "vouch_read_tools": [EXECUTOR]}).status_code == 200
        log = plan_approvals_path(sid)
        lines = log.read_text(encoding="utf-8").splitlines()
        assert lines                                                   # non-vacuous
        log.write_text("\n".join([lines[0].replace('"analyst-7"', '"mallory"'), *lines[1:]])
                       + "\n", encoding="utf-8")
        tampered = log.read_bytes()
        _sql_plan(sid, EXECUTOR, monkeypatch)                          # proposed again
        r = c.post(f"/sources/{sid}/plan/approve", json={
            "plan_digest": _digest(c, sid), "vouch_read_tools": [EXECUTOR]})
        assert r.status_code == 409, r.text
        assert "chain" in r.json()["refusal"]["message"]
    assert load_plan(sid).status == "proposed"
    assert log.read_bytes() == tampered
    with pytest.raises(PlanError):                                     # and at the primitive
        append_chained(log, {"event": "x"})
    assert log.read_bytes() == tampered


@pytest.mark.parametrize("estate", sorted(DB_SOURCES))
def test_a_single_approval_refuses_a_stray_vouch_and_logs_only_applied_ones(
        env, monkeypatch, estate):
    """Codex r2 #4: the eligible vouches are the live plan's unclassified tools. A vouch for any
    other tool (absent, or a read) is refused singly as in a batch; an accepted approval records
    exactly the vouches that were applied."""
    from databridge_mcp_ingest.plans import load_plan

    sid = DB_SOURCES[estate]
    read = _read_tool()
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, sid).status_code == 200 and _cmd(c, "plain-docs").status_code == 200
        _sql_plan(sid, EXECUTOR, monkeypatch)
        _doc_plan("plain-docs", read, read, monkeypatch)
        for stray in (["delete_everything"], [read]):
            r = c.post(f"/sources/{sid}/plan/approve", json={
                "plan_digest": _digest(c, sid), "vouch_read_tools": [EXECUTOR, *stray]})
            assert r.status_code == 409 and stray[0] in r.json()["refusal"]["message"], r.text
            assert load_plan(sid).status == "proposed" and _log(sid) == []
        r = c.post("/sources/plain-docs/plan/approve", json={
            "plan_digest": _digest(c, "plain-docs"), "vouch_read_tools": [read]})
        assert r.status_code == 409, r.text                     # a read is not vouchable either
        r = c.post(f"/sources/{sid}/plan/approve", json={
            "plan_digest": _digest(c, sid), "vouch_read_tools": [EXECUTOR]})
        assert r.status_code == 200, r.text
    assert r.json()["plan"]["approval"]["vouched_read_tools"] == [EXECUTOR]
    assert [x["vouched_read_tools"] for x in _log(sid)] == [[EXECUTOR]]


def test_a_proposal_and_its_revision_binding_are_one_save(env, monkeypatch):
    """Codex r2 #5 (gateway): the binding is handed to the package's proposal, not stamped after
    it — a failing stamp changes nothing — and the proposal is bound to the connection as it is."""
    from databridge_gateway.platform_runtime import PLAN_REVISION_NOTE, plan_binding
    from databridge_gateway.sources_routes import connection_revision

    app, rt, _ = _mount(env)

    def _no_stamp(*a, **k):
        raise OSError("stamp failed")
    monkeypatch.setattr(rt.ingest, "stamp_plan", _no_stamp)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, "pa").status_code == 200
        r = c.post("/sources/pa/plan")
        assert r.status_code == 200, r.text
    rev = connection_revision(rt.ingest.load_connection("pa"))
    assert plan_binding(rt.ingest.load_plan("pa"), PLAN_REVISION_NOTE) == rev


def test_the_package_proposal_writes_its_binding_in_the_same_save(env, monkeypatch):
    """Codex r2 #5 (package): `propose_plan(bind_notes=...)` saves the plan once, with the notes."""
    from databridge_mcp_ingest import plans
    from databridge_mcp_ingest.client import ToolResult

    read = _read_tool()
    from databridge_mcp_ingest.client import ToolInfo
    listing = [ToolInfo(name=read, input_schema={"type": "object"})]

    class Client:
        supports_resources = False

        def list_tools(self):
            return listing

        def call_tool(self, name, arguments):
            return ToolResult(parts=[], is_error=False)

    app, rt, _ = _mount(env)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, "pk").status_code == 200
    saves = []
    real_save = plans.save_plan
    monkeypatch.setattr(plans, "save_plan", lambda p, root=None: saves.append(list(p.notes))
                        or real_save(p, root))
    plan = plans.propose_plan("pk", client=Client(), probe=False, bind_notes=["bound:rev-1"])
    assert saves and all("bound:rev-1" in n for n in saves), saves
    assert plans.load_plan("pk").notes[-1] == "bound:rev-1" and plan.notes[-1] == "bound:rev-1"


def test_an_unbound_proposal_cannot_be_approved(env):
    """Codex r2 #5: a proposal that does not record the connection revision it was proposed for
    is stale: it is proposed again, never approved."""
    from test_platform_routes import FakePlan

    app, rt, _ = _mount(env)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, "ub").status_code == 200
        rt.ingest.plans["ub"] = FakePlan(source_id="ub", steps=["documents"])
        r = c.post("/sources/ub/plan/approve", json={"plan_digest": _digest(c, "ub")})
        assert r.status_code == 409 and (r.json()["refusal"]["code"], r.json()["refusal"].get("reason_code")) == (STANDARD_FOR["PLAN_STALE"], "PLAN_STALE"), r.text
    assert rt.ingest.approvals == []


def test_the_gateway_and_package_digest_the_same_plan_the_same_way(env, monkeypatch):
    from databridge_mcp_ingest.plans import load_plan, plan_review_digest

    from databridge_gateway.sources_routes import plan_digest

    sid = sorted(DB_SOURCES.values())[0]
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, sid).status_code == 200
        _sql_plan(sid, EXECUTOR, monkeypatch)
    assert plan_digest(load_plan(sid)) == plan_review_digest(load_plan(sid))
