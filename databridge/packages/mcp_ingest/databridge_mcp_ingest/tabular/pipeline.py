"""The database recipe end to end, with measured per-stage timings (P17, BUILD_PLAN §11a).

    ingest_database(source_id)
      session   open ONE MCP session (reused by every stage — a postgres-mcp start is seconds)
      profile   row counts, cardinality, prose-vs-numeric, date ranges, history snapshots
      mapping   propose PK/FK → kinds/edges from the profile (rules first, model second); saved
                for approval (auto-approved only when the connection says so)
      graph     the core staged run (`run_ingest`): capture → screen → rows → nodes/edges with
                `table_cell` pointers, driven by the APPROVED mapping
      facts     rows copied read-only into `<source>_facts.sqlite` (aggregated above the cap)
      cards     a schema card per table (+ prose passages) captured, anchored in the graph and
                indexed for retrieval

`run_tabular(...)` runs profile → mapping → facts → cards without the graph stage. Artefacts land
in the run directory (`tabular.report.json`, `profile.json`, `facts.manifest.json`,
`cards.jsonl`), beside the core run's own ledgers when both run.

R-022 f1 #11 — durable and incremental: the run directory is allocated FIRST; each stage's result
is written the moment the stage ends (`tabular.stages.jsonl`, plus its artefact), and a stage that
fails leaves its failure recorded before the error propagates. Downstream stages skip work whose
content hash is unchanged: the facts store is not re-published when every table's content digest
equals the last publication's, and a card or passage is re-indexed only when its sha256 changed.
Profiling always runs — it is how a change is noticed.

R-022 f1 #2 — review-band prose is HELD: only an `allow` verdict is captured and indexed; a
`review` verdict needs a current, authenticated override approval of that exact content
(`screen.approve_override`), else it is recorded in `cards.held.jsonl` and never reaches an
embedding model.

Nothing here names a source: the SQL tool, its argument and the SQL dialect come from the
approved plan's tabular step; every threshold comes from the tabular policy.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import time
from pathlib import Path
from typing import Any, Callable, Optional

from ..client import ReadOnlyClient, open_client
from ..connections import load_connection
from ..crawl import decide, enumerate_tables
from ..ledger import now_iso
from ..plans import load_plan, sql_dialect
from ..runner import PlanNotApproved, source_dir
from ..crawl import item_identity
from ..screen import content_digest, load_overrides, load_screen, screen_text
from . import cards as cards_mod
from .facts import build_facts
from .policy import load_policy
from .profiling import SqlRunner, profile_source


def _snapshots_from_profiles(profiles: list[dict]) -> dict[str, dict]:
    """The shape `mapping.py` reads, carrying MEASURED column stats instead of sampled rows."""
    return {p["ref"]: {"schema": p["schema"], "table": p["table"],
                       "columns": [{"name": c["name"], "type": c["type"],
                                    "unique": c["unique"], "distinct": c["distinct"],
                                    "non_null": c["non_null"]} for c in p["columns"]],
                       "primary_key": p["primary_key"], "foreign_keys": p["foreign_keys"],
                       "row_count_total": p["row_count"],
                       "history_snapshot": p.get("history_snapshot")}
            for p in profiles}


class TabularStage:
    def __init__(self, source_id: str, *, client=None, sources_root: Optional[Path] = None,
                 vocab=None, harness=None, run_dir: Optional[Path] = None,
                 progress: Optional[Callable[[dict], None]] = None) -> None:
        self.source_id = source_id
        self.sources_root = sources_root
        self.conn = load_connection(source_id, sources_root)
        plan = load_plan(source_id, sources_root)
        if plan.status != "approved":
            raise PlanNotApproved(f"the plan for {source_id!r} is {plan.status!r}; approve it "
                                  f"before running the tabular stage")
        steps = [s for s in plan.steps if s.strategy == "tabular" and s.sql is not None]
        if not steps:
            raise PlanNotApproved(f"the approved plan for {source_id!r} has no tabular step")
        self.step = steps[0]
        #: INT5 (R-022 sources cross-track 4): every read-only client this stage builds is bound to
        #: the APPROVED plan, so its SQL tool is pinned to the plan's `sql.tool` / `sql.arg` /
        #: `extra_args` and only `read:`-classified tools may be called.
        self.plan = plan
        # An APPROVED, live-probed dialect for this connection (W4, `dialect_draft`) wins over the
        # plan's named dialect; an unapproved draft is ignored.
        from .dialect_draft import approved_dialect

        drafted = approved_dialect(source_id, sources_root)
        self.dialect = drafted or sql_dialect(self.step.sql.dialect)
        self.dialect_origin = (f"approved draft {drafted.get('name')!r}" if drafted
                               else f"plan dialect {self.step.sql.dialect!r}")
        self.policy = load_policy(dialect=self.dialect, source_id=source_id, root=sources_root)
        self.client = client
        self.vocab = vocab
        self.harness = harness
        self.run_dir = run_dir
        self.timings: dict[str, float] = {}
        self.notes: list[str] = []
        self.progress = progress or (lambda _e: None)
        self.profiles: list[dict] = []
        self.excluded_tables: list[dict] = []
        self.manifest: dict = {}
        self.cards: list[dict] = []
        self.extracted_at = dt.datetime.now(dt.timezone.utc)
        self._runner: Optional[SqlRunner] = None
        self.held: list[dict] = []
        self.stage_log: list[dict] = []

    # -- helpers ---------------------------------------------------------------------------------
    def _time(self, stage: str, t0: float) -> None:
        self.timings[stage] = round(self.timings.get(stage, 0.0) + time.monotonic() - t0, 3)
        self.progress({"stage": stage, "seconds": self.timings[stage]})

    @property
    def runner(self) -> SqlRunner:
        if self._runner is None:
            if self.client is None:
                raise RuntimeError("no open client (use ingest_database / run_tabular)")
            self._runner = SqlRunner(ReadOnlyClient(self.client, plan=self.plan), self.step,
                                     self.dialect)
        return self._runner

    def _vocab(self):
        if self.vocab is None:
            from databridge_graph.packs import active_vocabulary

            self.vocab = active_vocabulary()
        return self.vocab

    # -- durability (R-022 f1 #11) -----------------------------------------------------------------
    def _persist(self, stage: str, status: str, **extra: Any) -> None:
        """Record one stage outcome NOW, in the run directory (when there is one)."""
        rec = {"stage": stage, "status": status, "at": now_iso(), **extra}
        self.stage_log.append(rec)
        if self.run_dir is None:
            return
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with open(self.run_dir / "tabular.stages.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
        artefact = {"profile": ("profile.json", self.profiles),
                    "facts": ("facts.manifest.json", self.manifest)}.get(stage)
        if artefact and status == "ok":
            (self.run_dir / artefact[0]).write_text(json.dumps(artefact[1], indent=1,
                                                               default=str), encoding="utf-8")
        if stage == "cards" and status == "ok":
            self._write_cards(self.run_dir)

    def _write_cards(self, run_dir: Path) -> None:
        with open(run_dir / "cards.jsonl", "w", encoding="utf-8") as f:
            for c in self.cards:
                f.write(json.dumps(c, sort_keys=True, default=str) + "\n")
        with open(run_dir / "cards.held.jsonl", "w", encoding="utf-8") as f:
            for h in self.held:
                f.write(json.dumps(h, sort_keys=True, default=str) + "\n")

    def attach(self, run_dir: Path) -> None:
        """Start recording into `run_dir`, writing the stage outcomes recorded so far (in memory)
        first — for a RESUMED run, whose directory is not touched until its core run has
        accepted the resume (FIN-14: a refused resume leaves the interrupted run as it was)."""
        self.run_dir = run_dir
        run_dir.mkdir(parents=True, exist_ok=True)
        with open(run_dir / "tabular.stages.jsonl", "a", encoding="utf-8") as f:
            for rec in self.stage_log:
                f.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
        if any(r.get("stage") == "profile" and r.get("status") == "ok" for r in self.stage_log):
            (run_dir / "profile.json").write_text(json.dumps(self.profiles, indent=1,
                                                             default=str), encoding="utf-8")

    def run_stage(self, stage: str, fn: Callable[..., Any], *a: Any, **kw: Any) -> Any:
        """Run one stage; its outcome — or its failure — is on disk before this returns/raises."""
        try:
            out = fn(*a, **kw)
        except BaseException as exc:
            self._persist(stage, "failed", error=f"{type(exc).__name__}: {str(exc)[:500]}")
            if self.run_dir is not None:
                (self.run_dir / "tabular.report.json").write_text(json.dumps({
                    "kind": "tabular", "source_id": self.source_id, "status": "failed",
                    "failed_stage": stage, "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                    "stages": self.stage_log, "notes": self.notes, "finished_at": now_iso()},
                    indent=1, default=str), encoding="utf-8")
            raise
        self._persist(stage, "ok", seconds=self.timings.get(stage))
        return out

    # -- stages ----------------------------------------------------------------------------------
    def profile(self) -> list[dict]:
        t0 = time.monotonic()
        tables: list[tuple[str, str]] = []
        for item in enumerate_tables(ReadOnlyClient(self.client, plan=self.plan), self.step,
                                     self.source_id):
            d = decide(item, self.conn.scope, collected_so_far=len(tables))
            if d.outcome != "collect":
                self.excluded_tables.append({"table": item.uri, "rule": d.rule,
                                             "reverse": d.reverse})
                continue
            tables.append((item.ctx.get("schema") or "", str(item.ctx.get("table"))))
        self.profiles = profile_source(self.runner, tables, self.policy)
        self.notes.extend(n for n in self.runner.notes if n not in self.notes)
        self._time("profile", t0)
        return self.profiles

    def propose_mapping(self):
        from ..mapping import ensure_mapping

        t0 = time.monotonic()
        m = ensure_mapping(self.source_id, _snapshots_from_profiles(self.profiles), self._vocab(),
                           harness=self.harness, auto_approve=False,
                           root=self.sources_root)
        self._time("mapping", t0)
        return m

    def facts(self, *, force: Any = None, force_digest: Optional[str] = None) -> dict:
        """`force`: the AUTHENTICATED principal overriding a refusal (R-022 f2 #5) — never a
        Boolean; `force_digest` binds it to the candidate content it saw refused."""
        t0 = time.monotonic()
        self.manifest = build_facts(self.source_id, self.runner, self.profiles, self.policy,
                                    extracted_at=self.extracted_at, force=force or None,
                                    force_digest=force_digest)
        if self.manifest.get("status") != "ok":
            self.notes.append(f"facts store NOT published: {self.manifest.get('refused')}")
        self._time("facts", t0)
        return self.manifest

    def build_cards(self, store=None, *, index: bool = True) -> list[dict]:
        from ..mapping import effective_mapping, load_mapping

        t0 = time.monotonic()
        # R-022 f2 #13: the EFFECTIVE mapping — an approval that does not verify (a hand-edited
        # `status: approved`, a changed digest, a column the live table lacks) is shown as what it
        # is, a proposal, never as an approved kind or identifier claim
        mapping = effective_mapping(load_mapping(self.source_id, self.sources_root),
                                    self._vocab(), self.sources_root,
                                    snapshots=_snapshots_from_profiles(self.profiles))
        by_table = {t["source_table"]: t for t in self.manifest.get("tables") or []}
        # an UNCHANGED facts store (same content digest) keeps the date its figures were measured
        # at, so an identical re-run renders identical cards (nothing to re-index, #11)
        when = (str(self.manifest.get("extracted_at")) if self.manifest.get("unchanged")
                else self.extracted_at.isoformat(timespec="seconds"))
        screen_cfg = load_screen()
        overrides = load_overrides(self.source_id, self.sources_root)
        self.held = []
        items: list[dict] = []
        for p in self.profiles:
            prose_note, prose_item = None, None
            if p.get("prose_columns") and not (p.get("history_snapshot") or {}).get("is"):
                text, prose_note = cards_mod.prose_text(self.runner, p, self.policy)
                if text:
                    verdict = screen_text(text, screen_cfg)
                    uri = cards_mod.prose_uri(self.source_id, p["ref"])
                    item_id = item_identity(self.source_id, uri)
                    digest = content_digest(text)
                    approved = verdict.decision == "review" and overrides.allows(item_id, digest)
                    if verdict.decision == "block":
                        prose_note += (" The prose was BLOCKED by the screen "
                                       f"({verdict.as_record().get('fired')}); not searchable.")
                    elif verdict.decision != "allow" and not approved:
                        # R-022 f1 #2: the review band is a human gate — HELD, not published
                        fired = sorted({str(f.get("rule")) for f in verdict.fired})
                        prose_note += (f" The prose is HELD for review by the screen (rules "
                                       f"{fired}); not searchable until an owner approves this "
                                       f"exact content.")
                        self.held.append({
                            "item_id": item_id, "uri": uri, "table": p["ref"],
                            "digest": digest, "decision": verdict.decision, "rules": fired,
                            "reverse": (f"screen.approve_override({self.source_id!r}, "
                                        f"{item_id!r}, digest={digest!r}, principal=<the "
                                        f"authenticated owner>, reason=...) and re-run")})
                    else:
                        if approved:
                            prose_note += " Review-band prose published under an approved override."
                        prose_item = cards_mod.capture_text_item(
                            self.source_id, cards_mod.prose_uri(self.source_id, p["ref"]),
                            f"{p['ref']} — prose ({', '.join(p['prose_columns'])})",
                            verdict.masked_text if verdict.masked_text else text,
                            retrieved_at=when, mime="text/plain")
                        prose_item.update(kind="prose", table=p["ref"], mime="text/plain")
            tm = mapping.tables.get(p["ref"])
            text = cards_mod.card_text(
                self.source_id, p, by_table.get(p["ref"]), extracted_at=when,
                facts_file=(self.manifest.get("facts_file")
                            if self.manifest.get("status") == "ok" else None),
                mapping=tm.model_dump() if tm else None,
                graph_row_cap=self.conn.scope.max_rows_per_table, prose_note=prose_note)
            # R-022 f2 #4: the card is rendered from SOURCE-CONTROLLED names (tables, columns,
            # types, ranges), so its exact text is screened like any other content before it is
            # captured, anchored or indexed: `block` is excluded, `review` is held unless an
            # authenticated override approved this exact text, `allow` publishes the masked text
            card_uri = cards_mod.card_uri(self.source_id, p["ref"])
            card_id = item_identity(self.source_id, card_uri)
            cverdict = screen_text(text, screen_cfg)
            cdigest = content_digest(text)
            capproved = cverdict.decision == "review" and overrides.allows(card_id, cdigest)
            if cverdict.decision != "allow" and not capproved:
                fired = sorted({str(f.get("rule")) for f in cverdict.fired})
                self.held.append({
                    "item_id": card_id, "uri": card_uri, "table": p["ref"], "kind": "schema_card",
                    "digest": cdigest, "decision": cverdict.decision, "rules": fired,
                    "reverse": ("nothing: the screen BLOCKED it" if cverdict.decision == "block"
                                else f"screen.approve_override({self.source_id!r}, {card_id!r}, "
                                     f"digest={cdigest!r}, principal=<the authenticated owner>, "
                                     f"reason=...) and re-run")})
            else:
                card = cards_mod.capture_text_item(
                    self.source_id, card_uri, f"Schema card — {p['ref']}",
                    cverdict.masked_text if cverdict.masked_text else text, retrieved_at=when)
                card.update(kind="schema_card", table=p["ref"], mime=cards_mod.CARD_MIME)
                items.append(card)
            if prose_item:
                items.append(prose_item)
        self._time("cards_capture", t0)
        if store is not None:
            t0 = time.monotonic()
            anchors = {a["item_id"]: a for a in cards_mod.anchor_cards(
                store, self._vocab(), self.source_id,
                [i for i in items if i["kind"] == "schema_card"], retrieved_at=self.extracted_at,
                document_kind=self.conn.document_kind)}
            for i in items:
                i["anchor"] = (anchors.get(i["item_id"]) or {}).get("anchor")
            self._time("cards_graph", t0)
        if index:
            t0 = time.monotonic()
            # R-022 f1 #11: re-index only what changed (content-addressed by sha256)
            state_p = source_dir(self.source_id) / "tabular.index_state.json"
            try:
                state = json.loads(state_p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                state = {}
            todo = [i for i in items if state.get(i["item_id"]) != i["sha256"]]
            if todo:
                self.index_result = cards_mod.index_items(todo, self.source_id)
                # R-022 f2 #12: record ONLY what retrieval says it accepted — an outage or an
                # error leaves the item unrecorded, so the next run indexes it
                accepted = set((self.index_result or {}).get("indexed_ids") or [])
                for i in todo:
                    if i["item_id"] in accepted:
                        state[i["item_id"]] = i["sha256"]
                state_p.parent.mkdir(parents=True, exist_ok=True)
                state_p.write_text(json.dumps(state, sort_keys=True, indent=1), encoding="utf-8")
            else:
                self.index_result = {"indexed": 0, "status": "unchanged: every card and passage "
                                                             "is already indexed at this content"}
            self.index_result["unchanged"] = len(items) - len(todo)
            self.notes.append(f"retrieval: {self.index_result}")
            self._time("cards_index", t0)
        self.cards = items
        return items

    # -- artefacts -------------------------------------------------------------------------------
    def write_artefacts(self, run_dir: Path, *, standalone: bool) -> dict:
        run_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "kind": "tabular", "source_id": self.source_id, "run_id": run_dir.name,
            "extracted_at": self.extracted_at.isoformat(timespec="seconds"),
            "dialect": getattr(self, "dialect_origin", None),
            "combined_profiling": bool(self._runner and self.runner.combined
                                       and self.runner.has("table_stats")),
            "durations": self.timings, "sql_calls": self.runner.calls if self._runner else 0,
            "sql_seconds": round(self.runner.seconds, 3) if self._runner else 0.0,
            "tables_profiled": len(self.profiles), "tables_excluded_by_scope":
                self.excluded_tables,
            "history_snapshots": [p["ref"] for p in self.profiles
                                  if (p.get("history_snapshot") or {}).get("is")],
            "facts": {k: self.manifest.get(k) for k in ("status", "refused", "facts_file",
                                                        "sha256", "rows_total", "problems")},
            "cards": len([c for c in self.cards if c.get("kind") == "schema_card"]),
            "prose_documents": len([c for c in self.cards if c.get("kind") == "prose"]),
            "notes": self.notes, "finished_at": now_iso(),
            # §5.8 (E2E F-33): profiling, facts and cards are rule-driven; a model is consulted
            # only by the mapping PROPOSAL, which records its own origin per table
            "produced_by": {"component": "databridge_mcp_ingest.tabular", "kind": "rule",
                            "run_id": run_dir.name, "models_used": []}}
        report["prose_held_for_review"] = len(self.held)
        report["stages"] = self.stage_log
        (run_dir / "profile.json").write_text(json.dumps(self.profiles, indent=1, default=str),
                                              encoding="utf-8")
        (run_dir / "facts.manifest.json").write_text(
            json.dumps(self.manifest, indent=1, default=str), encoding="utf-8")
        self._write_cards(run_dir)
        (run_dir / "tabular.report.json").write_text(json.dumps(report, indent=1, default=str),
                                                     encoding="utf-8")
        if standalone:
            ok = self.manifest.get("status") == "ok"
            (run_dir / "report.json").write_text(json.dumps({
                **report, "status": "ok" if ok else "failed",
                "message": (f"{len(self.profiles)} tables profiled, "
                            f"{self.manifest.get('rows_total', 0)} rows in the facts store, "
                            f"{report['cards']} schema cards") if ok else
                           f"facts store not published: {self.manifest.get('refused')}",
                "counts": {"tables": len(self.profiles), "cards": report["cards"],
                           "facts_rows": self.manifest.get("rows_total", 0)}},
                indent=1, default=str), encoding="utf-8")
        return report


@contextlib.contextmanager
def _session(conn, client):
    if client is not None:
        yield client
        return
    with open_client(conn) as c:
        yield c


def run_tabular(source_id: str, *, store=None, client=None, vocab=None, harness=None,
                sources_root: Optional[Path] = None, index: bool = True,
                force_publish: Any = None) -> dict:
    """profile → mapping → facts → cards, standalone (no graph rows)."""
    from ..runner import _new_run_id

    stage = TabularStage(source_id, client=client, sources_root=sources_root, vocab=vocab,
                         harness=harness)
    # R-022 f1 #11: the run directory exists BEFORE the first stage, so every stage's outcome
    # (or failure) is recorded as it happens
    run_dir = source_dir(source_id) / f"{_new_run_id()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    stage.run_dir = run_dir
    t0 = time.monotonic()
    with _session(stage.conn, client) as c:
        stage.client = c
        stage._time("session_open", t0)
        stage.run_stage("profile", stage.profile)
        stage.run_stage("mapping", stage.propose_mapping)
        stage.run_stage("facts", stage.facts, force=force_publish)
        stage.run_stage("cards", stage.build_cards, store, index=index)
    return stage.write_artefacts(run_dir, standalone=True)


def ingest_database(source_id: str, store=None, harness=None, *, client=None, vocab=None,
                    sources_root: Optional[Path] = None, index: bool = True,
                    force_publish: Any = None,
                    progress_cb: Optional[Callable[[dict], None]] = None,
                    run_id: Optional[str] = None) -> dict:
    """The whole database recipe on ONE session: profile → mapping → graph → facts → cards.
    Returns {"run": core RunReport dict, "tabular": tabular report, "timings": per stage}.

    `run_id`: the sync's run id (a caller that already announced one, or a resume); a new one
    otherwise. The recipe's artefacts and the core run share THIS one directory (FIN-14)."""
    from ..runner import _new_run_id, run_dir_for, run_ingest

    if store is None:
        from databridge_graph.store import GraphStore

        from .. import paths

        db = paths.graph_db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        store = GraphStore(str(db))
    stage = TabularStage(source_id, client=client, sources_root=sources_root, vocab=vocab,
                         harness=harness, progress=progress_cb)
    # R-022 f1 #11: allocate the run FIRST (the core run then uses the same id and directory)
    run_id = run_id or _new_run_id()
    run_dir = run_dir_for(source_id, run_id)
    from ..runner import _is_core_run_dir

    # FIN-14: a RESUME is recorded in memory until the core run accepts it (then `attach`); a
    # fresh sync records into its run directory from the first stage, as before
    resume = _is_core_run_dir(run_dir)
    stage.run_dir = None if resume else run_dir
    if not resume:
        run_dir.mkdir(parents=True, exist_ok=True)
    t_all = time.monotonic()
    with _session(stage.conn, client) as c:
        stage.client = c
        stage._time("session_open", t_all)
        stage.run_stage("profile", stage.profile)
        stage.run_stage("mapping", stage.propose_mapping)
        t0 = time.monotonic()
        report = stage.run_stage("graph_run", run_ingest, source_id, store, harness, progress_cb,
                                 run_id=run_id, client=c, sources_root=sources_root,
                                 vocab=stage._vocab())
        stage._time("graph_run", t0)
        if stage.run_dir is None:
            stage.attach(run_dir)
        stage.run_stage("facts", stage.facts, force=force_publish)
        stage.run_stage("cards", stage.build_cards, store, index=index)
    tab = stage.write_artefacts(Path(report.run_dir), standalone=False)
    timings = {**stage.timings, "graph_run.stages": report.durations,
               "total": round(time.monotonic() - t_all, 3)}
    return {"run": report.as_dict(), "tabular": tab, "timings": timings}


__all__ = ["TabularStage", "run_tabular", "ingest_database"]
