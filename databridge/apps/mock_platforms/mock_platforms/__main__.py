"""Run the mock platforms: ``python -m mock_platforms [--world PATH] [--host H] [--port N]``.

Run from ``databridge/apps/mock_platforms`` (or with it on ``PYTHONPATH``). Env: ``MOCK_WORLD_PATH``,
``MOCK_SVC_TOKEN`` (svc-dev), ``MOCK_ADMIN_TOKEN`` (admin-dev), ``MOCK_WEBHOOK_SECRET`` (whsec-dev),
``MOCK_WEBHOOK_URL`` (unset = webhooks recorded only, visible at ``GET /admin/webhooks``).
"""
from __future__ import annotations

import argparse
import os

import uvicorn

from .app import DEFAULT_WORLD, create_app


def main() -> None:
    ap = argparse.ArgumentParser(prog="mock_platforms")
    ap.add_argument("--world", default=os.environ.get("MOCK_WORLD_PATH") or str(DEFAULT_WORLD))
    ap.add_argument("--host", default=os.environ.get("MOCK_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("MOCK_PORT", "8200")))
    args = ap.parse_args()
    uvicorn.run(create_app(args.world), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
