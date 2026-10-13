"""Publishing the ledger head off-host, and verifying against it (P5, CONTRACTS §4 "External head").

`publish_head(ledger, sink)` posts ``{log_id, seq, head, at}`` of the VERIFIED ledger to a sink and then
records an ``anchor.published`` event in the ledger. `verify(ledger, sink)` compares the ledger with the
latest head the sink holds.

Why it matters. The chain, the seals and the internal anchor ledger (a separate directory) detect an
edit, a deleted record or a truncated log made by anyone WITHOUT the sealing key. They cannot detect:

* a rollback of the whole var directory (log + internal anchors restored together from a backup), or
* a rewrite by a key holder who re-seals the chain and regenerates the internal anchors.

Both leave a log that verifies internally. Only a head published somewhere the attacker cannot rewrite
— a separate git repo pushed elsewhere, a compliance-only Slack channel — exposes them: any rewrite of
history BEFORE the last publication no longer matches it, and a rollback leaves the log shorter than it.
Changes after the last publication are not covered until the next one (publish every 15 min and on
demand, plan §4.7).

Sinks:
* `FileSink(path)` — an append-only JSONL file, intended to live in a separate git repository that is
  committed and pushed after each publication (the push is what makes it off-host).
* `SlackWebhookSink(url)` — an incoming-webhook POST (url from ``IB_ANCHOR_SLACK_WEBHOOK``). A webhook
  cannot be read back, so `latest()` comes from an optional ``mirror`` (a `FileSink`) or ``reader``
  callable; without either it returns None and verify reports ``external_head_match: null``.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Callable, Optional, Protocol

from ..models import AnchorPublished
from .ledger import AccessLedger, LedgerUnavailable, VerifyReport

SLACK_WEBHOOK_ENV = "IB_ANCHOR_SLACK_WEBHOOK"


class AnchorPublishFailed(RuntimeError):
    """The head could not be published; nothing was recorded in the ledger."""


class AnchorSink(Protocol):
    name: str

    def publish(self, head: dict) -> None: ...

    def latest(self, log_id: str) -> Optional[dict]: ...


class FileSink:
    """Append-only JSONL of published heads. Put it in a separate git repo and push after publish."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.name = f"file:{self.path.name}"

    def publish(self, head: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(head, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def heads(self, log_id: str) -> list[dict]:
        if not self.path.is_file():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("log_id") == log_id:
                out.append(rec)
        return out

    def latest(self, log_id: str) -> Optional[dict]:
        heads = self.heads(log_id)
        return max(heads, key=lambda h: int(h.get("seq") or 0)) if heads else None


class SlackWebhookSink:
    """Posts the head to a Slack incoming webhook (a compliance-only channel)."""

    def __init__(self, url: str, *, mirror: Optional[FileSink] = None,
                 reader: Optional[Callable[[str], Optional[dict]]] = None,
                 client=None, timeout: float = 5.0) -> None:
        if not url:
            raise ValueError("a Slack webhook URL is required")
        self.url = url
        self.mirror = mirror
        self.reader = reader
        self._client = client
        self.timeout = timeout
        self.name = "slack-webhook"

    @classmethod
    def from_env(cls, **kw) -> Optional["SlackWebhookSink"]:
        url = os.environ.get(SLACK_WEBHOOK_ENV, "").strip()
        return cls(url, **kw) if url else None

    def publish(self, head: dict) -> None:
        import httpx                                                 # noqa: PLC0415

        text = (f"Walnut Internal Brain ledger head | log `{head['log_id']}` | seq {head['seq']} | "
                f"head `{head['head']}` | at {head['at']}")
        body = {"text": text, "ib_anchor": head}
        client = self._client or httpx.Client(timeout=self.timeout)
        try:
            r = client.post(self.url, json=body)
            if r.status_code >= 300:
                raise AnchorPublishFailed(f"the Slack webhook answered {r.status_code}")
        except httpx.HTTPError:
            raise AnchorPublishFailed("the Slack webhook could not be reached") from None
        finally:
            if self._client is None:
                client.close()
        if self.mirror is not None:
            self.mirror.publish(head)

    def latest(self, log_id: str) -> Optional[dict]:
        if self.reader is not None:
            return self.reader(log_id)
        return self.mirror.latest(log_id) if self.mirror is not None else None


def publish_head(ledger: AccessLedger, sink: AnchorSink, *,
                 now: Optional[dt.datetime] = None) -> AnchorPublished:
    """Publish the verified head of ``ledger`` to ``sink``, then ledger ``anchor.published``.

    A ledger that does not verify is never published (`LedgerUnavailable`): publishing the head of a
    tampered log would launder it. A sink failure raises `AnchorPublishFailed` and records nothing."""
    seq, head = ledger.head()                                     # strict: raises if broken
    if not seq or head is None:
        raise LedgerUnavailable("LEDGER_EMPTY", "the access ledger holds no record to publish")
    when = now or dt.datetime.now(dt.timezone.utc)
    payload = {"log_id": ledger.log_id, "seq": seq, "head": head, "at": when.isoformat()}
    try:
        sink.publish(payload)
    except AnchorPublishFailed:
        raise
    except Exception:                                                # noqa: BLE001
        raise AnchorPublishFailed(f"the head could not be published to {sink.name}") from None
    ev = AnchorPublished(event_time=when, seq=seq, head=head, sink=sink.name)
    ledger.append(ev)
    return ev


def verify(ledger: AccessLedger, sink: Optional[AnchorSink]) -> VerifyReport:
    """The ledger's verification against ``sink``'s latest published head."""
    if sink is None:
        return ledger.verify(use_sink=False)
    try:
        ext = sink.latest(ledger.log_id)
    except Exception:                                                # noqa: BLE001
        rep = ledger.verify(use_sink=False)
        rep.problems.append(f"the external head could not be read from {sink.name}")
        rep.external_head_match = False
        rep.ok = False
        return rep
    if ext is None:
        # no head published (or the sink lost it): verify still flags a log that says it published
        rep = ledger.verify(use_sink=False)
        published = _published_in_log(ledger)
        if published:
            rep.external_head_match = False
            rep.ok = False
            rep.problems.append(f"the log records an external publication of seq {published}, but "
                                f"{sink.name} holds no head for it")
        return rep
    return ledger.verify(external_head=ext)


def _published_in_log(ledger: AccessLedger) -> int:
    try:
        evs = list(ledger.events(strict=False))
    except Exception:                                                # noqa: BLE001
        return 0
    return max((int(e.data.get("seq") or 0) for e in evs if e.kind == "anchor.published"), default=0)


__all__ = ["AnchorSink", "FileSink", "SlackWebhookSink", "AnchorPublishFailed", "publish_head",
           "verify", "SLACK_WEBHOOK_ENV"]
