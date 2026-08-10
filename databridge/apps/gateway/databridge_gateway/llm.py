"""Real-LLM wiring for the Company-Brain /brain/query (T08, 16.5d Part 1).

Auto-detects the backend from env (OPENAI_API_KEY → gpt-4o · ANTHROPIC_API_KEY → claude-sonnet-4-6
· else FakeBackend). When a key is present, /brain/query serialises the relevant subgraph as
context and asks the LLM to answer using ONLY that context (sources labelled, analysis labelled,
nothing invented). With NO key, the gateway falls back to the deterministic claim-checked
``nl_query`` (airgapped CI/demo) — every grounded answer still works, just un-narrated.

No vendor SDK is imported here — the only LiteLLM import lives behind ``LiteLLMBackend`` (P2).
"""
from __future__ import annotations

import copy
import os
from typing import Optional

SYSTEM_PROMPT = (
    "You are a business intelligence assistant for Acme Corp. Answer questions using ONLY the graph "
    "context provided below. Label every fact with its source document. Label any inference or "
    "interpretation with a leading [ANALYSIS] tag. Never invent information that is not in the "
    "context. If the answer is not in the graph, say so clearly and briefly."
)


def detect_llm() -> dict:
    """Inspect env and decide the backend. Never returns the key itself."""
    if os.getenv("OPENAI_API_KEY"):
        return {"backend": "openai", "model": "openai/gpt-4o", "api_key_present": True,
                "alias_model": None}
    if os.getenv("ANTHROPIC_API_KEY"):
        return {"backend": "anthropic", "model": "anthropic/claude-sonnet-4-6",
                "api_key_present": True, "alias_model": "anthropic/claude-sonnet-4-6"}
    return {"backend": "fake", "model": None, "api_key_present": False, "alias_model": None}


def build_query_harness(detected: dict):
    """A real LiteLLM Harness (cloud profile) when a key is present, else None (use the fallback).
    For Anthropic we remap the ``primary_llm`` alias on a COPY of the config (never mutate cache)."""
    if detected["backend"] == "fake":
        return None
    from databridge_core.config import load_model_config
    from databridge_harness import Harness, LiteLLMBackend

    cfg = copy.deepcopy(load_model_config())
    if detected["alias_model"]:
        cfg.setdefault("aliases", {}).setdefault("primary_llm", {})["model"] = detected["alias_model"]
    return Harness(LiteLLMBackend(), config=cfg, profile="cloud")


def _source_of(node) -> str:
    return node.sources[0].source_id if node.sources else "unknown"


def build_context(brain, question: str, *, top_k: int = 8) -> dict:
    """Serialise the relevant subgraph as the LLM context block + return the cited nodes.

    Returns {context, nodes_used (ids), citations [{entity_id,label,kind,pointers}]}.
    """
    from databridge_graph import EdgeKind, link_entities, neighbors
    from databridge_graph.schema import NodeKind

    store = brain.store
    matches = link_entities(store, question, top_k=top_k)
    if not matches:
        return {"context": "", "nodes_used": [], "citations": []}

    # collect each match + its 1-hop neighbourhood
    by_id: dict = {}
    edges_seen: list = []
    for m in matches:
        by_id[m.id] = m
        sub = neighbors(store, m.id, depth=1)
        for n in sub.nodes:
            by_id.setdefault(n.id, n)
        edges_seen.extend(sub.edges)

    lines = ["COMPANY BRAIN CONTEXT — Acme Corp", "=" * 35, "ENTITIES FOUND:"]
    for m in matches:
        n = by_id[m.id]
        head = n.label
        if n.kind == NodeKind.PERSON and n.attrs.get("role"):
            head = f"{n.label} ({n.attrs['role']})"
            if n.attrs.get("email"):
                head += f" — {n.attrs['email']}"
        elif n.kind == NodeKind.FACT and n.attrs.get("rendered"):
            head = f"{n.attrs.get('claim', n.label)}: {n.attrs['rendered']}"
        elif n.kind in (NodeKind.PROJECT, NodeKind.ORG) and n.attrs.get("status", n.attrs.get("type")):
            head = f"{n.label} ({n.attrs.get('status', n.attrs.get('type'))})"
        lines.append(f"- {head}")
        # decisions made (person)
        decs = [by_id[e.dst_id].label for e in edges_seen
                if e.kind == EdgeKind.DECIDED and e.src_id == n.id and e.dst_id in by_id]
        if decs:
            lines.append("    Decisions: " + "; ".join(f'"{d}"' for d in decs[:4]))
        lines.append(f"    Source: {_source_of(n)}")

    rels = []
    seen_rel = set()
    for e in edges_seen:
        if e.src_id in by_id and e.dst_id in by_id:
            key = (e.src_id, e.kind.value, e.dst_id)
            if key in seen_rel:
                continue
            seen_rel.add(key)
            rels.append(f"- {by_id[e.src_id].label} --[{e.kind.value.upper()}]--> {by_id[e.dst_id].label}")
    if rels:
        lines.append("")
        lines.append("RELATIONSHIPS:")
        lines.extend(rels[:20])
    lines.append("=" * 35)

    citations = [{
        "entity_id": m.id, "label": m.label, "kind": m.kind.value,
        "pointers": [p.model_dump(mode="json", exclude_none=True) for p in m.sources],
    } for m in matches]
    return {"context": "\n".join(lines), "nodes_used": [m.id for m in matches], "citations": citations}
