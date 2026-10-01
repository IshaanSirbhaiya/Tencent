"""The `workspace` pack loads, its screen parses, and the demo corpus behaves as MANIFEST.md claims.

Expectations are DERIVED from the files themselves (CLAUDE.md rule 6): kind counts come from the
YAML, rule ids from screen.yaml, per-document outcomes from fixtures/workspace_demo/
expected_outcomes.yaml. Each assertion carries a non-vacuous guard so it cannot pass on an empty
file or an empty corpus.

The screen evaluation below is a small REFERENCE reading of the semantics documented in
screen.yaml's header — it proves the data (rules + planted corpus) is coherent. The production
screen lives in the ingest package and must agree with it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from databridge_graph.meta import EdgeMetaKind, NodeMetaKind
from databridge_graph.packs import DEFAULT_PACKS_ROOT, deployment, load_pack

PACK_DIR = DEFAULT_PACKS_ROOT / "workspace"
ONTOLOGY = PACK_DIR / "ontology.yaml"
SCREEN = PACK_DIR / "screen.yaml"
DEMO = DEFAULT_PACKS_ROOT.parent / "fixtures" / "workspace_demo"
FILES = DEMO / "files"
EXPECTED = DEMO / "expected_outcomes.yaml"

_SCREEN_TOP = {"version", "pack", "thresholds", "decisive", "signals", "redactions"}
_RULE_KEYS = {"id", "description", "pattern", "reverse"}
_SIGNAL_KEYS = _RULE_KEYS | {"weight", "min_count"}
_REDACTION_KEYS = {"id", "description", "pattern", "validate", "replacement"}
#: What each section MUST carry (R-017 t09 #5): an exclusion without its `reverse` text is an
#: exclusion nobody can undo, so a missing key fails as loudly as an unknown one.
_REQUIRED = {"decisive": _RULE_KEYS, "signals": _RULE_KEYS | {"weight"},
             "redactions": _REDACTION_KEYS}
_THRESHOLD_KEYS = {"block", "review", "min_signals_to_block"}
_VALIDATORS = {"none", "luhn", "mod97"}


def _raw(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and data, f"{path} is empty"
    return data


# ── ontology ──────────────────────────────────────────────────────────────────────────────────
def test_workspace_ontology_loads_and_matches_its_file():
    raw = _raw(ONTOLOGY)
    pack = load_pack("workspace", use_cache=False)
    assert pack.name == raw["pack"] == "workspace"
    # Derived, not hardcoded: whatever the YAML declares is exactly what the loader bound.
    assert set(pack.node_kinds) == {n["name"] for n in raw["node_kinds"]}
    assert set(pack.edge_kinds) == {e["name"] for e in raw["edge_kinds"]}
    assert set(pack.subject_kinds) == {s["kind"] for s in raw.get("subject_kinds", [])}
    assert len(pack.node_kinds) >= 5 and len(pack.edge_kinds) >= 5 and pack.subject_kinds
    for n in raw["node_kinds"]:
        assert pack.node_kinds[n["name"]].binds_to is NodeMetaKind(n["binds_to"])
    for e in raw["edge_kinds"]:
        decl = pack.edge_kinds[e["name"]]
        assert decl.binds_to is EdgeMetaKind(e["binds_to"])
        assert decl.src and decl.dst, f"edge {e['name']} must name src and dst"


def test_workspace_binds_as_a_deployment_with_its_subjects_protected():
    raw = _raw(ONTOLOGY)
    declared_subjects = {s["kind"] for s in raw["subject_kinds"]}
    with deployment("workspace") as vocab:
        assert vocab.packs == ("workspace",)
        assert vocab.never_fuzzy_merge == declared_subjects
        for s in raw["subject_kinds"]:
            assert vocab.identity_grant_for(s["kind"]) == s["grant_type"]
            assert vocab.attestation_for(s["kind"]) == s["attestation"]
            assert vocab.identifiers_for(s["kind"]) == tuple(s["identifiers"])


def test_workspace_binds_beside_common():
    with deployment("workspace", "common") as vocab:
        assert set(load_pack("workspace").node_kinds) <= set(vocab.node_kinds)


# ── screen.yaml ───────────────────────────────────────────────────────────────────────────────
def _screen() -> dict:
    s = _raw(SCREEN)
    assert set(s) == _SCREEN_TOP, f"screen.yaml top-level keys {sorted(s)}"
    return s


def test_screen_parses_and_every_rule_is_well_formed():
    s = _screen()
    assert s["pack"] == "workspace" and s["version"] == 1
    t = s["thresholds"]
    assert set(t) == _THRESHOLD_KEYS, f"thresholds keys {sorted(t)}"
    assert all(isinstance(v, int) and not isinstance(v, bool) for v in t.values())
    assert 0 < t["review"] < t["block"] and t["min_signals_to_block"] >= 2
    sections = (("decisive", _RULE_KEYS), ("signals", _SIGNAL_KEYS), ("redactions", _REDACTION_KEYS))
    ids: list[str] = []
    for section, allowed in sections:
        assert s[section], f"{section} is empty"
        for rule in s[section]:
            assert set(rule) <= allowed, f"{section}/{rule.get('id')}: unknown keys"
            missing = _REQUIRED[section] - set(rule)
            assert not missing, f"{section}/{rule.get('id')}: missing {sorted(missing)}"
            assert all(isinstance(rule[k], str) and rule[k].strip()
                       for k in _REQUIRED[section] - {"weight"}), f"{section}/{rule['id']}"
            re.compile(rule["pattern"])
            ids.append(rule["id"])
    assert len(ids) == len(set(ids)), "rule ids must be unique"
    for sig in s["signals"]:
        # P2: no single signal may block on its own.
        assert 1 <= sig["weight"] < t["block"]
    for red in s["redactions"]:
        assert red["validate"] in _VALIDATORS


# ── reference evaluation over the demo corpus ─────────────────────────────────────────────────
def _luhn(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()][::-1]
    total = sum(x if i % 2 == 0 else (x * 2 - 9 if x * 2 > 9 else x * 2) for i, x in enumerate(d))
    return len(d) >= 13 and total % 10 == 0


def _mod97(value: str) -> bool:
    v = re.sub(r"[\s-]", "", value).upper()
    v = v[4:] + v[:4]
    return int("".join(str(int(c, 36)) for c in v)) % 97 == 1


_CHECK = {"none": lambda _: True, "luhn": _luhn, "mod97": _mod97}


def _text(path: Path) -> str:
    if path.suffix == ".pdf":
        pypdf = pytest.importorskip("pypdf")
        return "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(str(path)).pages)
    if path.suffix == ".docx":
        docx = pytest.importorskip("docx")
        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    return path.read_text(encoding="utf-8")


def _evaluate(text: str, s: dict) -> tuple[str, set[str], set[str], str]:
    decisive = {r["id"] for r in s["decisive"] if re.search(r["pattern"], text)}
    if decisive:
        return "block", decisive, set(), text
    fired = {sig["id"]: sig["weight"] for sig in s["signals"]
             if len(re.findall(sig["pattern"], text)) >= sig.get("min_count", 1)}
    score, t = sum(fired.values()), s["thresholds"]
    if score >= t["block"] and len(fired) >= t["min_signals_to_block"]:
        decision = "block"
    elif score >= t["review"]:
        decision = "review"
    else:
        decision = "allow"
    redacted: set[str] = set()
    if decision == "allow":
        for red in s["redactions"]:
            def sub(m, red=red):
                if _CHECK[red["validate"]](m.group(0)):
                    redacted.add(red["id"])
                    return red["replacement"]
                return m.group(0)
            text = re.sub(red["pattern"], sub, text)
    return decision, set(fired), redacted, text


def _corpus() -> dict[str, Path]:
    found = {p.relative_to(FILES).as_posix(): p for p in FILES.rglob("*") if p.is_file()}
    assert len(found) >= 10, f"demo corpus under {FILES} looks empty or unbuilt"
    return found


def test_every_corpus_file_has_an_expectation_and_vice_versa():
    expected = _raw(EXPECTED)["files"]
    assert set(_corpus()) == set(expected)


def test_corpus_planted_cases_are_present():
    corpus, expected = _corpus(), _raw(EXPECTED)["files"]
    names = [Path(p).name for p in corpus]
    assert any(names.count(n) > 1 for n in names), "need a duplicate filename in two folders"
    assert any("#" in Path(p).parent.as_posix() for p in corpus), "need a folder with '#'"
    assert {Path(p).suffix for p in corpus} >= {".md", ".txt", ".pdf", ".docx"}
    decisions = {e["decision"] for e in expected.values()}
    assert decisions == {"allow", "review", "block"}, "corpus must exercise all three outcomes"


@pytest.mark.parametrize("rel", sorted(_raw(EXPECTED)["files"]))
def test_screen_outcome_matches_manifest(rel):
    s, exp = _screen(), _raw(EXPECTED)["files"][rel]
    decision, rules, redacted, cleaned = _evaluate(_text(FILES / rel), s)
    assert decision == exp["decision"], f"{rel}: {decision} (rules {sorted(rules)})"
    if "rules" in exp:
        assert set(exp["rules"]) <= rules, f"{rel}: fired {sorted(rules)}"
    assert set(exp.get("redacted", [])) <= redacted, f"{rel}: redacted {sorted(redacted)}"
    for literal in exp.get("kept", []):
        assert literal in cleaned, f"{rel}: {literal!r} was masked but fails its checksum"


# ── R-017 (Codex t09) ─────────────────────────────────────────────────────────────────────────
def _decisive(rule_id: str) -> re.Pattern:
    rule = next(r for r in _screen()["decisive"] if r["id"] == rule_id)
    return re.compile(rule["pattern"])


@pytest.mark.parametrize("text", [
    "Password: minimum-length requirements apply",
    "API key: rotated quarterly by the platform team",
    "password: ********",
    "password = <your password here>",
    "password: ${DB_PASSWORD}",
    "Secret key: see the vault",
])
def test_credential_rule_does_not_block_policy_prose_or_placeholders(text):
    """t09 #2. A decisive rule is a veto; prose ABOUT passwords must not trip it."""
    assert not _decisive("credential_assignment").search(text)
    decision, _rules, _red, _clean = _evaluate(text, _screen())
    assert decision != "block"


@pytest.mark.parametrize("text", [
    "api_key = sk_live_4eC39HqLyjWDarjtT1zdp7dc",
    "api_key=sk-proj-abcdefghijklmnop",
    "Password: Summer2026!",
    "PASSWORD=CorrectHorseBattery",
])
def test_credential_rule_blocks_literal_secrets(text):
    assert _decisive("credential_assignment").search(text)
    assert _evaluate(text, _screen())[0] == "block"


@pytest.mark.parametrize("text", [
    'password: "correct horse battery staple"',
    "client_secret: 'plain words but quoted'",
    'Password: "minimum-length requirements apply"',
])
def test_a_quoted_passphrase_or_quoted_prose_is_reviewed_not_vetoed(text):
    """R-021 (Codex round 2, reversing R-017 t09 #2): a quoted all-word value cannot be told apart
    from quoted policy prose, so it is a review-band signal, never a decisive block."""
    assert not _decisive("credential_assignment").search(text)
    assert _evaluate(text, _screen())[0] == "review"


def _iban(country: str, bban: str) -> str:
    """A VALID IBAN, its check digits computed (ISO 13616) — never written down."""
    rearranged = bban + country + "00"
    check = 98 - int("".join(str(int(c, 36)) for c in rearranged)) % 97
    return f"{country}{check:02d}{bban}"


@pytest.mark.parametrize("bban_len", [11, 12, 26, 30])      # IBAN lengths 15, 16, 30, 34
def test_every_iso_iban_length_is_masked(bban_len):
    """t09 #1. The ISO range is 15–34 characters; the shape used to start at 16."""
    iban = _iban("NO", ("86011117947" + "0" * 30)[:bban_len])
    assert 15 <= len(iban) <= 34 and _mod97(iban)
    decision, _rules, redacted, cleaned = _evaluate(f"Pay to {iban} by Friday.", _screen())
    assert decision == "allow" and "iban" in redacted and iban not in cleaned


def test_the_production_screen_agrees_on_the_new_cases():
    """The reference reading above and the ingest package's screen must agree (P2/P13)."""
    screen = pytest.importorskip("databridge_mcp_ingest.screen")
    cfg = screen.load_screen(path=SCREEN)
    assert screen.screen_text("Password: minimum-length requirements apply", cfg).decision != \
        "block"
    assert screen.screen_text('password: "correct horse battery staple"', cfg).decision == \
        "review"
    assert screen.screen_text("password: Summer2026!", cfg).decision == "block"
    iban = _iban("NO", "86011117947")
    result = screen.screen_text(f"Pay to {iban}.", cfg)
    assert result.decision == "allow" and iban not in result.masked_text


# ── the golden set agrees with the synthetic database ─────────────────────────────────────────
GOLDEN = PACK_DIR / "evals" / "agent_contract.jsonl"
DB_SQL = DEMO / "db" / "01_schema_and_data.sql"
_CITE = re.compile(r"^(?P<conn>[\w-]+)\.(?P<table>\w+)/(?P<key>[^#]+)#(?P<column>\w+)$")
_SQL_TOKEN = re.compile(r"'((?:[^']|'')*)'|(-?\d+(?:\.\d+)?|NULL)")


def _db_tables() -> dict[str, list[dict[str, str]]]:
    """Every INSERTed row of the fixture SQL, values as their literal TEXT ("22200.00" stays so).
    The SQL is parsed, never executed."""
    sql = DB_SQL.read_text(encoding="utf-8")
    tables: dict[str, list[dict[str, str]]] = {}
    for m in re.finditer(r"INSERT INTO (\w+) \(([^)]*)\) VALUES(.*?);", sql, re.S):
        cols = [c.strip() for c in m.group(2).split(",")]
        for tup in re.findall(r"^\s*\((.*)\)\s*,?\s*$", m.group(3), re.M):
            values = [(quoted.replace("''", "'") if number == "" else
                       None if number == "NULL" else number)
                      for quoted, number in _SQL_TOKEN.findall(tup)]
            assert len(values) == len(cols), tup
            tables.setdefault(m.group(1), []).append(dict(zip(cols, values)))
    assert tables, "the fixture SQL parsed to nothing"
    return tables


def _row_for(tables, table: str, key: str) -> dict:
    """The row a cite's key names: the first column, or `<first>:<second>` for a table whose own
    key is a serial the INSERTs do not carry (contacts)."""
    hits = [r for r in tables[table]
            if list(r.values())[0] == key or ":".join(list(r.values())[:2]) == key]
    assert len(hits) == 1, f"{table}/{key}: {len(hits)} rows"
    return hits[0]


def _golden() -> list[dict]:
    import json

    out = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines()
           if line.strip() and not line.lstrip().startswith("#")]
    assert out, "the golden set is empty"
    return out


def test_golden_set_records_follow_the_documented_format():
    """t09 #6. `why` is documented for every record; abstentions must carry it."""
    for rec in _golden():
        assert set(rec) <= {"id", "ask", "expect", "expect_abstain", "why", "outcome"}, rec["id"]
        if rec["expect_abstain"]:
            assert rec["expect"] == {} and rec.get("why") and rec.get("outcome"), rec["id"]
        for field, exp in rec["expect"].items():
            assert set(exp) in ({"value", "cite"}, {"value", "cites"}), (rec["id"], field)


def test_every_golden_value_is_the_cell_it_cites():
    """t09 #3. A read value cites ITS cell — the cited column holds exactly the expected value."""
    tables = _db_tables()
    checked = 0
    for rec in _golden():
        for field, exp in rec["expect"].items():
            if "cite" not in exp:
                continue
            m = _CITE.match(exp["cite"])
            assert m, exp["cite"]
            row = _row_for(tables, m["table"], m["key"])
            assert row[m["column"]] == exp["value"], (rec["id"], field, row)
            checked += 1
    assert checked >= 5


def test_an_aggregate_golden_cites_every_row_and_predicate_it_counts():
    """t09 #3. A computed value lists every cell its computation read, and recomputing from those
    cells alone gives the expected answer — dropping a row or a predicate changes it."""
    tables = _db_tables()
    aggregates = [(rec, exp) for rec in _golden() for exp in rec["expect"].values()
                  if "cites" in exp]
    assert aggregates, "the golden set has no aggregate case"
    for rec, exp in aggregates:
        cells: dict[tuple[str, str], dict[str, str]] = {}
        for cite in exp["cites"]:
            m = _CITE.match(cite)
            assert m, cite
            row = _row_for(tables, m["table"], m["key"])
            cells.setdefault((m["table"], m["key"]), {})[m["column"]] = row[m["column"]]
        owners = {c.get("customer_id") for c in cells.values()}
        assert len(owners) == 1 and None not in owners, rec["id"]
        owner = owners.pop()
        (table,) = {t for t, _ in cells}
        # every row that customer has is cited — none silently omitted…
        assert {k for _, k in cells} == {list(r.values())[0] for r in tables[table]
                                         if r["customer_id"] == owner}, rec["id"]
        # …and the count is recomputed from the cited status cells alone.
        assert str(sum(1 for c in cells.values() if c.get("status") == "open")) == exp["value"]
