"""IR → graph extraction (T08 Part 1; 04_PRD §5.3 — the LLM schema-constrained path).

Takes any IR already produced by C12/C13/C14 (``DocumentRecord``; email is a DocumentRecord with
``email_part`` block provenance) and runs C15-style schema-constrained extraction to pull typed
**entities + relations** (closed vocab, ADR-0008) into the bi-temporal graph.

**This is the single prose → graph path.** Every caller that turns text into graph rows — the
``/brain/ingest`` route, the MCP writer, the MCP ingestion pipeline (``packages/mcp_ingest``) —
calls :func:`extract_graph`. There is no second extractor to keep in step.

Closed vocabulary, enforced twice (KNOWLEDGE_SYSTEM_PRINCIPLES §6 Batch 1 "A"):

  * **in the request.** The response schema is built AT CALL TIME from the vocabulary in force:
    ``ExtractedEntity.kind`` and ``ExtractedRelation.rel_type`` carry a JSON-Schema ``enum`` of
    exactly the bound kinds (determination kinds are never offered). A backend with guided
    decoding is grammar-constrained by it; every other backend gets it in the prompt (harness L1).
  * **on the way back.** Parsing is deliberately LENIENT — the schema advertises the enum, but an
    off-ontology value still parses — so that one stray kind becomes a named line in ``refused``
    rather than a validation failure that throws the whole document away. Nothing off-ontology is
    coerced to a nearby kind: an unknown node kind, an unknown edge type, an identity
    DETERMINATION, and an edge whose endpoint kinds the ontology's ``src``/``dst`` do not allow
    are each refused with the reason.

Anti-fabrication wall (04_PRD §5.3, the same shape as C15's grounding wall):
  * every entity MUST name text that appears in the source as a whole token, EXACTLY up to case
    and whitespace (no similarity ratio — R-017 #2); an entity whose name does not appear is
    REFUSED (it was invented). A ``canonical_id`` is grounded separately, with its own pointer,
    or dropped and named in ``unresolved``;
  * a relation must connect two UNAMBIGUOUS grounded entities, and its nominated evidence
    passage must be a bounded span that itself mentions both, else REFUSED (R-017 #4, #5);
  * a SUBJECT mention is never merged into another record (R-017 #1) — see :func:`extract_graph`.
Each surviving entity/relation gets a ``doc_span`` SourcePointer (document id + char span, plus
the capture digest when the caller supplies one), so nothing ungrounded can reach the store.

The model call goes through the Harness (``task_class="entity_extraction"``). In CI it is driven
by a FakeBackend gold handler (``gold_extraction_handler``) — the deterministic plumbing proof;
the accuracy number on a real model is the separate EV-BRAIN run.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Annotated, Optional, Sequence

from pydantic import BaseModel, Field, WithJsonSchema, create_model

from databridge_core import SourcePointer
from databridge_harness import Harness

from .attestation import is_determination_kind
from .merge import upsert_edge, upsert_node
from .meta import ATTESTED_META_KINDS
from .ontology_state import currently_deprecated
from .packs import Vocabulary, active_vocabulary
from .schema import EdgeKind, GraphEdge, GraphNode, NodeKind, node_id
from .store import GraphStore

# --- structured extraction contract (what the model returns) ----------------------
# These are the LENIENT shapes: `kind`/`rel_type` are plain strings, so an off-ontology value
# parses and is refused by name below. What the MODEL is shown is the constrained schema that
# `extraction_response_model` derives from them at call time.
class ExtractedEntity(BaseModel):
    name: str
    kind: str                              # checked against the vocabulary in force, else refused
    char_start: int
    char_end: int
    canonical_id: Optional[str] = None     # email / domain when the model can supply one


class ExtractedRelation(BaseModel):
    src_name: str
    rel_type: str                          # checked against the vocabulary in force, else refused
    dst_name: str
    evidence_char_start: int
    evidence_char_end: int


class ExtractionDraft(BaseModel):
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relations: list[ExtractedRelation] = Field(default_factory=list)


@dataclass
class GraphExtractionResult:
    entities: list[GraphNode] = field(default_factory=list)
    relations: list[GraphEdge] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)        # human-readable refusal reasons
    upserts: list = field(default_factory=list)             # UpsertOutcome when a store is given
    #: Exactly what the response schema offered the model — the closed vocabulary of this call.
    #: Recorded so a reviewer (and a test) can see the enum rather than trust that it was sent.
    node_kinds_offered: tuple[str, ...] = ()
    edge_kinds_offered: tuple[str, ...] = ()
    #: Spine §5.8 — who produced this extraction: ``{agent, agent_version, task_class, model,
    #: prompt_version, run_id, at, cached}``, read from the harness result. ``None`` only when no
    #: model call was made (an empty vocabulary). Every emitted pointer carries the same facts
    #: (minus ``at``) in its ``derivation``, so the graph rows themselves say which model wrote them.
    produced_by: Optional[dict] = None
    #: Values the model supplied that did not survive grounding but did not sink their entity —
    #: today, a ``canonical_id`` that does not appear verbatim in the source. Named, never guessed.
    unresolved: list[str] = field(default_factory=list)
    #: Existing SUBJECT nodes an extracted subject mention MIGHT be (same identifier, or same
    #: label). A candidate, never a merge: every row carries ``requires_human_selection=True`` and
    #: nothing in this module links them (CLAUDE.md hard constraint 1; review R-017 #1).
    subject_candidates: list[dict] = field(default_factory=list)


# --- the vocabulary of one call ---------------------------------------------------
@dataclass(frozen=True)
class ExtractionVocabulary:
    """The closed vocabulary one ``extract_graph`` call runs under.

    ``node_kinds`` is the intersection of the requested vocabulary with the BOUND one: a
    ``GraphNode`` can only be constructed with a kind the deployment binding declares, so an
    override may narrow the vocabulary but never widen it. ``edges`` holds the BOUND
    declarations of the requested names — their ``src``/``dst`` are what the endpoint check
    reads, so taking them from the override would let it widen the endpoint rules (R-017 #3).
    """

    node_kinds: tuple[str, ...]
    edge_kinds: tuple[str, ...]            # offered to the model (determinations excluded)
    edges: dict                            # every declared edge name → EdgeKindDecl
    descriptions: dict                     # kind name → description, for the prompt


def extraction_vocabulary(vocabulary: Optional[Vocabulary] = None) -> ExtractionVocabulary:
    """Resolve the vocabulary for one call. Read per call, never at import: the deployment
    binding decides which packs are enabled, and a module-level snapshot would freeze whichever
    were bound at first import."""
    bound = active_vocabulary()
    v = vocabulary if vocabulary is not None else bound
    # A kind the owner removed on /ontology is DEPRECATED, not deleted (see ontology_state): it
    # stays declared so existing rows read back, but it is never offered or accepted again.
    dep_nodes, dep_edges = currently_deprecated(bound.packs)
    node_kinds = tuple(sorted(n for n in v.node_kinds
                              if n in bound.node_kinds and n not in dep_nodes))
    # Review R-017 finding 3: the override is a set of REQUESTED NAMES, never a source of
    # declarations. This used to keep the override's `EdgeKindDecl`, so an override carrying a
    # bound edge name with `src: ["*"]` (or a different `binds_to`) passed the name intersection
    # and then WIDENED the endpoint rule the check below reads. Every declaration — endpoints,
    # meta-kind, description — now comes from the deployment binding; an override can only narrow.
    edges = {n: bound.edge_kinds[n] for n in v.edge_kinds
             if n in bound.edge_kinds and n not in dep_edges}
    offered = tuple(sorted(n for n, d in edges.items() if d.binds_to not in ATTESTED_META_KINDS))
    descriptions = {n: bound.node_kinds[n].description for n in node_kinds
                    if bound.node_kinds[n].description}
    descriptions.update({n: d.description for n, d in edges.items() if d.description})
    return ExtractionVocabulary(node_kinds=node_kinds, edge_kinds=offered, edges=edges,
                                descriptions=descriptions)


@lru_cache(maxsize=64)
def extraction_response_model(node_kinds: tuple[str, ...],
                              edge_kinds: tuple[str, ...]) -> type[BaseModel]:
    """The response model sent to the harness: the lenient shapes above, with the kind fields'
    JSON Schema replaced by an ``enum`` of exactly ``node_kinds`` / ``edge_kinds``.

    ``WithJsonSchema`` changes what the schema SAYS without changing what validation ACCEPTS —
    that split is the design (see the module docstring). Cached per vocabulary, because the schema
    feeds the harness cache key and must be byte-stable across calls with the same binding.
    """
    kind_t = Annotated[str, WithJsonSchema({"type": "string", "enum": list(node_kinds)})]
    rel_t = Annotated[str, WithJsonSchema({"type": "string", "enum": list(edge_kinds)})]
    entity = create_model("ExtractedEntity", __base__=ExtractedEntity, kind=(kind_t, ...))
    relation = create_model("ExtractedRelation", __base__=ExtractedRelation, rel_type=(rel_t, ...))
    return create_model(
        "ExtractionDraft", __base__=ExtractionDraft,
        entities=(list[entity], Field(default_factory=list)),
        relations=(list[relation], Field(default_factory=list)))


def _prompt(ev: ExtractionVocabulary) -> str:
    def described(name: str) -> str:
        d = ev.descriptions.get(name)
        return f"{name} — {d}" if d else name

    node_lines = "\n".join(f"  - {described(n)}" for n in ev.node_kinds)
    edge_lines = "\n".join(
        f"  - {described(n)}  (from: {', '.join(ev.edges[n].src) or '-'}; "
        f"to: {', '.join(ev.edges[n].dst) or '-'})" for n in ev.edge_kinds)
    return (
        "Extract a knowledge graph from the document. Return entities and relations.\n"
        f"Entity kinds (use ONLY these):\n{node_lines}\n"
        f"Relation types (use ONLY these, and only between the listed kinds; '*' means any "
        f"entity kind):\n{edge_lines or '  (none)'}\n"
        "Every entity MUST include char_start/char_end offsets into the document text where its "
        "name appears; `name` must be copied exactly as written there, and `canonical_id` only "
        "when that identifier is itself written in the document. Every relation MUST include "
        f"evidence offsets of the one passage (at most {MAX_EVIDENCE_CHARS} characters) that "
        "states it, and that passage must contain both entity names. If something does not fit "
        "one of these kinds, leave it out — never invent a type, never force a near fit, and "
        "never invent a fact. Do NOT resolve pronouns.")


def doc_text(doc) -> str:
    """Flatten a DocumentRecord's blocks into one text the offsets index into."""
    return "\n".join(b.text for b in doc.blocks)


def _wordch(c: str) -> bool:
    return c.isalnum() or c == "_"


def _fold(s: str) -> str:
    """The only normalisation grounding permits: case and runs of whitespace. Both are lossless
    for identity — 'loh  wei\\nming' IS 'Loh Wei Ming' — and neither can turn one name into
    another, which is what a similarity ratio did (review R-017 #2: 'Jon Smith' grounded
    'John Smith')."""
    return " ".join(s.split()).casefold()


def _name_pattern(name: str) -> str:
    return r"\s+".join(re.escape(tok) for tok in name.split())


def _boundary_find(text: str, name: str, lo: int = 0,
                   hi: Optional[int] = None) -> Optional[tuple[int, int]]:
    """Locate ``name`` in ``text[lo:hi]`` as a whole token (word boundaries against the FULL
    text), case- and whitespace-insensitive. Returns None if the name does not appear as an
    identity — NOT as a substring of a larger word. This is the C15/C17 lesson: never ground by
    containment ('man' must not ride inside 'management')."""
    if not name.split():
        return None
    pattern = re.compile(r"(?<!\w)" + _name_pattern(name) + r"(?!\w)", re.IGNORECASE)
    m = pattern.search(text, lo, len(text) if hi is None else hi)
    return (m.start(), m.end()) if m else None


def _count_mentions(text: str, name: str, lo: int, hi: int) -> list[tuple[int, int]]:
    pattern = re.compile(r"(?<!\w)" + _name_pattern(name) + r"(?!\w)", re.IGNORECASE)
    return [(m.start(), m.end()) for m in pattern.finditer(text, lo, hi)]


#: Characters that continue an identifier token (email local part / domain / handle). A candidate
#: occurrence flanked by one of these is a FRAGMENT of a longer identifier, not the identifier.
_IDENT_CONT = r"[\w.@+\-]"


def _identifier_occurrences(text: str, ident: str, lo: int = 0,
                            hi: Optional[int] = None) -> list[tuple[int, int]]:
    """Every occurrence of ``ident`` in ``text[lo:hi]`` as a COMPLETE identifier token.

    Review R-019 t06 H2: the word-boundary rule :func:`_boundary_find` uses for names treats ``.``,
    ``-`` and ``@`` as boundaries, so ``user@example.com`` grounded inside
    ``user@example.com.attacker`` and ``foo.example.com`` inside ``team.foo.example.com``. Here an
    occurrence may not be preceded by an identifier character, nor followed by one — except a
    single trailing ``.`` that ends a sentence (a ``.`` followed by a word character continues
    the token and is refused). Case-insensitive; no whitespace folding (an identifier has none)."""
    ident = ident.strip()
    if not ident or any(c.isspace() for c in ident):
        return []
    pattern = re.compile(r"(?<!" + _IDENT_CONT + r")" + re.escape(ident)
                         + r"(?![\w@+\-])(?!\.[\w])", re.IGNORECASE)
    return [(m.start(), m.end())
            for m in pattern.finditer(text, lo, len(text) if hi is None else hi)]


def _identifier_near(text: str, ident: str, mention: tuple[int, int]):
    """The occurrence of ``ident`` closest to the grounded ``mention`` such that the two fit in
    ONE passage of at most :data:`MAX_EVIDENCE_CHARS` → ``((s, e), None)``, else
    ``(None, reason)``.

    Review R-019 t06 H1: an identifier found ANYWHERE in the document used to be attached to
    whichever entity the model named, so Bob's address written once could become Alice's. The
    identifier must now be written in the same bounded passage as the mention it is attributed
    to; attribution inside that passage remains the model's claim (for a subject it is only ever
    a candidate for a human)."""
    gs, ge = mention
    hits = _identifier_occurrences(text, ident)
    if not hits:
        return None, "does not appear in the source as a complete identifier"
    near = [(s, e) for s, e in hits if max(ge, e) - min(gs, s) <= MAX_EVIDENCE_CHARS]
    if not near:
        return None, (f"appears only outside one passage ({MAX_EVIDENCE_CHARS} chars) of the "
                      f"mention it was attributed to")
    return min(near, key=lambda se: (max(ge, se[1]) - min(gs, se[0]), se[0])), None


def _span_grounds(text: str, name: str, start: int, end: int) -> Optional[tuple[int, int]]:
    """Return the (start, end) whose text IS ``name``, or None if ``name`` is not in the source.

    EXACT up to :func:`_fold` (case, whitespace) and boundary-aware (no substring rides), and
    length-guarded (a 1-char "name" is never an entity). Tolerant of model OFFSET drift — the
    value must be real, the offset need not be exact: the model's span is used only when its text
    is the name; otherwise the first whole-token occurrence of the name is used. It is never
    tolerant of VALUE drift: until review R-017 #2 a ≥0.9 similarity ratio accepted a span reading
    'Jon Smith' for the name 'John Smith', so the node said one name while its pointer resolved to
    another. None ⇒ not present ⇒ fabrication ⇒ refuse. Boundaries are checked against the FULL
    text (never the isolated slice, which would fake a boundary for 'man' carved out of
    'management')."""
    name = name.strip()
    if len(name) < 2:                                  # a 1-char token is not a groundable entity
        return None
    if 0 <= start < end <= len(text):
        before_ok = start == 0 or not _wordch(text[start - 1])
        after_ok = end == len(text) or not _wordch(text[end])
        if before_ok and after_ok and _fold(text[start:end]) == _fold(name):
            return start, end
    return _boundary_find(text, name)


#: The widest passage one relation's evidence may span. A relation is a claim ONE passage makes;
#: evidence wider than this is a pointer that localises nothing (the old whole-document fallback
#: in a smaller coat) and is refused rather than trimmed. Sized to a long paragraph, and wide
#: enough for `mcp_ingest`'s cross-chunk bridge (mentions up to its BRIDGE_MAX_GAP apart, plus the
#: two names) — a caller that bridges wider must import this constant, not outgrow it.
MAX_EVIDENCE_CHARS = 800


def _relation_evidence(text: str, src_name: str, dst_name: str, start: int, end: int,
                       src_span: Optional[tuple[int, int]] = None,
                       dst_span: Optional[tuple[int, int]] = None):
    """The smallest span INSIDE the model's nominated evidence passage ``[start, end)`` holding a
    whole-token mention of BOTH endpoint names → ``((s, e), None)``, else ``(None, reason)``.

    Review R-017 #4: this used to IGNORE the evidence offsets and pair the first document-wide
    occurrence of each name, so 'Alice' in paragraph one and 'Acme' ten paragraphs later grounded
    an 'Alice works_for Acme' edge no passage asserts. Now the nominated passage must be valid,
    bounded (:data:`MAX_EVIDENCE_CHARS`) and must itself contain both mentions; nothing is
    synthesised from independent occurrences."""
    if not (0 <= start < end <= len(text)):
        return None, f"evidence offsets [{start}, {end}) are not a span of the source"
    if end - start > MAX_EVIDENCE_CHARS:
        return None, (f"evidence passage is {end - start} chars, wider than one passage "
                      f"({MAX_EVIDENCE_CHARS}) — it would localise nothing")
    # A pinned span (an endpoint disambiguated by its grounded mention) is the ONLY occurrence of
    # that endpoint the evidence may use — another occurrence of the same name is another mention.
    a = ([src_span] if src_span and start <= src_span[0] and src_span[1] <= end else [])         if src_span else _count_mentions(text, src_name, start, end)
    b = ([dst_span] if dst_span and start <= dst_span[0] and dst_span[1] <= end else [])         if dst_span else _count_mentions(text, dst_name, start, end)
    if not a or not b:
        missing = [n for n, hits in ((src_name, a), (dst_name, b)) if not hits]
        return None, f"the nominated evidence passage does not mention {missing}"
    best = min(((min(x[0], y[0]), max(x[1], y[1])) for x in a for y in b),
               key=lambda se: (se[1] - se[0], se[0]))
    return best, None


# --- §5.8 produced_by ------------------------------------------------------------
AGENT_NAME = "extractor"
AGENT_VERSION = "3"      # bump when the grounding rules change what a given draft yields


def _prompt_version(system_prompt: str) -> str:
    """Content address of the exact system prompt sent. Computed, never written down: the prompt
    is built from the bound vocabulary, so two bindings are two prompt versions."""
    return "sha256:" + hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16]


def _produced_by(result, prompt_version: str, run_id: Optional[str]) -> dict:
    """Spine §5.8 from the harness event. A fact the event does not carry is ``None`` and named in
    ``missing`` — never filled in. (Review R-019 t06 M2: an absent ``created_at`` used to become
    "now", a production time the record then asserted without having observed it.)"""
    event = getattr(result, "event", None)
    at = getattr(event, "created_at", None)
    pb = {"agent": AGENT_NAME, "agent_version": AGENT_VERSION,
          "task_class": getattr(event, "task_class", None) or "entity_extraction",
          "model": getattr(event, "model_used", None),
          "prompt_version": prompt_version, "run_id": run_id,
          "at": (at.isoformat() if hasattr(at, "isoformat") else str(at)) if at is not None
          else None,
          "cached": getattr(event, "cached", None)}
    pb["missing"] = [k for k in ("model", "run_id", "at", "cached") if pb[k] is None]
    return pb


def _derivation(pb: dict) -> str:
    """The ``produced_by`` facts stamped on every pointer, minus ``at``/``cached`` — those change
    on every re-run, and a pointer that differs per run would turn idempotent corroboration into
    one new pointer per re-extraction."""
    return (f"produced_by agent={pb['agent']}@{pb['agent_version']} "
            f"task_class={pb['task_class']} model={pb['model']} "
            f"prompt_version={pb['prompt_version']}")


def extract_graph(doc, harness: Harness, *, store: Optional[GraphStore] = None,
                  valid_from=None, system_from=None, seed: int = 7,
                  vocabulary: Optional[Vocabulary] = None,
                  capture_digest: Optional[str] = None,
                  trust=None, run_id: Optional[str] = None) -> GraphExtractionResult:
    """Extract a grounded, ontology-closed subgraph from ``doc`` and (optionally) upsert it.

    ``vocabulary``      narrows the closed vocabulary for this call (default: the bound one). It
                        can never widen it — see :class:`ExtractionVocabulary`.
    ``capture_digest``  content address of the captured bytes ``doc`` was parsed from; stamped on
                        every pointer this call emits. Falls back to
                        ``doc.source_meta["capture_digest"]``; ``None`` when neither is given
                        (never invented).
    ``trust``           the ``Trust`` label for every emitted pointer (e.g. ``UNTRUSTED`` for
                        third-party MCP content). ``None`` leaves it unlabelled, as before.
    ``run_id``          recorded in ``produced_by`` (spine §5.8) when the caller has one.

    Every pointer is ``kind="doc_span"`` with ``source_id = document_id = doc.document_id`` and
    the grounded ``char_start``/``char_end`` into :func:`doc_text` — so a caller can resolve it
    with ``doc_text(doc)[p.char_start:p.char_end]``.

    **Subjects are never merged here** (CLAUDE.md hard constraint 1; reviews R-017 #1, R-019 C1/C2).
    A node of a subject kind gets a MENTION id — ``<kind>:<name>@<document_id>#<start>-<end>``,
    the grounded span offset by ``source_meta["char_offset"]`` when the caller extracts a document
    in chunks — and no ``canonical_id``, and is upserted ``id_only``: it can re-sight itself and
    match nothing else, so two same-named people in one document stay two nodes. A grounded identifier is kept as the ``extracted_identifier`` attribute,
    and existing subjects that share it (or the label) are listed in ``subject_candidates`` for a
    human to select — a candidate, never a link.
    """
    text = doc_text(doc)
    ev = extraction_vocabulary(vocabulary)
    bound = active_vocabulary()
    meta = getattr(doc, "source_meta", None) or {}
    digest = capture_digest
    if digest is None:
        digest = meta.get("capture_digest")
    try:
        char_offset = max(0, int(meta.get("char_offset") or 0))
    except (TypeError, ValueError):
        char_offset = 0
    # FIN-07: the source item's own identity — its id, its uri and the digest of the bytes read.
    item_scope = hashlib.sha256("\x1f".join(
        (str(doc.document_id), str(meta.get("uri") or ""), str(digest or ""))).encode("utf-8")
    ).hexdigest()[:24]
    out = GraphExtractionResult(node_kinds_offered=ev.node_kinds,
                                edge_kinds_offered=ev.edge_kinds)
    if not ev.node_kinds:
        # No model call at all: an empty enum would ask the model to extract nothing, and a
        # model asked for nothing in a closed schema is a model asked to improvise.
        out.refused.append("no node kinds are bound in this deployment's vocabulary — nothing "
                           "can be extracted (bind a pack, e.g. DATABRIDGE_PACKS=workspace)")
        return out

    response_model = extraction_response_model(ev.node_kinds, ev.edge_kinds)
    system_prompt = _prompt(ev)
    sys_msg = {"role": "system", "content": system_prompt}
    user_msg = {"role": "user", "content": f"DOCUMENT TEXT:\n{text}"}
    result = harness.complete_structured(
        "entity_extraction", [sys_msg, user_msg], response_model, seed=seed,
        source_id=doc.document_id)
    draft = result.parsed
    out.produced_by = _produced_by(result, _prompt_version(system_prompt), run_id)
    derivation = _derivation(out.produced_by)

    vf = valid_from or doc.snapshot.as_of
    sf = system_from or (store.now() if store is not None else doc.snapshot.as_of)

    def pointer(start: int, end: int) -> SourcePointer:
        return SourcePointer(source_id=doc.document_id, kind="doc_span",
                             document_id=doc.document_id, char_start=start, char_end=end,
                             capture_digest=digest, trust=trust, derivation=derivation)

    # --- entities: closed-vocab + grounding wall --------------------------------
    offered_nodes = set(ev.node_kinds)
    # name → every DISTINCT node that name was extracted as. A list, not a dict slot: review
    # R-017 #5 found the last same-named entity silently overwrote the earlier ones, so every
    # relation naming 'Jordan' went to whichever 'Jordan' the model listed last.
    by_name: dict[str, dict[str, GraphNode]] = {}
    subject_identifier: dict[str, Optional[str]] = {}
    for ent in draft.entities:
        if ent.kind not in offered_nodes:
            out.refused.append(
                f"entity {ent.name!r}: unknown kind {ent.kind!r} — not in the closed vocabulary "
                f"(REFUSED, not coerced)")
            continue
        kind = NodeKind(ent.kind)
        grounded = _span_grounds(text, ent.name, ent.char_start, ent.char_end)
        if grounded is None:
            out.refused.append(f"entity {ent.name!r}: name not found in source (REFUSED — fabrication)")
            continue
        gs, ge = grounded
        name = " ".join(ent.name.split())     # whitespace-normalised; case as the model gave it
        # canonical_id is an IDENTITY value: it gets its own grounding, and its own pointer, or it
        # is dropped. Review R-017 #2: a model-supplied identifier used to become the node id and
        # the exact-merge key with only the NAME's span behind it.
        cid, cid_ptr = None, None
        if ent.canonical_id and ent.canonical_id.strip():
            # A complete identifier token (R-019 t06 H2), in ONE bounded passage with the mention
            # it is attributed to (R-019 t06 H1) — else dropped and named.
            hit, why_not = _identifier_near(text, ent.canonical_id.strip(), (gs, ge))
            if hit is None:
                out.unresolved.append(
                    f"entity {name!r}: canonical_id {ent.canonical_id!r} {why_not} — dropped "
                    f"(never used to build an id or drive a merge)")
            else:
                cid, cid_ptr = text[hit[0]:hit[1]], pointer(*hit)
        if bound.is_subject(ent.kind) or bound.is_person(ent.kind):
            # IDN (E2E run 5 F-04): a PERSON kind the pack forgot to declare a subject is a
            # person all the same (`Vocabulary.person_kinds`) and gets the same scoping.
            # A SUBJECT mention is scoped to its GROUNDED MENTION: document + span (review
            # R-019 t06 C2 — scoping by name@document fused two different same-named people in
            # one document). Its id cannot collide with any other mention, it carries no
            # canonical_id, and it is upserted id-only, so no step of `upsert_node` can fuse it
            # into another record. The grounded identifier survives as an ordinary attribute —
            # evidence for a human, not a merge key. `char_offset` (source_meta) is where this
            # text starts inside its document, for a caller that extracts a document in chunks.
            ms, me = gs + char_offset, ge + char_offset
            nid = node_id(kind, f"{name.lower()}@{doc.document_id}#{ms}-{me}")
            attrs, attr_sources = {}, {}
            if cid is not None:
                attrs, attr_sources = {"extracted_identifier": cid}, {"extracted_identifier": cid_ptr}
            node = GraphNode(id=nid, kind=kind, label=name, attrs=attrs,
                             attr_sources=attr_sources, sources=[pointer(gs, ge)],
                             valid_from=vf, system_from=sf)
        elif cid is None and bound.is_document_kind(ent.kind):
            # FIN-07: a DOCUMENT mention without a grounded identifier of its own is scoped to the
            # SOURCE ITEM it was read from (item id + uri + content digest). Its title alone is not
            # its identity: 95 filings from different companies titled "Annual Report for the
            # financial year ended 31 December 2023" were five nodes, each joining unrelated
            # companies. Two mentions are one document only when they come from the same item.
            nid = node_id(kind, f"{name.lower()}@{item_scope}")
            node = GraphNode(id=nid, kind=kind, label=name, sources=[pointer(gs, ge)],
                             valid_from=vf, system_from=sf)
        else:
            node = GraphNode(id=node_id(kind, (cid or name).lower()), kind=kind, label=name,
                             canonical_id=cid,
                             attr_sources={"canonical_id": cid_ptr} if cid is not None else {},
                             sources=[pointer(gs, ge)], valid_from=vf, system_from=sf)
        if any(n.id == node.id for n in out.entities):
            continue                              # the same entity listed twice: one node
        if bound.is_subject(ent.kind) or bound.is_person(ent.kind):
            # recorded only for the node that SURVIVES dedup, so a candidate lookup can never run
            # on an identifier the stored node does not carry (R-019 t06 C2)
            subject_identifier[node.id] = cid
        out.entities.append(node)
        by_name.setdefault(_fold(name), {})[node.id] = node

    def endpoint(name: str, ev_start: int, ev_end: int):
        """``(node, pinned_span, reason)``. A name shared by several extracted entities is
        ambiguous — unless exactly ONE of their grounded mentions lies inside the relation's
        nominated evidence passage, which then names that mention and no other."""
        hits = by_name.get(_fold(name), {})
        if len(hits) == 1:
            return next(iter(hits.values())), None, None
        if not hits:
            return None, None, "endpoint not a grounded entity"
        inside = []
        for n in hits.values():
            p = n.sources[0]
            if ev_start <= p.char_start and p.char_end <= ev_end:
                inside.append((n, (p.char_start, p.char_end)))
        if len(inside) == 1:
            return inside[0][0], inside[0][1], None
        return None, None, (
            f"endpoint {name!r} is ambiguous — {len(hits)} distinct extracted entities share "
            f"that name ({sorted(hits)}) and the evidence passage does not single one out; a "
            f"relation cannot choose between them")

    # --- relations: closed vocab, endpoint kinds, grounded endpoints, evidence ----
    offered_edges = set(ev.edge_kinds)
    for rel in draft.relations:
        label = f"relation {rel.src_name!r} -{rel.rel_type}-> {rel.dst_name!r}"
        decl = ev.edges.get(rel.rel_type)
        # A MODEL MAY NEVER PROPOSE AN IDENTITY DETERMINATION. Extraction reads a document and
        # emits candidates; a determination is what a named human produces on the far side of an
        # attestation (CLAUDE.md §Hard constraints 1, spine D4b/D12). Such kinds are not offered
        # in the schema, and are refused by name here if a model names one anyway — checked
        # BEFORE the generic unknown-type refusal so the reason a reviewer reads is the real one.
        if decl is not None and is_determination_kind(EdgeKind(rel.rel_type)):
            out.refused.append(
                f"{label}: that edge kind records an identity DETERMINATION, which extraction "
                f"may never propose. A named human attests; the model produces candidates "
                f"(REFUSED)")
            continue
        if decl is None or rel.rel_type not in offered_edges:
            out.refused.append(
                f"relation {rel.rel_type!r}: unknown type — not in the closed vocabulary "
                f"(REFUSED, not coerced)")
            continue
        ekind = EdgeKind(rel.rel_type)
        src, src_pin, why_src = endpoint(rel.src_name, rel.evidence_char_start,
                                         rel.evidence_char_end)
        dst, dst_pin, why_dst = endpoint(rel.dst_name, rel.evidence_char_start,
                                         rel.evidence_char_end)
        if src is None or dst is None:
            out.refused.append(f"{label}: {why_src or why_dst} (REFUSED)")
            continue
        # The ontology's endpoint shape. `works_for` from a document to a place is not a
        # relationship this vocabulary can express, however confidently a model proposes it.
        bad = [(f"{end} kind {k.value!r} not in {list(getattr(decl, end))}" if getattr(decl, end)
                else f"the ontology declares no {end} kinds for it — declare them, or ['*']")
               for end, k in (("src", src.kind), ("dst", dst.kind)) if not decl.allows(end, k)]
        if bad:
            out.refused.append(
                f"{label}: wrong endpoint kinds for {rel.rel_type!r} ({'; '.join(bad)}) "
                f"(REFUSED — off-ontology)")
            continue
        span, why = _relation_evidence(text, rel.src_name, rel.dst_name,
                                       rel.evidence_char_start, rel.evidence_char_end,
                                       src_span=src_pin, dst_span=dst_pin)
        if span is None:
            out.refused.append(f"{label}: {why} (REFUSED — unsupported relation)")
            continue
        edge = GraphEdge(id=f"{src.id}|{ekind.value}|{dst.id}", src_id=src.id, dst_id=dst.id,
                         kind=ekind, sources=[pointer(*span)], valid_from=vf, system_from=sf)
        out.relations.append(edge)

    if store is not None:
        # Candidates BEFORE the upsert, so a subject's own earlier sighting (same document, same
        # mention id) is not offered as a candidate for itself.
        for node in out.entities:
            if node.id in subject_identifier:
                out.subject_candidates.extend(
                    _subject_candidates(store, node, subject_identifier[node.id]))
        # A non-subject node may RESOLVE to an existing node under a different id (canonical or
        # fuzzy merge). The edges above were built from the incoming ids, so they must be
        # rewritten to the ids the store actually settled on — otherwise a merged endpoint leaves
        # the edge pointing at an id that was never inserted.
        resolved: dict[str, str] = {}
        for node in out.entities:
            # A subject is upserted ID-ONLY (R-019 t06 C1): its mention id may re-sight itself
            # (idempotent re-extraction) and can match nothing else — no canonical step, no
            # similarity step, whatever the pack's `never_fuzzy_merge` says.
            outcome = upsert_node(store, node, bound, id_only=node.id in subject_identifier)
            out.upserts.append(outcome)
            resolved[node.id] = outcome.id
        remapped: list[GraphEdge] = []
        for edge in out.relations:
            src_id = resolved.get(edge.src_id, edge.src_id)
            dst_id = resolved.get(edge.dst_id, edge.dst_id)
            if (src_id, dst_id) != (edge.src_id, edge.dst_id):
                edge = edge.model_copy(update={
                    "src_id": src_id, "dst_id": dst_id,
                    "id": f"{src_id}|{edge.kind.value}|{dst_id}"})
            remapped.append(edge)
            out.upserts.append(upsert_edge(store, edge))
        out.relations[:] = remapped
    return out


def _subject_candidates(store: GraphStore, node: GraphNode,
                        identifier: Optional[str]) -> list[dict]:
    """Existing open subject nodes ``node`` might be. Exact matches only (same grounded
    identifier as their ``canonical_id``, or the same label up to case/whitespace) — a candidate
    list is shown to a human, and a similarity-ranked one would be a merge suggestion dressed as a
    fact. Never acted on here."""
    found: dict[str, str] = {}
    if identifier:
        row = store.find_by_canonical(node.kind, identifier)
        if row is not None and row["id"] != node.id:
            found[row["id"]] = "same identifier"
        # An earlier EXTRACTED mention carries its identifier as `extracted_identifier`, never as
        # `canonical_id` (R-019 t06 M1) — look there too. Still a candidate list, never a link.
        for row in store.open_nodes_with_identifier(node.kind, identifier):
            if row["id"] != node.id:
                found.setdefault(row["id"], "same identifier")
    want = _fold(node.label)
    for row in store.open_node_candidates_of_kind(node.kind):
        if _fold(row["label"] or "") != want:
            continue
        full = store.node_row_by_rowid(row["rowid_pk"])
        if full is not None and full["id"] != node.id:
            found.setdefault(full["id"], "same label")
    return [{"extracted": node.id, "candidate": cid, "basis": basis,
             "requires_human_selection": True} for cid, basis in sorted(found.items())]


def extraction_json_schema(vocabulary: Optional[Vocabulary] = None) -> dict:
    """The JSON Schema ``extract_graph`` would send under ``vocabulary`` (default: the bound one).
    Exposed so a caller can show or test the closed enum without making a model call."""
    ev = extraction_vocabulary(vocabulary)
    return extraction_response_model(ev.node_kinds, ev.edge_kinds).model_json_schema()


# --- deterministic gold handler for CI (no live model) ----------------------------
def gold_extraction_handler(entities: list[dict], relations: list[dict]):
    """Build a FakeBackend handler returning a fixed ExtractionDraft — the EV-BRAIN extraction
    plumbing proof (accuracy on a real model is a separate run, mirroring e1.gold_backend)."""
    payload = ExtractionDraft(entities=[ExtractedEntity(**e) for e in entities],
                              relations=[ExtractedRelation(**r) for r in relations]).model_dump_json()

    def handler(*, model, messages, guided_json) -> str:
        return payload

    return handler
