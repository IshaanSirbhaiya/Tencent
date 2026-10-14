1. **High — Tokens are written to stdout.**  
   **File:** `databridge/scripts/ib_mint_token.py:77`  
   Running the documented command prints a usable bearer credential into terminal scrollback or captured CI logs, directly violating “secrets never printed.” Anyone obtaining it can impersonate the selected persona until expiry. Setup capturing stdout does not protect direct invocations.  
   **Minimal fix:** Have setup call the minting function directly. For CLI issuance, write only to an explicitly selected, access-restricted credential destination; never print the token.

2. **High — Credential output can land inside the repository and be readable by other users.**  
   **File:** `databridge/scripts/workbuddy_setup.py:43,52–62`  
   `--out .` writes all three tokens, including the compliance token, beneath the current directory. No repository-containment check exists. On POSIX with umask `022`, newly created directories/files ordinarily permit other local users to read those tokens; existing permissive files remain permissive. An outside-repository default does not enforce either protection.  
   **Minimal fix:** Resolve and reject destinations inside the repository, including symlink-resolved destinations. Create credential directories/files with owner-only permissions or equivalent Windows ACLs before writing; securely replace existing files. Prefer a credential store.

3. **Medium — Non-JSON and malformed successful responses produce unhandled tool failures.**  
   **Files:** `databridge/apps/internal_brain/databridge_ib/mcp_server.py:116–130,143–144`; `databridge/apps/internal_brain/databridge_ib/audit_mcp_server.py:142,156,165`  
   A proxy returning HTML with status 200, an empty 204 response, or an unfollowed 302 reaches `.json()` and raises. Valid JSON such as `[]`, or malformed citation/event entries, raises during `.get()` or formatting. Even a successful answer is lost if the subsequent whoami response is malformed. These are tool invocation failures; process termination is not established.  
   **Minimal fix:** Require an expected successful status, catch decoding errors, validate response shapes, and return a fixed sanitized error. Handle malformed whoami responses as unavailable identity metadata.

4. **Medium — Document IDs can change the requested audit endpoint.**  
   **File:** `databridge/apps/internal_brain/databridge_ib/audit_mcp_server.py:153`  
   `doc_id="../verify#"` builds `/audit/doc/../verify#/accessors`. URL normalization and fragment handling can turn that into a request to `/audit/verify`, rather than an accessor lookup. Ordinary IDs containing `?` or `#` also lose part of their value. This is endpoint confusion, not a demonstrated server-side authorization bypass.  
   **Minimal fix:** Percent-encode the document ID as path data, preserving the fixed route structure. Reject dot-segment traversal forms and test reserved characters and slash-containing IDs.

5. **Medium — Large `since_days` values crash the audit tool before HTTP handling.**  
   **File:** `databridge/apps/internal_brain/databridge_ib/audit_mcp_server.py:136–138`  
   Calling `audit_events(since_days=1000000000)` overflows `timedelta`; smaller sufficiently large values can underflow the datetime subtraction. Negative values silently omit the requested date filter.  
   **Minimal fix:** Declare and enforce a documented bounded integer range before date arithmetic, with an explicit policy for zero.

6. **Medium — Error messages echo potentially secret-bearing URLs.**  
   **Files:** `databridge/apps/internal_brain/databridge_ib/mcp_server.py:39`; `databridge/apps/internal_brain/databridge_ib/audit_mcp_server.py:41`  
   If `IB_URL` contains embedded credentials or a secret-bearing path/query, an HTTP error returns that URL to the MCP caller. Audit errors also echo the request’s filter query parameters. The Authorization header itself is not printed here.  
   **Minimal fix:** Return only the operation and status code, or a strictly sanitized origin. Reject credentials, queries, and fragments in the configured base URL.

7. **Medium — Remote HTTP URLs transmit bearer tokens without transport encryption.**  
   **Files:** `databridge/scripts/workbuddy_setup.py:45,60`; `databridge/apps/internal_brain/databridge_ib/mcp_server.py:49,64`; `databridge/apps/internal_brain/databridge_ib/audit_mcp_server.py:52–53`  
   Configuring `--ib-url http://remote-host:8100` sends the persona’s bearer token over plaintext HTTP. A network observer can replay it, including a compliance token.  
   **Minimal fix:** Require HTTPS for non-loopback destinations, retaining HTTP only for explicitly local development.

8. **Medium — Concurrent processes can lose token revocations.**  
   **File:** `databridge/apps/internal_brain/databridge_ib/auth.py:100–112`  
   The lock protects only one process. Two processes can read the same revocation list, each add a different JTI, and overwrite one another’s updates. Sharing the fixed `.tmp` filename can also cause replacement failures. A lost entry makes a revoked token acceptable again.  
   **Minimal fix:** Use a transactional store or an interprocess lock covering the complete read-modify-replace operation, with unique temporary files.

9. **Medium — Tests bypass the production HTTP helpers and omit audit authorization checks.**  
   **File:** `databridge/apps/internal_brain/tests/test_mcp_server.py:79–84,119–124,161–171`  
   Integration tests replace the helpers that attach credentials and handle transport errors. Consequently, broken Authorization headers, URL leakage, or transport handling can pass. The compliance fixture is unused; no supplied test invokes an audit endpoint as an employee or proves that `audit_events.user` cannot change the caller. Schema inspection alone cannot establish authorization.  
   **Minimal fix:** Mock the HTTP transport beneath the real helpers. Test employee rejection and officer success on every audit route, including `/ask` and `/reveal`; assert that filtering another subject still records the authenticated officer. Add malformed-response, overflow, URL-encoding, and credential-output regression cases.

10. **Low — The local launcher uses a Windows-only Python path separator on advertised POSIX platforms.**  
    **File:** `databridge/scripts/run_local_demo.sh:27`  
    On Linux/macOS, semicolons make the entire `PYTHONPATH` value one directory name. In a checkout relying on these paths rather than installed packages, `python -m databridge_ib` fails to import the application.  
    **Minimal fix:** Construct `PYTHONPATH` using the selected Python interpreter’s `os.pathsep`.

**Correctly defended in the supplied code:**

- Normal MCP tools expose no identity selector and obtain the bearer credential from `IB_TOKEN`. Audit `user` is passed as a filter; the route derives the acting subject separately from its authenticated dependency.
- Every shown audit route, including `/verify` and `/reveal`, uses the compliance-role dependency. No direct authorization bypass is demonstrated here; the dependency’s implementation is outside the bundle.
- Tokens use HMAC-SHA256 and constant-time signature comparison. Verification checks audience, expiration, and revocation membership and converts malformed-token failures into `TokenError`.
- An existing unreadable or malformed revocation list fails closed.
- Audit body models forbid extra fields. Reveal requires a reason, appends its audit event before reading answer content, and checks the request ID and sealed answer digest before release.
- Audit query responses are gated on a successful ledger append. `/verify` explicitly reports `ledgered: false` under its documented exception.
- Missing MCP tokens prevent HTTP requests. Caught transport errors expose only the exception class, not request headers.
- Minting rejects unknown/inactive personas and caps issued lifetimes at eight hours.

Filtering before the model, constant refusal behavior, and demo-login authentication cannot be verified from the supplied implementations.