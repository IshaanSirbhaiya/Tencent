"""Independent claim verifier (C17, 09_FINAL_PRD §12.3b).

A SECOND model — a DIFFERENT alias than the generator (``verification_pass`` → ``sonnet_alt``,
per models.yaml; 07_PRD §2 adversarial-separation: an independent judge reduces correlated
failure) — reads the generated text + the fact sheet and judges each claim supported / unsupported.

This is the model-level backstop BEHIND the deterministic ``claim_check``: claim_check owns the
unambiguous numeric/date surface; the verifier catches semantic unsupport a string match can't —
a misattributed name, a real number applied to the wrong entity, an implied trend. Unsupported ⇒
the turn is quarantined with the reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel

from databridge_harness import Harness

from .fact_extractor import Fact

VERIFICATION_TASK = "verification_pass"


class ClaimVerdict(BaseModel):
    claim: str
    supported: bool
    reason: str = ""


class VerificationReport(BaseModel):
    verdicts: list[ClaimVerdict] = []


@dataclass
class VerificationResult:
    supported: bool
    verdicts: list[ClaimVerdict] = field(default_factory=list)
    model: str = ""

    @property
    def unsupported(self) -> list[ClaimVerdict]:
        return [v for v in self.verdicts if not v.supported]


def _facts_block(facts: list[Fact]) -> str:
    return "\n".join(f"- {f.label}: {f.rendered}" for f in facts)


def verify(harness: Harness, text: str, facts: list[Fact], *, seed: Optional[int] = 7,
           source_id: Optional[str] = None) -> VerificationResult:
    """Judge each claim in ``text`` against ``facts`` with the INDEPENDENT verifier model.
    ``supported`` is True only if every verdict is supported (an empty report ⇒ supported)."""
    msgs = [
        {"role": "system", "content": (
            "You are an independent fact verifier. For each factual claim in the text, decide if "
            "it is SUPPORTED by the supplied facts (exact values, correct entity). A number/date "
            "applied to the wrong entity, or any value not in the facts, is UNSUPPORTED. Return "
            "JSON {\"verdicts\": [{\"claim\": str, \"supported\": bool, \"reason\": str}]}.")},
        {"role": "user", "content": f"Facts:\n{_facts_block(facts)}\n\nText to verify:\n{text}"},
    ]
    res = harness.complete_structured(VERIFICATION_TASK, msgs, VerificationReport, seed=seed,
                                      source_id=source_id)
    report = res.parsed
    supported = all(v.supported for v in report.verdicts)
    return VerificationResult(supported=supported, verdicts=list(report.verdicts),
                              model=getattr(res.event, "model_used", "") if res.event else "")
