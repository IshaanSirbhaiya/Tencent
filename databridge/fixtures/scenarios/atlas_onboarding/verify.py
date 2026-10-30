"""Exercise this fixture through real mock APIs, connectors, retrieval, ledger and page builder.

No external model calls. Does not replace the developer's running servers or enterprise fixtures.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT.parents[2]
for part in ["apps/internal_brain", "apps/internal_brain/tests", "apps/mock_platforms",
             "packages/core", "packages/harness", "packages/adapter", "packages/retrieval"]:
    sys.path.insert(0, str(DB / part))
os.environ["IB_CLAUDE"] = "0"
os.environ["IB_FORCE_OFFLINE"] = "1"

from fastapi.testclient import TestClient
from databridge_ib.app import Services, create_app
from databridge_ib.audit.content import AnswerStore
from databridge_ib.audit.ledger import AccessLedger
from databridge_ib.audit.query import AuditProjection
from databridge_ib.config import IbConfig
from e2e_world import E2E


def verify():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    checks = []
    with tempfile.TemporaryDirectory(prefix="walnut-atlas-") as tmp:
        path = Path(tmp)
        cfg = IbConfig(var_root=path / "state", world_path=ROOT / "world.json",
                       demo_login=True, deliverables=True)
        ledger, answers = AccessLedger(cfg), AnswerStore(cfg)
        engine = E2E(path, world_path=ROOT / "world.json", ledger=ledger, answers=answers)
        svc = Services(cfg=cfg, directory=engine.directory, index=engine.index,
                       connectors=engine.connectors, ledger=ledger, answers=answers,
                       pipeline=engine.pipeline, ingestor=engine.ingestor,
                       projection=AuditProjection(cfg, ledger))
        app = create_app(svc)
        client = TestClient(app)
        login = client.post("/auth/demo-login", json={"persona": "alex.product"})
        assert login.status_code == 200, login.text
        assert client.get("/auth/whoami").json()["name"] == "Alex Morgan"
        checks.append("Alex authenticates as the new lead using the directory, not the question text")
        result = client.post("/ib/ask", json={"question": manifest["question"]})
        assert result.status_code == 200, result.text
        answer = result.json()
        assert answer["citations"], answer
        checks.append("Broad onboarding question returns a grounded answer with source citations")
        decision = client.post("/ib/views", json={"req_id": answer["request_id"],
                                                 "instruction": manifest["question"][:300], "mode": "decide"})
        assert decision.status_code == 200, decision.text
        assert decision.json()["render"] == "page", decision.text
        checks.append("The real page decision recognises this question as needing a workspace")
        response = client.post("/ib/views", json={"req_id": answer["request_id"], "instruction": manifest["layout"]})
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["status"] == "ready", view
        checks.append("The actual A2UI page builder returns a ready page from authorised sources")
        serialized = json.dumps({"answer": answer, "view": view})
        assert manifest["never_reveal"] not in serialized
        assert "confluence:atlas-sec-private" not in serialized
        checks.append("Restricted security note and its marker never appear in the lead's answer or page")
        assert "confluence:boreal-overview" not in serialized
        checks.append("A separate project's date is excluded even though its text mentions Atlas")
        for c in answer["citations"]:
            rid = c.get("doc_id")
            if rid:
                preview = client.get("/ib/doc/" + rid)
                assert preview.status_code == 200, preview.text
        checks.append("Every answer citation can open a source preview through the real API")
        other = TestClient(app)
        other.post("/auth/demo-login", json={"persona": "jdoe"}).raise_for_status()
        denied = other.post("/ib/views", json={"req_id": answer["request_id"], "instruction": "report"})
        assert denied.status_code == 404, denied.text
        checks.append("Another employee cannot reuse Alex's answer to build a page")
        contractor = TestClient(app)
        contractor.post("/auth/demo-login", json={"persona": "ken.ext"}).raise_for_status()
        restricted_ask = contractor.post("/ib/ask", json={"question": manifest["question"]})
        assert restricted_ask.status_code == 200, restricted_ask.text
        assert not restricted_ask.json()["citations"], restricted_ask.text
        checks.append("A contractor without project membership gets no Atlas sources")
        followups = {}
        for name, q in {
            "date": "Why was the Atlas pilot moved, and what is the current target date?",
            "team": "Who are all the employees working on Atlas and what has each contributed? Show a team table from the Drive roster.",
            "blocker": "What are the Atlas callback failures and who owns the remaining fixes?",
            "first_week": "What are Alex Morgan's first-week priorities as Atlas product lead? Show the handover checklist.",
        }.items():
            r = client.post("/ib/ask", json={"question": q})
            assert r.status_code == 200 and r.json()["citations"], r.text
            followups[name] = r.json()
        checks.append("Date, team, blocker and first-week follow-up questions all retrieve cited evidence")
        data = view["a2ui"][2]["updateDataModel"]["value"]
        names_found = [p["name"] for p in manifest["team"] if p["name"] in json.dumps(data)]
        source_platforms = sorted({c["platform"] for c in answer["citations"]})
        assert source_platforms == ["confluence", "drive", "jira", "slack"], source_platforms
        table = next(b["table"] for b in data["blocks"] if b["type"] == "table")
        assert len(table["rows"]) == 12, table
        assert len(names_found) == 12, names_found
        assert not any(b["type"] == "missing" for b in data["blocks"]), view["layout"]
        checks.append("The initial offline workspace contains all 12 team members, table, steps, timeline and conversations")
        proof = {"synthetic": True, "external_model_calls": 0,
                 "mode": "Real pipeline over in-process mock platforms; deterministic answer and rules page",
                 "checks_passed": checks, "answer_platforms": source_platforms,
                 "team_names_in_initial_rules_page": names_found,
                 "layout": view["layout"], "answer": answer, "page_decision": decision.json(),
                 "view": view, "followups": followups}
        dest = ROOT / "verification"
        dest.mkdir(exist_ok=True)
        (dest / "offline-proof.json").write_text(json.dumps(proof, indent=2, ensure_ascii=False) + "\n")
        print(json.dumps({"checks_passed": len(checks), "answer_platforms": source_platforms,
                          "layout": view["layout"], "team_names_in_page": len(names_found),
                          "answer": answer["answer"]}, indent=2))


if __name__ == "__main__":
    verify()
