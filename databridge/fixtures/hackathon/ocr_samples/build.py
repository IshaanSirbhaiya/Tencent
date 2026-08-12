"""Generate synthetic scan-like documents for trying the OCR upload on /intake.

Uses the repo's own fixture builder, so the ballot-box glyphs (U+2610 / U+2612) are rendered with a
font that actually has them. Rendering these in Helvetica turns both into 'n', which would make a
checkbox test pass against a document that had already lost the signal it exists to check.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]      # databridge/
sys.path.insert(0, str(ROOT / "packages/convert/tests/fixtures/chits"))
sys.path.insert(0, str(ROOT / "packages/core"))

import build  # noqa: E402

OUT = pathlib.Path(r"C:\DataBridge\DataBridge POC\databridge\fixtures\hackathon\ocr_samples")
OUT.mkdir(parents=True, exist_ok=True)

font = build.ballot_font_path()
if not font:
    print("No font with U+2610/U+2612 found — refusing to build a fixture whose checkboxes")
    print("would silently render as 'n'. Install DejaVu Sans and re-run.")
    raise SystemExit(1)
print(f"font: {font}\n")

CHITS = ROOT / "fixtures/hackathon/chits"
# One of each shape the pipeline treats differently:
#   a checkbox voucher  -> exercises R-13 (the fail-closed control)
#   a prose referral    -> exercises ordinary field extraction under OCR
WANTED = ["chit_05_bluepeak_wellness_voucher.txt", "chit_01_meridian_referral.txt"]

made = []
for name in WANTED:
    src = CHITS / name
    if not src.exists():
        print(f"  skip {name}: not found")
        continue
    text = src.read_text(encoding="utf-8")
    stem = src.stem

    pdf = build.text_to_pdf(text, font_path=font)
    p_pdf = OUT / f"{stem}__text_layer.pdf"
    p_pdf.write_bytes(pdf)
    made.append((p_pdf, "PDF with a real text layer - nothing is withheld"))

    png = build.pdf_page_to_png(pdf, scale=2.5)
    p_png = OUT / f"{stem}__scan.png"
    p_png.write_bytes(png)
    made.append((p_png, "rasterised page - a 'scan'. OCR runs; checkbox fields fail closed"))

    p_txt = OUT / f"{stem}__typed.txt"
    p_txt.write_text(text, encoding="utf-8")
    made.append((p_txt, "the same document as plain text - the control case"))

print("Built:\n")
for p, why in made:
    print(f"  {p.name:52s} {p.stat().st_size:>8,} bytes   {why}")
print(f"\nAll in: {OUT}")
