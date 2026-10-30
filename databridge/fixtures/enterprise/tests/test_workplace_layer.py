"""The everyday-company layer of the Pandan Ledger world (scenario WP, ``workplace.py``): it only ADDS to
the estate (the world without it is byte-identical to the world before it existed), its handbook,
IT and finance documents are open to every employee and to nobody outside the company, its one
restricted document (salary bands) is sealed by the source ACL alone, its wording cannot disturb the
earlier scenario questions, and it never pre-ingests a topic reserved for the live-ingestion demo.

Expectations are derived from the generated data and the fact manifest (repo rule 4)."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import generate as G  # noqa: E402
import workplace as W  # noqa: E402

from test_generator import ARGS, PERSONAS, Ref  # noqa: E402

#: sha256 of world.json as committed before the workplace layer existed (generator run with the
#: same arguments). The world built with the layer switched off must still hash to it.
PRE_WORKPLACE_WORLD_SHA256 = "be21569dbfd75867da7d55e82ab671cc250e8c9cdb85261e0377b20a2d55f388"

#: D-43 renamed the company after that sha was taken ("Company A" -> "Pandan Ledger", and its
#: domains). The rename changes only these strings, so mapping them back must give the pinned world.
RENAME_BACK = (("Pandan Ledger", "Company A"), ("pandanledger.example", "companya.example"),
               ("pandanledger.atlassian.net", "companya.atlassian.net"),
               ("pandanledger.slack.com", "companya.slack.com"))

#: phrases reserved for the live-ingestion demo: they must not exist before the demo ingests them
RESERVED_LIVE_PHRASES = ("wellness day", "authenticator pro", "rehearsal moved", "5 nov",
                         "16 nov", "20 nov", "shard 4 cutover rehearsal", "shard four")
LIVE_SAMPLES = HERE.parents[1] / "apps" / "internal_brain_web" / "public" / "finals" / \
    "live_samples.json"


@pytest.fixture(scope="module")
def full():
    wb, _mb, m = G.generate(**ARGS)
    return json.loads(wb), m, wb


@pytest.fixture(scope="module")
def base():
    """The same world with the workplace layer switched off."""
    b = G.Builder(ARGS["seed"], datetime.fromisoformat(ARGS["now"]), ARGS["scale"])
    b.build_workplace = lambda: None
    b.build_workplace_questions = lambda: None
    return b.build()


@pytest.fixture(scope="module")
def world(full):
    return full[0]


@pytest.fixture(scope="module")
def ref(world):
    return Ref(world)


@pytest.fixture(scope="module")
def texts(world):
    return G.doc_texts(world)


@pytest.fixture(scope="module")
def wp_docs(world):
    """doc id → text for every document the workplace layer added."""
    wb = W
    users = set(wb.WORKPLACE_USERS)
    out = {}
    spaces = {"people", "finops"}
    for p in world["confluence"]["pages"]:
        if p["space"] in spaces:
            out[f"confluence:{p['id']}"] = None
    for i in world["jira"]["issues"]:
        if i["project"] == "ITHELP":
            out[f"jira:{i['key']}"] = None
    folders = {f["id"]: f["name"] for f in world["drive"]["folders"]}
    for f in world["drive"]["files"]:
        if folders[f["folder"]] in ("People", "Compensation (People leads only)"):
            out[f"drive:{f['id']}"] = None
    wp_threads = {f["doc"] for f in world["facts"] if f["scenario"] == "WP"
                  and f["doc"].startswith("slack:")}
    for t in world["slack"]["threads"]:
        did = f"slack:{t['channel']}:{t['ts']}"
        if t["channel"] in ("C0HRANN", "C0ITHELP", "C0OFFICESG") or did in wp_threads or \
                any(m["user"] in users for m in t["messages"]):
            out[did] = None
    all_texts = G.doc_texts(world)
    return {d: all_texts[d] for d in out}


def _fact(world, fid):
    return next(f for f in world["facts"] if f["id"] == fid)


def _q(world, qid):
    return next(q for q in world["questions"] if q["id"] == qid)


# --------------------------------------------------------------------------------------------
# additive only
# --------------------------------------------------------------------------------------------
def test_world_without_the_layer_is_the_pre_layer_world(base):
    text = G.dumps(base).decode("utf-8")
    assert "Company A" not in text and "companya.example" not in text
    for new, old in RENAME_BACK:
        text = text.replace(new, old)
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == PRE_WORKPLACE_WORLD_SHA256


def test_workplace_layer_only_adds(full, base):
    world = full[0]
    for plat, coll, key in (("confluence", "pages", "id"), ("jira", "issues", "key"),
                            ("drive", "files", "id"), ("drive", "folders", "id"),
                            ("confluence", "spaces", "key"), ("jira", "projects", "key"),
                            ("directory", "users", "id")):
        new = {x[key]: x for x in world[plat][coll]}
        old = base[plat][coll]
        assert old and all(new.get(x[key]) == x for x in old), (plat, coll)
        assert len(new) > len(old), (plat, coll)
        assert [x[key] for x in world[plat][coll]][:len(old)] == [x[key] for x in old]
    new_threads = {(t["channel"], t["ts"]): t for t in world["slack"]["threads"]}
    assert all(new_threads.get((t["channel"], t["ts"])) == t for t in base["slack"]["threads"])
    new_ch = {c["id"]: c for c in world["slack"]["channels"]}
    for c in base["slack"]["channels"]:
        assert {k: v for k, v in new_ch[c["id"]].items() if k != "members"} == \
            {k: v for k, v in c.items() if k != "members"}
        added = set(new_ch[c["id"]]["members"]) - set(c["members"])
        assert set(c["members"]) <= set(new_ch[c["id"]]["members"])
        assert added <= set(W.WORKPLACE_USERS), c["id"]
    groups = {g["id"]: g for g in world["directory"]["groups"]}
    for g in base["directory"]["groups"]:
        n = groups[g["id"]]
        assert n["members"] == g["members"]
        assert n["member_groups"][:len(g["member_groups"])] == g["member_groups"]
    new_facts = {f["id"]: f for f in world["facts"]}
    assert all(new_facts[f["id"]] == f for f in base["facts"])
    old_q = [q["id"] for q in base["questions"]]
    assert [q["id"] for q in world["questions"]][:len(old_q)] == old_q
    by_id = {q["id"]: q for q in world["questions"]}
    for q in base["questions"]:
        n = by_id[q["id"]]
        assert {k: v for k, v in n.items() if k != "must_not_contain_facts"} == \
            {k: v for k, v in q.items() if k != "must_not_contain_facts"}
        assert set(q["must_not_contain_facts"]) <= set(n["must_not_contain_facts"])
    assert base["audit_replay"] == world["audit_replay"]
    added = {f["scenario"] for f in world["facts"]} - {f["scenario"] for f in base["facts"]}
    assert added == {W.SCENARIO}


def test_existing_personas_and_roles_are_unchanged(world, base):
    users = {u["id"]: u for u in world["directory"]["users"]}
    for u in base["directory"]["users"]:
        assert users[u["id"]] == u
    assert not set(W.WORKPLACE_USERS) & set(PERSONAS)


# --------------------------------------------------------------------------------------------
# who can read the new content
# --------------------------------------------------------------------------------------------
def test_open_documents_reach_every_employee_and_nobody_outside(world, ref, wp_docs):
    everyone = ref.everyone()
    outsiders = {u["id"] for u in world["directory"]["users"]
                 if u["kind"] != "member" and u["active"]}
    assert outsiders and "ken.ext" in outsiders
    restricted = [d for d in wp_docs if not everyone <= ref.can_read(d)]
    assert [d for d in restricted] == [_fact(world, "WP-salary-bands")["doc"]]
    for d in wp_docs:
        assert not ref.can_read(d) & outsiders, d
    assert 60 <= len(wp_docs) <= 90


def test_salary_bands_are_readable_by_the_people_team_only(world, ref, texts):
    f = _fact(world, "WP-salary-bands")
    team = ref.members("grp-people")
    assert team == set(W.WORKPLACE_USERS)
    assert ref.can_read(f["doc"]) == team
    assert f["canary"] and f["canary"] in texts[f["doc"]] and f["permitted"] == []
    folder = next(x for x in world["drive"]["folders"]
                  if x["id"] == next(fl for fl in world["drive"]["files"]
                                     if f"drive:{fl['id']}" == f["doc"])["folder"])
    assert folder["inherited_permissions_disabled"] is True
    # the word the P2 pairs rely on occurs only in documents the People team alone can read
    for d, t in texts.items():
        if re.search(r"\bsalar(y|ies)\b", t, re.I):
            assert ref.can_read(d) <= team, d


def test_every_open_fact_is_canary_free_and_permitted_to_every_employee_persona(world):
    wp = [f for f in world["facts"] if f["scenario"] == W.SCENARIO]
    assert 12 <= len(wp) <= 30
    assert {f["doc"].split(":")[0] for f in wp} == {"confluence", "jira", "slack", "drive"}
    for f in wp:
        if f["id"] == "WP-salary-bands":
            continue
        assert f["canary"] is None, f["id"]
        assert f["denied"] == ["ken.ext"], f["id"]


# --------------------------------------------------------------------------------------------
# wording: the new open content cannot disturb earlier questions or the live-ingestion demo
# --------------------------------------------------------------------------------------------
def test_open_documents_avoid_reserved_vocabulary(world, ref, wp_docs):
    everyone = ref.everyone()
    for d, t in wp_docs.items():
        if not everyone <= ref.can_read(d):
            continue
        low = f" {t.lower()} "
        hits = [w for w in W.AVOID_IN_OPEN_DOCS if re.search(r"" + re.escape(w), low)]
        assert not hits, (d, hits)


def test_reserved_live_demo_topics_are_not_pre_ingested(full):
    blob = full[2].decode("utf-8").lower()
    for phrase in RESERVED_LIVE_PHRASES:
        assert phrase not in blob, phrase


def test_live_demo_samples_are_not_pre_ingested(full):
    if not LIVE_SAMPLES.exists():
        pytest.skip(f"{LIVE_SAMPLES.name} does not exist yet")
    samples = json.loads(LIVE_SAMPLES.read_text(encoding="utf-8"))
    entries = samples if isinstance(samples, list) else (
        samples.get("samples") or samples.get("items") or list(samples.values()))
    blob = full[2].decode("utf-8").lower()
    phrases = [e["proof_phrase"] for e in entries if isinstance(e, dict) and e.get("proof_phrase")]
    for p in phrases:
        assert p.lower() not in blob, p


def test_no_real_company_branding_in_the_layer(wp_docs):
    for d, t in wp_docs.items():
        assert "aspire" not in t.lower() and "tencent" not in t.lower(), d


# --------------------------------------------------------------------------------------------
# questions
# --------------------------------------------------------------------------------------------
def test_workplace_questions(world, full):
    facts = {f["id"]: f for f in world["facts"]}
    wq = [q for q in world["questions"] if q["scenario"] == W.SCENARIO]
    assert len(wq) >= 20
    answered = [q for q in wq if q["expect_refusal"] is False]
    cited = {c for q in answered for c in q["must_cite_facts"]}
    assert cited == {f for f in facts if facts[f]["scenario"] == W.SCENARIO} - {"WP-salary-bands"}
    assert len({q["persona"] for q in answered}) >= 6
    pairs = full[1]["p2_pairs"]
    for a, b in W.P2_PAIRS:
        assert [a, b] in pairs
        qa, qb = _q(world, a), _q(world, b)
        assert qa["persona"] == qb["persona"] and qa["expect_refusal"] is qb["expect_refusal"] is True
        assert "WP-salary-bands" in qa["must_not_contain_facts"]
    ken = _q(world, "WP-q25")
    assert ken["persona"] == "ken.ext" and ken["expect_refusal"] is True
    assert "WP-annual-leave" in ken["must_not_contain_facts"]


def test_probe_ids_extend_each_persona_without_renumbering(world, base):
    old = {q["id"] for q in base["questions"]}
    for p in PERSONAS:
        mine = [q for q in world["questions"] if q["scenario"] == "PROBE" and q["persona"] == p]
        new = [q for q in mine if q["id"] not in old]
        assert len(new) == 3 * len(W.PROBE_ASKS)
        nums = sorted(int(q["id"].rsplit("-", 1)[1]) for q in mine)
        assert nums == list(range(1, len(mine) + 1)), p
