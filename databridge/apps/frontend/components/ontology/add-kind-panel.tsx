"use client";
/**
 * "Define a kind" — the OWNER's edit. Submitting here calls `POST /ontology/kinds`, which writes
 * the kind into the pack's `ontology.yaml`, appends an audit line naming who did it, and reloads
 * the vocabulary caches so the very next extraction maps to it. Extraction maps ONLY to what is
 * defined here: anything else a model emits is refused.
 *
 * (Kinds that an agent or adapter agent SUGGESTS do not come through this form — they arrive in
 * the proposal queue and always wait for a human.)
 *
 * Entity kinds: a name, a meta-kind (each explained in plain English, from the meta-model's own
 * text), a description, and — optionally — "this is a person / subject", which makes the kind
 * identity-protected: its records are never merged by name, and the save asks for an explicit
 * confirmation. Relation kinds: a name, a meta-kind, source and target kinds picked from the
 * pack's entity kinds, and a description.
 *
 * Whether a change is identity-protected is decided by the gateway; the page's confirm dialog
 * handles its `IDENTITY_CONFIRMATION_REQUIRED` answer. This form only shows the warning early.
 */
import * as React from "react";
import { ArrowRight, Plus, ShieldAlert, X } from "lucide-react";
import type {
  DefineKindInput,
  MetaKindCatalog,
  NodeKindView,
  OwnerEditResult,
} from "@/lib/ontology-api";
import { KindPicker } from "./kind-picker";

const inputCls =
  "w-full rounded border border-border bg-bg px-2.5 py-1.5 text-xs text-fg outline-none focus:border-accent";
const labelCls = "mb-1 block text-2xs font-medium uppercase tracking-wider text-subtle";

function toSnake(v: string) {
  return v
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/^[0-9_]+/, "");
}

export function AddKindPanel({
  pack,
  metaKinds,
  nodeKinds,
  attestationModes,
  actingAs,
  defaultAxis = "node",
  initialName = "",
  onDefine,
  onClose,
}: {
  pack: string;
  metaKinds: MetaKindCatalog;
  nodeKinds: NodeKindView[];
  attestationModes: string[];
  actingAs: string;
  defaultAxis?: "node" | "edge";
  /** Prefill, e.g. a name ingestion refused because no kind existed for it. */
  initialName?: string;
  /** Resolves to `null` when the human cancelled the identity confirmation. */
  onDefine: (input: DefineKindInput) => Promise<OwnerEditResult | null>;
  onClose: () => void;
}) {
  const [axis, setAxis] = React.useState<"node" | "edge">(defaultAxis);
  const metas = axis === "node" ? metaKinds.node : metaKinds.edge;
  const [name, setName] = React.useState(initialName);
  const [bindsTo, setBindsTo] = React.useState(metas[0]?.value ?? "");
  const [description, setDescription] = React.useState("");
  const [src, setSrc] = React.useState<string[]>([]);
  const [dst, setDst] = React.useState<string[]>([]);
  const [isSubject, setIsSubject] = React.useState(false);
  const [identifiers, setIdentifiers] = React.useState("");
  const [attestation, setAttestation] = React.useState(attestationModes[0] ?? "");
  const [grantType, setGrantType] = React.useState("");
  const [reason, setReason] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [result, setResult] = React.useState<OwnerEditResult | null>(null);

  React.useEffect(() => {
    const list = axis === "node" ? metaKinds.node : metaKinds.edge;
    setBindsTo(list[0]?.value ?? "");
    setIsSubject(false);
    setResult(null);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [axis]);

  const nodeMeta = metaKinds.node.find((m) => m.value === bindsTo);
  const edgeMeta = metaKinds.edge.find((m) => m.value === bindsTo);
  const selectedMeta = axis === "node" ? nodeMeta : edgeMeta;
  const subjectEligible = axis === "node" && !!nodeMeta?.subject_eligible;
  const subjects = new Set(nodeKinds.filter((k) => k.subject).map((k) => k.kind));
  const touchesSubject =
    axis === "edge" && [...src, ...dst].some((k) => subjects.has(k) || (k === "*" && subjects.size > 0));
  const identityEdge = axis === "edge" && !!edgeMeta && (edgeMeta.attested || edgeMeta.claim);
  const identityProtected = (subjectEligible && isSubject) || touchesSubject || identityEdge;
  const snake = toSnake(name);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!snake || !bindsTo) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const done = await onDefine({
        pack,
        axis,
        name: snake,
        binds_to: bindsTo,
        description: description.trim(),
        src: axis === "edge" ? src : [],
        dst: axis === "edge" ? dst : [],
        subject:
          axis === "node" && subjectEligible && isSubject
            ? {
                identifiers: identifiers.split(",").map((s) => s.trim()).filter(Boolean),
                attestation,
                grant_type: grantType.trim(),
              }
            : null,
        by: actingAs,
        reason: reason.trim(),
      });
      if (done) {
        setResult(done);
        setName("");
        setDescription("");
        setSrc([]);
        setDst([]);
        setIsSubject(false);
        setIdentifiers("");
        setGrantType("");
        setReason("");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "the gateway refused this definition");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      data-testid={`add-${axis}-kind-form`}
      className="flex flex-col gap-3 rounded-lg border border-dashed border-border bg-muted/30 p-4"
    >
      <div className="flex items-center justify-between">
        <div className="inline-flex rounded border border-border bg-surface p-0.5">
          {(["node", "edge"] as const).map((a) => (
            <button
              key={a}
              type="button"
              onClick={() => setAxis(a)}
              data-testid={`define-axis-${a}`}
              className={`rounded px-2.5 py-1 text-2xs font-medium transition-colors ${
                axis === a ? "bg-accent text-accent-fg" : "text-subtle hover:text-fg"
              }`}
            >
              {a === "node" ? "Entity kind" : "Relation kind"}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="rounded p-1 text-subtle hover:bg-muted hover:text-fg"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <p className="text-2xs leading-relaxed text-subtle">
        Saved straight into <span className="font-mono text-fg">{pack}</span>&apos;s ontology, recorded in
        the audit log, and used by the very next extraction. Extraction maps only to kinds defined
        here — anything else is refused.
      </p>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label>
          <span className={labelCls}>{axis === "node" ? "entity kind name" : "relation name"}</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={axis === "node" ? "e.g. vendor" : "e.g. supplies"}
            required
            className={inputCls}
            data-testid="define-kind-name"
          />
          {name && snake !== name ? (
            <span className="mt-1 block text-2xs text-subtle">
              saved as <span className="font-mono text-fg">{snake || "—"}</span>
            </span>
          ) : null}
        </label>
        <label>
          <span className={labelCls}>what shape of thing (meta-kind)</span>
          <select
            value={bindsTo}
            onChange={(e) => setBindsTo(e.target.value)}
            className={inputCls}
            data-testid="define-binds-to"
          >
            {metas.map((m) => (
              <option key={m.value} value={m.value}>
                {m.value}
                {"attested" in m && m.attested ? " — determination" : ""}
                {"claim" in m && m.claim ? " — identity claim" : ""}
              </option>
            ))}
          </select>
          <span className="mt-1 block text-2xs leading-relaxed text-subtle" data-testid="define-meta-explanation">
            {selectedMeta?.explanation ?? "No description for this meta-kind in the meta-model."}
          </span>
        </label>
      </div>

      {axis === "edge" ? (
        <div className="flex flex-col gap-2 rounded border border-border bg-surface p-2.5">
          <div>
            <span className={labelCls}>from (source kinds)</span>
            <KindPicker kinds={nodeKinds} value={src} onChange={setSrc} testId="define-src" />
          </div>
          <div className="flex items-center gap-1 text-2xs text-subtle">
            <ArrowRight className="h-3.5 w-3.5" /> {snake || "relation"}
          </div>
          <div>
            <span className={labelCls}>to (target kinds)</span>
            <KindPicker kinds={nodeKinds} value={dst} onChange={setDst} testId="define-dst" />
          </div>
        </div>
      ) : null}

      <label>
        <span className={labelCls}>description — what the extractor is told this means</span>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={2}
          className={inputCls}
          data-testid="define-description"
        />
      </label>

      {subjectEligible ? (
        <div className="rounded border border-border bg-surface px-2.5 py-2">
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              checked={isSubject}
              onChange={(e) => setIsSubject(e.target.checked)}
              className="mt-0.5"
              data-testid="define-is-subject"
            />
            <span className="text-2xs leading-relaxed text-subtle">
              <span className="font-medium text-fg">This is a person / subject — identity protected.</span>{" "}
              Records of it are never merged by name similarity; a named human confirms every
              match. Saving asks for an explicit confirmation.
            </span>
          </label>
          {isSubject ? (
            <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
              <label>
                <span className={labelCls}>identifiers a human checks</span>
                <input
                  value={identifiers}
                  onChange={(e) => setIdentifiers(e.target.value)}
                  placeholder="email, employee_id"
                  className={inputCls}
                  data-testid="define-identifiers"
                />
              </label>
              <label>
                <span className={labelCls}>attestation</span>
                <select
                  value={attestation}
                  onChange={(e) => setAttestation(e.target.value)}
                  className={inputCls}
                >
                  {attestationModes.map((m) => (
                    <option key={m} value={m}>
                      {m.replace(/_/g, " ")}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span className={labelCls}>grant type</span>
                <input
                  value={grantType}
                  onChange={(e) => setGrantType(e.target.value)}
                  placeholder="e.g. ContractorReadGrant"
                  className={inputCls}
                  data-testid="define-grant-type"
                />
              </label>
            </div>
          ) : null}
        </div>
      ) : null}

      {identityProtected ? (
        <div className="flex items-start gap-1.5 rounded border border-band-review/30 bg-band-review/[0.06] px-2.5 py-2">
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-review" />
          <p className="text-2xs leading-relaxed text-band-review">
            Identity-protected: saving will ask you to confirm explicitly.
          </p>
        </div>
      ) : null}

      <label>
        <span className={labelCls}>reason (recorded in the audit log)</span>
        <input value={reason} onChange={(e) => setReason(e.target.value)} className={inputCls} />
      </label>

      {result ? (
        <div
          className="rounded border border-band-auto/40 bg-band-auto/[0.06] px-3 py-2 text-2xs text-band-auto"
          data-testid="define-result"
        >
          <p className="font-medium">Saved {result.kind}.</p>
          <p className="mt-0.5 leading-relaxed">
            {result.pack_bound
              ? result.in_active_vocabulary
                ? "It is in the active vocabulary now — the next extraction can use it."
                : "Saved, but the active vocabulary did not pick it up — check the pack loads."
              : `Saved to ${result.pack}, which this deployment does not bind — extraction will not use it until the pack is bound.`}
          </p>
        </div>
      ) : null}
      {error ? (
        <p className="text-2xs text-band-review" data-testid="define-refusal">
          Refused: {error}
        </p>
      ) : null}

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={busy || !snake}
          data-testid="define-submit"
          className="inline-flex items-center gap-1 rounded border border-accent bg-accent px-3 py-1.5 text-2xs font-medium text-accent-fg disabled:opacity-50"
        >
          <Plus className="h-3 w-3" /> {busy ? "Saving…" : axis === "node" ? "Define entity kind" : "Define relation"}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="rounded border border-border px-3 py-1.5 text-2xs text-subtle hover:text-fg"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
