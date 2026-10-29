"""Demo simulator routes (``/sim/*``, role ``admin_it`` only) — proxy a FIXED set of platform admin
actions to the mock platforms so the S2/S4 demo moments can be triggered from the UI.

The actions are a closed list: the simulator cannot be used to issue arbitrary admin calls, and it
never writes the IB index directly — changes reach the Internal Brain only the way real changes
do (the platform's signed webhook, or a sync that re-reads the source).

Reset is the one action whose platform change sends no webhook (the mock reloads its world
silently), and the incremental poll compares the platform with its own cursor, which still holds
the pre-reset state, so it would never notice (a revoked member stayed revoked). ``/sim/reset``
therefore runs a FULL reconciliation (``Ingestor.reconcile``) right after the platform reset: every
document, ACL and membership is re-read from the source and the index is repaired to match.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException


def _manifest(world_path: Path) -> dict:
    try:
        return json.loads((Path(world_path).parent / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def make_sim_router(cfg, require_role: Callable[[str], Callable], *,
                    client_factory: Callable[[], httpx.Client] | None = None,
                    ingestor=None) -> APIRouter:
    router = APIRouter(prefix="/sim")
    admin = require_role("admin_it")

    def _client() -> httpx.Client:
        if client_factory:
            return client_factory()
        return httpx.Client(base_url=cfg.platform_base["confluence"], timeout=10,
                            headers={"Authorization": f"Bearer {cfg.admin_token}"})

    def _call(path: str, body: dict) -> dict:
        with _client() as c:
            r = c.post(path, json=body)
        if r.status_code >= 400:
            raise HTTPException(502, f"simulator action failed ({r.status_code})")
        return {"ok": True, "action": path, "at": datetime.now(timezone.utc).isoformat()}

    def _find(kind: str) -> dict:
        m = _manifest(cfg.world_path).get("demo_actions", {})
        if kind not in m:
            raise HTTPException(409, f"demo action '{kind}' is not defined in the world manifest")
        return m[kind]

    @router.post("/runbook-update")
    def runbook_update(_u=Depends(admin)):
        a = _find("runbook_update")              # {"page_id": ..., "body": ...}
        return _call(f"/admin/confluence/pages/{a['page_id']}/edit", {"body": a["body"]})

    @router.post("/revoke-channel")
    def revoke_channel(_u=Depends(admin)):
        a = _find("revoke_channel")              # {"channel": ..., "user": ...}
        return _call(f"/admin/slack/channels/{a['channel']}/members/remove", {"user": a["user"]})

    @router.post("/restrict-page")
    def restrict_page(_u=Depends(admin)):
        a = _find("restrict_page")               # {"page_id": ..., "users": [...], "groups": [...]}
        return _call(f"/admin/confluence/pages/{a['page_id']}/restrict",
                     {"users": a.get("users", []), "groups": a.get("groups", [])})

    @router.post("/reset")
    def reset(_u=Depends(admin)):
        out = _call("/admin/reset", {})
        if ingestor is not None:
            rec = ingestor.reconcile("sim.reset")
            out["reconciled"] = [r["platform"] for r in rec["platforms"]]
            if rec["failed"]:
                # the platforms are reset but the index could not be repaired for these: say so
                # (the poller's next full reconciliation retries them)
                out["ok"] = False
                out["reconcile_failed"] = rec["failed"]
        return out

    return router
