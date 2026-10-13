"""The audit projection: a queryable SQLite view of the access ledger (P6, plan §4.7).

The ledger is the authority; this projection is DERIVED and rebuildable at any time (`rebuild`). It is
kept current incrementally (`apply`, subscribed to `AccessLedger.append`) and re-synchronised from the
verified ledger before every audit read (`sync`), so a projection that missed an event, or that is
ahead of a rolled-back ledger, never answers on its own authority.

Indexed by subject, doc id, container and event time. Every result row carries ``event_time`` (when it
happened; backdated for the S5 replay), ``recorded_at`` (when it was appended) and
``synthetic_replay``. The projection never holds answer text — only ``answer_ref`` /
``answer_sha256`` from the ledger.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..config import IbConfig
from ..models import EVENT_KINDS
from .ledger import AccessLedger, LedgerEvent, LedgerUnavailable

MAX_RANGE = dt.timedelta(days=365)
MAX_LIMIT = 500
_ALLOWED_VERDICTS = ("verified", "drift_material")

EventKind = Literal[EVENT_KINDS]  # type: ignore[valid-type]


def _aware(v: Optional[dt.datetime]) -> Optional[dt.datetime]:
    if v is None:
        return None
    return v.replace(tzinfo=dt.timezone.utc) if v.tzinfo is None else v


class AuditFilter(BaseModel):
    """The closed audit filter. The NL parser (and any model behind it) must produce exactly this."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    user: Optional[str] = Field(None, min_length=1, max_length=128,
                                pattern=r"^[A-Za-z0-9][A-Za-z0-9._@-]*$")
    container: Optional[str] = Field(None, min_length=1, max_length=200,
                                     pattern=r"^[A-Za-z0-9#][A-Za-z0-9 ._#/-]*$")
    doc: Optional[str] = Field(None, min_length=1, max_length=300,
                               pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._:/#-]*$")
    since: Optional[dt.datetime] = None
    until: Optional[dt.datetime] = None
    decision: Optional[Literal["allow", "deny"]] = None
    kind: Optional[EventKind] = None
    limit: int = Field(100, ge=1, le=MAX_LIMIT)

    @field_validator("since", "until")
    @classmethod
    def _tz(cls, v: Optional[dt.datetime]) -> Optional[dt.datetime]:
        return _aware(v)

    @model_validator(mode="after")
    def _range(self) -> "AuditFilter":
        if self.since and self.until:
            if self.since > self.until:
                raise ValueError("since is after until")
            if self.until - self.since > MAX_RANGE:
                raise ValueError("the time range is capped at 365 days")
        return self

    def bounded(self, now: dt.datetime) -> "AuditFilter":
        """Refuse an open range that reaches further back than 365 days from ``now``."""
        if self.since and not self.until and _aware(now) - self.since > MAX_RANGE:
            raise ValueError("the time range is capped at 365 days")
        return self

    def criteria(self) -> dict[str, Any]:
        """The filter without its defaults (what was actually asked for)."""
        return self.model_dump(mode="json", exclude_none=True, exclude={"limit"})


# ------------------------------------------------------------------------------------------------
def norm_time(v: Any) -> Optional[str]:
    """A fixed-width UTC string that sorts and compares correctly in SQLite."""
    if v is None:
        return None
    if isinstance(v, str):
        try:
            v = dt.datetime.fromisoformat(v)
        except ValueError:
            return None
    if not isinstance(v, dt.datetime):
        return None
    return _aware(v).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _platform_of(doc_id: str) -> Optional[str]:
    return doc_id.split(":", 1)[0] if ":" in doc_id else None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY, hash TEXT NOT NULL, kind TEXT NOT NULL, req_id TEXT, sub TEXT,
    target_req_id TEXT, event_time TEXT, event_time_n TEXT, recorded_at TEXT,
    synthetic INTEGER NOT NULL DEFAULT 0, refused INTEGER, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_ev_sub  ON events(sub, event_time_n);
CREATE INDEX IF NOT EXISTS ix_ev_time ON events(event_time_n);
CREATE INDEX IF NOT EXISTS ix_ev_req  ON events(req_id);
CREATE INDEX IF NOT EXISTS ix_ev_kind ON events(kind, event_time_n);
CREATE INDEX IF NOT EXISTS ix_ev_tgt  ON events(target_req_id);
CREATE TABLE IF NOT EXISTS doc_events (
    seq INTEGER NOT NULL, req_id TEXT, sub TEXT, doc_id TEXT NOT NULL, platform TEXT,
    container TEXT, container_name TEXT, title TEXT, verdict TEXT, reason_code TEXT,
    decision TEXT, considered INTEGER NOT NULL DEFAULT 0, released INTEGER NOT NULL DEFAULT 0,
    cited INTEGER NOT NULL DEFAULT 0, event_time_n TEXT, synthetic INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (req_id, doc_id));
CREATE INDEX IF NOT EXISTS ix_de_doc  ON doc_events(doc_id);
CREATE INDEX IF NOT EXISTS ix_de_cont ON doc_events(container COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ix_de_cname ON doc_events(container_name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ix_de_sub  ON doc_events(sub, event_time_n);
CREATE INDEX IF NOT EXISTS ix_de_time ON doc_events(event_time_n);
CREATE TABLE IF NOT EXISTS docs (
    doc_id TEXT PRIMARY KEY, platform TEXT, container TEXT, container_name TEXT, title TEXT);
"""
_TABLES = ("events", "doc_events", "docs", "meta")


class AuditProjection:
    """``AuditProjection(ledger, path=None)`` — ``path=None`` keeps the projection in memory. With a
    ledger it subscribes to its appends and catches up from it on construction (a ledger that does
    not verify leaves it empty; every audit read re-syncs and refuses then). ``AuditProjection(cfg,
    ledger)`` is accepted too (bootstrap's form). Thread-safe (one connection, one lock)."""

    #: the filter type, for callers that only hold the projection (``/ib/history``)
    filter_type = AuditFilter

    def __init__(self, ledger: Any = None, path: Any = None, *, attach: bool = True,
                 catch_up: bool = True) -> None:
        if isinstance(ledger, IbConfig):                      # AuditProjection(cfg, ledger)
            ledger, path = path, None
        self.ledger: Optional[AccessLedger] = ledger
        self._lock = threading.RLock()
        target = ":memory:"
        if path is not None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            target = str(path)
        self._db = sqlite3.connect(target, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        if ledger is not None and attach:
            ledger.subscribe(self.apply)
        if ledger is not None and catch_up:
            try:
                self.sync()
            except LedgerUnavailable:
                pass

    # --- maintenance ------------------------------------------------------------------------------
    @property
    def last_seq(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT MAX(seq) FROM events").fetchone()
            return int(row[0] or 0)

    def _clear(self) -> None:
        for t in _TABLES:
            self._db.execute(f"DELETE FROM {t}")                      # noqa: S608 — fixed names

    def rebuild(self, events: Optional[Iterable[LedgerEvent]] = None) -> int:
        """Drop everything and re-derive it from ``events`` (default: the verified ledger, strict).
        Returns the number of events applied."""
        if events is None:
            if self.ledger is None:
                raise RuntimeError("no ledger to rebuild from")
            events = list(self.ledger.events(strict=True))
        n = 0
        with self._lock:
            self._clear()
            for le in events:
                self._apply_locked(le)
                n += 1
            self._db.commit()
        return n

    def sync(self) -> int:
        """Bring the projection level with the verified ledger (raises `LedgerUnavailable` if the
        ledger does not verify). Rebuilds when the projection holds an event the ledger does not
        (a rollback or a rewrite). Returns the number of events applied."""
        if self.ledger is None:
            return 0
        evs = list(self.ledger.events(strict=True))
        with self._lock:
            mine = {r[0]: r[1] for r in self._db.execute("SELECT seq, hash FROM events")}
            theirs = {le.seq: le.hash for le in evs}
            if any(theirs.get(s) != h for s, h in mine.items()):
                return self.rebuild(evs)
            n = 0
            for le in evs:
                if le.seq not in mine:
                    self._apply_locked(le)
                    n += 1
            self._db.commit()
            return n

    def apply(self, le: LedgerEvent) -> None:
        """Apply one appended event (idempotent by seq)."""
        with self._lock:
            self._apply_locked(le)
            self._db.commit()

    def _apply_locked(self, le: LedgerEvent) -> None:
        d = le.data
        if self._db.execute("SELECT 1 FROM events WHERE seq = ?", (le.seq,)).fetchone():
            return
        kind = str(d.get("kind", ""))
        et = d.get("event_time")
        etn = norm_time(et)
        synth = 1 if d.get("synthetic_replay") else 0
        refused = None
        if kind == "answer.released":
            refused = 1 if d.get("refused") else 0
        self._db.execute(
            "INSERT INTO events(seq, hash, kind, req_id, sub, target_req_id, event_time, "
            "event_time_n, recorded_at, synthetic, refused, data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (le.seq, le.hash, kind, d.get("req_id"), d.get("sub"), d.get("target_req_id"), et, etn,
             le.recorded_at, synth, refused, json.dumps(d, sort_keys=True)))
        req, sub = d.get("req_id"), d.get("sub")
        if kind == "retrieval.decided":
            meta = {m.get("doc_id"): m for m in d.get("considered_meta") or () if isinstance(m, dict)}
            for doc_id, m in meta.items():
                if doc_id:
                    self._db.execute(
                        "INSERT INTO docs(doc_id, platform, container, container_name, title) "
                        "VALUES (?,?,?,?,?) ON CONFLICT(doc_id) DO UPDATE SET "
                        "platform=excluded.platform, container=excluded.container, "
                        "container_name=excluded.container_name, title=excluded.title",
                        (doc_id, m.get("platform") or _platform_of(doc_id), m.get("container"),
                         m.get("container_name"), m.get("title")))
            gate2 = {g.get("doc_id"): g for g in d.get("gate2") or () if isinstance(g, dict)}
            considered = list(d.get("considered") or ())
            released = set(d.get("released") or ())
            order = list(dict.fromkeys([*considered, *gate2.keys(), *released]))
            for doc_id in order:
                if not doc_id:
                    continue
                g = gate2.get(doc_id) or {}
                verdict = g.get("verdict")
                decision = None
                if verdict is not None:
                    decision = "allow" if verdict in _ALLOWED_VERDICTS else "deny"
                elif doc_id in released:
                    decision = "allow"
                info = self._doc_info(doc_id, meta.get(doc_id))
                self._db.execute(
                    "INSERT INTO doc_events(seq, req_id, sub, doc_id, platform, container, "
                    "container_name, title, verdict, reason_code, decision, considered, released, "
                    "cited, event_time_n, synthetic) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?) "
                    "ON CONFLICT(req_id, doc_id) DO UPDATE SET verdict=excluded.verdict, "
                    "reason_code=excluded.reason_code, decision=excluded.decision, "
                    "considered=excluded.considered, released=excluded.released",
                    (le.seq, req, sub, doc_id, info["platform"], info["container"],
                     info["container_name"], info["title"], verdict, g.get("reason_code"), decision,
                     1 if doc_id in considered else 0, 1 if doc_id in released else 0, etn, synth))
        elif kind == "answer.released":
            for c in d.get("citations") or ():
                doc_id = c.get("doc_id") if isinstance(c, dict) else None
                if not doc_id:
                    continue
                cur = self._db.execute("UPDATE doc_events SET cited = 1 WHERE req_id = ? AND "
                                       "doc_id = ?", (req, doc_id))
                if cur.rowcount == 0:
                    info = self._doc_info(doc_id, c)
                    self._db.execute(
                        "INSERT INTO doc_events(seq, req_id, sub, doc_id, platform, container, "
                        "container_name, title, decision, released, cited, event_time_n, "
                        "synthetic) VALUES (?,?,?,?,?,?,?,?,'allow',1,1,?,?)",
                        (le.seq, req, sub, doc_id, info["platform"], info["container"],
                         info["container_name"], info["title"], etn, synth))

    def _doc_info(self, doc_id: str, m: Optional[dict]) -> dict[str, Optional[str]]:
        m = m or {}
        row = self._db.execute("SELECT platform, container, container_name, title FROM docs "
                               "WHERE doc_id = ?", (doc_id,)).fetchone()
        known = dict(row) if row else {}
        return {"platform": m.get("platform") or known.get("platform") or _platform_of(doc_id),
                "container": m.get("container") or known.get("container"),
                "container_name": m.get("container_name") or known.get("container_name"),
                "title": m.get("title") or known.get("title")}

    def snapshot(self) -> dict[str, list[tuple]]:
        """Every row of every table, ordered — for rebuild == incremental comparisons."""
        with self._lock:
            out = {}
            for t in _TABLES:
                rows = self._db.execute(f"SELECT * FROM {t}").fetchall()      # noqa: S608
                out[t] = sorted(tuple(r) for r in rows)
            return out

    # --- queries ----------------------------------------------------------------------------------
    @staticmethod
    def _doc_match(alias: str = "d") -> str:
        return (f"({alias}.doc_id = :doc OR substr({alias}.doc_id, -length(:doc) - 1) = ':' || :doc "
                f"OR lower({alias}.title) = lower(:doc))")

    def search(self, f: AuditFilter) -> list[dict[str, Any]]:
        """Event rows matching ``f``, most recent first. Doc / container / decision select the
        REQUESTS that touched the matching documents; every event of those requests (that also
        matches user / kind / time) is returned."""
        where, p = [], {}
        if f.user:
            where.append("e.sub = :user")
            p["user"] = f.user
        if f.kind:
            where.append("e.kind = :kind")
            p["kind"] = f.kind
        if f.since:
            where.append("e.event_time_n >= :since")
            p["since"] = norm_time(f.since)
        if f.until:
            where.append("e.event_time_n <= :until")
            p["until"] = norm_time(f.until)
        scoped = f.doc or f.container
        if scoped:
            sub = []
            if f.doc:
                sub.append(self._doc_match("d"))
                p["doc"] = f.doc
            if f.container:
                sub.append("(d.container = :container COLLATE NOCASE OR "
                           "d.container_name = :container COLLATE NOCASE)")
                p["container"] = f.container
            if f.decision:
                sub.append("d.decision = :decision")
                p["decision"] = f.decision
            where.append("e.req_id IN (SELECT d.req_id FROM doc_events d WHERE "
                         + " AND ".join(sub) + ")")
        elif f.decision == "deny":
            where.append("e.req_id IN (SELECT req_id FROM doc_events WHERE decision = 'deny' "
                         "UNION SELECT req_id FROM events WHERE kind = 'answer.released' AND "
                         "refused = 1 UNION SELECT req_id FROM events WHERE kind = 'request.failed')")
        elif f.decision == "allow":
            where.append("e.req_id IN (SELECT req_id FROM events WHERE kind = 'answer.released' "
                         "AND refused = 0)")
        sql = ("SELECT e.* FROM events e" + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY e.event_time_n DESC, e.seq DESC LIMIT :limit")
        p["limit"] = int(f.limit)
        with self._lock:
            rows = self._db.execute(sql, p).fetchall()
            return [self._row(r) for r in rows]

    def _docs_of(self, req_id: Optional[str]) -> list[dict[str, Any]]:
        if not req_id:
            return []
        rows = self._db.execute(
            "SELECT doc_id, platform, container, container_name, title, verdict, reason_code, "
            "decision, considered, released, cited FROM doc_events WHERE req_id = ? "
            "ORDER BY rowid", (req_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for b in ("considered", "released", "cited"):
                d[b] = bool(d[b])
            out.append(d)
        return out

    def _row(self, r: sqlite3.Row) -> dict[str, Any]:
        data = json.loads(r["data"])
        row = {"seq": r["seq"], "hash": r["hash"], "kind": r["kind"], "req_id": r["req_id"],
               "sub": r["sub"], "event_time": r["event_time"], "recorded_at": r["recorded_at"],
               "synthetic_replay": bool(r["synthetic"]), "event": data}
        if r["kind"] == "retrieval.decided":
            row["docs"] = self._docs_of(r["req_id"])
        elif r["kind"] == "request.accepted":
            row["question"] = data.get("question")
            row["channel"] = data.get("channel")
        return row

    def accessors(self, doc_id: str, *, limit: int = MAX_LIMIT) -> dict[str, Any]:
        """Who had ``doc_id`` considered, released, cited or denied — per subject, with the
        requests (ids, times) behind each count."""
        with self._lock:
            rows = self._db.execute(
                "SELECT d.*, e.event_time AS et, e.recorded_at AS rec FROM doc_events d "
                "LEFT JOIN events e ON e.seq = d.seq WHERE " + self._doc_match("d") +
                " ORDER BY d.event_time_n DESC, d.seq DESC LIMIT :limit",
                {"doc": doc_id, "limit": int(min(limit, MAX_LIMIT))}).fetchall()
        people: dict[str, dict[str, Any]] = {}
        reqs = []
        for r in rows:
            sub = r["sub"] or "?"
            a = people.setdefault(sub, {"sub": sub, "requests": 0, "released": 0, "cited": 0,
                                        "denied": 0, "first_event_time": None,
                                        "last_event_time": None, "synthetic_replay": False,
                                        "req_ids": []})
            a["requests"] += 1
            a["released"] += int(r["released"])
            a["cited"] += int(r["cited"])
            a["denied"] += 1 if r["decision"] == "deny" else 0
            a["synthetic_replay"] = a["synthetic_replay"] or bool(r["synthetic"])
            a["req_ids"].append(r["req_id"])
            if a["last_event_time"] is None:
                a["last_event_time"] = r["et"]
            a["first_event_time"] = r["et"]
            reqs.append({"req_id": r["req_id"], "sub": r["sub"], "doc_id": r["doc_id"],
                         "title": r["title"], "container": r["container"], "verdict": r["verdict"],
                         "reason_code": r["reason_code"], "decision": r["decision"],
                         "released": bool(r["released"]), "cited": bool(r["cited"]),
                         "event_time": r["et"], "recorded_at": r["rec"],
                         "synthetic_replay": bool(r["synthetic"])})
        accessors = sorted(people.values(), key=lambda a: (-a["released"], a["sub"]))
        for a in accessors:
            a["accessed"] = a["released"] > 0
        return {"doc_id": doc_id, "accessors": accessors, "requests": reqs}

    def reconstruct(self, req_id: str) -> Optional[dict[str, Any]]:
        """Everything the ledger says about one request: who asked what, when, through which
        channel; the considered documents (ids, titles, containers); Gate-2 decisions and reason
        codes; what was released and cited; the model; ``answer_ref`` (never the text); reveals."""
        with self._lock:
            evs = self._db.execute("SELECT * FROM events WHERE req_id = ? ORDER BY seq",
                                   (req_id,)).fetchall()
            if not evs:
                return None
            reveals = self._db.execute("SELECT * FROM events WHERE kind = 'audit.reveal' AND "
                                       "target_req_id = ? ORDER BY seq", (req_id,)).fetchall()
            docs = self._docs_of(req_id)
        out: dict[str, Any] = {
            "req_id": req_id, "sub": None, "channel": None, "question": None,
            "question_sha256": None, "event_time": None, "recorded_at": None,
            "synthetic_replay": False, "policy_version": None, "directory_epoch": None,
            "time_window": None, "considered": [], "gate2": [], "released": [], "citations": [],
            "model": None, "prompt_version": None, "answer_ref": None, "answer_sha256": None,
            "refused": None, "latency_ms": None, "dropped_sentences_count": None,
            "failure": None, "status": "in_progress", "reveals": [], "events": []}
        by_doc = {d["doc_id"]: d for d in docs}
        for r in evs:
            d = json.loads(r["data"])
            out["events"].append({"seq": r["seq"], "kind": r["kind"], "hash": r["hash"],
                                  "event_time": r["event_time"], "recorded_at": r["recorded_at"]})
            out["synthetic_replay"] = out["synthetic_replay"] or bool(r["synthetic"])
            out["sub"] = out["sub"] or d.get("sub")
            k = r["kind"]
            if k == "request.accepted":
                out.update(channel=d.get("channel"), question=d.get("question"),
                           question_sha256=d.get("question_sha256"), event_time=r["event_time"],
                           recorded_at=r["recorded_at"], policy_version=d.get("policy_version"),
                           directory_epoch=d.get("directory_epoch"))
            elif k == "retrieval.decided":
                out["time_window"] = d.get("time_window")
                out["gate1_policy_version"] = d.get("gate1_policy_version")
                out["considered"] = [by_doc.get(x, {"doc_id": x}) for x in d.get("considered") or ()]
                out["gate2"] = list(d.get("gate2") or ())
                out["released"] = [{"doc_id": x, "title": by_doc.get(x, {}).get("title"),
                                    "container": by_doc.get(x, {}).get("container")}
                                   for x in d.get("released") or ()]
            elif k == "answer.released":
                out.update(model=d.get("model"), prompt_version=d.get("prompt_version"),
                           answer_ref=d.get("answer_ref"), answer_sha256=d.get("answer_sha256"),
                           refused=d.get("refused"), latency_ms=d.get("latency_ms"),
                           dropped_sentences_count=d.get("dropped_sentences_count"),
                           status="refused" if d.get("refused") else "released")
                out["citations"] = [{**c, "title": by_doc.get(c.get("doc_id"), {}).get("title")}
                                    for c in d.get("citations") or () if isinstance(c, dict)]
            elif k == "request.failed":
                out["failure"] = {"stage": d.get("stage"), "error_code": d.get("error_code")}
                out["status"] = "failed"
            if out["event_time"] is None:
                out["event_time"], out["recorded_at"] = r["event_time"], r["recorded_at"]
        for r in reveals:
            d = json.loads(r["data"])
            out["reveals"].append({"seq": r["seq"], "by": d.get("sub"), "reason": d.get("reason"),
                                   "event_time": r["event_time"], "recorded_at": r["recorded_at"]})
        return out

    def close(self) -> None:
        with self._lock:
            self._db.close()


__all__ = ["AuditFilter", "AuditProjection", "MAX_RANGE", "MAX_LIMIT", "norm_time"]
