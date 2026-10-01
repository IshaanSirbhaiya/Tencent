"""The deterministic pre-pass (P16: rules first, model second).

Reads the user's English with rules only — no model, no network — and finds what rules CAN find:

* **when**: a schedule (`every weekday at 9am`, `hourly`, `every Monday at 9`, `every morning`),
  an event (`when a new contract lands in the Contracts folder`), or neither (on demand);
* **what kind**: an agent (automates work) or an adapter agent (keeps sources in the graph);
* **over which sources**: connection names, their aliases, source-type words, folder names —
  each resolved against the catalogue, never assumed;
* **what it writes and with what approval**: write verbs at the head of a clause become actions,
  and the user's approval words (`ask me before…`, `tell me after…`, `just do it`) set each
  action's automation mode. An autonomous action needs a reversal; if the sentence has none,
  the pre-pass asks for one.

Everything it cannot settle becomes a `Question`. It never fills a gap with a plausible value:
an unknown folder, an unconnected system, an ambiguous `at 5`, a write with no stated approval.
The few conventions it applies (`morning` = 09:00) are listed in `assumptions` and shown to the
user in the summary, so they are visible rather than silent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .catalogue import (
    KIND_WORDS,
    CatalogueLike,
    ConnectionInfo,
    adapter_agent_of,
    adapter_agents_of,
    connection_of,
    writers_for,
)
from .cron import CronError, CronSchedule, zone


# --------------------------------------------------------------------------------------------
# result shapes
# --------------------------------------------------------------------------------------------
@dataclass
class Question:
    id: str
    text: str
    options: list[str] = field(default_factory=list)

    def as_record(self) -> dict:
        return {"id": self.id, "text": self.text, "options": list(self.options)}


@dataclass
class Mention:
    start: int
    end: int
    phrase: str
    connections: list[str]
    location: Optional[str] = None          # resolved folder path, if a folder was named
    folder: Optional[str] = None            # the folder name as the user wrote it


@dataclass
class ScheduleFinding:
    cron: str
    timezone: str
    phrase: str
    span: tuple[int, int]


@dataclass
class EventFinding:
    types: list[str]
    thing: str
    phrase: str
    span: tuple[int, int]
    source: Optional[str] = None
    uri_glob: Optional[str] = None


@dataclass
class ActionFinding:
    verb: str
    obj: str
    clause: str
    span: tuple[int, int]
    name: str
    connection: Optional[str] = None
    location: Optional[str] = None
    tool: Optional[str] = None
    automation_mode: Optional[str] = None
    reversal: Optional[str] = None
    touches_subject: bool = False


@dataclass
class PrePass:
    text: str
    kind: str = "agent"
    kind_by_rule: bool = False
    schedule: Optional[ScheduleFinding] = None
    event: Optional[EventFinding] = None
    change_sync: bool = False
    mentions: list[Mention] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    actions: list[ActionFinding] = field(default_factory=list)
    read_only_declared: bool = False
    facts_intent: Optional[tuple[int, int]] = None
    """Span of a counting / listing request ("how many", "list all") — answered from the facts
    store (`facts.query:<connection>`), exactly, never counted from search results (S2)."""
    freshness_hours: Optional[int] = None
    steps: list[str] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    kinds_mentioned: list[str] = field(default_factory=list)
    delegates: list[str] = field(default_factory=list)
    """Registered adapter agents the request delegates read-only sub-queries to
    (``delegate.ask:<id>``, E2E N-04) — named in the user's words or chosen in an answer."""
    delegate_spans: list[tuple[int, int]] = field(default_factory=list)
    """Where the words refer to an EXISTING adapter agent ("the larkspur_graph_sync adapter
    agent"): a reference, so it never makes the request itself an adapter agent."""
    derivations: list[dict] = field(default_factory=list)
    """§5.8: per emitted field, what produced it — ``rule`` (with the span of the user's words it
    came from), ``answer`` (the user's answer to a question), ``config`` (deployment config) —
    and the evidence. The compiler adds the model's contributions."""

    @property
    def trigger_type(self) -> str:
        if self.schedule:
            return "schedule"
        if self.event:
            return "event"
        return "manual"

    def ask(self, qid: str, text: str, options: Optional[list[str]] = None) -> None:
        if not any(q.id == qid for q in self.questions):
            self.questions.append(Question(qid, text, list(options or [])))

    def derive(self, field_name: str, value, by: str, *, span: Optional[tuple[int, int]] = None,
               evidence: str = "") -> None:
        self.derivations = [d for d in self.derivations if d["field"] != field_name]
        self.derivations.append({"field": field_name, "value": value, "by": by,
                                 "span": list(span) if span else None,
                                 "evidence": evidence or (self.text[span[0]:span[1]]
                                                          if span else ""),
                                 "model": None})


# --------------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------------
_DETERMINERS = {"the", "my", "our", "a", "an", "any", "some", "this", "that", "their", "its",
                "your", "each", "every"}
_STOP = _DETERMINERS | {"and", "or", "then", "to", "in", "into", "from", "on", "of", "for", "with",
                        "is", "are", "be", "been", "was", "were", "me", "us", "i", "it", "after",
                        "before", "just", "please", "also", "anything", "everything", "all"}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def stem(word: str) -> str:
    w = re.sub(r"[^a-z]", "", word.lower())
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            w = w[: -len(suf)]
            break
    if len(w) > 3 and w[-1] == w[-2]:
        w = w[:-1]
    if len(w) > 3 and w.endswith("e"):
        w = w[:-1]
    return w


def slug(text: str, *, max_len: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    s = re.sub(r"_+", "_", s)[:max_len].strip("_")
    if not s or not s[0].isalpha():
        s = f"a_{s}" if s else "automation"
    return s


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _word_re(phrase: str) -> re.Pattern:
    parts = [re.escape(p) for p in re.split(r"[\s\-_]+", phrase.strip()) if p]
    return re.compile(r"(?<![\w])" + r"[\s\-_]+".join(parts) + r"(?![\w])", re.I)


# --------------------------------------------------------------------------------------------
# WHEN — schedules
# --------------------------------------------------------------------------------------------
_DAY_TOKENS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
_DAY_WORD = r"(?:mon|tue|tues|wed|wednes|thu|thur|thurs|fri|sat|satur|sun)(?:day)?s?"
_TZ_WORDS = {"sgt": "Asia/Singapore", "singapore time": "Asia/Singapore", "utc": "UTC",
             "gmt": "UTC"}

_RE_MINUTES = re.compile(r"\bevery\s+(\d{1,2})\s*(?:minutes|mins?)\b", re.I)
_RE_HOURS = re.compile(r"\bevery\s+(\d{1,2})\s*(?:hours|hrs?)\b", re.I)
_RE_HOURLY = re.compile(r"\b(?:hourly|every\s+hour|each\s+hour|once\s+an\s+hour)\b", re.I)
_RE_WEEKDAYS = re.compile(
    r"\b(?:(?:every|each|on)\s+)?(?:weekdays?|working\s+days?|business\s+days?)\b"
    r"|\bmon(?:day)?\s*(?:-|to|through|thru)\s*fri(?:day)?\b", re.I)
_RE_WEEKENDS = re.compile(r"\b(?:(?:every|each|on)\s+)?weekends?\b", re.I)
_RE_NAMED_DAYS = re.compile(
    r"\b(?:every|each|on)\s+(" + _DAY_WORD + r"(?:\s*(?:,|and|&|or)\s*" + _DAY_WORD + r")*)\b",
    re.I)
_RE_DAILY = re.compile(
    r"\b(?:daily|nightly|every\s+day|each\s+day|every\s+(?:morning|afternoon|evening|night)"
    r"|each\s+(?:morning|afternoon|evening|night))\b", re.I)
_RE_PERIOD = re.compile(r"\b(morning|afternoon|evening|night|nightly)\b", re.I)
_PERIOD_HOUR = {"morning": 9, "afternoon": 14, "evening": 18, "night": 21, "nightly": 21}
_RE_WEEKLY = re.compile(r"\b(?:weekly|every\s+week|once\s+a\s+week)\b", re.I)
_RE_MONTHLY = re.compile(r"\b(?:monthly|every\s+month|each\s+month|once\s+a\s+month)\b", re.I)
_RE_MONTH_DAY = re.compile(r"\bon\s+the\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(?:of|day\s+of)\s+"
                           r"(?:every|each|the)\s+month)?\b", re.I)
_RE_TIME = re.compile(
    r"\bat\s+(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?(?![\w:])"
    r"|\b(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?)(?!\w)", re.I)
_RE_NOON = re.compile(r"\b(?:at\s+)?(noon|midday|midnight)\b", re.I)
_RE_IANA = re.compile(r"\b([A-Z][a-zA-Z]+/[A-Z][A-Za-z_]+(?:/[A-Z][A-Za-z_]+)?)\b")


def _parse_days(group: str) -> list[int]:
    out = []
    for tok in re.findall(_DAY_WORD, group, re.I):
        out.append(_DAY_TOKENS[tok.lower()[:3]])
    return sorted(set(out))


#: "Monday through Wednesday", "Fri-Mon" — a weekday RANGE, parsed whole (R-017 #12: a range
#: read as its first day would silently truncate the schedule).
_RE_DAY_RANGE = re.compile(
    r"\b(?:(?:every|each|on|from)\s+)?(" + _DAY_WORD + r")\s*(?:-|–|to|through|thru|till|until)\s*("
    + _DAY_WORD + r")\b", re.I)


def _day_range(a: str, b: str) -> list[int]:
    lo, hi = _DAY_TOKENS[a.lower()[:3]], _DAY_TOKENS[b.lower()[:3]]
    out, d = [lo], lo
    while d != hi:
        d = (d + 1) % 7
        out.append(d)
    return sorted(out)


def find_schedule(pp: PrePass, default_tz: Optional[str], answers: dict[str, str], *,
                  tz_source: str = "deployment config") -> None:
    """``default_tz`` is the DEPLOYMENT's zone (explicit argument or `WALNUT_TIMEZONE`); when it
    is None and the sentence names no zone, the pre-pass asks rather than assuming one."""
    text = pp.text
    spans: list[tuple[int, int]] = []

    tz, tz_span = None, None
    m = _RE_IANA.search(text)
    if m:
        try:
            zone(m.group(1))
            tz, tz_span = m.group(1), m.span()
            spans.append(m.span())
        except CronError:
            if not answers.get("timezone"):
                pp.ask("timezone", f"I don't recognise the timezone {m.group(1)!r}. Which IANA "
                                   f"timezone should this run in (e.g. Europe/London)?")
    if tz is None:
        for word, zname in _TZ_WORDS.items():
            mm = re.search(r"\b" + re.escape(word) + r"\b", text, re.I)
            if mm:
                tz, tz_span = zname, mm.span()
                spans.append(mm.span())
                break

    minute, hour, dom, dow = None, None, "*", "*"
    freq_phrase = None
    needs_time = False

    for rx, kind in ((_RE_MINUTES, "minutes"), (_RE_HOURS, "hours"), (_RE_HOURLY, "hourly")):
        mm = rx.search(text)
        if mm:
            spans.append(mm.span())
            freq_phrase = mm.group(0)
            if kind == "minutes":
                n = int(mm.group(1))
                if not 1 <= n <= 59:
                    pp.ask("schedule_interval", f"'{mm.group(0)}' is not an interval I can run on "
                                                f"— pick between 1 and 59 minutes.")
                    return
                minute, hour = f"*/{n}", "*"
            elif kind == "hours":
                n = int(mm.group(1))
                if not 1 <= n <= 23:
                    pp.ask("schedule_interval", f"'{mm.group(0)}' is not an interval I can run on "
                                                f"— pick between 1 and 23 hours.")
                    return
                minute, hour = "0", f"*/{n}"
            else:
                minute, hour = "0", "*"
            # an interval plus an explicit clock time ("every 2 hours at 1pm") has a phase the
            # cron above would silently drop (R-017 #13) — ask rather than discard the time
            tm = _RE_TIME.search(text)
            explicit = tm is not None and bool(tm.group(2) or tm.group(3) or tm.group(5)
                                               or tm.group(6))
            nm = _RE_NOON.search(text)
            if explicit or nm:
                when = (tm.group(0) if explicit else nm.group(0)).strip()
                pp.ask("schedule_time", f"You said '{mm.group(0)}' and also '{when}'. I can run "
                                        f"on a fixed interval, or at a set time of day, but not "
                                        f"both in one rule — which did you mean?",
                       [mm.group(0), f"every day {when}"])
                return
            break

    if minute is None:
        mr = _RE_DAY_RANGE.search(text)
        mm = None if mr else _RE_WEEKDAYS.search(text)
        if mr:
            days = _day_range(mr.group(1), mr.group(2))
            dow, freq_phrase = ",".join(map(str, days)), mr.group(0)
            spans.append(mr.span())
        elif mm:
            dow, freq_phrase = "1-5", mm.group(0)
            spans.append(mm.span())
        else:
            mm = _RE_WEEKENDS.search(text)
            if mm:
                dow, freq_phrase = "0,6", mm.group(0)
                spans.append(mm.span())
            else:
                mm = _RE_NAMED_DAYS.search(text)
                if mm:
                    days = _parse_days(mm.group(1))
                    dow, freq_phrase = ",".join(map(str, days)), mm.group(0)
                    spans.append(mm.span())
        if dow == "*":
            mm = _RE_MONTHLY.search(text)
            md = _RE_MONTH_DAY.search(text)
            if mm or (md and re.search(r"month", md.group(0), re.I)):
                freq_phrase = (mm or md).group(0)
                spans.append((mm or md).span())
                if md:
                    spans.append(md.span())
                    d = int(md.group(1))
                    if not 1 <= d <= 31:
                        pp.ask("schedule_day", f"Day {d} is not a day of the month.")
                        return
                    dom = str(d)
                else:
                    pp.ask("schedule_day", "Which day of the month should this run on?")
                    return
            elif _RE_WEEKLY.search(text):
                mw = _RE_WEEKLY.search(text)
                spans.append(mw.span())
                pp.ask("schedule_day", "Which day of the week should this run on?",
                       ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
                        "Sunday"])
                return
            else:
                mm = _RE_DAILY.search(text)
                if mm:
                    freq_phrase = mm.group(0)
                    spans.append(mm.span())
        if freq_phrase is not None:
            needs_time = True

    # time of day
    t = _RE_TIME.search(text)
    n = _RE_NOON.search(text)
    time_found = False
    if t is not None and freq_phrase is None and not (t.group(2) or t.group(3) or t.group(5)
                                                      or t.group(6)):
        t = None                              # a bare "at 3" with no frequency is not a time
    if needs_time or (t and freq_phrase is None) or (n and freq_phrase is None):
        if n and (not t or n.start() < t.start()):
            word = n.group(1).lower()
            hour, minute = ("0" if word == "midnight" else "12"), "0"
            spans.append(n.span())
            time_found = True
        elif t:
            spans.append(t.span())
            h = int(t.group(1) or t.group(4))
            mi = int(t.group(2) or t.group(5) or 0)
            ampm = (t.group(3) or t.group(6) or "").lower().replace(".", "")
            if mi > 59 or h > 23 or (ampm and not 1 <= h <= 12):
                pp.ask("schedule_time", f"'{t.group(0).strip()}' is not a time I can read. "
                                        f"What time should it run?")
                return
            if ampm == "pm" and h != 12:
                h += 12
            elif ampm == "am" and h == 12:
                h = 0
            elif not ampm:
                zero_padded = bool(re.match(r"0\d", (t.group(1) or t.group(4) or "")))
                if 1 <= h <= 6 and not zero_padded:
                    answer = answers.get("schedule_ampm", "").lower()
                    if answer in {"am", "pm"}:
                        h = h + 12 if answer == "pm" else h
                    else:
                        pp.ask("schedule_ampm", f"Did you mean {h} in the morning or {h} in the "
                                                f"evening?", ["am", "pm"])
                        return
                elif 7 <= h <= 11 and not zero_padded:
                    pp.assumptions.append(f"read 'at {h}' as {h:02d}:{mi:02d} (morning); say "
                                          f"'{h}pm' if you meant the evening")
            hour, minute = str(h), str(mi)
            time_found = True
        if not time_found and freq_phrase is not None:
            p = _RE_PERIOD.search(freq_phrase)
            if p:
                h = _PERIOD_HOUR[p.group(1).lower()]
                hour, minute = str(h), "0"
                pp.assumptions.append(f"read '{p.group(1)}' as {h:02d}:00; name a time to change it")
                time_found = True
        if not time_found and freq_phrase is not None:
            pp.ask("schedule_time", f"You said '{freq_phrase}' — at what time should it run?")
            return
        if time_found and freq_phrase is None:
            pp.ask("schedule_frequency",
                   f"You gave a time ({int(hour):02d}:{int(minute):02d}) but not how often. "
                   f"Every day, every weekday, or on particular days?",
                   ["every day", "every weekday", "every Monday"])
            return

    if freq_phrase is None:
        return
    # the zone: the user's words, else their answer, else the deployment's configured zone —
    # never a zone written into this code (R-017 #5)
    tz_by, tz_evidence = "rule", ""
    ans = (answers.get("timezone") or "").strip()
    if tz is None or ans:
        if ans:
            try:
                zone(ans)
            except CronError:
                pp.ask("timezone", f"'{ans}' is not an IANA timezone. Which timezone should this "
                                   f"run in (e.g. Europe/London)?")
                return
            tz, tz_span, tz_by, tz_evidence = ans, None, "answer", f"timezone = {ans}"
        elif default_tz:
            tz, tz_by, tz_evidence = default_tz, "config", f"{tz_source}: {default_tz}"
        else:
            pp.ask("timezone", "Which timezone should this schedule run in? You didn't name one "
                               "and this deployment has none configured (WALNUT_TIMEZONE).",
                   ["UTC"])
            return
    if any(q.id == "timezone" for q in pp.questions):
        return
    cron = f"{minute} {hour} {dom} * {dow}"
    try:
        CronSchedule.parse(cron, tz)
    except CronError as exc:                                    # pragma: no cover - defensive
        pp.ask("schedule", f"I could not build a schedule from that ({exc}). When should it run?")
        return
    time_spans = [s for s in spans if s != tz_span]
    lo = min(s[0] for s in time_spans)
    hi = max(s[1] for s in time_spans)
    pp.schedule = ScheduleFinding(cron=cron, timezone=tz, phrase=text[lo:hi], span=(lo, hi))
    pp.derive("trigger.type", "schedule", "rule", span=(lo, hi))
    pp.derive("trigger.schedule.cron", cron, "rule", span=(lo, hi))
    pp.derive("trigger.schedule.timezone", tz, tz_by, span=tz_span, evidence=tz_evidence)


# --------------------------------------------------------------------------------------------
# WHAT KIND — adapter agent vs agent
# --------------------------------------------------------------------------------------------
_RE_ADAPTER = re.compile(
    r"\bkeep\b.{0,120}?\b(?:in|into)\s+(?:the\s+)?(?:knowledge\s+)?graph\b"
    r"|\b(?:sync|re-?sync|ingest|re-?ingest|index|crawl|import|mirror|load)\b.{0,120}?"
    r"\b(?:in|into)\s+(?:the\s+)?(?:knowledge\s+)?graph\b"
    r"|\bkeep\b.{0,120}?\b(?:up\s+to\s+date|in\s+sync|synced|fresh)\b"
    r"|\badapter\s+agent\b", re.I | re.S)
_RE_CHANGE_SYNC = re.compile(
    r"\bre-?ingest\b.{0,40}?\bchang|\b(?:on|whenever\s+(?:anything|something)|when\s+"
    r"(?:anything|something))\s+chang|\banything\s+that\s+changes\b", re.I)
_RE_FRESHNESS = re.compile(
    r"\b(?:no\s+older\s+than|at\s+most|within|fresher\s+than|freshness(?:\s+of)?|max(?:imum)?\s+age"
    r"(?:\s+of)?)\s+(\d{1,4})\s*(hours?|hrs?|h|days?|d)\b", re.I)
#: A counting or listing request — exact answers from the facts store (S2), not from retrieval.
_RE_FACTS = re.compile(
    r"\bhow\s+many\b|\bcount\s+(?:the|all|every|each|of|how)\b|\bnumber\s+of\b"
    r"|\btotal\s+(?:number|count)\b|\blist\s+(?:all|every|each)\b"
    r"|\blist\s+(?:the\s+)?[\w-]+\s+(?:that|which|with|where|whose)\b", re.I)
_RE_READ_ONLY = re.compile(
    r"\bread[\s-]?only\b|\bdon'?t\s+(?:change|write|modify|edit|touch)\s+anything\b"
    r"|\bnever\s+(?:write|change|modify|edit)\b|\bno\s+writes\b|\bonly\s+read\b", re.I)


# --------------------------------------------------------------------------------------------
# WHO ELSE — delegation to a registered adapter agent (E2E N-04)
# --------------------------------------------------------------------------------------------
_OBJ = r"(?:\s+(?:it|them|this|that|these|those|the\s+questions?|questions?))?"
#: Words that hand work to another agent. Matched only to decide that an UNREGISTERED name is
#: meant as a delegate (it then becomes a question); a registered name needs no verb.
_DELEGATE_VERB = (r"(?:delegat\w*" + _OBJ + r"|hand(?:s|ing|ed)?" + _OBJ + r"(?:\s+(?:off|over))?"
                  r"|pass(?:es|ing|ed)?" + _OBJ + r"(?:\s+(?:on|off|over))?"
                  r"|rout(?:e|es|ing|ed)" + _OBJ + r"|forward(?:s|ing|ed)?" + _OBJ
                  + r"|send(?:s|ing)?" + _OBJ + r"|ask(?:s|ing|ed)?|us(?:e|es|ing)|via|through"
                  r"|consult(?:s|ing)?)")
#: "<verb> … to/via the <name> adapter agent" — a reference to an adapter agent by name.
_RE_AA_REFERENCE = re.compile(
    r"\b" + _DELEGATE_VERB + r"\b[^.;!?\n]{0,120}?"
    r"\b(?:to|via|through|with|from|by|using|ask)\s+(?:the\s+|my\s+|our\s+|an?\s+)?"
    r"(?P<name>[\w][\w\-]*(?:\s+[\w][\w\-]*){0,4}?)\s+adapter[\s\-]+agent\b", re.I)
_RE_DELEGATE_WORD = re.compile(r"\bdelegat\w*\b", re.I)
#: "the|my|our|your|that|this <name> adapter agent" — a reference to ONE, presumably existing,
#: adapter agent (E2E run 3, P-07). Indefinite ("an adapter agent") is not a reference.
_RE_AA_DEFINITE = re.compile(
    r"\b(?:the|my|our|your|that|this)\s+(?P<name>[\w][\w\-]*(?:\s+[\w][\w\-]*){0,4}?)"
    r"(?:'s)?\s+adapter[\s\-]+agents?\b", re.I)
#: A clause that asks to MAKE an adapter agent: a definite reference inside it is the thing to
#: build, not a delegate.
_RE_AA_CREATE = re.compile(r"\b(?:create|build|make|set\s*up|add|save|define|new|register)\b",
                           re.I)
#: Words that describe every adapter agent and so tell none apart.
_GENERIC_AA_WORDS = frozenset({"adapter", "agent", "agents", "graph", "knowledge", "keep", "sync",
                               "and", "the", "for", "with", "into"})


def _clause_before(text: str, at: int) -> str:
    start = max(text.rfind(c, 0, at) for c in ".;!?\n")
    return text[start + 1:at]


def _capability_words(agent, catalogue: CatalogueLike) -> set[str]:
    """What a registered adapter agent DECLARES, as words: its id, its title, and each of its
    connections' id, title and aliases (read from the catalogue). Nothing here is recalled."""
    phrases = [agent.id, agent.title, *agent.connections]
    for conn in getattr(catalogue, "connections", ()) or ():
        if conn.id in agent.connections:
            phrases += [conn.title or "", *(getattr(conn, "aliases", ()) or ())]
    words: set[str] = set()
    for ph in phrases:
        words |= {w.lower() for w in re.split(r"[^A-Za-z0-9]+", ph or "") if len(w) >= 3}
    return words - _GENERIC_AA_WORDS
_NOT_A_NAME = {"the", "my", "our", "a", "an", "this", "that", "to", "via", "through", "with",
               "from", "by", "using", "it", "them", "adapter", "new", "another", "other", "same"}


def _mask(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for a, b in spans:
        for i in range(a, b):
            chars[i] = " "
    return "".join(chars)


def find_delegates(pp: PrePass, catalogue: CatalogueLike, answers: dict[str, str]) -> None:
    """Which REGISTERED adapter agents the request delegates to (E2E N-04).

    * A delegable adapter agent named in the words (id, spaced id or title) is a delegate. A
      request cannot create an adapter agent that already exists, so the reference never makes
      the request an adapter agent; its span (with a trailing "adapter agent") is recorded.
    * "… to the <name> adapter agent" after a delegation word, where <name> is NOT registered, is
      a question (``delegate:<slug>``) whose options are the registered ones — never a new adapter
      agent and never a guess. A bare "delegate" with no target asks ``delegate``.
    * An answer is a registered, delegable adapter agent id (or several, comma-separated);
      ``none`` drops the delegation."""
    text = pp.text
    offered = adapter_agents_of(catalogue)
    taken: list[tuple[int, int]] = []
    names = sorted(((n, a.id) for a in offered for n in a.names()), key=lambda kv: -len(kv[0]))
    for phrase, aid in names:
        for m in _word_re(phrase).finditer(text):
            if any(_overlaps(m.span(), t) for t in taken):
                continue
            tail = re.match(r"(?:'s)?\s+(?:adapter[\s\-]+)?agents?\b", text[m.end():], re.I)
            span = (m.start(), m.end() + (tail.end() if tail else 0))
            taken.append(span)
            pp.delegate_spans.append(span)
            if aid not in pp.delegates:
                pp.delegates.append(aid)
                pp.derive(f"delegates[{aid}]", aid, "rule", span=m.span(),
                          evidence=f"{m.group(0)} — a registered adapter agent")

    def answer_ids(qid: str) -> Optional[list[str]]:
        raw = (answers.get(qid) or "").strip()
        if not raw:
            return None
        if raw.lower() in ("none", "no", "nobody"):
            return []
        ids = [x for x in re.split(r"[,\s]+", raw) if x]
        if ids and all(adapter_agent_of(catalogue, x) is not None for x in ids):
            return ids
        return None                                       # not a registered one: still open

    def adopt(ids: list[str], qid: str) -> None:
        for aid in ids:
            if aid not in pp.delegates:
                pp.delegates.append(aid)
            pp.derive(f"delegates[{aid}]", aid, "answer", evidence=f"{qid} = {answers[qid]}")

    options = [a.id for a in offered]
    for m in _RE_AA_REFERENCE.finditer(text):
        span = (m.start("name"), m.end())
        if any(_overlaps(span, t) for t in taken):
            continue
        words = [w for w in m.group("name").split() if w.lower() not in _NOT_A_NAME]
        if not words:
            continue
        name = " ".join(words)
        taken.append(span)
        pp.delegate_spans.append(span)
        qid = f"delegate:{slug(name)}"
        ids = answer_ids(qid)
        if ids is not None:
            adopt(ids, qid)
            continue
        pp.ask(qid, (f"'{name}' is not a registered adapter agent. Which adapter agent should "
                     f"this delegate to? Answer 'none' to read the sources directly."
                     if options else
                     f"'{name}' is not a registered adapter agent, and none is registered yet. "
                     f"Save the adapter agent first (e.g. 'Keep <source> in the graph'), or "
                     f"answer 'none' to read the sources directly."), options)
    # E2E run 3, P-07: a DEFINITE reference to an adapter agent ("have the Larkspur adapter agent
    # pull together …") that names no registered one exactly and carries no delegation verb was
    # read as a request to BUILD an adapter agent. It is matched against what the registered ones
    # DECLARE (`_capability_words`) and, when it overlaps any, becomes a `delegate:<slug>`
    # question offering them, best match first. Never a silent pick: the user answers. A clause
    # that asks to create one is untouched, and so is a reference that matches nothing declared.
    for m in _RE_AA_DEFINITE.finditer(text):
        span = (m.start("name"), m.end())
        if not offered or any(_overlaps(span, t) for t in taken):
            continue
        if _RE_AA_CREATE.search(_clause_before(text, m.start())):
            continue
        words = [w for w in re.split(r"[\s\-_]+", m.group("name"))
                 if w and w.lower() not in _NOT_A_NAME]
        if not words:
            continue
        asked = {w.lower() for w in words}
        ranked = sorted(((len(asked & _capability_words(a, catalogue)), a.id) for a in offered),
                        key=lambda kv: (-kv[0], kv[1]))
        matched = [aid for n, aid in ranked if n > 0]
        if not matched:
            continue
        name = " ".join(words)
        taken.append(span)
        pp.delegate_spans.append(span)
        qid = f"delegate:{slug(name)}"
        ids = answer_ids(qid)
        if ids is not None:
            adopt(ids, qid)
            continue
        pp.ask(qid, (f"'{name} adapter agent' is not a registered name, but it matches what "
                     f"{', '.join(matched)} declares. Should this delegate to it? Answer with "
                     f"the adapter agent's id, or 'none' to read the sources directly."),
               matched + [aid for aid in options if aid not in matched])
    declined = any((k == "delegate" or k.startswith("delegate:"))
                   and str(v).strip().lower() in ("none", "no", "nobody")
                   for k, v in answers.items())
    if _RE_DELEGATE_WORD.search(text) and not pp.delegates and not declined \
            and not any(q.id.startswith("delegate:") for q in pp.questions):
        ids = answer_ids("delegate")
        if ids:
            adopt(ids, "delegate")
        else:
            pp.ask("delegate", ("Which adapter agent should it delegate these questions to? "
                                "Answer 'none' to read the sources directly.") if options else
                   ("Nothing to delegate to: no adapter agent is registered. Answer 'none' to "
                    "read the sources directly, or save an adapter agent first."), options)


def add_delegate_sources(pp: PrePass, catalogue: CatalogueLike) -> None:
    """A delegating agent's declared ``sources`` bound what its delegates may read (the CALLER's
    admission, `ToolLayerAdmission`). When the words name no source, the delegates' own
    connections are the sources — from the registry, recorded as such."""
    if pp.sources or not pp.delegates:
        return
    for aid in pp.delegates:
        aa = adapter_agent_of(catalogue, aid)
        for cid in (aa.connections if aa else ()):
            conn = connection_of(catalogue, cid)
            if conn is None or conn.agent_write or cid in pp.sources:
                continue
            pp.sources.append(cid)
            pp.derive(f"sources[{cid}]", cid, "catalogue",
                      evidence=f"a connection of adapter agent {aid}, the delegate the request "
                               f"names")


# --------------------------------------------------------------------------------------------
# WHICH SOURCES — connections, source-type words, folders, unknown systems
# --------------------------------------------------------------------------------------------
#: Names of systems a user may mention that must resolve to a connection or become a question.
_SYSTEM_NOUNS = ("crm", "erp", "google drive", "drive", "dropbox", "sharepoint", "onedrive",
                 "slack", "gmail", "outlook", "inbox", "mailbox", "salesforce", "hubspot",
                 "notion", "jira", "confluence", "s3", "bucket", "spreadsheet", "google sheet",
                 "sheet", "calendar", "database", "db", "teams", "zendesk", "airtable")
_FOLDER_RE = re.compile(r"\b(folder|directory)\b", re.I)
_TOKEN_RE = re.compile(r"[\w#&'][\w#&'\-]*")


def _kind_word_index() -> list[tuple[str, str]]:
    out = []
    for kind, words in KIND_WORDS.items():
        for w in words:
            out.append((w, kind))
    return sorted(out, key=lambda kv: -len(kv[0]))


def find_mentions(pp: PrePass, catalogue: CatalogueLike) -> None:
    text = pp.text
    conns = list(catalogue.connections)
    taken: list[tuple[int, int]] = []

    # 1. connection names and aliases (longest first)
    names: list[tuple[str, str]] = sorted(
        ((n, c.id) for c in conns for n in c.names()), key=lambda kv: -len(kv[0]))
    for phrase, cid in names:
        for m in _word_re(phrase).finditer(text):
            if any(_overlaps(m.span(), s) for s in taken):
                # an adjacent alias of the SAME connection extends the mention ("CRM database")
                continue
            taken.append(m.span())
            pp.mentions.append(Mention(m.start(), m.end(), m.group(0), [cid]))

    # 2. source-type words
    for word, kind in _kind_word_index():
        for m in _word_re(word).finditer(text):
            if any(_overlaps(m.span(), s) for s in taken):
                continue
            taken.append(m.span())
            cands = [c.id for c in conns if c.kind == kind and not c.agent_write]
            pp.mentions.append(Mention(m.start(), m.end(), m.group(0), cands))

    # 3. folders
    tokens = [(m.group(0), m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]
    for fm in _FOLDER_RE.finditer(text):
        idx = next((i for i, t in enumerate(tokens) if t[1] == fm.start()), None)
        if idx is None:
            continue
        name_toks: list[tuple[str, int, int]] = []
        j = idx - 1
        while j >= 0 and len(name_toks) < 4:
            word, s, e = tokens[j]
            between = text[e:(name_toks[0][1] if name_toks else fm.start())]
            if re.search(r"[,.;:!?]", between) or word.lower() in _STOP:
                break
            name_toks.insert(0, tokens[j])
            j -= 1
        qualifiers: set[str] = set()
        qualified = False
        rest: list[tuple[str, int, int]] = []
        for tok in name_toks:
            owner = next((mt for mt in pp.mentions if mt.start <= tok[1] and tok[2] <= mt.end), None)
            if owner is not None and not rest:
                qualified = True
                qualifiers.update(owner.connections)
            else:
                rest.append(tok)
        tail = re.match(r"\s+(?:in|on|of|inside|within)\s+(?:the\s+|my\s+|our\s+)?", text[fm.end():])
        if tail:
            after = fm.end() + tail.end()
            owner = next((mt for mt in pp.mentions if mt.start == after), None)
            if owner is not None:
                qualified = True
                qualifiers.update(owner.connections)
        if not rest:
            continue                          # "the Larkspur files folder" names a connection
        folder = text[rest[0][1]:rest[-1][2]]
        span = (rest[0][1], fm.end())
        pool = [c for c in conns if (not qualified or c.id in qualifiers)]
        hits: dict[str, list[str]] = {}
        for c in pool:
            for loc in c.locations:
                segs = [s for s in re.split(r"[\\/]", loc) if s]
                if segs and norm(segs[-1]) == norm(folder):
                    hits.setdefault(c.id, []).append(loc)
        where = (" in " + ", ".join(sorted(qualifiers))) if qualified and qualifiers else ""
        if not hits:
            known = sorted({(c.id, loc) for c in pool for loc in c.locations})
            pp.ask(f"folder:{folder}",
                   f"I couldn't find a folder called '{folder}'{where or ' in any connected source'}. "
                   f"Which source and folder did you mean?",
                   [f"{cid}:{loc}" for cid, loc in known][:20])
            pp.mentions.append(Mention(span[0], span[1], text[span[0]:span[1]], [], folder=folder))
            continue
        if len(hits) > 1:
            pp.ask(f"folder:{folder}",
                   f"'{folder}' exists in more than one source ({', '.join(sorted(hits))}). "
                   f"Which one did you mean?", [f"{cid}:{hits[cid][0]}" for cid in sorted(hits)])
            pp.mentions.append(Mention(span[0], span[1], text[span[0]:span[1]], sorted(hits),
                                       folder=folder))
            continue
        (cid, locs), = hits.items()
        if len(set(locs)) > 1:
            # two folders of one source share the name (Legal/Contracts, Sales/Contracts): which
            # one is the user's to say — picking the shortest would be a guess (R-017 #5)
            pp.ask(f"folder:{folder}",
                   f"'{folder}' matches {len(set(locs))} folders in {cid} "
                   f"({', '.join(sorted(set(locs)))}). Which one did you mean?",
                   [f"{cid}:{loc}" for loc in sorted(set(locs))])
            pp.mentions.append(Mention(span[0], span[1], text[span[0]:span[1]], [cid],
                                       folder=folder))
            continue
        loc = locs[0]
        pp.mentions.append(Mention(span[0], span[1], text[span[0]:span[1]], [cid], location=loc,
                                   folder=folder))
        # the folder mention subsumes any qualifier mention inside it
        pp.mentions = [mt for mt in pp.mentions if not (
            mt.folder is None and span[0] <= mt.start and mt.end <= span[1] and mt.connections
            and set(mt.connections) <= {cid} | qualifiers)]
        taken.append(span)

    # 4. resolve type-word ambiguity, and systems nobody connected
    for mt in list(pp.mentions):
        if mt.folder is not None:
            continue
        if not mt.connections:
            pp.ask(f"source:{norm(mt.phrase)}",
                   f"You mentioned '{mt.phrase}', but no connected source is of that type. "
                   f"Connect it first, or tell me which source you meant.",
                   [c.id for c in conns])
        elif len(mt.connections) > 1:
            pp.ask(f"source:{norm(mt.phrase)}",
                   f"'{mt.phrase}' could mean {', '.join(mt.connections)}. Which one?",
                   list(mt.connections))
    all_spans = [(mt.start, mt.end) for mt in pp.mentions]
    for noun in sorted(_SYSTEM_NOUNS, key=len, reverse=True):
        for m in _word_re(noun).finditer(text):
            if any(_overlaps(m.span(), s) for s in all_spans):
                continue
            all_spans.append(m.span())
            pp.ask(f"source:{norm(m.group(0))}",
                   f"You mentioned '{m.group(0)}', but no connected source is called that. "
                   f"Connect it first, or tell me which source you meant.",
                   [c.id for c in conns])

    pp.mentions.sort(key=lambda mt: mt.start)
    for mt in pp.mentions:
        if len(mt.connections) == 1 and mt.connections[0] not in pp.sources:
            pp.sources.append(mt.connections[0])
            pp.derive(f"sources[{mt.connections[0]}]", mt.connections[0], "rule",
                      span=(mt.start, mt.end))


def apply_answers_to_mentions(pp: PrePass, catalogue: CatalogueLike, answers: dict[str, str]
                              ) -> None:
    """A user's answer to a folder/source question replaces the question with a resolution."""
    for q in list(pp.questions):
        ans = answers.get(q.id)
        if not ans:
            continue
        cid, _, loc = ans.partition(":")
        conn = connection_of(catalogue, cid)
        if conn is None:
            continue                          # an answer naming a non-existent source stays open
        if q.id.startswith("folder:") and loc and loc not in conn.locations:
            continue
        pp.questions.remove(q)
        key = q.id.split(":", 1)[1]
        for mt in pp.mentions:
            if (q.id.startswith("folder:") and mt.folder == key) or \
                    (q.id.startswith("source:") and norm(mt.phrase) == key):
                mt.connections = [cid]
                if loc:
                    mt.location = loc
        if cid not in pp.sources:
            pp.sources.append(cid)
        pp.derive(f"sources[{cid}]", cid, "answer", evidence=f"{q.id} = {ans}")


# --------------------------------------------------------------------------------------------
# WHEN — events
# --------------------------------------------------------------------------------------------
_ADDED = r"lands|landed|appears|arrives|shows\s+up|comes\s+in|(?:is|gets|was)\s+(?:added|uploaded|created|dropped|saved|put|placed)"
_CHANGED = r"changes|(?:is|gets|was)\s+(?:changed|updated|modified|edited|revised)"
_REMOVED = r"disappears|(?:is|gets|was)\s+(?:deleted|removed)"
_RUN = r"fails|failed|finishes|finished|completes|completed|is\s+done"
_RE_EVENT = re.compile(
    r"\b(?:when(?:ever)?|each\s+time|every\s+time|as\s+soon\s+as|once|if)\s+"
    r"(?:(?:a|an|any|some|the)\s+)?(?P<new>new\s+)?(?P<thing>[\w\-' ]{1,40}?)\s+"
    r"(?P<verb>" + _ADDED + "|" + _CHANGED + "|" + _REMOVED + "|" + _RUN + r")"
    r"(?P<more>\s+or\s+(?:is\s+|gets\s+)?(?:changed|updated|modified|edited|added|removed|deleted))?"
    r"(?P<rest>[^.;,]*)", re.I)


def _event_types(verb: str, more: str, thing: str) -> list[str]:
    types = []
    for part in (verb, more or ""):
        p = part.lower()
        if not p:
            continue
        if re.search(_RUN, p):
            types.append("run_failed" if "fail" in p else "run_completed")
        elif re.search(_REMOVED + r"|removed|deleted", p):
            types.append("item_removed")
        elif re.search(_CHANGED + r"|changed|updated|modified|edited", p):
            types.append("item_changed")
        else:
            types.append("item_added")
    return sorted(set(types))


def find_event(pp: PrePass, catalogue: CatalogueLike,
               answers: Optional[dict[str, str]] = None) -> None:
    answers = answers or {}
    m = _RE_EVENT.search(pp.text)
    if not m:
        return
    thing = m.group("thing").strip()
    types = _event_types(m.group("verb"), m.group("more") or "", thing)
    if any(t.startswith("run_") for t in types) and not re.search(
            r"\b(?:sync|run|ingest|crawl|import|refresh)\w*", thing, re.I):
        return                                  # "if the payment fails" is not an ingestion event
    ev = EventFinding(types=types, thing=thing, phrase=m.group(0).strip(), span=m.span())
    rest_start = m.start("rest")
    in_rest = [mt for mt in pp.mentions if rest_start <= mt.start < m.end()]
    in_thing = [mt for mt in pp.mentions if m.start("thing") <= mt.start < m.end("thing")]
    target = (in_rest or in_thing)
    if not target:
        # even with ONE connection this is asked: being the only source is not the user naming
        # it, and a second connection later would silently change nothing here (R-017 #5)
        answer = answers.get("event_source", "")
        if connection_of(catalogue, answer) is not None:
            ev.source = answer
            pp.derive("trigger.event.source", answer, "answer",
                      evidence=f"event_source = {answer}")
        else:
            pp.ask("event_source", f"'{ev.phrase}' — in which connected source should I watch "
                                   f"for this?", [c.id for c in catalogue.connections])
    else:
        mt = target[0]
        if len(mt.connections) == 1:
            ev.source = mt.connections[0]
            pp.derive("trigger.event.source", ev.source, "rule", span=(mt.start, mt.end))
            if mt.location:
                ev.uri_glob = f"**/{mt.location.strip('/')}/**"
                pp.derive("trigger.event.filter.uri_glob", ev.uri_glob, "rule",
                          span=(mt.start, mt.end), evidence=f"folder {mt.location} of {ev.source}")
    pp.derive("trigger.type", "event", "rule", span=m.span())
    pp.derive("trigger.event.on", types, "rule", span=m.span("verb"))
    kind = next((k for k in catalogue.kinds if stem(k) == stem(thing.split()[-1])), None) \
        if thing else None
    if kind:
        pp.kinds_mentioned.append(kind)
    elif thing and stem(thing.split()[-1]) not in {"file", "document", "item", "thing", "row",
                                                   "record", "doc", "sync", "run", "chang"}:
        pp.assumptions.append(f"'{thing}' is not an ontology kind, so the trigger fires on every "
                              f"matching item, whatever it contains")
    pp.event = ev
    if ev.source and ev.source not in pp.sources:
        pp.sources.append(ev.source)


# --------------------------------------------------------------------------------------------
# WHAT IT WRITES — actions and approval
# --------------------------------------------------------------------------------------------
#: write verb -> the tool-name words that can carry it (data, used to pick a connection's tool)
WRITE_VERBS: dict[str, tuple[str, ...]] = {
    "draft": ("write", "create", "save", "upload", "put"),
    "write": ("write", "create", "save", "upload", "put"),
    "save": ("write", "create", "save", "upload", "put"),
    "create": ("create", "write", "insert", "add"),
    "store": ("write", "save", "upload", "put", "insert"),
    "upload": ("upload", "write", "put"),
    "log": ("insert", "create", "add", "append", "write", "log"),
    "record": ("insert", "create", "add", "append", "write"),
    "insert": ("insert", "add", "create", "append"),
    "add": ("insert", "add", "create", "append", "write"),
    "append": ("append", "insert", "write"),
    "update": ("update", "edit", "patch", "set", "write"),
    "edit": ("edit", "update", "patch", "write"),
    "mark": ("update", "set", "patch", "edit"),
    "delete": ("delete", "remove"),
    "remove": ("remove", "delete"),
    "move": ("move", "rename"),
    "rename": ("rename", "move"),
    "send": ("send", "post"),
    "email": ("send", "post"),
    "post": ("post", "send"),
    "file": ("write", "create", "save", "upload", "put"),
    # verbs that change a system but were missing (R-017 #6): without them "publish the summary"
    # compiled as a read-only agent. Their tool words mirror `catalogue.WRITE_VERB_EXTRA`.
    "publish": ("publish", "post"),
    "submit": ("submit", "post", "send"),
    "approve": ("approve",),
    "reject": ("reject",),
    "archive": ("archive", "move"),
    "share": ("share",),
    "assign": ("assign", "update", "set"),
    "cancel": ("cancel", "delete"),
    "close": ("close", "update"),
    "merge": ("merge",),
    "invite": ("invite",),
    "restore": ("restore",),
    "reply": ("reply", "send", "post"),
    "forward": ("forward", "send"),
    "set": ("set", "update", "patch"),
}
_LEAD = r"(?:(?:and|then|also|please|and\s+then|finally)\s+)*"
_RE_CLAUSE_SPLIT = re.compile(r"[.;:!?\n]+|,|\s+and\s+then\s+|\s+then\s+", re.I)
_PREPS = {"into", "in", "to", "on", "onto", "under", "inside", "within", "at", "with", "for",
          "about", "from", "as"}

_RE_REQUIRED_NEG = re.compile(
    r"\bnothing\s+without\s+(?:my\s+)?(?:approval|asking)\b"
    r"|\bdon'?t\s+\w+(?:\s+\w+)?\s+without\s+(?:asking|my\s+approval)\b", re.I)
_RE_REQUIRED = re.compile(
    r"(?<!need to )(?<!have to )\bask(?:ing)?\s+(?:me|us)(?:\s+first)?\b|\bcheck\s+with\s+(?:me|us)\b"
    r"|\bwait\s+for\s+(?:my|our)\s+(?:ok|okay|approval|go-?ahead|sign-?off)\b"
    r"|\b(?:needs?|requires?|with|get|after)\s+(?:my|our)\s+(?:approval|sign-?off|ok|okay)\b"
    r"|\b(?:let\s+me|i\s+want\s+to|i'?ll|i\s+will|i\s+need\s+to|i\s+must)\s+(?:approve|review|confirm|sign\s+off)\b"
    r"|\bconfirm\s+with\s+(?:me|us)\b|\bonly\s+(?:after|once|when)\s+i\s+(?:approve|confirm|say)\b"
    r"|\bfor\s+(?:my|our)\s+approval\b", re.I)
_RE_NOTIFIED = re.compile(
    r"\b(?:just\s+)?tell\s+(?:me|us)\b|\blet\s+(?:me|us)\s+know\b|\bnotify\s+(?:me|us)\b"
    r"|\binform\s+(?:me|us)\b|\bkeep\s+(?:me|us)\s+posted\b|\breport\s+back\b", re.I)
_RE_AUTONOMOUS = re.compile(
    r"\bjust\s+do\s+it\b|\bno\s+need\s+to\s+(?:ask|check)\b"
    r"|\bdon'?t\s+(?:need\s+to\s+)?(?:ask|check\s+with)\s+(?:me|us)\b|\bwithout\s+asking\b"
    r"|\bwithout\s+(?:my\s+|any\s+)?approval\b|\bautomatically\b|\bon\s+(?:its|your)\s+own\b"
    r"|\bno\s+approval\s+(?:needed|required)\b|\bdon'?t\s+bother\s+(?:asking|me)\b", re.I)
_RE_REVERSAL = re.compile(
    r"\b(?:to\s+undo(?:\s+(?:it|this|that))?,?|undo\s+(?:it\s+|this\s+)?by|reverse\s+(?:it\s+|this\s+)?by"
    r"|reversal(?:\s+is)?:?|if\s+(?:it'?s\s+|it\s+is\s+)?wrong,?)\s+(?P<rev>[^.;]+)", re.I)
_GLOBAL_WORDS = {"anyth", "everyth", "any", "all", "writ", "change", "chang", "action", "it", "that",
                 "thi"}
#: Residue words that do not name anything: an approval phrase made only of these is a blanket rule.
_BLANKET_WORDS = ("about", "it", "them", "this", "that", "these", "those", "everything",
                  "anything", "all", "each", "every", "both", "done", "afterwards", "afterward",
                  "time", "please", "always", "write", "writes", "change", "changes", "action",
                  "actions", "step", "steps", "thing", "things", "happen", "happens", "run",
                  "runs", "go", "ahead", "do", "does")
_MODE_WORDS = {"human_required": "wait for your approval",
               "human_notified": "go ahead, then tell you",
               "autonomous": "go ahead on its own"}


_OPENERS = {"(": ")", "[": "]", "{": "}"}


def _bracketed(text: str) -> list[tuple[int, int]]:
    """The (open, close) index pairs of every BALANCED bracket in ``text``. A clause separator
    inside one — "(table follow_up_tasks: customer_id, title)" — does not end the clause (E2E
    F-32: the description was cut at the colon). An unclosed bracket protects nothing."""
    pairs, stack = [], []
    for i, ch in enumerate(text):
        if ch in _OPENERS:
            stack.append((ch, i))
        elif stack and ch == _OPENERS[stack[-1][0]]:
            pairs.append((stack.pop()[1], i))
        elif ch in _OPENERS.values():
            stack = []                          # mismatched: stop protecting
    return pairs


def _inside(pos: int, pairs: list[tuple[int, int]]) -> bool:
    return any(a < pos < b for a, b in pairs)


def _clauses(text: str) -> list[tuple[str, int, int]]:
    out = []
    pos = 0
    guarded = _bracketed(text)
    splits = [m for m in _RE_CLAUSE_SPLIT.finditer(text) if not _inside(m.start(), guarded)]
    for m in splits + [None]:
        end = m.start() if m else len(text)
        seg = text[pos:end]
        stripped = seg.strip()
        if stripped:
            s = pos + seg.index(stripped)
            out.append((stripped, s, s + len(stripped)))
        if m:
            pos = m.end()
    # split "draft X and log Y" where the second half starts with a write verb
    final = []
    verb_alt = "|".join(sorted(WRITE_VERBS, key=len, reverse=True))
    rx = re.compile(r"\s+and\s+(?=(?:" + verb_alt + r")\b)", re.I)
    for c, s, e in out:
        pos = 0
        inner = _bracketed(c)
        for m in [m for m in rx.finditer(c) if not _inside(m.start(), inner)] + [None]:
            end = m.start() if m else len(c)
            part = c[pos:end].strip()
            if part:
                ps = s + c.index(part, pos)
                final.append((part, ps, ps + len(part)))
            if m:
                pos = m.end()
    return final


def _approval_modes(text: str) -> list[tuple[str, tuple[int, int]]]:
    found: list[tuple[str, tuple[int, int]]] = []
    for mode, rx in (("human_required", _RE_REQUIRED_NEG), ("autonomous", _RE_AUTONOMOUS),
                     ("human_required", _RE_REQUIRED), ("human_notified", _RE_NOTIFIED)):
        for m in rx.finditer(text):
            if any(_overlaps(m.span(), s) for _, s in found):
                continue
            found.append((mode, m.span()))
    return sorted(found, key=lambda f: f[1][0])


def _route_to_writer(pp: PrePass, act: ActionFinding, catalogue: CatalogueLike,
                     answers: dict[str, str], clause: str) -> None:
    """A write aimed at a connection that is read through one identity and written through
    another (S2: `larkspur_db` reads, `larkspur_db_writer` INSERTs) goes to the WRITE connection.

    Declared data decides: an ``agent_write`` connection whose `writes_for` names the target is
    the route. When none declares it but write-only connections exist and the target itself has
    no write tool, which one to use is the user's to say — asked, never picked."""
    target = connection_of(catalogue, act.connection or "")
    if target is None or target.agent_write:
        return
    declared = writers_for(catalogue, target.id)
    answer = answers.get(f"writer:{act.name}", "")
    chosen = connection_of(catalogue, answer)
    if chosen is not None and chosen.agent_write:
        act.connection = chosen.id
        pp.derive(f"actions[{act.name}].connection", chosen.id, "answer",
                  evidence=f"writer:{act.name} = {answer}")
        return
    if len(declared) == 1:
        act.connection = declared[0].id
        pp.derive(f"actions[{act.name}].connection", declared[0].id, "rule",
                  span=act.span, evidence=f"{declared[0].id} declares writes_for {target.id}")
        return
    undeclared = [c for c in catalogue.connections if c.agent_write]
    if len(declared) > 1 or (not target.write_tools and undeclared):
        opts = [c.id for c in (declared or undeclared)]
        pp.ask(f"writer:{act.name}", f"'{clause}' writes to {target.id}, which is written through "
                                     f"a separate write-only connection. Which one?", opts)
        act.connection = None


def find_actions(pp: PrePass, catalogue: CatalogueLike, answers: dict[str, str]) -> None:
    text = pp.text
    approvals = _approval_modes(text)
    verb_alt = "|".join(sorted(WRITE_VERBS, key=len, reverse=True))
    head = re.compile(r"^" + _LEAD + r"(?P<verb>" + verb_alt + r")\s+(?P<body>.+)$", re.I | re.S)
    approval_clauses: list[tuple[str, int, int]] = []
    inline_modes: dict[str, set[str]] = {}
    mode_spans: dict[str, list[tuple[int, int]]] = {}
    used_names: set[str] = set()
    rev_span = _RE_REVERSAL.search(text)
    for clause, s, e in _clauses(text):
        if rev_span and rev_span.start() <= s < rev_span.end():
            continue                          # "to undo, move them back" describes a reversal
        m = head.match(clause)
        in_clause = [mode for mode, span in approvals if s <= span[0] < e]
        if not m:
            if in_clause:
                approval_clauses.append((clause, s, e))
            continue
        verb = m.group("verb").lower()
        body = m.group("body")
        words = []
        for w in re.findall(r"[\w'\-]+", body):
            if w.lower() in _PREPS:
                break
            words.append(w)
        obj_words = [w for w in words if w.lower() not in _DETERMINERS]
        obj = " ".join(obj_words) or "item"
        name = slug(f"{verb} {' '.join(obj_words[-3:])}", max_len=40)
        base, i = name, 2
        while name in used_names:
            name, i = f"{base}_{i}", i + 1
        used_names.add(name)
        shown = re.sub(r"^(?:(?:and|then|also|finally)\s+)+", "", clause, flags=re.I)
        act = ActionFinding(verb=verb, obj=obj, clause=shown, span=(s, e), name=name)
        targets = [mt for mt in pp.mentions if s <= mt.start < e]
        resolved = [mt for mt in targets if len(mt.connections) == 1]
        if resolved:
            mt = resolved[0]
            act.connection = mt.connections[0]
            act.location = mt.location
            _route_to_writer(pp, act, catalogue, answers, clause)
        elif not targets:
            # the user's answer to this very question (Codex batch 2 #5): `<connection>` or
            # `<connection>:<folder>`, checked against the catalogue — never a guess
            ans = (answers.get(f"target:{name}") or "").strip()
            cid, _, loc = ans.partition(":")
            conn_a = connection_of(catalogue, cid.strip()) if ans else None
            if conn_a is not None and (not loc or loc.strip() in conn_a.locations):
                act.connection = conn_a.id
                act.location = loc.strip() or None
                pp.derive(f"actions[{name}].connection", conn_a.id, "answer",
                          evidence=f"target:{name} = {ans}")
                if conn_a.id not in pp.sources:
                    pp.sources.append(conn_a.id)
                _route_to_writer(pp, act, catalogue, answers, clause)
            else:
                pp.ask(f"target:{name}", f"'{clause}' — where should this be written? Name the "
                                         f"connected source (and folder).",
                       [c.id for c in catalogue.connections if c.write_tools])
        # tool on the target
        if act.connection:
            conn = connection_of(catalogue, act.connection)
            assert conn is not None
            writes = list(conn.write_tools)
            fam = WRITE_VERBS.get(verb, ())
            fitting = [t for t in writes if any(re.search(r"(?:^|[_\-.])" + f + r"(?:[_\-.]|$)",
                                                          t.name, re.I) for f in fam)]
            chosen = None
            answer = answers.get(f"tool:{name}")
            if answer and answer in {t.name for t in writes}:     # classified writes (N-21)
                chosen = next(t for t in writes if t.name == answer)
            elif len(fitting) == 1:
                chosen = fitting[0]
            elif conn.agent_write and len(writes) == 1:
                chosen = writes[0]          # a write-only connection's one declared write path
            if chosen is not None:
                act.tool = f"source.write:{conn.id}:{chosen.name}"
                if conn.agent_write and chosen not in fitting and chosen.name != answer:
                    pp.derive(f"actions[{name}].tool", act.tool, "rule", span=(s, e),
                              evidence=f"{chosen.name} is the only tool of the write-only "
                                       f"connection {conn.id}")
                elif answer and chosen.name == answer:
                    pp.derive(f"actions[{name}].tool", act.tool, "answer",
                              evidence=f"tool:{name} = {answer}")
                else:
                    pp.derive(f"actions[{name}].tool", act.tool, "rule", span=(s, e),
                              evidence=f"verb '{verb}' -> tool words {list(fam)}; "
                                       f"{chosen.name} is the only write tool of {conn.id} "
                                       f"carrying one")
            elif not writes:
                pp.ask(f"tool:{name}", f"'{clause}' needs to write to {conn.id}, but that "
                                       f"connection offers no write tool. Connect a write-capable "
                                       f"server for it, or drop this step.")
            else:
                pp.ask(f"tool:{name}", f"'{clause}' — which of {conn.id}'s write tools should "
                                       f"do this?", [t.name for t in (fitting or writes)])
        # subject check: an action whose words name a subject kind is an identity-touching write
        subj = [k for k in catalogue.subject_kinds
                if re.search(r"\b" + re.escape(k) + r"s?\b", clause, re.I)]
        act.touches_subject = bool(subj)
        if in_clause:
            inline_modes[name] = set(in_clause)
            mode_spans[name] = [span for _, span in approvals if s <= span[0] < e]
        pp.actions.append(act)

    if not pp.actions:
        return

    # approval: each approval clause assigns a mode to the actions it names, or to all
    assigned: dict[str, set[str]] = {a.name: set(inline_modes.get(a.name, ())) for a in pp.actions}
    global_modes: set[str] = set()
    global_spans: list[tuple[int, int]] = []
    for clause, s, e in approval_clauses:
        modes = [mode for mode, span in approvals if s <= span[0] < e]
        mode = modes[0]
        phrase_spans = [span for _, span in approvals if s <= span[0] < e]
        residue = text[s:e]
        for ps in sorted(phrase_spans, reverse=True):
            residue = residue[:ps[0] - s] + " " + residue[ps[1] - s:]
        rwords = {stem(w) for w in re.findall(r"[a-zA-Z]+", residue)} - {stem(w) for w in _STOP}
        rwords -= {"", "me", "us", "just", "first", "after", "befor", "when", "once"}
        hit = False
        for a in pp.actions:
            keys = {stem(a.verb)} | {stem(w) for w in re.findall(r"[a-zA-Z]+", a.obj)}
            if a.connection:
                keys |= {stem(w) for w in re.findall(r"[a-zA-Z]+", a.connection)}
            if rwords & keys:
                assigned[a.name].add(mode)
                mode_spans.setdefault(a.name, []).extend(phrase_spans)
                hit = True
        # A phrase that names no action is a blanket rule ONLY when it has nothing else to say
        # ("Tell me after."). One with its own referent that matches no action ("tell me about
        # the latter") is not promoted to every action — each unmatched action is then asked
        # (R-017 #10); promoting it could weaken a destructive action's approval.
        referent = rwords - {stem(w) for w in _BLANKET_WORDS}
        if not hit and (not referent or len(pp.actions) == 1):
            global_modes.add(mode)
            global_spans.extend(phrase_spans)

    for a in pp.actions:
        modes = assigned[a.name] or global_modes
        ans = answers.get(f"mode:{a.name}")
        if ans in _MODE_WORDS:
            modes = {ans}
        if len(modes) == 1:
            a.automation_mode = next(iter(modes))
            if ans in _MODE_WORDS:
                pp.derive(f"actions[{a.name}].automation_mode", ans, "answer",
                          evidence=f"mode:{a.name} = {ans}")
            else:
                sp = (mode_spans.get(a.name) if assigned[a.name] else global_spans) or []
                pp.derive(f"actions[{a.name}].automation_mode", a.automation_mode, "rule",
                          span=sp[0] if sp else None,
                          evidence="; ".join(text[x:y] for x, y in sp))
        elif len(modes) > 1:
            pp.ask(f"mode:{a.name}", f"You gave more than one approval rule for '{a.clause}'. "
                                     f"Should I ask you before it, tell you after, or just do it?",
                   ["human_required", "human_notified", "autonomous"])
        else:
            pp.ask(f"mode:{a.name}", f"For '{a.clause}': should I ask you before doing it, do it "
                                     f"and tell you after, or just do it?",
                   ["human_required", "human_notified", "autonomous"])

    # reversal for autonomous actions
    rev = _RE_REVERSAL.search(text)
    autos = [a for a in pp.actions if a.automation_mode == "autonomous"]
    for a in autos:
        given = answers.get(f"reversal:{a.name}")
        if given and given.strip():
            a.reversal = given.strip()
            pp.derive(f"actions[{a.name}].reversal", a.reversal, "answer",
                      evidence=f"reversal:{a.name}")
        elif rev and len(autos) == 1:
            a.reversal = rev.group("rev").strip()
            pp.derive(f"actions[{a.name}].reversal", a.reversal, "rule", span=rev.span("rev"))
        else:
            pp.ask(f"reversal:{a.name}",
                   f"'{a.clause}' will run without asking you. If it goes wrong, how should it "
                   f"be undone? (e.g. 'delete the written file')")


# --------------------------------------------------------------------------------------------
# instructions — the user's own steps, numbered
# --------------------------------------------------------------------------------------------
_READ_VERBS = {"find", "look", "check", "get", "list", "search", "collect", "gather", "compare",
               "summarise", "summarize", "brief", "read", "fetch", "pull", "review", "identify",
               "count", "open", "extract", "flag", "note", "match", "calculate", "work"}


def build_steps(pp: PrePass) -> list[str]:
    text = pp.text
    cut = []
    if pp.schedule:
        cut.append(pp.schedule.span)
    if pp.event:
        cut.append(pp.event.span)
    approvals = _approval_modes(text)
    rev = _RE_REVERSAL.search(text)
    steps: list[str] = []
    for sent_m in re.finditer(r"[^.;!?\n]+", text):
        sent, s = sent_m.group(0), sent_m.start()
        if rev and _overlaps(sent_m.span(), rev.span()):
            continue
        if pp.actions and any(s <= a[1][0] < sent_m.end() for a in approvals) and not any(
                _overlaps(sent_m.span(), a.span) for a in pp.actions):
            continue
        # remove trigger phrases
        chars = list(sent)
        for a, b in cut:
            for i in range(max(a, s), min(b, sent_m.end())):
                chars[i - s] = " "
        sent = re.sub(r"\s+", " ", "".join(chars)).strip(" ,:-")
        if not sent:
            continue
        parts = [p.strip() for p in re.split(r",|\s+and\s+then\s+|\s+then\s+", sent) if p.strip()]
        parts = [re.sub(r"^(?:and|then|also)\s+", "", p, flags=re.I) for p in parts]
        verb_like = all(p.split()[0].lower() in _READ_VERBS | set(WRITE_VERBS) for p in parts)
        chosen = parts if (len(parts) > 1 and verb_like) else [sent]
        for p in chosen:
            p = p[0].upper() + p[1:]
            steps.append(p.rstrip(".") + ".")
    return steps


# --------------------------------------------------------------------------------------------
# answers to schedule questions, and write language that built no action (Codex batch 2 #5, #11)
# --------------------------------------------------------------------------------------------
#: Schedule questions whose answer is itself a schedule phrase ("at 9am", "every Monday").
SCHEDULE_QUESTIONS = ("schedule", "schedule_time", "schedule_day", "schedule_frequency",
                      "schedule_interval")


def apply_schedule_answers(pp: PrePass, default_tz: Optional[str], answers: dict[str, str], *,
                           tz_source: str = "deployment config") -> None:
    """An answer to a schedule question is parsed by the SAME schedule rules: first on its own,
    then together with the request. Adopted only when that yields a complete schedule with no
    question of its own; otherwise the question stays. Nothing is guessed."""
    asked = [q for q in pp.questions if q.id in SCHEDULE_QUESTIONS]
    given = [(q.id, (answers.get(q.id) or "").strip()) for q in asked]
    given = [(qid, a) for qid, a in given if a]
    if pp.schedule is not None or not given:
        return
    extra = ". ".join(a for _, a in given)
    evidence = "; ".join(f"{qid} = {a}" for qid, a in given)
    for alone, text in ((True, extra), (False, pp.text.rstrip(" .") + ". " + extra)):
        sub = PrePass(text=text)
        find_schedule(sub, default_tz, answers, tz_source=tz_source)
        if sub.schedule is None or sub.questions:
            continue
        lo, hi = sub.schedule.span
        span = (lo, hi) if (not alone and hi <= len(pp.text)) else (0, 0)
        pp.schedule = ScheduleFinding(cron=sub.schedule.cron, timezone=sub.schedule.timezone,
                                      phrase=sub.schedule.phrase, span=span)
        pp.questions = [q for q in pp.questions if q.id not in SCHEDULE_QUESTIONS]
        pp.derive("trigger.type", "schedule", "answer", evidence=evidence)
        pp.derive("trigger.schedule.cron", sub.schedule.cron, "answer", evidence=evidence)
        tzd = next((d for d in sub.derivations if d["field"] == "trigger.schedule.timezone"), None)
        if tzd is not None:
            pp.derivations = [d for d in pp.derivations
                              if d["field"] != "trigger.schedule.timezone"]
            if tzd.get("span") and (alone or tzd["span"][1] > len(pp.text)):
                tzd = {**tzd, "span": None, "by": "answer", "evidence": evidence}
            pp.derivations.append(tzd)
        return


_WRITE_INTENT_LEAD = (r"(?:\b(?:it|to|will|should|must|can|could|would|shall|then|and|also|"
                      r"please)\s+)")


def find_write_intent(pp: PrePass, answers: dict[str, str]) -> None:
    """A clause whose words say the agent should change something ("it emails the customer",
    "I want it to send the report") but that produced no governed action is ASKED about. It is
    never compiled as a read-only agent whose instructions still ask for the write."""
    verb_alt = "|".join(sorted(WRITE_VERBS, key=len, reverse=True))
    rx = re.compile(_WRITE_INTENT_LEAD + r"(?P<verb>" + verb_alt + r")(?:e?s)?\b", re.I)
    covered = [a.span for a in pp.actions]
    rev = _RE_REVERSAL.search(pp.text)
    for clause, s, e in _clauses(pp.text):
        if any(a <= s < b or s <= a < e for a, b in covered):
            continue
        if rev and rev.start() <= s < rev.end():
            continue
        m = rx.search(clause)
        if not m:
            continue
        qid = f"write_intent:{slug(clause, max_len=40)}"
        ans = (answers.get(qid) or "").strip().lower()
        if ans in ("no", "n", "false", "read_only"):
            pp.assumptions.append(f"'{clause}' is not a write (you answered no)")
            continue
        verb = m.group("verb").lower()
        pp.ask(qid, f"'{clause}' reads like it should {verb} something, but I could not turn it "
                    f"into an approved action. Say it as its own step naming where it writes "
                    f"(e.g. '{verb.capitalize()} ... into <source>'), or answer 'no' if it should "
                    f"not write.", ["no"])


# --------------------------------------------------------------------------------------------
# the pass
# --------------------------------------------------------------------------------------------
def subject_mentions(text: str, catalogue: CatalogueLike) -> list[tuple[str, tuple[int, int]]]:
    """Every place the WHOLE request names a subject kind (a person-like kind the pack declares).
    Identity is a property of the workflow, not of one clause: "find the Person matching each
    contact … update their record" touches a person although the write clause names nobody
    (R-017 #3). Any write in a request that names a subject anywhere is treated as touching one."""
    out = []
    for k in catalogue.subject_kinds:
        for m in re.finditer(r"\b" + re.escape(k) + r"s?\b", text, re.I):
            out.append((k, m.span()))
    return sorted(out, key=lambda x: x[1])


def prepass(text: str, catalogue: CatalogueLike, *, default_tz: Optional[str],
            answers: Optional[dict[str, str]] = None,
            tz_source: str = "deployment config") -> PrePass:
    answers = dict(answers or {})
    pp = PrePass(text=text)
    find_delegates(pp, catalogue, answers)
    # a reference to an EXISTING adapter agent is not a request to build one (E2E N-04)
    ma = _RE_ADAPTER.search(_mask(text, pp.delegate_spans))
    if ma and pp.delegates:
        choice = (answers.get("kind") or "").strip().lower()
        if choice in ("agent", "adapter_agent"):
            pp.kind, pp.kind_by_rule = choice, True
            pp.derive("kind", choice, "answer", evidence=f"kind = {choice}")
        else:
            pp.ask("kind", f"This delegates to {', '.join(pp.delegates)} but also reads like a "
                           f"new adapter agent ('{text[ma.start():ma.end()]}'). Is it an agent "
                           f"that delegates (agent), or a new adapter agent (adapter_agent)?",
                   ["agent", "adapter_agent"])
    elif ma:
        pp.kind, pp.kind_by_rule = "adapter_agent", True
        pp.derive("kind", "adapter_agent", "rule", span=ma.span())
    elif pp.delegates:
        pp.kind, pp.kind_by_rule = "agent", True
        pp.derive("kind", "agent", "rule", span=pp.delegate_spans[0],
                  evidence=f"delegates to the registered adapter agent(s) "
                           f"{', '.join(pp.delegates)}; an existing adapter agent is not rebuilt")
    elif pp.delegate_spans:
        pp.kind, pp.kind_by_rule = "agent", True     # a delegate is being asked for: an agent
        pp.derive("kind", "agent", "rule", span=pp.delegate_spans[0],
                  evidence="delegates to an adapter agent (which one is asked)")
    if pp.kind == "adapter_agent":
        pp.delegates = []                  # an adapter agent delegates nothing
    pp.read_only_declared = bool(_RE_READ_ONLY.search(text))
    mf = _RE_FACTS.search(text)
    if mf:
        pp.facts_intent = mf.span()
    fm = _RE_FRESHNESS.search(text)
    if fm:
        n = int(fm.group(1))
        pp.freshness_hours = n * 24 if fm.group(2).lower().startswith("d") else n
        pp.derive("freshness.max_age_hours", pp.freshness_hours, "rule", span=fm.span())
    pp.change_sync = bool(_RE_CHANGE_SYNC.search(text))

    find_mentions(pp, catalogue)
    apply_answers_to_mentions(pp, catalogue, answers)
    add_delegate_sources(pp, catalogue)
    find_schedule(pp, default_tz, answers, tz_source=tz_source)
    apply_schedule_answers(pp, default_tz, answers, tz_source=tz_source)
    find_event(pp, catalogue, answers)
    if pp.schedule and pp.event:
        if pp.kind == "adapter_agent":
            pp.assumptions.append("both a schedule and a change trigger were given; the sync runs "
                                  "on the schedule and re-ingests whatever changed since the last "
                                  "run (incremental by content hash)")
            pp.event = None
            pp.derivations = [d for d in pp.derivations
                              if not d["field"].startswith("trigger.event")]
            pp.derive("trigger.type", "schedule", "rule", span=pp.schedule.span)
        else:
            choice = (answers.get("trigger") or "").strip().lower()
            if choice == "schedule":
                pp.event = None
                pp.derivations = [d for d in pp.derivations
                                  if not d["field"].startswith("trigger.event")]
                pp.derive("trigger.type", "schedule", "answer", evidence="trigger = schedule")
            elif choice == "event":
                pp.schedule = None
                pp.derivations = [d for d in pp.derivations
                                  if not d["field"].startswith("trigger.schedule")]
                pp.derive("trigger.type", "event", "answer", evidence="trigger = event")
            else:
                pp.ask("trigger", "You gave both a schedule and an event. Should it run on the "
                                  "schedule, or when the event happens?", ["schedule", "event"])
    if pp.kind == "agent":
        find_actions(pp, catalogue, answers)
        if pp.read_only_declared and pp.actions:
            choice = (answers.get("mode") or "").strip().lower()
            if choice == "read_only":
                pp.assumptions.append("you answered read-only, so the write step(s) ("
                                      + "; ".join(a.clause for a in pp.actions)
                                      + ") are not actions; it cannot write")
                pp.derivations = [d for d in pp.derivations
                                  if not d["field"].startswith("actions[")]
                pp.questions = [q for q in pp.questions if not any(
                    q.id.endswith(":" + a.name) for a in pp.actions)]
                pp.actions = []
            elif choice == "read_write":
                pp.read_only_declared = False
                pp.derive("mode", "read_write", "answer", evidence="mode = read_write")
            else:
                pp.ask("mode", "You said read-only, but also asked it to write "
                               f"({', '.join(a.clause for a in pp.actions)}). Which is it?",
                       ["read_only", "read_write"])
        find_write_intent(pp, answers)
        subjects = subject_mentions(text, catalogue)
        if subjects:
            for a in pp.actions:
                a.touches_subject = True
    pp.steps = build_steps(pp)
    for k in catalogue.kinds:
        if k not in pp.kinds_mentioned and re.search(r"\b" + re.escape(k) + r"s?\b", text, re.I):
            pp.kinds_mentioned.append(k)
    return pp


__all__ = ["PrePass", "Question", "Mention", "ScheduleFinding", "EventFinding", "ActionFinding",
           "prepass", "stem", "slug", "norm", "WRITE_VERBS", "build_steps", "find_delegates"]
