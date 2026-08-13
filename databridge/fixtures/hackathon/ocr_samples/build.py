"""Generate synthetic scan-like documents for trying the OCR upload on /intake.

Three artefacts per chit, and the set is the point: the same document as a **typed .txt** (the
control — nothing is lost), as a **PDF with a real text layer** (extraction reads the text, no OCR
runs), and as a **rasterised page PNG** (a "scan" — OCR runs, and every value that survives carries
an ``ocr_bbox`` pointer into the page region it was read from). Uploading the three in turn is what
makes the difference between "we read it" and "we OCR'd it and can show you where" visible.

Uses the repo's own fixture builder (``packages/convert/tests/fixtures/chits/build.py``), so the
ballot-box glyphs (U+2610 / U+2612) are rendered with a font that actually has them. Rendering
these in Helvetica turns both into 'n', which would make a checkbox test pass against a document
that had already lost the signal it exists to check.

**Which chits, and why not all ten.** This is a set of OCR *shapes*, not a mirror of the corpus.
Each entry in ``WANTED`` names the path through /intake it exercises, and that sentence is the
standard for adding another — a near-duplicate of a shape already here costs a quarter-megabyte of
raster to prove nothing new. ``NOT_INCLUDED`` records the omissions and why, so "is chit 7 missing
or excluded?" has an answer in the file rather than in someone's memory.

Run it from anywhere:  ``python databridge/fixtures/hackathon/ocr_samples/build.py``
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]                                  # databridge/
sys.path.insert(0, str(ROOT / "packages/convert/tests/fixtures/chits"))
sys.path.insert(0, str(ROOT / "packages/core"))

import build  # noqa: E402

OUT = HERE
CHITS = ROOT / "fixtures/hackathon/chits"

#: chit filename → the path through /intake this shape exercises. The reason is not decoration:
#: it is the entry's justification for costing the repo a raster, and the next person adding one
#: has to be able to write the same sentence.
WANTED = {
    "chit_01_meridian_referral.txt":
        "prose referral letter — ordinary field extraction under OCR",
    "chit_03_everwell_group_invitation.txt":
        "AMBIGUOUS subject — the registry holds two candidates and the counter must choose "
        "(CLAUDE.md constraint 1), now with OCR noise in the name",
    "chit_05_bluepeak_wellness_voucher.txt":
        "checkbox voucher — R-13, the fail-closed control: an unticked option must not be "
        "confirmable after rasterisation",
    "chit_09_coralharbour_hr_email.eml":
        "a printed email — headers, quoting and no letterhead at all",
    "chit_10_northstar_second_referral.txt":
        "the second referral for a patient another chit already covers — the conflict path, "
        "reached through OCR",
}

#: Deliberately absent, and why. Written down because a set that is quietly smaller than the corpus
#: is indistinguishable from one that lost entries to a bug.
NOT_INCLUDED = {
    "chit_02_everwell_voucher.txt": "another insurer voucher — chit 05 already covers the shape",
    "chit_04_bluepeak_underwriting.txt": "another insurer letter — chit 01 covers the shape",
    "chit_06_northstar_further_req.txt": "another insurer letter — chits 01 and 10 cover it",
    "chit_07_mol_authorisation_pee226.txt":
        "the government TPA form runs to TWO rendered pages, and a scan is one page. Included, it "
        "would ship a PNG holding half the document — every extraction against it would look like "
        "a parser bug. The single-page guard in `main()` is what found this; if the form is ever "
        "wanted as a scan, the builder needs a page-per-PNG shape first, not a smaller font.",
    "chit_08_mol_authorisation_vital.txt":
        "the second MOL authorisation form — same shape as chit 07, and the same page problem",
}


def main() -> int:
    font = build.ballot_font_path()
    if not font:
        print("No font with U+2610/U+2612 found — refusing to build a fixture whose checkboxes")
        print("would silently render as 'n'. Install DejaVu Sans and re-run.")
        return 1
    print(f"font: {font}\n")

    OUT.mkdir(parents=True, exist_ok=True)
    made, failures = [], []
    for name, why in WANTED.items():
        src = CHITS / name
        if not src.exists():
            failures.append(f"{name}: not found under {CHITS}")
            continue
        text = src.read_text(encoding="utf-8")
        stem = src.stem

        pdf = build.text_to_pdf(text, font_path=font)
        pages = _page_count(pdf)
        if pages != 1:
            # `pdf_page_to_png` rasterises ONE page. A two-page chit would silently produce a
            # "scan" missing its second half — a fixture that quietly holds less than the document
            # it claims to be is worse than no fixture, because every extraction against it looks
            # like a parser bug.
            failures.append(f"{name}: renders to {pages} pages; the scan would be truncated")
            continue
        if not _ballots_survived(text, pdf):
            failures.append(f"{name}: ballot glyphs did not survive into the PDF text layer")
            continue

        p_pdf = OUT / f"{stem}__text_layer.pdf"
        p_pdf.write_bytes(pdf)
        made.append((p_pdf, "PDF with a real text layer - nothing is withheld"))

        png = build.pdf_page_to_png(pdf, scale=2.5)
        p_png = OUT / f"{stem}__scan.png"
        p_png.write_bytes(png)
        made.append((p_png, "rasterised page - a 'scan'. OCR runs; every value gets an ocr_bbox"))

        p_txt = OUT / f"{stem}__typed.txt"
        p_txt.write_text(text, encoding="utf-8")
        made.append((p_txt, "the same document as plain text - the control case"))
        print(f"  {stem}\n      {why}")

    print("\nBuilt:\n")
    for p, why in made:
        print(f"  {p.name:52s} {p.stat().st_size:>8,} bytes   {why}")
    if failures:
        print("\nNOT built:\n")
        for f in failures:
            print(f"  {f}")
    if NOT_INCLUDED:
        print("\nDeliberately not included:\n")
        for name, why in NOT_INCLUDED.items():
            print(f"  {name}\n      {why}")
    print(f"\nAll in: {OUT}")
    return 1 if failures else 0


def _page_count(pdf_bytes: bytes) -> int:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        return len(doc)
    finally:
        doc.close()


def _ballots_survived(text: str, pdf_bytes: bytes) -> bool:
    """For a chit that PRINTS ballot boxes, both glyphs must still be in the PDF's text layer.

    The font check above proves the font *can* draw them; this proves this document *kept* them.
    A voucher whose ☒ became 'n' still renders, still extracts, and quietly turns the fail-closed
    checkbox control into a test of nothing — the failure mode the fixture exists to rule out, so
    it is checked on the artefact rather than assumed from the font.
    """
    import pypdfium2 as pdfium

    wanted = {g for g in (build.BALLOT_TICKED, build.BALLOT_UNTICKED) if g in text}
    if not wanted:
        return True
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        rendered = "".join(page.get_textpage().get_text_bounded() for page in doc)
    finally:
        doc.close()
    return all(g in rendered for g in wanted)


if __name__ == "__main__":
    raise SystemExit(main())
