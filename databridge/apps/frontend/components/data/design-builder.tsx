"use client";
/**
 * Raw dataset by delegation (DSD) — describe the table you need, review and edit the proposed
 * TABLE DESIGN, approve it, and the main agent has each source's adapter agent supply its part.
 *
 *   1. describe   → POST /materialise/designs           a proposed design (grain, columns, label,
 *                                                       approved join links, questions)
 *   2. edit       → PUT  /materialise/designs/{id}      rename / remove / add columns, the grain,
 *                                                       the label; any edit clears the approval
 *   3. approve    → POST /materialise/designs/{id}/approve   the signed-in person, this digest
 *   4. build      → POST /materialise/designs/{id}/build     only the approved digest
 *
 * The output is a RAW table: every cell has a pointer, time is columns (never a filter), and the
 * receipt says plainly it is not cleaned, imputed or leakage-filtered. Each column says WHEN its
 * value became known (or that this is unknown) so the engineer decides about leakage.
 * Everything drawn here is the gateway's answer; nothing is computed on the page.
 */
import * as React from "react";
import { AlertTriangle, CheckCircle2, Download, Loader2, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import {
  designApi,
  fetchRunFile,
  saveBlob,
  type DesignColumn,
  type DesignDoc,
  type DesignJob,
  type Refusal,
  type RawRunSummary,
  type TableDesign,
} from "@/lib/data-api";

const ROLES: DesignColumn["role"][] = ["key", "feature", "label", "time"];
const AGGS = ["", "count", "sum", "avg", "min", "max"] as const;
const FILTER_OPS = ["eq", "ne", "lt", "le", "gt", "ge", "contains", "is_null", "not_null"] as const;
type DesignFilter = TableDesign["filters"][number];

function RefusalNote({ r }: { r: Refusal }) {
  return (
    <div className="flex items-start gap-1.5 rounded border border-band-review/40 bg-band-review/10 p-2 text-2xs text-band-review" data-testid="design-refusal">
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>
        <span className="font-mono">{r.code}</span>: {r.message}
      </span>
    </div>
  );
}

function sameColumns(a: DesignColumn[], b: DesignColumn[]) {
  return JSON.stringify(a.map(strip)) === JSON.stringify(b.map(strip));
}

function strip(c: DesignColumn) {
  const { name, role, via, field, aggregate, meaning } = c;
  return { name, role, via, field, aggregate, meaning };
}

export function DesignBuilder() {
  const [text, setText] = React.useState("");
  const [doc, setDoc] = React.useState<DesignDoc | null>(null);
  const [cols, setCols] = React.useState<DesignColumn[]>([]);
  const [grainEntity, setGrainEntity] = React.useState("");
  const [labelCol, setLabelCol] = React.useState<string>("");
  const [labelDef, setLabelDef] = React.useState("");
  const [busy, setBusy] = React.useState<string | null>(null);
  const [refusal, setRefusal] = React.useState<Refusal | null>(null);
  const [job, setJob] = React.useState<DesignJob | null>(null);
  const [answers, setAnswers] = React.useState<Record<string, string>>({});
  const [newCol, setNewCol] = React.useState<{ field: string; via: string; aggregate: string }>({ field: "", via: "", aggregate: "" });
  // E2E5 F-10: the design's filters are editable. Each is pushed down to the source; a filter on a
  // time column is applied as a ROW SCOPE (the design says so), never dropped silently.
  const [filters, setFilters] = React.useState<DesignFilter[]>([]);
  const [newFilter, setNewFilter] = React.useState<{ field: string; op: string; value: string }>({ field: "", op: "eq", value: "" });

  const load = React.useCallback((d: DesignDoc) => {
    setDoc(d);
    setAnswers({});
    if (!d.design) return;
    setCols(d.design.columns);
    setFilters(d.design.filters);
    setGrainEntity(d.design.grain.entity);
    setLabelCol(d.design.label?.column ?? "");
    setLabelDef(d.design.label?.definition ?? "");
  }, []);

  const design = doc?.design ?? null;
  const answered = Object.fromEntries(Object.entries(answers).filter(([, v]) => v.trim()));
  const dirty =
    !!design &&
    (Object.keys(answered).length > 0 ||
      !sameColumns(cols, design.columns) ||
      JSON.stringify(filters) !== JSON.stringify(design.filters) ||
      grainEntity !== design.grain.entity ||
      labelCol !== (design.label?.column ?? "") ||
      labelDef !== (design.label?.definition ?? ""));

  async function propose() {
    if (!text.trim() || busy) return;
    setBusy("propose");
    setRefusal(null);
    setJob(null);
    const a = await designApi.propose(text.trim());
    setBusy(null);
    if (a.ok) load(a.body);
    else setRefusal(a.refusal);
  }

  async function save() {
    if (!doc || !design || busy) return;
    setBusy("save");
    setRefusal(null);
    const edit = {
      rev: doc.rev,
      ...(Object.keys(answered).length > 0 ? { answers: answered } : {}),
      columns: cols,
      filters,
      ...(grainEntity !== design.grain.entity ? { grain: { ...design.grain, entity: grainEntity }, links: [] } : {}),
      label: labelCol ? { column: labelCol, definition: labelDef || `the value of ${labelCol}` } : null,
    };
    const a = await designApi.edit(doc.id, edit);
    setBusy(null);
    if (a.ok) load(a.body);
    else setRefusal(a.refusal);
  }

  async function approve() {
    if (!doc || busy) return;
    setBusy("approve");
    setRefusal(null);
    const a = await designApi.approve(doc.id, doc.digest);
    setBusy(null);
    if (a.ok) load(a.body);
    else setRefusal(a.refusal);
  }

  async function build() {
    if (!doc || busy) return;
    setBusy("build");
    setRefusal(null);
    setJob(null);
    const a = await designApi.build(doc.id, doc.digest);
    if (!a.ok) {
      setBusy(null);
      setRefusal(a.refusal);
      return;
    }
    const jobId = a.body.job_id;
    for (let i = 0; i < 600; i++) {
      const j = await designApi.job(jobId);
      if (!j.ok) {
        setRefusal(j.refusal);
        break;
      }
      setJob(j.body);
      if (j.body.status !== "running") {
        if (j.body.refusal) setRefusal(j.body.refusal);
        break;
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
    setBusy(null);
  }

  function addColumn() {
    if (!newCol.field.trim()) return;
    const via = newCol.via || null;
    const agg = (newCol.aggregate || null) as DesignColumn["aggregate"];
    const base = newCol.field.trim();
    const name = via ? `${via}_${base === "*" ? "count" : base}` : base;
    setCols((cs) => [...cs, { name, field: base, via, aggregate: agg, role: "feature", meaning: "", when_known: "" }]);
    setNewCol({ field: "", via: "", aggregate: "" });
  }

  const result: RawRunSummary | null = job?.status === "succeeded" ? job.result : null;
  const approved = doc?.status === "approved" && !dirty;

  return (
    <div className="flex flex-col gap-3" data-testid="design-builder">
      <p className="text-xs text-subtle">
        Describe the table you need. Walnut proposes a design — one row per what, the columns and where each comes from, a label
        if a source records one, time kept as columns — you edit and approve it, and each source&apos;s adapter agent supplies its
        part. The result is a <span className="text-fg">raw</span> table with a pointer for every value: no cleaning, no imputation,
        no leakage filtering. Those decisions are yours.
      </p>
      <div className="flex flex-col gap-2 rounded border border-border bg-surface p-3">
        <Textarea
          data-testid="design-input"
          rows={2}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="e.g. payments data for fraud, to train XGBoost"
        />
        <div>
          <Button data-testid="design-propose" onClick={() => void propose()} disabled={!text.trim() || !!busy}>
            {busy === "propose" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            Propose a table design
          </Button>
        </div>
      </div>

      {refusal && <RefusalNote r={refusal} />}
      {doc && !design && (
        <RefusalNote r={{ code: "DESIGN_INTEGRITY_FAILED", message: `this design's sealed log or revisions do not verify (${doc.integrity}); nothing in it is honoured` }} />
      )}

      {doc && design && (
        <div className="flex flex-col gap-3 rounded border border-border bg-surface p-3 text-xs" data-testid="design-card">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="font-medium">{design.grain_text}</span>
            <span className="text-2xs text-subtle">
              grain{" "}
              <Input
                data-testid="design-grain"
                className="inline-block h-6 w-40 text-2xs"
                value={grainEntity}
                onChange={(e) => setGrainEntity(e.target.value)}
              />{" "}
              from <span className="font-mono">{design.grain.adapter_agent}</span> / <span className="font-mono">{design.grain.connection}</span>,
              key <span className="font-mono">{design.grain_key}</span>
            </span>
            <span className="ml-auto text-2xs text-subtle" data-testid="design-status">
              {doc.status} · revision {doc.rev} · digest <span className="font-mono">{doc.digest.slice(0, 12)}…</span>
              {doc.approval ? ` · approved by ${doc.approval.principal_id}` : ""}
            </span>
          </div>

          {design.questions.length > 0 && (
            <div className="rounded border border-band-review/40 bg-band-review/10 p-2" data-testid="design-questions">
              <div className="text-2xs font-medium text-band-review">
                Questions to answer before approval — each stays open until you answer it:
              </div>
              <ul className="flex list-disc flex-col gap-1 pl-4 text-2xs">
                {doc.question_items.map((q) => (
                  <li key={q.id}>
                    {q.text}
                    <Input
                      data-testid="design-answer"
                      className="mt-0.5 h-6 w-full text-2xs"
                      placeholder="your answer (saved with the edit)"
                      value={answers[q.id] ?? ""}
                      onChange={(e) => setAnswers((a) => ({ ...a, [q.id]: e.target.value }))}
                    />
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 text-2xs" data-testid="design-label">
            <span className="text-subtle">Label</span>
            <select className="h-6 rounded border border-border bg-transparent px-1" value={labelCol} onChange={(e) => setLabelCol(e.target.value)}>
              <option value="">none (no label is invented)</option>
              {cols.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name}
                </option>
              ))}
            </select>
            <Input className="h-6 w-80 text-2xs" placeholder="how the label is defined" value={labelDef} onChange={(e) => setLabelDef(e.target.value)} />
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-2xs" data-testid="design-columns">
              <thead className="text-left text-subtle">
                <tr>
                  <th className="py-1 pr-2">Column</th>
                  <th className="pr-2">Role</th>
                  <th className="pr-2">Supplied by</th>
                  <th className="pr-2">When known</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {cols.map((c, i) => {
                  const lk = design.links.find((l) => l.id === c.via);
                  return (
                    <tr key={`${c.name}-${i}`} className="border-t border-border align-top" data-testid="design-column">
                      <td className="py-1 pr-2">
                        <Input
                          className="h-6 w-44 font-mono text-2xs"
                          value={c.name}
                          onChange={(e) => setCols((cs) => cs.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))}
                        />
                        {c.meaning ? <div className="mt-0.5 text-subtle">{c.meaning}</div> : null}
                      </td>
                      <td className="pr-2">
                        <select
                          className="h-6 rounded border border-border bg-transparent px-1"
                          value={c.role}
                          onChange={(e) => setCols((cs) => cs.map((x, j) => (j === i ? { ...x, role: e.target.value as DesignColumn["role"] } : x)))}
                        >
                          {ROLES.map((r) => (
                            <option key={r} value={r}>
                              {r}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="pr-2">
                        <span className="font-mono">
                          {lk ? `${lk.target.adapter_agent} · ${lk.target.entity || lk.target.connection}` : `${design.grain.adapter_agent} · ${design.grain.entity}`}
                        </span>
                        <span className="text-subtle"> · {c.aggregate ? `${c.aggregate}(${c.field})` : c.field}</span>
                        {lk ? (
                          <div className="text-subtle" title={lk.evidence}>
                            via {lk.kind} {lk.from_field}
                            {lk.candidate ? " · person: candidate only, never merged (withheld)" : ""}
                          </div>
                        ) : null}
                      </td>
                      <td className="max-w-xs pr-2 text-subtle" data-testid="design-when-known">
                        {c.when_known || "(computed when saved)"}
                      </td>
                      <td>
                        <Button
                          variant="ghost"
                          size="sm"
                          aria-label={`remove ${c.name}`}
                          onClick={() => setCols((cs) => cs.filter((_x, j) => j !== i))}
                        >
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap items-center gap-1.5 text-2xs" data-testid="design-add">
            <span className="text-subtle">Add a column:</span>
            <select className="h-6 rounded border border-border bg-transparent px-1" value={newCol.via} onChange={(e) => setNewCol({ ...newCol, via: e.target.value })}>
              <option value="">{design.grain.entity} (this table)</option>
              {design.links.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.kind}: {l.id}
                </option>
              ))}
            </select>
            <Input className="h-6 w-36 font-mono text-2xs" placeholder="field (or * to count)" value={newCol.field} onChange={(e) => setNewCol({ ...newCol, field: e.target.value })} />
            <select className="h-6 rounded border border-border bg-transparent px-1" value={newCol.aggregate} onChange={(e) => setNewCol({ ...newCol, aggregate: e.target.value })}>
              {AGGS.map((a) => (
                <option key={a} value={a}>
                  {a || "no aggregate"}
                </option>
              ))}
            </select>
            <Button variant="outline" size="sm" onClick={addColumn} disabled={!newCol.field.trim()}>
              <Plus className="h-3 w-3" /> Add
            </Button>
          </div>

          <div className="space-y-1 text-2xs" data-testid="design-filters">
            <div className="text-subtle">
              Filters — each is applied at the source. A filter on a time column keeps only those rows (a row scope you chose); time stays a column.
            </div>
            {filters.map((f, i) => (
              <div key={`${f.field}-${f.op}-${i}`} className="flex items-center gap-2">
                <span className="font-mono">
                  {f.field} {f.op} {f.value === null || f.value === undefined ? "" : JSON.stringify(f.value)}
                </span>
                <Button variant="ghost" size="sm" aria-label={`Remove filter on ${f.field}`} onClick={() => setFilters((fs) => fs.filter((_x, j) => j !== i))}>
                  <Trash2 className="h-3 w-3" />
                </Button>
              </div>
            ))}
            <div className="flex flex-wrap items-center gap-2">
              <Input className="h-6 w-32 font-mono text-2xs" placeholder="column" value={newFilter.field} onChange={(e) => setNewFilter({ ...newFilter, field: e.target.value })} />
              <select className="h-6 rounded border border-border bg-transparent px-1" value={newFilter.op} onChange={(e) => setNewFilter({ ...newFilter, op: e.target.value })}>
                {FILTER_OPS.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
              {newFilter.op !== "is_null" && newFilter.op !== "not_null" ? (
                <Input className="h-6 w-36 font-mono text-2xs" placeholder="value" value={newFilter.value} onChange={(e) => setNewFilter({ ...newFilter, value: e.target.value })} />
              ) : null}
              <Button
                variant="outline"
                size="sm"
                data-testid="design-add-filter"
                disabled={!newFilter.field.trim() || (newFilter.op !== "is_null" && newFilter.op !== "not_null" && !newFilter.value.trim())}
                onClick={() => {
                  const nullary = newFilter.op === "is_null" || newFilter.op === "not_null";
                  setFilters((fs) => [...fs, { field: newFilter.field.trim(), op: newFilter.op, value: nullary ? null : newFilter.value }]);
                  setNewFilter({ field: "", op: "eq", value: "" });
                }}
              >
                <Plus className="h-3 w-3" /> Add filter
              </Button>
            </div>
          </div>

          {design.links.length > 0 && (
            <details className="text-2xs">
              <summary className="cursor-pointer text-subtle">Join links ({design.links.length}) — approved links only</summary>
              <ul className="mt-1 list-disc pl-4">
                {design.links.map((l) => (
                  <li key={l.id}>
                    <span className="font-mono">{l.id}</span>: {l.evidence}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {design.assumptions.length > 0 && (
            <ul className="list-disc pl-4 text-2xs text-subtle" data-testid="design-assumptions">
              {design.assumptions.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" data-testid="design-save" onClick={() => void save()} disabled={!dirty || !!busy}>
              {busy === "save" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
              Save edits
            </Button>
            <Button data-testid="design-approve" onClick={() => void approve()} disabled={dirty || !!busy || doc.status === "approved" || design.questions.length > 0}>
              {busy === "approve" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
              Approve this design
            </Button>
            <Button data-testid="design-build" onClick={() => void build()} disabled={!approved || !!busy}>
              {busy === "build" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
              Build the raw table
            </Button>
            {dirty ? <span className="text-2xs text-subtle">Unsaved edits — save, then approve.</span> : null}
          </div>
        </div>
      )}

      {job && job.status === "running" && (
        <div className="text-2xs text-subtle" data-testid="design-job">
          <Loader2 className="mr-1 inline h-3 w-3 animate-spin" />
          {job.progress?.stage ?? "running"}
        </div>
      )}

      {result && (
        <div className="flex flex-col gap-2 rounded border border-border bg-surface p-3 text-xs" data-testid="design-result">
          <div className="font-medium">{result.statement}</div>
          <div className="text-2xs text-subtle">{result.honest_summary}</div>
          <div className="text-2xs">
            {result.rows?.emitted ?? 0} rows · pointers re-read {result.pointer_check?.values_match ?? 0}/{result.pointer_check?.grounded_cells ?? 0}
            {" · "}
            {result.cells?.policy_suppressed ?? 0} withheld · {result.cells?.unresolved ?? 0} absent
          </div>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(result.file_roles ?? {})
              .filter(([, role]) => role !== "other")
              .map(([name, role]) => (
                <Button
                  key={name}
                  variant="outline"
                  size="sm"
                  data-testid="design-file"
                  onClick={() =>
                    void fetchRunFile(result.plan_hash, name)
                      .then((b) => saveBlob(b, name))
                      .catch((e: unknown) => setRefusal({ code: "DOWNLOAD_FAILED", message: e instanceof Error ? e.message : "download failed" }))
                  }
                >
                  <Download className="h-3 w-3" /> {name} <span className="text-subtle">({role})</span>
                </Button>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}
