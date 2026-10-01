"""Synthetic-estate provisioning support — the one place that does filesystem/SQLite I/O to stand
up a demo or test estate from a pack's declared DDL and seed files.

`databridge_adapter`'s top-level package docstring is explicit that the package root has no I/O:
"if an `import psycopg` ever appears under this package, the layering ... has been broken." Real
I/O against a live driver belongs in `databridge_adapter.sources` (workstream 3B). This subpackage
is neither the contract nor a production driver: it is the loader that provisions a **synthetic**
estate (fixtures/packs/<pack>/) into SQLite so `sources.postgres.PostgresCore` — or any other real
adapter — can be exercised against real rows without a live PostgreSQL instance. Per repo CLAUDE.md
("synthetic data only"), every row it ever loads is fictional, so shipping it as installable package
code (rather than a test-only helper) is what lets `apps/gateway`'s `/evidence` demo build a real,
private working copy of the estate at runtime — the loader reads the estate's own DDL and seed; it
carries no baked-in schema.

Previously this loader existed as three drifting copies: `packages/adapter/tests/estate.py`,
`packages/materialise/tests/finance_estate.py`, and inline in
`apps/gateway/databridge_gateway/verify_fleet.py`. All three now import from here.
"""
from __future__ import annotations
