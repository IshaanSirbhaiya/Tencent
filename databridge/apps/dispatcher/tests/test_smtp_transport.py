"""The transport: three strings in, a message id out, and two screens before the socket.

It is the one thing in this repo that can put a message outside the building, so it gets tested for
what it refuses at least as carefully as for what it does.
"""
from __future__ import annotations

import pytest

import smtp_transport
from smtp_transport import (
    DEFAULT_SENDER,
    DEFAULT_SMTP_HOST,
    DEFAULT_SMTP_PORT,
    SendRefused,
    SmtpTransport,
)


def test_it_defaults_to_mailpit_on_loopback(monkeypatch):
    """Local by default: development mail goes to a catcher on this machine, never outward."""
    for name in ("DATABRIDGE_SMTP_HOST", "DATABRIDGE_SMTP_PORT", "DATABRIDGE_SMTP_FROM",
                 "DATABRIDGE_SMTP_USER", "DATABRIDGE_SMTP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    transport = SmtpTransport()
    assert (transport.host, transport.port) == (DEFAULT_SMTP_HOST, DEFAULT_SMTP_PORT) \
        == ("127.0.0.1", 1025)
    assert transport.sender == DEFAULT_SENDER
    assert transport.username is None and transport.password is None, (
        "an unset credential must mean an unauthenticated local relay, never a baked-in one")


def test_the_default_sender_can_never_resolve():
    """`.invalid` is reserved by RFC 2606. If this default ever reaches a real relay the mail
    bounces, rather than arriving from an address a patient might reply to."""
    assert DEFAULT_SENDER.endswith(".invalid")


def test_configuration_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("DATABRIDGE_SMTP_HOST", "mail.example.test")
    monkeypatch.setenv("DATABRIDGE_SMTP_PORT", "2525")
    monkeypatch.setenv("DATABRIDGE_SMTP_FROM", "clinic@example.test")
    transport = SmtpTransport()
    assert (transport.host, transport.port, transport.sender) == \
        ("mail.example.test", 2525, "clinic@example.test")


@pytest.mark.parametrize("raw", ["not-a-number", "0", "70000", "-1"])
def test_a_malformed_port_is_refused_rather_than_silently_defaulted(monkeypatch, raw):
    """A silently ignored value would send to a different server than the operator believes."""
    monkeypatch.setenv("DATABRIDGE_SMTP_PORT", raw)
    with pytest.raises(SendRefused):
        SmtpTransport()


def test_describe_never_names_the_password(monkeypatch):
    transport = SmtpTransport(host="h", port=25, sender="s@example.test",
                              username="u", password="hunter2")
    assert "hunter2" not in transport.describe()
    assert "u" not in transport.describe().replace("smtp://", "")


@pytest.mark.parametrize("field,value", [
    ("to", "patient@example.test\r\nBcc: attacker@evil.test"),
    ("to", "patient@example.test\nBcc: attacker@evil.test"),
    ("subject", "Results\r\nBcc: attacker@evil.test"),
])
def test_a_header_that_could_carry_a_second_header_is_refused_before_any_connection(field, value):
    """The second of the two screens. `databridge_outbox.models` refuses these at the type; this
    one is beside the socket, which is where the damage would be done."""
    transport = SmtpTransport(host="127.0.0.1", port=1)      # nothing listens; must not get there
    kwargs = {"to": "patient@example.test", "subject": "Results", "body": "hello"}
    kwargs[field] = value
    with pytest.raises(SendRefused, match="line break"):
        transport.send(**kwargs)


def test_an_empty_recipient_or_body_is_refused():
    transport = SmtpTransport(host="127.0.0.1", port=1)
    with pytest.raises(SendRefused, match="no recipient"):
        transport.send(to="  ", subject="s", body="b")
    with pytest.raises(SendRefused, match="empty body"):
        transport.send(to="p@example.test", subject="s", body="  ")


def test_a_dead_mail_server_raises_rather_than_pretending_to_have_sent():
    """A silent failure here is a message the clinic believes it sent."""
    transport = SmtpTransport(host="127.0.0.1", port=1, timeout=0.5)
    with pytest.raises(smtp_transport.TransportError, match="send FAILED"):
        transport.send(to="p@example.test", subject="s", body="b")


def test_availability_probe_is_false_for_a_closed_port():
    assert smtp_transport.smtp_available("127.0.0.1", 1, timeout=0.3) is False
