"""Patient resolution — ranked registry candidates for a claimed subject.

**This module never decides who a patient is.** It answers a narrower question: "which registry
rows are worth showing to the person at the counter?" ``ResolutionResult.requires_human_selection``
is a ``Literal[True]``, so a result that considers identity settled cannot be constructed — not by
this module, not by a future one. Staff check the physical NRIC/FIN/passport against the person, in
person, and click a candidate afterwards (CLAUDE.md §Hard constraints 1).

That constraint is why every design choice below leans toward *showing more and deciding less*:

* A candidate whose details disagree with the document is kept and flagged (``conflicts``), not
  dropped. A dropped row is invisible; a flagged row is a question staff can answer in two seconds.
* A name-only match is returned even though it is weak — with a low score and an explicit note, so
  the card cannot present it as though an identifier corroborated it.
* Scores are additive and traceable: ``matched_on`` names exactly which signals fired, so the
  number on screen is arguable rather than oracular.

**Names match exactly after normalisation — no fuzzy tolerance.** This carries forward the Phase 1
rule in ``ground.py``: "Lim Arjuna" scores 0.96 against "Lim Arjun" under any reasonable fuzzy
metric, and they are different people. A near-miss name here would put a stranger's record in front
of staff with a plausible-looking score, which is worse than showing nothing.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Iterable, Optional, Sequence

from .ground import canonicalise_id
from .models import (PatientRecord, ResolutionCandidate, ResolutionResult, ResolutionStatus,
                     SubjectBlock)
from .clock import resolve_today
from .registry import parse_dob_two_digit_year, registry

# Signal weights (PLAN.md §5 Phase 2). They sum to 1.0: an exact identifier, a corroborating date
# of birth, the name, and demographic agreement is everything a referring document can offer.
W_ID_NUMBER = 0.6
W_DOB = 0.2
W_NAME = 0.15
W_DEMOGRAPHIC = 0.05

# At or above this, at least one *identifier* matched rather than just a name. Below it, the match
# rests on a name alone — which in this registry is routinely shared by two or three people — and
# the result carries an explicit note so the UI can style it as weak.
HIGH_CONFIDENCE_SCORE = W_ID_NUMBER

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

_SEX_SYNONYMS = {"m": "M", "male": "M", "f": "F", "female": "F"}


def _norm_name(s: str) -> str:
    """Casefold and collapse punctuation/whitespace — 's/o' spacing and double spaces differ
    between a printed letter and a CSV cell without meaning two different people."""
    return _NON_ALNUM.sub(" ", str(s).lower()).strip()


def _norm_sex(s: object) -> Optional[str]:
    """'Male'/'M'/'male' → 'M'. Returns ``None`` for anything unrecognised rather than guessing:
    an unparseable value is missing information, not a disagreement."""
    return _SEX_SYNONYMS.get(str(s).strip().lower()) if s is not None else None


def _norm_nationality(s: object) -> Optional[str]:
    cleaned = _norm_name(s) if s is not None else ""
    return cleaned or None


def parse_claimed_dob(raw: object, *, today: Optional[dt.date] = None) -> Optional[dt.date]:
    """Parse a date of birth as *printed on a document*, or return ``None``.

    The sample forms print ``DD/MM/YY`` (``20/05/77``), the same two-digit-year ambiguity the
    registry CSV has, so the century is resolved by the same pivot function — one rule, one place,
    rather than two implementations that can drift apart.

    Returning ``None`` on anything unparseable is deliberate: an unreadable date must not become a
    ``dob_mismatch`` conflict. Absence of information is not evidence of disagreement, and a
    spurious conflict badge on the summary card trains staff to ignore the real ones.
    """
    if raw is None:
        return None
    if isinstance(raw, dt.datetime):
        return raw.date()
    if isinstance(raw, dt.date):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    parts = re.split(r"[/\-.\s]+", text)
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        # A leading four-digit component means ISO order (1990-10-07); everything else the sample
        # forms print is day-first (20/05/77). Checked before the day-first branch, which would
        # otherwise read the year as the day and silently return None.
        if len(parts[0]) == 4:
            try:
                return dt.date(int(parts[0]), int(parts[1]), int(parts[2]))
            except ValueError:
                return None
        day, month, year = parts
        if len(year) == 2:
            try:
                return parse_dob_two_digit_year(f"{day}/{month}/{year}", today=today)
            except Exception:
                return None
        if len(year) == 4:
            try:
                return dt.date(int(year), int(month), int(day))
            except ValueError:
                return None
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


class _Claimed:
    """The identifying details a document asserts, normalised for comparison.

    Built from ``SubjectBlock`` only. Nothing here is treated as true — the names of the source
    fields all carry ``claimed_`` for exactly that reason.
    """

    __slots__ = ("name", "id_number", "dob", "sex", "nationality")

    def __init__(self, subject: SubjectBlock, *, today: dt.date) -> None:
        def val(gv):
            return gv.value if gv is not None else None

        raw_name = val(subject.claimed_name)
        raw_id = val(subject.claimed_id_number)
        self.name = _norm_name(raw_name) if raw_name else None
        self.id_number = canonicalise_id(raw_id) if raw_id else None
        self.dob = parse_claimed_dob(val(subject.claimed_dob), today=today)
        self.sex = _norm_sex(val(subject.claimed_sex))
        self.nationality = _norm_nationality(val(subject.claimed_nationality))

    @property
    def has_strong_identifier(self) -> bool:
        """Whether there is anything to search the registry *by*.

        Sex and nationality are filters, never search keys: 'Male, Singaporean' describes a third
        of the registry, and returning 100 candidates is indistinguishable from returning none.
        """
        return bool(self.name or self.id_number)


def _score(claimed: _Claimed, record: PatientRecord) -> tuple[float, list[str], list[str]]:
    """Score one registry row against the claimed details.

    Returns ``(score, matched_on, conflicts)``. A field the document does not state contributes
    neither a match nor a conflict — it is simply silent.
    """
    matched: list[str] = []
    conflicts: list[str] = []
    score = 0.0

    if claimed.id_number:
        if canonicalise_id(record.id_number.value) == claimed.id_number:
            score += W_ID_NUMBER
            matched.append("id_number")
        else:
            conflicts.append("id_number_mismatch")

    if claimed.name:
        if _norm_name(record.full_name.value) == claimed.name:
            score += W_NAME
            matched.append("name")
        else:
            conflicts.append("name_mismatch")

    if claimed.dob is not None:
        if record.date_of_birth.value == claimed.dob:
            score += W_DOB
            matched.append("dob")
        else:
            conflicts.append("dob_mismatch")

    # Sex and nationality share one weight because neither is discriminating on its own. The
    # weight is divided by how many demographic fields EXIST, not by how many this document
    # happens to state — so agreeing on two attributes scores strictly higher than agreeing on
    # one. Dividing by the stated count instead would let the thinnest possible document (sex
    # alone) earn the same corroboration as one carrying both, and the score is shown to staff and
    # compared against HIGH_CONFIDENCE_SCORE, so it has to mean "how much agrees" in absolute
    # terms rather than "what fraction of whatever was printed".
    demographics = [(claimed.sex, _norm_sex(record.sex.value), "sex"),
                    (claimed.nationality, _norm_nationality(record.nationality.value),
                     "nationality")]
    per_field = W_DEMOGRAPHIC / len(demographics)
    for claimed_val, record_val, label in demographics:
        if claimed_val is None:
            continue
        if record_val is None:
            # The REGISTRY value did not normalise (e.g. a Sex outside the known vocabulary).
            # That is missing information on our side, not disagreement with the document — the
            # same rule `_norm_sex` and `parse_claimed_dob` already follow. Recording a mismatch
            # here puts a conflict badge on the card that no human can act on, which is how staff
            # learn to ignore the real ones.
            continue
        if record_val == claimed_val:
            score += per_field
            matched.append(label)
        else:
            conflicts.append(f"{label}_mismatch")

    # Float addition of the weights overshoots 1.0 by ~2e-16, which trips the `le=1.0` bound on
    # ResolutionCandidate.score. Round rather than clamp so the failure mode is a visibly wrong
    # number if a weight is ever mis-set, not a silent ceiling.
    return round(score, 4), matched, conflicts


def resolve(subject: SubjectBlock,
            *,
            records: Optional[Sequence[PatientRecord]] = None,
            today: Optional[dt.date] = None) -> ResolutionResult:
    """Rank registry candidates for a claimed subject. **Returns candidates; never confirms.**

    Candidate selection, in one rule: *a positive identifier match is decisive.* When the document
    states an identifier and some registry row carries it, only rows carrying it are returned —
    that is what lets an NRIC disambiguate a three-way name collision (chit 1: three patients named
    Loh Wei Ming, one of whom has the ID on the letter). When the document states an identifier
    that matches *nobody*, same-name rows are returned instead, each flagged ``id_number_mismatch``
    — "the document names someone we have no such ID for, but here is a same-name patient" is
    useful to staff, and the alternative is showing an empty result for a patient who is in fact
    registered.

    With no identifier at all, name matches are returned unranked-by-anything-else and the result
    carries a note: two people sharing a name is routine in this registry (chits 3 and 5 both hit
    it), so a name-only match must never present as a confident one.
    """
    today = resolve_today(today)
    pool: Iterable[PatientRecord] = records if records is not None else registry(today)
    claimed = _Claimed(subject, today=today)

    if not claimed.has_strong_identifier:
        # The walk-in / self-pay path, and any document too thin to search by. Not an error: it
        # routes to registration-from-scratch, which is a normal counter workflow.
        return ResolutionResult(
            candidates=(), status=ResolutionStatus.INSUFFICIENT_IDENTIFIERS,
            notes=("no name or identifier on the document to search the registry by — "
                   "register this patient at the counter",))

    ordered = list(pool)
    id_matches = ([r for r in ordered
                   if claimed.id_number and canonicalise_id(r.id_number.value) == claimed.id_number]
                  if claimed.id_number else [])
    name_matches = ([r for r in ordered
                     if claimed.name and _norm_name(r.full_name.value) == claimed.name]
                    if claimed.name else [])

    notes: list[str] = []
    if id_matches:
        pool_rows = id_matches
        if claimed.name and len(name_matches) > len(id_matches):
            notes.append(
                f"{len(name_matches)} registry patients share this name; the identifier on the "
                f"document narrowed it to {len(id_matches)}")
    else:
        pool_rows = name_matches
        if claimed.id_number and name_matches:
            notes.append(
                "the identifier on the document matches no registry patient — the candidate(s) "
                "below match by name only and are flagged with the mismatch")

    scored = [(record, *_score(claimed, record)) for record in pool_rows]
    # Rank best-first. Ties keep registry order (the enumerate index) so the list is deterministic
    # across runs — two candidates at 0.15 must not swap places between page loads.
    ranked = sorted(enumerate(scored), key=lambda pair: (-pair[1][1], pair[0]))

    candidates = tuple(
        ResolutionCandidate(patient_id=record.patient_id, score=score,
                            matched_on=matched, conflicts=conflicts, record=record)
        for _, (record, score, matched, conflicts) in ranked)

    if not candidates:
        status = ResolutionStatus.NO_CANDIDATE
        notes.append("no registry patient matches the name or identifier on this document")
    elif len(candidates) == 1:
        status = ResolutionStatus.SINGLE_CANDIDATE
    else:
        status = ResolutionStatus.MULTIPLE_CANDIDATES
        # Only say "equally well" when they actually score the same. Candidates are ranked and can
        # differ (an identifier narrows to two rows, one of which also matches the date of birth);
        # claiming a tie next to a visibly ordered list contradicts what staff are looking at.
        tied = len({c.score for c in candidates}) == 1
        notes.append(
            f"{len(candidates)} patients match"
            + (" equally well" if tied else " (ranked; scores differ)")
            + " — staff must select the correct one after checking identity in person")

    if candidates and candidates[0].score < HIGH_CONFIDENCE_SCORE:
        notes.append(
            f"low-confidence match (best score {candidates[0].score:.2f} < "
            f"{HIGH_CONFIDENCE_SCORE:.2f}): no identifier on the document corroborates the name")

    return ResolutionResult(candidates=candidates, status=status, notes=tuple(notes))
