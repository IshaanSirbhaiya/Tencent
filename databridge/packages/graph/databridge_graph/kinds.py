"""`NodeKind` / `EdgeKind` — the engine's handle on the **enabled packs'** closed vocabulary.

These were a pair of `str, Enum` classes with one vertical's vocabulary written into them
(ADR-0009). The members now come from `packs/<name>/ontology.yaml` via the deployment binding, so
the engine names no domain concept, but *every* property the enums had is preserved deliberately,
because the rest of the tree depends on all of them:

    NodeKind.<SYMBOL>          attribute access by symbol (the kind name, upper-cased)
    NodeKind("document")       construction by value — raises on a kind no enabled pack declares
    kind.value / kind.name     "document" / "DOCUMENT"
    kind == "document"         true; these are `str` subclasses, so SQL binding is unchanged
    set(NodeKind)              iteration over the bound vocabulary
    node.kind is <the same>    instances are INTERNED, so identity comparison works

The last one is not decoration: `node_sheet.py` and others compare with `is`. Interning per
(class, value) keeps that exact.

What is *new* is that every instance also answers `.meta` — its domain-free meta-kind — which is
how engine code reasons about a pack it has never seen. `merge.py` no longer asks whether a node
is of one named kind; it asks the vocabulary "is this kind a subject?", and the vocabulary answers
from the pack's `subject_kinds` declaration (spine D4b).

The vocabulary stays CLOSED. `NodeKind("not_a_kind")` raises, exactly as the enum did — an
unrecognised kind is skipped by callers, never coined.
"""
from __future__ import annotations

from typing import Any, Iterator

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

from .meta import EdgeMetaKind, NodeMetaKind
from .packs import UnknownKind, active_vocabulary

#: Interned instances, keyed by (class name, value). Populated only after validation against the
#: bound vocabulary, so an unbound kind can never be handed out even if it was bound earlier.
_INTERN: dict[tuple[str, str], "_Kind"] = {}


def _decls(axis: str):
    v = active_vocabulary()
    if axis == "node":
        return v.node_kinds
    if axis == "edge":
        return v.edge_kinds
    raise TypeError(
        "_Kind is abstract: use NodeKind or EdgeKind, which declare which axis they close over")


class _KindMeta(type):
    """Gives the kind classes the parts of the `Enum` API the tree actually uses."""

    def __getattr__(cls, symbol: str):
        # Only SYMBOLS resolve here. Everything private or lowercase must fall through to a real
        # AttributeError so that pydantic/copy/pickle introspection is not answered with a kind.
        if symbol.startswith("_") or not symbol.isupper():
            raise AttributeError(symbol)
        axis = cls._AXIS
        if not axis:
            raise AttributeError(symbol)
        decl = _decls(axis).get(symbol.lower())
        if decl is None:
            raise AttributeError(
                f"{cls.__name__} has no member {symbol!r}: no enabled pack declares that "
                f"{axis} kind. Enabled vocabulary: {sorted(_decls(axis))}")
        return cls(decl.name)

    def __iter__(cls) -> Iterator["_Kind"]:
        return iter([cls(name) for name in sorted(_decls(cls._AXIS))])

    def __len__(cls) -> int:
        return len(_decls(cls._AXIS))

    def __contains__(cls, item: Any) -> bool:
        return str(item) in _decls(cls._AXIS)


class _Kind(str, metaclass=_KindMeta):
    """One kind name. A `str` subclass, so it binds to SQL and serialises with no adapter."""

    __slots__ = ()
    _AXIS: str = ""

    def __new__(cls, value: Any) -> "_Kind":
        v = str(value)
        if v not in _decls(cls._AXIS):
            raise UnknownKind(cls._AXIS, v, list(_decls(cls._AXIS)))
        key = (cls.__name__, v)
        inst = _INTERN.get(key)
        if inst is None:
            inst = _INTERN[key] = super().__new__(cls, v)
        return inst

    # --- the enum-shaped surface ---
    @property
    def value(self) -> str:
        return str(self)

    @property
    def name(self) -> str:
        return str(self).upper()

    # --- the new surface: what this kind IS, independent of any domain ---
    @property
    def decl(self):
        """The pack declaration this kind came from."""
        return _decls(self._AXIS)[str(self)]

    @property
    def meta(self):
        """The domain-free meta-kind this kind binds to (`meta.py`, spine §4 Layer 0)."""
        return self.decl.binds_to

    @property
    def pack(self) -> str:
        """The pack that declared it."""
        return self.decl.pack

    def __repr__(self) -> str:                       # matches Enum's shape for readable failures
        return f"<{type(self).__name__}.{self.name}: {str(self)!r}>"

    # --- pydantic ---
    @classmethod
    def _validate(cls, v: Any) -> "_Kind":
        return v if type(v) is cls else cls(v)

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: GetCoreSchemaHandler):
        # `after` a str schema, so JSON-schema generation still describes a plain string and
        # serialisation is str's — the persisted `kind` column is byte-identical to the enum's.
        return core_schema.no_info_after_validator_function(cls._validate, core_schema.str_schema())


class NodeKind(_Kind):
    """A node kind declared by an enabled pack. Closed vocabulary."""

    __slots__ = ()
    _AXIS = "node"


class EdgeKind(_Kind):
    """An edge kind declared by an enabled pack. Closed vocabulary."""

    __slots__ = ()
    _AXIS = "edge"


def node_kinds_binding_to(meta: NodeMetaKind) -> tuple[NodeKind, ...]:
    """Every enabled node kind of a given meta-shape — how domain-free code finds, say, every
    DOCUMENT-shaped kind without knowing what the pack calls them."""
    return tuple(NodeKind(n) for n in active_vocabulary().nodes_binding_to(meta))


def edge_kinds_binding_to(meta: EdgeMetaKind) -> tuple[EdgeKind, ...]:
    return tuple(EdgeKind(n) for n in active_vocabulary().edges_binding_to(meta))


__all__ = ["NodeKind", "EdgeKind", "node_kinds_binding_to", "edge_kinds_binding_to"]
