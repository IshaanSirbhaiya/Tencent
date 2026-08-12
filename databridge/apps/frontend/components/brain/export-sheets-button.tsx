"use client";
import * as React from "react";
import { Download, Loader2 } from "lucide-react";
import { brainApi } from "@/lib/brain-api";

/**
 * "Download evidence sheets" — the whole graph as one markdown file per entity.
 *
 * It sits in the time-slider bar rather than the header, and that is the point: the export takes
 * the `asOf` the slider is currently showing, so the folder you download is the graph you are
 * looking at. A download control placed away from the time control would silently hand back "now"
 * while the canvas showed May, and the file would look like the answer to a question nobody asked.
 *
 * The click does no rendering of its own — the bytes come from the gateway, which returns the same
 * sheets the node panel displays.
 */
export function ExportSheetsButton({ asOf }: { asOf: string | null }) {
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const onClick = React.useCallback(async () => {
    setBusy(true);
    setError(null);
    let url: string | null = null;
    try {
      const { filename, blob } = await brainApi.downloadSheets(asOf);
      url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    } catch (e) {
      // A refusal is shown, never swallowed: a button that appears to do nothing reads as broken,
      // and "the graph is not loaded" is a fact the person can act on.
      setError(e instanceof Error ? e.message : "Export failed");
    } finally {
      // Revoked on the next tick — Safari cancels an in-flight download if the object URL dies
      // in the same frame as the click.
      if (url) setTimeout(() => URL.revokeObjectURL(url as string), 10_000);
      setBusy(false);
    }
  }, [asOf]);

  return (
    <div className="flex shrink-0 items-center gap-2">
      {error ? (
        <span role="alert" className="max-w-48 truncate text-2xs text-band-review" title={error}>
          {error}
        </span>
      ) : null}
      <button
        type="button"
        onClick={onClick}
        disabled={busy}
        data-testid="export-sheets"
        title={
          asOf
            ? "Download one markdown evidence sheet per entity, as the graph stood at the date shown"
            : "Download one markdown evidence sheet per entity — the same text shown when you click a node"
        }
        className="flex items-center gap-1.5 rounded border border-border px-2 py-1 text-2xs text-subtle transition-colors hover:bg-muted hover:text-fg disabled:cursor-not-allowed disabled:opacity-50"
      >
        {busy ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <Download className="h-3 w-3" />
        )}
        {busy ? "Preparing…" : "Evidence sheets"}
      </button>
    </div>
  );
}
