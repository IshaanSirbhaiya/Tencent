"""R-029 SETX F-09 (E2E run 5, finance, 2026-10-02): a one-time task whose words name no source
compiled RUNNABLE with graph tools only. An agent's scope is its declared sources, so it saw
nothing (20 graph pointers withheld "out of scope", retrieval "declares no sources") and ran 130 s
to "No answer was produced".

Now the compiler never yields a runnable agent that can read no source: it infers the likely
sources from what the catalogue says each connection HOLDS (its mapping's tables and kinds) and
ASKS - the person confirms; nothing inferred is a user reference until they do (R-017 #4).

Two ways in, both on both estates:
* the run's OWN catalogue (`var/e2e5-*/sources`, read-only) with the run's own words (skipped when
  the evidence directory is absent);
* a hermetic catalogue built from each estate's own `estates` fixture schema.
Expectations are derived from the catalogue (which connection holds which table), never written.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from databridge_automation import Catalogue, ConnectionInfo, Governance, ToolRef, compile_english

ROOT = Path(__file__).resolve().parents[3]                          # databridge/
EVIDENCE = {"finance": (ROOT / "var" / "e2e5-finance-20261002T1551",
                        "List every facility whose status is watchlist, with its relationship "
                        "manager."),
            "workspace": (ROOT / "var" / "e2e5-workspace-20261002T1515",
                          "List the customers that have an open invoice, with each one's account "
                          "owner.")}
MI_TESTS = ROOT / "packages" / "mcp_ingest" / "tests"


def _purposes(pack):
    from databridge_adapter.purposes import PurposeDecl, PurposeVocabulary
    p = f"{pack}.f09_test"
    return p, PurposeVocabulary({p: PurposeDecl(id=p, pack=pack, description="synthetic",
                                                subject_scoped=False)}, [pack])


def _golden(tmp_path: Path, pack: str) -> str:
    rel = f"packs/{pack}/evals/f09.jsonl"
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text(json.dumps({"question": "synthetic", "expect": "cited"}) + "\n",
                                encoding="utf-8")
    return rel


def _words(s: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", s.lower()):
        out.add(w[:-1] if w.endswith("s") and not w.endswith("ss") else w)
    return out


def _expected_holders(cat, text: str) -> set[str]:
    """The test's OWN statement of which readable connections hold something named in the words:
    a table or kind it holds, a folder's last segment or an alias, every word of it inside the
    request."""
    want = set()
    tw = " ".join(sorted(_words(text)))
    for c in cat.connections:
        if c.agent_write:
            continue
        for h in (*c.holds, *c.locations, *c.aliases):
            name = re.split(r"[./]", h)[-1]
            words = _words(name.replace("_", " "))
            if words and all(w in _words(text) for w in words):
                want.add(c.id)
    assert tw                                                       # non-vacuous
    return want


def _compile(cat, text, tmp_path, pack, **kw):
    purpose, voc = _purposes(pack)
    cat = cat.with_governance(purpose=purpose, golden_set=_golden(tmp_path, pack)) \
        if hasattr(cat, "with_governance") else cat
    return compile_english(text, cat, purposes=voc, repo_root=tmp_path, run_once=True, **kw)


def _check_asks_then_runs(cat, text, tmp_path, pack):
    holders = _expected_holders(cat, text)
    assert holders, "the estate's catalogue must hold a table the request names"
    draft = _compile(cat, text, tmp_path, pack)
    assert draft.spec is None or draft.open_questions, "a task that reads nothing never compiles"
    q = next((q for q in draft.open_questions if q.id == "sources"), None)
    assert q is not None, [x.id for x in draft.open_questions]
    for cid in holders:
        assert cid in q.text and cid in q.options[1:1 + len(holders)], (cid, q.text, q.options)
    readable = {c.id for c in cat.connections if not c.agent_write}
    assert set(q.options[1:]) <= readable                     # a write-only connection never offered
    # the person confirms the inferred sources -> runnable, reading exactly those
    ok = _compile(cat, text, tmp_path, pack, answers={"sources": "yes"})
    assert ok.spec is not None and not ok.open_questions, [x.text for x in ok.open_questions]
    assert set(ok.spec.sources) == holders
    assert any(d["field"] == f"sources[{c}]" and d["by"] == "catalogue+answer"
               for d in ok.derivations for c in holders)
    # or names one explicitly
    one = sorted(holders)[0]
    named = _compile(cat, text, tmp_path, pack, answers={"sources": one})
    assert named.spec is not None and list(named.spec.sources) == [one]
    # a name that is not a readable connection is asked again, never guessed
    bad = _compile(cat, text, tmp_path, pack, answers={"sources": "no-such-source"})
    assert bad.spec is None and any(x.id == "sources" for x in bad.open_questions)


@pytest.mark.parametrize("pack", ["finance", "workspace"])
def test_f09_the_runs_own_catalogue_and_words_ask_which_source(pack, tmp_path):
    from databridge_automation.catalogue import load_catalogue

    run, text = EVIDENCE[pack]
    if not (run / "sources").is_dir():
        pytest.skip(f"the run-5 evidence {run.name} is not on this machine")
    cat = load_catalogue(run / "sources", pack=pack, ingest_root=run / "ingest",
                         adapter_agents_root=run / "adapter_agents",
                         packs_root=ROOT / "packs")
    _check_asks_then_runs(cat, text, tmp_path, pack)


def _estate_catalogue(pack: str, tmp_path: Path) -> tuple[Catalogue, str]:
    """A catalogue whose database HOLDS the estate fixture's own tables (read from the estate),
    plus a files connection; and a request naming one of its tables by name, no source named."""
    if str(MI_TESTS) not in sys.path:
        sys.path.insert(0, str(MI_TESTS))
    import estates
    from databridge_automation.catalogue import classify_tool

    db = estates.build_estate(pack, tmp_path / "e.sqlite")
    tables = [f"{s}.{t}" for s, t in estates.tables_of(db, pack)]
    assert tables
    pick = sorted(tables, key=lambda t: (len(t.rsplit(".", 1)[-1]), t))[0]
    name = pick.rsplit(".", 1)[-1].replace("_", " ")
    reader = ConnectionInfo(id=f"{pack}_estate_db", title=f"{pack} estate", kind="database",
                            tools=(classify_tool("query", read_only=True),),
                            plan_status="approved", holds=tuple(tables))
    files = ConnectionInfo(id=f"{pack}_docs", title="documents", kind="files",
                           tools=(classify_tool("read_text_file"),), plan_status="approved")
    writer = ConnectionInfo(id=f"{pack}_writer", kind="files", purpose="agent_write",
                            tools=(ToolRef(name="write_file", write=True, basis="declared"),))
    cat = Catalogue(connections=(reader, files, writer), kinds=("person", "document"),
                    subject_kinds=("person",), governance=Governance(pack=pack))
    return cat, f"List every {name} with its identifier."


@pytest.mark.parametrize("pack", ["finance", "workspace"])
def test_f09_a_request_naming_no_source_is_asked_on_each_estate(pack, tmp_path):
    cat, text = _estate_catalogue(pack, tmp_path)
    _check_asks_then_runs(cat, text, tmp_path, pack)


def test_f09_with_no_inference_it_still_asks_and_never_compiles_a_blind_task(tmp_path):
    from databridge_automation.catalogue import classify_tool

    files = ConnectionInfo(id="docs", kind="files", tools=(classify_tool("read_text_file"),),
                           plan_status="approved")
    cat = Catalogue(connections=(files,), kinds=("document",), subject_kinds=(),
                    governance=Governance(pack="workspace"))
    draft = _compile(cat, "Summarise what changed this quarter.", tmp_path, "workspace")
    q = next((q for q in draft.open_questions if q.id == "sources"), None)
    assert draft.spec is None and q is not None and "docs" in q.options
