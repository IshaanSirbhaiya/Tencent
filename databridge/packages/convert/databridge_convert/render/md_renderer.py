"""ReportIR → Markdown (C18, 09_FINAL_PRD §13.4).

**Deterministic by contract: the same ReportIR renders to BYTE-IDENTICAL Markdown.** No locale, no
timestamps, no set-iteration — blocks render in list order, newlines are always ``\\n`` (never
``\\r\\n``), and the document ends in exactly one trailing newline. ``render_markdown_with_spans``
is the single source of truth (``render_markdown`` is its first element), and ``report_ir`` locates
each Claim against the per-block spans it returns — so a claim's span always brackets its own block.

**Structural-injection hardening (the Markdown analogue of the §21.7 formula guard):** untrusted
text (extracted from documents / a DB) must not be able to break OUT of its block and inject new
Markdown structure. So single-line blocks (Heading / Paragraph / list items / image alt) collapse
newlines, a Code block uses a CommonMark variable-length fence longer than any backtick run inside
it (content cannot close the fence early), and table cells escape ``|``/newlines (the grid cannot be
broken). See the per-block handling below.

**HTML boundary (honest, per §2):** Markdown permits raw inline HTML. This renderer emits Markdown,
NOT sanitized HTML — it does NOT neutralise ``<script>`` etc. (escaping ``<``/``>`` would corrupt
legitimate prose like "a < b"). Any consumer that renders this Markdown to HTML MUST run an HTML
sanitiser; the .md/.docx/.pdf artefacts themselves do not execute HTML.
"""
from __future__ import annotations

from ..ir import Block, ReportIR

NL = "\n"


def _oneline(text: str) -> str:
    """Collapse newlines to spaces — a single-line block (heading, paragraph, list item) can never
    spill into a new line and become a heading/fence/blank-line break (structural injection)."""
    return text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def _escape_cell(text: str) -> str:
    """Make a table cell safe for a GFM pipe row. NOTE: backslash is escaped FIRST so an input
    ``\\|`` does not get its pipe un-escaped; then ``|`` is escaped and any newline flattened to a
    space, so one cell can never spill into a new table row (determinism + structural safety)."""
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r\n", " ") \
               .replace("\n", " ").replace("\r", " ")


def _code_fence(text: str) -> str:
    """A CommonMark fence longer than the longest backtick run in ``text`` (min 3) — so content
    containing ``` can never close the fence early and inject Markdown after it."""
    longest = run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def _render_table(rows: list[list[str]]) -> str:
    """GFM table. First row is the header; a separator row follows. Ragged rows are padded so the
    grid is rectangular (deterministic width = the widest row)."""
    width = max((len(r) for r in rows), default=0)
    if width == 0:
        return ""
    out: list[str] = []
    header = rows[0] if rows else []
    header_cells = [_escape_cell(str(c)) for c in header] + [""] * (width - len(header))
    out.append("| " + " | ".join(header_cells) + " |")
    out.append("| " + " | ".join(["---"] * width) + " |")
    for row in rows[1:]:
        cells = [_escape_cell(str(c)) for c in row] + [""] * (width - len(row))
        out.append("| " + " | ".join(cells) + " |")
    return NL.join(out)


def _render_list_item(line: str) -> str:
    """One list item, single-line, with a leading block-starter (``#`` heading, ``>`` quote, ``` ` ```
    code) escaped so the item content cannot become a nested block."""
    item = _oneline(line).lstrip("-*+ ").rstrip()
    if item[:1] in {"#", ">", "`"}:
        item = "\\" + item
    return f"- {item}"


def _render_block(block: Block) -> str:
    kind = block.kind
    if kind == "Heading":
        level = max(1, min(block.level or 1, 6))
        return f"{'#' * level} {_oneline(block.text)}"
    if kind == "Table" and block.rows:
        return _render_table(block.rows)
    if kind == "Code":
        fence = _code_fence(block.text)
        lang = _oneline(block.lang or "").replace("`", "")
        return f"{fence}{lang}{NL}{block.text}{NL}{fence}"
    if kind == "ListBlock":
        return NL.join(_render_list_item(ln) for ln in block.text.split("\n"))
    if kind == "PageBreak":
        return "---"
    if kind == "Image":
        alt = _oneline(block.text or "image").replace("\\", "\\\\").replace("]", "\\]") \
                                              .replace("[", "\\[")
        url = (block.media_id or "").replace(")", "%29").replace("(", "%28")
        return f"![{alt}]({url})"
    # Paragraph and any unhandled kind → single-line plain text (no newline breakout)
    return _oneline(block.text)


def render_markdown_with_spans(report: ReportIR) -> tuple[str, dict[int, tuple[int, int]]]:
    """ReportIR → (deterministic Markdown, {block index → (char_start, char_end)}). Blocks render in
    order, separated by one blank line; the document ends in exactly one ``\\n``. The span map is how
    ``report_ir`` brackets each Claim to its own block (never a coincidental match elsewhere)."""
    parts = [_render_block(b) for b in report.blocks]
    sep = NL + NL
    spans: dict[int, tuple[int, int]] = {}
    cursor = 0
    for i, p in enumerate(parts):
        if i > 0:
            cursor += len(sep)
        spans[i] = (cursor, cursor + len(p))
        cursor += len(p)
    body = sep.join(parts)
    md = body + NL if body else NL
    return md, spans


def render_markdown(report: ReportIR) -> str:
    """ReportIR → deterministic Markdown string. Same ReportIR ⇒ byte-identical output (§13.4)."""
    return render_markdown_with_spans(report)[0]


def render_markdown_bytes(report: ReportIR) -> bytes:
    """UTF-8 bytes of the Markdown render (the on-disk artefact; byte-identity is over THIS)."""
    return render_markdown(report).encode("utf-8")
