"""R-029 SETX (E2E run 5, 2026-10-03): W-05 and W-06 at the package level.

W-05: a document source an earlier Sources run already built is discovered as "unchanged". The
hand-off gate counted only screen-ALLOWED (new) documents, so such a source could never be handed
to its keeper. The documents a person decides on now include the already-built ones (to keep,
not rebuild); the decision is still a person's, bound to exactly this discovery.

W-06: the setup surfaces named an absolute server path for a held document, and the explanation's
model was the literal string "model".

The discovery shapes below are the run's own (`var/e2e5-workspace-20261002T1515/ingest/
larkspur-files/20261002T152701Z-36e402/discovery.json`): decisions `unchanged` / `review` /
`block`, absolute Windows URIs. Expectations are derived from the shape, never written down.
"""
from __future__ import annotations

import pytest

ROOT = "C:\\srv\\e2e\\root\\fixtures\\workspace_demo\\files"


def _disc(items, *, run_id="20261002T152701Z-36e402", tables=None):
    from databridge_mcp_ingest.runner import DISCOVERED

    return {"run_id": run_id, "status": DISCOVERED, "plan_digest": "p1",
            "tables": tables or {}, "documents": {"captured": len(items), "allowed": sum(
                1 for i in items if i["decision"] == "allow"), "items": items}}


def _item(i, decision, rel, *, held=False):
    return {"item_id": f"id-{i}", "sha256": f"sha-{i}", "decision": decision, "held": held,
            "uri": f"{ROOT}\\{rel}"}


#: the run-5 shape: everything already built, one held for review, one blocked
RUN5 = [_item(0, "review", "contracts\\Corriedale\\SOW-2026-021_Demand_Forecasting.docx"),
        _item(1, "unchanged", "contracts\\Pellucid\\MSA-2026-014_Pellucid_Freightways.pdf"),
        _item(2, "block", "engineering\\deploy_notes.txt"),
        _item(3, "unchanged", "finance\\invoices\\INV-2026-0041.pdf")]


def _gate(disc, decision=None):
    from databridge_adapter_agents.setup import handoff_problems

    class _V:
        packs = ["workspace"]

    return handoff_problems("larkspur-files", plan_status="approved", plan_stale=None,
                            plan_digest="p1", discovery=disc, mapping=None, explanation=None,
                            vocab=_V(), documents_decision=decision)


# --- W-05 -------------------------------------------------------------------------------------
def test_w05_an_already_built_document_source_needs_a_persons_decision_then_hands_off():
    from databridge_adapter_agents.setup import documents_digest, documents_problems

    disc = _disc(RUN5)
    adopted = [i for i in RUN5 if i["decision"] == "unchanged"]
    assert adopted and not [i for i in RUN5 if i["decision"] == "allow"]   # the run-5 shape
    dig = documents_digest(disc)
    assert dig is not None, "already-built documents are something a keeper can keep"
    problems = _gate(disc)
    assert problems, "nothing is handed off before a person decides"
    assert not any("captured nothing" in p for p in problems)
    assert any(str(len(adopted)) in p for p in problems)     # names how many it would keep
    decision = {"decision": "adopt", "discovery_run_id": disc["run_id"], "documents_digest": dig,
                "principal_id": "alice"}
    assert documents_problems(decision, disc) == []
    assert _gate(disc, decision) == []
    # bound to exactly this discovery: another run, another digest, or a machine refuse
    assert documents_problems({**decision, "discovery_run_id": "other"}, disc)
    assert documents_problems({**decision, "documents_digest": "sha256:x"}, disc)
    assert documents_problems({**decision, "principal_id": "adapter_agent:keeper-x"}, disc)


def test_w05_a_held_or_blocked_document_is_never_adopted():
    from databridge_adapter_agents.setup import documents_digest

    only_held = _disc([RUN5[0], RUN5[2], {**RUN5[1], "held": True}])
    assert documents_digest(only_held) is None
    assert any("captured nothing" in p for p in _gate(only_held))


def test_w05_the_digest_changes_when_what_is_kept_changes_and_build_digests_are_unchanged():
    from databridge_adapter_agents.setup import documents_digest

    base = documents_digest(_disc(RUN5))
    more = documents_digest(_disc([*RUN5, _item(9, "unchanged", "x.md")]))
    assert base != more
    # a build-only discovery digests exactly as before (an earlier decision still verifies)
    import hashlib

    build = [_item(5, "allow", "a.md"), _item(6, "allow", "b.md")]
    want = "sha256:" + hashlib.sha256("\n".join(sorted(
        f"{i['item_id']}:{i['sha256']}" for i in build)).encode("utf-8")).hexdigest()
    assert documents_digest(_disc(build)) == want


def test_w05_record_documents_decision_names_what_is_built_and_what_is_kept(tmp_path):
    from databridge_adapter_agents.setup import documents_decision, record_documents_decision

    disc = _disc([*RUN5, _item(7, "allow", "new.md")])
    who = {"principal_id": "alice", "authenticated_by": "gateway",
           "principal_type": "AuthenticatedPrincipal", "dev_only": False}
    rec = record_documents_decision(tmp_path, "larkspur-files", who=who, discovery=disc)
    assert (rec["to_build"], rec["to_keep"]) == (
        sum(1 for i in disc["documents"]["items"] if i["decision"] == "allow"),
        sum(1 for i in disc["documents"]["items"] if i["decision"] == "unchanged"))
    assert documents_decision(tmp_path, "larkspur-files")["documents_digest"] == \
        rec["documents_digest"]


# --- W-06 -------------------------------------------------------------------------------------
def test_w06_a_held_document_is_named_by_its_source_relative_path():
    from databridge_adapter_agents.setup import pending_decisions

    class _V:
        def identity_rule_for(self, kind):
            return None

    got = pending_decisions("larkspur-files", mapping=None, discovery=_disc(RUN5), vocab=_V(),
                            overrides=set())
    holds = [d for d in got if d["kind"] == "hold"]
    assert holds                                                         # non-vacuous
    for d in holds:
        assert ROOT not in d["table"] and ROOT not in d["what"], d
        assert ":\\" not in d["what"] and not d["table"].startswith(("/", "\\"))
        # still names the document, relative to what the source holds
        assert d["table"].replace("\\", "/").endswith("SOW-2026-021_Demand_Forecasting.docx")
        assert "/" in d["table"].replace("\\", "/")                     # its folder, not a guess


@pytest.mark.parametrize("uri,others,want", [
    ("/srv/data/a/b.md", ["/srv/data/c/d.md"], "a/b.md"),
    ("mcp://drive/Folder/x.pdf", [], "mcp://drive/Folder/x.pdf"),        # not a server path
    ("public.invoices", [], "public.invoices"),                            # a table reference
])
def test_w06_display_path_strips_only_a_server_filesystem_prefix(uri, others, want):
    from databridge_adapter_agents.setup import display_ref

    assert display_ref(uri, [uri, *others]) == want


def test_w06_the_explanation_names_the_model_that_ran_never_the_word_model():
    from databridge_adapter_agents.explain import model_of_event

    class _Ev:
        model_used = "anthropic/claude-x"

    assert model_of_event(_Ev()) == "anthropic/claude-x"
    assert model_of_event({"model_used": "openai/gpt-x"}) == "openai/gpt-x"
    assert model_of_event({"model": "fake/explain"}) == "fake/explain"
    assert model_of_event(None) is None and model_of_event(object()) is None
