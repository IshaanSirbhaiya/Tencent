"""Phase B end-to-end proof — assertions over a COMPLETED e2e run directory.

These do not drive the run (that needs live models, Docker and minutes of wall clock); they read
what `scripts/e2e/*` produced and assert the Phase B bar (ROADMAP_KNOWLEDGE_PLATFORM §4). Skipped
unless `WALNUT_E2E_DIR` points at a run directory, so normal test runs never touch it:

    set WALNUT_E2E_DIR=var/e2e-20261001T0356
    python -m pytest tests/e2e -m integration -q

Expectations are DERIVED (fixtures/workspace_demo/expected_outcomes.yaml, the graph itself), never
written down here (CLAUDE.md rule 6). Findings: wiki/40-reviews/E2E-2026-10-01.md (run 1) and
wiki/40-reviews/E2E-2026-10-01-run2.md (run 2: + delegation D, materialise M, adaptation A,
security S). The run-2 tests read the artefacts `scripts/e2e/run2_phase*.py` saved under
<run>/artefacts; a run-2 artefact that is absent skips its test (a run-1 dir has none).

**Either estate** (2026-10-02): ``WALNUT_E2E_ESTATE=finance`` asserts over a finance run directory
(``var/finance``), ``workspace`` (the default) over the runs 1-4 directories. Tests that read the
workspace corpus's own expected outcomes skip on another estate.

**Completeness** (``test_c_*``, after the finance dry run): four live runs proved that every value
the graph holds re-verifies, and none asked whether the graph holds what the source has. The
``test_c_*`` assertions compare the source's own counts (rows, declared FKs) with the graph's, with
thresholds derived from the estate — ``scripts/e2e/completeness.py``, and
``wiki/40-reviews/LESSON-2026-10-02-completeness.md`` for why.
"""
from __future__ import annotations

import collections
import glob
import json
import os
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
RUN = os.environ.get("WALNUT_E2E_DIR")
if not RUN:
    pytest.skip("WALNUT_E2E_DIR not set: no completed e2e run to assert over", allow_module_level=True)
E = (ROOT / RUN).resolve() if not Path(RUN).is_absolute() else Path(RUN)
sys.path.insert(0, str(ROOT / "scripts" / "e2e"))
from e2e_env import ESTATES, estate_name, graph_db  # noqa: E402

ESTATE = estate_name()
GRAPH = graph_db(E, ESTATE)
if not GRAPH.is_file():
    pytest.skip(f"{E} holds no {ESTATE} e2e graph ({GRAPH.name})", allow_module_level=True)
os.environ["WALNUT_E2E_DIR"] = str(E)
workspace_only = pytest.mark.skipif(
    ESTATE != "workspace", reason="reads the workspace corpus's own expected outcomes / sources")


def _jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] \
        if p.is_file() else []


def _first_full_files_run() -> Path:
    runs = sorted((E / "ingest" / "larkspur-files").glob("*/governance.jsonl"))
    full = [r.parent for r in runs if len(_jsonl(r)) >= 15]
    assert full, "no files run screened the whole corpus"
    return full[0]


@workspace_only
def test_screen_decisions_match_expected_outcomes():
    expected = yaml.safe_load((ROOT / "fixtures/workspace_demo/expected_outcomes.yaml")
                              .read_text(encoding="utf-8"))["files"]
    gov = _jsonl(_first_full_files_run() / "governance.jsonl")
    from e2e_env import corpus_dir
    corpus = corpus_dir(E)
    got = {Path(r["uri"]).resolve().relative_to(corpus.resolve()).as_posix(): r for r in gov}
    assert set(expected) <= set(got)
    wrong = {k: (got[k]["decision"], v["decision"]) for k, v in expected.items()
             if got[k]["decision"] != v["decision"]}
    assert not wrong, wrong
    want = collections.Counter(v["decision"] for v in expected.values())
    assert collections.Counter(got[k]["decision"] for k in expected) == want


@workspace_only
def test_blocked_and_review_items_never_built():
    run = _first_full_files_run()
    decisions = {r["item_id"]: r["decision"] for r in _jsonl(run / "governance.jsonl")}
    built = {r["item_id"] for r in _jsonl(run / "build.jsonl")}
    assert built, "vacuous: nothing built"
    assert not [i for i in built if decisions.get(i) != "allow"]


def test_every_pointer_reverifies():
    import pointer_audit
    out = pointer_audit.main("pytest")
    assert out["total_pointers"] > 0
    assert out["counts"].get("verified", 0) == out["total_pointers"], out["counts"]


def test_graph_has_no_dangling_edges_and_spans_both_sources():
    import graph_audit
    out = graph_audit.audit(GRAPH)
    assert out["nodes"] > 0
    assert out["dangling_count"] == 0, out["dangling_edges"][:5]
    assert out["components_spanning_both_sources"] >= 1
    assert out["cross_source_entities_own_pointers"]


@workspace_only
def test_briefing_cites_two_sources():
    recs = [json.loads(Path(p).read_text(encoding="utf-8"))
            for p in glob.glob(str(E / "runs" / "agent" / "account_briefing" / "*.json"))]
    if not recs and (E / "estate.txt").is_file():
        pytest.skip("run 5 has no briefing scenario")
    done = [r for r in recs if r.get("status") == "completed"]
    assert done, "no completed briefing run"
    sources = {c.get("source_id") for c in (done[-1]["answer"].get("citations") or [])}
    assert len(sources) >= 2, sources


def test_every_model_step_names_its_model():
    recs = [json.loads(Path(p).read_text(encoding="utf-8"))
            for p in glob.glob(str(E / "runs" / "agent" / "*" / "*.json"))]
    steps = [s for r in recs if r.get("status") == "completed" for s in r.get("steps") or []]
    assert steps, "vacuous: no steps"
    assert all((s.get("produced_by") or {}).get("model") for s in steps)


# ============================================================== completeness (finance dry run)
_COMPLETENESS: dict = {}


def _completeness() -> dict:
    """The completeness audit of this run, computed once (read-only over graph, capture, ingest)."""
    if not _COMPLETENESS:
        import completeness

        answers = next((d for d in (E / "dryrun" / "http", E / "artefacts" / "http")
                        if d.is_dir()), None)
        _COMPLETENESS.update(completeness.audit(
            GRAPH, E / "ingest", E / "capture", answers_dir=answers,
            bound_packs=ESTATES[ESTATE]["packs"].split(","), packs_dir=ROOT / "packs"))
    return _COMPLETENESS


def test_c_every_source_row_reaches_the_graph():
    """Per captured table: the source's own row count == rows captured == rows the graph cites.
    A short capture is a row cap; a short graph is a build that dropped rows."""
    out = _completeness()
    assert out["tables"], "vacuous: no captured table in this run"
    check = out["checks"]["rows_complete"]
    assert check["passed"], check


def test_c_every_declared_foreign_key_becomes_an_edge():
    out = _completeness()
    assert out["foreign_keys"] or any(t["association"] for t in out["tables"]),         "vacuous: the run declares no foreign key"
    assert sum(f["edges_expected"] for f in out["foreign_keys"]) > 0
    check = out["checks"]["declared_fks_built"]
    assert check["passed"], check


def test_c_isolated_nodes_are_no_more_than_the_declared_keys_predict():
    """The threshold is DERIVED: a table-row node is predicted isolated only when it neither
    references nor is referenced by a captured row through a declared key."""
    out = _completeness()
    iso = out["isolation"]
    assert iso["table_nodes"] > 0, "vacuous: no table-row nodes"
    assert iso["isolated"] <= iso["predicted_isolated"], iso


def test_c_every_foreign_key_value_resolves_to_a_captured_row():
    check = _completeness()["checks"]["references_resolved"]
    assert check["passed"], check


def test_c_values_shared_across_sources_are_linked():
    """A column whose values are another source's keys (by the data, not by name) must be an edge
    — or the platform must have proposed one. Today there is no proposal path, so: an edge."""
    check = _completeness()["checks"]["cross_source_links"]
    assert check["passed"], check


def test_c_no_answer_uses_another_packs_subject_vocabulary():
    out = _completeness()
    if "foreign_vocabulary" not in out:
        pytest.skip("this run captured no HTTP responses to scan")
    assert out["foreign_vocabulary"]["terms"], "vacuous: no unbound pack declares a subject"
    assert out["checks"]["no_foreign_vocabulary"]["passed"], out["foreign_vocabulary"]["hits"][:5]


# ===================================================================================== run 2 (E2E2)
def _art(name: str):
    p = E / "artefacts" / name
    if not p.is_file():
        pytest.skip(f"{name}: not produced by this run")
    return json.loads(p.read_text(encoding="utf-8"))


def _principals() -> dict:
    """{'primary': principal_id, 'second': principal_id} from the deployment's token directory."""
    toks = json.loads((E / "auth" / "tokens.json").read_text(encoding="utf-8"))
    directory = json.loads((E / "auth" / "api_tokens.json").read_text(encoding="utf-8"))
    return {role: directory[t] for role, t in toks.items()}


# --- S. security ---------------------------------------------------------------------------------
def test_s_every_disclosing_route_refuses_without_a_token():
    sec = _art("p3_sec.json")
    assert sec["no_token"], "vacuous: no route probed"
    assert {k: v for k, v in sec["no_token"].items() if v != 401} == {}
    assert sec["bad_token"] == 401


def test_s_literal_secret_is_refused_and_never_echoed():
    rows = [r for r in (_art("p3_sec.json").get("literal_secret"),
                        _art("p3c_sec2.json").get("literal_secret_url")) if r]
    assert rows
    assert all(r["status"] >= 400 and not r["echoed"] for r in rows)
    # at least one refusal must be the secret scan itself, not an earlier gate (DNS, allow-list)
    assert any("literal secret" in json.dumps(r["body"]) for r in rows), rows


def test_s_a_typed_name_never_decides_an_approval():
    who = _principals()
    rej = _art("p3c_sec2.json").get("reject_second_typed_by")
    if not rej:
        pytest.skip("no proposal was left to reject")
    assert rej["status"] == 200 and rej["decided_by"] == who["second"]
    assert "refused" in _art("p3_sec.json")["adapter_principal_of_name"]


# --- D. delegation ---------------------------------------------------------------------------------
def test_d_ungranted_principal_is_refused():
    grants = json.loads((E / "auth" / "federated_grants.json").read_text(encoding="utf-8"))
    second = _principals()["second"]
    assert second not in grants
    sec = _art("p3_fed.json")["second"]
    # run 2 saved (status, body); run 3 saves {status, ms, body}
    st, body = (sec["status"], sec["body"]) if isinstance(sec, dict) else sec
    assert st == 403 and body["refusal"]["code"] == "FEDERATED_NOT_GRANTED"


def test_d_every_number_expands_to_exactly_its_input_cells():
    import fed_check
    _art("p3d_fed.json")          # run 2's single-source table; skips when the run made none
    out = fed_check.main(E / "artefacts" / "p3d_fed.json", "pytest")
    s = out["summary"]
    assert s["numeric_cells"] > 0, out["problems"]
    assert s["exactly_inputs"] == s["value_matches_independent"] == s["all_leaves_reread"] \
        == s["numeric_cells"], s


def test_d_cross_source_answer_matches_an_independent_computation():
    """Customers mentioned in meeting notes = canonical ids of the organisations that documents
    under meetings/ mention (graph); their open invoices (live Postgres) must be the table's
    groups."""
    import sqlite3
    import fed_check
    fed = _art("p3c_fed2.json")["open_by_customer"]["body"]
    c = sqlite3.connect(str(E / "graph" / "workspace.db"))
    mentioned = {r[0] for r in c.execute(
        "select d.canonical_id from edges e join nodes s on s.id=e.src_id join nodes d on d.id=e.dst_id "
        "where e.kind='mentions' and d.kind='organisation' and d.canonical_id is not null and "
        "e.system_to is null and s.system_to is null and d.system_to is null and "
        "(s.label like '%kickoff%' or s.label like '%steering%' or s.id like '%meetings/%')")}
    customers = {r[0] for r in fed_check.psql("select customer_id from public.customers")}
    want = mentioned & customers
    open_rows = fed_check.psql("select customer_id from public.invoices where status='open'")
    want_groups = {r[0] for r in open_rows if r[0] in want}
    assert want_groups, "vacuous: the estate has no open invoice for a meeting-note customer"
    got = {r["cells"]["customer_id"]["value"] for r in (fed.get("table") or {}).get("rows") or []}
    assert got == want_groups, (got, want_groups, fed.get("answer_markdown", "")[:400])


def test_d_delegation_tree_is_shown_to_the_asker_only():
    runs = _art("p3d_activity_runs.json")
    fed = {k: v for k, v in runs.items() if k.startswith("fed-")}
    assert fed
    for k, v in fed.items():
        assert v["status"] == (200 if k.endswith("/primary") else 404), (k, v)
        if k.endswith("/primary"):
            assert "delegation_tree" in v["keys"]


def test_d_a_workflow_agent_can_be_compiled_to_delegate():
    comp = _art("p2_result.json")["delegate_compile"]
    spec = (comp.get("final") or {}).get("spec_yaml") or ""
    assert comp.get("kind") == "agent" and "delegate.ask:" in spec, \
        f"compiled as {comp.get('kind')}: {(comp.get('final') or {}).get('understood', '')[:200]}"


# --- M. materialise ---------------------------------------------------------------------------------
def _mat_jobs():
    m = _art("p3c_mat2.json")
    return {k: v for k, v in m.items() if isinstance(v, dict) and "job" in v}


def test_m_every_built_dataset_has_receipt_pointer_check_leakage_check_and_isolation():
    built = {k: v for k, v in _mat_jobs().items() if v["job"].get("status") == "succeeded"}
    assert built, "vacuous: no dataset was built"
    for k, v in built.items():
        rc = v["receipt"]
        assert rc["plan_verification"]["matches"], k
        pc = v["job"]["result"]["pointer_check"]
        assert pc["grounded_cells"] > 0 and pc["all_resolved"] and pc["all_match"], (k, pc)
        assert "missingness_leak_check" in rc["receipt"], k
        iso = v["isolation"]
        assert iso["job"] == iso["receipt"] == 404 and iso["jobs_list"] == 0
        assert iso["download"] and set(iso["download"]) == {404}


def test_m_chunk_cells_reverify_against_the_passage_store():
    import sqlite3
    built = [v for v in _mat_jobs().values() if v["job"].get("status") == "succeeded"
             and v["job"]["result"].get("shape") == "chunk_corpus"]
    if not built:
        pytest.skip("no chunk_corpus built")
    checked = 0
    stores = [sqlite3.connect(str(p)) for p in (E / "passages").glob("*.sqlite")]
    for v in built:
        ph = v["job"]["result"]["plan_hash"]
        data = next((E / "materialise_downloads" / ph).glob("mp_*.jsonl"))
        for line in data.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            # a dataset row is a window [char_start, char_end) over the passage `document_id`;
            # the passage itself is a span of the captured document text (doc sha256)
            hits = [st.execute("select c.content, c.char_start, c.char_end, d.sha256 from chunks c "
                               "join documents d on d.doc_id=c.doc_id where c.chunk_id=?",
                               (row["document_id"],)).fetchone() for st in stores]
            hit = next((h for h in hits if h), None)
            assert hit, row["chunk_id"]
            content, cs, ce, sha = hit
            assert content[row["char_start"]:row["char_end"]] == row["text"], row["chunk_id"]
            cap = [E / "capture" / sha[:2] / f"{sha}{suf}" for suf in (".txt", ".redacted.txt")]
            texts = [p.read_text(encoding="utf-8")[cs:ce] for p in cap if p.is_file()]
            assert content in texts, (row["chunk_id"], "passage is not its capture's span")
            checked += 1
    assert checked > 0


def test_m_a_tabular_dataset_can_be_built_from_english():
    tab = [v for v in _mat_jobs().values()
           if (v["job"].get("request") or "").lower().startswith("one row per")]
    assert tab, "vacuous: no tabular request"
    assert any(v["job"].get("status") == "succeeded" for v in tab), \
        [(v["job"].get("status"), (v["job"].get("refusal") or {}).get("code")) for v in tab]


# --- A. adaptation ------------------------------------------------------------------------------------
def test_a_additive_drift_is_applied_as_a_delegated_approval():
    chain = _art("p3_chain.json")
    activated_by = _art("p2_result.json")["adapter"]["save"]["saved_by"]
    deleg = [a for a in chain["mapping_approvals"] if a.get("principal_type") == "DelegatedApproval"]
    assert deleg, "no delegated approval was recorded"
    for a in deleg:
        assert a["authenticated_by"].startswith("delegated:")
        assert a["delegated_by"] == a["principal_id"] == activated_by
        assert a["scope"] == "additive_within_declared_scope"


def test_a_breaking_and_out_of_scope_drift_is_held_for_a_person():
    chain = _art("p3_chain.json")
    held: dict = {}
    for step_ in ("b_sync", "c_sync", "d_sync2"):
        for c in chain[step_]["connections"]:
            ad = c.get("adaptation") or {}
            always = set((ad.get("policy") or {}).get("always_ask") or [])
            for kind in ad.get("drift_kinds") or []:
                held.setdefault(kind, []).append((bool(ad.get("pending")), kind in always))
    assert held, "vacuous: no drift detected"
    assert all(p for xs in held.values() for p, ask in xs if ask), held
    assert "new_file_type" in held and all(p for p, _ in held["new_file_type"]), held
    assert chain["csv_built"] is False


def test_a_event_agent_receives_the_event_it_was_fired_for():
    runs = _art("p3_chain.json")["a_event_runs"]
    assert runs, "the event agent never ran"
    ev = runs[0]["trigger"].get("event") or {}
    assert ev.get("type") == "item_added" and ev.get("uri")
    assert Path(ev["uri"].replace("\\", "/")).stem in runs[0]["answer"]


# --- 5 / 6. golden set and write loop ----------------------------------------------------------------
def test_golden_values_cite_their_rows_and_abstentions_report_their_outcome():
    rows = _art("s5_golden_larkspur_qa.json")
    gold = [json.loads(x) for x in (ROOT / "packs/workspace/evals/agent_contract.jsonl")
            .read_text(encoding="utf-8").splitlines() if x.strip() and not x.startswith("#")]
    assert {g["id"] for g in gold} == {r["id"] for r in rows}
    bad = {}
    for r in rows:
        if r.get("expect") == "abstain":
            if not (r.get("outcome") or {}).get("pass"):
                bad[r["id"]] = (r.get("outcome") or {}).get("checks")
        elif not (r.get("value_ok") and r.get("cite_references_row")):
            bad[r["id"]] = "value/cite"
        if r["citation_failures"]:
            bad[r["id"] + ":cites"] = r["citation_failures"]
    assert not bad, bad


def test_6b_an_approved_write_executes():
    # run 3: phase 3's harness approved a draft, so the CRM write was approved on a second firing
    ov = _art("p3d_crm.json") if (E / "artefacts" / "p3d_crm.json").is_file() else _art("p3_overdue.json")
    if "approve_crm" in ov:
        ov = {**ov, "approve_first": ov["approve_crm"]}
    assert ov["pending"], "the scheduled run proposed nothing"
    assert ov.get("approve_first", {}).get("status") == 200, ov.get("approve_first")


# ===================================================================================== run 3 (E2E3)
# A run-3 directory carries random_requests.json; its artefacts are written by
# scripts/e2e/run2_phase1.py + run3_phase{2,3,3c,3d}.py, run3_withhold.py, run3_typechange.py and
# fed_check3.py. Findings: wiki/40-reviews/E2E-2026-10-01-run3.md (P-01 ...).
def _run3():
    # run 5 also writes random_requests.json, in its own per-estate shape (estate.txt marks it)
    if not (E / "random_requests.json").is_file() or (E / "estate.txt").is_file():
        pytest.skip("not a run-3/4 directory")


def _psql(sql: str) -> list:
    import fed_check
    return fed_check.psql(sql)


def test_r3_activation_mints_a_sealed_delegation_for_the_signed_in_principal():
    _run3()
    p2 = _art("p2_result.json")
    seals = [r for r in p2.get("delegation_log") or [] if r.get("event") == "delegation_sealed"]
    assert seals, "no sealed delegation record"
    assert seals[-1]["principal_id"] == p2["adapter"]["save"]["saved_by"]


def test_r3_passages_are_back_filled_for_items_built_before_they_were_enabled():
    _run3()
    idx = [json.loads(p.read_text(encoding="utf-8"))
           for p in (E / "ingest" / "larkspur-files").glob("*/index.json")]
    bf = [i["backfill"] for i in idx if i.get("enabled") and (i.get("backfill") or {}).get("candidates")]
    assert bf, "vacuous: no back-fill candidates"
    assert all(b["indexed"] == b["candidates"] for b in bf), bf


def test_r3_an_approved_crm_write_lands_and_the_next_sync_reingests_it():
    _run3()
    crm = _art("p3d_crm.json")
    assert crm["approve_crm"]["status"] == 200, crm["approve_crm"]
    run_id = crm["approve_crm"]["body"]["run_id"]
    new = [r for r in crm["db_rows"].splitlines() if run_id in r]
    assert len(new) == 1, crm["db_rows"]
    assert crm["approve_crm_again"]["status"] == 409
    ended = crm["approve_crm"]["body"]["record"]["ended"][:19]
    ev = [e for e in crm["loop"]["events_follow_up"] if e["type"] == "item_changed" and e["ts"] >= ended]
    assert ev, "the next sync did not re-ingest follow_up_tasks"
    assert new[0].split("|")[0] not in crm["loop"]["rows_after"].split(), "scratch row not removed"


def test_r3_an_approved_draft_is_written():
    _run3()
    crm, ov = _art("p3d_crm.json"), _art("p3_overdue.json")
    tried = [x for x in (crm.get("approve_draft"), ov.get("approve_first")) if x]
    assert tried, "vacuous: no draft approval attempted"
    assert any(x["status"] == 200 for x in tried) and (crm["drafts_on_disk"] or ov["drafts"]), \
        [(x["status"], str(x["body"])[:160]) for x in tried]


def test_r3_cross_source_answer_equals_the_independent_computation():
    _run3()
    import fed_check3
    out = fed_check3.main(E / "artefacts" / "p3_fed.json", "primary.r1", "pytest")
    assert out["rows_returned"] > 0 and out["expected"], "vacuous"
    assert out["every_returned_value_correct"] and out["all_leaves_reread"] and out["values_match_leaves"]
    if not out["complete"]:      # an incomplete answer must say so
        assert out["coverage_status"] != "complete"
    assert out["complete"], {"missing": out["missing_groups"], "coverage": out["coverage_status"]}


def test_r3_brain_routes_the_cross_source_question_to_delegation():
    _run3()
    x = _art("p3_brain.json")["query"]["x-4"]
    assert x["routed_to"] == "federated" and (x["table"] or {}).get("rows")


def test_r3_brain_count_equals_live_postgres():
    _run3()
    q = _art("p3_brain.json")["query"]["ac-008"]
    live = _psql("select count(*) from public.invoices i join public.customers c using (customer_id) "
                 "where c.name ilike 'Pellucid Freightways%' and i.status = 'open'")[0][0]
    rows = (q.get("table") or {}).get("rows") or []
    got = [str(c["value"]) for r in rows for k, c in r["cells"].items() if k.startswith("count")]
    assert got, q.get("answer", "")[:300]
    assert got == [live], (got, live)


def test_r3_a_delegating_agent_answers_through_its_adapter_agent():
    _run3()
    d = _art("p3_deleg_agent.json")
    assert d["steps"] and all(s["tool"].startswith("delegate.ask:") for s in d["steps"])
    assert (d.get("outcome") or {}).get("coverage_status") == "complete", d["answer"][:400]


def test_r3_a_labelled_tabular_dataset_is_built_from_english():
    _run3()
    m = _art("p3d_mat2.json")
    tab = {k: v for k, v in m.items() if isinstance(v, dict) and "job" in v
           and "row per" in v["request"].lower()}
    assert tab, "vacuous"
    assert any(v["job"]["status"] == "succeeded" for v in tab.values()), \
        {k: (v["job"].get("refusal") or {}).get("code") for k, v in tab.items()}


def test_r3_every_sidecar_pointer_rereads_to_its_cell_and_downloads_are_isolated():
    _run3()
    built = {k: v for k, v in _art("p3d_mat2.json").items() if isinstance(v, dict)
             and (v.get("job") or {}).get("status") == "succeeded"}
    assert built, "vacuous: nothing built"
    checked = [v["sidecar_check_v2"] for v in built.values() if v.get("sidecar_check_v2")]
    assert checked, "no sidecar re-read with the run-3 checker"
    for sc in checked:
        assert sc["grounded"] > 0 and sc["reread_ok"] == sc["grounded"], sc
    for v in built.values():
        iso = v["isolation"]
        assert iso["job"] == iso["receipt"] == 404 and set(iso["download"]) == {404}


def test_r3_a_withheld_dataset_refuses_its_data_but_serves_its_receipt():
    _run3()
    w = _art("p3d_withhold.json")
    assert w["cli"]["rc"] == 0 and set(w["before"].values()) == {200}
    data = {n: r for n, r in w["after"].items() if n.endswith(".jsonl")}
    assert data and all(r["status"] == 409 and r["code"] == "DATASET_WITHHELD" for r in data.values())
    assert w["after"]["receipt.json"]["status"] == w["after"]["plan.yaml"]["status"] == 200


def test_r3_adaptation_seal_holds_and_decisions():
    _run3()
    chain, p2 = _art("p3_chain.json"), _art("p2_result.json")
    seal = [r for r in p2["delegation_log"] if r.get("event") == "delegation_sealed"][-1]["hash"]
    deleg = [a for a in chain["mapping_approvals"] if a.get("principal_type") == "DelegatedApproval"]
    assert deleg and all((a.get("delegation_seal") or {}).get("hash") == seal for a in deleg)
    for d in chain["decisions"].values():
        if "reject" in d:
            assert d["reject"][0] == 200 and d["approve_after_reject"][0] == 409
    tc = _art("p3d_typechange.json")
    assert tc["pending"] and all("type_changed" in (a.get("kinds") or []) for a in tc["pending"])
    assert {x[0] for x in tc["second_principal_approve"]} == {403}
    assert all(x["status"] == 200 for x in tc["approve"])


def test_r3_activity_is_owner_only_with_identical_404s_and_operators_see_all():
    _run3()
    a = _art("p3c_act.json")
    assert a["list_primary"]["n"] > 0
    assert a["list_second"]["n"] == 0
    assert set(a["list_operator"]["ids"]) >= set(a["list_primary"]["ids"])
    sec = {k: v for k, v in a["runs"].items() if k.endswith("/second")}
    assert sec and {v["status"] for v in sec.values()} == {404}
    assert len({v["body_hash"] for v in sec.values()}) == 1


def test_r3_the_delegation_tree_shows_assumptions_and_questions():
    _run3()
    _art("p3c_act.json")
    trees = []
    for f in (E / "artefacts" / "http").glob("*_p3c_act_run_primary.json"):
        t = (json.loads(f.read_text(encoding="utf-8")).get("response") or {}).get("delegation_tree")
        if t:
            trees.append(t)
    assert trees, "vacuous"
    assert all(t["plan"].get("assumptions") is not None for t in trees), \
        trees[0]["plan"].get("assumptions_note")


def test_r3_security_spot_checks():
    _run3()
    s = _art("p3_sec.json")
    assert s["no_token"] and set(s["no_token"].values()) == {401} and s["bad_token"] == 401
    assert s["approval_plain_string_http"]["status"] == 422
    assert all(v.startswith("refused") for v in s["approval_plain_string_package"].values())
    assert s["literal_secret"]["status"] >= 400 and not s["literal_secret"]["echoed"]
    assert "literal secret" in json.dumps(s["literal_secret"]["body"])
    assert s["ssrf"] and {v["code"] for v in s["ssrf"].values()} == {"SOURCE_DESTINATION_NOT_ALLOWED"}
    assert s["command_source"]["code"] == "COMMAND_SOURCES_DISABLED" and s["import_path"] == 403


def test_r3_random_requests_are_answered_asked_or_refused_honestly():
    _run3()
    asked = json.loads((E / "random_requests.json").read_text(encoding="utf-8"))
    verdicts = json.loads((E / "random_verdicts.json").read_text(encoding="utf-8"))
    ids = {k for grp in ("questions", "datasets", "agents") for k in asked[grp]}
    assert ids == set(verdicts) - {"graded_by"}, "every request must be graded"
    assert not {k: v["why"] for k, v in verdicts.items() if k in ids and v["verdict"] != "PASS"}


# ===================================================================================== run 4 (E2E4)
# A run-4 directory carries artefacts/p4_mat_core.json (scripts/e2e/run4_mat.py), p4_p09.json and
# p4_retest.json. Findings: wiki/40-reviews/E2E-2026-10-01-run4.md (Q-01 ...). Every expectation
# below is derived from the run's own plan, the live DB or the graph, never written down.
def _run4():
    if not (E / "artefacts" / "p4_mat_core.json").is_file():
        pytest.skip("not a run-4 directory")


def test_r4_follow_up_tasks_never_takes_another_tables_key_as_its_id():
    _run4()
    m = yaml.safe_load((E / "sources" / "larkspur_db.mapping.yaml").read_text(encoding="utf-8"))
    tables = m.get("tables") or m
    keys = {t: (v.get("node") or {}).get("canonical_id") for t, v in tables.items() if isinstance(v, dict)}
    assert "public.follow_up_tasks" in keys, "vacuous: the mapper never saw follow_up_tasks"
    other = {k for t, k in keys.items() if k and t != "public.follow_up_tasks"}
    assert other, "vacuous: no other table has a key"
    assert keys["public.follow_up_tasks"] not in other


def test_r4_materialise_questions_are_answered_back_and_the_job_then_runs():
    _run4()
    core = _art("p4_mat_core.json")
    asked = {k: v for k, v in core.items() if isinstance(v, dict) and v.get("answers")}
    assert asked, "vacuous: the planner asked nothing"
    for k, v in asked.items():
        assert v["final"]["job"]["status"] == "succeeded", (k, v["final"]["job"].get("refusal"))
        rc = v["final"]["receipt"]["platform"]["answers"]
        assert rc == v["answers"], k      # the answers are recorded in the receipt


def test_r4_every_sidecar_pointer_rereads_and_downloads_are_isolated():
    _run4()
    built = {k: v["final"] for k, v in _art("p4_mat_core.json").items()
             if isinstance(v, dict) and (v.get("final") or {}).get("job", {}).get("status") == "succeeded"}
    assert built, "vacuous"
    for k, f in built.items():
        sc = f["sidecar_check"]
        assert sc["grounded"] > 0 and sc["reread_ok"] == sc["grounded"], (k, sc["failures"][:3])
        iso = f["isolation"]
        assert iso["job"] == iso["receipt"] == 404 and set(iso["download"]) == {404}, k


def test_r4_withheld_dataset_refuses_data_and_serves_receipt():
    _run4()
    w = _art("p4_mat_core.json")["withhold"]
    assert w["cli"]["rc"] == 0 and set(w["before"].values()) == {200}
    data = {n: r for n, r in w["after"].items() if n.endswith(".jsonl")}
    assert data and all(r["status"] == 409 and r["code"] == "DATASET_WITHHELD" for r in data.values())
    assert w["after"]["receipt.json"]["status"] == w["after"]["plan.yaml"]["status"] == 200


def test_r4_labelled_rows_are_mature_and_their_features_are_present():
    """Q-07. A row's label instant is its decision time + the label window + the maturity lag (read
    from the plan); a label whose instant is after build_time cannot be known yet. A feature column
    the graph holds for the cohort must not be wholly absent."""
    _run4()
    import datetime as dt
    import re
    f = _art("p4_mat_core.json")["labelled"]["final"]
    ph = f["job"]["result"]["plan_hash"]
    plan = yaml.safe_load((E / "materialise_downloads" / ph / "plan.yaml").read_text(encoding="utf-8"))
    t = plan["temporal"]

    def days(iso):
        return int(re.fullmatch(r"P(\d+)D", iso).group(1))
    horizon = days(t["label_window"]["within"]) + days(t["label_maturity_lag"])
    build = dt.datetime.fromisoformat(t["build_time"].replace("Z", "+00:00")).date()
    dcol = next(c["path"] for c in plan["shape"]["columns"] if c["source"]["relation"] == "decision_time")
    rows = [json.loads(x) for x in next((E / "materialise_downloads" / ph).glob("mp_*[0-9a-f].jsonl"))
            .read_text(encoding="utf-8").splitlines() if x.strip()]
    assert rows, "vacuous: no rows"
    immature = [r for r in rows if dt.date.fromisoformat(r[dcol]) + dt.timedelta(days=horizon) > build]
    absent = f["receipt"]["platform"]["unresolved"]["columns_fully_absent"]
    assert not immature, f"{len(immature)} of {len(rows)} rows carry a label whose instant is after build_time"
    assert not absent, absent


def test_r4_human_notified_drafts_are_written_with_live_values():
    _run4()
    from e2e_env import followups_dir
    drafts = sorted(followups_dir(E).glob("*.txt"))
    assert drafts, "no draft was written"
    for d in drafts:
        inv = d.stem
        total = _psql(f"select total_gbp::text from public.invoices where invoice_no = '{inv}'")
        assert total, f"{d.name}: names no invoice"
        assert total[0][0] in d.read_text(encoding="utf-8"), d.name


def test_r4_cross_source_groups_match_the_independent_computation():
    _run4()
    import fed_check3
    out = fed_check3.main(E / "artefacts" / "p3_fed.json", "primary.r1", "pytest4")
    assert out["expected"] and out["rows_returned"] > 0, "vacuous"
    assert out["every_returned_value_correct"] and out["all_leaves_reread"] and out["values_match_leaves"]
    assert out["missing_groups"] == [], out["missing_groups"]


def test_r4_federated_coverage_is_complete_when_every_group_is_present():
    """Q-06: the only unlinked mention is an organisation that is not a customer."""
    _run4()
    body = _art("p3_fed.json")["primary"]["r1"]["body"]
    assert body["coverage"]["status"] == "complete", body["coverage"]["reasons"]


def test_r4_brain_count_and_delegating_agent():
    _run4()
    test_r3_brain_count_equals_live_postgres()
    d = _art("p3_deleg_agent.json")
    assert d["steps"] and all(s["tool"].startswith("delegate.ask:") for s in d["steps"])
    assert (d.get("outcome") or {}).get("coverage_status") == "complete"


def test_r4_approvals_inbox_and_run_history_are_owner_or_operator_only():
    _run4()
    p = _art("p4_p09.json")
    assert p["primary"]["n"] > 0, "vacuous: nothing was pending"
    assert p["second"]["n"] == 0
    assert p["operator"]["n"] == p["primary"]["n"]
    assert p["run_detail_second"]["status"] == p["run_detail_second"]["missing_status"] == 404


def test_r4_a_rejected_drift_is_not_rebundled_into_a_later_approval():
    """Q-09: a kind a person rejected must not ride along in a later adaptation's approval."""
    _run4()
    chain, tc = _art("p3_chain.json"), _art("p3d_typechange.json")
    rejected = {k for d in chain["decisions"].values() if "reject" in d for k in d["kinds"]}
    assert rejected, "vacuous: nothing was rejected"
    later = {k for a in tc["pending"] for k in a.get("kinds") or []}
    assert not (rejected & later), sorted(rejected & later)


def test_r4_briefing_uses_only_tools_the_runtime_accepts():
    """Q-02: the compiled tool list must not include a tool the runtime always refuses."""
    _run4()
    b = _art("p3_briefing.json")
    assert b["run_status"] == "completed"
    assert "was refused" not in b["answer"], b["answer"][:400]


def test_r4_random_requests_all_pass():
    _run4()
    test_r3_random_requests_are_answered_asked_or_refused_honestly()


# ===================================================================================== run 5 (E2E5)
# Both estates (WALNUT_E2E_ESTATE=workspace|finance), the FEAT8 features (PLX batch plans, AAX
# adapter setup, ONE one-time tasks, DSD raw designs) and the R-028 consistency pass. The run is
# driven by `scripts/e2e/run5_*.py`; every HTTP exchange is saved under <run>/artefacts/http/ by
# name. Expectations are derived from the run's own review, plan, graph, receipts or live DB.
def _run5():
    if not (E / "estate.txt").is_file():
        pytest.skip("not a run-5 directory")


def _http_opt(name: str):
    hits = sorted((E / "artefacts" / "http").glob(f"[0-9][0-9][0-9][0-9]_{name}.json"))
    return json.loads(hits[-1].read_text(encoding="utf-8")) if hits else None


def _http(name: str):
    """The latest saved exchange whose label is `name` (artefacts/http/NNNN_<name>.json)."""
    rec = _http_opt(name)
    if rec is None:
        pytest.skip(f"{name}: not exercised in this run")
    return rec


def _code(rec: dict):
    return ((rec.get("response") or {}).get("refusal") or {}).get("code")


def test_r5_batch_approval_needs_each_unclassified_tool_ticked():
    """PLX: the review names the tools that need a vouch; a batch item that does not tick them is
    sent to individual review, and the one that does is approved with exactly those tools."""
    _run5()
    review = _http("s1_batch_review")["response"]["pending"]
    vouchable = {r["source_id"]: sorted(v["tool"] for v in r["vouchable"]) for r in review}
    needing = {s: t for s, t in vouchable.items() if t}
    assert needing, "vacuous: no plan needed a vouch"
    probe = {r["source_id"]: r for r in _http("s1_batch_probe")["response"]["results"]}
    unticked = [s for s in needing if s in probe]
    assert unticked, "vacuous: no unticked plan was probed"
    assert all(probe[s]["outcome"] == "needs_individual_review" for s in unticked), probe
    approved = {r["source_id"]: r for r in _http("s1_batch_approve")["response"]["results"]}
    assert approved, "vacuous"
    for s, r in approved.items():
        assert r["outcome"] == "approved", r
        assert sorted(r["approval"]["vouched_read_tools"]) == needing.get(s, [])
        assert r["approval"]["dev_only"] is False and r["approval"]["authenticated_by"]


def test_r5_a_single_approval_is_bound_to_the_reviewed_digest():
    _run5()
    assert _http("s1_approve_no_digest")["status"] == 422
    stale = _http("s1_approve_wrong_digest")
    assert stale["status"] == 409 and _code(stale) == "DECISION_STALE"
    ok = _http("s1_approve_individual")
    assert ok["status"] == 200 and ok["response"]["plan"]["status"] == "approved"


def test_r5_a_write_tool_is_never_vouchable():
    _run5()
    b = [r for r in _http("s1_batch_probe")["response"]["results"]
         if "not an unclassified tool" in (r.get("reason") or "")]
    assert b and all(r["outcome"] == "needs_individual_review" for r in b), "vacuous or approved"
    assert _http("s11_vouch_write_individual")["status"] == 409


def test_r5_the_runs_own_completeness_block_agrees_with_the_graph():
    """TAB6's per-run `counts.completeness`: every built table's source rows became nodes and every
    expected FK edge was built, on the LATEST run that built each table."""
    _run5()
    reports = {}
    for src in (E / "ingest").iterdir():
        for run in sorted(p for p in src.iterdir() if (p / "report.json").is_file()):
            rep = json.loads((run / "report.json").read_text(encoding="utf-8"))
            for t in ((rep.get("counts") or {}).get("completeness") or {}).get("tables") or []:
                if t.get("built"):
                    reports[t["table"]] = t
    assert reports, "vacuous: no built table"
    # a join table becomes edges, not nodes; a row withheld after a lifted hold is reported as
    # withheld, never silently dropped
    short = {t: (r["source_rows"], r["nodes"], r.get("rows_withheld"), r["edges_missing"])
             for t, r in reports.items()
             if (not r.get("association") and r["source_rows"] != r["nodes"] + (r.get("rows_withheld") or 0))
             or r["edges_missing"]}
    assert not short, short


def test_r5_capture_only_discovery_writes_nothing_to_the_graph():
    """AAX: between a discovery's first and last stage no node or edge version was written."""
    _run5()
    import sqlite3
    finals = [r for n in ("s8_discover_db_final", "s8_discover_files_final", "s8_discover_final",
                          "s8_discover_doc_final") for r in (_http_opt(n),) if r]
    assert finals, "vacuous: no discovery ran"
    c = sqlite3.connect(f"{GRAPH.resolve().as_uri()}?mode=ro", uri=True)
    before, after = (E / "artefacts" / "srccount_before_discover.json",
                     E / "artefacts" / "srccount_after_discover.json")
    for f in finals:
        resp = f["response"]
        assert resp["status"] == "discovered", resp["status"]
        assert resp["verify"]["counts"]["built"] == 0
        if before.is_file() and after.is_file():
            # another source was building concurrently: count the discovered source's own
            # pointer rows before and after instead of a time window (run5_srccount.py)
            b, a = (json.loads(x.read_text(encoding="utf-8")) for x in (before, after))
            if b["source"] == resp["source_id"]:
                assert a["pointer_rows"] == b["pointer_rows"], (b, a)
                continue
        done = [s["done_at"].replace("Z", "") for s in resp["stages"].values()]
        start, end = min(done)[:17] + "00", max(done)
        for t in ("nodes", "edges"):
            n = c.execute(f"select count(*) from {t} where substr(system_from,1,19) between ? and ?",
                          (start, end)).fetchone()[0]
            assert n == 0, (resp["run_id"], t, n)


def test_r5_setup_and_design_decisions_refuse_a_machine_principal():
    _run5()
    recs = [r for n in ("s8_plan_approve_machine", "s8_handoff_machine", "s9_approve_machine",
                        "s8_documents_machine", "s1_link_approve_machine", "s1_hold_lift_machine")
            for r in (_http_opt(n),) if r]
    assert recs, "vacuous"
    assert all(_code(r) == "MACHINE_ACTOR" for r in recs), [(r["path"], _code(r)) for r in recs]


def test_r5_a_handoff_seals_a_delegation_for_the_person_who_handed_off():
    _run5()
    h = _http_opt("s8_handoff") or _http("s8_handoff_doc")
    assert h["status"] == 202
    agent = h["response"]["agent"]
    seals = _jsonl(E / "adapter_agents" / "_delegations" / f"{agent}.delegations.jsonl")
    sealed = [s for s in seals if s.get("event") == "delegation_sealed"]
    assert sealed and sealed[-1]["principal_id"] == _principals()["primary"]
    assert sealed[-1]["authenticated_by"] and sealed[-1]["hash"]


def test_r5_a_document_only_source_can_be_handed_off():
    """W-05: a document source must reach the hand-off (explain says NO_TABLES, documents decided)."""
    _run5()
    hs = [r for n in ("s8_handoff_files", "s8_handoff_doc") for r in (_http_opt(n),) if r]
    assert hs, "vacuous: no document-only setup was attempted"
    assert all(r["status"] == 202 for r in hs), [(r["path"], r["status"], _code(r)) for r in hs]


def test_r5_the_delegated_answer_rereads_every_cell_and_is_complete():
    _run5()
    fed = _art("s6_fed.json")
    assert fed["second"]["status"] == 403
    if ESTATE == "workspace":
        import fed_check3
        out = fed_check3.main(E / "artefacts" / "s6_fed.json", "r1" if "r1" in fed else "r0", "pytest5")
        assert out["expected"] and out["rows_returned"] > 0, "vacuous"
        assert out["every_returned_value_correct"] and out["all_leaves_reread"]
        assert out["missing_groups"] == [], (out["missing_groups"], out["coverage_status"])
    else:
        chk = _art("s6_fed_check.json")
        assert chk["cells"] > 0 and chk["reread_ok"] == chk["cells"], chk
        assert chk["matches_independent"], chk


def test_r5_a_one_time_write_is_held_until_its_owner_approves_it():
    _run5()
    run = _http("s7_run_rw")["response"]
    assert run["state"] == "waiting_for_approval" and run["held"] >= 1
    other, missing = _http("s7_get_other"), _http("s7_get_missing")
    assert other["status"] == missing["status"] == 404 and other["response"] == missing["response"]
    assert _http("s7_approve_other")["status"] == 404
    assert _http("s7_approve_owner")["response"]["state"] == "done"
    again = _http("s7_approve_again")
    assert again["status"] == 409 and _code(again) == "DECISION_ALREADY_FINAL"


def test_r5_an_unconnected_channel_is_never_compiled_runnable():
    _run5()
    c = _http("s7_compile_email")["response"]
    assert c["runnable"] is False and c["open_questions"]


def test_r5_every_design_cell_rereads_live_and_subject_rows_are_withheld():
    _run5()
    chk = _art("ptrcheck_s9_dsd.json")
    assert chk["grounded"] > 0 and chk["all_reread"], {k: v for k, v in chk.items() if k != "failures"}
    job = _http("s9_build_job")["response"]["result"]
    assert job["pointer_check"]["all_resolved"] and job["pointer_check"]["all_match"]
    subj = _http("s9_subject_job")["response"]["result"]
    receipt = json.loads((E / "materialise" / subj["plan_hash"] / "receipt.json").read_text("utf-8"))
    withheld = (receipt.get("receipt") or {}).get("subject_rows_withheld",
                                                  receipt.get("subject_rows_withheld"))
    assert withheld and withheld > 0 and subj["cells"]["policy_suppressed"] > 0


def test_r5_a_design_is_built_with_sql_pushdown():
    _run5()
    job = _http("s9_build_job")["response"]["result"]
    receipt = json.loads((E / "materialise" / job["plan_hash"] / "receipt.json").read_text("utf-8"))
    assert "facts_sql_pushdown" in json.dumps(receipt)


def test_r5_a_tampered_design_log_is_refused_and_shown_loudly():
    _run5()
    b = _http("s9_build_tampered")
    assert b["status"] == 409 and _code(b) == "LEDGER_INTEGRITY_FAILED"
    act = _http("s10_activity_tampered")["response"]["items"]
    assert any(i["kind"] == "decision_log" and i.get("loud") for i in act)


def test_r5_security_probes():
    _run5()
    sec = _art("s11_sec.json")
    assert sec["no_token"] and set(sec["no_token"].values()) == {401}
    assert sec["bad_token"] == 401 and sec["batch_approval_plain_string"]["status"] == 422
    assert sec["package_typed_name"].startswith("refused")
    assert sec["literal_secret"]["status"] >= 400 and not sec["literal_secret"]["echoed"]
    assert sec["ssrf"] and {v["status"] for v in sec["ssrf"].values()} == {403}
    assert sec["command_source"]["code"] == "COMMAND_SOURCES_DISABLED"
    assert not {"e2e5_ssrf", "e2e5_cmd", "e2e5_leaky_url"} & set(sec["sources_after"])


def test_r5_a_planted_injection_document_is_held_not_built():
    _run5()
    inj = _art("s11_injection.json")
    assert inj["decision"] in ("review", "block") and inj["rules"], inj
    assert not inj["built"], inj


def test_r5_activity_is_owner_or_operator_only():
    _run5()
    a = {w: _http(f"s10_activity_{w}")["response"] for w in ("primary", "second", "operator")}
    assert a["primary"]["items"], "vacuous"
    mine = {i["id"] for i in a["primary"]["items"]}
    second = _principals()["second"]
    # the second principal sees only what it decided or started (a decision on a source whose
    # keeper the primary created is visible to BOTH, by Activity's own rule)
    for i in a["second"]["items"]:
        who = {(i.get("actor") or {}).get("principal_id"), (i.get("started_by") or {}).get("principal_id")}
        assert second in who, (i["kind"], i["id"], who)
    assert len(a["second"]["items"]) < len(mine)
    assert mine <= {i["id"] for i in a["operator"]["items"]}
    other, missing = _http("s10_run_other"), _http("s10_run_missing")
    assert other["status"] == missing["status"] == 404 and other["response"] == missing["response"]


def test_r5_every_activity_row_names_its_actor_with_one_shape():
    """R-028 #7: one actor object. A signed-in person's principal_type/authenticated_by must not
    differ between families (W-09)."""
    _run5()
    items = _http("s10_activity_final")["response"]["items"]
    assert items, "vacuous"
    shapes = collections.defaultdict(set)
    for i in items:
        a = i.get("actor") or {}
        assert "principal_id" in a, i["kind"]
        if isinstance(a.get("dev_only"), bool):              # a person (machines are 'absent')
            shapes[a["principal_id"]].add((a.get("principal_type"), a.get("authenticated_by")))
    assert shapes, "vacuous: no person row"
    assert all(len(v) == 1 for v in shapes.values()), dict(shapes)


def test_r5_no_response_carries_exception_text():
    _run5()
    import re
    pat = re.compile(r"Traceback|\b[A-Z][A-Za-z]+(Error|Exception): |no such table")
    files = sorted((E / "artefacts" / "http").glob("*.json"))
    assert len(files) > 20, "vacuous"
    hits = [p.name for p in files
            if pat.search(json.dumps(json.loads(p.read_text(encoding="utf-8")).get("response")))]
    assert not hits, hits


def test_r5_no_two_nodes_of_a_non_subject_kind_share_a_label_across_sources():
    """W-02: an organisation (or a keyed agreement such as a project) named identically in two
    sources must be ONE node. People are exempt: they are never merged (constraint 1)."""
    _run5()
    import sqlite3
    sys.path.insert(0, str(ROOT / "packages" / "graph"))
    from databridge_graph.packs import load_pack  # noqa: PLC0415
    # organisations and agreements only: a kind bound to `entity` may be a natural person even
    # when the pack does not declare it a subject (F-04), and people are never joined by name
    named = set()
    for p in ESTATES[ESTATE]["packs"].split(","):
        pack = load_pack(p)
        named |= {k for k, v in pack.node_kinds.items()
                  if str(getattr(v.binds_to, "value", v.binds_to)) in ("organisation", "agreement")}
        named -= set(pack.subject_kinds)
    c = sqlite3.connect(f"{GRAPH.resolve().as_uri()}?mode=ro", uri=True)
    rows = c.execute("select n.id, n.kind, lower(trim(n.label)), s.source_pointer_json from nodes n "
                     "join sources s on s.entity_id = n.id where n.system_to is null").fetchall()
    srcs, meta = collections.defaultdict(set), {}
    for nid, kind, label, ptr in rows:
        srcs[nid].add((json.loads(ptr) or {}).get("source_id"))
        meta[nid] = (kind, label)
    by_label = collections.defaultdict(list)
    for nid, (kind, label) in meta.items():
        if kind in named:
            by_label[(kind, label)].append(nid)
    assert by_label, "vacuous"
    split = {k: v for k, v in by_label.items()
             if len(v) > 1 and len(set().union(*(srcs[n] for n in v))) > 1}
    assert not split, list(split.items())[:5]


def test_r5_random_requests_all_pass():
    _run5()
    v = json.loads((E / "random_verdicts.json").read_text(encoding="utf-8"))
    reqs = json.loads((E / "random_requests.json").read_text(encoding="utf-8"))[ESTATE]
    assert set(v["verdicts"]) == {r["id"] for r in reqs}, "every request graded"
    assert any(r["cat"] == "dataset" and "fine-tun" in r["text"].lower() for r in reqs)
    assert [k for k, x in v["verdicts"].items() if x["verdict"] != "PASS"] == []


@pytest.mark.skipif(ESTATE != "finance", reason="the finance pack declares the identity rule")
def test_r5_an_approved_identity_rule_makes_one_legal_entity_and_never_merges_a_person():
    _run5()
    chk = _art("s3_identity.json")
    assert chk["decision_status"] == 200
    assert len(chk["legal_entity_nodes"]) == 1, chk["legal_entity_nodes"]
    assert set(chk["expected_sources"]) <= set(chk["sources_cited"]), chk
    assert chk["person_nodes_merged_across_sources"] == 0
