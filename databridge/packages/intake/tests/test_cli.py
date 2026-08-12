"""Phase 1 — the headless CLI used for the extraction demo."""
from __future__ import annotations

import pytest

from databridge_intake.cli import main


def test_list_prints_every_fixture(capsys):
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "chit_01_meridian_referral.txt" in out
    assert "chit_09_coralharbour_hr_email.eml" in out


def test_extract_prints_the_front_desk_summary(capsys):
    assert main(["extract", "chit_02_everwell_voucher.txt"]) == 0
    out = capsys.readouterr().out
    assert "health_checkup_voucher" in out
    assert "EVWPA" in out and "Lim Arjun" in out
    assert "T2221854H" in out


def test_show_spans_prints_the_source_text_behind_each_value(capsys):
    assert main(["extract", "chit_02_everwell_voucher.txt", "--show-spans"]) == 0
    out = capsys.readouterr().out
    assert "provenance" in out
    assert "issuer.issuer_code" in out
    assert "'EVWPA'" in out          # the actual sliced source text, not just the value


def test_a_document_with_no_nric_says_so_rather_than_leaving_it_blank(capsys):
    assert main(["extract", "chit_05_bluepeak_wellness_voucher.txt"]) == 0
    out = capsys.readouterr().out
    assert "not on document" in out


def test_extract_all_covers_every_fixture(capsys):
    assert main(["extract-all"]) == 0
    out = capsys.readouterr().out
    for n in ("chit_01", "chit_05", "chit_09"):
        assert n in out


def test_unknown_fixture_lists_the_available_ones():
    with pytest.raises(SystemExit) as exc:
        main(["extract", "nope.txt"])
    assert "chit_01_meridian_referral.txt" in str(exc.value)
