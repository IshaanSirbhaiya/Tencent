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
from urllib.parse import quote, urlparse

import httpx
from mcp.server.fastmcp import FastMCP

DEFAULT_URL = "http://127.0.0.1:8100"
CHANNEL = "workbuddy-mcp"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

_SANITIZED_ERR = "Internal Brain API returned a malformed response."
# /ib/trace returns 404 for both an unknown id and another person's id (rule 4): the two are
# indistinguishable to the caller, so the receipt tool answers one fixed sentence.
_NOT_FOUND_RECEIPT = "No receipt found for that request id in your own requests."
_MAX_REQ_ID = 200


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
        resp = client.get(f"{_base_url()}{path}", headers=headers, follow_redirects=False)
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


def _format_receipt(data: dict[str, Any]) -> str:
    """A short readable receipt from a /ib/trace response."""
    question = data.get("question") or "(no question)"
    status = data.get("status") or "?"
    model = data.get("model") or {}
    if isinstance(model, dict):
        model_label = f"{model.get('provider', '?')}/{model.get('model', '?')}"
    else:
        model_label = "?"
    lat = data.get("latency_ms_rounded")
    lat_label = f"{lat} ms" if isinstance(lat, int) else "?"
    channel = data.get("channel") or "?"
    lines = [f"Question: {question}",
             f"Status: {status}",
             f"Channel: {channel}",
             f"Model: {model_label}",
             f"Latency: {lat_label}"]
    stages = data.get("stages") or []
    if isinstance(stages, list):
        stage_parts = [f"{s.get('name', '?')}:{s.get('status', '?')}"
                       for s in stages if isinstance(s, dict)]
        if stage_parts:
            lines.append("Stages: " + ", ".join(stage_parts))
    citations = data.get("citations") or []
    if isinstance(citations, list):
        cited = [c for c in citations if isinstance(c, dict)]
        if cited:
            lines.append("Cited documents you can open:")
            for c in cited:
                title = c.get("title") or "?"
                platform = c.get("platform") or "?"
                link = c.get("deep_link") or ""
                version = c.get("cited_version") or c.get("version") or "?"
                lines.append(f"  - [{platform}] {title} (v{version}) {link}".rstrip())
        else:
            lines.append("Cited documents you can open: (none)")
    records = data.get("records") or []
    if isinstance(records, list):
        recs = [r for r in records if isinstance(r, dict)]
        if recs:
            lines.append("Sealed records:")
            for r in recs:
                kind = r.get("kind") or "?"
                h = r.get("hash") or ""
                lines.append(f"  - {kind}: {str(h)[:12]}")
    return "\n".join(lines)


def _format_map(data: dict[str, Any]) -> str:
    """A SHORT summary of the caller's own access from a /ib/map response."""
    totals = data.get("totals") or {}
    if not isinstance(totals, dict):
        totals = {}
    n_items = totals.get("items", 0)
    n_containers = totals.get("containers", 0)
    n_platforms = totals.get("platforms", 0)
    lines = [f"You can open {n_items} item(s) across {n_containers} container(s) "
             f"on {n_platforms} platform(s)."]
    platforms = data.get("platforms") or []
    if isinstance(platforms, list):
        for p in platforms:
            if not isinstance(p, dict):
                continue
            pname = p.get("platform") or "?"
            pcount = p.get("count", 0)
            lines.append(f"- {pname}: {pcount} item(s)")
            containers = p.get("containers") or []
            if isinstance(containers, list):
                # at most 8 per platform, largest first
                sorted_c = sorted([c for c in containers if isinstance(c, dict)],
                                  key=lambda c: c.get("count", 0), reverse=True)[:8]
                for c in sorted_c:
                    cname = c.get("name") or c.get("container") or "?"
                    ccount = c.get("count", 0)
                    lines.append(f"    - {cname}: {ccount}")
    scope = data.get("scope_note")
    if scope:
        lines.append(f"Scope: {scope}")
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
            request_id = data.get("request_id") or ""
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
            if request_id:
                parts.append(f"request id: {request_id}")
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

    @mcp.tool()
    def my_answer_receipt(request_id: str) -> str:
        """Return a short readable receipt for one of the caller's own requests (from
        /ib/trace/{request_id}): the question, status, model, latency, stage names with ok/skipped,
        the cited documents still openable, and the sealed record kinds with the first 12 hex of
        each hash. An unknown id and another person's id both return one fixed sentence.
        Identity comes only from IB_TOKEN.

        Args:
            request_id: The request id returned by ask_internal_brain (1-200 chars).
        """
        if not request_id or request_id in (".", "..") or len(request_id) > _MAX_REQ_ID:
            return _NOT_FOUND_RECEIPT
        encoded = quote(request_id, safe="")
        client = _client()
        try:
            resp, err = _http_get(client, f"/ib/trace/{encoded}")
            if err:
                # A 404 (unknown id or another person's id) is indistinguishable (rule 4).
                if resp is not None and resp.status_code == 404:
                    return _NOT_FOUND_RECEIPT
                return err
            data, json_err = _safe_json(resp)
            if json_err:
                return json_err
            return _format_receipt(data)
        finally:
            client.close()

    @mcp.tool()
    def what_can_i_open() -> str:
        """Return a SHORT summary of the caller's own access (from /ib/map): total items, containers
        and platforms; per platform its item count and its containers (name and count, at most 8 per
        platform, largest first); and the scope note. Never lists item titles or document ids.
        Identity comes only from IB_TOKEN."""
        client = _client()
        try:
            resp, err = _http_get(client, "/ib/map")
            if err:
                return err
            data, json_err = _safe_json(resp)
            if json_err:
                return json_err
            return _format_map(data)
        finally:
            client.close()

    return mcp


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
