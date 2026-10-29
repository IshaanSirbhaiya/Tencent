"""Build a running Internal Brain from configuration: directory, connectors, index, ledger, answer
store, model chain, pipeline, ingestor, app. ``python -m databridge_ib`` serves it on :8100.

Connectors are constructed from ``IbConfig.platform_base`` (the mock platforms by default; a real
service per platform when ``IB_<PLATFORM>_BASE`` and its credentials are set — same code path).
"""
from __future__ import annotations

import importlib
import logging
import os
from typing import Optional

import httpx

from .app import Services, create_app
from .config import IbConfig
from .directory import Directory
from .index import Index
from .ingest import Ingestor
from .llm import ModelChain
from .pipeline import Pipeline

log = logging.getLogger("databridge_ib.bootstrap")

#: platform → (module, class, default path prefix on the mock platforms app)
CONNECTORS = {
    "slack": ("databridge_ib.connectors.slack", "SlackConnector", "/slack/api"),
    "confluence": ("databridge_ib.connectors.confluence", "ConfluenceConnector", "/confluence/wiki"),
    "jira": ("databridge_ib.connectors.jira", "JiraConnector", "/jira/rest/api/3"),
    "drive": ("databridge_ib.connectors.drive", "DriveConnector", "/drive/v3"),
}


def _client(base: str, token: str) -> httpx.Client:
    return httpx.Client(base_url=base, timeout=10.0, headers={"Authorization": f"Bearer {token}"})


def build_connectors(cfg: IbConfig, directory: Directory) -> dict:
    out = {}
    for platform, (mod, cls, prefix) in CONNECTORS.items():
        try:
            klass = getattr(importlib.import_module(mod), cls)
        except (ImportError, AttributeError):
            log.warning("connector %s not available — platform skipped", platform)
            continue
        base = cfg.platform_base[platform]
        prefix = os.environ.get(f"IB_{platform.upper()}_PREFIX", prefix)
        out[platform] = klass(_client(base, cfg.svc_token), directory, cfg.webhook_secret,
                              _client(base, cfg.admin_token), prefix=prefix)
    return out


def build_services(cfg: Optional[IbConfig] = None, *, chain: Optional[ModelChain] = None) -> Services:
    from .audit.content import AnswerStore
    from .audit.ledger import AccessLedger

    cfg = cfg or IbConfig()
    cfg.assert_deployable()
    cfg.var_root.mkdir(parents=True, exist_ok=True)
    directory = Directory.from_world_file(cfg.world_path)
    connectors = build_connectors(cfg, directory)
    index = Index(cfg.index_db)
    sink = build_anchor_sink(cfg)
    ledger = AccessLedger(cfg, external=sink)
    answers = AnswerStore(cfg)
    chain = chain if chain is not None else ModelChain.tokenhub()
    pipeline = Pipeline(index=index, directory=directory, connectors=connectors, ledger=ledger,
                        answers=answers, chain=chain)
    ingestor = Ingestor(index, connectors, cfg.var_root / "cursors.json", directory=directory,
                        ledger=ledger)
    projection = None
    try:
        from .audit.query import AuditProjection
        projection = AuditProjection(cfg, ledger)
    except Exception as exc:                                        # noqa: BLE001
        log.warning("audit projection unavailable: %s", exc)
    svc = Services(cfg=cfg, directory=directory, index=index, connectors=connectors,
                   ledger=ledger, answers=answers, pipeline=pipeline, ingestor=ingestor,
                   projection=projection)
    svc.anchor_sink = sink
    return svc


def build_anchor_sink(cfg: IbConfig):
    """External head sink: an append-only file meant to live in a SEPARATE git repo
    (``IB_ANCHOR_FILE``), mirrored to a compliance Slack channel when ``IB_ANCHOR_SLACK_WEBHOOK``
    is set. Default for local dev: ``<var>/external_anchor/heads.jsonl`` (clearly not off-host)."""
    from .audit.anchor import FileSink, SlackWebhookSink
    path = cfg.anchor_file or str(cfg.var_root / "external_anchor" / "heads.jsonl")
    sink = FileSink(path)
    if os.environ.get("IB_ANCHOR_SLACK_WEBHOOK"):
        try:
            return SlackWebhookSink.from_env(mirror=sink)
        except Exception as exc:                                    # noqa: BLE001
            log.warning("slack anchor sink unavailable: %s", exc)
    return sink


def start_anchor_publisher(svc, interval_s: float):
    """Publish the ledger head off-host every ``interval_s`` (plan §4.7)."""
    import threading
    from .audit.anchor import publish_head

    def run():
        stop = threading.Event()
        while not stop.wait(interval_s):
            try:
                publish_head(svc.ledger, svc.anchor_sink)
            except Exception as exc:                                # noqa: BLE001
                log.warning("anchor publish failed: %s", exc)
    threading.Thread(target=run, name="ib-anchor", daemon=True).start()


def audit_filter_parser(svc):
    """The plain-English audit search's model (plan v2 B13): the answers' own chain (TokenHub Hy3,
    then DeepSeek), or None when no key is configured or ``IB_FORCE_OFFLINE=1`` (the chain is None
    then). It sees only the question and the clock; its output is validated against the closed
    filter, and any failure falls back to the rule parser (``llm_fallback=True``)."""
    chain = getattr(svc.pipeline, "chain", None)
    if chain is None:
        return None
    from .audit.nl_llm import make_chain_parser
    return make_chain_parser(chain)


def build_app(cfg: Optional[IbConfig] = None, *, sync: bool = True, poll_s: Optional[float] = None):
    from .sim import make_sim_router
    svc = build_services(cfg)

    def audit_factory(current, require_role):
        from .audit.routes import make_audit_router
        return make_audit_router(svc.ledger, svc.answers, svc.projection, require_role,
                                 sink=svc.anchor_sink, llm=audit_filter_parser(svc),
                                 llm_fallback=True)

    app = create_app(svc, audit_router_factory=audit_factory,
                     sim_router_factory=lambda cur, rr: make_sim_router(svc.cfg, rr,
                                                                           ingestor=svc.ingestor))
    if sync:
        svc.ingestor.sync_all()
    if poll_s:
        from .freshness.poller import Poller
        Poller(svc.ingestor, poll_s).start()
    if svc.cfg.anchor_interval_s > 0:
        try:
            from .audit.anchor import publish_head
            publish_head(svc.ledger, svc.anchor_sink)              # an initial head at startup
        except Exception as exc:                                    # noqa: BLE001
            log.warning("initial anchor publish failed: %s", exc)
        start_anchor_publisher(svc, svc.cfg.anchor_interval_s)
    return app


def main() -> None:
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("IB_PORT", "8100"))
    app = build_app(poll_s=float(os.environ.get("IB_POLL_S", "120")))
    uvicorn.run(app, host=os.environ.get("IB_HOST", "127.0.0.1"), port=port)
