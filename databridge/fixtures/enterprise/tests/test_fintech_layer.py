"""The thin fintech layer of the Company A world (FC, MK, FEE, SENS): it only ADDS to the estate, its
FinCrime compartment is sealed by source ACLs alone, market scope is content not access, the draft fee
schedule is readable by nobody, and its sensitive values are published test values.

Expectations are derived from the generated data and the fact manifest (repo rule 6)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import generate as G  # noqa: E402

from test_generator import ARGS, Ref  # noqa: E402

FINTECH_SCENARIOS = {"FC", "MK", "FEE", "SENS"}


@pytest.fixture(scope="module")
def full():
    wb, _mb, m = G.generate(**ARGS)
    return json.loads(wb), m


def _build(*, fintech: bool, workplace: bool) -> dict:
    from datetime import datetime
    b = G.Builder(ARGS["seed"], datetime.fromisoformat(ARGS["now"]), ARGS["scale"])
    if not fintech:
        b.build_fintech = lambda: None
        b.build_fintech_questions = lambda: None
    if not workplace:                       # the later workplace layer (test_workplace_layer.py)
        b.build_workplace = lambda: None
        b.build_workplace_questions = lambda: None
    return b.build()


@pytest.fixture(scope="module")
def base():
    """The world as it was before the fintech layer: both later layers switched off."""
    return _build(fintech=False, workplace=False)


@pytest.fixture(scope="module")
def with_fintech():
    """The world with the fintech layer and without the later workplace layer."""
    return _build(fintech=True, workplace=False)


@pytest.fixture(scope="module")
def world(full):
    return full[0]


@pytest.fixture(scope="module")
def ref(world):
    return Ref(world)


@pytest.fixture(scope="module")
def texts(world):
    return G.doc_texts(world)


def _fact(world, fid):
    return next(f for f in world["facts"] if f["id"] == fid)


def _q(world, qid):
    return next(q for q in world["questions"] if q["id"] == qid)


# --------------------------------------------------------------------------------------------
# additive only
# --------------------------------------------------------------------------------------------
def test_fintech_layer_only_adds(with_fintech, base):
    world = with_fintech
    for plat, coll, key in (("confluence", "pages", "id"), ("jira", "issues", "key"),
                            ("drive", "files", "id"), ("drive", "folders", "id"),
                            ("confluence", "spaces", "key")):
        new = {x[key]: x for x in world[plat][coll]}
        old = base[plat][coll]
        assert old and all(new.get(x[key]) == x for x in old), (plat, coll)
        assert len(new) > len(old), (plat, coll)
    new_threads = {(t["channel"], t["ts"]): t for t in world["slack"]["threads"]}
    assert all(new_threads.get((t["channel"], t["ts"])) == t for t in base["slack"]["threads"])
    new_ch = {c["id"]: c for c in world["slack"]["channels"]}
    for c in base["slack"]["channels"]:
        assert {k: v for k, v in new_ch[c["id"]].items() if k != "members"} == \
            {k: v for k, v in c.items() if k != "members"}
        assert set(c["members"]) <= set(new_ch[c["id"]]["members"])
        added = set(new_ch[c["id"]]["members"]) - set(c["members"])
        assert added <= set(G.FINTECH_USERS), c["id"]
    new_facts = {f["id"]: f for f in world["facts"]}
    for f in base["facts"]:
        assert new_facts[f["id"]] == f, f["id"]
    new_q = [q["id"] for q in world["questions"]]
    old_q = [q["id"] for q in base["questions"]]
    assert [x for x in new_q if x in set(old_q)] == old_q             # same order, nothing removed
    by_id = {q["id"]: q for q in world["questions"]}
    for q in base["questions"]:
        n = by_id[q["id"]]
        assert {k: v for k, v in n.items() if k != "must_not_contain_facts"} == \
            {k: v for k, v in q.items() if k != "must_not_contain_facts"}
        assert set(q["must_not_contain_facts"]) <= set(n["must_not_contain_facts"])
    assert base["audit_replay"] == world["audit_replay"]
    added = {f["scenario"] for f in world["facts"]} - {f["scenario"] for f in base["facts"]}
    assert added == FINTECH_SCENARIOS


# --------------------------------------------------------------------------------------------
# FC: the FinCrime compartment and the approved customer script
# --------------------------------------------------------------------------------------------
def test_fincrime_compartment_is_sealed_by_source_acls(world, ref, texts):
    users = {u["id"]: u for u in world["directory"]["users"]}
    team = ref.members("grp-fincrime")
    assert "mlro.tan" in team and not team & {"jdoe", "leo.ops", "ken.ext", "priya.sre", "amy.am"}
    assert "grp-fincrime" not in users["amy.am"]["groups"]
    assert {"grp-accounts", "grp-staff"} <= set(users["amy.am"]["groups"])
    space = next(s for s in world["confluence"]["spaces"] if s["key"] == "fincrime")
    assert space["read"] == {"users": [], "groups": ["grp-fincrime"]}
    ch = next(c for c in world["slack"]["channels"] if c["name"] == "fincrime-cases")
    assert ch["type"] == "private" and set(ch["members"]) == team
    fc = [f for f in world["facts"] if f["id"].startswith("FINCRIME-")]
    assert {f["doc"].split(":")[0] for f in fc} == {"confluence", "slack", "drive"}
    for f in fc:
        assert f["canary"] and f["permitted"] == ["mlro.tan"], f["id"]
    # the customer's name occurs ONLY in documents the FinCrime team alone can read
    named = [d for d, t in texts.items() if "velquarry" in t.lower()]
    assert len(named) >= 4
    for d in named:
        readers = ref.can_read(d)
        assert readers and readers <= team, d
        assert "SYNTHETIC" in texts[d] or d.startswith("slack:"), d


def test_customer_review_script_is_readable_by_account_managers_not_fincrime_only(world):
    f = _fact(world, "CX-review-script")
    assert {"amy.am", "leo.ops"} <= set(f["permitted"]) and "mlro.tan" not in f["permitted"]
    assert "velquarry" not in f["statement"].lower()


def _stem(t: str) -> str:
    """The IB index's suffix stripper (databridge_ib.index.stem), restated so this fixture test
    does not import the app: trading/trade -> trad, traders -> trader, spice -> spic."""
    if any(ch.isdigit() for ch in t):
        return t
    for suf in ("ing", "ed", "es", "s"):
        if t.endswith(suf) and len(t) - len(suf) >= 4:
            t = t[: -len(suf)]
            break
    return t[:-1] if t.endswith("e") and len(t) >= 5 else t


def _words(s: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 1}


def test_p2_pairs_differ_only_in_words_the_asker_cannot_match(world, full, ref, texts):
    """The FC pairs (real restricted customer vs a company that never existed) can only produce
    the same response if the words that differ match nothing the asker can read."""
    pairs = full[1]["p2_pairs"]
    assert ["FC-q1", "FC-q2"] in pairs and ["FC-q4", "FC-q5"] in pairs
    for a, b in pairs:
        qa, qb = _q(world, a), _q(world, b)
        assert qa["persona"] == qb["persona"] and qa["must_cite_facts"] == qb["must_cite_facts"]
        assert qa["expect_refusal"] == qb["expect_refusal"]
        diff = _words(qa["question"]) ^ _words(qb["question"])
        assert diff
        readable = set().union(*(_words(t) for d, t in texts.items()
                                 if qa["persona"] in ref.can_read(d)))
        assert len(readable) > 100                                   # non-vacuous
        assert not diff & readable, (a, b, diff & readable)
    assert not any("bayfront" in t.lower() for t in texts.values())


def test_fc_questions(world):
    fincrime = {f["id"] for f in world["facts"] if f["id"].startswith("FINCRIME-")}
    for qid in ("FC-q1", "FC-q2"):
        q = _q(world, qid)
        assert q["persona"] == "amy.am" and q["must_cite_facts"] == ["CX-review-script"]
        assert fincrime <= set(q["must_not_contain_facts"]) and q["expect_refusal"] is False
    for qid in ("FC-q4", "FC-q5"):
        assert _q(world, qid)["expect_refusal"] is True
    q3 = _q(world, "FC-q3")
    assert q3["persona"] == "mlro.tan" and set(q3["must_cite_facts"]) == fincrime


# --------------------------------------------------------------------------------------------
# MK: market scope is content, never access
# --------------------------------------------------------------------------------------------
TAG = re.compile(r"^\[Applies to: ((?:SG|HK|ID|AU|EU|US)(?:, (?:SG|HK|ID|AU|EU|US))*) "
                 r"entit(?:y|ies)\]$")


def test_market_tags_lead_the_scoped_documents(world):
    pages = {f"confluence:{p['id']}": p["body"] for p in world["confluence"]["pages"]}
    files = {f"drive:{f['id']}": f["content"] for f in world["drive"]["files"]}
    tagged = [f for f in world["facts"] if f.get("markets")]
    assert {f["scenario"] for f in tagged} == {"MK", "FEE"}
    for f in tagged + [_fact(world, "Ops-policy-confluence"), _fact(world, "Ops-pricing-drive")]:
        first = {**pages, **files}[f["doc"]].split("\n", 1)[0]
        m = TAG.match(first)
        assert m, (f["id"], first)
        if f.get("markets"):
            assert set(f["markets"]) <= set(m.group(1).split(", ")), f["id"]
    # tags are not an access dimension: no ACL anywhere names a market
    blob = json.dumps([world["confluence"]["spaces"], world["drive"]["folders"],
                       world["directory"]["groups"]])
    assert "Applies to" not in blob


def test_mk_questions_cite_different_market_facts(world):
    q1, q2 = _q(world, "MK-q1"), _q(world, "MK-q2")
    assert q1["persona"] == q2["persona"] == "leo.ops"
    assert set(q1["must_cite_facts"]).isdisjoint(q2["must_cite_facts"])
    facts = {f["id"]: f for f in world["facts"]}
    assert all("SG" in facts[x].get("markets", ["SG"]) for x in q1["must_cite_facts"])
    assert any("ID" in facts[x].get("markets", []) for x in q2["must_cite_facts"])


# --------------------------------------------------------------------------------------------
# FEE: approved v3 is cited, the draft v4 is readable by nobody
# --------------------------------------------------------------------------------------------
def test_fee_draft_is_never_readable(world):
    pages = {p["id"]: p for p in world["confluence"]["pages"]}
    v3, dr = _fact(world, "FEE-v3"), _fact(world, "FEE-draft")
    assert pages[v3["doc"].split(":")[1]]["status"] == "current"
    assert pages[dr["doc"].split(":")[1]]["status"] == "draft"
    assert dr["permitted"] == [] and dr["canary"] and "leo.ops" in v3["permitted"]
    q = _q(world, "FEE-q1")
    assert q["must_cite_facts"] == ["FEE-v3"] and "FEE-draft" in q["must_not_contain_facts"]


# --------------------------------------------------------------------------------------------
# SENS: synthetic values only, each in exactly its document
# --------------------------------------------------------------------------------------------
def _luhn(d: str) -> bool:
    s = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch) * (2 if i % 2 else 1)
        s += n - 9 if n > 9 else n
    return s % 10 == 0


PUBLISHED_TEST_PANS = {"4111111111111111", "5555555555554444"}


def test_sens_values_are_published_test_values_in_their_doc(world, texts):
    sens = [f for f in world["facts"] if f["scenario"] == "SENS"]
    assert {f["sensitive_kind"] for f in sens} >= {"pan", "sg_nric"}
    for f in sens:
        assert f["raw"] not in f["statement"] and f["raw"] != f["masked"]
        assert [d for d, t in texts.items() if f["raw"] in t] == [f["doc"]], f["id"]
        if f["sensitive_kind"] == "pan":
            digits = re.sub(r"\D", "", f["raw"])
            assert digits in PUBLISHED_TEST_PANS and _luhn(digits)
            assert f["masked"].startswith(digits[:6]) and f["masked"].endswith(digits[-4:])
            assert not re.search(r"\d{7,}", f["masked"])
        if f["sensitive_kind"] == "sg_nric":
            assert re.fullmatch(r"[STFGM]\d{7}[A-Z]", f["raw"])
            assert f["masked"].endswith(f["raw"][-4:]) and f["raw"][:5] not in f["masked"]
