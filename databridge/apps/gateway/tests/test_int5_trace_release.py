"""INT5 — `/intelligence/ask` traces name the pointer of every value they release.

ACT3 made `TraceHandle.released()` require a `pointer_id` that an earlier `pointer` step recorded
for the SAME source. `_record_agent_read` released with none, so every answered read showed a
`RELEASE_POINTER_NOT_RECORDED` defect. (Its `LEDGER_APPEND_NOT_RECORDED` defect is deliberate and
documented there: this surface writes no read entry yet.)"""
from __future__ import annotations

from types import SimpleNamespace

from databridge_core import SourcePointer


def test_int5_every_released_intelligence_value_names_a_recorded_pointer():
    from databridge_gateway.intelligence_routes import _record_agent_read
    from databridge_gateway.trace_recorder import TraceRecorder

    ptr = SourcePointer(source_id="doc-1", kind="doc_span", document_id="doc-1", char_start=0,
                        char_end=5)
    response = SimpleNamespace(
        reads=[SimpleNamespace(tool="node_detail", status="ok")],
        values=[SimpleNamespace(tool="node_detail", field="label", source=ptr, redacted=False),
                SimpleNamespace(tool="node_detail", field="role", source=ptr, redacted=False)],
        unresolved=[], requires_human_selection=False)
    tr = TraceRecorder().begin(surface="intelligence")
    _record_agent_read(tr, response, None, wall_ms=1)
    trace = tr.close("answered")
    released = [s.detail for s in trace.steps if s.kind == "released"]
    assert len(released) == 2                                            # non-vacuous
    assert all(r["pointer_id"] and r["pointer_recorded"] for r in released), released
    codes = trace.integrity()["defect_codes"]
    assert "RELEASE_POINTER_NOT_RECORDED" not in codes
    # the gaps left are the two `_record_agent_read` documents: no read-ledger entry on this
    # surface yet, and no token count from the harness (STEP_UNRECORDABLE, said, not zero)
    assert set(codes) <= {"LEDGER_APPEND_NOT_RECORDED", "STEP_UNRECORDABLE"}


def test_tidy_a_withheld_value_is_recorded_withheld_and_never_released():
    """R-028 TIDY #7: the console's withheld value is the standard envelope in `text`; the trace
    records it as a withheld cell and never as a released (cited) value."""
    from databridge_agent.response import AnswerValue, WithheldValue
    from databridge_gateway.intelligence_routes import _record_agent_read
    from databridge_gateway.trace_recorder import TraceRecorder

    ptr = SourcePointer(source_id="doc-1", kind="doc_span", document_id="doc-1", char_start=0,
                        char_end=5)
    held = AnswerValue(key="k1", tool="node_detail", row=0, field="label", scope="", citation="c",
                       source=ptr, text=WithheldValue(reason="instruction_shaped",
                                                      pointers=(ptr.model_dump(mode="json"),)))
    shown = held.model_copy(update={"key": "k2", "field": "role", "text": "ok"})
    response = SimpleNamespace(reads=[], values=[held, shown], unresolved=[],
                               requires_human_selection=False)
    tr = TraceRecorder().begin(surface="intelligence")
    _record_agent_read(tr, response, None, wall_ms=1)
    trace = tr.close("answered")
    cells = {s.detail.get("field_path"): s.detail.get("state") for s in trace.steps
             if s.kind == "cell"}
    released = [s.detail.get("field_path") for s in trace.steps if s.kind == "released"]
    assert cells == {"node_detail.label": "withheld", "node_detail.role": "grounded"}
    assert released == ["node_detail.role"]
