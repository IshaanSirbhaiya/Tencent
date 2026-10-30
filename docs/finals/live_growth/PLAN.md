# Watch the brain grow: build plan

> **Status:** pre-build plan. Test names in this plan are planned; the shipped names are in the STATUS file.

Written 30 Oct 2026 for Demo Day (Tue 3 Nov). Sources: `RESEARCH.md` in this folder, plus the code as it
was at 10:10 on 30 Oct. Budget: about 2 to 3 hours, built and tested. Paths are relative to
`databridge/apps/` unless they start with `docs/`.

**Status when this was written.** A parallel session has already built a large part of the backend:

- `internal_brain/databridge_ib/live.py`: `GET /ib/graph/changes`, `ChangeTracker`, `CursorCodec`;
- `internal_brain/databridge_ib/sim.py`: `POST /sim/add-document`;
- the wiring in `bootstrap.py`;
- the proxy allowlist entries in `internal_brain_web/app/api/ib/[...path]/route.ts`.

There were no tests for these yet and no web UI. This plan therefore (a) adopts what was built,
(b) lists the changes it still needs (§3.4, §4.4) and (c) specifies the rest. Every claim below about
existing code names its file.

---

## 1. What we are building, in one paragraph

A signed-in employee posts a short note into a Slack channel from the graph page. The mock platform
creates the thread and sends its normal signed `content.changed` webhook, and the Internal Brain
ingests it (re-fetch, mask, index) the way it ingests any change. The person's graph, which polls a
permission-filtered change feed, shows the new document pulse into its channel's disc within a few
seconds, with a "Just added" entry beside the graph. The note is answerable with a citation at once.
In Compare, the second person (Ken, a contractor who is not in that channel) sees no change at all:
not a greyed node, not a count, not a feed entry.

## 2. UX spec: what the judges see

### 2.1 Stage flow (recommended: one session, no identity switching)

The add is made **as the signed-in person** (that is how `sim.py` is built: identity only from the
token, and the body names no author). The stage persona is **Priya** (`priya.sre`), who is a member of
`#db-migration` (`C0DBMIG`). The IT admin persona (`admin.it`) is *not* a member of `#db-migration`,
and the Confluence spaces in the world grant `admin.it` no read access, so an "IT admin adds it" flow
could not show the note in the admin's own graph. The fact that `admin.it` cannot see a note in a
channel they are not in is a good line for Q&A.

Page: `/dev/graph?compare=ken.ext`. The main session is Priya on the left; Ken is signed into compare
slot b on the right. Both graphs poll `/ib/graph/changes` under their own cookies.

| t (s) | Screen | Detail |
|---|---|---|
| 0 | Both galaxies on screen; the "Add a note" box (top of the left pane) is pre-filled by the presenter step: channel `#db-migration`, the text in §2.4 | The text is visible; nobody types on stage |
| 0.5 | `Space` posts | Button shows "Posting…", then "Posted to Slack · #db-migration" |
| 1 to 4 | Left: the node fades in at the next spiral slot of the `#db-migration` disc (0.4 s), pulses twice (1.8 s), then keeps an accent colour and a forced label for 10 s. Edges fade in. The camera does **not** move by itself | Feed (left): "Slack · #db-migration · new thread · just now", plus "appeared 2.6 s after you posted" (client-measured, own posts only) |
| 1 to 4 | Right (Ken): nothing moves. The feed still says "Nothing new yet" | This is the same reply he gets when nothing was added (§7 test L1) |
| ~5 | `Space` opens `/compare?mode=people&left=priya.sre&right=ken.ext` with the §2.4 question | Priya: a cited answer whose citation is the new thread. Ken: the constant refusal |
| ~12 | Hold | |

Measured end to end, this is about 15 to 20 s of screen time (§10 has the spoken lines).

**Fallback flow (if Compare plus polling misbehaves at the venue).** Post from `/dev/graph` as Priya
with no Compare. The feed and the pulse still show; Ken's side is shown afterwards on `/compare` only.

### 2.2 Visual spec (all in `GraphCanvas.tsx` reducers; no new renderer)

- **Enter:** the existing `born` fade (`FADE_MS` = 420 ms) for the node and its edges.
- **Pulse:** a new node attribute `fresh: { at: number; kind: 'new' | 'updated' }`.
  - For 0 to 1.8 s, size is multiplied by `1 + 0.5·|sin(π·t/0.9)|` (two beats).
  - For 0 to 10 s, the colour is mixed toward `p.accent` with a linearly decaying weight, starting
    at 0.9.
  - `forceLabel` stays true for 10 s, and `zIndex` is 5.
- **Updated document** (an existing node whose version changed): the same accent decay and no size
  pulse. The feed says "updated".
- **Reduced motion** (`useReducedMotion`): no size pulse and no fade. The node is drawn in solid accent
  with a forced label for 30 s, then returns to normal; a static change is not motion. The camera
  never flies on its own.
- **Selection still wins.** While a node is selected, the ego-dimming rules apply. A fresh node
  outside the ego set is dimmed like any other node, so the selection stays readable.
- **Camera:** freeze the frame before the first merge (`sigma.setCustomBBox(sigma.getBBox())`,
  RESEARCH §2.5), so a node near the edge does not rescale the view. Verify this exists in sigma 3.0.3;
  if it does not, skip it, because §5 keeps new nodes inside the current extent in the common case.
- The raf loop in `GraphCanvas` runs while any fade or pulse is live, not only for `FADE_MS`.

### 2.3 "Just added" feed (new `components/graph/LiveFeed.tsx`)

- **Placement:** a small card at the top-right of each explorer pane, under the toolbar. At most 5
  items, newest first. Items older than 10 minutes drop off. It is collapsed to one line in compact
  (Compare) mode.
- **Item text:** `{Platform} · {container label} · {new thread | new page | new issue | new file | updated} · {age}`.
  - The Slack container label is `#` + `container_name`.
  - The age is client-side ("just now", "12 s ago", "3 min ago"), refreshed every 5 s, and measured
    from when **this browser received** the item. The server sends no per-item times.
  - "new" or "updated" is decided by the client: new if the node was not in its graph before the
    merge.
- **Item action:** "Show" selects the node and flies to it (once, on click).
- **Empty state:** "Nothing new yet. Changes you can open appear here." This is the same sentence for
  everyone; it never says "nothing you can see".
- **Pause:** a "Live updates" switch in the card header, on by default (WCAG 2.2.2). Off stops
  polling. Polling also stops while `document.hidden`, and on 401, 403 or 404 (signed out or
  forbidden). After a 503 it backs off (×2 up to 30 s), and the card shows "Live updates paused:
  temporarily unavailable".
- **Screen readers:** the visible list is not a live region (it prepends; RESEARCH §5.3). One polite
  sentence per non-empty poll goes into the explorer's existing `sr-only aria-live="polite"` node,
  debounced to at most one every 3 s. For example: "1 new document in your graph: Slack,
  #db-migration, new thread."
- **Strings:** add them to `lib/i18n/graph.ts`, in English and 简体中文 (the `L` key flips the language
  on stage).

### 2.4 The note and the question (synthetic, checked unique in `fixtures/enterprise/world.json`)

- Channel: `#db-migration` (`C0DBMIG`). Members: aisha.r, jdoe, nina.k, priya.sre, raj.m, wei.l, yuki.s.
  Ken is not a member.
- Text: `Go/no-go call for Wave 3 (reporting schema) is Thursday 5 Nov, 10:00 SGT in the
  #db-migration huddle. Bring the replication-lag dashboard.`
  "go/no-go" does not occur in the world. "Wave 3 covers the reporting schema" does, so the note fits
  the existing migration plan without contradicting a fact.
- Question: `When is the go/no-go call for Wave 3 of the database migration?`
- Expected: Priya gets a cited answer whose citation is `slack:C0DBMIG:<new ts>`, and Ken gets the
  constant refusal, byte-identical to his refusal for a topic that does not exist.

### 2.5 The Add box (new `components/graph/AddNote.tsx`)

- Shown on `/dev/graph` (left or main pane only, never in compare slot b) when `IB_DEMO_LOGIN` is on.
  The backend enforces this anyway.
- **Channel select:** built only from Slack containers in the person's own `/ib/map` response, so it
  lists only channels they can already read and leaks nothing. Default `#db-migration` if present.
- **Text:** a textarea with a live 2,000-character counter.
- **Confluence:** not offered on stage; see §4.4 item 3.
- **On a 404 or 429:** one neutral message ("Could not post that note." or "Too many notes; try
  again in a minute."). The UI never says why.
- **Presenter handler:** `graph.addNote`, registered with `components/presenter/bus.ts`. It fills the
  fields from the script, and a second call posts.

## 3. API: `GET /ib/graph/changes?since=<cursor>`

### 3.1 Why polling, not SSE (decision)

1. **The Compare view picks the session by the `x-ib-slot` header.** `EventSource` cannot send
   custom headers (RESEARCH §3.1). SSE would need a fetch-stream reader and a new streaming path in
   the proxy.
2. **Threads.** IB routes are synchronous FastAPI handlers. A stream would hold a worker thread per
   open graph (two per Compare view) for the whole demo, and reconnect and heartbeat logic is new
   code to test.
3. **Reuse.** A poll is an ordinary request. It reuses the principal dependency (identity from the
   token), `authz.authorize`, the write-ahead ledger and the proxy allowlist, and its shape can be
   tested for identity exactly like `/ib/graph`.
4. **Latency is enough.** Webhook apply is under 1 s; a 2 s poll (Stage theme) or 3 s poll
   (otherwise) shows a note in about 1 to 4 s. Google Drive's change feed is the same design: a
   polled, opaque token (RESEARCH §3.4).

### 3.2 Contract (as built in `live.py`, with the §3.4 changes)

Request: `GET /ib/graph/changes?since=<opaque, ≤400 chars>`. The only parameter is `since`; it is
optional.

Caller: any employee role (`employee`, `compliance_officer`, `admin_it`); otherwise 403. The proxy
allows it on the main session and on compare slots (already in `SLOT_ALLOWED`).

Response 200, always exactly these three keys:

```json
{"cursor": "<opaque>", "nodes": [ ...GraphNode ], "edges": [ ...GraphEdge ]}
```

**Nodes** use the same shapes as `/ib/graph`:

- a `doc` node for each changed document the caller can open now;
- its `container` and `platform` nodes;
- its author (`person`) node, if the author is known;
- documents it links to or mentions that the caller can open now.

`count`, `degree_hidden_safe` and `more` are always `null` on these nodes (not exact here). The
client keeps the numbers it already has.

**Edges:**

- `contains`: platform → container, and container → doc;
- `authored`: person → doc;
- `links` and `mentions`: only between documents in this reply.

**Semantics:**

- **No cursor, or an invalid one.** A missing, malformed, forged, someone-else's or expired cursor
  (older than `IDLE_S` = 600 s) starts a **new window**: `nodes: []`, `edges: []`, a fresh cursor
  and no back-fill. This is the Kubernetes `410 Gone` rule, but with the same 200 shape.
- **Candidates:** documents first seen in this process, or whose version changed, after the cursor's
  time. They are intersected with the caller's Gate-1 readable set *before any source call*, then
  authorised live with `authz.authorize` (Gate 2 plus the version-bound read-through). Nodes and
  edges are built only from those snapshots.
- **Deletions and revocations** are not sent. The graph shows them on the next full load, as
  `/ib/graph` does today.

**Ledger (write-ahead).** `graph.viewed` holds counts only and is appended before the reply when:

- a new window opens (zero counts), or
- the reply carries at least one node.

An empty poll inside a window is not ledgered, because nothing was returned. A ledger failure gives
503 `{"error": "temporarily unavailable"}`.

Errors: 401 (no or invalid session), 403 (role), 503 (anything else, including the ledger). There is
never a 404 and never a reason.

### 3.3 Rule-4 properties the endpoint must hold (each one is a test in §7)

1. **R1.** A change the caller cannot open is indistinguishable from no change: the same keys, empty
   lists and the same cursor for the same clock.
2. **R2.** No global sequence numbers and no counts of filtered, denied or undecided candidates.
3. **R3.** The cursor's (signed, readable) payload is `{v, s, t}`, where `s` is the caller's own id
   and `t` is the server time **at reply**. Nothing in it depends on a document.
4. **R4.** A document outside the caller's Gate-1 set causes **no source call**, so the reply time
   does not change either.
5. **R5.** The output cap is applied **after** the live check (SECURITY.md §15.1), so a denied
   document never takes a visible slot.

### 3.4 Changes needed in `live.py` (found in review of the in-flight code)

| # | Issue in the current code | Why it matters | Change |
|---|---|---|---|
| L-1 | When there are more candidates than `MAX_DOCS`, the cursor time becomes the stamp of the 25th Gate-1 candidate (`next_t = taken[-1][0]`). When a check times out it becomes `stamp − 1e-6` of that undecided document. The payload is HMAC-signed but **plain base64 JSON**. | A Gate-1 document that fails or times out live (for example, revoked without a webhook) changes a value the caller can decode. That breaks R3. Retrying an undecided document also repeats source calls for something the caller may not be allowed to see. | Always return `t = high-water mark at reply` (L-2). Take up to `MAX_CHECK = 200` candidates **newest first**, authorise them all under one deadline, then cap the **authorised** ones to `MAX_DOCS` (R5). A candidate that was undecided or past the cap is simply not shown: fail closed. It is already in `/ib/map`, so the next full load shows it. |
| L-2 | `now = clock()` is read outside the tracker lock, and `since()` compares `s > t`. If another poll's scan stamps a document with a time equal to this reply's `t` (coarse clock), the document can fall into the gap and never be sent. | It is a gap, not a leak, but on stage it would mean a note that never pulses. | Make the tracker the clock: `ChangeTracker.mark()` returns, under its lock, `hw = max(clock(), last + 1e-6)`. `scan()` stamps with `max(clock(), last_mark + 1e-6)`. `changes()` reads `t_new = tracker.mark()` **before** `since(t_prev)` and returns `t_new`. |
| L-3 | After `/sim/reset`, `Ingestor.reconcile` re-indexes every document. A document edited during the demo (for example, the runbook at version 8) goes back to version 7, and the tracker stamps that as a change. | After a reset, everyone's feed would show "updated" pulses for pages nobody touched. | Add `ChangeTracker.rebaseline()`, which re-stamps everything as baseline (0.0). Call it from `/sim/reset` after `reconcile` (pass the tracker into `make_sim_router`, or let `sim.py` fire a callback that `bootstrap.py` wires up). |
| L-4 | The ledger records `mode="focus"`. | Compliance views would count polls as focus views, which is not what happened. | Add `"changes"` to `GraphViewed.mode` in `models.py`, and record it. `audit/stats.py` counts `graph.viewed` generically, so it needs no change; check `audit/ledger.py`'s event map still validates. |
| L-5 | The live router is included after `create_app`'s route-allowlist check. | The path is under `/ib/`, so it passes. The allowlist tests enumerate mounted routes. | Run `tests/test_app_routes.py::test_only_allowlisted_routes` and `::test_every_non_public_route_requires_auth` against an app that mounts the live router (as `bootstrap.build_app` does). |
| L-6 | `changes()` re-authorises link targets of changed documents (`MAX_LINKED` = 20) under a second deadline. | Two deadlines per call. | Use one `authz.deadline_in(CHECK_TIMEOUT_S)` for both `authorize` calls (SECURITY §15.1, "one deadline"). |

**Cost per poll.**

- With nothing new: one `tracker.scan()`, which is O(1) unless `index.acl_version` moved, then
  O(documents), about 1,100. One cached `index.readable`. No source call.
- With new items: at most 200 live checks, in practice 1 to 3.
- Two viewers polling every 2 s cost nothing measurable.

## 4. Add-a-document API: `POST /sim/add-document` (as built in `sim.py`)

### 4.1 Contract

Body (`extra="forbid"`; **no author field**):

```json
{"platform": "slack" | "confluence", "target": "<channel id | space key>", "title": "<≤120, Confluence only>", "text": "<1..2000>"}
```

The author is the token's subject, mapped to that person's platform account (`platform_ids`).

### 4.2 Who may post (all checked server-side)

1. **Never on a deployment.** If `IB_DEPLOYED=1`, the request is refused for everyone.
2. **`admin_it`** may always use it.
3. **Any other employee** may use it only while `IB_DEMO_LOGIN=1`, and only for Slack.
4. **Gate 1 over the container.** At least one document in the target container must already be in
   the poster's readable set. An unknown container fails the same check.
5. **Live membership (Slack).** The poster must be a current member of the channel, checked at the
   source at post time through the Slack connector (`conversations.info` + `conversations.members`
   with `gate2=True`). Any source error is a refusal.
6. **Rate limit:** 5 posts per person per minute, in memory.

### 4.3 Validation, refusals, masking, ledger

- **Validation:** HTML tags are stripped from `text` and `title`; text is trimmed and must be
  non-empty; at most 2,000 characters (title 120); `target` must match `^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$`.
  Pydantic gives 422 on a bad shape.
- **One refusal shape:**
  - Every refusal is `404 {"detail": "not found"}`. That covers role, deployment, demo sign-in off,
    unknown or unreadable container, not a member, no platform account and source unreachable.
  - Rate limiting is `429 {"detail": "too many requests"}`.
  - Reasons go to the ledger only (`request.failed`, `stage="sim.add-document"`).
- **Masking:** the IB never indexes the posted text directly. The webhook makes `Ingestor.apply_event`
  re-fetch the thread, and `mask_doc` masks Luhn-valid card numbers (first 6 + last 4), CVVs,
  NRIC/FIN (last 3 digits + letter) and mod-97-valid IBANs **before** indexing, embedding, prompts,
  answers and citations. The raw text exists only in the mock platform, which plays the source
  system.
- **Ledger:** a successful post writes nothing to the access ledger (writing to a source is not a
  read; the other simulator actions write nothing either). Every later read is ledgered by its own
  read path.

### 4.4 Changes needed in `sim.py`

1. **Rebaseline on reset.** Call the tracker rebaseline after `reconcile` (L-3).
2. **Reply.** Keep it as is: `{"ok", "doc_id", "platform", "container", "at"}`. The `doc_id` is the
   poster's own note and they are a live member, so it reveals nothing. The client uses `doc_id` to
   measure "appeared N s after you posted" and to label the feed item "your note".
3. **Confluence.** The parallel design limits Confluence to `admin_it` "into a space they can read".
   In this world `admin.it` (groups `grp-it`, `grp-staff`) can read **no** Confluence space, so the
   Confluence path always answers 404. **Do not show Confluence in the stage UI.** Keep the backend
   path and its tests; say "Slack today" if asked.
4. **No `?webhook=0` passthrough.** The stage path relies on the webhook. If the webhook is lost, the
   poller (`freshness/poller.py`, interval `poll_s`) applies the change on its next tick; set
   `poll_s` ≤ 10 s on the demo machine as the backstop.

### 4.5 Reset behaviour

- `/sim/reset` (admin_it) calls the mock's `/admin/reset`, which reloads `world.json`, so the added
  thread no longer exists at the source.
- `Ingestor.reconcile("sim.reset")` then runs a full sync per platform, deleting every indexed
  document not in the source listing, so the note leaves the index.
- The directory epoch is bumped, then (new, L-3) `tracker.rebaseline()` runs.
- **Open graphs** still show the deleted note until reloaded (the delta has no deletions). The
  presenter `R` already navigates away. `SimConsole`'s Reset also posts `BroadcastChannel('ib-live')`
  `{type: 'reset'}`, and an open graph that receives it reloads (`setAttempt(a => a + 1)`).
- **Rehearsal hygiene:** posting the same note in each rehearsal stacks duplicate notes until a
  reset. Add "Reset the world (admin.it → /dev/sim → Reset)" as the last item of each rehearsal in
  `docs/finals/rubric/demo_story/REHEARSAL_CHECKLIST.md`, and as the first item of the Demo Day
  morning check.

## 5. Incremental layout (no global re-layout)

The galaxy layout (`components/graph/layout-core.ts`, `galaxy-v2`) is deterministic, and
`applyLayout` caches it by dataset hash. Re-running it after a merge would move everything: a disc's
radius depends on its document count, and the packing depends on the radii. So a delta merge
**never calls `applyLayout`**. Existing nodes are fixed (the d3 `fx`/`fy` idea, RESEARCH §2.2), and
only new nodes get positions.

New exports in `layout-core.ts` (pure functions, so they unit-test without WebGL):

```ts
export const docSlot = (cx: number, cy: number, k: number): [number, number]   // sunflower slot k
export const discRadius = (count: number): number                              // DOC_STEP*sqrt(count+1)+4
export function placeAround(origin: [number, number], r: number, obstacles: Circle[], startAngle: number): [number, number]
```

Change `computeLayout` to use `docSlot` and `discRadius`, so the formulas cannot drift apart. The
layout output stays identical, and a test asserts that against a stored small fixture.

The new `model.ts → mergeDelta(g, resp, now): { added: string[]; updated: string[] }` handles nodes
in kind order (platform, then container, then person, then doc):

1. **Existing node.** Update its attributes like `mergeResponse` does (label, version, updatedAt,
   deepLink). Do **not** overwrite `count` with `null`. If its version changed, set `fresh = {at:
   now, kind: 'updated'}`.
2. **New document in a known container.**
   - Let `k` be the number of document nodes whose `container` is that container. Those occupied
     slots 0..k−1 when the galaxy was laid out; documents merged later by search or focus only make
     `k` larger, which is safe.
   - Position it at `docSlot(cx, cy, k)`.
   - Why it fits: slot `k` has radius `2.1·√(k+1)+2.2`, which is less than the disc radius
     `2.1·√(k+1)+4`, so it lies **inside the existing disc**. The next few additions stay inside too,
     because `2.1·(√(k+3)−√(k+1)) < 1.8` for every `k ≥ 0`. No other node moves.
3. **New container** (the first document in a channel this person can now read):
   - Obstacles are every existing container's disc, with centre at its position and radius
     `discRadius(docs in it)`.
   - Use `placeAround(platformHub, 9 + discRadius(1) + GAP, obstacles, hashAngle(id))`. This is the
     same outward spiral as `pack()` (angle step 0.21, distance step 0.45), returning the first free
     spot.
   - If there is no platform hub, start from the right edge of the current bounding box.
4. **New person.** Place them at the weighted centre of their new edges' other ends, then push them
   out of any disc and at least 12 units from other people, using the same rules as
   `computeLayout` step 4, moving only the new node.
5. **New document in an unknown container** (it should not happen, because the delta sends the
   container): use `mergeResponse`'s golden-angle ring around the platform hub.
6. **Edges.** Add each one if it is missing, with `born: now`.
   - Person → doc `authored` edges are added as given.
   - Also add or bump the galaxy's person → container summary edge (`weight + 1`), so the person
     reads as connected at overview zoom.
7. **Finish.** Call `resize(g)`. Do not touch the layout cache. A later full reload computes the
   galaxy for the new dataset, and the result may differ slightly; that is accepted and documented.

**Determinism.** The same graph plus the same delta gives the same positions, because there is no
randomness; angles come from `hashAngle(id)`.

## 6. Client wiring

| File | Change |
|---|---|
| `lib/types.ts` | `GraphChanges = { cursor: string; nodes: GraphNode[]; edges: GraphEdge[] }`; `AddNoteBody`, `AddNoteResult` |
| `lib/api.ts` | `graphApi(slot).changes(since?: string)` (with `x-ib-slot`); `api.addNote(body)` → `POST /sim/add-document` (main session only) |
| `lib/mock/extra/graph.ts` | Mock handler for `ib/graph/changes` returning `{cursor:'m', nodes:[], edges:[]}`, so mock and tour builds do not 404. Optionally a scripted delta behind `?mockLive=1` for the tour |
| `components/graph/useLiveChanges.ts` (new) | Polls every 2 s in the Stage theme (3 s otherwise), only while `phase === 'ready'`, visible and not paused. Checks `resp` against `subRef` (the cursor is opaque; the sub check is on the graph load, and a 401 stops polling). Calls `mergeDelta`, feeds `LiveFeed`, announces. Listens for the `ib:identity-changed` and `BroadcastChannel('ib-live')` reset messages |
| `components/graph/GraphExplorer.tsx` | Mounts `useLiveChanges`, `LiveFeed` and (main slot) `AddNote`; freezes the bbox before the first merge; registers the presenter handler `graph.addNote` |
| `components/graph/GraphCanvas.tsx` | `fresh` pulse and decay in `nodeReducer`; the raf loop lasts while any `fresh` or `born` animation is live; `setCustomBBox` |
| `components/presenter/script.ts` | New step `grow` (§10) |
| `lib/i18n/graph.ts` | Feed, pause and add strings in English and 简体中文 |

## 7. Tests

Backend tests go in `internal_brain/tests/test_live_growth.py`. They use the real mock platforms,
real connectors and signed webhooks (`tests/e2e_world.py`, `E2E`), plus an app with the live and sim
routers mounted (pattern: `tests/test_sim_reset_reconcile.py`). Clocks are injected (`clock=`), so
cursors are byte-comparable.

### 7.1 Rule-4 tests (must pass before the stage step is enabled)

| ID | Test | Asserts |
|---|---|---|
| L1 | `test_note_in_channel_ken_is_not_in_never_reaches_ken` | Ken and Priya each open a window. Priya posts the §2.4 note to `C0DBMIG`; the webhook is applied. Ken's poll is `{"cursor", "nodes": [], "edges": []}` with exactly those keys. Priya's poll contains `slack:C0DBMIG:<ts>`, `container:slack:C0DBMIG` and a `contains` edge between them |
| L2 | `test_ken_reply_is_byte_identical_with_and_without_the_note` | Two fresh worlds with the same frozen clock: A, where Priya posted the note, and B, with no post. Ken's poll JSON is **byte-identical**, cursor included |
| L3 | `test_ken_poll_makes_no_source_call` | Count Gate-2 calls on the connectors (wrap `gates.check_all` or the Slack connector's `_gate2`) during Ken's poll after the note: **0**. The same as in world B (no timing channel) |
| L4 | `test_ken_graph_and_map_unchanged_by_the_note` | `/ib/graph` overview and `/ib/map` for Ken, before and after the note, are equal apart from `as_of`. This follows the existing `test_graph_is_identical_with_and_without_restricted_docs` |
| L5 | `test_cursor_payload_depends_only_on_caller_and_clock` | Decode the cursor's base64 payload. Its keys are exactly `{v, s, t}`, `s` is the caller and `t` is the frozen clock. Also true when (a) more than `MAX_DOCS` Gate-1 candidates exist, some of them live-denied, and (b) a candidate's check times out (L-1) |
| L6 | `test_revoked_without_webhook_is_the_same_empty_reply` | Priya is removed from `C0DBMIG` silently (`?webhook=0`) after the note was posted. Her poll returns empty lists, and the cursor is identical to world B's |
| L7 | `test_cursor_of_another_person_or_forged_or_expired_starts_a_new_window` | Priya's cursor presented by Ken, a flipped signature byte, garbage, and a cursor older than `IDLE_S` all give the new-window reply with no back-fill (Ken never receives Priya's note through her cursor) |
| L8 | `test_link_to_a_document_ken_cannot_open_adds_no_node_or_edge` | Leo (`leo.ops`, a member of `#vendor-acme` `C0VACME` with Ken) posts a note there that mentions a Jira key Ken cannot open. Ken's reply contains the note, its container and its author, and **no** node or edge for the key. It is byte-identical (after removing the note's own id and ts) to Ken's reply for the same note without the mention |
| L9 | `test_cap_applies_after_the_live_check` | 30 new readable documents, the newest 10 of them live-denied. The reply holds the newest 25 **authorised** ones, and no denied one takes a slot |

### 7.2 Add-a-document tests

| ID | Asserts |
|---|---|
| A1 | Ken posting to `C0DBMIG` (not a member), Ken posting to `C0NOPE` (unknown) and Ken posting to a channel he is in but with demo sign-in off: all three bodies are byte-identical `404 {"detail":"not found"}`; each is ledgered as `request.failed` with a distinct `error_code` |
| A2 | `IB_DEPLOYED=1`: 404 for `admin_it` too |
| A3 | The body has no author field (`extra="forbid"` → 422 for `user` or `author`). The created message's author on the mock is the token's subject's Slack id |
| A4 | Text over 2,000 characters → 422; `<script>x</script>hi` is posted as `xhi`; an empty-after-strip text → 422 |
| A5 | Rate limit: the sixth post within 60 s → 429, and it is ledgered |
| A6 | **Masking:** a note containing a Luhn-valid card number and a shape-valid NRIC. The index row text, the `/ib/doc` excerpt and the answer text contain the masked forms (`411111••••••1111`, `•••••567D` style per `sensitive.py`) and never the raw values. The ledger has no raw value |
| A7 | **Answerable:** after the note, Priya's `/ib/ask` for the §2.4 question cites the new `doc_id`. Ken's refusal is byte-identical to his refusal for a question about a topic that does not exist |
| A8 | **Reset:** after `/sim/reset`, the note is gone from the mock and from the index. Priya's `/ib/graph` lacks it. The next poll (with a valid cursor) returns no "updated" documents for pages edited before the reset (L-3) |
| A9 | Ledger down: Priya's non-empty poll → 503. The first poll of a window → 503 for everyone |

### 7.3 Web tests

| Kind | Test |
|---|---|
| Unit (`model.ts`, `layout-core.ts`) | `mergeDelta` leaves every pre-existing node's `x,y` **exactly** equal. A new document in a container with `n` documents lies within `discRadius(n)` of the container. The same input gives the same positions. `computeLayout` output is unchanged after the `docSlot`/`discRadius` refactor (stored fixture) |
| Unit | `count` is not overwritten by a `null` from the delta. A known document with a new version is flagged `updated` and not re-placed |
| Playwright (mock backend) | Pausing stops `/ib/graph/changes` requests (count them on the network). A hidden tab stops polling. 401 stops polling. Under reduced motion there is no size change across frames (read `fresh` via the canvas test id) and a static label |
| Playwright (live backend, demo machine) | §2.1 end to end: post, the left feed shows the item within 6 s, and the right (Ken) feed still shows the empty sentence after 10 s |
| a11y | Feed card: the heading is the accessible name; the pause switch is reachable by keyboard; the polite region announces once per non-empty poll |

## 8. Optional: engine refresh (`/dev/brain`)

**Recommendation: do not build this for 3 Nov.**

- It rebuilds the Walnut engine snapshot, about 1 minute of CPU on the demo laptop, while the graph
  and ask are live.
- The snapshot is built for one persona (D-09, D-21), so a refresh must keep that lock-card rule.

If it is built after Demo Day:

- `POST /sim/engine-refresh` (admin_it, `IB_DEPLOYED` refused) starts a single-flight background
  job, with no second run while one is running.
- `GET /sim/engine-refresh` returns `{state: idle|running|failed, started_at, finished_at}` with no
  counts.
- `/dev/brain` shows "Snapshot built 12:03 · Refresh" for admin_it only.
- The job is ledgered as `ingest.sync` with reason `engine.refresh`.

## 9. Risks

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| 1 | The webhook does not reach the IB on the venue laptop (wrong `webhook_url`, port) | Medium | Check in `VENUE_TECH_CHECKLIST.md`: post a test note, then reset. The poller backstop is `poll_s` ≤ 10 s. Clip fallback `B` |
| 2 | Compare loads two overviews (~4 s each) during the beat | High | Open `/dev/graph?compare=ken.ext` during the previous beat's speech, or pre-warm on `P`. The presenter step navigates first, then speaks over the load |
| 3 | The pulse is too subtle on the projector | Medium | Stage theme: accent at full strength, forced label "New · Go/no-go call…", plus the feed card. Rehearse on the projector |
| 4 | Duplicate notes from rehearsals make the answer cite two threads | High without reset | Reset after every rehearsal (§4.5). The presenter `P` warm-up warns if more than one go/no-go note exists in Priya's map |
| 5 | Polling floods the ledger | Low | Empty polls are not ledgered (§3.2) |
| 6 | The incremental position overlaps a label | Low | Slot geometry stays inside the disc (§5). People are pushed apart. The label is forced only for 10 s |
| 7 | `setCustomBBox` is missing in sigma 3.0.3 | Low | Skip it; new documents stay inside the current extent by construction |
| 8 | A cursor leaks hidden state | Closed by L-1 and L-2, with tests L2, L5, L6 | |
| 9 | Timing channel via live checks | Low | Gate 1 removes non-members before any source call (L3). The remaining channel (a Gate-1 document revoked at source without a webhook) is the same one `/ib/graph` has (SECURITY.md §15.1 "Limit") and is documented, not new |
| 10 | Five minutes is full | High | §10: the beat is optional and funded by named cuts. The default is to keep it for Q&A |
| 11 | Mock or tour builds poll a missing endpoint | Medium | Add the mock handler (§6); the client stops polling on 404 |

## 10. Build order (about 2.5 h)

1. **(20 min) Backend fixes in `live.py`, `models.py`, `sim.py`:** L-1, L-2, L-3, L-4, L-6;
   `rebaseline` on reset. Re-run `tests/test_app_routes.py`.
2. **(45 min) `tests/test_live_growth.py`:** L1 to L9 and A1 to A9. Do L1, L2, L3, L5 and A6 first;
   the stage step is blocked until they pass. Run the full IB suite.
3. **(15 min) Types and API:** `lib/types.ts`, `lib/api.ts`, the mock handler.
4. **(30 min) Layout:** `layout-core.ts` helpers, `model.ts → mergeDelta`, and their unit tests.
5. **(20 min) Canvas:** the `GraphCanvas` pulse, decay and reduced-motion branch, the raf loop and
   the bbox freeze.
6. **(25 min) Explorer:** `useLiveChanges`, `LiveFeed` (pause, announce) and `AddNote`, wired into
   `GraphExplorer`; i18n strings.
7. **(10 min) Presenter:** the `grow` step and the `graph.addNote` handler. Add a line to
   `PRESENTER_KEYS.md` §6 marked PROPOSED.
8. **(15 min) On the demo machine:** the end-to-end Playwright run, one projector check, reset.
   Screenshot each step into `Tencent/submission/` (the evidence rule).
9. **(5 min) Docs:**
   - `docs/finals/contracts/backend_v1.md`: a "v3: live growth" section with the §3.2 and §4.1
     contracts;
   - `docs/tencent/SECURITY.md`: a new §15.11 citing the L and A tests;
   - `DECISIONS.md`: "polling, not SSE" and "post as the signed-in person".

## 11. Presenter step and run-sheet line

### 11.1 Presenter step (`components/presenter/script.ts`, PROPOSED)

```ts
{ id: 'grow', title: 'The brain grows', say: 'Ishaan: "Something new happens at work…"',
  url: '/dev/graph?compare=ken.ext', clip: 'clipD_grow.mp4',
  action: async (c) => {
    await c.signInMain('priya.sre');
    await c.go('/dev/graph?compare=ken.ext');
    if ((await c.call('graph.addNote', { channel: 'C0DBMIG', text: NOTE_GROW, post: false }, 15_000)) !== 'ok') fail('graph not ready');
  } },
// the next Space: c.call('graph.addNote', { post: true }); then the Space after that:
// c.go(peopleUrl({ mode: 'people', left: 'priya.sre', right: 'ken.ext', q1: Q_GROW, q2: Q_GROW })) and run
```

Notes on the step:

- The step's id needs adding to `StepId`.
- Record clip D (the §2.1 flow) as the `B` fallback the morning before.

### 11.2 Run-sheet lines (for `docs/finals/rubric/demo_story/RUN_SHEET.md`; team decision)

**Recommended placement:** right after the Ken beat (1:10), because it is the same rule shown on
brand-new data.

**Funding the 20 s:** the existing fallback cuts 1 (简体中文 flip, 3 s), 2 (CodeBuddy line, 5 s) and 4
(WorkBuddy audit-row cut, 8 s), plus 4 s of buffer. **Only if** two timed rehearsals with the beat
both finish by 5:00. Otherwise keep the beat as the Q&A answer to "what happens when new data
arrives?" and do not put it in the 5:00.

| Time | Beat | Who | Exact spoken line | Screen and action |
|---|---|---|---|---|
| 1:10–1:14 | The brain grows | Ishaan | "And when something new happens at work?" Dev: "Space." | `Space`: Priya's graph left, Ken's right (`/dev/graph?compare=ken.ext`); the note is pre-filled in Priya's Add box |
| 1:14–1:22 | | Ishaan | "Priya posts in the database-migration channel." Dev: "Space." (beat) "There it is, in her brain, seconds later." | `Space` posts. Left: the node pulses into #db-migration; the feed reads "Slack · #db-migration · new thread · just now". Point with an open hand |
| 1:22–1:26 | | Ishaan | "Ken's side: nothing. For him it does not exist." | Right: unchanged; the feed shows "Nothing new yet" |
| 1:26–1:30 | | Ishaan | "And it is already answerable, with the citation." Dev: "Space." | `Space`: `/compare`, Priya vs Ken, the go/no-go question; Priya's cited answer, Ken's refusal. Then continue at "Every sentence points at a passage…" (old 1:10 line) |

Truth check for the run sheet's §1 table (tag **[GROW]**): this beat depends on the note appearing
within about 4 s on the venue machine, and on L1, L2, L3 and A7 passing that morning. If either is
false, drop the beat and use clip D only in Q&A.
