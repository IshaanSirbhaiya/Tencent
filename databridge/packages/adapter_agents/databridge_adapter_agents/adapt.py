"""Adaptation — adapter agents adjust to source FORMAT changes at runtime (BUILD_PLAN §12, 2026-10-01).

On every sync (and when an MCP server says its tool/resource list changed) the agent fingerprints
each connection's shape (`databridge_mcp_ingest.drift`), diffs it against the APPROVED baseline, and
classifies every drift:

=========================  ==========  ==================================================================
drift                      class       under ``auto_additive`` (the default)
=========================  ==========  ==================================================================
added_column               additive    mapping extended as a NEW VERSION (attr named by rule; model
                                       second, validated) — its values land in this sync's build
new_table                  additive    mapping proposed from a measured profile (rules, then the model
                                       constrained to the vocabulary) and approved as a new version;
                                       a table no kind maps is recorded unmapped (ontology-proposal
                                       material once it recurs)
new_file_type              additive    `scope.file_types` extended as a new connection version when an
                                       extractor exists — captured from the next sync
added_field / tool_added   additive    noted, baseline advanced; a new tool is not used until a plan
/ new_mime                             needs it
removed / type_changed     breaking    ASKED (unless the spec narrows `always_ask`): the affected table
/ tool_removed /                       / step is HELD, a proposed new version + evidence + the diff is
tool_signature_changed                 filed for approval, the rest keeps syncing
renamed                    breaking    ALWAYS asked — a rename is a candidate, never a certainty
new_write_tool             breaking    ALWAYS asked (spec `ALWAYS_ASKED`); a crawl never calls it anyway
subject_touching           qualifier   ALWAYS asked (spine D4b, hard constraint 1)
=========================  ==========  ==================================================================

``ask_all`` asks every drift; ``off`` detects and REPORTS only (no version, no hold).

**Nothing approved is ever overwritten or deleted.** Before a plan / mapping / connection changes,
the version in force is copied to `_adapt/_versions/<conn>/<artifact>/v####.yaml` and the new one
is recorded after it (`versions.jsonl`, append-only). Baselines keep an append-only history too.

**A hold never drops data silently.** A held table is ledgered in the run directory as excluded with
rule `DRIFT_PENDING_APPROVAL` and its reversal (`excluded.drift.jsonl`, found by pattern — P3). Until
P wires the runner hook (`HONOURS_DRIFT_HOLDS`, see the AA review) a held table is held by moving its
mapping entry to `proposed` (its rows are captured, not built); a held step, or a removed table,
holds the whole connection — the coarser hold is said, never hidden.

Approval takes an AUTHENTICATED principal (an object with `principal_id`, as `agent_runtime`'s
`Approver` and the gateway's principal carry) — a typed name is refused (hard constraint 4).
"""
from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import threading
import weakref
from pathlib import Path
from typing import Any, Callable, Optional

from .events import now_iso
from .trace import RecordingHarness

#: Drift kinds that are ADDITIVE: nothing already in the graph can be misread because of them.
ADDITIVE = frozenset({"added_field", "added_column", "new_mime", "new_file_type", "tool_added",
                      "new_table"})
#: Never applied without a person (a rename is a guess; a write tool and a subject are a person's).
NEVER_AUTO = frozenset({"renamed", "new_write_tool", "subject_touching"})
#: Asked under EVERY policy, `off` included (spec `ALWAYS_ASKED`; `off` reports a rename, it never
#: applies one).
MANDATORY = frozenset({"new_write_tool", "subject_touching"})
#: spec `always_ask` names a category; a removed TOOL is a removal.
_CATEGORY = {"tool_removed": "removed"}
DEFAULT_ALWAYS_ASK = frozenset({"removed", "renamed", "type_changed", "tool_signature_changed",
                                "new_write_tool", "subject_touching"})
OPEN = ("pending", "rejected", "reported")
_ATTR = re.compile(r"^[a-z][a-z0-9_]*$")
_MACHINE = ("agent:", "adapter_agent:", "workflow:", "system", "rules")
ATTR_TASK_CLASS = "schema_annotation"


class AdaptationRefused(ValueError):
    """An approval / rejection that may not happen. Plain words."""


# --- policy ---------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class Policy:
    policy: str = "auto_additive"
    on_sync: bool = True
    on_list_changed: bool = True
    always_ask: frozenset = DEFAULT_ALWAYS_ASK

    @classmethod
    def of(cls, spec: Any) -> "Policy":
        """The spec's `adapt` block (read defensively: an older spec without it gets defaults)."""
        a = getattr(spec, "adapt", None)
        if a is None:
            return cls()
        get = (lambda k, d: a.get(k, d)) if isinstance(a, dict) else \
            (lambda k, d: getattr(a, k, d))
        watch = get("watch", None)
        wget = (lambda k: (watch.get(k, True) if isinstance(watch, dict)
                           else getattr(watch, k, True))) if watch is not None else (lambda k: True)
        ask = frozenset(get("always_ask", DEFAULT_ALWAYS_ASK) or ()) | \
            frozenset({"new_write_tool", "subject_touching"})
        return cls(policy=str(get("policy", "auto_additive")), on_sync=bool(wget("on_sync")),
                   on_list_changed=bool(wget("on_list_changed")), always_ask=ask)

    def asks(self, drift: Any) -> tuple[bool, str]:
        """(asked?, why) for one drift under this policy. The MANDATORY kinds (`MANDATORY`: a new
        write tool, anything touching a subject) are decided BEFORE the policy is read, so no
        policy — `off` included — can turn them into a report (spec `ALWAYS_ASKED`, R-019 #1)."""
        if drift.subject_touching:
            return True, (f"touches a subject ({drift.subject_reason}): always asked, under every "
                          f"policy")
        if drift.kind == "new_write_tool":
            return True, "a new write tool is always asked, under every policy"
        if self.policy == "off":
            return False, "adapt.policy is off: detected and reported only"
        if drift.kind == "renamed":
            return True, "a rename is a candidate, never a certainty: always asked"
        if self.policy == "ask_all":
            return True, "adapt.policy is ask_all"
        if drift.kind in ADDITIVE:
            return False, "additive: nothing already built can be misread"
        cat = _CATEGORY.get(drift.kind, drift.kind)
        if cat in self.always_ask:
            return True, f"{cat!r} is in adapt.always_ask"
        return False, f"{cat!r} was taken out of adapt.always_ask by the spec"

    def as_dict(self) -> dict[str, Any]:
        return {"policy": self.policy, "watch": {"on_sync": self.on_sync,
                                                 "on_list_changed": self.on_list_changed},
                "always_ask": sorted(self.always_ask)}


#: principal TYPE -> the mechanism that vouches for it. Only an instance of EXACTLY one of these
#: types is a principal (R-019 #2): a dict, a string or any other object that merely carries a
#: `principal_id` attribute is a claim a caller can type, not an authentication. The application's
#: authentication layer registers its principal type once at boot (`trust_principal_type`); the
#: agent runtime's `Approver` is trusted when that package is importable.
_TRUSTED_PRINCIPALS: dict[type, str] = {}
_TRUST_LOCK = threading.Lock()


def trust_principal_type(cls: type, *, authenticated_by: str) -> None:
    """Register the type the application's AUTHENTICATION layer mints for a request's principal
    (the gateway's `AuthenticatedPrincipal`, the CLI's OS-login principal). Called at boot by the
    code that authenticates — never with a type a caller can choose."""
    if not isinstance(cls, type) or cls in (dict, str, object):
        raise TypeError("a trusted principal type must be a dedicated class")
    if not (authenticated_by or "").strip():
        raise ValueError("say which mechanism authenticates this principal type")
    with _TRUST_LOCK:
        _TRUSTED_PRINCIPALS[cls] = authenticated_by.strip()


def _trusted_types() -> dict[type, str]:
    out = dict(_TRUSTED_PRINCIPALS)
    try:                                                          # optional: the agent runtime
        from databridge_agent_runtime.runtime import Approver     # type: ignore
    except Exception:                                             # noqa: BLE001
        Approver = None                                           # type: ignore[assignment]
    if Approver is not None:
        out.setdefault(Approver, "agent_runtime.Approver")
    return out


# --- delegated approval (INT3; coordinator default, founder may overrule) ---------------------------
#: An adapter agent's ``auto_additive`` policy is a DELEGATED approval by the principal who
#: ACTIVATED the agent: it applies ONLY to additive changes inside the declared scope. Breaking
#: changes, subject-touching changes, new write tools and scope widening still need a person. The
#: delegation is recorded distinctly (``authenticated_by: "delegated:<policy digest>"`` plus
#: ``delegated_by``) and mcp_ingest proves additivity itself (`mapping.approve_delegated_additive`),
#: so the agent cannot approve a non-additive change even if its own classification is wrong.
DELEGATED_POLICY = "auto_additive"
_MINTED_DELEGATIONS: "weakref.WeakSet[DelegatedApproval]" = weakref.WeakSet()


@dataclasses.dataclass(frozen=True, eq=False)
class DelegatedApproval:
    """A delegation, minted by THIS module only (`_mint_delegation`); a hand-built one is a claim
    and is refused by `delegation_of`. It is not a principal: `principal_of` refuses it, so it
    can never approve a plan, an override, a dialect or an adaptation."""
    agent_id: str
    delegated_by: str
    policy: str
    policy_digest: str
    adaptation_id: str
    scope: str = "additive_within_declared_scope"
    #: AA3: the SEALED delegation record (`delegations.py`) minted when a person activated the
    #: agent: its chain hash and the log it is in. `delegation_of` re-reads that log every time; a
    #: delegation that cites no sealed record, or one no longer in force, is refused.
    seal_hash: str = ""
    seal_log: str = ""


def policy_digest(policy: "Policy") -> str:
    return "sha256:" + _sha(policy.as_dict())


def _mint_delegation(spec: Any, *, adaptation_id: str, seal: Optional[dict[str, Any]] = None,
                     seal_log: Optional[Path] = None,
                     delegated_by: Optional[str] = None) -> DelegatedApproval:
    """One adaptation's delegation, issued FROM a sealed record (`seal`, read from `seal_log`).
    Without a seal the object is still minted but `delegation_of` refuses it, so nothing is ever
    auto-applied without a record (AA3)."""
    pol = Policy.of(spec)
    if seal is not None:
        delegated_by = str(seal.get("principal_id") or "")
    d = DelegatedApproval(agent_id=str(spec.id), delegated_by=delegated_by or "",
                          policy=pol.policy, policy_digest=policy_digest(pol),
                          adaptation_id=adaptation_id,
                          seal_hash=str((seal or {}).get("hash") or ""),
                          seal_log=str(seal_log) if seal is not None and seal_log else "")
    _MINTED_DELEGATIONS.add(d)
    return d


def delegation_of(obj: Any, what: str) -> dict[str, Any]:
    """The approval record of a minted delegation, or `AdaptationRefused`."""
    if type(obj) is not DelegatedApproval or obj not in _MINTED_DELEGATIONS:
        raise AdaptationRefused(f"{what} REFUSED: a {type(obj).__name__} is not a delegation this "
                                f"process minted for an active adapter agent")
    if obj.policy != DELEGATED_POLICY:
        raise AdaptationRefused(f"{what} REFUSED: adapt.policy {obj.policy!r} delegates nothing")
    pid = (obj.delegated_by or "").strip()
    if not pid or pid.lower().startswith(_MACHINE):
        raise AdaptationRefused(f"{what} REFUSED: no person activated this agent, so no person "
                                f"delegated its approvals")
    from .delegations import DelegationRefused, verify_sealed            # noqa: PLC0415

    try:
        sealed = verify_sealed(obj.seal_log, obj.seal_hash, agent_id=obj.agent_id,
                               delegated_by=pid, policy_digest=obj.policy_digest)
    except DelegationRefused as exc:
        raise AdaptationRefused(f"{what} REFUSED (DELEGATION_NOT_SEALED): {exc}") from None
    return {"principal_id": pid, "authenticated_by": f"delegated:{obj.policy_digest}",
            "principal_type": "DelegatedApproval", "delegated_by": pid,
            "policy_digest": obj.policy_digest, "agent_id": obj.agent_id,
            "adaptation_id": obj.adaptation_id, "scope": obj.scope, "dev_only": False,
            "delegation_seal": {"hash": sealed["hash"], "at": sealed.get("at"),
                                "scope_digest": sealed.get("scope_digest"),
                                "spec_digest": sealed.get("spec_digest"),
                                "activator_authenticated_by": sealed.get("authenticated_by")}}


def principal_of(by: Any, what: str) -> dict[str, Any]:
    """The authenticated principal behind `by`, or `AdaptationRefused`."""
    if by is None or isinstance(by, str):
        raise AdaptationRefused(
            f"{what} REFUSED: {by!r} is a name, not an authenticated principal. Pass the "
            f"principal the application authenticated (an Approver, or the gateway's principal "
            f"object); a caller-typed name cannot approve anything (hard constraint 4)")
    trusted = _trusted_types()
    mechanism = trusted.get(type(by))
    if mechanism is None:
        raise AdaptationRefused(
            f"{what} REFUSED: a {type(by).__name__} is not a principal the authentication layer "
            f"minted (trusted: {sorted(t.__name__ for t in trusted) or 'none registered'}); a "
            f"dict or an object that merely carries a principal_id is a claim, not an "
            f"authentication")
    if mechanism == "agent_runtime.Approver" or type(by).__module__.startswith(
            "databridge_agent_runtime"):
        # INT2 (R-021 follow-up 4): an Approver is valid only when a REGISTERED authenticator
        # issued it (seal verified); a hand-built Approver is a claim, not an authentication.
        from databridge_agent_runtime import is_issued_principal   # noqa: PLC0415
        if not is_issued_principal(by):
            raise AdaptationRefused(
                f"{what} REFUSED: this Approver was not issued by a registered authenticator "
                f"(databridge_agent_runtime.register_authenticator); a caller-constructed "
                f"Approver is a claim, not an authentication")
    pid = getattr(by, "principal_id", None)
    if not isinstance(pid, str) or not pid.strip():
        raise AdaptationRefused(f"{what} REFUSED: the principal carries no principal_id")
    if pid.strip().lower().startswith(_MACHINE):
        raise AdaptationRefused(f"{what} REFUSED: {pid!r} is a machine actor; a person decides")
    auth = getattr(by, "authenticated_by", None)
    if auth is not None and (not isinstance(auth, str) or not auth.strip()):
        raise AdaptationRefused(f"{what} REFUSED: the principal says nothing authenticated it")
    return {"principal_id": pid.strip(), "authenticated_by": (auth or mechanism).strip(),
            "principal_type": type(by).__name__}


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def changes_digest_of(drift_ids: Any) -> str:
    """The digest of EXACTLY the changes an adaptation carries (R-025, Q-09). A drift id already
    hashes its kind, unit, column, old and new value, so the digest names each change, not just
    how many there are. A decision may cite it; one that cites another set is refused."""
    return "sha256:" + _sha(sorted({str(i) for i in drift_ids or []}))


#: the note a reconcile writes when the source no longer shows an adaptation's drifts (also how a
#: record written before R-025 says its rejection was lifted by the source reverting)
_REVERTED = "the source no longer differs from the approved baseline here"


def rejection_stands(rec: dict[str, Any]) -> bool:
    """Was this adaptation REJECTED, and does that rejection still stand? It stands until the
    source no longer shows its drifts (a revert, or the source changing it again). A record that
    an older sync superseded by re-bundling (the Q-09 bug) keeps its rejection."""
    hist = rec.get("history") or []
    idx = [i for i, h in enumerate(hist) if h.get("status") == "rejected"]
    if not idx or rec.get("rejection_lifted"):
        return False
    return not any(h.get("status") == "superseded" and str(h.get("note") or "")
                   .startswith(_REVERTED) for h in hist[idx[-1] + 1:])


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _append(path: Path, rec: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as fh:
        fh.write(json.dumps(rec, sort_keys=True, default=str).encode("utf-8") + b"\n")
        fh.flush()


class AdaptAuditError(RuntimeError):
    """An append-only adaptation record could not be read or written as it must be. BLOCKING:
    a decision is never taken over an audit trail with a hole in it (R-019 #5)."""


def _read_jsonl(path: Path, *, strict: bool = False) -> list[dict[str, Any]]:
    """Every record in a JSONL file. `strict` (the adaptation log and the version index): a line
    that does not parse is an `AdaptAuditError`, never skipped — a skipped line could hide a
    decision or let a version number be reused."""
    if not path.is_file():
        return []
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                if strict:
                    raise AdaptAuditError(f"{path} line {n} is not a record ({exc}); the audit "
                                          f"trail is damaged — repair it before any adaptation "
                                          f"is decided") from exc
                continue
            if strict and not isinstance(rec, dict):
                raise AdaptAuditError(f"{path} line {n} is not an object")
            if isinstance(rec, dict):
                out.append(rec)
    return out


class _FileLock:
    """A cross-process lock (exclusive creation of `<path>`), for allocating version numbers. A
    lock older than `stale_s` is from a process that died holding it and is broken (said)."""

    def __init__(self, path: Path, *, timeout_s: float = 20.0, stale_s: float = 120.0) -> None:
        self.path, self.timeout_s, self.stale_s = path, timeout_s, stale_s

    def __enter__(self) -> "_FileLock":
        import time

        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()}".encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > self.stale_s:
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise AdaptAuditError(f"could not take the version lock {self.path} within "
                                          f"{self.timeout_s:.0f}s; nothing was written")
                time.sleep(0.02)

    def __exit__(self, *exc: Any) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


# --- the store (append-only records; nothing here deletes) ------------------------------------------
class AdaptStore:
    """`<adapter_agents_dir>/_adapt/`:

        <agent>/adaptations.jsonl          every adaptation and every status change (append-only)
        <agent>/baselines/<conn>.json      the approved shape now in force
        <agent>/baselines/<conn>.history.jsonl   every baseline ever in force (append-only)
        <agent>/shapes/<digest>.json       observed shapes an adaptation refers to
        <agent>/holds.json                 connection-level holds (and their history in the log)
        <agent>/state.json                 last check per connection (for health)
        <agent>/notifications.jsonl        list_changed notifications received
        _versions/<conn>/<artifact>/v####.yaml + _versions/versions.jsonl
    """

    def __init__(self, root_fn: Callable[[], Path]) -> None:
        self._root_fn = root_fn
        self.lock = threading.RLock()

    @property
    def root(self) -> Path:
        return self._root_fn()

    def _d(self, agent: str) -> Path:
        return self.root / agent

    # baselines
    def baseline(self, agent: str, conn: str) -> Optional[dict[str, Any]]:
        p = self._d(agent) / "baselines" / f"{conn}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

    def set_baseline(self, agent: str, conn: str, shape: dict[str, Any], *, reason: str,
                     by: str, adaptation_id: Optional[str] = None) -> None:
        with self.lock:
            d = self._d(agent) / "baselines"
            _atomic(d / f"{conn}.json", json.dumps(shape, sort_keys=True, indent=1))
            _append(d / f"{conn}.history.jsonl", {"at": now_iso(), "reason": reason, "by": by,
                                                  "adaptation_id": adaptation_id,
                                                  "digest": "sha256:" + _sha(shape),
                                                  "shape": shape})

    def baseline_history(self, agent: str, conn: str) -> list[dict[str, Any]]:
        return _read_jsonl(self._d(agent) / "baselines" / f"{conn}.history.jsonl")

    def save_shape(self, agent: str, shape: dict[str, Any]) -> str:
        digest = _sha(shape)[:24]
        p = self._d(agent) / "shapes" / f"{digest}.json"
        if not p.is_file():
            _atomic(p, json.dumps(shape, sort_keys=True))
        return digest

    def shape(self, agent: str, digest: str) -> Optional[dict[str, Any]]:
        p = self._d(agent) / "shapes" / f"{digest}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

    # adaptation records
    def append(self, agent: str, rec: dict[str, Any]) -> None:
        with self.lock:
            _append(self._d(agent) / "adaptations.jsonl", rec)

    def records(self, agent: str) -> dict[str, dict[str, Any]]:
        """adaptation id -> the record with every later status line folded in (last wins)."""
        out: dict[str, dict[str, Any]] = {}
        for line in _read_jsonl(self._d(agent) / "adaptations.jsonl", strict=True):
            aid = line.get("adaptation_id")
            if not aid:
                continue
            cur = out.setdefault(aid, {})
            history = cur.get("history", [])
            cur.update({k: v for k, v in line.items() if k != "history"})
            history.append({k: line.get(k) for k in ("status", "at", "by", "note")
                            if line.get(k) is not None})
            cur["history"] = history
        return out

    # connection-level holds
    def holds(self, agent: str) -> dict[str, dict[str, Any]]:
        p = self._d(agent) / "holds.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}

    def set_hold(self, agent: str, conn: str, aid: str, info: Optional[dict[str, Any]]) -> None:
        with self.lock:
            h = self.holds(agent)
            conn_h = h.setdefault(conn, {})
            if info is None:
                conn_h.pop(aid, None)
                if not conn_h:
                    h.pop(conn, None)
            else:
                conn_h[aid] = info
            _atomic(self._d(agent) / "holds.json", json.dumps(h, sort_keys=True, indent=1))

    # health state
    def state(self, agent: str) -> dict[str, Any]:
        p = self._d(agent) / "state.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}

    def set_state(self, agent: str, conn: str, value: dict[str, Any]) -> None:
        with self.lock:
            s = self.state(agent)
            s[conn] = value
            _atomic(self._d(agent) / "state.json", json.dumps(s, sort_keys=True, indent=1))

    def notify(self, agent: str, rec: dict[str, Any]) -> None:
        _append(self._d(agent) / "notifications.jsonl", rec)

    def notifications(self, agent: str) -> list[dict[str, Any]]:
        return _read_jsonl(self._d(agent) / "notifications.jsonl")

    # screening failures (a connection skipped because its shape could not be checked)
    def screening_failed(self, agent: str, rec: dict[str, Any]) -> None:
        _append(self._d(agent) / "screening_failures.jsonl", rec)

    def screening_failures(self, agent: str) -> list[dict[str, Any]]:
        return _read_jsonl(self._d(agent) / "screening_failures.jsonl")

    # config versions (never overwrite without keeping what was in force)
    @property
    def versions_index(self) -> Path:
        return self.root / "_versions" / "versions.jsonl"

    def versions(self, conn: str, artifact: str) -> list[dict[str, Any]]:
        return [r for r in _read_jsonl(self.versions_index, strict=True)
                if r.get("connection") == conn and r.get("artifact") == artifact]

    def add_version(self, conn: str, artifact: str, data: dict[str, Any], *, reason: str,
                    by: str, adaptation_id: Optional[str]) -> dict[str, Any]:
        """Record a new immutable version. Numbers are allocated under a cross-process lock; the
        file is created EXCLUSIVELY (an existing version is never replaced, R-019 #5); a version
        file a crashed writer left unindexed is indexed as `recovered` before a new one is added,
        so its number is never reused."""
        import yaml

        with self.lock, _FileLock(self.root / "_versions" / ".lock"):
            prior = self.versions(conn, artifact)
            d = self.root / "_versions" / conn / artifact
            indexed = {int(r["version"]) for r in prior}
            on_disk = sorted(int(m.group(1)) for f in (d.glob("v*.yaml") if d.is_dir() else [])
                             if (m := re.fullmatch(r"v(\d{4,})\.yaml", f.name)))
            last = prior[-1]["version"] if prior else None
            for k in on_disk:
                if k not in indexed:
                    body = (d / f"v{k:04d}.yaml").read_text(encoding="utf-8")
                    rec = {"connection": conn, "artifact": artifact, "version": k,
                           "path": str(d / f"v{k:04d}.yaml"),
                           "sha256": _sha(yaml.safe_load(body)) if body.strip() else None,
                           "supersedes": last, "recovered": True,
                           "reason": "a version file written but never indexed (the writer "
                                     "stopped between the two); kept, indexed, never reused",
                           "by": "(recovered)", "adaptation_id": None, "at": now_iso()}
                    _append(self.versions_index, rec)
                    prior.append(rec)
                    last = k
            n = max([*indexed, *on_disk, 0]) + 1
            path = d / f"v{n:04d}.yaml"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "x", encoding="utf-8") as fh:            # never overwrites
                fh.write(yaml.safe_dump(data, sort_keys=False))
                fh.flush()
                os.fsync(fh.fileno())
            rec = {"connection": conn, "artifact": artifact, "version": n, "path": str(path),
                   "sha256": _sha(data), "supersedes": last,
                   "reason": reason, "by": by, "adaptation_id": adaptation_id, "at": now_iso()}
            _append(self.versions_index, rec)
            return rec


# --- per-sync working state ------------------------------------------------------------------------
@dataclasses.dataclass
class PreRun:
    connection: str
    policy: Policy
    disabled: Optional[str] = None
    established: bool = False
    current: Any = None                              # drift.SourceShape (probe)
    drifts: list[Any] = dataclasses.field(default_factory=list)
    touched: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)
    problems: list[str] = dataclasses.field(default_factory=list)
    hold_connection: Optional[str] = None
    #: the shape check was due and could not be completed: the connection is NOT ingested this
    #: sync (drift screening fails closed, R-019 #4)
    screening_failed: Optional[str] = None
    #: a governance record this sync owed (a held table's exclusion ledger) was not written
    records_error: Optional[str] = None

    def summary(self) -> dict[str, Any]:
        by: dict[str, list[str]] = {}
        for aid, r in self.touched.items():
            by.setdefault(r.get("status", "?"), []).append(aid)
        return {"checked": self.disabled is None, "disabled": self.disabled,
                "policy": self.policy.as_dict(), "baseline_established": self.established,
                "drifts": len(self.drifts),
                "drift_kinds": sorted({k for d in self.drifts for k in d.kinds}),
                **{s: sorted(ids) for s, ids in by.items()},
                "held_connection": self.hold_connection, "problems": list(self.problems),
                "screening_failed": self.screening_failed, "records_error": self.records_error}


@dataclasses.dataclass
class Proposal:
    artifact: Optional[str] = None                   # mapping | plan | connection | None
    content: Optional[dict[str, Any]] = None
    previous: Optional[dict[str, Any]] = None
    summary: str = ""
    hold: Optional[dict[str, Any]] = None            # {"level": table|table_removed|steps, ...}
    noted: Optional[str] = None                      # nothing to change: what was noted
    notes: list[str] = dataclasses.field(default_factory=list)
    problems: list[str] = dataclasses.field(default_factory=list)


class _AttrDraft(__import__("pydantic").BaseModel):
    attribute: Optional[str] = None
    rationale: str = ""


def attr_name_for(column: str, *, harness: Any = None, table: str = "",
                  taken: tuple[str, ...] = ()) -> tuple[Optional[str], str, str]:
    """(attribute name, mode, why) for a new column. RULES first: a column already named in the
    attribute grammar is its own attribute (the mapping proposer's rule); otherwise its
    normalised form. The MODEL only when no rule yields a valid name, and its answer is validated
    against the same grammar and against names already taken — never trusted as given."""
    if _ATTR.match(column) and column not in taken:
        return column, "rules", "attribute named as its column (the mapping rule)"
    norm = re.sub(r"[^a-z0-9]+", "_", column.lower()).strip("_")
    if norm and _ATTR.match(norm) and norm not in taken:
        return norm, "rules", f"column {column!r} normalised to attribute {norm!r}"
    if harness is None:
        return None, "rules", f"no rule names column {column!r} and no model is configured"
    system = {"role": "system", "content": (
        "Name ONE graph attribute for a new database column. Answer a snake_case name "
        "(^[a-z][a-z0-9_]*$) describing what the column holds, or null if you cannot tell. "
        "Never reuse a name listed as taken.")}
    user = {"role": "user", "content": json.dumps({"table": table, "column": column,
                                                   "taken": sorted(taken)}, sort_keys=True)}
    try:
        draft = harness.complete_structured(ATTR_TASK_CLASS, [system, user], _AttrDraft,
                                            seed=7).parsed
    except Exception as exc:                                         # noqa: BLE001
        return None, "model", f"the model could not name {column!r} ({type(exc).__name__})"
    name = (draft.attribute or "").strip()
    if not _ATTR.match(name) or name in taken:
        return None, "model", (f"the model proposed {name!r} for {column!r}, which is not a valid "
                               f"unused attribute name (refused)")
    return name, "model", f"model: {draft.rationale or 'attribute named'}"


def _concept(ref: str) -> str:
    """A table name reduced to the concept it names (`shipments_2025` -> `shipment`)."""
    t = ref.rsplit(".", 1)[-1].lower()
    t = re.sub(r"(_\d+)+$", "", t)
    t = re.sub(r"[^a-z0-9_]+", "_", t).strip("_")
    if t.endswith("ies"):
        t = t[:-3] + "y"
    elif t.endswith(("ses", "xes", "ches", "shes")):
        t = t[:-2]
    elif t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    return t


# --- the engine -------------------------------------------------------------------------------------
class AdaptationEngine:
    def __init__(self, *, root_fn: Callable[[], Path], service: Any,
                 harness_fn: Callable[[], Any] = lambda: None,
                 clock: Optional[Callable[[], dt.datetime]] = None,
                 digest_fn: Callable[[Any], str] = lambda spec: "",
                 seal_fn: Callable[[Any], tuple[Optional[dict[str, Any]], Optional[str]]] = (
                     lambda spec: (None, "no delegation log is wired")),
                 seal_log_fn: Callable[[Any], Optional[Path]] = lambda spec: None) -> None:
        self.store = AdaptStore(root_fn)
        #: spec -> (the SEALED delegation record in force, None) or (None, why there is none):
        #: AA3. With no sealed record nothing is delegated and every change is held for a person.
        self._seal = seal_fn
        self._seal_log = seal_log_fn
        self.service = service
        self._harness_fn = harness_fn
        self._clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self._digest = digest_fn

    # --- capability ------------------------------------------------------------------------------
    _NEEDS = ("probe_shape", "run_shape", "read_config", "write_config")

    def unavailable(self) -> Optional[str]:
        missing = [m for m in self._NEEDS if not callable(getattr(self.service, m, None))]
        if missing:
            return (f"the ingest service offers no shape probe ({', '.join(missing)}); drift is "
                    f"not checked for this service")
        return None

    def _drift(self):
        from databridge_mcp_ingest import drift

        return drift

    def _now(self) -> str:
        return now_iso(self._clock())

    def _vocab(self) -> Any:
        """The vocabulary, for PROPOSING (a new table's kind). Subject screening goes through
        `_vocab_checked`, which holds rather than guess."""
        return self._vocab_checked()[0]

    def _vocab_checked(self) -> tuple[Any, Optional[str]]:
        """(vocabulary, problem). Subject screening NEEDS it: a missing or failing vocabulary is a
        problem the caller holds on, never "no subject here" (R-019 #8)."""
        fn = getattr(self.service, "vocabulary", None)
        if not callable(fn):
            return None, "the ingest service offers no vocabulary, so subjects cannot be screened"
        try:
            vocab = fn()
        except Exception as exc:                                      # noqa: BLE001
            return None, (f"the vocabulary could not be loaded ({type(exc).__name__}: {exc}), so "
                          f"subjects cannot be screened")
        if vocab is None:
            return None, "no vocabulary is active, so subjects cannot be screened"
        return vocab, None

    def _svc(self, name: str, *args, **kw) -> Any:
        fn = getattr(self.service, name, None)
        if not callable(fn):
            return None
        return fn(*args, **kw)

    def _holds_honoured(self) -> bool:
        try:
            return bool(self._svc("holds_honoured"))
        except Exception:                                             # noqa: BLE001
            return False

    # --- ids / records ---------------------------------------------------------------------------
    @staticmethod
    def adaptation_id(agent: str, conn: str, unit: str, drift_ids: list[str]) -> str:
        return "ad_" + _sha([agent, conn, unit, sorted(drift_ids)])[:16]

    def records(self, agent: str) -> dict[str, dict[str, Any]]:
        return self.store.records(agent)

    def _status(self, agent: str, aid: str, status: str, *, by: Any = None,
                note: Optional[str] = None, **extra: Any) -> dict[str, Any]:
        line = {"adaptation_id": aid, "status": status, "at": self._now(), "by": by,
                "note": note, **extra}
        self.store.append(agent, line)
        return self.records(agent)[aid]

    def pending(self, agent: str) -> list[dict[str, Any]]:
        return sorted((r for r in self.records(agent).values() if r.get("status") == "pending"),
                      key=lambda r: (r.get("created_at") or "", r["adaptation_id"]))

    def connection_hold(self, agent: str, conn: str) -> Optional[str]:
        h = self.store.holds(agent).get(conn) or {}
        if not h:
            return None
        parts = [f"{aid} ({v.get('why')})" for aid, v in sorted(h.items())]
        return (f"held: the source's shape changed and {len(h)} adaptation(s) await a person's "
                f"decision — {'; '.join(parts)}. Approve or reject with approve_adaptation / "
                f"reject_adaptation; everything else keeps syncing")

    # --- context ---------------------------------------------------------------------------------
    def _ctx(self, conn: str) -> dict[str, Any]:
        ctx: dict[str, Any] = {"mapping": None, "connection": None, "plan_tools": {}}
        for art in ("mapping", "connection"):
            try:
                ctx[art] = self._svc("read_config", conn, art)
            except Exception as exc:                                  # noqa: BLE001
                ctx.setdefault("problems", []).append(f"{art}: {exc}")
        try:
            ctx["plan_tools"] = self._svc("plan_tools", conn) or {}
        except Exception as exc:                                      # noqa: BLE001
            ctx.setdefault("problems", []).append(f"plan: {exc}")
        return ctx

    @staticmethod
    def _table_kinds(ctx: dict[str, Any]) -> dict[str, str]:
        out = {}
        for ref, e in ((ctx.get("mapping") or {}).get("tables") or {}).items():
            kind = ((e or {}).get("node") or {}).get("kind")
            if kind:
                out[ref] = kind
        return out

    # --- before the run --------------------------------------------------------------------------
    def before_run(self, spec: Any, conn: str, *, run_id: str,
                   trigger: Optional[dict[str, Any]] = None) -> PreRun:
        """Screen the connection's shape before it is ingested. Any failure to complete the screen
        sets `screening_failed`, records it, and the manager does NOT ingest: a probe, baseline,
        vocabulary or store failure is never permission to proceed (R-019 #4, #8)."""
        pol = Policy.of(spec)
        pre = PreRun(connection=conn, policy=pol)
        why = self.unavailable()
        if why:
            pre.disabled = why
            return pre
        ttype = (trigger or {}).get("type")
        if not pol.on_sync and ttype != "list_changed":
            pre.disabled = "adapt.watch.on_sync is off: the shape is not checked on a sync"
            pre.hold_connection = self.connection_hold(spec.id, conn)
            return pre
        try:
            self._screen(spec, conn, pre, run_id=run_id)
        except Exception as exc:                                      # noqa: BLE001
            self._fail_screening(spec, conn, pre, f"{type(exc).__name__}: {exc}")
        return pre

    def _fail_screening(self, spec: Any, conn: str, pre: PreRun, why: str) -> None:
        pre.screening_failed = (f"the shape check could not be completed ({why}); the connection "
                                f"is not ingested until it can be - an unchecked change could be "
                                f"a write tool or a subject")
        pre.disabled = pre.screening_failed
        pre.problems.append(pre.screening_failed)
        rec = {"at": self._now(), "connection": conn, "why": why}
        for step in (lambda: self.store.screening_failed(spec.id, rec),
                     lambda: self._check_state(spec, conn, pre)):
            try:
                step()
            except Exception as exc:                                  # noqa: BLE001
                pre.problems.append(f"could not record the screening failure: "
                                    f"{type(exc).__name__}: {exc}")
        try:
            pre.hold_connection = self.connection_hold(spec.id, conn)
        except Exception:                                             # noqa: BLE001
            pass

    def _first_baseline(self, spec: Any, conn: str, current: Any) -> Any:
        """The baseline a FIRST observation establishes: the live shape minus every write tool the
        approved crawl plan did not list (`tools_seen`) - so a write tool that appeared after the
        plan was approved is a drift that is asked, never silently adopted (R-019 #7). A plan that
        cannot be read, or lists no tools, vouches for none."""
        plan = self._svc("read_config", conn, "plan") or {}
        seen = set(plan.get("tools_seen") or [])
        tools = {n: t for n, t in (current.tools or {}).items() if not t.writes or n in seen}
        return current.model_copy(update={"tools": tools}, deep=True)

    def _screen(self, spec: Any, conn: str, pre: PreRun, *, run_id: str) -> None:
        drift = self._drift()
        try:
            current = self.service.probe_shape(conn)
        except Exception as exc:                                      # noqa: BLE001
            return self._fail_screening(spec, conn, pre, f"the shape probe failed: "
                                                         f"{type(exc).__name__}: {exc}")
        pre.current = current
        pre.problems.extend(current.problems)
        if current.tools is None or current.problems:
            return self._fail_screening(
                spec, conn, pre, "the probe did not observe the whole shape: "
                + ("; ".join(current.problems) or "the tool list was not observed"))
        base_raw = self.store.baseline(spec.id, conn)
        if base_raw is None:
            first = self._first_baseline(spec, conn, current)
            self.store.set_baseline(spec.id, conn, first.model_dump(mode="json"),
                                    reason="established at the first sync after the plan was "
                                           "approved (write tools the plan did not list are left "
                                           "out, so they are asked)",
                                    by=f"adapter_agent:{spec.id}")
            pre.established = True
            base_raw = self.store.baseline(spec.id, conn)
        baseline = drift.SourceShape.model_validate(base_raw)
        drifts = drift.diff(baseline, current)
        ctx = self._ctx(conn)
        ctx["current"] = current
        if drifts:
            vocab, vproblem = self._vocab_checked()
            if vproblem is not None:
                return self._fail_screening(spec, conn, pre, vproblem)
            drift.mark_subject(drifts, vocab=vocab, table_kinds=self._table_kinds(ctx))
        pre.drifts = drifts
        self._reconcile(spec, conn, drifts, ctx, pre, current)
        self._handle_all(spec, conn, drifts, ctx, pre, run_id=run_id, stage="pre")
        pre.hold_connection = self.connection_hold(spec.id, conn)
        self._check_state(spec, conn, pre)

    def _standing(self, agent: str, conn: str, *, exclude: Optional[str] = None
                  ) -> dict[str, str]:
        """drift id -> the adaptation whose REJECTION of it still stands, on this connection."""
        out: dict[str, str] = {}
        for aid, r in sorted(self.records(agent).items()):
            if aid == exclude or r.get("connection") != conn or not rejection_stands(r):
                continue
            for i in r.get("drift_ids") or []:
                out.setdefault(str(i), aid)
        return out

    def _handle_all(self, spec: Any, conn: str, drifts: list[Any], ctx: dict[str, Any],
                    pre: PreRun, *, run_id: str, stage: str) -> None:
        """File the drifts by unit, EXCLUDING every drift whose rejection still stands (R-025,
        Q-09): a rejection is final for that exact change, so it is never re-bundled with a new
        change of the same unit (approving the new one would approve it). If the source changes
        it again, that is a different drift (a new id) and is asked on its own. The rejected
        adaptation keeps its hold."""
        standing = self._standing(spec.id, conn)
        rejected = sorted({standing[d.drift_id] for d in drifts if d.drift_id in standing})
        recs = self.records(spec.id) if rejected else {}
        for aid in rejected:
            r = recs.get(aid) or {}
            if r.get("status") == "rejected":
                self._install_hold(spec, conn, r, ctx)                   # idempotent
            pre.touched[aid] = self.records(spec.id).get(aid, r)
        fresh = [d for d in drifts if d.drift_id not in standing]
        for unit, group in self._groups(fresh):
            self._handle(spec, conn, unit, group, ctx, pre, run_id=run_id, stage=stage)

    @staticmethod
    def _groups(drifts: list[Any]) -> list[tuple[str, list[Any]]]:
        groups: dict[str, list[Any]] = {}
        for d in drifts:
            groups.setdefault(d.unit, []).append(d)
        return sorted(groups.items())

    def _check_state(self, spec: Any, conn: str, pre: PreRun) -> None:
        self.store.set_state(spec.id, conn, {"checked_at": self._now(),
                                             "disabled": pre.disabled,
                                             "screening_failed": pre.screening_failed,
                                             "drifts": len(pre.drifts),
                                             "problems": pre.problems[:5]})

    # --- reconcile open adaptations against what the source looks like NOW ------------------------
    def _reconcile(self, spec: Any, conn: str, drifts: list[Any], ctx: dict[str, Any],
                   pre: PreRun, current: Any) -> None:
        live_units = {u for d in drifts for u in d.units()}
        live_ids = {d.drift_id for d in drifts}
        mapping_tables = (ctx.get("mapping") or {}).get("tables") or {}
        for aid, r in self.records(spec.id).items():
            if r.get("connection") != conn or r.get("stage") == "post":
                continue                                  # file facets are checked after the run
            ids = set(r.get("drift_ids") or [])
            if r.get("status") == "superseded" and rejection_stands(r):
                # a rejection an older sync re-bundled (Q-09): it stands until the source no
                # longer shows its drifts; then it is lifted, so a later change is asked afresh
                if ids and not ids & live_ids:
                    self._status(spec.id, aid, "superseded", by=f"adapter_agent:{spec.id}",
                                 note=f"{_REVERTED}; the rejection no longer applies",
                                 rejection_lifted=True)
                continue
            if r.get("status") not in ("pending", "rejected"):
                continue
            hold = r.get("hold") or {}
            if hold.get("via") == "mapping_status" and r.get("status") in ("pending", "rejected"):
                entry = mapping_tables.get(hold.get("table") or "") or {}
                if entry.get("status") == "approved":
                    # NOT an approval (R-019 #3): a mapping file's `status`/`approved_by` is
                    # mutable configuration carrying a typed name. Only approve_adaptation, with
                    # an authenticated principal, decides an adaptation. The hold is put back
                    # and the attempt is recorded (the status is unchanged).
                    self._status(spec.id, aid, r["status"], by=f"adapter_agent:{spec.id}",
                                 note=(f"the held table's mapping was marked approved outside "
                                       f"approve_adaptation (approved_by "
                                       f"{(ctx.get('mapping') or {}).get('approved_by')!r}, a "
                                       f"name in a config file); that is not an authenticated "
                                       f"decision on this adaptation, so the hold is re-installed"),
                                 rehold=True)
                    self._install_hold(spec, conn, self.records(spec.id)[aid], ctx)
                    ctx["mapping"] = self._svc("read_config", conn, "mapping")
                    mapping_tables = (ctx.get("mapping") or {}).get("tables") or {}
            # withdrawn when the source no longer shows ANY of its drifts (R-025: by drift, not by
            # unit, so a new change of the same table neither keeps a reverted rejection alive
            # nor withdraws a rejection that still applies)
            gone = (not ids & live_ids) if ids else \
                not set(r.get("units") or [r.get("unit")]) & live_units
            if gone:
                # the source no longer differs from the approved baseline there: withdraw
                rejected = r.get("status") == "rejected"
                self._restore_previous(spec, conn, r, status="approved")
                self._release_and_rehold(spec, conn, r)
                pre.touched[aid] = self._status(
                    spec.id, aid, "superseded", by=f"adapter_agent:{spec.id}",
                    note=f"{_REVERTED} (the change was reverted, or changed again); the hold is "
                         f"released",
                    **({"rejection_lifted": True} if rejected else {}))

    # --- one unit ------------------------------------------------------------------------------
    def _handle(self, spec: Any, conn: str, unit: str, drifts: list[Any], ctx: dict[str, Any],
                pre: PreRun, *, run_id: str, stage: str) -> None:
        agent = spec.id
        ids = sorted(d.drift_id for d in drifts)
        aid = self.adaptation_id(agent, conn, unit, ids)
        known = self.records(agent)
        n = 1
        while (known.get(aid) or {}).get("status") == "superseded":
            # the same drift observed AGAIN after its adaptation was withdrawn: a new adaptation,
            # so a rejected (then reverted) record is never re-opened under its old id
            n += 1
            aid = f"{self.adaptation_id(agent, conn, unit, ids)}.{n}"
        existing = known.get(aid)
        if existing is not None and existing.get("status") != "superseded":
            if existing.get("status") in ("pending", "rejected"):
                self._install_hold(spec, conn, existing, ctx)       # idempotent
            pre.touched[aid] = existing
            return
        for other_id, other in known.items():                # a newer change of the same unit
            # only an UNDECIDED record is superseded (its drifts are re-filed in this one). A
            # REJECTED one never is: its drifts are excluded from this group (`_handle_all`) and
            # its rejection stays final (R-025, Q-09)
            if other_id != aid and other.get("connection") == conn and \
                    other.get("unit") == unit and other.get("status") in ("pending", "reported"):
                self._restore_previous(spec, conn, other, status="approved")
                self._release_and_rehold(spec, conn, other)
                self._status(agent, other_id, "superseded", by=f"adapter_agent:{agent}",
                             note=f"the source changed again; superseded by {aid}")
        rh = RecordingHarness(self._harness_fn()) if self._harness_fn() is not None else None
        pol = pre.policy
        # a proposal is built under EVERY policy: it is what a person is shown, and proposing is
        # also how a new table is found to map to a subject kind. Under `off` it is never applied.
        try:
            proposal = self._propose(spec, conn, unit, drifts, ctx, rh)
        except Exception as exc:                                      # noqa: BLE001
            proposal = Proposal(summary=f"no proposal could be built ({type(exc).__name__}: "
                                        f"{exc}); a person must decide",
                                problems=[f"{type(exc).__name__}: {exc}"])
        decisions = {d.drift_id: dict(zip(("asked", "why"), pol.asks(d))) for d in drifts}
        for d in drifts:
            decisions[d.drift_id]["class"] = ("additive" if d.kind in ADDITIVE and
                                              not d.subject_touching else "breaking")
        if pol.policy != "off" and proposal.artifact == "connection":
            # widening the DECLARED scope (a new file type into scope.file_types) is a person's
            # decision under every asking policy: the scope is what a person approved to be read
            # (E2E F-24). Additive changes within scope (an added column) stay automatic.
            for d in drifts:
                if d.kind in ("new_file_type", "new_mime"):
                    decisions[d.drift_id].update(asked=True, why=(
                        "widens the connection's declared scope (scope.file_types): always "
                        "asked"))
        asked = any(v["asked"] for v in decisions.values())
        if pol.policy != "off":
            asked = asked or bool(proposal.problems) or (
                proposal.artifact == "plan" and bool((proposal.content or {}).get("problems")))
        if pol.policy == "off" and not asked:
            proposal = Proposal(summary="detected and reported only (adapt.policy: off): "
                                        + "; ".join(d.detail for d in drifts),
                                notes=proposal.notes)
        observed = self.store.save_shape(agent, pre.current.model_dump(mode="json")) \
            if pre.current is not None else None
        calls = rh.calls if rh is not None else []
        rec = {"adaptation_id": aid, "agent_id": agent, "connection": conn, "unit": unit,
               "units": sorted({u for d in drifts for u in d.units()}), "stage": stage,
               "drift_ids": ids, "changes_digest": changes_digest_of(ids),
               "drifts": [d.model_dump(mode="json") for d in drifts],
               "classification": decisions, "action": proposal.summary or proposal.noted or "",
               "notes": proposal.notes, "problems": proposal.problems,
               "proposal": {"artifact": proposal.artifact, "content": proposal.content,
                            "previous": proposal.previous},
               "hold": None, "observed_shape": observed,
               "evidence": {"baseline": self._baseline_digest(agent, conn),
                            "observed": observed, "drifts": [d.detail for d in drifts]},
               "produced_by": self._produced_by(spec, run_id, calls),
               "created_at": self._now(), "agent_run_id": run_id, "at": self._now()}
        if pol.policy != "off" and not asked and proposal.artifact and \
                proposal.content is not None:
            # The auto path is a DELEGATED approval (INT3). What it cannot cover is held for a
            # person, with the reason, exactly as an asked change is.
            refused, seal = self._delegation_refusal(spec, proposal)
            if refused is None:
                delegation = _mint_delegation(spec, seal=seal, seal_log=self._seal_log(spec),
                                              adaptation_id=aid)
                try:
                    rec["version"] = self._apply(
                        spec, conn, proposal, aid, principal=delegation,
                        by=f"adapter_agent:{agent} (adapt.policy {pol.policy}, delegated by "
                           f"{delegation.delegated_by})")
                    rec["delegation"] = delegation_of(delegation, "auto_additive")
                except AdaptationRefused as exc:
                    refused = str(exc)
            if refused is not None:
                asked = True
                for v in decisions.values():
                    v.update(asked=True, why=f"not covered by the delegated approval: {refused}")
        if pol.policy == "off" and not asked:
            rec["status"] = "reported"
            self.store.append(agent, rec)
        elif not asked:
            rec["status"] = "applied" if "version" in rec else "noted"
            self.store.append(agent, rec)
        else:
            rec["status"] = "pending"
            rec["hold"] = self._hold_plan(conn, proposal, ctx)
            self.store.append(agent, rec)
            self._install_hold(spec, conn, rec, ctx)
        pre.touched[aid] = self.records(agent)[aid]

    def _baseline_digest(self, agent: str, conn: str) -> Optional[str]:
        b = self.store.baseline(agent, conn)
        return "sha256:" + _sha(b) if b is not None else None

    def _produced_by(self, spec: Any, run_id: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
        def distinct(key: str) -> Any:
            vals = sorted({str(c[key]) for c in calls if c.get(key) is not None})
            return None if not vals else (vals[0] if len(vals) == 1 else vals)

        # §5.8: a deterministic adaptation records `agent: rules, model: null`
        return {"agent": f"adapter_agent:{spec.id}" if calls else "rules",
                "on_behalf_of": f"adapter_agent:{spec.id}",
                "agent_version": self._digest(spec) or None,
                "task_class": distinct("task_class"), "model": distinct("model"),
                "prompt_version": None, "run_id": run_id, "at": self._now(),
                "mode": "model" if calls else "rules", "model_calls": len(calls)}

    # --- proposals ---------------------------------------------------------------------------------
    def _propose(self, spec: Any, conn: str, unit: str, drifts: list[Any], ctx: dict[str, Any],
                 rh: Any) -> Proposal:
        prefix = unit.split(":", 1)[0]
        if prefix == "table":
            return self._propose_table(conn, unit.split(":", 1)[1], drifts, ctx, rh)
        if prefix in ("tool", "resource_template"):
            return self._propose_tool(conn, prefix, drifts, ctx)
        if prefix == "item_field":
            return self._propose_field(conn, drifts, ctx)
        if prefix == "file_type":
            return self._propose_file_type(conn, drifts, ctx)
        return Proposal(noted="; ".join(d.detail for d in drifts) + " — noted")

    def _propose_table(self, conn: str, ref: str, drifts: list[Any], ctx: dict[str, Any],
                       rh: Any) -> Proposal:
        mapping = ctx.get("mapping") or {}
        entry = (mapping.get("tables") or {}).get(ref)
        kinds = {d.kind for d in drifts}
        if "new_table" in kinds:
            vocab = self._vocab()
            props = self._svc("propose_table_mappings", conn, [ref], vocab=vocab,
                              harness=rh) or {}
            e = self._with_source_fks(ref, props.get(ref) or {}, ctx, vocab)
            if e.get("status") == "unmapped" or not (e.get("node") or e.get("association")):
                return Proposal(noted=(f"new table {ref!r}: no kind in the vocabulary maps it "
                                       f"({'; '.join(e.get('rationale') or [])}); its rows are "
                                       f"captured and counted, not graphed — recorded as an "
                                       f"unmapped concept"))
            is_subject = getattr(vocab, "is_subject", None) if vocab is not None else None
            if e.get("association"):
                # a JOIN table (by structure): no node; each row is an edge between the two rows
                # it references. It touches a subject when either end is a subject kind.
                ends = sorted({str(x).split(":", 1)[0] for edge in e.get("edges") or []
                               for x in (edge.get("src"), edge.get("dst")) if x})
                touched = [k for k in ends if callable(is_subject) and is_subject(k)]
                if touched:
                    for d in drifts:
                        d.subject_touching = True
                        d.subject_reason = (f"new join table {ref!r} links subject kind(s) "
                                            f"{touched}")
                return Proposal(artifact="mapping", content={"table": ref, "entry": e},
                                previous=None,
                                summary=(f"map new join table {ref!r} to edge(s) "
                                         f"{[x.get('kind') for x in e.get('edges') or []]} "
                                         f"between {ends} ({e.get('origin')}): "
                                         f"{'; '.join(e.get('rationale') or [])}"),
                                hold={"level": "table", "table": ref, "new": True})
            kind = e["node"]["kind"]
            if callable(is_subject) and is_subject(kind):
                for d in drifts:
                    d.subject_touching = True
                    d.subject_reason = f"new table {ref!r} maps to subject kind {kind!r}"
            return Proposal(artifact="mapping", content={"table": ref, "entry": e},
                            previous=None,
                            summary=(f"map new table {ref!r} to kind {kind!r} "
                                     f"({e.get('origin')}): {'; '.join(e.get('rationale') or [])}"),
                            hold={"level": "table", "table": ref, "new": True})
        table_gone = [d for d in drifts if d.kind == "removed" and d.column is None]
        if table_gone:
            return Proposal(summary=(f"table {ref!r} is no longer listed; approving lets the next "
                                     f"sync close its graph rows (bi-temporal: closed, never "
                                     f"deleted)"), hold={"level": "table_removed", "table": ref})
        if not entry or entry.get("status") != "approved" or not entry.get("node"):
            status = (entry or {}).get("status", "not in the mapping")
            return Proposal(noted=(f"table {ref!r} is not built into the graph (mapping "
                                   f"{status}); its shape change needs no mapping change: "
                                   + "; ".join(d.detail for d in drifts)))
        new_entry, notes = self._evolve(conn, ref, entry, drifts, rh, ctx)
        return Proposal(artifact="mapping", content={"table": ref, "entry": new_entry},
                        previous=copy.deepcopy(entry),
                        summary=f"{ref}: " + "; ".join(d.detail for d in drifts),
                        notes=notes, hold={"level": "table", "table": ref})

    def _evolve(self, conn: str, ref: str, entry: dict[str, Any], drifts: list[Any],
                rh: Any, ctx: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """The table's mapping entry, evolved for the drifts. Human decisions are kept; only what
        the drift names changes."""
        from databridge_mcp_ingest.build import FK_PREFIX, ROW_KEY

        e = copy.deepcopy(entry)
        node = e.setdefault("node", {})
        attrs: dict[str, str] = dict(node.get("attrs") or {})
        notes: list[str] = []
        for d in drifts:
            if d.kind == "renamed":
                old, new = str(d.old), str(d.new)
                attrs = {k: (new if v == old else v) for k, v in attrs.items()}
                node["label"] = str(node.get("label") or "").replace("{" + old + "}",
                                                                     "{" + new + "}")
                node["sources"] = [new if s == old else s for s in node.get("sources") or []]
                if node.get("canonical_id") == old:
                    node["canonical_id"] = new
                for edge in e.get("edges") or []:
                    edge["evidence"] = [new if x == old else x for x in edge.get("evidence") or []]
                    for end in ("src", "dst"):
                        edge[end] = str(edge.get(end) or "").replace(
                            "{" + FK_PREFIX + old + "}", "{" + FK_PREFIX + new + "}")
                    if str(edge.get("fk") or "").startswith(old + " "):
                        edge["fk"] = new + str(edge["fk"])[len(old):]
                notes.append(f"{old!r} -> {new!r}: attribute names kept, the column they read "
                             f"from renamed (candidate, confidence {d.confidence})")
            elif d.kind == "removed" and d.column:
                c = str(d.column)
                dropped = sorted(k for k, v in attrs.items() if v == c)
                attrs = {k: v for k, v in attrs.items() if v != c}
                if "{" + c + "}" in str(node.get("label") or ""):
                    node["label"] = "{" + ROW_KEY + "}"
                    notes.append(f"label used {c!r}; it falls back to the row identity")
                if node.get("canonical_id") == c:
                    node.pop("canonical_id", None)
                    notes.append(f"canonical_id {c!r} removed: rows merge by row key only")
                kept = [x for x in e.get("edges") or [] if c not in (x.get("evidence") or [])]
                if len(kept) != len(e.get("edges") or []):
                    notes.append(f"edge(s) evidenced by {c!r} dropped")
                e["edges"] = kept
                if dropped:
                    notes.append(f"attribute(s) {dropped} no longer read (column {c!r} is gone)")
            elif d.kind == "added_column":
                c = str(d.column)
                fk = (d.new or {}).get("foreign_key") if isinstance(d.new, dict) else None
                if fk:
                    edges = self._fk_edges(conn, ref, c, node.get("kind"), ctx)
                    if edges:
                        e.setdefault("edges", []).extend(edges)
                        notes.append(f"new foreign key {c!r}: edge(s) "
                                     f"{[x['kind'] for x in edges]} by rule")
                    else:
                        notes.append(f"new foreign key {c!r}: no declared edge kind admits it "
                                     f"(not mapped; its value stays in the facts store)")
                    # C-1: the reference value is kept on the node as well, as an ingest sync
                    # keeps it, so it is never silently dropped where no edge could be built
                    if c.lower() not in ("label", "canonical_id") and c not in attrs \
                            and c not in attrs.values():
                        attrs[c] = c
                    continue
                if c.lower() in ("label", "canonical_id"):
                    notes.append(f"column {c!r} is an identity field name; not mapped")
                    continue
                if len(attrs) >= 25:
                    notes.append(f"column {c!r} not mapped: the table already maps 25 attributes")
                    continue
                name, mode, why = attr_name_for(c, harness=rh, table=ref,
                                                taken=tuple(attrs))
                if name is None:
                    notes.append(why)
                    continue
                attrs[name] = c
                notes.append(f"new column {c!r} -> attribute {name!r} ({mode}: {why})")
            elif d.kind == "type_changed":
                notes.append(f"{d.detail}: the mapping reads the column as before")
        node["attrs"] = attrs
        e["rationale"] = list(e.get("rationale") or []) + [f"adapted: {n}" for n in notes]
        return self._evolve_schema(e, drifts), notes

    @staticmethod
    def _evolve_schema(e: dict[str, Any], drifts: list[Any]) -> dict[str, Any]:
        """The entry's schema snapshot (`columns` / `fk_columns`, R-022 f2 #9) moves with the
        drifts it applies: an added column is added (and to `fk_columns` when it is a foreign
        key), a renamed column is renamed in both, a removed column is dropped from both. mcp_ingest
        refuses an entry that reads a column its snapshot lacks, and refuses a delegated change
        whose snapshot loses a column, so a stale snapshot would hold every adaptation. A legacy
        entry with no snapshot (`columns` absent) is left as it is: mcp_ingest back-fills it."""
        if e.get("columns") is None:
            return e
        cols = [str(c) for c in e.get("columns") or []]
        fks = [str(c) for c in e.get("fk_columns") or []]
        for d in drifts:
            if d.kind == "added_column" and d.column:
                c = str(d.column)
                if c not in cols:
                    cols.append(c)
                new = d.new if isinstance(d.new, dict) else {}
                if new.get("foreign_key") and c not in fks:
                    fks.append(c)
            elif d.kind == "renamed" and d.old is not None and d.new is not None:
                old, new_name = str(d.old), str(d.new)
                cols = [new_name if c == old else c for c in cols]
                fks = [new_name if c == old else c for c in fks]
            elif d.kind == "removed" and d.column:
                c = str(d.column)
                cols = [x for x in cols if x != c]
                fks = [x for x in fks if x != c]
        e["columns"], e["fk_columns"] = cols, sorted(set(fks))
        return e

    @staticmethod
    def _snapshot(ctx: dict[str, Any], ref: str) -> Optional[dict[str, Any]]:
        """The table as the shape probe sees it NOW, in mcp_ingest's snapshot form (no rows: the
        foreign-key rules read the schema only)."""
        cur = ctx.get("current")
        table = cur.tables.get(ref) if cur is not None and cur.tables else None
        if table is None:
            return None
        schema, _, name = ref.rpartition(".")
        return {"ref": ref, "schema": schema, "table": name,
                "columns": [{"name": c.name, "type": c.type} for c in table.columns.values()],
                "primary_key": list(table.primary_key or []),
                "foreign_keys": [dict(f) for f in table.foreign_keys or []], "rows": []}

    def _with_source_fks(self, ref: str, entry: dict[str, Any], ctx: dict[str, Any],
                         vocab: Any) -> dict[str, Any]:
        """A new table's proposed entry with its declared foreign keys (or, for a join table, its
        two ends) resolved against the kinds the source's mapping ALREADY gives the referenced
        tables — `mapping.resolve_pending_fks`, the rule an ingest sync uses (C-1 / FIN-06). A
        reference into a table the model mapped, whose name no rule maps, still gets its edge.
        The entry comes back unchanged when nothing more resolves."""
        if not entry or vocab is None:
            return entry
        snap = self._snapshot(ctx, ref)
        if snap is None:
            return entry
        from databridge_mcp_ingest import mapping as mi

        try:
            mine = mi.TableMapping.model_validate(entry)
            known = {t: mi.TableMapping.model_validate(e)
                     for t, e in ((ctx.get("mapping") or {}).get("tables") or {}).items()
                     if t != ref and e}
            got = mi.resolve_pending_fks({**known, ref: mine}, {ref: snap}, vocab)
        except Exception:                                             # noqa: BLE001
            return entry
        return got[ref].model_dump(mode="json") if ref in got else entry

    def _fk_edges(self, conn: str, ref: str, column: str, kind: Optional[str],
                  ctx: dict[str, Any]) -> list[dict[str, Any]]:
        """The edge a new foreign-key column gets, by the SAME rule an ingest sync uses
        (`mapping.fk_edges`): the referenced table's kind comes from the source's mapping —
        whoever mapped it (rules, model or a person) — and the edge kind from the joint ranking
        (FIN-05), for the kind a person approved for this table (a human decision is never
        replaced). A reference to a non-key column is resolved at build time from the endpoint,
        exactly as for any declared key."""
        snap = self._snapshot(ctx, ref)
        if snap is None or not kind:
            return []
        snap["foreign_keys"] = [f for f in snap["foreign_keys"] if f.get("column") == column]
        if not snap["foreign_keys"]:
            return []
        from databridge_mcp_ingest.mapping import fk_edges

        try:
            edges, _ = fk_edges(snap, kind, self._table_kinds(ctx), self._vocab())
        except Exception:                                             # noqa: BLE001
            return []
        return [x for x in edges if x.get("evidence") == [column]]

    def _propose_tool(self, conn: str, prefix: str, drifts: list[Any], ctx: dict[str, Any]
                      ) -> Proposal:
        kinds = {d.kind for d in drifts}
        name = drifts[0].tool or str(drifts[0].new or drifts[0].old)
        if kinds <= {"tool_added"}:
            return Proposal(noted=f"new read-only {prefix.replace('_', ' ')} {name!r} noted; it "
                                  f"is not used until a crawl plan needs it")
        steps = [s for s, tools in (ctx.get("plan_tools") or {}).items()
                 if not s.endswith("#fields") and name in tools]
        if steps and kinds & {"tool_removed", "tool_signature_changed"}:
            try:
                plan = self._svc("propose_plan_version", conn)
            except Exception as exc:                                  # noqa: BLE001
                plan, err = None, f"{type(exc).__name__}: {exc}"
            else:
                err = None
            summary = (f"tool {name!r}, used by plan step(s) {steps}, "
                       + ("is gone" if "tool_removed" in kinds else "changed its arguments")
                       + "; a re-proposed plan (rules, from the live tool list) is filed")
            problems = [err] if err else []
            if plan is not None and plan.get("problems"):
                summary += f" — it has problems and cannot be approved as is: {plan['problems']}"
            return Proposal(artifact="plan" if plan else None, content=plan, summary=summary,
                            problems=problems, hold={"level": "steps", "steps": steps})
        if "new_write_tool" in kinds:
            return Proposal(summary=(f"{name!r} can change the source. A crawl never calls it "
                                     f"(ReadOnlyClient refuses write tools); approving records "
                                     f"that a person has seen it"))
        return Proposal(summary=(f"{prefix.replace('_', ' ')} {name!r}: "
                                 + "; ".join(d.detail for d in drifts)
                                 + " — the approved plan does not use it"))

    def _propose_field(self, conn: str, drifts: list[Any], ctx: dict[str, Any]) -> Proposal:
        fields = {s.split("#", 1)[0]: set(v) for s, v in (ctx.get("plan_tools") or {}).items()
                  if s.endswith("#fields")}
        kinds = {d.kind for d in drifts}
        if kinds <= {"added_field"}:
            return Proposal(noted="; ".join(d.detail for d in drifts) + " — noted")
        gone = {str(d.old) for d in drifts if d.kind in ("removed", "renamed") and d.old}
        steps = sorted(s for s, used in fields.items() if used & gone)
        if not steps:
            return Proposal(summary="; ".join(d.detail for d in drifts)
                            + " — no plan template reads it")
        return Proposal(summary=(f"plan step(s) {steps} read listing field(s) {sorted(gone)}: "
                                 + "; ".join(d.detail for d in drifts)),
                        hold={"level": "steps", "steps": steps})

    def _propose_file_type(self, conn: str, drifts: list[Any], ctx: dict[str, Any]) -> Proposal:
        c = ctx.get("connection") or {}
        scope = c.get("scope") or {}
        allowed = list(scope.get("file_types") or [])
        exts = [str(d.new) for d in drifts if d.kind == "new_file_type"]
        add = []
        notes = []
        for ext in exts:
            if ext == "(none)":
                notes.append("items with no extension: noted")
            elif not allowed:
                notes.append(f"{ext}: the scope admits every type an extractor exists for — "
                             f"already collected")
            elif ext in allowed:
                notes.append(f"{ext}: already in scope.file_types")
            elif not self._svc("extractor_exists", ext):
                notes.append(f"{ext}: no extractor exists; its items stay recorded as excluded "
                             f"(the reversal is to add an extractor)")
            else:
                add.append(ext)
        if not add:
            return Proposal(noted="; ".join(notes) or "noted")
        new = copy.deepcopy(c)
        new.setdefault("scope", {})["file_types"] = allowed + add
        return Proposal(artifact="connection", content=new, previous=copy.deepcopy(c),
                        summary=(f"add {add} to scope.file_types (an extractor exists); their "
                                 f"items are captured from the next sync"), notes=notes)

    # --- apply / hold / release --------------------------------------------------------------------
    def _write_version(self, spec: Any, conn: str, artifact: str, data: dict[str, Any], *,
                       reason: str, aid: Optional[str], by: str) -> dict[str, Any]:
        """Keep the version in force, then write the new one through mcp_ingest's own writer."""
        cur = self._svc("read_config", conn, artifact)
        prior = self.store.versions(conn, artifact)
        if cur is not None and (not prior or prior[-1]["sha256"] != _sha(cur)):
            self.store.add_version(conn, artifact, cur, by="(in force before adaptation)",
                                   reason="the version in force before this change",
                                   adaptation_id=aid)
        self._svc("write_config", conn, artifact, data)
        stored = self._svc("read_config", conn, artifact) or data
        return self.store.add_version(conn, artifact, stored, reason=reason, by=by,
                                      adaptation_id=aid)

    def _mapping_with(self, conn: str, ref: str, entry: dict[str, Any]) -> dict[str, Any]:
        m = copy.deepcopy(self._svc("read_config", conn, "mapping") or
                          {"source_id": conn, "tables": {}})
        m.setdefault("tables", {})[ref] = entry
        return m

    def _delegation_refusal(self, spec: Any, proposal: Proposal
                            ) -> tuple[Optional[str], Optional[dict[str, Any]]]:
        """(why the delegated approval cannot cover this proposal, None) or (None, the sealed
        record it is issued from). The package still proves the change additive, and re-verifies
        the sealed record when it accepts the delegation (`delegation_of`)."""
        pol = Policy.of(spec).policy
        if pol != DELEGATED_POLICY:
            return f"adapt.policy {pol!r} delegates nothing", None
        if proposal.artifact != "mapping":
            return (f"a {proposal.artifact} change (plan steps, connection scope) is never an "
                    f"additive change inside the declared scope"), None
        try:
            seal, why = self._seal(spec)
        except Exception as exc:                                      # noqa: BLE001
            seal, why = None, f"the delegation could not be checked ({type(exc).__name__}: {exc})"
        if seal is None:
            return (f"no sealed delegation is in force ({why}); nobody delegated these "
                    f"approvals"), None
        return None, seal

    def _versioned(self, spec: Any, conn: str, artifact: str, write: Callable[[], Any], *,
                   reason: str, aid: Optional[str], by: str) -> dict[str, Any]:
        """Keep the version in force, run ``write`` (mcp_ingest's own writer / approver), then
        record the version as STORED. Nothing approved is overwritten without its copy."""
        cur = self._svc("read_config", conn, artifact)
        prior = self.store.versions(conn, artifact)
        if cur is not None and (not prior or prior[-1]["sha256"] != _sha(cur)):
            self.store.add_version(conn, artifact, cur, by="(in force before adaptation)",
                                   reason="the version in force before this change",
                                   adaptation_id=aid)
        write()
        stored = self._svc("read_config", conn, artifact)
        if stored is None:
            raise AdaptationRefused(f"the {artifact} of {conn!r} could not be read back after "
                                    f"it was written")
        return self.store.add_version(conn, artifact, stored, reason=reason, by=by,
                                      adaptation_id=aid)

    def _need(self, name: str) -> Callable[..., Any]:
        fn = getattr(self.service, name, None)
        if not callable(fn):
            raise AdaptationRefused(f"the ingest service offers no {name}; an approval cannot be "
                                    f"recorded through it (fail closed)")
        return fn

    def _apply(self, spec: Any, conn: str, proposal: Proposal, aid: str, *, by: str,
               principal: Any) -> Optional[dict[str, Any]]:
        """Apply an approved proposal THROUGH mcp_ingest's approval API (INT3): nothing here
        writes ``status: approved`` into a YAML. ``principal`` is either the authenticated
        principal of a person (`approve`) or a minted `DelegatedApproval` (the auto path, mapping
        only — mcp_ingest proves the change additive before it records anything)."""
        reason = f"adaptation {aid}: {proposal.summary}"[:300]
        delegated = type(principal) is DelegatedApproval
        if proposal.artifact == "mapping":
            ref = proposal.content["table"]
            entry = {k: v for k, v in proposal.content["entry"].items()
                     if k not in ("status", "approval")}
            if delegated:
                fn = self._need("approve_delegated_mapping")

                def write() -> None:
                    try:
                        fn(conn, ref, entry, delegation=principal)
                    except Exception as exc:                          # noqa: BLE001
                        raise AdaptationRefused(str(exc)) from None
            else:
                fn = self._need("approve_mapping")

                def write() -> None:
                    self._svc("write_config", conn, "mapping",
                              self._mapping_with(conn, ref, {**entry, "status": "proposed"}))
                    fn(conn, principal=principal, tables=[ref])
            return self._versioned(spec, conn, "mapping", write, reason=reason, aid=aid, by=by)
        if delegated:
            raise AdaptationRefused(f"a delegated approval covers additive mapping changes only, "
                                    f"never a {proposal.artifact} change")
        if proposal.artifact == "plan":
            plan = {k: v for k, v in dict(proposal.content).items()
                    if k not in ("approval", "approved_at", "approved_by")}
            if plan.get("problems"):
                raise AdaptationRefused(f"the proposed plan has problems and cannot be approved: "
                                        f"{plan['problems']}; write the plan by hand")
            plan["status"] = "proposed"
            fn = self._need("approve_plan")

            def write() -> None:
                self._svc("write_config", conn, "plan", plan)
                fn(conn, principal=principal)
            return self._versioned(spec, conn, "plan", write, reason=reason, aid=aid, by=by)
        if proposal.artifact == "connection":
            return self._write_version(spec, conn, "connection", proposal.content, aid=aid, by=by,
                                       reason=reason)
        return None

    def _hold_plan(self, conn: str, proposal: Proposal, ctx: dict[str, Any]
                   ) -> Optional[dict[str, Any]]:
        h = proposal.hold
        if not h:
            return {"level": "none", "why": "nothing the crawl reads is affected"}
        honoured = self._holds_honoured()
        if h["level"] == "table":
            if honoured:
                return {**h, "via": "holds_file"}
            entry = ((ctx.get("mapping") or {}).get("tables") or {}).get(h["table"]) or {}
            if h.get("new") or entry.get("status") == "approved":
                return {**h, "via": "mapping_status"}
            return {"level": "none", "why": "the table is not built into the graph"}
        if honoured:
            return {**h, "via": "holds_file"}
        return {**h, "level": "connection", "via": "connection",
                "requested": h["level"],
                "why": (f"{h['level']} holds need the runner hook (HONOURS_DRIFT_HOLDS); until "
                        f"it is wired the whole connection is held — coarser, and said")}

    def _install_hold(self, spec: Any, conn: str, rec: dict[str, Any], ctx: dict[str, Any]
                      ) -> None:
        h = rec.get("hold") or {}
        aid = rec["adaptation_id"]
        via = h.get("via")
        if via == "holds_file":
            self._update_holds_file(conn, aid, h, add=True)
        elif via == "connection":
            self.store.set_hold(spec.id, conn, aid, {"why": rec.get("action", "")[:200],
                                                      "since": rec.get("created_at")})
        elif via == "mapping_status":
            ref = h["table"]
            entry = ((ctx.get("mapping") or {}).get("tables") or {}).get(ref)
            content = ((rec.get("proposal") or {}).get("content") or {}).get("entry")
            if rec.get("status") == "rejected":
                content = (rec.get("proposal") or {}).get("previous") or content
            want = dict(content or entry or {})
            if not want:
                return
            want["status"] = "proposed"
            if entry is not None and entry.get("status") == "proposed" and \
                    {k: v for k, v in entry.items() if k != "rationale"} == \
                    {k: v for k, v in want.items() if k != "rationale"}:
                return                                            # already held as wanted
            want["rationale"] = list(want.get("rationale") or []) + [
                f"HELD by adapter agent {spec.id}: adaptation {aid} awaits a person "
                f"(approve_adaptation / reject_adaptation)"]
            self._write_version(spec, conn, "mapping", self._mapping_with(conn, ref, want),
                                reason=f"hold: adaptation {aid} pending", aid=aid,
                                by=f"adapter_agent:{spec.id}")

    def _update_holds_file(self, conn: str, aid: str, h: dict[str, Any], *, add: bool) -> None:
        from databridge_mcp_ingest import records

        cur = records.load_holds(conn, getattr(self.service, "sources_root", None))
        data = cur.model_dump(mode="json")
        data.pop("source_id", None)
        if h.get("level") in ("table", "table_removed"):
            keys = [("tables", h["table"])]
        else:
            keys = [("steps", s) for s in h.get("steps") or []]
        for bucket, key in keys:
            if add:
                data[bucket][key] = aid
            elif data[bucket].get(key) == aid:
                data[bucket].pop(key)
        data["updated_at"] = self._now()
        self._svc("write_holds", conn, data)

    def _release(self, agent: str, conn: str, rec: dict[str, Any]) -> None:
        h = rec.get("hold") or {}
        if h.get("via") == "holds_file":
            self._update_holds_file(conn, rec["adaptation_id"], h, add=False)
        elif h.get("via") == "connection":
            self.store.set_hold(agent, conn, rec["adaptation_id"], None)

    def _release_and_rehold(self, spec: Any, conn: str, rec: dict[str, Any]) -> None:
        """Release `rec`'s hold, then put back the hold of every OTHER open adaptation on the same
        table / steps (R-025). A holds file names ONE adaptation per table, so releasing the one
        it names would otherwise un-hold a sibling — a rejected change kept held, typically."""
        self._release(spec.id, conn, rec)
        mine = rec.get("hold") or {}
        if mine.get("via") not in ("holds_file", "mapping_status"):
            return
        targets = self._hold_targets(mine)
        for aid, r in sorted(self.records(spec.id).items()):
            h = r.get("hold") or {}
            if aid == rec.get("adaptation_id") or r.get("connection") != conn or \
                    r.get("status") not in ("pending", "rejected") or \
                    not self._hold_targets(h) & targets:
                continue
            if h.get("via") == "holds_file":
                self._update_holds_file(conn, aid, h, add=True)
            elif h.get("via") == "mapping_status":
                # the entry NOW in force (it may carry a change just approved) is held as it is;
                # re-installing the sibling's own proposal would overwrite that change
                ref = h["table"]
                entry = ((self._svc("read_config", conn, "mapping") or {}).get("tables")
                         or {}).get(ref)
                if not entry or entry.get("status") == "proposed":
                    continue
                want = {**entry, "status": "proposed",
                        "rationale": list(entry.get("rationale") or []) + [
                            f"HELD by adapter agent {spec.id}: adaptation {aid} is still "
                            f"{r.get('status')}"]}
                self._write_version(spec, conn, "mapping", self._mapping_with(conn, ref, want),
                                    reason=f"hold: adaptation {aid} still {r.get('status')}",
                                    aid=aid, by=f"adapter_agent:{spec.id}")

    @staticmethod
    def _hold_targets(h: dict[str, Any]) -> set[tuple[str, str]]:
        if h.get("level") in ("table", "table_removed") and h.get("table"):
            return {("tables", str(h["table"]))}
        return {("steps", str(s)) for s in h.get("steps") or []}

    def _restore_previous(self, spec: Any, conn: str, rec: dict[str, Any], *, status: str
                          ) -> None:
        """Undo a MAPPING hold: put back the entry that was in force (as `status`), or mark a held
        new table unmapped. A new version is written — the held one stays in history."""
        h = rec.get("hold") or {}
        if h.get("via") != "mapping_status":
            return
        ref = h["table"]
        prev = (rec.get("proposal") or {}).get("previous")
        if rec.get("status") == "rejected" and prev:
            # a rejected proposal was never in force: what is held now IS the entry in force
            # (it may carry a change approved since the rejection, which must not be undone)
            now = ((self._svc("read_config", conn, "mapping") or {}).get("tables") or {}).get(ref)
            prev = {k: v for k, v in now.items() if k != "status"} if now else prev
        if prev:
            entry = {**prev, "status": status}
        else:
            entry = {"status": "unmapped", "origin": "manual", "node": None, "edges": [],
                     "rationale": [f"held new table released by adapter agent {spec.id} "
                                   f"({rec['adaptation_id']}); not mapped"]}
        self._write_version(spec, conn, "mapping", self._mapping_with(conn, ref, entry),
                            reason=f"release of hold {rec['adaptation_id']}",
                            aid=rec["adaptation_id"], by=f"adapter_agent:{spec.id}")

    def _advance_over(self, base: Any, current: Any, units: list[str], apply_ids: set[str],
                      skip_ids: set[str]) -> Any:
        """`base` advanced to `current` over `units`, taking ONLY the drifts in `apply_ids`.

        A unit whose drifts are all applied moves whole (`drift.advance`, R-022 f2 #7). A unit that
        also carries a drift whose REJECTION stands (`skip_ids`) moves one change at a time (a
        table's columns, keys and types), and the result is VERIFIED by diffing: from `base` it
        must show exactly the applied drifts, and to `current` exactly the skipped ones — so an
        approval never absorbs a rejected change (R-025, Q-09). Any drift in neither set, or a
        partial move that cannot be proven, raises `DriftAdvanceRefused`."""
        drift = self._drift()
        new = base
        for unit in sorted(set(units)):
            here = [d for d in drift.diff(new, current) if unit in d.units()]
            ids = {d.drift_id for d in here}
            extra = [d for d in here if d.drift_id not in apply_ids | skip_ids]
            if extra:
                raise drift.DriftAdvanceRefused(
                    f"advancing {unit} would absorb drift(s) nobody approved: "
                    + "; ".join(f"{d.drift_id} {d.detail}" for d in extra[:10]))
            if not ids & apply_ids:
                continue                                  # nothing to take (only rejected drifts)
            if not ids & skip_ids:
                new = drift.advance(new, current, [unit], adopt_unobserved=False,
                                    approved_drifts=sorted(apply_ids))
                continue
            new = self._advance_partial(new, current, unit,
                                        [d for d in here if d.drift_id in apply_ids],
                                        ids & skip_ids)
        return new

    def _advance_partial(self, base: Any, current: Any, unit: str, take: list[Any],
                         keep: set[str]) -> Any:
        drift = self._drift()
        prefix, _, ref = unit.partition(":")
        skipped = ", ".join(sorted(keep)[:10])
        if prefix != "table" or not base.tables or not current.tables or \
                ref not in base.tables or ref not in current.tables:
            raise drift.DriftAdvanceRefused(
                f"advancing {unit} would absorb the rejected drift(s) {skipped}: only a table's "
                f"columns move one change at a time")
        t = base.tables[ref].model_dump(mode="python")
        cur = current.tables[ref]
        cols, fks = t["columns"], t["foreign_keys"]
        for d in take:
            what = (d.evidence or {}).get("what")
            if d.kind == "added_column" and d.column in cur.columns:
                cols[d.column] = cur.columns[d.column].model_dump(mode="python")
                fks.extend(f for f in cur.foreign_keys if f.get("column") == d.column
                           and f not in fks)
            elif d.kind == "removed" and d.column:
                cols.pop(d.column, None)
                fks[:] = [f for f in fks if f.get("column") != d.column]
            elif d.kind == "renamed" and d.new in cur.columns:
                cols.pop(str(d.old), None)
                cols[str(d.new)] = cur.columns[str(d.new)].model_dump(mode="python")
            elif d.kind == "type_changed" and what == "primary_key":
                t["primary_key"] = list(cur.primary_key)
            elif d.kind == "type_changed" and what == "foreign_key":
                if isinstance(d.old, dict):
                    fks[:] = [f for f in fks if f != d.old]
                if isinstance(d.new, dict) and d.new not in fks:
                    fks.append(d.new)
            elif d.kind == "type_changed" and d.column in cur.columns:
                cols[d.column] = cur.columns[d.column].model_dump(mode="python")
            else:
                raise drift.DriftAdvanceRefused(
                    f"advancing {unit}: {d.drift_id} ({d.kind}) cannot be taken apart from the "
                    f"rejected drift(s) {skipped}, so it would absorb them")
        tables = dict(base.tables)
        tables[ref] = type(base.tables[ref]).model_validate(t)
        cand = base.model_copy(update={"tables": tables}, deep=True)
        got = {d.drift_id for d in drift.diff(base, cand) if unit in d.units()}
        left = {d.drift_id for d in drift.diff(cand, current) if unit in d.units()}
        if got != {d.drift_id for d in take} or left != keep:
            raise drift.DriftAdvanceRefused(
                f"advancing {unit} one change at a time could not be proven (took "
                f"{sorted(got)}, left {sorted(left)}): it would absorb the rejected drift(s) "
                f"{skipped} or lose an approved one")
        return cand

    def _planned_advance(self, spec: Any, conn: str, rec: dict[str, Any]) -> Optional[Any]:
        """The baseline approving `rec` would leave: its units advanced over ITS drifts only,
        stepping around every drift another adaptation's standing rejection keeps."""
        drift = self._drift()
        raw = self.store.shape(spec.id, rec.get("observed_shape") or "")
        base = self.store.baseline(spec.id, conn)
        if raw is None or base is None:
            return None
        apply_ids = {str(i) for i in rec.get("drift_ids") or []}
        skip = set(self._standing(spec.id, conn, exclude=rec.get("adaptation_id"))) - apply_ids
        return self._advance_over(drift.SourceShape.model_validate(base),
                                  drift.SourceShape.model_validate(raw),
                                  list(rec.get("units") or [rec.get("unit")]), apply_ids, skip)

    def _refuse_unapproved(self, spec: Any, conn: str, rec: dict[str, Any]) -> None:
        """Checked BEFORE anything is applied or released (R-022 f2 #7; R-025)."""
        aid = rec["adaptation_id"]
        bundled = {i: a for i, a in self._standing(spec.id, conn, exclude=aid).items()
                   if i in set(rec.get("drift_ids") or [])}
        if bundled:
            raise AdaptationRefused(
                f"REJECTED_CHANGE_BUNDLED: adaptation {aid} carries change(s) already rejected "
                f"({'; '.join(f'{i} in {a}' for i, a in sorted(bundled.items()))}). A rejection is "
                f"final, so approving this would approve them; nothing was applied. The next sync "
                f"re-proposes its other change(s) on their own")
        drift = self._drift()
        try:
            self._planned_advance(spec, conn, rec)
        except drift.DriftAdvanceRefused as exc:
            raise AdaptationRefused(
                f"adaptation {aid} cannot be decided: advancing its unit(s) would absorb drift(s) "
                f"it does not carry and nobody approved ({exc}). Nothing was applied; the next "
                f"sync re-proposes the unit as it is now") from None
        recorded = rec.get("changes_digest")
        if recorded is not None and recorded != changes_digest_of(rec.get("drift_ids")):
            raise AdaptationRefused(
                f"CHANGES_DIGEST_MISMATCH: adaptation {aid}'s recorded digest does not name the "
                f"drifts it carries; nothing was applied")

    def _advance_units(self, spec: Any, conn: str, rec: dict[str, Any], *, reason: str,
                       by: Any = None) -> None:
        # the drifts THIS adaptation carries are the approved set; a drift another adaptation's
        # rejection keeps is stepped around, any other drift refuses (`DriftAdvanceRefused`)
        new = self._planned_advance(spec, conn, rec)
        if new is None:
            return
        self.store.set_baseline(spec.id, conn, new.model_dump(mode="json"), reason=reason,
                                by=json.dumps(by, default=str) if by else
                                f"adapter_agent:{spec.id}",
                                adaptation_id=rec["adaptation_id"])

    # --- after the run -----------------------------------------------------------------------------
    def after_run(self, spec: Any, conn: str, pre: Optional[PreRun], *, run_id: str,
                  run_dir: Optional[Path]) -> dict[str, Any]:
        if pre is None:
            return {}
        if pre.disabled is not None or pre.current is None:
            return pre.summary()
        drift = self._drift()
        post = None
        if run_dir is not None and Path(run_dir).is_dir():
            try:
                post = self.service.run_shape(conn, Path(run_dir))
            except Exception as exc:                                  # noqa: BLE001
                pre.problems.append(f"run shape: {type(exc).__name__}: {exc}")
        if post is not None and not pre.established:
            base = drift.SourceShape.model_validate(self.store.baseline(spec.id, conn))
            file_base = drift.SourceShape(source_id=conn, file_types=base.file_types,
                                          mimes=base.mimes)
            post_drifts = drift.diff(file_base, post)
            if post_drifts:
                pre.drifts.extend(post_drifts)
                pre.current = pre.current.overlay(post)
                ctx = self._ctx(conn)
                ctx["current"] = pre.current
                self._handle_all(spec, conn, post_drifts, ctx, pre, run_id=run_id,
                                 stage="post")
        if run_dir is not None and Path(run_dir).is_dir():
            self._write_run_records(spec, conn, pre, Path(run_dir))
        elif self._mapping_status_holds(spec, conn):
            pre.records_error = (f"a held table must be ledgered excluded "
                                 f"({'DRIFT_PENDING_APPROVAL'}) in the run directory, and this "
                                 f"run's directory is unknown ({run_dir!r})")
        self._finish(spec, conn, pre, post)
        return pre.summary()

    def _finish(self, spec: Any, conn: str, pre: PreRun, post: Any = None) -> None:
        """Advance the baseline for every unit that has no open adaptation; adopt facets seen for
        the first time. Units with a pending / rejected / reported adaptation keep their baseline
        (so the drift is still visible, and approval is what moves it)."""
        drift = self._drift()
        if pre.current is None:
            return
        base_raw = self.store.baseline(spec.id, conn)
        if base_raw is None:
            return
        current = pre.current.overlay(post) if post is not None else pre.current
        base = drift.SourceShape.model_validate(base_raw)
        recs = [r for r in self.records(spec.id).values() if r.get("connection") == conn]
        # a unit with an UNDECIDED adaptation keeps its baseline whole; a unit with a standing
        # REJECTION moves only over the drifts that were applied/noted/approved, stepping around
        # the rejected ones (R-025), which stay visible and excluded
        open_units = {u for r in recs if r.get("status") in ("pending", "reported")
                      for u in (r.get("units") or [r.get("unit")])}
        # R-022 f2 #7: the baseline moves only over drifts an adaptation of this connection
        # applied, noted or a person approved; a unit carrying any other drift keeps its baseline
        # (the drift stays visible and is proposed at the next sync)
        covered = {str(i) for r in recs if r.get("status") in ("applied", "noted", "approved")
                   for i in r.get("drift_ids") or []}
        standing = set(self._standing(spec.id, conn)) - covered
        new, moved = base, []
        for unit in sorted({u for d in drift.diff(base, current) for u in d.units()}
                           - open_units):
            try:
                new = self._advance_over(new, current, [unit], covered, standing)
            except drift.DriftAdvanceRefused:
                continue
            moved.append(unit)
        new = drift.advance(new, current, [], approved_drifts=[])   # first-seen facets adopted
        if new.digest() != base.digest():
            self.store.set_baseline(spec.id, conn, new.model_dump(mode="json"),
                                    reason=(f"sync {pre.touched and 'applied/noted' or 'observed'}"
                                            f": units {sorted(moved)[:20]}"),
                                    by=f"adapter_agent:{spec.id}")

    def _write_run_records(self, spec: Any, conn: str, pre: PreRun, run_dir: Path) -> None:
        """Into the ingest run directory: `adaptations.json` (what this sync did about the shape)
        and `excluded.drift.jsonl` (every item held by a mapping-status hold, rule
        DRIFT_PENDING_APPROVAL + reversal). Both are found by pattern; §5.3 event types are
        unchanged."""
        from databridge_mcp_ingest import records

        touched = [r for r in pre.touched.values()]
        body = {"record_type": "adaptation_applied", "agent_id": spec.id, "connection": conn,
                "at": self._now(), "summary": pre.summary(),
                "records": [{k: r.get(k) for k in ("adaptation_id", "agent_id", "connection",
                                                   "status", "drift_ids", "drifts", "action",
                                                   "produced_by", "created_at", "unit", "hold",
                                                   "version")}
                            for r in touched]}
        _atomic(run_dir / "adaptations.json", json.dumps(body, indent=1, sort_keys=True,
                                                         default=str))
        held = self._mapping_status_holds(spec, conn)
        if not held:
            return
        inv = {}
        try:
            arts = records.read_run(run_dir)
            inv = {x.uri: x for x in arts.records("inventory")}
        except Exception:                                             # noqa: BLE001
            pass
        lines = []
        for r in held:
            ref = r["hold"]["table"]
            item = inv.get(ref)
            iid = item.item_id if item is not None else self._svc("item_identity", conn, ref)
            rev = records.DriftHolds(source_id=conn).reverse_text(r["adaptation_id"])
            lines.append({"item_id": iid, "uri": ref, "rel": ref,
                          "rule": records.DRIFT_PENDING_APPROVAL, "reverse": rev,
                          "at": self._now(), "adaptation_id": r["adaptation_id"],
                          "drift_ids": r.get("drift_ids"),
                          "note": ("captured, NOT built: the table's mapping is held until a "
                                   "person decides")})
        path = run_dir / "excluded.drift.jsonl"
        have = path.read_text(encoding="utf-8") if path.is_file() else ""   # the runner's lines
        seen = {json.loads(x).get("item_id") for x in have.splitlines() if x.strip()}
        _atomic(path, have + "".join(json.dumps(x, sort_keys=True) + "\n" for x in lines
                                     if x["item_id"] not in seen))

    def _mapping_status_holds(self, spec: Any, conn: str) -> list[dict[str, Any]]:
        """Open adaptations holding a table by its mapping status (the runner does not ledger
        these itself, so this engine owes their exclusion record)."""
        return [r for r in self.records(spec.id).values()
                if r.get("connection") == conn and r.get("status") in ("pending", "rejected")
                and (r.get("hold") or {}).get("via") == "mapping_status"]

    def unmapped_occurrences(self, spec: Any, conn: str, run_dir: Optional[Path]
                             ) -> list[dict[str, Any]]:
        """Grounded occurrences of tables no kind maps (for the ontology-proposal scan): the
        pointer is a real `table_cell` of the captured snapshot (first row's key cell)."""
        import yaml
        from databridge_core import SourcePointer
        from databridge_mcp_ingest import records

        from .proposals import _clean_kind, ground

        if run_dir is None or not (Path(run_dir) / "mapping.snapshot.yaml").is_file():
            return []
        snap = yaml.safe_load((Path(run_dir) / "mapping.snapshot.yaml")
                              .read_text(encoding="utf-8")) or {}
        arts = records.read_run(Path(run_dir))
        captured = {r.uri: r for r in arts.records("captured") if r.kind == "table"}
        out = []
        for ref, e in sorted((snap.get("tables") or {}).items()):
            if (e or {}).get("status") != "unmapped" or ref not in captured:
                continue
            if any("history snapshot" in str(x) for x in e.get("rationale") or []):
                continue
            name = _clean_kind(_concept(ref))
            table = self._svc("captured_table", captured[ref].sha256) or {}
            rows = table.get("rows") or []
            if not name or not rows:
                continue
            pk = list(table.get("primary_key") or [])
            row = rows[0]
            row_id = ",".join(str(row.get(c)) for c in pk) if pk and all(
                row.get(c) is not None for c in pk) else "#0"
            column = pk[0] if pk else next(iter(row), None)
            if column is None:
                continue
            doc = captured[ref].item_id
            try:
                ptr = SourcePointer(source_id=conn, kind="table_cell", table=ref, column=column,
                                    row_id=row_id, capture_digest=captured[ref].sha256,
                                    trust="untrusted").model_dump(mode="json", exclude_none=True)
            except Exception:                                         # noqa: BLE001
                continue
            ok, _why = ground({"source_id": conn, "document_id": doc, "pointer": ptr})
            if ok is None:
                continue
            key = _sha(["node", name, ref, ptr])
            out.append({"key": key, "axis": "node", "name": name, "raw_kind": ref,
                        "entity": ref, "document_id": doc, "uri": ref, "source_id": conn,
                        "run_id": Path(run_dir).name, "pointer": ok, "span_basis": "table",
                        "text": f"table {ref!r} is captured but no node kind maps it",
                        "produced_by": {"agent": "rules", "on_behalf_of":
                                        f"adapter_agent:{spec.id}", "model": None}})
        return out

    # --- decisions ---------------------------------------------------------------------------------
    def approve(self, spec: Any, adaptation_id: str, *, by: Any, note: Optional[str] = None,
                changes_digest: Optional[str] = None) -> dict[str, Any]:
        """Approve EXACTLY the changes this adaptation carries. `changes_digest`, when the caller
        passes the digest it was shown, must name that same set (R-025): a decision covers the
        change a person saw, never a set that differs from it."""
        who = principal_of(by, "approve_adaptation")
        with self.store.lock:
            rec = self.records(spec.id).get(adaptation_id)
            if rec is None:
                raise AdaptationRefused(f"no adaptation {adaptation_id!r} for {spec.id!r}")
            if rec.get("status") == "rejected":
                # AA3 (founder decision, INT4 open question 2): a rejection is FINAL. The part
                # stays held; if the source still differs it is proposed again only when the
                # drift is observed again later, as a NEW adaptation (`_handle`).
                raise AdaptationRefused(
                    f"ADAPTATION_REJECTED_FINAL: adaptation {adaptation_id} was rejected; a "
                    f"rejection is final - only a pending or reported one is approved. If the "
                    f"change is wanted, it is re-proposed as a new adaptation when the drift is "
                    f"observed again")
            if rec.get("status") not in ("pending", "reported"):
                raise AdaptationRefused(f"adaptation {adaptation_id} is {rec.get('status')!r}; "
                                        f"only a pending or reported one is decided")
            conn = rec["connection"]
            if changes_digest is not None and \
                    changes_digest != changes_digest_of(rec.get("drift_ids")):
                raise AdaptationRefused(
                    f"CHANGES_DIGEST_MISMATCH: adaptation {adaptation_id} carries "
                    f"{changes_digest_of(rec.get('drift_ids'))}, not the {changes_digest} this "
                    f"decision names; nothing was applied. Re-read the held change and decide it "
                    f"as it is")
            self._refuse_unapproved(spec, conn, rec)
            prop = rec.get("proposal") or {}
            version = None
            if prop.get("artifact") and prop.get("content") is not None:
                p = Proposal(artifact=prop["artifact"], content=prop["content"],
                             previous=prop.get("previous"), summary=rec.get("action", ""))
                version = self._apply(spec, conn, p, adaptation_id, principal=by,
                                      by=f"{who['principal_id']} (approve_adaptation)")
            self._release_and_rehold(spec, conn, rec)
            self._advance_units(spec, conn, rec, by=who,
                                reason=f"approved by {who['principal_id']} ({adaptation_id})")
            return self._status(spec.id, adaptation_id, "approved", by=who, note=note,
                                version=version, decided_by=who, decided_at=self._now())

    def resolve_by_plan(self, spec: Any, adaptation_id: str, *, by: Any, steps: list[str]
                        ) -> Optional[dict[str, Any]]:
        """A person re-planned the held step(s) and approved that plan (`plans.approve_plan(
        principal=...)` hands over the re-planned step ids). If EVERY held step is among them, an
        authenticated principal that decides the adaptation: approved, the hold released, the
        baseline advanced. The agent's own proposal is NOT applied (the person's plan is)."""
        who = principal_of(by, "a plan approval resolving a drift hold")
        with self.store.lock:
            rec = self.records(spec.id).get(adaptation_id)
            if rec is None or rec.get("status") not in ("pending", "rejected"):
                return None
            held = sorted(((rec.get("hold") or {}).get("steps")) or [])
            if not held or not set(held) <= set(steps):
                return None
            conn = rec["connection"]
            self._refuse_unapproved(spec, conn, rec)
            self._release_and_rehold(spec, conn, rec)
            self._advance_units(spec, conn, rec, by=who,
                                reason=f"re-planned by {who['principal_id']} ({adaptation_id})")
            return self._status(spec.id, adaptation_id, "approved", by=who,
                                note=(f"held step(s) {held} re-planned; the plan was approved "
                                      f"through approve_plan"),
                                decided_by=who, decided_at=self._now(), via="approve_plan")

    def reject(self, spec: Any, adaptation_id: str, *, by: Any, reason: str) -> dict[str, Any]:
        who = principal_of(by, "reject_adaptation")
        if not (reason or "").strip():
            raise AdaptationRefused("a rejection needs a reason (it is recorded)")
        with self.store.lock:
            rec = self.records(spec.id).get(adaptation_id)
            if rec is None:
                raise AdaptationRefused(f"no adaptation {adaptation_id!r} for {spec.id!r}")
            if rec.get("status") != "pending":
                raise AdaptationRefused(f"adaptation {adaptation_id} is {rec.get('status')!r}; "
                                        f"only a pending one can be rejected")
            out = self._status(spec.id, adaptation_id, "rejected", by=who, note=reason.strip(),
                               decided_by=who, decided_at=self._now())
            # the hold STAYS; a mapping hold is re-pointed at the entry that was in force, so an
            # unrelated `approve_mapping` cannot approve the rejected proposal
            ctx = self._ctx(out["connection"])
            self._install_hold(spec, out["connection"], out, ctx)
            return out

    # --- health ------------------------------------------------------------------------------------
    def health(self, spec: Any) -> dict[str, Any]:
        recs = self.records(spec.id)
        state = self.store.state(spec.id)

        def brief(r: dict[str, Any]) -> dict[str, Any]:
            return {"adaptation_id": r["adaptation_id"], "connection": r.get("connection"),
                    "unit": r.get("unit"), "status": r.get("status"),
                    "kinds": sorted({k for d in r.get("drifts") or []
                                     for k in ([d.get("kind")] + (["subject_touching"]
                                                                  if d.get("subject_touching")
                                                                  else []))}),
                    "action": r.get("action"), "hold": (r.get("hold") or {}).get("level"),
                    "created_at": r.get("created_at")}

        by_status: dict[str, list[dict[str, Any]]] = {}
        for r in recs.values():
            by_status.setdefault(r.get("status", "?"), []).append(brief(r))
        baselines = {}
        for c in spec.connections:
            b = self.store.baseline(spec.id, c)
            baselines[c] = ({"taken_at": b.get("taken_at"), "digest": "sha256:" + _sha(b)}
                            if b else None)
        return {"available": self.unavailable() is None, "unavailable": self.unavailable(),
                "policy": Policy.of(spec).as_dict(),
                "pending": by_status.get("pending", []), "rejected": by_status.get("rejected", []),
                "reported": by_status.get("reported", []),
                "counts": {s: len(v) for s, v in sorted(by_status.items())},
                "held_connections": {c: sorted(v) for c, v in self.store.holds(spec.id).items()},
                "baselines": baselines, "last_check": state,
                "screening_failed": {c: v.get("screening_failed") for c, v in sorted(state.items())
                                     if isinstance(v, dict) and v.get("screening_failed")},
                "notifications": len(self.store.notifications(spec.id))}


__all__ = ["AdaptationEngine", "AdaptStore", "AdaptationRefused", "AdaptAuditError", "Policy",
           "PreRun", "Proposal", "ADDITIVE", "NEVER_AUTO", "MANDATORY", "principal_of",
           "trust_principal_type", "attr_name_for", "DelegatedApproval", "delegation_of",
           "policy_digest", "DELEGATED_POLICY", "changes_digest_of", "rejection_stands"]
