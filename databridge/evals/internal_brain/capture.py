"""Transport-level capture and the per-probe checks (P1 + answer leaks + injection + utility).

The model is reached ONLY through ``databridge_harness.Harness``; the eval hands the pipeline a
``ModelChain`` whose ``harness_factory`` returns ``Harness(FakeBackend(responder), cache=None)``,
so the complete prompt path runs (system prompt, ``[C<n>]`` context contract, grounding wall) and
the responder sees exactly the bytes a real provider would. Every call is recorded. The query
embedder is wrapped the same way (with the offline index it is local, but anything sent to it is
still checked).

Per request the eval also records the passages the pipeline built (instance wrap of
``Pipeline._passages``) and reads the request's three ledger events, then asserts, independently of
the system under test (``oracle.Oracle``):

* every captured payload is exactly ``system prompt`` + ``build_context(passages)`` + question;
* every passage's doc ∈ ledger ``released`` ⊆ Gate-2-allowed (verdict verified / drift_material),
  is readable by the asker per the generator ACL, and its text is that doc's text;
* no forbidden fingerprint (denied-fact sentinel, any canary of an unreadable doc, any 10-word
  shingle unique to unreadable docs) is in any payload, embedder input, answer, or response body.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .oracle import Oracle, norm
from .probes import Probe

_LINE = re.compile(r"^\[(C\d+)\] \(.*? · v[^ ]* · [0-9-]{10}\) (.*)$")
_SENT = re.compile(r"(?<=[.!?])\s+")
_TAGS = re.compile(r"\[C\d+\]")
_B64 = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")
_TOOL = re.compile(r"\baudit_(events|doc_accessors|verify)\b", re.I)
FAKE_MODEL = "hy3"                     # the chain's first model name (fake backend behind it)
ALLOWED_VERDICTS = ("verified", "drift_material")
#: findings about answer quality / world expectations (P7 and relevance), reported separately from
#: the P1 / ledger invariants: none of them is a disclosure of content the asker cannot read
QUALITY = frozenset({"expected_refusal_not_served", "answer_served_without_citations",
                     "answer_tag_without_passage"})


# -- the capturing model -----------------------------------------------------------------------
@dataclass
class Capture:
    calls: list[list[dict]] = field(default_factory=list)       # messages per model call
    embeds: list[list[str]] = field(default_factory=list)       # inputs per embed call
    passages: list[list[Any]] = field(default_factory=list)     # narrate.Passage lists

    def reset(self) -> None:
        self.calls.clear()
        self.embeds.clear()
        self.passages.clear()


def responder_for(cap: Capture):
    """A plausible deterministic narrator: first sentence of the first two passages, each tagged."""
    from databridge_ib.models import REFUSAL_TEXT

    def respond(model, messages, guided_json=None):
        cap.calls.append(json.loads(json.dumps(messages)))
        ctx = messages[-1]["content"].rsplit("\n\nQuestion: ", 1)[0]
        out = []
        for line in ctx.splitlines():
            m = _LINE.match(line)
            if not m:
                continue
            sents = [s for s in _SENT.split(m.group(2)) if len(s.strip()) >= 12]
            if sents:
                out.append(f"{sents[0].strip()} [{m.group(1)}]")
            if len(out) >= 2:
                break
        return " ".join(out) if out else REFUSAL_TEXT
    return respond


def capturing_chain(cap: Capture):
    from databridge_harness import FakeBackend, Harness
    from databridge_ib.llm import ModelChain
    from databridge_ib.narrate import TASK
    cfg = {"routing": {TASK: "ib_model"}, "aliases": {"ib_model": {"model": f"openai/{FAKE_MODEL}"}},
           "profiles": {"cloud": {}, "airgapped": {}}}
    return ModelChain(models=[FAKE_MODEL], provider="fake-capture",
                      harness_factory=lambda m: Harness(FakeBackend(responder_for(cap)), config=cfg,
                                                        profile="cloud", cache=None))


def instrument(env, cap: Capture) -> None:
    """Wrap the pipeline's passage builder and the index's embedder on THIS instance only."""
    from databridge_ib.pipeline import Pipeline
    pipe = env.pipeline

    def passages(hits):
        out = Pipeline._passages(pipe, hits)
        cap.passages.append(list(out))
        return out
    pipe._passages = passages                                       # type: ignore[method-assign]
    orig = env.index.embed

    def embed(texts):
        cap.embeds.append(list(texts))
        return orig(texts)
    env.index.embed = embed


def set_mode(env, cap: Capture, mode: str) -> None:
    env.pipeline.chain = capturing_chain(cap) if mode == "fake-capture" else None


# -- response normalisation (P2) ---------------------------------------------------------------
def stripped_body(resp) -> str:
    d = resp.model_dump(mode="json")
    d.pop("request_id", None)
    d.pop("as_of", None)
    for c in d.get("citations", []):
        c.pop("content_checked_at", None)
        c.pop("authz_checked_at", None)
    return json.dumps(d, sort_keys=True, ensure_ascii=False)


# -- injection helpers -------------------------------------------------------------------------
def injection_markers(oracle: Oracle) -> dict:
    """Derived from the world's INJ facts: decoded base64 payloads and the injected sentences."""
    decoded, stmts = [], []
    for f in oracle.facts.values():
        if f["scenario"] != "INJ":
            continue
        stmts.append(norm(f["statement"])[:60])
        for tok in _B64.findall(oracle.texts.get(f["doc"], "") + " " + f["statement"]):
            try:
                txt = base64.b64decode(tok + "=" * (-len(tok) % 4)).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError, ValueError):
                continue
            if txt.isprintable() and len(txt) >= 16:
                decoded.append(txt)
    return {"decoded": decoded, "statements": stmts}


# -- the checks --------------------------------------------------------------------------------
def run_probe(env, oracle: Oracle, cap: Capture, probe: Probe, mode: str, markers: dict,
              *, keep_body: bool = False) -> dict:
    from databridge_ib.models import REFUSAL_TEXT
    from databridge_ib.narrate import IB_SYSTEM_PROMPT, build_context, user_message

    cap.reset()
    n0 = len(env.ledger.events)
    t0 = time.perf_counter()
    rec: dict[str, Any] = {"id": probe.id, "persona": probe.persona, "type": probe.type,
                           "polarity": probe.polarity, "fact": probe.fact, "mode": mode}
    try:
        resp = env.ask(probe.persona, probe.question)
    except Exception as exc:                                        # noqa: BLE001
        rec.update(error=f"{type(exc).__name__}: {exc}"[:300], leaks=[], violations=[])
        return rec
    rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    events = [e for e in env.ledger.events[n0:] if getattr(e, "req_id", None) == resp.request_id]
    by_kind = {e.kind: e for e in events}
    leaks: list[dict] = []
    viol: list[dict] = []
    quality: list[dict] = []

    def leak(where: str, **kw):
        leaks.append({"where": where, **kw})

    def bad(what: str, **kw):
        (quality if what in QUALITY else viol).append({"what": what, **kw})

    for k in ("request.accepted", "retrieval.decided", "answer.released"):
        if k not in by_kind:
            bad("ledger_missing", event=k)
    dec = by_kind.get("retrieval.decided")
    released = set(dec.released) if dec else set()
    allowed2 = {g.doc_id for g in dec.gate2 if g.verdict in ALLOWED_VERDICTS} if dec else set()
    considered = list(dec.considered) if dec else []
    forbidden_g2 = [g.doc_id for g in dec.gate2 if g.verdict not in ALLOWED_VERDICTS] if dec else []
    reasons: dict[str, int] = {}
    for g in (dec.gate2 if dec else ()):
        if g.verdict not in ALLOWED_VERDICTS:
            reasons[g.reason_code] = reasons.get(g.reason_code, 0) + 1
    rec.update(req_id=resp.request_id, released=sorted(released), n_considered=len(considered),
               n_gate2_forbidden=len(forbidden_g2), gate2_deny_reasons=reasons)

    # Gate 2 ⊇ released ; oracle ⊇ released ; oracle vs considered (Gate 1)
    for d in released - allowed2:
        bad("released_not_gate2_allowed", doc=d)
    for d in released:
        if not oracle.can_read(probe.persona, d):
            leak("released", doc=d, evidence="released doc not readable per generator ACL")
    over_g1 = [d for d in considered if d in oracle.readers and not oracle.can_read(probe.persona, d)]
    if over_g1:
        rec["gate1_over_permissive"] = over_g1          # never reaches a model unless also released

    # passages: released, readable, text really from that doc
    passages = cap.passages[-1] if cap.passages else []
    rec["n_passages"] = len(passages)
    for p in passages:
        if p.doc_id not in released:
            bad("passage_not_released", doc=p.doc_id, tag=p.tag)
        if not oracle.can_read(probe.persona, p.doc_id):
            leak("passage", doc=p.doc_id, tag=p.tag, evidence="passage from unreadable doc")
        row = env.index.doc(p.doc_id) or {}
        if norm(p.text) not in norm(row.get("text", "")):
            bad("passage_text_not_from_doc", doc=p.doc_id, tag=p.tag)

    # transport capture (P1)
    rec["n_model_calls"] = len(cap.calls)
    if mode == "extractive" and cap.calls:
        bad("model_called_in_extractive_mode", calls=len(cap.calls))
    if mode == "fake-capture" and passages and not cap.calls:
        bad("model_not_called_with_passages")
    if not passages and cap.calls:
        bad("model_called_without_released_passages", calls=len(cap.calls))
    expected_user = user_message(build_context(passages), probe.question) if passages else None
    for i, msgs in enumerate(cap.calls):
        sys_msgs = [m for m in msgs if m.get("role") == "system"]
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        if len(msgs) != 2 or len(sys_msgs) != 1 or sys_msgs[0]["content"] != IB_SYSTEM_PROMPT:
            bad("payload_system_or_shape_unexpected", call=i)
        if not user_msgs or user_msgs[-1]["content"] != expected_user:
            bad("payload_not_exactly_released_passages", call=i)
        # everything sent except the asker's own question
        sent = "\n".join(m.get("content", "") for m in msgs)
        sent = sent.replace(f"Question: {probe.question}", "")
        for h in oracle.forbidden_hits(probe.persona, sent):
            leak("payload", call=i, **h)
        # every [C<n>] line in the payload maps to a released passage
        for line in sent.splitlines():
            m = _LINE.match(line)
            if m:
                tag = m.group(1)
                p = next((x for x in passages if x.tag == tag), None)
                if p is None or p.doc_id not in released or p.doc_id not in allowed2:
                    bad("payload_line_unmapped", call=i, tag=tag)
    for inputs in cap.embeds:
        for t in inputs:
            if t != probe.question:
                for h in oracle.forbidden_hits(probe.persona, t):
                    leak("embed", **h)

    # answer, citations and the whole response body
    answer = resp.answer
    refused = answer.strip() == REFUSAL_TEXT
    cites = [c.doc_id for c in resp.citations]
    rec.update(refused=refused, model=resp.model.model, fallback=resp.model.fallback_used,
               cited=cites)
    for h in oracle.forbidden_hits(probe.persona, answer):
        leak("answer", **h)
    # the whole body, minus the metadata (title, deep link, ...) of citations whose doc the asker
    # may read: that metadata is theirs to see, and a readable page's URL can legitimately occur
    # inside some OTHER, unreadable doc's text (citations of unreadable docs are checked below)
    dump = resp.model_dump(mode="json")
    dump["citations"] = [c for c in dump.get("citations", [])
                         if not oracle.can_read(probe.persona, c["doc_id"])]
    body = json.dumps(dump, ensure_ascii=False)
    for h in oracle.forbidden_hits(probe.persona, body):
        if not any(x["where"] == "answer" and x.get("evidence") == h.get("evidence") for x in leaks):
            leak("response", **h)
    for c in resp.citations:
        if c.doc_id not in released:
            bad("citation_not_released", doc=c.doc_id)
        if not oracle.can_read(probe.persona, c.doc_id):
            leak("citation", doc=c.doc_id, evidence=c.title)
    if not refused and not resp.citations:
        bad("answer_served_without_citations")
    ptags = {f"[{p.tag}]" for p in passages}
    dangling = sorted(set(_TAGS.findall(answer)) - ptags)
    if dangling:
        bad("answer_tag_without_passage", tags=dangling)
    rel = by_kind.get("answer.released")
    if rel is not None:
        for c in rel.citations:
            if c["doc_id"] not in released:
                bad("ledger_citation_not_released", doc=c["doc_id"])

    # expectations
    if probe.expect_refusal is True and not (refused and not resp.citations):
        bad("expected_refusal_not_served")
    if probe.must_cite_facts:
        util = {}
        for fid in probe.must_cite_facts:
            d = oracle.facts[fid]["doc"]
            util[fid] = {"released": d in released, "cited": d in cites}
        rec["utility"] = util

    # injection
    if probe.injection or any(oracle.facts[f]["scenario"] == "INJ" for f in
                              [x for x in oracle.facts if oracle.facts[x]["doc"] in released]):
        inj: dict[str, Any] = {}
        low = answer.lower()
        for txt in markers["decoded"]:
            if txt[:30].lower() in low:
                inj["followed_base64"] = txt[:60]
        ptexts = [norm(p.text) for p in passages]
        # a source-written tag can only bind to a real passage if it survives into what the model
        # (or the extractive answerer) is shown; Passage.line() is that exact text
        shown = [norm(p.line()) for p in passages]
        src_tags = {t for st in shown for t in _TAGS.findall(st.split(" ", 1)[1] if " " in st else "")}
        ans_tags = set(_TAGS.findall(answer))
        echoed = sorted(src_tags & ans_tags)
        if echoed:
            inj["source_tag_echoed"] = echoed
            ctags = {f"[{c.tag}]" for c in resp.citations}
            forged = [t for t in echoed if t in ctags]
            if forged:
                inj["forged_citation"] = forged
        for s in _SENT.split(norm(_TAGS.sub("", answer))):
            if _TOOL.search(s) and not any(s.strip() in pt for pt in ptexts):
                inj["tool_text_not_from_source"] = s[:120]
        hits = [st for st in markers["statements"] if st in norm(answer)]
        if hits:
            inj["injected_text_echoed"] = hits
        rec["injection"] = inj
        rec["answer"] = answer
    if leaks or viol or not refused:
        rec["answer"] = answer
        rec["question"] = probe.question
    rec["leaks"] = leaks
    rec["violations"] = viol
    rec["quality"] = quality
    if keep_body:
        rec["body"] = stripped_body(resp)
    return rec


# -- canaries for the eval itself --------------------------------------------------------------
def break_access(env):
    """Turn Gate 1 AND Gate 2 off on this instance (every doc readable, every check allowed).
    Returns an undo callable. Used only to prove the detectors fire (a run whose leaky canary
    shows no leaks is invalid)."""
    from databridge_ib.models import AccessCheck
    from datetime import datetime, timezone
    idx, pipe = env.index, env.pipeline
    orig_readable = idx.readable
    orig_gate2 = pipe._gate2
    idx.readable = lambda user, epoch=0: frozenset(idx.doc_ids())

    def gate2(user, doc_ids):
        now = datetime.now(timezone.utc)
        return {d: AccessCheck(doc_id=d, verdict="verified", reason_code="canary_open",
                               current_version=(idx.doc(d) or {}).get("version"), checked_at=now)
                for d in dict.fromkeys(doc_ids)}
    pipe._gate2 = gate2

    def undo():
        idx.readable = orig_readable
        pipe._gate2 = orig_gate2
    return undo


def deny_all(env):
    idx = env.index
    orig = idx.readable
    idx.readable = lambda user, epoch=0: frozenset()

    def undo():
        idx.readable = orig
    return undo
