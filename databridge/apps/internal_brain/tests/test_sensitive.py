"""databridge_ib.sensitive: ingest-time masking of card numbers, card security codes, NRIC/FIN and
IBANs (rules in packs/enterprise/sensitive.yaml). Expected masked forms are DERIVED from the rule
config (keep_head / fill / keep_tail) and from the world's SENS facts, not written down twice."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from databridge_ib import sensitive as S

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "fixtures" / "enterprise" / "manifest.json"
RULES = S.default_rules()
BY_KIND = {r.kind: r for r in RULES}

# published test card numbers (never real cards)
TEST_PANS = ["4111 1111 1111 1111", "4111-1111-1111-1111", "4111111111111111",
             "5555 5555 5555 4444", "5105 1051 0510 5100", "378282246310005", "3530111333300000",
             "6011111111111117", "2223003122003222", "4222222222222"]


def _compact(v: str) -> str:
    return re.sub(r"[ -]", "", v)


def _expect(kind: str, raw: str) -> str:
    r = BY_KIND[kind]
    return r.masked(_compact(raw) if r.separators else raw)


# --------------------------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------------------------
def test_yaml_rules_equal_the_builtin_defaults():
    assert S.DEFAULT_PATH.is_file()
    assert S.load_rules() == S.compile_rules(S.DEFAULT_RULES)
    assert {"pan", "iban", "card_security_code", "sg_nric"} <= set(BY_KIND)


def test_missing_file_falls_back_to_defaults(tmp_path):
    assert S.load_rules(tmp_path / "nope.yaml") == S.compile_rules(S.DEFAULT_RULES)


@pytest.mark.parametrize("body", [
    "version: 1\nrules:\n  - {id: x, kind: x, pattern: 'a', mask: {fill: '*'}, colour: red}\n",
    "version: 2\nrules: []\n",
    "version: 1\nrules:\n  - {id: x, kind: x, pattern: 'a', validate: crc, mask: {fill: '*'}}\n",
    "version: 1\nrules:\n  - {id: x, kind: x, pattern: 'a', mask: {fill: 'X'}}\n",
])
def test_malformed_config_fails_loudly(tmp_path, body):
    p = tmp_path / "s.yaml"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        S.load_rules(p)


def test_display_formats_match_the_stated_standards():
    pan = BY_KIND["pan"]
    assert (pan.keep_head, pan.keep_tail) == (6, 4)              # PCI DSS 3.4.1: BIN + last 4 max
    nric = BY_KIND["sg_nric"]
    assert (nric.keep_head, nric.keep_tail) == (0, 4)            # PDPC: last 3 digits + letter
    iban = BY_KIND["iban"]
    assert (iban.keep_head, iban.keep_tail) == (4, 4)
    assert not any(ch.isalnum() for r in RULES for ch in r.fill)


# --------------------------------------------------------------------------------------------
# checksums
# --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("pan", TEST_PANS)
def test_luhn_accepts_published_test_pans(pan):
    assert S.luhn_valid(_compact(pan))


@pytest.mark.parametrize("digits", ["4111111111111112", "1234567812345678", "5555555555554445",
                                    "0", "", "41a1"])
def test_luhn_rejects(digits):
    assert not S.luhn_valid(digits)


def test_mod97_and_nric_checksums():
    assert S.mod97_valid("GB82WEST12345698765432")
    assert S.mod97_valid("DE89370400440532013000")
    assert not S.mod97_valid("GB83WEST12345698765432")
    assert S.nric_valid("S1234567D") and not S.nric_valid("S1234567A")
    assert not S.nric_valid("X1234567D")


# --------------------------------------------------------------------------------------------
# masking: positives
# --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("pan", TEST_PANS)
def test_pan_is_masked_to_first6_last4(pan):
    out, f = S.mask(f"card {pan} on file")
    want = _expect("pan", pan)
    assert out == f"card {want} on file"
    c = _compact(pan)
    assert want.startswith(c[:6]) and want.endswith(c[-4:]) and c not in out
    assert [x["kind"] for x in f] == ["pan"]


def test_pan_next_to_an_expiry_or_a_short_number_masks_only_the_pan():
    out, f = S.mask("4111 1111 1111 1111 12/28")
    assert out == f"{_expect('pan', '4111111111111111')} 12/28"
    out, _ = S.mask("ref 12 4111 1111 1111 1111 done")
    assert out == f"ref 12 {_expect('pan', '4111111111111111')} done"


def test_cvv_iban_nric():
    out, f = S.mask("CVV: 123")
    assert out == "CVV: " + BY_KIND["card_security_code"].fill
    out, _ = S.mask("cvc2 9999 and CSC=482")
    assert "9999" not in out and "482" not in out
    out, f = S.mask("Payout IBAN GB82 WEST 1234 5698 7654 32.")
    assert out == f"Payout IBAN {_expect('iban', 'GB82WEST12345698765432')}."
    assert [x["kind"] for x in f] == ["iban"]                    # its digits are not also a "PAN"
    out, f = S.mask("NRIC S1234567D, FIN G1234567X.")
    assert out == f"NRIC {_expect('sg_nric', 'S1234567D')}, FIN {_expect('sg_nric', 'G1234567X')}."


def test_findings_point_at_the_original_text_and_carry_no_raw_value():
    text = "Card 4111 1111 1111 1111, CVV: 123; NRIC S1234567D; IBAN GB82 WEST 1234 5698 7654 32"
    out, findings = S.mask(text)
    assert [f["kind"] for f in findings] == ["pan", "card_security_code", "sg_nric", "iban"]
    for f in findings:
        assert set(f) == {"rule", "kind", "start", "end", "masked"}
        raw = text[f["start"]:f["end"]]
        assert raw and raw not in json.dumps(findings, ensure_ascii=False)
        assert f["masked"] in out
    assert findings == sorted(findings, key=lambda f: f["start"])


# --------------------------------------------------------------------------------------------
# masking: negatives (no false positives) and idempotence
# --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "order 20261013000123 shipped",                 # order id: IIN prefix "20" is not a card
    "order ORD-4111111111111111 shipped",           # glued to an id prefix
    "ticket 4111111111111112",                      # Luhn fails
    "slack ts 1728370000.000100 and 1791256800.003156",
    "epoch ms 1728370000123",
    "call +65 6123 4567 or +1 415 555 2671",
    "invoice 2026-10-13 total SGD 1,240.00 qty 3",
    "SKU 123456789012345678901234",                  # 24 digits: too long, never a card
    "PAY-236 and AUTH-88 and STRO-SYN-2026-0912",
    "GB83 WEST 1234 5698 7654 32",                  # IBAN-shaped, mod-97 fails
    "S12345678 and T123456 and s1234567d",           # NRIC-shaped but not the format
    "CNRY-3f9a1c and v7 and Q3",
])
def test_no_false_positives(text):
    assert S.mask(text) == (text, [])


@pytest.mark.parametrize("text", [
    "Card 4111 1111 1111 1111, CVV: 123, NRIC S1234567D, IBAN GB82 WEST 1234 5698 7654 32.",
    "5555-5555-5555-4444 / 378282246310005 / CSC 4821",
    "nothing sensitive here",
    "",
])
def test_idempotent(text):
    once, _ = S.mask(text)
    twice, f2 = S.mask(once)
    assert twice == once and f2 == []


def test_mask_is_pure_and_deterministic():
    t = "Card 4111 1111 1111 1111 and NRIC S1234567D"
    assert S.mask(t) == S.mask(t)


def test_mask_doc_masks_title_and_text_and_keeps_clean_docs_identical():
    from datetime import datetime, timezone
    from databridge_ib.models import SourceDoc
    now = datetime(2026, 10, 13, tzinfo=timezone.utc)
    base = dict(doc_id="jira:PAY-1", platform="jira", kind="issue", container="PAY",
                container_name="Payments", version="1", created_at=now, updated_at=now,
                deep_link="https://x/PAY-1")
    clean = SourceDoc(title="Refund flow", text="no secrets", **base)
    assert S.mask_doc(clean) is clean
    dirty = SourceDoc(title="Card 4111111111111111", text="NRIC S1234567D", **base)
    m = S.mask_doc(dirty)
    assert "4111111111111111" not in m.title and "S1234567D" not in m.text
    assert m.doc_id == dirty.doc_id and m.version == dirty.version


# --------------------------------------------------------------------------------------------
# the world's planted SENS facts (derived from the manifest)
# --------------------------------------------------------------------------------------------
def _sens_facts():
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return [f for f in m["facts"] if f.get("raw")]


def test_world_sens_facts_mask_to_their_recorded_form():
    facts = _sens_facts()
    assert {f["sensitive_kind"] for f in facts} >= {"pan", "sg_nric"}        # non-vacuous
    for f in facts:
        out, findings = S.mask(f"x {f['raw']} y")
        assert out == f"x {f['masked']} y", f["id"]
        assert f["raw"] not in out and [x["kind"] for x in findings] == [f["sensitive_kind"]]
