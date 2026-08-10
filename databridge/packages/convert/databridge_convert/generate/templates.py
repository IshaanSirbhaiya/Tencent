"""Template library (C17, 09_FINAL_PRD §12.2) — versioned artefacts.

Generation is template-FIRST: pick a template, slot-fill deterministic fields, then the LLM
writes only the connective prose. Templates are versioned so a generated artefact records exactly
which template+version produced it (reproducibility; a template change is a visible diff).

Each template declares its ``fallback`` — the designated "data not available" / "let me check"
text the synthesiser emits when a referenced fact is absent (never a value, §12.2).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Template:
    template_id: str
    version: str
    description: str                      # shown to the LLM for selection
    instruction: str                      # the style/shape guidance for the prose
    fallback: str = "I don't have that information in the source data."
    tone: str = "neutral"

    @property
    def ref(self) -> str:
        return f"{self.template_id}@{self.version}"


# The shipped library. Add a template = add a versioned entry; bump version = a new artefact.
_LIBRARY: dict[str, Template] = {}


def register(t: Template) -> Template:
    _LIBRARY[t.template_id] = t
    return t


register(Template(
    template_id="metric_statement", version="1",
    description="A one-sentence factual statement of a metric or total (reports, summaries).",
    instruction=("Write ONE concise sentence stating the metric. Use only the rendered fact "
                 "values given, verbatim. No adjectives that imply a trend you weren't given."),
))
register(Template(
    template_id="support_reply", version="1",
    description="A short, polite customer-support reply that cites order/amount/date facts.",
    instruction=("Write a brief, polite support reply (1–3 sentences). Use only the rendered fact "
                 "values verbatim. If the customer asks about something not in the facts, use the "
                 "fallback line — never guess a value."),
    tone="polite",
))
register(Template(
    template_id="record_narration", version="1",
    description="A neutral narration of a single data record (row → prose for SFT).",
    instruction=("Describe the record in 1–2 plain sentences using only the rendered fact values "
                 "verbatim. Do not add, estimate, or round any number."),
))


def get_template(template_id: str) -> Template:
    if template_id not in _LIBRARY:
        raise KeyError(f"no template {template_id!r}; have {sorted(_LIBRARY)}")
    return _LIBRARY[template_id]


def library() -> list[Template]:
    return list(_LIBRARY.values())


def default_template() -> Template:
    return _LIBRARY["record_narration"]
