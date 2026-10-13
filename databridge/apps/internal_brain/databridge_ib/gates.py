"""Gate 2 — ask the SOURCE whether the principal may read each candidate NOW (plan §4.2 step 6).

Every candidate passage that could reach a model goes through here; there is no cache on this path
(revocation is honoured on the next query — P3). Checks run in parallel with a hard per-check timeout;
an exception, a timeout, a 429 or a missing connector is a DENY (fail closed).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from datetime import datetime, timezone
from typing import Mapping, Sequence

from .connectors.base import SourceConnector
from .models import AccessCheck, DirectoryUser

_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="ib-gate2")


def _deny(doc_id: str, reason: str) -> AccessCheck:
    return AccessCheck(doc_id=doc_id, verdict="forbidden", reason_code=reason,
                       checked_at=datetime.now(timezone.utc))


def check_all(user: DirectoryUser, doc_ids: Sequence[str], platform_of: Mapping[str, str],
              connectors: Mapping[str, SourceConnector], *, timeout_s: float = 2.0
              ) -> dict[str, AccessCheck]:
    futures = {}
    out: dict[str, AccessCheck] = {}
    for d in dict.fromkeys(doc_ids):
        conn = connectors.get(platform_of.get(d, ""))
        if conn is None:
            out[d] = _deny(d, "no_connector")
            continue
        futures[d] = _POOL.submit(conn.check_access, user, d)
    for d, fut in futures.items():
        try:
            res = fut.result(timeout=timeout_s)
            out[d] = res if isinstance(res, AccessCheck) and res.doc_id == d else _deny(d, "bad_result")
        except FutTimeout:
            fut.cancel()
            out[d] = _deny(d, "timeout")
        except Exception:                                           # noqa: BLE001
            out[d] = _deny(d, "source_error")
    return out
