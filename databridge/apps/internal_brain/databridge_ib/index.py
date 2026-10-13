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
WINDOW_BONUS = 1.0 / RRF_K
CHUNK_CHARS = 700
BM25_K1, BM25_B = 1.2, 0.75

_TOKEN = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*")
_IDENT = re.compile(r"\b[A-Za-z]{2,10}-\d+\b|\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b")
STOPWORDS = frozenset(
    "a an and are as at be been but by can could did do does for from had has have how i if in "
    "into is it its me my of on or our so than that the their them then there these they this to "
    "was we were what when where which who why will with would you your about any there's what's "
    "show tell give find list please latest last week weeks month quarter sprint days day recent".split())


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower()) if len(t) > 1 and t not in STOPWORDS]


def identifiers(text: str) -> list[str]:
    return sorted({m.group(0).upper() for m in _IDENT.finditer(text or "")})


def chunk_text(text: str, size: int = CHUNK_CHARS) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n|\n", text or "") if p.strip()]
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
    lexical: bool          # shares a content word or identifier with the query (relevance gate)


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
        title = self._docs.get(doc_id, {}).get("title", "")
        toks = tokens(f"{title}\n{text}")
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        if vec is not None:
            n = float(np.linalg.norm(vec))
            vec = vec / n if n else vec
        self._chunks[cid] = {"doc_id": doc_id, "text": text, "tf": tf, "len": max(1, len(toks)),
                             "vec": vec, "idents": set(identifiers(f"{title} {text}"))}
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
        key = (user.id, user.active, user.kind, user.groups, directory_epoch, self.acl_version)
        hit = self._readable_cache.get(key)
        if hit is not None:
            return hit
        with self._lock:
            out = frozenset(d for d, e in self._acl.items() if aclmod.allows(e, user))
            self._readable_cache[key] = out
            return out

    def search(self, query: str, allowed: Iterable[str], *, k: int = 12,
               window: Optional[Window] = None) -> list[Hit]:
        """Hybrid ranking over the PERMITTED chunks only. Restricted chunks are never scored."""
        allowed = set(allowed)
        with self._lock:
            cands = [(cid, c) for d in sorted(allowed) for cid in self._doc_chunks.get(d, [])
                     for c in (self._chunks.get(cid),) if c is not None]
        if not cands:
            return []
        q_terms = list(dict.fromkeys(tokens(query)))
        q_idents = set(identifiers(query))
        # BM25 with statistics from the permitted corpus only
        n = len(cands)
        avgdl = sum(c["len"] for _, c in cands) / n
        df = {t: sum(1 for _, c in cands if t in c["tf"]) for t in q_terms}
        bm25: dict[str, float] = {}
        for cid, c in cands:
            s = 0.0
            for t in q_terms:
                f = c["tf"].get(t, 0)
                if not f:
                    continue
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * f * (BM25_K1 + 1) / (f + BM25_K1 * (1 - BM25_B + BM25_B * c["len"] / avgdl))
            if s > 0:
                bm25[cid] = s
        # vector leg
        vec_scores: dict[str, float] = {}
        qv, _ = self.embed([query])
        q = np.asarray(qv[0], dtype=np.float32)
        qn = float(np.linalg.norm(q))
        if qn:
            q = q / qn
            for cid, c in cands:
                if c["vec"] is not None and c["vec"].shape == q.shape:
                    vec_scores[cid] = float(np.dot(c["vec"], q))
        # fuse
        acc: dict[str, float] = {}
        for r, cid in enumerate(sorted(bm25, key=lambda x: (-bm25[x], x)), start=1):
            acc[cid] = acc.get(cid, 0.0) + 1.0 / (RRF_K + r)
        for r, cid in enumerate(sorted(vec_scores, key=lambda x: (-vec_scores[x], x)), start=1):
            if r > max(4 * k, 40):
                break
            acc[cid] = acc.get(cid, 0.0) + 1.0 / (RRF_K + r)
        hits = []
        for cid, sc in acc.items():
            c = self._chunks[cid]
            lexical = cid in bm25 or bool(q_idents & c["idents"])
            if q_idents & c["idents"]:
                sc += EXACT_BONUS * len(q_idents & c["idents"])
            if window is not None:
                d = self._docs[c["doc_id"]]
                if overlaps(window, datetime.fromisoformat(d["created_at"]),
                            datetime.fromisoformat(d["updated_at"])):
                    sc += WINDOW_BONUS
            hits.append(Hit(cid, c["doc_id"], c["text"], sc, lexical))
        hits.sort(key=lambda h: (-h.score, h.chunk_id))
        return hits[:k]
