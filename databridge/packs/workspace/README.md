# packs/workspace — general business starter pack

The closed vocabulary and content screen for Walnut's **MCP-ingested knowledge graph** mode
(`start-walnut-kg.ps1` sets `DATABRIDGE_PACKS=workspace`; `packs/deployment.yaml` is unchanged).

| File | What it is | Read by |
|---|---|---|
| `ontology.yaml` | 10 node kinds (person, organisation, document, invoice, contract, project, meeting, product, location, topic), 11 edge kinds, 1 subject kind (`person`, verified-channel attestation) | `databridge_graph.packs` — extraction may emit only these kinds |
| `screen.yaml` | Deterministic pre-model screen: decisive rules, weighted signals, thresholds, checksum-validated redactions. Schema documented in its header. | the ingest pipeline's screen stage |

It is a **starter**: extend it on `/ontology`. Keep it loadable — the test
`packages/graph/tests/test_workspace_pack_loads.py` checks the ontology binds, the screen parses,
and the demo corpus (`fixtures/workspace_demo/`) screens exactly as `expected_outcomes.yaml` says.

Binding notes: `workspace` binds alone or beside `common`. It cannot bind beside `government`
(both declare subject `person` with different identity guarantees — the loader refuses by design).
