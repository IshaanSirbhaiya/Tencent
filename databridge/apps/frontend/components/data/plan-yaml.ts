/**
 * Reads the `plan.yaml` a run wrote (PyYAML `safe_dump` of the `MaterialisationPlan`) into a flat
 * `a.b.0.c -> value` map, so the page can say "here is what I understood" FROM THE PLAN THAT RAN —
 * not from a restatement of the request.
 *
 * Deliberately a reader for the block-style subset `safe_dump(sort_keys=False)` emits (block
 * mappings, indentless block sequences, plain / quoted scalars, folded continuation lines). It is
 * not a general YAML parser; when a line does not fit the subset the caller still shows the raw
 * file, so nothing is hidden behind a parse the page could not do.
 */

export type Flat = Record<string, string | null>;

const KEY_RE = /^([A-Za-z_][A-Za-z0-9_.-]*):(?: (.*))?$/;

function scalar(raw: string): string | null {
  const v = raw.trim();
  if (v === "null" || v === "~" || v === "") return null;
  if (v.startsWith("'") && v.endsWith("'") && v.length >= 2) return v.slice(1, -1).replace(/''/g, "'");
  if (v.startsWith('"') && v.endsWith('"') && v.length >= 2) {
    try {
      return JSON.parse(v) as string;
    } catch {
      return v.slice(1, -1);
    }
  }
  return v;
}

export function flattenPlanYaml(text: string): Flat {
  const out: Flat = {};
  const stack: { indent: number; path: string }[] = [{ indent: -1, path: "" }];
  const seqCount: Record<string, number> = {};
  let last: string | null = null;

  const join = (a: string, b: string) => (a ? `${a}.${b}` : b);

  function handle(indent: number, content: string) {
    if (content.startsWith("- ") || content === "-") {
      while (stack.length > 1 && stack[stack.length - 1].indent > indent) stack.pop();
      const owner = stack[stack.length - 1].path;
      const idx = seqCount[owner] ?? 0;
      seqCount[owner] = idx + 1;
      const itemPath = join(owner, String(idx));
      const rest = content.slice(2);
      if (!rest.trim()) {
        stack.push({ indent: indent + 0.5, path: itemPath });
        return;
      }
      const quoted = rest.startsWith("'") || rest.startsWith('"');
      if (!quoted && KEY_RE.test(rest)) {
        stack.push({ indent: indent + 0.5, path: itemPath });
        handle(indent + 2, rest);
      } else {
        out[itemPath] = scalar(rest);
        last = itemPath;
      }
      return;
    }
    const m = KEY_RE.exec(content);
    if (!m) {
      // a folded continuation of the previous plain scalar
      if (last && out[last] != null) out[last] = `${out[last]} ${content.trim()}`;
      return;
    }
    while (stack.length > 1 && stack[stack.length - 1].indent >= indent) stack.pop();
    const path = join(stack[stack.length - 1].path, m[1]);
    const value = m[2];
    if (value === undefined || value.trim() === "") {
      stack.push({ indent, path });
      last = null;
    } else {
      out[path] = value.trim() === "[]" || value.trim() === "{}" ? "" : scalar(value);
      last = path;
    }
  }

  for (const line of text.split(/\r?\n/)) {
    if (!line.trim() || line.trimStart().startsWith("#")) continue;
    const indent = line.length - line.trimStart().length;
    handle(indent, line.trim());
  }
  return out;
}

/** Items of a sequence at `prefix` (`prefix.0.*`, `prefix.1.*` ...). */
export function items(flat: Flat, prefix: string): Flat[] {
  const out: Flat[] = [];
  for (let i = 0; ; i++) {
    const p = `${prefix}.${i}`;
    const keys = Object.keys(flat).filter((k) => k === p || k.startsWith(`${p}.`));
    if (keys.length === 0) break;
    const item: Flat = {};
    for (const k of keys) item[k === p ? "" : k.slice(p.length + 1)] = flat[k];
    out.push(item);
  }
  return out;
}

/** ISO-8601 duration → plain words ("P14D" → "14 days"). Unknown forms are returned as written. */
export function humanDuration(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const m = /^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$/.exec(iso);
  if (!m) return iso;
  const units = ["year", "month", "week", "day", "hour", "minute", "second"];
  const parts = m.slice(1).map((v, i) => (v ? `${Number(v)} ${units[i]}${Number(v) === 1 ? "" : "s"}` : null)).filter(Boolean);
  return parts.length ? `${parts.join(" ")} (${iso})` : iso;
}

export interface PlanUnderstanding {
  entity: string | null;
  shape: string | null;
  consumer: string | null;
  decisionTimeField: string | null;
  features: { path: string; relation: string | null; field: string | null }[];
  label: { path: string | null; field: string | null; within: string | null; maturityLag: string | null } | null;
  featureAxis: string | null;
  labelAxis: string | null;
  asOf: string | null;
  asOfKind: string | null;
  chunking: { textField: string | null; maxChars: string | null } | null;
  identifiers: string | null;
  purpose: string | null;
  lawfulBasis: string | null;
  egress: string | null;
  outputFormat: string | null;
  planHash: string | null;
}

export function understand(flat: Flat): PlanUnderstanding {
  const cols = items(flat, "shape.columns");
  const labelPath = flat["shape.label.path"] ?? null;
  const asOf = flat["temporal.build_time"] ?? flat["temporal.corpus_as_of"] ?? flat["temporal.feature_as_of_fixed"] ?? null;
  return {
    entity: flat["selection.entity"] ?? null,
    shape: flat["shape.kind"] ?? null,
    consumer: flat["shape.consumer.framework"] ?? null,
    decisionTimeField: flat["selection.cohort.decision_time_field"] ?? null,
    features: cols.map((c) => ({ path: c["path"] ?? "", relation: c["source.relation"] ?? null, field: c["source.field"] ?? null })),
    label: labelPath
      ? {
          path: labelPath,
          field: flat["shape.label.source.field"] ?? null,
          within: flat["temporal.label_window.within"] ?? null,
          maturityLag: flat["temporal.label_maturity_lag"] ?? null,
        }
      : null,
    featureAxis: flat["temporal.feature_axis"] ?? null,
    labelAxis: flat["temporal.label_axis"] ?? null,
    asOf,
    asOfKind: flat["temporal.build_time"] ? "build time" : flat["temporal.corpus_as_of"] ? "corpus as-of" : flat["temporal.feature_as_of_fixed"] ? "fixed feature as-of" : null,
    chunking: flat["shape.chunking.max_chars"]
      ? { textField: flat["shape.text_field.field"] ?? null, maxChars: flat["shape.chunking.max_chars"] ?? null }
      : null,
    identifiers: flat["policy.pii.direct_identifiers"] ?? null,
    purpose: flat["policy.purpose"] ?? null,
    lawfulBasis: flat["policy.lawful_basis"] ?? null,
    egress: flat["policy.egress"] ?? null,
    outputFormat: flat["output.format"] ?? null,
    planHash: flat["hashes.plan_hash"] ?? null,
  };
}
