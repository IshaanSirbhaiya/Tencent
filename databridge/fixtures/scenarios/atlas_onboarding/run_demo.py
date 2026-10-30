"""Run Atlas on its own ports/state using the existing Walnut web app.

No server already running is stopped. Ctrl-C stops only the children this process started.
Default: deterministic, no paid API calls. --with-configured-model opts into existing env keys.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from fastapi import Request

ROOT = Path(__file__).resolve().parent
DB = ROOT.parents[2]
WEB = DB / "apps/internal_brain_web"
PORTS = (8215, 8115, 3115)


def backend():
    import httpx
    import uvicorn
    from fastapi import Request
    from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
    from databridge_ib.bootstrap import build_app

    app = build_app(poll_s=None)
    prompt = json.loads((ROOT / "manifest.json").read_text())["question"]
    import html

    @app.get("/scenario/atlas", response_class=HTMLResponse)
    def landing():
        return ("<!doctype html><html><head><title>Atlas onboarding demo</title></head>"
                "<body style='font:17px system-ui;max-width:760px;margin:70px auto;background:#f5f5f7;color:#202124'>"
                "<h1>Your first day leading Project Atlas.</h1><p>Same Company A. New project context. "
                "All records are fictional. This instance has its own state and ports.</p>"
                "<form method='post' action='/scenario/atlas/sign-in'>"
                "<button style='padding:15px 24px;border:0;border-radius:12px;background:#007aff;color:white;font:inherit'>"
                "Open Walnut as Alex Morgan</button></form><h2>Ask this</h2>"
                f"<textarea readonly rows='12' style='width:100%;padding:18px;font:16px system-ui;border-radius:12px'>{html.escape(prompt)}</textarea>"
                "<p>Copy the question, open Walnut, and paste it into Ask. Default mode uses quoted sources "
                "and rules; the interface keeps those labels visible.</p></body></html>")

    @app.post("/scenario/atlas/sign-in")
    def sign_in(request: Request):
        origin = request.headers.get("origin")
        if origin and origin not in {"http://localhost:8115", "http://127.0.0.1:8115"}:
            return JSONResponse({"detail": "wrong origin"}, status_code=403)
        # Use the existing sign-in endpoint so the real audit record and cookie rules run.
        with httpx.Client() as client:
            result = client.post("http://127.0.0.1:8115/auth/demo-login", json={"persona": "alex.product"})
        if result.status_code != 200:
            return JSONResponse({"detail": "demo sign-in unavailable"}, status_code=503)
        response = RedirectResponse("http://localhost:3115/", status_code=303)
        response.set_cookie("ib_session", result.cookies["ib_session"], httponly=True,
                            samesite="lax", max_age=8 * 3600)
        return response

    uvicorn.run(app, host="127.0.0.1", port=8115, log_level="warning")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--with-configured-model", action="store_true")
    args = ap.parse_args()
    for part in ["apps/internal_brain", "apps/mock_platforms", "packages/core", "packages/harness",
                 "packages/adapter", "packages/retrieval"]:
        sys.path.insert(0, str(DB / part))
    if args.backend:
        backend()
        return
    for port in PORTS:
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise SystemExit(f"Port {port} is in use. No servers were changed; close the earlier Atlas run first.")
    if not (ROOT / "world.json").exists():
        subprocess.run([sys.executable, str(ROOT / "build.py")], check=True)
    node = shutil.which("node")
    if node is None:
        raise SystemExit("Node.js is needed for the existing Walnut web app.")
    env = dict(os.environ)
    env.update({"IB_WORLD": str(ROOT / "world.json"), "MOCK_WORLD_PATH": str(ROOT / "world.json"),
                "IB_VAR": str(DB / "var/atlas-onboarding/state"), "IB_MOCK_BASE": "http://127.0.0.1:8215",
                "IB_API_URL": "http://127.0.0.1:8115", "IB_DEMO_LOGIN": "1", "IB_DELIVERABLES": "1",
                "IB_DEMO_FEATURES": "0", "NEXT_PUBLIC_IB_MOCK": "0", "IB_MOCK": "0",
                "NEXT_DIST_DIR": ".next-atlas", "PYTHONPATH": os.pathsep.join(sys.path)})
    if not args.with_configured_model:
        env.update({"IB_FORCE_OFFLINE": "1", "IB_CLAUDE": "0"})
    else:
        env["IB_CLAUDE"] = "1"
    logs = DB / "var/atlas-onboarding/logs"
    logs.mkdir(parents=True, exist_ok=True)
    commands = [
        ("sources", [sys.executable, "-m", "mock_platforms", "--port", "8215"], DB),
        ("brain", [sys.executable, str(Path(__file__).resolve()), "--backend"], DB),
        ("web", [node, str(WEB / "node_modules/next/dist/bin/next"), "dev", "-H", "127.0.0.1", "-p", "3115"], WEB),
    ]
    children, handles = [], []
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        for name, command, cwd in commands:
            handle = (logs / (name + ".log")).open("w")
            handles.append(handle)
            children.append(subprocess.Popen(command, cwd=cwd, env=env, stdout=handle, stderr=handle))
            if name == "sources":
                for _ in range(100):
                    try:
                        urllib.request.urlopen("http://127.0.0.1:8215/health", timeout=1)
                        break
                    except Exception:
                        time.sleep(.1)
        print("Atlas demo starting. Open http://localhost:8115/scenario/atlas", flush=True)
        print("Separate web: http://localhost:3115 · mock sources: 8215 · Brain: 8115", flush=True)
        print("Mode: " + ("existing configured model (if available)" if args.with_configured_model else "offline; no external model calls"), flush=True)
        print("Ctrl-C stops only this demo. Logs: " + str(logs), flush=True)
        while True:
            for child in children:
                if child.poll() is not None:
                    raise RuntimeError("A demo process exited; inspect the scenario logs.")
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
        for handle in handles:
            handle.close()


if __name__ == "__main__":
    main()
