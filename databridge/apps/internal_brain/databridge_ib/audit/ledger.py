"""The access ledger: every IB request phase and every audit call, sealed and hash-chained (P5).

A thin wrapper over Walnut's `DecisionLedger` (CONTRACTS §4) — hashing, sealing, the hash chain, the
internal anchor ledger, the cross-process lock and interrupted-append recovery all live there and are
NOT reimplemented here. This module adds three things:

* the IB payload shape: ``{"ib_event": <EventBase.model_dump(mode="json")>, "recorded_at": <iso>}``.
  ``event_time`` (inside the event) is when it happened — backdated for the S5 synthetic replay —
  and ``recorded_at`` is the real wall-clock time of the append (plan §4.7);
* fail-closed appends: ANY failure (no key, a short key, a broken or rolled-back log, a lock timeout,
  a disk error, an event that is not an IB event) raises `LedgerUnavailable`, which callers turn into
  a 503 and discard the answer (plan §4.2 steps 2/8/11);
* `verify()`, which reports the internal verification (chain, seals, the anchor ledger in its
  separate directory) AND the comparison with the latest externally published head (anchor.py).
  The external head is the only thing that detects a rollback of the WHOLE var directory (log and
  anchor ledger together) or a rewrite by someone holding the sealing key.

Key handling. The key is ``IbConfig.ledger_key`` (env ``IB_LEDGER_KEY``) handed to `DecisionLedger`
as bytes, so it is never a file inside the log's directory (DecisionLedger refuses a key FILE inside
the directory it seals; a supplied key must be >= 32 characters, else every append refuses with
``APPROVAL_KEY_UNAVAILABLE`` — fail closed). The anchor ledger lives in ``var/ib/anchors``, a
different directory from the log.

Performance note (measured on this Windows dev box): `DecisionLedger.append` re-verifies the whole log
and twice the whole anchor ledger under the lock, so an append costs O(records). See the A5 report.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional, Protocol

from databridge_core.decision_ledger import LEDGER_EVENT, DecisionLedger, DecisionRefused
from pydantic import ValidationError

from ..config import IbConfig
from ..models import (EVENT_KINDS, IB_EVENT, AnchorPublished, AnswerReleased, AuditQueryEvent,
                      AuditReveal, EventBase, RequestAccepted, RequestFailed, RetrievalDecided,
                      SourceChangeEvent)

#: The log's id in the anchor ledger and in every external head. Fixed (not the resolved path), so a
#: copied or restored var directory is still recognised as the same log.
LOG_ID = "ib-access"

_MODELS: dict[str, type[EventBase]] = {
    "request.accepted": RequestAccepted,
    "retrieval.decided": RetrievalDecided,
    "answer.released": AnswerReleased,
    "request.failed": RequestFailed,
    "audit.query": AuditQueryEvent,
    "audit.reveal": AuditReveal,
    "anchor.published": AnchorPublished,
    "acl.changed": SourceChangeEvent,
    "membership.changed": SourceChangeEvent,
    "directory.changed": SourceChangeEvent,
    "ingest.sync": SourceChangeEvent,
}


class LedgerUnavailable(RuntimeError):
    """The access ledger cannot record (or cannot vouch for) an event. Callers answer 503 and discard
    whatever they were about to release. ``code`` is a standard refusal code; ``message`` is public
    text (never a key, never an exception string from the OS)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ExternalHeadSource(Protocol):
    """Anything that can return the latest externally published head of a log (anchor.py sinks)."""

    name: str

    def latest(self, log_id: str) -> Optional[dict]: ...


@dataclass
class LedgerEvent:
    """One IB event as read back from the verified ledger."""
    seq: int
    hash: str
    recorded_at: Optional[str]
    data: dict[str, Any]                         # the ib_event dict exactly as stored
    event: Optional[EventBase] = None            # parsed model (None if the kind is unknown)

    @property
    def kind(self) -> str:
        return str(self.data.get("kind", ""))


@dataclass
class VerifyReport:
    ok: bool
    records: int                                 # records in the log (incl. ledger bookkeeping)
    head: Optional[str]                          # digest of the last record that verifies
    problems: list[str] = field(default_factory=list)
    external_head_match: Optional[bool] = None   # None: no external sink / nothing published yet
    external_head: Optional[dict] = None         # {log_id, seq, head, at} as published
    internal_ok: bool = True                     # chain + seals + internal anchor ledger

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "records": self.records, "head": self.head,
                "external_head_match": self.external_head_match, "problems": list(self.problems),
                "internal_ok": self.internal_ok, "external_head": self.external_head}


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_event(data: dict[str, Any]) -> Optional[EventBase]:
    model = _MODELS.get(str(data.get("kind")))
    if model is None:
        return None
    try:
        return model.model_validate(data)
    except ValidationError:
        return None


def compare_external(records: list[dict], ext: Optional[dict], *, chain_ok: bool,
                     published_in_log: Optional[int] = None,
                     digest_field: str = "hash") -> tuple[Optional[bool], list[str]]:
    """Compare the (verified) records of a log with its latest external head.

    ``records`` are the raw ledger records that verified (a prefix when the chain is broken).
    ``published_in_log`` is the highest seq the log itself says was published (from its
    ``anchor.published`` events) — an external head that is missing although the log records a
    publication means the external record was lost or withheld."""
    if ext is None:
        if published_in_log:
            return False, [f"the log records an external publication of seq {published_in_log}, "
                           f"but the external sink holds no head for it"]
        return None, []
    try:
        seq, head = int(ext["seq"]), str(ext["head"])
    except (KeyError, TypeError, ValueError):
        return False, ["the external head is malformed"]
    by_seq = {int(r["seq"]): str(r.get(digest_field)) for r in records if "seq" in r}
    last = max(by_seq) if by_seq else 0
    if seq in by_seq:
        if by_seq[seq] == head:
            return True, []
        return False, [f"history rewritten at or before seq {seq}: the log no longer matches the "
                       f"head published externally at {ext.get('at')}"]
    if chain_ok:
        return False, [f"rollback detected vs external head: the log holds {last} record(s), but "
                       f"seq {seq} was published externally at {ext.get('at')}"]
    return False, [f"the external head (seq {seq}) cannot be confirmed: the chain is broken at or "
                   f"before record {last + 1}"]


class AccessLedger:
    """``AccessLedger(cfg)`` — the IB access ledger at ``cfg.ledger_path``, anchored in
    ``cfg.anchors_path``. ``external`` (optional) is the sink whose latest published head `verify`
    compares against (anchor.py `FileSink` / `SlackWebhookSink` with a readable mirror)."""

    def __init__(self, cfg: IbConfig, *, external: Optional[ExternalHeadSource] = None,
                 lock_timeout: float = 10.0) -> None:
        self.cfg = cfg
        self.external = external
        self._subscribers: list[Callable[[LedgerEvent], None]] = []
        key = (cfg.ledger_key or "").encode("utf-8")
        self._dl = DecisionLedger(cfg.ledger_path, key=key, anchors=cfg.anchors_path,
                                  log_id=LOG_ID, what="access ledger", lock_timeout=lock_timeout)

    # --- plumbing ---------------------------------------------------------------------------------
    @property
    def log_id(self) -> str:
        return LOG_ID

    @property
    def path(self):
        return self._dl.path

    @property
    def anchors_path(self):
        return self._dl.anchors_path

    @property
    def core(self) -> DecisionLedger:
        """The underlying `DecisionLedger` (for operators: `recover()`)."""
        return self._dl

    def subscribe(self, fn: Callable[[LedgerEvent], None]) -> None:
        """Call ``fn`` with every event this process appends (the audit projection). A subscriber's
        failure never fails an append: the projection is derived and rebuildable."""
        self._subscribers.append(fn)

    # --- writing ----------------------------------------------------------------------------------
    def append(self, event: EventBase) -> dict:
        """Seal and append one IB event; returns the record as written. Raises `LedgerUnavailable`
        on ANY failure — nothing that depends on this record may be released."""
        try:
            if not isinstance(event, EventBase) or event.kind not in EVENT_KINDS:
                raise LedgerUnavailable("LEDGER_BAD_EVENT", "not an Internal Brain ledger event")
            payload = {IB_EVENT: event.model_dump(mode="json"),
                       "recorded_at": _utcnow().isoformat()}
            rec = self._dl.append(payload)
        except LedgerUnavailable:
            raise
        except DecisionRefused as exc:
            raise LedgerUnavailable(exc.code, f"the access ledger refused the append: "
                                              f"{exc.message}") from None
        except Exception:                                            # noqa: BLE001 — fail closed
            raise LedgerUnavailable("LEDGER_WRITE_FAILED",
                                    "the access ledger could not be written") from None
        le = LedgerEvent(seq=int(rec["seq"]), hash=str(rec["hash"]),
                         recorded_at=rec.get("recorded_at"), data=dict(rec[IB_EVENT]), event=event)
        for fn in list(self._subscribers):
            try:
                fn(le)
            except Exception:                                        # noqa: BLE001 — derived state
                pass
        return rec

    # --- reading ----------------------------------------------------------------------------------
    def records(self, *, strict: bool = True) -> tuple[list[dict], str]:
        """(raw verified records incl. ledger bookkeeping, why-not-verified). ``strict`` raises
        `LedgerUnavailable` when the log does not verify; otherwise the verified prefix is
        returned with the reason."""
        try:
            recs = self._dl.read(raw=True)
            return recs, ""
        except DecisionRefused as exc:
            if strict:
                raise LedgerUnavailable(exc.code, exc.message) from None
            why = exc.message
        except Exception:                                            # noqa: BLE001
            if strict:
                raise LedgerUnavailable("LEDGER_READ_FAILED",
                                        "the access ledger could not be read") from None
            why = "the access ledger could not be read"
        prefix, why2 = self._dl.inspect()
        return prefix, why2 or why

    def events(self, *, strict: bool = True, after_seq: int = 0) -> Iterator[LedgerEvent]:
        """The IB events in the ledger, in order, after verifying the whole chain and its anchor.
        ``strict=False`` yields the verified prefix of a broken log instead of raising."""
        recs, _ = self.records(strict=strict)
        for rec in recs:
            if LEDGER_EVENT in rec or IB_EVENT not in rec:
                continue
            seq = int(rec["seq"])
            if seq <= after_seq:
                continue
            data = dict(rec[IB_EVENT])
            yield LedgerEvent(seq=seq, hash=str(rec.get("hash")), recorded_at=rec.get("recorded_at"),
                              data=data, event=parse_event(data))

    def head(self) -> tuple[int, Optional[str]]:
        """(seq, head digest) of the verified log; raises `LedgerUnavailable` if it does not verify."""
        recs, _ = self.records(strict=True)
        if not recs:
            return 0, None
        return int(recs[-1]["seq"]), str(recs[-1]["hash"])

    # --- verification -----------------------------------------------------------------------------
    def verify(self, *, external_head: Optional[dict] = None, use_sink: bool = True) -> VerifyReport:
        """Verify the chain, the seals and the internal anchor ledger, then compare with the latest
        external head (``external_head`` if given, else ``self.external.latest(LOG_ID)``). Never
        raises."""
        problems: list[str] = []
        try:
            recs, why = self.records(strict=False)
        except Exception:                                            # noqa: BLE001
            recs, why = [], "the access ledger could not be read"
        internal_ok = not why
        if why:
            problems.append(why)
        ext = external_head
        if ext is None and use_sink and self.external is not None:
            try:
                ext = self.external.latest(LOG_ID)
            except Exception:                                        # noqa: BLE001
                ext = None
                problems.append(f"the external head could not be read from "
                                f"{getattr(self.external, 'name', 'the sink')}")
        published = 0
        for r in recs:
            ev = r.get(IB_EVENT)
            if isinstance(ev, dict) and ev.get("kind") == "anchor.published":
                published = max(published, int(ev.get("seq") or 0))
        sink_configured = external_head is not None or (use_sink and self.external is not None)
        match, ext_problems = compare_external(recs, ext, chain_ok=internal_ok,
                                               published_in_log=published if sink_configured else None)
        problems.extend(ext_problems)
        head = str(recs[-1]["hash"]) if recs else None
        ok = internal_ok and match is not False
        return VerifyReport(ok=ok, records=len(recs), head=head, problems=problems,
                            external_head_match=match, external_head=ext, internal_ok=internal_ok)


__all__ = ["LOG_ID", "LedgerUnavailable", "LedgerEvent", "VerifyReport", "AccessLedger",
           "ExternalHeadSource", "compare_external", "parse_event"]
