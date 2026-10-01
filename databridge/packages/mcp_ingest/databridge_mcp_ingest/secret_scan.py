"""Literal secrets in a connection declaration — found, refused and never echoed (Codex round 3 c).

A connection file is written to disk, listed over HTTP and shown in the UI, so a credential typed
into it is a credential leaked. What IS fine is a REFERENCE: an env-var NAME (`env_from`,
`headers_from`, `--password-env PG_PASSWORD`) or a `${NAME}` / `${NAME:-default}` placeholder,
expanded only at connect time (`connections.expand`).

Refused, wherever they appear in a stdio transport's command/args/env or an HTTP transport's
url/headers:

* a URL carrying `user:password@` whose password is not a `${...}` reference;
* a URL query parameter named like a credential (`api_key=`, `token=`, `password=`, ...);
* an argument `--password=v`, `--api-key v`, `--token=v`, `PASSWORD=v` (flag or KEY=VALUE forms);
* an env var or header NAMED like a credential holding a literal (not a reference);
* a well-known key shape anywhere (PEM private key, AKIA/ASIA key id, provider tokens,
  `Bearer <literal>`).

`find_literal_secrets` returns (location, rule) pairs — never the value; `redact` masks a value for
any message that must quote the text around it.

FXI3 (Codex f1 #5): a credential position must hold ONLY references — `secret${IGNORED}` is a
literal with a reference stuck on (refused); `--api-key SUPERSECRET123` is a literal however it is
cased (only a flag that names a variable/file/path — `--password-env`, `--key-file` — takes a
name); and every OTHER persisted field of a connection (`tools_hint`, `title`, `aliases`, `scope`,
...) is scanned too (`find_literal_secrets_in`).

FXJ3 (Codex f2 #3): a `${NAME:-default}` placeholder's DEFAULT is itself a literal, so it is
REFUSED by default wherever it could be a credential: in a credential position (a credential-named
env var / header / flag, a URL's `user:password@`, a credential-named query parameter), and
anywhere when NAME itself is credential-named (`${DB_PASSWORD:-x}`, `${PGPASSWORD:-x}`,
`${API_TOKEN:-x}`, `${CLIENT_SECRET:-x}`). Non-credential defaults (`${WALNUT_PYTHON:-python}`,
`${PORT:-5433}`) stay legal. A local demo that really wants defaulted credentials sets the explicit
DEV flag `WALNUT_DEV_ALLOW_CREDENTIAL_DEFAULTS=1` (`reference_defaults_allowed`); nothing else
re-opens it, and the shipped manifest (`infra/mcp_servers.yaml`) uses plain `${VAR}` references.

FXJ3 (Codex f2 #4): a credential name may be CONCATENATED (`PGPASSWORD`, `MYSQLPWD`, `APITOKEN`,
`DBSECRET`): a name that ENDS in a credential word counts, separator or not.
"""
from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlsplit

_CRED_WORD = r"(?:pass(?:word|wd)?|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|" \
             r"private[_-]?key|client[_-]?secret|auth(?:orization)?|credential|bearer)"
#: A credential word that ENDS a name with no separator (`PGPASSWORD`, `APITOKEN`) — Codex f2 #4.
_CRED_SUFFIX = r"(?:passw(?:or)?d|pwd|secret|token|api[_-]?key|apikey)"
_CRED_NAME = re.compile(rf"(?i)(?:(?:^|[_\-.]){_CRED_WORD}(?:$|[_\-.])|{_CRED_SUFFIX}$)")
_REF = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*(?::-[^}]*)?\}$|^\$[A-Za-z_][A-Za-z0-9_]*$")
_HAS_REF = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(?::-[^}]*)?\}")
_URL = re.compile(r"[a-z][a-z0-9+.\-]*://[^\s\"'<>]+", re.I)
_FLAG_EQ = re.compile(rf"(?i)^--?[a-z0-9_\-]*{_CRED_WORD}[a-z0-9_\-]*=(?P<v>.+)$")
_FLAG = re.compile(rf"(?i)^--?[a-z0-9_\-]*{_CRED_WORD}[a-z0-9_\-]*$")
_KV = re.compile(rf"(?i)^(?P<k>[A-Z0-9_]*{_CRED_WORD}[A-Z0-9_]*)=(?P<v>.+)$")
_SHAPES = [
    ("private_key_block", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY")),
    ("cloud_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("provider_token", re.compile(r"\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}|"
                                  r"xox[baprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9_-]{20,}|"
                                  r"AIza[0-9A-Za-z_-]{35})\b")),
    ("bearer_literal", re.compile(r"(?i)\bbearer\s+(?!\$)[A-Za-z0-9._~+/=\-]{8,}")),
]
#: A flag / key that NAMES where a credential is (a variable, a file, a path), not the credential.
_NAMES_A_THING = re.compile(r"(?i)[_-](?:env|var|file|path)$")


def is_reference(value: str) -> bool:
    return bool(_REF.match(value.strip()))


#: The ONLY switch that lets a `${VAR:-default}` stand in a credential position (Codex f2 #3): an
#: explicit, operator-set DEV flag. Default: refused.
DEV_ALLOW_DEFAULTS_ENV = "WALNUT_DEV_ALLOW_CREDENTIAL_DEFAULTS"


def reference_defaults_allowed() -> bool:
    """True only when the operator explicitly set `WALNUT_DEV_ALLOW_CREDENTIAL_DEFAULTS=1`."""
    import os

    return (os.environ.get(DEV_ALLOW_DEFAULTS_ENV) or "").strip().lower() in ("1", "true", "yes",
                                                                             "on")


def is_credential_name(name: str) -> bool:
    """A variable / key / parameter NAMED like a credential (`DB_PASSWORD`, `PGPASSWORD`,
    `api-key`, `client_secret`)."""
    return bool(_CRED_NAME.search(str(name)))


_ANY_REF = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(?::-(?P<d>[^}]*))?\}|\$[A-Za-z_][A-Za-z0-9_]*")
_AUTH_SCHEME = re.compile(r"(?i)^\s*(?:bearer|basic|token|apikey)\s+")


def credential_literal(value: str, *, header: bool = False) -> bool:
    """True when a value in a CREDENTIAL position holds anything but references: every `${VAR}` /
    `$VAR` is removed (and, for a header, a leading auth scheme word) and whatever text is left is
    the literal. `${TOKEN}` -> False; `secret${X}` -> True; `Bearer ${T}` (header) -> False."""
    text = str(value)
    if not reference_defaults_allowed() and any(m.group("d") for m in _ANY_REF.finditer(text)):
        return True                 # a default IS a literal (Codex f2 #3)
    rest = _ANY_REF.sub("", text)
    if header:
        rest = _AUTH_SCHEME.sub("", rest)
    return bool(rest.strip())


def _literal(value: str) -> bool:
    v = value.strip()
    return bool(v) and not is_reference(v)


def _url_findings(url: str) -> list[str]:
    out: list[str] = []
    try:
        parts = urlsplit(url)
    except ValueError:
        return out
    netloc = parts.netloc
    if "@" in netloc:
        userinfo = netloc.rsplit("@", 1)[0]
        if ":" in userinfo:
            pw = userinfo.split(":", 1)[1]
            if pw.strip() and credential_literal(pw):      # a `${VAR:-default}` counts (f2 #3)
                out.append("url_userinfo_password")
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        if _CRED_NAME.search(k) and v.strip() and credential_literal(v):
            out.append("url_credential_query_param")
    return out


def _default_findings(text: str) -> list[str]:
    """`${CREDENTIAL_NAME:-default}` anywhere: the default is a literal credential (f2 #3)."""
    if reference_defaults_allowed():
        return []
    for m in _ANY_REF.finditer(text):
        if m.group("d") and m.group(0).startswith("${") \
                and is_credential_name(m.group(0)[2:].split(":", 1)[0]):
            return ["credential_reference_default"]
    return []


def _text_findings(text: str) -> list[str]:
    out = [rule for rule, rx in _SHAPES if rx.search(text)]
    for m in _URL.finditer(text):
        out.extend(_url_findings(m.group(0)))
    out.extend(_default_findings(text))
    return out


def find_literal_secrets(transport: Any) -> list[tuple[str, str]]:
    """(location, rule) for every literal secret in a transport (dict or model). Never values."""
    t = transport if isinstance(transport, dict) else transport.model_dump(mode="json")
    found: list[tuple[str, str]] = []

    def add(where: str, rules: Iterable[str]) -> None:
        for r in rules:
            if (where, r) not in found:
                found.append((where, r))

    if t.get("type") == "http":
        add("url", _text_findings(str(t.get("url") or "")))
        for name, value in (t.get("headers") or {}).items():
            value = str(value)
            rules = _text_findings(value)
            if _CRED_NAME.search(name) and credential_literal(value, header=True):
                rules.append("credential_named_header")
            add(f"headers.{name}", rules)
        return found
    add("command", _text_findings(str(t.get("command") or "")))
    args = [str(a) for a in t.get("args") or []]
    for i, a in enumerate(args):
        rules = _text_findings(a)
        m = _FLAG_EQ.match(a)
        if m and not _NAMES_A_THING.search(a.split("=", 1)[0]) \
                and credential_literal(m.group("v")):
            rules.append("credential_flag_value")
        m = _KV.match(a)
        if m and not _NAMES_A_THING.search(m.group("k")) and credential_literal(m.group("v")):
            rules.append("credential_assignment_arg")
        if _FLAG.match(a) and i + 1 < len(args):
            nxt = args[i + 1]
            # `--password-env PG_PASSWORD` names a variable; `--api-key S3CR3T` is the secret,
            # upper-case or not (FXI3: no case-based exemption)
            if not nxt.startswith("-") and credential_literal(nxt) \
                    and not _NAMES_A_THING.search(a):
                rules.append("credential_flag_value")
        add(f"args[{i}]", rules)
    for name, value in (t.get("env") or {}).items():
        value = str(value)
        rules = _text_findings(value)
        if _CRED_NAME.search(name) and credential_literal(value) \
                and not re.search(r"(?i)(?:path|file|dir|url|uri|scopes?|port)$", name):
            rules.append("credential_named_env_literal")
        add(f"env.{name}", rules)
    return found


def find_literal_secrets_in(value: Any, where: str = "") -> list[tuple[str, str]]:
    """(location, rule) for literal secrets ANYWHERE in a persisted structure (every field of a
    connection other than its transport: `tools_hint`, `title`, `aliases`, `scope`...): key shapes
    and credential URLs in every string, and a credential-NAMED key holding a literal. Never
    values."""
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            loc = f"{where}.{k}" if where else str(k)
            if isinstance(v, str) and _CRED_NAME.search(str(k)) and credential_literal(v) \
                    and not _NAMES_A_THING.search(str(k)):
                found.append((loc, "credential_named_field"))
            found.extend(find_literal_secrets_in(v, loc))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            found.extend(find_literal_secrets_in(v, f"{where}[{i}]"))
    elif isinstance(value, str):
        found.extend((where, r) for r in dict.fromkeys(_text_findings(value)))
    return found


def redact(text: str) -> str:
    """`text` with every recognisable secret masked — for a message that must quote it."""
    out = text
    for _, rx in _SHAPES:
        out = rx.sub("[REDACTED]", out)
    out = re.sub(r"(://[^:/@\s]+:)(?!\$\{)[^@\s]+@", r"\1[REDACTED]@", out)
    # FXI3 (for FXI1's ledger scrubbing): `password=v`, `api_key=v`, `TOKEN=v`, `--api-key=v`,
    # `"client_secret": "v"` — the shapes the scanner refuses. A `${VAR}` reference is kept; a
    # key that NAMES where the secret is (`token_file=`, `password_env=`) is not a credential.
    out = _KV_CRED.sub(_mask_kv, out)
    return out


_KV_CRED = re.compile(
    # the key must END in the credential word (`auth_token`, `PGPASSWORD`, `--api-key`), so a
    # counter such as `token_count` / `tokens_in` in a ledger row is never masked
    rf"(?i)(?<![A-Za-z0-9_.\-])(?P<key>[\"']?(?:--?)?[A-Za-z0-9_.\-]*{_CRED_WORD}[\"']?)"
    r"(?P<sep>\s*[=:]\s*)(?P<q>[\"']?)(?P<val>\$\{[^}]*\}|[^\s\"'&,;}]+)")


def _mask_kv(m: re.Match) -> str:
    key = m.group("key").strip("\"'")
    if _NAMES_A_THING.search(key) or is_reference(m.group("val")) \
            or m.group("val") == "[REDACTED]":
        return m.group(0)
    return f"{m.group('key')}{m.group('sep')}{m.group('q')}[REDACTED]"


__all__ = ["find_literal_secrets", "find_literal_secrets_in", "redact", "is_reference",
           "credential_literal", "reference_defaults_allowed", "is_credential_name",
           "DEV_ALLOW_DEFAULTS_ENV"]
