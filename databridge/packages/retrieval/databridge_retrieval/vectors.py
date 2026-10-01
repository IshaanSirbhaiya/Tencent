"""Vector scoring that holds ONE copy of a store's vectors (principles P14 "Memory").

The trap this avoids is the triple copy: a list of per-row blobs, then ``b"".join(blobs)``, then
``np.frombuffer(...).reshape(...).copy()`` — three full copies of the matrix alive at the peak.
Here the float32 matrix is allocated once at its final shape and each row's blob is copied into
its slot straight from the cursor, so the only transient is one row. Norms come from ``einsum``
(no squared temporary matrix), normalisation is in place, and scoring streams in row blocks into
one preallocated score vector.

``search_ram_bytes(n, dim)`` is the published cost of holding a store for search. It is ``None``
when unknown — never ``0``, because ``0`` would read as both "tiny" and "missing" (trap 10).
"""
from __future__ import annotations

import math
import sqlite3
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import numpy as np

BLOCK_ROWS = 16384
#: The router describes a store with clamp(chunks // 1500, 12, 48) centroids (never more than it
#: has vectors). Scales with store size so a tiny store is not given a huge store's resolution.
CENTROID_DIVISOR, CENTROID_MIN, CENTROID_MAX = 1500, 12, 48


def centroid_count(n_vectors: int) -> int:
    if n_vectors <= 0:
        return 0
    return min(n_vectors, max(CENTROID_MIN, min(CENTROID_MAX, n_vectors // CENTROID_DIVISOR)))


def search_ram_bytes(n: Optional[int], dim: Optional[int]) -> Optional[int]:
    if not n or not dim:
        return None
    return int(n) * int(dim) * 4


def search_ram_mb(n: Optional[int], dim: Optional[int]) -> Optional[float]:
    """MB rounded UP to 0.001, so a genuinely tiny store never publishes as 0."""
    b = search_ram_bytes(n, dim)
    return None if b is None else math.ceil(b / 2**20 * 1000) / 1000


def to_blob(vec) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def normalise_inplace(m: np.ndarray) -> np.ndarray:
    norms = np.sqrt(np.einsum("ij,ij->i", m, m))
    norms[norms == 0] = 1.0
    m /= norms[:, None]
    return m


def normalise_vec(v) -> np.ndarray:
    q = np.asarray(v, dtype=np.float32).copy()
    n = float(np.sqrt(np.einsum("i,i->", q, q)))
    if n > 0:
        q /= n
    return q


def load_matrix(conn: sqlite3.Connection, model: str) -> tuple[list[str], np.ndarray]:
    """``(chunk_ids, M)`` for every embedding made by ``model``; M normalised in place.

    Raises ``ValueError`` when the rows do not share one dimension, or a blob's length disagrees
    with its recorded dim: silently skipping such rows would publish (or search) a store with
    missing vector coverage while every count still reconciles."""
    row = conn.execute("SELECT COUNT(*), MIN(dim), MAX(dim), SUM(length(vector) <> dim * 4) "
                       "FROM embeddings WHERE model=?", (model,)).fetchone()
    n, lo, dim, bad = int(row[0] or 0), int(row[1] or 0), int(row[2] or 0), int(row[3] or 0)
    if lo != dim or bad:
        raise ValueError(f"embeddings for {model} are not one dimension (dims {lo}..{dim}, "
                         f"{bad} blob(s) of the wrong length)")
    ids: list[str] = []
    m = np.empty((n, dim), dtype=np.float32)
    i = 0
    for cid, blob in conn.execute(
            "SELECT chunk_id, vector FROM embeddings WHERE model=? ORDER BY rowid", (model,)):
        if i >= n:
            break                                  # rows added after the count (never in a build)
        m[i] = np.frombuffer(blob, dtype=np.float32)
        ids.append(cid)
        i += 1
    if i < n:
        m = m[:i]
    normalise_inplace(m)
    return ids, m


def score(m: np.ndarray, q: np.ndarray, block: int = BLOCK_ROWS) -> np.ndarray:
    out = np.empty(m.shape[0], dtype=np.float32)
    for s in range(0, m.shape[0], block):
        np.matmul(m[s:s + block], q, out=out[s:s + block])
    return out


def top_k(scores: np.ndarray, k: int) -> np.ndarray:
    if scores.size == 0 or k <= 0:
        return np.empty(0, dtype=np.int64)
    k = min(k, scores.size)
    idx = np.argpartition(-scores, k - 1)[:k]
    return idx[np.argsort(-scores[idx], kind="stable")]


def score_many(m: np.ndarray, qs: np.ndarray, k: int,
               block: int = BLOCK_ROWS) -> list[list[tuple[int, float]]]:
    """Top-k for many queries, streaming the matrix once in blocks (P14: many queries, same store)."""
    nq = qs.shape[0]
    best: list[list[tuple[int, float]]] = [[] for _ in range(nq)]
    for s in range(0, m.shape[0], block):
        sims = m[s:s + block] @ qs.T                 # (block, nq) — bounded temporary
        for j in range(nq):
            col = sims[:, j]
            for i in top_k(col, k):
                best[j].append((s + int(i), float(col[i])))
            best[j] = sorted(best[j], key=lambda t: -t[1])[:k]
    return best


def kmeans(m: np.ndarray, k: int, *, iters: int = 12, seed: int = 0,
           sample: int = 50000) -> tuple[np.ndarray, np.ndarray]:
    """Spherical k-means on normalised rows. Deterministic (seeded k-means++). Returns (C, sizes)."""
    n = m.shape[0]
    k = min(k, n)
    if k <= 0:
        return np.empty((0, m.shape[1] if m.ndim == 2 else 0), np.float32), np.empty(0, np.int64)
    rng = np.random.default_rng(seed)
    x = m if n <= sample else m[np.sort(rng.choice(n, sample, replace=False))]
    first = int(rng.integers(x.shape[0]))
    c = [x[first]]
    d = 1.0 - x @ c[0]
    for _ in range(1, k):
        d = np.clip(d, 0, None)
        total = float(d.sum())
        nxt = int(rng.choice(x.shape[0], p=d / total)) if total > 0 else int(rng.integers(x.shape[0]))
        c.append(x[nxt])
        d = np.minimum(d, 1.0 - x @ x[nxt])
    cent = np.array(c, dtype=np.float32)
    for _ in range(iters):
        assign = np.argmax(x @ cent.T, axis=1)
        new = np.zeros_like(cent)
        np.add.at(new, assign, x)
        counts = np.bincount(assign, minlength=k)
        empty = counts == 0
        new[empty] = cent[empty]
        normalise_inplace(new)
        if np.allclose(new, cent, atol=1e-6):
            cent = new
            break
        cent = new
    sizes = np.bincount(np.argmax(m @ cent.T, axis=1) if n <= sample else
                        np.argmax(x @ cent.T, axis=1), minlength=k)
    return cent, sizes


class MatrixCache:
    """Holds at most ``max_stores`` matrices (one copy each), keyed by file identity + model."""

    def __init__(self, max_stores: int = 8) -> None:
        self._max = max_stores
        self._items: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
        self._loading: dict = {}                   # per-key loader locks (single flight)

    def get(self, path: Path, model: str) -> tuple[list[str], np.ndarray]:
        """Single-flight per (file, model): concurrent misses wait for ONE loader rather than
        each allocating a full copy of the matrix (the peak this module exists to prevent)."""
        key = (str(path.resolve()), model)
        with self._lock:
            slot = self._loading.setdefault(key, [threading.Lock(), 0])
            slot[1] += 1                            # waiters on this key's loader
        try:
            with slot[0]:
                return self._get_locked(key, path, model)
        finally:
            with self._lock:
                slot[1] -= 1
                if slot[1] == 0 and self._loading.get(key) is slot:
                    del self._loading[key]          # no waiter left: the registry is released

    def _get_locked(self, key: tuple, path: Path, model: str) -> tuple[list[str], np.ndarray]:
        st = path.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        with self._lock:
            hit = self._items.get(key)
            if hit is not None and hit[0] == stamp:
                self._items.move_to_end(key)
                return hit[1], hit[2]
            self._items.pop(key, None)         # drop a stale copy before loading the new one
        from .store import connect

        conn = connect(path, readonly=True)
        try:
            ids, m = load_matrix(conn, model)
        finally:
            conn.close()
        with self._lock:
            self._items[key] = (stamp, ids, m)
            self._items.move_to_end(key)
            while len(self._items) > self._max:
                self._items.popitem(last=False)
        return ids, m

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._loading.clear()


MATRICES = MatrixCache()
