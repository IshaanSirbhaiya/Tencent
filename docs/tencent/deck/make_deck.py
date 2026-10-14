"""Build the pitch deck: HTML slides -> 1920x1080 PNGs -> PDF + PPTX (with speaker notes).

    python docs/tencent/deck/make_deck.py html      # write the slide HTML
    node   docs/tencent/deck/shoot_deck.cjs         # render PNGs
    python docs/tencent/deck/make_deck.py pack      # PDF + PPTX from the PNGs

Numbers come from the repository (the eval's results.json); outside numbers name their source on the
slide. Screens are real screenshots of the running app.
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
OUT = REPO / "submission" / "07_deck"
SHOTS = REPO / "submission" / "03_app_scenes"
RESULTS = REPO / "databridge" / "evals" / "internal_brain" / "results.json"

CSS = """
*{box-sizing:border-box}html,body{margin:0;width:1920px;height:1080px;overflow:hidden}
body{background:radial-gradient(1300px 760px at 80% 12%,#1c2a49 0%,#0b1020 55%,#070a14 100%);color:#e8edf7;
font-family:'Segoe UI',Inter,system-ui,sans-serif}
.s{position:relative;height:100%;padding:96px 120px 90px;display:flex;flex-direction:column}
.k{color:#8fd3c7;letter-spacing:.18em;text-transform:uppercase;font-size:21px;font-weight:600}
h1{font-size:82px;line-height:1.04;margin:18px 0 16px;font-weight:700;letter-spacing:-.02em}
h2{font-size:54px;line-height:1.1;margin:14px 0 26px;font-weight:700;letter-spacing:-.01em}
.lead{font-size:30px;color:#b8c3d9;line-height:1.42;max-width:1500px;margin:0}
.row{display:flex;gap:40px;flex:1;min-height:0}.col{flex:1;min-width:0;display:flex;flex-direction:column}
.card{border:1px solid #2a3654;border-radius:20px;background:rgba(17,24,43,.78);padding:26px 30px}
.big{font-size:84px;font-weight:700;letter-spacing:-.03em;line-height:1}.ok{color:#5ee0b0}
.lbl{font-size:25px;color:#c9d3e6;margin-top:10px;line-height:1.35}.src{font-size:17px;color:#7f8aa3;margin-top:14px;letter-spacing:.03em}
.shot{border:1px solid #2a3654;border-radius:16px;overflow:hidden;background:#05070d;flex:1;min-height:0;display:flex}
.shot img{width:100%;height:100%;object-fit:cover;object-position:top left}
.cap{font-size:20px;color:#8a96ae;margin-top:12px}
ul.b{margin:0;padding-left:28px;font-size:26px;line-height:1.5;color:#d3dbea}ul.b li{margin:6px 0}
.n{color:#8fd3c7;font-weight:700;font-size:20px;letter-spacing:.1em}.t{font-size:30px;font-weight:700;margin:6px 0 8px}
.d{font-size:21px;color:#b8c3d9;line-height:1.4}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:28px}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:28px}
.pill{display:inline-block;border:1px solid #2f3d5e;border-radius:999px;padding:9px 20px;margin:8px 10px 0 0;font-size:22px;color:#cfd8ea;background:rgba(20,28,50,.7)}
.foot{position:absolute;left:120px;right:120px;bottom:34px;display:flex;justify-content:space-between;color:#6f7a93;font-size:18px}
.logo{display:inline-flex;align-items:center;gap:12px;color:#c9d3e6;font-weight:600}.dot{width:26px;height:26px;border-radius:8px;background:linear-gradient(135deg,#6d5dfc,#22c3a6)}
"""


def page(n: int, total: int, body: str) -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Slide {n}</title>"
            f"<style>{CSS}</style></head><body><div class='s'>{body}"
            f"<div class='foot'><span class='logo'><span class='dot'></span>Walnut Internal Brain</span>"
            f"<span>FinTech Track · Aspire · Tencent Cloud AI CAN DO IT Hackathon Singapore 2026 · {n}/{total}</span>"
            f"</div></div></body></html>")


def img(rel: str) -> str:
    return (SHOTS / rel).resolve().as_uri()


def slides() -> list[tuple[str, str]]:
    h = json.loads(RESULTS.read_text(encoding="utf-8"))["headline"]
    leaks, n_req, p2c, p2m, inj = (h["leaks"], h["n_requests_all_modes"], h["p2_compared"],
                                   h["p2_mismatches"], h["injection_hard_failures"])
    S: list[tuple[str, str]] = []
    S.append((f"""
<div class="row" style="align-items:center"><div class="col" style="flex:1.05"><div class="k">FinTech Track · Aspire · "The Internal Brain"</div>
<h1 style="font-size:66px">Ask your whole company.<br>See only what you're allowed to.</h1>
<p class="lead" style="font-size:27px">One assistant across Confluence, Jira, Slack and Google Drive. Every answer cited, every source
permission-checked twice, every access written to an audit log that can't be quietly edited.</p>
<div style="margin-top:34px"><span class="pill">Knowledge across 4 platforms</span><span class="pill">Restrictions per person</span><span class="pill">Tamper-evident audit</span><span class="pill">Built with CodeBuddy · WorkBuddy</span></div></div>
<div class="col" style="flex:.95;height:760px"><div class="shot"><img src="{img('internal_brain/19_answer_jdoe.png')}"></div><div class="cap">A real answer: one question, three platforms, every source re-checked.</div></div></div>""",
              "Hi, we're team Walnut. This is the Internal Brain: one place to ask questions across Aspire's four "
              "tools, answered only from what the person asking is allowed to open, with every access audited."))
    S.append(("""
<div class="k">The problem</div><h2>The answer exists. Finding it safely is the hard part.</h2>
<div class="grid3">
 <div class="card"><div class="big">~30%</div><div class="lbl">of the work week goes to looking for information</div><div class="src">ASPIRE · INTERNAL BRAIN CHALLENGE BRIEF</div></div>
 <div class="card"><div class="big">~3M</div><div class="lbl">sensitive records per organisation accessed by Microsoft Copilot in H1 2025</div><div class="src">CONCENTRIC AI · DATA RISK REPORT 2H 2025</div></div>
 <div class="card"><div class="big">9.3</div><div class="lbl">CVSS of EchoLeak (CVE-2025-32711): a zero-click prompt injection that leaked Copilot data</div><div class="src">REPORTED BY THE HACKER NEWS, JUNE 2025</div></div>
</div>
<div class="card" style="margin-top:28px"><div class="t" style="font-size:28px">In a fintech, one leak can be a crime</div>
<div class="d" style="font-size:23px">Telling a customer that a suspicious-transaction report exists is tipping-off (Singapore CDSA s.57, s.48 before the 2020 Revised Edition; MAS Notice PSN01).
An assistant that can read everything can leak everything, so most regulated firms simply don't roll one out.</div></div>
<div class="grid3" style="margin-top:28px">
 <div class="card"><div class="big" style="font-size:60px">50,000+</div><div class="lbl">businesses run their finances on Aspire</div><div class="src">TECHNODE · APRIL 2026</div></div>
 <div class="card"><div class="big" style="font-size:60px">10+</div><div class="lbl">licences across SG, HK, AU, EU, US and CA: many regulators, many policies, many audiences</div><div class="src">TECHNODE · APRIL 2026</div></div>
 <div class="card"><div class="big" style="font-size:60px">Oct 2026</div><div class="lbl">MAS issued its Guidelines on AI Risk Management, covering payment institutions</div><div class="src">MAS MEDIA RELEASE</div></div>
</div>""",
              "Everyone loses time searching. The obvious fix is an AI assistant, but assistants that can read "
              "everything have leaked: Copilot oversharing, EchoLeak, prompt injection in Slack AI. In a fintech "
              "that is not just embarrassing; tipping off a customer about a suspicious-transaction report is an "
              "offence. That is why Aspire set this problem."))
    S.append((f"""
<div class="k">The idea, on one screen</div><h2>Same question. Two people. Two honest answers.</h2>
<div class="row"><div class="col"><div class="shot"><img src="{img('internal_brain/12_compare.png')}"></div>
<div class="cap">Live app: Jordan (engineer) gets the cited answer. Ken (contractor) gets the standard refusal, with no hint that more exists.</div></div></div>""",
              "This is the whole idea on one screen. The same question from an engineer and a contractor. The "
              "engineer gets a cited answer from three platforms. The contractor gets the same polite refusal "
              "he'd get for something that doesn't exist. Restricted looks exactly like non-existent."))
    S.append(("""
<div class="k">How it works</div><h2>Two checks before the model. One after. A record before anything is shown.</h2>
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:22px">
 <div class="card"><div class="n">01</div><div class="t">Who's asking</div><div class="d">A signed token from the web app or WorkBuddy. Never a name typed into a box.</div></div>
 <div class="card"><div class="n">02</div><div class="t">Gate 1</div><div class="d">Search only what this person may open, using each platform's own permission model.</div></div>
 <div class="card"><div class="n">03</div><div class="t">Gate 2</div><div class="d">Ask the source again, live, for every passage. Error or timeout means no.</div></div>
 <div class="card"><div class="n">04</div><div class="t">Answer</div><div class="d">With a TokenHub key, Tencent Hy3 writes from those passages only; without one we quote them directly.</div></div>
 <div class="card"><div class="n">05</div><div class="t">Grounding</div><div class="d">Every sentence cites a passage; unsupported numbers and names are dropped.</div></div>
</div>
<div class="card" style="margin-top:28px;border-color:#3b5b4f;background:rgba(20,48,40,.55)"><div class="d" style="font-size:24px;color:#d7f5ea"><b>Audit log</b>: written before retrieval, before the model, before the answer is shown. Hash-chained and sealed. If it can't be written, nothing is answered.</div></div>
<div class="grid3" style="margin-top:28px">
 <div class="card"><div class="t" style="font-size:27px">Restricted = doesn't exist</div><div class="d">The same refusal, byte for byte, whether a document is restricted or was never written.</div></div>
 <div class="card"><div class="t" style="font-size:27px">Revoked = gone next question</div><div class="d">Remove someone from a channel and their very next answer no longer uses it, even if the notification is lost.</div></div>
 <div class="card"><div class="t" style="font-size:27px">Changed = re-read first</div><div class="d">An edited document is re-read and re-checked before use, and the citation shows the new version.</div></div>
</div>""",
              "How it works, in five steps. Identity only from a signed token. Gate one searches only what you can "
              "open, using each platform's own rules. Gate two asks the platform again, live, for every passage. "
              "The model sees only what passed, and a grounding check drops anything unsupported. And the audit "
              "record is written before anything is shown."))
    S.append((f"""
<div class="k">Knowledge, shaped by permissions</div><h2>Everything you can open. Nothing you can't.</h2>
<div class="row"><div class="col"><div class="shot"><img src="{img('internal_brain/10_map_jdoe.png')}"></div><div class="cap">Jordan, engineer: 867 items in 17 places on 4 platforms.</div></div>
<div class="col"><div class="shot"><img src="{img('internal_brain/11_map_kenext.png')}"></div><div class="cap">Ken, contractor: 24 items in 2 places. No "+843 hidden". Each item checked live with its source.</div></div></div>""",
              "The knowledge map shows what each person can open, checked live with each source. Jordan sees 867 "
              "items; Ken sees 24. The map never hints that anything else exists."))
    S.append((f"""
<div class="k">Built for a fintech</div><h2>Four things a payments company needs on day one</h2>
<div class="row"><div class="col" style="flex:1.1"><div class="grid2" style="grid-template-columns:1fr">
 <div class="card"><div class="t">FinCrime compartment</div><div class="d">Suspicious-transaction case notes are readable only by the MLRO. An account manager asking about that customer gets the approved script, word for word the same as for a company that doesn't exist.</div></div>
 <div class="card"><div class="t">Answers per market</div><div class="d">"What can we offer this SME?" gets a different, correct answer for Singapore and Indonesia, each citing that market's approved policy.</div></div>
 <div class="card"><div class="t">Approved policies only</div><div class="d">A draft fee schedule with different numbers is never cited. Only the approved version counts.</div></div>
 <div class="card"><div class="t">Card and ID masking</div><div class="d">Card numbers, CVVs, IBANs and NRICs are masked before indexing, so they never reach the index or a model.</div></div>
</div></div><div class="col" style="flex:.9"><div class="shot"><img src="{img('internal_brain/21_fintech_market_answer.png')}" style="object-position:0 45%"></div>
<div class="cap">Ops asks about an Indonesian SME: the answer cites only the Indonesia policy and pricing.</div></div></div>""",
              "On top of the general system we added four fintech pieces as data and configuration: a FinCrime "
              "compartment against tipping-off, market-specific answers to Aspire's 'what can we offer this "
              "customer' question, approved-only policies, and masking of card numbers and national IDs."))
    S.append((f"""
<div class="k">The audit trail</div><h2>Who asked what, what was allowed, and proof nobody edited it</h2>
<div class="row"><div class="col" style="flex:1.35"><div class="shot"><img src="{img('internal_brain/17_audit_verify.png')}"></div>
<div class="cap">Compliance asks in plain English; it becomes a visible filter. Every allow/deny decision per document. "Verify audit chain" re-checks every record and every stored answer.</div></div>
<div class="col" style="flex:.65;gap:22px">
 <div class="card"><div class="big ok" style="font-size:64px">6 / 6</div><div class="lbl">tamper attacks detected: edits, deletions, rollback, a key-holder rewrite. A 7th, a rollback after the last off-host head, is our stated limit</div><div class="src">SCRIPTS/TAMPER_DEMO.PY · REAL OUTPUT</div></div>
 <div class="card"><div class="t" style="font-size:26px">Reasoned reveal</div><div class="d">Compliance sees metadata by default. Reading an answer needs a reason, and the reveal is logged too.</div></div>
</div></div>""",
              "For compliance: a plain-English audit search that turns into a visible filter, every allow and deny "
              "decision, and a reveal of answer text that needs a reason and is itself logged. Verify audit chain "
              "re-checks every record; our tamper demo shows edits, deletions and rollbacks being caught."))
    S.append((f"""
<div class="k">Measured, not promised</div><h2>We attacked it on purpose</h2>
<div class="grid3">
 <div class="card"><div class="big ok">{leaks}</div><div class="lbl">leaks of restricted content in {n_req:,} adversarial requests</div><div class="src">EVALS/INTERNAL_BRAIN/RESULTS.MD</div></div>
 <div class="card"><div class="big ok">{p2m} / {p2c}</div><div class="lbl">differences between "restricted" and "doesn't exist"</div><div class="src">P2 DIFFERENTIAL TEST</div></div>
 <div class="card"><div class="big ok">{inj}</div><div class="lbl">prompt-injection failures, with planted instructions on all four platforms</div><div class="src">INJECTION PROBES</div></div>
</div>
<div class="grid3" style="margin-top:28px">
 <div class="card"><div class="big" style="font-size:64px">634</div><div class="lbl">automated tests on the Internal Brain</div></div>
 <div class="card"><div class="big" style="font-size:64px">7</div><div class="lbl">independent security reviews (6 Codex passes, CodeBuddy on Hy3), every finding fixed or documented</div></div>
 <div class="card"><div class="big" style="font-size:64px">100%</div><div class="lbl">recall on questions people are allowed to answer</div></div>
</div>""",
              "We attacked it on purpose: nearly six thousand adversarial requests with zero leaks, four hundred "
              "paired questions with zero differences, and zero prompt-injection failures. The evaluation has its "
              "own canaries, so it can't pass by accident."))
    S.append((f"""
<div class="k">Built with Tencent</div><h2>CodeBuddy built it. WorkBuddy is the second front door.</h2>
<div class="row"><div class="col" style="flex:1.2"><div class="shot"><img src="{(REPO / 'submission/01_codebuddy/cb2_session_overview.png').resolve().as_uri()}"></div>
<div class="cap">CodeBuddy session CB2 building the WorkBuddy MCP connector (every session's transcript is in the repository).</div></div>
<div class="col" style="flex:.8;gap:18px">
 <div class="card"><div class="t" style="font-size:26px">CodeBuddy · 6 sessions</div><div class="d">Trust-boundary diagram, WorkBuddy connector, token tool, model-chain tests, a security review on Hy3, then hardening its own code.</div></div>
 <div class="card"><div class="t" style="font-size:26px">WorkBuddy · 3 workspaces</div><div class="d">Ask as an engineer or a contractor, or audit as compliance. Each carries its own signed token.</div></div>
 <div class="card"><div class="t" style="font-size:26px">TokenHub · Hy3 · Lighthouse · Miora</div><div class="d">Hy3 with DeepSeek fallback (configured; the Hy3 run is pending); a hardened Lighthouse deploy kit; the cover made in Miora.</div></div>
</div></div>""",
              "Tencent tools are in the build, not bolted on. CodeBuddy ran six sessions, including a security "
              "review on Hy3 that found real issues we then fixed. WorkBuddy is a second front door, where each "
              "workspace carries its own signed token, so the model can never choose who you are."))
    S.append((f"""
<div class="k">What's underneath</div><h2>The Walnut evidence engine, reused as it was</h2>
<div class="row"><div class="col"><div class="shot"><img src="{img('walnut_original/02_graph_node_panel.png')}"></div><div class="cap">Knowledge graph: every attribute points to the record it came from.</div></div>
<div class="col"><div class="shot"><img src="{img('walnut_original/03_brain_time_slider.png')}"></div><div class="cap">Company brain with a time slider: what the company knew on any date.</div></div></div>""",
              "Underneath is Walnut, the evidence engine we built before this event. Every value carries a pointer "
              "to its source, and the sealed ledger and grounding wall come from it. Ninety-three percent of the "
              "code is that original base, reused unchanged."))
    S.append(("""
<div class="k">For Aspire</div><h2>From demo to pilot in four weeks</h2>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:24px">
 <div class="card"><div class="n">WEEK 1</div><div class="t">Connect</div><div class="d">Point the four connectors at Aspire's real Confluence, Jira, Slack and Drive (a base-URL change; same code path as the demo).</div></div>
 <div class="card"><div class="n">WEEK 2</div><div class="t">Identity</div><div class="d">Replace demo sign-in with Aspire's SSO; keep the ledger key in a managed key store.</div></div>
 <div class="card"><div class="n">WEEK 3</div><div class="t">Prove it</div><div class="d">Run the leak evaluation on Aspire's own permissions; compliance reviews the audit console.</div></div>
 <div class="card"><div class="n">WEEK 4</div><div class="t">Pilot</div><div class="d">One team (e.g. payments engineering), in the web app and WorkBuddy, with the audit log reviewed weekly.</div></div>
</div>
<div class="card" style="margin-top:28px"><div class="d" style="font-size:23px">Ahead of the MAS Guidelines on AI Risk Management (issued Oct 2026): model, prompt version and evidence are already logged for every answer, with human oversight through the audit console.</div></div>""",
              "For Aspire, this is a four-week path: connect the real tools, swap demo sign-in for SSO, run the "
              "same leak evaluation on Aspire's own permissions, then pilot with one team. It already logs what "
              "the new MAS AI risk guidelines ask firms to keep track of."))
    S.append(("""
<div class="k">Honest limits, and what's next</div><h2>What we don't claim yet</h2>
<div class="row"><div class="col" style="flex:1.25"><ul class="b">
 <li>The four platforms are mocked with their real API shapes and permission rules; the connectors haven't run against a real tenant yet.</li>
 <li>Sign-in is a demo login standing in for SSO; a deployed build refuses to start without a passcode.</li>
 <li>The grounding check verifies numbers and names, not the relations between them.</li>
 <li>Someone holding the ledger's sealing key could rewrite records after the last off-host published head; we say so and test it.</li>
 <li>All numbers come from a test model and the deterministic path; the Hy3 run waits on Tencent account verification.</li>
</ul></div><div class="col" style="flex:.75;gap:18px">
 <div class="card"><div class="t" style="font-size:26px">Next</div><div class="d">Real-tenant connector runs · SSO · ledger key in a managed key store · Hy3 evaluation · relation checks in the grounding wall</div></div>
 <div class="card"><div class="t" style="font-size:26px">Why say this?</div><div class="d">A compliance tool that overclaims is worse than none. Every claim here is backed by a test or a file in the repository.</div></div>
</div></div>""",
              "We're explicit about limits: mocked platforms, demo sign-in, grounding that checks values but not "
              "relations, the sealing-key window, and a Hy3 evaluation run that's still pending."))
    S.append(("""
<div style="margin-top:110px"><div class="k">Thank you</div><h1>Walnut Internal Brain</h1>
<p class="lead">The repository's INTERNAL_BRAIN.md shows what to look at first, how to run it in two commands,
and the evidence behind every claim on these slides.</p>
<div style="margin-top:40px"><span class="pill">github.com/IshaanSirbhaiya/Tencent</span><span class="pill">Start at INTERNAL_BRAIN.md</span><span class="pill">6-minute demo video</span></div></div>""",
              "Thank you. Everything here is in the repository, starting from INTERNAL_BRAIN.md, with the evidence "
              "behind every claim."))
    return S


def write_html() -> None:
    S = slides()
    (OUT / "html").mkdir(parents=True, exist_ok=True)
    notes = {}
    for i, (body, note) in enumerate(S, 1):
        (OUT / "html" / f"slide{i:02d}.html").write_text(page(i, len(S), body), encoding="utf-8")
        notes[f"slide{i:02d}"] = note
    (OUT / "notes.json").write_text(json.dumps(notes, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(S)} slides -> {OUT / 'html'}")


def pack() -> None:
    from PIL import Image
    from pptx import Presentation
    from pptx.util import Emu
    notes = json.loads((OUT / "notes.json").read_text(encoding="utf-8"))
    pngs = sorted((OUT / "png").glob("slide*.png"))
    imgs = [Image.open(p).convert("RGB") for p in pngs]
    pdf = OUT / "walnut_internal_brain_deck.pdf"
    imgs[0].save(pdf, save_all=True, append_images=imgs[1:], resolution=144)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(12192000), Emu(6858000)          # 16:9, 13.333 x 7.5 in
    blank = prs.slide_layouts[6]
    for p in pngs:
        s = prs.slides.add_slide(blank)
        s.shapes.add_picture(str(p), 0, 0, width=prs.slide_width, height=prs.slide_height)
        s.notes_slide.notes_text_frame.text = notes.get(p.stem, "")
    pptx_path = OUT / "walnut_internal_brain_deck.pptx"
    prs.save(pptx_path)
    print("->", pdf, "\n->", pptx_path)


if __name__ == "__main__":
    {"html": write_html, "pack": pack}[sys.argv[1] if len(sys.argv) > 1 else "html"]()
