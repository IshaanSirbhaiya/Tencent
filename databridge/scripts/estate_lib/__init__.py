"""Provisioning and verification for the Walnut synthetic estates.

Nothing in here knows the name of a table, a row count or a source. Everything is
read from the estate's own `sources.yaml`, its loader scripts, and — after a load —
the database catalog. See `scripts/estates.py` for the CLI.
"""

__all__ = ["discovery", "pg_to_sqlite", "backends", "verify", "probes", "report"]
