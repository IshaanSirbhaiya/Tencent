"""A5 natural-language audit filter: deterministic rules, range caps, validated optional model."""
from __future__ import annotations

import datetime as dt

import pytest
from databridge_ib.audit.nl_filter import NlFilterError, parse_audit_question
from databridge_ib.audit.query import MAX_RANGE, AuditFilter

from test_audit_support import NOW


def test_s5_question():
    r = parse_audit_question("Show me everything user `jdoe` accessed related to the "
                             "`payment-gateway` Confluence space in the last 30 days", NOW)
    f = r.filter
    assert r.source == "deterministic"
    assert (f.user, f.container, f.doc) == ("jdoe", "payment-gateway", None)
    assert f.since == NOW - dt.timedelta(days=30) and f.until == NOW


def test_who_accessed_doc():
    for q, doc in [("who accessed PAY-201?", "PAY-201"),
                   ("Who accessed confluence:cf-1001", "confluence:cf-1001"),
                   ("who accessed the PG runbook page last month", "PG runbook")]:
        f = parse_audit_question(q, NOW).filter
        assert f.doc == doc and f.user is None, q


def test_what_did_x_ask_last_week():
    f = parse_audit_question("what did priya.sre ask last week", NOW).filter
    assert f.user == "priya.sre" and f.kind == "request.accepted"
    # previous calendar week, Monday to Monday, in the clock's timezone
    assert f.since.weekday() == 0 and f.until - f.since == dt.timedelta(weeks=1)
    assert f.until <= NOW and NOW - f.until < dt.timedelta(weeks=1)


def test_denied_requests_for_user():
    f = parse_audit_question("denied requests for ken.ext", NOW).filter
    assert f.user == "ken.ext" and f.decision == "deny"


def test_range_cap_and_default_range():
    r = parse_audit_question("failed requests in the last 900 days", NOW)
    assert r.filter.kind == "request.failed"
    assert r.filter.until - r.filter.since == MAX_RANGE and any("capped" in n for n in r.notes)
    r = parse_audit_question("reveals by sara.sec", NOW)
    assert r.filter.kind == "audit.reveal" and r.filter.user == "sara.sec"
    assert r.filter.until - r.filter.since == MAX_RANGE


def test_explicit_dates():
    f = parse_audit_question("events for jdoe between 2026-09-01 and 2026-09-30", NOW).filter
    assert f.user == "jdoe"
    assert f.since.date() == dt.date(2026, 9, 1) and f.until.date() == dt.date(2026, 10, 1)


def test_injection_text_cannot_escape_the_schema():
    f = parse_audit_question("show user `jdoe'; DROP TABLE events; --` activity", NOW).filter
    assert isinstance(f, AuditFilter)
    assert f.user in (None, "jdoe")


def test_llm_only_when_rules_recognise_nothing():
    calls = []

    def llm(q, now):
        calls.append(q)
        return {"user": "zed", "since": (now - dt.timedelta(days=3)).isoformat(),
                "until": now.isoformat()}

    r = parse_audit_question("denied requests for ken.ext", NOW, llm=llm)
    assert r.source == "deterministic" and not calls
    r = parse_audit_question("anything odd lately with zed?", NOW, llm=llm)
    assert r.source == "llm" and r.filter.user == "zed" and calls


def test_llm_output_is_validated_and_fails_closed():
    # Codex sec r1 #16: a model filter that does not validate (or a model that fails) is an error,
    # never a silent fallback to the broad deterministic query
    def bad(q, now):
        return {"user": "zed", "where": "1=1"}           # outside the closed schema

    with pytest.raises(NlFilterError):
        parse_audit_question("anything odd lately?", NOW, llm=bad)

    def boom(q, now):
        raise RuntimeError("model down")

    with pytest.raises(NlFilterError):
        parse_audit_question("anything odd lately?", NOW, llm=boom)

    def wide(q, now):
        return {"user": "zed", "limit": 500}             # no range -> capped to 365 days

    f = parse_audit_question("anything odd lately?", NOW, llm=wide).filter
    assert f.until - f.since <= MAX_RANGE


def test_invalid_constraint_is_not_dropped():
    # Codex sec r1 #16: "R&D plan" is outside the doc vocabulary; it must not become "everything"
    with pytest.raises(NlFilterError) as ei:
        parse_audit_question("who accessed the R&D plan last week", NOW)
    assert ei.value.unparsed == {"doc": "R&D plan"}


@pytest.mark.parametrize("q", ["what did jdoe ask on 2026-02-30",
                               "events for jdoe between 2026-13-01 and 2026-09-30",
                               "events for jdoe until 9999-12-31"])
def test_parser_errors_are_nl_filter_errors(q):
    # Codex sec r1 #17: impossible dates and datetime overflow are a 422-able error, never a crash
    with pytest.raises(NlFilterError):
        parse_audit_question(q, NOW)


def test_huge_and_reversed_periods_are_capped_not_crashing():
    r = parse_audit_question("failed requests in the last 9999 years", NOW)
    assert r.filter.until - r.filter.since == MAX_RANGE and any("capped" in n for n in r.notes)
    r = parse_audit_question("events for jdoe between 2026-09-30 and 2024-01-01", NOW)
    f = r.filter
    assert f.since < f.until and f.until - f.since == MAX_RANGE
    assert f.until == dt.datetime(2026, 10, 1, tzinfo=NOW.tzinfo)          # ordered, then capped
    assert any("capped" in n for n in r.notes)


def test_calendar_bounds_are_half_open():
    # Codex sec r1 #18: the upper bound is the next interval's midnight, used exclusively
    midnight = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    f = parse_audit_question("what did jdoe ask yesterday", NOW).filter
    assert (f.since, f.until) == (midnight - dt.timedelta(days=1), midnight)
    f = parse_audit_question("events for jdoe on 2026-09-01", NOW).filter
    assert f.until - f.since == dt.timedelta(days=1) and f.until.time() == dt.time(0)
    f = parse_audit_question("events for jdoe before 2026-09-01", NOW).filter
    assert f.until == dt.datetime(2026, 9, 1, tzinfo=NOW.tzinfo)


def test_model_receives_only_question_and_clock():
    seen = []

    def llm(*args, **kw):
        seen.append((args, kw))
        return {}

    parse_audit_question("hmm?", NOW, llm=llm)
    assert seen == [(("hmm?", NOW), {})]
