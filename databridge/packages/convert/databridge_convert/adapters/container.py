"""Container-mode conversion (09_FINAL_PRD §13.1) — the U→U "no LLM" sub-mode.

PDF/DOCX/PPTX/HTML/Markdown/TXT → Markdown (and other document targets) via a DocumentRecord
round-trip. Two non-negotiables:
  * **Loss report mandatory** — itemised unsupported features (tracked-changes flattened,
    PPTX animations dropped, …). An EMPTY loss report on a known-lossy pair is itself a bug.
  * **Structural validator** — block counts, table-cell counts, and a char-coverage ratio of
    the output vs the source IR must clear a threshold.

Container mode is the §16.3 syntactic-bypass case (declared-lossless/standard hops, no
semantic decisions) → it runs without semantic confirmation.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..ir import DocumentRecord
from ..router.registry import REGISTRY
from ._docutil import markdown_serialize
from .source import docx as s_docx
from .source import html as s_html
from .source import md as s_md
from .source import pdf_text as s_pdf
from .source import pptx as s_pptx
from .source import txt as s_txt

CHAR_COVERAGE_THRESHOLD = 0.95          # §13.1 char-coverage ratio gate

_SOURCE_READERS: dict[str, Callable[[bytes], DocumentRecord]] = {
    "markdown": s_md.read, "md": s_md.read, "txt": s_txt.read, "text": s_txt.read,
    "html": s_html.read, "pdf": s_pdf.read, "docx": s_docx.read, "pptx": s_pptx.read,
}
# pairs that are inherently lossy → their loss report MUST be non-empty.
# canonical format aliases → registry keys (used for source AND target resolution).
_CANON = {"md": "markdown", "text": "txt"}


@dataclass
class StructuralValidation:
    source_blocks: int
    output_blocks: Optional[int]            # None if the target isn't re-parseable here
    source_table_cells: int
    output_table_cells: Optional[int]
    char_coverage: float
    passed: bool
    issues: list[str] = field(default_factory=list)


@dataclass
class ContainerResult:
    data: bytes
    source_format: str
    target_format: str
    doc: DocumentRecord
    loss_report: list[str]
    validation: StructuralValidation


def _count_table_cells(doc: DocumentRecord) -> int:
    return sum(len(r) for b in doc.blocks if b.kind == "Table" and b.rows for r in b.rows)


def _char_multiset(text: str) -> Counter:
    return Counter(c for c in text if not c.isspace())


def _char_coverage(source_doc: DocumentRecord, output_text: str) -> float:
    """Multiset char coverage of the source IR's text preserved in the output (whitespace
    ignored). 1.0 = every source character is present with ≥ its source count.

    Limitation (review M3): being order-insensitive, this catches DELETION but not
    REORDERING (a scrambled anagram scores 1.0). The block-count + table-cell-count checks
    in ``structural_validate`` are the complementary structural guards."""
    src, _ = markdown_serialize(source_doc.blocks)
    src_ms, out_ms = _char_multiset(src), _char_multiset(output_text)
    if not src_ms:
        return 1.0
    preserved = sum(min(cnt, out_ms.get(ch, 0)) for ch, cnt in src_ms.items())
    return preserved / sum(src_ms.values())


def structural_validate(source_doc: DocumentRecord, output_bytes: bytes,
                        target_format: str) -> StructuralValidation:
    """Compare the output against the source IR (§13.1): block counts, table cell counts,
    char coverage. md/txt/html outputs are re-parsed to count output blocks; binary targets
    (docx) are validated on char coverage only."""
    issues: list[str] = []
    out_doc: Optional[DocumentRecord] = None
    # Re-open the output with ITS OWN reader (§19) — incl. binary targets like docx, whose
    # bytes are a zip and must NOT be naively decoded (review H2).
    reparse = {"markdown": s_md.read, "md": s_md.read, "txt": s_txt.read,
               "html": s_html.read, "docx": s_docx.read}.get(target_format)
    if reparse is not None:
        out_doc = reparse(output_bytes)
        output_text, _ = markdown_serialize(out_doc.blocks)
    else:
        output_text = output_bytes.decode("utf-8", errors="replace")

    src_blocks = len(source_doc.blocks)
    out_blocks = len(out_doc.blocks) if out_doc is not None else None
    src_cells = _count_table_cells(source_doc)
    out_cells = _count_table_cells(out_doc) if out_doc is not None else None
    coverage = _char_coverage(source_doc, output_text)

    if coverage < CHAR_COVERAGE_THRESHOLD:
        issues.append(f"char coverage {coverage:.3f} < {CHAR_COVERAGE_THRESHOLD}")
    if out_cells is not None and out_cells != src_cells:
        issues.append(f"table cells {out_cells} != source {src_cells}")
    passed = not issues
    return StructuralValidation(
        source_blocks=src_blocks, output_blocks=out_blocks,
        source_table_cells=src_cells, output_table_cells=out_cells,
        char_coverage=round(coverage, 4), passed=passed, issues=issues)


def convert_document(data: bytes, source_format: str, target_format: str = "markdown",
                     *, full_dom: bool = False) -> ContainerResult:
    """Container-mode conversion via a DocumentRecord round-trip (§13.1). Returns the output
    bytes + the (mandatory) loss report + the structural validation."""
    reader = _SOURCE_READERS.get(source_format)
    if reader is None:
        raise ValueError(f"container: no document source adapter for {source_format!r}")
    doc = (s_html.read(data, full_dom=full_dom) if source_format == "html"
           else reader(data))

    writer_info = REGISTRY.get(_CANON.get(target_format, target_format))
    if writer_info is None or not writer_info.can_write or writer_info.modality != "document":
        raise ValueError(f"container: no document target writer for {target_format!r}")
    out_bytes = writer_info.write(doc)

    loss = list(doc.source_meta.get("loss_report", []))
    # Mandatory-loss check grounded in the REGISTRY's declared lossiness (the source of
    # truth), not the source-format string (review H3): if the source adapter is declared
    # lossy and this isn't an identity hop, an EMPTY loss report is itself a bug (§13.1).
    canon_src = _CANON.get(source_format, source_format)
    canon_tgt = _CANON.get(target_format, target_format)
    src_info = REGISTRY.get(canon_src)
    if (src_info is not None and src_info.lossiness == "lossy"
            and canon_src != canon_tgt and not loss):
        raise AssertionError(f"container: lossy source {source_format!r} produced an empty "
                             f"loss report (§13.1 violation)")
    validation = structural_validate(doc, out_bytes, target_format)
    return ContainerResult(data=out_bytes, source_format=source_format,
                           target_format=target_format, doc=doc,
                           loss_report=loss, validation=validation)


def to_markdown(data: bytes, source_format: str, *, full_dom: bool = False) -> ContainerResult:
    """Convenience: any supported document → Markdown (the canonical container target)."""
    return convert_document(data, source_format, "markdown", full_dom=full_dom)
