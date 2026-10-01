"""INT3 (R-022 build cross-track 3): a live read is lifted out of the screen's review band only by
an owner override that covers THIS item AT THIS content digest — `.allows(id, digest)`, never
`id in overrides`."""
from __future__ import annotations

import hashlib

from databridge_agent_runtime.tools import _override_allows


class _Overrides(set):
    """The shape of mcp_ingest's `OverrideSet`: a set of ids plus the digests each approves."""

    def __init__(self, digests):
        self.digests = {k: set(v) for k, v in digests.items()}
        super().__init__(self.digests)

    def allows(self, item_id, digest):
        return bool(digest) and digest in self.digests.get(item_id, ())


def test_an_override_covers_only_the_content_it_approved():
    v1 = hashlib.sha256(b"approved bytes").hexdigest()
    v2 = hashlib.sha256(b"changed bytes").hexdigest()
    got = _Overrides({"doc-1": {v1}})
    assert "doc-1" in got                                    # the id IS listed (non-vacuous)...
    assert _override_allows(got, "doc-1", v1)
    assert not _override_allows(got, "doc-1", v2)            # ...but a changed read is not lifted
    assert not _override_allows(got, "doc-2", v1)
    assert not _override_allows(got, "doc-1", None)


def test_a_bare_id_set_binds_no_content_and_lifts_nothing():
    assert not _override_allows({"doc-1"}, "doc-1", hashlib.sha256(b"x").hexdigest())


def test_the_real_override_set_is_what_the_tool_reads():
    import pytest

    screen = pytest.importorskip("databridge_mcp_ingest.screen")
    d = hashlib.sha256(b"y").hexdigest()
    real = screen.OverrideSet({"doc-1": {d}})
    assert _override_allows(real, "doc-1", d) and not _override_allows(real, "doc-1", "0" * 64)
