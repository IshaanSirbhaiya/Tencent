"""S4 — every route produces the SAME capture and pointer shapes (source_id == connection id).

One real fixture server (a stdio subprocess, synthetic corpus) is ingested twice under the same
connection id: once direct, once "through MCPJungle" — the stateful fake aggregator from
`test_s4_jungle` proxying every tool call and resource read to a live session on that server,
over the exact HTTP shapes the real aggregator speaks. Item ids, capture digests and pointer
records must match exactly; only run ids and timestamps may differ.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import databridge_mcp_ingest as mi
from databridge_mcp_ingest.client import JungleClient, SdkClient
from databridge_mcp_ingest.connections import with_route
from databridge_mcp_ingest.ledger import read_jsonl

from conftest import fs_connection
from test_s4_jungle import FakeJungle

_VOLATILE = {"at", "retrieved_at", "run_id", "ts", "started_at", "ended_at", "event_id", "built_at",
             "meta_digest",          # W1: the digest covers retrieved_at
             "capture_meta_digest"}  # INT3: the `.txt.json` sidecar's copy of that same digest


def _strip(rec):
    if isinstance(rec, dict):
        return {k: _strip(v) for k, v in rec.items() if k not in _VOLATILE}
    if isinstance(rec, list):
        return [_strip(v) for v in rec]
    return rec


def _point_env(monkeypatch, base: Path) -> Path:
    for k, sub in {"WALNUT_SOURCES_DIR": "sources", "WALNUT_INGEST_DIR": "ingest",
                   "WALNUT_CAPTURE_DIR": "capture"}.items():
        monkeypatch.setenv(k, str(base / sub))
    monkeypatch.setenv("WALNUT_EVENTS_PATH", str(base / "events" / "events.jsonl"))
    db = base / "graph" / "g.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WALNUT_GRAPH_DB_PATH", str(db))
    return db


def _run(monkeypatch, base: Path, spec: dict, harness, client=None):
    from databridge_graph.store import GraphStore

    db = _point_env(monkeypatch, base)
    mi.register_connection(spec)
    plan = mi.propose_plan(spec["id"], client=client)
    assert plan.status == "approved" and not plan.problems, plan.problems
    report = mi.run_ingest(spec["id"], store=GraphStore(str(db)), harness=harness, client=client)
    assert report.status == "ok", report.message
    run_dir = Path(report.run_dir)
    out = {}
    for name in ("inventory", "captured", "excluded", "blocked", "extracted", "pointers",
                 "build"):
        rows = [_strip(r) for r in read_jsonl(run_dir / f"{name}.jsonl")]
        out[name] = sorted(rows, key=lambda r: json.dumps(r, sort_keys=True))
    capture = base / "capture"
    metas = {}
    for p in capture.rglob("*.json"):
        if p.name.endswith(".txt.json"):     # INT3: the text-digest sidecar, compared on its own
            metas.setdefault("_text_sidecars", {})[p.name] = _strip(
                json.loads(p.read_text(encoding="utf-8")))
            continue
        metas[p.stem] = _strip(json.loads(p.read_text(encoding="utf-8")))
    return plan, out, metas


def test_direct_and_mcpjungle_routes_mint_identical_items_captures_and_pointers(
        tmp_path, corpus, monkeypatch, common_vocab, fake_harness):
    root, outcomes = corpus
    spec = fs_connection("same_source", root)

    plan_d, direct, metas_d = _run(monkeypatch, tmp_path / "direct", spec, fake_harness)

    # the same server, reached through the (fake) aggregator under the same connection id
    jungle = FakeJungle()
    live = SdkClient(mi.SourceConnection.model_validate(spec))
    try:
        api = jungle.api()
        jungle.servers["same_source"] = {"server": {"name": "same_source",
                                                    "transport": "stdio"}}
        jungle.proxies["same_source"] = live
        via = with_route(mi.SourceConnection.model_validate(spec), "mcpjungle").model_dump(
            mode="json")
        client = JungleClient(mi.SourceConnection.model_validate(via), backend=api)
        plan_j, routed, metas_j = _run(monkeypatch, tmp_path / "jungle", via, fake_harness,
                                       client=client)
    finally:
        live.close()

    invoked = [b["name"] for m, p, b in jungle.requests if p == "/api/v0/tools/invoke"]
    assert invoked and all(n.startswith("same_source__") for n in invoked)
    assert plan_d.profiles == plan_j.profiles and plan_d.steps == plan_j.steps

    collected = [r for r, o in outcomes.items() if o == "collect"]
    assert len(direct["captured"]) == len(collected)                 # non-vacuous
    assert direct["pointers"], "no pointers minted — the comparison would be vacuous"
    for name in direct:
        assert direct[name] == routed[name], f"{name} differs between routes"
    assert metas_d.get("_text_sidecars"), "no text sidecar written — vacuous"
    assert metas_d == metas_j
    assert {m["source"] for k, m in metas_d.items() if k != "_text_sidecars"} == {"same_source"}


def test_resources_have_the_same_identity_on_both_routes(tmp_path, fixture_db):
    """Resource listing and reads through the aggregator report the SERVER's own URI."""
    import sys

    from conftest import FIXTURE_SERVER

    db, _ = fixture_db
    spec = {"id": "res_src", "transport": {"type": "stdio", "command": sys.executable,
                                           "args": [str(FIXTURE_SERVER), "--db", str(db)]}}
    conn = mi.SourceConnection.model_validate(spec)
    live = SdkClient(conn)
    try:
        jungle = FakeJungle()
        jungle.servers["res_src"] = {"server": {"name": "res_src", "transport": "stdio"}}
        jungle.proxies["res_src"] = live
        routed = JungleClient(with_route(conn, "mcpjungle"), backend=jungle.api())
        direct_res = live.list_resources()
        routed_res = routed.list_resources()
        assert direct_res, "the fixture lists no resources — vacuous"
        assert [(r.uri, r.mime) for r in direct_res] == [(r.uri, r.mime) for r in routed_res]
        for r in direct_res:
            assert live.read_resource(r.uri) == routed.read_resource(r.uri)
        assert all(u.startswith("mcpj://res/res_src/")
                   for _, p, b in jungle.requests if p == "/api/v0/resources/read"
                   for u in [b["uri"]])
    finally:
        live.close()
