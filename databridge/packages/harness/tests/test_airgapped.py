"""Airgapped refusal (P1 / 07_PRD §10 mode 1) — no hosted call, no egress, no fallback.

This is also the FIRST test toward 09_FINAL_PRD §21.13's egress policy: the socket-deny
case proves the refusal happens BEFORE any network use.
"""
from __future__ import annotations

import socket
from contextlib import contextmanager

import pytest

from databridge_harness import AirgappedRefusal, FakeBackend, Harness

MSGS = [{"role": "user", "content": "classify"}]


def test_hosted_under_airgapped_is_refused_without_calling_backend(out, cfg):
    fake = FakeBackend('{"label": "x", "score": 1}')
    h = Harness(fake, config=cfg, profile="airgapped")
    with pytest.raises(AirgappedRefusal):
        h.complete_structured("leak_task", MSGS, out)        # resolves to hosted anthropic
    assert fake.call_count == 0                              # refused before any backend call


def test_local_under_airgapped_is_allowed(out, cfg):
    fake = FakeBackend('{"label": "x", "score": 1}')
    h = Harness(fake, config=cfg, profile="airgapped")
    res = h.complete_structured("olla_task", MSGS, out)      # ollama → local → fine
    assert res.parsed.score == 1
    assert res.event.model_used.startswith("ollama_chat/")


def test_remapped_alias_goes_local_under_airgapped(out, cfg):
    # anth_task routes to alias `a`, which airgapped remaps to ollama → not refused
    fake = FakeBackend('{"label": "x", "score": 1}')
    h = Harness(fake, config=cfg, profile="airgapped")
    res = h.complete_structured("anth_task", MSGS, out)
    assert res.event.model_used == "ollama_chat/qwen"


@contextmanager
def deny_external_sockets():
    """Block any TCP connect to a non-loopback address (egress policy seed, §21.13)."""
    orig = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise AssertionError(f"egress to {host!r} denied by airgapped socket policy")
        return orig(self, address)

    socket.socket.connect = guarded
    try:
        yield
    finally:
        socket.socket.connect = orig


def test_airgapped_refusal_under_socket_deny(out, cfg):
    # With external sockets denied, the hosted-under-airgapped call must raise the
    # refusal (not a socket error) — proving the refusal precedes any egress attempt.
    fake = FakeBackend('{"label": "x", "score": 1}')
    h = Harness(fake, config=cfg, profile="airgapped")
    with deny_external_sockets():
        with pytest.raises(AirgappedRefusal):
            h.complete_structured("leak_task", MSGS, out)
        assert fake.call_count == 0
