"""R-021 Data-page hand-over #3: the converter's quarantine threshold is reported by the gateway,
not invented by the page. `QuarantineView` carries it (optional, additive); absent stays absent."""
from __future__ import annotations

from databridge_convert.quarantine import DEFAULT_THRESHOLD

from databridge_gateway.models import ConvertResponse, QuarantineView


def test_the_view_carries_the_threshold_the_converter_applied():
    view = QuarantineView(quarantined=2, rate=0.02, over_threshold=True,
                          threshold=DEFAULT_THRESHOLD, threshold_rule="rate > threshold")
    body = ConvertResponse(result_b64="", quarantine=view, artifact_id="a").model_dump()
    assert body["quarantine"]["threshold"] == DEFAULT_THRESHOLD
    assert body["quarantine"]["threshold_rule"] == "rate > threshold"


def test_an_unreported_threshold_is_absent_not_defaulted():
    view = QuarantineView(quarantined=0)
    assert view.threshold is None and view.threshold_rule is None


def test_run_convert_reports_the_threshold_it_applied():
    """`/convert`'s quarantine view carries the threshold the conversion actually applied and the
    rule it applied it with, and `over_threshold` is consistent with both (derived, not a literal
    verdict). A run over the threshold never gets here: the converter raises instead."""
    import base64

    import orjson

    from databridge_gateway.demo_state import run_convert

    spec = {"language": "jsonata",
            "expression": '$.{ "id": id, "amount": amount }',
            "coercions": [{"name": "str_to_decimal", "params": {"locale": "us"},
                           "columns": ["amount"]}],
            "unmappable": []}
    n_good = int(2 / DEFAULT_THRESHOLD)                  # one bad row stays under the line
    rows = [{"id": str(i), "amount": f"{i}.50"} for i in range(n_good)]
    rows.append({"id": "bad", "amount": "not-a-number"})
    q = run_convert(base64.b64encode(orjson.dumps(rows)).decode(), "json", "csv",
                    spec)["quarantine"]
    view = QuarantineView(**q)
    assert view.quarantined == 1 and view.rate > 0                    # non-vacuous
    assert view.threshold == DEFAULT_THRESHOLD
    assert view.threshold_rule == "rate > threshold"
    assert view.over_threshold == (view.rate > view.threshold)
