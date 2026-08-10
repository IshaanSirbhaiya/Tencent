"use client";
import * as React from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Topbar } from "@/components/topbar";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { DataGrid } from "@/components/data-grid";
import { Grounded } from "@/components/grounded";
import { RefusalCard } from "@/components/refusal-card";
import { api } from "@/lib/api";
import type { AskResponse } from "@/lib/types";
import { Search, ShieldAlert, Sparkles } from "lucide-react";

export default function AskPage() {
  const [suggested, setSuggested] = React.useState<{ questions: string[]; injection: string } | null>(null);
  const [question, setQuestion] = React.useState("");
  const [answer, setAnswer] = React.useState<AskResponse | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    api.suggested().then((s) => {
      setSuggested(s);
      setQuestion(s.questions[0] ?? "");
    });
  }, []);

  async function ask(q: string) {
    setQuestion(q);
    setBusy(true);
    setError(null);
    try {
      setAnswer(await api.ask(q));
    } catch (e) {
      // a non-refusal error (refusals come back as a body) — surface it, don't drop it (review L2).
      setAnswer(null);
      setError(e instanceof Error ? e.message : "ask failed");
    } finally {
      setBusy(false);
    }
  }

  const cols: ColumnDef<Record<string, unknown>, unknown>[] = answer && answer.result.length
    ? Object.keys(answer.result[0]).map((k) => ({
        header: k,
        accessorKey: k,
        cell: ({ getValue }) => <span className="tnum font-mono text-2xs">{String(getValue() ?? "")}</span>,
      }))
    : [];

  return (
    <>
      <Topbar title="Question Answerer" />
      <div className="min-w-0 flex-1 overflow-auto p-5">
        <div className="mx-auto max-w-3xl">
          <div className="flex gap-2">
            <Input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && ask(question)}
              placeholder="Ask a question about your data…"
            />
            <Button onClick={() => ask(question)} disabled={busy || !question.trim()}>
              <Search className="h-3.5 w-3.5" /> Ask
            </Button>
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <span className="text-2xs text-subtle">suggested:</span>
            {suggested?.questions.map((q) => (
              <button
                key={q}
                onClick={() => ask(q)}
                className="rounded border border-border bg-muted px-2 py-1 text-2xs text-subtle hover:text-fg"
              >
                {q}
              </button>
            ))}
            {suggested && (
              <button
                onClick={() => ask(suggested.injection)}
                className="ml-auto inline-flex items-center gap-1 rounded border border-band-review/40 bg-band-review/10 px-2 py-1 text-2xs text-band-review"
              >
                <ShieldAlert className="h-3 w-3" /> Try injection attack
              </button>
            )}
          </div>

          {error && (
            <div className="mt-4 rounded border border-band-review/40 bg-band-review/10 p-2 text-2xs text-band-review">
              {error}
            </div>
          )}

          {answer && (
            <div className="mt-5 space-y-4">
              {answer.refusal ? (
                <RefusalCard refusal={answer.refusal} />
              ) : (
                <>
                  <div>
                    <div className="mb-1 flex items-center gap-2">
                      <span className="text-2xs uppercase tracking-wide text-subtle">generated SQL</span>
                      {answer.provenance && (
                        <Grounded pointer={answer.provenance}>
                          <Badge className="border-accent/40 text-accent">
                            <Sparkles className="h-3 w-3" /> SQL-as-provenance
                          </Badge>
                        </Grounded>
                      )}
                      {answer.replayed && <Badge>replayed · deterministic · no model</Badge>}
                      {answer.linked_tables.map((t) => (
                        <Badge key={t} className="font-mono">{t}</Badge>
                      ))}
                    </div>
                    <pre className="overflow-x-auto rounded border border-border bg-bg p-3 font-mono text-2xs text-fg">
                      {answer.sql}
                    </pre>
                  </div>
                  <div>
                    <div className="mb-1 text-2xs uppercase tracking-wide text-subtle">
                      result · {answer.result.length} row{answer.result.length === 1 ? "" : "s"}
                    </div>
                    <DataGrid data={answer.result} columns={cols} maxHeight={320} />
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
