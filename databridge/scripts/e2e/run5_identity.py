"""E2E5 scenario 3 (finance): after the identity rule, is a named company ONE legal_entity node, and
does no person node merge across sources? Read-only. -> <run>/artefacts/s3_identity.json

    python scripts/e2e/run5_identity.py "Fairmont Reach Property LLC" [decision_status]
Expected sources are DERIVED: the databases whose rows carry the exact legal name (live psql), plus
every document source whose captured corpus mentions it.
"""
import collections
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import e2e_dir, graph_db  # noqa: E402

E = e2e_dir()
name = sys.argv[1]
norm = re.sub(r"\s+", " ", name.strip().lower())


def psql(sql):
    out = subprocess.run(["docker", "exec", "walnut_demo_pg", "psql", "-U", "walnut", "-d",
                          "finance_demo", "-Atc", sql], capture_output=True, text=True, timeout=60)
    return out.stdout.strip()


c = sqlite3.connect(f"{graph_db(E).resolve().as_uri()}?mode=ro", uri=True)
nodes = [r[0] for r in c.execute(
    "select id from nodes where system_to is null and kind = 'legal_entity' and lower(trim(label)) = ?",
    (norm,))]
cited = set()
for nid in nodes:
    for (p,) in c.execute("select source_pointer_json from sources where entity_id = ?", (nid,)):
        cited.add((json.loads(p) or {}).get("source_id"))
exp = set()
if psql(f"select count(*) from core_banking.party where lower(legal_name) = '{norm}'") not in ("", "0"):
    exp.add("finance_core_banking")
if psql(f"select count(*) from crm.account_org where lower(org_name) = '{norm}'") not in ("", "0"):
    exp.add("finance_crm")
corpus = E / "root" / "fixtures" / "packs" / "finance"
docs = {"filings": "finance_filings", "credit_memos": "finance_credit_memos",
        "mailbox": "finance_mailbox", "spreadsheets": "finance_spreadsheets"}
doc_hits = sorted(s for d, s in docs.items()
                  if any(name.lower() in p.read_text("utf-8", "replace").lower()
                         for p in (corpus / d).rglob("*") if p.is_file()))
# subjects: no person-kind node cites two sources (never merged across sources)
subj = ("party", "contact", "beneficial_owner", "counterparty", "relationship_manager")
multi = 0
for (nid,) in c.execute(f"select id from nodes where system_to is null and kind in ({','.join('?'*len(subj))})", subj):
    s = {(json.loads(p) or {}).get("source_id") for (p,) in
         c.execute("select source_pointer_json from sources where entity_id = ?", (nid,))}
    if len(s) > 1 and nid.split(":")[0] in ("party", "contact", "beneficial_owner", "counterparty"):
        multi += 1
out = {"name": name, "legal_entity_nodes": nodes, "sources_cited": sorted(x for x in cited if x),
       "expected_sources": sorted(exp) + ([doc_hits[0]] if doc_hits and not (set(doc_hits) & cited) else
                                          sorted(set(doc_hits) & cited)[:1]),
       "doc_sources_mentioning": doc_hits,
       "person_nodes_merged_across_sources": multi,
       "decision_status": int(sys.argv[2]) if len(sys.argv) > 2 else None}
(E / "artefacts" / "s3_identity.json").write_text(json.dumps(out, indent=2), "utf-8")
print(json.dumps(out, indent=1))
