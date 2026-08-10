"""TargetRecordModel compiler (C15, 09_FINAL_PRD §11/§16): JSON-Schema | Pydantic | SQL-DDL
→ ONE internal Pydantic model + a ``FieldSpec`` list.

§11/§16 require "all three compiled to one internal TargetRecordModel". Downstream stages
(pre-extract, span-selection, pydantic-extract, grounding, confidence) consume the SAME
``CompiledTarget`` — the model for ``complete_structured`` strict validation, the FieldSpecs
for everything deterministic (pattern hints, grounding data-type, required/optional, repeating
records, cross-field rules). One compile target = no per-input-form code paths downstream.

Grounding data-type per field (drives §11.4 thresholds) is inferred from type + name, and may
be overridden by an explicit ``x-grounding`` annotation. This is the single place that decides
whether a field is matched exact (ids/numbers/dates) or fuzzy (names/free text).
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Optional, Union, get_args, get_origin

from pydantic import BaseModel, create_model

# the grounding data-type taxonomy — maps to §11.4 match rules.
GroundingKind = Literal["id", "number", "date", "name", "text", "enum", "bool"]


@dataclass(frozen=True)
class CrossFieldRule:
    """A schema-declared cross-field consistency rule (§11.5)."""
    kind: Literal["date_order", "sum_to_total", "nonneg"]
    fields: tuple[str, ...] = ()        # date_order: (earlier, later); nonneg: (field,)
    items: str = ""                     # sum_to_total: the repeating field name
    item_amount: str = ""               # sum_to_total: amount field within each item
    total: str = ""                     # sum_to_total: the total field
    tolerance: float = 0.01


@dataclass
class FieldSpec:
    name: str
    grounding: GroundingKind = "text"
    required: bool = False
    description: str = ""
    pattern: Optional[str] = None        # regex hint for deterministic pre-extraction (§11.1)
    enum: Optional[tuple] = None
    repeats: bool = False                # x-repeats: row_anchor → list of item records
    item_fields: tuple["FieldSpec", ...] = ()   # sub-fields when repeats / nested


@dataclass
class CompiledTarget:
    name: str
    model: type[BaseModel]               # the internal Pydantic TargetRecordModel (strict)
    fields: tuple[FieldSpec, ...]
    cross_field_rules: tuple[CrossFieldRule, ...] = ()

    def field(self, name: str) -> Optional[FieldSpec]:
        return next((f for f in self.fields if f.name == name), None)

    @property
    def scalar_fields(self) -> tuple[FieldSpec, ...]:
        return tuple(f for f in self.fields if not f.repeats)

    @property
    def repeating_fields(self) -> tuple[FieldSpec, ...]:
        return tuple(f for f in self.fields if f.repeats)


# --- grounding-kind inference --------------------------------------------------
# Broad id cues for STRING fields (an "invoice"/"account" string field is usually an id).
_ID_NAME = re.compile(r"(^|_)(id|no|number|sku|ref|code|account|invoice)(_|$)", re.I)
# STRICT id cues for NUMERIC fields — exclude account/invoice, which often pair with AMOUNTS
# (account_balance, invoice_total) and must ground as numbers, not exact-id strings (M1).
_NUM_ID_NAME = re.compile(r"(^|_)(id|no|number|sku|ref|code)(_|$)", re.I)
_NAME_NAME = re.compile(r"(^|_)(name|customer|vendor|company|contact|bill_to|ship_to)(_|$)", re.I)
_DATE_NAME = re.compile(r"(date|_at|_on|dob|due)", re.I)


def _infer_grounding(name: str, py_type: Any, *, enum: Optional[tuple],
                     explicit: Optional[str]) -> GroundingKind:
    if explicit in get_args(GroundingKind):
        return explicit  # type: ignore[return-value]
    if enum:
        return "enum"
    base = _strip_optional(py_type)
    if base is bool:
        return "bool"
    if base in (int, float, Decimal):
        # a numeric *_id / *_no is an identifier (exact); a numeric account/invoice/total is money
        return "id" if _NUM_ID_NAME.search(name) else "number"
    if base in (_dt.date, _dt.datetime):
        return "date"
    if _ID_NAME.search(name):
        return "id"
    if _DATE_NAME.search(name):
        return "date"
    if _NAME_NAME.search(name):
        return "name"
    return "text"


def _enum_from_annotation(base: Any) -> Optional[tuple]:
    """Extract enum members from a Literal[...] or an enum.Enum subclass (B1)."""
    import enum as _enum
    if get_origin(base) is Literal:
        return get_args(base)
    if isinstance(base, type) and issubclass(base, _enum.Enum):
        return tuple(e.value for e in base)
    return None


def _pydantic_pattern(info: Any) -> Optional[str]:
    """Read a `pattern` hint from a Pydantic field — both json_schema_extra AND the
    StringConstraints/Field(pattern=) metadata (M5)."""
    if isinstance(info.json_schema_extra, dict) and info.json_schema_extra.get("pattern"):
        return info.json_schema_extra["pattern"]
    for m in getattr(info, "metadata", []) or []:
        pat = getattr(m, "pattern", None)
        if pat:
            return pat
    return None


def _strip_optional(t: Any) -> Any:
    if get_origin(t) is Union:
        args = [a for a in get_args(t) if a is not type(None)]
        return args[0] if args else t
    return t


# --- JSON Schema → CompiledTarget ----------------------------------------------
_JSON_PY = {"string": str, "integer": int, "number": float, "boolean": bool}


def _json_field_type(prop: dict) -> Any:
    t = prop.get("type", "string")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "string")
    if prop.get("format") in ("date", "date-time"):
        return _dt.date
    return _JSON_PY.get(t, str)


def from_json_schema(schema: dict, *, name: str = "TargetRecord") -> CompiledTarget:
    props: dict = schema.get("properties", {})
    required = set(schema.get("required", []))
    specs: list[FieldSpec] = []
    model_fields: dict[str, tuple] = {}
    for fname, prop in props.items():
        is_req = fname in required
        if prop.get("type") == "array" and prop.get("x-repeats"):
            item_schema = prop.get("items", {})
            sub = from_json_schema(item_schema, name=f"{name}_{fname}_item")
            specs.append(FieldSpec(name=fname, grounding="text", required=is_req,
                                   description=prop.get("description", ""), repeats=True,
                                   item_fields=sub.fields))
            ann = list[sub.model]                                     # type: ignore[valid-type]
            model_fields[fname] = (ann if is_req else Optional[ann], ... if is_req else None)
            continue
        py = _json_field_type(prop)
        enum = tuple(prop["enum"]) if prop.get("enum") else None
        g = _infer_grounding(fname, py, enum=enum, explicit=prop.get("x-grounding"))
        specs.append(FieldSpec(name=fname, grounding=g, required=is_req,
                               description=prop.get("description", ""),
                               pattern=prop.get("pattern"), enum=enum))
        model_fields[fname] = (py if is_req else Optional[py], ... if is_req else None)
    model = create_model(name, **model_fields)  # type: ignore[call-overload]
    return CompiledTarget(name=name, model=model, fields=tuple(specs),
                          cross_field_rules=_parse_cross_rules(schema))


def _parse_cross_rules(schema: dict) -> tuple[CrossFieldRule, ...]:
    rules: list[CrossFieldRule] = []
    for r in schema.get("x-cross-field", []):
        k = r.get("kind")
        if k == "date_order":
            rules.append(CrossFieldRule(kind="date_order", fields=tuple(r["fields"])))
        elif k == "sum_to_total":
            rules.append(CrossFieldRule(kind="sum_to_total", items=r["items"],
                                        item_amount=r["item_amount"], total=r["total"],
                                        tolerance=float(r.get("tolerance", 0.01))))
        elif k == "nonneg":
            rules.append(CrossFieldRule(kind="nonneg", fields=tuple(r["fields"])))
    return tuple(rules)


# --- Pydantic model → CompiledTarget -------------------------------------------
def from_pydantic(model: type[BaseModel], *, name: Optional[str] = None) -> CompiledTarget:
    specs: list[FieldSpec] = []
    for fname, info in model.model_fields.items():
        ann = info.annotation
        is_req = info.is_required()
        base = _strip_optional(ann)
        # repeating: list[SubModel]
        if get_origin(base) is list:
            (item_t,) = get_args(base) or (str,)
            if isinstance(item_t, type) and issubclass(item_t, BaseModel):
                sub = from_pydantic(item_t, name=f"{name or model.__name__}_{fname}_item")
                specs.append(FieldSpec(name=fname, required=is_req, repeats=True,
                                       description=info.description or "",
                                       item_fields=sub.fields))
                continue
        enum = _enum_from_annotation(base)                         # B1: Literal / Enum → enum
        explicit = (info.json_schema_extra or {}).get("x-grounding") \
            if isinstance(info.json_schema_extra, dict) else None
        g = _infer_grounding(fname, base, enum=enum, explicit=explicit)
        specs.append(FieldSpec(name=fname, grounding=g, required=is_req,
                               description=info.description or "",
                               pattern=_pydantic_pattern(info), enum=enum))
    return CompiledTarget(name=name or model.__name__, model=model, fields=tuple(specs))


# --- SQL DDL → CompiledTarget --------------------------------------------------
_SQL_NUM = re.compile(r"\b(int|integer|bigint|smallint|numeric|decimal|float|double|real|money)\b", re.I)
_SQL_DATE = re.compile(r"\b(date|timestamp|datetime)\b", re.I)
_SQL_BOOL = re.compile(r"\bbool", re.I)


def from_sql_ddl(ddl: str, *, name: Optional[str] = None) -> CompiledTarget:
    import sqlglot
    from sqlglot import exp
    parsed = sqlglot.parse_one(ddl, read="postgres")
    table = parsed.find(exp.Table)
    tname = name or (table.name if table else "TargetRecord")
    specs: list[FieldSpec] = []
    model_fields: dict[str, tuple] = {}
    # TABLE-LEVEL constraints (H4): a PRIMARY KEY (a, b) / table CHECK is a sibling of the
    # ColumnDefs, invisible to per-column walks — collect referenced columns here.
    pk_cols: set[str] = set()
    for pk in parsed.find_all(exp.PrimaryKey):
        for c in pk.find_all(exp.Column) or []:
            pk_cols.add(c.name)
        for c in pk.find_all(exp.Identifier) or []:
            pk_cols.add(c.name)
    for col in parsed.find_all(exp.ColumnDef):
        cname = col.name
        dtype = col.args.get("kind")
        dtype_s = dtype.sql() if dtype is not None else "text"
        not_null = (cname in pk_cols) or any(
            isinstance(c.kind, exp.NotNullColumnConstraint)
            for c in col.find_all(exp.ColumnConstraint)) or any(
            col.find_all(exp.PrimaryKeyColumnConstraint))
        # CHECK ... IN ('a','b') → enum
        enum = None
        for chk in col.find_all(exp.CheckColumnConstraint):
            lits = [l.this for l in chk.find_all(exp.Literal) if l.is_string]
            if lits:
                enum = tuple(lits)
        if _SQL_NUM.search(dtype_s):
            py: Any = float
        elif _SQL_DATE.search(dtype_s):
            py = _dt.date
        elif _SQL_BOOL.search(dtype_s):
            py = bool
        else:
            py = str
        g = _infer_grounding(cname, py, enum=enum, explicit=None)
        specs.append(FieldSpec(name=cname, grounding=g, required=not_null, enum=enum))
        model_fields[cname] = (py if not_null else Optional[py], ... if not_null else None)
    model = create_model(tname, **model_fields)  # type: ignore[call-overload]
    return CompiledTarget(name=tname, model=model, fields=tuple(specs))


def coerce_value(value: Any, kind: GroundingKind) -> Any:
    """Coerce a (possibly string) extracted value to the field's declared type for the OUTPUT
    record (a schema with ``total_due: number`` must emit a number, not the captured text).
    Grounding is type-agnostic (it canonicalises), so coercion only shapes the emitted value.
    Falls back to the original value if coercion fails (grounding still gates correctness)."""
    if value is None:
        return None
    try:
        if kind == "number":
            s = re.sub(r"[^\d.\-]", "", str(value).replace(",", ""))
            return float(s) if s not in ("", "-", ".", "-.") else value
        if kind == "bool":
            return str(value).strip().lower() in ("true", "yes", "y", "1")
    except (ValueError, TypeError):
        return value
    return value


def compile_target(spec: Any, *, name: Optional[str] = None) -> CompiledTarget:
    """Dispatch: a Pydantic class, a JSON-Schema dict, or a SQL-DDL string → CompiledTarget."""
    if isinstance(spec, CompiledTarget):
        return spec
    if isinstance(spec, type) and issubclass(spec, BaseModel):
        return from_pydantic(spec, name=name)
    if isinstance(spec, dict):
        return from_json_schema(spec, name=name or "TargetRecord")
    if isinstance(spec, str):
        return from_sql_ddl(spec, name=name)
    raise TypeError(f"compile_target: unsupported schema spec {type(spec).__name__}")
