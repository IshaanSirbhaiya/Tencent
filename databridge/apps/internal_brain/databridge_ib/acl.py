"""Evaluate an ACL expression for one principal — deterministic, no model, fail-closed (plan §4.3).

Platform semantics are compiled INTO expressions by each connector (narrowing Confluence
restrictions become ``all``; widening Drive inheritance becomes ``any``); this module only
evaluates them, so it names no platform.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter

from .models import (AclAll, AclAny, AclDomain, AclEveryone, AclExpr, AclGroups, AclKind, AclNobody,
                     AclUsers, DirectoryUser)

_ADAPTER: TypeAdapter = TypeAdapter(AclExpr)
MAX_DEPTH = 32


def parse(data: Any) -> AclExpr:
    """JSON/dict → expression. Anything that does not parse is ``nobody`` (fail closed)."""
    try:
        if isinstance(data, (str, bytes)):
            data = json.loads(data)
        return _ADAPTER.validate_python(data)
    except Exception:                                               # noqa: BLE001
        return AclNobody(reason="unparseable")


def dump(expr: AclExpr) -> str:
    return json.dumps(_ADAPTER.dump_python(expr, mode="json"), sort_keys=True,
                      separators=(",", ":"))


def allows(expr: AclExpr, user: DirectoryUser, *, _depth: int = 0) -> bool:
    """True only if ``expr`` grants ``user`` read. Inactive users are denied everything."""
    if not user.active or _depth > MAX_DEPTH:
        return False
    if isinstance(expr, AclUsers):
        return user.id in expr.ids
    if isinstance(expr, AclGroups):
        return bool(set(expr.ids) & set(user.groups))
    if isinstance(expr, AclKind):
        return user.kind in expr.kinds
    if isinstance(expr, AclDomain):
        return bool(expr.domain) and user.domain == expr.domain.lower()
    if isinstance(expr, AclEveryone):
        return True
    if isinstance(expr, AclAll):
        return bool(expr.of) and all(allows(e, user, _depth=_depth + 1) for e in expr.of)
    if isinstance(expr, AclAny):
        return any(allows(e, user, _depth=_depth + 1) for e in expr.of)
    return False                                                    # AclNobody and anything unknown


# convenience constructors used by connectors -------------------------------------------------
def users(*ids: str) -> AclUsers:
    return AclUsers(ids=tuple(sorted(set(i for i in ids if i))))


def groups(*ids: str) -> AclGroups:
    return AclGroups(ids=tuple(sorted(set(i for i in ids if i))))


def any_of(*exprs: AclExpr) -> AclExpr:
    exprs = tuple(e for e in exprs if e is not None)
    return AclAny(of=exprs) if exprs else AclNobody(reason="empty-any")


def all_of(*exprs: AclExpr) -> AclExpr:
    exprs = tuple(e for e in exprs if e is not None)
    return AclAll(of=exprs) if exprs else AclNobody(reason="empty-all")


def nobody(reason: str) -> AclNobody:
    return AclNobody(reason=reason)
