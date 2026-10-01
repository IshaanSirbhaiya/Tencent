"use client";
/**
 * "Add a source" — register an MCP server with the gateway (`POST /sources`).
 *
 * Four ways, matching the gateway's modes:
 *   import      the servers declared in the deployment's infra file (`infra/mcp_servers.yaml`)
 *   command     a stdio program the gateway launches on its own host
 *   url         a streamable-HTTP MCP endpoint
 *   mcpjungle   a server already registered in the MCPJungle aggregator
 *
 * Secrets are never typed here. Environment variables and headers are declared by NAME: "the child
 * variable X takes its value from the host variable Y at connect time". This form offers no value
 * field, AND refuses to send a URL, command or argument that carries a literal credential
 * (`user:pass@`, `?token=…`, `--api-key <value>`, a well-known key shape), in every mode including
 * import — `registrationProblems` in
 * `lib/sources-api.ts`. The gateway's own refusal is a second wall, not the first.
 */
import * as React from "react";
import { useMutation } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import {
  registrationProblems,
  sourcesApi,
  type Connection,
  type RegisterBody,
  type Scope,
  type SourcesFailure,
} from "@/lib/sources-api";
import { cn } from "@/lib/utils";
import { FailureNotice, Warning } from "./common";

type Mode = RegisterBody["mode"];

const MODES: { id: Mode; label: string; hint: string }[] = [
  { id: "import", label: "Declared servers", hint: "Import the servers this deployment declares in its infra file." },
  { id: "command", label: "By command", hint: "A program the gateway starts on its host and talks to over stdio." },
  { id: "url", label: "By URL", hint: "An MCP server reachable over streamable HTTP." },
  { id: "mcpjungle", label: "Via MCPJungle", hint: "A server already registered in the MCPJungle aggregator." },
];

const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;
const HEADER_NAME = /^[A-Za-z0-9-]+$/;
const ID_RE = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/;

interface Pair {
  key: string;
  host: string;
}

function lines(s: string): string[] {
  return s
    .split(/\r?\n/)
    .map((x) => x.trim())
    .filter(Boolean);
}

// `group` renders a <fieldset>/<legend> for a field holding several controls (NamePairs). Wrapping
// those in a <label> folds the label text into every inner button's accessible name, so the "Add"
// button stopped being findable by name (E2E run 4, SPEC5).
function Field({ label, hint, group, children }: { label: string; hint?: React.ReactNode; group?: boolean; children: React.ReactNode }) {
  if (group) {
    return (
      <fieldset className="block space-y-1">
        <legend className="text-xs font-medium text-fg">{label}</legend>
        {children}
        {hint && <span className="block text-2xs text-subtle">{hint}</span>}
      </fieldset>
    );
  }
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-fg">{label}</span>
      {children}
      {hint && <span className="block text-2xs text-subtle">{hint}</span>}
    </label>
  );
}

function NamePairs({
  pairs,
  setPairs,
  keyLabel,
  keyPattern,
  testId,
}: {
  pairs: Pair[];
  setPairs: (p: Pair[]) => void;
  keyLabel: string;
  keyPattern: RegExp;
  testId: string;
}) {
  return (
    <div className="space-y-1.5" data-testid={testId}>
      {pairs.map((p, i) => {
        const badKey = p.key !== "" && !keyPattern.test(p.key);
        const badHost = p.host !== "" && !ENV_NAME.test(p.host);
        return (
          <div key={i} className="flex items-center gap-2">
            <Input
              aria-label={keyLabel}
              placeholder={keyLabel}
              value={p.key}
              onChange={(e) => setPairs(pairs.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))}
              className={cn("h-8 font-mono text-xs", badKey && "border-band-review")}
            />
            <span className="shrink-0 text-2xs text-subtle">from host variable</span>
            <Input
              aria-label="host environment variable name"
              placeholder="HOST_VAR_NAME"
              value={p.host}
              onChange={(e) => setPairs(pairs.map((x, j) => (j === i ? { ...x, host: e.target.value } : x)))}
              className={cn("h-8 font-mono text-xs", badHost && "border-band-review")}
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="remove"
              onClick={() => setPairs(pairs.filter((_, j) => j !== i))}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        );
      })}
      <Button type="button" variant="outline" size="sm" onClick={() => setPairs([...pairs, { key: "", host: "" }])}>
        <Plus className="h-3.5 w-3.5" /> Add
      </Button>
    </div>
  );
}

export function AddSourceForm({
  onRegistered,
  onCancel,
}: {
  onRegistered: (registered: Connection[]) => void;
  onCancel: () => void;
}) {
  const [mode, setMode] = React.useState<Mode>("command");
  const [id, setId] = React.useState("");
  const [title, setTitle] = React.useState("");
  const [command, setCommand] = React.useState("");
  const [args, setArgs] = React.useState("");
  const [envFrom, setEnvFrom] = React.useState<Pair[]>([]);
  const [url, setUrl] = React.useState("");
  const [headersFrom, setHeadersFrom] = React.useState<Pair[]>([]);
  const [jungleServer, setJungleServer] = React.useState("");
  const [route, setRoute] = React.useState<"direct" | "mcpjungle">("direct");
  const [only, setOnly] = React.useState("");
  const [roots, setRoots] = React.useState("");
  const [include, setInclude] = React.useState("");
  const [exclude, setExclude] = React.useState("");
  const [blocked, setBlocked] = React.useState("");
  const [fileTypes, setFileTypes] = React.useState("");
  const [replace, setReplace] = React.useState(false);
  const [ack, setAck] = React.useState(false);
  const [failure, setFailure] = React.useState<SourcesFailure | null>(null);

  const problems: string[] = [];
  if (mode !== "import") {
    if (!id) problems.push("Give the source an id.");
    else if (!ID_RE.test(id)) problems.push("The id may use letters, digits, '.', '_' and '-' only.");
  }
  if (mode === "command") {
    if (!command.trim()) problems.push("Enter the command to launch.");
    if (!ack) problems.push("Confirm that this gateway may run the program.");
    if (envFrom.some((p) => !ENV_NAME.test(p.key) || !ENV_NAME.test(p.host)))
      problems.push("Environment entries must be variable NAMES (letters, digits, underscore).");
  }
  if (mode === "url") {
    if (!/^https?:\/\//i.test(url.trim())) problems.push("Enter an http(s) URL.");
    if (headersFrom.some((p) => !HEADER_NAME.test(p.key) || !ENV_NAME.test(p.host)))
      problems.push("Header entries must be a header name and a host variable NAME.");
    if (headersFrom.length > 0 && /^http:\/\//i.test(url.trim()) && !/^http:\/\/(localhost|127\.0\.0\.1|\[::1\])([:/]|$)/i.test(url.trim()))
      problems.push("Headers taken from host variables are credentials: use an https URL (or a loopback address) so they are not sent in clear.");
  }
  // No literal secret may leave this form, in ANY mode — import's server-id list is free text too,
  // and a credential flag typed into the command field is refused like one in the arguments
  // (R-019 round 3, S1). The gateway validates independently.
  problems.push(
    ...registrationProblems({
      mode,
      only,
      url,
      command,
      args: lines(args),
      other: mode === "import" ? [] : [id, title, jungleServer, roots, include, exclude, blocked, fileTypes],
    }),
  );

  const buildBody = (): RegisterBody => {
    if (mode === "import") {
      const ids = only.split(/[\s,]+/).filter(Boolean);
      return { mode, route, ...(ids.length ? { only: ids } : {}) };
    }
    const scope: Scope = {};
    if (lines(roots).length) scope.roots = lines(roots);
    if (lines(include).length) scope.include = lines(include);
    if (lines(exclude).length) scope.exclude = lines(exclude);
    if (lines(blocked).length) scope.blocked = lines(blocked);
    const ft = fileTypes.split(/[\s,]+/).filter(Boolean);
    if (ft.length) scope.file_types = ft;
    const base: RegisterBody = { mode, id, title: title || id, scope, replace };
    if (mode === "command") {
      return {
        ...base,
        command: command.trim(),
        args: lines(args),
        env_from: Object.fromEntries(envFrom.map((p) => [p.key, p.host])),
      };
    }
    if (mode === "url") {
      return { ...base, url: url.trim(), headers_from: Object.fromEntries(headersFrom.map((p) => [p.key, p.host])) };
    }
    return { ...base, jungle_server: jungleServer.trim() || id };
  };

  const register = useMutation({
    mutationFn: () => sourcesApi.register(buildBody()),
    onSuccess: (r) => {
      if (r.ok) {
        setFailure(null);
        onRegistered(r.data.registered);
      } else {
        setFailure(r);
      }
    },
  });

  return (
    <form
      data-testid="add-source-form"
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (problems.length === 0) register.mutate();
      }}
    >
      <div className="flex flex-wrap gap-1.5" role="tablist">
        {MODES.map((m) => (
          <button
            key={m.id}
            type="button"
            role="tab"
            aria-selected={mode === m.id}
            data-testid={`add-mode-${m.id}`}
            onClick={() => {
              setMode(m.id);
              setFailure(null);
            }}
            className={cn(
              "rounded border px-2.5 py-1 text-xs",
              mode === m.id ? "border-accent bg-accent/10 text-accent" : "border-border text-subtle hover:bg-muted",
            )}
          >
            {m.label}
          </button>
        ))}
      </div>
      <p className="text-xs text-subtle">{MODES.find((m) => m.id === mode)?.hint}</p>

      {mode === "import" ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Route" hint="Direct launches each server here; MCPJungle calls it through the aggregator.">
            <select
              data-testid="add-route"
              value={route}
              onChange={(e) => setRoute(e.target.value as "direct" | "mcpjungle")}
              className="h-9 w-full rounded border border-border bg-surface px-2 text-sm"
            >
              <option value="direct">Direct</option>
              <option value="mcpjungle">MCPJungle</option>
            </select>
          </Field>
          <Field label="Only these server ids (optional)" hint="Comma-separated. Empty imports every declared server.">
            <Input data-testid="add-only" value={only} onChange={(e) => setOnly(e.target.value)} className="font-mono text-xs" />
          </Field>
          <div className="sm:col-span-2">
            <Warning>
              A declared server with a direct stdio route is a program this gateway will launch on its host when a
              plan is proposed or a run starts. Import only declarations you have reviewed.
            </Warning>
          </div>
        </div>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Source id" hint="Names files on the gateway; it cannot be changed later.">
              <Input data-testid="add-id" value={id} onChange={(e) => setId(e.target.value)} className="font-mono text-xs" />
            </Field>
            <Field label="Title">
              <Input data-testid="add-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder={id} />
            </Field>
          </div>

          {mode === "command" && (
            <div className="space-y-3">
              <Warning testId="command-warning">
                <strong>A command source runs a program on the gateway&apos;s host.</strong> When you propose a plan
                or start a run, the gateway launches exactly this command with the gateway&apos;s own permissions.
                Register only a program you trust.
              </Warning>
              <Field label="Command" hint="The executable, e.g. npx, uvx or a full path.">
                <Input data-testid="add-command" value={command} onChange={(e) => setCommand(e.target.value)} className="font-mono text-xs" />
              </Field>
              <Field label="Arguments" hint="One per line.">
                <Textarea data-testid="add-args" rows={3} value={args} onChange={(e) => setArgs(e.target.value)} />
              </Field>
              <Field
                group
                label="Environment variables (names only)"
                hint="The server's variable takes its value from a variable already set on the gateway host. Values are never entered or stored here."
              >
                <NamePairs pairs={envFrom} setPairs={setEnvFrom} keyLabel="SERVER_VAR_NAME" keyPattern={ENV_NAME} testId="add-env-from" />
              </Field>
              <label className="flex items-start gap-2 text-xs">
                <input type="checkbox" data-testid="add-ack" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-0.5" />
                <span>I understand this gateway will run this program on its host.</span>
              </label>
            </div>
          )}

          {mode === "url" && (
            <div className="space-y-3">
              <Field label="Server URL">
                <Input data-testid="add-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…/mcp" className="font-mono text-xs" />
              </Field>
              <Field group label="Headers (names only)" hint="Each header's value is read from a host variable at connect time.">
                <NamePairs pairs={headersFrom} setPairs={setHeadersFrom} keyLabel="Header-Name" keyPattern={HEADER_NAME} testId="add-headers-from" />
              </Field>
            </div>
          )}

          {mode === "mcpjungle" && (
            <Field
              label="Server name in MCPJungle"
              hint="Defaults to the source id. The aggregator's address comes from MCPJUNGLE_URL on the gateway host."
            >
              <Input data-testid="add-jungle" value={jungleServer} onChange={(e) => setJungleServer(e.target.value)} placeholder={id} className="font-mono text-xs" />
            </Field>
          )}

          <details className="rounded border border-border">
            <summary className="cursor-pointer px-3 py-2 text-xs font-medium">Scope — what may be collected</summary>
            <div className="grid gap-3 p-3 sm:grid-cols-2">
              <Field label="Roots" hint="One per line. Empty uses the server's own roots.">
                <Textarea rows={2} value={roots} onChange={(e) => setRoots(e.target.value)} />
              </Field>
              <Field label="File types" hint="e.g. .pdf, .md — empty means every type an extractor exists for.">
                <Input value={fileTypes} onChange={(e) => setFileTypes(e.target.value)} className="font-mono text-xs" />
              </Field>
              <Field label="Include patterns" hint="Globs, one per line.">
                <Textarea rows={2} value={include} onChange={(e) => setInclude(e.target.value)} />
              </Field>
              <Field label="Exclude patterns" hint="Out of scope — recorded as excluded.">
                <Textarea rows={2} value={exclude} onChange={(e) => setExclude(e.target.value)} />
              </Field>
              <Field label="Blocked patterns" hint="Never collected — recorded as blocked, e.g. **/pricing/**">
                <Textarea rows={2} value={blocked} onChange={(e) => setBlocked(e.target.value)} />
              </Field>
            </div>
          </details>

          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
            Replace the existing source if this id is already registered
          </label>
        </>
      )}

      {problems.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-2xs text-subtle" data-testid="add-problems">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      {failure && <FailureNotice failure={failure} testId="add-failure" />}

      <div className="flex items-center gap-2">
        <Button type="submit" data-testid="add-submit" disabled={problems.length > 0 || register.isPending}>
          {register.isPending ? "Registering…" : mode === "import" ? "Import declared servers" : "Register source"}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
