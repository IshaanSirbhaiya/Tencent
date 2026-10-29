"""Seed warm, LABELLED demo history through the REAL Internal Brain (plan B7, decision D-10).

About 300 questions are asked across the demo personas, through the real pipeline (Gate 1, live
Gate 2, the ledger, the content store), so the analytics, audit and history pages have real data.
Every seeded request is recorded as ``channel="replay"`` and ``synthetic_replay=True`` with an
``event_time`` spread over the last 30 days (``recorded_at`` stays the real append time).

What each persona is asked (Opus plan review #7: seeded history must never put a restricted title or
topic into a persona's own history):

* manifest questions that expect an answer (``expect_refusal: false``) and whose every must-cite
  fact the persona is PERMITTED to read (``facts[].permitted``), whoever the question was written for;
* simple questions about the titles of fact documents the persona may read (not injection or
  ACL-edge fixtures);
* the world's own ``audit_replay`` questions for that persona;
* a few topics that exist nowhere in the world (to show honest refusals).

A question is dropped for a persona if it contains the title or key of any fact document DENIED to
that persona, or if the manifest uses the same text as a refusal or existence probe for them.
``--dry-run`` prints the plan without calling anything.

It also performs, as real (not replay-labelled) calls at seed time:
* as ``maya.compliance``: the manifest's audit questions, a few reveals (with a reason) of seeded
  answers, and two Verify-chain runs;
* as ``admin.it``: two simulator revocations (revoke jdoe from the migration channel; restrict the
  settlement-keys page), each followed by one replay-labelled question from the affected person and
  then RESTORED through the mock platform's admin API (members/add, unrestrict), whose signed
  webhooks bring the index back. Audit routes have no replay switch, so these events carry the real
  time and ``synthetic_replay: false``;
* two SILENT revocations (plan v2 B14), the manifest's S4 actions (``s4_remove_member``,
  ``s4_restrict_page``) sent straight to the mock platform's admin API with ``?webhook=0``: the
  source changes but the Internal Brain is not told, so its index still lists the document and only
  the live Gate 2 check can refuse it. The affected person then asks the matching S4 question
  (replay-labelled), so ``retrieval.decided`` holds a real Gate 2 ``forbidden`` decision and the
  analytics "Gate-2 reasons" panel has real denials. The officer's journey view of that request
  confirms the denial (``gate2_denials_confirmed``). Each change is then RESTORED with a normal
  (webhook-sending) admin call. Run the seeder promptly after the stack starts: a poll that lands
  between the silent change and the question would let Gate 1 drop the document first.

Modes
-----
``--mode http`` (default, preferred: the running server's ledger, projection and index stay one)::

    # shell 1: start the stack with the seeding switch on (local only; refused when IB_DEPLOYED=1)
    export IB_REPLAY_KEY="$(python -c 'import secrets; print(secrets.token_hex(16))')"
    bash scripts/run_local_demo.sh                 # IB on :8100, mock platforms on :8200
    # shell 2: same IB_REPLAY_KEY in the environment
    .venv/Scripts/python scripts/seed_demo_activity.py --base http://127.0.0.1:8100

  The server honours ``X-IB-Replay-Key`` only when ``IB_REPLAY_KEY`` (>= 24 chars) is set, it is not
  deployed, the call carries a bearer token and comes from loopback. Sign-in is demo-login
  (``IB_DEMO_LOGIN=1``; ``IB_DEMO_PASSCODE`` is sent when set).

``--mode inprocess`` (the IB server must be STOPPED; the mock platforms on :8200 must run): builds the
services on the same state directory (``--state-dir``, default ``$IB_VAR`` or ``var/ib-demo``) with
the deterministic extractive answerer, asks through ``Pipeline.ask(..., replay=True)``, runs the
audit and simulator calls through the real routes in-process, and re-syncs the index after each
simulator change.

``--reset`` wipes the state directory first (refused while anything answers on ``--base``, and only
for a directory under ``databridge/var`` that holds nothing but Internal Brain state). With
``--mode http`` it then stops: start the server on the clean directory and run again without
``--reset``.

Other flags: ``--count 300``, ``--days 30``, ``--seed 7``, ``--refusal-share 0.12``.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]                   # databridge/
WORLD = ROOT / "fixtures" / "enterprise" / "world.json"
MANIFEST = ROOT / "fixtures" / "enterprise" / "manifest.json"
DEFAULT_STATE = ROOT / "var" / "ib-demo"
IB_STATE_NAMES = {"ledger", "anchors", "content", "index", "external_anchor", "cursors.json",
                  "directory.sqlite", "revoked.json", "mock.log", "audit"}

#: topics that exist nowhere in the Company A world (honest refusals)
NOWHERE = ("What is the parking policy at the Lisbon office?",
           "Who won the office chess tournament this year?",
           "What is on the cafeteria menu next Tuesday?",
           "When does the Mars data centre go live?",
           "What did the board decide about the company sailing team?",
           "Where is the guide to the Antarctica field office?",
           "What is the dress code for the winter gala?",
           "Which band is playing at the summer offsite?")
TITLE_TEMPLATES = ("What does {t} say?", "Summarise {t}.", "What is the latest on {t}?")
SKIP_FACT_PREFIXES = ("INJ-", "ACL-")
AUDIT_REASON = "Seeded demo history: sample review of a replayed answer"


# --------------------------------------------------------------------------------------------
# the plan (pure: no network)
# --------------------------------------------------------------------------------------------
def _titles(world: dict) -> dict[str, str]:
    out = {}
    for p in world["confluence"]["pages"]:
        out[f"confluence:{p['id']}"] = p["title"]
    for i in world["jira"]["issues"]:
        out[f"jira:{i['key']}"] = f"{i['key']}: {i['summary']}"
    for f in world["drive"]["files"]:
        out[f"drive:{f['id']}"] = f["name"]
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def build_plan(world: dict, manifest: dict, *, count: int, days: int, seed: int,
               refusal_share: float, now: datetime) -> list[dict]:
    facts = {f["id"]: f for f in manifest["facts"] if not f.get("applies_after")}
    titles = _titles(world)
    personas = sorted({q["persona"] for q in manifest["questions"]})
    probes: dict[str, set] = defaultdict(set)     # texts used as refusal / existence probes
    for q in manifest["questions"]:
        if q.get("expect_refusal") is not False:
            probes[q["persona"]].add(_norm(q["question"]))

    def restricted_marks(p: str) -> list[str]:
        marks = []
        for f in facts.values():
            if p in f.get("denied", ()):
                t = titles.get(f["doc"])
                if t:
                    marks.append(_norm(t))
                    if t.split(":", 1)[0].isupper() and f["doc"].startswith("jira:"):
                        marks.append(_norm(t.split(":", 1)[0]))
                    else:
                        marks.append(_norm(t.split(" - ")[0]))
                marks.append(_norm(f.get("canary") or ""))
        return [m for m in marks if len(m) >= 5]

    def safe(p: str, text: str) -> bool:
        n = _norm(text)
        return n not in probes[p] and not any(m in n for m in restricted_marks(p))

    pools: dict[str, list[str]] = {}
    for p in personas:
        pool = []
        for q in manifest["questions"]:
            cite = q.get("must_cite_facts") or []
            if (q.get("expect_refusal") is False and not q.get("applies_after") and cite
                    and all(c in facts and p in facts[c].get("permitted", ()) for c in cite)):
                pool.append(q["question"])
        for f in facts.values():
            t = titles.get(f["doc"])
            if (t and p in f.get("permitted", ()) and not f["id"].startswith(SKIP_FACT_PREFIXES)):
                pool += [tpl.format(t=t) for tpl in TITLE_TEMPLATES]
        pool += [r["question"] for r in world.get("audit_replay", []) if r.get("persona") == p]
        pools[p] = [q for q in dict.fromkeys(pool) if safe(p, q)]
    nowhere = [t for t in NOWHERE if not any(_norm(v) in _norm(t) for v in titles.values())]

    rng = random.Random(seed)
    plan = []
    per = max(1, count // len(personas))
    for p in personas:
        pool = pools[p]
        k_ref = max(1, int(round(per * refusal_share)))
        if not pool:                          # nothing this persona can open is askable: refusals
            k_ref = per
        picks = [rng.choice(nowhere) for _ in range(k_ref)]
        picks += [pool[i % len(pool)] for i in rng.sample(range(max(len(pool), per - k_ref)),
                                                          per - k_ref)] if pool else []
        plan += [{"persona": p, "question": q, "expected": "refusal" if q in NOWHERE else "answer"}
                 for q in picks]
    while len(plan) < count:                  # top up with answerable questions
        p = rng.choice([x for x in personas if pools[x]])
        plan.append({"persona": p, "question": rng.choice(pools[p]), "expected": "answer"})
    plan = plan[:count]
    # spread over the last ``days`` days, office hours in Singapore (UTC+8), never in the future
    sgt = timezone(timedelta(hours=8))
    for item in plan:
        day = rng.randrange(days)
        at = (now.astimezone(sgt) - timedelta(days=day)).replace(
            hour=rng.randrange(9, 19), minute=rng.randrange(60), second=rng.randrange(60),
            microsecond=0)
        if at >= now - timedelta(minutes=5):
            at = now - timedelta(minutes=rng.randrange(6, 600))
        item["at"] = at.astimezone(timezone.utc).isoformat()
    plan.sort(key=lambda x: x["at"])
    return plan


# --------------------------------------------------------------------------------------------
# HTTP mode
# --------------------------------------------------------------------------------------------
class HttpBrain:
    def __init__(self, base: str, replay_key: str):
        import httpx
        self.httpx = httpx
        self.base = base.rstrip("/")
        self.key = replay_key
        self.tokens: dict[str, str] = {}

    def token(self, persona: str) -> str:
        if persona not in self.tokens:
            body = {"persona": persona}
            if os.environ.get("IB_DEMO_PASSCODE"):
                body["passcode"] = os.environ["IB_DEMO_PASSCODE"]
            r = self.httpx.post(f"{self.base}/auth/demo-login", json=body, timeout=30)
            r.raise_for_status()
            tok = r.cookies.get("ib_session")
            if not tok:
                raise SystemExit("demo-login returned no session cookie")
            self.tokens[persona] = tok
        return self.tokens[persona]

    def _h(self, persona: str, **extra) -> dict:
        return {"Authorization": f"Bearer {self.token(persona)}", **extra}

    def ask(self, persona: str, question: str, at: str) -> dict:
        r = self.httpx.post(f"{self.base}/ib/ask", json={"question": question}, timeout=120,
                            headers=self._h(persona, **{"X-IB-Replay-Key": self.key,
                                                        "X-IB-Replay-At": at}))
        if r.status_code == 403:
            raise SystemExit("the server refused the replay switch (403): start it with the same "
                             "IB_REPLAY_KEY (>= 24 chars), not deployed, and call it on loopback")
        r.raise_for_status()
        return r.json()

    def get(self, persona: str, path: str) -> dict:
        r = self.httpx.get(f"{self.base}{path}", headers=self._h(persona), timeout=120)
        r.raise_for_status()
        return r.json()

    def post(self, persona: str, path: str, body: dict) -> dict:
        r = self.httpx.post(f"{self.base}{path}", json=body, headers=self._h(persona), timeout=120)
        r.raise_for_status()
        return r.json()

    def after_source_change(self) -> None:
        time.sleep(3)                         # the mock's signed webhook reaches :8100


# --------------------------------------------------------------------------------------------
# in-process mode (server stopped)
# --------------------------------------------------------------------------------------------
class InProcessBrain:
    def __init__(self, state_dir: Path):
        for rel in ("apps/internal_brain", "packages/core", "packages/harness", "packages/adapter",
                    "packages/retrieval"):
            sys.path.insert(0, str(ROOT / rel))
        from fastapi.testclient import TestClient

        from databridge_ib import auth as authmod
        from databridge_ib.app import create_app
        from databridge_ib.audit.routes import make_audit_router
        from databridge_ib.bootstrap import build_services
        from databridge_ib.config import IbConfig
        from databridge_ib.sim import make_sim_router

        cfg = IbConfig(var_root=state_dir, anchor_interval_s=0)
        self.svc = svc = build_services(cfg, chain=None)
        svc.pipeline.chain = None                 # deterministic extractive answers, labelled
        app = create_app(svc, audit_router_factory=lambda cur, rr: make_audit_router(
            svc.ledger, svc.answers, svc.projection, rr, sink=svc.anchor_sink),
            sim_router_factory=lambda cur, rr: make_sim_router(svc.cfg, rr))
        print("syncing the index from the mock platforms ...", flush=True)
        svc.ingestor.sync_all()
        self.authmod, self.TestClient, self.app = authmod, TestClient, app

    def _c(self, persona: str):
        u = self.svc.directory.get(persona)
        tok = self.authmod.mint(self.svc.cfg.token_key, persona, list(u.roles))
        return self.TestClient(self.app, headers={"Authorization": f"Bearer {tok}"})

    def ask(self, persona: str, question: str, at: str) -> dict:
        r = self.svc.pipeline.ask(self.svc.directory.get(persona), question, channel="replay",
                                  event_time=datetime.fromisoformat(at), replay=True)
        return json.loads(r.model_dump_json())

    def get(self, persona: str, path: str) -> dict:
        r = self._c(persona).get(path)
        r.raise_for_status()
        return r.json()

    def post(self, persona: str, path: str, body: dict) -> dict:
        r = self._c(persona).post(path, json=body)
        r.raise_for_status()
        return r.json()

    def after_source_change(self) -> None:
        self.svc.ingestor.sync_all(full=True)     # no webhook reaches a stopped server


# --------------------------------------------------------------------------------------------
def _mock_admin(path: str, body: dict | None = None) -> None:
    import httpx
    base = os.environ.get("IB_MOCK_BASE", "http://127.0.0.1:8200").rstrip("/")
    tok = os.environ.get("MOCK_ADMIN_TOKEN", "admin-dev")
    if body is None:
        body = {"user": "jdoe"} if "members" in path else {}
    r = httpx.post(f"{base}{path}", json=body,
                   headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    r.raise_for_status()


#: manifest S4 action -> (the world question that asks about it, the admin call that restores it)
SILENT = (("s4_remove_member", "S4-q4", "/admin/slack/channels/{channel}/members/add"),
          ("s4_restrict_page", "S4-q2", "/admin/confluence/pages/{page_id}/unrestrict"))


def silent_revocation_plan(world: dict, manifest: dict) -> list[dict]:
    """The silent revocations (B14), derived from the manifest's S4 actions and the world's S4
    questions (nothing typed in here): the revoking admin call (sent with ``?webhook=0``), who asks
    what afterwards, and the admin call that restores the source."""
    questions = {q["id"]: q for q in world.get("questions", [])}
    out = []
    for action, qid, restore in SILENT:
        spec = (manifest.get("s4_actions") or {}).get(action)
        q = questions.get(qid)
        if not spec or not q or q.get("applies_after") != action:
            continue
        method, path = spec["admin_call"].split(" ", 1)
        body = spec["body"] if isinstance(spec["body"], dict) else {"body": spec["body"]}
        restore_body = {"user": spec["user"]} if "user" in spec else {}
        out.append({"action": action, "revoke": f"{path}?webhook=0", "revoke_body": body,
                    "persona": q["persona"], "question": q["question"],
                    "restore": restore.format(**spec), "restore_body": restore_body})
    return out


def silent_revocations(brain, plan: list[dict], *, admin=_mock_admin, now=None,
                       officer: str = "maya.compliance") -> Counter:
    """Run `silent_revocation_plan`: revoke silently, ask as the affected person (replay-labelled),
    confirm the Gate 2 denial in the officer's journey view, restore (with a webhook). A failed
    restore is reported loudly; the next step still runs."""
    stats: Counter = Counter()
    at = ((now or datetime.now(timezone.utc)) - timedelta(minutes=1)).isoformat()
    for step in plan:
        try:
            admin(step["revoke"], step["revoke_body"])
            stats["silent_revocations"] += 1
            r = brain.ask(step["persona"], step["question"], at)
            rid = r.get("request_id") if isinstance(r, dict) else None
            if rid:
                j = brain.get(officer, f"/audit/journey/{rid}")
                if any(g.get("verdict") == "forbidden" for g in j.get("gate2") or []):
                    stats["gate2_denials_confirmed"] += 1
        except SystemExit:
            raise
        except Exception as exc:                                    # noqa: BLE001
            print(f"  silent revocation {step['action']} failed: {type(exc).__name__}")
        finally:
            try:
                admin(step["restore"], step["restore_body"])
                brain.after_source_change()
                stats["silent_restored"] += 1
            except Exception as exc:                                # noqa: BLE001
                print(f"  RESTORE {step['restore']} FAILED ({type(exc).__name__}): restore the mock "
                      "platforms (POST /sim/reset, then restart the Internal Brain)")
    return stats


def _up(base: str) -> bool:
    import httpx
    try:
        httpx.get(f"{base.rstrip('/')}/health", timeout=2)
        return True
    except Exception:                                               # noqa: BLE001
        return False


def reset_state(state: Path, base: str) -> None:
    state = state.resolve()
    var = (ROOT / "var").resolve()
    if var not in state.parents:
        raise SystemExit(f"--reset only wipes a directory under {var}; got {state}")
    if _up(base):
        raise SystemExit(f"something answers on {base}: stop the Internal Brain before --reset")
    if state.exists():
        unexpected = [p.name for p in state.iterdir() if p.name not in IB_STATE_NAMES
                      and not p.name.endswith((".sqlite", ".sqlite-wal", ".sqlite-shm", ".log"))]
        if unexpected:
            raise SystemExit(f"refusing to wipe {state}: unexpected entries {unexpected}")
        shutil.rmtree(state)
    state.mkdir(parents=True)
    print(f"reset: {state} is empty")


def run(brain, plan: list[dict], manifest: dict, silent: list[dict] | None = None) -> dict:
    t0 = time.time()
    stats: Counter = Counter()
    per: dict[str, Counter] = defaultdict(Counter)
    answered_ids: list[str] = []
    for i, item in enumerate(plan, 1):
        try:
            r = brain.ask(item["persona"], item["question"], item["at"])
        except SystemExit:
            raise
        except Exception as exc:                                    # noqa: BLE001
            stats["failed"] += 1
            per[item["persona"]]["failed"] += 1
            print(f"  ask failed ({type(exc).__name__}) for {item['persona']}", flush=True)
            continue
        outcome = "answered" if r.get("citations") else "refused"
        stats[outcome] += 1
        per[item["persona"]][outcome] += 1
        if outcome == "answered":
            answered_ids.append(r["request_id"])
        if i % 25 == 0:
            print(f"  {i}/{len(plan)} asked ({time.time() - t0:.0f}s)", flush=True)
    # compliance activity as maya.compliance (real calls, real time)
    officer = "maya.compliance"
    for q in manifest.get("audit_questions", []):
        try:
            brain.post(officer, "/audit/ask", {"question": q["question"]})
            stats["audit_asks"] += 1
        except Exception as exc:                                    # noqa: BLE001
            print(f"  audit ask failed: {type(exc).__name__}")
    for rid in answered_ids[: 3]:
        try:
            brain.post(officer, "/audit/reveal", {"req_id": rid, "reason": AUDIT_REASON})
            stats["reveals"] += 1
        except Exception as exc:                                    # noqa: BLE001
            print(f"  reveal failed: {type(exc).__name__}")
    for _ in range(2):
        rep = brain.get(officer, "/audit/verify")
        stats["verify_runs"] += 1
        stats["verify_ok"] += int(bool(rep.get("ok")))
    # two simulator revocations, each followed by one question, then restored at the source
    now = datetime.now(timezone.utc)
    for action, who, question, restore in (
            ("/sim/revoke-channel", "jdoe", "What blockers were raised in the db-migration channel?",
             "/admin/slack/channels/C0DBMIG/members/add"),
            ("/sim/restrict-page", "jdoe", "What does the settlement keys rotation page say?",
             "/admin/confluence/pages/cf-1004/unrestrict")):
        try:
            brain.post("admin.it", action, {})
            brain.after_source_change()
            brain.ask(who, question, (now - timedelta(minutes=2)).isoformat())
            stats["revocations"] += 1
        except SystemExit:
            raise
        except Exception as exc:                                    # noqa: BLE001
            print(f"  simulator step {action} failed: {type(exc).__name__}")
        finally:
            try:
                _mock_admin(restore)
                brain.after_source_change()
                stats["restored"] += 1
            except Exception as exc:                                # noqa: BLE001
                print(f"  RESTORE {restore} FAILED ({type(exc).__name__}): restore the mock "
                      "platforms (POST /sim/reset, then restart the Internal Brain)")
    # silent revocations: real Gate 2 denials (B14)
    stats.update(silent_revocations(brain, silent or []))
    return {"stats": dict(stats), "per_persona": {p: dict(c) for p, c in sorted(per.items())},
            "seconds": round(time.time() - t0, 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--mode", choices=("http", "inprocess"), default="http")
    ap.add_argument("--base", default="http://127.0.0.1:8100")
    ap.add_argument("--state-dir", type=Path,
                    default=Path(os.environ.get("IB_VAR") or DEFAULT_STATE))
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--count", type=int, default=300)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--refusal-share", type=float, default=0.12)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    world = json.loads(WORLD.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    plan = build_plan(world, manifest, count=args.count, days=args.days, seed=args.seed,
                      refusal_share=args.refusal_share, now=datetime.now(timezone.utc))
    by = Counter(x["persona"] for x in plan)
    print(f"plan: {len(plan)} questions over {args.days} days; per persona {dict(sorted(by.items()))}; "
          f"expected refusals {sum(x['expected'] == 'refusal' for x in plan)}")
    if args.dry_run:
        for x in plan:
            print(f"{x['at']}  {x['persona']:<16} {x['question']}")
        return 0
    if args.reset:
        reset_state(args.state_dir, args.base)
        if args.mode == "http":
            print("now start the Internal Brain on this state directory "
                  f"(IB_VAR={args.state_dir}) with IB_REPLAY_KEY set, and run again without --reset")
            return 0
    if args.mode == "http":
        key = os.environ.get("IB_REPLAY_KEY", "")
        if len(key) < 24:
            raise SystemExit("set IB_REPLAY_KEY (>= 24 chars), the same value the server runs with")
        if not _up(args.base):
            raise SystemExit(f"nothing answers on {args.base}: start the stack first")
        brain = HttpBrain(args.base, key)
    else:
        if _up(args.base):
            raise SystemExit(f"something answers on {args.base}: stop the Internal Brain first "
                             "(in-process mode writes the same state directory)")
        brain = InProcessBrain(args.state_dir)
    out = run(brain, plan, manifest, silent_revocation_plan(world, manifest))
    print(json.dumps(out, indent=1))
    return 0 if not out["stats"].get("failed") else 1


if __name__ == "__main__":
    sys.exit(main())
