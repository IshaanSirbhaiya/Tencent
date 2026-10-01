"""Tool arguments shaped and checked against the tool's ``inputSchema`` BEFORE the call (E2E run 2,
N-11).

A planner names arguments from memory; an MCP server validates them strictly and answers a wrong
name with an error the run pays for in tokens. So, deterministically and before anything is sent:

1. **rename** — when exactly ONE required argument is missing and exactly ONE argument the tool
   does not take was sent, and its value fits the missing one's type, it is that argument under
   another name (``{"uri": …}`` for a tool that takes ``path``). Anything less certain is not
   guessed;
2. **fill** — a missing required argument whose schema settles it (a ``default``, a ``const``, an
   ``enum`` of one value) is filled with that value. Nothing else is filled: a value the schema
   does not settle is the planner's to choose;
3. **coerce** — a scalar of the wrong JSON type is converted when the conversion is exact
   (``"20"`` -> ``20`` for an integer, ``20`` -> ``"20"`` for a string, ``"true"`` -> ``true``,
   a JSON object written as a string -> the object, a lone value -> a one-item array);
4. **check** — what remains is validated (`runtime.args_schema_problems`); a call that still does
   not fit is NOT sent, and the planner is told the schema in plain words so it can fix it.

Every change is reported back to the planner (``arguments_shaped``) and recorded on the step, so a
reader sees what was sent, not what was asked for. The rules read only the schema: no tool, server
or domain is named here.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping, Optional

_INT = re.compile(r"^[+-]?\d+$")
_NUM = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def _types(prop: Any) -> list[str]:
    if not isinstance(prop, Mapping):
        return []
    t = prop.get("type")
    if isinstance(t, str):
        return [t]
    if isinstance(t, list):
        return [x for x in t if isinstance(x, str) and x != "null"]
    return []


def _fits(value: Any, prop: Any) -> bool:
    """Whether ``value`` is (or converts exactly to) the property's single type."""
    types = _types(prop)
    if not types:
        return True
    return any(_coerce_to(value, t)[1] for t in types)


def _is_type(v: Any, t: str) -> bool:
    if t == "string":
        return isinstance(v, str)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "boolean":
        return isinstance(v, bool)
    if t == "object":
        return isinstance(v, Mapping)
    if t == "array":
        return isinstance(v, list)
    return v is None if t == "null" else False


def _coerce_to(v: Any, t: str) -> tuple[Any, bool]:
    """``(value, ok)``: ``v`` as JSON type ``t`` when the conversion is exact."""
    if t == "string":
        if isinstance(v, str):
            return v, True
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v), True
        return v, False
    if t == "integer":
        if isinstance(v, bool):
            return v, False
        if isinstance(v, int):
            return v, True
        if isinstance(v, float) and v.is_integer():
            return int(v), True
        if isinstance(v, str) and _INT.match(v.strip()):
            return int(v.strip()), True
        return v, False
    if t == "number":
        if isinstance(v, bool):
            return v, False
        if isinstance(v, (int, float)):
            return v, True
        if isinstance(v, str) and _NUM.match(v.strip()):
            x = float(v.strip())
            return (int(x) if _INT.match(v.strip()) else x), True
        return v, False
    if t == "boolean":
        if isinstance(v, bool):
            return v, True
        if isinstance(v, str) and v.strip().lower() in ("true", "false"):
            return v.strip().lower() == "true", True
        return v, False
    if t == "object":
        if isinstance(v, Mapping):
            return v, True
        if isinstance(v, str) and v.strip().startswith("{"):
            try:
                obj = json.loads(v)
            except ValueError:
                return v, False
            return (obj, True) if isinstance(obj, dict) else (v, False)
        return v, False
    if t == "array":
        if isinstance(v, (list, tuple)):
            return list(v), True
        if isinstance(v, str) and v.strip().startswith("["):
            try:
                arr = json.loads(v)
            except ValueError:
                arr = None
            if isinstance(arr, list):
                return arr, True
        if v is not None and not isinstance(v, Mapping):
            return [v], True
        return v, False
    if t == "null":
        return v, v is None
    return v, False


def shape_args(args: Any, schema: Optional[Mapping[str, Any]]) -> tuple[dict, list[str]]:
    """``(shaped, notes)``. No schema, or arguments that are not an object: returned unchanged."""
    if not isinstance(args, Mapping):
        return args, []                                       # type: ignore[return-value]
    out = dict(args)
    if not isinstance(schema, Mapping) or not schema:
        return out, []
    notes: list[str] = []
    props = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
    required = [k for k in (schema.get("required") or []) if isinstance(k, str)]
    # 1. rename: ONE required argument missing, ONE argument the tool does not take
    missing = [k for k in required if k not in out]
    unknown = [k for k in out if props and k not in props]
    if len(missing) == 1 and len(unknown) == 1 and _fits(out[unknown[0]], props.get(missing[0])):
        out[missing[0]] = out.pop(unknown[0])
        notes.append(f"'{unknown[0]}' was sent as '{missing[0]}': the tool takes no "
                     f"'{unknown[0]}' and '{missing[0]}' was its only missing required argument")
    # 2. fill what the schema itself settles
    for k in required:
        if k in out:
            continue
        p = props.get(k) if isinstance(props.get(k), Mapping) else {}
        if "default" in p:
            out[k] = p["default"]
        elif "const" in p:
            out[k] = p["const"]
        elif isinstance(p.get("enum"), list) and len(p["enum"]) == 1:
            out[k] = p["enum"][0]
        else:
            continue
        notes.append(f"'{k}' was missing and was filled with {json.dumps(out[k], default=str)}, "
                     f"the only value the tool's schema allows or its default")
    # 3. coerce exact conversions
    for k, v in list(out.items()):
        types = _types(props.get(k))
        if not types or v is None:
            continue
        if any(_is_type(v, t) for t in types):
            continue                                          # already the right type
        for t in types:
            nv, ok = _coerce_to(v, t)
            if ok:
                out[k] = nv
                notes.append(f"'{k}' was converted to {t}")
                break
    return out, notes


def describe_schema(schema: Optional[Mapping[str, Any]], *, max_props: int = 12) -> str:
    """A tool's arguments in one line for the planner: ``{path: string (required), k: integer}``."""
    if not isinstance(schema, Mapping) or not schema:
        return "{} (no schema published)"
    props = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
    required = set(k for k in (schema.get("required") or []) if isinstance(k, str))
    parts = []
    for k in list(props)[:max_props]:
        types = "|".join(_types(props.get(k))) or "any"
        enum = props.get(k, {}).get("enum") if isinstance(props.get(k), Mapping) else None
        if isinstance(enum, list) and 0 < len(enum) <= 6:
            types += " one of " + "/".join(json.dumps(e, default=str) for e in enum)
        parts.append(f"{k}: {types}{' (required)' if k in required else ''}")
    for k in sorted(required - set(props)):
        parts.append(f"{k} (required)")
    more = f", … {len(props) - max_props} more" if len(props) > max_props else ""
    return "{" + ", ".join(parts) + more + "}"


__all__ = ["shape_args", "describe_schema"]
