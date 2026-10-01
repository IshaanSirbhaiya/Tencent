#!/usr/bin/env python
"""Bring the Walnut synthetic estates up, and show a person that they are real.

    python scripts/estates.py provision            # load all four databases (SQLite)
    python scripts/estates.py provision --clean    # rebuild from scratch
    python scripts/estates.py provision --backend postgres
    python scripts/estates.py verify               # counts + referential integrity
    python scripts/estates.py status               # every source in both estates
    python scripts/estates.py translate finance/core_banking --explain

The estates declare a PostgreSQL dialect. SQLite is the default because it needs no
server and no Docker, which is what CI and a laptop demo actually have; the same
code path runs the shipped SQL unaltered against PostgreSQL when one is reachable
(`docker compose -f docker-compose.estates.yml up -d`, or `--pg-dsn`).

Nothing here hardcodes a table, a row count or a source name. Sources come from each
pack's `sources.yaml`, SQL file order from the pack's own loader scripts, table lists
from the database catalog after loading, and expectations from the pack's ground-truth
files. Everything it prints, it measured.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from estate_lib import probes as probe_mod  # noqa: E402
from estate_lib import report as R  # noqa: E402
from estate_lib import verify as verify_mod  # noqa: E402
from estate_lib.backends import (  # noqa: E402
    DEFAULT_PG_DSN,
    BackendUnavailable,
    PostgresBackend,
    estate_connection,
    make_backend,
)
from estate_lib.discovery import (  # noqa: E402
    DiscoveryError,
    EstateSpec,
    RelationalSource,
    discover_estates,
    resolve_relational,
)
from estate_lib.pg_to_sqlite import UntranslatableConstruct, translate  # noqa: E402

VAR_DIR = REPO / "var" / "estates"


# ---------------------------------------------------------------------------


def _estates(args) -> list[EstateSpec]:
    return discover_estates(REPO, args.estate or None)


def _rels(estate: EstateSpec) -> list[RelationalSource]:
    return [resolve_relational(s) for s in estate.relational]


def _backend(args):
    return make_backend(args.backend, VAR_DIR, getattr(args, "pg_dsn", None))


def _backend_banner(args) -> None:
    if args.backend == "sqlite":
        R.bullet(f"backend: SQLite {__import__('sqlite3').sqlite_version} (no server, no Docker) "
                 f"→ {VAR_DIR}")
        R.bullet("the estates' PostgreSQL DDL is translated mechanically on the way in; "
                 "`estates.py translate <pack>/<source> --explain` shows every rewrite")
    else:
        be = PostgresBackend(getattr(args, "pg_dsn", None))
        try:
            R.bullet(f"backend: {be.probe().split(',')[0]}")
        except BackendUnavailable as exc:
            R.bullet(R.c(str(exc), R.RED))
            R.bullet("start one with: docker compose -f docker-compose.estates.yml up -d")


# ---------------------------------------------------------------------------


def cmd_provision(args) -> int:
    R.heading("Provisioning the Walnut synthetic estates")
    _backend_banner(args)
    backend = _backend(args)
    estates = _estates(args)
    rows, total_seconds, failures = [], 0.0, 0

    for estate in estates:
        for rel in _rels(estate):
            try:
                res = backend.provision(rel, clean=args.clean)
            except BackendUnavailable as exc:
                print(R.c(f"  {estate.pack}/{rel.id}: {exc}", R.RED))
                return 2
            except Exception as exc:  # noqa: BLE001 — report the failure, do not abort the batch
                failures += 1
                rows.append([estate.pack, rel.id, rel.schema, "FAILED", "-", "-", type(exc).__name__])
                print(R.c(f"  {estate.pack}/{rel.id}: {exc}", R.RED))
                continue
            total_seconds += res.seconds
            rows.append([
                estate.pack, rel.id, rel.schema,
                R.verdict("loaded" if res.loaded else "unchanged"),
                f"{res.statements:,}" if res.loaded else "-",
                R.human_bytes(res.sql_bytes),
                f"{res.seconds:.2f}s",
            ])
            for note in res.notes:
                R.bullet(f"{rel.id}: {note}")
            if res.translation and args.show_translation:
                _print_translation(res.translation, rel)

    print()
    R.table(
        ["estate", "source", "schema", "state", "statements", "sql", "time"],
        rows, right=(4, 5, 6),
    )
    print()
    R.bullet(f"total load time {total_seconds:.2f}s across {len(rows)} database(s)")
    if failures:
        print(R.c(f"  {failures} source(s) failed to provision", R.RED))
        return 1
    return cmd_verify(args) if not args.no_verify else 0


def _print_translation(tr, rel: RelationalSource) -> None:
    R.heading(f"  translation applied to {rel.pack}/{rel.id} ({rel.schema})")
    R.table(
        ["rule", "times"],
        [[k, f"{v:,}"] for k, v in sorted(tr.counts().items())],
        right=(1,),
    )
    seen: set[str] = set()
    for rw in tr.rewrites:
        if rw.rule in seen:
            continue
        seen.add(rw.rule)
        R.bullet(str(rw))
    R.bullet(f"{len(tr.comments)} PostgreSQL COMMENT(s) preserved in the `_pg_comment` table")


# ---------------------------------------------------------------------------


def cmd_verify(args) -> int:
    R.heading("Verifying")
    backend = _backend(args)
    estates = _estates(args)
    bad = soft = 0

    for estate in estates:
        rels = _rels(estate)
        if not rels:
            continue
        try:
            with estate_connection(backend, rels) as conn:
                res = verify_mod.verify_estate(conn, estate, rels, backend.name)
        except BackendUnavailable as exc:
            print(R.c(f"  {estate.pack}: {exc}", R.RED))
            return 2

        R.rule(f"{estate.pack} — row counts vs {res.expectations_file.name if res.expectations_file else 'no declared counts'}")
        R.table(
            ["relation", "kind", "rows", "expected", "verdict"],
            [
                [c.key, c.kind, f"{c.rows:,}",
                 f"{c.expected:,}" if c.expected is not None else "-",
                 R.verdict(c.verdict)]
                for c in res.counts
            ],
            right=(2, 3),
        )
        R.bullet(f"{res.total_rows:,} rows across {len([c for c in res.counts if c.kind=='table'])} tables")

        declared = [i for i in res.integrity if i.declared]
        planted = [i for i in res.integrity if not i.declared]
        R.rule(f"{estate.pack} — referential integrity ({len(declared)} declared FK(s))")
        broken = [i for i in declared if i.orphans]
        R.table(
            ["declared foreign key", "child rows", "orphans", "verdict"],
            [[i.fk.label(), f"{i.child_rows:,}", f"{i.orphans:,}", R.verdict(i.verdict)]
             for i in (broken or declared[:0])],
            right=(1, 2),
        ) if broken else R.bullet(
            R.c(f"all {len(declared)} declared foreign keys hold — 0 orphans", R.GREEN)
        )

        if planted:
            R.rule(f"{estate.pack} — deliberate orphans (undeclared FKs the estate plants on purpose)")
            R.table(
                ["undeclared foreign key", "non-null", "orphans", "measured", "declared", "verdict"],
                [
                    [i.fk.label(), f"{i.non_null:,}", f"{i.orphans:,}", f"{i.rate:.1%}",
                     f"~{i.declared_rate:.0%}" if i.declared_rate is not None else "-",
                     R.verdict(i.verdict)]
                    for i in planted
                ],
                right=(1, 2, 3, 4),
            )
            R.bullet("these are fixtures, not bugs: a join that inner-joins them away is deleting evidence")

        for note in res.observations:
            print(R.c(f"  ~ {note}", R.YELLOW))
        for problem in res.problems:
            print(R.c(f"  ! {problem}", R.RED))
        bad += len(res.problems)
        soft += len(res.observations)

    print()
    if bad:
        print(R.c(f"  {bad} problem(s) found", R.RED))
        return 1
    print(R.c("  every loaded table matches the estate's declared count, and every declared "
              "foreign key holds.", R.GREEN))
    if soft:
        print(R.c(f"  {soft} fixture observation(s) above — the estate differs from its own prose, "
                  "which is a note about the fixture, not a failed load.", R.YELLOW))
    return 0


# ---------------------------------------------------------------------------


def cmd_status(args) -> int:
    t0 = time.perf_counter()
    backend = _backend(args)
    estates = _estates(args)
    payload: dict = {"backend": backend.name, "estates": {}}
    all_sources = 0

    if not args.json:
        R.heading("Walnut synthetic estates — status")
        _backend_banner(args)

    for estate in estates:
        rels = _rels(estate)
        expectations, exp_path = verify_mod.load_expectations(estate)
        est_payload: dict = {"relational": [], "non_relational": [], "problems": []}

        # ---- relational -------------------------------------------------
        rel_rows = []
        counts_by_schema: dict[str, list] = {}
        conn = None
        try:
            conn = backend.open_estate(rels) if rels else None
        except BackendUnavailable as exc:
            est_payload["problems"].append(str(exc))
            if not args.json:
                print(R.c(f"  {estate.pack}: {exc}", R.RED))
        if conn is not None:
            try:
                for c in verify_mod.count_relations(conn, rels):
                    counts_by_schema.setdefault(c.schema, []).append(c)
            finally:
                conn.close()

        for rel in rels:
            cs = counts_by_schema.get(rel.schema, [])
            tables = [c for c in cs if c.kind == "table"]
            views = [c for c in cs if c.kind == "view"]
            rows_total = sum(c.rows for c in tables)
            size = backend.size_on_disk(rel)
            rel_rows.append([
                rel.id, rel.schema, rel.spec.pinning_tier, backend.name,
                f"{len(tables)}" + (f" +{len(views)}v" if views else ""),
                f"{rows_total:,}" if cs else R.verdict("missing"),
                R.human_bytes(size),
            ])
            est_payload["relational"].append({
                "id": rel.id, "schema": rel.schema, "tier": rel.spec.pinning_tier,
                "backend": backend.name, "location": backend.location(rel),
                "tables": {c.name: c.rows for c in tables},
                "views": [c.name for c in views],
                "rows": rows_total, "bytes_on_disk": size,
            })
            all_sources += 1

        # ---- non-relational --------------------------------------------
        # counts for a loaded schema are the relational verifier's business; hand the
        # probes only what is left, so the footer names what nothing measures.
        loaded_schemas = {r.schema for r in rels}
        for_probes = {
            k: v for k, v in expectations.items()
            if k.split(".", 1)[0] not in loaded_schemas
        }
        probes, leftover = probe_mod.probe_estate(estate, for_probes)
        all_sources += len(probes)

        if not args.json:
            R.rule(f"{estate.pack} — relational sources")
            R.table(
                ["source", "schema", "tier", "backend", "tables", "rows", "on disk"],
                rel_rows, right=(4, 5, 6),
            )
            for rel in rels:
                cs = [c for c in counts_by_schema.get(rel.schema, []) if c.kind == "table"]
                if cs and args.tables:
                    R.bullet(rel.id + ": " + ", ".join(f"{c.name} {c.rows:,}" for c in cs))

            R.rule(f"{estate.pack} — non-relational sources (counted in place, never loaded)")
            R.table(
                ["source", "kind", "tier", "files", "bytes", "counted", "vs declared"],
                [
                    [
                        p.id + (" [held out]" if p.source.held_out else ""),
                        p.source.kind, p.source.pinning_tier,
                        f"{p.files:,}", R.human_bytes(p.bytes),
                        ", ".join(f"{k} {v:,}" for k, v in list(p.metrics.items())[:3]) or "-",
                        ", ".join(R.verdict(m.verdict) for m in p.matches) or R.c("not declared", R.DIM),
                    ]
                    for p in probes
                ],
                right=(3, 4),
            )
            if args.detail:
                for p in probes:
                    if p.notes or p.sample:
                        print()
                        print(f"  {R.c(p.id, R.BOLD)} — {p.source.title}")
                        for n in p.notes:
                            R.bullet(n)
                        for s in p.sample:
                            R.bullet(R.c(s, R.DIM), marker="›")
            for p in probes:
                for problem in p.problems:
                    print(R.c(f"  ! {problem}", R.RED))
                    est_payload["problems"].append(problem)
            if leftover:
                R.bullet(R.c(
                    "declared counts this tool does not measure (ground-truth facts, not source "
                    "row counts): " + ", ".join(f"{k}={v:,}" for k, v in sorted(leftover.items())),
                    R.DIM))

        for p in probes:
            est_payload["non_relational"].append({
                "id": p.id, "kind": p.source.kind, "tier": p.source.pinning_tier,
                "held_out": p.source.held_out,
                "root": str(p.root) if p.root else None,
                "files": p.files, "bytes": p.bytes, "metrics": p.metrics,
                "extensions": p.extensions, "notes": p.notes, "sample": p.sample,
                "matches": [{"key": m.key, "observed": m.observed, "expected": m.expected,
                             "verdict": m.verdict} for m in p.matches],
                "problems": p.problems,
            })
        est_payload["unmatched_expectations"] = leftover
        est_payload["expectations_file"] = str(exp_path) if exp_path else None
        payload["estates"][estate.pack] = est_payload

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0

    print()
    R.bullet(f"{all_sources} sources across {len(estates)} estate(s) — "
             f"status assembled in {time.perf_counter() - t0:.2f}s")
    return 0


# ---------------------------------------------------------------------------


def cmd_translate(args) -> int:
    pack, _, source_id = args.source.partition("/")
    estates = discover_estates(REPO, [pack])
    spec = next((s for s in estates[0].sources if s.id == source_id), None)
    if spec is None or not spec.is_relational:
        print(R.c(f"no relational source {args.source!r}", R.RED))
        return 2
    rel = resolve_relational(spec)
    chosen = rel.sql_files if args.include_seed else [
        f for f in rel.sql_files if "seed" not in f.parts
    ]
    sql = "\n".join(f.read_text(encoding="utf-8") for f in chosen)
    tr = translate(sql, rel.schema)
    if args.explain:
        R.heading(f"{args.source}: PostgreSQL → SQLite")
        R.bullet(f"schema {rel.schema}; the source ships {len(rel.sql_files)} SQL file(s), "
                 f"{R.human_bytes(rel.total_sql_bytes())}")
        R.bullet(f"translating {len(chosen)} of them"
                 + ("" if args.include_seed else " — DDL only; pass --include-seed for the data"))
        _print_translation(tr, rel)
        R.heading("every distinct rewrite")
        for rw in tr.rewrites[: args.limit]:
            R.bullet(str(rw))
        return 0
    for stmt in tr.statements:
        print(stmt + ";")
    return 0


# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="estates.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--backend", default="sqlite", choices=["sqlite", "postgres", "postgresql", "pg"])
        sp.add_argument("--estate", action="append", help="repeatable; default is every pack found")
        sp.add_argument("--pg-dsn", default=None, help=f"default {DEFAULT_PG_DSN} or $WALNUT_PG_DSN")
        return sp

    sp = common(sub.add_parser("provision", help="create and load the relational sources"))
    sp.add_argument("--clean", action="store_true", help="drop and rebuild rather than reuse")
    sp.add_argument("--no-verify", action="store_true", help="skip the verification pass")
    sp.add_argument("--show-translation", action="store_true")
    sp.set_defaults(func=cmd_provision)

    sp = common(sub.add_parser("verify", help="row counts, referential integrity, planted orphans"))
    sp.set_defaults(func=cmd_verify)

    sp = common(sub.add_parser("status", help="every source in every estate, readable"))
    sp.add_argument("--detail", action="store_true", help="per-source notes and samples")
    sp.add_argument("--tables", action="store_true", help="list every table and its row count")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("translate", help="show the PostgreSQL → SQLite rewrites, or emit the SQL")
    sp.add_argument("source", help="<pack>/<source_id>, e.g. finance/core_banking")
    sp.add_argument("--explain", action="store_true")
    sp.add_argument("--include-seed", action="store_true",
                    help="also translate the seed INSERTs, not just the DDL")
    sp.add_argument("--limit", type=int, default=40)
    sp.set_defaults(func=cmd_translate)
    return p


def main(argv: list[str] | None = None) -> int:
    R.enable_utf8()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (DiscoveryError, UntranslatableConstruct) as exc:
        print(R.c(f"error: {exc}", R.RED), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
