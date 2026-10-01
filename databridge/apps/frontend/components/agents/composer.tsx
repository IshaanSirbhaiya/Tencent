"use client";
/**
 * The English box → compile → "here's what I understood" → save / activate loop. Used by both
 * Builder tabs; the compiler decides the kind, not the tab (a sentence about keeping sources in the
 * graph compiles to an adapter agent wherever it is typed, and the card says so).
 *
 * Answers accumulate: each answer re-sends the ORIGINAL words plus every answer so far, and the
 * gateway returns a fresh draft (and a fresh `draft_id`). Editing the words starts over.
 *
 * A draft is saved only for the words it was compiled from: editing the words marks it stale and
 * disables Save/Activate, a compile in flight disables them too, a second submit/answer is ignored
 * while one is pending, and a response that is not the latest request's is dropped (generation
 * token) so a slow older compile can never replace a newer draft (R-019 round 3, B5).
 */
import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { agentsApi, type DraftResult, type SaveResult } from "@/lib/agents-api";
import { DraftCard } from "./draft-card";
import { EnglishBox, ErrorLine } from "./shared";

export function Composer({
  testId,
  examples,
  placeholder,
  onSaved,
  expectKind,
}: {
  testId: string;
  examples: { label: string; text: string }[];
  placeholder: string;
  onSaved: (r: SaveResult) => void;
  expectKind: "agent" | "adapter_agent";
}) {
  const qc = useQueryClient();
  const [text, setText] = React.useState("");
  const [compiledText, setCompiledText] = React.useState("");
  const [answers, setAnswers] = React.useState<Record<string, string>>({});
  const [draft, setDraft] = React.useState<DraftResult | null>(null);
  const [saved, setSaved] = React.useState<SaveResult | null>(null);
  const generation = React.useRef(0);

  const compile = useMutation({
    mutationFn: async (v: { text: string; answers: Record<string, string>; gen: number }) => ({
      gen: v.gen,
      draft: await agentsApi.compile(v.text, v.answers),
    }),
    onSuccess: (r) => {
      if (r.gen !== generation.current) return; // a superseded request's answer
      setDraft(r.draft);
      setSaved(null);
    },
  });

  const save = useMutation({
    mutationFn: (activate: boolean) => agentsApi.save(draft!.draft_id, activate),
    onSuccess: (r) => {
      setSaved(r);
      setDraft(null);
      setAnswers({});
      qc.invalidateQueries({ queryKey: ["agents"] });
      qc.invalidateQueries({ queryKey: ["adapter-agents"] });
      qc.invalidateQueries({ queryKey: ["automation-status"] });
      onSaved(r);
    },
  });

  function submit() {
    if (!text.trim() || compile.isPending || save.isPending) return;
    const fresh = text !== compiledText;
    const next = fresh ? {} : answers;
    if (fresh) setDraft(null); // the old draft belongs to other words
    setCompiledText(text);
    setAnswers(next);
    generation.current += 1;
    compile.mutate({ text, answers: next, gen: generation.current });
  }

  function answer(id: string, value: string) {
    if (compile.isPending || save.isPending) return;
    const next = { ...answers, [id]: value };
    setAnswers(next);
    generation.current += 1;
    compile.mutate({ text: compiledText, answers: next, gen: generation.current });
  }

  return (
    <div data-testid={testId} className="flex flex-col gap-3">
      <EnglishBox
        testId={`${testId}-box`}
        value={text}
        onChange={setText}
        onSubmit={submit}
        examples={examples}
        placeholder={placeholder}
        busy={compile.isPending}
      />
      <ErrorLine error={compile.error} testId={`${testId}-compile-error`} />
      {draft && draft.kind !== expectKind && (
        <p data-testid={`${testId}-kind-note`} className="text-2xs text-band-spot">
          This reads as {draft.kind === "adapter_agent" ? "an adapter agent (it manages sources)" : "an agent (it does work)"};
          saving it will put it under {draft.kind === "adapter_agent" ? "Adapter agents" : "Agents"}.
        </p>
      )}
      {draft && (
        <DraftCard
          key={draft.draft_id}
          draft={draft}
          answers={answers}
          onAnswer={answer}
          onSave={(activate) => save.mutate(activate)}
          busy={compile.isPending}
          saving={save.isPending}
          compiledText={compiledText}
          text={text}
        />
      )}
      <ErrorLine error={save.error} testId={`${testId}-save-error`} />
      {saved && (
        <p data-testid={`${testId}-saved`} className="text-xs text-band-auto">
          Saved {saved.kind === "adapter_agent" ? "adapter agent" : "agent"}{" "}
          <span className="font-mono">{saved.id}</span> as <span className="font-mono">{saved.status}</span>{" "}
          <span className="text-subtle">(by {saved.saved_by})</span>
        </p>
      )}
    </div>
  );
}
