"""The router — a small index describing every published store (principles P14 "Routing").

``router.json`` in the passages dir holds one entry per store: file, source_id, tier, a one-line
human **scope**, document/chunk/embedding counts, the embedding model (and whether it was the
labelled offline fallback), **search_ram_bytes / search_ram_mb** (``null`` when unknown — never
``0``), the centroid count, and when it was published. The centroids themselves live inside each
store (``centroids`` table) and are read on demand.

The index is a cache: stores are found by **schema**, and an entry whose file changed since it
was written is rebuilt from the store itself, so a stale or missing ``router.json`` never hides a
store or advertises one that is gone.

Routing a question: embed it, score each compatible store by its best centroid similarity, keep
the top stores. Routing only prunes when there are more stores than ``route_top``; below that
every store is searched exhaustively (there is nothing to save, and nothing to lose).
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

import numpy as np

from . import store as st
from .vectors import search_ram_bytes, search_ram_mb

ROUTER_FILE = "router.json"


def _entry_for(path: Path) -> dict[str, Any]:
    conn = st.connect(path, readonly=True)
    try:
        meta = st.all_meta(conn)
        c = st.counts(conn)
        dim = meta.get("embed_dim")
        try:
            n_cent = conn.execute("SELECT COUNT(*) FROM centroids").fetchone()[0]
        except sqlite3.Error:
            n_cent = 0                      # a legacy store without centroids: unroutable, not fatal
    finally:
        conn.close()
    stat = path.stat()
    return {
        "file": path.name,
        "source_id": meta.get("source_id"),
        "tier": meta.get("tier"),
        "scope": meta.get("scope"),
        "documents": c["documents"], "chunks": c["chunks"], "embeddings": c["embeddings"],
        "excluded_chunks": c["excluded_chunks"],
        "embed_model": meta.get("embed_model"),
        "embed_dim": dim,
        "offline_embedding": bool(meta.get("embed_offline")),
        "embedding_label": meta.get("embed_label"),
        "search_ram_bytes": search_ram_bytes(c["embeddings"], dim),
        "search_ram_mb": search_ram_mb(c["embeddings"], dim),
        "centroids": n_cent,
        "published_at": meta.get("published_at"),
        "throughput": meta.get("throughput"),
        "_stamp": [stat.st_mtime_ns, stat.st_size],
    }


def _write(root: Path, entries: list[dict]) -> None:
    tmp = root / f".{ROUTER_FILE}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps({"version": 1, "stores": entries}, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, root / ROUTER_FILE)


def load(root: Path, *, write_back: bool = False) -> list[dict[str, Any]]:
    """Every published store's entry, rebuilt (in memory) for any store whose file changed.

    READ-ONLY by default: search and stats run as read-only agents, which never write (P12). Only
    publication (``refresh``) rewrites ``router.json``; a stale cache costs a re-read, not truth."""
    cached: dict[str, dict] = {}
    f = root / ROUTER_FILE
    if f.is_file():
        try:
            for e in json.loads(f.read_text(encoding="utf-8")).get("stores", []):
                cached[e.get("file")] = e
        except (ValueError, AttributeError):
            cached = {}
    entries, changed = [], False
    for path in st.discover(root):
        try:
            stat = path.stat()
            e = cached.get(path.name)
            if e is None or e.get("_stamp") != [stat.st_mtime_ns, stat.st_size]:
                e = _entry_for(path)
                changed = True
        except (sqlite3.Error, OSError, ValueError, KeyError) as err:
            # one damaged store is reported, never allowed to abort every other store (P7)
            e = {"file": path.name, "source_id": None, "tier": None,
                 "error": f"{type(err).__name__}: {err}"}
            changed = True
        entries.append(e)
    if len(entries) != len(cached):
        changed = True
    if changed and write_back and root.is_dir():
        try:
            _write(root, entries)
        except OSError:
            pass                                    # a read-only dir still routes, uncached
    return entries


def refresh(root: Path) -> list[dict[str, Any]]:
    """Rewrite the cache — called by publication, the one writer."""
    return load(root, write_back=True)


def centroids(root: Path, entry: dict) -> Optional[np.ndarray]:
    conn = st.connect(root / entry["file"], readonly=True)
    try:
        rows = conn.execute("SELECT vector FROM centroids ORDER BY idx").fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    return np.vstack([np.frombuffer(r[0], dtype=np.float32) for r in rows])


def route(root: Path, entries: list[dict], q: np.ndarray, *, top: int
          ) -> tuple[list[dict], list[dict]]:
    """``(chosen, skipped)`` — skipped entries carry a ``reason``."""
    if len(entries) <= top:
        return list(entries), []
    scored = []
    undescribed = []
    for e in entries:
        try:
            c = centroids(root, e)
        except (sqlite3.Error, OSError, ValueError):
            c = None                        # unreadable description: searched, not routed out
        if c is None or c.ndim != 2 or c.shape[1] != q.shape[0]:
            undescribed.append(e)           # cannot route what the router cannot describe
            continue
        scored.append((float(np.max(c @ q)), e))
    scored.sort(key=lambda t: -t[0])
    chosen = undescribed + [e for _, e in scored[:max(0, top - len(undescribed))]]
    chosen_files = {e["file"] for e in chosen}
    skipped = [{"store": e["file"], "source_id": e.get("source_id"),
                "reason": f"routed out: best centroid similarity {s:.3f}"}
               for s, e in scored if e["file"] not in chosen_files]
    return chosen, skipped


def public_view(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if not k.startswith("_")}
