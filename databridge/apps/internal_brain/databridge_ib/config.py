"""Runtime configuration for the Internal Brain — one place for paths, secrets-by-env and SLOs.

State lives under ``var/ib`` (git-ignored) unless ``IB_VAR`` points elsewhere; tests and evals pass
their own root so they never touch the demo ledger (plan §4.7).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

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


#: Host headers the IB answers to (DNS-rebinding guard, Opus final review #1). Loopback names only:
#: a rebinding page always presents ITS OWN domain name as the Host, never one of these.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
#: the fixed Host of the in-process ASGI test transport (Starlette ``TestClient``), used by the
#: tests and the eval harness; a single-label name no public DNS can hand to a browser page
TEST_TRANSPORT_HOST = "testserver"


def _host_of(origin: str) -> str:
    try:
        host = urlsplit(origin).hostname or ""
    except ValueError:
        return ""
    return f"[{host}]" if ":" in host else host


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
        bad += [n for n, v, d in (("MOCK_SVC_TOKEN", self.svc_token, "svc-dev"),
                                  ("MOCK_ADMIN_TOKEN", self.admin_token, "admin-dev"),
                                  ("MOCK_WEBHOOK_SECRET", self.webhook_secret, "whsec-dev")) if v == d]
        if self.replay_key:
            bad.append("IB_REPLAY_KEY (seeding switch; never on a deployment)")
        if self.token_key == self.ledger_key:
            bad.append("IB_LEDGER_KEY (must differ from IB_TOKEN_KEY)")
        if bad:
            raise RuntimeError(f"refusing to start: set strong values for {', '.join(bad)}")
        # demo sign-in lets the caller pick a persona; on a reachable deployment only holders of
        # the passcode may use it (CodeBuddy security review CB5 finding 1)
        if self.demo_login and len(_env("IB_DEMO_PASSCODE")) < 8:
            raise RuntimeError("refusing to start: IB_DEMO_LOGIN=1 on a deployment needs "
                               "IB_DEMO_PASSCODE of at least 8 characters")
    demo_login: bool = field(default_factory=lambda: _env("IB_DEMO_LOGIN") == "1")
    #: the demo-history seeding switch (scripts/seed_demo_activity.py): when set (>= 24 chars) and
    #: NOT deployed, a loopback bearer call to /ib/ask carrying this key in ``X-IB-Replay-Key`` is
    #: recorded as ``channel="replay"``, ``synthetic_replay=True`` at ``X-IB-Replay-At``. Off by default.
    replay_key: str = field(default_factory=lambda: _env("IB_REPLAY_KEY"))
    live: bool = field(default_factory=lambda: _env("IB_LIVE") == "1")
    #: the public origin of the web app on a deployment (e.g. https://brain.example.com); its host
    #: is the only non-loopback Host the IB accepts when deployed
    public_origin: str = field(default_factory=lambda: _env("IB_PUBLIC_ORIGIN"))
    #: extra Host names, comma-separated (e.g. a LAN name for a rehearsal); empty by default
    extra_hosts: str = field(default_factory=lambda: _env("IB_ALLOWED_HOSTS"))

    def trusted_hosts(self) -> list[str]:
        """The Host allowlist: loopback always; when deployed, ``IB_PUBLIC_ORIGIN``'s host; when
        not deployed, the in-process test transport; plus ``IB_ALLOWED_HOSTS``. Anything else is a
        400 before any route runs."""
        hosts = list(LOOPBACK_HOSTS)
        if self.deployed:
            if _host_of(self.public_origin):
                hosts.append(_host_of(self.public_origin))
        else:
            hosts.append(TEST_TRANSPORT_HOST)
        hosts += [h.strip().lower() for h in self.extra_hosts.split(",") if h.strip()]
        return list(dict.fromkeys(hosts))

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
