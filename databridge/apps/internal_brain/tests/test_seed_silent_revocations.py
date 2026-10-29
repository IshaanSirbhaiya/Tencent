"""B14: the seeder's silent revocations (``?webhook=0`` on the mock admin API) produce REAL Gate 2
denials: the index still lists the document (no webhook), so only the live source check can refuse
it. Run against the real mock platforms, connectors and pipeline over the generated world; the plan
is derived from the manifest's S4 actions and the world's S4 questions."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from e2e_world import E2E, MANIFEST, WORLD

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "seed_demo_activity.py"


def _seeder():
    spec = importlib.util.spec_from_file_location("seed_demo_activity_b14", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    return E2E(tmp_path_factory.mktemp("e2e-silent"))


class _Brain:
    """The seeder's brain interface over the in-process E2E world. The officer's journey view is
    read from the request's own ``retrieval.decided`` record (the E2E ledger has no projection)."""

    def __init__(self, env):
        self.env = env

    def ask(self, persona, question, at):
        r = self.env.pipeline.ask(self.env.user(persona), question, channel="replay",
                                  event_time=datetime.fromisoformat(at), replay=True)
        return json.loads(r.model_dump_json())

    def get(self, persona, path):
        rid = path.rsplit("/", 1)[-1]
        dec = [e for e in self.env.ledger.events
               if e.kind == "retrieval.decided" and e.req_id == rid][-1]
        return {"gate2": [{"doc_id": g.doc_id, "verdict": g.verdict} for g in dec.gate2]}

    def after_source_change(self):
        pass                                    # the restore's webhook reaches the ingestor


def test_plan_comes_from_the_manifest():
    mod = _seeder()
    world = json.loads(WORLD.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    plan = mod.silent_revocation_plan(world, manifest)
    assert [p["action"] for p in plan] == ["s4_remove_member", "s4_restrict_page"]
    for p in plan:
        spec = manifest["s4_actions"][p["action"]]
        assert p["revoke"] == spec["admin_call"].split(" ", 1)[1] + "?webhook=0"
        q = next(q for q in world["questions"] if q.get("applies_after") == p["action"])
        assert (p["persona"], p["question"]) == (q["persona"], q["question"])


def test_silent_revocations_are_real_gate2_denials_and_are_restored(env):
    mod = _seeder()
    world = json.loads(WORLD.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    plan = mod.silent_revocation_plan(world, manifest)
    hooks_before = len(env.hooks)
    calls = []

    def admin(path, body):
        calls.append(path)
        env.admin_post(path, body)
    try:
        stats = mod.silent_revocations(_Brain(env), plan, admin=admin,
                                       now=datetime.now(timezone.utc))
    finally:
        env.reset()
    assert stats["silent_revocations"] == len(plan) == stats["silent_restored"]
    assert stats["gate2_denials_confirmed"] == len(plan)            # Gate 2 really said no
    # revoke (silent) then restore, per step; only the restores sent webhooks
    assert calls == [x for p in plan for x in (p["revoke"], p["restore"])]
    assert len(env.hooks) - hooks_before == len(plan)
    denied = [e for e in env.ledger.events if e.kind == "retrieval.decided"
              and any(g.verdict == "forbidden" for g in e.gate2)]
    assert denied and all(e.synthetic_replay for e in denied[-len(plan):])
    for e in denied[-len(plan):]:                       # the denied doc was never released
        forbidden = {g.doc_id for g in e.gate2 if g.verdict == "forbidden"}
        assert not forbidden & set(e.released)
