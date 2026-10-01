"""Two backends, one code path.

The estates declare a PostgreSQL dialect. `SqliteBackend` runs them through the
mechanical translation in `pg_to_sqlite` so a laptop and CI need no server;
`PostgresBackend` runs the shipped SQL unaltered so the dialect is exercised for
real. Everything above this module — verification, counting, status — is written
once against `EstateConnection`, whose queries are schema-qualified and identical
on both. On SQLite that works because each source's file is ATTACHed under its own
schema name, so `core_banking.party` and `crm.relationship_manager` resolve in a
single connection exactly as they do in one PostgreSQL database.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .discovery import RelationalSource
from .pg_to_sqlite import (
    COMMENT_TABLE_DDL,
    META_TABLE_DDL,
    TRANSLATOR_VERSION,
    Translation,
    split_statements,
    strip_psql_meta,
    translate,
)

DEFAULT_PG_DSN = "postgresql://walnut:walnut@127.0.0.1:55432/walnut_estates"


class BackendUnavailable(RuntimeError):
    """The backend cannot be reached; the caller should say so, not pretend."""


@dataclass
class LoadResult:
    source_id: str
    pack: str
    schema: str
    backend: str
    loaded: bool          # False => already provisioned at this fingerprint
    seconds: float
    statements: int
    sql_bytes: int
    fingerprint: str
    translation: Translation | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class ForeignKey:
    schema: str
    table: str
    column: str
    ref_schema: str
    ref_table: str
    ref_column: str

    def label(self) -> str:
        return (
            f"{self.schema}.{self.table}.{self.column} -> "
            f"{self.ref_schema}.{self.ref_table}.{self.ref_column}"
        )


class EstateConnection:
    """A read connection over one estate, with every source addressable by schema."""

    def __init__(self, backend: "Backend", raw: Any, schemas: list[str]):
        self.backend = backend
        self.raw = raw
        self.schemas = schemas

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        cur = self.raw.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return [tuple(r) for r in rows]

    def scalar(self, sql: str, params: tuple = ()) -> Any:
        rows = self.query(sql, params)
        return rows[0][0] if rows else None

    def relations(self, schema: str) -> list[tuple[str, str]]:
        """(name, 'table'|'view') for one schema, read from the catalog."""
        return self.backend.relations(self.raw, schema)

    def foreign_keys(self, schema: str) -> list[ForeignKey]:
        return self.backend.foreign_keys(self.raw, schema)

    def quote(self, schema: str, table: str) -> str:
        return f'"{schema}"."{table}"'

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.raw.close()


# ---------------------------------------------------------------------------


class Backend:
    name = "?"

    def provision(self, rel: RelationalSource, clean: bool) -> LoadResult:  # pragma: no cover
        raise NotImplementedError

    def is_provisioned(self, rel: RelationalSource) -> bool:  # pragma: no cover
        raise NotImplementedError

    def open_estate(self, rels: list[RelationalSource]) -> EstateConnection:  # pragma: no cover
        raise NotImplementedError

    def relations(self, raw: Any, schema: str) -> list[tuple[str, str]]:  # pragma: no cover
        raise NotImplementedError

    def foreign_keys(self, raw: Any, schema: str) -> list[ForeignKey]:  # pragma: no cover
        raise NotImplementedError

    def size_on_disk(self, rel: RelationalSource) -> int | None:  # pragma: no cover
        return None

    def location(self, rel: RelationalSource) -> str:  # pragma: no cover
        return "?"


# ---------------------------------------------------------------------------


class SqliteBackend(Backend):
    name = "sqlite"

    def __init__(self, var_dir: Path):
        self.var_dir = var_dir

    def db_path(self, rel: RelationalSource) -> Path:
        return self.var_dir / rel.pack / f"{rel.id}.sqlite"

    def location(self, rel: RelationalSource) -> str:
        return str(self.db_path(rel))

    def size_on_disk(self, rel: RelationalSource) -> int | None:
        p = self.db_path(rel)
        if not p.exists():
            return None
        total = p.stat().st_size
        for suffix in ("-wal", "-shm"):
            side = p.with_name(p.name + suffix)
            if side.exists():
                total += side.stat().st_size
        return total

    def _stored_fingerprint(self, path: Path) -> str | None:
        if not path.exists():
            return None
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        except sqlite3.Error:
            return None
        try:
            row = con.execute(
                "SELECT value FROM _estate_meta WHERE key = 'fingerprint'"
            ).fetchone()
            return row[0] if row else None
        except sqlite3.Error:
            return None
        finally:
            con.close()

    def is_provisioned(self, rel: RelationalSource) -> bool:
        return self._stored_fingerprint(self.db_path(rel)) == rel.fingerprint(TRANSLATOR_VERSION)

    def provision(self, rel: RelationalSource, clean: bool = False) -> LoadResult:
        t0 = time.perf_counter()
        path = self.db_path(rel)
        want = rel.fingerprint(TRANSLATOR_VERSION)
        notes: list[str] = []

        if clean:
            self._remove(path)
        elif self._stored_fingerprint(path) == want:
            return LoadResult(
                source_id=rel.id, pack=rel.pack, schema=rel.schema, backend=self.name,
                loaded=False, seconds=time.perf_counter() - t0, statements=0,
                sql_bytes=rel.total_sql_bytes(), fingerprint=want,
                notes=["already provisioned at this fingerprint; nothing reloaded"],
            )
        else:
            # a stale or partial database: rebuild rather than layer a second load on top
            if path.exists():
                notes.append("existing database had a different fingerprint; rebuilt from scratch")
            self._remove(path)

        sql = "\n".join(p.read_text(encoding="utf-8") for p in rel.sql_files)
        tr = translate(sql, rel.schema)

        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path)
        try:
            con.execute("PRAGMA journal_mode = OFF")
            con.execute("PRAGMA synchronous = OFF")
            # FK enforcement is deliberately off during the load: the estates ship
            # multi-row INSERTs with forward self-references (e.g. decisions.superseded_by).
            # Integrity is then *checked and reported* rather than aborting the load —
            # the deliberate orphans are fixtures, and a report is worth more than a crash.
            con.execute("PRAGMA foreign_keys = OFF")
            con.execute(COMMENT_TABLE_DDL)
            con.execute(META_TABLE_DDL)
            for stmt in tr.statements:
                con.execute(stmt)
            con.executemany(
                "INSERT INTO _pg_comment (object_kind, object_name, comment) VALUES (?,?,?)",
                tr.comments,
            )
            con.executemany(
                "INSERT OR REPLACE INTO _estate_meta (key, value) VALUES (?,?)",
                [
                    ("fingerprint", want),
                    ("schema", rel.schema),
                    ("source_id", rel.id),
                    ("pack", rel.pack),
                    ("translator_version", TRANSLATOR_VERSION),
                    ("dialect_origin", "postgresql"),
                    ("sql_files", str(len(rel.sql_files))),
                ],
            )
            con.commit()
            con.execute("PRAGMA optimize")
        finally:
            con.close()

        return LoadResult(
            source_id=rel.id, pack=rel.pack, schema=rel.schema, backend=self.name,
            loaded=True, seconds=time.perf_counter() - t0, statements=len(tr.statements),
            sql_bytes=rel.total_sql_bytes(), fingerprint=want, translation=tr, notes=notes,
        )

    @staticmethod
    def _remove(path: Path) -> None:
        for suffix in ("", "-wal", "-shm"):
            p = path.with_name(path.name + suffix)
            if p.exists():
                p.unlink()

    def open_estate(self, rels: list[RelationalSource]) -> EstateConnection:
        missing = [r.id for r in rels if not self.db_path(r).exists()]
        if missing:
            raise BackendUnavailable(
                f"not provisioned on sqlite: {', '.join(missing)} — run `estates.py provision` first"
            )
        con = sqlite3.connect(":memory:")
        schemas = []
        for r in rels:
            con.execute(f"ATTACH DATABASE ? AS \"{r.schema}\"", (str(self.db_path(r)),))
            schemas.append(r.schema)
        return EstateConnection(self, con, schemas)

    def relations(self, raw: Any, schema: str) -> list[tuple[str, str]]:
        rows = raw.execute(
            f'SELECT name, type FROM "{schema}".sqlite_master '
            "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
            "AND name NOT LIKE '\\_%' ESCAPE '\\' ORDER BY type DESC, name"
        ).fetchall()
        return [(n, t) for n, t in rows]

    def foreign_keys(self, raw: Any, schema: str) -> list[ForeignKey]:
        out: list[ForeignKey] = []
        for name, kind in self.relations(raw, schema):
            if kind != "table":
                continue
            for row in raw.execute(f'PRAGMA "{schema}".foreign_key_list("{name}")').fetchall():
                # id, seq, table, from, to, on_update, on_delete, match
                ref_table, from_col, to_col = row[2], row[3], row[4]
                if to_col is None:  # implicit reference to the target's primary key
                    pk = raw.execute(f'PRAGMA "{schema}".table_info("{ref_table}")').fetchall()
                    pks = [c[1] for c in pk if c[5]]
                    to_col = pks[0] if pks else None
                if to_col is None:
                    continue
                out.append(ForeignKey(schema, name, from_col, schema, ref_table, to_col))
        return out


# ---------------------------------------------------------------------------


class PostgresBackend(Backend):
    name = "postgresql"

    def __init__(self, dsn: str | None = None, connect_timeout: int = 5):
        dsn = dsn or os.environ.get("WALNUT_PG_DSN") or os.environ.get("DATABASE_URL") or DEFAULT_PG_DSN
        # fail fast and say why. Without this, "is PostgreSQL up?" is answered by a
        # multi-minute TCP timeout, which in a demo is indistinguishable from a hang.
        if "connect_timeout" not in dsn:
            dsn += ("&" if "?" in dsn else "?") + f"connect_timeout={connect_timeout}"
        self.dsn = dsn
        self._driver = None

    # -- driver ------------------------------------------------------------
    def _connect(self):
        if self._driver is None:
            try:
                import psycopg  # type: ignore

                self._driver = ("psycopg3", psycopg)
            except ImportError:
                try:
                    import psycopg2  # type: ignore

                    self._driver = ("psycopg2", psycopg2)
                except ImportError as exc:  # pragma: no cover
                    raise BackendUnavailable(
                        "neither psycopg (3) nor psycopg2 is installed; "
                        "`pip install psycopg[binary]` or use --backend sqlite"
                    ) from exc
        _, mod = self._driver
        try:
            return mod.connect(self.dsn)
        except Exception as exc:
            raise BackendUnavailable(f"cannot reach PostgreSQL at {self._safe_dsn()}: {exc}") from exc

    def _safe_dsn(self) -> str:
        dsn = self.dsn
        if "@" in dsn and "//" in dsn:
            head, tail = dsn.split("//", 1)
            creds, host = tail.split("@", 1)
            user = creds.split(":", 1)[0]
            return f"{head}//{user}:***@{host}"
        return dsn

    def location(self, rel: RelationalSource) -> str:
        return f"{self._safe_dsn()} schema={rel.schema}"

    def probe(self) -> str:
        con = self._connect()
        try:
            with con.cursor() as cur:
                cur.execute("SELECT version()")
                return str(cur.fetchone()[0])
        finally:
            con.close()

    # -- provisioning ------------------------------------------------------
    def _meta_fingerprint(self, con, schema: str) -> str | None:
        with con.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'walnut_estate_meta'"
            )
            if not cur.fetchone():
                return None
            cur.execute(
                "SELECT fingerprint FROM public.walnut_estate_meta WHERE schema_name = %s", (schema,)
            )
            row = cur.fetchone()
            return row[0] if row else None

    def is_provisioned(self, rel: RelationalSource) -> bool:
        con = self._connect()
        try:
            return self._meta_fingerprint(con, rel.schema) == rel.fingerprint("pg")
        finally:
            con.close()

    def provision(self, rel: RelationalSource, clean: bool = False) -> LoadResult:
        t0 = time.perf_counter()
        want = rel.fingerprint("pg")
        notes: list[str] = []
        con = self._connect()
        try:
            con.autocommit = False
            with con.cursor() as cur:
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS public.walnut_estate_meta ("
                    " schema_name text PRIMARY KEY, source_id text NOT NULL, pack text NOT NULL,"
                    " fingerprint text NOT NULL, loaded_at timestamptz NOT NULL DEFAULT now())"
                )
            con.commit()

            if not clean and self._meta_fingerprint(con, rel.schema) == want:
                return LoadResult(
                    source_id=rel.id, pack=rel.pack, schema=rel.schema, backend=self.name,
                    loaded=False, seconds=time.perf_counter() - t0, statements=0,
                    sql_bytes=rel.total_sql_bytes(), fingerprint=want,
                    notes=["already provisioned at this fingerprint; nothing reloaded"],
                )

            # The shipped DDL opens with DROP SCHEMA ... CASCADE, so a reload is a
            # rebuild by construction. No translation: this is the dialect the
            # estate was written in.
            sql, meta = strip_psql_meta(
                "\n".join(p.read_text(encoding="utf-8") for p in rel.sql_files)
            )
            if meta:
                notes.append(f"{len(meta)} psql meta-command(s) handled by the loader, not sent to the server")
            statements = split_statements(sql)
            with con.cursor() as cur:
                for stmt in statements:
                    cur.execute(stmt)
                cur.execute(
                    "INSERT INTO public.walnut_estate_meta (schema_name, source_id, pack, fingerprint)"
                    " VALUES (%s,%s,%s,%s) ON CONFLICT (schema_name) DO UPDATE SET"
                    " source_id = EXCLUDED.source_id, pack = EXCLUDED.pack,"
                    " fingerprint = EXCLUDED.fingerprint, loaded_at = now()",
                    (rel.schema, rel.id, rel.pack, want),
                )
            con.commit()
        except BackendUnavailable:
            raise
        except Exception:
            with contextlib.suppress(Exception):
                con.rollback()
            raise
        finally:
            con.close()

        return LoadResult(
            source_id=rel.id, pack=rel.pack, schema=rel.schema, backend=self.name,
            loaded=True, seconds=time.perf_counter() - t0, statements=len(statements),
            sql_bytes=rel.total_sql_bytes(), fingerprint=want, notes=notes,
        )

    def open_estate(self, rels: list[RelationalSource]) -> EstateConnection:
        con = self._connect()
        return EstateConnection(self, con, [r.schema for r in rels])

    def relations(self, raw: Any, schema: str) -> list[tuple[str, str]]:
        with raw.cursor() as cur:
            cur.execute(
                "SELECT table_name, CASE table_type WHEN 'VIEW' THEN 'view' ELSE 'table' END"
                " FROM information_schema.tables WHERE table_schema = %s"
                " ORDER BY 2 DESC, 1",
                (schema,),
            )
            return [(r[0], r[1]) for r in cur.fetchall()]

    def foreign_keys(self, raw: Any, schema: str) -> list[ForeignKey]:
        with raw.cursor() as cur:
            cur.execute(
                """
                SELECT tc.table_schema, tc.table_name, kcu.column_name,
                       ccu.table_schema, ccu.table_name, ccu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON kcu.constraint_name = tc.constraint_name
                 AND kcu.constraint_schema = tc.constraint_schema
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                 AND ccu.constraint_schema = tc.constraint_schema
                WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = %s
                ORDER BY 2, 3
                """,
                (schema,),
            )
            return [ForeignKey(*r) for r in cur.fetchall()]

    def size_on_disk(self, rel: RelationalSource) -> int | None:
        try:
            con = self._connect()
        except BackendUnavailable:
            return None
        try:
            with con.cursor() as cur:
                cur.execute(
                    "SELECT coalesce(sum(pg_total_relation_size(c.oid)), 0)"
                    " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " WHERE n.nspname = %s AND c.relkind IN ('r','m','i','t')",
                    (rel.schema,),
                )
                return int(cur.fetchone()[0])
        finally:
            con.close()


# ---------------------------------------------------------------------------


def make_backend(name: str, var_dir: Path, pg_dsn: str | None = None) -> Backend:
    if name == "sqlite":
        return SqliteBackend(var_dir)
    if name in ("postgres", "postgresql", "pg"):
        return PostgresBackend(pg_dsn)
    raise ValueError(f"unknown backend {name!r}")


@contextlib.contextmanager
def estate_connection(backend: Backend, rels: list[RelationalSource]) -> Iterator[EstateConnection]:
    conn = backend.open_estate(rels)
    try:
        yield conn
    finally:
        conn.close()
