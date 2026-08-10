"""Demo D3 (09_FINAL_PRD §27): nested JSON → flattened CSV — the FIRST working conversion.

Full lifecycle: plan (LLM proposes a JSONata mapping) → validate (syntactic + semantic
sample run) → confirm (auto, since this is a test fixture) → execute → artifact → replay
(EV-DET byte-identical) → infer (single record). The model surface is exercised via
FakeBackend, so this runs with no API keys.

Real format adapters + router (C03/C04, PROMPT 6) replace the inline CSV writer and the
hand-fed JSON input — the D3 demo is re-run THROUGH them then. Here it uses test shortcuts.
"""
from __future__ import annotations

import orjson

from databridge_harness import FakeBackend, Harness

from ..artifacts import audit, build_s2s_artifact, execute, infer, replay
from ..frame import Frame
from ..rules.proposer import propose_transform
from ..rules.validator import validate_semantic, validate_syntactic

D3_INPUT = [
    {"id": "001", "user": {"name": "Ana", "addr": {"city": "Manila"}}, "amount": "1,234.56"},
    {"id": "002", "user": {"name": "Ben", "addr": {"city": "Singapore"}}, "amount": "89.00"},
    {"id": "003", "user": {"name": "Cara", "addr": {"city": "Tokyo"}}, "amount": "12,000.00"},
]
TARGET_FIELDS = ["id", "user_name", "city", "amount"]
FLATTEN = '$.{ "id": id, "user_name": user.name, "city": user.addr.city, "amount": amount }'

_CFG = {
    "routing": {"rule_proposal": "m"},
    "aliases": {"m": {"model": "anthropic/test"}},
    "profiles": {"cloud": {}, "airgapped": {}},
}


def proposer_harness() -> Harness:
    """A Harness whose FakeBackend returns the D3 mapping (stands in for the real LLM plan)."""
    spec = {
        "language": "jsonata", "expression": FLATTEN,
        "coercions": [{"name": "str_to_decimal", "params": {"locale": "us"},
                       "columns": ["amount"]}],
        "unmappable": [],
    }
    return Harness(FakeBackend(orjson.dumps(spec).decode()), config=_CFG, profile="cloud")


def _csv_cell(v: object) -> str:
    if v is None:
        return ""
    s = str(v)
    if any(c in s for c in (",", '"', "\n")):
        return '"' + s.replace('"', '""') + '"'
    return s


def to_csv(frame: Frame) -> str:
    cols = frame.columns()
    lines = [",".join(cols)]
    lines += [",".join(_csv_cell(r.get(c)) for c in cols) for r in frame.rows]
    return "\n".join(lines) + "\n"


def run_d3(harness: Harness | None = None) -> dict:
    """Run the full D3 lifecycle and return every artifact for assertions."""
    harness = harness or proposer_harness()
    frame = Frame(rows=D3_INPUT, name="d3", source_id="d3_fixture")

    # PLAN
    spec = propose_transform(harness, source_sample=D3_INPUT,
                             target_fields=TARGET_FIELDS, source_id="d3")
    # VALIDATE
    syn_ok, syn_detail = validate_syntactic(spec)
    sem = validate_semantic(spec, frame, target_fields=TARGET_FIELDS)
    # CONFIRM (auto for the fixture) → ARTIFACT
    art = build_s2s_artifact(
        spec, artifact_id="D3", source_adapter="json", target_adapter="csv",
        approved_by="auto-test",
        model_routing={"rule_proposal": harness._resolve("rule_proposal")})
    # EXECUTE → CSV
    out, q = execute(art, frame)
    csv_exec = to_csv(out)
    # REPLAY (EV-DET byte-identity)
    csv_replay = to_csv(replay(art, frame))
    # INFER (single record)
    inferred = infer(art, D3_INPUT[0])

    return {
        "spec": spec,
        "syntactic": (syn_ok, syn_detail),
        "semantic": sem,
        "artifact": art,
        "csv": csv_exec,
        "csv_replay": csv_replay,
        "deterministic": csv_exec == csv_replay,
        "infer": inferred,
        "quarantine": q.report(),
        "audit": audit(art),
    }


def run_d3_via_router(harness: Harness | None = None) -> dict:
    """D3 through the REAL adapters + REAL router (C01–C04) — no Frame/inline-CSV shortcut.

    JSON bytes → json source adapter → TypedRecord → engine → RowBatch → csv target
    adapter → CSV bytes. Must produce the same CSV as the shortcut path (run_d3)."""
    import databridge_convert.adapters  # noqa: F401  (registers every adapter on import)
    from ..router import TargetSpec, convert

    harness = harness or proposer_harness()
    spec = propose_transform(harness, source_sample=D3_INPUT,
                             target_fields=TARGET_FIELDS, source_id="d3")
    json_bytes = orjson.dumps(D3_INPUT)
    csv_bytes, decision, q = convert(
        json_bytes, "json", TargetSpec(modality="structured", format="csv"), transform=spec)
    return {"csv": csv_bytes.decode("utf-8"), "decision": decision, "quarantine": q.report()}
