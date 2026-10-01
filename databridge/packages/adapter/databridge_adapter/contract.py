"""`CapabilityRequest` — the only thing an orchestrator may send an adapter (spine D2, §2).

One contract, all sources. Not natural language, not raw SQL, not per-source RPC: those are
unhashable, unverifiable and have no cache key, which is the same objection three times.

**The two hashes, because one hash leaks.**

| Hash | Covers | Used for |
|---|---|---|
| `read_hash` | source, capability, projection, predicate+params, order_by, as_of, limit, scheme_version | `SnapshotIdentity.query_hash` - pointer identity, re-execution, verification |
| `cache_key` | `read_hash` + purpose + entitlement hash + snapshot + pack version | serving a cached result |

`read_hash` is principal-INDEPENDENT *because a pointer must mean the same thing to everyone* - a
citation whose identity depended on who read it could not be verified by an auditor. Policy is
then applied on top, every time, on the way out.

`cache_key` is principal-SHAPED because the withheld set is. Two principals with the same purpose
but different grants are entitled to different rows and columns; sharing a cache entry between
them is a data-leak path, and it is the one an earlier draft of the spec had.

The two are separate methods rather than one parameterised one so that neither can be reached by
passing the wrong argument to the other.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Iterable, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from databridge_core import SnapshotIdentity, SourcePointer

from .canonical import (
    SCHEME_VERSION,
    Scalar,
    canonical_ast,
    canonical_json,
    canonical_pointer,
    iso_utc,
    sha256_of,
)
from .errors import UnorderedPointeredRead
from .purposes import PurposeDecl, active_purposes

#: Identifiers the contract will carry: entity names, field names, parameter names, capabilities.
#: Closed to lowercase snake_case with optional dotted segments, for the same reason
#: `databridge_graph.packs` closes kind names - these become parts of a hash and of a compiled
#: query, and case ambiguity in either is a defect generator.
_IDENT = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


def _check_ident(value: str, what: str) -> str:
    if not isinstance(value, str) or not _IDENT.match(value):
        raise ValueError(
            f"{what} {value!r} must be lowercase snake_case, optionally dotted "
            f"(e.g. 'party', 'core_banking.party') - it becomes part of a pointer identity")
    return value


class Principal(BaseModel):
    """Who is asking, on whose behalf, and WHY (spine D5).

    ``purpose`` is a field of the PRINCIPAL, not of the request, and it is validated against the
    enabled packs' declared vocabulary (`purposes.py`) rather than against a literal in engine
    code. Two consequences worth stating:

    * A purpose that can be omitted is a purpose that will be omitted. There is no default.
    * ``on_behalf_of`` has no default either. ``None`` means *no human asked* - a scheduled
      build-time crawl - and that is a decision, not an oversight, so the caller states it.

    ``grant_id`` is required exactly when the pack declares the purpose subject-scoped. A
    subject-scoped read without a grant is refused rather than defaulted
    (`ADAPTER_AGENT_SPEC` §1). And a grant is emphatically NOT identity verification: the grant is
    minted after a named human attests to a candidate in person; nothing here confirms anyone.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: str                          # service identity, e.g. "orchestrator@walnut"
    on_behalf_of: Optional[str]         # the human, if any; None for scheduled build-time crawls
    purpose: str                        # closed vocabulary, declared per pack
    grant_id: Optional[str] = None      # the pack's subject-read grant, when subject-scoped

    @model_validator(mode="after")
    def _check_purpose_is_pack_declared(self) -> "Principal":
        if not self.actor.strip():
            raise ValueError("actor must name a service identity: an unattributable read is not "
                             "an auditable one")
        # `None` means "no human asked". A BLANK string means "a human asked and we did not record
        # who", which is a different and much worse fact, and it must not be spellable as the
        # first. Constructing a Principal must never imply that a named human was involved - and
        # nothing here, including a non-blank name, is an identity verification: the grant is
        # minted after a human attests in person, and `requires_human_selection` stays
        # `Literal[True]` wherever identity is actually decided.
        if self.on_behalf_of is not None and not self.on_behalf_of.strip():
            raise ValueError(
                "on_behalf_of must be None (no human asked - a scheduled build-time crawl) or a "
                "non-blank human identifier. A blank string records that a human asked and that "
                "nobody wrote down who, which no audit can act on")
        decl: PurposeDecl = active_purposes().require(self.purpose)
        if decl.subject_scoped and not (self.grant_id or "").strip():
            raise ValueError(
                f"purpose {self.purpose!r} is declared subject_scoped by pack {decl.pack!r}, so "
                f"grant_id is required. A subject-scoped read without a grant is refused, never "
                f"defaulted (ADAPTER_AGENT_SPEC §1)")
        # Checked AFTER the subject-scoped rule, deliberately: for a subject-scoped purpose a
        # blank grant is the more specific failure and deserves the more specific message. This
        # catches the remaining case - a blank grant carried by a purpose that needs none, which
        # is a caller that thinks it has a grant and does not.
        if self.grant_id is not None and not self.grant_id.strip():
            raise ValueError("grant_id must be None or a non-blank grant identifier")
        return self

    @property
    def is_subject_scoped(self) -> bool:
        return active_purposes().require(self.purpose).subject_scoped


class Entitlement(BaseModel):
    """What this principal is ENTITLED to, hashed into the cache key (§2a).

    Distinct from `Principal` because entitlement changes without the principal changing: a grant
    is revoked, a role is removed, the policy bundle is redeployed. Each of those changes which
    rows and columns come back, so each must change the cache key - otherwise a cached result
    outlives the permission that produced it.

    ``policy_bundle_version`` is mandatory and non-blank. A blank one would collapse every
    deployment of the policy bundle into a single cache generation, which is the exact failure
    "a wrong answer with a citation" describes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    grant_id: Optional[str] = None
    roles: tuple[str, ...] = ()
    policy_bundle_version: str

    @field_validator("roles")
    @classmethod
    def _sorted_unique(cls, roles: tuple[str, ...]) -> tuple[str, ...]:
        # Sorted so that role ORDER cannot fork the cache; deduplicated so a repeated role cannot.
        return tuple(sorted(set(roles)))

    @model_validator(mode="after")
    def _check_bundle_version(self) -> "Entitlement":
        if not self.policy_bundle_version.strip():
            raise ValueError(
                "policy_bundle_version must be non-blank: without it every redeployment of the "
                "policy bundle shares one cache generation, and a cached result outlives the "
                "permission that produced it")
        return self

    @property
    def hash(self) -> str:
        return sha256_of(canonical_json({
            "grant_id": self.grant_id,
            "roles": list(self.roles),
            "policy_bundle_version": self.policy_bundle_version,
        }))


class Projection(BaseModel):
    """The fields actually read. This - not the whole schema - is what gets hashed.

    R02's finding, and the reason `DRIFT_BENIGN` exists at all: hashing the full source schema
    produces false-drift storms. An unrelated column added to a 200-column table invalidates every
    pointer into it, the signal becomes noise, and within a week someone turns verification off.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity: str                         # table / label / folder - the source's unit of collection
    fields: tuple[str, ...]             # ORDERED; participates in the read hash

    #: The entity's STABLE ROW IDENTITY, as the source contract declares it (`describe()` fills it
    #: from reflection). Empty means "not declared", which the contract permits and every Core
    #: that mints ordinals refuses - see `CapabilityRequest` on why a total order needs it.
    #:
    #: It is part of the projection, and therefore part of `read_hash`, because it is part of what
    #: was read: the key columns are selected so a pointer can cite them.
    key: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_fields(self) -> "Projection":
        _check_ident(self.entity, "projection entity")
        for k in self.key:
            _check_ident(k, "projection key column")
        if len(set(self.key)) != len(self.key):
            raise ValueError(f"duplicate column in projection key {self.key}")
        missing_key = [k for k in self.key if k not in self.fields]
        if missing_key:
            raise ValueError(
                f"projection key names {missing_key}, which is not projected. A pointer cites the "
                f"key, so a key column that was not read cannot be cited - and an ordering that "
                f"ends in a column the projection-only schema hash cannot see is not verifiable")
        if not self.fields:
            raise ValueError(
                "a projection must name at least one field: an empty projection has nothing to "
                "hash, so nothing it returned could be verified")
        seen: set[str] = set()
        for f in self.fields:
            _check_ident(f, "projection field")
            if f in seen:
                raise ValueError(
                    f"duplicate projection field {f!r}: two cells would carry one column name "
                    f"and a pointer could not say which was cited")
            seen.add(f)
        return self

    def path(self, field: str) -> str:
        """The dotted path a `WithholdingRecord` / `Absent` cell names for ``field``."""
        return f"{self.entity}.{field}"


class CapabilityRequest(BaseModel):
    """Frozen, hashable, and the ONLY orchestrator to adapter message (spine D2).

    Deviations from `ADAPTER_AGENT_SPEC` §2's sketch, each for a reason the spec itself gives:

    * **``params`` is a tuple of pairs, not a `Mapping`.** §2 asks for frozen AND hashable; a dict
      field is neither. Pass a mapping in - it is normalised - and read `param_map` back out.
    * **``order_by`` is required for a discovery read**, not merely documented as required. §7
      raises `UnorderedPointeredRead` inside the Postgres Core; every read through this port is
      pointered, so the requirement belongs to the contract rather than to each adapter
      remembering it. A resolve-only request is exempt: it re-fetches identified pointers and
      never mints an ordinal.
    * **``__hash__`` is defined explicitly.** Appendix A already notes that "frozen and hashable"
      is overstated for `SourcePointer`, which holds `facts`/`derived_from` LISTS. Hashing a
      request that carries such pointers would raise. Identity here is the digest, which is the
      thing that actually means "the same request".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    capability: str                     # from the adapter's advertised closed set
    principal: Principal
    projection: Projection
    predicate: Optional[str] = None     # portable filter expression, NOT raw SQL (§6)
    params: tuple[tuple[str, Scalar], ...] = ()     # bound separately, never inlined
    order_by: tuple[str, ...] = ()      # REQUIRED for a discovery read (§7)
    as_of: Optional[datetime] = None
    limit: int = Field(default=1000, ge=1, le=100_000)
    mode: Literal["build", "query"] = "query"

    #: Resolution mode: re-fetch pointers that already exist rather than discovering new rows.
    resolve: tuple[SourcePointer, ...] = ()
    scheme_version: int = SCHEME_VERSION

    #: Version of the PACK-DECLARED NAMED QUERY this `capability` names, when it names one.
    #:
    #: §6's instruction until the predicate grammar exists is "a fixed set of pack-declared named
    #: queries". A named query's BODY can change under the same name - a different join, a
    #: different filter - and that changes what was read without changing anything else in the
    #: request. Without this field the pointer identity would not move, and every stored citation
    #: into that query would silently start meaning something else.
    #:
    #: ``None`` means the capability is a structural verb whose meaning is fixed by
    #: `scheme_version` (a whole-projection ordered read). A Core that advertises named queries
    #: refuses ``None``; a Core that advertises only structural verbs refuses a non-``None``.
    capability_version: Optional[str] = None

    # Identity is computed ONCE, in the validator, and cached here. See `_check_request`.
    _read_hash: str = PrivateAttr(default="")
    _resolve_digest: str = PrivateAttr(default="")

    # --- normalisation ---
    @field_validator("params", mode="before")
    @classmethod
    def _params_from_mapping(cls, value: Any) -> Any:
        """Accept a mapping (the ergonomic form) and store sorted pairs (the hashable one)."""
        if isinstance(value, Mapping):
            return tuple(sorted(value.items(), key=lambda kv: kv[0]))
        if isinstance(value, (list, tuple)):
            return tuple(sorted((tuple(item) for item in value), key=lambda kv: kv[0]))
        return value

    @field_validator("as_of")
    @classmethod
    def _as_of_is_utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        iso_utc(value)                  # raises on a naive instant, with the reason
        return value.astimezone(timezone.utc)

    # --- invariants ---
    @model_validator(mode="after")
    def _check_request(self) -> "CapabilityRequest":
        # `source_id` selects WHICH ADAPTER runs, and therefore which credentials and which
        # egress class. It is closed to the same identifier shape as everything else that steers a
        # read: a source id that could be an arbitrary string is a control-flow position an
        # attacker-writable value can be spelled into, and the taint screen would then be the ONLY
        # thing standing in front of it (R-3A finding 7).
        _check_ident(self.source_id, "source_id")
        _check_ident(self.capability, "capability")

        if self.scheme_version != SCHEME_VERSION:
            raise ValueError(
                f"scheme_version {self.scheme_version} is not the canonicalisation scheme this "
                f"build implements ({SCHEME_VERSION}). Failing closed: re-deriving a pointer "
                f"identity under a scheme the request did not mean is how stored pointers stop "
                f"verifying silently (§2a)")

        # Refused at CONSTRUCTION, not lazily when `read_hash` is first read. A request that can
        # be built, logged and passed around before anyone discovers it is unusable is a request
        # that will reach a compiler through some path that never asks for its hash.
        #
        # v1 passed `()` here rather than `self.params`, which left the params unvalidated until
        # somebody asked for a hash - so a NaN float or a non-finite Decimal survived construction
        # and blew up somewhere else entirely. Passing the real params is the rule the docstring
        # already claimed.
        canonical_ast(self.predicate, self.params)

        if self.capability_version is not None and not self.capability_version.strip():
            raise ValueError(
                "capability_version must be None or a non-blank version: a blank one collapses "
                "every revision of a named query into one pointer identity")

        seen: set[str] = set()
        for name, _ in self.params:
            _check_ident(name, "parameter name")
            if name in seen:
                raise ValueError(f"duplicate parameter binding {name!r}")
            seen.add(name)

        projected = set(self.projection.fields)
        ordered: list[str] = []
        for term in self.order_by:
            field = term[1:] if term.startswith("-") else term
            _check_ident(field, "order_by field")
            if field not in projected:
                raise ValueError(
                    f"order_by names {field!r}, which is not in the projection. You cannot order "
                    f"by what you did not read: the ordering would depend on a column whose "
                    f"drift the projection-only schema hash cannot see (D6)")
            if field in ordered:
                raise ValueError(
                    f"order_by names {field!r} twice. A repeated term is either redundant or "
                    f"contradictory (`('a', '-a')`), and in both cases the ordering it describes "
                    f"is not the one the caller means")
            ordered.append(field)

        # **A non-empty `order_by` is not a total order, and only a total order is a citation.**
        #
        # `order_by=("status",)` leaves every tied row free to come back in any position, so
        # `row_ordinal` still moves under a plan change, a pagination boundary or a parallel scan
        # - which is the exact instability the requirement was written to remove. Totality needs
        # the ordering to END in a unique key.
        #
        # The contract cannot know the key by itself, so it does not guess: it enforces the rule
        # against the key the PROJECTION declares (filled from `describe()`, i.e. from reflection,
        # not from a caller's assertion), and permits an undeclared key so that a source with no
        # relational identity is still expressible. Every Core that mints an ordinal then refuses
        # an undeclared or mismatched key against its own reflected primary key - which is where
        # the claim can actually be checked rather than believed.
        if self.order_by and self.projection.key:
            tail = ordered[-len(self.projection.key):]
            if set(tail) != set(self.projection.key):
                raise ValueError(
                    f"order_by {self.order_by} is not a TOTAL order: it must end with the "
                    f"projection's declared key {self.projection.key} so that tied rows have a "
                    f"defined position. An ordinal over a partial order moves under pagination, "
                    f"plan changes and ties, and a citation that moves is not a citation (§7)")

        for ptr in self.resolve:
            if ptr.source_id != self.source_id:
                raise ValueError(
                    f"resolve carries a pointer into source {ptr.source_id!r} but the request "
                    f"targets {self.source_id!r}: one request reaches one adapter, and a pointer "
                    f"for another source has no path to be re-resolved by this one")

        # Raised as an AdapterError, NOT a ValueError, so it survives Pydantic with its type
        # intact - the caller's response to "add order_by" is specific (see errors.py).
        if not self.resolve and not self.order_by:
            raise UnorderedPointeredRead(
                "a pointered read requires `order_by`: `row_ordinal` over an unordered result is "
                "not a stable citation - it moves under pagination, plan changes and ties (§7). "
                "A resolve-only request is exempt because it mints no ordinals.")

        # **Freeze the identity HERE, at construction.**
        #
        # `frozen=True` freezes the FIELDS; it does not deep-freeze what they point at, and
        # `SourcePointer` holds `facts`/`derived_from` as lists. A caller could hash this request,
        # put it in a dict, then append a parent to one of those pointers - and the request's
        # `read_hash`, and therefore `__hash__`, would change after the fact. A pointer identity
        # that moves after it has been issued is not an identity, and a key that moves after
        # insertion corrupts the dict silently.
        #
        # So the deep canonical form of the pointer set is taken once, now, and the two digests
        # are computed from it and cached. Later mutation of a pointer is then invisible to this
        # request, which is the correct semantics: the request means what it meant when it was
        # made.
        object.__setattr__(self, "_resolve_digest", sha256_of(canonical_json(
            sorted(sha256_of(canonical_json(canonical_pointer(p))) for p in self.resolve))))
        object.__setattr__(self, "_read_hash", self._compute_read_hash())
        return self

    # --- reading back ---
    @property
    def param_map(self) -> Mapping[str, Scalar]:
        """The bound parameters as a read-only mapping. A view over a copy; never the interior."""
        return MappingProxyType(dict(self.params))

    # --- the two hashes ---
    def _compute_read_hash(self) -> str:
        payload = {
            "scheme_version": self.scheme_version,
            "source_id": self.source_id,
            "capability": self.capability,
            "capability_version": self.capability_version,
            "entity": self.projection.entity,
            "fields": list(self.projection.fields),
            "key": list(self.projection.key),
            "order_by": list(self.order_by),
            "as_of": iso_utc(self.as_of),
            "limit": self.limit,
            "predicate_ast": canonical_ast(self.predicate, self.params),
            "resolve_digest": self._resolve_digest,
        }
        return sha256_of(canonical_json(payload))

    @property
    def read_hash(self) -> str:
        """Identity of the READ - what was asked of the source. Principal-independent.

        Computed once at construction and cached, so that mutating a `SourcePointer` in `resolve`
        afterwards cannot change an identity that has already been issued (see `_check_request`).

        Excludes `principal` (a citation must mean the same thing to every reader) and `mode`
        (build vs query is scheduling, not identity - and see the class docstring for why the
        correct fix for "an adapter could branch on mode" is to forbid the branch, not to fork
        every pointer identity by schedule). INCLUDES the pointer set being resolved, digested,
        because two resolution requests for different pointers are different reads and must not
        collide; and INCLUDES `capability_version`, because a pack-declared named query whose
        body changes under the same name is a different read.

        This value BECOMES `SnapshotIdentity.query_hash`.
        """
        return self._read_hash

    def cache_key(self, *, entitlement: Entitlement, snapshot: SnapshotIdentity,
                  pack_version: str) -> str:
        """Key for serving a CACHED result (§2a). Principal-shaped, deliberately.

        ``snapshot`` and ``pack_version`` are in the tuple because a cached answer whose underlying
        pointer has moved is not a cache hit - it is a wrong answer with a citation, which is the
        single failure mode this system exists to prevent. R10's FinCacheServe validates exactly
        this guard tuple at 53% call-skip with zero stale outputs.

        **The guard tuple has to be COHERENT, not merely present** (review R-3A finding 3). v1
        took the entitlement and the snapshot on trust, and that reopened the leak the two-hash
        split closed, one level up:

        * an entitlement carrying a different ``grant_id`` than the principal, or ``None`` against
          a subject-scoped principal, produced a key that two principals could share;
        * ``actor``/``on_behalf_of`` were absent from the payload, so sharing was safe only if
          policy output could be PROVEN to depend on nothing else - and nothing proved it;
        * a snapshot of an unrelated source, or of a different read, was accepted into a
          valid-looking key.

        So each of those is now checked rather than assumed, and the principal's own identity is
        in the payload. The cost is a coarser cache; the alternative is a cache that occasionally
        serves one principal's rows to another, which is not a cache, it is an incident.
        """
        if not pack_version.strip():
            raise ValueError(
                "pack_version must be non-blank: the domain pack decides what a field MEANS, so "
                "two pack versions are two different answers to the same read")
        if entitlement.grant_id != self.principal.grant_id:
            raise ValueError(
                f"entitlement grant {entitlement.grant_id!r} is not the principal's grant "
                f"{self.principal.grant_id!r}. The entitlement is what SHAPES the withheld set, "
                f"so an entitlement that does not belong to this principal produces a key two "
                f"principals can share - which is the data-leak path the two-hash split exists "
                f"to close")
        if snapshot.source_id != self.source_id:
            raise ValueError(
                f"snapshot cites source {snapshot.source_id!r}, request targets "
                f"{self.source_id!r}: a cache entry guarded by an unrelated source's snapshot is "
                f"guarded by nothing")
        if snapshot.query_hash != self.read_hash:
            raise ValueError(
                f"snapshot.query_hash {snapshot.query_hash!r} is not this request's read_hash "
                f"{self.read_hash!r}. read_hash BECOMES query_hash (D2); a key built from another "
                f"read's snapshot would survive that read's data moving")
        payload = {
            "read_hash": self.read_hash,
            "actor": self.principal.actor,
            "on_behalf_of": self.principal.on_behalf_of,
            "purpose": self.principal.purpose,
            "entitlement_hash": entitlement.hash,
            "snapshot": {
                "source_id": snapshot.source_id,
                "as_of": iso_utc(snapshot.as_of),
                "content_hash": snapshot.content_hash,
                "schema_hash": snapshot.schema_hash,
                "query_hash": snapshot.query_hash,
            },
            "pack_version": pack_version,
        }
        return sha256_of(canonical_json(payload))

    # Identity IS the digest - see the class docstring on why the generated hash cannot be used.
    def __hash__(self) -> int:
        return hash((self.read_hash, self.principal.actor, self.principal.on_behalf_of,
                     self.principal.purpose, self.principal.grant_id, self.mode))


def bound_params(mapping: Mapping[str, Scalar]) -> tuple[tuple[str, Scalar], ...]:
    """Helper for callers that build ``params`` before they build the request."""
    return tuple(sorted(mapping.items(), key=lambda kv: kv[0]))


def iter_param_values(params: Iterable[tuple[str, Scalar]]) -> Iterable[Scalar]:
    for _, value in params:
        yield value
