# Live-ingestion kit: presenter runbook

Purpose: on Demo Day (Tue 3 Nov 2026) show that a new sentence or file goes into the brain **while the judges watch**, is answerable with a citation at once, and stays invisible to people without access. Everything is synthetic Company A data.

| Piece | Where |
|---|---|
| 12 samples (exact text, persona, channel/folder, questions, proof phrase) | `databridge/apps/internal_brain_web/public/finals/live_samples.json` |
| Sample files (docx, xlsx, pptx, WhatsApp txt, csv, md; all under 40 KB) | `docs/finals/live_kit/files/`, built by `make_files.py` (byte-identical on every run) |
| Absence proof (before) | `check_absent.py`, output `ABSENCE_PROOF.txt` |
| Judge's own sentence | `templates/` (`fill_template.py`, `slack_note_templates.md`) |
| Design of the feature | `docs/finals/live_growth/PLAN.md` |

## 0. Before you start (the morning of, and 10 minutes before going on)

1. Stack up with demo features: Internal Brain (`IB_DEMO_LOGIN=1`, `IB_DEMO_FEATURES=1`, not `IB_DEPLOYED`), mock platforms, web on 3100. The `/sim/*` endpoints (add-document, upload-file, check-absent) must exist; if `/sim/add-document` answers 404 for a member, demo features are off. Do not fix this on stage.
2. Reset to a clean world (the Reset control, or `POST /sim/reset` as `admin.it`). Reset re-reads every source, so anything added earlier leaves the index.
3. `python docs/finals/live_kit/check_absent.py --all` -> must end `RESULT: all checks passed`. This proves every sample is still absent and every "before" question still gets the refusal. It is read-only (it signs in with demo-login, which writes one `session.started` record per call).
4. Rehearse the ONE sample you will use at least twice, with the reset in between.

## 1. The scene (about 60 to 75 seconds of stage time per sample)

| Time | What happens | What you say |
|---|---|---|
| 0:00 | Ask the **before** question as the poster. The refusal card appears. | "The brain has never heard of this. Watch." |
| 0:10 | Open `/dev/graph?add=1`. The text is already in the box (or the judge dictates a sentence and Dev types it from `templates/`). Press Add / Upload. | "Now Priya posts it in Slack, the way she would on any Tuesday." |
| 0:15 to 0:25 | The node pulses into its channel/folder disc and a "Just added" entry appears. | Silence. Let the graph do it. |
| 0:25 | Ask the **after** question. The cited answer appears. | "Same question. Now it knows, and it shows where." |
| 0:45 | Switch to Ken (Compare, person mode). Same question: the refusal, unchanged. | "Ken is a contractor outside that channel. For him nothing changed, not even a count." |

Target ingest-to-answer: **under 10 seconds**. This is a target, not a measurement: the live-stack time was not measured when this kit was written. Time it in rehearsal and write the number on your card.

## 2. Reset after every rehearsal (and between takes on stage)

1. Reset (`/sim/reset` as `admin.it`, or the Reset button): the platform reloads its world and the Internal Brain runs a full reconciliation, so the added note or file leaves the index.
2. Re-run `check_absent.py <id>`. Only when it says PASS is the sample usable again. If it fails after a reset, do not use that sample; pick another.
3. Never delete from the ledger. Rehearsal records stay (they are part of the honest audit trail); the reset removes the *content*, not the audit.
4. One ingested sample at a time. Two samples touching the same topic (for example `up-cutover-docx` and anything about DBMIG-12) can give a mixed answer; reset between them.

## 3. If ingest is slow or fails

- **Over 10 s with no pulse:** keep talking about the Ken half ("while it indexes, here is what Ken sees: nothing"), then ask the after question once. Do not ask repeatedly.
- **Over 25 s or an error toast:** say "the webhook is slow on this laptop; here is the same step recorded earlier" and play the screen recording of the same sample (record each sample you may use during rehearsal and keep the clips next to the run sheet). Never claim it worked.
- **Upload refused (404 "not found"):** it is the same refusal by design. Check you are signed in as the person in the sample, the folder is the one listed, and the file is under 5 MB. Switch to a Slack note.
- **Answer arrives but without the new fact:** say "it is still indexing", wait 5 seconds, ask again once. If still absent, use the fallback clip.
- **Hy3 is slow or down:** the answer is the extractive one (quoted from the sources, same checks). Do not name a model.

## 4. What you may say, and what you may not

**May say**
- "This note was ingested live through the same connectors and permission checks as every other document."
- "It reached the brain the way any change does: the platform sent a signed change event, the Internal Brain re-read the source, masked sensitive values and indexed it."
- "For someone without access the answer is the same refusal as for a thing that does not exist."
- "The platforms are mock servers with real API shapes." (say it if asked; say it unprompted once)
- "The upload is read with Walnut's own readers; the file's text becomes a cited passage."

**May not say**
- "Real Slack" / "our Slack" / "connected to a customer's Slack". The platforms are mocks.
- "Instant" or any specific seconds figure you have not just timed.
- That the brain "understood" or "learned" the document beyond "indexed and cited".
- That it works for PDFs, images or audio live (PDF is supported by the reader but is not in this kit; do not demo it unrehearsed).
- Anything about the note being "approved" or "official": it is a demo post by a demo persona.
- That the Ken refusal "proves nothing leaked". The claim is the tested one: restricted equals non-existent, 400 pairs, 0 differences (re-run that morning).

## 5. Per-sample cards

Categories: `extends` (adds to a topic it knows), `changes-a-fact` (it now holds a newer fact; the old one is still cited, so read the dates), `new-topic` (nothing like it before), `permission-twist` (who can see it is the point). **Best first pick: sample 1** (the story the room has already seen). **Best "wow": sample 5** (the FinCrime note that Amy must not feel). **Best for "is it just hiding Ken?": sample 6 and 11** (Ken sees it, because the permission says so).

### 1. `shard3-resume`: Copy job resumes (extends the DB migration story)
*slack-note | category: extends | proof phrase: `copy job resumes`*

- **Sign in as:** `jdoe`   **Channel:** #db-migration (private)
- **Type (exact):** Update on DBMIG-12: replica tuning on ledger-shard-3 is done; the copy job resumes Thursday 5 Nov 2026, 21:00 SGT from batch 1,812. On-call for the restart: priya.sre.

**Before.** Sign in as `jdoe`, ask: *What is the restart time for the copy job?*
Expected: `I couldn't find an answer to that in the sources you can access.` and no citations.
Old fact (optional, shows 'it already knew something'): *When does the ledger-shard-3 copy job resume?* -> Today it says the copy job is paused at batch 1,812 and 'keep it paused until the shim fix is merged' (no date). This is the 'it knew something, now it knows more' beat.

**Action.** Open `/dev/graph?add=1` with demo features on (`IB_DEMO_FEATURES=1`), signed in as `jdoe`, pick the channel, paste or type the note, press Add.

**After.** Ask (as `jdoe`): *When does the ledger-shard-3 copy job resume?*
Must show, cited: `5 Nov 2026`, `21:00`, `batch 1,812`, `priya.sre`. The new node should pulse into the channel disc on the graph.

**Ken's view** (`ken.ext`, same question): `I couldn't find an answer to that in the sources you can access.` Exactly as before; his graph shows nothing new.

### 2. `wellness-day`: Wellness day (a brand-new topic)
*slack-note | category: new-topic | proof phrase: `wellness day`*

- **Sign in as:** `melissa.k`   **Channel:** #hr-announcements (public)
- **Type (exact):** People team: the company wellness day is Friday 20 Nov 2026. Offices are closed for everyone and no leave needs to be applied. Book your free health screening with the People team by Friday 13 Nov.

**Before.** Sign in as `hana.y`, ask: *When is the company wellness day in November 2026?*
Expected: `I couldn't find an answer to that in the sources you can access.` and no citations.

**Action.** Open `/dev/graph?add=1` with demo features on (`IB_DEMO_FEATURES=1`), signed in as `melissa.k`, pick the channel, paste or type the note, press Add.

**After.** Ask (as `hana.y`): *When is the company wellness day, and do I need to apply for leave?*
Must show, cited: `20 Nov 2026`, `no leave`. The new node should pulse into the channel disc on the graph.

**Ken's view** (`ken.ext`, same question): `I couldn't find an answer to that in the sources you can access.` Exactly as before; his graph shows nothing new.

### 3. `mfa-authenticator`: MFA moves to a new app (IT notice)
*slack-note | category: extends | proof phrase: `authenticator pro`*

- **Sign in as:** `admin.it`   **Channel:** #it-helpdesk (public)
- **Type (exact):** Heads-up from IT: on Monday 16 Nov 2026 company MFA moves to Authenticator Pro. Re-enrol at the IT portal before then. SMS codes stop working on Monday 30 Nov 2026.

**Before.** Sign in as `jdoe`, ask: *When does MFA move to Authenticator Pro?*
Expected: `I couldn't find an answer to that in the sources you can access.` and no citations.

**Action.** Open `/dev/graph?add=1` with demo features on (`IB_DEMO_FEATURES=1`), signed in as `admin.it`, pick the channel, paste or type the note, press Add.

**After.** Ask (as `jdoe`): *When does MFA move to Authenticator Pro, and when do SMS codes stop working?*
Must show, cited: `16 Nov 2026`, `30 Nov 2026`. The new node should pulse into the channel disc on the graph.

**Ken's view** (`ken.ext`, same question): `I couldn't find an answer to that in the sources you can access.` Exactly as before; his graph shows nothing new.

### 4. `deepavali-bridge`: An extra day off (changes a fact the brain already holds)
*slack-note | category: changes-a-fact | proof phrase: `bridge day`*

- **Sign in as:** `melissa.k`   **Channel:** #hr-announcements (public)
- **Type (exact):** People team update: the office is also closed on Tuesday 10 Nov 2026, a bridge day after the Deepavali holiday on Monday 9 Nov. No leave needed.

**Before.** Sign in as `hana.y`, ask: *Do we get an extra day off on 10 Nov?*
Expected: `I couldn't find an answer to that in the sources you can access.` and no citations.
Old fact (optional, shows 'it already knew something'): *When is the office closed for Deepavali in 2026?* -> Today: closed Monday 9 November 2026 (Confluence public-holidays page, which says 'the People team announces any change in #hr-announcements'). After: Monday 9 plus the Tuesday 10 bridge day, both cited.

**Action.** Open `/dev/graph?add=1` with demo features on (`IB_DEMO_FEATURES=1`), signed in as `melissa.k`, pick the channel, paste or type the note, press Add.

**After.** Ask (as `hana.y`): *Is the office closed on Tuesday 10 November 2026?*
Must show, cited: `10 Nov 2026`. The new node should pulse into the channel disc on the graph.

**Ken's view** (`ken.ext`, same question): `I couldn't find an answer to that in the sources you can access.` Exactly as before; his graph shows nothing new.

### 5. `fincrime-twist`: Permission twist: a FinCrime note Amy must never feel
*slack-note | category: permission-twist | proof phrase: `Nightjar Logistics`*

- **Sign in as:** `mlro.tan`   **Channel:** #fincrime-cases (private)
- **Type (exact):** FC-2290 update: enhanced due diligence on Nightjar Logistics Pte Ltd is complete. The case pack goes to the committee on Friday 6 Nov 2026.

**Before.** Sign in as `mlro.tan`, ask: *Is enhanced due diligence on Nightjar Logistics complete?*
Expected: `I couldn't find an answer to that in the sources you can access.` and no citations.

**Action.** Open `/dev/graph?add=1` with demo features on (`IB_DEMO_FEATURES=1`), signed in as `mlro.tan`, pick the channel, paste or type the note, press Add.

**After.** Ask (as `mlro.tan`): *Is enhanced due diligence on Nightjar Logistics complete?*
Must show, cited: `complete`, `6 Nov 2026`. The new node should pulse into the channel disc on the graph.

**Ken's view** (`ken.ext`, same question): `I couldn't find an answer to that in the sources you can access.` Exactly as before; his graph shows nothing new.
**amy.am's view:** `I couldn't find an answer to that in the sources you can access.` before and after. Amy (account manager) is not in #fincrime-cases. Her answer is the same refusal before and after, byte for byte. Her graph gets no new node, no count, no feed entry.

### 6. `vendor-review`: Permission twist, inverse: Ken IS in the channel and sees it
*slack-note | category: permission-twist | proof phrase: `business review is booked`*

- **Sign in as:** `leo.ops`   **Channel:** #vendor-acme (connect)
- **Type (exact):** Acme Vendor quarterly business review is booked for Thursday 12 Nov 2026, 15:00 SGT. Agenda: renewal terms and the 2027 support tiers.

**Before.** Sign in as `leo.ops`, ask: *When is the quarterly business review with the vendor?*
Expected: `I couldn't find an answer to that in the sources you can access.` and no citations.

**Action.** Open `/dev/graph?add=1` with demo features on (`IB_DEMO_FEATURES=1`), signed in as `leo.ops`, pick the channel, paste or type the note, press Add.

**After.** Ask (as `leo.ops`): *When is the Acme Vendor quarterly business review?*
Must show, cited: `12 Nov 2026`, `15:00`. The new node should pulse into the channel disc on the graph.

**Ken's view** (`ken.ext`): he DOES get the cited answer. Ken is a member of the shared (Slack Connect) channel #vendor-acme, so he sees the same cited answer. Same rule, different person, different result. Use this to show the system is not just 'hide everything from Ken'.

### 7. `up-cutover-docx`: Upload a Word plan that CHANGES the cutover date
*upload | category: changes-a-fact | proof phrase: `14 November 2026, 22:00`*

- **Sign in as:** `admin.it`   **Drive folder:** DB Migration (fd-3)
- **File:** `DB Migration — Wave 4 cutover plan v2.docx` (`docs/finals/live_kit/files/`)
- **Why this person:** admin.it may upload to any folder. Engineering folders give employees read only, so the upload is done by IT and the question asked as Priya.

**Before.** Sign in as `priya.sre`, ask: *What is the fallback date if the go/no-go call says no-go?*
Expected: `I couldn't find an answer to that in the sources you can access.` and no citations.
Old fact (optional, shows 'it already knew something'): *When is the wave 4 cutover for the ledger?* -> Today: planned for 24 October 2026 (Confluence). After: Saturday 14 November 2026, 22:00 SGT, with the Drive plan cited.

**Action.** Open `/dev/graph?add=1` with demo features on (`IB_DEMO_FEATURES=1`), signed in as `admin.it`, choose the folder, drop the file, press Upload.

**After.** Ask (as `priya.sre`): *When is the wave 4 ledger cutover now, and what is the fallback date?*
Must show, cited: `14 November 2026`, `21 November 2026`. The new node should pulse into the folder disc on the graph.

**Ken's view** (`ken.ext`, same question): `I couldn't find an answer to that in the sources you can access.` Exactly as before; his graph shows nothing new.

### 8. `up-fx-xlsx`: Upload an Excel fee draft (two sheets, one test card number that gets masked)
*upload | category: new-topic | proof phrase: `pricing committee on 18 November 2026`*

- **Sign in as:** `leo.ops`   **Drive folder:** Pricing & Offers (fd-6)
- **File:** `Q4 FX fee schedule — SG & ID (draft for review).xlsx` (`docs/finals/live_kit/files/`)
- **Why this person:** grp-product holds writer on this folder, so Leo can upload as himself.

**Before.** Sign in as `leo.ops`, ask: *What is the proposed Q4 fee for SG FX conversion on the Growth SME plan?*
Expected: `I couldn't find an answer to that in the sources you can access.` and no citations.

**Action.** Open `/dev/graph?add=1` with demo features on (`IB_DEMO_FEATURES=1`), signed in as `leo.ops`, choose the folder, drop the file, press Upload.

**After.** Ask (as `leo.ops`): *What does the Q4 draft propose for the SG FX conversion fee on the Growth SME plan, and when is it approved?*
Must show, cited: `0.30%`, `18 November 2026`. The new node should pulse into the folder disc on the graph.

**Extra beat.** The sheet carries a published test card number (4000 0566 5566 5556). It is masked at ingest; ask 'What test card number is in the FX fee draft?' to show it never appears in the answer.

**Ken's view** (`ken.ext`, same question): `I couldn't find an answer to that in the sources you can access.` Exactly as before; his graph shows nothing new.

### 9. `up-postmortem-pptx`: Upload a PowerPoint post-incident review (extends the outage story)
*upload | category: extends | proof phrase: `must close by 30 November 2026`*

- **Sign in as:** `admin.it`   **Drive folder:** Postmortems (fd-1)
- **File:** `Payment outage — post-incident review.pptx` (`docs/finals/live_kit/files/`)
- **Why this person:** Uploaded by IT, asked as Jordan (engineering reads the Postmortems folder).

**Before.** Sign in as `jdoe`, ask: *Who must close all five follow-up tickets by 30 November 2026?*
Expected: `I couldn't find an answer to that in the sources you can access.` and no citations.
Old fact (optional, shows 'it already knew something'): *What follow-up tickets were created after the payment outage?* -> Today: PAY-201 to PAY-205 listed, no deadline. After: the deadline 30 November 2026 and PAY-203 flagged as riskiest.

**Action.** Open `/dev/graph?add=1` with demo features on (`IB_DEMO_FEATURES=1`), signed in as `admin.it`, choose the folder, drop the file, press Upload.

**After.** Ask (as `jdoe`): *What did the post-incident review decide about the deadline for PAY-201 to PAY-205?*
Must show, cited: `30 November 2026`, `PAY-203`. The new node should pulse into the folder disc on the graph.

**Ken's view** (`ken.ext`, same question): `I couldn't find an answer to that in the sources you can access.` Exactly as before; his graph shows nothing new.

### 10. `up-whatsapp-oncall`: Upload a WhatsApp chat export (a team's messy on-call chat)
*upload | category: extends | proof phrase: `retry job resent 3,400`*

- **Sign in as:** `admin.it`   **Drive folder:** SRE Ops (fd-4)
- **File:** `WhatsApp Chat with Ops on-call.txt` (`docs/finals/live_kit/files/`)
- **Why this person:** Uploaded by IT; engineering reads SRE Ops (product also holds reader on it).

**Before.** Sign in as `jdoe`, ask: *What did the settlement retry job resend in one burst?*
Expected: `I couldn't find an answer to that in the sources you can access.` and no citations.

**Action.** Open `/dev/graph?add=1` with demo features on (`IB_DEMO_FEATURES=1`), signed in as `admin.it`, choose the folder, drop the file, press Upload.

**After.** Ask (as `jdoe`): *What caused the PAY-204 success-rate alert on 24 October?*
Must show, cited: `3,400`, `02:10`. The new node should pulse into the folder disc on the graph.

**Ken's view** (`ken.ext`, same question): `I couldn't find an answer to that in the sources you can access.` Exactly as before; his graph shows nothing new.

### 11. `up-vendor-csv`: Upload a CSV vendor SLA tracker (Ken can read this folder)
*upload | category: permission-twist | proof phrase: `SLA credit of SGD 1,800`*

- **Sign in as:** `leo.ops`   **Drive folder:** Vendor share (fd-7)
- **File:** `vendor-sla-tracker.csv` (`docs/finals/live_kit/files/`)
- **Why this person:** grp-product holds writer on Vendor share; ken.ext is a commenter on it, so Ken can read what lands there.

**Before.** Sign in as `leo.ops`, ask: *Who is owed an SLA credit by Acme Vendor for October?*
Expected: `I couldn't find an answer to that in the sources you can access.` and no citations.

**Action.** Open `/dev/graph?add=1` with demo features on (`IB_DEMO_FEATURES=1`), signed in as `leo.ops`, choose the folder, drop the file, press Upload.

**After.** Ask (as `leo.ops`): *What SLA credit does Acme Vendor owe for October?*
Must show, cited: `1,800`, `Sev 1`. The new node should pulse into the folder disc on the graph.

**Ken's view** (`ken.ext`): he DOES get the cited answer. Ken has comment access to the Vendor share folder, so he gets the same cited answer. The permission follows the folder, not the person's label.

### 12. `up-buddy-md`: Upload a Markdown People guide (staff only; Ken is outside the company)
*upload | category: new-topic | proof phrase: `buddy coffee allowance`*

- **Sign in as:** `melissa.k`   **Drive folder:** People (fd-13)
- **File:** `Onboarding buddy guide.md` (`docs/finals/live_kit/files/`)
- **Why this person:** grp-people holds writer; the whole company domain holds reader, Ken's acmevendor.example does not.

**Before.** Sign in as `hana.y`, ask: *Is there a buddy coffee allowance for onboarding buddies?*
Expected: `I couldn't find an answer to that in the sources you can access.` and no citations.

**Action.** Open `/dev/graph?add=1` with demo features on (`IB_DEMO_FEATURES=1`), signed in as `melissa.k`, choose the folder, drop the file, press Upload.

**After.** Ask (as `hana.y`): *How much is the buddy coffee allowance, and who do I message to volunteer?*
Must show, cited: `SGD 40`, `Hana Yusuf`. The new node should pulse into the folder disc on the graph.

**Ken's view** (`ken.ext`, same question): `I couldn't find an answer to that in the sources you can access.` Exactly as before; his graph shows nothing new.

## 6. When a judge gives you their own sentence

1. Ask for one sentence with a date or a number in it ("something only you would know").
2. Dev types it into the note box, posted in a channel the signed-in persona belongs to (`templates/slack_note_templates.md`), or runs `templates/fill_template.py` for a file.
3. Ask the judge's own question FIRST. If it refuses, that is your absence proof. If it answers, the topic already existed: say "it already had something on that" and read the old answer; the new line will then show next to it.
4. Quick offline check if there is time: `python docs/finals/live_kit/check_absent.py --phrase "<4 words>" --question "<the question>" --as jdoe`.
5. Never accept: a real person's name, a real card number, anything that looks like real customer data. Say "synthetic only, please make one up."

## 7. Known limits (say them if asked)

- Posting is demo-only (`IB_DEMO_FEATURES=1`, never on a deployment) and acts as the signed-in person; it cannot write into a channel the person is not in (that is a plain "not found").
- Confluence pages can be posted by IT only; this kit uses Slack and Drive uploads.
- Card numbers, NRIC and IBANs are masked at ingest, in the answer and in the cited passage.
- Ingest speed is bounded by the mock platform's webhook and the laptop; it was not measured on the final stack.
- `check_absent.py` proves absence from the *source world* and via real asks; the in-index count needs `--sim` (IT account, writes an audit record).

## 8. What was and was not verified when this kit was written (30 Oct 2026)

Verified against the running stack at 127.0.0.1:8100 (read-only asks) and `world.json`: all channels and folders exist; every poster is a member / has write access; every proof phrase is absent; all 12 "before" questions give the constant refusal for the right persona; Ken and Amy refuse where listed. See `ABSENCE_PROOF.txt` for the latest run.

NOT verified (nothing was ingested; `/sim/*` was deliberately not called): the "after" answers and their exact wording; the graph pulse; the ingest time; that each upload extracts cleanly through the live reader; Ken's positive view for samples 6 and 11 (derived from channel membership and the Drive permission list). Run each sample you plan to use once in rehearsal and write down what actually appears.
