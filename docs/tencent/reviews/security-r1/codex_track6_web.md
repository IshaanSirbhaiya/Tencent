# Codex review, track 6: the web app (proxy, compare slots, map, tour)

Run: `codex exec -s read-only` (model and session in the log header), 2026-10-15, bundle of 9 files from `databridge/apps/internal_brain_web/` fed on stdin. Prompt:

> You are a senior web-application security reviewer. The full bundle is on stdin; review ONLY it. Find concrete defects: (1) the same-origin proxy: allowlist bypasses (path traversal, encoding, method confusion), cookie slot handling (x-ib-slot), Set-Cookie rewriting, any way slot b/c could read or overwrite the main session or another slot, CSRF exposure of state-changing routes, header injection, open redirect, SSRF via path; (2) identity: any way the browser can choose identity other than via demo-login; (3) information leaks: anything implying hidden items exist for the signed-in person, error messages leaking internals, caching of authenticated responses; (4) XSS: dangerouslySetInnerHTML, unsafe URL schemes in links (deep_link javascript:), tour/infotip content injection; (5) correctness bugs that break the demo (race conditions, stale map for a different person, unbounded timeouts). For each: severity (critical/high/medium/low), file:line, concrete scenario, minimal fix. Then list what is correctly defended. No preamble; numbered findings, most severe first.
> 

## Output (verbatim)

1. **High — Compare can display another persona’s data under the wrong identity.** `app/compare/page.tsx:101–113`; `lib/api.ts:156–168`  
   **Scenario:** Two Compare tabs share cookies `ib_session_b` and `ib_session_c`. Tab A logs slot b in as Jordan; tab B overwrites b with Ken before A sends its ask or map request. A retains Jordan’s login result but receives Ken’s data. The map’s request-ID check only checks the answer currently displayed; it never checks the map’s subject. This can also expose Jordan’s results in a column labelled Ken.  
   **Minimal fix:** Serialize each slot’s complete login → ask → map transaction across tabs, and validate response subjects against the expected subject before rendering. Prefer server-managed, isolated comparison contexts if concurrent comparisons must work.

2. **Medium — Dot-segment normalization bypasses the claimed closed path allowlist.** `app/api/ib/[...path]/route.ts:27,64–88`  
   **Scenario:** `/api/ib/audit/doc/%2e%2e/accessors` supplies the decoded document segment `..`. The allowlist accepts `audit/doc/../accessors`; `encodeURIComponent('..')` leaves it unchanged, and URL normalization sends `/audit/accessors` upstream. That path was never allowed. Whether it reaches a useful endpoint depends on backend routes absent from this bundle; this does **not** demonstrate arbitrary-host SSRF.  
   **Minimal fix:** Reject `.` and `..` segments, require a strict document-ID format, and validate the canonical outbound pathname against the same method-specific allowlist.

3. **Medium — State-changing routes have no proxy-level CSRF protection.** `app/api/ib/[...path]/route.ts:22–29,63–104`  
   **Scenario:** An attacker-controlled same-site sibling origin can submit a POST to the main-slot logout or simulator routes. The proxy checks neither `Origin` nor a CSRF token and accepts form content types. SameSite cookies do not protect against same-site sibling origins. Actual mutation depends on backend body validation and CSRF defenses, neither supplied here. Cross-site exploitation additionally depends on cookie attributes.  
   **Minimal fix:** Require an exact trusted `Origin` for state-changing browser requests, enforce JSON where applicable, and use a CSRF token when the origin policy cannot cover legitimate clients.

4. **Medium — Source links accept executable and other unwanted URL schemes.** `components/map/PlacePanel.tsx:34–37`  
   **Scenario:** A returned item with `deep_link: "javascript:..."` is placed directly into an anchor. There is no application-level scheme validation. Execution depends on React/browser handling and the new-tab target, so this bundle alone does not establish reliable script execution. `noopener noreferrer` is not a URL-scheme validator.  
   **Minimal fix:** Parse URLs and allow only approved schemes—normally `https:` and, if required, `http:`—plus explicitly approved application schemes. Render invalid links as text.

5. **Medium — Tour cancellation does not cancel or serialize identity changes.** `components/tour/Tour.tsx:75–89,118–120,183–203`  
   **Scenario:** Start a step that logs in as Maya, then move quickly to the admin step. Both login requests can remain in flight. If Maya’s response arrives last, its `Set-Cookie` replaces the admin session even though the old effect is marked dead. Closing the tour during login can likewise change the session afterward.  
   **Minimal fix:** Serialize persona changes and block step transitions while login is pending. Reconcile the effective identity before navigation or actions; an `alive` flag only suppresses JavaScript continuation, not cookie installation.

6. **Medium — Request buffering is unbounded, and response-body failures escape the error handler.** `app/api/ib/[...path]/route.ts:97–110`; `lib/api.ts:41–61`  
   **Scenario:** A slow incoming POST stalls at `await req.arrayBuffer()` before `AbortSignal.timeout(90_000)` is created. Large request and response bodies are buffered without an application limit. If the upstream sends headers and then stalls or disconnects, its body read can reject outside the `try`, producing a framework error instead of the promised 503. Browser requests also have no deadline.  
   **Minimal fix:** Bound body sizes, start a deadline before reading the incoming body, make body reading cancellable, include upstream body consumption in the error handler, and add a browser-side timeout. The existing signal does bound upstream fetching; the entire operation is not currently bounded.

7. **Medium — Main-slot cookie relay accepts arbitrary upstream cookies and unchecked attributes.** `app/api/ib/[...path]/route.ts:49–55,114`  
   **Scenario:** If an upstream response sets `ib_session_b`, `ib_session_c`, or another application cookie, a main-slot request relays it unchanged. This contradicts “only the session cookie” and allows an upstream cookie mistake to overwrite comparison sessions. Preserving an upstream-only `Domain` or a backend-relative `Path` can also make login silently fail through the proxy. An attacker-controlled upstream cookie source is not demonstrated here.  
   **Minimal fix:** Parse and accept only the exact `ib_session` cookie, rename it for every selected slot, and apply explicit frontend cookie scope and security attributes while preserving expiry/deletion semantics.

8. **Medium — Upstream error bodies are exposed without sanitization.** `app/api/ib/[...path]/route.ts:110–114`; `lib/api.ts:51–59`  
   **Scenario:** An upstream 500 containing a traceback, internal hostname, or permission-sensitive diagnostic is returned verbatim to the browser. Friendly messages in Compare do not prevent inspection of the network response. No particular secret-bearing backend response is supplied, so the leak contents remain conditional.  
   **Minimal fix:** Return a small, approved error envelope for upstream failures; retain detailed diagnostics in server logs with a correlation ID.

9. **Low — Invalid slot selectors silently operate on the main session.** `app/api/ib/[...path]/route.ts:43–45,70–74`  
   **Scenario:** A malformed selector such as `x-ib-slot: b,c` falls back to slot a. A logout or demo-login intended for a comparison slot consequently changes the main session; an audit request also avoids the extra-slot restriction. This is a fail-open routing bug, not a privilege escalation: callers can already explicitly select the main session.  
   **Minimal fix:** Default to a only when the header is absent; reject any supplied value outside the supported enumeration.

10. **Low — Passcode-protected demo login breaks tour persona changes.** `lib/api.ts:115–120,158–163`; `components/tour/Tour.tsx:82`  
    **Scenario:** The visitor signs in with a required demo passcode, which is remembered for Compare. The tour later calls `api.login(step.persona)` without that passcode, so changing to Maya or admin fails.  
    **Minimal fix:** Have tour login use the stored demo passcode through a shared login helper.

**Correctly defended, within the supplied code:**

- Exact b/c selectors read only their corresponding cookie; no missing-cookie fallback to the main token exists. Their relayed session cookies are renamed, and audit/simulator routes are denied.
- Browser authorization headers, identity headers, arbitrary cookies, and method-override headers are not forwarded. No alternate identity-selection mechanism is demonstrated outside demo-login. Token verification remains a backend responsibility not shown here.
- Method checks are paired with anchored path expressions. The upstream origin comes from server configuration; redirects are not followed and `Location` is not relayed. No concrete arbitrary-host SSRF or open redirect is demonstrated.
- Real upstream requests use `cache: 'no-store'`, and their relayed responses carry `Cache-Control: no-store`. Mock responses return before that policy is applied, so mock caching cannot be verified without `handleMock`.
- No supplied component uses `dangerouslySetInnerHTML`. Titles, search strings, SVG labels, tour text, and the shown InfoTip content use React rendering. Tour routes and selectors come from fixed definitions.
- The main map suppresses a response whose `sub` differs from an available `me.sub` and refetches. Protection while identity is unavailable, and identity refresh after another tab signs in, cannot be established without `AppShell`.
- Map listings and searches use returned items; no explicit hidden-item totals or denied-item placeholders appear. Backend filtering, audit authorization, and the omitted `AnswerPanel` cannot be verified.
- Tour anchor waits are bounded and observe effect cancellation; its interval and event listeners are cleaned up.
