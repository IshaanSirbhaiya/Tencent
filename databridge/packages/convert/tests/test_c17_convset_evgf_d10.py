"""C17 conversation_set serialisers (§12.4) + EV-GF zero-escape (§23) + Demo D10 (§27)."""
from __future__ import annotations

from databridge_core import SnapshotIdentity, SourcePointer

from databridge_convert.generate.conversation_set import (
    from_message_log,
    provenance_complete,
    provenance_sidecar,
    serialise,
    to_jsonl,
)
from databridge_convert.demos.d10 import run_d10
from ev_gf import run_ev_gf


# --- conversation_set -------------------------------------------------------------
_ROWS = [
    {"id": 1, "user_id": 7, "body": "my card was charged twice", "is_from_agent": 0},
    {"id": 2, "user_id": 7, "body": "we are looking into it", "is_from_agent": 1},
    {"id": 3, "user_id": 9, "body": "where is my refund", "is_from_agent": 0},
]


def test_from_message_log_groups_and_grounds():
    cset = from_message_log(_ROWS)
    assert len(cset.conversations) == 2                 # grouped by user_id (7, 9)
    c7 = cset.conversations[0]
    assert [t.role for t in c7.turns] == ["user", "assistant"]
    assert c7.turns[0].content == "my card was charged twice"
    # every turn grounded to its source row
    assert c7.turns[1].facts_used[0].kind == "table_cell"
    assert c7.turns[1].facts_used[0].row_id == "2"


def test_serialisers_shapes():
    cset = from_message_log(_ROWS)
    oa = serialise(cset, "openai")
    assert oa[0]["messages"][0] == {"role": "user", "content": "my card was charged twice"}
    sg = serialise(cset, "sharegpt")
    assert sg[0]["conversations"][0]["from"] == "human"
    assert sg[0]["conversations"][1]["from"] == "gpt"
    al = serialise(cset, "alpaca")
    assert al[0] == {"instruction": "my card was charged twice", "input": "",
                     "output": "we are looking into it"}


def test_jsonl_and_provenance_complete():
    cset = from_message_log(_ROWS)
    jl = to_jsonl(serialise(cset, "openai"))
    assert jl.count(b"\n") == 2 and jl.endswith(b"\n")
    assert provenance_complete(cset) is True
    side = provenance_sidecar(cset)
    assert all(s["facts_used"] for s in side)


def test_unknown_format_rejected():
    import pytest
    with pytest.raises(ValueError):
        serialise(from_message_log(_ROWS), "llama-chat")


# --- EV-GF (0 fabrications escape) ------------------------------------------------
def test_ev_gf_zero_escapes():
    out = run_ev_gf()
    assert out["n_escapes"] == 0, out["escapes"]
    assert out["gate_zero_escapes"] is True
    assert out["n_false_alarms"] == 0, out["false_alarms"]      # faithful texts not over-flagged
    # the suites are real: ≥5 absent-fact, a paraphrase suite, and every fabrication was caught
    assert out["absent_fact_count"] >= 5
    assert out["paraphrase_count"] >= 4
    assert out["caught"] == out["n_fabrications"]
    assert out["canary_valid"] is True                          # the wall is what earns the 0


# --- Demo D10 ---------------------------------------------------------------------
def test_d10_db_rows_to_grounded_sft_jsonl():
    out = run_d10()
    assert out["status"].startswith("DONE")
    assert out["n_rows"] == 3 and out["n_conversations"] == 2
    # zero generation: every turn is a verbatim source body, every turn grounded (E2 100%)
    assert out["zero_generation_verbatim"] is True
    assert out["e2_provenance_complete"] is True
    assert out["every_turn_grounded"] is True
    # role discrimination is real (an agent row → assistant)
    assert "assistant" in out["roles"] and "user" in out["roles"]
    # all three serialisations emitted + JSONL
    assert out["openai"] and out["sharegpt"] and out["alpaca"]
    assert out["openai_jsonl"].endswith(b"\n")
