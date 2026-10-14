# Walnut web app (the original screens), running on Company A

This is Walnut's original frontend (Next.js 14, React Flow, d3-force). For the hackathon it runs on
**Company A**, the fictional fintech the Internal Brain uses (`fixtures/enterprise/world.json`), so you
can see the knowledge graph underneath the Internal Brain with the screens Walnut was built with.

Everything below is synthetic. Company A and `companya.example` are made up.

## Start it

From `databridge/` in Git Bash (or any bash):

```bash
bash scripts/run_walnut_original.sh          # build what is missing, then start both servers
bash scripts/run_walnut_original.sh status   # show what is listening on 8300 and 3300
bash scripts/run_walnut_original.sh stop     # stop the processes on 8300 and 3300 only
```

The first run builds the graph (about 30 s), installs the web dependencies and runs `next build`
(a few minutes). Later runs start in seconds. `REBUILD_GRAPH=1` or `REBUILD_WEB=1` forces a rebuild.

| What | Where |
|---|---|
| Web app | http://localhost:3300 |
| Gateway (the original Walnut API) | http://127.0.0.1:8300 |
| Graph file | `var/graph/enterprise.db` |
| Logs | `var/walnut_original/logs/` |

Ports 8100, 8200 and 3100 belong to the Internal Brain. The launcher never touches them, and it
refuses to start on a port that is already in use instead of freeing it.

The first screen asks who is at the terminal. Pick any of the nine Internal Brain personas. That
only labels the session; this app does not filter anything by persona.

## The screens

| Screen | What it shows on Company A |
|---|---|
| `/graph` | The knowledge graph: 1,219 nodes and 2,919 links (people, teams, Confluence spaces, Jira projects, Slack channels, shared drives, and the pages, tickets, threads and files in them). It opens on the best-connected nodes. Search for a node, double-click to expand it, and click any node or link to see the pointer behind each value. |
| `/brain` | The same graph as a company brain, all at once, with a colour legend, counts and a time slider. Drag the slider back and everything created after that date disappears (every Slack thread in the export is from June 2026 or later). Click a node for its evidence sheet: each value next to the file, table, column and row it came from. |
| `/ontology` | The `enterprise` pack: 7 node kinds and 8 link kinds, how they connect, and how many of each are in the graph. |
| `/evidence` | Provenance for one node or link, step by step. Try `/evidence?entity=knowledge_page%3Acf-1001` (the DB Migration Plan page) or the chips on the page. |
| `/trace` | Agent and adapter activity. It stays empty here because nothing is ingested live. |
| `/demo` | The original guided tour: a caption bar walks through the screens above in about 80 seconds. Pause or Skip at any time. |

## Where the data comes from

1. `scripts/build_enterprise_graph.py` flattens `world.json` into row files in
   `fixtures/enterprise/graph_rows/`. It copies no document bodies: labels are titles and names, and
   chat thread labels are the first line of the first message after the Internal Brain's own masking
   (card numbers, CVV, IBAN, NRIC).
2. Walnut's standard loader (`databridge_graph.estate_seed.load_pack_graph`) builds the graph from
   those rows, following `packs/enterprise/seed_map.yaml`. Each node and link cites the row it came
   from. No engine code was changed for this.
3. `packs/enterprise/ui.yaml` holds the words, colours, persona list and tour steps the screens show.

## Limits

- **Local only.** The web build contains the development token `dev-local-only`, and the gateway
  accepts it only because the launcher sets `WALNUT_DEV_AUTH=1`. Do not expose these ports.
- **The graph does not check permissions.** It records who has access to each space (the
  `reads_space` links) but shows every item's title to whoever opens it. The Internal Brain is the
  permission-aware product; this is the estate seen from above.
- Provenance points at rows in the generated JSON files (`table_cell` pointers). There are no
  captured bytes to re-read, so `/evidence` says "shown as recorded" rather than "re-verified".
- Asking questions on `/brain` uses the gateway's own model setting. Without a model key it falls
  back to a fake backend, so the tour does not ask a question.

## Screenshots

`tests/showcase/capture_company_a.cjs` signs in, checks that each screen shows Company A data, and
saves screenshots (1600x1000) to `submission/03_app_scenes/walnut_original/`:

```bash
cd apps/frontend && node tests/showcase/capture_company_a.cjs
```
