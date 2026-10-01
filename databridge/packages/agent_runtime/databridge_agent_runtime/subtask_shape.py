"""``delegate.ask`` arguments shaped and checked against the ``SubTask`` contract BEFORE the call
(E2E run 3, P-02) — the delegated twin of `shape` (N-11), which does the same for MCP tools.

A planner writes a sub-task from memory: ``op: "="``, ``field: "invoice.status"``,
``time_window.axis: "invoice.due_date"``, ``group_by: "account_owner"``. The federated service
validates strictly and answers with pydantic's text, which the run then pastes into its answer. So,
deterministically and before anything is sent:

1. **schema shaping** — the sub-task is walked against the contract's OWN JSON schema
   (``SubTask.model_json_schema()``, read at call time, never copied here):

   * a value outside an ``enum`` becomes the enum member it spells: the same word in another case,
     or a comparison SYMBOL (``=`` ``==`` ``!=`` ``<>`` ``<`` ``<=`` ``>`` ``>=``) for the operator
     word the enum itself lists (``eq`` … ``ge``). A word the enum does not hold (``mentioned_in``)
     is not guessed;
   * a lone value where the schema takes an array becomes a one-item array; a scalar of the wrong
     JSON type is converted when the conversion is exact (`shape._coerce_to`).

2. **the contract's name rules** — field names in a sub-task are PLAIN column names, and one
   sub-task reads ONE entity kind:

   * a qualified name (``invoice.status``) loses its qualifier when the qualifier names the
     sub-task's entity kind (``entity_kinds[0]``, a trailing plural ``s`` aside); with no entity
     kind given and every qualifier the same, that qualifier BECOMES the entity kind. A qualifier
     naming any OTHER kind is a second kind in one sub-task: not guessed, reported;
   * ``time_window.axis`` given a field name (``invoice.due_date``) when the window names no
     ``field``: the name moves to ``field`` and ``axis`` takes the schema's default.

3. **check** — what remains is validated with the contract itself. A sub-task that still does not
   fit is NOT sent; the planner is told what is wrong in one line per problem (no pydantic URLs,
   no repr dumps) and what a sub-task takes.

Every change is reported (``arguments_shaped``), so a reader sees what was sent, not what was asked
for. Nothing here names a domain, a table or an adapter agent.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping, Optional

from .shape import _coerce_to, _is_type

#: Comparison symbols, and the operator WORD each one spells. A symbol is rewritten only to a word
#: the contract's own enum lists.
_SYMBOLS = {"=": "eq", "==": "eq", "!=": "ne", "<>": "ne", "<": "lt", "<=": "le", ">": "gt",
            ">=": "ge"}
#: A plain field name (the contract's own rule is checked again by its validator).
_PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
#: ``qualifier.field``: one dot, both sides plain.
_QUALIFIED = re.compile(r"^([A-Za-z_][A-Za-z0-9_]{0,63})\.([A-Za-z_][A-Za-z0-9_]{0,63})$")
_PYDANTIC_URL = re.compile(r"\s*For further information visit \S+")


def subtask_model() -> Any:
    """The federated ``SubTask`` model, or None where the federated package is absent (the runtime
    does not import it at module load: that package builds on this one)."""
    try:
        from databridge_federated.contracts import SubTask  # type: ignore  # noqa: PLC0415
    except Exception:                                        # noqa: BLE001 - absent: not shaped
        return None
    return SubTask


def summarise_error(text: Any, *, limit: int = 600) -> str:
    """An error for a person and a planner: pydantic's "for further information" URLs dropped,
    whitespace folded, capped. Nothing is reworded."""
    s = _PYDANTIC_URL.sub("", str(text or ""))
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _resolve(schema: Any, defs: Mapping[str, Any]) -> Any:
    if not isinstance(schema, Mapping):
        return {}
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        return _resolve(defs.get(ref.split("/")[-1], {}), defs)
    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        branches = [b for b in any_of if not (isinstance(b, Mapping) and b.get("type") == "null")]
        if len(branches) == 1:
            return _resolve(branches[0], defs)
    return schema


def _enum_member(value: Any, enum: list) -> Optional[Any]:
    """The enum member ``value`` spells, or None. Exact case-insensitive word, or a symbol for
    the operator word the enum lists."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    folded = [e for e in enum if isinstance(e, str) and e.lower() == v.lower()]
    if len(folded) == 1:
        return folded[0]
    word = _SYMBOLS.get(v)
    if word is not None and word in enum:
        return word
    return None


def _shape(value: Any, schema: Any, defs: Mapping[str, Any], path: str,
           notes: list[str]) -> Any:
    s = _resolve(schema, defs)
    enum = s.get("enum") if isinstance(s, Mapping) else None
    if isinstance(enum, list) and value not in enum:
        member = _enum_member(value, enum)
        if member is not None:
            notes.append(f"{path} {json.dumps(value)} was sent as {json.dumps(member)}")
            return member
        return value
    t = s.get("type") if isinstance(s, Mapping) else None
    if t == "object" and isinstance(value, Mapping):
        props = s.get("properties") if isinstance(s.get("properties"), Mapping) else {}
        return {k: (_shape(v, props[k], defs, f"{path}.{k}" if path else k, notes)
                    if k in props else v) for k, v in value.items()}
    if t == "array":
        if value is not None and not isinstance(value, (list, tuple)):
            nv, ok = _coerce_to(value, "array")
            if ok:
                notes.append(f"{path} was a single value; sent as a one-item list")
                value = nv
        if isinstance(value, (list, tuple)):
            items = s.get("items")
            return [_shape(v, items, defs, f"{path}.{i}", notes) for i, v in enumerate(value)]
        return value
    if isinstance(t, str) and t in ("integer", "number", "boolean", "string") and \
            value is not None and not _is_type(value, t):
        nv, ok = _coerce_to(value, t)
        if ok:
            notes.append(f"{path} was converted to {t}")
            return nv
    return value


def _same_kind(qualifier: str, kind: str) -> bool:
    q, k = qualifier.lower(), kind.lower()
    return q == k or q + "s" == k or q == k + "s"


def _field_slots(st: dict) -> list[tuple[str, Any, Any]]:
    """``(path, getter-container, key)`` for every place the contract holds a FIELD NAME."""
    slots: list[tuple[str, Any, Any]] = []
    for i, f in enumerate(st.get("filters") or []):
        if isinstance(f, dict) and isinstance(f.get("field"), str):
            slots.append((f"filters.{i}.field", f, "field"))
    fields = st.get("fields")
    if isinstance(fields, list):
        for i, x in enumerate(fields):
            if isinstance(x, str):
                slots.append((f"fields.{i}", fields, i))
    tw = st.get("time_window")
    if isinstance(tw, dict) and isinstance(tw.get("field"), str):
        slots.append(("time_window.field", tw, "field"))
    agg = st.get("aggregate")
    if isinstance(agg, dict):
        if isinstance(agg.get("field"), str):
            slots.append(("aggregate.field", agg, "field"))
        gb = agg.get("group_by")
        if isinstance(gb, list):
            for i, x in enumerate(gb):
                if isinstance(x, str):
                    slots.append((f"aggregate.group_by.{i}", gb, i))
    return slots


def _name_rules(st: dict, defs: Mapping[str, Any], notes: list[str],
                problems: list[str]) -> None:
    # time_window.axis given a field name, and the window names no field
    tw = st.get("time_window")
    if isinstance(tw, dict):
        axis_schema = _resolve(_resolve(defs.get("TimeWindow", {}), defs).get(
            "properties", {}).get("axis"), defs) if defs else {}
        axes = axis_schema.get("enum") if isinstance(axis_schema, Mapping) else None
        axis = tw.get("axis")
        if isinstance(axes, list) and isinstance(axis, str) and axis not in axes and \
                (_PLAIN.match(axis) or _QUALIFIED.match(axis)) and not tw.get("field"):
            tw["field"] = axis
            default = axis_schema.get("default")
            if default in axes:
                tw["axis"] = default
            else:
                tw.pop("axis", None)
            notes.append(f"time_window.axis {json.dumps(axis)} names a field, not an axis; it "
                         f"was sent as time_window.field, with axis "
                         f"{json.dumps(tw.get('axis', 'default'))}")
    slots = _field_slots(st)
    quals = {m.group(1) for _, c, k in slots if (m := _QUALIFIED.match(c[k]))}
    if not quals:
        return
    kinds = [k for k in (st.get("entity_kinds") or []) if isinstance(k, str)]
    if not kinds and len(quals) == 1:
        (only,) = quals
        st["entity_kinds"] = [only]
        kinds = [only]
        notes.append(f"entity_kinds was empty; every qualified field names {json.dumps(only)}, "
                     f"so it was sent as the entity kind")
    primary = kinds[0] if kinds else None
    other = sorted(q for q in quals if primary is None or not _same_kind(q, primary))
    for path, c, k in slots:
        m = _QUALIFIED.match(c[k])
        if m and primary is not None and _same_kind(m.group(1), primary):
            notes.append(f"{path} {json.dumps(c[k])} was sent as {json.dumps(m.group(2))}: field "
                         f"names are plain, and {json.dumps(m.group(1))} is this sub-task's kind")
            c[k] = m.group(2)
    if other:
        problems.append(
            f"field names qualified by {other} name another entity kind than this sub-task's "
            f"{json.dumps(primary)}; one sub-task reads ONE kind (entity_kinds[0]) — ask one "
            f"delegate.ask per kind")


def _summarise_validation(e: Exception) -> list[str]:
    errors = getattr(e, "errors", None)
    if not callable(errors):
        return [summarise_error(e)]
    try:
        items = errors(include_url=False)
    except TypeError:
        items = errors()
    out = []
    for it in items:
        loc = ".".join(str(x) for x in it.get("loc", ()) if x != "__root__") or "sub-task"
        msg = str(it.get("msg", "")).replace("Value error, ", "")
        inp = it.get("input")
        shown = "" if isinstance(inp, (dict, list)) or inp is None else \
            f" (got {json.dumps(inp, default=str)[:60]})"
        out.append(f"{loc}: {msg}{shown}")
    return out


def describe_subtask(model: Any = None) -> str:
    """What a sub-task takes, in one line, from the contract's own schema."""
    model = model or subtask_model()
    if model is None:
        return "(the sub-task contract is not available here)"
    schema = model.model_json_schema(by_alias=True)
    defs = schema.get("$defs") or {}

    def enum_of(def_name: str, prop: str) -> str:
        p = _resolve(_resolve(defs.get(def_name, {}), defs).get("properties", {}).get(prop),
                     defs)
        return "|".join(str(x) for x in (p.get("enum") or [])) if isinstance(p, Mapping) else ""

    return (f"{{entity_kinds: [ONE kind], filters: [{{field: plain column name, op: "
            f"{enum_of('Filter', 'op')}, value}}], fields: [plain names], time_window: {{axis: "
            f"{enum_of('TimeWindow', 'axis')}, field: the date column, period: "
            f"{enum_of('TimeWindow', 'period')} | from, to}}, aggregate: {{op: "
            f"{enum_of('Aggregate', 'op')}, field, group_by: [names]}}, limit}}")


def shape_subtask(raw: Any, adapter_agent: str, *,
                  model: Any = None) -> tuple[dict, list[str], list[str]]:
    """``(subtask, notes, problems)``. ``problems`` empty means the contract accepts ``subtask``
    (``adapter_agent`` set from the TOOL NAME, never from the arguments). No contract available:
    returned unchanged with no problems — the federated service validates it."""
    st = dict(raw) if isinstance(raw, Mapping) else {}
    if not isinstance(raw, Mapping):
        nv, ok = _coerce_to(raw, "object")
        st = dict(nv) if ok else {}
        if not ok and raw not in (None, ""):
            return {"adapter_agent": adapter_agent}, [], [
                "the sub-task must be an object (entity_kinds, filters, fields, …)"]
    st["adapter_agent"] = adapter_agent
    model = model or subtask_model()
    if model is None:
        return st, [], []
    schema = model.model_json_schema(by_alias=True)
    defs = schema.get("$defs") or {}
    notes: list[str] = []
    problems: list[str] = []
    st = _shape(st, schema, defs, "", notes)
    st["adapter_agent"] = adapter_agent
    _name_rules(st, defs, notes, problems)
    try:
        model.model_validate(st)
    except Exception as e:                                   # noqa: BLE001 - summarised
        named = bool(problems)               # a qualified name already reported is not repeated
        problems.extend(p for p in _summarise_validation(e)
                        if not (named and "not a plain field name" in p))
    return st, notes, problems


def delegate_holdings(federation: Any, adapter_agent: str) -> Optional[list]:
    """What ``adapter_agent``'s delegate can read — its connections, facts tables and columns,
    document kinds — from the federated service's MEASURED catalogue (names only, no data), or
    None when the service publishes none. Shown to the planner so a sub-task names real tables."""
    cat = getattr(federation, "catalogue", None)
    describe = getattr(cat, "describe", None)
    if not callable(describe):
        return None
    try:
        doc = describe()
    except Exception:                                        # noqa: BLE001 - nothing to show
        return None
    for a in (doc or {}).get("adapter_agents") or []:
        if isinstance(a, Mapping) and a.get("id") == adapter_agent:
            return list(a.get("connections") or []) or None
    return None


__all__ = ["shape_subtask", "describe_subtask", "summarise_error", "subtask_model",
           "delegate_holdings"]
