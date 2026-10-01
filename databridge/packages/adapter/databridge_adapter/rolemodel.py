"""The estate's ROLE MODEL, read from `roles.yaml` — deterministic, no LLM, no I/O but the file.

`fixtures/packs/finance/roles.yaml` is the policy input, and it is written the way spine D5 wants
access described: the principal is `(who, on-behalf-of, PURPOSE)`, and a grant that does not name
a purpose is not a grant. This module turns that file into value objects. It decides nothing —
`policy.py` does the deciding; this is the vocabulary the decisions are made in.

Three ideas from the file's own preamble survive into the types here, because losing any one of
them collapses the model into "can this person query this database", which is not a policy:

1. **A role sees TABLES, COLUMNS and ROWS — three separate decisions.** `Role` therefore carries
   `allow`/`deny_tables`, `deny_columns`/`column_masks`, and `row_filters` as three independent
   structures, never one merged allow-list.
2. **`Withheld` is not `Absent`.** Every mask in the file declares `cell_type: Withheld`, and
   this module preserves that rather than "helpfully" turning a mask into a value. A masked cell
   is a denial with a reason code; it is not a partial disclosure, and inventing the latter would
   be inventing a cell state the design does not have (O1b, still open).
3. **Free text is the hard case.** The file names the free-text surfaces explicitly, and the
   column classification is carried through so a policy engine can answer *"what is this column"*
   before it answers *"who may see it"*.

## Row filters, and the part that fails closed

`row_filters` are written as SQL-ish fragments. Compiling arbitrary ones needs the portable
predicate grammar of §6, which does not exist (Appendix A calls it the highest-risk undefined
component). So exactly two shapes compile, and they are the two the file actually uses for
relational sources:

    <column> = :principal.<attr>
    <column> IN (SELECT <col> FROM <schema>.<table> [WHERE <col> = :principal.<attr>])

Everything else — the three-table `JOIN ... USING` filter on `core_banking.txn`, the mailbox
header filter, the document filter on `credit_memos` — compiles to `UNCOMPILABLE`, and a read of
that entity is then **refused**, not served with the rule dropped. A row rule that silently does
not run is the exact shape of a policy that permits everything while appearing not to.

## Two grammars, and why `version:` does not tell them apart

Two estates were authored independently and their `roles:` blocks are **different languages**, not
different dialects:

| | `permission_lists` (finance) | `grant_list` (government) |
|---|---|---|
| `roles` | mapping name → body | list of `{id, title, egress_ceiling, grants, denied}` |
| permissions | `allow` / `deny_tables` / `deny_columns` | `grants: [{object, purposes, row_scope, |
| | `row_filters` | columns, withheld, masked}]` |
| purpose | role-level, + a `purpose_scoped_grants` table | **per grant** (R-002 D5, deliberately) |
| row rules | inline SQL-ish, one per object | `row_scope.predicate`, naming `@aliases` |
| | | in a top-level `scopes:` block |
| column policy | deny / mask lists | a CLOSED `columns:` allow-list per grant |

**`version:` cannot be the discriminator, and it was checked.** `fixtures/packs/finance/roles.yaml`
declares `version: 2` and is `permission_lists`; `fixtures/packs/government/roles.yaml` declares
`version: 2` and is `grant_list`; `packages/adapter/tests/mcp_estate/roles.yaml` declares
`version: 1` and is `permission_lists`. The field is each estate's own POLICY revision number —
"this is revision 2 of our access model" — and two estates revising independently arrived at the
same number meaning different things. Branching on it would read the government file with the
finance reader on the strength of a coincidence.

So the grammar is named, once, by `_detect_grammar`: a pack may DECLARE it as a top-level
`roles_grammar:` key (no pack does yet, and that is the fix — a file should say what language
it is written in), and failing that it is determined ONCE from the `roles:` node's own declared
form and then every entry is validated against that choice. A file that mixes the two is refused
rather than half-read. This is one explicit decision with a refusal on ambiguity, not per-role
shape sniffing.

## Grant atomicity (R-002 `combination_semantics.grant_atomicity`)

> A grant is an indivisible tuple `(object, row_scope, columns, transformations, purposes,
> egress_ceiling)`. A cell is disclosed if and only if ONE SINGLE grant authorises the whole tuple.
> Row scopes and column sets are NEVER unioned independently.

`Grant` is that tuple, and it is the type the `grant_list` reader produces. Three things keep the
rule true rather than merely stated:

1. `Grant` carries its row scope, its column list, its transforms, its purposes AND its ceiling on
   ONE object. There is no constructor, method or property anywhere in this module that returns a
   merged column set or a merged row scope across two grants.
2. `Role.grant_for()` returns at most one grant per `(object, purpose)`, and the loader REFUSES a
   role that declares two grants over one object (`_v2_grants_for_role`). Two grants over one
   object cannot be flattened into the single `row_filters[object]` / `column_masks[column]` entry
   this build compiles without pairing one grant's rows with the other's columns, which is the
   escalation the rule exists to forbid. Refusing is the only answer that is not that bug.
3. Deny takes precedence and combines across every role the principal holds — that is `policy.py`
   stage 5, reading `deny_sources`/`deny_tables`, which the `grant_list` reader populates from each
   role's `denied:` block.

## What a `grant_list` role compiles down to, and where it fails closed

`policy.py` speaks the `permission_lists` vocabulary, so each `Grant` is ALSO projected onto
`allow` / `row_filters` / `column_masks` / `purpose_grants` / `unscoped_objects`. Every place the
projection cannot carry the whole truth, it carries LESS rather than more:

* **A withheld column becomes a mask, never a `deny_columns` entry.** v2 gives every withholding
  its own reason code from the estate's `reason_codes` vocabulary, and `policy.py` emits
  `POLICY_COLUMN_DENIED` for a denied column — a code the government estate does not declare, so
  the read would die inside `require_reason` instead of denying. Binding the column to a
  synthesised `withheld:<REASON>` mask instead makes the cell come back `Withheld` carrying **the
  estate's own code**. The mask emits nothing (`emit: ""`); it is a denial with a reason, which is
  what `Mask` has always been.
* **The closed world is materialised where the file can see it.** Stage 7 is `closed_world: true`:
  a column outside the grant's `columns:` list is Withheld, not disclosed. This build's column rule
  is asked one column at a time and has no schema, so the projection withholds every column the
  estate's own `column_classification` names for that object and the grant does not list, under the
  grant's `default_excluded_column_reason`. A column in NEITHER the grant nor the classification is
  the `POLICY_UNCLASSIFIED_COLUMN` case; closing it needs the source schema, which is `describe()`'s
  and not this module's — it is named here rather than left to be discovered.
* **A conditional grant does not exist until its condition can be evaluated.** `adapter_service`
  writes `condition: ":principal.bound_source_id == 'permits_registry'"` and the estate is explicit
  that "a grant whose condition is false is not merely filtered — it does not exist for that
  instance" (review finding H11). This build cannot evaluate a credential binding, so it cannot
  find the condition true; the grant is kept on `Grant.condition` and left OUT of `allow`, and the
  instance reaches nothing. That is the safe direction for a machine credential.
* **A per-grant, per-column free-text exception is not projected.** v2 writes two of them
  (a permit condition's text; an inspector's notes on their own inspection) as
  `free_text_exception:` on the grant. `policy.py` reads `free_text_policy.exceptions`, which is
  keyed by role and purpose only, so projecting them would hand the whole role every free-text
  column in the estate. They stay on `Grant.free_text_exception`, and the two columns stay
  withheld: narrower than the estate wrote, and narrow is the direction that is safe to be wrong in.

## A role with no grants is a load error, not an empty role

`_normalise_roles` once made the government file *parse* while every role came back with zero
grants — it failed closed, and it read as success, which is worse than an obvious failure because
nothing downstream can tell an empty role model from a working one. A `grant_list` role that
resolves to zero grants therefore raises `RoleModelError` naming the role and the file.
`RoleModel.grant_census()` exposes the per-role count so a caller can assert on it too, but the
raise is the load-bearing half: a census nobody reads is the same silence in a different place.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Optional, Sequence

import yaml

from .errors import PurposeNotBound

#: Where the packaged pack-purpose→estate-purpose binding lives. See the file's own header on why
#: it belongs in the pack and lives here for now.
PURPOSE_BINDINGS_FILE = Path(__file__).resolve().parent / "config" / "purpose_bindings.yaml"

#: Egress classes, ranked. A role's ceiling must be at least the source's class for rows to be
#: allowed to reach it. `LICENSED_THIRD_PARTY` ranks highest because the constraint on it is
#: contractual as well as internal: the licence forbids the rows leaving even where the internal
#: classification would allow it.
EGRESS_RANK: Mapping[str, int] = MappingProxyType({
    "PUBLIC": 0, "INTERNAL": 1, "RESTRICTED_INTERNAL": 2, "LICENSED_THIRD_PARTY": 3,
})

#: The two grammars a pack may write its `roles:` block in. NOT the file's `version:` field — see
#: the module docstring for why that field cannot discriminate them.
GRAMMAR_PERMISSION_LISTS = "permission_lists"
GRAMMAR_GRANT_LIST = "grant_list"
GRAMMARS: frozenset[str] = frozenset({GRAMMAR_PERMISSION_LISTS, GRAMMAR_GRANT_LIST})

#: The reason a column outside a grant's `columns:` list carries when the grant names no other.
#: Declared by the estate as `default_excluded_column_reason`; this is the fallback when it does
#: not, and it is the code both estates use for exactly this case.
DEFAULT_EXCLUDED_REASON = "POLICY_DATA_MINIMIZATION"

#: `@permits.my_caseload` — a reference to the top-level `scopes:` block.
_ALIAS = re.compile(r"@[A-Za-z_][\w.]*")

_EQ = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*=\s*:principal\.([A-Za-z_]\w*)\s*$")
_IN_SUB = re.compile(
    r"^\s*([A-Za-z_]\w*)\s+IN\s*\(\s*SELECT\s+([A-Za-z_]\w*)\s+FROM\s+([A-Za-z_][\w.]*)"
    r"(?:\s+WHERE\s+([A-Za-z_]\w*)\s*=\s*:principal\.([A-Za-z_]\w*))?\s*\)\s*$",
    re.IGNORECASE | re.DOTALL,
)


class RoleModelError(Exception):
    """`roles.yaml` (or the purpose binding) is missing, malformed, or inconsistent.

    Loud and file-naming, exactly like `PurposeError`: a policy input that half-loaded would give
    a role fewer denials than its author wrote, and nothing downstream could tell.
    """


# ---------------------------------------------------------------------------------------
# Row rules
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Subquery:
    """The `IN (SELECT ...)` half of a row rule, compiled.

    Carried as data rather than as a string so the Core renders it through the driver's own
    parameter binding. The Core may execute exactly this and nothing else; there is no field here
    that could widen it, which is what "Core evaluates it locally and cannot widen it" has to mean
    in practice.
    """

    schema: Optional[str]           # "core_banking" — the source's unit of namespacing, if any
    table: str
    select_column: str
    where_column: Optional[str] = None
    where_principal_attr: Optional[str] = None

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.table}" if self.schema else self.table


@dataclass(frozen=True)
class RowRule:
    """One compiled row-level rule, or an honest record that it could not be compiled."""

    target: str                     # "core_banking.party" | "mailbox" — as written in roles.yaml
    role: str
    kind: str                       # "eq" | "in_subquery" | "uncompilable"
    raw: str
    column: Optional[str] = None
    principal_attr: Optional[str] = None
    subquery: Optional[Subquery] = None

    @property
    def rule_id(self) -> str:
        return f"{self.role}:row:{self.target}"

    @property
    def compilable(self) -> bool:
        return self.kind != "uncompilable"

    def describe(self) -> str:
        if self.kind == "eq":
            return f"{self.column} = :principal.{self.principal_attr}"
        if self.kind == "in_subquery":
            sub = self.subquery
            assert sub is not None                                # narrowed by `kind`
            where = (f" WHERE {sub.where_column} = :principal.{sub.where_principal_attr}"
                     if sub.where_column else "")
            return f"{self.column} IN (SELECT {sub.select_column} FROM {sub.qualified}{where})"
        return self.raw


def compile_row_filter(target: str, role: str, expression: str) -> RowRule:
    """Compile one `row_filters` entry. Never raises — an uncompilable rule is a RECORD.

    Refusing here would make loading the role model depend on which entities a deployment happens
    to read. The refusal belongs at admission, where the entity being read is known, so the error
    can name it.
    """
    text = " ".join(str(expression).split())
    m = _EQ.match(text)
    if m:
        return RowRule(target=target, role=role, kind="eq", raw=text,
                       column=m.group(1).rsplit(".", 1)[-1], principal_attr=m.group(2))
    m = _IN_SUB.match(text)
    if m:
        column, select_col, source_table, where_col, where_attr = m.groups()
        schema: Optional[str] = None
        table = source_table
        if "." in source_table:
            schema, table = source_table.rsplit(".", 1)
        return RowRule(
            target=target, role=role, kind="in_subquery", raw=text, column=column,
            principal_attr=where_attr,
            subquery=Subquery(schema=schema, table=table, select_column=select_col,
                              where_column=where_col, where_principal_attr=where_attr))
    return RowRule(target=target, role=role, kind="uncompilable", raw=text)


def resolve_scope_aliases(predicate: str, scopes: Mapping[str, Any], *, where: str,
                          path: Path) -> str:
    """Substitute `@alias` references with the definitions the estate's `scopes:` block declares.

    **One level, deliberately.** `@permits.my_decisions` is defined as
    `SELECT decision_id FROM permits.decisions WHERE application_id IN @permits.my_caseload` — a
    two-hop scope, which the estate's own `row_scope_compilation` note says does not compile.
    Expanding it a second time would produce a nested `IN (SELECT ... IN (SELECT ...))` that
    `compile_row_filter` refuses anyway; leaving the inner `@` in the text reaches the same verdict
    and keeps the RAW rule readable, so the refusal names the two-hop scope rather than a
    machine-generated string nobody wrote.

    An alias the `scopes:` block does not declare is a LOADER error, at any depth. A reference to a
    scope that does not exist is not a narrow scope, it is no scope, and reading it as "uncompilable
    and therefore refused" would let a typo in an alias name look identical to a policy this build
    cannot yet express.
    """
    text = " ".join(str(predicate).split())
    seen = _ALIAS.findall(text)
    if not seen:
        return text
    _require_declared_aliases(seen, scopes, where=where, path=path)
    # Longest first, so `@permits.my_caseload` cannot eat the head of a longer alias name.
    for name in sorted(set(seen), key=len, reverse=True):
        definition = str((scopes.get(name) or {}).get("definition") or "").strip()
        if not definition:
            raise RoleModelError(
                f"{path}: {where} names scope {name!r}, which the `scopes:` block declares without "
                f"a `definition`. A named scope with no definition is a filter that cannot run")
        text = text.replace(name, f"({definition})")
    text = " ".join(text.split())
    # Nested aliases must exist too, even though this build stops here and refuses the read.
    _require_declared_aliases(_ALIAS.findall(text), scopes, where=where, path=path)
    return text


def _require_declared_aliases(names: Sequence[str], scopes: Mapping[str, Any], *, where: str,
                              path: Path) -> None:
    unknown = sorted({n for n in names if n not in scopes})
    if unknown:
        raise RoleModelError(
            f"{path}: {where} names scope alias(es) {unknown} that the top-level `scopes:` block "
            f"does not declare. Declared: {sorted(scopes) or '(none)'}. Refused rather than left "
            f"uncompilable: a mistyped alias and a scope this build cannot compile must not look "
            f"the same, because only one of them is fixed by editing the policy")


# ---------------------------------------------------------------------------------------
# Grants — the indivisible tuple of the `grant_list` grammar
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Grant:
    """ONE grant: `(object, row_scope, columns, transformations, purposes, egress_ceiling)`.

    Indivisible on purpose. Every field a disclosure decision needs is on this one object, so
    `authorises()` can be answered without consulting a second grant — which is what grant
    atomicity means once it stops being a paragraph. Nothing here returns a set unioned with
    another grant's.
    """

    role: str
    object: str                          # "permits.applications" | "case_files" | "permits.*"
    source_id: str
    entity: Optional[str]                # None where the estate names the object at SOURCE level
    purposes: frozenset[str]
    columns: frozenset[str]              # {"*"} for a declared wildcard projection
    withheld: Mapping[str, str]          # column -> the estate's own reason code
    withheld_when: Mapping[str, str]     # column -> the predicate the estate conditioned it on
    transforms: Mapping[str, str]        # column -> declared mask name (`masked:` / a `transform:`)
    egress_ceiling: str
    row_scope: Optional[RowRule]         # None == the estate declares this object unscoped
    outside: str = "n/a"                 # existence_not_disclosed | contents_withheld | n/a
    outside_reason: Optional[str] = None
    condition: Optional[str] = None      # ":principal.bound_source_id == '...'"
    free_text_exception: frozenset[str] = frozenset()
    default_excluded_reason: str = DEFAULT_EXCLUDED_REASON
    wildcard_justification: Optional[str] = None

    @property
    def object_path(self) -> str:
        return self.object

    @property
    def unscoped(self) -> bool:
        """The estate declared `predicate: null` — no row scope, with a written justification."""
        return self.row_scope is None

    @property
    def conditional(self) -> bool:
        return self.condition is not None

    @property
    def compilable(self) -> bool:
        """Can this build actually run the row scope? An unscoped grant needs none."""
        return self.row_scope is None or self.row_scope.compilable

    @property
    def wildcard_columns(self) -> bool:
        return "*" in self.columns

    def covers(self, source_id: str, entity: Optional[str] = None) -> bool:
        if self.source_id != source_id:
            return False
        if self.entity in (None, "*") or entity is None:
            return True
        return entity == self.entity

    def admits_purpose(self, purpose: str) -> bool:
        """D5, moved role→grant. A grant that does not name the purpose is not a grant for it."""
        return purpose in self.purposes

    def column_disposition(self, column: str) -> tuple[str, Optional[str]]:
        """`("disclosed", None)` | `("transformed", mask)` | `("withheld", reason_code)`.

        Most restrictive first, per `combination_semantics.within_one_context`: withheld beats
        transformed beats raw, compared per cell. The final branch is stage 7's closed world — a
        column the grant does not list is withheld, never disclosed by omission.
        """
        if column in self.withheld:
            return ("withheld", self.withheld[column])
        if column in self.transforms:
            return ("transformed", self.transforms[column])
        if self.wildcard_columns or column in self.columns:
            return ("disclosed", None)
        return ("withheld", self.default_excluded_reason)

    def authorises(self, *, source_id: str, entity: Optional[str], purpose: str, column: str,
                   egress_class: Optional[str] = None) -> bool:
        """Does THIS ONE grant authorise the whole tuple for this cell?

        The whole tuple or nothing: object, purpose, an evaluable row scope, the column, and the
        source's egress class against the ceiling this grant carries. A conditional grant answers
        `False` while its condition cannot be evaluated — an unevaluated condition is not a
        satisfied one.
        """
        if self.conditional or not self.covers(source_id, entity):
            return False
        if not self.admits_purpose(purpose) or not self.compilable:
            return False
        if egress_class is not None:
            if EGRESS_RANK.get(egress_class, len(EGRESS_RANK)) > EGRESS_RANK[self.egress_ceiling]:
                return False
        return self.column_disposition(column)[0] == "disclosed"


# ---------------------------------------------------------------------------------------
# Masks, roles, the model
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Mask:
    """A declared masking rule. Every one in `roles.yaml` declares `cell_type: Withheld`.

    Kept verbatim rather than interpreted. `emit:` is documentation of what the estate INTENDED a
    mask to look like; this build does not emit it, because a masked value that is not in the
    source cannot carry a pointer that cites the source (§4a), and the estate itself resolves the
    tension by declaring the cell type as `Withheld`.
    """

    name: str
    emit: str
    cell_type: str
    note: Optional[str] = None
    #: The estate's own reason code for this mask (`roles.yaml` v2 declares one per mask).
    #: Read rather than invented: `WithholdingRecord.reason_code` is supposed to come from
    #: the policy's vocabulary, and a code this engine made up is not in it.
    declared_reason: Optional[str] = None

    @property
    def reason_code(self) -> str:
        return self.declared_reason or f"MASKED_{self.name.upper()}"


@dataclass(frozen=True)
class Role:
    """One role. Tables, columns and rows are three separate decisions and stay that way."""

    name: str
    description: str
    purposes: frozenset[str]
    egress_ceiling: str
    allow: Mapping[str, frozenset[str]]         # source_id -> entity names, or {"*"}
    deny_tables: frozenset[str]                 # "core_banking.party_address"
    deny_columns: frozenset[str]                # "core_banking.party.notes"
    column_masks: Mapping[str, str]             # column path (or "*") -> mask name
    deny_sources: frozenset[str]                # "mailbox" | "core_banking.party" (prefix match)
    row_filters: Mapping[str, RowRule]          # target -> rule
    aggregate_only: bool = False
    minimum_group_size: Optional[int] = None
    pointers_only: bool = False
    metadata_only: bool = False
    expires_on: Optional[date] = None
    obligations: tuple[str, ...] = ()
    #: `<purpose> -> objects`. `roles.yaml` v2: "The role-level `purposes` list is a union,
    #: not a grant. `purpose_scoped_grants` is what a purpose actually reaches. A (role,
    #: purpose) pair with no entry reaches NOTHING. Absence is a refusal, not a wildcard."
    purpose_grants: Mapping[str, frozenset[str]] = MappingProxyType({})
    #: Objects this role reads WITHOUT a row scope, each with a written justification.
    #: `row_scope_completeness`: every allowed object must be in `row_filters` or here, and
    #: an object in neither fails closed. There is no default scope.
    unscoped_objects: frozenset[str] = frozenset()
    #: The `grant_list` grammar's indivisible tuples, in declaration order. Empty for a
    #: `permission_lists` role, where the same information is spread across `allow`,
    #: `row_filters`, `column_masks` and `purpose_grants` and there is no single tuple to carry.
    grants: tuple[Grant, ...] = ()
    #: `default_excluded_column_reason` — what a column outside every grant's `columns:` carries.
    default_excluded_reason: str = DEFAULT_EXCLUDED_REASON

    def grants_entity(self, source_id: str, entity: str) -> bool:
        """Is `source.entity` inside this role's allow-list and outside every denial?

        Order matters and denial wins: an entry in `deny_sources`/`deny_tables` beats an `allow`
        of `"*"`, because the file writes the wildcard as the ordinary case and the denials as the
        exceptions to it.
        """
        path = f"{source_id}.{entity}"
        for denied in self.deny_sources:
            if denied == source_id or denied == path or path.startswith(f"{denied}."):
                return False
        if path in self.deny_tables:
            return False
        allowed = self.allow.get(source_id) or self.allow.get("*")
        if allowed is None:
            return False
        return "*" in allowed or entity in allowed

    def mask_for(self, source_id: str, entity: str, column: str) -> Optional[str]:
        """The mask name applying to a column, or `None`. `"*"` masks every column (auditor).

        The middle `"*"` is the `grant_list` grammar's source-level object: the government estate
        writes `object: case_files` — a source with one collection — so its column policy is
        registered as `case_files.*.content` and applies whatever entity name a reader splits that
        source into. No `permission_lists` file writes a `*` in that position, so the extra lookup
        cannot change what the finance estate resolves to.
        """
        path = f"{source_id}.{entity}.{column}"
        if path in self.column_masks:
            return self.column_masks[path]
        source_level = f"{source_id}.*.{column}"
        if source_level in self.column_masks:
            return self.column_masks[source_level]
        return self.column_masks.get("*")

    # --- the `grant_list` grammar's own vocabulary ------------------------------------
    @property
    def grant_count(self) -> int:
        return len(self.grants)

    def grants_for(self, source_id: str, entity: Optional[str] = None,
                   purpose: Optional[str] = None) -> tuple[Grant, ...]:
        """Every grant covering this object (and purpose). At most one, by loader invariant."""
        return tuple(g for g in self.grants
                     if g.covers(source_id, entity)
                     and (purpose is None or g.admits_purpose(purpose)))

    def grant_for(self, source_id: str, entity: Optional[str] = None,
                  purpose: Optional[str] = None) -> Optional[Grant]:
        """THE grant authorising this object under this purpose, or `None`.

        Singular on purpose. The loader refuses a role holding two grants over one object, so this
        never has to choose — and there is deliberately no sibling that merges two grants' rows
        and columns, because that merge IS the escalation `grant_atomicity` forbids.
        """
        found = self.grants_for(source_id, entity, purpose)
        return found[0] if found else None

    def denies_column(self, source_id: str, entity: str, column: str) -> bool:
        return f"{source_id}.{entity}.{column}" in self.deny_columns

    def row_rule_for(self, source_id: str, entity: str) -> Optional[RowRule]:
        return (self.row_filters.get(f"{source_id}.{entity}")
                or self.row_filters.get(source_id))

    def is_unscoped(self, source_id: str, entity: str) -> bool:
        """Does the role declare this object readable WITHOUT a row scope, with a reason?"""
        return bool({"*", source_id, f"{source_id}.{entity}"} & set(self.unscoped_objects))

    def purpose_reaches(self, purpose: str, source_id: str, entity: str) -> bool:
        """Does THIS purpose reach this object? Absence is a refusal, not a wildcard."""
        objects = self.purpose_grants.get(purpose)
        if objects is None:
            return False
        return bool({"*", source_id, f"{source_id}.{entity}"} & set(objects))

    def is_expired(self, today: date) -> bool:
        return self.expires_on is not None and today > self.expires_on


class RoleModel:
    """The whole `roles.yaml`, loaded. Sealed: mappings are proxies over copies."""

    __slots__ = ("_estate", "_version", "_roles", "_masks", "_classification", "_estate_purposes",
                 "_path", "_reason_codes",
                 "_free_text_exceptions", "_grammar", "_free_text_source_columns")

    def __init__(self, *, estate: str, version: int, roles: Mapping[str, Role],
                 masks: Mapping[str, Mask], classification: Mapping[str, tuple[str, ...]],
                 estate_purposes: Mapping[str, str], path: Path,
                 reason_codes: Mapping[str, str] = MappingProxyType({}),
                 free_text_exceptions: Sequence[Mapping[str, Any]] = (),
                 grammar: str = GRAMMAR_PERMISSION_LISTS,
                 free_text_source_columns: Iterable[tuple[str, str]] = ()) -> None:
        self._estate = estate
        self._version = version
        self._roles = MappingProxyType(dict(roles))
        self._masks = MappingProxyType(dict(masks))
        self._classification = MappingProxyType(dict(classification))
        self._estate_purposes = MappingProxyType(dict(estate_purposes))
        self._path = path
        self._reason_codes = MappingProxyType(dict(reason_codes))
        self._free_text_exceptions = tuple(dict(e) for e in free_text_exceptions)
        self._grammar = grammar
        self._free_text_source_columns = frozenset(free_text_source_columns)

    # --- reading ---
    @property
    def estate(self) -> str:
        return self._estate

    @property
    def version(self) -> int:
        return self._version

    @property
    def path(self) -> Path:
        return self._path

    @property
    def roles(self) -> Mapping[str, Role]:
        return self._roles

    @property
    def grammar(self) -> str:
        """Which `roles:` language this file was written in. See the module docstring."""
        return self._grammar

    def grant_census(self) -> Mapping[str, int]:
        """role -> number of grants it resolved to. Zero anywhere is a bug this loader refuses.

        `load_role_model` already raises on an empty `grant_list` role, so this can only report
        zero for a `permission_lists` role, where grants are not a countable thing. It exists so a
        caller can ASSERT rather than assume — an empty role model that loaded cleanly is exactly
        what hid the government estate's grants for as long as it did.
        """
        return MappingProxyType({name: role.grant_count for name, role in self._roles.items()})

    @property
    def masks(self) -> Mapping[str, Mask]:
        return self._masks

    @property
    def estate_purposes(self) -> Mapping[str, str]:
        return self._estate_purposes

    @property
    def classification(self) -> Mapping[str, tuple[str, ...]]:
        return self._classification

    @property
    def reason_codes(self) -> Mapping[str, str]:
        """The estate's own denial vocabulary. `WithholdingRecord.reason_code` is documented as
        coming from \"that policy's vocabulary\", so it comes from here rather than from a
        constant in engine code - a reason an auditor cannot look up is a reason nobody can
        aggregate, appeal against, or count."""
        return self._reason_codes

    def require_reason(self, code: str) -> str:
        if self._reason_codes and code not in self._reason_codes:
            raise RoleModelError(
                f"{self._path}: reason code {code!r} is not in the estate's declared "
                f"vocabulary. A denial nobody can look up is a denial nobody can appeal")
        return code

    @property
    def free_text_exceptions(self) -> tuple[Mapping[str, Any], ...]:
        """`free_text_policy.exceptions` - the named exceptions to "withheld from every role".

        There is exactly one in this estate, and reading it from the file rather than hard-coding
        `compliance_officer` is the difference between a policy engine and an engine with a
        favourite role.
        """
        return self._free_text_exceptions

    def is_free_text(self, source_id: str, entity: str, column: str) -> bool:
        """`free_text_policy`: withheld from EVERY role by default, by named exception only.

        \"462 pieces of personal data are planted inside these columns and no column-level rule
        can see them, because there are no columns inside a paragraph. A column-level policy
        that denies `party.date_of_birth` and then hands over `party.notes` containing the same
        date of birth in prose has denied nothing.\"

        The second lookup is for the `grant_list` grammar, which names some free-text surfaces at
        SOURCE level because the source has exactly one of them: `case_files.content`,
        `correspondence.body`, `inspection_photos.ocr.blocks[].text`. Those are indexed as
        `(source, column)` and match whatever entity a reader splits the source into. Only entries
        the estate itself declared as free text are indexed this way, and the effect is only ever
        to withhold MORE - a `permission_lists` file, whose classification paths are all
        `source.entity.column`, contributes nothing to this set and is unaffected.
        """
        path = f"{source_id}.{entity}.{column}"
        if path in self._classification.get("free_text_may_contain_personal_data", ()):
            return True
        return (source_id, column) in self._free_text_source_columns

    def require_role(self, name: str) -> Role:
        try:
            return self._roles[name]
        except KeyError:
            raise RoleModelError(
                f"role {name!r} is not declared in {self._path}. Known roles: "
                f"{', '.join(sorted(self._roles))}. An unknown role is refused rather than "
                f"treated as 'no role', which would read as 'no denials'") from None

    def require_mask(self, name: str) -> Mask:
        try:
            return self._masks[name]
        except KeyError:
            raise RoleModelError(
                f"mask {name!r} is not declared in {self._path}: a column bound to an undeclared "
                f"mask would disclose in full") from None

    @property
    def bundle_version(self) -> str:
        """What goes into `Entitlement.policy_bundle_version` alongside the compiled table."""
        return f"{self._estate}@v{self._version}"


def _as_str_set(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        return frozenset({value})
    return frozenset(str(v) for v in value)


def _merge(parent: Optional[Mapping[str, Any]], child: Mapping[str, Any]) -> dict[str, Any]:
    """`extends:` resolution. A child's key REPLACES the parent's, it does not union with it.

    `senior_relationship_manager` extends `relationship_manager` and overrides two row filters
    with wider ones. Unioning would leave the narrower own-book filter in force beside the wider
    team filter, and the role would silently not do what it says.
    """
    if parent is None:
        return dict(child)
    merged = dict(parent)
    merged.update({k: v for k, v in child.items() if k != "extends"})
    if "row_filters" in parent and "row_filters" in child:
        rf = dict(parent["row_filters"] or {})
        rf.update(child["row_filters"] or {})
        merged["row_filters"] = rf
    return merged


def _normalise_roles(raw: Any, path: Path) -> Mapping[str, Any]:
    """The `permission_lists` grammar's `roles:` node: a mapping of role name -> body.

    This once accepted a LIST too, so that the government file would parse. It did parse - and
    every role came back with zero grants, because the list form is a DIFFERENT GRAMMAR and not a
    second spelling of this one. Accepting the shape without reading the grammar produced a role
    model that looked complete and granted nothing, which is worse than the loud failure it
    replaced. The list form is read by `_load_grant_list_roles`, and which reader runs is decided
    once by `_detect_grammar`.
    """
    if isinstance(raw, dict):
        return raw
    # pragma: no cover - `_detect_grammar` refuses a non-mapping before this reader runs
    raise RoleModelError(
        f"{path}: the {GRAMMAR_PERMISSION_LISTS!r} reader was handed a "
        f"{type(raw).__name__}; `roles` must be a mapping of name -> declaration")


def _detect_grammar(data: Mapping[str, Any], path: Path) -> str:
    """Which language is this file's `roles:` block written in? Decided ONCE, then enforced.

    Order of authority:

    1. a top-level `roles_grammar:` key, if the pack declares one. No pack does yet, and that is
       the real fix — a policy file should say what language it is written in rather than leave a
       loader to work it out.
    2. otherwise the `roles:` node's own declared form, taken once for the whole file, after which
       EVERY entry is validated against that choice. A file that mixes the two is refused.

    NOT `version:`. Both estates declare `version: 2` and mean different things by it — see
    the module docstring. Branching on it would read one estate's file with the other's reader
    on the strength of a coincidence.
    """
    declared = data.get("roles_grammar")
    if declared is not None:
        if str(declared) not in GRAMMARS:
            raise RoleModelError(
                f"{path}: declares roles_grammar {declared!r}; known grammars are "
                f"{sorted(GRAMMARS)}")
        return str(declared)

    raw = data.get("roles")
    if isinstance(raw, dict):
        offenders = sorted(name for name, body in raw.items()
                           if isinstance(body, dict) and ("grants" in body or "overrides" in body))
        if offenders:
            raise RoleModelError(
                f"{path}: `roles` is a mapping, which is the {GRAMMAR_PERMISSION_LISTS!r} grammar, "
                f"but {offenders} carry a `grants:`/`overrides:` block, which is "
                f"{GRAMMAR_GRANT_LIST!r}. A file in two grammars is refused rather than half-read: "
                f"whichever reader ran, the other grammar's permissions would be silently dropped, "
                f"and dropped permissions read as a working policy")
        return GRAMMAR_PERMISSION_LISTS
    if isinstance(raw, list):
        for i, entry in enumerate(raw):
            if not isinstance(entry, dict):
                raise RoleModelError(
                    f"{path}: roles[{i}] is not a mapping. In the {GRAMMAR_GRANT_LIST!r} grammar "
                    f"every entry is a role declaration carrying an `id`")
            if not (entry.get("id") or entry.get("role") or entry.get("name")):
                raise RoleModelError(
                    f"{path}: roles[{i}] declares no `id`. The id is what names the role, and a "
                    f"role nobody can name cannot be granted to anyone")
        return GRAMMAR_GRANT_LIST
    raise RoleModelError(
        f"{path}: `roles` must be a mapping of name -> declaration ({GRAMMAR_PERMISSION_LISTS}) "
        f"or a list of declarations each carrying an `id` ({GRAMMAR_GRANT_LIST}). Found "
        f"{type(raw).__name__}")


# ---------------------------------------------------------------------------------------
# The `grant_list` reader
# ---------------------------------------------------------------------------------------


def _split_object(obj: str) -> tuple[str, Optional[str]]:
    """`"permits.applications"` -> `("permits", "applications")`; `"case_files"` -> `("case_files",
    None)`; `"permits.*"` -> `("permits", "*")`.

    The estate names some objects at SOURCE level because the source has exactly one collection.
    That is carried as `entity=None` rather than resolved to a reader's entity name here — which
    entities a source splits into is `describe()`'s answer, and inventing one in the policy loader
    would put a name in the access model that no policy author wrote.
    """
    source, dot, entity = str(obj).partition(".")
    return (source, entity if dot else None)


def _classified_columns(data: Mapping[str, Any]) -> Mapping[str, frozenset[str]]:
    """`object path -> the columns `column_classification` names for it`.

    This is what makes stage 7's closed world enforceable through a column rule that is asked one
    column at a time and holds no schema: every column the estate has classified for an object,
    and which a grant does not list, is withheld. It is not the whole closed world — a column in
    neither the grant nor the classification is the `POLICY_UNCLASSIFIED_COLUMN` case and needs the
    source schema — and the shortfall is named in the module docstring rather than left to be
    found.
    """
    out: dict[str, set[str]] = {}
    for entries in (data.get("column_classification") or {}).values():
        if not isinstance(entries, (list, tuple)):
            continue
        for entry in entries:
            declared = str(entry.get("column") if isinstance(entry, dict) else entry)
            obj, _, column = declared.rpartition(".")
            if not obj or column == "*":
                continue
            out.setdefault(obj, set()).add(column)
    return {k: frozenset(v) for k, v in out.items()}


def _free_text_source_columns(data: Mapping[str, Any]) -> frozenset[tuple[str, str]]:
    """`free_text_may_contain_personal_data` entries that are NOT `source.entity.column`.

    `case_files.content` (two segments) and `inspection_photos.ocr.blocks[].text` (a path into a
    sidecar) name a source and a column with the entity elided. Indexed as `(source, column)` so
    `is_free_text` matches whatever entity name a reader gives that source.
    """
    out: set[tuple[str, str]] = set()
    for entry in ((data.get("column_classification") or {})
                  .get("free_text_may_contain_personal_data") or ()):
        declared = str(entry.get("column") if isinstance(entry, dict) else entry)
        parts = declared.split(".")
        if len(parts) == 3 or parts[-1] == "*":
            continue                      # already an exact path, or a wildcard the reader handles
        out.add((parts[0], parts[-1]))
    return frozenset(out)


def _v2_grants_for_role(name: str, entries: Mapping[str, Mapping[str, Any]], path: Path,
                        seen: tuple[str, ...] = ()) -> tuple[list[dict], list[dict]]:
    """Resolve one role's grants and denials, applying `extends` + `overrides`.

    `combination_semantics.extends`: "`extends` copies the parent's grants, then applies the
    child's overrides. An override may narrow a grant. An override that WIDENS one must restate the
    whole grant tuple." So an override REPLACES the parent's grant for that object — it does not
    merge with it, because a merge would leave the parent's narrower row scope in force beside the
    child's wider one and the role would silently not do what it says.

    Denials UNION with the parent's. Deny is the one direction in which combining is safe, and
    `deny_precedence` says so: "they combine only in the restrictive direction."
    """
    if name in seen:
        raise RoleModelError(f"{path}: role inheritance cycle: {' -> '.join((*seen, name))}")
    entry = entries.get(name)
    if entry is None:
        raise RoleModelError(
            f"{path}: role {name!r} is extended but not declared. Known roles: "
            f"{', '.join(sorted(entries))}")

    parent_name = entry.get("extends")
    if parent_name:
        inherited, denied = _v2_grants_for_role(str(parent_name), entries, path, (*seen, name))
        by_object = {str(g["object"]): g for g in inherited}
    else:
        by_object, denied = {}, []

    own: dict[str, dict] = {}
    for kind in ("grants", "overrides"):
        for grant in (entry.get(kind) or ()):
            if not isinstance(grant, dict) or not grant.get("object"):
                raise RoleModelError(f"{path}: role {name!r} declares a {kind} entry with no "
                                     f"`object`. A grant over nothing is not a grant")
            obj = str(grant["object"])
            if obj in own:
                # `grant_atomicity`: the flat projection this build compiles has ONE row scope and
                # ONE column policy per object, so two grants over one object could only be
                # flattened by pairing one grant's rows with the other's columns. That pairing is
                # exactly what the rule forbids, so it is refused instead.
                raise RoleModelError(
                    f"{path}: role {name!r} declares two grants over {obj!r}. A grant is an "
                    f"indivisible tuple (object, row_scope, columns, transformations, purposes, "
                    f"egress_ceiling) and 'there is no path by which grant A's rows meet grant B's "
                    f"columns'. This build compiles one row scope and one column policy per "
                    f"object, so two would have to be merged - which is that path. Merge them in "
                    f"the file, where the widening is visible in the diff")
            if kind == "overrides" and parent_name and obj not in by_object:
                raise RoleModelError(
                    f"{path}: role {name!r} overrides {obj!r}, which its parent "
                    f"{str(parent_name)!r} does not grant. An override of nothing is a grant "
                    f"wearing an override's name, and it would not be visible as a widening")
            own[obj] = dict(grant)
    by_object.update(own)

    denied = list(denied) + [dict(d) for d in (entry.get("denied") or ()) if isinstance(d, dict)]
    return list(by_object.values()), denied


def _load_grant_list_roles(data: Mapping[str, Any], path: Path, *, masks: dict[str, Mask],
                           estate_purposes: Mapping[str, str],
                           reason_codes: Mapping[str, str]) -> dict[str, Role]:
    """Read the `grant_list` grammar into `Role`s that carry their `Grant`s AND a flat projection.

    The `Grant` tuples are the truth. The flat `allow`/`row_filters`/`column_masks`/
    `purpose_grants`/`unscoped_objects` are a PROJECTION of them onto the vocabulary `policy.py`
    speaks, and every place the projection cannot carry the whole truth it carries less. See the
    module docstring for the four places that happens and why each is the safe direction.
    """
    scopes = dict(data.get("scopes") or {})
    classified = _classified_columns(data)
    forbidden = frozenset(
        str(f) for f in
        ((data.get("untrusted_fields_forbidden_in_predicates") or {}).get("fields") or ()))

    entries: dict[str, dict] = {}
    for i, entry in enumerate(data.get("roles") or ()):
        name = str(entry.get("id") or entry.get("role") or entry.get("name"))
        if name in entries:
            raise RoleModelError(
                f"{path}: role {name!r} is declared twice. Two grants under one name is an "
                f"access-control question the file does not answer")
        entries[name] = dict(entry)

    def require_code(code: Optional[str], where: str) -> str:
        """Every reason code in the file must be in the file's own closed set.

        `reason_codes`: "a withholding with no code in this list is a bug, not a policy." Checked
        at LOAD, because a code checked only when it fires is a code nobody has checked.
        """
        text = str(code or DEFAULT_EXCLUDED_REASON)
        if reason_codes and text not in reason_codes:
            raise RoleModelError(
                f"{path}: {where} names reason code {text!r}, which is not in the estate's own "
                f"`reason_codes` set. A denial nobody can look up is a denial nobody can appeal")
        return text

    def withholding_mask(reason: str) -> str:
        """A denial carrying the estate's OWN reason code, expressed as a `Mask`.

        Not a `deny_columns` entry: `policy.py` gives a denied column `POLICY_COLUMN_DENIED`, a
        code this estate does not declare, so the read would die in `require_reason` rather than
        deny. A mask emitting nothing is a denial with a reason attached, which is what `Mask` has
        meant since it was written ("a masked cell is a denial with a reason code; it is not a
        partial disclosure").
        """
        mask_name = f"withheld:{reason}"
        if mask_name not in masks:
            masks[mask_name] = Mask(
                name=mask_name, emit="", cell_type="Withheld", declared_reason=reason,
                note=("synthesised from a `withheld:` entry in the grant_list grammar, which gives "
                      "every withholding its own declared reason code. Emits nothing"))
        return mask_name

    roles: dict[str, Role] = {}
    for name in entries:
        entry = entries[name]
        raw_grants, raw_denied = _v2_grants_for_role(name, entries, path)
        if not raw_grants:
            raise RoleModelError(
                f"{path}: role {name!r} resolved to ZERO grants. A role that grants nothing is "
                f"either a file this loader failed to understand or a role that should be deleted, "
                f"and it must not load as a working role model - an empty role model that loads "
                f"cleanly is indistinguishable from a working one to everything downstream, which "
                f"is how the government estate went un-enforced")

        ceiling = str(entry.get("egress_ceiling") or "INTERNAL")
        if ceiling not in EGRESS_RANK:
            raise RoleModelError(f"{path}: role {name!r} declares unknown egress_ceiling "
                                 f"{ceiling!r}; known: {', '.join(EGRESS_RANK)}")
        role_default_reason = require_code(entry.get("default_excluded_column_reason"),
                                           f"role {name!r}")

        grants: list[Grant] = []
        allow: dict[str, set[str]] = {}
        purpose_grants: dict[str, set[str]] = {}
        row_filters: dict[str, RowRule] = {}
        unscoped: set[str] = set()
        column_masks: dict[str, str] = {}
        purposes: set[str] = set()

        for raw in raw_grants:
            obj = str(raw["object"])
            where = f"role {name!r} grant over {obj!r}"
            source_id, entity = _split_object(obj)

            grant_purposes = _as_str_set(raw.get("purposes"))
            if not grant_purposes:
                # `purpose_binding_rules`: "A grant with no `purposes` list is INVALID and must
                # fail the policy loader, not default to the role's list."
                raise RoleModelError(
                    f"{path}: {where} declares no `purposes`. Purpose moved from the role to the "
                    f"GRANT deliberately (D5, review finding C4): a grant that does not name a "
                    f"purpose is not a grant, and defaulting to the role's list is the exact "
                    f"escalation that move was made to stop")
            unknown = sorted(grant_purposes - set(estate_purposes))
            if unknown:
                raise RoleModelError(
                    f"{path}: {where} names purpose(s) {unknown} that the file does not declare. A "
                    f"grant acting under an undeclared purpose cannot be audited against a lawful "
                    f"basis")

            declared_columns = raw.get("columns")
            columns = (frozenset({"*"}) if declared_columns == "*"
                       else _as_str_set(declared_columns))
            if not columns:
                raise RoleModelError(
                    f"{path}: {where} declares no `columns`. Stage 7 is a closed world - the list "
                    f"IS the projection - so an absent list is not 'everything', it is a grant "
                    f"nobody wrote")

            withheld: dict[str, str] = {}
            withheld_when: dict[str, str] = {}
            transforms: dict[str, str] = {}
            grant_default = require_code(
                raw.get("withheld_default_reason") or role_default_reason, where)
            for item in (raw.get("withheld") or ()):
                item = dict(item or {})
                column = str(item.get("column") or "")
                if not column:
                    raise RoleModelError(f"{path}: {where} has a `withheld` entry with no `column`")
                reason = require_code(item.get("reason") or grant_default, where)
                transform = item.get("transform")
                if item.get("when"):
                    # A row-conditional withholding ("sealed = true"). This build has no engine for
                    # a per-row column rule, so it withholds UNCONDITIONALLY. Withholding a cell
                    # the estate would have disclosed on some rows is the safe error; disclosing a
                    # sealed one because the condition could not be evaluated is not.
                    withheld_when[column] = " ".join(str(item["when"]).split())
                if transform:
                    if str(transform) not in masks:
                        raise RoleModelError(
                            f"{path}: {where} binds {column!r} to undeclared transform "
                            f"{str(transform)!r}. An undeclared transform would disclose in full")
                    transforms[column] = str(transform)
                else:
                    withheld[column] = reason
            for column, mask_name in (raw.get("masked") or {}).items():
                column, mask_name = str(column), str(mask_name)
                if mask_name not in masks:
                    raise RoleModelError(
                        f"{path}: {where} binds {column!r} to undeclared mask {mask_name!r}")
                if column in withheld:
                    raise RoleModelError(
                        f"{path}: {where} both withholds and masks {column!r}. A column cannot be "
                        f"withheld outright AND disclosed in transformed form; the file has to "
                        f"say which")
                transforms[column] = mask_name

            scope = dict(raw.get("row_scope") or {})
            predicate = scope.get("predicate")
            rule: Optional[RowRule] = None
            if predicate:
                expanded = resolve_scope_aliases(str(predicate), scopes, where=where, path=path)
                # The objects this predicate actually reads: the grant's own, plus anything it
                # selects FROM. A bare column name is only checked against those, so
                # `benefits.payments.reference` cannot condemn an unrelated `reference` column
                # somewhere else - a loader that over-rejects gets edited until it stops, which
                # is how a real check becomes a disabled one.
                reads = {obj} | set(re.findall(r"\bFROM\s+([A-Za-z_][\w.]*)", expanded,
                                               re.IGNORECASE))
                named = sorted(
                    f for f in forbidden
                    if f in expanded
                    or (f.rsplit(".", 1)[0] in reads
                        and re.search(rf"\b{re.escape(f.rsplit('.', 1)[-1])}\b", expanded)))
                if named:
                    # D3, and the estate's own `enforcement` line: "policy loader rejects any grant
                    # whose row_scope predicate names one of these."
                    raise RoleModelError(
                        f"{path}: {where} scopes rows on {named}, which "
                        f"`untrusted_fields_forbidden_in_predicates` forbids. An untrusted value "
                        f"may be stored, cited and displayed; it may never decide who reads it "
                        f"(D3). Rejected at load - at runtime this is POLICY_UNTRUSTED_PREDICATE")
                rule = compile_row_filter(obj, name, expanded)

            free_text_exception = raw.get("free_text_exception") or {}
            if isinstance(free_text_exception, dict):
                fte = _as_str_set(free_text_exception.get("column"))
            else:
                fte = frozenset(str(e.get("column")) for e in free_text_exception
                                if isinstance(e, dict) and e.get("column"))

            grant = Grant(
                role=name, object=obj, source_id=source_id, entity=entity,
                purposes=grant_purposes, columns=columns,
                withheld=MappingProxyType(dict(withheld)),
                withheld_when=MappingProxyType(dict(withheld_when)),
                transforms=MappingProxyType(dict(transforms)),
                egress_ceiling=ceiling, row_scope=rule,
                outside=str(scope.get("outside") or raw.get("existence") or "n/a"),
                outside_reason=(require_code(scope.get("outside_reason"), where)
                                if scope.get("outside_reason") else None),
                condition=(str(raw["condition"]) if raw.get("condition") else None),
                free_text_exception=fte,
                default_excluded_reason=grant_default,
                wildcard_justification=(str(raw["wildcard_justification"])
                                        if raw.get("wildcard_justification") else None))
            grants.append(grant)
            purposes |= grant_purposes

            # --- the projection onto the vocabulary `policy.py` speaks ---
            if grant.conditional:
                # "A grant whose condition is false is not merely filtered - it does not exist for
                # that instance." This build cannot evaluate a credential binding, so it cannot
                # find the condition TRUE, and an unevaluated condition is not a satisfied one.
                # The grant stays on `Grant.condition`; it reaches nothing through `allow`.
                continue
            allow.setdefault(source_id, set()).add(entity or "*")
            for purpose in grant_purposes:
                purpose_grants.setdefault(purpose, set()).add(obj)
            if rule is None:
                unscoped.add(obj)
            else:
                row_filters[obj] = rule

            prefix = f"{source_id}.{entity or '*'}."
            for column, mask_name in transforms.items():
                column_masks[prefix + column] = mask_name
            for column, reason in withheld.items():
                column_masks[prefix + column] = withholding_mask(reason)
            if "*" not in columns:
                # Stage 7's closed world, as far as the file itself can see it.
                for column in sorted(classified.get(obj, frozenset())
                                     - columns - set(withheld) - set(transforms)):
                    column_masks[prefix + column] = withholding_mask(grant_default)

        deny_tables: set[str] = set()
        deny_sources: set[str] = set()
        for denial in raw_denied:
            obj = str(denial.get("object") or "")
            if not obj:
                raise RoleModelError(f"{path}: role {name!r} has a `denied` entry with no `object`")
            require_code(denial.get("reason"), f"role {name!r} denial of {obj!r}")
            if obj.endswith(".*"):
                deny_sources.add(obj[:-2])
            elif "." in obj:
                deny_tables.add(obj)
            else:
                deny_sources.add(obj)

        roles[name] = Role(
            name=name,
            description=str(entry.get("description") or "").strip(),
            purposes=frozenset(purposes),
            egress_ceiling=ceiling,
            allow=MappingProxyType({s: frozenset(e) for s, e in allow.items()}),
            deny_tables=frozenset(deny_tables),
            deny_columns=frozenset(),        # v2 expresses column denial as a reason-carrying mask
            column_masks=MappingProxyType(column_masks),
            deny_sources=frozenset(deny_sources),
            row_filters=MappingProxyType(row_filters),
            obligations=tuple(str(o) for o in (entry.get("obligations") or ())),
            purpose_grants=MappingProxyType({p: frozenset(o)
                                             for p, o in purpose_grants.items()}),
            unscoped_objects=frozenset(unscoped),
            grants=tuple(grants),
            default_excluded_reason=role_default_reason,
        )
    return roles


def load_role_model(path: Path) -> RoleModel:
    """Read one estate's `roles.yaml`, in whichever of the two grammars it is written in."""
    path = Path(path)
    if not path.is_file():
        raise RoleModelError(f"{path}: no role model. Fail closed: with no policy input there is "
                             f"no principal that may read anything")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:                                # pragma: no cover - yaml detail
        raise RoleModelError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RoleModelError(f"{path}: top level must be a mapping")

    masks: dict[str, Mask] = {}
    for name, body in (data.get("masks") or {}).items():
        body = body or {}
        masks[str(name)] = Mask(
            name=str(name), emit=str(body.get("emit") or ""),
            # `permission_lists` spells it `cell_type`, `grant_list` spells it `cell`. A rename
            # only - both declare `Withheld`, and both mean a denial carrying a reason code.
            cell_type=str(body.get("cell_type") or body.get("cell") or "Withheld"),
            note=(str(body["note"]) if body.get("note") else None),
            declared_reason=(str(body["reason"]) if body.get("reason") else None))

    estate_purposes = {str(p["id"]): str(p.get("description") or "")
                       for p in (data.get("purposes") or []) if isinstance(p, dict) and "id" in p}
    if not estate_purposes:
        raise RoleModelError(f"{path}: declares no purposes. A grant that does not name a purpose "
                             f"is not a grant (D5), so a role model with no purposes grants "
                             f"nothing and says so here rather than at every read")

    reason_codes = {str(k): str(v) for k, v in (data.get("reason_codes") or {}).items()}
    classification = {str(k): tuple(str(v) for v in (vals or ()))
                      for k, vals in (data.get("column_classification") or {}).items()}
    estate = str(data.get("estate") or path.parent.name)
    version = int(data.get("version") or 1)
    grammar = _detect_grammar(data, path)

    if grammar == GRAMMAR_GRANT_LIST:
        # `masks` is passed in and mutated: the reader synthesises one `withheld:<REASON>` mask per
        # distinct reason code the grants use, so every withheld cell carries the ESTATE's own
        # code rather than one this engine made up. See the module docstring.
        return RoleModel(
            estate=estate, version=version,
            roles=_load_grant_list_roles(data, path, masks=masks,
                                         estate_purposes=estate_purposes,
                                         reason_codes=reason_codes),
            masks=masks, classification=classification, estate_purposes=estate_purposes,
            path=path, reason_codes=reason_codes,
            free_text_exceptions=tuple(
                (data.get("free_text_policy") or {}).get("exceptions") or ()),
            grammar=grammar, free_text_source_columns=_free_text_source_columns(data))

    scoped_grants = data.get("purpose_scoped_grants") or {}
    raw_roles = _normalise_roles(data.get("roles"), path)

    resolved: dict[str, dict] = {}

    def _resolve(name: str, seen: tuple[str, ...] = ()) -> dict:
        if name in resolved:
            return resolved[name]
        if name in seen:
            raise RoleModelError(f"{path}: role inheritance cycle: {' -> '.join((*seen, name))}")
        body = raw_roles.get(name)
        if not isinstance(body, dict):
            raise RoleModelError(f"{path}: role {name!r} is not a mapping")
        parent_name = body.get("extends")
        parent = _resolve(str(parent_name), (*seen, name)) if parent_name else None
        merged = _merge(parent, body)
        resolved[name] = merged
        return merged

    roles: dict[str, Role] = {}
    for name in raw_roles:
        body = _resolve(str(name))
        purposes = _as_str_set(body.get("purposes"))
        unknown = sorted(purposes - set(estate_purposes))
        if unknown:
            raise RoleModelError(
                f"{path}: role {name!r} names purpose(s) {unknown} that the file does not "
                f"declare. A role acting under an undeclared purpose cannot be audited against a "
                f"lawful basis")
        allow_raw = body.get("allow") or {}
        allow = {str(src): _as_str_set(ents) for src, ents in allow_raw.items()}
        ceiling = str(body.get("egress_ceiling") or "INTERNAL")
        if ceiling not in EGRESS_RANK:
            raise RoleModelError(f"{path}: role {name!r} declares unknown egress_ceiling "
                                 f"{ceiling!r}; known: {', '.join(EGRESS_RANK)}")
        masks_raw = {str(k): str(v) for k, v in (body.get("column_masks") or {}).items()}
        for column, mask_name in masks_raw.items():
            if mask_name not in masks:
                raise RoleModelError(
                    f"{path}: role {name!r} binds {column!r} to undeclared mask {mask_name!r}")
        row_filters = {str(target): compile_row_filter(str(target), str(name), str(expr))
                       for target, expr in (body.get("row_filters") or {}).items()}
        unscoped = frozenset(
            str(entry["object"]) if isinstance(entry, dict) else str(entry)
            for entry in (body.get("unscoped_objects") or ()))
        grants: dict[str, frozenset[str]] = {}
        for purpose, grant in (scoped_grants.get(str(name)) or {}).items():
            grant = grant or {}
            inherited = grant.get("inherits")
            if inherited:
                grant = ((scoped_grants.get(str(inherited)) or {}).get(purpose) or {})
            grants[str(purpose)] = frozenset(str(o) for o in (grant.get("objects") or ()))
        # DENY WINS over every allow AND every mask, and it is a LOADER error for one column
        # to be in both lists - `deny_mask_precedence` says so in as many words, and calls it
        # "not a runtime precedence question".
        overlap = sorted(set(masks_raw) & _as_str_set(body.get("deny_columns")))
        if overlap:
            raise RoleModelError(
                f"{path}: role {name!r} both denies and masks {overlap}. `deny_mask_precedence` "
                f"makes this a policy LOADER error rather than a runtime precedence question: "
                f"a column cannot be both withheld outright and disclosed in transformed form")
        expires = body.get("expires_on")
        roles[str(name)] = Role(
            name=str(name),
            description=str(body.get("description") or "").strip(),
            purposes=purposes,
            egress_ceiling=ceiling,
            allow=MappingProxyType(allow),
            deny_tables=_as_str_set(body.get("deny_tables")),
            deny_columns=_as_str_set(body.get("deny_columns")),
            column_masks=MappingProxyType(masks_raw),
            deny_sources=_as_str_set(body.get("deny_sources")),
            row_filters=MappingProxyType(row_filters),
            aggregate_only=bool(body.get("aggregate_only", False)),
            minimum_group_size=(int(body["minimum_group_size"])
                                if body.get("minimum_group_size") is not None else None),
            pointers_only=bool(body.get("pointers_only", False)),
            metadata_only=bool(body.get("metadata_only", False)),
            expires_on=(expires if isinstance(expires, date) else
                        (date.fromisoformat(str(expires)) if expires else None)),
            obligations=tuple(str(o) for o in (body.get("obligations") or ())),
            purpose_grants=MappingProxyType(grants),
            unscoped_objects=unscoped,
        )

    return RoleModel(estate=estate, version=version, roles=roles, masks=masks,
                     classification=classification, estate_purposes=estate_purposes,
                     path=path, reason_codes=reason_codes,
                     free_text_exceptions=tuple(
                         (data.get("free_text_policy") or {}).get("exceptions") or ()),
                     grammar=grammar,
                     free_text_source_columns=_free_text_source_columns(data))


# ---------------------------------------------------------------------------------------
# Pack purpose -> estate purpose
# ---------------------------------------------------------------------------------------


class PurposeBinding:
    """The declared join between a pack's purpose vocabulary and an estate's access model."""

    __slots__ = ("_estate", "_pack", "_bindings", "_path")

    def __init__(self, *, estate: str, pack: str, bindings: Mapping[str, str], path: Path) -> None:
        self._estate = estate
        self._pack = pack
        self._bindings = MappingProxyType(dict(bindings))
        self._path = path

    @property
    def estate(self) -> str:
        return self._estate

    @property
    def pack(self) -> str:
        return self._pack

    @property
    def bindings(self) -> Mapping[str, str]:
        return self._bindings

    def require(self, pack_purpose: str) -> str:
        """The estate purpose for a pack purpose, or `PurposeNotBound` naming both files."""
        try:
            return self._bindings[pack_purpose]
        except KeyError:
            raise PurposeNotBound(
                f"pack purpose {pack_purpose!r} is declared but bound to no purpose in estate "
                f"{self._estate!r}'s access model. Bindings live in {self._path}; the estate's "
                f"purposes live in its roles.yaml. Bound purposes: "
                f"{', '.join(sorted(self._bindings)) or '(none)'}. Refused rather than mapped to "
                f"the nearest-looking estate purpose, which would attach a lawful basis nobody "
                f"wrote down (D5)") from None


def load_purpose_bindings(estate: str, *, path: Path = PURPOSE_BINDINGS_FILE,
                          known_estate_purposes: Optional[Iterable[str]] = None) -> PurposeBinding:
    """Read the binding table for one estate, checking it against that estate's own purposes."""
    path = Path(path)
    if not path.is_file():
        raise RoleModelError(f"{path}: no purpose bindings. Without them no pack purpose maps to "
                             f"the access model and every read is refused")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entry = (data.get("estates") or {}).get(estate)
    if entry is None:
        raise RoleModelError(
            f"{path}: no purpose bindings for estate {estate!r}. Declared: "
            f"{', '.join(sorted((data.get('estates') or {}))) or '(none)'}")
    bindings = {str(k): str(v) for k, v in (entry.get("bindings") or {}).items()}
    if known_estate_purposes is not None:
        known = set(known_estate_purposes)
        unknown = sorted({v for v in bindings.values() if v not in known})
        if unknown:
            raise RoleModelError(
                f"{path}: estate {estate!r} binds pack purposes to estate purpose(s) {unknown} "
                f"that its roles.yaml does not declare")
    return PurposeBinding(estate=estate, pack=str(entry.get("pack") or ""), bindings=bindings,
                          path=path)


def applicable_roles(roles: Sequence[Role], estate_purpose: str) -> tuple[Role, ...]:
    """The roles that may act under this purpose. Purpose-scoped, not role-scoped.

    This one line is `roles.yaml`'s `purpose_mismatch` case: an RM holds `relationship_management`
    and `credit_assessment`, so asking under `financial_crime_compliance` leaves the applicable
    set empty and the read is refused. Holding data for one purpose is not permission to use it
    for another, and the check has to happen here rather than at the column level — otherwise the
    role's grants would still apply and only the audit log would differ.
    """
    return tuple(r for r in roles if estate_purpose in r.purposes)
