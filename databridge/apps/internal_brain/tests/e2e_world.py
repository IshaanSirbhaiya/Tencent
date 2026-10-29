"""End-to-end harness: the REAL mock platforms app (in-process) + the REAL connectors + the IB
pipeline, over the generated Company A world. Webhooks flow mock → connector.parse_webhook (signature
verified) → Ingestor.apply_event, exactly as in deployment, just without sockets."""
from __future__ import annotations

import contextlib
import importlib
import json
import threading
import uuid
from pathlib import Path
from typing import Optional

import anyio.from_thread
from fastapi.testclient import TestClient

from databridge_ib.directory import Directory
from databridge_ib.index import Index
from databridge_ib.ingest import Ingestor
from databridge_ib.pipeline import Pipeline

ROOT = Path(__file__).resolve().parents[3]
WORLD = ROOT / "fixtures" / "enterprise" / "world.json"
MANIFEST = ROOT / "fixtures" / "enterprise" / "manifest.json"
SVC, ADMIN, SECRET = "svc-e2e", "admin-e2e", "whsec-e2e"

CONNECTORS = {
    "slack": ("databridge_ib.connectors.slack", "SlackConnector", "/slack/api"),
    "confluence": ("databridge_ib.connectors.confluence", "ConfluenceConnector", "/confluence/wiki"),
    "jira": ("databridge_ib.connectors.jira", "JiraConnector", "/jira/rest/api/3"),
    "drive": ("databridge_ib.connectors.drive", "DriveConnector", "/drive/v3"),
}


class MemLedger:
    def __init__(self):
        self.events = []

    def append(self, ev):
        self.events.append(ev)
        return {}


class MemAnswers:
    def put(self, req_id, answer):
        import hashlib
        return req_id, hashlib.sha256(answer.encode()).hexdigest()


_PORTAL = None
_PORTAL_THREAD = None
_PORTAL_LOCK = threading.Lock()


def _shared_portal():
    """One long-lived event loop per process for every in-process mock request. Starlette's
    TestClient otherwise starts a fresh loop per request; on Windows each loop opens a localhost
    socketpair, and thousands of them across eval shards exhaust ephemeral ports until a socketpair
    ``accept()`` blocks forever."""
    global _PORTAL, _PORTAL_THREAD
    with _PORTAL_LOCK:
        if _PORTAL is None:
            holder: list = []
            ready = threading.Event()

            async def serve():
                async with anyio.from_thread.BlockingPortal() as portal:
                    holder.append(portal)
                    ready.set()
                    await portal.sleep_until_stopped()

            # a daemon thread, so an idle loop never holds up interpreter shutdown
            t = threading.Thread(target=anyio.run, args=(serve,), name="e2e-portal", daemon=True)
            t.start()
            ready.wait()
            _PORTAL, _PORTAL_THREAD = holder[0], t.ident
        return _PORTAL


class _Client(TestClient):
    @contextlib.contextmanager
    def _portal_factory(self):
        portal = _shared_portal()
        if threading.get_ident() == _PORTAL_THREAD:      # nested call from the loop itself
            with super()._portal_factory() as own:
                yield own
        else:
            yield portal


def load_world() -> dict:
    return json.loads(WORLD.read_text(encoding="utf-8"))


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


class E2E:
    def __init__(self, tmp: Path, *, ledger=None, answers=None, chain=None,
                 world_path: Optional[Path] = None):
        from mock_platforms.app import create_app
        self.world = json.loads((world_path or WORLD).read_text(encoding="utf-8"))
        self.facts = {f["id"]: f for f in self.world["facts"]}
        self.hooks: list = []
        self.deliver = True                       # False ⇒ webhooks are lost (Gate-2-only test)
        self.mock = create_app(world_path or WORLD, webhook_sink=self._sink, svc_token=SVC,
                               admin_token=ADMIN, webhook_secret=SECRET)
        self.svc = _Client(self.mock, base_url="http://testserver", headers={"Authorization": f"Bearer {SVC}"})
        self.admin = _Client(self.mock, base_url="http://testserver",
                                headers={"Authorization": f"Bearer {ADMIN}"})
        self.directory = Directory.from_world(self.world)
        self.connectors = {}
        for platform, (mod, cls, prefix) in CONNECTORS.items():
            try:
                klass = getattr(importlib.import_module(mod), cls)
            except (ImportError, AttributeError):
                continue
            self.connectors[platform] = klass(self.svc, self.directory, SECRET, self.admin,
                                              prefix=prefix)
        self.index = Index(tmp / f"index-{uuid.uuid4().hex[:6]}.sqlite")
        self.ledger = ledger or MemLedger()
        self.pipeline = Pipeline(index=self.index, directory=self.directory,
                                 connectors=self.connectors, ledger=self.ledger,
                                 answers=answers or MemAnswers(), chain=chain)
        self.ingestor = Ingestor(self.index, self.connectors, tmp / "cursors.json",
                                 directory=self.directory)
        self.ingestor.sync_all()

    def _sink(self, platform, headers, body):
        self.hooks.append((platform, headers, body))
        conn = getattr(self, "connectors", {}).get(platform)
        if conn is None or not self.deliver:
            return
        for ev in conn.parse_webhook({k.lower(): v for k, v in headers.items()}, body):
            self.ingestor.apply_event(ev)

    def user(self, persona: str):
        return self.directory.get(persona)

    def ask(self, persona: str, question: str):
        return self.pipeline.ask(self.user(persona), question)

    def admin_post(self, path: str, body: dict) -> dict:
        r = self.admin.post(path, json=body)
        assert r.status_code == 200, (path, r.status_code, r.text)
        return r.json()

    def apply(self, action: str) -> None:
        """Apply a manifest demo action (s2_update / s4_restrict_page / s4_remove_member)."""
        m = load_manifest()
        spec = m["s2_update"] if action == "s2_update" else m["s4_actions"][action]
        method, path = spec["admin_call"].split(" ", 1)
        body = spec["body"] if isinstance(spec["body"], dict) else {"body": spec["body"]}
        self.admin_post(path, body)

    def reset(self) -> None:
        """Restore the world (mock /admin/reset) and re-sync the index to it."""
        self.deliver = True
        self.admin_post("/admin/reset", {})
        self.ingestor.sync_all(full=True)

    def last(self, kind: str):
        return [e for e in self.ledger.events if e.kind == kind][-1]

    def platforms(self) -> set[str]:
        return set(self.connectors)

    def fact_platform(self, fid: str) -> str:
        return self.facts[fid]["doc"].split(":", 1)[0]
