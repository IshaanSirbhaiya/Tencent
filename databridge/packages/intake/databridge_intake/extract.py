"""LLM extraction → grounded ``IntakeRecord``.

One prompt and one schema for every document type. A referral letter, a voucher, an underwriting
memo, a government authorisation form and an HR email all fill in the same flat draft; which
fields come back populated is what differs. This is the whole anti-hardcoding stance applied to
extraction: there is no per-issuer prompt, no per-template parser, and adding an insurer requires
no change here at all.

The pipeline is deliberately two-stage:

    model → IntakeDraft  (syntax: what shape is the answer?)
    wall  → IntakeRecord (semantics: is any of it actually on the page?)

Nothing the model says is trusted. Every value is re-located in the source text by
``ground.py`` before it reaches the record; values that cannot be found are dropped into
``refused`` and their paths into ``unresolved``. So the failure mode of the whole component is
"the front desk sees a blank field marked *not on document*", never "the front desk sees a
confident wrong policy number".
"""
from __future__ import annotations

import datetime as dt
import hashlib
from typing import Optional

from pydantic import BaseModel, Field

from databridge_harness import Harness

from .classify import classify
from .ground import GroundKind, canonicalise_id, ground, mask_unticked_options
from .ocr import MediaIndex, attach_media_provenance
from .models import (
    AuthorisationBlock,
    BillingParty,
    DocumentRef,
    DocumentType,
    EmployerBlock,
    IdType,
    IntakeRecord,
    IssuerBlock,
    IssuerKind,
    SubjectBlock,
    VisitType,
)
from .registry import classify_id_type
from .rules import rules

TASK_CLASS = "intake_extraction"


# --- what the model returns --------------------------------------------------------
class IntakeDraft(BaseModel):
    """Flat, LLM-friendly draft. Every field is an optional VERBATIM string.

    Verbatim matters: the grounding wall locates values by matching them against the document, so
    a "helpfully" reformatted date or expanded abbreviation fails to ground and is discarded. The
    prompt says so explicitly, and this is the reason.
    """

    document_type: Optional[str] = None

    issuer_name: Optional[str] = None
    issuer_kind: Optional[str] = None
    issuer_code: Optional[str] = None
    policy_number: Optional[str] = None
    reference_number: Optional[str] = None
    issuer_contact: Optional[str] = None

    claimed_name: Optional[str] = None
    claimed_id_number: Optional[str] = None
    claimed_dob: Optional[str] = None
    claimed_sex: Optional[str] = None
    claimed_nationality: Optional[str] = None

    purpose_text: Optional[str] = None
    package_code: Optional[str] = None
    package_name: Optional[str] = None
    requested_tests: list[str] = Field(default_factory=list)
    add_ons: list[str] = Field(default_factory=list)
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    appointment_datetime: Optional[str] = None
    appointment_location: Optional[str] = None
    billing_party: Optional[str] = None
    billing_address: Optional[str] = None
    special_instructions: list[str] = Field(default_factory=list)

    employer_name: Optional[str] = None
    agency: Optional[str] = None
    scheme_of_service: Optional[str] = None
    hr_contact: Optional[str] = None

    visit_type: Optional[str] = None


# draft field → (IntakeRecord dotted path, grounding kind)
FIELD_MAP: dict[str, tuple[str, GroundKind]] = {
    "issuer_name": ("issuer.issuer_name", "name"),
    "issuer_code": ("issuer.issuer_code", "id"),
    "policy_number": ("issuer.policy_number", "id"),
    "reference_number": ("issuer.reference_number", "id"),
    "issuer_contact": ("issuer.contact", "text"),
    "claimed_name": ("subject.claimed_name", "name"),
    "claimed_id_number": ("subject.claimed_id_number", "id"),
    "claimed_dob": ("subject.claimed_dob", "date"),
    "claimed_sex": ("subject.claimed_sex", "text"),
    "claimed_nationality": ("subject.claimed_nationality", "text"),
    "purpose_text": ("authorisation.purpose_text", "text"),
    "package_code": ("authorisation.package_code", "id"),
    "package_name": ("authorisation.package_name", "text"),
    "valid_from": ("authorisation.valid_from", "date"),
    "valid_until": ("authorisation.valid_until", "date"),
    "appointment_datetime": ("authorisation.appointment_datetime", "date"),
    "appointment_location": ("authorisation.appointment_location", "text"),
    "billing_address": ("authorisation.billing_address", "text"),
    "employer_name": ("employer.employer_name", "name"),
    "agency": ("employer.agency", "name"),
    "scheme_of_service": ("employer.scheme_of_service", "text"),
    "hr_contact": ("employer.hr_contact", "text"),
}

LIST_MAP: dict[str, str] = {
    "requested_tests": "authorisation.requested_tests",
    "add_ons": "authorisation.add_ons",
    "special_instructions": "authorisation.special_instructions",
}

# Fields whose value describes what was SELECTED on a checkbox form. These are grounded against a
# view with unticked lines blanked out — see `ground.mask_unticked_options`.
CHECKBOX_SENSITIVE: frozenset[str] = frozenset({
    "authorisation.package_code",
    "authorisation.package_name",
    "authorisation.requested_tests",
    "authorisation.add_ons",
})


def _is_nric_shaped(value: str) -> bool:
    # Compare on the canonical form: a form printing "S 8001691 D" is the same NRIC as
    # "S8001691D", and the grounded value records the document's spacing verbatim.
    return classify_id_type(canonicalise_id(value)) is IdType.NRIC_FIN


def _is_a_classifiable_identifier(value: str) -> bool:
    return classify_id_type(canonicalise_id(value)) is not IdType.UNKNOWN


# Per-field shape checks, applied AFTER grounding.
#
# The wall answers "is this string on the page?" — it cannot answer "is it the right *kind* of
# thing for this field". So a model that swaps two real values passes: on chit_01 both
# `policy_number="S8010946C"` and `claimed_id_number="MRD707314"` are genuinely printed, both
# ground, and both get valid-looking pointers. The front desk then sees the patient's NRIC filed
# as a policy number.
#
# Format is the only issuer-agnostic signal available: a Singapore NRIC/FIN has a fixed shape and
# no insurer's policy number shares it. Anything more would need label context, which is the
# per-issuer template knowledge this design exists to avoid.
#
# The two directions are deliberately ASYMMETRIC:
#
#   * policy/reference: REFUSE an NRIC-shaped value. An NRIC in a policy field is unambiguously
#     wrong, so deleting it loses nothing.
#   * claimed_id_number: accept anything classifiable at all — NRIC/FIN *or* passport. The chit
#     forms label this field "NRIC/Passport No." and CLAUDE.md names passports explicitly.
#     Requiring NRIC shape here refused every foreign patient's passport number, turning a real
#     value into "not on document". Refusing on shape cannot distinguish a swap from an
#     unrecognised-but-genuine identifier, and the house preference is to surface uncertainty,
#     not delete data. A genuine swap still surfaces downstream: the Phase 2 resolver looks this
#     value up in the registry, and a policy number simply finds no candidate.
FIELD_SHAPE_RULES: dict[str, tuple[callable, str]] = {
    "subject.claimed_id_number": (
        _is_a_classifiable_identifier,
        "is not the shape of any identity document number (NRIC/FIN or passport)"),
    "issuer.policy_number": (
        lambda v: not _is_nric_shaped(v),
        "has the shape of an NRIC/FIN, not a policy number"),
    "issuer.reference_number": (
        lambda v: not _is_nric_shaped(v),
        "has the shape of an NRIC/FIN, not a reference number"),
}

# Fields the model may CLASSIFY rather than copy. These are conclusions ABOUT the document, not
# quotations FROM it, so they cannot be grounded by locating them in the text. They are instead
# validated against a closed vocabulary and given a `derived` pointer that chains back to the
# document — provenance that says "inferred from this document" rather than faking a span.
CLASSIFIED_FIELDS: dict[str, tuple[str, type]] = {
    "issuer_kind": ("issuer.issuer_kind", IssuerKind),
    "billing_party": ("authorisation.billing_party", BillingParty),
    "visit_type": ("visit_type", VisitType),
}

SYSTEM_PROMPT = """You read a referring medical document for a Singapore clinic's front desk and \
extract it into a fixed schema. The document may be an insurer referral letter, a health check-up \
voucher, a group-insurance invitation, an underwriting follow-up memo, a TPA or government \
authorisation form, or an employer HR email. One schema covers all of them.

RULES:
1. Copy values VERBATIM from the document. Do not reformat dates, expand abbreviations, fix \
spelling, or tidy punctuation. Every value you emit is checked character-by-character against the \
source and DISCARDED if it does not appear there.
2. If the document does not state something, leave the field null. Never infer, never guess, \
never carry a value over from your general knowledge. A null is a correct answer; an invented \
value is the worst possible answer.
3. For checkbox forms, extract ONLY the ticked options. A checkbox rendered as U+2612 is ticked; \
one rendered as U+2610 is NOT ticked and must be ignored entirely.
4. requested_tests must list only actual medical tests, examinations or procedures the document \
asks for. Do not include administrative requirements (proof of address, manager's approval), and \
do not include options that were offered but not selected.
5. issuer_code is the short alphanumeric code the document prints to identify the paying scheme \
(often labelled "Code:" or "TPA / Company Code:"). Copy it exactly.

Return only JSON matching the schema."""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_messages(text: str) -> list[dict]:
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"--- DOCUMENT ---\n{text}\n--- END DOCUMENT ---"}]


def _set_path(blocks: dict, path: str, value) -> None:
    block, field = path.split(".", 1)
    blocks[block][field] = value


def extract_intake(
    text: str,
    *,
    document_id: str,
    harness: Harness,
    channel: str = "upload",
    media_type: str = "text/plain",
    received_at: Optional[dt.datetime] = None,
    intake_id: Optional[str] = None,
    seed: int = 7,
    media: Optional[MediaIndex] = None,
) -> IntakeRecord:
    """Read one document into a fully grounded ``IntakeRecord``.

    The classifier's verdict wins over the model's opinion on ``document_type``: the classifier is
    deterministic and inspectable, and its abstention behaviour is the thing that routes ambiguous
    documents to a human.

    ``media`` is the page geometry from an uploaded scan (``ocr.extract_upload``). When present,
    every ``doc_span`` pointer gains an ``ocr_bbox`` child naming the region on the original that
    the span was recognised from. ``text`` must be the *same* extracted text the index was built
    against — see ``ocr`` — or the children will frame the wrong part of the page.
    """
    now = received_at or dt.datetime.now(dt.timezone.utc)
    doc = DocumentRef(document_id=document_id, sha256=_sha256(text), media_type=media_type,
                      channel=channel, received_at=now)
    # Derived from the DOCUMENT, not the clock. With `now` in the hash, re-reading the same
    # document produced a different intake every time — so the queue accumulated duplicate
    # rows and any attestation already recorded was orphaned on the previous id.
    rid = intake_id or f"intake:{_sha256(document_id + doc.sha256)[:12]}"

    verdict = classify(text)
    # The classification is derived, not quoted, so it gets a `derived` pointer naming the markers
    # that fired — chained to the span of the marker that ACTUALLY fired. A fixed 0:400 window
    # would be a citation that need not contain its own evidence, which is the sort of provenance
    # that is worse than none: it looks checkable and isn't.
    from databridge_core import GroundedValue, SourcePointer
    ev_start, ev_end = verdict.evidence_span or (0, min(len(text), 200))
    type_gv = GroundedValue(
        value=verdict.doc_type.value,
        pointer=SourcePointer(
            source_id=document_id, kind="derived",
            derivation=("document classification from layout markers: "
                        + (", ".join(verdict.matched) if verdict.matched else "no markers matched")),
            derived_from=[SourcePointer(source_id=document_id, kind="doc_span",
                                        document_id=document_id, char_start=ev_start,
                                        char_end=ev_end)]),
        confidence=verdict.confidence)
    # Attached HERE, not in a pass at the end: the classified fields below chain their
    # `derived_from` to `type_gv.pointer`, so a later attachment would leave those chains citing
    # the pre-attachment pointer — the scan region reachable from the quoted fields and not from
    # the derived ones, for no reason a reader of the record could see.
    type_gv = attach_media_provenance(type_gv, media)

    if verdict.doc_type is DocumentType.NO_DOCUMENT:
        return IntakeRecord(intake_id=rid, document=doc, document_type=type_gv,
                            visit_type=None, extracted_at=now)

    result = harness.complete_structured(TASK_CLASS, build_messages(text), IntakeDraft,
                                         seed=seed, source_id=document_id)
    draft: IntakeDraft = result.parsed

    blocks: dict[str, dict] = {"issuer": {}, "subject": {}, "authorisation": {}, "employer": {}}
    unresolved: list[str] = []
    refused: list[str] = []

    # Fields describing what was SELECTED on a checkbox form are grounded against a view with
    # unticked lines blanked out. Otherwise the wall happily confirms an option the form declined:
    # PEE224/PEE225 and WELL1/WELL3 are all printed on their forms, so "does this string appear?"
    # is the wrong question for them. Offsets are preserved, so pointers still resolve.
    selected_view = mask_unticked_options(text)

    def _source_for(path: str) -> str:
        return selected_view if path in CHECKBOX_SENSITIVE else text

    # -- scalar fields: ground or refuse ------------------------------------------
    for field_name, (path, kind) in FIELD_MAP.items():
        raw = getattr(draft, field_name, None)
        if raw is None or not str(raw).strip():
            unresolved.append(path)
            continue
        gv, reason = ground(_source_for(path), str(raw).strip(), document_id=document_id,
                            kind=kind, field_path=path)
        if gv is None:
            refused.append(reason or f"{path}: not grounded")
            unresolved.append(path)
            continue
        # Being on the page is necessary but not sufficient — it must also be the right KIND of
        # value for this field. See FIELD_SHAPE_RULES.
        shape = FIELD_SHAPE_RULES.get(path)
        if shape is not None and not shape[0](str(gv.value)):
            refused.append(f"{path}: {gv.value!r} {shape[1]}")
            unresolved.append(path)
            continue
        _set_path(blocks, path, attach_media_provenance(gv, media))

    # -- list fields: each element grounded independently --------------------------
    for field_name, path in LIST_MAP.items():
        items = getattr(draft, field_name, []) or []
        grounded_items = []
        any_refused = False
        for item in items:
            if not str(item).strip():
                continue
            gv, reason = ground(_source_for(path), str(item).strip(), document_id=document_id,
                                kind="text", field_path=path)
            if gv is None:
                refused.append(reason or f"{path}: not grounded")
                any_refused = True
                continue
            grounded_items.append(attach_media_provenance(gv, media))
        block, field = path.split(".", 1)
        blocks[block][field] = grounded_items
        # Only a list with NOTHING in it is "unresolved". A partially-refused list keeps its
        # surviving items and is reported through `refused` instead — marking it unresolved would
        # print "not on document" beside a field that visibly has values on the card. The two
        # channels stay disjoint: `unresolved` = no value at all, `refused` = something dropped.
        if not grounded_items:
            unresolved.append(path)
        elif any_refused:
            refused.append(f"{path}: kept {len(grounded_items)} item(s); "
                           f"see the refusals above for what was dropped")

    # -- classified (not quoted) fields --------------------------------------------
    # Who the issuer IS and who PAYS are properties of the scheme, not opinions about the letter.
    # Once the issuer code has been grounded, the coverage-rules table is the authority for them —
    # the model's guess is discarded. Trusting the model here would let it silently reroute a bill
    # to the patient, and would mean two sources of truth for the same fact.
    issuer_code_gv = blocks["issuer"].get("issuer_code")
    rule_issuer = rules().issuer(str(issuer_code_gv.value)) if issuer_code_gv else None

    # path → (authoritative value, the YAML KEY it came from). The key is carried explicitly
    # because it does NOT always equal the last segment of the field path: the issuer table spells
    # these `kind` and `default_visit_type`. Deriving the column from the path produced pointers
    # citing `issuers.issuer_kind` and `issuers.visit_type` — columns that do not exist, so the
    # citation looked authoritative and resolved to nothing.
    from_config: dict[str, tuple[str, str]] = {}
    if rule_issuer is not None:
        from_config = {
            "issuer.issuer_kind": (rule_issuer.kind.value, "kind"),
            "authorisation.billing_party": (rule_issuer.billing_party.value, "billing_party"),
            "visit_type": (rule_issuer.default_visit_type.value, "default_visit_type"),
        }

    visit_gv = None
    for draft_field, (path, enum_cls) in CLASSIFIED_FIELDS.items():
        authoritative = from_config.get(path)
        if authoritative is not None:
            value, yaml_column = authoritative
            gv = GroundedValue(
                value=value,
                pointer=SourcePointer(
                    source_id="coverage_rules.yaml", kind="table_cell", table="issuers",
                    column=yaml_column, row_id=rule_issuer.code),
                confidence=1.0)
            if path == "visit_type":
                visit_gv = gv
            else:
                _set_path(blocks, path, gv)
            continue

        raw = getattr(draft, draft_field, None)
        allowed = {m.value for m in enum_cls}
        if raw not in allowed:
            # Includes the None case. An out-of-vocabulary answer is discarded rather than
            # stored, and either way the field is accounted for as unresolved.
            if raw:
                refused.append(f"{path}: {raw!r} is not a valid {enum_cls.__name__}")
            unresolved.append(path)
            continue
        gv = GroundedValue(
            value=raw,
            pointer=SourcePointer(
                source_id=document_id, kind="derived",
                derivation=f"{path} classified from document content (not quoted from it); "
                           f"no coverage rule matched an issuer code, so this is the model's "
                           f"reading and carries reduced confidence",
                derived_from=[type_gv.pointer]),
            confidence=0.6)
        if path == "visit_type":
            visit_gv = gv
        else:
            _set_path(blocks, path, gv)

    # -- claimed_id_type: derived from the id number's FORMAT ----------------------
    # Structural classification only — it picks the NRIC/Passport radio button on the
    # questionnaire. It says nothing about whether the document is genuine or belongs to the
    # person presenting it (CLAUDE.md §Hard constraints 1).
    id_gv = blocks["subject"].get("claimed_id_number")
    if id_gv is not None:
        # canonicalise: the recorded value keeps the form's spacing ('S 8001691 D')
        id_type = classify_id_type(canonicalise_id(str(id_gv.value)))
        if id_type is IdType.UNKNOWN:
            unresolved.append("subject.claimed_id_type")
        else:
            blocks["subject"]["claimed_id_type"] = GroundedValue(
                value=id_type.value,
                pointer=SourcePointer(
                    source_id=document_id, kind="derived",
                    derivation=f"id format classification -> {id_type.value}",
                    derived_from=[id_gv.pointer]),
                confidence=1.0 if id_type is IdType.NRIC_FIN else 0.6)
    else:
        unresolved.append("subject.claimed_id_type")

    return IntakeRecord(
        intake_id=rid,
        document=doc,
        document_type=type_gv,
        issuer=IssuerBlock(**blocks["issuer"]),
        subject=SubjectBlock(**blocks["subject"]),
        authorisation=AuthorisationBlock(**blocks["authorisation"]),
        employer=EmployerBlock(**blocks["employer"]),
        visit_type=visit_gv,
        unresolved=tuple(dict.fromkeys(unresolved)),
        refused=tuple(refused),
        extracted_at=now,
    )
