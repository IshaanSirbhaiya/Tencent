"use client";
/**
 * Rename a kind. Rename is DEPRECATE + ADD: the new name is declared with the old declaration, the
 * old name is deprecated (never deleted — every stored node or edge of it still reads back), and,
 * for an entity kind, every relation that accepted the old name is widened to accept the new one.
 */
import * as React from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { PencilLine, X } from "lucide-react";

export function RenameDialog({
  open,
  axis,
  kind,
  relationCount,
  busy,
  error,
  onConfirm,
  onOpenChange,
}: {
  open: boolean;
  axis: "node" | "edge";
  kind: string;
  /** How many relations name this entity kind (they are widened to the new name). */
  relationCount: number;
  busy: boolean;
  error: string | null;
  onConfirm: (newName: string, reason: string) => void;
  onOpenChange: (open: boolean) => void;
}) {
  const [newName, setNewName] = React.useState("");
  const [reason, setReason] = React.useState("");
  React.useEffect(() => {
    if (open) {
      setNewName("");
      setReason("");
    }
  }, [open, kind]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-surface p-5 shadow-lg">
          <div className="mb-3 flex items-start justify-between gap-3">
            <div className="flex items-start gap-2">
              <PencilLine className="mt-0.5 h-4 w-4 shrink-0 text-subtle" />
              <div>
                <Dialog.Title className="text-sm font-semibold text-fg">
                  Rename {axis === "node" ? "entity kind" : "relation"}{" "}
                  <span className="font-mono">{kind}</span>
                </Dialog.Title>
                <Dialog.Description className="mt-1 text-2xs leading-relaxed text-subtle">
                  The new name gets the same definition. <span className="font-mono">{kind}</span> is
                  deprecated, not deleted, so everything already stored under it still reads back.
                  {axis === "node" && relationCount > 0
                    ? ` ${relationCount} relation${relationCount === 1 ? "" : "s"} that accept ${kind} will accept the new name too.`
                    : ""}
                </Dialog.Description>
              </div>
            </div>
            <Dialog.Close className="rounded p-1 text-subtle hover:bg-muted hover:text-fg" aria-label="Close">
              <X className="h-3.5 w-3.5" />
            </Dialog.Close>
          </div>
          <label className="mb-2 block">
            <span className="mb-1 block text-2xs font-medium uppercase tracking-wider text-subtle">new name</span>
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              data-testid="rename-new-name"
              className="w-full rounded border border-border bg-bg px-2.5 py-1.5 font-mono text-xs text-fg outline-none focus:border-accent"
            />
          </label>
          <label className="mb-3 block">
            <span className="mb-1 block text-2xs font-medium uppercase tracking-wider text-subtle">reason</span>
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              className="w-full rounded border border-border bg-bg px-2.5 py-1.5 text-xs text-fg outline-none focus:border-accent"
            />
          </label>
          {error ? <p className="mb-2 text-2xs text-band-review">Refused: {error}</p> : null}
          <div className="flex justify-end gap-2">
            <Dialog.Close className="rounded border border-border px-3 py-1.5 text-2xs text-subtle hover:text-fg">
              Cancel
            </Dialog.Close>
            <button
              type="button"
              disabled={busy || !newName.trim()}
              onClick={() => onConfirm(newName.trim(), reason.trim())}
              data-testid="rename-submit"
              className="rounded border border-accent bg-accent px-3 py-1.5 text-2xs font-medium text-accent-fg disabled:opacity-50"
            >
              {busy ? "Renaming…" : "Rename"}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
