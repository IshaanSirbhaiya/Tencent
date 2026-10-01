"""`McpCore` — the **governed MCP** adapter, and the compiler that builds one (AGENT_PLANE §6).

**Deterministic. No LLM.** Same three-layer split as `postgres.py`: Policy decides, the Core holds
the transport, and no model is anywhere in the read path.

## What this module actually is

Not a connector. A **compiler**. `load_mcp_declaration` reads a pack file
(`packs/<pack>/adapters/<name>.yaml`, AGENT_PLANE §6.2) and `compile_mcp_adapter` turns that
declaration plus a pinned third-party MCP server into an adapter that emits `GroundedValue`s.
Connector breadth is unwinnable — Airbyte ships 600+ for free — but **nobody governs MCP**, and
the four layers wrapped around somebody else's server are the product:

| Layer | Raw MCP gives you | The compiled adapter adds |
|---|---|---|
| Evidence | a JSON blob | every cell carries a `SourcePointer` + a capture digest |
| Trust | nothing | `trust` stamped per field at the boundary, from the pack (D3) |
| Policy | nothing | purpose, grant, budget, egress — enforced at admission (D4a) |
| Pinning | nothing | T3 declared, capture-on-read, so the citation survives the tool call |

## Why this is a *containment* builder, in numbers

Third-party MCP servers are hostile until contained, and the measured position
(`R03-adapter-agents-and-mcp.md` §2) is not ambiguous: **MCPTox 72.8% attack success with <3%
refusal**; *Exposed by Design* found **91.8%** of 640 audited internet-facing servers had no OAuth
and **687 tool instances exposing shell execution**; `postmark-mcp` backdoored ~300 organisations;
scanners report 96.89% risky and manual validation finds **<50% true positives**; **41.6%** of
audited servers vanish within three days; and the official Postgres/Slack/Drive/GitHub servers are
**archived**. So none of the following is a hardening pass over a working adapter — each is load
bearing, and removing any one of them makes the rest theatre.

**1. The declaration digest is pinned, and a change QUARANTINES.** Two pins, because one is not
enough and the fixture proves it: `mcp.digest` covers the vendored artefact bytes and is checked
at load; `mcp.declaration_digest` covers the **tool declarations the server actually serves** and
is re-checked on **every connect**. A server can change a tool description with its binary
byte-identical — that is the rug pull (MCPoison / CVE-2025-54136), and Elastic Security Labs found
most clients do not re-prompt on a description change. A mismatch raises `ServerQuarantined` and
every server-touching verb keeps raising until `approve_declaration()` records a **named human's**
re-approval. There is no flag that skips it.

**2. Everything the server returns is `untrusted` unless a human declared otherwise, in the pack,
with a reason.** For a database an unclassified column is a review gap and the loader refuses it;
for a third-party MCP server nobody owns the schema, so the declared default is `untrusted` and a
field absent from `trust_class` is stamped untrusted rather than refused. Declaring `trusted`
requires a `trusted_justification` entry — the loader will not compile without one. The label is
stamped by the Core **from the declaration** and never inferred from content, because inferring it
lets the thing being contained decide whether it is contained.

**3. Tool declarations are screened at registration**, with `databridge_agent.tools`'
`screen_declaration` (send-shaped and identity-shaped names and descriptions) plus a shell screen
this module adds, plus a **concealment** screen: any Unicode TAG character, bidi control or
zero-width character in a name, description or schema is refused outright rather than decoded,
because arXiv 2607.05744 found 4 of 8 concealment techniques evade string-matching sanitisers.
Screening covers **every** tool the server advertises, not only the ones the pack uses: a shell
tool on the same server is a shell tool one call away.

**4. No registry resolution at runtime.** `mcp.server_ref` is recorded provenance and is never
dereferenced; the launch path is `mcp.vendored_path`, a file in this repo, digest-checked. A URL
in the launch position is refused by name.

**5. T3 by default, so capture is mandatory.** MCP exposes tools, not snapshots or cursors, so
`snapshot()` raises `CannotPinPast` — *including for the present*, because "now" is not a pinnable
instant when a second identical call may return a different set with no signal that it did. Every
pointer carries a `capture_digest` and resolves against the Capture Store. The best verdict this
adapter can return is `VERIFIED_AS_OF_CAPTURE`; `VERIFIED` is not in its `reachable_verdicts` and
`assert_verdict_advertised` enforces that.

**6. Reads only.** `capabilities.write` must be empty. A non-empty one is refused at load with a
message naming why: a write is an `ActionType` with `automation.mode` and a structural human gate
(AGENT_PLANE §6.4), it appends to the ledger before the call, it is idempotent on the action's own
hash and it declares a reversal path. None of that is expressible as a checkbox on a read adapter,
and the sealed-adapter design assumes read-only in a way that is load-bearing.

## Two things this module does NOT claim

**The sandbox is credential scoping, not isolation.** `StdioMcpTransport` launches the server as a
child process with an **explicitly constructed** environment — the parent's API keys and secrets
are not inherited — and pins its cwd. That removes the *documented* path to a credential. It does
not stop a compromised server from opening a socket; only an egress firewall does, and this
deployment has none. The loader therefore **refuses** a declaration claiming `sandbox: microvm` or
`container` unless a launcher is supplied, for the same reason a `T0` claim needs a retention
window: an unenforceable declaration is worse than an honest weaker one.

**The verdict lattice has no state for "the citation and the capture disagree."** If a pointer's
cited digest is not found at its location inside the capture it names, nothing drifted — the
pointer is wrong, which is an integrity failure. `UNVERIFIABLE` is returned with a note saying so,
because inventing a ninth verdict here would fork the lattice per adapter. Recorded as a gap
rather than papered over.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol, Sequence

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from databridge_core import (
    Absent,
    GroundedValue,
    SnapshotIdentity,
    SourcePointer,
    Trust,
    Withheld,
)

from ..canonical import canonical_json, canonical_scalar, sha256_of
from ..capture import Capture, CaptureNotFound, CaptureShredded, CaptureStore, CaptureCorrupt
from ..contract import CapabilityRequest, Principal
from ..describe import (
    CursorDescription,
    EntityDescription,
    FieldDescription,
    SourceDescription,
)
from ..errors import (
    AdapterError,
    CannotPinPast,
    CapabilityNotAdvertised,
    PolicyNotApplied,
    UnorderedPointeredRead,
)
from ..grounding import (
    DerivationError,
    assert_cell_grounded_in_row,
    assert_derived_from,
    register_derivation,
)
from ..policy import Admission, PolicyDecision, PolicyEngine
from ..results import AdapterResult, Cell, Cost, GroundedRows, ReadPlan
from ..verdicts import Verdict, assert_verdict_advertised, combine_verdicts

__all__ = [
    "McpCore",
    "McpAdapterDeclaration",
    "ToolDeclaration",
    "McpTransport",
    "StdioMcpTransport",
    "QuarantineRecord",
    "declaration_digest",
    "screen_tool_declaration",
    "screen_tool_set",
    "load_mcp_declaration",
    "compile_mcp_adapter",
    "McpDeclarationRefused",
    "ToolDeclarationRefused",
    "ShellCapabilityRefused",
    "IdentityDecisionRefused",
    "ConcealedTextRefused",
    "WriteCapabilityRefused",
    "RegistryResolutionRefused",
    "SandboxNotEnforceable",
    "ServerQuarantined",
    "McpTransportError",
    "McpToolError",
    "HostileResponse",
]


# =======================================================================================
# refusals — one type per operator response, the same rule `errors.py` states
# =======================================================================================


class McpDeclarationRefused(AdapterError):
    """The pack's adapter declaration will not compile. There is no "run it anyway" flag.

    An adapter that will not compile will not run (AGENT_PLANE §3): that flag is how every
    governed system eventually becomes ungoverned.
    """


class ToolDeclarationRefused(AdapterError):
    """A tool the server advertises is refused at REGISTRATION, before any call is made.

    The rejection is final and is never fed back to a model to reconsider — a screen that retries
    until something passes is a screen that eventually passes anything (`errors.EscalationRequired`
    makes the same argument for capabilities).
    """


class ShellCapabilityRefused(ToolDeclarationRefused):
    """A shell-shaped tool. 687 tool instances in the audited population expose shell execution
    without access controls (arXiv 2608.00150); none of them is reachable from here."""


class IdentityDecisionRefused(ToolDeclarationRefused):
    """A tool that would let something decide who a subject IS.

    CLAUDE.md hard constraint 1 reaching all the way out to a third-party server. The clinic
    agent's screen catches this shape too, and it matches on a verb immediately followed by an
    identity noun (`verify_identity`, `select_patient`) — which a real MCP server will not
    necessarily oblige with: `verify_applicant_identity` slips it, and the fixture server proves
    that by advertising exactly that name. So this screen is TOKEN-SET based instead: an identity
    noun and a decision verb anywhere in the name, in any order.

    Reported rather than fixed in place: widening
    `databridge_agent.tools._IDENTITY_NAME` is another package's reviewed security screen.
    """


class ConcealedTextRefused(ToolDeclarationRefused):
    """A declaration carrying invisible characters — Unicode TAG block, bidi controls,
    zero-width joiners. Refused as CHARACTERS rather than decoded and inspected, because 4 of 8
    concealment techniques evade string-matching sanitisers (arXiv 2607.05744)."""


class WriteCapabilityRefused(McpDeclarationRefused, ToolDeclarationRefused):
    """A write on a read adapter. Writes are a separate capability class (AGENT_PLANE §6.4)."""


class RegistryResolutionRefused(McpDeclarationRefused):
    """The declaration puts a fetchable reference in the launch position. A server you fetch at
    runtime is a server nobody reviewed."""


class SandboxNotEnforceable(McpDeclarationRefused):
    """The declaration claims an isolation or egress control this deployment cannot enforce."""


class ServerQuarantined(AdapterError):
    """The server's tool declarations no longer match the pinned digest — the rug pull.

    Every verb that would touch the server refuses until a NAMED human re-approves through
    `McpCore.approve_declaration`. Carries the `QuarantineRecord` so the refusal can name exactly
    what moved.
    """

    def __init__(self, message: str, record: "QuarantineRecord") -> None:
        super().__init__(message)
        self.record = record


class McpTransportError(AdapterError):
    """The server is unreachable, died, or spoke something that is not JSON-RPC. Fails closed."""


class McpToolError(AdapterError):
    """The server returned `isError: true`. Refused, and its text is NEVER echoed onward.

    VATS (arXiv 2606.07992) found error paths **triple** standard prompt-injection success, up to
    100% compliance on frontier models — so error text is code-authored here and the server's own
    string is not propagated into any message a model could read.
    """


class HostileResponse(AdapterError):
    """The response is well-formed JSON-RPC and the wrong SHAPE — a nested object where a scalar
    was declared, a missing record list, a non-mapping row. Refused rather than coerced: shape
    confusion is how a hostile server gets a cell past a projection."""


# =======================================================================================
# screening tool declarations
# =======================================================================================

#: Verbs that mean "execute something on the host". Matched as whole tokens in a tool NAME, the
#: same way `databridge_agent.tools` matches send tokens. It over-matches on purpose: rename the
#: tool, do not shorten the list.
_SHELL_TOKENS: frozenset[str] = frozenset({
    "shell", "bash", "sh", "zsh", "powershell", "pwsh", "cmd", "exec", "execute", "spawn",
    "subprocess", "system", "eval", "compile", "interpret", "script", "command", "run",
    "terminal", "console", "process", "kill", "chmod", "sudo", "ssh", "scp", "rsync",
})

#: A description screen for the same idea. Narrower than the name screen, for the reason
#: `databridge_agent.tools` gives: a read tool may legitimately mention a command in prose, and
#: refusing that pushes authors towards vaguer descriptions, which costs more than it protects.
_SHELL_DESCRIPTION = re.compile(
    r"\b(?:run|runs|running|execute|executes|executing|invoke|invokes|spawn|spawns|eval|"
    r"evaluate|evaluates)\b"
    # Up to three qualifiers between the verb and its object. "Run an arbitrary command" and "runs
    # any safe maintenance script" are both how a shell tool describes itself, and a pattern that
    # allowed a single article missed both — which is the shape of near-miss that makes a screen
    # look present and behave absent.
    r"(?:\s+(?:a|an|the|any|some|arbitrary|maintenance|safe|simple|read|only|restricted|"
    r"limited|shell|system|host|remote|local|single|whitelisted|allowed)\b){0,3}\s+"
    r"\b(?:command|commands|shell|bash|script|scripts|binary|process|sql|query|code|program)\b",
    re.I)

#: Mutation verbs. A read adapter may not reach a tool that changes the source, whatever the pack
#: says, because `capabilities.write: []` is only a promise about what we ASK for.
_WRITE_TOKENS: frozenset[str] = frozenset({
    "write", "writes", "create", "creates", "update", "updates", "set", "sets", "delete",
    "deletes", "remove", "removes", "insert", "inserts", "upsert", "upserts", "patch",
    "patches", "put", "puts", "modify", "modifies", "approve", "approves", "grant", "grants",
    "revoke", "revokes", "issue", "issues", "cancel", "cancels", "drop", "drops", "truncate",
    # Widened by finding 10 (`phase3-adapters-mcp` review): `archive_record`, `publish_record`
    # and `apply_changes` all slipped the original list, and pinning only proves the reviewed
    # SPELLING has not changed — it does not make an uncovered spelling read-only. This is still
    # a name heuristic and still coarse; it is not claimed to be complete (see the annotations
    # and description checks below for the other two layers).
    "archive", "archives", "publish", "publishes", "apply", "applies", "mutate", "mutates",
    "alter", "alters", "commit", "commits", "merge", "merges", "sync", "synchronize",
    "synchronise", "finalize", "finalizes", "finalise", "finalises", "submit", "submits",
    "rollback", "reset", "resets", "restore", "restores",
})

#: A description screen for the write class, mirroring `_SHELL_DESCRIPTION`'s reasoning: a name
#: heuristic misses a tool whose NAME says nothing but whose description says "changes the row".
_WRITE_DESCRIPTION = re.compile(
    r"\b(?:mutate|mutates|alter|alters|modify|modifies|change|changes|overwrite|overwrites|"
    r"persist|persists|commit|commits|apply|applies)\b"
    r"(?:\s+(?:a|an|the|any|this|that|new|existing)\b){0,2}\s+"
    r"\b(?:record|records|row|rows|entry|entries|state|value|values|data|database|source|"
    r"document|item|items|change|changes)\b",
    re.I)


#: Nouns that name a SUBJECT, and verbs that name a DECISION about one. A tool carrying both is
#: refused whatever the order, because the composition is the capability: `verify` + `identity` is
#: an identity decision however the author spelled it.
_IDENTITY_NOUNS: frozenset[str] = frozenset({
    "identity", "identities", "patient", "person", "candidate", "applicant", "claimant",
    "subject", "nric", "fin", "passport", "ecard", "id", "individual", "citizen", "resident",
})
_IDENTITY_VERBS: frozenset[str] = frozenset({
    "verify", "verifies", "verification", "confirm", "confirms", "confirmation", "validate",
    "validates", "authenticate", "authenticates", "select", "selects", "selection", "choose",
    "chooses", "pick", "picks", "resolve", "resolves", "match", "matches", "identify",
    "identifies", "prove", "proves", "assert", "asserts",
})


def _name_tokens(name: str) -> set[str]:
    """Lowercase words across snake_case, kebab-case and camelCase. Mirrors the agent screen."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(name))
    return {t for t in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if t}


def _assert_no_concealment(text: str, *, where: str) -> None:
    """Refuse invisible characters anywhere in a declaration.

    Deliberately a CHARACTER-CLASS refusal, not a decode-and-inspect. The concealment paper's
    finding is that string-matching sanitisers miss half the techniques; the property that does
    hold is that none of these characters has any business in a tool name, a description or a
    JSON-Schema key. So the rule is "if you cannot see it, it does not ship", which is checkable.
    """
    for ch in str(text):
        code = ord(ch)
        if 0xE0000 <= code <= 0xE007F:                          # Unicode TAG block
            raise ConcealedTextRefused(
                f"{where} contains a Unicode TAG character (U+{code:04X}). TAG characters render "
                f"as nothing and carry a payload; they are refused as characters rather than "
                f"decoded, because 4 of 8 concealment techniques evade string-matching "
                f"sanitisers (arXiv 2607.05744)")
        if code in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF) or 0x202A <= code <= 0x202E \
                or 0x2066 <= code <= 0x2069:
            raise ConcealedTextRefused(
                f"{where} contains an invisible or bidirectional control character "
                f"(U+{code:04X}). A declaration a reviewer cannot see is a declaration nobody "
                f"reviewed")
        if unicodedata.category(ch) in ("Cf", "Co", "Cs"):
            raise ConcealedTextRefused(
                f"{where} contains a format/private-use character (U+{code:04X}, category "
                f"{unicodedata.category(ch)}). Refused for the same reason as the TAG block")


def screen_tool_declaration(name: str, description: str = "", schema: Any = None, *,
                            raw: Any = None) -> None:
    """Refuse a tool declaration that would give this adapter a capability it must not have.

    Runs at REGISTRATION — on connect, before any call, before any model has spoken. Six screens,
    in order of what each catches:

    1. **concealment** — invisible characters in the name, the description, anywhere in the input
       schema (keys and values, walked recursively), and — when the caller passes `raw`, the
       server's ENTIRE tool declaration (`outputSchema`, `annotations`, anything else it sent),
       for the same reason finding 2 folded `raw` into the pin: a digest over part of a document
       screens part of a document;
    2. **`databridge_agent.tools.screen_declaration`** — send-shaped and identity-shaped names and
       descriptions. Reused rather than restated: it is the screen the clinic agent already
       passes review with, and two copies of a security screen drift;
    3. **identity** — an identity noun paired with a decision verb, in any order. Broader than the
       agent's regex, which wants the noun immediately after the verb and therefore misses
       `verify_applicant_identity`;
    4. **shell** — the name-token and description screens above;
    5. **write, self-declared** — the MCP `annotations.readOnlyHint` / `destructiveHint` the
       server itself ships, when `raw` carries them. A different SIGNAL than the name screen: the
       server saying "I am not read-only" is worth believing even when its name is innocuous,
       though a hostile server can of course lie about its own annotations too — this is a layer,
       not a replacement for the pin;
    6. **write, by name and description** — mutation verbs in the name, and mutation phrasing in
       the description.

    Screens 2-4 and 6 are name/description matching, which is a *coarse* control and is stated as
    one. What makes it hold is that it runs over the tool surface a human reviewed and pinned, so
    a tool that slips the regex still cannot appear later without moving the declaration digest —
    and it is not the only write control: `capabilities.write` must be empty at the declaration
    level regardless of what any individual tool looks like.
    """
    _assert_no_concealment(name, where=f"tool name {name!r}")
    _assert_no_concealment(description, where=f"description of tool {name!r}")
    if schema is not None:
        for part in _walk_strings(schema):
            _assert_no_concealment(part, where=f"input schema of tool {name!r}")
    if raw is not None:
        for part in _walk_strings(raw):
            _assert_no_concealment(part, where=f"declaration of tool {name!r}")

    agent_screen, agent_error = _send_and_identity_screen()
    try:
        agent_screen(name, description)
    except agent_error as exc:
        raise ToolDeclarationRefused(str(exc)) from exc

    tokens = _name_tokens(name)
    nouns = sorted(tokens & _IDENTITY_NOUNS)
    verbs = sorted(tokens & _IDENTITY_VERBS)
    if nouns and verbs:
        raise IdentityDecisionRefused(
            f"tool {name!r} REFUSED: its name pairs {verbs} with {nouns}, which is an identity "
            f"DECISION. Identity is decided in person by a named human, and the system may only "
            f"present candidates — `ResolutionResult.requires_human_selection` is `Literal[True]` "
            f"and there is no code path that sets it False (CLAUDE.md hard constraint 1). An "
            f"adapter may not proxy such a verb from a third-party server either")
    shell = sorted(tokens & _SHELL_TOKENS)
    if shell:
        raise ShellCapabilityRefused(
            f"tool {name!r} REFUSED: the name contains {shell}. A read adapter reaches no tool "
            f"that executes anything; 687 tool instances in the audited MCP population expose "
            f"shell execution without access controls (arXiv 2608.00150)")
    if description and _SHELL_DESCRIPTION.search(description):
        raise ShellCapabilityRefused(
            f"tool {name!r} REFUSED: its description describes executing something. Whether it "
            f"'only runs a safe subset' is not a property this side of the pipe can check")
    if isinstance(raw, Mapping):
        annotations = raw.get("annotations")
        if isinstance(annotations, Mapping):
            read_only = annotations.get("readOnlyHint")
            destructive = annotations.get("destructiveHint")
            if read_only is False or destructive is True:
                raise WriteCapabilityRefused(
                    f"tool {name!r} REFUSED: the server's own MCP annotations declare "
                    f"readOnlyHint={read_only!r}, destructiveHint={destructive!r}. A tool that "
                    f"self-declares as not read-only, or as destructive, is refused on that "
                    f"signal alone — whatever its name says. (A hostile server can lie about its "
                    f"own annotations; this is one more layer, not a replacement for the pin.)")
    write = sorted(tokens & _WRITE_TOKENS)
    if write:
        raise WriteCapabilityRefused(
            f"tool {name!r} REFUSED: the name contains {write}, which is a mutation. Writes are a "
            f"separate capability class: an ActionType carrying automation.mode, appending to the "
            f"ledger BEFORE the call, idempotent on the action's own hash and declaring a "
            f"reversal path (AGENT_PLANE §6.4). None of that is a checkbox on a read adapter, and "
            f"the sealed-adapter design assumes read-only in a way that is load-bearing")
    if description and _WRITE_DESCRIPTION.search(description):
        raise WriteCapabilityRefused(
            f"tool {name!r} REFUSED: its description describes mutating something. A name that "
            f"gives no hint of a write is exactly the shape finding 10 named — the pin proves the "
            f"spelling has not moved, not that the operation is read-only")


_AGENT_SCREEN: Optional[tuple[Callable[..., None], type]] = None


def _send_and_identity_screen() -> tuple[Callable[..., None], type]:
    """`databridge_agent.tools.screen_declaration`, memoised. **Reused, never restated.**

    The clinic agent already has a reviewed screen for send-shaped and identity-shaped tool
    declarations, and two copies of a security screen drift — the second one always ends up the
    stale one. So this imports it rather than duplicating the token lists.

    The import is lazy and memoised because it is not cheap (the agent package pulls the intake
    package behind it), and it fails **closed**: if the screen cannot be imported, no MCP tool
    registers at all. A skipped screen and a passed screen are indistinguishable afterwards, and
    only one of them is safe.

    Worth naming as an architectural cost rather than hiding: this is a dependency edge from the
    adapter fleet to the clinic agent, and `packages/adapter/pyproject.toml` does not declare it.
    The right home for these token lists is a small shared screening module that both packages
    depend on; moving them is outside this workstream's scope and is reported instead.
    """
    global _AGENT_SCREEN
    if _AGENT_SCREEN is None:
        try:
            from databridge_agent.tools import ToolError as _AgentToolError
            from databridge_agent.tools import screen_declaration as _agent_screen
        except ImportError as exc:                               # pragma: no cover - packaging
            raise ToolDeclarationRefused(
                f"the send/identity declaration screen "
                f"(`databridge_agent.tools.screen_declaration`) is not importable in this "
                f"process: {exc}. Refusing to register any MCP tool with a screen missing") from exc
        _AGENT_SCREEN = (_agent_screen, _AgentToolError)
    return _AGENT_SCREEN


def screen_tool_set(tools: Sequence["ToolDeclaration"]) -> None:
    """Screen EVERY tool the server advertises, not only the ones the pack uses.

    A shell tool sitting beside the two we call is a shell tool one call away, and cross-server
    shadowing (the WhatsApp rug-pull) works precisely by using a tool nobody meant to invoke. The
    screen is over the whole surface or it is over nothing.
    """
    for tool in tools:
        screen_tool_declaration(tool.name, tool.description, tool.input_schema,
                                raw=getattr(tool, "raw", None))


def _walk_strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, Mapping):
        for k, v in obj.items():
            yield str(k)
            yield from _walk_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v)


# =======================================================================================
# tool declarations and the pin
# =======================================================================================


@dataclass(frozen=True)
class ToolDeclaration:
    """One tool as the server declares it. **The description is part of the identity.**

    That is the whole rug-pull defence in one sentence: in raw MCP a tool's description is the
    planning context, so redefining it redefines what the tool does without changing its name or
    its schema. Here it is inside the pinned digest, so redefining it quarantines the adapter.

    `raw` is the server's COMPLETE tool declaration — `outputSchema`, `annotations`, `title`,
    anything else it sent — not only the three fields this Core actively reads. Finding 2
    (`phase3-adapters-mcp` review): a digest computed over `name`/`description`/`input_schema`
    alone lets a server change `outputSchema` or `annotations` with zero effect on the pin, which
    is a digest that covers PART of a document while the module's own docstring claims it covers
    "the tool declarations the server actually serves". `canonical()` now hashes `raw` in full,
    with `name`/`description`/`inputSchema` re-asserted from the typed fields so a caller building
    a `ToolDeclaration` by hand (with no `raw`) still gets a meaningful digest.
    """

    name: str
    description: str
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    raw: Mapping[str, Any] = field(default_factory=dict)

    def canonical(self) -> dict[str, Any]:
        payload = dict(self.raw) if self.raw else {}
        payload["name"] = self.name
        payload["description"] = self.description
        payload["inputSchema"] = self.input_schema
        return json.loads(json.dumps(payload, sort_keys=True))


def declaration_digest(tools: Sequence[ToolDeclaration]) -> str:
    """`sha256:…` over the whole advertised tool surface. Order-independent.

    Order-independent because a server is free to list its tools in any order and a digest that
    moved on re-ordering would produce a false quarantine — and a control that cries wolf is a
    control somebody switches off, which is the same argument `rows_digest` makes for data.
    """
    payload = sorted(json.dumps(t.canonical(), sort_keys=True) for t in tools)
    return "sha256:" + sha256_of(canonical_json(payload))


@dataclass(frozen=True)
class QuarantineRecord:
    """Why the adapter is quarantined, and what exactly moved. Carried on the refusal.

    `observed_tools` carries the FULL canonical form of every tool the server advertised at the
    moment of quarantine — not only `changed_tools`' names. Finding 11's second half: a reviewer
    re-approving a rug-pull needs to be able to inspect what actually changed, and a digest plus a
    list of names is not enough to review anything; it is enough to approve blind.
    """

    source_id: str
    expected_digest: str
    observed_digest: str
    detected_at: datetime
    changed_tools: tuple[str, ...] = ()
    detail: str = ""
    observed_tools: tuple[Mapping[str, Any], ...] = ()

    def describe(self) -> str:
        return (f"{self.source_id}: pinned tool declarations {self.expected_digest[:19]}…, server "
                f"now serves {self.observed_digest[:19]}… "
                f"(changed: {', '.join(self.changed_tools) or 'unknown'}). {self.detail}")


# =======================================================================================
# transport
# =======================================================================================


class McpTransport(Protocol):
    """What the Core needs from a server. Three methods, no natural language on the boundary."""

    def list_tools(self) -> tuple[ToolDeclaration, ...]:
        """The server's advertised tool surface, re-fetched on every connect."""
        ...

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> tuple[bytes, Mapping[str, Any]]:
        """Invoke a tool. Returns **(the raw response frame, the parsed message)**.

        The raw frame is what gets captured, so `capture_digest` names the bytes that were
        actually parsed rather than a re-serialisation of them — which is the difference between
        a capture store and a decoration.
        """
        ...

    def close(self) -> None:
        ...


#: Environment variables a child MCP server may inherit. An allowlist, deliberately: a denylist of
#: secret-looking names is a denylist somebody adds a key to. Everything else the server needs must
#: be declared in the pack and passed explicitly.
_INHERITABLE_ENV: tuple[str, ...] = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SystemRoot", "COMSPEC", "ComSpec", "WINDIR", "windir",
    "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
)

#: A bound on `tools/list` pagination (finding 1). A pagination protocol is a reason to keep
#: reading until the server says stop, not a reason to read without limit — a server that never
#: stops, or that cycles `nextCursor` back on itself, is refused rather than looped on forever.
_MAX_TOOL_LIST_PAGES = 1000


def child_environment(declared: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment a vendored MCP server is launched with. **Constructed, never inherited.**

    This is credential scoping and it is precisely that much: the parent's `ANTHROPIC_API_KEY`,
    database URLs, cloud metadata tokens and everything else are simply not in the child's
    environment, so the documented path from a compromised server to a credential does not exist.
    It is **not** isolation — the child can still open a socket, and only an egress firewall
    changes that. `ADAPTER_AGENT_SPEC` §1 makes the same distinction for the Shell, and the claim
    to make in public is the same one: the system is architecturally missing the exfiltration leg,
    not that any given process is incapable of one.
    """
    env = {k: os.environ[k] for k in _INHERITABLE_ENV if k in os.environ}
    env.update({str(k): str(v) for k, v in (declared or {}).items()})
    return env


class StdioMcpTransport:
    """MCP over stdio: newline-delimited JSON-RPC 2.0 to a **child process**.

    Written against the wire format rather than against an SDK, on purpose. 40+ CVEs landed across
    the Python/TypeScript/Java/Rust MCP SDKs between January and April 2026 (R03 §2.1), and the
    stdio framing is a dozen lines. A dependency here would be a dependency inside the containment
    boundary.

    A reader thread plus a deadline gives every call a timeout, which MCP clients SHOULD implement
    and which matters more than usual here: `2026-07-28` removed resumability, so a dropped stream
    must be re-issued as a new request with a new id rather than resumed.
    """

    def __init__(self, *, command: Sequence[str], cwd: Optional[Path] = None,
                 env: Optional[Mapping[str, str]] = None, timeout: float = 20.0,
                 protocol_version: str = "2026-07-28",
                 expected_digest: Optional[str] = None,
                 digest_path: Optional[Path] = None) -> None:
        self._command = list(command)
        self._cwd = Path(cwd) if cwd else None
        self._env = child_environment(env)
        self._timeout = float(timeout)
        self._protocol_version = protocol_version
        self._proc: Optional[subprocess.Popen] = None
        self._lines: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        self._next_id = 0
        # ONE lock, held for the full write-then-await-reply cycle (finding 14). Two threads
        # calling this transport concurrently would otherwise both read from the SAME response
        # queue and each stands an even chance of stealing the other's reply — the `id` guard in
        # `_rpc` only detects the mismatch after the wrong line is already dequeued and gone.
        # REENTRANT because `_start()` — called from inside `_rpc`, under the lock — issues its
        # own `initialize` call through `_rpc` again on the SAME thread; a plain `Lock` would
        # deadlock the very first call.
        self._call_lock = threading.RLock()
        # The TOCTOU half of finding 8. `load_mcp_declaration` checks the artefact digest once,
        # at load time; the process is not actually spawned until the first RPC, which can be
        # arbitrarily later. Re-checking here, immediately before `Popen`, closes the window a
        # digest-checked-then-not-enforced pin leaves open. `digest_path` is None when a caller
        # supplies a custom transport directly (documented as the deployment's own launcher
        # substitution point, not a hook this Core can re-verify from the outside).
        self._expected_digest = expected_digest
        self._digest_path = Path(digest_path) if digest_path else None

    # --- lifecycle ---
    def _start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        if self._expected_digest is not None and self._digest_path is not None:
            try:
                actual = "sha256:" + hashlib.sha256(self._digest_path.read_bytes()).hexdigest()
            except OSError as exc:
                raise McpTransportError(
                    f"cannot re-read the vendored artefact {self._digest_path} immediately "
                    f"before launch to re-verify its pin: {exc}. Failing closed — a pin that "
                    f"cannot be re-checked at the moment of use is not enforced, it is decorative"
                ) from exc
            if actual != self._expected_digest:
                raise McpTransportError(
                    f"the vendored artefact at {self._digest_path} digests to {actual[:19]}… "
                    f"now, and {self._expected_digest[:19]}… was verified at load time. The "
                    f"file changed in the window between the load-time check and this launch "
                    f"(TOCTOU) — refusing to start a process whose pin no longer holds")
        try:
            self._proc = subprocess.Popen(
                self._command, cwd=str(self._cwd) if self._cwd else None, env=self._env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=0)
        except OSError as exc:
            raise McpTransportError(
                f"cannot launch the vendored MCP server {self._command!r}: {exc}. Failing closed — "
                f"a read that cannot reach its source returns an error, never a partial result "
                f"that looks complete") from exc
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        self._rpc("initialize", {
            "protocolVersion": self._protocol_version,
            "capabilities": {},
            "clientInfo": {"name": "walnut-governed-mcp", "version": "1"},
        })

    def _pump(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        for raw in proc.stdout:
            self._lines.put(raw)
        self._lines.put(None)

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=5)
        except Exception:                                        # pragma: no cover - teardown
            proc.kill()

    # --- the RPC ---
    def _rpc(self, method: str, params: Mapping[str, Any]) -> tuple[bytes, Mapping[str, Any]]:
        # Held for the whole write-then-await cycle, not just the write: two threads racing past
        # an unlocked write could each dequeue the OTHER's reply out of the shared queue, and the
        # `id` check below only notices after the wrong line is already gone (finding 14).
        with self._call_lock:
            self._start()
            proc = self._proc
            if proc is None or proc.stdin is None:                # pragma: no cover - defensive
                raise McpTransportError("the MCP server process is not running")
            self._next_id += 1
            request_id = self._next_id
            frame = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method,
                                "params": dict(params)}, sort_keys=True).encode("utf-8") + b"\n"
            try:
                proc.stdin.write(frame)
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise McpTransportError(f"the MCP server closed its input during {method!r}: "
                                        f"{exc}") from exc

            deadline = time.monotonic() + self._timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise McpTransportError(
                        f"the MCP server did not answer {method!r} within {self._timeout:.0f}s. "
                        f"A per-call deadline is not optional: MCP 2026-07-28 removed "
                        f"resumability, so a hung call must be abandoned rather than waited on")
                try:
                    raw = self._lines.get(timeout=remaining)
                except queue.Empty:                               # pragma: no cover - timing
                    continue
                if raw is None:
                    raise McpTransportError(f"the MCP server exited during {method!r}")
                line = raw.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise McpTransportError(
                        f"the MCP server emitted a frame that is not JSON-RPC during {method!r}: "
                        f"{exc}") from exc
                if message.get("id") != request_id:
                    continue                                      # a notification, or a late reply
                if "error" in message:
                    # PROTOCOL error. Code-authored text; the server's own message is not
                    # propagated, because error paths are a stronger injection vector than happy
                    # paths.
                    raise McpTransportError(
                        f"the MCP server returned a JSON-RPC error for {method!r} "
                        f"(code {message['error'].get('code')!r}). Its text is not propagated: "
                        f"error paths triple prompt-injection success (arXiv 2606.07992)")
                return line, message

    # --- the two verbs the Core uses ---
    def list_tools(self) -> tuple[ToolDeclaration, ...]:
        """The server's ENTIRE advertised tool surface — every page, not only the first.

        Finding 1: a server that paginates `tools/list` and is asked only once shows a surface
        that looks complete and is not. A tool on a later page is neither screened nor folded
        into `declaration_digest`, so it can be a shell tool, a write tool, or an identity-decision
        tool that never has to pass registration at all. `_MAX_TOOL_LIST_PAGES` bounds the loop
        against a server that serves an endless or cyclic `nextCursor` — a pagination protocol is
        not a reason to accept an unbounded read.
        """
        out: list[ToolDeclaration] = []
        cursor: Optional[str] = None
        seen_cursors: set[str] = set()
        for _ in range(_MAX_TOOL_LIST_PAGES):
            params: dict[str, Any] = {"cursor": cursor} if cursor is not None else {}
            _raw, message = self._rpc("tools/list", params)
            result = message.get("result") or {}
            tools = result.get("tools")
            if not isinstance(tools, list):
                raise HostileResponse(
                    "tools/list did not return a list of tools. A server whose catalogue cannot "
                    "be read cannot be pinned, and an unpinnable server is not registrable")
            for item in tools:
                if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
                    raise HostileResponse(
                        "a tool declaration is not a mapping with a string name")
                out.append(ToolDeclaration(
                    name=item["name"],
                    description=str(item.get("description") or ""),
                    input_schema=dict(item.get("inputSchema") or item.get("input_schema") or {}),
                    raw=dict(item)))
            next_cursor = result.get("nextCursor")
            if not next_cursor:
                return tuple(out)
            if not isinstance(next_cursor, str) or next_cursor in seen_cursors:
                raise HostileResponse(
                    f"tools/list returned a malformed or repeated nextCursor "
                    f"({next_cursor!r}). A cursor that cannot make progress would either loop "
                    f"forever or make the same page's tools authoritative twice; refused rather "
                    f"than followed")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise McpTransportError(
            f"tools/list did not terminate after {_MAX_TOOL_LIST_PAGES} pages. A catalogue this "
            f"large is refused rather than accepted partially or read without bound")

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> tuple[bytes, Mapping[str, Any]]:
        return self._rpc("tools/call", {"name": name, "arguments": dict(arguments)})


# =======================================================================================
# the declaration (AGENT_PLANE §6.2)
# =======================================================================================


class McpServerRef(BaseModel):
    """The pinned server. `extra="forbid"`, so a key nobody implemented fails loudly."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    server_ref: str = ""
    vendored_path: str
    digest: str
    declaration_digest: str
    transport: str = "stdio"
    protocol_version: str = "2026-07-28"
    sandbox: str = "subprocess"
    sandbox_note: str = ""
    env: Mapping[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> "McpServerRef":
        if self.transport != "stdio":
            raise SandboxNotEnforceable(
                f"transport {self.transport!r} is declared and only 'stdio' is implemented. "
                f"streamable-http would put the server on a network this build cannot firewall, "
                f"and 91.8% of audited internet-facing MCP servers have no OAuth at all "
                f"(arXiv 2608.00150) — so it is refused rather than half-supported")
        if re.match(r"^[a-z][a-z0-9+.-]*://", self.vendored_path):
            raise RegistryResolutionRefused(
                f"vendored_path {self.vendored_path!r} is a URL. The launch path must be a file "
                f"in this repository: a server fetched at runtime is a server nobody reviewed "
                f"(AGENT_PLANE §10), and 41.6% of audited public servers vanish within three days")
        for name, value in (("digest", self.digest),
                            ("declaration_digest", self.declaration_digest)):
            if not re.match(r"^sha256:[0-9a-f]{64}$", value or ""):
                raise McpDeclarationRefused(
                    f"mcp.{name} must be 'sha256:<64 hex>'; got {value!r}. An unpinned server is "
                    f"an unreviewed server, and a malformed pin is an unpinned one")
        if self.sandbox in ("microvm", "container", "vm", "gvisor", "firecracker"):
            raise SandboxNotEnforceable(
                f"sandbox: {self.sandbox!r} is declared and this build launches a child process. "
                f"Declaring an isolation boundary that nothing enforces is the same class of "
                f"error as a tier overclaim: it makes a promise a citation cannot keep. Declare "
                f"`sandbox: subprocess` and its honest note, or supply a launcher")
        if self.sandbox != "subprocess":
            raise SandboxNotEnforceable(
                f"unknown sandbox {self.sandbox!r}; this build implements 'subprocess' only")
        return self


class CaptureDecl(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    when: str = "ledger_bound"
    store: str = ""
    retention: str = "bound_to_ledger_entry"

    @model_validator(mode="after")
    def _check(self) -> "CaptureDecl":
        if self.when != "ledger_bound":
            raise McpDeclarationRefused(
                f"capture.when must be 'ledger_bound' for an MCP source; got {self.when!r}. MCP "
                f"exposes tools, not snapshots, so anything that may be cited must be captured — "
                f"this is the detail most teams defer and then discover during an audit, when the "
                f"bytes are gone (AGENT_PLANE §6.3)")
        return self


class ProjectionDecl(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entity: str
    key: tuple[str, ...]
    fields: tuple[str, ...]

    @model_validator(mode="after")
    def _check(self) -> "ProjectionDecl":
        if not self.fields:
            raise McpDeclarationRefused("projection.fields is empty: nothing may be read at all")
        if not self.key:
            raise McpDeclarationRefused(
                "projection.key is empty. A pointer cites a stable row identity, and an MCP tool "
                "result has no ordinal a citation can survive — two calls may return the same rows "
                "in a different order with nothing to say that they did")
        missing = [k for k in self.key if k not in self.fields]
        if missing:
            raise McpDeclarationRefused(
                f"projection.key names {missing}, which is not projected: a key column that is "
                f"not read cannot be cited")
        return self


class CapabilitiesDecl(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    read: tuple[str, ...] = ()
    write: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> "CapabilitiesDecl":
        if self.write:
            raise WriteCapabilityRefused(
                f"capabilities.write declares {list(self.write)}. Writes are a SEPARATE capability "
                f"class and are not implemented here: a write is invoked by an ActionType and "
                f"never by an adapter, it carries automation.mode, it appends to the ledger BEFORE "
                f"the call and records the outcome after, it is idempotent on the action's own "
                f"hash, and it declares a reversal path — because an agent that can write with no "
                f"way back is an incident generator (AGENT_PLANE §6.4). 'Write if allowed' cannot "
                f"be a checkbox on a read adapter; the read-only assumption is load-bearing")
        if not self.read:
            raise McpDeclarationRefused(
                "capabilities.read is empty: an adapter that can be asked for nothing is not "
                "registrable, and an empty set makes the capability screen vacuous")
        return self


class VerificationDecl(BaseModel):
    """How `verify()` addresses ONE row again. Declared, because a tool surface has no convention.

    Without it the live half of verification is simply unavailable and `verify()` says so, which
    is better than guessing which of the advertised tools takes a primary key.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    lookup_capability: str = ""
    lookup_argument: str = ""


class McpAdapterDeclaration(BaseModel):
    """The compiled form of `packs/<pack>/adapters/<name>.yaml` (AGENT_PLANE §6.2).

    `extra="forbid"` throughout, for the reason `ADAPTER_AGENT_SPEC`'s preamble gives about
    `SourcePointer`: a proposed-but-unimplemented key that is silently ignored produces a
    declaration that appears to govern something and does not.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    adapter: str
    source_id: str
    title: str = ""
    estate: str = ""
    mcp: McpServerRef
    pinning_tier: str = "T3"
    pinning_proof: Optional[str] = None
    pinning_note: str = ""
    capture: CaptureDecl = CaptureDecl()
    projection: ProjectionDecl
    field_types: Mapping[str, str] = Field(default_factory=dict)
    trust_class: Mapping[str, str] = Field(default_factory=dict)
    trusted_justification: Mapping[str, str] = Field(default_factory=dict)
    untrusted_note: Mapping[str, str] = Field(default_factory=dict)
    redaction: Mapping[str, tuple[str, ...]] = Field(default_factory=dict)
    capabilities: CapabilitiesDecl
    verification: VerificationDecl = VerificationDecl()
    egress: Mapping[str, tuple[str, ...]] = Field(default_factory=lambda: {"allow": ()})
    egress_class: str = "RESTRICTED_INTERNAL"
    deletes_detectable: str = "none"
    deletes_note: str = ""

    #: Where the file came from. Every refusal names it, so a reviewer knows which line to edit.
    source_path: Optional[Path] = None

    @model_validator(mode="after")
    def _check(self) -> "McpAdapterDeclaration":
        where = f" ({self.source_path})" if self.source_path else ""

        # T3 unless the SOURCE independently proves a pinning capability. "The MCP server says it
        # supports snapshots" is not such a proof: it is a claim by the thing being contained.
        if self.pinning_tier != "T3" and not (self.pinning_proof or "").strip():
            raise McpDeclarationRefused(
                f"{self.adapter!r}{where} declares pinning_tier {self.pinning_tier!r} with no "
                f"`pinning_proof`. An MCP-derived adapter is T3 by default because MCP exposes "
                f"TOOLS, not snapshots or cursors; a stronger tier needs the UNDERLYING source to "
                f"prove a pinning capability independently of anything the server asserts about "
                f"itself (AGENT_PLANE §6.3)")
        if self.pinning_tier != "T3":
            raise McpDeclarationRefused(
                f"{self.adapter!r}{where} declares pinning_tier {self.pinning_tier!r}. Only T3 is "
                f"implemented: honouring a stronger tier needs a pinning mechanism this transport "
                f"does not have, and accepting the declaration without implementing it would be a "
                f"tier overclaim, which is exactly the failure the tier system exists to prevent")

        # Trust: default UNTRUSTED, `trusted` requires a written reason.
        for name, value in self.trust_class.items():
            if value not in ("trusted", "untrusted"):
                raise McpDeclarationRefused(
                    f"{self.adapter!r}{where}: trust_class[{name!r}] is {value!r}; the lattice is "
                    f"{{trusted, untrusted}} and nothing else")
            if value == "trusted" and not (self.trusted_justification.get(name) or "").strip():
                raise McpDeclarationRefused(
                    f"{self.adapter!r}{where}: field {name!r} is declared `trusted` with no "
                    f"`trusted_justification`. Everything a third-party MCP server returns is "
                    f"untrusted unless a HUMAN declared otherwise (AGENT_PLANE §6.3.3), and a "
                    f"declaration with no reason beside it is indistinguishable from one nobody "
                    f"thought about")
        unknown = sorted(set(self.trust_class) - set(self.projection.fields))
        if unknown:
            raise McpDeclarationRefused(
                f"{self.adapter!r}{where}: trust_class classifies {unknown}, which the projection "
                f"does not read. A trust label on a field nobody reads is a label nobody applies")
        missing_types = sorted(set(self.projection.fields) - set(self.field_types))
        if missing_types:
            raise McpDeclarationRefused(
                f"{self.adapter!r}{where}: field_types omits {missing_types}. `describe()` returns "
                f"a capability matrix, and a field with no declared type cannot appear in one")
        stray = sorted(set(self.redaction) - set(self.projection.fields))
        if stray:
            raise McpDeclarationRefused(
                f"{self.adapter!r}{where}: redaction names unprojected field(s) {stray}")
        for name, classes in self.redaction.items():
            bad = sorted(set(classes) - set(_REDACTION_CLASSES))
            if bad:
                raise McpDeclarationRefused(
                    f"{self.adapter!r}{where}: redaction[{name!r}] names class(es) {bad}; known: "
                    f"{sorted(_REDACTION_CLASSES)}. A redaction class with no rule behind it "
                    f"redacts nothing while appearing to")

        if tuple(self.egress.get("allow") or ()):
            raise SandboxNotEnforceable(
                f"{self.adapter!r}{where}: egress.allow is non-empty and this deployment has no "
                f"enforcing proxy. Four catalogued production exfiltrations — ForcedLeak, "
                f"Superhuman, Claude Cowork, Snowflake — went out through TRUSTED ALLOWLIST "
                f"ENTRIES, one of them an expired domain the researchers re-registered. An "
                f"allowlist nothing enforces is a comment")

        if self.verification.lookup_capability and \
                self.verification.lookup_capability not in self.capabilities.read:
            raise McpDeclarationRefused(
                f"{self.adapter!r}{where}: verification.lookup_capability "
                f"{self.verification.lookup_capability!r} is not in capabilities.read. "
                f"Verification may not reach a tool the read allowlist does not name")
        return self

    # --- reading back ---
    def trust_of(self, field_name: str) -> Trust:
        """The field's trust label. **A lookup, and the default is UNTRUSTED.**

        The opposite default to `sources/contract.py`, and deliberately so. For a database the
        estate owns the schema, so an unclassified column is a review gap and refusing is right.
        For a third-party MCP server nobody owns the schema, the tool surface can grow a field
        between two connects, and the declared position is that anything unclassified is
        attacker-writable.
        """
        return Trust.TRUSTED if self.trust_class.get(field_name) == "trusted" else Trust.UNTRUSTED

    def redaction_for(self, field_name: str) -> tuple[str, ...]:
        return tuple(self.redaction.get(field_name) or ())

    def derivation_for(self, field_name: str) -> str:
        classes = "|".join(self.redaction_for(field_name))
        return f"mcp_redact@v1(column={field_name},classes={classes})"


def load_mcp_declaration(path: Path, *, repo_root: Optional[Path] = None
                         ) -> McpAdapterDeclaration:
    """Read and validate a pack adapter declaration. Also checks the vendored artefact digest.

    The artefact digest is checked HERE and the tool-declaration digest is checked on every
    connect, because they catch different things: the first catches a swapped binary, the second
    catches the same binary serving a different description. The fixture in `fixtures/mcp/` proves
    the second is not implied by the first.
    """
    path = Path(path)
    if not path.is_file():
        raise McpDeclarationRefused(
            f"{path}: no adapter declaration. An MCP server with no governance spec is not an "
            f"adapter; it is a tool surface, and this build has no way to read one")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise McpDeclarationRefused(f"{path}: top level must be a mapping")
    data = {k: v for k, v in data.items() if not str(k).startswith("_")}
    data["source_path"] = path
    declaration = McpAdapterDeclaration.model_validate(data)

    root = Path(repo_root) if repo_root else _repo_root()
    vendored = (root / declaration.mcp.vendored_path).resolve()
    if not _contained(vendored, root):
        raise RegistryResolutionRefused(
            f"{path}: mcp.vendored_path {declaration.mcp.vendored_path!r} resolves to {vendored}, "
            f"which is OUTSIDE {root}. The launch path must stay inside the repository that was "
            f"reviewed and pinned — a relative path that walks out of it (`../../outside/...`) "
            f"names a file nobody reviewed just as surely as a URL does")
    if not vendored.is_file():
        raise RegistryResolutionRefused(
            f"{path}: mcp.vendored_path {declaration.mcp.vendored_path!r} does not resolve to a "
            f"file under {root}. Servers are vendored and pinned, never fetched on demand")
    actual = "sha256:" + hashlib.sha256(vendored.read_bytes()).hexdigest()
    if actual != declaration.mcp.digest:
        raise ServerQuarantined(
            f"{path}: the vendored server at {declaration.mcp.vendored_path} digests to "
            f"{actual[:19]}…, and the declaration pins {declaration.mcp.digest[:19]}…. The "
            f"artefact changed since it was reviewed. Re-review it and re-pin — a digest that is "
            f"updated to match whatever is on disk is not a pin, it is a checksum of the attack",
            QuarantineRecord(source_id=declaration.source_id,
                             expected_digest=declaration.mcp.digest, observed_digest=actual,
                             detected_at=datetime.now(timezone.utc),
                             detail="vendored artefact digest mismatch at load"))
    return declaration


def _repo_root() -> Path:
    """`databridge/` — packages/adapter/databridge_adapter/sources/mcp.py -> up four."""
    return Path(__file__).resolve().parents[4]


def _contained(path: Path, root: Path) -> bool:
    """Is `path` (already resolved) actually inside `root`? Part of finding 8's containment gap.

    `(root / vendored_path).resolve()` happily resolves a `vendored_path` of
    `../../outside/server.py` to a file OUTSIDE `root` — the string concatenation does not stop a
    `..` from walking back out, so "a file in this repository" was a claim the code never checked.
    """
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


# =======================================================================================
# the redaction rule — registered, versioned, replayable (§4a)
# =======================================================================================

#: Inline patterns. Unlike `derivations.PII_PATTERNS`, these are NOT anchored: an MCP free-text
#: field carries an address or a number *inside* a sentence, and a whole-value matcher redacts
#: nothing there while appearing to. Same classes, different scope, so it is a different rule with
#: its own name and version rather than a quiet widening of the existing one.
_REDACTION_CLASSES: Mapping[str, "re.Pattern[str]"] = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    # Trailing lookahead is `(?!\d)` rather than `(?![\w.])`: a number at the end of a sentence
    # is followed by a full stop, and excluding "." there made the pattern miss every phone number
    # that ends one — which is most of them in a free-text field.
    "phone": re.compile(r"(?<![\w.-])\+?\d[\d\s().-]{6,}\d(?!\d)"),
    "national_id": re.compile(r"(?<![\w-])(?:\d{3}-\d{2}-\d{4}|[A-Z]\d{7}[A-Z])(?![\w-])"),
}


def _mcp_redact_v1(parents: Sequence[Any], args: Mapping[str, str]) -> Any:
    column = args.get("column")
    if not column:
        raise DerivationError("mcp_redact@v1 requires a `column` argument")
    if len(parents) != 1:
        raise DerivationError(
            f"mcp_redact@v1 transforms exactly one cell, got {len(parents)} parent value(s)")
    value = parents[0]
    if not isinstance(value, str):
        return value
    classes = [c for c in (args.get("classes") or "").split("|") if c]
    unknown = sorted(set(classes) - set(_REDACTION_CLASSES))
    if unknown:
        raise DerivationError(
            f"mcp_redact@v1 names unregistered class(es) {unknown}; a replay that silently skips "
            f"a class would re-certify a value the original rule never produced")
    out = value
    for name in classes:
        out = _REDACTION_CLASSES[name].sub(f"<REDACTED:{name}>", out)
    return out


register_derivation(
    "mcp_redact", 1, _mcp_redact_v1,
    doc="Replace INLINE occurrences of an email address, a phone number or a national identifier "
        "inside a free-text field with a typed <REDACTED:kind> marker. Distinct from redact_pii, "
        "whose patterns are anchored to the whole cell.")


# =======================================================================================
# digests — restated here rather than imported from postgres.py
# =======================================================================================
#
# `sources/postgres.py` computes the same three digests and importing them would drag SQLAlchemy
# into every process that reads an MCP server. Same argument `derivations.py` makes for the PII
# patterns, and the same remedy: duplication guarded by a test
# (`test_the_cell_digest_matches_the_postgres_core`) rather than a layering violation guarded by a
# comment.


def _token(value: Any) -> Mapping[str, str]:
    try:
        return canonical_scalar(value)
    except (TypeError, ValueError):
        if isinstance(value, (bytes, bytearray, memoryview)):
            return {"t": "bytes", "v": sha256_of(bytes(value).hex())}
        return {"t": f"opaque:{type(value).__name__}", "v": sha256_of(repr(value))}


def cell_digest(value: Any) -> str:
    """The content address of ONE cell. What `verify()` compares to decide material drift."""
    return sha256_of(canonical_json(_token(value)))


def row_digest(row: Mapping[str, Any], fields: Sequence[str], key: Sequence[str]) -> str:
    return sha256_of(canonical_json({
        "key": [_token(row.get(k)) for k in key],
        "cells": {f: _token(row.get(f)) for f in fields},
    }))


def rows_digest(rows: Sequence[Mapping[str, Any]], fields: Sequence[str],
                key: Sequence[str]) -> str:
    """Order-independent, for the reason §5 rule 2 gives — and doubly so here: an MCP tool makes
    no ordering promise at all, so an order-dependent digest would report drift on every call."""
    return sha256_of(canonical_json(sorted(row_digest(r, fields, key) for r in rows)))


def projection_schema_hash(declaration: McpAdapterDeclaration, fields: Sequence[str],
                           tool_digest: str) -> str:
    """`schema_hash` over the read projection **and the pinned tool declarations**.

    The tool surface IS the schema for an MCP source: there is no DDL to reflect, and the shape of
    what comes back is whatever the tool decides today. Folding the declaration digest in means a
    changed tool description moves `schema_hash`, so pointers issued before the change are
    distinguishable from pointers issued after it — which is what makes the quarantine
    reconstructable after the fact rather than only preventable in the moment.
    """
    return sha256_of(canonical_json({
        "entity": declaration.projection.entity,
        "key": list(declaration.projection.key),
        "fields": [{"name": f, "type": declaration.field_types.get(f, "unknown")}
                   for f in fields],
        "tool_declarations": tool_digest,
    }))


# =======================================================================================
# the Core
# =======================================================================================


class McpCore:
    """The five verbs over a pinned, screened, sandboxed MCP server. Deterministic. No LLM.

    Constructed by `compile_mcp_adapter`, never directly from a YAML path, so there is no way to
    obtain one without the declaration having compiled. It cannot be constructed without a
    `PolicyEngine` or without a `CaptureStore`: the first is the in-process half of "a caller must
    not be able to invoke Core directly and skip Policy", and the second is not optional at T3 —
    an adapter that could read without capturing would issue citations that stop resolving the
    moment the tool answers differently.
    """

    pinning_tier: str = "T3"

    def __init__(self, *, declaration: McpAdapterDeclaration, transport: McpTransport,
                 policy: PolicyEngine, capture: CaptureStore,
                 now: Optional[Callable[[], datetime]] = None,
                 pack_version: str = "") -> None:
        if policy is None:                                       # pragma: no cover - typing
            raise PolicyNotApplied("a Core cannot be constructed without a PolicyEngine")
        if capture is None:                                      # pragma: no cover - typing
            raise McpDeclarationRefused(
                "an MCP Core cannot be constructed without a CaptureStore: the source is T3, so a "
                "pointer that does not resolve against a capture cites nothing that can be checked")
        self._declaration = declaration
        self._transport = transport
        self._policy = policy
        self._capture = capture
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._pack_version = pack_version or declaration.estate
        self.source_id = declaration.source_id
        self.capabilities: frozenset[str] = frozenset(declaration.capabilities.read)
        self._accepted_digest = declaration.mcp.declaration_digest
        self._quarantine: Optional[QuarantineRecord] = None
        self._tools: tuple[ToolDeclaration, ...] = ()
        self._described: Optional[SourceDescription] = None
        self._approvals: list[Mapping[str, str]] = []

    # --- the pin ----------------------------------------------------------------------
    @property
    def quarantined(self) -> Optional[QuarantineRecord]:
        return self._quarantine

    @property
    def approvals(self) -> tuple[Mapping[str, str], ...]:
        """Every re-approval this adapter has been given, with who gave it and why."""
        return tuple(self._approvals)

    def connect(self) -> tuple[ToolDeclaration, ...]:
        """Fetch the tool surface, **screen it, and check it against the pin.** Every connect.

        Three things happen here and the order matters. The declarations are screened FIRST, so a
        shell tool is refused even if the digest happens to match a pin somebody approved by
        mistake. Then the digest is compared, and a mismatch quarantines. Then the pack's declared
        read capabilities are checked to exist, because a pack naming a tool the server does not
        serve is a pack that would fail on first use with a confusing error.
        """
        if self._quarantine is not None:
            raise ServerQuarantined(
                f"{self.source_id} is QUARANTINED and no verb that touches the server will run. "
                f"{self._quarantine.describe()} Re-approval is `approve_declaration(digest, "
                f"attested_by=…, reason=…)` by a named human; there is no flag that skips it.",
                self._quarantine)

        tools = self._transport.list_tools()
        try:
            screen_tool_set(tools)                    # registration-time, before any call
        except ToolDeclarationRefused:
            # Finding 9: a screening REFUSAL is itself a discovery about the server, not a
            # one-off validation error to surface and forget. Without this, a server could present
            # a hostile surface once (get refused, non-stickily), then present the ORIGINAL pinned
            # surface on the very next connect and carry on as if nothing had happened — the rug
            # pull would have been detected and then silently un-detected. So this SETS
            # `self._quarantine` before re-raising, even though the failure is a screening
            # refusal rather than a digest mismatch — the guard at the top of this method then
            # makes it sticky exactly the way a digest-mismatch quarantine already is, and a
            # SECOND `connect()` (whether the caller retries, or the hostile server reverts to
            # its pinned surface on the next call) is refused by that guard rather than
            # re-evaluated fresh.
            #
            # The ORIGINAL exception type and message propagate unchanged (bare `raise`):
            # `ShellCapabilityRefused`/`IdentityDecisionRefused`/etc. are what callers and tests
            # key off, and wrapping them in `ServerQuarantined` here would silently change what a
            # caller catching `ShellCapabilityRefused` sees on the FIRST refusal.
            self._quarantine = QuarantineRecord(
                source_id=self.source_id, expected_digest=self._accepted_digest,
                observed_digest=declaration_digest(tools), detected_at=self._now(),
                changed_tools=self._changed_tools(tools),
                detail=("Tool declaration screening refused the server's advertised surface. A "
                        "surface that fails screening once must not be silently retried away on "
                        "the next connect."),
                observed_tools=tuple(t.canonical() for t in tools))
            raise

        observed = declaration_digest(tools)
        if observed != self._accepted_digest:
            changed = self._changed_tools(tools)
            self._quarantine = QuarantineRecord(
                source_id=self.source_id, expected_digest=self._accepted_digest,
                observed_digest=observed, detected_at=self._now(), changed_tools=changed,
                detail=("The server's declared tool surface moved after it was approved. In raw "
                        "MCP a tool description IS the planning context, so redefining it "
                        "redefines what the tool does without changing its name or its schema — "
                        "and most clients do not re-prompt on a description change."),
                observed_tools=tuple(t.canonical() for t in tools))
            raise ServerQuarantined(
                f"{self.source_id}: QUARANTINED. {self._quarantine.describe()} No read runs until "
                f"a named human re-approves the new declaration.", self._quarantine)

        advertised = {t.name for t in tools}
        missing = sorted(set(self._declaration.capabilities.read) - advertised)
        if missing:
            raise CapabilityNotAdvertised(
                f"{self.source_id}: the pack declares read capability(ies) {missing} and the "
                f"server advertises {sorted(advertised)}. Refused rather than degraded: a pack "
                f"that names a tool the server does not serve has not been reviewed against this "
                f"server")
        self._tools = tools
        return tools

    def _changed_tools(self, observed: Sequence[ToolDeclaration]) -> tuple[str, ...]:
        """Which tool names differ from the last accepted surface, for the refusal's message.

        Best effort: on the first connect there is no previous surface, so it names every tool
        whose declaration is not byte-identical to what the pack recorded — which is why the pack
        does not record the bodies, only the digest, and this falls back to naming all of them.
        """
        if not self._tools:
            return tuple(sorted(t.name for t in observed))
        before = {t.name: t.canonical() for t in self._tools}
        after = {t.name: t.canonical() for t in observed}
        return tuple(sorted(set(before) ^ set(after)) or
                     [n for n in sorted(set(before) & set(after)) if before[n] != after[n]])

    def approve_declaration(self, digest: str, *, attested_by: str, reason: str,
                            principal: Optional[Principal] = None) -> QuarantineRecord:
        """Re-approve a moved tool surface. **A named human, a written reason, or nothing.**

        This is the whole point of the quarantine: it is not a retry, it is a re-review. The
        signature refuses a blank attester for the same reason `CaptureStore.shred` does — an act
        nobody can be attributed to is not an act, it is a gap in the record. The approval is
        appended to `approvals`, never replacing an earlier one, so the history of what this
        adapter has been permitted to talk to survives.

        ``principal`` is optional and additive (finding 11's core complaint: nothing here checked
        for an authenticated principal, an authorization check, or a human-attestation TYPE — any
        caller holding the Core could approve with `attested_by="bot"`). Left `None`, `attested_by`
        stays a free-text name, exactly the prior behaviour, because a Core is also reachable from
        contexts with no principal directory at hand (a CLI, an offline demo). **A caller that DOES
        hold an authenticated `Principal`** — the gateway's own re-approval route, when one exists
        — should pass it: it is checked against this adapter's own `PolicyEngine` directory the
        same way every read is (`binding_for`, the identical call `_verify` makes for `FORBIDDEN`),
        so `attested_by="bot"` fails the moment a real directory is asked to vouch for it, and the
        resolved identity and roles are recorded on the approval rather than trusting the free-text
        name alone.
        """
        record = self._quarantine
        if record is None:
            raise McpDeclarationRefused(
                f"{self.source_id} is not quarantined; there is nothing to approve. Re-approving "
                f"an adapter that is running would create an approval record for a decision "
                f"nobody made")
        if not str(attested_by or "").strip():
            raise McpDeclarationRefused(
                "re-approving a changed MCP tool surface requires a named human: an approval "
                "nobody can be attributed to is not an approval, and this is the exact control "
                "that stands between an approved server and a rug pull")
        if not str(reason or "").strip():
            raise McpDeclarationRefused(
                "re-approving a changed MCP tool surface requires a written reason: 'the tests "
                "were red' is a reason and it is one an auditor can weigh, which is more than a "
                "blank field offers")
        if digest != record.observed_digest:
            raise McpDeclarationRefused(
                f"approval names digest {digest[:19]}… and the server serves "
                f"{record.observed_digest[:19]}…. Approve what is actually there, or the approval "
                f"records agreement to something nobody looked at")
        resolved_roles: tuple[str, ...] = ()
        if principal is not None:
            binding = self._policy.binding_for(principal)      # raises if unknown — fail closed
            resolved_roles = binding.roles
        self._accepted_digest = digest
        self._quarantine = None
        self._approvals.append({
            "digest": digest, "previous_digest": record.expected_digest,
            "attested_by": str(attested_by), "reason": str(reason),
            "approved_at": self._now().isoformat(),
            "principal_actor": principal.actor if principal is not None else "",
            "principal_roles": ",".join(resolved_roles),
        })
        return record

    # --- verb 1: describe -------------------------------------------------------------
    def describe(self) -> SourceDescription:
        """Schema, capability matrix, retention reality, egress class. **No data, no server call.**

        Built entirely from the PACK, not from the server. That is the inversion this whole module
        is: what the adapter can prove is a statement by the people who reviewed it, and the
        server's own declarations are checked *against* it rather than believed.
        """
        if self._described is not None:
            return self._described
        decl = self._declaration
        fields = tuple(
            FieldDescription(
                name=name,
                type=decl.field_types.get(name, "unknown"),
                trust=decl.trust_of(name),
                sensitive=bool(decl.redaction_for(name)),
            )
            for name in decl.projection.fields)
        entity = EntityDescription(name=decl.projection.entity, fields=fields,
                                   key=tuple(decl.projection.key))
        self._described = SourceDescription(
            source_id=decl.source_id,
            title=decl.title,
            kind="mcp",
            capabilities=self.capabilities,
            entities=(entity,),
            pinning_tier="T3",
            cursor=_NO_CURSOR,
            deletes_detectable=decl.deletes_detectable,
            detects_benign_drift=False,
            effective_retention={
                "source": "unknown — a third-party tool surface makes no retention statement",
                "capture": decl.capture.retention,
            },
            documented_retention=None,
            capture_required=True,
            egress_class=decl.egress_class,
            # VERIFIED is deliberately ABSENT. A T3 source cannot prove what it said at a past
            # instant, so the strongest honest verdict is that the CAPTURE still matches.
            # `assert_verdict_advertised` turns that into an enforced property rather than a
            # docstring, and DRIFT_BENIGN is absent for the reason §5 gives.
            reachable_verdicts=frozenset({
                Verdict.VERIFIED_AS_OF_CAPTURE, Verdict.DRIFT_MATERIAL, Verdict.VANISHED,
                Verdict.FORBIDDEN, Verdict.UNVERIFIABLE,
            }),
        )
        return self._described

    # --- verb 2: snapshot -------------------------------------------------------------
    def snapshot(self, req: CapabilityRequest) -> SnapshotIdentity:
        """**Always raises `CannotPinPast`.** MCP exposes tools, not snapshots or cursors.

        Including for the present, and that is not pedantry. `snapshot()` promises "this identity
        names a state a later read can return to". A tool call has no snapshot id, no cursor, no
        version handle and no ordering guarantee; two identical calls a second apart may return
        different sets with nothing to say that they did. Returning "now" here would hand the
        caller an identity that identifies nothing, which is the silent failure the tier system
        exists to prevent — the rows look exactly like rows.

        The pin that DOES exist is the capture: `resolve()` stores the response frame, and
        `SourcePointer.capture_digest` names those bytes forever.
        """
        self._admit(req)
        raise CannotPinPast(
            f"{self.source_id!r} is T3: an MCP tool surface exposes tools, not snapshots or "
            f"cursors, so no instant — past or present — is pinnable through it"
            + (f" (as_of={req.as_of.isoformat()})" if req.as_of else "") +
            f". The citation is pinned by CAPTURE instead: resolve() stores the response frame "
            f"content-addressed and every pointer carries its `capture_digest`. Resolve against "
            f"the Capture Store; do not ask the server what it said last week, because it does "
            f"not know and it will answer anyway")

    # --- verb 3: plan -----------------------------------------------------------------
    def plan(self, req: CapabilityRequest) -> ReadPlan:
        """Cost/row estimate plus the exact operations. Deterministic; **no server call.**"""
        admission = self._admit(req)
        decl = self._declaration
        operations = [
            f"mcp tools/call name={req.capability!r} "
            f"arguments={{{', '.join(f'{k}=<bound>' for k, _ in req.params)}}}",
            f"-- server pinned at {decl.mcp.digest[:19]}… (artefact) and "
            f"{self._accepted_digest[:19]}… (tool declarations); a change quarantines",
            f"-- capture the response frame ({decl.capture.retention}) before any cell is emitted",
            f"-- project {list(decl.projection.fields)}; drop every other field the server returns",
            f"-- sort by {list(req.order_by)} locally: an MCP tool makes no ordering promise",
        ]
        for name in decl.projection.fields:
            if decl.redaction_for(name):
                operations.append(f"-- {decl.derivation_for(name)} on {name}")
        withheld = [c.column for c in admission.decisions.columns if c.withheld]
        if withheld:
            operations.append(f"-- withheld by policy, never requested: {', '.join(withheld)}")
        return ReadPlan(
            read_hash=req.read_hash, source_id=self.source_id, entity=req.projection.entity,
            operations=tuple(operations),
            estimated_rows=req.limit,
            estimated_cost=Cost(rows=req.limit),
            requires_capture=True,
            completeness_caveat=(
                "an MCP tool is not a cursor: this read cannot be shown to have missed nothing, "
                "and a second identical call may return a different set with no signal that it "
                "did. Deletes are undetectable — a tool that stops returning a row is not a "
                "tombstone"),
        )

    # --- verb 4: resolve --------------------------------------------------------------
    def resolve(self, req: CapabilityRequest) -> AdapterResult:
        """Execute. Returns `GroundedRows`: every cell carries a `SourcePointer`.

        The order is the design: **admit → connect and re-check the pin → call → CAPTURE → parse
        the capture → emit.** The parse runs over bytes read back out of the capture store, not
        over the in-memory response, so the evidence a cell is grounded against and the bytes its
        pointer names are the same object by construction rather than by intention.
        """
        started = time.perf_counter()
        admission = self._admit(req).check()
        decl = self._declaration
        key = self._require_total_order(req)

        if req.resolve:
            return self._resolve_pointers(req, admission, key, started)

        self.connect()                                   # the pin, re-checked on every read
        raw, _message = self._transport.call_tool(req.capability, dict(req.param_map))
        capture = self._capture.put(
            raw, media_type="application/json", source_id=self.source_id,
            retention="ledger_bound",
            note=f"mcp tools/call {req.capability} for read {req.read_hash[:16]}")

        # Read the bytes BACK. The capture store re-digests on read, so this also proves the
        # capture is intact before a single citation is minted against it.
        records = _records_from_frame(self._capture.get(capture.qualified_digest),
                                      source_id=self.source_id)
        records = self._order(records, req)[:req.limit]

        tool_digest = self._accepted_digest
        schema_hash = projection_schema_hash(decl, req.projection.fields, tool_digest)
        snap = SnapshotIdentity(
            source_id=self.source_id,
            as_of=self._now(),
            content_hash=rows_digest(records, req.projection.fields, key),
            schema_hash=schema_hash,
            query_hash=req.read_hash,
        )

        row_ids = _require_row_ids(records, key, source_id=self.source_id)
        cells: list[tuple[Cell, ...]] = []
        withheld_rows = 0
        for ordinal, (record, row_id) in enumerate(zip(records, row_ids)):
            denial = admission.decisions.row_verdict(
                record, attributes=admission.attribute_map, subquery_sets={})
            if denial is not None:
                # Invariant 4 / finding 5: a policy denial is a `Withheld` CELL, with a reason
                # code, never a row that silently disappears. `continue`-ing here would make a
                # denied row indistinguishable from a row the source never returned — exactly the
                # distinction the four-state cell vocabulary exists to preserve.
                withheld_rows += 1
                cells.append(self._emit_withheld_row(req, denial, admission))
                continue
            cells.append(self._emit_row(req, record, row_id, ordinal, snap, capture, admission))

        warnings = list(admission.notes)
        warnings.append(
            f"T3 source: every cell above resolves against capture {capture.qualified_digest[:19]}"
            f"…, never against a re-call of the tool")
        if withheld_rows:
            warnings.append(f"{withheld_rows} row(s) denied by a row-level rule and returned as "
                            f"`Withheld` cells; this result is scoped, not complete")
        durability = self._capture.durability()
        if not durability.immutable_at_rest:
            warnings.append(
                f"capture backend {durability.backend!r} does not enforce immutability at rest: "
                f"{durability.notes}")

        rows = GroundedRows(columns=req.projection.fields, rows=tuple(cells), snapshot=snap)
        return AdapterResult(
            read_hash=req.read_hash, rows=rows,
            cost=Cost(rows=len(cells), wall_ms=int((time.perf_counter() - started) * 1000),
                      bytes_egressed=len(raw)),
            warnings=tuple(warnings))

    def _resolve_pointers(self, req: CapabilityRequest, admission: Admission,
                          key: Sequence[str], started: float) -> AdapterResult:
        """RESOLUTION MODE: re-fetch the cells these pointers address — **from the CAPTURE**.

        This is the half of `ADAPTER_AGENT_SPEC` §11.5 that people forget: an untrusted pointer
        may be *re-resolved*, because using a locator to retrieve bytes is not the same as letting
        the bytes decide something. `resolve` is deliberately not in `TRUSTED_SINKS`.

        And it never touches the server. At T3 the citation's evidence IS the capture, so
        re-resolving a pointer against a live tool call would answer a different question — "what
        does the server say today?" — while looking like it answered the original one. A caller
        that wants the live comparison asks `verify()`, which reports it as a verdict rather than
        smuggling it back as a value.
        """
        decl = self._declaration
        wanted: dict[str, tuple[str, SourcePointer]] = {}
        for ptr in req.resolve:
            # Finding 3: nothing here checked WHOSE pointer this is. A fabricated pointer, or a
            # perfectly genuine pointer from a DIFFERENT source, whose `capture_digest` happens to
            # name a JSON blob this Core can parse as rows, would otherwise be emitted under THIS
            # adapter's `source_id`, trust declarations and schema hash — source forgery, and
            # potentially trust laundering if the foreign source is more permissive.
            if ptr.source_id != self.source_id:
                raise UnorderedPointeredRead(
                    f"{self.source_id}: a pointer offered for re-resolution cites source "
                    f"{ptr.source_id!r}, not this adapter. Refused rather than resolved against a "
                    f"capture this adapter did not produce — a pointer's own `source_id` is what "
                    f"stops one adapter's evidence being relabelled as another's")
            if ptr.snapshot is not None and ptr.snapshot.source_id \
                    and ptr.snapshot.source_id != self.source_id:
                raise UnorderedPointeredRead(
                    f"{self.source_id}: a pointer offered for re-resolution carries a snapshot "
                    f"identity for source {ptr.snapshot.source_id!r}, not this adapter")
            if ptr.kind == "derived":
                # A derived pointer's location is its parents; re-resolve those. Finding 12: a
                # derived pointer with NO cited parents, or a parent missing its own row id or
                # capture digest, used to be silently skipped — a caller who asked to re-resolve
                # it got a quiet, successful, EMPTY result instead of a signal that the request
                # could not be honoured.
                parents = list(ptr.derived_from or ())
                if not parents:
                    raise UnorderedPointeredRead(
                        f"{self.source_id}: a derived pointer offered for re-resolution cites no "
                        f"parents. A derived value with nothing to re-resolve is refused, not "
                        f"silently resolved to nothing")
                for parent in parents:
                    if parent.source_id != self.source_id:
                        raise UnorderedPointeredRead(
                            f"{self.source_id}: a derived pointer's parent cites source "
                            f"{parent.source_id!r}, not this adapter")
                    if not parent.row_id or not parent.capture_digest:
                        raise UnorderedPointeredRead(
                            f"{self.source_id}: a derived pointer's parent names no row id or no "
                            f"capture digest, so it addresses nothing this adapter can return to. "
                            f"Refused rather than silently dropped from the resolution")
                    self._claim_row(wanted, parent.row_id, parent.capture_digest, parent)
                continue
            if ptr.kind != "query_cell":
                raise UnorderedPointeredRead(
                    f"{self.source_id}: a pointer offered for re-resolution has kind "
                    f"{ptr.kind!r}; this Core re-resolves 'query_cell' and 'derived' pointers only")
            if not ptr.row_id or not ptr.capture_digest:
                raise UnorderedPointeredRead(
                    f"{self.source_id}: a pointer offered for re-resolution names no row id or no "
                    f"capture digest. At T3 the capture is where a citation resolves, so a "
                    f"pointer without one addresses nothing this adapter can return to")
            self._claim_row(wanted, ptr.row_id, ptr.capture_digest, ptr)

        sorted_ids = sorted(wanted)
        rows: list[Mapping[str, Any]] = []
        digests: dict[str, Capture] = {}
        for row_id in sorted_ids:
            capture_digest, _ptr = wanted[row_id]
            described = self._capture.describe(capture_digest)
            if described.source_id and described.source_id != self.source_id:
                # Finding 3, the other half: even a pointer that correctly names THIS source_id
                # could still cite a capture the CAPTURE STORE itself recorded as belonging to a
                # different source (a shared store, or a forged `capture_digest` that happens to
                # collide with someone else's capture). The capture's own metadata is the second,
                # independent witness.
                raise UnorderedPointeredRead(
                    f"{self.source_id}: capture {capture_digest[:19]}… was recorded for source "
                    f"{described.source_id!r}, not this adapter. Refused rather than parsed as "
                    f"this source's row — a capture is evidence for whoever produced it")
            frame = self._capture.get(capture_digest)
            digests[row_id] = described
            record = _find_row(_records_from_frame(frame, source_id=self.source_id),
                               decl.projection.key, row_id, source_id=self.source_id)
            if record is None:
                raise UnorderedPointeredRead(
                    f"{self.source_id}: capture {capture_digest[:19]}… does not contain row "
                    f"{row_id!r}. Refused rather than returned empty: an empty resolution reads as "
                    f"'this row holds nothing', which is a different fact from 'the citation and "
                    f"the capture disagree'")
            rows.append(record)

        schema_hash = projection_schema_hash(decl, req.projection.fields, self._accepted_digest)
        snap = SnapshotIdentity(
            source_id=self.source_id, as_of=self._now(),
            content_hash=rows_digest(rows, req.projection.fields, key),
            schema_hash=schema_hash, query_hash=req.read_hash)

        cells: list[tuple[Cell, ...]] = []
        withheld_rows = 0
        for ordinal, (row_id, record) in enumerate(zip(sorted_ids, rows)):
            # Finding 4: row-level authorization was evaluated in `resolve()` and never in THIS
            # method — a pointer addressing a row that policy would now deny was returned anyway,
            # subject only to column decisions. Re-resolution does not bypass row scope.
            denial = admission.decisions.row_verdict(
                record, attributes=admission.attribute_map, subquery_sets={})
            if denial is not None:
                withheld_rows += 1
                cells.append(self._emit_withheld_row(req, denial, admission))
                continue
            cells.append(self._emit_row(req, record, row_id, ordinal, snap, digests[row_id],
                                        admission))
        grounded = GroundedRows(columns=req.projection.fields, rows=tuple(cells), snapshot=snap)
        warnings = list(admission.notes) + [
            f"resolution mode: {len(rows)} pointer(s) re-resolved against the Capture Store; "
            f"the MCP server was NOT called, because at T3 the capture is the evidence and the "
            f"server can only say what it thinks today"]
        if withheld_rows:
            warnings.append(f"{withheld_rows} row(s) denied by a row-level rule and returned as "
                            f"`Withheld` cells; this resolution is scoped, not complete")
        return AdapterResult(
            read_hash=req.read_hash, rows=grounded,
            cost=Cost(rows=len(cells), wall_ms=int((time.perf_counter() - started) * 1000)),
            warnings=tuple(warnings))

    @staticmethod
    def _claim_row(wanted: dict[str, tuple[str, SourcePointer]], row_id: str,
                   capture_digest: str, ptr: SourcePointer) -> None:
        """Record which capture a row id must resolve against — REFUSING a conflicting second claim.

        Finding 12: `dict.setdefault` silently keeps the FIRST pointer's capture and drops every
        later one that names a different capture for the same row — a caller who offered two
        pointers at two different captures for one row got one of them back with no signal the
        other was discarded.
        """
        existing = wanted.get(row_id)
        if existing is not None and existing[0] != capture_digest:
            raise UnorderedPointeredRead(
                f"two pointers offered for re-resolution both address row {row_id!r} and name "
                f"DIFFERENT captures ({existing[0][:19]}… and {capture_digest[:19]}…). Refused "
                f"rather than silently resolved against whichever arrived first")
        wanted[row_id] = (capture_digest, ptr)

    # --- verb 5: verify ---------------------------------------------------------------
    def verify(self, pointer: SourcePointer, *, principal=None) -> Verdict:
        """Is this pointer still good? Returns the LATTICE, never a boolean.

        ``principal`` is keyword-only with a default so the port's signature is unchanged; it is
        here for the reason `ADAPTER_AGENT_SPEC` §7a gives — `FORBIDDEN` is a per-principal fact
        and is otherwise unreachable.
        """
        verdict, _note = self.verify_with_note(pointer, principal=principal)
        return verdict

    def verify_with_note(self, pointer: SourcePointer,
                         *, principal=None) -> tuple[Verdict, Optional[str]]:
        description = self.describe()
        verdict, note = self._verify(pointer, description, principal)
        return assert_verdict_advertised(verdict, description), note

    def _verify(self, pointer: SourcePointer, description: SourceDescription,
                principal) -> tuple[Verdict, Optional[str]]:
        if pointer.source_id != self.source_id:
            return Verdict.UNVERIFIABLE, (
                f"pointer cites source {pointer.source_id!r}; this adapter serves "
                f"{self.source_id!r} and has no handle to the other")

        if pointer.kind == "derived":
            parents = list(pointer.derived_from or ())
            if not parents:
                return Verdict.UNVERIFIABLE, (
                    "a derived pointer with no cited parents cannot be re-resolved")
            joined = combine_verdicts([self._verify(p, description, principal)[0]
                                       for p in parents])
            if joined is Verdict.VERIFIED_AS_OF_CAPTURE:
                return joined, ("derived value: every cited parent still matches its capture, so "
                                "the derivation still produces what it produced")
            return joined, (f"derived value: the worst verdict over {len(parents)} cited "
                            f"parent(s) is {joined.value}")

        if pointer.kind != "query_cell":
            return Verdict.UNVERIFIABLE, (
                f"this Core re-resolves captured tool-result cells; pointer kind "
                f"{pointer.kind!r} is not one")
        if not pointer.capture_digest:
            return Verdict.UNVERIFIABLE, (
                "the pointer carries no `capture_digest`. At T3 the capture IS the evidence: "
                "without it there is nothing to check the citation against, and re-calling the "
                "tool would only say what the server thinks TODAY")
        if not pointer.column or pointer.row_id is None:
            return Verdict.UNVERIFIABLE, (
                "the pointer does not name a column and a row id, so there is no cell to return to")

        if principal is not None:
            try:
                self._policy.binding_for(principal)
            except AdapterError:
                return Verdict.FORBIDDEN, ("the principal is no longer in the directory, so the "
                                           "read that produced this citation would be refused now")

        # --- half one: the capture. This is the half that can actually answer. ---
        try:
            frame = self._capture.get(pointer.capture_digest)
        except CaptureShredded as exc:
            return Verdict.VANISHED, (
                f"the capture this citation rests on was destroyed under an erasure request "
                f"({exc.tombstone.get('authority')}, {exc.tombstone.get('reason')}). The fact of "
                f"the capture survives; the bytes do not")
        except CaptureNotFound:
            return Verdict.UNVERIFIABLE, (
                f"capture {pointer.capture_digest[:19]}… is not in this store. The citation may be "
                f"good and this deployment cannot see the evidence, which is a different problem "
                f"from the citation being wrong")
        except CaptureCorrupt as exc:
            return Verdict.UNVERIFIABLE, (
                f"the stored capture no longer matches the digest that names it ({exc}). This is "
                f"an integrity failure in the evidence store and must be escalated by hand")

        cited_digest = pointer.snapshot.content_hash if pointer.snapshot else None
        if cited_digest is None:
            return Verdict.UNVERIFIABLE, "the pointer carries no cell digest to compare against"

        records = _records_from_frame(frame, source_id=self.source_id)
        captured = _find_row(records, self._declaration.projection.key, pointer.row_id)
        if captured is None or pointer.column not in captured:
            return Verdict.UNVERIFIABLE, (
                f"the capture {pointer.capture_digest[:19]}… does not contain "
                f"{pointer.row_id!r}.{pointer.column}. The citation and the capture it names "
                f"disagree — nothing DRIFTED, the pointer is wrong, which is an integrity failure "
                f"rather than a source change. The lattice has no state for this and inventing a "
                f"ninth verdict per adapter would be worse; escalate by hand")
        if cell_digest(captured[pointer.column]) != cited_digest:
            return Verdict.UNVERIFIABLE, (
                f"the capture holds a different value at {pointer.row_id!r}.{pointer.column} than "
                f"this citation vouches for. The capture is immutable, so this is not drift: the "
                f"pointer did not come from these bytes. Escalate by hand")

        # --- half two: the live tool, where the declaration says how to address one row. ---
        live_note = self._live_check(pointer, captured)
        if live_note is _DRIFTED:
            return Verdict.DRIFT_MATERIAL, (
                f"the capture still holds the cited value, and the live tool now returns a "
                f"different one for {pointer.row_id!r}.{pointer.column}. A determination in the "
                f"ledger rests on something the source no longer says")
        return Verdict.VERIFIED_AS_OF_CAPTURE, (
            f"matched capture {pointer.capture_digest[:19]}…. {live_note} This source is T3 and "
            f"cannot prove what it said at a past instant, so the capture is the authority and "
            f"VERIFIED is not a verdict it can reach")

    def _live_check(self, pointer: SourcePointer, captured: Mapping[str, Any]):
        """Re-call the tool for one row, where the pack declares how. Best effort, by design.

        A failure here is never a negative verdict: the capture has already answered the question
        the citation asks. What the live call adds is the ONE thing the capture cannot say — that
        the source still agrees — and that is worth having even when it is unavailable.
        """
        verification = self._declaration.verification
        if not verification.lookup_capability:
            return ("The pack declares no `verification.lookup_capability`, so the live source "
                    "was not consulted.")
        if self._quarantine is not None:
            return ("The live source was NOT consulted: this adapter is quarantined, and asking a "
                    "server whose declarations moved to adjudicate a citation would be asking the "
                    "thing under suspicion for its own alibi.")
        try:
            self.connect()
            _raw, message = self._transport.call_tool(
                verification.lookup_capability, {verification.lookup_argument: pointer.row_id})
            live = _find_row(_records_from_message(message, source_id=self.source_id),
                             self._declaration.projection.key, pointer.row_id,
                             source_id=self.source_id)
        except ServerQuarantined:
            # Finding 13. `connect()` re-checks the pin on every call, including this one, and a
            # NEWLY detected mismatch is exactly the event `verify()` must not suppress: the
            # module's own promise is that "every server-touching verb keeps raising until a named
            # human re-approves," and a live check IS server-touching. Catching this into a note
            # would let `verify()` return `VERIFIED_AS_OF_CAPTURE` for a source that was JUST
            # discovered to have rug-pulled — the one moment the quarantine is most worth seeing.
            raise
        except (AdapterError, OSError) as exc:
            return f"The live source was not reachable ({type(exc).__name__}); capture only."
        if live is None or pointer.column not in live:
            return ("The live tool no longer returns this row. That is NOT reported as VANISHED: "
                    "this source declares deletes_detectable=none, and a tool that stops "
                    "returning a row is not a tombstone.")
        if cell_digest(live[pointer.column]) != cell_digest(captured[pointer.column]):
            return _DRIFTED
        return "The live tool currently agrees."

    # =====================================================================================
    # internals
    # =====================================================================================

    def _admit(self, req: CapabilityRequest) -> Admission:
        """Every verb goes through Policy. There is no argument that bypasses this."""
        if req.source_id != self.source_id:
            raise PolicyNotApplied(
                f"request targets {req.source_id!r}; this adapter serves {self.source_id!r}")
        if req.capability not in self.capabilities:
            raise CapabilityNotAdvertised(
                f"{self.source_id!r} advertises {sorted(self.capabilities)}, not "
                f"{req.capability!r}. The rejection is final and is never fed back to a model to "
                f"reconsider")
        if req.capability_version is not None:
            raise CapabilityNotAdvertised(
                f"capability_version={req.capability_version!r} is refused. For an MCP source the "
                f"revision of a named query is not a string a caller supplies — it is the PINNED "
                f"DECLARATION DIGEST, checked on every connect, which quarantines on change and "
                f"lands in `schema_hash`. A caller-supplied version here would let the caller "
                f"assert which revision it read")
        if req.projection.entity != self._declaration.projection.entity:
            raise CapabilityNotAdvertised(
                f"{self.source_id!r} projects entity "
                f"{self._declaration.projection.entity!r} and the request names "
                f"{req.projection.entity!r}. The projection is what may be read AT ALL")
        outside = sorted(set(req.projection.fields) - set(self._declaration.projection.fields))
        if outside:
            raise CapabilityNotAdvertised(
                f"{self.source_id!r}: field(s) {outside} are outside the declared projection "
                f"{list(self._declaration.projection.fields)}. A field the pack did not declare "
                f"has no trust class, no type and no review behind it")
        return self._policy.admit(req, description=self.describe())

    def _require_total_order(self, req: CapabilityRequest) -> tuple[str, ...]:
        """The DECLARED key, checked against the ordering the request states.

        There is no reflection here to check the caller's claim against, so the pack's declaration
        is the authority — which is exactly why `projection.key` is mandatory in the declaration
        and why an MCP result's position in the response is never a citation.
        """
        key = tuple(self._declaration.projection.key)
        if req.resolve and not req.order_by:
            return key
        declared = tuple(req.projection.key)
        if declared != key:
            raise UnorderedPointeredRead(
                f"the request declares key {declared or '()'} and the adapter declaration names "
                f"{key}. A pointer cites the key, so a request naming a different one would "
                f"produce citations that address nothing")
        tail = tuple(t.lstrip("-") for t in req.order_by)[-len(key):]
        if set(tail) != set(key):
            raise UnorderedPointeredRead(
                f"order_by {req.order_by} does not end with the declared key {key}: an MCP tool "
                f"makes no ordering promise at all, so tied rows have no defined position and "
                f"`row_ordinal` is not a citation")
        return key

    def _order(self, records: Sequence[Mapping[str, Any]],
               req: CapabilityRequest) -> list[Mapping[str, Any]]:
        """Sort locally. **The server's order is not evidence of anything.**

        A relational Core can push ORDER BY down and trust the engine. A tool cannot: nothing in
        MCP promises a stable order, and a `row_ordinal` derived from response position would move
        between two identical calls. So the ordering the request declared is applied here, over
        the whole result, before the limit.
        """
        rows = [dict(r) for r in records]
        for term in reversed(req.order_by or tuple(self._declaration.projection.key)):
            desc, name = term.startswith("-"), term.lstrip("-")
            rows.sort(key=lambda r, n=name: _sort_key(r.get(n)), reverse=desc)
        return rows

    # --- emission ---------------------------------------------------------------------
    def _emit_withheld_row(self, req: CapabilityRequest, denial: PolicyDecision,
                           admission: Admission) -> tuple[Cell, ...]:
        """A row-level denial, as `Withheld` CELLS — one per requested column, never an omission.

        Finding 5 (and finding 4's re-resolution echo of it): invariant 4 says a policy denial is
        `Withheld`, with a reason code, never a silently absent row. A row that just does not
        appear is indistinguishable from a row the source never returned, which is precisely the
        distinction the four-state cell vocabulary (`GroundedValue | Withheld | Absent |
        Transformed`) exists to preserve.
        """
        entity = self._declaration.projection.entity
        return tuple(
            Withheld(record=denial.to_record(
                field_path=f"{entity}.{column}", principal=req.principal,
                policy_version=admission.decisions.policy_version))
            for column in req.projection.fields)

    def _emit_row(self, req: CapabilityRequest, record: Mapping[str, Any], row_id: str,
                  ordinal: int, snap: SnapshotIdentity, capture: Capture,
                  admission: Admission) -> tuple[Cell, ...]:
        entity = self._declaration.projection.entity
        cells: list[Cell] = []
        for column in req.projection.fields:
            decision = admission.decisions.decide(entity, column, record, req.principal)
            if decision.withheld:
                cells.append(Withheld(record=decision.to_record(
                    field_path=f"{entity}.{column}", principal=req.principal,
                    policy_version=admission.decisions.policy_version)))
                continue
            if column not in record:
                # The tool did not return the field. "Not in the response" and "not permitted to
                # you" are different facts (D4), and only one of them carries a policy id.
                cells.append(Absent(field_path=f"{entity}.{column}"))
                continue
            raw = record[column]
            if raw is None:
                cells.append(Absent(field_path=f"{entity}.{column}"))
                continue
            if not isinstance(raw, (str, int, float, bool)):
                raise HostileResponse(
                    f"{self.source_id}: {entity}.{column} for row {row_id!r} came back as "
                    f"{type(raw).__name__}, and the declaration types it as "
                    f"{self._declaration.field_types.get(column)!r}. A nested object where a "
                    f"scalar was declared is refused, not flattened: shape confusion is how a "
                    f"hostile response gets a value past a projection, and a cell that is not a "
                    f"scalar has no content address")
            cell = self._emit_cell(req, entity, column, raw, row_id, ordinal, snap, capture)
            # §4a — THE WALL, at emission, against the record parsed out of the CAPTURE.
            assert_cell_grounded_in_row(cell, record)
            cells.append(cell)
            self._register_taint(cell)
        return tuple(cells)

    def _emit_cell(self, req: CapabilityRequest, entity: str, column: str, raw: Any,
                   row_id: str, ordinal: int, snap: SnapshotIdentity,
                   capture: Capture) -> GroundedValue:
        decl = self._declaration
        raw_ptr = SourcePointer(
            source_id=self.source_id,
            kind="query_cell",
            # Per CELL, not per read: §7a's first correction. A read-level digest on every pointer
            # makes every pointer report material drift when anything anywhere in the read moves.
            snapshot=SnapshotIdentity(
                source_id=self.source_id, as_of=snap.as_of, content_hash=cell_digest(raw),
                schema_hash=snap.schema_hash, query_hash=snap.query_hash),
            query_hash=req.read_hash,
            row_id=row_id,
            row_ordinal=ordinal,                     # a hint; the response order is not identity
            column=column,
            table=entity,
            # THE T3 BINDING. Without this the capture store is decoration: nobody could prove
            # WHICH bytes were parsed, and a citation into a tool call would resolve against
            # whatever the server feels like saying today.
            capture_digest=capture.qualified_digest,
            # Stamped from the DECLARATION. Never from the content — letting the value decide its
            # own label is letting the thing being contained decide whether it is contained.
            trust=decl.trust_of(column),
        )
        classes = decl.redaction_for(column)
        if not classes:
            return GroundedValue(value=raw, pointer=raw_ptr, confidence=1.0)

        produced = _mcp_redact_v1([raw], {"column": column, "classes": "|".join(classes)})
        if produced == raw:
            # The rule was a no-op on this value. Emitting a `derived` pointer anyway would claim
            # a transformation that did not happen and cost the cell its locator for nothing.
            return GroundedValue(value=raw, pointer=raw_ptr, confidence=1.0)
        derived_ptr = SourcePointer(
            source_id=self.source_id,
            kind="derived",
            derivation=decl.derivation_for(column),
            derived_from=[raw_ptr],
            trust=raw_ptr.trust,                     # LUB — redaction does not launder trust
            capture_digest=capture.qualified_digest,
        )
        assert_derived_from(derived_ptr, raw_ptr)
        return GroundedValue(value=produced, pointer=derived_ptr, confidence=1.0)

    def _register_taint(self, cell: GroundedValue) -> None:
        """Register every untrusted value with the engine's `TaintMap` as it is unwrapped.

        Without this registration `screen_sinks` is a naming convention: the map only knows values
        it has been shown. Read the honesty section in `taint.py` before quoting this — taint does
        not survive a string operation, so this raises the cost of the DIRECT flow (read an
        injected note, drop it into a filter) and does not close the indirect one.
        """
        taint = getattr(self._policy, "taint", None)
        if taint is None or not hasattr(taint, "register"):      # pragma: no cover - custom map
            return
        try:
            taint.register(cell.value, cell.trust)
        except TypeError:                                        # pragma: no cover - unhashable
            pass

    # --- lifecycle --------------------------------------------------------------------
    def close(self) -> None:
        self._transport.close()


#: Sentinel returned by `_live_check` when the live tool disagrees with the capture.
_DRIFTED = object()

#: The honest cursor declaration for a tool surface: there is no watermark of any kind, and saying
#: so is a planning input. `kind: "none"` is a DECLARATION; omission would not be.
_NO_CURSOR = CursorDescription(kind="none", field=None, lossy=True, replay="none")


# =======================================================================================
# response parsing
# =======================================================================================


def _records_from_frame(frame: bytes, *, source_id: str) -> list[Mapping[str, Any]]:
    """Parse a captured JSON-RPC frame into records. Everything here fails closed."""
    try:
        message = json.loads(frame.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HostileResponse(
            f"{source_id}: the captured response frame is not JSON ({exc}). A capture that cannot "
            f"be parsed cannot ground a citation") from exc
    if not isinstance(message, Mapping):
        raise HostileResponse(f"{source_id}: the captured frame is not a JSON object")
    return _records_from_message(message, source_id=source_id)


def _records_from_message(message: Mapping[str, Any], *, source_id: str
                          ) -> list[Mapping[str, Any]]:
    result = message.get("result")
    if not isinstance(result, Mapping):
        raise HostileResponse(f"{source_id}: the response carries no `result` object")
    if result.get("isError"):
        raise McpToolError(
            f"{source_id}: the MCP server reported an execution error. Its text is deliberately "
            f"NOT propagated: error paths triple prompt-injection success and reach up to 100% "
            f"compliance on frontier models (arXiv 2606.07992), so a server's error string never "
            f"becomes something a model reads")
    records = None
    structured = result.get("structuredContent")
    if isinstance(structured, Mapping):
        records = structured.get("records")
    if records is None:
        for block in (result.get("content") or ()):
            if isinstance(block, Mapping) and block.get("type") == "text":
                try:
                    parsed = json.loads(str(block.get("text") or ""))
                except json.JSONDecodeError as exc:
                    raise HostileResponse(
                        f"{source_id}: the tool's text content is not JSON ({exc})") from exc
                if isinstance(parsed, Mapping):
                    records = parsed.get("records")
                break
    if not isinstance(records, list):
        raise HostileResponse(
            f"{source_id}: the tool result carries no `records` list. Refused rather than "
            f"interpreted: guessing which part of an arbitrary JSON blob is 'the rows' is how a "
            f"hostile server chooses what a projection means")
    out = []
    for item in records:
        if not isinstance(item, Mapping):
            raise HostileResponse(f"{source_id}: a record is not an object")
        out.append(dict(item))
    return out


def _escape_row_id_part(text: str) -> str:
    """Backslash-escape `\\` and `|` so a composite row identity has no delimiter to collide on."""
    return text.replace("\\", "\\\\").replace("|", "\\|")


def _row_id(record: Mapping[str, Any], key: Sequence[str]) -> Optional[str]:
    """The row's identity under `key`, or `None` when a key field is missing or null.

    Finding 7: `"|".join(str(record.get(k)) for k in key)` has three problems at once — a missing
    key silently becomes the literal string `"None"`; `("a|b", "c")` and `("a", "b|c")` collide on
    the join delimiter; and `1` (int) and `"1"` (str) become the same string. This closes all
    three EXCEPT for a single-field key, which stays the bare `str(value)` on purpose: it is the
    only shape any existing pointer's `row_id` is compared against by identity elsewhere in this
    codebase (fixtures cite it directly), and a single field has no delimiter to collide on and no
    sibling field to be confused with — the ambiguity this function guards against is specifically
    the MULTI-field case.
    """
    values = [record.get(k) for k in key]
    if any(v is None for v in values):
        return None
    if len(key) == 1:
        return str(values[0])
    return "|".join(f"{type(v).__name__}:{_escape_row_id_part(str(v))}" for v in values)


def _require_row_ids(records: Sequence[Mapping[str, Any]], key: Sequence[str], *,
                     source_id: str) -> list[str]:
    """Every record's row identity, refusing before a single cell is emitted if any is unusable.

    Two of finding 7's triggers, closed here rather than per-cell: a record missing a key field
    (which `_row_id` alone would just return `None` for — not enough, an emitter needs a reason to
    refuse) and two records sharing one identity (`_find_row` and a later re-resolution would then
    return the FIRST match for either, so a citation minted against the second row silently
    resolves to the first one's bytes).
    """
    ids: list[str] = []
    seen: dict[str, int] = {}
    for idx, record in enumerate(records):
        row_id = _row_id(record, key)
        if row_id is None:
            raise HostileResponse(
                f"{source_id}: row {idx} has no value for key field(s) {list(key)}. A row with no "
                f"stable identity cannot be cited and is refused before a single cell is emitted, "
                f"rather than silently addressed under the literal string 'None'")
        if row_id in seen:
            raise HostileResponse(
                f"{source_id}: rows {seen[row_id]} and {idx} both declare identity {row_id!r} "
                f"under key {list(key)}. Two records sharing one declared key cannot both be "
                f"cited under distinct pointers — refused before a single cell is emitted, rather "
                f"than silently letting a later citation resolve to the first row's bytes")
        seen[row_id] = idx
        ids.append(row_id)
    return ids


def _find_row(records: Sequence[Mapping[str, Any]], key: Sequence[str],
              row_id: str, *, source_id: str = "") -> Optional[Mapping[str, Any]]:
    """The one record addressed by `row_id`, or `None`. Refuses if more than one record matches.

    The second half of finding 7: a capture that happens to hold two records under the same
    declared key would otherwise resolve to whichever came first in the list — silently, and
    differently depending on iteration order. A re-resolution or a `verify()` that could land on
    either of two rows addresses neither reliably, so this raises rather than picks.
    """
    match: Optional[Mapping[str, Any]] = None
    for record in records:
        if _row_id(record, key) == row_id:
            if match is not None:
                raise HostileResponse(
                    f"{source_id}: the capture holds more than one row addressed by identity "
                    f"{row_id!r} under key {list(key)}. Refused rather than resolved to the "
                    f"first match")
            match = record
    return match


def _sort_key(value: Any) -> tuple:
    """Total, deterministic, and EXACT across the JSON scalars a tool can return.

    Finding 15: formatting every number through `float(value)` loses precision for integers
    beyond 2**53 and for anything IEEE-754 cannot represent exactly, so two DISTINCT values can
    format to the identical string and collide in the sort. `Fraction` represents an `int`
    exactly and represents a `float` as the exact value of its binary representation — comparing
    Fractions is therefore an exact, lossless numeric order, never an approximation of one.
    `NaN`/`inf` cannot become a `Fraction` at all, so they get their own fixed buckets rather than
    raising mid-sort.
    """
    if value is None:
        return (0, 0, 0)
    if isinstance(value, bool):
        return (1, 1 if value else 0, 0)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return (2, 2, 0)                              # NaN: fixed position, after +inf
        if isinstance(value, float) and math.isinf(value):
            return (2, 1 if value > 0 else -1, 0)
        return (2, 0, Fraction(value))                    # exact: no precision lost, ever
    return (3, 0, str(value))


# =======================================================================================
# the compiler
# =======================================================================================


def compile_mcp_adapter(declaration: McpAdapterDeclaration, *, policy: PolicyEngine,
                        capture: CaptureStore, transport: Optional[McpTransport] = None,
                        repo_root: Optional[Path] = None,
                        env: Optional[Mapping[str, str]] = None,
                        timeout: float = 20.0,
                        now: Optional[Callable[[], datetime]] = None) -> McpCore:
    """Compile a declaration into a configured adapter. **This is the Adapter Builder, in engine
    form** — AGENT_PLANE §5's right-hand box, before it has a UI.

    A default `StdioMcpTransport` is built from the declaration's vendored path when none is
    supplied, with a constructed environment. Passing a transport is how a deployment substitutes
    a launcher that provides real isolation; it is not a hook for a mock, and the fixture tests do
    not use it.
    """
    if transport is None:
        root = Path(repo_root) if repo_root else _repo_root()
        vendored = (root / declaration.mcp.vendored_path).resolve()
        if not _contained(vendored, root):
            # Finding 8's containment half, re-checked here too: a declaration built by hand
            # (`McpAdapterDeclaration.model_validate(...)`, skipping `load_mcp_declaration`
            # entirely) never went through the check above, and this is the OTHER place a path
            # turns into an actual launch.
            raise RegistryResolutionRefused(
                f"{declaration.source_id}: mcp.vendored_path {declaration.mcp.vendored_path!r} "
                f"resolves to {vendored}, which is OUTSIDE {root}. Refusing to launch a server "
                f"outside the repository that was reviewed and pinned")
        transport = StdioMcpTransport(
            command=[sys.executable, str(vendored)],
            cwd=vendored.parent,
            env={**dict(declaration.mcp.env), **dict(env or {})},
            timeout=timeout,
            protocol_version=declaration.mcp.protocol_version,
            # Finding 8's TOCTOU half: `load_mcp_declaration` (or a caller building the
            # declaration by hand) checked this digest once, and the process is not actually
            # spawned until the first RPC — which can be arbitrarily later. Passing the expected
            # digest and the path through means `StdioMcpTransport._start()` re-verifies
            # IMMEDIATELY before `Popen`, closing the window between the check and the use.
            expected_digest=declaration.mcp.digest, digest_path=vendored)
    return McpCore(declaration=declaration, transport=transport, policy=policy, capture=capture,
                   now=now)
