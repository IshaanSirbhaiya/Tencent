"""Shared fixtures. No network, no model: ingestion is a `FakeIngestService` that writes the real
§5.2 artefacts — with `databridge_mcp_ingest`'s own field names and document identity — into a
temp dir, and is incremental by content hash, like the real one. `test_aa_contract_mcp_ingest.py`
checks the same manager against a REAL `run_ingest` run, so this fake cannot drift unnoticed."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

_PKG = Path(__file__).resolve().parents[1]
_PACKAGES = _PKG.parent
for p in (_PKG, _PACKAGES / "workflow_spec", _PACKAGES / "core", _PACKAGES / "graph",
          _PACKAGES / "mcp_ingest"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from aa_testkit import CHUNK, item_id, item_uri, refusal_reason  # noqa: E402,F401
from databridge_adapter_agents import (AdapterAgentManager, AdapterAgentRegistry,  # noqa: E402
                                       ProposalLog, ProposalPolicy, read_run_reports)

T0 = dt.datetime(2026, 10, 1, 9, 0, tzinfo=dt.timezone.utc)


class Clock:
    def __init__(self, t: dt.datetime = T0) -> None:
        self.t = t

    def __call__(self) -> dt.datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t = self.t + dt.timedelta(**kw)


class FakeStore:
    """Upsert semantics: writing the same node twice leaves one node."""

    def __init__(self) -> None:
        self.nodes: set[str] = set()


class FakeIngestService:
    """Per-connection behaviour, declared as data. Writes the SAME §5.2 artefacts, with the same
    field names, that `databridge_mcp_ingest.runner.run_ingest` writes (report.json with
    `status` ok|empty|failed|unverified, `started_at`/`finished_at`, verify-style `counts`;
    captured / governance_chunks / refused ledgers), and emits `run_completed`/`run_failed` for
    every run it starts, as mcp_ingest does (§12 event ownership).

    items:        {rel_path: text}        what the source holds (content-hashed, incremental)
    plan:         "approved" | "proposed" | None
    raises:       raised BEFORE the run starts (no run dir, no report, no event)
    fail_in_run:  raised AFTER a failed report is written and run_failed emitted (mcp_ingest's
                  own in-run failure path)
    status:       override the report's status word; `OMIT` leaves the key out entirely
    no_report:    run, but write no report.json
    refused:      [{doc: rel_path, kind, entity?, chunk?, axis?}] written in mcp_ingest's format
    refused_raw:  records written to refused.jsonl verbatim
    """

    OMIT = object()

    def __init__(self, root: Path, clock: Clock, events_path: Optional[Path] = None) -> None:
        self.root = root
        self.clock = clock
        self.events_path = events_path
        self.config: dict[str, dict[str, Any]] = {}
        self.seen: dict[str, set[str]] = {}
        self.calls: list[str] = []
        self.harnesses: list[Any] = []
        self._n = 0

    def configure(self, conn: str, **kw) -> None:
        self.config.setdefault(conn, {"items": {}, "plan": "approved"}).update(kw)

    def plan_status(self, source_id: str) -> Optional[str]:
        return self.config.get(source_id, {}).get("plan")

    def _event(self, type_: str, source_id: str, run_id: str, attrs: dict) -> None:
        if self.events_path is None:
            return
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"event_id": f"ingest-{run_id}-{type_}", "ts": self.clock().isoformat(),
               "type": type_, "source_id": source_id, "item_id": None, "uri": None,
               "content_hash": None, "run_id": run_id, "attrs": attrs}
        with open(self.events_path, "ab") as fh:
            fh.write(json.dumps(rec).encode() + b"\n")

    def run_ingest(self, source_id: str, *, store: Any, harness: Any,
                   progress_cb: Optional[Callable[[dict], None]] = None) -> Any:
        self.calls.append(source_id)
        self.harnesses.append(harness)
        cfg = self.config[source_id]
        if cfg.get("raises"):
            raise cfg["raises"]
        self._n += 1
        run_id = f"2026{self._n:04d}T000000Z-run{self._n:04d}"
        run_dir = self.root / source_id / run_id
        run_dir.mkdir(parents=True)
        started = self.clock().isoformat()
        seen = self.seen.setdefault(source_id, set())
        items = cfg.get("items", {})
        ids = {rel: item_id(source_id, item_uri(source_id, rel)) for rel in items}
        hashes = {rel: hashlib.sha256(t.encode()).hexdigest() for rel, t in items.items()}
        new = [rel for rel, h in hashes.items() if h not in seen]
        for rel in new:
            store.nodes.add(f"{source_id}:{rel}")
            seen.add(hashes[rel])
        if harness is not None and new and hasattr(harness, "complete_structured"):
            for rel in new:                       # one "extraction call" per built document
                harness.complete_structured("entity_extraction", [], None)
        if progress_cb:
            progress_cb({"stage": "build", "done": len(new)})

        def write(name: str, recs: list) -> None:
            (run_dir / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs),
                                                   encoding="utf-8")

        # every field `databridge_mcp_ingest.records` requires — the shape the real runner writes
        at = self.clock().isoformat()
        write("inventory", [{"item_id": ids[r], "source_id": source_id, "step": "fake",
                             "kind": "document", "uri": item_uri(source_id, r), "name": r,
                             "rel": r, "mime": "text/plain", "size": len(items[r]),
                             "decision": "collect"} for r in items])
        write("captured", [{"item_id": ids[r], "uri": item_uri(source_id, r), "name": r,
                            "kind": "document", "sha256": hashes[r], "bytes": len(items[r]),
                            "mime": "text/plain", "step": "fake", "at": at} for r in items])
        chunks = []
        for rel in items:
            n = len(items[rel])
            for i, start in enumerate(range(0, max(n, 1), CHUNK)):
                chunks.append({"item_id": ids[rel], "chunk": i, "char_start": start,
                               "char_end": min(start + CHUNK, n), "decision": "allow"})
        write("governance_chunks", chunks)
        for ledger in ("excluded", "blocked", "extract_skipped", "governance"):
            write(ledger, cfg.get(f"{ledger}_records", []))
        refused = [{"item_id": ids[r["doc"]], "reason": refusal_reason(r)}
                   for r in cfg.get("refused", [])] + list(cfg.get("refused_raw", []))
        write("refused", refused)

        if cfg.get("fail_in_run"):
            report = {"source_id": source_id, "run_id": run_id, "run_dir": str(run_dir),
                      "status": "failed", "message": str(cfg["fail_in_run"]), "counts": {},
                      "started_at": started, "finished_at": self.clock().isoformat()}
            self._event("run_failed", source_id, run_id, {"error": report["message"]})
            (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
            raise cfg["fail_in_run"]

        counts = {"inventory": len(items), "captured": len(items),
                  "unchanged": len(items) - len(new), "extracted": len(new),
                  "built": len(new), "entities": len(new), "relations": 0,
                  "excluded": len(cfg.get("excluded_records", [])),
                  "blocked": len(cfg.get("blocked_records", [])), "failed": 0,
                  "refused": len(refused)}
        status = cfg.get("status", "ok" if items else "empty")
        report = {"source_id": source_id, "run_id": run_id, "run_dir": str(run_dir),
                  "message": "", "counts": counts, "durations_s": {}, "verify": {"ok": True},
                  "notes": [], "started_at": started,
                  "finished_at": (self.clock() + dt.timedelta(seconds=5)).isoformat(),
                  "plan": {}}
        if status is not self.OMIT:
            report["status"] = status
        self._event("run_completed" if status in ("ok", "empty") else "run_failed", source_id,
                    run_id, {"status": None if status is self.OMIT else status})
        if not cfg.get("no_report"):
            (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
        return dict(report)

    def list_runs(self, source_id: str):
        return read_run_reports(source_id, self.root)


class FakeVocabulary:
    def __init__(self, nodes=(), edges=(), subjects=()) -> None:
        self.nodes, self.edges, self.subjects = set(nodes), set(edges), set(subjects)
        self.problem = None

    def known_kinds(self, axis: str) -> set[str]:
        return set(self.nodes if axis == "node" else self.edges)

    def subject_kinds(self) -> set[str]:
        return set(self.subjects)


def make_spec(agent_id: str = "larkspur_sources", connections=("files-a", "db-b"),
              plans: Optional[dict] = None, **extra) -> dict:
    return {"spec": "adapter_agent/v1", "id": agent_id, "title": "Larkspur sources",
            "created_from": "Keep the files folder and the CRM database in the graph; check hourly",
            "connections": list(connections),
            "plans": plans if plans is not None else {c: "approved" for c in connections},
            "sync": {"type": "schedule", "schedule": {"cron": "0 * * * *", "timezone": "UTC"}},
            "extract": {"pack": "workspace"}, "freshness": {"max_age_hours": 24},
            "status": "active", **extra}


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name, sub in (("WALNUT_ADAPTER_AGENTS_DIR", "agents"), ("WALNUT_RUNS_DIR", "runs"),
                      ("WALNUT_EVENTS_PATH", "events/events.jsonl"),
                      ("WALNUT_INGEST_DIR", "ingest")):
        monkeypatch.setenv(name, str(tmp_path / sub))
    clock = Clock()
    service = FakeIngestService(tmp_path / "ingest", clock,
                                events_path=tmp_path / "events" / "events.jsonl")
    store = FakeStore()
    vocab = FakeVocabulary(nodes={"person", "organisation", "document"},
                           edges={"works_for"}, subjects={"person"})
    registry = AdapterAgentRegistry(tmp_path / "agents")
    manager = AdapterAgentManager(service=service, registry=registry, store=store,
                                  runs_root=tmp_path / "runs",
                                  events_path=tmp_path / "events" / "events.jsonl",
                                  proposal_policy=ProposalPolicy(min_occurrences=3,
                                                                 min_documents=2),
                                  proposal_log=ProposalLog(tmp_path / "agents" / "_proposals"
                                                           / "ontology_proposals.jsonl"),
                                  vocabulary=vocab, clock=clock)

    class Env:
        pass

    e = Env()
    e.tmp, e.clock, e.service, e.store, e.vocab = tmp_path, clock, service, store, vocab
    e.registry, e.manager = registry, manager
    e.events_path = tmp_path / "events" / "events.jsonl"
    return e


@pytest.fixture
def spec_factory():
    return make_spec


@pytest.fixture(autouse=True)
def _operator_registration_mode(monkeypatch):
    """R-022 round 2 (Codex f2 #10): mcp_ingest registration is fail-closed unless the DEPLOYMENT
    declares operator mode; these suites register their own fixture servers as the operator."""
    monkeypatch.setenv("WALNUT_SOURCE_REGISTRATION", "operator")


@pytest.fixture(autouse=True)
def _local_approval_key(monkeypatch):
    """INT5 (R-022 build cross-track 4): approvals are SEALED with the deployment approval key
    (`mcp_ingest.screen`). Tests use a LOCAL key file under `databridge/var/keys/` (gitignored,
    never committed), generated here if missing — never the developer's `~/.walnut` key. A test
    about key custody overrides `WALNUT_APPROVAL_KEY_FILE` itself."""
    import secrets as _secrets

    key = Path(__file__).resolve().parents[3] / "var" / "keys" / "approval.test.key"
    if not key.is_file():
        key.parent.mkdir(parents=True, exist_ok=True)
        tmp = key.with_name(f".{key.name}.{_secrets.token_hex(6)}.tmp")
        tmp.write_text(_secrets.token_hex(32), encoding="utf-8")
        try:
            tmp.replace(key) if not key.exists() else tmp.unlink()
        except OSError:
            tmp.unlink(missing_ok=True)
    monkeypatch.delenv("WALNUT_APPROVAL_KEY", raising=False)
    monkeypatch.setenv("WALNUT_APPROVAL_KEY_FILE", str(key))
