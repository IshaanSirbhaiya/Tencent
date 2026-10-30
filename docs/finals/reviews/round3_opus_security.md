# Round 3: security and correctness review (Opus seat)

Branch `final_v1.2`. This was a read-only review; this file is the only change. Every finding below was confirmed
by reading the code and, where marked, by a small in-memory Python check (`databridge/.venv`,
`IB_FORCE_OFFLINE=1`, scratch scripts outside the repo). Anything that earlier reviews already reported
(`docs/finals/deliverables/review_*.md`, `docs/finals/reviews/round2_*.md`,
`round2b_opus_unreviewed.md`) is left out unless it is still unfixed in the code.

Scope:
1. Deliverables (`databridge_ib/deliverables/*`, the `.pptx` export and the formula guard, `IB_DELIVERABLES`).
2. Live growth, add-document and engine refresh (`live.py`, `engine_refresh.py`, `sim.py`, `config.py`,
   `bootstrap.py`, and the demo-only gating).
3. File-upload ingest (`uploads.py`, `extract_files.py`, `connectors/drive.py`,
   `mock_platforms/uploads.py`, `docs/finals/contracts/uploads.md`).

Test baseline for this review: `test_connector_drive.py`, `test_uploads.py`, `test_extract_files.py`,
`test_demo_features.py`, `test_live_growth.py`, `test_deliverables.py` and `test_deliverables_verify.py` gave
**255 passed, 0 failed**. The two Drive failures listed in the deliverables and live-growth STATUS files are fixed.

## Counts

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 0 |
| Medium | 1 |
| Low | 7 |
| Info | 3 |

## Findings (ranked)

### M1. Medium: the `.pptx` formula guard misses `{=...}` array formulas and XlsxWriter's automatic links, so source text becomes a live formula or link in the chart's embedded workbook

- **Where:** `deliverables/export.py:20` (`_TRIGGER`), `:115-120` (`ptext`), `:185-188` (categories and series
  names). The cause is `xlsxwriter/worksheet.py:438-455`: `write()` treats a string as an array formula when
  it starts with `{=` and ends with `}`. With the default `strings_to_urls=True`, it also writes a hyperlink
  for a string that starts with `http(s)://`, `ftp(s)://`, `mailto:`, `internal:`, `external:` or `file://`.
  python-pptx writes chart categories and series names through `write()` (`pptx/chart/xlsx.py:162,174`).
  `verify.unsafe_text` (`verify.py:62`) blocks `http(s)://` and `www.`, but not `file://`, `external:`,
  `ftp://`, or a `mailto:` that has no `@`.
- **Scenario (confirmed):** a cited Drive CSV has the first-column label `{=cmd|' /C calc'!A0 market}` (or
  `{=HYPERLINK("file:///C:/x","Open market")}`) next to `0.35% FX margin`. With demo features on, any
  employee with write access to a folder can upload that file. The deterministic `source_chart` keeps the
  label, because every label token is in the same CSV row. `ptext` lets it through, because it starts with
  `{`. The exported deck's embedded workbook then contains
  `<c r="A2"><f t="array" ref="A2">cmd|' /C calc'!A0 market</f>`. When a reader opens "Edit Data" in
  PowerPoint, Excel evaluates it as a DDE or HYPERLINK formula. With the labels `external:c:\temp\x.xlsx
  market` and `mailto:a market`, the workbook gets a `<hyperlinks>` entry. A model-proposed chart reaches the
  same place. T15 (`test_pptx_embedded_workbook_has_no_formulas`) and the unit test plant only a leading
  `=`, so they pass. SECURITY §15.14 says every string "passes the formula guard" and the workbook holds
  no formula, which is more than is true (rule 8).
- **Fix:** in `ptext`, also prefix `'` when the string starts with `{` (after Unicode spaces) or matches
  `^(?:(?:ftp|http)s?://|mailto:|(?:in|ex)ternal:|file://)`, case-insensitive. Better still, write the
  workbook with `strings_to_formulas=False, strings_to_urls=False`, for example through a small
  `CategoryWorkbookWriter` subclass. Add `{=cmd|...}`, `{=HYPERLINK(...)}`, `external:` and `mailto:`
  cases to T15. The test should open the embedded XLSX and assert that there is no `<f` and no
  `<hyperlink`.

### L1. Low: deterministic `source_table` column headers skip the safety check, so the table shown and sealed differs from the export

- **Where:** `deliverables/build.py:84` (`"columns": [nfkc(c) for c in cols]`). The cells pass through
  `unsafe_text` (`:79`), but the headers do not. `create` returns `source_table` output as built
  (`service.py:315-317`). `recheck` on export runs `check_table`, which applies `check_short_text` to every
  column (`build.py:185-191`).
- **Scenario (confirmed):** a cited CSV has the header `market,contact ops@walnut-bank.example now,phone +65
  6123 4567 x`. At create time the deliverable shows those headers, and they go into the sealed response
  and the `deliverable.created` digest. The CSV or `.pptx` export of the same, unchanged sources has
  `Column 2`, `Column 3`. An e-mail address or phone number is shown "as ours", which the grounding wall
  forbids for cells. STATUS §1 ("Deterministic builds pass the same checks", Codex #13) is not true for
  headers.
- **Fix:** in `source_table`, replace a header that fails `unsafe_text` (or `check_short_text` against
  `ev.units`) with `Column n`, as `check_table` does. Alternatively, pass the deterministic table through
  `check_table` before sealing. Add a test that create and export columns are equal.

### L2. Low: the XML DTD/entity refusal can be bypassed (UTF-16 part, or a part not named `.xml`); only the readers' own parser settings stop XXE

- **Where:** `extract_files.py:92` (`_XML_MEMBER`), `:97` (`_DTD` is an ASCII byte regex), `:124` (only
  members named `.xml`, `.rels`, `.vml` or `[Content_Types].xml` are scanned).
- **Scenario (confirmed):** in a `.docx`, `word/document.xml` is re-encoded as UTF-16 with a BOM and holds
  `<!DOCTYPE ... <!ENTITY xxe SYSTEM "file:///etc/hostname">`. Or the main part is moved to
  `word/document.dat`, with `_rels/.rels` and `[Content_Types].xml` updated, and its DTD is in UTF-8. Both
  pass `check_bytes`. The extraction then succeeds (`'Hello'`, entities not expanded), because
  python-docx and python-pptx use lxml with `resolve_entities=False`. A UTF-16 `.xlsx` sheet with a DTD
  reached the reader and was refused as `unreadable`. So no file read was possible in these tests. But
  SECURITY §15.12 item 4 and `uploads.md` §4 say "no reader sees an external entity", and the bypass shows
  that is not what the check guarantees (rule 8).
- **Fix:** scan EVERY zip member, not only those with XML-looking names. Refuse any member that starts
  with a UTF-16 or UTF-32 BOM, or that declares a non-UTF-8 `encoding=` (OOXML parts written by Office are
  UTF-8). Alternatively, decode per the BOM before running `_DTD`. Add both bypass files to
  `test_xml_dtd_and_entities_are_refused_before_any_reader`.

### L3. Low: `/sim/upload-file` is always mounted, and on a deployment any employee can append unlimited ledger rows

- **Where:** `bootstrap.py:153` (mounted unconditionally) and `uploads.py:296-300` (the `_who` refusal is
  ledgered before the rate limiter is consulted). The web proxy allows `POST sim/[a-z-]+`
  (`internal_brain_web/app/api/ib/[...path]/route.ts:49`).
- **Scenario (confirmed):** with `IB_DEPLOYED=1`, demo login off and demo features off, 50 signed-in POSTs
  to `/sim/upload-file` gave 50× 404 and **50 `request.failed` rows** (`error_code="deployed"`). Nothing
  bounds it per person. Without a session the route answers 401 rather than the unknown-route 404. That
  differs from the D-24 rule for the other demo-only routes ("off, not mounted") and from the live-growth
  STATUS "Left 1", which is still open.
- **Fix:** mount the uploads router only when `cfg.demo_features_on and cfg.demo_login`, the same way
  `/sim/add-document` is mounted. If it must stay mounted, apply the rate limiter before writing a refusal
  row, and do not ledger refusals at all when the feature is off.

### L4. Low: two concurrent engine refreshes ledger two "started" records for one job

- **Where:** `sim.py:454-466` checks `may_start()`, ledgers, then calls `refresher.start()` and ignores
  its return value. `engine_refresh.py:119-144` holds the lock in two separate sections.
- **Scenario (confirmed):** two simultaneous `POST /sim/engine-refresh` calls, with a ledger write that
  takes 0.3 s, both return 200. One build runs, and the ledger records `started, started, done`. The
  second `started` names a person (`detail.by`) whose request started nothing, so the write-ahead record
  claims an action that did not happen.
- **Fix:** reserve atomically, then ledger, then run: `ticket = refresher.reserve()` (sets `running` under
  the lock, or returns `running`/`rate_limited`), then `_ledger_engine(...)`, releasing the reservation
  on a ledger failure, then `refresher.run(ticket)`. At minimum, ledger `phase="not_started"` when
  `start()` returns False.

### L5. Low: successful writes to a source on someone's behalf (add-document, upload-file) leave no ledger record; only refusals are recorded

- **Where:** `sim.py:303-337` (`_add`) and `uploads.py:227-277` (`_upload`). Both post to the platform
  with the ADMIN token, attributed to the session's person, and return without writing a ledger record.
  `Ingestor.apply_event` records ACL and membership changes only (as the `sim.py` docstring says).
- **Scenario:** jdoe uploads a 5 MB spreadsheet into a shared folder, or posts a note in a channel. The
  access ledger has no row saying jdoe wrote document X into container Y. It has rows only for refused
  attempts. Act's ticket creation, by contrast, is ledgered write-ahead (`action.*`, `models.py:326-379`).
  For a compliance reader the audit trail is inverted: failures are visible and successes are not. This is
  documented as intended, but it is weaker than rule 3's write-ahead principle for an action taken as a
  person.
- **Fix:** before the source call, write one record (sub, platform, container, kind, byte length and
  SHA-256, never the text or the name), and answer 503 when the ledger fails. After the call, write the
  resulting doc id.

### L6. Low: `clean_text` takes quadratic-style time on unclosed `<!--` and runs outside the worker's time budget

- **Where:** `extract_files.py:95` (`<!--.{0,2000}?-->`) is used by `clean_text` (`:211-218`). It runs in
  the IB process, not the worker, at `uploads.py:249` (after the worker has already passed the file),
  `connectors/drive.py:291` (`whatsapp_text` on every `text/plain` fetch: `is_whatsapp` looks only at the
  first 20,000 characters, then `clean_text` runs over the whole text) and `connectors/drive.py:316` (the
  header-only CSV fallback).
- **Scenario (confirmed timing):** `clean_text("<!--" * 1,310,720)` (5 MiB) took **24.7 s**. The same
  size with closed or ordinary tags took 0.15 to 0.17 s. Text whose first lines are WhatsApp headers,
  followed by about 1 MiB of `<!--`, passes the 15 s worker budget. It then costs several more seconds of
  IB CPU in the upload thread, and again on every connector fetch (webhook, reconcile, read-through), all
  outside the budget the contract describes.
- **Fix:** strip comments in one pass with `str.find` (when no `-->` follows, stop scanning), or cap the
  scan with a possessive or atomic construct. Alternatively, run all cleaning of untrusted text inside the
  worker only.

### L7. Low: the "every non-public route needs a session" test still skips every included router

- **Where:** `apps/internal_brain/tests/test_app_routes.py:51` iterates `app.routes`. FastAPI 0.143 keeps
  each included router as one lazy `_IncludedRouter` entry with no `path`. Confirmed: a minimal app's
  `app.routes` lists `('_IncludedRouter', None)`, and `iter_routes` lists `/sim/x`.
- **Scenario:** `/sim/*` and `/audit/*` (simulator actions, the audit console, tamper demo) are never
  probed by this test. A route added there without `Depends(current)` would pass CI. This is live-growth
  STATUS "Left 2", still unfixed. The L-5 test covers only the bootstrap app with the toggle on.
- **Fix:** use `iter_routes(app)` in both loops of `test_app_routes.py`.

### I1. Info: with the deliverables kill switch off, an invalid body answers 422, not the not-found 404

- **Where:** `deliverables/routes.py:58-71`. FastAPI validates the body before the handler calls `_on()`.
- **Confirmed:** switch off: valid body 404, `kind: "pie"` 422, export without `format` 422. The route's
  existence is already public in `/ib/status.deliverables`, so this exposes nothing, but the docstring
  ("the SAME 404 body") is not exact.
- **Fix:** accept the body as a raw `dict` and validate it after `_on()`, or reword the docstring.

### I2. Info: `/ib/graph/changes` ledgers a "new window" row on every cursor-less poll, with no rate limit

- **Where:** `live.py:219-221`. A signed-in employee polling without `since`, or with a garbage cursor,
  writes one `graph.viewed` row per request. This applies only while demo features are on. A per-person
  limit on window openings (for example 1 per 5 s) would bound it.

### I3. Info: an exception inside the deliverable build leaves `deliverable.requested` without a matching `failed`

- **Where:** `deliverables/service.py:222-223`. `_build` runs outside a try block. A verifier or builder
  bug reaches the route's generic 503 and writes no `deliverable.failed`, unlike the `context` failure
  path (`:210-216`).

## Verified safe (as far as checked)

- **Deliverables:**
  - **Ordering and lookups.** The order is session, role, kill switch, rate limit, `projection.owns`
    (no source read), then the write-ahead `deliverable.requested`, then `ActionService.context` (live
    Gate 1 and Gate 2). A not-owned, unknown or refused lookup, or a rate-limit refusal, gives one 404 or
    429 and is ledgered without ids. On export, the owner and TTL check runs before the format 422.
  - **Re-checks.** The sealed copy is deep-copied and never mutated. After a model call, the cited
    documents are authorised again and checked for version drift. On export, an authorisation error means
    `ok={}`, which means nothing is left, which means 404 (fail closed).
  - **Prompt and model output.** The model's orientation facts are filtered to openable tags
    (`actions.py:435`). The JSON parse accepts one whole object.
  - **Ledger and file delivery.**
    - A ledger failure raises `Unavailable`, which gives 503 and releases no bytes.
    - File names come only from fixed parts, with an `assert` in `content_disposition`.
    - The CSV guard covers `= + - @`, tab, CR, LF and the full-width forms after Unicode spaces, on every
      cell including the Sources block.
    - `safe_link` allows `http(s)` only, for both CSV and the `.pptx` hyperlinks.
    - The response headers include `nosniff` and `no-store`.
- **Live growth:**
  - **Cursor.** It is HMAC-keyed from the token key, holds the caller and the clock only, and is checked
    against the caller. A forged, foreign or expired cursor starts an empty window.
  - **Checks and cap.** Gate 1 intersects the candidates before any source call, then there is one
    deadline for every live check. The cap is applied after the check. Linked targets are limited to
    the caller's readable set.
  - **Response shape.** Count fields are `null`, and the shape is constant.
- **Demo gating:** `IbConfig.demo_features_on` is `demo_features and not deployed`. The live router and
  `/sim/add-document`, `check-absent`, `engine-refresh` and `engine-status` are mounted only when it is on.
  `/ib/status` reports the flag.
- **add-document:**
  - The body is closed, and the author comes from the session.
  - The container must be in Gate 1 and the person a member at the source now (fail closed).
  - Every refusal is the same 404, ledgered. The rate limit is 5 per minute.
  - Confluence posts are `admin_it` only, and the body is HTML-escaped.
- **check-absent:** `admin_it` only, demo login on, not deployed. The ledger gets the phrase's length and
  SHA-256 only, and a ledger failure is a 503.
- **Engine refresh:** jdoe or `admin_it`, demo login on, not deployed. The build goes to a side file and
  is published with SQLite online backup, so a failed build does not touch the live snapshot. The gateway
  token is used server-side only, and the status holds no paths or output.
- **Uploads:**
  - **Request handling.** The body cap is checked on `Content-Length` and while streaming, before
    parsing. The schema is closed, base64 is decoded with `validate=True`, and the extension is checked
    against a whitelist.
  - **Write permission.** The folder's live permissions are read with one attempt, and any odd entry
    fails the whole check closed. `anyone` never grants write. The directory's current identity must still
    match the Drive account.
  - **Name and permissions.** The display name is display-only. The mock stores the file under
    `up-<uuid>` with `permissions: []`, so it inherits the folder's ACL.
- **Extraction:**
  - **Zip limits.** Entry count, declared total and streamed total, the ratio cap, encrypted members, and
    `..`, absolute or `:` names are all refused.
  - **Macros and PDFs.** A `vbaProject.bin` member or a macro-enabled content type is refused. The PDF
    page cap is checked in the worker.
  - **Worker.** The worker runs one read at a time and is terminated when the budget runs out (code path
    read; covered by `test_a_read_past_the_time_budget_is_killed_with_its_worker`).
  - **Parsers and formula cells.** python-docx and python-pptx parse with `resolve_entities=False`. xlsx
    formula cells yield cached values only (a `=HYPERLINK(...)` cell with no cached value was omitted from
    the extracted text).
- **Lock file:** `python-pptx`, `pypdfium2`, `XlsxWriter`, `openpyxl`, `python-calamine`, `python-docx`
  and `opencv-python-headless` are in `requirements-lock.txt`. The `uploads.md` §8 "Left" note about the
  lock is out of date.

## Not reviewed

- **Web components:** `DeliverSheet.tsx`, `AddDocumentSheet.tsx`, `LiveFeed.tsx`, `useLiveChanges.ts`
  and the presenter overlays. The web proxy was read only for its allowlist, the upload body cap and the
  `content-disposition` relay.
- **Shared server code:** `actions.py` (`context`), `authz.py` internals, `graph.py` and `ingest.py`,
  beyond the call sites named above.
- **Mock platforms:** only `uploads.py` was read.
- **Reader memory:** peak memory of the readers inside the 15 s budget (for example an `.xlsx` near the
  64 MiB cap) was not measured. The contract lists it as a known limit.
- **PDF and the WhatsApp parser:** no PDF was tried adversarially (only the page-count path was read).
  The WhatsApp parser was read for safety, not for parsing accuracy.
- **Live runs:** no live Hy3/TokenHub model path, no browser runs, and no full IB suite (only the seven
  files listed above).
