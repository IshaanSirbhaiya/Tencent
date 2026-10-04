"""R-029 TIDY2 item 4: ONE definition of the gateway sign-in mechanism, at the source.

`platform_runtime` registered the gateway with the agent runtime as ``gateway:auth`` while the auth
layer (and every approval the shared gates record) named the SAME mechanism ``gateway``. Activity
had to rename it at read time. Now the producer's constants ARE the auth layer's, so a new record
never diverges; Activity still reads the old name of records written before (an alias for history,
not a second definition).
"""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("dev", [False, True])
def test_the_runtime_records_the_auth_layers_mechanism(dev):
    from databridge_agent_runtime.runtime import _decided_by

    from databridge_gateway import auth, platform_runtime

    assert platform_runtime.GATEWAY_MECHANISM is auth.GATEWAY_MECHANISM
    assert platform_runtime.GATEWAY_DEV_MECHANISM is auth.DEV_MECHANISM
    p = auth.AuthenticatedPrincipal(principal_id="analyst-7", dev_mode=dev)
    who = platform_runtime.approver_for(p)
    # what the runtime writes on a decision event is what the auth layer says of the principal
    assert _decided_by(who)["authenticated_by"] == p.authenticated_by
    assert bool(_decided_by(who).get("dev_only")) is dev


def test_adapter_agents_trust_the_gateway_under_the_same_name():
    from databridge_adapter_agents.adapt import principal_of

    from databridge_gateway import auth, platform_runtime

    platform_runtime.register_gateway_authenticators()
    p = auth.AuthenticatedPrincipal(principal_id="analyst-7")
    who = principal_of(p, "a test decision")
    assert who["authenticated_by"] == auth.GATEWAY_MECHANISM


def test_activity_still_reads_records_written_under_the_old_name():
    from databridge_gateway import auth
    from databridge_gateway.activity_actor import canonical_mechanism

    assert canonical_mechanism("gateway:auth") == auth.GATEWAY_MECHANISM
    assert canonical_mechanism(auth.GATEWAY_MECHANISM) == auth.GATEWAY_MECHANISM
    assert canonical_mechanism(auth.DEV_MECHANISM) == auth.DEV_MECHANISM
