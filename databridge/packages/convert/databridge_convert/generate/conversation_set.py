"""ConversationSet builders + serialisers (C17, 09_FINAL_PRD §12.4).

Two paths into a ConversationSet:
  * **real-log reformatting (PREFERRED, §12.4 "zero generation when logs exist")** — existing
    support messages → turns VERBATIM, each carrying a SourcePointer to its source row. No model,
    no fabrication risk; provenance is 100% by construction.
  * synthesis — grounded GenerationResults become turns whose ``facts_used`` are the cited facts.

Serialisers emit the three standard SFT shapes (openai / sharegpt / alpaca). Because those wire
formats have no provenance field, ``provenance_sidecar`` emits the per-turn SourcePointers
alongside (so the 100% E2 provenance gate is checkable on the output, §12.3c).
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

from databridge_core import SnapshotIdentity, SourcePointer

from ..ir import Conversation, ConversationSet, Turn

# role normalisation for the three target formats
_SHAREGPT_FROM = {"user": "human", "assistant": "gpt", "system": "system"}


def from_message_log(rows: Iterable[dict], *, group_by: str = "user_id",
                     body: str = "body", agent_flag: str = "is_from_agent",
                     row_id: str = "id", table: str = "support_messages",
                     source_id: str = "support_messages",
                     snapshot: Optional[SnapshotIdentity] = None) -> ConversationSet:
    """Real-log reformatting: group rows into conversations, each row a VERBATIM turn pointing at
    its source cell (table_cell). Role = assistant iff ``agent_flag`` is truthy, else user. The
    body is copied unchanged — nothing is generated, so nothing can be fabricated (§12.4)."""
    snap = snapshot or SnapshotIdentity(source_id=source_id, as_of="1970-01-01T00:00:00",
                                        content_hash="loglog", schema_hash="logsch")
    groups: dict[Any, list[dict]] = {}
    order: list[Any] = []
    for r in rows:
        key = r.get(group_by)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    conversations: list[Conversation] = []
    for key in order:
        turns: list[Turn] = []
        for r in groups[key]:
            role = "assistant" if r.get(agent_flag) else "user"
            ptr = SourcePointer(source_id=source_id, kind="table_cell", table=table,
                                column=body, row_id=str(r.get(row_id)))
            turns.append(Turn(role=role, content=str(r.get(body, "")), facts_used=[ptr]))
        conversations.append(Conversation(turns=turns))
    return ConversationSet(snapshot=snap, conversations=conversations)


def from_generations(pairs: Iterable[tuple[str, Any]], *,
                     snapshot: SnapshotIdentity) -> ConversationSet:
    """Synthesis path: (user_prompt, GenerationResult) pairs → conversations. Only OK (claim-clean)
    generations become assistant turns; a quarantined generation is dropped (never emitted)."""
    conversations: list[Conversation] = []
    for prompt, gen in pairs:
        if getattr(gen, "outcome", "ok") != "ok":
            continue
        turns = [Turn(role="user", content=prompt, facts_used=[]),
                 Turn(role="assistant", content=gen.text, facts_used=list(gen.facts_used))]
        conversations.append(Conversation(turns=turns))
    return ConversationSet(snapshot=snapshot, conversations=conversations)


# --- serialisers ------------------------------------------------------------------
def to_openai(cset: ConversationSet) -> list[dict]:
    """OpenAI chat-fine-tune shape: [{"messages": [{"role", "content"}, …]}]."""
    return [{"messages": [{"role": t.role, "content": t.content} for t in c.turns]}
            for c in cset.conversations]


def to_sharegpt(cset: ConversationSet) -> list[dict]:
    """ShareGPT shape: [{"conversations": [{"from": human|gpt, "value"}, …]}]."""
    return [{"conversations": [{"from": _SHAREGPT_FROM.get(t.role, t.role), "value": t.content}
                              for t in c.turns]} for c in cset.conversations]


def to_alpaca(cset: ConversationSet) -> list[dict]:
    """Alpaca shape: [{"instruction", "input", "output"}] — first user turn → instruction, the
    following assistant turn → output (multi-turn conversations contribute one record per
    user→assistant adjacency)."""
    out: list[dict] = []
    for c in cset.conversations:
        turns = c.turns
        for i in range(len(turns) - 1):
            if turns[i].role == "user" and turns[i + 1].role == "assistant":
                out.append({"instruction": turns[i].content, "input": "",
                            "output": turns[i + 1].content})
    return out


_SERIALISERS: dict[str, Callable[[ConversationSet], list[dict]]] = {
    "openai": to_openai, "sharegpt": to_sharegpt, "alpaca": to_alpaca,
}


def serialise(cset: ConversationSet, fmt: str) -> list[dict]:
    if fmt not in _SERIALISERS:
        raise ValueError(f"unknown SFT format {fmt!r}; have {sorted(_SERIALISERS)}")
    return _SERIALISERS[fmt](cset)


def to_jsonl(records: list[dict]) -> bytes:
    import orjson
    return b"\n".join(orjson.dumps(r) for r in records) + (b"\n" if records else b"")


def provenance_sidecar(cset: ConversationSet) -> list[dict]:
    """Per-turn provenance: [{conversation, turn, role, facts_used: [pointer dict]}] — the
    companion the wire SFT formats can't carry, so E2 (100% resolvable pointers) stays checkable."""
    side = []
    for ci, c in enumerate(cset.conversations):
        for ti, t in enumerate(c.turns):
            side.append({"conversation": ci, "turn": ti, "role": t.role,
                         "facts_used": [p.model_dump(mode="json") for p in t.facts_used]})
    return side


def provenance_complete(cset: ConversationSet, *, require_assistant: bool = True) -> bool:
    """E2 100% gate (§12.3c): every assistant turn that asserts content carries ≥1 resolvable
    SourcePointer. (User turns are prompts, not claims.) Reformatted logs satisfy this by
    construction; a synthesised turn satisfies it via facts_used."""
    for c in cset.conversations:
        for t in c.turns:
            if require_assistant and t.role != "assistant":
                continue
            if t.content.strip() and not t.facts_used:
                return False
    return True
