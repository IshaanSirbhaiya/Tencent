"""Write the video's still cards (title, why, architecture, numbers, closing) as 1920x1080 HTML.

    python docs/tencent/video/make_cards.py        # then: node docs/tencent/video/shoot_cards.cjs

Every number on a card is read from the repository (the eval's results.json), never typed in.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
OUT = REPO / "submission" / "05_video" / "cards"
RESULTS = REPO / "databridge" / "evals" / "internal_brain" / "results.json"
MMD = REPO / "docs" / "tencent" / "diagrams" / "trust_boundaries.mmd"

BASE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title><style>
*{{box-sizing:border-box}}html,body{{margin:0;width:1920px;height:1080px;overflow:hidden}}
body{{background:radial-gradient(1200px 700px at 78% 18%,#1d2b4a 0%,#0b1020 55%,#070a14 100%);color:#e8edf7;
font-family:'Segoe UI',Inter,system-ui,sans-serif}}
.pad{{padding:110px 140px;height:100%;display:flex;flex-direction:column}}
.kicker{{color:#8fd3c7;letter-spacing:.18em;text-transform:uppercase;font-size:22px;font-weight:600}}
h1{{font-size:84px;line-height:1.05;margin:22px 0 18px;font-weight:700;letter-spacing:-.02em}}
h2{{font-size:56px;margin:18px 0;font-weight:700;letter-spacing:-.01em}}
p.lead{{font-size:32px;color:#b8c3d9;max-width:1400px;line-height:1.4;margin:0}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:36px;margin-top:70px}}
.stat{{border:1px solid #2a3654;border-radius:22px;padding:34px 36px;background:rgba(17,24,43,.75)}}
.big{{font-size:92px;font-weight:700;letter-spacing:-.03em;color:#fff}}
.big.ok{{color:#5ee0b0}}.lbl{{font-size:27px;color:#c9d3e6;margin-top:8px;line-height:1.35}}
.src{{font-size:19px;color:#7f8aa3;margin-top:18px;letter-spacing:.04em}}
.foot{{margin-top:auto;display:flex;justify-content:space-between;color:#7f8aa3;font-size:21px}}
.logo{{display:inline-flex;align-items:center;gap:14px;font-weight:700;color:#e8edf7;font-size:24px}}
.dot{{width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,#6d5dfc,#22c3a6)}}
.pill{{display:inline-block;border:1px solid #2f3d5e;border-radius:999px;padding:10px 22px;margin:8px 10px 0 0;
font-size:24px;color:#cfd8ea;background:rgba(20,28,50,.7)}}
</style>{head}</head><body><div class="pad">{body}
<div class="foot"><span class="logo"><span class="dot"></span>Walnut Internal Brain</span>
<span>FinTech Track · Aspire · Tencent Cloud AI CAN DO IT Hackathon Singapore 2026</span></div></div></body></html>"""


def card(name: str, title: str, body: str, head: str = "") -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.html").write_text(BASE.format(title=html.escape(title), body=body, head=head),
                                      encoding="utf-8")


def main() -> int:
    h = json.loads(RESULTS.read_text(encoding="utf-8"))["headline"]
    card("01_title", "Title", """
<div class="kicker">FinTech Track · Aspire · "The Internal Brain"</div>
<h1>Ask your whole company.<br>See only what you're allowed to.</h1>
<p class="lead">One assistant across Confluence, Jira, Slack and Google Drive. Every answer cited,
every source permission-checked, every access written to an audit trail that can't be quietly edited.</p>
<div style="margin-top:46px"><span class="pill">Knowledge across 4 platforms</span><span class="pill">Restrictions per person</span><span class="pill">Tamper-evident audit</span></div>""")
    card("02_why", "Why this matters", """
<div class="kicker">Why this matters</div>
<h2>The answer exists. Finding it, safely, is the problem.</h2>
<div class="grid">
 <div class="stat"><div class="big">~30%</div><div class="lbl">of the work week goes to looking for information</div>
  <div class="src">ASPIRE · INTERNAL BRAIN CHALLENGE BRIEF</div></div>
 <div class="stat"><div class="big">1 leak</div><div class="lbl">is enough: an assistant that can read everything can leak everything</div>
  <div class="src">COPILOT OVERSHARING · ECHOLEAK · SLACK AI INJECTION</div></div>
 <div class="stat"><div class="big">Offence</div><div class="lbl">to tip off a customer that a suspicious-transaction report exists</div>
  <div class="src">CDSA S.57 · MAS NOTICE PSN01 (SINGAPORE)</div></div>
</div>""")
    flow = [("01", "Who's asking", "Signed token from the web app or WorkBuddy. Never a name typed into a box."),
            ("02", "Gate 1", "Search only what this person may open, using each platform's own permissions."),
            ("03", "Gate 2", "Ask the source again, live, for every passage. Error or timeout means no."),
            ("04", "Answer", "Tencent Hunyuan writes it from those passages only, or we quote them directly."),
            ("05", "Grounding check", "Every sentence must cite a passage. Unsupported numbers and names are dropped.")]
    steps = "".join(f'<div class="st"><div class="n">{n}</div><div class="t">{html.escape(t)}</div>'
                    f'<div class="d">{html.escape(d)}</div></div>' for n, t, d in flow)
    card("12_architecture", "How it fits together", f"""
<div class="kicker">How it fits together</div>
<h2 style="margin-bottom:40px">Two checks before the model. One after. A record before anything is shown.</h2>
<div class="flow">{steps}</div>
<div class="ledger"><b>Audit ledger</b> · written <i>before</i> retrieval, <i>before</i> the model, <i>before</i> the
answer is shown · hash-chained and sealed · if it can't be written, nothing is answered</div>
<div class="srcs">Sources: Confluence · Jira · Slack · Google Drive, kept fresh by signed webhooks, re-read when a document changed</div>""",
         head="""<style>.flow{display:grid;grid-template-columns:repeat(5,1fr);gap:22px}
.st{position:relative;border:1px solid #2a3654;border-radius:20px;padding:26px 24px;background:rgba(17,24,43,.8);min-height:280px}
.st:not(:last-child)::after{content:"→";position:absolute;right:-21px;top:44%;color:#8fd3c7;font-size:30px}
.n{color:#8fd3c7;font-weight:700;font-size:22px;letter-spacing:.1em}.t{font-size:34px;font-weight:700;margin:10px 0}
.d{font-size:23px;color:#b8c3d9;line-height:1.4}
.ledger{margin-top:34px;border:1px solid #3b5b4f;background:rgba(20,48,40,.55);border-radius:18px;padding:24px 28px;font-size:25px;color:#d7f5ea}
.srcs{margin-top:18px;font-size:22px;color:#8a96ae}</style>""")
    card("12b_trust_boundaries", "Trust boundaries", f"""
<div class="kicker">Trust boundaries (made with CodeBuddy)</div>
<div id="d" style="flex:1;display:flex;align-items:center;justify-content:center;overflow:hidden">
<pre class="mermaid" style="background:transparent">{html.escape(MMD.read_text(encoding="utf-8"))}</pre></div>""",
         head='<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>'
              '<script>mermaid.initialize({startOnLoad:true,theme:"dark",flowchart:{useMaxWidth:false,nodeSpacing:30,rankSpacing:40}});'
              'window.addEventListener("load",()=>setTimeout(()=>{const s=document.querySelector("#d svg");if(!s)return;'
              'const b=s.getBBox();const k=Math.min(1640/b.width,760/b.height);s.style.transform=`scale(${k})`;},800));</script>')
    card("13_numbers", "The numbers", f"""
<div class="kicker">We attacked it on purpose</div>
<h2>Measured, not promised.</h2>
<div class="grid">
 <div class="stat"><div class="big ok">{h['leaks']}</div><div class="lbl">leaks of restricted content in {h['n_requests_all_modes']:,} adversarial requests</div>
  <div class="src">databridge/evals/internal_brain/RESULTS.md</div></div>
 <div class="stat"><div class="big ok">{h['p2_mismatches']} / {h['p2_compared']}</div><div class="lbl">differences between "restricted" and "doesn't exist", across paired questions</div>
  <div class="src">P2 DIFFERENTIAL TEST</div></div>
 <div class="stat"><div class="big ok">{h['injection_hard_failures']}</div><div class="lbl">prompt-injection failures (planted instructions on all four platforms)</div>
  <div class="src">INJECTION PROBES</div></div>
</div>""")
    card("14_closing", "Thanks", """
<div class="kicker">Built with</div>
<h1>Thank you.</h1>
<p class="lead">CodeBuddy planned the trust boundaries, built the WorkBuddy connector, wrote tests and reviewed
our security. WorkBuddy is the second front door. Every session's log is in the repository.</p>
<div style="margin-top:46px"><span class="pill">Tencent CodeBuddy</span><span class="pill">Tencent WorkBuddy</span><span class="pill">Tencent TokenHub · Hunyuan</span><span class="pill">Walnut evidence engine</span></div>""")
    print("cards written to", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
