"""Conversion + artifact helpers for the gateway's `/convert` and `/artifacts` routes, and the
on-demand E1 plumbing proof behind `/evals/e1`.

What this module NO LONGER does (removed 2026-10-01, ROADMAP_KNOWLEDGE_PLATFORM §3 item 17):
it used to build, at gateway boot, a canned fintech annotation draft (`/annotate`), a read-only
fintech SQLite engine plus a question→answer replay dictionary (`/ask`), a hard-coded 200-row
conversion sample (`/convert/sample`) and a pre-registered demo artifact (`/artifacts`). Every one
of those answered from data written down in advance rather than from the request. The routes are
gone and nothing here is built at boot. The fixtures they read (`databridge_ingest.fintech_fixture`,
`databridge_convert.demos.d3`/`d9`, `databridge_evals.e_sql.PAIRS`) stay on disk — they are
still used by the package test suites — they are simply no longer wired into the gateway.

`run_eval_e1` (a gold-backend plumbing score) is kept but NO LONGER WIRED to any route (R-019
round 2, main #3): a route whose answer is a fixed backend's fixed score is a canned result
whatever its label says. It stays importable for tests/CI; `/evals/e1` and `/evals/brain` 404.
"""
from __future__ import annotations

import base64

import databridge_convert.adapters  # noqa: F401  (registers every format adapter on import)
from databridge_convert import TargetSpec, convert
from databridge_convert.artifacts import execute, replay
from databridge_convert.quarantine import DEFAULT_THRESHOLD
from databridge_convert.rules.spec import TransformSpec

__all__ = ["TransformSpec", "run_convert", "replay_artifact", "source_frame", "artifact_entry",
           "source_record_count", "run_eval_e1", "E1_PROOF_LABEL"]

_CFG = {
    "routing": {"schema_annotation": "m", "cross_table_inference": "m", "label_discovery": "m",
                "sql_generation": "m", "sql_revision": "m", "rule_proposal": "m"},
    "aliases": {"m": {"model": "anthropic/test"}},
    "profiles": {"cloud": {}, "airgapped": {}},
}


# -- conversion --------------------------------------------------------------------
def run_convert(source_b64: str, source_format: str, target_format: str,
                transform_spec: dict) -> dict:
    """Real conversion through the router + rule engine; quarantine accounted, not dropped."""
    source_bytes = base64.b64decode(source_b64)
    spec = TransformSpec.model_validate(transform_spec)
    out_bytes, _decision, q = convert(
        source_bytes, source_format,
        TargetSpec(modality="structured", format=target_format), transform=spec)
    report = q.report()
    # The denominator is the number of RECORDS the source adapter parsed — the same read the
    # conversion made — never a newline count (R-019 round 2, main #6): a CSV whose one record
    # holds many quoted newlines is one record, and 100% of it quarantined is a 100% rate.
    total = source_record_count(source_bytes, source_format)
    return {
        "result_b64": base64.b64encode(out_bytes).decode(),
        "quarantine": {
            "quarantined": report.get("quarantined", 0),
            "by_reason": report.get("by_reason", {}),
            "rate": round(q.rate(total) if total else 0.0, 4),
            "over_threshold": q.exceeds(total, DEFAULT_THRESHOLD) if total else False,
            # What the conversion APPLIED (the router runs the rule engine at its default; a run
            # over it raises before this point) and the rule `QuarantineStore.exceeds` uses.
            "threshold": DEFAULT_THRESHOLD,
            "threshold_rule": "rate > threshold",
            "records": [{"reason_code": r.reason_code, "detail": r.detail, "payload": r.payload}
                        for r in q.records],
        },
    }


def source_record_count(source_bytes: bytes, fmt: str) -> int:
    """How many records the source format's registered adapter reads from ``source_bytes`` —
    the Frame the conversion itself ran over (`REGISTRY[fmt].read` -> the router's IR->Frame
    bridge). Raises if the adapter cannot read it; the conversion would have failed first."""
    return len(_read_frame(source_bytes, fmt)[0].rows)


def _read_frame(source_bytes: bytes, fmt: str):
    from databridge_convert.router.registry import REGISTRY
    from databridge_convert.router.router import _ir_to_frame

    info = REGISTRY.get(fmt)
    if info is None or not info.can_read:
        raise ValueError(f"no readable adapter for {fmt!r}")
    ir = info.read(source_bytes)
    return _ir_to_frame(ir), ir


# -- artifacts ---------------------------------------------------------------------
def source_frame(source_b64: str, source_format: str, *, name: str):
    """The input Frame of a conversion, read by the SAME registered source adapter `convert`
    uses (`REGISTRY[source_format].read` -> the router's IR->Frame bridge), so the artifact
    `/convert` registers replays over exactly what was converted — for every format it accepts,
    not only JSON."""
    from databridge_convert.frame import Frame

    frame, _ir = _read_frame(base64.b64decode(source_b64), source_format)
    return Frame(rows=list(frame.rows), name=name, source_id="convert")


def artifact_entry(artifact, *, source_b64: str, source_format: str, target_format: str,
                   result_b64: str, name: str) -> dict:
    """What `/convert` registers: the artifact, the frame it ran over, the source snapshot the
    target IR is stamped with, the TARGET format the caller asked for, and the digest of the
    bytes the conversion actually returned — so a replay can be serialised the same way and
    compared with the original, not only with itself (R-019 round 2, main #4)."""
    import hashlib

    from databridge_convert.frame import Frame

    frame, ir = _read_frame(base64.b64decode(source_b64), source_format)
    return {"artifact": artifact,
            "frame": Frame(rows=list(frame.rows), name=name, source_id="convert"),
            "snapshot": ir.snapshot, "target_format": target_format,
            "result_sha256": hashlib.sha256(base64.b64decode(result_b64)).hexdigest()}


def _write_target(frame, target_format: str, snapshot) -> bytes:
    """Serialise ``frame`` through the TARGET format's registered adapter — the router's own
    Frame->IR bridge and the adapter's `write`, exactly as `convert` does."""
    from databridge_convert.router.registry import REGISTRY
    from databridge_convert.router.router import _frame_to_ir

    tgt = REGISTRY.get(target_format)
    if tgt is None or not getattr(tgt, "can_write", True):
        raise ValueError(f"no writable adapter for {target_format!r}")
    return tgt.write(_frame_to_ir(frame, tgt.ir, snapshot))


def replay_artifact(entry: dict) -> dict:
    """Re-run an artifact registered by a real `/convert` call (checksum verified by the caller).

    The replay is serialised in the artifact's REQUESTED target format (JSON stays JSON), and
    ``deterministic`` is true only when execute and replay produce identical bytes AND those
    bytes are the ones the original conversion returned (compared by digest). Two CSV renders
    agreeing with each other proved nothing about a JSON conversion (R-019 round 2, main #4)."""
    import hashlib

    art, frame = entry["artifact"], entry["frame"]
    fmt, snapshot = entry["target_format"], entry["snapshot"]
    exec_out, _q = execute(art, frame)
    replay_out = replay(art, frame)
    exec_bytes = _write_target(exec_out, fmt, snapshot)
    replay_bytes = _write_target(replay_out, fmt, snapshot)
    matches_original = hashlib.sha256(replay_bytes).hexdigest() == entry["result_sha256"]
    return {"result_b64": base64.b64encode(replay_bytes).decode(),
            "target_format": fmt,
            "matches_original": matches_original,
            "deterministic": exec_bytes == replay_bytes and matches_original}


# -- evals -------------------------------------------------------------------------
E1_PROOF_LABEL = {
    "kind": "plumbing_proof",
    "backend": "deterministic_gold",
    "note": ("Scores the schema annotator's plumbing against a deterministic gold backend over a "
             "fixed synthetic schema. It is not a measurement of any model's accuracy on your "
             "data."),
}


def run_eval_e1() -> dict:
    """E1 over the gold FakeBackend, labelled. NOT wired to a route (R-019 round 2, main #3) —
    a CI/test helper only; never served as a runtime answer."""
    from databridge_evals import run_e1
    from databridge_evals.e1 import gold_backend as e1_gold_backend
    from databridge_harness import FakeBackend, Harness
    harness = Harness(FakeBackend(e1_gold_backend(self_confidence=0.9)), config=_CFG,
                      profile="cloud")
    return {**run_e1(harness).to_dict(), **E1_PROOF_LABEL}
