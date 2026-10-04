"""The deterministic screen — runs BEFORE any model, twice (per document, then per chunk). P1/P2/P13.

Pattern matching and arithmetic only: no network, no model, no telemetry. Asking a model whether
content is too sensitive to show a model is already the disclosure this gate exists to prevent.

Schema: `screen.yaml` version 1, as documented in `packs/workspace/screen.yaml` (thresholds,
decisive, signals, redactions). Three outcomes (P2):

    decisive rule matches                                   → block (that rule, full stop)
    score >= thresholds.block AND >= min_signals_to_block
        distinct signals fired                              → block (corroborated)
    score >= thresholds.review                              → review (excluded, listed for an
                                                              owner override)
    otherwise                                               → allow

Redactions run IN ORDER; a span is masked only when its checksum passes (`luhn`, `mod97` — shape
is not enough, trap 11). The mask is the rule's `replacement` padded with `*` to the SAME LENGTH as
the masked span, so every offset into the masked text is the same offset into the captured text:
a pointer minted against what the model saw resolves against what was captured.

Rules come from the first enabled pack shipping `packs/<pack>/screen.yaml`, else from
`defaults/screen.yaml`. The default DECISIVE (credential) rules are ENGINE POLICY, not pack
configuration (R-022 #3): they are generic credential shapes, not domain behaviour, and no pack can
disable, narrow or replace them. `load_screen` puts them first under their own ids; a pack rule
that reuses a reserved id with a DIFFERENT pattern is kept as an additional rule (`<id>.pack`) and
noted — additive, never a replacement — and `screen_text` re-applies any that a hand-built
`ScreenConfig` lacks. Unknown keys and duplicate rule ids are an error, as in the pack loader.

**Overrides (R-022 #2).** An item (or a table row) leaves the REVIEW band only through
`approve_override`, which takes an AUTHENTICATED principal (`plans.require_authenticated_approver`)
and appends a record — principal id, mechanism, time, reason and the DIGEST of the content approved
— to `<sources>/<source>.override_approvals.jsonl` (append-only; a revocation is a new line). A
free-form `by:` in a YAML file is a claim, not an approval: the old `<source>.overrides.yaml` is
no longer read. A block is never overridable. Each stored line is SEALED (R-022 f2 #1): an HMAC
under a deployment approval key kept outside the sources directory, chained to the line before
it, so an appended forgery fails its seal and a removed or reordered line breaks the chain (which
disables every approval in the log — fail closed).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import yaml

from . import paths

_TOP_KEYS = frozenset({"version", "pack", "thresholds", "decisive", "signals", "redactions",
                       "quarantine"})
_THRESHOLD_KEYS = frozenset({"block", "review", "min_signals_to_block"})
_RULE_KEYS = {
    "decisive": frozenset({"id", "description", "pattern", "reverse"}),
    "signals": frozenset({"id", "description", "pattern", "weight", "min_count", "reverse"}),
    "redactions": frozenset({"id", "description", "pattern", "validate", "replacement"}),
    "quarantine": frozenset({"id", "description", "pattern", "reverse", "min_count"}),
}


class ScreenError(ValueError):
    """A screen file is malformed. Refused, never guessed around."""


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern
    weight: float = 1.0
    min_count: int = 1
    reverse: str = ""
    replacement: str = ""
    validate: Optional[str] = None


@dataclass
class ScreenConfig:
    decisive: list[Rule]
    signals: list[Rule]
    redactions: list[Rule]
    block_threshold: float = 2.0
    review_threshold: float = 1.0
    min_signals_to_block: int = 2
    source: str = "defaults"
    notes: list[str] = field(default_factory=list)
    #: FIN-17: instruction-like content addressed to an automated reader (prompt injection). Any
    #: match HOLDS the text in the review band — never extracted, listed for a human — whatever
    #: the score. Matched against `detection_text` (format characters removed, NFKC), so text
    #: split by zero-width characters is read as the words it shows.
    quarantine: list[Rule] = field(default_factory=list)


@dataclass
class ScreenResult:
    decision: str                               # allow | block | review
    score: float
    fired: list[dict]                           # [{rule, kind, count, weight}]
    redactions: list[dict]                      # [{rule, char_start, char_end}]
    masked_text: str
    rationale: str
    reverse: str

    def as_record(self) -> dict:
        return {"decision": self.decision,
                "score": None if self.score == float("inf") else self.score,
                "fired": self.fired, "redactions": self.redactions,
                "rationale": self.rationale, "reverse": self.reverse}


# --- checksums (P13: shape is not enough) --------------------------------------------------------
def _digits_only(value: str) -> str:
    return re.sub(r"[ \-]", "", value)


def luhn_ok(value: str) -> bool:
    s = _digits_only(value)
    if not s.isdigit() or not 13 <= len(s) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(s)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def mod97_ok(value: str) -> bool:
    s = _digits_only(value).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", s):
        return False
    rearranged = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1


_VALIDATORS = {"luhn": luhn_ok, "mod97": mod97_ok, "iban": mod97_ok}


# --- loading ----------------------------------------------------------------------------------
def _rules(raw, section: str, where: str) -> list[Rule]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ScreenError(f"{where}: `{section}` must be a list")
    out = []
    seen_ids: set[str] = set()
    seen_patterns: set[str] = set()
    for i, r in enumerate(raw):
        if not isinstance(r, dict):
            raise ScreenError(f"{where}: {section}[{i}] is not a mapping")
        unknown = sorted(set(r) - _RULE_KEYS[section])
        if unknown:
            raise ScreenError(f"{where}: {section}[{i}] has unknown key(s) {unknown}")
        if not r.get("id") or not r.get("pattern"):
            raise ScreenError(f"{where}: {section}[{i}] needs `id` and `pattern`")
        try:
            rx = re.compile(str(r["pattern"]))
        except re.error as exc:
            raise ScreenError(f"{where}: rule {r['id']!r} pattern does not compile: {exc}") from exc
        # R-022 #11: one signal declared twice would count as two corroborating signals
        rid, pat = str(r["id"]), str(r["pattern"])
        if rid in seen_ids:
            raise ScreenError(f"{where}: {section} declares rule id {rid!r} twice")
        if pat in seen_patterns:
            raise ScreenError(f"{where}: {section} rule {rid!r} repeats another rule's pattern "
                              f"(one signal declared twice is still one signal)")
        seen_ids.add(rid)
        seen_patterns.add(pat)
        validate = r.get("validate")
        validate = None if validate in (None, "none") else str(validate)
        if validate is not None and validate not in _VALIDATORS:
            raise ScreenError(f"{where}: rule {r['id']!r} names unknown validator {validate!r}")
        out.append(Rule(id=str(r["id"]), pattern=rx, weight=float(r.get("weight", 1)),
                        min_count=int(r.get("min_count", 1)), reverse=str(r.get("reverse") or ""),
                        replacement=str(r.get("replacement") or f"[{str(r['id']).upper()}]"),
                        validate=validate))
    return out


def parse_screen(data: dict, where: str) -> ScreenConfig:
    if not isinstance(data, dict):
        raise ScreenError(f"{where}: top level must be a mapping")
    unknown = sorted(set(data) - _TOP_KEYS)
    if unknown:
        raise ScreenError(f"{where}: unknown key(s) {unknown}")
    th = data.get("thresholds") or {}
    bad = sorted(set(th) - _THRESHOLD_KEYS)
    if bad:
        raise ScreenError(f"{where}: thresholds has unknown key(s) {bad}")
    return ScreenConfig(
        decisive=_rules(data.get("decisive"), "decisive", where),
        signals=_rules(data.get("signals"), "signals", where),
        redactions=_rules(data.get("redactions"), "redactions", where),
        quarantine=_rules(data.get("quarantine"), "quarantine", where),
        block_threshold=float(th.get("block", 2)), review_threshold=float(th.get("review", 1)),
        min_signals_to_block=int(th.get("min_signals_to_block", 2)), source=where)


def _read(path: Path) -> ScreenConfig:
    return parse_screen(yaml.safe_load(path.read_text(encoding="utf-8")) or {}, str(path))


_MANDATORY: Optional[tuple[Rule, ...]] = None
_MANDATORY_QUARANTINE: Optional[tuple[Rule, ...]] = None


def mandatory_rules() -> tuple[Rule, ...]:
    """The engine's credential gate: every DECISIVE rule of `defaults/screen.yaml`. Engine policy —
    applied whatever screen a pack ships and whatever `ScreenConfig` a caller builds."""
    global _MANDATORY
    if _MANDATORY is None:
        _MANDATORY = tuple(_read(paths.default_screen_path()).decisive)
    return _MANDATORY


def mandatory_quarantine_rules() -> tuple[Rule, ...]:
    """FIN-17: the engine's prompt-injection hold — every QUARANTINE rule of
    `defaults/screen.yaml`. Engine policy like the credential gate: structural shapes of text
    addressed to an automated reader, not domain behaviour, so no pack can disable or narrow
    them (a pack may add its own)."""
    global _MANDATORY_QUARANTINE
    if _MANDATORY_QUARANTINE is None:
        _MANDATORY_QUARANTINE = tuple(_read(paths.default_screen_path()).quarantine)
    return _MANDATORY_QUARANTINE


def _with_mandatory(decisive: list[Rule], where: str, *, section: str = "decisive",
                    mandatory_of=None) -> tuple[list[Rule], list[str]]:
    """The mandatory rules FIRST under their own ids, then the pack's. A pack rule identical to a
    mandatory one (same id, same pattern) is that rule; one that reuses a reserved id with a
    DIFFERENT pattern cannot narrow it: it is kept beside it as `<id>.pack`, and noted."""
    mandatory = list((mandatory_of or mandatory_rules)())
    reserved = {r.id: r.pattern.pattern for r in mandatory}
    notes: list[str] = []
    extra: list[Rule] = []
    for r in decisive:
        if r.id in reserved:
            if r.pattern.pattern == reserved[r.id]:
                continue
            notes.append(f"{where}: {section} rule {r.id!r} reuses a reserved engine "
                         f"rule id with a different pattern; the engine rule is always applied "
                         f"and the pack's is kept beside it as {r.id + '.pack'!r}")
            r = Rule(id=f"{r.id}.pack", pattern=r.pattern, weight=r.weight,
                     min_count=r.min_count, reverse=r.reverse, replacement=r.replacement,
                     validate=r.validate)
        extra.append(r)
    return mandatory + extra, notes


def load_screen(packs: Optional[list[str]] = None, *, path: Optional[Path] = None) -> ScreenConfig:
    """The screen for this deployment. `path` forces a file. The engine credential rules are
    always part of the result (`mandatory_rules`), whatever the file says."""
    defaults = _read(paths.default_screen_path())
    candidate: Optional[Path] = Path(path) if path is not None else None
    if candidate is None:
        if packs is None:
            try:
                from databridge_graph.packs import configured_packs

                packs = list(configured_packs())
            except Exception:                                    # noqa: BLE001
                packs = []
        for pack in packs:
            f = paths.packs_dir() / pack / "screen.yaml"
            if f.is_file():
                candidate = f
                break
    if candidate is None:
        # FIN-03: a pack that declares no screen is screened by the ENGINE DEFAULT policy — the
        # credential gate, the prompt-injection hold and the PII masks of defaults/screen.yaml.
        # Said in every run report (`screen_policy`), never silently.
        defaults.notes.append(f"{DEFAULT_POLICY_NOTE} ({paths.default_screen_path().name}: "
                              f"credential gate, prompt-injection hold, PII masks)")
        return defaults
    cfg = _read(candidate)
    have = {(r.id, r.pattern.pattern) for r in cfg.decisive}
    cfg.decisive, notes = _with_mandatory(cfg.decisive, str(candidate))
    cfg.notes.extend(notes)
    added = [r.id for r in mandatory_rules() if (r.id, r.pattern.pattern) not in have]
    if added:
        cfg.notes.append(f"default credential rules always applied: {added}")
    have_q = {(r.id, r.pattern.pattern) for r in cfg.quarantine}
    cfg.quarantine, notes = _with_mandatory(cfg.quarantine, str(candidate),
                                            section="quarantine",
                                            mandatory_of=mandatory_quarantine_rules)
    cfg.notes.extend(notes)
    added = [r.id for r in mandatory_quarantine_rules()
             if (r.id, r.pattern.pattern) not in have_q]
    if added:
        cfg.notes.append(f"default prompt-injection hold always applied: {added}")
    return cfg


#: The run-report wording when no enabled pack declares a screen (FIN-03).
DEFAULT_POLICY_NOTE = ("no enabled pack ships screen.yaml; the ENGINE DEFAULT screen policy "
                       "is in use")


def pack_screen_file(pack: str) -> Optional[Path]:
    """`packs/<pack>/screen.yaml` when that pack declares a screen; None when it declares none
    (it is then screened by the engine default policy)."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", str(pack)):
        return None                                  # a pack is a directory name, never a path
    f = paths.packs_dir() / str(pack) / "screen.yaml"
    return f if f.is_file() else None


def is_engine_default(cfg: ScreenConfig) -> bool:
    """True when `cfg` is the engine default policy (`defaults/screen.yaml`)."""
    try:
        return Path(str(cfg.source)).resolve() == paths.default_screen_path().resolve()
    except (OSError, ValueError):
        return False


def screen_policy(cfg: ScreenConfig) -> dict:
    """What a run reports about its screen (FIN-03): `policy` is `pack` (that pack's
    `screen.yaml`, plus the engine's mandatory rules), `engine_default` (no enabled pack declares
    a screen) or `file` (a file forced by path)."""
    pack = screen_pack_of(cfg)
    policy = "pack" if pack else ("engine_default" if is_engine_default(cfg) else "file")
    return {"policy": policy, "pack": pack, "source": str(cfg.source),
            "credential_rules": len(cfg.decisive), "quarantine_rules": len(cfg.quarantine),
            "signals": len(cfg.signals), "redactions": len(cfg.redactions)}


def screen_pack_of(cfg: ScreenConfig) -> Optional[str]:
    """The pack whose ``packs/<pack>/screen.yaml`` this config was read from; None for the bundled
    defaults or a file outside the packs root. What an adapter agent's ``spec.screen.pack`` is
    checked against at run time (R-021 D2)."""
    try:
        src = Path(str(cfg.source)).resolve()
        root = paths.packs_dir().resolve()
    except (OSError, ValueError):
        return None
    if src.name == "screen.yaml" and src.parent.parent == root:
        return src.parent.name
    return None


# --- the gate ---------------------------------------------------------------------------------
def _mask_token(replacement: str, n: int) -> str:
    return replacement + "*" * (n - len(replacement)) if n >= len(replacement) else "*" * n


def apply_masks(text: str, rules: list[Rule]) -> tuple[str, list[dict]]:
    """Mask in rule order; later rules see earlier masks (an IBAN's digits are never a card).

    What an earlier rule masked is tracked by SPAN (R-022 #12): a later match overlapping a masked
    span is skipped, and nothing is inferred from the characters a value contains — a raw value
    holding `*` or starting with `[` is masked like any other."""
    redactions = []
    masked_spans: list[tuple[int, int]] = []
    for rule in rules:
        pieces = []
        last = 0
        for m in rule.pattern.finditer(text):
            s, e = m.span()
            if s == e or any(s < me and ms < e for ms, me in masked_spans):
                continue
            if rule.validate and not _VALIDATORS[rule.validate](m.group(0)):
                continue
            pieces.append(text[last:s])
            pieces.append(_mask_token(rule.replacement, e - s))
            last = e
            masked_spans.append((s, e))
            redactions.append({"rule": rule.id, "char_start": s, "char_end": e})
        if pieces:
            pieces.append(text[last:])
            text = "".join(pieces)
    redactions.sort(key=lambda r: r["char_start"])
    return text, redactions


_JSON_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")


def detection_text(text: str) -> str:
    """The text the prompt-injection hold reads (FIN-17): what a reader is SHOWN. Unicode
    compatibility forms folded (NFKC: full-width and styled letters become plain ones), every
    FORMAT character removed (category Cf: zero-width spaces and joiners, bidi controls, soft
    hyphens — the usual way to split a phrase so a pattern misses it), and the same characters
    removed when they arrive JSON-escaped (a table row is screened as its JSON). Never used for
    masking or offsets — only to decide whether to hold."""
    import unicodedata

    def unescape(m: re.Match) -> str:
        ch = chr(int(m.group(1), 16))
        return "" if unicodedata.category(ch) == "Cf" else m.group(0)

    unescaped = _JSON_ESCAPE.sub(unescape, text) if "\\u" in text else text
    if unescaped.isascii():
        # F-02 (E2E run 5): ASCII holds no format character and NFKC leaves it unchanged, so the
        # per-character pass (most of the screen stage on a large table) is skipped — same result
        return unescaped
    folded = unicodedata.normalize("NFKC", unescaped)
    return "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")


_MIME_TRANSFER = re.compile(r"(?im)^content-transfer-encoding:[ \t]*(?:base64|quoted-printable)\b")
_MAX_MIME_PARTS = 64


def detection_variants(text: str) -> list[str]:
    """Every reading of `text` the injection hold checks: the text itself, and — when it is a
    MIME message whose parts are transfer-encoded (base64, quoted-printable) — each decoded text
    part, HTML entities unescaped. An instruction hidden in an encoded body or attachment is what
    a mail client (or a model given the decoded part) would show, so it is read too. Binary parts
    are skipped; a message that does not parse is read as text only."""
    out = [detection_text(text)]
    if not _MIME_TRANSFER.search(text):
        return out
    import email
    import html as _html
    from email import policy as _policy

    try:
        msg = email.message_from_string(text, policy=_policy.compat32)
        for n, part in enumerate(msg.walk()):
            if n >= _MAX_MIME_PARTS:
                break
            if part.is_multipart():
                continue
            payload = part.get_payload(decode=True)
            if not isinstance(payload, (bytes, bytearray)) or not payload or \
                    b"\x00" in payload[:4096]:
                continue
            try:
                decoded = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            except LookupError:
                decoded = payload.decode("utf-8", errors="replace")
            out.append(detection_text(_html.unescape(decoded)))
    except Exception:                                                # noqa: BLE001
        pass                                 # not a parseable message: the text reading stands
    return out


def _quarantine_fired(text: str, rules: list[Rule]) -> list[dict]:
    if not rules:
        return []
    probes = detection_variants(text)
    fired = []
    for rule in rules:
        n = max(sum(1 for _ in rule.pattern.finditer(p)) for p in probes)
        if n >= rule.min_count:
            fired.append({"rule": rule.id, "kind": "quarantine", "count": n, "weight": None})
    return fired


QUARANTINE_REVERSE = ("Possible prompt injection: text addressed to an automated reader. It is "
                      "held (never extracted, never sent to a model) until a person looks. If it "
                      "is legitimate, an authenticated owner approves this content "
                      "(screen.approve_override: principal, reason, content digest) and the "
                      "source is re-run; otherwise remove it at the source.")


def screen_text(text: str, cfg: ScreenConfig, *, override_allow: bool = False,
                also: Iterable[str] = ()) -> ScreenResult:
    """Decide on one text. `override_allow` — the caller holds a VERIFIED override approval for
    exactly this content (`load_overrides(...).allows(item, digest)`) — lifts a REVIEW only.

    FIN-17: a QUARANTINE match (instruction-like text addressed to an automated reader) puts the
    text in the review band whatever its score — held, listed for a human, never extracted. A
    decisive or corroborated block still wins over it. `also`: the SOURCE the text was extracted
    from (an email's raw MIME, whose non-preferred HTML part or hidden comment the extracted text
    leaves out) — read by the injection hold only; the decision is about `text`."""
    fired: list[dict] = []
    decisive = list(cfg.decisive)
    pairs = {(r.id, r.pattern.pattern) for r in decisive}
    missing = [r for r in mandatory_rules() if (r.id, r.pattern.pattern) not in pairs]
    if missing:                          # a hand-built config: the credential gate still applies
        decisive = missing + decisive
    quarantine = list(cfg.quarantine)
    qpairs = {(r.id, r.pattern.pattern) for r in quarantine}
    # a hand-built config: the injection hold still applies
    quarantine = [r for r in mandatory_quarantine_rules()
                  if (r.id, r.pattern.pattern) not in qpairs] + quarantine
    for rule in decisive:
        n = sum(1 for _ in rule.pattern.finditer(text))
        if n:
            fired.append({"rule": rule.id, "kind": "decisive", "count": n, "weight": None})
    masked, redactions = apply_masks(text, cfg.redactions)
    if fired:
        first = next(r for r in decisive if r.id == fired[0]["rule"])
        return ScreenResult("block", float("inf"), fired, redactions, masked,
                            f"decisive rule(s) {[f['rule'] for f in fired]} matched",
                            first.reverse or "remove the matched material from the source item")
    score = 0.0
    reverse = ""
    for rule in cfg.signals:
        n = sum(1 for _ in rule.pattern.finditer(text))
        if n >= rule.min_count:
            score += rule.weight
            fired.append({"rule": rule.id, "kind": "signal", "count": n, "weight": rule.weight})
            reverse = reverse or rule.reverse
    names = [f["rule"] for f in fired]
    distinct = len(set(names))           # R-022 #11: distinct signal identities, not declarations
    if score >= cfg.block_threshold and distinct >= cfg.min_signals_to_block:
        return ScreenResult("block", score, fired, redactions, masked,
                            f"{distinct} corroborating signals {names} scored {score:g} "
                            f">= {cfg.block_threshold:g}",
                            reverse or "the owner re-marks or removes the material")
    held = _quarantine_fired(text, quarantine)
    for source in also:
        seen = {h["rule"] for h in held}
        held += [h for h in _quarantine_fired(source, quarantine) if h["rule"] not in seen]
    if held:
        fired.extend(held)
        if not override_allow:
            return ScreenResult("review", score, fired, redactions, masked,
                                f"possible prompt injection: quarantine rule(s) "
                                f"{[h['rule'] for h in held]} matched text addressed to an "
                                f"automated reader (held for a human: excluded, never "
                                f"extracted)", QUARANTINE_REVERSE)
        names = [f["rule"] for f in fired]
    if score >= cfg.review_threshold and not override_allow:
        how = ("Override: an authenticated owner approves this content "
               "(screen.approve_override: principal, reason, content digest) and the source is "
               "re-run.")
        return ScreenResult("review", score, fired, redactions, masked,
                            f"signal(s) {names} scored {score:g} (review band: excluded, listed "
                            f"for owner override)",
                            reverse if "approve_override" in reverse else
                            (reverse + " " if reverse else "") + how)
    rationale = ("owner override" if score >= cfg.review_threshold or held else
                 ("no rule fired" if not fired else f"signal(s) {names} below the review band"))
    return ScreenResult("allow", score, fired, redactions, masked, rationale, "")


# --- overrides: approvals by an authenticated principal (R-022 #2) --------------------------------
class OverrideRefused(ValueError):
    """An override offered without an authenticated principal, a content digest or a reason."""


_SHA256 = re.compile(r"[0-9a-f]{64}")


def override_log_path(source_id: str, root: Optional[Path] = None) -> Path:
    return paths.contained(root or paths.sources_dir(), f"{source_id}.override_approvals.jsonl",
                           what="source id")


def content_digest(data: "bytes | str") -> str:
    """The digest an override approves: sha256 of the item's captured bytes (for a row, of its
    canonical JSON — `row_digest`). An approval covers exactly that content."""
    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    return hashlib.sha256(raw).hexdigest()


def row_digest(row: dict) -> str:
    return content_digest(json.dumps(row, sort_keys=True, default=str, ensure_ascii=False))


def row_ref(source_id: str, table: str, row_id: str) -> str:
    """A table row's override id: a hash of its locator, so a raw key (an email primary key) is
    never written into the approvals log."""
    return "row:" + hashlib.sha256(f"{source_id}|{table}|{row_id}".encode("utf-8")).hexdigest()[:32]


def _record_digest(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k not in ("record_digest", "seal")}
    return content_digest(json.dumps(body, sort_keys=True, separators=(",", ":")))


# --- the approval seal (R-022 f2 #1) ---------------------------------------------------------------
#: Stored approvals are SEALED: each line carries an HMAC-SHA256 of its `record_digest` under a
#: deployment approval key that does NOT live in the sources directory, and chains to the line
#: before it (`seq`, `prev`). A line appended by anyone who can write the sources directory but
#: not read the key fails its seal; a deleted or reordered line breaks the chain, and a broken
#: chain disables EVERY approval in the log (fail closed: everything in the review band is
#: withheld again until an owner re-approves). Key custody and rotation are a founder decision
#: (R-022 founder decision 1); the default below is a per-user key file created on first use.
APPROVAL_KEY_ENV = "WALNUT_APPROVAL_KEY"
APPROVAL_KEY_FILE_ENV = "WALNUT_APPROVAL_KEY_FILE"
_MIN_KEY_CHARS = 32
_SEAL_LOCK = __import__("threading").Lock()


def approval_key_path() -> Path:
    import os

    raw = os.environ.get(APPROVAL_KEY_FILE_ENV, "").strip()
    return Path(raw) if raw else Path.home() / ".walnut" / "keys" / "approval.key"


def _approval_key(root: Optional[Path]) -> bytes:
    """The deployment approval key. `WALNUT_APPROVAL_KEY` (>= 32 chars) wins; else the key file
    (`WALNUT_APPROVAL_KEY_FILE`, default `~/.walnut/keys/approval.key`). It is the ONE core rule
    (`databridge_core.decision_ledger.approval_key`): READ ONLY, never created implicitly (R-029
    TIDY2: a read never writes). A missing key refuses (`OverrideRefused`): a write is refused
    explicitly, and a read verifies nothing. A key file INSIDE the sources directory is refused:
    whoever can append an approval line could then also read the key that seals it."""
    from databridge_core import decision_ledger as _dl

    sources = (root or paths.sources_dir()).resolve()
    try:
        return _dl.approval_key(protect=[sources])
    except _dl.DecisionRefused as exc:
        raise OverrideRefused(exc.message) from None


def _seal(key: bytes, record_digest: str) -> str:
    import hmac

    return hmac.new(key, record_digest.encode("ascii"), hashlib.sha256).hexdigest()


def _seal_ok(key: bytes, rec: dict) -> bool:
    import hmac

    seal = rec.get("seal")
    return isinstance(seal, str) and hmac.compare_digest(seal, _seal(key, str(
        rec.get("record_digest") or "")))


def _append_override(source_id: str, action: str, item_id: str, digest: str, principal,
                     reason: str, root: Optional[Path]) -> dict:
    from .plans import ApprovalRefused, require_authenticated_approver   # lazy: no import cycle

    try:
        who = require_authenticated_approver(principal, what=f"screen override ({action})")
    except ApprovalRefused as exc:
        raise OverrideRefused(str(exc)) from None
    if not isinstance(item_id, str) or not item_id.strip():
        raise OverrideRefused("an override names the item (or row ref) it approves")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise OverrideRefused("an override names the sha256 digest of the content it approves")
    if not isinstance(reason, str) or not reason.strip():
        raise OverrideRefused("an override says why (reason)")
    key = _approval_key(root)
    p = override_log_path(source_id, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _SEAL_LOCK:
        chain = _read_chain(p, key, source_id)
        if chain.broken:
            raise OverrideRefused(f"the approvals log {p.name} fails verification "
                                  f"({chain.broken}); it is not extended — an operator inspects it")
        rec = {"action": action, "source_id": source_id, "item_id": item_id.strip(),
               "digest": digest, "reason": reason.strip(),
               "at": dt.datetime.now(dt.timezone.utc).isoformat(), **who,
               "seq": chain.seq + 1, "prev": chain.head}
        rec["record_digest"] = _record_digest(rec)
        rec["seal"] = _seal(key, rec["record_digest"])
        with open(p, "a", encoding="utf-8") as fh:              # append-only: never rewritten
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


def approve_override(source_id: str, item_id: str, *, digest: str, principal, reason: str,
                     root: Optional[Path] = None) -> dict:
    """An authenticated owner lifts ONE item (or row) with ONE content digest out of the review
    band. Raises `OverrideRefused` for anything but an authenticated principal."""
    return _append_override(source_id, "approve", item_id, digest, principal, reason, root)


def revoke_override(source_id: str, item_id: str, *, digest: str, principal, reason: str,
                    root: Optional[Path] = None) -> dict:
    """Withdraw an approval — a new line; the approval stays in the log."""
    return _append_override(source_id, "revoke", item_id, digest, principal, reason, root)


class OverrideSet(set):
    """The ids holding a CURRENT override approval (a `set`, so `item_id in overrides` keeps
    working), plus the content digests each approval covers. `allows(item_id, digest)` is the
    exact check — the approval covers THIS content."""

    def __init__(self, digests: Optional[dict] = None, *, rejected: int = 0,
                 ignored_legacy: int = 0, broken: str = "") -> None:
        self.digests: dict[str, set[str]] = {k: set(v) for k, v in (digests or {}).items() if v}
        super().__init__(self.digests)
        self.rejected = rejected
        self.ignored_legacy = ignored_legacy
        self.broken = broken          # why the log failed verification ("" when it verified)

    def allows(self, item_id: str, digest: Optional[str]) -> bool:
        return bool(digest) and digest in self.digests.get(item_id, ())


def _override_lines(p: Path) -> Iterable[dict]:
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            rec = None
        yield rec if isinstance(rec, dict) else {}


@dataclass
class _Chain:
    seq: int = 0
    head: str = ""
    broken: str = ""
    rejected: int = 0
    records: list = field(default_factory=list)


def _well_formed(rec: dict, source_id: str) -> bool:
    return (rec.get("action") in ("approve", "revoke")
            and rec.get("source_id") == source_id
            and bool(str(rec.get("principal_id") or "").strip())
            and bool(str(rec.get("authenticated_by") or "").strip())
            and isinstance(rec.get("item_id"), str) and bool(rec["item_id"])
            and isinstance(rec.get("digest"), str)
            and _SHA256.fullmatch(rec["digest"]) is not None)


def _read_chain(p: Path, key: Optional[bytes], source_id: str) -> _Chain:
    """Walk the sealed chain. An UNSEALED line (no `seal`, no `seq` — the pre-f2 format, or junk)
    is never honoured and counted in `rejected`; it does not extend the chain. A line that claims
    a place in the chain (`seal` or `seq`) must verify — digest, seal, `seq` = previous + 1,
    `prev` = previous digest — or the chain is BROKEN at it and nothing in it is honoured."""
    chain = _Chain()
    if not p.is_file():
        return chain
    for n, rec in enumerate(_override_lines(p), start=1):
        if "seal" not in rec and "seq" not in rec:
            chain.rejected += 1
            continue
        if key is None:
            chain.broken = "no approval key to verify the seals"
            return chain
        if rec.get("record_digest") != _record_digest(rec) or not _seal_ok(key, rec):
            chain.broken = f"line {n}: seal or digest does not verify"
            return chain
        if rec.get("seq") != chain.seq + 1 or rec.get("prev") != chain.head:
            chain.broken = f"line {n}: chain gap (a line was removed, reordered or inserted)"
            return chain
        if not _well_formed(rec, source_id):
            chain.broken = f"line {n}: a sealed record is malformed"
            return chain
        chain.seq, chain.head = rec["seq"], rec["record_digest"]
        chain.records.append(rec)
    return chain


def load_overrides(source_id: str, root: Optional[Path] = None) -> OverrideSet:
    """Items (and rows) an AUTHENTICATED owner approved out of the review band (never out of a
    block), read from the append-only approvals log. A line that does not verify (edited,
    truncated, no principal, no digest) is counted in `rejected`, never honoured. The legacy
    `<source>.overrides.yaml` (a free-form `by:` name) is not an approval: its entries are counted
    in `ignored_legacy` and otherwise ignored."""
    base = root or paths.sources_dir()
    legacy = 0
    lp = base / f"{source_id}.overrides.yaml"
    if lp.is_file():
        try:
            data = yaml.safe_load(lp.read_text(encoding="utf-8")) or {}
            legacy = len(data.get("allow") or []) if isinstance(data, dict) else 1
        except yaml.YAMLError:
            legacy = 1
    p = override_log_path(source_id, root)
    digests: dict[str, set[str]] = {}
    if not p.is_file():
        return OverrideSet(digests, ignored_legacy=legacy)
    try:
        key: Optional[bytes] = _approval_key(root)
    except (OverrideRefused, OSError):
        key = None
    chain = _read_chain(p, key, source_id)
    if chain.broken:
        # R-022 f2 #1: a log that fails verification honours NOTHING (fail closed)
        return OverrideSet({}, rejected=chain.rejected + len(chain.records) + 1,
                           ignored_legacy=legacy, broken=chain.broken)
    for rec in chain.records:
        got = digests.setdefault(rec["item_id"], set())
        (got.add if rec["action"] == "approve" else got.discard)(rec["digest"])
    return OverrideSet(digests, rejected=chain.rejected, ignored_legacy=legacy)


__all__ = ["ScreenConfig", "ScreenResult", "ScreenError", "Rule", "load_screen", "parse_screen",
           "screen_text", "apply_masks", "luhn_ok", "mod97_ok", "load_overrides",
           "approve_override", "revoke_override", "OverrideSet", "OverrideRefused",
           "override_log_path", "content_digest", "row_digest", "row_ref", "mandatory_rules",
           "approval_key_path", "APPROVAL_KEY_ENV", "APPROVAL_KEY_FILE_ENV",
           "mandatory_quarantine_rules", "detection_text", "detection_variants", "screen_policy", "pack_screen_file",
           "is_engine_default", "screen_pack_of", "DEFAULT_POLICY_NOTE", "QUARANTINE_REVERSE"]
