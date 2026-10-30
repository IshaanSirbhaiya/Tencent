"""``POST /sim/upload-file``: real files (Word, Excel, PowerPoint, PDF, CSV, Markdown, plain text and
WhatsApp chat exports) reach the Internal Brain the way an employee's Drive upload does.

The REAL mock platforms (in-process), the REAL Drive connector and the real app routes. The route
puts the file in Drive; Drive's signed ``content.changed`` webhook fires; the connector fetches the
file and extracts its text with Walnut's convert readers; the ingest path masks and indexes it. The
files are built here with python-docx / openpyxl / python-pptx (a PDF is written by hand). Personas
and folders are DERIVED from the generated world (who holds which Drive role), not written down."""
from __future__ import annotations

import base64
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from databridge_ib import auth as authmod  # noqa: E402
from databridge_ib import extract_files as X  # noqa: E402
from databridge_ib.app import Services, create_app  # noqa: E402
from databridge_ib.config import IbConfig  # noqa: E402
from databridge_ib.sim import _RateLimiter, make_sim_router  # noqa: E402
from databridge_ib.uploads import (MAX_BODY_BYTES, demo_features_on, display_name,  # noqa: E402
                                   make_uploads_router, reset_uploads)

from e2e_world import ADMIN, E2E, WORLD, MemAnswers, _Client, load_world  # noqa: E402
from test_extract_files import pdf  # noqa: E402

docx = pytest.importorskip("docx")
openpyxl = pytest.importorskip("openpyxl")
pptx = pytest.importorskip("pptx")

W = load_world()
USERS = {u["id"]: u for u in W["directory"]["users"]}
FOLDERS = {f["id"]: f for f in W["drive"]["folders"]}
DRIVES = {d["id"]: d for d in W["drive"]["drives"]}
WRITE = {"writer", "fileOrganizer", "organizer", "owner"}
ADMIN_USER = next(u for u, x in USERS.items() if "admin_it" in x.get("roles", []))
REFUSAL = {"detail": "not found"}


# ---------------------------------------------------------------------------------------------
# personas, derived from the world: who can write / read which folder (folder grants, then the
# shared drive's members unless the folder is limited-access)
# ---------------------------------------------------------------------------------------------
def _groups(uid: str) -> set[str]:
    from databridge_ib.directory import Directory
    return set(Directory.from_world(W).get(uid).groups)


def _grants(fid: str) -> list[dict]:
    f = FOLDERS[fid]
    out = list(f.get("permissions", []))
    if not f.get("inherited_permissions_disabled"):
        m = DRIVES[f["drive"]].get("members") or {}
        out += [{"type": "user", "principal": u, "role": m.get("role", "reader")}
                for u in m.get("users", [])]
        out += [{"type": "group", "principal": g, "role": m.get("role", "reader")}
                for g in m.get("groups", [])]
    return out


def _holds(uid: str, fid: str, roles: set[str] | None) -> bool:
    gs, dom = _groups(uid), USERS[uid]["email"].rsplit("@", 1)[-1]
    for g in _grants(fid):
        if roles is not None and g.get("role") not in roles:
            continue
        if (g["type"] == "user" and g["principal"] == uid) or \
                (g["type"] == "group" and g["principal"] in gs) or \
                (g["type"] == "domain" and g["principal"] == dom):
            return True
    return False


def _eligible(uid: str) -> bool:
    u = USERS[uid]
    return u.get("active", True) and "admin_it" not in u.get("roles", []) and \
        bool(u.get("platform_ids", {}).get("drive")) and bool(set(u.get("roles", [])))


def _pick():
    for fid in sorted(FOLDERS):
        writers = sorted(u for u in USERS if _eligible(u) and _holds(u, fid, WRITE))
        readers = sorted(u for u in USERS if _eligible(u) and _holds(u, fid, None)
                         and not _holds(u, fid, WRITE))
        outsiders = sorted(u for u in USERS if _eligible(u) and not _holds(u, fid, None))
        if writers and readers and outsiders:
            return fid, writers[0], readers[0], outsiders[0]
    pytest.skip("no folder with a writer, a read-only person and an outsider in this world")


FOLDER, WRITER, READER, OUTSIDER = _pick()
#: a folder the writer cannot write to (admin_it may still upload there)
OTHER_FOLDER = next(f for f in sorted(FOLDERS) if not _holds(WRITER, f, WRITE))


# ---------------------------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------------------------
def _docx(heading: str, para: str) -> bytes:
    d = docx.Document()
    d.add_heading(heading, 1)
    d.add_paragraph(para)
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


def _xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Limits"
    for r in rows:
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def _pptx(title: str, body: str) -> bytes:
    p = pptx.Presentation()
    s = p.slides.add_slide(p.slide_layouts[1])
    s.shapes.title.text = title
    s.placeholders[1].text = body
    b = io.BytesIO()
    p.save(b)
    return b.getvalue()


def _pdf(text: str) -> bytes:
    """A one-page text PDF, written by hand (no PDF library needed to build it)."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def _with_member(data: bytes, name: str, payload: bytes, *, deflate=True) -> bytes:
    """Copy an OOXML zip and add one member."""
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED if deflate else zipfile.ZIP_STORED) as z:
        for i in src.infolist():
            z.writestr(i, src.read(i))
        z.writestr(name, payload)
    return buf.getvalue()


#: kind → (filename, bytes, question, the phrase the answer's evidence must contain). Each file
#: carries a made-up code name so retrieval is unambiguous.
CASES = {
    "docx": ("Rebate plan.docx",
             _docx("Quillfeather rebate plan", "The Quillfeather rebate threshold is 4.5 percent."),
             "What is the Quillfeather rebate threshold?", "Quillfeather rebate threshold"),
    "xlsx": ("Limits.xlsx",
             _xlsx([["Programme", "Daily limit"], ["Marrowind corporate card", "25000 SGD"]]),
             "What is the Marrowind corporate card daily limit?", "Marrowind corporate card"),
    "pptx": ("Launch.pptx", _pptx("Brightwater launch", "Brightwater goes live on Friday"),
             "When does Brightwater go live?", "Brightwater goes live on Friday"),
    "pdf": ("Audit.pdf", _pdf("Thornvale vendor audit is due on 14 November"),
            "When is the Thornvale vendor audit due?", "Thornvale vendor audit"),
    "csv": ("Fees.csv", b"Corridor,Fee\nSkellig remittance corridor,1.2 percent\n",
            "What is the Skellig remittance corridor fee?", "Skellig remittance corridor"),
    "md": ("Runbook.md", b"# Halloway failover\n\nThe Halloway failover drill runs every Tuesday.\n",
           "When does the Halloway failover drill run?", "Halloway failover drill"),
    "txt": ("Notes.txt", b"Ostrander settlement window closes at 16:00 SGT.\n",
            "When does the Ostrander settlement window close?", "Ostrander settlement window"),
    "whatsapp_android": (
        "WhatsApp Chat with Ops.txt",
        "12/10/2026, 21:04 - Priya: Varnholt cutover moved to Thursday 21:00\n"
        "12/10/2026, 21:05 - Raj: ok, I will tell the vendor\n".encode(),
        "When is the Varnholt cutover?", "Varnholt cutover moved to Thursday"),
    "whatsapp_ios": (
        "_chat.txt",
        "[12/10/26, 9:04:11 PM] Priya: Kestrelmoor rollback is approved\n"
        "[12/10/26, 9:05:02 PM] Raj: thanks\n".encode(),
        "Is the Kestrelmoor rollback approved?", "Kestrelmoor rollback is approved"),
}


# ---------------------------------------------------------------------------------------------
# app
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def world(tmp_path_factory):
    e = E2E(tmp_path_factory.mktemp("uploads"))
    if "drive" not in e.platforms():
        pytest.skip("no Drive connector")
    e.ingestor.ledger = e.ledger
    if not X.warm():
        pytest.skip("the convert readers cannot be imported here")
    return e


def _admin_client(e: E2E):
    return _Client(e.mock, base_url="http://testserver", headers={"Authorization": f"Bearer {ADMIN}"})


class _Cfg:
    """The app's config with ``demo_features`` (and any other switch) set explicitly."""

    def __init__(self, base, **over):
        self._base, self._over = base, over

    def __getattr__(self, name):
        return self._over[name] if name in self._over else getattr(self._base, name)


def _app(e: E2E, tmp_path, *, demo_login=True, demo_features=True, cfg_over=None, limiter=None):
    cfg = IbConfig(var_root=tmp_path / "var", world_path=WORLD, admin_token=ADMIN,
                   demo_login=demo_login)
    svc = Services(cfg=cfg, directory=e.directory, index=e.index, connectors=e.connectors,
                   ledger=e.ledger, answers=MemAnswers(), pipeline=e.pipeline, ingestor=e.ingestor)
    hooks: list = []
    app = create_app(svc, sim_router_factory=lambda cur, rr: make_sim_router(
        cfg, rr, client_factory=lambda: _admin_client(e), ingestor=e.ingestor, current=cur,
        ledger=e.ledger, on_reset=lambda: [h() for h in hooks]))
    from databridge_ib.app import principal_dependency
    router = make_uploads_router(
        svc, principal_dependency(lambda: app.state.svc),
        cfg=_Cfg(cfg, demo_features=demo_features, **(cfg_over or {})),
        client_factory=lambda: _admin_client(e),
        rate_limiter=limiter or _RateLimiter(n=1000), warm=False)
    app.include_router(router)
    hooks.insert(0, lambda: reset_uploads(svc))      # as mount_uploads_router does in build_app
    app.state.upload_state = router.upload_state
    return app, svc


def _as(app, svc, sub):
    u = svc.directory.get(sub)
    tok = authmod.mint(svc.cfg.token_key, sub, list(u.roles))
    return TestClient(app, headers={"Authorization": f"Bearer {tok}"})


@pytest.fixture
def env(world, tmp_path):
    """Uploads carry made-up code names, so tests that only upload share the world; a test that
    changes the world's permissions resets it itself."""
    app, svc = _app(world, tmp_path)
    yield world, app, svc


def _up(c, filename, data: bytes, folder=FOLDER, **extra):
    return c.post("/sim/upload-file", json={"filename": filename, "folder": folder,
                                            "content_b64": base64.b64encode(data).decode(),
                                            **extra})


def _refusals(e):
    return [ev for ev in e.ledger.events if ev.kind == "request.failed"
            and ev.stage == "sim.upload-file"]


# ---------------------------------------------------------------------------------------------
# every file type: upload → webhook → connector → index → cited answer
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", sorted(CASES))
def test_each_file_type_is_ingested_and_answerable_with_a_citation(env, case):
    e, app, svc = env
    filename, data, question, phrase = CASES[case]
    hooks = len(e.hooks)
    c = _as(app, svc, WRITER)
    r = _up(c, filename, data)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"ok", "doc_id", "platform", "container", "kind", "at"}
    assert body["ok"] is True and body["platform"] == "drive"
    assert body["container"] == FOLDERS[FOLDER]["drive"]
    assert body["kind"] == ("whatsapp" if case.startswith("whatsapp")
                            else X.kind_for_filename(filename))
    assert body["doc_id"].startswith("drive:up-")
    # reached the index only through Drive's signed webhook (one delivery, no direct write)
    assert len(e.hooks) == hooks + 1 and e.hooks[-1][0] == "drive"
    row = e.index.doc(body["doc_id"])
    assert row is not None and phrase in row["text"]

    ans = c.post("/ib/ask", json={"question": question})
    assert ans.status_code == 200, ans.text
    cited = [x["doc_id"] for x in ans.json()["citations"]]
    assert body["doc_id"] in cited, (case, cited)

    doc = c.get(f"/ib/doc/{body['doc_id']}")
    assert doc.status_code == 200, doc.text
    d = doc.json()
    assert d["kind"] == "file" and d["author"]["id"] == WRITER and phrase in d["excerpt"]

    g = c.get("/ib/graph", params={"focus": body["doc_id"]})
    assert g.status_code == 200, g.text
    assert body["doc_id"] in {n["id"] for n in g.json()["nodes"]}


def test_structure_survives_extraction(env):
    """Excel → "Sheet / Row N: header=value"; PowerPoint → one heading per slide; Word → headings."""
    e, app, svc = env
    c = _as(app, svc, WRITER)
    texts = {}
    for kind in ("xlsx", "pptx", "docx"):
        filename, data, _, _ = CASES[kind]
        texts[kind] = e.index.doc(_up(c, filename, data).json()["doc_id"])["text"]
    assert "Sheet: Limits" in texts["xlsx"]
    assert "Row 1: Programme=Marrowind corporate card, Daily limit=25000 SGD" in texts["xlsx"]
    assert "## Brightwater launch" in texts["pptx"]
    assert "# Quillfeather rebate plan" in texts["docx"]


def test_a_whatsapp_export_is_indexed_one_message_per_paragraph(env):
    e, app, svc = env
    r = _up(_as(app, svc, WRITER), *CASES["whatsapp_android"][:2])
    text = e.index.doc(r.json()["doc_id"])["text"]
    assert text.startswith("WhatsApp chat export: 2 messages from Priya, Raj")
    assert "\n\n12/10/2026 21:04, Priya: Varnholt cutover moved to Thursday 21:00" in text


def test_card_and_nric_inside_a_docx_are_masked(env):
    e, app, svc = env
    card, nric = "4111 1111 1111 1111", "S1234567D"
    c = _as(app, svc, WRITER)
    r = _up(c, "Onboarding.docx", _docx("Tamsworth onboarding",
                                       f"Tamsworth test card {card} and NRIC {nric} on file."))
    assert r.status_code == 200, r.text
    row = e.index.doc(r.json()["doc_id"])
    assert "Tamsworth test card" in row["text"]
    for raw in (card, card.replace(" ", ""), nric):
        assert raw not in row["text"]
    assert "567D" in row["text"]                         # the partial NRIC form
    d = c.get(f"/ib/doc/{r.json()['doc_id']}").json()
    assert card not in d["excerpt"] and nric not in d["excerpt"]


def test_card_number_inside_an_xlsx_is_masked(env):
    e, app, svc = env
    card = "4111 1111 1111 1111"
    c = _as(app, svc, WRITER)
    r = _up(c, "Cards.xlsx", _xlsx([["Holder", "Card"], ["Wrenfield test holder", card],
                                    ["Second", 4111111111111111]]))
    assert r.status_code == 200, r.text
    d = c.get(f"/ib/doc/{r.json()['doc_id']}").json()
    assert "Wrenfield test holder" in d["excerpt"]
    assert card not in d["excerpt"] and "4111111111111111" not in d["excerpt"]
    row = e.index.doc(r.json()["doc_id"])
    assert card not in row["text"] and "4111111111111111" not in row["text"]


def test_text_is_cleaned_and_the_name_is_display_only(env):
    e, app, svc = env
    c = _as(app, svc, WRITER)
    r = _up(c, "../../secrets/<b>chat</b>.txt",
            "Pellucid <script>alert(1)</script>notes​ are final\u0007\n".encode())
    assert r.status_code == 200, r.text
    row = e.index.doc(r.json()["doc_id"])
    assert "<script>" not in row["text"] and "​" not in row["text"] and "\x07" not in row["text"]
    assert "Pellucid alert(1)notes are final" in row["text"]
    assert row["title"] == "chat.txt"                # path parts and tags gone
    assert display_name("a/b\\c.docx", None, "docx") == "c.docx"
    assert display_name("x.docx", "Q3 <i>plan</i>", "docx") == "Q3 plan.docx"
    assert len(display_name("y" * 300 + ".pdf", None, "pdf")) == 120


# ---------------------------------------------------------------------------------------------
# who and where
# ---------------------------------------------------------------------------------------------
def test_restricted_upload_equals_non_existent_for_a_person_without_folder_access(env):
    e, app, svc = env
    doc_id = _up(_as(app, svc, WRITER), *CASES["docx"][:2]).json()["doc_id"]
    out = _as(app, svc, OUTSIDER)
    hidden, unknown = out.get(f"/ib/doc/{doc_id}"), out.get("/ib/doc/drive:up-0000000000000000")
    assert hidden.status_code == unknown.status_code == 404
    assert hidden.content == unknown.content
    ans = out.post("/ib/ask", json={"question": CASES["docx"][2]}).json()
    assert doc_id not in json.dumps(ans)


def test_write_permission_is_required_and_read_is_not_enough(env):
    e, app, svc = env
    for who in (READER, OUTSIDER):
        r = _up(_as(app, svc, who), *CASES["md"][:2])
        assert r.status_code == 404 and r.json() == REFUSAL
    assert [ev.error_code for ev in _refusals(e)[-2:]] == ["no_write_permission"] * 2


def test_write_permission_is_read_live_from_drive(env):
    """The writer's grant removed at the source WITHOUT a webhook: the live check refuses."""
    e, app, svc = env
    holders = [p for p in FOLDERS[FOLDER].get("permissions", []) if p.get("role") in WRITE]
    try:
        assert _up(_as(app, svc, WRITER), *CASES["txt"][:2]).status_code == 200
        for p in holders:
            r = _admin_client(e).post(f"/admin/drive/files/{FOLDER}/permissions/remove?webhook=0",
                                      json={"permission_id": p["id"]})
            assert r.status_code == 200
        assert _up(_as(app, svc, WRITER), *CASES["txt"][:2]).status_code == 404
        assert _refusals(e)[-1].error_code == "no_write_permission"
    finally:
        e.reset()


def test_a_source_error_on_the_live_check_is_a_refusal(env, monkeypatch):
    e, app, svc = env
    from databridge_ib.connectors._http import SourceFailure

    def boom(*_a, **_k):
        raise SourceFailure("source_429")
    monkeypatch.setattr(e.connectors["drive"], "_permissions", boom)
    r = _up(_as(app, svc, WRITER), *CASES["txt"][:2])
    assert r.status_code == 404 and r.json() == REFUSAL
    assert _refusals(e)[-1].error_code == "source_error"


def test_the_body_names_no_person_and_the_owner_is_the_signed_in_person(env):
    e, app, svc = env
    w = _as(app, svc, WRITER)
    for extra in ({"owner": OUTSIDER}, {"user": OUTSIDER}, {"sub": OUTSIDER}):
        assert _up(w, *CASES["txt"][:2], **extra).json() == REFUSAL
        assert _refusals(e)[-1].error_code == "bad_body"
    fid = _up(w, *CASES["txt"][:2]).json()["doc_id"].split(":", 1)[1]
    meta = e.svc.get(f"/drive/v3/files/{fid}").json()
    assert meta["lastModifyingUser"]["emailAddress"] == svc.directory.get(WRITER).platform_ids["drive"]


def test_admin_it_may_upload_into_any_folder(env):
    e, app, svc = env
    r = _up(_as(app, svc, ADMIN_USER), *CASES["csv"][:2], folder=OTHER_FOLDER)
    assert r.status_code == 200, r.text
    assert _up(_as(app, svc, WRITER), *CASES["csv"][:2], folder=OTHER_FOLDER).status_code == 404
    assert _up(_as(app, svc, ADMIN_USER), *CASES["csv"][:2],
               folder="fd-does-not-exist").json() == REFUSAL


@pytest.mark.parametrize("switch,reason", [({"demo_login": False}, "demo_login_off"),
                                           ({"demo_features": False}, "demo_features_off"),
                                           ({"cfg_over": {"deployed": True}}, "deployed")])
def test_demo_only_switches_refuse_everyone_including_admin_it(world, tmp_path, switch, reason):
    app, svc = _app(world, tmp_path, **switch)
    hooks = len(world.hooks)
    for who in (WRITER, ADMIN_USER):
        r = _up(_as(app, svc, who), *CASES["txt"][:2])
        assert r.status_code == 404 and r.json() == REFUSAL
        assert _refusals(world)[-1].error_code == reason and _refusals(world)[-1].sub == who
    assert len(world.hooks) == hooks


def test_demo_features_switch_is_read_like_the_other_switches(monkeypatch):
    class Bare:                                       # a config without the field: the environment
        pass
    for value, on in (("1", True), (" 1 ", True), ("0", False), ("", False), ("true", False)):
        monkeypatch.setenv("IB_DEMO_FEATURES", value)
        assert demo_features_on(Bare()) is on
    monkeypatch.setenv("IB_DEMO_FEATURES", "1")
    assert demo_features_on(_Cfg(Bare(), demo_features=False)) is False


def test_unauthenticated_is_401(env):
    e, app, svc = env
    assert TestClient(app).post("/sim/upload-file", json={}).status_code == 401


# ---------------------------------------------------------------------------------------------
# what: every refusal is the same response
# ---------------------------------------------------------------------------------------------
def _bomb_total() -> bytes:
    return _with_member(CASES["docx"][1], "word/media/pad.bin", b"\0" * (X.ZIP_MAX_TOTAL + 1))


def _bomb_ratio() -> bytes:
    return _with_member(CASES["docx"][1], "word/media/pad.bin", b"\0" * (8 * 1024 * 1024))


def _xxe_docx() -> bytes:
    base = CASES["docx"][1]
    doc = zipfile.ZipFile(io.BytesIO(base)).read("word/document.xml")
    head, rest = doc.split(b"?>", 1)
    evil = head + b'?><!DOCTYPE w [<!ENTITY x SYSTEM "file:///C:/Windows/win.ini">]>' + rest
    src = zipfile.ZipFile(io.BytesIO(base))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i in src.infolist():
            z.writestr(i, evil if i.filename == "word/document.xml" else src.read(i))
    return buf.getvalue()


REFUSED_FILES = {
    "extension": ("tool.exe", b"MZ\x90\x00"),
    "macro_extension": ("macro.docm", CASES["docx"][1]),
    "docx_is_a_pdf": ("fake.docx", CASES["pdf"][1]),
    "xlsx_is_a_docx": ("fake.xlsx", CASES["docx"][1]),
    "pdf_is_text": ("fake.pdf", b"just text"),
    "txt_not_utf8": ("latin.txt", "café".encode("latin-1")),
    "txt_with_nul": ("nul.txt", b"a\x00b"),
    "empty": ("empty.txt", b""),
    "macro_docx": ("macro.docx", _with_member(CASES["docx"][1], "word/vbaProject.bin", b"VBA")),
    "zip_bomb_total": ("bomb.docx", _bomb_total()),
    "zip_bomb_ratio": ("ratio.docx", _bomb_ratio()),
    "corrupt_docx": ("broken.docx", b"PK\x03\x04" + b"\x00" * 64),
    "too_large": ("big.txt", b"a" * (X.MAX_FILE_BYTES + 1)),
    "xml_entity": ("xxe.docx", _xxe_docx()),
    "pdf_pages": ("long.pdf", pdf([f"page {i}" for i in range(X.PDF_MAX_PAGES + 1)])),
    "no_text": ("blank.md", b"<p></p>\n\n"),
}


@pytest.mark.parametrize("name", sorted(REFUSED_FILES))
def test_bad_files_are_refused_with_the_one_response(env, name):
    e, app, svc = env
    hooks = len(e.hooks)
    r = _up(_as(app, svc, WRITER), *REFUSED_FILES[name])
    assert r.status_code == 404 and r.json() == REFUSAL, (name, r.text)
    assert len(e.hooks) == hooks                    # nothing reached Drive
    assert _refusals(e)[-1].sub == WRITER


def test_macro_and_bomb_reasons_are_ledgered(env):
    e, app, svc = env
    c = _as(app, svc, WRITER)
    for name, want in (("macro_docx", "file_macros"), ("zip_bomb_total", "file_zip_bomb_total"),
                       ("zip_bomb_ratio", "file_zip_bomb_ratio"), ("xml_entity", "file_xml_dtd"),
                       ("pdf_pages", "file_pdf_pages"), ("macro_extension", "type"),
                       ("docx_is_a_pdf", "file_magic_mismatch"), ("no_text", "file_no_text")):
        assert _up(c, *REFUSED_FILES[name]).status_code == 404
        assert _refusals(e)[-1].error_code == want


def test_every_refusal_is_byte_identical(world, tmp_path):
    t = [0.0]
    app, svc = _app(world, tmp_path, limiter=_RateLimiter(n=8, clock=lambda: t[0]))
    dep_app, dep_svc = _app(world, tmp_path / "dep", cfg_over={"deployed": True})
    off_app, off_svc = _app(world, tmp_path / "off", demo_features=False)
    w = _as(app, svc, WRITER)
    good = base64.b64encode(CASES["txt"][1]).decode()
    resp = [
        _up(w, *REFUSED_FILES["extension"]),                              # what
        _up(w, *REFUSED_FILES["macro_docx"]),
        _up(w, *CASES["txt"][:2], folder="fd-does-not-exist"),            # where: unknown
        _up(_as(app, svc, OUTSIDER), *CASES["txt"][:2]),                  # where: restricted
        w.post("/sim/upload-file", json={"filename": "a.txt", "folder": FOLDER,
                                         "content_b64": good, "owner": OUTSIDER}),  # body
        w.post("/sim/upload-file", json={"filename": "a.txt", "folder": FOLDER,
                                         "content_b64": "%%%not-base64%%%"}),
        w.post("/sim/upload-file", content=b"{not json",
               headers={"Content-Type": "application/json"}),
        w.post("/sim/upload-file", content=b"x" * (MAX_BODY_BYTES + 1),
               headers={"Content-Type": "application/json"}),             # body too large
        _up(_as(dep_app, dep_svc, WRITER), *CASES["txt"][:2]),            # when: deployed
        _up(_as(off_app, off_svc, ADMIN_USER), *CASES["txt"][:2]),        # when: features off
    ]
    while True:                                                           # rate limit
        r = _up(w, *REFUSED_FILES["empty"])
        if world.ledger.events[-1].error_code == "rate_limited":
            resp.append(r)
            break
    first = resp[0]
    for r in resp:
        assert r.status_code == 404
        assert r.content == first.content
        assert r.headers.get("content-type") == first.headers.get("content-type")


def test_oversized_body_is_refused_before_decoding(env, monkeypatch):
    e, app, svc = env
    called = []
    import types
    from databridge_ib import uploads as U
    monkeypatch.setattr(U, "base64", types.SimpleNamespace(
        b64decode=lambda *a, **k: called.append(1) or b"", b64encode=base64.b64encode))
    r = _as(app, svc, WRITER).post("/sim/upload-file", content=b"x" * (MAX_BODY_BYTES + 1),
                                   headers={"Content-Type": "application/json"})
    assert r.status_code == 404 and r.json() == REFUSAL and not called
    assert _refusals(e)[-1].error_code == "body_too_large"


def test_rate_limit_five_per_minute(world, tmp_path):
    t = [0.0]
    app, svc = _app(world, tmp_path, limiter=_RateLimiter(n=5, clock=lambda: t[0]))
    c = _as(app, svc, WRITER)
    for i in range(5):
        assert _up(c, f"n{i}.txt", f"Note number {i} for Galbraith".encode()).status_code == 200
    r = _up(c, "n5.txt", b"one too many")
    assert r.status_code == 404 and r.json() == REFUSAL
    assert _refusals(world)[-1].error_code == "rate_limited"
    t[0] += 61
    assert _up(c, "n6.txt", b"a minute later").status_code == 200


# ---------------------------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------------------------
def test_sim_reset_removes_uploads(env):
    e, app, svc = env
    ids = [_up(_as(app, svc, WRITER), *CASES[k][:2]).json()["doc_id"] for k in ("xlsx", "txt")]
    assert all(e.index.doc(d) is not None for d in ids)
    r = _as(app, svc, ADMIN_USER).post("/sim/reset")
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    for d in ids:
        assert e.index.doc(d) is None
        assert _as(app, svc, WRITER).get(f"/ib/doc/{d}").status_code == 404


def test_reset_hook_drops_a_lingering_upload_and_clears_the_rate_window(world, tmp_path):
    """If reconciliation missed an uploaded row, ``reset_uploads`` still drops it; the rate window
    starts again."""
    t = [0.0]
    app, svc = _app(world, tmp_path, limiter=_RateLimiter(n=2, clock=lambda: t[0]))
    c = _as(app, svc, WRITER)
    doc = _up(c, "Lingering.txt", b"Ambleforth lingering upload note").json()["doc_id"]
    assert _up(c, "Second.txt", b"Ambleforth second note").status_code == 200
    assert _up(c, "Third.txt", b"Ambleforth third").json() == REFUSAL          # rate limited
    world.admin_post("/admin/reset", {})          # the platform forgets the file, no reconcile
    assert world.index.doc(doc) is not None
    assert reset_uploads(svc) >= 1
    assert world.index.doc(doc) is None
    assert _up(c, "Fourth.txt", b"Ambleforth fourth").status_code == 200       # window cleared
    world.reset()


# ---------------------------------------------------------------------------------------------
# the live-kit files (docs/finals/live_kit), end to end, as the kit runs them on stage
# ---------------------------------------------------------------------------------------------
_KIT_JSON = WORLD.parents[2] / "apps" / "internal_brain_web" / "public" / "finals" / "live_samples.json"


def _kit() -> list[dict]:
    if not _KIT_JSON.is_file():
        return []
    return [s for s in json.loads(_KIT_JSON.read_text(encoding="utf-8"))["samples"]
            if s["kind"] == "upload"]


def _cited(c, question: str) -> list[str]:
    r = c.post("/ib/ask", json={"question": question})
    assert r.status_code == 200, r.text
    return [x["doc_id"] for x in r.json()["citations"]]


@pytest.mark.parametrize("sample", _kit(), ids=lambda s: s["id"])
def test_live_kit_upload_end_to_end(env, sample):
    """Before: the kit's question is not answered from the new file. Upload as the kit's person
    into the kit's folder. After: the asker gets an answer that cites the new file, whose indexed
    text holds the proof phrase; Ken gets it only when the folder lets him read it."""
    e, app, svc = env
    path = WORLD.parents[3] / sample["file"]["path"]
    if not path.is_file():
        pytest.skip("run docs/finals/live_kit/make_files.py")
    for who in (sample["sign_in_as"], sample["ask_as"]):
        if svc.directory.get(who) is None:
            pytest.skip(f"{who} is not in this world")
    asker = _as(app, svc, sample["ask_as"])
    if sample.get("before_expect") == "refusal" and sample.get("before_question"):
        assert _cited(asker, sample["before_question"]) == [], sample["id"]     # not known yet
    r = _up(_as(app, svc, sample["sign_in_as"]), path.name, path.read_bytes(),
            folder=sample["folder"]["id"])
    assert r.status_code == 200, (sample["id"], r.text, _refusals(e)[-1].error_code)
    doc_id = r.json()["doc_id"]
    assert r.json()["container"] == sample["folder"]["drive"]
    text = " ".join(e.index.doc(doc_id)["text"].split())
    assert sample["proof_phrase"].lower() in text.lower()
    for want in sample.get("after_must_contain") or []:
        assert want in text, (sample["id"], want)
    from databridge_ib.sensitive import mask
    assert mask(e.index.doc(doc_id)["text"])[1] == []      # every card / NRIC / IBAN already masked
    assert doc_id in _cited(asker, sample["question"]), sample["id"]
    ken = sample.get("ken") or {}
    if ken and svc.directory.get("ken.ext") is not None:
        got = doc_id in _cited(_as(app, svc, "ken.ext"), ken.get("question") or sample["question"])
        assert got is bool(ken.get("in_scope")), sample["id"]
