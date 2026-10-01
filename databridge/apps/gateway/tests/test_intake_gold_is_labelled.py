"""HC1 (2026-10-01), SWEEP hardcode audit #1: `/intake` may use the recalled gold extraction ONLY
when no model is configured, and must then say so on the card. A configured model that fails is a
refusal, never a silent fall back to gold.

The gold backend is an input→answer table keyed on fixture markers (`databridge_intake.gold`). Served
unlabelled, a fixture pasted through the one-click trials came back as a fully read card that
nobody could tell was recalled. `/brain/ingest` already refuses in the same situation
(`NO_EXTRACTION_MODEL`); this pins the intake path to the same honesty.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import AUTH_HEADERS

from databridge_gateway import create_app
from databridge_gateway import llm as gateway_llm


def _chit() -> str:
    from databridge_intake.paths import chits_dir

    path = sorted(chits_dir().glob("chit_0*.txt"))[0]
    return path.read_text(encoding="utf-8")


def _provider_key_envs() -> tuple[str, ...]:
    # Derived from the gateway's own provider policy, never restated here.
    return tuple(gateway_llm._PROVIDER_KEY_ENV.values())


@pytest.fixture
def client():
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:
        yield c


def _no_keys(monkeypatch):
    assert _provider_key_envs(), "the provider policy names no key env — the test would be vacuous"
    for env in _provider_key_envs():
        monkeypatch.delenv(env, raising=False)


def test_no_key_configured_uses_gold_and_labels_the_card(client, monkeypatch, request):
    monkeypatch.delenv("DATABRIDGE_FORCE_GOLD", raising=False)
    _no_keys(monkeypatch)
    # The configured extraction model may be keyless (ollama) — then it is "configured", and this
    # case does not apply. Pin the routing to "nothing reachable" through the real decision.
    monkeypatch.setattr(gateway_llm, "build_extraction_harness", lambda detected: None)
    r = client.post("/intake/upload", json={"document_id": f"{request.node.name}.txt",
                                            "text": _chit()})
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["offline_gold"] is True
    assert card["extraction_backend"] == "offline_gold"
    # The label survives on every later read of the same intake.
    again = client.get(f"/intake/{card['intake_id']}").json()
    assert again["offline_gold"] is True
    queued = [i for i in client.get("/intake").json()["intakes"]
              if i["intake_id"] == card["intake_id"]]
    assert len(queued) >= 1 and queued[0]["offline_gold"] is True


def test_force_gold_is_labelled_too(client, monkeypatch, request):
    monkeypatch.setenv("DATABRIDGE_FORCE_GOLD", "1")
    r = client.post("/intake/upload", json={"document_id": f"{request.node.name}.txt",
                                            "text": _chit()})
    assert r.status_code == 200, r.text
    assert r.json()["offline_gold"] is True


def test_a_configured_model_that_fails_to_build_refuses_rather_than_using_gold(
        client, monkeypatch, request):
    monkeypatch.delenv("DATABRIDGE_FORCE_GOLD", raising=False)
    monkeypatch.setenv(_provider_key_envs()[0], "sk-test-not-a-real-key")

    def boom(detected):
        raise RuntimeError("provider SDK missing")

    monkeypatch.setattr(gateway_llm, "build_extraction_harness", boom)
    r = client.post("/intake/upload", json={"document_id": f"{request.node.name}.txt",
                                            "text": _chit()})
    assert r.status_code == 503, r.text
    assert r.json()["refusal"]["code"] == "LIVE_MODEL_UNAVAILABLE"
    # Nothing was queued from a recalled answer.
    ids = {i["document_id"] for i in client.get("/intake").json()["intakes"]}
    assert f"{request.node.name}.txt" not in ids


def test_a_key_with_no_reachable_extraction_route_refuses(client, monkeypatch, request):
    monkeypatch.delenv("DATABRIDGE_FORCE_GOLD", raising=False)
    monkeypatch.setenv(_provider_key_envs()[0], "sk-test-not-a-real-key")
    monkeypatch.setattr(gateway_llm, "build_extraction_harness", lambda detected: None)
    r = client.post("/intake/upload", json={"document_id": f"{request.node.name}.txt",
                                            "text": _chit()})
    assert r.status_code == 503, r.text
    assert r.json()["refusal"]["code"] == "LIVE_MODEL_UNAVAILABLE"


def test_a_live_harness_is_labelled_live(client, monkeypatch, request):
    """A harness the gateway built is used and the card says `offline_gold: false`. A FakeBackend
    stands in for the model so the test stays offline (CLAUDE.md)."""
    from databridge_intake.gold import gold_harness

    monkeypatch.delenv("DATABRIDGE_FORCE_GOLD", raising=False)
    monkeypatch.setenv(_provider_key_envs()[0], "sk-test-not-a-real-key")
    monkeypatch.setattr(gateway_llm, "build_extraction_harness", lambda detected: gold_harness())
    r = client.post("/intake/upload", json={"document_id": f"{request.node.name}.txt",
                                            "text": _chit()})
    assert r.status_code == 200, r.text
    assert r.json()["offline_gold"] is False
    assert r.json()["extraction_backend"] == "live"


def test_the_gateway_reads_today_from_the_deployment_clock(client, monkeypatch, request):
    """SWEEP #3: `/intake` passes a real "today" — the clock in `WALNUT_TIMEZONE` — to the
    pipeline, never the fixture date."""
    import datetime as dt

    from databridge_gateway import intake_routes
    from databridge_intake.registry import DATASET_TODAY

    pinned = DATASET_TODAY + dt.timedelta(days=400)
    monkeypatch.setattr(intake_routes, "deployment_today", lambda now=None: pinned)
    seen: list = []
    real = intake_routes.run_intake

    def spy(*a, **kw):
        seen.append(kw.get("today"))
        return real(*a, **kw)

    monkeypatch.setattr(intake_routes, "run_intake", spy)
    r = client.post("/intake/upload", json={"document_id": f"{request.node.name}.txt",
                                            "text": _chit()})
    assert r.status_code == 200, r.text
    assert len(seen) >= 1 and all(t == pinned for t in seen)
