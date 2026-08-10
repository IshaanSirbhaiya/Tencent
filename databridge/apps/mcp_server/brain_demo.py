"""Drag-and-drop demo ingestion (T08 Part 2).

Three REAL source documents an investor can drop onto /brain. Each, when ingested, runs through
the SAME grounded extraction + bi-temporal upsert path the production pipeline uses (the model is
the deterministic gold backend — the airgapped-demo constraint, D-FE-4 replay-first — not a hosted
LLM), and adds genuinely NEW entities to the Acme graph (which animate in).

Honesty: the files are real and downloadable; their content is real; the extraction grounding wall,
the merge/supersede, and the new graph nodes are all real. Only the extraction MODEL is replaced by
a deterministic gold handler keyed to the document content — exactly the gold-backend pattern E1 /
EV-BRAIN use as the "plumbing proof". Names in each fixture appear verbatim so the grounding wall
relocates them (no fabrication).
"""
from __future__ import annotations

from databridge_evals.e_brain import FIXTURE_ENTITIES, FIXTURE_RELATIONS, FIXTURE_TEXT
from databridge_graph.extractor import ExtractedEntity, ExtractedRelation, ExtractionDraft
from databridge_harness import FakeBackend, Harness

# --- fixture bodies (the extraction source text; also the file content) -----------
EMAIL_TEXT = (
    "Hi James,\n\n"
    "Following up on the BigClient renewal discussion. I met with Alice Wong, the head of "
    "procurement at BigClient, and she is keen to move forward on a multi-year agreement. Based on "
    "that conversation I want us to Finalise BigClient renewal terms before the end of the quarter, "
    "with a clearer SLA and the pricing Priya proposed.\n\n"
    "Alice Wong was clear that BigClient values our support and wants to expand usage next year. I "
    "will brief the board on Thursday; please prepare the updated contract so Legal can review it "
    "this week.\n\n"
    "Thanks,\nSarah Chen\nCEO, Acme Corp\n"
)

MEETING_TEXT = (
    "Q1 Planning Meeting - January 20, 2024\n"
    "Attendees: Sarah Chen, James Miller, Tom Walsh, Priya Patel, Nina Roy\n\n"
    "Sarah Chen: Thanks everyone. Our priority this quarter is growth.\n"
    "James Miller: Engineering is on track with the cloud migration.\n"
    "Tom Walsh: Project Atlas is progressing well and on schedule.\n"
    "Nina Roy: On the marketing side, I propose we kick off Project Phoenix to drive demand in the "
    "second half of the year.\n"
    "Sarah Chen: Agreed. Let's Launch Q3 marketing campaign as the centerpiece of Project Phoenix. "
    "Nina Roy will own Project Phoenix end to end.\n"
    "Priya Patel: Sales will align the pipeline to support the campaign.\n"
    "Nina Roy: Great. Project Phoenix will need a budget, but we can start planning now.\n"
    "Sarah Chen: Approved in principle. Nina Roy, please draft the plan.\n"
)

CONTRACT_TEXT = (
    "MASTER SERVICES AGREEMENT - RENEWAL\n\n"
    "This renewal of the Master Services Agreement is entered into between Acme Corp and BigClient. "
    "On behalf of Acme Corp, the agreement is managed by Priya Patel. BigClient is represented by "
    "Robert Vance, General Counsel at BigClient.\n\n"
    "The parties agree to Sign BigClient MSA renewal for a further term, preserving the existing "
    "service levels and the agreed contract value. Maria Garcia, the account contact, will "
    "coordinate onboarding for the renewed term.\n\n"
    "This agreement is effective upon signature by Priya Patel and Robert Vance.\n"
)


def _ents(text: str, specs: list[tuple]) -> list[dict]:
    """Build ExtractedEntity dicts with offsets located in ``text`` (the grounding wall relocates
    by whole-token match anyway; real offsets keep it tidy). specs: (name, kind, canonical|None)."""
    out = []
    for name, kind, canon in specs:
        i = text.find(name)
        out.append({"name": name, "kind": kind, "char_start": max(i, 0),
                    "char_end": (i + len(name)) if i >= 0 else len(name), "canonical_id": canon})
    return out


def _rels(specs: list[tuple]) -> list[dict]:
    # (src_name, rel_type, dst_name) — evidence offsets are 0; the extractor grounds a relation by
    # the two endpoint names CO-OCCURRING in the text (it relocates the evidence span itself).
    return [{"src_name": s, "rel_type": r, "dst_name": d,
             "evidence_char_start": 0, "evidence_char_end": 0} for s, r, d in specs]


# --- the fixtures (filename → text + the entities/relations its extraction yields) ---
FIXTURES: list[dict] = [
    {
        "filename": "email_001.eml",
        "marker": "Alice Wong",          # unique substring → dispatch
        "text": EMAIL_TEXT,
        "entities": _ents(EMAIL_TEXT, [
            ("Sarah Chen", "person", "sarah@acmecorp.com"),       # corroborates seed
            ("Alice Wong", "person", "alice.wong@bigclient.com"),  # NEW
            ("BigClient", "org", "bigclient.com"),                 # corroborates seed
            ("Finalise BigClient renewal terms", "decision", None),  # NEW
        ]),
        "relations": _rels([
            ("Alice Wong", "works_at", "BigClient"),
            ("Sarah Chen", "mentioned", "Alice Wong"),
            ("Sarah Chen", "decided", "Finalise BigClient renewal terms"),
        ]),
    },
    {
        "filename": "meeting_001.txt",
        "marker": "Project Phoenix",
        "text": MEETING_TEXT,
        "entities": _ents(MEETING_TEXT, [
            ("Sarah Chen", "person", "sarah@acmecorp.com"),
            ("Nina Roy", "person", "nina.roy@acmecorp.com"),       # NEW
            ("Project Phoenix", "project", None),                  # NEW
            ("Launch Q3 marketing campaign", "decision", None),    # NEW
        ]),
        "relations": _rels([
            ("Nina Roy", "owns", "Project Phoenix"),
            ("Sarah Chen", "decided", "Launch Q3 marketing campaign"),
            ("Launch Q3 marketing campaign", "enables", "Project Phoenix"),
        ]),
    },
    {
        "filename": "contract_001.pdf",
        "marker": "Robert Vance",
        "text": CONTRACT_TEXT,
        "entities": _ents(CONTRACT_TEXT, [
            ("Priya Patel", "person", "priya@acmecorp.com"),
            ("Robert Vance", "person", "robert.vance@bigclient.com"),  # NEW
            ("BigClient", "org", "bigclient.com"),
            ("Sign BigClient MSA renewal", "decision", None),          # NEW
            ("Maria Garcia", "person", "maria@techcorp.io"),           # corroborates seed
        ]),
        "relations": _rels([
            ("Robert Vance", "works_at", "BigClient"),
            ("Priya Patel", "mentioned", "Robert Vance"),
            ("Priya Patel", "decided", "Sign BigClient MSA renewal"),
        ]),
    },
]

# the EV-BRAIN sample stays a registered source too (keeps the existing /brain/ingest demo working).
# marker must be UNIQUE across all fixture texts — "James Miller leads engineering" appears only here
# (NOT a substring of the meeting text), so dispatch never depends on dict iteration order.
_SAMPLE = {"filename": "demo_sample.txt", "marker": "James Miller leads engineering",
           "text": FIXTURE_TEXT, "entities": FIXTURE_ENTITIES, "relations": FIXTURE_RELATIONS}


def registered_sources() -> dict[str, str]:
    """filename → text, for the allow-listed ingest (brain_ingest never opens a real path)."""
    return {fx["filename"]: fx["text"] for fx in (*FIXTURES, _SAMPLE)}


def demo_ingest_harness() -> Harness:
    """A FakeBackend harness whose handler DISPATCHES on the document text — so each dropped fixture
    deterministically extracts its own entities (not a single fixed set)."""
    drafts = {
        fx["marker"]: ExtractionDraft(
            entities=[ExtractedEntity(**e) for e in fx["entities"]],
            relations=[ExtractedRelation(**r) for r in fx["relations"]],
        ).model_dump_json()
        for fx in (*FIXTURES, _SAMPLE)
    }

    def handler(*, model, messages, guided_json) -> str:
        doc = messages[-1]["content"] if messages else ""
        for marker, payload in drafts.items():
            if marker in doc:
                return payload
        return ExtractionDraft().model_dump_json()       # unknown doc → extract nothing (honest)

    return Harness(FakeBackend(handler), profile="cloud")
