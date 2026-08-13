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

def _system_prompt() -> str:
    """Assembled from `packages/intake/.../config/assistant.yaml`, not hardcoded here.

    Config rather than code so swapping the provider (Azure OpenAI, for the Copilot Studio story)
    does not mean editing a Python string, and so the refusal rules are reviewable by someone who
    does not read Python. The rules are ALSO enforced deterministically in `guardrails.py`, which
    runs before this prompt is ever sent — a prompt is a request, the guardrail is the guarantee.
    """
    from databridge_intake.assistant import assistant_config

    return assistant_config().system_prompt()


def __getattr__(name: str):
    if name == "SYSTEM_PROMPT":
        return _system_prompt()
    raise AttributeError(name)


def detect_llm() -> dict:
    """Inspect env and decide the backend. Never returns the key itself.

    ``DATABRIDGE_FORCE_GOLD=1`` reports "no key" regardless of what is set. It lives HERE rather
    than at each call site because the first version guarded only the intake path, and the
    Playwright run then showed `LiteLLM completion() model= gpt-4o` while the e2e assertions ran:
    `/brain/query` builds its own harness and was never covered. One switch, at the point the
    decision is actually made, is the only version that cannot be half-applied.
    """
    if os.getenv("DATABRIDGE_FORCE_GOLD") == "1":
        return {"backend": "fake", "model": None, "api_key_present": False, "alias_model": None}
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


def build_extraction_harness(detected: dict):
    """A real Harness for the intake pipeline, or ``None`` to leave the gold backend in place.

    **Why this exists at all.** ``run_intake``'s harness argument defaults to
    ``gold_harness()`` — a ``FakeBackend`` that dispatches on a marker string unique to each of the
    ten chit fixtures and returns an EMPTY draft for anything else. That default is right for the
    library (tests and an offline demo must be deterministic) and wrong for the gateway, which is
    the one caller whose whole job is reading a document it has never seen. No call site passed a
    harness, so every upload — including a judge pasting their own referral letter — went through
    the fixture table and came back blank. Review found it; `CLAUDE.md` had said a real model runs
    whenever a key is present, and `models.yaml` had routed `intake_extraction` since Phase 1.

    **Why the alias is remapped.** `intake_extraction` routes to the ``sonnet`` alias, which names
    an Anthropic model. A deployment holding only an ``OPENAI_API_KEY`` cannot serve that alias, so
    the detected model is substituted on a DEEP COPY of the config — the same remap
    ``build_query_harness`` already performs for ``primary_llm``, applied to the alias the intake
    path actually uses. Routing stays configuration; this only picks the configured provider that
    is reachable. ``sonnet_alt`` is deliberately left alone: it exists to be a *different* model
    from the generator, and collapsing the two would quietly end that adversarial separation.
    """
    if detected["backend"] == "fake" or not detected.get("model"):
        return None
    from databridge_core.config import load_model_config
    from databridge_harness import Harness, LiteLLMBackend

    cfg = copy.deepcopy(load_model_config())
    cfg.setdefault("aliases", {}).setdefault("sonnet", {})["model"] = detected["model"]
    return Harness(LiteLLMBackend(), config=cfg, profile="cloud")


def _source_of(node) -> str:
    return node.sources[0].source_id if node.sources else "unknown"


def _candidate_phrase(count: Optional[int]) -> str:
    """Render a FOR_PATIENT edge's corroboration count for the LLM context.

    ``candidate_count`` is only populated by the clinic seed (``clinic_seed.py``'s ``_edge``
    calls, which always set it); a FOR_PATIENT edge minted by ``extract_graph`` (the live
    ``brain_ingest`` path) builds edges with empty ``attrs``, so ``count`` arrives here as
    ``None``. Printing that straight into the f-string produced literal text like
    ``"doc" (1 of None candidate(s) — UNVERIFIED)`` — a model reading that sees a specific,
    slightly malformed NUMBER rather than what it actually means, which is "we never counted
    at all". An unknown count must read as unknown, not as the string ``"None"`` sitting where a
    number belongs.
    """
    return f"1 of {count} candidate(s)" if count is not None else "an unknown number of candidates"


def build_context(brain, question: str, *, top_k: int = 8) -> dict:
    """Serialise the relevant subgraph as the LLM context block + return the cited nodes.

    Returns {context, nodes_used (ids), citations [{entity_id,label,kind,pointers}], candidates,
    refusal}. A non-empty ``refusal`` means NO context was built and the caller must not call a
    model — see the ambiguity block below.

    **This is the model-facing twin of ``query.nl_query``, and constraint 1 has to hold on BOTH
    paths.** `/brain/query` takes the LLM branch whenever a key is present and the deterministic
    branch otherwise, so a guarantee implemented in only one of them is a guarantee that
    disappears the moment a key is exported — which is precisely the configuration the product is
    demonstrated in.
    """
    from databridge_graph import EdgeKind, link_entities, neighbors
    from databridge_graph.identity import (
        ambiguous_refusal, identity_caveat, same_label_node_ids)
    from databridge_graph.query import _named_outright
    from databridge_graph.schema import NodeKind

    store = brain.store
    explicit = _named_outright(store, question)
    matches = link_entities(store, question, top_k=top_k)
    if not matches:
        return {"context": "", "nodes_used": [], "citations": [], "candidates": [], "refusal": ""}

    # **A NAME DOES NOT IDENTIFY A PERSON — on this path too.** `nl_query` refuses when the
    # top-ranked entity shares its label with others; this branch did not, and it is the worse
    # place for the omission: `top_k=8` puts ALL THREE "Loh Wei Ming" into one prompt and asks a
    # model to answer about "the patient", which delegates the choice between three people to the
    # thing least able to say it is not entitled to make it. Same rule, same wording, same way
    # forward — an id typed into the question wins outright (`_named_outright`), so the refusal
    # is a fork in the road rather than a wall.
    if explicit is None:
        shared = same_label_node_ids(store, matches[0].id)
        if len(shared) > 1:
            return {"context": "", "nodes_used": [], "citations": [],
                    "candidates": shared, "refusal": ambiguous_refusal(question, shared)}

    # collect each match + its 1-hop neighbourhood
    by_id: dict = {}
    edges_seen: list = []
    for m in matches:
        by_id[m.id] = m
        sub = neighbors(store, m.id, depth=1)
        for n in sub.nodes:
            by_id.setdefault(n.id, n)
        edges_seen.extend(sub.edges)

    lines = ["CLINIC BRAIN CONTEXT — Parkway Shenton", "=" * 38, "ENTITIES FOUND:"]
    for m in matches:
        n = by_id[m.id]
        head = n.label
        if n.kind == NodeKind.ORG and n.attrs.get("kind"):
            head = f"{n.label} ({n.attrs['kind']})"
            if n.attrs.get("issuer_code"):
                head += f" — {n.attrs['issuer_code']}"
        elif n.kind == NodeKind.PACKAGE and n.attrs.get("eligibility"):
            head = f"{n.label} ({n.attrs.get('name', '')}, {n.attrs['eligibility']})"
        elif n.kind == NodeKind.TEST and n.attrs.get("category"):
            head = f"{n.label} ({n.attrs['category']})"
        elif n.kind == NodeKind.COVERAGE_RULE and n.attrs.get("billed_to"):
            head = f"{n.label} (billed to {n.attrs['billed_to']})"
        lines.append(f"- {head}")
        # Documents that CLAIM to be about this patient. Rendered with the caveat inline because
        # this text goes straight into an LLM prompt — a bare "patient X, document Y" line invites
        # the model to narrate it as an established identity (CLAUDE.md §Hard constraints 1).
        # `src_id` must be a DOCUMENT. FOR_PATIENT also runs VISIT -> PATIENT, so an unguarded
        # read counts a visit as a document naming the patient — which both overstates the
        # corroboration and selects the *less* cautious caveat below.
        claims = [(by_id[e.src_id].label, e.attrs.get("candidate_count")) for e in edges_seen
                  if e.kind == EdgeKind.FOR_PATIENT and e.dst_id == n.id and e.src_id in by_id
                  and by_id[e.src_id].kind == NodeKind.DOCUMENT]
        if claims:
            rendered = "; ".join(
                f'"{label}" ({_candidate_phrase(count)} — UNVERIFIED)' for label, count in claims[:4])
            lines.append("    Named by (claimed, not verified): " + rendered)
        # THE CAVEAT IS KEYED ON THE KIND, NEVER ON `claims`. It used to sit under `if claims:`,
        # which was invisible while every seeded patient had a document naming them; seeding the
        # whole registry left 289 patients edge-less, and each of those went into the prompt as a
        # bare name and a date of birth. Registry presence is not identification — and this is the
        # text a model paraphrases back to staff (CLAUDE.md §Hard constraints 1).
        if n.kind == NodeKind.PATIENT:
            lines.append(f"    IDENTITY: {identity_caveat(claimed=bool(claims))}")
        lines.append(f"    Source: {_source_of(n)}")

    rels = []
    seen_rel = set()
    for e in edges_seen:
        if e.src_id in by_id and e.dst_id in by_id:
            key = (e.src_id, e.kind.value, e.dst_id)
            if key in seen_rel:
                continue
            seen_rel.add(key)
            line = (f"- {by_id[e.src_id].label} --[{e.kind.value.upper()}]--> "
                    f"{by_id[e.dst_id].label}")
            # A FOR_PATIENT edge must NEVER reach the model as a bare arrow. The per-entity caveat
            # above only covers nodes that were themselves matched; a patient reached through the
            # 1-hop neighbourhood arrives here instead, and "document --[FOR_PATIENT]--> Loh Wei
            # Ming" is precisely the line that gets narrated back as an established identity.
            if e.kind == EdgeKind.FOR_PATIENT:
                count = e.attrs.get("candidate_count")
                line += (f"  [CLAIMED ON DOCUMENT — UNVERIFIED; {_candidate_phrase(count)}; "
                         f"identity is checked in person by staff]")
            rels.append(line)
    if rels:
        lines.append("")
        lines.append("RELATIONSHIPS:")
        lines.extend(rels[:20])
    lines.append("=" * 35)

    citations = [{
        "entity_id": m.id, "label": m.label, "kind": m.kind.value,
        "pointers": [p.model_dump(mode="json", exclude_none=True) for p in m.sources],
    } for m in matches]
    return {"context": "\n".join(lines), "nodes_used": [m.id for m in matches], "citations": citations, "candidates": [], "refusal": ""}
