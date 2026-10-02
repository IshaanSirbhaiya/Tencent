"use client";
/**
 * `/data` — Layer 3: take data out, in the shape you need.
 *
 * ONE page, replacing both `/materialise` (the honest "not built" page, now outdated — the
 * materialiser is attached) and `/convert` (C5's converter). Both old routes redirect here
 * (CLAUDE.md rule 9: replace, never shadow).
 *
 *   #agent    the Data Agent — describe a dataset, get a plan, a leakage-checked build, a receipt
 *   #raw      a RAW table by delegation (DSD) — propose a table design, edit, approve, build
 *   #convert  the direct converter — your document or table, another format, deterministic
 */
import * as React from "react";
import { Topbar } from "@/components/topbar";
import { Panel } from "@/components/platform/preview-notice";
import { DataAgent } from "@/components/data/data-agent";
import { Converter } from "@/components/data/converter";
import { DesignBuilder } from "@/components/data/design-builder";

export default function DataPage() {
  // The old routes land here with `#agent` / `#convert`; the sections render synchronously, so
  // scroll once they exist.
  React.useEffect(() => {
    const id = window.location.hash.slice(1);
    if (id) document.getElementById(id)?.scrollIntoView({ block: "start" });
  }, []);

  return (
    <>
      <Topbar title="Data" />
      <div className="min-w-0 flex-1 overflow-auto p-5">
        <div className="mx-auto flex max-w-5xl flex-col gap-5">
          <header>
            <h1 className="text-lg font-semibold">Data in the shape you need</h1>
            <p className="text-xs text-subtle">
              Ask the Data Agent for a dataset built from the brain, with a receipt for every value in it — or convert a file you
              already have.
            </p>
          </header>

          <section id="agent" data-testid="data-agent-section" className="scroll-mt-4">
            <Panel title="Data Agent" hint="POST /materialise · plans, leakage checks, receipts">
              <DataAgent />
            </Panel>
          </section>

          <section id="raw" data-testid="data-raw-section" className="scroll-mt-4">
            <Panel title="Raw dataset by delegation" hint="POST /materialise/designs · design, approve, each source's adapter agent supplies its part">
              <DesignBuilder />
            </Panel>
          </section>

          <section id="convert" data-testid="data-convert-section" className="scroll-mt-4">
            <Panel title="Convert a file" hint="POST /convert · deterministic, no model">
              <Converter />
            </Panel>
          </section>
        </div>
      </div>
    </>
  );
}
