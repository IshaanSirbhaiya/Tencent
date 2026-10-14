In databridge/apps/internal_brain/databridge_ib/mcp_server.py implement a stdio MCP server using the python `mcp` SDK (already installed in the venv; use mcp.server.fastmcp.FastMCP) named "walnut-internal-brain". It is a thin client of the Internal Brain HTTP API at env IB_URL (default http://127.0.0.1:8100), authenticating with the bearer token in env IB_TOKEN (use httpx). Tools:
  - ask_internal_brain(question: str): POST /ib/ask with JSON {"question": question} and header "x-ib-channel: workbuddy-mcp"; return the answer text, the citations (title, platform, deep_link, version) and a line "answered as <sub>" where <sub> comes from GET /auth/whoami.
  - my_recent_questions(): GET /ib/history, returned as a short readable list.
No tool may take a user / identity / persona argument: identity comes only from IB_TOKEN. Fail clearly (a readable error string) when IB_TOKEN is missing or the API returns an error; never print the token. Provide a main() and `if __name__ == "__main__": main()` so it runs with `python -m databridge_ib.mcp_server`.

Also create databridge/apps/internal_brain/databridge_ib/audit_mcp_server.py, a second stdio MCP server "walnut-ib-audit" for compliance officers, same IB_URL / IB_TOKEN pattern, exposing:
  - audit_events(user: str = "", container: str = "", since_days: int = 30, doc: str = ""): GET /audit/events with those as query parameters (omit empty ones)
  - audit_doc_accessors(doc_id: str): GET /audit/doc/{doc_id}/accessors
  - audit_verify(): GET /audit/verify
(Here `user` is a filter on the audit log, not the caller's identity.) Read databridge/apps/internal_brain/databridge_ib/app.py and databridge_ib/audit/routes.py first to match the real request and response shapes.

Add tests in databridge/apps/internal_brain/tests/test_mcp_server.py: build the IB app in-process the same way tests/test_app_routes.py does, route the MCP tools' HTTP calls to it (for example by injecting an httpx client or transport), and assert (1) ask_internal_brain returns an answer with citations for a permitted question, (2) the tool input schemas of both servers' end-user tools contain no identity argument (no "user", "persona", "sub" or "principal" in ask_internal_brain or my_recent_questions), (3) a missing IB_TOKEN gives a clear error. Run the new tests and fix any failure.
