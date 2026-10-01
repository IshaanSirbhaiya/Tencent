"""Hybrid search: vector (one-copy matrix) + FTS5, fused with reciprocal rank fusion (P14).

Vector similarity finds passages that MEAN the same thing; FTS5 finds the one containing the part
number or ID. Neither alone is enough, so ``mode="hybrid"`` ranks each leg globally across the
searched stores and fuses them: ``score = Σ 1 / (RRF_K + rank)``.

Every result is a ``Passage`` whose ``pointer`` is a ``doc_span`` over the capture ``.txt``
(``document_id`` = hashed full path, ``char_start``/``char_end``, ``capture_digest`` = the
capture sha256), labelled ``trust=UNTRUSTED`` — document text is third-party writable (spine D3).

Depth levels (``deep_search``): ``standard``; ``facets`` — the question is split into parts and
each part is searched separately, so a topic that lost the overall ranking still gets evidence;
``rerank`` — facets, then a model judges which extracts truly answer. Splitting and judging go
through the harness; with no usable model the split falls back to deterministic rules (P16) and
the notice says so.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import numpy as np
from pydantic import BaseModel, Field

from databridge_core.provenance import SourcePointer, Trust

from . import paths, router
from . import store as st
from .models import Passage, SearchResults
from .runtime import get_harness
from .vectors import MATRICES, normalise_vec, score, top_k

RRF_K = 60
#: Upper bound on ``k`` at the public boundary: results are materialised rows, so an unbounded k
#: (or a negative one, which Python slicing would turn into "all but the last") costs memory.
MAX_K = 500
#: Rerank: the judge sees each extract whole unless the whole candidate set exceeds this many
#: characters; then each is cut to an equal share, and a cut extract is never dropped for not
#: being judged relevant (the judge did not see all of it).
RERANK_CHAR_BUDGET = 48000
RERANK_MIN_SHARE = 600
#: At most this many candidates are sent to one rerank call; the rest keep their fused order after
#: the judged ones. With the per-extract floor this bounds the prompt, labels and question included.
RERANK_MAX_CANDIDATES = 40
_TRUNC = " [extract truncated]"
#: A store failure while reading rows / kind filters is one store's problem, not the search's.
_STORE_ERRORS = (sqlite3.Error, OSError, ValueError)
MODES = ("hybrid", "vector", "keyword")
DEPTHS = ("standard", "facets", "rerank")
_TERM = re.compile(r"[^\s\"]+")


def fts_query(text: str) -> Optional[str]:
    """Each whitespace term as a quoted FTS5 phrase, OR-ed. An ID like ``INV-7731`` stays one
    phrase (its tokens must be adjacent), and no user text can inject FTS5 syntax."""
    terms = []
    for t in _TERM.findall(text):
        t = t.strip(".,;:!?()[]{}'")
        if re.search(r"[0-9A-Za-z]", t):
            terms.append('"' + t.replace('"', '""') + '"')
    return " OR ".join(dict.fromkeys(terms)) or None


def _pointer(row: sqlite3.Row) -> SourcePointer:
    """A ``doc_span`` over the capture ``.txt``. When the passage text is the screen's same-length
    mask of that span (not the span itself), ``derivation`` says so — the pointer never claims
    the emitted text IS the source slice when it is a masked view of it."""
    deriv = row["derivation"] if "derivation" in row.keys() else None
    return SourcePointer(
        source_id=row["source_id"], kind="doc_span", document_id=row["doc_id"],
        char_start=int(row["char_start"]), char_end=int(row["char_end"]),
        capture_digest=row["sha256"], trust=Trust.UNTRUSTED, derivation=deriv or None)


_ROW_COLS = ("c.chunk_id, c.doc_id, c.char_start, c.char_end, c.locator, c.approx_locator, "
             "c.content, c.chunk_kind, d.source_id, d.sha256, d.doc_type")
_ROW_SQL = ("SELECT {cols} FROM chunks c JOIN documents d USING(doc_id) "
            "WHERE c.chunk_id IN ({marks})")


def _check_k(k: Any) -> int:
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= MAX_K:
        raise ValueError(f"k must be an integer between 1 and {MAX_K}, got {k!r}")
    return k


def _kind_ok(row: sqlite3.Row, kinds: Optional[set[str]]) -> bool:
    return not kinds or row["chunk_kind"] in kinds or (row["doc_type"] or "") in kinds


class Retriever:
    def __init__(self, *, passages_dir: Optional[Path] = None, harness: Any = None,
                 route_top: int = 6) -> None:
        self.root = Path(passages_dir) if passages_dir else None
        self._harness = harness
        self.route_top = route_top

    @property
    def harness(self) -> Any:
        return self._harness if self._harness is not None else get_harness()

    def _root(self) -> Path:
        return self.root if self.root is not None else paths.passages_dir()

    # -- the §5.5 call ---------------------------------------------------------------------------
    def search(self, query: str, *, k: int = 8, sources: Optional[Sequence[str]] = None,
               kinds: Optional[Sequence[str]] = None, mode: str = "hybrid") -> SearchResults:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        k = _check_k(k)
        root = self._root()
        entries = router.load(root, write_back=False)          # read-only: never writes
        coverage: dict[str, list] = {"searched": [], "skipped": []}
        notices: list[str] = []
        if sources is not None:
            wanted = set(sources)
            entries = [e for e in entries if e.get("source_id") in wanted]
            missing = wanted - {e.get("source_id") for e in entries}
            for s in sorted(missing):
                coverage["skipped"].append({"store": None, "source_id": s,
                                            "reason": "no published passage store for source"})
        broken = [e for e in entries if e.get("error")]
        for e in broken:
            coverage["skipped"].append({"store": e["file"], "source_id": e.get("source_id"),
                                        "reason": f"store unreadable: {e['error']}"})
            notices.append(f"store {e['file']} is unreadable and was not searched: {e['error']}")
        entries = [e for e in entries if not e.get("error")]
        kind_set = set(kinds) if kinds else None
        res_kwargs = dict(query=query, mode=mode, coverage=coverage, notices=notices)
        if not re.search(r"\w", query or ""):
            # a blank or punctuation-only query has nothing to match: every vector score would be
            # the same meaningless number, so any "result" would be arbitrary rows
            notices.append("query is blank or has no searchable terms; nothing was searched")
            return SearchResults([], **res_kwargs)
        if not entries:
            notices.append("no published passage stores to search")
            return SearchResults([], **res_kwargs)
        pool = max(4 * k, 40)

        vec_hits: list[tuple[float, str, str]] = []          # (cosine, file, chunk_id)
        emb = None
        if mode in ("hybrid", "vector"):
            emb = self.harness.embed([query], batch_size=1, workers=1)
            raw = np.asarray(emb.vectors[0], dtype=np.float32)
            q = normalise_vec(raw)
            qdim = int(q.shape[0])
            compatible = []
            vector_entries = entries
            if not raw.size or not np.all(np.isfinite(raw)) or not np.any(raw):
                notices.append("vector leg skipped: the query embedding is a zero (or non-finite) "
                               "vector, so every similarity would be meaningless")
                vector_entries = []
            for e in vector_entries:
                if e.get("embed_model") != emb.model:
                    why = f"store embedded with {e.get('embed_model')}, query with {emb.model}"
                elif e.get("embed_dim") is not None and int(e["embed_dim"]) != qdim:
                    why = (f"store vectors have dim {e.get('embed_dim')}, the query has {qdim} "
                           f"(same model id {emb.model})")
                else:
                    compatible.append(e)
                    continue
                coverage["skipped"].append({"store": e["file"], "source_id": e.get("source_id"),
                                            "reason": f"vector leg skipped: {why}"})
            chosen, routed_out = router.route(root, compatible, q, top=self.route_top)
            coverage["skipped"].extend(routed_out)
            for e in chosen:
                try:
                    ids, m = MATRICES.get(root / e["file"], emb.model)
                    if ids and m.shape[1] != qdim:
                        raise ValueError(f"store vectors have dim {m.shape[1]}, query {qdim}")
                except (ValueError, sqlite3.Error) as err:
                    coverage["skipped"].append({"store": e["file"],
                                                "source_id": e.get("source_id"),
                                                "reason": f"vector leg failed: {err}"})
                    notices.append(f"vector leg failed for {e['file']}: {err}")
                    continue
                if not ids:
                    continue
                s = score(m, q)
                if kind_set:
                    # filter BEFORE ranking: the wanted kind may sit below the global top pool
                    try:
                        ok = self._kind_ids(root / e["file"], kind_set)
                    except _STORE_ERRORS as err:
                        coverage["skipped"].append({"store": e["file"],
                                                    "source_id": e.get("source_id"),
                                                    "reason": f"vector leg failed: {err}"})
                        notices.append(f"vector leg failed for {e['file']}: {err}")
                        continue
                    s[np.fromiter((cid not in ok for cid in ids), dtype=bool,
                                  count=len(ids))] = -np.inf
                for i in top_k(s, pool):
                    if np.isfinite(s[i]):
                        vec_hits.append((float(s[i]), e["file"], ids[i]))
                coverage["searched"].append({"store": e["file"], "source_id": e.get("source_id"),
                                             "leg": "vector"})
        # (per-store rank, bm25 — lower is better, file, id). BM25 is only comparable WITHIN one
        # store (its IDF is that store's corpus): it orders a store's own hits and nothing else.
        # Across stores only the LOCAL rank counts — equal ranks tie, broken by a neutral
        # deterministic key (file, chunk id), never by another corpus's BM25.
        kw_hits: list[tuple[int, float, str, str]] = []
        if mode in ("hybrid", "keyword"):
            fq = fts_query(query)
            if fq is None:
                notices.append("query has no searchable terms for the keyword leg")
            else:
                for e in entries:
                    hits, err = self._fts(root / e["file"], e["file"], fq, pool, kind_set)
                    if err is not None:
                        coverage["skipped"].append({"store": e["file"],
                                                    "source_id": e.get("source_id"),
                                                    "reason": f"keyword leg failed: {err}"})
                        notices.append(f"keyword leg failed for {e['file']}: {err}")
                        continue
                    kw_hits.extend((r, b, f, c) for r, (b, f, c) in enumerate(hits))
                    coverage["searched"].append({"store": e["file"],
                                                 "source_id": e.get("source_id"),
                                                 "leg": "keyword"})

        # fetch rows for every candidate (bounded by pool x stores) — needed for kinds + output
        by_file: dict[str, set[str]] = {}
        for *_, f, cid in vec_hits + kw_hits:
            by_file.setdefault(f, set()).add(cid)
        rows: dict[tuple[str, str], sqlite3.Row] = {}
        for f, cids in by_file.items():
            try:
                rows.update(self._rows(root / f, f, cids))
            except _STORE_ERRORS as err:
                sid = next((e.get("source_id") for e in entries if e["file"] == f), None)
                coverage["skipped"].append({"store": f, "source_id": sid,
                                            "reason": f"reading passages failed: {err}"})
                notices.append(f"reading passages failed for {f}: {err}")
        keep = lambda f, c: (f, c) in rows and _kind_ok(rows[(f, c)], kind_set)  # noqa: E731

        vec_rank = [(f, c) for s, f, c in sorted(vec_hits, key=lambda t: (-t[0], t[1], t[2]))
                    if keep(f, c)]
        vec_score = {(f, c): s for s, f, c in vec_hits}
        # keyword: each store's LOCAL rank (1-based) is its RRF rank and its keyword-only score
        kw_local = {(f, c): r + 1 for r, _, f, c in kw_hits if keep(f, c)}
        kw_rank = sorted(kw_local, key=lambda key: (kw_local[key], key[0], key[1]))
        if mode == "vector":
            fused = [(key, vec_score[key]) for key in vec_rank]
        elif mode == "keyword":
            fused = [(key, 1.0 / (RRF_K + kw_local[key])) for key in kw_rank]
        else:
            acc: dict[tuple[str, str], float] = {}
            for r, key in enumerate(vec_rank, start=1):
                acc[key] = acc.get(key, 0.0) + 1.0 / (RRF_K + r)
            for key in kw_rank:
                acc[key] = acc.get(key, 0.0) + 1.0 / (RRF_K + kw_local[key])
            fused = sorted(acc.items(), key=lambda t: (-t[1], t[0][0], t[0][1]))
        out = [self._passage(rows[key], sc) for key, sc in fused[:k]]
        if len(out) < max(1, k // 2):
            notices.append(f"thin result ({len(out)} of {k}); a deeper search "
                           "(depth='facets' or 'rerank') may find more")
        return SearchResults(out, embedding_model=emb.model if emb else None,
                             offline_embedding=bool(emb.offline) if emb else False,
                             embedding_label=emb.label if emb else None, **res_kwargs)

    # -- helpers ---------------------------------------------------------------------------------
    @staticmethod
    def _kind_ids(path: Path, kinds: set[str]) -> set[str]:
        conn = st.connect(path, readonly=True)
        try:
            marks = ",".join("?" * len(kinds))
            return {r[0] for r in conn.execute(
                "SELECT c.chunk_id FROM chunks c JOIN documents d USING(doc_id) "
                f"WHERE c.chunk_kind IN ({marks}) OR d.doc_type IN ({marks})",
                list(kinds) * 2)}
        finally:
            conn.close()

    @staticmethod
    def _fts(path: Path, file: str, fq: str, limit: int, kinds: Optional[set[str]]
             ) -> tuple[list[tuple[float, str, str]], Optional[str]]:
        """``(hits, error)``: a failed keyword leg is REPORTED (coverage + notice), never passed
        off as a store that was searched and matched nothing (P7)."""
        try:
            conn = st.connect(path, readonly=True)
        except sqlite3.Error as err:
            return [], f"{type(err).__name__}: {err}"
        try:
            sql = ("SELECT c.chunk_id, bm25(chunks_fts) AS s FROM chunks_fts "
                   "JOIN chunks c ON c.rowid = chunks_fts.rowid ")
            params: list[Any] = [fq]
            if kinds:
                sql += "JOIN documents d ON d.doc_id = c.doc_id "
            sql += "WHERE chunks_fts MATCH ? "
            if kinds:
                marks = ",".join("?" * len(kinds))
                sql += f"AND (c.chunk_kind IN ({marks}) OR d.doc_type IN ({marks})) "
                params += list(kinds) * 2
            sql += "ORDER BY s LIMIT ?"
            params.append(limit)
            return [(float(r[1]), file, r[0]) for r in conn.execute(sql, params)], None
        except sqlite3.Error as err:
            return [], f"{type(err).__name__}: {err}"
        finally:
            conn.close()

    @staticmethod
    def _rows(path: Path, file: str, cids: Iterable[str]) -> dict[tuple[str, str], sqlite3.Row]:
        cids = list(cids)
        out: dict[tuple[str, str], sqlite3.Row] = {}
        conn = st.connect(path, readonly=True)
        try:
            # a store published before the derivation column still reads (as "no derivation")
            cols = _ROW_COLS + (", c.derivation" if st.has_column(conn, "chunks", "derivation")
                                else "")
            for i in range(0, len(cids), 500):
                part = cids[i:i + 500]
                sql = _ROW_SQL.format(cols=cols, marks=",".join("?" * len(part)))
                for r in conn.execute(sql, part):
                    out[(file, r["chunk_id"])] = r
        finally:
            conn.close()
        return out

    @staticmethod
    def _passage(row: sqlite3.Row, sc: float) -> Passage:
        return Passage(chunk_id=row["chunk_id"], doc_id=row["doc_id"],
                       source_id=row["source_id"], text=row["content"],
                       locator=row["locator"] or "", approx_locator=bool(row["approx_locator"]),
                       score=float(sc), pointer=_pointer(row))

    # -- depth levels ----------------------------------------------------------------------------
    def deep_search(self, query: str, *, depth: str = "facets", k: int = 8,
                    sources: Optional[Sequence[str]] = None,
                    kinds: Optional[Sequence[str]] = None, mode: str = "hybrid",
                    facet_task_class: str = "brain_query",
                    rerank_task_class: str = "brain_analysis") -> SearchResults:
        if depth not in DEPTHS:
            raise ValueError(f"depth must be one of {DEPTHS}, got {depth!r}")
        k = _check_k(k)
        if depth == "standard":
            res = self.search(query, k=k, sources=sources, kinds=kinds, mode=mode)
            return res
        facets, how = self._facets(query, facet_task_class)
        per = [self.search(f, k=k, sources=sources, kinds=kinds, mode=mode) for f in facets]
        merged: list[Passage] = []
        seen: set[tuple[str, str, str]] = set()
        # round-robin: every facet's best evidence first, then the rest in rank order. The key is
        # the source-qualified identity: equal chunk ids in two sources are two pieces of evidence
        for rank in range(k):
            for res in per:
                if rank < len(res):
                    p = res[rank]
                    key = (p.source_id, p.doc_id, p.chunk_id)
                    if key not in seen:
                        merged.append(p)
                        seen.add(key)
        notices = [f"facets ({how}): {facets}"]
        base = per[0]
        coverage = {"searched": [c for r in per for c in r.coverage["searched"]],
                    "skipped": [c for r in per for c in r.coverage["skipped"]]}
        if depth == "rerank" and merged:
            merged, note = self._rerank(query, merged, rerank_task_class)
            notices.append(note)
        else:
            merged = merged[:k]
        return SearchResults(merged[:k], query=query, mode=mode, depth=depth,
                             embedding_model=base.embedding_model,
                             offline_embedding=base.offline_embedding,
                             embedding_label=base.embedding_label, coverage=coverage,
                             notices=notices + [n for r in per for n in r.notices])

    def _model_usable(self, task_class: str) -> bool:
        """False when the real LiteLLM backend has no credential for the routed model — then the
        rules path is used without attempting a call that can only fail."""
        backend = getattr(self.harness, "_backend", None)
        if getattr(backend, "name", "") != "litellm":
            return True
        import os

        from databridge_core.config import resolve_model
        from databridge_harness import embedding_key_present

        model = resolve_model(task_class)
        if model.startswith("anthropic/"):
            return bool(os.environ.get("ANTHROPIC_API_KEY"))
        return embedding_key_present(model)

    def _facets(self, query: str, task_class: str) -> tuple[list[str], str]:
        rules = rule_facets(query)
        if not self._model_usable(task_class):
            return rules, "rules; no model credential configured"
        try:
            res = self.harness.complete_structured(task_class, [
                {"role": "system", "content": (
                    "Split the user's question into 1-5 short, self-contained search queries, "
                    "one per distinct topic it asks about. Do not add topics it does not ask.")},
                {"role": "user", "content": query}], _Facets, max_retries=1)
            facets = [f.strip() for f in res.parsed.facets if f and f.strip()][:5]
            if facets:
                return list(dict.fromkeys(facets)), "model"
        except Exception as err:            # noqa: BLE001 — recorded, falls back to rules (P16)
            return rules, f"rules; model split unavailable: {type(err).__name__}"
        return rules, "rules; model returned no facets"

    def _rerank(self, query: str, passages: list[Passage],
                task_class: str) -> tuple[list[Passage], str]:
        if not self._model_usable(task_class):
            return passages, "rerank unavailable (no model credential configured); fused order kept"
        # The budget covers the WHOLE user message: header, question, labels and extracts. At
        # most RERANK_MAX_CANDIDATES are sent, fewer when each could not get RERANK_MIN_SHARE.
        header = f"Question: {query}\n\nExtracts:\n"

        def overhead(n: int) -> int:
            return sum(len(f"[{i}] ") + len(_TRUNC) + 2 for i in range(n))

        n = min(len(passages), RERANK_MAX_CANDIDATES)
        while n and (RERANK_CHAR_BUDGET - len(header) - overhead(n)) // n < RERANK_MIN_SHARE:
            n -= 1
        if n == 0:
            return passages, ("rerank not attempted: the question alone exceeds the rerank "
                              "budget; fused order kept")
        sent, unsent = passages[:n], passages[n:]
        room = RERANK_CHAR_BUDGET - len(header) - overhead(n)
        share = room // n if sum(len(p.text) for p in sent) > room else None
        cut = {i for i, p in enumerate(sent) if share is not None and len(p.text) > share}
        listing = "\n\n".join(
            f"[{i}] {p.text[:share] if i in cut else p.text}" + (_TRUNC if i in cut else "")
            for i, p in enumerate(sent))
        try:
            res = self.harness.complete_structured(task_class, [
                {"role": "system", "content": (
                    "Judge which numbered extracts actually answer the question. Return only "
                    "those, each with a relevance between 0 and 1. Judge only from the text.")},
                {"role": "user", "content": header + listing}],
                _Judgement, max_retries=1)
        except Exception as err:            # noqa: BLE001
            return passages, f"rerank unavailable ({type(err).__name__}); fused order kept"
        judged = [(j.index, j.relevance) for j in res.parsed.keep
                  if 0 <= j.index < len(sent)]
        judged = list({i: r for i, r in judged}.items())
        judged.sort(key=lambda t: -t[1])
        out = [sent[i].model_copy(update={"score": float(r)}) for i, r in judged]
        kept = {i for i, _ in judged}
        # a truncated extract the judge did not keep is NOT dropped: it was not judged whole
        unjudged = [sent[i] for i in sorted(cut - kept)]
        dropped = len(sent) - len(out) - len(unjudged)
        note = (f"reranked by model ({task_class}); score = judged relevance; "
                f"{dropped} extract(s) judged not to answer were dropped")
        if unjudged:
            note += (f"; {len(unjudged)} truncated extract(s) not judged relevant were kept after "
                     "the judged ones, with their fused scores")
        if unsent:
            note += (f"; {len(unsent)} extract(s) beyond the first {n} were not sent to the judge "
                     "(budget) and were kept after them in fused order")
        return out + unjudged + unsent, note


class _Facets(BaseModel):
    facets: list[str] = Field(default_factory=list)


class _Judged(BaseModel):
    index: int
    relevance: float = Field(ge=0.0, le=1.0)


class _Judgement(BaseModel):
    keep: list[_Judged] = Field(default_factory=list)


def rule_facets(query: str) -> list[str]:
    """Deterministic split: sentences / semicolons, then ' and ' / ', ' between multi-word parts."""
    parts = [p.strip() for p in re.split(r"[?;\n]+|\.\s+", query) if p.strip()]
    out: list[str] = []
    for p in parts:
        sub = [s.strip(" ,.") for s in re.split(r",\s+|\s+and\s+|\s+also\s+", p) if s.strip(" ,.")]
        if len(sub) > 1 and all(len(s.split()) >= 2 for s in sub):
            out.extend(sub)
        else:
            out.append(p)
    out = list(dict.fromkeys(out))
    return out or [query]


def search(query: str, *, k: int = 8, sources: Optional[Sequence[str]] = None,
           kinds: Optional[Sequence[str]] = None, mode: str = "hybrid") -> list[Passage]:
    """BUILD_PLAN §5.5 — exactly this signature. Returns ``SearchResults`` (a ``list[Passage]``)."""
    return Retriever().search(query, k=k, sources=sources, kinds=kinds, mode=mode)


def deep_search(query: str, *, depth: str = "facets", k: int = 8,
                sources: Optional[Sequence[str]] = None, kinds: Optional[Sequence[str]] = None,
                mode: str = "hybrid") -> list[Passage]:
    return Retriever().deep_search(query, depth=depth, k=k, sources=sources, kinds=kinds,
                                   mode=mode)


def stats(passages_dir: Optional[Path] = None) -> list[dict]:
    """The router view. Read-only: never writes ``router.json``."""
    root = Path(passages_dir) if passages_dir else paths.passages_dir()
    return [router.public_view(e) for e in router.load(root, write_back=False)]
