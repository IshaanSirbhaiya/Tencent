"""Generate the contract_001.pdf brain-demo fixture (minimal valid one-page PDF, stdlib only).

One-off generator kept in the repo so the fixture is reproducible. Run:
    python scripts/gen_brain_pdf.py
"""
import os
import sys

LINES = (
    "MASTER SERVICES AGREEMENT - RENEWAL",
    "",
    "This renewal of the Master Services Agreement is entered into",
    "between Acme Corp and BigClient. On behalf of Acme Corp, the",
    "agreement is managed by Priya Patel. BigClient is represented",
    "by Robert Vance, General Counsel at BigClient.",
    "",
    "The parties agree to Sign BigClient MSA renewal for a further",
    "term, preserving the existing service levels and the agreed",
    "contract value. Maria Garcia, the account contact, will",
    "coordinate onboarding for the renewed term.",
    "",
    "This agreement is effective upon signature by Priya Patel",
    "and Robert Vance.",
)
BACK = chr(92)  # backslash, without writing a literal one


def esc(s: str) -> str:
    return s.replace(BACK, BACK + BACK).replace("(", BACK + "(").replace(")", BACK + ")")


def build() -> bytes:
    content = "BT /F1 11 Tf 50 740 Td 15 TL " + "".join(f"({esc(l)}) Tj T* " for l in LINES) + "ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
    ]
    pdf = "%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(pdf.encode("latin-1")))
        pdf += f"{i} 0 obj\n{body}\nendobj\n"
    xref_pos = len(pdf.encode("latin-1"))
    pdf += f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n"
    for off in offsets:
        pdf += f"{off:010d} 00000 n \n"
    pdf += f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n"
    return pdf.encode("latin-1")


def main() -> None:
    data = build()
    for path in sys.argv[1:]:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        print("wrote", len(data), "bytes ->", path)


if __name__ == "__main__":
    main()
