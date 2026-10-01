"use client";
/**
 * Step 7 — configure the adapter agent.
 *
 * The founder listed this separately from the prompt ("add a prompt for the adapter agent... and
 * configure the adapter agent"), so it gets its own surface rather than being folded into step 5's
 * free text. It is shaped directly off `packages/adapter/databridge_adapter/shell.py::AdapterShell`
 * — the ONLY layer with a model in it, and the module's own claim is structural, not a promise:
 * "It never executes anything. It never sees a row. It holds no credential and no driver handle."
 *
 * Two fields are real, validated inputs on `ProposedRead` / `AdapterShell.propose`:
 *   - row ceiling — `ProposedRead.limit: int = Field(default=200, ge=1, le=100_000)`;
 *   - named queries — `AdapterShell.__init__(named_queries: Optional[Mapping[str, str]])`, the
 *     reviewed vocabulary a Shell may offer without inventing SQL.
 * Neither is WIRED to a live Shell from this HTTP surface (there is no reachable path from a
 * browser to one), so both are recorded on the generated declaration's inert `_connector.agent`
 * block — the same honest treatment `extraction_context` and `skills` already get, and for the
 * same reason: nothing outside that block is ever read by the compiler.
 *
 * Escalation behaviour is NOT a field here, because it is not a choice. `AdapterShell.validate`
 * either returns a request or raises `EscalationRequired` / `CapabilityNotAdvertised` — there is
 * no retry loop and no branch that repairs a bad proposal (§10: "Shell proposes an unadvertised
 * capability -> reject; log; do not escalate to a model"). Stated here rather than offered as a
 * toggle that would imply it is configurable.
 */
import { ListTree, ShieldOff, SlidersHorizontal } from "lucide-react";

const NOT_EXPOSED = [
  "a credential, connection string, or DSN — the Shell's constructor accepts none",
  "a driver handle or Core reference — there is no `resolve()` or `run()` on a Shell",
  "a retry-on-rejection toggle — a rejected proposal is logged and never fed back to a model",
];

export function AdapterAgentConfig({
  rowLimit,
  onRowLimitChange,
  namedQueries,
  onNamedQueriesChange,
}: {
  rowLimit: number;
  onRowLimitChange: (n: number) => void;
  namedQueries: string;
  onNamedQueriesChange: (v: string) => void;
}) {
  const MAX_CHARS = 2_000;
  const over = namedQueries.length > MAX_CHARS;
  return (
    <div className="flex flex-col gap-4">
      <div>
        <label className="mb-1 flex items-center gap-1.5 text-2xs font-medium text-fg">
          <SlidersHorizontal className="h-3 w-3 text-subtle" aria-hidden />
          Row ceiling per proposal
        </label>
        <div className="flex items-center gap-2">
          <input
            type="number"
            min={1}
            max={100_000}
            value={rowLimit}
            onChange={(e) => onRowLimitChange(Math.max(1, Math.min(100_000, Number(e.target.value) || 1)))}
            data-testid="agent-row-limit"
            className="w-32 rounded border border-border bg-bg px-2.5 py-1.5 text-xs text-fg outline-none focus:border-accent"
          />
          <span className="text-2xs text-subtle">
            rows — the same bound `ProposedRead.limit` enforces on every plan the Shell proposes
          </span>
        </div>
      </div>

      <div>
        <label className="mb-1 flex items-center gap-1.5 text-2xs font-medium text-fg">
          <ListTree className="h-3 w-3 text-subtle" aria-hidden />
          Reviewed named queries
        </label>
        <textarea
          value={namedQueries}
          onChange={(e) => onNamedQueriesChange(e.target.value)}
          rows={3}
          maxLength={MAX_CHARS + 200}
          placeholder={"One per line — name: what it returns\ne.g. recent_denials: permits denied in the last 30 days"}
          data-testid="agent-named-queries"
          className="w-full resize-y rounded border border-border bg-bg px-2.5 py-2 text-xs text-fg placeholder:text-subtle focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
        />
        <div className="mt-1 flex items-center justify-between text-2xs text-subtle">
          <span>
            The vocabulary a Shell may offer instead of writing SQL (§6, §9) — never executed by
            this form, and reviewed once before it means anything.
          </span>
          <span className={over ? "text-band-review" : undefined}>
            {namedQueries.length}/{MAX_CHARS}
          </span>
        </div>
      </div>

      <div className="rounded border border-border bg-muted/40 p-3">
        <p className="flex items-center gap-1.5 text-2xs font-medium text-fg">
          <ShieldOff className="h-3 w-3 text-subtle" aria-hidden />
          Not exposed here — the type forbids it
        </p>
        <ul className="mt-1.5 flex flex-col gap-1">
          {NOT_EXPOSED.map((item) => (
            <li key={item} className="flex gap-1.5 text-2xs text-subtle">
              <span className="text-band-spot">·</span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-2xs text-subtle">
          Escalation is fixed, not configured: a proposal outside the advertised capability set is
          rejected and logged, never retried automatically. A human adds a named query if it
          recurs.
        </p>
      </div>
    </div>
  );
}
