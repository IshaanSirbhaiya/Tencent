"""deliverables/verify.py, round-5 Codex review fixes (docs/finals/reviews/round5_codex_final.md).

- #2 (B5 partial): a sentence's number is bound to EVERY content word of its clause, short ones
  included ("gas", "oil", "fee", "tax", "net", "SG"); only a small stop-word list is skipped.
- #3 (B6 partial): a chart series whose name is from the sources holds EVERY point to that metric
  in the point's own evidence row or sentence; a point from evidence that does not name the metric
  is dropped. A name not from the sources still falls back to "Values".
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from databridge_ib.deliverables import evidence as E
from databridge_ib.deliverables import verify as V


def snap(text, doc_id):
    return NS(doc_id=doc_id, platform="drive", container_name="Finance", title=doc_id,
              version="1", updated_at="2026-09-25T15:00:00+08:00", text=text)


def ev(**texts):
    return E.build_evidence({tag: snap(t, f"drive:{tag}") for tag, t in texts.items()})


# -- #2 short content words bind -----------------------------------------------------------------
@pytest.mark.parametrize("text,sentence,ok", [
    ("The gas cost is 10. The oil cost is 20.", "The gas cost is 10.", True),
    ("The gas cost is 10. The oil cost is 20.", "The gas cost is 20.", False),   # oil's value
    ("The gas cost is 10. The oil cost is 20.", "The oil cost is 10.", False),
    ("Net revenue is 50. Gross revenue is 60.", "The net revenue is 60.", False),
    ("Net revenue is 50. Gross revenue is 60.", "The net revenue is 50.", True),
    ("The fee rate is 5 and the tax rate is 7.", "The tax rate is 5.", False),
    ("The fee rate is 5 and the tax rate is 7.", "The tax rate is 7.", True),
])
def test_r5_short_content_words_are_bound_to_their_number(text, sentence, ok):
    e = ev(C1=text)
    got = V.check_sentence(sentence, ["C1"], e, V.Check())
    assert (got is not None) is ok, (sentence, got)


def test_r5_claim_tokens_keep_short_words_and_skip_stop_words():
    assert V.claim_tokens("The gas cost is") == {"gas", "cost"}
    assert V.claim_tokens("the SG fee was") == {"sg", "fee"}
    assert V.claim_tokens("net tax of the ID entity is") == {"net", "tax", "id", "entity"}


# -- #3 every point of a named series is bound to its metric -------------------------------------
REV = "Market,Revenue\nSingapore,100\nHong Kong,200\n"
COST = "Market,Cost\nIndonesia,30\nMalaysia,40\n"


def _chart(name, pts):
    return {"title": "t", "type": "bar", "x_label": "", "y_label": "", "series": [
        {"name": name, "points": [{"x": x, "value": v, "raw": str(v), "tag": t}
                                  for x, v, t in pts]}]}


MIXED = [("Singapore", 100, "C1"), ("Hong Kong", 200, "C1"),
         ("Indonesia", 30, "C2"), ("Malaysia", 40, "C2")]


def test_r5_mixed_evidence_series_cannot_release_costs_as_revenue():
    e = ev(C1=REV, C2=COST)
    c = V.Check()
    got = V.check_chart(_chart("Revenue", MIXED), e, c)
    assert got is not None and got["series"][0]["name"] == "Revenue"
    assert [p["value"] for p in got["series"][0]["points"]] == [100, 200]
    assert c.values_dropped == 2 and c.values_kept == 2


def test_r5_all_points_from_evidence_without_the_metric_are_dropped():
    e = ev(C1=REV, C2=COST)
    c = V.Check()
    got = V.check_chart(_chart("Revenue", [("Indonesia", 30, "C2"), ("Malaysia", 40, "C2"),
                                           ("Singapore", 100, "C1")]), e, c)
    assert got is None                                   # one point left: too few for a chart
    assert c.reasons.get("not_found") == 2


def test_r5_a_series_name_not_from_the_sources_still_falls_back_to_values():
    e = ev(C1=REV, C2=COST)
    c = V.Check()
    got = V.check_chart(_chart("Turnover", MIXED), e, c)
    assert got is not None and got["series"][0]["name"] == "Values"
    assert len(got["series"][0]["points"]) == 4 and c.names_dropped == 1
