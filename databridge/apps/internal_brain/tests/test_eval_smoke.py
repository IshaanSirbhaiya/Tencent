"""Smoke test for the leak eval (``databridge/evals/internal_brain``): a tiny world (the fact
documents plus their Confluence ancestors, every container kept) and ≤20 generated probes through
the capturing model path. Asserts the eval's detectors find nothing on the real pipeline AND do fire
when Gate 1 + Gate 2 are switched off (the eval's own canary). Full run: ``python -m
evals.internal_brain.run`` from ``databridge/``."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_DATABRIDGE = Path(__file__).resolve().parents[3]
if str(_DATABRIDGE) not in sys.path:
    sys.path.insert(0, str(_DATABRIDGE))

from evals.internal_brain import capture as C                      # noqa: E402
from evals.internal_brain import oracle as O                       # noqa: E402
from evals.internal_brain.probes import generate                   # noqa: E402


def _small_world(tmp: Path) -> Path:
    world = O.load_world()
    pages = {p["id"]: p for p in world["confluence"]["pages"]}
    keep = {f["doc"] for f in world["facts"]}
    for d in list(keep):
        if d.startswith("confluence:"):
            cur = pages[d.split(":", 1)[1]]["parent"]
            while cur:
                keep.add(f"confluence:{cur}")
                cur = pages[cur]["parent"]
    small, rep = O.filter_world(world, set(O.all_doc_ids(world)) - keep)
    assert not rep["orphaned_pages"]
    return O.write_world(small, tmp / "world_small.json")


def _pick(probes, n=18):
    """Deterministic spread: deny probes of several types, positive probes, world + injection."""
    out = []
    for pred in (lambda p: p.polarity == "deny" and p.type == "direct",
                 lambda p: p.polarity == "deny" and p.type == "injection",
                 lambda p: p.polarity == "deny" and p.type in ("paraphrase", "completion"),
                 lambda p: p.polarity == "deny" and p.type.startswith("meta"),
                 lambda p: p.polarity == "allow",
                 lambda p: p.polarity == "world" and p.injection,
                 lambda p: p.polarity == "world" and not p.injection):
        group = [p for p in probes if pred(p)]
        step = max(1, len(group) // 3)
        out += group[::step][:3]
    return out[:n]


@pytest.fixture(scope="module")
def small(tmp_path_factory):
    from e2e_world import E2E
    tmp = tmp_path_factory.mktemp("ibeval")
    path = _small_world(tmp)
    world = O.load_world(path)
    oracle = O.Oracle(world)
    env = E2E(tmp, world_path=path)
    cap = C.Capture()
    C.instrument(env, cap)
    return env, oracle, cap, generate(world, oracle), C.injection_markers(oracle)


def test_probes_generated_cover_every_denied_pair(small):
    _, oracle, _, probes, _ = small
    for persona in ("jdoe", "ken.ext"):
        denied = set(oracle.denied_facts(persona))
        assert denied, persona                                   # non-vacuous
        assert denied == {p.fact for p in probes if p.persona == persona and p.polarity == "deny"}


def test_no_leak_through_the_capturing_model_path(small):
    env, oracle, cap, probes, markers = small
    picked = _pick(probes)
    assert 10 <= len(picked) <= 20
    C.set_mode(env, cap, "fake-capture")
    recs = [C.run_probe(env, oracle, cap, p, "fake-capture", markers) for p in picked]
    assert not [r for r in recs if r.get("error")]
    assert not [(r["id"], r["leaks"]) for r in recs if r["leaks"]]
    assert not [(r["id"], r["violations"]) for r in recs if r["violations"]]
    assert any(r["n_model_calls"] for r in recs), "the model path must actually be exercised"
    assert not [r["id"] for r in recs if r.get("injection", {}).get("forged_citation")
                or r.get("injection", {}).get("followed_base64")]


def test_eval_canary_detects_a_leaky_pipeline(small):
    env, oracle, cap, probes, markers = small
    deny = [p for p in probes if p.polarity == "deny" and p.type == "direct"][:4]
    C.set_mode(env, cap, "fake-capture")
    undo = C.break_access(env)
    try:
        recs = [C.run_probe(env, oracle, cap, p, "fake-capture", markers) for p in deny]
    finally:
        undo()
    assert any(r["leaks"] for r in recs), "detectors must fire when Gate 1 + Gate 2 are off"
