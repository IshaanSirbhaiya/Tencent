"""Poll fallback for freshness (plan §4.8): webhooks give seconds-level freshness; this loop bounds
the window when a webhook is lost or a platform cannot push (live track without public ingress).

Each tick runs ``Ingestor.sync_all()`` (cursor-based, committed only after a batch is applied).
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("databridge_ib.freshness")


class Poller:
    def __init__(self, ingestor, interval_s: float, *, full_every: int = 10) -> None:
        self.ingestor = ingestor
        self.full_every = max(1, int(full_every))     # every Nth tick: full reconciliation
        self._ticks = 0
        self.interval_s = max(5.0, float(interval_s))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="ib-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            self._ticks += 1
            try:
                self.ingestor.sync_all(full=self._ticks % self.full_every == 0)
            except Exception as exc:                                # noqa: BLE001
                log.warning("poll tick failed: %s", exc)
