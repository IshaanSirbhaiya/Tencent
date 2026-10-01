"""The adapter-agent registry: `AdapterAgentSpec` YAML under `WALNUT_ADAPTER_AGENTS_DIR`.

    <root>/<id>.yaml              the spec (§5.6), exactly as `dump_spec` writes it
    <root>/_state/<id>.json       registry-only state the frozen spec cannot carry (deleted marker)
    <root>/_history/<id>.jsonl    append-only audit: every create/update/pause/resume/delete, who,
                                  when, and the full spec as it stood after the change

**Delete is a status, never a file deletion.** The spec's `status` vocabulary is frozen
(draft/active/paused), so a deleted agent is paused and marked deleted in `_state/`; its spec file
and its history stay on disk, and `list()` hides it unless asked. Nothing here removes a file.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional, Union

from databridge_workflow_spec import AdapterAgentSpec, SpecError, dump_spec, load_spec
from databridge_workflow_spec import spec_digest as _contract_spec_digest

from . import paths
from .events import now_iso


class RegistryError(ValueError):
    """An adapter agent that does not exist, already exists, or cannot take that change."""


#: Fields an `update` may not change: the id names files and history, and `created_from` is the
#: user's English, kept verbatim (§5.6 — "never reworded").
IMMUTABLE_FIELDS = frozenset({"spec", "id", "created_from"})


def _check_transition(old: str, new: str, action: str) -> None:
    """`active` is reached only by `activate` (from a draft, checked) or `resume` (from a pause of
    an active agent); a generic update cannot flip an agent live (R-019, UI-builder deferred #1)."""
    if new == "active" and action not in ("activate", "resume"):
        raise RegistryError(f"status {old!r} -> 'active' is not an update: "
                            f"{'activate the draft' if old == 'draft' else 'resume it'} instead")


def spec_digest(spec: AdapterAgentSpec) -> str:
    """The workflow contract's digest (`databridge_workflow_spec.spec_digest`, canonical JSON) —
    one definition for every spec kind. Re-exported here because callers import it from this
    package (INT1, 2026-10-01)."""
    return _contract_spec_digest(spec)


def legacy_spec_digest(spec: AdapterAgentSpec) -> str:
    """The digest this registry wrote before INT1: sha256 over the presentation YAML
    (`dump_spec`). Kept ONLY to recognise digests already stored in history lines, run records and
    `produced_by.agent_version`; never written any more."""
    return "sha256:" + hashlib.sha256(dump_spec(spec).encode("utf-8")).hexdigest()


def classify_digest(stored: Any, spec_data: Any) -> dict[str, Any]:
    """How a STORED digest relates to the spec it was stored with. Never raises.

    ``digest_scheme``: ``canonical_json`` (the current rule) · ``legacy_dump_spec`` (the pre-INT1
    rule — re-digested, not rejected) · ``unverified`` (matches neither, or the stored spec no
    longer loads: flagged, the line is still returned). ``spec_digest_current`` is the current-rule
    digest of the stored spec, or None when the spec cannot be loaded."""
    try:
        spec = load_spec(spec_data) if isinstance(spec_data, dict) else None
    except (SpecError, ValueError, TypeError):
        spec = None
    if not isinstance(spec, AdapterAgentSpec):
        return {"digest_scheme": "unverified", "spec_digest_current": None}
    current = spec_digest(spec)
    if stored == current:
        scheme = "canonical_json"
    elif stored == legacy_spec_digest(spec):
        scheme = "legacy_dump_spec"
    else:
        scheme = "unverified"
    return {"digest_scheme": scheme, "spec_digest_current": current}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class AdapterAgentRegistry:
    def __init__(self, root: Optional[Path] = None) -> None:
        self._root = Path(root) if root is not None else None
        #: files under root that did not parse on the last `list()` — surfaced, never dropped
        self.problems: dict[str, str] = {}

    @property
    def root(self) -> Path:
        return self._root if self._root is not None else paths.adapter_agents_dir()

    # --- paths -----------------------------------------------------------------------------
    def spec_path(self, agent_id: str) -> Path:
        return self.root / f"{agent_id}.yaml"

    def _state_path(self, agent_id: str) -> Path:
        return self.root / "_state" / f"{agent_id}.json"

    def _history_path(self, agent_id: str) -> Path:
        return self.root / "_history" / f"{agent_id}.jsonl"

    # --- reads -----------------------------------------------------------------------------
    def state(self, agent_id: str) -> dict[str, Any]:
        p = self._state_path(agent_id)
        if not p.is_file():
            return {"deleted": False}
        return json.loads(p.read_text(encoding="utf-8"))

    def is_deleted(self, agent_id: str) -> bool:
        return bool(self.state(agent_id).get("deleted"))

    def exists(self, agent_id: str) -> bool:
        return self.spec_path(agent_id).is_file()

    def get(self, agent_id: str, *, include_deleted: bool = False) -> AdapterAgentSpec:
        p = self.spec_path(agent_id)
        if not p.is_file():
            raise RegistryError(f"no adapter agent {agent_id!r} (looked for {p})")
        if not include_deleted and self.is_deleted(agent_id):
            raise RegistryError(f"adapter agent {agent_id!r} was deleted; its spec and history are "
                                f"kept under {self.root}")
        spec = load_spec(p)
        if not isinstance(spec, AdapterAgentSpec):
            raise RegistryError(f"{p} holds a {type(spec).__name__}, not an adapter agent spec")
        return spec

    def list(self, *, include_deleted: bool = False) -> list[AdapterAgentSpec]:
        self.problems = {}
        if not self.root.is_dir():
            return []
        out: list[AdapterAgentSpec] = []
        for p in sorted(self.root.glob("*.yaml")):
            try:
                spec = load_spec(p)
            except (SpecError, OSError, ValueError) as exc:
                self.problems[p.name] = str(exc)
                continue
            if not isinstance(spec, AdapterAgentSpec):
                self.problems[p.name] = f"not an adapter agent spec ({type(spec).__name__})"
                continue
            if not include_deleted and self.is_deleted(spec.id):
                continue
            out.append(spec)
        return out

    def history(self, agent_id: str) -> list[dict[str, Any]]:
        """Every history line, as stored, plus how its stored `spec_digest` was computed
        (`classify_digest`): a line from before the single digest definition reads as
        ``legacy_dump_spec`` with its current-rule digest beside it. The file is append-only and is
        never rewritten; nothing here refuses a line for its digest."""
        p = self._history_path(agent_id)
        if not p.is_file():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if isinstance(rec, dict) and "spec_digest" in rec:
                rec.update(classify_digest(rec.get("spec_digest"), rec.get("spec")))
            out.append(rec)
        return out

    # --- writes ----------------------------------------------------------------------------
    def _record(self, agent_id: str, action: str, by: str, spec: AdapterAgentSpec,
                **extra: Any) -> None:
        p = self._history_path(agent_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": now_iso(), "action": action, "by": by or None,
               "spec_digest": spec_digest(spec), "status": spec.status,
               "spec": spec.model_dump(mode="json"), **extra}
        with open(p, "ab") as fh:
            fh.write(json.dumps(rec, sort_keys=True).encode("utf-8") + b"\n")
            fh.flush()

    def _save(self, spec: AdapterAgentSpec) -> Path:
        path = self.spec_path(spec.id)
        _atomic_write(path, dump_spec(spec))
        return path

    def create(self, source: Union[AdapterAgentSpec, dict, str, Path], *,
               by: str = "") -> AdapterAgentSpec:
        spec = source if isinstance(source, AdapterAgentSpec) else load_spec(source)
        if not isinstance(spec, AdapterAgentSpec):
            raise RegistryError(f"expected an adapter_agent/v1 spec, got {type(spec).__name__}")
        if self.exists(spec.id):
            deleted = " (deleted — its spec and history are kept)" if self.is_deleted(spec.id) \
                else ""
            raise RegistryError(f"adapter agent {spec.id!r} already exists{deleted}; update it, "
                                f"or choose another id")
        self._save(spec)
        self._record(spec.id, "create", by, spec)
        return spec

    def update(self, agent_id: str, changes: Union[dict[str, Any], AdapterAgentSpec], *,
               by: str = "", action: str = "update") -> AdapterAgentSpec:
        current = self.get(agent_id)
        data = current.model_dump(mode="json")
        new = changes.model_dump(mode="json") if isinstance(changes, AdapterAgentSpec) \
            else dict(changes)
        for field in IMMUTABLE_FIELDS:
            if field in new and new[field] != data[field]:
                raise RegistryError(f"{field!r} cannot change on an existing adapter agent "
                                    f"({data[field]!r} -> {new[field]!r})")
        if new.get("status", data["status"]) != data["status"]:
            _check_transition(data["status"], new["status"], action)
        data.update(new)
        try:
            spec = load_spec(data)
        except SpecError as exc:
            raise RegistryError(str(exc)) from None
        assert isinstance(spec, AdapterAgentSpec)
        self._save(spec)
        self._record(agent_id, action, by, spec)
        return spec

    def pause(self, agent_id: str, *, by: str = "") -> AdapterAgentSpec:
        return self.update(agent_id, {"status": "paused"}, by=by, action="pause")

    def resume(self, agent_id: str, *, by: str = "") -> AdapterAgentSpec:
        """PAUSED -> active, and only for an agent that was active before it was paused. A draft
        is activated (`activate`, with its checks), never resumed — fail closed (R-019)."""
        spec = self.get(agent_id)
        if spec.status != "paused":
            raise RegistryError(f"adapter agent {agent_id!r} is {spec.status!r}; only a paused "
                                f"agent is resumed" + (" — a draft is activated (with its "
                                                       "checks), not resumed"
                                                       if spec.status == "draft" else ""))
        before = self._status_before_pause(agent_id)
        if before != "active":
            raise RegistryError(f"adapter agent {agent_id!r} was {before!r} before it was paused; "
                                f"only an agent that was active is resumed — activate it (with "
                                f"its checks) instead")
        return self.update(agent_id, {"status": "active"}, by=by, action="resume")

    def activate(self, agent_id: str, *, by: str = "") -> AdapterAgentSpec:
        """DRAFT -> active. Callers check the draft first (`AdapterAgentManager.activate`)."""
        spec = self.get(agent_id)
        if spec.status != "draft":
            raise RegistryError(f"adapter agent {agent_id!r} is {spec.status!r}; only a draft is "
                                f"activated")
        return self.update(agent_id, {"status": "active"}, by=by, action="activate")

    def _status_before_pause(self, agent_id: str) -> Optional[str]:
        """The status the agent had before its current pause, from the append-only history."""
        statuses = [r.get("status") for r in self.history(agent_id)]
        for st in reversed(statuses):
            if st != "paused":
                return st
        return None

    def delete(self, agent_id: str, *, by: str = "", reason: str = "") -> AdapterAgentSpec:
        """Mark deleted: status paused + `_state` marker. The spec file and history remain."""
        spec = self.get(agent_id)
        if spec.status != "paused":
            spec = self.update(agent_id, {"status": "paused"}, by=by, action="pause")
        _atomic_write(self._state_path(agent_id), json.dumps(
            {"deleted": True, "deleted_at": now_iso(), "deleted_by": by or None,
             "reason": reason or None}, indent=2))
        self._record(agent_id, "delete", by, spec, reason=reason or None)
        return spec


__all__ = ["AdapterAgentRegistry", "RegistryError", "spec_digest", "legacy_spec_digest",
           "classify_digest", "IMMUTABLE_FIELDS"]
