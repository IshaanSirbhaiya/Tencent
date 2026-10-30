"""Data-driven workflow checker for the Internal Brain (docs/finals/demo_day/WORKFLOW_TEST_PLAN_STORY1.md).

    python scripts/workflow_check.py scripts/workflow_steps/sample_no_model.json
    python scripts/workflow_check.py STEPS.yaml --base http://127.0.0.1:8100 --out report.json
    python scripts/workflow_check.py STEPS.json --only amy_velquarry,ken_bayfront

It runs a list of steps from a JSON (or YAML, when PyYAML is installed) file through the REAL HTTP
API of a running Internal Brain and checks each one. Every call is an ordinary API call: demo
sign-in (``POST /auth/demo-login``, passcode from ``IB_DEMO_PASSCODE`` when the server needs one),
``POST /ib/ask``, ``GET /ib/status``, ``POST /ib/graph/openable``, ``GET /ib/doc/{id}``, the act,
deliverable and view routes, and the compliance officer's ``GET /audit/journey/{req_id}`` and
``GET /audit/verify``. Asks are real asks: they are ledgered like any other (channel ``ui``).

A non-loopback ``--base`` is refused unless ``--allow-remote``, and then only over https (the
demo passcode goes to that origin). Standard library only (PyYAML optional, for .yaml files).

Steps file
----------
Either a list of steps, or an object::

    {"auditor": "maya.compliance",          # who reads /audit/* for ledger checks (default)
     "refusal_text": "...",                 # the one refusal text (default: the IB's own)
     "provider_terms": "regex",             # extra names a neutral label must never contain
     "steps": [ ... ]}

Each step (``id`` and ``persona`` are required; ``question`` may be left out for an audit-only
step whose ``expect.ledger.of`` names an earlier step)::

    id:        unique step id
    persona:   demo persona id (amy.am, mlro.tan, ken.ext, jdoe, maya.compliance, ...)
    question:  the exact question, asked through POST /ib/ask
    same_as:   <step id>  the response must be byte-identical to that step's after stripping
               request_id, as_of and every citation's *_checked_at (the P2 rule)
    expect:
      contains:      [..]  case-insensitive substrings that must ALL appear
      not_contains:  [..]  terms that must NOT appear (case-insensitive substring; write "/re/"
                     for a case-insensitive regex, e.g. "/\\bSTR\\b/" so "restricted" is not a hit).
                     Searched in every visible string of the response (answer, scope note,
                     citation titles and containers, model label), never in ids or timestamps.
      refusal:       true | false   the answer is (not) the one refusal text with no citations
      citations_min: n     at least n distinct cited documents
      cite_recheck:  true  every cited document is openable NOW by this persona: the API's own
                     check (POST /ib/graph/openable must return every cited id) and GET
                     /ib/doc/{id} must be 200 for each
      model_label:   "neutral"  no stand-in provider name anywhere in the labels or the answer;
                     no Hy3/Hunyuan/TokenHub unless /ib/status says the active provider is TokenHub;
                     no "no model" (and not the extractive label) when a model answered; no model
                     label at all when /ib/status says no model is active
      model_answered: true | false   a model (not the quoted extractive path) wrote the answer
      max_latency_ms: n    the ask's wall-clock time (as measured by this script)
      ledger:        [kinds]  or  {kinds: [..], of: <step id>, sub: <persona>, auditor: <persona>,
                     verify_chain: true}
                     the request (this step's, or ``of``'s) is in the audit trail, read by the
                     auditor through GET /audit/journey/{req_id}: the kinds appear in this order
                     (other kinds may sit between them), the asker is ``sub`` (default: that
                     step's persona), ``refused`` matches expect.refusal when given, and the
                     answer text is NOT in the journey. ``verify_chain``: GET /audit/verify is ok.
    action:        optional, run after the ask on its request id
      type:   draft | ticket_propose | deliverable | view
      kind:   reply | message (draft); table | chart | analysis (deliverable)
      chart:  auto | bar | line (deliverable);  instruction: "..." (view)
      expect:
        status_code:   200 (default); e.g. 404 for a refused answer (the same 404 as not-found)
        grounded:      true (default when status_code is 200): every document the result cites
                       is one the answer cited; a draft is not sent; a deliverable or view is
                       "ready"; a ticket is "proposed" (nothing created)
        contains / not_contains / citations_min / cite_recheck / model_label: as above, on the
                       action's result
        ledger:        [kinds] the kinds the action adds to the request's journey (e.g.
                       ["action.proposed"], ["deliverable.requested", "deliverable.created"])

Output: a PASS/FAIL table on stdout, a JSON report with ``--out``, exit code 0 only when every
selected step passed (1 when one failed, 2 for a bad steps file or an unreachable server).
``--only a,b`` runs those steps plus the steps they depend on (same_as / ledger.of).

In-process use (tests): ``run_steps(doc, client)`` takes any object with
``request(method, path, json=None, headers=None)`` returning a response with ``status_code``,
``headers``, ``text`` and ``json()`` (a FastAPI TestClient fits), so no socket is opened.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

DEFAULT_BASE = "http://127.0.0.1:8100"
DEFAULT_AUDITOR = "maya.compliance"
#: the Internal Brain's one refusal text (databridge_ib/models.py REFUSAL_TEXT)
REFUSAL_TEXT = "I couldn't find an answer to that in the sources you can access."
#: the same rules as tests/test_standin_labels.py: names a neutral label never shows
PROVIDER_TERMS = r"claude|anthropic|opus|sonnet|haiku"
HY3_TERMS = r"hy3|hunyuan|tokenhub|混元"
TOKENHUB = "tencent-tokenhub"
EXTRACTIVE_MODEL = "extractive-v1"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
#: keys whose values are ids, links or clocks: never searched for terms
_SKIP_KEYS = {"request_id", "req_id", "action_id", "proposal_id", "deliverable_id", "view_id",
              "doc_id", "deep_link", "version", "cited_version", "surfaceId", "catalogId",
              "as_of", "expires_at", "tag", "tags", "id"}
ACTIONS = {"draft", "ticket_propose", "deliverable", "view"}


class StepsError(ValueError):
    """The steps file is malformed (exit 2)."""


# -- transport ----------------------------------------------------------------------------------
class _Resp:
    def __init__(self, status: int, headers: dict, body: bytes):
        self.status_code = status
        self.headers = headers
        self.text = body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text)


class HttpClient:
    """A minimal stdlib client for a running Internal Brain (no cookies kept: bearer tokens)."""

    def __init__(self, base: str, timeout_s: float = 120.0):
        self.base = base.rstrip("/")
        self.timeout_s = timeout_s

    def request(self, method: str, path: str, json: Any = None, headers: Optional[dict] = None):
        data = None
        hdrs = dict(headers or {})
        if json is not None:
            data = _json_dumps(json).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=hdrs)
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=self.timeout_s) as r:
                return _Resp(r.status, {k.lower(): v for k, v in r.headers.items()}, r.read())
        except urllib.error.HTTPError as e:
            return _Resp(e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):                             # noqa: D401
        return None


def _json_dumps(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False)


# -- the P2 strip rule ----------------------------------------------------------------------------
def p2_body(resp: dict) -> str:
    """The response with only its per-request identity and clock fields removed (the P2 rule:
    request_id, as_of and every citation's *_checked_at), canonical JSON."""
    d = json.loads(json.dumps(resp))
    d.pop("request_id", None)
    d.pop("as_of", None)
    for c in d.get("citations") or []:
        for k in [k for k in c if k.endswith("_checked_at")]:
            c.pop(k)
    return json.dumps(d, sort_keys=True, ensure_ascii=False)


# -- term matching --------------------------------------------------------------------------------
def visible_strings(obj: Any, key: Optional[str] = None) -> list[str]:
    """Every string value a person could see, skipping ids, links and clocks."""
    if key is not None and (key in _SKIP_KEYS or key.endswith("_at")):
        return []
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for k, v in obj.items() for s in visible_strings(v, str(k))]
    if isinstance(obj, (list, tuple)):
        return [s for v in obj for s in visible_strings(v)]
    return []


def _term_rx(term: str) -> re.Pattern:
    if len(term) > 2 and term.startswith("/") and term.endswith("/"):
        return re.compile(term[1:-1], re.I)
    return re.compile(re.escape(term), re.I)


def find_term(term: str, texts: list[str]) -> Optional[str]:
    """The first snippet around a hit of ``term`` in ``texts``, or None."""
    rx = _term_rx(term)
    for t in texts:
        m = rx.search(t)
        if m:
            a, b = max(0, m.start() - 30), min(len(t), m.end() + 30)
            return t[a:b].replace("\n", " ")
    return None


# -- the run --------------------------------------------------------------------------------------
class Check:
    def __init__(self, name: str, ok: Optional[bool], detail: str = ""):
        self.name, self.ok, self.detail = name, ok, detail      # ok None = skipped (with a reason)

    def as_dict(self) -> dict:
        return {"name": self.name, "status": {True: "pass", False: "fail", None: "skip"}[self.ok],
                "detail": self.detail}


class Runner:
    def __init__(self, client: Any, doc: dict, *, passcode: Optional[str] = None):
        self.c = client
        self.doc = doc
        self.passcode = passcode
        self.auditor = doc.get("auditor") or DEFAULT_AUDITOR
        self.refusal_text = doc.get("refusal_text") or REFUSAL_TEXT
        extra = doc.get("provider_terms")
        self.provider_rx = re.compile(PROVIDER_TERMS + (f"|{extra}" if extra else ""), re.I)
        self.hy3_rx = re.compile(HY3_TERMS, re.I)
        self.tokens: dict[str, str] = {}
        self.status: Optional[dict] = None
        self.results: dict[str, dict] = {}      # step id -> the recorded responses

    # -- HTTP helpers --
    def token(self, persona: str) -> str:
        if persona not in self.tokens:
            body = {"persona": persona}
            if self.passcode:
                body["passcode"] = self.passcode
            r = self.c.request("POST", "/auth/demo-login", json=body)
            if r.status_code != 200:
                raise RuntimeError(f"demo sign-in as {persona} failed: HTTP {r.status_code} "
                                   f"{r.text[:200]} (demo sign-in on? IB_DEMO_PASSCODE set?)")
            tok = _cookie(r.headers, "ib_session")
            if not tok:
                raise RuntimeError(f"demo sign-in as {persona} returned no session cookie")
            self.tokens[persona] = tok
        return self.tokens[persona]

    def call(self, persona: str, method: str, path: str, body: Any = None):
        h = {"Authorization": "Bearer " + self.token(persona)}
        return self.c.request(method, path, json=body, headers=h)

    def ib_status(self, persona: str) -> dict:
        if self.status is None:
            r = self.call(persona, "GET", "/ib/status")
            self.status = r.json() if r.status_code == 200 else {"error": r.status_code}
        return self.status

    # -- one step --
    def run_step(self, step: dict) -> dict:
        sid, persona = step["id"], step["persona"]
        exp = step.get("expect") or {}
        checks: list[Check] = []
        out: dict[str, Any] = {"id": sid, "persona": persona, "question": step.get("question"),
                               "checks": checks}
        resp = None
        if step.get("question"):
            t0 = time.monotonic()
            r = self.call(persona, "POST", "/ib/ask", {"question": step["question"]})
            out["latency_ms"] = int((time.monotonic() - t0) * 1000)
            if r.status_code != 200:
                checks.append(Check("ask", False, f"HTTP {r.status_code}: {r.text[:200]}"))
            else:
                resp = r.json()
                checks.append(Check("ask", True, "HTTP 200"))
                out.update(request_id=resp.get("request_id"), answer=resp.get("answer"),
                           model=resp.get("model"),
                           citations=[c.get("doc_id") for c in resp.get("citations") or []])
        self.results[sid] = {"resp": resp, "persona": persona, "exp": exp}
        if resp is not None:
            checks += self.answer_checks(persona, resp, exp, out.get("latency_ms"))
            if step.get("same_as"):
                checks.append(self.same_as(resp, step["same_as"]))
        elif step.get("question") is None and step.get("same_as"):
            checks.append(Check("same_as", False, "a same_as step needs a question"))
        act = step.get("action")
        if act:
            if resp is None:
                checks.append(Check(f"action.{act.get('type')}", False, "no answer to act on"))
            else:
                checks += self.action_checks(persona, resp, act)
        if "ledger" in exp:
            checks += self.ledger_checks(step, exp["ledger"], resp)
        out["ok"] = all(c.ok is not False for c in checks) and any(c.ok for c in checks)
        out["checks"] = [c.as_dict() for c in checks]
        return out

    # -- answer checks (shared with actions) --
    def answer_checks(self, persona: str, resp: dict, exp: dict, latency: Optional[int],
                      prefix: str = "") -> list[Check]:
        cs: list[Check] = []
        texts = visible_strings(resp)
        answer = str(resp.get("answer") or resp.get("text") or "")
        cites = list(dict.fromkeys(c.get("doc_id") for c in resp.get("citations") or []
                                   if isinstance(c, dict) and c.get("doc_id")))
        for term in exp.get("contains") or []:
            hit = find_term(term, texts)
            cs.append(Check(f"{prefix}contains", hit is not None,
                            f"{term!r}" + ("" if hit else " not found")))
        for term in exp.get("not_contains") or []:
            hit = find_term(term, texts)
            cs.append(Check(f"{prefix}not_contains", hit is None,
                            f"{term!r}" + (f" found: ...{hit}..." if hit else " absent")))
        if "refusal" in exp:
            refused = answer.strip() == self.refusal_text and not cites
            want = bool(exp["refusal"])
            cs.append(Check(f"{prefix}refusal", refused == want,
                            f"expected {'a' if want else 'no'} refusal, got "
                            f"{'the refusal' if refused else 'an answer'} ({len(cites)} cited)"))
        if "citations_min" in exp:
            n = int(exp["citations_min"])
            cs.append(Check(f"{prefix}citations_min", len(cites) >= n,
                            f"{len(cites)} cited document(s), need >= {n}"))
        if exp.get("cite_recheck"):
            cs.append(self.cite_recheck(persona, cites, prefix))
        if exp.get("model_label"):
            cs.append(self.model_label(persona, resp, answer, exp["model_label"], prefix))
        if "model_answered" in exp:
            m = resp.get("model") or {}
            by_model = m.get("provider") not in (None, "deterministic")
            want = bool(exp["model_answered"])
            cs.append(Check(f"{prefix}model_answered", by_model == want,
                            f"model {m.get('provider')}/{m.get('model')}, expected "
                            f"{'a model' if want else 'the quoted (no-model) path'}"))
        if "max_latency_ms" in exp and latency is not None:
            cs.append(Check(f"{prefix}latency", latency <= int(exp["max_latency_ms"]),
                            f"{latency} ms (max {exp['max_latency_ms']})"))
        return cs

    def cite_recheck(self, persona: str, cites: list[str], prefix: str = "") -> Check:
        name = f"{prefix}cite_recheck"
        if not cites:
            return Check(name, True, "nothing cited, nothing to re-check")
        r = self.call(persona, "POST", "/ib/graph/openable", {"doc_ids": cites})
        if r.status_code != 200:
            return Check(name, False, f"/ib/graph/openable HTTP {r.status_code}")
        openable = set(r.json().get("openable") or [])
        missing = [d for d in cites if d not in openable]
        if missing:
            return Check(name, False, f"not openable by {persona} now: {missing}")
        bad = []
        for d in cites:
            rd = self.call(persona, "GET", "/ib/doc/" + urllib.parse.quote(d, safe=":"))
            if rd.status_code != 200:
                bad.append(f"{d} (HTTP {rd.status_code})")
        if bad:
            return Check(name, False, f"/ib/doc refused: {bad}")
        return Check(name, True, f"{len(cites)} cited doc(s) openable by {persona} "
                                 "(/ib/graph/openable and /ib/doc)")

    def model_label(self, persona: str, resp: dict, answer: str, rule: str,
                    prefix: str = "") -> Check:
        name = f"{prefix}model_label"
        if rule != "neutral":
            return Check(name, False, f"unknown model_label rule {rule!r}")
        st = self.ib_status(persona)
        models = st.get("models") or {}
        active = bool(models.get("active"))
        tokenhub = models.get("active_provider") == TOKENHUB
        m = resp.get("model") or {}
        label = " ".join(str(m.get(k) or "") for k in ("provider", "model"))
        # what the web shows keys off the ACTIVE provider and chain; ``chain`` lists configured
        # model ids (a key may be configured but unused), so it is not a label anyone sees
        status_text = json.dumps({k: models.get(k) for k in ("active_provider", "active_chain")},
                                 ensure_ascii=False)
        problems = []
        for where, text in (("model label", label), ("answer", answer), ("/ib/status", status_text)):
            hit = self.provider_rx.search(text)
            if hit:
                problems.append(f"provider name {hit.group(0)!r} in the {where}")
            hit = self.hy3_rx.search(text) if not tokenhub else None
            if hit:
                problems.append(f"{hit.group(0)!r} in the {where} but the active provider is "
                                f"{models.get('active_provider')!r}")
        by_model = m.get("provider") not in (None, "deterministic")
        if by_model:
            if re.search(r"no model", answer + " " + label, re.I):
                problems.append("'no model' shown although a model answered")
            if m.get("model") == EXTRACTIVE_MODEL:
                problems.append("labelled extractive although a model answered")
            if not active:
                problems.append("a model label although /ib/status says no model is active")
        if problems:
            return Check(name, False, "; ".join(problems))
        return Check(name, True, f"{m.get('provider')}/{m.get('model')} "
                                 f"(status active={active}, provider={models.get('active_provider')})")

    def same_as(self, resp: dict, other: str) -> Check:
        prev = (self.results.get(other) or {}).get("resp")
        if prev is None:
            return Check("same_as", False, f"step {other!r} has no answer to compare with")
        a, b = p2_body(resp), p2_body(prev)
        if a == b:
            return Check("same_as", True, f"byte-identical to {other} ({len(a)} bytes, P2 strip)")
        i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))
        return Check("same_as", False, f"differs from {other} at byte {i}: "
                                       f"{a[max(0, i - 40):i + 40]!r} vs {b[max(0, i - 40):i + 40]!r}")

    # -- actions --
    def action_checks(self, persona: str, resp: dict, act: dict) -> list[Check]:
        typ = act.get("type")
        name = f"action.{typ}"
        rid = resp.get("request_id")
        if typ == "draft":
            path, body = "/ib/act/draft", {"req_id": rid, "kind": act.get("kind", "reply")}
        elif typ == "ticket_propose":
            path, body = "/ib/act/ticket/propose", {"req_id": rid}
        elif typ == "deliverable":
            path, body = "/ib/deliverables", {"req_id": rid, "kind": act.get("kind", "table"),
                                              "chart": act.get("chart", "auto")}
        elif typ == "view":
            path, body = "/ib/views", {"req_id": rid, "instruction": act.get("instruction", "")}
        else:
            return [Check(name, False, f"unknown action type {typ!r}")]
        aexp = act.get("expect") or {}
        want_code = int(aexp.get("status_code", 200))
        r = self.call(persona, "POST", path, body)
        cs = [Check(name, r.status_code == want_code,
                    f"HTTP {r.status_code} (expected {want_code})"
                    + (f": {r.text[:160]}" if r.status_code != want_code else ""))]
        if r.status_code != want_code:
            if r.status_code == 404 and typ in ("deliverable", "view"):
                dv = (self.ib_status(persona).get("deliverables") or {}).get("enabled")
                if dv is False:
                    cs[0].detail += " (deliverables are off on this server: IB_DELIVERABLES=1)"
            return cs
        if r.status_code != 200:
            return cs
        out = r.json()
        p = f"{name}."
        if aexp.get("grounded", True):
            cs.append(self.grounded(typ, out, resp, p))
        cs += self.answer_checks(persona, out, aexp, None, prefix=p)
        return cs

    @staticmethod
    def grounded(typ: str, out: dict, resp: dict, p: str) -> Check:
        answer_docs = {c.get("doc_id") for c in resp.get("citations") or []}
        docs = {c.get("doc_id") for c in out.get("citations") or [] if isinstance(c, dict)}
        problems = []
        if not docs:
            problems.append("cites nothing")
        extra = sorted(d for d in docs - answer_docs if d)
        if extra:
            problems.append(f"cites documents the answer did not: {extra}")
        if typ == "draft" and out.get("sent") is not False:
            problems.append("the draft is not marked unsent")
        if typ in ("deliverable", "view") and out.get("status") != "ready":
            problems.append(f"status {out.get('status')!r}, not 'ready'")
        if typ == "ticket_propose" and out.get("status") != "proposed":
            problems.append(f"status {out.get('status')!r}, not 'proposed'")
        if problems:
            return Check(p + "grounded", False, "; ".join(problems))
        return Check(p + "grounded", True, f"{len(docs)} cited doc(s), all cited by the answer")

    # -- ledger --
    def ledger_checks(self, step: dict, spec: Any, resp: Optional[dict]) -> list[Check]:
        if isinstance(spec, list):
            spec = {"kinds": spec}
        of = spec.get("of")
        target = self.results.get(of) if of else {"resp": resp, "persona": step["persona"],
                                                     "exp": step.get("expect") or {}}
        if target is None or target.get("resp") is None:
            return [Check("ledger", False, f"no request to look up ({'step ' + of if of else 'this step'} "
                                           "has no answer)")]
        rid = target["resp"].get("request_id")
        auditor = spec.get("auditor") or self.auditor
        r = self.call(auditor, "GET", "/audit/journey/" + urllib.parse.quote(rid, safe=""))
        if r.status_code != 200:
            return [Check("ledger", False, f"/audit/journey as {auditor}: HTTP {r.status_code}")]
        j = r.json()
        kinds = [e.get("kind") for e in j.get("events") or []]
        cs = []
        want = list(spec.get("kinds") or [])
        it = iter(kinds)
        in_order = all(any(k == w for k in it) for w in want)
        cs.append(Check("ledger.kinds", in_order,
                        f"journey {kinds}" + ("" if in_order else f"; expected in order {want}")))
        sub = spec.get("sub") or target["persona"]
        cs.append(Check("ledger.asker", j.get("sub") == sub, f"asker {j.get('sub')!r}, expected {sub!r}"))
        texp = target.get("exp") or {}
        if "refusal" in texp:
            cs.append(Check("ledger.refused", j.get("refused") is bool(texp["refusal"]),
                            f"journey refused={j.get('refused')}"))
        answer = str(target["resp"].get("answer") or "")
        if len(answer) >= 20 and answer.strip() != self.refusal_text:
            leaked = answer[:80] in json.dumps(j, ensure_ascii=False)
            cs.append(Check("ledger.no_answer_text", not leaked,
                            "the answer text is in the journey" if leaked else
                            "no answer text in the journey"))
        if spec.get("verify_chain"):
            v = self.call(auditor, "GET", "/audit/verify")
            ok = v.status_code == 200 and v.json().get("ok") is True
            cs.append(Check("ledger.verify_chain", ok,
                            f"/audit/verify HTTP {v.status_code} ok={v.json().get('ok') if v.status_code == 200 else None}"))
        return cs


def _cookie(headers: Any, name: str) -> Optional[str]:
    vals = []
    get_list = getattr(headers, "get_list", None)
    if callable(get_list):
        vals = get_list("set-cookie")
    else:
        v = headers.get("set-cookie") if hasattr(headers, "get") else None
        vals = [v] if v else []
    for v in vals:
        for part in re.split(r",(?=\s*\w+=)", v):
            k, _, rest = part.strip().partition("=")
            if k == name:
                return rest.split(";", 1)[0]
    return None


# -- the steps file -------------------------------------------------------------------------------
def load_steps(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise StepsError("YAML steps need PyYAML (pip install pyyaml), or use JSON") from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    return normalise(data)


def normalise(data: Any) -> dict:
    doc = {"steps": data} if isinstance(data, list) else dict(data or {})
    steps = doc.get("steps")
    if not isinstance(steps, list) or not steps:
        raise StepsError("no steps")
    seen = set()
    for i, s in enumerate(steps):
        if not isinstance(s, dict) or not s.get("id") or not s.get("persona"):
            raise StepsError(f"step {i}: id and persona are required")
        if s["id"] in seen:
            raise StepsError(f"duplicate step id {s['id']!r}")
        for ref in _deps(s):
            if ref not in seen:
                raise StepsError(f"step {s['id']!r} refers to {ref!r}, which is not an earlier step")
        seen.add(s["id"])
        act = s.get("action")
        if act is not None and (not isinstance(act, dict) or act.get("type") not in ACTIONS):
            raise StepsError(f"step {s['id']!r}: action.type must be one of {sorted(ACTIONS)}")
        if not s.get("question") and not (s.get("expect") or {}).get("ledger"):
            raise StepsError(f"step {s['id']!r}: needs a question (or an audit-only expect.ledger)")
    return doc


def _deps(step: dict) -> list[str]:
    out = []
    if step.get("same_as"):
        out.append(step["same_as"])
    led = (step.get("expect") or {}).get("ledger")
    if isinstance(led, dict) and led.get("of"):
        out.append(led["of"])
    return out


def select(doc: dict, only: Optional[list[str]]) -> list[dict]:
    steps = doc["steps"]
    if not only:
        return steps
    by_id = {s["id"]: s for s in steps}
    unknown = [o for o in only if o not in by_id]
    if unknown:
        raise StepsError(f"--only: unknown step id(s) {unknown}")
    want: set[str] = set()
    stack = list(only)
    while stack:
        sid = stack.pop()
        if sid not in want:
            want.add(sid)
            stack += _deps(by_id[sid])
    return [s for s in steps if s["id"] in want]


def run_steps(doc: dict, client: Any, *, only: Optional[list[str]] = None,
              passcode: Optional[str] = None) -> dict:
    """Run the steps through ``client`` and return the report (``ok`` is the overall verdict)."""
    doc = normalise(doc)
    runner = Runner(client, doc, passcode=passcode)
    picked = select(doc, only)
    rows = []
    for s in picked:
        try:
            rows.append(runner.run_step(s))
        except RuntimeError as exc:                                 # sign-in failed etc.
            rows.append({"id": s["id"], "persona": s["persona"], "question": s.get("question"),
                         "ok": False, "checks": [Check("run", False, str(exc)).as_dict()]})
    return {"ok": all(r["ok"] for r in rows), "passed": sum(r["ok"] for r in rows),
            "total": len(rows), "only": only or None, "status": runner.status, "steps": rows}


def format_table(report: dict) -> str:
    lines = [f"{'STEP':<26} {'PERSONA':<16} {'RESULT':<6} DETAIL", "-" * 100]
    for r in report["steps"]:
        fails = [c for c in r["checks"] if c["status"] == "fail"]
        n = len([c for c in r["checks"] if c["status"] == "pass"])
        detail = ("; ".join(f"{c['name']}: {c['detail']}" for c in fails) if fails
                  else f"{n} check(s) passed")
        lines.append(f"{r['id']:<26} {r['persona']:<16} {'PASS' if r['ok'] else 'FAIL':<6} {detail}")
    lines.append("-" * 100)
    lines.append(f"{report['passed']}/{report['total']} steps passed: "
                 f"{'ALL PASS' if report['ok'] else 'FAILED'}")
    return "\n".join(lines)


def _check_base(base: str, allow_remote: bool) -> None:
    u = urllib.parse.urlparse(base)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise SystemExit(f"--base must be an http(s) URL, got {base!r}")
    if u.hostname not in LOOPBACK:
        if not allow_remote:
            raise SystemExit(f"refusing non-loopback --base {base} (pass --allow-remote, https only)")
        if u.scheme != "https":
            raise SystemExit("a remote --base must be https (the demo passcode is sent to it)")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Run a data-driven list of workflow steps through a running Internal Brain's "
                    "HTTP API and check each one (see the module docstring for the step schema).")
    ap.add_argument("steps", type=Path, help="steps file (.json, or .yaml/.yml with PyYAML)")
    ap.add_argument("--base", default=os.environ.get("IB_BASE_URL", DEFAULT_BASE),
                    help=f"Internal Brain base URL (default {DEFAULT_BASE}, or IB_BASE_URL)")
    ap.add_argument("--out", type=Path, help="write the JSON report here")
    ap.add_argument("--only", help="comma-separated step ids (their dependencies run too)")
    ap.add_argument("--allow-remote", action="store_true",
                    help="allow a non-loopback --base (https only)")
    ap.add_argument("--timeout", type=float, default=120.0, help="per-request timeout, seconds")
    a = ap.parse_args(argv)
    _check_base(a.base, a.allow_remote)
    try:
        doc = load_steps(a.steps)
    except (OSError, ValueError) as exc:
        print(f"steps file: {exc}", file=sys.stderr)
        return 2
    client = HttpClient(a.base, a.timeout)
    try:
        h = client.request("GET", "/health")
        if h.status_code != 200:
            raise OSError(f"HTTP {h.status_code}")
    except OSError as exc:
        print(f"Internal Brain not reachable at {a.base}: {exc}", file=sys.stderr)
        return 2
    only = [s.strip() for s in a.only.split(",") if s.strip()] if a.only else None
    try:
        report = run_steps(doc, client, only=only, passcode=os.environ.get("IB_DEMO_PASSCODE") or None)
    except StepsError as exc:
        print(f"steps file: {exc}", file=sys.stderr)
        return 2
    report.update(base=a.base, steps_file=str(a.steps),
                  finished_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    print(format_table(report))
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"report: {a.out}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
