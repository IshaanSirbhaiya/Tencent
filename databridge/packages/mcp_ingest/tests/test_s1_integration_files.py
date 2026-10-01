"""S1 integration — the WHOLE Larkspur corpus through the real walnut-files-blob server over stdio.

Launches the server exactly as the shipped declaration says
(profiles/files/connections/larkspur-files.connection.yaml), proposes the plan from its live
tools/list, runs every stage into a temp store, and checks the artefacts — not the intentions (P8):

  * every expected_outcomes.yaml decision, and the allow/review/block split derived from it;
  * the '#' folder item captured; both same-named briefs distinct (identity = full path);
  * every captured digest == sha256 of the file ON DISK (original bytes, not a conversion);
  * PDF/DOCX text present in the capture store, extracted by docling;
  * every pointer resolves: the capture re-hashes to its name and each labelled span contains
    its label — including spans inside PDF- and DOCX-derived text;
  * report.json reconciles with the corpus on disk.

Marked `integration`: it spawns a subprocess and runs docling (tens of seconds). No network, no
live model — the fake model returns entity offsets it COMPUTES from the text it is sent, for
names read from the synthetic database seed (fixtures/workspace_demo/db).
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import pytest
import yaml

import databridge_mcp_ingest as mi
from databridge_mcp_ingest import capture
from databridge_mcp_ingest.ledger import read_jsonl
from databridge_mcp_ingest.paths import DATABRIDGE_ROOT, bundled_profiles_dir

pytestmark = pytest.mark.integration

DEMO = DATABRIDGE_ROOT / "fixtures" / "workspace_demo"
FILES = DEMO / "files"
DECL = bundled_profiles_dir() / "files" / "connections" / "larkspur-files.connection.yaml"


def _seed_names() -> dict[str, str]:
    """name -> kind, from the synthetic DB seed: customers are organisations; employees and
    contacts are people (the second column of each VALUES tuple)."""
    sql = (DEMO / "db" / "01_schema_and_data.sql").read_text(encoding="utf-8")
    kinds = {"customers": "organisation", "employees": "person", "contacts": "person"}
    out: dict[str, str] = {}
    for m in re.finditer(r"INSERT INTO (\w+)\s*\([^)]*\)\s*VALUES(.*?);", sql, re.S):
        kind = kinds.get(m.group(1))
        if kind:
            for t in re.finditer(r"\(\s*'[^']*'\s*,\s*'([^']*)'", m.group(2)):
                out[t.group(1)] = kind
    return out


def _harness(names: dict[str, str]):
    from databridge_harness import FakeBackend, Harness

    def handler(*, model, messages, guided_json) -> str:
        last = messages[-1]["content"]
        if not last.startswith("DOCUMENT TEXT:\n"):
            return json.dumps({})
        text = last[len("DOCUMENT TEXT:\n"):]
        ents = []
        for name, kind in names.items():
            i = text.find(name)
            if i >= 0:
                ents.append({"name": name, "kind": kind, "char_start": i,
                             "char_end": i + len(name)})
        return json.dumps({"entities": ents, "relations": []})

    return Harness(FakeBackend(handler), profile="cloud")


@pytest.mark.skipif(not FILES.is_dir(), reason="the Larkspur demo corpus is missing")
def test_larkspur_corpus_end_to_end_through_walnut_files_blob(walnut_env, tmp_path, monkeypatch):
    from databridge_graph.packs import deployment
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest.extract import docling_available

    if not docling_available():
        pytest.skip("docling not installed")
    if not (DATABRIDGE_ROOT / "packs" / "workspace" / "screen.yaml").is_file():
        pytest.skip("packs/workspace not present")
    monkeypatch.setenv("WALNUT_PYTHON", sys.executable)
    monkeypatch.setenv("PYTHONUNBUFFERED", "1")
    expected = yaml.safe_load((DEMO / "expected_outcomes.yaml").read_text(encoding="utf-8"))["files"]
    on_disk = {p.relative_to(FILES).as_posix() for p in FILES.rglob("*") if p.is_file()}
    assert set(expected) == on_disk, "expected_outcomes.yaml and the corpus disagree"
    names = _seed_names()
    assert names, "vacuous: no seed names parsed"

    decl = yaml.safe_load(DECL.read_text(encoding="utf-8"))
    decl["auto_approve"] = True
    timings: dict[str, float] = {}
    with deployment("workspace"):
        mi.register_connection(decl)
        t0 = time.monotonic()
        plan = mi.propose_plan(decl["id"])
        timings["propose_plan (spawn + tools/list)"] = round(time.monotonic() - t0, 3)
        assert plan.profiles == ["walnut-files-blob"] and plan.status == "approved", plan
        t0 = time.monotonic()
        report = mi.run_ingest(decl["id"], store=GraphStore(str(tmp_path / "g.db")),
                               harness=_harness(names))
        timings["run_ingest total"] = round(time.monotonic() - t0, 3)
    assert report.status == "ok", (report.message, report.verify)
    assert report.verify["ok"], report.verify["problems"]
    run_dir = Path(report.run_dir)

    def rel(uri: str) -> str:
        return Path(uri).resolve().relative_to(FILES.resolve()).as_posix()

    # ---- every screen decision, and the split derived from the expectations
    gov = {rel(r["uri"]): r for r in read_jsonl(run_dir / "governance.jsonl")}
    assert set(gov) == set(expected)
    for r, spec in expected.items():
        assert gov[r]["decision"] == spec["decision"], (r, gov[r].get("rationale"))
        assert set(spec.get("redacted") or []) <= {x["rule"] for x in gov[r]["redactions"]}, r
    want = collections.Counter(s["decision"] for s in expected.values())
    assert collections.Counter(g["decision"] for g in gov.values()) == want
    assert report.counts["governance"] == dict(want)

    # ---- nothing failed, nothing silently missing
    assert read_jsonl(run_dir / "failed.jsonl") == []
    assert read_jsonl(run_dir / "extract_skipped.jsonl") == []
    captured = read_jsonl(run_dir / "captured.jsonl")
    by_rel = {rel(r["uri"]): r for r in captured}
    assert set(by_rel) == on_disk

    # ---- '#' folder, duplicate names, original bytes
    assert [r for r in by_rel if "#" in r], "the '#' folder item was not captured"
    briefs = [r for r in captured if r["name"] == "brief.md"]
    assert len(briefs) == 2 and len({b["item_id"] for b in briefs}) == 2
    assert len({b["uri"] for b in briefs}) == 2
    for r, rec in by_rel.items():
        assert rec["sha256"] == hashlib.sha256((FILES / r).read_bytes()).hexdigest(), r

    # ---- PDF / DOCX text through docling
    extracted = {rel(r["uri"]): r for r in read_jsonl(run_dir / "extracted.jsonl")}
    binaries = [r for r in on_disk if r.endswith((".pdf", ".docx"))]
    assert binaries
    for r in binaries:
        assert extracted[r]["extractor"].startswith("docling"), (r, extracted[r])
        text = capture.get_text(by_rel[r]["sha256"]) or ""
        assert len(text.strip()) > 100, r

    # ---- pointers resolve against the capture store, recomputed here
    ptrs = read_jsonl(run_dir / "pointers.jsonl")
    labelled_by_ext = collections.Counter()
    ext_of_sha = {rec["sha256"]: Path(r).suffix for r, rec in by_rel.items()}
    for p in ptrs:
        if p["kind"] != "doc_span":            # an anchor's listing cell (R-022 #5), not a span
            continue
        assert hashlib.sha256(capture.get_bytes(p["sha"])).hexdigest() == p["sha"]
        text = capture.get_text(p["sha"])
        assert 0 <= p["start"] <= p["end"] <= len(text)
        if p.get("label"):
            assert p["label"] in text[p["start"]:p["end"]], p
            labelled_by_ext[ext_of_sha.get(p["sha"])] += 1
    assert report.counts["pointers"] == report.counts["pointers_resolved"] == len(ptrs) > 0
    assert labelled_by_ext[".pdf"] > 0 and labelled_by_ext[".docx"] > 0, labelled_by_ext

    # ---- report.json reconciles with the corpus
    rep = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    c = rep["counts"]
    assert c["inventory"] == c["captured"] == c["extracted"] == len(on_disk)
    assert c["excluded"] == c["blocked"] == c["failed"] == 0
    assert c["built"] == want["allow"]

    stages = json.loads((run_dir / "stages.json").read_text(encoding="utf-8"))
    print("\nS1 stages.json:", json.dumps({k: v["seconds"] for k, v in stages.items()}))
    print("S1 wall:", json.dumps(timings))
    print("S1 counts:", json.dumps({k: c[k] for k in ("inventory", "captured", "extracted",
                                                       "governance", "built", "entities",
                                                       "pointers", "pointers_resolved")}))
