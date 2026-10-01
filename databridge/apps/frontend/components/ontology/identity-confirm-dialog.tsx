"use client";
/**
 * The explicit confirmation every identity-protected vocabulary change needs.
 *
 * The gateway decides WHETHER a change is identity-protected (it answers
 * `IDENTITY_CONFIRMATION_REQUIRED` with the classes); this dialog only states what that means and
 * collects the deliberate "yes". Nothing here pre-ticks, defaults or remembers the confirmation —
 * a subject is the entity whose identity this system never auto-confirms (CLAUDE.md hard
 * constraint 1), and changing what a subject IS must be a separate, conscious act every time.
 */
import * as React from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ShieldAlert, X } from "lucide-react";

const CLASS_TEXT: Record<string, string> = {
  subject_declaration:
    "declares a new SUBJECT — records of it are never merged by name, and matching one needs a named human",
  subject_rename: "renames a SUBJECT kind — its identity guarantee is copied to the new name",
  subject_touching: "lets a relation point at a SUBJECT kind",
  determination: "is a DETERMINATION relation — written only behind a named human's attestation",
  identity_claim: "is an identity CLAIM relation — never read as a determination",
};

export function IdentityConfirmDialog({
  open,
  classes,
  message,
  busy,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  classes: string[];
  message?: string | null;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const [checked, setChecked] = React.useState(false);
  React.useEffect(() => {
    if (open) setChecked(false);
  }, [open]);

  return (
    <Dialog.Root open={open} onOpenChange={(o) => (!o ? onCancel() : undefined)}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
        <Dialog.Content
          data-testid="identity-confirm-dialog"
          className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg border border-band-review/40 bg-surface p-5 shadow-lg"
        >
          <div className="mb-3 flex items-start justify-between gap-3">
            <div className="flex items-start gap-2">
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-band-review" />
              <div>
                <Dialog.Title className="text-sm font-semibold text-fg">
                  This change is identity-protected
                </Dialog.Title>
                <Dialog.Description className="mt-1 text-2xs leading-relaxed text-subtle">
                  It is saved only if you confirm it explicitly. This change:
                </Dialog.Description>
              </div>
            </div>
            <Dialog.Close className="rounded p-1 text-subtle hover:bg-muted hover:text-fg" aria-label="Close">
              <X className="h-3.5 w-3.5" />
            </Dialog.Close>
          </div>
          <ul className="mb-3 list-disc space-y-1 pl-5 text-2xs leading-relaxed text-fg">
            {classes.length ? (
              classes.map((c) => <li key={c}>{CLASS_TEXT[c] ?? c}</li>)
            ) : (
              <li>{message ?? "touches an identity-protected kind"}</li>
            )}
          </ul>
          <label className="mb-4 flex items-start gap-2 rounded border border-border bg-bg px-2.5 py-2 text-2xs text-fg">
            <input
              type="checkbox"
              checked={checked}
              onChange={(e) => setChecked(e.target.checked)}
              className="mt-0.5"
              data-testid="identity-confirm-check"
            />
            I understand this changes how identities are protected, and I am making this change
            deliberately.
          </label>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onCancel}
              className="rounded border border-border px-3 py-1.5 text-2xs text-subtle hover:text-fg"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={!checked || busy}
              onClick={onConfirm}
              data-testid="identity-confirm-submit"
              className="rounded border border-band-review bg-band-review px-3 py-1.5 text-2xs font-medium text-white disabled:opacity-40"
            >
              {busy ? "Saving…" : "Confirm and save"}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
