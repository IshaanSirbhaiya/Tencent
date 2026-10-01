"""The tabular policy: defaults (`defaults.yaml`) ⊕ dialect `tabular_policy:` ⊕ per-connection file.

Everything the database recipe decides by rule — type classes, prose thresholds, the facts row
cap, the aggregation cardinality band, history-snapshot rules, mapping hints — is data here, so a
new source family is a YAML change, never a Python branch.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Optional

import yaml

_DEFAULTS = Path(__file__).with_name("defaults.yaml")


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def policy_path(source_id: str, root: Optional[Path] = None) -> Path:
    from ..connections import connection_path

    return connection_path(source_id, root).with_name(f"{source_id}.tabular.yaml")


def load_policy(*, dialect: Optional[dict] = None, source_id: Optional[str] = None,
                root: Optional[Path] = None) -> dict:
    data = yaml.safe_load(_DEFAULTS.read_text(encoding="utf-8")) or {}
    if dialect and isinstance(dialect.get("tabular_policy"), dict):
        data = _merge(data, dialect["tabular_policy"])
    if source_id:
        p = policy_path(source_id, root)
        if p.is_file():
            data = _merge(data, yaml.safe_load(p.read_text(encoding="utf-8")) or {})
    return data


def type_class(data_type: Any, policy: dict) -> str:
    """temporal | numeric | boolean | json | text | other — by the declared regexes."""
    t = str(data_type or "").strip().lower()
    for cls in ("temporal", "numeric", "boolean", "json", "text"):
        for rx in (policy.get("type_classes") or {}).get(cls) or []:
            if re.search(rx, t):
                return cls
    return "other"


__all__ = ["load_policy", "type_class", "policy_path"]
