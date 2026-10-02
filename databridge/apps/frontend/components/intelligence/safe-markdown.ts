"use client";
/**
 * Markdown written by a model (or quoted from a source) on the Intelligence page, rendered so it
 * cannot make the browser fetch anything. The policy and its reasons are in
 * `safe-markdown-policy.ts`; this is the one place that applies it, so every renderer of model
 * Markdown under `components/intelligence` goes through here (R-027 ONE, Codex round 1, HIGH).
 *
 * Written with `React.createElement` rather than JSX so the `@unit` tests can render the real
 * component to static markup (Playwright's test loader rewrites JSX it compiles).
 */
import * as React from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import { SAFE_MARKDOWN_ELEMENTS, safeMarkdownUrl } from "./safe-markdown-policy";

const h = React.createElement;

export function SafeMarkdown({
  children,
  components,
}: {
  children: string;
  /** Per-surface overrides (the citation chips). Cannot re-enable an element: the allow-list is
   *  applied before any component renders, and `img` is always the inert stand-in below. */
  components?: Components;
}) {
  const custom = components?.a;
  const merged: Components = {
    ...components,
    a: (props) => {
      // A link whose URL the policy dropped stays as its text: no href, nothing to follow.
      if (!props.href) return h("span", null, props.children);
      if (custom && typeof custom !== "string") return h(custom, props);
      return h("a", { href: props.href, className: "text-accent hover:underline" }, props.children);
    },
    img: ({ alt }) =>
      h(
        "span",
        { "data-testid": "markdown-image-withheld", className: "italic text-subtle" },
        `[image not loaded${alt ? `: ${alt}` : ""}]`,
      ),
  };
  // `img` passes the allow-list only to reach the inert stand-in above; it never renders as <img>.
  return h(ReactMarkdown, {
    allowedElements: [...SAFE_MARKDOWN_ELEMENTS, "img"],
    unwrapDisallowed: true,
    skipHtml: true,
    urlTransform: (url: string) => safeMarkdownUrl(url),
    components: merged,
    children,
  });
}
