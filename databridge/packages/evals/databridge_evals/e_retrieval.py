"""EV-RETRIEVAL — does entity linking find the right thing, and does it decline when it should?

`PLAN.md` §6.5. This is an **eval, not an assertion suite**: it reports a score over a fixed
question set. Two rules that make the number mean anything:

1. **Report the score; do not tune to it.** `MIN_SCORE` in `retrieval.py` is a floor chosen on
   principle, not fitted until this passes.
2. **Changing `PAIRS` is a reviewed change, not a fix.** Deleting a question the ranker fails is how
   an eval becomes decoration.

The half that matters most is `expected=None` — the questions retrieval must answer with *nothing*.
The previous scorer had no floor at all and always returned its top-k, which is why the identity
guardrail had to catch "what is the meaning of life" downstream: retrieval should have declined it.
"""
from __future__ import annotations

from dataclasses import dataclass

from databridge_graph.clinic_seed import fresh_clinic_store
from databridge_graph.query import link_entities

# (question, expected node id | None). `None` means "retrieval must return nothing".
#
# The out-of-domain half is drawn from real false positives recorded in STATUS.md Gotchas plus the
# obvious neighbours of each — a question set containing only the one bug we already found would
# prove very little.
PAIRS: list[tuple[str, str | None]] = [
    # --- should find a specific entity ---------------------------------------------------
    ("What does the WELL2 package include?", "package:blphs-well2"),
    ("What is in WELL1?", "package:blphs-well1"),
    ("What does PEE226 cover?", "package:mol0199vme-pee226"),
    ("Tell me about the PEE224 package", "package:mol0199vme-pee224"),
    ("Which patients does Meridian Life Assurance cover?", "org:meridian-life-assurance"),
    ("What is Northstar Life Assurance?", "org:northstar-life-assurance"),
    ("Everwell Insurance Group", "org:everwell-insurance-group"),
    ("Is a chest x-ray covered?", "test:chest_xray"),
    ("full blood count", "test:full_blood_count"),
    # A DELIBERATE, REPORTED MISS. The node is labelled with the acronym "UFEME"; this is exact
    # token matching, so it cannot bridge acronym→expansion. Kept in the set because pretending the
    # limitation does not exist is worse than scoring 0.95 instead of 1.00. Fixing it means a
    # synonym table in config — not a change to the scorer.
    ("What is a urine examination and microscopy?", "test:ufeme"),

    # --- must return NOTHING: out of domain, or only incidentally sharing a word ----------
    # The recorded false positive: matched four "…Life Assurance" insurers by substring.
    ("What is the meaning of life?", None),
    ("What is the meaning of life and death?", None),
    ("How do I get life insurance for my dog?", None),
    ("What is the weather today?", None),
    ("Tell me a joke", None),
    ("Who won the football on Saturday?", None),
    # "group" appears in an insurer name; "well" is a prefix of the WELL packages but not a token.
    ("Is the group well?", None),
    ("How well did we do?", None),
    ("What time does the assurance office open in Tokyo?", None),
    ("Recommend a good restaurant near the clinic", None),
]


@dataclass
class RetrievalResult:
    total: int
    correct: int
    found_wrong: list[tuple[str, str | None, list[str]]]
    score: float
    precision_in_domain: float
    decline_rate_out_of_domain: float


def run_eval() -> RetrievalResult:
    store = fresh_clinic_store()

    correct = 0
    wrong: list[tuple[str, str | None, list[str]]] = []
    in_dom_total = in_dom_ok = 0
    out_total = out_ok = 0

    for question, expected in PAIRS:
        got = [n.id for n in link_entities(store, question, top_k=3)]
        if expected is None:
            out_total += 1
            ok = not got
            if ok:
                out_ok += 1
        else:
            in_dom_total += 1
            # Top-3 containment: the answer builder gets a small subgraph, so the right entity
            # being present is what matters, not it being rank 1.
            ok = expected in got
            if ok:
                in_dom_ok += 1
        if ok:
            correct += 1
        else:
            wrong.append((question, expected, got))

    total = len(PAIRS)
    return RetrievalResult(
        total=total,
        correct=correct,
        found_wrong=wrong,
        score=correct / total if total else 0.0,
        precision_in_domain=in_dom_ok / in_dom_total if in_dom_total else 0.0,
        decline_rate_out_of_domain=out_ok / out_total if out_total else 0.0,
    )


if __name__ == "__main__":                                   # pragma: no cover
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    r = run_eval()
    print(f"EV-RETRIEVAL  {r.correct}/{r.total} = {r.score:.2f}")
    print(f"  in-domain recall@3      {r.precision_in_domain:.2f}")
    print(f"  out-of-domain declined  {r.decline_rate_out_of_domain:.2f}")
    for q, exp, got in r.found_wrong:
        print(f"  MISS  {q!r}\n        expected={exp!r}\n        got={got!r}")
