"use client";
/**
 * The confirmation for "remove". Read `ontology_routes.EMPTY_STATE`'s comment before touching this
 * — the action behind this dialog DEPRECATES, it never deletes. `NodeKind(row["kind"])` resolves
 * every stored node's kind through the CURRENT vocabulary, so deleting a declaration a node already
 * used would make every existing node of that kind unreadable, not merely un-addable. This dialog
 * exists so the operator hears that trade-off in plain language before confirming, rather than
 * clicking a trash icon and hoping.
 */
import * as React from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { AlertTriangle, X } from "lucide-react";

export function DeprecateDialog({
  open,
  onOpenChange,
  axis,
  kind,
  actingAs,
  onConfirm,
  busy,
  error,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  axis: "node" | "edge";
  kind: string;
  actingAs: string;
  onConfirm: (reason: string) => void;
  busy: boolean;
  error: string | null;
}) {
  const [reason, setReason] = React.useState("");

  React.useEffect(() => {
    if (open) setReason("");
  }, [open, kind]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-surface p-5 shadow-lg">
          <div className="mb-3 flex items-start justify-between gap-3">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-band-review" />
              <div>
                <Dialog.Title className="text-sm font-semibold text-fg">
                  Remove {axis} kind <span className="font-mono">{kind}</span>?
                </Dialog.Title>
                <Dialog.Description className="mt-1 text-2xs leading-relaxed text-subtle">
                  This <span className="font-medium text-fg">deprecates</span> the kind — it does
                  not delete its declaration. Every node or edge already written with{" "}
                  <span className="font-mono">{kind}</span> keeps reading back exactly as it does
                  today; this system never invalidates graph data by editing a vocabulary. What
                  changes: <span className="font-mono">{kind}</span> stops being offered for new
                  proposals, and a new edge naming it as an endpoint is refused until it is
                  reactivated. You can reactivate it from this screen at any time.
                </Dialog.Description>
              </div>
            </div>
            <Dialog.Close asChild>
              <button
                type="button"
                aria-label="Cancel"
                className="rounded p-1 text-subtle hover:bg-muted hover:text-fg"
              >
                <X className="h-4 w-4" />
              </button>
            </Dialog.Close>
          </div>

          <label className="mb-1 block text-2xs font-medium uppercase tracking-wider text-subtle">
            Reason <span className="font-normal normal-case tracking-normal text-subtle/70">(optional)</span>
          </label>
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. superseded by a better kind"
            className="mb-3 w-full rounded border border-border bg-bg px-2.5 py-1.5 text-xs outline-none focus:border-accent"
          />

          <p className="mb-3 text-2xs text-subtle">
            Recorded as removed by{" "}
            <span className="font-medium text-fg">{actingAs || "(sign in to remove a kind)"}</span>.
          </p>

          {error ? (
            <p className="mb-3 text-2xs text-band-review" data-testid="deprecate-refusal">
              Refused: {error}
            </p>
          ) : null}

          <div className="flex justify-end gap-2">
            <Dialog.Close asChild>
              <button
                type="button"
                className="rounded border border-border px-3 py-1.5 text-2xs text-subtle hover:text-fg"
              >
                Cancel
              </button>
            </Dialog.Close>
            <button
              type="button"
              disabled={busy || !actingAs}
              data-testid="confirm-deprecate"
              onClick={() => onConfirm(reason.trim())}
              className="rounded border border-band-review/50 bg-band-review/10 px-3 py-1.5 text-2xs font-medium text-band-review transition-colors hover:bg-band-review/20 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {busy ? "Removing…" : "Remove kind"}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
