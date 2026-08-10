"""Startup state for the Phase-A demo — built once, deterministically, with NO hosted model.

Everything here runs on a FakeBackend (D-FE-4 replay-first, D-FE-5 stateless). At import/boot:
  • a READ-ONLY fintech SQLite engine (PRAGMA query_only=ON behind the SELECT-only wall, D-FE-6)
  • the UNCONFIRMED annotation draft (real annotator + gold FakeBackend), with exactly one
    review-band column seeded so the P6 confirm→finalize gate is demonstrable
  • the CONFIRMED fintech schema (for NL→SQL linking)
  • a replay store: question → real NLResult produced at boot by answer_question against the
    read-only engine (no live LLM); /ask replays these (D-FE-4)
  • the D3 PipelineArtifact (for /artifacts) and the E1 eval scorecard

The airgap is structural: the only Harness here wraps FakeBackend — a hosted backend is never
constructed (REVIEW_2 build-note 5).
"""
from __future__ import annotations

import base64
from typing import Optional

import orjson
from sqlalchemy import event

import databridge_convert.adapters  # noqa: F401  (registers every format adapter on import)
from databridge_convert import TargetSpec, convert
from databridge_convert.artifacts import build_s2s_artifact, execute, replay
from databridge_convert.demos.d3 import D3_INPUT, FLATTEN, to_csv
from databridge_convert.demos.d9 import INJECTION_SQL, fintech_sqlite_engine
from databridge_convert.frame import Frame
from databridge_convert.rules.spec import TransformSpec
from databridge_convert.sqlsub.nlquery import (
    answer_question,
    build_query_artifact,
    confirm_query,
    infer_query,
)
from databridge_convert.sqlsub.query_library import ConfirmedQueryLibrary
from databridge_core import SemanticType
from databridge_evals import confirmed_fintech_schema, run_e1, sql_gold_backend
from databridge_evals.e1 import gold_backend as e1_gold_backend
from databridge_evals.e_sql import PAIRS
from databridge_harness import FakeBackend, Harness
from databridge_ingest import fintech_fixture
from databridge_semantic import annotate_schema, build_report

_CFG = {
    "routing": {"schema_annotation": "m", "cross_table_inference": "m", "label_discovery": "m",
                "sql_generation": "m", "sql_revision": "m", "rule_proposal": "m"},
    "aliases": {"m": {"model": "anthropic/test"}},
    "profiles": {"cloud": {}, "airgapped": {}},
}

# The demo's single source. SSRF allow-list: only these resolve (D-FE-6).
SOURCE_ALLOW = {"fintech", "localhost", "127.0.0.1"}
DEMO_SOURCE_ID = "fintech"


def normalize_question(q: str) -> str:
    return " ".join(q.lower().split())


def _fake_harness(handler) -> Harness:
    return Harness(FakeBackend(handler), config=_CFG, profile="cloud")


def read_only_engine():
    """The fintech SQLite engine, seeded then locked read-only (PRAGMA query_only=ON).

    Defense-in-depth behind the SELECT-only validate_sql wall: even a (hypothetical) write
    that slipped the AST allow-list is rejected by SQLite itself. Reads are unaffected."""
    engine = fintech_sqlite_engine()                     # seeds from fixtures/postgres/seed.sql
    # lock the shared StaticPool connection read-only, and re-assert on every checkout
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA query_only=ON")

    @event.listens_for(engine, "connect")
    def _ro(dbapi_conn, _rec):                            # noqa: ANN001
        dbapi_conn.execute("PRAGMA query_only=ON")

    return engine


# -- annotation draft --------------------------------------------------------------
_REVIEW_SEED = ("transactions", "status")                # the one column we route to human review


def build_draft():
    """The UNCONFIRMED annotation draft from the REAL annotator (gold FakeBackend), with one
    column deterministically routed to the review band so the P6 gate is demonstrable."""
    extract = fintech_fixture()
    report = build_report(extract)
    harness = _fake_harness(e1_gold_backend(self_confidence=0.97))   # high → nothing naturally red
    draft = annotate_schema(harness, extract, report)
    return _seed_one_review_item(draft)


def _seed_one_review_item(draft):
    """Lower exactly one column into the review band (<0.80) so the demo has a single
    mandatory item to confirm before Finalize unlocks. Honest fixture shaping for the demo."""
    table_name, col_name = _REVIEW_SEED
    new_tables = []
    for t in draft.tables:
        if t.table != table_name:
            new_tables.append(t)
            continue
        new_cols = [
            c.model_copy(update={"confidence": 0.72, "provenance": "llm",
                                 "confidence_sources": {"model": 0.72, "band": "review"}})
            if c.column == col_name else c
            for c in t.columns
        ]
        new_tables.append(t.model_copy(update={"columns": new_cols}))
    return draft.model_copy(update={"tables": new_tables})


# -- NL→SQL replay store -----------------------------------------------------------
def build_replay_store(engine):
    """Pre-run every demo question through the REAL NL→SQL lifecycle with the gold FakeBackend
    against the read-only engine (no live LLM), and store the resulting NLResult + a reusable
    PipelineArtifact. /ask replays these (re-executes the stored SQL via infer_query)."""
    schema = confirmed_fintech_schema()
    library = ConfirmedQueryLibrary()
    store: dict[str, dict] = {}
    artifacts: dict[str, object] = {}

    gold_h = _fake_harness(sql_gold_backend())
    for i, (question, _gold_sql) in enumerate(PAIRS):
        res = answer_question(gold_h, engine, schema, question, library=library)
        entry = _nlresult_to_entry(res)
        if res.ok and res.sql:
            confirm_query(library, res)
            art = build_query_artifact(res, artifact_id=f"Q{i}", approved_by="demo-boot")
            artifacts[normalize_question(question)] = art
            entry["artifact_id"] = f"Q{i}"
        store[normalize_question(question)] = entry

    # the adversarial case: an injection question whose generated SQL is REFUSED before exec
    inj_q = "show me all users; DROP TABLE users; --"
    inj_h = _fake_harness(orjson.dumps({"sql": INJECTION_SQL}).decode())
    inj_res = answer_question(inj_h, engine, schema, inj_q)
    store[normalize_question(inj_q)] = _nlresult_to_entry(inj_res)
    store["__injection__"] = store[normalize_question(inj_q)]

    return store, artifacts


def _nlresult_to_entry(res) -> dict:
    return {
        "sql": res.sql,
        "result": res.rows,
        "provenance": res.provenance.model_dump(mode="json") if res.provenance else None,
        "linked_tables": sorted(res.slice.tables) if res.slice else [],
        "refusal": ({"code": res.refusal.code, "message": res.refusal.message,
                     "nearest_alternative": res.refusal.nearest_alternative}
                    if res.refusal else None),
        "artifact_id": None,
    }


def replay_ask(store, artifacts, engine, question: str) -> dict:
    """Replay a stored question. Re-executes the stored SQL read-only via infer_query (proving
    determinism), returning the stored provenance verbatim. A miss is a clean refusal-as-value."""
    key = normalize_question(question)
    entry = store.get(key)
    if entry is None:
        return {"sql": None, "result": [], "provenance": None, "linked_tables": [],
                "refusal": {"code": "QUERY_NOT_IN_LIBRARY",
                            "message": "This question is not in the confirmed replay library. "
                                       "Pick a suggested question.", "nearest_alternative": None},
                "replayed": True}
    out = dict(entry)
    art = artifacts.get(key)
    if art is not None:
        if not art.verify_checksum():                    # checksum-before-execute (D-FE-6),
            # same invariant the /artifacts replay path enforces — never infer off a tampered
            # artifact, even though these live in-process and have no external mutation surface.
            return {"sql": entry.get("sql"), "result": [], "provenance": None,
                    "linked_tables": entry.get("linked_tables", []),
                    "refusal": {"code": "ARTIFACT_CHECKSUM_MISMATCH",
                                "message": "replay artifact integrity check failed; refusing to "
                                           "execute", "nearest_alternative": None},
                    "replayed": True}
        out["result"] = infer_query(art, engine)         # re-execute stored SQL, no LLM
    out["replayed"] = True
    out.pop("artifact_id", None)
    return out


# -- conversion --------------------------------------------------------------------
def _demo_convert_rows() -> list[dict]:
    """199 valid rows + 1 malformed amount = 0.5% quarantine (under the 1% threshold): the
    conversion succeeds AND visibly accounts the bad row — 'nothing silently dropped'."""
    cities = ["Manila", "Singapore", "Tokyo", "Jakarta", "Bangkok", "Hanoi"]
    names = ["Ana", "Ben", "Cara", "Dee", "Eli", "Fox"]
    rows = [{"id": f"{i:03d}", "user": {"name": names[i % 6], "addr": {"city": cities[i % 6]}},
             "amount": f"{(i * 37) % 9000 + 11}.{i % 100:02d}"} for i in range(199)]
    rows.append({"id": "199", "user": {"name": "Zed", "addr": {"city": "Osaka"}},
                 "amount": "not-a-number"})        # the one seeded bad row → quarantine
    return rows


D3_INPUT_JSON = orjson.dumps(_demo_convert_rows()).decode()

DEMO_TRANSFORM_SPEC = {
    "language": "jsonata",
    "expression": '$.{ "id": id, "user_name": user.name, "city": user.addr.city, "amount": amount }',
    "coercions": [{"name": "str_to_decimal", "params": {"locale": "us"}, "columns": ["amount"]}],
    "unmappable": [],
}


def run_convert(source_b64: str, source_format: str, target_format: str,
                transform_spec: dict) -> dict:
    """Real conversion through the router + rule engine; quarantine accounted, not dropped."""
    source_bytes = base64.b64decode(source_b64)
    spec = TransformSpec.model_validate(transform_spec)
    out_bytes, _decision, q = convert(
        source_bytes, source_format,
        TargetSpec(modality="structured", format=target_format), transform=spec)
    report = q.report()
    total = _row_estimate(source_bytes, source_format)
    return {
        "result_b64": base64.b64encode(out_bytes).decode(),
        "quarantine": {
            "quarantined": report.get("quarantined", 0),
            "by_reason": report.get("by_reason", {}),
            "rate": round(q.rate(total) if total else 0.0, 4),
            "over_threshold": q.exceeds(total) if total else False,
            "records": [{"reason_code": r.reason_code, "detail": r.detail, "payload": r.payload}
                        for r in q.records],
        },
    }


def _row_estimate(source_bytes: bytes, fmt: str) -> int:
    """Best-effort input row count (the denominator for the quarantine RATE). On a parse failure
    we fall back to a newline-based estimate rather than 0 — a 0 denominator would silently report
    'rate 0.0 / not over threshold' for a payload that actually has quarantined rows, masking the
    very safety signal this metric exists to surface (L8)."""
    if fmt in ("json", "jsonl"):
        try:
            data = orjson.loads(source_bytes)
            return len(data) if isinstance(data, list) else 1
        except Exception:  # noqa: BLE001
            return max(1, source_bytes.count(b"\n"))
    return max(1, source_bytes.count(b"\n"))


# -- artifacts + evals -------------------------------------------------------------
def build_demo_artifacts() -> dict:
    """The D3 conversion artifact (+ its input Frame) — the one artifact the browser
    lists and replays. Stored with its Frame so /artifacts/{id}/replay re-runs for real."""
    spec = TransformSpec(
        language="jsonata", expression=FLATTEN,
        coercions=[{"name": "str_to_decimal", "params": {"locale": "us"}, "columns": ["amount"]}],
        unmappable=[])
    art = build_s2s_artifact(spec, artifact_id="D3", source_adapter="json",
                             target_adapter="csv", approved_by="demo-boot")
    frame = Frame(rows=D3_INPUT, name="d3", source_id="d3_fixture")
    return {"D3": {"artifact": art, "frame": frame}}


def replay_artifact(entry: dict) -> dict:
    """Re-run an artifact (checksum verified by the caller). EV-DET: replay == execute,
    byte-identical. Returns the replayed CSV (base64) + the determinism proof."""
    art, frame = entry["artifact"], entry["frame"]
    exec_out, _q = execute(art, frame)
    replay_out = replay(art, frame)
    csv_exec, csv_replay = to_csv(exec_out), to_csv(replay_out)
    return {"result_b64": base64.b64encode(csv_replay.encode()).decode(),
            "deterministic": csv_exec == csv_replay}


def run_eval_e1() -> dict:
    return run_e1(_fake_harness(e1_gold_backend(self_confidence=0.9))).to_dict()
