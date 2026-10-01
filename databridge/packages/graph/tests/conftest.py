"""Test-collection config for `packages/graph/tests`.

Registers the `slow` marker used by the merge-performance scale test
(`test_merge_scales_without_changing_results.py`) so `pytest -m "not slow"` can deselect it
without an "unknown mark" warning, mirroring how `integration` is registered in the root
`pyproject.toml` for the same reason.
"""
from __future__ import annotations


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: exercises a large synthetic graph to catch an O(n^2) regression; can be "
        "deselected with -m \"not slow\" when wall-clock budget is tight")
