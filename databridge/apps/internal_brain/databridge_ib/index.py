"""The Internal Brain's own retrieval index — Gate 1 lives here (plan §4.2 steps 4–5).

Why its own index rather than Walnut's passage stores (Opus review B2/H6): ranking must not depend
on content the asker cannot read. Here every statistic is computed over the PERMITTED corpus only:

* BM25 is evaluated in Python with document frequency, corpus size and average length taken from
  the asker's readable chunks — a restricted document changes no permitted passage's score;
* vector similarity is per-chunk cosine (no store routing, no centroids);
* the exact-identifier bonus and the time-window boost read only the candidate chunk.

So the differential test (P2: same query, world with vs without restricted docs) can hold exactly.
ACLs are policy expressions stored per document and evaluated at query time against the principal
(``acl.allows``); they are mutable without republishing anything (plan §4.3).
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

import numpy as np

from . import acl as aclmod
from .models import AclExpr, DirectoryUser, SourceDoc
from .timewin import Window, overlaps

RRF_K = 60
EXACT_BONUS = 1.0
WINDOW_BONUS = 0.15
TITLE_WEIGHT = 0.6
#: a passage is RELEVANT only if it covers this share of the question's IDF-weighted terms
#: (IDF from the asker's permitted corpus — P2 holds); below it the passage never reaches a model
MIN_COVERAGE = 0.4
MIN_COVERAGE_SINGLE = 0.5
#: without a matching adjacent term pair (e.g. "key rotation"), a clause of ≥3 terms needs this much
MIN_COVERAGE_NO_PHRASE = 0.6
#: weight of the vector leg; the offline hashing embedding is lexical noise, so it barely counts
VECTOR_WEIGHT_REAL, VECTOR_WEIGHT_OFFLINE = 0.35, 0.05
CHUNK_CHARS = 700
BM25_K1, BM25_B = 1.2, 0.75

_TOKEN = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*")
_IDENT = re.compile(r"\b[A-Za-z]{2,10}-\d+\b|\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b")
STOPWORDS = frozenset(
    "a an and are as at be been but by can could did do does for from had has have how i if in "
    "into is it its me my of on or our so than that the their them then there these they this to "
    "was we were what when where which who why will with would you your about any there's what's "
    "show tell give find list please latest last week weeks month quarter sprint days day recent "
    "summarize summarise summary explain describe provide link around new current any were".split())


#: platform names are routing HINTS (pipeline.platform_hints), not content: "raised in Slack" must
#: not require the word "slack" to appear in a Slack thread
PLATFORM_WORDS = frozenset("slack confluence jira drive google gdrive wiki".split())
_SUFFIXES = ("ing", "ed", "es", "s")


def stem(t: str) -> str:
    """Tiny, deterministic suffix stripper (applied identically to docs and queries):
    raised/raise/raising → rais, blockers → blocker."""
    if any(ch.isdigit() for ch in t):
        return t
    for suf in _SUFFIXES:
        if t.endswith(suf) and len(t) - len(suf) >= 4:
            t = t[: -len(suf)]
            break
    if t.endswith("e") and len(t) >= 5:
        t = t[:-1]
    return t


def tokens(text: str) -> list[str]:
    out = []
    for t in _TOKEN.findall((text or "").lower()):
        parts = [t] + ([p for p in re.split(r"[-_.]", t) if p] if re.search(r"[-_.]", t) else [])
        for p in parts:
            if len(p) > 1 and p not in STOPWORDS:
                out.append(stem(p))
    return out


_CLAUSE = re.compile(r"\band\b|\balso\b|[,;?]|\bplus\b", re.I)


def clauses(text: str) -> list[list[str]]:
    """Content terms per clause of a (possibly multi-intent) question."""
    out = [query_terms(c) for c in _CLAUSE.split(text or "")]
    return [c for c in out if c]


def query_terms(text: str) -> list[str]:
    plat = {stem(w) for w in PLATFORM_WORDS} | set(PLATFORM_WORDS)
    return [t for t in dict.fromkeys(tokens(text)) if t not in plat]


_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'-]*")


def anchors(text: str) -> set[str]:
    """Distinctive question words: tokens with a digit (Q3, wave-4) or capitalised words that do
    not open the sentence (Indonesian). A clause naming an anchor that occurs in NONE of the asker's
    permitted chunks cannot be answered from their sources."""
    out = set()
    words = _WORD.findall(text or "")
    for i, w in enumerate(words):
        if w.lower() in PLATFORM_WORDS or w in ("I",):
            continue
        if any(ch.isdigit() for ch in w) or (i > 0 and w[0].isupper()):
            out.update(tokens(w))
    return {a for a in out if a not in {stem(p) for p in PLATFORM_WORDS}}


def identifiers(text: str) -> list[str]:
    return sorted({m.group(0).upper() for m in _IDENT.finditer(text or "")})


def chunk_text(text: str, size: int = CHUNK_CHARS) -> list[str]:
    parts = []
    for p in re.split(r"\n\s*\n|\n", text or ""):
        p = p.strip()
        while len(p) > size:                 # hard limit: split oversized lines (sec1 #33)
            cut = p.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            parts.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            parts.append(p)
    out, cur = [], ""
    for p in parts:
        if cur and len(cur) + len(p) + 1 > size:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur}\n{p}" if cur else p
    if cur:
        out.append(cur)
    return out or [""]


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    doc_id: str
    text: str
    score: float
    lexical: bool          # passes the relevance gate (IDF coverage ≥ MIN_COVERAGE, or exact id)
    coverage: float = 0.0


Embedder = Callable[[list[str]], tuple[list[list[float]], str]]


def offline_embedder(texts: list[str]) -> tuple[list[list[float]], str]:
    from databridge_harness import OFFLINE_EMBED_MODEL, hashing_embed
    return hashing_embed(texts), OFFLINE_EMBED_MODEL


class Index:
    def __init__(self, path: Path, *, embedder: Optional[Embedder] = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embed = embedder or offline_embedder
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS docs(doc_id TEXT PRIMARY KEY, platform TEXT, kind TEXT,
                container TEXT, container_name TEXT, title TEXT, version TEXT, created_at TEXT,
                updated_at TEXT, deep_link TEXT, author TEXT, links TEXT, acl TEXT, text TEXT,
                indexed_at TEXT);
            CREATE TABLE IF NOT EXISTS chunks(chunk_id TEXT PRIMARY KEY, doc_id TEXT, ord INTEGER,
                text TEXT, model TEXT, vec BLOB);
            CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
        """)
        self.acl_version = 0
        self._docs: dict[str, dict] = {}
        self._acl: dict[str, AclExpr] = {}
        self._chunks: dict[str, dict] = {}              # chunk_id -> {doc_id, text, tf, len, vec}
        self._doc_chunks: dict[str, list[str]] = {}
        self._readable_cache: dict[tuple, frozenset[str]] = {}
        self._load()

    # -- persistence ----------------------------------------------------------------------------
    def _load(self) -> None:
        for r in self._db.execute("SELECT * FROM docs"):
            d = dict(r)
            self._docs[d["doc_id"]] = d
            self._acl[d["doc_id"]] = aclmod.parse(d["acl"])
        for r in self._db.execute("SELECT chunk_id, doc_id, text, vec FROM chunks ORDER BY doc_id, ord"):
            vec = np.frombuffer(r["vec"], dtype=np.float32) if r["vec"] else None
            self._add_chunk(r["chunk_id"], r["doc_id"], r["text"], vec)
        self.acl_version += 1

    def _add_chunk(self, cid: str, doc_id: str, text: str, vec) -> None:
        row = self._docs.get(doc_id, {})
        title = row.get("title", "")
        #: the container (space / project / channel / drive) name is part of a doc's "title" signal
        heading = f"{title} {row.get('container_name', '')}"
        toks = tokens(f"{title}\n{text}")
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        if vec is not None:
            n = float(np.linalg.norm(vec))
            vec = vec / n if n else vec
        seq = tokens(heading) + ["‖"] + tokens(text)          # ‖ separates heading and body
        pairs = {(a, b) for a, b in zip(seq, seq[1:])}
        self._chunks[cid] = {"doc_id": doc_id, "text": text, "tf": tf, "len": max(1, len(toks)),
                             "pairs": pairs,
                             "vec": vec, "idents": set(identifiers(f"{title} {text}")),
                             "title_terms": set(tokens(heading))}
        self._doc_chunks.setdefault(doc_id, []).append(cid)

    # -- writes ---------------------------------------------------------------------------------
    def upsert(self, doc: SourceDoc, acl: AclExpr) -> None:
        pieces = chunk_text(doc.text)
        vecs, model = self.embed([f"{doc.title}\n{p}" for p in pieces])
        now = datetime.now().astimezone().isoformat()
        with self._lock:
            self._drop_doc_locked(doc.doc_id)
            row = {"doc_id": doc.doc_id, "platform": doc.platform, "kind": doc.kind,
                   "container": doc.container, "container_name": doc.container_name,
                   "title": doc.title, "version": doc.version,
                   "created_at": doc.created_at.isoformat(), "updated_at": doc.updated_at.isoformat(),
                   "deep_link": doc.deep_link, "author": doc.author or "",
                   "links": json.dumps(list(doc.links)), "acl": aclmod.dump(acl), "text": doc.text,
                   "indexed_at": now}
            self._db.execute(f"INSERT INTO docs({','.join(row)}) VALUES({','.join('?' * len(row))})",
                             list(row.values()))
            self._docs[doc.doc_id] = row
            self._acl[doc.doc_id] = acl
            for i, (p, v) in enumerate(zip(pieces, vecs)):
                cid = f"{doc.doc_id}#{i}"
                arr = np.asarray(v, dtype=np.float32)
                self._db.execute("INSERT INTO chunks VALUES(?,?,?,?,?,?)",
                                 (cid, doc.doc_id, i, p, model, arr.tobytes()))
                self._add_chunk(cid, doc.doc_id, p, arr)
            self._db.commit()
            self._bump()

    def set_acl(self, doc_id: str, acl: AclExpr) -> None:
        with self._lock:
            if doc_id not in self._docs:
                return
            self._acl[doc_id] = acl
            self._docs[doc_id]["acl"] = aclmod.dump(acl)
            self._db.execute("UPDATE docs SET acl=? WHERE doc_id=?", (aclmod.dump(acl), doc_id))
            self._db.commit()
            self._bump()

    def delete(self, doc_id: str) -> None:
        with self._lock:
            self._drop_doc_locked(doc_id)
            self._db.commit()
            self._bump()

    def _drop_doc_locked(self, doc_id: str) -> None:
        for cid in self._doc_chunks.pop(doc_id, []):
            self._chunks.pop(cid, None)
        self._docs.pop(doc_id, None)
        self._acl.pop(doc_id, None)
        self._db.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        self._db.execute("DELETE FROM docs WHERE doc_id=?", (doc_id,))

    def _bump(self) -> None:
        self.acl_version += 1
        self._readable_cache.clear()

    # -- reads ----------------------------------------------------------------------------------
    def doc(self, doc_id: str) -> Optional[dict]:
        return self._docs.get(doc_id)

    def doc_ids(self) -> list[str]:
        return list(self._docs)

    def docs_in_container(self, platform: str, container: str) -> list[str]:
        return [d for d, r in self._docs.items() if r["platform"] == platform and r["container"] == container]

    def acl_of(self, doc_id: str) -> Optional[AclExpr]:
        return self._acl.get(doc_id)

    def readable(self, user: DirectoryUser, directory_epoch: int = 0) -> frozenset[str]:
        """GATE 1: every doc whose stored policy admits ``user``. Fail-closed (unknown ⇒ no)."""
        key = (user.id, user.active, user.kind, user.groups, user.email, directory_epoch,
               self.acl_version)
        hit = self._readable_cache.get(key)
        if hit is not None:
            return hit
        with self._lock:
            out = frozenset(d for d, e in self._acl.items() if aclmod.allows(e, user))
            self._readable_cache[key] = out
            return out

    def search(self, query: str, allowed: Iterable[str], *, k: int = 12,
               window: Optional[Window] = None, relevant_only: bool = False) -> list[Hit]:
        """Hybrid ranking over the PERMITTED chunks only. Restricted chunks are never scored."""
        allowed = set(allowed)
        with self._lock:
            cands = [(cid, c) for d in sorted(allowed) for cid in self._doc_chunks.get(d, [])
                     for c in (self._chunks.get(cid),) if c is not None]
        if not cands:
            return []
        q_terms = query_terms(query)
        q_idents = set(identifiers(query))
        # statistics from the permitted corpus only (P2)
        n = len(cands)
        avgdl = sum(c["len"] for _, c in cands) / n
        df = {t: sum(1 for _, c in cands if t in c["tf"]) for t in q_terms}
        idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in q_terms}
        parts = [c for c in clauses(query) if any(t in idf for t in c)] or [q_terms]
        # A clause naming a distinctive anchor (Q7, Indonesian) that occurs in NONE of the asker's
        # permitted chunks cannot be answered from their sources: it scores 0. df is over the
        # permitted corpus only, so this reveals nothing about restricted content.
        anc = anchors(query)
        answerable = [p for p in parts if not any(t in anc and df.get(t, 0) == 0 for t in p)]
        lex: dict[str, float] = {}
        cover: dict[str, float] = {}
        for cid, c in cands:
            s = 0.0
            hit_terms = set()
            for t in q_terms:
                f = c["tf"].get(t, 0)
                in_title = t in c["title_terms"]
                if f:
                    s += idf[t] * f * (BM25_K1 + 1) / (f + BM25_K1 * (1 - BM25_B + BM25_B * c["len"] / avgdl))
                if in_title:
                    s += TITLE_WEIGHT * idf[t]
                if f or in_title:
                    hit_terms.add(t)
            if s > 0:
                lex[cid] = s
                # best coverage over the question's clauses (multi-intent questions)
                best = 0.0
                for p in answerable:
                    cov = (sum(idf.get(t, 0) for t in p if t in hit_terms) /
                           (sum(idf.get(t, 0) for t in p) or 1.0))
                    phrase = len(p) <= 2 or any(pair in c["pairs"] for pair in zip(p, p[1:]))
                    if not phrase and cov < MIN_COVERAGE_NO_PHRASE:
                        cov = min(cov, MIN_COVERAGE - 0.01)      # cannot pass the gate on scatter
                    best = max(best, cov)
                cover[cid] = best
        max_lex = max(lex.values(), default=0.0) or 1.0
        # vector leg (per-chunk cosine; no routing, no centroids)
        vec_scores: dict[str, float] = {}
        qv, model = self.embed([query])
        w_vec = VECTOR_WEIGHT_OFFLINE if "hash" in str(model).lower() or "offline" in str(model).lower()             else VECTOR_WEIGHT_REAL
        q = np.asarray(qv[0], dtype=np.float32)
        qn = float(np.linalg.norm(q))
        if qn:
            q = q / qn
            for cid, c in cands:
                if c["vec"] is not None and c["vec"].shape == q.shape:
                    vec_scores[cid] = max(0.0, float(np.dot(c["vec"], q)))
        hits = []
        for cid, c in cands:
            l = lex.get(cid, 0.0) / max_lex
            v = vec_scores.get(cid, 0.0)
            exact = q_idents & c["idents"]
            if not l and not exact and v < 0.2:
                continue
            sc = l + w_vec * v + EXACT_BONUS * len(exact)
            d = self._docs.get(c["doc_id"]) if window is not None else None
            if d is not None:
                if overlaps(window, datetime.fromisoformat(d["created_at"]),
                            datetime.fromisoformat(d["updated_at"])):
                    sc += WINDOW_BONUS
            cov = cover.get(cid, 0.0)
            # single-intent questions need half their weighted terms; multi-intent clauses are
            # shorter, so each needs less (a passage answering one part of a two-part question)
            relevant = bool(exact) or (cov >= (MIN_COVERAGE if len(parts) > 1 else MIN_COVERAGE_SINGLE))
            hits.append(Hit(cid, c["doc_id"], c["text"], sc, relevant, cov))
        if relevant_only:                    # relevance BEFORE top-k truncation (sec1 #28)
            hits = [h for h in hits if h.lexical]
        hits.sort(key=lambda h: (-h.score, h.chunk_id))
        return hits[:k]
