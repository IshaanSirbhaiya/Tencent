"""INT3 — the cross-track integration of mcp_ingest with its callers (2026-10-01).

* a DELEGATED approval (an adapter agent's ``auto_additive`` policy, delegated by the person who
  activated the agent) is accepted ONLY by `mapping.approve_delegated_additive`, ONLY for an
  additive change, and is recorded distinctly (``authenticated_by: "delegated:<digest>"``);
* every other approval gate refuses it (plans, mappings, overrides);
* owner overrides are bound to content (`runner._override_allows`), and the run binding changes
  when an approval's digests change;
* the extracted-text digest is recorded in a `<sha>.txt.json` sidecar and verified on read.
Everything synthetic; no network, no model."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from conftest import test_principal

pytestmark = pytest.mark.real_approval


@pytest.fixture
def ws_vocab():
    from databridge_graph.packs import deployment

    with deployment("workspace") as vocab:
        yield vocab


def _snap(table, cols):
    return {"table": table, "columns": [{"name": c, "type": "text"} for c in cols],
            "primary_key": ["id"], "foreign_keys": [],
            "rows": [{c: f"{c}-{i}" for c in cols} for i in range(3)]}


def _approved_org(source_id, vocab):
    """An `organisation` table mapping, approved by an authenticated person."""
    from databridge_mcp_ingest.mapping import (SourceMapping, _table_mapping, approve_mapping,
                                               save_mapping)

    from databridge_mcp_ingest import register_connection

    # R-022 f2 #2: a delegation is checked against the connection's DECLARED scope
    register_connection({"id": source_id, "transport": {"type": "stdio", "command": "uvx"}})
    tm = _table_mapping("organisation", _snap("organisation", ["id", "name", "domain"]),
                        "organisation", {}, vocab, origin="rules", why="test")
    save_mapping(SourceMapping(source_id=source_id, tables={"organisation": tm}))
    m = approve_mapping(source_id, principal=test_principal("Owner Person"), vocab=vocab)
    assert m.tables["organisation"].status == "approved"                  # non-vacuous
    return m.tables["organisation"]


def _delegation(delegated_by="Owner Person", policy="auto_additive", adaptation_id="ad_test"):
    """A delegation as an adapter agent mints one (AA3): issued FROM a sealed record, the record
    an authenticated activation writes to the hash-chained delegations log. A name the principal
    layer refuses (blank, a machine actor) gets no seal, so its delegation is refused at
    acceptance — as it must be. INT5: replaces the unsealed helper AA3 made red by design."""
    import tempfile
    from pathlib import Path

    from databridge_adapter_agents.adapt import (Policy, _mint_delegation, policy_digest,
                                                 principal_of)
    from databridge_adapter_agents.delegations import DelegationLog

    spec = SimpleNamespace(id="larkspur_sources", adapt={"policy": policy})
    try:
        who = principal_of(test_principal(delegated_by), "test activation")
    except Exception:                                    # noqa: BLE001 — nobody activated it
        return _mint_delegation(spec, delegated_by=delegated_by, adaptation_id=adaptation_id)
    root = Path(tempfile.mkdtemp(prefix="int5_seal_"))
    log = DelegationLog(lambda: root)
    seal = log.seal(agent_id=spec.id, who=who, policy=policy,
                    policy_digest=policy_digest(Policy.of(spec)),
                    scope_digest="sha256:" + "0" * 64, spec_digest="",
                    history_line={}, history_n=1)
    return _mint_delegation(spec, seal=seal, seal_log=log.path(spec.id),
                            adaptation_id=adaptation_id)


def _with_new_attr(tm):
    entry = tm.model_dump(mode="json")
    entry["node"] = {**entry["node"],
                     "attrs": {**(entry["node"].get("attrs") or {}), "region": "region"}}
    entry["columns"] = [*entry["columns"], "region"]        # f2 #9: the column now exists
    return entry


# --- delegated approval ---------------------------------------------------------------------------
def test_a_delegation_approves_an_additive_column_and_is_recorded_distinctly(walnut_env, ws_vocab):
    from databridge_mcp_ingest.mapping import (approval_log_path, approval_problem,
                                               approve_delegated_additive, load_mapping)

    tm = _approved_org("d1", ws_vocab)
    d = _delegation()
    m = approve_delegated_additive("d1", "organisation", _with_new_attr(tm), delegation=d,
                                   vocab=ws_vocab)
    got = m.tables["organisation"]
    assert got.status == "approved" and got.node["attrs"]["region"] == "region"
    assert got.approval["authenticated_by"] == f"delegated:{d.policy_digest}"
    assert got.approval["delegated_by"] == got.approval["principal_id"] == "Owner Person"
    assert got.approval["principal_type"] == "DelegatedApproval"
    # it is honoured at run time like any verified approval...
    assert approval_problem("d1", "organisation", load_mapping("d1").tables["organisation"],
                            ws_vocab) is None
    # ...and the append-only log tells it apart from the person's own approval
    lines = [json.loads(x) for x in approval_log_path("d1").read_text("utf-8").splitlines()]
    assert [ln["authenticated_by"].startswith("delegated:") for ln in lines] == [False, True]


@pytest.mark.parametrize("change", ["drop_attr", "relabel", "rekind", "resource", "repoint"])
def test_a_delegation_never_approves_a_non_additive_change(walnut_env, ws_vocab, change):
    from databridge_mcp_ingest.mapping import (MappingApprovalRefused,
                                               approve_delegated_additive, load_mapping)

    tm = _approved_org("d2", ws_vocab)
    before = load_mapping("d2").model_dump(mode="json")
    entry = tm.model_dump(mode="json")
    node = dict(entry["node"])
    if change == "drop_attr":
        assert node.get("attrs")                                          # non-vacuous
        node["attrs"] = {}
    elif change == "relabel":
        node["label"] = "{id}"
    elif change == "rekind":
        node["kind"] = "project"
    elif change == "resource":                # the row identity read from another column
        assert node["sources"] != ["name"]                                # non-vacuous
        node["sources"] = ["name"]
    else:                                     # an existing attribute re-pointed at a new column
        node["attrs"] = {**node["attrs"], "name": "domain"}
    entry["node"] = node
    with pytest.raises(MappingApprovalRefused, match="a person must approve"):
        approve_delegated_additive("d2", "organisation", entry, delegation=_delegation(),
                                   vocab=ws_vocab)
    assert load_mapping("d2").model_dump(mode="json") == before           # nothing written


def test_a_delegation_never_maps_a_new_table_to_a_subject_kind(walnut_env, ws_vocab):
    from databridge_mcp_ingest.mapping import (MappingApprovalRefused, SourceMapping,
                                               _table_mapping, approve_delegated_additive,
                                               save_mapping)

    subject = next(k for k in ws_vocab.node_kinds if ws_vocab.is_subject(k))
    save_mapping(SourceMapping(source_id="d3", tables={}))
    tm = _table_mapping("people", _snap("people", ["id", "name"]), subject, {}, ws_vocab,
                        origin="rules", why="test")
    with pytest.raises(MappingApprovalRefused, match="subject"):
        approve_delegated_additive("d3", "people", tm.model_dump(mode="json"),
                                   delegation=_delegation(), vocab=ws_vocab)


def test_only_a_minted_delegation_by_a_person_under_auto_additive_is_accepted(walnut_env,
                                                                              ws_vocab):
    from databridge_adapter_agents.adapt import DelegatedApproval
    from databridge_mcp_ingest.mapping import (MappingApprovalRefused,
                                               approve_delegated_additive)

    tm = _approved_org("d4", ws_vocab)
    entry = _with_new_attr(tm)
    forged = DelegatedApproval(agent_id="x", delegated_by="Owner Person", policy="auto_additive",
                               policy_digest="sha256:0", adaptation_id="ad_x")
    for bad in (forged, test_principal("Owner Person"), "Owner Person",
                {"delegated_by": "Owner Person"}, _delegation(policy="ask_all"),
                _delegation(delegated_by="adapter_agent:x"), _delegation(delegated_by="")):
        with pytest.raises(MappingApprovalRefused):
            approve_delegated_additive("d4", "organisation", entry, delegation=bad,
                                       vocab=ws_vocab)


def test_no_other_approval_gate_accepts_a_delegation(walnut_env, ws_vocab):
    from databridge_mcp_ingest.mapping import MappingApprovalRefused, approve_mapping
    from databridge_mcp_ingest.plans import ApprovalRefused, require_authenticated_approver
    from databridge_mcp_ingest.screen import OverrideRefused, approve_override, content_digest

    _approved_org("d5", ws_vocab)
    d = _delegation()
    with pytest.raises(ApprovalRefused):
        require_authenticated_approver(d)
    with pytest.raises(MappingApprovalRefused):
        approve_mapping("d5", principal=d, vocab=ws_vocab)
    with pytest.raises(OverrideRefused):
        approve_override("d5", "item-1", digest=content_digest(b"x"), principal=d, reason="r")


# --- overrides are bound to content ---------------------------------------------------------------
def test_an_override_admits_only_the_content_it_approved(walnut_env):
    from databridge_mcp_ingest.runner import _override_allows, _overrides_fingerprint
    from databridge_mcp_ingest.screen import approve_override, content_digest, load_overrides

    v1, v2 = content_digest(b"version one"), content_digest(b"version two")
    assert not _override_allows(load_overrides("o1"), "item-1", v1)
    before = _overrides_fingerprint(load_overrides("o1"))
    approve_override("o1", "item-1", digest=v1, principal=test_principal("Owner Person"),
                     reason="reviewed")
    got = load_overrides("o1")
    assert _override_allows(got, "item-1", v1)
    assert not _override_allows(got, "item-1", v2)            # a later version is not lifted
    assert not _override_allows({"item-1"}, "item-1", v1)     # a bare id set binds no content
    assert _overrides_fingerprint(got) != before              # a new approval rebinds the run


# --- the extracted-text digest --------------------------------------------------------------------
def test_the_text_digest_is_recorded_beside_the_text_and_verified(walnut_env):
    from databridge_mcp_ingest import capture
    from databridge_mcp_ingest.extract import text_digest

    sha = capture.put_bytes(b"%PDF fake bytes")
    capture.put_meta(sha, {"source": "s", "uri": "file:///a.pdf", "mime": "application/pdf",
                           "title": "a.pdf", "retrieved_at": "2026-10-01T00:00:00Z"})
    text = "Extracted text of a.pdf"
    capture.put_text(sha, text, text_sha256=text_digest(text), extractor="docling")
    side = capture.get_text_meta(sha)
    assert side == {"text_sha256": text_digest(text), "extractor": "docling",
                    "capture_meta_digest": capture.stored_meta_digest(sha)}
    assert side["capture_meta_digest"]                                    # non-vacuous
    assert capture.get_text(sha, expect_text_digest=text_digest(text)) == text
    with pytest.raises(capture.CaptureCorrupt):
        capture.get_text(sha, expect_text_digest=text_digest("other"))
    with pytest.raises(capture.CaptureCorrupt):
        capture.put_text(sha, text, text_sha256=text_digest("other"))
