# gradCapital — 3-minute application video script

> **For:** Dev Sirbhaiya · interview@gradcapital.in (received 13 Aug)
> **Their prompt:** *What are you working on? What got you to work on this project? Feel free to
> share as much as possible. We want to understand why this project is important.*
>
> **Target: 3:00.** The spoken lines below total **478 words** — that lands at **3:02** at a natural
> 158 wpm, or 3:11 if you speak slowly. Do not speed up to fit more in; if you run long, cut the
> "materialises on demand" line, which is the only sentence the argument survives without.
>
> **Their ask was 2–3 minutes, so 3:02 is at the top of the range, not the middle.** Time your first
> read-through before you commit to a take.

---

## What gradCapital is actually scoring

Their own writing says they weight **personal story, evidence you can figure things out, why you
care, and how big this could get** — over decks and business jargon. Their first calls are 10–15
minutes of fundamentals. They back "rebellious student founders," sector-agnostic, and their
own site lists **$300k full-time / $60k while studying / $1k–5k no-strings experiment grants.**

So the video is not a demo and not a deck read-out. It is: *here is a specific thing I saw that
most people never get to see, here is why it made me build this, here is the proof I can build it.*

**Three rules for the take:**
1. **Face-to-camera, no slides for the first 45 seconds.** They are buying you first.
2. **Never say "AI-powered platform."** Say what it does mechanically.
3. **One hard number per section.** Vague enthusiasm reads as no traction.

---

## THE SCRIPT

### 0:00 – 0:22 · Who I am *(60 words)*

> Hi, I'm Dev Sirbhaiya, Computer Science and Business at NTU Singapore.
>
> For two years I've built AI systems inside real organisations, in industries with nothing in common
> — claims at Singlife, fraud and payments at Hitachi, lending at Bajaj Finance, retrivel and diagnostics at
> Neurowyzr, a robotics lab at NTU.
>
> Five systems, five industries. Every one failed in the same place. And it wasn't the model.

**Delivery:** flat and fast through the list — it's context, not the pitch, and it is doing exactly
one job: proving the pattern you're about to describe isn't one sector's story. Then a **full beat of
silence** before *"And it wasn't the model."* That line is the hook for the whole video.

---

### 0:22 – 1:05 · The problem *(110 words)*

> It was the data. The same problem every time — a hospital, a bank, and a robotics lab, stuck in the
> identical place.
>
> The data an AI needs sits in systems never designed to talk. A twenty-year-old system nobody may
> touch. Shared drives, scanned PDFs, email, Slack. None of it in a shape a model can consume.
>
> So every team does the same thing: build a pipeline, copy it all into a warehouse, and hope.
>
> That copy is stale the moment it's made, it doubles your compliance surface, and nobody can prove
> where an answer came from.
>
> I watched officers refuse a correct answer, because it came without proof.

**Delivery:** this is the emotional centre *and* the load-bearing insight — the same failure in
industries with nothing in common is precisely why the answer has to be infrastructure and not
another vertical tool. Land *"nobody can prove where an answer came from"* hard. The last line is
the one moment where you are a witness rather than a builder — slow right down, and don't name the
client.

---

### 1:05 – 1:52 · What we built *(118 words)*

> So I'm building **Walnut**.
>
> Instead of one giant pipeline, we deploy **one small adapter agent per source.** A Postgres agent. A
> SharePoint agent. A legacy-system agent. Each owns its own connection, its own credentials, its own
> governance policy — and knows how to query its source natively.
>
> They don't copy your data. They build a **temporal knowledge graph of pointers** — what exists,
> where it lives, how it connects to your business. Ask a question, an orchestrator splits it, and the
> right adapters fetch the live rows.
>
> And every value that comes back carries a pointer to where it came from. **A value without one is
> refused at construction.** Not flagged — refused. It cannot be built.

**Delivery:** *"Not flagged — refused. It cannot be built."* is your strongest technical
differentiator, in one breath. It's a type-system guarantee, not a policy — say it like it's obvious.

---

### 1:52 – 2:35 · Why it matters *(111 words)*

> Three things follow from that.
>
> **It runs sandboxed and air-gapped.** The adapters sit inside your network, no data leaves, and the
> model can propose a request but never assign trust, decide a policy, or emit a value.
>
> **It makes legacy migration survivable.** You don't rip the old system out — you point an adapter at
> it and it's queryable on day one, and the people who depended on it keep working in plain language
> instead of queueing for a data team.
>
> **And it materialises on demand** — the same graph produces tabular data for an ML team, chunked
> data for RAG, conversational data for fine-tuning.

**Delivery:** three fingers, literally, with a pause between each. Don't rush the third — the
materialisation point is what makes this infrastructure rather than a governance tool. The
"keep working in plain language" clause is the employee story: say it as relief, not as a feature.

---

### 2:35 – 3:00 · Proof and close *(81 words)*

> It's real. **3,800 tests passing.** We've built a full vertical on it for Parkway Shenton clinics in
> Singapore — it cuts patient admin from 27 minutes to 11, and it still refuses to identify a patient
> without a human attestation, by design.
>
> We're backed by our university's startup fund, and we hold **six letters of interest — across
> healthcare, insurance and manufacturing.** Which is the point: this isn't one industry's problem.
>
> I've seen exactly where this breaks. I'd like to go fix it.

**Delivery:** the LOI line is doing real work — spread across industries, it reads as evidence of a
market rather than of one customer. Name your actual sectors. Then *"I've seen exactly where this
breaks. I'd like to go fix it."* — plainly, and then **stop.** No "thank you for your time," no
smile-and-wave. End on the line.

---

## Before you record — verify these three claims

You will be asked about each of these on the follow-up call. Numbers you can't defend are worse
than no numbers.

| Claim in the script | Where it's from | Do this before recording |
|---|---|---|
| "3,800 tests passing" | `STATUS.md` §2 — 3,813 passed, 0 failed | Re-run `python -m pytest -q -m "not integration"` from `databridge/` and use the real number |
| "27 minutes to 11" | `SYSTEM.md` — baseline 27.5 min, mean saved 16.3 min (59%) | Correct as written. This is computed by `test_submission_figures.py`, not quoted |
| "six letters of interest" | Your figure | **Say the exact number you can produce.** If it's four, say four — a defended four beats a soft six |
| "healthcare, insurance, manufacturing" | Your LOIs | **Replace with your real sectors, in the order of your strongest three.** If they span four or more, say "four different industries" and name three |
| "our university's startup fund" | Your resume says "Backed by BSF" | Say the fund's full name out loud. "Our university's startup fund" sounds evasive on camera |

---

## Practical notes

- **Shoot it in one take if you can.** They asked for a video of *yourself*, not a produced piece.
  Slight roughness reads as authentic; a polished edit reads as a pitch you've rehearsed at other
  funds.
- **Screen recording:** optional, and only under 1:00–1:50. If you use it, show `/evidence` —
  a value, its pointer, and the source span resolving live. Ten seconds, no narration change.
  Do **not** show the graph visualisation; it looks like every other AI demo.
- **The one question they will ask next:** *"Who is paying you for this?"* Have the answer ready
  before you record — it will change how confidently you deliver the last 30 seconds.

---

## If you need a 2:00 cut

Drop the "materialises on demand" paragraph, and shorten the opener to *"five AI systems across
five industries — insurance, healthcare, lending, payments, robotics"* without naming employers.
The argument survives: *the same failure in industries with nothing in common → data is scattered
and unprovable → adapter agents, values refused without proof → sandboxed, legacy-friendly →
here's the proof, and the LOIs span sectors.*

**Keep the cross-industry framing even in the short cut.** It is what separates "a governance tool
for banks" from "infrastructure" — and it's the reason a sector-agnostic fund should care.
