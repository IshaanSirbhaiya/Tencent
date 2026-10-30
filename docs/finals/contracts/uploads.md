# Upload a real file: `POST /sim/upload-file` (finals, demo only)

For the UI and the live-ingest kit. Served by the Internal Brain (`databridge_ib`, :8100). Paths in the
code column are relative to `databridge/apps/internal_brain/databridge_ib/`; tests are in
`databridge/apps/internal_brain/tests/` unless a path says otherwise. Security notes:
`docs/tencent/SECURITY.md` §15.12.

## 1. What it does

A presenter drops a real file (Word, Excel, PowerPoint, PDF, CSV, Markdown, plain text or a WhatsApp chat
export) into a Google Drive folder as the signed-in person. The Brain learns it the way it learns any
Drive change:

1. `POST /sim/upload-file` checks who, where and what (section 4) and puts the file into Drive with the
   admin upload call `POST /admin/drive/files/upload` (mock platforms, :8200). The uploader is the
   person's own Drive account.
2. Drive stores the file under a generated id (`up-<hex>`), in the folder's shared drive, with NO direct
   permission: it inherits the folder's permissions. Drive fires its signed `content.changed` webhook.
3. The Drive connector verifies the webhook, fetches the file, and extracts its text with Walnut's own
   convert readers (`extract_files.py`, `connectors/drive.py`).
4. The ingest path masks card numbers, NRIC/FIN and IBANs (`sensitive.mask_doc`) and indexes the text
   with the ACL compiled from Drive's permissions.
5. Asking about it gives a cited answer to people who can open the folder, and the constant refusal to
   everyone else.

The route never writes the index. Code: `uploads.py` (route), `extract_files.py` (checks and readers),
`connectors/drive.py` (fetch and extract), `apps/mock_platforms/mock_platforms/uploads.py` (Drive side).

## 2. Request

`POST /sim/upload-file`, JSON, at most 7 MiB (`MAX_BODY_BYTES`). The web proxy forwards this one path with a
7 MiB limit (`app/api/ib/[...path]/route.ts`, `MAX_UPLOAD_BODY`).

| Field | Type | Rule |
|---|---|---|
| `filename` | string, 1..255 | Its extension picks the format (section 3). Display only; never a path. |
| `content_b64` | string | Standard base64 of the file; at most 5 MiB decoded. |
| `folder` | string | A Drive folder id, `[A-Za-z0-9_-]{1,128}` (e.g. `fd-6`). |
| `title` | string, optional, ≤ 120 | The display name instead of the filename's stem. |

No other field is accepted: `owner`, `user`, `sub` or anything else is a refusal. The uploader is the
signed-in person.

## 3. Formats

| Extension | Reader (`databridge_convert`, imported, not changed) | What is indexed |
|---|---|---|
| `.docx` | `adapters/source/docx.read` | Markdown: headings, paragraphs, tables |
| `.xlsx` | `adapters/excel.read_xlsx` | `Sheet: <name>`, title lines, then `Row N: <header>=<value>, ...` (header row from the reader's own header inference) |
| `.pptx` | `adapters/source/pptx.read` | `## Slide N` then the slide's text |
| `.pdf` | `adapters/source/pdf_text.read` (pypdfium2) | The text layer, one paragraph per line; at most 50 pages; no OCR |
| `.csv` | `adapters/csv.read` | `Row N: <column>=<value>, ...` (a raw CSV line is not answerable); a CSV with a header and no rows is indexed as its cleaned text |
| `.md` | `adapters/source/md.read` (validation) | The cleaned Markdown |
| `.txt` | `adapters/source/txt.read` (validation) | The cleaned text |
| `.txt`, WhatsApp export | our parser (`extract_files.whatsapp_text`) | One paragraph per message: `<date> <time>, <sender>: <message>`, under a one-line summary |

WhatsApp: both export forms are recognised, Android `24/10/2026, 02:14 - Name: message` and iOS
`[24/10/26, 2:14:05 AM] Name: message`. The date is kept as written (day and month order are never
guessed). System lines (the encryption notice, "created group", joins) and media placeholders are
dropped; a message's continuation lines are joined to it. A `.txt` that does not start with the export
header is ordinary text. Not accepted: `.doc`, `.xls`, `.ppt`, macro-enabled files (`.docm`, `.xlsm`,
`.pptm`), images, scanned PDFs without a text layer (they yield no text and are refused), archives.

## 4. Rules

| Rule | How | Test |
|---|---|---|
| Demo only | Needs `IB_DEMO_LOGIN=1` AND `IB_DEMO_FEATURES=1`; refused when `IB_DEPLOYED=1`. Applies to every person, `admin_it` included. | `test_uploads.py::test_demo_only_switches_refuse_everyone_including_admin_it`, `::test_demo_features_switch_is_read_like_the_other_switches` |
| Identity | From the session only; an extra body field is refused; Drive records the person's own account as the uploader. | `::test_the_body_names_no_person_and_the_owner_is_the_signed_in_person` |
| Write permission | An employee needs `writer`, `fileOrganizer`, `organizer` or `owner` on the folder (to them, a group they are in, or the company domain), read LIVE from Drive at upload time, one attempt; any source error is a refusal. Read access is not enough. `admin_it` may upload into any existing folder. | `::test_write_permission_is_required_and_read_is_not_enough`, `::test_write_permission_is_read_live_from_drive`, `::test_a_source_error_on_the_live_check_is_a_refusal`, `::test_admin_it_may_upload_into_any_folder` |
| Body limit | Over 7 MiB is refused from `Content-Length` or while streaming, before JSON parsing or base64 decoding. | `::test_oversized_body_is_refused_before_decoding` |
| File checks | Extension whitelist; bytes must match (OOXML zip with its own part folder, `%PDF-`, strict UTF-8 without NUL); at most 5 MiB; zip at most 2,000 entries and 64 MiB uncompressed (declared and streamed), ratio cap; no DTD or entity in any XML part; no `vbaProject.bin` or macro-enabled content type; PDF at most 50 pages; 15 s time budget in a worker process that is killed on overrun; text capped at 200,000 characters; a file with no text is refused. | `test_extract_files.py` (all), `test_uploads.py::test_bad_files_are_refused_with_the_one_response`, `::test_macro_and_bomb_reasons_are_ledgered` |
| Clean and mask | NFC; control, zero-width and bidi-override characters removed; HTML tags removed; then masked like any ingest. | `::test_text_is_cleaned_and_the_name_is_display_only`, `::test_card_and_nric_inside_a_docx_are_masked`, `::test_card_number_inside_an_xlsx_is_masked` |
| Filename | Tags and path parts dropped, reserved and control characters removed, capped at 120 characters with the extension kept; display only. Drive keeps the bytes under a generated id. | `::test_text_is_cleaned_and_the_name_is_display_only` |
| Rate | At most 5 attempts per person per minute (in memory). | `::test_rate_limit_five_per_minute` |
| Reset | `/sim/reset` reloads the platforms (the files are gone), reconciles the index, then `uploads.reset_uploads` drops any uploaded row still indexed and clears the rate window. | `::test_sim_reset_removes_uploads`, `::test_reset_hook_drops_a_lingering_upload_and_clears_the_rate_window` |

## 5. Responses

**200**

```json
{"ok": true, "doc_id": "drive:up-3f9c0a1b2c3d4e5f6a7b", "platform": "drive",
 "container": "0ACompany", "kind": "xlsx", "at": "2026-11-03T10:15:02.123456+00:00"}
```

- `doc_id`: the indexed document id; use it with `GET /ib/doc/{doc_id}` and the graph focus.
- `container`: the shared drive the folder belongs to.
- `kind`: `docx`, `xlsx`, `pptx`, `pdf`, `csv`, `md`, `txt` or `whatsapp`.
- By the time the 200 arrives, the webhook has been delivered; indexing takes as long as one ingest
  (in-process tests: immediately).

**404 `{"detail": "not found"}`** is the ONE refusal, byte-identical for every reason: switched off,
deployed, no employee role, unknown folder, no write permission, live check failed, wrong or unsafe file,
too large, malformed body, rate limited (`test_uploads.py::test_every_refusal_is_byte_identical`). The
reason is in the access ledger only: `request.failed` with `stage="sim.upload-file"` and an
`error_code` such as `demo_features_off`, `no_write_permission`, `source_error`, `body_too_large`,
`rate_limited`, `file_macros`, `file_xml_dtd`, `file_zip_bomb_total`, `file_pdf_pages`,
`file_time_budget`. The UI shows one message, for example "That file can't be added here."

**401** without a session, as everywhere.

## 6. Running it

- Both switches: `IB_DEMO_LOGIN=1` and `IB_DEMO_FEATURES=1` in the IB's environment.
- `packages/convert` is NOT on the IB's `PYTHONPATH` (`scripts/demo_up.sh`, `IB_PATH`); `extract_files`
  appends it to `sys.path` itself (last, so it shadows nothing)
  (`test_extract_files.py::test_readers_import_on_the_demo_pythonpath_without_packages_convert`).
- The readers run in one worker process started with the route (about 5 to 10 s to import); the first
  upload waits for it if it is not ready yet. Libraries used, all already in `requirements-lock.txt`:
  python-docx, python-pptx, python-calamine and openpyxl, pypdfium2.
- Sample files: `docs/finals/live_kit/files/` (built by `docs/finals/live_kit/make_files.py`); each
  live-kit upload sample in `public/finals/live_samples.json` is run end to end, before and after, Ken
  included (`test_uploads.py::test_live_kit_upload_end_to_end`).

## 7. Limits

- Demo only. Uploaded files live in the mock platforms' memory; a platform restart or `/sim/reset`
  removes them.
- The time budget is wall-clock on the worker process. A file is read once at upload (to refuse a bad
  file before it reaches Drive) and again by the connector on the webhook.
- No OCR: a scanned PDF has no text and is refused.
- Phone numbers in a WhatsApp export are not masked (masking covers cards, card security codes,
  NRIC/FIN and IBANs).
- Office files are parsed by third-party libraries (python-docx, python-pptx, calamine, openpyxl) and
  PDFs by PDFium, in the worker process, after the byte checks above; a parser bug could crash or hang
  the worker, which is then killed and replaced, and the file is refused.

## 8. STATUS / next steps (checkpoint, 30 Oct)

**Done.** Route `uploads.py` (behind `IB_DEMO_LOGIN=1` + `IB_DEMO_FEATURES=1`, refused when `IB_DEPLOYED=1`);
`extract_files.py` (byte checks, XML DTD refusal, per-format reader loading, spawn worker with a killed-on-overrun
15 s budget, WhatsApp parser); `connectors/drive.py` (Office / PDF / CSV extracted on fetch, WhatsApp `.txt`
rewritten); mock Drive upload (`mock_platforms/uploads.py`, one mount line in `mock_platforms/app.py`);
reset hook: `bootstrap.py` line `mount_uploads_router(app, svc, reset_hooks)` puts `uploads.reset_uploads`
first in the `/sim/reset` hooks (no `sim.py` change needed). SECURITY.md §15.12 written.

**Tested.**
- `tests/test_uploads.py`: 53 passed (every format incl. both WhatsApp forms, the six live-kit files end to
  end with before/after and Ken, masking, write permission live, switches, byte-identical refusal, body
  limit before decoding, rate limit, reset).
- `tests/test_extract_files.py`: 21 passed.
- `apps/mock_platforms/tests`: 151 passed.
- Full IB suite, run 1: 944 passed, 2 failed (`test_connector_drive.py::test_markdown_and_csv`,
  `::test_crawl_full_then_quiet`: the fake Drive's `df-7` is a header-only CSV, `a,b`, which extraction
  turned into "no text", so the file was skipped). FIXED: a header-only CSV is indexed as its cleaned text
  (`DriveConnector._extracted`); `test_connector_drive.py` now 65 passed. Full-suite run 2 was at 90 % with
  no failure when the session stopped; re-run it to confirm.

**Left / gotchas.**
- `python-pptx` and `pypdfium2` are installed in `.venv` but NOT in `requirements-lock.txt`. A fresh install
  from the lock loses pptx and pdf, and (because `databridge_convert/adapters/source/__init__.py` imports
  every document reader) docx, md and txt too; csv and xlsx keep working. Add `python-pptx==1.0.2` and
  `pypdfium2==5.14.0` to the lock (not done: no unvetted install or lock change by this agent).
- `IB_DEMO_FEATURES` is read from `cfg.demo_features` if `IbConfig` gets that field, else from the
  environment (`uploads.demo_features_on`). Restart the IB with both switches for the stage.
- The extraction worker starts with the route (about 5 to 10 s); `test_uploads.py` takes about 80 s
  (one shared E2E world). The worker is per process; it is killed on budget overrun and restarted.
- `docs/finals/live_kit/make_files.py` builds no PDF; PDF is covered by hand-built PDFs in the tests.
- Phone numbers in WhatsApp exports are not masked.
