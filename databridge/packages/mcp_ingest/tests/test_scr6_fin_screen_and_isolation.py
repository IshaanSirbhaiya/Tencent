"""SCR6 — the finance dry run's screen and per-item isolation findings (R-026-SCR6).

FIN-01  one document whose extraction fails (the model returned nothing for msg_00119) must not
        fail the source: the item is recorded `extract_failed` with its reason, the run goes on,
        coverage is reported as partial, retries are bounded, and a model that fails EVERY item
        still stops the run.
FIN-03  a pack that declares no `screen.yaml` is screened by the ENGINE DEFAULT policy, and the run
        says so.
FIN-17  instruction-like text addressed to an automated reader is HELD (review band) by structural
        rules — never extracted, never sent to a model — on BOTH estates' planted injections, with
        no flag on their clean documents.

Two estates throughout: the finance fixtures (`fixtures/packs/finance`) and the Larkspur workspace
(`fixtures/workspace_demo`); the government estate's planted injections are a third check (the
workspace estate plants none — derived below, not assumed). Every expectation is derived from the
fixtures' own ground truth or from the run's own artefacts.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import databridge_mcp_ingest as mi
from databridge_mcp_ingest import runner, screen
from databridge_mcp_ingest.ledger import read_jsonl

from conftest import fs_connection

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
FIN = FIXTURES / "packs" / "finance"
GOV = FIXTURES / "packs" / "government"
WS = FIXTURES / "workspace_demo"
TEXT_SUFFIXES = (".md", ".txt", ".eml", ".csv", ".jsonl", ".json", ".sql")
#: files that DESCRIBE the fixtures (quote the payloads on purpose) rather than being estate data
DESCRIPTIVE = {"README.md", "GROUND_TRUTH.md", "SOURCES.md", "SCHEMA.md", "MANIFEST.json",
               "MANIFEST.md", "INJECTIONS.md", "expected_outcomes.yaml", "_generate.py",
               "generate.py"}


def _quarantined(result) -> list[str]:
    return [f["rule"] for f in result.fired if f["kind"] == "quarantine"]


# --- the planted injections, located from each estate's own ground truth ------------------------
def _finance_plantings() -> list[tuple[str, str, Path | None]]:
    """(injection id, the text the pipeline screens, the file it lives in or None for a row).
    A planting in a file is screened as that whole file; one in a table cell is screened as its
    row, the way `build.build_tables` screens rows (`json.dumps(row)`)."""
    gt = json.loads((FIN / "ground_truth" / "injections.json").read_text(encoding="utf-8"))
    out = []
    for p in gt["plantings"]:
        path = p["locator"].split("#")[0]
        f = next((b / path for b in (FIN / p["source"], FIN / "mailbox")
                  if (b / path).is_file()), None)
        if f is not None:
            out.append((p["injection_id"], f.read_text(encoding="utf-8", errors="replace"), f))
        else:
            row = {"locator": p["locator"], "value": p["rendered_snippet"]}
            out.append((p["injection_id"], json.dumps(row, sort_keys=True, default=str), None))
    assert len(out) == gt["planting_count"] and out
    return out


def _government_plantings() -> list[tuple[str, str, Path | None]]:
    md = (GOV / "correspondence" / "INJECTIONS.md").read_text(encoding="utf-8")
    where = re.findall(r"\*\*Planted at:\*\* `([^`]+)`", md)
    payloads = re.findall(r"```text\n(.*?)\n```", md, re.S)
    assert where and len(where) == len(payloads)
    out = []
    for loc, payload in zip(where, payloads):
        f = next((b / loc for b in (GOV, GOV / "correspondence") if (b / loc).is_file()), None)
        if f is not None:
            out.append((loc, f.read_text(encoding="utf-8", errors="replace"), f))
        else:
            out.append((loc, json.dumps({"note": payload}, sort_keys=True), None))
    return out


def _clean_files(root: Path, planted: set[Path]) -> list[Path]:
    return sorted(f for f in root.rglob("*")
                  if f.is_file() and f.suffix.lower() in TEXT_SUFFIXES
                  and f.name not in DESCRIPTIVE and "ground_truth" not in f.parts
                  and f.resolve() not in planted)


def _planted_paths(plantings) -> set[Path]:
    return {f.resolve() for _, _, f in plantings if f is not None}


# --- FIN-17: detection ------------------------------------------------------------------------
@pytest.mark.parametrize("pack", [[], ["workspace"]], ids=["engine_default", "workspace_pack"])
def test_fin17_every_planted_finance_injection_is_held(pack):
    """All 26 finance plantings — the dry run let every one into the graph — are now held for a
    human, under the engine default AND under a pack's own screen (the hold is engine policy)."""
    cfg = screen.load_screen(packs=pack)
    plantings = _finance_plantings()
    missed = []
    for inj, text, f in plantings:
        r = screen.screen_text(text, cfg)
        if r.decision == "allow" or not _quarantined(r):
            missed.append((inj, str(f) if f else "row"))
        elif r.decision == "review":
            assert r.reverse == screen.QUARANTINE_REVERSE
    assert not missed, missed


def test_fin17_government_planted_injections_are_held_except_the_human_addressed_request():
    """The government estate's plantings: every one ADDRESSED TO A MACHINE is held. The rules are
    structural, so a plain request addressed to a person (a social-engineering email asking a
    caseworker for data) is not a machine instruction and is not claimed — at most one such."""
    cfg = screen.load_screen(packs=[])
    plantings = _government_plantings()
    held = [loc for loc, text, _ in plantings if _quarantined(screen.screen_text(text, cfg))]
    assert len(held) >= len(plantings) - 1, set(p[0] for p in plantings) - set(held)


def test_fin17_the_workspace_estate_plants_no_injection_and_its_documents_are_not_flagged():
    """The Larkspur estate's manifest plants credentials, markings and prices — no injections
    (derived: none of its planted cases is an injection). Every text document in it, screened
    under its own pack, fires no quarantine rule."""
    manifest = (WS / "MANIFEST.md").read_text(encoding="utf-8")
    assert "inject" not in manifest.lower()
    cfg = screen.load_screen(packs=["workspace"])
    files = _clean_files(WS, set())
    assert files
    flagged = [(f.name, _quarantined(screen.screen_text(f.read_text(encoding="utf-8"), cfg)))
               for f in files]
    assert [x for x in flagged if x[1]] == []


@pytest.mark.parametrize("estate", ["finance", "government"])
def test_fin17_false_positive_rate_on_clean_documents_is_zero(estate):
    """Every clean text file of the estate (the planted ones and the descriptive ones excluded):
    no quarantine rule fires. A false positive is a document held from the graph for nothing."""
    root, plantings = ((FIN, _finance_plantings()) if estate == "finance"
                       else (GOV, _government_plantings()))
    planted = _planted_paths(plantings)
    # a wildcard locator (payments events/*.jsonl) names its files by content, not by path
    for _, text, f in plantings:
        if f is None:
            planted |= {p.resolve() for p in _clean_files(root, set())
                        if _snippet_of(text) and _snippet_of(text) in p.read_text(
                            encoding="utf-8", errors="replace")}
    cfg = screen.load_screen(packs=[])
    files = _clean_files(root, planted)
    assert len(files) > 50                                         # non-vacuous
    fp = [str(f.relative_to(root)) for f in files
          if _quarantined(screen.screen_text(f.read_text(encoding="utf-8", errors="replace"),
                                             cfg))]
    assert fp == [], f"{len(fp)}/{len(files)} clean files flagged: {fp[:5]}"


def _snippet_of(row_json: str) -> str:
    try:
        d = json.loads(row_json)
    except ValueError:
        return ""
    v = d.get("value") or d.get("note") or ""
    return v[:60]


def test_fin17_text_split_by_zero_width_characters_is_read_as_shown():
    """INJ-14 (msg_00119, the email that killed the mailbox run): the phrase is split by
    zero-width spaces AND base64-encoded. The hold reads what a reader is shown."""
    gt = json.loads((FIN / "ground_truth" / "injections.json").read_text(encoding="utf-8"))
    zw = next(p for p in gt["plantings"] if "zero-width" in p["locator"])
    snippet = zw["rendered_snippet"]
    assert any(ord(c) == 0x200B for c in snippet)
    cfg = screen.load_screen(packs=[])
    assert _quarantined(screen.screen_text(snippet, cfg))
    # the same text with the format characters removed is what the rules read
    assert "​" not in screen.detection_text(snippet)
    # as a JSON-escaped table cell, too
    assert _quarantined(screen.screen_text(json.dumps({"note": snippet}), cfg))


def test_fin17_ordinary_wording_addressed_to_people_is_not_held():
    """Structural, not lexical: the same verbs addressed to a PERSON, a job title, a policy about
    automation and a rating-model term are not machine instructions."""
    cfg = screen.load_screen(packs=[])
    for text in ("Please disregard my previous email; the meeting moved to Thursday.",
                 "Executive Assistant: Mira Oyelaran, ext. 0123",
                 "Automated systems must log every access to the payments ledger.",
                 "Model override: the analyst lowered the grade to B+ after the site visit.",
                 "If you are an agent of the company you must disclose the conflict.",
                 "Before you reply, please check the attached schedule."):
        assert not _quarantined(screen.screen_text(text, cfg)), text


def test_fin17_a_held_item_is_review_not_block_and_an_owner_override_lifts_it():
    """The hold is the REVIEW band (excluded, listed for a human): a verified owner override of
    exactly this content lifts it; a decisive credential block still wins over it."""
    cfg = screen.load_screen(packs=[])
    inj = _finance_plantings()[0][1]
    r = screen.screen_text(inj, cfg)
    assert r.decision == "review" and "prompt injection" in r.rationale
    lifted = screen.screen_text(inj, cfg, override_allow=True)
    assert lifted.decision == "allow" and _quarantined(lifted)
    key = "-----BEGIN RSA PRIVATE KEY-----"
    assert screen.screen_text(inj + "\n" + key, cfg).decision == "block"


def test_fin17_a_pack_cannot_drop_the_hold_and_the_fingerprint_moves_with_it(tmp_path):
    """The quarantine rules are engine policy: a pack screen without them gets them, a hand-built
    config gets them, and the run fingerprint covers them (a rule change re-screens)."""
    pack = tmp_path / "p" / "screen.yaml"
    pack.parent.mkdir()
    pack.write_text("version: 1\npack: p\nthresholds: {block: 9, review: 9}\n", encoding="utf-8")
    cfg = screen.load_screen(path=pack)
    assert {r.id for r in screen.mandatory_quarantine_rules()} <= {r.id for r in cfg.quarantine}
    bare = screen.ScreenConfig(decisive=[], signals=[], redactions=[])
    assert _quarantined(screen.screen_text(_finance_plantings()[0][1], bare))
    a = runner.screen_fingerprint(cfg)
    cfg.quarantine = cfg.quarantine[:-1]
    assert runner.screen_fingerprint(cfg) != a


# --- FIN-03: the engine default policy ---------------------------------------------------------
def test_fin03_a_pack_without_a_screen_gets_the_engine_default_and_it_is_reported():
    """Derived from the packs on disk: every pack that ships no screen.yaml resolves to the
    engine default (credential gate + injection hold + PII masks); one that ships one resolves
    to its own. Both estates' packs are covered (finance declares none, workspace declares one)."""
    root = screen.paths.packs_dir()
    packs = sorted(p.name for p in root.iterdir() if (p / "ontology.yaml").is_file())
    without = [p for p in packs if screen.pack_screen_file(p) is None]
    with_ = [p for p in packs if screen.pack_screen_file(p) is not None]
    assert "finance" in without and "workspace" in with_                  # the two estates
    for p in without:
        cfg = screen.load_screen(packs=[p])
        pol = screen.screen_policy(cfg)
        assert pol["policy"] == "engine_default" and pol["pack"] is None, p
        assert pol["credential_rules"] and pol["quarantine_rules"] and pol["redactions"]
        assert any(screen.DEFAULT_POLICY_NOTE in n for n in cfg.notes)
        # conservative: a credential blocks, an e-mail address is masked
        assert screen.screen_text("api_key = sk-Zq9f2s3cr3tVx7abcdefgh", cfg).decision == "block"
        assert "[EMAIL]" in screen.screen_text("write to a.b@c.example", cfg).masked_text
    for p in with_:
        assert screen.screen_policy(screen.load_screen(packs=[p]))["policy"] == "pack"


# --- FIN-01 + FIN-17 through the real run -------------------------------------------------------
@pytest.fixture
def store(walnut_env):
    from databridge_graph.store import GraphStore

    path = walnut_env["WALNUT_GRAPH_DB_PATH"]
    path.parent.mkdir(parents=True, exist_ok=True)
    return GraphStore(str(path))


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("WALNUT_MCP_POOL", "0")
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(tmp_path / "facts"))
    monkeypatch.setenv("WALNUT_PASSAGES_DIR", str(tmp_path / "passages"))


def _two_estate_corpus(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    """Larkspur's text documents + finance credit memos + finance emails, including the email
    that killed the dry run (msg_00119) and one memo with a planted injection."""
    root = tmp_path / "estate"
    picked: dict[str, Path] = {}
    for f in _clean_files(WS / "files", set()):
        picked[f"workspace/{f.relative_to(WS / 'files').as_posix()}"] = f
    memos = sorted((FIN / "credit_memos").rglob("*.md"))
    for f in memos[:4]:
        picked[f"finance/memos/{f.name}"] = f
    gt = json.loads((FIN / "ground_truth" / "injections.json").read_text(encoding="utf-8"))
    planted = {p["locator"].split("#")[0] for p in gt["plantings"]}
    memo_inj = next(f for f in memos if any(f.as_posix().endswith(x) for x in planted))
    picked[f"finance/memos/{memo_inj.name}"] = memo_inj
    msgs = sorted((FIN / "mailbox" / "messages").glob("*.eml"))
    killer = next(f for f in msgs if any(f.as_posix().endswith(x) for x in planted
                                         if "msg_00119" in x))
    for f in [m for m in msgs if not any(m.as_posix().endswith(x) for x in planted)][:3] \
            + [killer]:
        picked[f"finance/mail/{f.name}"] = f
    for rel, src in picked.items():
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    return root, picked


def _failing_handler(calls: list, fails_on, failed_calls: list | None = None):
    """The FakeBackend handler: the model returns NOTHING (an empty string — the dry run's
    `input_value=''`) for any text `fails_on` selects; otherwise grounded entities. Every call
    goes to `calls`; the failing ones also to `failed_calls`."""
    from conftest import extraction_handler

    ok = extraction_handler(calls)

    def handler(*, model, messages, guided_json) -> str:
        # the document is the last message on the first attempt; on a retry the harness appends
        # its correction request after it, and the dry run's model returned '' every time
        doc = next((m["content"] for m in reversed(messages)
                    if str(m.get("content", "")).startswith("DOCUMENT TEXT:\n")), None)
        if doc is not None and fails_on(doc[len("DOCUMENT TEXT:\n"):]):
            calls.append(messages)
            if failed_calls is not None:
                failed_calls.append(messages)
            return ""
        return ok(model=model, messages=messages, guided_json=guided_json)
    return handler


def _harness(handler):
    from databridge_harness import FakeBackend, Harness

    return Harness(FakeBackend(handler), profile="cloud")


def _run(sid, root, store, harness, **kw):
    mi.register_connection(fs_connection(sid, root, blocked=[]))
    mi.propose_plan(sid)
    return mi.run_ingest(sid, store=store, harness=harness, concurrency=1, cache=False, **kw)


def test_fin01_one_document_the_model_cannot_read_does_not_fail_the_source(
        walnut_env, tmp_path, common_vocab, store):
    root, picked = _two_estate_corpus(tmp_path)
    # the victim: a CLEAN finance memo the screen allows (the killer email is now held by FIN-17,
    # so the model failure is reproduced on a document that reaches the model)
    cfg = screen.load_screen()
    victim = next(rel for rel, f in sorted(picked.items()) if rel.startswith("finance/memos/")
                  and screen.screen_text(f.read_text(encoding="utf-8"), cfg).decision == "allow")
    marker = picked[victim].read_text(encoding="utf-8").strip().splitlines()[0][:40]
    calls, bad = [], []
    report = _run("estate", root, store,
                  _harness(_failing_handler(calls, lambda t: marker in t, bad)))
    run_dir = Path(report.run_dir)
    assert report.status == "ok", report.message                  # the source did not fail
    failed = [r for r in read_jsonl(run_dir / "build_skipped.jsonl")
              if r.get("reason") == "extract_failed"]
    assert len(failed) == 1 and failed[0]["uri"].endswith(Path(victim).name)
    assert "StructuredOutputError" in failed[0]["detail"] and failed[0].get("reverse")
    # every OTHER allowed item was built; coverage is reported as partial, honestly
    allowed = {r["item_id"] for r in read_jsonl(run_dir / "governance.jsonl")
               if r["decision"] == "allow"}
    built = {r["item_id"] for r in read_jsonl(run_dir / "build.jsonl")}
    assert built == allowed - {failed[0]["item_id"]} and built
    assert report.counts["extract_failed"] == 1
    assert "PARTIAL" in report.message and "1 of" in report.message
    # retries are bounded: the harness's own retry ladder (1 + max_retries attempts) per model
    # job of the item, never an item-level loop on top of it
    import inspect

    from databridge_harness import Harness

    ladder = 1 + inspect.signature(Harness.complete_structured).parameters["max_retries"].default
    assert failed[0]["model_jobs"] >= 1
    assert 1 <= len(bad) <= ladder * failed[0]["model_jobs"]
    # the item's state is NOT advanced: the next sync tries it again (and again isolates it)
    state = runner.load_state("estate")
    assert failed[0]["item_id"] not in state
    again = mi.run_ingest("estate", store=store,
                          harness=_harness(_failing_handler([], lambda t: marker in t)),
                          concurrency=1, cache=False)
    assert again.status == "ok" and again.counts["extract_failed"] == 1
    assert again.counts["built"] == 0                              # the rest were unchanged


def test_fin17_the_killer_email_and_the_planted_memo_are_held_and_never_reach_the_model(
        walnut_env, tmp_path, common_vocab, store):
    root, picked = _two_estate_corpus(tmp_path)
    gt = json.loads((FIN / "ground_truth" / "injections.json").read_text(encoding="utf-8"))
    planted = {p["locator"].split("#")[0] for p in gt["plantings"]}
    is_planted = {rel: any(f.as_posix().endswith(x) for x in planted)
                  for rel, f in picked.items()}
    assert sum(is_planted.values()) == 2
    calls: list = []
    report = _run("estate2", root, store, _harness(_failing_handler(calls, lambda t: False)))
    assert report.status == "ok", report.message
    run_dir = Path(report.run_dir)
    gov = {r["uri"]: r for r in read_jsonl(run_dir / "governance.jsonl")}
    for rel, planted_here in is_planted.items():
        rec = next(r for u, r in gov.items() if u.endswith(Path(rel).name))
        q = [f["rule"] for f in rec["fired"] if f["kind"] == "quarantine"]
        if planted_here:
            assert rec["decision"] == "review" and q, rel
        else:
            assert not q, (rel, q)                       # no clean document of either estate
    # nothing held was sent to a model: no call carried a held item's text
    def lines(rel):
        return {ln.strip() for ln in picked[rel].read_text(encoding="utf-8", errors="replace")
                .splitlines() if len(ln.strip()) > 20}

    others = set().union(*(lines(r) for r, p in is_planted.items() if not p))
    sent = "\n".join(str(m.get("content", "")) for msgs in calls for m in msgs)
    assert calls                                          # the clean documents did reach it
    for rel in [r for r, p in is_planted.items() if p]:
        own = lines(rel) - others                         # lines only the held item has
        assert own and not [ln for ln in own if ln in sent], rel
    # the run reports the policy it screened under (FIN-03)
    assert report.as_dict()["screen_policy"]["policy"] == "engine_default"
    assert any(screen.DEFAULT_POLICY_NOTE in n for n in report.notes)


def test_fin01_a_model_that_fails_every_item_stops_the_run_after_a_bounded_number(
        walnut_env, tmp_path, common_vocab, store, monkeypatch):
    """Isolation is for ONE bad document, not for a dead model: after N consecutive items fail,
    the run fails with that said, rather than burning the retry ladder on every item."""
    monkeypatch.setenv("WALNUT_EXTRACT_MAX_CONSECUTIVE_FAILURES", "2")
    root, _ = _two_estate_corpus(tmp_path)
    calls: list = []
    with pytest.raises(Exception) as exc:
        _run("estate3", root, store, _harness(_failing_handler(calls, lambda t: True)))
    assert "consecutive" in str(exc.value)
    run_dir = max(d for d in (Path(walnut_env["WALNUT_INGEST_DIR"]) / "estate3").iterdir()
                  if d.is_dir())
    allowed = [r for r in read_jsonl(run_dir / "governance.jsonl") if r["decision"] == "allow"]
    failed = [r for r in read_jsonl(run_dir / "build_skipped.jsonl")
              if r.get("reason") == "extract_failed"]
    assert len(failed) == 2 < len(allowed)
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed" and "consecutive" in report["message"]
