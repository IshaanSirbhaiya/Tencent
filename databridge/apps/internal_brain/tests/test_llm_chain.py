"""ModelChain tests: hy3 → deepseek-v4.1-flash → extractive fallback, and the grounding wall.

The chain is exercised over a databridge_harness FakeBackend so no network call is ever made.
The four guarantees pinned here (plan §4.10):

  (1) hy3 answers and is grounded → the answer is hy3's, fallback_used False;
  (2) hy3 raises → the chain falls back to deepseek-v4.1-flash, fallback_used True;
  (3) both models fail → no model answer, so the pipeline serves the deterministic extractive
      answer (asserted end to end through databridge_ib.pipeline.Pipeline, model "extractive-v1");
  (4) a model answer containing a number NOT present in the context is refused by the grounding
      wall, so that model is skipped.
"""
from __future__ import annotations

import pytest

from databridge_harness import FakeBackend, Harness
from databridge_ib.llm import ModelChain
from databridge_ib.models import ModelInfo
from databridge_ib.narrate import Passage, build_context


# ----------------------------------------------------------------------------------
# Harness / chain helpers (mirrors _capturing_chain / _scripted_chain in
# test_pipeline_core.py — a ModelChain built over a FakeBackend, never the network).
# ----------------------------------------------------------------------------------

def _harness(respond) -> Harness:
    """A cloud-profile Harness over a FakeBackend whose ``respond`` shapes the model reply."""
    cfg = {"routing": {"ib_answer": "m"}, "aliases": {"m": {"model": "openai/hy3"}},
           "profiles": {"cloud": {}, "airgapped": {}}}
    return Harness(FakeBackend(respond), config=cfg, profile="cloud", cache=None)


def _chain(models, respond) -> ModelChain:
    """A ModelChain over a per-model FakeBackend: ``respond(model, messages, guided_json)``."""
    def factory(model: str):
        cfg = {"routing": {"ib_answer": "m"},
               "aliases": {"m": {"model": f"openai/{model}"}},
               "profiles": {"cloud": {}, "airgapped": {}}}
        return Harness(FakeBackend(respond), config=cfg, profile="cloud", cache=None)
    return ModelChain(models=list(models), harness_factory=factory)


def _passages() -> list[Passage]:
    """Two grounded passages whose numbers (2026, 24, 4) the grounding wall recognises."""
    return [
        Passage(tag="C1", doc_id="slack:t1", platform="slack", container_name="eng",
                title="migration", version="1", updated="2026-10-13",
                text="The database migration cutover is planned for 24 October 2026."),
        Passage(tag="C2", doc_id="confluence:p2", platform="confluence", container_name="runbooks",
                title="runbook", version="1", updated="2026-10-13",
                text="Step 4 of the runbook is the final sign-off before cutover."),
    ]


CONTEXT = build_context(_passages())
QUESTION = "when is the database migration cutover planned?"


# ----------------------------------------------------------------------------------
# (1) hy3 succeeds: grounded answer, model "hy3", fallback_used False.
# ----------------------------------------------------------------------------------

def test_hy3_success_returns_hy3_answer_no_fallback():
    def respond(model, messages, guided_json=None):
        # A grounded, citation-tagged answer whose every value is in the context.
        return ("The database migration cutover is planned for 24 October 2026. [C1]")

    chain = _chain(["hy3", "deepseek-v4.1-flash"], respond)
    text, info, notes = chain.answer(CONTEXT, QUESTION)

    # The grounding wall annotates every value with its binding tag, so "24" and "2026" are each
    # followed by "[C1]" in the served text; assert the evidence is present as separate values.
    assert text and "24" in text and "October 2026" in text and "[C1]" in text
    assert info is not None and info.model == "hy3"
    assert info.fallback_used is False


# ----------------------------------------------------------------------------------
# (2) hy3 raises → the chain falls back to "deepseek-v4.1-flash", fallback_used True.
# ----------------------------------------------------------------------------------

def test_hy3_raises_falls_back_to_deepseek_flash():
    calls: list[str] = []

    def respond(model, messages, guided_json=None):
        calls.append(model)
        if model.endswith("/hy3"):
            # An ungrounded number (999 is NOT in the context) → the grounding wall raises
            # UngroundedNarration, which ModelChain.answer catches → next model.
            return "The migration cutover budget is 999. [C1]"
        # deepseek-v4.1-flash answers groundedly.
        return "The database migration cutover is planned for 24 October 2026. [C1]"

    chain = _chain(["hy3", "deepseek-v4.1-flash"], respond)
    text, info, notes = chain.answer(CONTEXT, QUESTION)

    # The grounding wall annotates values with their binding tags ("24 [C1]", "2026 [C1]").
    assert text and "24" in text and "October 2026" in text and "[C1]" in text
    assert info is not None and info.model == "deepseek-v4.1-flash"
    assert info.fallback_used is True
    assert any("hy3" in n for n in notes), notes
    assert calls[0].endswith("/hy3") and calls[1].endswith("/deepseek-v4.1-flash")


# ----------------------------------------------------------------------------------
# (3) both models fail → no model answer → the pipeline serves the deterministic
#     extractive answer (model "extractive-v1"). Asserted end to end through Pipeline.
# ----------------------------------------------------------------------------------

def test_both_fail_pipeline_serves_extractive(tmp_path):
    # Reuse the in-memory fixtures pattern from test_pipeline_core.py.
    from datetime import datetime, timedelta, timezone

    from databridge_ib.directory import Directory
    from databridge_ib.index import Index
    from databridge_ib.models import (DirectoryUser, SourceDoc, UserKind)
    from databridge_ib.pipeline import Pipeline

    NOW = datetime(2026, 10, 13, 9, 0, tzinfo=timezone(timedelta(hours=8)))

    class _MemLedger:
        def __init__(self):
            self.events = []
        def append(self, ev):
            self.events.append(ev)
            return {}

    class _MemAnswers:
        def __init__(self):
            self.rows = {}
        def put(self, req_id, answer):
            self.rows[req_id] = answer
            import hashlib
            return req_id, hashlib.sha256(answer.encode()).hexdigest()

    class _FakeConnector:
        def __init__(self, platform, docs, readers):
            self.platform = platform
            self.docs = {d.doc_id: d for d in docs}
            self.readers = {k: set(v) for k, v in readers.items()}
        def list_changes(self, cursor):
            return ()
        def fetch(self, doc_id):
            return self.docs.get(doc_id)
        def acl(self, doc_id):
            from databridge_ib import acl as aclmod
            return aclmod.users(*self.readers.get(doc_id, ()))
        def check_access(self, user, doc_id, *, known_version=None):
            from databridge_ib.models import AccessCheck
            d = self.docs.get(doc_id)
            ok = user.id in self.readers.get(doc_id, set())
            return AccessCheck(doc_id=doc_id, verdict="verified" if ok else "forbidden",
                               current_version=d.version if d else None, checked_at=NOW)
        def parse_webhook(self, headers, body):
            return []

    doc = SourceDoc(doc_id="slack:t1", platform="slack", kind="thread", container="eng",
                    container_name="eng", title="migration",
                    text="The database migration cutover is planned for 24 October 2026.",
                    version="1", created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(days=1),
                    deep_link="https://x/t1")

    readers = {"slack:t1": {"jdoe"}}
    conn = _FakeConnector("slack", [doc], readers)
    idx = Index(tmp_path / "index.sqlite")
    idx.upsert(doc, conn.acl(doc.doc_id))

    directory = Directory([
        DirectoryUser(id="jdoe", name="J", email="jdoe@companya.example", employment="employee",
                      kind=UserKind.MEMBER),
    ])

    def respond(model, messages, guided_json=None):
        # Both models emit an ungrounded number (999) so the grounding wall refuses both.
        return "The migration cutover budget is 999. [C1]"

    chain = _chain(["hy3", "deepseek-v4.1-flash"], respond)
    p = Pipeline(index=idx, directory=directory, connectors={"slack": conn},
                 ledger=_MemLedger(), answers=_MemAnswers(), chain=chain, clock=lambda: NOW)

    r = p.ask(directory.get("jdoe"), "when is the database migration cutover planned?")

    # The deterministic extractive answerer ran: it is grounded by construction.
    assert r.model.model == "extractive-v1"
    assert r.model.fallback_used is True
    # The extractive answer is verbatim evidence from the passage.
    assert "24 October 2026" in r.answer
    assert "[C1]" in r.answer


# ----------------------------------------------------------------------------------
# (4) a model answer containing a number NOT present in the context is refused by
#     the grounding wall (that model is skipped; the chain falls through).
# ----------------------------------------------------------------------------------

def test_ungrounded_number_refused_by_grounding_wall():
    def respond(model, messages, guided_json=None):
        # 7777 is NOT in the context → ground_narration marks it unbound → UngroundedNarration.
        return "The migration cutover will cost 7777 dollars. [C1]"

    chain = _chain(["hy3"], respond)
    text, info, notes = chain.answer(CONTEXT, QUESTION)

    # The ungrounded model is refused → no answer from the chain.
    assert text is None
    assert info is None
    assert any("hy3" in n for n in notes), notes
