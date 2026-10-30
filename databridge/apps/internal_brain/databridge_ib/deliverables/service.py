"""The deliverable service: write-ahead, ownership first, built on Act's action context.

Order of one ``create`` (PLAN §4, with review_opus.md #9-#11 and review_codex.md #5, #7, #8):

1. rate limit (per person; over it: ``deliverable.failed`` stage ``rate``, no ids, 429);
2. ownership (``projection.owns``; cheap, no source reads). Not mine, unknown: ``deliverable.failed``
   stage ``lookup`` WITHOUT any id, one 404;
3. LEDGER ``deliverable.requested`` (before any live source read and before any model call);
4. ``ActionService.context`` (sealed-answer check + live Gate 1/Gate 2/read-through of every cited
   document). None (refused, failed, every source revoked): one 404. Any error: 503;
5. evidence (source units only, capped) and the build: model (bounded) and/or deterministic,
   every field checked (``verify.py``);
6. after ANY model call (round 4 B4: also when the model's output was invalid and the deterministic
   fallback is released), the cited documents are authorised AGAIN: a document that is no longer
   openable, or whose version moved on, is removed and the content checked again. A model title is
   re-checked against what is left (round 4 B3), else the fixed title; a deterministic build is
   rebuilt from the remaining sources only;
7. the response is built privately, its canonical digest computed once, LEDGER
   ``deliverable.created``; only then is it published to the export store and returned.

``export``: rate limit → owner + TTL lookup (one 404) → format/kind (422) → LEDGER
``deliverable.requested`` with ``export_format`` (round 4 B2: write-ahead, before Gate 2 and the
source read; a ledger failure is a 503) → re-authorise and re-check against the sources as they
are now → build the file (CSV, or .pptx when python-pptx is
installed) from the STORED copy (never mutated) → LEDGER ``deliverable.exported`` → bytes.

The store is in memory (90 min, 300 entries): it is lost on a restart, and the ledger's digest
then proves only what was shown, not the content (rule 8; Opus review #13).
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional

from .. import actions as actmod
from .. import authz
from ..config import POLICY_VERSION
from ..models import (SCOPE_NOTE, DeliverableCreated, DeliverableExported, DeliverableFailed,
                      DeliverableRequested, DirectoryUser, ModelInfo)
from . import build, export, prompts
from .evidence import build_evidence, context_text
from .model import structured
from .spec import MODEL_FOR
from .verify import Check, check_short_text

KINDS = ("table", "chart", "analysis")
MODEL_KINDS = ("table", "chart", "analysis")
LABEL = "Draft for your review"
STORE_TTL_S = 90 * 60
MAX_STORE = 300
RATE_N, RATE_WINDOW_S = 10, 60.0
EXPORT_RATE_N = 20
SOURCE_TABLE = ModelInfo(provider="deterministic", model="source-table-v1")
QUOTED_TABLE = ModelInfo(provider="deterministic", model="quoted-table-v1")
SOURCE_CHART = ModelInfo(provider="deterministic", model="source-chart-v1")


class NotFound(Exception):
    """One 404 for every not-found reason."""


class Unavailable(Exception):
    """503: the ledger, the sources or the store could not be relied on. Nothing was shown."""


class RateLimited(Exception):
    """429."""


class Invalid(Exception):
    """422 (a format the deliverable does not export)."""


def _sha(s: str | bytes) -> str:
    return hashlib.sha256(s if isinstance(s, bytes) else s.encode("utf-8")).hexdigest()


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


#: the response fields the ``created`` digest covers (canonical JSON of exactly these)
DIGEST_FIELDS = ("deliverable_id", "kind", "status", "title", "generated", "model", "check",
                 "content", "citations")


def response_digest(resp: Mapping[str, Any]) -> str:
    return _sha(canonical({k: resp.get(k) for k in DIGEST_FIELDS}))


@dataclass
class Entry:
    sub: str
    req_id: str
    kind: str
    response: dict[str, Any]                       # the sealed, immutable copy
    question: str
    facts: tuple                                   # actions.Fact, for the excerpt scoring
    tag_doc: dict[str, str]
    created: float = field(default_factory=time.monotonic)


class _Limiter:
    def __init__(self, n: int, window: float):
        self.n, self.window = n, window
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.n:
                return False
            q.append(now)
            return True


class DeliverableService:
    def __init__(self, *, actions: Any, ledger: Any, projection: Any, index: Any,
                 connectors: Mapping[str, Any], directory: Any, chain: Any = None,
                 enabled: bool = False, sync: Optional[Callable[[], None]] = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.actions, self.ledger, self.projection = actions, ledger, projection
        self.index, self.connectors, self.directory = index, connectors, directory
        self.chain, self.enabled, self.sync, self.clock = chain, bool(enabled), sync, clock
        self._store: dict[str, Entry] = {}
        self._lock = threading.Lock()
        self._rate = _Limiter(RATE_N, RATE_WINDOW_S)
        self._export_rate = _Limiter(EXPORT_RATE_N, RATE_WINDOW_S)

    # -- status -------------------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        model = self.chain is not None
        return {"enabled": self.enabled, "model": model,
                "kinds": [k for k in KINDS if model or k != "analysis"] if self.enabled else [],
                "exports": {"csv": self.enabled, "pptx": self.enabled and export.pptx_available()}}

    # -- ledger -------------------------------------------------------------------------------
    def _append(self, event) -> None:
        try:
            self.ledger.append(event)
        except Exception as exc:                                    # noqa: BLE001
            raise Unavailable(type(exc).__name__) from None

    def _fail(self, user: DirectoryUser, stage: str, reason: str, *, req_id: Optional[str] = None,
              deliverable_id: Optional[str] = None, kind: Optional[str] = None) -> None:
        self._append(DeliverableFailed(sub=user.id, stage=stage, reason_code=reason[:80],
                                       req_id=req_id, deliverable_id=deliverable_id,
                                       deliverable=kind, event_time=self.clock()))

    # -- numbering ------------------------------------------------------------------------------
    def _answer_citations(self, req_id: str) -> list[dict[str, Any]]:
        try:
            res = self.projection.reconstruct(req_id) or {}
        except Exception:                                           # noqa: BLE001
            return []
        return [c for c in res.get("citations") or () if isinstance(c, dict)]

    @staticmethod
    def _citations(content_tags: list[str], cites: Mapping[str, Any],
                   answer_cites: list[dict[str, Any]], checked_at: str) -> list[dict[str, Any]]:
        order = [str(c.get("tag")) for c in answer_cites if c.get("tag")]
        version = {str(c.get("tag")): str(c.get("version") or "") for c in answer_cites}
        out = []
        for t in content_tags:
            snap = cites.get(t)
            if snap is None:
                continue
            n = order.index(t) + 1 if t in order else int(t[1:]) if t[1:].isdigit() else 0
            cv = version.get(t, "")
            out.append({"tag": t, "n": n, **authz.public_meta(snap), "cited_version": cv,
                        "changed_since_answer": bool(cv) and cv != snap.version,
                        "checked_at": checked_at})
        return out

    # -- admission (round 4 B12: the route calls this BEFORE it reads the request body) ---------
    def admit(self, user: DirectoryUser, op: str) -> None:
        """The per-person rate limit for ``create`` or ``export``; over it: ``deliverable.failed``
        stage ``rate`` (no ids) and ``RateLimited``."""
        limiter = self._export_rate if op == "export" else self._rate
        if not limiter.allow(user.id):
            self._fail(user, "rate", "rate_limited")
            raise RateLimited()

    # -- create ---------------------------------------------------------------------------------
    def create(self, user: DirectoryUser, req_id: str, kind: str, chart: str = "auto", *,
               admitted: bool = False) -> dict[str, Any]:
        if not admitted:
            self.admit(user, "create")
        if kind not in KINDS:
            raise Invalid("kind")
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
        did = uuid.uuid4().hex
        planned = self.chain is not None and kind in MODEL_KINDS
        # write-ahead: on record before the live source checks and before any model call
        self._append(DeliverableRequested(sub=user.id, req_id=req_id, deliverable_id=did,
                                          deliverable=kind, model_planned=planned,
                                          policy_version=POLICY_VERSION, event_time=self.clock()))
        try:
            ctx = self.actions.context(user, req_id)
        except Exception as exc:                                    # noqa: BLE001
            try:
                self._fail(user, "generate", "context_" + type(exc).__name__, req_id=req_id,
                           deliverable_id=did, kind=kind)
            except Unavailable:
                pass
            raise Unavailable(type(exc).__name__) from None
        evidence = build_evidence(ctx.cites, question=ctx.question, facts=ctx.facts) if ctx else {}
        if not evidence:
            self._fail(user, "lookup", "not_found", req_id=req_id, deliverable_id=did, kind=kind)
            raise NotFound()
        # round 3 I3: a builder or verifier error still closes the write-ahead record
        try:
            context = context_text(evidence, [f.text for f in ctx.facts])
            content, title, generated, model, fallback, check = self._build(kind, chart, evidence,
                                                                            context, ctx.question)
            cites = dict(ctx.cites)
            if self.chain is not None:      # a model call took time: authorise again (B4: always)
                content, title, generated, model, fallback, cites, evidence = self._reauthorise(
                    user, kind, content, title, generated, model, fallback, cites, evidence,
                    check, ctx.question)
            status = ("ready" if content is not None
                      else "needs_model" if kind == "analysis" else "insufficient")
            now = self.clock()
            tags = build.content_tags(kind, content)
            citations = self._citations(tags, cites, self._answer_citations(req_id),
                                        now.isoformat())
            resp = {"deliverable_id": did, "req_id": req_id, "kind": kind, "status": status,
                    "label": LABEL, "title": title, "generated": generated if content else "none",
                    "model": model.model_dump(mode="json") if model else None,
                    "check": check.public() if generated == "model" else Check().public(),
                    "content": content, "citations": citations,
                    "exports": self._exports(status, kind),
                    "fallback": fallback, "prompt_version": prompts.PROMPT_VERSION,
                    "scope_note": SCOPE_NOTE, "as_of": now.isoformat()}
            resp = json.loads(canonical(resp))            # one private, immutable copy
            digest = response_digest(resp)
        except (NotFound, Unavailable, RateLimited, Invalid):
            raise
        except Exception as exc:                                    # noqa: BLE001
            try:
                self._fail(user, "generate", "build_" + type(exc).__name__, req_id=req_id,
                           deliverable_id=did, kind=kind)
            except Unavailable:
                pass
            raise Unavailable(type(exc).__name__) from None
        c = resp["check"]
        self._append(DeliverableCreated(
            sub=user.id, req_id=req_id, deliverable_id=did, deliverable=kind, status=status,
            generated=resp["generated"], model=model, prompt_version=prompts.PROMPT_VERSION,
            cited_docs=len({x["doc_id"] for x in citations}),
            doc_ids=tuple(dict.fromkeys(x["doc_id"] for x in citations)),
            context_chars=len(context), context_digest=_sha(context),
            items_proposed=c["items_proposed"], items_kept=c["items_kept"],
            items_dropped=c["items_dropped"], values_proposed=c["values_proposed"],
            values_kept=c["values_kept"], values_dropped=c["values_dropped"],
            names_dropped=c["names_dropped"], fallback=fallback, digest=digest,
            policy_version=POLICY_VERSION, event_time=now))
        if status == "ready":                      # published only after the seal
            entry = Entry(sub=user.id, req_id=req_id, kind=kind, response=resp,
                          question=ctx.question, facts=tuple(ctx.facts),
                          tag_doc={t: s.doc_id for t, s in cites.items()})
            with self._lock:
                self._evict()
                self._store[did] = entry
        return copy.deepcopy(resp)

    @staticmethod
    def _export_title(kind: str, title: str, evidence) -> str:
        """The sealed title, checked again against the sources as they are now (Opus review #12):
        fixed text stays, a source-worded title must still be in a remaining cited unit."""
        fixed = set(build.FIXED_TITLE.values()) | {"Quoted from your sources"}
        if title in fixed:
            return title
        units = [u for ev in evidence.values() for u in ev.units]
        return title if units and check_short_text(title, units) else build.FIXED_TITLE[kind]

    @staticmethod
    def _exports(status: str, kind: str) -> list[str]:
        """CSV for a ready table or chart; .pptx too when python-pptx is installed (P1 Slides)."""
        if status != "ready" or kind not in ("table", "chart"):
            return []
        return ["csv", "pptx"] if export.pptx_available() else ["csv"]

    def _build(self, kind: str, chart: str, evidence, context: str, question: str):
        check = Check()
        if self.chain is not None:
            parsed, info, code = structured(self.chain, prompts.system_prompt(kind), context,
                                            prompts.instruction(kind, chart), MODEL_FOR[kind])
            if parsed is not None:
                spec = parsed.model_dump(mode="json")
                if kind == "chart":
                    content = build.model_chart(spec, evidence, check)
                elif kind == "table":
                    content = build.check_table(spec, evidence, check, source="model")
                else:
                    content = build.check_analysis(spec, evidence, check)
                if content is not None:
                    title = build.checked_title(spec.get("title", ""), kind, evidence,
                                                build.content_tags(kind, content), check,
                                                labels=build.title_labels(kind, content))
                    return content, title, "model", info, "none", check
                code = "nothing_passed"
            got = self._deterministic(kind, evidence, question, fallback=code)
            # the counts of what the model proposed stay visible when it was dropped
            return (*got[:4], got[4], check if parsed is not None else Check())
        got = self._deterministic(kind, evidence, question, fallback="no_model")
        return (*got[:4], got[4], check)

    def _deterministic(self, kind: str, evidence, question: str, *, fallback: str):
        fb = fallback not in ("none", "no_model")

        def info(base: ModelInfo) -> ModelInfo:
            return ModelInfo(provider=base.provider, model=base.model, fallback_used=fb)
        if kind == "table":
            t = build.source_table(evidence)
            if t is not None:
                return t, build.FIXED_TITLE["table"], "deterministic", info(SOURCE_TABLE), fallback
            q = build.quoted_table(evidence, question)
            if q is not None:
                return q, "Quoted from your sources", "deterministic", info(QUOTED_TABLE), fallback
        elif kind == "chart":
            got = build.source_chart(evidence)
            if got is not None:
                c, title, tag = got
                c["from_tag"] = tag
                return c, title, "deterministic", info(SOURCE_CHART), fallback
        return None, build.FIXED_TITLE[kind], "none", None, fallback

    def _reauthorise(self, user, kind, content, title, generated, model, fallback, cites,
                     evidence, check, question):
        """After a model call (whatever it returned): the cited documents authorised AGAIN. Nothing
        revoked: unchanged. Else the model's content is checked again against the remaining
        sources and its TITLE too (round 4 B3: a title worded from a revoked source is replaced by
        the fixed one); a deterministic build (round 4 B4) is rebuilt from the remaining sources."""
        ids = list(dict.fromkeys(s.doc_id for s in cites.values()))
        try:
            ok, _ = authz.authorize(user, ids, index=self.index, connectors=self.connectors,
                                    directory_epoch=getattr(self.directory, "epoch", 0))
        except Exception:                                           # noqa: BLE001
            ok = {}
        keep = {t: s for t, s in cites.items() if s.doc_id in ok and ok[s.doc_id].version == s.version}
        if len(keep) == len(cites):
            return content, title, generated, model, fallback, cites, evidence
        ev2 = {t: e for t, e in evidence.items() if t in keep}
        if generated == "model":
            out, dropped = build.recheck(kind, content, ev2)
            check.items_dropped += dropped
            check.items_kept = max(0, check.items_kept - dropped)
            if out is not None:
                if title not in build.FIXED_TITLE.values():
                    title = build.checked_title(title, kind, ev2, build.content_tags(kind, out),
                                                check, labels=build.title_labels(kind, out))
                return out, title, generated, model, fallback, keep, ev2
            fallback = "nothing_passed"
        c, t, g, m, fb = self._deterministic(kind, ev2, question, fallback=fallback)
        return c, t, g, m, fb, keep, ev2

    # -- store ----------------------------------------------------------------------------------
    def _evict(self) -> None:
        now = time.monotonic()
        for k in [k for k, e in self._store.items() if now - e.created > STORE_TTL_S]:
            del self._store[k]
        while len(self._store) >= MAX_STORE:
            self._store.pop(next(iter(self._store)))

    def _lookup(self, user: DirectoryUser, did: str) -> Optional[Entry]:
        if not isinstance(did, str) or not actmod.ID_RE.match(did):
            return None
        with self._lock:
            e = self._store.get(did)
            if e is None or e.sub != user.id or time.monotonic() - e.created > STORE_TTL_S:
                return None
            return e

    # -- export ---------------------------------------------------------------------------------
    def export(self, user: DirectoryUser, deliverable_id: str, fmt: str, *,
               admitted: bool = False) -> tuple[bytes, str, str]:
        """``(bytes, content_type, content_disposition)``."""
        if not admitted:
            self.admit(user, "export")
        e = self._lookup(user, deliverable_id)
        if e is None:
            self._fail(user, "lookup", "not_found")                # no ids (Opus review #11)
            raise NotFound()
        if fmt not in e.response.get("exports", ()):              # only after ownership
            raise Invalid("format")
        sealed = copy.deepcopy(e.response)                        # never mutate the sealed copy
        # round 4 B2 (rule 3): write-ahead, on record before Gate 2 and any source read; a ledger
        # failure is a 503 with nothing read and no bytes
        self._append(DeliverableRequested(sub=user.id, req_id=e.req_id,
                                          deliverable_id=deliverable_id, deliverable=e.kind,
                                          model_planned=False, policy_version=POLICY_VERSION,
                                          export_format=fmt, event_time=self.clock()))
        try:
            ok, _ = authz.authorize(user, list(dict.fromkeys(e.tag_doc.values())),
                                    index=self.index, connectors=self.connectors,
                                    directory_epoch=getattr(self.directory, "epoch", 0))
        except Exception:                                           # noqa: BLE001
            ok = {}
        cites = {t: ok[d] for t, d in e.tag_doc.items() if d in ok}
        evidence = build_evidence(cites, question=e.question, facts=e.facts)
        content, dropped = build.recheck(e.kind, sealed["content"], evidence)
        if content is None:
            self._fail(user, "export", "nothing_left", req_id=e.req_id,
                       deliverable_id=deliverable_id, kind=e.kind)
            raise NotFound()
        now = self.clock()
        tags = build.content_tags(e.kind, content)
        citations = self._citations(tags, cites, self._answer_citations(e.req_id), now.isoformat())
        if fmt == "pptx":
            try:
                title = self._export_title(e.kind, str(sealed["title"]), evidence)
                data = export.build_pptx(e.kind, content, citations, title=title,
                                         req_id=e.req_id, as_of=now)
            except Exception as exc:                                # noqa: BLE001
                self._fail(user, "export", "pptx_" + type(exc).__name__, req_id=e.req_id,
                           deliverable_id=deliverable_id, kind=e.kind)
                raise Unavailable("pptx") from None
            cap = export.PPTX_CAP
        else:
            data, cap = export.build_csv(e.kind, content, citations), export.CSV_CAP
        if len(data) > cap:
            self._fail(user, "export", "too_large", req_id=e.req_id,
                       deliverable_id=deliverable_id, kind=e.kind)
            raise Unavailable("too_large")
        self._append(DeliverableExported(sub=user.id, req_id=e.req_id,
                                         deliverable_id=deliverable_id, deliverable=e.kind,
                                         format=fmt, bytes=len(data), digest=_sha(data),
                                         items_dropped_on_recheck=dropped, event_time=now))
        name = export.filename(e.kind, e.req_id, now, fmt)
        return data, export.CONTENT_TYPES[fmt], export.content_disposition(name)


__all__ = ["DeliverableService", "NotFound", "Unavailable", "RateLimited", "Invalid", "KINDS",
           "response_digest", "canonical", "DIGEST_FIELDS"]
