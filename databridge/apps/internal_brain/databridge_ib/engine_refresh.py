"""Demo-only engine refresh: rebuild the Walnut engine snapshot and ask the gateway to reload it.

The original Walnut screens on ``/dev/brain`` read a build-time snapshot of Company A
(``scripts/build_enterprise_graph.py`` -> ``var/graph/enterprise.db``), built AS ONE PERSON SEES IT
(jdoe; D-09, D-21). This module rebuilds that snapshot in the background on request and then asks
the gateway, SERVER-SIDE, to reload it. It is a demo-proof feature (D-24): mounted only when
``IbConfig.demo_features_on``, and reachable only through ``/sim/engine-refresh`` and
``/sim/engine-status`` (``sim.py``).

* **Who.** ``admin_it``, or the person the snapshot is built for (``SNAPSHOT_PERSONA``), and only
  while demo sign-in is on; never on a deployment. Anyone else gets the simulator's constant 404.
* **One job at a time, at most one start per 2 minutes** (in memory, for everyone, because there
  is one snapshot). A start while a job runs returns that job's status; a start inside the
  2-minute window is ``429``.
* **The build** runs the stock script in a subprocess (``--as jdoe``) into a side file, so a failed
  build never touches the live snapshot. The script's own row step rewrites
  ``fixtures/enterprise/graph_rows/*.json`` deterministically from ``world.json`` (byte-stable).
* **Publish** copies the new database INTO the live file with SQLite's online backup, so it works
  while the gateway holds the file open (Windows cannot replace an open file). The gateway's
  store caches nothing, so its next read sees the new snapshot.
* **Reload** then calls the gateway's ``POST /graph/reload`` from this process with the gateway's
  local token (``IB_ENGINE_TOKEN``); the browser never holds that token. A failed reload is not a
  failed refresh (the published rows are already visible); the status says ``reloaded: false``.
* **Status** is ``{state, step, started_at, finished_at, snapshot_at, reloaded}``: no counts, no
  paths, no output of the build. Build output goes to the server log only.
* **Ledger.** A start is recorded write-ahead as ``ingest.sync`` (``detail.reason =
  "engine.refresh"``, ``phase = "started"``); a ledger failure is a 503 and nothing starts. The end
  is recorded as ``phase = "done"`` or ``"failed"`` (best effort; the job already ran).
* **Limit.** The snapshot is built from the world file, so a note added live with
  ``/sim/add-document`` appears in the Internal Brain's own graph, not in this snapshot.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from .config import DATABRIDGE_ROOT

log = logging.getLogger("databridge_ib.engine_refresh")

SNAPSHOT_PERSONA = "jdoe"
MIN_INTERVAL_S = 120.0
BUILD_TIMEOUT_S = 600.0
BUILD_SCRIPT = DATABRIDGE_ROOT / "scripts" / "build_enterprise_graph.py"


def _iso(t: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(t, timezone.utc).isoformat() if t else None


def default_build(out: Path) -> None:
    """Run the stock build script into ``out`` (raises on failure)."""
    r = subprocess.run([sys.executable, str(BUILD_SCRIPT), "--as", SNAPSHOT_PERSONA, "--out", str(out)],
                       cwd=str(DATABRIDGE_ROOT), capture_output=True, text=True,
                       timeout=BUILD_TIMEOUT_S, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    log.info("engine refresh build exit %s\n%s", r.returncode, (r.stdout or "")[-2000:])
    if r.returncode != 0:
        log.warning("engine refresh build failed:\n%s", (r.stderr or "")[-4000:])
        raise RuntimeError(f"build exited {r.returncode}")


def publish(src: Path, dst: Path) -> None:
    """Copy ``src`` into ``dst`` in place (SQLite online backup; works while ``dst`` is open)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    s = sqlite3.connect(str(src))
    try:
        d = sqlite3.connect(str(dst), timeout=30)
        try:
            s.backup(d, pages=1024, sleep=0.05)
        finally:
            d.close()
    finally:
        s.close()


class EngineRefresher:
    """Single-flight background rebuild of the engine snapshot."""

    def __init__(self, cfg, *, build: Optional[Callable[[Path], None]] = None,
                 reload: Optional[Callable[[], bool]] = None,
                 clock: Callable[[], float] = time.time, min_interval_s: float = MIN_INTERVAL_S,
                 run_inline: bool = False) -> None:
        self.cfg = cfg
        self.target = Path(getattr(cfg, "engine_graph_db"))
        self.build = build or default_build
        self.reload = reload or self._reload_gateway
        self.clock = clock
        self.min_interval_s = min_interval_s
        self.run_inline = run_inline                     # tests: run the job in the caller's thread
        self._lock = threading.Lock()
        self._state = "idle"
        self._step: Optional[str] = None
        self._started: Optional[float] = None
        self._finished: Optional[float] = None
        self._reloaded: Optional[bool] = None
        self._last_start: Optional[float] = None
        self._prev: Optional[tuple] = None

    # -- status ---------------------------------------------------------------------------------
    def status(self) -> dict:
        with self._lock:
            try:
                snap = self.target.stat().st_mtime if self.target.exists() else None
            except OSError:
                snap = None
            return {"state": self._state, "step": self._step, "started_at": _iso(self._started),
                    "finished_at": _iso(self._finished), "snapshot_at": _iso(snap),
                    "reloaded": self._reloaded}

    def may_start(self) -> str:
        """``"running"``, ``"rate_limited"`` or ``"ok"``."""
        with self._lock:
            if self._state == "running":
                return "running"
            if self._last_start is not None and self.clock() - self._last_start < self.min_interval_s:
                return "rate_limited"
            return "ok"

    def reserve(self) -> str:
        """Atomically claim the single job slot (round 3 L4): ``"running"``, ``"rate_limited"``
        or ``"ok"``; on ``"ok"`` the state is already ``running`` under the lock, so a second
        caller sees ``"running"`` and records nothing. Follow with ``run`` or ``release``."""
        with self._lock:
            if self._state == "running":
                return "running"
            now = self.clock()
            if self._last_start is not None and now - self._last_start < self.min_interval_s:
                return "rate_limited"
            self._prev = (self._state, self._step, self._started, self._finished, self._reloaded,
                          self._last_start)
            self._state, self._step = "running", "building"
            self._started, self._finished, self._reloaded = now, None, None
            self._last_start = now
            return "ok"

    def release(self) -> None:
        """Give back a reservation that will not run (its write-ahead record failed)."""
        with self._lock:
            prev = getattr(self, "_prev", None)
            if self._state == "running" and prev is not None:
                (self._state, self._step, self._started, self._finished, self._reloaded,
                 self._last_start) = prev
            self._prev = None

    def run(self, on_end: Optional[Callable[[str], None]] = None) -> None:
        """Run the job a successful ``reserve`` claimed."""
        self._prev = None
        if self.run_inline:
            self._run(on_end)
        else:
            threading.Thread(target=self._run, args=(on_end,), name="ib-engine-refresh",
                             daemon=True).start()

    def start(self, on_end: Optional[Callable[[str], None]] = None) -> bool:
        """Start a job; False when one runs or the 2-minute window is not over."""
        if self.reserve() != "ok":
            return False
        self.run(on_end)
        return True

    # -- the job --------------------------------------------------------------------------------
    def _set(self, **kw: Any) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self, f"_{k}", v)

    def _run(self, on_end: Optional[Callable[[str], None]]) -> None:
        side = self.target.with_name(self.target.stem + ".refresh.db")
        outcome = "failed"
        try:
            self.build(side)
            self._set(step="publishing")
            if self.target.exists():
                publish(side, self.target)
            else:
                self.target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(side, self.target)
            self._set(step="reloading")
            try:
                reloaded = bool(self.reload())
            except Exception as exc:                                # noqa: BLE001
                log.warning("engine reload failed: %s", type(exc).__name__)
                reloaded = False
            self._set(reloaded=reloaded)
            outcome = "done"
        except Exception as exc:                                    # noqa: BLE001
            log.warning("engine refresh failed: %s", exc)
        finally:
            for p in (side, Path(str(side) + "-wal"), Path(str(side) + "-shm")):
                try:
                    if p.exists():
                        p.unlink()
                except OSError:
                    pass
            self._set(state=outcome, step=None, finished=self.clock())
            if on_end is not None:
                try:
                    on_end(outcome)
                except Exception as exc:                            # noqa: BLE001
                    log.warning("engine refresh end hook failed: %s", type(exc).__name__)

    def _reload_gateway(self) -> bool:
        """``POST <engine_url>/graph/reload`` with the gateway's local token (server-side only)."""
        token = getattr(self.cfg, "engine_token", "")
        if not token:
            return False
        r = httpx.post(f"{str(self.cfg.engine_url).rstrip('/')}/graph/reload", timeout=30,
                       headers={"Authorization": f"Bearer {token}"})
        return r.status_code == 200


__all__ = ["EngineRefresher", "SNAPSHOT_PERSONA", "MIN_INTERVAL_S", "publish", "default_build"]
