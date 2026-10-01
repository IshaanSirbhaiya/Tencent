"""The **determination** routes — the front-desk surface, and the confirmation gate.

Served at ``/determination/*``, and at ``/intake/*`` as an alias. The vertical name came first and
the frontend, the e2e specs and every saved bookmark still use it, so the old prefix answers
identically rather than being broken in the same change that generalises the engine — one handler
object, two paths, so there is no second implementation to drift and a route added to one is a
route added to both. ``/determination`` is the documented prefix (it is the one that appears in
the OpenAPI schema); ``/intake`` is a compatibility alias.

*Determination* is the domain-free word for what this surface does: read an artefact, resolve it
against a registry, decide what it entitles the subject to, and refuse to commit any of it until
a named human attests. A clinic calls that intake; a lender calls it underwriting; a benefits
office calls it assessment. The mechanics are the product; the noun is a pack's.

The route order tells the story the product tells: upload a document, read the card, click through
to the evidence, **select a patient after checking their ID in person**, then confirm. Only the
last step writes anything, and it refuses without an attestation.

Three deliberate choices, each of which a more convenient design would get wrong:

* **``select-patient`` and ``confirm`` are separate calls.** Selection records which candidate a
  human picked; confirmation writes the evidence. Collapsing them would let a single request both
  choose an identity and commit it, which is the thing this architecture exists to prevent.
* **The attestation must be explicit in the request body.** ``attested_in_person`` is a required
  boolean, not inferred from the presence of a staff id — a client that forgets it gets a refusal,
  not a silent default.
* **Uploads are held in memory per intake, never on a filesystem path the client names.** The
  client posts the document *text*, or posts *bytes* that are extracted in memory; there is no
  endpoint that opens a path it is given.

``/intake/upload-file`` (T-36) adds files to that surface, and adds one rule of its own: an upload
that looks like an identity document or an insurance e-card is refused by
``databridge_intake.id_document_guard``, and refused so that **nothing is extracted from it** — no
stored text, no record, no model call, no queue entry. `/intake` promises we never read identity
documents; a file path is the first way that promise could be broken, so the guard runs twice, once
on the declared filename before a byte is parsed and once on the extracted text before a character
of it is kept.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

from .auth import install_auth_handler, require_principal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from databridge_intake.clock import deployment_today
from databridge_intake.extract import CHECKBOX_SENSITIVE
from databridge_intake.guardrails import check_question
from databridge_intake.id_document_guard import (screen_extracted_text, screen_upload)
from databridge_intake.ledger import (AlreadyCommitted, EvidenceLedger, IdentityNotVerified,
                                      LedgerError, SubjectChanged, attestation_subject_matches,
                                      subject_fingerprint_for)
from databridge_intake.models import ConfirmationState
from databridge_intake.ocr import (ExtractionUnavailable, MAX_UPLOAD_BYTES, UnsupportedUpload,
                                   extract_upload)
from databridge_intake.pipeline import evidence_for, run_intake, summary_card

log = logging.getLogger("databridge.gateway.intake")

MAX_DOCUMENT_CHARS = 200_000

#: The platform's route prefix, and the vertical alias it grew out of. FIRST WINS for
#: documentation: only the neutral prefix appears in the OpenAPI schema, so the alias cannot
#: quietly become the name of the thing.
DETERMINATION_PREFIX = "/determination"
LEGACY_PREFIX = "/intake"
ROUTE_PREFIXES = (DETERMINATION_PREFIX, LEGACY_PREFIX)


#: INT1 (2026-10-01): every route in this module requires an authenticated principal — the gateway
#: serves nothing anonymously except `/health` (`test_every_route_on_the_app_is_gated`).
_AUTH = [Depends(require_principal)]


def subject_id_fields() -> tuple[str, ...]:
    """The payload keys that may carry a subject's registry id, READ FROM THE ACTIVE PACK.

    ``patient_id`` is not written down here. The enabled packs declare their subject kinds
    (spine D4b), and the wire name for each is ``f"{kind}_id"`` — so the healthcare pack's
    ``patient`` kind is what makes ``patient_id`` an accepted field, and binding a different pack
    accepts that pack's noun instead. ``subject_id`` is always accepted as the neutral spelling.

    A key no pack declares is REFUSED rather than ignored: a client that sends ``customer_id`` to
    a deployment with no such subject kind has misunderstood something, and silently dropping the
    field would turn that into "no id supplied" — which on this surface means selecting nobody,
    quietly.

    Computed per call: the deployment binding is rebindable, and a tuple frozen at import time
    would answer for whichever packs happened to be bound when this module was first imported.
    """
    from databridge_graph.packs import active_vocabulary

    return tuple(sorted(f"{kind}_id" for kind in active_vocabulary().subject_kinds))


#: The file a pack ships, beside its ``ontology.yaml``, naming the gateway route groups it needs.
ROUTES_FILENAME = "gateway_routes.yaml"

#: The route groups a pack may declare. Closed: an unknown name is a typo or a route this gateway
#: does not have, and either way booting quietly without it would hide the mistake.
ROUTE_GROUPS = frozenset({"determination", "outbox"})


class PackRoutesError(RuntimeError):
    """A pack's ``gateway_routes.yaml`` is malformed or names an unknown route group."""


def pack_declared_routes() -> frozenset[str]:
    """The route groups the BOUND packs declare (HC1, 2026-10-01; SWEEP hardcode audit #10).

    ``/intake`` and ``/outbox`` used to be registered for every deployment, so a finance or
    workspace binding served one vertical's refusal copy from routes it had no use for. Now a group
    is mounted only when a pack the deployment binds declares it. Read through the graph loader's
    ``configured_packs`` so an in-process ``deployment(...)`` binding is honoured too. Computed
    from the deployment, never from a pack's name.
    """
    import yaml
    from databridge_graph.packs import configured_packs, packs_root

    root = packs_root()
    declared: set[str] = set()
    for name in configured_packs(root):
        path = root / name / ROUTES_FILENAME
        if not path.is_file():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        routes = data.get("routes") if isinstance(data, dict) else None
        if not isinstance(routes, list) or not all(isinstance(r, str) for r in routes):
            raise PackRoutesError(f"{path}: `routes` must be a list of route-group names")
        unknown = sorted(set(routes) - ROUTE_GROUPS)
        if unknown:
            raise PackRoutesError(f"{path}: unknown route group(s) {unknown}; known: "
                                  f"{sorted(ROUTE_GROUPS)}")
        declared.update(routes)
    return frozenset(declared)


def _pack_noun(key: str, neutral: str) -> str:
    """A noun from the active pack's ``ui.yaml`` (``nouns``), for refusal copy (HC1).

    The wording used to name one country's identity documents in code. ``neutral`` is what the
    sentence says when the pack declares no such noun — a plain word, never another pack's term.
    """
    import yaml

    try:
        from databridge_intake.pack import pack_dir

        data = yaml.safe_load((pack_dir() / "ui.yaml").read_text(encoding="utf-8")) or {}
        value = str(((data.get("nouns") or {}).get(key)) or "").strip()
    except Exception:                                                # noqa: BLE001
        value = ""
    return value or neutral


def _refusal(code: str, message: str, status: int = 422) -> JSONResponse:
    return JSONResponse(status_code=status, content={"refusal": {
        "code": code, "message": message, "nearest_alternative": None}})


class IntakeUploadRequest(BaseModel):
    document_id: str = Field(min_length=1, max_length=200)
    text: str
    channel: str = "upload"
    media_type: str = "text/plain"


# One staff-id format, enforced at the request boundary AND mirrored in the frontend's shift
# session (`lib/staff-session.tsx`). `min_length=1` was the whole validation, so a control
# character, a whitespace-only id via the raw API, or an unbounded string could be written into the
# ledger's `identity_checked_in_person_by` — the column whose entire purpose is naming a real,
# accountable person. Printable ASCII, bounded, no surrounding whitespace.
STAFF_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,63}$"


class SelectSubjectRequest(BaseModel):
    """The candidate a human picked, after checking their identity in person.

    The subject id may arrive as ``subject_id`` (the neutral spelling) or under the wire name the
    active pack declares for its subject kind — ``patient_id`` under the healthcare pack. Exactly
    one of them, and the alias is resolved through `subject_id_fields()`, so it is the pack that
    decides which nouns this endpoint answers to rather than this file.

    Nothing about the gate moved. ``attested_in_person`` is still a required, explicit boolean and
    still defaults to the refusing value.
    """

    # `extra="allow"` so a pack-declared alias reaches the validator at all; the validator then
    # REFUSES every extra key it does not recognise, which is `extra="forbid"` with one
    # pack-computed exception rather than an open door.
    model_config = ConfigDict(extra="allow")

    subject_id: Optional[str] = Field(default=None, min_length=1)
    staff_id: str = Field(pattern=STAFF_ID_PATTERN)
    # Not inferred from `staff_id` being present: a client that forgets the attestation must get a
    # refusal, not a default that happens to be permissive.
    attested_in_person: bool = False

    @model_validator(mode="after")
    def _exactly_one_subject_id(self) -> "SelectSubjectRequest":
        aliases = subject_id_fields()
        extra = dict(self.model_extra or {})
        unknown = sorted(k for k in extra if k not in aliases)
        if unknown:
            raise ValueError(
                f"unknown field(s) {unknown}: a subject id must be sent as 'subject_id' or as "
                f"one of {list(aliases)}, the wire names the enabled packs declare")
        supplied = {k: v for k, v in extra.items() if v not in (None, "")}
        if self.subject_id and supplied:
            raise ValueError(
                f"send the subject id once: both 'subject_id' and {sorted(supplied)} were given")
        if not self.subject_id:
            if len(supplied) > 1:
                raise ValueError(f"send the subject id once: {sorted(supplied)} were all given")
            if not supplied:
                raise ValueError(
                    f"a subject id is required, as 'subject_id' or as one of {list(aliases)}")
            value = str(next(iter(supplied.values()))).strip()
            if not value:
                raise ValueError("the subject id is empty")
            self.subject_id = value
        return self

    @property
    def patient_id(self) -> str:
        """Back-compatible read for call sites written against the vertical's noun."""
        return self.subject_id or ""


#: The name this model had while the surface was a clinic's. Kept as an alias so the rename is
#: invisible to anything importing it.
SelectPatientRequest = SelectSubjectRequest


class ConfirmRequest(BaseModel):
    staff_id: str = Field(pattern=STAFF_ID_PATTERN)
    attested_in_person: bool = False
    field_paths: Optional[list[str]] = None


def _register_intake_routes(app: FastAPI) -> None:
    """Attach the determination surface under BOTH prefixes. State lives on ``app.state``.

    Only when a bound pack declares the ``determination`` route group (`pack_declared_routes`).
    Otherwise nothing is mounted and ``app.state.intakes`` stays unset — every reader of it
    already treats a missing queue as "no intake surface in this deployment".
    """
    if "determination" not in pack_declared_routes():
        log.info("no bound pack declares the determination routes; /intake is not mounted")
        return
    install_auth_handler(app)

    def _route(method: str, suffix: str, *, documented: bool = True, **kw):
        """Register one handler at ``/determination<suffix>`` AND at ``/intake<suffix>``.

        Registration order within each prefix is preserved, which is load-bearing: ``/fixtures``
        must be declared before ``/{intake_id}`` or the literal path would be swallowed by the
        parameter.

        ``documented=False`` for a suffix that is itself a compatibility spelling, so the schema
        describes one name per operation and a vertical noun cannot arrive on the neutral prefix
        as though it were the platform's word for the thing.
        """
        def register(fn):
            for i, prefix in enumerate(ROUTE_PREFIXES):
                getattr(app, method)(prefix + suffix,
                                     include_in_schema=documented and i == 0,
                                     dependencies=_AUTH, **kw)(fn)
            return fn
        return register

    # intake_id → {"draft": IntakeDraft, "text": str, "media": MediaIndex | None}. `text` is the
    # ONE extraction of the document and `media` indexes into those exact offsets, so the two are
    # stored and re-read as a pair — see `_ingest`.
    app.state.intakes = {}
    app.state.confirmations = {}    # intake_id → ConfirmationState
    app.state.ledger = EvidenceLedger()
    # FastAPI runs these sync routes in a threadpool, so the dict transitions below are genuine
    # read-modify-write races: a select and a confirm interleaving could store a stale, uncommitted
    # state over a committed one and let the same intake commit twice.
    app.state.intake_lock = threading.RLock()

    def _get(intake_id: str):
        return app.state.intakes.get(intake_id)

    def _history():
        """The graph adapter over the live clinic graph — the composition point for the Phase 6
        seam. `packages/intake` never imports the graph; the wiring happens here.

        Degrades to `NoHistory` if the brain is not up. An enrichment must never be able to stop an
        intake being read, so a missing graph loses flags and nothing else.
        """
        from databridge_intake.history import NoHistory

        brain = getattr(app.state, "brain", None)
        if brain is None:
            return NoHistory()
        try:
            from databridge_graph.clinic_history import GraphClinicHistory

            return GraphClinicHistory(brain.store)
        except Exception:                                            # noqa: BLE001
            log.exception("subject history unavailable; continuing without it")
            return NoHistory()

    class _LiveModelUnavailable(Exception):
        """A model is configured for extraction and could not be used. Refused, never papered over."""

    def _extraction_backend():
        """``(harness, offline_gold)`` for one document read, or raise ``_LiveModelUnavailable``.

        HC1 (2026-10-01, SWEEP hardcode audit #1). The gold backend is an input→answer table keyed
        on fixture markers (`databridge_intake.gold`): it RECALLS a reading, it does not compute
        one. It is therefore allowed here in exactly one situation — no model is configured at all
        (no provider key, or ``DATABRIDGE_FORCE_GOLD=1``, which `detect_llm` treats as "no key") —
        and the card then carries ``offline_gold: true`` so nobody mistakes it for a reading.

        Everything else is a refusal, mirroring ``/brain/ingest``'s ``NO_EXTRACTION_MODEL``:

        * building the configured harness throws → ``LIVE_MODEL_UNAVAILABLE``. This used to log
          and return ``None``, which silently handed the document to the gold table;
        * a provider key is set but no extraction route is reachable → the same refusal, naming
          the routing reason. A deployment that configured a model expects that model.

        ``DATABRIDGE_FORCE_GOLD=1`` still pins gold: the e2e suite sets it because ``CLAUDE.md``
        requires that no test depend on a live model, and it is labelled like any other gold read.
        """
        import os

        from databridge_intake.gold import gold_harness

        from . import llm as gateway_llm

        if os.getenv("DATABRIDGE_FORCE_GOLD") == "1":
            return gold_harness(), True
        try:
            detected = gateway_llm.detect_llm()
            harness = gateway_llm.build_extraction_harness(detected)
        except Exception as exc:                                     # noqa: BLE001
            log.exception("the configured extraction model could not be built")
            raise _LiveModelUnavailable(
                "a model is configured for document extraction but could not be started "
                f"({type(exc).__name__}). The document was not read; nothing was queued. "
                "Check the gateway log and /health.") from None
        if harness is not None:
            return harness, False
        keyed = [env for env in gateway_llm._PROVIDER_KEY_ENV.values() if os.getenv(env)]
        if keyed:
            reasons = sorted({str(r.get("reason")) for r in (detected.get("routing") or {}).values()
                              if r.get("effective") is None})
            raise _LiveModelUnavailable(
                f"{', '.join(sorted(keyed))} is set but no extraction model is reachable"
                + (f" ({'; '.join(reasons)})" if reasons else "")
                + ". The document was not read; nothing was queued.")
        return gold_harness(), True

    def _backend_or_refusal():
        try:
            return _extraction_backend(), None
        except _LiveModelUnavailable as exc:
            return (None, None), _refusal("LIVE_MODEL_UNAVAILABLE", str(exc), status=503)

    def _label(card: dict, offline_gold: Optional[bool]) -> dict:
        """Say on the card how it was read. ``offline_gold`` is the label the UI must show.

        ``None`` means the entry was queued by something other than ``_ingest`` (a test, a seed)
        and nobody recorded how it was read — reported as such, never guessed to be live.
        """
        card["offline_gold"] = offline_gold
        card["extraction_backend"] = ("unrecorded" if offline_gold is None
                                      else "offline_gold" if offline_gold else "live")
        return _currency_neutral(card)

    def _currency_neutral(card: dict) -> dict:
        """SWEEP #15 (HC3): say the currency, and name every amount without it.

        `billing.currency` is read from the active pack's `coverage_rules.yaml` and cited by a
        pointer to that key. Every key that ends in `_<currency>` under `billing` and
        `coverage.authorised_tests` gets a currency-neutral alias with the same value, including
        the pointer under `field_sources`. The suffix is computed from the declared currency, so
        no currency is written here. If the table declares none, `currency` is null and no alias
        is made.

        DEPRECATED: the `_<currency>` keys (`estimated_patient_payable_sgd`, `known_subtotal_sgd`,
        `list_price_sgd`) are still sent for one release so older clients keep working. New
        readers use the neutral key plus `currency`.
        """
        from databridge_core import SourcePointer
        from databridge_intake.billing import RULES_SOURCE_ID
        from databridge_intake.rules import rules

        billing = card.get("billing")
        if not isinstance(billing, dict):
            return card
        currency = rules().currency
        billing["currency"] = currency
        if currency is None:
            return card
        billing.setdefault("field_sources", {})["currency"] = SourcePointer(
            source_id=RULES_SOURCE_ID, kind="table_cell", table="coverage_rules",
            column="currency", row_id="currency").model_dump(mode="json")
        suffix = "_" + currency.lower()

        def alias(node) -> None:
            if isinstance(node, dict):
                for key in list(node):
                    alias(node[key])
                    if isinstance(key, str) and key.endswith(suffix) and len(key) > len(suffix):
                        node.setdefault(key[: -len(suffix)], node[key])
            elif isinstance(node, list):
                for item in node:
                    alias(item)

        alias(billing)
        alias((card.get("coverage") or {}).get("authorised_tests"))
        return card

    def _ingest(text: str, *, document_id: str, channel: str, media_type: str, media=None):
        """Read one document into the queue and return its card, or a refusal.

        Shared by the text route and the file route. Extracted rather than copied because the
        compare-and-swap dance below is not incidental: every branch in it was written to close a
        specific wrong-subject-attestation window (see the comments), and a second copy of it is a
        second place for those windows to reopen quietly.

        `media` is the uploaded scan's page geometry. It is an index into **this** `text`, so it is
        stored beside it and every later re-run for this intake must pass the pair together.
        """
        if len(text) > MAX_DOCUMENT_CHARS:
            return _refusal("DOCUMENT_TOO_LARGE",
                            f"document exceeds {MAX_DOCUMENT_CHARS} characters")
        (harness, offline_gold), refused = _backend_or_refusal()
        if refused is not None:
            return refused
        try:
            draft = run_intake(text, document_id=document_id, channel=channel,
                               media_type=media_type, history=_history(),
                               harness=harness, media=media, today=deployment_today())
        except Exception:                                            # noqa: BLE001
            # Logged in full server-side; the RESPONSE carries only a stable code and the exception
            # type. A raw exception message can quote document content back to the caller.
            log.exception("intake extraction failed for document_id=%r", document_id)
            return _refusal("EXTRACTION_FAILED", "could not read this document")
        # Read the existing state under the lock, then do any expensive re-run OUTSIDE it.
        # `run_intake` is a live model call when an API key is present; holding the global lock
        # across it blocked the polling queue and /impact for seconds at a time.
        with app.state.intake_lock:
            existing = app.state.confirmations.get(draft.intake_id)
            # Snapshot what we read, so the write-back can prove nothing moved underneath us while
            # the lock was released. See the compare-and-swap below.
            seen_confirmation = existing
            seen_held = app.state.intakes.get(draft.intake_id)
        if existing is not None and existing.committed:
            return _refusal(
                "ALREADY_COMMITTED",
                "this document has already been read and committed; the ledger is append-only")
        if True:
            if existing is not None and existing.selected_patient_id:
                # THE BUG THIS GUARDS. `intake_id` is derived from the document, so re-uploading
                # the same file lands on the same intake. Overwriting the draft with a fresh,
                # PRE-SELECTION one while keeping the attestation left `ready_to_commit` true over
                # a record that had never been resolved against the selected patient — and confirm
                # then wrote 19 ledger rows stamped with a patient the draft knew nothing about.
                # Re-run WITH the retained selection so the two can never diverge.
                selected = next(
                    (c.record for c in draft.resolution.candidates
                     if c.patient_id == existing.selected_patient_id), None)
                if selected is None:
                    return _refusal(
                        "SELECTION_NO_LONGER_A_CANDIDATE",
                        "this document was re-read and the previously selected subject is no "
                        "longer among its candidates; start a new intake")
                try:
                    draft = run_intake(
                        text, document_id=document_id, channel=channel,
                        media_type=media_type, intake_id=draft.intake_id,
                        confirmation=existing, selected_patient=selected, history=_history(),
                        harness=harness, media=media, today=deployment_today())
                except Exception:                                    # noqa: BLE001
                    # Guarded like the first call: a bare raise here would 500 and could quote
                    # document content back to the caller.
                    log.exception("re-read failed for document_id=%r", document_id)
                    return _refusal("EXTRACTION_FAILED", "could not re-read this document")
        with app.state.intake_lock:
            # COMPARE-AND-SWAP, the same one `select-patient` performs, and for the same reason.
            # The re-run above happens OUTSIDE the lock because it can be a live model call, so
            # another workstation can select or confirm this intake in the meantime. Without this
            # check the write below replaces the draft unconditionally:
            #
            #   1. this request re-reads the document, resolved against patient A;
            #   2. another workstation selects B and stores a B-resolved draft and a B confirmation;
            #   3. this request lands, overwriting the draft with its A-resolved result while
            #      `setdefault` leaves B's confirmation in place;
            #   4. confirm then commits A's record stamped with B as the selected patient.
            #
            # That is the wrong-subject attestation bug again (CLAUDE.md §Hard constraints 1). It
            # was fixed on the selection path and left open here; two independent reviewers found
            # it, and `TASKS.md` T-01d had already written down that this path "has the same
            # window".
            current_confirmation = app.state.confirmations.get(draft.intake_id)
            current_held = app.state.intakes.get(draft.intake_id)
            if current_confirmation is not seen_confirmation or current_held is not seen_held:
                return _refusal(
                    "INTAKE_CHANGED",
                    "this intake was selected or confirmed on another workstation while the "
                    "document was being re-read; nothing has been changed. Reload the card.",
                    status=409)
            # Confirmations FIRST. Readers (`intake_card`, `intake_claim_draft`) find the intake and
            # then index `confirmations[id]` directly, so populating `intakes` first left a window in
            # which a polling GET raised KeyError → 500.
            # Do NOT reset an existing attestation: it is a record of something a person did, and
            # clearing it on a re-upload would make "who checked this ID, and when?" depend on how
            # many times a file was dropped.
            app.state.confirmations.setdefault(
                draft.intake_id, ConfirmationState(intake_id=draft.intake_id))
            app.state.intakes[draft.intake_id] = {"draft": draft, "text": text, "media": media,
                                                  "offline_gold": offline_gold}
        return _label(summary_card(draft), offline_gold)

    @_route("post", "/upload")
    def intake_upload(req: IntakeUploadRequest):
        """Run the pipeline and return the DRAFT. Nothing is committed."""
        return _ingest(req.text, document_id=req.document_id, channel=req.channel,
                       media_type=req.media_type)

    @_route("post", "/upload-file")
    def intake_upload_file(file: UploadFile = File(...),
                           document_id: Optional[str] = Form(None),
                           channel: str = Form("upload")):
        """Upload a referring document as a FILE — `.txt`/`.md`/`.eml`, a PDF, or a scan.

        **The identity-document refusal comes first, and it comes twice.** `screen_upload` runs on
        what the caller *named* the file, before a single byte is parsed. `screen_extracted_text`
        runs on the extracted text before it is stored, before it reaches a model, and before an
        `IntakeRecord` exists — because a photographed NRIC is not recognisable from its filename,
        and the only honest reading of "we never read identity documents" is that nothing survives
        the call. Neither refusal echoes any extracted content back to the caller.

        One caveat, stated rather than glossed: Starlette spools a multipart file over 1 MB to a
        temporary file while it parses the request, before any of our code runs. It deletes it when
        the request ends. So "nothing is stored" means nothing enters the intake queue, the record,
        the graph or the ledger — the raw bytes may still have touched a temp file that the server
        then removed.

        **The file is extracted exactly ONCE** and the extracted text is what is stored, what the
        pipeline reads, and what every `doc_span` pointer resolves against. Re-extracting on a
        later request would move every offset under the pointers already recorded.

        Deliberately a SYNC route. docling is CPU-bound for seconds; declaring this `async` would
        run it on the event loop and freeze every other request on the gateway, including the
        queue poll the front desk screen lives on. Sync routes go to the threadpool.
        """
        # Bounded read: `UploadFile` spools to disk, so an unbounded `.read()` is a disk-fill.
        # One byte over the ceiling is enough to know it is over.
        raw = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            return _refusal("DOCUMENT_TOO_LARGE",
                            f"the file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
        filename = (file.filename or "").strip()
        doc_id = (document_id or filename or "").strip()
        if not doc_id or len(doc_id) > 200:
            return _refusal("INVALID_DOCUMENT_ID",
                            "a document id is required and must be at most 200 characters")

        declared = (file.content_type or "").strip()
        guard = screen_upload(filename=filename, document_id=doc_id)
        if guard.refused:
            log.info("upload refused before extraction (%s)", guard.reason)
            return _refusal(guard.code, guard.message)

        try:
            extraction = extract_upload(raw, document_id=doc_id, filename=filename,
                                        media_type=declared)
        except UnsupportedUpload as exc:
            # `UnsupportedUpload` messages are fixed strings chosen by `ocr.extract_upload`; the
            # engine's own error text, which can quote the bytes it choked on, stays in the log.
            log.info("upload rejected for document_id=%r", doc_id, exc_info=True)
            return _refusal("UNSUPPORTED_DOCUMENT", str(exc))
        except ExtractionUnavailable as exc:
            return _refusal("EXTRACTION_UNAVAILABLE", str(exc), status=503)
        except Exception:                                            # noqa: BLE001
            # Same rule as the text route: the response carries a stable code, never the exception
            # message, which can quote document content.
            log.exception("file extraction failed for document_id=%r", doc_id)
            return _refusal("EXTRACTION_FAILED", "could not read this file")

        text_guard = screen_extracted_text(extraction.text)
        if text_guard.refused:
            # Return BEFORE `_ingest`. Nothing is stored, no record is built, no model is called,
            # and `extraction` goes out of scope here — the whole point of the refusal is that the
            # extracted text does not survive it.
            log.info("upload refused after extraction, nothing stored (%s)", text_guard.reason)
            return _refusal(text_guard.code, text_guard.message)

        if not extraction.text.strip():
            return _refusal(
                "NO_TEXT_EXTRACTED",
                "no text could be read from this file. If it is a photograph, try a flatter, "
                "better-lit shot, or paste the text instead.")

        card = _ingest(extraction.text, document_id=doc_id, channel=channel,
                       media_type=extraction.media_type, media=extraction.media)
        if isinstance(card, JSONResponse):
            return card
        # What the extraction itself knows, kept beside the card rather than folded into it: the
        # engine, whether OCR was involved, and what it could NOT recover. A scan whose checkbox
        # marks were lost is a fact the front desk has to see, because the package on this card
        # may then be reading an option the form declined.
        card["extraction"] = {
            "engine": extraction.engine,
            "media_type": extraction.media_type,
            "page_count": extraction.page_count,
            "ocr_pages": list(extraction.ocr_pages),
            "regions": len(extraction.media.regions),
            "warnings": list(extraction.warnings),
            "loss_report": extraction.meta.get("loss_report", []),
            # SWEEP #16 (HC3): which field paths describe a tick-box SELECTION. This is the same
            # set the grounding step masks unticked options for, so the UI's "take these from
            # the original" notice and the masking cannot drift apart.
            "selection_paths": sorted(CHECKBOX_SENSITIVE),
        }
        return card

    @_route("get", "/fixtures")
    def intake_fixtures():
        """The demo corpus, by name. An ALLOW-LIST derived from the fixtures directory — this
        never opens a path the client supplies, for the same reason `brain_ingest` does not."""
        from databridge_intake.paths import fixtures_dir

        chits = sorted(p.name for p in (fixtures_dir() / "chits").glob("*"))
        return {"fixtures": chits}

    @_route("get", "/fixtures/{name}")
    def intake_fixture_text(name: str):
        from databridge_intake.paths import fixtures_dir

        chits = {p.name: p for p in (fixtures_dir() / "chits").glob("*")}
        path = chits.get(name)
        if path is None:
            # Membership in the enumerated set, not a path join — `../` cannot reach anything.
            return _refusal("UNKNOWN_FIXTURE", f"no such fixture '{name}'", status=404)
        return {"document_id": name, "text": path.read_text(encoding="utf-8")}

    @_route("get", "")
    def intake_queue():
        """The front-desk queue: every intake awaiting the counter."""
        out = []
        with app.state.intake_lock:
            snapshot = list(app.state.intakes.items())
            states = dict(app.state.confirmations)
        for intake_id, held in snapshot:
            draft = held["draft"]
            state = states.get(intake_id) or ConfirmationState(intake_id=intake_id)
            out.append({
                "intake_id": intake_id,
                "document_id": draft.record.document.document_id,
                "document_type": draft.record.document_type.value,
                "identity_status": draft.resolution.status.value,
                "candidate_count": len(draft.resolution.candidates),
                "coverage_status": draft.coverage.status.value,
                "eligibility_flags": draft.coverage.eligibility_flags,
                "committed": state.committed,
                "offline_gold": held.get("offline_gold"),
                # NOT `bool(identity_checked_in_person_by)`. That reports an attestation made
                # about a previous reading of this document as verification of the draft now in
                # the queue — the same stale join R-22 found on `/intelligence`.
                "identity_verified": attestation_subject_matches(
                    draft.record, draft.resolution, state)[0],
            })
        return {"intakes": out}

    @_route("get", "/{intake_id}")
    def intake_card(intake_id: str):
        held = _get(intake_id)
        if held is None:
            return _refusal("UNKNOWN_INTAKE", f"no such intake '{intake_id}'", status=404)
        card = _label(summary_card(held["draft"]), held.get("offline_gold"))
        state = app.state.confirmations.get(intake_id) or ConfirmationState(intake_id=intake_id)
        # `is_ready_to_commit()` asks whether the STATE is complete; this asks whether it is
        # complete *about this draft*. A re-upload re-reads the document and deliberately keeps
        # the existing attestation, so the state stays "ready" over a record the check was never
        # made against — the card must not offer a confirm button the gate is going to refuse.
        applies, why = attestation_subject_matches(held["draft"].record, held["draft"].resolution,
                                                   state)
        card["confirmation"] = {
            "identity_checked_in_person_by": state.identity_checked_in_person_by,
            "identity_checked_at": (state.identity_checked_at.isoformat()
                                    if state.identity_checked_at else None),
            # One value, two spellings — the platform's and the one already on the wire.
            "selected_subject_id": state.selected_patient_id,
            "selected_patient_id": state.selected_patient_id,
            "committed": state.committed,
            "ready_to_commit": applies,
            "blocked_because": why,
        }
        return card

    @_route("get", "/{intake_id}/evidence/{field_path:path}")
    def intake_evidence(intake_id: str, field_path: str):
        """The click-through: the pointer behind a value, plus the source span it names."""
        held = _get(intake_id)
        if held is None:
            return _refusal("UNKNOWN_INTAKE", f"no such intake '{intake_id}'", status=404)
        found = evidence_for(held["draft"], field_path, held["text"])
        if found is None:
            return _refusal("NO_SUCH_FIELD",
                            f"'{field_path}' is not a value this document carries", status=404)
        return found

    @_route("post", "/{intake_id}/select-subject")
    @_route("post", "/{intake_id}/select-patient", documented=False)
    def intake_select_subject(intake_id: str, req: SelectSubjectRequest):
        """Record the candidate a HUMAN picked, after they checked the ID in person."""
        held = _get(intake_id)
        if held is None:
            return _refusal("UNKNOWN_INTAKE", f"no such intake '{intake_id}'", status=404)
        if not req.attested_in_person:
            return _refusal(
                "IDENTITY_NOT_VERIFIED",
                f"selecting a {_pack_noun('subject', 'subject')} requires an explicit in-person "
                f"identity attestation: staff check the {_pack_noun('identifier', 'identity document')} "
                "against the person. The system never confirms identity.")
        draft = held["draft"]
        known = {c.patient_id for c in draft.resolution.candidates}
        if req.patient_id not in known:
            # Selecting off-list would let a client attach any registry row to any document.
            return _refusal("NOT_A_CANDIDATE",
                            f"'{req.patient_id}' is not one of this intake's candidates")
        with app.state.intake_lock:
            state = app.state.confirmations[intake_id]
            if state.committed:
                return _refusal("ALREADY_COMMITTED",
                                "this intake is already committed; the ledger is append-only")
            # The staff member who SELECTS must be the one who attested. Otherwise A attests and
            # selects candidate A, then B selects candidate B and gets a 200 — leaving the record
            # saying A checked the ID for a candidate A never chose, and every ledger row stamped
            # `confirmed_by=A`. The attester and the selector have to be the same person.
            if (state.identity_checked_in_person_by
                    and state.identity_checked_in_person_by != req.staff_id):
                return _refusal(
                    "ATTESTER_MISMATCH",
                    f"identity was attested by {state.identity_checked_in_person_by!r}; only that "
                    f"staff member may select a candidate for this intake. If someone else is "
                    f"taking over, they must make their own in-person check on a new intake.")
            # The digest of what this human is looking at RIGHT NOW: this reading of the
            # document, this candidate list, this row out of it. Computed here, at the moment of
            # attestation, because that is the only moment at which it is a record of a human
            # act — computed later it would be a description of whatever the system happens to
            # hold. `commit` compares it against the same digest recomputed over what is actually
            # being committed, so an attestation cannot outlive the resolution it was made about
            # (CLAUDE.md §Hard constraints 1).
            try:
                fingerprint = subject_fingerprint_for(draft.record, draft.resolution,
                                                      req.patient_id)
                if not state.identity_checked_in_person_by:
                    state = state.attest_identity_checked(
                        req.staff_id, dt.datetime.now(dt.timezone.utc))
                state = state.select_patient(req.patient_id, subject_fingerprint=fingerprint)
            except ValueError as exc:
                return _refusal("SELECTION_REFUSED", str(exc))
            # Re-run the pipeline WITH the selection BEFORE storing the state. Without the re-run
            # the questionnaire is never pre-filled through the API and coverage/billing keep the
            # pre-selection answer; storing the state first meant a failed re-run left
            # `ready_to_commit` true over a stale draft — the same divergence as the re-upload bug.
            selected = next(c.record for c in draft.resolution.candidates
                            if c.patient_id == req.patient_id)
            # Snapshot the identity of what we read, so the write-back can prove nothing moved
            # underneath us while the lock was released. See the CAS check below.
            seen = (id(held), app.state.confirmations.get(intake_id))
            pending_rerun = (draft, selected, state)
        # Outside the lock: the pipeline re-run can be a live model call.
        if True:
            draft, selected, state = pending_rerun
            (harness, offline_gold), refused = _backend_or_refusal()
            if refused is not None:
                return refused
            try:
                rerun = run_intake(
                    held["text"], document_id=draft.record.document.document_id,
                    # Carry the ORIGINAL channel/media type/receipt time. Defaulting them here
                    # rewrote an emailed referral as an uploaded text file, timestamped at the
                    # moment of the ID check rather than when it arrived — and every ledger
                    # pointer thereafter cited the wrong provenance.
                    channel=draft.record.document.channel,
                    media_type=draft.record.document.media_type,
                    received_at=draft.record.document.received_at,
                    intake_id=draft.intake_id, confirmation=state, selected_patient=selected,
                    history=_history(), harness=harness, today=deployment_today(),
                    # The stored index belongs to the stored text. Re-running without it would
                    # rebuild every pointer without its `ocr_bbox` child, so selecting a patient
                    # would silently strip the scan regions off an uploaded document's evidence.
                    media=held.get("media"))
            except Exception:                                        # noqa: BLE001
                log.exception("pipeline re-run failed after selection for intake_id=%r", intake_id)
                return _refusal(
                    "SELECTION_FAILED",
                    "the subject was not selected: re-reading this document against that "
                    "registry record failed. Nothing has been recorded.")
            with app.state.intake_lock:
                # COMPARE-AND-SWAP. The pipeline re-run above happens OUTSIDE the lock (it can be a
                # live model call taking seconds), so anything could have happened to this intake in
                # the meantime — including a commit from another workstation.
                #
                # Without this check the write-back was unconditional, so: workstation A is ready for
                # candidate X and confirms it, writing ledger rows and setting `committed`; meanwhile
                # workstation B's selection of candidate Y finishes its re-run and overwrites that
                # state with its own stale, UNCOMMITTED view. The intake then looks confirmable
                # again, and candidate Y can be confirmed too — a SECOND ledger batch for one
                # document, against a different patient. Refuse instead, and make the operator
                # re-read the current state.
                current_conf = app.state.confirmations.get(intake_id)
                if current_conf is not None and current_conf.committed:
                    return _refusal(
                        "ALREADY_COMMITTED",
                        "this intake was confirmed by someone else while the document was being "
                        "re-read. Nothing has been changed. Reload the card to see what was "
                        "committed.", status=409)
                if (id(app.state.intakes.get(intake_id)), current_conf) != seen:
                    return _refusal(
                        "INTAKE_CHANGED",
                        "this intake changed while the document was being re-read — someone else "
                        "selected a subject or re-uploaded it. Nothing has been changed. Reload "
                        "the card and try again.", status=409)
                # The re-run above re-extracts the document, so its resolution can differ from the
                # one the human was shown a moment ago — a live model is not bit-identical run to
                # run. Check that here rather than storing a state whose fingerprint can never
                # match the draft beside it: the failure then surfaces while the patient is still
                # at the counter, instead of as an unexplainable refusal at confirm time.
                if subject_fingerprint_for(rerun.record, rerun.resolution,
                                           req.patient_id) != state.subject_fingerprint:
                    return _refusal(
                        "SUBJECT_CHANGED",
                        "re-reading this document produced a different candidate list from the "
                        "one shown when identity was checked. Nothing has been recorded. Reload "
                        "the card and check identity against what it shows now.", status=409)
                # Store BACK into the mapping rather than mutating the dict captured before the
                # lock. `intake_upload` REPLACES `app.state.intakes[id]`, so a concurrent upload
                # between the read and this write would have left the post-selection draft in an
                # orphaned dict while the attestation was recorded — `ready_to_commit` true over a
                # pre-selection record, which is exactly the divergence this re-run exists to stop.
                app.state.confirmations[intake_id] = state
                app.state.intakes[intake_id] = {"draft": rerun, "text": held["text"],
                                                "media": held.get("media"),
                                                "offline_gold": offline_gold}
                held = app.state.intakes[intake_id]

        # Both spellings. `selected_subject_id` is the platform's; `selected_patient_id` is what
        # the frontend and the e2e specs read, and it is the SAME value — not a second field that
        # could ever disagree about who was selected.
        return {"intake_id": intake_id,
                "selected_subject_id": state.selected_patient_id,
                "selected_patient_id": state.selected_patient_id,
                "identity_checked_in_person_by": state.identity_checked_in_person_by,
                "ready_to_commit": state.is_ready_to_commit()[0],
                "questionnaire_prefilled": held["draft"].questionnaire.prefilled_count,
                "offline_gold": held.get("offline_gold")}

    @_route("post", "/{intake_id}/confirm")
    def intake_confirm(intake_id: str, req: ConfirmRequest):
        """**The gate.** Writes to the append-only evidence ledger, or refuses."""
        if _get(intake_id) is None:
            return _refusal("UNKNOWN_INTAKE", f"no such intake '{intake_id}'", status=404)
        if not req.attested_in_person:
            return _refusal(
                "IDENTITY_NOT_VERIFIED",
                "confirmation requires an explicit in-person identity attestation from a named "
                "staff member. Nothing is committed without it.")
        with app.state.intake_lock:
            # BOTH halves are read here, under the ONE lock, and the pair below is the pair that
            # commits. Reading `held` outside it and `state` inside was the historical wrong-subject
            # attestation bug in a third disguise: this request captures the pre-selection draft,
            # `select-patient` concurrently stores a patient-B-resolved draft AND B's attestation,
            # and this request then commits the STALE draft stamped with B. The record would name a
            # patient the committed values were never resolved against
            # (CLAUDE.md §Hard constraints 1). Found by review; the same shape has now appeared on
            # the select, re-upload and confirm paths, which is why it is read as a pair, once.
            held = _get(intake_id)
            if held is None:
                return _refusal("UNKNOWN_INTAKE", f"no such intake '{intake_id}'", status=404)
            state = app.state.confirmations[intake_id]
            if state.committed:
                # Checked BEFORE the write. `is_ready_to_commit()` stays true after a commit, so
                # without this the rows landed and only then did `state.commit()` raise — a 422
                # that had already written a duplicate batch superseding the originals.
                return _refusal("ALREADY_COMMITTED",
                                "this intake is already committed; the ledger is append-only, so "
                                "record a correction as a new entry rather than re-confirming")
            if (state.identity_checked_in_person_by
                    and state.identity_checked_in_person_by != req.staff_id):
                return _refusal(
                    "ATTESTER_MISMATCH",
                    f"identity was attested by {state.identity_checked_in_person_by!r}; only that "
                    f"staff member may confirm this intake")
            before = app.state.ledger.count(intake_id)
            # Recomputed from the draft that is ACTUALLY about to be committed, and compared by
            # the ledger against the digest the human's attestation carries. `state` and
            # `held["draft"]` were read together above, so this is the pair that commits — the
            # same discipline, one level down: `intake_id` cannot tell these two apart because it
            # is derived from the document and survives every re-extraction of it.
            #
            # `None` when nothing has been selected, which the ledger refuses on its readiness
            # check before it ever looks at the digest. It is passed explicitly rather than
            # omitted: `commit` has no default for it, so no call site can skip the comparison by
            # forgetting it.
            subject = (subject_fingerprint_for(held["draft"].record, held["draft"].resolution,
                                               state.selected_patient_id)
                       if state.selected_patient_id else None)
            try:
                entries = app.state.ledger.commit(held["draft"].record, state,
                                                  subject_fingerprint=subject,
                                                  field_paths=req.field_paths)
                app.state.confirmations[intake_id] = state.commit()
            except SubjectChanged as exc:
                # Its own code, ahead of the `IdentityNotVerified` clause it inherits from: the
                # counter's next action is different. IDENTITY_NOT_VERIFIED means "nobody has
                # checked"; this means "someone checked, against something else" — the fix is to
                # reload the card and check again, not to attest harder.
                return _refusal("SUBJECT_CHANGED", str(exc))
            except IdentityNotVerified as exc:
                return _refusal("IDENTITY_NOT_VERIFIED", str(exc))
            except AlreadyCommitted as exc:
                return _refusal("ALREADY_COMMITTED", str(exc))
            except (LedgerError, ValueError):
                # Never `str(exc)`: a pydantic ValidationError quotes the rejected input, which
                # here is document content.
                log.exception("commit refused for intake_id=%r", intake_id)
                return _refusal("COMMIT_REFUSED",
                                "this intake could not be committed; see the server log")
        return {"intake_id": intake_id, "committed": True,
                "entries_written": len(entries),
                "ledger_rows_before": before,
                "ledger_rows_after": app.state.ledger.count(intake_id)}

    @_route("get", "/{intake_id}/ledger")
    def intake_ledger(intake_id: str):
        """What was actually committed, with each row's provenance."""
        if _get(intake_id) is None:
            # 404, not an empty list: "no such intake" and "this intake committed nothing" are
            # different answers, and the card is careful about that distinction elsewhere.
            return _refusal("UNKNOWN_INTAKE", f"no such intake '{intake_id}'", status=404)
        rows = app.state.ledger.entries(intake_id)
        return {"intake_id": intake_id, "rows": [{
            "rowid": r["rowid_pk"], "field_path": r["field_path"],
            "value": r["value_json"], "confirmed_by": r["confirmed_by"],
            "confirmed_at": r["confirmed_at"], "supersedes": r["supersedes"],
            "selected_patient_id": r["selected_patient_id"],
            "pointer": app.state.ledger.pointer_of(r).model_dump(mode="json", exclude_none=True),
        } for r in rows]}

    @_route("get", "/{intake_id}/claim-draft")
    def intake_claim_draft(intake_id: str):
        """The post-visit TPA claim draft — the single largest time sink after document reading.

        A DRAFT. A human sends it: this endpoint never contacts a payer.

        Built by ``databridge_intake.claim`` from the **evidence ledger**, not from the working
        draft held in memory. That is not a stylistic preference: this route used to read the draft
        and return bare strings, so a partial confirmation emitted fields nobody had confirmed and
        no value on the response could say where it came from (CLAUDE.md §Hard constraints 2).
        ``build_claim_draft`` has no parameter through which an uncommitted value can reach it.
        """
        from databridge_intake.claim import (AmbiguousPackageRule, ConflictingSelections,
                                             NoPayerToClaimFrom, NothingCommitted,
                                             build_claim_draft)

        held = _get(intake_id)
        if held is None:
            return _refusal("UNKNOWN_INTAKE", f"no such intake '{intake_id}'", status=404)
        state = app.state.confirmations.get(intake_id) or ConfirmationState(intake_id=intake_id)
        if not state.committed:
            return _refusal("NOT_CONFIRMED",
                            "a claim draft is only produced after the intake is confirmed")
        try:
            claim = build_claim_draft(intake_id, app.state.ledger)
        except NothingCommitted as exc:
            return _refusal("NOT_CONFIRMED", str(exc))
        except NoPayerToClaimFrom as exc:
            # A self-pay walk-in has no third party to claim from, and `metrics.py` already refuses
            # to claim the TPA minutes for it — the two must agree.
            return _refusal("NO_PAYER_TO_CLAIM_FROM", str(exc))
        except ConflictingSelections as exc:
            # The committed rows disagree about who this encounter belongs to. Resolving that is an
            # identity decision, and the system does not make those.
            return _refusal("CONFLICTING_SELECTIONS", str(exc))
        except AmbiguousPackageRule as exc:
            return _refusal("AMBIGUOUS_PACKAGE_RULE", str(exc))
        return claim.to_payload()

    @app.get("/impact", dependencies=_AUTH)
    def impact():
        """Time saved, measured over the intakes actually in the queue.

        Every number here is computed from `metrics.measure` over real drafts — nothing is a
        literal. If the queue is empty the answer is zeros, not a projection: a dashboard that
        shows a headline figure with no intakes behind it is exactly the thing that makes a judge
        stop believing the rest of the screen.
        """
        from databridge_intake.metrics import aggregate, baseline, measure

        table = baseline()
        with app.state.intake_lock:
            # Snapshot under the lock. Iterating `app.state.intakes` while an upload inserts into
            # it raises `dictionary changed size during iteration` — and both this route and the
            # queue poll on an interval, so that is a live-demo 500, not a theoretical one.
            held_items = list(app.state.intakes.items())
            confirmed = {i: (app.state.confirmations.get(i) is not None
                             and app.state.confirmations[i].committed)
                         for i in app.state.intakes}
        measured = [measure(held["draft"], confirmed=confirmed.get(intake_id, False), table=table)
                    for intake_id, held in held_items]
        agg = aggregate(measured, table=table)
        return {
            "baseline": {
                "total_min": table.baseline_total,
                "patients_per_morning": table.patients_per_morning,
                # The band comes from config so the page can state its provenance without a
                # literal, and so a change to the table cannot leave the prose behind.
                "band_min_total": table.band_min_total,
                "band_max_total": table.band_max_total,
                "steps": [{
                    "step": s.step, "name": s.name,
                    "baseline_min": s.baseline_midpoint,
                    "with_copilot_min": s.with_copilot_min,
                    "max_saving_min": s.max_saving,
                    "automatable": s.automatable,
                    "never_automatable": s.never_automatable,
                    "note": s.note,
                } for s in table.steps],
            },
            "measured": agg.model_dump(mode="json"),
            "per_intake": [m.model_dump(mode="json") for m in measured],
            # HC1 (2026-10-01, SWEEP UNSURE → VIOLATION): the aggregate sits under "measured", but
            # each saving is `baseline_min - with_copilot_min`, and `with_copilot_min` is an
            # ESTIMATE in the pack's time_baseline.yaml — nothing times a counter. What IS measured
            # is which stages completed for which intakes. Say so on the wire, so no consumer can
            # present the minutes as a stopwatch reading. (The key name stays for compatibility.)
            "saving_basis": {
                "minutes": "estimate",
                "stages_completed": "measured",
                "source": "time_baseline.yaml: with_copilot_min per step",
                "note": ("Minutes saved are the configured baseline minus an ESTIMATE of each "
                         "step's time with this system, counted only for stages that actually "
                         "completed on the intakes in the queue. No counter time was measured."),
            },
        }

    @app.get("/brain/suggested", dependencies=_AUTH)
    def brain_suggested():
        """Suggested questions from `assistant.yaml`, so the demo's prompts are config rather than
        strings compiled into the frontend bundle."""
        from databridge_intake.assistant import assistant_config

        config = assistant_config()
        return {"questions": config.suggested_questions, "scope_note": config.scope_note}

    @app.post("/brain/screen", dependencies=_AUTH)
    def brain_screen(payload: dict):
        """Expose the deterministic guardrail on its own, so the UI can grey out the ask box
        before a question is sent rather than after it is refused."""
        result = check_question(str(payload.get("question", "")))
        return {"refused": result.refused, "code": result.code, "answer": result.answer}
