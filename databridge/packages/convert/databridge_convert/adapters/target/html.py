"""HTML TARGET adapter — DocumentRecord → bytes (C12, §5.2). A minimal, deterministic
renderer (headings/paragraphs/tables/lists/code). Text is HTML-escaped (no injection)."""
from __future__ import annotations

from html import escape

from ...ir import DocumentRecord


def write(ir: DocumentRecord) -> bytes:
    out = ["<!DOCTYPE html>", "<html>", "<body>"]
    for b in ir.blocks:
        if b.kind == "Heading":
            lvl = max(1, min(b.level or 1, 6))
            out.append(f"<h{lvl}>{escape(b.text)}</h{lvl}>")
        elif b.kind == "Table" and b.rows:
            out.append("<table>")
            for i, row in enumerate(b.rows):
                tag = "th" if i == 0 else "td"
                out.append("<tr>" + "".join(f"<{tag}>{escape(str(c))}</{tag}>"
                                            for c in row) + "</tr>")
            out.append("</table>")
        elif b.kind == "ListBlock":
            out.append("<ul>")
            out.extend(f"<li>{escape(ln.lstrip('-*+ ').strip())}</li>"
                       for ln in b.text.split("\n") if ln.strip())
            out.append("</ul>")
        elif b.kind == "Code":
            out.append(f"<pre><code>{escape(b.text)}</code></pre>")
        elif b.kind == "PageBreak":
            out.append("<hr/>")
        else:
            out.append(f"<p>{escape(b.text)}</p>")
    out += ["</body>", "</html>"]
    return ("\n".join(out)).encode("utf-8")
