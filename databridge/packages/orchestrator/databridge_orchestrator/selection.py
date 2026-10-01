"""Which adapters get asked — from the **declared capability matrix**, never from a model.

The brief in `apps/gateway/databridge_gateway/orchestrator_routes.py` is explicit about why: a
Shell sees only its own adapter's description (`shell.py` §9a — accuracy degrades past 30-50
tools), so *something* has to decide which adapters are worth asking before any Shell runs. That
decision must not become the thing §9a was written to prevent — a model handed the whole fleet's
tool definitions at once — and it must not be made from content a source returned, because source
SELECTION is one of the taint sinks `databridge_adapter.taint` names.

So this module is deterministic and reads only `SourceDescription` — the schema, capability set and
field names a source itself advertises via `describe()`. No values, no counts, no samples ever
reach it (the same guarantee `AdapterShell.describe_for_model` makes one level down). Where a model
could help — ranking a shortlist a human would also produce — it may RANK; it may never CONJURE a
source this function did not already consider, because `select_sources` only ever returns items
from its own input list.

This build does not call a model here at all: keyword overlap between the question and the
declared entity/field vocabulary is enough to route a demo-sized fleet (a handful of sources), it
costs nothing, and it is the same decision a human skimming `describe()` output would make. A
larger fleet is exactly the `rank or shortlist declared options` case the brief allows for, and the
extension point is `select_sources`'s return value — swap the scoring, keep the closed-set
contract.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from databridge_adapter import SourceDescription

_WORD = re.compile(r"[a-z0-9]+")

#: Below this length two tokens are compared only for equality — "id", "to", "at" would otherwise
#: substring-match almost anything and turn every question into a match for every source.
_MIN_SUBSTRING_LEN = 4


def tokenize(text: str) -> frozenset[str]:
    """Lowercase word tokens. The same normalisation on both sides of every comparison here."""
    return frozenset(_WORD.findall((text or "").lower()))


def _entity_vocabulary(entity) -> frozenset[str]:
    words: set[str] = set(tokenize(entity.name))
    for f in entity.fields:
        words |= tokenize(f.name)
        words |= tokenize(f.type)
    return frozenset(words)


def source_vocabulary(description: "SourceDescription") -> frozenset[str]:
    """Every word `describe()` volunteers about this source: id, title, kind, entities, fields.

    Never a value. `SourceDescription` structurally cannot carry one (`describe.py`'s whole
    argument), so there is nothing here to screen even in principle.
    """
    words: set[str] = set()
    words |= tokenize(description.source_id)
    words |= tokenize(getattr(description, "title", "") or "")
    words |= tokenize(getattr(description, "kind", "") or "")
    for entity in description.entities:
        words |= _entity_vocabulary(entity)
    return frozenset(words)


def overlap_score(question_tokens: frozenset[str], vocabulary: frozenset[str]) -> int:
    """Exact-token matches count once; a longer word containing a shorter one counts as a weaker
    signal, so "message" in a question still favours a source describing "messages" or "message_id"
    without requiring the caller to guess the source's exact pluralisation."""
    score = len(question_tokens & vocabulary)
    for qt in question_tokens:
        if len(qt) < _MIN_SUBSTRING_LEN:
            continue
        for vt in vocabulary:
            if qt == vt or len(vt) < _MIN_SUBSTRING_LEN:
                continue
            if qt in vt or vt in qt:
                score += 1
    return score


def select_sources(question: str, handles: Sequence, *, max_sources: int = 4
                   ) -> tuple[list, str]:
    """Score every candidate against its OWN declared vocabulary; return the shortlist and why.

    ``handles`` is any sequence of objects with a ``.source_id`` and a ``.description``
    (`orchestrator.AdapterHandle` satisfies this; the type is left unannotated so this module does
    not need to import the orchestrator module that imports it). Ties break on ``source_id`` so the
    order is reproducible across runs, which matters for a trace an auditor re-reads later.

    Never returns an item this call was not handed — that is the whole safety property source
    selection needs (`taint.py`: source selection is a control-flow position, and nothing outside
    this closed input may steer it).
    """
    tokens = tokenize(question)
    scored = sorted(
        ((overlap_score(tokens, source_vocabulary(h.description)), h.source_id, h)
         for h in handles),
        key=lambda t: (-t[0], t[1]),
    )
    positive = [h for score, _, h in scored if score > 0]
    if positive:
        chosen = positive[:max_sources]
        omitted = len(handles) - len(chosen)
        note = (
            f"selected {[h.source_id for h in chosen]} by keyword overlap between the question "
            f"and each source's OWN declared entity/field names (describe()); {omitted} source(s) "
            f"not selected on that basis" if omitted > 0 else
            f"selected {[h.source_id for h in chosen]} by keyword overlap with the declared "
            f"capability matrix")
    else:
        chosen = [h for _, _, h in scored[:max_sources]]
        note = (
            f"no declared entity or field name in any of the {len(handles)} available source(s) "
            f"overlapped the question's words; considering all {len(chosen)} of them rather than "
            f"answering none")
    return chosen, note
