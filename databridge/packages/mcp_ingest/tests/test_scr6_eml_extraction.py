"""SCR6 follow-up — `.eml` is extracted DECODED (stdlib `email`, policy.default), not as raw MIME.

Read raw, the model saw quoted-printable soft breaks and base64 blocks (msg_00119's body was one
base64 block; the model returned nothing). Now the extracted text is what a mail client shows:
headers, the text/plain body (HTML only when there is no plain part), text attachments. The
capture keeps the ORIGINAL bytes; pointers index the decoded extracted text (`text_sha256`,
extractor `email`) and must resolve there. Two mailboxes: finance and government. Expectations
are derived with the stdlib parser, independently of the code under test.
"""
from __future__ import annotations

import email
import re
import shutil
from email import policy
from pathlib import Path

import pytest

import databridge_mcp_ingest as mi
from databridge_mcp_ingest import capture, screen
from databridge_mcp_ingest.extract import extract_text, extractor_for
from databridge_mcp_ingest.ledger import read_jsonl

from conftest import fs_connection

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "packs"
MAILBOXES = {"finance": FIXTURES / "finance" / "mailbox" / "messages",
             "government": FIXTURES / "government" / "correspondence"}


def _emls(name: str) -> list[Path]:
    files = sorted(MAILBOXES[name].rglob("*.eml"))
    assert len(files) > 50
    return files


def _shown_body(data: bytes) -> str:
    """The preferred body as the stdlib renders it — the reference the extraction must match."""
    msg = email.message_from_bytes(data, policy=policy.default)
    part = msg.get_body(preferencelist=("plain", "html"))
    return part.get_content() if part is not None else ""


def _norm(s: str) -> str:
    return " ".join(s.split())


@pytest.mark.parametrize("mailbox", sorted(MAILBOXES))
def test_every_email_is_extracted_as_its_decoded_body(mailbox):
    encoded = 0
    for f in _emls(mailbox):
        data = f.read_bytes()
        assert extractor_for(f.name) == "email"
        got = extract_text(data, name=f.name)
        assert got.extractor == "email"
        body = _shown_body(data)
        if "html" not in (email.message_from_bytes(data, policy=policy.default)
                          .get_body(preferencelist=("plain", "html")) or {}).get(
                "content-type", "text/plain"):
            assert _norm(body) in _norm(got.text), f.name
        # no transport encoding survives into the text a model is shown
        assert "Content-Transfer-Encoding" not in got.text, f.name
        if re.search(rb"(?im)^content-transfer-encoding:\s*(base64|quoted-printable)", data):
            encoded += 1
            assert not re.search(r"=\r?\n", got.text), f.name             # QP soft breaks
            assert not re.search(r"(?m)^[A-Za-z0-9+/]{60,}={0,2}$", got.text), f.name  # base64
    if mailbox == "finance":
        assert encoded > 0                                                # non-vacuous


def test_a_base64_text_attachment_is_rendered_decoded():
    """Derived: the finance mailbox's base64 text attachments appear decoded in the text."""
    seen = 0
    for f in _emls("finance"):
        data = f.read_bytes()
        msg = email.message_from_bytes(data, policy=policy.default)
        for part in msg.iter_attachments():
            if part.get_content_maintype() == "text" and part["content-transfer-encoding"] \
                    and "base64" in part["content-transfer-encoding"]:
                text = extract_text(data, name=f.name).text
                first = next(ln for ln in part.get_content().splitlines() if ln.strip())
                assert first.strip() in text and f"--- attachment: {part.get_filename()}" in text
                seen += 1
    assert seen


# --- through the real run: pointers into the decoded text resolve ------------------------------
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


def _body_sentence_handler(calls: list):
    """The fake model 'finds' the first long body line of whatever it is sent (an organisation,
    offsets computed in the text it was SENT) — so a pointer lands inside the decoded body."""
    import json

    def handler(*, model, messages, guided_json) -> str:
        calls.append(messages)
        doc = next((m["content"] for m in reversed(messages)
                    if str(m.get("content", "")).startswith("DOCUMENT TEXT:\n")), None)
        if doc is None:
            return json.dumps({})
        text = doc[len("DOCUMENT TEXT:\n"):]
        body = text.split("\n\n", 1)[-1]
        line = next((ln.strip() for ln in body.splitlines() if len(ln.strip()) > 60), None)
        if not line:
            return json.dumps({"entities": [], "relations": []})
        i = text.find(line)
        return json.dumps({"entities": [{"name": line, "kind": "organisation", "char_start": i,
                                         "char_end": i + len(line)}], "relations": []})
    return handler


def test_pointers_into_decoded_email_text_resolve_and_the_capture_keeps_the_bytes(
        walnut_env, tmp_path, common_vocab, store):
    from databridge_harness import FakeBackend, Harness

    root = tmp_path / "mail"
    picked: list[Path] = []
    for name in sorted(MAILBOXES):
        files = _emls(name)
        qp = [f for f in files if b"quoted-printable" in f.read_bytes()][:3]
        picked += qp or files[:3]
    # the government mailbox's hidden-HTML-comment planting: its plain body is clean, so only
    # the raw MIME source shows the injection
    hidden = next(f for f in _emls("government") if b"<!--" in f.read_bytes()
                  and screen.screen_text(f.read_text(encoding="utf-8"),
                                         screen.load_screen()).decision == "review")
    picked.append(hidden)
    for f in picked:
        dst = root / f.parent.name / f.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, dst)
    calls: list = []
    mi.register_connection(fs_connection("mail", root, blocked=[]))
    mi.propose_plan("mail")
    report = mi.run_ingest("mail", store=store,
                           harness=Harness(FakeBackend(_body_sentence_handler(calls)),
                                           profile="cloud"), concurrency=1, cache=False)
    assert report.status == "ok" and report.verify["ok"], report.message
    run_dir = Path(report.run_dir)
    captured = {r["item_id"]: r for r in read_jsonl(run_dir / "captured.jsonl")}
    extracted = {r["item_id"]: r for r in read_jsonl(run_dir / "extracted.jsonl")}
    assert {r["extractor"] for r in extracted.values()} == {"email"}
    for iid, r in extracted.items():
        raw = capture.get_bytes(captured[iid]["sha256"])                 # ORIGINAL bytes kept
        assert capture.sha256_bytes(raw) == captured[iid]["sha256"]
        assert capture.get_text(r["sha256"], expect_text_digest=r["text_sha256"]) != \
            raw.decode("utf-8", errors="replace")
    # every entity pointer indexes the DECODED text: its slice is the grounded value, and for a
    # quoted-printable body that same value is NOT a verbatim run of the raw MIME bytes
    ptrs = [p for p in read_jsonl(run_dir / "pointers.jsonl")
            if p.get("kind") == "doc_span" and p.get("label")]
    assert ptrs
    decoded_only = 0
    for p in ptrs:
        rec = extracted[p["item_id"]]
        text = capture.get_text(rec["sha256"], expect_text_digest=rec["text_sha256"])
        value = text[p["start"]:p["end"]]
        assert value == p["label"], p                              # the grounded value, exactly
        if value not in capture.get_bytes(rec["sha256"]).decode("utf-8", errors="replace"):
            decoded_only += 1
    assert decoded_only, "no pointer proved it indexes decoded text rather than raw bytes"
    # the hidden-comment email is held although its extracted body is clean
    gov = {r["uri"]: r for r in read_jsonl(run_dir / "governance.jsonl")}
    held = next(r for u, r in gov.items() if u.endswith(hidden.name))
    assert held["decision"] == "review"
    assert any(f["kind"] == "quarantine" for f in held["fired"])
