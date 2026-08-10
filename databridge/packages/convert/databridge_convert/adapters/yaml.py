"""YAML adapter → TypedRecord (09_FINAL_PRD §5.1, §21.9).

ruamel.yaml SAFE loader only (custom tags refused). Anchors/aliases expand (lossy,
warned). SECURITY: a billion-laughs alias bomb is REFUSED via an anchor/alias-count guard
applied to the RAW bytes BEFORE loading — so the exponential expansion never runs.
"""
from __future__ import annotations

import io
import re
import warnings

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from ..ir import TypedRecord
from ..router.registry import AdapterInfo, register_adapter
from ._util import AdapterError, snapshot_for

_ALIAS_RE = re.compile(rb"(?<![\w*])\*[^\s,\[\]{}]+")   # alias references (YAML anchor chars)
_ANCHOR_RE = re.compile(rb"(?<![\w&])&[^\s,\[\]{}]+")    # anchor definitions
# An exponential alias bomb needs alias-refs ∝ nesting depth, so capping the alias COUNT
# bounds the worst-case expansion (~e^(N/e) nodes). 30 keeps a passing payload under ~60k
# expanded nodes; the post-load node cap is the backstop for anything that slips through.
_MAX_ALIASES = 30
_MAX_NODES = 200_000


def _guard_alias_bomb(data: bytes) -> None:
    if len(_ALIAS_RE.findall(data)) > _MAX_ALIASES or len(_ANCHOR_RE.findall(data)) > _MAX_ALIASES:
        raise AdapterError("yaml: anchor/alias count exceeds limit — refusing "
                           "(billion-laughs guard, §21.9)")


def _guard_node_count(root: object) -> None:
    """Backstop: refuse if the (post-alias-cap, so bounded) structure is still huge."""
    n = 0
    stack: list = [root]
    while stack:
        x = stack.pop()
        n += 1
        if n > _MAX_NODES:
            raise AdapterError("yaml: expanded node count exceeds limit — refusing "
                               "(billion-laughs guard, §21.9)")
        if isinstance(x, dict):
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)


def read(data: bytes) -> TypedRecord:
    _guard_alias_bomb(data)
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    try:
        root = yaml.load(io.BytesIO(data))
    except YAMLError as e:                       # custom tags, syntax → refuse
        raise AdapterError(f"yaml: refused ({e})") from e
    _guard_node_count(root)
    if _ANCHOR_RE.search(data):
        warnings.warn("yaml: anchors/aliases expanded (lossy)", stacklevel=2)
    return TypedRecord(root=root, snapshot=snapshot_for("yaml", data))


def write(ir: TypedRecord) -> bytes:
    yaml = YAML(typ="safe")
    sink = io.BytesIO()
    yaml.dump(ir.root, sink)
    return sink.getvalue()


register_adapter(AdapterInfo(
    format="yaml", modality="structured", ir="TypedRecord", lossiness="lossy",
    limits=["anchors/aliases expanded (lossy, warned)", "custom tags refused",
            "alias-bomb refused"],
    read=read, write=write))
