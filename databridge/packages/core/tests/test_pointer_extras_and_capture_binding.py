"""`extra="forbid"` on SourcePointer, and the capture binding it had to ship alongside.

Why these two are one test file. Before this change, `SourcePointer` used Pydantic's default
`extra` behaviour, which is to **silently ignore** an unknown keyword argument. So every
sample in ADAPTER_AGENT_SPEC that passed `trust=...` against the then-current model would have
constructed a pointer with no trust at all, and the subsequent `.trust` access would have
raised `AttributeError` somewhere far away from the mistake.

That is why the spec insisted the fields and the `forbid` land in the SAME change: adding
`trust` without `forbid` fixes the near case and leaves the trap armed for the next proposed
field; adding `forbid` without the fields breaks every spec sample loudly instead of quietly,
which is better but still broken.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from databridge_core.provenance import SourcePointer, Trust


def _leaf(**over) -> SourcePointer:
    kwargs = dict(source_id="s", kind="table_cell", table="t", column="c", row_id="1",
                  trust=Trust.TRUSTED)
    kwargs.update(over)
    return SourcePointer(**kwargs)   # trust-gate: exempt - defaults above supply trust


# --- extra="forbid" ------------------------------------------------------------------------

def test_an_unknown_field_is_refused_not_dropped():
    with pytest.raises(ValidationError):
        _leaf(risk_certificate="looks plausible, does not exist")


def test_a_misspelled_real_field_is_refused():
    """The realistic case: `capture_digest` typed as `captured_digest` used to vanish."""
    with pytest.raises(ValidationError):
        _leaf(captured_digest="sha256:abc")


def test_a_field_that_only_exists_in_a_later_phase_is_refused_today():
    """`tier` and `pin` are proposed for SnapshotIdentity in Phase 3 and are not here yet.

    Silently accepting them would make a Phase 3 draft appear to work while pinning nothing.
    """
    with pytest.raises(ValidationError):
        _leaf(tier="T0")


def test_known_fields_still_construct():
    assert _leaf().kind == "table_cell"


# --- capture_digest ------------------------------------------------------------------------

def test_capture_digest_binds_a_pointer_to_the_bytes_that_were_parsed():
    ptr = SourcePointer(source_id="mail", kind="email_part", message_id="<m@x>",
                        part="attachment:0", trust=Trust.UNTRUSTED,
                        capture_digest="sha256:" + "ab" * 32)
    assert ptr.capture_digest.startswith("sha256:")


def test_capture_digest_is_available_on_an_ocr_pointer():
    """`ocr_bbox` resolves against the Capture Store, not the live source (spine D6)."""
    ptr = SourcePointer(source_id="scan", kind="ocr_bbox", media_id="m", page=1,
                        x0=0.0, y0=0.0, x1=1.0, y1=1.0, ocr_conf=0.9,
                        trust=Trust.UNTRUSTED, capture_digest="sha256:" + "cd" * 32)
    assert ptr.capture_digest is not None


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_blank_capture_digest_is_refused(blank):
    """A blank digest is worse than none: it looks like proof and binds to nothing, while
    still satisfying any downstream `if ptr.capture_digest:` check."""
    with pytest.raises(ValidationError, match="content address"):
        _leaf(capture_digest=blank)


def test_capture_digest_stays_optional_for_now():
    """Existing pointers predate the Capture Store; requiring it here would break them.

    Recorded as a KNOWN GAP rather than a design choice: once the Capture Store exists,
    `email_part` / `ocr_bbox` / any T3 pointer must require it, and that is a Phase 3 change
    to `_REQUIRED_FIELDS`, not a new concept.
    """
    ptr = SourcePointer(source_id="mail", kind="email_part", message_id="<m@x>",
                        part="body", trust=Trust.UNTRUSTED)
    assert ptr.capture_digest is None


# --- round-tripping, because pointers are persisted as JSON ---------------------------------

def test_a_pointer_round_trips_through_json_under_forbid():
    """`forbid` must not break the ~10 call sites that rehydrate pointers from stored JSON."""
    ptr = SourcePointer(source_id="mail", kind="email_part", message_id="<m@x>",
                        part="attachment:0", trust=Trust.UNTRUSTED,
                        capture_digest="sha256:" + "ef" * 32,
                        child=_leaf())
    assert SourcePointer.model_validate_json(
        ptr.model_dump_json()) == ptr   # trust-gate: exempt - round-trips a LABELLED pointer


def test_json_written_before_this_change_still_loads():
    """Backwards compatibility: an old row has neither new key. Optional means it loads."""
    old = '{"source_id":"s","kind":"table_cell","table":"t","column":"c","row_id":"1"}'
    ptr = SourcePointer.model_validate_json(old)   # trust-gate: exempt - the legacy row IS the test
    assert ptr.trust is None and ptr.capture_digest is None
