"""``python -m databridge_retrieval {index,search,stats}``.

    index  --source S [--tier T] [--sha SHA ...] [--all] [--force] [--scope TEXT] [--pack P]
           Index captured items into S's store and publish. ``--all`` indexes every capture whose
           ``.json`` names source S. Refuses an empty or >50%-shrunk publication unless --force
           (empty is refused always).
    search QUERY [--k 8] [--mode hybrid|vector|keyword] [--depth standard|facets|rerank]
           [--source S ...] [--kind K ...] [--json]
    stats  [--json]      the router view: every store, counts, scope, search_ram_mb, model

Paths come from WALNUT_PASSAGES_DIR / WALNUT_CAPTURE_DIR (defaults under databridge/var/).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from . import paths
from .indexer import BuildSession, read_capture
from .models import PublishRefused
from .search import Retriever, stats


def _captures_for(source: str, root: Path) -> list[tuple[str, Optional[str]]]:
    """``(sha, uri)`` for every sighting of a capture by ``source`` — one capture seen at two
    paths is two documents (identity is the path), and each is indexed under ITS path."""
    out: list[tuple[str, Optional[str]]] = []
    if not root.is_dir():
        return out
    for meta_p in sorted(root.glob("*/*.json")):
        sha = meta_p.stem
        if len(sha) != 64:
            continue
        _, meta = read_capture(sha, root)
        seen = [{"source": meta.get("source"), "uri": meta.get("uri")}]
        seen += [s for s in meta.get("sightings") or [] if isinstance(s, dict)]
        for s in seen:
            if s.get("source") == source:
                out.append((sha, s.get("uri")))
    return out


def _cmd_index(a: argparse.Namespace) -> int:
    cap_root = paths.capture_dir()
    items: list[tuple[str, Optional[str]]] = [(sha, None) for sha in a.sha or []]
    if a.all:
        items += _captures_for(a.source, cap_root)
    if not items:
        print(f"nothing to index for source {a.source!r} (capture dir {cap_root})",
              file=sys.stderr)
        return 2
    session = BuildSession(a.source, a.tier, pack=a.pack, scope=a.scope, force=a.force)
    session.open()
    try:
        for sha, uri in dict.fromkeys(items):
            meta = {"source": a.source, **({"uri": uri} if uri else {})}
            r = session.index_capture(sha, meta)
            print(f"{r.status:16s} {r.doc_id[:16]} chunks={r.chunks} excluded={r.excluded} "
                  f"reused={r.reused_vectors} {r.detail}", flush=True)
    except BaseException:
        session.abort()
        raise
    try:
        rep = session.publish()
    except PublishRefused as err:
        print(f"REFUSED: {err}", file=sys.stderr)
        return 3
    print(json.dumps(rep.__dict__, indent=2, default=str))
    if rep.offline_embedding:
        print(f"NOTE: {rep.embedding_label}", file=sys.stderr)
    return 0


def _cmd_search(a: argparse.Namespace) -> int:
    r = Retriever()
    res = (r.search(a.query, k=a.k, sources=a.source, kinds=a.kind, mode=a.mode)
           if a.depth == "standard" else
           r.deep_search(a.query, depth=a.depth, k=a.k, sources=a.source, kinds=a.kind,
                         mode=a.mode))
    if a.json:
        print(json.dumps({"results": [p.model_dump(mode="json") for p in res],
                          **res.describe()}, indent=2))
        return 0
    if res.offline_embedding:
        print(f"[{res.embedding_label}]")
    for n in res.notices:
        print(f"note: {n}")
    for i, p in enumerate(res, 1):
        loc = p.locator + (" (approx.)" if p.approx_locator and "approx" not in p.locator else "")
        print(f"{i}. [{p.score:.4f}] {p.source_id} {p.doc_id[:12]} {loc} "
              f"chars {p.pointer.char_start}-{p.pointer.char_end}")
        print("   " + p.text[:240].replace("\n", " "))
    if not res:
        print("no passages")
    return 0


def _cmd_stats(a: argparse.Namespace) -> int:
    rows = stats()
    if a.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print(f"no published passage stores in {paths.passages_dir()}")
    for e in rows:
        ram = "unknown" if e.get("search_ram_mb") is None else f"{e['search_ram_mb']} MB"
        print(f"{e['file']}: {e['documents']} docs, {e['chunks']} chunks, "
              f"{e['centroids']} centroids, search RAM {ram}, model {e['embed_model']}"
              + (" (OFFLINE)" if e.get("offline_embedding") else ""))
        print(f"    scope: {e.get('scope')}")
        if e.get("throughput"):
            print(f"    throughput: {e['throughput']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m databridge_retrieval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("index")
    i.add_argument("--source", required=True)
    i.add_argument("--tier", default="default")
    i.add_argument("--sha", action="append")
    i.add_argument("--all", action="store_true")
    i.add_argument("--force", action="store_true")
    i.add_argument("--scope")
    i.add_argument("--pack")
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--k", type=int, default=8)
    s.add_argument("--mode", default="hybrid", choices=["hybrid", "vector", "keyword"])
    s.add_argument("--depth", default="standard", choices=["standard", "facets", "rerank"])
    s.add_argument("--source", action="append")
    s.add_argument("--kind", action="append")
    s.add_argument("--json", action="store_true")
    t = sub.add_parser("stats")
    t.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    return {"index": _cmd_index, "search": _cmd_search, "stats": _cmd_stats}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
