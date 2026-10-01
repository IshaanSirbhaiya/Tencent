"""What `/intelligence` is allowed to say — asserted over the payloads it actually returns.

The suite this file replaces checked that the `/ask` body named no patient. That is a check on one
payload, and the review found what it missed: the *drill-down*. `/ask` cites a document; a member
of staff clicks the citation; `/intelligence/subgraph/{id}` ran a generic traversal, walked the
document's `for_patient` edges, and returned registry patient nodes with their date of birth and
nationality, beside the edge's own `match_score` and `matched_on`. Named, ranked candidates, one
click from a page whose entire contract is that it never produces one — and every existing test
passed, because each of them scanned one payload and none of them followed a link.

So this file walks the surface the way a user does:

1. ask a business question;
2. follow **every** citation it returns into the drill-down;
3. follow the drill-down's own members one hop further;
4. and assert, over everything that came back, that no registry name, no candidate score, no
   document filename and no `for_patient` edge appears anywhere in it.

The other half is the ordinary label. `Alice_Tan_referral.pdf` is a perfectly normal document
label — not injection-shaped, so no content screen sees it — and it was reported as a value, used
as the entity id, and repeated inside every nested `SourcePointer`. The fixture below puts exactly
that filename in the graph and then looks for it in every field of every payload.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from databridge_gateway.intelligence_routes import _register_intelligence_routes

from conftest import AUTH_HEADERS

BUSINESS_QUESTION = ("which issuers sent the most documents this week, and what is still "
                     "blocked?")

#: An ORDINARY document label — a filename with a patient's name in it. Nothing about this is
#: injection-shaped, which is the point: no content screen can see it, and it used to render.
NAMED_FILENAME = "Alice_Tan_referral.pdf"
NAMED_DOCUMENT = f"document:{NAMED_FILENAME}"


def _pointer(document_id: str):
    from databridge_core import SourcePointer

    return SourcePointer(source_id=document_id, kind="doc_span", document_id=document_id,
                         char_start=0, char_end=64)


@pytest.fixture(scope="module")
def app():
    from brain_server import BrainTools
    from databridge_evals.e_intake import gold_harness
    from databridge_graph.schema import GraphNode, NodeKind

    brain = BrainTools(harness=gold_harness())
    brain.load_seed()
    when = dt.datetime(2026, 8, 10, tzinfo=dt.timezone.utc)
    # Unlinked to any registry row, so it lands in `documents_needing_a_person` — the read that
    # used to publish the label as a value.
    brain.store.add_node(GraphNode(id=NAMED_DOCUMENT, kind=NodeKind.DOCUMENT,
                                   label=NAMED_FILENAME, canonical_id=NAMED_FILENAME,
                                   sources=[_pointer(NAMED_FILENAME)],
                                   valid_from=when, system_from=when))
    a = FastAPI()
    _register_intelligence_routes(a)
    a.state.brain = brain
    return a


@pytest.fixture(scope="module")
def client(app):
    with TestClient(app, headers=AUTH_HEADERS) as c:
        yield c


@pytest.fixture(scope="module")
def answered(client):
    r = client.post("/intelligence/ask", json={"question": BUSINESS_QUESTION})
    assert r.status_code == 200, r.text
    return r.json()


def _registry_names(app) -> list[str]:
    from databridge_graph.schema import NodeKind

    return [row["label"] for row in app.state.brain.store.open_nodes_of_kind(NodeKind.PATIENT)]


def _document_filenames(app) -> list[str]:
    from databridge_graph.schema import NodeKind

    return [row["label"] for row in app.state.brain.store.open_nodes_of_kind(NodeKind.DOCUMENT)]


def _forbidden(app) -> list[str]:
    """Everything that must not appear, in one list: names, filenames, and score vocabulary."""
    return [*_registry_names(app), *_document_filenames(app),
            "match_score", "matched_on", "for_patient", "date_of_birth", "nationality"]


def _offenders(payload, forbidden: list[str]) -> list[str]:
    flat = json.dumps(payload).lower()
    return sorted({token for token in forbidden if token and token.lower() in flat})


def _walk(client, root: str, as_of=None, seen=None) -> list[dict]:
    """Every drill-down reachable from `root` in two hops, as a user clicking through would."""
    seen = seen if seen is not None else set()
    if root in seen:
        return []
    seen.add(root)
    params = {"as_of": as_of} if as_of else {}
    response = client.get(f"/intelligence/subgraph/{root}", params=params)
    assert response.status_code == 200, f"{root} → {response.status_code}: {response.text[:200]}"
    body = response.json()
    out = [body]
    for node in body["nodes"]:
        out.extend(_walk(client, node["id"], as_of=as_of, seen=seen))
    return out


# --- the fixture is real ---------------------------------------------------------------
def test_the_fixture_puts_a_persons_name_in_an_ordinary_document_label(app):
    """Guards every negative assertion below: the hazard must actually be in the graph."""
    assert NAMED_FILENAME in _document_filenames(app)
    assert _registry_names(app), "the registry has no patients, so nothing could leak"


# --- 1. the drill-down ------------------------------------------------------------------
def test_no_citation_leads_to_a_named_or_scored_candidate(client, app, answered):
    """The finding, made a test. Follow every citation the route returns, then keep going.

    `/ask` cites a DOCUMENT; the drill-down used to walk that document's `for_patient` edges and
    return the registry rows behind them, their attributes, and the edge's `match_score` and
    `matched_on`. The old suite asserted only that the citation ROOT resolved and only scanned the
    `/ask` body, so it passed throughout.
    """
    assert answered["values"], "nothing was cited, so nothing was checked"
    forbidden = _forbidden(app)
    bodies = []
    for value in answered["values"]:
        bodies.extend(_walk(client, value["source"]["source_id"]))
    assert bodies, "no citation resolved"
    for body in bodies:
        assert not _offenders(body, forbidden), (
            f"the drill-down from a citation returned {_offenders(body, forbidden)}")
        assert all(node["kind"] != "patient" for node in body["nodes"])
        assert all(edge["kind"] != "for_patient" for edge in body["edges"])


def test_a_registry_row_is_not_reachable_by_asking_for_it_directly(client, app):
    """Not reachable by traversal, and not reachable by typing its id either."""
    from databridge_graph.schema import NodeKind

    patient = app.state.brain.store.open_nodes_of_kind(NodeKind.PATIENT)[0]["id"]
    r = client.get(f"/intelligence/subgraph/{patient}")
    # R-015: 403, not 422 — this entity EXISTS and is not releasable here, which is the same shape
    # of refusal `/evidence/trace/{id}` gives `AWAITING_HUMAN_CONFIRMATION` (403), not a "bad
    # request" (422). One existence-oracle policy applied uniformly across both surfaces.
    assert r.status_code == 403
    assert r.json()["refusal"]["code"] == "NOT_ON_THIS_SURFACE"
    assert not _offenders(r.json(), _registry_names(app))


def test_the_figures_and_their_members_lead_nowhere_near_a_patient(client, app):
    """The cards drill down through the same route the citations do, so they get the same test."""
    graph = client.get("/intelligence").json()["graph"]
    forbidden = _forbidden(app)
    assert not _offenders(graph, forbidden), _offenders(graph, forbidden)
    members = [m for figure in ("ambiguity", "unlinked_documents", "conflicts")
               for m in graph[figure]["members"]]
    members += [m for row in graph["issuer_mix"] for m in row["members"]]
    assert members, "no figure named anything, so nothing was walked"
    for member in members:
        for body in _walk(client, member):
            assert not _offenders(body, forbidden), _offenders(body, forbidden)


def test_the_conflicts_figure_says_out_loud_that_it_withheld_its_members(client):
    """Two of this page's rules collide here, and the collision is announced.

    `conflicts` counts REGISTRY ROWS. "Every figure names the ids it counted" and "this page never
    names a patient" cannot both hold for it, constraint 1 wins, and an empty `members` with no
    stated reason would read as a bug — or, worse, as a zero.
    """
    from databridge_gateway.intelligence_surface import MEMBERS_WITHHELD

    conflicts = client.get("/intelligence").json()["graph"]["conflicts"]
    assert conflicts["n"] > 0, "the fixture has no conflict, so this asserts nothing"
    assert conflicts["members"] == []
    assert conflicts["members_withheld"] == MEMBERS_WITHHELD


# --- 3. the ordinary document label -----------------------------------------------------
def test_a_filename_carrying_a_persons_name_appears_nowhere_on_this_surface(client, app,
                                                                            answered):
    """The whole surface, field by field: the answer, the figures, and every drill-down.

    The entity id and the nested `SourcePointer`s repeated the filename too, which is why this
    scans serialised payloads rather than a list of fields somebody remembered to check.
    """
    filenames = _document_filenames(app)
    assert NAMED_FILENAME in filenames
    assert not _offenders(answered, filenames), _offenders(answered, filenames)
    assert not _offenders(client.get("/intelligence").json(), filenames)
    for value in answered["values"]:
        for body in _walk(client, value["source"]["source_id"]):
            assert not _offenders(body, filenames), _offenders(body, filenames)


def test_a_document_is_reported_as_a_handle_and_a_closed_vocabulary_kind(client, answered):
    """The trade, asserted. A wholly opaque handle would be useless to staff; this is one step
    short of that — the prefix is the node's KIND, a member of the bound packs' closed
    vocabulary, never anything the document wrote."""
    from databridge_graph.meta import NodeMetaKind
    from databridge_graph.packs import active_vocabulary

    document_kinds = set(active_vocabulary().nodes_binding_to(NodeMetaKind.DOCUMENT))
    documents = [v for v in answered["values"]
                 if v["tool"] == "documents_needing_a_person" and v["field"] == "document"]
    assert documents, "nothing was blocked, so this asserts nothing"
    for value in documents:
        text = value["text"]
        kind, _, handle = (part.strip() for part in text.partition("·"))
        assert handle.startswith("doc-"), text
        assert kind in document_kinds, text


def test_a_handle_walks_back_to_the_entity_it_names(client, answered):
    """An opaque handle that did not resolve would be strictly worse than the filename: it would
    be a citation nobody can check, which is the thing this page exists not to have."""
    for value in answered["values"]:
        body = client.get(f"/intelligence/subgraph/{value['source']['source_id']}")
        assert body.status_code == 200
        assert body.json()["root"] == value["source"]["source_id"]
        assert body.json()["sources"].get(value["source"]["source_id"])


def test_an_organisation_prints_its_name_only_when_the_catalogue_authored_it(app):
    """The rule is about provenance, not phrasing.

    An issuer in `coverage_rules.yaml` is a catalogue entry somebody committed to git and it
    prints. An organisation the graph holds only because a chit's employer field said so is
    document text, and gets a handle — because an employer field can carry a person's name for
    exactly the same reason a filename can.
    """
    import datetime as dt

    from databridge_graph.schema import GraphNode, NodeKind

    from databridge_gateway.intelligence_surface import SurfaceNames

    store = app.state.brain.store
    when = dt.datetime(2026, 8, 10, tzinfo=dt.timezone.utc)
    store.add_node(GraphNode(id="org:alice-tan-consulting", kind=NodeKind.ORG,
                             label="Alice Tan Consulting", sources=[_pointer("chit_9.txt")],
                             valid_from=when, system_from=when))
    names = SurfaceNames(store)

    from_document, is_handle = names.display("org:alice-tan-consulting")
    assert is_handle and "Alice" not in from_document

    catalogued = next(row["id"] for row in store.open_nodes()
                      if row["kind"] == "org" and row["canonical_id"])
    text, handled = names.display(catalogued)
    assert handled is False and text


# --- 5. the window ----------------------------------------------------------------------
def test_the_drilldown_reads_the_window_the_answer_was_computed_in(client, app):
    """`as_of` was accepted by `/ask` and ignored by `/intelligence/subgraph`, so a historical
    answer opened into the present graph — evidence that had changed, or none at all."""
    as_of = "2026-01-02T00:00:00+00:00"
    r = client.post("/intelligence/ask", json={"question": BUSINESS_QUESTION, "as_of": as_of})
    assert r.status_code in (200, 422), r.text

    graph = client.get("/intelligence", params={"as_of": as_of}).json()["graph"]
    entity = next(row["issuer_id"] for row in graph["issuer_mix"]) if graph["issuer_mix"] else None
    if entity is None:                    # nothing was valid that early; use the live window
        entity = client.get("/intelligence").json()["graph"]["issuer_mix"][0]["issuer_id"]

    rewound = client.get(f"/intelligence/subgraph/{entity}", params={"as_of": as_of})
    live = client.get(f"/intelligence/subgraph/{entity}")
    assert rewound.status_code == 200 and live.status_code == 200
    assert rewound.json()["as_of"] == as_of
    assert live.json()["as_of"] is None
    # The seed's documents are all later than this instant, so the rewound walk must be smaller.
    assert len(rewound.json()["edges"]) < len(live.json()["edges"]), (
        "the drill-down ignored `as_of` and read the present graph")


def test_a_malformed_as_of_on_the_drilldown_is_refused_and_not_echoed(client):
    r = client.get("/intelligence/subgraph/org:x", params={"as_of": "<script>last tuesday"})
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "BAD_AS_OF"
    assert "last tuesday" not in r.text and "script" not in r.text


# --- 6. bounds that refuse --------------------------------------------------------------
def test_a_graph_past_this_pages_envelope_is_refused_not_truncated(client, monkeypatch):
    """Silent truncation is the one degrade this page must never have: every figure on it is a
    count, and a count over part of a graph is a wrong number, not a short one."""
    from databridge_gateway import intelligence_surface

    monkeypatch.setattr(intelligence_surface, "MAX_GRAPH_NODES", 1)

    summary = client.get("/intelligence").json()
    assert summary["graph"] is None
    assert "withheld" in summary["graph_unavailable"]

    asked = client.post("/intelligence/ask", json={"question": BUSINESS_QUESTION})
    assert asked.status_code == 503
    assert asked.json()["refusal"]["code"] == "GRAPH_TOO_LARGE"

    walked = client.get("/intelligence/subgraph/org:x")
    assert walked.status_code == 503
    assert walked.json()["refusal"]["code"] == "GRAPH_TOO_LARGE"


def test_a_traversal_that_reaches_too_far_is_refused_not_trimmed(client, monkeypatch):
    """`depth` bounded hops and nothing else — not degree, not response size. A single issuer with
    a thousand documents was a depth-1 walk that returned a thousand nodes."""
    from databridge_gateway import intelligence_surface

    monkeypatch.setattr(intelligence_surface, "MAX_SUBGRAPH_NODES", 1)
    entity = client.get("/intelligence").json()["graph"]["issuer_mix"][0]["issuer_id"]
    r = client.get(f"/intelligence/subgraph/{entity}", params={"depth": 3})
    assert r.status_code == 503
    assert r.json()["refusal"]["code"] == "SUBGRAPH_TOO_LARGE"
    assert "nodes" not in r.json(), "a bounded traversal must not return a trimmed graph"


# --- 7. the response contract -----------------------------------------------------------
def test_every_refusal_body_validates_against_the_declared_type(client):
    """`_refusal` returned a hand-built dict through `JSONResponse`, which bypasses
    `response_model` entirely — so the error path, the one most likely to be wrong, was the one
    path with no contract on it."""
    from databridge_gateway.intelligence_routes import SurfaceRefusal

    responses = [
        client.post("/intelligence/ask", json={"question": "  "}),
        client.post("/intelligence/ask", json={"question": "x" * 5000}),
        client.post("/intelligence/ask", json={"question": "what is blocked?",
                                               "as_of": "last tuesday"}),
        client.get("/intelligence/subgraph/document:nope.txt"),
        client.get("/intelligence", params={"as_of": "last tuesday"}),
    ]
    for response in responses:
        assert response.status_code >= 400, response.text
        # Raises on an extra key, a missing key or a wrong type — `extra="forbid"`.
        SurfaceRefusal.model_validate(response.json())
