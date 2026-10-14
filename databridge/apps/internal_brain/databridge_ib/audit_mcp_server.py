"""stdio MCP server ``walnut-ib-audit``: a thin client of the Internal Brain audit API, for
compliance officers.

Same IB_URL / IB_TOKEN pattern as :mod:`databridge_ib.mcp_server`: identity comes ONLY from
``IB_TOKEN``. The ``user`` argument on ``audit_events`` is a filter on the audit log (whose
activity is being inspected), NOT the caller's identity. The token is never logged. On a missing
token or an API error, each tool returns a readable error string.

Run with::

    python -m databridge_ib.audit_mcp_server
"""
from __future__ import annotations

import datetime as dt
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

DEFAULT_URL = "http://127.0.0.1:8100"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _base_url() -> str:
    return os.environ.get("IB_URL", DEFAULT_URL).rstrip("/")


def _token() -> str:
    return os.environ.get("IB_TOKEN", "").strip()


def _missing_token_error() -> str:
    return ("IB_TOKEN is not set. The audit MCP server needs a bearer token to identify the "
            "compliance officer caller; identity cannot be passed as a tool argument. Set IB_TOKEN "
            "to the token issued by the Internal Brain and retry.")


def _api_error(resp: httpx.Response, what: str) -> str:
    return f"Internal Brain API error ({what}): HTTP {resp.status_code} from {resp.url}."


def _http_get(path: str, params: dict[str, Any] | None = None) -> tuple[httpx.Response | None,
                                                                       str | None]:
    """GET ``path`` with the bearer token. Returns (response, error_message)."""
    token = _token()
    if not token:
        return None, _missing_token_error()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = httpx.get(f"{_base_url()}{path}", params=params or None, headers=headers,
                         timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        return None, f"Internal Brain API is unreachable ({exc.__class__.__name__})."
    if resp.status_code >= 400:
        return resp, _api_error(resp, f"GET {path}")
    return resp, None


def _format_events(payload: dict[str, Any]) -> str:
    events = payload.get("events") or []
    if not events:
        filt = payload.get("filter") or {}
        return f"No audit events matched the filter ({filt})."
    lines = [f"{len(events)} audit event(s):"]
    for ev in events:
        kind = ev.get("kind") or "?"
        sub = ev.get("sub") or "?"
        rid = ev.get("req_id") or "-"
        t = ev.get("event_time") or "?"
        lines.append(f"  - [{kind}] sub={sub} req={rid} at={t}")
    return "\n".join(lines)


def _format_accessors(payload: dict[str, Any]) -> str:
    doc_id = payload.get("doc_id") or "?"
    accessors = payload.get("accessors") or []
    truncated = payload.get("truncated")
    if not accessors:
        return f"No accessors found for document {doc_id}."
    lines = [f"{len(accessors)} accessor(s) of {doc_id}" +
             (" (truncated)" if truncated else "") + ":"]
    for a in accessors:
        sub = a.get("sub") or "?"
        accessed = "accessed" if a.get("accessed") else "denied-only"
        n = a.get("requests") or 0
        lines.append(f"  - {sub}: {n} request(s), {accessed}")
    return "\n".join(lines)


def _format_verify(payload: dict[str, Any]) -> str:
    ok = payload.get("ok")
    records = payload.get("records")
    head = payload.get("head")
    ledgered = payload.get("ledgered")
    problems = payload.get("problems") or []
    status = "OK" if ok else "FAILED"
    lines = [f"Audit ledger verification: {status}",
             f"  records: {records}",
             f"  head: {head}",
             f"  ledgered: {ledgered}"]
    if problems:
        lines.append("  problems:")
        for p in problems:
            lines.append(f"    - {p}")
    return "\n".join(lines)


def build_server() -> FastMCP:
    """Construct the FastMCP audit server. Exposed for tests (and for ``main``)."""
    mcp = FastMCP("walnut-ib-audit",
                  instructions=("Compliance-officer client of the Internal Brain audit API. The "
                                "caller is identified by the IB_TOKEN bearer token; the ``user`` "
                                "argument on audit_events is a filter on the log, not the caller."))

    @mcp.tool()
    def audit_events(user: str = "", container: str = "", since_days: int = 30,
                     doc: str = "") -> str:
        """List audit events, optionally filtered. The caller is identified by IB_TOKEN; ``user``
        filters the audit log (whose activity is inspected), it is not the caller's identity.

        Args:
            user: Filter by the subject whose activity is logged (e.g. \"jdoe\"). Empty = no filter.
            container: Filter by container/space/project/channel id or name. Empty = no filter.
            since_days: Only events from the last N days (default 30).
            doc: Filter by document id or title. Empty = no filter.
        """
        params: dict[str, Any] = {}
        if user:
            params["user"] = user
        if container:
            params["container"] = container
        if doc:
            params["doc"] = doc
        if since_days and since_days > 0:
            since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=since_days))
            params["since"] = since.isoformat()
        resp, err = _http_get("/audit/events", params=params)
        if err:
            return err
        return _format_events(resp.json())

    @mcp.tool()
    def audit_doc_accessors(doc_id: str) -> str:
        """Who had access to a given document. The caller is identified by IB_TOKEN.

        Args:
            doc_id: The document id (e.g. \"confluence:cf-1001\").
        """
        if not doc_id or not doc_id.strip():
            return "A non-empty doc_id is required."
        resp, err = _http_get(f"/audit/doc/{doc_id}/accessors")
        if err:
            return err
        return _format_accessors(resp.json())

    @mcp.tool()
    def audit_verify() -> str:
        """Verify the integrity of the access ledger (chain, seals, external head). The caller is
        identified by IB_TOKEN."""
        resp, err = _http_get("/audit/verify")
        if err:
            return err
        return _format_verify(resp.json())

    return mcp


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
