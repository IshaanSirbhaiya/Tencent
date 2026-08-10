# DataBridge Living Wiki — Setup & Operating Manual
# Version 1.0 · The team's shared brain: humans, Claude Code, and any other model
# Goal: context survives compaction, model switches, and people joining/leaving

---

## 0. Why This Exists (read before setting up)

Three failure modes kill startup velocity, and this wiki is engineered against each:

1. **Context compaction loss.** Claude Code compacts long sessions; whatever wasn't written down is gone. The wiki is the externalised context: every session BEGINS by reading state files and ENDS by writing them. The conversation is disposable; the wiki is not.
2. **Cross-model amnesia.** You will use Fable for hard architecture, Sonnet for routine builds, maybe Qwen locally. Models share nothing. The wiki is the shared memory: any model, pointed at the same files, reconstructs the same project state.
3. **Bus factor / onboarding.** A new teammate (human or AI) must reach productive context in under an hour by reading, not by asking.

Design lineage: Garry Tan's **gbrain** pattern — knowledge as Markdown in a **git repo as the system of record**, with deterministic "minion" scripts maintaining structure at zero token cost, and LLMs used only for reasoning over it. We adopt gbrain's architecture directly (it's MIT, proven at YC scale: 146k pages, 97.6% R@5 retrieval) but you can run the identical layout in Obsidian — the vault IS the repo. **Karpathy-style living wiki** means: append-only logs + continuously-rewritten state pages, where the state pages are always current and the logs preserve how we got here.

**One repo, two consumers:** humans read it in Obsidian (nice graph view, backlinks); agents read it as plain files. Markdown + wikilinks is the contract both understand.

---

## 1. Repository Setup (30 minutes)

```bash
# 1. Create the brain repo (SEPARATE from the code repo — different change cadence,
#    different review rules; code PRs shouldn't drown in wiki commits)
git init databridge-brain && cd databridge-brain

# 2. Directory skeleton (create exactly this)
mkdir -p 00-meta 10-product 20-architecture 30-decisions 40-tasks \
         50-research 60-sessions 70-people 80-glossary 90-inbox \
         _minions _templates

# 3. Obsidian: open this folder as a vault. Settings to enforce:
#    - Files & Links → New link format: "Relative path to file"  (agents need real paths)
#    - Files & Links → Use [[Wikilinks]]: ON (humans) — minion converts for portability
#    - Core plugins: Backlinks, Outgoing links, Templates (point at _templates/), Canvas
#    - Community (optional, human-only): Dataview, Excalidraw. NOTHING the wiki
#      depends on may require a plugin — plain markdown must always be sufficient.

# 4. Commit hooks (see §4) and push to a private remote. Every teammate clones it.
#    Claude Code sessions get it mounted/cloned alongside the code repo.
```

### Canonical layout and what lives where

```
databridge-brain/
├── 00-meta/
│   ├── HOW-TO-USE-THIS-WIKI.md      # this file, §§2–6, kept current
│   ├── STATE.md                      # ★ THE single current-truth page (see §2)
│   └── CONVENTIONS.md                # naming, linking, frontmatter rules
├── 10-product/
│   ├── PRD.md                        # mirrored from build docs; wiki copy is canonical
│   ├── positioning.md                # one-liners, pitch fragments, application answers
│   └── customers/                    # one file per design partner/prospect: context,
│                                     #   schemas seen, promises made, gotchas
├── 20-architecture/
│   ├── system-overview.md            # the A.1 diagram + prose, ALWAYS current
│   ├── components/                   # one file per package: harness.md, semantic.md,
│   │                                 #   graph.md, convert.md, evals.md — each contains
│   │                                 #   purpose, interfaces, invariants, gotchas,
│   │                                 #   links to ADRs that shaped it
│   └── model-routing.md              # current task_class→model table + why
├── 30-decisions/                     # ADRs — append-only, never edited after acceptance
│   ├── ADR-0001-monorepo-and-package-boundaries.md
│   ├── ADR-0002-litellm-as-only-model-abstraction.md
│   ├── ADR-0003-bitemporal-graph-graphiti-pattern.md
│   ├── ADR-0004-jsonata-plus-duckdb-for-rule-engine.md
│   ├── ADR-0005-flat-graph-first-no-leiden-in-poc.md
│   └── ...                           # template in _templates/adr.md
├── 40-tasks/
│   ├── BOARD.md                      # kanban as markdown: Backlog/Doing/Review/Done,
│   │                                 #   each item links to its task file
│   └── T01-core-types.md ... T12-cli.md   # one file per TRD task: status, owner,
│                                     #   session links, deviations from TRD, open questions
├── 50-research/                      # distilled deep-research findings (NOT raw dumps)
│   ├── data-bottlenecks.md           # the validated stats + sources
│   ├── company-brain-landscape.md    # gbrain/Graphiti/GraphRAG/LightRAG/Onyx notes
│   ├── data-formats.md               # format×industry matrix; SFT formats; graph formats
│   ├── text-to-sql-sota.md           # techniques, benchmarks, our implications
│   ├── grounding-hallucination.md    # slot-filling, verification, E2/E3 rationale
│   └── tools/                        # one file per dependency: graphify.md,
│                                     #   markitdown.md, litellm.md, airbyte.md, neo4j.md
│                                     #   — version pinned, limits, integration notes
├── 60-sessions/                      # ★ the compaction-survival mechanism (see §3)
│   └── 2026-06-12-T03-postgres-connector.md
├── 70-people/                        # who knows what, who decided what, contacts
├── 80-glossary/                      # GroundedValue, bi-temporal, RRF, task_class...
│                                     #   one concept per file so links resolve precisely
├── 90-inbox/                         # raw captures; minion nags until triaged
├── _minions/                         # deterministic maintenance scripts (§4)
└── _templates/                       # session.md, adr.md, task.md, research.md
```

---

## 2. STATE.md — the page every session reads first

`00-meta/STATE.md` is the project's working memory, rewritten (not appended) so it is ALWAYS current and small enough to read in one breath (<150 lines enforced by minion). Structure:

```markdown
# STATE — updated 2026-06-12T14:30 by claude-code (session 60-sessions/2026-06-12-...)

## Now
Stage: 1 (POC) · Week 3 of 6
Active tasks: [[40-tasks/T07-llm-annotator]] (doing, dev),
              [[40-tasks/T08-knowledge-graph]] (doing, claude-code)

## Last 3 materially important changes
1. E1 fintech fixture at 88% — legacy-schema fixture at 61%, see open question Q-14
2. ADR-0005 accepted: no Leiden communities in POC (cost)
3. Harness fallback chain verified against Ollama qwen3:72b

## Open questions blocking work
- Q-14: legacy cryptic columns — add few-shot from corrections store, or defer? → owner dev
- Q-15: Neo4j CE memory ceiling on fixture graph? measure before T08 done

## Do-not-touch / invariants (enforced; violating these = wrong, see PRD principles)
- No vendor SDK imports outside packages/harness (P2)
- Every emitted value is a GroundedValue (P4)
- Closed vocabularies only change via ADR (P3/P6)

## Next session should
Start T08 retrieval fusion; read [[60-sessions/<last T08 session>]] first.
```

**Rule:** any session that changes project reality updates STATE.md before it ends. The minion (§4) fails the commit if code-affecting session notes exist without a STATE.md touch.

---

## 3. Session Protocol — how compaction-proofing actually works

Every working session (human, Claude Code, any model) follows the same loop. Put this verbatim in your Claude Code project instructions / CLAUDE.md:

```
SESSION START (cheap — ~2k tokens of reading):
1. Read 00-meta/STATE.md
2. Read the task file(s) in 40-tasks/ for what you're working on
3. Read the LAST session note linked from that task file
4. Skim 20-architecture/components/<relevant>.md invariants section
Only then open code.

DURING SESSION:
- Decision with lasting consequence? → draft ADR in 30-decisions/, link it from the task file
- Discovered a gotcha/limit (library quirk, perf cliff)? → append to the relevant
  50-research/tools/*.md or component file IMMEDIATELY (not at session end — compaction
  doesn't warn you)
- New concept/term introduced? → 80-glossary/ stub, even one line

SESSION END (MANDATORY, even mid-task — especially mid-task):
1. Write 60-sessions/YYYY-MM-DD-<task>.md from _templates/session.md:
     ## Did            (bullet facts, link commits in the CODE repo by hash)
     ## Learned        (anything future-you didn't know this morning)
     ## Decided        (links to ADRs, or "none")
     ## Next           (the exact first action of the next session — this line is
                        what makes resumption after compaction instant)
     ## State of mind  (optional, human: doubts, hunches — Karpathy-style honesty)
2. Update the task file status + deviations from TRD
3. Update STATE.md (rewrite, keep <150 lines)
4. Commit brain repo with message: "session: <task> — <one-line outcome>"
```

**Why this defeats compaction:** the expensive thing about compaction isn't losing chat text — it's losing *the plan and the gotchas*. `Next` + STATE.md + task deviations reconstruct the plan in <2k tokens. Gotchas are written the moment they're found. A fresh model cold-starts to full context from four small files.

**Cross-model rule:** session notes record `by:` (human name / model+version). When switching models mid-task (e.g., Sonnet built it, Fable reviews architecture), the incoming model reads the same four files — no special handoff doc needed, the protocol IS the handoff.

---

## 4. Minions — deterministic upkeep at zero token cost (gbrain's key insight)

Scripts in `_minions/`, run by pre-commit hook + nightly cron. NO LLM in any minion — they are parsers and checkers (~ms, free, deterministic), exactly mirroring gbrain's minion/agent split and our own P3.

| Minion | What it does | Failure behaviour |
|---|---|---|
| `links.py` | resolves all [[wikilinks]]/relative links; builds `_generated/backlinks.json` (agents can't see Obsidian's pane — this file is their backlink view) | broken link list → blocks commit |
| `state_guard.py` | STATE.md <150 lines, `updated` timestamp newer than newest session note | blocks commit |
| `session_guard.py` | commits touching 40-tasks/ require a same-day session note | blocks commit |
| `frontmatter.py` | enforces YAML frontmatter (`type`, `status`, `owner`, `updated`) per CONVENTIONS.md | autofix or block |
| `inbox_nag.py` | 90-inbox items older than 7 days → list into STATE.md Open questions | warn |
| `toc.py` | regenerates BOARD.md columns from task frontmatter `status:` | autofix |
| `digest.py` (nightly) | writes `_generated/weekly-digest.md`: sessions, ADRs, STATE diffs this week — the human Monday read & the investor-update raw material | n/a |
| `sync_docs.py` | mirrors PRD/TRD from the code repo's docs/ into 10-product/ & 20-architecture/ (one-way, code repo → brain) so the wiki never drifts from the build contract | warn on divergence |

Retrieval for agents (Phase 2 of the wiki, optional but recommended once the vault grows): index the vault into pgvector/DuckDB-FTS via a nightly minion and expose `wiki_search` as an MCP tool — same hybrid pattern as the product's Task 8, eating our own dogfood. Until then, the layout is small enough that path conventions + backlinks.json suffice.

---

## 5. Writing Conventions (CONVENTIONS.md summary)

- **One concept per file**; filename is the concept (kebab-case). Links beat headings-within-monster-files — graphs and agents both navigate links.
- **Frontmatter on everything:** `type: adr|task|session|research|component`, `status:`, `owner:`, `updated:`.
- **ADRs are append-only.** Superseding = new ADR with `supersedes: ADR-XXXX`. History is the point.
- **State pages rewrite; logs append.** STATE.md, component pages, BOARD.md rewrite. Sessions, ADRs, research notes append. Never mix the modes in one file.
- **Distil research; never paste dumps.** A research page = claims + numbers + source links + "implications for us." Raw exports go to 90-inbox and die there after distillation.
- **Write for the reader with zero context.** Every page's first line says what it is. Assume the reader is a brand-new model instance — because half the time it literally is.

---

## 6. Bootstrap Checklist (first day)

- [ ] Create repo + skeleton + hooks (§1, §4); everyone clones; Obsidian vaults opened
- [ ] Seed 10-product/PRD.md and 20-architecture/ from the build docs (sync_docs minion)
- [ ] Write ADR-0001..0005 retroactively (the decisions already made in the TRD — monorepo, LiteLLM, bi-temporal, JSONata+DuckDB, flat-graph-first) so the decision log starts complete
- [ ] Distil existing deep-research into 50-research/ pages (bottlenecks, landscape, formats, text-to-SQL, grounding, tools/graphify, tools/markitdown)
- [ ] Create 40-tasks/T01..T12 from the TRD with status: backlog
- [ ] Write the first STATE.md
- [ ] Paste the §3 session protocol into the code repo's CLAUDE.md
- [ ] Run one full fake session end-to-end (start ritual → tiny change → end ritual) to verify hooks bite
