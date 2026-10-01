"""The one place in this repo that can put a message outside the building.

`docs/hackathon/GRAPH_AGENT_AND_MCP.md` §3 rule 1: *the agent never holds send credentials*. This
module is where those credentials would live, and it is deliberately as small and as stupid as a
thing with that power can be: it takes three strings and an address that something else already
established, opens an SMTP connection, and returns a message id. It makes no decisions. It reads no
outbox, evaluates no condition, and has never heard of a patient.

**Configuration is environment, never repo.** Host and port default to ``127.0.0.1:1025``, which is
Mailpit on a developer's machine (web UI on :8025) — so a send in development genuinely leaves the
process and is genuinely visible, rather than being a mock that proves nothing. Username and
password have **no defaults at all**: an unset credential means an unauthenticated local relay,
which is the only kind that should ever be the default. There are no secrets in this file and none
in this repository.

**Two screens before anything is written to a socket.** The address and the subject are checked for
CR/LF, because an address carrying a newline can append an SMTP header of its own — a ``Bcc:`` to
somewhere else being the interesting case. ``databridge_outbox.models`` already refuses those at the
type. This is the second check, and it is here rather than only there because this is the code that
would actually do the damage, and a guard that lives one import away from the socket is a guard that
survives somebody writing a second caller.

Nothing in this module decides WHO a message goes to. That decision has exactly two permitted
sources (``databridge_outbox.recipients``) and neither of them is reachable from here.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import smtplib
import socket
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Optional

SMTP_HOST_ENV = "DATABRIDGE_SMTP_HOST"
SMTP_PORT_ENV = "DATABRIDGE_SMTP_PORT"
SMTP_FROM_ENV = "DATABRIDGE_SMTP_FROM"
SMTP_USER_ENV = "DATABRIDGE_SMTP_USER"
SMTP_PASSWORD_ENV = "DATABRIDGE_SMTP_PASSWORD"
SMTP_STARTTLS_ENV = "DATABRIDGE_SMTP_STARTTLS"
SMTP_TIMEOUT_ENV = "DATABRIDGE_SMTP_TIMEOUT"

#: Mailpit's SMTP port on a developer machine. A real SMTP conversation against a real server that
#: happens to keep everything — so the demo shows a message arriving, and nothing reaches a person.
DEFAULT_SMTP_HOST = "127.0.0.1"
DEFAULT_SMTP_PORT = 1025
DEFAULT_SMTP_TIMEOUT = 10.0

#: ``.invalid`` is reserved by RFC 2606 and can never resolve. If this default is ever used against
#: a real relay, the mail bounces instead of arriving from an address somebody might reply to.
DEFAULT_SENDER = "walnut@databridge.invalid"


def message_id_domain(sender: str) -> str:
    """The Message-Id's domain: the sender's own, so the two can never disagree.

    Accepts a bare address or a ``Name <addr>`` form; anything without an ``@`` falls back to the
    default sender's domain (still ``.invalid``).
    """
    addr = sender.strip()
    if "<" in addr and addr.endswith(">"):
        addr = addr[addr.rindex("<") + 1:-1]
    domain = addr.rpartition("@")[2].strip()
    return domain or DEFAULT_SENDER.rpartition("@")[2]

_HEADER_INJECTION = re.compile(r"[\r\n\x00]")


class TransportError(RuntimeError):
    """The message could not be handed to the mail server."""


class SendRefused(TransportError):
    """The message was refused before any connection was opened."""


def _env_port() -> int:
    raw = os.environ.get(SMTP_PORT_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_SMTP_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise SendRefused(f"{SMTP_PORT_ENV}={raw!r} is not a port number") from exc
    if not 1 <= port <= 65535:
        raise SendRefused(f"{SMTP_PORT_ENV}={port} is outside 1-65535")
    return port


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def smtp_available(host: Optional[str] = None, port: Optional[int] = None,
                   timeout: float = 0.75) -> bool:
    """Whether something is listening where the transport would connect.

    Used by the end-to-end test to skip cleanly when Mailpit is not running. A test that needs a
    container must not break the suite for everyone — the MCP runbook's §6 lesson, applied before
    it has to be re-learned.
    """
    target = (host or os.environ.get(SMTP_HOST_ENV) or DEFAULT_SMTP_HOST,
              port if port is not None else _env_port())
    try:
        with socket.create_connection(target, timeout=timeout):
            return True
    except OSError:
        return False


class SmtpTransport:
    """Send one message over SMTP. Configured from the environment, or explicitly for a test."""

    name = "smtp"

    def __init__(self, *,
                 host: Optional[str] = None,
                 port: Optional[int] = None,
                 sender: Optional[str] = None,
                 timeout: Optional[float] = None,
                 username: Optional[str] = None,
                 password: Optional[str] = None,
                 starttls: Optional[bool] = None) -> None:
        self.host = host or os.environ.get(SMTP_HOST_ENV) or DEFAULT_SMTP_HOST
        self.port = port if port is not None else _env_port()
        self.sender = sender or os.environ.get(SMTP_FROM_ENV) or DEFAULT_SENDER
        self.timeout = timeout if timeout is not None else float(
            os.environ.get(SMTP_TIMEOUT_ENV) or DEFAULT_SMTP_TIMEOUT)
        # No defaults. An unset credential is an unauthenticated local relay, which is what
        # 127.0.0.1:1025 is; anything else has to be configured deliberately by an operator.
        self.username = username if username is not None else os.environ.get(SMTP_USER_ENV)
        self.password = password if password is not None else os.environ.get(SMTP_PASSWORD_ENV)
        self.starttls = starttls if starttls is not None else _env_flag(SMTP_STARTTLS_ENV)

    def describe(self) -> str:
        """Where this transport points, for a log line. **Never the password.**"""
        return f"smtp://{self.host}:{self.port} as {self.sender}"

    def send(self, *, to: str, subject: str, body: str) -> str:
        """Send, and return the ``Message-Id`` the recipient's mail log will show.

        The id is generated here rather than read back from the server, because SMTP does not
        return one — and the outbox records it, so "which message was this row" has to be
        answerable from something we chose and sent.
        """
        for field, value in (("to", to), ("subject", subject), ("from", self.sender)):
            if _HEADER_INJECTION.search(value or ""):
                raise SendRefused(
                    f"send REFUSED: the {field} header contains a line break, which would let it "
                    f"carry a second header (a Bcc, typically) into the message")
        if not (to or "").strip():
            raise SendRefused("send REFUSED: no recipient")
        if not (body or "").strip():
            raise SendRefused("send REFUSED: an empty body is a message nobody reviewed")

        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = to
        message["Subject"] = subject
        message["Date"] = dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
        message_id = make_msgid(domain=message_id_domain(self.sender))
        message["Message-Id"] = message_id
        # Says out loud, in the message itself, that a person approved this. The alternative is a
        # patient receiving clinic mail with no way to tell whether anybody read it first.
        message["X-Walnut-Human-Approved"] = "yes"
        message.set_content(body)

        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
                if self.starttls:
                    smtp.starttls()
                if self.username and self.password:
                    smtp.login(self.username, self.password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise TransportError(
                f"send FAILED via {self.describe()}: {type(exc).__name__}: {exc}") from exc
        return message_id


__all__ = [
    "DEFAULT_SENDER",
    "DEFAULT_SMTP_HOST",
    "DEFAULT_SMTP_PORT",
    "SendRefused",
    "SmtpTransport",
    "TransportError",
    "smtp_available",
]
