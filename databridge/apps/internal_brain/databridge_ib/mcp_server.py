"""stdio MCP server ``walnut-internal-brain``: a thin client of the Internal Brain HTTP API.

Identity comes ONLY from the ``IB_TOKEN`` bearer token (P8). No tool accepts a user / persona /
sub / principal argument. The token is never logged. When ``IB_TOKEN`` is missing or the API
returns an error, the tool returns a readable error string (it does not raise).

Run with::

    python -m databridge_ib.mcp_server
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import FastMCP

DEFAULT_URL = "http://127.0.0.1:8100"
CHANNEL = "workbuddy-mcp"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

_SANITIZED_ERR = "Internal Brain API returned a malformed response."


def _base_url() -> str:
    return os.environ.get("IB_URL", DEFAULT_URL).rstrip("/")


def _token() -> str:
    return os.environ.get("IB_TOKEN", "").strip()


def _missing_token_error() -> str:
    return ("IB_TOKEN is not set. The Internal Brain MCP server needs a bearer token to identify "
            "the caller; identity cannot be passed as a tool argument. Set IB_TOKEN to the token "
            "issued by the Internal Brain and retry.")


def _api_error(resp: httpx.Response, what: str) -> str:
    return f"Internal Brain API error ({what}): HTTP {resp.status_code}."


def _client() -> httpx.Client:
    """Return an httpx.Client used for all HTTP calls. Tests patch this to route to an in-process app."""
    return httpx.Client(timeout=_TIMEOUT)


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
    # Reject URLs with embedded credentials, query, or fragment (finding 6).
    if parsed.username or parsed.password:
        return "IB_URL must not contain credentials. Remove the user:pass@ portion and retry."
    if parsed.query:
        return "IB_URL must not contain a query string. Remove the '?...' portion and retry."
    if parsed.fragment:
        return "IB_URL must not contain a fragment. Remove the '#...' portion and retry."
    # Plain HTTP is only allowed for loopback (finding 7).
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "http" and host not in ("127.0.0.1", "localhost", "::1"):
        return ("IB_URL uses plain HTTP for a non-loopback host. "
                "Use https:// for any non-local destination, or set IB_URL to a loopback address.")
    return None


def _http_get(client: httpx.Client, path: str) -> tuple[httpx.Response | None, str | None]:
    """GET ``path`` with the bearer token. Returns (response, error_message)."""
    token = _token()
    if not token:
        return None, _missing_token_error()
    url_err = _validate_base_url(_base_url())
    if url_err:
        return None, url_err
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = client.get(f"{_base_url()}{path}", headers=headers, follow_redirects=True)
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
    headers = {"Authorization": f"Bearer {token}", "x-ib-channel": CHANNEL}
    try:
        resp = client.post(f"{_base_url()}{path}", json=json_body, headers=headers,
                           follow_redirects=True)
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


def _format_citations(citations: list[dict[str, Any]]) -> str:
    if not citations:
        return "(no citations)"
    lines = []
    for c in citations:
        title = c.get("title") or c.get("doc_id") or "?"
        platform = c.get("platform") or "?"
        link = c.get("deep_link") or ""
        version = c.get("version") or "?"
        lines.append(f"  - [{platform}] {title} (v{version}) {link}".rstrip())
    return "\n".join(lines)


def _format_history(items: list[dict[str, Any]]) -> str:
    if not items:
        return "No recent questions."
    lines = [f"{len(items)} recent question(s):"]
    for i, it in enumerate(items, 1):
        q = it.get("question") or "?"
        rid = it.get("req_id") or "?"
        t = it.get("event_time") or "?"
        lines.append(f"  {i}. [{rid}] ({t}) {q}")
    return "\n".join(lines)


def build_server() -> FastMCP:
    """Construct the FastMCP server. Exposed for tests (and for ``main``)."""
    mcp = FastMCP("walnut-internal-brain",
                  instructions=("A thin client of the Internal Brain. The caller is identified by "
                                "the IB_TOKEN bearer token; no identity argument is accepted."))

    @mcp.tool()
    def ask_internal_brain(question: str) -> str:
        """Ask the Internal Brain a question and return the answer, its citations, and who it was
        answered as (from /auth/whoami). Identity comes only from IB_TOKEN.

        Args:
            question: The question to ask (1-2000 chars).
        """
        if not question or not question.strip():
            return "A non-empty question is required."
        client = _client()
        try:
            resp, err = _http_post(client, "/ib/ask", {"question": question})
            if err:
                return err
            data, json_err = _safe_json(resp)
            if json_err:
                return json_err
            answer = data.get("answer") or ""
            citations_raw = data.get("citations") or []
            if not isinstance(citations_raw, list):
                citations_raw = []
            citations = [c for c in citations_raw if isinstance(c, dict)]
            # whoami: identity comes from the token, never from the caller
            who_resp, who_err = _http_get(client, "/auth/whoami")
            sub = "(unavailable)"
            if who_err is None and who_resp is not None:
                who_data, who_json_err = _safe_json(who_resp)
                if who_json_err is None and isinstance(who_data, dict):
                    sub = who_data.get("sub") or "(unavailable)"
            parts = [
                answer.strip() or "(no answer text)",
                "",
                "Citations:",
                _format_citations(citations),
                "",
                f"answered as {sub}",
            ]
            return "\n".join(parts)
        finally:
            client.close()

    @mcp.tool()
    def my_recent_questions() -> str:
        """Return the caller's recent questions (from /ib/history), as a short readable list.
        Identity comes only from IB_TOKEN."""
        client = _client()
        try:
            resp, err = _http_get(client, "/ib/history")
            if err:
                return err
            data, json_err = _safe_json(resp)
            if json_err:
                return json_err
            items = data.get("items") or []
            if not isinstance(items, list):
                items = []
            items = [it for it in items if isinstance(it, dict)]
            return _format_history(items)
        finally:
            client.close()

    return mcp


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
