"""INT2 #2 (R-021 follow-up 4) — an agent-runtime `Approver` decides an adaptation only when a
REGISTERED authenticator issued it (`databridge_agent_runtime.is_issued_principal`), in addition
to the type check. A hand-built `Approver` is a claim, not an authentication (hard constraint 4).
"""
from __future__ import annotations

import pytest

from databridge_adapter_agents.adapt import AdaptationRefused, principal_of

ar = pytest.importorskip("databridge_agent_runtime")


def test_a_hand_built_approver_is_refused():
    forged = ar.Approver(principal_id="alice@x.example", authenticated_by="sso")
    assert not ar.is_issued_principal(forged)                      # the rule the code uses
    with pytest.raises(AdaptationRefused, match="issued"):
        principal_of(forged, "approve_adaptation")


def test_an_issued_approver_is_accepted():
    auth = ar.register_authenticator("int2_test_mechanism")
    issued = auth.issue("alice@x.example")
    assert ar.is_issued_principal(issued)                          # non-vacuous
    who = principal_of(issued, "approve_adaptation")
    assert who["principal_id"] == "alice@x.example"
    assert who["authenticated_by"] == issued.authenticated_by
