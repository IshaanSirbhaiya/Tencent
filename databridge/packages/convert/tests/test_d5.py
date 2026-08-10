"""Demo D5 (09_FINAL_PRD §27): XML → JSON via the container path, XXE canary still fires."""
from __future__ import annotations

import orjson

from databridge_convert.demos.d5 import run_d5


def test_d5_routes_clean_s2s():
    r = run_d5()
    assert r["decision_direction"] == "s2s"
    assert r["decision_refusals"] == []
    assert r["xml_registered"]


def test_d5_xml_to_json_values_preserved():
    r = run_d5()
    payload = orjson.loads(r["json"])
    orders = payload["orders"]["order"]
    assert len(orders) == 2
    assert orders[0]["customer"] == "Ana"
    assert orders[0]["total"] == "129.95"              # XML text → string (typed downstream)
    assert orders[0]["items"]["item"]["sku"] == "A-1"


def test_d5_roundtrip_stable():
    r = run_d5()
    assert r["roundtrip_root"] == r["source_tree"]


def test_d5_xxe_canary_refused():
    # the C04 XXE-refusal canary MUST still fire through this path
    assert run_d5()["xxe_refused"] is True
