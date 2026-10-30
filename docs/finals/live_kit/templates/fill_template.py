"""Turn a judge's own sentence into a demo file in seconds (no dependencies).

    python docs/finals/live_kit/templates/fill_template.py md "The audit committee meets on 9 Dec 2026." --title "Audit calendar" --owner "Hana Yusuf"
    python docs/finals/live_kit/templates/fill_template.py txt "The standby VPN token expires Friday." --person-a "Priya Raman" --person-b "Jordan Doe"
    python docs/finals/live_kit/templates/fill_template.py csv "Renewal needs sign-off by 30 Nov." --item "Vendor renewal"

Writes to templates/out/ (created on demand) and prints the path plus the proof phrase to use
with check_absent. Empty fields fall back to harmless defaults. Synthetic names only.
"""
import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KINDS = {"md": "template_note.md", "txt": "template_whatsapp.txt", "csv": "template_tracker.csv"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=KINDS)
    ap.add_argument("sentence")
    ap.add_argument("--title", default="Team note")
    ap.add_argument("--team", default="Operations")
    ap.add_argument("--owner", default="Hana Yusuf")
    ap.add_argument("--date", default="")
    ap.add_argument("--what", default="See the note above")
    ap.add_argument("--when", default="As stated above")
    ap.add_argument("--who", default="The owner above")
    ap.add_argument("--person-a", default="Priya Raman")
    ap.add_argument("--person-b", default="Jordan Doe")
    ap.add_argument("--group", default="Ops chat")
    ap.add_argument("--item", default="Follow-up")
    ap.add_argument("--status", default="Open")
    ap.add_argument("--due", default="")
    ap.add_argument("--name", default="")
    a = ap.parse_args()
    date = a.date or ("30/10/2026" if a.kind == "txt" else "October 2026")
    vals = {"TITLE": a.title, "TEAM": a.team, "OWNER": a.owner, "DATE": date, "SENTENCE": a.sentence,
            "WHAT": a.what, "WHEN": a.when, "WHO": a.who, "PERSON_A": a.person_a,
            "PERSON_B": a.person_b, "GROUP": a.group, "ITEM": a.item, "STATUS": a.status,
            "DUE": a.due or "TBC"}
    text = (HERE / KINDS[a.kind]).read_text(encoding="utf-8")
    for k, v in vals.items():
        text = text.replace("{{" + k + "}}", v.replace(",", ";") if a.kind == "csv" else v)
    left = re.findall(r"\{\{\w+\}\}", text)
    if left:
        print("unfilled:", left)
        return 1
    out = HERE / "out"
    out.mkdir(exist_ok=True)
    name = a.name or {"md": re.sub(r"[^\w -]", "", a.title) + ".md", "txt": f"WhatsApp Chat with {a.group}.txt", "csv": "tracker.csv"}[a.kind]
    p = out / name
    p.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {p} ({p.stat().st_size} bytes)")
    print("proof phrase (use the first 4-6 words of the judge's sentence):", " ".join(a.sentence.split()[:5]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
