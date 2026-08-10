"""Build the Gauntlet Workbook + canaries (09_FINAL_PRD §23 EV-XL).

Every feature §14 must handle is present:
  * 2 visible sheets ("Sales", "Quarterly") + 1 hidden ("_internal")
  * merged cells: a header-context merge (Quarterly group row) AND a data-range merge (Sales)
  * a named range ("TaxRate") + an Excel Table / ListObject ("SalesTable")
  * formulas WITH cached values (total = qty*price) + a cross-sheet reference
  * real dates AND ambiguous numeric serials in a date-ish column
  * locale-formatted number text ("1.234,56"), leading-zero text ids ("00012345")
  * one #N/A, one #VALUE!, one #DIV/0! cell
  * multi-row headers on "Quarterly"
  * an ``epoch_anchor`` named range (real date as serial + ISO text) for the epoch check

openpyxl cannot COMPUTE formulas, so cached ``<v>`` values are injected into the saved
XML (``_inject_cached``). That is exactly what Excel would store on save.
"""
from __future__ import annotations

import datetime as _dt
import io
import re
import shutil
import zipfile
from typing import Any

import openpyxl
from openpyxl.utils.datetime import CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900, to_excel
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.table import Table, TableStyleInfo

# --- expected values, asserted by EV-XL (the oracle) ----------------------------
EXPECTED: dict[str, Any] = {
    "sheet_names_all": ["Sales", "Quarterly", "Ledger", "_internal"],
    "sheet_names_visible": ["Sales", "Quarterly", "Ledger"],
    "sales_header": ["id", "merchant", "amount_text", "qty", "price", "total",
                     "created", "status", "note"],
    # row 2 (first data row) typed values, cells A2..I2
    "sales_row2": {
        "id": "00012345",                # leading-zero text id (string!)
        "merchant": "Acme Corp",
        "amount_text": "1.234,56",       # EU-locale number kept as TEXT (§14.11)
        "qty": 3.0,
        "price": 19.99,
        "total": 59.97,                  # cached formula value (=qty*price)
        "created": _dt.datetime(2024, 3, 1, 9, 30, 0),
        "status": "active",
        "note": "first",
    },
    "sales_total_row3": 25.0,            # qty=5 * price=5.00
    "crosssheet_value": 59.97,          # Ledger!C2 = Sales!F2 cached
    "named_range_TaxRate": 0.20,
    "errors": {"N_A": "#N/A", "VALUE": "#VALUE!", "DIV0": "#DIV/0!"},  # Ledger D2/E2/F2
    "ledger_ship_serials": [45000.0, 45010.0, 45020.0],   # ambiguous serial col (§14.6)
    "epoch_anchor_date": _dt.date(2021, 6, 15),
    # sheets routed to human confirm (header conf < 0.9): the multi-row-header sheet.
    "confirm_sheets": ["Quarterly"],
    "auto_sheets": ["Sales", "Ledger"],
}


# --- xlsx XML helpers -----------------------------------------------------------
def _save_bytes(wb: openpyxl.Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _repack(data: bytes, edits: dict[str, Any]) -> bytes:
    """Rewrite named members inside the xlsx zip. ``edits`` maps member name → callable
    (old_text -> new_text) or raw bytes."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        members = {n: z.read(n) for n in z.namelist()}
    for name, edit in edits.items():
        if callable(edit):
            members[name] = edit(members[name].decode("utf8")).encode("utf8")
        else:
            members[name] = edit
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in members.items():
            z.writestr(name, body)
    return out.getvalue()


def _inject_cached(sheet_xml: str, cached: dict[str, tuple[str, bool]]) -> str:
    """Fill the empty ``<v></v>`` openpyxl writes after a formula with the cached value.
    ``cached`` maps cellref → (value_text, is_string)."""
    for ref, (val, is_str) in cached.items():
        attr = ' t="str"' if is_str else ""
        pat = re.compile(r'(<c r="%s")([^>]*)(><f>.*?</f>)<v\s*/?>(?:</v>)?'
                         % re.escape(ref))
        sheet_xml = pat.sub(
            lambda m, a=attr, v=val: f"{m.group(1)}{a}{m.group(3)}<v>{v}</v>", sheet_xml)
    return sheet_xml


def _make_error_cells(sheet_xml: str, errors: dict[str, str]) -> str:
    """Inject error cells (t="e") into a row. ``errors`` maps cellref → code (#N/A …).
    Appends a fresh row block to <sheetData> for refs not already present."""
    inject = "".join(f'<row r="{int(re.match(r"[A-Z]+(\\d+)", ref).group(1))}">' if False
                     else "" for ref in errors)  # rows added individually below
    # Build one <row> per distinct row number with its error cells.
    by_row: dict[int, list[str]] = {}
    for ref, code in errors.items():
        rownum = int(re.match(r"[A-Z]+(\d+)", ref).group(1))
        by_row.setdefault(rownum, []).append(f'<c r="{ref}" t="e"><v>{code}</v></c>')
    blocks = "".join(f'<row r="{rn}">{"".join(cs)}</row>'
                     for rn, cs in sorted(by_row.items()))
    return sheet_xml.replace("</sheetData>", blocks + "</sheetData>")


# --- the main Gauntlet ----------------------------------------------------------
def _build_base(epoch: int = CALENDAR_WINDOWS_1900) -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    wb.epoch = epoch

    # ---- Sheet "Sales" (visible) — a CLEAN single-header table → header auto-detects ----
    ws = wb.active
    ws.title = "Sales"
    for c, name in enumerate(EXPECTED["sales_header"], start=1):
        ws.cell(row=1, column=c, value=name)
    rows = [
        ("00012345", "Acme Corp", "1.234,56", 3, 19.99, _dt.datetime(2024, 3, 1, 9, 30), "active", "first"),
        ("00000077", "Beta LLC", "2.000,00", 5, 5.00, _dt.datetime(2024, 3, 2, 10, 0), "active", "second"),
        ("00009900", "Gamma Inc", "89,00", 2, 49.50, _dt.datetime(2024, 3, 3, 14, 15), "pending", "third"),
    ]
    for r, row in enumerate(rows, start=2):
        ws.cell(row=r, column=1, value=row[0])               # id (text, leading zeros)
        ws.cell(row=r, column=2, value=row[1])
        ws.cell(row=r, column=3, value=row[2])               # amount_text (locale text)
        ws.cell(row=r, column=4, value=row[3])               # qty
        ws.cell(row=r, column=5, value=row[4])               # price
        ws.cell(row=r, column=6, value=f"=D{r}*E{r}")        # total (formula; cached injected)
        ws.cell(row=r, column=7, value=row[5])               # created (real datetime)
        ws.cell(row=r, column=8, value=row[6])               # status
        ws.cell(row=r, column=9, value=row[7])               # note
    for r in range(2, 5):                                    # store ids + locale text as strings
        ws.cell(row=r, column=1).number_format = "@"
        ws.cell(row=r, column=3).number_format = "@"
    tbl = Table(displayName="SalesTable", ref="A1:I4")       # Excel Table / ListObject (§14.8)
    tbl.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    ws.add_table(tbl)

    # ---- Sheet "Quarterly" (visible) — MULTI-ROW header + header-context merge ----
    q = wb.create_sheet("Quarterly")
    q["B1"] = "Q1"
    q["D1"] = "Q2"
    q.merge_cells("B1:C1")                                   # header-context merge (fill_down)
    q.merge_cells("D1:E1")
    for c, name in enumerate(["region", "rev", "cost", "rev", "cost"], start=1):
        q.cell(row=2, column=c, value=name)
    for r, row in enumerate([("North", 100, 60, 120, 70), ("South", 90, 55, 110, 65)], start=3):
        for c, v in enumerate(row, start=1):
            q.cell(row=r, column=c, value=v)

    # ---- Sheet "Ledger" (visible) — the feature sheet (contiguous headers, auto-detects) ----
    lg = wb.create_sheet("Ledger")
    lg_header = ["ref_id", "ship_date", "xref_total", "err_na", "err_value", "err_div",
                 "batch", "tax_rate", "anchor_date", "anchor_iso"]
    for c, name in enumerate(lg_header, start=1):
        lg.cell(row=1, column=c, value=name)
    for i, (ref, serial) in enumerate(zip(["L-1", "L-2", "L-3"], [45000, 45010, 45020])):
        r = i + 2
        lg.cell(row=r, column=1, value=ref)                  # ref_id (text)
        lg.cell(row=r, column=2, value=serial)               # ship_date: ambiguous serial (§14.6)
        lg.cell(row=r, column=3, value=f"=Sales!F{r}")       # cross-sheet ref (cached injected)
    lg["G2"] = "BATCH-7"
    lg.merge_cells("G2:G3")                                  # data-range merge (anchor_only §14.9)
    lg["H2"] = 0.20                                          # TaxRate source
    anchor_date = EXPECTED["epoch_anchor_date"]
    lg["I2"] = _dt.datetime(anchor_date.year, anchor_date.month, anchor_date.day)  # date-formatted
    lg["J2"] = anchor_date.isoformat()                       # same date as ISO TEXT
    lg["J2"].number_format = "@"
    lg.column_dimensions["K"].hidden = True                  # a hidden column (§14.10)

    # ---- Sheet "_internal" (HIDDEN) ----
    h = wb.create_sheet("_internal")
    h["A1"] = "secret"
    h["A2"] = "stale-do-not-use"
    h.sheet_state = "hidden"

    # ---- named ranges (§14.7) ----
    wb.defined_names.add(DefinedName("TaxRate", attr_text="Ledger!$H$2"))
    wb.defined_names.add(DefinedName("epoch_anchor", attr_text="Ledger!$I$2:$J$2"))
    return wb


def _cached_and_errors(data: bytes) -> bytes:
    """Inject cached formula values (Sales totals + Ledger cross-sheet refs) and the three
    error cells (Ledger D2/E2/F2). Sheet members are resolved by title (robust to ordering)."""
    members = _sheet_members(data)
    sales_m, ledger_m = members["Sales"], members["Ledger"]
    data = _repack(data, {
        sales_m: lambda xml: _inject_cached(xml, {
            "F2": ("59.97", False), "F3": ("25", False), "F4": ("99", False)}),
    })
    data = _repack(data, {
        ledger_m: lambda xml: _inject_cached(xml, {
            "C2": ("59.97", False), "C3": ("25", False), "C4": ("99", False)}),
    })
    return _repack(data, {
        ledger_m: lambda xml: _make_error_cells(xml, {
            "D2": "#N/A", "E2": "#VALUE!", "F2": "#DIV/0!"}),
    })


def build_gauntlet() -> bytes:
    """The full Gauntlet Workbook, 1900 epoch (.xlsx)."""
    return _cached_and_errors(_save_bytes(_build_base(CALENDAR_WINDOWS_1900)))


def build_gauntlet_1904() -> bytes:
    """1904-epoch variant with documented epoch metadata; the anchor is CONSISTENT here
    (declared 1904, serial computed under 1904) → no flag."""
    return _cached_and_errors(_save_bytes(_build_base(CALENDAR_MAC_1904)))


def build_swapped_epoch() -> bytes:
    """The EV-XL canary: a workbook that DECLARES 1900 but whose anchor serial was computed
    for 1904 → calamine decodes it ~1462 days off the ISO anchor text → MUST be flagged.

    We author with 1904 (so openpyxl writes the 1904 serial for the anchor date), then flip
    the declared epoch to 1900 in workbook.xml WITHOUT touching the serial. That is exactly
    a Mac-authored file whose date1904 flag got stripped."""
    data = _cached_and_errors(_save_bytes(_build_base(CALENDAR_MAC_1904)))

    def _flip(xml: str) -> str:
        if 'date1904="1"' in xml or 'date1904="true"' in xml:
            return re.sub(r'date1904="(?:1|true)"', 'date1904="0"', xml)
        if "<workbookPr" in xml:
            return xml.replace("<workbookPr", '<workbookPr date1904="0"', 1)
        return xml.replace("<sheets>", '<workbookPr date1904="0"/><sheets>', 1)
    return _repack(data, {"xl/workbook.xml": _flip})


def _sheet_members(data: bytes) -> dict[str, str]:
    """Map sheet title → worksheet zip member, via workbook.xml (name→r:id) + its rels
    (r:id→target). Robust to openpyxl's sheetN.xml numbering not matching tab order."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        wbxml = z.read("xl/workbook.xml").decode("utf8")
        rels = z.read("xl/_rels/workbook.xml.rels").decode("utf8")
    name_to_rid: dict[str, str] = {}
    for tag in re.findall(r"<sheet\b[^>]*/?>", wbxml):
        nm = re.search(r'name="([^"]+)"', tag)
        rid = re.search(r'r:id="([^"]+)"', tag) or re.search(r'id="([^"]+)"', tag)
        if nm and rid:
            name_to_rid[nm.group(1)] = rid.group(1)
    rid_to_target: dict[str, str] = {}
    for tag in re.findall(r"<Relationship\b[^>]*/?>", rels):
        rid = re.search(r'Id="([^"]+)"', tag)
        tgt = re.search(r'Target="([^"]+)"', tag)
        if rid and tgt:
            rid_to_target[rid.group(1)] = tgt.group(1)
    out: dict[str, str] = {}
    for name, rid in name_to_rid.items():
        tgt = rid_to_target.get(rid, "").lstrip("/")
        if not tgt:
            continue
        out[name] = tgt if tgt.startswith("xl/") else f"xl/{tgt}"
    return out


# --- Gauntlet-lite (D2) ---------------------------------------------------------
def build_gauntlet_lite() -> bytes:
    """A SIMPLER irregular workbook for demo D2 (Excel→Parquet+JSON): 2 sheets — "Orders"
    has a merged-cell TITLE row above its real header (header-context merge); "Stock" has a
    hidden column. Used to exercise header-confirm + merged/hidden policy end to end."""
    wb = openpyxl.Workbook()
    # Orders: row1 merged title (A1:D1), row2 real header, rows 3-5 data
    o = wb.active
    o.title = "Orders"
    o["A1"] = "Q1 Orders Export"
    o.merge_cells("A1:D1")                                # merged title (header context)
    for c, name in enumerate(["order_id", "customer", "amount", "status"], start=1):
        o.cell(row=2, column=c, value=name)
    for r, row in enumerate([("00100", "Ana", 1299.50, "shipped"),
                             ("00101", "Ben", 4.50, "shipped"),
                             ("00102", "Cara", 1999.00, "pending")], start=3):
        o.cell(row=r, column=1, value=row[0]).number_format = "@"
        for c, v in enumerate(row[1:], start=2):
            o.cell(row=r, column=c, value=v)
    # Stock: clean header, a hidden column ("internal_cost")
    s = wb.create_sheet("Stock")
    for c, name in enumerate(["sku", "name", "qty", "internal_cost"], start=1):
        s.cell(row=1, column=c, value=name)
    for r, row in enumerate([("A-1", "Widget", 30, 4.20), ("A-2", "Gadget", 12, 9.90)], start=2):
        for c, v in enumerate(row, start=1):
            s.cell(row=r, column=c, value=v)
    s.column_dimensions["D"].hidden = True               # hide internal_cost
    return _save_bytes(wb)


# --- irregular KV workbook (C16: header-chaos → typed record) -------------------
# A finance export whose "headers" are a vertical KEY-VALUE block, not a table header.
# The §14.2 detector reports low confidence (the value column looks like more labels), and
# C16 routes the sheet to C15 extraction instead of a one-nonsense-row RowBatch.
KV_INVOICE_GOLD = {
    "invoice_number": "INV-2024-0042",
    "invoice_date": "2024-03-15",
    "bill_to": "Acme Industries",
    "subtotal": 1250.00,
    "tax": 100.00,
    "total_due": 1350.00,
}

# Label as the workbook displays it (col A) → typed cell value (col B). Labels normalise onto
# INVOICE_SCHEMA field names; values are TYPED (text id, real date, floats) so calamine returns
# the same shapes a real export would.
_KV_INVOICE_ROWS = [
    ("Invoice Number", "INV-2024-0042"),
    ("Invoice Date", _dt.datetime(2024, 3, 15)),
    ("Bill To", "Acme Industries"),
    ("Subtotal", 1250.00),
    ("Tax", 100.00),
    ("Total Due", 1350.00),
]


def build_kv_invoice() -> bytes:
    """A single-sheet workbook ('Invoice Summary') that is a vertical key-value block — the
    C16 irregular-Excel-as-KV fixture. No ListObject, no table header; just Label | value."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Invoice Summary"
    for r, (label, value) in enumerate(_KV_INVOICE_ROWS, start=1):
        ws.cell(row=r, column=1, value=label)
        cell = ws.cell(row=r, column=2, value=value)
        if label == "Invoice Number":
            cell.number_format = "@"            # keep the id as text (leading-zero safe)
    return _save_bytes(wb)


# --- macro workbook (§14.17) ----------------------------------------------------
# A recognizable sentinel as the 'macro payload' — the test asserts it NEVER leaks into IR.
MACRO_PAYLOAD = b"FAKE_VBA_MACRO_PAYLOAD\x00Sub Hello()\x00MsgBox 1\x00End Sub"


def build_macro_xlsm() -> bytes:
    """A minimal .xlsm: a tiny workbook + a vbaProject.bin part (a trivial 'macro').
    has_macros must read True; the payload must never appear in any IR/output."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Macro"
    ws["A1"] = "id"
    ws["B1"] = "val"
    ws["A2"] = 1
    ws["B2"] = "hello"
    data = _save_bytes(wb)
    # add the VBA part + declare the macro content type
    def _ct(xml: str) -> str:
        part = ('<Override PartName="/xl/vbaProject.bin" '
                'ContentType="application/vnd.ms-office.vbaProject"/>')
        wb_ct = ('application/vnd.ms-excel.sheet.macroEnabled.main+xml')
        xml = xml.replace(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml',
            wb_ct)
        return xml.replace("</Types>", part + "</Types>")
    return _repack(data, {
        "[Content_Types].xml": _ct,
        "xl/vbaProject.bin": MACRO_PAYLOAD,
    })


# --- encrypted workbook (§14.18) ------------------------------------------------
def build_encrypted(password: str = "secret") -> bytes:
    """A password-protected .xlsx (OLE2 + EncryptedPackage). The reader must REFUSE with
    ENCRYPTED_WORKBOOK. Built with msoffcrypto if it supports encryption; otherwise a
    minimal OLE2 container carrying the EncryptionInfo/EncryptedPackage streams that
    msoffcrypto.is_encrypted() recognises."""
    plain = build_gauntlet()
    try:
        from msoffcrypto.format.ooxml import OOXMLFile
        off = OOXMLFile(io.BytesIO(plain))
        out = io.BytesIO()
        off.encrypt(password, out)                # real ECMA-376 agile encryption
        return out.getvalue()
    except Exception:                              # pragma: no cover - fallback path
        return _minimal_encrypted_ole()


def _minimal_encrypted_ole() -> bytes:
    """Fallback: a valid OLE2/CFB file with EncryptionInfo + EncryptedPackage streams so
    encryption detection trips. Uses olefile-compatible CFB layout via a tiny hand-roll."""
    try:
        import olefile  # noqa: F401
    except ImportError:                            # pragma: no cover
        pass
    # Build a CFB with two streams using the `compoundfiles`-free approach: write via
    # a known-good template. Simplest robust path: construct with `olefile` is read-only,
    # so we craft the minimal header + directory by hand.
    return _handmade_cfb({
        "EncryptionInfo": b"\x04\x00\x04\x00" + b"\x40\x00\x00\x00" + b"AGILE-ENC" + b"\x00" * 64,
        "EncryptedPackage": b"\x00" * 8 + b"ENCRYPTED-OOXML-PAYLOAD" + b"\x00" * 64,
    })


def _handmade_cfb(streams: dict[str, bytes]) -> bytes:
    """Minimal CFB/OLE2 container (512-byte sectors) holding small streams in the mini-FAT.
    Enough that the magic + directory names ('EncryptedPackage') are present for detection."""
    import struct
    SECT = 512
    header = bytearray(SECT)
    header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"      # OLE2 magic
    struct.pack_into("<H", header, 24, 0x003E)             # minor version
    struct.pack_into("<H", header, 26, 0x0003)             # major version (512-byte sectors)
    struct.pack_into("<H", header, 28, 0xFFFE)             # little-endian byte order
    struct.pack_into("<H", header, 30, 9)                  # sector shift (2^9 = 512)
    struct.pack_into("<H", header, 32, 6)                  # mini sector shift (2^6 = 64)
    struct.pack_into("<I", header, 44, 1)                  # number of FAT sectors
    struct.pack_into("<I", header, 48, 1)                  # first directory sector
    struct.pack_into("<I", header, 56, 0x1000)             # mini stream cutoff
    struct.pack_into("<I", header, 60, 0xFFFFFFFE)         # first mini-FAT (none → END)
    struct.pack_into("<I", header, 64, 0xFFFFFFFE)         # first DIFAT (none)
    # FAT sector (sector 0): mark dir sector (1) and FAT sector (0) used.
    fat = bytearray(b"\xff\xfe\xff\xff" * (SECT // 4))     # all FREESECT/ENDOFCHAIN
    struct.pack_into("<I", fat, 0, 0xFFFFFFFD)             # sector 0 = FATSECT
    struct.pack_into("<I", fat, 4, 0xFFFFFFFE)             # sector 1 = ENDOFCHAIN (dir)
    struct.pack_into("<I", header, 48, 1)
    # directory sector (sector 1): root + the named streams (names only; sizes 0 to keep it
    # parse-light — detection only needs the UTF-16 names present in the bytes).
    directory = bytearray(SECT)

    def dir_entry(offset: int, name: str, obj_type: int) -> None:
        nb = name.encode("utf-16-le") + b"\x00\x00"
        directory[offset:offset + len(nb)] = nb
        struct.pack_into("<H", directory, offset + 64, len(nb))
        directory[offset + 66] = obj_type                 # 5=root,2=stream
        directory[offset + 67] = 1                        # colour=black
        struct.pack_into("<I", directory, offset + 68, 0xFFFFFFFF)  # left
        struct.pack_into("<I", directory, offset + 72, 0xFFFFFFFF)  # right
        struct.pack_into("<I", directory, offset + 76, 0xFFFFFFFF)  # child
    dir_entry(0, "Root Entry", 5)
    dir_entry(128, "EncryptionInfo", 2)
    dir_entry(256, "EncryptedPackage", 2)
    return bytes(header + fat + directory)


# --- CLI: materialise files for demos -------------------------------------------
def _write_all(dirpath: str) -> None:
    import os
    mapping = {
        "gauntlet.xlsx": build_gauntlet(),
        "gauntlet_1904.xlsx": build_gauntlet_1904(),
        "swapped_epoch.xlsx": build_swapped_epoch(),
        "macro.xlsm": build_macro_xlsm(),
        "encrypted.xlsx": build_encrypted(),
    }
    for name, data in mapping.items():
        with open(os.path.join(dirpath, name), "wb") as f:
            f.write(data)
        print(f"wrote {name} ({len(data)} bytes)")


if __name__ == "__main__":
    import os
    _write_all(os.path.dirname(os.path.abspath(__file__)))
