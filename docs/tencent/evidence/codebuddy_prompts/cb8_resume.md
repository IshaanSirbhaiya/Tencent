Your previous turn in this session stopped on a CodeBuddy rate limit (HTTP 429) before the tests were written. The two server files are already changed; do not rewrite them. Same working rules as before.

Finish the task: create databridge/apps/internal_brain/tests/test_mcp_finals_tools.py with the tests (a) to (i) from the task, run it together with databridge/apps/internal_brain/tests/test_mcp_server.py, and fix any failure (you may change only the two server files and this new test file).

Note: audit_mcp_server._client now takes an optional `timeout` argument, so a test that patches `_client` must accept it (for example `lambda *a, **k: client`).

Reply with the tools added and the test results.
