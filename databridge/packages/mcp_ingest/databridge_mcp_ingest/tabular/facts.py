"""The facts store — rows copied READ-ONLY into a companion SQLite, never embedded (P6).

    WALNUT_FACTS_DIR (default var/facts)/<source>_facts.sqlite     the rows (and aggregates)
                                        /<source>_facts.json       manifest: extracted_at,
                                                                   sha256 of the file, per table

Whole tables up to `facts.row_cap` rows are copied page by page in primary-key order. A table
above the cap is AGGREGATED: grouped by the columns whose MEASURED distinct count sits in the
policy band (default 10–500), with row counts and sums of numeric measures — and the card says so
(P7). History-snapshot tables are excluded and recorded. After the copy, each table's row count is
read back from the published file and compared with the source's measured count (P8).

Publication refuses the silent disasters (P11, R-022 f1 #7/#10): an empty facts file; one that
shrank below `min_shrink_ratio` of the previous file's rows (rows copied PLUS rows an aggregated
table's groups cover — every mode counts); an INCONSISTENT one (a table whose copied rows or group
coverage disagree with its measured count, or whose rows were measured but none copied); and one
built from a profile whose row count could not be measured (`row_count` None — an unknown is never
a zero). `force=<principal>` — an AUTHENTICATED principal (`plans.require_authenticated_approver`;
a Boolean, a name or a dict is refused, R-022 f2 #5) confirming the source really changed —
overrides the shrink and consistency checks, never the empty or unmeasured ones. Each override is
appended to `<source>_facts.overrides.jsonl` (append-only, record-digest sealed) with the
principal, the time, the CANDIDATE's content digest and the refusal it overrides;
`force_digest=` binds the override to a candidate seen refused earlier (a different candidate is
refused). The previous file stays in place.

The incremental key (`content_sha256`, R-022 f2 #10) covers the whole logical publication: every
table's DDL, the `_facts_tables` / `_facts_columns` catalogue rows (less the extract date) and
every row — a renamed column with identical values is a change.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from .. import paths
from .profiling import SqlRunner, quote_ident

SQLITE_TYPES = {"numeric": "NUMERIC", "boolean": "INTEGER", "temporal": "TEXT", "json": "TEXT",
                "text": "TEXT", "other": "TEXT"}


def facts_dir() -> Path:
    v = os.environ.get("WALNUT_FACTS_DIR")
    return Path(v) if v else paths.DATABRIDGE_ROOT / "var" / "facts"


def facts_path(source_id: str, directory: Optional[Path | str] = None) -> Path:
    return (Path(directory) if directory else facts_dir()) / f"{source_id}_facts.sqlite"


def manifest_path(source_id: str, directory: Optional[Path | str] = None) -> Path:
    return (Path(directory) if directory else facts_dir()) / f"{source_id}_facts.json"


def load_manifest(source_id: str, directory: Optional[Path | str] = None) -> Optional[dict]:
    p = manifest_path(source_id, directory)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _cell(value: Any, cls: str) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() and cls == "numeric" \
            and value.as_tuple().exponent >= 0 else float(value) if cls == "numeric" else str(value)
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    from .pyrepr import to_text

    t = to_text(value)
    return t if isinstance(t, (str, int, float)) else json.dumps(t, default=str)


def facts_table_names(profiles: list[dict]) -> dict[str, str]:
    """source table ref -> facts table name: the bare table name, schema-qualified with `__`
    only where two schemas share a table name.

    COLLISION-CHECKED (R-022 f2 #15): `s.foo` + `x.foo` qualify to `s__foo`, which a distinct
    source table named `s__foo` would also claim; SQLite names compare case-insensitively; and the
    `_facts_*` catalogue names are reserved. Every ref whose name is not unique gets a stable
    suffix from the sha256 of its ref, so the mapping is one-to-one and does not depend on order."""
    counts: dict[str, int] = {}
    for p in profiles:
        counts[p["table"]] = counts.get(p["table"], 0) + 1
    names = {p["ref"]: (p["table"] if counts[p["table"]] == 1 or not p["schema"]
                        else f"{p['schema']}__{p['table']}") for p in profiles}
    claims: dict[str, int] = {}
    for n in names.values():
        claims[n.lower()] = claims.get(n.lower(), 0) + 1
    out = {}
    for ref, n in names.items():
        if claims[n.lower()] > 1 or n.lower().startswith("_facts_"):
            n = f"{n}__{hashlib.sha256(ref.encode('utf-8')).hexdigest()[:10]}"
        out[ref] = n
    if len({n.lower() for n in out.values()}) != len(out):
        raise ValueError(f"facts table names still collide after suffixing: {sorted(out.values())}")
    return out


class FactsOverrideRefused(ValueError):
    """A forced publication without an authenticated principal."""


def overrides_log_path(source_id: str, directory: Optional[Path | str] = None) -> Path:
    return (Path(directory) if directory else facts_dir()) / f"{source_id}_facts.overrides.jsonl"


def _record_digest(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k != "record_digest"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     default=str).encode("utf-8")).hexdigest()


def logged_overrides(source_id: str, directory: Optional[Path | str] = None) -> list[dict]:
    """The override records whose record digest verifies (an edited line is never honoured)."""
    p = overrides_log_path(source_id, directory)
    out: list[dict] = []
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("record_digest") == _record_digest(rec):
            out.append(rec)
    return out


def _forcer(force: Any) -> Optional[dict]:
    """The authenticated principal behind `force`, or None when nothing is forced. A Boolean,
    a str, a dict or an unissued principal is REFUSED (R-022 f2 #5) — never read as 'forced'."""
    if force is None or force is False:
        return None
    from ..plans import ApprovalRefused, require_authenticated_approver

    if force is True:
        raise FactsOverrideRefused(
            "forced facts publication REFUSED: force=True is a Boolean, not a person; pass the "
            "authenticated principal confirming the source really changed (force=<principal>)")
    try:
        return require_authenticated_approver(force, what="forced facts publication")
    except ApprovalRefused as exc:
        raise FactsOverrideRefused(str(exc)) from None


def choose_group_columns(profile: dict, policy: dict) -> tuple[list[str], str]:
    f = policy.get("facts") or {}
    lo, hi = int(f.get("aggregate_min_distinct", 10)), int(f.get("aggregate_max_distinct", 500))
    k = int(f.get("max_group_columns", 2))
    skip = set(profile["primary_key"])
    band = [c for c in profile["columns"]
            if c["name"] not in skip and not c["prose"] and c["class"] in ("text", "boolean",
                                                                            "temporal", "other")
            and c.get("distinct") is not None and lo <= c["distinct"] <= hi]
    band.sort(key=lambda c: (c["distinct"], c["name"]))
    chosen = [c["name"] for c in band[:k]]
    measured = ", ".join(f"{c['name']}={c.get('distinct')}" for c in profile["columns"]
                         if c["name"] not in skip)
    why = (f"grouped by {chosen} — measured distinct counts in the {lo}–{hi} band "
           f"(all measured: {measured})" if chosen else
           f"no column's measured distinct count is in the {lo}–{hi} band ({measured})")
    return chosen, why


def _replace(tmp: Path, final: Path, attempts: int = 20) -> None:
    """os.replace, retried briefly: on Windows a reader holding the old file open (a concurrent
    count_query) makes the swap fail with access denied for a few milliseconds."""
    import time

    for i in range(attempts):
        try:
            os.replace(tmp, final)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.05)


def build_facts(source_id: str, runner: SqlRunner, profiles: list[dict], policy: dict, *,
                extracted_at: Optional[dt.datetime] = None, force: Any = None,
                force_digest: Optional[str] = None) -> dict:
    """Write and publish the facts file. Returns the manifest (status ok | refused).

    `force`: the AUTHENTICATED principal overriding a SHRUNK / INCONSISTENT refusal (see the
    module doc); `force_digest`: the candidate `content_sha256` that principal saw refused."""
    who = _forcer(force)
    force = who is not None
    fpol = policy.get("facts") or {}
    cap, page = int(fpol.get("row_cap", 200000)), int(fpol.get("page_size", 500))
    extracted_at = extracted_at or dt.datetime.now(dt.timezone.utc)
    when = extracted_at.isoformat(timespec="seconds")
    names = facts_table_names(profiles)
    out_dir = facts_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    final = facts_path(source_id)
    tmp = final.with_name(f".{final.name}.{os.getpid()}.tmp")
    if tmp.exists():
        tmp.unlink()
    db = sqlite3.connect(tmp)
    db.execute("PRAGMA journal_mode=DELETE")
    db.executescript("""
        CREATE TABLE _facts_tables (facts_table TEXT PRIMARY KEY, source_table TEXT, mode TEXT,
            rows_source INTEGER, rows_in_facts INTEGER, group_by TEXT, reason TEXT,
            extracted_at TEXT, source_sql TEXT);
        CREATE TABLE _facts_columns (facts_table TEXT, column_name TEXT, source_type TEXT,
            class TEXT, prose INTEGER, distinct_n INTEGER, non_null INTEGER, lo TEXT, hi TEXT);
        CREATE TABLE _facts_meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    tables: list[dict] = []
    problems: list[str] = []
    unmeasured: list[str] = []
    for p in profiles:
        ft = names[p["ref"]]
        entry = {"source_table": p["ref"], "facts_table": ft, "rows_source": p["row_count"],
                 "rows_in_facts": 0, "rows_covered": 0, "mode": "rows", "group_by": [],
                 "reason": "", "source_sql": ""}
        if p.get("row_count") is None:
            # R-022 f1 #7: the row count was NOT measured (a failed or empty catalogue answer).
            # Recorded as an exclusion with its reason — never copied as if it were 0 rows.
            unmeasured.append(p["ref"])
            entry.update(mode="unmeasured", facts_table=None, rows_source=None,
                         reason=f"row count not measured: "
                                f"{'; '.join(p.get('measure_problems') or ['unknown'])}")
            tables.append(entry)
            db.execute("INSERT INTO _facts_tables VALUES (?,?,?,?,?,?,?,?,?)",
                       (f"(unmeasured) {p['ref']}", p["ref"], "unmeasured", None, 0, "",
                        entry["reason"], when, ""))
            continue
        hist = p.get("history_snapshot") or {}
        if hist.get("is"):
            entry.update(mode="excluded", facts_table=None,
                         reason=f"history snapshot ({'; '.join(hist.get('signals') or [])}) — "
                                f"excluded by rule {hist.get('rule')}; reverse: declare the "
                                f"table in <source>.tabular.yaml history_snapshot exceptions")
            tables.append(entry)
            db.execute("INSERT INTO _facts_tables VALUES (?,?,?,?,?,?,?,?,?)",
                       (f"(excluded) {p['ref']}", p["ref"], "excluded", p["row_count"], 0, "",
                        entry["reason"], when, ""))
            continue
        cls = {c["name"]: c["class"] for c in p["columns"]}
        ctx = {"schema": p["schema"], "table": p["table"]}
        if p["row_count"] <= cap:
            cols = [c["name"] for c in p["columns"]]
            ddl = ", ".join(f"{quote_ident(c)} {SQLITE_TYPES.get(cls[c], 'TEXT')}" for c in cols)
            db.execute(f"CREATE TABLE {quote_ident(ft)} ({ddl})")
            order = ", ".join(runner.quote(c) for c in (p["primary_key"] or cols))
            copied, offset = 0, 0
            opaque0 = int(getattr(runner, "opaque_values", 0) or 0)
            placeholders = ", ".join("?" for _ in cols)
            first_sql = ""
            while True:
                sql = runner.sql("select_page", {**ctx, "order_by": order, "limit": page,
                                                 "offset": offset})
                first_sql = first_sql or sql
                rows = runner.run(sql)
                if rows:
                    db.executemany(f"INSERT INTO {quote_ident(ft)} VALUES ({placeholders})",
                                   [tuple(_cell(r.get(c), cls[c]) for c in cols) for r in rows])
                copied += len(rows)
                offset += page
                if len(rows) < page:
                    break
            entry.update(rows_in_facts=copied, rows_covered=copied, source_sql=first_sql,
                         reason="all rows copied")
            lost = int(getattr(runner, "opaque_values", 0) or 0) - opaque0
            if lost:
                # R-022 f1 #7: recorded, never silent — the cells are NULL in the facts store
                entry["opaque_values_nulled"] = lost
                problems.append(f"{p['ref']}: {lost} opaque value(s) the server printed as an "
                                f"object were stored as NULL")
            if copied != p["row_count"]:
                problems.append(f"{p['ref']}: measured {p['row_count']} rows, copied {copied} "
                                f"(the table changed during the extract?)")
        else:
            group, why = choose_group_columns(p, policy)
            if not group:
                entry.update(mode="count_only", facts_table=None,
                             reason=f"{p['row_count']} rows is above the facts cap {cap} and "
                                    f"{why}; only the row count is recorded")
                tables.append(entry)
                db.execute("INSERT INTO _facts_tables VALUES (?,?,?,?,?,?,?,?,?)",
                           (f"(count only) {p['ref']}", p["ref"], "count_only", p["row_count"],
                            0, "", entry["reason"], when, ""))
                continue
            measures = [c["name"] for c in p["columns"] if c["class"] == "numeric"
                        and c["name"] not in p["primary_key"]
                        and c["name"] not in {f["column"] for f in p["foreign_keys"]}]
            gcols = ", ".join(runner.quote(g) for g in group)
            msql = "".join(f", SUM({runner.quote(m)}) AS {runner.quote('sum_' + m)}"
                           for m in measures)
            sql = runner.sql("aggregate", {**ctx, "group_cols": gcols, "measures": msql})
            rows = runner.run(sql)
            outcols = group + ["row_count"] + [f"sum_{m}" for m in measures]
            types = [SQLITE_TYPES.get(cls[g], "TEXT") for g in group] + \
                ["INTEGER"] + ["NUMERIC"] * len(measures)
            db.execute(f"CREATE TABLE {quote_ident(ft)} (" + ", ".join(
                f"{quote_ident(c)} {t}" for c, t in zip(outcols, types)) + ")")
            db.executemany(
                f"INSERT INTO {quote_ident(ft)} VALUES ({', '.join('?' for _ in outcols)})",
                [tuple(_cell(r.get(c), "numeric" if c not in group else cls[c])
                       for c in outcols) for r in rows])
            covered = sum(int(r.get("row_count") or 0) for r in rows)
            entry.update(mode="aggregated", group_by=group, rows_in_facts=len(rows),
                         rows_covered=covered, source_sql=sql,
                         reason=f"{p['row_count']} rows is above the facts cap {cap}: "
                                f"aggregated, {why}; {len(rows)} groups covering {covered} rows")
            if covered != p["row_count"]:
                problems.append(f"{p['ref']}: groups cover {covered} of {p['row_count']} rows")
        for c in p["columns"] if entry["mode"] == "rows" else []:
            rng = c.get("range") or {}
            db.execute("INSERT INTO _facts_columns VALUES (?,?,?,?,?,?,?,?,?)",
                       (ft, c["name"], c["type"], c["class"], int(c["prose"]), c["distinct"],
                        c["non_null"], rng.get("lo"), rng.get("hi")))
        if entry["mode"] == "aggregated":
            # R-022 f1 #18: an aggregated table's queryable columns are its GROUP columns, the
            # group's row_count and one sum_<measure> per numeric measure — catalogued too.
            bycol = {c["name"]: c for c in p["columns"]}
            for g in group:
                c = bycol[g]
                rng = c.get("range") or {}
                db.execute("INSERT INTO _facts_columns VALUES (?,?,?,?,?,?,?,?,?)",
                           (ft, g, c["type"], c["class"], int(c["prose"]), c["distinct"],
                            c["non_null"], rng.get("lo"), rng.get("hi")))
            db.execute("INSERT INTO _facts_columns VALUES (?,?,?,?,?,?,?,?,?)",
                       (ft, "row_count", "COUNT(*)", "numeric", 0, None, None, None, None))
            for m in measures:
                db.execute("INSERT INTO _facts_columns VALUES (?,?,?,?,?,?,?,?,?)",
                           (ft, f"sum_{m}", f"SUM({bycol[m]['type']})", "numeric", 0, None,
                            None, None, None))
        db.execute("INSERT INTO _facts_tables VALUES (?,?,?,?,?,?,?,?,?)",
                   (ft, p["ref"], entry["mode"], p["row_count"], entry["rows_in_facts"],
                    json.dumps(entry["group_by"]), entry["reason"], when, entry["source_sql"]))
        tables.append(entry)
    db.executemany("INSERT INTO _facts_meta VALUES (?, ?)",
                   [("source_id", source_id), ("extracted_at", when),
                    ("note", "rows copied read-only from the source; never embedded (P6)")])
    db.commit()
    # P8: read the counts back from the file as written; and the CONTENT digest of the whole
    # logical publication (the incremental key, R-022 f1 #11 / f2 #10): DDL, the catalogue rows
    # (less the extract date, which changes every run) and every table's rows
    content = hashlib.sha256()
    for (ddl_sql,) in db.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL "
                                 "ORDER BY name"):
        content.update(json.dumps(["ddl", ddl_sql]).encode("utf-8"))
    for row in db.execute("SELECT facts_table, source_table, mode, rows_source, rows_in_facts, "
                          "group_by, reason, source_sql FROM _facts_tables ORDER BY facts_table"):
        content.update(json.dumps(["table", *row], default=str).encode("utf-8"))
    for row in db.execute("SELECT * FROM _facts_columns ORDER BY rowid"):
        content.update(json.dumps(["column", *row], default=str).encode("utf-8"))
    for t in tables:
        content.update(json.dumps([t["source_table"], t["facts_table"], t["mode"],
                                   t["group_by"], t["rows_source"]], default=str).encode("utf-8"))
        if t["facts_table"]:
            got = db.execute(f"SELECT COUNT(*) FROM {quote_ident(t['facts_table'])}").fetchone()[0]
            if got != t["rows_in_facts"]:
                problems.append(f"{t['facts_table']}: wrote {t['rows_in_facts']}, read back {got}")
            for row in db.execute(f"SELECT * FROM {quote_ident(t['facts_table'])} ORDER BY rowid"):
                content.update(json.dumps(row, default=str).encode("utf-8"))
    db.close()
    content_sha = content.hexdigest()

    # every mode counts toward the shrink check: rows copied AND rows an aggregate covers (#10)
    total = sum(int(t.get("rows_covered") or 0) for t in tables
                if t["mode"] in ("rows", "aggregated"))
    manifest = {"source_id": source_id, "extracted_at": when, "facts_file": str(final),
                "tables": tables, "rows_total": total, "problems": problems, "status": "ok",
                "refused": None, "content_sha256": content_sha}
    prev = load_manifest(source_id)
    ratio = float(fpol.get("min_shrink_ratio", 0.5))
    publishable = [t for t in tables if t["facts_table"]]
    measured_rows = sum(int(t.get("rows_source") or 0) for t in tables
                        if t["mode"] in ("rows", "aggregated"))
    if unmeasured:
        manifest.update(status="refused", refused=(
            f"UNMEASURED: the row count of {unmeasured} could not be measured; an unknown is "
            f"never published as a count — the previous file is kept"))
    elif not publishable:
        manifest.update(status="refused", refused="EMPTY: no table could be written to the "
                                                   "facts store; the previous file is kept")
    elif measured_rows > 0 and total == 0:
        manifest.update(status="refused", refused=(
            f"EMPTY: {measured_rows} rows were measured at the source but none reached the facts "
            f"store; the previous file is kept"))
    elif problems and not force:
        manifest.update(status="refused", refused=(
            f"INCONSISTENT: {'; '.join(problems)}; the previous file is kept — re-run, or confirm "
            f"the source changed during the extract and re-run with force=<your principal>, "
            f"force_digest={content_sha!r}"))
    elif not force and prev and prev.get("status") == "ok" and prev.get("rows_total") and \
            total < ratio * prev["rows_total"]:
        manifest.update(status="refused", refused=(
            f"SHRUNK: {total} rows vs {prev['rows_total']} last time (below {ratio:.0%}); the "
            f"previous file is kept — confirm the source really shrank, then re-run with "
            f"force=<your principal>, force_digest={content_sha!r}"))
    overridden = []
    if force and manifest["status"] == "ok":
        if problems:
            overridden.append(f"INCONSISTENT: {'; '.join(problems)}")
        if prev and prev.get("status") == "ok" and prev.get("rows_total") and \
                total < ratio * prev["rows_total"]:
            overridden.append(f"SHRUNK: {total} rows vs {prev['rows_total']} last time")
        if overridden and force_digest is not None and force_digest != content_sha:
            manifest.update(status="refused", refused=(
                f"OVERRIDE MISMATCH: the override approved candidate {force_digest[:12]}… but "
                f"this candidate is {content_sha[:12]}…; the previous file is kept"))
            overridden = []
    if manifest["status"] == "refused":
        tmp.unlink(missing_ok=True)
        manifest["facts_file"] = str(final) if final.exists() else None
        manifest["sha256"] = sha256_file(final) if final.exists() else None
        return manifest
    if not force and prev and prev.get("status") == "ok" \
            and prev.get("content_sha256") == content_sha and final.exists() \
            and prev.get("sha256") and sha256_file(final) == prev["sha256"]:
        # R-022 f1 #11: identical content — the published file already holds exactly this; it
        # is not re-published (its extract date stays the one its figures were measured at)
        tmp.unlink(missing_ok=True)
        return {**prev, "unchanged": True,
                "note": f"content unchanged since {prev.get('extracted_at')}: not re-published"}
    if overridden:
        # R-022 f2 #5: the override is a person's decision on THIS candidate — appended
        # (append-only, sealed) before the file is published
        rec = {"action": "force_publish", "source_id": source_id, **who,
               "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               "content_sha256": content_sha, "overrides": overridden}
        rec["record_digest"] = _record_digest(rec)
        with open(overrides_log_path(source_id), "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
        manifest["override"] = {k: rec[k] for k in ("principal_id", "authenticated_by", "at",
                                                    "content_sha256", "overrides")}
    _replace(tmp, final)
    manifest["sha256"] = sha256_file(final)
    mp = manifest_path(source_id)
    mp.write_text(json.dumps(manifest, indent=1, sort_keys=True, default=str), encoding="utf-8")
    return manifest


__all__ = ["build_facts", "facts_dir", "facts_path", "manifest_path", "load_manifest",
           "choose_group_columns", "facts_table_names", "sha256_file", "FactsOverrideRefused",
           "overrides_log_path", "logged_overrides"]
