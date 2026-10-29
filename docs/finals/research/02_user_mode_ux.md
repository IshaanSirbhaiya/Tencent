# 02. User mode UX research for Walnut DataBridge "Internal Brain"

Prepared for the Demo Day rebuild (Next.js 14 + Tailwind + Radix, about 3 days). Written in plain English.

**Evidence labels.** `[searched]` means I found it in a web search this session. `[known]` means it comes from well-documented public product behaviour or standards that I am confident about but did not re-open this session (check before quoting). Design-teardown blogs are secondary sources. Items marked "my recommendation" are judgement, not findings.

---

## 0. The one-paragraph answer

Make User mode a calm, single-column chat that looks like ChatGPT or Claude: one input box, four starter prompts, an answer that streams in, small numbered citation chips, and a side panel that opens the source. Put trust in three quiet places: a one-line "What I can see" scope pill above the input, a "Checked 2 min ago" freshness line under each answer, and an honest "I can't find that in what you can open" refusal. Hide everything else (agents, retrieval, audit log) behind a Developer toggle. Teach a first-time judge with a 45-second guided "try it" that asks one real question as a persona, then switches persona so the answer visibly changes. That persona switch is the product's whole argument in one gesture.

---

## 1. Consumer AI chat patterns

### 1.1 Pattern by product

| Product | Empty state and starters | Citations and sources | Reasoning disclosure | Artifacts / side panel | What to steal |
|---|---|---|---|---|---|
| **ChatGPT** | Centered greeting and input; a few suggested prompt cards [known] | Inline source links and a "Sources" button opening a side list in search mode [known] | "Thinking" state with an expandable summary for reasoning models [known] | **Projects** group chats and files; **Canvas** is a side editor for drafts and code [known] | Canvas-style right panel for drafts; "projects" becomes team Spaces |
| **Claude** | Plain greeting, input, quiet starter chips [known] | Citations with web search or documents [known] | Collapsible "thinking" block [known] | **Artifacts** open in a side panel, versioned, copy and download [known] | Artifact panel for tables and customer-reply scripts |
| **Perplexity** | Search box first; trending prompts [known] | Answer first, numbered inline citations, domain chips, expandable source list, then related questions [searched: [teardown](https://www.925studios.co/blog/perplexity-design-breakdown), [UX Design Institute](https://www.uxdesigninstitute.com/blog/?p=10492)] | Visible progress steps while searching [known] | Pages and Spaces [known] | Source chips at the end of claims; cap visible follow-ups near three [searched] |
| **Microsoft 365 Copilot Chat** | Suggested prompts tied to work content [known] | Inline citations next to the claim; hover opens the source in a side pane; "Sources" button lists all [searched: [Microsoft support](https://support.microsoft.com/en-au/topic/control-and-review-sources-of-microsoft-365-copilot-chat-responses-fe762067-15b0-4cb1-b8cf-08cc07b07c5d)] | Search queries shown in the citation section [searched, 2024 article, may be dated: [Neowin](https://www.neowin.net/news/microsoft-will-add-web-search-query-citations-for-copilot-and-365-copilot-in-october/)] | Pages, Word/Excel hand-off [known] | Hover preview plus side pane; link to a Transparency Note |
| **Glean** | Search-bar first with an assistant; prompts by team [known] | LLM answer with citations back to source docs; results filtered at query time by permissions copied from the source system [searched: [comparison](https://vantaige.io/blog/glean-vs-hebbia-vs-perplexity-enterprise-2026), [Glean](https://www.glean.com/brand-enterprise-ai-demo)] | Shows which apps were searched [known] | Saved prompts and agents [known] | Permission-aware is Glean's core claim; ours must be *visible*, not only stated. "Permission-aware" is not a standard feature, so show how it works [searched] |
| **Notion AI** | Prompt menu on an empty page [known] | Q&A answers list the pages used [known] | Light | Writes into the page itself, so the page is the artifact [known] | Drafts open as an editable doc where the work lives |
| **Gemini in Workspace** | Side panel in Docs, Gmail, Drive with starter chips [known] | Drive and Gmail files linked under answers [known] | Light | Side panel, "insert into doc" [known] | Side panel plus one-click "Use this draft" |

### 1.2 Patterns for each moment

**Empty state and starter prompts.** Leaders show a greeting, one input and 3 to 6 starters. Starters work when specific to the user's world ("Summarise the refund policy for failed card payments" beats "Ask me anything"). My recommendation: four *persona-aware* starters (an Ops agent sees refund and chargeback prompts; a Finance lead sees reconciliation prompts), each with a tiny row of source icons (Confluence, Jira, Slack, Drive) so the empty state already says where answers come from.

**Streaming.** Stream text, but keep layout stable: reserve the citation row, never jump the input box. Show a short status line before the first token ("Searching Confluence, Jira, Slack, Drive...") then replace it with text. Offer a Stop button. Respect reduced motion by showing the finished answer without the typewriter effect (section 4).

**Citations and source previews.**
- Numbered chips `[1]` inline at the end of the claim, plus a source row under the answer (Perplexity, Copilot) [searched].
- Hover shows a small card (title, system icon, last updated, owner); click opens the right panel with the passage highlighted (Copilot pattern) [searched].
- A "wrong source" feedback option, separate from "inaccurate", so citation failures get their own route [searched, teardown suggestion].
- Show the *system* with a logo, not only a domain; teardowns warn abbreviated domains confuse some non-US users [searched]. Internal tools have an advantage: "Confluence / Jira / Slack / Drive" is instantly understood.

**"Show your work".** Progressive disclosure. Default: one line, "Used 3 sources from 2 systems". Click expands to a plain list ("Searched Confluence: 4 pages you can open. Skipped: 2 pages you cannot open"). Show *actions and evidence*, not raw reasoning. The full trace with tool calls belongs in Developer mode only.

**Refusals and "I can't find that".** Three cases, three messages (my recommendation, informed by Google's People + AI guidance on errors [known: [PAIR Guidebook](https://pair.withgoogle.com/guidebook/)]):

| Case | Message pattern | Next action offered |
|---|---|---|
| Nothing relevant exists in what you can open | "I could not find this in the documents you can open. I checked Confluence, Jira, Slack and Drive." | Rephrase chips; "Ask the owner" |
| Relevant content exists but you lack access | "This may be in a space you do not have access to, so I can't show or summarise it." (leak no titles or counts if policy forbids) | "Request access" |
| Out of scope or risky | "I help with company knowledge. For legal or regulated advice, please ask Compliance." | Link to the right team |

Never fill the gap with a guess. The refusal is a feature to demo.

**Follow-up chips.** Three maximum, specific to the answer ("Make this a customer reply", "Show as a table", "What changed since March?"). Long chip lists push the input down [searched].

**Artifacts.** Things (a table, a draft email, a customer-reply script) open in a right panel. Each carries its own sources and a version label. Buttons: Copy, Download, Edit, "Make it shorter". Claude Artifacts and ChatGPT Canvas set the expectation [known].

---

## 2. Trust UX for regulated finance

Frames relevant to a Singapore fintech: MAS FEAT (fairness, ethics, accountability, transparency) and the Veritas assessment methodologies [searched: [Allen & Gledhill](https://www.allenandgledhill.com/sg/publication/articles/20233/mas-led-industry-consortium-publishes-assessment-methodologies-for-responsible-use-of-ai-by-financial-institutions)], PDPC's Model AI Governance Framework [known: [PDPC](https://www.pdpc.gov.sg/)], and Microsoft's HAX guidelines [known: [HAX Toolkit](https://www.microsoft.com/en-us/haxtoolkit/)]. FEAT is principles-based and prescribes no UI wording [searched], so the patterns below are good practice, not compliance claims.

| Trust need | Quiet pattern | Where it lives |
|---|---|---|
| "Only from what you can see" | **Scope pill** above the input: lock icon plus "Searching what Priya (Support) can open: Confluence, Jira, Slack, Drive". Click opens a popover with connected systems and a "why some things are hidden" line. Answer footer: "Built only from sources you can open." | Composer, every answer |
| Data freshness | Per source: "Updated 2 hours ago" in the hover card. Per answer: "Checked just now". If a source is stale (for example older than its sync promise), show an amber "Older source" tag on that chip only | Citation card, answer footer |
| Confidence | No fake percentages. A 3-level plain label tied to evidence: "Well supported (3 sources agree)", "Partly supported (1 source)", "Not enough evidence". Text badge, not colour alone | Answer header |
| Conflicts | "Confluence (Mar) says 5 days; Slack (Oct) says 3 days. The newer is Slack." Show both with dates | Inline callout |
| Privacy notice | One line under the input: "Answers use only company content you already have access to. Activity is logged for audit. Learn more." "Learn more" opens a 5-bullet panel. Full notice once at first sign-in | Composer footer, Settings |
| Auditability | "View my activity" link in History; the full audit trail is Developer mode | History |
| Human in the loop | Drafts say "Draft for your review"; never auto-sent | Artifact header |
| Feedback | Thumbs plus "Wrong source" and "Out of date" | Under answer |

**Declutter rules.** One trust element per zone (composer, answer header, answer footer, source card). Small grey text, never banners. Detail on click. The persona switch (section 3) is the strongest trust proof because it shows the permission boundary changing the answer.

---

## 3. Interactive in-product teaching

### 3.1 What the references do

| Reference | Pattern worth borrowing | Evidence |
|---|---|---|
| Linear | Product-first onboarding: sample issues, keyboard hints, few modals | [searched: guide classes Linear and Notion as product-first](https://skills.cat/skills/skenetechnologies/plg-skills/product-onboarding) |
| Notion | Template pages with sample content that teach by editing | same source |
| Stripe | Test mode with test data and a visible banner; docs with runnable snippets | [known: Stripe testing docs](https://docs.stripe.com/testing) |
| Vercel | First success within minutes: import, deploy, preview URL; templates | [known] |
| Arc | A playful guided first run that is a tiny tour of the product | [known] |

I found no first-hand teardowns of each company's tour, so treat these as well-known behaviours, not studied findings [searched].

General guidance from the search: tours of about 3 steps and at most 5; exit any time; make the user act, not read; do not replay completed tours; checklists of 3 to 5 items, persistent, first item pre-ticked [searched: [CRO guide](https://claudeskills.info/skills/openclaudia/openclaudia-skills/onboarding-cro/), [playbook](https://playbooks.com/skills/skenetechnologies/plg-skills/product-onboarding)]. These are practitioner opinions, not controlled studies. A "20 to 30 percent completion lift" figure circulating is unsourced; do not quote it [searched].

### 3.2 Recommended design: "Try it as Priya" (45 seconds, 3 steps)

A judge lands on the persona picker. The tour is a thin overlay, skippable with Esc, and never blocks the real UI.

| Step | Time | What the judge does | What it teaches |
|---|---|---|---|
| 1. Pick a person | 0-10s | Three cards: **Priya (Support agent)**, **Marcus (Finance lead)**, **Guest (new hire)**. Priya preselected. Hint: "Each person sees different documents." | Permissions are the idea |
| 2. Ask once | 10-30s | Composer pre-filled with a starter ("What is our refund policy for failed card payments?"); pulsing hotspot on Send. Answer streams with 3 citations. One coach mark on citation `[2]`: "Click a source to read it" | Cited answers |
| 3. Switch and compare | 30-45s | Coach mark on the persona menu: "Now ask the same thing as Guest." One click re-runs the question; the answer becomes "I can't find that in what you can open" and notes that one source is hidden (no title leaked) | Permission-aware refusal, the wow moment |

Afterwards a small persistent 4-item checklist sits in the sidebar (Ask a question, Open a source, Turn a table into a draft, Switch persona) with items 1 and 3 already ticked; closing it is one click. "Replay tour" lives in Settings. This is a safe sandbox because the data is fixed and resettable ("Reset demo" in the footer).

Build shape (my recommendation): a small tour state machine (Zustand or React context), steps as data (`target`, `copy`, `advance-on-event`), highlights using Radix Popover anchored to `data-tour` attributes. No tour library needed; driver.js is an acceptable fallback [known: [driver.js](https://driverjs.com/)].

---

## 4. Accessibility (WCAG 2.2 AA) for the chat UI

WCAG 2.2 is the current W3C Recommendation [known: [WCAG 2.2](https://www.w3.org/TR/WCAG22/)]. There is no criterion specific to streaming AI text; the nearest is 4.1.3 Status Messages, and practitioners warn that per-token DOM updates make screen readers repeat or drop content [searched: [Accessibility gap in AI interfaces](https://tianpan.co/blog/2026/04/17/ai-accessibility-streaming-screen-readers)]. Chat-widget vendors' "WCAG AA" claims are assertions, not audits [searched].

| Area | Requirement | How to build it |
|---|---|---|
| Keyboard | 2.1.1 operable, 2.1.2 no trap | Radix primitives. `Enter` sends, `Shift+Enter` newline, `Esc` closes panel or tour, `/` focuses input, `Ctrl/Cmd+K` command menu |
| Focus visible and not hidden | 2.4.7, 2.4.11 (new in 2.2) | 2px offset focus ring; sticky composer must not cover a focused chip (`scroll-padding-bottom`) |
| Focus management | Logical order | After send, focus stays in the input. Source panel moves focus in, and back to the chip on close (Radix Dialog or Sheet does this) |
| Streaming live regions | 4.1.3 Status Messages | Message list `role="log"` with `aria-live="polite"`. During streaming announce only "Assistant is answering" once, keep the visual stream `aria-hidden`, then put the **complete** answer into the live region when done (pattern from [Serenity Star docs](https://docs.serenitystar.ai/docs/client-library-reference/javascript-chat-widget-accessibility)). Refusals `role="status"`; errors `role="alert"` |
| Reduced motion | `prefers-reduced-motion`; 2.2.2 | No typewriter, shimmer or pulses; show the finished answer; plus an in-app "Reduce motion" switch [known: [MDN](https://developer.mozilla.org/docs/Web/CSS/@media/prefers-reduced-motion)] |
| Contrast | 1.4.3 text 4.5:1; 1.4.11 UI 3:1 | Check both themes; no grey-on-grey freshness text; chips get a border, not only a fill |
| Colour not alone | 1.4.1 | Confidence and "Older source" badges carry text and icon |
| Resize and reflow | 1.4.4 to 200%; 1.4.10 at 320px | `rem` units; single column; source panel becomes a full-screen sheet on narrow screens |
| Target size | 2.5.8, 24x24 CSS px minimum | Chips and icon buttons at least 32px high |
| Consistent help | 3.2.6 | Privacy and Help links in the same place on every page |
| Accessible sign-in | 3.3.8 | Persona picker is click-to-enter, no password or puzzle |
| Redundant entry | 3.3.7 | A persona switch re-runs the question; the user never retypes it |
| Captions | 1.2.2 | Prefer live UI over video; caption any video |
| Language | 3.1.1, 3.1.2 | `<html lang>` follows the toggle; mark mixed-language citations with `lang` |

**Language toggle (English and Simplified Chinese).** Globe menu top-right, labels written in their own language ("English", "简体中文"), also on the first screen. Use `next-intl` with two message files [known: [next-intl](https://next-intl.dev/)] and `lang="zh-Hans"`. Translate UI strings, starters and persona blurbs; answers follow the question's language, with a note "Sources are shown in their original language". Font stack includes `"PingFang SC","Noto Sans SC"`, line-height about 1.7 for Chinese. Test button text expansion. For Tencent judges, a working toggle on the first screen is a small respectful signal.

---

## 5. Visual polish that wins demos

| Topic | Recommendation | Reference |
|---|---|---|
| Typography | Inter or Geist for UI, 15-16px body, 1.6 line-height, answer column about 68ch; tabular numbers in tables; mono only in Developer mode | [Vercel Geist](https://vercel.com/geist) [known], Linear [known] |
| Spacing | 4/8px scale; generous rhythm between answer blocks; one primary action per area | Linear, Raycast [known] |
| Colour | Neutral greys plus one accent (Walnut brown or calm teal); semantic colours only for status | [shadcn/ui](https://ui.shadcn.com/) tokens [known] |
| Dark and light | CSS variables; follow system by default; override in Settings; check contrast in both | shadcn/ui, Vercel [known] |
| Motion | 120-200ms ease-out for panels; fade plus 4px rise for new messages; no bounce; honour reduced motion | Linear, Arc [known] |
| Micro-interactions | Chip hover card; copy button flips to a check for about 1s; send morphs to Stop; skeleton lines before first token; persona avatar change animates once | Raycast, Arc [known] |
| Command menu | `Cmd+K` (switch persona, language, theme, Developer mode) using [cmdk](https://cmdk.paco.me/) | Raycast, Linear [known] |
| Components | [Radix UI](https://www.radix-ui.com/) for Dialog, Popover, HoverCard, Tabs, Tooltip, Switch, DropdownMenu; shadcn/ui as the copy-in layer; Tailwind tokens | [known] |
| Streaming engine | Vercel [AI SDK](https://ai-sdk.dev/) `useChat`, or a plain SSE stub with scripted answers for reliability | [known] |

Demo safety (my recommendation): script the three demo answers as deterministic fixtures with believable streaming speed and fall back to them if the live backend is slow. Judges reward a smooth flow over a live gamble.

---

## 6. User mode spec for Walnut DataBridge

### 6.1 Page by page

**A. Sign-in / persona picker (`/`)**
- Centered Walnut mark, one sentence ("Ask your company anything. Get answers from what you can open."), language globe, theme toggle.
- Three persona cards with avatar, role and a one-line "can see" list (for example "Support: Help Centre, Jira CS, Slack #support").
- Primary button "Continue as Priya"; small link "Take the 45-second tour".
- Footer: privacy one-liner. Developer mode is not shown here.
- Accessibility: cards form a radio group; arrow keys move; no password.

**B. Home / Ask (`/ask`)**
- Collapsible left sidebar: New chat, History, Spaces (optional), Settings, persona menu at the bottom.
- Centre: greeting ("Good afternoon, Priya"), composer, scope pill, four persona-aware starter cards with system icons.
- Composer footer: privacy line. No attach button (keeps scope simple).
- Top bar: language, theme, Developer mode toggle (off by default).

**C. Answer view (`/ask/[id]`)**
- Question bubble; status line while retrieving; streamed answer with `[n]` chips; confidence badge in header; source row; freshness footer; actions (Copy, Regenerate, thumbs, "Wrong source").
- "How I answered" disclosure: one line collapsed, plain-language list expanded.
- Up to three follow-up chips ("Make this a table", "Draft a customer reply") that open an artifact.
- Artifact panel (right, resizable): table with Copy CSV; customer-reply script with tone selector (Friendly, Formal) and a "Draft for your review" tag; each lists its sources.
- Refusal variants per section 1.2.

**D. Sources panel (right sheet, also `/sources/[id]`)**
- Header: system logo, title, owner, "Updated 2 hours ago", access note ("You can open this").
- Body: excerpt with the cited passage highlighted; "Open in Confluence" (mock link for the demo).
- Tabs: Passage, Details. Next/previous source arrows. `Esc` closes and returns focus.

**E. History (`/history`)**
- Searchable list grouped Today / Last 7 days; each row shows question, persona and number of sources. "View my activity" opens a plain-language log of what was searched (not the developer trace). Delete one or clear all.

**F. Settings and accessibility (`/settings`)**
- Language (English / 简体中文), Theme (System/Light/Dark), Text size (100/115/130%), Reduce motion, High contrast, optional "Read answers aloud" (browser speech synthesis), Privacy (5-bullet notice), Replay tour, Reset demo data, Developer mode toggle.
- Persist in `localStorage` inside try/catch with a safe default.

### 6.2 Prioritised recommendations (28)

P0 = must ship for Demo Day; P1 = should; P2 = if time remains.

| # | Pri | Recommendation | Rubric items served |
|---|---|---|---|
| 1 | P0 | Single-column chat home: greeting, composer, four persona-aware starters | Human-Centered Design, UX |
| 2 | P0 | Persona picker as sign-in (Priya, Marcus, Guest); no password | UX & Accessibility, Demo |
| 3 | P0 | Scope pill above composer: "Searching what Priya can open" with popover | Responsible AI |
| 4 | P0 | Streaming answer with stable layout, status line, Stop button | AI Interaction |
| 5 | P0 | Numbered inline citation chips with system logos | AI Interaction, Responsible AI |
| 6 | P0 | Right-hand sources panel with highlighted passage, freshness, access note | Technical Execution |
| 7 | P0 | Honest refusal states (3 cases) with next actions | Responsible AI, AI Interaction |
| 8 | P0 | Persona switch re-runs the same question and the answer visibly changes | Innovation, Demo |
| 9 | P0 | Developer mode toggle revealing trace, tool calls, audit log; hidden by default | Technical Execution, Demo |
| 10 | P0 | Scripted fixtures for the 3 demo questions, with fallback if backend is slow | Feasibility, Demo |
| 11 | P0 | Full keyboard support, visible focus, Esc closes panels | UX & Accessibility |
| 12 | P0 | `role="log"` live region; announce the full answer once when streaming ends | UX & Accessibility |
| 13 | P0 | English / 简体中文 toggle on first screen and in Settings | UX & Accessibility, Impact |
| 14 | P1 | Three follow-up chips specific to each answer | AI Interaction |
| 15 | P1 | Artifact panel: table (copy CSV) and customer-reply script (tone selector) | Innovation, Impact |
| 16 | P1 | Plain-words confidence label with evidence count (no fake percentages) | Responsible AI |
| 17 | P1 | Conflict callout when sources disagree, with dates | Responsible AI, Innovation |
| 18 | P1 | 45-second guided tour (3 steps) and 4-item checklist | Human-Centered Design, Demo |
| 19 | P1 | "How I answered" disclosure, one line collapsed | Responsible AI, AI Interaction |
| 20 | P1 | Light and dark themes via CSS variables; contrast checked in both | UX & Accessibility, Overall |
| 21 | P1 | `prefers-reduced-motion` plus in-app switch; no typewriter when reduced | UX & Accessibility |
| 22 | P1 | Privacy one-liner under composer and 5-bullet notice panel | Responsible AI |
| 23 | P1 | History page with plain-language "my activity" view | Responsible AI, Feasibility |
| 24 | P2 | `Cmd+K` command menu (persona, language, theme, Developer) | Overall Quality, Innovation |
| 25 | P2 | Text-size setting and 320px reflow check | UX & Accessibility |
| 26 | P2 | "Wrong source" and "Out of date" feedback options | Responsible AI |
| 27 | P2 | Micro-interactions: copy-to-check, send-to-stop morph, hover cards | Overall Quality |
| 28 | P2 | "Reset demo" button and tour replay for judges who try it themselves | Demo, Feasibility |

### 6.3 Three wow moments for the 5-minute pitch

| # | Moment | Approx. time | What the audience sees | Why it scores |
|---|---|---|---|---|
| 1 | **Cited answer in seconds** | 0:45-1:45 | As Priya, ask the refund question. The answer streams, three chips appear, click `[2]` and the Confluence passage opens highlighted with "Updated 2 hours ago" | AI Interaction, Technical Execution, Human-Centered Design |
| 2 | **Same question, different person** | 2:00-3:15 | Switch to Guest in one click. The answer becomes "I can't find that in what you can open", noting that something exists but is hidden. Marcus then sees a richer answer | Responsible AI, Innovation, Impact (the permission boundary is the product) |
| 3 | **Answer becomes work** | 3:30-4:30 | Click "Draft a customer reply"; a script opens in the side panel with sources and "Draft for your review"; copy it. Flip Developer mode on for 5 seconds to show the logged trace, then back off | Demo & Storytelling, Overall Quality, Technical Execution |

Close the last 30 seconds on the privacy line and switching the UI live to 简体中文, which speaks to both Singapore SME buyers and Tencent judges.

### 6.4 Three-day build plan

| Day | Focus |
|---|---|
| 1 | Design tokens, layout shell, persona picker, composer with scope pill, streaming fixtures, answer view with citation chips |
| 2 | Sources panel, refusals, follow-ups, artifact panel, Developer toggle, history, settings, i18n |
| 3 | Tour and checklist, accessibility pass (keyboard, live region, contrast, reduced motion), dark theme, polish, rehearsal on deterministic fixtures |

---

## 7. Sources

Searched this session:
- Perplexity teardown: https://www.925studios.co/blog/perplexity-design-breakdown
- UX Design Institute on Perplexity: https://www.uxdesigninstitute.com/blog/?p=10492
- Copilot Chat sources: https://support.microsoft.com/en-au/topic/control-and-review-sources-of-microsoft-365-copilot-chat-responses-fe762067-15b0-4cb1-b8cf-08cc07b07c5d
- Copilot search-query citations (2024): https://www.neowin.net/news/microsoft-will-add-web-search-query-citations-for-copilot-and-365-copilot-in-october/
- Copilot transparency note: https://support.microsoft.com/en-US/microsoft-copilot/transparency-note-for-microsoft-copilot
- Glean: https://www.glean.com/brand-enterprise-ai-demo ; comparison https://vantaige.io/blog/glean-vs-hebbia-vs-perplexity-enterprise-2026
- Chat accessibility and streaming: https://tianpan.co/blog/2026/04/17/ai-accessibility-streaming-screen-readers ; https://docs.serenitystar.ai/docs/client-library-reference/javascript-chat-widget-accessibility
- Onboarding guidance: https://skills.cat/skills/skenetechnologies/plg-skills/product-onboarding ; https://claudeskills.info/skills/openclaudia/openclaudia-skills/onboarding-cro/
- MAS FEAT and Veritas: https://www.allenandgledhill.com/sg/publication/articles/20233/mas-led-industry-consortium-publishes-assessment-methodologies-for-responsible-use-of-ai-by-financial-institutions

Known references (not re-opened this session; verify before quoting): WCAG 2.2 https://www.w3.org/TR/WCAG22/ ; Radix https://www.radix-ui.com/ ; shadcn/ui https://ui.shadcn.com/ ; Vercel Geist https://vercel.com/geist ; cmdk https://cmdk.paco.me/ ; next-intl https://next-intl.dev/ ; Microsoft HAX https://www.microsoft.com/en-us/haxtoolkit/ ; Google PAIR https://pair.withgoogle.com/guidebook/ ; PDPC https://www.pdpc.gov.sg/ ; AI SDK https://ai-sdk.dev/.

**Gaps.** I could not open Microsoft's full M365 Copilot transparency note (behind sign-in), found no first-hand Linear/Stripe/Vercel/Arc onboarding teardowns, and found no PDPC guidance specific to AI notices. Treat Singapore regulatory points as a prompt for the team's compliance review, not legal advice. ChatGPT, Claude, Notion AI and Gemini details are from general product knowledge, not fetched pages.
