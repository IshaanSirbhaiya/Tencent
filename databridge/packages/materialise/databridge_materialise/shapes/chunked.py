"""The RAG chunk corpus — SYSTEM_DESIGN §6.6(c), executed.

Membership on **system time**: a document is in the corpus iff it was on file by
`temporal.corpus_as_of`, so retrieval replays what the organisation actually had rather than what
it has now. Valid time is retained as metadata where the source records it, so a retriever can
prefer in-force versions, but it is never the membership axis.

**Late chunking, and why it is the default rather than an option.** Boundaries are computed on the
ORIGINAL text, so `(row_id, column, char_start, char_end)` are exact offsets into the source cell
and every chunk re-slices. That makes the chunk itself a pointer rather than a copy with a note
attached.

**The re-slice check is the grounding wall, one level up.** Each chunk is emitted with a `derived`
pointer whose rule is `text_slice@v1(start=…,end=…)`, and `assert_grounded` replays that rule over
the cited parent cell. A chunk whose stored text does not reproduce byte-identically from its own
offsets is refused. This has to be structural rather than a prompt instruction: citation
hallucination rates of 11–57% persist across commercially deployed models.

**Contextual-retrieval prefixes are index-only and are not produced here.** A generated prefix
would be a `generated_claim` — `UNTRUSTED` by construction — parented to the chunk's span, and it
may never be returned as evidence. This build emits no such prefix at all, which is the honest
position for a package with no model in its read path.
"""
from __future__ import annotations

from dataclasses import dataclass

from databridge_adapter import assert_derived_from, assert_grounded
from databridge_core import GroundedValue, SourcePointer, Withheld, trust_from_parents

from ..compiler import CompiledPlan
from ..dataset import Cell, Dataset
from ..errors import ChunkResliceFailed
from ..plan import MaterialisationPlan
from ..reader import EntityRead, EstateReader
from ..temporal import known_at

#: The corpus's columns. Fixed rather than plan-declared: a chunk corpus whose shape varied per
#: plan would need its own vocabulary, and every retriever consuming one would have to learn it.
CHUNK_COLUMNS: tuple[str, ...] = ("chunk_id", "document_id", "char_start", "char_end", "text")


@dataclass
class ChunkBuild:
    dataset: Dataset
    documents_considered: int = 0
    documents_admitted: int = 0
    rejected_by_policy: int = 0
    rejected_by_membership: int = 0
    reslice_checked: int = 0
    notes: tuple[str, ...] = ()
    reads: tuple[EntityRead, ...] = ()


def _boundaries(length: int, max_chars: int, overlap: int) -> list[tuple[int, int]]:
    """Character ranges over the ORIGINAL text. Deterministic and total."""
    if length == 0:
        return []
    step = max_chars - overlap
    out: list[tuple[int, int]] = []
    start = 0
    while start < length:
        end = min(start + max_chars, length)
        out.append((start, end))
        if end == length:
            break
        start += step
    return out


def build_chunk_corpus(compiled: CompiledPlan, reader: EstateReader) -> ChunkBuild:
    """Execute an accepted `chunk_corpus` plan."""
    plan: MaterialisationPlan = compiled.plan
    source = plan.shape.text_field
    chunking = plan.shape.chunking
    if source is None or chunking is None:                       # pragma: no cover - compiler-bound
        raise ValueError("a chunk corpus needs shape.text_field and shape.chunking")
    corpus_as_of = plan.temporal.corpus_as_of

    fields = [source.field]
    if source.system_time_field:
        fields.append(source.system_time_field)
    if source.valid_time_field:
        fields.append(source.valid_time_field)
    read = reader.read(source.entity, fields)
    snapshot = read.snapshot

    rows: list[tuple[Cell, ...]] = []
    considered = admitted = rejected_policy = rejected_membership = reslice_checked = 0

    for row in read.rows:
        considered += 1
        text_cell = row.get(source.field)
        if isinstance(text_cell, Withheld):
            rejected_policy += 1
            continue
        if not isinstance(text_cell, GroundedValue) or not isinstance(text_cell.value, str):
            # Absent, or a non-text cell. Neither is a document; neither is an error.
            continue
        if not known_at(read.value(row, source.system_time_field), corpus_as_of):
            # Membership on system time: not on file by the corpus instant. Excluded, and counted
            # apart from a policy denial — "we did not have it yet" and "you may not see it" are
            # different facts and a corpus that summed them would be lying about its coverage.
            rejected_membership += 1
            continue

        document_id = read.row_id(row)
        if document_id is None:
            rejected_policy += 1
            continue
        admitted += 1
        text = text_cell.value
        # The document id is the row's KEY, so it is cited with the key cell's own pointer when
        # there is exactly one key column and it is grounded (R-021 round 2, Finding 1): that
        # pointer names the record the id is a column of, and re-reads to exactly it. The text
        # cell's pointer says nothing about the id. (A composite key has no single citation; it
        # keeps the text pointer, and a full pointer check refuses it rather than passing it.)
        key_cell = row.get(read.key[0]) if len(read.key) == 1 else None
        id_pointer = (key_cell.pointer if isinstance(key_cell, GroundedValue)
                      and str(key_cell.value) == document_id else text_cell.pointer)
        for index, (start, end) in enumerate(
                _boundaries(len(text), chunking.max_chars, chunking.overlap_chars)):
            chunk_text = text[start:end]
            pointer = SourcePointer(
                source_id=text_cell.pointer.source_id,
                kind="derived",
                derivation=f"text_slice@v1(start={start},end={end})",
                derived_from=[text_cell.pointer],
                snapshot=snapshot,
                trust=trust_from_parents([text_cell.pointer]),
            )
            assert_derived_from(pointer, text_cell.pointer)
            chunk_cell = GroundedValue(value=chunk_text, pointer=pointer, confidence=1.0)
            # THE RE-SLICE CHECK. `assert_grounded` replays `text_slice@v1` over the parent cell's
            # value and refuses if the result is not byte-identical to what this chunk carries.
            try:
                assert_grounded(chunk_cell, text)
            except Exception as err:                             # pragma: no cover - wall-bound
                raise ChunkResliceFailed(
                    f"chunk {document_id}#{index} does not reproduce from its own offsets "
                    f"[{start}, {end}) under the pinned snapshot: {err}. Each chunk IS a pointer; "
                    f"one that does not re-slice is a copy with a citation attached") from err
            reslice_checked += 1
            rows.append((
                _literal(f"{document_id}#{index}", pointer),
                _literal(document_id, id_pointer),
                _literal(start, pointer),
                _literal(end, pointer),
                chunk_cell,
            ))

    dataset = Dataset(columns=CHUNK_COLUMNS, rows=tuple(rows),
                      float_scale=plan.determinism.float_scale)
    notes: list[str] = []
    if chunking.embedding is None:
        notes.append(
            "no embedding model is pinned, so this corpus carries text and offsets and no "
            "vectors. Stated rather than defaulted: the embedding model's id, revision, pooling, "
            "normalisation and dimension are part of a corpus's identity, and a corpus embedded "
            "with an unpinned model would not be re-executable")
    notes.append(
        "no contextual-retrieval prefixes were generated. A prefix is a `generated_claim`, "
        "UNTRUSTED by construction, index-only and never citable as evidence; this package has no "
        "model in its read path and does not produce one")
    return ChunkBuild(dataset=dataset, documents_considered=considered,
                      documents_admitted=admitted, rejected_by_policy=rejected_policy,
                      rejected_by_membership=rejected_membership,
                      reslice_checked=reslice_checked, notes=tuple(notes), reads=(read,))


def _literal(value, pointer: SourcePointer) -> GroundedValue:
    """A structural column of the corpus, carrying the pointer it describes.

    `chunk_id`, `document_id` and the two offsets are not values read from the source — they are
    the pointer's own coordinates, rendered so a consumer can join on them. They carry that same
    pointer rather than a fresh one, so there is no cell in the corpus whose provenance is 'the
    materialiser said so'.
    """
    return GroundedValue(value=value, pointer=pointer, confidence=1.0)


__all__ = ["ChunkBuild", "build_chunk_corpus", "CHUNK_COLUMNS"]
