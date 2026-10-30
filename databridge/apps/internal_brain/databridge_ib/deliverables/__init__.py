"""Deliverables from an answer (docs/finals/deliverables/PLAN.md, with the Codex, Fable and Opus
reviews applied): a table, a chart or a short analysis built ONLY from one of the asker's own
released answers and the cited sources they can still open now.

* ``evidence``  the source units each cited document contributes (lines, sentences, CSV rows),
  filtered like the extractive answerer and capped like ``/ib/doc``;
* ``verify``    pure, deterministic checks: every kept number is a number span of its own cited
  source unit, bound to its label in the same row or sentence, one unit per chart;
* ``spec``      the closed JSON shapes a model may return (Pydantic, ``extra="forbid"``);
* ``prompts``   the system preamble and the per-kind task text;
* ``build``     the deterministic builders (source table, quoted facts, source chart);
* ``export``    CSV (formula-guarded) built on the server from the stored, checked content;
* ``service``   the write-ahead service (ownership, ledger, model call, store);
* ``routes``    ``POST /ib/deliverables`` and ``POST /ib/deliverables/export``.

Off unless ``IB_DELIVERABLES=1`` (a kill switch, read at start-up: changing it needs an IB restart).
"""
