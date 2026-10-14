"""The ask pipeline — the binding order of plan §4.2 (coordinator-owned).

    1 authn (app)  2 LEDGER request.accepted  3 parse window  4 GATE 1 + rank (permitted corpus)
    5 relevance gate  6 GATE 2 per candidate (+read-through on drift)  7 linked objects (1+2 again)
    8 LEDGER retrieval.decided  9 generate (hy3 → deepseek → extractive)  10 grounding wall
    11 LEDGER answer.released (before the response)  12 respond — one shape for every outcome.

Any ledger failure aborts the request (fail closed): no retrieval without ``request.accepted``, no
model call without ``retrieval.decided``, no response without ``answer.released``.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

from . import gates, narrate
from .config import POLICY_VERSION, TOP_K
from .connectors.base import SourceConnector
from .index import Hit, Index, identifiers
from .llm import ModelChain
from .models import (REFUSAL_TEXT, AccessCheck, AnswerReleased, AskResponse, Citation,
                     DirectoryUser, Gate2Decision, ModelInfo, RequestAccepted, RetrievalDecided)
from .timewin import parse as parse_window
from .timewin import strip as strip_window
from .sensitive import mask_doc

EXTRACTIVE = ModelInfo(provider="deterministic", model="extractive-v1")
MAX_PASSAGES = 14
MAX_CHUNKS_PER_DOC = 2
MAX_LINKED = 4
PER_PLATFORM_K = 4
#: words in a question that name a platform → that platform gets its own top-k slot, searched only
#: over the asker's readable docs ON that platform (recall, not access: Gate 1 already applied)
PLATFORM_HINTS = {
    "slack": ("slack", "channel", "thread", "chat", "discussion", "discussed", "raised"),
    "confluence": ("confluence", "wiki", "runbook", "page", "decision doc", "adr", "design doc", "rca"),
    "jira": ("jira", "ticket", "tickets", "issue", "issues", "status", "follow-up", "blocker"),
    "drive": ("drive", "google drive", "file", "sheet", "spreadsheet", "postmortem", "deck", "pricing"),
}


def platform_hints(question: str) -> list[str]:
    q = (question or "").lower()
    return [p for p, words in PLATFORM_HINTS.items()
            if any(re.search(rf"\b{re.escape(w)}\b", q) for w in words)]


class Ledger(Protocol):
    def append(self, event: Any) -> dict: ...


class AnswerStore(Protocol):
    def put(self, req_id: str, answer: str) -> tuple[str, str]: ...


def _sha(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


@dataclass
class Pipeline:
    index: Index
    directory: Any                                  # databridge_ib.directory.Directory
    connectors: Mapping[str, SourceConnector]
    ledger: Ledger
    answers: AnswerStore
    chain: Optional[ModelChain] = None
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))
    top_k: int = TOP_K
    gate2_timeout_s: float = 2.0

    # -- the call -----------------------------------------------------------------------------
    def ask(self, user: DirectoryUser, question: str, *, channel: str = "ui",
            event_time: Optional[datetime] = None, replay: bool = False) -> AskResponse:
        req_id = uuid.uuid4().hex
        try:
            return self._ask(user, question, channel, event_time, replay, req_id)
        except Exception as exc:
            # best effort: a sanitized failure record (the error class only, never the message)
            try:
                from .models import RequestFailed
                self.ledger.append(RequestFailed(sub=user.id, stage="ask",
                                                 error_code=type(exc).__name__, req_id=req_id,
                                                 event_time=self.clock()))
            except Exception:                                       # noqa: BLE001
                pass
            raise

    def _ask(self, user: DirectoryUser, question: str, channel: str,
             event_time: Optional[datetime], replay: bool, req_id: str) -> AskResponse:
        t0 = time.perf_counter()
        now = self.clock()
        etime = event_time or now
        common = dict(req_id=req_id, event_time=etime, synthetic_replay=replay)
        epoch = getattr(self.directory, "epoch", 0)
        # 2 — write-ahead
        self.ledger.append(RequestAccepted(sub=user.id, channel=channel, question=question,
                                           question_sha256=_sha(question),
                                           policy_version=POLICY_VERSION, directory_epoch=epoch,
                                           **common))
        # 3 — window (ranking preference)
        window = parse_window(question, now)
        search_q = strip_window(question)       # time phrases rank by date, not as content words
        # 4 — GATE 1 + rank over the permitted corpus only
        readable = self.index.readable(user, epoch)
        hits = self.index.search(search_q, readable, k=self.top_k, window=window,
                                 relevant_only=True)                  # 5 — relevance gate
        seen = {h.chunk_id for h in hits}
        for platform in platform_hints(question):                    # per-platform recall slots
            scoped = [d for d in readable if d.startswith(platform + ":")]
            for h in self.index.search(search_q, scoped, k=PER_PLATFORM_K, window=window,
                                       relevant_only=True):
                if h.chunk_id not in seen:
                    hits.append(h)
                    seen.add(h.chunk_id)
        cand_docs = list(dict.fromkeys(h.doc_id for h in hits))
        checks: dict[str, AccessCheck] = {}
        released_hits: list[Hit] = []
        linked: list[str] = []
        live: set[str] = set()
        if cand_docs:
            # 6 — GATE 2
            checks = self._gate2(user, cand_docs)
            ok_docs = self._read_through(user, checks, live)
            denied = {d for d, c in checks.items() if not c.allowed}
            # source-denied docs leave the ranking corpus too (Codex sec1 #4)
            released_hits = self._select(search_q, ok_docs, set(readable) - denied, window)
            # 7 — linked objects (only ids the platform shapes allow; Gate 1 + Gate 2 again)
            linked = self._linked(released_hits, readable, set(checks))
            if linked:
                lchecks = self._gate2(user, linked)
                checks.update(lchecks)
                ok_linked = self._read_through(user, lchecks, live)
                released_hits += self._best_chunks(search_q, ok_linked)
        released_hits = released_hits[:MAX_PASSAGES]
        released_docs = list(dict.fromkeys(h.doc_id for h in released_hits))
        considered = list(dict.fromkeys(cand_docs + linked))
        # 8 — write-ahead before any model call
        self.ledger.append(RetrievalDecided(
            sub=user.id, time_window=window, gate1_policy_version=POLICY_VERSION,
            considered=tuple(considered),
            considered_meta=tuple(self._meta(d) for d in considered),
            gate2=tuple(Gate2Decision(doc_id=d, verdict=c.verdict, reason_code=c.reason_code)
                        for d, c in checks.items()),
            released=tuple(released_docs), **common))
        # 9/10 — generate + ground
        passages = self._passages(released_hits)
        answer, model, dropped = REFUSAL_TEXT, EXTRACTIVE, 0
        if passages:
            context = narrate.build_context(passages)
            text, info, _notes = (self.chain.answer(context, question) if self.chain
                                  else (None, None, []))
            valid = {p.tag for p in passages}
            removed = 0
            if text and text.strip() != REFUSAL_TEXT:
                text, removed = narrate.drop_addressed_to_ai_counted(text)
            if text and info and (text.strip() == REFUSAL_TEXT
                                  or set(narrate.cited_tags(text)) & valid):
                answer, model = text, info
                dropped = removed
            else:                           # no model, or an answer citing no permitted passage
                answer = narrate.extractive_answer(passages, question)
                if self.chain is not None:      # the model was tried and did not produce a usable answer
                    model = ModelInfo(provider="deterministic", model="extractive-v1",
                                      fallback_used=True)
        refused = answer.strip() == REFUSAL_TEXT
        by_tag = {p.tag: p for p in passages}
        cites = [] if refused else [by_tag[t] for t in narrate.cited_tags(answer) if t in by_tag]
        citations = tuple(self._citation(p, checks, live) for p in cites)
        # 11 — write-ahead before release
        ref, sha = self.answers.put(req_id, answer)
        self.ledger.append(AnswerReleased(
            sub=user.id, model=model, prompt_version=narrate.PROMPT_VERSION, answer_sha256=sha,
            answer_ref=ref, refused=refused,
            citations=tuple({"doc_id": c.doc_id, "version": c.version,
                             "content_checked_at": c.content_checked_at.isoformat(),
                             "authz_checked_at": c.authz_checked_at.isoformat()} for c in citations),
            dropped_sentences_count=dropped, latency_ms=int((time.perf_counter() - t0) * 1000),
            **common))
        # 12 — one shape for every outcome
        return AskResponse(request_id=req_id, answer=answer, citations=citations, model=model,
                           as_of=now)

    # -- steps --------------------------------------------------------------------------------
    def _gate2(self, user: DirectoryUser, doc_ids: Sequence[str]) -> dict[str, AccessCheck]:
        rows = {d: (self.index.doc(d) or {}) for d in doc_ids}
        return gates.check_all(user, doc_ids, {d: r.get("platform", "") for d, r in rows.items()},
                               self.connectors, timeout_s=self.gate2_timeout_s,
                               known_versions={d: r.get("version") for d, r in rows.items()})

    def _read_through(self, user: DirectoryUser, checks: Mapping[str, AccessCheck],
                      live: set[str]) -> set[str]:
        """Allowed docs. A doc whose source version moved on is re-fetched and re-indexed first, and
        admitted ONLY if the fresh version's policy still admits this user and the fetched version is
        the one Gate 2 checked (Codex sec1 #2, #17)."""
        from . import acl as aclmod
        ok = set()
        for d, c in checks.items():
            if not c.allowed:
                continue
            row = self.index.doc(d)
            if row is None:
                continue
            if c.verdict == "drift_material" and not c.current_version:
                continue                    # material drift with no version: cannot verify -> deny
            if c.current_version and c.current_version != row["version"]:
                conn = self.connectors.get(row["platform"])
                try:
                    fresh = conn.fetch(d) if conn else None
                    if fresh is None:
                        self.index.delete(d)
                        continue
                    fresh_acl = conn.acl(d)
                    self.index.upsert(mask_doc(fresh), fresh_acl)    # masked like any ingest (F4)
                    if not aclmod.allows(fresh_acl, user):
                        continue            # the new version is no longer readable by this user
                    if fresh.version != c.current_version:
                        continue            # fetched a different version than Gate 2 checked
                    live.add(d)
                except Exception:                                   # noqa: BLE001
                    continue                                        # cannot confirm content ⇒ omit
            ok.add(d)
        return ok

    def _select(self, question: str, ok_docs: set[str], readable, window) -> list[Hit]:
        """Re-rank after read-through (content may have changed) and cap chunks per doc."""
        if not ok_docs:
            return []
        # Rank over the asker's FULL permitted set (the same statistics Gate 1 used — a tiny
        # candidate-only corpus would distort IDF and coverage), then keep the Gate-2-allowed docs.
        # Re-running the search also picks up content re-read on drift.
        pool = set(readable) | ok_docs
        fresh = [h for h in self.index.search(question, pool, k=max(self.top_k * 4, 60),
                                              window=window, relevant_only=True)
                 if h.doc_id in ok_docs]
        ranked, per = [], {}
        for h in fresh:
            if h.doc_id not in ok_docs or per.get(h.doc_id, 0) >= MAX_CHUNKS_PER_DOC:
                continue
            per[h.doc_id] = per.get(h.doc_id, 0) + 1
            ranked.append(h)
        # round-robin across platforms (each keeps its own score order): a cross-platform question
        # gets cross-platform evidence before the context budget truncates (MAX_PASSAGES)
        lanes: dict[str, list[Hit]] = {}
        for h in ranked:
            lanes.setdefault(h.doc_id.split(":", 1)[0], []).append(h)
        out = []
        while any(lanes.values()):
            for lane in list(lanes.values()):
                if lane:
                    out.append(lane.pop(0))
        return out

    def _linked(self, released: list[Hit], readable, already: set[str]) -> list[str]:
        out: list[str] = []
        for h in released:
            row = self.index.doc(h.doc_id) or {}
            for link in json.loads(row.get("links") or "[]"):
                d = self._resolve_link(link, readable)
                if d and d not in already and d not in out:
                    out.append(d)
                if len(out) >= MAX_LINKED:
                    return out
        return out

    def _resolve_link(self, link: str, readable) -> Optional[str]:
        """Resolve ONLY within the asker's readable set: whether a restricted object with the same
        identifier exists must not change which readable object a link resolves to (sec1 #13)."""
        candidates = [link] + [p + link for p in ("jira:", "confluence:", "drive:")]
        if identifiers(link):
            candidates.append("jira:" + link.upper())
        for c in candidates:
            if c in readable:
                return c
        return None

    def _best_chunks(self, question: str, docs: set[str]) -> list[Hit]:
        if not docs:
            return []
        hits = self.index.search(question, docs, k=len(docs) * 2)
        out, seen = [], set()
        for h in hits:
            if h.doc_id in seen:
                continue
            seen.add(h.doc_id)
            out.append(h)
        for d in sorted(docs - seen):                               # linked but no lexical overlap
            row = self.index.doc(d)
            if row:
                out.append(Hit(f"{d}#0", d, row["text"][:700], 0.0, False))
        return out

    def _passages(self, hits: list[Hit]) -> list[narrate.Passage]:
        out = []
        for h in hits:
            row = self.index.doc(h.doc_id)
            if row is None:                 # deleted concurrently: drop, never dereference
                continue
            out.append(narrate.Passage(tag=f"C{len(out) + 1}", doc_id=h.doc_id, platform=row["platform"],
                                       container_name=row["container_name"], title=row["title"],
                                       version=row["version"], updated=row["updated_at"][:10],
                                       text=h.text))
        return out

    def _meta(self, doc_id: str) -> dict:
        row = self.index.doc(doc_id) or {}
        return {"doc_id": doc_id, "platform": row.get("platform"), "container": row.get("container"),
                "title": row.get("title")}

    def _citation(self, p: narrate.Passage, checks: Mapping[str, AccessCheck],
                  live: set[str]) -> Citation:
        row = self.index.doc(p.doc_id)
        chk = checks.get(p.doc_id)
        at = chk.checked_at if chk else datetime.now(timezone.utc)
        return Citation(tag=p.tag, doc_id=p.doc_id, platform=row["platform"],
                        container_name=row["container_name"], title=row["title"],
                        deep_link=row["deep_link"], version=row["version"],
                        updated_at=_dt(row["updated_at"]),
                        content_checked_at=at if p.doc_id in live else _dt(row["indexed_at"]),
                        authz_checked_at=at, live_read=p.doc_id in live)
