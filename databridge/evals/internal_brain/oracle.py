"""Ground truth that does NOT come from the system under test.

* **Who may read what** comes from the world generator's own ``Acl`` (``fixtures/enterprise/
  generate.py``), the rule set the fact manifest's permitted/denied lists were computed from and
  which ``fixtures/enterprise/tests`` checks against a second, separately written evaluator. The
  IB index, connectors and Gate 2 are never consulted for the oracle.
* **What text belongs to which document** comes from the generator's ``doc_texts`` (title, body,
  history, comments). Leak detection uses 10-word shingles that occur in some document the persona
  may NOT read and in no document they MAY read ("distinctive" shingles), plus every ``CNRY-xxxxxx``
  canary token, plus the fact sentinels.
* **World variants** for P2 and the smoke test: a filtered copy of ``world.json`` with a set of
  documents removed (containers, directory, facts and questions kept; links left as written).
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from . import GENERATOR, WORLD

SHINGLE = 10
_WORD = re.compile(r"[a-z0-9]+")
_CANARY = re.compile(r"CNRY-[0-9a-f]{6}")
_TAG = re.compile(r"\[C\d+\]")


def _generator():
    name = "_ib_eval_world_generator"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, GENERATOR)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)                     # type: ignore[union-attr]
    return mod


def load_world(path: Optional[Path] = None) -> dict:
    return json.loads(Path(path or WORLD).read_text(encoding="utf-8"))


def norm(text: str) -> str:
    return " ".join((text or "").split())


def words(text: str) -> list[str]:
    return _WORD.findall(_TAG.sub(" ", text or "").lower())


_SEGMENT = re.compile(r"(?<=[.!?])\s+|\n+")
MIN_SEGMENT = 6


def shingles(text: str, n: int = SHINGLE) -> set[str]:
    """n-word shingles that never cross a sentence or line boundary (an extractive answer stitches
    sentences from different passages; a window spanning the seam is not text of any one doc).
    A sentence shorter than n words (but at least MIN_SEGMENT) is one shingle on its own."""
    out: set[str] = set()
    for seg in _SEGMENT.split(_TAG.sub(" ", text or "")):
        w = words(seg)
        if len(w) >= n:
            out.update(" ".join(w[i:i + n]) for i in range(len(w) - n + 1))
        elif len(w) >= MIN_SEGMENT:
            out.add(" ".join(w))
    return out


def canaries(text: str) -> set[str]:
    return set(_CANARY.findall(text or ""))


class Oracle:
    """Readers per document (generator rules) + per-persona forbidden text fingerprints."""

    def __init__(self, world: dict) -> None:
        gen = _generator()
        self.world = world
        self.acl = gen.Acl(world)
        self.texts: dict[str, str] = gen.doc_texts(world)
        self.readers: dict[str, frozenset[str]] = {d: frozenset(self.acl.readers(d)) for d in self.texts}
        self.facts = {f["id"]: f for f in world["facts"]}
        self.users = {u["id"]: u for u in world["directory"]["users"]}
        self._doc_sh = {d: shingles(t) for d, t in self.texts.items()}
        self._doc_cn = {d: canaries(t) for d, t in self.texts.items()}
        self._fp: dict[str, tuple[dict[str, str], dict[str, str]]] = {}

    # -- access ---------------------------------------------------------------------------------
    def can_read(self, persona: str, doc_id: str) -> bool:
        return persona in self.readers.get(doc_id, frozenset())

    def readable(self, persona: str) -> set[str]:
        return {d for d, r in self.readers.items() if persona in r}

    def unreadable(self, persona: str) -> set[str]:
        return set(self.readers) - self.readable(persona)

    def denied_facts(self, persona: str) -> list[str]:
        """Facts the persona may not see. For the seven manifest personas this is the manifest's
        own ``denied`` list; for any other user it is derived from the generator ACL."""
        out = []
        for fid, f in self.facts.items():
            if f.get("applies_after"):
                continue
            if persona in f["denied"] or persona in f["permitted"]:
                if persona in f["denied"]:
                    out.append(fid)
            elif not self.can_read(persona, f["doc"]):
                out.append(fid)
        return out

    def permitted_facts(self, persona: str) -> list[str]:
        out = []
        for fid, f in self.facts.items():
            if f.get("applies_after"):
                continue
            if persona in f["permitted"] or (persona not in f["denied"] and
                                             self.can_read(persona, f["doc"])):
                out.append(fid)
        return out

    def manifest_vs_acl(self) -> list[dict]:
        """Sanity: the manifest's permitted/denied lists agree with the generator ACL."""
        bad = []
        for fid, f in self.facts.items():
            if f.get("applies_after"):
                continue
            for p in f["permitted"]:
                if not self.can_read(p, f["doc"]):
                    bad.append({"fact": fid, "persona": p, "manifest": "permitted", "acl": "deny"})
            for p in f["denied"]:
                if self.can_read(p, f["doc"]):
                    bad.append({"fact": fid, "persona": p, "manifest": "denied", "acl": "allow"})
        return bad

    # -- forbidden text fingerprints ------------------------------------------------------------
    def fingerprints(self, persona: str) -> tuple[dict[str, str], dict[str, str]]:
        """``(shingle → doc, canary → doc)`` for documents the persona may NOT read, minus any
        shingle that also occurs in a document they MAY read (shared filler is not a leak)."""
        if persona in self._fp:
            return self._fp[persona]
        ok = self.readable(persona)
        allowed_sh: set[str] = set()
        for d in ok:
            allowed_sh |= self._doc_sh[d]
        sh: dict[str, str] = {}
        cn: dict[str, str] = {}
        for d in sorted(set(self.texts) - ok):
            for s in self._doc_sh[d]:
                if s not in allowed_sh and s not in sh:
                    sh[s] = d
            for c in self._doc_cn[d]:
                cn.setdefault(c, d)
        allowed_cn = set().union(*(self._doc_cn[d] for d in ok)) if ok else set()
        cn = {c: d for c, d in cn.items() if c not in allowed_cn}
        self._fp[persona] = (sh, cn)
        return sh, cn

    def forbidden_hits(self, persona: str, text: str) -> list[dict]:
        """Every forbidden fingerprint (shingle / canary / denied-fact sentinel) found in ``text``."""
        sh, cn = self.fingerprints(persona)
        hits: list[dict] = []
        seen_docs = set()
        for s in shingles(text) & set(sh):
            d = sh[s]
            if d not in seen_docs:
                seen_docs.add(d)
                hits.append({"kind": "doc_text", "doc": d, "evidence": s})
        for c in canaries(text):
            if c in cn:
                hits.append({"kind": "canary", "doc": cn[c], "evidence": c})
        nt = norm(text)
        for fid in self.denied_facts(persona):
            f = self.facts[fid]
            if norm(f["statement"])[:60] in nt:
                hits.append({"kind": "sentinel", "doc": f["doc"], "fact": fid,
                             "evidence": norm(f["statement"])[:60]})
        return hits


# -- world variants ------------------------------------------------------------------------------
def filter_world(world: dict, drop: Iterable[str]) -> tuple[dict, dict]:
    """A deep copy of ``world`` without the documents in ``drop`` (doc ids). Containers (spaces,
    projects, channels, drives, folders), the directory, facts and questions are kept unchanged;
    links inside kept documents are left exactly as written. Returns ``(world, report)``."""
    drop = set(drop)
    w = copy.deepcopy(world)
    cf = w["confluence"]
    kept_pages = [p for p in cf["pages"] if f"confluence:{p['id']}" not in drop]
    kept_ids = {p["id"] for p in kept_pages}
    orphans = [p["id"] for p in kept_pages if p["parent"] and p["parent"] not in kept_ids]
    cf["pages"] = kept_pages
    jr = w["jira"]
    jr["issues"] = [i for i in jr["issues"] if f"jira:{i['key']}" not in drop]
    sl = w["slack"]
    sl["threads"] = [t for t in sl["threads"] if f"slack:{t['channel']}:{t['ts']}" not in drop]
    dr = w["drive"]
    dr["files"] = [f for f in dr["files"] if f"drive:{f['id']}" not in drop]
    report = {"dropped": len(drop), "orphaned_pages": orphans,
              "kept": {"confluence": len(cf["pages"]), "jira": len(jr["issues"]),
                       "slack": len(sl["threads"]), "drive": len(dr["files"])}}
    return w, report


def write_world(world: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(world, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    return path


def all_doc_ids(world: dict) -> list[str]:
    return list(_generator().doc_texts(world))


@lru_cache(maxsize=1)
def base_world() -> dict:
    return load_world()
