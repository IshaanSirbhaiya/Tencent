"""P — core extensions asked for by S2 (tabular) and S3 (Drive): exact-merge keys for rows, the
write-capable connection that is never crawled, and errors a server reports as text."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import databridge_mcp_ingest as mi
from databridge_mcp_ingest.build import CANON_KEY, ProseBuilder, build_tables
from databridge_mcp_ingest.client import ContentPart, ReadOnlyClient, ToolResult
from databridge_mcp_ingest.screen import load_screen

from conftest import fs_connection


def _store(path: Path):
    from databridge_graph.store import GraphStore

    path.parent.mkdir(parents=True, exist_ok=True)
    return GraphStore(str(path))


def _customers(rows: list[dict]) -> dict:
    return {"schema": "public", "table": "customers", "primary_key": ["id"],
            "foreign_keys": [], "rows": rows}


def _mapping(canonical: str) -> dict:
    return {"node": {"kind": "organisation", "label": "{name}", "canonical_id": canonical,
                     "attrs": {}}, "edges": []}


def test_a_row_and_a_document_mention_with_the_same_identifier_are_one_node(
        walnut_env, common_vocab, tmp_path):
    from databridge_harness import FakeBackend, Harness

    assert not common_vocab.is_subject("organisation")     # the rule under test is non-subject
    store = _store(tmp_path / "g.db")
    now = dt.datetime.now(dt.timezone.utc)
    rows = [{"id": 1, "name": "Quillon Freight", "domain": "quillon.example"},
            {"id": 2, "name": "Aldermoor Logistics", "domain": "aldermoor.example"}]
    out = build_tables(store, common_vocab, source_id="db",
                       tables=[(_customers(rows), "a" * 64, _mapping("domain"))],
                       retrieved_at=now, mask_cfg=load_screen(), screen_cfg=load_screen())
    assert out["nodes"] == len(rows) and out["canonical_masked"] == []

    text = "Quillon Freight (quillon.example) signed the renewal.\n"
    name, domain = "Quillon Freight", "quillon.example"
    draft = {"entities": [{"name": name, "kind": "organisation", "char_start": text.find(name),
                           "char_end": text.find(name) + len(name), "canonical_id": domain}],
             "relations": []}
    harness = Harness(FakeBackend(json.dumps(draft)), profile="cloud")
    builder = ProseBuilder(store, harness, source_id="files", vocab=common_vocab,
                           doc_kind="document", screen=load_screen())
    built = builder.build(item_id="i" * 64, sha="b" * 64, raw_text=text, masked_text=text,
                          title="renewal.md", uri="file:///renewal.md", retrieved_at=now)
    assert built.entities == 1
    orgs = [r for r in store.open_nodes() if r["kind"] == "organisation"
            and r["canonical_id"] == domain]
    assert len(orgs) == 1                                         # ONE node, not two
    # evidence from both sources. The row's identity is COMPOSED from its pk cell, so since EF3
    # (E2E F-18) it is a `derived` pointer whose basis is that table_cell — not the cell itself.
    ptrs = store.sources_for(orgs[0]["id"])
    basis = {q.kind for p in ptrs for q in (p.derived_from or [])}
    assert {p.kind for p in ptrs} - {"derived"} | basis == {"table_cell", "doc_span"}
    assert len([r for r in store.open_nodes() if r["kind"] == "organisation"]) == len(rows)


def test_a_masked_identifier_is_never_a_merge_key(walnut_env, common_vocab, tmp_path):
    store = _store(tmp_path / "g.db")
    rows = [{"id": 1, "name": "One", "email": "ann@one.example"},
            {"id": 2, "name": "Two", "email": "bob@two.example"}]
    assert len(rows[0]["email"]) == len(rows[1]["email"])         # they would mask alike
    out = build_tables(store, common_vocab, source_id="db",
                       tables=[(_customers(rows), "c" * 64, _mapping("email"))],
                       retrieved_at=dt.datetime.now(dt.timezone.utc), mask_cfg=load_screen(),
                       screen_cfg=load_screen())
    assert len(out["canonical_masked"]) == len(rows)
    nodes = [r for r in store.open_nodes() if r["kind"] == "organisation"]
    assert len(nodes) == len(rows)
    assert all("[EMAIL]" not in (r["canonical_id"] or "") for r in nodes)
    assert CANON_KEY == "walnut_canonical"


def test_an_agent_write_connection_is_never_crawled(walnut_env, corpus, tmp_path):
    root, _ = corpus
    conn = mi.register_connection({**fs_connection("writer", root), "purpose": "agent_write"})
    assert conn.purpose == "agent_write"
    assert mi.load_connection("writer").purpose == "agent_write"   # survives the round trip
    with pytest.raises(mi.PlanError) as err:
        mi.run_ingest("writer", store=_store(tmp_path / "g.db"))
    assert "agent_write" in str(err.value)
    with pytest.raises(mi.PlanError) as err:
        mi.propose_plan("writer")                                 # refused before connecting
    assert "agent_write" in str(err.value)
    assert mi.register_connection(fs_connection("reader", root)).purpose == "ingest"


def test_import_declarations_keeps_purpose(walnut_env, tmp_path):
    decl = tmp_path / "servers.yaml"
    decl.write_text(json.dumps({"version": 1, "servers": [
        {"id": "crm-write", "purpose": "agent_write",
         "direct": {"transport": "stdio", "command": "x"}},
        {"id": "crm-read", "direct": {"transport": "stdio", "command": "x"}}]}),
        encoding="utf-8")
    got = {c.id: c.purpose for c in mi.import_declarations(decl, register=False)}
    assert got == {"crm-write": "agent_write", "crm-read": "ingest"}


class _Inner:
    def __init__(self, text: str) -> None:
        self.text = text

    def call_tool(self, name, arguments):
        return ToolResult(parts=(ContentPart(kind="text", text=self.text),))


def test_an_error_reported_as_text_is_an_error_when_the_profile_declares_it(tmp_path,
                                                                           monkeypatch):
    prof = tmp_path / "profiles" / "texterr"
    prof.mkdir(parents=True)
    (prof / "texterr.profile.yaml").write_text(json.dumps({
        "id": "text-errors", "match": {"all_tools": ["run_sql"], "error_text_prefix": "Error:"},
        "step": {"strategy": "resources"}}), encoding="utf-8")
    monkeypatch.setenv("WALNUT_CRAWL_PROFILES_DIR", str(tmp_path / "profiles"))
    plan = SimpleNamespace(profiles=["text-errors"], deny_tools=[])
    failing = _Inner("Error: relation \"nope\" does not exist")
    assert ReadOnlyClient(failing, plan=plan).call_tool("run_sql", {"sql": "SELECT 1"}).is_error is True
    # without the plan's declaration the same text is data — another server's document may
    # legitimately begin with that word
    assert ReadOnlyClient(failing).call_tool("run_sql", {"sql": "SELECT 1"}).is_error is False
    other = SimpleNamespace(profiles=["some-other-profile"], deny_tools=[])
    assert ReadOnlyClient(failing, plan=other).call_tool("run_sql", {"sql": "SELECT 1"}).is_error is False
    fine = _Inner("[{'n': 1}]")
    assert ReadOnlyClient(fine, plan=plan).call_tool("run_sql", {"sql": "SELECT 1"}).is_error is False
    assert ReadOnlyClient(failing, error_prefixes=["Error:"]).call_tool("q", {}).is_error


def test_python_repr_values_keep_their_text_and_strings_are_never_rewritten():
    from databridge_mcp_ingest.parse import loads_structured

    got = loads_structured(
        "[{'d': datetime.date(2026, 8, 31), 'n': Decimal('3'), 'note': \"it's Decimal('3')\", "
        "'t': datetime.datetime(2026, 9, 30, 18, 28, 45, tzinfo=zoneinfo.ZoneInfo(key='Etc/UTC'))}]")
    assert got[0]["d"] == "2026-08-31" and got[0]["n"] == "3"
    assert got[0]["note"] == "it's Decimal('3')"
    assert got[0]["t"].startswith("2026-09-30T18:28:45")


# --- the write gate reads camelCase (S3; security) ----------------------------------------------
@pytest.mark.parametrize("name", ["createFolder", "deleteItem", "shareFile", "uploadFile",
                                  "moveItem", "renameFile", "updateGoogleDoc", "createGoogleDoc",
                                  "appendToSheet", "addPermission",
                                  "create_folder", "CreateFolder"])
def test_camel_case_write_tools_are_refused_by_name(name):
    from databridge_mcp_ingest.plans import writes_by_name

    assert writes_by_name(name), name
    with pytest.raises(mi.McpClientError):
        ReadOnlyClient(_Inner("ok")).call_tool(name, {})


@pytest.mark.parametrize("name", ["listFolder", "readGoogleDoc", "getFile", "search",
                                  "list_directory", "read_text_file", "getSettings",
                                  "listUpdates"])
def test_camel_case_read_tools_still_read(name):
    from databridge_mcp_ingest.plans import writes_by_name

    assert not writes_by_name(name), name


def test_the_write_verb_pattern_is_the_same_in_the_automation_catalogue():
    catalogue = pytest.importorskip("databridge_automation.catalogue")
    from databridge_mcp_ingest import plans

    assert catalogue.WRITE_VERB.pattern == plans.WRITE_VERB.pattern
    assert catalogue.WRITE_VERB_EXTRA.pattern == plans.WRITE_VERB_EXTRA.pattern


# --- declared roots bind a resources crawl (S3 hazard) -------------------------------------------
class _Resources:
    supports_resources = True

    def __init__(self, uris):
        from databridge_mcp_ingest.client import ResourceInfo

        self._res = [ResourceInfo(uri=u, name=u.rsplit("/", 1)[-1], mime="text/plain", size=1)
                     for u in uris]

    def list_resources(self):
        return list(self._res)


def test_declared_roots_bind_a_resources_crawl():
    from databridge_mcp_ingest.connections import Scope
    from databridge_mcp_ingest.crawl import enumerate_resources
    from databridge_mcp_ingest.plans import StepSpec

    uris = ["mem:///team/a.txt", "mem:///team/sub/b.txt", "mem:///team-old/c.txt",
            "mem:///private/d.txt"]
    step = StepSpec(id="r", strategy="resources")
    everything = [i.uri for i in enumerate_resources(_Resources(uris), step, "s")]
    assert everything == uris                                  # no roots declared: all of them
    seen = []
    got = [i.uri for i in enumerate_resources(_Resources(uris), step, "s",
                                              scope=Scope(roots=["mem:///team"]),
                                              on_outside=lambda n, r: seen.append((n, r)))]
    inside = [u for u in uris if u.startswith("mem:///team/")]
    assert got == inside and inside
    assert seen == [(len(uris) - len(inside), ["mem:///team"])]


# --- the default screen carries F6's corrected rules (R-017 t09) ---------------------------------
@pytest.mark.parametrize("text,decision", [
    ("Password: minimum-length requirements apply.", "allow"),        # policy prose
    ("pwd: ********", "allow"),                                        # placeholder
    # a quoted all-word value is a passphrase OR quoted policy prose: REVIEW, not a decisive
    # block (R-021 rule, ported to the default screen by EF3)
    ('password: "correct horse battery staple"', "review"),
    ("api_key = sk_live_9aB3xYz12", "block")])
def test_default_credential_rule_blocks_secrets_not_policy_prose(text, decision):
    from databridge_mcp_ingest.screen import screen_text

    assert screen_text(text, load_screen()).decision == decision


def _iban(country: str, bban: str) -> str:
    digits = "".join(str(int(c, 36)) for c in bban + country + "00")
    return f"{country}{98 - int(digits) % 97:02d}{bban}"


@pytest.mark.parametrize("country,bban", [("NO", "86011117947"), ("BE", "539007547034"),
                                          ("GB", "NWBK60161331926819"),
                                          ("LC", "HEMM000100010012001200023015")])
def test_default_screen_masks_every_iso_iban_length(country, bban):
    from databridge_mcp_ingest.screen import apply_masks

    iban = _iban(country, bban)
    assert 15 <= len(iban) <= 34
    masked, red = apply_masks(f"Pay {iban} today", load_screen().redactions)
    assert iban not in masked and [r["rule"] for r in red] == ["iban"]
