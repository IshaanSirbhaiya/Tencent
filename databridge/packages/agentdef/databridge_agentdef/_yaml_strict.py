"""A `yaml.safe_load` that refuses a mapping with a duplicate key (finding 8, R-013).

PyYAML's default loader keeps the LAST of a duplicate key and raises nothing - `citation: none`
followed later by `citation: required_per_field` in the same mapping loads as
`required_per_field` with no trace that a reviewer who read the file top to bottom saw a different
rule. In a governance document that is a hazard, not a style nit: the file a human reviewed and the
mapping the compiler evaluates can silently disagree.

This is a loader-level fix rather than a `parse_declaration` one on purpose. By the time YAML has
been parsed into a `dict`, the duplicate is already gone - Python dicts cannot represent "this key
appeared twice". The refusal has to happen during parsing, where both occurrences are still
visible.
"""
from __future__ import annotations

import yaml


class _DuplicateKeyRefusingLoader(yaml.SafeLoader):
    """`yaml.SafeLoader`, with one override: a mapping may not repeat a key.

    Subclassing rather than reimplementing keeps every other `SafeLoader` behaviour (scalar
    resolution, tags, anchors) exactly as `yaml.safe_load` gives it - only mapping construction
    changes.
    """


def _construct_mapping_no_duplicates(loader: yaml.SafeLoader, node: yaml.MappingNode,
                                     deep: bool = False) -> dict:
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                f"found duplicate key {key!r} - a governance document that repeats a key silently "
                f"keeps the LAST value and hides the first from whoever reviewed it top to bottom",
                key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_DuplicateKeyRefusingLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_no_duplicates)


def strict_safe_load(text: str):
    """`yaml.safe_load(text)`, refusing any mapping - at any depth - that repeats a key."""
    return yaml.load(text, Loader=_DuplicateKeyRefusingLoader)
