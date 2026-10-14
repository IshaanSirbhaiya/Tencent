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

import httpx
from mcp.server.fastmcp import FastMCP

DEFAULT_URL = "http://127.0.0.1:8100"
CHANNEL = "workbuddy-mcp"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _base_url() -> str:
    return os.environ.get("IB_URL", DEFAULT_URL).rstrip("/")


def _token() -> str:
    return os.environ.get("IB_TOKEN", "").strip()


def _missing_token_error() -> str:
    return ("IB_TOKEN is not set. The Internal Brain MCP server needs a bearer token to identify "
            "the caller; identity cannot be passed as a tool argument. Set IB_TOKEN to the token "
            "issued by the Internal Brain and retry.")


def _api_error(resp: httpx.Response, what: str) -> str:
    return f"Internal Brain API error ({what}): HTTP {resp.status_code} from {resp.url}."


def _http_get(path: str) -> tuple[httpx.Response | None, str | None]:
    """GET ``path`` with the bearer token. Returns (response, error_message)."""
    token = _token()
    if not token:
        return None, _missing_token_error()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = httpx.get(f"{_base_url()}{path}", headers=headers, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        return None, f"Internal Brain API is unreachable ({exc.__class__.__name__})."
    if resp.status_code >= 400:
        return resp, _api_error(resp, f"GET {path}")
    return resp, None


def _http_post(path: str, json_body: dict[str, Any]) -> tuple[httpx.Response | None, str | None]:
    """POST ``path`` with the bearer token. Returns (response, error_message)."""
    token = _token()
    if not token:
        return None, _missing_token_error()
    headers = {"Authorization": f"Bearer {token}", "x-ib-channel": CHANNEL}
    try:
        resp = httpx.post(f"{_base_url()}{path}", json=json_body, headers=headers, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        return None, f"Internal Brain API is unreachable ({exc.__class__.__name__})."
    if resp.status_code >= 400:
        return resp, _api_error(resp, f"POST {path}")
    return resp, None


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
        resp, err = _http_post("/ib/ask", {"question": question})
        if err:
            return err
        data = resp.json()
        answer = data.get("answer") or ""
        citations = data.get("citations") or []
        # whoami: identity comes from the token, never from the caller
        who_resp, who_err = _http_get("/auth/whoami")
        sub = "?"
        if who_err is None and who_resp is not None:
            sub = who_resp.json().get("sub", "?")
        elif who_err:
            sub = "(unavailable)"
        parts = [
            answer.strip() or "(no answer text)",
            "",
            "Citations:",
            _format_citations(citations),
            "",
            f"answered as {sub}",
        ]
        return "\n".join(parts)

    @mcp.tool()
    def my_recent_questions() -> str:
        """Return the caller's recent questions (from /ib/history), as a short readable list.
        Identity comes only from IB_TOKEN."""
        resp, err = _http_get("/ib/history")
        if err:
            return err
        items = resp.json().get("items") or []
        return _format_history(items)

    return mcp


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
