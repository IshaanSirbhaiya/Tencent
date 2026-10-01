"""`/evidence/verify` — the verdict has to be *computed*, and the impact answer has to be honest.

Two properties carry this surface and both are easy to fake, which is why they are asserted here
rather than eyeballed in a browser:

1. **The verdict comes from a real adapter over a real estate.** The test performs the design's
   own sequence — resolve a citation, verify it, change the underlying row, verify it again — and
   asserts `VERIFIED` then `DRIFT_MATERIAL`. Nothing is stubbed: if the route ever decided a
   verdict itself, step 3 would keep saying `VERIFIED` and this test would fail.
2. **"Unknown" is never rendered as "nothing".** `_impact` has three answers and the payload keeps
   them apart, because an empty affected-list read as reassurance is exactly how a drift alarm
   stops being worth having (`ptr_index.py`).

The fleet is built lazily and loads two seed shards into SQLite; these tests are the only place
that cost is paid, and they take a couple of seconds.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

#: databridge/apps/gateway/tests -> databridge/
DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]
for _p in ("packages/adapter", "packages/core", "packages/graph"):
    _path = str(DATABRIDGE_ROOT / _p)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from databridge_gateway.verify_fleet import dispose_fleet                      # noqa: E402
from databridge_gateway.verify_routes import (                                 # noqa: E402
    VERDICT_MEANING,
    _tone,
    register_verify_routes,
)

from conftest import AUTH_HEADERS, auth_headers                                # noqa: E402


@pytest.fixture()
def app():
    a = FastAPI()
    register_verify_routes(a)
    yield a
    dispose_fleet(a)


@pytest.fixture()
def client(app):
    # R-015: every route here but /evidence/verify/lattice now requires a credential.
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        yield c


# =======================================================================================
# the lattice
# =======================================================================================
def test_the_lattice_is_eight_states_and_never_a_boolean(client):
    body = client.get("/evidence/verify/lattice").json()
    verdicts = {v["verdict"]: v for v in body["verdicts"]}
    assert len(verdicts) == 8, "the lattice is closed at eight; a ninth is a fork"
    assert set(verdicts) == set(VERDICT_MEANING), "every verdict needs operator language"
    for v in verdicts.values():
        assert v["meaning"], f"{v['verdict']} has no meaning text"


def test_drift_material_is_alarming_and_drift_benign_is_not(client):
    """§1a: the split that decides whether verification survives contact with production."""
    verdicts = {v["verdict"]: v for v in client.get("/evidence/verify/lattice").json()["verdicts"]}
    assert verdicts["drift_material"]["tone"] == "bad"
    assert verdicts["drift_material"]["must_escalate"] is True
    assert verdicts["drift_benign"]["tone"] != "bad"
    assert verdicts["drift_benign"]["must_escalate"] is False


def test_unverifiable_is_an_honest_limit_not_a_failure(client):
    verdicts = {v["verdict"]: v for v in client.get("/evidence/verify/lattice").json()["verdicts"]}
    unverifiable = verdicts["unverifiable"]
    assert unverifiable["tone"] == "neutral", "rendering it as a failure trains an operator to ignore it"
    assert unverifiable["must_escalate"] is False
    assert unverifiable["is_positive"] is False


def test_verified_as_of_capture_is_a_positive_verdict(client):
    """It is the DESIGNED outcome for a captured T1/T3 artefact, not a degraded one."""
    verdicts = {v["verdict"]: v for v in client.get("/evidence/verify/lattice").json()["verdicts"]}
    assert verdicts["verified_as_of_capture"]["is_positive"] is True
    assert verdicts["verified_as_of_capture"]["tone"] == "good"


def test_tone_is_derived_from_the_enum_not_from_a_table():
    from databridge_adapter.verdicts import Verdict

    # Every verdict gets a tone, and the escalating one is the only `bad` one. If the enum grows a
    # state, this still holds — which is the point of deriving rather than tabulating.
    tones = {v.value: _tone(v) for v in Verdict}
    assert [k for k, t in tones.items() if t == "bad"] == ["drift_material"]
    assert set(tones.values()) <= {"good", "warn", "bad", "neutral"}


# =======================================================================================
# the fleet, and the four-step sequence
# =======================================================================================
def test_the_fleet_reports_a_real_source_with_its_declared_verdicts(client):
    status = client.get("/evidence/verify/fleet").json()
    assert status["available"] is True, status["reason"]
    assert status["sources"], "a fleet with no source cannot verify anything"
    source = status["sources"][0]
    assert source["source_id"] == "core_banking"
    assert source["reachable_verdicts"], "the source contract says what this source can prove"
    assert source["working_copy"], "the estate is a private copy; the fixture is never written"


def test_citations_are_minted_by_a_read_and_carry_a_pointer(client):
    body = client.get("/evidence/verify/citations").json()
    assert body["citations"], "no citation means no read happened"
    for c in body["citations"]:
        assert c["pointer"]["source_id"] == c["source_id"]
        assert c["pointer"].get("column") == c["column"]
        assert c["value_at_read"] is not None
        assert c["trust"] in ("trusted", "untrusted", None)


def _facility_citation(client) -> dict:
    body = client.get("/evidence/verify/citations").json()
    for c in body["citations"]:
        if c["entity"] == "facility" and c["column"] == "limit_amount":
            return c
    raise AssertionError("the fleet served no facility.limit_amount citation")


def test_the_four_step_sequence_verified_then_drift_material(client):
    """The demo's best ten seconds, executed. Nothing here decides a verdict."""
    citation = _facility_citation(client)
    pointer = citation["pointer"]

    first = client.post("/evidence/verify", json={"pointer": pointer}).json()
    assert first["verdict"] == "verified", first
    assert first["must_escalate"] is False
    assert first["tone"] == "good"

    # step 2: the underlying row changes.
    changed = client.post("/evidence/verify/mutate",
                          json={"pointer": pointer, "new_value": "1.00"})
    assert changed.status_code == 200, changed.json()
    record = changed.json()["changed"]
    assert record["entity"] == "facility" and record["column"] == "limit_amount"
    assert record["from"] != record["to"]

    # step 3: the same pointer, re-executed.
    second = client.post("/evidence/verify", json={"pointer": pointer}).json()
    assert second["verdict"] == "drift_material", second
    assert second["must_escalate"] is True
    assert second["tone"] == "bad"
    assert second["note"], "an escalating verdict with no reason is not actionable"
    # old beside new, and both halves come from the system that emitted them.
    assert second["cited_value_known"] is True
    assert second["cited_value"] == citation["value_at_read"]
    assert second["current_value_known"] is True
    assert str(second["current_value"]) != str(second["cited_value"])


def test_a_pointer_no_adapter_serves_is_unverifiable_not_a_failure(client):
    pointer = {"source_id": "nothing_serves_this", "kind": "table_cell",
               "table": "x.y", "column": "z", "row_id": "1"}
    body = client.post("/evidence/verify", json={"pointer": pointer}).json()
    assert body["verdict"] == "unverifiable"
    assert body["tone"] == "neutral"
    assert "core_banking" in (body["note"] or ""), "the note must name what this fleet does serve"


def test_a_body_that_is_not_a_pointer_is_refused_rather_than_verdicted(client):
    r = client.post("/evidence/verify", json={"pointer": {"kind": "table_cell"}})
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "BAD_POINTER"
    assert "verdict" not in r.json()


# =======================================================================================
# step 4 — the reverse index, and the answer it is entitled to give
# =======================================================================================
def test_impact_is_unavailable_rather_than_empty_when_there_is_no_index(client):
    """The distinction the whole panel rests on: nobody can say != nothing is affected."""
    citation = _facility_citation(client)
    body = client.post("/evidence/impact", json={"pointer": citation["pointer"]}).json()
    impact = body["impact"]
    assert impact["answer"] in ("known", "unknown", "unavailable")
    # This app has no graph store attached, so the honest answer is `unavailable` — and it must
    # carry a reason rather than an empty list that reads as reassurance.
    assert impact["answer"] == "unavailable"
    assert impact["reason"]
    assert impact["records"] == []


def test_impact_reports_a_real_fan_out_when_the_index_can_answer(app, client):
    """With a real store attached, the panel shows real affected records and says `known`."""
    graph = pytest.importorskip("databridge_graph")
    from databridge_core import SourcePointer

    store = graph.GraphStore()
    from databridge_graph.clinic_seed import load_clinic_seed

    load_clinic_seed(store)

    class _Brain:
        pass

    brain = _Brain()
    brain.store = store
    app.state.brain = brain

    row = store.fetchone("SELECT source_pointer_json FROM sources LIMIT 1")
    assert row is not None, "the seed wrote no pointers, so there is nothing to ask about"
    pointer = SourcePointer.model_validate_json(row[0])

    body = client.post("/evidence/impact",
                       json={"pointer": pointer.model_dump(mode="json", exclude_none=True)}).json()
    impact = body["impact"]
    assert impact["answer"] == "known", impact["reason"]
    assert impact["coverage"]["pointers"] > 0
    assert impact["records"], "a pointer the seed wrote must have something bound to it"
    for record in impact["records"]:
        assert record["entity_kind"] in ("node", "edge")
        assert record["entity_id"]


def test_a_known_answer_with_no_records_still_says_so_in_words(app, client):
    """`known` + empty is a real answer and it is allowed to be one — but only when complete."""
    graph = pytest.importorskip("databridge_graph")
    from databridge_graph.clinic_seed import load_clinic_seed

    store = graph.GraphStore()
    load_clinic_seed(store)

    class _Brain:
        pass

    brain = _Brain()
    brain.store = store
    app.state.brain = brain

    citation = _facility_citation(client)
    impact = client.post("/evidence/impact",
                         json={"pointer": citation["pointer"]}).json()["impact"]
    assert impact["answer"] == "known"
    assert impact["records"] == []
    assert impact["reason"], "an empty list without a reason reads as reassurance"


# =======================================================================================
# the write route, and what it refuses
# =======================================================================================
def test_the_mutation_refuses_a_cell_this_gateway_never_served(client):
    client.get("/evidence/verify/citations")           # build the fleet
    pointer = {"source_id": "core_banking", "kind": "table_cell",
               "table": "core_banking.party", "column": "legal_name", "row_id": "CB-P-999999"}
    r = client.post("/evidence/verify/mutate", json={"pointer": pointer, "new_value": "x"})
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "MUTATION_REFUSED"


def test_the_mutation_never_writes_the_repo_fixture(client):
    """The estate under `fixtures/` must be byte-identical after a live mutation."""
    seed = DATABRIDGE_ROOT / "fixtures" / "packs" / "finance" / "core_banking" / "seed"
    before = {p.name: p.stat().st_mtime_ns for p in seed.glob("*.sql")}
    citation = _facility_citation(client)
    client.post("/evidence/verify/mutate", json={"pointer": citation["pointer"], "new_value": "7"})
    after = {p.name: p.stat().st_mtime_ns for p in seed.glob("*.sql")}
    assert before == after


def test_a_mutation_with_no_value_is_refused(client):
    citation = _facility_citation(client)
    r = client.post("/evidence/verify/mutate", json={"pointer": citation["pointer"]})
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "NO_NEW_VALUE"


# =======================================================================================
# R-015: authentication is the gate for everything real on this surface
# =======================================================================================
def test_the_lattice_needs_a_credential(app):
    """INT1 (2026-10-01, coordinator): the gateway serves nothing anonymously except `/health`.
    The lattice used to be the one open route here (engine vocabulary, nothing estate-specific);
    it is now gated like the rest — `lib/verify-api.ts` already sends the credential."""
    with TestClient(app) as bare:
        r = bare.get("/evidence/verify/lattice")
    assert r.status_code == 401 and r.json()["refusal"]["code"] == "UNAUTHENTICATED"
    with TestClient(app, headers=AUTH_HEADERS) as signed_in:
        assert signed_in.get("/evidence/verify/lattice").status_code == 200


def test_citations_require_a_credential(app):
    with TestClient(app) as bare:
        r = bare.get("/evidence/verify/citations")
    assert r.status_code == 401
    assert r.json()["refusal"]["code"] == "UNAUTHENTICATED"


def test_verify_requires_a_credential(app):
    pointer = {"source_id": "core_banking", "kind": "table_cell",
               "table": "core_banking.party", "column": "legal_name", "row_id": "1"}
    with TestClient(app) as bare:
        r = bare.post("/evidence/verify", json={"pointer": pointer})
    assert r.status_code == 401


def test_mutate_requires_a_credential(app):
    """The write route is held to the SAME gate as the reads — a caller who cannot read a citation
    must not be able to change one."""
    pointer = {"source_id": "core_banking", "kind": "table_cell",
               "table": "core_banking.facility", "column": "limit_amount", "row_id": "1"}
    with TestClient(app) as bare:
        r = bare.post("/evidence/verify/mutate", json={"pointer": pointer, "new_value": "1"})
    assert r.status_code == 401


def test_fleet_status_no_longer_leaks_a_filesystem_path(client):
    """R-015 MEDIUM: `working_copy` used to be the literal absolute temp-directory path."""
    import re

    status = client.get("/evidence/verify/fleet").json()
    source = status["sources"][0]
    assert source["working_copy"]
    assert not re.search(r"[\\/](Users|home|tmp|Temp)[\\/]", source["working_copy"] or ""), (
        "a filesystem path leaked into an HTTP response")


# =======================================================================================
# R-019 (Codex batch 2, finding 5): the fixture fleet acts as the SIGNED-IN identity
# =======================================================================================
def test_an_identity_with_no_fixture_grant_gets_no_citations_and_no_write(app):
    """Every caller used to run as a hard-coded `analyst-7`: a signed-in identity with no grant in
    the fixture estate received analyst-admitted citations and could drive the mutation."""
    from databridge_gateway.verify_fleet import FIXTURE_PRINCIPALS

    assert "staff" not in FIXTURE_PRINCIPALS, "the premise: `staff` holds no fixture grant"
    with TestClient(app, headers=auth_headers("analyst-7")) as analyst:
        citation = _facility_citation(analyst)
    with TestClient(app, headers=auth_headers("staff")) as staff:
        r = staff.get("/evidence/verify/citations")
        assert r.status_code == 403, r.text
        refusal = r.json()["refusal"]
        assert refusal["code"] == "NO_GRANT_IN_FIXTURE_ESTATE"
        assert not [n for n in FIXTURE_PRINCIPALS if n in refusal["message"]], \
            "the refusal must not name the identities that DO hold grants"
        m = staff.post("/evidence/verify/mutate",
                       json={"pointer": citation["pointer"], "new_value": "1.00"})
        assert m.status_code == 403, m.text
        v = staff.post("/evidence/verify", json={"pointer": citation["pointer"]})
        assert v.status_code == 403, v.text


def test_a_citation_served_to_one_identity_cannot_be_mutated_by_another(app):
    """The mutation control moves a value THIS caller was shown — not one served to somebody
    else, which the old single global register allowed."""
    from databridge_gateway.verify_fleet import FIXTURE_PRINCIPALS

    other = next(p for p in FIXTURE_PRINCIPALS if p != "analyst-7")
    with TestClient(app, headers=auth_headers("analyst-7")) as analyst:
        citation = _facility_citation(analyst)
    with TestClient(app, headers=auth_headers(other)) as c:
        r = c.post("/evidence/verify/mutate",
                   json={"pointer": citation["pointer"], "new_value": "1.00"})
    assert r.status_code == 422, r.text
    assert r.json()["refusal"]["code"] == "MUTATION_REFUSED"


def test_the_mutation_record_names_who_made_it(client):
    citation = _facility_citation(client)
    r = client.post("/evidence/verify/mutate",
                    json={"pointer": citation["pointer"], "new_value": "2.00"})
    assert r.json()["changed"]["by"] == "analyst-7"


def test_fleet_status_reports_the_signed_in_identitys_purpose(app):
    from databridge_gateway.verify_fleet import FIXTURE_PRINCIPALS

    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        mine = c.get("/evidence/verify/fleet").json()
    with TestClient(app, headers=auth_headers("staff")) as c:
        none = c.get("/evidence/verify/fleet").json()
    assert mine["read_purpose"] == FIXTURE_PRINCIPALS["analyst-7"]["purpose"]
    assert mine["has_grant"] is True
    assert none["read_purpose"] is None and none["has_grant"] is False


# =======================================================================================
# R-019 ROUND 2 (Codex gpt-5.6-sol): the fixture estate as configuration, and what the
# fixture fleet will no longer do
# =======================================================================================
def _other_evidence_identity() -> str:
    from databridge_gateway.verify_fleet import FIXTURE_PRINCIPALS

    return next(p for p in sorted(FIXTURE_PRINCIPALS) if p != "analyst-7")


def test_a_citation_served_to_one_identity_discloses_nothing_to_another(app):
    """Round 2 findings 2 + 5: the register was global, so B submitting A's pointer got A's
    `cited_value` back (and a fixture verdict about a pointer B was never served)."""
    other = _other_evidence_identity()
    with TestClient(app, headers=auth_headers("analyst-7")) as analyst:
        citation = _facility_citation(analyst)
    with TestClient(app, headers=auth_headers(other)) as c:
        body = c.post("/evidence/verify", json={"pointer": citation["pointer"]}).json()
    assert body["verdict"] == "unverifiable", body
    assert body["cited_value"] is None and body["cited_value_known"] is False
    assert body["current_value_known"] is False


def test_a_pointer_the_fixture_did_not_serve_is_never_verdicted_against_fixture_rows(client):
    """Finding 2: any pointer naming a fixture source id — a real source sharing that id, or a
    hand-made one — was verdicted against fixture rows."""
    citation = _facility_citation(client)
    forged = dict(citation["pointer"], row_id="not-a-row-this-fleet-served")
    body = client.post("/evidence/verify", json={"pointer": forged}).json()
    assert body["verdict"] == "unverifiable", body
    assert body["cited_value_known"] is False


def test_status_shows_only_the_callers_own_mutations(app):
    """Finding 5: `status()` exposed every mutation record (who, old value, new value) to every
    caller, including one with no grant here."""
    other = _other_evidence_identity()
    with TestClient(app, headers=auth_headers("analyst-7")) as analyst:
        citation = _facility_citation(analyst)
        r = analyst.post("/evidence/verify/mutate",
                         json={"pointer": citation["pointer"], "new_value": "3.00"})
        assert r.status_code == 200, r.text
        assert len(analyst.get("/evidence/verify/fleet").json()["mutations"]) == 1
    for who in (other, "staff"):
        with TestClient(app, headers=auth_headers(who)) as c:
            assert c.get("/evidence/verify/fleet").json()["mutations"] == [], who


def test_a_read_grant_is_not_a_write_grant(app):
    """Round 2 finding 11: any identity that could obtain a citation could mutate it."""
    from databridge_gateway.verify_fleet import MUTATE_ENTITLEMENT, fleet_for

    readers = [p for p, d in fleet_for(app).decl.directory("evidence").items()
               if MUTATE_ENTITLEMENT not in d.writes]
    assert readers, "every declared identity may write — the refusal below would be untested"
    who = readers[0]
    with TestClient(app, headers=auth_headers(who)) as c:
        body = c.get("/evidence/verify/citations").json()
        mine = body["citations"]
        assert mine, f"{who} was served no citation — the write check below would be vacuous"
        r = c.post("/evidence/verify/mutate",
                   json={"pointer": mine[0]["pointer"], "new_value": "x"})
        assert r.status_code == 403, r.text
        assert r.json()["refusal"]["code"] == "NO_WRITE_GRANT"
        assert c.get("/evidence/verify/fleet").json()["can_mutate"] is False


def test_a_mutation_of_a_row_that_no_longer_exists_commits_nothing(app, client):
    """Finding 11, second half: the update ignored its row count, so a deleted row produced a
    successful 'changed' record that changed nothing."""
    from sqlalchemy import text

    from databridge_gateway.verify_fleet import fleet_for

    citation = _facility_citation(client)
    fleet = fleet_for(app)
    reg = fleet._sources[citation["source_id"]]                    # noqa: SLF001
    key = next(e.key for e in reg.description.entities if e.name == citation["entity"])
    with reg.engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {reg.write_schema}.{citation['entity']} "
                          f"WHERE {key[0]} = :k"), {"k": citation["row_id"]})
    r = client.post("/evidence/verify/mutate",
                    json={"pointer": citation["pointer"], "new_value": "9.00"})
    assert r.status_code == 422, r.text
    assert r.json()["refusal"]["code"] == "MUTATION_REFUSED"
    assert client.get("/evidence/verify/fleet").json()["mutations"] == []


def test_the_gateway_fleet_modules_name_no_estate():
    """Round 2 finding 4: identities, purposes, roles, source ids and reads were compiled into
    `verify_fleet.py` / `orchestrator_fleet.py`. Every name the estate's declaration carries is
    DERIVED from the declaration and must not appear in either module's source."""
    from databridge_gateway import orchestrator_fleet, verify_fleet

    decls = verify_fleet.fixture_fleet_decls()
    assert decls, "no fixture-fleet declaration found — the scan below would be vacuous"
    names: set[str] = set()
    for d in decls:
        # (`actor_id` is the product's own service identity, shared by every surface — not the
        # estate's.)
        names |= {d.mode, d.actor_role, d.estate_label, *d.purpose_packs, *d.seed,
                  d.schema_dir.name, d.path.parent.name}
        for p in d.principals.values():
            names |= {p.id, p.role, p.purpose, p.grant_id}
        for a in (*d.orchestrate_adapters, *d.evidence_adapters):
            names |= {a.source_id}
        for r in d.reads:
            names |= {r.entity, *r.key}            # field names can be ordinary words
    names = {n for n in names if n and len(n) > 3}
    assert len(names) > 10, names
    for module in (verify_fleet, orchestrator_fleet):
        src = Path(module.__file__).read_text(encoding="utf-8")
        leaked = sorted(n for n in names if n in src)
        assert leaked == [], f"{Path(module.__file__).name} names estate values: {leaked}"


def test_a_declaration_naming_an_undeclared_role_or_purpose_is_refused(tmp_path):
    """The declaration is validated against the closed vocabularies — never partially loaded."""
    import shutil

    import yaml

    from databridge_gateway.verify_fleet import (
        FixtureFleetError,
        build_policy,
        evidence_fleet_decl,
        load_fixture_fleet,
    )

    real = evidence_fleet_decl()
    assert real is not None
    for field, bad in (("role", "no_such_role_anywhere"), ("purpose", "no.such.purpose")):
        estate = tmp_path / field
        shutil.copytree(real.path.parent, estate,
                        ignore=shutil.ignore_patterns("*.sql", "seed", "mailbox"))
        raw = yaml.safe_load(real.path.read_text(encoding="utf-8"))
        first = next(iter(raw["principals"]))
        raw["principals"][first][field] = bad
        (estate / real.path.name).write_text(yaml.safe_dump(raw), encoding="utf-8")
        decl = load_fixture_fleet(estate / real.path.name)
        with pytest.raises(FixtureFleetError):
            build_policy(decl, "evidence")


def test_changing_the_declaration_changes_the_fleet(tmp_path, monkeypatch):
    """Behaviour lives in the YAML: a second estate declaration with its own mode is discovered
    and selected by name, with no code change."""
    import shutil

    import yaml

    from databridge_gateway import verify_fleet

    real = verify_fleet.evidence_fleet_decl()
    root = tmp_path / "packs"
    estate = root / "other_estate"
    shutil.copytree(real.path.parent, estate,
                    ignore=shutil.ignore_patterns("*.sql", "seed", "mailbox"))
    raw = yaml.safe_load(real.path.read_text(encoding="utf-8"))
    raw["mode"] = "other_fixture"
    raw["principals"] = {k: v for k, v in list(raw["principals"].items())[:1]}
    raw["default_principal"] = next(iter(raw["principals"]))
    (estate / real.path.name).write_text(yaml.safe_dump(raw), encoding="utf-8")
    found = verify_fleet.fixture_fleet_decl("other_fixture", "evidence", root)
    assert found is not None and set(found.principals) == set(raw["principals"])
    monkeypatch.setenv(verify_fleet.EVIDENCE_FLEET_ENV, "other_fixture")
    assert verify_fleet.evidence_fleet_decl(root).mode == "other_fixture"


def test_a_fixture_citation_offered_for_another_value_is_not_verified(client):
    """Round 2 finding 7, fixture half: the offered `value` was ignored on the adapter path."""
    citation = _facility_citation(client)
    wrong = client.post("/evidence/verify",
                        json={"pointer": citation["pointer"], "value": "not-the-cited-value"})
    assert wrong.json()["verdict"] == "drift_material", wrong.json()
    right = client.post("/evidence/verify",
                        json={"pointer": citation["pointer"], "value": citation["value_at_read"]})
    assert right.json()["verdict"] == "verified", right.json()
