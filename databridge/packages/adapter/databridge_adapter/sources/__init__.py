"""Real adapter Cores — **the only place in this package that may hold a driver.**

Everything above this directory is a contract: value objects, protocols and pure functions, with a
test (`test_the_contract_package_does_no_io`) asserting that the top level imports no driver, no
socket and no model client. That test globs the top level only, and this subpackage is where the
exception lives — deliberately, and visibly, one directory down.

`ADAPTER_AGENT_SPEC` §1 is explicit that a textual source scan cannot be the boundary for a real
adapter, because a Postgres Core *must* import a driver and a Gmail Core *must* import
`googleapiclient`. The honest boundary is process isolation plus OS credential scoping plus an
egress firewall. What the directory split buys is the smaller, checkable claim: the Shell and the
Policy layer import nothing from here, so the documented path to a source runs through Policy and
Core — which `test_the_shell_holds_no_credentials` asserts structurally rather than by review.

Import the Core explicitly (`from databridge_adapter.sources.postgres import PostgresCore`).
`databridge_adapter/__init__.py` does not re-export it, so importing the contract stays free of
SQLAlchemy.
"""
