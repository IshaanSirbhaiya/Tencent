# Codex review, track 7: verification of the track 6 fixes

Run: `codex exec -s read-only`, 2026-10-15, bundle = SECURITY.md §13 + the fix diff + the proxy file, fed on stdin. Prompt:

> You are a senior web-application security reviewer doing a fix-verification pass. The bundle on stdin has a diff that claims to fix ten findings, plus the proxy file after the fix. Review ONLY the bundle. For each of the ten findings: is it actually fixed (yes / partly / no), with file:line evidence. Then list any NEW defect the fix introduced (regressions, bypasses of the new guards: Origin/Sec-Fetch-Site logic, X-Forwarded-Host trust, body cap, cookie relay attribute handling, 5xx envelope, Web Locks usage and fallback, safeHref, tour serialization deadlocks). For each new defect: severity, file:line, concrete scenario, minimal fix. No preamble; be concise; numbered.

## Output (verbatim)

1. **Finding 1 — partly fixed.** `app/compare/page.tsx:124–143` holds both locks through login, ask and map; map identity is checked at `:115–118`. However, `:104–107` checks a subsequent `whoami`, not the identity that produced the answer. The unlocked fallback remains unsafe; see item 11.

2. **Finding 2 — yes.** `app/api/ib/[...path]/route.ts:26–35,61–62,168–169` rejects dot/separator segments and restricts document IDs; `:200–207` verifies the normalized outbound path. No traversal bypass is evident in the bundle.

3. **Finding 3 — partly fixed.** `route.ts:80–94,175–184` adds fetch-metadata, Origin and content-type checks. However, the Origin comparison ignores scheme and accepts untrusted host alternatives; see item 12. Modern browser requests carrying `Sec-Fetch-Site: cross-site` or `same-site` are rejected.

4. **Finding 4 — yes.** `lib/url.ts:5–12` parses and returns only normalized HTTP(S) URLs. Both citation and map links use it: `components/AnswerPanel.tsx:71–87`, `components/map/PlacePanel.tsx:20,36–53`. No executable-scheme bypass is evident.

5. **Finding 5 — yes, within one mounted tour overlay.** `components/tour/Tour.tsx:74–104` waits for the previous login and clears its pending reference in `finally`; `:195–206` prevents navigation and defers closing. This establishes serialization, but introduces the availability problem in item 15. Cross-tab main-session serialization is not implemented.

6. **Finding 6 — partly fixed.** `route.ts:100–129` counts actual streamed bytes, so missing or false `Content-Length` does not bypass the 64 KiB cap. `:164,217–229` applies the shared deadline to upstream headers and body and catches body failures. However, `lib/api.ts:38–40` silently supplies no browser timeout when `AbortSignal.timeout` is unavailable. The mock branch also does not apply the deadline to `handleMock` (`route.ts:188–197`).

7. **Finding 7 — yes for the stated restriction.** `route.ts:138–144` accepts only `ib_session`, renames it for the slot, strips Domain/Path and adds `Path=/; HttpOnly`. The attribute allowlist has a conditional regression; see item 14.

8. **Finding 8 — partly fixed.** `route.ts:233–238` replaces most 5xx bodies, but `:149–152` accepts **any** single-field JSON object containing an error string of at most 200 characters. That does not identify a trusted IB message; see item 13.

9. **Finding 9 — yes.** `route.ts:66–70,170–171` rejects unknown slot values with 400; only an absent header defaults to the main session.

10. **Finding 10 — yes.** `components/tour/Tour.tsx:87` uses `api.switchTo`; `lib/api.ts:143–144` supplies `storedPasscode()` to the shared login function.

11. **New guard bypass — High: unlocked Compare permits identity confusion.**  
    **Evidence:** `lib/api.ts:192–196`; `app/compare/page.tsx:104–108`.  
    **Scenario:** Without Web Locks, tab A logs in as Alice; another tab switches the slot to Bob before A’s ask, then back to Alice before A’s `whoami`. Bob’s answer passes the check and renders under Alice, exposing hidden content.  
    **Minimal fix:** Fail closed when locking is unavailable. For protection against writers outside this locking protocol, bind identity to the answer itself or use independently scoped per-tab sessions; a later `whoami` is insufficient.

12. **New guard bypass — Medium, deployment-dependent: Origin is matched against an overly broad trust set.**  
    **Evidence:** `route.ts:82–94`.  
    **Scenario:** With fetch metadata absent, `Origin: http://app.example` passes an HTTPS endpoint because only hosts are compared. Likewise, `Origin: https://evil.example` passes if an ingress preserves/appends attacker-controlled `X-Forwarded-Host: evil.example`; every comma-separated entry is trusted. These are guard bypasses, not proof that a normal browser can freely supply forwarded headers or bypass CORS.  
    **Minimal fix:** Compare the complete Origin against a configured public origin. Accept forwarded authority only from a trusted ingress that overwrites it; never trust an arbitrary list of candidates.

13. **New guard bypass — High: the 503 exception leaks arbitrary messages and can relay active HTML.**  
    **Evidence:** `route.ts:149–152,233–238`.  
    **Scenario:** A 503 containing `{"error":"Restricted document SecretPlan exists"}` passes unchanged. A valid JSON error string containing `<script>…</script>`, served upstream as `text/html`, also passes; direct navigation to a GET proxy route can execute it in the application origin.  
    **Minimal fix:** Map explicitly recognized error codes/messages to freshly constructed JSON responses with a fixed JSON content type. Never relay the original exception body or content type.

14. **New regression — Medium, conditional: cookie partitioning is removed.**  
    **Evidence:** `route.ts:132–144`.  
    **Scenario:** If upstream issues `ib_session=…; Secure; SameSite=None; Partitioned`, the relay drops `Partitioned`. Where unpartitioned third-party cookies are accepted, a session intended to be isolated by top-level site becomes shared. The bundle does not establish whether upstream currently uses this attribute.  
    **Minimal fix:** Preserve `Partitioned` and its required `Secure` flag, or explicitly enforce the intended partitioning policy when constructing the cookie. Retained duplicate/value-bearing HttpOnly attributes do not negate the added HttpOnly flag.

15. **New regression — Medium: stalled requests can trap the tour and hold Compare locks indefinitely.**  
    **Evidence:** `lib/api.ts:38–40,192–196`; `components/tour/Tour.tsx:87–100,195–206`.  
    **Scenario:** In a browser without `AbortSignal.timeout`, a stalled connection leaves the login promise pending: Back/Next remain disabled and Close/Skip never finish. A stalled Compare request similarly holds its locks, blocking other tabs. The server deadline cannot guarantee delivery through a stalled connection.  
    **Minimal fix:** Implement an `AbortController` timer fallback covering fetch and response consumption, with cleanup on settlement.

16. **New latent defect — Low: duplicate slot arguments self-deadlock.**  
    **Evidence:** `lib/api.ts:192–196`.  
    **Scenario:** `withSlotLocks(['b', 'b'], fn)` acquires the exclusive `b` lock, then waits for that same lock while retaining it. The shown Compare call uses distinct slots, so this is not triggered there. Sorting prevents ordering cycles but does not remove duplicates.  
    **Minimal fix:** Deduplicate before acquisition: `[...new Set(slots)].sort()`.
