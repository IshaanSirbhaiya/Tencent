"""EF3 — the DEFAULT screen (`defaults/screen.yaml`) carries the R-021 fixes already made to
`packs/workspace/screen.yaml`: case-insensitive IBANs, masked placeholders are never credentials,
hyphen/dot secret tokens block, and quoted all-word values go to REVIEW, not a decisive block.

Run through the production engine over the bundled defaults alone (no pack screen). IBANs are
computed (ISO 13616 check digits), never written down.
"""
from __future__ import annotations

import pytest

from databridge_mcp_ingest import screen as engine


@pytest.fixture(scope="module")
def cfg():
    c = engine.load_screen(packs=[])
    assert c.pack == "defaults" if hasattr(c, "pack") else True
    return c


def _iban(country: str, bban: str) -> str:
    check = 98 - int("".join(str(int(ch, 36)) for ch in bban + country + "00")) % 97
    return f"{country}{check:02d}{bban}"


def _grouped(iban: str) -> str:
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


@pytest.mark.parametrize("bban_len", [11, 12, 26, 30])
@pytest.mark.parametrize("case", ["lower", "mixed"])
@pytest.mark.parametrize("spaced", [False, True])
def test_a_valid_iban_is_masked_whatever_its_case(cfg, bban_len, case, spaced):
    iban = _iban("GB", ("WEST12345698765432" + "0" * 30)[:bban_len])
    assert engine.mod97_ok(iban)
    written = iban.lower() if case == "lower" else "".join(
        ch.lower() if i % 2 else ch for i, ch in enumerate(iban))
    written = _grouped(written) if spaced else written
    result = engine.screen_text(f"Pay to {written} by Friday.", cfg)
    assert written not in result.masked_text
    assert any(r["rule"].startswith("iban") for r in result.redactions)


@pytest.mark.parametrize("after", [" by Friday.", " 12 items", " is due", "."])
def test_the_next_word_is_never_swallowed_as_a_tail(cfg, after):
    for iban in (_iban("NO", "86011117947"), _iban("ES", "21000418450200051332")):
        for written in (iban.lower(), _grouped(iban).lower(), _grouped(iban)):
            assert written not in engine.screen_text(f"Pay to {written}{after}", cfg).masked_text


def test_a_mod97_failure_is_left_alone(cfg):
    iban = _iban("GB", "WEST12345698765432")
    broken = iban[:2] + f"{(int(iban[2:4]) + 1) % 100:02d}" + iban[4:]
    assert not engine.mod97_ok(broken)
    assert broken.lower() in engine.screen_text(f"Part {broken.lower()} ships.", cfg).masked_text


SECRETS = ["api_key=sk-proj-abcdefghijklmnop", "api_key: sk.proj.abcdefghijklmnop",
           "access_token = ghs-lowercase-token-value", "Password: Summer2026!",
           "PASSWORD=CorrectHorseBattery", 'password: "Summer2026!"',
           "client_secret: 'q8Zr-Lm2p-Vx9t'"]
PLACEHOLDERS = ['password: "********"', "password: '********'", "password: ********",
                'password: "••••••••"', 'password: "xxxxxxxx"', 'password: "${DB_PASSWORD}"',
                "password: ${DB_PASSWORD}", 'password: "<your password here>"',
                'password: "[REDACTED]"', "password: %(db_password)s", 'password: "ab******"',
                "Password: minimum-length requirements apply",
                "API key: rotated quarterly by the platform team"]
AMBIGUOUS_QUOTED = ['Password: "minimum-length requirements apply"',
                    'password: "correct horse battery staple"',
                    "client_secret: 'plain words but quoted'"]


def _decisive(result) -> set[str]:
    return {f["rule"] for f in result.fired if f["kind"] == "decisive"}


@pytest.mark.parametrize("text", SECRETS)
def test_a_secret_shaped_value_blocks(cfg, text):
    result = engine.screen_text(text, cfg)
    assert result.decision == "block" and _decisive(result), result.fired


@pytest.mark.parametrize("text", PLACEHOLDERS)
def test_placeholders_and_prose_are_never_credentials(cfg, text):
    result = engine.screen_text(text, cfg)
    assert not any(f["rule"].startswith("credential") for f in result.fired), (text, result.fired)
    assert result.decision == "allow", (text, result.rationale)


@pytest.mark.parametrize("text", AMBIGUOUS_QUOTED)
def test_a_quoted_all_word_value_goes_to_review(cfg, text):
    result = engine.screen_text(text, cfg)
    assert not _decisive(result), result.fired
    assert result.decision == "review", result.rationale
