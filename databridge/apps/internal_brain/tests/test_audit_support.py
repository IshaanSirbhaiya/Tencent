"""Shared helpers for the audit tests (no tests here): a config per tmp var root and a synthetic,
seeded request history written through the real `AccessLedger` + `AnswerStore`.

The seeded history is described by `SPECS`, and every test derives its expectations from those
specs (never from hard-coded counts)."""
from __future__ import annotations

import datetime as dt
import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from databridge_ib.audit.content import AnswerStore
from databridge_ib.audit.ledger import AccessLedger
from databridge_ib.config import IbConfig
from databridge_ib.models import (AnswerReleased, Gate2Decision, ModelInfo, RequestAccepted,
                                  RequestFailed, RetrievalDecided, SourceChangeEvent)

NOW = dt.datetime.fromisoformat("2026-10-13T09:00:00+08:00")
KEY = "test-ledger-key-0123456789abcdef-0123456789"

DOCS = {
    "confluence:cf-1001": ("confluence", "payment-gateway", "Payment Gateway", "PG runbook"),
    "confluence:cf-1002": ("confluence", "payment-gateway", "Payment Gateway", "PG settlement design"),
    "confluence:cf-1003": ("confluence", "payment-gateway", "Payment Gateway", "PG incident review"),
    "confluence:cf-2001": ("confluence", "platform", "Platform", "DB migration plan"),
    "jira:PAY-201": ("jira", "PAY", "Payments", "Migrate settlement batch"),
    "slack:C0DBMIG:1728370000.000100": ("slack", "C0DBMIG", "db-migration", "thread 1728370000"),
}


@dataclass
class Spec:
    req_id: str
    sub: str
    days_ago: float
    docs: tuple[str, ...]
    denied: tuple[str, ...] = ()           # subset of docs that Gate 2 denied
    refused: bool = False
    failed: bool = False
    question: str = ""
    answer: str = ""
    cited: tuple[str, ...] = field(default=())

    @property
    def event_time(self) -> dt.datetime:
        return NOW - dt.timedelta(days=self.days_ago)

    @property
    def released(self) -> tuple[str, ...]:
        return () if self.failed else tuple(d for d in self.docs if d not in self.denied)


def _specs() -> list[Spec]:
    s = []
    pg = ("confluence:cf-1001", "confluence:cf-1002", "confluence:cf-1003")
    # jdoe x payment-gateway over the last 30 days (and one older, outside the window)
    for i, (days, docs) in enumerate([(2, pg[:2]), (9, pg[1:]), (17, (pg[0], "jira:PAY-201")),
                                      (28, (pg[2],)), (41, (pg[0],))]):
        s.append(Spec(f"r-jdoe-pg-{i}", "jdoe", days, docs,
                      denied=(pg[2],) if pg[2] in docs and days == 9 else ()))
    s.append(Spec("r-jdoe-plat", "jdoe", 5, ("confluence:cf-2001",)))
    s.append(Spec("r-jdoe-slack", "jdoe", 12, ("slack:C0DBMIG:1728370000.000100",)))
    s.append(Spec("r-priya-pg", "priya.sre", 6, (pg[0],)))
    s.append(Spec("r-priya-jira", "priya.sre", 3, ("jira:PAY-201",)))
    s.append(Spec("r-ken-1", "ken.ext", 4, (pg[1],), denied=(pg[1],), refused=True))
    s.append(Spec("r-ken-2", "ken.ext", 15, ("confluence:cf-2001",), denied=("confluence:cf-2001",),
                  refused=True))
    s.append(Spec("r-leo-fail", "leo.ops", 7, ("jira:PAY-201",), failed=True))
    s.append(Spec("r-sara-1", "sara.sec", 1, (pg[2], "jira:PAY-201")))
    for sp in s:
        sp.question = f"synthetic question {sp.req_id} about {', '.join(sp.docs)}"
        sp.answer = f"SECRET-ANSWER-TEXT for {sp.req_id}: settlement cut-off is 17:30 [C1]"
        sp.cited = sp.released[:1]
    return s


SPECS = _specs()


def make_cfg(root: Path, *, key: str = KEY) -> IbConfig:
    return IbConfig(var_root=Path(root) / "var", ledger_key=key)


def _meta(doc_id: str) -> dict:
    platform, container, cname, title = DOCS[doc_id]
    return {"doc_id": doc_id, "platform": platform, "container": container,
            "container_name": cname, "title": title}


def write_request(ledger: AccessLedger, store: AnswerStore, sp: Spec) -> None:
    t = sp.event_time
    ledger.append(RequestAccepted(req_id=sp.req_id, sub=sp.sub, channel="replay",
                                  question=sp.question,
                                  question_sha256=hashlib.sha256(sp.question.encode()).hexdigest(),
                                  policy_version="ib-policy-1", directory_epoch=1, event_time=t,
                                  synthetic_replay=True))
    t2 = t + dt.timedelta(seconds=1)
    ledger.append(RetrievalDecided(
        req_id=sp.req_id, sub=sp.sub, gate1_policy_version="ib-policy-1", considered=sp.docs,
        considered_meta=tuple(_meta(d) for d in sp.docs),
        gate2=tuple(Gate2Decision(doc_id=d, verdict="forbidden" if d in sp.denied else "verified",
                                  reason_code="acl.denied" if d in sp.denied else "acl.ok")
                    for d in sp.docs),
        released=sp.released, event_time=t2, synthetic_replay=True))
    t3 = t + dt.timedelta(seconds=2)
    if sp.failed:
        ledger.append(RequestFailed(req_id=sp.req_id, sub=sp.sub, stage="generate",
                                    error_code="MODEL_TIMEOUT", event_time=t3,
                                    synthetic_replay=True))
        return
    ref, sha = store.put(sp.req_id, sp.answer)
    ledger.append(AnswerReleased(
        req_id=sp.req_id, sub=sp.sub, model=ModelInfo(provider="deterministic", model="extractive-v1"),
        prompt_version="ib-1", answer_sha256=sha, answer_ref=ref, refused=sp.refused,
        citations=tuple({"doc_id": d, "version": "7", "content_checked_at": t3.isoformat(),
                         "authz_checked_at": t3.isoformat()} for d in sp.cited),
        latency_ms=420, event_time=t3, synthetic_replay=True))


def seed(root: Path) -> tuple[IbConfig, AccessLedger, AnswerStore]:
    cfg = make_cfg(root)
    ledger = AccessLedger(cfg)
    store = AnswerStore(cfg)
    for sp in SPECS:
        write_request(ledger, store, sp)
    ledger.append(SourceChangeEvent(kind="acl.changed", platform="confluence",
                                    detail={"doc_ids": ["confluence:cf-1003"]},
                                    event_time=NOW - dt.timedelta(days=8)))
    return cfg, ledger, store


def clone(src_root: Path, dst_root: Path) -> tuple[IbConfig, AccessLedger, AnswerStore]:
    shutil.copytree(Path(src_root) / "var", Path(dst_root) / "var")
    cfg = make_cfg(dst_root)
    return cfg, AccessLedger(cfg), AnswerStore(cfg)


def events_in_seed() -> int:
    return 3 * len(SPECS) + 1
