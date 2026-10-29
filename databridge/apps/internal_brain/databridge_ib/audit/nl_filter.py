"""Natural-language audit questions -> the closed `AuditFilter` (plan §4.7, ``POST /audit/ask``).

Deterministic first: keyword and pattern rules compute the filter. A model is OPTIONAL and only
consulted (``llm=``) when the rules recognise nothing; its output must validate as the same closed
schema (``extra="forbid"``) and passes through the same range cap, otherwise the deterministic result
is used. The model is given the question and the clock — never a ledger row.

Examples (``now`` = the estate clock):

* "Show me everything user `jdoe` accessed related to the `payment-gateway` Confluence space in the
  last 30 days" -> user=jdoe, container=payment-gateway, since=now-30d, until=now
* "who accessed PAY-201" / "who accessed confluence:cf-1001" -> doc=...
* "what did priya.sre ask last week" -> user=priya.sre, kind=request.accepted, previous calendar week
* "denied requests for ken.ext" -> user=ken.ext, decision=deny

Time phrases: "last|past N hours|days|weeks|months|years", "last week|month" (previous calendar
week/month in the clock's timezone), "this week|month", "today", "yesterday", "since <date>",
"before|until <date>", "between <date> and <date>", "on <date>". No time phrase -> the last 365
days. Every range is capped at 365 days (the earlier end moves; the note says so). Ranges are
half-open, ``since <= event_time < until``: "yesterday" ends at today's midnight, exclusive.

Fail closed (Codex sec r1 #16/#17). A constraint the question names but that does not validate (a
title with characters outside the closed vocabulary, an impossible date such as 2026-02-30) raises
`NlFilterError` carrying the unparsed constraint; it is never dropped to run a broader query. A
configured model whose output does not validate, or that fails, is an `NlFilterError` too,
unless the caller asks for ``llm_fallback=True`` (the wired Hy3 parser, plan v2 B13): then the rule
parser's result is used, with a note saying so, exactly as when no model is configured.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

from pydantic import ValidationError

from .query import MAX_RANGE, AuditFilter

LlmParser = Callable[[str, dt.datetime], Union[dict, AuditFilter]]

_Q = r"[`'\"“”‘’]?"                         # optional quoting around an identifier
_ID = r"[A-Za-z0-9][A-Za-z0-9._@-]*[A-Za-z0-9]|[A-Za-z0-9]"
_STOP = {"the", "a", "an", "all", "any", "every", "everyone", "anyone", "someone", "me", "us", "it",
         "last", "this", "past", "previous", "user", "users", "each", "what", "who", "which",
         "requests", "request", "events", "docs", "documents", "access", "accessed", "in", "on",
         "to", "of", "and", "or", "with", "from", "for", "by", "related", "space", "channel",
         "project", "drive", "since", "before", "until", "between", "today", "yesterday", "week",
         "month", "year", "days", "denied", "allowed", "did", "do", "does", "was", "were", "is",
         "are", "has", "have", "ask", "asked", "see", "saw", "everything", "anything", "show",
         "list", "find", "get", "give", "confluence", "jira", "slack", "google", "document"}

_PLATFORM_DOC = re.compile(r"\b(?:confluence|jira|slack|drive):[A-Za-z0-9._:/-]*[A-Za-z0-9]")
_JIRA_KEY = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
_DATE = r"(\d{4}-\d{2}-\d{2})"

_USER_PATTERNS = [
    re.compile(rf"\buser\s+{_Q}(?P<u>{_ID}){_Q}", re.I),
    re.compile(rf"\bwhat\s+(?:did|has|have)\s+{_Q}(?P<u>{_ID}){_Q}\s+(?:ask|asked|access|accessed|"
               rf"see|seen|view|viewed|query|queried)\b", re.I),
    re.compile(rf"\b(?:everything|anything|all)\s+(?:that\s+)?{_Q}(?P<u>{_ID}){_Q}\s+(?:has\s+)?"
               rf"(?:accessed|asked|saw|seen|viewed|opened|read)\b", re.I),
    re.compile(rf"\b(?:requests?|questions?|queries|events?|activity|access(?:es)?|answers?|"
               rf"denials?|reveals?)\s+(?:made\s+)?(?:for|by|from|of)\s+{_Q}(?P<u>{_ID}){_Q}", re.I),
    re.compile(rf"\b(?:by|for)\s+{_Q}(?P<u>{_ID}){_Q}\s*(?:$|[?.,;]|\s+(?:in|over|during|since|"
               rf"last|this|before|until|between|on|yesterday|today)\b)", re.I),
]
_CONTAINER_PATTERNS = [
    re.compile(rf"{_Q}(?P<c>[A-Za-z0-9][A-Za-z0-9._#-]*){_Q}\s+(?:confluence\s+|jira\s+|slack\s+|"
               rf"shared\s+|google\s+)?(?:space|channel|project|drive)\b", re.I),
    re.compile(rf"\b(?:space|channel|project|drive)\s+{_Q}(?P<c>[A-Za-z0-9#][A-Za-z0-9._#-]*){_Q}",
               re.I),
    re.compile(r"(?:^|\s)(?P<c>#[a-z0-9][a-z0-9._-]*)", re.I),
]
_WHO_ACCESSED = re.compile(r"\bwho\s+(?:has\s+|had\s+)?(?:accessed|viewed|saw|seen|read|opened|"
                           r"asked\s+about|got)\s+(?P<d>.+)$", re.I)
_RELATED = re.compile(rf"\b(?:related\s+to|about|regarding|concerning)\s+(?:the\s+)?{_Q}"
                      rf"(?P<c>[A-Za-z0-9][A-Za-z0-9._#-]*){_Q}", re.I)
_TIME_TAIL = re.compile(r"\s+(?:in|over|during|within)\s+the\s+(?:last|past).*$|\s+(?:last|this|"
                        r"since|before|until|between|on|yesterday|today)\b.*$", re.I)

_DENY = re.compile(r"\b(?:denied|deny|denials?|refused|refusals?|blocked|rejected|forbidden)\b", re.I)
_ALLOW = re.compile(r"\b(?:allowed|granted|permitted|released\s+to)\b", re.I)
_KINDS = [
    (re.compile(r"\breveal(?:s|ed)?\b", re.I), "audit.reveal"),
    (re.compile(r"\baudit\s+(?:quer(?:y|ies)|searches)\b", re.I), "audit.query"),
    (re.compile(r"\bfail(?:ed|ures?)\b|\berrors?\b", re.I), "request.failed"),
    (re.compile(r"\banchor(?:s|ed)?\b|\bpublished\s+heads?\b", re.I), "anchor.published"),
    (re.compile(r"\bacl\s+changes?\b|\bpermission\s+changes?\b", re.I), "acl.changed"),
    (re.compile(r"\bmembership\s+changes?\b", re.I), "membership.changed"),
    (re.compile(r"\bwhat\s+(?:did|has|have)\s+\S+\s+ask", re.I), "request.accepted"),
    (re.compile(r"\bquestions?\s+(?:asked\s+)?(?:by|from|of)\b", re.I), "request.accepted"),
]
_UNITS = {"hour": dt.timedelta(hours=1), "day": dt.timedelta(days=1), "week": dt.timedelta(weeks=1),
          "month": dt.timedelta(days=30), "year": dt.timedelta(days=365)}


class NlFilterError(ValueError):
    """The question names a constraint that cannot become a valid filter value (the route answers
    422 with ``unparsed``; nothing broader is run in its place)."""

    def __init__(self, message: str, unparsed: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.unparsed = dict(unparsed or {})


@dataclass
class NlParse:
    filter: AuditFilter
    source: str = "deterministic"                 # deterministic | llm
    notes: list[str] = field(default_factory=list)
    recognised: list[str] = field(default_factory=list)
    model: Optional[str] = None                   # the model that proposed an llm filter
    model_tried: bool = False                     # a model was asked (its filter may be unused)

    def as_dict(self) -> dict[str, Any]:
        return {"filter": self.filter.model_dump(mode="json", exclude_none=True),
                "parser": self.source, "notes": list(self.notes)}


def _clean_id(s: str) -> str:
    return s.strip().strip("`'\"“”‘’").rstrip(".,;:?!")


def _day(s: str, tz: dt.tzinfo) -> dt.datetime:
    try:
        d = dt.date.fromisoformat(s)
    except ValueError:
        raise NlFilterError(f"{s} is not a valid date", {"date": s}) from None
    return dt.datetime(d.year, d.month, d.day, tzinfo=tz)


def _month_start(t: dt.datetime) -> dt.datetime:
    return t.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def parse_time(q: str, now: dt.datetime) -> tuple[Optional[dt.datetime], Optional[dt.datetime], str]:
    """(since, until, matched phrase) — both None when the question names no time."""
    tz = now.tzinfo or dt.timezone.utc
    now = now if now.tzinfo else now.replace(tzinfo=tz)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    m = re.search(r"\b(?:last|past|previous)\s+(\d{1,4})\s+(hour|day|week|month|year)s?\b", q, re.I)
    if m:
        # clamp before subtracting (9999 years would leave datetime's range); _cap notes the cap
        span = min(int(m.group(1)) * _UNITS[m.group(2).lower()], 2 * MAX_RANGE)
        return now - span, now, m.group(0)
    m = re.search(rf"\bbetween\s+{_DATE}\s+and\s+{_DATE}\b", q, re.I)
    if m:
        a, b = sorted((_day(m.group(1), tz), _day(m.group(2), tz)))   # order before capping
        return a, b + dt.timedelta(days=1), m.group(0)
    m = re.search(rf"\bon\s+{_DATE}\b", q, re.I)
    if m:
        d = _day(m.group(1), tz)
        return d, d + dt.timedelta(days=1), m.group(0)
    since = until = None
    phrase = []
    m = re.search(rf"\b(?:since|after|from)\s+{_DATE}\b", q, re.I)
    if m:
        since = _day(m.group(1), tz)
        phrase.append(m.group(0))
    m = re.search(rf"\b(?:before|until|till|to)\s+{_DATE}\b", q, re.I)
    if m:
        until = _day(m.group(1), tz) + (dt.timedelta(days=1) if "until" in m.group(0).lower() or
                                         "till" in m.group(0).lower() else dt.timedelta(0))
        phrase.append(m.group(0))
    if phrase:
        return since, until or now, " ".join(phrase)
    week_start = midnight - dt.timedelta(days=midnight.weekday())
    if re.search(r"\b(?:last|previous|past)\s+week\b", q, re.I):
        return week_start - dt.timedelta(weeks=1), week_start, "last week"
    if re.search(r"\bthis\s+week\b", q, re.I):
        return week_start, now, "this week"
    if re.search(r"\b(?:last|previous|past)\s+month\b", q, re.I):
        this_month = _month_start(midnight)
        return _month_start(this_month - dt.timedelta(days=1)), this_month, "last month"
    if re.search(r"\bthis\s+month\b", q, re.I):
        return _month_start(midnight), now, "this month"
    if re.search(r"\b(?:last|past)\s+year\b", q, re.I):
        return now - _UNITS["year"], now, "last year"
    if re.search(r"\byesterday\b", q, re.I):
        return midnight - dt.timedelta(days=1), midnight, "yesterday"
    if re.search(r"\btoday\b", q, re.I):
        return midnight, now, "today"
    return None, None, ""


def _cap(since: Optional[dt.datetime], until: Optional[dt.datetime], now: dt.datetime,
         notes: list[str]) -> tuple[dt.datetime, dt.datetime]:
    until = until or now
    if since is None:
        since = until - MAX_RANGE
        notes.append("no time range named: the last 365 days")
    if since > until:                                    # order first, then cap (Codex sec r1 #17)
        since, until = until, since
        notes.append("the range was given in reverse; its ends were swapped")
    if until - since > MAX_RANGE:
        since = until - MAX_RANGE
        notes.append("range capped at 365 days")
    return since, until


def parse_deterministic(question: str, now: dt.datetime, *, limit: int = 100) -> NlParse:
    q = " ".join(str(question or "").split())
    rec: list[str] = []
    notes: list[str] = []
    user = container = doc = None

    # documents first: explicit ids, then "who accessed <thing>"
    m = _PLATFORM_DOC.search(q)
    if m:
        doc = m.group(0)
    else:
        m = _JIRA_KEY.search(q)
        if m and not re.search(rf"\b(?:user|for|by)\s+{_Q}{re.escape(m.group(0))}", q, re.I):
            doc = m.group(0)
    w = _WHO_ACCESSED.search(q)
    if w and doc is None:
        target = _TIME_TAIL.sub("", w.group("d")).strip().rstrip("?.!").strip()
        target = re.sub(r"^(?:the|a|an)\s+", "", target, flags=re.I)
        target = re.sub(r"\s+(?:page|doc|document|file|issue|ticket|thread)$", "", target, flags=re.I)
        target = _clean_id(target)
        if target and not re.search(r"\b(?:space|channel|project|drive)\b", target, re.I):
            doc = target[:300]
    if doc:
        rec.append("doc")

    for p in _CONTAINER_PATTERNS:
        m = p.search(q)
        if m and _clean_id(m.group("c")).lower() not in _STOP:
            container = _clean_id(m.group("c"))
            break
    if container is None and doc is None:
        m = _RELATED.search(q)
        if m and _clean_id(m.group("c")).lower() not in _STOP:
            container = _clean_id(m.group("c"))
    if container:
        rec.append("container")

    for p in _USER_PATTERNS:
        for m in p.finditer(q):
            cand = _clean_id(m.group("u"))
            if cand and cand.lower() not in _STOP and cand != container and cand != doc \
                    and not _JIRA_KEY.fullmatch(cand):
                user = cand
                break
        if user:
            break
    if user:
        rec.append("user")

    decision = None
    if _DENY.search(q):
        decision = "deny"
    elif _ALLOW.search(q):
        decision = "allow"
    if decision:
        rec.append("decision")
    kind = None
    for p, k in _KINDS:
        if p.search(q):
            kind = k
            break
    if kind:
        rec.append("kind")
        if kind == "request.failed" and decision == "deny":
            decision = None

    since, until, phrase = parse_time(q, now)
    if phrase:
        rec.append("time")
    since, until = _cap(since, until, now, notes)
    try:
        f = AuditFilter(user=user, container=container, doc=doc, since=since, until=until,
                        decision=decision, kind=kind, limit=limit)
    except ValidationError:
        # Never drop a named scope to run a broader query (Codex sec r1 #16): name what failed.
        unparsed = {}
        for name, val in (("user", user), ("container", container), ("doc", doc)):
            if val is None:
                continue
            try:
                AuditFilter.model_validate({name: val})
            except ValidationError:
                unparsed[name] = val[:300]
        raise NlFilterError("the question names a constraint that is not a valid filter value; "
                            "nothing was searched", unparsed or {"question": q[:300]}) from None
    return NlParse(filter=f, source="deterministic", notes=notes, recognised=rec)


def _validate_llm(out: Any, now: dt.datetime, limit: int) -> AuditFilter:
    f = out if isinstance(out, AuditFilter) else AuditFilter.model_validate(out)
    notes: list[str] = []
    since, until = _cap(f.since, f.until, now, notes)
    return AuditFilter.model_validate({**f.model_dump(), "since": since, "until": until,
                                       "limit": min(f.limit, limit) if f.limit else limit})


def parse_audit_question(question: str, now: dt.datetime, *, llm: Optional[LlmParser] = None,
                         limit: int = 100, llm_fallback: bool = False) -> NlParse:
    """The closed filter for ``question``. Deterministic rules first; ``llm`` (optional) is asked
    only when they recognise nothing, and its output is validated against the closed schema.

    Raises `NlFilterError` (never anything else) when the question cannot become a valid filter:
    a named constraint that does not validate, an impossible date, or a configured model whose
    output does not validate or that fails. Nothing broader is substituted.

    ``llm_fallback=True``: a model that fails or whose output does not validate is NOT an error;
    the rule parser's result is returned (``source == "deterministic"``) with a note, the same
    result as with no model configured. ``llm`` may return a dict, an `AuditFilter`, or an object
    with ``filter`` and ``model`` (nl_llm.ModelFilter), whose model name is kept."""
    try:
        det = parse_deterministic(question, now, limit=limit)
    except NlFilterError:
        raise
    except Exception:                                                # noqa: BLE001 — 422, not 500
        raise NlFilterError("the question could not be parsed into a filter",
                            {"question": str(question)[:300]}) from None
    if det.recognised or llm is None:
        return det
    model: Optional[str] = None
    try:
        out = llm(str(question), now)
        if not isinstance(out, (dict, AuditFilter)) and hasattr(out, "filter"):
            model = str(getattr(out, "model", "") or "") or None
            out = out.filter
        f = _validate_llm(out, now, limit)
    except Exception:                                                # noqa: BLE001 — any failure
        if llm_fallback:
            det.model_tried = True
            det.notes.append("the model's filter was not usable; the rule parser's result is shown")
            return det
        raise NlFilterError("the rules recognised nothing and the model's filter did not "
                            "validate; nothing was searched",
                            {"question": str(question)[:300]}) from None
    return NlParse(filter=f, source="llm", notes=["filter proposed by the model and validated"],
                   recognised=["llm"], model=model, model_tried=True)


__all__ = ["NlParse", "NlFilterError", "LlmParser", "parse_audit_question", "parse_deterministic",
           "parse_time"]
