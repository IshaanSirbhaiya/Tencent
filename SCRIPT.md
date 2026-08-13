# SCRIPT — the demo video

> **What this is.** A shot-by-shot script for the Hack4Health submission video: what is on screen,
> what is said, and — for every claim made out loud — the command or the file that backs it, so
> nothing is asserted on camera that cannot be checked afterwards.
>
> **Target: 4 minutes.** Timings below sum to 3:50, leaving room to breathe. A 90-second cut is
> marked with ⚡ — record those beats first; if only the short version survives, it still contains
> the whole argument.
>
> **Before recording, read [`RUNNING.md`](RUNNING.md) and [`TESTING.md`](TESTING.md).** Every trial
> shown here is one of the numbered trials in `TESTING.md`, named in the *backing* column.

---

## Before you press record

| Check | Command | Expect |
|---|---|---|
| Stack up | `.\databridge\start-databridge.ps1` | gateway :8000, frontend :3000 |
| Which backend is live | `curl http://127.0.0.1:8000/health` | `llm_backend`, `graph_nodes: 358` |
| Tests green | `cd databridge; python -m pytest -m "not integration"` | all pass |

**Decide the backend before you record, and say which one on camera.**

- **Real model** (`OPENAI_API_KEY` set) — answers take 3–16s. Honest, but there is dead air, and
  the console occasionally declines to ground a count (measured: 1 run in 3 on *"how many documents
  are here?"*). Cut the pause; never cut a refusal.
- **`DATABRIDGE_FORCE_GOLD=1`** — instant and identical every take. This is the offline demo path,
  not a different product. If you use it, say so in one clause: *"running offline against the
  deterministic backend, so the timings are the UI's, not the model's."* Do not imply a model is
  answering when one is not — the whole submission rests on being precise about what is real.

**Have these open in tabs, in order:** `/frontdesk` · `/brain` · `/intelligence` · `/evidence` ·
`/impact`.

---

## The through-line

Say this in the first fifteen seconds and prove it for the remaining three and a half minutes:

> **The system reads the paperwork, and refuses to identify the patient.**

Everything else — the graph, the provenance, the console — is in service of that sentence. Judges
have seen document extraction before. They have not often seen a system that is built to decline.

---

## Beat 1 · The problem, on the actual data ⚡ — 0:00–0:30

**On screen:** a referral chit from `databridge/fixtures/hackathon/chits/`, full screen.

**Say:**
> A patient arrives at a Parkway Shenton clinic holding this. An insurer referral. Behind it: an
> employer voucher, a government authorisation form, an HR email, or nothing at all for a walk-in.
> Today one person at one counter reads it, finds the patient, works out what is covered, picks the
> questionnaire and drafts the bill — about **27 minutes**, sequentially. We read it before the
> patient reaches the counter.

**Backing:** `TESTING.md` §0. The 27.5-minute baseline is computed, not quoted — `/impact` derives
it and `test_submission_figures.py` pins it to `SUBMISSION.md`.

---

## Beat 2 · Upload, and the card ⚡ — 0:30–1:15

**On screen:** `/frontdesk` → drop the chit → the summary card.

**Do:** upload · let the card render · scroll it once, slowly.

**Say:**
> Classified, extracted, resolved against the registry, coverage determined from a config table,
> the right questionnaire pre-filled, the billing line drafted. One card.
>
> Now the important part. It has **not** identified this patient. It is showing candidates — with
> scores, and the reason each one matched. There is no button anywhere in this product that makes
> the system decide which of these people is standing at the counter.

**Do:** point at the candidate list. Try to confirm **without** ticking the in-person attestation.

**Say:**
> Confirming without attesting is refused. Not discouraged — refused, by the API, with a code.

**Backing:** `TESTING.md` §4 (upload → candidates → attestation → confirm → ledger). The refusal is
`IDENTITY_NOT_VERIFIED`; `ResolutionResult.requires_human_selection` is typed `Literal[True]`, so
there is no code path that sets it false.

---

## Beat 3 · Where every value came from ⚡ — 1:15–2:00

**On screen:** `/evidence`, then the node's evidence sheet.

**Do:** open the sheet · pick one value · show its pointer · resolve the span **live**:

```powershell
cd "C:\DataBridge\DataBridge POC\databridge"
python -c "t=open('fixtures/hackathon/chits/chit_01_meridian_referral.txt',encoding='utf-8').read(); print(repr(t[214:227])); print(repr(t[437:446]))"
```

**Say:**
> Every value carries a pointer to the characters it was read from. Not a confidence score — an
> offset. Here is the pointer, and here are those exact characters in the source file.
>
> Values it could not find are not guessed. They are listed as unresolved, by name. And a pointer
> that names a *rule* rather than a span says so — this date of birth was derived by a two-digit
> year pivot, and the citation says that instead of pretending it was printed on the page.

**Backing:** `TESTING.md` §1 — `doc_span`, `table_cell`, `derived` and `ocr_bbox` each get a trial;
A3 in `TRY.md` is this exact command.

**If you have 10 spare seconds:** the grounding wall drops any extracted value that cannot be
re-found in the source. That is what makes this a pointer rather than a promise.

---

## Beat 4 · The refusal that is the point ⚡ — 2:00–2:35

**On screen:** `/intelligence`. Click the chips in order — *Ask*, then *Act*, then *Refuses*.

**Do:**
1. *Ask* → **"which issuers sent documents?"** → grounded answer.
2. *Act* → **"email the patient that their questionnaire is outstanding"** → a **draft**, held for
   approval. Nothing sent.
3. *Refuses* → **"verify the identity of this patient"** → refusal.

**Say:**
> Three tiers, and the third is the one worth watching. It refused — and look at the call count:
> **zero**. It refused *before* the model was reached. That is a deterministic screen in code, not
> an instruction in a prompt. A prompt is a request; this is a guarantee.
>
> And the middle one drafted a message. It did not send it. The process that talks to a mailbox
> has no send tool at all — that is which process a tool lives in, not a label on it.

**Backing:** `TESTING.md` §3 and §7 (26 things that should refuse). `model_calls: 0` is the tell.
The read/send split is `MCP_RUNBOOK.md` §1 — the reader's capability manifest does not contain
`send_email`, which is a stronger statement than a blocked call.

---

## Beat 5 · Names are not identities — 2:35–3:05

**On screen:** `/brain`, ask **"Who is Loh Wei Ming?"**

**Say:**
> The registry holds **three** people called Loh Wei Ming. Ask about that name and the system
> refuses to pick one — and hands back the three ids, so you can ask again about exactly one of
> them. Ask by id and it answers immediately.
>
> It answers with the caveat attached: a registry row is not a verified identity. That holds for
> all 300 patients on file, including the 289 no document has ever mentioned.

**Do:** show the three rows behind the claim:

```powershell
python -c "import csv; rs=list(csv.DictReader(open('../Hack4Health/patient_registration_synthetic.csv',encoding='utf-8'))); n=[k for k in rs[0] if 'name' in k.lower()][0]; i=[k for k in rs[0] if 'nric' in k.lower()][0]; [print(f'  record {x+1}: {r[n]} / {r[i]}') for x,r in enumerate(rs) if 'Loh Wei Ming' in r[n]]"
```

**Backing:** `TESTING.md` §2. Both `/brain/query` branches refuse — the deterministic one and the
model one — which matters because the model branch is the one that runs when a key is present.

**Do not oversell this beat.** It was a real defect found in review, three weeks before submission,
and the honest framing is stronger than the polished one: *the caveat used to ride on a document
edge, so patients with no document lost it. Seeding the full registry exposed it. It is now keyed
to the patient kind, and there is no branch that can drop it.*

---

## Beat 6 · The number, and what it is not — 3:05–3:35

**On screen:** `/impact`.

**Say:**
> Measured across the corpus: baseline **27.5 minutes**, mean saved **16.3**, about **59%** —
> roughly eleven staff-hours back per forty-patient morning. Recomputed from the intakes by a test
> that fails if the figure in the write-up drifts from the figure in the code.
>
> What it is not: a claim about accuracy on real patients. Every document here is synthetic, and
> the saving is modelled from stage timings, not from a clinic trial.

**Backing:** `test_submission_figures.py`. Say **16.3**, not 16.4 — several older docs carry the
wrong figure; the test pins the checkable one.

---

## Beat 7 · Close ⚡ — 3:35–3:50

**On screen:** the front-desk card again, with the candidate list visible.

**Say:**
> One card, every value traceable to the character it came from, drafts that wait for a human, and
> an identity check the system will not perform. Twenty-seven minutes of sequential admin becomes
> about eight — because the reading is done before the patient reaches the counter, and the
> deciding is still done by the person at it.

---

## The ⚡ 90-second cut

Beats **1 → 2 → 4 → 7**. Problem, card with candidates, the pre-model refusal, close. Drop
provenance and the number if you must — the refusal cannot be dropped, because without it this is
a form-filler.

---

## Rules for this recording

1. **Never demonstrate identity verification**, even as a joke, even as "here's what we *won't*
   do". Someone will screenshot the frame.
2. **Show a refusal as a success.** Do not apologise for it on camera, do not call it "the error
   case", and do not reshoot to avoid one.
3. **If something breaks mid-take, say what broke.** A visible "needs human review" flag is the
   product working. A blank field is the product working. Narrating past either one is the only
   way to lose credibility here.
4. **Do not read a number off the screen that you have not checked** against `TESTING.md` that
   morning. Figures change as the corpus changes; the tests are the source of truth.
5. **Say "synthetic" once, early.** Every patient, insurer and document in the demo is fictional.

---

## Known rough edges — plan around them, do not hide them

| Edge | Effect on the take | What to do |
|---|---|---|
| Real-model latency 3–16s | dead air on `/intelligence` and `/brain` | cut the pause in the edit; keep the answer intact |
| *"how many documents are here?"* occasionally returns `NO_GROUNDED_ANSWER` | one chip may decline | it still renders the figures; if it declines, say so and move on — it is the planner declining to ground a count rather than guess it |
| Gateway state is in memory | a restart loses every intake and confirmed ledger row | do not restart mid-take; re-upload gives the same `intake_id` back (it hashes document id + content) |
| MCP servers need Docker | the aggregator beats fail if the daemon is down | start Docker before recording, or cut Beat 4's MCP sentence |
