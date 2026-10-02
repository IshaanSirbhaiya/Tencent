"""`compile_english` — plain English in, a draft agent / adapter agent out, or questions.

    text ─► deterministic pre-pass (rules, no model)          prepass.py
         ─► model proposal (harness structured output)        optional; fills title, steps, gaps
         ─► merge: rules win wherever they found something     P16
         ─► catalogue validation: unknown connection / tool / kind / folder -> QUESTION
         ─► spec built and validated by databridge_workflow_spec.load_spec
         ─► (agents) declaration generated + compiled by databridge_agentdef.compile_agent
         ─► DraftResult: spec, declaration, "here is what I understood", open questions

Nothing here activates anything. A draft with open questions has NO spec (there is nothing
coherent to activate); a draft whose declaration does not compile has a spec but is not
`activatable`. `save_draft(..., activate=True)` refuses both.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field

from databridge_workflow_spec import (
    AdapterAgentSpec,
    SpecError,
    WorkflowSpec,
    dump_spec,
    load_spec,
    parse_tool,
)

from databridge_agent_runtime import parse_declaration_text
from databridge_agent_runtime.mapping import pair_problems

from . import paths
from .catalogue import (
    CatalogueLike,
    adapter_agent_of,
    adapter_agents_of,
    connection_of,
    writers_for,
)
from .cron import CronError, CronSchedule
from .declaration import (
    GENERATOR,
    RUNTIME_CITATION,
    Compilation,
    compile_declaration,
    declaration_from_workflow,
    dump_declaration,
)
from .prepass import PrePass, Question, prepass, slug, subject_mentions

#: Routed task class for the model pass. `agentic_orchestration` exists in config/models.yaml
#: today; a dedicated `automation_compile` class is a config change (reported to the coordinator).
TASK_CLASS = "agentic_orchestration"

_MODES = ("human_required", "human_notified", "autonomous")


# --------------------------------------------------------------------------------------------
# the model's structured output — tolerant on input, validated by rules afterwards
# --------------------------------------------------------------------------------------------
class ProposedAction(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    description: str = ""
    connection: Optional[str] = None
    tool: Optional[str] = Field(None, description="source.write:<connection>:<tool>")
    automation_mode: Optional[Literal["human_required", "human_notified", "autonomous"]] = None
    reversal: Optional[str] = None


class ModelProposal(BaseModel):
    """What the model is asked for. Every reference in it is checked against the catalogue."""

    model_config = ConfigDict(extra="ignore")
    kind: Literal["agent", "adapter_agent"] = "agent"
    title: str = ""
    agent_id: str = ""
    instructions: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    actions: list[ProposedAction] = Field(default_factory=list)
    trigger_type: Literal["manual", "schedule", "event"] = "manual"
    cron: Optional[str] = None
    timezone: Optional[str] = None
    event_source: Optional[str] = None
    event_types: list[str] = Field(default_factory=list)
    uri_glob: Optional[str] = None
    kinds_referenced: list[str] = Field(default_factory=list)
    freshness_hours: Optional[int] = None
    open_questions: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------------------------
# the result
# --------------------------------------------------------------------------------------------
@dataclass
class DraftResult:
    kind: str
    created_from: str
    understood: str
    open_questions: list[Question] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    spec: Optional[Union[WorkflowSpec, AdapterAgentSpec]] = None
    spec_yaml: Optional[str] = None
    declaration: Optional[dict] = None
    declaration_yaml: Optional[str] = None
    declaration_source: Optional[str] = None
    compilation: Optional[Compilation] = None
    model_used: bool = False
    notes: list[str] = field(default_factory=list)
    derivations: list[dict] = field(default_factory=list)
    """§5.8: one entry per emitted field — ``{field, value, by, span, evidence, model,
    task_class, prompt_version}``. ``by`` is ``rule`` (``span`` = the user's words it came from),
    ``model`` (with the model id and prompt version), ``answer`` / ``model+answer`` (the user
    answered a question), ``config`` (deployment config), ``catalogue`` or ``default``."""

    @property
    def activatable(self) -> bool:
        if self.spec is None or self.open_questions:
            return False
        if self.kind == "agent":
            return bool(self.compilation and self.compilation.compiled)
        # an adapter agent needs every connection's crawl plan approved (P5, R-017 #2)
        plans = getattr(self.spec, "plans", {}) or {}
        return all(plans.get(c) == "approved" for c in getattr(self.spec, "connections", []))

    @property
    def spec_id(self) -> Optional[str]:
        if self.spec is None:
            return None
        return self.spec.agent if isinstance(self.spec, WorkflowSpec) else self.spec.id

    def as_record(self) -> dict:
        return {
            "kind": self.kind, "created_from": self.created_from, "understood": self.understood,
            "open_questions": [q.as_record() for q in self.open_questions],
            "assumptions": list(self.assumptions), "spec_id": self.spec_id,
            "spec": (self.spec.model_dump(mode="json") if self.spec is not None else None),
            "spec_yaml": self.spec_yaml, "declaration_yaml": self.declaration_yaml,
            "declaration_source": self.declaration_source,
            "compilation": (self.compilation.as_record() if self.compilation else None),
            "activatable": self.activatable, "model_used": self.model_used,
            "notes": list(self.notes), "derivations": list(self.derivations),
        }


# --------------------------------------------------------------------------------------------
# text helpers (E2E F-32) and question bookkeeping (E2E F-09)
# --------------------------------------------------------------------------------------------
def clip(text: str, limit: int = 80) -> str:
    """``text`` shortened to at most ``limit`` characters on a WHOLE word, never inside an
    unclosed bracket (the clause the bracket opens is dropped whole), trailing punctuation
    removed. Short text is returned unchanged."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    cut = text[:limit + 1]
    cut = cut[:cut.rfind(" ")] if " " in cut else cut[:limit]
    depth_at: list[int] = []
    for i, ch in enumerate(cut):
        if ch in "([{":
            depth_at.append(i)
        elif ch in ")]}" and depth_at:
            depth_at.pop()
    if depth_at:
        cut = cut[:depth_at[0]]
    return cut.rstrip(" ,;:-–—(")


_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def same_question(a: str, b: str, *, threshold: float = 0.8) -> bool:
    """Two question texts ask the same thing: equal once normalised, or word-set Jaccard >=
    ``threshold``. Used to drop the model's echo of a question already asked (E2E F-09)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return ta == tb or len(ta & tb) / len(ta | tb) >= threshold


def model_question_id(text: str) -> str:
    return f"model:{hashlib.sha1(text.strip().encode()).hexdigest()[:8]}"


#: The id prefix of a question the FEDERATED planner raised (E2E N-03). Agreed with the federated
#: track (FED3): ``fed:<sha1 hex of the question text>``, the text stripped and whitespace-collapsed
#: so the same question asked twice has one id. One function, so both sides compute it the same.
FED_PREFIX = "fed:"


def federated_question_id(text: str) -> str:
    """``fed:<sha1>`` for a question the federated planner raised (E2E N-03)."""
    canon = " ".join(str(text).split())
    return FED_PREFIX + hashlib.sha1(canon.encode("utf-8")).hexdigest()


#: The text of every model question this process issued, by id, so an ANSWER to it can be shown
#: to the model with its question on the next compile (the `answers` map carries only ids).
#: Content-addressed (the id is the text's hash), bounded; losing it only loses the echo.
_ISSUED: "OrderedDict[str, str]" = OrderedDict()
_ISSUED_MAX = 2048


def _remember(qid: str, text: str) -> None:
    _ISSUED[qid] = text
    _ISSUED.move_to_end(qid)
    while len(_ISSUED) > _ISSUED_MAX:
        _ISSUED.popitem(last=False)


# --------------------------------------------------------------------------------------------
# model pass
# --------------------------------------------------------------------------------------------
_SYSTEM = (
    "You turn a user's plain-English request into a Walnut agent or adapter-agent draft. "
    "Use ONLY the connections, tools and ontology kinds in the catalogue below; never invent a "
    "name. If the request references something the catalogue does not contain, or leaves a "
    "decision open (which folder, which approval rule, how to undo an autonomous write), put a "
    "question in open_questions instead of guessing. An agent is read_only unless the user asks "
    "it to write; each write action's automation_mode comes from the user's words only: 'ask me "
    "before' = human_required, 'tell me after' = human_notified, 'just do it' = autonomous "
    "(which needs a reversal). Tools: graph.<op>, retrieve.search, source.query:<connection>, "
    "source.read:<connection>:<tool>, delegate.ask:<adapter agent> (a read-only sub-query "
    "answered by a REGISTERED adapter agent listed under adapter_agents — an agent that asks one "
    "is an agent, not a new adapter agent); write actions use source.write:<connection>:<tool>. "
    "instructions are short numbered steps a planner follows; name every source tool a step "
    "uses — a read tool no step names is not granted. The rule-based findings below were "
    "computed deterministically and take precedence; fill what they leave empty. Their "
    "open_questions are already asked: never repeat them. ANSWERS are the user's replies to "
    "earlier questions: use them, and never ask an answered question again.")

#: §5.8 `prompt_version`: the system prompt's content digest, so a draft names the exact prompt.
PROMPT_VERSION = "automation.compile/1+sha256:" + hashlib.sha256(_SYSTEM.encode("utf-8")).hexdigest()[:12]


def _model_pass(text: str, catalogue: CatalogueLike, pp: PrePass, harness: Any,
                task_class: str, answers: Optional[dict[str, str]] = None
                ) -> tuple[Optional[ModelProposal], dict]:
    findings = {
        "kind": pp.kind if pp.kind_by_rule else None,
        "trigger": pp.trigger_type,
        "schedule": ({"cron": pp.schedule.cron, "timezone": pp.schedule.timezone}
                     if pp.schedule else None),
        "event": ({"source": pp.event.source, "on": pp.event.types, "uri_glob": pp.event.uri_glob}
                  if pp.event else None),
        "sources": pp.sources,
        "actions": [{"name": a.name, "tool": a.tool, "automation_mode": a.automation_mode}
                    for a in pp.actions],
        "open_questions": [q.text for q in pp.questions],
    }
    cat = {"connections": [{"id": c.id, "title": c.title, "kind": c.kind,
                            "read_tools": [t.name for t in c.read_tools],
                            "write_tools": [t.name for t in c.write_tools],
                            "query": c.query_capable, "folders": list(c.locations)[:50]}
                           for c in catalogue.connections],
           "internal_tools": list(catalogue.internal_tools), "kinds": list(catalogue.kinds),
           # E2E N-04: registered adapter agents a workflow agent may delegate to
           "adapter_agents": [{"id": a.id, "title": a.title, "connections": list(a.connections),
                               "tool": a.tool} for a in adapter_agents_of(catalogue)]}
    given = [{"id": k, "question": _ISSUED.get(k), "answer": v}
             for k, v in sorted((answers or {}).items()) if str(v).strip()]
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": "CATALOGUE:\n" + json.dumps(cat, sort_keys=True)
         + "\n\nRULE FINDINGS:\n" + json.dumps(findings, sort_keys=True)
         # the answers are part of what the model is shown, so they are part of the harness
         # cache key: an answered compile is a new model call, never a replay (E2E F-09)
         + ("\n\nANSWERS:\n" + json.dumps(given, sort_keys=True) if given else "")},
        {"role": "user", "content": "REQUEST (verbatim):\n" + text},
    ]
    result = harness.complete_structured(task_class, messages, ModelProposal)
    ev = getattr(result, "event", None)
    meta = {"model": getattr(ev, "model_used", None), "task_class": task_class,
            "prompt_version": PROMPT_VERSION}
    return result.parsed, meta


# --------------------------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------------------------
def facts_supported() -> bool:
    """Whether the installed workflow_spec grammar knows `facts.query:<connection>` (S2/F1).
    Checked at call time, so the compiler degrades to `source.query` on an older grammar."""
    try:
        return parse_tool("facts.query:probe")["family"] == "facts"
    except SpecError:
        return False


def _names_tool(text: str, tool: str) -> bool:
    """Whether ``text`` names ``tool`` as a word (``list_schemas``, not ``list_schemas_v2``)."""
    return bool(re.search(r"(?<![\w])" + re.escape(tool) + r"(?![\w])", text, re.I))


def _read_tools(sources: list[str], catalogue: CatalogueLike,
                facts_for: tuple[str, ...] = (), instructions: str = "") -> list[str]:
    """The read grants for ``sources`` — least privilege (E2E F-26, Codex batch 2 #4):

    * a connection in ``facts_for`` (a counting / listing request) is answered from its facts
      store — `facts.query:<id>` INSTEAD of a live `source.query:<id>` (S2);
    * a query-capable connection gets its query path, plus only the read tools a step NAMES;
    * a connection with no query path is read through its read tools: the ones a step names, or
      — when no step names any — all of them (there is no narrower way to read it).

    The internal tools (`graph.*`, `retrieve.search`) are the deployment's choice
    (`catalogue.internal_tools`); they read only the already-screened graph and index, and the
    runtime withholds any pointer outside the agent's declared sources."""
    tools = list(catalogue.internal_tools)
    for cid in sources:
        conn = connection_of(catalogue, cid)
        if conn is None:
            continue
        has_path = False
        if cid in facts_for:
            tools.append(f"facts.query:{cid}")
            has_path = True
        elif conn.query_capable:
            tools.append(f"source.query:{cid}")
            has_path = True
        plain = [t for t in conn.read_tools if not t.query]
        named = [t for t in plain if _names_tool(instructions, t.name)]
        chosen = named if (named or has_path) else plain
        if not has_path and named:
            # FIN-11: a document source is listed and read along its approved reading path; a
            # step that names one tool of that path gets the rest of it (never a write, never a
            # tool outside the path), so "find the memo with list_dir" can also read the memo.
            path = set(conn.reading_path)
            if path & {t.name for t in named}:
                chosen = [t for t in plain if t in named or t.name in path]
        for t in chosen:
            tools.append(f"source.read:{cid}:{t.name}")
    return tools


def _grant_basis(tool: str, catalogue: CatalogueLike, instructions: str) -> tuple[str, dict]:
    """(by, kwargs) for a grant's derivation: why this tool is in the spec."""
    p = parse_tool(tool)
    if p["family"] in ("graph", "retrieve"):
        return "catalogue", {"evidence": "an internal tool this deployment grants every agent "
                                         "(catalogue.internal_tools)"}
    if p["family"] == "facts":
        return "rule", {"evidence": f"counting/listing over {p['connection']}"}
    if p["family"] == "delegate":
        return "rule", {"evidence": f"the request delegates to adapter agent {p['connection']}"}
    if p["verb"] == "query":
        return "rule", {"evidence": f"the query path of {p['connection']}, a source the request "
                                    f"names"}
    if p["tool"] and _names_tool(instructions, str(p["tool"])):
        return "rule", {"evidence": f"a step of the instructions uses {p['tool']}"}
    conn = connection_of(catalogue, str(p["connection"]))
    if conn is not None and p["tool"] in conn.reading_path:
        return "rule", {"evidence": f"{p['tool']} is on {conn.id}'s approved reading path, a step "
                                    f"of which the instructions use (FIN-11)"}
    return "rule", {"evidence": f"{p['connection']} has no query path and no step names a tool, "
                                f"so it is read through its read tools"}


def _validate_tool(tool: str, catalogue: CatalogueLike, *, write: bool) -> Optional[str]:
    """None if ``tool`` is real and of the right class, else the reason it is not."""
    try:
        p = parse_tool(tool)
    except SpecError as exc:
        return str(exc)
    if p["family"] in ("graph", "retrieve"):
        if write:
            return f"{tool} is not a write tool"
        return None if tool in catalogue.internal_tools else f"{tool} is not an available tool"
    if p["family"] == "delegate":
        if write:
            return f"{tool} is a read-only delegated query, not a write"
        return None if adapter_agent_of(catalogue, str(p["connection"])) is not None else (
            f"{p['connection']!r} is not a registered adapter agent that can be delegated to")
    conn = connection_of(catalogue, str(p["connection"]))
    if conn is None:
        return f"{tool} names connection {p['connection']!r}, which is not connected"
    if write and (p["family"] != "source" or p["verb"] != "write"):
        return f"{tool} reads; a write action needs a source.write tool"
    if write and not conn.agent_write:
        via = writers_for(catalogue, conn.id)
        if via:
            return (f"writes to {conn.id} go through its write connection "
                    f"{', '.join(w.id for w in via)}")
    if not write and conn.agent_write:
        return f"{conn.id} is a write-only connection (purpose agent_write); it is never read"
    if p["family"] == "facts":
        return None if conn.facts_capable else f"{conn.id} has no facts store to query"
    if p["verb"] == "query":
        return None if conn.query_capable else f"{conn.id} does not answer queries"
    if not p["tool"]:
        return f"{tool} does not name a tool of {conn.id}"
    ref = conn.tool(str(p["tool"]))
    if ref is None:
        return f"{conn.id} has no tool called {p['tool']!r}"
    if write and ref.name not in {t.name for t in conn.write_tools}:
        # the SHARED classifier decides (E2E N-21); a write-only connection's purpose does not
        # turn its read-classified tools into writes to offer
        return f"{p['tool']} on {conn.id} is not classified as a write tool"
    if not write and (ref.write or p["verb"] == "write"):
        return f"{p['tool']} on {conn.id} writes, so it can only be an approved action"
    if not write and ref.query:
        # E2E run 4 Q-02: the tool that EXECUTES SQL is never a `source.read` grant, even when a
        # crawl plan vouched for it as a read — the runtime never sends a statement through
        # source.read unchecked. It is reached as `source.query:<connection>`, where every
        # statement passes the read-only check (granted for a query-capable source by `_read_tools`).
        return (f"{p['tool']} on {conn.id} executes SQL; it is reached as source.query:{conn.id}, "
                f"where every statement is checked read-only")
    return None


def _title_from(pp: PrePass) -> str:
    if pp.kind == "adapter_agent":
        return "Sources: " + ", ".join(pp.sources) if pp.sources else "Adapter agent"
    first = pp.steps[0].rstrip(".") if pp.steps else pp.text.strip()
    words = first.split()
    from .prepass import _STOP
    words = words[:8]
    while len(words) > 1 and words[-1].lower().strip(".,:;") in _STOP:
        words.pop()
    title = " ".join(words).rstrip(" .,:;-")
    return clip(title) or "Automation"


def _unique_id(base: str, existing: set[str]) -> str:
    cand, i = base, 2
    while cand in existing:
        cand, i = f"{base[:44]}_{i}", i + 1
    return cand


def _describe_trigger(trigger: dict) -> str:
    t = trigger.get("type")
    if t == "schedule":
        s = trigger["schedule"]
        cron = CronSchedule.parse(s["cron"], s["timezone"])
        from datetime import datetime, timezone
        nxt = cron.next_fire(datetime.now(timezone.utc))
        nxt_s = ""
        if nxt is not None:
            from .cron import zone
            nxt_s = f"; next run {nxt.astimezone(zone(s['timezone'])):%a %d %b %Y %H:%M}"
        return f"on a schedule — {cron.describe()} [cron `{s['cron']}`{nxt_s}]"
    if t == "event":
        e = trigger["event"]
        what = {"item_added": "a new item appears", "item_changed": "an item changes",
                "item_removed": "an item is removed", "run_completed": "a sync finishes",
                "run_failed": "a sync fails"}
        ons = " or ".join(what.get(x, x) for x in e["on"])
        where = f" matching {e['filter']['uri_glob']}" if e.get("filter", {}).get("uri_glob") else ""
        return f"whenever {ons} in {e['source']}{where}"
    return "only when you ask for it (on demand)"


def _summary_agent(spec: dict, pp: PrePass, catalogue: CatalogueLike) -> str:
    lines = [f"**{spec['title']}** — an agent that runs {_describe_trigger(spec['trigger'])}."]
    internal = [t for t in spec["tools"] if not t.startswith("source.")]
    src = [t for t in spec["tools"] if t.startswith("source.")]
    reads = "the knowledge graph and document search" if internal else "nothing internal"
    if spec["sources"]:
        reads += " plus " + ", ".join(spec["sources"]) + f" ({', '.join(src)})" if src else \
            " plus " + ", ".join(spec["sources"])
    lines.append(f"It reads {reads}.")
    if spec.get("delegates"):
        lines.append("It delegates read-only sub-questions to the adapter agent(s) "
                     + ", ".join(spec["delegates"]) + " ("
                     + ", ".join(f"delegate.ask:{d}" for d in spec["delegates"]) + ").")
    lines.append("Steps:\n" + "\n".join(f"  {i}. {s}" for i, s in enumerate(pp_steps(spec), 1)))
    if spec["mode"] == "read_only":
        lines.append("It is **read-only**: it cannot write anywhere.")
    else:
        lines.append("It may write, and only through these actions:")
        say = {"human_required": "waits for your approval before it runs",
               "human_notified": "runs, then tells you",
               "autonomous": "runs on its own"}
        for a in spec["actions"]:
            rev = f"; undone by: {a['reversal']}" if a.get("reversal") else ""
            lines.append(f"  - `{a['name']}` via {a['tool']} — {say[a['automation_mode']]}{rev}")
        lines.append("Nothing else is written.")
    lines.append("Every claim in its answers must cite a source pointer.")
    return "\n".join(lines)


def pp_steps(spec: dict) -> list[str]:
    return [re.sub(r"^\d+\.\s*", "", s).strip() for s in spec["instructions"].splitlines()
            if s.strip()]


def _summary_adapter(spec: dict) -> str:
    sync = _describe_trigger(spec["sync"])
    plans = spec.get("plans") or {}
    lines = [f"**{spec['title']}** — an adapter agent that keeps {', '.join(spec['connections'])} "
             f"in the graph, syncing {sync}."]
    for c in spec["connections"]:
        st = plans.get(c)
        lines.append(f"  - {c}: crawl plan {st or 'not proposed yet — it must be approved before a sync runs'}")
    lines.append(f"Extraction maps only to the `{spec['extract']['pack']}` ontology; screening uses "
                 f"packs/{spec['screen'].get('pack', spec['extract']['pack'])}/screen.yaml. "
                 f"Data older than {spec['freshness']['max_age_hours']}h is flagged stale.")
    lines.append("It reads sources and writes only to the graph; it never writes back to a source.")
    return "\n".join(lines)


def _understood_partial(pp: PrePass) -> str:
    parts = ["I could not build this yet — please answer the questions below."]
    if pp.kind == "adapter_agent":
        parts.append("It looks like an adapter agent (keeping sources in the graph).")
    if pp.delegates:
        parts.append("It delegates to the adapter agent(s) " + ", ".join(pp.delegates) + ".")
    if pp.schedule:
        try:
            parts.append("When: " + CronSchedule.parse(pp.schedule.cron,
                                                       pp.schedule.timezone).describe() + ".")
        except CronError:                                           # pragma: no cover
            pass
    elif pp.event:
        parts.append(f"When: {pp.event.phrase}.")
    else:
        parts.append("When: on demand.")
    if pp.sources:
        parts.append("Sources I recognised: " + ", ".join(pp.sources) + ".")
    if pp.actions:
        parts.append("Writes I found: " + "; ".join(a.clause for a in pp.actions) + ".")
    return " ".join(parts)


def compile_english(text: str, catalogue: CatalogueLike, *, harness: Any = None,
                    answers: Optional[dict[str, str]] = None,
                    default_timezone: Optional[str] = None,
                    task_class: str = TASK_CLASS,
                    purposes: Any = None, repo_root: Optional[Path] = None,
                    packs_dir: Optional[Path] = None) -> DraftResult:
    """Compile the user's English into a draft. See the module docstring for the pipeline.

    ``harness`` is a `databridge_harness.Harness` (or None: rules only, fully offline).
    ``answers`` maps a question id from a previous result to the user's answer, e.g.
    ``{"reversal:save_report": "delete the file", "folder:Contracts": "drive:Legal/Contracts",
    "mode:log_task": "human_required", "schedule_ampm": "pm", "timezone": "Europe/London",
    "model_source:crm": "yes", "model_action:save_list": "yes"}``.
    ``default_timezone`` is the deployment's zone; when omitted, `WALNUT_TIMEZONE`; when neither
    is set and the sentence names no zone, a schedule becomes a question.

    The model PROPOSES; nothing it proposes is a user reference until the user's words contain it
    or the user answers yes (R-017 #4). A catalogue-valid name is validated, not grounded.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("compile_english needs the user's words")
    created_from = text                                   # verbatim, never reworded
    answers = dict(answers or {})
    if default_timezone:
        tz, tz_source = default_timezone, "deployment default_timezone"
    else:
        tz, tz_source = paths.configured_timezone(), "WALNUT_TIMEZONE"
    pp = prepass(text, catalogue, default_tz=tz, answers=answers, tz_source=tz_source)
    result = DraftResult(kind=pp.kind, created_from=created_from, understood="",
                         assumptions=list(pp.assumptions))
    derivations: list[dict] = [dict(d) for d in pp.derivations]

    def derive(field_name: str, value: Any, by: str, *, evidence: str = "",
               meta: Optional[dict] = None) -> None:
        derivations[:] = [d for d in derivations if d["field"] != field_name]
        d: dict = {"field": field_name, "value": value, "by": by, "span": None,
                   "evidence": evidence, "model": None}
        if meta:
            d.update(model=meta.get("model"), task_class=meta.get("task_class"),
                     prompt_version=meta.get("prompt_version"))
        derivations.append(d)

    # --- model pass (optional) ---------------------------------------------------------------
    proposal: Optional[ModelProposal] = None
    meta: dict = {}
    if harness is not None:
        try:
            proposal, meta = _model_pass(text, catalogue, pp, harness, task_class, answers)
            result.model_used = True
        except Exception as exc:                          # noqa: BLE001 - degrade to rules, say so
            result.notes.append(f"the model pass failed ({type(exc).__name__}); this draft is "
                                f"from rules only")

    questions = list(pp.questions)
    sources = list(pp.sources)
    grounded = set(pp.sources)            # connections the user's words or answers named
    kind = pp.kind
    title = ""
    steps = list(pp.steps)
    extra_tools: list[str] = []
    actions = list(pp.actions)
    model_actions: list[ProposedAction] = []
    subject_hit = bool(subject_mentions(text, catalogue))

    if proposal is not None:
        if not pp.kind_by_rule and proposal.kind == "adapter_agent":
            ans = answers.get("model_kind", "").strip().lower()
            if ans in ("adapter_agent", "yes"):
                kind = "adapter_agent"
                derive("kind", kind, "model+answer", evidence=f"model_kind = {ans}", meta=meta)
            elif ans not in ("agent", "no"):
                questions.append(Question(
                    "model_kind", "This could be an adapter agent (keeping sources in the "
                                  "graph) rather than an agent that does work. Which did you "
                                  "mean?", ["adapter_agent", "agent"]))
        for sid in proposal.sources:
            if connection_of(catalogue, sid) is None:
                qid = f"source:{slug(sid)}"
                ans = answers.get(qid, "").strip()
                if connection_of(catalogue, ans) is not None:
                    if ans not in sources:
                        sources.append(ans)
                    grounded.add(ans)
                    derive(f"sources[{ans}]", ans, "answer", evidence=f"{qid} = {ans}")
                    continue
                questions.append(Question(qid, f"'{sid}' is not a connected source. Which source "
                                               f"did you mean?",
                                          [c.id for c in catalogue.connections]))
                continue
            if sid in sources:
                continue
            qid = f"model_source:{sid}"
            ans = answers.get(qid, "").strip().lower()
            if ans in ("yes", "y", "true"):
                sources.append(sid)
                grounded.add(sid)
                derive(f"sources[{sid}]", sid, "model+answer", evidence=f"{qid} = yes", meta=meta)
            elif ans in ("no", "n", "false"):
                result.notes.append(f"the model suggested reading {sid}; you declined")
            else:
                questions.append(Question(qid, f"You didn't name a source for this, but "
                                               f"'{sid}' looks relevant. Should it read {sid}?",
                                          ["yes", "no"]))
        for t in proposal.tools:
            reason = _validate_tool(t, catalogue, write=False)
            if not reason:
                p = parse_tool(t)
                if p["family"] == "delegate":
                    if p["connection"] not in pp.delegates:
                        reason = (f"{p['connection']} is not an adapter agent you named or chose "
                                  f"(the model may not add a delegate)")
                elif p["family"] not in ("graph", "retrieve") and p["connection"] not in grounded:
                    reason = (f"{p['connection']} is not a source you named or confirmed "
                              f"(a tool is not a reason to read a source)")
            if reason:
                result.notes.append(f"dropped model tool {t}: {reason}")
            else:
                extra_tools.append(t)
                derive(f"tools[{t}]", t, "model", meta=meta,
                       evidence="model proposal, validated against the catalogue, on a source "
                                "the user named")
        for k in proposal.kinds_referenced:
            if k not in catalogue.kinds:
                ans = answers.get(f"kind:{slug(k)}", "").strip()
                if ans in catalogue.kinds:
                    derive(f"kinds[{k}]", ans, "answer", evidence=f"kind:{slug(k)} = {ans}")
                    if ans in catalogue.subject_kinds:
                        subject_hit = True
                    continue
                questions.append(Question(f"kind:{slug(k)}",
                                          f"'{k}' is not a kind in the ontology. Add it on "
                                          f"/ontology, or tell me which existing kind you meant.",
                                          list(catalogue.kinds)))
            elif k in catalogue.subject_kinds:
                subject_hit = True        # the model saw a person-like subject: fail closed
        if proposal.title.strip():
            title = clip(proposal.title)
            derive("title", title, "model", meta=meta)
        if [s for s in proposal.instructions if s.strip()]:
            steps = [re.sub(r"^\d+[.)]\s*", "", s).strip() for s in proposal.instructions
                     if s.strip()]
            derive("instructions", steps, "model", meta=meta)
        if not pp.schedule and not pp.event and proposal.trigger_type != "manual":
            questions.append(Question(
                "trigger", "I didn't find a schedule or an event in your words, but this reads "
                           "like an automation. When should it run? (e.g. 'every weekday at 9am', "
                           "'when a new file lands in <folder>', or 'only when I ask')"))
        if kind == "agent" and not actions and proposal.actions:
            model_actions = proposal.actions
        answered_texts = [_ISSUED[k] for k in answers if k in _ISSUED]
        for q in proposal.open_questions:
            q = q.strip()
            if not q:
                continue
            qid = model_question_id(q)
            _remember(qid, q)
            if qid in answers and str(answers[qid]).strip():
                continue                               # answered: fed to the model above
            if any(same_question(q, t) for t in answered_texts):
                continue                               # the model re-asked an answered one
            if any(same_question(q, other.text) for other in questions):
                result.notes.append("the model repeated a question already asked; asked once")
                continue
            questions.append(Question(qid, q))

    for qid, ans in sorted(answers.items()):
        if qid.startswith(FED_PREFIX) and str(ans).strip() and pp.delegates:
            derive(f"answers[{qid}]", str(ans).strip(), "answer",
                   evidence=f"{qid} = {ans} (an answer to a question the federated planner "
                            f"asked; passed to the delegated queries)")
        if qid.startswith("model:") and str(ans).strip():
            derive(f"answers[{qid}]", str(ans).strip(), "answer",
                   evidence=f"{qid} = {ans}" + (f" (asked: {_ISSUED[qid]})" if qid in _ISSUED
                                                else ""))

    # model actions: a write the user never named is asked about — even with a real tool — and
    # then needs a mode from the user's words (or an answer) and a reversal from the user
    if model_actions:
        from .prepass import _RE_REVERSAL, ActionFinding, _approval_modes
        stated = {m for m, _ in _approval_modes(text)}
        rev = _RE_REVERSAL.search(text)
        for pa in model_actions:
            name = slug(pa.name, max_len=40)
            tool = pa.tool or ""
            reason = _validate_tool(tool, catalogue, write=True) if tool else "no tool named"
            if reason:
                questions.append(Question(f"tool:{name}", f"Action '{name}': {reason}. Which "
                                                          f"connected source and tool should do it?"))
                continue
            conn = str(parse_tool(tool)["connection"])
            what = pa.description or name.replace("_", " ")
            qid = f"model_action:{name}"
            confirm = answers.get(qid, "").strip().lower()
            if confirm in ("no", "n", "false"):
                result.notes.append(f"the model suggested the write '{what}'; you declined")
                continue
            if confirm not in ("yes", "y", "true"):
                questions.append(Question(qid, f"You didn't ask it to write anything, but it could "
                                               f"'{what}' using {tool} (writing to {conn}). "
                                               f"Should it?", ["yes", "no"]))
                continue
            af = ActionFinding(verb="", obj=what, clause=what, span=(0, 0), name=name,
                               connection=conn, tool=tool)
            derive(f"actions[{name}].tool", tool, "model+answer", evidence=f"{qid} = yes",
                   meta=meta)
            answer = answers.get(f"mode:{name}")
            if answer in _MODES:
                af.automation_mode = answer
                derive(f"actions[{name}].automation_mode", answer, "answer",
                       evidence=f"mode:{name} = {answer}")
            elif pa.automation_mode and pa.automation_mode in stated and len(stated) == 1:
                af.automation_mode = pa.automation_mode
                derive(f"actions[{name}].automation_mode", af.automation_mode, "model", meta=meta,
                       evidence="the one approval rule in the user's words")
            else:
                questions.append(Question(f"mode:{name}", f"For '{af.clause}': should I ask you "
                                                          f"before doing it, do it and tell you "
                                                          f"after, or just do it?", list(_MODES)))
            if af.automation_mode == "autonomous":
                given = (answers.get(f"reversal:{name}") or "").strip()
                if given:
                    af.reversal = given
                    derive(f"actions[{name}].reversal", given, "answer",
                           evidence=f"reversal:{name}")
                elif rev is not None:            # the user's own undo words, never the model's
                    af.reversal = rev.group("rev").strip()
                    derive(f"actions[{name}].reversal", af.reversal, "rule",
                           evidence=rev.group(0))
                else:
                    questions.append(Question(f"reversal:{name}",
                                              f"'{af.clause}' will run without asking you. If it "
                                              f"goes wrong, how should it be undone?"))
            if conn not in sources:
                sources.append(conn)
            actions.append(af)

    # identity is a property of the whole workflow (R-017 #3): any write in a request that names
    # a subject kind — in any step, or as the model read it — touches a subject
    if subject_hit:
        for a in actions:
            a.touches_subject = True

    # sources written to are sources
    for a in actions:
        if a.connection and a.connection not in sources:
            sources.append(a.connection)
            derive(f"sources[{a.connection}]", a.connection, "rule",
                   evidence=f"written to by action {a.name}")

    # de-duplicate questions by id, keep order
    seen: set[str] = set()
    questions = [q for q in questions if not (q.id in seen or seen.add(q.id))]
    result.kind = kind

    if kind == "adapter_agent" and not sources:
        picked = [c for c in re.split(r"[,\s]+", answers.get("connections", "")) if c
                  and connection_of(catalogue, c) is not None
                  and not connection_of(catalogue, c).agent_write]
        for c in dict.fromkeys(picked):
            sources.append(c)
            derive(f"sources[{c}]", c, "answer", evidence=f"connections = {answers['connections']}")
        if not sources:
            questions.append(Question("connections", "Which connected sources should this adapter "
                                                     "agent keep in the graph?",
                                      [c.id for c in catalogue.connections]))

    result.open_questions = questions
    result.derivations = derivations
    if questions:
        result.understood = _understood_partial(pp)
        return result

    # least privilege (E2E F-26): a model-proposed `source.read` tool is granted only when a step
    # of the final instructions names it — a tool no step uses is not needed
    steps_text = "\n".join(steps)
    for t in list(extra_tools):
        p = parse_tool(t)
        if p["family"] == "source" and p["verb"] == "read" and p["tool"] \
                and not _names_tool(steps_text, str(p["tool"])):
            extra_tools.remove(t)
            derivations[:] = [d for d in derivations if d["field"] != f"tools[{t}]"]
            result.notes.append(f"not granted {t}: no step of the instructions uses it "
                                f"(least privilege)")

    existing = set(catalogue.existing_ids)
    if not title:
        title = _title_from(pp)
        derive("title", title, "rule", evidence="the first words of the request")
    if not any(d["field"] == "instructions" for d in derivations):
        derive("instructions", steps, "rule", evidence="the request's own sentences, numbered")
    model_id = bool(proposal and proposal.agent_id.strip())
    base_id = slug(proposal.agent_id if model_id else title)
    spec_id = _unique_id(base_id, existing)
    derive("id", spec_id, "model" if model_id else "rule", meta=meta if model_id else None,
           evidence="slug, unique among existing ids")

    if pp.schedule:
        trigger: dict = {"type": "schedule", "schedule": {"cron": pp.schedule.cron,
                                                          "timezone": pp.schedule.timezone}}
    elif pp.event:
        ev: dict = {"source": pp.event.source, "on": pp.event.types}
        if pp.event.uri_glob:
            ev["filter"] = {"uri_glob": pp.event.uri_glob}
        trigger = {"type": "event", "event": ev}
    else:
        trigger = {"type": "manual"}
        derive("trigger.type", "manual", "rule", evidence="no schedule or event in the words")

    if kind == "adapter_agent":
        data = _adapter_spec(pp, spec_id, title, created_from, sources, trigger, catalogue, result)
        for cid, st in (data.get("plans") or {}).items():
            derive(f"plans[{cid}]", st, "catalogue", evidence=f"crawl plan status of {cid}")
        if pp.freshness_hours is None:
            derive("freshness.max_age_hours", data["freshness"]["max_age_hours"], "default",
                   evidence="adapter_agent/v1 default; it only flags data as stale")
        if data["sync"] != trigger:
            derive("sync.type", data["sync"]["type"], "rule",
                   evidence="'re-ingest on change' with exactly one source watches that source")
    else:
        facts_for: tuple[str, ...] = ()
        if pp.facts_intent:
            cands = tuple(c for c in sources if (conn := connection_of(catalogue, c)) is not None
                          and conn.facts_capable)
            if cands and facts_supported():
                facts_for = cands
                for c in cands:
                    derive(f"tools[facts.query:{c}]", f"facts.query:{c}", "rule",
                           evidence=f"counting/listing request "
                                    f"'{text[pp.facts_intent[0]:pp.facts_intent[1]]}' over {c}")
            elif cands:
                result.notes.append("this counts or lists, but the installed workflow_spec has no "
                                    "facts.query tool yet; it uses a live source.query instead")
        clarifications = [str(v).strip() for k, v in sorted(answers.items())
                          if k.startswith(FED_PREFIX) and str(v).strip()] if pp.delegates else []
        data = _workflow_spec(pp, spec_id, title, created_from, sources, trigger, steps,
                              extra_tools, actions, catalogue, facts_for, subject_hit=subject_hit,
                              delegates=list(pp.delegates), clarifications=clarifications)
        for t in data["tools"]:
            if not any(d["field"] == f"tools[{t}]" for d in derivations):
                by, kw = _grant_basis(t, catalogue, data["instructions"])
                derive(f"tools[{t}]", t, by, **kw)
        if subject_hit:
            derive("instructions.candidates", CANDIDATE_STEP, "rule",
                   evidence="the request names a subject kind "
                            f"({', '.join(sorted(set(k for k, _ in subject_mentions(text, catalogue)) or ['model reading']))})")
        derive("mode", data["mode"], "rule",
               evidence="read_write only because of the declared actions" if data["actions"]
               else "no write action in the request")
    data["derivations"] = [dict(d) for d in derivations]        # persisted with the spec (§5.8)
    try:
        spec = load_spec(data)
    except SpecError as exc:
        result.open_questions.append(Question("spec", f"I could not build a valid draft: {exc}"))
        result.understood = _understood_partial(pp)
        return result
    result.spec = spec
    result.spec_yaml = dump_spec(spec)

    if isinstance(spec, WorkflowSpec):
        _governance(result, spec, actions, catalogue, purposes, repo_root, packs_dir)
        result.understood = _summary_agent(spec.model_dump(mode="json"), pp, catalogue)
    else:
        for cid in spec.connections:
            st = spec.plans.get(cid)
            if st != "approved":
                result.open_questions.append(Question(
                    f"plan:{cid}", f"{cid}'s crawl plan is {st or 'not proposed yet'}. Approve it "
                                   f"on /sources before this adapter agent can be activated — a "
                                   f"sync reads only what an approved plan declares."))
        result.understood = _summary_adapter(spec.model_dump(mode="json"))
    if result.assumptions:
        result.understood += "\nI assumed: " + "; ".join(result.assumptions) + "."
    result.derivations = derivations
    return result


#: The step a request that names a subject kind (a person) always carries: identity is never
#: decided by the agent (CLAUDE.md hard constraint 1). The runtime marks subject entities as
#: candidates, and any write about one is `human_required`; this says so to the planner.
CANDIDATE_STEP = ("People it finds are candidates only: list each with the evidence for it and "
                  "never state which one someone is; a person selects the match.")


def _workflow_spec(pp: PrePass, spec_id: str, title: str, created_from: str, sources: list[str],
                   trigger: dict, steps: list[str], extra_tools: list[str], actions: list,
                   catalogue: CatalogueLike, facts_for: tuple[str, ...] = (), *,
                   subject_hit: bool = False, delegates: Optional[list[str]] = None,
                   clarifications: Optional[list[str]] = None) -> dict:
    acts = []
    for a in actions:
        body: dict = {"name": a.name, "tool": a.tool, "automation_mode": a.automation_mode,
                      "description": a.clause + (f" (into {a.location})" if a.location
                                                  and a.location not in a.clause else "")}
        if a.reversal:
            body["reversal"] = a.reversal
        acts.append(body)
    step_lines = list(steps) or [pp.text.strip()]
    if subject_hit:
        step_lines.append(CANDIDATE_STEP)
    delegates = list(dict.fromkeys(delegates or []))
    for d in delegates:
        aa = adapter_agent_of(catalogue, d)
        over = [c for c in (aa.connections if aa else ()) if c in sources] or list(sources)
        step_lines.append(f"Send each sub-question that needs {', '.join(over) or 'its sources'} "
                          f"to delegate.ask:{d} (a read-only delegated query) and combine only "
                          f"what it returns with its pointers.")
    for c in clarifications or []:
        step_lines.append(f"The user clarified: {c}")
    if facts_for:
        step_lines.append("Answer every count or list with "
                          + ", ".join(f"facts.query:{c}" for c in facts_for)
                          + " (exact SQL over the facts store); never count from search results.")
    if acts:
        names = ", ".join(a["name"] for a in acts)
        step_lines.append(f"Write only through the declared actions ({names}); every other step "
                          f"is read-only.")
    step_lines.append("Cite a source pointer for every claim; flag anything you cannot cite.")
    instructions = "\n".join(f"{i}. {s}" for i, s in enumerate(step_lines, 1)) + "\n"
    tools = _read_tools(sources, catalogue, facts_for, instructions)
    tools += [f"delegate.ask:{d}" for d in delegates if f"delegate.ask:{d}" not in tools]
    for t in extra_tools:
        if t not in tools:
            tools.append(t)
    out = {"spec": "workflow/v1", "agent": spec_id, "title": title,
           "created_from": created_from, "mode": "read_write" if acts else "read_only",
           "sources": sources, "tools": tools, "actions": acts, "trigger": trigger,
           "instructions": instructions, "status": "draft"}
    if delegates:
        out["delegates"] = delegates
    return out


def _adapter_spec(pp: PrePass, spec_id: str, title: str, created_from: str, sources: list[str],
                  trigger: dict, catalogue: CatalogueLike, result: DraftResult) -> dict:
    if trigger["type"] == "manual" and pp.change_sync and len(sources) == 1:
        trigger = {"type": "event", "event": {"source": sources[0],
                                              "on": ["item_added", "item_changed"]}}
    elif trigger["type"] == "manual" and pp.change_sync:
        result.notes.append("re-ingesting on change needs one source to watch; with several "
                            "sources it runs on demand — add a schedule ('check hourly') to sync "
                            "them all")
    plans = {}
    for cid in sources:
        conn = connection_of(catalogue, cid)
        if conn is not None and conn.plan_status in ("proposed", "approved"):
            plans[cid] = conn.plan_status
    pack = catalogue.governance.pack
    if pp.freshness_hours is None:
        result.assumptions.append("data older than 24 hours counts as stale (the default); "
                                  "say 'no older than N hours' to change it")
    return {"spec": "adapter_agent/v1", "id": spec_id, "title": title,
            "created_from": created_from, "connections": sources, "plans": plans,
            "sync": trigger, "extract": {"pack": pack, "prose": True, "tabular": True,
                                         "passages": True},
            "screen": {"pack": pack},
            "freshness": {"max_age_hours": pp.freshness_hours or 24},
            "propose_ontology_additions": True, "status": "draft"}


def _governance(result: DraftResult, spec: WorkflowSpec, actions: list, catalogue: CatalogueLike,
                purposes: Any, repo_root: Optional[Path],
                packs_dir: Optional[Path]) -> None:
    touches = {a.name: bool(getattr(a, "touches_subject", False)) for a in actions}
    gov = catalogue.governance
    for a in spec.actions:
        if touches.get(a.name) and a.automation_mode != "human_required":
            result.open_questions.append(Question(
                f"mode:{a.name}",
                f"'{a.name}' writes something about a person, so it must wait for your approval "
                f"(human_required) — identity-touching writes are never automatic. Change it to "
                f"'ask me before'?", ["human_required"]))
    label = GENERATOR
    try:
        # the runtime's generator, the runtime's names: what compiles here is what runs (W2). There
        # is no injected builder: a second generator is the bug W2 fixed (Codex batch 2 #8).
        text = declaration_from_workflow(spec, gov, touches_subject=touches)
        decl = yaml.safe_load(text)
        if not isinstance(decl, dict):
            raise TypeError("the declaration is not a mapping")
    except Exception as exc:                                  # noqa: BLE001 - shown, not hidden
        # No fallback generator: a second one that names things differently is the bug W2 fixed.
        result.declaration_source = label
        result.compilation = Compilation(
            "not_compiled", reason=f"the {label} declaration generator refused this workflow: "
                                   f"{exc}")
        return
    if gov.citation != RUNTIME_CITATION:
        result.notes.append(f"the deployment asks for citation {gov.citation!r}; the declaration "
                            f"uses the runtime's {RUNTIME_CITATION!r} (the stricter rule)")
    result.declaration = decl
    result.declaration_yaml = text
    result.declaration_source = label
    result.compilation = compile_declaration(text, catalogue, purposes=purposes,
                                             repo_root=repo_root, packs_dir=packs_dir)
    # the runtime's own pairing check (`load_agent` / `compile_for_run` run it before every run):
    # a pair it would refuse is not activatable here either
    if result.compilation.compiled:
        try:
            problems = pair_problems(spec, parse_declaration_text(
                text, declared_in=f"var/agents/{spec.agent}.agent.yaml"))
        except Exception as exc:                              # noqa: BLE001 - shown, not hidden
            problems = [f"{type(exc).__name__}: {exc}"]
        if problems:
            result.compilation = Compilation(
                "not_compiled", reason="the runtime would refuse this workflow/declaration pair: "
                                       + "; ".join(problems), problems=list(problems))


# --------------------------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------------------------
class ActivationRefused(RuntimeError):
    pass


def save_draft(result: DraftResult, *, activate: bool = False,
               agents_dir: Optional[Path] = None,
               adapter_agents_dir: Optional[Path] = None) -> list[Path]:
    """Write the draft's files (§5.6 locations). ``activate=True`` sets `status: active` and is
    refused unless the draft is `activatable` — no open question, and (agents) a compiled
    declaration. There is no override."""
    if result.spec is None:
        raise ActivationRefused("there is no draft to save: " + "; ".join(
            q.text for q in result.open_questions))
    if activate and not result.activatable:
        why = [q.text for q in result.open_questions]
        if result.compilation and not result.compilation.compiled:
            why += [r["detail"] for r in result.compilation.refusals] or [result.compilation.reason]
        raise ActivationRefused("this draft cannot be activated: " + "; ".join(why))
    spec = result.spec.model_copy(update={"status": "active" if activate else "draft"})
    written: list[Path] = []
    if isinstance(spec, WorkflowSpec):
        base = agents_dir or paths.agents_dir()
        base.mkdir(parents=True, exist_ok=True)
        wf = base / f"{spec.agent}.workflow.yaml"
        wf.write_text(dump_spec(spec), encoding="utf-8", newline="\n")
        written.append(wf)
        if result.declaration_yaml:
            ag = base / f"{spec.agent}.agent.yaml"
            ag.write_text(result.declaration_yaml, encoding="utf-8", newline="\n")
            written.append(ag)
    else:
        base = adapter_agents_dir or paths.adapter_agents_dir()
        base.mkdir(parents=True, exist_ok=True)
        p = base / f"{spec.id}.yaml"
        p.write_text(dump_spec(spec), encoding="utf-8", newline="\n")
        written.append(p)
    return written


__all__ = ["compile_english", "DraftResult", "ModelProposal", "ProposedAction", "save_draft",
           "ActivationRefused", "TASK_CLASS", "PROMPT_VERSION", "federated_question_id",
           "FED_PREFIX"]
