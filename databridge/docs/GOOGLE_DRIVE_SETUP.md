# Connecting Google Drive to Walnut — step by step

This lets Walnut read documents from **one folder in your own Google Drive**, read-only. It takes
about 20 minutes the first time. You do not write any code: every step is either a click in a web
page or a command you paste into PowerShell. You need a Google account and Node.js (which brings
the `npx` command — check with `npx --version`).

Walnut talks to Drive through a small helper program (an "MCP server") called
**`@piotr-agier/google-drive-mcp`**, version **2.12.0**. It runs on your computer. Your Google
password never goes to Walnut, and the sign-in token stays in a file on your computer that git
ignores.

> Use only the **synthetic demo documents** (Larkspur Analytics) in this folder. Do not put real
> company or personal data into it.

**What Walnut can and cannot do with this access.** It asks Google for `drive.readonly`: it can
list and read files, and it cannot create, change, share or delete anything. On top of that, the
Drive crawl profile names every one of the helper's 95 non-read tools as forbidden, and the crawl
calls exactly one tool (`search`). It only looks inside the folder you name — never your whole
Drive.

---

## Part 1 — Create a Google Cloud project (once)

1. Open <https://console.cloud.google.com/> and sign in with the Google account whose Drive you
   will use.
2. At the top of the page, click the **project picker** (next to "Google Cloud") → **New project**.
3. Name it `walnut-demo`. Leave "Location" as it is. Click **Create**, then make sure
   `walnut-demo` is the selected project (the picker at the top shows its name).

## Part 2 — Turn on the Google Drive API (once)

1. In the search bar at the top, type **Google Drive API** and open it.
2. Click **Enable**. Wait until the page says the API is enabled.

That is the only API Walnut needs. (The helper's own guide also lists the Docs, Sheets and Slides
APIs; those are for its editing tools, which Walnut never uses.)

## Part 3 — Set up the OAuth consent screen (once)

This is the screen Google shows when you sign in, asking "allow walnut-demo to see your files?".

1. Left menu → **APIs & Services** → **OAuth consent screen** (on newer consoles this is under
   **Google Auth Platform** → **Branding** / **Audience**).
2. If asked to "Get started": App name `Walnut demo`, support email = your email. Click **Next**.
3. **Audience / User type: External.** Click **Next**.
4. Contact email: your email. Agree to the policy. Click **Create** / **Finish**.
5. Go to **Audience** (or "Test users") → **Add users** → type **your own Gmail address** →
   **Save**. The app stays in **Testing** mode — that is correct. Only the test users you add can
   sign in.
6. (Recommended) **Data access / Scopes** → **Add or remove scopes** → tick
   `.../auth/drive.readonly` → **Update** → **Save**.

## Part 4 — Create the credentials file (once)

1. Left menu → **APIs & Services** → **Credentials** (or **Google Auth Platform** → **Clients**).
2. **Create credentials** → **OAuth client ID**.
3. **Application type: Desktop app.** (Not "Web application" — Desktop needs no redirect
   settings.) Name it `walnut-desktop`. Click **Create**.
4. In the dialog, click **Download JSON**. You get a file like `client_secret_123-abc.json`.

## Part 5 — Put the file where Walnut looks for it

Open PowerShell and go to the `databridge` folder (adjust the path if your copy lives elsewhere):

```powershell
cd "C:\DataBridge\DataBridge POC\databridge"
New-Item -ItemType Directory -Force var\secrets\gdrive
Move-Item "$HOME\Downloads\client_secret_*.json" var\secrets\gdrive\gcp-oauth.keys.json
```

Check it: `Test-Path var\secrets\gdrive\gcp-oauth.keys.json` must print `True`.

That folder is inside `var/`, which git ignores. **Never** email, paste or commit this file.

## Part 6 — Sign in once

Still in the `databridge` folder, paste all five lines:

```powershell
$env:GOOGLE_DRIVE_OAUTH_CREDENTIALS = "$PWD\var\secrets\gdrive\gcp-oauth.keys.json"
$env:GOOGLE_DRIVE_MCP_TOKEN_PATH    = "$PWD\var\secrets\gdrive\tokens.json"
$env:GOOGLE_DRIVE_MCP_SCOPES        = "drive.readonly"
$env:GOOGLE_DRIVE_MCP_AUTH_PORT     = "3100"   # the default (3000) is Walnut's own web page
npx -y @piotr-agier/google-drive-mcp@2.12.0 auth
```

1. A browser tab opens. Choose your Google account.
2. Google warns **"Google hasn't verified this app"** — expected for a testing app you made
   yourself. Click **Continue**.
3. Tick the permission to see your Drive files, click **Continue**.
4. The tab says authentication succeeded; PowerShell prints `Authentication successful`.
5. Check: `Test-Path var\secrets\gdrive\tokens.json` must print `True`. That file is your
   sign-in; it is reused from now on (also by the MCPJungle container, which mounts this folder
   at `/secrets/gdrive`).

**Do this before Part 8.** If Walnut starts the helper while you are not signed in, the helper
tries to open this sign-in page itself in the middle of the run, and the run stops after 60
seconds with "the server did not answer".

## Part 7 — Put the synthetic documents in a Drive folder

Pick **one** of the two ways.

**A. By hand, in the browser (no extra sign-in).**

1. Open <https://drive.google.com/>, click **New** → **New folder**, name it
   `Walnut demo — Larkspur`.
2. Open the folder, then drag **the contents of** `databridge\fixtures\workspace_demo\files`
   (the folders `contracts`, `engineering`, `finance`, `hr`, `meetings`, `planning`, `projects`,
   `sales`) into the Drive window. Keep the folder structure — including `planning\Q3 #roadmap`,
   whose `#` is deliberate.
3. Make sure Drive does **not** convert uploads to Google Docs (gear icon → **Settings** →
   **General** → "Convert uploads" off), so the PDFs and Word files arrive exactly as they are.
4. Optional, to see a native Google Doc handled too: right-click
   `meetings\2026-08-19_steering_corriedale.docx` in Drive → **Open with** → **Google Docs**, then
   **File** → **Save as Google Docs**. Walnut reads that copy as Markdown.

**B. With the upload script (it signs in separately, with permission to create files).**

```powershell
python scripts\gdrive_upload_demo.py                           # shows what it would do
python scripts\gdrive_upload_demo.py --upload --also-google-doc
```

The script signs in with Google's `drive.file` permission ("only files this app creates") and
keeps that sign-in in a **separate** file, `var\secrets\gdrive\tokens.upload.json`, so Walnut's
own read-only sign-in is never widened. A browser tab opens the first time — same steps as
Part 6. Running it again skips files that are already there. At the end it prints the folder id.
You can delete `tokens.upload.json` afterwards.

**Either way, copy the folder id.** Open the folder in Drive; the address bar shows
`https://drive.google.com/drive/folders/1AbCdEfGhIjK...` — the id is everything after
`folders/`.

## Part 8 — Connect the folder in Walnut and read it

In PowerShell, in the `databridge` folder (a fresh window is fine — the Part 6 settings are not
needed here, they are in Walnut's declaration):

```powershell
$env:PYTHONPATH = "$PWD\packages\mcp_ingest;$PWD\packages\retrieval"
$env:WALNUT_GDRIVE_FOLDER_ID = "1AbCdEfGhIjK..."        # your folder id from Part 7

python -m databridge_mcp_ingest connect --from-infra --only google_drive
python -m databridge_mcp_ingest plan google_drive
```

`connect` registers the Drive source declared in `infra\mcp_servers.yaml` (entry `google_drive`)
as `var\sources\google_drive.yaml`. `plan` starts the helper, reads its tool list and proposes
how to read the folder. **Check what it printed:**

- It must say `profiles=['google-drive-mcp']` and show no `PROBLEM:` lines. Then approve it with
  your name, and run it:

  ```powershell
  python -m databridge_mcp_ingest approve google_drive --by "Your Name"
  python -m databridge_mcp_ingest run google_drive
  python -m databridge_mcp_ingest status google_drive
  ```

  `run` prints progress as it goes. Add `--no-model` to `run` if no model key is configured —
  documents are then still read, screened and anchored, just without entity extraction.

- If it says `profiles=['mcp-server-gdrive']` instead, **do not approve**. That is an older,
  whole-Drive plan written for a different helper; it means the Drive profile has not been
  switched on in this copy of Walnut yet. Tell the developer: the profile is
  `packages\mcp_ingest\databridge_mcp_ingest\profiles\gdrive\google_drive_mcp.profile.yaml.pending`
  and needs the engine change in `packages\mcp_ingest\tests\fixtures\gdrive\`.

Keep `WALNUT_GDRIVE_FOLDER_ID` set in the window you run from: the declaration names the folder
as `${WALNUT_GDRIVE_FOLDER_ID}`, and it is looked up each time Walnut reads Drive (so the id is
never written into a file). To read the folder again later — after you add or edit files — just
repeat `run`. Only files whose content changed are processed again; a file you deleted from the
folder is reported as removed.

## Part 9 — What you should see

`status` prints the run's report; the files behind it are in
`var\ingest\google_drive\<run id>\`. For the demo upload:

- **15 files seen** (plus your optional Google Doc), `status: ok`, and verification without
  problems.
- The screen's decisions match `fixtures\workspace_demo\expected_outcomes.yaml`: **11 allowed,
  1 held for review** (the statement of work, "Total contract value"), **3 blocked** (the
  compensation bands, the planted private key, the rate card). Each hold and block is listed in
  `governance.jsonl` with the rule that fired and how to reverse it.
- The two `brief.md` files (and the two `notes.md`) stay two separate documents — identity is the
  folder path plus Drive's file id, never the file name.
- A Google Doc is read as Markdown; `captured.jsonl` records it with
  `drive_mime: application/vnd.google-apps.document` and its Drive modified time.
- Anything Walnut cannot read (a Google Form, a shortcut) is listed in `excluded.jsonl` with the
  reason — never silently skipped.

## If something goes wrong

| What you see | What it means / what to do |
|---|---|
| Google says **"access blocked"** | You are not a test user — Part 3, step 5. |
| Sign-in says a **port is in use** | Change `GOOGLE_DRIVE_MCP_AUTH_PORT` to another number, e.g. `3200`, and repeat Part 6. |
| `the server did not answer within 60s` | You are not signed in (no `tokens.json`), so the helper waited for a browser sign-in. Do Part 6, then retry. |
| `no roots: set scope.roots` or `${WALNUT_GDRIVE_FOLDER_ID} is referenced but not set` | Set `$env:WALNUT_GDRIVE_FOLDER_ID` in this window (Part 8). |
| `EMPTY RUN: the source listed nothing` | The folder id is wrong, the folder is empty, or it is in a Shared drive this account cannot see. Open the folder in Drive and copy the id again. |
| `No module named databridge_mcp_ingest` | Set `$env:PYTHONPATH` as in Part 8, from the `databridge` folder. |
| A Sheet only shows its first tab | Drive exports only the first tab of a Google Sheet as CSV. Download it as .xlsx and upload that instead. |
| `Invalid Credentials` / `invalid_grant` after weeks | Testing-mode sign-ins expire after 7 days. Delete `var\secrets\gdrive\tokens.json` and repeat Part 6. |

## Undoing it

- To sign Walnut out: delete `var\secrets\gdrive\tokens.json` (and `tokens.upload.json` if the
  upload script made one), and remove access at <https://myaccount.google.com/permissions> (find
  "Walnut demo").
- To forget the source in Walnut: delete `var\sources\google_drive.yaml` and
  `var\sources\google_drive.plan.yaml`.
- To remove everything: delete the `walnut-demo` project in the Google Cloud console.
