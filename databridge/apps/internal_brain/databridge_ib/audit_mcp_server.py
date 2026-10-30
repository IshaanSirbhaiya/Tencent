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
from urllib.parse import quote, urlparse

import httpx
from mcp.server.fastmcp import FastMCP

DEFAULT_URL = "http://127.0.0.1:8100"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_TAMPER_TIMEOUT = httpx.Timeout(120.0, connect=10.0)

_SANITIZED_ERR = "Internal Brain API returned a malformed response."
_SINCE_DAYS_MIN = 1
_SINCE_DAYS_MAX = 3650


def _base_url() -> str:
    return os.environ.get("IB_URL", DEFAULT_URL).rstrip("/")


def _token() -> str:
    return os.environ.get("IB_TOKEN", "").strip()


def _missing_token_error() -> str:
    return ("IB_TOKEN is not set. The audit MCP server needs a bearer token to identify the "
            "compliance officer caller; identity cannot be passed as a tool argument. Set IB_TOKEN "
            "to the token issued by the Internal Brain and retry.")


def _api_error(resp: httpx.Response, what: str) -> str:
    return f"Internal Brain API error ({what}): HTTP {resp.status_code}."


def _client(timeout: httpx.Timeout | None = None) -> httpx.Client:
    """Return an httpx.Client used for all HTTP calls. Tests patch this to route to an in-process app."""
    return httpx.Client(timeout=timeout or _TIMEOUT)


def _validate_base_url(url: str) -> str | None:
    """Return an error string if *url* is unsafe, or None if it is acceptable.

    An empty URL is allowed (tests route through a TestClient that ignores the base URL).
    """
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return (f"IB_URL must use http or https (got '{parsed.scheme}'). "
                "Set IB_URL to the Internal Brain base URL and retry.")
    if parsed.username or parsed.password:
        return "IB_URL must not contain credentials. Remove the user:pass@ portion and retry."
    if parsed.query:
        return "IB_URL must not contain a query string. Remove the '?...' portion and retry."
    if parsed.fragment:
        return "IB_URL must not contain a fragment. Remove the '#...' portion and retry."
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "http" and host not in ("127.0.0.1", "localhost", "::1"):
        return ("IB_URL uses plain HTTP for a non-loopback host. "
                "Use https:// for any non-local destination, or set IB_URL to a loopback address.")
    return None


def _http_get(client: httpx.Client, path: str,
              params: dict[str, Any] | None = None) -> tuple[httpx.Response | None, str | None]:
    """GET ``path`` with the bearer token. Returns (response, error_message)."""
    token = _token()
    if not token:
        return None, _missing_token_error()
    url_err = _validate_base_url(_base_url())
    if url_err:
        return None, url_err
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = client.get(f"{_base_url()}{path}", params=params or None, headers=headers,
                          follow_redirects=False)
    except httpx.HTTPError as exc:
        return None, f"Internal Brain API is unreachable ({exc.__class__.__name__})."
    if resp.status_code >= 400:
        return resp, _api_error(resp, f"GET {path}")
    return resp, None


def _http_post(client: httpx.Client, path: str,
               json_body: dict[str, Any]) -> tuple[httpx.Response | None, str | None]:
    """POST ``path`` with the bearer token. Returns (response, error_message)."""
    token = _token()
    if not token:
        return None, _missing_token_error()
    url_err = _validate_base_url(_base_url())
    if url_err:
        return None, url_err
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = client.post(f"{_base_url()}{path}", json=json_body, headers=headers,
                           follow_redirects=False)
    except httpx.HTTPError as exc:
        return None, f"Internal Brain API is unreachable ({exc.__class__.__name__})."
    if resp.status_code >= 400:
        return resp, _api_error(resp, f"POST {path}")
    return resp, None


def _safe_json(resp: httpx.Response) -> tuple[dict[str, Any] | None, str | None]:
    """Parse JSON from *resp*; return (data, None) or (None, sanitized_error)."""
    try:
        data = resp.json()
    except Exception:                                              # noqa: BLE001
        return None, _SANITIZED_ERR
    if not isinstance(data, dict):
        return None, _SANITIZED_ERR
    return data, None


def _format_events(payload: dict[str, Any]) -> str:
    events = payload.get("events") or []
    if not isinstance(events, list):
        events = []
    events = [e for e in events if isinstance(e, dict)]
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
    accessors_raw = payload.get("accessors") or []
    # The API returns accessors as a dict keyed by sub; accept both dict and list.
    if isinstance(accessors_raw, dict):
        accessors = [a for a in accessors_raw.values() if isinstance(a, dict)]
    elif isinstance(accessors_raw, list):
        accessors = [a for a in accessors_raw if isinstance(a, dict)]
    else:
        accessors = []
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
    if not isinstance(problems, list):
        problems = []
    status = "OK" if ok else "FAILED"
    lines = [f"Audit ledger verification: {status}",
             f"  records: {records}",
             f"  head: {head}",
             f"  ledgered: {ledgered}"]
    # stored-answer content check (counts only)
    content = payload.get("content")
    if isinstance(content, dict):
        checked = content.get("checked", 0)
        intact = content.get("intact")
        problems_text = content.get("problems") or []
        # derive missing/mismatched/unreadable counts from the problem strings
        missing = mismatched = unreadable = 0
        if isinstance(problems_text, list):
            for p in problems_text:
                ptext = str(p).lower()
                if "missing" in ptext:
                    missing = _extract_count(ptext)
                elif "does not match" in ptext or "mismatch" in ptext:
                    mismatched = _extract_count(ptext)
                elif "cannot be read" in ptext or "unreadable" in ptext:
                    unreadable = _extract_count(ptext)
        lines.append(f"  content check: checked={checked}, missing={missing}, "
                     f"mismatched={mismatched}, unreadable={unreadable}, intact={intact}")
    if problems:
        lines.append("  problems:")
        for p in problems:
            lines.append(f"    - {p}")
    return "\n".join(lines)


def _extract_count(text: str) -> int:
    """Pull the leading integer count from a problem string like '2 answer(s) missing...'."""
    import re
    m = re.match(r"\s*(\d+)", text)
    return int(m.group(1)) if m else 0


def _format_stats(payload: dict[str, Any]) -> str:
    """Headline numbers only from /audit/stats: no per-person or per-day breakdowns."""
    window = payload.get("window") or {}
    if not isinstance(window, dict):
        window = {}
    totals = payload.get("totals") or {}
    if not isinstance(totals, dict):
        totals = {}
    docs = payload.get("documents_flow") or {}
    if not isinstance(docs, dict):
        docs = {}
    lat = payload.get("latency_ms") or {}
    if not isinstance(lat, dict):
        lat = {}
    models = payload.get("models") or {}
    if not isinstance(models, dict):
        models = {}
    channels = payload.get("channels") or {}
    if not isinstance(channels, dict):
        channels = {}
    ledger = payload.get("ledger") or {}
    if not isinstance(ledger, dict):
        ledger = {}
    lines = [
        f"Window: {window.get('days', '?')} day(s) "
        f"({window.get('since', '?')} to {window.get('until', '?')})",
        f"Requests: asked={totals.get('requests', 0)}, "
        f"answered={totals.get('answered', 0)}, "
        f"refused={totals.get('refused', 0)}, "
        f"failed={totals.get('failed', 0)}, "
        f"answer_rate={totals.get('answer_rate')}",
        f"Citations: {totals.get('citations', 0)}",
        f"Documents: considered={docs.get('considered', 0)}, "
        f"gate2_checked={docs.get('gate2_checked', 0)}, "
        f"allowed={docs.get('gate2_allowed', 0)}, "
        f"denied={docs.get('gate2_denied', 0)}, "
        f"released={docs.get('released', 0)}, "
        f"cited={docs.get('cited', 0)}",
        f"Latency (ms): p50={lat.get('p50')}, p95={lat.get('p95')}",
        f"Models: {', '.join(f'{k}={v}' for k, v in models.items()) or '(none)'}",
        f"Channels: {', '.join(f'{k}={v}' for k, v in channels.items()) or '(none)'}",
        f"Reveals: {totals.get('reveals', 0)}",
        f"Audit queries: {totals.get('audit_queries', 0)}",
        f"Ledger: events={ledger.get('events', 0)}, head={str(ledger.get('head') or '')[:12]}",
    ]
    return "\n".join(lines)


def _format_tamper(payload: dict[str, Any]) -> str:
    """One line per scenario: title, expected, detected, behaved as expected, first_bad_seq."""
    records_copied = payload.get("records_copied", 0)
    real_modified = payload.get("real_log_modified")
    real_label = "yes" if real_modified else "no"
    lines = [f"Records copied: {records_copied}",
             f"Real log modified: {real_label}"]
    scenarios = payload.get("scenarios") or []
    if isinstance(scenarios, list):
        for s in scenarios:
            if not isinstance(s, dict):
                continue
            title = s.get("title") or s.get("id") or "?"
            expected = s.get("expected") or "?"
            detected = "yes" if s.get("detected") else "no"
            ok = s.get("ok")
            behaved = "yes" if ok else "no"
            fb = s.get("first_bad_seq")
            fb_label = f", first_bad_seq={fb}" if fb is not None else ""
            lines.append(f"  - {title}: expected={expected}, detected={detected}, "
                         f"as_expected={behaved}{fb_label}")
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
            since_days: Only events from the last N days (1..3650, default 30).
            doc: Filter by document id or title. Empty = no filter.
        """
        if not (_SINCE_DAYS_MIN <= since_days <= _SINCE_DAYS_MAX):
            return (f"since_days must be between {_SINCE_DAYS_MIN} and "
                    f"{_SINCE_DAYS_MAX} (got {since_days}).")
        params: dict[str, Any] = {}
        if user:
            params["user"] = user
        if container:
            params["container"] = container
        if doc:
            params["doc"] = doc
        since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=since_days))
        params["since"] = since.isoformat()
        client = _client()
        try:
            resp, err = _http_get(client, "/audit/events", params=params)
            if err:
                return err
            data, json_err = _safe_json(resp)
            if json_err:
                return json_err
            return _format_events(data)
        finally:
            client.close()

    @mcp.tool()
    def audit_doc_accessors(doc_id: str) -> str:
        """Who had access to a given document. The caller is identified by IB_TOKEN.

        Args:
            doc_id: The document id (e.g. \"confluence:cf-1001\").
        """
        if not doc_id or not doc_id.strip():
            return "A non-empty doc_id is required."
        if doc_id in (".", ".."):
            return "A valid doc_id is required ('.' and '..' are not allowed)."
        encoded = quote(doc_id, safe="")
        client = _client()
        try:
            resp, err = _http_get(client, f"/audit/doc/{encoded}/accessors")
            if err:
                return err
            data, json_err = _safe_json(resp)
            if json_err:
                return json_err
            return _format_accessors(data)
        finally:
            client.close()

    @mcp.tool()
    def audit_verify() -> str:
        """Verify the integrity of the access ledger (chain, seals, external head). The caller is
        identified by IB_TOKEN."""
        client = _client()
        try:
            resp, err = _http_get(client, "/audit/verify")
            if err:
                return err
            data, json_err = _safe_json(resp)
            if json_err:
                return json_err
            return _format_verify(data)
        finally:
            client.close()

    @mcp.tool()
    def org_activity_summary(days: int = 30) -> str:
        """Return the headline activity numbers for the whole organization over the last ``days``
        days (from /audit/stats?days=N): the window, requests asked/answered/refused/failed and the
        answer rate, citations, the document funnel, latency p50/p95, the models used, the channels,
        reveals and audit queries, and the ledger event count and the first 12 hex of its head.
        Per-person and per-day breakdowns are not included. Compliance officers only; identity
        comes only from IB_TOKEN.

        Args:
            days: The window in days (1..3650, default 30).
        """
        if not (_SINCE_DAYS_MIN <= days <= _SINCE_DAYS_MAX):
            return (f"days must be between {_SINCE_DAYS_MIN} and "
                    f"{_SINCE_DAYS_MAX} (got {days}).")
        client = _client()
        try:
            resp, err = _http_get(client, "/audit/stats", params={"days": str(days)})
            if err:
                return err
            data, json_err = _safe_json(resp)
            if json_err:
                return json_err
            return _format_stats(data)
        finally:
            client.close()

    @mcp.tool()
    def run_tamper_demo() -> str:
        """Run the tamper scenarios on a temporary COPY of the live ledger (the real log is never
        edited) and report, per scenario, whether the tamper was expected to be flagged, whether it
        was detected, whether it behaved as expected, and the first_bad_seq when present. A 409 means
        a demo is already running. Compliance officers only; identity comes only from IB_TOKEN."""
        client = _client(timeout=_TAMPER_TIMEOUT)
        try:
            resp, err = _http_post(client, "/audit/tamper-demo", {})
            if err:
                # a 409 means a demo is already running
                if resp is not None and resp.status_code == 409:
                    return "A tamper demo is already running; try again shortly."
                return err
            data, json_err = _safe_json(resp)
            if json_err:
                return json_err
            return _format_tamper(data)
        finally:
            client.close()

    return mcp


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
