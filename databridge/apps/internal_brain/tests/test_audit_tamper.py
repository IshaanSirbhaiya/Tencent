"""A5 tamper suite (P5): edit, suffix deletion, whole-directory rollback (detected ONLY via the external
head), anchor mismatch, key-holder rewrite, and the publish/verify plumbing."""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import httpx
import pytest
from databridge_ib.audit.anchor import (AnchorPublishFailed, FileSink, SlackWebhookSink,
                                        publish_head, verify)
from databridge_ib.audit.ledger import LOG_ID, AccessLedger, LedgerUnavailable
from databridge_ib.models import AuditQueryEvent

from test_audit_support import KEY, NOW, make_cfg

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "tamper_demo.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("ib_tamper_demo", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tamper = _load_script()


def _ev(i: int) -> AuditQueryEvent:
    return AuditQueryEvent(sub="sara.sec", filter={"op": "t", "i": i}, result_count=i, event_time=NOW)


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    """A ledger with 10 records, a publication at seq 10 (anchor event = seq 11), then 3 more."""
    root = tmp_path_factory.mktemp("tamper-src")
    cfg = make_cfg(root)
    sink = FileSink(root / "external-anchor-repo" / "heads.jsonl")
    ledger = AccessLedger(cfg, external=sink)
    for i in range(10):
        ledger.append(_ev(i))
    published = publish_head(ledger, sink)
    for i in range(3):
        ledger.append(_ev(100 + i))
    return root, cfg, sink, published


def _copy(source, tmp_path):
    root, cfg, sink, _ = source
    shutil.copytree(cfg.var_root, tmp_path / "var")
    ext = tmp_path / "ext" / "heads.jsonl"
    ext.parent.mkdir()
    shutil.copy2(sink.path, ext)
    c2 = make_cfg(tmp_path)
    return c2, FileSink(ext), AccessLedger(c2, external=FileSink(ext))


def test_published_and_recorded(source):
    root, cfg, sink, published = source
    head = sink.latest(LOG_ID)
    assert head["seq"] == published.seq == 10 and head["head"] == published.head
    evs = list(AccessLedger(cfg).events())
    anchor = [e for e in evs if e.kind == "anchor.published"]
    assert len(anchor) == 1 and anchor[0].seq == published.seq + 1
    rep = AccessLedger(cfg, external=sink).verify()
    assert rep.ok and rep.external_head_match is True


def test_edit_detected(source, tmp_path):
    cfg, _, ledger = _copy(source, tmp_path)
    lines = cfg.ledger_path.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[4])
    rec["ib_event"]["result_count"] = 999
    lines[4] = json.dumps(rec, sort_keys=True)
    cfg.ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rep = ledger.verify()
    assert not rep.ok and not rep.internal_ok
    assert any("record 5" in p for p in rep.problems)
    with pytest.raises(LedgerUnavailable):
        ledger.append(_ev(0))


def test_suffix_deletion_detected(source, tmp_path):
    cfg, _, ledger = _copy(source, tmp_path)
    lines = cfg.ledger_path.read_text(encoding="utf-8").splitlines()
    cfg.ledger_path.write_text("\n".join(lines[:-2]) + "\n", encoding="utf-8")
    rep = ledger.verify()
    assert not rep.ok and not rep.internal_ok
    assert any("rolled back" in p for p in rep.problems)


def test_directory_rollback_detected_only_via_external_head(source, tmp_path):
    root, _, _, published = source
    cfg, ext, ledger = _copy(source, tmp_path)
    tamper.restore_prefix(cfg.var_root, published.seq - 4)       # a backup from before publication
    internal_only = AccessLedger(cfg).verify()
    assert internal_only.ok and internal_only.internal_ok        # the chain alone cannot see it
    rep = ledger.verify()
    assert not rep.ok and rep.internal_ok and rep.external_head_match is False
    assert any("rollback detected vs external head" in p for p in rep.problems)


def test_rollback_after_last_publication_is_the_stated_window(source, tmp_path):
    root, _, _, published = source
    cfg, _, ledger = _copy(source, tmp_path)
    tamper.restore_prefix(cfg.var_root, published.seq + 1)
    rep = ledger.verify()
    assert rep.ok and rep.external_head_match is True             # honest: not detectable yet


def test_anchor_mismatch_key_holder_rewrite(source, tmp_path):
    cfg, _, ledger = _copy(source, tmp_path)

    def mut(recs):
        recs[2]["ib_event"]["sub"] = "someone-else"
        return recs
    tamper.reseal(cfg.var_root, KEY.encode(), mut)
    assert AccessLedger(cfg).verify().ok                           # re-sealed: internally fine
    rep = ledger.verify()
    assert not rep.ok and rep.internal_ok and rep.external_head_match is False
    assert any("history rewritten" in p for p in rep.problems)


def test_external_head_tampered(source, tmp_path):
    cfg, ext, ledger = _copy(source, tmp_path)
    head = ext.latest(LOG_ID)
    ext.path.write_text(json.dumps({**head, "head": "0" * 64}) + "\n", encoding="utf-8")
    rep = ledger.verify()
    assert not rep.ok and rep.external_head_match is False


def test_internal_anchor_removed(source, tmp_path):
    cfg, _, ledger = _copy(source, tmp_path)
    cfg.anchors_path.unlink()
    rep = ledger.verify()
    assert not rep.ok and not rep.internal_ok


def test_external_head_missing(source, tmp_path):
    cfg, ext, ledger = _copy(source, tmp_path)
    ext.path.unlink()
    rep = ledger.verify()
    assert not rep.ok and rep.external_head_match is False
    assert not verify(AccessLedger(cfg), ext).ok                   # same via anchor.verify


def test_publish_refuses_broken_ledger(source, tmp_path):
    cfg, ext, ledger = _copy(source, tmp_path)
    lines = cfg.ledger_path.read_text(encoding="utf-8").splitlines()
    cfg.ledger_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    before = ext.path.read_text(encoding="utf-8")
    with pytest.raises(LedgerUnavailable):
        publish_head(ledger, ext)
    assert ext.path.read_text(encoding="utf-8") == before


def test_slack_sink_posts_and_failure_records_nothing(tmp_path):
    cfg = make_cfg(tmp_path)
    ledger = AccessLedger(cfg)
    ledger.append(_ev(1))
    posted = []

    def ok(req: httpx.Request) -> httpx.Response:
        posted.append(json.loads(req.content))
        return httpx.Response(200, text="ok")

    mirror = FileSink(tmp_path / "mirror.jsonl")
    sink = SlackWebhookSink("https://hooks.example.invalid/x", mirror=mirror,
                            client=httpx.Client(transport=httpx.MockTransport(ok)))
    ev = publish_head(ledger, sink)
    assert posted and posted[0]["ib_anchor"]["head"] == ev.head
    assert mirror.latest(LOG_ID)["seq"] == ev.seq
    assert verify(ledger, sink).external_head_match is True

    seq_before = ledger.head()[0]
    down = SlackWebhookSink("https://hooks.example.invalid/x", client=httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    with pytest.raises(AnchorPublishFailed):
        publish_head(ledger, down)
    assert ledger.head()[0] == seq_before                          # nothing recorded


def test_tamper_demo_script_scenarios(tmp_path):
    var, ext = tamper.build_demo(tmp_path / "src")
    results = tamper.run_scenarios(var, ext, tamper.DEMO_KEY, work=tmp_path / "work")
    names = {r["scenario"] for r in results}
    assert {"baseline", "edit-record", "suffix-delete", "directory-rollback",
            "remove-internal-anchors", "external-head-missing", "key-holder-rewrite"} <= names
    for r in results:
        assert r["detected"] == r["expect_detected"], r
    by = {r["scenario"]: r for r in results}
    for only_external in ("directory-rollback", "key-holder-rewrite"):
        assert by[only_external]["internal_ok"] and by[only_external]["external_head_match"] is False


# --- Codex security review r1, track 2 ---------------------------------------------------------
def test_stale_external_head_fails_despite_later_publication(source, tmp_path):
    # #5: the log records a publication at seq S, the sink's latest is older than S
    cfg, ext, ledger = _copy(source, tmp_path)
    older = ext.path.read_text(encoding="utf-8")
    second = publish_head(ledger, ext)
    assert verify(ledger, ext).ok
    ext.path.write_text(older, encoding="utf-8")                      # the newer head withheld
    assert ext.latest(LOG_ID)["seq"] < second.seq
    for rep in (ledger.verify(), verify(ledger, ext)):
        assert not rep.ok and rep.internal_ok and rep.external_head_match is False
        assert any("external head stale" in p for p in rep.problems)


class _RaisingSink:
    name = "raising"

    def latest(self, log_id):
        raise OSError("sink unreachable")


def test_sink_exception_fails_closed_even_without_a_publication(tmp_path):
    # #6: no anchor.published in the log, sink raises -> still not ok; verify never raises
    cfg = make_cfg(tmp_path)
    ledger = AccessLedger(cfg, external=_RaisingSink())
    ledger.append(_ev(1))
    for rep in (ledger.verify(), verify(AccessLedger(cfg), _RaisingSink())):
        assert not rep.ok and rep.internal_ok and rep.external_head_match is False
        assert any("could not be read" in p for p in rep.problems)


@pytest.mark.parametrize("mutate", [
    lambda h: {**h, "log_id": "another-log"},
    lambda h: {**h, "seq": str(h["seq"])},
    lambda h: {**h, "seq": True},
    lambda h: {**h, "seq": -1},
    lambda h: {**h, "head": "not-a-digest"},
    lambda h: {k: v for k, v in h.items() if k != "head"},
    lambda h: ["not", "a", "record"],
])
def test_malformed_or_foreign_external_head_fails(source, tmp_path, mutate):
    # #20: strict shape and log identity; malformed input is a failed report, never an exception
    cfg, ext, ledger = _copy(source, tmp_path)
    bad = mutate(ext.latest(LOG_ID))

    class Reader:
        name = "reader"

        def latest(self, log_id):
            return bad
    for rep in (AccessLedger(cfg, external=Reader()).verify(), verify(ledger, Reader())):
        assert not rep.ok and rep.external_head_match is False and rep.problems


def test_corrupt_external_file_fails_closed(source, tmp_path):
    # #20: a garbled newest line must not silently fall back to an older head
    cfg, ext, ledger = _copy(source, tmp_path)
    with open(ext.path, "a", encoding="utf-8") as fh:
        fh.write('{"log_id": "ib-access", "seq": \n')
    for rep in (ledger.verify(), verify(ledger, ext)):
        assert not rep.ok and rep.external_head_match is False
        assert any("could not be read" in p for p in rep.problems)
