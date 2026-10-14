1. **High — Map discloses documents after source permissions are revoked.**  
   **Location:** `databridge_ib/knowledge_map.py:30–43`.  
   **Scenario:** A document remains readable in the synced index after the source revokes access and a webhook is lost. `/ib/map` returns its title, ID, container, version, timestamp and link, and includes it in counts. This explicitly acknowledged behavior violates the requirement that callers learn nothing about items they cannot currently open.  
   **Minimal fix:** Recheck source authorization before including each document or deriving any metadata/counts from it. Exclude denied documents; fail closed on unavailable authorization. A freshness disclaimer does not enforce P2.

2. **High — Verify deliberately returns information when audit logging fails.**  
   **Location:** `databridge_ib/audit/routes.py:231–241, 257–259`.  
   **Scenario:** The ledger refuses the audit append, but the officer still receives verification results. Unlike the “counts and digests only” justification, the new report includes raw `answer_ref` values for missing or mismatched answers. This both violates the supplied unconditional ledger-down ⇒ 503 rule and exposes identifiers without a successful access record.  
   **Minimal fix:** Return a generic 503 when the append fails. Remove raw references from the health report; expose diagnostic identifiers only through an explicitly authorized, successfully ledgered diagnostic operation.

3. **Medium — Content verification silently skips released answers without references.**  
   **Location:** `databridge_ib/audit/routes.py:250–252`.  
   **Scenario:** An `answer.released` event with a missing or empty `answer_ref` contributes neither a check nor a problem. `_verify_content` can therefore report `intact: true, checked: 0` despite released answers having no verifiable content. Whether this can make the endpoint’s overall `ok` true depends on event validation and `ledger.verify()`, whose implementations are absent.  
   **Minimal fix:** Treat missing references and missing/invalid digests as verification problems unless the release schema explicitly defines a legitimate content-free variant. Test both variants explicitly.

4. **Medium — Integrity and content checks do not establish a shared verification snapshot.**  
   **Location:** `databridge_ib/audit/routes.py:226–233, 250`.  
   **Scenario:** Chain verification completes, then `_verify_content` independently enumerates events with `strict=False`. If the ledger changes between these operations, the returned head/record count describes one ledger state while the content result describes another. For example, content verification can include a newly appended release that the preceding chain report never verified. Concurrent content changes can likewise produce mixed-time results. The route shows no mechanism binding these reads together; internal guarantees cannot be assessed from the bundle.  
   **Minimal fix:** Obtain verified release records and a head from one immutable snapshot, then check content against those records. Report that snapshot’s head explicitly; use a consistent content-store read transaction where supported.

5. **Medium — The problem limit does not bound verification memory or work.**  
   **Location:** `databridge_ib/audit/routes.py:226, 250–263`.  
   **Scenario:** Each request performs ledger verification, enumerates the ledger again, fetches every referenced answer and hashes its entire text. If a large content store is missing or corrupted, one error string is allocated for every affected release; only the returned list is capped at 20. Repeated authorized requests can exhaust workers, storage throughput or memory.  
   **Minimal fix:** Maintain a total problem counter while retaining only the first `max_problems` messages. Bound concurrent verification jobs and run large checks as controlled jobs against a recorded head. Batch content reads where possible. Do not report a partial scan as intact.

6. **Medium — Malformed content can crash verification before its access is ledgered.**  
   **Location:** `databridge_ib/audit/routes.py:255–260`; append occurs at `235`.  
   **Scenario:** The check handles only `ContentStoreUnavailable` and `LedgerUnavailable`. If corrupted stored data produces an invalid `stored.answer` type or a decoding/validation error not translated by `AnswerStore`, hashing or deserialization raises outside that handler. The endpoint returns an error without its verification audit event. This is particularly relevant for a feature intended to inspect tampered storage. Exact exception reachability depends on the omitted `AnswerStore` implementation.  
   **Minimal fix:** Validate stored records at the storage boundary and normalize corruption/decoding failures into a defined verification failure. Ensure the route records failed verification attempts or returns generic 503 if logging is unavailable. Add real-store corruption tests.

7. **Medium — Following redirects bypasses the initial destination policy.**  
   **Location:** `databridge_ib/mcp_server.py`, diff `_http_post`, approximately lines `101–106`; `audit_mcp_server.py`, diff `_http_get`.  
   **Scenario:** Only the configured base URL is checked. An allowed HTTPS endpoint can issue a 307/308 redirect to a non-loopback HTTP destination; the ask client follows it and forwards the question body over plaintext. HTTPX stripping cross-origin authorization does not protect that body. This does **not**, by itself, demonstrate a bearer-token leak or a server-side channel-rule bypass.  
   **Minimal fix:** Disable redirects, or validate every redirect destination before following it and prohibit HTTPS downgrade and unapproved origin changes.

8. **Low — Map audit counts describe eligible items, not the items actually listed.**  
   **Location:** `databridge_ib/knowledge_map.py:52–59`.  
   **Scenario:** A container contains 250 readable documents. The response includes 200 item entries, but the event records `docs_listed=250`. An auditor cannot distinguish the 200 disclosed titles from the 50 documents represented only in an aggregate count. This is an audit accuracy defect; counting genuinely authorized documents is not itself a P2 violation.  
   **Minimal fix:** Record separate total-readable and returned-item counts. If the ledger must support reconstructing document access, also record the returned document identifiers or an appropriately protected manifest reference.

9. **Medium — The tests do not establish the advertised security properties.**  
   **Location:** `tests/test_knowledge_map.py:61–81`; `tests/test_audit_verify_content.py:29–43`.  
   **Scenario:** The map P2 test compares only two small, static fixtures and strips `as_of`. It cannot catch stale source revocations, authorization changes between `readable()` and `doc()`, cache invalidation faults, restricted-only container/platform effects, or timing/error differences. The ordering test observes an event after the request completes; an implementation that logs after response emission could satisfy that assertion. Content tests cover only an untouched store and appended text.  
   **Minimal fix:** Add:
   - Source revocation with a missed webhook, source authorization failure, and controlled authorization/index mutation during map construction.
   - Paired worlds with restricted-only containers/platforms and warmed caches, checking status, body and failure behavior.
   - An ASGI send probe proving successful append precedes response emission.
   - Missing content, absent reference/digest, wrong request ID, malformed stored values, ledger failure, concurrent verification changes, and more than 20 problems.
   - A large-ledger test verifying bounded diagnostic memory and controlled concurrency.

10. **Unassessable from this bundle — Three requested controls and their tests are missing.**  
    **Location:** No implementation or file:line supplied for the server’s bearer-only `workbuddy-mcp` enforcement, cross-process revocation lock, or deployed demo-passcode guard.  
    The MCP client setting `Authorization` and `x-ib-channel` does not prove the server rejects cookie-only requests or alternate identity paths. Nothing supplied establishes lock acquisition/release, stale-lock ownership checks, atomic updates, Windows behavior, or deployed configuration validation. Concrete severity, deadlock/race scenarios and minimal code fixes for those controls cannot responsibly be assigned without their implementations. No filesystem or external material was consulted.

11. **Correctly defended in the supplied code.**
    - `/ib/map` uses the authenticated dependency and an employee-role check; it accepts no caller-supplied subject.
    - Assuming `index.readable()` is correct and current, grouping and counting operate only on its returned IDs. Restricted documents do not directly seed containers or platform counts.
    - Successful map construction calls `ledger.append()` before returning its response object. Exceptions within `build_map()` produce a generic 503; the handler logs only the exception class.
    - The content check detects missing stored answers, changed answer text and request-ID mismatches for releases it actually examines.
    - Overall verification requires both the existing ledger result and content integrity; a passing content check cannot override a failed chain report.
    - Audit routes require the compliance role. Reveal logs before reading answer text and checks the request ID and sealed digest before disclosure.
    - MCP clients obtain identity credentials from `IB_TOKEN`, reject several unsafe base-URL forms, sanitize HTTP errors, close clients in `finally`, and reject non-object JSON responses.
    - Map event registration is added to both `EVENT_KINDS` and the ledger’s model mapping.