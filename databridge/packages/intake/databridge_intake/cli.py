"""Headless CLI for inspecting extraction — `python -m databridge_intake.cli`.

Useful for the Phase 1 demo and for debugging a document without booting the gateway. It prints
what the front desk would see plus, crucially, the two honesty columns: what the document did not
carry, and what the model proposed that the grounding wall threw away.

    python -m databridge_intake.cli list
    python -m databridge_intake.cli extract chit_02_everwell_voucher.txt
    python -m databridge_intake.cli extract chit_02_everwell_voucher.txt --show-spans
    python -m databridge_intake.cli extract-all
"""
from __future__ import annotations

import argparse
import sys

from .extract import extract_intake
from .gold import gold_harness
from .models import IntakeRecord
from .paths import chits_dir


def _load(name: str) -> tuple[str, str]:
    path = chits_dir() / name
    if not path.exists():
        available = ", ".join(sorted(p.name for p in chits_dir().iterdir()))
        raise SystemExit(f"no such fixture {name!r}.\nAvailable: {available}")
    return name, path.read_text(encoding="utf-8")


def _print_record(rec: IntakeRecord, text: str, *, show_spans: bool) -> None:
    g = lambda gv: gv.value if gv else None
    print(f"\n\033[1m{rec.document.document_id}\033[0m")
    print(f"  type          {rec.document_type.value}  "
          f"(confidence {rec.document_type.confidence})")
    print(f"  issuer        {g(rec.issuer.issuer_name)}  [{g(rec.issuer.issuer_code)}]")
    print(f"  subject       {g(rec.subject.claimed_name)}  "
          f"id={g(rec.subject.claimed_id_number) or '— not on document'}")
    print(f"  package       {g(rec.authorisation.package_code) or '— not stated'}")
    tests = [t.value for t in rec.authorisation.requested_tests]
    print(f"  tests ({len(tests)})     {', '.join(tests) if tests else '— none listed'}")
    if rec.authorisation.add_ons:
        print(f"  add-ons       {', '.join(a.value for a in rec.authorisation.add_ons)}")
    if rec.authorisation.appointment_datetime:
        print(f"  appointment   {g(rec.authorisation.appointment_datetime)}  "
              f"{g(rec.authorisation.appointment_location) or ''}")

    fields = rec.grounded_fields()
    print(f"\n  grounded {len(fields)} field(s), "
          f"{len(rec.unresolved)} not on document, {len(rec.refused)} refused")

    if show_spans:
        print("\n  \033[1mprovenance\033[0m (every value, and the source text it came from)")
        for path, gv in sorted(fields.items()):
            p = gv.pointer
            if p.kind == "doc_span":
                snippet = text[p.char_start:p.char_end].replace("\n", " ")
                print(f"    {path:42s} [{p.char_start:5d}:{p.char_end:5d}] {snippet!r}")
            else:
                print(f"    {path:42s} ({p.kind}) {str(gv.value)!r}")

    if rec.unresolved:
        print("\n  \033[2mnot on document:\033[0m " + ", ".join(sorted(rec.unresolved)))
    if rec.refused:
        # The interesting column: values the model proposed that are not in the source.
        print("\n  \033[31mREFUSED (not found in source — would have been a fabrication):\033[0m")
        for r in rec.refused:
            print(f"    - {r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="databridge_intake.cli",
                                     description="Inspect Walnut extraction.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list the available document fixtures")
    ex = sub.add_parser("extract", help="extract one fixture")
    ex.add_argument("fixture")
    ex.add_argument("--show-spans", action="store_true",
                    help="print every value with the source span it was grounded to")
    allp = sub.add_parser("extract-all", help="extract every fixture")
    allp.add_argument("--show-spans", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "list":
        for p in sorted(chits_dir().iterdir()):
            print(p.name)
        return 0

    harness = gold_harness()
    names = ([args.fixture] if args.command == "extract"
             else sorted(p.name for p in chits_dir().iterdir()))
    for name in names:
        doc_id, text = _load(name)
        rec = extract_intake(text, document_id=doc_id, harness=harness)
        _print_record(rec, text, show_spans=args.show_spans)
    return 0


if __name__ == "__main__":       # pragma: no cover
    sys.exit(main())
