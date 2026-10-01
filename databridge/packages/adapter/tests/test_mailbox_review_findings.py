"""Review **R-005** batch 1, `phase3-adapters-mailbox.md`: 8 Critical, 3 High, 1 Medium against
`sources/mailbox.py`. One test per finding, each failing without the fix, per `CLAUDE.md`'s loop.

Dispositions are in `wiki/40-reviews/R-009-mailbox-dispositions.md`. Findings numbered as the
review numbers them:

* **Finding 3** ("raw untrusted message content can drive access decisions") is DONE elsewhere,
  via a centralised compile-time check in `PolicyEngine._compile`
  (`taint.require_trusted_scope_column`). Confirmed here against mailbox's own description.
* **Finding 10** ("valid non-projected `order_by` fields silently ignored") is a FALSE POSITIVE.
  `CapabilityRequest._check_request` already refuses to construct the request codex's trigger
  needs, so the trigger cannot reach `MailboxCore`. Confirmed here.
* Findings 1, 2, 4, 5, 6, 7, 8, 9, 11, 12 are fixed in `sources/mailbox.py` and each gets a test
  below that fails against the pre-fix code.

Fixtures and helpers are imported from `test_mailbox_adapter` rather than duplicated: this file
shares the real finance mailbox drop, the real `roles.yaml`, and the same policy wiring, because a
review-finding test that runs against a mock agrees with whatever the code does.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from databridge_adapter import CapabilityRequest, Principal, Projection, Verdict
from databridge_adapter.capture import CaptureStore
from databridge_adapter.errors import UntrustedColumnInPolicy
from databridge_adapter.sources import mailbox as mailbox_module
from databridge_adapter.sources.mailbox import MailboxContractError, MailboxCore
from databridge_adapter.taint import require_trusted_scope_column
from databridge_core import Trust

from test_mailbox_adapter import (  # noqa: F401 -- fixtures, re-exported for pytest collection
    FINANCE_DROP,
    FINANCE_SOURCES,
    GOVERNMENT_SOURCES,
    _by_message_id,
    _finance_policy,
    _grounded,
    _message_id_of,
    analyst,
    compliance,
    mailbox,
    message_request,
    private_drop,
    purposes,
    store,
)

MULTIPART_MESSAGE_ID_SOURCE = FINANCE_DROP / "msg_00049_102242.eml"


def _principal() -> Principal:
    return Principal(actor="orchestrator@walnut", on_behalf_of="fcc-3",
                     purpose="kyc.screening", grant_id="g")


def _bare_read(fields=("message_id", "subject")) -> CapabilityRequest:
    return CapabilityRequest(
        source_id="mailbox", capability="message_read", principal=_principal(),
        projection=Projection(entity="message", fields=fields, key=("message_id",)),
        order_by=("message_id",), limit=25)


def _one_message_core(tmp_path: Path, raw: bytes, name: str = "msg.eml") -> MailboxCore:
    root = tmp_path / "messages"
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_bytes(raw)
    return MailboxCore(source_id="mailbox", policy=_finance_policy(),
                       capture=CaptureStore(root=tmp_path / "capture"),
                       sources_yaml=FINANCE_SOURCES, root=root)


_HEADERS = (
    b"Date: Mon, 01 Jan 2026 00:00:00 +0000\r\n"
    b"From: sender@walnut.example\r\n"
    b"To: reader@walnut.example\r\n"
    b"Subject: Review finding fixture\r\n"
)


def _eml(message_id: str) -> bytes:
    """A minimal, otherwise-valid message. `message_id` is the RAW header value — pass `""` to
    omit the header entirely."""
    header = f"Message-ID: {message_id}\r\n".encode() if message_id else b""
    return (header + _HEADERS +
            b'Content-Type: text/plain; charset="utf-8"\r\n\r\nBody.\r\n')


# =======================================================================================
# 1 (Critical) — no contract/source binding check at construction.
# =======================================================================================

def test_finding_1_a_contract_for_a_different_source_is_refused_at_construction(store, purposes):
    """A `contract=` for `correspondence` combined with `source_id="mailbox"` used to construct
    successfully: every field declaration, the root path and every trust label would come from
    the government estate's `correspondence` contract while captures and pointers were stamped
    under `mailbox`. False provenance under someone else's name, and cheap to refuse."""
    foreign = mailbox_module.load_source_contract("correspondence", sources_yaml=GOVERNMENT_SOURCES)
    with pytest.raises(MailboxContractError, match="constructed with source_id"):
        MailboxCore(source_id="mailbox", policy=_finance_policy(), capture=store, contract=foreign)


# =======================================================================================
# 2 (Critical) — only two probe fields were checked for a TRUSTED-by-default contract.
# =======================================================================================

def test_finding_2_a_contract_declaring_only_two_fields_untrusted_is_refused(store, purposes):
    """Checking only `body`/`subject`, as the review found, would pass a contract that declared
    exactly those two untrusted under a TRUSTED base default and left every other exposed field —
    including `claimed_from_address`, a renamed `From` header — at that default. Built with
    `trust_class: trusted` so the source-level default (closed separately, for the postgres
    workstream's own finding, by `SourceContract.trust_of` falling back to `trust_class`) does
    not itself mask the gap this fix targets."""
    contract = mailbox_module.load_source_contract("mailbox", sources_yaml=FINANCE_SOURCES)
    entry = dict(contract.entry)
    entry["trust_class"] = "trusted"
    entry["untrusted_fields"] = ["mailbox.message.body", "mailbox.message.subject"]
    narrow = mailbox_module.SourceContract(entry, path=FINANCE_SOURCES, estate=contract.estate)
    with pytest.raises(MailboxContractError, match="attacker-writable"):
        MailboxCore(source_id="mailbox", policy=_finance_policy(), capture=store, contract=narrow)


# =======================================================================================
# 3 (Critical) — DONE elsewhere. Confirmed, not re-fixed.
# =======================================================================================

def test_finding_3_an_untrusted_mailbox_column_cannot_compile_as_a_row_scope(mailbox):
    """`PolicyEngine._compile` refuses, at COMPILE time, to scope any row rule through a
    contract-declared untrusted column (`taint.require_trusted_scope_column`) — the centralised
    fix for the same hole five independent reviews found from five angles (R-005 triage, root
    cause 1). Confirmed here against mailbox's own description: every field this Core exposes is
    untrusted, so a hypothetical `roles.yaml` change scoping `mailbox.message` by `from_address`
    — exactly the header-based filter the estate's own comment says was REMOVED rather than
    rewritten — would fail to compile."""
    description = mailbox.describe()
    with pytest.raises(UntrustedColumnInPolicy):
        require_trusted_scope_column(description, entity="message", column="from_address",
                                     rule_id="test:hypothetical-mailbox-row-scope",
                                     position="row_filters comparison column")


# =======================================================================================
# 4 (Critical) — charset conversion emitted as verbatim evidence.
# =======================================================================================

def test_finding_4_a_non_utf8_charset_body_is_derived_not_verbatim(tmp_path, purposes):
    """§4a: if the emitted bytes differ from the cited bytes, the pointer is `derived` or the
    value is ungrounded. Decoding ISO-8859-1 bytes into a Python `str` is a genuine representation
    change — unlike UTF-8/US-ASCII, re-encoding the decoded text does not reproduce anything
    resembling "the bytes on disk" for an interchange format that assumes UTF-8. The fix routes a
    non-UTF-8-compatible charset through the SAME registered `binary_part_digest@v1` derivation a
    genuinely undecodable part already takes, rather than a silently transformed `email_part`
    pointer."""
    body = "café, prière".encode("iso-8859-1")
    core = _one_message_core(tmp_path, (
        b"Message-ID: <latin1@walnut.example>\r\n" + _HEADERS +
        b'Content-Type: text/plain; charset="iso-8859-1"\r\n\r\n' + body + b"\r\n"))
    result = core.resolve(CapabilityRequest(
        source_id="mailbox", capability="message_read", principal=_principal(),
        projection=Projection(entity="message", fields=("message_id", "body"),
                              key=("message_id",)),
        order_by=("message_id",), limit=5))
    cell = _grounded(result.rows.rows[0])[1]
    assert cell.pointer.kind == "derived"
    assert cell.pointer.derivation.startswith("binary_part_digest@v1")
    assert core.verify(cell.pointer) is Verdict.VERIFIED_AS_OF_CAPTURE


# =======================================================================================
# 5 (Critical) — `_verify`'s derived branch reported positive verdicts without replaying.
# =======================================================================================

def test_finding_5_a_derived_pointer_with_a_tampered_digest_is_drift_material(mailbox,
                                                                              message_request):
    """"Provenance structurally present" (every parent still verifies) is NOT "derivation
    verified" (the rule, replayed today, still produces the cited value). The old branch combined
    parent verdicts and returned the join; it never parsed or replayed `pointer.derivation`, so a
    derived pointer whose content digest had been tampered with — impossible to even express
    before this fix, since derived pointers carried no content digest at all — verified cleanly."""
    result = mailbox.resolve(message_request(fields=("message_id", "body"), limit=250))
    cell = _by_message_id(result)[_message_id_of(MULTIPART_MESSAGE_ID_SOURCE)][1]
    assert cell.pointer.kind == "derived"
    assert mailbox.verify(cell.pointer) is Verdict.VERIFIED_AS_OF_CAPTURE, (
        "sanity: the untampered pointer must still verify")

    tampered = cell.pointer.model_copy(update={
        "snapshot": cell.pointer.snapshot.model_copy(update={"content_hash": "0" * 64})})
    verdict, note = mailbox.verify_with_note(tampered)
    assert verdict is Verdict.DRIFT_MATERIAL
    assert "no longer produces the value" in note


def test_finding_5_a_derived_pointer_with_an_unregistered_derivation_is_unverifiable(
        mailbox, message_request):
    """The replay must actually run: an unknown derivation is UNVERIFIABLE, never a silent
    positive on the strength of the parents alone."""
    result = mailbox.resolve(message_request(fields=("message_id", "body"), limit=250))
    cell = _by_message_id(result)[_message_id_of(MULTIPART_MESSAGE_ID_SOURCE)][1]
    forged = cell.pointer.model_copy(update={"derivation": "nonexistent_rule@v1()"})
    verdict, note = mailbox.verify_with_note(forged)
    assert verdict is Verdict.UNVERIFIABLE
    assert "not registered" in note


# =======================================================================================
# 6 (Critical) — the `email_part` branch never validated the pointer's own metadata.
# =======================================================================================

def test_finding_6_a_swapped_column_is_drift_material(mailbox, message_request):
    result = mailbox.resolve(message_request(fields=("message_id", "subject"), limit=1))
    cell = _grounded(result.rows.rows[0])[1]
    assert cell.pointer.column == "subject"
    forged = cell.pointer.model_copy(update={"column": "sent_at"})
    verdict, note = mailbox.verify_with_note(forged)
    assert verdict is Verdict.DRIFT_MATERIAL
    assert "belongs to a different field" in note


def test_finding_6_a_swapped_message_id_is_drift_material(mailbox, message_request):
    result = mailbox.resolve(message_request(fields=("message_id", "subject"), limit=2))
    rows = list(_by_message_id(result).items())
    (_mid_a, row_a), (mid_b, _row_b) = rows[0], rows[1]
    cell_a = _grounded(row_a)[1]
    forged = cell_a.pointer.model_copy(update={"message_id": mid_b})
    verdict, note = mailbox.verify_with_note(forged)
    assert verdict is Verdict.DRIFT_MATERIAL
    assert "disagree about which message" in note


def test_finding_6_an_upgraded_trust_label_is_drift_material(mailbox, message_request):
    result = mailbox.resolve(message_request(fields=("message_id", "subject"), limit=1))
    cell = _grounded(result.rows.rows[0])[1]
    assert cell.pointer.trust is Trust.UNTRUSTED
    forged = cell.pointer.model_copy(update={"trust": Trust.TRUSTED})
    verdict, note = mailbox.verify_with_note(forged)
    assert verdict is Verdict.DRIFT_MATERIAL
    assert "Trust is a lookup" in note


# =======================================================================================
# 7 (Critical) — principal handling could not compute FORBIDDEN correctly.
# =======================================================================================

def test_finding_7_a_principal_that_exists_but_cannot_read_mailbox_is_forbidden(
        mailbox, message_request, analyst):
    """`analyst-7` (`credit_analyst`) IS in the directory, so the old check — "does
    `binding_for` raise?" — let it through with `VERIFIED_AS_OF_CAPTURE`. But
    `test_acceptance_6_...` in `test_mailbox_adapter.py` already establishes
    `"mailbox" in model.require_role("credit_analyst").deny_sources`: this principal was never
    entitled to read a mailbox pointer at all. The fix re-runs authorisation and gets FORBIDDEN;
    a request with no principal is unaffected."""
    cell = _grounded(mailbox.resolve(message_request(limit=1)).rows.rows[0])[1]
    assert mailbox.verify(cell.pointer, principal=analyst) is Verdict.FORBIDDEN
    assert mailbox.verify(cell.pointer) is Verdict.VERIFIED_AS_OF_CAPTURE


# =======================================================================================
# 8 (Critical) — missing / duplicate Message-ID accepted despite being the entity key.
# =======================================================================================

def test_finding_8_a_message_with_no_message_id_is_refused(tmp_path, purposes):
    core = _one_message_core(tmp_path, _eml(""))
    with pytest.raises(mailbox_module.MessageNotReadable, match="Message-ID"):
        core.resolve(_bare_read())


def test_finding_8_two_messages_sharing_one_message_id_are_refused(tmp_path, purposes):
    root = tmp_path / "messages"
    root.mkdir(parents=True)
    (root / "a.eml").write_bytes(_eml("<dup@walnut.example>"))
    (root / "b.eml").write_bytes(_eml("<dup@walnut.example>"))
    core = MailboxCore(source_id="mailbox", policy=_finance_policy(),
                       capture=CaptureStore(root=tmp_path / "capture"),
                       sources_yaml=FINANCE_SOURCES, root=root)
    with pytest.raises(mailbox_module.MessageNotReadable, match="not unique"):
        core.resolve(_bare_read())


# =======================================================================================
# 9 (High) — resolution mode could not resolve emitted DERIVED pointers.
# =======================================================================================

def test_finding_9_resolution_mode_can_re_resolve_a_derived_pointer(mailbox, message_request):
    """A top-level `derived` pointer used to carry no `message_id` of its own — only its
    `derived_from` parents did — so `_read`'s `wanted = {p.message_id for p in req.resolve if
    p.message_id}` silently dropped it, and a `resolve` set made up entirely of derived pointers
    collected an EMPTY `wanted`, returning zero rows after scanning and capturing the whole drop."""
    result = mailbox.resolve(message_request(fields=("message_id", "body"), limit=250))
    cell = _by_message_id(result)[_message_id_of(MULTIPART_MESSAGE_ID_SOURCE)][1]
    assert cell.pointer.kind == "derived"
    assert cell.pointer.message_id, "a derived pointer must carry its own message_id"

    resolved = mailbox.resolve(CapabilityRequest(
        source_id="mailbox", capability="message_read", principal=_principal(),
        projection=Projection(entity="message", fields=("message_id", "body"),
                              key=("message_id",)),
        resolve=(cell.pointer,), limit=5))
    assert len(resolved.rows.rows) == 1, (
        "a resolve set made entirely of derived pointers must not silently return zero rows")
    again = _grounded(resolved.rows.rows[0])[1]
    assert again.value == cell.value


# =======================================================================================
# 10 (High) — FALSE POSITIVE. See disposition.
# =======================================================================================

def test_finding_10_dismissed_a_non_projected_order_by_field_cannot_reach_mailboxcore(compliance):
    """`CapabilityRequest._check_request` (databridge_adapter/contract.py) already refuses to
    construct a request whose `order_by` names a field outside the projection — so the trigger
    codex describes (fields not including `sent_at`, `order_by=("sent_at", ...)`) cannot reach
    `MailboxCore` at all. The reviewer saw only `mailbox.py` in isolation."""
    with pytest.raises(ValueError, match="not in the projection"):
        CapabilityRequest(
            source_id="mailbox", capability="message_read", principal=compliance,
            projection=Projection(entity="message", fields=("subject", "message_id"),
                                  key=("message_id",)),
            order_by=("sent_at", "message_id"), limit=25)


# =======================================================================================
# 11 (High) — the snapshot digest omitted MIME parts that affect the emitted cell.
# =======================================================================================

def _multipart_eml(html_snippet: bytes) -> bytes:
    return (
        b"Message-ID: <finding11@walnut.example>\r\n" + _HEADERS +
        b'Content-Type: multipart/alternative; boundary="B1"\r\n\r\n'
        b"--B1\r\n"
        b'Content-Type: text/plain; charset="utf-8"\r\n\r\n'
        b"Plain text branch.\r\n"
        b"--B1\r\n"
        b'Content-Type: text/html; charset="utf-8"\r\n\r\n'
        b"<html>" + html_snippet + b"</html>\r\n"
        b"--B1--\r\n")


def test_finding_11_the_read_level_digest_covers_every_locator_not_just_the_first(
        tmp_path, purposes):
    """`INJ-04` in both estates lives ONLY in the second (HTML) alternative part. `_raw_row` used
    to digest `part[0]` alone, so two drops differing ONLY in that second part digested
    identically at the read level — the exact shape that would let a planted-payload edit slip
    past a snapshot comparison."""
    core_a = _one_message_core(tmp_path / "a", _multipart_eml(b"visible text"))
    core_b = _one_message_core(tmp_path / "b", _multipart_eml(b"DIFFERENT hidden text"))
    req = CapabilityRequest(
        source_id="mailbox", capability="message_read", principal=_principal(),
        projection=Projection(entity="message", fields=("message_id", "body"),
                              key=("message_id",)),
        order_by=("message_id",), limit=5)
    result_a = core_a.resolve(req)
    result_b = core_b.resolve(req)
    assert result_a.rows.snapshot.content_hash != result_b.rows.snapshot.content_hash


# =======================================================================================
# 12 (Medium) — live-state membership cached indefinitely with no invalidation.
# =======================================================================================

def test_finding_12_verify_detects_a_deleted_message_without_close(private_drop, message_request):
    """The old `_live_message_ids` cached its header-only pass for the Core's entire lifetime
    with no invalidation at all — only an explicit `close()` (as
    `test_acceptance_8_editing_the_live_message_does_not_move_the_old_citation` in
    `test_mailbox_adapter.py` calls before its own re-verify) forced a re-scan. This proves the
    SAME scenario now self-invalidates, with no `close()` call, via a cheap per-call
    `stat()`-based drop signature."""
    core, root = private_drop
    result = core.resolve(message_request(limit=3))
    cell = _grounded(result.rows.rows[0])[1]

    # Warm the live-membership cache BEFORE the drop changes.
    assert core.verify(cell.pointer) is Verdict.VERIFIED_AS_OF_CAPTURE

    victim = next(p for p in root.glob("*.eml") if _message_id_of(p) == cell.pointer.message_id)
    victim.unlink()
    # Deliberately no core.close() — that is the point of this test.

    verdict, note = core.verify_with_note(cell.pointer)
    assert verdict is Verdict.VERIFIED_AS_OF_CAPTURE
    assert note and "no longer in the live drop" in note
