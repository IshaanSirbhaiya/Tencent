"""Runtime configuration for the Internal Brain — one place for paths, secrets-by-env and SLOs.

State lives under ``var/ib`` (git-ignored) unless ``IB_VAR`` points elsewhere; tests and evals pass
their own root so they never touch the demo ledger (plan §4.7).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]          # .../databridge
DEFAULT_WORLD = DATABRIDGE_ROOT / "fixtures" / "enterprise" / "world.json"

#: Plan §4.8 freshness SLOs (seconds).
SLO_MOCK_S = 5 * 60
SLO_LIVE_S = 15 * 60
#: Plan §4.2 step 6.
GATE2_TIMEOUT_S = 2.0
TOP_K = 12
POLICY_VERSION = "ib-policy-1"
#: dev-only defaults (≥32 chars, as DecisionLedger requires); a deployed build refuses them
DEV_TOKEN_KEY = "ib-token-dev-key-not-for-deployment-0001"
DEV_LEDGER_KEY = "ib-ledger-dev-key-not-for-deployment-0001"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class IbConfig:
    var_root: Path = field(default_factory=lambda: Path(_env("IB_VAR") or DATABRIDGE_ROOT / "var" / "ib"))
    world_path: Path = field(default_factory=lambda: Path(_env("IB_WORLD") or DEFAULT_WORLD))
    #: base URL of the platform APIs (the mock platforms app, or a real service per platform)
    platform_base: dict = field(default_factory=lambda: {
        p: _env(f"IB_{p.upper()}_BASE") or _env("IB_MOCK_BASE", "http://127.0.0.1:8200")
        for p in ("slack", "confluence", "jira", "drive")})
    svc_token: str = field(default_factory=lambda: _env("MOCK_SVC_TOKEN", "svc-dev"))
    admin_token: str = field(default_factory=lambda: _env("MOCK_ADMIN_TOKEN", "admin-dev"))
    webhook_secret: str = field(default_factory=lambda: _env("MOCK_WEBHOOK_SECRET", "whsec-dev"))
    token_key: str = field(default_factory=lambda: _env("IB_TOKEN_KEY", DEV_TOKEN_KEY))
    ledger_key: str = field(default_factory=lambda: _env("IB_LEDGER_KEY", DEV_LEDGER_KEY))
    deployed: bool = field(default_factory=lambda: _env("IB_DEPLOYED") == "1")
    anchor_file: str = field(default_factory=lambda: _env("IB_ANCHOR_FILE"))
    anchor_interval_s: float = field(default_factory=lambda: float(_env("IB_ANCHOR_INTERVAL_S", "900")))

    def assert_deployable(self) -> None:
        """A deployed build must never run on the dev defaults (public repo ⇒ public keys)."""
        if not self.deployed:
            return
        bad = [n for n, v, d in (("IB_TOKEN_KEY", self.token_key, DEV_TOKEN_KEY),
                                 ("IB_LEDGER_KEY", self.ledger_key, DEV_LEDGER_KEY))
               if v == d or len(v) < 32]
        if bad:
            raise RuntimeError(f"refusing to start: set strong values for {', '.join(bad)}")
    demo_login: bool = field(default_factory=lambda: _env("IB_DEMO_LOGIN") == "1")
    live: bool = field(default_factory=lambda: _env("IB_LIVE") == "1")

    # derived paths ---------------------------------------------------------------------------
    @property
    def ledger_path(self) -> Path:
        return self.var_root / "ledger" / "access.jsonl"

    @property
    def anchors_path(self) -> Path:
        return self.var_root / "anchors" / "access.anchors.jsonl"

    @property
    def content_db(self) -> Path:
        return self.var_root / "content" / "answers.sqlite"

    @property
    def index_db(self) -> Path:
        return self.var_root / "index" / "index.sqlite"

    @property
    def acl_db(self) -> Path:
        return self.var_root / "index" / "acl.sqlite"

    @property
    def directory_db(self) -> Path:
        return self.var_root / "directory.sqlite"

    @property
    def revoked_path(self) -> Path:
        return self.var_root / "revoked.json"

    @property
    def slo_s(self) -> int:
        return SLO_LIVE_S if self.live else SLO_MOCK_S
