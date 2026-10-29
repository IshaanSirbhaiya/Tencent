# 01 · How Palantir and Databricks present a "company brain"

Research note for the Walnut DataBridge finals UI (Tencent Cloud AI CAN DO IT Hackathon Singapore 2026, FinTech track, sponsor Aspire).
Written 30 Oct 2026 from official docs, product pages and demo listings. Where a doc did not say something, this note says so rather than guessing.

Scope: Palantir (Foundry, AIP, Ontology, Vertex, Workshop, Quiver, Object Explorer, Data Lineage, AIP Logic, Agent/Chatbot Studio), Databricks (Unity Catalog lineage and Catalog Explorer, AI/BI Genie, AI/BI dashboards, MLflow tracing and agent evaluation, Lakehouse Monitoring / data profiling), then a short look at Glean, Neo4j Bloom and Microsoft Fabric / Copilot.

---

## 0. One-paragraph takeaway

Both leaders sell the same idea we do: one semantic layer where every number has a source, every person sees only what they may, and AI answers are tied back to that layer. They present it with (1) a **search-first entry** that lands you on an **object or table page with tabs**, (2) a **graph that you grow one hop at a time** (never "show me everything"), (3) **node colouring and masking as the way to show permissions**, (4) **AI answers that expose their working** (SQL, reasoning steps, retrieval, citations as clickable bubbles) and (5) a **monitoring / trace view** that turns every answer into an auditable record. The look is dense, quiet, mostly neutral grey with a few semantic colours, almost no motion, and very explicit empty states. Our developer mode should copy these five habits, not their visual style.

---

## 1. Palantir

### 1.1 Mental model Palantir sells

- **Ontology** = semantic layer: datasets become object types, rows become objects, columns become properties, joins become link types. ([What is the Foundry Ontology](https://palantir.com/docs/foundry/learning-application-ontology-01/02), [Ontology Manager overview](https://palantir.com/docs/foundry/ontology-manager/overview/))
- Every object type has an **Overview page with seven sections**: metadata, properties, action types, link type graph, dependents, data (backing datasets), usage. Clicking a property opens a property editor; clicking a link opens a link view with Overview and Datasources pages; action types get Overview / Logic / Observability pages. ([Ontology Manager overview](https://palantir.com/docs/foundry/ontology-manager/overview/))
- This is the closest public analogue to our "every value points to its source": the object type page always shows which dataset backs it, and Data Lineage can show object types next to their backing datasets. ([The Ontology in Data Lineage](https://palantir.com/docs/foundry/learning-application-ontology-01/06), [Explore artifacts and ontology entities](https://palantir.com/docs/foundry/data-lineage/explore-artifacts/))

### 1.2 Object Explorer (search-first entry, object sets, object views)

| Pattern | What the UI does | Source |
|---|---|---|
| Global search bar on top | Type-ahead across objects, object types, saved explorations and modules. First option is literally "Search for…" which opens the results page. | [Getting started](https://palantir.com/docs/foundry/object-explorer/getting-started/) |
| Left side-nav of object type groups | Groups narrow the search before you choose a type. Favourites (star on the card) float to the top. | same |
| **Group graph** button | Shows the link types between object types in a group; clicking a `<->` symbol lists the link types; clicking a node offers "preview type" or "start exploration". | same |
| Object type preview card | Description, properties, linked types, and a **Start Exploration** button, all without leaving the home page. | same |
| Exploration view | Preset, configurable charts/maps that drill into subsets; a **table view**; and a **single Object View** per object. | [Overview](https://palantir.com/docs/foundry/object-explorer/overview/) |
| Histogram-as-filter | Filtering on a string property shows a value histogram you can include/exclude; numeric histograms let you drag a range; grid plots filter by clicking a cell; single-stat cards show count / sum / avg. | [Explore with charts](https://palantir.com/docs/foundry/object-explorer/explore-charts/), [Basic searching and filtering](https://palantir.com/docs/foundry/learning-application-ontology-01/09) |
| Pivot ("search around") | Jump from an object set to its linked objects; compare object sets; run bulk Actions; open set in Quiver; save explorations and lists. | [Overview](https://palantir.com/docs/foundry/object-explorer/overview/) |

**Object View** (the "object card" opened full-screen): every object type ships a default full view with a **Property List** widget of the "prominent" properties and a **Links** widget. Each extra tab is a Workshop module; there is a full and a **panel** form factor; a gear opens "Manage tabs". ([Configured full Object View](https://palantir.com/docs/foundry/object-views/config-object-views/))

### 1.3 Vertex (graph exploration of the ontology)

- Starts as a **blank canvas**; you add starting objects through an object search pop-up ("+ add objects").
- Clicking a node opens a **selection panel with the object's properties**; derived property functions appear at the top of the panel.
- Right-click → menus for selection, layout, exploration. **Basic Search Around** lists related object types **with counts** and a filter icon per type; **Multi-step Search Around** chains link → filter → link, each step's input being the previous step's output; then **Add to graph**.
- A **histogram** of property values for the selected nodes; selecting a bar highlights nodes on the graph.
- Parameters (name, description, default) bind to filters so one slider updates every Search Around; Search Arounds can be saved and reused. ([Explore object relationships](https://palantir.com/docs/foundry/vertex/explore-object-relationships/))

Lesson: the graph is **never shown whole**. It is grown hop by hop, with counts before you expand, and a side panel does the explaining.

### 1.4 Data Lineage (provenance graph)

Node types and indicators ([Graph elements reference](https://www.palantir.com/docs/foundry/data-lineage/elements-reference/)):

- **Data source** node (named as in Data Connection), **Dataset** node (colour follows the chosen colouring; **dashed border** = unstructured data), **Object type** node (icon + colour from the object type definition; link icon shows relations), **Artifact** node (type label at the top of the node).
- Small **indicators** on top of a dataset node: open issues (hover = count), "defines an object type" (hover the right arrow to see which), syncs, and **trashed** (faded node, name struck through).
- Expand arrows on either side of a node pull in upstream / downstream resources one level at a time. ([Node coloring](https://palantir.com/docs/foundry/data-lineage/node-coloring/))

**Node colouring is the governance overlay.** The dropdown "Node color options" offers ~27 schemes; the ones that matter for us:

| Scheme | Shows | Why it matters for Walnut |
|---|---|---|
| **Permissions** + **View as** user | Colours each node by what a chosen user can access. Two modes: *data access* (follows lineage, dataset nodes only, finds the upstream dataset that blocks a user) and *resource access* (the role a user holds, e.g. Editor/Viewer). | This is exactly our permission overlay. |
| Out-of-date | Stale data or stale logic vs parent / ancestor | "Freshness" badge on a value |
| Data Health / Build status | Health-check and build state, grouped nodes show the most severe | Connector health |
| Time last built / Build duration | Recency | "Last synced 3 min ago" |
| User views / Row count / Files | Usage and size | Analytics page |
| Custom colour | Pick nodes, press Color, name the group; a legend appears top-right | Legend pattern |

([Node coloring](https://palantir.com/docs/foundry/data-lineage/node-coloring/), [Check resource permissions](https://palantir.com/docs/foundry/data-lineage/check-permissions/), [Automatic node coloring options](https://palantir.com/docs/foundry/learning-data-lineage/07))

Docs tell teams to **save a lineage graph as the documentation of a pipeline**, which is a nice framing for a demo: the graph *is* the proof. ([Documenting your pipeline with a Data Lineage graph](https://palantir.com/docs/foundry/learning-data-dataeng-03/10))

### 1.5 Workshop and Quiver (how apps and analyses look)

- **Workshop** editor: canvas split into **sections** (toolbar adds sections left/right; sections can have a header and be collapsible; column widths can be absolute pixels), widget list, **right-hand configuration panel**. Everything is wired by **variables** (object set variable → filter output variable → table → "active object" variable → action parameters). Design advice in the docs: put filters in their own section (vertical list or pills), hide the widget header when the section header already names it, use **Blueprint icons** on section headers, sort the table by the property that matters most and put that column first. ([Workshop getting started](https://www.palantir.com/docs/foundry/workshop/getting-started/), [Object Table](https://www.palantir.com/docs/foundry/workshop/widgets-object-table), [Object View widget](https://palantir.com/docs/foundry/workshop/widgets-object-view/))
- **Quiver**: analyses are **cards** on a canvas (Object, Time series, Event set, Chart, Table, Numeric, String…); **canvas mode** for presenting, **graph mode** "for inspecting card dependencies and data flow". Charts embed in Workshop/Notepad; decisions write back through Actions. ([Quiver overview](https://www.palantir.com/docs/foundry/quiver/overview/))

Lesson: even the analysis tool has a **graph mode that shows how every card was derived**. Provenance is a first-class view, not a tooltip.

### 1.6 AIP Logic and AIP Chatbot Studio (formerly Agent Studio) — AI with sources

- AIP Logic: no-code LLM functions built from blocks (e.g. **Use LLM** block with a prompt and tools such as **Query objects** on an object type; typed output variables). **AIP Evals** runs suites, a **results analyzer** groups failures by root cause, metrics track success/failure counts and duration. Ontology edits can be **staged for human review** instead of applied. ([AIP Logic overview](https://www.palantir.com/docs/foundry/logic/overview/))
- Chatbot Studio (renamed from Agent Studio; AIP Agents → AIP Chatbots): config panel with identity (name, description, avatar), model, system prompt (type `/` to reference tools and state), temperature, **conversation starters**, and three kinds of **retrieval context**: Ontology context (fixed N objects or semantic top-K), Document context (full text or relevant chunks), Function-backed context. **View** shows the end-user perspective; **Monitoring** and **Usage** tabs show metrics and thumbs up/down. ([Getting started](https://palantir.com/docs/foundry/agent-studio/getting-started), [Retrieval context](https://www.palantir.com/docs/foundry/agent-studio/retrieval-context), [AIP features](https://palantir.com/docs/foundry/aip/aip-features/))
- **Citations**: the LLM emits XML citation tags; the UI renders each as a **citation bubble** in the answer and a **"Sources" dropdown at the bottom of every message** listing the context used. Clicking an **object citation** opens the object in Object Explorer; a **document citation** opens a dialog at the cited page; an external URL opens the site. Click behaviour can be overridden per object type and can **set a Workshop variable** so a side panel reacts to the clicked citation. ([Citations](https://www.palantir.com/docs/foundry/agent-studio/citations))

### 1.7 Permissions, markings, restricted views

- **Restricted views** give row-level permissions based on user attributes; set one as the backing dataset so Object Explorer only shows the rows a user may see. Docs warn that different users therefore see different rows *and different aggregates*. ([Manage restricted views](https://palantir.com/docs/foundry/platform-security-management/manage-restricted-views/))
- **Property security markings**: a property can carry its own marking label; it is shown with that label in the object view but the value stays hidden unless the user holds the marking. ([Property security markings](https://palantir.com/docs/foundry/security/property-security-markings/))
- Not verified: what an inaccessible object looks like in Object Explorer (hidden vs placeholder). Treat as unknown.

### 1.8 What makes Foundry look enterprise-grade

- Built on Palantir's own **Blueprint** React design system: desktop-first, dense, 14px body with 10/12/14/16px scale, built-in dark theme, icon set used everywhere (section headers, nodes). ([Scaling product design with Blueprint](https://blog.palantir.com/scaling-product-design-with-blueprint-25492827bb4a), [Blueprint](https://blueprintjs.com/))
- Colour is **semantic, not decorative**: node colour means something and always has a legend. Neutral chrome, few accent colours.
- Information is **layered**: list → card preview → full object view → graph; counts shown before expansion; side panels instead of modals.
- Reviewers sometimes call the theme "monotonous" — a reminder that legitimacy comes from consistency, not flourish. ([PeerSpot reviews](https://www.peerspot.com/products/palantir-foundry-reviews))

### 1.9 How Palantir demos to executives

- AIPCon is **customer-led demos** on stage (30 presentations in 2023, ~70 customers in 2024), streamed on Palantir's YouTube channel; CTO demo "AIP Capabilities Demonstration" runs ~20 min. ([BusinessWire 2023](https://www.businesswire.com/news/home/20230907499815/en/Palantir-Customers-to-Feature-Artificial-Intelligence-Platform-in-Action-at-AIPCon-with-30-Presentations-and-Demos), [BusinessWire 2024](https://www.businesswire.com/news/home/20240605318018/en/palantir-to-unveil-new-customers-and-product-announcements-at-aipcon), [Palantir YouTube streams](https://www.youtube.com/@palantirtech/streams))
- The demo recipe visible in those sessions and in the docs: one **fictional but realistic company** (e.g. "Onyx" in AIPCon pre-show material), one operational question, the chatbot answers with citations, the presenter clicks a citation to land on the object, then shows the ontology / lineage behind it, then shows a **staged action waiting for human approval**. ([Third-party summary of AIPCon pre-show](https://morphsys.substack.com/p/ai-agents-and-ontologies-create-a))

---

## 2. Databricks

### 2.1 Catalog Explorer (object page with tabs)

- Entry: **Catalog** icon in the sidebar → tree of catalog → schema → tables/views/volumes/models. Browsing needs only the `BROWSE` privilege and no running compute. ([Catalog Explorer](https://docs.databricks.com/aws/en/catalog-explorer/), [Discover database objects](https://docs.databricks.com/aws/en/discover/database-objects))
- Table page tabs documented: **Columns** (with a **View relationships** button opening an entity-relationship diagram of PK/FK links), **Sample Data** (needs compute; has a natural-language box where Genie writes SQL you can validate before running), **Details** (table properties), **History** (Delta history), **Lineage**, **Permissions**, **Popularity** (most frequent queries and the users who accessed the table in the last 30 days), **Policies** (ABAC row filters / column masks). ([Discover database objects](https://docs.databricks.com/aws/en/discover/database-objects), [Manage permissions](https://docs.databricks.com/catalog-explorer/manage-permissions.html), [ABAC policies](https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/policies))
- **AI-generated comments** on catalogs, tables, columns, volumes and models; Genie Code can answer prompts about lineage and insights. ([Catalog Explorer](https://docs.databricks.com/aws/en/catalog-explorer/))
- Permissions tab: **Grant** → type a user email or group → tick privileges → OK; **Revoke** from the same list. Owners/MANAGE/metastore admins see all grants; everybody else sees only *their own* grants. ([Manage permissions](https://docs.databricks.com/catalog-explorer/manage-permissions.html))
- Column masks return a redacted placeholder such as `XXX-XXX-XXXX` or null to users without access. ([ABAC policies](https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/policies))

### 2.2 Unity Catalog lineage graph

| Pattern | Detail | Source |
|---|---|---|
| Lineage tab first, graph second | The tab lists related tables; **See Lineage Graph** opens the interactive view. | [View data lineage](https://docs.databricks.com/aws/en/data-governance/unity-catalog/data-lineage) |
| One level by default | Click the **+** on a node to reveal more connections. | same |
| Edge click → **Lineage details** panel | Shows source and target tables, and the notebooks / jobs / pipelines / queries that produced the edge, filterable by type. | same |
| Column-level lineage | Click a column to highlight the upstream columns it came from (e.g. a derived `revenue`). | same |
| Node kinds | Tables and views, ML model versions, external assets, file paths. | same |
| **Masked nodes** | Tables you lack `BROWSE`/`SELECT` on appear masked and cannot be expanded through. | same |
| Time range | Dropdown: 1 year default, "All time". | same |
| Retention | Catalog Explorer lineage kept indefinitely (post Sept 2024); system tables keep a rolling year. | same |

Lesson: Databricks shows permission denial **inside the graph** (masked node) instead of hiding the node. The user sees that something exists and that they cannot open it. That is a powerful, honest demo moment.

### 2.3 AI/BI Genie (AI answers with their working shown)

Response anatomy, in order ([Use a Genie Agent](https://docs.databricks.com/aws/en/genie-agents/talk-to-genie)):

1. **Analysis** (expandable): *Understanding the question*, *Found relevant data*, *Referenced SQL example*, *Thinking steps*.
2. **Summary** in plain language.
3. **Result table** with a **Show code** control that reveals the generated SQL.
4. **Visualization** auto-generated; collapsible; edit icon opens a pane with a chart-type dropdown.
5. **Applied filters** as editable chips that re-run the result.
6. **Trusted asset** marking when the answer came from a verified parameterised query or function; **Show more** reveals the author's comments. ([Trusted assets](https://docs.databricks.com/aws/en/genie/trusted-assets))
7. Feedback is not thumbs: **Yes / Fix it (with reasons) / Request review**.
8. Follow-up questions as clickable chips; **Save to dashboard**, CSV/PNG download, copy link, share with *Private / Reviewable by agent managers / All account users*.

Context sidebar: **About** (owner, tags, description, common questions), **Data** (tables, button to Catalog Explorer), **Instructions** (text, joins, SQL expressions, trusted queries/functions). New chats start with sample questions. **Agent mode** (deep-research icon) breaks the question into steps and returns a report with **citations to its research steps** plus a *Download PDF* button; **Answer now** stops reasoning early. ([Genie concepts](https://docs.databricks.com/aws/en/genie/concepts))

Permissions: the generated SQL runs on the author's warehouse, but **Unity Catalog checks the asker's own access**; row filters and column masks apply per user; an unauthorised question returns an empty response. ([Genie concepts](https://docs.databricks.com/aws/en/genie/concepts))

### 2.4 Genie monitoring and benchmarks (the audit of AI)

- **Monitor** tab: every question with its response; filters by **time, rating, user, status**; click → full thread. A **weekly digest** shows message volume, active users, thumbs up/down; **Analyze Agent Usage** opens Genie Code over six months of messages with citations that open the related conversations.
- Private conversations show only the user prompt, not the result (privacy).
- Authors act on a flagged answer: edit and run SQL, **Add as instruction**, **Add as benchmark**, regenerate.
- **Evaluations** tab: timestamped runs with *Execution status*, *Accuracy* (needs manual review when a question had no SQL answer), *Created by*; run detail shows **Model output next to Ground truth**, with Good/Bad labels you can flip. ([Monitor a Genie Agent](https://docs.databricks.com/aws/en/genie-agents/monitor))

### 2.5 AI/BI dashboards

- Canvas with **pages** (tabs), **Data** tab for datasets (UC table, metric view, or SQL), widgets (bar, line, area, scatter, pie, combo, funnel, pivot/detail tables, box plot, histogram, heatmap, choropleth/point maps, **gauge, single-value indicator**).
- Filters at three scopes (global, page, widget); **cross-filtering** by clicking a data point; **drill-through** between pages keeps filter context.
- Publish creates a snapshot with an optional changelog; choose **shared permissions (publisher's access) or individual permissions (viewer's own Unity Catalog access)**; iframe embed; a companion **Genie Agent** on every published dashboard; dashboards can link to a Genie space and pass their filter context. ([Dashboard concepts](https://docs.databricks.com/gcp/en/dashboards/concepts), [AI/BI release notes](https://docs.databricks.com/ai-bi/release-notes/index.html))

### 2.6 MLflow tracing and agent evaluation (how an AI answer becomes an auditable record)

Trace list columns: Trace ID, Request/Response preview, Session, **User**, Execution time, Request time, Run, Source, **State (OK / ERROR / IN_PROGRESS)**, Trace name, Tags, plus one column per assessment with aggregates above the list. Filters: keyword, **AI search in plain English** ("traces slower than 5 seconds"), structured filters on tags/metadata.

Trace detail has two tabs: **Summary** (root inputs/outputs, key spans, exceptions; Default / JSON / Table toggles) and **Details & Timeline** with three panels: left **span tree or waterfall**, centre **span details** (Chat / Inputs-Outputs / Attributes such as `model`, `temperature`, `doc_uri` for retrievers / Events), right **Assessments** with "+ Add new assessment". ([View traces in the UI](https://docs.databricks.com/aws/en/mlflow3/genai/tracing/observe-with-traces/ui-traces), [View MLflow traces](https://docs.databricks.com/aws/en/mlflow/view-traces))

### 2.7 Lakehouse Monitoring (now "Data profiling")

Each monitor writes a **profile metrics** table and a **drift metrics** table (consecutive-window drift, and drift vs a baseline). An **auto-generated dashboard** per profile: metrics grouped in sections, a left list of the metrics used, selector widgets at the top (time window, slice, model), fully editable. ([Lakehouse Monitoring](https://docs.databricks.com/aws/en/lakehouse-monitoring/index), [Monitor metric tables](https://docs.databricks.com/aws/en/lakehouse-monitoring/monitor-output), [Monitoring dashboard](https://docs.databricks.com/aws/en/lakehouse-monitoring/monitor-dashboard))

### 2.8 Audit log as data

`system.access.audit`: `event_time`, `user_identity{email, subject_name}`, `service_name` (e.g. `unityCatalog`), `action_name` (e.g. `getTable`), `request_params` map, `response{statusCode, errorMessage, result}`, `audit_level`, `source_ip_address`, `user_agent`, `session_id`, `request_id`, `identity_metadata{run_by, run_as}`. Databricks presents audit as a **queryable table**, not a bespoke screen; dashboards are built on top. ([Audit log system table](https://docs.databricks.com/aws/en/admin/system-tables/audit-logs))

### 2.9 What makes Databricks look enterprise-grade

- Light, white, **tabbed object pages**; a thin left tree; tables with sortable columns; chips for filters; monospace for SQL and identifiers.
- The AI never answers naked: **Analysis → Summary → Table → Show code → Visualization** is the same shape every time, so viewers learn to trust it.
- Governance is shown as **state on existing elements** (masked node, trusted label, redacted `XXX`), not as separate "security" screens.
- Everything has an **owner, a last-updated time and a popularity count**.

### 2.10 How Databricks demos

Official short videos: [What is AI/BI Genie? (5 min)](https://www.databricks.com/resources/demos/videos/what-aibi-genie), [Databricks basics: AI/BI Genie (9 min)](https://www.databricks.com/resources/demos/videos/databricks-basics-ai-bi-genie), self-paced tour [AI/BI for end users](https://www.databricks.com/resources/demos/tours/bi/ai-bi-for-end-users). Keynote recaps: [theCUBE Research](https://thecuberesearch.com/databricks-keynote-highlights-data-ai-summit-2025/), [Monte Carlo recap](https://www.montecarlodata.com/blog-databricks-data-ai-summit-2025-keynote-recap-the-5-biggest-announcements/). The recurring script: business user asks a question in Genie → shows SQL → shows trusted badge → saves to dashboard → admin flips to Catalog Explorer and shows lineage and the permission that made it safe.

---

## 3. Short looks: Glean, Neo4j Bloom, Microsoft Fabric / Copilot

### 3.1 Glean (enterprise knowledge graph + permissions-aware answers)

- Graph has three pillars: **content** (full text, metadata, permissions), **people** (unified identity, team, role, collaborators), **activity** (engagement; individual data isolated per user, aggregates behind privacy thresholds). ([Knowledge Graph](https://docs.glean.com/security/knowledge-graph))
- Positioning: answers are "permissions-aware" and "fully referenceable back to source documentation"; 275+ connectors. ([Press release](https://www.glean.com/press/glean-introduces-first-enterprise-grade-generative-ai-search-capabilities), [Glean Search](https://www.glean.com/product/search), [Glean Assistant](https://www.glean.com/product/assistant))
- UI detail is thin in public docs; what is consistent: a single search/chat box, answer with inline numbered citations and source cards (app icon, title, owner, last updated), people results alongside documents. Treat exact layout as unverified.

### 3.2 Neo4j Bloom (graph UX reference)

- **Perspectives**: business-specific views of one graph that define categories, visible properties/relationships, **styling (colour, icon, caption)** and saved **search phrases** (parameterised Cypher with a human sentence; suggestions appear while typing; can be launched by URL). Bloom can auto-generate a perspective. ([Perspectives](https://neo4j.com/docs/bloom-user-guide/current/bloom-perspectives/bloom-perspectives))
- UI tour sections: **search bar, card list, legend panel, perspective drawer, scene interactions**; the perspective designer sits left and the **legend panel right**, and rule-based styling (colour/size by property) is set there. ([Visual tour](https://neo4j.com/docs/bloom-user-guide/current/bloom-visual-tour/), [Autumn is Bloom-ing](https://neo4j.com/blog/developer/autumn-is-bloom-ing-with-new-visualization-features/))
- Lesson: a **legend that doubles as a filter** and **sentence-like search phrases** make a graph feel approachable to non-engineers.

### 3.3 Microsoft Fabric OneLake catalog and Microsoft 365 Copilot

- OneLake catalog **Explore tab**: items list (Name, Type, Owner, Refreshed, Location, **Endorsement**, **Sensitivity** with info icon) + filter pane (All / My items / Endorsed / Favorites, workspaces, item type category, tags) + domain selector + an **in-context item details pane** that stays open while you click through the list; details show lineage (list or graph), permissions and monitoring. Items you cannot open still appear so you can **request access**. AI Auto-Summary of a semantic model via Copilot. ([OneLake catalog explore tab](https://learn.microsoft.com/en-us/fabric/governance/onelake-catalog-explore), [Lineage in Fabric](https://learn.microsoft.com/en-us/fabric/governance/lineage))
- Copilot: grounded answers **show the sensitivity label on each citation**, and the whole response inherits the **most restrictive label** among its sources; items the user lacks VIEW/EXTRACT rights for are excluded. ([Purview for Microsoft 365 Copilot](https://learn.microsoft.com/en-us/purview/ai-m365-copilot), [Purview and Copilot](https://learn.microsoft.com/en-us/purview/ai-microsoft-purview))
- Lesson: label the *answer* with the strictest classification of what it used. Cheap to build, very convincing for a FinTech sponsor.

---

## 4. Cross-product pattern table

| Pattern | Palantir | Databricks | Glean / Bloom / Fabric | Walnut equivalent |
|---|---|---|---|---|
| Search-first entry | Global bar with type-ahead across objects and types | Catalog tree + Genie sample questions | Single box; Bloom search phrases | One search box in both modes |
| Object page with tabs | Object View: Property List + Links, extra tabs | Columns / Sample / Details / History / Lineage / Permissions / Popularity | Fabric details pane | Node page: Facts · Sources · Lineage · Who can see · History |
| Graph grown one hop | Vertex Search Around with counts | Lineage `+` one level | Bloom expand | Expand button with count badge |
| Provenance chain | Data Lineage, Quiver graph mode | Column-level lineage, edge → details panel | Fabric lineage list/graph | Value → chunk → document → connector → sync |
| Permissions shown | Node colouring **View as user**; markings labels; restricted views | **Masked nodes**; grants list; `XXX` masks; per-user UC checks in Genie | Request-access rows; Copilot label on citation | "View as" switcher that greys nodes and redacts values |
| AI with sources | Citation bubbles + Sources dropdown; click → object/page | Analysis steps + Show code + trusted label + citations | Numbered citations | Numbered citations → open the exact chunk in the node page |
| Audit of AI | Monitoring/Usage tabs, Evals results analyzer | Genie Monitor + Evaluations; MLflow traces; `system.access.audit` | — | Audit page with hash chain + trace view per answer |
| Time travel | Branches, Out-of-date colouring, Time last built | Delta History tab, lineage time range | Fabric "Refreshed" column | "As of" slider on node page and graph |
| Analytics | Usage section on object type; Node colour by user views | Popularity tab, Lakehouse Monitoring dashboard | Govern tab health | End-to-end analytics page |

---

## 5. What Walnut DataBridge should copy

Ranking: **Impact** = how much it moves a judge in a 5-minute demo (H/M/L). **Effort** = build time in a Next.js 14 + Tailwind app with ~3 days (S ≤ half day, M ≈ 1 day, L > 1 day). Sorted by impact first, then effort. Items 1–12 are the core; 13–22 if time allows.

| # | Recommendation | Where | Impact | Effort | Copied from |
|---|---|---|---|---|---|
| 1 | **Citation bubbles + "Sources" row under every answer.** Numbered `[1] [2]` chips inline; a collapsible *Sources* strip at the bottom listing source title, system icon (Confluence/Jira/Slack/Drive/DB), owner, last-updated. Click → opens the node page scrolled to the exact chunk, highlighted. | User + Dev mode | H | S | Palantir citations; Genie; Copilot |
| 2 | **"Show the working" accordion on each answer**: *Understood question → Searched N sources (K allowed, J hidden by permissions) → Retrieved chunks → Answer*. Collapsed by default in User mode, open in Dev mode. | Both | H | S | Genie *Analysis*; MLflow spans |
| 3 | **"View as" permission overlay on the graph.** Dropdown of demo personas (CFO, Analyst, Intern, Contractor). Nodes the persona cannot open turn grey with a lock badge and cannot be expanded; values are redacted `•••••`. Re-ask the same question as another persona and watch the Sources row shrink. | Dev mode graph + Q&A | H | M | Foundry Permissions colouring + View as; UC masked nodes |
| 4 | **Grow the graph one hop at a time.** Start from the answer's cited nodes, not from the whole graph. Each node has a `+` with a count badge ("+12 linked"). Never auto-render more than ~40 nodes. | Graph explorer | H | M | Vertex Search Around counts; UC lineage `+` |
| 5 | **Provenance chain strip on every value**: `Value ← chunk ← document ← connector ← sync @ time`, drawn as a horizontal breadcrumb of pills; clicking any pill opens that layer. Also as a vertical "lineage" tab on the node page. | Node page | H | S | Column-level lineage; Quiver graph mode |
| 6 | **Node page with tabs**: *Facts* (property list, prominent first) · *Sources* · *Lineage* · *Who can see this* · *History*. Side-panel form factor when opened from the graph, full page via URL. | Dev mode | H | M | Foundry Object View + Catalog Explorer tabs |
| 7 | **Audit log as a table first**, then a hash-chain view: columns `time · user · action · object · allowed? · hash · prev_hash`; filters by user / action / allowed; row click opens the trace of that answer. Add a **"Verify chain" button** that recomputes hashes live and turns green. | Audit page | H | S | `system.access.audit`; Genie Monitor filters |
| 8 | **Semantic colour with a legend, always.** One colour per source system, one hue for permission state (grey = hidden, amber = partial/redacted, green = full), red only for denied/tampered. Legend top-right of the graph; clicking a legend entry filters. | Graph | H | S | Foundry node colouring legend; Bloom legend |
| 9 | **Trusted / verified badge** on answers that came from a curated query or a human-reviewed fact; tooltip shows who verified and when. | Q&A | M | S | Genie trusted assets |
| 10 | **Classification label on the answer** = strictest label among its sources ("Internal · Finance"). Shown as a small pill on the answer header. | Q&A | M | S | Copilot / Purview |
| 11 | **Edge click → details panel.** Clicking a link in the graph opens a right panel: relationship type, which document/row asserted it, confidence, first seen / last seen. | Graph | M | S | UC *Lineage details* panel |
| 12 | **Freshness and health badges**: "synced 3 min ago", stale (amber) if older than threshold, connector error (red). Shown on connector nodes and in the analytics page. | Graph + Analytics | M | S | Foundry Out-of-date / Data Health colouring |
| 13 | **Search-first home in Dev mode**: one bar with type-ahead across nodes, node types, saved explorations; below it, cards of node types with counts and a *Start exploration* button. | Dev mode home | M | M | Object Explorer home |
| 14 | **Histogram-as-filter side panel** for the current node set (by source system, by type, by owner, by date). Click a bar to highlight on the graph. | Graph | M | M | Vertex histogram; Object Explorer charts |
| 15 | **Trace view per answer** (dev mode): left span tree (retrieve → permission filter → rank → generate), centre inputs/outputs for the selected span, right "assessments" (thumbs, reviewer note). Even a static 4-span tree reads as serious engineering. | Dev mode | M | M | MLflow trace detail |
| 16 | **Monitoring page for the AI**: list of every question, with user, time, status, rating; filters; weekly digest tiles (questions, active users, % with ≥1 source, % blocked by permissions). | Analytics | M | M | Genie Monitor |
| 17 | **Request access from a locked node.** Locked node still shows title, type and owner; button "Request access" writes an audit event. | Graph / node page | M | S | Fabric discoverable items; UC masked nodes |
| 18 | **"As of" time slider** on the node page that replays facts and sources as they were at a date; graph edges outside the window fade. Demo with one value that changed last week. | Node page / graph | M | L | Delta History; Foundry branches/out-of-date |
| 19 | **Saved explorations**: save the current graph (nodes, expansions, persona, filters) as a shareable URL; load it as the demo's opening state so nothing is typed live. | Graph | M | S | Object Explorer saved explorations; Bloom search-phrase URLs |
| 20 | **Feedback = Yes / Fix it / Request review**, not thumbs; "Request review" writes to the audit log and shows in Monitoring. | Q&A | L | S | Genie feedback |
| 21 | **Analytics page built from the audit table**: single-value tiles (sources connected, facts, % with provenance, questions answered, denials), one bar by source system, one line of questions per day; all filters at page scope. | Analytics | M | M | AI/BI dashboards; Lakehouse Monitoring dashboard |
| 22 | **Right-hand configuration panel pattern** everywhere (graph node, edge, filter) instead of modals; Blueprint-like density (13–14 px, 8 px grid, 1 px borders, no shadows). | All Dev mode | L | S | Workshop / Blueprint |

### What *not* to copy
- Do not try to render the full knowledge graph as a force-directed hairball. Neither vendor does; it reads as a toy.
- Do not build 27 colouring schemes. Three (source system, permission state, freshness) is enough.
- Do not hide governance on a separate "Security" page. Show it as state on nodes, values and answers.
- Do not animate. The only motion that helps is the live "Verify chain" tick and nodes greying out on persona change.

---

## 6. Five-minute demo story (built from the above)

| Minute | Screen | Beat | Pattern used |
|---|---|---|---|
| 0:00–0:40 | User mode | Persona **Analyst** asks: "What was our Q3 Singapore payment-volume target and who owns it?" Answer streams with `[1] [2] [3]` citations and a *Sources* row (Confluence page, Jira epic, Finance sheet). Classification pill "Internal · Finance". | #1, #10 |
| 0:40–1:20 | User mode | Click `[2]` → node page opens in a side panel scrolled to the highlighted chunk; the *Facts* tab shows the value and the **provenance strip** `value ← cell B14 ← Q3_targets.xlsx ← Google Drive ← synced 09:41`. | #5, #6 |
| 1:20–2:20 | Dev mode graph | Switch to Dev mode. The graph opens from the **saved exploration** with the three cited nodes. Press `+12` on the Finance sheet to pull in the owners and the Slack thread. Click an edge → details panel shows which sentence asserted the ownership. Legend top-right. | #4, #8, #11, #19 |
| 2:20–3:20 | Dev mode graph | **View as → Intern.** Two nodes grey out with locks, redacted values appear. Re-ask the same question: the answer now has one citation and says the rest is not visible to this user. "Same brain, different person, different truth — and nothing leaked." | #3, #17 |
| 3:20–4:10 | Audit | Open the audit table filtered to the last two minutes: both questions, both personas, one `denied` row. Press **Verify chain** → hashes recompute, green. Click a row → the trace view shows retrieve → permission filter → generate spans. | #7, #15 |
| 4:10–5:00 | Analytics | End-to-end page: sources connected, facts with provenance (100 %), questions answered, denials, freshness per connector; close on the line "every number on this page comes from the same audit table you just verified". | #12, #21 |

Rehearsal notes: pre-load the saved exploration URL and both personas; keep the browser at 125 % zoom so the dense UI is legible on a projector; never type a question live, pick it from the sample-question chips (Genie pattern).

---

## 7. Things I could not verify (so the UI team does not over-claim)

- Exact colours Palantir uses for "no access" in Permissions colouring, and what an inaccessible object looks like in Object Explorer.
- Glean's citation layout (public pages describe behaviour, not screens).
- Neo4j Bloom scene-interaction page returned 404; perspective and legend details come from the perspectives page and blog.
- Whether Catalog Explorer previews masked values in Sample Data for a masked user.

## 8. Source index

Palantir: [Object Explorer overview](https://palantir.com/docs/foundry/object-explorer/overview/) · [Object Explorer getting started](https://palantir.com/docs/foundry/object-explorer/getting-started/) · [Explore with charts](https://palantir.com/docs/foundry/object-explorer/explore-charts/) · [Vertex](https://palantir.com/docs/foundry/vertex/explore-object-relationships/) · [Data Lineage node coloring](https://palantir.com/docs/foundry/data-lineage/node-coloring/) · [Graph elements reference](https://www.palantir.com/docs/foundry/data-lineage/elements-reference/) · [Check resource permissions](https://palantir.com/docs/foundry/data-lineage/check-permissions/) · [Explore artifacts and ontology entities](https://palantir.com/docs/foundry/data-lineage/explore-artifacts/) · [Object Views](https://palantir.com/docs/foundry/object-views/config-object-views/) · [Workshop getting started](https://www.palantir.com/docs/foundry/workshop/getting-started/) · [Quiver overview](https://www.palantir.com/docs/foundry/quiver/overview/) · [AIP Logic](https://www.palantir.com/docs/foundry/logic/overview/) · [Chatbot Studio getting started](https://palantir.com/docs/foundry/agent-studio/getting-started) · [Retrieval context](https://www.palantir.com/docs/foundry/agent-studio/retrieval-context) · [Citations](https://www.palantir.com/docs/foundry/agent-studio/citations) · [AIP features](https://palantir.com/docs/foundry/aip/aip-features/) · [Ontology Manager](https://palantir.com/docs/foundry/ontology-manager/overview/) · [Restricted views](https://palantir.com/docs/foundry/platform-security-management/manage-restricted-views/) · [Property security markings](https://palantir.com/docs/foundry/security/property-security-markings/) · [Blueprint blog](https://blog.palantir.com/scaling-product-design-with-blueprint-25492827bb4a) · [Palantir YouTube streams](https://www.youtube.com/@palantirtech/streams)

Databricks: [Data lineage](https://docs.databricks.com/aws/en/data-governance/unity-catalog/data-lineage) · [Catalog Explorer](https://docs.databricks.com/aws/en/catalog-explorer/) · [Discover database objects](https://docs.databricks.com/aws/en/discover/database-objects) · [Manage permissions](https://docs.databricks.com/catalog-explorer/manage-permissions.html) · [ABAC policies](https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/policies) · [Genie concepts](https://docs.databricks.com/aws/en/genie/concepts) · [Use a Genie Agent](https://docs.databricks.com/aws/en/genie-agents/talk-to-genie) · [Monitor a Genie Agent](https://docs.databricks.com/aws/en/genie-agents/monitor) · [Trusted assets](https://docs.databricks.com/aws/en/genie/trusted-assets) · [Dashboard concepts](https://docs.databricks.com/gcp/en/dashboards/concepts) · [AI/BI release notes](https://docs.databricks.com/ai-bi/release-notes/index.html) · [MLflow trace UI](https://docs.databricks.com/aws/en/mlflow3/genai/tracing/observe-with-traces/ui-traces) · [View MLflow traces](https://docs.databricks.com/aws/en/mlflow/view-traces) · [Lakehouse Monitoring](https://docs.databricks.com/aws/en/lakehouse-monitoring/index) · [Monitor output tables](https://docs.databricks.com/aws/en/lakehouse-monitoring/monitor-output) · [Monitoring dashboard](https://docs.databricks.com/aws/en/lakehouse-monitoring/monitor-dashboard) · [Audit log system table](https://docs.databricks.com/aws/en/admin/system-tables/audit-logs) · [What is AI/BI Genie (video)](https://www.databricks.com/resources/demos/videos/what-aibi-genie) · [Databricks basics: Genie (video)](https://www.databricks.com/resources/demos/videos/databricks-basics-ai-bi-genie) · [AI/BI for end users (tour)](https://www.databricks.com/resources/demos/tours/bi/ai-bi-for-end-users)

Others: [Glean Knowledge Graph](https://docs.glean.com/security/knowledge-graph) · [Glean Assistant](https://www.glean.com/product/assistant) · [Glean press release](https://www.glean.com/press/glean-introduces-first-enterprise-grade-generative-ai-search-capabilities) · [Neo4j Bloom perspectives](https://neo4j.com/docs/bloom-user-guide/current/bloom-perspectives/bloom-perspectives) · [Bloom visual tour](https://neo4j.com/docs/bloom-user-guide/current/bloom-visual-tour/) · [Bloom features blog](https://neo4j.com/blog/developer/autumn-is-bloom-ing-with-new-visualization-features/) · [OneLake catalog explore](https://learn.microsoft.com/en-us/fabric/governance/onelake-catalog-explore) · [Lineage in Fabric](https://learn.microsoft.com/en-us/fabric/governance/lineage) · [Purview for Microsoft 365 Copilot](https://learn.microsoft.com/en-us/purview/ai-m365-copilot)
