"""The demo-history seeder (scripts/seed_demo_activity.py) never puts a restricted title, key or
container name into a persona's own history (Opus plan review #7), spreads its questions over the
last 30 days without a future date, and covers every demo persona. Checked against the REAL index
built from the world (Gate 1 per persona), not against the seeder's own rules."""
from __future__ import annotations

import importlib.util
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from e2e_world import E2E, MANIFEST, WORLD

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "seed_demo_activity.py"


def _seeder():
    spec = importlib.util.spec_from_file_location("seed_demo_activity", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def test_seed_plan_names_nothing_the_persona_cannot_open(tmp_path):
    mod = _seeder()
    now = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
    plan = mod.build_plan(json.loads(WORLD.read_text(encoding="utf-8")),
                          json.loads(MANIFEST.read_text(encoding="utf-8")),
                          count=300, days=30, seed=7, refusal_share=0.12, now=now)
    assert len(plan) == 300
    w = E2E(tmp_path)
    personas = {x["persona"] for x in plan}
    assert personas == {q["persona"] for q in json.loads(MANIFEST.read_text(encoding="utf-8"))[
        "questions"]}
    for p in personas:
        readable = w.index.readable(w.user(p), w.directory.epoch)
        open_containers = {(w.index.doc(d)["platform"], w.index.doc(d)["container"]) for d in readable}
        marks = set()
        for d in w.index.doc_ids():
            if d in readable:
                continue
            row = w.index.doc(d)
            title = row["title"]
            if row["platform"] == "jira":
                marks.add(_norm(title.split(":", 1)[0]))            # the key
                marks.add(_norm(title.split(":", 1)[1]))
            elif row["platform"] != "slack":
                marks.add(_norm(title))
            if (row["platform"], row["container"]) not in open_containers:
                marks.add(_norm(row["container_name"]))
        marks = {m for m in marks if len(m) >= 5}
        for item in (x for x in plan if x["persona"] == p):
            q = _norm(item["question"])
            hit = [m for m in marks
                   if re.search(r"(?<![a-z0-9])" + re.escape(m) + r"(?![a-z0-9])", q)]
            assert not hit, (p, item["question"], hit)
    times = [datetime.fromisoformat(x["at"]) for x in plan]
    assert all(now - timedelta(days=31) <= t < now for t in times)
    assert len({t.date() for t in times}) >= 20                     # a real timeline
    assert sum(x["expected"] == "refusal" for x in plan) >= len(personas)
