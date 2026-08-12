"""A real message, over a real SMTP conversation, landing in a real mailbox.

`STATUS.md`'s standing rule — *a green suite proves nothing about whether the app starts* — is the
reason this exists. Every other test in this app uses a recording transport, which proves the gate
and proves nothing about whether a message can actually leave the process. This one opens a socket
to Mailpit (SMTP 1025, web UI 8025), sends, and then reads the message back out of Mailpit's API to
check that what arrived is what a human approved.

**It skips cleanly when Mailpit is not running.** A test that needs a container is a test that rots
(MCP_RUNBOOK.md §6), and it must not break the suite for everyone. The skip is on a socket probe,
not on an environment variable somebody has to remember to set.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from smtp_transport import DEFAULT_SMTP_HOST, DEFAULT_SMTP_PORT, SmtpTransport, smtp_available

from dispatcher import Dispatcher

NURSE = "Nurse Lim"
MAILPIT_API = "http://127.0.0.1:8025/api/v1"

pytestmark = pytest.mark.skipif(
    not smtp_available(DEFAULT_SMTP_HOST, DEFAULT_SMTP_PORT),
    reason=f"nothing is listening on {DEFAULT_SMTP_HOST}:{DEFAULT_SMTP_PORT} "
           f"(start Mailpit to run the real-send test)")


def _mailpit(path: str):
    try:
        with urllib.request.urlopen(f"{MAILPIT_API}{path}", timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError):
        pytest.skip("Mailpit's HTTP API is not reachable on 8025")


def test_an_approved_draft_really_leaves_the_process(store, draft):
    """The whole path, end to end: committed evidence -> monitor -> draft -> human -> SMTP.

    Nothing is stubbed. If this passes, a message with the approved subject and body is sitting in
    Mailpit's inbox, addressed to the address the registry holds for the patient a named human
    selected in person.
    """
    store.approve(draft.draft_id, approved_by=NURSE)
    receipt, = Dispatcher(store, SmtpTransport()).run_once()

    assert receipt.message_id.startswith("<") and receipt.message_id.endswith(">")
    row = store.current(draft.draft_id)
    assert row.status == "sent" and row.message_id == receipt.message_id
    assert row.approved_by == NURSE

    # And it actually arrived. Matched on Message-Id — the id the outbox recorded — so this checks
    # that the record and the delivery are about the same message, not merely that mail exists.
    # Mailpit stores the id without the angle brackets RFC 5322 requires on the wire.
    wanted = receipt.message_id.strip("<>")
    inbox = _mailpit("/messages?limit=200").get("messages", [])
    landed = next((m for m in inbox if m.get("MessageID") == wanted), None)
    assert landed is not None, f"nothing in Mailpit has Message-Id {wanted}"
    assert landed["Subject"] == draft.subject.value
    assert landed["To"][0]["Address"] == draft.recipient.address


def test_an_unapproved_draft_never_reaches_mailpit(store, draft):
    """The negative, against the real transport. The gate is not a property of the fake."""
    from databridge_outbox.models import NotApproved

    before = _mailpit("/messages").get("total", 0)
    dispatcher = Dispatcher(store, SmtpTransport())
    assert dispatcher.run_once() == []
    with pytest.raises(NotApproved):
        dispatcher.dispatch(draft.draft_id)
    assert _mailpit("/messages").get("total", 0) == before
