"""Codex round 2 on `packs/workspace` (wiki/40-reviews/R-021): each REAL finding, red first.

The screen cases run through the PRODUCTION screen engine (`databridge_mcp_ingest.screen`), once
over the pack's own file (the pack must stand on its own) and once as a deployment composes it
(`load_screen`, which adds the always-on default credential rules). Expectations are derived from
the files — the connection manifest, the fixture SQL, the demo corpus manifest, the ontology — never
written down twice.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from databridge_graph.packs import DEFAULT_PACKS_ROOT
from databridge_mcp_ingest import screen as engine

PACK_DIR = DEFAULT_PACKS_ROOT / "workspace"
SCREEN = PACK_DIR / "screen.yaml"
GOLDEN = PACK_DIR / "evals" / "agent_contract.jsonl"
ROOT = DEFAULT_PACKS_ROOT.parent
MANIFEST = ROOT / "infra" / "mcp_servers.yaml"
DEMO = ROOT / "fixtures" / "workspace_demo"
DB_SQL = DEMO / "db" / "01_schema_and_data.sql"
_CITE = re.compile(r"^(?P<conn>[\w-]+)\.(?P<table>\w+)/(?P<key>[^#]+)#(?P<column>\w+)$")


def _configs():
    own = engine.parse_screen(yaml.safe_load(SCREEN.read_text(encoding="utf-8")), str(SCREEN))
    return {"pack alone": own, "as deployed": engine.load_screen(path=SCREEN)}


CONFIGS = _configs()


def _iban(country: str, bban: str) -> str:
    """A VALID IBAN, check digits computed (ISO 13616) — never written down."""
    check = 98 - int("".join(str(int(c, 36)) for c in bban + country + "00")) % 97
    return f"{country}{check:02d}{bban}"


def _grouped(iban: str) -> str:
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


# ── High: lowercase / mixed-case IBANs are masked ───────────────────────────────────────────────
@pytest.mark.parametrize("cfg", sorted(CONFIGS))
@pytest.mark.parametrize("bban_len", [11, 12, 26, 30])            # IBAN lengths 15, 16, 30, 34
@pytest.mark.parametrize("case", ["lower", "mixed"])
@pytest.mark.parametrize("spaced", [False, True])
def test_a_valid_iban_is_masked_whatever_its_case(cfg, bban_len, case, spaced):
    iban = _iban("GB", ("WEST12345698765432" + "0" * 30)[:bban_len])
    assert engine.mod97_ok(iban) and 15 <= len(iban) <= 34
    written = iban.lower() if case == "lower" else "".join(
        c.lower() if i % 2 else c for i, c in enumerate(iban))
    assert written != iban
    written = _grouped(written) if spaced else written
    result = engine.screen_text(f"IBAN: {written} — pay by Friday.", CONFIGS[cfg])
    assert result.decision == "allow", result.rationale
    assert written not in result.masked_text
    assert any(r["rule"] == "iban" for r in result.redactions)


@pytest.mark.parametrize("cfg", sorted(CONFIGS))
@pytest.mark.parametrize("after", [" by Friday.", " 12 items", " is due", "."])
def test_case_insensitivity_never_swallows_the_next_word_as_a_tail(cfg, after):
    """Case-insensitive shape must not read a following short word as the IBAN's tail (that
    would fail mod-97 and leave the account unmasked)."""
    for iban in (_iban("NO", "86011117947"), _iban("ES", "21000418450200051332")):
        for written in (iban.lower(), _grouped(iban).lower(), _grouped(iban)):
            result = engine.screen_text(f"Pay to {written}{after}", CONFIGS[cfg])
            assert written not in result.masked_text, (written, after)


@pytest.mark.parametrize("cfg", sorted(CONFIGS))
def test_an_iban_shaped_string_failing_mod97_is_still_left_alone(cfg):
    iban = _iban("GB", "WEST12345698765432")
    broken = iban[:2] + f"{(int(iban[2:4]) + 1) % 100:02d}" + iban[4:]
    assert not engine.mod97_ok(broken)
    result = engine.screen_text(f"Part {broken.lower()} ships Monday.", CONFIGS[cfg])
    assert broken.lower() in result.masked_text


# ── High: credential rule — secret-shaped values block; placeholders and prose never do ─────────
SECRETS = [
    "api_key=sk-proj-abcdefghijklmnop",                     # codex: hyphenated lowercase token
    "api_key: sk.proj.abcdefghijklmnop",                    # dotted lowercase token
    "access_token = ghs-lowercase-token-value",
    "api_key = sk_live_4eC39HqLyjWDarjtT1zdp7dc",
    "Password: Summer2026!",
    "PASSWORD=CorrectHorseBattery",
    'password: "Summer2026!"',
    "client_secret: 'q8Zr-Lm2p-Vx9t'",
]
PLACEHOLDERS = [
    'password: "********"', "password: '********'", "password: ********",
    'password: "••••••••"', 'password: "xxxxxxxx"', "api_key: XXXXXXXXXXXX",
    'password: "${DB_PASSWORD}"', "password: ${DB_PASSWORD}", "password: $DB_PASSWORD",
    'password: "<your password here>"', "password = <your password here>",
    'password: "{{ vault.db_password }}"', 'password: "[REDACTED]"', "password: [REDACTED]",
    "password: %(db_password)s", "api_key: sk-****************", 'password: "ab******"',
]
PROSE = [
    "Password: minimum-length requirements apply",
    "API key: rotated quarterly by the platform team",
    "Secret key: see the vault",
]
AMBIGUOUS_QUOTED = [                                         # a passphrase OR quoted policy prose
    'Password: "minimum-length requirements apply"',
    'password: "correct horse battery staple"',
    "client_secret: 'plain words but quoted'",
]


def _decisive_ids(result) -> set[str]:
    return {f["rule"] for f in result.fired if f["kind"] == "decisive"}


@pytest.mark.parametrize("cfg", sorted(CONFIGS))
@pytest.mark.parametrize("text", SECRETS)
def test_a_secret_shaped_value_blocks_decisively(cfg, text):
    result = engine.screen_text(text, CONFIGS[cfg])
    assert result.decision == "block"
    assert "credential_assignment" in _decisive_ids(result), result.fired


@pytest.mark.parametrize("cfg", sorted(CONFIGS))
@pytest.mark.parametrize("text", PLACEHOLDERS + PROSE)
def test_placeholders_and_policy_prose_are_never_credentials(cfg, text):
    result = engine.screen_text(text, CONFIGS[cfg])
    assert "credential_assignment" not in _decisive_ids(result), text
    assert not any(f["rule"].startswith("credential") for f in result.fired), result.fired
    assert result.decision == "allow", (text, result.rationale)


@pytest.mark.parametrize("cfg", sorted(CONFIGS))
@pytest.mark.parametrize("text", AMBIGUOUS_QUOTED)
def test_an_ambiguous_quoted_value_goes_to_review_not_a_decisive_block(cfg, text):
    result = engine.screen_text(text, CONFIGS[cfg])
    assert "credential_assignment" not in _decisive_ids(result)
    assert result.decision == "review", result.rationale
    assert any(f["kind"] == "signal" and f["rule"].startswith("credential")
               for f in result.fired)


def test_the_credential_signal_alone_cannot_block():
    """P2: a signal is corroboration, never a veto — derived from the file's own thresholds."""
    raw = yaml.safe_load(SCREEN.read_text(encoding="utf-8"))
    sig = [s for s in raw["signals"] if s["id"].startswith("credential")]
    assert sig and all(s["weight"] < raw["thresholds"]["block"] for s in sig)
    assert all(s["weight"] >= raw["thresholds"]["review"] for s in sig)


# ── Medium: the part_of label reads in the stored direction ─────────────────────────────────────
def test_part_of_ui_label_reads_the_way_the_ontology_says_to_read_the_edge():
    onto = yaml.safe_load((PACK_DIR / "ontology.yaml").read_text(encoding="utf-8"))
    ui = yaml.safe_load((PACK_DIR / "ui.yaml").read_text(encoding="utf-8"))
    edge = next(e for e in onto["edge_kinds"] if e["name"] == "part_of")
    m = re.search(r'read the\s+edge as\s+"src (?P<label>[^"]+) dst"', edge["description"])
    assert m, "the ontology must say how the edge is read"
    reading = re.sub(r"\s+", " ", m["label"])
    assert ui["graph_vocabulary"]["edge_labels"]["part_of"] == reading


# ── Critical / F-28: every golden citation names a DECLARED connection ──────────────────────────
def _golden() -> list[dict]:
    out = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines()
           if line.strip() and not line.lstrip().startswith("#")]
    assert out
    return out


def _all_cites(rec: dict) -> list[str]:
    cites = []
    for exp in rec.get("expect", {}).values():
        cites += [exp["cite"]] if "cite" in exp else list(exp.get("cites", []))
    for cand in (rec.get("outcome") or {}).get("candidates", []):
        cites += list(cand["cites"])
    return cites


def test_every_golden_citation_resolves_to_a_declared_database_connection():
    servers = {s["id"]: s for s in yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["servers"]}
    checked = 0
    for rec in _golden():
        for cite in _all_cites(rec):
            m = _CITE.match(cite)
            assert m, (rec["id"], cite)
            assert m["conn"] in servers, (rec["id"], m["conn"], sorted(servers))
            assert servers[m["conn"]]["source_class"] == "database", (rec["id"], m["conn"])
            checked += 1
    assert checked >= 8


# ── Medium (run 2): abstentions carry a STRUCTURED outcome, not only prose ──────────────────────
REASONS = {"value_absent", "identity_candidate", "withheld_by_screen", "source_not_connected"}


def _db_rows() -> dict[str, list[dict[str, str]]]:
    sql = DB_SQL.read_text(encoding="utf-8")
    tables: dict[str, list[dict[str, str]]] = {}
    for m in re.finditer(r"INSERT INTO (\w+) \(([^)]*)\) VALUES(.*?);", sql, re.S):
        cols = [c.strip() for c in m.group(2).split(",")]
        for tup in re.findall(r"^\s*\((.*)\)\s*,?\s*$", m.group(3), re.M):
            vals = [q.replace("''", "'") if n == "" else n
                    for q, n in re.findall(r"'((?:[^']|'')*)'|(-?\d+(?:\.\d+)?|NULL)", tup)]
            tables.setdefault(m.group(1), []).append(dict(zip(cols, vals)))
    return tables


def test_every_abstention_states_its_outcome_in_fields():
    abstentions = [r for r in _golden() if r["expect_abstain"]]
    assert abstentions
    for rec in abstentions:
        out = rec.get("outcome")
        assert out, rec["id"]
        assert out["reason_code"] in REASONS, rec["id"]
        assert isinstance(out["human_confirmation_required"], bool), rec["id"]
        assert out["coverage_status"] in {"complete", "reduced"}, rec["id"]
    assert {r["outcome"]["reason_code"] for r in abstentions} >= REASONS - {"value_absent"}


def test_the_identity_case_presents_a_cited_candidate_for_a_human():
    tables = _db_rows()
    recs = [r for r in _golden() if (r.get("outcome") or {}).get("reason_code")
            == "identity_candidate"]
    assert recs
    for rec in recs:
        out = rec["outcome"]
        assert out["human_confirmation_required"] is True and out["candidates"]
        for cand in out["candidates"]:
            cells = {}
            for cite in cand["cites"]:
                m = _CITE.match(cite)
                (row,) = [r for r in tables[m["table"]]
                          if ":".join(list(r.values())[:2]) == m["key"]
                          or list(r.values())[0] == m["key"]]
                cells[m["column"]] = row[m["column"]]
            assert cand["value"] in cells.values()
            # the evidence a human checks: the address in the ask is the cited record's own
            assert any(v and v in rec["ask"] for v in cells.values()), cells


def test_the_withheld_case_names_what_the_real_screen_blocks():
    expected = yaml.safe_load((DEMO / "expected_outcomes.yaml").read_text(encoding="utf-8"))["files"]
    cfg = engine.load_screen(path=SCREEN)
    recs = [r for r in _golden() if (r.get("outcome") or {}).get("reason_code")
            == "withheld_by_screen"]
    assert recs
    for rec in recs:
        out = rec["outcome"]
        assert out["coverage_status"] == "reduced" and out["withheld"]
        for item in out["withheld"]:
            assert expected[item["item"]]["decision"] == item["decision"] == "block"
            text = (DEMO / "files" / item["item"]).read_text(encoding="utf-8")
            result = engine.screen_text(text, cfg)
            assert result.decision == "block"
            assert set(item["rules"]) <= {f["rule"] for f in result.fired}


def test_the_unconnected_source_case_names_a_source_the_manifest_lacks():
    servers = {s["id"] for s in yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["servers"]}
    recs = [r for r in _golden() if (r.get("outcome") or {}).get("reason_code")
            == "source_not_connected"]
    assert recs
    for rec in recs:
        out = rec["outcome"]
        assert out["coverage_status"] == "reduced" and out["not_connected"]
        for name in out["not_connected"]:
            assert name.lower() in rec["ask"].lower()
            assert not any(name.lower() in s.lower() for s in servers), name
