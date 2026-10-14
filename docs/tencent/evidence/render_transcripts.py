"""Render the CodeBuddy CLI transcripts (stream-json) as readable HTML session pages.

    python docs/tencent/evidence/render_transcripts.py [out_dir]

Each page shows the session's model, prompt, every step CodeBuddy took (its messages and tool
calls, with short tool results) and its final summary. Nothing is added or edited: the page is a
faithful view of the saved transcript (secrets were already redacted in the transcript itself).
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SESSIONS = [
    ("cb1", "Trust-boundary diagram (Plan)"),
    ("cb2", "WorkBuddy MCP connector + tests"),
    ("cb3", "Token CLI + WorkBuddy setup guide"),
    ("cb4", "Model-chain tests"),
    ("cb5", "Security review"),
    ("cb6", "Harden connectors after Codex review"),
]
CSS = """
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#0d1117;color:#e6edf3;
font:14px/1.5 'Segoe UI',Inter,system-ui,sans-serif}.wrap{max-width:1180px;margin:0 auto;padding:28px}
.top{display:flex;align-items:center;gap:14px;margin-bottom:18px}.logo{width:40px;height:40px;border-radius:10px;
background:linear-gradient(135deg,#6d5dfc,#22c3a6);display:grid;place-items:center;font-weight:700}
h1{font-size:20px;margin:0}.sub{color:#8b949e;font-size:13px}.meta{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 18px}
.chip{border:1px solid #30363d;border-radius:999px;padding:3px 10px;font-size:12px;color:#c9d1d9;background:#161b22}
.card{border:1px solid #30363d;border-radius:12px;background:#161b22;padding:14px 16px;margin:10px 0}
.label{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#8b949e;margin-bottom:6px}
.prompt{white-space:pre-wrap;font-family:Consolas,monospace;font-size:12.5px;color:#d2a8ff}
.say{white-space:pre-wrap}.tool{font-family:Consolas,monospace;font-size:12.5px;color:#79c0ff}
.res{font-family:Consolas,monospace;font-size:12px;color:#8b949e;white-space:pre-wrap;max-height:7.5em;overflow:hidden;
border-left:2px solid #30363d;padding-left:10px;margin-top:6px}.final{border-color:#2ea043}.foot{color:#8b949e;font-size:12px;margin-top:22px}
"""


def _load(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def _short(obj, n=220) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def render(sid: str, title: str) -> str:
    ev = _load(HERE / f"codebuddy_{sid}.jsonl")
    prompt = (HERE / "codebuddy_prompts" / f"{sid}.md").read_text(encoding="utf-8").strip()
    init = next((e for e in ev if e.get("type") == "system" and e.get("subtype") == "init"), {})
    result = next((e for e in reversed(ev) if e.get("type") == "result"), {})
    model = init.get("model") or next((e["message"].get("model") for e in ev
                                        if e.get("type") == "assistant" and e.get("message", {}).get("model")), "?")
    results: dict[str, str] = {}
    for e in ev:
        if e.get("type") == "user":
            for c in (e.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    body = c.get("content")
                    if isinstance(body, list):
                        body = " ".join(x.get("text", "") for x in body if isinstance(x, dict))
                    results[c.get("tool_use_id", "")] = _short(body or "", 400)
    steps = []
    for e in ev:
        if e.get("type") != "assistant":
            continue
        for c in (e.get("message") or {}).get("content") or []:
            if c.get("type") == "text" and c.get("text", "").strip():
                steps.append(f'<div class="card"><div class="label">CodeBuddy</div><div class="say">'
                             f'{html.escape(c["text"].strip())}</div></div>')
            elif c.get("type") == "tool_use":
                res = results.get(c.get("id", ""), "")
                steps.append(f'<div class="card"><div class="tool">⚙ {html.escape(c.get("name", "?"))} '
                             f'{html.escape(_short(c.get("input"), 260))}</div>'
                             + (f'<div class="res">{html.escape(res)}</div>' if res else "") + "</div>")
    if steps and result.get("result"):
        steps.pop()                                          # the last text is the final summary
    final = html.escape(str(result.get("result") or "(no final summary)"))
    chips = [f"model: {model}", f"turns: {result.get('num_turns', '?')}",
             f"duration: {round((result.get('duration_ms') or 0) / 1000)} s",
             f"outcome: {result.get('subtype', '?')}", f"session: {str(init.get('session_id', '?'))[:13]}…"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>CodeBuddy {sid.upper()}</title>
<style>{CSS}</style></head><body><div class="wrap">
<div class="top"><div class="logo">CB</div><div><h1>CodeBuddy session {sid.upper()}: {html.escape(title)}</h1>
<div class="sub">Rendered from the CodeBuddy CLI transcript docs/tencent/evidence/codebuddy_{sid}.jsonl
(working directory {html.escape(str(init.get('cwd', '?')))})</div></div></div>
<div class="meta">{''.join(f'<span class="chip">{html.escape(c)}</span>' for c in chips)}</div>
<div class="card"><div class="label">Task prompt (sent after the standard working rules in codebuddy_prompts/_rules.md)</div><div class="prompt">{html.escape(prompt)}</div></div>
{''.join(steps)}
<div class="card final"><div class="label">CodeBuddy's final summary</div><div class="say">{final}</div></div>
<div class="foot">Walnut Internal Brain · Tencent Cloud AI CAN DO IT Hackathon Singapore 2026 · evidence of
CodeBuddy use</div></div></body></html>"""


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "submission" / "01_codebuddy" / "html"
    out.mkdir(parents=True, exist_ok=True)
    for sid, title in SESSIONS:
        if (HERE / f"codebuddy_{sid}.jsonl").exists():
            (out / f"{sid}.html").write_text(render(sid, title), encoding="utf-8")
            print("wrote", out / f"{sid}.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
