"use client";
/**
 * `/ontology` — the vocabulary the knowledge graph is allowed to use, and where the owner defines
 * it.
 *
 * The founder's requirement: "the ontology page allows me to define the relations and entities and
 * the system only maps to these." So this page is the place the vocabulary is WRITTEN:
 *
 *   - entity kinds and relation kinds are defined here directly (`POST /ontology/kinds`), written
 *     into the pack's `ontology.yaml`, recorded in an append-only audit log, and the vocabulary
 *     caches are reloaded so the very next extraction maps to them — and only to them;
 *   - relations name their source and target kinds, picked from the pack's entity kinds; the
 *     closed-vocabulary extractor refuses any edge whose endpoints do not fit;
 *   - rename is deprecate + add; remove is deprecate — never delete, because stored nodes read
 *     their kind back through the current vocabulary;
 *   - subject kinds (people — identity-protected) and anything touching them need an explicit
 *     human confirmation every time; the gateway decides when, this page asks;
 *   - kinds SUGGESTED by agents and adapter agents arrive as proposals and always wait for a human.
 *
 * The page targets the deployment's first bound pack by default (`workspace` under the launcher),
 * with a selector for every pack on disk. Everything shown is live from `/ontology*`.
 */
import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { Topbar } from "@/components/topbar";
import { useStaffSession } from "@/lib/staff-session";
import {
  approveProposal,
  defineKind,
  deprecateKind,
  fetchOntology,
  fetchOntologyPacks,
  identityClassesOf,
  OntologyApiError,
  reactivateKind,
  rejectProposal,
  renameKind,
  setAutoApprove,
  setEndpoints,
  type DefineKindInput,
  type EdgeKindView,
  type NodeKindView,
  type OntologyProposalRecord,
} from "@/lib/ontology-api";
import { PackPicker } from "@/components/ontology/pack-picker";
import { GrowthPolicyPanel } from "@/components/ontology/growth-policy-panel";
import { ProposalQueue } from "@/components/ontology/proposal-queue";
import { NodeKindGrid } from "@/components/ontology/node-kind-grid";
import { RelationList } from "@/components/ontology/relation-list";
import { AddKindPanel } from "@/components/ontology/add-kind-panel";
import { DeprecateDialog } from "@/components/ontology/deprecate-dialog";
import { RenameDialog } from "@/components/ontology/rename-dialog";
import { IdentityConfirmDialog } from "@/components/ontology/identity-confirm-dialog";
import { SchemaView } from "@/components/ontology/schema-view";
import { DataSummary } from "@/components/ontology/data-summary";
import { AuditLog } from "@/components/ontology/audit-log";

type Target = { axis: "node" | "edge"; kind: string; identityProtected: boolean };
type ConfirmState = { classes: string[]; message: string | null; resolve: (ok: boolean) => void };
type Adding = { axis: "node" | "edge"; name: string; nonce: number } | null;

function errorText(err: unknown, fallback: string) {
  return err instanceof Error ? err.message : fallback;
}

export default function OntologyPage() {
  const { staff } = useStaffSession();
  const queryClient = useQueryClient();
  const actingAs = staff ? `${staff.name} (${staff.staffId})` : "";

  const packsQuery = useQuery({
    queryKey: ["ontology-packs"],
    queryFn: ({ signal }) => fetchOntologyPacks(signal),
    staleTime: 60_000,
  });
  const availablePacks = packsQuery.data?.packs ?? [];
  const enabledPacks = packsQuery.data?.enabled ?? [];
  const [chosenPack, setChosenPack] = React.useState<string | null>(null);
  // Default: the deployment's FIRST BOUND pack — the vocabulary extraction actually uses.
  const effectivePack = chosenPack ?? packsQuery.data?.default ?? "";

  const ontologyQuery = useQuery({
    queryKey: ["ontology", effectivePack],
    queryFn: ({ signal }) => fetchOntology(effectivePack, signal),
    enabled: !!effectivePack,
    retry: false,
  });
  const view = ontologyQuery.data;

  const reload = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["ontology", effectivePack] }),
      queryClient.invalidateQueries({ queryKey: ["ontology-packs"] }),
      queryClient.invalidateQueries({ queryKey: ["ontology-audit"] }),
    ]);

  const [adding, setAdding] = React.useState<Adding>(null);
  const [proposalBusyId, setProposalBusyId] = React.useState<string | null>(null);
  const [autoApproveRefusal, setAutoApproveRefusal] = React.useState<string | null>(null);
  const [removalTarget, setRemovalTarget] = React.useState<Target | null>(null);
  const [removalBusy, setRemovalBusy] = React.useState(false);
  const [removalError, setRemovalError] = React.useState<string | null>(null);
  const [renameTarget, setRenameTarget] = React.useState<Target | null>(null);
  const [renameBusy, setRenameBusy] = React.useState(false);
  const [renameError, setRenameError] = React.useState<string | null>(null);
  const [pageError, setPageError] = React.useState<string | null>(null);
  const [confirmState, setConfirmState] = React.useState<ConfirmState | null>(null);

  // --- the explicit identity confirmation -----------------------------------------------------
  const askConfirm = (classes: string[], message: string | null) =>
    new Promise<boolean>((resolve) => setConfirmState({ classes, message, resolve }));

  /** Run an owner edit; if the gateway answers IDENTITY_CONFIRMATION_REQUIRED, ask the human and
   *  re-send with `confirm_identity: true` only on an explicit yes. `null` = the human declined. */
  async function guarded<T>(fn: (confirm: boolean) => Promise<T>): Promise<T | null> {
    try {
      return await fn(false);
    } catch (err) {
      const classes = identityClassesOf(err);
      if (classes === null) throw err;
      const ok = await askConfirm(classes, errorText(err, ""));
      if (!ok) return null;
      return fn(true);
    }
  }

  async function handleDefine(input: DefineKindInput) {
    const result = await guarded((confirm) => defineKind({ ...input, confirm_identity: confirm }));
    if (result) await reload();
    return result;
  }

  async function handleSetEndpoints(edge: EdgeKindView, src: string[], dst: string[]) {
    const result = await guarded((confirm) =>
      setEndpoints({ pack: effectivePack, kind: edge.kind, src, dst, by: actingAs, confirm_identity: confirm }),
    );
    if (result) await reload();
    return !!result;
  }

  async function handleRename(newName: string, reason: string) {
    if (!renameTarget) return;
    setRenameBusy(true);
    setRenameError(null);
    try {
      const result = await guarded((confirm) =>
        renameKind({
          pack: effectivePack,
          axis: renameTarget.axis,
          kind: renameTarget.kind,
          new_name: newName,
          by: actingAs,
          reason,
          confirm_identity: confirm,
        }),
      );
      if (result) {
        setRenameTarget(null);
        await reload();
      }
    } catch (err) {
      setRenameError(errorText(err, "the gateway refused the rename"));
    } finally {
      setRenameBusy(false);
    }
  }

  async function handleDecide(proposal: OntologyProposalRecord, approve: boolean) {
    if (!actingAs) return;
    setProposalBusyId(proposal.id);
    setPageError(null);
    try {
      await (approve ? approveProposal : rejectProposal)(proposal.id, actingAs);
      await reload();
    } catch (err) {
      setPageError(errorText(err, "the gateway refused the decision"));
    } finally {
      setProposalBusyId(null);
    }
  }

  async function handleToggleAutoApprove() {
    if (!view) return;
    const next = !view.auto_approve.enabled;
    if (next && !actingAs) {
      setAutoApproveRefusal("Sign in for this shift first — turning the gate off records who turned it off.");
      return;
    }
    try {
      await setAutoApprove(next, actingAs);
      setAutoApproveRefusal(null);
      await reload();
    } catch (err) {
      setAutoApproveRefusal(err instanceof OntologyApiError ? err.message : "the gateway refused the change");
    }
  }

  function target(kind: NodeKindView | EdgeKindView, axis: "node" | "edge"): Target {
    return { axis, kind: kind.kind, identityProtected: kind.identity_protected };
  }

  async function confirmRemoval(reason: string) {
    if (!removalTarget || !actingAs) return;
    let confirmIdentity = false;
    if (removalTarget.identityProtected) {
      const ok = await askConfirm([], `removes the identity-protected kind ${removalTarget.kind}`);
      if (!ok) return;
      confirmIdentity = true;
    }
    setRemovalBusy(true);
    setRemovalError(null);
    try {
      await deprecateKind({
        pack: effectivePack,
        axis: removalTarget.axis,
        kind: removalTarget.kind,
        by: actingAs,
        reason,
        confirm_identity: confirmIdentity,
      });
      setRemovalTarget(null);
      await reload();
    } catch (err) {
      setRemovalError(errorText(err, "the gateway refused the removal"));
    } finally {
      setRemovalBusy(false);
    }
  }

  async function reactivate(kind: NodeKindView | EdgeKindView, axis: "node" | "edge") {
    if (!actingAs) {
      setPageError("Sign in for this shift first — reactivating a kind records who did it.");
      return;
    }
    setPageError(null);
    try {
      await reactivateKind({ pack: effectivePack, axis, kind: kind.kind, by: actingAs });
      await reload();
    } catch (err) {
      setPageError(errorText(err, "the gateway refused the reactivation"));
    }
  }

  const openAdd = (axis: "node" | "edge", name = "") => setAdding({ axis, name, nonce: Date.now() });

  const addPanel = (axis: "node" | "edge") =>
    view && adding?.axis === axis ? (
      <div className="mb-3">
        <AddKindPanel
          key={adding.nonce}
          pack={effectivePack}
          metaKinds={view.meta_kinds}
          nodeKinds={view.node_kinds}
          attestationModes={view.attestation_modes ?? []}
          actingAs={actingAs}
          defaultAxis={axis}
          initialName={adding.name}
          onClose={() => setAdding(null)}
          onDefine={handleDefine}
        />
      </div>
    ) : null;

  const pendingProposals = view?.pending_proposals ?? [];
  const relationsNaming = (kind: string) =>
    (view?.edge_kinds ?? []).filter((e) => e.from.includes(kind) || e.to.includes(kind)).length;
  const packBound = enabledPacks.includes(effectivePack);

  return (
    <>
      <Topbar title="Ontology" />
      <div className="flex flex-col gap-6 p-4 sm:p-6">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-2xl">
            <h1 className="text-lg font-semibold text-fg">
              Entities and relations
              {view ? (
                <span className="text-sm font-normal text-subtle">
                  {" "}
                  · {view.title} ({view.pack} v{view.version})
                </span>
              ) : null}
            </h1>
            <p className="mt-1 text-xs leading-relaxed text-subtle">
              Define the kinds of things your knowledge graph holds and how they may relate. The
              system maps only to what is defined here — anything extraction finds that does not
              fit is refused, and counted below. Changes apply immediately and are recorded in the
              change history.
            </p>
            {view && !packBound ? (
              <p className="mt-1 text-2xs text-band-spot" data-testid="ontology-pack-unbound">
                {view.pack} is on disk but not bound by this deployment — edits are saved, but
                extraction does not use this pack until it is bound.
              </p>
            ) : null}
          </div>
          {packsQuery.data ? (
            <PackPicker
              packs={availablePacks}
              enabled={enabledPacks}
              active={effectivePack}
              onChange={(p) => {
                setChosenPack(p);
                setAdding(null);
              }}
            />
          ) : null}
        </header>

        {packsQuery.isSuccess && !effectivePack ? (
          <p className="rounded border border-dashed border-border px-3 py-6 text-center text-xs text-subtle">
            No ontology pack exists on this gateway yet, so there is nothing to define against.
          </p>
        ) : null}

        {ontologyQuery.isError || packsQuery.isError ? (
          <p
            className="rounded border border-band-review/40 bg-band-review/[0.06] px-3 py-2 text-xs text-band-review"
            role="alert"
            data-testid="ontology-error"
          >
            {ontologyQuery.error instanceof OntologyApiError
              ? `${ontologyQuery.error.code}: ${ontologyQuery.error.message}`
              : "Could not reach the gateway."}
          </p>
        ) : null}

        {pageError ? (
          <p className="text-2xs text-band-review" data-testid="ontology-page-refusal">
            Refused: {pageError}
          </p>
        ) : null}

        {view ? (
          <>
            <DataSummary
              data={view.data}
              onDefineNode={(name) => openAdd("node", name)}
              onDefineEdge={(name) => openAdd("edge", name)}
            />

            <section>
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-subtle">
                Schema — what the graph can say
              </h2>
              <SchemaView nodes={view.node_kinds} edges={view.edge_kinds} />
            </section>

            <section>
              <div className="mb-2 flex items-center justify-between">
                <h2 className="text-xs font-semibold uppercase tracking-wider text-subtle">
                  Entity kinds — {view.node_kinds.length} defined
                </h2>
                {adding?.axis !== "node" ? (
                  <button
                    type="button"
                    onClick={() => openAdd("node")}
                    data-testid="open-add-node"
                    className="inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-2xs text-subtle hover:text-fg"
                  >
                    <Plus className="h-3 w-3" /> Define entity kind
                  </button>
                ) : null}
              </div>
              {addPanel("node")}
              <NodeKindGrid
                nodes={view.node_kinds}
                catalog={view.meta_kinds}
                onRemove={(n) => {
                  setRemovalError(null);
                  setRemovalTarget(target(n, "node"));
                }}
                onReactivate={(n) => reactivate(n, "node")}
                onRename={(n) => {
                  setRenameError(null);
                  setRenameTarget(target(n, "node"));
                }}
              />
            </section>

            <section>
              <div className="mb-2 flex items-center justify-between">
                <h2 className="text-xs font-semibold uppercase tracking-wider text-subtle">
                  Relations — {view.edge_kinds.length} defined
                </h2>
                {adding?.axis !== "edge" ? (
                  <button
                    type="button"
                    onClick={() => openAdd("edge")}
                    data-testid="open-add-edge"
                    className="inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-2xs text-subtle hover:text-fg"
                  >
                    <Plus className="h-3 w-3" /> Define relation
                  </button>
                ) : null}
              </div>
              {addPanel("edge")}
              <RelationList
                edges={view.edge_kinds}
                catalog={view.meta_kinds}
                nodeKinds={view.node_kinds}
                onRemove={(e) => {
                  setRemovalError(null);
                  setRemovalTarget(target(e, "edge"));
                }}
                onReactivate={(e) => reactivate(e, "edge")}
                onRename={(e) => {
                  setRenameError(null);
                  setRenameTarget(target(e, "edge"));
                }}
                onSetEndpoints={handleSetEndpoints}
              />
            </section>

            <section>
              <div className="mb-2 flex items-baseline justify-between">
                <h2 className="text-xs font-semibold uppercase tracking-wider text-subtle">
                  Suggested by agents — awaiting a decision
                </h2>
                <span className="text-2xs text-subtle">{pendingProposals.length} pending</span>
              </div>
              <p className="mb-2 text-2xs leading-relaxed text-subtle">
                Kinds suggested by agents and adapter agents never apply on their own — each waits
                here for a named human, whatever the auto-approve setting below says.
              </p>
              <ProposalQueue
                proposals={pendingProposals}
                decidedBy={actingAs}
                onDecide={handleDecide}
                busyId={proposalBusyId}
              />
            </section>

            <GrowthPolicyPanel
              enabled={view.auto_approve.enabled}
              setBy={view.auto_approve.set_by}
              neverCovers={view.auto_approve_never_covers}
              actingAs={actingAs}
              onToggle={handleToggleAutoApprove}
              refusal={autoApproveRefusal}
            />

            <AuditLog pack={effectivePack} />
          </>
        ) : ontologyQuery.isLoading || packsQuery.isLoading ? (
          <p className="text-xs text-subtle">Loading…</p>
        ) : null}
      </div>

      {removalTarget ? (
        <DeprecateDialog
          open={!!removalTarget}
          onOpenChange={(open) => {
            if (!open) {
              setRemovalTarget(null);
              setRemovalError(null);
            }
          }}
          axis={removalTarget.axis}
          kind={removalTarget.kind}
          actingAs={actingAs}
          onConfirm={confirmRemoval}
          busy={removalBusy}
          error={removalError}
        />
      ) : null}

      {renameTarget ? (
        <RenameDialog
          open={!!renameTarget}
          axis={renameTarget.axis}
          kind={renameTarget.kind}
          relationCount={renameTarget.axis === "node" ? relationsNaming(renameTarget.kind) : 0}
          busy={renameBusy}
          error={renameError}
          onConfirm={handleRename}
          onOpenChange={(open) => {
            if (!open) setRenameTarget(null);
          }}
        />
      ) : null}

      <IdentityConfirmDialog
        open={!!confirmState}
        classes={confirmState?.classes ?? []}
        message={confirmState?.message}
        onConfirm={() => {
          confirmState?.resolve(true);
          setConfirmState(null);
        }}
        onCancel={() => {
          confirmState?.resolve(false);
          setConfirmState(null);
        }}
      />
    </>
  );
}
