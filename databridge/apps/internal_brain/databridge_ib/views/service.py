"""The view service: a page for one of the asker's OWN answers, decided and built just in time.

Two calls, one route (``POST /ib/views``):

* ``decide``: does this answer need a page, or does it stay in the chat? Claude decides when it is
  connected (composer.py); otherwise the rules do (``plan.wants_page``). A question too short to need
  a page stays in the chat without reading anything.
* ``build``: the page. Claude lays it out (blocks, order, headings, tone, accent) when connected;
  every value it proposes is checked against the sources (pagecheck.py). Without Claude, or when
  its page does not pass, the rules build it (plan.py, blocks.py).

Built on the deliverables' rules and records (``deliverables/service.py``), in the same order:

1. rate limit (per person; over it: ``deliverable.failed`` stage ``rate``, no ids, 429);
2. ownership (``projection.owns``; no source reads). Not mine, unknown: ``deliverable.failed`` stage
   ``lookup`` WITHOUT any id, one 404;
3. LEDGER ``deliverable.requested`` (``deliverable="view"``) before any live source read and before
   any model call;
4. ``ActionService.context`` (sealed-answer check + live Gate 1/Gate 2/read-through of every cited
   document). None: one 404. Any error: 503;
5. ``build`` with Claude only: more of the asker's documents on the question's topic, found over
   their permitted set only (Gate 1) and each checked live (Gate 2 + read-through), up to
   ``MAX_EXTRA``: "everything about the project" needs more than an answer's few citations;
6. the decision or the layout; after a model call the documents are authorised AGAIN, and when one
   is no longer openable or has moved on, the page is rebuilt by rules from those that still are;
7. the response (A2UI v0.9 messages, ``a2ui.py``) is built privately, its canonical digest computed
   once, LEDGER ``deliverable.created`` (status ``page``/``chat`` for a decision), then returned.

The person's request is never written to the ledger (counts, ids and digests only), and never
reaches a source or a check. A view is not stored and has no export.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional

from .. import actions as actmod
from .. import authz
from ..config import POLICY_VERSION
from ..deliverables.evidence import build_evidence, context_text
from ..deliverables.model import structured
from ..deliverables.service import (NotFound, RateLimited, Unavailable, DeliverableService,
                                    _Limiter, canonical)
from ..deliverables.verify import Check, words
from ..models import (SCOPE_NOTE, DeliverableCreated, DeliverableFailed, DeliverableRequested,
                      DirectoryUser, ModelInfo)
from ..timewin import strip as strip_window
from . import a2ui, blocks, pagecheck, plan, prompts
from .spec import MView

LABEL = "Draft for your review"
RATE_N, RATE_WINDOW_S = 10, 60.0
RULES = ModelInfo(provider="deterministic", model="view-rules-v1")
DIGEST_FIELDS = ("view_id", "status", "title", "generated", "model", "check", "a2ui", "citations")
MAX_EXTRA = 12                       # more documents for a Claude page, beyond the answer's own
PAGE_CONTEXT_CHARS = 26_000          # the evidence a Claude page may rest on (whole units)
_SPEAKER = re.compile(r"^([a-z0-9][a-z0-9._-]{1,40}):\s")


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def response_digest(resp: Mapping[str, Any]) -> str:
    return _sha(canonical({k: resp.get(k) for k in DIGEST_FIELDS}))


class ViewService:
    def __init__(self, *, actions: Any, ledger: Any, projection: Any, index: Any,
                 connectors: Mapping[str, Any], directory: Any, chain: Any = None,
                 composer: Any = None, enabled: bool = False,
                 sync: Optional[Callable[[], None]] = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.actions, self.ledger, self.projection = actions, ledger, projection
        self.index, self.connectors, self.directory = index, connectors, directory
        self.chain, self.composer = chain, composer
        self.enabled, self.sync, self.clock = bool(enabled), sync, clock
        self._rate = _Limiter(RATE_N, RATE_WINDOW_S)

    def status(self) -> dict[str, Any]:
        model = self.enabled and (self.chain is not None or self.composer is not None)
        out: dict[str, Any] = {"enabled": self.enabled, "model": model,
                               "blocks": [b for b in plan.BLOCKS] if self.enabled else []}
        if self.enabled and self.composer is not None:
            c = self.composer.status()
            out["composer"] = {"provider": c["provider"], "model": c["model"],
                               "available": c["available"]}
        return out

    # -- ledger -------------------------------------------------------------------------------
    def _append(self, event) -> None:
        try:
            self.ledger.append(event)
        except Exception as exc:                                    # noqa: BLE001
            raise Unavailable(type(exc).__name__) from None

    def _fail(self, user: DirectoryUser, stage: str, reason: str, *, req_id: Optional[str] = None,
              view_id: Optional[str] = None) -> None:
        self._append(DeliverableFailed(sub=user.id, stage=stage, reason_code=reason[:80],
                                       req_id=req_id, deliverable_id=view_id,
                                       deliverable="view" if view_id else None,
                                       event_time=self.clock()))

    def _answer_citations(self, req_id: str) -> list[dict[str, Any]]:
        try:
            res = self.projection.reconstruct(req_id) or {}
        except Exception:                                           # noqa: BLE001
            return []
        return [c for c in res.get("citations") or () if isinstance(c, dict)]

    # -- the shared start: rate, ownership, write-ahead, live context ---------------------------
    def _open(self, user: DirectoryUser, req_id: str, *, planned: bool):
        if not self._rate.allow(user.id):
            self._fail(user, "rate", "rate_limited")
            raise RateLimited()
        if not isinstance(req_id, str) or not actmod.ID_RE.match(req_id) or self.projection is None:
            self._fail(user, "lookup", "not_found")
            raise NotFound()
        try:
            if self.sync is not None:
                self.sync()
            owns = self.projection.owns(req_id, user.id)
        except Exception as exc:                                    # noqa: BLE001
            raise Unavailable(type(exc).__name__) from None
        if not owns:
            self._fail(user, "lookup", "not_found")                # no ids: not their receipt
            raise NotFound()
        vid = uuid.uuid4().hex
        self._append(DeliverableRequested(sub=user.id, req_id=req_id, deliverable_id=vid,
                                          deliverable="view", model_planned=planned,
                                          policy_version=POLICY_VERSION, event_time=self.clock()))
        try:
            ctx = self.actions.context(user, req_id)
        except Exception as exc:                                    # noqa: BLE001
            try:
                self._fail(user, "generate", "context_" + type(exc).__name__, req_id=req_id,
                           view_id=vid)
            except Unavailable:
                pass
            raise Unavailable(type(exc).__name__) from None
        if ctx is None or not ctx.cites:
            self._fail(user, "lookup", "not_found", req_id=req_id, view_id=vid)
            raise NotFound()
        return vid, ctx

    def _created(self, user, req_id, vid, *, status, generated, model, check: Check, context,
                 doc_ids, fallback, digest) -> None:
        c = check.public()
        self._append(DeliverableCreated(
            sub=user.id, req_id=req_id, deliverable_id=vid, deliverable="view", status=status,
            generated=generated, model=model, prompt_version=prompts.PROMPT_VERSION,
            cited_docs=len(doc_ids), doc_ids=tuple(doc_ids),
            context_chars=len(context), context_digest=_sha(context),
            items_proposed=c["items_proposed"], items_kept=c["items_kept"],
            items_dropped=c["items_dropped"], values_proposed=c["values_proposed"],
            values_kept=c["values_kept"], values_dropped=c["values_dropped"],
            names_dropped=c["names_dropped"], fallback=fallback, digest=digest,
            policy_version=POLICY_VERSION, event_time=self.clock()))

    # -- decide ---------------------------------------------------------------------------------
    def decide(self, user: DirectoryUser, req_id: str, question_hint: str = "") -> dict[str, Any]:
        """``{"render": "page" | "chat", "title", "decided_by"}``. A question too short to need a
        page answers ``chat`` before anything is read or recorded (but after ownership)."""
        if not isinstance(req_id, str) or not actmod.ID_RE.match(req_id):
            raise NotFound()
        hint = plan.clean_instruction(question_hint)
        if self.composer is None or not plan.worth_asking(hint):
            # no model call: the rules decide on the person's own words, nothing is read
            render = "page" if plan.wants_page(hint) else "chat"
            if render == "chat":
                return {"render": "chat", "title": "", "decided_by": "rules"}
        vid, ctx = self._open(user, req_id, planned=self.composer is not None)
        evidence = build_evidence(ctx.cites, question=ctx.question, facts=ctx.facts)
        context = context_text(evidence, [f.text for f in ctx.facts])
        doc_ids = list(dict.fromkeys(s.doc_id for s in ctx.cites.values()))
        render, title, decided_by, model, fallback = "chat", "", "rules", None, "no_model"
        if self.composer is not None:
            got, info, code = self.composer.decide(user.id, ctx.question,
                                                   " ".join(f.text for f in ctx.facts), context)
            fallback = code
            if got is not None:
                allowed = blocks.allowed_words(evidence, ctx.question) | pagecheck.PAGE_WORDS
                render, decided_by, model = got.render, "model", info
                title = blocks.checked_label(got.title[:pagecheck.LIMIT], allowed,
                                             limit=pagecheck.LIMIT) or ""
        if decided_by == "rules":
            render = "page" if plan.wants_page(ctx.question) else "chat"
        resp = {"view_id": vid, "req_id": req_id, "render": render,
                "title": title or (" ".join(ctx.question.split())[:120] if render == "page" else ""),
                "decided_by": decided_by,
                "model": model.model_dump(mode="json") if model else None}
        resp = json.loads(canonical(resp))
        self._created(user, req_id, vid, status=render, generated="model" if model else "deterministic",
                      model=model or RULES, check=Check(), context=context, doc_ids=doc_ids,
                      fallback=fallback if decided_by == "rules" else "none",
                      digest=_sha(canonical(resp)))
        return copy.deepcopy(resp)

    # -- build ----------------------------------------------------------------------------------
    def create(self, user: DirectoryUser, req_id: str, instruction: str = "") -> dict[str, Any]:
        vid, ctx = self._open(user, req_id, planned=self.chain is not None or self.composer is not None)
        try:
            layout = plan.clean_instruction(instruction)
            cites = dict(ctx.cites)
            if self.composer is not None:
                cites = self._expand(user, cites, ctx.question)
            evidence = build_evidence(cites, question=ctx.question, facts=ctx.facts,
                                      max_chars=PAGE_CONTEXT_CHARS if self.composer else 12_000)
            if not evidence:
                self._fail(user, "lookup", "not_found", req_id=req_id, view_id=vid)
                raise NotFound()
            cites = {t: s for t, s in cites.items() if t in evidence}
            context = context_text(evidence, [f.text for f in ctx.facts])
            built = self._build(user, ctx, cites, evidence, layout, context)
            resp, digest = self._respond(vid, req_id, ctx, layout, built)
        except (NotFound, Unavailable, RateLimited):
            raise
        except Exception as exc:                                    # noqa: BLE001
            try:
                self._fail(user, "generate", "build_" + type(exc).__name__, req_id=req_id,
                           view_id=vid)
            except Unavailable:
                pass
            raise Unavailable(type(exc).__name__) from None
        model = built["model"]
        self._created(user, req_id, vid, status=resp["status"],
                      generated="model" if resp["generated"] == "model" else
                      "deterministic" if resp["status"] == "ready" else "none",
                      model=model, check=built["check"], context=context,
                      doc_ids=list(dict.fromkeys(s.doc_id for s in built["cites"].values())),
                      fallback=built["fallback"], digest=digest)
        return copy.deepcopy(resp)

    def _expand(self, user: DirectoryUser, cites: dict, question: str) -> dict:
        """The answer's cited documents, then up to ``MAX_EXTRA`` more of the asker's documents on
        the same subject: first the most recent ones in the same places as the cited ones (the Jira
        project, the Slack channel, the Confluence space, the Drive folder), then a search on the
        cited titles and the question. Only over their permitted set (Gate 1), each checked live
        (Gate 2 + read-through): restricted documents are never looked at, so they cannot count."""
        try:
            epoch = getattr(self.directory, "epoch", 0)
            readable = self.index.readable(user, epoch)
            have = {s.doc_id for s in cites.values()}
            places = list(dict.fromkeys((s.platform, s.container) for s in cites.values()))
            near = [d for p, c in places for d in self.index.docs_in_container(p, c)
                    if d in readable and d not in have]
            near.sort(key=lambda d: str((self.index.doc(d) or {}).get("updated_at", "")),
                      reverse=True)
            topic = " ".join([*(s.title for s in cites.values()), strip_window(question)])
            hits = self.index.search(topic, readable, k=80)
            ids = list(dict.fromkeys(near[:MAX_EXTRA * 2 // 3]
                                     + [h.doc_id for h in hits if h.doc_id not in have]))
            ids = ids[:MAX_EXTRA]
            if not ids:
                return cites
            ok, _ = authz.authorize(user, ids, index=self.index, connectors=self.connectors,
                                    readable=readable, directory_epoch=epoch)
        except Exception:                                           # noqa: BLE001
            return cites                                            # the answer's own sources
        nums = [int(t[1:]) for t in cites if t[1:].isdigit()]
        n = max(nums or [0])
        out = dict(cites)
        for d in ids:
            if d in ok and n < 99:
                n += 1
                out[f"C{n}"] = ok[d]
        return out

    def _people(self, evidence, cites) -> tuple[str, set[str]]:
        """``("handle = Name; …", allowed name words)`` for the people the evidence itself names:
        chat speakers and document authors. Nobody else."""
        handles: list[str] = []
        for tag, ev in evidence.items():
            for u in ev.units:
                m = _SPEAKER.match(u.text.strip())
                if m:
                    handles.append(m.group(1))
            author = str(getattr(cites.get(tag), "author", "") or "")
            if author:
                handles.append(author)
        pairs, names = [], set()
        for h in dict.fromkeys(handles):
            person = self.directory.get(h) if hasattr(self.directory, "get") else None
            if person is None:
                continue
            pairs.append(f"{h} = {person.name}")
            names |= set(words(person.name)) | set(words(h))
        return "; ".join(pairs[:40]), names

    def _build(self, user: DirectoryUser, ctx, cites, evidence, layout: str, context: str
               ) -> dict[str, Any]:
        title_of = {t: str(getattr(s, "title", "") or "") for t, s in cites.items()}
        kinds, explicit = plan.plan(layout)
        terms = blocks._terms(ctx.question, layout)
        check = Check()
        fallback = "no_model"
        if self.composer is not None:
            people, names = self._people(evidence, cites)
            page, info, code = self.composer.compose(user.id, ctx.question, layout, context, people)
            fallback = code
            if page is not None:
                allowed = (blocks.allowed_words(evidence, ctx.question, layout) | names
                           | pagecheck.PAGE_WORDS)
                got, title, accent = pagecheck.check_page(
                    page, evidence, cites, title_of, ctx.question, terms, allowed, names, check)
                if got:
                    got, cites2, evidence2, moved = self._reauthorise(user, got, cites, evidence)
                    if not moved:
                        return {"blocks": got, "title": title or None, "accent": accent,
                                "generated": "model", "model": info, "fallback": "none",
                                "check": check, "cites": cites, "kinds": kinds,
                                "explicit": explicit}
                    cites, evidence, fallback = cites2, evidence2, "source_changed"
                else:
                    fallback = "nothing_passed"
        elif self.chain is not None:
            parsed, info, code = structured(self.chain, prompts.system_prompt(), context,
                                            prompts.instruction(ctx.question, layout), MView)
            fallback = code
            if parsed is not None:
                spec = parsed.model_dump(mode="json")
                allowed = blocks.allowed_words(evidence, ctx.question, layout)
                got = [blocks.check_model_block(b, evidence, cites, title_of, ctx.question, terms,
                                                allowed, check) for b in spec["blocks"]]
                kept = [b for b in got if b is not None]
                if kept:
                    title = blocks.checked_label(spec.get("title", ""), allowed,
                                                 limit=blocks.MAX_TITLE)
                    kept, cites2, evidence2, moved = self._reauthorise(user, kept, cites, evidence)
                    if not moved:
                        return {"blocks": kept, "title": title, "accent": "blue",
                                "generated": "model", "model": info, "fallback": "none",
                                "check": check, "cites": cites, "kinds": kinds,
                                "explicit": explicit}
                    cites, evidence, fallback = cites2, evidence2, "source_changed"
                else:
                    fallback = "nothing_passed"
        out = self._rules(kinds, explicit, evidence, cites, title_of, ctx.question, terms)
        return {"blocks": out, "title": None, "accent": "blue", "generated": "rules",
                "model": ModelInfo(provider=RULES.provider, model=RULES.model,
                                   fallback_used=fallback not in ("none", "no_model")),
                "fallback": fallback, "check": check if fallback == "nothing_passed" else Check(),
                "cites": cites, "kinds": kinds, "explicit": explicit}

    @staticmethod
    def _rules(kinds, explicit: bool, evidence, cites, title_of, question: str, terms
               ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for k in kinds:
            if k == "sources":
                continue
            b = blocks.build_rules(k, evidence, cites, title_of, question, terms)
            if not explicit and k == "table" and b and b["table"].get("source") != "source_table":
                b = None                    # automatic layout: only a table the sources contain
            if b is not None:
                out.append(b)
            elif explicit:
                out.append({"type": "missing", "wanted": k})
        return out

    def _reauthorise(self, user, kept, cites, evidence):
        """After the model call: every document authorised again. ``moved`` when one is no longer
        openable or its version moved on (the caller then rebuilds by rules)."""
        ids = list(dict.fromkeys(s.doc_id for s in cites.values()))
        try:
            ok, _ = authz.authorize(user, ids, index=self.index, connectors=self.connectors,
                                    directory_epoch=getattr(self.directory, "epoch", 0))
        except Exception:                                           # noqa: BLE001
            ok = {}
        keep = {t: s for t, s in cites.items()
                if s.doc_id in ok and ok[s.doc_id].version == s.version}
        if len(keep) == len(cites):
            return kept, cites, evidence, False
        return kept, keep, {t: e for t, e in evidence.items() if t in keep}, True

    def _respond(self, vid: str, req_id: str, ctx, layout: str, built: Mapping[str, Any]
                 ) -> tuple[dict[str, Any], str]:
        now = self.clock()
        content = [b for b in built["blocks"] if b["type"] != "missing"]
        status = "ready" if content else "insufficient"
        tags: list[str] = []
        for b in content:
            tags += pagecheck.page_tags(b)
        tags = list(dict.fromkeys(tags))
        citations = DeliverableService._citations(tags, built["cites"],
                                                  self._answer_citations(req_id),
                                                  now.isoformat())
        view_blocks = [*built["blocks"], {"type": "sources"}] if content else list(built["blocks"])
        title = built["title"] or " ".join(str(ctx.question or "").split())[:blocks.MAX_TITLE]
        model: Optional[ModelInfo] = built["model"]
        generated = built["generated"] if content else "none"
        meta = {"label": LABEL, "generated": generated,
                "model": model.model_dump(mode="json") if model else None,
                "instruction": layout, "requested": built["kinds"] if built["explicit"] else [],
                "accent": built.get("accent", "blue"),
                "as_of": now.isoformat(), "scope_note": SCOPE_NOTE}
        resp = {"view_id": vid, "req_id": req_id, "status": status, "label": LABEL,
                "title": title, "generated": generated,
                "model": model.model_dump(mode="json") if model else None,
                "check": built["check"].public(),
                "layout": [b["type"] for b in view_blocks],
                "a2ui": a2ui.messages(vid, title, meta, view_blocks, citations),
                "citations": citations, "fallback": built["fallback"],
                "prompt_version": prompts.PROMPT_VERSION, "scope_note": SCOPE_NOTE,
                "as_of": now.isoformat()}
        resp = json.loads(canonical(resp))                  # one private, immutable copy
        return resp, response_digest(resp)


__all__ = ["ViewService", "response_digest", "DIGEST_FIELDS"]
