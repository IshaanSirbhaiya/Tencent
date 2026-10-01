"""`MailboxCore` — the second real Core (`ADAPTER_AGENT_SPEC` §8). **Deterministic. No LLM.**

§11 says the design is real when the eight acceptance tests pass "for two adapters — one
relational source, one mailbox". `PostgresCore` is the relational one. This is the mailbox, and it
is the one where the trust model is genuinely tested, because this is the source that carries the
attacker's text.

## The four things §8 is emphatic about, and where each is enforced here

**1. Capture BEFORE parsing.** `_load` puts the raw RFC5322 bytes into the Capture Store and then
parses *the bytes the store handed back*, re-digested on the way out. The pointer carries
`capture.qualified_digest`. A mailbox pointer that cites only a `Message-ID` cites a mutable
mailbox: the owner can edit, move or delete the message, MIME can be re-encoded in transit, and
"which bytes were parsed?" becomes unanswerable exactly when an auditor asks it.

**2. The tier is what the estate declares, not what a mailbox usually is.** §8's sketch says T1,
and both estates that ship a mailbox in this repo say otherwise, in the same words:

> "As shipped this is an unversioned directory drop with a static `listing.csv` and a static
> `cursor.json`. That is T3, and an adapter built against it is testing T3 behaviour whatever the
> header claims."

Both carry a `live_contract` declaring `pinning_tier: T1` with a monotonic history cursor — and
both stamp it `status: NOT_EXERCISED_BY_THIS_FIXTURE` with a list of what would have to exist
first (a mock history endpoint, cursor advancement across three pages, expiry mid-read, duplicate
delivery, a late arrival, deletion ambiguity, a resync seam). So this Core reads
`pinning_tier` from the contract, refuses `as_of` for every tier below T0 — which, here, is every
tier this source can declare — and its refusal *names the live contract and its status*, because a
refusal that does not say what would change it gets worked around.

**3. Everything returned is `untrusted`, stamped from the contract.** Both estates declare
`untrusted_fields: ["*"]`. That label is never inferred from content: letting the thing being
contained decide whether it is contained is the attack, not a shortcut. The Core additionally
*refuses to construct* against a mailbox contract that does not declare its bodies attacker-
writable (`_check_contract_declares_untrusted`) — a contract-time assertion, not a content-time
inference.

**4. Redaction, normalisation and joining emit `derived` pointers.** §4a: if the bytes emitted
differ from the bytes cited, the pointer kind is `derived` or the value is ungrounded, and there
is no third option. Three derivations are registered here and every one of them is replayed by
`assert_grounded` at emission — the address normalisation the finance estate's own operator note
specifies, the join over a multipart body, and a digest for a part that is not text.

## What an `email_part` pointer addresses, and why the decoded payload is verbatim

`part` is a small closed grammar, parsed by `extract_part` — which is the SAME function emission
and `verify()` both call, so the two cannot drift:

| `part` | addresses |
|---|---|
| `header:<lowercased-name>` | one header, exactly as written |
| `body` / `body:<i>` | the i-th `text/*` part that is not an attachment, transfer-decoded |
| `body#<start>:<end>` | a character range of `body` |
| `attachment:<j>` | the j-th attachment part, transfer-decoded |

A MIME part's *content* is what remains after its `Content-Transfer-Encoding` is undone —
quoted-printable soft breaks and base64 are properties of the envelope, not of the message — so a
transfer-decoded payload is the verbatim content of the part and keeps the `email_part` pointer.
Anything beyond that (unicode normalisation, zero-width stripping, HTML-to-text) is a
transformation and would take a `derived` pointer. **This Core performs none of them**, which is
why `INJ-14`'s zero-width-obfuscated payload comes back with its zero-width joiners intact: an
adapter that silently normalised them would be destroying the evidence that the message was
obfuscated at all.

## What the field set is, and why there is no per-source branch in it

Hard constraint 3 says no per-issuer branching, and a mailbox adapter is where that rule is
easiest to break — the two estates name their exposed fields differently (`claimed_from_address`
vs `from_address`, `claimed_facility_reference` vs `claimed_case_ref`). So the field set is
**RFC5322 universals plus whatever the contract's `trusted_binding` declares**, and the contract
may *rename* a universal through `exposed_as`. Adding a mailbox is a YAML change:

* finance declares `From -> claimed_from_address`, `To -> claimed_to_addresses`,
  `In-Reply-To/References -> claimed_thread_refs` and a body pattern
  `"Facility reference: CB-F-......" -> claimed_facility_reference`;
* government declares `X-Case-Reference -> claimed_case_ref` and leaves the universals alone.

Both then match their own `roles.yaml` column classifications exactly, which is the check that the
naming was read out of the contract rather than guessed.

**Every one of those fields is a CLAIM.** Both estates say so at length and for the same reason:
there is no envelope identity anywhere in either fixture, `From`/`To`/`X-Case-Reference` are
attacker-set headers, and scoping a mailbox on them "would let anyone who can send mail choose
whose portfolio a message lands in". So this Core exposes them as untrusted, cited values and
nothing else — there is no path from a header to a scope, and `roles.yaml`'s mailbox entry carries
no row filter for exactly that reason.
"""
from __future__ import annotations

import email
import email.parser
import email.utils
import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence, Union

from databridge_core import (
    Absent,
    GroundedValue,
    SnapshotIdentity,
    SourcePointer,
    Trust,
    Withheld,
)

from ..canonical import canonical_json, canonical_scalar, sha256_of
from ..capture import (
    Capture,
    CaptureCorrupt,
    CaptureNotFound,
    CaptureShredded,
    CaptureStore,
)
from ..contract import CapabilityRequest, Projection
from ..describe import SourceDescription
from ..errors import (
    AdapterError,
    CannotPinPast,
    CapabilityNotAdvertised,
    PolicyNotApplied,
    UnorderedPointeredRead,
)
from ..grounding import (
    DerivationError,
    assert_derived_from,
    assert_grounded,
    parse_derivation,
    register_derivation,
    replay_derivation,
)
from ..policy import Admission, PolicyEngine
from ..results import AdapterResult, Budget, Cell, Cost, GroundedRows, ReadPlan
from ..verdicts import Verdict, assert_verdict_advertised, combine_verdicts
from .contract import SourceContract, load_source_contract

__all__ = [
    "CAPABILITIES",
    "MailboxCore",
    "MailboxContractError",
    "MailboxField",
    "cell_digest",
    "extract_part",
    "normalise_addresses",
    "rows_digest",
]

#: What this Core can be asked for. A closed set, advertised in `describe()` and screened by
#: Policy before anything runs.
#:
#: These are READ capabilities, and they are deliberately not the same axis as the estate's own
#: `capabilities: [describe, plan, resolve, verify_against_capture]`, which lists the VERBS the
#: source supports. That list is still load-bearing here and is checked at construction: it names
#: `verify_against_capture` rather than `verify`, and this Core honours the distinction by
#: resolving every pointer against the Capture Store and never returning `VERIFIED` — the estate
#: does not list `VERIFIED` as reachable, and says why: "a live re-read of a mailbox is not a
#: re-read of the same artefact: MIME re-encoding, transfer-encoding rewrites and provider-side
#: normalisation change the bytes without changing the message. Cite the capture."
CAPABILITIES: frozenset[str] = frozenset({"structural_read", "message_read", "attachment_read"})

#: The entity a mailbox exposes. One row per message; `message_id` is the stable row identity.
ENTITY_KEY: tuple[str, ...] = ("message_id",)


class MailboxContractError(AdapterError):
    """The source contract cannot be turned into a field set, or contradicts what a mailbox is.

    Raised at CONSTRUCTION, never at read time. A mailbox whose contract does not declare its
    bodies attacker-writable, or whose `trusted_binding` names a claimed field this Core cannot
    locate, is a misconfiguration — and discovering it on the first read means discovering it
    after a pointer has already been minted with the wrong trust label.
    """


class MessageNotReadable(AdapterError):
    """A message file in the declared drop cannot be read. Fails closed, always (§10).

    §10's first row: no partial graph write. A mailbox read that silently skipped the messages it
    could not open would return a result that looks complete, which is the only outcome worse than
    an error.
    """


# ---------------------------------------------------------------------------------------
# registered derivations (§4a) — every transformation this Core can perform, replayable
# ---------------------------------------------------------------------------------------
#
# `assert_grounded` parses the `derivation` string, looks the rule up here, replays it over the
# cited parent values and requires the result to equal the emitted value exactly. A derivation
# nobody can replay is a claim, and a `derived` pointer exists precisely not to be one.

#: Separator tokens for `join_text_parts`. Named rather than literal because the derivation
#: grammar splits arguments on `,` and `=`, so a literal separator containing either would not
#: survive a round trip through the canonical form — and a derivation that cannot be re-parsed
#: cannot be replayed.
_JOIN_SEPARATORS: Mapping[str, str] = {"lf": "\n", "lf2": "\n\n", "sp": " "}


def normalise_addresses(header_text: Any) -> str:
    """RFC5322 address normalisation, exactly as the finance estate's operator note specifies.

    > "Address comparison is exact match after RFC5322 normalisation (case-fold the domain, strip
    > display name and comments, do NOT case-fold the local part). Substring matching on an
    > address field matches `rm.007@bank.example.attacker.example`."

    The local part is left alone because it is case-sensitive per RFC5321 §2.3.11, and folding it
    would merge two mailboxes a server considers distinct. The domain is folded because it is not.

    Returns `""` when nothing in the header parses as an address; the caller then emits the raw
    header verbatim rather than an empty string, because "we could not parse it" and "it said
    nothing" are different facts.
    """
    text = "" if header_text is None else str(header_text)
    out: list[str] = []
    for _display, addr in email.utils.getaddresses([text]):
        if "@" not in addr:
            # `getaddresses` splits an unquoted display name containing a comma
            # (`CORTESI, Jarrah <treasury@...>`) into a bogus first entry. It is not an address
            # and must not be emitted as one.
            continue
        local, _, domain = addr.rpartition("@")
        out.append(f"{local}@{domain.lower()}" if domain else addr)
    return ", ".join(out)


def _rfc5322_addresses_v1(parents: Sequence[Any], _args: Mapping[str, str]) -> Any:
    if len(parents) != 1:
        raise DerivationError(
            f"rfc5322_addresses@v1 normalises exactly one header, got {len(parents)} parent(s)")
    return normalise_addresses(parents[0])


register_derivation(
    "rfc5322_addresses", 1, _rfc5322_addresses_v1,
    doc="Strip display names and comments from an RFC5322 address header, case-fold the domain, "
        "leave the local part alone, and join the results with ', '. The estate's declared "
        "comparison form; substring matching on an address field is forbidden by the same note.")


def _join_text_parts_v1(parents: Sequence[Any], args: Mapping[str, str]) -> Any:
    token = args.get("sep", "lf2")
    sep = _JOIN_SEPARATORS.get(token)
    if sep is None:
        raise DerivationError(
            f"join_text_parts@v1: separator token {token!r} is not registered; known: "
            f"{sorted(_JOIN_SEPARATORS)}. A separator spelled literally could not survive the "
            f"derivation grammar, so it is named")
    if not parents:
        raise DerivationError("join_text_parts@v1 needs at least one cited part")
    return sep.join("" if p is None else str(p) for p in parents)


register_derivation(
    "join_text_parts", 1, _join_text_parts_v1,
    doc="Concatenate the transfer-decoded text of the cited MIME parts, in MIME order. Used when "
        "a message carries more than one text part; each part keeps its own resolvable pointer.")


def _binary_part_digest_v1(parents: Sequence[Any], args: Mapping[str, str]) -> Any:
    algo = args.get("algo", "sha256")
    if algo != "sha256":
        raise DerivationError(
            f"binary_part_digest@v1 computes sha256, not {algo!r}: changing the algorithm is a new "
            f"version, or every stored citation replays under a rule that did not produce it")
    if len(parents) != 1:
        raise DerivationError(
            f"binary_part_digest@v1 digests exactly one part, got {len(parents)} parent(s)")
    raw = parents[0]
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return "sha256:" + hashlib.sha256(bytes(raw)).hexdigest()


register_derivation(
    "binary_part_digest", 1, _binary_part_digest_v1,
    doc="Content address of a MIME part whose bytes are not text in its declared charset. The "
        "bytes stay in the capture and stay citable; what is EMITTED is their digest, because a "
        "lossy text rendering of them would be a fabricated value with a real pointer.")


# ---------------------------------------------------------------------------------------
# digests
# ---------------------------------------------------------------------------------------


def _token(value: Any) -> Mapping[str, str]:
    """One value, canonicalised, with a typed fallback for the bytes a MIME part can hold.

    The fallback digests rather than `str()`-ing, for the reason `PostgresCore` gives: a digest
    over `str(memoryview)` would make two different byte strings look identical, and a content
    hash that collides is a content hash that certifies drift did not happen.
    """
    try:
        return canonical_scalar(value)
    except (TypeError, ValueError):
        if isinstance(value, (bytes, bytearray, memoryview)):
            return {"t": "bytes", "v": sha256_of(bytes(value).hex())}
        return {"t": f"opaque:{type(value).__name__}", "v": sha256_of(repr(value))}


def cell_digest(value: Any) -> str:
    """The content address of ONE cited artefact — one header, one part, one span.

    What `verify()` compares. Per-cell rather than per-read, which is correction 1 in
    `ADAPTER_AGENT_SPEC` §7a: a read-level digest on every pointer makes any change anywhere in
    the read report as material drift on every pointer from it, and the lattice collapses.
    """
    return sha256_of(canonical_json(_token(value)))


def row_digest(row: Mapping[str, Any], fields: Sequence[str], key: Sequence[str]) -> str:
    """One message, over the READ PROJECTION ONLY, keyed by its `Message-ID`."""
    return sha256_of(canonical_json({
        "key": [_token(row.get(k)) for k in key],
        "cells": {f: _token(row.get(f)) for f in fields},
    }))


def rows_digest(rows: Sequence[Mapping[str, Any]], fields: Sequence[str],
                key: Sequence[str]) -> str:
    """**Order-independent** digest of what was actually read (§5, rule 2).

    Sorted per-message digests, so a different directory-walk order, a filesystem that returns
    entries in inode order, or a different pagination boundary does not read as data drift.
    """
    return sha256_of(canonical_json(sorted(row_digest(r, fields, key) for r in rows)))


def projection_schema_hash(entity: str, fields: Sequence[str], key: Sequence[str],
                           declared: Mapping[str, str]) -> str:
    """`schema_hash` over the **read projection only** (spine D6).

    A mailbox has no reflectable schema, so "the schema" is the field set this Core derives from
    the contract plus RFC5322. Hashing it still matters: if the contract renames a claimed field
    or adds one, the projection means something different and every pointer into it should say so.
    """
    return sha256_of(canonical_json({
        "entity": entity,
        "key": list(key),
        "fields": [{"name": f, "type": declared.get(f, "text")} for f in fields],
    }))


# ---------------------------------------------------------------------------------------
# the field set — RFC5322 universals, renamed and extended by the contract
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MailboxField:
    """One readable field of the `message` entity, and how to locate it in a parsed message.

    ``derivation`` is the registered rule applied to the located raw value on the way out. `None`
    means the value is emitted verbatim under the `email_part` pointer that located it.
    """

    name: str
    kind: str                                    # "header" | "body" | "attachment" | "body_span"
    headers: tuple[str, ...] = ()
    derivation: Optional[str] = None
    pack_type: str = "text"
    #: For ``kind="body_span"``: the compiled form of the contract's declared body pattern.
    pattern: Optional["re.Pattern[str]"] = None
    #: The contract text this field was derived from, for `describe()` and for error messages.
    declared_as: str = ""


#: The RFC5322 universals, in the order `describe()` reports them. A mailbox is a mailbox
#: everywhere; only the NAMES an estate exposes them under are contract-declared.
UNIVERSAL_FIELDS: tuple[MailboxField, ...] = (
    MailboxField("message_id", "header", ("Message-ID",), pack_type="varchar",
                 declared_as="RFC5322 Message-ID"),
    # The `Date` header VERBATIM, not an instant. Parsing it would be a derivation, and a
    # malformed date in one message would then refuse the whole read — which is a worse failure
    # than handing back what the message actually says and letting a caller parse it under its own
    # error handling.
    MailboxField("sent_at", "header", ("Date",), declared_as="RFC5322 Date"),
    MailboxField("subject", "header", ("Subject",), declared_as="RFC5322 Subject"),
    MailboxField("from_address", "header", ("From",), derivation="rfc5322_addresses@v1()",
                 declared_as="RFC5322 From"),
    MailboxField("to_address", "header", ("To",), derivation="rfc5322_addresses@v1()",
                 declared_as="RFC5322 To"),
    MailboxField("thread_refs", "header", ("In-Reply-To", "References"),
                 declared_as="RFC5322 In-Reply-To/References"),
    MailboxField("body", "body", declared_as="the message's text/* parts"),
    MailboxField("attachment", "attachment", declared_as="the message's attachment parts"),
)

#: A header name, possibly written as an alternation (`In-Reply-To/References`).
_HEADER_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]*(/[A-Za-z][A-Za-z0-9-]*)*$")


def _compile_body_pattern(declared: str) -> "re.Pattern[str]":
    """Turn a contract's declared body-pattern literal into an anchored search pattern.

    The finance estate declares its body-borne claimed field as the literal
    ``"Facility reference: CB-F-......"``. That is prose with a shape in it, so the reading is
    stated here rather than guessed at each call site: everything up to and including the first
    ``": "`` is a LITERAL PREFIX, and in the remainder a ``.`` stands for one non-space character
    and every other character stands for itself.

    This is narrower than a regex on purpose. A contract that could declare an arbitrary pattern
    would be a contract that can declare catastrophic backtracking, and the value it produces goes
    into a pointer.
    """
    prefix, sep, shape = declared.partition(": ")
    if not sep or not shape.strip():
        raise MailboxContractError(
            f"claimed field {declared!r} is neither a header name nor a '<prefix>: <shape>' body "
            f"pattern. This Core will not invent a locator for a field the contract describes "
            f"only in prose — the value would land in a pointer, and a pointer that addresses "
            f"something nobody specified is worse than a missing field")
    body = "".join(r"\S" if ch == "." else re.escape(ch) for ch in shape.strip())
    return re.compile(re.escape(prefix + sep) + f"({body})")


def _claimed_field_entries(contract: SourceContract) -> tuple[Mapping[str, Any], ...]:
    """The contract's `trusted_binding` declarations, in either shape the estates use.

    Finance writes a `claimed_fields:` LIST; government writes ONE binding inline. Both are read,
    because a contract that changed shape must not silently stop declaring anything — "no claimed
    fields" and "we are reading the wrong key" look identical from here and only one is safe.
    """
    binding = contract.entry.get("trusted_binding") or {}
    if not isinstance(binding, Mapping):
        raise MailboxContractError(
            f"{contract.source_id}: `trusted_binding` must be a mapping, got "
            f"{type(binding).__name__}")
    declared = binding.get("claimed_fields")
    if isinstance(declared, (list, tuple)):
        return tuple(dict(e) for e in declared if isinstance(e, Mapping))
    if binding.get("exposed_as"):
        return (dict(binding),)
    return ()


def field_set(contract: SourceContract) -> tuple[MailboxField, ...]:
    """The `message` entity's fields: RFC5322 universals, renamed and extended by the contract.

    A rename rather than an addition where the contract's declared header matches a universal's,
    so `From` cannot be exposed twice under two names — two cells citing one header, differing
    only in what a `roles.yaml` classification says about them, is the shape of a policy hole.
    """
    fields: list[MailboxField] = list(UNIVERSAL_FIELDS)
    by_headers = {tuple(h.lower() for h in f.headers): i
                  for i, f in enumerate(fields) if f.kind == "header"}

    for entry in _claimed_field_entries(contract):
        exposed = str(entry.get("exposed_as") or "").strip()
        declared = str(entry.get("derived_from_header") or entry.get("field") or "").strip()
        if not exposed:
            raise MailboxContractError(
                f"{contract.source_id}: a `trusted_binding` claimed field declares no "
                f"`exposed_as`, so this Core cannot know what to call it. Declared: {declared!r}")
        if not declared:
            raise MailboxContractError(
                f"{contract.source_id}: claimed field {exposed!r} declares neither `field` nor "
                f"`derived_from_header`, so there is nothing to locate it by")

        if _HEADER_NAME.match(declared):
            headers = tuple(part for part in declared.split("/") if part)
            key = tuple(h.lower() for h in headers)
            existing = by_headers.get(key)
            if existing is not None:
                # A RENAME of a universal. The locator and the derivation are unchanged; only the
                # name the estate's policy classification refers to moves.
                fields[existing] = MailboxField(
                    name=exposed, kind="header", headers=fields[existing].headers,
                    derivation=fields[existing].derivation,
                    pack_type=fields[existing].pack_type, declared_as=declared)
                by_headers[key] = existing
                continue
            fields.append(MailboxField(name=exposed, kind="header", headers=headers,
                                       declared_as=declared))
            by_headers[key] = len(fields) - 1
            continue

        fields.append(MailboxField(name=exposed, kind="body_span",
                                   pattern=_compile_body_pattern(declared),
                                   declared_as=declared))

    names = [f.name for f in fields]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise MailboxContractError(
            f"{contract.source_id}: the contract exposes {dupes} twice. Two cells citing one "
            f"location under two names would be classified twice by `roles.yaml` and could "
            f"disagree, which is a policy hole with a citation on it")
    return tuple(fields)


# ---------------------------------------------------------------------------------------
# the `part` grammar — ONE parser, used at emission and at verification
# ---------------------------------------------------------------------------------------

_SPAN_PART = re.compile(r"^body#(\d+):(\d+)$")
_BODY_PART = re.compile(r"^body(?::(\d+))?$")
_ATTACHMENT_PART = re.compile(r"^attachment:(\d+)$")
_HEADER_PART = re.compile(r"^header:(.+)$")


def _is_attachment(part: Message) -> bool:
    if (part.get_content_disposition() or "").lower() == "attachment":
        return True
    return part.get_content_maintype() != "text"


def _leaf_parts(msg: Message) -> list[Message]:
    return [p for p in msg.walk() if not p.is_multipart()]


def text_parts(msg: Message) -> list[Message]:
    """The message's readable text parts, in MIME order. Alternatives included, deliberately.

    A `multipart/alternative` HTML branch is not a duplicate to be discarded: `INJ-04` in both
    estates lives *only* there — finance as white-on-white 1px text, government as an HTML comment
    — and an adapter that took the plain branch and dropped the rest would report containment it
    never performed, because it never read the payload.
    """
    return [p for p in _leaf_parts(msg) if not _is_attachment(p)]


def attachment_parts(msg: Message) -> list[Message]:
    return [p for p in _leaf_parts(msg) if _is_attachment(p)]


#: Charsets whose decoded text recovers exactly the bytes it was decoded from on re-encoding, so
#: decoding one of them is not a transformation of the cited bytes — it is what "the content" of a
#: text/* part named those bytes always was. Any other declared charset is a genuine
#: representation change (review finding 4, §4a): decoding ISO-8859-1 bytes into a Python `str`
#: produces a value that does not equal the bytes on disk, and a value that differs from its cited
#: bytes cannot keep a verbatim pointer.
_VERBATIM_CHARSETS = frozenset({"utf-8", "utf8", "us-ascii", "ascii", "ansi_x3.4-1968"})


def _decode(part: Message) -> Union[str, bytes, None]:
    """The part's content: transfer-decoded, then charset-decoded ONLY under a charset whose
    round trip is lossless and unambiguous (UTF-8 / US-ASCII).

    Returns `bytes` when the declared charset does not decode the payload, OR when it decodes
    under a charset outside `_VERBATIM_CHARSETS`. Neither is a failure path papered over with
    `errors="replace"`: a replacement character is a byte the message never contained, and a
    string decoded under a non-UTF-8 charset is a transformed representation of the captured
    bytes, not a verbatim reading of them. `_emit_cell`'s structural dispatch already routes
    `bytes` content through the registered, replayed `binary_part_digest@v1` derivation — the
    same path a part whose charset cannot decode at all already takes, so a non-UTF-8 charset part
    gets the same honest treatment rather than a silently transformed `email_part` pointer.
    """
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return raw if isinstance(raw, str) else None
    charset = (part.get_content_charset() or "utf-8").lower()
    if charset not in _VERBATIM_CHARSETS:
        return bytes(payload)
    try:
        return bytes(payload).decode(charset)
    except (UnicodeDecodeError, LookupError):
        return bytes(payload)


def extract_part(msg: Message, part: str) -> Union[str, bytes, None]:
    """Resolve a `part` locator against a parsed message. `None` means the part is not there.

    The single source of truth for what an `email_part` pointer addresses. Emission uses it to
    produce a value; `verify()` uses it to re-produce the value from the CAPTURE. Two
    implementations would eventually disagree, and the disagreement would look like drift.
    """
    m = _HEADER_PART.match(part)
    if m:
        # `Message.get` is case-insensitive, so the lowercased name in the locator round-trips.
        return msg.get(m.group(1))

    m = _SPAN_PART.match(part)
    if m:
        start, end = int(m.group(1)), int(m.group(2))
        parts = text_parts(msg)
        if not parts:
            return None
        text = _decode(parts[0])
        if not isinstance(text, str) or end > len(text) or start > end:
            return None
        return text[start:end]

    m = _BODY_PART.match(part)
    if m:
        index = int(m.group(1) or 0)
        parts = text_parts(msg)
        return _decode(parts[index]) if index < len(parts) else None

    m = _ATTACHMENT_PART.match(part)
    if m:
        index = int(m.group(1))
        parts = attachment_parts(msg)
        return _decode(parts[index]) if index < len(parts) else None

    return None


# ---------------------------------------------------------------------------------------
# the Core
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Loaded:
    """One captured, parsed message. The capture is taken BEFORE the parse, always."""

    path: Path
    capture: Capture
    message: Message
    signature: tuple[int, int]


class MailboxCore:
    """Deterministic. No LLM. Owns the drop and the Capture Store.

    **It cannot be constructed without a `PolicyEngine`, and it obtains its own admission inside
    every verb** — the same in-process half of Appendix A's "a caller must not be able to invoke
    Core directly and skip Policy" that `PostgresCore` implements. **It cannot be constructed
    without a `CaptureStore` either** when the contract requires capture, which for a T3 mailbox is
    always: a citation into an unversioned drop that does not resolve against captured bytes cites
    nothing that can be checked.
    """

    capabilities: frozenset[str] = CAPABILITIES

    def __init__(self, *, source_id: str, policy: PolicyEngine, capture: CaptureStore,
                 root: Optional[Path] = None,
                 sources_yaml: Optional[Path] = None,
                 contract: Optional[SourceContract] = None,
                 entity: str = "message",
                 now: Optional[Callable[[], datetime]] = None,
                 pack_version: str = "",
                 register_taint: bool = True) -> None:
        if policy is None:                                       # pragma: no cover - typing
            raise PolicyNotApplied(
                "a Core cannot be constructed without a PolicyEngine: the documented path to a "
                "source has to run through Policy, and an optional policy is no policy")
        if contract is None:
            if sources_yaml is None:
                raise ValueError("MailboxCore needs either `contract` or `sources_yaml`")
            contract = load_source_contract(source_id, sources_yaml=sources_yaml)
        if contract.source_id != source_id:
            # Review finding 1. A `contract=` passed explicitly and a `source_id=` naming a
            # DIFFERENT source is not a shape `load_source_contract` can produce — it only
            # arises when a caller supplies both — but when it does, every field declaration,
            # the root path and every trust label come from the WRONG estate while captures and
            # pointers are stamped under this source_id. That is false provenance under someone
            # else's name, and it is cheap to refuse at construction rather than discover later.
            raise MailboxContractError(
                f"constructed with source_id={source_id!r} but the supplied contract is for "
                f"{contract.source_id!r}: one Core reads one contract, and stamping a different "
                f"source's fields, root and trust under this source_id would be false provenance")
        self.source_id = source_id
        self._contract = contract
        self._policy = policy
        self._entity = entity
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._pack_version = pack_version or contract.estate
        self._register_taint = register_taint

        self._fields = field_set(contract)
        self._check_contract_declares_untrusted(self._fields)
        if capture is None and self._capture_required:           # pragma: no cover - typing
            raise MailboxContractError(
                f"{source_id!r} declares capture_required and was constructed without a Capture "
                f"Store. An `email_part` pointer resolves against the capture, always, never "
                f"against the live mailbox (§5, §8)")
        self._capture = capture

        self._by_name = {f.name: f for f in self._fields}
        if ENTITY_KEY[0] not in self._by_name:                   # pragma: no cover - defensive
            raise MailboxContractError(
                f"{source_id!r} exposes no {ENTITY_KEY[0]!r} field, so no message has a stable "
                f"identity and no pointer into one would be a citation")
        self.pinning_tier: str = contract.declared_tier
        self._root = Path(root) if root is not None else self._drop_root()
        self._described: Optional[SourceDescription] = None
        self._loaded: dict[Path, _Loaded] = {}
        self._live_index: Optional[dict[str, Path]] = None
        self._live_signature: Optional[tuple[tuple[str, int, int], ...]] = None

    # --- contract reading -------------------------------------------------------------
    @property
    def _capture_required(self) -> bool:
        return bool(self._contract.entry.get("capture_required", self.pinning_tier == "T3"))

    @property
    def live_contract(self) -> Mapping[str, Any]:
        """The tier this source would have if its live transport were wired up — and its status.

        Both estates declare `live_contract: {pinning_tier: T1, ...,
        status: NOT_EXERCISED_BY_THIS_FIXTURE}` with a list of what a real T1 claim would need
        first. Surfaced rather than silently ignored, because "this is T3 today and T1 when
        somebody builds the history endpoint" is a planning fact, and hiding it is how the T1
        claim got asserted by prose and tested by nothing in the first place.
        """
        entry = self._contract.entry.get("live_contract") or {}
        return dict(entry) if isinstance(entry, Mapping) else {}

    @property
    def live_contract_exercised(self) -> bool:
        status = str(self.live_contract.get("status") or "").upper()
        return bool(self.live_contract) and "NOT_EXERCISED" not in status

    def _check_contract_declares_untrusted(self, fields: Sequence["MailboxField"]) -> None:
        """Refuse a mailbox contract that does not declare EVERY exposed field attacker-writable.

        A contract-time assertion, and emphatically NOT a content-time inference: nothing here
        looks at a byte of any message. It closes the one gap the "trust is a lookup" rule leaves
        open for this source in particular — a mailbox contract that simply forgot to declare
        `untrusted_fields` would make every body `TRUSTED` by the lookup's own default, and the
        wrongest possible label would be applied to the most hostile input in the estate.

        Checked over every field this Core will actually expose, not two fixed probes (review
        finding 2): checking only `body` and `subject` would let a contract that declared exactly
        those two untrusted — and left a renamed claimed field, `From` under `trusted_binding`
        say — sit at the lookup's TRUSTED default. Every field here addresses an attacker-writable
        envelope or body (the module docstring says so at length), so every one of them is
        checked, not a sample.
        """
        for field in fields:
            if self._contract.trust_of(self._entity, field.name) is not Trust.UNTRUSTED:
                raise MailboxContractError(
                    f"{self.source_id!r} does not declare {self._entity}.{field.name} untrusted. "
                    f"A mailbox field is attacker-writable by definition — it is the canonical "
                    f"injection vector, and `trust=untrusted` is not a judgement call here (§8). "
                    f"Declare `untrusted_fields: [\"*\"]` in the source contract; this Core will "
                    f"not stamp the label the contract failed to make")

    def _drop_root(self) -> Path:
        """Where the `.eml` files are, read from the contract's own declared address.

        Two spellings, both declared: finance names a `transport: "file://mailbox/messages/"`,
        government an `entrypoint: correspondence/INDEX.csv`. Both are resolved against the
        directory holding `sources.yaml`, so the estate says where its data is and this Core does
        not guess.
        """
        base = self._contract.path.parent
        transport = str(self._contract.entry.get("transport") or "")
        if transport.startswith("file://"):
            return base / transport[len("file://"):].rstrip("/")
        entrypoint = str(self._contract.entry.get("entrypoint") or "")
        if entrypoint:
            return base / Path(entrypoint).parent
        raise MailboxContractError(
            f"{self.source_id!r} declares neither a `file://` transport nor an `entrypoint`, so "
            f"this Core cannot find the drop. A default of 'the pack directory' would read "
            f"whatever happened to be there")

    # --- capture, then parse ----------------------------------------------------------
    def _message_paths(self) -> list[Path]:
        if not self._root.is_dir():
            raise MessageNotReadable(
                f"{self.source_id}: the declared drop {self._root} is not a directory. Failing "
                f"closed - an empty result here would read as 'the mailbox is empty'")
        return sorted(self._root.rglob("*.eml"))

    def _load(self, path: Path) -> _Loaded:
        """**Capture BEFORE parsing**, then parse the bytes the store handed back (§8).

        The re-read through `CaptureStore.get` is deliberate and it is not paranoia: the store
        re-digests on every read, so the bytes that get parsed are provably the bytes that got
        stored, and a pointer's `capture_digest` therefore names the object the value came out of
        rather than the object we intended it to come out of.

        Memoised by `(size, mtime_ns)` because a mailbox read parses every message to order them
        and a test suite reads the same drop many times. The signature is part of the key, so a
        drop that changes on disk is re-captured rather than served from a stale parse.
        """
        stat = path.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
        cached = self._loaded.get(path)
        if cached is not None and cached.signature == signature:
            return cached
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise MessageNotReadable(
                f"{self.source_id}: cannot read {path.name}: {exc}. Failing closed - a mailbox "
                f"read that skipped what it could not open would look complete") from exc
        capture = self._capture.put(
            raw, media_type="message/rfc822", retention="ledger_bound",
            source_id=self.source_id, note=f"{self.source_id}:{path.name}")
        # `compat32`, not `policy.default`: the default policy RFC2047-decodes header values on
        # the way out, which is a TRANSFORMATION. A decoded header emitted under a pointer that
        # cites the raw header is exactly the redaction idiom §4a convicts, one level down.
        message = email.message_from_bytes(self._capture.get(capture.qualified_digest))
        loaded = _Loaded(path=path, capture=capture, message=message, signature=signature)
        self._loaded[path] = loaded
        return loaded

    def _drop_signature(self) -> tuple[tuple[str, int, int], ...]:
        """A cheap fingerprint of the drop's CURRENT listing: name, size, mtime.

        What decides whether `_live_message_ids` redoes its header-only pass (review finding 12):
        the earlier version cached that pass for the Core's entire lifetime with no invalidation
        at all, so an add, remove or replace between two `verify()` calls on the SAME Core kept
        reading the membership from the first call. `stat()` on every file is far cheaper than the
        header parse it decides whether to redo, so this runs on every call rather than trusting a
        caller to remember `close()`.
        """
        return tuple(sorted((path.name, stat.st_size, stat.st_mtime_ns)
                            for path in self._message_paths()
                            for stat in (path.stat(),)))

    def _live_message_ids(self) -> Mapping[str, Path]:
        """A `Message-ID -> path` index of the drop AS IT IS NOW. Used only to detect VANISHED.

        **Headers only, and deliberately WITHOUT capturing.** Capture-before-parse is a rule about
        bytes that will be CITED, and nothing from this pass reaches a pointer, a value or a
        digest - it answers one question, "is this message still in the drop?", and throws the
        rest away. Capturing here would make `verify()` write to a WORM store as a side effect of
        being asked a read-only question, which is both surprising and wrong: an operator checking
        an old citation would silently create new evidence, and a capture store that gained
        objects during verification could never be reconciled against what was actually read.
        """
        signature = self._drop_signature()
        if self._live_index is not None and self._live_signature == signature:
            return self._live_index
        index: dict[str, Path] = {}
        for path in self._message_paths():
            try:
                with path.open("rb") as handle:
                    headers = email.parser.BytesParser().parse(handle, headersonly=True)
            except OSError as exc:                               # pragma: no cover - defensive
                raise MessageNotReadable(
                    f"{self.source_id}: cannot read {path.name}: {exc}") from exc
            mid = headers.get("Message-ID")
            if mid:
                index[str(mid)] = path
        self._live_index = index
        self._live_signature = signature
        return index

    # --- verb 1: describe -------------------------------------------------------------
    def describe(self) -> SourceDescription:
        """Schema, capability matrix, retention reality, egress class. **No data.**"""
        if self._described is not None:
            return self._described
        columns = [{"name": f.name, "type": f.pack_type, "nullable": True} for f in self._fields]
        entity = self._contract.entity_description(self._entity, columns=columns, key=ENTITY_KEY)
        self._described = self._contract.describe(capabilities=self.capabilities,
                                                  entities=(entity,))
        return self._described

    # --- verb 2: snapshot -------------------------------------------------------------
    def snapshot(self, req: CapabilityRequest) -> SnapshotIdentity:
        """Pin a point in time. Refuses the past; see `_check_as_of`."""
        admission = self._admit(req)
        self._check_as_of(req)
        snap, _rows = self._read(req, admission)
        return snap

    # --- verb 3: plan -----------------------------------------------------------------
    def plan(self, req: CapabilityRequest) -> ReadPlan:
        """The exact operations, and what the read honestly cannot promise. No message is opened."""
        admission = self._admit(req)
        self._require_total_order(req)
        operations = [
            f"scan {self._root.name}/**/*.eml declared by the source contract",
            "capture each message to the Capture Store (message/rfc822, ledger_bound) BEFORE "
            "parsing it",
            f"parse RFC5322 and project {', '.join(req.projection.fields)}",
            f"order by {', '.join(req.order_by) or ENTITY_KEY[0]}, limit {req.limit}",
        ]
        if req.resolve:
            operations.append(
                f"resolution mode: restrict to the {len(req.resolve)} cited message id(s)")
        for field in (self._by_name[f] for f in req.projection.fields if f in self._by_name):
            if field.derivation:
                operations.append(
                    f"-- {field.name} is emitted under a `derived` pointer: {field.derivation}")
        withheld = [c.column for c in admission.decisions.columns if c.withheld]
        if withheld:
            operations.append(f"-- withheld by policy, not emitted: {', '.join(withheld)}")

        estimated = min(len(self._message_paths()), req.limit)
        return ReadPlan(
            read_hash=req.read_hash, source_id=self.source_id, entity=req.projection.entity,
            operations=tuple(operations), estimated_rows=estimated,
            estimated_cost=Cost(rows=estimated),
            requires_capture=self._capture_required,
            completeness_caveat=self._completeness_caveat())

    def _completeness_caveat(self) -> Optional[str]:
        """What this drop's own cursor cannot promise — declared, never inferred.

        The finance mailbox ships `cursor: {kind: none}` with a `live_contract` cursor beside it
        that is explicitly not exercised. Reporting the LIVE cursor's `lossy: false` here would be
        the exact overclaim the estate corrected: "the T1 claim was asserted by prose and tested by
        nothing".
        """
        cursor = dict(self._contract.entry.get("cursor") or {})
        kind = str(cursor.get("kind") or "none")
        live = self.live_contract
        if kind == "none":
            caveat = (f"{self.source_id} ships no change cursor at all, so an incremental read is "
                      f"not expressible and a full read cannot be shown to have missed nothing; "
                      f"deletes leave no tombstone")
            if live and not self.live_contract_exercised:
                caveat += (f". A live contract declaring {live.get('pinning_tier')} is on file "
                           f"with status {live.get('status')} - it is not what this adapter is "
                           f"reading")
            return caveat
        if cursor.get("lossy"):
            return (f"cursor {kind!r} is declared lossy: it may PRIORITISE a re-read and may "
                    f"never PROVE completeness")
        return None

    # --- verb 4: resolve --------------------------------------------------------------
    def resolve(self, req: CapabilityRequest) -> AdapterResult:
        """Execute. Returns `GroundedRows`: every cell carries a `SourcePointer`."""
        started = time.perf_counter()
        admission = self._admit(req).check()
        self._check_as_of(req)
        snap, rows = self._read(req, admission)

        cells: list[tuple[Cell, ...]] = []
        withheld_rows = 0
        for ordinal, (loaded, raw) in enumerate(rows):
            denial = admission.decisions.row_verdict(
                raw, attributes=admission.attribute_map, subquery_sets={})
            if denial is not None:                               # pragma: no cover - see below
                # No role in either estate scopes a mailbox by row, and both say why at length:
                # there is no envelope identity in the fixture, so any row rule would have to key
                # on a forgeable header. The branch stays because the compiled table is what
                # decides, not this Core.
                withheld_rows += 1
                continue
            cells.append(self._emit_row(req, loaded, raw, ordinal, snap, admission))

        warnings = list(admission.notes)
        caveat = self._completeness_caveat()
        if caveat:
            warnings.append(caveat)
        if withheld_rows:                                        # pragma: no cover - see above
            warnings.append(f"{withheld_rows} message(s) withheld by a row-level rule; this "
                            f"result is scoped, not complete")
        result_rows = GroundedRows(columns=req.projection.fields, rows=tuple(cells), snapshot=snap)
        return AdapterResult(
            read_hash=req.read_hash, rows=result_rows,
            cost=Cost(rows=len(cells), wall_ms=int((time.perf_counter() - started) * 1000),
                      bytes_egressed=sum(l.capture.size for l, _ in rows)),
            warnings=tuple(warnings))

    # --- verb 5: verify ---------------------------------------------------------------
    def verify(self, pointer: SourcePointer, *, principal=None) -> Verdict:
        """Is this pointer still good? Returns the LATTICE, never a boolean (spine D6).

        **Against the capture, first and mostly last.** §8: "`verify()` on that pointer resolves
        against `capture_digest` first and returns `VERIFIED_AS_OF_CAPTURE` — the live mailbox is
        consulted only to detect `VANISHED`, and its disagreement with the capture is expected
        rather than alarming."
        """
        description = self.describe()
        verdict, _note, _content = self._verify(pointer, description, principal)
        return assert_verdict_advertised(verdict, description)

    def verify_with_note(self, pointer: SourcePointer,
                         *, principal=None) -> tuple[Verdict, Optional[str]]:
        """`verify` plus the operator-language reason, for building a `PointerResolution`."""
        description = self.describe()
        verdict, note, _content = self._verify(pointer, description, principal)
        return assert_verdict_advertised(verdict, description), note

    def _verify(self, pointer: SourcePointer, description: SourceDescription,
               principal) -> tuple[Verdict, Optional[str], Any]:
        """Returns ``(verdict, note, content)``. ``content`` is what this pointer currently
        resolves to when the verdict is positive — `None` otherwise — so a `derived` pointer one
        level up can replay ITS OWN derivation over what its parents resolve to NOW, rather than
        over what they resolved to at emission (review finding 5)."""
        if pointer.source_id != self.source_id:
            return Verdict.UNVERIFIABLE, (
                f"pointer cites source {pointer.source_id!r}; this adapter serves "
                f"{self.source_id!r} and has no handle to the other"), None

        if pointer.kind == "derived":
            return self._verify_derived(pointer, description, principal)

        if pointer.kind != "email_part":
            return Verdict.UNVERIFIABLE, (
                f"this Core re-resolves captured MIME parts; pointer kind {pointer.kind!r} is not "
                f"one, and a mailbox cannot answer for a locator it never issued"), None

        if principal is not None and Verdict.FORBIDDEN in description.reachable_verdicts:
            # `FORBIDDEN` is "policy now denies what it once allowed", which is a fact about a
            # PRINCIPAL - §7a correction 3 notes the port as drawn has nobody to deny, so the
            # verb takes one keyword-only, exactly as `PostgresCore` does.
            reason = self._forbidden_reason(pointer, principal, description)
            if reason is not None:
                return Verdict.FORBIDDEN, reason, None

        if not pointer.capture_digest:
            return Verdict.UNVERIFIABLE, (
                "the pointer carries no capture_digest, so it cites a mutable mailbox rather than "
                "the bytes that were parsed. Nobody can now establish WHICH message this value "
                "came out of, and a live re-read would not answer the question (§5)"), None

        live_gone = (pointer.message_id or "") not in self._live_message_ids()
        try:
            raw = self._capture.get(pointer.capture_digest)
        except CaptureShredded as exc:
            return Verdict.VANISHED, (
                f"the captured message was destroyed under an erasure request "
                f"({exc.tombstone.get('authority')}: {exc.tombstone.get('reason')}). The "
                f"determination that cites it is not retracted; the evidence can no longer be "
                f"re-checked"), None
        except CaptureNotFound:
            if live_gone:
                return Verdict.VANISHED, (
                    f"no capture {pointer.capture_digest} is held here and message "
                    f"{pointer.message_id!r} is no longer in the drop: neither the cited artefact "
                    f"nor its source can be produced"), None
            return Verdict.UNVERIFIABLE, (
                f"no capture {pointer.capture_digest} is held by this store (CAPTURE_EXPIRED or "
                f"a different deployment). The live message still exists, but a live re-read is "
                f"not a re-read of the cited artefact - MIME re-encoding changes the bytes without "
                f"changing the message"), None
        except CaptureCorrupt as exc:
            return Verdict.UNVERIFIABLE, (
                f"the stored bytes no longer match the digest that names them ({exc}). A capture "
                f"that did not survive intact is not a capture, and it will not be served"), None

        message = email.message_from_bytes(raw)
        content = extract_part(message, str(pointer.part))
        if content is None:
            return Verdict.DRIFT_MATERIAL, (
                f"part {pointer.part!r} is not present in the captured message. The capture is "
                f"immutable, so a locator that no longer resolves against it was never right"), None

        cited = pointer.snapshot.content_hash if pointer.snapshot else None
        if cited is None:
            return Verdict.UNVERIFIABLE, (
                "the pointer carries no part digest, so a re-read has nothing to compare "
                "against"), None
        if cell_digest(content) != cited:
            return Verdict.DRIFT_MATERIAL, (
                f"part {pointer.part!r} of the captured message {pointer.message_id!r} does not "
                f"hold the content this citation vouches for"), None

        # The pointer's OWN metadata must agree with the artefact it resolves against — checked
        # AFTER the content digest so a cited-value change is always reported as exactly that
        # (the message above), even when the pointer is also missing other metadata. A forged or
        # corrupted `message_id`, `column` or `trust` on a pointer whose CONTENT digest still
        # matches would otherwise verify cleanly (review finding 6).
        identity_problem = self._validate_pointer_identity(pointer, message)
        if identity_problem is not None:
            return Verdict.DRIFT_MATERIAL, identity_problem, None

        note = None
        if live_gone:
            # Expected, not alarming. The cited artefact is the CAPTURE; a mailbox that has since
            # rotated, been cleaned up or had the message deleted does not weaken a citation into
            # bytes that are still held. Recording it is what lets an operator tell "the evidence
            # is gone" from "the mailbox moved on", which are different tasks with different
            # owners.
            note = (f"message {pointer.message_id!r} is no longer in the live drop; the capture "
                    f"still holds the cited part, which is the artefact the pointer addresses")
        return Verdict.VERIFIED_AS_OF_CAPTURE, note, content

    def _verify_derived(self, pointer: SourcePointer, description: SourceDescription,
                        principal) -> tuple[Verdict, Optional[str], Any]:
        """`derived`-kind verification: replay the derivation over what its parents resolve to
        NOW, rather than reporting a positive verdict on the strength of the parents alone
        (review finding 5). "Provenance structurally present" (every parent still verifies) is
        NOT "derivation verified" (the rule, replayed today, still produces the cited value) —
        the two are kept impossible to confuse by actually running the replay here.
        """
        parents = list(pointer.derived_from or ())
        if not parents:
            return Verdict.UNVERIFIABLE, (
                "a derived pointer with no cited parents cannot be re-resolved; its value rests "
                "on a rule with no inputs"), None

        results = [self._verify(p, description, principal) for p in parents]
        joined = combine_verdicts([verdict for verdict, _note, _content in results])
        if not joined.is_positive:
            # No point replaying a derivation over a parent that no longer verifies: the worst
            # parent verdict already dominates whatever the replay would say.
            return joined, (f"derived value: the worst verdict over {len(parents)} cited "
                            f"part(s) is {joined.value}"), None

        try:
            call = parse_derivation(pointer.derivation or "")
        except DerivationError as exc:
            return Verdict.UNVERIFIABLE, (
                f"derived value: the derivation could not be parsed, so it cannot be replayed: "
                f"{exc}"), None

        contents = [content for _verdict, _note, content in results]
        if any(c is None for c in contents):
            return Verdict.UNVERIFIABLE, (
                "derived value: a cited parent verified but its current content could not be "
                "re-established here, so the derivation cannot be replayed"), None

        try:
            produced = replay_derivation(call, contents)
        except DerivationError as exc:
            return Verdict.UNVERIFIABLE, (
                f"derived value: {pointer.derivation!r} is not registered in this process and "
                f"could not be replayed: {exc}"), None

        cited = pointer.snapshot.content_hash if pointer.snapshot else None
        if cited is None:
            return Verdict.UNVERIFIABLE, (
                "the derived pointer carries no content digest, so a replayed value has nothing "
                "to compare against"), None
        if cell_digest(produced) != cited:
            return Verdict.DRIFT_MATERIAL, (
                f"derivation {pointer.derivation!r}, replayed over its cited parts as they read "
                f"now, no longer produces the value this citation vouches for"), None

        return joined, (f"derived value: replayed {pointer.derivation!r} over {len(parents)} "
                        f"cited part(s); the worst parent verdict is {joined.value}"), produced

    def _forbidden_reason(self, pointer: SourcePointer, principal,
                          description: SourceDescription) -> Optional[str]:
        """Re-run the authorisation that minted this pointer, rather than merely checking the
        principal still exists (review finding 7). A role can be narrowed or a grant revoked
        without the principal leaving the directory — `binding_for` alone cannot tell "gone" from
        "still here but no longer entitled to this column" apart, and an adapter may only
        advertise a verdict it can actually compute.

        A throwaway `Budget` is passed so this probe never touches the caller's real admission
        budget: `verify()` reads nothing and must cost nothing.
        """
        column = str(pointer.column or "")
        field = self._by_name.get(column)
        capability = "attachment_read" if field is not None and field.kind == "attachment" \
            else "message_read"
        fields = (column, ENTITY_KEY[0]) if column else ENTITY_KEY
        fields = tuple(dict.fromkeys(fields))
        try:
            req = CapabilityRequest(
                source_id=self.source_id, capability=capability, principal=principal,
                projection=Projection(entity=self._entity, fields=fields, key=ENTITY_KEY),
                order_by=ENTITY_KEY, limit=1)
            admission = self._policy.admit(req, description=description, budget=Budget(max_rows=1))
        except AdapterError as exc:
            return f"the read that minted this citation would be refused now: {exc}"
        if column:
            decision = admission.decisions.decide(self._entity, column, {}, principal)
            if decision.withheld:
                return (f"the principal is still known, but column {column!r} is now withheld "
                        f"by policy ({decision.reason_code}): the read that produced this "
                        f"citation would no longer disclose it")
        return None

    def _validate_pointer_identity(self, pointer: SourcePointer, message: Message) -> Optional[str]:
        """Confirm the pointer's own metadata agrees with the captured message it resolves
        against (review finding 6). `message_id`, `column` and `trust` are convenience labels
        stamped once at emission; nothing before this point re-checks them against the artefact
        itself, so a forged or corrupted pointer citing a real `capture_digest` under a different
        message_id, a different column name, or an upgraded trust label would otherwise verify
        cleanly on the strength of a content hash that says nothing about any of the three.
        Returns the mismatch found, or `None` when every label checks out.
        """
        column = str(pointer.column or "")
        field = self._by_name.get(column)
        if field is None:
            return (f"pointer cites column {column!r}, which {self.source_id!r} does not "
                    f"expose; a citation naming a field that does not exist cites nothing")
        locators = self._locate(message, field) or ()
        if pointer.part not in locators:
            return (f"pointer cites column {column!r} at part {pointer.part!r}, but that "
                    f"location belongs to a different field in the captured message (column "
                    f"{column!r} resolves to {list(locators)})")
        actual_mid = message.get("Message-ID")
        if str(actual_mid or "") != (pointer.message_id or ""):
            return (f"pointer claims message_id {pointer.message_id!r}, but the captured "
                    f"message's own Message-ID header is {actual_mid!r}: the citation and the "
                    f"artefact it resolves against disagree about which message this is")
        declared_trust = self._contract.trust_of(self._entity, column)
        if pointer.trust != declared_trust:
            return (f"pointer stamps trust={pointer.trust!r} on column {column!r}, but the "
                    f"source contract declares {declared_trust!r}. Trust is a lookup, never an "
                    f"inference (D3), and this citation disagrees with the lookup")
        return None

    # ------------------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------------------
    def _admit(self, req: CapabilityRequest) -> Admission:
        """Every verb goes through Policy. There is no argument that bypasses this."""
        if req.source_id != self.source_id:
            raise PolicyNotApplied(
                f"request targets {req.source_id!r}; this adapter serves {self.source_id!r}")
        if req.capability not in self.capabilities:
            raise CapabilityNotAdvertised(
                f"{self.source_id!r} advertises {sorted(self.capabilities)}, not "
                f"{req.capability!r}")
        if req.capability_version is not None:
            raise CapabilityNotAdvertised(
                f"{self.source_id!r} advertises only structural verbs, whose meaning is fixed by "
                f"the canonicalisation scheme; it declares no named queries, so "
                f"capability_version={req.capability_version!r} names a revision of something "
                f"that does not exist")
        if req.projection.entity != self._entity:
            raise CapabilityNotAdvertised(
                f"{self.source_id!r} exposes one entity, {self._entity!r}, not "
                f"{req.projection.entity!r}")
        unknown = [f for f in req.projection.fields if f not in self._by_name]
        if unknown:
            raise CapabilityNotAdvertised(
                f"{self.source_id}.{self._entity} has no field(s) {unknown}; the field set is "
                f"RFC5322 plus whatever `trusted_binding` declares, and it is "
                f"{[f.name for f in self._fields]}")
        return self._policy.admit(req, description=self.describe())

    def _check_as_of(self, req: CapabilityRequest) -> None:
        """The past is refused, and the refusal names the live contract and its status.

        A mailbox replays FORWARD. Even at its declared-best T1 the history cursor cannot
        reconstruct a past state, and as shipped this source is T3 - an unversioned directory drop
        with no version field, no watermark and no tombstone, where a message replaced in place is
        indistinguishable from one that was always that way.

        There is deliberately no branch that substitutes `now()`. A caller that believes it
        received the past and did not is the failure the tier system exists to prevent, and it is
        silent - the messages look exactly like messages.
        """
        if req.as_of is None:
            return
        live = self.live_contract
        elsewhere = ""
        if live:
            elsewhere = (f" A live contract declaring {live.get('pinning_tier')} is on file with "
                         f"status {live.get('status')}; even exercised, a monotonic history "
                         f"cursor replays FORWARD ONLY and expires, so it could not serve this "
                         f"instant either.")
        raise CannotPinPast(
            f"{self.source_id!r} is {self.pinning_tier} "
            f"({self._contract.entry.get('pinning_tier_scope') or 'as declared'}) and cannot "
            f"serve as_of={req.as_of.isoformat()}. An unversioned drop has no past to return "
            f"to.{elsewhere} To cite a past state, resolve the pointer against the Capture Store "
            f"instead - that is what `capture_digest` is for (§5, §8).")

    def _require_total_order(self, req: CapabilityRequest) -> tuple[str, ...]:
        """The key is `message_id`, checked against what the request declared.

        A `Message-ID` is the only stable identity a message has: a path is a locator that a
        re-drop can reuse, an ordinal moves under any change to the drop, and a subject is not
        unique. So a request that declares a different key would mint citations that address
        nothing, and it is refused rather than quietly corrected.
        """
        if req.resolve and not req.order_by:
            return ENTITY_KEY                                    # resolve-only mints no ordinals
        declared = tuple(req.projection.key)
        if declared != ENTITY_KEY:
            raise UnorderedPointeredRead(
                f"the projection declares key {declared or '()'} and a message's stable identity "
                f"is {ENTITY_KEY}. A pointer cites the key, so a request naming a different one "
                f"would produce citations that address nothing")
        tail = tuple(t.lstrip("-") for t in req.order_by)[-len(ENTITY_KEY):]
        if set(tail) != set(ENTITY_KEY):
            raise UnorderedPointeredRead(
                f"order_by {req.order_by} does not end with {ENTITY_KEY}: tied messages have no "
                f"defined position, so `row_ordinal` moves between reads and is not a citation")
        return ENTITY_KEY

    def _raw_row(self, loaded: _Loaded, fields: Sequence[str]) -> dict[str, Any]:
        """The raw located value for each projected field. What ordering and digests run over.

        Deliberately the RAW value, before any registered derivation and before any policy
        transform: a digest over the emitted form would move whenever a rule changed, and a
        citation is supposed to survive a rule change.

        **Every locator, not just the first** (review finding 11). A field can span several MIME
        parts — a `multipart/alternative` body, several attachments — and `_emit_row` joins ALL of
        them into the emitted cell. Digesting only `part[0]` let a change confined to the SECOND
        part (`INJ-04` lives only in the HTML alternative in both estates) leave the read-level
        `content_hash` unmoved: two materially different reads could digest identically. A
        multi-locator field is stored as a tuple of its located values so the digest covers what
        was actually emitted.
        """
        row: dict[str, Any] = {}
        for name in fields:
            field = self._by_name[name]
            locators = self._locate(loaded.message, field)
            if not locators:
                row[name] = None
            elif len(locators) == 1:
                row[name] = extract_part(loaded.message, locators[0])
            else:
                row[name] = tuple(extract_part(loaded.message, part) for part in locators)
        return row

    def _locate(self, msg: Message, field: MailboxField) -> Optional[tuple[str, ...]]:
        """The `part` locator(s) for one field of one message, or `None` when it is not there.

        Returns a TUPLE because a field can span several parts: a `multipart/alternative` message
        has two text parts and both belong to `body`. One locator means a verbatim `email_part`
        pointer; several mean a `derived` pointer over all of them (§4a).
        """
        if field.kind == "header":
            for header in field.headers:
                if msg.get(header) is not None:
                    return (f"header:{header.lower()}",)
            return None
        if field.kind == "body":
            count = len(text_parts(msg))
            if not count:
                return None
            return tuple("body" if i == 0 else f"body:{i}" for i in range(count))
        if field.kind == "attachment":
            count = len(attachment_parts(msg))
            if not count:
                return None
            return tuple(f"attachment:{j}" for j in range(count))
        if field.kind == "body_span":
            parts = text_parts(msg)
            if not parts or field.pattern is None:
                return None
            text = _decode(parts[0])
            if not isinstance(text, str):
                return None
            match = field.pattern.search(text)
            if match is None:
                return None
            return (f"body#{match.start(1)}:{match.end(1)}",)
        return None                                              # pragma: no cover - defensive

    def _read(self, req: CapabilityRequest,
              admission: Admission) -> tuple[SnapshotIdentity, list[tuple[_Loaded, dict]]]:
        """Capture, parse, order, limit — then build the read-level snapshot from what was read.

        The snapshot is built AFTER the messages are in hand, for the reason §7 gives about
        `pg_export_snapshot()`: read consistency comes from what was actually read, and
        replayability comes from an order-independent digest of it.
        """
        key = self._require_total_order(req)
        fields = tuple(dict.fromkeys((*req.projection.fields, *key)))

        wanted: Optional[set[str]] = None
        if req.resolve:
            # RESOLUTION MODE: re-fetch exactly the messages the given pointers address. A pointer
            # is a locator, and re-resolving it must not depend on the ordering that happened to
            # surface it the first time.
            wanted = {str(p.message_id) for p in req.resolve if p.message_id}

        rows: list[tuple[_Loaded, dict]] = []
        seen_keys: dict[str, Path] = {}
        for path in self._message_paths():
            loaded = self._load(path)
            raw = self._raw_row(loaded, fields)
            # The declared entity key must be TOTAL and UNIQUE, or a pointer built on it cites
            # nothing stable (review finding 8). Checked over the WHOLE drop, not only the rows a
            # resolution-mode request happens to want: a corpus whose key is broken is broken
            # regardless of which rows one caller asked for.
            mid = raw.get(key[0])
            if not mid:
                raise MessageNotReadable(
                    f"{self.source_id}: {path.name} has no {key[0]!r} (RFC5322 Message-ID). The "
                    f"declared entity key is {key}, and a message with a missing or empty key "
                    f"has no stable identity for a pointer to cite (§8)")
            mid = str(mid)
            if mid in seen_keys:
                raise MessageNotReadable(
                    f"{self.source_id}: {path.name} and {seen_keys[mid].name} both declare "
                    f"{key[0]}={mid!r}. The declared entity key is not unique, and a pointer "
                    f"built on a colliding key would cite more than one message")
            seen_keys[mid] = path
            if wanted is not None and mid not in wanted:
                continue
            rows.append((loaded, raw))

        terms = req.order_by or tuple(key)
        for term in reversed(terms):
            descending, name = term.startswith("-"), term.lstrip("-")
            rows.sort(key=lambda item, n=name: str(item[1].get(n) or ""), reverse=descending)
        rows = rows[:req.limit]

        snap = SnapshotIdentity(
            source_id=self.source_id,
            as_of=self._now(),
            content_hash=rows_digest([r for _l, r in rows], req.projection.fields, key),
            schema_hash=projection_schema_hash(
                req.projection.entity, req.projection.fields, key,
                {f.name: f.pack_type for f in self._fields}),
            query_hash=req.read_hash,            # D2 - the pointer identity lives HERE
        )
        return snap, rows

    # --- emission -------------------------------------------------------------------
    def _emit_row(self, req: CapabilityRequest, loaded: _Loaded, raw: Mapping[str, Any],
                  ordinal: int, snap: SnapshotIdentity, admission: Admission) -> tuple[Cell, ...]:
        entity = req.projection.entity
        message_id = str(raw.get(ENTITY_KEY[0]) or "")
        cells: list[Cell] = []
        for name in req.projection.fields:
            decision = admission.decisions.decide(entity, name, raw, req.principal)
            if decision.withheld:
                # D4 - a denial, NOT an assertion that a value exists behind it. It carries no
                # pointer, because withholding is not a citation.
                cells.append(Withheld(record=decision.to_record(
                    field_path=f"{entity}.{name}", principal=req.principal,
                    policy_version=admission.decisions.policy_version)))
                continue

            field = self._by_name[name]
            locators = self._locate(loaded.message, field)
            if not locators or raw.get(name) is None:
                # "Not in the source" and "not permitted to you" are different facts (D4). A
                # message with no attachment, no Subject or no matching body pattern is the first.
                cells.append(Absent(field_path=f"{entity}.{name}"))
                continue

            cell = self._emit_cell(req, loaded, field, locators, message_id, ordinal, snap,
                                   decision.transform)
            self._emit_taint(cell)
            cells.append(cell)
        return tuple(cells)

    def _part_pointer(self, req: CapabilityRequest, loaded: _Loaded, part: str,
                      message_id: str, snap: SnapshotIdentity, content: Any,
                      field_name: str) -> SourcePointer:
        """One `email_part` pointer: WHICH bytes, WHICH part of them, and who could write them."""
        span = _SPAN_PART.match(part)
        return SourcePointer(
            source_id=self.source_id,
            kind="email_part",
            message_id=message_id,
            # The character range is carried in `part` as well as in `char_start`/`char_end`, and
            # that redundancy is deliberate. `canonical.canonical_pointer` allowlists
            # `source_id`, `kind`, `capture_digest`, `message_id`, `part` and `child` for this
            # kind and nothing else - so two spans of ONE part would otherwise share a locator
            # identity, and two different citations that digest identically are not citations.
            # Reported as a finding; encoded here so the identity is right in the meantime.
            part=part,
            char_start=int(span.group(1)) if span else None,
            char_end=int(span.group(2)) if span else None,
            # THE CAPTURE IS THE CITED ARTEFACT. Without this the pointer cites a mutable mailbox
            # and the Capture Store is decoration - nobody could later prove which bytes were
            # parsed, and a history sequence is a cursor, not a content hash.
            capture_digest=loaded.capture.qualified_digest,
            # Per-part, deliberately (§7a correction 1): a read-level digest on every pointer
            # makes `verify()` unable to tell a changed cited value from any change at all.
            snapshot=SnapshotIdentity(
                source_id=self.source_id, as_of=snap.as_of, content_hash=cell_digest(content),
                schema_hash=snap.schema_hash, query_hash=snap.query_hash),
            query_hash=req.read_hash,
            column=field_name,
            # Stamped from the SOURCE CONTRACT, never inferred from what the bytes say. Letting
            # content decide its own label is the attack (§4, §8).
            trust=self._contract.trust_of(self._entity, field_name),
        )

    def _emit_cell(self, req: CapabilityRequest, loaded: _Loaded, field: MailboxField,
                   locators: Sequence[str], message_id: str, ordinal: int,
                   snap: SnapshotIdentity, transform: Optional[str]) -> GroundedValue:
        """Verbatim content keeps its `email_part` pointer; anything else gets a `derived` one.

        Three transformations can apply, and each adds one link to the chain rather than replacing
        the one below it, so the raw part stays cited and stays resolvable by a principal entitled
        to see it:

        1. **structural** - a multipart body is joined, a non-text part is digested;
        2. **contract-declared** - `From`/`To` are normalised to the estate's declared comparison
           form;
        3. **policy-declared** - a `sensitive` field is redacted on disclosure.

        `assert_grounded` runs on the emitted cell at every step (§4a, §10): at emission, against
        the content this read actually parsed out of the capture, not in a test and not in review.
        """
        parents: list[SourcePointer] = []
        contents: list[Any] = []
        for part in locators:
            content = extract_part(loaded.message, part)
            contents.append(content)
            parents.append(self._part_pointer(req, loaded, part, message_id, snap, content,
                                              field.name))

        # What the wall is run AGAINST. For every kind but a span it is the cited content itself.
        # For a span the pointer carries `char_start`/`char_end`, so `assert_grounded` slices the
        # WHOLE part down to the cited range first - which is what stops a span pointer being
        # vouched for by a match somewhere else in the same body.
        evidence: list[Any] = list(contents)
        if field.kind == "body_span":
            evidence = [extract_part(loaded.message, "body")]

        # 1. STRUCTURAL. One text part is itself; several are joined; bytes are digested.
        if len(parents) > 1:
            derivation = "join_text_parts@v1(sep=lf2)"
            cell = self._derive(req, snap, derivation, parents, contents, field.name)
        elif isinstance(contents[0], (bytes, bytearray)):
            derivation = "binary_part_digest@v1(algo=sha256)"
            cell = self._derive(req, snap, derivation, parents, contents, field.name)
        else:
            cell = GroundedValue(value=contents[0], pointer=parents[0], confidence=1.0)
            assert_grounded(cell, evidence[0])

        # 2. CONTRACT-DECLARED normalisation, applied to whatever step 1 produced.
        if field.derivation:
            cell = self._maybe_derive(req, snap, field.derivation, cell, field.name)

        # 3. POLICY-DECLARED transform. A permit that NARROWS, not a denial: the value reaches the
        #    caller as a value the source did not literally hold, so §4a applies in full.
        if transform:
            cell = self._maybe_derive(req, snap, str(transform), cell, field.name)
        return cell

    def _derive(self, req: CapabilityRequest, snap: SnapshotIdentity, derivation: str,
               parents: Sequence[SourcePointer], contents: Sequence[Any],
               field_name: str) -> GroundedValue:
        """Build a `derived` cell over cited parents and run the wall over the replay."""
        produced = replay_derivation(parse_derivation(derivation), list(contents))
        # The message this derivation was computed over — always ONE message, since every parent
        # of a single field's derivation is located within the same loaded message. Set here so
        # RESOLUTION MODE can re-fetch a `derived` pointer by its own `message_id` rather than
        # only by digging into `derived_from` (review finding 9): before this, only `email_part`
        # pointers carried `message_id`, so a `req.resolve` set made up entirely of derived
        # pointers (a normalised address, a joined body) collected an EMPTY `wanted` set and
        # `_read` silently returned zero rows.
        mid = next((p.message_id for p in parents if p.message_id), None)
        pointer = SourcePointer(
            source_id=self.source_id,
            kind="derived",
            derivation=derivation,
            derived_from=list(parents),
            message_id=mid,
            # Per-cell, deliberately (§7a correction 1): what `_verify_derived` replays the
            # derivation against and compares to, so a change to the cited parts is caught rather
            # than reported as a positive verdict on the strength of provenance alone (review
            # finding 5).
            snapshot=SnapshotIdentity(
                source_id=self.source_id, as_of=snap.as_of, content_hash=cell_digest(produced),
                schema_hash=snap.schema_hash, query_hash=snap.query_hash),
            query_hash=req.read_hash,
            # LUB over the parents - a derivation never launders trust, and `databridge_core`
            # refuses the pointer if this claims otherwise.
            trust=self._contract.trust_of(self._entity, field_name),
        )
        for parent in parents:
            assert_derived_from(pointer, parent)
        cell = GroundedValue(value=produced, pointer=pointer, confidence=1.0)
        assert_grounded(cell, list(contents))
        return cell

    def _maybe_derive(self, req: CapabilityRequest, snap: SnapshotIdentity, derivation: str,
                      cell: GroundedValue, field_name: str) -> GroundedValue:
        """Apply a registered rule to an already-emitted cell, unless it is a no-op on this value.

        A transformation that changed nothing must NOT get a `derived` pointer: claiming a
        transformation that did not happen costs the cell its locator for nothing, and an
        auditor reading `rfc5322_addresses@v1()` beside an unchanged address would reasonably
        conclude the address had been rewritten.
        """
        produced = replay_derivation(parse_derivation(derivation), [cell.value])
        if produced == cell.value:
            return cell
        return self._derive(req, snap, derivation, [cell.pointer], [cell.value], field_name)

    def _emit_taint(self, cell: Cell) -> None:
        """Register an emitted untrusted value with the engine's taint map (§4).

        > "`TaintMap` is the part that cannot be skipped: every value extracted from an untrusted
        > pointer is registered when it is unwrapped."

        Done HERE, in the Core, rather than left to each caller to remember - a taint map only
        populated by the tests that think to populate it is a naming convention. What it does not
        do is survive string operations; `taint.py` says so and does not pretend otherwise.
        """
        if not self._register_taint or not isinstance(cell, GroundedValue):
            return
        register = getattr(self._policy.taint, "register_value", None)
        if register is None:                                     # pragma: no cover - protocol
            return
        try:
            register(cell)
        except TypeError:                                        # pragma: no cover - unhashable
            pass

    # --- lifecycle --------------------------------------------------------------------
    def close(self) -> None:
        self._loaded.clear()
        self._live_index = None
        self._live_signature = None
