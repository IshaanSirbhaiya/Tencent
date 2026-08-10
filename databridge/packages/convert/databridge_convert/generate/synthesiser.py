"""Template-first grounded synthesis (C17, 09_FINAL_PRD §12.2).

Order: (1) pick a template (LLM, ``template_selection``); (2) slot-fill the rendered facts;
(3) the LLM composes connective prose under a hard contract — use ONLY the rendered fact values
verbatim, and for any referenced-but-absent fact write the template's FALLBACK line, never a value.

The output then passes the deterministic ``claim_check`` (§12.3a). **No generated text is emitted
as a valid turn unless claim_check is clean** — so even if the model fabricates, the fabrication
is QUARANTINED, never shipped. That is the "0 fabrications escaping" guarantee (EV-GF): the model
is not trusted; the wall is.

``facts_used`` per turn = the pointers of the facts whose canonical form actually appears in the
text (the citation contract).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel

from databridge_core import SourcePointer
from databridge_harness import Harness

from .claim_check import ClaimCheckResult, check_claims
from .fact_extractor import Fact
from .templates import Template, default_template, get_template, library

GENERATION_TASK = "data_to_text"
SELECTION_TASK = "template_selection"


class _TemplateChoice(BaseModel):
    template_id: str


def _facts_block(facts: list[Fact]) -> str:
    """The fact sheet handed to the model: id, label, and the CANONICAL rendered value to copy."""
    return "\n".join(f"{f.fid} ({f.label}): {f.rendered}" for f in facts)


def _cited(form: str, text: str) -> bool:
    """Boundary-aware 'is this fact form cited?': the fact '8.0%' must NOT count as cited inside a
    fabricated '18.0%' (a raw substring test would falsely attribute it — review MEDIUM)."""
    if not form:
        return False
    start = 0
    while True:
        i = text.find(form, start)
        if i < 0:
            return False
        j = i + len(form)
        if (i == 0 or not text[i - 1].isdigit()) and (j >= len(text) or not text[j].isdigit()):
            return True
        start = i + 1


def select_template(harness: Harness, facts: list[Fact], *, question: Optional[str] = None,
                    seed: Optional[int] = 7, source_id: Optional[str] = None) -> Template:
    """LLM picks a template from the library by description. Falls back to the default on an
    unknown id (never crash a generation on a bad pick)."""
    opts = "\n".join(f"- {t.template_id}: {t.description}" for t in library())
    msgs = [
        {"role": "system", "content": "Choose the single best template id for the task. "
                                      "Return JSON {\"template_id\": <id>}."},
        {"role": "user", "content": f"Templates:\n{opts}\n\nTask: {question or 'narrate the record'}\n"
                                    f"Facts available: {_facts_block(facts)}"},
    ]
    try:
        res = harness.complete_structured(SELECTION_TASK, msgs, _TemplateChoice, seed=seed,
                                          source_id=source_id)
        return get_template(res.parsed.template_id)
    except (KeyError, Exception):                       # unknown id / model hiccup → safe default
        return default_template()


@dataclass
class GenerationResult:
    text: str
    template_ref: str
    facts_used: list[SourcePointer] = field(default_factory=list)
    claim_check: Optional[ClaimCheckResult] = None
    outcome: str = "ok"                                 # "ok" | "quarantined"
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome == "ok"


def _system_prompt(template: Template) -> str:
    return (
        "You generate text whose every fact is SUPPLIED, never invented.\n"
        f"Style: {template.instruction}\n"
        "RULES:\n"
        "1. Use ONLY the rendered fact values given, copied VERBATIM (exact spelling, exact digits).\n"
        "2. Never round, estimate, hedge ('about', 'approximately'), or add a number not in the facts.\n"
        f"3. If you must refer to a fact that is NOT in the list, write exactly: \"{template.fallback}\"\n"
        "4. No commentary about these rules; output only the text."
    )


def synthesise(harness: Harness, facts: list[Fact], *, template: Optional[Template] = None,
               question: Optional[str] = None, select: bool = False, seed: Optional[int] = 7,
               source_id: Optional[str] = None) -> GenerationResult:
    """Generate one grounded text from ``facts``. Returns ok only if claim_check is clean —
    otherwise the text is QUARANTINED (a fabrication never escapes as a valid turn)."""
    if template is None:
        template = select_template(harness, facts, question=question, seed=seed,
                                   source_id=source_id) if select else default_template()

    user = f"Facts:\n{_facts_block(facts)}"
    if question:
        user += f"\n\nTask / question: {question}"
    msgs = [{"role": "system", "content": _system_prompt(template)},
            {"role": "user", "content": user}]
    text = harness.complete_text(GENERATION_TASK, msgs, seed=seed, source_id=source_id).text.strip()

    cc = check_claims(text, facts)
    used = [f.pointer for f in facts if _cited(f.rendered, text)]
    if not cc.ok:
        return GenerationResult(text=text, template_ref=template.ref, facts_used=used,
                                claim_check=cc, outcome="quarantined",
                                reason="claim_check failed: " +
                                       "; ".join(f"{v.kind}:{v.token}" for v in cc.violations))
    return GenerationResult(text=text, template_ref=template.ref, facts_used=used,
                            claim_check=cc, outcome="ok")
