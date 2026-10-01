"use client";
/**
 * Tab 2 of `/builder` — the Adapter Builder.
 *
 * The founder's own sequence: new adapter, select an MCP, a POP-UP configures it, then read or
 * write, a prompt for the adapter agent, skills, configure the adapter agent, compile. Every step
 * after the modal produces the SAME declaration `/builder`'s Agent Builder pane compiles through
 * `load_mcp_declaration` — the modal just narrows what step 2 selected into something the rest of
 * the wizard can build on top of, exactly the way the founder described the adapter/connector
 * split: an MCP is only ever configured *inside* an adapter, never as a route of its own.
 *
 * Gate: steps 4-8 stay disabled until the MCP modal has been confirmed at least once for the
 * selected server (`mcpConfigured`). Choosing a `not_yet_vendored` server never unlocks them —
 * there is nothing real to configure yet, and the search card already says so honestly.
 */
import * as React from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Blocks, RotateCcw, Settings2 } from "lucide-react";
import { connectorApi, type CatalogueEntry, type Mode } from "@/lib/connector-api";
import { CatalogueSearch } from "@/components/connectors/catalogue-search";
import { ModeToggle } from "@/components/connectors/mode-toggle";
import { SkillsPicker } from "@/components/connectors/skills-picker";
import { ExtractionContext } from "@/components/connectors/extraction-context";
import {
  CompiledConnectorView,
  ConnectorRefusalView,
  GeneratedDeclaration,
} from "@/components/connectors/compiled-connector";
import { RefusalCard } from "@/components/refusal-card";
import { StepShell } from "@/components/builder/step-shell";
import { NewAdapterStep } from "@/components/builder/new-adapter-step";
import { McpConfigModal } from "@/components/builder/mcp-config-modal";
import { AdapterAgentConfig } from "@/components/builder/adapter-agent-config";

export function AdapterBuilder() {
  const [name, setName] = React.useState("");
  const [query, setQuery] = React.useState("");
  const [selected, setSelected] = React.useState<CatalogueEntry | null>(null);
  const [modalOpen, setModalOpen] = React.useState(false);
  const [mcpConfigured, setMcpConfigured] = React.useState(false);
  const [envNames, setEnvNames] = React.useState<string[]>([]);
  const [mode, setMode] = React.useState<Mode>("read");
  const [skillIds, setSkillIds] = React.useState<string[]>([]);
  const [context, setContext] = React.useState("");
  const [agentRowLimit, setAgentRowLimit] = React.useState(200);
  const [agentNamedQueries, setAgentNamedQueries] = React.useState("");

  const catalogueQ = useQuery({
    queryKey: ["connector-catalogue", query],
    queryFn: () => connectorApi.catalogue(query),
  });
  const skillsQ = useQuery({ queryKey: ["connector-skills"], queryFn: () => connectorApi.skills() });

  const compile = useMutation({
    mutationFn: () =>
      connectorApi.compile({
        server_id: selected!.id,
        mode,
        skills: skillIds,
        extraction_context: context,
        env_names: envNames,
        agent_row_limit: agentRowLimit,
        agent_named_queries: agentNamedQueries,
        name: name.trim() || undefined,
      }),
  });

  const entries = catalogueQ.data?.ok ? catalogueQ.data.data.servers : [];
  const catalogueRefusal = catalogueQ.data && !catalogueQ.data.ok ? catalogueQ.data.refusal : null;
  const skills = skillsQ.data?.ok ? skillsQ.data.data.skills : [];
  const skillsNote = skillsQ.data?.ok ? skillsQ.data.data.note : null;

  function selectEntry(entry: CatalogueEntry) {
    setSelected(entry);
    setMcpConfigured(false);
    setEnvNames([]);
    compile.reset();
    if (entry.status === "vendored") setModalOpen(true);
  }

  function toggleSkill(id: string) {
    setSkillIds((prev) => (prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]));
    compile.reset();
  }

  function reset() {
    setName("");
    setSelected(null);
    setModalOpen(false);
    setMcpConfigured(false);
    setEnvNames([]);
    setMode("read");
    setSkillIds([]);
    setContext("");
    setAgentRowLimit(200);
    setAgentNamedQueries("");
    compile.reset();
  }

  const stepsUnlocked = mcpConfigured && !!selected;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <p className="max-w-2xl text-xs text-subtle">
          An MCP is configured only from inside an adapter — there is no standalone connector
          route. Select a server and the config dialog opens over this builder, the way it does in
          Claude Desktop or ChatGPT.
        </p>
        {selected ? (
          <button
            onClick={reset}
            className="flex shrink-0 items-center gap-1.5 rounded border border-border px-2.5 py-1.5 text-2xs text-subtle transition-colors hover:text-fg"
          >
            <RotateCcw className="h-3 w-3" />
            Start over
          </button>
        ) : null}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="flex flex-col gap-6">
          <StepShell n={1} title="New adapter">
            <NewAdapterStep name={name} onNameChange={setName} />
          </StepShell>

          <StepShell n={2} title="Select an MCP">
            {catalogueRefusal ? <RefusalCard refusal={catalogueRefusal} /> : null}
            <CatalogueSearch
              entries={entries}
              loading={catalogueQ.isLoading}
              query={query}
              onQueryChange={setQuery}
              selectedId={selected?.id ?? null}
              onSelect={selectEntry}
            />
            {selected && selected.status === "vendored" ? (
              <div className="mt-2 flex items-center justify-between gap-2 rounded border border-border bg-muted/40 px-3 py-2">
                <span className="text-2xs text-subtle">
                  {mcpConfigured ? (
                    <>
                      <span className="text-band-auto">Configured</span> — {envNames.length} env
                      var{envNames.length === 1 ? "" : "s"} declared.
                    </>
                  ) : (
                    "Configuration required before the next steps unlock."
                  )}
                </span>
                <button
                  onClick={() => setModalOpen(true)}
                  data-testid="mcp-reconfigure"
                  className="flex shrink-0 items-center gap-1 rounded border border-border px-2 py-1 text-2xs text-subtle hover:text-fg"
                >
                  <Settings2 className="h-3 w-3" />
                  {mcpConfigured ? "Reconfigure" : "Configure"}
                </button>
              </div>
            ) : null}
          </StepShell>

          <StepShell
            n={4}
            title="Read or write"
            disabled={!stepsUnlocked}
            hint={!stepsUnlocked ? "configure the MCP first" : undefined}
          >
            <ModeToggle
              mode={mode}
              onChange={(m) => {
                setMode(m);
                compile.reset();
              }}
            />
          </StepShell>

          <StepShell
            n={5}
            title="A prompt for the adapter agent"
            disabled={!stepsUnlocked}
            hint={!stepsUnlocked ? "configure the MCP first" : "optional"}
          >
            <ExtractionContext value={context} onChange={setContext} />
          </StepShell>

          <StepShell
            n={6}
            title="Adapter-agent skills"
            disabled={!stepsUnlocked}
            hint={!stepsUnlocked ? "configure the MCP first" : "optional"}
          >
            <SkillsPicker skills={skills} note={skillsNote} selected={skillIds} onToggle={toggleSkill} />
          </StepShell>

          <StepShell
            n={7}
            title="Configure the adapter agent"
            disabled={!stepsUnlocked}
            hint={!stepsUnlocked ? "configure the MCP first" : undefined}
          >
            <AdapterAgentConfig
              rowLimit={agentRowLimit}
              onRowLimitChange={setAgentRowLimit}
              namedQueries={agentNamedQueries}
              onNamedQueriesChange={setAgentNamedQueries}
            />
          </StepShell>

          <div className="flex items-center gap-3">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-muted text-2xs font-semibold text-fg">
              8
            </span>
            <button
              onClick={() => compile.mutate()}
              disabled={!stepsUnlocked || selected?.status !== "vendored" || compile.isPending}
              data-testid="connector-compile-button"
              className="rounded bg-accent px-4 py-2 text-xs font-semibold text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {compile.isPending ? "Compiling…" : "Compile"}
            </button>
          </div>
        </div>

        {/* Right column: the compiled result, or the refusal. */}
        <div className="flex flex-col gap-3 lg:sticky lg:top-6 lg:self-start">
          <p className="flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wide text-subtle">
            <Blocks className="h-3.5 w-3.5" aria-hidden />
            Compiles to
          </p>
          {!selected ? (
            <p className="rounded border border-border bg-surface px-4 py-8 text-center text-2xs text-subtle">
              Pick a server to begin.
            </p>
          ) : !stepsUnlocked ? (
            <p className="rounded border border-border bg-surface px-4 py-8 text-center text-2xs text-subtle">
              Configure the MCP to continue.
            </p>
          ) : !compile.data ? (
            <p className="rounded border border-border bg-surface px-4 py-8 text-center text-2xs text-subtle">
              Press <span className="text-fg">Compile</span> to run these steps through the real
              loader. Try flipping to Write first — the refusal is real.
            </p>
          ) : compile.data.compiled && compile.data.compiles_to ? (
            <>
              <CompiledConnectorView out={compile.data.compiles_to} />
              {compile.data.declaration ? <GeneratedDeclaration text={compile.data.declaration} /> : null}
            </>
          ) : compile.data.refusal ? (
            <>
              <ConnectorRefusalView refusal={compile.data.refusal} />
              {compile.data.declaration ? <GeneratedDeclaration text={compile.data.declaration} /> : null}
            </>
          ) : null}
        </div>
      </div>

      <p className="max-w-3xl pb-2 text-2xs text-subtle">
        Synthetic data only. Nothing configured here is persisted — the declaration is compiled in
        a temp file and deleted, the same containment the Agent Builder tab uses.
      </p>

      {modalOpen && selected ? (
        <McpConfigModal
          entry={selected}
          initialEnvNames={envNames}
          onClose={() => setModalOpen(false)}
          onConfirm={(names) => {
            setEnvNames(names);
            setMcpConfigured(true);
            setModalOpen(false);
            compile.reset();
          }}
        />
      ) : null}
    </div>
  );
}
