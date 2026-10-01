"""HC1 (2026-10-01), SWEEP hardcode audit #9: the CLI's harness runs models READ from
`config/models.yaml`, per task class — never a model id written in `llm.py`."""
from __future__ import annotations

import ast
import copy
import re
from pathlib import Path

import pytest

from databridge_mcp_ingest import llm

_MODEL_ID = re.compile(r"^[a-z_]+/[A-Za-z0-9][A-Za-z0-9.:_-]*$")


def _config():
    from databridge_core.config import load_model_config

    return copy.deepcopy(load_model_config())


def _all_config_models(cfg) -> set[str]:
    return {v["model"] for v in (cfg.get("aliases") or {}).values()
            if isinstance(v, dict) and v.get("model")}


@pytest.fixture
def no_keys(monkeypatch):
    monkeypatch.delenv("DATABRIDGE_FORCE_GOLD", raising=False)
    assert llm.PROVIDER_KEY_ENV
    for env in llm.PROVIDER_KEY_ENV.values():
        monkeypatch.delenv(env, raising=False)
    return monkeypatch


def test_llm_py_names_no_model_id_in_code():
    tree = ast.parse(Path(llm.__file__).read_text(encoding="utf-8"))
    strings = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert [s for s in strings if _MODEL_ID.match(s)] == []
    # Non-vacuous: the same pattern does recognise the ids the config holds.
    assert any(_MODEL_ID.match(m) for m in _all_config_models(_config()))


@pytest.mark.parametrize("provider", sorted(llm.PROVIDER_KEY_ENV))
def test_with_one_provider_key_every_effective_model_comes_from_the_config(no_keys, provider):
    no_keys.setenv(llm.PROVIDER_KEY_ENV[provider], "sk-test")
    cfg = _config()
    detected = llm.detect(cfg)
    assert set(detected["routing"]) == set(llm.TASK_CLASSES)
    for route in detected["routing"].values():
        if route["effective"] is not None:
            assert route["effective"] in _all_config_models(cfg)
            assert route["effective"].startswith(provider + "/") or \
                llm._provider(route["effective"]) in llm.KEYLESS_PROVIDERS


def test_the_configured_model_wins_when_its_provider_is_reachable(no_keys):
    cfg = _config()
    alias = cfg["routing"][llm.TASK_CLASSES[0]]
    configured = cfg["aliases"][alias]["model"]
    env = llm.PROVIDER_KEY_ENV.get(llm._provider(configured))
    if env:
        no_keys.setenv(env, "sk-test")
    detected = llm.detect(cfg)
    assert detected["routing"][llm.TASK_CLASSES[0]]["effective"] == configured
    assert detected["routing"][llm.TASK_CLASSES[0]]["fallback"] is False


def test_a_synthetic_table_falls_back_only_to_a_model_it_names(no_keys):
    providers = sorted(llm.PROVIDER_KEY_ENV)
    assert len(providers) >= 2
    primary, other = providers[0], providers[1]
    cfg = {"routing": {t: "gen" for t in llm.TASK_CLASSES},
           "aliases": {"gen": {"model": f"{primary}/synthetic-gen-1"},
                       llm.FALLBACK_ALIAS: {"model": f"{other}/synthetic-fallback-1"}}}
    no_keys.setenv(llm.PROVIDER_KEY_ENV[other], "sk-test")
    detected = llm.detect(cfg)
    assert detected["model"] == f"{other}/synthetic-fallback-1"
    assert all(r["fallback"] for r in detected["routing"].values())


def test_nothing_reachable_is_reported_not_papered_over(no_keys):
    providers = sorted(llm.PROVIDER_KEY_ENV)
    cfg = {"routing": {t: "gen" for t in llm.TASK_CLASSES},
           "aliases": {"gen": {"model": f"{providers[0]}/synthetic-gen-1"}}}
    detected = llm.detect(cfg)
    assert detected["model"] is None and detected["backend"] == "none"
    assert detected["reason"]
