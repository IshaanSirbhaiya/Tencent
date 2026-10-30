# Deliverables from an answer: research

Research date: 30 Oct 2026. Companion to `PLAN.md` in this folder. Evidence labels:

- **[S]** a source page we fetched and read;
- **[S2]** a search-result summary or a secondary write-up, not read in full: treat as a lead, not proof;
- **[R]** a fact in this repo (file named);
- **[I]** our judgement.

---

## 1. How other products present generated deliverables

| Product | How it is presented | Sources and proof | What we take |
|---|---|---|---|
| **Claude Artifacts** | An artifact "opens in its own window beside your conversation"; controls at the top copy or download it; template artifacts export to Word, PDF, PowerPoint and others [S: Claude Help Center] | Not a provenance feature | The panel beside the answer, with Copy and Download at the top |
| **ChatGPT Canvas** | A panel beside the chat for writing and code; the model suggests targeted edits; opens automatically or by hand; launched as a beta in Oct 2024 and opened to all users in Dec 2024 [S2: VentureBeat, Tom's Guide, TechRadar; the OpenAI page returned 403] | Not a provenance feature | Regenerate inside the panel; we do not copy free editing of model text (an edited deliverable would lose its checks) |
| **Microsoft 365 Copilot Pages** | "Edit in Pages" turns a chat response into a persistent page opened side by side with the chat, for group editing [S2: Microsoft support pages via search] | The 2024 launch deck says Copilot gives citations "along the way so you can check its work before adding it to the Page" [S2: Microsoft Wave 2 PDF]. We found nothing saying citations survive into the page | Gap we fill: our citations stay attached to every sentence, cell and data point, including in exports |
| **Glean Canvas** | Opens alongside Chat as "a dedicated editor"; version history; exports to Google Docs, Word, Markdown; slides use a company template [S: docs.glean.com Canvas guide] | "A **Sources** section at the bottom of the document that shows exactly where the content came from", each citation linking to the original [S] | A sources list at the bottom of every deliverable, and slides from a fixed template |
| **Perplexity Pages** | Turns a thread into a structured article with sections; "in each section, you can view the sources for that content" [S2: Tom's Guide quoting a spokesperson]; a Forbes complaint about attribution [S2] | Per-section sources | Per-section is too coarse for numbers; we cite per sentence, cell and point |
| **Notion AI** | Writes into the page itself; Q&A answers cite the pages used [S2: Notion help pages via search] | Third-party reports of citations landing on the wrong block [S2: WiseChecker, unconfirmed] | A citation must open the exact passage, highlighted: our source sheet does this |
| **Databricks AI/BI Genie** | Responds with "the generated query and results table, if possible", and often an auto-generated chart [S: Databricks Genie concepts] | Answers built from author-verified "trusted assets" are marked Trusted: "the answer comes from this verified logic" [S] | A visible marker for checked output. Ours is narrower and stated plainly: "N of N numbers found in your sources" |
| **Hebbia / Rogo** (finance research tools) | Grid and Excel outputs | Comparison pages say Hebbia gives sentence-level citations and cell-level citations in Excel outputs, Rogo response-level [S2: vendor and competitor pages; no independent check] | The market for finance AI already competes on citation granularity; value-level is the finest [I] |

Pattern [I]: every leading product puts the deliverable in a panel beside the conversation with copy and
export, and the enterprise ones (Glean, Copilot) attach sources. None we found documents a check that each
number in a generated chart or table is actually in its cited source. That is the gap the plan fills.

## 2. Grounded chart generation

- **Charts that look right can carry wrong data.** DEEPCHART (arXiv 2608.26757, submitted 27 Aug 2026)
  reports that "visually plausible charts often conceal data-level hallucinations", and that faithful
  chart generation needs reliable evidence extraction before rendering, not only bigger context
  windows [S: arXiv abstract]. It scores source-data fidelity separately from the rendered chart [S2].
- **Checks by hand are still the ground truth in benchmarks.** ChartFI's authors checked every claim
  against the chart and the source text [S2: arXiv 2605.23694, search summary].
- **Ask for the table behind the chart and verify it.** A practitioner guide recommends getting the
  underlying table with the chart and checking labels and scales against it [S2: Digital Applied,
  Oct 2026]. Semiotic's generative-UI layer validates a chart spec and checks it fits the supplied data
  [S2].
- **A second model is not a verifier.** A presenter guide warns that a second model checking the first
  mostly agrees with it [S2: SlideModel]; a survey notes a chart reader that hallucinates cannot certify a
  chart writer [S2: arXiv 2608.05179].
- **Our own prior art.** Walnut already reads numbers by value, not spelling (FIN-08):
  `33,600,000.00` equals `33600000`, `HKD33,600,000.00` is a number with a code, `CM-2026-0029` is one
  identifier [R `databridge/packages/core/databridge_core/value_forms.py`]. The answer wall refuses any
  value not bound to a tagged passage line [R `databridge/apps/gateway/databridge_gateway/llm.py`
  `ground_narration`].

Conclusion for the plan [I]: keep the model's numbers as data (`value` plus the exact `raw` text and a
`tag`), check them deterministically against the cited passage with Walnut's normaliser, require the label
and the number on the same source line, drop what fails, and say how many were dropped. No model checks a
model.

## 3. JSON schema for model chart specs: Vega-Lite or a small custom spec

- A typical Vega-Lite spec for a moderately complex chart runs to 100+ lines; Microsoft Research's Flint
  has the model write a short semantic spec (about 10 lines) and compiles it, and reports that the
  advantage grows as model capability decreases [S2: Flint paper arXiv 2607.20775 and a Better Stack
  write-up].
- In DracoGPT, GPT-3.5-Turbo produced valid Vega-Lite only 19.1% of the time [S2: arXiv 2408.06845].
  Older models, but the direction is clear.
- DynaVis constrained output to one Vega-Lite schema version and used few-shot examples; Chat2Plot offers
  a simple internal format as well as Vega-Lite [S2].
- Our harness already implements the structured-output ladder: schema in the prompt, JSON mode for
  `openai/*` models (`response_format: json_object`), Pydantic validation, one corrective retry with the
  validation error [R `databridge/packages/harness/databridge_harness/client.py` `complete_structured`].

Decision [I]: a small custom spec per kind (Pydantic, `extra="forbid"`, capped lists and strings). Reasons:
fewer ways for the model to be wrong; each data point needs its own citation, which Vega-Lite has no slot
for; our renderer is Recharts, not Vega.

**Open question [V]:** whether TokenHub honours `response_format: json_object` for `hy3`. Third-party
pages say TokenHub is OpenAI-compatible with tool calling and `reasoning_effort`, and disagree on
capabilities; none mention JSON mode [S2: OpenClaw docs, Mastra provider page]. The plan has a text-mode
fallback (`IB_JSON_MODE=0`).

## 4. python-pptx

- The documented chart pattern: `CategoryChartData`, set `categories`, `add_series`, then
  `slide.shapes.add_chart(XL_CHART_TYPE.…, x, y, cx, cy, chart_data)`; most 2D chart types are supported,
  and the result is a native chart the user can edit in PowerPoint [S2: python-pptx docs "Working with
  charts" via search].
- Local spike on the demo laptop, 30 Oct [R]: `python-pptx 1.0.2` from `databridge/.venv`, a 16:9 deck with
  one clustered-column chart and speaker notes, saved to `BytesIO`: **39,189 bytes, 1.4 s including the
  cold import**. `Presentation.save()` accepts a file-like object.
- `python-pptx` is installed in `.venv` but is not in `databridge/requirements-lock.txt`; `lxml 6.1.3` and
  `Pillow 12.3.0` are pinned there [R]. The plan pins it.

## 5. Accessible charts

- W3C WAI's complex-images tutorial: a short text alternative plus a long description; for a chart that
  needs a data table, use `<figure role="group">` with a `<figcaption>` holding the table. Not
  `aria-describedby`, because the referenced element is "treated as one continuous paragraph of text", so
  headings and table structure are lost [S: w3.org/WAI/tutorials/images/complex].
- University accessibility guides repeat the same pattern (name the chart type and data in the short
  description; give the data as a table; SC 1.1.1) [S2].
- Recharts: the `accessibilityLayer` prop adds ARIA attributes and arrow-key navigation between data
  points [S2: Recharts GitHub discussion #4662; accessibility guides]. It exists in the installed version
  (`accessibilityLayer?: boolean` in `recharts@2.12.7` types) [R]. The same discussion reports VoiceOver
  QuickNav can intercept the arrow keys [S2], so the data table stays the primary accessible form.

## 6. Export safety

- **CSV injection.** Spreadsheet apps treat a cell starting with `=` as a formula; OWASP lists `=`, `+`, `-`,
  `@` (and full-width variants in some locales) and recommends neutralising them; it warns that Excel may
  strip escapes on save and re-open, so no escape is bulletproof [S2: OWASP CSV Injection page via
  search]. Other references add tab and carriage return [S2: CWE-1236 write-up]. The plan prefixes `'`,
  exempting plain numbers, and puts only checked, cited content in the file.
- **File names.** RFC 6266 governs `Content-Disposition`; the OWASP ASVS discussion found most problems were
  unescaped quotes or CRLF in `filename`, and recommends an ASCII `filename` plus a percent-encoded
  `filename*` [S2: httpwg.org RFC 6266, OWASP ASVS issue #1390]. The plan avoids the problem by building
  names from fixed parts only (kind, request id prefix, date) and asserting a regex.

## 7. What this means for the build (summary)

1. Panel beside the answer, copy and download at the top, sources at the bottom (Claude, Glean).
2. Value-level citations with a deterministic check, and a visible, honest count (gap in §1; evidence in
   §2).
3. Small custom JSON spec through the existing harness ladder (§3), with a text-mode fallback for TokenHub.
4. Native, editable pptx charts from checked values (§4).
5. Chart inside `<figure>` with a real data table in `<figcaption>`; `accessibilityLayer` on (§5).
6. CSV formula guard; file names from fixed parts (§6).

## Sources

Fetched and read [S]:
- Claude Help Center, "What are artifacts and how do I use them?": https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them
- Glean docs, Canvas: https://docs.glean.com/user-guide/assistant/glean-chat/canvas
- Databricks, Genie concepts: https://docs.databricks.com/aws/en/genie/concepts
- DEEPCHART, arXiv 2608.26757: https://arxiv.org/abs/2608.26757
- W3C WAI, Complex Images tutorial: https://www.w3.org/WAI/tutorials/images/complex/

Search summaries and secondary pages [S2]:
- VentureBeat, "OpenAI launches ChatGPT Canvas, challenging Claude Artifacts": https://venturebeat.com/ai/openai-launches-chatgpt-canvas-challenging-claude-artifacts
- Tom's Guide, Canvas official launch: https://www.tomsguide.com/ai/chatgpt/openai-announces-official-launch-of-canvas-for-writing-and-coding-heres-whats-new
- Microsoft support, "How Microsoft 365 Copilot Pages works": https://support.microsoft.com/en-us/Microsoft-365-Copilot/how-microsoft-365-copilot-pages-works
- Microsoft 365 Copilot Wave 2 (PDF): https://news.microsoft.com/wp-content/uploads/prod/2024/09/Microsoft-365-Copilot-Wave-2.pdf
- Tom's Guide, Perplexity Pages: https://www.tomsguide.com/ai/i-tried-perplexitys-new-pages-feature-wikipedia-should-be-worried
- Gigazine, Perplexity Pages and Forbes: https://gigazine.net/gsc_news/en/20240610-perplexity-ai-page-news-scraped-summary
- Notion help, "Everything you can do with Notion AI": https://www.notion.com/help/guides/everything-you-can-do-with-notion-ai
- WiseChecker, Notion citation anchors: https://wisechecker.com/?p=27101
- Hebbia vs Rogo (vendor page): https://www.hebbia.com/resources/hebbia-vs-rogo
- ChartFI, arXiv 2605.23694: https://arxiv.org/pdf/2605.23694
- Digital Applied, AI-generated chart data checks: https://www.digitalapplied.com/blog/ai-generated-chart-data-checks
- Semiotic, generative UI: https://semiotic.nteract.io/interoperability/generative-ui
- SlideModel, AI hallucination checker for presentations: https://slidemodel.com/ai-hallucination-checker-for-presentations/
- Survey, verification gap in AI research agents, arXiv 2608.05179: https://arxiv.org/pdf/2608.05179
- Flint, arXiv 2607.20775: https://arxiv.org/pdf/2607.20775 and Better Stack write-up: https://betterstack.com/community/guides/ai/flint-microsoft.md
- DracoGPT, arXiv 2408.06845: https://arxiv.org/pdf/2408.06845
- DynaVis, arXiv 2401.10880: https://arxiv.org/pdf/2401.10880
- Chat2Plot: https://pypi.org/project/chat2plot/0.0.9/
- OpenClaw, Tencent TokenHub provider: https://docs.openclaw.ai/it/providers/tencent.md
- Mastra, Tencent TokenHub provider: https://mastra.ai/models/providers/tencent-tokenhub
- python-pptx, Working with charts: https://python-pptx.readthedocs.io/en/latest/user/charts.html
- Recharts discussion #4662 (keyboard navigation, VoiceOver): https://github.com/recharts/recharts/discussions/4662
- OWASP, CSV Injection: https://owasp.org/www-community/attacks/CSV_Injection
- CWE-1236 write-up: https://shoulder.dev/threats/cwe/CWE-1236
- RFC 6266: https://httpwg.org/specs/rfc6266.html
- OWASP ASVS issue #1390 (Content-Disposition): https://github.com/OWASP/ASVS/issues/1390
