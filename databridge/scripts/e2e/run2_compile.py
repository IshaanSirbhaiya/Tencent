"""English compile with a scripted USER answering the compiler's questions (E2E2).

Answering a question the compiler asks is what a user does in the builder; it is not a workaround.
The scripted user only ever answers from the sentence it typed or by picking one of the OFFERED
options; it never edits a spec, never writes files and never adds tool hints. Every round is
recorded, so 'needed N rounds' is evidence.
"""
from __future__ import annotations

import re

from e2e_client import call

# What the sentence-writer means by these words (a user's intent, stated once).
USER_MEANS = {"crm": "larkspur_db", "database": "larkspur_db", "files": "larkspur-files",
              "follow-ups": "larkspur-followups", "followups": "larkspur-followups"}


def _answer(q: dict, text: str) -> str | None:
    qid, opts = q.get("id", ""), q.get("options") or []
    low = text.lower()
    if qid.startswith("mode:") and len(opts) == 1:
        return opts[0]                      # the compiler allows only one mode: accept it (E2E3)
    if qid.startswith("model_source:") and set(opts) == {"yes", "no"}:
        # E2E3: 'should it READ <src>?' — yes only when the user's own words name that source
        src = qid.split(":", 1)[1]
        named = src.lower() in low or any(w in low and v == src for w, v in USER_MEANS.items())
        return "yes" if named else "no"
    if qid.startswith("mode:"):
        name = qid.split(":", 1)[1].replace("_", " ")
        words = [w for w in name.split() if len(w) > 3]
        # the clause naming this action best; 'ask me before' -> human_required
        clauses = sorted(re.split(r"[.;]", low), key=lambda c: -sum(w in c for w in words))
        for clause in clauses[:1]:
            if "ask me" in clause:
                return "human_required"
            if "tell me" in clause:
                return "human_notified"
        return "human_required"
    if qid.startswith("source:"):
        word = qid.split(":", 1)[1].lower()
        if USER_MEANS.get(word) in opts or not opts:
            return USER_MEANS.get(word)
    if len(opts) == 1:
        return opts[0]
    if qid.startswith("tool:"):
        writes = [o for o in opts if re.search(r"execute|write|insert", o)]
        if len(writes) == 1:
            return writes[0]
    if qid.startswith("model:") and not opts:
        # a free-text question from the model: what this user would type back
        t = q.get("text", "").lower()
        if "follow-up task" in t or "crm table" in t or "written to" in t:
            return "The follow_up_tasks table, one row per invoice."
        if "folder" in t and ("draft" in t or "named" in t or "followups" in t or "follow-ups" in t):
            return "The top folder of larkspur-followups; one markdown file per invoice, named after the invoice number."
        if "invoice" in t and ("status" in t or "due" in t):
            return "The invoices table; status 'open' means open, due_on is the due date."
        return "All of it: every root folder of larkspur-files and every table of larkspur_db."
    for o in opts:
        if o.endswith("_writer") and ("crm" in q.get("text", "").lower() or "task" in qid):
            return o
    return None


def compile_text(text: str, name: str, *, max_rounds: int = 3, token=None) -> dict:
    answers: dict = {}
    rounds = []
    resp = {}
    for i in range(max_rounds):
        st, resp, ms = call("POST", "/agents/compile", {"text": text, "answers": answers},
                            name=f"{name}_r{i}", token=token, timeout=600)
        oq = resp.get("open_questions") or []
        rounds.append({"round": i, "status": st, "ms": round(ms), "activatable": resp.get("activatable"),
                       "open_questions": [{"id": q.get("id"), "text": q.get("text"),
                                           "options": q.get("options")} for q in oq],
                       "answers_sent": dict(answers),
                       "compilation": (resp.get("compilation") or {})})
        if st != 200 or resp.get("activatable") or not oq:
            break
        new = {q["id"]: a for q in oq if q.get("id") not in answers
               and (a := _answer(q, text)) is not None}
        if not new:
            break
        answers.update(new)
    return {"text": text, "rounds": rounds, "final": resp, "activatable": resp.get("activatable"),
            "draft_id": resp.get("draft_id"), "spec_id": resp.get("spec_id"),
            "kind": resp.get("kind")}


def save_draft(c: dict, name: str, token=None) -> dict:
    if not c.get("draft_id"):
        return {"status": None, "error": "no draft"}
    st, d, _ = call("POST", "/agents", {"draft_id": c["draft_id"], "activate": True},
                    name=f"{name}_save", token=token)
    return {"status": st, **(d if isinstance(d, dict) else {})}
