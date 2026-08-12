"use client";
/**
 * The literal `SourcePointer` object, unformatted into prose. "Showcase the pointer" (T-37) is
 * literal: a judge should be able to read the JSON, not a summary of it.
 */
import type { SourcePointer } from "@/lib/types";

export function PointerJson({ pointer }: { pointer: SourcePointer }) {
  return (
    <pre
      data-testid="pointer-json"
      className="overflow-x-auto rounded border border-border bg-muted px-3 py-2 text-2xs leading-relaxed text-fg"
    >
      {JSON.stringify(pointer, null, 2)}
    </pre>
  );
}
