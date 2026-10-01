"""Load the synthetic **government** estate's policy inputs so the tests run the production path.

`fixtures/packs/finance/roles.yaml` and `fixtures/packs/government/roles.yaml` are two revisions of
the same idea and **they are not the same file format**. `rolemodel.load_role_model` implements the
finance shape (v1): `roles` is a mapping of role name to body, grants are an `allow` mapping,
column policy is `deny_columns`/`column_masks`, and free text is named in
`column_classification.free_text_may_contain_personal_data`. The government file is **v2**: `roles`
is a *list* of objects with `id`, each carrying a list of `grants` with per-grant `columns`,
`withheld`, `masked` and a `row_scope` written against named `scopes` aliases.

So `load_role_model(government/roles.yaml)` raises today — `'roles' must be a mapping`. That is a
real gap in a shared module and it is **reported, not patched here** (workstream 3F owns
`sources/document.py` and this test module, nothing else).

This module is the same kind of translation `estate.py` performs for the finance DDL: it reads the
government file and renders it into the shape the current loader understands, changing nothing that
decides anything. Every mapping is written out below with the line of the estate it comes from,
because a policy translation that nobody can audit is worse than no policy at all.

## The translation, rule by rule

| v2 (government) | v1 (what the loader reads) | Note |
|---|---|---|
| `roles[].id` | the role's key | |
| `grants[].object` | `allow[source][entity]` | estate objects are source-level (`case_files`); the reader's entity names are supplied by `OBJECT_ENTITIES` |
| `grants[].purposes` | `purpose_scoped_grants[role][purpose].objects` | |
| `grants[].withheld[].transform` | `column_masks[column]` | each `withheld` entry names a `transform` (`classify_only`), whose declared `reason` is the code the cell carries |
| `grants[].masked` | `column_masks[column]` | the auditor's `pseudonym_engagement` / `initial_surname` / `year_only` |
| `grants[].row_scope.predicate` | `row_filters[object]` | `@alias` expanded from the estate's own `scopes` block |
| `grants[].row_scope.predicate: null` | `unscoped_objects` | the estate declaring an object deliberately unscoped |
| `denied[].object` | `deny_tables` / `deny_sources` | |
| `free_text_policy.covered_fields` | `column_classification.free_text_may_contain_personal_data` | expanded to entity paths — see below |
| `masks[].cell` | `masks[].cell_type` | a rename only |

## The two places the translation adds something, and why

**1. Free-text paths are expanded to the reader's entities.** The estate writes `case_files.content`
and `inspection_photos.ocr.blocks[].text` — source plus column, with the entity elided because each
of those sources has exactly one text surface. `RoleModel.is_free_text` builds
`f"{source}.{entity}.{column}"`, so the paths are expanded to `case_files.document.content` and
`inspection_photos.ocr_block.text`. **And every label column parsed out of a case-file body is
added**, because each one is a span *of* `content`: the estate's own rationale is "All free text
and all OCR is treated as sensitive AND untrusted by default", and a policy that denied the whole
page while disclosing the `NAME:` line off it would have denied nothing.

**2. `free_text_policy.exceptions` gets one entry: `adapter_service` under `ingest`.** The v2 file
has no `exceptions` key; it writes the exception as a grant instead —
``adapter_service → case_files → columns: "*" , trust_default: untrusted``. Without it nothing is
ever ingested, and `correspondence/INJECTIONS.md` is explicit that this is not a pass:

> *"A system that refuses to ingest these has not passed. It has just moved the failure somewhere
> harder to see."*

`free_text_exception=False` builds the model with the exception removed — the estate's literal
default — which is how the tests show a free-text column arriving as `Withheld` rather than as a
value.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import yaml

#: packages/adapter/tests/ -> packages/adapter -> packages -> databridge/
DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]
GOV_PACK = DATABRIDGE_ROOT / "fixtures" / "packs" / "government"
GOV_SOURCES_YAML = GOV_PACK / "sources.yaml"
GOV_ROLES_YAML = GOV_PACK / "roles.yaml"
INJECTIONS_MD = GOV_PACK / "correspondence" / "INJECTIONS.md"

#: The estate names its document sources at SOURCE level (`case_files`), because each has one
#: collection. The readers split two of them into entities, so the grant has to be expanded.
#: Declared here rather than inferred, so that adding an entity is a visible change.
OBJECT_ENTITIES: Mapping[str, tuple[str, tuple[str, ...]]] = {
    "case_files": ("case_files", ("document",)),
    "legacy_records.LEGACY-MASTER": ("legacy_records", ("legacy_master",)),
    "inspection_photos": ("inspection_photos", ("photo", "ocr_block")),
}

#: `free_text_policy.covered_fields` -> the reader's `source.entity.column` paths.
FREE_TEXT_PATHS: Mapping[str, tuple[str, ...]] = {
    "case_files.content": ("case_files.document.content",),
    "inspection_photos.ocr.blocks[].text": ("inspection_photos.ocr_block.text",),
}


def _load(path: Path) -> Mapping[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def _expand_scope(predicate: str, scopes: Mapping[str, Any]) -> str:
    """Substitute the estate's `@alias` scope references with their declared definitions.

    One level, deliberately. A nested alias (`@permits.my_inspections` selects over
    `@permits.my_caseload`) stays unexpanded and therefore uncompilable, which is the honest
    outcome: the estate's own `row_scope_compilation` note says a two-hop scope does not compile,
    and inventing an expansion here would hide the refusal the policy engine is supposed to make.
    """
    for alias, body in scopes.items():
        if alias in predicate:
            predicate = predicate.replace(alias, f"({body.get('definition')})")
    return predicate


def translate_roles(*, free_text_exception: bool = True,
                    extra_free_text: Sequence[str] = ()) -> dict:
    """Render `government/roles.yaml` (v2) into the mapping shape `load_role_model` reads.

    Returns a plain dict; `write_roles` puts it on disk because the loader takes a path.
    """
    src = _load(GOV_ROLES_YAML)
    scopes = dict(src.get("scopes") or {})

    masks: dict[str, dict] = {}
    for name, body in (src.get("masks") or {}).items():
        body = dict(body or {})
        masks[str(name)] = {
            "emit": body.get("emit", ""),
            # v2 spells it `cell`; the loader reads `cell_type`. A rename, nothing more.
            "cell_type": body.get("cell", "Withheld"),
            "reason": body.get("reason"),
            "note": body.get("note"),
        }

    roles: dict[str, dict] = {}
    grants: dict[str, dict] = {}
    for entry in (src.get("roles") or ()):
        name = str(entry["id"])
        allow: dict[str, set[str]] = {}
        column_masks: dict[str, str] = {}
        row_filters: dict[str, str] = {}
        unscoped: list[str] = []
        purposes: set[str] = set()
        per_purpose: dict[str, set[str]] = {}

        for grant in (entry.get("grants") or ()):
            obj = str(grant.get("object"))
            mapped = OBJECT_ENTITIES.get(obj)
            if mapped is None:
                # A relational object (`permits.applications`). Carried through verbatim so the
                # role model stays whole; the document adapter never reads it.
                source, _, entity = obj.partition(".")
                mapped = (source, (entity,) if entity else ())
            source, entities = mapped
            allow.setdefault(source, set()).update(entities)
            for purpose in (grant.get("purposes") or ()):
                purposes.add(str(purpose))
                per_purpose.setdefault(str(purpose), set()).update(
                    f"{source}.{e}" for e in entities)
            for withheld in (grant.get("withheld") or ()):
                column = str(withheld.get("column"))
                transform = str(withheld.get("transform") or "classify_only")
                if transform in masks:
                    column_masks[_column_name(column)] = transform
            for column, mask in (grant.get("masked") or {}).items():
                column_masks[_column_name(str(column))] = str(mask)
            scope = dict(grant.get("row_scope") or {})
            predicate = scope.get("predicate")
            for entity in entities:
                target = f"{source}.{entity}"
                if predicate:
                    row_filters[target] = _expand_scope(str(predicate), scopes)
                else:
                    unscoped.append(target)

        deny_tables: list[str] = []
        deny_sources: list[str] = []
        for denial in (entry.get("denied") or ()):
            obj = str(denial.get("object"))
            mapped = OBJECT_ENTITIES.get(obj)
            if mapped is not None:
                deny_sources.append(mapped[0])
            elif "." in obj:
                deny_tables.append(obj)
            else:
                deny_sources.append(obj)

        roles[name] = {
            "description": str(entry.get("description") or "").strip(),
            "purposes": sorted(purposes),
            "egress_ceiling": str(entry.get("egress_ceiling") or "INTERNAL"),
            "allow": {s: sorted(e) for s, e in allow.items()},
            "deny_tables": deny_tables,
            "deny_sources": deny_sources,
            "column_masks": column_masks,
            "row_filters": row_filters,
            "unscoped_objects": sorted(set(unscoped)),
            "obligations": [str(o) for o in (entry.get("obligations") or ())],
        }
        grants[name] = {p: {"objects": sorted(o)} for p, o in per_purpose.items()}

    free_text: list[str] = []
    for declared in ((src.get("free_text_policy") or {}).get("covered_fields") or ()):
        free_text.extend(FREE_TEXT_PATHS.get(str(declared), ()))
    free_text.extend(str(p) for p in extra_free_text)

    exceptions: list[dict] = []
    if free_text_exception:
        exceptions.append({
            "role": "adapter_service",
            "purposes": ["ingest"],
            "note": "the v2 grant `adapter_service -> case_files -> columns: \"*\"` is this "
                    "exception, written as a grant. INJECTIONS.md: a system that refuses to "
                    "ingest the planted payloads has not passed.",
        })

    return {
        "estate": "government",
        "version": int(src.get("version") or 2),
        "purposes": [{"id": p["id"], "description": p.get("description", "")}
                     for p in (src.get("purposes") or ())],
        "reason_codes": dict(src.get("reason_codes") or {}),
        "masks": masks,
        "roles": roles,
        "purpose_scoped_grants": grants,
        "free_text_policy": {"default": "withheld", "exceptions": exceptions},
        "column_classification": {
            "free_text_may_contain_personal_data": sorted(set(free_text)),
        },
    }


def _column_name(declared: str) -> str:
    """`ocr.blocks[].text` -> `text`, `LM-SURNAME` -> `lm_surname`, `content` -> `content`."""
    tail = declared.rsplit(".", 1)[-1]
    return tail.replace("-", "_").lower()


def write_roles(target: Path, **kw: Any) -> Path:
    """Write the translated role model where `load_role_model` can read it."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(translate_roles(**kw), sort_keys=True), encoding="utf-8")
    return target


#: Pack purpose -> government estate purpose. The packaged
#: `databridge_adapter/config/purpose_bindings.yaml` declares `walnut-finance-synthetic` only, and
#: that file is another workstream's; `load_purpose_bindings(path=...)` takes this one instead.
#:
#: `register.source_health` is DELIBERATELY UNBOUND, in the style of the finance file. The pack
#: declares it as "metadata-only reads that touch no subject data"; the government estate has no
#: counterpart, and the nearest candidates (`audit`, `control_testing`) are engagement-validated
#: purposes that carry obligations a health check does not discharge. An unbound purpose is a
#: refusal naming both files, which is a question for the estate's data owner.
PURPOSE_BINDINGS: Mapping[str, str] = {
    "register.graph_build": "ingest",
    "register.pointer_verification": "audit",
    "permits.assessment": "case_determination",
    "benefits.eligibility": "eligibility_determination",
    "benefits.case_review": "quality_assurance",
}


def write_purpose_bindings(target: Path) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump({
        "version": 1,
        "estates": {"government": {"pack": "government",
                                   "bindings": dict(PURPOSE_BINDINGS)}},
    }, sort_keys=True), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------------------
# the planted injections
# ---------------------------------------------------------------------------------------

def planted_injections() -> list[dict]:
    """The estate's own ground truth for what was planted where.

    `correspondence/INJECTIONS.md` is prose with a table, so it is parsed rather than restated: a
    second copy of the payloads in a test file would drift from the fixture, and a containment
    test that asserted against its own copy would prove nothing about the estate.
    """
    text = INJECTIONS_MD.read_text(encoding="utf-8")
    out: list[dict] = []
    for block in text.split("### INJ-")[1:]:
        ident = "INJ-" + block[:2]
        planted = _between(block, "**Planted at:** `", "`")
        surface = _between(block, "**Surface:** `", "`")
        payload = _between(block, "```text\n", "\n```")
        defence = _between(block, "**Expected defence:**", "\n")
        out.append({"id": ident, "surface": surface, "planted_at": planted,
                    "payload": (payload or "").strip(), "expected_defence": (defence or "").strip()})
    return out


def _between(text: str, start: str, end: str) -> Optional[str]:
    i = text.find(start)
    if i < 0:
        return None
    i += len(start)
    j = text.find(end, i)
    return text[i:j] if j >= 0 else None


def injections_for(source: str) -> list[dict]:
    """The payloads planted in one source, by the path prefix the estate records."""
    return [i for i in planted_injections() if (i["planted_at"] or "").startswith(source)]


def contract_entry(source_id: str) -> Mapping[str, Any]:
    """One source's raw `sources.yaml` entry — the estate's own declaration, unmediated."""
    for entry in (_load(GOV_SOURCES_YAML).get("sources") or ()):
        if str(entry.get("id")) == source_id:
            return entry
    raise KeyError(f"{GOV_SOURCES_YAML}: no source {source_id!r}")


def sidecar(image_id: str) -> Mapping[str, Any]:
    path = GOV_PACK / "inspection_photos" / "sidecars" / f"{image_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))
