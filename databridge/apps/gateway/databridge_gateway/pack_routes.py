"""``GET /packs`` — the pack registry, served from the real pack directory.

The frontend has always asked the gateway for this first and fallen back to a bundled
``config/packs.json`` when the route 404'd (``apps/frontend/lib/packs.ts``). This module is what
makes the fallback dead code rather than a fiction: the registry is assembled from ``packs/`` on
disk, which is where a pack's vocabulary already lives.

**Two files per pack, and the split is the point.**

``packs/<name>/ontology.yaml``  what KINDS the pack has — read through ``databridge_graph.packs``
                                so this route cannot disagree with the loader the engine uses.
``packs/<name>/ui.yaml``        what a PERSON is shown — the nouns, the operator surfaces, the
                                roster, the demo trials, the graph colours.

**Availability is computed, never declared.** ``ui.yaml`` carries no ``available`` key on purpose.
Whether a pack is available is a property of the *deployment* — the binding in
``packs/deployment.yaml``, ``DATABRIDGE_PACKS``, or ``bind_deployment()`` — and of whether the pack
declares any operator surface to open. A pack that declared its own availability could claim to be
enabled when nothing had enabled it, which is the one thing a pack switcher must not be able to
say. The ``unavailable_note`` names which of the two reasons applies and, for the binding case,
what the binding actually is.

**A pack missing its ``ui.yaml`` is reported, not invented.** It is served with the identity and
ontology facts that *do* exist and a ``ui_missing`` reason, and it is not available. The
alternative — synthesising plausible nouns so the chrome renders — would put words on a screen
that no pack ever said, which is the same failure as a fabricated value with no pointer.

Nothing here knows a domain. Every noun in the response came out of a pack file.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from .auth import install_auth_handler, require_principal

log = logging.getLogger("databridge.gateway.packs")

#: The presentation half of a pack, beside ``ontology.yaml``.
UI_FILE = "ui.yaml"

#: Keys `apps/frontend/lib/packs.ts` reads off a pack. Listed so a pack missing one is REPORTED
#: (``ui_incomplete``) rather than silently rendering blanks in the chrome.
UI_KEYS = (
    "label", "tagline", "nouns", "roster", "surfaces", "trials", "demo_steps",
    "demo_focus_node", "demo_as_of", "demo_question", "graph_vocabulary",
    "brain_suggested_fallback",
)


#: INT1 (2026-10-01): every route in this module requires an authenticated principal — the gateway
#: serves nothing anonymously except `/health` (`test_every_route_on_the_app_is_gated`).
_AUTH = [Depends(require_principal)]


def _refusal(code: str, message: str, status: int = 422) -> JSONResponse:
    return JSONResponse(status_code=status, content={"refusal": {
        "code": code, "message": message, "nearest_alternative": None}})


def _read_ui(root: Path, name: str) -> tuple[Optional[dict], Optional[str]]:
    """``(ui, reason_it_is_missing)``. Never raises — a malformed pack file must not 500 the
    chrome, but it must not be reported as an empty pack either."""
    path = root / name / UI_FILE
    if not path.is_file():
        return None, f"{name}/{UI_FILE} does not exist, so this pack declares no operator vocabulary"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        log.warning("pack %r: %s is not valid YAML: %s", name, UI_FILE, exc)
        return None, f"{name}/{UI_FILE} is not valid YAML"
    if not isinstance(data, dict):
        return None, f"{name}/{UI_FILE} top level must be a mapping"
    declared = data.get("pack")
    if declared is not None and str(declared) != name:
        # Same rule the ontology loader applies: the directory name is the pack's identity.
        return None, (f"{name}/{UI_FILE} declares pack {declared!r} but lives in directory "
                      f"{name!r}; the directory name is the pack's identity")
    return data, None


def _ontology_facts(name: str) -> dict[str, Any]:
    """What the ontology loader says about this pack. Empty ``{}`` if it will not load."""
    try:
        from databridge_graph.packs import load_pack

        pack = load_pack(name)
    except Exception as exc:                                         # noqa: BLE001
        log.warning("pack %r: ontology did not load: %s", name, exc)
        return {"ontology_error": str(exc)}
    return {
        "version": pack.version,
        "ontology_title": pack.title,
        "node_kind_count": len(pack.node_kinds),
        "edge_kind_count": len(pack.edge_kinds),
        "subject_kinds": sorted(pack.subject_kinds),
    }


def build_registry(root: Optional[Path] = None,
                   enabled: Optional[tuple[str, ...]] = None) -> dict[str, Any]:
    """Assemble the registry. Pure over the filesystem so a test can point it at a temp tree."""
    from databridge_graph.packs import available_packs, configured_packs, packs_root

    r = Path(root) if root is not None else packs_root()
    names = available_packs(r)
    if enabled is None:
        try:
            enabled = tuple(configured_packs(r))
        except Exception as exc:                                     # noqa: BLE001
            # A deployment with no binding is a refusal in the loader and must stay one here: a
            # registry that silently bound every pack on disk would make the vocabulary open.
            log.warning("no deployment binding: %s", exc)
            enabled = ()
    bound = tuple(enabled)

    packs: list[dict[str, Any]] = []
    for name in names:
        ui, ui_missing = _read_ui(r, name)
        facts = _ontology_facts(name)
        is_bound = name in bound
        entry: dict[str, Any] = {"id": name, **facts}
        if ui is not None:
            entry.update({k: ui[k] for k in UI_KEYS if k in ui})
            missing_keys = [k for k in UI_KEYS if k not in ui]
        else:
            missing_keys = list(UI_KEYS)
        surfaces = entry.get("surfaces") or ()
        entry["available"] = bool(is_bound and surfaces and ui is not None)
        if not entry["available"]:
            if ui_missing:
                note = ui_missing
            elif not is_bound:
                note = (f"not enabled by this deployment: the binding is "
                        f"{list(bound) or 'empty'}. Enable it in packs/deployment.yaml or via "
                        f"DATABRIDGE_PACKS; no code change is involved.")
            else:
                note = (f"{name}/{UI_FILE} declares no operator surfaces, so this pack has no "
                        f"screen to open. The platform surfaces work against whichever pack is "
                        f"active.")
            entry["unavailable_note"] = note
        if missing_keys:
            entry["ui_incomplete"] = missing_keys
        entry["enabled_by_deployment"] = is_bound
        packs.append(entry)

    # The default is the first BOUND pack that is actually available; a default naming a pack the
    # deployment did not bind would open the app on a vocabulary the engine will not extract in.
    default = next((p["id"] for p in packs if p["available"]), None)
    if default is None:
        default = next((p["id"] for p in packs if p["enabled_by_deployment"]), None)
    if default is None and packs:
        default = packs[0]["id"]
    return {
        "default_pack": default or "",
        "enabled_packs": list(bound),
        "packs_root": str(r),
        "packs": packs,
    }


def register_pack_routes(app: FastAPI) -> None:
    """Attach ``GET /packs`` and ``GET /packs/{name}``. Read-only."""
    install_auth_handler(app)

    @app.get("/packs", dependencies=_AUTH)
    def list_packs():
        try:
            return build_registry()
        except Exception:                                            # noqa: BLE001
            log.exception("pack registry could not be assembled")
            return _refusal("PACK_REGISTRY_UNAVAILABLE",
                            "the pack directory could not be read; see server logs", status=503)

    @app.get("/packs/{name}", dependencies=_AUTH)
    def get_pack(name: str):
        try:
            registry = build_registry()
        except Exception:                                            # noqa: BLE001
            log.exception("pack registry could not be assembled")
            return _refusal("PACK_REGISTRY_UNAVAILABLE",
                            "the pack directory could not be read; see server logs", status=503)
        for entry in registry["packs"]:
            if entry["id"] == name:
                return entry
        return _refusal("UNKNOWN_PACK",
                        f"no pack {name!r} in {registry['packs_root']}", status=404)
