"""`test_no_domain_branching` — the engine may not name a domain concept.

This generalises `test_no_issuer_branching` (`packages/intake/tests/test_rules_hardening.py`),
which enforces the narrower rule that no code branches on an issuer code. The wider rule, from
`00_SPINE.md` §4: **a domain is a pack; no domain is the product.** Adding an insurer was already
a YAML change; adding an *industry* must be adding a directory under `packs/`.

A convention would not survive a hurried session. So it is a test: a source scan over the engine
packages that fails if one of them names a term belonging to a vertical.

It lives in `packages/graph/tests/` rather than beside `test_no_issuer_branching`, because the
vocabulary it guards lives in `packages/graph` and `packages/intake` is being extracted into
`packs/healthcare/` in the next workstream. When that lands, the two tests should be read
together.

--------------------------------------------------------------------------------------
THE QUARANTINE, and why it is not a loophole
--------------------------------------------------------------------------------------
`packages/graph` still contains modules that ARE the healthcare vertical — the seed built from the
clinic fixtures, its history loader, and the surfaces written against them. Phase 1A moved the
*vocabulary* out of code; Phase 1B moves those modules out of `packages/` entirely, into
`packs/healthcare/`. Until then they are named here explicitly.

Three properties keep the list honest rather than decorative:

* every quarantined path must EXIST — a stale entry fails the test, so the list cannot rot;
* the list may only SHRINK — it must stay inside `_QUARANTINE_BASELINE`, a FIXED literal set, and
  under `MAX_QUARANTINED`, a fixed literal number (Codex f2 #8: this used to be
  `len(QUARANTINE)`, which no addition could ever exceed). Widening it therefore means editing the
  baseline itself, in this file, where a reviewer sees it — the test cannot stop a determined edit
  to the test, and does not claim to;
* nothing outside the list may name a domain term, and the engine spine may not so much as
  mention a vocabulary MEMBER (`NodeKind.PATIENT`), because naming a member is naming a domain.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]          # databridge/
ENGINE_ROOTS = (
    REPO / "packages" / "core" / "databridge_core",
    REPO / "packages" / "graph" / "databridge_graph",
)

#: Terms that belong to a vertical. Drawn from the three shipped packs plus the healthcare
#: vocabulary this repo grew up with. Deliberately NOT here: words a pack and the engine both use
#: in a domain-free sense — `document`, `claim`, `event`, `rule`, `party` — because banning those
#: would ban the meta-model itself. Matched case-insensitively on word boundaries.
DOMAIN_TERMS = (
    # healthcare
    "patient", "patients", "clinic", "clinics", "clinical", "parkway", "hack4health",
    "nric", "chit", "voucher", "screening", "insurer", "insurers", "payer",
    "referral", "referring", "doctor", "nurse", "questionnaire", "phlebotomy",
    # finance
    "borrower", "guarantor", "collateral", "covenant", "underwriting", "drawdown",
    "creditor", "debtor", "kyc",
    # government
    # "permits" is omitted deliberately: it is also an ordinary English verb, and banning it
    # would force the meta-model to avoid the plainest word for what AUTHORISES means.
    "applicant", "applicants", "claimant", "claimants", "permit", "parcel",
    "benefits", "case_file", "caseworker",
)

#: Modules that ARE the healthcare vertical and move to `packs/healthcare/` in Phase 1B.
#: This list may only shrink. Paths are relative to `databridge/`.
QUARANTINE = (
    "packages/graph/databridge_graph/clinic_seed.py",       # the seed built from clinic fixtures
    "packages/graph/databridge_graph/clinic_history.py",    # its bulk history loader
    "packages/graph/databridge_graph/analytics.py",         # counters written against the seed
    "packages/graph/databridge_graph/identity.py",          # the caveat copy, which is pack text
    "packages/graph/databridge_graph/node_sheet.py",        # the sheet's caveat copy
    "packages/graph/databridge_graph/query.py",             # per-kind narration
    "packages/graph/databridge_graph/sheet_export.py",      # sheet filenames and front matter
    "packages/graph/databridge_graph/__init__.py",          # re-exports the modules above
)
#: The baseline the quarantine may never leave, and its size. Both are LITERALS on purpose (Codex
#: f2 #8): a ceiling computed from the list it bounds is a tautology. Lower both when a module
#: leaves; never raise either.
_QUARANTINE_BASELINE = frozenset({
    "packages/graph/databridge_graph/clinic_seed.py",
    "packages/graph/databridge_graph/clinic_history.py",
    "packages/graph/databridge_graph/analytics.py",
    "packages/graph/databridge_graph/identity.py",
    "packages/graph/databridge_graph/node_sheet.py",
    "packages/graph/databridge_graph/query.py",
    "packages/graph/databridge_graph/sheet_export.py",
    "packages/graph/databridge_graph/__init__.py",
})
MAX_QUARANTINED = 8

_WORD = re.compile(r"\b(" + "|".join(sorted(DOMAIN_TERMS, key=len, reverse=True)) + r")\b",
                   re.IGNORECASE)
_MEMBER = re.compile(r"\b(?:Node|Edge)Kind\.[A-Z][A-Z_]*")


def _engine_files() -> list[Path]:
    quarantined = {(REPO / q).resolve() for q in QUARANTINE}
    out: list[Path] = []
    for root in ENGINE_ROOTS:
        for p in sorted(root.rglob("*.py")):
            if p.resolve() not in quarantined:
                out.append(p)
    return out


def _hits(path: Path, pattern: re.Pattern) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [f"{path.relative_to(REPO).as_posix()}:{i}: {line.strip()}"
            for i, line in enumerate(text.splitlines(), 1)
            if pattern.search(line)]


def test_quarantine_only_names_files_that_exist():
    """A stale entry would silently exempt nothing — or, worse, mask a rename."""
    missing = [q for q in QUARANTINE if not (REPO / q).is_file()]
    assert not missing, f"quarantine names paths that no longer exist: {missing}"


def test_quarantine_may_only_shrink():
    assert len(QUARANTINE) <= MAX_QUARANTINED
    assert len(set(QUARANTINE)) == len(QUARANTINE)
    added = sorted(set(QUARANTINE) - _QUARANTINE_BASELINE)
    assert not added, f"the quarantine grew beyond its fixed baseline: {added}"
    assert len(_QUARANTINE_BASELINE) <= MAX_QUARANTINED


def test_core_names_no_domain_concept_at_all():
    """`packages/core` carries no quarantine and never should — it is the provenance root."""
    hits: list[str] = []
    for p in sorted((REPO / "packages" / "core" / "databridge_core").rglob("*.py")):
        hits += _hits(p, _WORD)
    assert not hits, "databridge_core names a domain concept:\n" + "\n".join(hits)


def test_no_domain_branching():
    """The engine, minus the quarantined vertical modules, names no domain concept.

    If this fails on a line you believe is legitimate, the fix is almost never to widen the
    quarantine. It is either (a) the term belongs in a pack's `ontology.yaml`, or (b) the sentence
    can be written in the meta-model's words — `subject`, `document`, `organisation`, `event`.
    """
    hits: list[str] = []
    for p in _engine_files():
        hits += _hits(p, _WORD)
    assert not hits, (
        "engine code names a domain concept — move it to packs/<domain>/ontology.yaml:\n"
        + "\n".join(hits))


def test_engine_never_names_a_vocabulary_member():
    """`NodeKind.PATIENT` is a domain concept spelled in Python. The engine reasons about
    meta-kinds and pack declarations; only a pack's own modules name a member."""
    hits: list[str] = []
    for p in _engine_files():
        hits += _hits(p, _MEMBER)
    assert not hits, (
        "engine code names a closed-vocabulary member; ask the vocabulary for the meta-kind "
        "instead (`kind.meta`, `node_kinds_binding_to(...)`):\n" + "\n".join(hits))


#: The app surfaces that serve EVERY binding. `ENGINE_ROOTS` above stopped at `packages/`, which is
#: why `apps/mcp_server/brain_server.py` could branch on `NodeKind.PATIENT` / `EdgeKind.FOR_PATIENT`
#: unseen until E2E-2026-10-01 F-05 (`/brain/graph` 500 under DATABRIDGE_PACKS=workspace). Every
#: Python package under `apps/` is in scope — derived from the tree, so a new app is scanned the
#: day it lands. Only CODE tokens are scanned here (comments and strings may cite history); the
#: domain-WORD scan stays on the engine, since the apps still host the clinic product's copy.
APP_ROOT = REPO / "apps"


def _app_files() -> list[Path]:
    return sorted(p for p in APP_ROOT.rglob("*.py")
                  if "tests" not in p.parts and "node_modules" not in p.parts
                  and "__pycache__" not in p.parts)


_ENUMS = frozenset({"NodeKind", "EdgeKind"})


def _pack_kind_literals() -> frozenset[str]:
    """Every node/edge kind name a pack on disk declares, minus the meta-model's own words and the
    `common` pack's (those are the engine's shared vocabulary, not a domain's). Derived from the
    ontologies, so a new pack's kinds are guarded the day it lands (Codex f1 #10)."""
    from databridge_graph.meta import EdgeMetaKind, NodeMetaKind
    from databridge_graph.packs import available_packs, load_pack

    shared = {m.value for m in NodeMetaKind} | {m.value for m in EdgeMetaKind}
    names: set[str] = set()
    for name in available_packs():
        pack = load_pack(name)
        kinds = set(pack.node_kinds) | set(pack.edge_kinds)
        if name == "common":
            shared |= kinds
        else:
            names |= kinds
    return frozenset(names - shared)


def _mentions_kind(node) -> bool:
    import ast

    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and "kind" in sub.id.lower():
            return True
        if isinstance(sub, ast.Attribute) and "kind" in sub.attr.lower():
            return True
        if isinstance(sub, ast.Constant) and sub.value == "kind":
            return True
    return False


def _member_uses_in_code(path: Path, root: Path = REPO,
                         vocab: frozenset[str] | None = None) -> list[str]:
    """Every way executable code can name ONE pack's kind, found on the AST (so comments and
    strings that merely cite history never trip it). Codex f1 #10 showed the token scan missed:

    * an ALIAS — ``from ... import NodeKind as NK; NK.PATIENT`` or ``NK = NodeKind``;
    * a dotted path — ``schema.NodeKind.PATIENT``;
    * ``getattr(NodeKind, "PATIENT")``, ``NodeKind["PATIENT"]``, ``NodeKind("patient")``;
    * a comparison against a kind LITERAL — ``str(node.kind) == "patient"``,
      ``row["kind"] in ("patient", ...)`` — for any kind name a pack declares.
    """
    import ast

    vocab = _pack_kind_literals() if vocab is None else vocab
    tree = ast.parse(path.read_text(encoding="utf-8"))
    aliases = set(_ENUMS)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            aliases |= {a.asname or a.name for a in node.names if a.name in _ENUMS}

    def is_enum(e) -> bool:
        return ((isinstance(e, ast.Name) and e.id in aliases)
                or (isinstance(e, ast.Attribute) and e.attr in _ENUMS))

    def bindings(node):
        """``(target, value)`` pairs a statement binds: ``=`` (chained and tuple-unpacked),
        annotated ``=``, and the walrus."""
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, (ast.Tuple, ast.List)) and \
                        isinstance(node.value, (ast.Tuple, ast.List)):
                    yield from zip(t.elts, node.value.elts)
                else:
                    yield t, node.value
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            yield node.target, node.value

    # TRANSITIVE alias propagation (Codex f2 #8): `NK = NodeKind; K = NK; K.PATIENT` — iterate to
    # a fixpoint, so an alias of an alias of the enum is an alias of the enum. Name-level and
    # flow-insensitive, which over-approximates (a name later rebound to something else still
    # counts) — the safe direction for a guard. It is not a semantic proof: a kind built from DATA
    # (`NodeKind(name)`) is allowed by design, and code can always defeat a static scan it wants
    # to defeat; this catches the shapes a hurried edit produces.
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            for target, value in bindings(node):
                if isinstance(target, ast.Name) and target.id not in aliases and is_enum(value):
                    aliases.add(target.id)
                    changed = True

    def kind_literals(e) -> list[str]:
        return [c.value for c in ast.walk(e)
                if isinstance(c, ast.Constant) and isinstance(c.value, str) and c.value in vocab]

    hits: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and is_enum(node.value) and node.attr.isupper():
            hits.add((node.lineno, f"member {node.attr}"))
        elif isinstance(node, ast.Subscript) and (
                is_enum(node.value)
                or (isinstance(node.value, ast.Attribute) and is_enum(node.value.value))):
            # `NodeKind["X"]`, and `NodeKind.__members__["X"]` / `NodeKind._member_map_["X"]`
            hits.add((node.lineno, "enum subscript"))
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == "getattr" and node.args \
                    and is_enum(node.args[0]):
                hits.add((node.lineno, "getattr on a kind enum"))
            elif is_enum(f) and any(kind_literals(a) for a in node.args):
                hits.add((node.lineno, "constructs a kind from a pack literal"))
        elif isinstance(node, ast.Compare):
            found = [v for part in (node.left, *node.comparators) for v in kind_literals(part)]
            if found and _mentions_kind(node):
                hits.add((node.lineno, f"compares a kind with {sorted(set(found))}"))
    rel = path.relative_to(root).as_posix()
    return [f"{rel}:{line}: {what}" for line, what in sorted(hits)]


def test_app_surfaces_never_name_a_vocabulary_member():
    """A member attribute raises AttributeError under any binding that does not declare it, and a
    kind literal is a branch on one domain; either way an app naming one is a route that is wrong
    for every other pack. Ask the vocabulary instead (`active_vocabulary().is_subject(kind)`,
    `edge_kinds_binding_to(EdgeMetaKind.ABOUT)`)."""
    files = _app_files()
    assert files, "no app sources found — the scan below would be vacuous"
    assert _pack_kind_literals(), "no pack kind names were derived — the literal scan is vacuous"
    hits: list[str] = []
    for p in files:
        hits += _member_uses_in_code(p)
    assert not hits, "app code names a closed-vocabulary member:\n" + "\n".join(hits)


def test_the_app_member_scan_would_catch_the_f05_shape(tmp_path):
    """Pin that the scan fires on code and stays quiet on a comment or a string."""
    p = tmp_path / "x.py"
    p.write_text("# NodeKind.PATIENT in a comment\ns = 'EdgeKind.FOR_PATIENT'\n"
                 "ok = kind is NodeKind.PATIENT\n", encoding="utf-8")
    hits = _member_uses_in_code(p, root=tmp_path)
    assert len(hits) == 1 and "member PATIENT" in hits[0] and ":3:" in hits[0]


@pytest.mark.parametrize("src", [
    "from databridge_graph.kinds import NodeKind as NK\nx = NK.PATIENT\n",
    "from databridge_graph.kinds import NodeKind\nNK = NodeKind\nx = NK.PATIENT\n",
    "import databridge_graph.schema as schema\nx = schema.NodeKind.PATIENT\n",
    "from databridge_graph.kinds import NodeKind\nx = getattr(NodeKind, 'PATIENT')\n",
    "from databridge_graph.kinds import NodeKind\nx = NodeKind['PATIENT']\n",
    "from databridge_graph.kinds import NodeKind\nx = NodeKind('patient')\n",
    "ok = str(node.kind) == 'patient'\n",
    "ok = row['kind'] in ('patient', 'visit')\n",
    "ok = node.kind.value != 'for_patient'\n",
    # Codex f2 #8: transitive and indirect aliases, and the member map
    "from databridge_graph.kinds import NodeKind\nNK = NodeKind\nK = NK\nx = K.PATIENT\n",
    "from databridge_graph.kinds import NodeKind\nK = NK = NodeKind\nx = K.PATIENT\n",
    "from databridge_graph.kinds import NodeKind, EdgeKind\nA, B = NodeKind, EdgeKind\n"
    "x = A.PATIENT\n",
    "from databridge_graph.kinds import NodeKind\nK: type = NodeKind\nx = K.PATIENT\n",
    "from databridge_graph.kinds import NodeKind\nif (K := NodeKind):\n    x = K.PATIENT\n",
    "from databridge_graph.kinds import NodeKind\nx = NodeKind.__members__['PATIENT']\n",
])
def test_the_app_member_scan_catches_every_bypass_codex_named(tmp_path, src):
    """Codex f1 #10: each of these passed the old token scan."""
    p = tmp_path / "x.py"
    p.write_text(src, encoding="utf-8")
    assert _member_uses_in_code(p, root=tmp_path), src


@pytest.mark.parametrize("src", [
    "ok = str(node.kind) == 'document'\n",       # a meta-model word, not a domain's
    "ok = status == 'patient'\n",                 # a literal that is not compared with a kind
    "x = NodeKind(name)\n",                       # constructed from data, not a literal
])
def test_the_app_member_scan_leaves_the_meta_model_alone(tmp_path, src):
    p = tmp_path / "x.py"
    p.write_text(src, encoding="utf-8")
    assert not _member_uses_in_code(p, root=tmp_path), src


#: The shared brain surfaces serve EVERY binding, so they are held to the engine's WORD rule too,
#: on code identifiers and string constants (docstrings and comments may cite history). Codex f1
#: #10's second half: the domain-word scan was deliberately not run over `apps/`, and the seed
#: wiring in `brain_server.py` passed because of it.
BRAIN_SURFACES = (
    "apps/mcp_server/brain_server.py",
    "apps/mcp_server/brain_reader_server.py",
    "apps/mcp_server/brain_writer_server.py",
    "apps/gateway/databridge_gateway/llm.py",
)
#: Names these surfaces must keep for a caller they do not own. Shrink-only: delete an entry
#: the day its caller stops importing it. EMPTY since INT3: `main.py` imports `DEMO_SEED_ENV` /
#: `demo_seed_enabled` / `demo_seed_sources` (R-020 f2 hand-off 1 and 3).
BRAIN_SURFACE_COMPAT: frozenset = frozenset()
#: External contracts spelled as strings: the env var's name. Shrink-only. The seed's dotted
#: location left this list in Codex f2 #6 (it now lives in the pack's own `demo_seed.yaml`); the
#: two MCP servers' names left it when HC2 renamed them `databridge-reader` / `databridge-writer`.
BRAIN_SURFACE_STRINGS = frozenset({"WALNUT_LOAD_CLINIC_SEED"})
#: The FIXED baselines both allow-lists must stay inside (Codex f2 #8). Literals on purpose — see
#: `_QUARANTINE_BASELINE`. Remove an entry here when it leaves the list above; never add one.
_COMPAT_BASELINE: frozenset = frozenset()
_STRINGS_BASELINE = frozenset({"WALNUT_LOAD_CLINIC_SEED"})


def _code_texts(path: Path):
    """``(lineno, text)`` for every identifier, definition name, attribute, import alias and
    non-docstring string constant in ``path``."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                    ast.ClassDef))
                  and n.body and isinstance(n.body[0], ast.Expr)
                  and isinstance(n.body[0].value, ast.Constant)}
    for node in ast.walk(tree):
        text = None
        if isinstance(node, ast.Name):
            text = node.id
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            text = node.name
        elif isinstance(node, ast.Attribute):
            text = node.attr
        elif isinstance(node, ast.alias):
            text = node.asname or node.name
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings:
            text = node.value
        if text:
            yield getattr(node, "lineno", 0), text


def _texts(path: Path) -> set[str]:
    return {t for _, t in _code_texts(path)}


def _identifier_and_string_hits(path: Path, root: Path = REPO) -> list[str]:
    hits: list[str] = []
    for lineno, text in _code_texts(path):
        if text in BRAIN_SURFACE_COMPAT or text in BRAIN_SURFACE_STRINGS:
            continue
        if _WORD.search(text.replace("_", " ")):
            hits.append(f"{path.relative_to(root).as_posix()}:{lineno}: {text[:60]!r}")
    return hits


def test_the_brain_surface_allow_lists_never_grow():
    for name, live, base in (("BRAIN_SURFACE_COMPAT", BRAIN_SURFACE_COMPAT, _COMPAT_BASELINE),
                             ("BRAIN_SURFACE_STRINGS", BRAIN_SURFACE_STRINGS,
                              _STRINGS_BASELINE)):
        added = sorted(live - base)
        assert not added, f"{name} grew beyond its fixed baseline: {added}"
    # non-vacuity: each exemption is still USED, so a dead one is noticed and removed
    used = {t for f in BRAIN_SURFACES for t in _texts(REPO / f)}
    stale = sorted((BRAIN_SURFACE_COMPAT | BRAIN_SURFACE_STRINGS) - used)
    assert not stale, f"allow-list entries no brain surface uses any more — delete them: {stale}"


def test_the_brain_surfaces_name_no_domain_concept_in_code():
    missing = [f for f in BRAIN_SURFACES if not (REPO / f).is_file()]
    assert not missing, f"stale entries: {missing}"
    hits: list[str] = []
    for f in BRAIN_SURFACES:
        hits += _identifier_and_string_hits(REPO / f)
    assert not hits, "a shared brain surface names a domain concept:\n" + "\n".join(hits)


def test_the_brain_surface_scan_would_catch_a_domain_identifier(tmp_path):
    p = tmp_path / "brain_x.py"
    p.write_text('"""A docstring may say patient."""\ndef load_patient_seed():\n    pass\n',
                 encoding="utf-8")
    hits = _identifier_and_string_hits(p, root=tmp_path)
    assert len(hits) == 1 and "load_patient_seed" in hits[0]


@pytest.mark.parametrize("term", ["patient", "permit", "collateral"])
def test_the_scanner_would_actually_catch_something(tmp_path, term):
    """A scan that cannot fail is worse than none — pin that the pattern matches."""
    p = tmp_path / "x.py"
    p.write_text(f"# a comment about the {term} record\n", encoding="utf-8")
    assert _WORD.search(p.read_text(encoding="utf-8"))


def test_the_scanner_does_not_ban_the_meta_models_own_words(tmp_path):
    p = tmp_path / "x.py"
    p.write_text("# document, entity, organisation, event, rule, agreement, subject, party\n",
                 encoding="utf-8")
    assert not _WORD.search(p.read_text(encoding="utf-8"))
