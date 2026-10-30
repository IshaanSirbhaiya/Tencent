"""``extract_files``: the safe-parsing rules for uploaded files, without the platforms.

Every reader is one of Walnut's convert readers, run in a worker process. These tests build their
files here (python-docx / openpyxl / python-pptx; a PDF is written by hand)."""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from databridge_ib import extract_files as X

docx = pytest.importorskip("docx")
openpyxl = pytest.importorskip("openpyxl")
pptx = pytest.importorskip("pptx")

ROOT = Path(__file__).resolve().parents[3]                      # databridge/
KIT = ROOT.parent / "docs" / "finals" / "live_kit" / "files"


@pytest.fixture(scope="module", autouse=True)
def worker():
    if not X.warm():
        pytest.skip("the convert readers cannot be imported here")


def _docx(*paras: str) -> bytes:
    d = docx.Document()
    for p in paras:
        d.add_paragraph(p)
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


def pdf(pages: list[str]) -> bytes:
    """A text PDF with one line per page, written by hand."""
    n = len(pages)
    objs: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>",
                         b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
                             b" ".join(b"%d 0 R" % (4 + 2 * i) for i in range(n)), n),
                         b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i, text in enumerate(pages):
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
                    b"/Resources << /Font << /F1 3 0 R >> >> >>" % (5 + 2 * i))
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def _replace_member(data: bytes, name: str, payload: bytes) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i in src.infolist():
            z.writestr(i, payload if i.filename == name else src.read(i))
    return buf.getvalue()


def _reason(kind: str, data: bytes, **kw) -> str:
    with pytest.raises(X.Refused) as ei:
        X.extract(kind, data, **kw)
    return ei.value.reason


# ---------------------------------------------------------------------------------------------
# the convert readers are reachable on the Internal Brain's own PYTHONPATH
# ---------------------------------------------------------------------------------------------
def test_readers_import_on_the_demo_pythonpath_without_packages_convert():
    """``scripts/demo_up.sh`` starts the IB with IB_PATH, which does not list packages/convert:
    extract_files finds it by itself (appended to sys.path, so it shadows nothing)."""
    ib_path = re.search(r'^IB_PATH="([^"]+)"', (ROOT / "scripts" / "demo_up.sh").read_text(
        encoding="utf-8"), re.M).group(1)
    assert "packages/convert" not in ib_path
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        str(ROOT / p) for p in ib_path.replace("${SEP}", "|").split("|")))
    code = ("import sys; from databridge_ib import extract_files as X; "
            "print(X.extract('csv', b'a,b\\n1,2\\n')); "
            "print(sys.path.index(str(X._CONVERT)) == len(sys.path) - 1)")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True,
                         text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.split("\n")[:2] == ["Row 1: a=1, b=2", "True"]



# ---------------------------------------------------------------------------------------------
# readers and flattening
# ---------------------------------------------------------------------------------------------
def test_pdf_text_is_extracted_and_the_page_cap_holds():
    assert "Thornvale audit" in X.extract("pdf", pdf(["Thornvale audit is due"]))
    assert _reason("pdf", pdf([f"page {i}" for i in range(X.PDF_MAX_PAGES + 1)])) == "pdf_pages"
    assert "page 49" in X.extract("pdf", pdf([f"page {i}" for i in range(X.PDF_MAX_PAGES)]))


def test_xlsx_uses_the_header_row_and_keeps_title_lines():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Fees"
    for r in (["Quillon fee schedule (draft)"], [], ["Plan", "Fee"], ["Growth", "0.30%"],
              ["Approval: committee on 18 November"]):
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    text = X.extract("xlsx", b.getvalue())
    assert "Sheet: Fees" in text and "Quillon fee schedule (draft)" in text
    assert "Row 1: Plan=Growth, Fee=0.30%" in text
    assert "Approval: committee on 18 November" in text and "Plan=Approval" not in text


def test_text_is_capped_and_cleaned():
    text = X.extract("txt", ("Sentence of filler text. " * 20_000).encode())
    assert len(text) <= X.MAX_TEXT_CHARS
    dirty = "Cafe\u0301 <b>bold</b> a < b\u202e\u200b\x07 <!-- note --> end"
    assert X.clean_text(dirty) == "Café bold a < b  end"



def test_clean_text_is_linear_on_unclosed_comments():
    """Round 3 L6: 5 MiB of unclosed ``<!--`` took ~25 s with the old lazy regex."""
    for evil in ("<!--" * 1_310_720, "<!--" * 1_310_720 + "-->", "<!--" + "x" * 5_000_000 + "-->",
                 "<a" * 2_621_440):
        t0 = time.monotonic()
        X.clean_text(evil)
        assert time.monotonic() - t0 < 2.0
    assert X.clean_text("a<!-- x -->b<!---->c<!-- " + "y" * 2001 + " -->d") == \
        "abc<!-- " + "y" * 2001 + " -->d"
    assert X.clean_text("a <!-- unclosed b") == "a <!-- unclosed b"
    assert X.clean_text("<!-- one --> x <!-- two -->") == "x"


# ---------------------------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------------------------
def test_xml_dtd_and_entities_are_refused_before_any_reader():
    base = _docx("Plain paragraph")
    doc = zipfile.ZipFile(io.BytesIO(base)).read("word/document.xml")
    head, rest = doc.split(b"?>", 1)
    xxe = head + b'?><!DOCTYPE w [<!ENTITY x SYSTEM "file:///C:/Windows/win.ini">]>' + rest
    lol = head + b'?><!DOCTYPE w [<!ENTITY a "aaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>' + rest
    for payload in (xxe, lol):
        assert _reason("docx", _replace_member(base, "word/document.xml", payload)) == "xml_dtd"



def _rename_member(data: bytes, old: str, new: str) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i in src.infolist():
            body = src.read(i)
            if i.filename in ("[Content_Types].xml", "word/_rels/document.xml.rels", "_rels/.rels"):
                body = body.replace(old.split("/")[-1].encode(), new.split("/")[-1].encode())
            z.writestr(new if i.filename == old else i.filename, body)
    return buf.getvalue()


def test_xml_dtd_bypasses_from_round3_l2_are_refused():
    """Round 3 L2: a DTD in a UTF-16 part (with or without a BOM, either byte order), in a part not
    named ``.xml``, or behind a non-UTF encoding declaration never reaches a reader."""
    base = _docx("Plain paragraph")
    doc = zipfile.ZipFile(io.BytesIO(base)).read("word/document.xml").decode("utf-8")
    body = doc.split("?>", 1)[1]
    dtd = '<!DOCTYPE w [<!ENTITY xxe SYSTEM "file:///etc/hostname">]>'
    u16 = '<?xml version="1.0" encoding="UTF-16" standalone="yes"?>' + dtd + body
    for payload in (b"\xff\xfe" + u16.encode("utf-16-le"), b"\xfe\xff" + u16.encode("utf-16-be"),
                    u16.encode("utf-16-le"), u16.encode("utf-32")):
        assert _reason("docx", _replace_member(base, "word/document.xml", payload)) == "xml_dtd"
    renamed = _rename_member(base, "word/document.xml", "word/document.dat")
    assert "word/document.dat" in zipfile.ZipFile(io.BytesIO(renamed)).namelist()
    with_dtd = doc.replace("?>", "?>" + dtd, 1).encode("utf-8")
    assert _reason("docx", _replace_member(renamed, "word/document.dat", with_dtd)) == "xml_dtd"
    utf7 = ('<?xml version="1.0" encoding="UTF-7"?>' + body).encode("utf-8")
    assert _reason("docx", _replace_member(base, "word/document.xml", utf7)) == "xml_encoding"
    ebcdic = '<?xml version="1.0" encoding="IBM037"?><w/>'.encode("cp037")
    assert _reason("docx", _replace_member(base, "word/document.xml", ebcdic)) == "xml_encoding"
    # a UTF-16 part with no DTD is still read
    clean16 = b"\xff\xfe" + ('<?xml version="1.0" encoding="UTF-16" standalone="yes"?>' + body
                              ).encode("utf-16-le")
    X.check_bytes("docx", _replace_member(base, "word/document.xml", clean16))


def test_zip_entry_count_cap():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        for i in range(X.ZIP_MAX_ENTRIES):
            z.writestr(f"word/p{i}.xml", "<a/>")
    assert _reason("docx", buf.getvalue()) == "zip_entries"


def test_macro_enabled_content_type_is_refused():
    base = _docx("x")
    ct = zipfile.ZipFile(io.BytesIO(base)).read("[Content_Types].xml").replace(
        b"document.main+xml", b"document.macroEnabled.main+xml")
    assert _reason("docx", _replace_member(base, "[Content_Types].xml", ct)) == "macros"
    assert X.kind_for_filename("plan.docm") is None and X.kind_for_filename("a.xlsm") is None


def test_kind_is_decided_by_extension_and_checked_against_the_bytes():
    assert X.kind_for_filename("../x/Plan.DOCX") == "docx"
    assert X.kind_for_filename(".pdf") is None and X.kind_for_filename("noext") is None
    assert _reason("pdf", _docx("x")) == "magic_mismatch"
    assert _reason("xlsx", _docx("x")) == "magic_mismatch"
    assert _reason("csv", b"\xff\xfe") == "not_utf8"


def test_a_read_past_the_time_budget_is_killed_with_its_worker():
    pid = X.worker_pid()
    t0 = time.monotonic()
    with pytest.raises(X.Refused) as ei:
        X.run_in_worker(time.sleep, (60,), 1.0)
    assert ei.value.reason == "time_budget" and time.monotonic() - t0 < 10
    assert X.worker_pid() is None                       # the process running it is gone
    if pid:
        assert not _alive(pid)
    assert "ok" in X.extract("txt", b"ok")              # a fresh worker serves the next file
    assert X.worker_pid() not in (None, pid)


def _alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)                            # QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return code.value == 259                                         # STILL_ACTIVE


# ---------------------------------------------------------------------------------------------
# WhatsApp chat exports
# ---------------------------------------------------------------------------------------------
ANDROID = ("24/10/2026, 02:13 - Messages and calls are end-to-end encrypted. No one outside of this "
           "chat can read them.\n"
           "24/10/2026, 02:13 - Mira created group \"Ops\"\n"
           "24/10/2026, 02:14 - Mira: Varnholt cutover moved\n"
           "to Thursday 21:00\n"
           "24/10/2026, 02:15 - Raj: <Media omitted>\n"
           "24/10/2026, 02:16 - +65 9000 0000: ok\n")
IOS = ("\u200e[12/10/26, 9:04:11\u202fPM] Mira: Kestrelmoor rollback is approved\n"
       "[12/10/26, 9:05:02\u202fPM] Raj: \u200eimage omitted\n"
       "[12/10/26, 9:06:00\u202fPM] Raj: thanks\n")


def test_whatsapp_android_export_is_one_paragraph_per_message():
    out = X.whatsapp_text(ANDROID)
    paras = out.split("\n\n")
    assert paras[0] == ("WhatsApp chat export: 2 messages from +65 9000 0000, Mira, "
                        "24/10/2026 to 24/10/2026.")
    assert paras[1:] == ["24/10/2026 02:14, Mira: Varnholt cutover moved to Thursday 21:00",
                         "24/10/2026 02:16, +65 9000 0000: ok"]
    assert "encrypted" not in out and "created group" not in out and "omitted" not in out


def test_whatsapp_ios_export():
    out = X.whatsapp_text(IOS)
    assert "12/10/26 9:04:11 PM, Mira: Kestrelmoor rollback is approved" in out
    assert "12/10/26 9:06:00 PM, Raj: thanks" in out and "omitted" not in out


def test_extract_txt_rewrites_a_chat_and_leaves_other_text_alone():
    assert X.extract("txt", ANDROID.encode()).split("\n\n")[1].startswith("24/10/2026 02:14, Mira:")
    plain = "Meeting at 10:00 - agenda below\nItem: budget\n"
    assert X.whatsapp_text(plain) is None and not X.is_whatsapp(plain)


def test_no_world_text_file_is_mistaken_for_a_chat():
    world = json.loads((ROOT / "fixtures" / "enterprise" / "world.json").read_text(encoding="utf-8"))
    texts = [f.get("content") or "" for f in world["drive"]["files"]]
    assert texts and not any(X.is_whatsapp(t) for t in texts)


# ---------------------------------------------------------------------------------------------
# the live-kit sample files (docs/finals/live_kit/files, built by make_files.py)
# ---------------------------------------------------------------------------------------------
def _kit_samples() -> list[dict]:
    p = ROOT / "apps" / "internal_brain_web" / "public" / "finals" / "live_samples.json"
    if not p.is_file():
        return []
    return [s for s in json.loads(p.read_text(encoding="utf-8"))["samples"] if s["kind"] == "upload"]


@pytest.mark.parametrize("sample", _kit_samples(), ids=lambda s: s["id"])
def test_every_live_kit_file_extracts_with_its_proof_phrase(sample):
    path = ROOT.parent / sample["file"]["path"]
    if not path.is_file():
        pytest.skip("run docs/finals/live_kit/make_files.py")
    kind = X.kind_for_filename(path.name)
    assert kind is not None
    text = X.extract(kind, path.read_bytes())
    assert sample["proof_phrase"].lower() in " ".join(text.split()).lower()


def test_every_whitelisted_format_has_its_reader_in_this_environment():
    """All seven formats are readable with the installed libraries (python-docx, python-pptx,
    python-calamine, pypdfium2). A missing library switches off only the formats that need it."""
    assert sorted(X.available_kinds()) == sorted(X.KIND_MIME)


def test_a_missing_library_switches_off_only_its_formats(monkeypatch):
    monkeypatch.setattr(X, "_READERS", {k: v for k, v in X._READERS.items() if k != "_pdfium"})
    monkeypatch.setattr(X, "_MISSING", {"_pdfium"})
    with pytest.raises(X.Refused) as ei:
        X._readers_for("pdf")
    assert ei.value.reason == "unavailable"
    assert set(X._readers_for("csv")) == {"csv"}


# ---------------------------------------------------------------------------------------------
# round 4 Codex B11: a sparse .xlsx is bounded BEFORE any reader builds its dense grid
# ---------------------------------------------------------------------------------------------
def _xlsx_cells(cells: dict[str, str]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for ref, v in cells.items():
        ws[ref] = v
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def _sheet_member(data: bytes) -> str:
    return next(n for n in zipfile.ZipFile(io.BytesIO(data)).namelist()
                if n.startswith("xl/worksheets/") and n.endswith(".xml"))


def _patch_sheet(data: bytes, fn) -> bytes:
    name = _sheet_member(data)
    xml = zipfile.ZipFile(io.BytesIO(data)).read(name).decode("utf-8")
    return _replace_member(data, name, fn(xml).encode("utf-8"))


def test_sparse_xlsx_extent_is_measured_from_the_xml_without_a_reader():
    """The extent is max_row x max_column per sheet, from cell and row references, the declared
    dimension, merged ranges, hyperlinks and comments (openpyxl materialises every one of those),
    and from implicit positions when a cell or row carries no reference."""
    small = _xlsx_cells({"A1": "Plan", "B1": "Fee", "A2": "Growth", "B2": "0.30%"})
    assert X.xlsx_extent(small) == 4
    far = _xlsx_cells({"A1": "a", "XFD1048576": "far"})
    assert X.xlsx_extent(far) == 16384 * 1048576
    merged = _patch_sheet(small, lambda x: x.replace(
        "</sheetData>", '</sheetData><mergeCells count="1"><mergeCell ref="C3:ZZ90000"/></mergeCells>'))
    assert X.xlsx_extent(merged) == 702 * 90000
    linked = _patch_sheet(small, lambda x: x.replace(
        "</sheetData>", '</sheetData><hyperlinks><hyperlink ref="A1:AB60000" r:id="rId9"/>'
                        '</hyperlinks>'))
    assert X.xlsx_extent(linked) == 28 * 60000
    implicit = _patch_sheet(small, lambda x: re.sub(r'<sheetData>.*</sheetData>',
                                                    "<sheetData><row>" + "<c/>" * 700
                                                    + "</row><row/><row/></sheetData>", x,
                                                    flags=re.S))
    assert X.xlsx_extent(implicit) == 700 * 3
    assert X.xlsx_extent(_patch_sheet(small, lambda x: x.replace(
        'r="B2"', 'r="B99999999999999999999"'))) > X.XLSX_MAX_CELLS


def test_sparse_xlsx_is_refused_before_iterating():
    over = _xlsx_cells({"A1": "Plan", "B1": "Fee", "A2": "Growth", "B2": "1",
                        "CV12000": "far"})                  # 100 x 12,000 = 1.2M cells
    assert _reason("xlsx", over) == "xlsx_dimensions"
    t0 = time.monotonic()
    assert _reason("xlsx", _xlsx_cells({"A1": "a", "B1": "b", "XFD1048576": "far"})) == \
        "xlsx_dimensions"
    assert time.monotonic() - t0 < 10                    # refused up front, not by the kill
    assert "Row 1: Plan=Growth, Fee=1" in X.extract(
        "xlsx", _xlsx_cells({"A1": "Plan", "B1": "Fee", "A2": "Growth", "B2": "1"}))


def test_an_xlsx_tag_of_any_length_is_measured_round5():
    """Round 5 Codex #6 (B11 partial): a start tag padded past the old 4,096-byte window, a ``>``
    inside an attribute value, or an ``r=`` hidden inside another attribute's value cannot hide a
    far cell from the extent scan (the sheet is stream-parsed, attributes exactly as a reader
    sees them)."""
    small = _xlsx_cells({"A1": "Plan", "B1": "Fee", "A2": "Growth", "B2": "1"})
    far = 16384 * 1048576
    pad = " " * 10240                                      # a 10 KB start tag
    padded = _patch_sheet(small, lambda x: x.replace('<c r="B2"', f'<c{pad}r="XFD1048576"'))
    assert len(padded) < 64 * 1024                         # still a small file
    assert X.xlsx_extent(padded) >= far
    assert _reason("xlsx", padded) == "xlsx_dimensions"
    row_padded = _patch_sheet(small, lambda x: x.replace('<row r="2"', f'<row{pad}r="1048576"'))
    assert X.xlsx_extent(row_padded) >= 1048576 * 2
    gt = _patch_sheet(small, lambda x: x.replace('<c r="B2"', '<c foo=">" r="XFD1048576"'))
    assert X.xlsx_extent(gt) >= far
    hidden = _patch_sheet(small, lambda x: x.replace('<c r="B2"',
                                                     '<c foo=" r=\'A1\'" r="XFD1048576"'))
    assert X.xlsx_extent(hidden) >= far
    assert X.xlsx_extent(small) == 4                       # the ordinary sheet is unchanged
