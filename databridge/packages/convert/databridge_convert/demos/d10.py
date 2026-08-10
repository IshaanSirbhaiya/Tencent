"""Demo D10 (09_FINAL_PRD §27): Database rows → grounded SFT JSONL.

Takes the ``support_messages`` table from the fintech fixture and reformats it into a
ConversationSet via the **real-log path** (§12.4 "zero generation when logs exist") — each message
becomes a VERBATIM turn carrying a SourcePointer to its source row, grouped into a conversation per
user. No model is called: there is nothing to fabricate, and E2 provenance is 100% by construction.

Then it serialises to all three SFT shapes (openai / sharegpt / alpaca) + a provenance sidecar.
The synthesis path's verifier stack (claim_check + verifier) is exercised separately by EV-GF on a
smaller synthetic example — here the win is the grounded, deterministic reformatting.
"""
from __future__ import annotations

from sqlalchemy import text

from .d9 import fintech_sqlite_engine
from ..generate.conversation_set import (
    from_message_log,
    provenance_complete,
    provenance_sidecar,
    serialise,
    to_jsonl,
)

_QUERY = ("SELECT id, user_id, body, sent_at, is_from_agent, channel "
          "FROM support_messages ORDER BY id")


def run_d10() -> dict:
    engine = fintech_sqlite_engine()
    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(text(_QUERY))]

    cset = from_message_log(rows)                       # real-log reformatting (no generation)

    openai = serialise(cset, "openai")
    sharegpt = serialise(cset, "sharegpt")
    alpaca = serialise(cset, "alpaca")
    sidecar = provenance_sidecar(cset)

    # every assistant turn traces to its source row (E2 100%, §12.3c)
    e2_complete = provenance_complete(cset)
    # zero generation: every emitted turn's content is a verbatim source body
    bodies = {str(r["body"]) for r in rows}
    verbatim = all(t.content in bodies for c in cset.conversations for t in c.turns)
    # the assistant turn really points at the agent row (role discrimination is real, not vacuous)
    roles = [t.role for c in cset.conversations for t in c.turns]

    return {
        "status": "DONE — DB rows → grounded SFT JSONL (real-log path, zero generation)",
        "n_rows": len(rows),
        "n_conversations": len(cset.conversations),
        "roles": roles,
        "e2_provenance_complete": e2_complete,
        "zero_generation_verbatim": verbatim,
        "openai": openai,
        "sharegpt": sharegpt,
        "alpaca": alpaca,
        "openai_jsonl": to_jsonl(openai),
        "provenance_sidecar": sidecar,
        "every_turn_grounded": all(
            len(s["facts_used"]) >= 1 for s in sidecar),
    }
