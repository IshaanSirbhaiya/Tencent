"""The per-chunk screen — the SECOND pass of the deterministic gate (P1: "and again per chunk").

The rules and the gate are the ingest pipeline's own (``databridge_mcp_ingest.screen``: the
``screen.yaml`` v1 schema, ``load_screen``, ``screen_text``) whenever that package is importable, so
there is one gate, not two that drift. When it is not importable (retrieval used standalone), a
minimal loader for the SAME schema is used instead — same three outcomes, same checksum-validated,
SAME-LENGTH masks (so chunk offsets still slice the captured text).

A chunk that screens ``block`` or ``review`` is not stored; it is written to the store's
``excluded_chunks`` table with the rule ids and the reversal text at the moment it happens (P3).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from . import paths


@dataclass
class ChunkScreen:
    decision: str            # allow | review | block
    score: float
    rules: list[str]
    reverse: str
    rationale: str
    masked_text: str
    redactions: list[dict] = field(default_factory=list)


def _digest_of(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
                          ).hexdigest()


class Screen:
    """A loaded screen: ``evaluate(text) -> ChunkScreen``. ``source`` says which file/impl.

    ``digest`` identifies the screening POLICY — the parsed rules and thresholds, not the file
    path or its comments. A store records the digest it was screened under; when the current
    screen's digest differs, every document is re-screened at publication (R-019 round 3, #2), so
    a rule change reaches content whose capture did not change. A screen built from a bare
    callable has no rules to hash, so its digest is that of its ``source`` label."""

    def __init__(self, evaluate: Callable[..., ChunkScreen], source: str,
                 digest: Optional[str] = None) -> None:
        self._evaluate = evaluate
        self.source = source
        self.digest = digest or _digest_of({"source": source})

    def evaluate(self, text: str, *, override_allow: bool = False) -> ChunkScreen:
        return self._evaluate(text, override_allow=override_allow)


def _from_ingest(pack: Optional[str], path: Optional[Path]) -> Optional[Screen]:
    try:
        from databridge_mcp_ingest.screen import load_screen, screen_text
    except Exception:            # noqa: BLE001 — not importable: use the local loader
        return None
    cfg = load_screen([pack] if pack else None, path=path)

    def evaluate(text: str, *, override_allow: bool = False) -> ChunkScreen:
        r = screen_text(text, cfg, override_allow=override_allow)
        return ChunkScreen(r.decision, r.score, [f["rule"] for f in r.fired], r.reverse,
                           r.rationale, r.masked_text, list(r.redactions))

    policy = {k: v for k, v in dataclasses.asdict(cfg).items() if k not in ("source", "notes")}
    return Screen(evaluate, f"databridge_mcp_ingest.screen:{cfg.source}", _digest_of(policy))


# --- minimal local loader for the same schema (fallback only) ------------------------------------
_TOP = {"version", "pack", "thresholds", "decisive", "signals", "redactions"}


def _luhn(v: str) -> bool:
    d = re.sub(r"\D", "", v)
    if len(d) < 12:
        return False
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _mod97(v: str) -> bool:
    s = re.sub(r"[\s-]", "", v).upper()
    if len(s) < 5 or not s.isalnum():
        return False
    s = s[4:] + s[:4]
    try:
        return int("".join(str(int(c, 36)) for c in s)) % 97 == 1
    except ValueError:
        return False


_VALIDATE = {"none": None, None: None, "luhn": _luhn, "mod97": _mod97, "iban": _mod97}


def _local(path: Path) -> Screen:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    unknown = set(data) - _TOP
    if unknown:
        raise ValueError(f"{path}: unknown screen keys {sorted(unknown)}")
    th = data.get("thresholds") or {}
    block_t, review_t = float(th.get("block", 3)), float(th.get("review", 2))
    min_sig = int(th.get("min_signals_to_block", 2))
    comp = lambda rules: [dict(r, _re=re.compile(r["pattern"])) for r in (rules or [])]  # noqa: E731
    decisive, signals, redactions = (comp(data.get("decisive")), comp(data.get("signals")),
                                     comp(data.get("redactions")))
    for r in redactions:
        if r.get("validate") not in _VALIDATE:
            raise ValueError(f"{path}: unknown validate {r.get('validate')!r}")

    def mask(text: str) -> tuple[str, list[dict]]:
        out: list[dict] = []
        for r in redactions:
            check = _VALIDATE[r.get("validate")]
            rep = str(r.get("replacement", ""))
            pieces, last = [], 0
            for m in r["_re"].finditer(text):
                s, e = m.span()
                if s == e or "*" in m.group(0) or (check and not check(m.group(0))):
                    continue
                pieces += [text[last:s], (rep + "*" * (e - s - len(rep))) if e - s >= len(rep)
                           else "*" * (e - s)]
                last = e
                out.append({"rule": r["id"], "char_start": s, "char_end": e})
            if pieces:
                text = "".join(pieces) + text[last:]
        return text, out

    def evaluate(text: str, *, override_allow: bool = False) -> ChunkScreen:
        masked, reds = mask(text)
        hit = [r for r in decisive if r["_re"].search(text)]
        if hit:
            return ChunkScreen("block", float("inf"), [r["id"] for r in hit],
                               hit[0].get("reverse", ""), "decisive rule matched", masked, reds)
        fired = [r for r in signals
                 if len(r["_re"].findall(text)) >= int(r.get("min_count", 1))]
        score = float(sum(r.get("weight", 1) for r in fired))
        ids = [r["id"] for r in fired]
        rev = next((r.get("reverse", "") for r in fired if r.get("reverse")), "")
        if score >= block_t and len(fired) >= min_sig:
            return ChunkScreen("block", score, ids, rev, "corroborated signals", masked, reds)
        if score >= review_t and not override_allow:
            return ChunkScreen("review", score, ids, rev, "review band", masked, reds)
        return ChunkScreen("allow", score, ids, "", "allowed", masked, reds)

    return Screen(evaluate, f"local:{path}", _digest_of(data))


def load_chunk_screen(pack: Optional[str] = None, *, path: Optional[Path] = None) -> Screen:
    """The ingest pipeline's screen if importable, else the local loader over the same file."""
    pack = pack or paths.default_pack()
    got = _from_ingest(pack, path)
    if got is not None:
        return got
    candidate = Path(path) if path is not None else paths.packs_dir() / pack / "screen.yaml"
    if not candidate.is_file():
        raise FileNotFoundError(
            f"no screen for pack {pack!r} at {candidate}: the per-chunk gate cannot run, so "
            "nothing is indexed (P1). Pass an explicit screen path or install the pack's "
            "screen.yaml.")
    return _local(candidate)


def screen_record(result: ChunkScreen) -> dict[str, Any]:
    return {"decision": result.decision, "rules": result.rules, "reverse": result.reverse,
            "rationale": result.rationale,
            "score": None if result.score == float("inf") else result.score}
